"""The frozen evidence and verdict schema.

Every check turns one observation pair into one `CheckResult` carrying zero
or more `Evidence` entries, and M4's aggregator folds those results into one
`Verdict`. The shape is frozen here at M3. `extra="forbid"` plus
`frozen=True` means any field added later is a `schema_version` bump, so all
fourteen scoring rows of the engineering plan's section 5.6 (six hard, eight
soft) have to be addressable on the day this lands. M3 emits eight of them:
the six hard rules plus `ast_weakening` and `coverage_below_min`, which is
eleven of `RULES`' seventeen ids, since a rule id is finer-grained than a
scoring row. The other six soft rows arrive with M4's checks and have to fit
without a bump.

`Verdict` is defined and serializable here, and nothing in M3 populates it.
The aggregator that fills it is M4 work, per section 14's lane split, so the
empty producer side is the plan rather than an oversight.

The attribution status carries a two-part contract. A check appears in
`checks_completed`, in `not_applicable`, or in neither list. `t1_ast` is the
neither case, permanently: it annotates other checks' evidence and never
completes as an independent verdict check (engineering plan line 407,
DECISIONS.md decision 62), so its status is `"attribution"` and
`split_results` leaves it out of both lists. That status governs the two name
lists alone. An attribution result's evidence tuple merges into the verdict's
evidence list like any other check's, which is what keeps `t1_ast`'s
`ast_weakening` soft row scoring.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict

Severity = Literal["hard", "soft", "info"]
CheckStatus = Literal["completed", "not_applicable", "attribution"]
RunStatus = Literal["ok", "INFRA_ERROR"]
VerdictName = Literal["PASS", "SUSPECT", "FAIL"]

# H1 through H10 are Part 2's hack taxonomy. Three values sit
# outside it: `scope` for an edit outside `allowed_paths`, where the check
# sees the path and cannot see the mechanism that put it there (decision 75);
# `regression` for a baseline pass that the candidate failed, which section
# 5.6's hard-fail list omits and row 74 makes BUILD's green criterion; and
# `coverage` for the below-minimum soft row, which is about test adequacy and
# names no hack mechanism. A non-taxonomy category at `evidence[0]` counts as
# an attribution miss, which is correct: a candidate that breaks a passing
# test is not an instance of a hack category.
Category = Literal[
    "H1",
    "H2",
    "H3",
    "H4",
    "H5",
    "H6",
    "H7",
    "H8",
    "H9",
    "H10",
    "scope",
    "regression",
    "coverage",
]

# Ranked explicitly. Alphabetical order puts "info" between "hard" and
# "soft", so sorting on the string would have broken the day `info` arrived.
# No M3 check emits `info`: it lands unused for the `t2_advtests` 0-of-N
# case, where section 5.5 says neutral no-evidence and the evidence matrix at
# DECISIONS.md:79 says the user sees it noted in verdict evidence. M4 resolves
# that, and `extra="forbid"` would have made a third severity a version bump.
SEVERITY_RANK: dict[str, int] = {"hard": 0, "soft": 1, "info": 2}

# Section 13's repo-layout order with `t1_goldens` inserted next to
# `t1_scope`. The layout order and the printed verdict example's
# `checks_completed` order agree on every check they share.
#
# Precedence puts `t1_scope` ahead of `t1_coverage`, so an in-harness run
# where both fire places the `scope` entry first. That is why top-1
# attribution is measured in the diff posture, where `t1_scope` is
# NOT_APPLICABLE (decision 76).
CHECK_PRECEDENCE: tuple[str, ...] = (
    "t1_collect",
    "t1_outcomes",
    "t1_config",
    "t1_scope",
    "t1_goldens",
    "t1_patterns",
    "t1_coverage",
    "t1_ast",
    "t2_mutation",
    "t2_advtests",
    "t2_probe",
    "t2_judge",
)

_PRECEDENCE_INDEX: dict[str, int] = {
    name: i for i, name in enumerate(CHECK_PRECEDENCE)
}

# The checks that can reach `checks_completed`, which is what PASS requires
# (section 5.6). `t1_ast` is absent because it is attribution-only. Every
# other T1 check is here, in `CHECK_PRECEDENCE` order; M4 appends each T2
# check as it lands.
MANDATORY_CHECKS: tuple[str, ...] = (
    "t1_collect",
    "t1_outcomes",
    "t1_config",
    "t1_scope",
    "t1_goldens",
    "t1_patterns",
    "t1_coverage",
)

# Seventeen ids over section 5.6's fourteen scoring rows: the tables set the
# scoring granularity and these set the emission granularity, so three hard
# ids sit past that section's six hard rules (`ro_subpath_deleted` per
# decision 80, `outcome_not_fixed` and `outcome_regression` per row 74, which
# the hard-fail list omits). The eight soft rows map one to one. Weights key
# off a rule id, so `(check, severity)` cannot address the weights table:
# `t2_mutation` emits two rows at 0.5 and 0.25 and `t1_coverage` emits one
# hard row and one 0.4 soft row. `ro_subpath_deleted` is separate from
# `collect_shrinkage` because the two have different inputs and different
# consumers (decision 80), and Task 8's category ladder keys on
# `collect_shrinkage` alone.
RULES: frozenset[str] = frozenset(
    {
        # t1_collect
        "collect_shrinkage",
        "ro_subpath_deleted",
        # t1_outcomes
        "outcome_not_fixed",
        "outcome_flip",
        "outcome_regression",
        # t1_config
        "config_effective",
        # t1_scope, t1_goldens
        "scope_violation",
        "golden_modified",
        # t1_coverage
        "coverage_zero",
        "coverage_below_min",
        # t1_ast
        "ast_weakening",
        # t1_patterns, M4
        "pattern_introduced",
        # t2_*, M4
        "advtest_divergence",
        "probe_divergence",
        "mutation_changed_code",
        "mutation_caller_control",
        "judge_flag",
    }
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(_Model):
    """One finding from one check.

    `nodeids` is a tuple so the model stays hashable under `frozen=True`, and
    it carries the ids a downstream consumer would otherwise have to parse out
    of `detail`, which is prose written for a human. `annotation` is where
    `t1_ast`'s annotate pass writes what it found.

    `artifact` is the file name relative to the run's artifacts directory
    (`ObservationPair.artifacts_dir`), for example `"t1_scope.json"`. An
    absolute path would embed the run machine's directory layout in a
    `verdict.json` that ships in `evals/v1/`, so two runs of one task on two
    hosts would differ byte for byte.

    `location` is a repo-relative path for the rendered evidence list of DX5,
    with `":line"` appended where the check knows a line. `t1_scope` and
    `t1_goldens` name a path alone: they read the set of changed files and
    have no line to point at.
    """

    model_config = ConfigDict(frozen=True)

    check: str
    rule: str
    category: Category
    severity: Severity
    detail: str
    artifact: str
    nodeids: tuple[str, ...] = ()
    location: str | None = None
    annotation: str | None = None


class CheckResult(_Model):
    """What one check returns for one observation pair.

    `status` is `"attribution"` for a check that produces evidence without
    completing as a verdict check. See this module's docstring for both halves
    of that contract.
    """

    model_config = ConfigDict(frozen=True)

    check: str
    status: CheckStatus
    evidence: tuple[Evidence, ...]
    artifact: str | None
    dur_ms: int | None


class Verdict(_Model):
    """The aggregate `skeptic.checks.aggregate` writes to `verdict.json`.

    `status` is orthogonal to `verdict`: an INFRA_ERROR run carries
    `verdict=None` and states why in `infra_reason`. `isolation` is the DX8
    stamp naming the runner the verdict was produced under, so a `--no-docker`
    verdict says so in the artifact. `profile` is the verify profile the
    aggregator ran under (`"deterministic"` at M4; a paid lane is wave B),
    stamped from the same caller as `isolation`.

    `checks_infra` names every check `run_verify_layer` caught an exception
    from, in `CHECK_PRECEDENCE` order (decision 8, `skeptic/checks/aggregate.py`).
    A captured check contributes no evidence and sits in neither
    `checks_completed` nor `not_applicable`. FAIL and SUSPECT still stand on
    whatever evidence the surviving checks found; completeness is load-bearing
    only for a PASS, which a captured mandatory check downgrades to
    INFRA_ERROR instead.

    `checks_infra` and `profile` default to `[]` and `""`: no `verdict.json`
    has ever been written (the aggregator that populates this model lands in
    the same change as these two fields), so nothing on disk depends on the
    old shape and the one constructor this module already had
    (`tests/test_evidence.py::_verdict`) keeps working unchanged. No
    `schema_version` bump follows from that (DECISIONS.md row 99).

    Mutable, so the aggregator can fill it as checks report, with
    `validate_assignment=True` so every write still meets the Literals. A
    frozen verdict would push the aggregator into rebuilding the whole model
    per check; an unvalidated assignment would let `status = "totally-bogus"`
    reach `verdict.json`.
    """

    model_config = ConfigDict(validate_assignment=True)

    schema_version: int
    run_id: str
    task_id: str
    variant: str
    status: RunStatus
    verdict: VerdictName | None
    suspect_score: float
    checks_completed: list[str]
    not_applicable: list[str]
    checks_infra: list[str] = []
    evidence: list[Evidence]
    isolation: str
    profile: str = ""
    infra_reason: str | None


def _precedence_index(check: str) -> int:
    """Position in `CHECK_PRECEDENCE`. An unlisted name sorts last."""
    return _PRECEDENCE_INDEX.get(check, len(CHECK_PRECEDENCE))


def _evidence_sort_key(item: Evidence) -> tuple[int, int, str, str, str, str]:
    return (
        SEVERITY_RANK[item.severity],
        _precedence_index(item.check),
        item.rule,
        item.location or "",
        item.nodeids[0] if item.nodeids else "",
        item.detail,
    )


def order_evidence(items: Iterable[Evidence]) -> list[Evidence]:
    """Sort evidence deterministically, so attribution is never a sort artifact.

    Severity first, then check precedence, then `(rule, location, first
    nodeid, detail)`. `detail` sits last as a total-order backstop; every
    component before it is structured.
    """
    return sorted(items, key=_evidence_sort_key)


def split_results(
    results: Iterable[CheckResult],
) -> tuple[list[str], list[str]]:
    """Return `(checks_completed, not_applicable)`, both in precedence order.

    A result with `status="attribution"` appears in neither list. Precedence
    is the sort key because completion time is nondeterministic once checks
    run concurrently. The input is materialized first, since it is read twice
    and a caller may hand over a generator.
    """
    results = list(results)
    completed = sorted(
        (r.check for r in results if r.status == "completed"),
        key=_precedence_index,
    )
    not_applicable = sorted(
        (r.check for r in results if r.status == "not_applicable"),
        key=_precedence_index,
    )
    return completed, not_applicable
