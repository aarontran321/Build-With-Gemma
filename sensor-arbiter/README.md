# Sensor Arbiter — deep-space descent fault arbitration with edge Gemma

A phone stands in for a spacecraft during descent. Two independent sensors
observe its rotation: the IMU gyroscope, and a **camera-derived
rotational-motion proxy computed from pixels only**. A deterministic monitor
watches the two streams at frame rate; when they persistently conflict, a
**local Gemma model is woken exactly once** to diagnose the fault, decide
which sensor (if any) to trust, and recommend a safe action. A deterministic
guardrail validates that proposal against safety invariants before it becomes
the flight decision. The dashboard shows a simulated naive descent that
trusts the corrupted sensor and crashes, next to the guarded descent that
lands.

Fault class modeled: single-axis rotational-rate saturation — the same
failure mechanism as ESA's 2016 Schiaparelli loss (Ferri et al.,
EPSC2017-614).

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
diagnosis, guardrail validation, dual descent outcome — runs from the
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
3. Press a fault button (⚡ gyro saturation / 🕶 camera dark / 〰 transient).
   Injection is deterministic and synthetic — it overwrites the stream
   server-side before the monitor sees it, and is labeled on screen.
4. Watch: conflict state machine arms → Gemma diagnosis appears with its
   source label → dual descent shows NAIVE crash vs GUARDED landing.
5. Show all three scenarios — Gemma returns a different verdict for each,
   including trusting the **gyro** (camera dark) and merely observing
   (transient).
6. Fallback check: stop Ollama (`pkill ollama`), replay a golden run, and
   confirm the decision still lands, labeled **FALLBACK**.

## Mission log and printable reports

**Mission log** (dashboard, bottom left) — a wall-clock-stamped record of
everything significant: state transitions, injections, the arbiter waking,
verdicts, guardrail overrides, and descent beats (premature chute cut,
impact, touchdown). Each line carries both the wall clock and mission
elapsed time (`T+mm:ss.ss`); hover for the full ISO timestamp and the
sample-stream time. Sensor samples are deliberately *not* here — they stay
in the JSONL session recording, so the log never fills with frame-rate
noise. **Key events** filters to conflicts/decisions/overrides/injections;
**Clear view** clears only the screen, never the server's record.

**Reports** — a report is generated automatically the moment a decision is
made, and one whole-session report is always available.

### Gemma writes the report

The **same local Gemma model writes the report's prose** (`server/narrator.py`)
— headline, summary, what happened, why, reviewer note. This is a second,
deliberately separate use of the model:

| | arbiter (`arbiter.py`) | narrator (`narrator.py`) |
|---|---|---|
| decides | the flight decision | nothing |
| fenced by | the deterministic guardrail | numeric verification |
| runs | on conflict, in the decision path | after the decision is broadcast |
| if it fails | deterministic fallback classifier | deterministic templated text |

Because the narrator makes no decision, it is allowed to write freely where
the arbiter is not. What it may **not** do is invent facts, so the same
"propose then verify" shape is applied to prose:

1. It is handed a **fact sheet** built deterministically from the report —
   never the raw pipeline.
2. It returns a **schema-constrained** narrative (Ollama `format`), not free
   text scraped afterwards.
3. Every figure it writes is **checked back against the fact sheet**. A
   number that is not in the record fails the report, triggering one
   stricter retry and then the deterministic text.

Narration runs *after* the decision is already on the dashboard, so a slow
narration can never delay a flight decision or the telemetry. Stop Ollama
and reports still generate — labelled `DETERMINISTIC TEXT` with the reason.
The byline on every report says which engine wrote it; the tables, evidence
and timeline around the prose are always deterministic and never
model-written.

Tuning: `NARRATOR_ENABLED=0` disables it, `NARRATOR_TIMEOUT_S` caps the wait
(default 120 s; measured ~15–17 s for `gemma4:e4b`).

### What is in a report

Each answers *what happened and why* in pipeline order:

| section | contents |
|---|---|
| Summary / What happened / Why | **written by Gemma**, verified against the record |
| Decision | fault class, trusted/faulty sensor, action, confidence, source, latency |
| Guardrail override | (only when one fired) invariant violated, what the model proposed, what replaced it |
| Why this decision | 5 stages: detection → evidence → diagnosis → validation → consequence, each stating what that stage saw and what it therefore did |
| Evidence given to the arbiter | the exact compact window the arbiter received, and nothing else |
| Descent consequence | NAIVE vs GUARDED outcome |
| Full event timeline | every logged event with both clocks |
| Governing parameters | the thresholds that actually applied, so a decision can be re-checked against its own tuning |

### Export as PDF

Press **⬇ PDF** in the Reports panel (or **Download PDF** on the report
page). The PDF is rendered **server-side** by `server/report_pdf.py` — no
browser print dialog, no manual step — and downloads as
`sensor-arbiter_<session>_<report>.pdf`. The report page still prints
directly with Cmd/Ctrl-P if paper is what you want.

ReportLab was chosen over the alternatives on purpose: WeasyPrint needs
Cairo/Pango system libraries, and driving headless Chrome would make export
depend on a browser being installed. ReportLab is a pure-Python wheel, so
`pip install -r requirements.txt` is the whole setup and PDF export works on
any judging machine, fully offline.

Attribution rule enforced throughout: the diagnosis is always credited to
whoever *proposed* it (Gemma or the fallback). The guardrail validates and
can veto — it never classifies a fault, and no report implies it did.

Endpoints, if you want the data rather than the page:

```
GET /api/log?since=<seq>          significant events since a sequence number
GET /api/reports                  index of available reports
GET /api/report/<id>.pdf          server-rendered PDF (id: session | latest | conflict-N)
GET /api/report/<id>.html         the same report as a printable page
GET /api/report/<id>.json         the same report as structured JSON
```

Reports are assembled on request from live state, never cached, so a
printed report always shows the descent outcome true at the moment it was
generated. Reports survive a **Reset** — the operator can still print what
just happened — and conflict numbering that restarts after a reset never
overwrites an earlier report.

## Fault scenarios

| button | what is injected | correct diagnosis | correct action | outcome |
|---|---|---|---|---|
| ⚡ gyro saturation | gyro pinned at 34 rad/s (rail) for 10 s | `gyro_saturation`, trust camera | `switch_to_camera` | naive CRASH, guarded SAFE |
| 🕶 camera dark | flow confidence → ~0 for 10 s | `camera_obstruction_or_darkness`, trust gyro | `continue_with_gyro` | naive CRASH, guarded SAFE |
| 〰 transient | 0.4 s blip (ignored) + 2 s blip (arbitrated) | `transient_disagreement` | `observe_transient_conflict` | both SAFE |

Each scenario has a committed golden run in `data/` reachable from the
dashboard with no phone.

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
                                             DUAL DESCENT SIM → dashboard (WS)
```

Trust boundary in one line: **the monitor detects, Gemma diagnoses, the
guardrail validates, deterministic fallback guarantees completion.**

## State-machine tuning

Defaults in `server/config.py` (divergence threshold, CANDIDATE persistence,
RECOVERING agreement duration, cooldown, flow-confidence floor) are tuned
against the committed golden runs — all three pass `scripts/replay_check.py`.
Live phone tuning may differ (lighting, scene texture, hand motion); the
constants are grouped and commented for on-site adjustment.

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
- Low flow confidence is displayed as *camera unreliable* rather than
  letting a dark/textureless scene silently invert the comparison.
- Hard timeout on the Gemma call; on timeout or error the deterministic
  fallback completes the decision and the dashboard labels the source.

## Repository map

```
server/    monitor.py (detect + state machine)  arbiter.py (Gemma)
           fallback.py (minimal deterministic)  guardrail.py (invariants)
           descent.py (dual sim)  inject.py  recorder.py  main.py  config.py
           mission_log.py (event log + report assembly)
           narrator.py (Gemma writes the report prose, fact-verified)
           report_html.py (report web page)  report_pdf.py (PDF export)
phone/     index.html capture.js (gyro + pixels-only optical flow)
dashboard/ index.html dashboard.js vendor/chart.umd.min.js (pinned, local)
data/      three committed golden runs (jsonl)
scripts/   make_golden.py  replay_check.py
tests/     pytest suites for schemas, monitor, guardrail, fallback, descent,
           mission log + reports, narrator, PDF export
```

Run tests: `python -m pytest tests/ -q`
