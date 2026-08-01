# Janus — Sensor Arbiter · Triage In Light Speed (Track 2: Trajectory & Orbit)

**Edge-deployed Gemma as the onboard fault-arbitration engine for a
spacecraft that is minutes from help and must decide alone.**

## The problem, in two sentences

Deep-space vehicles cannot phone home during a descent: light-speed delay
means every sensor fault must be handled onboard, and today's default —
safe mode or a fixed fault table — cannot cover faults nobody enumerated.
Mis-trusting one saturated sensor is fatal: in 2016, ESA's Schiaparelli
lander experienced saturation of one of its gyroscopes at parachute
inflation; the erroneous attitude information produced a **negative
altitude estimate**, a premature parachute release, a too-brief retrorocket
burn, and on-ground systems activating ~3.7 km above Mars — it hit the
surface 33 seconds later at about 150 m/s (Ferri et al., EPSC2017-614).

## What we built

A phone stands in for the spacecraft. It carries two **independent** ways
to observe rotation:

- the built-in **gyroscope** (IMU, calibrated rad/s), and
- a **camera-derived rotational-motion proxy** computed *from pixels only*
  (sparse block-matching optical flow, translation/rotation decomposition,
  self-reported confidence) — it never reads a single IMU value.

A deterministic **monitor** compares the two streams at frame rate. When
they persistently conflict, it wakes a **local Gemma model — exactly once
per conflict** — with ~1.5 s of compact temporal evidence. Gemma diagnoses
the fault class, names the faulty sensor, states which sensor (if any) to
trust and how confident it is, and recommends a safe action, as
schema-constrained JSON. A deterministic **guardrail** validates that
proposal against safety invariants before it becomes the flight decision.
On the same input, the dashboard runs two simulated descents side by side:
a **naive** filter that trusts the corrupted stream, whose altitude
estimate dives negative and triggers a premature chute cut and a crash —
the Schiaparelli signature — and a **guarded** vehicle that follows the
validated diagnosis and lands.

We inject the same *failure mechanism* as Schiaparelli: single-axis
rotational-rate saturation (plus two more fault types; see below). We do
not claim identity with the mission — same mechanism, demo-scale
everything else.

## Why Gemma is central (not a wrapper)

Gemma is the **primary diagnosis engine**, not a summarizer of a decision
deterministic code already made:

- The classical monitor only **detects**: divergence, persistence,
  rail/flatline scores, camera quality, trend correlation. It never
  attributes the fault or selects a sensor in the normal path.
- Gemma reasons over **temporal evidence** (trends, agreement history,
  variance collapse) and distinguishes seven fault classes:
  `gyro_saturation`, `gyro_flatline`, `camera_degradation`,
  `camera_obstruction_or_darkness`, `transient_disagreement`,
  `dual_sensor_degradation`, `unknown`.
- **The proof it does real work: three different verdicts on three
  different injected faults.**

| scenario | Gemma's verdict | action | outcome (naive vs guarded) |
|---|---|---|---|
| gyro pinned at rail, camera healthy | `gyro_saturation`, trust **camera** | `switch_to_camera` | CRASH vs SAFE |
| camera covered, gyro healthy | `camera_obstruction_or_darkness`, trust **gyro** | `continue_with_gyro` | CRASH vs SAFE |
| brief blip, already recovering | `transient_disagreement`, no switch | `observe_transient_conflict` | SAFE vs SAFE |

The camera-dark case shows the model does **not** reflexively trust the
camera; the transient case shows the system does not overreact (a shorter
sub-threshold blip in the same run is ignored by the state machine without
any model call at all).

**On the thesis:** the injected saturation is deliberately a fault a
deterministic monitor *can* detect — it is the legible case that proves the
arbitration loop end to end. The claim is that this architecture (an
independent evaluator woken on conflict, with deterministic guardrails
retaining authority) generalizes to conflicts a fixed lookup table cannot
enumerate, where the model interprets novel combinations of evidence. The
multiple scenarios, and Gemma's freedom to trust either sensor or neither,
are that generalization made visible.

## Architecture and trust boundary

```
monitor (deterministic, frame rate)  →  detects & characterizes conflict
gemma arbiter (edge, on conflict only) →  diagnoses & recommends
guardrail (deterministic, thin)      →  validates safety invariants only
fallback (deterministic, minimal)    →  completes the decision if the model
                                        times out — demo can never stall
dual descent sim                     →  shows the consequence, honestly labeled
```

Key properties, enforced in code and tested:

- **Independence:** the flow estimator is pixels-only by construction
  (commented invariant in `phone/capture.js`); streams first meet on the
  server's comparison plot.
- **Conflict lifecycle:** NORMAL → CANDIDATE → ACTIVE → RECOVERING →
  NORMAL, with persistence, recovery-hold, and cooldown; monotonic
  `conflict_id`; **one Gemma call per conflict**, logged with latency.
- **LLM proposes, code disposes:** the guardrail never re-classifies; it
  enforces invariants like *never trust a railed gyro*, *never trust
  zero-quality flow*, *nothing irreversible when both sensors are bad*,
  and visibly overrides violations with a reason.
- **Structured output:** the verdict is schema-constrained JSON (Ollama
  `format` = pydantic schema), validated, with one strict retry, a hard
  timeout, and a labeled deterministic fallback.
- **Every decision carries its source label:** GEMMA / FALLBACK /
  GUARDRAIL OVERRIDE.

## Honesty section (what is real, what is simulated)

- **Real:** phone gyro data; camera pixels and the flow computed from
  them; the conflict detection; the live Gemma inference; every session is
  recorded and replayable.
- **Synthetic and labeled:** fault injection (deterministic profiles,
  applied server-side before the monitor; the pipeline cannot tell
  injected from physical). Injection is the primary trigger — a hand-spun
  phone rarely rails a consumer IMU, and we make no claim about
  degrees-per-second equivalence to a lander.
- **Simulated and labeled:** the descent consequence. The dashboard states
  verbatim: *"Accelerated simulated descent consequence driven by real or
  replayed sensor data. The phone is not physically descending, and this
  is not a flight-accurate Schiaparelli simulator."*
- **A proxy, not a sensor:** the camera signal is an uncalibrated
  rotational-motion proxy affected by texture, lighting, translation,
  moving objects, depth, rolling shutter, and exposure. We compare shape
  and trend against the gyro, never unit-for-unit values, and the proxy
  reports its own confidence so low light shows up as *unreliable* rather
  than silently wrong.
- **Golden runs:** deterministic synthetic hand-motion traces (seeded,
  committed) pushed through the *live* pipeline on replay — Gemma really
  diagnoses each replayed conflict; nothing downstream is pre-recorded.
  Replay is always labeled REPLAY.
- We do **not** claim classical software couldn't catch the injected gyro
  fault. The monitor detects the conflict; Gemma independently interprets
  the evidence and classifies fault and recovery; deterministic guardrails
  retain final authority.

## Reproducibility

- Public repo, pinned `requirements.txt`, vendored Chart.js — **no CDN, no
  cloud API; the demo runs with the network off.**
- `README.md` has exact steps: venv, `ollama pull`, one `uvicorn` command;
  two documented HTTPS options for the phone.
- Three committed golden runs make the full demo reproducible with no
  phone: `python scripts/replay_check.py` verifies all three end to end
  (and `ARBITER_FORCE_FALLBACK=1` proves the no-model path).
- Unit tests cover schemas, the state machine, guardrail invariants, the
  fallback classifier, and the descent sim.

## Limits and future work

- The camera proxy is coarse: it tracks trend and timing, not calibrated
  rates; darkness or textureless scenes degrade it (which the system
  detects and displays rather than hides).
- Single vehicle, single fault at a time, demo-scale descent dynamics.
- The generalization claim — arbitrating faults nobody enumerated — is
  demonstrated across seven fault classes and three live scenarios, not
  across a flight-qualified fault catalog. The natural next step is
  hardware-in-the-loop testing with recorded flight telemetry and a
  broader taxonomy (thermal drift, timing skew, correlated failures), plus
  a quantized Gemma variant profiled on radiation-tolerant edge hardware.

*Fault-mechanism source: A. Ferri et al., "ExoMars 2016: Schiaparelli
anomaly investigation," EPSC2017-614.*
