"""Mission event log and derived decision reports.

The frame-rate JSONL in sessions/ is a machine audit trail: every sample,
~25 lines per second, write-only. It is the wrong artifact for a human who
walks up mid-demo and asks "what just happened, and why did it do that?"

This module adds the two things that were missing:

1. A MISSION LOG — every SIGNIFICANT event (state transitions, injections,
   arbitration wake, verdicts, guardrail overrides, descent story beats)
   stamped with wall-clock time and mission-elapsed time, with the sensor
   noise left out. Bounded ring buffer, so a long session cannot grow it
   without limit.

2. REPORTS — one per conflict, plus a whole-session report, assembled in
   the order a reviewer needs: what was detected, what evidence the arbiter
   was given, what it concluded and why, whether the guardrail let it
   stand, and what the consequence was. Rendered to a printable page by
   report_html.py.

Design rule: the log is the single source of truth. Reports are DERIVED
from it plus the structured records captured alongside, and are built at
request time from live state — so a report can never disagree with the log
it came from, and never shows a stale descent outcome.
"""

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

from . import config

# Severities drive dashboard colour and the printed report's emphasis.
# "critical" is reserved for things a reviewer must not miss: an armed
# conflict, a guardrail override, a crash.
SEVERITIES = ("info", "success", "warn", "critical")

KIND_LABELS = {
    "session": "SESSION",
    "inject": "FAULT INJECTION",
    "transition": "STATE MACHINE",
    "arbitration": "ARBITRATION",
    "decision": "DECISION",
    "guardrail": "GUARDRAIL",
    "descent": "DESCENT",
    "replay": "REPLAY",
    "stream": "TELEMETRY",
    "error": "ERROR",
}


def _clock(wall: float) -> str:
    """Local wall clock with milliseconds — what an operator reads off."""
    return time.strftime("%H:%M:%S", time.localtime(wall)) + f".{int(wall % 1 * 1000):03d}"


def _iso(wall: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(wall)) + \
        f".{int(wall % 1 * 1000):03d}" + time.strftime("%z", time.localtime(wall))


def _mmss(seconds: float) -> str:
    seconds = max(0.0, seconds)
    return f"T+{int(seconds // 60):02d}:{seconds % 60:05.2f}"


@dataclass
class LogEvent:
    """One significant thing that happened, with both clocks attached.

    `wall` answers "when in real time" (for the printed report and for
    correlating with anything outside this process). `mission_t` answers
    "how far into this run" (stable across timezones and replays).
    `stream_t` is the phone/replay sample clock, present only for events
    that were triggered by a sample.

    `conflict_id` is the monitor's number, which RESTARTS at 1 after a
    reset; `conflict_ref` is this log's own never-reused record number and
    is what report scoping filters on.
    """

    seq: int
    wall: float
    mission_t: float
    kind: str
    severity: str
    title: str
    detail: dict = field(default_factory=dict)
    conflict_id: Optional[int] = None
    conflict_ref: Optional[int] = None
    stream_t: Optional[float] = None

    def as_dict(self) -> dict:
        return {
            "seq": self.seq,
            "wall": round(self.wall, 3),
            "clock": _clock(self.wall),
            "iso": _iso(self.wall),
            "mission_t": round(self.mission_t, 2),
            "mission_clock": _mmss(self.mission_t),
            "kind": self.kind,
            "kind_label": KIND_LABELS.get(self.kind, self.kind.upper()),
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "conflict_id": self.conflict_id,
            "conflict_ref": self.conflict_ref,
            "report_id": f"conflict-{self.conflict_ref}" if self.conflict_ref else None,
            "stream_t": round(self.stream_t, 3) if self.stream_t is not None else None,
        }


@dataclass
class ConflictRecord:
    """Everything the report needs about one conflict, accumulated as the
    pipeline runs. Populated across three moments: the monitor arming the
    conflict, the arbiter returning, and the guardrail validating.

    `record_no` is the log's own identity for this conflict and is never
    reused; `conflict_id` is the monitor's, which restarts after a reset.
    Reports are addressed by record_no so a printed report always refers to
    exactly one event, even across several runs in one session.
    """

    record_no: int
    conflict_id: int
    run: int
    opened_wall: float
    opened_mission_t: float
    opened_stream_t: Optional[float] = None
    trigger: Optional[str] = None          # injected scenario active at wake
    evidence: Optional[dict] = None        # exactly what the arbiter was given
    proposed: Optional[dict] = None        # the model's own proposal
    # Who PROPOSED ("gemma" | "fallback"). Kept separately because the final
    # decision's source becomes "guardrail_override" when the proposal is
    # rejected, which would otherwise erase who did the diagnosing — and the
    # guardrail must never be described as having classified anything.
    proposed_source: str = ""
    final: Optional[dict] = None           # FinalDecision after the guardrail
    note: str = ""                         # timeout / error explanation
    decided_wall: Optional[float] = None
    decided_mission_t: Optional[float] = None
    latency_s: float = 0.0
    descent_at_decision: Optional[dict] = None
    mode: str = "live"
    replay_name: Optional[str] = None
    # Gemma-written prose for this conflict's report (server/narrator.py).
    # Generated once, after the decision is already broadcast, and cached
    # here: narration must never re-run per report fetch, and must never sit
    # between a decision and the dashboard.
    narrative: Optional[dict] = None
    # Set on every record sharing a conflict_id once a second one appears.
    # A reset alone does not make a label ambiguous — only a genuine
    # collision does — so the ordinary one-run demo stays uncluttered.
    ambiguous: bool = False

    @property
    def decided(self) -> bool:
        return self.final is not None

    @property
    def report_id(self) -> str:
        return f"conflict-{self.record_no}"

    @property
    def label(self) -> str:
        return (f"conflict #{self.conflict_id} (run {self.run})" if self.ambiguous
                else f"conflict #{self.conflict_id}")


class MissionLog:
    """Bounded, append-only event log plus per-conflict records."""

    def __init__(self, max_events: int = config.MISSION_LOG_MAX_EVENTS) -> None:
        self.started_wall = time.time()
        self.session_id = time.strftime("%Y%m%d_%H%M%S", time.localtime(self.started_wall))
        self.events: Deque[LogEvent] = deque(maxlen=max_events)
        self.conflicts: Dict[int, ConflictRecord] = {}   # keyed by record_no
        self.injections: List[dict] = []
        self.run = 1
        # Session-level prose is cached against a key describing what the
        # session contains, so it is re-narrated when the session actually
        # changes — not on every fetch, and not never.
        self.session_narrative: Optional[dict] = None
        self._session_narrative_key: Optional[tuple] = None
        self._seq = 0
        self._record_no = 0
        self._open_by_cid: Dict[int, int] = {}  # monitor cid -> record_no, this run
        self._dropped = 0  # events aged out of the ring, reported honestly

    # ---------------- writing ----------------

    def add(self, kind: str, title: str, *, severity: str = "info",
            detail: Optional[dict] = None, conflict_id: Optional[int] = None,
            stream_t: Optional[float] = None) -> LogEvent:
        wall = time.time()
        self._seq += 1
        if len(self.events) == self.events.maxlen:
            self._dropped += 1
        ev = LogEvent(
            seq=self._seq,
            wall=wall,
            mission_t=wall - self.started_wall,
            kind=kind,
            severity=severity if severity in SEVERITIES else "info",
            title=title,
            detail=detail or {},
            conflict_id=conflict_id,
            conflict_ref=self._open_by_cid.get(conflict_id) if conflict_id else None,
            stream_t=stream_t,
        )
        self.events.append(ev)
        return ev

    def open_conflict(self, conflict_id: int, evidence: dict, *,
                      stream_t: Optional[float], trigger: Optional[str],
                      mode: str, replay_name: Optional[str]) -> ConflictRecord:
        wall = time.time()
        self._record_no += 1
        rec = ConflictRecord(
            record_no=self._record_no,
            conflict_id=conflict_id,
            run=self.run,
            opened_wall=wall,
            opened_mission_t=wall - self.started_wall,
            opened_stream_t=stream_t,
            trigger=trigger,
            evidence=evidence,
            mode=mode,
            replay_name=replay_name,
        )
        self.conflicts[rec.record_no] = rec
        self._open_by_cid[conflict_id] = rec.record_no
        # A repeated conflict number (only possible across runs) makes every
        # record carrying that number ambiguous, including the earlier ones.
        clashes = [r for r in self.conflicts.values() if r.conflict_id == conflict_id]
        if len(clashes) > 1:
            for r in clashes:
                r.ambiguous = True
        return rec

    def close_conflict(self, conflict_id: int, *, proposed: dict, final: dict,
                       note: str, latency_s: float, descent: dict,
                       proposed_source: str = "") -> Optional[ConflictRecord]:
        record_no = self._open_by_cid.get(conflict_id)
        rec = self.conflicts.get(record_no) if record_no else None
        if rec is None:  # decision without a recorded wake (e.g. after a reset)
            return None
        wall = time.time()
        rec.proposed = proposed
        rec.proposed_source = proposed_source or final.get("source", "")
        rec.final = final
        rec.note = note
        rec.latency_s = latency_s
        rec.decided_wall = wall
        rec.decided_mission_t = wall - self.started_wall
        rec.descent_at_decision = descent
        return rec

    def reset(self) -> None:
        """New run within the same session. Past conflicts and their reports
        are KEPT — an operator who resets must still be able to print the
        report of what just happened — but the monitor's conflict numbering
        restarts, so the id mapping is dropped and new conflicts get fresh
        never-reused record numbers."""
        self.run += 1
        self._open_by_cid.clear()

    # ---------------- reading ----------------

    def recent(self, since_seq: int = 0, limit: int = 300) -> List[dict]:
        out = [e.as_dict() for e in self.events if e.seq > since_seq]
        return out[-limit:] if limit and len(out) > limit else out

    @property
    def dropped(self) -> int:
        return self._dropped

    def report_index(self) -> List[dict]:
        """Newest first: the session report, then one per decided conflict."""
        index = [{
            "report_id": "session",
            "kind": "session",
            "title": f"Session report — {self.session_id}",
            "subtitle": f"{len(self.conflicts)} conflict(s), "
                        f"{len(self.injections)} injection(s)",
            "wall": self.started_wall,
            "clock": _clock(self.started_wall),
        }]
        for record_no in sorted(self.conflicts, reverse=True):
            rec = self.conflicts[record_no]
            if rec.final is not None:
                verdict = rec.final["verdict"]
                subtitle = (f"{verdict['fault_class'].replace('_', ' ')} → "
                            f"{verdict['decision'].replace('_', ' ')} "
                            f"({rec.final['source']})")
            else:
                subtitle = "diagnosis in progress…"
            index.append({
                "report_id": rec.report_id,
                "kind": "conflict",
                "title": f"Decision report — {rec.label}",
                "subtitle": subtitle,
                "wall": rec.opened_wall,
                "clock": _clock(rec.opened_wall),
                "decided": rec.decided,
            })
        return index

    def session_narrative_key(self) -> tuple:
        """What the session-level prose depends on. If this changes, the
        cached narrative no longer describes the session."""
        return (len(self.conflicts),
                sum(1 for c in self.conflicts.values() if c.decided),
                len(self.injections))

    def session_narrative_stale(self) -> bool:
        return (self.session_narrative is None
                or self._session_narrative_key != self.session_narrative_key())

    def set_session_narrative(self, narrative: dict) -> None:
        self.session_narrative = narrative
        self._session_narrative_key = self.session_narrative_key()

    def latest_report_id(self) -> str:
        decided = [c for c in self.conflicts.values() if c.decided]
        if decided:
            return max(decided, key=lambda c: c.decided_wall or 0).report_id
        if self.conflicts:
            return max(self.conflicts.values(), key=lambda c: c.opened_wall).report_id
        return "session"

    # ---------------- report assembly ----------------

    def build_report(self, report_id: str, *, descent: dict,
                     status: dict) -> Optional[dict]:
        """Assemble a full report dict. Built fresh on every request so the
        descent consequence and session context are always current."""
        if report_id == "latest":
            report_id = self.latest_report_id()
        if report_id == "session":
            return self._session_report(descent=descent, status=status)
        if not report_id.startswith("conflict-"):
            return None
        try:
            record_no = int(report_id.split("-", 1)[1])
        except ValueError:
            return None
        rec = self.conflicts.get(record_no)
        if rec is None:
            return None
        return self._conflict_report(rec, descent=descent, status=status)

    def _header(self, status: dict) -> dict:
        now = time.time()
        return {
            "session_id": self.session_id,
            "session_started_iso": _iso(self.started_wall),
            "generated_iso": _iso(now),
            "generated_clock": _clock(now),
            "mission_elapsed": _mmss(now - self.started_wall),
            "mode": status.get("mode", "live"),
            "replay_name": status.get("replay_name"),
            "model": status.get("gemma_model", config.GEMMA_MODEL),
            "force_fallback": status.get("force_fallback", False),
            "session_log": status.get("session_log"),
            "events_recorded": len(self.events),
            "events_dropped": self._dropped,
        }

    def _timeline(self, conflict_ref: Optional[int] = None) -> List[dict]:
        """Events for the report. A conflict report keeps the events tied to
        that conflict plus the session-level context around it (injections,
        replay/reset), because "what else was going on" is part of why."""
        rows = []
        for e in self.events:
            if conflict_ref is not None:
                relevant = (e.conflict_ref == conflict_ref or
                            e.kind in ("inject", "replay", "session", "descent", "error"))
                if not relevant:
                    continue
            rows.append(e.as_dict())
        return rows

    def _conflict_report(self, rec: ConflictRecord, *, descent: dict,
                         status: dict) -> dict:
        ev = rec.evidence or {}
        final = rec.final
        verdict = (final or {}).get("verdict") or {}
        proposed = rec.proposed or {}
        overrode = bool((final or {}).get("guardrail_overrode"))

        if final is None:
            headline = f"{rec.label.capitalize()} — diagnosis in progress"
            outcome_word = "PENDING"
        else:
            headline = (f"{rec.label.capitalize()} — "
                        f"{verdict.get('fault_class', 'unknown').replace('_', ' ')}")
            outcome_word = verdict.get("decision", "—").replace("_", " ").upper()

        return {
            "report_id": rec.report_id,
            "kind": "conflict",
            "title": f"Decision Report — {rec.label.capitalize()}",
            "headline": headline,
            "outcome_word": outcome_word,
            "header": self._header(status),
            "summary": self._conflict_summary(rec, descent),
            "rationale": self._rationale(rec),
            "evidence": ev,
            "proposed": proposed,
            "verdict": verdict,
            "final": final,
            "guardrail_overrode": overrode,
            "override_reason": (final or {}).get("override_reason"),
            "source": (final or {}).get("source"),
            "proposed_source": rec.proposed_source,
            "narrative": rec.narrative,
            "note": rec.note,
            "latency_s": rec.latency_s,
            "trigger": rec.trigger,
            "opened_iso": _iso(rec.opened_wall),
            "opened_mission": _mmss(rec.opened_mission_t),
            "decided_iso": _iso(rec.decided_wall) if rec.decided_wall else None,
            "decided_mission": _mmss(rec.decided_mission_t) if rec.decided_mission_t else None,
            "descent_at_decision": rec.descent_at_decision,
            "descent_now": descent,
            "timeline": self._timeline(rec.record_no),
            "thresholds": self._thresholds(),
        }

    def _conflict_summary(self, rec: ConflictRecord, descent: dict) -> str:
        """One paragraph a reviewer can read without any other context."""
        ev = rec.evidence or {}
        opened = _mmss(rec.opened_mission_t)
        trigger = (f"a synthetic {rec.trigger.replace('_', ' ')} fault was active"
                   if rec.trigger else "no synthetic fault was active")
        base = (f"At {opened} the deterministic monitor measured a normalized "
                f"divergence of {ev.get('normalized_rate_difference', '—')} between the IMU "
                f"gyroscope and the camera-derived rotation proxy, sustained for "
                f"{ev.get('seconds_diverged', '—')} s — past the "
                f"{config.DIVERGENCE_THRESHOLD} threshold held for "
                f"{config.CANDIDATE_PERSISTENCE_S} s required to arm a conflict. "
                f"At that moment {trigger}. The conflict was escalated to ACTIVE "
                f"and the arbiter was woken once.")
        if rec.final is None:
            return base + " The diagnosis had not returned when this report was generated."

        # The diagnosis is always attributed to whoever PROPOSED it. The
        # guardrail validates and can veto; it never classifies a fault, and
        # the report must not imply otherwise.
        proposer = {"gemma": f"the local {config.GEMMA_MODEL} model",
                    "fallback": "the deterministic fallback classifier"
                    }.get(rec.proposed_source, "the arbiter")
        p = rec.proposed or rec.final["verdict"]
        base += (f" After {rec.latency_s:.2f} s, {proposer} classified the fault as "
                 f"{p['fault_class'].replace('_', ' ')}, named "
                 f"{p['faulty_sensor']} as the faulty sensor, and proposed "
                 f"{p['decision'].replace('_', ' ')} at "
                 f"{int(float(p['confidence']) * 100)}% confidence.")
        if rec.final.get("guardrail_overrode"):
            v = rec.final["verdict"]
            base += (f" The deterministic guardrail REJECTED that proposal — "
                     f"{rec.final.get('override_reason')} — and substituted the "
                     f"conservative decision {v['decision'].replace('_', ' ')} "
                     f"(trust {v['trusted_sensor']}), which became the flight decision.")
        else:
            base += (" The guardrail validated the proposal against all safety "
                     "invariants and let it stand as the flight decision.")
        guarded = descent.get("guarded", {})
        base += (f" Simulated consequence at report time: the guarded vehicle is "
                 f"{guarded.get('outcome', '—')}.")
        return base

    def _rationale(self, rec: ConflictRecord) -> List[dict]:
        """The 'why' chain, in pipeline order. Each step states what that
        stage saw and what it therefore did — this is the part a reviewer
        reads to decide whether they trust the decision."""
        ev = rec.evidence or {}
        steps: List[dict] = []

        steps.append({
            "stage": "1 · DETECTION",
            "actor": "Deterministic monitor (no model involved)",
            "finding": (f"Normalized divergence {ev.get('normalized_rate_difference', '—')} "
                        f"exceeded the {config.DIVERGENCE_THRESHOLD} threshold and stayed there for "
                        f"{ev.get('seconds_diverged', '—')} s (≥ {config.CANDIDATE_PERSISTENCE_S} s "
                        f"required). Trend correlation between the two streams was "
                        f"{ev.get('trend_correlation', '—')}."),
            "action": ("Conflict armed: CANDIDATE → ACTIVE. The arbiter is woken exactly "
                       "once for this conflict; all frame-rate math stays deterministic."),
        })

        steps.append({
            "stage": "2 · EVIDENCE",
            "actor": "Monitor → arbiter hand-off",
            "finding": (f"Gyro: {ev.get('gyro_status', '—')} at {ev.get('gyro_rate', '—')} rad/s, "
                        f"rail score {ev.get('gyro_rail_score', '—')}, flatline score "
                        f"{ev.get('gyro_flatline_score', '—')}, variance {ev.get('gyro_variance', '—')}. "
                        f"Camera: {ev.get('camera_status', '—')} at {ev.get('flow_rate', '—')} proxy units, "
                        f"quality {ev.get('flow_quality', '—')}, variance {ev.get('flow_variance', '—')}."),
            "action": (f"A compact {config.EVIDENCE_WINDOW_S} s window — the two trend arrays and the "
                       f"recent-agreement history — was the ONLY input given to the arbiter. "
                       f"No raw frames, no pixels, no history beyond this window."),
        })

        if rec.final is None:
            steps.append({
                "stage": "3 · DIAGNOSIS",
                "actor": "Arbiter",
                "finding": "Diagnosis had not returned when this report was generated.",
                "action": "Pending.",
            })
            return steps

        proposed = rec.proposed or {}
        # Attribute to whoever PROPOSED. rec.final["source"] is unusable here:
        # it reads "guardrail_override" once a veto fires, which would credit
        # the wrong component with the diagnosis.
        if rec.proposed_source == "fallback":
            actor = "Deterministic fallback classifier (server/fallback.py)"
            why_fallback = f" Reason the model was not used: {rec.note}." if rec.note else ""
        else:
            actor = f"Local Gemma model — {config.GEMMA_MODEL}, via Ollama, offline"
            why_fallback = ""
        prop_ev = proposed.get("evidence") or (rec.final["verdict"].get("evidence") or [])
        observations = ("  Stated observations: " + "; ".join(prop_ev)) if prop_ev else ""
        steps.append({
            "stage": "3 · DIAGNOSIS",
            "actor": actor,
            "finding": (f"Proposed {proposed.get('fault_class', '—')} with "
                        f"{proposed.get('faulty_sensor', '—')} faulty and "
                        f"{proposed.get('trusted_sensor', '—')} trusted, at "
                        f"{int(float(proposed.get('confidence', 0)) * 100)}% confidence, in "
                        f"{rec.latency_s:.2f} s.{why_fallback}{observations}"),
            "action": (f"Recommended action: {proposed.get('recommended_action', '—')}. "
                       f"Alternative hypothesis it ruled out: "
                       f"{proposed.get('alternative_hypothesis', '—')}"),
        })

        if rec.final.get("guardrail_overrode"):
            steps.append({
                "stage": "4 · VALIDATION",
                "actor": "Deterministic guardrail (safety invariants only)",
                "finding": f"INVARIANT VIOLATED — {rec.final.get('override_reason')}",
                "action": (f"The proposal was REJECTED and replaced with the conservative "
                           f"decision {rec.final['verdict']['decision'].replace('_', ' ')} "
                           f"(trust {rec.final['verdict']['trusted_sensor']}). The guardrail "
                           f"does not re-diagnose; it only steers to the safe direction the "
                           f"violated invariant already implies."),
            })
        else:
            steps.append({
                "stage": "4 · VALIDATION",
                "actor": "Deterministic guardrail (safety invariants only)",
                "finding": ("All safety invariants held: the diagnosis is internally consistent, "
                            "trusts no unavailable sensor, trusts no gyro pinned above rail score "
                            f"{config.RAIL_SCORE_TRUST_LIMIT}, trusts no camera below quality "
                            f"{config.FLOW_CONFIDENCE_FLOOR}, and takes no irreversible action "
                            "with both sensors unreliable."),
                "action": "The proposed diagnosis was accepted unchanged as the flight decision.",
            })

        d = rec.descent_at_decision or {}
        guarded = d.get("guarded", {})
        steps.append({
            "stage": "5 · CONSEQUENCE",
            "actor": "Guarded descent simulation (accelerated, simulated)",
            "finding": (f"At the moment of decision the guarded vehicle was at "
                        f"{guarded.get('alt', '—')} m with its own estimate reading "
                        f"{guarded.get('est_alt', '—')} m ({guarded.get('phase', '—')}), "
                        f"holding a conservative attitude freeze while arbitration ran."),
            "action": (f"The guarded vehicle followed the validated decision. Left unarbitrated, "
                       f"this fault class is the Schiaparelli signature: a corrupted rotational "
                       f"rate drives the altitude estimate negative, which cuts the parachute "
                       f"early."),
        })
        return steps

    def _session_report(self, *, descent: dict, status: dict) -> dict:
        decided = [c for c in self.conflicts.values() if c.decided]
        overrides = [c for c in decided if c.final.get("guardrail_overrode")]
        by_source: Dict[str, int] = {}
        for c in decided:
            by_source[c.final["source"]] = by_source.get(c.final["source"], 0) + 1
        latencies = [c.latency_s for c in decided if c.latency_s]

        conflicts = []
        for record_no in sorted(self.conflicts):
            rec = self.conflicts[record_no]
            v = (rec.final or {}).get("verdict") or {}
            conflicts.append({
                "conflict_id": rec.conflict_id,
                "label": rec.label,
                "report_id": rec.report_id,
                "opened": _mmss(rec.opened_mission_t),
                "opened_iso": _iso(rec.opened_wall),
                "trigger": rec.trigger,
                "fault_class": v.get("fault_class"),
                "decision": v.get("decision"),
                "trusted": v.get("trusted_sensor"),
                "source": (rec.final or {}).get("source"),
                "overrode": bool((rec.final or {}).get("guardrail_overrode")),
                "latency_s": rec.latency_s,
                "decided": rec.decided,
            })

        summary = (
            f"This session opened {len(self.conflicts)} conflict(s) and reached "
            f"{len(decided)} validated decision(s). "
            f"{len(self.injections)} synthetic fault(s) were injected. "
            f"The safety guardrail overrode the proposed diagnosis "
            f"{len(overrides)} time(s). "
        )
        if latencies:
            summary += (f"Arbitration latency ranged {min(latencies):.2f}–{max(latencies):.2f} s "
                        f"(mean {sum(latencies) / len(latencies):.2f} s). ")
        summary += (f"Decision sources: "
                    f"{', '.join(f'{k} ×{v}' for k, v in by_source.items()) or 'none yet'}. "
                    f"Simulated outcome at report time — guarded vehicle: "
                    f"{descent.get('guarded', {}).get('outcome', '—')}.")

        return {
            "report_id": "session",
            "kind": "session",
            "title": f"Mission Session Report — {self.session_id}",
            "headline": f"Session {self.session_id}",
            "outcome_word": f"{len(decided)} DECISION(S)",
            "header": self._header(status),
            "summary": summary,
            "narrative": self.session_narrative,
            "conflicts": conflicts,
            "injections": list(self.injections),
            "descent_now": descent,
            "timeline": self._timeline(),
            "thresholds": self._thresholds(),
            "stats": {
                "conflicts": len(self.conflicts),
                "decided": len(decided),
                "overrides": len(overrides),
                "injections": len(self.injections),
                "by_source": by_source,
                "latency_min": round(min(latencies), 3) if latencies else None,
                "latency_max": round(max(latencies), 3) if latencies else None,
                "latency_mean": round(sum(latencies) / len(latencies), 3) if latencies else None,
            },
        }

    @staticmethod
    def _thresholds() -> List[dict]:
        """The constants that actually governed this run, printed with the
        report so a decision can be re-checked against its own tuning."""
        return [
            {"name": "divergence threshold (arm)", "value": config.DIVERGENCE_THRESHOLD,
             "unit": "normalized"},
            {"name": "divergence recovery", "value": config.DIVERGENCE_RECOVERY,
             "unit": "normalized"},
            {"name": "candidate persistence", "value": config.CANDIDATE_PERSISTENCE_S, "unit": "s"},
            {"name": "recovery agreement", "value": config.RECOVERY_AGREEMENT_S, "unit": "s"},
            {"name": "conflict cooldown", "value": config.CONFLICT_COOLDOWN_S, "unit": "s"},
            {"name": "evidence window", "value": config.EVIDENCE_WINDOW_S, "unit": "s"},
            {"name": "guardrail rail-score trust limit", "value": config.RAIL_SCORE_TRUST_LIMIT,
             "unit": "score"},
            {"name": "guardrail flow-confidence floor", "value": config.FLOW_CONFIDENCE_FLOOR,
             "unit": "quality"},
            {"name": "arbiter timeout", "value": config.GEMMA_TIMEOUT_S, "unit": "s"},
            {"name": "model", "value": config.GEMMA_MODEL, "unit": ""},
        ]
