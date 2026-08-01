"""Central configuration for the Sensor Arbiter demo.

Every tunable lives here so the team can adjust the demo without hunting
through modules. State-machine defaults are tuned against the committed
golden runs in data/ (see README: live tuning may differ).
"""

import os

# ---------------------------------------------------------------------------
# Gemma / Ollama
# ---------------------------------------------------------------------------
# Tradeoff (deliberate, measured on the demo laptop, 24 GB RAM):
#   gemma4:e4b        ~2-3 s per verdict, 9.6 GB — the reliable demo default
#   gemma4:31b-it-qat strongest reasoning, but 18 GB swaps on this machine
#                     and a verdict can take minutes — only for big-RAM hosts
# Because Gemma is woken only once per conflict (never per frame), a slower
# model never affects telemetry — swap with one line here or
# `GEMMA_MODEL=... uvicorn ...`.
GEMMA_MODEL = os.environ.get("GEMMA_MODEL", "gemma4:e4b")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")

# Hard timeout on the whole Gemma call. On timeout or error the deterministic
# fallback classifier (server/fallback.py) completes the decision so the demo
# never stalls (P0 reliability beats P1 model quality).
GEMMA_TIMEOUT_S = float(os.environ.get("GEMMA_TIMEOUT_S", "15.0"))
GEMMA_RETRIES = 1  # one stricter retry on malformed output, then fallback
# num_predict must comfortably clear the FULL verdict. Measured on gemma4:e2b
# after the manoeuvre fields were added to the schema: at 700 a verdict took
# 82s, at 1100 the same verdict took 19s. Schema-constrained decoding degrades
# sharply as it approaches the token ceiling, so an undersized budget reads as
# "the model is slow" and silently pushes every decision onto the fallback.
# Re-check this whenever the Verdict schema grows.
GEMMA_OPTIONS = {"temperature": 0.1, "num_predict": 1100}
# Fire a tiny request at startup so the (large) model is resident in memory
# before the first real conflict; otherwise first-call load time would eat
# the timeout.
GEMMA_WARMUP = os.environ.get("GEMMA_WARMUP", "1") == "1"
# For tests / offline judging without Ollama: skip the model entirely.
ARBITER_FORCE_FALLBACK = os.environ.get("ARBITER_FORCE_FALLBACK", "0") == "1"

# ---------------------------------------------------------------------------
# Gemma report narrator (server/narrator.py)
# ---------------------------------------------------------------------------
# The SAME local Gemma model writes the prose of each mission report. This is
# a second, non-safety-critical use of the model: it makes no decision, it
# only explains one that is already final and already validated, so it is
# allowed to write freely where the arbiter is not.
# It runs AFTER the decision has been broadcast, never in the decision path,
# so a slow narration can never delay a flight decision or the telemetry.
# CONTENTION WARNING: the narrator uses the SAME Ollama model as the arbiter,
# and Ollama serialises requests per model. A narration still running when the
# next conflict arms puts that arbitration behind it in the queue. Measured on
# gemma4:e2b during a replay: ~38-48s per verdict with the narrator on, and
# timeouts at 45s; the same verdict took ~19s with no other load. If verdicts
# are landing as FALLBACK during a busy demo, set NARRATOR_ENABLED=0 before
# blaming the arbiter, or run a model fast enough to absorb both.
NARRATOR_ENABLED = os.environ.get("NARRATOR_ENABLED", "1") == "1"
# Prose is far longer than a verdict, and this is background work with a
# deterministic text already on screen — so the budget is generous where the
# arbiter's is tight.
NARRATOR_TIMEOUT_S = float(os.environ.get("NARRATOR_TIMEOUT_S", "120.0"))
NARRATOR_RETRIES = 1   # one stricter retry if it invents a figure, then fallback
NARRATOR_OPTIONS = {"temperature": 0.3, "num_predict": 1100}

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
SERVER_PORT = int(os.environ.get("PORT", "8000"))
SESSIONS_DIR = os.environ.get("SESSIONS_DIR", "sessions")
DATA_DIR = "data"

# ---------------------------------------------------------------------------
# Mission log / reports
# ---------------------------------------------------------------------------
# The mission log holds only SIGNIFICANT events (transitions, injections,
# decisions, descent beats) — never the ~25 Hz samples, which stay in the
# JSONL session recording. A long demo therefore fills this slowly; the ring
# is capped so an unattended server cannot grow it without bound, and the
# count of aged-out events is reported in every report header.
MISSION_LOG_MAX_EVENTS = int(os.environ.get("MISSION_LOG_MAX_EVENTS", "2000"))
# How many events a reconnecting dashboard is backfilled with.
MISSION_LOG_REPLAY_ON_CONNECT = 120
# A NORMAL <-> CANDIDATE round trip is the state machine correctly IGNORING a
# sub-threshold blip — no conflict armed, no arbiter call. It is worth showing
# once (it demonstrates the persistence filter working), but a noisy live
# stream can produce several per second and bury the events that matter. Blips
# inside this window are counted and folded into one summary line instead.
# Anything touching ACTIVE/RECOVERING is a real conflict and is never
# rate-limited.
MISSION_LOG_BLIP_QUIET_S = float(os.environ.get("MISSION_LOG_BLIP_QUIET_S", "10.0"))

# ---------------------------------------------------------------------------
# Signal normalization
# ---------------------------------------------------------------------------
# The gyro is calibrated rad/s; the camera proxy is UNCALIBRATED pixel units.
# We normalize both to a comparable scale so the monitor compares SHAPE and
# TREND, never unit-for-unit values (see accuracy rules in WRITEUP.md).
GYRO_NORM_RAD_S = 4.0   # a brisk hand-spin is a few rad/s
FLOW_NORM = 3.0         # typical proxy magnitude for the same motion
# Adaptive gain: while the streams agree and both show motion, we slowly learn
# the actual flow->gyro scale so divergence isn't an artifact of a bad guess.
FLOW_GAIN_EMA_ALPHA = 0.02
# Normalized rate below this counts as "still" (no comparison possible).
# Tuned UP from 0.12 after live-phone audit: the flow proxy's at-rest noise
# is ~0.5-0.7 units (fn ~0.17-0.23), which kept arming phantom conflicts on
# a stationary phone. 0.28 sits above that noise floor and well below real
# hand motion (fn ~0.4+).
MOTION_FLOOR = 0.28

# ---------------------------------------------------------------------------
# Conflict state machine (NORMAL -> CANDIDATE -> ACTIVE -> RECOVERING -> NORMAL)
# ---------------------------------------------------------------------------
DIVERGENCE_THRESHOLD = 0.55    # enter CANDIDATE above this
DIVERGENCE_RECOVERY = 0.35     # ACTIVE -> RECOVERING below this
CANDIDATE_PERSISTENCE_S = 0.8  # continuous divergence required to go ACTIVE
RECOVERY_AGREEMENT_S = 1.2     # continuous agreement required to close out
CONFLICT_COOLDOWN_S = 3.0      # dead time before another conflict may arm
EVIDENCE_WINDOW_S = 1.5        # compact-evidence lookback sent to Gemma
TREND_POINTS = 7               # samples in each trend array
AGREEMENT_HISTORY_POINTS = 6   # recent_agreement length
AGREEMENT_SAMPLE_PERIOD_S = 0.25

# ---------------------------------------------------------------------------
# Sensor health scoring
# ---------------------------------------------------------------------------
GYRO_RAIL_VALUE = 34.0         # rad/s value the synthetic saturation pins to
RAIL_TOLERANCE = 0.06          # fraction of rail value counted as "at rail"
RAIL_SCORE_TRUST_LIMIT = 0.9   # guardrail: never trust a gyro railed harder
FLATLINE_STD = 0.05            # std below which a stream reads "frozen"
FLOW_CONFIDENCE_FLOOR = 0.15   # below this the camera is "unavailable"
FLOW_QUALITY_HEALTHY = 0.5

# ---------------------------------------------------------------------------
# Fault injection (synthetic, the PRIMARY trigger — see hard constraint 3)
# ---------------------------------------------------------------------------
INJECTION_DURATION_S = 10.0        # rail / dark faults auto-recover after this
TRANSIENT_SHORT_BLIP_S = 0.4       # below persistence: state machine ignores it
# The long blip's divergence must stay above threshold for longer than
# CANDIDATE_PERSISTENCE_S (its sinusoidal envelope only exceeds the
# threshold for ~58% of its duration): 2.0 s -> ~1.16 s above threshold.
TRANSIENT_LONG_BLIP_S = 2.0        # just past persistence: wakes Gemma once
TRANSIENT_BLIP_GAP_S = 2.5
TRANSIENT_BLIP_FACTOR = 3.0        # modest multiplier — NOT a rail signature

# ---------------------------------------------------------------------------
# Guarded descent simulation (accelerated, clearly labeled SIMULATED)
# ---------------------------------------------------------------------------
DESCENT_START_ALT_M = 3700.0       # echoes Schiaparelli's ~3.7 km
DESCENT_NOMINAL_RATE_M_S = 80.0    # accelerated demo-scale, not flight-accurate
DESCENT_FLARE_ALT_M = 120.0        # guarded path flares here for touchdown
DESCENT_FLARE_RATE_M_S = 12.0
DESCENT_SAFE_IMPACT_M_S = 15.0     # touchdown at/below this counts SAFE
DESCENT_CAUTION_RATE_M_S = 30.0    # rate while in trust-neither CAUTION mode
GUARDED_RECONVERGE_TAU_S = 3.0     # hold-drift error decays with this tau
GUARDED_HOLD_DRIFT_M_S = 5.0       # small drift during conservative hold

# ---------------------------------------------------------------------------
# Attitude control / de-spin (demo-scale, SIMULATED)
# ---------------------------------------------------------------------------
# Nulling a spin is the reason the vehicle needs a trustworthy RATE at all:
# a fault verdict that only picks a sensor stops short of the actual job.
# Sizing a burn is deterministic physics, so it is computed in code
# (server/despin.py) and used to bound whatever the model proposes.
#
#   angular impulse required   L = I * omega        [kg m^2 / s]
#   burn time at full torque   t = I * omega / tau
#
# Numbers are demo-scale, not flight-accurate: the mechanism is the point.
SPACECRAFT_INERTIA_KG_M2 = 120.0   # about the controlled axis
THRUSTER_MAX_TORQUE_NM = 45.0      # full authority of the RCS pair
# Rates below this are not worth spending propellant on.
DESPIN_DEADBAND_RAD_S = 0.25
# Never command a burn longer than this in one go — a long open-loop burn on a
# possibly-wrong rate estimate is exactly the irreversible action the guardrail
# exists to prevent. Longer corrections happen over repeated arbitrations.
DESPIN_MAX_BURN_S = 8.0
# An axis estimate this unstable is a tumble, not a spin: the dominant-axis
# mean is meaningless and no burn may be commanded from it.
DESPIN_MIN_AXIS_STABILITY = 0.6

# ---------------------------------------------------------------------------
# GPS altitude (optional third sensor, OFF by default)
# ---------------------------------------------------------------------------
# WHY THIS IS A TOGGLE, AND WHY IT DEFAULTS OFF.
#
# The phone's barometric altimeter is NOT reachable from a web page — iOS
# exposes it through native CoreMotion only — so the only altitude available
# here is GPS, via navigator.geolocation. That is +/-10-30 m at best and
# frequently null indoors.
#
# Against a 3700 m descent profile that noise is not a rounding error: a 10 m
# jitter downward reads as terrain and would spuriously "land" the vehicle.
# So altitude NEVER drives the simulated descent altitude. It is an
# OBSERVATION: shown live, and — only when this toggle is on and the fix is
# good enough — added to the evidence Gemma reasons over, where it supplies
# the time-to-ground budget that decides whether a de-spin is affordable.
#
# Off by default so a venue with no sky view degrades to exactly the demo
# that already works, rather than to a confusing one.
ALTITUDE_ENABLED = os.environ.get("ALTITUDE_ENABLED", "0") == "1"
# Fixes worse than this are discarded rather than reasoned over: a 60 m
# uncertainty cannot inform a decision at this scale, and feeding it to the
# model would invite confident nonsense.
ALTITUDE_MAX_ACCURACY_M = float(os.environ.get("ALTITUDE_MAX_ACCURACY_M", "40.0"))
# Descent rate assumed when converting altitude into a time budget. The demo
# vehicle's nominal rate; real telemetry would supply this.
ALTITUDE_TIME_BUDGET_RATE_M_S = DESCENT_NOMINAL_RATE_M_S
