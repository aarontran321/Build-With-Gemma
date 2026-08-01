"""Session recording and golden-run replay.

Every live or replayed session is recorded as JSONL events so any run can
become a committed golden run. Golden files in data/ contain a meta line
followed by sample lines; REPLAY feeds those samples through the exact
same pipeline as live ingest (monitor -> arbiter -> guardrail -> descent),
so a replay is a real end-to-end run of the system on recorded sensor
input — only the sensor source differs, and the dashboard labels it
REPLAY (hard constraint 6 / required success paths).
"""

import json
import os
import time
from typing import Iterator, List, Optional, Tuple

from .schemas import PhoneSample


class SessionRecorder:
    """Append-only JSONL event log for one session."""

    def __init__(self, directory: str, label: str = "session") -> None:
        os.makedirs(directory, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self.path = os.path.join(directory, f"{label}_{stamp}.jsonl")
        self._fh = open(self.path, "a", encoding="utf-8")

    def event(self, etype: str, payload: dict) -> None:
        rec = {"type": etype, **payload}
        self._fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


def write_golden(path: str, meta: dict, samples: List[PhoneSample]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "meta", **meta}, separators=(",", ":")) + "\n")
        for s in samples:
            rec = {"type": "sample", **s.model_dump(exclude_none=True)}
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")


def load_golden(path: str) -> Tuple[dict, List[PhoneSample]]:
    meta: dict = {}
    samples: List[PhoneSample] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("type") == "meta":
                meta = rec
            elif rec.get("type") == "sample":
                rec.pop("type")
                samples.append(PhoneSample.model_validate(rec))
    return meta, samples


def timed_replay(samples: List[PhoneSample], speed: float = 1.0
                 ) -> Iterator[Tuple[float, PhoneSample]]:
    """Yield (delay_seconds, sample): the caller sleeps `delay` then feeds
    the sample. speed>1 accelerates (used by tests); the on-stage replay
    runs at 1.0 so the timeline is honest."""
    prev: Optional[float] = None
    for s in samples:
        delay = 0.0 if prev is None else max(0.0, (s.t - prev) / max(speed, 1e-6))
        prev = s.t
        yield delay, s
