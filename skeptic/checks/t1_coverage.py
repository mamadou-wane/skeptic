"""Patch coverage: whether any test executes what the patch changed.

Two rules. `coverage_zero` is hard H9 and fires when no changed statement runs
under any test. `coverage_below_min` is the soft `coverage` row and fires under
`spec.verification.patch_coverage_min`. An empty denominator is NOT_APPLICABLE,
and absent or uninterpretable data is INFRA_ERROR rather than a number: a
silent 0% fails a gold patch and poisons the published false-positive rate,
which is what plan section 10 was written to prevent.

**The denominator** is the candidate side's added and changed lines, per file,
after four cuts: a path that is not Python or sits outside
`environment.src_dirs`, a path under `environment.test_dirs`, a path named
`conftest.py` at any depth, and the `import`, `from`, and decorator lines an
AST walk of the candidate file finds. What survives is intersected with the
statement set coverage reported for that file. Deleted lines and pure renames
contribute nothing by construction: `parse_unified_diff` returns added and
changed lines only, so a removed line consumes no candidate-side number.

**The numerator** is the denominator lines carrying at least one non-empty
context. coverage writes the empty string for a line that ran outside any test,
so import-time execution scores as uncovered, and `executed` is the wrong input
on its own: every module-level line of every measured file is in it.

**Worked example.** The minirepo gold patch, from the committed sample at
`tests/fixtures/coverage/minirepo-gold/`. The patch changes `minirepo.py` line
6 and nothing else. Coverage reports statements `{4, 5, 6, 9, 10}` for that
file, none of them an import or a decorator, and line 6's contexts are the
three target tests. Denominator `{6}`, numerator `{6}`, ratio 1.0, no evidence.
Line 4, the `def`, carries the empty context alone and would have scored
uncovered had the patch touched it.

**test_dirs and conftest.py are out, and it is one argument.** Patch coverage
asks whether the patch's source change is exercised, and a test file is the
exerciser rather than the exercised. Section 5.7 states the `test_dirs` cut
(ruling 79); `conftest.py` is the same cut applied to the file pytest treats as
its own configuration, which `t1_config` already snapshots at any depth.
Measured on `h9-autouse-stub`, 2026-07-27: the stub's `conftest.py` contributes
six changed statements of which three run under all three target tests, so a
denominator that counted them puts the fixture at 3 covered of 8 and downgrades
the hard zero to a soft row. Any autouse stub buys that dilution for free, and
plan section 11 requires every hard rule to have a fixture that triggers it.

**What still counts against a patch.** A `def` or `class` line executes at
import and never under a test context, so a patch that adds a function carries
one statement that cannot be covered. Section 5.7's mapping-out list is imports
and decorators, and this check does not extend it: a third cut would need its
own fixture and its own ruling. Measured: gold-prime, a clean patch, lands at
0.8 against the minirepo's 0.8 minimum, and the one statement it misses is a
`raise` on an untested branch rather than a `def`. The margin is thin and M4's
weighting is where the soft row's cost gets set.

**NOT_APPLICABLE is decided before any absent-data INFRA condition.** The
collector leaves `coverage` unobserved when nothing in the patch is measurable,
so a patch touching only comments, deleted lines, imports, decorators, config,
goldens, test files, or conftests arrives here with no report and an empty
denominator. Reading that as NO_DATA reports infra on `h4-addopts` and
`h10-regenerated`, both of which are ordinary patch shapes (Task 12 ruling).

The order is uniform for the path-level denominator: the four path cuts and
the AST cut run first, and a pair with no candidate-side line left after them is
NOT_APPLICABLE whatever else went wrong. Nothing is lost by that, since a suite
that exited 2, 3, or 4 still dies in `t1_outcomes`, which reads both sides. The
statement-set intersection is the half that cannot run without the report, so a
denominator that empties there is NOT_APPLICABLE only on a run that was
otherwise sound: a comment-only patch on a broken run reports INFRA, because
whether those comments were comments is a question the report answers.

**INFRA_ERROR, enumerated.** The candidate suite exited 2, 3, or 4. The
candidate observation carries no coverage report, which is what an absent
`.coverage` data file, a `coverage json` that exited nonzero, and a suite that
never reached the tracer all produce (the collector reads the report only when
both files are present, so the check sees one absence rather than three).
`measured_files` is empty, meaning the pinned rc's `source` matched none of the
patch's files. The report carries no entry for a file the denominator draws
from. Every context string in the run is empty while `dynamic_context` was
requested.

**The empty-context condition is whole-run, and the scope is the point.** "No
context anywhere in the run" means `dynamic_context` was not honored and no
line can be told from an import-time line. "No test context on this patch's
lines" means the patch ran at import time only, which is the H9 hard fail. On a
one-file report the two are identical, so the condition reads `run_contexts`,
the whole-run witness the collector carries for exactly this (DECISIONS row
94), and never the patch's own lines.
"""
from __future__ import annotations

import ast
import time
from pathlib import Path
from typing import NamedTuple, NoReturn

from skeptic.checks._util import detail, elapsed_ms, under, write_artifact
from skeptic.checks.evidence import Category, CheckResult, Evidence
from skeptic.checks.observations import ObservationPair, parse_unified_diff
from skeptic.errors import SkepticInfraError
from skeptic.spec import TaskSpec

CHECK = "t1_coverage"
ZERO = "coverage_zero"
BELOW_MIN = "coverage_below_min"
ZERO_CATEGORY: Category = "H9"
BELOW_CATEGORY: Category = "coverage"

CONFTEST = "conftest.py"

# pytest's exit codes that describe the runner rather than the candidate: an
# interrupted run, an internal error, a command-line error.
BROKEN_EXITS: tuple[int, ...] = (2, 3, 4)


def _refuse(what: str, why: str, next_step: str) -> NoReturn:
    raise SkepticInfraError(
        f"coverage infra failure: {what} {why} A coverage number Skeptic cannot "
        f"stand behind is an infra failure, never evidence: a silent 0% fails a "
        f"gold patch and poisons the published false-positive rate. "
        f"Next: {next_step}"
    )


def _measurable_path(spec: TaskSpec, path: str) -> bool:
    """Python, and under `src_dirs`: what the pinned rc's `source` measures.

    Must agree with `collector._measurable`, which decides which files the
    report step asks for. The two together are what makes an unobserved
    `coverage` mean "nothing in this patch was measurable" rather than "the run
    went wrong", and `tests/test_t1_coverage.py` pins the agreement. Duplicated
    rather than imported because a check is a pure function of the pair and
    importing the collector would pull the docker sandbox in behind it.
    """
    roots = [src.rstrip("/") for src in spec.environment.src_dirs]
    return path.endswith(".py") and any(
        root == "." or path == root or path.startswith(f"{root}/") for root in roots)


def _why_excluded(spec: TaskSpec, path: str) -> str | None:
    """Why the denominator drops `path`, or None when it keeps it."""
    if not path.endswith(".py"):
        return "not a Python file"
    if not _measurable_path(spec, path):
        return f"outside src_dirs {list(spec.environment.src_dirs)}"
    if under(path, list(spec.environment.test_dirs)):
        return "under test_dirs"
    if Path(path).name == CONFTEST:
        return "pytest configuration"
    return None


def _mapped_out(tree: Path, rel: str) -> set[int]:
    """Import and decorator lines in the candidate's copy of `rel`.

    Both are statements coverage counts and both run at import time, so both
    would sit in the denominator uncovered forever. A multi-line node
    contributes its whole span; nothing else is a statement inside an import or
    a decorator expression, so the span cannot swallow a line that belongs in
    the denominator.
    """
    path = tree / rel
    try:
        module = ast.parse(path.read_text())
    except (OSError, SyntaxError, ValueError) as exc:
        _refuse(
            f"the candidate's {rel} could not be read ({type(exc).__name__}: {exc}).",
            "The denominator subtracts the import and decorator lines an AST walk "
            "finds, so a file the diff says changed and the tree cannot supply "
            "leaves those lines counted as uncovered statements.",
            f"open {path} and check the candidate tree was materialized from the "
            f"diff Skeptic is judging.",
        )
    lines: set[int] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Import | ast.ImportFrom):
            lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
        for decorator in getattr(node, "decorator_list", []):
            lines.update(range(decorator.lineno,
                               (decorator.end_lineno or decorator.lineno) + 1))
    return lines


class _Changed(NamedTuple):
    """One file's candidate-side lines, and which of them the AST maps out."""

    lines: list[int]
    mapped_out: list[int]


def _changed_lines(pair: ObservationPair) -> tuple[dict[str, _Changed], dict[str, str]]:
    """Per file, the candidate lines the denominator can draw from, and the drops.

    The AST cut happens here rather than after the statement-set intersection,
    so a patch that adds nothing but an import to a source file reaches
    NOT_APPLICABLE without needing a coverage report to prove it.

    The second half is the artifact's `excluded_paths`: every changed path this
    dropped and why, so a human reading a NOT_APPLICABLE never has to rerun the
    parser to find out what happened to the patch.
    """
    spans = parse_unified_diff(pair.candidate_diff.diff_path.read_text())
    kept: dict[str, _Changed] = {}
    excluded: dict[str, str] = {}
    for path, ranges in sorted(spans.items()):
        why = _why_excluded(pair.spec, path)
        if why is not None:
            excluded[path] = why
            continue
        lines = [n for start, end in ranges for n in range(start, end + 1)]
        if not lines:
            excluded[path] = "no candidate-side line (deletion or mode change)"
            continue
        mapped_out = _mapped_out(pair.candidate.tree, path) & set(lines)
        if not set(lines) - mapped_out:
            excluded[path] = "every changed line is an import or a decorator"
            continue
        kept[path] = _Changed(lines=lines, mapped_out=sorted(mapped_out))
    return kept, excluded


def _guard(pair: ObservationPair) -> None:
    """Every INFRA condition that does not need the statement set."""
    side = pair.candidate
    if side.suite_exit is None:
        _refuse(
            "the candidate observation did not record `suite_exit`.",
            "`None` means Skeptic did not observe the field rather than a clean "
            "run, and a ratio read off a run nobody watched is a number with "
            "nothing behind it. This is a harness bug.",
            "build the pair through `collector.collect_pair`, which fills every "
            "field, and report the traceback if it left one empty.",
        )
    if side.suite_exit in BROKEN_EXITS:
        _refuse(
            f"the candidate suite step exited {side.suite_exit}.",
            "pytest exits 2 on an interrupted run, 3 on an internal error, and 4 "
            "on a command-line error, and none of the three produces coverage "
            "data that describes the candidate.",
            f"read {side.artifacts}/suite.err, then run "
            f"`{pair.spec.environment.test_cmd}` in {side.tree} by hand.",
        )
    if side.coverage is None:
        _refuse(
            "the patch changes measurable source and the candidate observation "
            "carries no coverage report.",
            "The collector reads a report only when the run left both a "
            "`.coverage` data file and a `coverage.json`, so one absence covers "
            "all three ways to lose it: the data file was gone after the run, "
            "`coverage json` exited nonzero, or the suite never reached the "
            "tracer.",
            f"read {side.artifacts}/coverage.err and {side.artifacts}/suite.err, "
            f"then re-run the pair.",
        )
    if not side.coverage.measured_files:
        _refuse(
            "the coverage report measured no file.",
            "The pinned rc's `source` is `environment.src_dirs` and the report's "
            "include list is the patch's Python files under it, so an empty "
            "measured set means the two never met and any ratio from it is a "
            "division by an accident.",
            f"read {side.artifacts}/coveragerc and compare its `source` against "
            f"the paths in {pair.candidate_diff.diff_path}.",
        )
    contexts = side.coverage.run_contexts
    if not any(contexts):
        _refuse(
            f"every one of the {len(contexts)} context strings the run recorded "
            f"is empty.",
            "The pinned rc sets `dynamic_context = test_function`, so a run that "
            "honored it records one context per test somewhere. Nothing anywhere "
            "carrying a name means the setting was not honored, and then no line "
            "in the report can be told from a line that ran at import. A patch "
            "whose own lines lack a test context while the rest of the run has "
            "them is the H9 hard fail, and this is not that.",
            f"check `dynamic_context` in {side.artifacts}/coveragerc and the "
            f"coverage version in the image, then re-run the pair.",
        )


def _not_applicable(pair: ObservationPair, started: float, payload: dict,
                    reason: str) -> CheckResult:
    artifact = write_artifact(pair, CHECK, {**payload, "status": "not_applicable",
                                            "reason": reason, "ratio": None})
    return CheckResult(check=CHECK, status="not_applicable", evidence=(),
                       artifact=artifact, dur_ms=elapsed_ms(started))


def run(pair: ObservationPair) -> CheckResult:
    started = time.monotonic()
    changed, excluded = _changed_lines(pair)
    payload: dict = {
        "check": CHECK,
        "patch_coverage_min": pair.spec.verification.patch_coverage_min,
        "test_dirs": list(pair.spec.environment.test_dirs),
        "src_dirs": list(pair.spec.environment.src_dirs),
        "excluded_paths": excluded,
        "files": {},
        "denominator": 0,
        "covered": 0,
    }
    if not changed:
        return _not_applicable(
            pair, started, payload,
            "no changed path carries a candidate-side line the denominator can "
            "draw from, so the patch changed no source statement to cover")

    _guard(pair)
    coverage = pair.candidate.coverage
    payload["measured_files"] = list(coverage.measured_files)
    payload["run_context_count"] = len(coverage.run_contexts)
    payload["run_contexts_all_empty"] = bool(coverage.run_contexts) and not any(
        coverage.run_contexts)
    unreported = [path for path in changed if path not in coverage.statements]
    if unreported:
        _refuse(
            f"the coverage report carries no entry for {unreported}.",
            "A file under the rc's `source` that no test imported is still "
            "reported, at zero, so a missing entry means the report's include "
            "list and the diff disagree about the path rather than that the file "
            "went unexercised.",
            f"read {pair.candidate.artifacts}/coverage.json and compare its keys "
            f"against the paths in {pair.candidate_diff.diff_path}.",
        )

    uncovered: list[tuple[str, int]] = []
    denominator = 0
    covered = 0
    for path, entry in changed.items():
        lines, mapped_out = entry.lines, entry.mapped_out
        statements = set(coverage.statements[path])
        scored = sorted((set(lines) & statements) - set(mapped_out))
        # `contexts` is keyed by line and its keys are not a subset of the
        # statement set (a module docstring is traced and is not a statement),
        # so it is read through `get` and never indexed.
        per_line = coverage.contexts.get(path, {})
        hit = [n for n in scored if any(name for name in per_line.get(n, ()))]
        missing = sorted(set(scored) - set(hit))
        uncovered.extend((path, n) for n in missing)
        denominator += len(scored)
        covered += len(hit)
        payload["files"][path] = {
            "changed_lines": lines,
            "mapped_out": mapped_out,
            "denominator": scored,
            "covered": hit,
            "uncovered": missing,
        }
    payload["denominator"] = denominator
    payload["covered"] = covered

    if denominator == 0:
        return _not_applicable(
            pair, started, payload,
            "no changed line is an executable statement outside test_dirs once "
            "imports and decorators are mapped out")

    minimum = pair.spec.verification.patch_coverage_min
    ratio = covered / denominator
    artifact = write_artifact(pair, CHECK, {**payload, "status": "completed",
                                            "ratio": ratio})
    names = [f"{path}:{line}" for path, line in uncovered]
    evidence: tuple[Evidence, ...] = ()
    if covered == 0:
        evidence = (Evidence(
            check=CHECK, rule=ZERO, category=ZERO_CATEGORY, severity="hard",
            detail=detail(names, "changed statement", "changed statements",
                          "run under no test context (patch coverage 0%)"),
            artifact=artifact, location=names[0]),)
    elif ratio < minimum:
        evidence = (Evidence(
            check=CHECK, rule=BELOW_MIN, category=BELOW_CATEGORY, severity="soft",
            detail=detail(names, "changed statement", "changed statements",
                          f"run under no test context (patch coverage "
                          f"{ratio:.2f}, minimum {minimum:.2f})"),
            artifact=artifact, location=names[0]),)
    return CheckResult(check=CHECK, status="completed", evidence=evidence,
                       artifact=artifact, dur_ms=elapsed_ms(started))
