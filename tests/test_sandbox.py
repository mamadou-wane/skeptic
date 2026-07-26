import shutil

import pytest

from skeptic.errors import SkepticInfraError, VenvBuildRefused
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


def test_venv_exec_missing_executable_reports_127(venv_runner):
    """Under sh -c, a missing binary is exit 127 with a note on stderr.

    The infra-error contract moved one level up: VenvRunner.setup and
    run_suite convert nonzero exits into SkepticInfraError with the
    stderr tail, so the operator still sees an actionable message.
    """
    result = venv_runner.exec("definitely_not_a_real_binary_zzz --x", timeout_s=5)
    assert result.exit_code == 127
    assert "not found" in result.stderr.lower()


def test_venv_exec_has_shell_semantics(venv_runner):
    result = venv_runner.exec("echo a && echo b | tr 'b' 'c'", timeout_s=5)
    assert result.exit_code == 0
    assert result.stdout.split() == ["a", "c"]


def test_venv_setup_unknown_python_raises_infra_error(tmp_path):
    runner = VenvRunner(workspace=tmp_path, venv_dir=tmp_path / "v")
    with pytest.raises(SkepticInfraError, match="python-does-not-exist-9x9"):
        runner.setup([], python="python-does-not-exist-9x9")


@needs_docker
def test_docker_available_matches_cli():
    assert docker_available() == (shutil.which("docker") is not None)


def test_venv_env_pins_locale_and_leaves_columns_unset(venv_runner):
    """Terminal width must not be pinned; locale and timezone must be.

    A suite that probes terminal-size fallback is testing the unset case, so a
    pinned COLUMNS fails it for a reason unrelated to any seeded bug. Locale and
    timezone are the opposite: they change output with no test opting in.
    """
    result = venv_runner.exec(
        "python -c \"import os;print(repr(os.environ.get('COLUMNS')),"
        "os.environ.get('LANG'),os.environ.get('LC_ALL'),os.environ.get('TZ'))\"",
        timeout_s=60,
    )
    assert result.stdout.strip() == "None C.UTF-8 C.UTF-8 UTC"
