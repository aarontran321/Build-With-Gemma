"""Guardrail invariant tests: LLM proposes, code disposes."""

from server.guardrail import validate
from server.schemas import Evidence, Verdict


def evidence(**over) -> Evidence:
    base = dict(
        conflict_id=1, gyro_rate=1.6, gyro_saturated=False,
        gyro_rail_score=0.0, gyro_flatline_score=0.1, gyro_variance=0.2,
        gyro_trend=[1.5, 1.6, 1.7, 1.6, 1.5, 1.6, 1.7],
        flow_rate=1.2, flow_quality=0.9, flow_variance=0.3,
        flow_trend=[1.1, 1.2, 1.3, 1.2, 1.1, 1.2, 1.3],
        normalized_rate_difference=0.1, trend_correlation=0.9,
        seconds_diverged=0.0, recent_agreement=[0.95, 0.94, 0.96],
        camera_status="healthy", gyro_status="reporting",
    )
    base.update(over)
    return Evidence.model_validate(base)


def verdict(**over) -> Verdict:
    base = dict(
        fault_class="gyro_saturation", faulty_sensor="gyro",
        trusted_sensor="camera", confidence=0.9,
        evidence=["gyro pinned"], alternative_hypothesis="camera fine",
        recommended_action="use camera", decision="switch_to_camera",
    )
    base.update(over)
    return Verdict.model_validate(base)


RAILED = dict(gyro_rail_score=0.99, gyro_saturated=True, gyro_status="railed",
              gyro_variance=0.001, normalized_rate_difference=0.95,
              seconds_diverged=1.2)
DARK = dict(flow_quality=0.03, camera_status="unavailable",
            normalized_rate_difference=1.0, seconds_diverged=1.2)


def test_consistent_verdict_passes_through_untouched():
    v = verdict()
    final, overrode, reason = validate(v, evidence(**RAILED))
    assert not overrode and reason is None
    assert final == v, "guardrail must not edit a valid verdict"


def test_never_trust_a_railed_gyro():
    v = verdict(fault_class="camera_degradation", faulty_sensor="camera",
                trusted_sensor="gyro", decision="continue_with_gyro")
    final, overrode, reason = validate(v, evidence(**RAILED))
    assert overrode and "rail" in reason
    assert final.trusted_sensor == "camera"
    assert final.decision == "switch_to_camera"


def test_never_trust_an_unavailable_camera():
    v = verdict(fault_class="gyro_saturation", trusted_sensor="camera",
                decision="switch_to_camera")
    final, overrode, reason = validate(v, evidence(**DARK))
    assert overrode
    assert final.decision == "continue_with_gyro"


def test_both_unreliable_downgrades_to_caution():
    ev = evidence(**{**RAILED, **DARK})
    v = verdict()  # proposes a hard switch to a dead camera
    final, overrode, _ = validate(v, ev)
    assert overrode
    assert final.decision == "trust_neither_enter_caution"
    assert final.trusted_sensor == "none"


def test_trusting_the_declared_faulty_sensor_is_rejected():
    v = verdict(faulty_sensor="camera", trusted_sensor="camera")
    final, overrode, reason = validate(v, evidence(**RAILED))
    assert overrode and "faulty" in reason


def test_decision_trusted_sensor_mismatch_is_rejected():
    v = verdict(faulty_sensor="camera", trusted_sensor="gyro",
                decision="switch_to_camera")
    _, overrode, reason = validate(v, evidence(**RAILED))
    assert overrode and "inconsistent" in reason


def test_overconfident_hard_decision_on_clean_evidence_downgraded():
    v = verdict(confidence=0.98)  # hard switch, but no fault signature at all
    final, overrode, reason = validate(v, evidence())
    assert overrode and "overconfident" in reason
    assert final.decision == "trust_neither_enter_caution"


def test_cautious_verdict_on_ambiguous_evidence_is_allowed():
    v = verdict(fault_class="transient_disagreement", faulty_sensor="none",
                trusted_sensor="both", confidence=0.6,
                decision="observe_transient_conflict")
    _, overrode, _ = validate(v, evidence())
    assert not overrode
