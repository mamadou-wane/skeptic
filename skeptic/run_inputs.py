"""Material execution identity and host-owned snapshots of file inputs."""
import hashlib
import importlib.metadata
import platform
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from skeptic import orchestrator
from skeptic.errors import SkepticInfraError
from skeptic.spec import TaskSpec


def file_hash(path: Path | str | None) -> str | None:
    if path is None:
        return None
    try:
        with Path(path).open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError as exc:
        raise SkepticInfraError(f"Cannot read evaluation input {path}: {exc}. "
                                "Next: restore the input before running evaluation.") from exc


def toolchain_identity() -> dict:
    versions = {}
    for name in ("pydantic", "PyYAML", "defusedxml", "iniconfig", "pytest", "coverage",
                 "anthropic", "typer", "setuptools"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    for command in ("git", "docker"):
        try:
            result = subprocess.run([command, "--version"], capture_output=True, text=True,
                                    timeout=5, check=False)
            versions[command] = result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            versions[command] = None
    return {"python": platform.python_version(), "implementation": platform.python_implementation(),
            "os": platform.system(), "machine": platform.machine(), "sqlite": sqlite3.sqlite_version,
            "versions": versions}


def execution_identity(spec: TaskSpec, image_id: str) -> dict:
    if not image_id:
        raise SkepticInfraError("No resolved execution image was supplied. "
                                "Next: prepare and resolve the image before cache lookup.")
    return {
        "contract": orchestrator.CACHE_CONTRACT, "implementation": orchestrator.verifier_revision(),
        "toolchain": toolchain_identity(), "repo": spec.repo.model_dump(), "image_id": image_id,
        "seed": {**spec.seed.model_dump(), "bug_patch": file_hash(spec.seed.bug_patch)},
        "environment": {**spec.environment.model_dump(),
                        "constraints": file_hash(spec.environment.constraints_file)},
    }


def clean_reference_identity(spec: TaskSpec) -> list[dict]:
    return [{"id": v.id, "sha256": file_hash(v.patch)}
            for v in spec.evaluation.variants if v.label == "clean"]


def capture_bytes(data: bytes, root: Path, name: str) -> Path:
    destination = root / name
    try:
        with destination.open("xb") as stream:
            stream.write(data)
        destination.chmod(0o400)
    except OSError as exc:
        raise SkepticInfraError(
            f"Cannot publish captured input {destination}: {exc}. "
            "Next: use a writable, fresh host-owned input directory.") from exc
    return destination


def capture_file(source: Path, root: Path, name: str) -> Path:
    try:
        return capture_bytes(source.read_bytes(), root, name)
    except OSError as exc:
        raise SkepticInfraError(f"Cannot capture evaluation input {source}: {exc}. "
                                "Next: supply readable inputs and a fresh host-owned directory.") from exc


def freeze_inputs(spec: TaskSpec, parent: Path, *, variants: bool = True) -> tuple[TaskSpec, Path]:
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="inputs-", dir=parent))
    updates = {}
    if spec.seed.bug_patch is not None:
        patch = capture_file(Path(spec.seed.bug_patch), root, "seed.diff")
        updates["seed"] = spec.seed.model_copy(update={"bug_patch": str(patch)})
    if spec.environment.constraints_file is not None:
        pin = capture_file(spec.environment.constraints_file, root, "constraints.txt")
        updates["environment"] = spec.environment.model_copy(update={"constraints": str(pin)})
    if variants:
        copied = [v.model_copy(update={"patch": str(capture_file(Path(v.patch), root, f"v{i}.diff"))})
                  for i, v in enumerate(spec.evaluation.variants)]
        updates["evaluation"] = spec.evaluation.model_copy(update={"variants": copied})
    return spec.model_copy(update=updates), root
