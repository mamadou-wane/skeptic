"""Adversarial-test candidate generation, parsing, and the host-side import screen.

`generate_candidates` is the only entrypoint the acceptance ladder (task 6)
calls. It makes exactly one `call_with_retry` request (`SKEPTIC_MODEL`, one
shot, `max_tokens=16000`) built from `build_testgen_prompt`, whose own
signature is the security boundary: `problem_statement` and the pristine
bodies of the patch's changed files are structurally the only inputs a
candidate's prompt can carry. No test file, no acceptance path, no seed
patch, and nothing outside the `sources` the caller hands in can leak into
what the model sees, because the function that builds the prompt has no
parameter through which to receive them.

`screen_imports` is rung 5 of the promotion ladder, run host-side before any
container work: a candidate may import the standard library
(`sys.stdlib_module_names`), `pytest`, and the package(s) under
`spec.environment.src_dirs`, nothing else. `generate_candidates` derives
that allowed set from `src_dirs` by basename alone (`"src/click/"` ->
`"click"`) since this module does no file I/O and cannot list a pristine
tree; the ladder's own precise derivation, by directory listing on a real
materialized tree, is task 6's job and supersedes this heuristic wherever
the two would disagree.

Two ways a candidate never reaches the ladder. A block that does not parse
as Python (`ast.parse` raises `SyntaxError`) rejects with
`rejected_at="generation"`: the model produced no usable test, a generation
failure rather than an import problem. A block that parses but imports
something outside the allowed set rejects with `rejected_at="import_screen"`.
Every other parsed block comes back `status="trusted"`, `rejected_at=None`,
which is provisional, not a verdict: the ladder demotes any of these that
fails a further rung by rebuilding the record with that rung's name, and the
final report's `trusted` tuple is computed only after the ladder runs.
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


def generate_candidates(
    client, spec: TaskSpec, sources: dict[str, str], trace: TraceWriter,
) -> tuple[AdvCandidate, ...]:
    prompt = build_testgen_prompt(spec.builder_input.problem_statement, sources)
    response = call_with_retry(
        client, model=SKEPTIC_MODEL, max_tokens=16000, system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}], trace=trace,
        stage="VERIFY", actor="checks.t2_advtests",
    )
    blocks = parse_candidates(
        response_text(response), spec.verification.adversarial_tests.n_candidates
    )
    allowed_packages = _allowed_packages(spec)

    candidates: list[AdvCandidate] = []
    for i, source in enumerate(blocks, start=1):
        candidate_id = f"c{i}"
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
    return tuple(candidates)
