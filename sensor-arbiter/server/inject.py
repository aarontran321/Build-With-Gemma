"""Synthetic fault injection — the PRIMARY demo trigger (hard constraint 3).

Injection happens server-side, at ingest, BEFORE the monitor sees the
sample: the monitor, arbiter, guardrail, and descent sim cannot tell an
injected fault from a physical one, which is the honest way to do this.
The injector records the pre-injection ("clean") values on the sample so
the dashboard can label what is real versus synthetic and the descent sim
can model the corruption honestly.

All profiles are deterministic (no randomness): pressing the same button
always produces the same fault shape.

Layout note: the build spec routes injection through main.py/config.py; it
lives in this small module so scripts/make_golden.py can reuse the exact
same code path that live injection uses.
"""

import math
from typing import Optional

from . import config
from .schemas import PhoneSample

SCENARIOS = ("gyro_saturation", "camera_dark", "transient")


class FaultInjector:
    def __init__(self) -> None:
        self.scenario: Optional[str] = None
        self.start_t: Optional[float] = None

    def trigger(self, scenario: str, t: float) -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {scenario!r}")
        self.scenario = scenario
        self.start_t = t

    def reset(self) -> None:
        self.scenario = None
        self.start_t = None

    @property
    def active(self) -> bool:
        return self.scenario is not None

    # ------------------------------------------------------------------

    def apply(self, s: PhoneSample) -> PhoneSample:
        """Return the sample, corrupted per the active scenario."""
        if self.scenario is None:
            return s
        e = s.t - self.start_t  # elapsed since injection
        if e < 0:
            return s

        if self.scenario == "gyro_saturation":
            if e >= config.INJECTION_DURATION_S:
                self.reset()  # auto-recover: exercises RECOVERING -> NORMAL
                return s
            s.clean_gyro_mag = s.gyro_mag
            ramp = min(e / 0.5, 1.0)  # half-second ramp up to the rail
            pinned = config.GYRO_RAIL_VALUE + 0.02 * math.sin(s.t * 40.0)
            s.gyro_mag = s.gyro_mag * (1.0 - ramp) + pinned * ramp
            s.gyro.z = s.gyro_mag  # keep the vector story consistent
            s.raw_saturated = ramp >= 1.0
            s.injected = "gyro_saturation"
            return s

        if self.scenario == "camera_dark":
            if e >= config.INJECTION_DURATION_S:
                self.reset()
                return s
            s.clean_flow_mag = s.flow_mag
            s.clean_flow_confidence = s.flow_confidence
            ramp = min(e / 0.3, 1.0)  # lens covered: quality collapses fast
            s.flow_confidence = s.flow_confidence * (1.0 - ramp) + 0.03 * ramp
            s.flow_mag = s.flow_mag * (1.0 - ramp) + 0.02 * ramp
            s.injected = "camera_dark"
            return s

        # transient: two blips on the gyro stream.
        # Blip A is SHORTER than the CANDIDATE persistence threshold — the
        # state machine must ignore it (no Gemma call). Blip B persists just
        # past the threshold, so the arbiter is woken once and should
        # recognize a transient: modest multiplier (no rail signature),
        # healthy camera, agreement history already recovering.
        a0, a1 = 0.0, config.TRANSIENT_SHORT_BLIP_S
        b0 = a1 + config.TRANSIENT_BLIP_GAP_S
        b1 = b0 + config.TRANSIENT_LONG_BLIP_S
        if e >= b1 + 0.1:
            self.reset()
            return s
        blip = None
        if a0 <= e < a1:
            blip = (e - a0) / (a1 - a0)
        elif b0 <= e < b1:
            blip = (e - b0) / (b1 - b0)
        if blip is not None:
            s.clean_gyro_mag = s.gyro_mag
            envelope = math.sin(math.pi * blip)  # smooth up-down, no rail
            factor = 1.0 + (config.TRANSIENT_BLIP_FACTOR - 1.0) * envelope
            s.gyro_mag *= factor
            s.gyro.z = s.gyro_mag
            s.injected = "transient"
        return s
