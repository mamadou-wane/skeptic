"""Regressions for candidate execution crossing the host boundary."""
from pathlib import Path

import pytest

from skeptic.builder_tools import ToolContext, dispatch_tool
from skeptic.cli import _run_attempt_acceptance
from skeptic.errors import SkepticInfraError
from skeptic.sandbox import ExecResult, SessionContainer
from skeptic.spec import AcceptanceSuiteSpec
from tests.helpers import make_task_spec


class SeparateFilesystemSession:
    """The container filesystem differs from the host bind-path spelling."""

    def __init__(self, root):
        self.root = root

    def file_operation(self, operation, arguments):
        from skeptic._builder_files import perform
        return perform(self.root, operation, arguments)

    def exec_argv(self, argv, timeout_s, env=None):
        (self.root / ".skeptic-junit-build.xml").write_text(
            '<testsuites><testsuite><testcase file="tests/test_x.py" '
            'classname="tests.test_x" name="test_fix"/></testsuite></testsuites>'
        )
        return ExecResult(0, "1 passed", "", 1)


@pytest.mark.parametrize("operation", ["list_files", "read_file", "edit_file", "run_tests"])
def test_builder_file_and_report_operations_use_container_files(tmp_path, operation):
    host, container = tmp_path / "host", tmp_path / "container"
    host.mkdir()
    container.mkdir()
    (host / "mod.py").write_text("host sentinel")
    (container / "mod.py").write_text("container value")
    (container / "container_only.py").write_text("")
    ctx = ToolContext(
        workspace=host, session=SeparateFilesystemSession(container),
        spec=make_task_spec(allowed_paths=["mod.py"],
                            failing_tests=["tests/test_x.py::test_fix"]),
        baseline_passed=frozenset(), baseline_collection_errors=0,
    )
    arguments = {"path": "mod.py"}
    if operation == "list_files":
        arguments = {"path": ""}
    elif operation == "edit_file":
        arguments.update(old_str="container value", new_str="edited value")
    elif operation == "run_tests":
        arguments = {}
    result = dispatch_tool(ctx, operation, arguments)
    assert not result.refused, result.text
    if operation == "list_files":
        assert "container_only.py" in result.text
    elif operation == "read_file":
        assert result.text == "container value"
    elif operation == "edit_file":
        assert (container / "mod.py").read_text() == "edited value"
    else:
        assert result.green, result.text
    assert (host / "mod.py").read_text() == "host sentinel"


@pytest.mark.parametrize("exit_code", [1, -1])
def test_session_does_not_release_workspace_when_removal_is_unconfirmed(tmp_path, monkeypatch,
                                                                      exit_code):
    session = SessionContainer("image", tmp_path)
    session._container_id = "candidate-still-running"
    monkeypatch.setattr("skeptic.sandbox._run",
                        lambda *a, **k: ExecResult(exit_code, "", "daemon unavailable", 1))
    with pytest.raises(SkepticInfraError, match="confirm"):
        session.stop()
    assert session._container_id == "candidate-still-running"


def test_builder_report_transport_preserves_declared_xml_encoding(tmp_path):
    from skeptic.builder_tools import _report

    (tmp_path / "report.xml").write_bytes(
        b'<?xml version="1.0" encoding="iso-8859-1"?>'
        b'<testsuites><testsuite><testcase file="tests/t.py" '
        b'classname="tests.t" name="test_caf\xe9"/></testsuite></testsuites>'
    )
    result = _report(SeparateFilesystemSession(tmp_path), "report.xml")
    assert result.outcomes == {"tests/t.py::test_caf\u00e9": "passed"}


@pytest.mark.docker
@pytest.mark.slow
@pytest.mark.parametrize("repaired", [True, False])
def test_build_arm_acceptance_never_uses_host_runner(tmp_path, monkeypatch,
                                                    minirepo_spec_and_repo, repaired):
    from skeptic import sandbox, seedcheck

    spec, _ = minirepo_spec_and_repo
    suite = tmp_path / "acceptance"
    suite.mkdir()
    sentinel = tmp_path / "host-only"
    sentinel.write_text("host sentinel")
    (suite / "test_acceptance.py").write_text(
        "from pathlib import Path\nimport minirepo\n"
        "def test_fix():\n"
        "    assert minirepo.parse_range('1-5') == (1, 5)\n"
        f"    assert not Path({str(sentinel)!r}).exists()\n"
    )
    spec = spec.model_copy(update={"acceptance_suite": AcceptanceSuiteSpec(
        path=str(suite), must_pass_on=["pristine", "gold"], must_fail_on=["seeded"])})
    workdir = tmp_path / "runs"
    # Let the old path reach its runner instead of stopping on the prerequisite.
    (workdir / spec.task_id / "venvs" / "seeded").mkdir(parents=True)
    trusted_runner = getattr(sandbox, "VenvRunner", None) or seedcheck._TrustedCorpusVenvRunner

    def refuse_host_setup(*args, **kwargs):
        raise AssertionError("candidate acceptance attempted host execution")

    monkeypatch.setattr(trusted_runner, "setup", refuse_host_setup)
    gold = next(v for v in spec.evaluation.variants if v.id == "gold")
    patch = Path(gold.patch)
    if not repaired:
        patch = tmp_path / "unfixed.diff"
        patch.write_text(Path(gold.patch).read_text().replace(
            "+    return int(lo), int(hi)\n",
            "+    return int(lo), int(hi) - 1  # still broken\n"))
    result = _run_attempt_acceptance(spec, {"candidate": str(patch.resolve())},
                                     workdir, 1)
    assert bool(result.red_set()) is (not repaired)
    assert result.outcomes
    assert sentinel.read_text() == "host sentinel"


@pytest.mark.docker
@pytest.mark.slow
def test_builder_helpers_enforce_container_mounts_and_ignore_python_shadows(
    tmp_path, minirepo_spec_and_repo,
):
    from skeptic.image import ensure_repo_image
    from skeptic.workspace import materialize

    spec, repo = minirepo_spec_and_repo
    pristine = materialize(repo, spec.repo.commit, tmp_path / "pristine")
    image = ensure_repo_image(spec, pristine, tmp_path / "image")
    tree = materialize(repo, spec.repo.commit, tmp_path / "candidate")
    sentinel = tmp_path / "host-sentinel"
    sentinel.write_text("unchanged")
    ro = tuple(spec.environment.test_dirs) + tuple(spec.environment.config_files)
    with SessionContainer(image.image_id, tree, ro_subpaths=ro) as session:
        ctx = ToolContext(tree, session, spec, frozenset(), 0)
        # These entries are created by a running candidate, after installation.
        code = (
            "from pathlib import Path; "
            f"Path('host-link').symlink_to({str(sentinel)!r}); "
            "Path('sitecustomize.py').write_text('raise RuntimeError(\"shadow\")'); "
            "Path('json.py').write_text('raise RuntimeError(\"shadow\")')"
        )
        assert session.exec_argv(["python", "-c", code], 10).exit_code == 0
        read = dispatch_tool(ctx, "read_file", {"path": "minirepo.py"})
        assert not read.refused and "parse_range" in read.text
        escaped = dispatch_tool(ctx, "read_file", {"path": "host-link"})
        assert escaped.refused and "unchanged" not in escaped.text
        edited = dispatch_tool(ctx, "edit_file", {
            "path": "minirepo.py", "old_str": "def parse_range", "new_str": "def parse_range",
        })
        assert not edited.refused
        protected = session.file_operation("edit_file", {
            "path": "tests/test_minirepo.py", "old_str": "def test_parse_range_basic",
            "new_str": "def deleted_test",
            "allowed_paths": ["tests/"],
        })
        assert protected["refused"]
        malformed = session.file_operation("read_file", {"path": "missing"})
        assert malformed["refused"]
    assert sentinel.read_text() == "unchanged"


@pytest.mark.docker
@pytest.mark.slow
def test_holdout_runs_both_candidate_phases_without_host_runner(
    tmp_path, monkeypatch, minirepo_spec_and_repo,
):
    from skeptic import seedcheck
    from tests.test_holdout_scripts import holdout_screen

    spec, _ = minirepo_spec_and_repo
    suite = tmp_path / "acceptance"
    suite.mkdir()
    (suite / "test_acceptance.py").write_text(
        "import minirepo\ndef test_fix():\n"
        "    assert minirepo.parse_range('1-5') == (1, 5)\n"
    )
    spec = spec.model_copy(update={"acceptance_suite": AcceptanceSuiteSpec(
        path=str(suite), must_pass_on=["pristine", "gold"], must_fail_on=["seeded"])})
    monkeypatch.setattr(seedcheck._TrustedCorpusVenvRunner, "setup",
                        lambda *a, **k: pytest.fail("candidate reached host runner"))
    monkeypatch.setitem(holdout_screen.holdout_common.CATEGORY_BY_TASK, spec.task_id, "H5")
    gold = next(v for v in spec.evaluation.variants if v.id == "gold")
    result = holdout_screen.screen_patch(spec, Path(gold.patch), tmp_path / "runs")
    assert (result.verdict, result.condition) == ("REJECTED", "correct-fix")
