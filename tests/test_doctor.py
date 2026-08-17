"""`skeptic doctor` (M6 spec, PR 2): per-check, per-state, no live Docker or
network anywhere. The Docker ladder is unit-tested against the measured
stderr shapes, and every failure asserts the house `Next:` contract."""

import subprocess
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from skeptic import doctor as doctor_mod
from skeptic import sandbox
from skeptic.cli import app
from skeptic.sandbox import DockerDiagnosis, docker_diagnosis

runner = CliRunner()


# --- the Docker ladder, against measured stderr shapes ---

def _fake_run(returncode=0, stderr="", stdout=""):
    def fake(cmd, **kwargs):
        return SimpleNamespace(returncode=returncode, stderr=stderr,
                               stdout=stdout)
    return fake


def test_diagnosis_ok(monkeypatch):
    monkeypatch.setattr(sandbox.subprocess, "run", _fake_run(0))
    assert docker_diagnosis() == DockerDiagnosis("ok", "")


def test_diagnosis_cli_absent(monkeypatch):
    def raise_fnf(cmd, **kwargs):
        raise FileNotFoundError("docker")
    monkeypatch.setattr(sandbox.subprocess, "run", raise_fnf)
    assert docker_diagnosis().state == "cli-absent"


def test_diagnosis_timeout(monkeypatch):
    def raise_timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd="docker info", timeout=10)
    monkeypatch.setattr(sandbox.subprocess, "run", raise_timeout)
    assert docker_diagnosis().state == "timeout"


def test_diagnosis_unreachable_tcp_shape(monkeypatch):
    # Docker Desktop shape, measured: carries "Cannot connect".
    monkeypatch.setattr(sandbox.subprocess, "run", _fake_run(
        1, stderr="ERROR: Cannot connect to the Docker daemon at "
                  "tcp://127.0.0.1:1. Is the docker daemon running?"))
    assert docker_diagnosis().state == "unreachable"


def test_diagnosis_unreachable_socket_shape(monkeypatch):
    # colima/stale-context shape, measured: no "Cannot connect" anywhere.
    monkeypatch.setattr(sandbox.subprocess, "run", _fake_run(
        1, stderr="failed to connect to the docker API at "
                  "unix:///nonexistent.sock; check if the path is correct "
                  "and if the daemon is running: dial unix "
                  "/nonexistent.sock: connect: no such file or directory"))
    assert docker_diagnosis().state == "unreachable"


def test_diagnosis_permission_wins_over_connect_words(monkeypatch):
    # A socket-permission failure carries a connect prefix; permission must
    # classify first or Linux users get told to start a running daemon.
    monkeypatch.setattr(sandbox.subprocess, "run", _fake_run(
        1, stderr="permission denied while trying to connect to the Docker "
                  "daemon socket at unix:///var/run/docker.sock"))
    assert docker_diagnosis().state == "permission"


def test_diagnosis_unclassified_keeps_stderr_tail(monkeypatch):
    monkeypatch.setattr(sandbox.subprocess, "run", _fake_run(
        1, stderr="some novel failure nobody has seen"))
    diag = docker_diagnosis()
    assert diag.state == "unclassified"
    assert "some novel failure" in diag.detail


def test_diagnosis_reads_stdout_when_stderr_is_empty(monkeypatch):
    # Some docker builds report the daemon error under stdout's "Server:".
    monkeypatch.setattr(sandbox.subprocess, "run", _fake_run(
        1, stdout="Server:\nERROR: Cannot connect to the Docker daemon"))
    assert docker_diagnosis().state == "unreachable"


# --- platform-aware next commands ---

def test_docker_next_darwin_vs_linux():
    assert "Docker Desktop" in doctor_mod.docker_next("unreachable", "darwin")
    assert "systemctl start docker" in doctor_mod.docker_next("unreachable",
                                                              "linux")
    assert "usermod -aG docker" in doctor_mod.docker_next("permission",
                                                          "linux")
    assert "Docker Desktop" in doctor_mod.docker_next("cli-absent", "darwin")


def test_failure_message_carries_what_why_and_next():
    msg = doctor_mod.docker_failure_message(
        DockerDiagnosis("unreachable", "dial unix: no such file"), "WHY.")
    assert msg.startswith("Docker daemon unavailable.")
    assert "dial unix" in msg
    assert "WHY." in msg
    assert "Next:" in msg


# --- individual checks ---

def test_check_python_current_interpreter_passes():
    assert doctor_mod.check_python().label == "ok"


def test_check_python_311_fails_with_next():
    r = doctor_mod.check_python((3, 11, 9))
    assert r.label == "FAIL"
    assert "3.11.9" in r.detail
    assert "Next:" in r.detail


def test_check_api_key_unset_fails_and_never_probes(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    called = []
    monkeypatch.setattr(doctor_mod, "probe_api_key",
                        lambda key: called.append(key) or ("valid", ""))
    r = doctor_mod.check_api_key()
    assert r.label == "FAIL"
    assert "not set" in r.detail
    assert "demo and deterministic verify run without it" in r.detail
    assert called == []


@pytest.mark.parametrize("state,fragment", [
    ("valid", "zero-token"),
    ("rejected", "rejected"),
    ("unreachable", "could not reach the API"),
])
def test_check_api_key_probe_states(monkeypatch, state, fragment):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(doctor_mod, "probe_api_key",
                        lambda key: (state, "APIConnectionError: boom"))
    r = doctor_mod.check_api_key()
    assert (r.label == "ok") == (state == "valid")
    assert fragment in r.detail


def test_check_disk_low_fails_with_derivation(monkeypatch):
    monkeypatch.setattr(doctor_mod, "disk_usage",
                        lambda p: SimpleNamespace(free=1 * 1024**3))
    r = doctor_mod.check_disk()
    assert r.label == "FAIL"
    assert "floor is 2 GiB" in r.detail
    assert "measured" in r.detail
    assert "Next:" in r.detail


def test_check_arch_reports_and_never_fails():
    r = doctor_mod.check_arch()
    assert r.label == "info"
    assert "report only" in r.detail


# --- the command, end to end through the CLI ---

def _all_green(monkeypatch):
    monkeypatch.setattr(doctor_mod, "docker_diagnosis",
                        lambda: DockerDiagnosis("ok", ""))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(doctor_mod, "probe_api_key", lambda key: ("valid", ""))
    monkeypatch.setattr(doctor_mod, "disk_usage",
                        lambda p: SimpleNamespace(free=100 * 1024**3))


def test_doctor_all_green_exits_zero(monkeypatch):
    _all_green(monkeypatch)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "All checks passed." in result.output
    for name in ("python", "docker", "api-key", "disk", "arch"):
        assert f"{name}:" in result.output


def test_doctor_docker_down_exits_three_and_keeps_checking(monkeypatch):
    _all_green(monkeypatch)
    monkeypatch.setattr(doctor_mod, "docker_diagnosis",
                        lambda: DockerDiagnosis("unreachable", "test"))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 3
    assert "Docker daemon unavailable" in result.output
    assert "Next:" in result.output
    assert "1 of 4 checks failed." in result.output
    # doctor never stops at the first failure: later checks still printed
    assert "disk:" in result.output


def test_doctor_two_failures_counts_both(monkeypatch):
    _all_green(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(doctor_mod, "disk_usage",
                        lambda p: SimpleNamespace(free=1 * 1024**3))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 3
    assert "2 of 4 checks failed." in result.output


# --- ladder hardening: warning lines and stream choice ---

def test_diagnosis_plugin_warning_does_not_read_as_permission(monkeypatch):
    # Measured shape: a cli-plugin without +x warns "permission denied" on
    # stderr ahead of the genuine stopped-daemon line. WARNING lines are
    # skipped, so this is unreachable, and the next command says start the
    # daemon rather than joining the docker group.
    monkeypatch.setattr(sandbox.subprocess, "run", _fake_run(
        1, stderr='WARNING: Plugin "/tmp/cli-plugins/docker-foo" is not '
                  "valid: failed to fetch metadata: fork/exec: permission "
                  "denied\n"
                  "failed to connect to the docker API at "
                  "unix:///var/run/docker.sock: dial unix: no such file"))
    assert docker_diagnosis().state == "unreachable"


def test_diagnosis_falls_to_stdout_when_stderr_is_only_warnings(monkeypatch):
    monkeypatch.setattr(sandbox.subprocess, "run", _fake_run(
        1, stderr='WARNING: Plugin "docker-foo" is not valid\n',
        stdout="Server:\nERROR: Cannot connect to the Docker daemon"))
    assert docker_diagnosis().state == "unreachable"


def test_diagnosis_detail_is_the_matching_line(monkeypatch):
    monkeypatch.setattr(sandbox.subprocess, "run", _fake_run(
        1, stderr="WARNING: noise\nERROR: Cannot connect to the Docker "
                  "daemon at unix:///var/run/docker.sock"))
    diag = docker_diagnosis()
    assert diag.detail.startswith("ERROR: Cannot connect")


def test_diagnosis_oserror_without_execute_bit(monkeypatch):
    # A docker on PATH without +x raises PermissionError, an OSError the
    # old code let escape as a traceback.
    def raise_perm(cmd, **kwargs):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(sandbox.subprocess, "run", raise_perm)
    assert docker_diagnosis().state == "permission"


def test_docker_next_remaining_branches():
    assert "systemctl" in doctor_mod.docker_next("timeout", "linux")
    assert "package manager" in doctor_mod.docker_next("cli-absent", "linux")
    assert "docker info" in doctor_mod.docker_next("unclassified", "linux")


def test_failure_message_names_docker_host_when_set(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "ssh://box:22")
    msg = doctor_mod.docker_failure_message(
        DockerDiagnosis("unreachable", ""), "WHY.")
    assert "DOCKER_HOST=ssh://box:22" in msg


# --- the probe itself: catch order, malformed screen, broad last arm ---

class _StubModels:
    def __init__(self, exc):
        self._exc = exc

    def list(self, limit):
        if self._exc:
            raise self._exc
        return SimpleNamespace(data=[])


def _stub_anthropic(monkeypatch, exc):
    import anthropic

    class StubClient:
        def __init__(self, **kwargs):
            self.models = _StubModels(exc)

    monkeypatch.setattr(anthropic, "Anthropic", StubClient)
    return anthropic


def test_probe_valid_when_list_succeeds(monkeypatch):
    _stub_anthropic(monkeypatch, None)
    assert doctor_mod.probe_api_key("sk-ok") == ("valid", "")


def test_probe_rejected_on_401(monkeypatch):
    import httpx
    anthropic = _stub_anthropic(monkeypatch, None)
    resp = httpx.Response(401, request=httpx.Request("GET", "http://t"))
    err = anthropic.AuthenticationError("no", response=resp, body=None)
    _stub_anthropic(monkeypatch, err)
    state, detail = doctor_mod.probe_api_key("sk-bad")
    assert state == "rejected"
    assert "401" in detail


def test_probe_unreachable_on_connection_error(monkeypatch):
    import httpx
    anthropic = _stub_anthropic(monkeypatch, None)
    err = anthropic.APIConnectionError(
        request=httpx.Request("GET", "http://t"))
    _stub_anthropic(monkeypatch, err)
    assert doctor_mod.probe_api_key("sk-x")[0] == "unreachable"


def test_probe_malformed_key_never_reaches_the_client(monkeypatch):
    # The two most common env-var mistakes: a trailing newline and wrapping
    # whitespace. The SDK dies locally on these with a misleading
    # connection error, so the screen fires before any client exists.
    import anthropic

    def explode(**kwargs):
        raise AssertionError("client constructed for a malformed key")
    monkeypatch.setattr(anthropic, "Anthropic", explode)
    assert doctor_mod.probe_api_key("sk-fake\n")[0] == "malformed"
    assert doctor_mod.probe_api_key(" sk-fake ")[0] == "malformed"
    assert doctor_mod.probe_api_key("sk-’bad")[0] == "malformed"


def test_probe_failed_when_sdk_breaks(monkeypatch):
    import anthropic

    def explode(**kwargs):
        raise RuntimeError("kaput")
    monkeypatch.setattr(anthropic, "Anthropic", explode)
    state, detail = doctor_mod.probe_api_key("sk-x")
    assert state == "probe-failed"
    assert "RuntimeError" in detail


def test_check_api_key_malformed_names_the_fault(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(doctor_mod, "probe_api_key",
                        lambda key: ("malformed", "it carries whitespace"))
    r = doctor_mod.check_api_key()
    assert r.label == "FAIL"
    assert "malformed" in r.detail
    assert "Next:" in r.detail


# --- a crashing check costs one row, never the report ---

def test_doctor_survives_a_crashing_check(monkeypatch):
    _all_green(monkeypatch)
    def boom(p):
        raise RuntimeError("kaput")
    monkeypatch.setattr(doctor_mod, "disk_usage", boom)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 3
    assert "disk: the check itself crashed (RuntimeError: kaput)" in result.output
    assert "arch:" in result.output          # later checks still ran
    assert "1 of 4 checks failed." in result.output
