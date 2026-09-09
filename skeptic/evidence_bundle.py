"""Portable decision evidence, separate from disposable execution trees.

Inputs are host-owned, admitted records. Digests bind bytes and detect damage;
they do not authenticate measurements produced by candidate code.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import uuid
import zlib
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath

from skeptic.artifacts import (
    COVERAGE_JSON_MAX,
    STRUCTURED_MAX,
    _open_source,
    _parts,
    _publish_chunks,
    publish_artifact_bytes,
    read_artifact_bytes,
)
from skeptic.errors import SkepticInfraError
from skeptic.orchestrator import artifact_digest, publish_cache_record

VERSION = 1
PLAN = "evidence-plan.json"
OMISSIONS = [
    {
        "kind": "workspaces",
        "reason": "Disposable pristine, seeded, candidate and mutant trees; pinned inputs and patches are retained.",
    },
    {
        "kind": "environments",
        "reason": "Venvs, containers, images and caches are reproducible intermediates; identities and dependency pins are retained.",
    },
]


def _error(detail: str) -> SkepticInfraError:
    return SkepticInfraError(
        f"Evidence export: {detail}. Next: preserve the source records and export to a fresh directory."
    )


def _name(name: str) -> None:
    _parts(name)
    if PureWindowsPath(name).drive or "\\" in name or name == "index.json":
        raise _error(f"unsupported bundle path {name!r}")


def _stream(path: Path):
    fd = _open_source(path.parent, path.name, required=True)
    with os.fdopen(fd, "rb") as source:
        while chunk := source.read(65536):
            yield chunk


def begin_record(run: Path) -> None:
    """Retire stale success inventories before any new execution can fail."""
    try:
        run.mkdir(parents=True, exist_ok=True)
        write_plan(run, {"version": VERSION, "status": "incomplete", "files": {}})
    except OSError as exc:
        raise _error(f"cannot start evidence record: {exc}") from exc


def source_plan(files: dict[str, Path], *, status: str = "complete") -> dict:
    records = {}
    for name, path in sorted(files.items()):
        _name(name)
        try:
            records[name] = {"source": str(path.resolve()), "sha256": artifact_digest(path)}
        except (OSError, ValueError) as exc:
            raise _error(f"cannot record required file {path}: {exc}") from exc
    return {"version": VERSION, "status": status, "files": records}


def write_plan(run: Path, plan: dict) -> None:
    publish_cache_record(run / PLAN, plan)


def tree_files(root: Path, prefix: str) -> dict[str, Path]:
    """Enumerate only a declared evidence root, never a whole workdir."""
    if not root.exists():
        return {}
    files = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise _error(f"symlink in evidence root: {path}")
        if path.is_file():
            files[f"{prefix}/{path.relative_to(root).as_posix()}"] = path
    return files


def write_settings(spec, input_root: Path) -> None:
    settings = input_root / "task.json"
    payload = json.loads(spec.model_dump_json())

    def portable(value):
        if isinstance(value, dict):
            return {k: portable(v) for k, v in value.items()}
        if isinstance(value, list):
            return [portable(v) for v in value]
        if isinstance(value, str) and value.startswith(str(input_root) + "/"):
            return "inputs/" + value[len(str(input_root)) + 1 :]
        return value

    if (input_root / "acceptance").is_dir() and payload.get("acceptance_suite"):
        payload["acceptance_suite"]["path"] = "inputs/acceptance"
    try:
        settings.write_text(json.dumps(portable(payload), sort_keys=True, indent=2) + "\n")
    except OSError as exc:
        raise _error(f"cannot record task inputs: {exc}") from exc


def decision_plan(run: Path, spec, input_root: Path, files: dict[str, Path], trace) -> dict:
    """Freeze settings and the originating trace beside captured input bytes."""
    write_settings(spec, input_root)
    from skeptic.checks.aggregate import SUSPECT_THRESHOLD, WEIGHTS
    from skeptic.collector import COLLECTOR_VERSION
    from skeptic.orchestrator import verifier_revision
    from skeptic.run_inputs import toolchain_identity

    publish_cache_record(
        input_root / "evaluator.json",
        {
            "verifier_revision": verifier_revision(),
            "collector_version": COLLECTOR_VERSION,
            "weights": WEIGHTS,
            "suspect_threshold": SUSPECT_THRESHOLD,
            "toolchain": toolchain_identity(),
        },
    )
    origin = input_root / "origin-trace.jsonl"
    with export_errors():
        origin.write_bytes(trace.path.read_bytes())
    files = {**files, **tree_files(input_root, "inputs"), "origin-trace.jsonl": origin}
    for line in origin.read_text().splitlines():
        event = json.loads(line)
        if event.get("event") == "model_record":
            path = run / event["payload"]["path"]
            if artifact_digest(path) != event["payload"]["sha256"]:
                raise _error("model record changed after capture")
            files[f"model/{path.name}"] = path
    return source_plan(files)


def _export_file(root: Path, name: str, entry: dict) -> dict:
    _name(name)
    source = Path(entry["source"])
    compressed = (source.stat().st_size > 1024 * 1024 and not name.endswith(".jsonl")) or Path(
        name
    ).name in (".coverage", "coverage.json")
    storage = name + ".gz" if compressed else name
    digest = hashlib.sha256()
    total = 0
    compressor = zlib.compressobj(level=6, wbits=31) if compressed else None

    def chunks():
        nonlocal total
        for chunk in _stream(source):
            total += len(chunk)
            if total > COVERAGE_JSON_MAX:
                raise _error(f"{name} exceeds the admitted measurement limit")
            digest.update(chunk)
            yield compressor.compress(chunk) if compressor else chunk
        if digest.hexdigest() != entry["sha256"]:
            raise _error(f"{name} differs from the recorded decision evidence")
        if compressor:
            yield compressor.flush()

    _publish_chunks(root, storage, COVERAGE_JSON_MAX + STRUCTURED_MAX, chunks())
    return {
        "storage": storage,
        "encoding": "gzip" if compressed else "identity",
        "sha256": digest.hexdigest(),
        "bytes": total,
        "stored_sha256": artifact_digest(root / storage),
    }


def export_bundle(plan: dict, root: Path) -> dict:
    try:
        if plan["version"] != VERSION or plan["status"] not in ("complete", "incomplete"):
            raise _error("unrecognized source inventory")
        if root.exists() or root.is_symlink():
            raise _error("destination already exists")
        root.mkdir(parents=True)
        records = {}
        for name, entry in sorted(plan["files"].items()):
            records[name] = _export_file(root, name, entry)
        index = {
            "version": VERSION,
            "status": plan["status"],
            "files": records,
            "omissions": OMISSIONS,
            "metadata": plan.get("metadata", {}),
            "summaries": plan.get("summaries", []),
        }
        publish_artifact_bytes(
            root, "index.json", json.dumps(index, sort_keys=True, indent=2).encode(), STRUCTURED_MAX
        )
        return index
    except (OSError, ValueError, KeyError, TypeError, AttributeError, zlib.error) as exc:
        raise _error(f"incomplete export ({exc})") from exc


def validate_bundle(root: Path, *, complete: bool = True) -> dict:
    try:
        index = json.loads(read_artifact_bytes(root, "index.json", STRUCTURED_MAX))
        if index["version"] != VERSION or (complete and index["status"] != "complete"):
            raise _error("bundle is not a complete current-contract export")
        if index["status"] not in ("complete", "incomplete"):
            raise _error("unknown completeness status")
        for name, record in index["files"].items():
            if record["encoding"] not in ("gzip", "identity"):
                raise _error("unknown artifact encoding")
            _name(name)
            _name(record["storage"])
            path = root / record["storage"]
            if artifact_digest(path) != record["stored_sha256"]:
                raise _error(f"changed stored artifact {name}")
            fd = _open_source(root, record["storage"], required=True)
            with os.fdopen(fd, "rb") as raw:
                stream = gzip.GzipFile(fileobj=raw) if record["encoding"] == "gzip" else raw
                digest, count = hashlib.sha256(), 0
                while chunk := stream.read(65536):
                    count += len(chunk)
                    if count > min(record["bytes"], COVERAGE_JSON_MAX):
                        raise _error(f"invalid expanded length for {name}")
                    digest.update(chunk)
                if count != record["bytes"] or digest.hexdigest() != record["sha256"]:
                    raise _error(f"changed decision artifact {name}")
        return index
    except (OSError, ValueError, KeyError, TypeError, AttributeError, zlib.error) as exc:
        raise _error(f"unreadable bundle ({exc})") from exc


def recorded_call(client, kwargs: dict, trace, *, stage: str, actor: str):
    """Capture application payloads only, without credentials or HTTP headers."""
    directory = trace.path.parent / "model-io"
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _error(f"cannot create model record directory: {exc}") from exc
    name = uuid.uuid4().hex
    request_path = directory / f"{name}-request.json"

    def jsonable(value):
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if hasattr(value, "__dict__"):
            return vars(value)
        raise TypeError(f"Unsupported model payload: {type(value).__name__}")

    def record(path, value):
        try:
            data = json.dumps(value, default=jsonable, sort_keys=True, indent=2).encode()
        except (TypeError, ValueError) as exc:
            raise _error(f"cannot serialize model payload: {exc}") from exc
        publish_artifact_bytes(directory, path.name, data, STRUCTURED_MAX)
        trace.event(
            stage=stage,
            actor=actor,
            event="model_record",
            payload={
                "path": path.relative_to(trace.path.parent).as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(),
            },
        )

    record(request_path, kwargs)
    try:
        response = client.messages.create(**kwargs)
    except Exception as exc:
        record(directory / f"{name}-error.json", {"error_type": type(exc).__name__})
        raise
    record(directory / f"{name}-response.json", response)
    return response


def validate_snapshot(directory: Path) -> None:
    """Current exports require their commit marker; legacy snapshots stay readable."""
    try:
        meta = json.loads((directory / "meta.json").read_text())
        if "evidence_version" not in meta:
            if (directory / "evidence").exists():
                raise _error("new bundle has lost its version marker")
            return
        if meta["evidence_version"] != VERSION:
            raise _error("unknown evidence version")
        root = directory / "evidence"
        if artifact_digest(root / "index.json") != meta["evidence_index_sha256"]:
            raise _error("missing or changed bundle index")
        index = validate_bundle(root, complete=meta["exit_code"] != 3)
        if index.get("metadata") != {k: v for k, v in meta.items() if k != "evidence_index_sha256"}:
            raise _error("snapshot metadata differs from its bundle")
        for name in index.get("summaries", []):
            _name(name)
            record = index["files"].get(name)
            if record is None or artifact_digest(directory / name) != record["sha256"]:
                raise _error(f"changed snapshot summary {name}")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, zlib.error) as exc:
        raise _error(f"unreadable snapshot ({exc})") from exc


def finish_origin(plan: dict, trace) -> None:
    record = plan["files"]["origin-trace.jsonl"]
    path = Path(record["source"])
    try:
        path.write_bytes(trace.path.read_bytes())
        digest = artifact_digest(path)
    except OSError as exc:
        raise _error(f"cannot complete originating trace: {exc}") from exc
    for entry in plan["files"].values():
        if entry["source"] == str(path):
            entry["sha256"] = digest


@contextmanager
def suite_record(run: Path, inputs: Path, reports: Path, spec):
    """Keep available admitted diagnostics even when acceptance cannot complete."""
    begin_record(run)
    status = "incomplete"
    try:
        yield
        status = "complete"
    finally:
        write_settings(spec, inputs)
        files = {**tree_files(inputs, "inputs"), **tree_files(reports, "reports")}
        write_plan(run, source_plan(files, status=status))


@contextmanager
def export_errors():
    """Keep storage/format failures distinct from candidate verdict exits."""
    try:
        yield
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise _error(f"storage or inventory failure: {exc}") from exc
