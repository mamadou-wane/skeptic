from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def config_hash(config: dict) -> str:
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


class TraceWriter:
    def __init__(self, path: Path, run_id: str, task_id: str) -> None:
        self.path = path
        self.run_id = run_id
        self.task_id = task_id
        path.parent.mkdir(parents=True, exist_ok=True)

    def event(
        self,
        stage: str,
        actor: str,
        event: str,
        payload: dict | None = None,
        usage: dict | None = None,
        dur_ms: int | None = None,
        variant: str | None = None,
    ) -> None:
        record: dict = {
            "schema_version": SCHEMA_VERSION,
            "ts": _now_iso(),
            "run_id": self.run_id,
            "task_id": self.task_id,
            "stage": stage,
            "actor": actor,
            "event": event,
        }
        if variant is not None:
            record["variant"] = variant
        if payload is not None:
            record["payload"] = payload
        if usage is not None:
            record["usage"] = usage
        if dur_ms is not None:
            record["dur_ms"] = dur_ms
        with self.path.open("a") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")


def read_trace(path: Path) -> tuple[list[dict], int]:
    events: list[dict] = []
    skipped = 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            skipped += 1
    return events, skipped


def write_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {**manifest, "schema_version": SCHEMA_VERSION}
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
