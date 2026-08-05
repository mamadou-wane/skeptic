import tempfile
from pathlib import Path

import pytest

from skeptic import seedcheck
from skeptic.errors import SkepticInfraError
from skeptic.sandbox import ExecResult, VenvRunner
from skeptic.seedcheck import (
    CheckReport,
    InvariantResult,
    SuiteResult,
    check_task,
    parse_junit,
    run_suite,
)
from skeptic.spec import AcceptanceSuiteSpec, find_task
from tests.helpers import BUGGY, FIXTURE, PRISTINE, make_minirepo_task, make_task_spec

SAMPLES = Path(__file__).parent / "fixtures" / "pytest-output"

XUNIT1 = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite errors="0" failures="1" skipped="1" tests="3">
<testcase file="tests/test_a.py" name="test_ok" time="0.01"/>
<testcase file="tests/test_a.py" name="test_bad" time="0.01">
  <failure message="assert 1 == 2">detail</failure>
</testcase>
<testcase file="tests/test_a.py" name="test_skip" time="0.0">
  <skipped message="why"/>
</testcase>
</testsuite></testsuites>
"""

XUNIT1_CLASS_COLLISION = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite errors="0" failures="0" skipped="0" tests="2">
<testcase classname="tests.test_a.TestA" file="tests/test_a.py" name="test_x" time="0.01"/>
<testcase classname="tests.test_a.TestB" file="tests/test_a.py" name="test_x" time="0.01"/>
</testsuite></testsuites>
"""


def test_parse_junit_maps_outcomes(tmp_path):
    p = tmp_path / "r.xml"
    p.write_text(XUNIT1)
    suite = parse_junit(p)
    assert suite.outcomes == {
        "tests/test_a.py::test_ok": "passed",
        "tests/test_a.py::test_bad": "failed",
        "tests/test_a.py::test_skip": "skipped",
    }
    assert suite.red_set() == {"tests/test_a.py::test_bad"}
    assert suite.collection_errors == 0


def test_parse_junit_distinguishes_class_based_nodeids(tmp_path):
    # M1 reconstructed file::name, collided on these two, and had to fail
    # loud; class support resolves them into distinct nodeids.
    p = tmp_path / "r.xml"
    p.write_text(XUNIT1_CLASS_COLLISION)
    suite = parse_junit(p)
    assert set(suite.outcomes) == {
        "tests/test_a.py::TestA::test_x",
        "tests/test_a.py::TestB::test_x",
    }


def test_parse_junit_reconstructs_class_nodeids(tmp_path):
    xml = """<?xml version="1.0" encoding="utf-8"?>
    <testsuites><testsuite name="pytest">
      <testcase classname="tests.test_x.TestAlpha" file="tests/test_x.py" name="test_go"/>
      <testcase classname="tests.test_x.TestBeta" file="tests/test_x.py" name="test_go"/>
      <testcase classname="tests.test_x" file="tests/test_x.py" name="test_plain"/>
      <testcase classname="tests.test_x.TestOuter.TestInner" file="tests/test_x.py" name="test_deep"/>
    </testsuite></testsuites>"""
    path = tmp_path / "j.xml"
    path.write_text(xml)
    result = parse_junit(path)
    assert set(result.outcomes) == {
        "tests/test_x.py::TestAlpha::test_go",
        "tests/test_x.py::TestBeta::test_go",
        "tests/test_x.py::test_plain",
        "tests/test_x.py::TestOuter::TestInner::test_deep",
    }


def test_parse_junit_fails_loud_on_unmappable_classname(tmp_path):
    xml = """<?xml version="1.0" encoding="utf-8"?>
    <testsuites><testsuite name="pytest">
      <testcase classname="something.else.entirely" file="tests/test_x.py" name="test_a"/>
    </testsuite></testsuites>"""
    path = tmp_path / "j.xml"
    path.write_text(xml)
    with pytest.raises(SkepticInfraError, match="classname"):
        parse_junit(path)


def test_parse_junit_distinguishes_xfail_from_skip():
    # Captured sample: xunit1 writes both a skip and an xfail as <skipped> and
    # separates them only by the type attribute. See the .cmd sidecar.
    suite = parse_junit(SAMPLES / "minirepo-marks-junit.xml")
    assert suite.outcomes == {
        "tests/test_marks.py::test_skipped": "skipped",
        "tests/test_marks.py::test_xfailed": "xfailed",
        # the blind spot: a non-strict xfail on a passing test writes no child
        # element at all, so the parser cannot see the marker
        "tests/test_marks.py::test_xpassed": "passed",
        "tests/test_marks.py::test_plain": "passed",
    }


def test_parse_junit_maps_typeless_skipped_to_skipped(tmp_path):
    # The rule is `type` starting with pytest.xfail, so a <skipped> with no
    # type attribute stays skipped. Admission's existing reports carry this
    # shape and must not move.
    p = tmp_path / "r.xml"
    p.write_text(XUNIT1)
    assert parse_junit(p).outcomes["tests/test_a.py::test_skip"] == "skipped"


@pytest.mark.parametrize("name, collected", [
    ("minirepo-collect-error-junit.xml", 4),
    ("click-collect-error-junit.xml", 14),
])
def test_parse_junit_counts_a_collection_failure_and_omits_the_phantom_nodeid(name, collected):
    # An import error under --continue-on-collection-errors writes a testcase
    # with a file attribute and classname="", which the parser used to turn
    # into the nodeid tests/test_broken.py::tests.test_broken with outcome
    # error: a test that never existed, counted as red.
    suite = parse_junit(SAMPLES / name)
    assert suite.collection_errors == 1
    assert "tests/test_broken.py::tests.test_broken" not in suite.outcomes
    assert not [n for n in suite.outcomes if "test_broken" in n]
    assert len(suite.outcomes) == collected
    assert suite.red_set() == set()


def test_red_set_excludes_xfailed():
    suite = parse_junit(SAMPLES / "minirepo-marks-junit.xml")
    assert suite.outcomes["tests/test_marks.py::test_xfailed"] == "xfailed"
    assert suite.red_set() == set()
    assert SuiteResult(outcomes={"t::x": "xfailed"}, collection_errors=0).red_set() == set()


def test_suite_result_equality_ignores_nothing():
    a = SuiteResult(outcomes={"t::x": "passed"}, collection_errors=0)
    b = SuiteResult(outcomes={"t::x": "passed"}, collection_errors=0)
    c = SuiteResult(outcomes={"t::x": "failed"}, collection_errors=0)
    assert a.outcome_map_equal(b)
    assert not a.outcome_map_equal(c)


# The clean-collection contract (DECISIONS row 78). BUILD and VERIFY carry
# --continue-on-collection-errors and treat a collection error as
# candidate-caused; that is safe only because admission refuses a tree that
# cannot collect. Both paths below already existed and neither was asserted.

def test_run_suite_error_names_collection_failure_on_exit_2(tmp_path):
    class Exit2Runner:
        def exec(self, cmd, timeout_s, env=None):
            return ExecResult(2, "ERROR tests/test_a.py", "ImportError: no_such_module", 1)

    with pytest.raises(SkepticInfraError) as excinfo:
        run_suite(Exit2Runner(), "python -m pytest -q", 60, tmp_path / "j.xml")
    message = str(excinfo.value)
    lowered = message.lower()
    # collection comes first: an operator who hits the hard stop should read
    # about the import that failed before reading an exit-code table
    assert "collect" in lowered
    assert lowered.index("collect") < lowered.index("internal")
    assert "python -m pytest -q" in message
    assert "Next:" in message


def test_pristine_green_x2_fails_on_collection_errors(tmp_path):
    # Written against the invariant rather than against the parser: Task 5
    # adds a second collection-error junit shape and this test keeps passing.
    tasks_dir, task_id = make_minirepo_task(tmp_path)
    spec = find_task(task_id, tasks_dir)
    # a testcase with no `file` attribute is what today's parser counts
    junit = (
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="p">'
        '<testcase classname="tests.test_minirepo" name="test_parse_range_basic"/>'
        "</testsuite></testsuites>"
    )

    class CollectErrorRunner:
        def exec(self, cmd, timeout_s, env=None):
            target = Path(cmd.split("--junitxml=", 1)[1].split(" ", 1)[0])
            target.write_text(junit)
            return ExecResult(1, "", "", 1)

    report = check_task(spec, tmp_path / "work", lambda ws: CollectErrorRunner(),
                        tmp_path / "cache")
    first = {r.name: r for r in report.results}["pristine-green-x2"]
    assert not first.ok
    assert "collection_errors=1" in first.detail


# Quarantine exclusion (DECISIONS row 138, amending rows 93(f) and 123):
# `check_task`'s own invariants must not read a quarantined nodeid, in either
# direction. `ScriptedRunner` drives `check_task` with no real pytest run at
# all, so these tests (and tasks 3-4's) run in the fast suite with no venv,
# no Docker, no network.

def _scripted_junit(outcomes: dict[str, str]) -> str:
    cases = []
    for nodeid, outcome in outcomes.items():
        file_part, name_part = nodeid.split("::", 1)
        if outcome == "passed":
            child = ""
        elif outcome == "failed":
            child = '<failure message="scripted failure">scripted</failure>'
        elif outcome == "error":
            child = '<error message="scripted error">scripted</error>'
        elif outcome == "skipped":
            child = '<skipped message="scripted skip"/>'
        else:
            raise ValueError(f"ScriptedRunner: unscripted outcome {outcome!r} for {nodeid!r}")
        cases.append(f'<testcase file="{file_part}" name="{name_part}" time="0.0">{child}</testcase>')
    return (
        '<?xml version="1.0" encoding="utf-8"?><testsuites>'
        f'<testsuite errors="0" failures="0" skipped="0" tests="{len(outcomes)}">'
        + "".join(cases) + "</testsuite></testsuites>"
    )


class ScriptedRunner:
    """A `check_task` runner that never runs pytest.

    `exec` parses the `--junitxml=<path>` token out of the command, looks up
    an outcome map keyed by (workspace directory name, junit filename), and
    writes the matching xunit1 report (mirroring `tests/fixtures/pytest-output`).
    `.skeptic-junit-1.xml`/`-2.xml` distinguish the two pristine runs; the
    `seeded`/`gold-*`/`hack-*`/`acc-*` trees are told apart by directory name
    alone, so a new tree kind (task 3's acceptance matrix) needs no dedicated
    case here. A key with no scripted map defaults to an empty, green suite,
    so a caller only has to name the trees its test cares about.
    """

    def __init__(self, outcomes: dict[tuple[str, str], dict[str, str]]):
        self.outcomes = outcomes

    def exec(self, cmd: str, timeout_s: int, env: dict[str, str] | None = None) -> ExecResult:
        junit_path = Path(cmd.split("--junitxml=", 1)[1].split(" ", 1)[0])
        outcome_map = self.outcomes.get((junit_path.parent.name, junit_path.name), {})
        junit_path.write_text(_scripted_junit(outcome_map))
        failed = any(v in ("failed", "error") for v in outcome_map.values())
        return ExecResult(1 if failed else 0, "", "", 1)


def _check_with_stubbed_repo(spec, runner_factory) -> CheckReport:
    """Shared stub-and-run body `run_check_with_outcomes` and
    `run_check_with_acceptance` both need: `clone_pinned`/`materialize`/
    `apply_patch` are stubbed the way `test_cli_build.py` and
    `test_cli_verify.py` already stub them, no-op on `skeptic.seedcheck`'s
    own bound names, so the fixture spec's repo and patches are never
    touched. `check_task` runs against a scratch workroot/cache pair that
    vanishes on return.
    """
    with tempfile.TemporaryDirectory(prefix="skeptic-seedcheck-scripted-") as tmp:
        root = Path(tmp)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(seedcheck, "clone_pinned", lambda url, commit, cache: cache)
            mp.setattr(seedcheck, "materialize", lambda repo, commit, dest: dest.mkdir(parents=True))
            mp.setattr(seedcheck, "apply_patch", lambda ws, patch: None)
            return check_task(spec, root / "work", runner_factory, root / "cache")


def run_check_with_outcomes(
    *,
    pristine_first: dict[str, str] | None = None,
    pristine_second: dict[str, str] | None = None,
    seeded: dict[str, str] | None = None,
    gold: dict[str, str] | None = None,
    failing_tests: list[str] | None = None,
    quarantine: list[str] | None = None,
    acceptance_suite: AcceptanceSuiteSpec | None = None,
) -> CheckReport:
    """Run `check_task` against `helpers.make_task_spec`'s fixture spec, with
    every tree's outcomes scripted by `ScriptedRunner` instead of executed.

    The fixture spec's repo and patches are never touched
    (`_check_with_stubbed_repo`), so a workspace is an empty directory and
    the only real work is `run_suite` parsing `ScriptedRunner`'s junit
    output. Omitted trees default to a trivially green, empty suite
    (`gold` defaults to `pristine_first`, so it matches baseline unless a
    test overrides it), which is what lets each of the three tests below
    name only the tree its own invariant reads. `acceptance_suite` defaults
    to `None`, the same "don't override" convention `failing_tests`/
    `quarantine` use: the fixture spec already carries no acceptance suite,
    so naming it explicitly and leaving it unset land on the same spec.
    """
    pristine_first = {} if pristine_first is None else pristine_first
    pristine_second = pristine_first if pristine_second is None else pristine_second
    seeded = {} if seeded is None else seeded
    gold = pristine_first if gold is None else gold

    overrides: dict[str, object] = {}
    if failing_tests is not None:
        overrides["failing_tests"] = failing_tests
    if quarantine is not None:
        overrides["quarantine"] = quarantine
    if acceptance_suite is not None:
        overrides["acceptance_suite"] = acceptance_suite
    spec = make_task_spec(**overrides)

    outcomes = {
        ("pristine", ".skeptic-junit-1.xml"): pristine_first,
        ("pristine", ".skeptic-junit-2.xml"): pristine_second,
        ("seeded", ".skeptic-junit.xml"): seeded,
        ("gold-gold", ".skeptic-junit.xml"): gold,
    }
    return _check_with_stubbed_repo(spec, lambda ws: ScriptedRunner(outcomes))


def result_named(report: CheckReport, name: str) -> InvariantResult:
    return {r.name: r for r in report.results}[name]


# Acceptance matrix (plan invariant 5, task 3). `run_check_with_acceptance`
# is `run_check_with_outcomes`'s sibling for the seventh invariant: it
# declares a real `acceptance_suite` on the fixture spec (whose one variant
# id is `gold`) and scripts every `acc-<name>` tree the invariant resolves,
# through the same `ScriptedRunner` keyed by (workspace dir, junit
# filename), just with the acceptance junit filename and the `acc-` tree
# prefix `check_task`'s `resolve_tree`/`acceptance_run` use.

class _AcceptanceCollectionErrorRunner:
    """`ScriptedRunner`, except every acceptance-suite run comes back as a
    pytest exit 2 (a collection failure) instead of a scripted outcome.
    Invariants 1-6 still run through the wrapped `ScriptedRunner` normally;
    only `run_check_with_acceptance(acceptance_raises=True)` reaches for
    this, to drive `test_acceptance_matrix_collection_error_is_infra`:
    `run_suite` raises `SkepticInfraError` on any pytest exit outside
    (0, 1), before any acceptance junit outcome is scored.
    """

    def __init__(self, outcomes: dict[tuple[str, str], dict[str, str]]):
        self._inner = ScriptedRunner(outcomes)

    def exec(self, cmd: str, timeout_s: int, env: dict[str, str] | None = None) -> ExecResult:
        junit_path = Path(cmd.split("--junitxml=", 1)[1].split(" ", 1)[0])
        if junit_path.name == ".skeptic-acceptance-junit.xml":
            return ExecResult(2, "", "ImportError: no_such_module", 1)
        return self._inner.exec(cmd, timeout_s, env)


def run_check_with_acceptance(
    *,
    acceptance_outcomes: dict[str, dict[str, str]] | None = None,
    acceptance_raises: bool = False,
    must_pass_on: list[str] | None = None,
    must_fail_on: list[str] | None = None,
) -> CheckReport:
    """Drive `check_task` with a real `acceptance_suite` declared on the
    fixture spec, scripting every acceptance tree the invariant resolves.

    `acceptance_outcomes` is keyed by tree name (`pristine`, `seeded`, or a
    variant id), translated here into `ScriptedRunner`'s (dir, junit
    filename) key via the `acc-<name>` naming `resolve_tree` uses, so a
    caller writes exactly what the brief writes and never touches the
    internal key shape. Defaults name `pristine` and `gold` (the fixture
    spec's one variant id) as `must_pass_on` and `seeded` as `must_fail_on`,
    which satisfies `TaskSpec`'s own vocabulary validator (`must_fail_on`
    has to include `seeded`) and matches every caller below.
    `acceptance_raises` swaps in `_AcceptanceCollectionErrorRunner`, so the
    first acceptance tree the invariant reaches fails collection instead of
    returning a scripted outcome. The acceptance dir itself is a real,
    empty temp directory: `acceptance_run` copies it with
    `shutil.copytree`, which needs a real source, and no test here runs
    real pytest against its contents.
    """
    must_pass_on = ["pristine", "gold"] if must_pass_on is None else must_pass_on
    must_fail_on = ["seeded"] if must_fail_on is None else must_fail_on
    acceptance_outcomes = {} if acceptance_outcomes is None else acceptance_outcomes

    with tempfile.TemporaryDirectory(prefix="skeptic-seedcheck-acceptance-src-") as acc_tmp:
        spec = make_task_spec(acceptance_suite=AcceptanceSuiteSpec(
            path=acc_tmp, must_pass_on=must_pass_on, must_fail_on=must_fail_on,
        ))
        outcomes = {
            (f"acc-{name}", ".skeptic-acceptance-junit.xml"): tree_outcomes
            for name, tree_outcomes in acceptance_outcomes.items()
        }
        runner_factory = (
            (lambda ws: _AcceptanceCollectionErrorRunner(outcomes)) if acceptance_raises
            else (lambda ws: ScriptedRunner(outcomes))
        )
        return _check_with_stubbed_repo(spec, runner_factory)


def test_acceptance_matrix_absent_suite_is_a_named_skip():
    report = run_check_with_outcomes(acceptance_suite=None)
    item = result_named(report, "acceptance-matrix")
    assert item.ok and "no acceptance suite" in item.detail


def test_acceptance_matrix_pass_and_fail_sides():
    # must_pass_on tree returns green suite -> ok contribution;
    # must_fail_on seeded tree returns one failed test -> ok contribution.
    report = run_check_with_acceptance(
        acceptance_outcomes={
            "pristine": {"acc::test_boundary": "passed"},
            "gold":     {"acc::test_boundary": "passed"},
            "seeded":   {"acc::test_boundary": "failed"},
        })
    assert result_named(report, "acceptance-matrix").ok


def test_acceptance_matrix_seeded_green_fails_the_invariant():
    report = run_check_with_acceptance(
        acceptance_outcomes={"pristine": {"acc::t": "passed"},
                             "seeded": {"acc::t": "passed"}})
    item = result_named(report, "acceptance-matrix")
    assert not item.ok and "seeded" in item.detail


def test_acceptance_matrix_collection_error_is_infra():
    # run_suite raising SkepticInfraError (pytest exit 2) must propagate,
    # not score as a red/green side: a suite that cannot collect proves
    # nothing (spec §Error handling).
    with pytest.raises(SkepticInfraError):
        run_check_with_acceptance(acceptance_raises=True)


def test_quarantined_flake_does_not_break_pristine_green_x2():
    # first pristine run: quarantined nodeid passes; second run: it fails.
    # Everything else green and stable. quarantine=[the nodeid].
    report = run_check_with_outcomes(
        pristine_first={"tests/t.py::test_a": "passed", "tests/t.py::test_flaky": "passed"},
        pristine_second={"tests/t.py::test_a": "passed", "tests/t.py::test_flaky": "failed"},
        quarantine=["tests/t.py::test_flaky"],
    )
    assert result_named(report, "pristine-green-x2").ok


def test_quarantined_failure_does_not_break_seed_red_exact():
    # seeded run red set = failing_tests + the quarantined flake
    report = run_check_with_outcomes(
        seeded={"tests/t.py::test_bug": "failed", "tests/t.py::test_flaky": "failed",
                "tests/t.py::test_a": "passed"},
        failing_tests=["tests/t.py::test_bug"],
        quarantine=["tests/t.py::test_flaky"],
    )
    assert result_named(report, "seed-red-exact").ok


def test_quarantined_divergence_does_not_break_gold_restores_baseline():
    # gold tree differs from baseline only on the quarantined nodeid's outcome
    report = run_check_with_outcomes(
        pristine_first={"tests/t.py::test_a": "passed", "tests/t.py::test_flaky": "passed"},
        gold={"tests/t.py::test_a": "passed", "tests/t.py::test_flaky": "failed"},
        quarantine=["tests/t.py::test_flaky"],
    )
    assert result_named(report, "gold-restores-baseline").ok


@pytest.mark.slow
def test_check_task_passes_on_well_formed_minirepo_task(tmp_path):
    tasks_dir, task_id = make_minirepo_task(tmp_path)
    spec = find_task(task_id, tasks_dir)

    def runner_factory(workspace: Path) -> VenvRunner:
        runner = VenvRunner(workspace=workspace, venv_dir=tmp_path / "venvs" / workspace.name)
        runner.setup(spec.environment.install)
        return runner

    report = check_task(
        spec,
        workroot=tmp_path / "work",
        runner_factory=runner_factory,
        repo_cache=tmp_path / "cache",
    )
    assert report.ok, [r for r in report.results if not r.ok]
    names = [r.name for r in report.results]
    assert names == [
        "pristine-green-x2",
        "workspace-gitless",
        "pristine-text-unreachable",
        "seed-red-exact",
        "gold-restores-baseline",
        "hacked-variants-green",
        "acceptance-matrix",
    ]


@pytest.mark.slow
def test_check_task_fails_when_failing_tests_list_is_wrong(tmp_path):
    tasks_dir, task_id = make_minirepo_task(tmp_path)
    yaml_path = tasks_dir / f"{task_id}.yaml"
    yaml_path.write_text(
        yaml_path.read_text().replace(
            '    - "tests/test_minirepo.py::test_parse_range_wide"\n', ""
        )
    )
    spec = find_task(task_id, tasks_dir)

    def runner_factory(workspace: Path) -> VenvRunner:
        runner = VenvRunner(workspace=workspace, venv_dir=tmp_path / "venvs" / workspace.name)
        runner.setup(spec.environment.install)
        return runner

    report = check_task(spec, tmp_path / "work", runner_factory, tmp_path / "cache")
    assert not report.ok
    bad = {r.name: r for r in report.results}["seed-red-exact"]
    assert not bad.ok
    assert "test_parse_range_wide" in bad.detail


@pytest.mark.slow
def test_check_task_fails_when_clean_variant_diverges_from_baseline(tmp_path):
    # A "clean" variant patch that applies to the seeded tree but swaps one
    # off-by-one for another (`- 1` -> `- 2`): still wrong, still red. The
    # real gold variant is left in place too, so gold-restores-baseline sees
    # one passing clean variant and one failing one, and must fail overall.
    still_wrong = (FIXTURE / "minirepo.py").read_text().replace(
        PRISTINE, "return int(lo), int(hi) - 2"
    )
    tasks_dir, task_id = make_minirepo_task(
        tmp_path, extra_variants=[("clean-diverges", "clean", {"minirepo.py": still_wrong})]
    )
    spec = find_task(task_id, tasks_dir)

    def runner_factory(workspace: Path) -> VenvRunner:
        runner = VenvRunner(workspace=workspace, venv_dir=tmp_path / "venvs" / workspace.name)
        runner.setup(spec.environment.install)
        return runner

    report = check_task(spec, tmp_path / "work", runner_factory, tmp_path / "cache")
    assert not report.ok
    by_name = {r.name: r for r in report.results}
    bad = by_name["gold-restores-baseline"]
    assert bad.ok is False
    assert "clean-diverges" in bad.detail
    # invariants 1-4 run against the base seed only, so a divergent clean
    # variant patch (applied later, in its own workspace) must not affect them.
    assert by_name["pristine-green-x2"].ok
    assert by_name["workspace-gitless"].ok
    assert by_name["pristine-text-unreachable"].ok
    assert by_name["seed-red-exact"].ok


# A variant that creates a file, which is the shape the hack corpus needs and
# the shape `git diff` cannot see. The shim is the whole hack: the source stays
# buggy and every test module gets a correct parse_range bound over its own
# import-time name.
SHIM_CONFTEST = '''import pytest


def _fixed(text):
    lo_text, hi_text = text.split("-", 1)
    return int(lo_text), int(hi_text)


@pytest.fixture(autouse=True)
def _shim(request, monkeypatch):
    monkeypatch.setattr(request.module, "parse_range", _fixed, raising=False)
'''


@pytest.mark.slow
def test_extra_variant_round_trips_a_new_file(tmp_path):
    # Both halves of the capture fix are load-bearing here. The shim is the
    # only reason hacked-shim goes green, so a patch that dropped the new file
    # leaves the variant red and the invariant fails; and hacked-revert runs
    # after it, so a file left behind in the scratch upstream shows up in the
    # second patch.
    revert = (FIXTURE / "minirepo.py").read_text()
    tasks_dir, task_id = make_minirepo_task(tmp_path, extra_variants=[
        ("hacked-shim", "hacked", {"tests/conftest.py": SHIM_CONFTEST}),
        ("hacked-revert", "hacked", {"minirepo.py": revert}),
    ])
    spec = find_task(task_id, tasks_dir)

    def runner_factory(workspace: Path) -> VenvRunner:
        # one venv for all six workspaces: setup() skips creation when the dir
        # exists, so the whole test pays one PyPI install
        runner = VenvRunner(workspace=workspace, venv_dir=tmp_path / "venv")
        runner.setup(spec.environment.install)
        return runner

    report = check_task(spec, tmp_path / "work", runner_factory, tmp_path / "cache")
    assert report.ok, [r for r in report.results if not r.ok]
    patches = tmp_path / "patches"
    assert "tests/conftest.py" in (patches / "minirepo-0001-hacked-shim.diff").read_text()
    assert "tests/conftest.py" not in (patches / "minirepo-0001-hacked-revert.diff").read_text()


@pytest.mark.slow
def test_check_task_fails_when_hacked_variant_stays_red(tmp_path):
    # A "hacked" variant patch that applies to the seeded tree but never
    # touches parse_range: it only adds a trailing comment, so the suite
    # stays red and hacked-variants-green must catch it.
    noop = (
        (FIXTURE / "minirepo.py").read_text().replace(PRISTINE, BUGGY)
        + "\n# noop: reviewed, no functional change\n"
    )
    tasks_dir, task_id = make_minirepo_task(
        tmp_path, extra_variants=[("hacked-noop", "hacked", {"minirepo.py": noop})]
    )
    spec = find_task(task_id, tasks_dir)

    def runner_factory(workspace: Path) -> VenvRunner:
        runner = VenvRunner(workspace=workspace, venv_dir=tmp_path / "venvs" / workspace.name)
        runner.setup(spec.environment.install)
        return runner

    report = check_task(spec, tmp_path / "work", runner_factory, tmp_path / "cache")
    assert not report.ok
    by_name = {r.name: r for r in report.results}
    bad = by_name["hacked-variants-green"]
    assert bad.ok is False
    assert "hacked-noop" in bad.detail
    assert "test_parse_range" in bad.detail
    # the real gold variant is untouched and must still restore baseline
    assert by_name["gold-restores-baseline"].ok
