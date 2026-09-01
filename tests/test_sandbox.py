import os
import shutil

import pytest

from skeptic import sandbox as sandbox_module
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


def test_docker_run_args_normalize_ro_subpath_for_mounting(tmp_path):
    (tmp_path / "tests").mkdir()

    args = docker_run_args("img", tmp_path, ro_subpaths=("./tests//",))

    assert f"{tmp_path}/tests:/workspace/tests:ro" in args


def test_docker_run_args_refuse_ro_subpath_escape_at_final_boundary(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "tests").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SkepticInfraError, match="outside the workspace"):
        docker_run_args("img", workspace, ro_subpaths=("tests",))


def test_docker_run_args_set_home_to_workspace(tmp_path):
    joined = " ".join(docker_run_args("img", tmp_path))
    assert "-e HOME=/workspace" in joined


def test_docker_run_args_reject_missing_ro_source(tmp_path):
    (tmp_path / "tests").mkdir()
    with pytest.raises(SkepticInfraError, match="does not exist"):
        docker_run_args("img", tmp_path, ro_subpaths=("tests/", "pyproject.toml"))


from skeptic.sandbox import INSTALL_FAILURE_EXIT, ExecResult, SessionContainer


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


from skeptic.sandbox import RunContainer, base_env, overlay_install_cmd


def _record_run(monkeypatch, stdout=""):
    """Capture every argv `_run` is handed and answer a clean ExecResult."""
    calls = []
    monkeypatch.setattr(
        "skeptic.sandbox._run",
        lambda cmd, cwd, timeout_s, env: (calls.append(cmd),
                                          ExecResult(0, stdout, "", 1))[1],
    )
    return calls


class _FixedUuid:
    hex = "0123456789abcdef"


class _FixedUuidFactory:
    @staticmethod
    def uuid4():
        return _FixedUuid()


def _fixed_capture_name(monkeypatch):
    monkeypatch.setattr("skeptic.sandbox.os.getpid", lambda: 4242)
    monkeypatch.setattr(sandbox_module, "uuid", _FixedUuidFactory, raising=False)
    return "skeptic-4242-0123456789abcdef"


def test_run_capture_refuses_fractional_deadline_before_docker_run(
    tmp_path, monkeypatch
):
    """A sub-second remainder cannot be rounded into a new one-second run."""
    workspace, quarantine = tmp_path / "workspace", tmp_path / "quarantine"
    workspace.mkdir()
    calls = _record_run(monkeypatch)
    monkeypatch.setattr("skeptic.sandbox.time.monotonic", lambda: 9.5)

    with pytest.raises(SkepticInfraError, match="whole second"):
        RunContainer("img", workspace).run_capture(
            "echo unreachable", 60, quarantine,
            deadline=sandbox_module.HostDeadline(expires_at=10.0),
        )

    assert calls == []


def test_run_capture_does_not_start_copy_after_deadline(tmp_path, monkeypatch):
    """A completed primary run cannot start evidence copy after expiry."""
    workspace, quarantine = tmp_path / "workspace", tmp_path / "quarantine"
    workspace.mkdir()
    _fixed_capture_name(monkeypatch)
    calls = []

    def fake_run(cmd, cwd, timeout_s, env):
        calls.append((cmd, timeout_s))
        return ExecResult(0, "", "", 1)

    readings = iter((1.0, 2.0, 10.0))
    monkeypatch.setattr("skeptic.sandbox.time.monotonic", lambda: next(readings))
    monkeypatch.setattr("skeptic.sandbox._run", fake_run)

    with pytest.raises(SkepticInfraError, match="deadline"):
        RunContainer("img", workspace).run_capture(
            "echo ok", 60, quarantine,
            deadline=sandbox_module.HostDeadline(expires_at=10.0),
        )

    assert [(cmd[:2], timeout_s) for cmd, timeout_s in calls] == [
        (["docker", "volume"], 9),
        (["docker", "run"], 8),
        (["docker", "rm"], 30),
        (["docker", "volume"], 30),
    ]
    assert not any(cmd[:2] == ["docker", "create"] for cmd, _ in calls)
    assert not any(cmd[:2] == ["docker", "cp"] for cmd, _ in calls)


def test_run_capture_does_not_start_timeout_stop_after_deadline(
    tmp_path, monkeypatch
):
    """A primary timeout at expiry cannot start a separately budgeted stop."""
    workspace, quarantine = tmp_path / "workspace", tmp_path / "quarantine"
    workspace.mkdir()
    _fixed_capture_name(monkeypatch)
    calls = []

    def fake_run(cmd, cwd, timeout_s, env):
        calls.append((cmd, timeout_s))
        if cmd[:2] == ["docker", "run"]:
            return ExecResult(-1, "partial", "timed out", 5000)
        return ExecResult(0, "", "", 1)

    readings = iter((1.0, 2.0, 10.0))
    monkeypatch.setattr("skeptic.sandbox.time.monotonic", lambda: next(readings))
    monkeypatch.setattr("skeptic.sandbox._run", fake_run)

    with pytest.raises(SkepticInfraError, match="deadline"):
        RunContainer("img", workspace).run_capture(
            "sleep 30", 60, quarantine,
            deadline=sandbox_module.HostDeadline(expires_at=10.0),
        )

    assert [(cmd[:2], timeout_s) for cmd, timeout_s in calls] == [
        (["docker", "volume"], 9),
        (["docker", "run"], 8),
        (["docker", "rm"], 30),
        (["docker", "volume"], 30),
    ]
    assert not any(cmd[:2] == ["docker", "stop"] for cmd, _ in calls)
    assert not any(cmd[:2] == ["docker", "create"] for cmd, _ in calls)


def test_run_capture_refuses_admission_when_cleanup_crosses_deadline(
    tmp_path, monkeypatch
):
    """Post-copy cleanup may continue, but cannot return evidence after expiry."""
    workspace, quarantine = tmp_path / "workspace", tmp_path / "quarantine"
    workspace.mkdir()
    _fixed_capture_name(monkeypatch)
    calls = []

    def fake_run(cmd, cwd, timeout_s, env):
        calls.append((cmd, timeout_s))
        return ExecResult(0, "", "", 1)

    readings = iter((1.0, 2.0, 3.0, 4.0, 10.2))
    monkeypatch.setattr("skeptic.sandbox.time.monotonic", lambda: next(readings))
    monkeypatch.setattr("skeptic.sandbox._run", fake_run)

    with pytest.raises(SkepticInfraError, match="deadline"):
        RunContainer("img", workspace).run_capture(
            "echo ok", 60, quarantine,
            deadline=sandbox_module.HostDeadline(expires_at=10.0),
        )

    assert [(cmd[:2], timeout_s) for cmd, timeout_s in calls] == [
        (["docker", "volume"], 9),
        (["docker", "run"], 8),
        (["docker", "create"], 7),
        (["docker", "cp"], 6),
        (["docker", "rm"], 30),
        (["docker", "rm"], 30),
        (["docker", "volume"], 30),
    ]


def test_run_capture_bounds_timeout_stop_and_copy_by_one_deadline(
    tmp_path, monkeypatch
):
    workspace, quarantine = tmp_path / "workspace", tmp_path / "quarantine"
    workspace.mkdir()
    _fixed_capture_name(monkeypatch)
    calls = []

    def fake_run(cmd, cwd, timeout_s, env):
        calls.append((cmd, timeout_s))
        if cmd[:2] == ["docker", "run"]:
            return ExecResult(-1, "partial", "timed out", 7000)
        return ExecResult(0, "", "", 1)

    readings = iter((1.0, 2.0, 3.0, 4.0, 5.0, 6.0))
    monkeypatch.setattr("skeptic.sandbox.time.monotonic", lambda: next(readings))
    monkeypatch.setattr("skeptic.sandbox._run", fake_run)

    result = RunContainer("img", workspace).run_capture(
        "sleep 30", 7, quarantine,
        deadline=sandbox_module.HostDeadline(expires_at=10.0),
    )

    assert result.exit_code == -1
    assert [(cmd[:2], timeout_s) for cmd, timeout_s in calls] == [
        (["docker", "volume"], 9),
        (["docker", "run"], 7),
        (["docker", "stop"], 7),
        (["docker", "create"], 6),
        (["docker", "cp"], 5),
        (["docker", "rm"], 30),
        (["docker", "rm"], 30),
        (["docker", "volume"], 30),
    ]


def test_run_capture_never_mounts_quarantine(tmp_path, monkeypatch):
    workspace, quarantine = tmp_path / "workspace", tmp_path / "quarantine"
    workspace.mkdir()
    name = _fixed_capture_name(monkeypatch)
    token = name.removeprefix("skeptic-")
    volume = f"skeptic-artifacts-{token}"
    helper = f"skeptic-copy-{token}"
    calls = []

    def fake_run(cmd, cwd, timeout_s, env):
        calls.append(cmd)
        return ExecResult(0, "", "", 1)

    monkeypatch.setattr("skeptic.sandbox._run", fake_run)
    RunContainer("img", workspace).run_capture("echo ok", 60, quarantine)

    assert [cmd[:2] for cmd in calls] == [
        ["docker", "volume"],
        ["docker", "run"],
        ["docker", "create"],
        ["docker", "cp"],
        ["docker", "rm"],
        ["docker", "rm"],
        ["docker", "volume"],
    ]
    run = calls[1]
    assert str(quarantine) not in "\n".join(run)
    assert "--rm" not in run
    assert "--user" not in run
    assert run[run.index("--name") + 1] == name
    assert calls[0] == ["docker", "volume", "create", volume]
    assert calls[2] == [
        "docker", "create", "--name", helper,
        "--mount", f"type=volume,src={volume},dst=/tmp/skeptic-artifacts,readonly",
        "img", "true",
    ]
    assert calls[3] == [
        "docker", "cp", f"{helper}:/tmp/skeptic-artifacts/.", str(quarantine),
    ]
    script = run[-1]
    assert script.index("mkdir -p /tmp/skeptic-artifacts") < script.index(
        overlay_install_cmd("/tmp/sv")
    )
    initializer = run[-3]
    assert f"chown {os.getuid()}:{os.getgid()} /tmp/skeptic-artifacts" in initializer
    assert f"setpriv --reuid {os.getuid()} --regid {os.getgid()}" in initializer
    assert "--clear-groups" in initializer
    assert f"{overlay_install_cmd('/tmp/sv')} || exit {INSTALL_FAILURE_EXIT}" in script
    assert "install.ok" not in script


def test_run_capture_copies_from_fresh_volume_through_never_started_helper(
    tmp_path, monkeypatch
):
    """Private output crosses no host bind and copy-out sees no workspace mounts."""
    workspace, quarantine = tmp_path / "workspace", tmp_path / "quarantine"
    workspace.mkdir()
    candidate = _fixed_capture_name(monkeypatch)
    token = candidate.removeprefix("skeptic-")
    volume = f"skeptic-artifacts-{token}"
    helper = f"skeptic-copy-{token}"
    calls = _record_run(monkeypatch)

    RunContainer("img", workspace).run_capture("echo ok", 60, quarantine)

    assert [cmd[:2] for cmd in calls] == [
        ["docker", "volume"],
        ["docker", "run"],
        ["docker", "create"],
        ["docker", "cp"],
        ["docker", "rm"],
        ["docker", "rm"],
        ["docker", "volume"],
    ]
    run = calls[1]
    assert ["--mount", f"type=volume,src={volume},dst=/tmp/skeptic-artifacts"] == run[
        run.index("--mount"):run.index("--mount") + 2
    ]
    assert str(quarantine) not in "\n".join(run)

    assert calls[2] == [
        "docker", "create", "--name", helper,
        "--mount", f"type=volume,src={volume},dst=/tmp/skeptic-artifacts,readonly",
        "img", "true",
    ]
    assert not any(cmd[:2] == ["docker", "start"] for cmd in calls)
    assert not any("/workspace" in arg for arg in calls[2])
    assert calls[3] == [
        "docker", "cp", f"{helper}:/tmp/skeptic-artifacts/.", str(quarantine),
    ]
    assert calls[-3:] == [
        ["docker", "rm", "-f", helper],
        ["docker", "rm", "-f", candidate],
        ["docker", "volume", "rm", "-f", volume],
    ]


def test_run_capture_stops_timeout_before_copy(tmp_path, monkeypatch):
    workspace, quarantine = tmp_path / "workspace", tmp_path / "quarantine"
    workspace.mkdir()
    name = _fixed_capture_name(monkeypatch)
    token = name.removeprefix("skeptic-")
    volume = f"skeptic-artifacts-{token}"
    helper = f"skeptic-copy-{token}"
    calls = []

    def fake_run(cmd, cwd, timeout_s, env):
        calls.append(cmd)
        if cmd[:2] == ["docker", "run"]:
            return ExecResult(-1, "partial", "command timed out after 7s", 7000)
        return ExecResult(0, "", "", 1)

    monkeypatch.setattr("skeptic.sandbox._run", fake_run)
    result = RunContainer("img", workspace).run_capture("sleep 30", 7, quarantine)

    assert [cmd[:2] for cmd in calls] == [
        ["docker", "volume"],
        ["docker", "run"],
        ["docker", "stop"],
        ["docker", "create"],
        ["docker", "cp"],
        ["docker", "rm"],
        ["docker", "rm"],
        ["docker", "volume"],
    ]
    assert calls[0] == ["docker", "volume", "create", volume]
    assert calls[1][:2] == ["docker", "run"]
    assert calls[2] == ["docker", "stop", name]
    assert calls[3] == [
        "docker", "create", "--name", helper,
        "--mount", f"type=volume,src={volume},dst=/tmp/skeptic-artifacts,readonly",
        "img", "true",
    ]
    assert calls[4] == [
        "docker", "cp", f"{helper}:/tmp/skeptic-artifacts/.", str(quarantine),
    ]
    assert calls[-3:] == [
        ["docker", "rm", "-f", helper],
        ["docker", "rm", "-f", name],
        ["docker", "volume", "rm", "-f", volume],
    ]
    assert result == ExecResult(-1, "partial", "command timed out after 7s", 7000)


@pytest.mark.parametrize("stop_exit", [1, -1])
def test_run_capture_refuses_copy_when_timeout_stop_is_unconfirmed(
    tmp_path, monkeypatch, stop_exit
):
    workspace, quarantine = tmp_path / "workspace", tmp_path / "quarantine"
    workspace.mkdir()
    name = _fixed_capture_name(monkeypatch)
    token = name.removeprefix("skeptic-")
    volume = f"skeptic-artifacts-{token}"
    calls = []

    def fake_run(cmd, cwd, timeout_s, env):
        calls.append(cmd)
        if cmd[:2] == ["docker", "run"]:
            return ExecResult(-1, "partial", "command timed out after 7s", 7000)
        if cmd[:2] == ["docker", "stop"]:
            return ExecResult(stop_exit, "", "stop not confirmed", 30000)
        return ExecResult(0, "", "", 1)

    monkeypatch.setattr("skeptic.sandbox._run", fake_run)

    with pytest.raises(SkepticInfraError, match="could not be confirmed stopped"):
        RunContainer("img", workspace).run_capture("sleep 30", 7, quarantine)

    assert [cmd[:2] for cmd in calls] == [
        ["docker", "volume"],
        ["docker", "run"],
        ["docker", "stop"],
        ["docker", "rm"],
        ["docker", "volume"],
    ]
    assert calls[1][:2] == ["docker", "run"]
    assert calls[2] == ["docker", "stop", name]
    assert calls[3] == ["docker", "rm", "-f", name]
    assert calls[4] == ["docker", "volume", "rm", "-f", volume]
    assert not any(cmd[:2] == ["docker", "create"] for cmd in calls)
    assert not any(cmd[:2] == ["docker", "cp"] for cmd in calls)


def test_run_capture_removes_container_when_copy_raises(tmp_path, monkeypatch):
    workspace, quarantine = tmp_path / "workspace", tmp_path / "quarantine"
    workspace.mkdir()
    name = _fixed_capture_name(monkeypatch)
    token = name.removeprefix("skeptic-")
    volume = f"skeptic-artifacts-{token}"
    helper = f"skeptic-copy-{token}"
    calls = []

    def fake_run(cmd, cwd, timeout_s, env):
        calls.append(cmd)
        if cmd[:2] == ["docker", "cp"]:
            raise RuntimeError("copy broke")
        return ExecResult(0, "", "", 1)

    monkeypatch.setattr("skeptic.sandbox._run", fake_run)
    with pytest.raises(RuntimeError, match="copy broke"):
        RunContainer("img", workspace).run_capture("echo ok", 60, quarantine)

    assert [cmd[:2] for cmd in calls] == [
        ["docker", "volume"],
        ["docker", "run"],
        ["docker", "create"],
        ["docker", "cp"],
        ["docker", "rm"],
        ["docker", "rm"],
        ["docker", "volume"],
    ]
    assert calls[-3:] == [
        ["docker", "rm", "-f", helper],
        ["docker", "rm", "-f", name],
        ["docker", "volume", "rm", "-f", volume],
    ]


def test_run_capture_cleans_failed_volume_creation(tmp_path, monkeypatch):
    workspace, quarantine = tmp_path / "workspace", tmp_path / "quarantine"
    workspace.mkdir()
    name = _fixed_capture_name(monkeypatch)
    token = name.removeprefix("skeptic-")
    volume = f"skeptic-artifacts-{token}"
    calls = []

    def fake_run(cmd, cwd, timeout_s, env):
        calls.append(cmd)
        if cmd[:3] == ["docker", "volume", "create"]:
            return ExecResult(1, "", "volume refused", 1)
        return ExecResult(0, "", "", 1)

    monkeypatch.setattr("skeptic.sandbox._run", fake_run)
    with pytest.raises(SkepticInfraError, match="artifact volume"):
        RunContainer("img", workspace).run_capture("echo ok", 60, quarantine)

    assert calls == [
        ["docker", "volume", "create", volume],
        ["docker", "volume", "rm", "-f", volume],
    ]


def test_run_capture_cleans_failed_copy_helper_without_copying(tmp_path, monkeypatch):
    workspace, quarantine = tmp_path / "workspace", tmp_path / "quarantine"
    workspace.mkdir()
    name = _fixed_capture_name(monkeypatch)
    token = name.removeprefix("skeptic-")
    volume = f"skeptic-artifacts-{token}"
    helper = f"skeptic-copy-{token}"
    calls = []

    def fake_run(cmd, cwd, timeout_s, env):
        calls.append(cmd)
        if cmd[:2] == ["docker", "create"]:
            return ExecResult(1, "", "helper refused", 1)
        return ExecResult(0, "", "", 1)

    monkeypatch.setattr("skeptic.sandbox._run", fake_run)
    with pytest.raises(SkepticInfraError, match="copy helper"):
        RunContainer("img", workspace).run_capture("echo ok", 60, quarantine)

    assert [cmd[:2] for cmd in calls] == [
        ["docker", "volume"],
        ["docker", "run"],
        ["docker", "create"],
        ["docker", "rm"],
        ["docker", "rm"],
        ["docker", "volume"],
    ]
    assert not any(cmd[:2] == ["docker", "cp"] for cmd in calls)
    assert calls[-3:] == [
        ["docker", "rm", "-f", helper],
        ["docker", "rm", "-f", name],
        ["docker", "volume", "rm", "-f", volume],
    ]


def test_run_capture_refuses_existing_quarantine(tmp_path, monkeypatch):
    workspace, quarantine = tmp_path / "workspace", tmp_path / "quarantine"
    workspace.mkdir()
    quarantine.mkdir()
    calls = _record_run(monkeypatch)

    with pytest.raises(SkepticInfraError, match="already exists"):
        RunContainer("img", workspace).run_capture("echo ok", 60, quarantine)

    assert calls == []


def test_run_capture_preserves_primary_failure_when_private_root_is_empty(
    tmp_path, monkeypatch
):
    workspace, quarantine = tmp_path / "workspace", tmp_path / "quarantine"
    workspace.mkdir()
    _fixed_capture_name(monkeypatch)
    primary = ExecResult(19, "install-out", "install failed", 22)

    def fake_run(cmd, cwd, timeout_s, env):
        if cmd[:2] == ["docker", "run"]:
            return primary
        return ExecResult(0, "", "", 1)

    monkeypatch.setattr("skeptic.sandbox._run", fake_run)

    assert RunContainer("img", workspace).run_capture(
        "echo unreachable", 60, quarantine
    ) == primary


def test_run_capture_refuses_failed_copy_after_primary_test_failure(
    tmp_path, monkeypatch
):
    """A partial copy can never become admissible pytest output."""
    workspace, quarantine = tmp_path / "workspace", tmp_path / "quarantine"
    workspace.mkdir()
    _fixed_capture_name(monkeypatch)
    primary = ExecResult(1, "pytest primary stdout", "pytest primary stderr", 22)

    def fake_run(cmd, cwd, timeout_s, env):
        if cmd[:2] == ["docker", "run"]:
            return primary
        if cmd[:2] == ["docker", "cp"]:
            (quarantine / "junit.xml").write_text(
                '<testsuite tests="1"><testcase name="plausible"/></testsuite>')
            return ExecResult(1, "copied one file", "archive read failed", 3)
        return ExecResult(0, "", "", 1)

    monkeypatch.setattr("skeptic.sandbox._run", fake_run)

    with pytest.raises(SkepticInfraError, match="private output copy failed") as exc:
        RunContainer("img", workspace).run_capture(
            "python -m pytest -q", 60, quarantine)

    detail = str(exc.value)
    assert "primary exit 1" in detail
    assert "pytest primary stdout" in detail
    assert "pytest primary stderr" in detail
    assert "copy exit 1" in detail
    assert "copied one file" in detail
    assert "archive read failed" in detail
    assert (quarantine / "junit.xml").is_file()


def test_run_capture_mounts_inputs_and_contained_workspace_overlay_read_only(
    tmp_path, monkeypatch
):
    workspace, quarantine = tmp_path / "workspace", tmp_path / "quarantine"
    target = workspace / "src" / "module.py"
    target.parent.mkdir(parents=True)
    target.write_text("old = True\n")
    coveragerc = tmp_path / "coveragerc"
    coveragerc.write_text("[run]\n")
    replacement = tmp_path / "replacement.py"
    replacement.write_text("old = False\n")
    _fixed_capture_name(monkeypatch)
    calls = _record_run(monkeypatch)

    RunContainer(
        "img",
        workspace,
        input_mounts=((coveragerc, "/opt/skeptic/coveragerc"),),
        workspace_overlays=((replacement, "./src//module.py"),),
    ).run_capture("echo ok", 60, quarantine)

    run = next(cmd for cmd in calls if cmd[:2] == ["docker", "run"])
    workspace_mount = run.index(f"{workspace}:/workspace")
    input_mount = run.index(f"{coveragerc}:/opt/skeptic/coveragerc:ro")
    overlay_mount = run.index(f"{replacement}:/workspace/src/module.py:ro")
    assert workspace_mount < input_mount < overlay_mount
    assert not any(arg.endswith(":rw") for arg in run)


def test_run_capture_can_skip_install_with_a_read_only_workspace(tmp_path, monkeypatch):
    workspace, quarantine = tmp_path / "workspace", tmp_path / "quarantine"
    workspace.mkdir()
    _fixed_capture_name(monkeypatch)
    calls = _record_run(monkeypatch)

    RunContainer(
        "img", workspace, install_overlay=False, workspace_mode="ro",
    ).run_capture("python -P -m coverage json", 60, quarantine)

    run = next(cmd for cmd in calls if cmd[:2] == ["docker", "run"])
    assert f"{workspace}:/workspace:ro" in run
    assert overlay_install_cmd("/tmp/sv") not in run[-1]
    assert "install.ok" not in run[-1]
    assert "python -P -m coverage json" in run[-1]


@pytest.mark.parametrize(
    "target",
    [
        "/opt/../workspace/pyproject.toml",
        "/opt/./skeptic/input",
        "/opt//skeptic/input",
        "//workspace/pyproject.toml",
        "///workspace/pyproject.toml",
    ],
)
def test_input_mount_target_rejects_normalization_bypass(tmp_path, target):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "input"
    source.write_text("trusted\n")

    with pytest.raises(SkepticInfraError, match="canonical absolute path"):
        RunContainer("img", workspace, input_mounts=((source, target),))


def test_run_capture_refuses_workspace_overlay_target_escape(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    replacement = tmp_path / "replacement.py"
    replacement.write_text("safe = False\n")
    calls = _record_run(monkeypatch)

    with pytest.raises(SkepticInfraError, match="invalid"):
        RunContainer(
            "img", workspace, workspace_overlays=((replacement, "../outside.py"),)
        )

    assert calls == []


@pytest.mark.docker
@pytest.mark.slow
def test_run_capture_returns_regular_private_output(tmp_path, minirepo_spec_and_repo):
    from skeptic.image import ensure_repo_image
    from skeptic.workspace import materialize

    spec, repo = minirepo_spec_and_repo
    pristine, workspace = tmp_path / "pristine", tmp_path / "workspace"
    materialize(repo, spec.repo.commit, pristine)
    ref = ensure_repo_image(spec, pristine, tmp_path / "image")
    materialize(repo, spec.repo.commit, workspace)
    quarantine = tmp_path / "quarantine"
    result = RunContainer(ref.tag, workspace).run_capture(
        "printf harmless > /tmp/skeptic-artifacts/result", 300, quarantine
    )
    assert result.exit_code == 0, result.stderr[-800:]
    assert (quarantine / "result").read_bytes() == b"harmless"


@pytest.mark.docker
@pytest.mark.slow
def test_run_capture_copies_private_output_with_nested_single_file_ro_overlay(
    tmp_path, minirepo_spec_and_repo
):
    """The `rich-0002` shape: a protected file nested under a protected dir."""
    from skeptic.image import ensure_repo_image
    from skeptic.workspace import materialize

    spec, repo = minirepo_spec_and_repo
    pristine, workspace = tmp_path / "pristine", tmp_path / "workspace"
    materialize(repo, spec.repo.commit, pristine)
    image = ensure_repo_image(spec, pristine, tmp_path / "image")
    materialize(repo, spec.repo.commit, workspace)
    protected = workspace / "tests" / "_single_file_golden.txt"
    protected.write_text("fixed golden\n")
    quarantine = tmp_path / "quarantine"

    result = RunContainer(
        image.tag,
        workspace,
        ro_subpaths=("tests/", "tests/_single_file_golden.txt"),
        install_overlay=False,
    ).run_capture(
        "printf sealed > /tmp/skeptic-artifacts/result",
        300,
        quarantine,
    )

    assert result.exit_code == 0, result.stderr[-800:]
    assert (quarantine / "result").read_bytes() == b"sealed"


@pytest.mark.docker
@pytest.mark.slow
def test_run_capture_uses_reserved_exit_when_build_hook_spoofs_old_marker(
    tmp_path, minirepo_spec_and_repo
):
    """Candidate packaging cannot authorize a failed editable install."""
    from skeptic.candidate import snapshot
    from skeptic.image import ensure_repo_image
    from skeptic.workspace import materialize

    spec, repo_dir = minirepo_spec_and_repo
    pristine = materialize(repo_dir, spec.repo.commit, tmp_path / "pristine")
    image = ensure_repo_image(spec, pristine, tmp_path / "image")
    workspace = tmp_path / "workspace"
    snapshot(pristine, workspace)
    (workspace / "spoof_backend.py").write_text(
        "from pathlib import Path\n"
        "Path('/tmp/skeptic-artifacts/install.ok').write_text('spoofed\\n')\n"
        "raise RuntimeError('forced editable-install failure')\n"
    )
    (workspace / "pyproject.toml").write_text(
        "[build-system]\n"
        "requires = []\n"
        "build-backend = 'spoof_backend'\n"
        "backend-path = ['.']\n"
        "[project]\n"
        "name = 'malicious-minirepo'\n"
        "version = '0.0.0'\n"
    )
    quarantine = tmp_path / "quarantine"

    result = RunContainer(image.tag, workspace).run_capture(
        "printf phase-ran > /tmp/skeptic-artifacts/phase-ran",
        300,
        quarantine,
    )

    assert result.exit_code == 125
    assert (quarantine / "install.ok").read_text() == "spoofed\n"
    assert not (quarantine / "phase-ran").exists()


def test_run_container_runs_once_and_removes_itself(tmp_path, monkeypatch):
    calls = _record_run(monkeypatch)
    RunContainer("img", tmp_path).run("pytest -q", timeout_s=60)
    assert len(calls) == 1
    argv = calls[0]
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "-d" not in argv
    assert argv[-3:-1] == ["sh", "-c"]
    assert "pytest -q" in argv[-1]


def test_run_container_prepends_the_overlay_install(tmp_path, monkeypatch):
    calls = _record_run(monkeypatch)
    RunContainer("img", tmp_path).run("pytest -q; echo tail", timeout_s=60)
    script = calls[0][-1]
    assert script.startswith(overlay_install_cmd("/tmp/sv"))
    assert "--system-site-packages" in script
    assert "--no-index" in script and "--no-build-isolation" in script
    # the whole script sits in a brace group, so a failed install runs none
    # of it: `install && a; b` would run b against the system python
    assert script == f"{overlay_install_cmd('/tmp/sv')} && {{ pytest -q; echo tail\n}}"


def test_run_container_venv_is_outside_the_workspace(tmp_path, monkeypatch):
    """The judged tree carries no venv.

    A venv inside /workspace is one more directory coverage, collection, and
    M4's mutation scanner each have to be told to ignore.
    """
    calls = _record_run(monkeypatch)
    RunContainer("img", tmp_path).run("pytest -q", timeout_s=60)
    argv = calls[0]
    assert "/workspace/.sv" not in " ".join(argv)
    assert "/tmp/sv" in argv[-1]
    path = next(a for a in argv if a.startswith("PATH="))
    assert path.startswith("PATH=/tmp/sv/bin:")


def test_docker_run_args_place_env_before_the_image(tmp_path):
    args = docker_run_args("img", tmp_path, env={"COVERAGE_RCFILE": "/opt/skeptic/rc"})
    pair = args.index("COVERAGE_RCFILE=/opt/skeptic/rc")
    assert args[pair - 1] == "-e"
    assert args[-1] == "img"
    assert pair < args.index("img")


def test_run_container_accepts_only_read_only_extra_mounts(tmp_path, monkeypatch):
    rc = tmp_path / "rc"
    rc.write_text("[run]\n")
    out = tmp_path / "out"
    out.mkdir()
    mounts = ((rc, "/opt/skeptic/rc", "ro"), (out, "/out", "ro"))
    args = docker_run_args("img", tmp_path, extra_mounts=mounts)
    joined = " ".join(args)
    assert f"-v {rc}:/opt/skeptic/rc:ro" in joined
    assert f"-v {out}:/out:ro" in joined
    assert joined.index(f"-v {tmp_path}:/workspace ") < joined.index("/opt/skeptic/rc")

    # RunContainer.run passes ro_subpaths, extra_mounts, and env positionally,
    # so an argument-order slip there would first surface inside a container
    calls = _record_run(monkeypatch)
    (tmp_path / "tests").mkdir()
    RunContainer("img", tmp_path, ro_subpaths=("tests/",), extra_mounts=mounts).run(
        "pytest -q", timeout_s=60, env={"COVERAGE_RCFILE": "/opt/skeptic/rc"}
    )
    threaded = " ".join(calls[0])
    assert f"-v {tmp_path}/tests:/workspace/tests:ro" in threaded
    assert f"-v {rc}:/opt/skeptic/rc:ro" in threaded
    assert f"-v {out}:/out:ro" in threaded
    assert "-e COVERAGE_RCFILE=/opt/skeptic/rc" in threaded
    assert calls[0][-4] == "img"

    with pytest.raises(SkepticInfraError, match="only read-only"):
        RunContainer(
            "img", tmp_path,
            extra_mounts=((out, "/out", "rw"),),
        )


def test_docker_run_args_reject_extra_mount_inside_the_workspace(tmp_path):
    rc = tmp_path / "rc"
    rc.write_text("[run]\n")
    with pytest.raises(SkepticInfraError, match="inside /workspace"):
        docker_run_args("img", tmp_path, extra_mounts=((rc, "/workspace/rc", "ro"),))
    with pytest.raises(SkepticInfraError, match="inside /workspace"):
        docker_run_args("img", tmp_path, extra_mounts=((rc, "/workspace", "ro"),))
    with pytest.raises(SkepticInfraError, match="absolute"):
        docker_run_args("img", tmp_path, extra_mounts=((rc, "opt/rc", "ro"),))


def test_docker_run_args_reject_missing_extra_mount_source(tmp_path):
    with pytest.raises(SkepticInfraError, match="does not exist"):
        docker_run_args("img", tmp_path,
                        extra_mounts=((tmp_path / "gone", "/opt/skeptic/rc", "ro"),))


def test_run_container_raises_on_a_missing_ro_subpath_by_default(tmp_path, monkeypatch):
    """The strict default: the baseline side and every BUILD caller get this."""
    calls = _record_run(monkeypatch)
    (tmp_path / "pyproject.toml").write_text("[tool.x]\n")
    rc = RunContainer("img", tmp_path, ro_subpaths=("tests/", "pyproject.toml"))
    assert rc.dropped_ro_subpaths == ()
    with pytest.raises(SkepticInfraError, match="does not exist"):
        rc.run("pytest -q", timeout_s=60)
    assert calls == []


def test_run_container_drops_a_missing_ro_subpath_when_asked_and_records_it(
    tmp_path, monkeypatch
):
    calls = _record_run(monkeypatch)
    (tmp_path / "pyproject.toml").write_text("[tool.x]\n")
    rc = RunContainer("img", tmp_path, ro_subpaths=("tests/", "pyproject.toml"),
                      missing_ro="drop")
    assert rc.dropped_ro_subpaths == ("tests",)
    result = rc.run("pytest -q", timeout_s=60)
    assert result.exit_code == 0
    joined = " ".join(calls[0])
    assert "/workspace/tests:ro" not in joined
    assert f"-v {tmp_path}/pyproject.toml:/workspace/pyproject.toml:ro" in joined


def test_run_container_dropped_ro_subpaths_is_empty_when_every_path_exists(
    tmp_path, monkeypatch
):
    """The negative half: a healthy tree must not feed the check a false positive."""
    calls = _record_run(monkeypatch)
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("[tool.x]\n")
    rc = RunContainer("img", tmp_path, ro_subpaths=("tests/", "pyproject.toml"),
                      missing_ro="drop")
    rc.run("pytest -q", timeout_s=60)
    assert rc.dropped_ro_subpaths == ()
    joined = " ".join(calls[0])
    assert f"-v {tmp_path}/tests:/workspace/tests:ro" in joined
    assert f"-v {tmp_path}/pyproject.toml:/workspace/pyproject.toml:ro" in joined


def test_resolve_ro_subpath_refuses_final_symlink_escape(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "tests").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SkepticInfraError, match="outside the workspace"):
        sandbox_module._resolve_ro_subpath(workspace, "tests")


def test_resolve_ro_subpath_refuses_intermediate_symlink_escape(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    (outside / "tests").mkdir(parents=True)
    (workspace / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SkepticInfraError, match="outside the workspace"):
        sandbox_module._resolve_ro_subpath(workspace, "linked/tests")


def test_resolve_ro_subpath_accepts_internal_symlink(tmp_path):
    workspace = tmp_path / "workspace"
    real = workspace / "real" / "tests"
    real.mkdir(parents=True)
    (workspace / "linked").symlink_to(workspace / "real", target_is_directory=True)

    assert sandbox_module._resolve_ro_subpath(workspace, "linked/tests") == (
        "linked/tests",
        real.resolve(),
    )


@pytest.mark.parametrize("raw", ["/etc", "escape", "dangling"])
def test_run_container_refuses_invalid_ro_subpath_before_missing_ro_drop(tmp_path, raw):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    if raw == "escape":
        outside = tmp_path / "outside"
        outside.mkdir()
        (workspace / raw).symlink_to(outside, target_is_directory=True)
    elif raw == "dangling":
        (workspace / raw).symlink_to(tmp_path / "gone", target_is_directory=True)

    with pytest.raises(SkepticInfraError):
        RunContainer("img", workspace, ro_subpaths=(raw,), missing_ro="drop")


def test_session_container_refuses_invalid_ro_subpath_at_construction(tmp_path):
    with pytest.raises(SkepticInfraError):
        SessionContainer("img", tmp_path, ro_subpaths=("/etc",))


def test_session_container_and_run_container_share_one_install_string(tmp_path, monkeypatch):
    calls = _record_run(monkeypatch, stdout="cid\n")
    SessionContainer("img", tmp_path).start()
    RunContainer("img", tmp_path).run("pytest -q", timeout_s=60)
    session_install = calls[1][-1]
    run_script = calls[2][-1]
    assert session_install == overlay_install_cmd("/workspace/.sv")
    assert run_script.startswith(overlay_install_cmd("/tmp/sv"))
    assert session_install.replace("/workspace/.sv", "/tmp/sv") == overlay_install_cmd("/tmp/sv")
    # the base env comes from the same function, with the other venv path
    assert f"PATH={base_env('/workspace/.sv/bin')['PATH']}" in calls[1]
    assert f"PATH={base_env('/tmp/sv/bin')['PATH']}" in calls[2]


@pytest.mark.docker
@pytest.mark.slow
def test_run_container_imports_the_workspace_source(tmp_path, minirepo_spec_and_repo):
    """The overlay install is what makes the tree importable.

    The image carries the frozen dependency closure and no repo source, so
    the same probe run without the install cannot find the module.
    """
    from skeptic.image import ensure_repo_image
    from skeptic.sandbox import _run
    from skeptic.workspace import materialize

    spec, repo_dir = minirepo_spec_and_repo
    pristine = tmp_path / "pristine"
    materialize(repo_dir, spec.repo.commit, pristine)
    ref = ensure_repo_image(spec, pristine, tmp_path / "img")
    ws = tmp_path / "ws"
    materialize(repo_dir, spec.repo.commit, ws)

    probe = 'python -P -c "import minirepo; print(minirepo.__file__)"'
    imported = RunContainer(ref.tag, ws).run(probe, timeout_s=300)
    assert imported.exit_code == 0, imported.stderr[-800:]
    assert "/workspace/minirepo.py" in imported.stdout

    bare = _run(docker_run_args(ref.tag, ws) + ["sh", "-c", probe],
                cwd=ws, timeout_s=120, env=None)
    assert bare.exit_code != 0
    assert "ModuleNotFoundError" in bare.stderr


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


def test_venv_setup_hands_the_pin_to_pip_and_reads_the_closure_back(tmp_path, monkeypatch):
    """The venv lane runs `environment.install` verbatim, so the pin reaches
    pip the one way that covers every command as written: PIP_CONSTRAINT in
    the install's environment, and only there. Then the freeze is read back:
    a version the pin does not name is an infra error naming it, and without
    a pin there is neither the variable nor the read-back, the pre-M7 shape."""
    from skeptic.sandbox import ExecResult

    runner = VenvRunner(workspace=tmp_path, venv_dir=tmp_path / "v")
    calls: list[tuple[str, dict | None]] = []
    frozen = {"out": "pytest==9.1.1\niniconfig==2.3.0\n"}

    def fake_exec(cmd, timeout_s, env=None):
        calls.append((cmd, env))
        out = frozen["out"] if cmd.startswith("pip freeze") else ""
        return ExecResult(exit_code=0, stdout=out, stderr="", dur_ms=0)

    monkeypatch.setattr(runner, "exec", fake_exec)
    pin = tmp_path / "pins.txt"
    pin.write_text("pytest==9.1.1\niniconfig==2.3.0\ncoverage==7.15.2\n")
    runner.setup(["pip install -q -e . pytest"], constraints=pin)
    runner.setup(["pip install -q -e . pytest"])
    assert calls == [
        ("pip install -q -e . pytest", {"PIP_CONSTRAINT": str(pin.resolve())}),
        ("pip freeze --exclude-editable", None),
        ("pip install -q -e . pytest", None),
    ]

    frozen["out"] = "pytest==9.1.1\nPygments==2.21.0\n"
    with pytest.raises(SkepticInfraError, match="Pygments==2.21.0"):
        runner.setup(["pip install -q -e . pytest"], constraints=pin)


def test_overlay_install_takes_the_pep_517_path_itself():
    """`hkhonming/lp-to-jira#16`, a `setup.py` with no `pyproject.toml`: pip's
    legacy editable path hands off to setuptools' `develop`, which re-invokes
    pip without the offline flags and dies under `--network none`. The flag
    keeps the install on the PEP 660 hook against the image's own setuptools,
    the path every pyproject repo took already."""
    assert "--use-pep517" in overlay_install_cmd("/tmp/sv")
