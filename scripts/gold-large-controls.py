#!/usr/bin/env python3
"""Two tables over the deterministic validation sweep of the 65-row corpus.

The twelve `gold-large` controls (issue #33, DECISIONS row 243) are
behavior-preserving refactors of 20 to 100 changed lines that carry each
task's fix. This script reads the sweep that validated them at $0
(`SWEEP_RUN`) through `skeptic.evalkit.load_rows`, sizes every control with
`evalkit.changed_lines` (added plus removed lines inside hunks, the one
counting method for the issue), and prints:

1. The controls, one row per task: shape, changed lines, deterministic
   verdict and score.
2. The sweep by split, each clean split on its own denominator (rows 147,
   218, 234) and the hacked rows as one line, so the deterministic lane's
   reading of the enlarged corpus sits beside the paid figures the README
   carries.

docs/evaluation.md quotes the output verbatim; `tests/test_gold_large.py`
binds the two.
"""
from __future__ import annotations

import statistics
from pathlib import Path

from skeptic import evalkit
from skeptic.errors import SkepticInfraError
from skeptic.spec import find_task

REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = REPO_ROOT / "tasks"
SWEEP_RUN = REPO_ROOT / "evals" / "v1" / "runs" / "eval-20260902-004842"

CONTROL = "gold-large"
CLEAN_SPLITS = ("gold", "gold-prime", CONTROL)

# What each control does, in the words a reviewer would use on the diff.
SHAPES = {
    "click-0001": "local rename through `_make_default_short_help`",
    "click-0002": "loop guard restructured and renamed in `_truncate_visible`",
    "click-0003": "`_default_preview` extracted from `_build_prompt`",
    "click-0004": "named normalization and early return in `BoolParamType`",
    "click-0005": "`_find_command` extracted from `Group.resolve_command`",
    "click-0006": "early returns and a nested `is_channel` predicate in `_interpret_color`",
    "rich-0001": "`_title_text` extracted and side chars hoisted in `Rule`",
    "rich-0002": "`_example_table` extracted from `make_test_card`",
    "rich-0003": "guard clause inverted in `Segment.split_cells`",
    "rich-0004": "`_fold_spans` extracted from `chop_cells`, remaining headroom named",
    "rich-0005": "`guide_stack` rename and `root_guides` named in `Tree`",
    "rich-0006": "`_styles` extracted from `ProgressBar.__rich_console__`",
}


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _control_patch(task_id: str, tasks_dir: Path) -> Path:
    spec = find_task(task_id, tasks_dir)
    variant = next(v for v in spec.evaluation.variants if v.id == CONTROL)
    return REPO_ROOT / variant.patch


def render_controls(run_dir: Path, tasks_dir: Path) -> str:
    rows = [r for r in evalkit.load_rows(run_dir, tasks_dir) if r.variant == CONTROL]
    if not rows:
        raise SkepticInfraError(
            f"{run_dir} holds no {CONTROL} snapshot. Next: point the script at a "
            f"sweep that ran after the controls joined the corpus."
        )
    lines = ["| task | shape | changed lines | verdict | score |",
             "|---|---|---|---|---|"]
    sizes = []
    for row in sorted(rows, key=lambda r: r.task_id):
        size = evalkit.changed_lines(_control_patch(row.task_id, tasks_dir))
        sizes.append(size)
        lines.append(f"| {row.task_id} | {SHAPES[row.task_id]} | {size} | "
                     f"{row.verdict or 'INFRA'} | {row.suspect_score:.2f} |")
    lines.append("")
    lines.append(f"changed lines, min/median/max: {min(sizes)}/"
                 f"{_fmt(statistics.median(sizes))}/{max(sizes)}")
    return "\n".join(lines)


def render_splits(run_dir: Path, tasks_dir: Path) -> str:
    rows = evalkit.load_rows(run_dir, tasks_dir)
    lines = ["| split | n | PASS | SUSPECT | FAIL | INFRA |",
             "|---|---|---|---|---|---|"]
    groups = [(split, [r for r in rows if r.variant == split]) for split in CLEAN_SPLITS]
    groups.append(("hacked", [r for r in rows if r.label == "hacked"]))
    for name, group in groups:
        counts = {v: sum(1 for r in group if r.verdict == v) for v in ("PASS", "SUSPECT", "FAIL")}
        infra = sum(1 for r in group if r.verdict is None)
        lines.append(f"| {name} | {len(group)} | {counts['PASS']} | {counts['SUSPECT']} | "
                     f"{counts['FAIL']} | {infra} |")
    return "\n".join(lines)


def main() -> None:
    if not SWEEP_RUN.is_dir():
        raise SkepticInfraError(
            f"{SWEEP_RUN} is not a run directory. Next: set SWEEP_RUN to the "
            f"committed deterministic sweep under evals/v1/runs/."
        )
    print(f"Deterministic validation sweep: {SWEEP_RUN.relative_to(REPO_ROOT)}")
    print()
    print(render_controls(SWEEP_RUN, TASKS_DIR))
    print()
    print(render_splits(SWEEP_RUN, TASKS_DIR))


if __name__ == "__main__":
    main()
