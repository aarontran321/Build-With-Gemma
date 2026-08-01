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

    # --- optional third sensor: GPS altitude ---
    # Present only when the operator has switched altitude on AND the phone
    # has a fix. NOT the barometer: iOS exposes the barometric altimeter to
    # native CoreMotion only, never to a web page, so this is GPS-derived and
    # typically +/-10-30 m, frequently null indoors. Both fields are None
    # whenever there is nothing trustworthy to report — an absent fix must
    # never be confused with an altitude of zero.
    altitude_m: Optional[float] = None
    altitude_accuracy_m: Optional[float] = None

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

    # --- axis-resolved spin (gyro only; see note) ---
    # The camera proxy is a residual-magnitude estimate: it has no sign and no
    # axis. So these fields come from the GYRO alone, and if the gyro is the
    # sensor being distrusted they are not a usable basis for a manoeuvre.
    # That asymmetry is the point: trusting the camera preserves rate
    # DETECTION but forfeits attitude-control authority.
    spin_axis: Literal["x", "y", "z"] = "z"
    spin_rate_signed: float = 0.0      # rad/s; sign = direction of rotation
    spin_axis_stability: float = Field(default=0.0, ge=0.0, le=1.0)
    gyro_axis_rates: dict = Field(default_factory=dict)  # signed mean per axis

    # --- optional altitude context ---
    # None unless the operator enabled altitude AND the fix was good enough
    # (see main.Hub._gate_altitude). When present it buys the arbiter a
    # time-to-ground budget: a manoeuvre that cannot complete before impact
    # is not worth starting. Absent is the normal case and must read as
    # "unknown", never as "at ground level".
    altitude_m: Optional[float] = None
    altitude_accuracy_m: Optional[float] = None
    seconds_to_ground: Optional[float] = None


Manoeuvre = Literal["despin", "hold_attitude", "reduce_rate_partial"]


class ControlAction(BaseModel):
    """A concrete attitude-control command — the half of the response that
    picking a sensor does not cover.

    The vehicle does not need to know which sensor is faulty; it needs to
    stop tumbling. `fire_direction` opposes the measured spin. Magnitudes
    here are always the deterministic ones from server/despin.py, never a
    number the model produced (see that module's docstring for the split).

    `feasible=False` is a first-class outcome, not an error: a verdict that
    trusts the camera is a valid diagnosis that still cannot command a
    manoeuvre, because the camera proxy has no axis or sign.
    """

    manoeuvre: Manoeuvre
    axis: Literal["x", "y", "z"]
    fire_direction: Literal["positive", "negative", "none"]
    burn_s: float = Field(ge=0.0)
    thrust_fraction: float = Field(ge=0.0, le=1.0)
    measured_rate_rad_s: float
    rationale: str
    feasible: bool = True


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

    # --- attitude-control INTENT (judgement only, never arithmetic) ---
    # The model says whether the vehicle should try to null its spin and
    # about which axis. It is deliberately NOT asked for burn time or thrust:
    # those follow from rigid-body mechanics and are computed in despin.py,
    # so a plausible-sounding wrong number can never reach a thruster.
    proposed_manoeuvre: Manoeuvre = "hold_attitude"
    manoeuvre_axis: Literal["x", "y", "z"] = "z"


class FinalDecision(BaseModel):
    """The validated flight decision shown on the dashboard.

    `control_action` is what the vehicle will actually execute: the
    deterministic manoeuvre from despin.py after the guardrail has checked it
    against the model's intent. When the two differ, `control_overridden`
    says so and the dashboard shows proposed beside executed.
    """

    conflict_id: int
    verdict: Verdict
    source: DecisionSource
    control_action: Optional[ControlAction] = None
    control_overridden: bool = False
    control_override_reason: Optional[str] = None
    guardrail_overrode: bool = False
    override_reason: Optional[str] = None
    arbitration_latency_s: float = 0.0


class Transition(BaseModel):
    """One conflict-state-machine transition, recorded in the session log."""

    t: float
    from_state: str
    to_state: str
    conflict_id: Optional[int] = None
