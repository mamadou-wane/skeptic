import shutil

import pytest

from skeptic.errors import VenvBuildRefused
from skeptic.sandbox import (
    VenvRunner,
    docker_available,
    docker_run_args,
)

needs_docker = pytest.mark.docker


def test_docker_run_args_include_hardening_flags(tmp_path):
    args = docker_run_args("skeptic-task:abc", tmp_path)
    joined = " ".join(args)
    assert "--network none" in joined
    assert "--pids-limit 256" in joined
    assert "--security-opt no-new-privileges" in joined
    assert args[0:2] == ["docker", "run"]


@pytest.fixture(scope="module")
def venv_runner(tmp_path_factory):
    ws = tmp_path_factory.mktemp("ws")
    (ws / "hello.py").write_text("print('hi from workspace')\n")
    runner = VenvRunner(workspace=ws, venv_dir=tmp_path_factory.mktemp("venv") / "v")
    runner.setup(install_cmds=[])
    return runner


def test_venv_exec_runs_in_workspace(venv_runner):
    result = venv_runner.exec("python hello.py", timeout_s=60)
    assert result.exit_code == 0
    assert "hi from workspace" in result.stdout
    assert result.dur_ms >= 0


def test_venv_exec_nonzero_exit_captured(venv_runner):
    result = venv_runner.exec("python -c 'import sys; sys.exit(4)'", timeout_s=60)
    assert result.exit_code == 4


def test_venv_exec_timeout_is_reported_not_raised(venv_runner):
    result = venv_runner.exec("python -c 'import time; time.sleep(30)'", timeout_s=1)
    assert result.exit_code == -1
    assert "timed out" in result.stderr.lower()


def test_venv_runner_refuses_build_stage(venv_runner):
    with pytest.raises(VenvBuildRefused):
        venv_runner.build_stage_guard()
    assert venv_runner.isolation == "venv-reduced-isolation"


@needs_docker
def test_docker_available_matches_cli():
    assert docker_available() == (shutil.which("docker") is not None)
