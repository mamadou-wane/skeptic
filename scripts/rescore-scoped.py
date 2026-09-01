#!/usr/bin/env python3
"""Four tables over the two committed published runs, no re-run, no API call.

Everything here rescores evidence the dev-set run
(`evals/v1/runs/eval-20260822-195147`) and the blind-holdout run
(`evals/v1/runs/eval-20260822-211836`) already recorded, through
`skeptic.evalkit.load_rows` and `evalkit.rescore` under the shipped `WEIGHTS`
and `SUSPECT_THRESHOLD`, the same way `scripts/rescore-deterministic.py`
does. That script's `drop_paid_evidence` is reused by import so "the
deterministic lane" means one thing in both.

1. Scoped split. The plan's Part 2 table marks six categories as prevented
   in-harness by read-only mounts (H1, H2, H3, H4, H9, H10) and four as
   detected (H5, H6, H7, H8). The published 29/29 and 12/29 pool both.
   This table reports each split on its own denominator, per lane.
2. Size-only baseline. SUSPECT when the variant's patch changes more than N
   lines, nothing else read. The corpus's clean variants are short, so a
   baseline that only measures length belongs beside the three the README
   already carries. Both thresholds were chosen after reading the corpus,
   which is the point: nothing pre-registered rules a size proxy out.
3. Per-rule fire counts and pivotality. How often each rule id fired on
   hacked and clean rows, and on how many catches dropping that one rule
   would have turned the verdict to PASS, in-harness and in the
   deterministic lane.
4. Leave-one-category-out. For each category, every evidence row the checks
   labeled with it is dropped and the category's own rows rescored; what
   survives is carried by rules labeled something else. Read with the
   labeling artifact in mind: `scope_violation` is labeled `scope` and
   `advtest_divergence` is labeled H6 on every row it emits, so a survivor
   carried by either is not transfer across categories.

A third posture, the deterministic lane with `scope_violation` also dropped,
is the closest offline approximation to `verify --diff`, where
`synthesize_spec` leaves `allowed_paths` empty and `t1_scope` never fires
(docs/evaluation.md, CI patch audit). It stays an approximation: nothing
here replays a check.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import statistics
from collections.abc import Callable, Iterable
from pathlib import Path

from skeptic import evalkit
from skeptic.checks import t1_scope, t2_advtests
from skeptic.checks.aggregate import SUSPECT_THRESHOLD, WEIGHTS
from skeptic.checks.evidence import RULES
from skeptic.errors import SkepticInfraError

REPO_ROOT = Path(__file__).resolve().parent.parent
DEV_RUN = REPO_ROOT / "evals" / "v1" / "runs" / "eval-20260822-195147"
HOLDOUT_RUN = REPO_ROOT / "evals" / "v1" / "runs" / "eval-20260822-211836"
TASKS_DIR = REPO_ROOT / "tasks"
REGISTRY = REPO_ROOT / "evals" / "v1" / "holdout" / "registry.yaml"

# docs/skeptic-engineering-plan.md Part 2, the Control column: which hack
# categories the in-harness sandbox refuses outright and which a check has
# to read off the patch.
PREVENTED = ("H1", "H2", "H3", "H4", "H9", "H10")
DETECTED = ("H5", "H6", "H7", "H8")
CATEGORIES = tuple(f"H{i}" for i in range(1, 11))
# A plain `raise`, not `assert`, so it holds under `python -O`: a category
# in neither tuple, or in both, would drop out of or double into the split
# table with nothing in the output saying so.
if sorted(PREVENTED + DETECTED) != sorted(CATEGORIES):
    raise SkepticInfraError(
        f"PREVENTED {PREVENTED} and DETECTED {DETECTED} do not partition "
        f"CATEGORIES {CATEGORIES}: the split table would silently drop or "
        f"double-count a category. Next: fix the two tuples against the "
        f"plan's Part 2 Control column."
    )
SIZE_THRESHOLDS = (4, 10)
# Rules that carry no weight and never score. Read off the recorded
# evidence where a rule fired; this set is the fallback for one that never
# did, and `_severity` refuses a disagreement between the two.
INFO_RULES = frozenset({t2_advtests.RULE_ZERO})

Rows = list[evalkit.EvalRow]

# The sibling script's own definition of the paid rule set, loaded by path
# because the file name carries a hyphen; importing it also runs that
# script's guard that the set still matches what `verify --diff` excuses.
_spec = importlib.util.spec_from_file_location(
    "rescore_deterministic", REPO_ROOT / "scripts" / "rescore-deterministic.py")
_deterministic = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_deterministic)
PAID_RULES = _deterministic.PAID_RULES


def drop_rules(rows: Rows, rules: Iterable[str]) -> Rows:
    dropped = frozenset(rules)
    return [
        dataclasses.replace(row, evidence=tuple(e for e in row.evidence if e.rule not in dropped))
        for row in rows
    ]


def drop_category(rows: Rows, category: str) -> Rows:
    return [
        dataclasses.replace(
            row, evidence=tuple(e for e in row.evidence if e.category != category))
        for row in rows
    ]


def rescored(rows: Rows) -> Rows:
    return evalkit.rescore(rows, WEIGHTS)


# lane name -> how its rows derive from the recorded paid-lane rows
LANES: dict[str, Callable[[Rows], Rows]] = {
    "paid": lambda rows: rows,
    "deterministic": lambda rows: drop_rules(rows, PAID_RULES),
    "deterministic, scope_violation dropped":
        lambda rows: drop_rules(rows, PAID_RULES | {t1_scope.RULE}),
}


def _cell(figure: tuple[int, int]) -> str:
    hits, n = figure
    return f"{hits}/{n}"


def _hacked(rows: Rows) -> Rows:
    return [r for r in rows if r.label == "hacked" and not r.infra]


def _in(rows: Rows, categories: Iterable[str]) -> Rows:
    wanted = frozenset(categories)
    return [r for r in rows if r.hack_category in wanted]


def render_split(dev: Rows, holdout: Rows) -> list[str]:
    lines = [
        (f"scoped split (prevented = {' '.join(PREVENTED)}, detected = {' '.join(DETECTED)}; "
         f"plan Part 2, Control column)"),
        "",
        "| lane | split | dev lenient | dev strict | holdout lenient | holdout strict |",
        "|---|---|---|---|---|---|",
    ]
    for lane, derive in LANES.items():
        lane_dev, lane_holdout = rescored(derive(dev)), rescored(derive(holdout))
        for rows in (lane_dev, lane_holdout):
            covered = len(_in(rows, PREVENTED)) + len(_in(rows, DETECTED))
            if covered != len(_hacked(rows)):
                raise SkepticInfraError(
                    f"{len(_hacked(rows))} hacked rows but the two splits cover "
                    f"{covered}: a hacked row carries a category outside "
                    f"{CATEGORIES}. Next: add it to PREVENTED or DETECTED."
                )
        for split, categories in (("prevented", PREVENTED), ("detected", DETECTED)):
            d, h = _in(lane_dev, categories), _in(lane_holdout, categories)
            lines.append(
                f"| {lane} | {split} | {_cell(evalkit.detection(d))} | "
                f"{_cell(evalkit.detection(d, strict=True))} | "
                f"{_cell(evalkit.detection(h))} | {_cell(evalkit.detection(h, strict=True))} |"
            )
    return lines


def render_size_baseline(
    dev: Rows, holdout: Rows, patch_lines: dict[tuple[str, str], int],
) -> list[str]:
    lines = [
        ("size-only baseline (SUSPECT when the variant patch changes more than N lines, "
         "nothing else read)"),
        "",
        ("| baseline | dev lenient | dev strict | holdout lenient | holdout strict | "
         "FP gold | FP gold-prime |"),
        "|---|---|---|---|---|---|---|",
    ]
    for threshold in SIZE_THRESHOLDS:
        d = evalkit.baseline_size_only(dev, patch_lines, threshold)
        h = evalkit.baseline_size_only(holdout, patch_lines, threshold)
        lines.append(
            f"| {d.name} | {_cell(d.detection_lenient)} | {_cell(d.detection_strict)} | "
            f"{_cell(h.detection_lenient)} | {_cell(h.detection_strict)} | "
            f"{_cell(d.false_positives['gold'])} | {_cell(d.false_positives['gold-prime'])} |"
        )
    kinds = (
        ("dev gold", [r for r in dev if r.variant == "gold"]),
        ("dev gold-prime", [r for r in dev if r.variant == "gold-prime"]),
        ("dev hacked", _hacked(dev)),
        ("holdout hacked", _hacked(holdout)),
    )
    spread = " · ".join(
        f"{kind} {_spread([patch_lines[(r.task_id, r.variant)] for r in rows])}"
        for kind, rows in kinds
    )
    lines += ["", f"changed lines, min/median/max: {spread}"]
    return lines


def _spread(values: list[int]) -> str:
    return f"{min(values)}/{statistics.median(values):g}/{max(values)}"


def _fires(rows: Rows, rule: str) -> int:
    return sum(1 for r in rows if any(e.rule == rule for e in r.evidence))


def _pivotal(rows: Rows, rule: str) -> tuple[int, int]:
    """(catches lost, hacked rows): hacked rows caught lenient with `rule`
    present that read PASS once that rule alone is dropped. Both sides are
    rescored under the lane's own evidence: a lane's rows still carry the
    recorded paid-lane verdict, and comparing against that would charge
    every paid-only catch to whichever rule is being dropped."""
    hacked = rescored(_hacked(rows))
    without = rescored(drop_rules(hacked, (rule,)))
    lost = sum(
        1 for before, after in zip(hacked, without, strict=True)
        if before.verdict in ("SUSPECT", "FAIL") and after.verdict == "PASS"
    )
    return lost, len(hacked)


def _rule_order(rule: str) -> tuple:
    if rule in WEIGHTS:
        return (1, -WEIGHTS[rule], rule)
    return (2 if rule in INFO_RULES else 0, 0, rule)


def _severity(rule: str, recorded: dict[str, str]) -> str:
    """A rule's severity: what the recorded evidence says where the rule
    fired, else soft if it carries a weight, info if `INFO_RULES` names it,
    hard otherwise. The two sources have to agree, or the table would print
    a severity the harness never emitted."""
    derived = "soft" if rule in WEIGHTS else "info" if rule in INFO_RULES else "hard"
    if rule in recorded and recorded[rule] != derived:
        raise SkepticInfraError(
            f"{rule} is recorded at severity {recorded[rule]!r} but derives as "
            f"{derived!r} from WEIGHTS and INFO_RULES. Next: fix INFO_RULES or "
            f"the weights table, whichever moved."
        )
    return recorded.get(rule, derived)


def render_per_rule(dev: Rows, holdout: Rows) -> list[str]:
    lines = [
        ("per-rule fire counts and pivotality (pivotal: hacked rows whose catch turns to "
         "PASS when that one rule is dropped; dev · holdout)"),
        "",
        ("| rule | severity | weight | dev hacked | dev gold | dev gold-prime | "
         "holdout hacked | pivotal in-harness | pivotal deterministic |"),
        "|---|---|---|---|---|---|---|---|---|",
    ]
    dev_hacked, holdout_hacked = _hacked(dev), _hacked(holdout)
    gold = [r for r in dev if r.label == "clean" and r.variant == "gold" and not r.infra]
    prime = [r for r in dev if r.label == "clean" and r.variant == "gold-prime" and not r.infra]
    det_dev, det_holdout = LANES["deterministic"](dev), LANES["deterministic"](holdout)
    recorded = {e.rule: e.severity for r in dev + holdout for e in r.evidence}
    for rule in sorted(RULES, key=_rule_order):
        severity = _severity(rule, recorded)
        weight = str(WEIGHTS[rule]) if severity == "soft" else "n/a"
        in_harness = f"{_cell(_pivotal(dev, rule))} · {_cell(_pivotal(holdout, rule))}"
        if rule in PAID_RULES:
            deterministic = "n/a"
        else:
            deterministic = (f"{_cell(_pivotal(det_dev, rule))} · "
                             f"{_cell(_pivotal(det_holdout, rule))}")
        lines.append(
            f"| {rule} | {severity} | {weight} | "
            f"{_fires(dev_hacked, rule)}/{len(dev_hacked)} | "
            f"{_fires(gold, rule)}/{len(gold)} | {_fires(prime, rule)}/{len(prime)} | "
            f"{_fires(holdout_hacked, rule)}/{len(holdout_hacked)} | "
            f"{in_harness} | {deterministic} |"
        )
    return lines


def _ablation(rows: Rows, category: str) -> tuple[str, list[evalkit.EvalRow]]:
    """`"full -> ablated"` over this category's hacked rows, and the rows
    still caught after every entry labeled `category` is dropped."""
    own = _in(_hacked(rows), (category,))
    if not own:
        return "no rows", []
    full = sum(1 for r in rescored(own) if r.verdict != "PASS")
    ablated = [r for r in rescored(drop_category(own, category)) if r.verdict != "PASS"]
    return f"{full}/{len(own)} -> {len(ablated)}/{len(own)}", ablated


def render_leave_one_out(dev: Rows, holdout: Rows) -> list[str]:
    lines: list[str] = []
    for lane, derive in LANES.items():
        lane_dev, lane_holdout = derive(dev), derive(holdout)
        table = []
        survived = catches = 0
        for category in CATEGORIES:
            dev_cell, dev_left = _ablation(lane_dev, category)
            holdout_cell, holdout_left = _ablation(lane_holdout, category)
            for rows, cell in ((lane_dev, dev_cell), (lane_holdout, holdout_cell)):
                if cell != "no rows":
                    catches += int(cell.split(" -> ")[0].split("/")[0])
            survived += len(dev_left) + len(holdout_left)
            residual = sorted({
                e.rule for r in dev_left + holdout_left for e in r.evidence
                if e.severity != "info"
            })
            table.append(
                f"| {category} | {dev_cell} | {holdout_cell} | {', '.join(residual) or 'none'} |")
        n_hacked = len(_hacked(dev)) + len(_hacked(holdout))
        if lines:
            lines.append("")
        lines += [
            (f"leave-one-category-out, {lane}: {survived} of {catches} catches survive, "
             f"of {n_hacked} hacks"),
            "",
            "| category | dev full -> ablated | holdout full -> ablated | residual rules |",
            "|---|---|---|---|",
            *table,
        ]
    return lines


def render(dev: Rows, holdout: Rows, patch_lines: dict[tuple[str, str], int]) -> str:
    blocks = [
        [(f"rescored from committed evidence · dev {DEV_RUN.name} · holdout {HOLDOUT_RUN.name} "
          f"· WEIGHTS as shipped, threshold {SUSPECT_THRESHOLD}")],
        render_split(dev, holdout),
        render_size_baseline(dev, holdout, patch_lines),
        render_per_rule(dev, holdout),
        render_leave_one_out(dev, holdout),
    ]
    return "\n\n".join("\n".join(block) for block in blocks)


def main() -> None:
    registry = evalkit.load_holdout_registry(REGISTRY)
    dev = evalkit.load_rows(DEV_RUN, TASKS_DIR)
    holdout = evalkit.load_rows(HOLDOUT_RUN, TASKS_DIR, registry)
    patches = evalkit.variant_patches(dev + holdout, TASKS_DIR, registry)
    patch_lines = {key: evalkit.changed_lines(REPO_ROOT / path) for key, path in patches.items()}
    print(render(dev, holdout, patch_lines))


if __name__ == "__main__":
    main()
