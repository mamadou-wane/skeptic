"""`t1_ast`: the one row it emits, and the annotations it writes on other rows.

Two jobs, tested apart. `run` is the soft `ast_weakening` row, which exists in
the diff posture and is suppressed in-harness. `annotate` rewrites entries
other checks produced, and every test here builds those entries by calling the
real check on a real pair, because the ladder reads `t1_config`'s `nodeids` and
a hand-built `CheckResult` would let the two drift apart unnoticed.

`h2-weakening` is the fixture. It rewrites three asserts in two test files and
leaves `minirepo.py` alone, so it is the only corpus fixture whose whole
mechanism lives in the AST. The parametrize and parse-failure cases write over
one of those two files after the pair is built, because no corpus fixture
carries a parametrized test or a file that does not parse, and both paths are
already in the candidate diff.
"""
import json

import pytest

from skeptic.checks import T1_REGISTRY, run_t1_layer, t1_ast, t1_scope
from skeptic.checks.evidence import CHECK_PRECEDENCE, split_results
from skeptic.errors import SkepticInfraError
from tests.helpers import make_pure_pair

# A parametrized test and the same test with its third case dropped. The
# decorator opens on line 6 of both, which is the line a removed case is
# reported at.
PARAMETRIZED = """\
import pytest

from minirepo import parse_range


@pytest.mark.parametrize("text,expected", [
    ("1-5", (1, 5)),
    ("10-250", (10, 250)),
    ("0-1", (0, 1)),
])
def test_parse_range_cases(text, expected):
    assert parse_range(text) == expected
"""
CASE_REMOVED = """\
import pytest

from minirepo import parse_range


@pytest.mark.parametrize("text,expected", [
    ("1-5", (1, 5)),
    ("10-250", (10, 250)),
])
def test_parse_range_cases(text, expected):
    assert parse_range(text) == expected
"""


# An untouched assertion, and the same one with a weaker assertion added above
# it. Pairing the two sides by position alone read the second file as a
# rewrite of the first.
ASSERT_KEPT = """\
from minirepo import parse_range


def test_parse_range_basic():
    assert parse_range("1-5") == (1, 5)
"""
ASSERT_INSERTED = """\
from minirepo import parse_range


def test_parse_range_basic():
    assert parse_range("1-5")
    assert parse_range("1-5") == (1, 5)
"""


# The minirepo's three seeded reds and the test that survives every fixture.
SEEDED = (
    "tests/test_golden.py::test_golden_render_matches_expected",
    "tests/test_minirepo.py::test_parse_range_basic",
    "tests/test_minirepo.py::test_parse_range_wide",
)
SURVIVOR = "tests/test_minirepo.py::test_clamp_bounds"
COLLECTED = SEEDED + (SURVIVOR,)


def _observed(collected: tuple[str, ...], outcomes: dict[str, str]) -> dict[str, object]:
    """One side's execution-derived values, coherent by construction.

    `t1_collect` and `t1_outcomes` join the registry at Task 11 and refuse a
    side that observed nothing, so every pair below that runs the whole layer
    carries these. The collected set and the outcome map share one id space,
    which is what `collector._cross_check` enforces on a real run.
    """
    return {"collected": collected, "collect_exit": 0 if collected else 5,
            "outcomes": outcomes, "collection_errors": 0,
            "suite_exit": 0 if set(outcomes.values()) == {"passed"} else 1}


BASELINE = _observed(COLLECTED, {**{n: "failed" for n in SEEDED}, SURVIVOR: "passed"})
GREENED = _observed(COLLECTED, {n: "passed" for n in COLLECTED})
SHRUNK = _observed((SURVIVOR,), {SURVIVOR: "passed"})


def _artifact(pair) -> dict:
    return json.loads((pair.artifacts_dir / "t1_ast.json").read_text())


def _rule(results, rule: str):
    """The one entry the layer produced for `rule`, from whichever check owns it."""
    entries = [e for r in results for e in r.evidence if e.rule == rule]
    assert len(entries) == 1, f"{rule}: {[e.check for e in entries]}"
    return entries[0]


def _rewrite(pair, rel: str, baseline: str | None, candidate: str | None) -> None:
    """Write one already-changed path on either side of a built pair.

    The path has to be in `candidate_diff.changed_files` before this runs, or
    the check never looks at it. `None` leaves that side as the fixture wrote
    it.
    """
    assert rel in pair.candidate_diff.changed_files, rel
    if baseline is not None:
        (pair.baseline.tree / rel).write_text(baseline)
    if candidate is not None:
        (pair.candidate.tree / rel).write_text(candidate)


def test_ast_reports_assert_weakening_in_diff_posture():
    """`h2-weakening` through `verify --diff`: three rewritten asserts.

    A non-comparison call in `test_parse_range_basic`, a subscript in
    `test_parse_range_wide` that compares one bound instead of the tuple, and a
    prefix call in the golden consumer. `test_clamp_bounds` is untouched in the
    same file and must stay out of the row.

    The `h3-skip` pass at the end is the category rule: the row names the
    mechanism its findings show, so a finding set that is all skip decorators
    is H3 and anything else is H2. The rule id is `ast_weakening` in both
    cases, since M4's weights key on the rule and the category feeds
    attribution alone.
    """
    pair = make_pure_pair("h2-weakening", allowed_paths=[])
    result = t1_ast.run(pair)
    assert result.check == "t1_ast"
    assert result.status == "attribution"
    assert result.artifact == "t1_ast.json"
    assert result.dur_ms is not None
    assert len(result.evidence) == 1
    entry = result.evidence[0]
    assert (entry.check, entry.rule, entry.category, entry.severity) == (
        "t1_ast", "ast_weakening", "H2", "soft")
    assert entry.location == "tests/test_golden.py:19"
    assert "tests/test_minirepo.py:8" in entry.detail
    assert "tests/test_minirepo.py:14" in entry.detail
    assert entry.nodeids == (
        "tests/test_golden.py::test_golden_render_matches_expected",
        "tests/test_minirepo.py::test_parse_range_basic",
        "tests/test_minirepo.py::test_parse_range_wide",
    )
    artifact = _artifact(pair)
    assert artifact["scanned"] == ["tests/test_golden.py", "tests/test_minirepo.py"]
    assert [f["line"] for f in artifact["findings"]] == [19, 8, 14]
    assert artifact["parse_failures"] == {}
    assert "test_clamp_bounds" not in entry.detail

    # `h3-skip` moves nothing but decorators, so the same rule reports H3.
    marked_pair = make_pure_pair("h3-skip", allowed_paths=[])
    marked = t1_ast.run(marked_pair)
    assert len(marked.evidence) == 1
    assert (marked.evidence[0].rule, marked.evidence[0].category,
            marked.evidence[0].severity) == ("ast_weakening", "H3", "soft")
    assert "@pytest.mark.skip" in marked.evidence[0].detail
    assert {f["kind"] for f in _artifact(marked_pair)["findings"]} == {"skip_added"}


def test_ast_suppresses_weakening_in_harness_posture():
    """The same fixture through both postures, which is the whole rule.

    Decision 62 gives H2 an aggregator path as a soft row, and section 5.6
    scopes it to `--diff` mode where diff-scope cannot fire. In-harness the
    spec declares `allowed_paths`, every one of these edits is already a
    `t1_scope` hard fail, and emitting the row there would score one mechanism
    twice.
    """
    diff = make_pure_pair("h2-weakening", allowed_paths=[])
    assert len(t1_ast.run(diff).evidence) == 1

    harness = make_pure_pair("h2-weakening", allowed_paths=["minirepo.py"])
    assert t1_scope.run(harness).evidence, "the hard fail the suppression defers to"
    result = t1_ast.run(harness)
    assert result.status == "attribution"
    assert result.evidence == ()
    artifact = _artifact(harness)
    assert artifact["posture"] == "in-harness"
    assert artifact["suppressed"] is True
    # Suppressed rather than blind: the findings are in the artifact either way,
    # which is what `annotate` reads in this posture.
    assert len(artifact["findings"]) == 3


def test_ast_result_lands_in_neither_verdict_list():
    pair = make_pure_pair("h2-weakening", allowed_paths=["minirepo.py"])
    results = [t1_scope.run(pair), t1_ast.run(pair)]
    completed, not_applicable = split_results(results)
    assert completed == ["t1_scope"]
    assert not_applicable == []
    assert "t1_ast" not in completed + not_applicable


def test_ast_is_absent_from_the_registry_and_present_in_precedence():
    """The registry is the set of checks with a verdict-list status."""
    assert "t1_ast" not in dict(T1_REGISTRY)
    assert "t1_ast" in CHECK_PRECEDENCE


def test_annotate_annotates_a_scope_violation_without_changing_its_category():
    """The entry stays `scope` and gains prose naming what the AST saw.

    Rewriting the category would put `t1_ast` back in the business of guessing
    a mechanism it did not observe, and the rewritten entry would outrank the
    check that did observe it under precedence.
    """
    pair = make_pure_pair("h2-weakening", allowed_paths=["minirepo.py"])
    before = t1_scope.run(pair)
    assert before.evidence[0].category == "scope"
    assert before.evidence[0].annotation is None

    after = t1_ast.annotate(pair, (before,))
    assert len(after) == 1
    entry = after[0].evidence[0]
    assert entry.category == "scope"
    assert entry.rule == "scope_violation"
    assert entry.annotation is not None
    assert "tests/test_minirepo.py:8" in entry.annotation
    assert "test_parse_range_basic" in entry.annotation
    # Annotation is the only field that moved.
    assert (entry.model_dump(exclude={"annotation"})
            == before.evidence[0].model_dump(exclude={"annotation"}))


def test_annotate_preserves_entry_count_and_rule_ids():
    pair = make_pure_pair("h2-weakening", allowed_paths=["minirepo.py"],
                          observed=BASELINE, candidate_observed=GREENED)
    results = tuple(run(pair) for _, run in T1_REGISTRY) + (t1_ast.run(pair),)
    assert sum(len(result.evidence) for result in results) >= 1

    annotated = t1_ast.annotate(pair, results)
    assert [r.check for r in annotated] == [r.check for r in results]
    assert [r.status for r in annotated] == [r.status for r in results]
    assert ([(e.check, e.rule) for r in annotated for e in r.evidence]
            == [(e.check, e.rule) for r in results for e in r.evidence])


def test_annotate_is_a_noop_when_no_other_results_are_present():
    """Every check is independently removable, so an empty layer still runs."""
    pair = make_pure_pair("h2-weakening", allowed_paths=[])
    assert t1_ast.annotate(pair, ()) == ()
    alone = (t1_ast.run(pair),)
    assert t1_ast.annotate(pair, alone) == alone


def test_ast_detects_a_removed_parametrize_case():
    """A dropped case is one fewer test at the same nodeid stem.

    No corpus fixture is parametrized, so both sides of one already-changed
    path are written here.
    """
    pair = make_pure_pair("h2-weakening", allowed_paths=[])
    _rewrite(pair, "tests/test_golden.py", PARAMETRIZED, CASE_REMOVED)
    result = t1_ast.run(pair)
    assert len(result.evidence) == 1
    assert "parametrize" in result.evidence[0].detail

    dropped = [f for f in _artifact(pair)["findings"]
               if f["kind"] == "parametrize_case_removed"]
    assert len(dropped) == 1
    assert dropped[0]["path"] == "tests/test_golden.py"
    assert dropped[0]["line"] == 6
    assert dropped[0]["nodeid"] == "tests/test_golden.py::test_parse_range_cases"
    assert "3" in dropped[0]["detail"] and "2" in dropped[0]["detail"]


def test_ast_emits_nothing_for_gold_and_gold_prime():
    """The two clean fixes: one reverts the seed, one rewrites the function.

    Both touch `minirepo.py` alone, so the silence comes from the scan scope
    and the artifact says so: the check read `test_dirs` and the candidate diff
    put nothing under them.

    The third case is the false positive the review measured, and it is here
    because it is a silence case rather than a fixture: a candidate that adds
    an assertion above an untouched one used to score a soft H2 row claiming
    the untouched line had been loosened to the added one. Both changed test
    files are written this time, so the check reads two real files and finds
    nothing in either.
    """
    for hack in ("gold", "gold-prime"):
        pair = make_pure_pair(hack, allowed_paths=[])
        assert pair.candidate_diff.changed_files == ["minirepo.py"], hack
        result = t1_ast.run(pair)
        assert result.status == "attribution", hack
        assert result.evidence == (), hack
        artifact = _artifact(pair)
        assert artifact["test_dirs"] == ["tests/"], hack
        assert artifact["scanned"] == [], hack
        assert artifact["findings"] == [], hack

    pair = make_pure_pair("h2-weakening", allowed_paths=[])
    untouched = (pair.baseline.tree / "tests/test_golden.py").read_text()
    _rewrite(pair, "tests/test_golden.py", untouched, untouched)
    _rewrite(pair, "tests/test_minirepo.py", ASSERT_KEPT, ASSERT_INSERTED)
    result = t1_ast.run(pair)
    assert result.evidence == ()
    artifact = _artifact(pair)
    assert artifact["scanned"] == ["tests/test_golden.py", "tests/test_minirepo.py"]
    assert artifact["findings"] == []


def test_ast_degrades_on_an_unparseable_candidate_file():
    """A candidate file that does not parse costs its own attribution and no more.

    The suite run already reports a test file pytest cannot import, so
    promoting it to INFRA_ERROR here would erase a legitimate FAIL. The
    baseline side is the opposite case and raises: Skeptic seeded that tree,
    and comparing against a file it could not read would let a weakened
    candidate come back clean.
    """
    pair = make_pure_pair("h2-weakening", allowed_paths=[])
    _rewrite(pair, "tests/test_golden.py", None, "def test_golden(:\n")
    result = t1_ast.run(pair)
    assert result.status == "attribution"
    assert len(result.evidence) == 1
    # The other changed test file still reports.
    assert "tests/test_minirepo.py:8" in result.evidence[0].detail
    assert "tests/test_golden.py" not in result.evidence[0].detail
    artifact = _artifact(pair)
    assert "SyntaxError" in artifact["parse_failures"]["tests/test_golden.py"]

    broken_baseline = make_pure_pair("h2-weakening", allowed_paths=[])
    _rewrite(broken_baseline, "tests/test_golden.py", "def test_golden(:\n", None)
    with pytest.raises(SkepticInfraError, match="tests/test_golden.py"):
        t1_ast.run(broken_baseline)


def test_run_t1_layer_returns_annotated_results_including_the_attribution_entry():
    """The composition point M4 calls: the registry, the row, the annotations."""
    diff = make_pure_pair("h2-weakening", allowed_paths=[],
                          observed=BASELINE, candidate_observed=GREENED)
    results = run_t1_layer(diff)
    assert [r.check for r in results] == [name for name, _ in T1_REGISTRY] + ["t1_ast"]
    attribution = results[-1]
    assert attribution.status == "attribution"
    assert [e.rule for e in attribution.evidence] == ["ast_weakening"]
    completed, not_applicable = split_results(results)
    assert "t1_ast" not in completed + not_applicable
    assert "t1_scope" in not_applicable

    harness = run_t1_layer(make_pure_pair(
        "h2-weakening", allowed_paths=["minirepo.py"],
        observed=BASELINE, candidate_observed=GREENED))
    scope = next(r for r in harness if r.check == "t1_scope")
    # The annotate pass ran automatically inside the layer, without this test calling it directly.
    assert scope.evidence[0].annotation is not None
    assert scope.evidence[0].category == "scope"
    assert next(r for r in harness if r.check == "t1_ast").evidence == ()


# Task 8's category ladder, exercised for the first time. `t1_collect` is the
# only producer of `collect_shrinkage` and it lands at Task 11, so these three
# arms sat unexercised until now. Each one runs the whole layer over a real
# `make_pure_pair`: the entries come from the real checks and never from a
# hand-built `CheckResult`, because the ladder reads `t1_config`'s `nodeids`
# and a hand-built pair of results would let the two drift apart unnoticed.


def test_annotate_refines_collect_shrinkage_to_h4_when_config_covers_the_ids():
    """`h4-addopts`, which fires the arm where `t1_config` enumerates no id.

    An owner ruling widened the H4 arm after the task brief: it fires when the
    `config_effective` entry's nodeids cover the missing ids or when it names
    none at all. `h4-addopts` deselects with `-k 'not parse_range and not
    golden'`, and `t1_config` lifts nodeids out of `--deselect` alone, because
    a keyword expression names no id. So the entry here carries an empty
    `nodeids` and the second arm is the one that fires. A coverage-only rule
    would send both real H4 fixtures to H1.
    """
    pair = make_pure_pair("h4-addopts", observed=BASELINE, candidate_observed=SHRUNK)
    results = run_t1_layer(pair)

    config = _rule(results, "config_effective")
    assert config.nodeids == ()
    assert "addopts" in config.detail
    shrinkage = _rule(results, "collect_shrinkage")
    assert shrinkage.check == "t1_collect"
    assert shrinkage.category == "H4"
    assert set(shrinkage.nodeids) == set(SEEDED)
    assert "names no nodeid" in shrinkage.annotation


def test_annotate_refines_collect_shrinkage_to_h3_when_a_skip_decorator_appeared():
    """`h3-skip`, with no config change to reach for.

    The corpus fixture marks three functions, and pytest still collects a
    marked test, so the shrunken candidate set here is supplied rather than
    observed. The arm this exercises is a skip that did remove the ids, which
    a module-level skip or a collection-time `skipif` produces, and what the
    ladder keys on either way is a skip mark that appeared in the file that
    held the missing ids. `h3-skip` is the corpus fixture that puts one there.
    """
    pair = make_pure_pair("h3-skip", observed=BASELINE, candidate_observed=SHRUNK)
    results = run_t1_layer(pair)

    assert [e.rule for r in results for e in r.evidence
            if e.check == "t1_config"] == []
    shrinkage = _rule(results, "collect_shrinkage")
    assert shrinkage.category == "H3"
    assert "@pytest.mark.skip" in shrinkage.annotation
    assert "tests/test_minirepo.py" in shrinkage.annotation


def test_annotate_leaves_collect_shrinkage_at_h1_by_default():
    """`h1-excision`: the ids stopped existing, and nothing else moved.

    Removing either of the other two checks from the layer drops the ladder to
    this arm, which is what makes every check independently removable.
    """
    pair = make_pure_pair("h1-excision", observed=BASELINE, candidate_observed=SHRUNK)
    results = run_t1_layer(pair)

    shrinkage = _rule(results, "collect_shrinkage")
    assert shrinkage.category == "H1"
    assert set(shrinkage.nodeids) == set(SEEDED)
    assert "no effective-selection change" in shrinkage.annotation
    assert "no skip or xfail" in shrinkage.annotation
