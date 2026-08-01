"""Minimal deterministic fallback classifier.

This is NOT the guardrail (guardrail.py validates safety invariants only)
and it is NOT a second Gemma. It is used ONLY when Gemma times out or
errors, and it is intentionally dumber than the model: it handles just the
obvious unambiguous cases well enough to carry a golden run to a correct
outcome. It does not attempt the full seven-class taxonomy or ambiguous
evidence — anything unclear becomes `unknown` + trust-neither CAUTION.

The dashboard labels decisions produced here as source="fallback".
"""

from . import config
from .schemas import Evidence, Verdict


def classify(ev: Evidence) -> Verdict:
    gyro_bad = ev.gyro_rail_score > 0.8 or ev.gyro_flatline_score > 0.9
    camera_dead = ev.flow_quality < config.FLOW_CONFIDENCE_FLOOR
    camera_healthy = ev.flow_quality >= config.FLOW_QUALITY_HEALTHY

    # Obvious case 1: gyro clearly railed/flatlined against a healthy camera.
    if gyro_bad and camera_healthy:
        fault = "gyro_saturation" if ev.gyro_rail_score > 0.8 else "gyro_flatline"
        return Verdict(
            fault_class=fault,
            faulty_sensor="gyro",
            trusted_sensor="camera",
            confidence=0.75,  # deliberately modest: rule, not reasoning
            evidence=[
                f"gyro rail score {ev.gyro_rail_score} with variance {ev.gyro_variance}",
                f"camera quality {ev.flow_quality} remains healthy",
            ],
            alternative_hypothesis="not evaluated (deterministic fallback rule)",
            recommended_action="use camera-derived attitude while gyro is pinned",
            decision="switch_to_camera",
        )

    # Obvious case 2: camera dark/obstructed against a reporting gyro.
    if camera_dead and ev.gyro_status == "reporting":
        return Verdict(
            fault_class="camera_obstruction_or_darkness",
            faulty_sensor="camera",
            trusted_sensor="gyro",
            confidence=0.75,
            evidence=[
                f"camera quality collapsed to {ev.flow_quality}",
                "gyro remains within range and varying normally",
            ],
            alternative_hypothesis="not evaluated (deterministic fallback rule)",
            recommended_action="continue on IMU; camera cannot corroborate",
            decision="continue_with_gyro",
        )

    # Obvious case 3: short-lived divergence already heading back to agreement.
    recovering = (
        len(ev.recent_agreement) >= 2
        and ev.recent_agreement[-1] > ev.recent_agreement[-2]
    )
    if ev.seconds_diverged < 2.0 and not gyro_bad and not camera_dead and recovering:
        return Verdict(
            fault_class="transient_disagreement",
            faulty_sensor="none",
            trusted_sensor="both",
            confidence=0.6,
            evidence=[
                f"divergence lasted only {ev.seconds_diverged}s",
                "agreement already recovering; no rail/flatline/quality signature",
            ],
            alternative_hypothesis="not evaluated (deterministic fallback rule)",
            recommended_action="hold configuration and keep monitoring",
            decision="observe_transient_conflict",
        )

    # Everything else is beyond this classifier by design: fail safe.
    return Verdict(
        fault_class="unknown",
        faulty_sensor="none",
        trusted_sensor="none",
        confidence=0.4,
        evidence=["evidence does not match an unambiguous fallback rule"],
        alternative_hypothesis="not evaluated (deterministic fallback rule)",
        recommended_action="enter caution mode pending further evidence",
        decision="trust_neither_enter_caution",
    )
