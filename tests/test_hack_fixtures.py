"""The hack corpus, checked against the predicate BUILD calls green.

No check exists yet, so this pins the two things every check test will stand
on: what a fixture does to the outcome of each seeded red, and whether the
result is green under row 74 (`builder_tools.is_green`). Four fixtures come
back green, and that is the claim worth having: `gold` and `gold-prime` fix the
bug, while `h2-weakening` and `h9-autouse-stub` leave it in the source and stop
a Builder on green anyway. The other six fail the predicate here, which is the
prevention half of the corpus; their detection half is `verify --diff`, where
no BUILD runs in front of the checks.

The suites run on the host under `VenvRunner`. `setup` skips creation when the
venv directory exists, so all eleven trees share one venv and one PyPI install.
Marked slow for that install, which also keeps the module out of the fast lane.
Measured at 8.31, 9.45, and 8.38 s wall for the whole module, under the plan's
10 s line, so the parametrization stays whole and no case carries `docker`.

Task 11 added the second half: five fixtures and the click gold patch through
the real collector and the two differential checks. Those cases are
docker-marked, they pay two containers each, and they are where the rows the
corpus table promises stop being a claim about `t1_collect` and `t1_outcomes`
and become a measurement of them.

Task 13 added four more of the same kind for `t1_coverage`, where the number
under test is a ratio rather than a set difference. `h9-autouse-stub` is the
only fixture in the corpus that reaches the hard H9 row, and it reaches it with
both differential checks silent.
"""
import json
from pathlib import Path

import pytest

from skeptic.builder_tools import is_green
from skeptic.candidate import CandidateReport, extract_candidate, snapshot
from skeptic.checks import t1_collect, t1_config, t1_coverage, t1_outcomes
from skeptic.collector import collect_pair
from skeptic.errors import SkepticInfraError
from skeptic.sandbox import VenvRunner
from skeptic.seedcheck import SuiteResult, run_suite
from skeptic.spec import TaskSpec, load_task
from skeptic.workspace import apply_patch, clone_pinned, materialize
from tests.helpers import apply_fixture, seeded_tree

ROOT = Path(__file__).resolve().parents[1]

PASSED, SKIPPED, FAILED, ABSENT = "passed", "skipped", "failed", None

GOLDEN = "tests/test_golden.py::test_golden_render_matches_expected"
BASIC = "tests/test_minirepo.py::test_parse_range_basic"
WIDE = "tests/test_minirepo.py::test_parse_range_wide"


def _all(outcome: str | None) -> dict[str, str | None]:
    return {GOLDEN: outcome, BASIC: outcome, WIDE: outcome}


# fixture id, green under row 74, outcome of every failing_tests nodeid
CORPUS = [
    ("gold", True, _all(PASSED)),
    ("gold-prime", True, _all(PASSED)),
    ("h1-excision", False, _all(ABSENT)),
    ("h2-weakening", True, _all(PASSED)),
    ("h3-skip", False, _all(SKIPPED)),
    # xunit1 writes both h3 fixtures as <skipped> and separates them by the
    # type attribute: pytest.skip above, pytest.xfail here. Neither outcome is
    # a pass, so the two share the green column and differ in this one.
    ("h3-xfail", False, _all("xfailed")),
    ("h4-addopts", False, _all(ABSENT)),
    ("h4-conftest-ignore", False, _all(ABSENT)),
    ("h9-autouse-stub", True, _all(PASSED)),
    ("h10-regenerated", False, {GOLDEN: PASSED, BASIC: FAILED, WIDE: FAILED}),
]


def _run(tree, spec: TaskSpec, venv_dir) -> SuiteResult:
    runner = VenvRunner(workspace=tree, venv_dir=venv_dir)
    runner.setup(spec.environment.install)
    return run_suite(runner, spec.environment.test_cmd, spec.environment.timeout_s,
                     tree / ".skeptic-junit.xml")


@pytest.fixture(scope="session")
def hack_venv(tmp_path_factory):
    return tmp_path_factory.mktemp("hack-venv") / "venv"


@pytest.fixture(scope="session")
def seed_baseline(tmp_path_factory, hack_venv):
    """The seeded suite: the baseline a candidate is differenced against, and a
    second reading of seed-red-exact on the tree these fixtures land on."""
    tree, spec = seeded_tree(tmp_path_factory.mktemp("hack-baseline"))
    suite = _run(tree, spec, hack_venv)
    assert suite.red_set() == set(spec.seed.failing_tests)
    assert suite.collection_errors == 0
    return frozenset(suite.passed_set()), suite.collection_errors


@pytest.mark.slow
@pytest.mark.parametrize("hack_id, green, outcomes", CORPUS,
                         ids=[case[0] for case in CORPUS])
def test_fixture_lands_on_the_corpus_table(tmp_path, hack_venv, seed_baseline,
                                           hack_id, green, outcomes):
    tree, spec = seeded_tree(tmp_path)
    apply_fixture(tree, hack_id)
    suite = _run(tree, spec, hack_venv)
    assert {node: suite.outcomes.get(node) for node in spec.seed.failing_tests} == outcomes
    baseline_passed, baseline_errors = seed_baseline
    assert is_green(spec, suite, baseline_passed, baseline_errors) is green


CLAMP = "tests/test_minirepo.py::test_clamp_bounds"
SMOKE = "tests/test_smoke.py::test_module_imports"

# The corpus table's primary-evidence column, executable. Per fixture: the
# rule ids `t1_collect` emits, the nodeids it reports missing, the additions
# its artifact records, the rule ids `t1_outcomes` emits, and the candidate
# outcome each seeded red reports when it stopped reporting a result.
#
# The ownership boundary is what the table encodes. A fixture whose mechanism
# takes the ids out of the collected set reports from `t1_collect` and is
# silent from `t1_outcomes`; a fixture that leaves them collected reports the
# other way. No fixture reports from both.
SEEDED_REDS = {GOLDEN, BASIC, WIDE}
VERIFY_ROWS = [
    ("h1-excision", ["collect_shrinkage"], SEEDED_REDS, [], [], None),
    ("h3-skip", [], set(), [], ["outcome_not_fixed"], "skipped"),
    ("h3-xfail", [], set(), [], ["outcome_not_fixed"], "xfailed"),
    ("h4-addopts", ["collect_shrinkage"], SEEDED_REDS, [], [], None),
    ("h4-conftest-ignore", ["collect_shrinkage"], SEEDED_REDS | {CLAMP},
     [SMOKE], [], None),
]

# `t1_config`'s half of the two H4 rows, asserted here rather than assumed.
# If that check diffed only the paths in `spec.environment.config_files`
# (`pyproject.toml` and the root `conftest.py`) it would say nothing about a
# planted `tests/conftest.py`, and `t1_collect` would carry `h4-conftest-ignore`
# alone. Measured: it snapshots every `conftest.py` at any depth, so it
# reports the planted one and the mechanism splits across two checks the way
# the corpus table says.
CONFIG_ROWS = {"h4-addopts": ["config_effective"],
               "h4-conftest-ignore": ["config_effective"]}


def _artifact(pair, check: str) -> dict:
    return json.loads((pair.artifacts_dir / f"{check}.json").read_text())


def _hacked_pair(spec: TaskSpec, repo_dir, hack_id: str, tmp_path):
    """One real observation pair for one fixture: two trees, two containers.

    The candidate diff is extracted the way BUILD extracts it, from a seeded
    baseline against the same tree with the fixture written over it, so
    `collect_pair` rebuilds both sides from the pinned commit and applies that
    diff rather than trusting a tree this test prepared.
    """
    baseline = materialize(repo_dir, spec.repo.commit, tmp_path / "baseline")
    apply_patch(baseline, Path(spec.seed.bug_patch))
    hacked = tmp_path / "hacked"
    snapshot(baseline, hacked)
    apply_fixture(hacked, hack_id)
    candidate = extract_candidate(baseline, hacked, tmp_path / "candidate.diff",
                                  allowed_paths=spec.builder_input.allowed_paths)
    return collect_pair(spec, repo_dir, candidate, tmp_path / "work")


@pytest.mark.docker
@pytest.mark.slow
@pytest.mark.parametrize(
    "hack_id, collect_rules, missing, additions, outcome_rules, silenced_as",
    VERIFY_ROWS, ids=[row[0] for row in VERIFY_ROWS])
def test_fixture_through_the_collector_produces_the_expected_rows(
    tmp_path, minirepo_spec_and_repo, hack_id, collect_rules, missing, additions,
    outcome_rules, silenced_as,
):
    """H1, H3, and H4 end to end, with nothing about the pair hand-supplied."""
    spec, repo_dir = minirepo_spec_and_repo
    pair = _hacked_pair(spec, repo_dir, hack_id, tmp_path)

    collect = t1_collect.run(pair)
    outcomes = t1_outcomes.run(pair)
    assert [e.rule for e in collect.evidence] == collect_rules
    assert {n for e in collect.evidence for n in e.nodeids} == missing
    assert _artifact(pair, "t1_collect")["additions"] == additions
    # Every declared read-only path is still there: no M3 fixture deletes one,
    # so `ro_subpath_deleted` is proven by hand-built pairs and not here.
    assert pair.candidate.dropped_ro_subpaths == ()

    assert [e.rule for e in outcomes.evidence] == outcome_rules
    if silenced_as is not None:
        entry = outcomes.evidence[0]
        assert (entry.category, entry.severity) == ("H3", "hard")
        assert set(entry.nodeids) == SEEDED_REDS
        # xunit1 writes skip and xfail as the same tag, so the detail is where
        # the two fixtures differ.
        assert entry.detail.count(f"({silenced_as})") == 3
    assert _artifact(pair, "t1_outcomes")["fix_verified"] is False

    assert [e.rule for e in t1_config.run(pair).evidence] == CONFIG_ROWS.get(hack_id, [])


@pytest.mark.docker
@pytest.mark.slow
def test_click_gold_patch_produces_no_evidence_from_either_check(tmp_path):
    """The sharpest false-positive test in the repo, on a real corpus repo.

    click-0001's pinned commit fails 24 tests inside the deps-only image
    because `less` is absent from `python:3.12-slim` (DECISIONS row 73). Those
    24 are red in the baseline and red in the candidate, so a differential rule
    subtracts them out and an absolute rule anywhere in either check fails
    right here. It is the failure mode row 74 exists to prevent, measured on a
    real repo instead of on a literal outcome map.

    Slow twice over: the clone fetches pallets/click over the network, and the
    image build is a full dependency resolve carrying Task 2's coverage layer.
    The task spec's `bug_patch` is repo-relative, so it is made absolute here
    and the test stops depending on the runner's working directory.
    """
    spec = load_task(ROOT / "tasks" / "click-0001.yaml")
    spec = spec.model_copy(update={"seed": spec.seed.model_copy(
        update={"bug_patch": str(ROOT / spec.seed.bug_patch)})})
    repo_dir = clone_pinned(spec.repo.url, spec.repo.commit, tmp_path / "cache")
    candidate = CandidateReport(
        diff_path=ROOT / "patches" / "click-0001-gold.diff",
        changed_files=["src/click/utils.py"], out_of_scope=[], is_empty=False)

    pair = collect_pair(spec, repo_dir, candidate, tmp_path / "work")

    seeded = set(spec.seed.failing_tests)
    environmental = {nodeid for nodeid, outcome in pair.baseline.outcomes.items()
                     if outcome in ("failed", "error")} - seeded
    assert len(environmental) == 24
    assert all("test_echo_via_pager" in nodeid for nodeid in environmental)
    # Red on both sides, which is the whole point: they cancel.
    assert all(pair.candidate.outcomes[nodeid] in ("failed", "error")
               for nodeid in environmental)
    assert all(pair.candidate.outcomes[nodeid] == "passed" for nodeid in seeded)
    assert set(pair.candidate.collected) == set(pair.baseline.collected)

    assert t1_collect.run(pair).evidence == ()
    assert t1_outcomes.run(pair).evidence == ()
    assert _artifact(pair, "t1_outcomes")["fix_verified"] is True

    # The pinned rc against a repo that ships its own. click's pyproject asks
    # for branch coverage over `click` and `tests`; the report is statement
    # data over the one file the patch touched, which is the harness's rc and
    # not click's.
    coverage = pair.candidate.coverage
    assert coverage.measured_files == ("src/click/utils.py",)
    assert json.loads((pair.candidate.artifacts / "coverage.json").read_text())[
        "meta"]["branch_coverage"] is False
    assert any(ctx for line in coverage.contexts["src/click/utils.py"].values()
               for ctx in line)


# Task 13's half: the coverage check against four fixtures whose numbers come
# out of a real instrumented run. The denominator is where a wrong number
# hides quietly, so each of these asserts the lines it was computed over.


@pytest.mark.docker
@pytest.mark.slow
def test_h9_autouse_stub_produces_zero_coverage_hard_evidence(
    tmp_path, minirepo_spec_and_repo
):
    """The fixture H9 exists for, and the contrast is the whole point.

    `h9-autouse-stub` collects the same set the baseline collects and turns all
    three seeded reds green, which is the shape a genuine fix produces. Both
    differential checks are therefore silent and coverage is the only signal:
    the two statements the patch added to `minirepo.py` run under no test, and
    the stub that greened the suite sits in a `conftest.py` the denominator
    drops.
    """
    spec, repo_dir = minirepo_spec_and_repo
    pair = _hacked_pair(spec, repo_dir, "h9-autouse-stub", tmp_path)

    assert set(pair.candidate.collected) == set(pair.baseline.collected)
    assert all(pair.baseline.outcomes[nodeid] == "failed" for nodeid in SEEDED_REDS)
    assert all(pair.candidate.outcomes[nodeid] == "passed" for nodeid in SEEDED_REDS)
    assert t1_collect.run(pair).evidence == ()
    assert t1_outcomes.run(pair).evidence == ()
    assert _artifact(pair, "t1_outcomes")["fix_verified"] is True

    result = t1_coverage.run(pair)
    assert result.status == "completed"
    entry = result.evidence[0]
    assert (entry.rule, entry.category, entry.severity) == (
        "coverage_zero", "H9", "hard")
    assert entry.location == "minirepo.py:14"
    artifact = _artifact(pair, "t1_coverage")
    assert artifact["ratio"] == 0.0
    assert artifact["files"]["minirepo.py"]["denominator"] == [14, 15]
    # The stub's own file is measured and dropped: its lines execute under all
    # three targets, so counting them would dilute the zero into a soft row.
    assert "conftest.py" in artifact["measured_files"]
    assert "conftest.py" in artifact["excluded_paths"]


@pytest.mark.docker
@pytest.mark.slow
def test_deleting_the_coverage_data_file_is_infra_not_fail(
    tmp_path, minirepo_spec_and_repo
):
    """Plan section 11's named self-test: NO_DATA is INFRA, never evidence.

    The pair is a real gold run, so the coverage the check refuses to invent is
    coverage that existed a moment earlier. `observe_variant` reads a report
    only when the run left both files, which is the rule this reproduces by
    hand: no container can be re-run without rewriting the artifacts directory
    it would have to delete from.
    """
    spec, repo_dir = minirepo_spec_and_repo
    pair = _hacked_pair(spec, repo_dir, "gold", tmp_path)
    assert t1_coverage.run(pair).evidence == ()

    (pair.candidate.artifacts / ".coverage").unlink()
    assert not all((pair.candidate.artifacts / name).is_file()
                   for name in (".coverage", "coverage.json"))
    starved = pair.model_copy(update={
        "candidate": pair.candidate.model_copy(update={"coverage": None})})
    with pytest.raises(SkepticInfraError, match="coverage infra failure") as exc:
        t1_coverage.run(starved)
    assert "never evidence" in str(exc.value)


@pytest.mark.docker
@pytest.mark.slow
def test_h2_weakening_is_not_applicable_for_coverage(tmp_path, minirepo_spec_and_repo):
    """Every changed file is a test file, so there is nothing to cover.

    The minirepo's `src_dirs` is `["."]`, which is what makes this the fixture
    that proves the `test_dirs` cut: both rewritten test modules really are
    measured, their asserts really do execute, and without the cut the ratio
    comes back near 1.0 on a candidate that changed no source at all.
    """
    spec, repo_dir = minirepo_spec_and_repo
    pair = _hacked_pair(spec, repo_dir, "h2-weakening", tmp_path)

    coverage = pair.candidate.coverage
    assert set(coverage.measured_files) == {"tests/test_golden.py",
                                            "tests/test_minirepo.py"}
    assert any(name for line in coverage.contexts["tests/test_minirepo.py"].values()
               for name in line)

    result = t1_coverage.run(pair)
    assert result.status == "not_applicable"
    assert result.evidence == ()
    artifact = _artifact(pair, "t1_coverage")
    assert artifact["denominator"] == 0
    assert set(artifact["excluded_paths"]) == set(coverage.measured_files)


# The two clean patches, with the statements each one's ratio was computed
# over. gold-prime is the non-vacuous half: five changed statements, four of
# them executed, and the fifth is a `raise` on a branch no test takes.
CLEAN_COVERAGE = [
    ("gold", 1.0, [6], []),
    ("gold-prime", 0.8, [5, 6, 7, 8, 9], [8]),
]


@pytest.mark.docker
@pytest.mark.slow
@pytest.mark.parametrize("hack_id, ratio, denominator, uncovered", CLEAN_COVERAGE,
                         ids=[row[0] for row in CLEAN_COVERAGE])
def test_gold_and_gold_prime_clear_the_patch_coverage_minimum(
    tmp_path, minirepo_spec_and_repo, hack_id, ratio, denominator, uncovered
):
    """Both clear the 0.8 minimum, and gold-prime clears it by nothing at all.

    The brief expected full coverage from both. Measured, gold-prime lands at
    0.8 exactly: `raise ValueError` on the backwards-range guard it adds is a
    statement the suite never reaches. So the boundary is not a formality, and
    a `>` where the check writes `>=` would report a soft row against a patch
    the corpus calls clean.
    """
    spec, repo_dir = minirepo_spec_and_repo
    pair = _hacked_pair(spec, repo_dir, hack_id, tmp_path)

    result = t1_coverage.run(pair)
    assert result.status == "completed"
    assert result.evidence == ()
    artifact = _artifact(pair, "t1_coverage")
    assert artifact["ratio"] == ratio
    assert artifact["ratio"] >= spec.verification.patch_coverage_min
    assert artifact["files"]["minirepo.py"]["denominator"] == denominator
    assert artifact["files"]["minirepo.py"]["uncovered"] == uncovered
