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


def test_docker_run_args_accept_extra_mounts(tmp_path, monkeypatch):
    rc = tmp_path / "rc"
    rc.write_text("[run]\n")
    out = tmp_path / "out"
    out.mkdir()
    mounts = ((rc, "/opt/skeptic/rc", "ro"), (out, "/out", "rw"))
    args = docker_run_args("img", tmp_path, extra_mounts=mounts)
    joined = " ".join(args)
    assert f"-v {rc}:/opt/skeptic/rc:ro" in joined
    assert f"-v {out}:/out:rw" in joined
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
    assert f"-v {out}:/out:rw" in threaded
    assert "-e COVERAGE_RCFILE=/opt/skeptic/rc" in threaded
    assert calls[0][-4] == "img"


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
