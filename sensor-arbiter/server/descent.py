"""Dual descent simulation: NAIVE vs GUARDED, on the same sensor input.

This is an ACCELERATED SIMULATED DESCENT CONSEQUENCE layered on real or
replayed sensor data. The phone is not physically descending and this is
not a flight-accurate Schiaparelli simulator (the dashboard shows this
disclaimer verbatim). Gains and rates are demo-scale; the mechanism is the
point, and the mechanism echoes the real loss: corrupted rotational-rate
data => erroneous attitude => the altitude ESTIMATE dives negative =>
premature parachute cut => freefall impact.

Two hypothetical vehicles fly the same descent from 3700 m on identical
sensor input:

* NAIVE integrates attitude from whatever stream the fault corrupted (no
  arbitration). Its estimate error grows while corruption persists and its
  computed altitude heads sharply toward and below zero, triggering a
  simulated premature chute cut and a CRASH. If measurements re-agree
  (transient case) the naive filter re-converges — persistent corruption
  is what kills it, and the transient golden run honestly shows both
  vehicles landing.

* GUARDED follows the validated diagnosis. While a conflict is detected
  but not yet arbitrated it applies a conservative hold (freezes attitude
  updates from the disputed axis; small bounded drift), so arbitration
  latency is visible but survivable. After the decision it flies the
  trusted sensor, flares, and lands SAFE. A trust-neither verdict puts it
  in a slower CAUTION descent.
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
    phase: str = "CHUTE"                         # CHUTE|FREEFALL|LANDED
    outcome: str = "DESCENDING"                  # DESCENDING|SAFE|CRASH
    impact_speed: float = 0.0
    events: list = field(default_factory=list)   # (t, label) story beats


class DualDescent:
    def __init__(self) -> None:
        self.naive = PathState()
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

    @staticmethod
    def _naive_error_rate(s: PhoneSample) -> float:
        """Normalized 'how wrong is the stream the naive filter trusts'.

        Uses the injector-recorded clean values, so the error is the honest
        difference between what the corrupted stream reports and what the
        sensor actually measured — not an invented number.
        """
        if not s.injected:
            return 0.0
        if s.injected.startswith("gyro") or s.injected == "transient":
            clean = s.clean_gyro_mag if s.clean_gyro_mag is not None else s.gyro_mag
            return abs(s.gyro_mag - clean) / config.GYRO_NORM_RAD_S
        # camera fault: the naive filter is fusing a blind sensor — it both
        # reads garbage and cannot observe the real motion.
        clean = s.clean_flow_mag if s.clean_flow_mag is not None else s.flow_mag
        unobservable = (1.0 - s.flow_confidence) * config.CAMERA_UNOBSERVABLE_PENALTY
        return abs(s.flow_mag - clean) / config.FLOW_NORM + unobservable

    def _step_naive(self, s: PhoneSample, dt: float, t: float) -> None:
        p = self.naive
        if p.phase == "LANDED":
            return
        err = self._naive_error_rate(s)
        if err > 0.0:
            # soft-saturating growth of the altitude-estimate error
            p.err_int += config.NAIVE_ERROR_GAIN * (err / (err + config.NAIVE_ERROR_HALF)) * dt
        else:
            # measurements agree again: the filter re-converges
            p.err_int *= max(0.0, 1.0 - dt / config.NAIVE_RECONVERGE_TAU_S)

        if p.phase == "CHUTE":
            p.est_alt = p.alt - p.err_int
            if p.est_alt <= 0.0 and p.alt > 0.0:
                # the Schiaparelli signature: negative altitude estimate =>
                # premature chute cut + too-brief retro burn
                p.phase = "FREEFALL"
                p.events.append((t, f"premature_chute_cut_at_{p.alt:.0f}m"))
            elif p.est_alt < config.DESCENT_FLARE_ALT_M:
                p.rate = config.DESCENT_FLARE_RATE_M_S
        if p.phase == "FREEFALL":
            p.rate = min(p.rate + config.FREEFALL_ACCEL_M_S2 * dt,
                         config.FREEFALL_TERMINAL_M_S)
            p.est_alt = p.alt - p.err_int

        p.alt -= p.rate * dt
        if p.alt <= 0.0:
            p.alt = 0.0
            p.phase = "LANDED"
            p.impact_speed = p.rate
            p.outcome = "SAFE" if p.rate <= config.DESCENT_SAFE_IMPACT_M_S else "CRASH"
            p.events.append((t, f"impact_{p.rate:.0f}m_s_{p.outcome}"))

    def _step_guarded(self, s: PhoneSample, frame: MonitorFrame, dt: float, t: float) -> None:
        p = self.guarded
        if p.phase == "LANDED":
            return
        unresolved = frame.state in ("CANDIDATE", "ACTIVE") and self._decision is None
        if unresolved:
            # conservative hold pending arbitration: bounded drift only
            p.err_int += config.GUARDED_HOLD_DRIFT_M_S * dt
        else:
            p.err_int *= max(0.0, 1.0 - dt / config.NAIVE_RECONVERGE_TAU_S)
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
        self._step_naive(s, dt, s.t)
        self._step_guarded(s, frame, dt, s.t)
        return self.snapshot()

    def snapshot(self) -> dict:
        def pack(p: PathState) -> dict:
            return {
                "alt": round(p.alt, 1),
                "est_alt": round(p.est_alt, 1),
                "rate": round(p.rate, 1),
                "phase": p.phase,
                "outcome": p.outcome,
                "impact_speed": round(p.impact_speed, 1),
            }
        return {"naive": pack(self.naive), "guarded": pack(self.guarded)}
