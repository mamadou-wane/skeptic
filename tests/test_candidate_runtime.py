"""Suite capture failure contracts, with Docker transport replaced at its boundary."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from skeptic import candidate_runtime as runtime
from skeptic.errors import SkepticInfraError
from skeptic.sandbox import ExecResult
from tests.helpers import make_task_spec

GOOD = (b'<testsuites><testsuite><testcase file="tests/test_x.py" '
        b'classname="tests.test_x" name="test_fix"/></testsuite></testsuites>')


@pytest.fixture
def capture(tmp_path, monkeypatch):
    def materialize(repo, commit, destination):
        destination.mkdir()
        return destination

    monkeypatch.setattr(runtime, "materialize", materialize)
    monkeypatch.setattr(runtime, "apply_patch", lambda *args: None)
    monkeypatch.setattr(runtime, "apply_candidate", lambda *a, **k: None)
    monkeypatch.setattr(runtime, "ensure_repo_image", lambda *args: SimpleNamespace(
        image_id="sha256:fixed", tag="mutable:tag"))
    spec = make_task_spec(quarantine=["tests/test_x.py::test_fix"])
    spec = spec.model_copy(update={"environment": spec.environment.model_copy(
        update={"test_dirs": [], "config_files": [], "golden_dirs": []})})

    def run(code=0, junit=GOOD):
        def transport(container, script, timeout_s, quarantine, env=None, deadline=None):
            assert container.image == "sha256:fixed"
            assert container.workspace.is_dir()
            quarantine.mkdir()
            if isinstance(junit, Path):
                (quarantine / "junit.xml").symlink_to(junit)
            elif junit is not None:
                (quarantine / "junit.xml").write_bytes(junit)
            return ExecResult(code, "output", "diagnostic", 1)
        monkeypatch.setattr(runtime.RunContainer, "run_capture", transport)
        return runtime.run_candidate_suite(spec, Path("repo"), Path("patch"), tmp_path / "run")
    return run, tmp_path


def test_candidate_runtime_admits_results_preserves_quarantine_and_removes_tree(capture):
    run, root = capture
    observed = run()
    assert observed.suite.outcomes == {}
    assert observed.image_id == "sha256:fixed"
    assert (observed.artifacts / "junit.xml").read_bytes() == GOOD
    assert not list((root / "run").glob("execution-*"))


@pytest.mark.parametrize("code", [-1, 2, 3, 4, 5, 125])
def test_candidate_runtime_refuses_non_suite_exit_and_keeps_diagnostics(capture, code):
    run, root = capture
    with pytest.raises(SkepticInfraError, match="Candidate suite exited"):
        run(code, None)
    assert next((root / "run").glob("observed-*/suite.err")).read_text() == "diagnostic"
    assert not list((root / "run").glob("execution-*"))


@pytest.mark.parametrize("junit", [None, b"not XML"])
def test_candidate_runtime_refuses_missing_or_malformed_junit(capture, junit):
    run, _ = capture
    with pytest.raises(SkepticInfraError):
        run(0, junit)


def test_candidate_runtime_does_not_replace_earlier_observation(capture):
    run, _ = capture
    first = run()
    second = run()
    assert first.artifacts != second.artifacts
    assert (first.artifacts / "junit.xml").read_bytes() == GOOD


def test_candidate_runtime_rejects_host_runner_injection(tmp_path):
    with pytest.raises(TypeError):
        runtime.run_candidate_suite(make_task_spec(), Path("repo"), Path("patch"), tmp_path,
                                    runner_factory=lambda _: None)


def test_candidate_runtime_reports_missing_docker_as_infra(capture, monkeypatch):
    run, _ = capture

    def missing(*args):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(runtime, "ensure_repo_image", missing)
    with pytest.raises(SkepticInfraError, match="Docker"):
        run()


def test_candidate_runtime_refuses_symlinked_report(capture):
    run, root = capture
    outside = root / "outside.xml"
    outside.write_bytes(GOOD)
    with pytest.raises(SkepticInfraError, match="symbolic link"):
        run(0, outside)
    assert outside.read_bytes() == GOOD


def test_trusted_admission_owns_its_runner(tmp_path):
    from skeptic.seedcheck import check_trusted_task
    with pytest.raises(TypeError):
        check_trusted_task(make_task_spec(), tmp_path, tmp_path, tmp_path,
                           runner_factory=lambda _: None)
