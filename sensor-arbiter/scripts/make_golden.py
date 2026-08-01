"""Generate the committed golden runs (deterministic, seeded).

Each golden file is a synthetic-but-realistic hand-motion sensor trace with
a fault injected through the EXACT SAME FaultInjector code path the live
demo uses. Replay feeds these samples through the real pipeline (monitor ->
arbiter -> guardrail -> descent), so nothing downstream is pre-recorded:
Gemma (or the fallback) genuinely diagnoses each replayed conflict.

Honesty note (also in WRITEUP.md): these traces are synthesized hand-motion
profiles, not recordings of a physical phone. The team can re-record real
sessions live (every session is recorded to sessions/); these committed
files exist so the judged demo is reproducible on any machine, offline.

Usage: python -m scripts.make_golden   (from the repo root)
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import config
from server.inject import FaultInjector
from server.recorder import write_golden
from server.schemas import GyroVec, PhoneSample

HZ = 25
DURATION_S = 60.0
INJECT_AT_S = 10.0
BASE_T = 1730500000.0  # fixed epoch base so runs are byte-stable


def hand_motion(t: float, rng_noise: float) -> float:
    """Gentle hand-held wobble, a few tenths to ~2 rad/s."""
    return (1.6 + 0.4 * math.sin(0.9 * t) + 0.15 * math.sin(3.7 * t)
            + rng_noise)


def synthesize(scenario: str, seed: int):
    rng = np.random.default_rng(seed)
    injector = FaultInjector()
    samples = []
    n = int(DURATION_S * HZ)
    triggered = False
    prev_flow = None
    for i in range(n):
        rel = i / HZ
        t = BASE_T + rel
        g = max(0.05, hand_motion(rel, float(rng.normal(0, 0.05))))
        # camera proxy follows the same motion SHAPE (uncalibrated units),
        # with its own noise and one frame of lag — like the real estimator
        f_target = (g / config.GYRO_NORM_RAD_S) * config.FLOW_NORM
        f = f_target if prev_flow is None else 0.6 * prev_flow + 0.4 * f_target
        prev_flow = f
        f = max(0.02, f + float(rng.normal(0, 0.04)))
        conf = float(np.clip(0.85 + 0.05 * math.sin(1.3 * rel)
                             + rng.normal(0, 0.02), 0.0, 1.0))
        s = PhoneSample(t=t, gyro=GyroVec(x=0.1 * g, y=0.2 * g, z=g),
                        gyro_mag=g, flow_mag=f, flow_confidence=conf)
        if not triggered and rel >= INJECT_AT_S:
            injector.trigger(scenario, t)
            triggered = True
        s = injector.apply(s)
        samples.append(s)
    return samples


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = os.path.join(root, config.DATA_DIR)
    os.makedirs(out, exist_ok=True)
    runs = [
        ("gyro_saturation", 101, "golden_gyro_saturation.jsonl"),
        ("camera_dark", 202, "golden_camera_dark.jsonl"),
        ("transient", 303, "golden_transient.jsonl"),
    ]
    for scenario, seed, fname in runs:
        samples = synthesize(scenario, seed)
        meta = {
            "scenario": scenario,
            "hz": HZ,
            "duration_s": DURATION_S,
            "inject_at_s": INJECT_AT_S,
            "seed": seed,
            "synthetic_profile": True,
            "note": ("deterministic synthetic hand-motion trace; fault applied "
                     "via the live FaultInjector code path; replay runs the "
                     "full live pipeline on these samples"),
        }
        path = os.path.join(out, fname)
        write_golden(path, meta, samples)
        n_inj = sum(1 for s in samples if s.injected)
        print(f"wrote {fname}: {len(samples)} samples, {n_inj} injected")


if __name__ == "__main__":
    main()
