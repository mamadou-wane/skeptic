from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path

from skeptic.trace import TraceWriter


def verifier_revision(package_root: Path | None = None) -> str:
    """Content hash (12 hex) over every `*.py` under the skeptic package,
    sorted by relative path, hashing path and bytes. A dirty tree misses the
    cache.

    This is the VERIFY-verdict half of the two-key design (`skeptic.
    collector.COLLECTOR_VERSION` is the baseline-observation half): the
    VERIFY cache key hashes this, so any edit to a check or the aggregator
    re-verdicts every cached pair on the next run, with no re-collection.
    A collector behavior change does not move this hash at all (nothing here
    reads `skeptic/collector.py` differently), so it needs `COLLECTOR_VERSION`
    bumped by hand to invalidate a baseline cached under the old behavior.
    """
    root = package_root or Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


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
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            # a truncated write from a killed run is a miss; the stage
            # re-executes and overwrites it atomically
            return None

    def put(self, key: str, value: dict) -> None:
        path = self._path(key)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        tmp.replace(path)


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
    try:
        result = fn()
    except Exception:
        trace.event(stage=stage, actor="orchestrator", event="stage_error",
                    payload={"key": key})
        raise
    dur_ms = int((time.monotonic() - start) * 1000)
    cache.put(key, result)
    trace.event(stage=stage, actor="orchestrator", event="stage_end",
                payload={"key": key}, dur_ms=dur_ms)
    return result
