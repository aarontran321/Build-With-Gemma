"""Virtual phone — a synthetic sensor node on the REAL live websocket path.

WHAT THIS IS (read before demoing it to anyone)
-----------------------------------------------
This is a stand-in for phone/capture.js, not a recording of a phone. It
speaks the exact PhoneSample wire format over the exact /ws/phone socket,
at the same ~20 Hz, so every byte downstream of the socket -- monitor,
state machine, Gemma, guardrail, dual descent, recorder -- runs the live
code path with no replay branch and no special casing. The server cannot
distinguish it from phone/capture.js, which is the point of the tool and
also the reason to LABEL IT HONESTLY when presenting: say "simulated
sensor node", never "the phone".

Why it exists, in order of usefulness:
  1. rehearse and time the landing demo without holding a phone
  2. develop/debug the live path (injection buttons, descent, arbitration)
     without HTTPS, a tunnel, or a second device
  3. a working fallback if the phone, the venue wifi, or the tunnel dies
     five minutes before a demo

WHAT IT PROVES, AND WHAT IT DOES NOT
------------------------------------
Proves: the two-stream contract is satisfiable and the whole pipeline
behaves correctly on data with the right STRUCTURE -- a calibrated rad/s
gyro stream and an independent, uncalibrated, self-scoring camera proxy
that agree in shape and trend until a fault separates them.

Does NOT prove: that the camera proxy in capture.js actually recovers
rotation from real pixels. Only the real phone shows that, because only
the real phone runs the block-matching estimator. This script models that
estimator's OUTPUT; it does not reimplement it. Keep that distinction in
the writeup.

MOTION MODEL
------------
The gyro stream is a gentle hand-held wobble (~1.2-2.2 rad/s), sitting
above config.MOTION_FLOOR so the monitor treats it as real motion rather
than "still". The camera proxy is derived from the same underlying motion
in the units the real estimator produces:

    f_target = (gyro_mag / GYRO_NORM_RAD_S) * FLOW_NORM

then given one frame of lag (the real estimator compares frame N to N-1)
and its OWN independent noise, so the two streams agree in shape and
trend without ever agreeing digit-for-digit. That is exactly the
relationship the monitor is tuned against, and it is the same model
scripts/make_golden.py uses for the committed golden runs.

Unlike make_golden.py, the emitted gyro VECTOR is self-consistent:
hypot(x, y, z) == gyro_mag exactly, so the payload survives inspection.

USAGE
-----
    # 1. clean stream; drive faults from the dashboard buttons
    python scripts/virtual_phone.py

    # 2. scripted end-to-end landing demo, no phone, no clicking
    python scripts/virtual_phone.py --auto gyro_saturation

    # 3. fault applied AT THE SOURCE, server told nothing (see caveat)
    python scripts/virtual_phone.py --source-fault gyro_saturation --fault-at 8
"""

import argparse
import asyncio
import json
import math
import os
import sys
import time
from typing import Optional

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import config
from server.inject import SCENARIOS, FaultInjector
from server.schemas import GyroVec, PhoneSample

SEND_HZ = 20.0          # matches SEND_PERIOD_MS = 50 in phone/capture.js
FLOW_LAG_ALPHA = 0.4    # one-frame lag of the proxy behind true motion
FLOW_NOISE = 0.04       # proxy noise, independent of the gyro's
GYRO_NOISE = 0.05       # rad/s
BASE_CONF = 0.85        # healthy flow confidence in a textured, lit scene


def hand_motion(rel: float) -> float:
    """Gentle hand-held wobble in rad/s (same profile as make_golden.py)."""
    return 1.6 + 0.4 * math.sin(0.9 * rel) + 0.15 * math.sin(3.7 * rel)


def spin_motion(rel: float) -> float:
    """Slower, larger sweeps — easier to see the trend on the dashboard."""
    return 2.0 + 1.4 * math.sin(0.45 * rel)


PROFILES = {"handheld": hand_motion, "spin": spin_motion,
            "still": lambda rel: 0.15}


class VirtualPhone:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.rng = np.random.default_rng(args.seed)
        self.motion = PROFILES[args.profile]
        self.prev_flow: Optional[float] = None
        # Source-applied faults reuse the SERVER's injector so the fault
        # shape is byte-identical to the dashboard button; only the place
        # it is applied differs.
        self.injector = FaultInjector() if args.source_fault else None
        self.injected_yet = False
        self.sent = 0

    def sample(self, rel: float, t: float) -> PhoneSample:
        g = max(0.05, self.motion(rel) + float(self.rng.normal(0, GYRO_NOISE)))

        # Camera proxy: same motion, proxy units, one frame of lag, own noise.
        f_target = (g / config.GYRO_NORM_RAD_S) * config.FLOW_NORM
        f = (f_target if self.prev_flow is None
             else (1 - FLOW_LAG_ALPHA) * self.prev_flow + FLOW_LAG_ALPHA * f_target)
        self.prev_flow = f
        f = max(0.02, f + float(self.rng.normal(0, FLOW_NOISE)))

        conf = float(np.clip(BASE_CONF + 0.05 * math.sin(1.3 * rel)
                             + self.rng.normal(0, 0.02), 0.0, 1.0))

        # Distribute the magnitude over a slowly tumbling axis so the vector
        # is self-consistent: hypot(x, y, z) == gyro_mag.
        ax, ay, az = math.sin(0.3 * rel), math.cos(0.21 * rel), 1.0
        n = math.sqrt(ax * ax + ay * ay + az * az)
        gyro = GyroVec(x=round(g * ax / n, 4), y=round(g * ay / n, 4),
                       z=round(g * az / n, 4))

        s = PhoneSample(t=t, gyro=gyro, gyro_mag=round(g, 4),
                        flow_mag=round(f, 4), flow_confidence=round(conf, 4),
                        raw_saturated=g > 30.0)

        if self.injector is not None:
            if not self.injected_yet and rel >= self.args.fault_at:
                self.injector.trigger(self.args.source_fault, t)
                self.injected_yet = True
                print(f"  [{rel:5.1f}s] source fault applied: "
                      f"{self.args.source_fault} (server told nothing)")
            s = self.injector.apply(s)
            # The injector annotates what it corrupted; a real phone has no
            # such metadata and the server strips these fields anyway. Drop
            # them here so the payload is exactly what a phone would send.
            s.injected = None
            s.clean_gyro_mag = s.clean_flow_mag = s.clean_flow_confidence = None
            s.raw_saturated = s.gyro_mag > 30.0
        return s

    def wire(self, s: PhoneSample) -> str:
        """Serialize exactly the fields phone/capture.js puts on the wire."""
        return json.dumps({
            "t": s.t,
            "gyro": {"x": s.gyro.x, "y": s.gyro.y, "z": s.gyro.z},
            "gyro_mag": s.gyro_mag,
            "flow_mag": s.flow_mag,
            "flow_confidence": s.flow_confidence,
            "raw_saturated": s.raw_saturated,
        })


async def server_inject(http_base: str, scenario: str) -> None:
    """Fire the dashboard's injection button over the HTTP API."""
    import httpx
    async with httpx.AsyncClient(verify=False, timeout=10.0) as c:
        r = await c.post(f"{http_base}/api/inject/{scenario}")
        body = r.json()
    if body.get("error"):
        print(f"  !! injection refused: {body['error']}")
    else:
        print(f"  ** server-side injection fired: {scenario}")


async def server_reset(http_base: str) -> None:
    import httpx
    async with httpx.AsyncClient(verify=False, timeout=10.0) as c:
        await c.post(f"{http_base}/api/reset")
    print("  ** session reset (descent back to 3700 m)")


def http_base_from_ws(url: str) -> str:
    base = url.replace("wss://", "https://").replace("ws://", "http://")
    return base.rsplit("/ws/phone", 1)[0]


async def run(args: argparse.Namespace) -> None:
    import websockets

    phone = VirtualPhone(args)
    http_base = http_base_from_ws(args.url)
    period = 1.0 / args.hz

    print("=" * 68)
    print("  VIRTUAL PHONE — SIMULATED sensor node, not a real device")
    print("=" * 68)
    print(f"  uplink   : {args.url}")
    print(f"  profile  : {args.profile} @ {args.hz:g} Hz  (seed {args.seed})")
    if args.auto:
        print(f"  scripted : reset, then inject '{args.auto}' at "
              f"t+{args.auto_after:g}s via the server API")
    if args.source_fault:
        print(f"  source fault: {args.source_fault} at t+{args.fault_at:g}s")
        print("  NOTE: a source-applied fault carries no ground truth, so the")
        print("        NAIVE vehicle will NOT crash. Monitor -> Gemma ->")
        print("        guardrail still run. See --help for why.")
    print("=" * 68)

    # ssl=False lets a self-signed wss:// dev cert through for local testing.
    kwargs = {}
    if args.url.startswith("wss://"):
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl"] = ctx

    async with websockets.connect(args.url, **kwargs) as ws:
        print("  connected.\n")
        if args.auto:
            await server_reset(http_base)

        t_start = time.time()
        next_send = t_start
        fired = False
        while True:
            now = time.time()
            rel = now - t_start
            if args.duration and rel >= args.duration:
                break

            await ws.send(phone.wire(phone.sample(rel, now)))
            phone.sent += 1

            if args.auto and not fired and rel >= args.auto_after:
                fired = True
                await server_inject(http_base, args.auto)

            if phone.sent % int(args.hz * 5) == 0:
                print(f"  [{rel:5.1f}s] {phone.sent} samples sent")

            next_send += period
            await asyncio.sleep(max(0.0, next_send - time.time()))

    print(f"\n  done: {phone.sent} samples over {rel:.1f}s")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Stream a simulated phone sensor node into /ws/phone.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    p.add_argument("--url", default="ws://127.0.0.1:8000/ws/phone",
                   help="phone websocket endpoint (ws:// or wss://)")
    p.add_argument("--hz", type=float, default=SEND_HZ,
                   help="uplink rate; capture.js sends 20 Hz")
    p.add_argument("--profile", choices=sorted(PROFILES), default="handheld",
                   help="motion profile of the simulated vehicle")
    p.add_argument("--seed", type=int, default=7, help="RNG seed")
    p.add_argument("--duration", type=float, default=0.0,
                   help="stop after N seconds (0 = run until Ctrl-C)")
    p.add_argument("--auto", choices=SCENARIOS, metavar="SCENARIO",
                   help="scripted demo: reset, then fire this SERVER-side "
                        "injection automatically (full landing story)")
    p.add_argument("--auto-after", type=float, default=8.0,
                   help="seconds after connect to fire --auto (default 8; "
                        "keep well under 45s or the vehicles land first)")
    p.add_argument("--source-fault", choices=SCENARIOS, metavar="SCENARIO",
                   help="corrupt the stream HERE instead of on the server; "
                        "proves the monitor detects faults it was not told "
                        "about (but the NAIVE descent cannot crash — no "
                        "ground truth to measure error against)")
    p.add_argument("--fault-at", type=float, default=8.0,
                   help="seconds after connect to apply --source-fault")
    args = p.parse_args()

    if args.auto and args.source_fault:
        p.error("--auto and --source-fault are mutually exclusive")

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\n  stopped.")


if __name__ == "__main__":
    main()
