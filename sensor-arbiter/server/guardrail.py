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

Attitude-control invariants (validate_control):
  C1  never fire thrusters off a rate source with no signed axis — the
      camera proxy is magnitude-only, so a camera-trusted verdict cannot
      command a manoeuvre no matter how confident the model is
  C2  never aim a burn with a railed or flatlined gyro (a limit is not a
      measurement)
  C3  never burn on an unstable axis estimate (a tumble has no single axis)
  C4  never burn inside the rate deadband (propellant for nothing)
  C5  burn magnitude is always the deterministic figure from despin.py,
      never a number the model produced, and is clamped to the single-burn
      limit

(Malformed / out-of-schema model output is invariant I0, enforced upstream:
arbiter.py rejects anything that fails Verdict validation before it can
reach this module.)
"""

from typing import Optional, Tuple

from . import config, despin
from .schemas import ControlAction, Evidence, Verdict

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


def validate_control(verdict: Verdict, ev: Evidence
                     ) -> Tuple[ControlAction, bool, Optional[str]]:
    """Turn the model's manoeuvre INTENT into the command that will execute.

    Returns (action, overridden, reason). The magnitudes always come from
    despin.py — the model is never the source of a burn number (C5) — so
    "overridden" here means the model's *intent* was refused, not that a
    figure was corrected.

    Note the direction of the check: the guardrail can always veto a burn,
    and can never invent one the physics does not support. If the model asked
    to hold while a de-spin was in fact available, that is left alone: not
    firing is the conservative outcome, and second-guessing it would put this
    module back in the business of deciding, which is precisely what the
    architecture forbids.
    """
    computed = despin.recommend(ev, verdict.trusted_sensor)
    wants_burn = verdict.proposed_manoeuvre in ("despin", "reduce_rate_partial")

    # C1-C4: the model wants to fire but the evidence cannot aim it.
    if wants_burn and not computed.feasible:
        reason = computed.rationale.replace("no de-spin commanded: ", "")
        return computed, True, f"manoeuvre refused — {reason}"

    if not wants_burn:
        # Model chose to hold. Honour it, but keep the computed figures on the
        # record so the report can show what was available and declined.
        held = ControlAction(
            manoeuvre="hold_attitude",
            axis=ev.spin_axis,
            fire_direction="none",
            burn_s=0.0,
            thrust_fraction=0.0,
            measured_rate_rad_s=ev.spin_rate_signed,
            rationale=("attitude hold: arbiter did not call for a correction"
                       + (f" (a {computed.burn_s}s burn about {computed.axis} "
                          f"was available)" if computed.feasible else
                          f" — {computed.rationale}")),
            feasible=computed.feasible,
        )
        return held, False, None

    # Model wants a burn and the physics supports one. The axis is taken from
    # the MEASUREMENT, not the model: if it named a different axis, that is a
    # reasoning slip on a value it was handed, and firing about the wrong axis
    # would add momentum rather than remove it.
    if verdict.manoeuvre_axis != computed.axis:
        r = (f"manoeuvre axis corrected {verdict.manoeuvre_axis} -> "
             f"{computed.axis} (dominant measured spin axis)")
        return computed, True, r

    # A partial correction is a legitimate cautious choice: keep the model's
    # restraint, but scale the deterministic burn rather than trusting a
    # model-supplied fraction.
    if verdict.proposed_manoeuvre == "reduce_rate_partial":
        partial = computed.model_copy(update={
            "manoeuvre": "reduce_rate_partial",
            "burn_s": round(computed.burn_s * 0.5, 2),
            "rationale": computed.rationale + " (partial: half-impulse by request)",
        })
        return partial, False, None

    return computed, False, None
