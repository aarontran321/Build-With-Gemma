"""Gemma arbiter: the PRIMARY fault-diagnosis engine.

Design intent (comment required by spec): the arbiter is deliberately
INDEPENDENT of both the sensor pipeline and the flight controller — an
evaluator outside the loop. It never runs in the normal control path; the
classical monitor wakes it exactly once per persistent conflict and hands
it only the compact temporal evidence. It interprets that evidence and
proposes a structured diagnosis; the deterministic guardrail (not this
module) decides whether the proposal becomes the flight decision.

Reliability contract:
* the entire call (including one stricter retry on malformed output) runs
  under a hard timeout from config — a slow model can never stall the demo
* structured output: we pass the Verdict JSON schema to Ollama's `format`
  parameter, so Gemma emits schema-constrained JSON (real structured
  reasoning, not free text we regex afterwards)
* any timeout / connection error / double parse failure raises
  ArbiterUnavailable, and the caller completes the decision with the
  deterministic fallback classifier (server/fallback.py), labeled as such
"""

import asyncio
import json
import re
import time
from typing import Tuple

from . import config
from .schemas import Evidence, Verdict

SYSTEM_PROMPT = (
    "You are the primary fault-diagnosis module on a spacecraft during "
    "descent. You are independent of the sensors and of the flight "
    "controller and are used only for diagnosis, never in the normal "
    "control loop. Two independent sensors estimate the vehicle's "
    "rotation: an IMU gyroscope and a camera-based optical-flow estimator "
    "that shares no hardware or data path with the gyro. They normally "
    "agree. You are woken only when a persistent conflict is detected. "
    "From the compact temporal evidence, determine the fault class, which "
    "sensor is faulty, which sensor if any to trust, your confidence, a "
    "safe action, and whether the evidence is too ambiguous for a hard "
    "decision. A gyroscope that is saturated or railed out has exceeded "
    "its measurable range and reports a pinned or frozen maximum; a "
    "flatlined gyro reports a dead constant; a camera with near-zero "
    "quality (dark or obstructed) is unreliable. Note that the optical "
    "estimator reports HIGH confidence on a static scene (features still "
    "match when nothing moves), so near-zero flow quality is diagnostic of "
    "obstruction or darkness on its own, even when the vehicle is barely "
    "rotating — lack of motion never explains a collapse in flow quality. "
    "Do not assume the camera "
    "is always correct; you may trust the gyro, the camera, or neither. "
    "Output strict JSON with keys fault_class, faulty_sensor, "
    "trusted_sensor, confidence, evidence, alternative_hypothesis, "
    "recommended_action, decision. No prose outside the JSON. "
    "fault_class must be one of: gyro_saturation, gyro_flatline, "
    "camera_degradation, camera_obstruction_or_darkness, "
    "transient_disagreement, dual_sensor_degradation, unknown. "
    "decision must be one of: continue_with_gyro, switch_to_camera, "
    "trust_neither_enter_caution, observe_transient_conflict, "
    "request_redundant_measurement. faulty_sensor and trusted_sensor must "
    "be one of: gyro, camera, none, both. evidence is a list of short "
    "diagnostic observations, not chain-of-thought. alternative_hypothesis "
    "briefly states why the most plausible competing explanation is less "
    "likely. "
    # --- attitude control ---
    "The vehicle must also decide whether to fire its thrusters to stop "
    "rotating. The evidence carries spin_axis (the dominant body axis), "
    "spin_rate_signed (rad/s, where the SIGN is the direction of rotation), "
    "spin_axis_stability (1.0 is a clean single-axis spin, near 0 is a "
    "tumble) and gyro_axis_rates per axis. Set proposed_manoeuvre to "
    "'despin' only when a correction is both warranted and possible, "
    "'hold_attitude' when it is not, or 'reduce_rate_partial' for a cautious "
    "partial correction. Set manoeuvre_axis to the axis you would correct. "
    "Critical asymmetry: ONLY the gyroscope reports a signed, axis-resolved "
    "rate. The camera estimator reports magnitude alone — no axis, no "
    "direction — so if you decide to trust the camera, no de-spin can be "
    "commanded and you must choose hold_attitude. Likewise a railed or "
    "flatlined gyro reports a limit rather than a measurement and cannot aim "
    "a burn. Do NOT state burn durations or thrust levels: those are "
    "computed from rigid-body mechanics once you have chosen the intent. "
    # --- optional altitude context ---
    "The evidence MAY carry altitude_m and seconds_to_ground from a GPS "
    "altimeter. When present, seconds_to_ground is the time available before "
    "impact: prefer hold_attitude or reduce_rate_partial when the budget is "
    "short, since a manoeuvre that cannot finish spends propellant and leaves "
    "the vehicle part-corrected. When these fields are null the altitude "
    "input is off or unavailable — that means NO INFORMATION about the time "
    "budget, not that time is short. Judge the other evidence on its own and "
    "do not speculate about altitude you were not given."
)

RETRY_NUDGE = (
    "Your previous reply was not valid against the required schema. "
    "Respond again with ONLY a strict JSON object matching the required "
    "keys and allowed values. No code fences, no prose."
)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class ArbiterUnavailable(Exception):
    """Gemma could not produce a valid verdict in time; use fallback.py."""


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text.strip()).strip()


async def _ask_gemma(ev: Evidence) -> Verdict:
    import ollama  # local import: tests and fallback path run without it

    client = ollama.AsyncClient(host=config.OLLAMA_HOST)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": ev.model_dump_json() +
         "\nDiagnose the fault and recommend the action. Respond with JSON only."},
    ]
    last_err: Exception | None = None
    for attempt in range(1 + config.GEMMA_RETRIES):
        kwargs = dict(
            model=config.GEMMA_MODEL,
            messages=messages,
            # schema-constrained decoding: Gemma must emit a Verdict
            format=Verdict.model_json_schema(),
            options=config.GEMMA_OPTIONS,
        )
        try:
            # Gemma 4 defaults to a hidden "thinking" phase that can consume
            # the whole num_predict budget and leave content EMPTY. The
            # verdict schema already demands explicit evidence and an
            # alternative hypothesis, so we disable thinking: faster verdicts
            # and no silent token exhaustion.
            resp = await client.chat(think=False, **kwargs)
        except TypeError:  # older client/server without the think parameter
            resp = await client.chat(**kwargs)
        content = _strip_fences(resp["message"]["content"])
        try:
            return Verdict.model_validate_json(content)
        except Exception as e:  # malformed/out-of-schema: one stricter retry
            last_err = e
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": RETRY_NUDGE})
    raise ArbiterUnavailable(f"unparseable model output: {last_err}")


async def arbitrate(ev: Evidence) -> Tuple[Verdict, str, float, str]:
    """Return (verdict, source, latency_s, note).

    source is "gemma" or "fallback"; the guardrail may later relabel the
    final decision as "guardrail_override". Never raises: the deterministic
    fallback guarantees a decision so the demo cannot stall (P0).
    """
    from . import fallback  # local import to keep module deps one-way

    t0 = time.monotonic()
    if config.ARBITER_FORCE_FALLBACK:
        v = fallback.classify(ev)
        return v, "fallback", time.monotonic() - t0, "forced fallback (config)"
    try:
        v = await asyncio.wait_for(_ask_gemma(ev), timeout=config.GEMMA_TIMEOUT_S)
        return v, "gemma", time.monotonic() - t0, ""
    except asyncio.TimeoutError:
        note = f"gemma timeout after {config.GEMMA_TIMEOUT_S}s"
    except ArbiterUnavailable as e:
        note = str(e)
    except Exception as e:  # connection refused, model missing, ...
        note = f"gemma error: {e.__class__.__name__}: {e}"
    v = fallback.classify(ev)
    return v, "fallback", time.monotonic() - t0, note


async def warmup() -> None:
    """Load the model into memory at server start so the first conflict's
    verdict isn't eaten by model-load time. Failure is fine (offline
    fallback path still works); generous timeout, fire-and-forget."""
    if config.ARBITER_FORCE_FALLBACK:
        return
    try:
        import ollama
        client = ollama.AsyncClient(host=config.OLLAMA_HOST)
        await asyncio.wait_for(
            client.chat(model=config.GEMMA_MODEL,
                        messages=[{"role": "user", "content": "Reply with {} only."}],
                        format="json",
                        options={"num_predict": 8}),
            timeout=180.0,
        )
        print(f"[arbiter] warmup ok: {config.GEMMA_MODEL} resident")
    except Exception as e:
        print(f"[arbiter] warmup skipped ({e.__class__.__name__}: {e}); "
              f"fallback path remains available")
