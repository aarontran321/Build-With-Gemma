"""Conflict state machine and detection-feature tests.

The lifecycle contract under test (hard constraint 2):
NORMAL -> CANDIDATE -> ACTIVE -> RECOVERING -> NORMAL, Gemma woken exactly
once per conflict on the transition into ACTIVE, monotonic conflict_id,
sub-threshold blips ignored, cooldown enforced.
"""

import math

from server import config
from server.monitor import Monitor
from server.schemas import GyroVec, PhoneSample

HZ = 25
DT = 1.0 / HZ


def sample(t: float, gyro: float, flow: float, conf: float = 0.85) -> PhoneSample:
    return PhoneSample(t=t, gyro=GyroVec(x=0, y=0, z=gyro), gyro_mag=gyro,
                       flow_mag=flow, flow_confidence=conf)


def agreeing(t: float) -> PhoneSample:
    """Gentle hand motion where both normalized rates track each other."""
    g = 1.6 + 0.3 * math.sin(2.0 * t)
    f = g * (config.FLOW_NORM / config.GYRO_NORM_RAD_S)  # same normalized value
    return sample(t, g, f)


def run(mon: Monitor, t0: float, seconds: float, make):
    frames = []
    n = int(seconds * HZ)
    for i in range(n):
        t = t0 + i * DT
        frames.append(mon.ingest(make(t)))
    return frames, t0 + n * DT


def test_agreement_stays_normal_and_never_wakes():
    mon = Monitor()
    frames, _ = run(mon, 0.0, 4.0, agreeing)
    assert all(f.state == "NORMAL" for f in frames)
    assert mon.gemma_call_count == 0
    assert all(f.wake_evidence is None for f in frames)


def test_persistent_divergence_reaches_active_and_wakes_exactly_once():
    mon = Monitor()
    _, t = run(mon, 0.0, 3.0, agreeing)
    railed = lambda tt: sample(tt, config.GYRO_RAIL_VALUE, agreeing(tt).flow_mag)
    frames, t = run(mon, t, 3.0, railed)

    states = [f.state for f in frames]
    assert "CANDIDATE" in states and "ACTIVE" in states
    wakes = [f for f in frames if f.wake_evidence is not None]
    assert len(wakes) == 1, "Gemma must be woken exactly once per conflict"
    assert mon.gemma_call_count == 1
    assert mon.conflict_id == 1

    ev = wakes[0].wake_evidence
    assert ev.conflict_id == 1
    assert ev.gyro_rail_score > 0.6
    assert ev.gyro_saturated is True
    assert ev.camera_status == "healthy"
    assert ev.seconds_diverged >= config.CANDIDATE_PERSISTENCE_S * 0.9
    assert len(ev.gyro_trend) == config.TREND_POINTS
    # divergence persisting after ACTIVE must not re-wake
    frames2, _ = run(mon, t, 2.0, railed)
    assert all(f.wake_evidence is None for f in frames2)
    assert mon.gemma_call_count == 1


def test_short_blip_recovers_before_persistence_no_wake():
    mon = Monitor()
    _, t = run(mon, 0.0, 3.0, agreeing)
    blip_end = t + config.CANDIDATE_PERSISTENCE_S * 0.5
    def blip(tt):
        base = agreeing(tt)
        if tt < blip_end:
            base.gyro_mag *= 3.0
        return base
    frames, _ = run(mon, t, 3.0, blip)
    states = [f.state for f in frames]
    assert "CANDIDATE" in states, "blip should arm a candidate conflict"
    assert "ACTIVE" not in states, "sub-threshold blip must never go ACTIVE"
    assert mon.gemma_call_count == 0
    assert mon.conflict_id == 0
    assert frames[-1].state == "NORMAL"


def test_full_lifecycle_recovery_and_cooldown_then_second_conflict():
    mon = Monitor()
    _, t = run(mon, 0.0, 3.0, agreeing)
    railed = lambda tt: sample(tt, config.GYRO_RAIL_VALUE, agreeing(tt).flow_mag)
    _, t = run(mon, t, 2.0, railed)
    assert mon.state.value == "ACTIVE"

    # recovery: agreement must hold RECOVERY_AGREEMENT_S before NORMAL
    frames, t = run(mon, t, config.RECOVERY_AGREEMENT_S + 1.0, agreeing)
    states = [f.state for f in frames]
    assert "RECOVERING" in states
    assert frames[-1].state == "NORMAL"

    seq = [(tr.from_state, tr.to_state) for tr in mon.transitions]
    assert ("NORMAL", "CANDIDATE") in seq
    assert ("CANDIDATE", "ACTIVE") in seq
    assert ("ACTIVE", "RECOVERING") in seq
    assert ("RECOVERING", "NORMAL") in seq

    # cooldown: an immediate new divergence may not arm a candidate
    frames, t = run(mon, t, config.CONFLICT_COOLDOWN_S * 0.5, railed)
    assert all(f.state == "NORMAL" for f in frames)
    assert mon.conflict_id == 1

    # after cooldown expires the same divergence arms a NEW conflict
    frames, _ = run(mon, t, config.CONFLICT_COOLDOWN_S, railed)
    wakes = [f for f in frames if f.wake_evidence is not None]
    assert len(wakes) == 1
    assert mon.conflict_id == 2
    assert mon.gemma_call_count == 2
    assert wakes[0].wake_evidence.conflict_id == 2


def test_camera_quality_collapse_is_a_conflict():
    mon = Monitor()
    _, t = run(mon, 0.0, 3.0, agreeing)
    def dark(tt):
        base = agreeing(tt)
        base.flow_mag = 0.02
        base.flow_confidence = 0.03
        return base
    frames, _ = run(mon, t, 3.0, dark)
    wakes = [f for f in frames if f.wake_evidence is not None]
    assert len(wakes) == 1
    ev = wakes[0].wake_evidence
    assert ev.camera_status == "unavailable"
    assert ev.gyro_status == "reporting"
    assert ev.flow_quality < config.FLOW_CONFIDENCE_FLOOR + 0.2
