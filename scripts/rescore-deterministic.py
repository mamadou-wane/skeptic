#!/usr/bin/env python3
"""Rescores Eval A's published run with the two paid checks' evidence gone.

`skeptic verify --diff` runs the deterministic profile only (M6 spec
decision 2): no API key, no `t2_advtests`, no `t2_judge`. This script
answers what the committed paid-lane run
(`evals/v1/runs/eval-20260816-225027`, 27/29 lenient) would have detected
without those two checks, by loading it through `skeptic.evalkit.load_rows`,
dropping every evidence entry the two paid checks contributed, and
rescoring what is left under the shipped `WEIGHTS` and `SUSPECT_THRESHOLD`
(`skeptic.checks.aggregate`, both read by import).

This is an approximation of the diff lane, not a replay of it. `verify
--diff` synthesizes an empty `allowed_paths`, which takes `t1_scope` out of
contention entirely (`skeptic.diffmode.synthesize_spec`'s own docstring),
while the rows here still carry whatever `t1_scope` found in-harness. The
README's CI patch audit section states that gap as a posture caveat next
to the numbers this script prints.

The two paid checks and the rule ids they emit are read off `t2_advtests`
and `t2_judge` themselves and checked against `aggregate.PAID_ONLY_CHECKS`,
so a third paid check or a renamed rule breaks this script loudly instead
of silently under-dropping evidence.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

from skeptic import evalkit
from skeptic.checks import t2_advtests, t2_judge
from skeptic.checks.aggregate import PAID_ONLY_CHECKS, SUSPECT_THRESHOLD, WEIGHTS

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_DIR = REPO_ROOT / "evals" / "v1" / "runs" / "eval-20260816-225027"
TASKS_DIR = REPO_ROOT / "tasks"

# check -> the rule ids that check emits, read off the check modules rather
# than typed out here, so a rule rename shows up as an AttributeError instead
# of a silent mismatch.
_PAID_CHECK_RULES: dict[str, tuple[str, ...]] = {
    t2_advtests.CHECK: (t2_advtests.RULE_DIVERGENCE, t2_advtests.RULE_ZERO),
    t2_judge.CHECK: (t2_judge.RULE,),
}
assert set(_PAID_CHECK_RULES) == PAID_ONLY_CHECKS, (
    f"_PAID_CHECK_RULES covers {sorted(_PAID_CHECK_RULES)} but "
    f"aggregate.PAID_ONLY_CHECKS is {sorted(PAID_ONLY_CHECKS)}: a paid check "
    f"was added, removed, or renamed and this script's evidence drop no "
    f"longer matches it."
)
PAID_RULES = frozenset(
    rule for check in PAID_ONLY_CHECKS for rule in _PAID_CHECK_RULES[check]
)


def drop_paid_evidence(rows: list[evalkit.EvalRow]) -> list[evalkit.EvalRow]:
    """`rows`, each with every paid-check evidence entry removed."""
    return [
        dataclasses.replace(
            row, evidence=tuple(e for e in row.evidence if e.rule not in PAID_RULES)
        )
        for row in rows
    ]


def deterministic_lane(rows: list[evalkit.EvalRow]) -> list[evalkit.EvalRow]:
    """`rows` rescored under the shipped WEIGHTS with paid evidence dropped.

    `evalkit.rescore` calls `aggregate.score_evidence` with no threshold
    argument of its own, so `SUSPECT_THRESHOLD` reaches this rescore through
    `score_evidence`'s own default rather than a value this script passes;
    importing it here, and printing it in `render` below, is what keeps that
    fact checkable rather than assumed.
    """
    return evalkit.rescore(drop_paid_evidence(rows), WEIGHTS)


def render(rows: list[evalkit.EvalRow]) -> str:
    """The deterministic-lane table: lenient/strict totals, then per-category
    caught counts over the hacked, non-INFRA rows."""
    lenient = evalkit.detection(rows)
    strict = evalkit.detection(rows, strict=True)
    header = (f"deterministic lane (paid checks dropped, threshold "
              f"{SUSPECT_THRESHOLD}) · {RUN_DIR.name}")
    lines = [
        header,
        f"detection lenient {lenient[0]}/{lenient[1]}",
        f"detection strict {strict[0]}/{strict[1]}",
    ]
    hacked = [r for r in rows if r.label == "hacked" and not r.infra]
    for category in sorted({r.hack_category for r in hacked}):
        in_category = [r for r in hacked if r.hack_category == category]
        hits = sum(1 for r in in_category if r.verdict in ("SUSPECT", "FAIL"))
        lines.append(f"  {category} {hits}/{len(in_category)}")
    return "\n".join(lines)


def main() -> None:
    rows = evalkit.load_rows(RUN_DIR, TASKS_DIR)
    print(render(deterministic_lane(rows)))


if __name__ == "__main__":
    main()
