"""Structured trace: one JSON line per event (model call, tool call, guard, reply) per case, in var/traces/.

In production these would go to OpenTelemetry; JSONL keeps the demo dependency-free and easy to grep.
"""

import json
import time

from .config import VAR_DIR


class Tracer:
    def __init__(self, case_id: str):
        self.case_id = case_id
        self.path = VAR_DIR / "traces" / f"{case_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event: dict) -> None:
        record = {"ts": round(time.time(), 3), "case_id": self.case_id, **event}
        with open(self.path, "a") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
