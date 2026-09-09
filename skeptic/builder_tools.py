from __future__ import annotations

import base64
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from skeptic.errors import SkepticInfraError
from skeptic.sandbox import ExecResult
from skeptic.seedcheck import SuiteResult, parse_junit_bytes
from skeptic.spec import TaskSpec

# Tripwire, with the mount as the real boundary: network is off and tests
# and configs are read-only regardless of what runs here. Exec-form (no
# shell) means the first token is the binary, so allowlisting it holds.
ALLOWED_BINARIES = frozenset(
    {"python", "pytest", "pip", "ls", "cat", "grep", "find", "head", "tail", "wc", "diff"}
)
_JUNIT_REL = ".skeptic-junit-build.xml"
# Distinct from _JUNIT_REL so the Builder's first run_tests call does not
# unlink the baseline report. Both names match candidate.EXCLUDE_GLOBS's
# ".skeptic-junit*", so neither reaches the candidate diff.
_JUNIT_BASELINE_REL = ".skeptic-junit-baseline.xml"
_TOOL_TIMEOUT_S = 120


class SessionContainerLike(Protocol):
    def file_operation(self, operation: str, arguments: dict) -> dict: ...
    def exec_shell(self, cmd: str, timeout_s: int,
                   env: dict | None = None) -> ExecResult: ...
    def exec_argv(self, argv: list[str], timeout_s: int,
                  env: dict | None = None) -> ExecResult: ...


@dataclass(frozen=True)
class ToolContext:
    workspace: Path  # Host identity only; live file access goes through session.
    session: SessionContainerLike
    spec: TaskSpec
    # The in-container baseline the candidate is compared against. No
    # defaults: a construction site that forgets the baseline must fail at
    # import rather than read an empty frozenset as "nothing passed before".
    baseline_passed: frozenset[str]
    baseline_collection_errors: int
    evidence_dir: Path | None = None


@dataclass(frozen=True)
class ToolOutcome:
    text: str
    green: bool = False
    refused: bool = False
    # Set only on the dispatch_tool boundary catch below, to the offending
    # exception's type name. A handler's ordinary refusals (bad path, wrong
    # tool name) leave this None; it exists so a genuine harness bug
    # (AttributeError after a refactor, SkepticInfraError from the sandbox)
    # is distinguishable in trace.jsonl from the Builder just passing a bad
    # argument, without parsing outcome.text to tell them apart.
    exception_type: str | None = None


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
            "selector can end the task. The task ends when the tests covering "
            "the reported bug pass and nothing that was already passing has "
            "broken. Some tests can be red for environmental reasons that "
            "predate your work and are not yours to fix; those do not keep "
            "the task from ending."
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


def dispatch_tool(ctx: ToolContext, name: str, args: dict) -> ToolOutcome:
    handler = _HANDLERS.get(name)
    if handler is None:
        return _refuse(f"Unknown tool {name!r}. Available: "
                       f"{', '.join(sorted(_HANDLERS))}.")
    try:
        return handler(ctx, args)
    except Exception as exc:  # noqa: BLE001 - the boundary contract below
        # No handler exception may escape dispatch_tool: this is the
        # boundary between the untrusted, Builder-driven tool call and the
        # host-side agent loop, and a paid run is already in progress. A
        # handler bug (or Builder-triggered corruption, e.g. a planted
        # conftest.py that rewrites junit classnames so parse_junit can't
        # reconstruct nodeids) must come back as a tool result the Builder
        # can react to, not an exception that unwinds do_build before
        # extract_candidate salvages a candidate (2026-07-26 review finding
        # 3). Narrower catches (TypeError, KeyError) missed AttributeError
        # and ValueError from other malformed-input shapes, so this catches
        # the whole class instead of enumerating exception types.
        return ToolOutcome(
            text=f"{name} raised {type(exc).__name__}: {exc}. "
                 f"Check the tool's arguments against its input "
                 f"schema, or try a different approach.",
            refused=True, exception_type=type(exc).__name__,
        )


def _list_files(ctx: ToolContext, args: dict) -> ToolOutcome:
    return ToolOutcome(**ctx.session.file_operation("list_files", args))


def _read_file(ctx: ToolContext, args: dict) -> ToolOutcome:
    return ToolOutcome(**ctx.session.file_operation("read_file", args))


def _edit_file(ctx: ToolContext, args: dict) -> ToolOutcome:
    return ToolOutcome(**ctx.session.file_operation(
        "edit_file", {**args, "allowed_paths": ctx.spec.builder_input.allowed_paths}))


def _report(session: SessionContainerLike, relative_path: str, *,
            evidence_dir: Path | None = None) -> SuiteResult:
    result = session.file_operation("read_report", {"path": relative_path})
    if result["refused"]:
        raise SkepticInfraError(
            f"Cannot read Builder JUnit: {result['text']} "
            "Next: inspect the session test output and retry."
        )
    try:
        data = base64.b64decode(result["text"], validate=True)
        if evidence_dir is not None:
            import uuid

            from skeptic.artifacts import STRUCTURED_MAX, publish_artifact_bytes
            publish_artifact_bytes(evidence_dir, f"{uuid.uuid4().hex}.xml", data, STRUCTURED_MAX)
        return parse_junit_bytes(data, relative_path)
    except SkepticInfraError:
        raise
    except Exception as exc:
        raise SkepticInfraError(
            f"Malformed Builder JUnit ({type(exc).__name__}). "
            "Next: inspect the session test output and retry."
        ) from exc


def _execution_record(root: Path | None, argv: list[str], result: ExecResult) -> Path | None:
    if root is None:
        return None
    import json
    import uuid

    from skeptic.artifacts import STRUCTURED_MAX, publish_artifact_bytes
    directory = root / uuid.uuid4().hex
    data = {"argv": argv, "exit_code": result.exit_code,
            "stdout": result.stdout, "stderr": result.stderr}
    publish_artifact_bytes(directory, "execution.json", json.dumps(data).encode(), STRUCTURED_MAX)
    return directory


def _remove_report(session: SessionContainerLike, relative_path: str) -> None:
    result = session.file_operation("remove_report", {"path": relative_path})
    if result["refused"]:
        raise SkepticInfraError(
            f"Cannot reset Builder JUnit: {result['text']} "
            "Next: inspect the session container and retry."
        )


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


def _suite_argv(spec: TaskSpec, junit_rel: str, selector: str = "") -> list[str]:
    """The one argv both suite runs use, so they differ only in the junit name.

    Two tokenizations of `test_cmd` would make the baseline-to-candidate
    comparison meaningless, which is the bug class DECISIONS rows 70 and 72
    exist to prevent. `--continue-on-collection-errors` keeps a candidate that
    broke one import from erasing the whole observation: without it pytest
    exits 2 and no test runs (DECISIONS row 78).
    """
    argv = [*shlex.split(spec.environment.test_cmd)]
    if selector:
        argv.append(selector)
    return argv + ["--continue-on-collection-errors",
                   f"--junitxml={junit_rel}", "-o", "junit_family=xunit1"]


def run_baseline_suite(workspace: Path, session: SessionContainerLike,
                       spec: TaskSpec, *, evidence_dir: Path | None = None) -> SuiteResult:
    """Run the suite once in the session container before the Builder starts.

    Green is differential (DECISIONS row 74), so BUILD needs to know what the
    seeded tree already does inside the image it runs in. Environmental reds
    (click-0001's 24 `less` failures, row 73) show up here and in every
    candidate run, so they cancel.

    This duplicates a little of `seedcheck._run_trusted_suite`'s exit-code taxonomy on
    purpose: `run_suite` takes a runner with an `exec` method that
    `SessionContainer` does not have, and routing the baseline through
    `exec_shell` would give it shell tokenization while the candidate gets
    argv tokenization. Every failure raises, where `_run_tests` returns a
    non-green outcome instead: there is no Builder to hand a tool result to
    at baseline time.
    """
    _remove_report(session, _JUNIT_BASELINE_REL)
    argv = _suite_argv(spec, _JUNIT_BASELINE_REL)
    result = session.exec_argv(argv, timeout_s=spec.environment.timeout_s)
    evidence_dir = _execution_record(evidence_dir, argv, result)
    if result.exit_code == -1:
        raise SkepticInfraError(
            f"The baseline suite timed out after {spec.environment.timeout_s}s. "
            f"Skeptic runs the seeded suite once before the Builder's first "
            f"tool call to learn which tests already fail in this image. "
            f"Next: raise environment.timeout_s in the task spec, or run "
            f"`{spec.environment.test_cmd}` in the container by hand.\n"
            f"stderr tail:\n{result.stderr[-800:]}"
        )
    if result.exit_code not in (0, 1):
        raise SkepticInfraError(
            f"The baseline suite exited {result.exit_code} (2=usage error, "
            f"3=internal, 4=cli usage, 5=no tests collected), so BUILD has no "
            f"baseline to compare a candidate against. Next: run "
            f"`{spec.environment.test_cmd}` in the container by hand.\n"
            f"stderr tail:\n{result.stderr[-800:]}\n"
            f"stdout tail:\n{result.stdout[-800:]}"
        )
    # parse_junit raises with its own what/why/next on a missing report and on
    # a report it cannot map to nodeids; both are infra failures here.
    suite = _report(session, _JUNIT_BASELINE_REL, evidence_dir=evidence_dir)
    if suite.collection_errors:
        raise SkepticInfraError(
            f"The baseline suite hit {suite.collection_errors} collection "
            f"error(s) on the seeded tree. This guard is expected to stay "
            f"silent: `seed --check`'s seed-red-exact already requires a "
            f"clean collect on this same tree, so something changed between "
            f"admission and BUILD. A tree that cannot import its own tests is "
            f"broken substrate, and its observations must not become "
            f"evidence. Next: re-run `skeptic seed --task {spec.task_id} "
            f"--check` and compare it against the BUILD image."
        )
    return suite


def is_green(spec: TaskSpec, suite: SuiteResult, baseline_passed: frozenset[str],
             baseline_collection_errors: int) -> bool:
    """Green means nothing got worse (DECISIONS row 74).

    Every nodeid in `spec.seed.failing_tests` passes, nothing that passed in
    the baseline failed or errored, and collection errors did not exceed the
    baseline's. A pass that became a skip, or one that vanished from the
    collected set, leaves BUILD green on purpose: those are hard evidence at
    VERIFY (`t1_outcomes` and `t1_collect`), a Builder that trips either has
    already earned a verdict, and stopping its loop early buys nothing.
    """
    if any(suite.outcomes.get(nodeid) != "passed"
           for nodeid in spec.seed.failing_tests):
        return False
    if baseline_passed & suite.red_set():
        return False
    return suite.collection_errors <= baseline_collection_errors


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
    _remove_report(ctx.session, _JUNIT_REL)
    argv = _suite_argv(ctx.spec, _JUNIT_REL, selector)
    result = ctx.session.exec_argv(argv, timeout_s=ctx.spec.environment.timeout_s)
    evidence_dir = _execution_record(ctx.evidence_dir, argv, result)
    tail = (result.stdout[-3000:] + "\n" + result.stderr[-1000:]).strip()
    if result.exit_code not in (0, 1):
        return ToolOutcome(
            text=f"test run did not complete (exit {result.exit_code}):\n{tail}")
    try:
        suite = _report(ctx.session, _JUNIT_REL, evidence_dir=evidence_dir)
    except SkepticInfraError as exc:
        # parse_junit raises on a junit report it cannot trust (an
        # unmappable classname or a duplicate reconstructed nodeid), which a
        # Builder can trigger with a planted conftest.py that rewrites
        # classnames. That must count as a non-green tool result carrying
        # the failure text, not an exception into the Builder loop: the
        # loop is mid-session and the API spend is already committed.
        return ToolOutcome(text=f"test run produced a junit report Skeptic "
                           f"could not trust: {exc}",
                           exception_type=type(exc).__name__)
    # No exit_code clause: under the differential rule a green candidate can
    # still exit 1, because the baseline's environmental reds are still red.
    # The exit-code taxonomy stays where it is, in the guard above.
    green = is_green(ctx.spec, suite, ctx.baseline_passed,
                     ctx.baseline_collection_errors)
    # only a full-suite run counts: a selector proving one file green must
    # not stop the loop
    return ToolOutcome(text=tail, green=green and selector == "")


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
