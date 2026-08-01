"""Server-side PDF export for mission reports.

ReportLab, chosen deliberately over the alternatives: WeasyPrint needs
Cairo/Pango system libraries (no Homebrew on the demo machine), and driving
headless Chrome would make PDF export depend on a browser being installed
at a guessable path. ReportLab is a pure-Python wheel, so `pip install -r
requirements.txt` is the whole setup and export works on any judging
machine, fully offline — the same promise the rest of the project makes.

Layout mirrors report_html.py section for section, so the printed PDF and
the on-screen page tell the same story in the same order. The prose at the
top is written by Gemma (server/narrator.py) when available; every table,
figure and timestamp below it is deterministic and never model-written.
"""

from io import BytesIO
from typing import Any, List, Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

TEAL = colors.HexColor("#0f766e")
INK = colors.HexColor("#14181f")
MUTED = colors.HexColor("#64748b")
RULE = colors.HexColor("#cbd5e1")
HAIR = colors.HexColor("#eef2f7")
RED = colors.HexColor("#b91c1c")
RED_BG = colors.HexColor("#fef2f2")
GREEN_BG = colors.HexColor("#ecfdf5")
AMBER = colors.HexColor("#a16207")
PANEL = colors.HexColor("#f8fafc")


def _style(name: str, **kw) -> ParagraphStyle:
    base = dict(name=name, fontName="Helvetica", fontSize=8.6, leading=12,
                textColor=INK, alignment=TA_LEFT)
    base.update(kw)
    return ParagraphStyle(**base)


S = {
    "title": _style("t", fontName="Helvetica-Bold", fontSize=17, leading=20),
    "headline": _style("hl", fontSize=10.5, leading=14, textColor=colors.HexColor("#475569")),
    "stamp": _style("st", fontSize=7.4, leading=10.5, textColor=MUTED),
    "h2": _style("h2", fontName="Helvetica-Bold", fontSize=8, leading=11,
                 textColor=MUTED, spaceBefore=2, spaceAfter=2),
    "body": _style("b"),
    "summary": _style("sum", fontSize=9.2, leading=13.4),
    "small": _style("sm", fontSize=7.6, leading=10.6, textColor=MUTED),
    "italic": _style("it", fontName="Helvetica-Oblique", fontSize=7.8,
                     leading=11, textColor=MUTED),
    "cellk": _style("ck", fontSize=8.2, leading=11, textColor=MUTED),
    "cellv": _style("cv", fontName="Helvetica-Bold", fontSize=8.2, leading=11),
    "cell": _style("c", fontSize=8, leading=11),
    "mono": _style("m", fontName="Courier", fontSize=7.4, leading=10),
    "stage": _style("sg", fontName="Helvetica-Bold", fontSize=7.6, leading=10,
                    textColor=TEAL),
    "actor": _style("ac", fontSize=7.6, leading=10, textColor=MUTED),
    "bullet": _style("bu", fontSize=8.6, leading=12, leftIndent=9, bulletIndent=1),
    "outcome": _style("oc", fontName="Helvetica-Bold", fontSize=15, leading=18,
                      alignment=1),
    "outwho": _style("ow", fontSize=6.8, leading=9, textColor=MUTED, alignment=1),
    "outsub": _style("os", fontSize=7.6, leading=10, textColor=MUTED, alignment=1),
}


def _t(v: Any) -> str:
    """Escape for ReportLab's mini-markup — its Paragraph parses tags, so
    model-written prose must never reach it raw."""
    return escape("—" if v is None else str(v))


def _num(v: Any, digits: int = 2, suffix: str = "") -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return _t(v)


def _p(text: Any, style: str = "body") -> Paragraph:
    """Untrusted text. Everything is escaped, so tags in model prose print
    as characters instead of being interpreted."""
    return Paragraph(_t(text), S[style])


def _pm(markup: str, style: str = "body") -> Paragraph:
    """Markup written by THIS module. Any interpolated value must already
    have gone through _t() — the two helpers exist so that requirement is
    visible at every call site rather than assumed."""
    return Paragraph(markup, S[style])


def _heading(text: str) -> List[Any]:
    """Section heading with the teal underrule used on the HTML page."""
    tbl = Table([[Paragraph(_t(text.upper()), S["h2"])]], colWidths=[178 * mm])
    tbl.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 0.6, RULE),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return [tbl, Spacer(1, 5)]


def _kv_table(rows: List[tuple]) -> Table:
    data = [[Paragraph(_t(k), S["cellk"]), Paragraph(_t(v), S["cellv"])]
            for k, v in rows]
    tbl = Table(data, colWidths=[46 * mm, 132 * mm], hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1.6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6),
    ]))
    return tbl


def _data_table(header: List[str], rows: List[List[str]],
                widths: List[float], aligns: Optional[dict] = None) -> Table:
    data = [[Paragraph(f"<b>{_t(h)}</b>", S["small"]) for h in header]]
    for r in rows:
        data.append([c if isinstance(c, Paragraph) else Paragraph(_t(c), S["cell"])
                     for c in r])
    tbl = Table(data, colWidths=widths, hAlign="LEFT", repeatRows=1)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
        ("LINEBELOW", (0, 1), (-1, -2), 0.3, HAIR),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]
    for col, al in (aligns or {}).items():
        style.append(("ALIGN", (col, 0), (col, -1), al))
    tbl.setStyle(TableStyle(style))
    return tbl


def _callout(lines: List[Any], bg, border) -> Table:
    tbl = Table([[lines]], colWidths=[178 * mm], hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.7, border),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return tbl


# ---------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------

def _masthead(report: dict) -> List[Any]:
    h = report.get("header") or {}
    left = [_p(report.get("title", "Mission Report"), "title"),
            Spacer(1, 2),
            _p(report.get("headline"), "headline"),
            Spacer(1, 4),
            _p(str(report.get("outcome_word", "")).upper(), "cellv")]
    right = [Paragraph("SENSOR ARBITER", S["stamp"]),
             Paragraph(_t(h.get("generated_iso")), S["stamp"]),
             Paragraph("session " + _t(h.get("session_id")), S["stamp"])]
    tbl = Table([[left, right]], colWidths=[122 * mm, 56 * mm], hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (0, 0), "TOP"), ("VALIGN", (1, 0), (1, 0), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    rule = Table([[""]], colWidths=[178 * mm], rowHeights=[2.4])
    rule.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), TEAL)]))
    return [tbl, Spacer(1, 7), rule, Spacer(1, 2)]


def _narrative_section(report: dict) -> List[Any]:
    """The Gemma-written prose. Always labelled with which engine wrote it —
    a reader must never have to guess whether a model or a template
    produced the words in front of them."""
    n = report.get("narrative")
    if not n:
        return []
    src = n.get("source", "deterministic")
    if src == "gemma":
        by = f"Written by {n.get('model')} running locally, in {n.get('latency_s')} s."
    else:
        by = "Written deterministically from the mission record."
        if n.get("note"):
            by += f" (Model narration unavailable: {n['note']})"

    out: List[Any] = _heading("Summary")
    out.append(_callout([_p(n.get("summary"), "summary")], PANEL, RULE))
    out.append(Spacer(1, 4))
    out.append(_p(by, "italic"))

    if n.get("what_happened"):
        out += _heading("What happened")
        for line in n["what_happened"]:
            out.append(Paragraph(_t(line), S["bullet"], bulletText="•"))
    if n.get("why"):
        out += _heading("Why the system acted as it did")
        for line in n["why"]:
            out.append(Paragraph(_t(line), S["bullet"], bulletText="•"))
    if n.get("reviewer_note"):
        out += _heading("Reviewer note")
        out.append(_p(n["reviewer_note"]))
    return out


def _context_section(report: dict) -> List[Any]:
    h = report.get("header") or {}
    mode = str(h.get("mode", "")).upper()
    if h.get("replay_name"):
        mode += f" · replay: {h['replay_name']}"
    rows = [
        ("session", h.get("session_id")),
        ("session started", h.get("session_started_iso")),
        ("report generated", h.get("generated_iso")),
        ("mission elapsed", h.get("mission_elapsed")),
        ("data source", mode),
        ("arbiter model", str(h.get("model")) +
         (" (forced fallback)" if h.get("force_fallback") else "")),
        ("raw session log", h.get("session_log")),
        ("events recorded", str(h.get("events_recorded")) +
         (f" (+{h.get('events_dropped')} aged out)" if h.get("events_dropped") else "")),
    ]
    return _heading("Run context") + [_kv_table(rows)]


def _decision_section(report: dict) -> List[Any]:
    v = report.get("verdict") or {}
    if not v:
        return []
    rows = [
        ("fault class", v.get("fault_class")),
        ("faulty sensor", v.get("faulty_sensor")),
        ("trusted sensor", v.get("trusted_sensor")),
        ("flight decision", v.get("decision")),
        ("recommended action", v.get("recommended_action")),
        ("confidence", _num(v.get("confidence"))),
        ("decision source", str(report.get("source", "")).replace("_", " ").upper()),
        ("arbitration latency", _num(report.get("latency_s"), 3, " s")),
        ("conflict opened", f"{report.get('opened_iso')} ({report.get('opened_mission')})"),
        ("decision reached", f"{report.get('decided_iso')} ({report.get('decided_mission')})"),
        ("fault injection active", report.get("trigger") or "none (real input)"),
    ]
    out = _heading("Decision") + [_kv_table(rows)]
    if v.get("evidence"):
        out.append(Spacer(1, 5))
        out.append(_pm("<b>Stated evidence</b>"))
        for line in v["evidence"]:
            out.append(Paragraph(_t(line), S["bullet"], bulletText="•"))
    if v.get("alternative_hypothesis"):
        out.append(Spacer(1, 3))
        out.append(_p("Alternative hypothesis considered: " +
                      str(v["alternative_hypothesis"]), "italic"))
    if report.get("note"):
        out.append(_p("Arbiter note: " + str(report["note"]), "italic"))
    return out


def _override_section(report: dict) -> List[Any]:
    if not report.get("guardrail_overrode"):
        return []
    proposed = report.get("proposed") or {}
    v = report.get("verdict") or {}
    inner = [
        Paragraph("SAFETY INVARIANT VIOLATED", ParagraphStyle(
            "ov", parent=S["stage"], textColor=RED)),
        Spacer(1, 3),
        _p(report.get("override_reason")),
        Spacer(1, 4),
        _pm(f"<b>Model proposed:</b> {_t(proposed.get('decision'))} "
           f"(fault {_t(proposed.get('fault_class'))}, trust "
           f"{_t(proposed.get('trusted_sensor'))}) — <b>replaced with</b> "
           f"{_t(v.get('decision'))} (trust {_t(v.get('trusted_sensor'))})."),
    ]
    return _heading("Guardrail override") + [_callout(inner, RED_BG, RED)]


def _rationale_section(report: dict) -> List[Any]:
    steps = report.get("rationale") or []
    if not steps:
        return []
    out = _heading("Why this decision — stage by stage")
    for s in steps:
        block = [
            Paragraph(_t(s.get("stage")), S["stage"]),
            Paragraph(_t(s.get("actor")), S["actor"]),
            Spacer(1, 3),
            _p(s.get("finding")),
            Spacer(1, 3),
            _pm("<b>Therefore:</b> " + _t(s.get("action"))),
        ]
        is_override = report.get("guardrail_overrode") and \
            str(s.get("stage", "")).startswith("4")
        out.append(KeepTogether(_callout(
            block, RED_BG if is_override else colors.white,
            RED if is_override else RULE)))
        out.append(Spacer(1, 4))
    return out


def _evidence_section(report: dict) -> List[Any]:
    ev = report.get("evidence") or {}
    if not ev:
        return []

    def trend(key: str) -> str:
        vals = ev.get(key) or []
        return ", ".join(f"{v:.2f}" if isinstance(v, (int, float)) else str(v)
                         for v in vals) or "—"

    gyro = _kv_table([
        ("status", ev.get("gyro_status")),
        ("rate", _num(ev.get("gyro_rate"), 3, " rad/s")),
        ("saturated flag", ev.get("gyro_saturated")),
        ("rail score", _num(ev.get("gyro_rail_score"), 3)),
        ("flatline score", _num(ev.get("gyro_flatline_score"), 3)),
        ("variance", _num(ev.get("gyro_variance"), 4)),
    ])
    cam = _kv_table([
        ("status", ev.get("camera_status")),
        ("rate", _num(ev.get("flow_rate"), 3, " units")),
        ("quality", _num(ev.get("flow_quality"), 3)),
        ("variance", _num(ev.get("flow_variance"), 4)),
    ])
    out = _heading("Evidence given to the arbiter")
    out += [
        _pm("<b>IMU gyroscope</b>"), gyro,
        _p("trend: " + trend("gyro_trend"), "mono"), Spacer(1, 6),
        _pm("<b>Camera rotation proxy</b>"), cam,
        _p("trend: " + trend("flow_trend"), "mono"), Spacer(1, 6),
        _kv_table([
            ("normalized rate difference", _num(ev.get("normalized_rate_difference"), 3)),
            ("trend correlation", _num(ev.get("trend_correlation"), 3)),
            ("seconds diverged", _num(ev.get("seconds_diverged"), 2, " s")),
            ("recent agreement", trend("recent_agreement")),
        ]),
        Spacer(1, 4),
        _p("This compact window was the complete and only input to the arbiter. "
           "Comparison is by shape and trend — the gyro is calibrated rad/s, the "
           "camera proxy is uncalibrated, and the two are never compared "
           "unit-for-unit.", "italic"),
    ]
    return out


def _outcomes_section(report: dict) -> List[Any]:
    d = report.get("descent_now") or {}
    naive, guarded = d.get("naive", {}), d.get("guarded", {})

    def cell(who: str, p: dict) -> List[Any]:
        outcome = p.get("outcome", "—")
        sub = (f"impact {_num(p.get('impact_speed'), 1)} m/s"
               if outcome in ("SAFE", "CRASH")
               else f"alt {_num(p.get('alt'), 0)} m · est {_num(p.get('est_alt'), 0)} m")
        colour = TEAL if outcome == "SAFE" else (RED if outcome == "CRASH" else INK)
        return [Paragraph(_t(who), S["outwho"]),
                Paragraph(_t(outcome), ParagraphStyle("o", parent=S["outcome"],
                                                      textColor=colour)),
                Paragraph(_t(sub), S["outsub"])]

    tbl = Table([[cell("NAIVE FILTER", naive),
                  cell("GUARDED (ARBITER + GUARDRAIL)", guarded)]],
                colWidths=[88 * mm, 88 * mm], hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (0, 0), 0.7,
         RED if naive.get("outcome") == "CRASH" else RULE),
        ("BOX", (1, 0), (1, 0), 0.7,
         TEAL if guarded.get("outcome") == "SAFE" else RULE),
        ("BACKGROUND", (0, 0), (0, 0),
         RED_BG if naive.get("outcome") == "CRASH" else colors.white),
        ("BACKGROUND", (1, 0), (1, 0),
         GREEN_BG if guarded.get("outcome") == "SAFE" else colors.white),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return _heading("Simulated descent consequence") + [
        tbl, Spacer(1, 4),
        _p("Status at the time this report was generated.", "italic")]


def _timeline_section(report: dict) -> List[Any]:
    timeline = report.get("timeline") or []
    if not timeline:
        return _heading("Full event timeline") + [_p("No events recorded.", "italic")]
    rows = []
    for e in timeline:
        detail = e.get("detail") or {}
        bits = ", ".join(f"{k}={v}" for k, v in list(detail.items())[:5]
                         if not isinstance(v, (dict, list)))
        sev = e.get("severity")
        colour = {"critical": RED, "warn": AMBER, "success": TEAL}.get(sev, INK)
        event = Paragraph(
            f'<font color="{colour.hexval()}">{_t(e.get("title"))}</font>' +
            (f'<br/><font size="6.4" color="{MUTED.hexval()}">{_t(bits)}</font>'
             if bits else ""),
            S["cell"])
        clock = Paragraph(
            f'{_t(e.get("clock"))}<br/><font size="6.4" color="{MUTED.hexval()}">'
            f'{_t(e.get("mission_clock"))}</font>', S["mono"])
        rows.append([clock, Paragraph(_t(e.get("kind_label")), S["small"]), event])
    return _heading("Full event timeline") + [
        _data_table(["Clock / mission", "Source", "Event"], rows,
                    [26 * mm, 26 * mm, 126 * mm])]


def _session_tables(report: dict) -> List[Any]:
    stats = report.get("stats") or {}
    out = _heading("Session statistics") + [_kv_table([
        ("conflicts opened", stats.get("conflicts")),
        ("decisions reached", stats.get("decided")),
        ("guardrail overrides", stats.get("overrides")),
        ("synthetic faults injected", stats.get("injections")),
        ("arbitration latency",
         f"{_num(stats.get('latency_min'))} – {_num(stats.get('latency_max'))} s "
         f"(mean {_num(stats.get('latency_mean'))} s)"),
    ])]
    rows = [[f"#{c.get('conflict_id')}", c.get("opened"), c.get("trigger") or "none",
             c.get("fault_class"), c.get("decision"), c.get("trusted"),
             str(c.get("source") or "pending").replace("_", " ").upper(),
             _num(c.get("latency_s"), 2, " s")]
            for c in report.get("conflicts") or []]
    out += _heading("Conflicts and decisions")
    if rows:
        out.append(_data_table(
            ["#", "Opened", "Injection", "Fault class", "Decision", "Trusted",
             "Source", "Latency"], rows,
            [10 * mm, 22 * mm, 24 * mm, 34 * mm, 33 * mm, 17 * mm, 22 * mm, 16 * mm],
            aligns={7: "RIGHT"}))
    else:
        out.append(_p("No conflicts in this session.", "italic"))
    return out


def _thresholds_section(report: dict) -> List[Any]:
    rows = [[t.get("name"), str(t.get("value")), t.get("unit") or ""]
            for t in report.get("thresholds") or []]
    return _heading("Governing parameters") + [
        _data_table(["Governing parameter", "Value", "Unit"], rows,
                    [96 * mm, 44 * mm, 38 * mm])]


DISCLAIMER = (
    "This report documents an engineering demonstration. The dual descent is an "
    "ACCELERATED SIMULATED CONSEQUENCE layered on real or replayed phone sensor "
    "data — the phone is not physically descending, and this is not a "
    "flight-accurate Schiaparelli simulator. Faults labelled as injected are "
    "synthetic and were applied server-side before the monitor saw the sample, so "
    "the monitor, arbiter, guardrail and descent simulation could not distinguish "
    "them from physical faults. All inference ran locally; no data left this "
    "machine."
)


def _footer(report: dict):
    rid = report.get("report_id", "report")
    session = (report.get("header") or {}).get("session_id", "")

    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 6.8)
        canvas.setFillColor(MUTED)
        canvas.drawString(16 * mm, 11 * mm,
                          f"Sensor Arbiter · report {rid} · session {session}")
        canvas.drawRightString(LETTER[0] - 16 * mm, 11 * mm, f"page {doc.page}")
        canvas.restoreState()
    return draw


def render(report: dict) -> bytes:
    """Return the report as PDF bytes."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=15 * mm, bottomMargin=17 * mm,
        title=report.get("title", "Mission Report"),
        author="Sensor Arbiter",
        subject="Deep-space descent fault arbitration — mission report",
    )
    story: List[Any] = []
    story += _masthead(report)
    story += _narrative_section(report)
    story += _context_section(report)

    if report.get("kind") == "conflict":
        story += _decision_section(report)
        story += _override_section(report)
        story += _rationale_section(report)
        story += _evidence_section(report)
    else:
        story += _session_tables(report)

    story += _outcomes_section(report)
    story.append(PageBreak())          # the raw record starts on a fresh page
    story += _timeline_section(report)
    story += _thresholds_section(report)

    story += [Spacer(1, 12), _p(DISCLAIMER, "small")]

    doc.build(story, onFirstPage=_footer(report), onLaterPages=_footer(report))
    return buf.getvalue()
