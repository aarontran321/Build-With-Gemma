"""Classical monitor: DETECT ONLY.

Runs at frame rate on every sample and answers exactly four questions:
are the two independent streams behaving consistently; has disagreement
persisted long enough to be a conflict; what compact evidence should be
sent to Gemma; has this conflict already awakened Gemma.

It computes saturation/flatline indicators, variances, camera quality,
trend correlation, normalized divergence, and agreement history — but it
must NOT make the final fault attribution or sensor-selection decision.
That is Gemma's job (server/arbiter.py). The monitor detects and
characterizes; the arbiter diagnoses; the guardrail validates.

Conflict lifecycle state machine (hard constraint 2):

    NORMAL -> CANDIDATE -> ACTIVE -> RECOVERING -> NORMAL

* enter CANDIDATE when normalized divergence exceeds the threshold
* return to NORMAL if the signal recovers before the persistence threshold
* enter ACTIVE only after divergence persists continuously long enough
* assign a monotonically increasing conflict_id
* wake Gemma EXACTLY ONCE, on the transition into ACTIVE
* never wake Gemma again while the same conflict is active
* enter RECOVERING only after evidence returns toward agreement
* return to NORMAL only after agreement holds for the recovery duration
* apply a cooldown before another conflict may arm
"""

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

import numpy as np

from . import config
from .schemas import Evidence, PhoneSample, Transition


class ConflictState(str, Enum):
    NORMAL = "NORMAL"
    CANDIDATE = "CANDIDATE"
    ACTIVE = "ACTIVE"
    RECOVERING = "RECOVERING"


@dataclass
class MonitorFrame:
    """Everything downstream consumers (dashboard, descent sim, recorder)
    need about one processed sample."""

    t: float
    gyro_mag: float
    flow_mag: float
    flow_confidence: float
    gyro_norm: float
    flow_norm: float
    divergence: float
    agreement: float
    gyro_rail_score: float
    gyro_flatline_score: float
    flow_quality: float
    camera_status: str
    gyro_status: str
    state: str
    conflict_id: Optional[int]
    wake_evidence: Optional[Evidence] = None      # set on NORMAL->...->ACTIVE
    transitions: List[Transition] = field(default_factory=list)


class Monitor:
    def __init__(self) -> None:
        window = config.EVIDENCE_WINDOW_S
        # ~25 Hz sampling; deques sized generously for the evidence window.
        self._maxlen = max(8, int(window * 40))
        self._t: deque = deque(maxlen=self._maxlen)
        self._gyro: deque = deque(maxlen=self._maxlen)
        self._flow: deque = deque(maxlen=self._maxlen)
        self._conf: deque = deque(maxlen=self._maxlen)

        # Adaptive flow->gyro scale, learned only while the streams agree and
        # both show motion, so the shape/trend comparison isn't hostage to a
        # bad fixed guess. Starts at 1.0 (i.e. config norms only).
        self._flow_gain = 1.0

        self.state = ConflictState.NORMAL
        self.conflict_id = 0
        self.gemma_call_count = 0
        self._candidate_since: Optional[float] = None
        self._diverged_since: Optional[float] = None
        self._recover_since: Optional[float] = None
        self._cooldown_until: float = 0.0
        self._woken_for_current: bool = False

        self._agreement_hist: deque = deque(maxlen=config.AGREEMENT_HISTORY_POINTS)
        self._last_agreement_sample_t: float = -1e9
        self.transitions: List[Transition] = []

    # ------------------------------------------------------------------
    # feature computation
    # ------------------------------------------------------------------

    # Health scores use only the RECENT slice of the window (last ~0.4 s):
    # scored over the full evidence window they would be diluted by healthy
    # pre-fault/ramp samples and lag the fault by the window length. 0.4 s
    # is long enough to be robust to single-sample glitches and short
    # enough that a pinned gyro scores ~1.0 by the time Gemma is woken.
    RECENT = 10  # samples at ~25 Hz

    def _rail_score(self, g: np.ndarray) -> float:
        """Fraction of recent samples pinned at the known rail magnitude."""
        if len(g) == 0:
            return 0.0
        r = g[-self.RECENT:]
        near = np.abs(r - config.GYRO_RAIL_VALUE) < config.RAIL_TOLERANCE * config.GYRO_RAIL_VALUE
        return float(np.mean(near))

    def _flatline_score(self, g: np.ndarray) -> float:
        """1.0 when the stream is frozen at a dead constant, ~0 when it varies.

        Requires a clearly nonzero mean: a still phone's quiet gyro is
        healthy, not flatlined. (A gyro dead at exactly zero still surfaces
        via divergence + variance evidence; it just isn't scored here.)
        """
        r = g[-self.RECENT:]
        if len(r) < 4 or float(np.mean(r)) < 0.3:
            return 0.0
        return float(np.clip(1.0 - np.std(r) / config.FLATLINE_STD, 0.0, 1.0))

    @staticmethod
    def _trend(values: np.ndarray, points: int) -> List[float]:
        """Downsample the window into `points` bin means (temporal trend)."""
        if len(values) == 0:
            return [0.0] * points
        bins = np.array_split(values, points)
        return [round(float(np.mean(b)), 3) if len(b) else 0.0 for b in bins]

    @staticmethod
    def _correlation(a: List[float], b: List[float]) -> float:
        aa, bb = np.asarray(a), np.asarray(b)
        if np.std(aa) < 1e-9 or np.std(bb) < 1e-9:
            return 0.0  # a constant stream correlates with nothing
        return float(np.clip(np.corrcoef(aa, bb)[0, 1], -1.0, 1.0))

    def _divergence(self, gn: float, fn: float, flow_quality: float) -> float:
        """Normalized divergence in [0, 1].

        A camera that cannot see (quality below the floor) cannot corroborate
        anything, which is itself a conflict-worthy condition, so quality
        collapse forces full divergence. Otherwise compare normalized rates:
        shape/trend, not units (the proxy is uncalibrated).
        """
        if flow_quality < config.FLOW_CONFIDENCE_FLOOR:
            return 1.0
        if gn < config.MOTION_FLOOR and fn < config.MOTION_FLOOR:
            return 0.0  # both quiet: nothing to disagree about
        return float(np.clip(abs(gn - fn) / max(gn, fn, 0.25), 0.0, 1.0))

    # ------------------------------------------------------------------
    # state machine
    # ------------------------------------------------------------------

    def _transition(self, t: float, to: ConflictState) -> Transition:
        tr = Transition(
            t=t,
            from_state=self.state.value,
            to_state=to.value,
            conflict_id=self.conflict_id or None,
        )
        self.state = to
        self.transitions.append(tr)
        return tr

    def ingest(self, s: PhoneSample) -> MonitorFrame:
        t = s.t
        self._t.append(t)
        self._gyro.append(abs(s.gyro_mag))
        self._flow.append(abs(s.flow_mag))
        self._conf.append(s.flow_confidence)

        # trim to the evidence window by time (deque maxlen is just a bound)
        while len(self._t) > 4 and t - self._t[0] > config.EVIDENCE_WINDOW_S:
            self._t.popleft(); self._gyro.popleft(); self._flow.popleft(); self._conf.popleft()

        g = np.asarray(self._gyro)
        f = np.asarray(self._flow)

        gyro_rate = float(np.mean(g[-5:]))
        flow_rate = float(np.mean(f[-5:]))
        # quality is also recency-weighted: a lens covered NOW matters now
        flow_quality = float(np.mean(np.asarray(self._conf)[-5:]))
        rail = self._rail_score(g)
        flat = self._flatline_score(g)

        gn = gyro_rate / config.GYRO_NORM_RAD_S
        fn = flow_rate / config.FLOW_NORM * self._flow_gain

        div = self._divergence(gn, fn, flow_quality)
        agreement = 1.0 - div

        # statuses (detection labels only — NOT a diagnosis)
        if rail > 0.6:
            gyro_status = "railed"
        elif flat > 0.8:
            gyro_status = "flatlined"
        else:
            gyro_status = "reporting"
        if flow_quality >= config.FLOW_QUALITY_HEALTHY:
            camera_status = "healthy"
        elif flow_quality >= config.FLOW_CONFIDENCE_FLOOR:
            camera_status = "degraded"
        else:
            camera_status = "unavailable"

        # learn the flow scale only in NORMAL agreement with real motion
        if (self.state == ConflictState.NORMAL and div < 0.3
                and gn > config.MOTION_FLOOR and fn > config.MOTION_FLOOR):
            ratio = (gyro_rate / config.GYRO_NORM_RAD_S) / max(flow_rate / config.FLOW_NORM, 1e-6)
            ratio = float(np.clip(ratio, 0.3, 3.0))
            self._flow_gain += config.FLOW_GAIN_EMA_ALPHA * (ratio - self._flow_gain)

        # agreement history, sampled every AGREEMENT_SAMPLE_PERIOD_S
        if t - self._last_agreement_sample_t >= config.AGREEMENT_SAMPLE_PERIOD_S:
            self._agreement_hist.append(round(agreement, 2))
            self._last_agreement_sample_t = t

        # track continuous divergence duration
        if div > config.DIVERGENCE_THRESHOLD:
            if self._diverged_since is None:
                self._diverged_since = t
        else:
            if self.state not in (ConflictState.ACTIVE, ConflictState.RECOVERING):
                self._diverged_since = None

        frame = MonitorFrame(
            t=t, gyro_mag=s.gyro_mag, flow_mag=s.flow_mag,
            flow_confidence=s.flow_confidence, gyro_norm=gn, flow_norm=fn,
            divergence=div, agreement=agreement, gyro_rail_score=rail,
            gyro_flatline_score=flat, flow_quality=flow_quality,
            camera_status=camera_status, gyro_status=gyro_status,
            state=self.state.value, conflict_id=self.conflict_id or None,
        )

        # ----- state machine -----
        if self.state == ConflictState.NORMAL:
            if div > config.DIVERGENCE_THRESHOLD and t >= self._cooldown_until:
                self._candidate_since = t
                frame.transitions.append(self._transition(t, ConflictState.CANDIDATE))

        elif self.state == ConflictState.CANDIDATE:
            if div <= config.DIVERGENCE_THRESHOLD:
                # recovered before persistence: a transient the system ignores
                frame.transitions.append(self._transition(t, ConflictState.NORMAL))
                self._candidate_since = None
            elif t - self._candidate_since >= config.CANDIDATE_PERSISTENCE_S:
                self.conflict_id += 1
                self._woken_for_current = False
                frame.transitions.append(self._transition(t, ConflictState.ACTIVE))
                # Wake Gemma EXACTLY ONCE per conflict, here and only here.
                if not self._woken_for_current:
                    self._woken_for_current = True
                    self.gemma_call_count += 1
                    frame.wake_evidence = self._build_evidence(
                        t, g, f, gyro_rate, flow_rate, flow_quality,
                        rail, flat, gn, fn, div, camera_status, gyro_status)

        elif self.state == ConflictState.ACTIVE:
            if div < config.DIVERGENCE_RECOVERY:
                self._recover_since = t
                frame.transitions.append(self._transition(t, ConflictState.RECOVERING))

        elif self.state == ConflictState.RECOVERING:
            if div > config.DIVERGENCE_THRESHOLD:
                # relapse: same conflict resumes; Gemma is NOT woken again
                frame.transitions.append(self._transition(t, ConflictState.ACTIVE))
            elif t - self._recover_since >= config.RECOVERY_AGREEMENT_S:
                frame.transitions.append(self._transition(t, ConflictState.NORMAL))
                self._cooldown_until = t + config.CONFLICT_COOLDOWN_S
                self._diverged_since = None
                self._candidate_since = None

        frame.state = self.state.value
        frame.conflict_id = self.conflict_id or None
        return frame

    # ------------------------------------------------------------------

    def _build_evidence(self, t, g, f, gyro_rate, flow_rate, flow_quality,
                        rail, flat, gn, fn, div, camera_status, gyro_status) -> Evidence:
        """Compact evidence for the arbiter — trends, not one flag."""
        gyro_trend = self._trend(g, config.TREND_POINTS)
        flow_trend = self._trend(f, config.TREND_POINTS)
        seconds_diverged = round(t - self._diverged_since, 2) if self._diverged_since else 0.0
        return Evidence(
            conflict_id=self.conflict_id,
            gyro_rate=round(gyro_rate, 3),
            gyro_saturated=rail > 0.6,
            gyro_rail_score=round(rail, 2),
            gyro_flatline_score=round(flat, 2),
            gyro_variance=round(float(np.var(g)), 4),
            gyro_trend=gyro_trend,
            flow_rate=round(flow_rate, 3),
            flow_quality=round(flow_quality, 2),
            flow_variance=round(float(np.var(f)), 4),
            flow_trend=flow_trend,
            normalized_rate_difference=round(div, 2),
            trend_correlation=round(self._correlation(gyro_trend, flow_trend), 2),
            seconds_diverged=seconds_diverged,
            recent_agreement=list(self._agreement_hist),
            camera_status=camera_status,
            gyro_status=gyro_status,
        )
