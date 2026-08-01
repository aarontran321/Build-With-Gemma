# FINAL MASTERPLAN — Sensor Arbiter (Build with Gemma: Triage In Light Speed)

This document has two parts.
- Part A is context for the team: the hackathon, the inferred rubric, and how it shapes priorities. Do not paste Part A into Fable.
- Part B is the single build prompt. Paste everything below the "PART B" line into Claude Fable 5.

---

# PART A. CONTEXT FOR THE TEAM (do not paste into Fable)

## The event

Build with Gemma: Triage In Light Speed. GDG Waterloo, one-day in-person sprint at RIM Park, sponsored by the Google Gemma team. Theme: life-or-death decisions in fractions of a second. Submission is a Kaggle Writeup judged by a panel.

We are building for Track 2, Trajectory and Orbit (Deep Space Navigation): tools that assist mission control or spacecraft systems with real-time telemetry analysis, orbital anomaly detection, or autonomous navigation when communicating with Earth is delayed. Track 2 explicitly favors multimodal vision tools, predictive physics assistants, and edge-deployed models. Our project sits squarely in this: an edge-deployed Gemma model that does autonomous sensor-fault arbitration on a vehicle that is minutes from help and must decide alone.

## The judged artifact is a Kaggle Writeup, not only a live demo

Two consequences. First, a committed, reproducible repository plus a clear written case matters as much as the on-stage moment. Second, a short demo video is your insurance: it is judged even if live hardware misbehaves. Budget real time for the writeup and the video, not just the code.

## Inferred rubric (NOT official, verify on the Kaggle competition page)

I could not find a published rubric for this specific one-day event. The following is inferred from the criteria that recur across Gemma-team hackathons and from this event's description. Treat it as a planning aid, not ground truth. Confirm the real weights on Kaggle before optimizing against them.

Likely scored dimensions:
1. Effective Gemma implementation (core utilization, not a thin API wrapper). Gemma must do a central, technically meaningful job. This is the dimension most likely to separate winners at a Gemma-sponsored event, and it is exactly the one our earlier drafts were weakest on. The fix in Part B makes Gemma the primary fault-diagnosis engine, not a summarizer.
2. Technical execution. It runs, it performs live inference, the architecture is sound, the trust boundaries are clean.
3. Impact and clear use case. The problem is real and legible in seconds. Deep-space fault management, comms delay, no human in the loop.
4. Communication. The writeup and demo make the pain and the payoff obvious fast.
5. Reproducibility. Public repo, pinned deps, documented run steps, works offline.
6. Theme fit. Split-second, high-stakes, real-time. Ours fits Track 2 directly.

## What this means for how we spend the remaining hours

- Make Gemma visibly do real diagnostic work. The single strongest proof is Gemma returning different verdicts for different injected faults, including cases where it trusts the gyro or trusts neither. That is built into Part B as mandatory.
- Get the golden replay working first. It is the minimum judged artifact and must not depend on the phone.
- Write the WRITEUP.md and record the video. Do not let these slip to the final ten minutes.
- Stop refining this prompt after this version. The prompt is thorough. The remaining risk is execution time, not prompt detail.

## One standing accuracy caveat

The "single-axis" saturation framing rests on the Copernicus secondary paper (Ferri et al.), not cross-checked against ESA's primary inquiry report. It is fine for the writeup as worded. If a judge who knows the incident presses, that is the one claim to have verified.

---

# PART B. MASTER BUILD PROMPT (paste everything below this line into Claude Fable 5)

---

## Role

You are a senior full-stack engineer building a hackathon demo in one shot. Produce a complete, runnable repository with clear run instructions, plus a judging writeup. Prefer boring, reliable choices over clever ones. Every claim the demo makes must be technically defensible. When you must choose between "impressive" and "reliable," choose reliable and note the tradeoff in a comment.

This is a Build with Gemma hackathon (Google Gemma team sponsored), Track 2, Deep Space Navigation. The judged artifact is a Kaggle writeup plus a reproducible repo and a short video. Gemma must perform a central, technically meaningful function. It must not be a wrapper that summarizes a decision deterministic code already made.

## Priority order

Treat requirements in this order. Never sacrifice a higher-priority requirement to complete a lower-priority one.

P0. Reliable demonstration
- The repository starts successfully.
- Replay Golden Run completes the entire demonstration from sensor input through final landing outcome, offline, with no phone.
- The dashboard stays responsive if the phone, Ollama, or a WebSocket connection fails.

P1. Meaningful Gemma role and technical integrity
- Gemma is the primary fault-diagnosis engine, not a summarizer (see "Gemma is the primary fault arbiter").
- The camera signal is computed from pixels only and is fully independent of the IMU.
- Gemma is invoked exactly once per conflict event.
- Deterministic code validates every model recommendation against safety invariants.
- No runtime component depends on internet access.
- All claims and labels are technically honest.

P2. Live hardware
- Phone gyroscope and camera stream over HTTPS and WSS.
- Fault injection fires deterministically, and supports multiple distinct fault types.
- Live camera motion estimate tracks phone movement well enough for the demo.

P3. Presentation polish
- Animations, styling, and optional real physical saturation detection.

## Required implementation order

Build and verify in this order. Run the relevant tests after each phase.
1. Schemas, classical monitor, deterministic fallback classifier, guardrail, descent simulation, and unit tests.
2. Golden-run replay and dashboard, including at least three committed golden runs (see fault scenarios).
3. Ollama integration with a hard timeout and deterministic fallback.
4. Live phone WebSocket ingestion.
5. Camera-based motion estimator and iOS permission handling.
6. WRITEUP.md, then visual polish and optional features.

The replay demonstration is the minimum successful deliverable and must not depend on phone hardware.

## Required success paths

Golden replay path: runs entirely from one laptop, no phone, reproduces the full conflict, Gemma diagnosis, guardrail validation, and dual descent outcome.
Live path: real phone gyro and camera-derived motion, with deterministic synthetic fault injection as the primary trigger.
Graceful fallback path: if Ollama errors or exceeds its timeout, a minimal deterministic classifier completes the demonstration. The dashboard clearly labels whether each result came from Gemma, deterministic fallback, or a guardrail override.

Do not use fake timing, randomized sensor results, simulated model streaming, or hard-coded live-mode outcomes. Replay mode may reproduce recorded events but must be visibly labeled REPLAY.

## The thesis-versus-demo tension (resolve it deliberately)

The flagship pitch is "you cannot enumerate every fault, which is why an onboard language model earns its place." The primary injected fault here, single-sensor rotational saturation, is one a deterministic monitor can detect. That is intentional: it is the legible case that proves the arbitration architecture end to end. Do not let the honest framing quietly contradict the thesis. Resolve it explicitly in copy and narration:
- This is the simple case that makes the loop visible and verifiable.
- The claim is that the same architecture (independent evaluator woken on conflict, deterministic guardrail retaining authority) generalizes to conflicts a fixed lookup table cannot enumerate, where the model interprets novel combinations of evidence. The multiple fault scenarios below demonstrate exactly this generalization.
- The injected saturation is the demonstration vehicle, not the hard problem.

## What we are building, in one paragraph

A phone stands in for a spacecraft during descent. It has two independent ways to observe rotational motion: the built-in gyroscope, representing an IMU, and a camera-derived rotational-motion proxy computed exclusively from pixels, representing an independent secondary sensing path. The two signals normally follow the same motion trend. We inject faults that reproduce the class of failure involved in ESA's 2016 Schiaparelli loss: rotational-rate saturation causing erroneous downstream navigation information. A small local Gemma model is the primary fault-diagnosis engine, woken only at the moment of a persistent sensor conflict. It classifies the fault, identifies which sensor to trust, states its confidence, and recommends a safe action. Deterministic guardrails validate that proposal against safety invariants before it becomes the displayed flight decision. On the same input, the dashboard shows a simulated naive path that trusts the corrupted sensor and crashes, alongside a guarded path that follows Gemma's diagnosis and lands safely.

## The real event we are modeling (use this framing verbatim in demo copy)

Schiaparelli's entry and most of its descent were nominal. Per Ferri et al. (EPSC2017-614, Copernicus Meetings): the aerobraking under the frontshield occurred as expected, the parachute deployed normally, and the heatshield was released 40 seconds later as programmed. The unexpected dynamics of the vehicle at parachute inflation resulted in the saturation of one of the gyroscopes, which caused the fatal error in the guidance and control system. The erroneous attitude information generated a negative altitude estimate, which triggered a premature release of the parachute and backshell, a too-brief firing of the retrorockets, and activation of on-ground systems as if it had landed. In reality it was still around 3.7 km up and fell to the surface 33 seconds later at about 150 m/s. Source: https://meetingorganizer.copernicus.org/EPSC2017/EPSC2017-614.pdf

## Accuracy rules for all on-screen, spoken, and writeup copy

- Say "the same failure mechanism: single-axis rotational-rate saturation." Do not say "identical to Schiaparelli."
- Do not put a specific degrees-per-second number on the equivalence between a hand-spun phone and the lander. That value is not sourced.
- The naive integrator's crash is a simulated descent consequence layered on real sensor data. Label it as simulated on screen. Do not imply the phone is physically falling.
- Use "camera-derived rotational-motion proxy" for the camera stream. Do not describe it as a calibrated angular-rate sensor or claim it measures radians per second.
- The camera signal is affected by scene texture, lighting, translation, moving objects, depth, rolling shutter, and exposure. The demo compares its temporal shape and trend against the gyroscope, not unit-for-unit values.
- All relevant dashboard panels must visibly distinguish: real phone sensor input, synthetic fault injection, camera-derived proxy, accelerated simulated descent consequence, and replay versus live.
- Include this visible disclaimer near the descent visualization: "Accelerated simulated descent consequence driven by real or replayed sensor data. The phone is not physically descending, and this is not a flight-accurate Schiaparelli simulator."
- Do not claim classical software could not solve the injected gyro fault. Frame it as: the deterministic monitor detects that a conflict exists; Gemma independently interprets the compact diagnostic evidence and classifies the fault and recovery action; deterministic guardrails retain final authority.

## Hard constraints (non-negotiable, enforce in code and comment each)

1. INDEPENDENCE. The camera rotation estimate is computed from camera pixels only. It must never read DeviceMotionEvent, DeviceOrientationEvent, or any IMU value. This independence is the entire credibility of the demo. Comment the optical-flow function asserting this.
2. GEMMA ON CONFLICT ONLY. Gemma is invoked exactly once per detected conflict, never per frame or per sample. A deterministic monitor performs all real-time calculation and conflict detection at frame rate. Implement the conflict lifecycle as this state machine: NORMAL -> CANDIDATE -> ACTIVE -> RECOVERING -> NORMAL. Rules: enter CANDIDATE when normalized divergence exceeds the configured threshold; return to NORMAL if the signal recovers before the persistence threshold; enter ACTIVE only after divergence persists continuously for the configured minimum duration; assign a monotonically increasing conflict_id; invoke Gemma exactly once on the transition into ACTIVE; do not invoke Gemma again while the same conflict is active; enter RECOVERING only after evidence returns toward agreement; return to NORMAL only after agreement holds continuously for the configured recovery duration; apply a configurable cooldown before another conflict may become active. Store conflict_id, state-transition times, Gemma-call count, and arbitration latency in the recorded session.
3. FAULT INJECTION IS THE PRIMARY TRIGGER, AND SUPPORTS MULTIPLE FAULT TYPES. On-screen buttons inject synthetic faults deterministically (see "Multiple injectable fault scenarios"). Real physical saturation is an optional secondary path the demo never depends on.
4. LLM PROPOSES, CODE DISPOSES. Gemma outputs a proposed diagnosis. A deterministic guardrail validates it against safety invariants before it is shown as the flight decision, and overrides it if it violates one, visibly. See the division of responsibility below.
5. FULLY OFFLINE. The model runs locally via Ollama. No cloud model API. The demo works with the network off. No CDN or external asset anywhere.
6. REPLAY AND FALLBACK. Record every session. Commit golden runs so the demo works even if the phone, lighting, or websocket fail.

## Gemma is the primary fault arbiter (this is the core of the build)

Responsibility split. Keep these roles distinct in code and in the writeup.

Classical monitor: detect only. Runs continuously and answers: are the two independent streams behaving consistently; has disagreement persisted long enough to be a conflict; what compact diagnostic evidence should be sent to Gemma; has this conflict already awakened Gemma. It may compute saturation indicators, variance, camera quality, temporal correlation, normalized divergence, and recent agreement history. It must not normally make the final fault attribution or sensor-selection decision. Its job is to detect and characterize.

Gemma: diagnose and recommend. Gemma is the primary fault-arbitration engine. When awakened it interprets the combined temporal evidence and determines: what type of failure most likely occurred; which sensor is faulty; which sensor, if any, to trust; how confident it is; what safe action the simulated vehicle should take; whether the evidence is too ambiguous for a hard decision. Gemma must distinguish among at least these fault classes: gyro_saturation, gyro_flatline, camera_degradation, camera_obstruction_or_darkness, transient_disagreement, dual_sensor_degradation, unknown. Gemma must not always choose the camera. It must be capable of trusting the gyro, trusting the camera, or trusting neither.

Deterministic guardrail: validate safety only. The guardrail does not replace Gemma and is not a second full classifier. It validates the proposed decision against a small set of high-confidence safety invariants, for example: never trust a sensor explicitly marked unavailable; never trust a gyro unmistakably pinned at an injected rail value; never trust camera flow when camera quality is effectively zero; never recommend an irreversible action when both sensors are unreliable; reject malformed or out-of-schema model output; downgrade an overconfident decision to CAUTION when evidence is fundamentally ambiguous. If Gemma's answer is consistent with the evidence and invariants, pass it through. If it violates an invariant, override it and visibly explain the override.

Normal successful path: monitor detects conflict, Gemma diagnoses, guardrail validates, simulation applies the decision. Not: monitor diagnoses, Gemma repeats, guardrail repeats.

## Deterministic fallback classifier (separate file, separate role from the guardrail)

The graceful-fallback path is NOT the guardrail. Implement a small, deliberately minimal deterministic classifier in server/fallback.py, used only when Gemma times out or errors. It handles only the obvious unambiguous case (a clearly railed or flatlined gyro against a healthy camera, or a dark or obstructed camera against a healthy gyro) well enough to carry a golden run to a correct outcome. It is intentionally dumber than Gemma and does not attempt the full seven-class taxonomy or ambiguous cases. The guardrail stays thin: safety invariants only. This resolves the apparent tension: the guardrail never reproduces Gemma's classification; fallback.py is what guarantees the demo completes without the model, and the dashboard labels which path produced the decision.

## Multiple injectable fault scenarios (mandatory, this is the evidence Gemma does real work)

Support at least three distinct injectable faults, each with its own button and its own committed golden run. Showing Gemma return different verdicts, including trusting the gyro and trusting neither, is the single strongest proof for judges that the model is diagnosing rather than mapping one flag.

1. gyro_saturation. Gyro pinned high, camera healthy. Correct diagnosis gyro_saturation, trusted_sensor camera, decision switch_to_camera. Outcome SAFE.
2. camera_obstruction_or_darkness. Camera covered so flow_confidence collapses, gyro healthy. Correct diagnosis camera_obstruction_or_darkness, trusted_sensor gyro, decision continue_with_gyro. Outcome SAFE. This is the case that proves Gemma does not reflexively trust the camera.
3. transient_disagreement. A brief blip that recovers before the persistence threshold. Correct diagnosis transient_disagreement, decision observe_transient_conflict, no switch. This exercises the state machine and shows the system does not overreact.

Optional fourth if time allows: dual_sensor_degradation, both signals unreliable. Correct decision trust_neither_enter_caution.

Each scenario has a committed golden run file and is reachable in replay with no phone.

## Architecture and data flow

```
[ PHONE (browser, HTTPS) ]
  gyro sampler (DeviceMotion, rad/s, 3-axis)
  camera (getUserMedia) -> pixel-only rotation proxy -> flow_mag, flow_confidence
  packages sample -> websocket -> laptop
        |
        v
[ LAPTOP: FastAPI server ]
  ingest -> optional fault injection (overwrites the affected stream before the monitor sees it)
  MONITOR (every sample): rates, saturation/flatline scores, variances, camera quality,
     trend correlation, normalized divergence, agreement history; runs the conflict state machine;
     on transition to ACTIVE, builds compact evidence and WAKES Gemma once
  GEMMA ARBITER (on conflict only): compact temporal evidence -> structured diagnosis JSON
  FALLBACK CLASSIFIER (only if Gemma times out/errors): minimal deterministic diagnosis
  GUARDRAIL: validates the diagnosis against safety invariants -> final decision (+ source label)
  DUAL DESCENT SIM (same input): naive path trusts the corrupted stream and CRASHES;
     guarded path follows the validated diagnosis and lands SAFE
  records session; broadcasts state -> dashboard websocket
        |
        v
[ LAPTOP: dashboard (browser) ]
  live plot: gyro rate vs camera rotation proxy diverging
  rail-out / flatline / low-confidence markers (same signature class as Schiaparelli saturation)
  Gemma diagnosis: fault_class, trusted sensor, confidence, short evidence
  decision source label: Gemma / fallback / guardrail override
  two altitude tracks side by side: NAIVE (crashes) vs GUARDED (lands)
```

Owners after the scaffold exists:
- Person 1: phone page and physical demo reliability (mount, lighting, HTTPS, iOS permission).
- Person 2: Gemma arbiter loop, prompt, structured output, fault taxonomy.
- Person 3: classical monitor, state machine, fallback classifier, guardrail invariants.
- Person 4: dashboard, dual-descent story layer, writeup, video, choreography.

## Tech stack (pinned, avoid build steps)

- Phone page: vanilla HTML and JS, no framework, no bundler. Served over HTTPS (getUserMedia and iOS DeviceMotion both require it).
- Server: Python 3.11+, FastAPI, uvicorn, native FastAPI websockets, the ollama python client, numpy.
- Model: Ollama local. Model name in one config variable GEMMA_MODEL. This is a Gemma hackathon, so use a Gemma 4 model. Default to a variant that returns a structured verdict quickly on a laptop (an E4B or 12B class variant is a safe default; gemma4:31b gives stronger reasoning but the verdict may exceed a second). Since Gemma runs only on conflict, either works; make it a one-line swap and document the tradeoff. Prefer Gemma's structured or function-calling style output so the model is doing real structured reasoning, not free text, which also reads as core utilization rather than a wrapper.
- Dashboard: vanilla HTML and JS with a pinned local copy of Chart.js at dashboard/vendor/chart.umd.min.js. No bundler, no build step. Do not load any JS, CSS, font, icon, image, or analytics from a CDN or external URL. The dashboard must work with the network disabled.

## Repository layout

```
sensor-arbiter/
  README.md                  run instructions, HTTPS setup, ollama pull, gotchas
  WRITEUP.md                 Kaggle-style judging writeup (see spec)
  server/
    main.py                  FastAPI: phone ws in, dashboard ws out, routes
    monitor.py               detection: rates, saturation/flatline, variance, camera quality,
                             divergence, agreement history, conflict state machine, wake logic
    arbiter.py               Gemma invocation, prompt, structured verdict parsing
    fallback.py              minimal deterministic classifier for the obvious case only
    guardrail.py             safety-invariant validation and override (thin, not a classifier)
    descent.py               dual descent sim (naive vs guarded) over live/replayed data
    recorder.py              session record and replay
    config.py                GEMMA_MODEL, ports, injection settings, Gemma timeout, and all
                             state-machine tuning: divergence threshold, CANDIDATE persistence,
                             RECOVERING agreement duration, cooldown, flow-confidence floor
    schemas.py               pydantic models for all messages
  phone/
    index.html               sensor capture UI, permission button, status
    capture.js               gyro sampler, camera + pixel-only rotation proxy, ws send
  dashboard/
    index.html               plots, diagnosis panel, dual-altitude, decision + source
    dashboard.js             ws client, Chart.js wiring
    vendor/
      chart.umd.min.js       pinned local Chart.js, no CDN
  data/
    golden_gyro_saturation.jsonl
    golden_camera_dark.jsonl
    golden_transient.jsonl
  tests/
    test_schemas.py
    test_monitor.py
    test_guardrail.py
    test_fallback.py
    test_descent.py
  requirements.txt
```

## Data contracts (use these field names exactly)

Phone sample (phone -> server), roughly every 40 to 60 ms:
```json
{ "t": 1730500000.123, "gyro": {"x": 0.1, "y": 0.0, "z": 4.2}, "gyro_mag": 4.2,
  "flow_mag": 0.9, "flow_confidence": 0.85, "raw_saturated": false }
```
- gyro_mag: magnitude of angular rate in rad/s from the IMU.
- flow_mag: camera-derived rotational-motion proxy, pixels only, uncalibrated units.
- flow_confidence: fraction of tracking blocks that survived rejection, 0 to 1. Low means unreliable and must be shown as such.
- raw_saturated: the phone's own hint if the IMU reports a rail value; may be false the whole time, which is why injection exists.

Compact evidence (monitor -> Gemma), computed over the last ~1.5 s. Include short temporal trends so Gemma reasons about behavior over time, not one flag:
```json
{ "conflict_id": 3,
  "gyro_rate": 34.0, "gyro_saturated": true, "gyro_rail_score": 0.99,
  "gyro_flatline_score": 0.95, "gyro_variance": 0.01,
  "gyro_trend": [0.8, 1.6, 3.2, 8.0, 34.0, 34.0, 34.0],
  "flow_rate": 4.1, "flow_quality": 0.91, "flow_variance": 0.6,
  "flow_trend": [0.7, 1.5, 3.0, 4.4, 4.0, 2.8, 1.5],
  "normalized_rate_difference": 0.92, "trend_correlation": 0.18,
  "seconds_diverged": 1.2, "recent_agreement": [0.98, 0.97, 0.91, 0.61, 0.30, 0.10],
  "camera_status": "healthy", "gyro_status": "reporting" }
```

Gemma verdict (arbiter -> guardrail), strict JSON only:
```json
{ "fault_class": "gyro_saturation",
  "faulty_sensor": "gyro", "trusted_sensor": "camera",
  "confidence": 0.96,
  "evidence": ["gyro became pinned at a high value",
               "gyro variance collapsed while camera motion stayed responsive",
               "the streams agreed before the conflict"],
  "alternative_hypothesis": "camera degradation is unlikely because camera quality remains high",
  "recommended_action": "retain parachute and use camera-derived attitude",
  "decision": "switch_to_camera" }
```
Allowed decision values: continue_with_gyro, switch_to_camera, trust_neither_enter_caution, observe_transient_conflict, request_redundant_measurement. The evidence field holds short diagnostic observations, not private chain-of-thought. The alternative_hypothesis briefly states why the most plausible competing explanation is less likely.

## Camera-derived motion estimator (implement in phone/capture.js)

Highest-risk component. It must produce a rotational-motion proxy from pixels only and react to rotation more than translation, or the independent-sensor claim is weak. Steps:
1. Frame prep. Downscale each frame to a small grid (for example 160 wide) and convert to grayscale. The coarse resolution is intentional and is what makes real-time work on a phone; comment this.
2. Sparse block matching. A fixed grid of tracking blocks (for example 6 by 4). For each block, search a small window in the next frame for the best match by sum of absolute differences, giving a per-block displacement vector and a coarse flow field.
3. Reject low-texture and outlier blocks. Discard blocks with weak best-match scores (flat regions give meaningless vectors) and drop vectors far from the field median. This stops lighting flicker and moving objects from dominating.
4. Decompose the field into rotation versus translation. This is what makes it a rotation proxy, not a generic motion detector. A pure pan or handshake (translation) gives a field where most vectors point the same way (high mean vector, low structured variation). Rotation about the viewing axis gives a curl or swirl; rotation about a perpendicular axis gives a structured gradient across the frame, not a uniform shift. Compute (a) mean flow vector magnitude, dominated by translation, and (b) a rotation proxy from the structured component, for example average curl or the residual after subtracting the mean vector. Use the rotation proxy as flow_mag. Comment clearly.
5. Temporal smoothing. A short moving average, stable enough to plot cleanly but short enough to track a fast spin.
6. Output flow_mag (uncalibrated rotation proxy) and flow_confidence (fraction of surviving blocks). Low confidence must be visible downstream.

Comment the hard constraints in this file: pixels only, never reads the IMU; reports its own confidence so low-texture or low-light shows up rather than being silently wrong; runs within the per-frame budget and drops frames rather than queueing if it falls behind. Encode the realistic expectation in comments: this proxy tracks trend and timing well enough for side-by-side comparison but is coarse and not a calibrated rate. The demo relies on the affected sensor showing an obvious signature (pinned, flatlined, or zero-confidence) while the other stays smooth and finite; that contrast is the signal, not unit agreement.

## Gemma arbiter prompt (implement in arbiter.py)

System message: "You are the primary fault-diagnosis module on a spacecraft during descent. You are independent of the sensors and of the flight controller and are used only for diagnosis, never in the normal control loop. Two independent sensors estimate the vehicle's rotation: an IMU gyroscope and a camera-based optical-flow estimator that shares no hardware or data path with the gyro. They normally agree. You are woken only when a persistent conflict is detected. From the compact temporal evidence, determine the fault class, which sensor is faulty, which sensor if any to trust, your confidence, a safe action, and whether the evidence is too ambiguous for a hard decision. A gyroscope that is saturated or railed out has exceeded its measurable range and reports a pinned or frozen maximum; a flatlined gyro reports a dead constant; a camera with near-zero quality (dark or obstructed) is unreliable. Do not assume the camera is always correct; you may trust the gyro, the camera, or neither. Output strict JSON with keys fault_class, faulty_sensor, trusted_sensor, confidence, evidence, alternative_hypothesis, recommended_action, decision. No prose outside the JSON."

User message: serialize the compact evidence JSON, then: "Diagnose the fault and recommend the action. Respond with JSON only."

Parsing: strip code fences, parse JSON, validate against the schema. If parsing or validation fails, retry once with a stricter instruction, then hand off to fallback.py so the demo never stalls. Give the whole Gemma call a hard timeout from config so a slow model never freezes the pipeline. Comment that the arbiter is intentionally independent of both the sensor pipeline and the controller, an evaluator outside the loop.

## Guardrail (implement in guardrail.py, thin, invariants only)

Validate the diagnosis (from Gemma or fallback) against safety invariants before it becomes the flight decision. Never trust a sensor marked unavailable. Never trust a gyro unmistakably pinned at an injected rail value. Never trust camera flow when flow_confidence is effectively zero. Never recommend an irreversible action when both sensors are unreliable; downgrade to CAUTION. Reject malformed or out-of-schema output. If the diagnosis is consistent with evidence and invariants, pass it through. If it violates one, override and set guardrail_overrode true with a short reason. Display the final decision plus its source: Gemma, fallback, or guardrail override. Do not re-classify here; this is validation, not a duplicate diagnosis engine.

## Dual descent sim (implement in descent.py)

A simple simulated descent driven by the live or replayed stream, starting at 3700 m to echo Schiaparelli, descending at a nominal rate. Naive path: integrates attitude from the corrupted stream; the error corrupts the vertical estimate and drives computed altitude sharply toward and below zero, triggering a simulated premature parachute-cut and a CRASH state. Guarded path: follows the validated diagnosis, uses the trusted sensor's attitude, altitude decreases smoothly, reaches SAFE. Both run on the same input so the dashboard shows them side by side. Label the panel clearly as a simulated descent consequence with the required disclaimer.

## Dashboard (implement in dashboard/)

- Top: two live plots on a shared time axis, gyro rate vs camera rotation proxy. Mark the injection moment and the fault signature (rail-out, flatline, or low confidence).
- Middle: the Gemma diagnosis, fault_class, trusted sensor, confidence, and the short evidence list, then the decision and its source label (Gemma / fallback / guardrail override).
- Bottom: the two altitude tracks, NAIVE (heads negative, CRASH) vs GUARDED (smooth, SAFE), each with a big outcome label, plus the accelerated-simulation disclaimer.
- Controls: one button per fault scenario (gyro saturation, camera dark, transient), Reset, Replay (per golden run), and a live/replay indicator. Panels must visibly distinguish real input, injected fault, camera proxy, simulated descent, and replay versus live.

## WRITEUP.md (Kaggle judging writeup, generate this)

Write it to score on the likely rubric: effective Gemma use, technical execution, impact, communication, reproducibility, theme fit. Structure:
- Problem, in two sentences. Deep-space vehicles are minutes from help and must handle sensor faults alone; the current default is safe mode or a fixed fault table, and a mis-trusted sensor can be fatal, as in Schiaparelli.
- What we built. The edge-deployed Gemma arbiter, the independent camera sensor, the monitor and guardrail trust boundary.
- Why Gemma is central. It is the primary diagnosis engine over compact temporal evidence, classifies among multiple fault types, and can trust the gyro, the camera, or neither. Show the three scenarios and the three different verdicts as the proof.
- Architecture, with the trust-boundary story: monitor detects, Gemma diagnoses, guardrail validates, deterministic fallback guarantees completion offline.
- Honesty section. State plainly what is simulated (descent), what is real (sensors), what is a proxy (camera), and that this is not a flight-accurate simulator. Judges reward this.
- Reproducibility. Exact run steps, offline operation, pinned deps, committed golden runs.
- Limits and future work. Coarse camera proxy, single-vehicle scope, and the generalization claim to unenumerated faults.
Keep it tight and legible in a couple of minutes of reading.

## Setup and run instructions (put in README.md, keep correct)

1. pip install -r requirements.txt
2. ollama pull <GEMMA_MODEL>, confirm ollama run <GEMMA_MODEL> works offline.
3. Start server: uvicorn server.main:app --host 0.0.0.0 --port 8000
4. HTTPS for the phone: getUserMedia and iOS DeviceMotion require a secure context. Document two options: a tunneling tool such as ngrok to the phone page using its https URL, or a local self-signed cert served over https on the LAN. Explain both.
5. On the phone, open the page over https, tap the permission button (iOS Safari DeviceMotionEvent.requestPermission fires only from a user gesture), grant motion and camera.
6. On the laptop, open the dashboard. Confirm both streams plot and agree when the phone is still.
7. Demo: pick a fault scenario button, or spin and inject for the gyro case. Watch the diagnosis, the source label, and the dual outcome. Show all three scenarios.
8. Fallback check: stop Ollama, run a golden replay, confirm the deterministic fallback still reaches SAFE and is labeled as fallback.

## Gotchas you must handle in code

- iOS permission: request only on a user gesture, over HTTPS, clear error if denied.
- Websocket resilience: auto-reconnect on phone and dashboard; a dropped socket must not freeze the dashboard.
- Frame rate: cap the flow computation, downscale before flow, drop frames if behind.
- Units mismatch: gyro is calibrated rad/s, the camera proxy is uncalibrated. Normalize both to a comparable scale for divergence; comment that the comparison is shape and trend.
- Gemma timeout and fallback: hard timeout on the Gemma call; on timeout or error, fallback.py completes the decision; the dashboard shows the source.
- State-machine tuning: CANDIDATE persistence, RECOVERING agreement duration, cooldown, and divergence threshold need defaults tuned against the committed golden runs. Too short wakes the model on noise; too long misses the conflict. Ship defaults that pass all three golden runs and note in the README that live tuning may differ.
- Flow confidence: when low (dark or textureless), show the camera signal as unreliable rather than ground truth. Do not let a low-texture background silently invert the demo.

## Definition of done

- Replay of each of the three golden runs reproduces the full demo (conflict, Gemma diagnosis, guardrail, dual descent, correct outcome) with no phone and the network off. This is the minimum successful deliverable.
- Gemma returns three different, correct verdicts across the three scenarios, including at least one where it trusts the gyro and does not switch to the camera.
- With Gemma disabled, fallback.py alone still drives the gyro-saturation golden run to SAFE, labeled as fallback.
- Phone streams gyro and the pixel-only camera proxy over HTTPS/WSS. Still phone: streams agree.
- Conflict lifecycle runs NORMAL -> CANDIDATE -> ACTIVE -> RECOVERING -> NORMAL; Gemma is invoked exactly once per conflict, on the transition to ACTIVE; the session logs conflict_id, transition times, call count, and arbitration latency.
- Guardrail validates, overrides, or defers to fallback, and the dashboard shows the source.
- Dual descent shows NAIVE crash vs GUARDED safe landing on the same input, with the disclaimer visible.
- Dashboard distinguishes real input, injected fault, camera proxy, simulated descent, and replay versus live.
- No asset loads from a CDN or external URL; the dashboard works offline.
- WRITEUP.md exists and follows the rubric-aligned structure and the accuracy rules.
- Unit tests for schemas, monitor, guardrail, fallback, and descent pass.

Build the full repository now. Output every file in full. Follow the required implementation order and run the relevant tests after each phase. After the code, give a short "first 15 minutes" checklist for a team of four to verify the demo end to end, and a one-paragraph script for the 90-second video. 