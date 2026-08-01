"""Gemma report narrator: the model writes the report's prose.

Second, deliberately separate use of Gemma in this system. The arbiter
(server/arbiter.py) makes a FLIGHT DECISION and is therefore fenced by a
deterministic guardrail. The narrator makes no decision at all — it turns
an already-final, already-validated record into readable English for a
human reviewer. Getting the prose wrong is an editorial problem, not a
safety one, which is why this module is allowed to write freely where the
arbiter is not.

What it is NOT allowed to do is invent facts. Every number in a mission
report has to be traceable to the record, so the same "propose then
verify" shape used for decisions is applied to prose:

  1. Gemma is given a FACT SHEET built deterministically from the report —
     never the raw pipeline, never anything it could misread.
  2. It returns a schema-constrained narrative (Ollama `format`), not free
     text scraped afterwards.
  3. `_unsupported_numbers` re-reads its output and rejects any figure that
     does not appear in the fact sheet. One stricter retry, then the
     deterministic narrative built by mission_log.py is used instead.

So a report is always complete and always honest: with Ollama running it
reads like a person wrote it; with Ollama stopped, or with a model that
drifts off the facts, it silently falls back to the templated text and
labels which one produced it. The tables, evidence block and timeline
around the prose are always deterministic and are never model-written.
"""

import asyncio
import re
import time
from typing import List, Optional, Set, Tuple

from pydantic import BaseModel, Field

from . import config

SYSTEM_PROMPT = (
    "You are a flight-systems engineer writing the incident report for a "
    "spacecraft descent fault. You are given a FACT SHEET describing one "
    "already-final, already-validated event. Write clear, factual, "
    "non-promotional English for an engineer who was not present.\n\n"
    "ABSOLUTE RULES:\n"
    "1. Use ONLY facts from the fact sheet. Never invent a number, a "
    "sensor, a time, a threshold or an outcome.\n"
    "2. Every numeric value you write must appear verbatim in the fact "
    "sheet. If you are unsure of a number, describe it in words instead.\n"
    "3. Never claim the guardrail diagnosed or classified anything. The "
    "guardrail only validates a proposal and may veto it. The diagnosis "
    "belongs to whoever the fact sheet names as the proposer.\n"
    "4. Do not speculate about causes that are not in the fact sheet, and "
    "do not offer reassurance. State what happened and why.\n"
    "5. The descent is an accelerated SIMULATION. Never write as though a "
    "real vehicle was lost.\n\n"
    "Return strict JSON only, with keys: headline, summary, "
    "what_happened, why, reviewer_note."
)

RETRY_NUDGE = (
    "Your previous reply used one or more numbers that do not appear in "
    "the fact sheet, or was not valid JSON. Rewrite it. Every numeric "
    "value must appear verbatim in the fact sheet; if in doubt, describe "
    "the quantity in words. Respond with ONLY the JSON object."
)

# Small integers are ordinary English ("two sensors", "stage 3", "once"),
# not claims about telemetry, so they are not treated as facts to verify.
SAFE_INT_MAX = 10
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


class ReportNarrative(BaseModel):
    """The prose layer of a report. Deliberately small: the model writes
    the connective tissue, and the deterministic record supplies every
    table, number and timestamp around it."""

    headline: str = Field(description="One plain-language sentence: what happened.")
    summary: str = Field(description="3-6 sentence executive summary for a reviewer.")
    what_happened: List[str] = Field(
        description="Chronological plain-language beats, 3-6 items.")
    why: List[str] = Field(
        description="Why the system acted as it did, 3-6 items, cause before effect.")
    reviewer_note: str = Field(
        description="What a reviewer should scrutinise, or residual uncertainty.")


class NarratorUnavailable(Exception):
    """Gemma could not produce a fact-faithful narrative; use the
    deterministic text from mission_log.py."""


# ---------------------------------------------------------------------------
# fact sheet
# ---------------------------------------------------------------------------

def _seconds_from_clock(mission_clock: Optional[str]) -> str:
    """"T+01:23.40" -> "83.40". Gives the narrator a plain quotable number
    alongside the formatted clock."""
    if not mission_clock:
        return "0.00"
    try:
        mm, ss = mission_clock.replace("T+", "").split(":")
        return f"{int(mm) * 60 + float(ss):.2f}"
    except (ValueError, AttributeError):
        return "0.00"


def build_fact_sheet(report: dict) -> str:
    """Flatten a report into the only input the narrator may use.

    Deterministic and lossy on purpose: the model sees a curated set of
    already-validated facts, so there is nothing ambiguous for it to
    misread and nothing outside the record for it to reach for.
    """
    h = report.get("header") or {}
    lines: List[str] = [
        f"REPORT TYPE: {report.get('kind')}",
        f"SESSION: {h.get('session_id')}",
        f"DATA SOURCE: {str(h.get('mode', '')).upper()}"
        + (f" (golden replay: {h.get('replay_name')})" if h.get("replay_name") else ""),
        f"ARBITER MODEL: {h.get('model')}",
        "NOTE: the descent below is an ACCELERATED SIMULATION, not a real vehicle.",
    ]

    if report.get("kind") == "conflict":
        ev = report.get("evidence") or {}
        verdict = report.get("verdict") or {}
        proposed = report.get("proposed") or {}
        th = {t["name"]: t["value"] for t in report.get("thresholds") or []}
        lines += [
            "",
            "--- WHAT THE MONITOR DETECTED (deterministic code, no model) ---",
            # Both forms on purpose: the clock string is what the timeline
            # shows, the plain seconds give the model a quotable number so it
            # does not resort to spelling a figure out in words.
            f"conflict opened {_seconds_from_clock(report.get('opened_mission'))} "
            f"seconds into the session (mission clock "
            f"{report.get('opened_mission')})",
            f"normalized divergence between the two sensors: "
            f"{ev.get('normalized_rate_difference')}",
            f"divergence threshold that must be exceeded to arm: "
            f"{th.get('divergence threshold (arm)')}",
            f"time the divergence persisted: {ev.get('seconds_diverged')} s "
            f"(must persist {th.get('candidate persistence')} s to arm)",
            f"trend correlation between the streams: {ev.get('trend_correlation')}",
            f"synthetic fault injected and active at that moment: "
            f"{report.get('trigger') or 'none - this was real sensor input'}",
            "",
            "--- SENSOR EVIDENCE HANDED TO THE ARBITER ---",
            f"gyroscope status: {ev.get('gyro_status')}, rate {ev.get('gyro_rate')} rad/s, "
            f"rail score {ev.get('gyro_rail_score')}, flatline score "
            f"{ev.get('gyro_flatline_score')}, variance {ev.get('gyro_variance')}",
            f"camera status: {ev.get('camera_status')}, rate {ev.get('flow_rate')} "
            f"proxy units, quality {ev.get('flow_quality')}, variance "
            f"{ev.get('flow_variance')}",
            "the gyroscope is calibrated rad/s; the camera proxy is uncalibrated, "
            "so the two are compared by shape and trend only",
        ]
        if report.get("final"):
            proposer = {"gemma": f"the local {h.get('model')} model",
                        "fallback": "the deterministic fallback classifier"}.get(
                            report.get("proposed_source"), "the arbiter")
            lines += [
                "",
                "--- THE DIAGNOSIS (proposed) ---",
                f"proposed by: {proposer}",
                f"time taken to produce it: {report.get('latency_s')} seconds",
                f"fault class: {proposed.get('fault_class')}",
                f"sensor judged faulty: {proposed.get('faulty_sensor')}",
                f"sensor judged trustworthy: {proposed.get('trusted_sensor')}",
                f"proposed action: {proposed.get('decision')}",
                f"confidence: {proposed.get('confidence')}",
                f"stated observations: {'; '.join(proposed.get('evidence') or []) or 'none'}",
                f"alternative hypothesis it ruled out: "
                f"{proposed.get('alternative_hypothesis')}",
                "",
                "--- SAFETY VALIDATION (deterministic guardrail; it never diagnoses) ---",
            ]
            if report.get("guardrail_overrode"):
                lines += [
                    "the guardrail REJECTED the proposal",
                    f"safety invariant that was violated: {report.get('override_reason')}",
                    f"the decision that replaced it: {verdict.get('decision')} "
                    f"(trusting {verdict.get('trusted_sensor')})",
                ]
            else:
                lines += [
                    "the guardrail accepted the proposal unchanged; every safety "
                    "invariant held",
                    f"final flight decision: {verdict.get('decision')} "
                    f"(trusting {verdict.get('trusted_sensor')})",
                ]
            if report.get("note"):
                lines.append(f"arbiter note: {report.get('note')}")
        else:
            lines += ["", "--- THE DIAGNOSIS ---",
                      "the diagnosis had not returned when this report was generated"]
    else:
        stats = report.get("stats") or {}
        lines += [
            "",
            "--- SESSION TOTALS ---",
            f"conflicts opened: {stats.get('conflicts')}",
            f"decisions reached: {stats.get('decided')}",
            f"times the guardrail overrode the proposed diagnosis: {stats.get('overrides')}",
            f"synthetic faults injected: {stats.get('injections')}",
            f"arbitration latency min/mean/max seconds: {stats.get('latency_min')} / "
            f"{stats.get('latency_mean')} / {stats.get('latency_max')}",
        ]
        for c in report.get("conflicts") or []:
            lines.append(
                f"- {c.get('label')}: opened {c.get('opened')}, injected fault "
                f"{c.get('trigger') or 'none'}, diagnosed {c.get('fault_class')}, "
                f"decided {c.get('decision')} trusting {c.get('trusted')}, "
                f"source {c.get('source')}")

    d = report.get("descent_now") or {}
    naive, guarded = d.get("naive", {}), d.get("guarded", {})
    lines += [
        "",
        "--- SIMULATED DESCENT CONSEQUENCE (two vehicles, same sensor input) ---",
        "the NAIVE vehicle fuses the corrupted stream with no arbitration; "
        "the GUARDED vehicle follows the validated decision",
        f"NAIVE outcome: {naive.get('outcome')}, impact speed "
        f"{naive.get('impact_speed')} m/s",
        f"GUARDED outcome: {guarded.get('outcome')}, impact speed "
        f"{guarded.get('impact_speed')} m/s",
        # Worded to remove an ambiguity a model can and did invert: the
        # parachute is RELEASED too early, it is not deployed too early.
        "the failure mechanism modelled is the one that destroyed ESA's "
        "Schiaparelli lander in 2016: a corrupted rotational rate makes the "
        "altitude estimate go negative, so the flight computer believes it has "
        "already landed and RELEASES (cuts away) a parachute that is still "
        "needed; the vehicle then falls freely and hits the ground too fast. "
        "The parachute is jettisoned too early - it is NOT deployed too early",
    ]
    return "\n".join(str(x) for x in lines)


# ---------------------------------------------------------------------------
# numeric verification
# ---------------------------------------------------------------------------

def _numbers_in(text: str) -> List[str]:
    return _NUM_RE.findall(text or "")


def _allowed_numbers(fact_sheet: str) -> Set[float]:
    allowed: Set[float] = set()
    for tok in _numbers_in(fact_sheet):
        try:
            allowed.add(float(tok))
        except ValueError:
            continue
    return allowed


def _unsupported_numbers(narrative: ReportNarrative, fact_sheet: str) -> List[str]:
    """Return figures the narrative asserts that the fact sheet does not.

    This is the prose equivalent of the guardrail: the model may phrase
    things however it likes, but it may not introduce a quantity that is
    not in the record. Small integers are exempt — they are counting
    words, not telemetry.
    """
    allowed = _allowed_numbers(fact_sheet)
    body = " ".join([
        narrative.headline, narrative.summary, narrative.reviewer_note,
        " ".join(narrative.what_happened), " ".join(narrative.why),
    ])
    bad: List[str] = []
    for tok in _numbers_in(body):
        try:
            value = float(tok)
        except ValueError:
            continue
        if value.is_integer() and abs(value) <= SAFE_INT_MAX:
            continue
        # A rounded restatement of a supported figure is acceptable prose;
        # an unrelated invented figure is not.
        if any(abs(value - a) < 1e-6 or (a != 0 and abs(value - a) / abs(a) < 0.005)
               for a in allowed):
            continue
        bad.append(tok)
    return bad


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------

async def _ask_gemma(fact_sheet: str) -> ReportNarrative:
    import ollama  # local import: the deterministic path runs without it

    client = ollama.AsyncClient(host=config.OLLAMA_HOST)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "FACT SHEET:\n" + fact_sheet +
         "\n\nWrite the incident report as JSON only."},
    ]
    last_err: Optional[str] = None
    for _ in range(1 + config.NARRATOR_RETRIES):
        kwargs = dict(
            model=config.GEMMA_MODEL,
            messages=messages,
            format=ReportNarrative.model_json_schema(),
            options=config.NARRATOR_OPTIONS,
        )
        try:
            # Same reason as the arbiter: Gemma 4's hidden thinking phase can
            # eat the whole token budget and return empty content.
            resp = await client.chat(think=False, **kwargs)
        except TypeError:  # older client/server without `think`
            resp = await client.chat(**kwargs)
        content = (resp["message"]["content"] or "").strip()
        try:
            narrative = ReportNarrative.model_validate_json(content)
        except Exception as e:
            last_err = f"unparseable narrative: {e}"
        else:
            bad = _unsupported_numbers(narrative, fact_sheet)
            if not bad:
                return narrative
            last_err = f"narrative asserted unsupported figures: {', '.join(bad)}"
        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": RETRY_NUDGE})
    raise NarratorUnavailable(last_err or "no narrative produced")


def deterministic_narrative(report: dict) -> dict:
    """The always-available narrative, assembled from the templated text
    mission_log.py already built. Never fails, never calls a model."""
    rationale = report.get("rationale") or []
    return {
        "headline": report.get("headline") or report.get("title") or "Mission report",
        "summary": report.get("summary") or "",
        "what_happened": [f"{s['stage'].split('·')[-1].strip()}: {s['finding']}"
                          for s in rationale],
        "why": [f"{s['stage'].split('·')[-1].strip()}: {s['action']}"
                for s in rationale],
        "reviewer_note": ("Written deterministically from the mission record. "
                          "Every figure above is taken directly from the log."),
        "source": "deterministic",
        "model": None,
        "latency_s": 0.0,
        "note": "",
    }


async def narrate(report: dict) -> Tuple[dict, str, float]:
    """Return (narrative_dict, source, latency_s).

    Never raises and never blocks a decision: a report always has prose,
    and the caller can always tell which engine wrote it.
    """
    t0 = time.monotonic()
    if not config.NARRATOR_ENABLED or config.ARBITER_FORCE_FALLBACK:
        n = deterministic_narrative(report)
        n["note"] = ("narrator disabled by configuration"
                     if not config.NARRATOR_ENABLED else "forced fallback (config)")
        return n, "deterministic", time.monotonic() - t0

    fact_sheet = build_fact_sheet(report)
    try:
        narrative = await asyncio.wait_for(_ask_gemma(fact_sheet),
                                           timeout=config.NARRATOR_TIMEOUT_S)
        latency = time.monotonic() - t0
        return ({**narrative.model_dump(), "source": "gemma",
                 "model": config.GEMMA_MODEL, "latency_s": round(latency, 2),
                 "note": ""}, "gemma", latency)
    except asyncio.TimeoutError:
        note = f"narrator timeout after {config.NARRATOR_TIMEOUT_S}s"
    except NarratorUnavailable as e:
        note = str(e)
    except Exception as e:  # ollama down, model missing, ...
        note = f"narrator error: {e.__class__.__name__}: {e}"

    latency = time.monotonic() - t0
    n = deterministic_narrative(report)
    n["note"] = note
    n["latency_s"] = round(latency, 2)
    return n, "deterministic", latency
