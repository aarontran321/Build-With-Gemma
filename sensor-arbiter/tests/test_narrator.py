"""Gemma report-narrator tests.

The narrator is allowed to write freely — it makes no decision — but it is
NOT allowed to introduce a fact. These tests pin the thing that makes that
safe: the fact sheet is complete, invented figures are caught, and a
missing or misbehaving model always degrades to deterministic text rather
than to a wrong or empty report. No test here contacts Ollama.
"""

import asyncio

import pytest

from server import config, narrator
from server.narrator import ReportNarrative
from tests.test_mission_log import DESCENT, STATUS, decided_log


def narrative(**kw) -> ReportNarrative:
    base = dict(headline="Gyro saturation detected",
                summary="The gyroscope railed and the camera was trusted.",
                what_happened=["The gyroscope pinned at its rail value."],
                why=["The camera remained healthy, so it was trusted."],
                reviewer_note="Simulated descent; not a real vehicle.")
    base.update(kw)
    return ReportNarrative(**base)


def conflict_report(**kw):
    log, rec = decided_log(**kw)
    return log.build_report(rec.report_id, descent=DESCENT, status=STATUS)


# ---------------- fact sheet ----------------

def test_fact_sheet_carries_every_figure_the_prose_may_need():
    sheet = narrator.build_fact_sheet(conflict_report())
    for expected in ("0.99",          # gyro rail score from the evidence
                     "switch_to_camera",
                     "gyro_saturation",
                     "SAFE",           # guarded descent outcome
                     "12.0"):          # guarded impact speed
        assert expected in sheet, f"fact sheet is missing {expected}"


def test_fact_sheet_names_the_proposer_and_never_credits_the_guardrail():
    sheet = narrator.build_fact_sheet(
        conflict_report(proposed_source="fallback", source="guardrail_override",
                        overrode=True, reason="proposed trusting a railed gyro",
                        decision="trust_neither_enter_caution", trusted="none"))
    assert "proposed by: the deterministic fallback classifier" in sheet
    assert "it never diagnoses" in sheet, \
        "the guardrail's role must be stated so the model cannot misattribute it"
    assert "the guardrail REJECTED the proposal" in sheet


def test_fact_sheet_discloses_simulation_and_injection():
    sheet = narrator.build_fact_sheet(conflict_report())
    assert "ACCELERATED SIMULATION" in sheet
    assert "gyro_saturation" in sheet


def test_fact_sheet_states_the_chute_is_cut_not_deployed():
    """Regression: "cuts the parachute early" was ambiguous enough that the
    model reported "premature parachute deployment", inverting the failure."""
    sheet = narrator.build_fact_sheet(conflict_report())
    assert "NOT deployed too early" in sheet
    assert "RELEASES (cuts away)" in sheet


def test_fact_sheet_offers_the_conflict_time_as_a_plain_number():
    """Regression: with only "T+00:00.06" available the model spelled the
    time out in words rather than quote an unverifiable figure."""
    sheet = narrator.build_fact_sheet(conflict_report())
    assert "seconds into the session" in sheet


@pytest.mark.parametrize("clock,expected", [
    ("T+00:00.06", "0.06"), ("T+01:23.40", "83.40"), ("T+10:00.00", "600.00"),
    (None, "0.00"), ("nonsense", "0.00")])
def test_mission_clock_converts_to_seconds(clock, expected):
    assert narrator._seconds_from_clock(clock) == expected


def test_session_fact_sheet_lists_the_conflicts():
    log, _ = decided_log()
    sheet = narrator.build_fact_sheet(
        log.build_report("session", descent=DESCENT, status=STATUS))
    assert "SESSION TOTALS" in sheet
    assert "conflict #1" in sheet


# ---------------- numeric verification ----------------

def test_faithful_narrative_passes_verification():
    report = conflict_report()
    sheet = narrator.build_fact_sheet(report)
    n = narrative(summary="Rail score reached 0.99 and the camera held quality 0.9.")
    assert narrator._unsupported_numbers(n, sheet) == []


def test_invented_figure_is_caught():
    sheet = narrator.build_fact_sheet(conflict_report())
    n = narrative(summary="The gyroscope railed at 87.3 rad/s for 41.5 seconds.")
    bad = narrator._unsupported_numbers(n, sheet)
    assert "87.3" in bad and "41.5" in bad


def test_invented_figures_are_caught_in_every_prose_field():
    """A hallucination in a bullet is as bad as one in the summary."""
    sheet = narrator.build_fact_sheet(conflict_report())
    for field, value in (("headline", "Fault at 91.7 rad/s"),
                         ("summary", "Drift of 91.7 units"),
                         ("reviewer_note", "Check the 91.7 figure"),
                         ("what_happened", ["It read 91.7"]),
                         ("why", ["Because of 91.7"])):
        n = narrative(**{field: value})
        assert "91.7" in narrator._unsupported_numbers(n, sheet), \
            f"an invented number in {field} slipped through"


def test_small_integers_are_not_treated_as_telemetry():
    """"two sensors", "stage 3", "woken once" are English, not claims."""
    sheet = narrator.build_fact_sheet(conflict_report())
    n = narrative(summary="The 2 sensors disagreed; the arbiter was woken 1 time "
                          "and produced 5 observations.")
    assert narrator._unsupported_numbers(n, sheet) == []


def test_rounded_restatement_of_a_supported_figure_is_accepted():
    """Prose that says 0.99 for a 0.990 fact is good writing, not a fabrication."""
    sheet = "rail score 0.990 and impact speed 160.04 m/s"
    n = narrative(summary="Rail score 0.99 with impact at 160.0 m/s.")
    assert narrator._unsupported_numbers(n, sheet) == []


def test_verification_is_not_fooled_by_a_number_only_in_another_field():
    sheet = "quality 0.9"
    n = narrative(summary="Quality was 0.9 but variance was 77.25.")
    assert narrator._unsupported_numbers(n, sheet) == ["77.25"]


# ---------------- fallback behaviour ----------------

def test_deterministic_narrative_is_always_complete():
    report = conflict_report()
    n = narrator.deterministic_narrative(report)
    assert n["summary"] == report["summary"]
    assert len(n["what_happened"]) == len(report["rationale"])
    assert len(n["why"]) == len(report["rationale"])
    assert n["source"] == "deterministic"


def test_narrate_falls_back_when_the_narrator_is_disabled(monkeypatch):
    monkeypatch.setattr(config, "NARRATOR_ENABLED", False)
    n, source, _ = asyncio.run(narrator.narrate(conflict_report()))
    assert source == "deterministic"
    assert "disabled" in n["note"]
    assert n["summary"], "a disabled narrator still yields a complete report"


def test_narrate_falls_back_when_forced_fallback_is_set(monkeypatch):
    monkeypatch.setattr(config, "ARBITER_FORCE_FALLBACK", True)
    _n, source, _ = asyncio.run(narrator.narrate(conflict_report()))
    assert source == "deterministic"


def test_narrate_falls_back_when_ollama_is_unreachable(monkeypatch):
    """Ollama stopped mid-demo must degrade the prose, never the report."""
    monkeypatch.setattr(config, "NARRATOR_ENABLED", True)
    monkeypatch.setattr(config, "ARBITER_FORCE_FALLBACK", False)

    async def boom(_sheet):
        raise ConnectionError("connection refused")
    monkeypatch.setattr(narrator, "_ask_gemma", boom)

    n, source, _ = asyncio.run(narrator.narrate(conflict_report()))
    assert source == "deterministic"
    assert "ConnectionError" in n["note"]
    assert n["summary"]


def test_narrate_falls_back_on_timeout(monkeypatch):
    monkeypatch.setattr(config, "NARRATOR_ENABLED", True)
    monkeypatch.setattr(config, "ARBITER_FORCE_FALLBACK", False)
    monkeypatch.setattr(config, "NARRATOR_TIMEOUT_S", 0.05)

    async def slow(_sheet):
        await asyncio.sleep(5)
    monkeypatch.setattr(narrator, "_ask_gemma", slow)

    n, source, _ = asyncio.run(narrator.narrate(conflict_report()))
    assert source == "deterministic"
    assert "timeout" in n["note"]


def test_narrate_labels_a_successful_gemma_narrative(monkeypatch):
    monkeypatch.setattr(config, "NARRATOR_ENABLED", True)
    monkeypatch.setattr(config, "ARBITER_FORCE_FALLBACK", False)

    async def ok(_sheet):
        return narrative()
    monkeypatch.setattr(narrator, "_ask_gemma", ok)

    n, source, _ = asyncio.run(narrator.narrate(conflict_report()))
    assert source == "gemma"
    assert n["model"] == config.GEMMA_MODEL
    assert n["note"] == ""
    assert n["headline"] == "Gyro saturation detected"


def test_narrate_never_raises_on_an_undecided_report(monkeypatch):
    """A report can be requested before the arbiter has returned."""
    # Disabled explicitly: no test in this suite may depend on Ollama being
    # up, and none should pay a model round-trip.
    monkeypatch.setattr(config, "NARRATOR_ENABLED", False)
    from server.mission_log import MissionLog
    from tests.test_guardrail import evidence
    log = MissionLog()
    rec = log.open_conflict(1, evidence().model_dump(), stream_t=1.0, trigger=None,
                            mode="live", replay_name=None)
    report = log.build_report(rec.report_id, descent=DESCENT, status=STATUS)
    sheet = narrator.build_fact_sheet(report)
    assert "had not returned" in sheet
    n, _source, _ = asyncio.run(narrator.narrate(report))
    assert n["summary"]
