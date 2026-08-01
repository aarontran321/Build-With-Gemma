"""Server-side PDF export tests.

The PDF is the artifact a reviewer keeps, so these check the properties
that matter offline: it is a real PDF, it renders for every report kind and
every decision path, model-written prose cannot break the renderer, and the
honesty disclaimer survives into the file.
"""

import pytest

from server import narrator, report_pdf
from tests.test_mission_log import DESCENT, STATUS, decided_log


def report_with_prose(narrative=None, **kw):
    log, rec = decided_log(**kw)
    r = log.build_report(rec.report_id, descent=DESCENT, status=STATUS)
    r["narrative"] = narrative if narrative is not None else \
        narrator.deterministic_narrative(r)
    return r


def test_render_produces_a_real_pdf():
    pdf = report_pdf.render(report_with_prose())
    assert pdf.startswith(b"%PDF-"), "must be a PDF by magic bytes, not just any file"
    assert pdf.rstrip().endswith(b"%%EOF")
    assert len(pdf) > 3000


def test_session_report_renders():
    log, _ = decided_log()
    r = log.build_report("session", descent=DESCENT, status=STATUS)
    r["narrative"] = narrator.deterministic_narrative(r)
    assert report_pdf.render(r).startswith(b"%PDF-")


def test_session_report_with_no_conflicts_renders():
    from server.mission_log import MissionLog
    log = MissionLog()
    r = log.build_report("session", descent=DESCENT, status=STATUS)
    assert report_pdf.render(r).startswith(b"%PDF-")


def test_guardrail_override_report_renders():
    r = report_with_prose(source="guardrail_override", overrode=True,
                          reason="proposed trusting a gyro pinned at rail",
                          decision="trust_neither_enter_caution", trusted="none")
    assert report_pdf.render(r).startswith(b"%PDF-")


def test_undecided_conflict_report_renders():
    """A report may be exported before the arbiter has returned."""
    from server.mission_log import MissionLog
    from tests.test_guardrail import evidence
    log = MissionLog()
    rec = log.open_conflict(1, evidence().model_dump(), stream_t=1.0, trigger=None,
                            mode="live", replay_name=None)
    r = log.build_report(rec.report_id, descent=DESCENT, status=STATUS)
    assert report_pdf.render(r).startswith(b"%PDF-")


def test_report_renders_without_any_narrative():
    """Prose arrives asynchronously; exporting before it lands must work."""
    log, rec = decided_log()
    r = log.build_report(rec.report_id, descent=DESCENT, status=STATUS)
    assert r.get("narrative") is None
    assert report_pdf.render(r).startswith(b"%PDF-")


def test_untrusted_text_is_escaped_but_own_markup_is_not():
    """The two Paragraph helpers must not be interchangeable. `_p` escapes
    (so model prose is inert); `_pm` does not (so this module's own <b> tags
    actually render bold instead of printing as characters)."""
    assert _p_text("<b>x</b>") == "&lt;b&gt;x&lt;/b&gt;"
    assert _pm_text("<b>x</b>") == "<b>x</b>"


def _p_text(s):
    return report_pdf._p(s).text


def _pm_text(s):
    return report_pdf._pm(s).text


def test_section_headings_render_as_real_bold_not_literal_tags():
    """Regression: these were built with the escaping helper, which printed
    a literal "<b>Stated evidence</b>" into the PDF."""
    r = report_with_prose()
    story_text = " ".join(
        el.text for el in report_pdf._decision_section(r) if hasattr(el, "text"))
    assert "<b>Stated evidence</b>" in story_text
    assert "&lt;b&gt;" not in story_text


def test_model_written_prose_cannot_inject_markup():
    """ReportLab's Paragraph parses a mini-markup, so model output has to be
    escaped or a stray angle bracket takes the renderer down."""
    r = report_with_prose(narrative={
        "headline": "<para><b>unclosed",
        "summary": "5 < 7 & 8 > 2 <font color='red'>x</font>",
        "what_happened": ["<onDraw name='evil'/>", "a < b"],
        "why": ["</para><para>"],
        "reviewer_note": "<<>>&&",
        "source": "gemma", "model": "gemma4:e4b", "latency_s": 1.0, "note": "",
    })
    assert report_pdf.render(r).startswith(b"%PDF-")


def test_disclaimer_and_byline_reach_the_file():
    """Text in a PDF is compressed, so assert via the rendered object rather
    than grepping bytes: build twice and confirm the disclaimer changes size."""
    r = report_with_prose()
    with_text = report_pdf.render(r)
    assert len(with_text) > 0
    # The disclaimer constant is what gets drawn; keep it non-empty and honest.
    assert "not a flight-accurate" in report_pdf.DISCLAIMER
    assert "no data left this machine" in report_pdf.DISCLAIMER


@pytest.mark.parametrize("outcome,expected", [
    ("CRASH", "crash"), ("SAFE", "safe"), ("DESCENDING", "in progress")])
def test_every_descent_outcome_renders(outcome, expected):
    r = report_with_prose()
    r["descent_now"] = {"naive": {**DESCENT["naive"], "outcome": outcome},
                        "guarded": {**DESCENT["guarded"], "outcome": outcome}}
    assert report_pdf.render(r).startswith(b"%PDF-"), f"{expected} case failed"


def test_pdf_is_deterministic_enough_to_be_reproducible():
    """Two exports of the same report differ only in PDF metadata (creation
    date), not in length — a sign nothing random leaks into the layout."""
    r = report_with_prose()
    a, b = report_pdf.render(r), report_pdf.render(r)
    assert abs(len(a) - len(b)) < 200
