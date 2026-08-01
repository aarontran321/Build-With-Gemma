"""Deterministic guardrail: SAFETY-INVARIANT VALIDATION ONLY.

LLM proposes, code disposes (hard constraint 4). This module takes the
diagnosis proposed by Gemma (or by fallback.py) and validates it against a
small set of high-confidence safety invariants before it becomes the
displayed flight decision. It is deliberately THIN: it never re-classifies
the fault and never duplicates the arbiter's reasoning — that would defeat
the point of the architecture. If a proposal violates an invariant, the
guardrail overrides it, visibly, with a stated reason.

Invariants enforced here:
  I1  internal consistency (trusted != faulty; decision matches trusted)
  I2  never trust a sensor explicitly unavailable
  I3  never trust a gyro unmistakably pinned at the rail value
  I4  never trust camera flow when camera quality is effectively zero
  I5  never keep an irreversible/hard action when BOTH sensors are
      unreliable -> downgrade to CAUTION
  I6  downgrade an overconfident hard decision to CAUTION when the evidence
      carries no clear fault signature at all (fundamentally ambiguous)

(Malformed / out-of-schema model output is invariant I0, enforced upstream:
arbiter.py rejects anything that fails Verdict validation before it can
reach this module.)
"""

from typing import Optional, Tuple

from . import config
from .schemas import Evidence, Verdict

HARD_DECISIONS = {"switch_to_camera", "continue_with_gyro"}


def _safe_override(ev: Evidence, reason: str) -> Verdict:
    """Construct the conservative replacement decision from the evidence.

    This is NOT a re-classification: it only picks the safe direction the
    violated invariant already implies (trust the clearly-healthy sensor if
    exactly one exists, otherwise CAUTION).
    """
    gyro_pinned = ev.gyro_rail_score > config.RAIL_SCORE_TRUST_LIMIT
    camera_dead = ev.flow_quality < config.FLOW_CONFIDENCE_FLOOR
    if gyro_pinned and not camera_dead:
        trusted, decision, action = "camera", "switch_to_camera", "use camera-derived attitude"
        faulty = "gyro"
    elif camera_dead and not gyro_pinned:
        trusted, decision, action = "gyro", "continue_with_gyro", "continue on IMU"
        faulty = "camera"
    else:
        trusted, decision, action = "none", "trust_neither_enter_caution", "enter caution mode"
        faulty = "both" if (gyro_pinned and camera_dead) else "none"
    return Verdict(
        fault_class="unknown",
        faulty_sensor=faulty,
        trusted_sensor=trusted,
        confidence=0.5,
        evidence=[f"guardrail override: {reason}"],
        alternative_hypothesis="n/a (safety override, not a diagnosis)",
        recommended_action=action,
        decision=decision,
    )


def validate(verdict: Verdict, ev: Evidence) -> Tuple[Verdict, bool, Optional[str]]:
    """Return (final_verdict, overrode, reason)."""
    gyro_pinned = ev.gyro_rail_score > config.RAIL_SCORE_TRUST_LIMIT
    camera_dead = ev.flow_quality < config.FLOW_CONFIDENCE_FLOOR

    # I1: internal consistency
    if verdict.trusted_sensor == verdict.faulty_sensor and verdict.trusted_sensor not in ("none", "both"):
        r = "diagnosis trusts the sensor it also declares faulty"
        return _safe_override(ev, r), True, r
    if verdict.decision == "switch_to_camera" and verdict.trusted_sensor != "camera":
        r = "decision switch_to_camera inconsistent with trusted_sensor"
        return _safe_override(ev, r), True, r
    if verdict.decision == "continue_with_gyro" and verdict.trusted_sensor != "gyro":
        r = "decision continue_with_gyro inconsistent with trusted_sensor"
        return _safe_override(ev, r), True, r

    # I2: never trust a sensor explicitly unavailable
    if verdict.trusted_sensor == "camera" and ev.camera_status == "unavailable":
        r = "proposed trusting a camera marked unavailable"
        return _safe_override(ev, r), True, r

    # I3: never trust a gyro unmistakably pinned at the injected rail value
    if verdict.trusted_sensor == "gyro" and gyro_pinned:
        r = f"proposed trusting a gyro pinned at rail (rail score {ev.gyro_rail_score})"
        return _safe_override(ev, r), True, r

    # I4: never trust camera flow with effectively zero quality
    if verdict.trusted_sensor == "camera" and camera_dead:
        r = f"proposed trusting camera flow with quality {ev.flow_quality}"
        return _safe_override(ev, r), True, r

    # I5: both sensors unreliable -> nothing irreversible
    if gyro_pinned and camera_dead and verdict.decision in HARD_DECISIONS:
        r = "both sensors unreliable; hard sensor selection is not permitted"
        return _safe_override(ev, r), True, r

    # I6: overconfident hard decision with no clear fault signature anywhere
    no_signature = (
        ev.gyro_rail_score < 0.5
        and ev.gyro_flatline_score < 0.5
        and ev.flow_quality >= config.FLOW_QUALITY_HEALTHY
        and not ev.gyro_saturated
    )
    if no_signature and verdict.decision in HARD_DECISIONS and verdict.confidence > 0.9:
        r = "overconfident hard decision on ambiguous evidence; downgraded to caution"
        return _safe_override(ev, r), True, r

    # Consistent with evidence and invariants: pass it through untouched.
    return verdict, False, None
