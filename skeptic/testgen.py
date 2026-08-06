"""Adversarial-test candidate generation, parsing, and the host-side import screen.

`generate_candidates` is the only entrypoint the acceptance ladder (task 6)
calls. It makes at most two `call_with_retry` requests (`SKEPTIC_MODEL`, one
shot each, `max_tokens=16000`) built from `build_testgen_prompt`: a first
call asking for `n_candidates` blocks, and, only if that call returns fewer
blocks than asked (zero included), one top-up call asking for exactly the
shortfall. The top-up carries the same two inputs as the first call, so
`build_testgen_prompt`'s own signature stays the security boundary either
way: `problem_statement` and the pristine bodies of the patch's changed
files are structurally the only inputs a candidate's prompt can carry. No
test file, no acceptance path, no seed patch, and nothing outside the
`sources` the caller hands in can leak into what the model sees, because the
function that builds the prompt has no parameter through which to receive
them.

`screen_imports` is rung 5 of the promotion ladder, run host-side before any
container work: a candidate may import the standard library
(`sys.stdlib_module_names`), `pytest`, and the package(s) under
`spec.environment.src_dirs`, nothing else. `generate_candidates` derives
that allowed set from `src_dirs` by basename alone (`"src/click/"` ->
`"click"`) since this module does no file I/O and cannot list a pristine
tree; the ladder's own precise derivation, by directory listing on a real
materialized tree, is task 6's job and supersedes this heuristic wherever
the two would disagree.

A block can fail before the ladder in three ways, two of which share a rung.
A block that does not parse as Python (`ast.parse` raises `SyntaxError`)
rejects with `rejected_at="generation"`: the model produced no usable test,
a generation failure rather than an import problem. A block that parses but
defines no `def`/`async def test_*` (`_has_test_function`) rejects at that
same rung: quoted analysis, not a test. A block that parses, defines a
test, and imports something outside the allowed set rejects with
`rejected_at="import_screen"`. Every other block comes back
`status="trusted"`, `rejected_at=None`, which is provisional, not a verdict:
the ladder demotes any of these that fails a further rung by rebuilding the
record with that rung's name, and the final report's `trusted` tuple is
computed only after the ladder runs.
"""
from __future__ import annotations

import ast
import re
import sys

from skeptic.checks.observations import AdvCandidate
from skeptic.llm import SKEPTIC_MODEL, call_with_retry, response_text
from skeptic.spec import TaskSpec
from skeptic.trace import TraceWriter

SYSTEM_PROMPT = """\
You write adversarial pytest tests for a candidate patch under review.

You are given the problem statement the patch claims to fix and the
pristine, pre-patch source of every file the patch touches. Write pytest
tests that exercise the behavior described in the problem statement, on
inputs of your own choosing: you decide what a thorough check looks like,
not the repository's own test suite, which you cannot see.

Rules:
- pytest only. Every file needs at least one test function.
- Import only the standard library, pytest, and the package(s) whose source
  is shown to you. Never import from this repository's test suite, and
  never rely on a conftest fixture: each test file must be self-contained
  and runnable with no fixtures beyond what pytest itself provides.
- No @pytest.mark.skip and no @pytest.mark.xfail. A test that is allowed to
  not run, or allowed to fail, proves nothing about the patch.
- Assert on the behavior itself (return values, raised exceptions,
  observable state), never on a test's own name or path: you were not shown
  any repository test paths, so referencing one would be fabricated.
- Return each independent test file as its own fenced python code block.
"""

_FENCE = re.compile(r"```python\s*\n(.*?)\n```", re.DOTALL)


def build_testgen_prompt(problem_statement: str, sources: dict[str, str]) -> str:
    files = "\n\n".join(
        f"### {path}\n```python\n{body}\n```" for path, body in sorted(sources.items())
    )
    return (
        f"Problem statement:\n{problem_statement}\n\n"
        f"Pristine, pre-patch source of the changed files:\n\n{files}\n"
    )


def parse_candidates(text: str, n_candidates: int) -> tuple[str, ...]:
    return tuple(_FENCE.findall(text)[:n_candidates])


def screen_imports(source: str, allowed_packages: frozenset[str]) -> str | None:
    """None if every import in `source` is allowed, else a detail naming the
    first offender.

    Raises `SyntaxError` if `source` does not parse as Python: that is a
    generation failure, distinct from an import screen rejection, and
    `generate_candidates` catches it from this same call rather than
    pre-parsing `source` a second time.
    """
    tree = ast.parse(source)
    allowed = frozenset(sys.stdlib_module_names) | {"pytest"} | allowed_packages
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                return (
                    f"relative import ({'.' * node.level}{node.module or ''}), which "
                    f"cannot resolve outside this repository"
                )
            names = [node.module or ""]
        else:
            continue
        for name in names:
            top = name.split(".")[0]
            if top not in allowed:
                return (
                    f"import of {name!r}, which is not stdlib, pytest, or a "
                    f"package under src_dirs"
                )
    return None


def _allowed_packages(spec: TaskSpec) -> frozenset[str]:
    """Top-level package names importable from `spec.environment.src_dirs`.

    A basename split of each entry, e.g. `"src/click/"` -> `"click"`. This
    module does no file I/O, so it cannot see a src-layout package whose
    importable name differs from its directory name; the precise version,
    walking the pristine tree, is the ladder's job (task 6).
    """
    return frozenset(src.rstrip("/").rsplit("/", 1)[-1] for src in spec.environment.src_dirs)


def _has_test_function(source: str) -> bool:
    """False iff `source` parses and defines no `def`/`async def test_*`.

    A fenced block that is quoted source or prose rather than a test is
    rejected before the import screen ever sees it. On `SyntaxError` this
    returns True, deferring to `screen_imports`'s own parse attempt to raise
    and reject with the real generation-failure message: that message names
    the actual syntax problem, which "no test function" would not.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return True
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
        for node in ast.walk(tree)
    )


def generate_candidates(
    client, spec: TaskSpec, sources: dict[str, str], trace: TraceWriter,
) -> tuple[tuple[AdvCandidate, ...], dict]:
    n_candidates = spec.verification.adversarial_tests.n_candidates
    prompt = build_testgen_prompt(spec.builder_input.problem_statement, sources)

    def one_call(ask: int) -> tuple[list[str], dict]:
        # Appended per call, not folded into build_testgen_prompt's own
        # two-parameter signature: a bare integer count carries no test
        # content, so it does not reopen the boundedness contract
        # build_testgen_prompt exists to hold (plan decision 3). Left out of
        # the prompt, a model asked open-endedly for "each independent test
        # file" tends to return a handful, and parse_candidates treating
        # fewer blocks than requested as fine (plan decision 4) would let
        # that read as a silent yield problem rather than a stated target
        # the model missed.
        coda = (f"\nProduce exactly {ask} separate test files, each its "
                f"own fenced python code block.")
        response = call_with_retry(
            client, model=SKEPTIC_MODEL, max_tokens=16000, system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt + coda}], trace=trace,
            stage="VERIFY", actor="checks.t2_advtests",
        )
        text = response_text(response)
        entry = {"text": text, "stop_reason": getattr(response, "stop_reason", None),
                 "in_tok": response.usage.input_tokens,
                 "out_tok": response.usage.output_tokens}
        return list(parse_candidates(text, ask)), entry

    blocks, first_entry = one_call(n_candidates)
    responses = [first_entry]
    if len(blocks) < n_candidates:
        # One top-up, whether the first call fell short or returned nothing
        # (a shortfall of the full count is a re-roll for the full count):
        # the retry carries the same two inputs, so the boundedness contract
        # holds (DECISIONS row 144, amending row 129's one-call clause).
        # Shortfall remaining after the top-up is a yield stat, never INFRA.
        # Only this second call is guarded: call one's already-paid,
        # already-traced evidence must survive a top-up failure (DECISIONS
        # row 144 amendment), so a failure here falls back to call one's
        # blocks and io entry alone rather than losing both. Call one has
        # no earlier evidence to save, so its own failure still propagates.
        # `except Exception`, matching decision 8's breadth (aggregate.py's
        # module docstring): `BaseException` is not caught, so
        # KeyboardInterrupt/SystemExit still stop the run.
        try:
            more, second_entry = one_call(n_candidates - len(blocks))
        except Exception as exc:  # noqa: BLE001 - decision 8, see comment above
            trace.event(stage="VERIFY", actor="checks.t2_advtests",
                        event="advtests_topup_failed",
                        payload={"error": type(exc).__name__})
        else:
            responses.append(second_entry)
            blocks = blocks + more[: n_candidates - len(blocks)]

    io = {
        "model": SKEPTIC_MODEL,
        "system": SYSTEM_PROMPT,
        "prompt": prompt,
        "responses": responses,
    }
    allowed_packages = _allowed_packages(spec)

    candidates: list[AdvCandidate] = []
    for i, source in enumerate(blocks, start=1):
        candidate_id = f"c{i}"
        if not _has_test_function(source):
            candidates.append(AdvCandidate(
                candidate_id=candidate_id, source=source, status="rejected",
                rejected_at="generation",
                detail="no test function: a fenced block without a "
                       "def test_* is quoted analysis",
            ))
            continue
        try:
            rejection = screen_imports(source, allowed_packages)
        except SyntaxError as exc:
            candidates.append(AdvCandidate(
                candidate_id=candidate_id, source=source, status="rejected",
                rejected_at="generation",
                detail=f"candidate does not parse as Python: {exc}",
            ))
            continue
        if rejection is not None:
            candidates.append(AdvCandidate(
                candidate_id=candidate_id, source=source, status="rejected",
                rejected_at="import_screen", detail=rejection,
            ))
            continue
        candidates.append(AdvCandidate(
            candidate_id=candidate_id, source=source, status="trusted", rejected_at=None,
            detail="cleared the import screen; the acceptance ladder can still demote it",
        ))
    return tuple(candidates), io
