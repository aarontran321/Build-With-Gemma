"""Headless end-to-end check of all three golden runs.

Feeds each committed golden run through the real pipeline (monitor ->
arbiter -> guardrail -> descent) at data speed and asserts the definition
of done:

* the conflict lifecycle runs NORMAL -> CANDIDATE -> ACTIVE -> RECOVERING
  -> NORMAL and Gemma is woken exactly once per conflict
* the transient's short first blip is ignored (CANDIDATE -> NORMAL, no call)
* each scenario reaches its correct decision and dual-descent outcome

Modes:
    ARBITER_FORCE_FALLBACK=1 python -m scripts.replay_check   # no Ollama
    python -m scripts.replay_check                            # real Gemma

With the fallback, the transient scenario is allowed to land on
trust_neither_enter_caution: the minimal classifier deliberately refuses
ambiguous cases (that nuance is exactly what Gemma is for).
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import arbiter, config, guardrail
from server.descent import DualDescent
from server.monitor import Monitor
from server.recorder import load_golden
from server.schemas import FinalDecision

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RUNS = {
    "gyro_saturation": "golden_gyro_saturation.jsonl",
    "camera_dark": "golden_camera_dark.jsonl",
    "transient": "golden_transient.jsonl",
}

EXPECT = {
    "gyro_saturation": {
        "decisions": {"switch_to_camera"},
        "fault_classes": {"gyro_saturation"},
        "naive": "CRASH", "guarded": "SAFE",
    },
    "camera_dark": {
        "decisions": {"continue_with_gyro"},
        "fault_classes": {"camera_obstruction_or_darkness", "camera_degradation"},
        "naive": "CRASH", "guarded": "SAFE",
    },
    "transient": {
        "decisions": {"observe_transient_conflict"},
        "fault_classes": {"transient_disagreement"},
        "naive": "SAFE", "guarded": "SAFE",
    },
}

FALLBACK_EXTRA = {
    # the minimal classifier may refuse the ambiguous transient: acceptable
    "transient": ({"observe_transient_conflict", "trust_neither_enter_caution"},
                  {"transient_disagreement", "unknown"}),
}


async def run_one(name: str):
    meta, samples = load_golden(os.path.join(ROOT, config.DATA_DIR, RUNS[name]))
    monitor, descent = Monitor(), DualDescent()
    decisions = []
    for s in samples:
        frame = monitor.ingest(s)
        if frame.wake_evidence is not None:
            ev = frame.wake_evidence
            verdict, source, latency, note = await arbiter.arbitrate(ev)
            final, overrode, reason = guardrail.validate(verdict, ev)
            fd = FinalDecision(conflict_id=ev.conflict_id, verdict=final,
                               source="guardrail_override" if overrode else source,
                               guardrail_overrode=overrode, override_reason=reason,
                               arbitration_latency_s=round(latency, 3))
            decisions.append((fd, note))
            descent.apply_decision(fd, s.t)
        descent.step(s, frame)
    return monitor, descent, decisions


def check(name: str, monitor, descent, decisions, fallback_mode: bool):
    errs = []
    exp = EXPECT[name]
    allowed_decisions = set(exp["decisions"])
    allowed_faults = set(exp["fault_classes"])
    if fallback_mode and name in FALLBACK_EXTRA:
        allowed_decisions, allowed_faults = FALLBACK_EXTRA[name]

    seq = [(t.from_state, t.to_state) for t in monitor.transitions]
    for step_ in [("NORMAL", "CANDIDATE"), ("CANDIDATE", "ACTIVE"),
                  ("ACTIVE", "RECOVERING"), ("RECOVERING", "NORMAL")]:
        if step_ not in seq:
            errs.append(f"lifecycle missing {step_[0]}->{step_[1]}: {seq}")
    if monitor.gemma_call_count != 1:
        errs.append(f"arbiter called {monitor.gemma_call_count} times, want 1")
    if len(decisions) != 1:
        errs.append(f"{len(decisions)} decisions, want 1")
    if name == "transient" and seq.count(("CANDIDATE", "NORMAL")) < 1:
        errs.append("short blip was not ignored (no CANDIDATE->NORMAL)")

    if decisions:
        fd, note = decisions[0]
        if fd.verdict.decision not in allowed_decisions:
            errs.append(f"decision {fd.verdict.decision} not in {allowed_decisions}")
        if fd.verdict.fault_class not in allowed_faults:
            errs.append(f"fault_class {fd.verdict.fault_class} not in {allowed_faults}")
        expected_source = {"fallback"} if fallback_mode else {"gemma"}
        if fd.source not in expected_source | {"guardrail_override"}:
            errs.append(f"source {fd.source} unexpected")

    caution = decisions and decisions[0][0].verdict.decision == "trust_neither_enter_caution"
    want_guarded = exp["guarded"]
    if caution:
        want_guarded = None  # caution descends slowly; may still be airborne
    if descent.naive.outcome != exp["naive"]:
        errs.append(f"naive outcome {descent.naive.outcome}, want {exp['naive']}")
    if want_guarded and descent.guarded.outcome != want_guarded:
        errs.append(f"guarded outcome {descent.guarded.outcome}, want {want_guarded}")
    if caution and descent.guarded.outcome == "CRASH":
        errs.append("guarded path crashed in caution mode")
    return errs


async def main() -> int:
    fallback_mode = config.ARBITER_FORCE_FALLBACK
    label = "FALLBACK (no model)" if fallback_mode else f"GEMMA ({config.GEMMA_MODEL})"
    print(f"== replay check, arbitration path: {label} ==")
    if not fallback_mode:
        print("warming model (first load can take minutes)...")
        await arbiter.warmup()
    failures = 0
    for name in RUNS:
        monitor, descent, decisions = await run_one(name)
        errs = check(name, monitor, descent, decisions, fallback_mode)
        d = decisions[0][0] if decisions else None
        print(f"\n--- {name} ---")
        if d:
            print(f"  verdict : {d.verdict.fault_class} | trusted={d.verdict.trusted_sensor} "
                  f"| decision={d.verdict.decision} | conf={d.verdict.confidence}")
            print(f"  source  : {d.source} (overrode={d.guardrail_overrode}) "
                  f"latency={d.arbitration_latency_s}s")
            if decisions[0][1]:
                print(f"  note    : {decisions[0][1]}")
            for line in d.verdict.evidence[:3]:
                print(f"  evidence: {line}")
        print(f"  descent : naive={descent.naive.outcome} "
              f"(impact {descent.naive.impact_speed} m/s), "
              f"guarded={descent.guarded.outcome} "
              f"(impact {descent.guarded.impact_speed} m/s)")
        print(f"  arbiter calls: {monitor.gemma_call_count}, "
              f"transitions: {[(t.from_state[0], t.to_state[0]) for t in monitor.transitions]}")
        if errs:
            failures += 1
            for e in errs:
                print(f"  FAIL: {e}")
        else:
            print("  OK")
    print(f"\n{'ALL GOOD' if failures == 0 else f'{failures} SCENARIO(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
