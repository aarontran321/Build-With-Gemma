# MASTER PROMPT — Sensor Arbiter (Build With Gemma)

Paste everything below the line into Claude Fable 5. It is written as a single, self-contained build instruction.

---

## Role

You are a senior full-stack engineer building a hackathon demo in one shot. Produce a complete, runnable repository with clear run instructions. Prefer boring, reliable choices over clever ones. Every claim the demo makes on stage must be technically defensible. When you must choose between "impressive" and "reliable," choose reliable and note the tradeoff in a comment.

## Priority order

Treat requirements in this order. Never sacrifice a higher-priority requirement to complete a lower-priority one.

P0. Reliable demonstration
- The repository starts successfully.
- Replay Golden Run completes the entire demonstration from sensor input through final landing outcome.
- The dashboard remains responsive if the phone, Ollama, or a WebSocket connection fails.

P1. Technical integrity
- Optical flow remains completely independent from the IMU.
- Gemma is invoked only once per conflict event.
- Deterministic code validates every model recommendation.
- No runtime component depends on internet access.
- All claims and labels remain technically honest.

P2. Live hardware
- Phone gyroscope and camera data stream over HTTPS and WSS.
- Fault injection works deterministically.
- Live optical flow tracks phone movement sufficiently for the demonstration.

P3. Presentation polish
- Animations, transitions, visual styling, and optional real physical saturation detection.

## Required implementation order

Build and verify the project in this order:
1. Schemas, classical monitor, guardrail, descent simulation, and unit tests.
2. Golden-run replay and dashboard.
3. Ollama integration with timeout and deterministic fallback.
4. Live phone WebSocket ingestion.
5. Camera-based motion estimator and iOS permission handling.
6. Visual polish and optional secondary features.

Run the relevant tests after each phase. The replay demonstration is the minimum successful deliverable and must not depend on phone hardware.

## Required success paths

The project must support three distinct operating paths:

Golden replay path
- Runs entirely from one laptop.
- Requires no phone.
- Reproduces the full sensor conflict, Gemma arbitration, guardrail validation, and dual descent outcome.

Live path
- Uses actual phone gyroscope and camera-derived motion data.
- Uses deterministic synthetic fault injection as the primary trigger.

Graceful fallback path
- If Ollama errors or exceeds its timeout, deterministic arbitration completes the demonstration.
- The dashboard clearly identifies whether the result came from Gemma, deterministic fallback, or a guardrail override.

Do not use fake timing, randomized sensor results, simulated model streaming, or hard-coded live-mode outcomes. Replay mode may reproduce recorded events but must be visibly labeled REPLAY.

## The thesis-versus-demo tension (read this, resolve it deliberately)

The flagship pitch is "you cannot enumerate every fault, which is why an onboard language model earns its place." This specific demo, single-sensor rotational saturation, is a fault a deterministic monitor can and does detect. That is intentional: it is the legible, teachable case that proves the arbitration architecture works end to end. Do NOT let the honest framing quietly contradict the thesis. Resolve it explicitly in copy and narration:
- This demo is the simple case that makes the loop visible and verifiable on stage.
- The claim is that the same architecture (independent evaluator woken on conflict, deterministic guardrail retaining authority) generalizes to conflicts a fixed lookup table cannot enumerate, where the model interprets novel combinations of evidence.
- The injected saturation is the demonstration vehicle, not the hard problem. State this rather than implying the model is required to solve this particular fault.

## What we are building, in one paragraph

A phone stands in for a spacecraft during descent. It has two independent ways to observe rotational motion: the built-in gyroscope, representing an IMU, and a camera-derived rotational-motion proxy computed exclusively from pixels, representing an independent secondary sensing path. The two signals normally follow the same motion trend. We deliberately corrupt the gyro to reproduce the same class of failure mechanism involved in ESA's 2016 Schiaparelli loss: single-axis rotational-rate saturation causing erroneous downstream navigation information. A small local Gemma model acts as an independent diagnostic evaluator that is woken only at the moment of a persistent sensor conflict. It proposes a fault classification, identifies which sensor should be trusted, and recommends a safe action. Deterministic guardrails validate or override that proposal before it becomes the displayed flight decision. On the same input, the dashboard shows a simulated naive path that trusts the corrupted gyro and crashes, alongside a guarded path that switches to the camera-derived signal and lands safely.

## The real event we are modeling (use this framing verbatim in demo copy)

Schiaparelli's entry and most of its descent were nominal. Per Ferri et al. (EPSC2017-614, Copernicus Meetings): the aerobraking under the frontshield occurred as expected, the parachute deployed normally, and the heatshield was released 40 seconds later as programmed. The unexpected dynamics of the vehicle at parachute inflation resulted in the saturation of one of the gyroscopes, which caused the fatal error in the guidance and control system. The erroneous attitude information generated a negative altitude estimate, which triggered a premature release of the parachute and backshell, a too-brief firing of the retrorockets, and activation of on-ground systems as if it had landed. In reality it was still around 3.7 km up and fell to the surface 33 seconds later at about 150 m/s. Source: https://meetingorganizer.copernicus.org/EPSC2017/EPSC2017-614.pdf

Accuracy rules for all on-screen and spoken copy:
- Say "the same failure mechanism: single-axis rotational-rate saturation." Do NOT say "identical to Schiaparelli."
- Do NOT put a specific degrees-per-second number on the equivalence between a hand-spun phone and the lander. That value is not sourced.
- The naive integrator's "crash" is a simulated descent consequence layered on real sensor data. Label it as simulated on screen. Do not imply the phone is physically falling.
- Use the phrase "camera-derived rotational-motion proxy" for the optical-flow stream. Do not describe it as a calibrated angular-rate sensor or claim it directly measures radians per second.
- The camera signal is affected by scene texture, lighting, translation, moving objects, depth, rolling shutter, and exposure. The demonstration compares its temporal shape and trend against the gyroscope rather than claiming unit-for-unit equivalence.
- All relevant dashboard panels must visibly distinguish: real phone sensor input, synthetic gyro fault injection, camera-derived rotational-motion proxy, accelerated simulated descent consequence, and replay mode versus live mode.
- Include this visible disclaimer near the descent visualization: "Accelerated simulated descent consequence driven by real or replayed sensor data. The phone is not physically descending, and this is not a flight-accurate Schiaparelli simulator."
- Do NOT claim classical software could not solve the injected gyro fault. Use this framing: the deterministic monitor detects that a conflict exists; Gemma independently interprets the compact diagnostic evidence and proposes a fault classification and recovery action; deterministic guardrails retain final authority.

## Hard constraints (non-negotiable, enforce in code and comment each)

1. INDEPENDENCE. The optical-flow rotation estimate must be computed from camera pixels only. It must never read `DeviceMotionEvent`, `DeviceOrientationEvent`, or any IMU value. This independence is the entire credibility of the demo. Add a comment at the optical-flow function asserting this.
2. GEMMA ON CONFLICT ONLY. Gemma is invoked exactly once per detected conflict event, never per frame or per incoming sample. A deterministic classical monitor performs all real-time calculations and conflict detection at frame rate. Implement the conflict lifecycle as this explicit state machine: NORMAL -> CANDIDATE -> ACTIVE -> RECOVERING -> NORMAL. Rules: enter CANDIDATE when normalized divergence exceeds the configured threshold; return to NORMAL if the signal recovers before the persistence threshold is reached; enter ACTIVE only after divergence persists continuously for the configured minimum duration; assign the event a monotonically increasing conflict_id; invoke Gemma exactly once when transitioning into ACTIVE; do not invoke Gemma again while the same conflict remains active; enter RECOVERING only after evidence begins returning to agreement; return to NORMAL only after agreement remains restored continuously for the configured recovery duration; apply a configurable cooldown before another conflict may become active. Store the conflict_id, state-transition times, Gemma-call count, and arbitration latency in the recorded session. This keeps latency honest and prevents threshold oscillation from repeatedly invoking the model.
3. FAULT INJECTION IS THE PRIMARY TRIGGER. A keypress or on-screen button injects a synthetic corrupted gyro reading (pegged at max / frozen / nonsensical) that fires deterministically every time. Real physical saturation is an optional secondary path that may or may not trigger; the demo must never depend on it.
4. LLM PROPOSES, CODE DISPOSES. Gemma outputs a proposed verdict. A deterministic guardrail validates that verdict against classical checks (e.g., does Gemma's "trust the camera" agree with which stream is actually railed out?) before it is displayed as the flight decision. If Gemma's verdict contradicts the deterministic evidence, the guardrail overrides it and the dashboard shows that it did. This preserves a clean trust-boundary story.
5. FULLY OFFLINE. The model runs locally via Ollama. No cloud model API. The demo must work with the network off.
6. REPLAY / FALLBACK MODE. Record every live session to a file, and support replaying a recorded session with no phone attached. This is demo insurance for stage failure. A recorded "golden run" must be committed so the demo works even if the phone, lighting, or websocket fail on the day.

## Architecture and data flow

```
[ PHONE (browser, HTTPS) ]
  gyro sampler (DeviceMotion, rad/s, 3-axis)
  camera (getUserMedia) -> optical flow (pixels only) -> flow rate
  packages sample -> websocket -> laptop
        |
        v
[ LAPTOP: FastAPI server ]
  ingest -> optional fault injection (overwrites gyro before monitor sees it)
  classical MONITOR (runs every sample):
     - gyro rate, gyro saturation/rail-out flag, gyro variance
     - optical-flow rate
     - divergence score + rolling agreement history
     - decides when to WAKE gemma
  GEMMA ARBITER (on conflict only): compact window stats -> verdict JSON
  GUARDRAIL: validates verdict vs deterministic evidence -> final decision
  DUAL DESCENT SIM (on live data):
     - naive path: trusts (corrupted) gyro -> attitude error -> altitude goes negative -> premature chute cut -> CRASH
     - arbiter path: trusts camera when gyro flagged -> sane attitude -> altitude decreases normally -> SAFE
  records session; broadcasts state -> dashboard websocket
        |
        v
[ LAPTOP: dashboard (browser) ]
  live plot: gyro rate vs camera rotation proxy diverging
  gyro rail-out marker (the same signature class as the Schiaparelli saturation)
  Gemma reasoning streaming in
  two altitude tracks side by side: NAIVE (crashes) vs ARBITER (lands)
  final flight decision + source label (Gemma / fallback / override) + guardrail status
```

Map to the four owners so the team can split after the scaffold exists:
- Person 1 owns the phone page and physical demo reliability (mount, lighting, HTTPS, iOS permission).
- Person 2 owns the Gemma arbiter loop and prompt.
- Person 3 owns the classical monitor, rail-out detection, and guardrail.
- Person 4 owns the dashboard, the dual-descent story layer, and choreography.

## Tech stack (pinned, avoid build steps)

- Phone page: vanilla HTML + JS, no framework, no bundler. Must be served over HTTPS (getUserMedia and iOS DeviceMotion both require it).
- Server: Python 3.11+, FastAPI, `uvicorn`, `websockets`/native FastAPI websockets, `ollama` python client, `numpy`.
- Model: Ollama local. Put the model name in one config variable `GEMMA_MODEL`. Default to a fast Gemma 4 variant for sub-second verdicts (for example an E4B or 12B class variant); document that `gemma4:31b` gives stronger reasoning but the verdict may exceed one second on a laptop. Since Gemma runs only on conflict, either is acceptable; make it a one-line swap.
- Dashboard: vanilla HTML and JavaScript with a pinned local copy of Chart.js stored inside the repository at `dashboard/vendor/chart.umd.min.js`. No bundler and no runtime build step. Do NOT load any JavaScript, CSS, font, icon, image, analytics script, or other asset from a CDN or external URL. The entire dashboard must work after the network is disabled.

## Repository layout

```
sensor-arbiter/
  README.md                  run instructions, HTTPS setup, ollama pull, gotchas
  server/
    main.py                  FastAPI app: phone ws in, dashboard ws out, routes
    monitor.py               classical detection: rates, rail-out, divergence, wake logic
    arbiter.py               Gemma invocation + prompt + verdict parsing
    guardrail.py             deterministic validation / override of Gemma verdict
    descent.py               dual descent sim (naive vs arbiter) over live data
    recorder.py              session record + replay
    config.py                GEMMA_MODEL, ports, injection settings, and all state-machine
                             tuning: divergence threshold, CANDIDATE persistence duration,
                             RECOVERING agreement duration, cooldown, Gemma timeout, flow-confidence floor
    schemas.py               dataclasses / pydantic for all messages
  phone/
    index.html               sensor capture UI, permission button, status
    capture.js               gyro sampler, camera + optical flow (pixels only), ws send
  dashboard/
    index.html               plots, reasoning stream, dual-altitude, decision panel
    dashboard.js             ws client, Chart.js wiring
    vendor/
      chart.umd.min.js       pinned local Chart.js, no CDN
  data/
    golden_run.jsonl         committed recorded session for fallback
  requirements.txt
```

## Data contracts (define exactly, use these field names)

Phone sample (phone -> server), roughly every 40-60 ms:
```json
{ "t": 1730500000.123, "gyro": {"x": 0.1, "y": 0.0, "z": 4.2}, "gyro_mag": 4.2, "flow_mag": 0.9, "flow_confidence": 0.85, "raw_saturated": false }
```
- `gyro_mag` is magnitude of angular rate in rad/s from the IMU.
- `flow_mag` is the camera-derived rotational-motion proxy, pixels only, uncalibrated units; the demo compares shape and trend, not absolute rad/s.
- `flow_confidence` is the fraction of tracking blocks that survived rejection (0 to 1). Low values mean the camera signal is unreliable and must be shown as such.
- `raw_saturated` is the phone's own hint if the IMU reports a rail value; may be false the whole time, which is why injection exists.

Window stats (monitor -> arbiter), computed over the last ~1.5 s:
```json
{ "gyro_rate": 34.0, "gyro_saturated": true, "gyro_variance": 0.0,
  "flow_rate": 4.1, "flow_variance": 0.6,
  "divergence": 0.92, "seconds_diverged": 1.2,
  "recent_agreement": [0.98, 0.97, 0.3, 0.1] }
```

Gemma verdict (arbiter -> guardrail), strict JSON only:
```json
{ "faulty_sensor": "gyro", "trusted_sensor": "camera",
  "confidence": 0.0-1.0, "reason": "one or two sentences",
  "recommended_action": "keep parachute / trust camera-derived attitude" }
```

## Camera-derived motion estimator (implement in phone/capture.js)

This is the highest-risk component. It must produce a rotational-motion proxy from pixels only, and it must react to rotation more than to translation, or the "independent rotation sensor" claim is weak. Implement a lightweight, dependency-free estimator with these steps:

1. Frame prep. Downscale each camera frame to a small grid (for example 160 wide) and convert to grayscale. Downscaling is what makes this run in real time on a phone; comment that the coarse resolution is intentional.
2. Sparse block matching. Place a fixed grid of tracking blocks (for example 6 by 4). For each block, search a small window in the next frame for the best match (sum of absolute differences). This yields a per-block displacement vector, giving you a coarse optical-flow field.
3. Reject low-texture and outlier blocks. Discard blocks whose best-match score is weak (flat, textureless regions give meaningless vectors) and drop vectors far from the field median. This prevents lighting flicker and moving objects from dominating.
4. Decompose the flow field into rotation versus translation. This is the step that makes the signal a rotation proxy rather than a generic motion detector:
   - A pure camera pan or handshake (translation) produces a flow field where most vectors point the same direction (high mean vector, low structured variation).
   - A rotation about the viewing axis produces a curl or swirl pattern in the field; rotation about a perpendicular axis produces a structured gradient of vectors across the frame, not a uniform shift.
   - Compute two scalars each frame: (a) the mean flow vector magnitude, which is dominated by translation, and (b) a rotation proxy from the structured component of the field, for example the average curl or the residual after subtracting the mean vector. Use the rotation proxy, not raw mean flow, as `flow_mag`. Comment this clearly.
5. Temporal smoothing. Apply a short moving average so the proxy is stable enough to plot against the gyro without high-frequency jitter, but short enough to still track a fast spin.
6. Output. Emit `flow_mag` as the rotation proxy in uncalibrated units, plus a `flow_confidence` scalar (fraction of blocks that survived rejection). Low confidence must be visible downstream so the monitor and dashboard can show when the camera signal is unreliable (for example a dark or textureless scene).

Hard constraints for this file, comment each:
- Uses camera pixels only. Never reads `DeviceMotionEvent`, `DeviceOrientationEvent`, or any IMU value.
- Reports its own confidence so low-texture or low-light conditions are visible rather than silently wrong.
- Runs within the per-frame time budget on a phone; if it falls behind, drop frames rather than queue them.

Realistic expectation to encode in comments: this proxy tracks the trend and timing of rotation well enough for a side-by-side comparison, but it is coarse, sensitive to scene conditions, and is not a calibrated angular-rate measurement. The demo relies on the gyro railing out to an obvious pinned value while the camera proxy stays smooth and finite; that contrast is the signal, not absolute agreement in units.

## Gemma arbiter prompt (implement in arbiter.py)

System message:
"You are an independent fault-arbitration module on a spacecraft during descent. You are separate from the sensors and from the flight controller, and you are used only for evaluation, never in the normal control loop. Two independent sensors estimate the vehicle's rotation rate: an IMU gyroscope and a camera-based optical-flow estimator that shares no hardware or data path with the gyro. They normally agree. You are woken only when they disagree. Decide which sensor is faulty and which to trust, and recommend the action. A gyroscope that is 'saturated' or 'railed out' has exceeded its measurable range and is reporting a pinned or frozen maximum value; such a reading is untrustworthy even though it looks confident. Be decisive and brief. Output strict JSON with keys faulty_sensor, trusted_sensor, confidence, reason, recommended_action. No prose outside the JSON."

User message: serialize the window-stats JSON above, then: "Which sensor is lying and what should the vehicle do? Respond with JSON only."

Parsing: strip any code fences, parse JSON, and if parsing fails, retry once with a stricter instruction, then fall back to a deterministic verdict derived from the saturation flag so the demo never stalls.

Note in a comment: the arbiter model is intentionally independent of both the sensor pipeline and the controller, mirroring a clean evaluator-outside-the-loop design.

## Guardrail (implement in guardrail.py)

Deterministic checks before the verdict is shown as the flight decision:
- If exactly one stream is railed out / saturated and the other is smooth and low-variance, the trustworthy sensor is unambiguously the smooth one. If Gemma agrees, pass it through. If Gemma disagrees, override and mark `guardrail_overrode = true` on the dashboard.
- If the camera stream has low `flow_confidence`, do not trust it blindly; show a caution state instead of asserting the camera is correct.
- If neither or both are saturated, treat as low-confidence and show a caution state rather than a hard decision.
Display the final decision plus whether the guardrail agreed with, overrode, or replaced (fallback) Gemma. This is the trust-boundary story: the model proposes, deterministic code disposes.

The guardrail must be complete enough to produce the full correct decision entirely on its own, because it IS the graceful-fallback path when Ollama times out or errors. Verify this explicitly: with Gemma disabled, the deterministic path alone must still drive the golden run to the correct SAFE outcome. Be honest in comments that in this specific single-sensor-saturation demo the deterministic guardrail can reach the right answer without the model; the model's role here is to interpret compact evidence and to generalize to conflicts the guardrail's fixed rules do not cover. The dashboard label (Gemma / deterministic fallback / guardrail override) is what keeps this distinction visible and honest.

## Dual descent sim (implement in descent.py)

Run a simple simulated descent driven by the live sensor stream, starting at 3700 m to echo Schiaparelli, descending at a nominal rate.
- Naive path: integrates attitude from the possibly-corrupted gyro. When the gyro is corrupted, the attitude error corrupts the vertical estimate and drives computed altitude sharply toward and below zero, triggering a simulated premature parachute-cut event and a CRASH state.
- Arbiter path: when the monitor flags the gyro and the guardrail confirms, it switches to the camera-derived attitude, altitude decreases smoothly, and it reaches a SAFE landing.
Both run on the same input simultaneously so the dashboard can show them side by side. Label this panel clearly as a simulated descent consequence.

## Dashboard (implement in dashboard/)

- Top: two live line plots on shared time axis, gyro rate vs optical-flow rate. Mark the moment of injection and the gyro rail-out.
- Middle: Gemma reasoning streaming in as text, then the parsed verdict, then the guardrail status (agreed / overrode).
- Bottom: the two altitude tracks, NAIVE (heads negative, CRASH) vs ARBITER (smooth, SAFE), with a big outcome label on each.
- A control row: Inject Fault button, Reset button, Replay Golden Run button, and a live/replay indicator.

## Setup and run instructions (put in README.md, and make them correct)

1. `pip install -r requirements.txt`
2. `ollama pull <GEMMA_MODEL>` and confirm `ollama run <GEMMA_MODEL>` works offline.
3. Start server: `uvicorn server.main:app --host 0.0.0.0 --port 8000`.
4. HTTPS for the phone: getUserMedia and iOS DeviceMotion require a secure context. Provide two documented options: (a) run a tunneling tool such as ngrok to the phone page and use the https URL, or (b) generate a local self-signed cert and serve the phone page over https on the LAN. Explain both.
5. On the phone, open the phone page over https, tap the permission button (iOS Safari `DeviceMotionEvent.requestPermission()` only fires from a user gesture), grant motion and camera.
6. On the laptop, open the dashboard page. Confirm both streams plot and agree when the phone is still.
7. Demo: spin the phone, then press Inject Fault at the peak of the spin. Watch divergence, Gemma's verdict, guardrail, and the dual outcome.

## Gotchas you must handle in code (do not skip)

- iOS permission: request only on a user gesture, over HTTPS, and show a clear error if denied.
- Websocket resilience: auto-reconnect on the phone and dashboard; a dropped socket must not freeze the dashboard.
- Frame rate: cap optical-flow computation so the phone does not overheat or lag; downscale frames before flow.
- Units mismatch: gyro is calibrated rad/s, optical flow is an uncalibrated proxy. Normalize both to a comparable scale for the divergence score; comment that the comparison is of shape and trend, not absolute rad/s.
- Graceful degradation: if Gemma is slow or errors, fall back to the deterministic verdict so the visual never stalls; show which path produced the decision. Give the Gemma call a hard timeout so a slow model never freezes the pipeline.
- Recording: always record live sessions to `data/`, and ship one committed `golden_run.jsonl` so Replay works with nothing attached.
- State-machine tuning: the CANDIDATE persistence duration, RECOVERING agreement duration, cooldown, and divergence threshold all need defaults that are then tuned against the actual golden run. If persistence is too short the model is woken by noise; too long and the conflict is missed. Ship defaults that pass the golden run, and note in the README that live-hardware tuning may differ.
- Flow confidence: when `flow_confidence` is low (dark or textureless scene), the dashboard must show the camera signal as unreliable rather than presenting it as ground truth. Do not let a low-texture background silently invert the demo.

## Definition of done

- Replay Golden Run reproduces the full demo (conflict, arbitration, guardrail, dual descent, SAFE outcome) with no phone connected and the network off. This is the minimum successful deliverable.
- With Gemma disabled, the deterministic fallback alone still drives the golden run to the correct SAFE outcome, and the dashboard labels the decision source as fallback.
- Phone streams gyro and the pixel-only camera rotation proxy over HTTPS/WSS to the laptop.
- Still phone: streams agree. Inject Fault: streams diverge, gyro shows rail-out, camera proxy stays smooth.
- Conflict lifecycle runs NORMAL -> CANDIDATE -> ACTIVE -> RECOVERING -> NORMAL; Gemma is invoked exactly once per conflict, on the transition into ACTIVE, and the recorded session logs conflict_id, transition times, call count, and arbitration latency.
- Guardrail validates, overrides, or replaces (fallback) Gemma, and the dashboard shows which.
- Dual descent shows NAIVE crash vs ARBITER safe landing on the same input, with the accelerated-simulation disclaimer visible.
- Dashboard visibly distinguishes real sensor input, injected fault, camera proxy, simulated descent, and replay versus live.
- No asset loads from a CDN or external URL; the dashboard works after the network is disabled.
- All on-screen copy follows the accuracy rules above, including the thesis-versus-demo framing.
- Unit tests exist for schemas, monitor, guardrail, and descent, and pass.

Build the full repository now. Output every file in full. Follow the required implementation order and run the relevant tests after each phase. After the code, give a short "first 15 minutes" checklist for a team of four to verify the demo end to end.