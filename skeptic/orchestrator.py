from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from collections.abc import Callable
from pathlib import Path

from skeptic.errors import SkepticInfraError
from skeptic.trace import TraceWriter

CACHE_CONTRACT = "evaluation-integrity-1"


def verifier_revision(package_root: Path | None = None) -> str:
    """Hash current package source paths and bytes for execution/cache identity.

    Collector and verifier changes both move this fingerprint. Explicit cache
    and collector contract versions also prevent reuse of legacy layouts.
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
    """Versioned stage results bound to the required artifact bytes."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def get(self, key: str) -> dict | None:
        path = self._path(key)
        try:
            record = json.loads(path.read_text())
            if not isinstance(record, dict) or record.get("contract") != CACHE_CONTRACT:
                return None
            value, artifacts = record["value"], record["artifacts"]
            if not isinstance(value, dict) or not isinstance(artifacts, dict):
                return None
            for name, digest in artifacts.items():
                if artifact_digest(Path(name)) != digest:
                    return None
            return value
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def put(self, key: str, value: dict) -> None:
        try:
            artifacts = {str(Path(p).absolute()): artifact_digest(Path(p))
                         for p in value.get("_cache_artifacts", ())}
        except (OSError, ValueError, TypeError) as exc:
            raise SkepticInfraError(
                "Required cache artifacts could not be validated. "
                "Next: inspect the run's artifacts and retry with a fresh workdir.") from exc
        publish_cache_record(self._path(key), {
            "contract": CACHE_CONTRACT, "value": value, "artifacts": artifacts})


def publish_cache_record(path: Path, record: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
        temporary.replace(path)
    except (OSError, TypeError) as exc:
        raise SkepticInfraError(
            f"Cannot publish cache record {path}: {exc}. "
            "Next: inspect run-directory storage and retry with a fresh workdir.") from exc


def artifact_digest(path: Path) -> str:
    """Stream a regular cache dependency without following a final symlink."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(f"cache dependency {path} is not a regular file")
        return hashlib.file_digest(stream, "sha256").hexdigest()


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
    trace.event(stage=stage, actor="orchestrator", event="stage_end",
                payload={"key": key}, dur_ms=dur_ms)
    if "_evidence_plan" in result:
        from skeptic.evidence_bundle import finish_origin
        finish_origin(result["_evidence_plan"], trace)
    cache.put(key, result)
    return result
