"""Render a report dict (mission_log.build_report) to a printable page.

Why HTML-and-print rather than a PDF library: the project's hard rule is
that nothing leaves the machine and nothing is fetched from a CDN, and a
PDF toolchain (weasyprint/reportlab) would add a heavy dependency whose
layout we would then have to maintain twice. A self-contained page with a
real print stylesheet gives both requested outputs from one artifact —
Cmd/Ctrl-P → "Save as PDF" for the file, or straight to a printer for
paper — with byte-identical styling and zero new dependencies.

The page is deliberately LIGHT-themed (the dashboard is dark): dark
backgrounds waste toner and print badly, and browsers do not print
background colours by default.
"""

import html
from typing import Any, List, Optional

CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font: 13px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
       "Helvetica Neue", sans-serif; color: #14181f; background: #f1f3f7;
       padding: 24px; }
.sheet { max-width: 860px; margin: 0 auto; background: #fff; padding: 40px 46px 54px;
         box-shadow: 0 1px 3px rgba(0,0,0,.16); border-radius: 3px; }

.toolbar { max-width: 860px; margin: 0 auto 14px; display: flex; gap: 8px;
           align-items: center; flex-wrap: wrap; }
.toolbar button, .toolbar a.btn {
  font: 600 12px/1 inherit; padding: 9px 15px; border-radius: 7px; cursor: pointer;
  border: 1px solid #c3ccdb; background: #fff; color: #14181f; text-decoration: none; }
.toolbar button.primary, .toolbar a.btn.primary {
  background: #0f766e; border-color: #0f766e; color: #fff; }
.toolbar button:hover { border-color: #0f766e; }
.toolbar .hint { color: #64748b; font-size: 11.5px; }

h1 { font-size: 21px; letter-spacing: -.01em; margin-bottom: 3px; }
.headline { font-size: 14px; color: #475569; margin-bottom: 2px; }
h2 { font-size: 11px; letter-spacing: .13em; text-transform: uppercase;
     color: #64748b; margin: 26px 0 9px;
     padding-bottom: 5px; border-bottom: 1px solid #e2e8f0; }
h3 { font-size: 12.5px; margin-bottom: 3px; }

.rule { height: 3px; background: #0f766e; margin: 14px 0 18px; }
.masthead { display: flex; justify-content: space-between; align-items: flex-start;
            gap: 20px; flex-wrap: wrap; }
.stamp { text-align: right; font-size: 11px; color: #64748b; line-height: 1.7;
         white-space: nowrap; }
.chip { display: inline-block; padding: 3px 11px; border-radius: 999px; font-size: 11px;
        font-weight: 800; letter-spacing: .05em; border: 1px solid; }
.chip.ok    { color: #0f766e; border-color: #0f766e; background: #ecfdf5; }
.chip.warn  { color: #a16207; border-color: #ca8a04; background: #fefce8; }
.chip.crit  { color: #b91c1c; border-color: #dc2626; background: #fef2f2; }
.chip.plain { color: #475569; border-color: #cbd5e1; background: #f8fafc; }

.summary { font-size: 13.5px; line-height: 1.68; background: #f8fafc;
           border-left: 3px solid #0f766e; padding: 13px 16px; border-radius: 0 5px 5px 0; }
.byline-row { margin-top: 8px; display: flex; align-items: center; gap: 8px;
              flex-wrap: wrap; }
.byline { font-size: 11px; color: #64748b; font-style: italic; }
ul.narr { margin: 6px 0 0 18px; }
ul.narr li { margin-bottom: 5px; page-break-inside: avoid; break-inside: avoid; }

table { width: 100%; border-collapse: collapse; font-size: 12px; }
th { text-align: left; font-size: 10.5px; letter-spacing: .07em; text-transform: uppercase;
     color: #64748b; border-bottom: 1px solid #cbd5e1; padding: 6px 8px 5px; font-weight: 700; }
td { padding: 6px 8px; border-bottom: 1px solid #eef2f7; vertical-align: top; }
tr:last-child td { border-bottom: none; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
code, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
              font-size: 11.5px; }

.kv { display: grid; grid-template-columns: max-content 1fr; gap: 4px 18px; font-size: 12.5px; }
.kv dt { color: #64748b; }
.kv dd { font-weight: 600; }

.step { border: 1px solid #e2e8f0; border-radius: 7px; padding: 12px 15px;
        margin-bottom: 9px; page-break-inside: avoid; break-inside: avoid; }
.step .stage { font-size: 10.5px; font-weight: 800; letter-spacing: .11em; color: #0f766e; }
.step .actor { font-size: 11.5px; color: #64748b; margin-bottom: 6px; }
.step .finding { margin-bottom: 6px; }
.step .action { font-size: 12.5px; color: #334155; border-top: 1px dashed #e2e8f0;
                padding-top: 6px; }
.step .action b, .step .finding b { color: #14181f; }
.step.override { border-color: #fca5a5; background: #fffafa; }

.tl { font-size: 11.5px; }
.tl td:first-child { white-space: nowrap; color: #475569; }
.tl .sev-critical td:nth-child(3) { color: #b91c1c; font-weight: 700; }
.tl .sev-warn td:nth-child(3) { color: #a16207; font-weight: 600; }
.tl .sev-success td:nth-child(3) { color: #0f766e; font-weight: 600; }
.tl .detail { color: #64748b; font-size: 10.5px; }

.outcomes { display: flex; gap: 12px; }
.outcome { flex: 1; border: 1px solid #cbd5e1; border-radius: 7px; padding: 11px;
           text-align: center; }
.outcome .who { font-size: 10px; letter-spacing: .09em; color: #64748b; }
.outcome .what { font-size: 19px; font-weight: 900; margin: 3px 0; }
.outcome .sub { font-size: 11px; color: #64748b; }
.outcome.safe { border-color: #0f766e; background: #ecfdf5; }
.outcome.safe .what { color: #0f766e; }
.outcome.crash { border-color: #dc2626; background: #fef2f2; }
.outcome.crash .what { color: #b91c1c; }

.evgrid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
.trend { font-family: ui-monospace, Menlo, monospace; font-size: 11px; color: #475569;
         word-break: break-all; }
.note { font-size: 11.5px; color: #64748b; margin-top: 8px; font-style: italic; }
.disclaimer { margin-top: 26px; padding-top: 12px; border-top: 1px solid #e2e8f0;
              font-size: 10.5px; color: #64748b; line-height: 1.6; }
.footer { margin-top: 10px; font-size: 10px; color: #94a3b8; }
section { page-break-inside: auto; }
h2 { page-break-after: avoid; break-after: avoid; }

@media print {
  @page { size: letter portrait; margin: 14mm 13mm; }
  body { background: #fff; padding: 0; font-size: 10.5pt; }
  .sheet { max-width: none; box-shadow: none; padding: 0; border-radius: 0; }
  .toolbar { display: none !important; }
  .step, .outcome, tr { page-break-inside: avoid; break-inside: avoid; }
  a { color: inherit; text-decoration: none; }
  thead { display: table-header-group; }
}
"""

PRINT_JS = """
document.getElementById('printbtn').addEventListener('click', () => window.print());
if (location.search.includes('print=1')) window.addEventListener('load', () => setTimeout(() => window.print(), 350));
"""


def _e(v: Any) -> str:
    """Escape any value for HTML text content."""
    return html.escape("—" if v is None else str(v))


def _num(v: Any, digits: int = 2, suffix: str = "") -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return _e(v)


def _outcome_class(outcome: Optional[str]) -> str:
    return {"SAFE": "safe", "CRASH": "crash"}.get(outcome or "", "")


def _outcomes_block(descent: dict, caption: str) -> str:
    naive = (descent or {}).get("naive", {})
    guarded = (descent or {}).get("guarded", {})

    def one(who: str, p: dict) -> str:
        sub = (f"impact {_num(p.get('impact_speed'), 1)} m/s"
               if p.get("outcome") in ("SAFE", "CRASH")
               else f"alt {_num(p.get('alt'), 0)} m · est {_num(p.get('est_alt'), 0)} m")
        return (f'<div class="outcome {_outcome_class(p.get("outcome"))}">'
                f'<div class="who">{_e(who)}</div>'
                f'<div class="what">{_e(p.get("outcome", "—"))}</div>'
                f'<div class="sub">{sub}</div></div>')

    return (f'<div class="outcomes">{one("NAIVE FILTER", naive)}'
            f'{one("GUARDED (ARBITER + GUARDRAIL)", guarded)}</div>'
            f'<div class="note">{_e(caption)}</div>')


def _timeline_table(timeline: List[dict]) -> str:
    if not timeline:
        return '<p class="note">No events recorded.</p>'
    rows = []
    for e in timeline:
        detail = e.get("detail") or {}
        # Keep the printed detail short — the JSONL session log holds the full record.
        bits = ", ".join(f"{k}={v}" for k, v in list(detail.items())[:6]
                         if not isinstance(v, (dict, list)))
        detail_html = f'<div class="detail">{_e(bits)}</div>' if bits else ""
        rows.append(
            f'<tr class="sev-{_e(e.get("severity", "info"))}">'
            f'<td class="mono">{_e(e.get("clock"))}<br>'
            f'<span class="detail">{_e(e.get("mission_clock"))}</span></td>'
            f'<td>{_e(e.get("kind_label"))}</td>'
            f'<td>{_e(e.get("title"))}{detail_html}</td></tr>'
        )
    return ('<table class="tl"><thead><tr><th>Clock / mission</th><th>Source</th>'
            '<th>Event</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table>")


def _evidence_block(ev: dict) -> str:
    if not ev:
        return '<p class="note">No evidence captured for this conflict.</p>'

    def trend(key: str) -> str:
        vals = ev.get(key) or []
        return ", ".join(f"{v:.2f}" if isinstance(v, (int, float)) else str(v) for v in vals) or "—"

    return f"""
<div class="evgrid">
  <div>
    <h3>IMU gyroscope</h3>
    <dl class="kv">
      <dt>status</dt><dd>{_e(ev.get('gyro_status'))}</dd>
      <dt>rate</dt><dd>{_num(ev.get('gyro_rate'), 3, ' rad/s')}</dd>
      <dt>saturated flag</dt><dd>{_e(ev.get('gyro_saturated'))}</dd>
      <dt>rail score</dt><dd>{_num(ev.get('gyro_rail_score'), 3)}</dd>
      <dt>flatline score</dt><dd>{_num(ev.get('gyro_flatline_score'), 3)}</dd>
      <dt>variance</dt><dd>{_num(ev.get('gyro_variance'), 4)}</dd>
    </dl>
    <div class="note">trend: <span class="trend">{_e(trend('gyro_trend'))}</span></div>
  </div>
  <div>
    <h3>Camera rotation proxy</h3>
    <dl class="kv">
      <dt>status</dt><dd>{_e(ev.get('camera_status'))}</dd>
      <dt>rate</dt><dd>{_num(ev.get('flow_rate'), 3, ' units')}</dd>
      <dt>quality</dt><dd>{_num(ev.get('flow_quality'), 3)}</dd>
      <dt>variance</dt><dd>{_num(ev.get('flow_variance'), 4)}</dd>
    </dl>
    <div class="note">trend: <span class="trend">{_e(trend('flow_trend'))}</span></div>
  </div>
</div>
<dl class="kv" style="margin-top:14px">
  <dt>normalized rate difference</dt><dd>{_num(ev.get('normalized_rate_difference'), 3)}</dd>
  <dt>trend correlation</dt><dd>{_num(ev.get('trend_correlation'), 3)}</dd>
  <dt>seconds diverged</dt><dd>{_num(ev.get('seconds_diverged'), 2, ' s')}</dd>
  <dt>recent agreement</dt><dd class="trend">{_e(trend('recent_agreement'))}</dd>
</dl>
<div class="note">This compact window was the complete and only input to the arbiter.
Comparison is by shape and trend — the gyro is calibrated rad/s, the camera proxy is
uncalibrated, and the two are never compared unit-for-unit.</div>
"""


def _rationale_block(steps: List[dict], overrode: bool) -> str:
    out = []
    for s in steps:
        is_override = overrode and s.get("stage", "").startswith("4")
        out.append(
            f'<div class="step{" override" if is_override else ""}">'
            f'<div class="stage">{_e(s.get("stage"))}</div>'
            f'<div class="actor">{_e(s.get("actor"))}</div>'
            f'<div class="finding">{_e(s.get("finding"))}</div>'
            f'<div class="action"><b>Therefore:</b> {_e(s.get("action"))}</div></div>'
        )
    return "".join(out)


def _thresholds_table(rows: List[dict]) -> str:
    body = "".join(
        f'<tr><td>{_e(r.get("name"))}</td>'
        f'<td class="num mono">{_e(r.get("value"))}</td>'
        f'<td>{_e(r.get("unit"))}</td></tr>' for r in rows
    )
    return ('<table><thead><tr><th>Governing parameter</th><th class="num">Value</th>'
            '<th>Unit</th></tr></thead><tbody>' + body + "</tbody></table>")


def _narrative_block(n: Optional[dict]) -> str:
    """The Gemma-written prose. Always carries a visible byline: a reader
    must never have to guess whether a model or a template wrote the words
    in front of them."""
    if not n:
        return ""
    if n.get("source") == "gemma":
        by = (f'<span class="chip ok">WRITTEN BY {_e(n.get("model"))}</span> '
              f'<span class="byline">generated locally in {_e(n.get("latency_s"))} s '
              f'from the mission record; every figure is checked against it</span>')
    else:
        note = f" — {_e(n.get('note'))}" if n.get("note") else ""
        by = (f'<span class="chip plain">DETERMINISTIC TEXT</span> '
              f'<span class="byline">written from the mission record without a '
              f'model{note}</span>')

    def bullets(key: str, heading: str) -> str:
        items = n.get(key) or []
        if not items:
            return ""
        lis = "".join(f"<li>{_e(x)}</li>" for x in items)
        return f'<h3 style="margin-top:14px">{_e(heading)}</h3><ul class="narr">{lis}</ul>'

    return f"""
<section>
  <h2>Summary</h2>
  <p class="summary">{_e(n.get('summary'))}</p>
  <div class="byline-row">{by}</div>
  {bullets('what_happened', 'What happened')}
  {bullets('why', 'Why the system acted as it did')}
  {f'<h3 style="margin-top:14px">Reviewer note</h3><p>{_e(n.get("reviewer_note"))}</p>' if n.get('reviewer_note') else ''}
</section>"""


def _header_block(h: dict) -> str:
    replay = f" · replay: {h.get('replay_name')}" if h.get("replay_name") else ""
    return f"""
<dl class="kv">
  <dt>session</dt><dd class="mono">{_e(h.get('session_id'))}</dd>
  <dt>session started</dt><dd>{_e(h.get('session_started_iso'))}</dd>
  <dt>report generated</dt><dd>{_e(h.get('generated_iso'))}</dd>
  <dt>mission elapsed</dt><dd class="mono">{_e(h.get('mission_elapsed'))}</dd>
  <dt>data source</dt><dd>{_e(str(h.get('mode', '')).upper())}{_e(replay)}</dd>
  <dt>arbiter model</dt><dd class="mono">{_e(h.get('model'))}
      {' (forced fallback)' if h.get('force_fallback') else ''}</dd>
  <dt>raw session log</dt><dd class="mono">{_e(h.get('session_log'))}</dd>
  <dt>events recorded</dt><dd>{_e(h.get('events_recorded'))}
      {f"(+{h.get('events_dropped')} aged out of the buffer)" if h.get('events_dropped') else ""}</dd>
</dl>
"""


DISCLAIMER = (
    "This report documents an engineering demonstration. The dual descent is an "
    "ACCELERATED SIMULATED CONSEQUENCE layered on real or replayed phone sensor data — "
    "the phone is not physically descending, and this is not a flight-accurate "
    "Schiaparelli simulator. Faults labelled as injected are synthetic and were applied "
    "server-side before the monitor saw the sample, so the monitor, arbiter, guardrail "
    "and descent simulation could not distinguish them from physical faults. All "
    "inference ran locally; no data left this machine."
)


def _conflict_body(r: dict) -> str:
    verdict = r.get("verdict") or {}
    proposed = r.get("proposed") or {}
    overrode = bool(r.get("guardrail_overrode"))
    source = r.get("source") or "—"
    src_chip = {"gemma": "ok", "fallback": "warn", "guardrail_override": "crit"}.get(source, "plain")

    ev_lines = "".join(f"<li>{_e(line)}</li>" for line in (verdict.get("evidence") or []))
    override_html = ""
    if overrode:
        override_html = f"""
<section>
  <h2>Guardrail override</h2>
  <div class="step override">
    <div class="stage">SAFETY INVARIANT VIOLATED</div>
    <div class="finding">{_e(r.get('override_reason'))}</div>
    <div class="action"><b>Model proposed:</b> {_e(proposed.get('decision'))}
      (fault {_e(proposed.get('fault_class'))}, trust {_e(proposed.get('trusted_sensor'))},
      confidence {_num(proposed.get('confidence'), 2)}) —
      <b>replaced with</b> {_e(verdict.get('decision'))}
      (trust {_e(verdict.get('trusted_sensor'))}).</div>
  </div>
</section>"""

    note_html = (f'<div class="note">Arbiter note: {_e(r.get("note"))}</div>'
                 if r.get("note") else "")

    return f"""
<section>
  <h2>Decision</h2>
  <dl class="kv">
    <dt>fault class</dt><dd>{_e(verdict.get('fault_class'))}</dd>
    <dt>faulty sensor</dt><dd>{_e(verdict.get('faulty_sensor'))}</dd>
    <dt>trusted sensor</dt><dd>{_e(verdict.get('trusted_sensor'))}</dd>
    <dt>flight decision</dt><dd>{_e(verdict.get('decision'))}</dd>
    <dt>recommended action</dt><dd>{_e(verdict.get('recommended_action'))}</dd>
    <dt>confidence</dt><dd>{_num(verdict.get('confidence'), 2)}</dd>
    <dt>decision source</dt>
      <dd><span class="chip {src_chip}">{_e(str(source).replace('_', ' ').upper())}</span></dd>
    <dt>arbitration latency</dt><dd>{_num(r.get('latency_s'), 3, ' s')}</dd>
    <dt>conflict opened</dt><dd>{_e(r.get('opened_iso'))} ({_e(r.get('opened_mission'))})</dd>
    <dt>decision reached</dt><dd>{_e(r.get('decided_iso'))} ({_e(r.get('decided_mission'))})</dd>
    <dt>fault injection active</dt>
      <dd>{_e(r.get('trigger') or 'none (real input)')}</dd>
  </dl>
  {f'<h3 style="margin-top:12px">Stated evidence</h3><ul style="margin-left:18px">{ev_lines}</ul>' if ev_lines else ''}
  {f'<div class="note">Alternative hypothesis considered: {_e(verdict.get("alternative_hypothesis"))}</div>' if verdict.get('alternative_hypothesis') else ''}
  {note_html}
</section>

{override_html}

<section>
  <h2>Why this decision — stage by stage</h2>
  {_rationale_block(r.get('rationale') or [], overrode)}
</section>

<section>
  <h2>Evidence given to the arbiter</h2>
  {_evidence_block(r.get('evidence') or {})}
</section>

<section>
  <h2>Simulated descent consequence</h2>
  {_outcomes_block(r.get('descent_now') or {}, 'Status at the time this report was generated.')}
</section>

<section>
  <h2>Full event timeline</h2>
  {_timeline_table(r.get('timeline') or [])}
</section>

<section>
  <h2>Governing parameters</h2>
  {_thresholds_table(r.get('thresholds') or [])}
</section>
"""


def _session_body(r: dict) -> str:
    stats = r.get("stats") or {}
    rows = []
    for c in r.get("conflicts") or []:
        chip = "crit" if c.get("overrode") else ("ok" if c.get("decided") else "plain")
        rows.append(
            f'<tr><td class="num">#{_e(c.get("conflict_id"))}</td>'
            f'<td class="mono">{_e(c.get("opened"))}</td>'
            f'<td>{_e(c.get("trigger") or "none")}</td>'
            f'<td>{_e(c.get("fault_class"))}</td>'
            f'<td>{_e(c.get("decision"))}</td>'
            f'<td>{_e(c.get("trusted"))}</td>'
            f'<td><span class="chip {chip}">{_e(str(c.get("source") or "pending").replace("_", " ").upper())}</span></td>'
            f'<td class="num">{_num(c.get("latency_s"), 2, " s")}</td></tr>'
        )
    table = ('<table><thead><tr><th class="num">#</th><th>Opened</th><th>Injection</th>'
             '<th>Fault class</th><th>Decision</th><th>Trusted</th><th>Source</th>'
             '<th class="num">Latency</th></tr></thead><tbody>'
             + ("".join(rows) or '<tr><td colspan="8">No conflicts in this session.</td></tr>')
             + "</tbody></table>")

    return f"""
<section>
  <h2>Session statistics</h2>
  <dl class="kv">
    <dt>conflicts opened</dt><dd>{_e(stats.get('conflicts'))}</dd>
    <dt>decisions reached</dt><dd>{_e(stats.get('decided'))}</dd>
    <dt>guardrail overrides</dt><dd>{_e(stats.get('overrides'))}</dd>
    <dt>synthetic faults injected</dt><dd>{_e(stats.get('injections'))}</dd>
    <dt>arbitration latency</dt>
      <dd>{_num(stats.get('latency_min'), 2, ' s')} – {_num(stats.get('latency_max'), 2, ' s')}
          (mean {_num(stats.get('latency_mean'), 2, ' s')})</dd>
  </dl>
</section>

<section>
  <h2>Conflicts and decisions</h2>
  {table}
</section>

<section>
  <h2>Simulated descent consequence</h2>
  {_outcomes_block(r.get('descent_now') or {}, 'Status at the time this report was generated.')}
</section>

<section>
  <h2>Full event timeline</h2>
  {_timeline_table(r.get('timeline') or [])}
</section>

<section>
  <h2>Governing parameters</h2>
  {_thresholds_table(r.get('thresholds') or [])}
</section>
"""


def render(report: dict) -> str:
    """Return a complete, self-contained printable HTML document."""
    header = report.get("header") or {}
    is_conflict = report.get("kind") == "conflict"
    body = _conflict_body(report) if is_conflict else _session_body(report)
    rid = report.get("report_id", "report")
    title = report.get("title", "Mission Report")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<!-- OFFLINE RULE: self-contained. No CDN, no external URL, no web font. -->
<style>{CSS}</style>
</head>
<body>
<div class="toolbar">
  <a class="btn primary" href="/api/report/{_e(rid)}.pdf">⬇ Download PDF</a>
  <button id="printbtn">🖨 Print this page</button>
  <a class="btn" href="/api/report/{_e(rid)}.json" download="{_e(rid)}.json">⬇ Raw JSON</a>
  <a class="btn" href="/dashboard/">← Dashboard</a>
  <span class="hint">The PDF is rendered on the server and needs no browser
  dialog. Printing this page instead? Enable “Background graphics” to keep the
  status colours.</span>
</div>

<div class="sheet">
  <div class="masthead">
    <div>
      <h1>{_e(title)}</h1>
      <div class="headline">{_e(report.get('headline'))}</div>
      <div style="margin-top:7px"><span class="chip plain">{_e(report.get('outcome_word'))}</span></div>
    </div>
    <div class="stamp">
      SENSOR ARBITER<br>
      {_e(header.get('generated_iso'))}<br>
      session {_e(header.get('session_id'))}
    </div>
  </div>
  <div class="rule"></div>

  {_narrative_block(report.get('narrative')) or f'''
  <section>
    <h2>Summary</h2>
    <p class="summary">{_e(report.get('summary'))}</p>
  </section>'''}

  <section>
    <h2>Run context</h2>
    {_header_block(header)}
  </section>

  {body}

  <div class="disclaimer">{_e(DISCLAIMER)}</div>
  <div class="footer">Report {_e(rid)} · generated {_e(header.get('generated_iso'))} ·
    Sensor Arbiter — deep-space descent fault arbitration with edge Gemma</div>
</div>
<script>{PRINT_JS}</script>
</body>
</html>
"""
