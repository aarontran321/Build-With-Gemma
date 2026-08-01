"""De-spin sizing and the attitude-control guardrail invariants (C1-C5)."""

import pytest

from server import config, despin, guardrail
from server.descent import DualDescent
from server.monitor import MonitorFrame
from server.schemas import Evidence, FinalDecision, GyroVec, PhoneSample, Verdict


def evidence(**kw) -> Evidence:
    base = dict(
        conflict_id=1, gyro_rate=2.0, gyro_saturated=False, gyro_rail_score=0.0,
        gyro_flatline_score=0.0, gyro_variance=0.1, gyro_trend=[2.0] * 7,
        flow_rate=1.5, flow_quality=0.85, flow_variance=0.05,
        flow_trend=[1.5] * 7, normalized_rate_difference=0.6,
        trend_correlation=0.5, seconds_diverged=1.0,
        recent_agreement=[0.9, 0.8, 0.5, 0.3, 0.3, 0.3],
        camera_status="healthy", gyro_status="reporting",
        spin_axis="z", spin_rate_signed=2.0, spin_axis_stability=0.95,
        gyro_axis_rates={"x": 0.1, "y": 0.2, "z": 2.0},
    )
    base.update(kw)
    return Evidence(**base)


def verdict(**kw) -> Verdict:
    base = dict(
        fault_class="camera_obstruction_or_darkness", faulty_sensor="camera",
        trusted_sensor="gyro", confidence=0.8, evidence=["e"],
        alternative_hypothesis="a", recommended_action="r",
        decision="continue_with_gyro", proposed_manoeuvre="despin",
        manoeuvre_axis="z",
    )
    base.update(kw)
    return Verdict(**base)


# --------------------------- sizing arithmetic ---------------------------

def test_burn_matches_angular_impulse():
    """t = I*omega/tau, exactly. This is the number a thruster acts on."""
    ev = evidence(spin_rate_signed=3.0)
    b = despin.size_burn(ev)
    expected = config.SPACECRAFT_INERTIA_KG_M2 * 3.0 / config.THRUSTER_MAX_TORQUE_NM
    assert b["angular_impulse_kg_m2_s"] == pytest.approx(
        config.SPACECRAFT_INERTIA_KG_M2 * 3.0, rel=1e-3)
    assert b["burn_s_full_authority"] == pytest.approx(expected, rel=1e-3)


def test_fire_direction_opposes_spin():
    pos = despin.recommend(evidence(spin_rate_signed=2.0), "gyro")
    neg = despin.recommend(evidence(spin_rate_signed=-2.0), "gyro")
    assert pos.fire_direction == "negative"
    assert neg.fire_direction == "positive"


def test_long_burn_is_clamped_not_silently_truncated():
    ev = evidence(spin_rate_signed=50.0)   # absurd rate
    b = despin.size_burn(ev)
    assert b["truncated"] is True
    assert b["burn_s"] == config.DESPIN_MAX_BURN_S
    assert b["thrust_fraction"] < 1.0


# --------------------- C1: the camera cannot aim a burn ---------------------

def test_camera_trusted_cannot_despin():
    """The headline invariant: the flow proxy is magnitude-only, so a
    camera-trusted verdict has no axis or sign to fire against."""
    ok, why = despin.despin_feasible(evidence(), "camera")
    assert ok is False
    assert "no signed axis" in why


def test_guardrail_refuses_burn_when_camera_trusted():
    v = verdict(trusted_sensor="camera", faulty_sensor="gyro",
                decision="switch_to_camera", proposed_manoeuvre="despin")
    action, overridden, reason = guardrail.validate_control(v, evidence())
    assert overridden is True
    assert action.manoeuvre == "hold_attitude"
    assert action.burn_s == 0.0
    assert "no signed axis" in reason


# ------------------- C2/C3/C4: unusable rate references -------------------

@pytest.mark.parametrize("kw,frag", [
    (dict(gyro_status="railed"), "pinned at its rail"),
    (dict(gyro_status="flatlined"), "frozen"),
    (dict(spin_rate_signed=0.05), "deadband"),
    (dict(spin_axis_stability=0.1), "unstable"),
])
def test_infeasible_rate_references(kw, frag):
    ok, why = despin.despin_feasible(evidence(**kw), "gyro")
    assert ok is False
    assert frag in why


def test_guardrail_corrects_wrong_axis_rather_than_firing_it():
    """Firing about the wrong axis adds momentum instead of removing it."""
    v = verdict(manoeuvre_axis="x")
    action, overridden, reason = guardrail.validate_control(v, evidence())
    assert overridden is True
    assert action.axis == "z"
    assert "axis corrected" in reason


# --------------------------- honouring restraint ---------------------------

def test_hold_is_honoured_and_records_what_was_declined():
    v = verdict(proposed_manoeuvre="hold_attitude")
    action, overridden, _ = guardrail.validate_control(v, evidence())
    assert overridden is False          # never second-guess NOT firing
    assert action.manoeuvre == "hold_attitude"
    assert "was available" in action.rationale


def test_partial_scales_the_deterministic_burn():
    full, _, _ = guardrail.validate_control(verdict(), evidence())
    part, _, _ = guardrail.validate_control(
        verdict(proposed_manoeuvre="reduce_rate_partial"), evidence())
    # burn_s is rounded to 2 dp on the way out, so compare at that precision
    assert part.burn_s == pytest.approx(full.burn_s * 0.5, abs=0.01)


# ------------------------- execution in the sim -------------------------

def _fly(action, rate, seconds=6.0, hz=25):
    sim = DualDescent()
    fd = FinalDecision(conflict_id=1, verdict=verdict(), source="fallback",
                       control_action=action)
    f = MonitorFrame(
        t=0.0, gyro_mag=abs(rate), flow_mag=1.0, flow_confidence=0.9,
        gyro_norm=0.5, flow_norm=0.5, divergence=0.1, agreement=0.9,
        gyro_rail_score=0.0, gyro_flatline_score=0.0, flow_quality=0.9,
        camera_status="healthy", gyro_status="reporting", state="NORMAL",
        conflict_id=1, spin_rate_signed=rate)
    s = PhoneSample(t=0.0, gyro=GyroVec(x=0, y=0, z=rate), gyro_mag=abs(rate),
                    flow_mag=1.0, flow_confidence=0.9)
    sim.step(s, f)
    sim.apply_decision(fd, 0.0)
    for i in range(1, int(seconds * hz)):
        s.t = f.t = i / hz
        sim.step(s, f)
    return sim


def test_burn_nulls_the_spin_and_does_not_reverse_it():
    ev = evidence(spin_rate_signed=2.0)
    action = despin.recommend(ev, "gyro")
    sim = _fly(action, 2.0)
    assert sim.snapshot()["attitude"]["despin_done"] is True
    # nulled, and critically NOT spun up the other way
    assert abs(sim.body_rate) < 0.05
    assert sim.body_rate > -0.05


def test_residual_requirement_falls_to_zero_as_the_burn_takes_effect():
    """The live readout must track reality, not stay stuck on the figure that
    was correct at decision time."""
    ev = evidence(spin_rate_signed=2.0)
    action = despin.recommend(ev, "gyro")
    assert action.burn_s > 0

    sim = DualDescent()
    f = MonitorFrame(
        t=0.0, gyro_mag=2.0, flow_mag=1.0, flow_confidence=0.9, gyro_norm=0.5,
        flow_norm=0.5, divergence=0.1, agreement=0.9, gyro_rail_score=0.0,
        gyro_flatline_score=0.0, flow_quality=0.9, camera_status="healthy",
        gyro_status="reporting", state="NORMAL", conflict_id=1,
        spin_rate_signed=2.0)
    s = PhoneSample(t=0.0, gyro=GyroVec(x=0, y=0, z=2.0), gyro_mag=2.0,
                    flow_mag=1.0, flow_confidence=0.9)
    sim.step(s, f)
    before = sim.attitude_snapshot()
    assert before["residual_burn_s"] > 0
    assert before["settled"] is False

    fd = FinalDecision(conflict_id=1, verdict=verdict(), source="fallback",
                       control_action=action)
    sim.apply_decision(fd, 0.0)
    for i in range(1, 150):
        s.t = f.t = i / 25
        sim.step(s, f)

    after = sim.attitude_snapshot()
    assert after["settled"] is True
    assert after["residual_burn_s"] == 0.0
    assert after["residual_thrust_fraction"] == 0.0
    assert after["burning"] is False


def test_readout_stays_live_after_the_burn():
    """A completed de-spin must not freeze the readout. If the vehicle spins
    up again afterwards, the requirement has to come back."""
    sim = DualDescent()
    f = MonitorFrame(
        t=0.0, gyro_mag=2.0, flow_mag=1.0, flow_confidence=0.9, gyro_norm=0.5,
        flow_norm=0.5, divergence=0.1, agreement=0.9, gyro_rail_score=0.0,
        gyro_flatline_score=0.0, flow_quality=0.9, camera_status="healthy",
        gyro_status="reporting", state="NORMAL", conflict_id=1,
        spin_rate_signed=2.0)
    s = PhoneSample(t=0.0, gyro=GyroVec(x=0, y=0, z=2.0), gyro_mag=2.0,
                    flow_mag=1.0, flow_confidence=0.9)
    sim.step(s, f)
    sim.apply_decision(
        FinalDecision(conflict_id=1, verdict=verdict(), source="fallback",
                      control_action=despin.recommend(evidence(spin_rate_signed=2.0),
                                                      "gyro")),
        0.0)
    for i in range(1, 150):
        s.t = f.t = i / 25
        sim.step(s, f)
    assert sim.attitude_snapshot()["settled"] is True

    # now the vehicle spins up again — the panel must react, not stay at zero
    for i in range(150, 200):
        s.t = f.t = i / 25
        f.spin_rate_signed = 4.0
        sim.step(s, f)
    after = sim.attitude_snapshot()
    assert after["settled"] is False
    assert after["residual_burn_s"] > 0
    assert abs(after["body_rate"]) > config.DESPIN_DEADBAND_RAD_S


def test_settled_flag_tracks_the_deadband():
    assert despin.burn_for_rate(0.0)["settled"] is True
    assert despin.burn_for_rate(config.DESPIN_DEADBAND_RAD_S * 0.5)["settled"] is True
    assert despin.burn_for_rate(config.DESPIN_DEADBAND_RAD_S * 2)["settled"] is False


def test_refused_manoeuvre_commits_no_propellant():
    refused = despin.recommend(evidence(), "camera")     # infeasible
    sim = _fly(refused, 2.0)
    assert sim.snapshot()["attitude"]["burning"] is False
    assert sim.snapshot()["attitude"]["despin_done"] is False
