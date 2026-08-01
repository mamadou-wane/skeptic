"""What a check is handed: two observed variants, and the parsers behind them.

Every T1 check reads an `ObservationPair` and returns a `CheckResult`. The
collector (Task 10) runs things and fills the pair; everything here is pure, so
a check test builds a pair by hand and never starts a container.

Unobserved is `None`. Every execution-derived field is `X | None`, and `None`
means Skeptic did not observe it, never "the suite collected nothing" or "zero
tests failed". The pure checks are tested against pairs with no execution at
all, so a half-populated pair is normal input; a check that needs a field it
was not given raises `SkepticInfraError` naming itself as a harness bug rather
than reading the absence as evidence. `dropped_ro_subpaths` is the one
exception and says why in its own docstring.

Config snapshots are not here. Reading `pyproject.toml` and `conftest.py` off
a tree is pure file IO, so `t1_config` builds its own snapshots from
`pair.<side>.tree`. The collector's job is the things that execute, which is
what makes it the expensive, container-bound half. The research proposed
carrying a `ConfigSnapshot` on the observations; that would have made every
check test that never looks at config pay for building one.

Immutability is shallow. The models are frozen, so no check can rebind a
field, and Pydantic coerces `outcomes` to a plain dict, which a check could
still mutate in place. Nothing does. The frozen models exist to stop a check
from rewriting the pair it is judging, and they stop nothing deeper.

The xpass blind spot. A non-strict `xfail` added to an already-passing test
reports as plain `passed` and the test still collects, so `t1_outcomes` and
`t1_collect` both miss it and only `t1_ast` and `t1_scope` see the decorator.
That is why the taxonomy lists T1 AST as H3's secondary. Measured in
`tests/fixtures/pytest-output/minirepo-marks-junit.xml`: the xpassed testcase
carries no child element at all, so it is byte-identical to a plain pass in
the report `parse_junit` reads.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from skeptic.candidate import CandidateReport
from skeptic.errors import SkepticInfraError
from skeptic.spec import TaskSpec

Outcome = Literal["passed", "failed", "error", "skipped", "xfailed"]

Side = Literal["baseline", "candidate"]

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def parse_collect_manifest(text: str) -> tuple[str, ...]:
    """Read the nodeids out of `pytest --collect-only -q` stdout.

    The manifest is the run of lines before the first blank one. Everything
    after that blank line is terminal reporting, which is where all four
    measured summary forms sit: `4 tests collected in 0.00s`, `2/4 tests
    collected (2 deselected) in 0.00s`, `no tests collected (4 deselected) in
    0.00s`, and, under --continue-on-collection-errors, `4 tests collected, 1
    error in 0.01s`. So do the ERRORS section, the warnings summary, and the
    short test summary. Samples and their capturing commands:
    tests/fixtures/pytest-output/.

    A duplicate nodeid raises, matching `parse_junit`: a manifest Skeptic
    cannot trust must not become evidence, and a silently deduplicated one
    would understate what the tree collects.
    """
    nodeids: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            break
        if line in seen:
            raise SkepticInfraError(
                f"Duplicate nodeid {line!r} in the collection manifest. Skeptic "
                f"compares the baseline and candidate manifests as sets of "
                f"nodeids, and a duplicate means the manifest does not describe "
                f"what pytest will run. This is an infra failure, never "
                f"evidence. Next: run the collect command in the workspace by "
                f"hand and look for a test file collected twice (a duplicated "
                f"path argument, or two rootdirs)."
            )
        seen.add(line)
        nodeids.append(line)
    return tuple(nodeids)


def _strip_prefix(path: str) -> str:
    return path[2:] if path[:2] in ("a/", "b/") else path


def _spans(numbers: list[int]) -> tuple[tuple[int, int], ...]:
    """Group ascending line numbers into inclusive `(start, end)` ranges."""
    spans: list[tuple[int, int]] = []
    for n in numbers:
        if spans and n == spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], n)
        else:
            spans.append((n, n))
    return tuple(spans)


def parse_unified_diff(text: str) -> dict[str, tuple[tuple[int, int], ...]]:
    """Map each changed path to the candidate-side lines the diff adds or changes.

    Paths come from the `diff --git` line and either prefix order is accepted.
    The committed gold patches were produced by `git diff -R` and open
    `diff --git b/src/click/utils.py a/src/click/utils.py`, so a parser keying
    on `+++ b/` reads them as changing nothing at all: a check that emits no
    evidence because it saw no changed files looks exactly like a check that
    emits no evidence because the patch was clean.

    Ranges hold added and changed lines only, grouped into consecutive spans.
    The `@@` header supplies the absolute numbering and the hunk body says
    which of those lines the candidate wrote: click-0001-gold's one-line fix
    sits in a seven-line hunk and yields `((89, 89),)`. Reporting the header's
    range instead would hand `t1_coverage` a denominator six sevenths made of
    context, so a dead one-line fix whose neighbors execute would read as
    mostly covered (2026-07-26 ruling). A file that appears with no added
    lines (a pure deletion, a mode change, a binary payload) maps to an empty
    tuple, so the key set stays the changed-file set.

    Two shapes raise `SkepticInfraError` instead of returning something
    partial: a rename, where the two `diff --git` paths disagree and the
    caller has to decide what the candidate did; and a hunk with no
    `diff --git` above it, which is a diff dialect this parser cannot map to
    paths.
    """
    added: dict[str, list[int]] = {}
    current: str | None = None
    lineno = 0
    in_hunk = False
    for line in text.splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
            parts = line[len("diff --git "):].split(" ")
            if len(parts) != 2:
                raise SkepticInfraError(
                    f"Cannot read the two paths out of {line!r}. Skeptic splits "
                    f"the `diff --git` line on a space, which a path containing "
                    f"a space defeats. This is an infra failure, never "
                    f"evidence. Next: inspect the patch; a path with a space "
                    f"needs a dedicated parser before Skeptic can judge it."
                )
            left, right = (_strip_prefix(p) for p in parts)
            if left != right:
                raise SkepticInfraError(
                    f"Diff renames {left!r} to {right!r}. Skeptic maps changed "
                    f"lines to one path per file, and a rename is two paths "
                    f"with one history, so the caller has to say which side it "
                    f"is judging. This is an infra failure, never evidence. "
                    f"Next: handle the rename explicitly at the call site, or "
                    f"re-take the diff with --no-renames."
                )
            current = left
            added.setdefault(current, [])
            continue
        hunk = _HUNK.match(line)
        if hunk:
            if current is None:
                raise SkepticInfraError(
                    f"Hunk header {line!r} appears before any `diff --git` "
                    f"line. Skeptic takes paths from `diff --git`, so this "
                    f"patch has hunks it cannot attribute to a file. This is "
                    f"an infra failure, never evidence. Next: re-take the diff "
                    f"with `git diff`, which always writes the header."
                )
            lineno = int(hunk.group(1))
            in_hunk = True
            continue
        if not in_hunk:
            # Header lines sit between `diff --git` and the first `@@`, which
            # is why the walk starts at the hunk: `+++ b/src/click/utils.py`
            # begins with a plus and is not a candidate line.
            continue
        if line.startswith("+"):
            added[current].append(lineno)
            lineno += 1
        elif line.startswith(("-", "\\")):
            # A removed line consumes no candidate-side number, and `\ No
            # newline at end of file` annotates the line above it.
            continue
        elif line.startswith(" ") or not line:
            # Context. An empty line is context whose single leading space a
            # trailing-whitespace stripper ate.
            lineno += 1
        else:
            in_hunk = False
    return {path: _spans(numbers) for path, numbers in added.items()}


class CoverageReport(_Model):
    """Line coverage of one variant, scoped to the candidate's changed files.

    Defined here rather than with `t1_coverage`, because
    `VariantObservations` declares the field and a Pydantic model with an
    unresolvable annotation fails at class creation. `collector.read_coverage`
    is the only thing that builds one, and `t1_coverage` is the only thing
    that reads one.

    `contexts` is the per-line context list coverage.py records under dynamic
    contexts, which is how a check tells "this line ran under the target test"
    from "this line ran under some other test". Its line numbers are not a
    subset of `statements`: coverage traces a module docstring and leaves it
    out of the statement analysis, so a file's contexts can carry a line the
    statement set does not (measured on the committed sample,
    `tests/fixtures/coverage/minirepo-gold/`).

    Everything except `run_contexts` is scoped to the patch. Contexts are a
    per-line by per-test cross product, measured at 1.3 GB over click's suite
    in the M1 spike, so no run may carry them for every measured file.
    """

    model_config = ConfigDict(frozen=True)

    statements: Mapping[str, tuple[int, ...]]
    executed: Mapping[str, tuple[int, ...]]
    contexts: Mapping[str, Mapping[int, tuple[str, ...]]]
    measured_files: tuple[str, ...]
    """The changed files that carried coverage data, sorted.

    A subset of the changed files the collector scoped the report to, and it
    is a subset twice over: a path outside `src_dirs` is never reported on,
    and a path that was reported on but never imported carries no data. Empty
    means the pinned rc's `source` matched none of the patch's files.
    """

    run_contexts: tuple[str, ...]
    """Every distinct context string the whole run recorded, sorted.

    Whole-run where the rest of this model is per-patch, and that is the only
    reason it exists. `t1_coverage` has to tell two things apart that look
    identical inside one file: a `dynamic_context` that was never honored,
    where nothing anywhere in the run carries a context, and a patch that ran
    at import time only, where these lines carry the empty context while
    other tests carry theirs. The first is INFRA and the second is the H9
    hard fail, so reading `contexts` alone would turn a misconfigured rc into
    a FAIL on a gold patch.

    Carries the empty string when the run recorded import-time execution,
    which is almost always. `("",)` alone is the misconfiguration signal.
    """


MutantStatus = Literal["killed", "survived", "timeout", "invalid", "uncovered", "import_failed"]


class MutantRecord(_Model):
    """One mutant's disposition after a batch: never run, or run to an exit code.

    `tests_run` carries the nodeids `mutation.select_tests` resolved for a
    changed-population mutant, or the sentinel `("<full-suite>",)` for a
    caller-population mutant (the coverage report is scoped to changed files,
    so a caller line has no per-line context to select from). Empty for a
    mutant that never ran (`invalid` or `uncovered`).
    """

    model_config = ConfigDict(frozen=True)

    mutant_id: str
    path: str
    line: int
    operator: str
    population: Literal["changed", "caller"]
    status: MutantStatus
    tests_run: tuple[str, ...]
    dur_ms: int | None


class CalibrationVoid(_Model):
    """One `FULL_SUITE` selection whose own calibration run came back red.

    A caller-population mutant has no per-line coverage context to select
    tests from, so it always lands on `mutation.FULL_SUITE`
    (`("<full-suite>",)`). If that selection's unmutated-candidate timing run
    exits nonzero over an environmentally red full suite (a documented,
    permanent gap the candidate did not introduce, DECISIONS row 73), every
    mutant sampled onto it would read `killed` regardless of what it
    changed, a void result rather than evidence, so `observe_mutation`
    excludes those mutants from `MutationReport.records` entirely (DECISIONS
    row 119) instead of scoring them. A per-line
    selection's own nonzero calibration stays `SkepticInfraError`
    unconditionally; this model exists for `FULL_SUITE` alone.
    """

    model_config = ConfigDict(frozen=True)

    selection: tuple[str, ...]
    calibration_exit: int
    excluded_mutant_ids: tuple[str, ...]
    reason: str


class MutationReport(_Model):
    """One candidate's mutation batch: the config it ran under, plus every record.

    `generated` is the count of mutants this batch sampled
    (`sample_mutants`'s output count), which can be smaller than `budget`
    when the candidate's changed spans do not carry that many mutable sites.
    `records` covers every sampled mutant except the ones named in
    `calibration_void`'s `excluded_mutant_ids`, which never ran at all and
    carry no record of any status, existing or new: `len(records) +
    sum(len(v.excluded_mutant_ids) for v in calibration_void) == generated`.
    """

    model_config = ConfigDict(frozen=True)

    seed: int
    budget: int
    generated: int
    records: tuple[MutantRecord, ...]
    calibration_void: tuple[CalibrationVoid, ...] = ()


class ProbeCall(_Model):
    """One `consumer_probe` entrypoint's two readings: same call, two processes.

    `in_pytest` and `bare` are each `"value:" + repr(result)` or `"raised:" +
    type(exc).__name__`, never anything else: the driver
    (`collector.observe_probe`) never records a raw traceback, an argument, or
    any other entrypoint-controlled text into these two fields, which is what
    lets `t2_probe` compare them with plain string inequality. Equal strings
    is agreement, including the both-raised case (`"raised:ValueError"` on
    both sides), and unequal is `H8` divergence.
    """

    model_config = ConfigDict(frozen=True)

    call: str
    in_pytest: str
    bare: str


class ProbeReport(_Model):
    """One candidate's consumer-probe batch: one `ProbeCall` per spec entrypoint,
    in `spec.verification.consumer_probe.entrypoints` order."""

    model_config = ConfigDict(frozen=True)

    calls: tuple[ProbeCall, ...]


class VariantObservations(_Model):
    """One side of the comparison: what one tree collected, ran, and covered.

    Everything from `collected` through `coverage` is `None` when it was not
    observed. See the module docstring. `mutation` and `probe` are
    collector-side machinery recorded onto the candidate side only (Tasks 9
    and 10); the baseline is never mutation-tested or probed, so both stay
    `None` there by construction.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    side: Side
    tree: Path
    collected: tuple[str, ...] | None
    collect_exit: int | None
    outcomes: Mapping[str, str] | None
    collection_errors: int | None
    suite_exit: int | None
    coverage: CoverageReport | None
    artifacts: Path
    dropped_ro_subpaths: tuple[str, ...] = ()
    """Declared read-only paths the candidate tree could not supply.

    Defaulted rather than `| None`: empty means the container mounted
    everything the spec declared, which is also what a pair built with no
    execution carries, so the empty case needs no second spelling. `t1_collect`
    turns an entry here into `ro_subpath_deleted` evidence, which is why
    deleting a declared `test_dirs` or `config_files` path is a finding
    instead of an INFRA death (the 2026-07-26 ruling).

    Always empty on the baseline side, by construction: the baseline is the
    seeded tree, and a declared path missing there raises. A reader who sees
    the field on both sides would otherwise expect the baseline to fill it.
    """
    mutation: MutationReport | None = None
    probe: ProbeReport | None = None


class ObservationPair(_Model):
    """The baseline and the candidate, plus what a check needs to judge them.

    `candidate_diff` is the extracted patch (`skeptic.candidate`), which is
    what `t1_scope`, `t1_goldens`, and `t1_coverage` read instead of walking
    the two trees themselves.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    spec: TaskSpec
    baseline: VariantObservations
    candidate: VariantObservations
    candidate_diff: CandidateReport
    artifacts_dir: Path
