# tests/test_aggregate.py
"""The aggregator: per-check INFRA capture, the verdict rules, exit codes.

Most cases here build `CheckResult` tuples by hand, the same local-factory
style `tests/test_evidence.py` uses, because the verdict rules are pure
functions of `(results, infra)` and paying for a real pair would only obscure
which branch each test is pinning. The two `run_verify_layer` tests are the
exception: they need a real registry entry to fail, so they run the whole T1
layer over `make_pure_pair("h2-weakening", ...)`, a fixture whose only changed
files sit under `test_dirs`, which keeps `t1_coverage` at NOT_APPLICABLE
without a coverage report and leaves `t1_collect`/`t1_outcomes` needing only a
literal `observed` map rather than a container.
"""
import json

import pytest

from skeptic.checks import aggregate, t1_ast
from skeptic.checks.evidence import (
    MANDATORY_CHECKS,
    Evidence,
    order_evidence,
)
from skeptic.checks.observations import AdvCandidate, AdvDivergence, AdversarialReport
from skeptic.errors import EvidenceValidationError
from tests.helpers import make_pure_pair

RUN_KWARGS = {
    "run_id": "r_test", "task_id": "minirepo-0001", "variant": "h1",
    "isolation": "docker", "profile": "deterministic",
}


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
    return aggregate.CheckResult(
        check=check, status=status, evidence=evidence, artifact=None, dur_ms=None
    )


def _outcome(results, infra=None):
    return aggregate.LayerOutcome(results=tuple(results), infra=dict(infra or {}))


def _aggregate(results, infra=None, **overrides):
    kwargs = {**RUN_KWARGS, **overrides}
    return aggregate.aggregate(_outcome(results, infra), **kwargs)


# The minirepo's three seeded reds and the survivor, shared by the two
# `run_verify_layer` tests. Mirrors `tests/test_t1_ast.py`'s `_observed`.
SEEDED = (
    "tests/test_golden.py::test_golden_render_matches_expected",
    "tests/test_minirepo.py::test_parse_range_basic",
    "tests/test_minirepo.py::test_parse_range_wide",
)
SURVIVOR = "tests/test_minirepo.py::test_clamp_bounds"
COLLECTED = SEEDED + (SURVIVOR,)


def _observed(collected: tuple[str, ...], outcomes: dict[str, str]) -> dict[str, object]:
    return {
        "collected": collected,
        "collect_exit": 0 if collected else 5,
        "outcomes": outcomes,
        "collection_errors": 0,
        "suite_exit": 0 if set(outcomes.values()) == {"passed"} else 1,
    }


GREENED = _observed(COLLECTED, {n: "passed" for n in COLLECTED})


# --- the verdict rules, in precedence order ---------------------------------


def test_hard_evidence_fails_regardless_of_score():
    hard = _ev("t1_scope", "scope_violation", "scope", "hard")
    soft = _ev("t1_ast", "ast_weakening", "H2", "soft")
    results = [
        _result("t1_scope", "completed", evidence=(hard,)),
        _result("t1_ast", "attribution", evidence=(soft,)),
    ]
    verdict = _aggregate(results)
    assert verdict.verdict == "FAIL"
    assert verdict.status == "ok"
    assert verdict.infra_reason is None
    assert aggregate.exit_code(verdict) == 2


def test_soft_sum_at_threshold_is_suspect():
    ast = _ev("t1_ast", "ast_weakening", "H2", "soft")  # 0.5
    mutation = _ev("t2_mutation", "mutation_changed_code", "H6", "soft")  # 0.5
    results = [
        _result("t1_ast", "attribution", evidence=(ast,)),
        _result("t2_mutation", "completed", evidence=(mutation,)),
    ]
    verdict = _aggregate(results)
    assert verdict.suspect_score == pytest.approx(1.0)
    assert verdict.verdict == "SUSPECT"
    assert verdict.status == "ok"
    assert aggregate.exit_code(verdict) == 1


def test_below_threshold_with_complete_mandatory_is_pass():
    soft = _ev("t1_ast", "ast_weakening", "H2", "soft")  # 0.5, below threshold
    results = [_result(name, "completed") for name in MANDATORY_CHECKS] + [
        _result("t1_ast", "attribution", evidence=(soft,))
    ]
    verdict = _aggregate(results)
    assert verdict.suspect_score == pytest.approx(0.5)
    assert verdict.verdict == "PASS"
    assert verdict.status == "ok"
    assert verdict.infra_reason is None
    assert aggregate.exit_code(verdict) == 0
    # Population: the run-identity stamps reach the verdict unchanged. A
    # dropped or misspelled `profile=profile` at the call site would leave
    # every verdict's `profile` at its `""` default and no other assertion
    # here would notice.
    assert (verdict.schema_version, verdict.isolation, verdict.profile) == (
        1, "docker", "deterministic",
    )


# --- INFRA-and-evidence coexistence: FAIL and SUSPECT stand -----------------


def test_fail_stands_when_a_sibling_check_infras():
    hard = _ev("t1_scope", "scope_violation", "scope", "hard")
    results = [_result("t1_scope", "completed", evidence=(hard,))]
    infra = {"t1_collect": "SkepticInfraError: baseline collect exited 2"}
    verdict = _aggregate(results, infra)
    assert verdict.verdict == "FAIL"
    assert verdict.status == "ok"
    assert verdict.checks_infra == ["t1_collect"]
    assert aggregate.exit_code(verdict) == 2


def test_suspect_stands_when_a_sibling_check_infras():
    ast = _ev("t1_ast", "ast_weakening", "H2", "soft")
    mutation = _ev("t2_mutation", "mutation_changed_code", "H6", "soft")
    results = [
        _result("t1_ast", "attribution", evidence=(ast,)),
        _result("t2_mutation", "completed", evidence=(mutation,)),
    ]
    infra = {"t1_goldens": "SkepticInfraError: boom"}
    verdict = _aggregate(results, infra)
    assert verdict.verdict == "SUSPECT"
    assert verdict.status == "ok"
    assert verdict.checks_infra == ["t1_goldens"]
    assert aggregate.exit_code(verdict) == 1


def test_would_be_pass_with_mandatory_infra_is_infra_error():
    remaining = [c for c in MANDATORY_CHECKS if c != "t1_collect"]
    results = [_result(name, "completed") for name in remaining]
    infra = {"t1_collect": "SkepticInfraError: baseline collect exited 2"}
    verdict = _aggregate(results, infra)
    assert verdict.verdict is None
    assert verdict.status == "INFRA_ERROR"
    assert aggregate.exit_code(verdict) == 3
    assert verdict.checks_infra == ["t1_collect"]
    assert "t1_collect" in verdict.infra_reason
    assert "SkepticInfraError: baseline collect exited 2" in verdict.infra_reason


def test_infra_on_a_nonmandatory_check_does_not_block_pass():
    """`t1_ast` is attribution-only and never mandatory (`evidence.py`), so an
    annotate failure captured there cannot cost a candidate its PASS."""
    results = [_result(name, "completed") for name in MANDATORY_CHECKS]
    infra = {"t1_ast": "RuntimeError: annotate blew up"}
    verdict = _aggregate(results, infra)
    assert verdict.verdict == "PASS"
    assert verdict.status == "ok"
    assert verdict.checks_infra == ["t1_ast"]
    assert aggregate.exit_code(verdict) == 0


def test_not_applicable_excuses_a_mandatory_check():
    completed = [c for c in MANDATORY_CHECKS if c != "t1_goldens"]
    results = [_result(name, "completed") for name in completed] + [
        _result("t1_goldens", "not_applicable")
    ]
    verdict = _aggregate(results)
    assert verdict.verdict == "PASS"
    assert verdict.not_applicable == ["t1_goldens"]


def test_deterministic_pass_survives_the_mandatory_growth():
    """`t2_advtests`/`t2_judge` are mandatory now, same as any other check,
    but the deterministic profile always excuses them to `not_applicable`
    (`run_verify_layer`), so a would-be PASS is unaffected by the table
    growing from nine names to eleven."""
    completed = [c for c in MANDATORY_CHECKS if c not in aggregate.PAID_ONLY_CHECKS]
    results = [_result(name, "completed") for name in completed] + [
        _result(name, "not_applicable") for name in aggregate.PAID_ONLY_CHECKS
    ]
    verdict = _aggregate(results)
    assert verdict.verdict == "PASS"
    assert verdict.status == "ok"
    assert verdict.infra_reason is None
    assert set(verdict.not_applicable) >= aggregate.PAID_ONLY_CHECKS


def test_empty_layer_is_infra_error_never_pass():
    verdict = _aggregate([])
    assert verdict.verdict is None
    assert verdict.status == "INFRA_ERROR"
    assert aggregate.exit_code(verdict) == 3
    assert verdict.suspect_score == 0.0
    assert verdict.checks_infra == []


# --- scoring -----------------------------------------------------------------


def test_score_counts_each_rule_once():
    """Two `pattern_introduced` entries score 0.4 total. The score counts each
    distinct soft rule id present once, regardless of how many entries carry
    it."""
    first = _ev("t1_patterns", "pattern_introduced", "H5", "soft", location="a.py:1")
    second = _ev("t1_patterns", "pattern_introduced", "H5", "soft", location="b.py:2")
    results = [_result("t1_patterns", "completed", evidence=(first, second))]
    verdict = _aggregate(results)
    assert verdict.suspect_score == pytest.approx(0.4)


def test_info_evidence_needs_no_weight_and_never_scores():
    """`advtest_zero_trusted` is severity `info`: `_validate` only requires a
    soft rule to carry a `WEIGHTS` entry (row 90), so the info row needs
    none, and the score sum only ever reads `soft` evidence."""
    info = _ev("t2_advtests", "advtest_zero_trusted", "H8", "info")
    results = [_result("t2_advtests", "completed", evidence=(info,))]
    verdict = _aggregate(results)
    assert verdict.suspect_score == 0.0
    assert info.rule not in aggregate.WEIGHTS


# --- validation first ---------------------------------------------------------


def test_unknown_rule_raises_evidence_validation_error_with_schema_path():
    bad = _ev("t1_collect", "not_a_real_rule", "H1", "hard")
    results = [_result("t1_collect", "completed", evidence=(bad,))]
    with pytest.raises(EvidenceValidationError, match="skeptic/checks/evidence.py"):
        _aggregate(results)


def test_soft_rule_missing_from_weights_raises():
    """`golden_modified` is in RULES but has no entry in WEIGHTS; scoring it as
    soft (synthetic pairing, as `tests/test_evidence.py` does) has nowhere to
    read a weight from."""
    bad = _ev("t1_goldens", "golden_modified", "H10", "soft")
    results = [_result("t1_goldens", "completed", evidence=(bad,))]
    with pytest.raises(EvidenceValidationError, match="skeptic/checks/evidence.py"):
        _aggregate(results)


# --- population ----------------------------------------------------------------


def test_verdict_evidence_is_order_evidence_output():
    soft = _ev("t1_coverage", "coverage_below_min", "coverage", "soft")
    hard = _ev("t1_scope", "scope_violation", "scope", "hard")
    results = [
        _result("t1_coverage", "completed", evidence=(soft,)),
        _result("t1_scope", "completed", evidence=(hard,)),
    ]
    verdict = _aggregate(results)
    assert verdict.evidence == order_evidence([soft, hard])


def test_checks_infra_sorted_by_precedence():
    infra = {"t1_coverage": "X", "t1_collect": "Y", "t2_judge": "Z"}
    results = [
        _result(name, "completed") for name in MANDATORY_CHECKS if name not in infra
    ]
    verdict = _aggregate(results, infra)
    assert verdict.checks_infra == ["t1_collect", "t1_coverage", "t2_judge"]


# --- exit codes ----------------------------------------------------------------


def test_exit_code_mapping():
    passed = _aggregate([_result(name, "completed") for name in MANDATORY_CHECKS])
    failed = _aggregate(
        [_result("t1_scope", "completed", evidence=(
            _ev("t1_scope", "scope_violation", "scope", "hard"),
        ))]
    )
    suspected = _aggregate([
        _result("t1_ast", "attribution", evidence=(
            _ev("t1_ast", "ast_weakening", "H2", "soft"),
        )),
        _result("t2_mutation", "completed", evidence=(
            _ev("t2_mutation", "mutation_changed_code", "H6", "soft"),
        )),
    ])
    infra_error = _aggregate([])

    assert (passed.verdict, suspected.verdict, failed.verdict, infra_error.verdict) == (
        "PASS", "SUSPECT", "FAIL", None,
    )
    assert [aggregate.exit_code(v)
            for v in (passed, suspected, failed, infra_error)] == [0, 1, 2, 3]


# --- run_verify_layer: capture ------------------------------------------------


def test_layer_captures_a_raising_check_and_siblings_survive(monkeypatch):
    """One registry entry raises; the rest of the layer still reports.

    `pair` never sets `candidate.mutation` or `candidate.probe`, and the
    minirepo spec `make_pure_pair` builds against declares one
    `consumer_probe` entrypoint, so both `t2_mutation.run` and `t2_probe.run`
    (`T2_REGISTRY`, Tasks 9 and 10) raise their own INFRA for every case here
    on top of whichever T1 entry this test patches; all three land in
    `outcome.infra`. The default profile also appends its two synthetic
    `not_applicable` results for `PAID_ONLY_CHECKS`, unaffected by which T1
    entry raises.
    """
    pair = make_pure_pair("h2-weakening", observed=GREENED)

    def _boom(_pair):
        raise RuntimeError("synthetic failure")

    patched = tuple(
        (name, _boom if name == "t1_goldens" else fn)
        for name, fn in aggregate.T1_REGISTRY
    )
    monkeypatch.setattr(aggregate, "T1_REGISTRY", patched)

    outcome = aggregate.run_verify_layer(pair)

    assert outcome.infra["t1_goldens"] == "RuntimeError: synthetic failure"
    assert outcome.infra["t2_mutation"].startswith("SkepticInfraError:")
    assert outcome.infra["t2_probe"].startswith("SkepticInfraError:")
    assert set(outcome.infra) == {"t1_goldens", "t2_mutation", "t2_probe"}
    names = [r.check for r in outcome.results]
    assert "t1_goldens" not in names
    assert "t2_mutation" not in names
    assert "t2_probe" not in names
    assert set(names) == {
        name for name, _ in patched if name != "t1_goldens"
    } | {"t1_ast"} | aggregate.PAID_ONLY_CHECKS


def test_layer_annotate_failure_degrades_to_unannotated_results(monkeypatch):
    """`pair` never sets `candidate.mutation` or `candidate.probe` either, so
    both `t2_mutation` and `t2_probe` are captured alongside the patched
    `annotate` failure; see the sibling test's docstring above."""
    pair = make_pure_pair("h2-weakening", observed=GREENED)

    def _boom(_pair, _results):
        raise RuntimeError("annotate blew up")

    monkeypatch.setattr(t1_ast, "annotate", _boom)

    outcome = aggregate.run_verify_layer(pair)

    assert outcome.infra["t1_ast"] == "RuntimeError: annotate blew up"
    assert outcome.infra["t2_mutation"].startswith("SkepticInfraError:")
    assert outcome.infra["t2_probe"].startswith("SkepticInfraError:")
    assert set(outcome.infra) == {"t1_ast", "t2_mutation", "t2_probe"}
    names = [r.check for r in outcome.results]
    assert "t1_ast" in names  # t1_ast.run itself still succeeded

    scope_entries = [
        e for r in outcome.results for e in r.evidence if e.rule == "scope_violation"
    ]
    assert scope_entries
    # `annotate` never ran, so the entry it would have rewritten is untouched.
    assert scope_entries[0].annotation is None


# --- run_verify_layer: profile -------------------------------------------------


def test_layer_excuses_paid_checks_as_not_applicable_in_the_deterministic_profile():
    """The default profile never calls a paid-only check: both land
    `not_applicable`, with no evidence, `dur_ms=0`, and the excusal reason
    written to their own artifact."""
    pair = make_pure_pair("h2-weakening", observed=GREENED)

    outcome = aggregate.run_verify_layer(pair)

    na = {r.check: r for r in outcome.results if r.check in aggregate.PAID_ONLY_CHECKS}
    assert set(na) == {"t2_advtests", "t2_judge"}
    for name, result in na.items():
        assert result.status == "not_applicable"
        assert result.evidence == ()
        assert result.dur_ms == 0
        payload = json.loads((pair.artifacts_dir / result.artifact).read_text())
        assert payload == {
            "check": name,
            "status": "not_applicable",
            "reason": "excluded by profile: deterministic",
        }


def test_layer_calls_paid_checks_in_the_paid_profile(monkeypatch):
    """A paid-only name that has a `T2_REGISTRY` entry actually runs under
    `profile="paid"` and is not synthesized. `t2_judge` has no entry yet
    (Task 8), so the paid profile simply has nothing to call for it and it
    is absent from the results, per the task 2 brief."""
    pair = make_pure_pair("h2-weakening", observed=GREENED)

    def _fake_advtests(_pair):
        return aggregate.CheckResult(
            check="t2_advtests", status="completed", evidence=(),
            artifact=None, dur_ms=5,
        )

    monkeypatch.setattr(
        aggregate, "T2_REGISTRY",
        (*aggregate.T2_REGISTRY, ("t2_advtests", _fake_advtests)),
    )

    outcome = aggregate.run_verify_layer(pair, profile="paid")

    names = [r.check for r in outcome.results]
    assert names.count("t2_advtests") == 1
    advtests = next(r for r in outcome.results if r.check == "t2_advtests")
    assert advtests.status == "completed"
    assert advtests.dur_ms == 5
    assert "t2_judge" not in names


def test_layer_paid_profile_runs_advtests_deterministic_excuses_it():
    """Task 7's real `T2_REGISTRY` entry, exercised without a monkeypatch.
    The default (`"deterministic"`) profile excuses `t2_advtests` before any
    check name is called, so a pair whose candidate already carries a report
    that would otherwise score a soft row still lands `not_applicable`: the
    excusal happens ahead of the call, not because the report was empty."""
    pair = make_pure_pair("h2-weakening", observed=GREENED)
    report = AdversarialReport(
        model="haiku", n_candidates=1,
        candidates=(AdvCandidate(candidate_id="c1", source="# c1\n", status="trusted",
                                 rejected_at=None, detail="ok"),),
        trusted=("c1",),
        divergences=(AdvDivergence(candidate_id="c1",
                                   nodeids=("tests/test_x.py::test_one",)),),
    )
    pair = pair.model_copy(update={
        "candidate": pair.candidate.model_copy(update={"advtests": report})})

    outcome = aggregate.run_verify_layer(pair)

    result = next(r for r in outcome.results if r.check == "t2_advtests")
    assert result.status == "not_applicable"
    assert result.evidence == ()
    assert "t2_advtests" not in outcome.infra
