import pytest
from pydantic import ValidationError

from server.schemas import Evidence, FinalDecision, PhoneSample, Verdict


def test_phone_sample_parses_contract_fields():
    s = PhoneSample.model_validate({
        "t": 1730500000.123,
        "gyro": {"x": 0.1, "y": 0.0, "z": 4.2},
        "gyro_mag": 4.2,
        "flow_mag": 0.9,
        "flow_confidence": 0.85,
        "raw_saturated": False,
    })
    assert s.gyro_mag == 4.2
    assert s.injected is None


def test_phone_sample_rejects_out_of_range_confidence():
    with pytest.raises(ValidationError):
        PhoneSample.model_validate({
            "t": 1.0, "gyro": {"x": 0, "y": 0, "z": 0},
            "gyro_mag": 0.0, "flow_mag": 0.0, "flow_confidence": 1.5,
        })


def _verdict_dict(**over):
    d = {
        "fault_class": "gyro_saturation",
        "faulty_sensor": "gyro",
        "trusted_sensor": "camera",
        "confidence": 0.96,
        "evidence": ["gyro pinned", "camera responsive"],
        "alternative_hypothesis": "camera degradation unlikely; quality high",
        "recommended_action": "retain parachute and use camera-derived attitude",
        "decision": "switch_to_camera",
    }
    d.update(over)
    return d


def test_verdict_roundtrip():
    v = Verdict.model_validate(_verdict_dict())
    assert v.decision == "switch_to_camera"
    assert Verdict.model_validate(v.model_dump()) == v


def test_verdict_rejects_unknown_decision():
    with pytest.raises(ValidationError):
        Verdict.model_validate(_verdict_dict(decision="panic_and_reboot"))


def test_verdict_rejects_unknown_fault_class():
    with pytest.raises(ValidationError):
        Verdict.model_validate(_verdict_dict(fault_class="gremlins"))


def test_evidence_requires_status_literals():
    base = {
        "conflict_id": 1, "gyro_rate": 34.0, "gyro_saturated": True,
        "gyro_rail_score": 0.99, "gyro_flatline_score": 0.95,
        "gyro_variance": 0.01, "gyro_trend": [1, 2, 34],
        "flow_rate": 4.1, "flow_quality": 0.91, "flow_variance": 0.6,
        "flow_trend": [1, 2, 4], "normalized_rate_difference": 0.92,
        "trend_correlation": 0.18, "seconds_diverged": 1.2,
        "recent_agreement": [0.9, 0.5, 0.1],
        "camera_status": "healthy", "gyro_status": "railed",
    }
    assert Evidence.model_validate(base).camera_status == "healthy"
    with pytest.raises(ValidationError):
        Evidence.model_validate({**base, "camera_status": "fine-ish"})


def test_final_decision_source_labels():
    v = Verdict.model_validate(_verdict_dict())
    fd = FinalDecision(conflict_id=1, verdict=v, source="fallback",
                       arbitration_latency_s=0.01)
    assert fd.source == "fallback"
    with pytest.raises(ValidationError):
        FinalDecision(conflict_id=1, verdict=v, source="oracle")
