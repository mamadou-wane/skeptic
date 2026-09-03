#!/usr/bin/env python3
"""Ten paid sweeps, one row per run, never pooled.

Issue #33 (b), pre-registered in DECISIONS row 243: five paid Eval A sweeps
over the 65-row corpus and five paid holdout sweeps over the 11 registry
rows, each from a fresh workdir because the verify cache is content-keyed
and would replay the first draw. Sweep a1 is the current evaluation
headline, pre-registered as the next release's snapshot. This script
reads the ten committed runs through `skeptic.evalkit.load_rows` and prints:

1. Provenance: the one `verifier_revision` and `collector_version` every
   manifest names, and the total spend.
2. Eval A, one row per sweep: INFRA, lenient and strict detection, the three
   clean splits on their own denominators (rows 147, 218, 234), spend.
3. The holdout, one row per sweep: INFRA, lenient, strict, spend.
4. Per-row stability, one table per corpus: every (task, variant) whose
   verdict differs between draws, with the verdicts in sweep order, and a
   count of the rows that read the same in all five.

docs/evaluation.md quotes the output verbatim; `tests/test_paid_repeats.py`
binds the two and recomputes every cell from the raw `verdict.json` files.
"""
from __future__ import annotations

import json
from pathlib import Path

from skeptic import evalkit
from skeptic.errors import SkepticInfraError

REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = REPO_ROOT / "tasks"
RUNS_DIR = REPO_ROOT / "evals" / "v1" / "runs"
REGISTRY = REPO_ROOT / "evals" / "v1" / "holdout" / "registry.yaml"

EVAL_A = (
    ("a1", "eval-20260902-164059"),
    ("a2", "eval-20260902-201826"),
    ("a3", "eval-20260903-001440"),
    ("a4", "eval-20260903-042605"),
    ("a5", "eval-20260903-082536"),
)
HOLDOUT = (
    ("h1", "eval-20260903-130514"),
    ("h2", "eval-20260903-133829"),
    ("h3", "eval-20260903-141024"),
    ("h4", "eval-20260903-144515"),
    ("h5", "eval-20260903-151849"),
)
CLEAN_SPLITS = ("gold", "gold-prime", "gold-large")


def _run_dir(run: str) -> Path:
    path = RUNS_DIR / run
    if not (path / "manifest.json").is_file():
        raise SkepticInfraError(
            f"{path} is not a committed run directory. Next: check the run id "
            f"in EVAL_A or HOLDOUT against evals/v1/runs/."
        )
    return path


def _rows(run: str, registry=None) -> list[evalkit.EvalRow]:
    return evalkit.load_rows(_run_dir(run), TASKS_DIR, registry=registry)


def _cell(figure: tuple[int, int]) -> str:
    hits, n = figure
    return f"{hits}/{n}"


def _spend(rows: list[evalkit.EvalRow]) -> str:
    return f"${sum(r.usd for r in rows):.4f}"


def render_provenance() -> str:
    verifiers, collectors = set(), set()
    for _, run in EVAL_A + HOLDOUT:
        manifest = json.loads((_run_dir(run) / "manifest.json").read_text())
        verifiers.add(manifest["verifier_revision"])
        collectors.add(str(manifest["collector_version"]))
    if len(verifiers) != 1 or len(collectors) != 1:
        raise SkepticInfraError(
            f"the ten runs do not share one harness: verifier {sorted(verifiers)}, "
            f"collector {sorted(collectors)}. Next: every sweep in one repeat set "
            f"must run at the same commit; check the manifests."
        )
    registry = evalkit.load_holdout_registry(REGISTRY)
    total = sum(r.usd for _, run in EVAL_A for r in _rows(run))
    total += sum(r.usd for _, run in HOLDOUT for r in _rows(run, registry))
    return (f"verifier_revision {verifiers.pop()}, collector_version {collectors.pop()}, "
            f"ten fresh workdirs, total spend ${total:.4f}")


def render_eval_a() -> str:
    fp_heads = " | ".join(f"FP {split}" for split in CLEAN_SPLITS)
    lines = [f"| sweep | run | INFRA | lenient | strict | {fp_heads} | spend |",
             "|---|---|---|---|---|" + "---|" * len(CLEAN_SPLITS) + "---|"]
    for label, run in EVAL_A:
        rows = _rows(run)
        fp = evalkit.false_positives(rows)
        fp_cells = " | ".join(_cell(fp[split]) for split in CLEAN_SPLITS)
        lines.append(
            f"| {label} | {run} | {sum(r.infra for r in rows)} | "
            f"{_cell(evalkit.detection(rows))} | {_cell(evalkit.detection(rows, strict=True))} | "
            f"{fp_cells} | {_spend(rows)} |")
    return "\n".join(lines)


def render_holdout() -> str:
    registry = evalkit.load_holdout_registry(REGISTRY)
    lines = ["| sweep | run | INFRA | lenient | strict | spend |",
             "|---|---|---|---|---|---|"]
    for label, run in HOLDOUT:
        rows = _rows(run, registry)
        lines.append(
            f"| {label} | {run} | {sum(r.infra for r in rows)} | "
            f"{_cell(evalkit.detection(rows))} | {_cell(evalkit.detection(rows, strict=True))} | "
            f"{_spend(rows)} |")
    return "\n".join(lines)


def render_stability(sweeps: tuple[tuple[str, str], ...], registry=None) -> str:
    verdicts: dict[tuple[str, str], list[str]] = {}
    for _, run in sweeps:
        for r in _rows(run, registry):
            verdicts.setdefault((r.task_id, r.variant), []).append(r.verdict or "INFRA")
    labels = [label for label, _ in sweeps]
    lines = ["| task | variant | " + " | ".join(labels) + " |",
             "|---|---|" + "---|" * len(labels)]
    moved = 0
    for key in sorted(verdicts):
        draws = verdicts[key]
        if len(draws) != len(sweeps):
            raise SkepticInfraError(
                f"{key[0]}/{key[1]} appears in {len(draws)} of {len(sweeps)} sweeps. "
                f"Next: a repeat set must sweep the same rows; check the run dirs."
            )
        if len(set(draws)) > 1:
            moved += 1
            lines.append(f"| {key[0]} | {key[1]} | " + " | ".join(draws) + " |")
    lines.append("")
    lines.append(f"rows with the same verdict in all {len(sweeps)} draws: "
                 f"{len(verdicts) - moved} of {len(verdicts)}")
    return "\n".join(lines)


def main() -> None:
    print(render_provenance())
    print()
    print("Eval A, five sweeps over the 65-row corpus")
    print()
    print(render_eval_a())
    print()
    print("Holdout, five sweeps over the 11 registry rows")
    print()
    print(render_holdout())
    print()
    print("Eval A rows whose verdict moved between draws")
    print()
    print(render_stability(EVAL_A))
    print()
    print("Holdout rows whose verdict moved between draws")
    print()
    print(render_stability(HOLDOUT, evalkit.load_holdout_registry(REGISTRY)))


if __name__ == "__main__":
    main()
