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
GEMMA_OPTIONS = {"temperature": 0.1, "num_predict": 700}
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
# Dual descent simulation (accelerated, clearly labeled SIMULATED)
# ---------------------------------------------------------------------------
DESCENT_START_ALT_M = 3700.0       # echoes Schiaparelli's ~3.7 km
DESCENT_NOMINAL_RATE_M_S = 80.0    # accelerated demo-scale, not flight-accurate
DESCENT_FLARE_ALT_M = 120.0        # guarded path flares here for touchdown
DESCENT_FLARE_RATE_M_S = 12.0
DESCENT_SAFE_IMPACT_M_S = 15.0     # touchdown at/below this counts SAFE
DESCENT_CAUTION_RATE_M_S = 30.0    # rate while in trust-neither CAUTION mode
FREEFALL_TERMINAL_M_S = 160.0      # after premature chute cut (~150 m/s echo)
FREEFALL_ACCEL_M_S2 = 40.0         # accelerated
# Naive-filter estimate-error model (demo-scale gains; the point is the
# mechanism — corrupted rates => negative altitude estimate => premature
# chute cut — not flight-accurate numbers):
NAIVE_ERROR_GAIN = 300.0           # m/s of estimate error at full saturation
NAIVE_ERROR_HALF = 0.3             # soft-saturation knee for the error rate
NAIVE_RECONVERGE_TAU_S = 3.0       # error decays once streams agree again
CAMERA_UNOBSERVABLE_PENALTY = 1.5  # extra error rate when trusting dead camera
GUARDED_HOLD_DRIFT_M_S = 5.0       # small drift during conservative hold
