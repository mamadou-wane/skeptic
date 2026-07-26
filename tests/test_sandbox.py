import os
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


def test_docker_run_args_use_host_uid_gid(tmp_path):
    args = docker_run_args("img", tmp_path)
    expected = f"{os.getuid()}:{os.getgid()}"
    assert args[args.index("--user") + 1] == expected


def test_docker_run_args_mount_ro_subpaths_over_workspace(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("[tool.x]\n")
    args = docker_run_args("img", tmp_path, ro_subpaths=("tests/", "pyproject.toml"))
    joined = " ".join(args)
    assert f"-v {tmp_path}:/workspace" in joined
    assert f"-v {tmp_path}/tests:/workspace/tests:ro" in joined
    assert f"-v {tmp_path}/pyproject.toml:/workspace/pyproject.toml:ro" in joined
    # ro overlays must come after the rw workspace mount to take precedence
    assert joined.index(":/workspace ") < joined.index(":/workspace/tests:ro")


def test_docker_run_args_set_home_to_workspace(tmp_path):
    joined = " ".join(docker_run_args("img", tmp_path))
    assert "-e HOME=/workspace" in joined


def test_docker_run_args_reject_missing_ro_source(tmp_path):
    (tmp_path / "tests").mkdir()
    with pytest.raises(SkepticInfraError, match="does not exist"):
        docker_run_args("img", tmp_path, ro_subpaths=("tests/", "pyproject.toml"))


from skeptic.sandbox import ExecResult, SessionContainer


def test_session_container_start_args_are_detached_and_hardened(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "skeptic.sandbox._run",
        lambda cmd, cwd, timeout_s, env: (calls.append(cmd),
                                          ExecResult(0, "cid\n", "", 1))[1],
    )
    sc = SessionContainer("img", tmp_path)
    sc.start()
    start_cmd = calls[0]
    joined = " ".join(start_cmd)
    assert "--network none" in joined and "-d" in start_cmd
    assert start_cmd[-2:] == ["sleep", "infinity"]
    # the second call is the offline overlay-venv install
    install = " ".join(calls[1])
    assert calls[1][:2] == ["docker", "exec"]
    assert "--system-site-packages" in install
    assert "--no-index" in install and "--no-build-isolation" in install


def test_session_exec_argv_never_wraps_in_shell(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "skeptic.sandbox._run",
        lambda cmd, cwd, timeout_s, env: (calls.append(cmd), ExecResult(0, "", "", 1))[1],
    )
    sc = SessionContainer("img", tmp_path)
    sc._container_id = "cid"
    sc.exec_argv(["ls", "-la"], timeout_s=5)
    assert calls[0][-2:] == ["ls", "-la"]
    assert "sh" not in calls[0]


@pytest.mark.docker
@pytest.mark.slow
def test_session_container_end_to_end(tmp_path, minirepo_spec_and_repo):
    import os

    from skeptic.image import ensure_repo_image
    from skeptic.workspace import materialize

    spec, repo_dir = minirepo_spec_and_repo
    pristine = tmp_path / "pristine"
    materialize(repo_dir, spec.repo.commit, pristine)
    ref = ensure_repo_image(spec, pristine, tmp_path / "img")
    ws = tmp_path / "ws"
    materialize(repo_dir, spec.repo.commit, ws)
    with SessionContainer(ref.tag, ws, ro_subpaths=tuple(spec.environment.test_dirs)) as sc:
        # host-UID validation: a file created in-container lands host-owned
        touch = sc.exec_shell("touch /workspace/made-inside", timeout_s=10)
        assert touch.exit_code == 0
        assert (ws / "made-inside").stat().st_uid == os.getuid()
        # env passes through docker exec -e
        env = sc.exec_shell("echo $TZ", timeout_s=10)
        assert env.stdout.strip() == "UTC"
        # the suite runs green through the overlay venv
        suite = sc.exec_shell(spec.environment.test_cmd,
                              timeout_s=spec.environment.timeout_s)
        assert suite.exit_code == 0
