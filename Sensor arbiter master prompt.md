# MASTER PROMPT — Sensor Arbiter (Build With Gemma)

Paste everything below the line into Claude Fable 5. It is written as a single, self-contained build instruction.

---

## Role

You are a senior full-stack engineer building a hackathon demo in one shot. Produce a complete, runnable repository with clear run instructions. Prefer boring, reliable choices over clever ones. Every claim the demo makes on stage must be technically defensible. When you must choose between "impressive" and "reliable," choose reliable and note the tradeoff in a comment.

## What we are building, in one paragraph

A phone stands in for a spacecraft during descent. It has two independent ways to sense its own rotation: the built-in gyroscope (the IMU-equivalent) and a camera-based optical-flow estimate (the radar-equivalent, independent because it is derived only from pixels). The two normally agree. We deliberately corrupt the gyro to reproduce the failure mechanism that crashed ESA's Schiaparelli Mars lander in 2016: a rotation-rate sensor driven past its measurable range, feeding a wrong value into the navigation math. A small local Gemma model acts as an independent arbiter that is woken only at the moment of conflict, decides which sensor is lying, and recommends the safe action. We show, on the same live input, two outcomes side by side: a naive Schiaparelli-style integrator that trusts the bad gyro and "crashes," and the Gemma-arbitrated path that trusts the camera and "lands."

## The real event we are modeling (use this framing verbatim in demo copy)

Schiaparelli's entry and most of its descent were nominal. Per Ferri et al. (EPSC2017-614, Copernicus Meetings): the aerobraking under the frontshield occurred as expected, the parachute deployed normally, and the heatshield was released 40 seconds later as programmed. The unexpected dynamics of the vehicle at parachute inflation resulted in the saturation of one of the gyroscopes, which caused the fatal error in the guidance and control system. The erroneous attitude information generated a negative altitude estimate, which triggered a premature release of the parachute and backshell, a too-brief firing of the retrorockets, and activation of on-ground systems as if it had landed. In reality it was still around 3.7 km up and fell to the surface 33 seconds later at about 150 m/s. Source: https://meetingorganizer.copernicus.org/EPSC2017/EPSC2017-614.pdf

Accuracy rules for all on-screen and spoken copy:
- Say "the same failure mechanism: single-axis rotational-rate saturation." Do NOT say "identical to Schiaparelli."
- Do NOT put a specific degrees-per-second number on the equivalence between a hand-spun phone and the lander. That value is not sourced.
- The naive integrator's "crash" is a simulated descent consequence layered on real sensor data. Label it as simulated on screen. Do not imply the phone is physically falling.

## Hard constraints (non-negotiable, enforce in code and comment each)

1. INDEPENDENCE. The optical-flow rotation estimate must be computed from camera pixels only. It must never read `DeviceMotionEvent`, `DeviceOrientationEvent`, or any IMU value. This independence is the entire credibility of the demo. Add a comment at the optical-flow function asserting this.
2. GEMMA ON CONFLICT ONLY. Gemma is invoked once per detected conflict event, never per frame. A deterministic classical monitor does the real-time detection at frame rate. This keeps latency honest and lets the "decision in well under a second" claim hold, because Gemma runs a single short prompt at the moment of divergence, not a continuous stream.
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
  live plot: gyro rate vs optical-flow rate diverging
  gyro rail-out marker (the literal Schiaparelli signature)
  Gemma reasoning streaming in
  two altitude tracks side by side: NAIVE (crashes) vs ARBITER (lands)
  final flight decision + guardrail status
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
- Dashboard: vanilla HTML + JS + Chart.js from CDN. No build step.

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
    config.py                thresholds, GEMMA_MODEL, ports, injection settings
    schemas.py               dataclasses / pydantic for all messages
  phone/
    index.html               sensor capture UI, permission button, status
    capture.js               gyro sampler, camera + optical flow (pixels only), ws send
  dashboard/
    index.html               plots, reasoning stream, dual-altitude, decision panel
    dashboard.js             ws client, Chart.js wiring
  data/
    golden_run.jsonl         committed recorded session for fallback
  requirements.txt
```

## Data contracts (define exactly, use these field names)

Phone sample (phone -> server), roughly every 40-60 ms:
```json
{ "t": 1730500000.123, "gyro": {"x": 0.1, "y": 0.0, "z": 4.2}, "gyro_mag": 4.2, "flow_mag": 0.9, "raw_saturated": false }
```
- `gyro_mag` is magnitude of angular rate in rad/s from the IMU.
- `flow_mag` is the camera-derived rotation-rate proxy, pixels only, uncalibrated units are fine; the demo compares shape and trend, not absolute rad/s.
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

## Gemma arbiter prompt (implement in arbiter.py)

System message:
"You are an independent fault-arbitration module on a spacecraft during descent. You are separate from the sensors and from the flight controller, and you are used only for evaluation, never in the normal control loop. Two independent sensors estimate the vehicle's rotation rate: an IMU gyroscope and a camera-based optical-flow estimator that shares no hardware or data path with the gyro. They normally agree. You are woken only when they disagree. Decide which sensor is faulty and which to trust, and recommend the action. A gyroscope that is 'saturated' or 'railed out' has exceeded its measurable range and is reporting a pinned or frozen maximum value; such a reading is untrustworthy even though it looks confident. Be decisive and brief. Output strict JSON with keys faulty_sensor, trusted_sensor, confidence, reason, recommended_action. No prose outside the JSON."

User message: serialize the window-stats JSON above, then: "Which sensor is lying and what should the vehicle do? Respond with JSON only."

Parsing: strip any code fences, parse JSON, and if parsing fails, retry once with a stricter instruction, then fall back to a deterministic verdict derived from the saturation flag so the demo never stalls.

Note in a comment: the arbiter model is intentionally independent of both the sensor pipeline and the controller, mirroring a clean evaluator-outside-the-loop design.

## Guardrail (implement in guardrail.py)

Deterministic checks before the verdict is shown as the flight decision:
- If exactly one stream is railed out / saturated and the other is smooth and low-variance, the trustworthy sensor is unambiguously the smooth one. If Gemma agrees, pass it through. If Gemma disagrees, override and mark `guardrail_overrode = true` on the dashboard.
- If neither or both are saturated, treat as low-confidence and show a caution state rather than a hard decision.
Display the final decision plus whether the guardrail agreed with or overrode Gemma. This is the trust-boundary story: the model proposes, deterministic code disposes.

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
- Graceful degradation: if Gemma is slow or errors, fall back to the deterministic verdict so the visual never stalls; show which path produced the decision.
- Recording: always record live sessions to `data/`, and ship one committed `golden_run.jsonl` so Replay works with nothing attached.

## Definition of done

- Phone streams gyro and pixel-only optical flow over HTTPS to the laptop.
- Still phone: streams agree. Inject Fault: streams diverge, gyro shows rail-out.
- Gemma is woken once, returns a JSON verdict in well under the time it takes to read it aloud.
- Guardrail validates or overrides and the dashboard shows which.
- Dual descent shows NAIVE crash vs ARBITER safe landing on the same input.
- Everything runs with the network off.
- Replay Golden Run reproduces the full demo with no phone connected.
- All on-screen copy follows the accuracy rules above.

Build the full repository now. Output every file in full. After the code, give a short "first 15 minutes" checklist for a team of four to verify the demo end to end.