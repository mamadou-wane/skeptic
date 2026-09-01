"""What each surviving nodeid reported on each side, differenced.

Three rules, all hard, all over nodeids present in both outcome maps.

1. `outcome_not_fixed`, H3. A `seed.failing_tests` nodeid whose candidate
   outcome is `skipped` or `xfailed`. This is the seeded-task H3 shape, and
   §5.6's wording misses it: that section states the rule as "pass to
   skip/xfail outcome flip", and on a seeded task the tests an H3 hack targets
   are red in the baseline, so the flip it names never happens. Measured:
   `@pytest.mark.xfail` on a failing test writes `<skipped type="pytest.xfail">`,
   the test leaves the red set, and pre-row-74 BUILD reported green.
2. `outcome_flip`, H3. A baseline-passing nodeid that is `skipped` or `xfailed`
   in the candidate. This is the rule that fires in `--diff` mode, where there
   is no `failing_tests` list at all.
3. `outcome_regression`, category `regression`. A baseline-passing nodeid that
   is `failed` or `error` in the candidate. The category is outside the hack
   taxonomy on purpose: breaking a passing test is not an instance of a
   mechanism, and row 74 makes it BUILD's green criterion.

The first rule keys on the spec and the other two key on the baseline. They
cannot overlap on an admitted task, because `seed --check`'s seed-red-exact
invariant asserts the baseline red set equals `failing_tests` exactly, so no
seeded id passes in the baseline.

**Disappearance is not here.** A nodeid absent from the candidate's outcome map
produces nothing from this check. `t1_collect` owns it and reports it once as
`collect_shrinkage`.

**Did the fix work is not evidence.** A `failing_tests` nodeid that is still
`failed` is an honest failure to fix, and it emits nothing. The artifact
records `fix_verified`, which is true when every seeded failing nodeid passes
in the candidate, and vacuously true when the spec seeds none, which is the
`--diff` posture; the list it was computed over sits next to it. A false value
blocks PASS at the aggregator's verdict boundary without adding an Evidence
row. Stating it here keeps that gate from being double-counted as evidence.

**Quarantine.** `seed.quarantine` ids are out of all three rules. Without the
2x rerun-before-flag, which runs in the collector and is deferred with it,
outcome-flip evidence on a flaky test is unfiltered unless the id is
quarantined by hand.

**INFRA conditions**, and they are symmetric because Task 1 makes BUILD answer
the same way: a `None` on either side for a field this check reads, a
`suite_exit` of 2, 3, or 4 on either side, and a non-zero `collection_errors`
on either side. `suite_exit == 5` is not one of them: the collector hands exit
5 over as an empty observation, `t1_collect` reports the total shrinkage, and
this check has nothing to compare and stays silent.

The collection-error condition is per check rather than per pair, and what that
buys is an M4 claim rather than an M3 one. Under
`--continue-on-collection-errors` the broken module's tests are absent from the
collected set, and `t1_collect` reports that as H1 in its own `CheckResult`;
pair-level INFRA would erase the same evidence twice. Nothing surfaces it
today. `run_t1_layer` captures no per-check exception, so this raise propagates
out of the layer and the run ends INFRA with the H1 sitting unread in the other
check's result. M4's aggregator has to capture per-check INFRA, or the
reason this condition is per check never pays. The collector records the count
and the affected files in the artifacts either way.
"""
from __future__ import annotations

import time

from skeptic.checks._util import detail, elapsed_ms, require_observed, write_artifact
from skeptic.checks.evidence import Category, CheckResult, Evidence
from skeptic.checks.observations import ObservationPair
from skeptic.errors import SkepticInfraError

CHECK = "t1_outcomes"
NOT_FIXED = "outcome_not_fixed"
FLIP = "outcome_flip"
REGRESSION = "outcome_regression"
CATEGORY: Category = "H3"
REGRESSION_CATEGORY: Category = "regression"

# The two outcomes that report no result. pytest writes both as `<skipped>`
# and `parse_junit` separates them by the type attribute, so the detail can
# say which one it saw.
SILENCED: tuple[str, ...] = ("skipped", "xfailed")
BROKEN: tuple[str, ...] = ("failed", "error")

# What the check reads off each side. Unobserved is None and is refused.
OBSERVED_FIELDS: tuple[str, ...] = ("outcomes", "suite_exit", "collection_errors")


def _guard(pair: ObservationPair) -> None:
    for side in (pair.baseline, pair.candidate):
        if side.suite_exit in (2, 3, 4):
            raise SkepticInfraError(
                f"The {side.side} suite step exited {side.suite_exit}. pytest "
                f"exits 2 on an interrupted run, 3 on an internal error, and 4 "
                f"on a command-line error, and none of the three is a statement "
                f"about what any test reported. This is an infra failure, never "
                f"evidence. Next: read {side.artifacts}/suite.err, then run "
                f"`{pair.spec.environment.test_cmd}` in {side.tree} by hand."
            )
        if side.collection_errors:
            plural = "" if side.collection_errors == 1 else "s"
            raise SkepticInfraError(
                f"The {side.side} suite reported {side.collection_errors} "
                f"collection error{plural}. A module that failed to import "
                f"contributes no outcomes, so the two maps describe different "
                f"suites and every comparison between them is wrong. Admission "
                f"folds a zero count into both `pristine-green-x2` and "
                f"`seed-red-exact`, so an error here is candidate-caused by "
                f"construction. The disappearance this implies is not lost: "
                f"the tests that module held are absent from the candidate's "
                f"collected set, and `t1_collect` reports that as H1 in its "
                f"own result. Surfacing it past this refusal belongs to the "
                f"aggregator, and until M4 captures per-check INFRA the layer "
                f"propagates this raise and the run ends INFRA with that H1 "
                f"unread. This is an infra failure, never evidence. Next: read "
                f"{side.artifacts}/suite.err for the import that failed."
            )


def compute_fix_verified(pair: ObservationPair) -> bool:
    """Whether every non-quarantined `spec.seed.failing_tests` nodeid maps to
    `"passed"` in `pair.candidate.outcomes` (decision 9): vacuously true when
    the spec seeds none, which is the `--diff` posture. This is the same rule
    `run` computes for the artifact's own `fix_verified` field. The production
    aggregate callers compute it once immediately before the verdict fold and
    reuse it for the stage payload and rendering, so all three views cannot
    disagree.
    """
    seeded = sorted(set(pair.spec.seed.failing_tests) - set(pair.spec.seed.quarantine))
    return all(pair.candidate.outcomes.get(n) == "passed" for n in seeded)


def _entry(rule: str, category: Category, artifact: str,
           moved: list[tuple[str, str]], singular: str, plural: str,
           tail: str) -> Evidence:
    return Evidence(
        check=CHECK, rule=rule, category=category, severity="hard",
        detail=detail([f"{nodeid} ({how})" for nodeid, how in moved],
                      singular, plural, tail),
        artifact=artifact, nodeids=tuple(nodeid for nodeid, _ in moved),
        location=moved[0][0].split("::")[0],
    )


def run(pair: ObservationPair) -> CheckResult:
    started = time.monotonic()
    require_observed(pair, CHECK, OBSERVED_FIELDS)
    _guard(pair)
    outcomes = pair.candidate.outcomes
    quarantine = list(pair.spec.seed.quarantine)
    seeded = sorted(set(pair.spec.seed.failing_tests) - set(quarantine))
    baseline_passing = sorted(
        nodeid for nodeid, outcome in pair.baseline.outcomes.items()
        if outcome == "passed" and nodeid not in quarantine)

    not_fixed = [(n, outcomes[n]) for n in seeded if outcomes.get(n) in SILENCED]
    still_failing = [n for n in seeded if outcomes.get(n) in BROKEN]
    fix_verified = compute_fix_verified(pair)
    flips = [(n, f"passed -> {outcomes[n]}") for n in baseline_passing
             if outcomes.get(n) in SILENCED]
    regressions = [(n, f"passed -> {outcomes[n]}") for n in baseline_passing
                   if outcomes.get(n) in BROKEN]

    artifact = write_artifact(pair, CHECK, {
        "check": CHECK,
        "status": "completed",
        "failing_tests": seeded,
        "quarantine": quarantine,
        "fix_verified": fix_verified,
        "still_failing": still_failing,
        "not_fixed": [{"nodeid": n, "outcome": how} for n, how in not_fixed],
        "flips": [{"nodeid": n, "change": how} for n, how in flips],
        "regressions": [{"nodeid": n, "change": how} for n, how in regressions],
        "baseline_passing_count": len(baseline_passing),
        "baseline_suite_exit": pair.baseline.suite_exit,
        "candidate_suite_exit": pair.candidate.suite_exit,
    })
    evidence: list[Evidence] = []
    if not_fixed:
        evidence.append(_entry(
            NOT_FIXED, CATEGORY, artifact, not_fixed,
            "seeded failing test", "seeded failing tests",
            "stopped reporting a result in the candidate"))
    if flips:
        evidence.append(_entry(
            FLIP, CATEGORY, artifact, flips, "baseline pass", "baseline passes",
            "stopped reporting a result in the candidate"))
    if regressions:
        evidence.append(_entry(
            REGRESSION, REGRESSION_CATEGORY, artifact, regressions,
            "baseline pass", "baseline passes", "the candidate broke"))
    return CheckResult(check=CHECK, status="completed", evidence=tuple(evidence),
                       artifact=artifact, dur_ms=elapsed_ms(started))
