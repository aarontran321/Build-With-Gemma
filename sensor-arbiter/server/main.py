"""FastAPI server: phone WS in, dashboard WS out, injection + replay routes.

Pipeline per sample (identical for live and replay — only the source and
the mode label differ):

    ingest -> fault injection (live only; replays carry recorded faults)
           -> monitor (detect, state machine, wake-once logic)
           -> [on wake] arbiter task: Gemma -> (or fallback) -> guardrail
           -> dual descent sim
           -> record + broadcast to dashboards

Resilience rules (P0): a dead dashboard socket is dropped silently; a
malformed phone sample is counted and skipped; Ollama being down only
changes the decision's source label to "fallback"; a running replay wins
over live samples so the two timelines can't interleave.
"""

import asyncio
import json
import os
import time
from typing import Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import arbiter, config, guardrail
from .descent import DualDescent
from .inject import SCENARIOS, FaultInjector
from .monitor import Monitor
from .recorder import SessionRecorder, load_golden, timed_replay
from .schemas import Evidence, FinalDecision, PhoneSample

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GOLDEN_RUNS = {
    "gyro_saturation": "golden_gyro_saturation.jsonl",
    "camera_dark": "golden_camera_dark.jsonl",
    "transient": "golden_transient.jsonl",
}


class Hub:
    """Owns pipeline state and fans events out to dashboard sockets."""

    def __init__(self) -> None:
        self.monitor = Monitor()
        self.injector = FaultInjector()
        self.descent = DualDescent()
        self.recorder = SessionRecorder(os.path.join(ROOT, config.SESSIONS_DIR))
        self.dashboards: Set[WebSocket] = set()
        self.preview_dashboards: Set[WebSocket] = set()
        self.mode = "live"          # "live" | "replay"
        self.replay_name: Optional[str] = None
        self._replay_task: Optional[asyncio.Task] = None
        self.last_t: float = 0.0
        self.bad_samples = 0
        self.last_decision: Optional[FinalDecision] = None
        self.last_evidence: Optional[Evidence] = None

    # ---------------- broadcast ----------------

    async def broadcast(self, msg: dict) -> None:
        dead = []
        payload = json.dumps(msg)
        for ws in self.dashboards:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:  # a dropped socket must never freeze the pipeline
            self.dashboards.discard(ws)

    # ---------------- pipeline ----------------

    async def process_sample(self, s: PhoneSample, source: str) -> None:
        if source == "live":
            if self.mode == "replay":
                return  # replay owns the timeline; drop live samples
            s = self.injector.apply(s)
        self.last_t = s.t

        frame = self.monitor.ingest(s)
        self.recorder.event("sample", s.model_dump(exclude_none=True))
        for tr in frame.transitions:
            self.recorder.event("transition", tr.model_dump())
            await self.broadcast({"type": "transition", **tr.model_dump()})
        if frame.wake_evidence is not None:
            ev = frame.wake_evidence
            self.recorder.event("evidence", ev.model_dump())
            await self.broadcast({"type": "arbitration", "status": "started",
                                  "conflict_id": ev.conflict_id,
                                  "evidence": ev.model_dump()})
            # arbitration runs concurrently; sensor/descent stream never blocks
            asyncio.create_task(self._arbitrate(ev))

        desc = self.descent.step(s, frame)
        await self.broadcast({
            "type": "state",
            "mode": self.mode,
            "replay_name": self.replay_name,
            "t": s.t,
            "gyro_mag": round(s.gyro_mag, 4),
            "flow_mag": round(s.flow_mag, 4),
            "flow_confidence": round(s.flow_confidence, 3),
            "gyro_norm": round(frame.gyro_norm, 4),
            "flow_norm": round(frame.flow_norm, 4),
            "divergence": round(frame.divergence, 3),
            "state": frame.state,
            "conflict_id": frame.conflict_id,
            "injected": s.injected,
            "gyro_status": frame.gyro_status,
            "camera_status": frame.camera_status,
            "gyro_rail_score": round(frame.gyro_rail_score, 2),
            "descent": desc,
        })

    async def _arbitrate(self, ev: Evidence) -> None:
        verdict, source, latency, note = await arbiter.arbitrate(ev)
        final_verdict, overrode, reason = guardrail.validate(verdict, ev)
        fd = FinalDecision(
            conflict_id=ev.conflict_id,
            verdict=final_verdict,
            source="guardrail_override" if overrode else source,
            guardrail_overrode=overrode,
            override_reason=reason,
            arbitration_latency_s=round(latency, 3),
        )
        self.last_decision = fd
        self.last_evidence = ev
        self.descent.apply_decision(fd, self.last_t)
        self.recorder.event("decision", {**fd.model_dump(), "note": note,
                                         "proposed": verdict.model_dump()})
        await self.broadcast({"type": "decision", **fd.model_dump(),
                              "note": note,
                              "proposed": verdict.model_dump() if overrode else None})

    # ---------------- control ----------------

    async def reset(self, mode: str = "live") -> None:
        if self._replay_task and not self._replay_task.done():
            self._replay_task.cancel()
            self._replay_task = None
        self.monitor = Monitor()
        self.injector = FaultInjector()
        self.descent = DualDescent()
        self.mode = mode
        self.replay_name = None
        self.last_decision = None
        self.last_evidence = None
        self.recorder.event("reset", {"mode": mode, "wall_t": time.time()})
        await self.broadcast({"type": "reset", "mode": mode})

    async def start_replay(self, name: str, speed: float = 1.0) -> None:
        await self.reset(mode="replay")
        self.mode = "replay"
        self.replay_name = name
        path = os.path.join(ROOT, config.DATA_DIR, GOLDEN_RUNS[name])
        meta, samples = load_golden(path)
        self.recorder.event("replay_start", {"name": name, "meta": meta})
        await self.broadcast({"type": "replay", "status": "started",
                              "name": name, "meta": meta, "speed": speed})

        async def run() -> None:
            try:
                for delay, s in timed_replay(samples, speed):
                    if delay > 0:
                        await asyncio.sleep(delay)
                    await self.process_sample(s, source="replay")
                await self.broadcast({"type": "replay", "status": "finished",
                                      "name": name})
                self.recorder.event("replay_end", {"name": name})
            except asyncio.CancelledError:
                pass

        self._replay_task = asyncio.create_task(run())


hub = Hub()
app = FastAPI(title="Sensor Arbiter")


@app.on_event("startup")
async def _startup() -> None:
    asyncio.create_task(arbiter.warmup())


@app.get("/")
async def index():
    return RedirectResponse("/dashboard/")


@app.get("/api/status")
async def status():
    return {
        "mode": hub.mode,
        "replay_name": hub.replay_name,
        "state": hub.monitor.state.value,
        "conflict_id": hub.monitor.conflict_id,
        "gemma_call_count": hub.monitor.gemma_call_count,
        "gemma_model": config.GEMMA_MODEL,
        "force_fallback": config.ARBITER_FORCE_FALLBACK,
        "bad_samples": hub.bad_samples,
        "dashboards": len(hub.dashboards),
        "session_log": hub.recorder.path,
        "scenarios": list(SCENARIOS),
        "golden_runs": list(GOLDEN_RUNS),
    }


@app.post("/api/inject/{scenario}")
async def inject(scenario: str):
    if scenario not in SCENARIOS:
        return JSONResponse({"error": f"unknown scenario {scenario}"}, 404)
    if hub.mode == "replay":
        return JSONResponse({"error": "stop replay before injecting live"}, 409)
    if hub.last_t <= 0.0:
        return JSONResponse({"error": "no live stream yet; connect the phone "
                                      "or use a replay"}, 409)
    hub.injector.trigger(scenario, hub.last_t)
    hub.recorder.event("inject", {"scenario": scenario, "t": hub.last_t})
    await hub.broadcast({"type": "inject", "scenario": scenario, "t": hub.last_t})
    return {"ok": True, "scenario": scenario, "t": hub.last_t}


@app.post("/api/reset")
async def reset():
    await hub.reset()
    return {"ok": True}


@app.post("/api/replay/{name}")
async def replay(name: str, speed: float = 1.0):
    if name not in GOLDEN_RUNS:
        return JSONResponse({"error": f"unknown golden run {name}"}, 404)
    await hub.start_replay(name, max(0.1, min(speed, 20.0)))
    return {"ok": True, "name": name, "speed": speed}


@app.websocket("/ws/phone")
async def ws_phone(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            raw = await ws.receive_text()
            try:
                s = PhoneSample.model_validate_json(raw)
            except Exception:
                hub.bad_samples += 1  # malformed input never crashes ingest
                continue
            # never accept injection metadata from the network boundary
            s.injected = None
            s.clean_gyro_mag = s.clean_flow_mag = s.clean_flow_confidence = None
            await hub.process_sample(s, source="live")
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/dashboard")
async def ws_dashboard(ws: WebSocket):
    await ws.accept()
    hub.dashboards.add(ws)
    hello = {"type": "hello", "mode": hub.mode, "model": config.GEMMA_MODEL,
             "scenarios": list(SCENARIOS), "golden_runs": list(GOLDEN_RUNS),
             "state": hub.monitor.state.value}
    if hub.last_decision is not None:
        hello["last_decision"] = hub.last_decision.model_dump()
    await ws.send_text(json.dumps(hello))
    try:
        while True:
            await ws.receive_text()  # keepalive/pings; content ignored
    except WebSocketDisconnect:
        hub.dashboards.discard(ws)


@app.websocket("/ws/preview/phone")
async def ws_preview_phone(ws: WebSocket):
    """Relay low-rate JPEG previews separately from flight telemetry.

    Preview traffic is deliberately lossy and isolated: a slow or absent
    dashboard can never delay phone samples or arbitration. Replays suppress
    live frames so recorded input is never presented as a live camera feed.
    """
    await ws.accept()
    try:
        while True:
            frame = await ws.receive_bytes()
            if hub.mode != "live" or len(frame) > 100_000:
                continue
            dead = []
            for dashboard in hub.preview_dashboards:
                try:
                    await dashboard.send_bytes(frame)
                except Exception:
                    dead.append(dashboard)
            for dashboard in dead:
                hub.preview_dashboards.discard(dashboard)
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/preview/dashboard")
async def ws_preview_dashboard(ws: WebSocket):
    await ws.accept()
    hub.preview_dashboards.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.preview_dashboards.discard(ws)


# static AFTER routes so /api and /ws take precedence
app.mount("/dashboard", StaticFiles(directory=os.path.join(ROOT, "dashboard"),
                                    html=True), name="dashboard")
app.mount("/phone", StaticFiles(directory=os.path.join(ROOT, "phone"),
                                html=True), name="phone")
