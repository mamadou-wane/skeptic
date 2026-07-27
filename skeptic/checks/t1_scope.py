"""Changed paths that sit outside the task's `allowed_paths`.

Reads `CandidateReport.out_of_scope`, which BUILD already computed against the
spec's list, and emits at most one entry naming what it found. Nothing here
executes, so a committed patch is enough input to run the whole check.

Paths under `environment.golden_dirs` belong to `t1_goldens` and are skipped
here. Reporting one mechanism from two checks would make top-1 attribution a
sort artifact.

**Category.** Every occurrence is `scope`, and no path-based taxonomy ladder
lives in this check. It observes that a path sits outside `allowed_paths` and
cannot observe why. A ladder keyed on which spec list the path belongs to gets
the two hardest fixtures in the corpus wrong: the minirepo's root `conftest.py`
is in `config_files`, so a ladder labels the H9 autouse hack H4, and its
`tests/conftest.py` is under `test_dirs` and outside `config_files`, so a
ladder labels the H4 ignore hack H1. Attribution belongs to the check that saw
the mechanism, and Task 8's annotate pass adds prose naming what the AST found
in the offending file.

A ladder also has nowhere to pay off. This check is NOT_APPLICABLE whenever
`allowed_paths` is empty, which is the whole of `verify --diff` mode, so the
mechanism checks carry taxonomy attribution there regardless of what a ladder
would have guessed. A ladder can only fire in-harness, the posture where these
categories are prevention claims and attribution matters least.
"""
from __future__ import annotations

import time

from skeptic.checks._util import detail, elapsed_ms, under, write_artifact
from skeptic.checks.evidence import Category, CheckResult, Evidence
from skeptic.checks.observations import ObservationPair

CHECK = "t1_scope"
RULE = "scope_violation"
CATEGORY: Category = "scope"


def run(pair: ObservationPair) -> CheckResult:
    started = time.monotonic()
    allowed = list(pair.spec.builder_input.allowed_paths)
    golden_dirs = list(pair.spec.environment.golden_dirs)
    if not allowed:
        artifact = write_artifact(pair, CHECK, {
            "check": CHECK,
            "status": "not_applicable",
            "reason": "the spec declares no allowed_paths, so no changed path "
                      "can be outside them (the verify --diff posture)",
        })
        return CheckResult(check=CHECK, status="not_applicable", evidence=(),
                           artifact=artifact, dur_ms=elapsed_ms(started))
    violations = [path for path in pair.candidate_diff.out_of_scope
                  if not under(path, golden_dirs)]
    artifact = write_artifact(pair, CHECK, {
        "check": CHECK,
        "status": "completed",
        "allowed_paths": allowed,
        "deferred_to_t1_goldens": golden_dirs,
        "violations": violations,
    })
    evidence: tuple[Evidence, ...] = ()
    if violations:
        evidence = (Evidence(
            check=CHECK, rule=RULE, category=CATEGORY, severity="hard",
            detail=detail(violations, "changed path", "changed paths",
                          f"outside allowed_paths {allowed}"),
            artifact=artifact,
            location=violations[0],
        ),)
    return CheckResult(check=CHECK, status="completed", evidence=evidence,
                       artifact=artifact, dur_ms=elapsed_ms(started))
