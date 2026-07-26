from pathlib import Path

import pytest

from skeptic.errors import SkepticInfraError
from skeptic.sandbox import ExecResult, VenvRunner
from skeptic.seedcheck import SuiteResult, check_task, parse_junit, run_suite
from skeptic.spec import find_task
from tests.helpers import BUGGY, FIXTURE, PRISTINE, make_minirepo_task

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
        tmp_path, extra_variants=[("clean-diverges", "clean", still_wrong)]
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
        tmp_path, extra_variants=[("hacked-noop", "hacked", noop)]
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
