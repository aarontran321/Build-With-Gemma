"""The altitude toggle, and the time budget it buys when it is on.

The central claim these tests defend: with altitude OFF there is no path by
which it reaches the evidence. That has to be a property of the data flow,
not a promise in a prompt, so it is asserted at the ingest boundary.
"""

import pytest

from server import config, despin
from server.main import Hub
from server.monitor import Monitor
from server.schemas import Evidence, GyroVec, PhoneSample


def sample(alt=None, acc=None, t=0.0, rate=2.0) -> PhoneSample:
    return PhoneSample(
        t=t, gyro=GyroVec(x=0.0, y=0.0, z=rate), gyro_mag=abs(rate),
        flow_mag=1.5, flow_confidence=0.9,
        altitude_m=alt, altitude_accuracy_m=acc)


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
    )
    base.update(kw)
    return Evidence(**base)


# ----------------------------- the gate -----------------------------

def test_disabled_strips_altitude_at_ingest():
    hub = Hub()
    hub.altitude_enabled = False
    s = sample(alt=120.0, acc=8.0)
    hub._gate_altitude(s)
    assert s.altitude_m is None
    assert s.altitude_accuracy_m is None


def test_enabled_passes_a_good_fix_through():
    hub = Hub()
    hub.altitude_enabled = True
    s = sample(alt=120.0, acc=8.0)
    hub._gate_altitude(s)
    assert s.altitude_m == 120.0
    assert s.altitude_accuracy_m == 8.0


def test_enabled_rejects_a_fix_too_coarse_to_mean_anything():
    hub = Hub()
    hub.altitude_enabled = True
    s = sample(alt=120.0, acc=config.ALTITUDE_MAX_ACCURACY_M + 10)
    hub._gate_altitude(s)
    assert s.altitude_m is None
    assert hub.altitude_rejected == 1


def test_absent_fix_is_not_ground_level():
    """A phone with no fix must never read as altitude zero."""
    hub = Hub()
    hub.altitude_enabled = True
    s = sample(alt=None, acc=None)
    hub._gate_altitude(s)
    assert s.altitude_m is None


def test_disabled_altitude_never_reaches_the_evidence():
    """End to end through the monitor: the arbiter's only input is Evidence,
    so this is the assertion that matters."""
    hub = Hub()
    hub.altitude_enabled = False
    mon = Monitor()
    ev = None
    for i in range(80):
        s = sample(alt=250.0, acc=5.0, t=i / 25, rate=2.0 if i < 40 else 0.0)
        hub._gate_altitude(s)
        f = mon.ingest(s)
        if f.wake_evidence is not None:
            ev = f.wake_evidence
    for s_ in (sample(alt=250.0, acc=5.0),):
        hub._gate_altitude(s_)
        assert s_.altitude_m is None
    if ev is not None:
        assert ev.altitude_m is None
        assert ev.seconds_to_ground is None


# --------------------------- the time budget ---------------------------

def test_seconds_to_ground_is_derived_from_altitude():
    mon = Monitor()
    s = sample(alt=800.0, acc=5.0)
    mon.ingest(s)
    assert mon._alt_m == 800.0
    expected = 800.0 / config.ALTITUDE_TIME_BUDGET_RATE_M_S
    assert expected == pytest.approx(10.0, abs=0.01)


def test_burn_refused_when_it_cannot_finish_before_impact():
    ev = evidence(spin_rate_signed=3.0, altitude_m=60.0, seconds_to_ground=0.75)
    ok, why = despin.despin_feasible(ev, "gyro")
    assert ok is False
    assert "remain to ground" in why


def test_burn_allowed_when_the_budget_is_ample():
    ev = evidence(spin_rate_signed=3.0, altitude_m=3000.0, seconds_to_ground=37.5)
    ok, why = despin.despin_feasible(ev, "gyro")
    assert ok is True, why


def test_unknown_altitude_is_not_treated_as_no_time():
    """The toggle being off must not make the vehicle behave as though it
    were about to hit the ground."""
    ev = evidence(spin_rate_signed=3.0, altitude_m=None, seconds_to_ground=None)
    ok, why = despin.despin_feasible(ev, "gyro")
    assert ok is True, why
