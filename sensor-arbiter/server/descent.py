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
        # Attitude state. body_rate is the SIMULATED vehicle rate: it follows
        # the measured spin until a burn is armed, after which the burn — not
        # the phone — governs it, so the effect of firing is visible.
        self._burn_remaining: float = 0.0
        self._burn_torque: float = 0.0     # N m, signed
        self.body_rate: float = 0.0        # rad/s, signed
        self._despin_done: bool = False
        # Rate the thrusters have removed so far. body_rate is always
        # measured + this, so the readout stays live after a burn instead of
        # freezing at the value the burn happened to end on.
        self._despin_offset: float = 0.0

    def apply_decision(self, d: FinalDecision, t: float) -> None:
        self._decision = d
        self.guarded.events.append((t, f"decision:{d.verdict.decision}:{d.source}"))
        if d.verdict.decision == "trust_neither_enter_caution":
            self.guarded.rate = config.DESCENT_CAUTION_RATE_M_S
        # Arm the validated burn. Only a feasible, non-zero manoeuvre commits
        # propellant; a refused or held action is recorded and changes nothing,
        # which is what "the guardrail vetoed it" has to mean physically.
        a = d.control_action
        if a is not None and a.feasible and a.burn_s > 0.0:
            self._burn_remaining = a.burn_s
            self._burn_torque = (config.THRUSTER_MAX_TORQUE_NM * a.thrust_fraction
                                 * (-1.0 if a.fire_direction == "negative" else 1.0))
            self.guarded.events.append(
                (t, f"despin:{a.axis}:{a.burn_s}s:{a.fire_direction}"))

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

    def _step_attitude(self, frame: MonitorFrame, dt: float, t: float) -> None:
        """Integrate the body rate, firing the armed burn if there is one.

        While no burn is active the simulated vehicle simply reports what the
        phone measures — the phone IS the vehicle. Once a burn is armed the
        thruster takes over: omega' = omega + (tau / I) * dt, and the burn
        stops early at zero crossing so it cannot spin the vehicle back up
        the other way, which is the classic way an open-loop de-spin goes
        wrong.
        """
        measured = frame.spin_rate_signed
        if self._burn_remaining > 0.0:
            step = min(dt, self._burn_remaining)
            self._burn_remaining -= step
            before = measured + self._despin_offset
            self._despin_offset += (
                self._burn_torque / config.SPACECRAFT_INERTIA_KG_M2) * step
            after = measured + self._despin_offset
            if before != 0.0 and (before > 0.0) != (after > 0.0):
                # Nulled. Cut the burn rather than spinning up the other way —
                # the classic way an open-loop de-spin goes wrong.
                self._despin_offset = -measured
                self._burn_remaining = 0.0
            if self._burn_remaining <= 0.0:
                self._despin_done = True
                self.guarded.events.append(
                    (t, f"despin_complete_{measured + self._despin_offset:+.2f}rad_s"))
        # The vehicle rate is always the MEASURED rate plus whatever the
        # thrusters have taken out of it. Holding the post-burn value instead
        # would freeze the readout: spin the phone up again after a de-spin
        # and the panel would keep insisting the rate is zero.
        self.body_rate = measured + self._despin_offset

    def step(self, s: PhoneSample, frame: MonitorFrame) -> dict:
        if self._prev_t is None:
            self._prev_t = s.t
        dt = max(0.0, min(s.t - self._prev_t, 0.2))  # clamp gaps/reorders
        self._prev_t = s.t
        self._step_attitude(frame, dt, s.t)
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
        }, "attitude": self.attitude_snapshot()}

    def attitude_snapshot(self) -> dict:
        """Live attitude state, including the burn STILL required right now.

        `residual_burn_s` is recomputed every frame from the current body
        rate, so it falls to zero as a burn takes effect and the panel can
        say "nulled, no thrust required" instead of leaving the figure that
        was correct at decision time sitting there forever.
        """
        from . import despin
        need = despin.burn_for_rate(self.body_rate)
        return {
            "body_rate": round(self.body_rate, 3),
            "burning": self._burn_remaining > 0.0,
            "burn_remaining_s": round(self._burn_remaining, 2),
            "despin_done": self._despin_done,
            "residual_burn_s": 0.0 if need["settled"] else need["burn_s"],
            "residual_thrust_fraction": 0.0 if need["settled"]
                                        else need["thrust_fraction"],
            "settled": need["settled"],
        }
