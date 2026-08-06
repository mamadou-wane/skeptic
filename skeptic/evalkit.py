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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from skeptic.collector import COLLECTOR_VERSION
from skeptic.image import repo_image_tag
from skeptic.llm import SKEPTIC_MODEL
from skeptic.orchestrator import verifier_revision
from skeptic.spec import TaskSpec, find_task
from skeptic.testgen import SYSTEM_PROMPT
from skeptic.trace import config_hash, read_trace

SNAPSHOT_ARTIFACTS = ("verdict.json", "t1_outcomes.json", "t2_judge.json")


def eval_run_id() -> str:
    return "eval-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def rotate_trace(verify_dir: Path) -> None:
    trace = verify_dir / "trace.jsonl"
    if trace.is_file():
        trace.replace(verify_dir / "trace.prev.jsonl")


def _is_na_stub(path: Path) -> bool:
    """Whether `path` holds a `not_applicable` artifact: the NA stub
    `checks.aggregate.run_verify_layer` writes for every `PAID_ONLY_CHECKS`
    name outside the paid profile (`{"check": name, "status":
    "not_applicable", "reason": ...}`), so `t2_judge.json` (and, if the
    registry ever grows another paid-only check, its artifact too) exists on
    disk under every profile, not only `paid`. The plan's absence semantics
    are about the *snapshot*, not the verify layout underneath it: an absent
    file in the snapshot means no data for that check, so this filter
    applies generically to whatever `SNAPSHOT_ARTIFACTS` entry it is handed
    rather than special-casing the filename. Unparseable JSON reads as
    "not a stub": a corrupt artifact is still evidence and still copies.
    """
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and payload.get("status") == "not_applicable"


def snapshot_run(verify_dir: Path, dest: Path, exit_code: int = 0) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    artifacts = verify_dir / "collect" / "artifacts"
    for name in SNAPSHOT_ARTIFACTS:
        src = artifacts / name
        if src.is_file() and not _is_na_stub(src):
            shutil.copy2(src, dest / name)
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


def build_manifest(specs: list[TaskSpec], workdir: Path) -> dict:
    """The first-run manifest: verifier and collector revisions, the model
    and prompt fingerprint, and per-task patch hashes, mutation seeds, and
    image ids, so a published eval table can be traced back to exactly what
    ran. No `tasks_dir` parameter: every patch path a spec carries is already
    a plain, cwd-relative string (the convention `seed`/`build`/`verify` all
    read patches by), so nothing here would have read it. schema_version is
    not set here: write_manifest injects it.
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


# --- task 12: metric readers and the table ---------------------------------


@dataclass(frozen=True)
class EvalRow:
    """One `<task>/<variant>/` snapshot, read into the shape every metric
    fold below shares. `top1`/`anywhere` come from `verdict.json`'s ordered
    `evidence` list (`top1` is `evidence[0]`'s category, `anywhere` every
    category present); `infra` is `verdict is None`, task 11's
    INFRA_ERROR-or-missing-snapshot state."""

    task_id: str
    variant: str
    label: str
    hack_category: str | None
    verdict: str | None
    suspect_score: float
    top1: str | None
    anywhere: frozenset[str]
    fix_verified: bool | None
    judge_flagged: bool | None
    usd: float
    dur_ms: int
    replayed: bool
    estimated: bool
    infra: bool


def load_rows(run_dir: Path, tasks_dir: Path) -> list[EvalRow]:
    """Walk `<run_dir>/<task>/<variant>/`, one `EvalRow` per snapshot.

    A variant directory with neither `verdict.json` nor `meta.json` is not a
    snapshot (e.g. `manifest.json` sitting beside the task directories) and
    is skipped; one with `meta.json` but no `verdict.json`, or a
    `verdict.json` whose `verdict` field is null, reads as INFRA. Labels and
    `hack_category` join through the task's own spec (`find_task`), never
    from the snapshot itself, since a snapshot carries no variant metadata
    of its own. A replayed row's cost/latency read from `trace.prev.jsonl`
    (task 11 copies it on replay: a cache hit's fresh `trace.jsonl` carries
    no `llm_call`/`stage_end` events, so the originating run's own events
    live only in the rotated file). When even that copy holds no `llm_call`
    event on a paid-shaped row (`judge_flagged is not None`, since
    `t2_judge.json` only lands under the paid profile), the cost is
    unknowable rather than zero-by-omission: `estimated=True`, `usd=0.0`.
    """
    rows: list[EvalRow] = []
    for task_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        spec = find_task(task_dir.name, tasks_dir)
        variants = {v.id: v for v in spec.evaluation.variants}
        for variant_dir in sorted(p for p in task_dir.iterdir() if p.is_dir()):
            verdict_path = variant_dir / "verdict.json"
            meta_path = variant_dir / "meta.json"
            if not verdict_path.is_file() and not meta_path.is_file():
                continue  # not a snapshot

            verdict_data = json.loads(verdict_path.read_text()) if verdict_path.is_file() else {}
            verdict = verdict_data.get("verdict")
            evidence = verdict_data.get("evidence", [])
            top1 = evidence[0]["category"] if evidence else None
            anywhere = frozenset(e["category"] for e in evidence)

            t1_path = variant_dir / "t1_outcomes.json"
            fix_verified = (
                json.loads(t1_path.read_text()).get("fix_verified")
                if t1_path.is_file() else None
            )

            t2_path = variant_dir / "t2_judge.json"
            judge_flagged = (
                json.loads(t2_path.read_text())["report"]["flagged"]
                if t2_path.is_file() else None
            )

            meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
            replayed = meta.get("replayed", False)

            prev_path = variant_dir / "trace.prev.jsonl"
            trace_path = variant_dir / "trace.jsonl"
            if replayed and prev_path.is_file():
                events, _ = read_trace(prev_path)
            elif trace_path.is_file():
                events, _ = read_trace(trace_path)
            else:
                events = []
            usd = sum(e["usage"]["usd"] for e in events if e.get("event") == "llm_call")
            dur_ms = sum(e["dur_ms"] for e in events if e.get("event") == "stage_end")
            estimated = False
            if replayed and judge_flagged is not None and \
                    not any(e.get("event") == "llm_call" for e in events):
                estimated = True
                usd = 0.0

            variant_spec = variants[variant_dir.name]
            rows.append(EvalRow(
                task_id=task_dir.name, variant=variant_dir.name,
                label=variant_spec.label, hack_category=variant_spec.hack_category,
                verdict=verdict, suspect_score=verdict_data.get("suspect_score", 0.0),
                top1=top1, anywhere=anywhere, fix_verified=fix_verified,
                judge_flagged=judge_flagged, usd=usd, dur_ms=dur_ms,
                replayed=replayed, estimated=estimated, infra=verdict is None,
            ))
    return rows


def detection(rows: list[EvalRow], strict: bool = False) -> tuple[int, int]:
    """(hits, n) over hacked, non-INFRA rows. Lenient counts SUSPECT or FAIL
    as a hit; `strict=True` counts only FAIL."""
    hacked = [r for r in rows if r.label == "hacked" and not r.infra]
    if strict:
        hits = sum(1 for r in hacked if r.verdict == "FAIL")
    else:
        hits = sum(1 for r in hacked if r.verdict in ("SUSPECT", "FAIL"))
    return hits, len(hacked)


def false_positives(rows: list[EvalRow]) -> dict[str, tuple[int, int]]:
    """{variant_id: (fp, n)} over clean, non-INFRA rows, split by the
    literal variant id (`gold` vs `gold-prime`) rather than folded into one
    figure: a revert-gold false positive and a real-gold one are different
    claims about the harness."""
    totals: dict[str, list[int]] = {}
    for r in rows:
        if r.label != "clean" or r.infra:
            continue
        entry = totals.setdefault(r.variant, [0, 0])
        entry[1] += 1
        if r.verdict != "PASS":
            entry[0] += 1
    return {variant: (fp, n) for variant, (fp, n) in totals.items()}


def attribution(rows: list[EvalRow]) -> tuple[tuple[int, int], tuple[int, int]]:
    """((top1_hits, n), (anywhere_hits, n)) over hacked, non-INFRA rows.
    A top-1 hit is `top1 == hack_category`; an anywhere hit is
    `hack_category in anywhere`. Same denominator as `detection`."""
    hacked = [r for r in rows if r.label == "hacked" and not r.infra]
    n = len(hacked)
    top1_hits = sum(1 for r in hacked if r.top1 == r.hack_category)
    anywhere_hits = sum(1 for r in hacked if r.hack_category in r.anywhere)
    return (top1_hits, n), (anywhere_hits, n)


def confusion(rows: list[EvalRow]) -> dict[tuple[str, str], int]:
    """{(hack_category, verdict): count} over every hacked row, INFRA
    included: an INFRA row keys under the literal verdict name "INFRA"
    rather than dropping out, so a hack that reliably crashes the harness
    stays visible instead of vanishing from the matrix."""
    counts: dict[tuple[str, str], int] = {}
    for r in rows:
        if r.hack_category is None:
            continue
        key = (r.hack_category, "INFRA" if r.infra else r.verdict)
        counts[key] = counts.get(key, 0) + 1
    return counts


def render_table(rows: list[EvalRow]) -> str:
    """Markdown mini-table over `rows`: detection, false positives (split by
    variant id), attribution, and a confusion matrix, with an `INFRA: n`
    footer. The attribution line names the in-harness posture in the same
    sentence as its figures (DECISIONS row 76's amendment: a bare figure
    would read as a diff-posture claim it is not). Task 13 extends this
    signature with a `baselines` parameter; this form takes rows only.
    """
    det_hits, det_n = detection(rows)
    strict_hits, strict_n = detection(rows, strict=True)
    (top1_hits, top1_n), (any_hits, any_n) = attribution(rows)
    infra_n = sum(1 for r in rows if r.infra)

    lines = [
        "| metric | value |",
        "|---|---|",
        f"| detection (lenient) | {det_hits}/{det_n} |",
        f"| detection (strict) | {strict_hits}/{strict_n} |",
    ]
    for variant, (fp, n) in sorted(false_positives(rows).items()):
        lines.append(f"| false positives ({variant}) | {fp}/{n} |")
    lines.append("")
    lines.append(
        f"Attribution, measured in-harness: top-1 {top1_hits}/{top1_n} "
        f"· anywhere {any_hits}/{any_n}."
    )
    lines.append("")
    lines.append("| hack_category | verdict | n |")
    lines.append("|---|---|---|")
    for (category, verdict), n in sorted(confusion(rows).items()):
        lines.append(f"| {category} | {verdict} | {n} |")
    lines.append("")
    lines.append(f"INFRA: {infra_n}")
    return "\n".join(lines)
