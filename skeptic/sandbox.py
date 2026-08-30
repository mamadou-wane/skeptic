from __future__ import annotations

import errno
import os
import posixpath
import shutil
import stat
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal, Self

from skeptic.errors import SkepticInfraError, VenvBuildRefused
from skeptic.spec import normalize_ro_subpath


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    dur_ms: int


@dataclass(frozen=True)
class HostDeadline:
    """One monotonic authority for a multi-command host operation."""

    expires_at: float

    @classmethod
    def after(cls, timeout_s: int) -> Self:
        return cls(expires_at=time.monotonic() + timeout_s)

    def remaining_timeout_s(self, operation: str) -> int:
        """Return a safe whole-second timeout or refuse to start an operation."""
        left = self.expires_at - time.monotonic()
        timeout_s = int(left)
        if timeout_s < 1:
            raise SkepticInfraError(
                f"{operation} cannot start before its shared host deadline: "
                f"only {max(0.0, left):.3f}s remains, less than one whole second "
                f"the subprocess timeout can represent safely. This is an infra "
                f"failure, never evidence. Next: re-run after checking host and "
                f"Docker daemon load."
            )
        return timeout_s

    def require_active(self, operation: str) -> None:
        """Refuse evidence work once the shared monotonic deadline has expired."""
        if time.monotonic() >= self.expires_at:
            raise SkepticInfraError(
                f"{operation} cannot continue because its shared host deadline "
                f"expired. This is an infra failure, never evidence. Next: re-run "
                f"after checking host and Docker daemon load."
            )


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
    return docker_diagnosis().state == "ok"


@dataclass(frozen=True)
class DockerDiagnosis:
    state: Literal["ok", "cli-absent", "permission", "unreachable", "timeout",
                   "unclassified"]
    detail: str


_CONNECT_MARKERS = ("cannot connect", "failed to connect", "error during connect",
                    "dial unix", "dial tcp")
_SOCKET_MARKERS = ("connect", "socket", "docker daemon", "dial")


def _cap(line: str) -> str:
    return line if len(line) <= 200 else "..." + line[-197:]


def docker_diagnosis() -> DockerDiagnosis:
    """docker_available() collapses every failure into one False and discards
    stderr; this sibling keeps both so doctor and the build/verify refusals
    can name the state. Classification is per line, WARNING lines skipped
    (a cli-plugin warning also says "permission denied"), over stderr when it
    has non-warning content and stdout otherwise (some docker builds report
    the daemon error under stdout's "Server:" heading). On a line, permission
    classifies before the connect family and only when tied to the daemon
    socket, because a genuine socket-permission failure carries a connect
    prefix.
    """
    try:
        proc = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=10, check=False
        )
    except FileNotFoundError:
        return DockerDiagnosis("cli-absent", "")
    except subprocess.TimeoutExpired:
        return DockerDiagnosis("timeout", "")
    except OSError as exc:
        state = "permission" if exc.errno == errno.EACCES else "unclassified"
        return DockerDiagnosis(state, _cap(str(exc)))
    if proc.returncode == 0:
        return DockerDiagnosis("ok", "")

    def lines_of(stream: str | None) -> list[str]:
        return [ln.strip() for ln in (stream or "").splitlines()
                if ln.strip() and not ln.strip().upper().startswith("WARNING")]

    lines = lines_of(proc.stderr) or lines_of(proc.stdout)
    for line in lines:
        low = line.lower()
        if "permission denied" in low and any(m in low for m in _SOCKET_MARKERS):
            return DockerDiagnosis("permission", _cap(line))
        if any(m in low for m in _CONNECT_MARKERS):
            return DockerDiagnosis("unreachable", _cap(line))
    detail = lines[-1] if lines else f"{proc.stderr}{proc.stdout}".strip()[:200]
    return DockerDiagnosis("unclassified", _cap(detail))


def overlay_install_cmd(venv_dir: str) -> str:
    """The offline editable install that puts the live tree on sys.path.

    One string with one caller-chosen venv directory: BUILD overlays at
    /workspace/.sv, VERIFY at /tmp/sv. A change to install policy has to
    land in one place, so both containers call this rather than carrying a
    copy that has to stay identical.

    `--use-pep517` is there for the repo with a `setup.py` and no
    `pyproject.toml` (`hkhonming/lp-to-jira#16`). Without it pip takes its
    legacy editable path, setuptools' `develop` command re-invokes
    `pip install -e . --use-pep517 --no-deps` on its own, that nested pip
    carries neither `--no-index` nor `--no-build-isolation`, and it dies
    under `--network none` before any check runs. With it, pip runs the
    PEP 660 hook against the setuptools already in the image. A repo that
    declares a backend in pyproject.toml took this path already, so nothing
    changes for the corpus.
    """
    return (
        f"python -m venv --system-site-packages {venv_dir} && "
        f"{venv_dir}/bin/pip install -q --no-deps --no-index "
        f"--no-build-isolation --use-pep517 -e /workspace"
    )


def base_env(venv_bin: str) -> dict[str, str]:
    """The environment every container command runs under.

    Locale and timezone are pinned because they change program output with
    no test opting in. COLUMNS is deliberately absent (DECISIONS.md #68).
    """
    return {
        "PATH": f"{venv_bin}:/usr/local/bin:/usr/bin:/bin",
        "HOME": "/workspace",
        "TERM": "dumb",
        "NO_COLOR": "1",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
    }


ExtraMount = tuple[Path, str, Literal["ro", "rw"]]
InputMount = tuple[Path, str]
WorkspaceOverlay = tuple[Path, str]


def _resolve_ro_subpath(workspace: Path, raw: str) -> tuple[str, Path]:
    """Return normalized mount spelling and a source contained by workspace.

    Ordinary missing components are returned for the caller's existing
    raise/drop policy. An existing symlink is resolved strictly, so a dangling
    link or a link that leaves the workspace is always an infrastructure
    refusal instead of looking like an ordinary candidate deletion.
    """
    try:
        clean = normalize_ro_subpath(raw)
    except ValueError as exc:
        raise SkepticInfraError(
            f"read-only mount subpath {raw!r} is invalid: {exc}. "
            f"Skeptic only mounts protected repository-relative paths. "
            f"Next: fix test_dirs, config_files, or golden_dirs in the task spec."
        ) from exc

    try:
        root = workspace.resolve(strict=True)
    except OSError as exc:
        raise SkepticInfraError(
            f"read-only mount workspace {workspace} cannot be resolved: {exc}. "
            f"Skeptic must resolve the workspace before checking protected mounts."
        ) from exc

    cursor = root
    parts = clean.split("/")
    for index, part in enumerate(parts):
        candidate = cursor / part
        try:
            candidate.lstat()
        except FileNotFoundError:
            return clean, candidate.joinpath(*parts[index + 1:])
        except OSError as exc:
            raise SkepticInfraError(
                f"read-only mount source {candidate} cannot be inspected: {exc}. "
                f"Skeptic checks every existing path component before mounting it."
            ) from exc
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise SkepticInfraError(
                f"read-only mount source {candidate} is a dangling or "
                f"unresolvable link ({exc}). Skeptic refuses dangling protected "
                f"paths before applying the candidate missing-path policy."
            ) from exc
        if resolved == root or root not in resolved.parents:
            raise SkepticInfraError(
                f"read-only mount source {candidate} resolves to {resolved}, "
                f"outside the workspace {root}. Protected mount sources must "
                f"remain strictly beneath the workspace."
            )
        cursor = resolved
    return clean, cursor


def _validate_input_mount(source: Path, target: str) -> InputMount:
    if not source.exists():
        raise SkepticInfraError(
            f"input mount source {source} does not exist. Docker would "
            f"silently create it as a directory. Next: this is a harness "
            f"bug; write the capture input before mounting it."
        )
    if not target.startswith("/"):
        raise SkepticInfraError(
            f"input mount target {target!r} is not an absolute path. "
            f"Capture inputs use fixed absolute container paths."
        )
    canonical = posixpath.normpath(target)
    if target.startswith("//") or canonical != target:
        raise SkepticInfraError(
            f"input mount target {target!r} is not a canonical absolute path. "
            f"Capture inputs reject dot components and repeated separators "
            f"before Docker can normalize them into a different target."
        )
    if canonical == "/workspace" or canonical.startswith("/workspace/"):
        raise SkepticInfraError(
            f"input mount target {target!r} is inside /workspace. "
            f"Use a workspace overlay for a contained repository file."
        )
    return source, target


def _validate_workspace_overlay(
    workspace: Path, source: Path, target: str
) -> WorkspaceOverlay:
    clean, _ = _resolve_ro_subpath(workspace, target)
    try:
        source_mode = source.stat(follow_symlinks=False).st_mode
    except OSError as exc:
        raise SkepticInfraError(
            f"workspace overlay source {source} cannot be inspected: {exc}. "
            f"Capture overlays must be host-authored regular files."
        ) from exc
    if not stat.S_ISREG(source_mode):
        raise SkepticInfraError(
            f"workspace overlay source {source} is not a regular file. "
            f"Capture overlays accept only host-authored regular files."
        )
    return source, clean


def docker_run_args(
    image: str,
    workspace: Path,
    ro_subpaths: tuple[str, ...] = (),
    extra_mounts: tuple[ExtraMount, ...] = (),
    env: dict[str, str] | None = None,
    input_mounts: tuple[InputMount, ...] = (),
    workspace_overlays: tuple[WorkspaceOverlay, ...] = (),
    workspace_mode: Literal["rw", "ro"] = "rw",
) -> list[str]:
    # The container user is the host UID:GID so files written through the
    # bind mount stay owned by the invoking user (M1 review deferral). That
    # user has no /etc/passwd entry in the image, so HOME is pointed at the
    # workspace for tools that insist on one. Trusted no-install captures may
    # mount the whole workspace read-only.
    args = [
        "docker", "run", "--rm",
        "--network", "none",
        "--pids-limit", "256",
        "--security-opt", "no-new-privileges",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-e", "HOME=/workspace",
        "-v", f"{workspace}:/workspace{':ro' if workspace_mode == 'ro' else ''}",
    ]
    # Read-only overlays mount AFTER the workspace so they shadow it:
    # tests, runner configs, and goldens stay Builder-visible and immutable
    # (prevention tier, plan section 6).
    for sub in ro_subpaths:
        clean, host = _resolve_ro_subpath(workspace, sub)
        if not host.exists():
            raise SkepticInfraError(
                f"read-only mount source {host} does not exist. Docker would "
                f"silently create it as a directory on both sides, turning a "
                f"prevention mount into a hole. Next: fix test_dirs, "
                f"config_files, or golden_dirs in the task spec so every "
                f"entry names a real path, or check whether the tree changed "
                f"between observation setup and this run, which is the likely "
                f"cause on a candidate tree."
            )
        args += ["-v", f"{host}:/workspace/{clean}:ro"]
    # Helper mounts (a coverage rc, an artifacts directory) live outside the
    # judged tree: anything mounted inside /workspace changes the thing
    # VERIFY measures.
    for src, target, mode in extra_mounts:
        if not src.exists():
            raise SkepticInfraError(
                f"extra mount source {src} does not exist. Docker would "
                f"silently create it as a directory on both sides, so the "
                f"container would read an empty file where the harness meant "
                f"to hand it one. Next: this is a harness bug; the caller "
                f"writes the path before it mounts it."
            )
        if not target.startswith("/"):
            raise SkepticInfraError(
                f"extra mount target {target!r} is not an absolute path. "
                f"Skeptic mounts helper files at fixed absolute locations so "
                f"the script it runs can name them. Next: this is a harness "
                f"bug; give an absolute container path."
            )
        if target == "/workspace" or target.startswith("/workspace/"):
            raise SkepticInfraError(
                f"extra mount target {target!r} is inside /workspace. The "
                f"judged tree is what VERIFY measures, and a harness file "
                f"mounted into it is one more thing the candidate diff, "
                f"coverage, and collection have to be told to ignore. Next: "
                f"this is a harness bug; mount outside /workspace and point "
                f"the tool at it through the environment."
            )
        args += ["-v", f"{src}:{target}:{mode}"]
    # Capture inputs are harness-owned and can never be writable. They live
    # outside /workspace so they do not change the tree VERIFY measures.
    for src, target in input_mounts:
        _validate_input_mount(src, target)
        args += ["-v", f"{src}:{target}:ro"]
    # File overlays mount after the rw workspace so a mutation or other
    # harness-authored replacement shadows exactly one contained repo path.
    for src, target in workspace_overlays:
        _, clean = _validate_workspace_overlay(workspace, src, target)
        args += ["-v", f"{src}:/workspace/{clean}:ro"]
    for key, value in (env or {}).items():
        args += ["-e", f"{key}={value}"]
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

    def setup(self, install_cmds: list[str], python: str = "python3.12",
              constraints: Path | None = None) -> None:
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
        # The install lines run verbatim, so the pin reaches pip the one way
        # that covers every command as written: its environment. Absent a
        # pin, no key is set and the install resolves as it always did.
        pin_env = {"PIP_CONSTRAINT": str(constraints.resolve())} if constraints else None
        for cmd in install_cmds:
            result = self.exec(cmd, timeout_s=900, env=pin_env)
            if result.exit_code != 0:
                raise SkepticInfraError(
                    f"Install command failed in venv runner: {cmd!r} "
                    f"(exit {result.exit_code}).\nstderr tail:\n{result.stderr[-2000:]}\n"
                    f"Skeptic needs the target repo installed to run its tests. "
                    f"Next: fix the environment.install commands in the task spec, "
                    f"then re-run `skeptic seed --task <id> --check`."
                )
        if constraints is not None:
            # Read the closure back, as the image build does: a constraint pip
            # did not honor is silent otherwise. The venv installs a subset of
            # the pin (no build backends, no harness tooling), so the check is
            # that every version present is one the pin names.
            frozen = self.exec("pip freeze --exclude-editable", timeout_s=120)
            named = set(constraints.read_text().splitlines())
            off = [line for line in frozen.stdout.splitlines() if line and line not in named]
            if frozen.exit_code != 0 or off:
                raise SkepticInfraError(
                    f"the venv at {self.venv_dir} resolved versions the pin "
                    f"{constraints} does not name: {', '.join(off[:8]) or frozen.stderr[-300:]}.\n"
                    f"Skeptic pins task installs so a fresh machine measures "
                    f"what the corpus measured. Next: rewrite the pin from a "
                    f"closure you stand behind and record the move in "
                    f"DECISIONS.md, or fix the install lines the pin does not cover."
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
        # venv_env keeps this distinct from the module-level base_env(),
        # which builds the container environment. This one is the host venv's.
        venv_env = {
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
            venv_env.update(env)
        # sh -c on purpose, matching the container runners: the same command
        # string must mean the same thing on every runner (M1 review
        # deferral, DECISIONS.md #70). Commands here are spec-authored
        # trusted input. A missing binary is exit 127 from sh; callers
        # convert nonzero exits into SkepticInfraError with the stderr tail.
        return _run(["sh", "-c", cmd], cwd=self.workspace, timeout_s=timeout_s, env=venv_env)

    def build_stage_guard(self) -> None:
        raise VenvBuildRefused(
            "The venv runner is verify-only: the Builder (an LLM with shell "
            "access) never runs outside Docker. Start Docker Desktop and re-run, "
            "or use verify-only commands (`skeptic seed --check`, `skeptic verify`)."
        )


class RunContainer:
    """One fresh `docker run --rm` per VERIFY observation unit.

    A unit is one (tree state, image) pair. Row 72 scoped the persistent
    container to BUILD, where the Builder issues many tool calls against one
    tree state; VERIFY compares two tree states, so a container that outlived
    one of them is contamination. Several commands inside one `sh -c` are
    fine and intended, and the container is gone when `run` returns.

    Candidate-executing phases install an overlay venv at /tmp/sv, outside the
    judged tree. A trusted transform may explicitly skip that install and
    mount a pre-execution source snapshot read-only. BUILD can
    afford /workspace/.sv because `candidate.EXCLUDE_NAMES` strips it from
    the diff; in VERIFY the workspace is the thing being measured.

    Scripts are harness-composed and spec-authored, which is what makes the
    shell safe here. No candidate-supplied text reaches this string.
    """

    _VENV: ClassVar[str] = "/tmp/sv"

    def __init__(self, image: str, workspace: Path,
                 ro_subpaths: tuple[str, ...] = (),
                 extra_mounts: tuple[ExtraMount, ...] = (),
                 missing_ro: Literal["raise", "drop"] = "raise",
                 input_mounts: tuple[InputMount, ...] = (),
                 workspace_overlays: tuple[WorkspaceOverlay, ...] = (),
                 install_overlay: bool = True,
                 workspace_mode: Literal["rw", "ro"] = "rw") -> None:
        self.image = image
        self.workspace = workspace
        self.install_overlay = install_overlay
        self.workspace_mode = workspace_mode
        self.extra_mounts = tuple(extra_mounts)
        self.input_mounts = tuple(
            _validate_input_mount(source, target)
            for source, target in input_mounts
        )
        self.workspace_overlays = tuple(
            _validate_workspace_overlay(workspace, source, target)
            for source, target in workspace_overlays
        )
        # The tree state is fixed once the instance exists, so the split is
        # decided here rather than per run. "raise" is the default and the
        # baseline side keeps it: the seeded tree is git archive plus the
        # seed patch, so a declared path missing there is an authoring or
        # infra fault that should stop the run (docker_run_args raises).
        # The candidate side asks for "drop", where an absent path is the
        # hack itself: reporting INFRA_ERROR would trade a whole verdict for
        # a mount that had nothing left to protect. The dropped paths become
        # `ro_subpath_deleted` evidence (DECISIONS.md #80).
        dropped: list[str] = []
        kept: list[str] = []
        for sub in ro_subpaths:
            clean, source = _resolve_ro_subpath(workspace, sub)
            if missing_ro == "drop" and not source.exists():
                dropped.append(clean)
            else:
                kept.append(clean)
        self.ro_subpaths = tuple(kept)
        self.dropped_ro_subpaths: tuple[str, ...] = tuple(sorted(dropped))

    @property
    def isolation(self) -> str:
        return "docker-run"

    def run(self, script: str, timeout_s: int,
            env: dict[str, str] | None = None) -> ExecResult:
        """Install the overlay venv and run `script`, in one container."""
        # HOME=/workspace is set twice on purpose: once in docker_run_args'
        # base flags and once here through base_env. Same value, last one
        # wins, and base_env has to carry it for SessionContainer's
        # `docker exec` path, which never sees the base flags.
        merged = {**base_env(f"{self._VENV}/bin"), **(env or {})}
        args = docker_run_args(self.image, self.workspace, self.ro_subpaths,
                               self.extra_mounts, merged,
                               workspace_mode=self.workspace_mode)
        # The script goes in a brace group so `&&` guards all of it. A failed
        # install must reach the caller as its own exit code with nothing run
        # after it: an unguarded `install && a; b` runs b against the system
        # python, which would launder a frozen-closure suite into a candidate
        # observation. The newline before the closing brace is sh syntax.
        guarded = (
            f"{overlay_install_cmd(self._VENV)} && {{ {script}\n}}"
            if self.install_overlay else script
        )
        full = args + ["sh", "-c", guarded]
        return _run(full, cwd=self.workspace, timeout_s=timeout_s, env=None)

    def run_capture(self, script: str, timeout_s: int, quarantine: Path,
                    env: dict[str, str] | None = None, *,
                    deadline: HostDeadline | None = None) -> ExecResult:
        """Run with private output and copy it under one optional host deadline."""
        def bounded_timeout(cap_s: int, operation: str) -> int:
            if deadline is None:
                return cap_s
            return min(cap_s, deadline.remaining_timeout_s(operation))

        primary_timeout_s = bounded_timeout(
            timeout_s, "capture primary Docker run")
        try:
            quarantine.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise SkepticInfraError(
                f"capture quarantine {quarantine} already exists. Skeptic "
                f"requires a fresh host-owned directory for every execution. "
                f"Next: this is a harness lifecycle bug; allocate a new path."
            ) from exc

        merged = {**base_env(f"{self._VENV}/bin"), **(env or {})}
        args = docker_run_args(
            self.image,
            self.workspace,
            self.ro_subpaths,
            self.extra_mounts,
            merged,
            self.input_mounts,
            self.workspace_overlays,
            self.workspace_mode,
        )
        args.remove("--rm")
        name = f"skeptic-{os.getpid()}-{uuid.uuid4().hex}"
        args[2:2] = ["--name", name]
        install = (
            f"{overlay_install_cmd(self._VENV)} && "
            if self.install_overlay else ""
        )
        prepared = (
            "mkdir -p /tmp/skeptic-artifacts && "
            f"{install}"
            "printf 'ok\\n' > /tmp/skeptic-artifacts/install.ok && "
            f"{{ {script}\n}}"
        )
        full = args + ["sh", "-c", prepared]

        try:
            primary = _run(
                full, cwd=self.workspace, timeout_s=primary_timeout_s, env=None)
            if primary.exit_code == -1:
                stop_timeout_s = bounded_timeout(
                    30, "capture timeout-stop confirmation")
                stopped = _run(["docker", "stop", name], cwd=self.workspace,
                               timeout_s=stop_timeout_s, env=None)
                if stopped.exit_code != 0:
                    raise SkepticInfraError(
                        f"capture container {name} could not be confirmed stopped "
                        f"after its run timed out (stop exit {stopped.exit_code}).\n"
                        f"run stderr tail:\n{primary.stderr[-800:]}\n"
                        f"stop stderr tail:\n{stopped.stderr[-800:]}\n"
                        f"Skeptic refuses to copy private output while candidate "
                        f"code may still be writing it. Next: inspect Docker daemon "
                        f"health and remove the container if it remains."
                    )
            copy_timeout_s = bounded_timeout(
                60, "capture private-output copy")
            copied = _run(
                ["docker", "cp", f"{name}:/tmp/skeptic-artifacts/.", str(quarantine)],
                cwd=self.workspace,
                timeout_s=copy_timeout_s,
                env=None,
            )
            if deadline is not None and copied.exit_code == -1:
                raise SkepticInfraError(
                    f"container-private output copy for {name} timed out under "
                    f"the shared host deadline. Skeptic cannot admit an incomplete "
                    f"copy. This is an infra failure, never evidence. Next: inspect "
                    f"Docker storage and retry the verification."
                )
            if copied.exit_code != 0 and primary.exit_code == 0:
                raise SkepticInfraError(
                    f"container-private output copy failed for {name} "
                    f"(exit {copied.exit_code}): {copied.stderr[-800:]}. "
                    f"Skeptic cannot admit outputs that did not cross the "
                    f"container boundary. Next: inspect Docker storage and "
                    f"retry the verification."
                )
        finally:
            _run(["docker", "rm", "-f", name], cwd=self.workspace,
                 timeout_s=30, env=None)
        if deadline is not None:
            deadline.require_active("capture evidence admission")
        return primary


class SessionContainer:
    """Persistent tool-exec container for one BUILD session.

    BUILD is the one stage where many commands share warm state (the
    overlay venv, bytecode caches), so it gets a long-lived container;
    every VERIFY-side run stays fresh-per-container. The workspace mounts
    rw with tests/configs/goldens shadowed read-only (prevention tier).
    """

    _VENV: ClassVar[str] = "/workspace/.sv"
    _BASE_ENV: ClassVar[dict[str, str]] = base_env(f"{_VENV}/bin")
    _INSTALL: ClassVar[str] = overlay_install_cmd(_VENV)

    def __init__(self, image: str, workspace: Path,
                 ro_subpaths: tuple[str, ...] = ()) -> None:
        self.image = image
        self.workspace = workspace
        self.ro_subpaths = tuple(
            _resolve_ro_subpath(workspace, sub)[0] for sub in ro_subpaths
        )
        self._container_id: str | None = None

    @property
    def isolation(self) -> str:
        return "docker-session"

    def start(self) -> None:
        run_args = docker_run_args(self.image, self.workspace, self.ro_subpaths)
        # docker_run_args ends with [-w, /workspace, IMAGE]; insert -d after
        # `docker run` and keep the container alive for the session
        args = run_args[:2] + ["-d"] + run_args[2:] + ["sleep", "infinity"]
        started = _run(args, cwd=self.workspace, timeout_s=60, env=None)
        if started.exit_code != 0:
            raise SkepticInfraError(
                f"tool-exec container failed to start from {self.image} "
                f"(exit {started.exit_code}): {started.stderr[-800:]}\n"
                f"Skeptic runs every Builder tool inside this container. "
                f"Next: `docker run --rm {self.image} true` by hand to see "
                f"the daemon's complaint."
            )
        self._container_id = started.stdout.strip()
        install = self.exec_shell(self._INSTALL, timeout_s=300)
        if install.exit_code != 0:
            self.stop()
            raise SkepticInfraError(
                f"offline editable install failed inside the tool-exec "
                f"container (exit {install.exit_code}).\n"
                f"stderr tail:\n{install.stderr[-1500:]}\n"
                f"Skeptic overlays a workspace venv on the image's dependency "
                f"closure so the repo under test imports from the live tree. "
                f"Next: rebuild the repo image (`docker rmi {self.image}`) so "
                f"the constraints include the build backend, then re-run."
            )

    def _exec(self, tail: list[str], timeout_s: int,
              env: dict[str, str] | None) -> ExecResult:
        if self._container_id is None:
            raise SkepticInfraError(
                "SessionContainer.exec_shell/exec_argv called before "
                "start(). Skeptic starts the tool-exec container once per "
                "BUILD session. Next: this is a harness bug; report the "
                "traceback."
            )
        merged = {**self._BASE_ENV, **(env or {})}
        env_args: list[str] = []
        for key, value in merged.items():
            env_args += ["-e", f"{key}={value}"]
        args = ["docker", "exec", *env_args, "-w", "/workspace",
                self._container_id, *tail]
        return _run(args, cwd=self.workspace, timeout_s=timeout_s, env=None)

    def exec_shell(self, cmd: str, timeout_s: int,
                   env: dict[str, str] | None = None) -> ExecResult:
        """Harness-composed commands: full shell semantics, trusted input."""
        return self._exec(["sh", "-c", cmd], timeout_s, env)

    def exec_argv(self, argv: list[str], timeout_s: int,
                  env: dict[str, str] | None = None) -> ExecResult:
        """Builder-supplied commands: exec form, no shell, so the allowlisted
        first token cannot chain further commands."""
        return self._exec(list(argv), timeout_s, env)

    def stop(self) -> None:
        if self._container_id is not None:
            _run(["docker", "rm", "-f", self._container_id],
                 cwd=self.workspace, timeout_s=30, env=None)
            self._container_id = None

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
