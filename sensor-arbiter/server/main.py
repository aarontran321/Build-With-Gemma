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
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                               Response)
from fastapi.staticfiles import StaticFiles

from . import arbiter, config, guardrail, narrator, report_html, report_pdf
from .descent import DualDescent
from .inject import SCENARIOS, FaultInjector
from .mission_log import MissionLog
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
        self.log = MissionLog()
        self.dashboards: Set[WebSocket] = set()
        self.preview_dashboards: Set[WebSocket] = set()
        self.mode = "live"          # "live" | "replay"
        self.replay_name: Optional[str] = None
        self._replay_task: Optional[asyncio.Task] = None
        self.last_t: float = 0.0
        self.bad_samples = 0
        self.last_decision: Optional[FinalDecision] = None
        self.last_evidence: Optional[Evidence] = None
        # Descent story beats are logged on CHANGE only — the sim reports
        # phase every frame, and the mission log must never carry frame-rate
        # traffic.
        self._descent_phase = {"guarded": "CHUTE"}
        self._despin_logged = False
        self._stream_live = False
        # Altitude is an OPTIONAL third sensor and defaults off; see
        # _gate_altitude and config.ALTITUDE_ENABLED for why.
        self.altitude_enabled = config.ALTITUDE_ENABLED
        self.altitude_rejected = 0
        # Rate-limit state for NORMAL <-> CANDIDATE blips (see _log_transition).
        self._blip_window_start = -1e9
        self._blip_suppressed = 0

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

    async def note(self, kind: str, title: str, *, severity: str = "info",
                   detail: Optional[dict] = None, conflict_id: Optional[int] = None,
                   stream_t: Optional[float] = None) -> None:
        """Record one significant event and push it to every dashboard.

        Single funnel on purpose: anything worth logging is worth showing
        live, so the panel on screen and the printed report can never drift
        apart.
        """
        ev = self.log.add(kind, title, severity=severity, detail=detail,
                          conflict_id=conflict_id, stream_t=stream_t)
        await self.broadcast({"type": "log", "event": ev.as_dict()})

    # ---------------- pipeline ----------------

    async def process_sample(self, s: PhoneSample, source: str) -> None:
        if source == "live":
            if self.mode == "replay":
                return  # replay owns the timeline; drop live samples
            s = self.injector.apply(s)
        self.last_t = s.t
        self._gate_altitude(s)

        if not self._stream_live:
            self._stream_live = True
            await self.note("stream", f"Sensor stream acquired ({self.mode})",
                            severity="success", stream_t=s.t,
                            detail={"source": source, "mode": self.mode})

        frame = self.monitor.ingest(s)
        self.recorder.event("sample", s.model_dump(exclude_none=True))
        for tr in frame.transitions:
            self.recorder.event("transition", tr.model_dump())
            await self.broadcast({"type": "transition", **tr.model_dump()})
            await self._log_transition(tr, frame)
        if frame.wake_evidence is not None:
            ev = frame.wake_evidence
            self.recorder.event("evidence", ev.model_dump())
            rec = self.log.open_conflict(
                ev.conflict_id, ev.model_dump(), stream_t=s.t,
                trigger=s.injected, mode=self.mode, replay_name=self.replay_name)
            await self.broadcast({"type": "arbitration", "status": "started",
                                  "conflict_id": ev.conflict_id,
                                  "report_id": rec.report_id,
                                  "evidence": ev.model_dump()})
            await self.note(
                "arbitration",
                f"Arbiter woken for {rec.label} — divergence "
                f"{ev.normalized_rate_difference} sustained {ev.seconds_diverged}s",
                severity="critical", conflict_id=ev.conflict_id, stream_t=s.t,
                detail={"gyro_status": ev.gyro_status,
                        "camera_status": ev.camera_status,
                        "gyro_rail_score": ev.gyro_rail_score,
                        "flow_quality": ev.flow_quality,
                        "model": config.GEMMA_MODEL})
            # arbitration runs concurrently; sensor/descent stream never blocks
            asyncio.create_task(self._arbitrate(ev))

        desc = self.descent.step(s, frame)
        await self._log_descent_beats(desc, s.t)
        await self._log_attitude_beats(desc, s.t)
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
            "altitude_enabled": self.altitude_enabled,
            "altitude_m": s.altitude_m,
            "altitude_accuracy_m": s.altitude_accuracy_m,
            "descent": desc,
        })

    def _gate_altitude(self, s: PhoneSample) -> None:
        """Enforce the altitude toggle at the INGEST boundary.

        Stripping the fields here, before the monitor sees the sample, is the
        whole point of the toggle: with altitude off there is no downstream
        path by which it could reach the evidence, the arbiter, the report or
        the model. "Gemma ignores it" is a property of the data flow, not a
        promise in a prompt.

        A fix too coarse to mean anything at this scale is discarded on the
        same grounds — a +/-60 m altitude cannot inform a decision about a
        3700 m descent, and handing it over anyway would invite confident
        nonsense.
        """
        if not self.altitude_enabled:
            s.altitude_m = s.altitude_accuracy_m = None
            return
        acc = s.altitude_accuracy_m
        if acc is not None and acc > config.ALTITUDE_MAX_ACCURACY_M:
            self.altitude_rejected += 1
            s.altitude_m = s.altitude_accuracy_m = None

    async def _log_transition(self, tr, frame) -> None:
        """State-machine transitions, with the severity a reader expects:
        arming is critical, returning to NORMAL is a success.

        NORMAL <-> CANDIDATE round trips are rate-limited. Those are blips the
        persistence filter correctly rejected — no conflict, no arbiter call —
        and a noisy live stream emits several per second, which buries the
        arming/decision/recovery lines the log exists to show. The first blip
        in a quiet period is logged; the rest are counted and summarised.
        Transitions touching ACTIVE or RECOVERING are real conflict lifecycle
        events and always log immediately.
        """
        sev = {"ACTIVE": "critical", "CANDIDATE": "warn",
               "RECOVERING": "info", "NORMAL": "success"}.get(tr.to_state, "info")
        detail = {"divergence": round(frame.divergence, 3),
                  "gyro_status": frame.gyro_status,
                  "camera_status": frame.camera_status}

        is_blip = {tr.from_state, tr.to_state} == {"NORMAL", "CANDIDATE"}
        if not is_blip:
            # Real conflict lifecycle. Flush any pending blip summary first so
            # the log reads in order, then log this transition unconditionally.
            await self._flush_blip_summary(tr.t)
            await self.note(
                "transition", f"Conflict state {tr.from_state} → {tr.to_state}",
                severity=sev, conflict_id=tr.conflict_id, stream_t=tr.t,
                detail=detail)
            return

        if tr.t - self._blip_window_start >= config.MISSION_LOG_BLIP_QUIET_S:
            await self._flush_blip_summary(tr.t)
            self._blip_window_start = tr.t
            self._blip_suppressed = 0
            await self.note(
                "transition", f"Conflict state {tr.from_state} → {tr.to_state}",
                severity=sev, conflict_id=tr.conflict_id, stream_t=tr.t,
                detail=detail)
        else:
            self._blip_suppressed += 1

    async def _flush_blip_summary(self, t: float) -> None:
        """Emit one line standing in for the blips suppressed since the last
        logged one, so the count is never silently lost."""
        n = self._blip_suppressed
        self._blip_suppressed = 0
        if n <= 0:
            return
        await self.note(
            "transition",
            f"+{n} further sub-threshold divergence blip{'s' if n > 1 else ''} "
            f"ignored (below the {config.CANDIDATE_PERSISTENCE_S}s persistence "
            f"threshold — no conflict armed)",
            severity="info", stream_t=t, detail={"suppressed_blips": n})

    async def _log_attitude_beats(self, desc: dict, t: float) -> None:
        """Log the moment a burn finishes and the vehicle is back under
        control. Commanding a de-spin is only half the story: the point of
        the manoeuvre is that the rate afterwards is zero, so that has to be
        an event in its own right rather than something the operator has to
        infer from a number that stopped changing.

        Change-triggered, like the descent beats — never per frame."""
        at = desc.get("attitude")
        if at is None:
            return
        done = at["despin_done"]
        if done == self._despin_logged:
            return
        self._despin_logged = done
        if not done:
            return
        settled = at["settled"]
        await self.note(
            "control",
            (f"De-spin complete — body rate {at['body_rate']:+.2f} rad/s"
             + (", spin nulled; no further thrust required" if settled else
                f", still outside the {config.DESPIN_DEADBAND_RAD_S} rad/s "
                f"deadband: a follow-up burn of {at['residual_burn_s']}s is "
                f"still required")),
            severity="success" if settled else "warn",
            stream_t=t, detail=dict(at))

    async def _log_descent_beats(self, desc: dict, t: float) -> None:
        """Log only when the vehicle changes phase — touchdown or impact.
        These are the beats the report's narrative is built from."""
        for who in ("guarded",):
            phase = desc[who]["phase"]
            if phase == self._descent_phase[who]:
                continue
            self._descent_phase[who] = phase
            p = desc[who]
            if phase == "LANDED":
                crashed = p["outcome"] == "CRASH"
                await self.note(
                    "descent",
                    f"{who.upper()} vehicle: {'IMPACT' if crashed else 'touchdown'} at "
                    f"{p['impact_speed']} m/s — {p['outcome']}",
                    severity="critical" if crashed else "success",
                    stream_t=t, detail=dict(p))

    async def _arbitrate(self, ev: Evidence) -> None:
        verdict, source, latency, note = await arbiter.arbitrate(ev)
        final_verdict, overrode, reason = guardrail.validate(verdict, ev)
        # Sizing the manoeuvre uses the FINAL verdict, not the proposed one:
        # if the guardrail moved trust to another sensor, the burn must be
        # judged against the sensor actually being flown.
        action, ctl_overrode, ctl_reason = guardrail.validate_control(
            final_verdict, ev)
        fd = FinalDecision(
            conflict_id=ev.conflict_id,
            verdict=final_verdict,
            source="guardrail_override" if overrode else source,
            control_action=action,
            control_overridden=ctl_overrode,
            control_override_reason=ctl_reason,
            guardrail_overrode=overrode,
            override_reason=reason,
            arbitration_latency_s=round(latency, 3),
        )
        self.last_decision = fd
        self.last_evidence = ev
        self.descent.apply_decision(fd, self.last_t)
        self.recorder.event("decision", {**fd.model_dump(), "note": note,
                                         "proposed": verdict.model_dump()})

        # Finalize the conflict record, then log the decision and (if it
        # fired) the override as separate beats — a guardrail rejection is
        # its own event in the timeline, not a footnote on the decision.
        rec = self.log.close_conflict(
            ev.conflict_id, proposed=verdict.model_dump(), final=fd.model_dump(),
            note=note, latency_s=latency, descent=self.descent.snapshot(),
            proposed_source=source)  # who diagnosed, before any override
        if note:
            await self.note("arbitration", f"Arbiter note: {note}", severity="warn",
                            conflict_id=ev.conflict_id)
        if overrode:
            await self.note(
                "guardrail",
                f"GUARDRAIL OVERRIDE — {reason}",
                severity="critical", conflict_id=ev.conflict_id,
                detail={"model_proposed": verdict.decision,
                        "model_trusted": verdict.trusted_sensor,
                        "replaced_with": final_verdict.decision,
                        "now_trusting": final_verdict.trusted_sensor})
        await self.note(
            "decision",
            f"Decision: {final_verdict.decision.replace('_', ' ')} — "
            f"{final_verdict.fault_class.replace('_', ' ')}, trust "
            f"{final_verdict.trusted_sensor} "
            f"({fd.source}, {fd.arbitration_latency_s}s)",
            severity="success" if not overrode else "warn",
            conflict_id=ev.conflict_id,
            detail={"confidence": final_verdict.confidence,
                    "faulty_sensor": final_verdict.faulty_sensor,
                    "source": fd.source})

        # Committing propellant is its own flight action, logged separately
        # from the diagnosis that motivated it — including the refusals, which
        # are the more interesting half.
        if ctl_overrode:
            await self.note(
                "control", f"MANOEUVRE REFUSED — {ctl_reason}",
                severity="critical", conflict_id=ev.conflict_id,
                detail={"model_proposed": final_verdict.proposed_manoeuvre,
                        "model_axis": final_verdict.manoeuvre_axis,
                        "executed": action.manoeuvre})
        elif action.manoeuvre == "hold_attitude":
            await self.note(
                "control", f"Attitude hold — {action.rationale}",
                severity="info", conflict_id=ev.conflict_id,
                detail=action.model_dump())
        else:
            await self.note(
                "control",
                f"DE-SPIN commanded: {action.burn_s}s {action.fire_direction} "
                f"about {action.axis} at {int(action.thrust_fraction * 100)}% "
                f"torque (measured {action.measured_rate_rad_s:+.2f} rad/s)",
                severity="warn", conflict_id=ev.conflict_id,
                detail=action.model_dump())

        await self.broadcast({"type": "decision", **fd.model_dump(),
                              "note": note,
                              "report_id": rec.report_id if rec else None,
                              "proposed": verdict.model_dump() if overrode else None})
        # A decision is exactly the moment a report becomes worth printing.
        if rec is not None:
            await self.broadcast({
                "type": "report_ready",
                "report_id": rec.report_id,
                "label": rec.label,
                "title": f"Decision report — {rec.label}",
                "url": f"/api/report/{rec.report_id}.html",
            })
            # Prose is written only now, once the decision is already out —
            # narration is an explanation, never part of the decision path.
            asyncio.create_task(self._narrate_conflict(rec.record_no))

    async def _narrate_conflict(self, record_no: int) -> None:
        """Have Gemma write this report's prose, in the background."""
        rec = self.log.conflicts.get(record_no)
        if rec is None:
            return
        report = self.build_report(rec.report_id)
        if report is None:
            return
        narrative, source, latency = await narrator.narrate(report)
        rec.narrative = narrative
        await self.note(
            "report",
            f"Report narrative written for {rec.label} by "
            f"{'Gemma (' + config.GEMMA_MODEL + ')' if source == 'gemma' else 'deterministic template'}"
            + (f" — {narrative.get('note')}" if narrative.get("note") else ""),
            severity="info" if source == "gemma" else "warn",
            conflict_id=rec.conflict_id,
            detail={"source": source, "latency_s": round(latency, 2)})
        await self.broadcast({
            "type": "narrative_ready",
            "report_id": rec.report_id,
            "label": rec.label,
            "source": source,
            "headline": narrative.get("headline"),
        })

    async def narrate_session_report(self) -> None:
        """Refresh the session report's prose if the session has moved on.
        Called on demand, because a session report is only read on demand."""
        if not self.log.session_narrative_stale():
            return
        report = self.build_report("session")
        if report is None:
            return
        narrative, _source, _lat = await narrator.narrate(report)
        self.log.set_session_narrative(narrative)

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
        self._descent_phase = {"guarded": "CHUTE"}
        self._despin_logged = False
        self._stream_live = False
        self._blip_window_start = -1e9
        self._blip_suppressed = 0
        self.recorder.event("reset", {"mode": mode, "wall_t": time.time()})
        # Past reports survive a reset — the operator may still want to print
        # what just happened — but conflict numbering starts over.
        self.log.reset()
        await self.note("session", f"Session reset — new run #{self.log.run} ({mode})",
                        severity="warn", detail={"mode": mode})
        await self.broadcast({"type": "reset", "mode": mode})

    async def start_replay(self, name: str, speed: float = 1.0) -> None:
        await self.reset(mode="replay")
        self.mode = "replay"
        self.replay_name = name
        path = os.path.join(ROOT, config.DATA_DIR, GOLDEN_RUNS[name])
        meta, samples = load_golden(path)
        self.recorder.event("replay_start", {"name": name, "meta": meta})
        await self.note("replay", f"Golden run replay started: {name}",
                        detail={"name": name, "speed": speed,
                                "samples": len(samples)})
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
                await self.note("replay", f"Golden run replay finished: {name}",
                                severity="success", detail={"name": name})
            except asyncio.CancelledError:
                pass
            finally:
                # Hand the timeline back to the phone. "replay wins over live"
                # must hold only while a replay is actually running: leaving
                # mode="replay" after it ends silently drops every live sample
                # in process_sample and every frame in the preview relay, so
                # the phone looks dead until someone presses RESET.
                if self.mode == "replay":
                    self.mode = "live"
                    self.replay_name = None
                    await self.broadcast({"type": "mode", "mode": "live"})

        self._replay_task = asyncio.create_task(run())

    # ---------------- reports ----------------

    def build_report(self, report_id: str) -> Optional[dict]:
        """Reports are assembled on demand from live state, never cached, so
        the descent outcome and session context in a printed report are the
        ones true at the moment it was generated."""
        return self.log.build_report(
            report_id,
            descent=self.descent.snapshot(),
            status={"mode": self.mode, "replay_name": self.replay_name,
                    "gemma_model": config.GEMMA_MODEL,
                    "force_fallback": config.ARBITER_FORCE_FALLBACK,
                    "session_log": self.recorder.path},
        )


hub = Hub()
app = FastAPI(title="Janus — Sensor Arbiter")


@app.on_event("startup")
async def _startup() -> None:
    hub.log.add("session", f"Server started — session {hub.log.session_id}",
                severity="success",
                detail={"model": config.GEMMA_MODEL,
                        "force_fallback": config.ARBITER_FORCE_FALLBACK,
                        "session_log": hub.recorder.path})
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
        "altitude_enabled": hub.altitude_enabled,
        "altitude_rejected": hub.altitude_rejected,
        "dashboards": len(hub.dashboards),
        "session_log": hub.recorder.path,
        "scenarios": list(SCENARIOS),
        "golden_runs": list(GOLDEN_RUNS),
        "session_id": hub.log.session_id,
        "run": hub.log.run,
        "log_events": len(hub.log.events),
        "reports": len(hub.log.report_index()),
        "latest_report": hub.log.latest_report_id(),
    }


@app.get("/api/log")
async def get_log(since: int = 0, limit: int = 300):
    """Event feed for the dashboard panel. `since` is the last seq the client
    already holds, so a reconnecting dashboard backfills without duplicates."""
    return {
        "session_id": hub.log.session_id,
        "started_wall": hub.log.started_wall,
        "run": hub.log.run,
        "dropped": hub.log.dropped,
        "events": hub.log.recent(since_seq=since, limit=limit),
    }


@app.get("/api/reports")
async def list_reports():
    return {"reports": hub.log.report_index(),
            "latest": hub.log.latest_report_id()}


async def _narrate_if_session(report_id: str) -> None:
    """Session prose is written lazily, on request, because a session report
    is only ever read on request — and re-written only when the session has
    actually changed. Conflict reports are narrated when their decision
    lands, so they never reach here."""
    resolved = hub.log.latest_report_id() if report_id == "latest" else report_id
    if resolved == "session":
        await hub.narrate_session_report()


@app.get("/api/report/{report_id}.pdf")
async def report_pdf_file(report_id: str):
    """The report as a real PDF, rendered server-side (server/report_pdf.py).

    A session report is re-narrated here if the session has moved on since
    the last time prose was written; conflict reports were already narrated
    in the background when their decision landed.
    """
    await _narrate_if_session(report_id)
    report = hub.build_report(report_id)
    if report is None:
        return JSONResponse({"error": f"unknown report {report_id}"}, 404)
    pdf = report_pdf.render(report)
    stamp = (report.get("header") or {}).get("session_id", "")
    filename = f"sensor-arbiter_{stamp}_{report_id}.pdf"
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/report/{report_id}.html")
async def report_page(report_id: str):
    """The report as a printable web page. The PDF button on it downloads
    the server-rendered PDF; Cmd/Ctrl-P still prints this page directly."""
    await _narrate_if_session(report_id)
    report = hub.build_report(report_id)
    if report is None:
        return HTMLResponse(
            "<h1>Report not found</h1><p>No such report in this session. "
            "<a href='/dashboard/'>Back to dashboard</a></p>", status_code=404)
    return HTMLResponse(report_html.render(report))


@app.get("/api/report/{report_id}.json")
async def report_json(report_id: str):
    await _narrate_if_session(report_id)
    report = hub.build_report(report_id)
    if report is None:
        return JSONResponse({"error": f"unknown report {report_id}"}, 404)
    return report


@app.post("/api/altitude/{state}")
async def altitude_toggle(state: str):
    """Turn the optional altitude input on or off at runtime.

    Off is not cosmetic: _gate_altitude strips the fields at ingest, so with
    altitude off there is no path by which it can reach the evidence, the
    arbiter or a report. Toggling it is therefore a demonstrable claim, not
    a display preference.
    """
    if state not in ("on", "off"):
        return JSONResponse({"error": "state must be 'on' or 'off'"}, 400)
    hub.altitude_enabled = state == "on"
    await hub.note(
        "sensor",
        f"GPS altitude input {'ENABLED' if hub.altitude_enabled else 'DISABLED'}"
        + ("" if hub.altitude_enabled else
           " — stripped at ingest; the arbiter cannot see it"),
        severity="warn")
    await hub.broadcast({"type": "altitude_mode",
                         "enabled": hub.altitude_enabled})
    return {"ok": True, "altitude_enabled": hub.altitude_enabled}


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
    hub.log.injections.append({"scenario": scenario, "t": hub.last_t,
                               "wall": time.time()})
    await hub.note("inject", f"Synthetic fault injected: {scenario}",
                   severity="warn", stream_t=hub.last_t,
                   detail={"scenario": scenario, "synthetic": True,
                           "duration_s": config.INJECTION_DURATION_S})
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
             "state": hub.monitor.state.value,
             "altitude_enabled": hub.altitude_enabled,
             "session_id": hub.log.session_id,
             # backfill so a dashboard opened mid-demo still shows the history
             "log": hub.log.recent(limit=config.MISSION_LOG_REPLAY_ON_CONNECT),
             "reports": hub.log.report_index()}
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
