from pathlib import Path

import pytest

from skeptic.errors import SkepticInfraError
from skeptic.sandbox import VenvRunner
from skeptic.seedcheck import SuiteResult, check_task, parse_junit
from skeptic.spec import find_task
from tests.helpers import make_minirepo_task

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


def test_parse_junit_raises_on_class_based_nodeid_collision(tmp_path):
    # Two testcases share file + name but differ only by classname; the
    # reconstructed file::name id collides, and M1 does not distinguish
    # classes, so this must fail loud rather than silently overwrite.
    p = tmp_path / "r.xml"
    p.write_text(XUNIT1_CLASS_COLLISION)
    with pytest.raises(SkepticInfraError, match=r"tests/test_a\.py::test_x"):
        parse_junit(p)


def test_suite_result_equality_ignores_nothing():
    a = SuiteResult(outcomes={"t::x": "passed"}, collection_errors=0)
    b = SuiteResult(outcomes={"t::x": "passed"}, collection_errors=0)
    c = SuiteResult(outcomes={"t::x": "failed"}, collection_errors=0)
    assert a.outcome_map_equal(b)
    assert not a.outcome_map_equal(c)


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
