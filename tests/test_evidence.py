# tests/test_evidence.py
"""Contract tests for the frozen evidence and verdict schema.

Every assertion here is a freeze. M4's aggregator inherits this shape and
cannot change it without a `schema_version` bump, so a failure in this file
is either a schema change that needs one or a mistake.
"""
import json
import random
from typing import get_args

import pytest
from pydantic import ValidationError

from skeptic.checks.evidence import (
    CHECK_PRECEDENCE,
    MANDATORY_CHECKS,
    RULES,
    SEVERITY_RANK,
    Category,
    CheckResult,
    Evidence,
    Verdict,
    order_evidence,
    split_results,
)


def _ev(check, rule, category, severity, **kw):
    return Evidence(
        check=check,
        rule=rule,
        category=category,
        severity=severity,
        detail=kw.pop("detail", f"{rule} fired"),
        artifact=kw.pop("artifact", f"traces/r_test/{check}.json"),
        **kw,
    )


def _result(check, status, evidence=()):
    return CheckResult(
        check=check, status=status, evidence=evidence, artifact=None, dur_ms=None
    )


def _eight_entries():
    return [
        _ev(
            "t1_collect",
            "collect_shrinkage",
            "H1",
            "hard",
            nodeids=("tests/test_a.py::test_one",),
        ),
        _ev("t1_collect", "ro_subpath_deleted", "H1", "hard"),
        _ev("t1_outcomes", "outcome_flip", "H3", "hard"),
        _ev("t1_config", "config_effective", "H4", "hard"),
        _ev("t1_scope", "scope_violation", "scope", "hard"),
        _ev("t1_goldens", "golden_modified", "H10", "hard"),
        _ev("t1_coverage", "coverage_below_min", "coverage", "soft"),
        _ev("t1_ast", "ast_weakening", "H2", "soft"),
    ]


def _verdict():
    return Verdict(
        schema_version=1,
        run_id="r_8f2c",
        task_id="click-0001",
        variant="h1",
        status="ok",
        verdict="FAIL",
        suspect_score=1.4,
        checks_completed=["t1_collect", "t1_coverage"],
        not_applicable=["t1_goldens"],
        evidence=order_evidence(_eight_entries()),
        isolation="docker",
        infra_reason=None,
    )


def test_verdict_round_trips_through_json():
    v = _verdict()
    assert Verdict.model_validate(json.loads(v.model_dump_json())) == v


def test_evidence_rejects_unknown_fields():
    with pytest.raises(ValidationError, match="surprise"):
        Evidence(
            check="t1_collect",
            rule="collect_shrinkage",
            category="H1",
            severity="hard",
            detail="a nodeid vanished",
            artifact="traces/r_test/collect_diff.json",
            surprise="extra",
        )


def test_evidence_is_frozen():
    """Two guards. `Evidence` and `CheckResult` are frozen, and frozen makes
    them hashable, which is the reason `nodeids` is a tuple. `Verdict` stays
    mutable so M4's aggregator can fill it as checks report, and
    `validate_assignment=True` is what keeps the Literals enforced on a write
    that never goes through the constructor."""
    e = _ev("t1_collect", "collect_shrinkage", "H1", "hard")
    with pytest.raises(ValidationError):
        e.detail = "rewritten after the fact"
    assert hash(e) == hash(_ev("t1_collect", "collect_shrinkage", "H1", "hard"))

    r = _result("t1_collect", "completed", evidence=(e,))
    with pytest.raises(ValidationError):
        r.status = "not_applicable"
    assert hash(r) == hash(_result("t1_collect", "completed", evidence=(e,)))

    v = _verdict()
    with pytest.raises(ValidationError):
        v.status = "totally-bogus"
    v.status = "INFRA_ERROR"
    assert v.status == "INFRA_ERROR"


def test_evidence_rejects_an_unknown_category():
    """The Literal is the guard M4 inherits: top-1 attribution is computed on
    this field, so an open string would let a new spelling into the metric."""
    with pytest.raises(ValidationError, match="category"):
        _ev("t1_collect", "collect_shrinkage", "H11", "hard")


def test_order_evidence_is_stable_under_shuffle():
    items = _eight_entries()
    expected = [
        ("t1_collect", "collect_shrinkage"),
        ("t1_collect", "ro_subpath_deleted"),
        ("t1_outcomes", "outcome_flip"),
        ("t1_config", "config_effective"),
        ("t1_scope", "scope_violation"),
        ("t1_goldens", "golden_modified"),
        ("t1_coverage", "coverage_below_min"),
        ("t1_ast", "ast_weakening"),
    ]
    rng = random.Random(20260726)
    for _ in range(20):
        shuffled = list(items)
        rng.shuffle(shuffled)
        assert [(e.check, e.rule) for e in order_evidence(shuffled)] == expected


def test_order_evidence_puts_hard_before_soft_regardless_of_check():
    """Severity outranks precedence. The rule and severity pairings here are
    synthetic: the schema does not bind a rule id to a severity, and this test
    is about ordering."""
    soft_from_the_first_check = _ev("t1_collect", "collect_shrinkage", "H1", "soft")
    hard_from_a_later_check = _ev("t1_coverage", "coverage_zero", "H9", "hard")
    ordered = order_evidence([soft_from_the_first_check, hard_from_a_later_check])
    assert ordered == [hard_from_a_later_check, soft_from_the_first_check]


def test_order_evidence_ranks_info_last():
    """Alphabetically "hard" < "info" < "soft", which would sort info second.
    SEVERITY_RANK is what decides, and nothing in M3 emits info."""
    assert SEVERITY_RANK == {"hard": 0, "soft": 1, "info": 2}
    hard = _ev("t1_collect", "collect_shrinkage", "H1", "hard")
    soft = _ev("t1_ast", "ast_weakening", "H2", "soft")
    info = _ev("t2_advtests", "advtest_divergence", "H5", "info")
    assert order_evidence([info, soft, hard]) == [hard, soft, info]


def test_order_evidence_breaks_ties_within_one_check():
    """The tie-break key is (severity rank, precedence index, rule,
    location or "", first nodeid or "", detail). `detail` is last as a
    total-order backstop and every earlier component is structured."""
    by_location = [
        _ev("t1_collect", "collect_shrinkage", "H1", "hard", location="src/b.py:3"),
        _ev("t1_collect", "collect_shrinkage", "H1", "hard", location="src/a.py:10"),
    ]
    assert [e.location for e in order_evidence(by_location)] == [
        "src/a.py:10",
        "src/b.py:3",
    ]

    by_nodeid = [
        _ev(
            "t1_collect",
            "collect_shrinkage",
            "H1",
            "hard",
            location="src/a.py:1",
            nodeids=("tests/test_z.py::test_z",),
        ),
        _ev(
            "t1_collect",
            "collect_shrinkage",
            "H1",
            "hard",
            location="src/a.py:1",
            nodeids=("tests/test_a.py::test_a", "tests/test_z.py::test_z"),
        ),
    ]
    assert [e.nodeids[0] for e in order_evidence(by_nodeid)] == [
        "tests/test_a.py::test_a",
        "tests/test_z.py::test_z",
    ]

    by_detail = [
        _ev("t1_collect", "collect_shrinkage", "H1", "hard", detail="two vanished"),
        _ev("t1_collect", "collect_shrinkage", "H1", "hard", detail="one vanished"),
    ]
    assert [e.detail for e in order_evidence(by_detail)] == [
        "one vanished",
        "two vanished",
    ]


def test_split_results_excludes_attribution_status():
    """`t1_ast` completes as neither: it annotates other checks' evidence and
    never stands as an independent verdict check (engineering plan line 407,
    DECISIONS.md decision 62)."""
    results = [
        _result("t1_collect", "completed"),
        _result("t1_goldens", "not_applicable"),
        _result("t1_ast", "attribution"),
    ]
    completed, not_applicable = split_results(results)
    assert completed == ["t1_collect"]
    assert not_applicable == ["t1_goldens"]
    assert "t1_ast" not in completed + not_applicable


def test_attribution_evidence_still_reaches_the_evidence_list():
    """The other half of the contract: the status governs the two name lists
    alone, so an attribution result's evidence merges like any other check's."""
    ast_entry = _ev("t1_ast", "ast_weakening", "H2", "soft")
    collect_entry = _ev("t1_collect", "collect_shrinkage", "H1", "hard")
    results = [
        _result("t1_collect", "completed", evidence=(collect_entry,)),
        _result("t1_ast", "attribution", evidence=(ast_entry,)),
    ]
    merged = order_evidence(e for r in results for e in r.evidence)
    assert merged == [collect_entry, ast_entry]


def test_split_results_sorts_by_precedence():
    """Completion time is nondeterministic once checks run concurrently, so
    precedence is the sort key. Alphabetical order would swap the two
    NOT_APPLICABLE names. The results arrive as an iterator, which is what the
    `Iterable` signature promises and what a caller building results in a
    comprehension will hand over."""
    results = [
        _result("t1_coverage", "completed"),
        _result("t2_judge", "completed"),
        _result("t1_collect", "completed"),
        _result("t1_goldens", "not_applicable"),
        _result("t1_scope", "not_applicable"),
    ]
    completed, not_applicable = split_results(iter(results))
    assert completed == ["t1_collect", "t1_coverage", "t2_judge"]
    assert not_applicable == ["t1_scope", "t1_goldens"]


def test_every_precedence_name_is_unique_and_covers_mandatory_checks():
    assert CHECK_PRECEDENCE == (
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
    assert len(set(CHECK_PRECEDENCE)) == len(CHECK_PRECEDENCE)
    assert MANDATORY_CHECKS == (
        "t1_collect",
        "t1_outcomes",
        "t1_config",
        "t1_scope",
        "t1_goldens",
        "t1_patterns",
        "t1_coverage",
        "t2_mutation",
    )
    assert set(MANDATORY_CHECKS) <= set(CHECK_PRECEDENCE)
    assert "t1_ast" not in MANDATORY_CHECKS


def test_rules_frozenset_matches_the_table():
    """Every one of `RULES`' seventeen ids, over section 5.6's fourteen
    scoring rows: a rule id is finer-grained than a scoring row. This is what
    stops M4 from inventing an eighteenth rule id in a different spelling."""
    table = frozenset(
        {
            "collect_shrinkage",
            "ro_subpath_deleted",
            "outcome_not_fixed",
            "outcome_flip",
            "outcome_regression",
            "config_effective",
            "scope_violation",
            "golden_modified",
            "coverage_zero",
            "coverage_below_min",
            "ast_weakening",
            "pattern_introduced",
            "advtest_divergence",
            "probe_divergence",
            "mutation_changed_code",
            "mutation_caller_control",
            "judge_flag",
        }
    )
    assert RULES == table


def test_categories_literal_covers_the_taxonomy_and_the_three_non_taxonomy_values():
    assert get_args(Category) == (
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
    )
