"""Fallback classifier tests: minimal rules for the unambiguous cases only."""

from server.fallback import classify
from tests.test_guardrail import evidence  # shared Evidence fixture builder


def test_railed_gyro_healthy_camera_switches_to_camera():
    v = classify(evidence(gyro_rail_score=0.99, gyro_saturated=True,
                          gyro_status="railed", gyro_variance=0.001,
                          normalized_rate_difference=0.95, seconds_diverged=1.2))
    assert v.fault_class == "gyro_saturation"
    assert v.trusted_sensor == "camera"
    assert v.decision == "switch_to_camera"
    assert v.confidence <= 0.8, "fallback confidence stays modest by design"


def test_flatlined_gyro_healthy_camera_switches_to_camera():
    v = classify(evidence(gyro_flatline_score=0.95, gyro_status="flatlined",
                          gyro_variance=0.0001, normalized_rate_difference=0.9,
                          seconds_diverged=1.2))
    assert v.fault_class == "gyro_flatline"
    assert v.decision == "switch_to_camera"


def test_dark_camera_reporting_gyro_continues_on_gyro():
    v = classify(evidence(flow_quality=0.03, camera_status="unavailable",
                          normalized_rate_difference=1.0, seconds_diverged=1.2))
    assert v.fault_class == "camera_obstruction_or_darkness"
    assert v.trusted_sensor == "gyro"
    assert v.decision == "continue_with_gyro"


def test_recovering_short_divergence_is_observed_not_acted_on():
    v = classify(evidence(normalized_rate_difference=0.6, seconds_diverged=0.9,
                          recent_agreement=[0.9, 0.4, 0.3, 0.55]))
    assert v.fault_class == "transient_disagreement"
    assert v.decision == "observe_transient_conflict"


def test_anything_ambiguous_fails_safe_to_caution():
    v = classify(evidence(gyro_rail_score=0.6, flow_quality=0.3,
                          camera_status="degraded",
                          normalized_rate_difference=0.7, seconds_diverged=3.0,
                          recent_agreement=[0.5, 0.4, 0.3]))
    assert v.fault_class == "unknown"
    assert v.decision == "trust_neither_enter_caution"
