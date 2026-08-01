/* Phone sensor node: IMU gyro sampler + camera-derived rotational-motion proxy.
 *
 * HARD CONSTRAINTS ENFORCED IN THIS FILE (do not weaken):
 *
 * 1. INDEPENDENCE. The camera rotation estimate below is computed from
 *    camera PIXELS ONLY. Nothing in the optical-flow path reads
 *    DeviceMotionEvent, DeviceOrientationEvent, or any IMU value — the two
 *    streams first meet on the server's comparison plot. This independence
 *    is the entire credibility of the demo.
 *
 * 2. SELF-REPORTED CONFIDENCE. The proxy reports flow_confidence (fraction
 *    of tracking blocks that survived rejection) so low texture / darkness
 *    shows up as visibly unreliable instead of silently wrong.
 *
 * 3. FRAME BUDGET. Flow runs on a downscaled grayscale frame within a fixed
 *    per-frame budget and DROPS frames rather than queueing when behind.
 *
 * Realistic expectation (also in the writeup): this proxy tracks the TREND
 * and TIMING of rotation well enough for side-by-side comparison against
 * the gyro; it is coarse, uncalibrated, and not an angular-rate sensor.
 * The demo's signal is the contrast between an obviously faulted stream
 * (pinned / flatlined / zero-confidence) and a smooth finite one — never
 * unit agreement between the two.
 */

"use strict";

/* ---------------------------- tunables ---------------------------- */

const PROC_W = 160;          // downscaled width: coarse ON PURPOSE — this is
                             // what makes real-time flow work on a phone CPU
const PROC_H = 120;
const GRID_X = 6, GRID_Y = 4;   // fixed grid of tracking blocks
const BLOCK = 12;               // block half-size is BLOCK/2
const SEARCH = 6;               // search window radius (px) in the next frame
const TEXTURE_MIN = 60;         // min block variance: flat regions give
                                // meaningless vectors and are rejected
const OUTLIER_DIST = 4.0;       // px from field median: moving objects /
                                // lighting flicker get dropped here
const SMOOTH_N = 4;             // short moving average: stable plot, still
                                // fast enough to track a quick spin
const SEND_PERIOD_MS = 50;      // ~20 Hz sample uplink (spec: 40–60 ms)
const PROC_PERIOD_MS = 66;      // ~15 Hz flow processing budget
const FLOW_SCALE = 1.4;         // maps residual px/frame into a convenient
                                // plotting range; uncalibrated by design
const PREVIEW_PERIOD_MS = 250;  // 4 fps; visual proof, never control telemetry

/* ---------------------------- state ---------------------------- */

const $ = (id) => document.getElementById(id);
const video = $("video"), preview = $("preview");
const pctx = preview.getContext("2d");

let ws = null, wsOpen = false, sent = 0;
let previewWs = null, previewWsOpen = false, lastPreview = 0, previewEncoding = false;
let gyro = { x: 0, y: 0, z: 0 };      // rad/s (DeviceMotion gives deg/s)
let gyroSeen = false;
let flowMag = 0, flowConf = 0;
let flowHist = [];
let prevGray = null;
let busy = false, lastProc = 0;

/* ---------------------------- uplink ---------------------------- */

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws/phone`;
}

function connect() {
  // Auto-reconnect with backoff: a dropped socket must never wedge the node.
  ws = new WebSocket(wsUrl());
  ws.onopen = () => { wsOpen = true; setDot("ws", true, "connected"); };
  ws.onclose = () => {
    wsOpen = false; setDot("ws", false, "reconnecting…");
    setTimeout(connect, 1000);
  };
  ws.onerror = () => { try { ws.close(); } catch (_) {} };
}

function connectPreview() {
  previewWs = new WebSocket(`${wsUrl().replace(/\/ws\/phone$/, "/ws/preview/phone")}`);
  previewWs.binaryType = "arraybuffer";
  previewWs.onopen = () => { previewWsOpen = true; };
  previewWs.onclose = () => {
    previewWsOpen = false;
    setTimeout(connectPreview, 1000);
  };
  previewWs.onerror = () => { try { previewWs.close(); } catch (_) {} };
}

function sendPreview(now) {
  if (!previewWsOpen || previewWs.readyState !== 1 || previewEncoding ||
      now - lastPreview < PREVIEW_PERIOD_MS || previewWs.bufferedAmount > 100000) return;
  lastPreview = now;
  previewEncoding = true;
  preview.toBlob(async (blob) => {
    try {
      if (blob && previewWsOpen && previewWs.readyState === 1 &&
          previewWs.bufferedAmount < 100000) {
        previewWs.send(await blob.arrayBuffer());
      }
    } finally {
      previewEncoding = false;
    }
  }, "image/jpeg", 0.58);
}

function sendSample() {
  if (!wsOpen || ws.readyState !== 1) return;
  const mag = Math.hypot(gyro.x, gyro.y, gyro.z);
  const sample = {
    t: Date.now() / 1000,
    gyro: { x: r4(gyro.x), y: r4(gyro.y), z: r4(gyro.z) },
    gyro_mag: r4(mag),
    flow_mag: r4(flowMag),
    flow_confidence: r4(flowConf),
    // The phone's own rail hint. Consumer APIs rarely expose a true rail,
    // so this may stay false forever — that is exactly why the demo's
    // primary trigger is deterministic synthetic injection on the server.
    raw_saturated: mag > 30.0,
  };
  ws.send(JSON.stringify(sample));
  $("v-sent").textContent = String(++sent);
}

const r4 = (x) => Math.round(x * 10000) / 10000;

/* ---------------------------- IMU ---------------------------- */

function onMotion(e) {
  const rr = e.rotationRate;
  if (!rr || rr.alpha === null) return;
  const D2R = Math.PI / 180;
  // NOTE: IMU values are used ONLY here (uplink + status UI), never in the
  // optical-flow functions below.
  gyro = { x: (rr.beta || 0) * D2R, y: (rr.gamma || 0) * D2R, z: (rr.alpha || 0) * D2R };
  if (!gyroSeen) { gyroSeen = true; setDot("imu", true, ""); }
  $("v-imu").textContent = Math.hypot(gyro.x, gyro.y, gyro.z).toFixed(2);
}

/* ------------------- camera rotation proxy (PIXELS ONLY) ------------------- */

const work = document.createElement("canvas");
work.width = PROC_W; work.height = PROC_H;
const wctx = work.getContext("2d", { willReadFrequently: true });

function grabGray() {
  // Step 1 (spec): downscale + grayscale. Coarse resolution is intentional.
  wctx.drawImage(video, 0, 0, PROC_W, PROC_H);
  const rgba = wctx.getImageData(0, 0, PROC_W, PROC_H).data;
  const g = new Uint8ClampedArray(PROC_W * PROC_H);
  for (let i = 0, j = 0; j < g.length; i += 4, j++) {
    g[j] = (rgba[i] * 3 + rgba[i + 1] * 4 + rgba[i + 2]) >> 3; // fast luma
  }
  return g;
}

function blockStats(g, cx, cy) {
  const h = BLOCK >> 1;
  let sum = 0, sum2 = 0, n = 0;
  for (let y = cy - h; y < cy + h; y++)
    for (let x = cx - h; x < cx + h; x++) {
      const v = g[y * PROC_W + x]; sum += v; sum2 += v * v; n++;
    }
  const mean = sum / n;
  return sum2 / n - mean * mean; // variance
}

function sad(prev, curr, cx, cy, dx, dy) {
  const h = BLOCK >> 1;
  let s = 0;
  for (let y = -h; y < h; y++) {
    const rowP = (cy + y) * PROC_W + cx;
    const rowC = (cy + y + dy) * PROC_W + cx + dx;
    for (let x = -h; x < h; x++) s += Math.abs(prev[rowP + x] - curr[rowC + x]);
  }
  return s;
}

function computeFlow(prev, curr) {
  // Step 2 (spec): sparse block matching over a fixed grid.
  const margin = (BLOCK >> 1) + SEARCH + 1;
  const vectors = [];
  let total = 0;
  for (let by = 0; by < GRID_Y; by++) {
    for (let bx = 0; bx < GRID_X; bx++) {
      total++;
      const cx = Math.round(margin + (PROC_W - 2 * margin) * (bx + 0.5) / GRID_X);
      const cy = Math.round(margin + (PROC_H - 2 * margin) * (by + 0.5) / GRID_Y);
      // Step 3a (spec): reject low-texture blocks — flat regions match anywhere.
      if (blockStats(prev, cx, cy) < TEXTURE_MIN) continue;
      let best = Infinity, bdx = 0, bdy = 0;
      for (let dy = -SEARCH; dy <= SEARCH; dy++)
        for (let dx = -SEARCH; dx <= SEARCH; dx++) {
          const s = sad(prev, curr, cx, cy, dx, dy);
          if (s < best) { best = s; bdx = dx; bdy = dy; }
        }
      // weak best match => unreliable block (blur, motion beyond window)
      if (best / (BLOCK * BLOCK) > 28) continue;
      vectors.push({ x: cx, y: cy, dx: bdx, dy: bdy });
    }
  }
  if (vectors.length < 4) return { mag: 0, conf: 0, vectors: [] };

  // Step 3b (spec): drop vectors far from the field median (moving objects,
  // lighting flicker, rolling-shutter artifacts).
  const med = (arr) => { const a = [...arr].sort((p, q) => p - q); return a[a.length >> 1]; };
  const mdx = med(vectors.map(v => v.dx)), mdy = med(vectors.map(v => v.dy));
  const kept = vectors.filter(v => Math.hypot(v.dx - mdx, v.dy - mdy) <= OUTLIER_DIST);
  if (kept.length < 4) return { mag: 0, conf: kept.length / total, vectors: kept };

  // Step 4 (spec): decompose translation vs rotation — this is what makes
  // it a ROTATION proxy rather than a generic motion detector.
  //   * pure pan / handshake => nearly uniform field: high mean vector,
  //     low structured variation
  //   * rotation about the view axis => swirl (curl); rotation about a
  //     perpendicular axis => structured gradient across the frame
  // We subtract the mean vector (the translation estimate) and use the
  // mean magnitude of the RESIDUAL field as the rotation proxy.
  const mx = kept.reduce((s, v) => s + v.dx, 0) / kept.length;
  const my = kept.reduce((s, v) => s + v.dy, 0) / kept.length;
  let residual = 0;
  for (const v of kept) residual += Math.hypot(v.dx - mx, v.dy - my);
  residual /= kept.length;

  return { mag: residual * FLOW_SCALE, conf: kept.length / total, vectors: kept };
}

function processFrame(now) {
  requestAnimationFrame(processFrame);
  // Step-budget rule: if we're behind, DROP this frame; never queue work.
  if (busy || now - lastProc < PROC_PERIOD_MS || video.readyState < 2) return;
  busy = true; lastProc = now;
  try {
    const gray = grabGray();
    if (prevGray) {
      const { mag, conf, vectors } = computeFlow(prevGray, gray);
      // Step 5 (spec): short moving average — stable enough to plot cleanly,
      // short enough to track a fast spin.
      flowHist.push(mag);
      if (flowHist.length > SMOOTH_N) flowHist.shift();
      flowMag = flowHist.reduce((a, b) => a + b, 0) / flowHist.length;
      flowConf = conf;
      drawPreview(gray, vectors);
      sendPreview(now);
      $("v-cam").textContent = flowMag.toFixed(2);
      $("v-conf").textContent = flowConf.toFixed(2);
      setDot("cam", conf > 0.3, "");
    }
    prevGray = gray;
  } finally {
    busy = false;
  }
}

function drawPreview(gray, vectors) {
  const img = pctx.createImageData(PROC_W, PROC_H);
  for (let j = 0, i = 0; j < gray.length; j++, i += 4) {
    img.data[i] = img.data[i + 1] = img.data[i + 2] = gray[j];
    img.data[i + 3] = 255;
  }
  pctx.putImageData(img, 0, 0);
  pctx.strokeStyle = "#5eead4"; pctx.lineWidth = 1;
  for (const v of vectors) {
    pctx.beginPath();
    pctx.moveTo(v.x, v.y);
    pctx.lineTo(v.x + v.dx * 3, v.y + v.dy * 3);
    pctx.stroke();
  }
}

/* ---------------------------- boot ---------------------------- */

function setDot(which, ok, txt) {
  $(`d-${which}`).className = "dot " + (ok ? "ok" : "bad");
  if (txt) $(`v-${which === "imu" ? "imu" : which === "cam" ? "cam" : "ws"}`).textContent = txt;
}

async function start() {
  $("err").textContent = "";
  try {
    // iOS 13+: motion permission must be requested from a user gesture over
    // HTTPS; anything else silently yields no events.
    if (typeof DeviceMotionEvent !== "undefined" &&
        typeof DeviceMotionEvent.requestPermission === "function") {
      const res = await DeviceMotionEvent.requestPermission();
      if (res !== "granted") throw new Error("Motion permission denied.");
    }
    window.addEventListener("devicemotion", onMotion);

    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "environment", width: { ideal: 640 }, height: { ideal: 480 } },
      audio: false,
    });
    video.srcObject = stream;
    await video.play();

    $("start").style.display = "none";
    connect();
    connectPreview();
    setInterval(sendSample, SEND_PERIOD_MS);
    requestAnimationFrame(processFrame);
    setTimeout(() => {
      if (!gyroSeen) $("err").textContent =
        "No gyro events yet — check that this page is HTTPS and motion access is allowed.";
    }, 3000);
  } catch (e) {
    $("err").textContent =
      `${e.message}\nBoth camera and motion require HTTPS and a user tap (iOS Safari).`;
  }
}

$("start").addEventListener("click", start);
