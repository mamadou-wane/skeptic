from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from skeptic.errors import SkepticInfraError, VenvBuildRefused


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    dur_ms: int


def _run(cmd: list[str], cwd: Path, timeout_s: int, env: dict[str, str] | None) -> ExecResult:
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout_s,
            check=False,
        )
        dur = int((time.monotonic() - start) * 1000)
        return ExecResult(proc.returncode, proc.stdout, proc.stderr, dur)
    except subprocess.TimeoutExpired as exc:
        dur = int((time.monotonic() - start) * 1000)
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return ExecResult(-1, out, f"command timed out after {timeout_s}s", dur)
    except FileNotFoundError as exc:
        raise SkepticInfraError(
            f"Executable {cmd[0]!r} not found on the runner PATH "
            f"(full command: {cmd!r}).\n"
            f"Skeptic needed it to run the sandboxed command above. "
            f"Next: check the executable name in the task spec's "
            f"environment.install/test_cmd, or ensure it is installed in "
            f"the venv/image."
        ) from exc


def docker_available() -> bool:
    try:
        proc = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=10, check=False
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def docker_run_args(image: str, workspace: Path, ro_subpaths: tuple[str, ...] = ()) -> list[str]:
    # The container user is the host UID:GID so files written through the
    # bind mount stay owned by the invoking user (M1 review deferral). That
    # user has no /etc/passwd entry in the image, so HOME is pointed at the
    # writable workspace for tools that insist on one.
    args = [
        "docker", "run", "--rm",
        "--network", "none",
        "--pids-limit", "256",
        "--security-opt", "no-new-privileges",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-e", "HOME=/workspace",
        "-v", f"{workspace}:/workspace",
    ]
    # Read-only overlays mount AFTER the rw workspace so they shadow it:
    # tests, runner configs, and goldens stay Builder-visible and immutable
    # (prevention tier, plan section 6).
    for sub in ro_subpaths:
        clean = sub.rstrip("/")
        host = workspace / clean
        if not host.exists():
            raise SkepticInfraError(
                f"read-only mount source {host} does not exist. Docker would "
                f"silently create it as a directory on both sides, turning a "
                f"prevention mount into a hole. Next: fix test_dirs, "
                f"config_files, or golden_dirs in the task spec so every "
                f"entry names a real path."
            )
        args += ["-v", f"{host}:/workspace/{clean}:ro"]
    args += ["-w", "/workspace", image]
    return args


class VenvRunner:
    """Reduced-isolation runner for verify-only work. Never runs BUILD."""

    def __init__(self, workspace: Path, venv_dir: Path) -> None:
        self.workspace = workspace
        self.venv_dir = venv_dir

    @property
    def isolation(self) -> str:
        return "venv-reduced-isolation"

    @property
    def _python(self) -> Path:
        return self.venv_dir / "bin" / "python"

    def setup(self, install_cmds: list[str], python: str = "python3.12") -> None:
        if not self.venv_dir.exists():
            resolved = shutil.which(python)
            if resolved is None:
                raise SkepticInfraError(
                    f"Interpreter {python!r} not found on PATH. "
                    f"Skeptic builds the verify venv with the interpreter "
                    f"named in repo.python. "
                    f"Next: install {python!r}, or fix repo.python in the "
                    f"task spec."
                )
            proc = subprocess.run(
                [resolved, "-m", "venv", str(self.venv_dir)],
                capture_output=True, text=True, check=False,
            )
            if proc.returncode != 0:
                raise SkepticInfraError(
                    f"venv creation failed for {python!r} ({resolved}) "
                    f"(exit {proc.returncode}).\n"
                    f"stderr tail:\n{proc.stderr[-2000:]}\n"
                    f"Skeptic needs a working venv to install and run the "
                    f"target repo's tests. "
                    f"Next: check {resolved} is a working interpreter, or "
                    f"fix repo.python in the task spec, then re-run "
                    f"`skeptic seed --task <id> --check`."
                )
        for cmd in install_cmds:
            result = self.exec(cmd, timeout_s=900)
            if result.exit_code != 0:
                raise SkepticInfraError(
                    f"Install command failed in venv runner: {cmd!r} "
                    f"(exit {result.exit_code}).\nstderr tail:\n{result.stderr[-2000:]}\n"
                    f"Skeptic needs the target repo installed to run its tests. "
                    f"Next: fix the environment.install commands in the task spec, "
                    f"then re-run `skeptic seed --task <id> --check`."
                )

    def exec(self, cmd: str, timeout_s: int, env: dict[str, str] | None = None) -> ExecResult:
        venv_bin = str(self.venv_dir / "bin")
        # COLUMNS is deliberately absent. Pinning it looks like determinism and
        # is not: a suite that renders to a terminal width sets that width
        # explicitly, while a suite that probes terminal-size *fallback* is
        # testing the behavior when COLUMNS is unset, and pinning it fails those
        # tests for a reason unrelated to any seeded bug. Measured on both
        # corpus repos: rich fails 3 tests with COLUMNS pinned, and click's
        # 1939 pass identically either way, so the pin cost coverage and bought
        # nothing (DECISIONS.md #68).
        #
        # Locale and timezone ARE pinned, because those change program output
        # without any test opting in.
        base_env = {
            "PATH": f"{venv_bin}:/usr/bin:/bin",
            "VIRTUAL_ENV": str(self.venv_dir),
            "HOME": str(self.workspace),
            "TERM": "dumb",
            "NO_COLOR": "1",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
        }
        if env:
            base_env.update(env)
        # sh -c on purpose, matching DockerRunner: the same command string
        # must mean the same thing on every runner (M1 review deferral,
        # DECISIONS.md #70). Commands here are spec-authored trusted input.
        # A missing binary is exit 127 from sh; callers convert nonzero
        # exits into SkepticInfraError with the stderr tail.
        return _run(["sh", "-c", cmd], cwd=self.workspace, timeout_s=timeout_s, env=base_env)

    def build_stage_guard(self) -> None:
        raise VenvBuildRefused(
            "The venv runner is verify-only: the Builder (an LLM with shell "
            "access) never runs outside Docker. Start Docker Desktop and re-run, "
            "or use verify-only commands (`skeptic seed --check`, `skeptic verify`)."
        )


class DockerRunner:
    def __init__(self, image: str, workspace: Path) -> None:
        self.image = image
        self.workspace = workspace

    @property
    def isolation(self) -> str:
        return "docker"

    @classmethod
    def build_image(cls, tag: str, context_dir: Path, dockerfile: Path) -> None:
        proc = subprocess.run(
            ["docker", "build", "-t", tag, "-f", str(dockerfile), str(context_dir)],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            raise SkepticInfraError(
                f"docker build failed for {tag} (exit {proc.returncode}).\n"
                f"stderr tail:\n{proc.stderr[-2000:]}\n"
                f"Skeptic builds one image per repo so task runs pay zero installs. "
                f"Next: check the Dockerfile, or run `skeptic doctor`."
            )

    def exec(self, cmd: str, timeout_s: int, env: dict[str, str] | None = None) -> ExecResult:
        args = docker_run_args(self.image, self.workspace)
        env_args: list[str] = []
        for key, value in (env or {}).items():
            env_args += ["-e", f"{key}={value}"]
        full = args[:-1] + env_args + [args[-1], "sh", "-c", cmd]
        return _run(full, cwd=self.workspace, timeout_s=timeout_s, env=None)
