"""Guarded descent simulation.

This is an ACCELERATED SIMULATED DESCENT CONSEQUENCE layered on real or
replayed sensor data. The phone is not physically descending and this is
not a flight-accurate Schiaparelli simulator (the dashboard shows this
disclaimer verbatim). Gains and rates are demo-scale; the mechanism is the
point.

One simulated vehicle flies the descent from 3700 m on the live or
replayed sensor input, protected by the arbitration pipeline:

* While a conflict is detected but not yet arbitrated it applies a
  conservative hold (freezes attitude updates from the disputed axis;
  small bounded drift), so arbitration latency is visible but survivable.
* After the validated decision it flies the trusted sensor, flares below
  the flare altitude, and touches down SAFE.
* A trust-neither verdict puts it in a slower CAUTION descent.

The Schiaparelli failure chain (corrupted rotational rate => negative
altitude estimate => premature parachute cut => freefall impact) is told
in the mission narrative; this module simulates only the protected
vehicle's flight.
"""

from dataclasses import dataclass, field
from typing import Optional

from . import config
from .monitor import MonitorFrame
from .schemas import FinalDecision, PhoneSample


@dataclass
class PathState:
    alt: float = config.DESCENT_START_ALT_M      # simulated true altitude
    est_alt: float = config.DESCENT_START_ALT_M  # controller's belief
    rate: float = config.DESCENT_NOMINAL_RATE_M_S
    err_int: float = 0.0                         # accumulated estimate error, m
    phase: str = "CHUTE"                         # CHUTE|LANDED
    outcome: str = "DESCENDING"                  # DESCENDING|SAFE|CRASH
    impact_speed: float = 0.0
    events: list = field(default_factory=list)   # (t, label) story beats


class DualDescent:
    """Kept under its historical name so imports stay stable; it now flies
    a single guarded vehicle."""

    def __init__(self) -> None:
        self.guarded = PathState()
        self._prev_t: Optional[float] = None
        self._decision: Optional[FinalDecision] = None

    def apply_decision(self, d: FinalDecision, t: float) -> None:
        self._decision = d
        self.guarded.events.append((t, f"decision:{d.verdict.decision}:{d.source}"))
        if d.verdict.decision == "trust_neither_enter_caution":
            self.guarded.rate = config.DESCENT_CAUTION_RATE_M_S

    def reset(self) -> None:
        self.__init__()

    # ------------------------------------------------------------------

    def _step_guarded(self, s: PhoneSample, frame: MonitorFrame, dt: float, t: float) -> None:
        p = self.guarded
        if p.phase == "LANDED":
            return
        unresolved = frame.state in ("CANDIDATE", "ACTIVE") and self._decision is None
        if unresolved:
            # conservative hold pending arbitration: bounded drift only
            p.err_int += config.GUARDED_HOLD_DRIFT_M_S * dt
        else:
            p.err_int *= max(0.0, 1.0 - dt / config.GUARDED_RECONVERGE_TAU_S)
        p.est_alt = p.alt - p.err_int

        in_caution = (self._decision is not None and
                      self._decision.verdict.decision == "trust_neither_enter_caution")
        if p.est_alt < config.DESCENT_FLARE_ALT_M:
            p.rate = config.DESCENT_FLARE_RATE_M_S
        elif in_caution:
            p.rate = config.DESCENT_CAUTION_RATE_M_S
        else:
            p.rate = config.DESCENT_NOMINAL_RATE_M_S

        p.alt -= p.rate * dt
        if p.alt <= 0.0:
            p.alt = 0.0
            p.phase = "LANDED"
            p.impact_speed = p.rate
            p.outcome = "SAFE" if p.rate <= config.DESCENT_SAFE_IMPACT_M_S else "CRASH"
            p.events.append((t, f"touchdown_{p.rate:.0f}m_s_{p.outcome}"))

    # ------------------------------------------------------------------

    def step(self, s: PhoneSample, frame: MonitorFrame) -> dict:
        if self._prev_t is None:
            self._prev_t = s.t
        dt = max(0.0, min(s.t - self._prev_t, 0.2))  # clamp gaps/reorders
        self._prev_t = s.t
        self._step_guarded(s, frame, dt, s.t)
        return self.snapshot()

    def snapshot(self) -> dict:
        p = self.guarded
        return {"guarded": {
            "alt": round(p.alt, 1),
            "est_alt": round(p.est_alt, 1),
            "rate": round(p.rate, 1),
            "phase": p.phase,
            "outcome": p.outcome,
            "impact_speed": round(p.impact_speed, 1),
        }}
