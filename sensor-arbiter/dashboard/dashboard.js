/* Mission dashboard: websocket client + Chart.js wiring.
 *
 * Resilience rules (P0): the socket auto-reconnects; a server restart or a
 * dropped connection never freezes the page; unknown message types are
 * ignored. All rendering is local — no external asset or URL anywhere.
 */

"use strict";

const $ = (id) => document.getElementById(id);
const WINDOW_S = 30;          // rolling plot window
const UPDATE_EVERY = 4;       // chart.update() every Nth sample (~6 Hz)

/* ---------------- rolling buffers ---------------- */

let t0 = null;
let sampleCount = 0;
const buf = { gyro: [], flow: [], conf: [], naive: [], guarded: [], naiveEst: [] };
const markers = [];           // {x, label, color} vertical lines on rate charts

function push(arr, x, y) {
  arr.push({ x, y });
  const cutoff = x - WINDOW_S - 2;
  while (arr.length && arr[0].x < cutoff) arr.shift();
}

/* ---------------- chart plumbing ---------------- */

// Vertical marker lines (injection moment, decision) drawn without any
// external annotation plugin — tiny afterDraw hook instead.
const markerPlugin = {
  id: "markers",
  afterDraw(chart) {
    const { ctx, chartArea, scales } = chart;
    if (!scales.x) return;
    for (const m of markers) {
      const px = scales.x.getPixelForValue(m.x);
      if (px < chartArea.left || px > chartArea.right) continue;
      ctx.save();
      ctx.strokeStyle = m.color; ctx.setLineDash([4, 4]); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(px, chartArea.top); ctx.lineTo(px, chartArea.bottom); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = m.color; ctx.font = "10px sans-serif";
      ctx.fillText(m.label, px + 3, chartArea.top + 10);
      ctx.restore();
    }
  },
};
Chart.register(markerPlugin);

const baseOpts = (yTitle) => ({
  responsive: true, maintainAspectRatio: false, animation: false,
  parsing: false, normalized: true,
  interaction: { intersect: false },
  scales: {
    x: { type: "linear", ticks: { color: "#64748b", maxTicksLimit: 8,
         callback: (v) => (Math.round(v * 10) / 10) + "s" }, grid: { color: "#1f2b3f" } },
    y: { ticks: { color: "#64748b" }, grid: { color: "#1f2b3f" },
         title: { display: true, text: yTitle, color: "#8fa3bd", font: { size: 10 } } },
  },
  plugins: { legend: { labels: { color: "#b9c7da", boxWidth: 14, font: { size: 10 } } } },
});

const gyroChart = new Chart($("c-gyro"), {
  type: "line",
  data: { datasets: [{ label: "gyro |ω| rad/s", data: buf.gyro, borderColor: "#60a5fa",
    borderWidth: 1.5, pointRadius: 0, tension: 0.15 }] },
  options: baseOpts("rad/s"),
});

const flowOpts = baseOpts("proxy units");
flowOpts.scales.y2 = { position: "right", min: 0, max: 1.05,
  ticks: { color: "#475569" }, grid: { display: false },
  title: { display: true, text: "confidence", color: "#8fa3bd", font: { size: 10 } } };
const flowChart = new Chart($("c-flow"), {
  type: "line",
  data: { datasets: [
    { label: "camera rotation proxy", data: buf.flow, borderColor: "#5eead4",
      borderWidth: 1.5, pointRadius: 0, tension: 0.15 },
    { label: "flow confidence", data: buf.conf, borderColor: "#f59e0b",
      borderWidth: 1, pointRadius: 0, borderDash: [3, 3], yAxisID: "y2" },
  ] },
  options: flowOpts,
});

const altOpts = baseOpts("altitude m");
altOpts.scales.y.min = -100;
const altChart = new Chart($("c-alt"), {
  type: "line",
  data: { datasets: [
    { label: "NAIVE altitude", data: buf.naive, borderColor: "#f87171",
      borderWidth: 2, pointRadius: 0 },
    { label: "NAIVE computed altitude (estimate)", data: buf.naiveEst,
      borderColor: "#f87171", borderWidth: 1, borderDash: [5, 4], pointRadius: 0 },
    { label: "GUARDED altitude", data: buf.guarded, borderColor: "#34d399",
      borderWidth: 2, pointRadius: 0 },
  ] },
  options: altOpts,
});

function refreshCharts(latestX) {
  const xmin = Math.max(0, latestX - WINDOW_S);
  for (const c of [gyroChart, flowChart]) {
    c.options.scales.x.min = xmin;
    c.options.scales.x.max = Math.max(latestX, WINDOW_S);
    c.update("none");
  }
  // altitude chart keeps the whole story visible from t=0
  altChart.options.scales.x.min = 0;
  altChart.options.scales.x.max = Math.max(latestX, WINDOW_S);
  altChart.update("none");
}

/* ---------------- message handling ---------------- */

let lastInjected = null;
let currentMode = "live";
let conflictHasVerdict = false;   // a verdict is latched on the diagnosis panel

function onState(m) {
  currentMode = m.mode;
  setPreviewMode(m.mode);
  if (t0 === null) t0 = m.t;
  const x = m.t - t0;
  push(buf.gyro, x, m.gyro_mag);
  push(buf.flow, x, m.flow_mag);
  push(buf.conf, x, m.flow_confidence);
  // altitude series keep full history (they tell the whole story)
  buf.naive.push({ x, y: m.descent.naive.alt });
  buf.naiveEst.push({ x, y: Math.max(-100, m.descent.naive.est_alt) });
  buf.guarded.push({ x, y: m.descent.guarded.alt });

  if (m.injected && m.injected !== lastInjected) {
    markers.push({ x, label: "INJECT " + m.injected, color: "#fbbf24" });
  }
  lastInjected = m.injected;

  $("mode").textContent = m.mode === "replay"
    ? "REPLAY — " + (m.replay_name || "") : "LIVE";
  $("mode").className = "badge " + (m.mode === "replay" ? "replay" : "live");
  $("fsm").textContent = m.state;
  $("fsm").className = "badge " + m.state;
  $("t-state").textContent = m.state;
  $("t-cid").textContent = m.conflict_id ?? "—";
  $("t-div").textContent = m.divergence.toFixed(2);
  $("t-gstat").textContent = m.gyro_status;
  $("t-cstat").textContent = m.camera_status;
  $("t-rail").textContent = m.gyro_rail_score.toFixed(2);
  $("t-inj").textContent = m.injected
    ? "SYNTHETIC: " + m.injected : "none (real input)";
  $("t-inj").style.color = m.injected ? "#fbbf24" : "";

  setOutcome("naive", m.descent.naive);
  setOutcome("guarded", m.descent.guarded);

  if (++sampleCount % UPDATE_EVERY === 0) refreshCharts(x);
}

function setOutcome(who, p) {
  const el = $("o-" + who);
  const label = el.querySelector(".what");
  label.textContent = p.outcome;
  el.className = "outcome " +
    (p.outcome === "SAFE" ? "safe" : p.outcome === "CRASH" ? "crash" : "");
  $("o-" + who + "-d").textContent =
    p.outcome === "DESCENDING"
      ? `alt ${p.alt} m · est ${p.est_alt} m · ${p.rate} m/s`
      : `impact ${p.impact_speed} m/s`;
}

/* A verdict is latched on screen until something clears it. Without this the
 * panel keeps showing a resolved fault as though it were still happening: the
 * state machine returns to NORMAL, the streams re-agree, and the dashboard
 * still reads "GYRO SATURATION / trusted camera". Only a full RESET used to
 * clear it, which is not obvious when the symptom is a stale red panel.
 *
 * The verdict itself is kept — it is what the demo is showing — but marked
 * historical, so an active conflict and a cleared one can never be confused. */
function markConflictResolved(conflictId) {
  if (!conflictHasVerdict) return;          // nothing latched; nothing to clear
  conflictHasVerdict = false;
  $("diag").classList.add("resolved");
  $("thinking").style.display = "none";
  const note = $("resolved-note");
  note.style.display = "block";
  note.innerHTML =
    `<b>✔ RESOLVED</b> — conflict${conflictId ? " #" + conflictId : ""} cleared: ` +
    `the two streams agree again and the monitor is back to NORMAL. ` +
    `The verdict above is the historical record of that conflict.`;
}

function clearResolvedMark() {
  conflictHasVerdict = true;
  $("diag").classList.remove("resolved");
  $("resolved-note").style.display = "none";
}

function onArbitration(m) {
  clearResolvedMark();                       // a new conflict is live again
  $("thinking").style.display = "block";
  $("fault").textContent = `conflict #${m.conflict_id} under diagnosis`;
  const ev = m.evidence;
  $("evd").innerHTML = "";
  addEv(`monitor: divergence ${ev.normalized_rate_difference}, diverged ${ev.seconds_diverged}s`);
  addEv(`gyro: ${ev.gyro_status}, rail score ${ev.gyro_rail_score}`);
  addEv(`camera: ${ev.camera_status}, quality ${ev.flow_quality}`);
}

function addEv(text) {
  const li = document.createElement("li");
  li.textContent = text;
  $("evd").appendChild(li);
}

function onDecision(m) {
  $("thinking").style.display = "none";
  conflictHasVerdict = true;      // latched: a later return to NORMAL clears it
  const v = m.verdict;
  $("fault").textContent = v.fault_class.replace(/_/g, " ").toUpperCase();
  $("faulty").textContent = v.faulty_sensor;
  $("trusted").textContent = v.trusted_sensor;
  $("decision-chip").textContent = v.decision.replace(/_/g, " ");
  $("lat").textContent = m.arbitration_latency_s + " s";
  $("confv").textContent = (v.confidence * 100).toFixed(0) + "%";
  $("confbar").firstElementChild.style.width = (v.confidence * 100) + "%";
  $("src").innerHTML =
    `<span class="srcbadge src-${m.source}">${m.source.replace(/_/g, " ").toUpperCase()}</span>`;
  $("evd").innerHTML = "";
  for (const line of v.evidence) addEv(line);
  $("alt-hyp").textContent = v.alternative_hypothesis
    ? "alternative considered: " + v.alternative_hypothesis : "";
  const ov = $("override");
  if (m.guardrail_overrode) {
    ov.style.display = "block";
    ov.innerHTML = `<b>GUARDRAIL OVERRIDE</b> — ${m.override_reason}` +
      (m.proposed ? `<br><span class="muted">model proposed: ${m.proposed.decision}
        (${m.proposed.fault_class}, trusted ${m.proposed.trusted_sensor})</span>` : "");
  } else {
    ov.style.display = "none";
  }
  // Straight from the verdict to the report that explains it. A decision
  // replayed in the "hello" backfill carries no report id — fall back to
  // "latest", which resolves server-side to this same decision.
  const link = $("diag-report");
  link.href = `/api/report/${m.report_id || "latest"}.html`;
  link.style.display = "inline-block";
  if (t0 !== null) markers.push({ x: buf.gyro.length ? buf.gyro[buf.gyro.length - 1].x : 0,
                                  label: "DECISION", color: "#5eead4" });
}

function resetView() {
  t0 = null; lastInjected = null; sampleCount = 0;
  for (const k of Object.keys(buf)) buf[k].length = 0;
  markers.length = 0;
  $("thinking").style.display = "none";
  $("fault").textContent = "— no conflict —";
  for (const id of ["faulty", "trusted", "decision-chip", "lat", "confv", "src"])
    $(id).textContent = "—";
  $("confbar").firstElementChild.style.width = "0%";
  $("evd").innerHTML = ""; $("alt-hyp").textContent = "";
  $("override").style.display = "none";
  $("diag-report").style.display = "none";
  conflictHasVerdict = false;
  $("diag").classList.remove("resolved");
  $("resolved-note").style.display = "none";
  // The log deliberately survives a reset: what happened before it is still
  // part of the session record, and the server keeps those reports too.
  refreshReports();
  setOutcome("naive", { outcome: "DESCENDING", alt: 3700, est_alt: 3700, rate: 0, impact_speed: 0 });
  setOutcome("guarded", { outcome: "DESCENDING", alt: 3700, est_alt: 3700, rate: 0, impact_speed: 0 });
  refreshCharts(0);
}

function setPreviewMode(mode) {
  const replay = mode === "replay";
  if (replay) {
    $("phone-preview").style.display = "none";
    $("preview-live").style.display = "none";
    $("preview-empty").style.display = "grid";
    $("preview-empty").innerHTML = "REPLAY MODE<br>live camera preview paused";
  } else if ($("phone-preview").src) {
    // Back to live with a frame already buffered: restore it now instead of
    // leaving the replay overlay up until the next 4 fps frame lands.
    $("phone-preview").style.display = "block";
    $("preview-live").style.display = "inline-block";
    $("preview-empty").style.display = "none";
  } else {
    $("preview-empty").style.display = "grid";
    $("preview-empty").innerHTML = "Waiting for the phone camera…<br>Open /phone/ and enable sensors.";
  }
}

function banner(text, ms) {
  const b = $("banner");
  b.textContent = text; b.style.display = "block";
  if (ms) setTimeout(() => { b.style.display = "none"; }, ms);
}

/* ---------------- mission log ---------------- */

/* The log panel is append-only and capped, mirroring the server's ring
 * buffer: a long demo must not grow the DOM without bound. Auto-scroll
 * pauses as soon as the operator scrolls up, so reading history is never
 * yanked away by an incoming event. */

const LOG_MAX_ROWS = 400;
// "Key events" filter: what someone reviewing the demo actually needs.
const KEY_KINDS = new Set(["arbitration", "decision", "guardrail", "inject", "descent"]);
let keyOnly = false;
let lastSeq = 0;

function logAtBottom() {
  const el = $("logscroll");
  return el.scrollHeight - el.scrollTop - el.clientHeight < 40;
}

function addLogRow(e) {
  if (e.seq <= lastSeq) return;   // backfill and live stream can overlap
  lastSeq = e.seq;

  const box = $("logscroll");
  const empty = $("logempty");
  if (empty) empty.remove();
  const stick = logAtBottom();

  const row = document.createElement("div");
  row.className = "logrow sev-" + (e.severity || "info");
  row.dataset.kind = e.kind;
  if (keyOnly && !KEY_KINDS.has(e.kind)) row.style.display = "none";

  const t = document.createElement("div");
  t.className = "lt";
  t.textContent = e.clock;
  t.title = `${e.iso}  ·  mission ${e.mission_clock}` +
            (e.stream_t !== null ? `  ·  stream t=${e.stream_t}s` : "");

  const k = document.createElement("div");
  k.className = "lk";
  k.textContent = e.kind_label;

  const msg = document.createElement("div");
  msg.className = "lmsg";
  msg.textContent = e.title;                       // textContent: never innerHTML
  if (e.report_id) {
    const a = document.createElement("a");
    a.className = "rep";
    a.href = `/api/report/${e.report_id}.html`;
    a.target = "_blank"; a.rel = "noopener";
    a.textContent = "report";
    msg.appendChild(a);
  }
  const bits = Object.entries(e.detail || {})
    .filter(([, v]) => v !== null && typeof v !== "object")
    .slice(0, 5).map(([kk, v]) => `${kk}=${v}`).join("  ");
  if (bits) {
    const d = document.createElement("span");
    d.className = "ldetail";
    d.textContent = bits;
    msg.appendChild(d);
  }

  row.append(t, k, msg);
  box.appendChild(row);
  while (box.children.length > LOG_MAX_ROWS) box.removeChild(box.firstChild);
  if (stick) box.scrollTop = box.scrollHeight;
}

function applyLogFilter() {
  for (const row of $("logscroll").children) {
    if (!row.dataset.kind) continue;
    row.style.display = (keyOnly && !KEY_KINDS.has(row.dataset.kind)) ? "none" : "";
  }
}

/* ---------------- reports ---------------- */

let latestReportId = null;

function renderReports(reports) {
  const box = $("replist");
  box.innerHTML = "";
  if (!reports || !reports.length) {
    box.innerHTML = '<div class="muted" style="padding:6px">No reports yet. One is ' +
      'generated automatically the moment a decision is made.</div>';
    return;
  }
  for (const r of reports) {
    const item = document.createElement("div");
    item.className = "repitem";

    const text = document.createElement("div");
    const title = document.createElement("div");
    title.className = "rtitle";
    title.textContent = r.title;
    const sub = document.createElement("div");
    sub.className = "rsub";
    sub.textContent = `${r.clock} · ${r.subtitle}`;
    text.append(title, sub);

    const act = document.createElement("div");
    act.className = "ract";
    const open = document.createElement("a");
    open.className = "primary";
    open.href = `/api/report/${r.report_id}.html`;
    open.target = "_blank"; open.rel = "noopener";
    open.textContent = "Open";
    // Server-rendered PDF: a plain download, no browser print dialog.
    const pdf = document.createElement("a");
    pdf.href = `/api/report/${r.report_id}.pdf`;
    pdf.textContent = "⬇ PDF";
    act.append(open, pdf);

    item.append(text, act);
    box.appendChild(item);
  }
}

async function refreshReports() {
  try {
    const r = await fetch("/api/reports");
    const j = await r.json();
    latestReportId = j.latest;
    renderReports(j.reports);
  } catch (_) { /* a failed refresh must never break the dashboard */ }
}

/* ---------------- websocket ---------------- */

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/dashboard`);
  ws.onopen = () => { $("conn").textContent = "dashboard link: connected"; };
  ws.onclose = () => {
    $("conn").textContent = "dashboard link: reconnecting…";
    setTimeout(connect, 1000);
  };
  ws.onerror = () => { try { ws.close(); } catch (_) {} };
  ws.onmessage = (e) => {
    let m;
    try { m = JSON.parse(e.data); } catch (_) { return; }
    switch (m.type) {
      case "state": onState(m); break;
      case "arbitration": onArbitration(m); break;
      case "decision": onDecision(m); break;
      // The state chip is driven by state messages, but the return to NORMAL
      // is the one transition the diagnosis panel must react to: it is the
      // moment the conflict is over and the latched verdict goes stale.
      case "transition":
        if (m.to_state === "NORMAL" && m.from_state !== "CANDIDATE")
          markConflictResolved(m.conflict_id);
        break;
      case "log": addLogRow(m.event); break;
      case "report_ready":
        refreshReports();
        banner(`📄 ${m.title} ready — Gemma is writing its summary. Download the PDF from the Reports panel.`, 9000);
        break;
      // Sent when a replay ends and the timeline returns to the phone. Without
      // it the mode chip and camera preview stay stuck on REPLAY until the next
      // live sample arrives — which never happens if no phone is connected.
      case "mode":
        currentMode = m.mode;
        $("mode").textContent = m.mode === "replay" ? "REPLAY" : "LIVE";
        $("mode").className = "badge " + (m.mode === "replay" ? "replay" : "live");
        setPreviewMode(m.mode);
        break;
      case "narrative_ready":
        refreshReports();
        if (m.source === "gemma") banner(`✍ Gemma wrote the report summary: “${m.headline}”`, 8000);
        break;
      case "inject": banner(`Synthetic fault injected: ${m.scenario}`, 4000); break;
      case "reset": resetView(); banner("Session reset", 2500); break;
      case "replay":
        if (m.status === "started") { resetView(); banner(`REPLAY started: ${m.name} (recorded sensor data, live pipeline)`, 6000); }
        else banner(`REPLAY finished: ${m.name}`, 0);
        break;
      case "hello":
        $("model").textContent = "model: " + m.model;
        if ($("replaysel").options.length === 0)
          for (const g of m.golden_runs) {
            const o = document.createElement("option");
            o.value = g; o.textContent = "golden: " + g;
            $("replaysel").appendChild(o);
          }
        // a dashboard (re)connecting mid-session still shows the verdict,
        // the log history and the reports already generated
        for (const e of m.log || []) addLogRow(e);
        if (m.reports) { renderReports(m.reports); refreshReports(); }
        if (m.last_decision) {
          onDecision(m.last_decision);
          // A reconnecting dashboard gets the last verdict but no transition
          // history, so a conflict that already closed would come back looking
          // active. hello carries the current state — trust it.
          if (m.state === "NORMAL")
            markConflictResolved(m.last_decision.conflict_id);
        }
        break;
    }
  };
}
connect();

/* Preview uses a separate lossy socket so video can never block telemetry. */
function connectPreview() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/preview/dashboard`);
  ws.binaryType = "blob";
  ws.onopen = () => ws.send("ready");
  ws.onclose = () => setTimeout(connectPreview, 1000);
  ws.onerror = () => { try { ws.close(); } catch (_) {} };
  ws.onmessage = (e) => {
    if (currentMode !== "live") return;
    const img = $("phone-preview");
    const old = img.dataset.objectUrl;
    const url = URL.createObjectURL(e.data);
    img.dataset.objectUrl = url;
    img.onload = () => { if (old) URL.revokeObjectURL(old); };
    img.src = url;
    img.style.display = "block";
    $("preview-empty").style.display = "none";
    $("preview-live").style.display = "inline-block";
  };
}
connectPreview();

/* ---------------- controls ---------------- */

async function post(path) {
  try {
    const r = await fetch(path, { method: "POST" });
    const j = await r.json();
    if (j.error) banner(j.error, 4000);
  } catch (e) { banner("Request failed: " + e.message, 4000); }
}

$("b-gyro").onclick = () => post("/api/inject/gyro_saturation");
$("b-cam").onclick = () => post("/api/inject/camera_dark");
$("b-trans").onclick = () => post("/api/inject/transient");
$("b-reset").onclick = () => post("/api/reset");
$("b-replay").onclick = () => post("/api/replay/" + $("replaysel").value);

$("b-report").onclick = () =>
  window.open(`/api/report/${latestReportId || "latest"}.html`, "_blank", "noopener");
$("b-session").onclick = () =>
  window.open("/api/report/session.html", "_blank", "noopener");

$("b-logfilter").onclick = (e) => {
  keyOnly = !keyOnly;
  e.currentTarget.classList.toggle("on", keyOnly);
  applyLogFilter();
};
$("b-logclear").onclick = () => {
  // View-only: the server's record and every report are untouched.
  $("logscroll").innerHTML = '<div id="logempty">View cleared — the server\'s ' +
    'record is intact and still printable from the Reports panel.</div>';
};
$("logscroll").addEventListener("scroll", () => {
  $("paused").style.display = logAtBottom() ? "none" : "inline";
});

refreshReports();
