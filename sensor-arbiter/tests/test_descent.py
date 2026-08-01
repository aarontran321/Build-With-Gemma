"""Dual descent tests: naive crashes on persistent corruption, guarded
lands, and a transient corrupts neither outcome."""

from server import config
from server.descent import DualDescent
from server.monitor import MonitorFrame
from server.schemas import FinalDecision, GyroVec, PhoneSample, Verdict

HZ = 25
DT = 1.0 / HZ


def sample(t, gyro=1.6, flow=1.2, conf=0.85, injected=None,
           clean_gyro=None, clean_flow=None) -> PhoneSample:
    return PhoneSample(t=t, gyro=GyroVec(x=0, y=0, z=gyro), gyro_mag=gyro,
                       flow_mag=flow, flow_confidence=conf, injected=injected,
                       clean_gyro_mag=clean_gyro, clean_flow_mag=clean_flow)


def frame(t, state="NORMAL") -> MonitorFrame:
    return MonitorFrame(t=t, gyro_mag=1.6, flow_mag=1.2, flow_confidence=0.85,
                        gyro_norm=0.4, flow_norm=0.4, divergence=0.0,
                        agreement=1.0, gyro_rail_score=0.0,
                        gyro_flatline_score=0.0, flow_quality=0.85,
                        camera_status="healthy", gyro_status="reporting",
                        state=state, conflict_id=None)


def switch_decision() -> FinalDecision:
    v = Verdict(fault_class="gyro_saturation", faulty_sensor="gyro",
                trusted_sensor="camera", confidence=0.9,
                evidence=["gyro pinned"], alternative_hypothesis="n/a",
                recommended_action="use camera", decision="switch_to_camera")
    return FinalDecision(conflict_id=1, verdict=v, source="gemma",
                         arbitration_latency_s=1.0)


def run(sim, seconds, make_sample, make_state="NORMAL", decision_at=None,
        decision=None):
    n = int(seconds * HZ)
    applied = False
    for i in range(n):
        t = i * DT
        st = make_state(t) if callable(make_state) else make_state
        if decision_at is not None and not applied and t >= decision_at and decision:
            sim.apply_decision(decision, t)
            applied = True
        sim.step(make_sample(t), frame(t, st))


def test_persistent_gyro_corruption_naive_crashes_guarded_lands():
    sim = DualDescent()
    inject_t = 2.0

    def mk(t):
        if t < inject_t:
            return sample(t)
        return sample(t, gyro=config.GYRO_RAIL_VALUE, injected="gyro_saturation",
                      clean_gyro=1.6)

    def state(t):
        return "NORMAL" if t < inject_t else "ACTIVE"

    run(sim, 60.0, mk, state, decision_at=3.5, decision=switch_decision())

    assert sim.naive.outcome == "CRASH"
    assert sim.naive.impact_speed > 100, "echoes the ~150 m/s class of impact"
    assert any("premature_chute_cut" in e[1] for e in sim.naive.events)
    assert sim.guarded.outcome == "SAFE"
    assert sim.guarded.impact_speed <= config.DESCENT_SAFE_IMPACT_M_S
    assert sim.naive.phase == "LANDED" and sim.guarded.phase == "LANDED"


def test_no_fault_both_land_safe():
    sim = DualDescent()
    run(sim, 60.0, lambda t: sample(t))
    assert sim.naive.outcome == "SAFE"
    assert sim.guarded.outcome == "SAFE"


def test_transient_blip_crashes_neither():
    sim = DualDescent()
    b0, b1 = 10.0, 11.4

    def mk(t):
        if b0 <= t < b1:
            return sample(t, gyro=4.8, injected="transient", clean_gyro=1.6)
        return sample(t)

    run(sim, 60.0, mk)
    assert not any("premature_chute_cut" in e[1] for e in sim.naive.events), \
        "naive filter must re-converge after a transient"
    assert sim.naive.outcome == "SAFE"
    assert sim.guarded.outcome == "SAFE"


def test_caution_decision_slows_guarded_descent():
    sim = DualDescent()
    v = Verdict(fault_class="dual_sensor_degradation", faulty_sensor="both",
                trusted_sensor="none", confidence=0.6, evidence=["both bad"],
                alternative_hypothesis="n/a", recommended_action="caution",
                decision="trust_neither_enter_caution")
    d = FinalDecision(conflict_id=1, verdict=v, source="gemma",
                      arbitration_latency_s=1.0)
    run(sim, 20.0, lambda t: sample(t), decision_at=5.0, decision=d)
    assert sim.guarded.rate == config.DESCENT_CAUTION_RATE_M_S
    assert sim.guarded.alt > sim.naive.alt, "caution mode descends slower"
