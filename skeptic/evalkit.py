"""Evalkit: the eval driver, snapshots, and (tasks 12-13) the metric readers.

Pure functions of what verify leaves on disk, per the plan's "report,
evalkit, and ledger are pure functions of the stream". The driver exists
because verify's own layout cannot hold history: its run_id is deterministic
per (task, variant) and trace.jsonl opens in append mode, so two runs of the
same pair share one id and one growing file. rotate-before, snapshot-after
is what makes each snapshot hold exactly one run.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from skeptic.collector import COLLECTOR_VERSION
from skeptic.image import repo_image_tag
from skeptic.llm import SKEPTIC_MODEL
from skeptic.orchestrator import verifier_revision
from skeptic.spec import TaskSpec
from skeptic.testgen import SYSTEM_PROMPT
from skeptic.trace import config_hash, read_trace

SNAPSHOT_ARTIFACTS = ("verdict.json", "t1_outcomes.json", "t2_judge.json")


def eval_run_id() -> str:
    return "eval-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def rotate_trace(verify_dir: Path) -> None:
    trace = verify_dir / "trace.jsonl"
    if trace.is_file():
        trace.replace(verify_dir / "trace.prev.jsonl")


def snapshot_run(verify_dir: Path, dest: Path, exit_code: int = 0) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    artifacts = verify_dir / "collect" / "artifacts"
    for name in SNAPSHOT_ARTIFACTS:
        if (artifacts / name).is_file():
            shutil.copy2(artifacts / name, dest / name)
    trace = verify_dir / "trace.jsonl"
    replayed = False
    if trace.is_file():
        shutil.copy2(trace, dest / "trace.jsonl")
        events, _ = read_trace(trace)
        replayed = any(e.get("event") == "stage_cached" for e in events)
    prev = verify_dir / "trace.prev.jsonl"
    if replayed and prev.is_file():
        # a cache hit's fresh trace carries no llm_call/stage_end events;
        # the originating run's live in the rotated file
        shutil.copy2(prev, dest / "trace.prev.jsonl")
    meta = {"exit_code": exit_code, "replayed": replayed,
            "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    (dest / "meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    return meta


def _image_id(spec: TaskSpec, workdir: Path) -> str:
    """`workdir/<task>/build/result.json`'s `image_id` when a BUILD run left
    one, else the image tag computed straight from the spec: a task that was
    only ever seeded and verified (never built) still gets a real,
    reproducible id."""
    result_path = workdir / spec.task_id / "build" / "result.json"
    if result_path.is_file():
        image_id = json.loads(result_path.read_text()).get("image_id")
        if image_id:
            return image_id
    return repo_image_tag(spec)


def build_manifest(specs: list[TaskSpec], tasks_dir: Path, workdir: Path) -> dict:
    """The first-run manifest: verifier and collector revisions, the model
    and prompt fingerprint, and per-task patch hashes, mutation seeds, and
    image ids, so a published eval table can be traced back to exactly what
    ran. `tasks_dir` is accepted for parity with the rest of the CLI's
    `--tasks-dir` plumbing; every patch path a spec carries is already a
    plain repo-relative string (the convention `seed`/`build`/`verify` all
    read patches by, cwd-relative rather than tasks_dir-relative), so nothing
    here re-reads it directly. schema_version is not set here: write_manifest
    injects it.
    """
    tasks = {}
    for spec in specs:
        tasks[spec.task_id] = {
            "seed": hashlib.sha256(Path(spec.seed.bug_patch).read_bytes()).hexdigest(),
            "variants": {
                variant.id: hashlib.sha256(Path(variant.patch).read_bytes()).hexdigest()
                for variant in spec.evaluation.variants
            },
            "mutation_seed": spec.verification.mutation.seed,
            "image_id": _image_id(spec, workdir),
        }
    return {
        "verifier_revision": verifier_revision(),
        "collector_version": COLLECTOR_VERSION,
        "model": SKEPTIC_MODEL,
        "prompt_hash": config_hash({"system": SYSTEM_PROMPT}),
        "tasks": tasks,
    }
