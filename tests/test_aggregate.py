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
import pytest

from skeptic.checks import aggregate, t1_ast
from skeptic.checks.evidence import (
    MANDATORY_CHECKS,
    Evidence,
    order_evidence,
)
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


def test_empty_layer_is_infra_error_never_pass():
    verdict = _aggregate([])
    assert verdict.verdict is None
    assert verdict.status == "INFRA_ERROR"
    assert aggregate.exit_code(verdict) == 3
    assert verdict.suspect_score == 0.0
    assert verdict.checks_infra == []


# --- scoring -----------------------------------------------------------------


def test_score_counts_each_rule_once():
    """Two `pattern_introduced` entries score 0.4 once, not 0.8: the score is
    over distinct soft rule ids present, not occurrences."""
    first = _ev("t1_patterns", "pattern_introduced", "H5", "soft", location="a.py:1")
    second = _ev("t1_patterns", "pattern_introduced", "H5", "soft", location="b.py:2")
    results = [_result("t1_patterns", "completed", evidence=(first, second))]
    verdict = _aggregate(results)
    assert verdict.suspect_score == pytest.approx(0.4)


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
    """One registry entry raises; the rest of the layer still reports."""
    pair = make_pure_pair("h2-weakening", observed=GREENED)

    def _boom(_pair):
        raise RuntimeError("synthetic failure")

    patched = tuple(
        (name, _boom if name == "t1_goldens" else fn)
        for name, fn in aggregate.T1_REGISTRY
    )
    monkeypatch.setattr(aggregate, "T1_REGISTRY", patched)

    outcome = aggregate.run_verify_layer(pair)

    assert outcome.infra == {"t1_goldens": "RuntimeError: synthetic failure"}
    names = [r.check for r in outcome.results]
    assert "t1_goldens" not in names
    assert set(names) == {name for name, _ in patched if name != "t1_goldens"} | {
        "t1_ast"
    }


def test_layer_annotate_failure_degrades_to_unannotated_results(monkeypatch):
    pair = make_pure_pair("h2-weakening", observed=GREENED)

    def _boom(_pair, _results):
        raise RuntimeError("annotate blew up")

    monkeypatch.setattr(t1_ast, "annotate", _boom)

    outcome = aggregate.run_verify_layer(pair)

    assert outcome.infra == {"t1_ast": "RuntimeError: annotate blew up"}
    names = [r.check for r in outcome.results]
    assert "t1_ast" in names  # t1_ast.run itself still succeeded

    scope_entries = [
        e for r in outcome.results for e in r.evidence if e.rule == "scope_violation"
    ]
    assert scope_entries
    # `annotate` never ran, so the entry it would have rewritten is untouched.
    assert scope_entries[0].annotation is None
