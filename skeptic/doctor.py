"""Preflight checks behind `skeptic doctor` (plan line 208; M6 spec, PR 2).

Every failure names what failed, why Skeptic needs it, and the exact next
command. The Docker diagnosis is shared with the build/verify refusals in
cli.py so the two surfaces never drift.
"""

from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from shutil import disk_usage

from skeptic.sandbox import DockerDiagnosis, docker_diagnosis

# Floor measured 2026-08-17 (M6 spec, PR 2): one task's workdir ran 545MB
# (du over workdir/click-0001), so 2 GiB is about three tasks of headroom on
# the workdir volume. Docker images live in Docker's own storage, which
# `docker system df` reports; this check covers the volume Skeptic writes to
# directly.
DISK_FLOOR_BYTES = 2 * 1024**3

_DOCKER_WHAT = {
    "cli-absent": "The docker CLI is not on PATH.",
    "unreachable": "Docker daemon unavailable.",
    "timeout": "Docker daemon unavailable: `docker info` gave no answer in 10 s.",
    "permission": "The Docker socket refused permission.",
    "unclassified": "`docker info` failed for an unrecognized reason.",
}


def docker_next(state: str, plat: str | None = None) -> str:
    """The next command per Docker failure state, per platform. The old
    refusals hardcoded the macOS answer; Linux gets systemctl and the docker
    group instead."""
    mac = (plat or sys.platform) == "darwin"
    if state == "cli-absent":
        if mac:
            return "install Docker Desktop, then re-run."
        return "install the docker engine with your package manager, then re-run."
    if state in ("unreachable", "timeout"):
        if mac:
            return "start Docker Desktop, then re-run."
        return "start the daemon (`sudo systemctl start docker`), then re-run."
    if state == "permission":
        if mac:
            return "restart Docker Desktop, then re-run."
        return ("join the docker group (`sudo usermod -aG docker $USER`), "
                "log back in, then re-run.")
    return "run `docker info` and fix what it reports, then re-run."


def docker_failure_message(diag: DockerDiagnosis, why: str,
                           plat: str | None = None) -> str:
    parts = [_DOCKER_WHAT[diag.state]]
    if diag.detail:
        parts.append(f"({diag.detail})")
    host = os.environ.get("DOCKER_HOST")
    if host:
        parts.append(f"(probed DOCKER_HOST={host}, which the next command "
                     f"may not apply to)")
    parts.append(why)
    parts.append(f"Next: {docker_next(diag.state, plat)}")
    return " ".join(parts)


def probe_api_key(key: str) -> tuple[str, str]:
    """('valid' | 'rejected' | 'malformed' | 'unreachable' | 'probe-failed',
    detail). GET /v1/models costs zero tokens. The malformed screen runs
    before any request: a key carrying whitespace or non-ASCII bytes dies
    inside the HTTP client with a misleading connection error and never
    reaches the wire, so it must not be read as a network problem. The probe
    itself must never take doctor down, hence the broad last arm."""
    if key != key.strip() or not key.isascii():
        return "malformed", "it carries whitespace or non-ASCII bytes"
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=key, timeout=10.0, max_retries=1)
        try:
            client.models.list(limit=1)
        except anthropic.AuthenticationError:
            return "rejected", "HTTP 401"
        except anthropic.PermissionDeniedError:
            return "rejected", "HTTP 403: the key authenticated but lacks access"
        except anthropic.RateLimitError:
            return "valid", "auth accepted; rate-limited at probe time"
        except anthropic.APIConnectionError as exc:
            return "unreachable", f"{type(exc).__name__}: {exc}"
        except anthropic.AnthropicError as exc:
            return "probe-failed", f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001 - the probe must never take doctor down
        return "probe-failed", f"{type(exc).__name__}: {exc}"
    return "valid", ""


@dataclass(frozen=True)
class CheckResult:
    name: str
    label: str  # "ok" | "FAIL" | "info"
    detail: str


def check_python(version: tuple[int, int, int] | None = None) -> CheckResult:
    v = version or tuple(sys.version_info[:3])
    if v >= (3, 12):
        return CheckResult("python", "ok", f"{v[0]}.{v[1]}.{v[2]}")
    return CheckResult(
        "python", "FAIL",
        f"this interpreter is {v[0]}.{v[1]}.{v[2]} and Skeptic requires 3.12+ "
        f"(pyproject requires-python gates install, and says nothing about an "
        f"already-running interpreter). Next: python3.12 -m venv .venv && "
        f".venv/bin/pip install -e '.[dev]', then re-run `skeptic doctor`.")


def check_docker() -> CheckResult:
    diag = docker_diagnosis()
    if diag.state == "ok":
        return CheckResult("docker", "ok", "daemon reachable")
    return CheckResult("docker", "FAIL", docker_failure_message(
        diag, "BUILD and VERIFY run inside containers; `skeptic demo`, "
              "`skeptic tasks` and `skeptic seed --check` run without Docker."))


def check_api_key() -> CheckResult:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return CheckResult(
            "api-key", "FAIL",
            "ANTHROPIC_API_KEY is not set. build, build-arm and the paid "
            "verify/eval profiles call the Anthropic API from the host; demo "
            "and deterministic verify run without it. Next: export "
            "ANTHROPIC_API_KEY, then re-run `skeptic doctor`.")
    state, detail = probe_api_key(key)
    if state == "valid":
        note = f"; {detail}" if detail else ""
        return CheckResult(
            "api-key", "ok",
            f"set and accepted (zero-token GET /v1/models{note})")
    if state == "malformed":
        return CheckResult(
            "api-key", "FAIL",
            f"ANTHROPIC_API_KEY is malformed: {detail}, so the SDK fails "
            f"locally before any request leaves the machine. Next: re-export "
            f"it without wrapping whitespace, for example "
            f"export ANTHROPIC_API_KEY=\"$(tr -d '[:space:]' < keyfile)\", "
            f"then re-run `skeptic doctor`.")
    if state == "rejected":
        return CheckResult(
            "api-key", "FAIL",
            f"ANTHROPIC_API_KEY is set but the API rejected it ({detail} on "
            f"the zero-token GET /v1/models probe). Next: replace the key, "
            f"then re-run `skeptic doctor`.")
    if state == "unreachable":
        return CheckResult(
            "api-key", "FAIL",
            f"ANTHROPIC_API_KEY is set but the probe could not reach the API "
            f"({detail}). Next: check connectivity and status.anthropic.com, "
            f"then re-run `skeptic doctor`.")
    return CheckResult(
        "api-key", "FAIL",
        f"the validity probe itself failed ({detail}); the key was not "
        f"judged. Next: reinstall dependencies (.venv/bin/pip install -e "
        f"'.[dev]' -c requirements-dev.lock), then re-run `skeptic doctor`.")


def check_disk() -> CheckResult:
    free = disk_usage(Path.cwd()).free
    gib = free / 1024**3
    if free >= DISK_FLOOR_BYTES:
        return CheckResult("disk", "ok",
                           f"{gib:.1f} GiB free on the workdir volume")
    return CheckResult(
        "disk", "FAIL",
        f"only {gib:.1f} GiB free on the workdir volume where the floor is "
        f"2 GiB (one task's workdir measured 545MB, so the floor is about "
        f"three tasks of headroom; Docker's image storage is separate, see "
        f"`docker system df`). Next: free space on this volume or reclaim "
        f"Docker build cache (`docker system prune`), then re-run "
        f"`skeptic doctor`.")


def check_arch() -> CheckResult:
    return CheckResult(
        "arch", "info",
        f"{platform.machine()} (report only: the pinned base image is a "
        f"multi-platform index and the daemon resolves the host arch at "
        f"build time)")


def run_doctor(echo) -> int:
    """Run every check, print one line each, return the failure count. A
    check that raises becomes its own FAIL row and the rest still run;
    doctor never stops at the first failure and never tracebacks."""
    failed = 0
    checked = 0
    for name, check in (("python", check_python), ("docker", check_docker),
                        ("api-key", check_api_key), ("disk", check_disk),
                        ("arch", check_arch)):
        try:
            r = check()
        except Exception as exc:  # noqa: BLE001 - a crashing check costs one row
            r = CheckResult(
                name, "FAIL",
                f"the check itself crashed ({type(exc).__name__}: {exc}). "
                f"Next: reinstall dependencies (.venv/bin/pip install -e "
                f"'.[dev]' -c requirements-dev.lock) and re-run; if it "
                f"persists, report this line.")
        checked += r.label != "info"
        failed += r.label == "FAIL"
        echo(f"{r.label:<5} {r.name}: {r.detail}")
    echo(f"{failed} of {checked} checks failed." if failed
         else "All checks passed.")
    return failed
