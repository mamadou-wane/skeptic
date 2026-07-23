from __future__ import annotations

import shlex
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


def docker_run_args(image: str, workspace: Path) -> list[str]:
    return [
        "docker", "run", "--rm",
        "--network", "none",
        "--pids-limit", "256",
        "--security-opt", "no-new-privileges",
        "--user", "1000:1000",
        "-v", f"{workspace}:/workspace",
        "-w", "/workspace",
        image,
    ]


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
        base_env = {
            "PATH": f"{venv_bin}:/usr/bin:/bin",
            "VIRTUAL_ENV": str(self.venv_dir),
            "HOME": str(self.workspace),
            "TERM": "dumb",
            "COLUMNS": "80",
            "NO_COLOR": "1",
        }
        if env:
            base_env.update(env)
        # `pip install ...` and `python -m pytest` style commands resolve
        # against the venv because its bin dir leads PATH.
        return _run(shlex.split(cmd), cwd=self.workspace, timeout_s=timeout_s, env=base_env)

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
