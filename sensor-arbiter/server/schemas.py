"""Pydantic models for every message that crosses a boundary.

Boundaries: phone -> server (PhoneSample), monitor -> Gemma (Evidence),
Gemma/fallback -> guardrail (Verdict), guardrail -> dashboard (FinalDecision).
Field names follow the data contracts in the build spec exactly.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

FaultClass = Literal[
    "gyro_saturation",
    "gyro_flatline",
    "camera_degradation",
    "camera_obstruction_or_darkness",
    "transient_disagreement",
    "dual_sensor_degradation",
    "unknown",
]

SensorName = Literal["gyro", "camera", "none", "both"]

DecisionName = Literal[
    "continue_with_gyro",
    "switch_to_camera",
    "trust_neither_enter_caution",
    "observe_transient_conflict",
    "request_redundant_measurement",
]

DecisionSource = Literal["gemma", "fallback", "guardrail_override"]


class GyroVec(BaseModel):
    x: float
    y: float
    z: float


class PhoneSample(BaseModel):
    """One sensor sample from the phone (or a golden-run replay line).

    gyro_mag is calibrated rad/s from the IMU. flow_mag is the camera-derived
    rotational-motion proxy, computed from pixels only, in uncalibrated units.
    flow_confidence is the fraction of tracking blocks that survived rejection.
    raw_saturated is the phone's own rail hint and may be false the whole time,
    which is why synthetic injection exists.
    """

    t: float
    gyro: GyroVec
    gyro_mag: float
    flow_mag: float
    flow_confidence: float = Field(ge=0.0, le=1.0)
    raw_saturated: bool = False

    # --- server-side injection metadata (never produced by the phone) ---
    # When a synthetic fault overwrites a stream, the injector records the
    # pre-injection values here so the descent sim can honestly model the
    # difference between the corrupted stream and reality, and so replays and
    # the dashboard can label what is real versus injected.
    injected: Optional[str] = None
    clean_gyro_mag: Optional[float] = None
    clean_flow_mag: Optional[float] = None
    clean_flow_confidence: Optional[float] = None


class Evidence(BaseModel):
    """Compact temporal evidence (monitor -> Gemma), last ~1.5 s.

    Includes short trends so Gemma reasons about behavior over time, not a
    single flag. This is the ONLY input the arbiter sees.
    """

    conflict_id: int
    gyro_rate: float
    gyro_saturated: bool
    gyro_rail_score: float = Field(ge=0.0, le=1.0)
    gyro_flatline_score: float = Field(ge=0.0, le=1.0)
    gyro_variance: float
    gyro_trend: List[float]
    flow_rate: float
    flow_quality: float = Field(ge=0.0, le=1.0)
    flow_variance: float
    flow_trend: List[float]
    normalized_rate_difference: float
    trend_correlation: float
    seconds_diverged: float
    recent_agreement: List[float]
    camera_status: Literal["healthy", "degraded", "unavailable"]
    gyro_status: Literal["reporting", "railed", "flatlined"]


class Verdict(BaseModel):
    """Structured diagnosis proposed by Gemma (or the fallback classifier).

    `evidence` holds short diagnostic observations, not chain-of-thought.
    `alternative_hypothesis` states why the best competing explanation is
    less likely. The guardrail validates this before it becomes the flight
    decision (LLM proposes, code disposes).
    """

    fault_class: FaultClass
    faulty_sensor: SensorName
    trusted_sensor: SensorName
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: List[str]
    alternative_hypothesis: str
    recommended_action: str
    decision: DecisionName


class FinalDecision(BaseModel):
    """The validated flight decision shown on the dashboard."""

    conflict_id: int
    verdict: Verdict
    source: DecisionSource
    guardrail_overrode: bool = False
    override_reason: Optional[str] = None
    arbitration_latency_s: float = 0.0


class Transition(BaseModel):
    """One conflict-state-machine transition, recorded in the session log."""

    t: float
    from_state: str
    to_state: str
    conflict_id: Optional[int] = None
