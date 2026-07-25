from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

from skeptic.trace import TraceWriter


class StageCache:
    """Content-keyed cache for stage results. Unwired until M2: DECISIONS.md #67."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def get(self, key: str) -> dict | None:
        path = self._path(key)
        if not path.is_file():
            return None
        return json.loads(path.read_text())

    def put(self, key: str, value: dict) -> None:
        self._path(key).write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def run_stage(
    cache: StageCache,
    stage: str,
    key: str,
    fn: Callable[[], dict],
    trace: TraceWriter,
) -> dict:
    cached = cache.get(key)
    if cached is not None:
        trace.event(stage=stage, actor="orchestrator", event="stage_cached",
                    payload={"key": key})
        return cached
    trace.event(stage=stage, actor="orchestrator", event="stage_start",
                payload={"key": key})
    start = time.monotonic()
    result = fn()
    dur_ms = int((time.monotonic() - start) * 1000)
    cache.put(key, result)
    trace.event(stage=stage, actor="orchestrator", event="stage_end",
                payload={"key": key}, dur_ms=dur_ms)
    return result
