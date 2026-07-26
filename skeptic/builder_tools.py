from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from skeptic.sandbox import ExecResult
from skeptic.seedcheck import parse_junit
from skeptic.spec import TaskSpec

# Tripwire, with the mount as the real boundary: network is off and tests
# and configs are read-only regardless of what runs here. Exec-form (no
# shell) means the first token is the binary, so allowlisting it holds.
ALLOWED_BINARIES = frozenset(
    {"python", "pytest", "pip", "ls", "cat", "grep", "find", "head", "tail", "wc", "diff"}
)
_JUNIT_REL = ".skeptic-junit-build.xml"
_MAX_READ_BYTES = 100_000
_TOOL_TIMEOUT_S = 120


class SessionContainerLike(Protocol):
    def exec_shell(self, cmd: str, timeout_s: int,
                   env: dict | None = None) -> ExecResult: ...
    def exec_argv(self, argv: list[str], timeout_s: int,
                  env: dict | None = None) -> ExecResult: ...


@dataclass(frozen=True)
class ToolContext:
    workspace: Path
    session: SessionContainerLike
    spec: TaskSpec


@dataclass(frozen=True)
class ToolOutcome:
    text: str
    suite_green: bool = False
    refused: bool = False


TOOL_DEFS: list[dict] = [
    {
        "name": "list_files",
        "description": "List files under a workspace directory (recursive).",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string",
                                    "description": "Relative dir, default repo root."}},
        },
    },
    {
        "name": "read_file",
        "description": "Read a file from the workspace (truncated past 100 kB).",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Replace an exact unique string in a file, or create a new file by "
            "passing an empty old_str. Edits are restricted to the allowed paths."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_str": {"type": "string"},
                "new_str": {"type": "string"},
            },
            "required": ["path", "old_str", "new_str"],
        },
    },
    {
        "name": "run_tests",
        "description": (
            "Run the repo test suite. Pass selector to narrow the run to one "
            "file or nodeid while investigating; only a full run with no "
            "selector can end the task."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"selector": {
                "type": "string",
                "description": (
                    "a single token naming a path or nodeid, e.g. "
                    "tests/test_x.py or tests/test_x.py::test_name; no -k "
                    "expressions"
                ),
            }},
        },
    },
    {
        "name": "run_cmd",
        "description": "Run an allowlisted read-only command in the sandbox (no shell).",
        "input_schema": {
            "type": "object",
            "properties": {"argv": {"type": "array", "items": {"type": "string"}}},
            "required": ["argv"],
        },
    },
]


def _refuse(text: str) -> ToolOutcome:
    return ToolOutcome(text=text, refused=True)


def _safe_rel(ctx: ToolContext, raw: str) -> Path | None:
    """Resolve a Builder-supplied path inside the workspace, or None."""
    candidate = (ctx.workspace / raw).resolve()
    root = ctx.workspace.resolve()
    if candidate == root or root in candidate.parents:
        return candidate
    return None


def _in_allowed(ctx: ToolContext, rel: str) -> bool:
    return any(
        rel == p.rstrip("/") or rel.startswith(p.rstrip("/") + "/")
        for p in ctx.spec.builder_input.allowed_paths
    )


def dispatch_tool(ctx: ToolContext, name: str, args: dict) -> ToolOutcome:
    handler = _HANDLERS.get(name)
    if handler is None:
        return _refuse(f"Unknown tool {name!r}. Available: "
                       f"{', '.join(sorted(_HANDLERS))}.")
    try:
        return handler(ctx, args)
    except (TypeError, KeyError) as exc:
        return _refuse(f"Malformed arguments for {name}: {exc!r}. "
                       f"Check the tool's input schema and retry.")


def _list_files(ctx: ToolContext, args: dict) -> ToolOutcome:
    target = _safe_rel(ctx, str(args.get("path", "")))
    if target is None or not target.is_dir():
        return _refuse("path must name a directory inside the workspace.")
    lines = []
    for p in sorted(target.rglob("*")):
        rel = p.relative_to(ctx.workspace)
        if any(part in EXCLUDED_PARTS or part.endswith(".egg-info")
               for part in rel.parts):
            continue
        if p.is_file():
            lines.append(str(rel))
    return ToolOutcome(text="\n".join(lines[:2000]) or "(empty)")


EXCLUDED_PARTS = {".sv", ".pytest_cache", "__pycache__"}


def _read_file(ctx: ToolContext, args: dict) -> ToolOutcome:
    target = _safe_rel(ctx, str(args["path"]))
    if target is None or not target.is_file():
        return _refuse(f"{args['path']!r} is not a file inside the workspace.")
    # Bound the read itself (not just the returned text): a Builder can
    # create an arbitrarily large file via the allowlisted `python` in
    # run_cmd, and this call runs host-side, outside any container limit.
    # Read one character past the cap so truncation reflects what was
    # actually decoded, not the file's byte size (multi-byte UTF-8 makes
    # those differ: st_size counts bytes, .read(n) counts characters).
    with target.open(errors="replace") as fh:
        data = fh.read(_MAX_READ_BYTES + 1)
    if len(data) > _MAX_READ_BYTES:
        data = data[:_MAX_READ_BYTES] + "\n[truncated]"
    return ToolOutcome(text=data)


def _edit_file(ctx: ToolContext, args: dict) -> ToolOutcome:
    raw = str(args["path"])
    target = _safe_rel(ctx, raw)
    if target is None:
        return _refuse(f"{raw!r} escapes the workspace.")
    rel = str(target.relative_to(ctx.workspace.resolve()))
    if not _in_allowed(ctx, rel):
        return _refuse(
            f"{rel!r} is outside allowed_paths "
            f"{ctx.spec.builder_input.allowed_paths}; edits are restricted to "
            f"those paths. Tests and configs are read-only by design."
        )
    old, new = str(args["old_str"]), str(args["new_str"])
    if old == "":
        if target.exists():
            return _refuse(f"{rel!r} already exists; pass the exact old_str to edit it.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new)
        return ToolOutcome(text=f"created {rel}")
    if not target.is_file():
        return _refuse(f"{rel!r} does not exist; create it with an empty old_str.")
    content = target.read_text()
    count = content.count(old)
    if count != 1:
        return _refuse(
            f"old_str occurs {count} times in {rel}; it must occur exactly "
            f"once. Add surrounding context to make it unique."
        )
    target.write_text(content.replace(old, new, 1))
    return ToolOutcome(text=f"edited {rel}")


# Builder-supplied, so it goes through exec_argv (no shell) as one argv
# token, never interpolated into a shell string: a shell metacharacter or
# a second token (e.g. a -k expression) would smuggle chained commands
# past the allowlist that run_cmd otherwise enforces.
_SELECTOR_METACHARS = set(";&|<>$`(){}[]*?!~#\\\n\r\t\"'")


def _selector_problem(selector: str) -> str | None:
    """Return why `selector` can't be used as a single argv token, or None."""
    if any(ch in _SELECTOR_METACHARS for ch in selector):
        return "contains a shell metacharacter"
    if len(selector.split()) > 1:
        return "is more than one token"
    return None


def _run_tests(ctx: ToolContext, args: dict) -> ToolOutcome:
    selector = str(args.get("selector", "")).strip()
    if selector:
        problem = _selector_problem(selector)
        if problem is not None:
            return _refuse(
                f"selector {selector!r} is not usable: {problem}. A selector "
                f"must be exactly one token: a test path (tests/test_x.py) or "
                f"a full nodeid (tests/test_x.py::test_name). No spaces, no "
                f"shell metacharacters, no -k expressions."
            )
    junit_host = ctx.workspace / _JUNIT_REL
    junit_host.unlink(missing_ok=True)
    argv = [*shlex.split(ctx.spec.environment.test_cmd)]
    if selector:
        argv.append(selector)
    argv += [f"--junitxml={_JUNIT_REL}", "-o", "junit_family=xunit1"]
    result = ctx.session.exec_argv(argv, timeout_s=ctx.spec.environment.timeout_s)
    tail = (result.stdout[-3000:] + "\n" + result.stderr[-1000:]).strip()
    if result.exit_code not in (0, 1) or not junit_host.is_file():
        return ToolOutcome(
            text=f"test run did not complete (exit {result.exit_code}):\n{tail}")
    suite = parse_junit(junit_host)
    green = (
        not suite.red_set()
        and suite.collection_errors == 0
        and result.exit_code == 0
    )
    # only a full-suite green run counts: a selector proving one file green
    # must not stop the loop
    return ToolOutcome(text=tail, suite_green=green and selector == "")


def _run_cmd(ctx: ToolContext, args: dict) -> ToolOutcome:
    argv = [str(a) for a in args["argv"]]
    if not argv or argv[0] not in ALLOWED_BINARIES:
        return _refuse(
            f"run_cmd allows only {sorted(ALLOWED_BINARIES)} as the first "
            f"token; got {argv[:1]!r}. There is no shell: pipes and && do "
            f"not work here."
        )
    result = ctx.session.exec_argv(argv, timeout_s=_TOOL_TIMEOUT_S)
    return ToolOutcome(
        text=f"exit {result.exit_code}\n{result.stdout[-3000:]}{result.stderr[-1000:]}"
    )


_HANDLERS = {
    "list_files": _list_files,
    "read_file": _read_file,
    "edit_file": _edit_file,
    "run_tests": _run_tests,
    "run_cmd": _run_cmd,
}
