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
"""
import pytest

from skeptic.builder_tools import is_green
from skeptic.sandbox import VenvRunner
from skeptic.seedcheck import SuiteResult, run_suite
from skeptic.spec import TaskSpec
from tests.helpers import apply_fixture, seeded_tree

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
