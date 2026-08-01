# Janus - Sensor Arbiter

**Deep-space descent fault arbitration with edge Gemma.** Named for the
two-faced Roman god of gates and transitions: Janus watches two independent
witnesses of the same motion and judges, alone, which face to believe.

A phone stands in for a spacecraft during descent. Two independent sensors
observe its rotation: the IMU gyroscope, and a **camera-derived
rotational-motion proxy computed from pixels only**. A deterministic monitor
watches the two streams at frame rate; when they persistently conflict, a
**local Gemma model is woken exactly once** to diagnose the fault, decide
which sensor (if any) to trust, and recommend a safe action. A deterministic
guardrail validates that proposal against safety invariants before it becomes
the flight decision. The dashboard shows a clearly-labeled simulated
**guarded descent** that holds a conservative attitude freeze while
arbitration runs, follows the validated verdict, and lands.

Fault class modeled: single-axis rotational-rate saturation, the same
failure mechanism as ESA's 2016 Schiaparelli loss (Ferri et al.,
EPSC2017-614). Left unarbitrated, that fault drives the altitude estimate
negative and cuts the parachute early; the mission narrative and reports
state that mechanism explicitly.

Everything runs offline: local model via Ollama, no CDN, no cloud API.

## Quick start (no phone needed — golden replay)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# model (any Gemma 4 variant; see "Model choice" below)
ollama pull gemma4:e4b

# start the server
uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000/dashboard/ on the laptop, pick a golden run,
press **▶ Replay golden run**. The full demonstration — conflict, Gemma
diagnosis, guardrail validation, guarded descent outcome — runs from the
committed recording with no phone and the network off.

Headless verification of all three golden runs:

```bash
python scripts/replay_check.py                          # real Gemma verdicts
ARBITER_FORCE_FALLBACK=1 python scripts/replay_check.py # no-model fallback path
```

## Model choice

`GEMMA_MODEL` (env var or `server/config.py`) selects the Ollama model — a
one-line swap:

| model | size | measured verdict latency (24 GB MacBook) |
|---|---|---|
| `gemma4:e4b` (default) | 9.6 GB | **~2.5 s** |
| `gemma4:31b-it-qat` | 18 GB | minutes on 24 GB RAM (swaps); use only on big-RAM hosts |

Gemma runs **only on conflict** (never per frame), so a slow verdict does not
affect telemetry; the dashboard shows "diagnosing…" until it lands, and
`GEMMA_TIMEOUT_S` caps the wait before the deterministic fallback completes
the decision (labeled as such).

Note: the arbiter calls Ollama with `think=False`. Gemma 4's hidden thinking
phase can consume the whole token budget and return empty content under
schema-constrained decoding; the verdict schema already demands explicit
evidence and an alternative hypothesis.

The server warms the model at startup so the first conflict is not eaten by
model-load time. Keep Ollama running before the demo.

## Live demo with the phone

The phone page needs **HTTPS**: `getUserMedia` and iOS `DeviceMotionEvent`
both require a secure context, and iOS requires the permission prompt to be
triggered by a user tap.

Two documented options:

**Option A — tunnel (easiest):**
```bash
ngrok http 8000
```
Open `https://<your-id>.ngrok-free.app/phone/` on the phone. The page
connects back over WSS through the same tunnel. (The tunnel is for the
phone's TLS requirement only; inference stays local.)

**Option B — LAN with a self-signed cert (fully offline):**
```bash
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem \
        -days 30 -subj "/CN=$(ipconfig getifaddr en0)"
uvicorn server.main:app --host 0.0.0.0 --port 8443 \
        --ssl-keyfile key.pem --ssl-certfile cert.pem
```
On the phone open `https://<laptop-LAN-IP>:8443/phone/` and accept the
certificate warning (iOS: you may need to view the cert and trust it).

Then:
1. On the phone: tap **ENABLE SENSORS**, grant motion + camera. Point the
   camera at a textured, well-lit scene.
2. On the laptop: open the dashboard. With the phone still or moving gently,
   both streams should plot and agree in trend.
3. Press **Reset** so the descent starts fresh, then within ~25 s press a
   fault button (⚡ gyro saturation / 🕶 camera dark / 〰 transient).
   Injection is deterministic and synthetic — it overwrites the stream
   server-side before the monitor sees it, and is labeled on screen.
4. Watch: conflict state machine arms → Gemma diagnosis appears with its
   source label → the guarded vehicle rides out the conflict and lands SAFE.
5. Show all three scenarios — Gemma returns a different verdict for each,
   including trusting the **gyro** (camera dark) and merely observing
   (transient).
6. Button-free physical fault: cover the rear lens with your palm for six
   seconds while slowly rotating the phone — a genuine
   camera-obstruction conflict with no injection at all.
7. Fallback check: stop Ollama (`pkill ollama`), replay a golden run, and
   confirm the decision still lands, labeled **FALLBACK**.

## Mission log and printable reports

**Mission log** (dashboard, bottom left) — a wall-clock-stamped record of
everything significant: state transitions, injections, the arbiter waking,
verdicts, guardrail overrides, and descent beats (touchdown or impact).
Each line carries both the wall clock and mission elapsed time
(`T+mm:ss.ss`); hover for the full ISO timestamp and the sample-stream
time. Sensor samples are deliberately *not* here — they stay in the JSONL
session recording, so the log never fills with frame-rate noise. **Key
events** filters to conflicts/decisions/overrides/injections; **Clear
view** clears only the screen, never the server's record.

**Reports** — a report is generated automatically the moment a decision is
made, and one whole-session report is always available.

### Gemma writes the report

The **same local Gemma model writes the report's prose**
(`server/narrator.py`) — headline, summary, what happened, why, reviewer
note. This is a second, deliberately separate use of the model:

| | arbiter (`arbiter.py`) | narrator (`narrator.py`) |
|---|---|---|
| decides | the flight decision | nothing |
| fenced by | the deterministic guardrail | numeric verification |
| runs | on conflict, in the decision path | after the decision is broadcast |
| if it fails | deterministic fallback classifier | deterministic templated text |

The narrator is handed a fact sheet built deterministically from the
report, returns a schema-constrained narrative, and every figure it writes
is checked back against the fact sheet. A number that is not in the record
fails the report, triggering one stricter retry and then the deterministic
text. Stop Ollama and reports still generate, labelled `DETERMINISTIC
TEXT`. Tuning: `NARRATOR_ENABLED=0` disables it, `NARRATOR_TIMEOUT_S` caps
the wait.

## Fault scenarios

| button | what is injected | correct diagnosis | correct action | guarded outcome |
|---|---|---|---|---|
| ⚡ gyro saturation | gyro pinned at 34 rad/s (rail) for 10 s | `gyro_saturation`, trust camera | `switch_to_camera` | SAFE |
| 🕶 camera dark | flow confidence → ~0 for 10 s | `camera_obstruction_or_darkness`, trust gyro | `continue_with_gyro` | SAFE |
| 〰 transient | 0.4 s blip (ignored) + 2 s blip (arbitrated) | `transient_disagreement` | `observe_transient_conflict` | SAFE |

Each scenario has a committed golden run in `data/` reachable from the
dashboard with no phone. If a decisive fault signature lands while a
noise-triggered conflict is already active, the monitor **escalates**: it
opens a new conflict and re-arbitrates once with the fresh evidence.

## Architecture

```
phone (HTTPS/WSS)                 laptop
 gyro rad/s ──────┐   ┌─ injection (synthetic, labeled) ─ MONITOR (detect only,
 camera pixels →  ├─ ws ┤                                  state machine, wakes
 rotation proxy ──┘   └─ recorder (every session)          Gemma ONCE per conflict)
                                                              │ compact evidence
                                             GEMMA ARBITER (diagnose + recommend)
                                             FALLBACK (deterministic, on timeout)
                                                              │ proposed verdict
                                             GUARDRAIL (safety invariants only)
                                                              │ final decision + source
                                             GUARDED DESCENT SIM → dashboard (WS)
                                             MISSION LOG + GEMMA-NARRATED REPORTS
```

Trust boundary in one line: **the monitor detects, Gemma diagnoses, the
guardrail validates, deterministic fallback guarantees completion.**

## State-machine tuning

Defaults in `server/config.py` (divergence threshold, CANDIDATE persistence,
RECOVERING agreement duration, cooldown, flow-confidence floor, motion
floor) are tuned against the committed golden runs *and* live-phone noise —
all three pass `scripts/replay_check.py`. Live conditions may still differ
(lighting, scene texture, hand motion); the constants are grouped and
commented for on-site adjustment.

## Gotchas handled

- iOS motion/camera permission only from a user gesture over HTTPS, with a
  clear on-page error if denied.
- Auto-reconnecting websockets on both phone and dashboard; a dropped socket
  never freezes the pipeline.
- Flow computation is downscaled, budgeted, and **drops frames** rather than
  queueing when behind.
- Gyro is calibrated rad/s, the camera proxy is uncalibrated: both are
  normalized and compared by **shape and trend**, with an adaptive scale
  learned only while the streams agree.
- A still gyro is trustworthy at rest, so modest camera-flow noise against a
  still phone is treated as noise, not conflict.
- Low flow confidence is displayed as *camera unreliable* rather than
  letting a dark/textureless scene silently invert the comparison.
- Hard timeout on the Gemma call; on timeout or error the deterministic
  fallback completes the decision and the dashboard labels the source.
- The descent is a one-shot story from 3700 m: press **Reset** before a live
  fault demonstration so the vehicle is still airborne when the fault lands.

## Repository map

```
server/    monitor.py (detect + state machine)  arbiter.py (Gemma)
           fallback.py (minimal deterministic)  guardrail.py (invariants)
           descent.py (guarded sim)  inject.py  recorder.py  mission_log.py
           narrator.py (Gemma-written reports)  report_html.py  report_pdf.py
           main.py  config.py  schemas.py
phone/     index.html capture.js (gyro + pixels-only optical flow)
dashboard/ index.html dashboard.js vendor/chart.umd.min.js (pinned, local)
data/      three committed golden runs (jsonl)
scripts/   make_golden.py  replay_check.py
tests/     pytest suites (99 tests)
```

Run tests: `python -m pytest tests/ -q`
