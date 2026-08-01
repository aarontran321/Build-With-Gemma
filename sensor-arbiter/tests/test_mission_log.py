"""Mission log and report tests.

Covers the three properties that make the log trustworthy as an audit
artifact: it never carries frame-rate noise, its identifiers survive a
reset without collision, and a report always agrees with the log it was
built from. Plus the printable renderer's escaping, since verdict text is
model-generated and lands in HTML.
"""

import pytest

from server import config, report_html
from server.mission_log import MissionLog
from tests.test_guardrail import evidence  # shared Evidence fixture builder


STATUS = {"mode": "live", "replay_name": None, "gemma_model": "gemma4:e4b",
          "force_fallback": False, "session_log": "sessions/test.jsonl"}
DESCENT = {"naive": {"alt": 0.0, "est_alt": -400.0, "rate": 160.0,
                     "phase": "LANDED", "outcome": "CRASH", "impact_speed": 160.0},
           "guarded": {"alt": 0.0, "est_alt": 5.0, "rate": 12.0,
                       "phase": "LANDED", "outcome": "SAFE", "impact_speed": 12.0}}


def verdict_dict(decision="switch_to_camera", fault="gyro_saturation",
                 trusted="camera", faulty="gyro", confidence=0.88):
    return {"fault_class": fault, "faulty_sensor": faulty,
            "trusted_sensor": trusted, "confidence": confidence,
            "evidence": ["gyro pinned at 34 rad/s", "camera trend healthy"],
            "alternative_hypothesis": "camera drift, ruled out by steady quality",
            "recommended_action": "use camera-derived attitude",
            "decision": decision}


def final_dict(conflict_id=1, source="gemma", overrode=False, reason=None,
               **kw):
    return {"conflict_id": conflict_id, "verdict": verdict_dict(**kw),
            "source": source, "guardrail_overrode": overrode,
            "override_reason": reason, "arbitration_latency_s": 2.4}


def decided_log(proposed_source="gemma", note="", **kw) -> MissionLog:
    """A log with one conflict opened and decided — the common fixture."""
    log = MissionLog()
    log.add("session", "server started")
    rec = log.open_conflict(1, evidence(gyro_rail_score=0.99, gyro_saturated=True,
                                        gyro_status="railed").model_dump(),
                            stream_t=10.0, trigger="gyro_saturation",
                            mode="live", replay_name=None)
    log.close_conflict(1, proposed=verdict_dict(), final=final_dict(**kw),
                       note=note, latency_s=2.4, descent=DESCENT,
                       proposed_source=proposed_source)
    return log, rec


# ---------------- event log ----------------

def test_seq_is_monotonic_and_since_filters_without_duplicates():
    log = MissionLog()
    for i in range(5):
        log.add("session", f"event {i}")
    first = log.recent()
    assert [e["seq"] for e in first] == [1, 2, 3, 4, 5]
    # a client holding seq 3 gets exactly the two it has not seen
    assert [e["seq"] for e in log.recent(since_seq=3)] == [4, 5]
    assert log.recent(since_seq=99) == []


def test_events_carry_both_clocks():
    log = MissionLog()
    e = log.add("decision", "decided", stream_t=12.5).as_dict()
    assert e["clock"].count(":") == 2, "wall clock is HH:MM:SS.mmm"
    assert e["mission_clock"].startswith("T+")
    assert e["stream_t"] == 12.5
    assert e["kind_label"] == "DECISION"


def test_ring_buffer_caps_memory_and_reports_what_it_dropped():
    log = MissionLog(max_events=10)
    for i in range(25):
        log.add("session", f"event {i}")
    assert len(log.events) == 10
    assert log.dropped == 15, "aged-out events are counted, not silently lost"
    # the survivors are the most recent ones
    assert [e["seq"] for e in log.recent()][0] == 16


def test_unknown_severity_falls_back_to_info():
    log = MissionLog()
    assert log.add("session", "x", severity="catastrophic").severity == "info"


# ---------------- conflict identity across resets ----------------

def test_reset_keeps_old_reports_and_does_not_reuse_report_ids():
    log = MissionLog()
    first = log.open_conflict(1, evidence().model_dump(), stream_t=1.0,
                              trigger=None, mode="live", replay_name=None)
    log.close_conflict(1, proposed=verdict_dict(), final=final_dict(),
                       note="", latency_s=1.0, descent=DESCENT)

    log.reset()  # monitor restarts numbering at 1
    second = log.open_conflict(1, evidence().model_dump(), stream_t=2.0,
                               trigger=None, mode="live", replay_name=None)

    assert first.report_id != second.report_id, "a reset must not overwrite a report"
    assert log.build_report(first.report_id, descent=DESCENT, status=STATUS) is not None
    assert log.build_report(second.report_id, descent=DESCENT, status=STATUS) is not None
    assert second.run == 2
    # Both records now share conflict number 1, so BOTH gain the run suffix —
    # a label that is ambiguous is never shown unqualified.
    assert "run 2" in second.label
    assert "run 1" in first.label


def test_a_reset_alone_does_not_clutter_labels_with_run_numbers():
    """Every replay resets, so the ordinary demo path must stay clean."""
    log = MissionLog()
    log.reset()
    rec = log.open_conflict(1, evidence().model_dump(), stream_t=1.0, trigger=None,
                            mode="replay", replay_name="gyro_saturation")
    assert rec.run == 2
    assert rec.label == "conflict #1", "no collision, so no run suffix"


def test_decision_after_reset_does_not_attach_to_a_stale_conflict():
    log = MissionLog()
    log.open_conflict(1, evidence().model_dump(), stream_t=1.0, trigger=None,
                      mode="live", replay_name=None)
    log.reset()
    # an in-flight arbitration landing after the reset has no open record
    assert log.close_conflict(1, proposed=verdict_dict(), final=final_dict(),
                              note="", latency_s=1.0, descent=DESCENT) is None


def test_single_run_label_is_not_cluttered_with_a_run_number():
    log = MissionLog()
    rec = log.open_conflict(3, evidence().model_dump(), stream_t=1.0, trigger=None,
                            mode="live", replay_name=None)
    assert rec.label == "conflict #3"


# ---------------- report assembly ----------------

def test_conflict_report_has_the_full_why_chain_in_pipeline_order():
    log, rec = decided_log()
    r = log.build_report(rec.report_id, descent=DESCENT, status=STATUS)
    stages = [s["stage"] for s in r["rationale"]]
    assert stages == ["1 · DETECTION", "2 · EVIDENCE", "3 · DIAGNOSIS",
                      "4 · VALIDATION", "5 · CONSEQUENCE"]
    assert all(s["finding"] and s["action"] for s in r["rationale"]), \
        "every stage states what it saw AND what it therefore did"


def test_report_records_the_evidence_the_arbiter_actually_received():
    log, rec = decided_log()
    r = log.build_report(rec.report_id, descent=DESCENT, status=STATUS)
    assert r["evidence"]["gyro_rail_score"] == 0.99
    assert r["evidence"]["gyro_status"] == "railed"
    assert r["trigger"] == "gyro_saturation", "synthetic origin is disclosed"


def test_guardrail_override_is_called_out_in_report_and_rationale():
    log, rec = decided_log(source="guardrail_override", overrode=True,
                           reason="proposed trusting a gyro pinned at rail",
                           decision="trust_neither_enter_caution",
                           trusted="none", faulty="both")
    r = log.build_report(rec.report_id, descent=DESCENT, status=STATUS)
    assert r["guardrail_overrode"] is True
    assert "pinned at rail" in r["override_reason"]
    validation = [s for s in r["rationale"] if s["stage"].startswith("4")][0]
    assert "INVARIANT VIOLATED" in validation["finding"]
    assert "REJECTED" in validation["action"]
    assert "REJECTED" in r["summary"]


def test_passing_guardrail_says_so_rather_than_staying_silent():
    log, rec = decided_log()
    r = log.build_report(rec.report_id, descent=DESCENT, status=STATUS)
    validation = [s for s in r["rationale"] if s["stage"].startswith("4")][0]
    assert "invariants held" in validation["finding"]
    assert "accepted unchanged" in validation["action"]


def test_fallback_source_explains_why_the_model_was_not_used():
    log, rec = decided_log(proposed_source="fallback", source="fallback",
                           note="gemma timeout after 15.0s")
    r = log.build_report(rec.report_id, descent=DESCENT, status=STATUS)
    diagnosis = [s for s in r["rationale"] if s["stage"].startswith("3")][0]
    assert "fallback" in diagnosis["actor"].lower()
    assert "timeout" in diagnosis["finding"]


def test_the_guardrail_is_never_described_as_having_diagnosed_anything():
    """An override replaces final['source'] with 'guardrail_override'. The
    report must still credit whoever actually classified the fault — the
    guardrail validates and vetoes, it never diagnoses."""
    log, rec = decided_log(proposed_source="fallback", source="guardrail_override",
                           overrode=True, reason="proposed trusting a railed gyro",
                           decision="trust_neither_enter_caution", trusted="none")
    r = log.build_report(rec.report_id, descent=DESCENT, status=STATUS)

    assert "guardrail classified" not in r["summary"]
    assert "fallback classifier classified the fault" in r["summary"]
    assert "REJECTED that proposal" in r["summary"]

    diagnosis = [s for s in r["rationale"] if s["stage"].startswith("3")][0]
    assert "fallback" in diagnosis["actor"].lower(), \
        "an override must not re-credit the diagnosis to Gemma"
    assert config.GEMMA_MODEL not in diagnosis["actor"]


def test_summary_reports_the_proposal_then_what_replaced_it():
    log, rec = decided_log(source="guardrail_override", overrode=True,
                           reason="proposed trusting a railed gyro",
                           decision="trust_neither_enter_caution", trusted="none")
    summary = log.build_report(rec.report_id, descent=DESCENT, status=STATUS)["summary"]
    # the PROPOSED decision is what the arbiter said, not the substituted one
    assert "proposed switch to camera" in summary
    assert "trust neither enter caution" in summary


def test_undecided_conflict_still_reports_without_pretending_to_a_verdict():
    log = MissionLog()
    rec = log.open_conflict(1, evidence().model_dump(), stream_t=1.0, trigger=None,
                            mode="live", replay_name=None)
    r = log.build_report(rec.report_id, descent=DESCENT, status=STATUS)
    assert r["outcome_word"] == "PENDING"
    assert "had not returned" in r["summary"]


def test_conflict_timeline_keeps_its_own_events_and_drops_other_conflicts():
    log = MissionLog()
    a = log.open_conflict(1, evidence().model_dump(), stream_t=1.0, trigger=None,
                          mode="live", replay_name=None)
    log.add("transition", "conflict A transition", conflict_id=1)
    b = log.open_conflict(2, evidence().model_dump(), stream_t=2.0, trigger=None,
                          mode="live", replay_name=None)
    log.add("transition", "conflict B transition", conflict_id=2)
    log.add("inject", "session-wide injection")

    titles = [e["title"] for e in
              log.build_report(a.report_id, descent=DESCENT, status=STATUS)["timeline"]]
    assert "conflict A transition" in titles
    assert "conflict B transition" not in titles
    assert "session-wide injection" in titles, \
        "session context stays: what else was happening is part of why"
    assert b.report_id != a.report_id


def test_session_report_aggregates_every_conflict():
    log, _ = decided_log()
    log.injections.append({"scenario": "gyro_saturation", "t": 10.0, "wall": 0.0})
    r = log.build_report("session", descent=DESCENT, status=STATUS)
    assert r["stats"]["conflicts"] == 1
    assert r["stats"]["decided"] == 1
    assert r["stats"]["by_source"] == {"gemma": 1}
    assert len(r["conflicts"]) == 1
    assert r["conflicts"][0]["decision"] == "switch_to_camera"


def test_latest_resolves_to_the_most_recent_decision():
    log, rec = decided_log()
    assert log.latest_report_id() == rec.report_id
    assert log.build_report("latest", descent=DESCENT,
                            status=STATUS)["report_id"] == rec.report_id


def test_latest_on_an_empty_session_is_the_session_report():
    assert MissionLog().latest_report_id() == "session"


def test_unknown_report_ids_return_none_rather_than_raising():
    log = MissionLog()
    for bad in ("conflict-999", "conflict-abc", "nonsense", ""):
        assert log.build_report(bad, descent=DESCENT, status=STATUS) is None


def test_report_states_the_thresholds_that_governed_the_run():
    log, rec = decided_log()
    r = log.build_report(rec.report_id, descent=DESCENT, status=STATUS)
    values = {t["name"]: t["value"] for t in r["thresholds"]}
    assert values["divergence threshold (arm)"] == config.DIVERGENCE_THRESHOLD
    assert values["candidate persistence"] == config.CANDIDATE_PERSISTENCE_S


# ---------------- printable renderer ----------------

def test_rendered_report_is_self_contained_and_printable():
    log, rec = decided_log()
    html = report_html.render(
        log.build_report(rec.report_id, descent=DESCENT, status=STATUS))
    assert html.startswith("<!DOCTYPE html>")
    assert "@media print" in html and "@page" in html
    # OFFLINE RULE: nothing may be fetched from anywhere
    for scheme in ("http://", "https://", "//cdn", "<link"):
        assert scheme not in html, f"report must not reference {scheme}"


def test_rendered_report_shows_the_decision_and_the_consequence():
    log, rec = decided_log()
    html = report_html.render(
        log.build_report(rec.report_id, descent=DESCENT, status=STATUS))
    assert "switch_to_camera" in html
    assert "CRASH" in html and "SAFE" in html
    assert "Why this decision" in html
    assert "Full event timeline" in html


def test_renderer_escapes_model_generated_text():
    """Verdict prose comes from an LLM and lands in HTML — it must be data,
    never markup."""
    log = MissionLog()
    rec = log.open_conflict(1, evidence().model_dump(), stream_t=1.0, trigger=None,
                            mode="live", replay_name=None)
    nasty = verdict_dict()
    nasty["evidence"] = ["<script>alert('xss')</script>"]
    nasty["alternative_hypothesis"] = "a < b && c > d"
    final = final_dict()
    final["verdict"] = nasty
    log.close_conflict(1, proposed=nasty, final=final, note="",
                       latency_s=1.0, descent=DESCENT)

    html = report_html.render(
        log.build_report(rec.report_id, descent=DESCENT, status=STATUS))
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
    assert "a &lt; b &amp;&amp; c &gt; d" in html


def test_session_report_renders_with_no_conflicts_at_all():
    log = MissionLog()
    html = report_html.render(
        log.build_report("session", descent=DESCENT, status=STATUS))
    assert "No conflicts in this session." in html


@pytest.mark.parametrize("report_id", ["session", "conflict-1"])
def test_every_report_kind_renders(report_id):
    log, _ = decided_log()
    html = report_html.render(
        log.build_report(report_id, descent=DESCENT, status=STATUS))
    assert f"/api/report/{report_id}.pdf" in html, "PDF export is offered"
    assert "Print this page" in html
    assert "not a flight-accurate" in html, "the honesty disclaimer is always printed"
