"""VERIFY's executing half: two fresh trees, one throwaway container each.

`collect_pair` is the only place VERIFY runs anything. It hands back one
`ObservationPair`, and every check downstream is a pure function over it.

Both trees are born from `git archive` plus patches. The Builder's workspace
is never copied: it carries `.sv`, junit files, and `__pycache__`, and the
bytecode is the part that bites. pytest writes the junit `file` attribute
from each module's `co_filename`, stale bytecode carries the path of the tree
it was compiled in, and `parse_junit` then raises on a classname it cannot
map to that file (measured at Task 5).

One fidelity gap, inherited from extraction. `candidate.extract_candidate`
strips `.gitattributes` and `.gitignore` from the copies it diffs, because a
planted `*.py -diff` attribute renders every changed file as an opaque binary
patch, and folds those two files' own changes back into `changed_files` by
byte comparison. A candidate that edited one of them therefore produces a
judged tree that is not byte-identical to BUILD's final tree in that one
respect. `t1_scope` still reports the edit.

No flake reruns at M3. The plan's rerun-before-flag belongs in this module
rather than in a check, because a rerun inside a check would stop the check
being pure. It lands with the quarantine work.

The scripts here are harness-composed, and two kinds of outside text reach
them. `spec.environment.test_cmd` is spec-authored, and validation already
restricts it to a plain argv with no shell metacharacters. The coverage
report step names the candidate's changed files, which the candidate chose:
every step is built as an argv and joined with `shlex.join`, so a path
carrying a space or a quote becomes one quoted word instead of shell syntax.
The artifact filenames are the constants below, and the candidate itself
arrives as a diff that `git apply` reads as a file.
"""
from __future__ import annotations

import hashlib
import json
import re
import shlex
import shutil
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from contextlib import closing
from pathlib import Path

from skeptic.candidate import CandidateReport
from skeptic.checks.observations import (
    CoverageReport,
    MutantRecord,
    MutantStatus,
    MutationReport,
    ObservationPair,
    Side,
    VariantObservations,
    parse_collect_manifest,
)
from skeptic.errors import SkepticInfraError
from skeptic.image import ensure_repo_image
from skeptic.mutation import FULL_SUITE, Mutant
from skeptic.sandbox import RunContainer
from skeptic.seedcheck import parse_junit
from skeptic.spec import TaskSpec
from skeptic.trace import config_hash
from skeptic.workspace import apply_candidate, apply_patch, materialize

# The artifacts directory mounts here, outside /workspace. Everything a unit
# produces is written through this path, so nothing the harness measures with
# lands inside the tree it is measuring.
ARTIFACTS = "/artifacts"
_INSTALL_OK = "install.ok"
_JUNIT = "junit.xml"
_DROPPED = "dropped-ro-subpaths.txt"
_RC = "coveragerc"
_COVERAGE_DATA = ".coverage"
_COVERAGE_JSON = "coverage.json"

# The mutation batch's own artifact layout, all under one batch's artifacts
# mount (Task 9): `originals/<path>` (the pre-mutation candidate source, one
# copy per distinct changed path), `mutants/<id>/<basename>` (one mutated
# source per mutant) plus its `selection.txt`, and `calibration/<key>/`
# (one timed baseline run per distinct selection set). See `observe_mutation`.
_MUT_ORIGINALS = "originals"
_MUT_MUTANTS = "mutants"
_MUT_CALIBRATION = "calibration"

# Timeout caps (DECISIONS row 112): 3x the calibration measurement, floor 5s,
# ceiling 60s. The ceiling doubles as the calibration run's own bound, since a
# hung baseline suite needs a cap too.
_MUT_CAP_FLOOR_S = 5
_MUT_CAP_CEILING_S = 60

# The host-side `container.run` timeout: the worst case of every mutant and
# every calibration run individually hitting the ceiling, plus slack for the
# overlay install and the file copies. Independent of `spec.environment.
# timeout_s`, which bounds the T1 unit's own suite run, not this batch.
_MUT_SLACK_S = 120

# Bumped by hand when observe_variant's or read_variant's behavior changes in
# a way that makes an old baseline observation wrong to reuse: a new field
# read off the artifacts, a changed script, a changed exit-code contract.
# This is the baseline-observation half of the two-key design
# (`skeptic.orchestrator.verifier_revision` is the VERIFY-verdict half): a
# detector edit never touches this constant and re-verdicts through
# verifier_revision with no re-collection, while a collector behavior change
# needs this bumped by hand to invalidate a baseline cached under the old
# behavior. Precedent: `skeptic.builder.GREEN_RULE_VERSION`.
COLLECTOR_VERSION = "1"

# `-q`, `-qq`, `-v`, `-vv`: pytest counts these, so they compose.
_VERBOSITY = re.compile(r"^-[qv]+$")
_LONG_VERBOSITY = ("--quiet", "--verbose")


def _collect_argv(test_cmd: str) -> list[str]:
    """`--collect-only -q`, with the command's own verbosity flags removed.

    `parse_collect_manifest` reads the one-nodeid-per-line form pytest prints
    at verbosity -1. Appending `-q` to a `test_cmd` that already carries one
    gives verbosity -2, and pytest 9.1.1 then prints `path: count` aggregate
    lines instead (measured on the minirepo fixture, 2026-07-27), which the
    parser would read as four nodeids that no junit report can match.
    `--verbosity=N` sets the same counter outright and needs stripping for the
    same reason: an appended `-q` only decrements it, and at verbosity 0 the
    session header becomes phantom manifest lines. Its separated form carries
    its value in the next token, which pytest would otherwise collect as a
    path. Selection flags stay: a `-k` that shapes the suite has to shape the
    manifest too, or the two id spaces below cannot be compared.
    """
    argv: list[str] = []
    tokens = iter(shlex.split(test_cmd))
    for token in tokens:
        if _VERBOSITY.match(token) or token in _LONG_VERBOSITY:
            continue
        if token == "--verbosity":
            next(tokens, None)
            continue
        if token.startswith("--verbosity="):
            continue
        argv.append(token)
    return argv + ["--collect-only", "-q", "--continue-on-collection-errors"]


def coverage_test_cmd(test_cmd: str) -> list[str]:
    """`python -m pytest ...` rewritten to run under coverage.

    Only the leading `python -m` moves. A `-m` further along is pytest's
    marker selector and stays a pytest argument, since rewriting it would
    hand coverage a module name it cannot import.

    `python` is the overlay venv's interpreter at /tmp/sv/bin/python and
    `coverage` comes from the image's frozen closure, which that venv reaches
    through `--system-site-packages`. There is no /tmp/sv/bin/coverage, so
    `python -m coverage` is the spelling that resolves.

    Anything else refuses. `spec.py` already guarantees `test_cmd` is a plain
    argv with no shell metacharacters, so what is left open is which runner it
    names, and Skeptic knows one instrumented spelling.
    """
    argv = shlex.split(test_cmd)
    if argv[:3] != ["python", "-m", "pytest"]:
        raise SkepticInfraError(
            f"environment.test_cmd {test_cmd!r} does not start with "
            f"`python -m pytest`, so Skeptic cannot say what its "
            f"coverage-instrumented form is. T1's patch-coverage evidence "
            f"comes from running the suite under `python -m coverage run`, "
            f"and a guessed rewrite produces a coverage number instead of a "
            f"refusal. This is an infra failure, never evidence. Next: write "
            f"the task's test_cmd as `python -m pytest ...`, or teach "
            f"`collector.coverage_test_cmd` this runner's instrumented form."
        )
    return ["python", "-m", "coverage", "run", "-m", "pytest", *argv[3:]]


def render_coverage_rc(spec: TaskSpec, data_file: str) -> str:
    """The one coverage configuration an instrumented run is allowed to read.

    coverage.py discovers config from the tree it is measuring, and both
    corpus repos carry one: click's `pyproject.toml` sets
    `[tool.coverage.run] branch = true` with `source = ["click", "tests"]`,
    and rich ships a root `.coveragerc` whose omit list drops four modules.
    Either would silently redefine what T1 measures. This file overrides both
    through `COVERAGE_RCFILE`, which is the only mechanism in play: no
    `--rcfile` is passed anywhere, so one pin governs the run and the report
    that reads its data back.

    Every setting is written rather than defaulted. `source` comes from
    `spec.environment.src_dirs`, `branch` is off because M3 scores statements,
    `dynamic_context = test_function` is what lets Task 13 tell a line that
    ran under a test from one that ran at import time, and `data_file` is the
    caller's, which puts it outside the judged tree.

    `relative_files` makes the report's file keys the diff's paths: the
    container works in /workspace, so the JSON says `minirepo.py` where
    `parse_unified_diff` says `minirepo.py`. Relative `source` entries
    resolve against the working directory rather than against this file's
    directory (measured with coverage 7.15.2, 2026-07-27), which is why an rc
    that lives on the artifacts mount can name `.` and mean the tree.
    """
    sources = "".join(f"    {src.rstrip('/')}\n" for src in spec.environment.src_dirs)
    return (
        "[run]\n"
        f"data_file = {data_file}\n"
        f"source =\n{sources}"
        "branch = false\n"
        "dynamic_context = test_function\n"
        "relative_files = true\n"
    )


def _suite_argv(spec: TaskSpec) -> list[str]:
    """The instrumented suite, with junit written outside the judged tree.

    `builder_tools._suite_argv` is BUILD's and stays BUILD's: it writes junit
    into the workspace, which is right there and wrong here, and it stays
    plain while this one runs under coverage. The flag both have to carry is
    `--continue-on-collection-errors` (DECISIONS row 78), so a candidate that
    broke one import is still observed instead of erasing the run.

    One run produces both readings. The junit report and the coverage data
    come out of the same command, so nothing has to make two observations of
    one tree agree.
    """
    return [*coverage_test_cmd(spec.environment.test_cmd),
            "--continue-on-collection-errors",
            f"--junitxml={ARTIFACTS}/{_JUNIT}", "-o", "junit_family=xunit1"]


def _report_argv(changed_py: list[str]) -> list[str]:
    """The scoped context report, written next to the data file.

    `--include` is the whole cost control. Contexts are a per-line by per-test
    cross product, and the M1 spike measured `coverage json --show-contexts`
    over click's full suite at 1.3 GB, so the patch's own files are the only
    scope this is ever run at.

    coverage splits the pattern list on commas, so a candidate that renamed a
    file to one containing a comma gets two patterns that match nothing and no
    entry in the report. That reaches Task 13 as a changed file with no
    coverage data, which is one of its enumerated INFRA conditions rather than
    a number that is quietly wrong.
    """
    return ["python", "-m", "coverage", "json", "--show-contexts",
            f"--include={','.join(changed_py)}",
            "-o", f"{ARTIFACTS}/{_COVERAGE_JSON}"]


def _measurable(spec: TaskSpec, changed_files: Sequence[str]) -> list[str]:
    """The changed files coverage could measure: Python, under `src_dirs`.

    Both halves of that are the rc's. `source` is `spec.environment.src_dirs`,
    so a changed file outside those directories is never in the data however
    thoroughly the suite ran it, and coverage measures Python.

    The filter is what makes an absent report mean one thing. Without it a
    patch that touched only `tests/` on a repo whose `src_dirs` is `src/click`
    would ask for a report, get "No data to report", and leave `t1_coverage`
    unable to tell "nothing here is measurable" from "the run went wrong".

    The minirepo's `src_dirs` is `["."]`, so nothing is ever dropped there and
    no M3 fixture exercises the filter. click's is `["src/click/"]`, which is
    where it has teeth: `.` matches everything by construction and a real
    source directory does not.
    """
    roots = [src.rstrip("/") for src in spec.environment.src_dirs]
    return [path for path in changed_files
            if path.endswith(".py")
            and any(root == "." or path == root or path.startswith(f"{root}/")
                    for root in roots)]


def _unit_steps(spec: TaskSpec,
                changed_files: Sequence[str]) -> list[tuple[str, list[str]]]:
    """The unit's steps in order: collect-only, the suite, the report.

    Collection stays uninstrumented. It imports the test modules and runs no
    test, so measuring it would add tracer time to a step whose only product
    is a list of nodeids.

    The report step is dropped when nothing in the patch is measurable.
    `coverage json` over an `--include` list that matches nothing exits 1 with
    "No data to report" (measured with coverage 7.15.2, 2026-07-27), and a
    patch that changed only a golden or a config file has no statements to
    score either way. `h4-addopts` and `h10-regenerated` are both that shape.
    """
    changed_py = _measurable(spec, changed_files)
    steps = [("collect", _collect_argv(spec.environment.test_cmd)),
             ("suite", _suite_argv(spec))]
    if changed_py:
        steps.append(("coverage", _report_argv(changed_py)))
    return steps


def _unit_script(spec: TaskSpec, changed_files: Sequence[str]) -> str:
    """One script per unit, one file per step's exit code, stdout, and stderr.

    The separate files are what let the host recover each step independently,
    so a failure in one is never read as a failure in another. `install.ok` is
    written first, inside the brace group `RunContainer.run` guards with the
    overlay install, so its absence after a nonzero run means the install
    failed rather than any step. No step stops the next: a candidate whose
    collect exits 5 still runs the suite, both empties become the same
    observation, and a report step that finds no data leaves the coverage
    field unobserved instead of failing the unit.
    """
    lines = [f"echo ok > {ARTIFACTS}/{_INSTALL_OK}"]
    for step, argv in _unit_steps(spec, changed_files):
        lines.append(f"{shlex.join(argv)} > {ARTIFACTS}/{step}.out "
                     f"2> {ARTIFACTS}/{step}.err")
        lines.append(f"echo $? > {ARTIFACTS}/{step}.exit")
    return "\n".join(lines)


def read_coverage(artifacts: Path, changed_files: Iterable[str]) -> CoverageReport:
    """One variant's report: the scoped JSON, plus the run's context list.

    Two routes lead to the per-file half and the size of the unscoped answer
    picks one. The M1 spike measured `coverage json --show-contexts` over
    click's full suite at 1.3 GB, so no run may dump contexts unscoped. The
    unit therefore reports with `--include` set to the patch's measurable
    files, which bounds the cross product by the patch, and this function
    reads that JSON rather than the `.coverage` SQLite next to it. The
    database would serve the executed lines and the contexts equally well and
    would not serve the statement set: the data file records what ran, and
    what could have run comes from parsing the source, which lives in the
    container.

    `run_contexts` is the one thing the JSON cannot answer, because answering
    it there means reporting every measured file's contexts, which is the
    1.3 GB shape. `select context from context` on the data file is the whole
    query: one row per distinct context name in the run, no source tree, no
    numbits decoding, stdlib sqlite3. `t1_coverage` reads it to tell a
    `dynamic_context` that was never honored from a patch that ran only at
    import time, and the alternative was a check reaching past the model into
    the artifacts directory.

    `changed_files` scopes the per-file half a second time. The include list
    is identical on both sides, so the baseline's report is measured against
    the candidate's paths, and a caller asking for a subset gets a subset.
    The include list is already the narrower bound: it drops paths outside
    `src_dirs`, which this function has no spec to check.

    Empty context strings survive. coverage writes `""` for a line that ran
    outside any test, which is Task 13's import-time signal, and a read that
    dropped the empty string would score an import-time-only patch as covered.
    The context line numbers are not a subset of the statement set either: a
    module docstring is traced and is not a statement, so line 1 of the
    committed sample carries a context and appears in no statement list.
    """
    wanted = set(changed_files)
    data = json.loads((artifacts / _COVERAGE_JSON).read_text())
    statements: dict[str, tuple[int, ...]] = {}
    executed: dict[str, tuple[int, ...]] = {}
    contexts: dict[str, dict[int, tuple[str, ...]]] = {}
    for path, entry in sorted(data["files"].items()):
        if path not in wanted:
            continue
        ran = tuple(sorted(entry["executed_lines"]))
        statements[path] = tuple(sorted(set(ran) | set(entry["missing_lines"])))
        executed[path] = ran
        contexts[path] = {int(line): tuple(names)
                          for line, names in sorted(entry.get("contexts", {}).items(),
                                                    key=lambda item: int(item[0]))}
    with closing(sqlite3.connect(artifacts / _COVERAGE_DATA)) as data_file:
        recorded = data_file.execute("select context from context").fetchall()
    return CoverageReport(statements=statements, executed=executed,
                          contexts=contexts, measured_files=tuple(statements),
                          run_contexts=tuple(sorted({row[0] for row in recorded})))


def _read_exit(artifacts: Path, step: str) -> int:
    path = artifacts / f"{step}.exit"
    if not path.is_file():
        raise SkepticInfraError(
            f"The {step} step left no exit code at {path}. The unit script "
            f"records one after every step, so an absent file means the "
            f"container stopped mid-script (the daemon killed it, or the host "
            f"filled up). This is an infra failure, never evidence. Next: read "
            f"{artifacts}/{step}.err, then re-run the pair."
        )
    raw = path.read_text().strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise SkepticInfraError(
            f"The {step} step's exit file {path} holds {raw[:40]!r} where an "
            f"exit code belongs. `echo $? >` writes one integer, so a partial "
            f"or empty file means the container stopped while writing it. This "
            f"is an infra failure, never evidence. Next: read "
            f"{artifacts}/{step}.err, then re-run the pair."
        ) from exc


def _guard_exit(spec: TaskSpec, side: Side, step: str, exit_code: int,
                tree: Path, artifacts: Path) -> None:
    """The VERIFY exit-code contract, which is side-specific at 5.

    `(0, 1)` is a run. `5` on the candidate is an observation: total collection
    shrinkage is the maximal H1, and a check that dies on its own worst case is
    not a check. `5` on the baseline is broken substrate. Everything else, on
    either side, is infra. `seedcheck.run_suite` raises on 5 from both sides
    and keeps doing so: admission asks whether the repo is sound, VERIFY asks
    what the candidate did.
    """
    if exit_code in (0, 1) or (exit_code == 5 and side == "candidate"):
        return
    if exit_code == 5:
        why = ("pytest exits 5 when it collected no tests. A seeded tree that "
               "collects nothing cannot say what the candidate changed, so the "
               "run stops here; on the candidate side the same code is an "
               "observation rather than a failure.")
    else:
        why = ("pytest exits 2 on an interrupted run, 3 on an internal error, "
               "and 4 on a command-line error. None of the three is a "
               "statement about the candidate.")
    raise SkepticInfraError(
        f"The {side} {step} step exited {exit_code}. {why} This is an infra "
        f"failure, never evidence. Next: read {artifacts}/{step}.err and "
        f"{artifacts}/{step}.out, then run `{spec.environment.test_cmd}` in "
        f"{tree} by hand."
    )


def _cross_check(side: Side, collected: tuple[str, ...],
                 outcomes: dict[str, str], artifacts: Path) -> None:
    """Every nodeid in the outcome map has to appear in the collected set.

    The two come from different derivations. The manifest is pytest printing
    its own nodeids; the outcome map is reconstructed from junit's `file`,
    `classname`, and `name` (`seedcheck.py:82`). They can disagree on
    class-based tests, on setup errors, and on plugin-rewritten classnames,
    and a disagreement produces either a phantom `collect_shrinkage` or a
    silently dropped `outcome_flip`. Collection-failure entries are already
    out of the outcome map (Task 5) and deselected tests are in neither.
    """
    divergent = sorted(set(outcomes) - set(collected))
    if not divergent:
        return
    shown = divergent[:10]
    raise SkepticInfraError(
        f"The {side} junit report names {len(divergent)} test id(s) that its "
        f"collection manifest does not: {shown}. Skeptic compares the two id "
        f"spaces so a disappeared test cannot read as a renamed one, and an id "
        f"that exists in one and not the other makes both comparisons wrong. "
        f"This is an infra failure, never evidence. Next: read "
        f"{artifacts}/collect.out against {artifacts}/{_JUNIT}; a plugin that "
        f"rewrites classnames needs its own mapping before this repo can be "
        f"judged."
    )


def observe_variant(spec: TaskSpec, image_tag: str, tree: Path, artifacts: Path,
                    side: Side, changed_files: Sequence[str]) -> VariantObservations:
    """Run one tree in one throwaway container, then read back what it produced.

    The missing-mount policy is set here and it is side-specific. The baseline
    takes `RunContainer`'s strict default, because that tree is `git archive`
    plus the seed patch and a declared path missing from it is an authoring
    fault. The candidate takes `missing_ro="drop"`, because there the absent
    path is the hack, and the dropped list leaves on the observation as well as
    in the artifacts: the file is what a human reads, and only the field can
    become `ro_subpath_deleted` evidence (DECISIONS row 91).

    `changed_files` is the candidate's, on both sides. It scopes the coverage
    report and nothing else, and the two sides run the same command either
    way (`_suite_argv` reads the spec), so the argv symmetry the differential
    checks stand on holds. It is read three times (the script, the timeout
    diagnosis, and the report), so it is materialized on entry: a generator
    would build a script naming files that the diagnosis then says nothing
    about.
    """
    changed_files = tuple(changed_files)
    # Same reuse policy as the two trees in `collect_pair`: this directory is
    # rebuilt, never topped up. A workdir reused across runs would otherwise
    # keep the previous run's `install.ok`, exit files, and junit report, and
    # a unit that died before writing anything would then be read back as a
    # complete, self-consistent observation of the run before it.
    if artifacts.exists():
        shutil.rmtree(artifacts)
    artifacts.mkdir(parents=True)
    ro = (tuple(spec.environment.test_dirs)
          + tuple(spec.environment.config_files)
          + tuple(spec.environment.golden_dirs))
    container = RunContainer(
        image_tag, tree, ro_subpaths=ro,
        extra_mounts=((artifacts, ARTIFACTS, "rw"),),
        missing_ro="drop" if side == "candidate" else "raise",
    )
    # Written before the run, so the human-readable half survives a unit that
    # dies partway.
    (artifacts / _DROPPED).write_text(
        "".join(f"{path}\n" for path in container.dropped_ro_subpaths))
    # The pin sits on the artifacts mount, which is already rw and already
    # outside the judged tree, so it needs no mount of its own and changes
    # nothing under measurement.
    (artifacts / _RC).write_text(
        render_coverage_rc(spec, f"{ARTIFACTS}/{_COVERAGE_DATA}"))
    steps = [step for step, _ in _unit_steps(spec, changed_files)]
    # One budget for the whole unit: the overlay install, the collect-only
    # pass, the instrumented suite, and the report. A timeout is infra on
    # both sides.
    result = container.run(_unit_script(spec, changed_files),
                           timeout_s=spec.environment.timeout_s,
                           env={"COVERAGE_RCFILE": f"{ARTIFACTS}/{_RC}"})
    if result.exit_code == -1:
        # Which exit files exist says where the budget went. The stderr tail
        # does not: on this path it is the harness's own timeout sentence.
        done = [step for step in steps if (artifacts / f"{step}.exit").is_file()]
        if not done:
            where = ("No step recorded an exit code, so the time went to the "
                     "overlay install or the collect-only pass.")
        elif "suite" not in done:
            where = ("The collect step recorded an exit code and the suite did "
                     "not, so the suite is what hung.")
        elif done != steps:
            where = ("The suite recorded an exit code and the coverage report "
                     "did not, so writing the report is what hung.")
        else:
            where = ("Every step recorded an exit code, so the time went to "
                     "something after the last one: container teardown, or a "
                     "process the suite left behind.")
        raise SkepticInfraError(
            f"The {side} observation unit timed out after "
            f"{spec.environment.timeout_s}s. One unit is the overlay install, "
            f"a collect-only pass, and the instrumented suite, and the budget "
            f"for all of it is environment.timeout_s. {where} A partial run "
            f"says nothing about the candidate, so this is an infra failure, "
            f"never evidence. Next: raise environment.timeout_s in the task "
            f"spec, or "
            f"run `{spec.environment.test_cmd}` in {tree} by hand and time it."
        )
    if not (artifacts / _INSTALL_OK).is_file():
        raise SkepticInfraError(
            f"The {side} unit never reached its first step (container exit "
            f"{result.exit_code}), so the overlay install failed or the "
            f"container did not start. Skeptic installs the tree into a venv "
            f"at /tmp/sv before either pytest step, and a suite run without it "
            f"would import the image's frozen closure instead of the code "
            f"under judgment. Next: `docker run --rm {image_tag} true`, then "
            f"re-run the pair.\n"
            f"stderr tail:\n{result.stderr[-1500:]}"
        )
    return read_variant(spec, tree, artifacts, side, changed_files)


def read_variant(spec: TaskSpec, tree: Path, artifacts: Path, side: Side,
                 changed_files: Sequence[str]) -> VariantObservations:
    """The pure read-back half of `observe_variant`: no container, no run.

    Parses an artifacts directory `observe_variant` already wrote (the exit
    files, the collection manifest, the junit report, the coverage JSON and
    data file, `dropped-ro-subpaths.txt`) into one `VariantObservations`.
    This is what makes a baseline observation reusable: `collect_pair`'s
    `baseline_cache` skips the container on a cache hit and calls this
    directly against the previous run's artifacts instead.
    """
    changed_files = tuple(changed_files)
    collect_exit = _read_exit(artifacts, "collect")
    suite_exit = _read_exit(artifacts, "suite")
    _guard_exit(spec, side, "collect", collect_exit, tree, artifacts)
    _guard_exit(spec, side, "suite", suite_exit, tree, artifacts)
    # Exit 5 short-circuits both parsers on the contract alone. The files are
    # readable either way: pytest at exit 5 still writes a junit report with
    # zero testcases (measured with pytest 9.1.1, 2026-07-27), so the
    # short-circuit and a parse of that report agree.
    collected: tuple[str, ...] = ()
    if collect_exit != 5:
        collected = parse_collect_manifest((artifacts / "collect.out").read_text())
    outcomes: dict[str, str] = {}
    collection_errors = 0
    if suite_exit != 5:
        suite = parse_junit(artifacts / _JUNIT)
        outcomes, collection_errors = suite.outcomes, suite.collection_errors
    _cross_check(side, collected, outcomes, artifacts)
    # No report, no reading. `coverage json` writes its file only when it had
    # data, so an absent one means nothing in the patch was measurable, the
    # suite never reached the tracer, or the report step failed; all three
    # leave the field unobserved and Task 13 says what an unobserved one
    # means. Both files are checked because `read_coverage` reads both, and
    # sqlite3 would otherwise create the missing one as an empty database.
    coverage = None
    if all((artifacts / name).is_file() for name in (_COVERAGE_JSON, _COVERAGE_DATA)):
        coverage = read_coverage(artifacts, changed_files)
    # Written before the run (see observe_variant), one path per line, in the
    # sorted order RunContainer already put them in; reading it back is what
    # lets a rehydrated observation carry the same dropped-mount evidence a
    # freshly run one would.
    dropped_path = artifacts / _DROPPED
    dropped: tuple[str, ...] = ()
    if dropped_path.is_file():
        dropped = tuple(line for line in dropped_path.read_text().splitlines() if line)
    return VariantObservations(
        side=side, tree=tree, artifacts=artifacts, collected=collected,
        collect_exit=collect_exit, outcomes=outcomes,
        collection_errors=collection_errors, suite_exit=suite_exit,
        coverage=coverage, dropped_ro_subpaths=dropped,
    )


def _baseline_key(spec: TaskSpec, changed_files: Sequence[str]) -> str:
    """The `OBSERVE_BASELINE` cache key: every input that shapes the
    baseline's observation, `_build_cache_key`-style.

    `changed_files` is in the key because both sides' coverage report is
    scoped to the candidate's changed files (`observe_variant`'s docstring):
    two candidates against the same seed with different footprints need two
    baseline observations, even though the baseline tree itself is identical
    either way. The gap that leaves: a baseline observed for one candidate's
    changed-files scope is never reused for a different candidate against the
    same seed, even when everything but the coverage report would be
    identical, because scoping the whole observation on `changed_files`
    trades that reuse for a key that can never serve a stale coverage report.
    `COLLECTOR_VERSION` is the module docstring's constant: bumped by hand
    when this function's caller's behavior changes underneath it.
    """
    seed_sha = hashlib.sha256(Path(spec.seed.bug_patch).read_bytes()).hexdigest()
    return config_hash({
        "stage": "OBSERVE_BASELINE",
        "task": spec.task_id,
        "commit": spec.repo.commit,
        "seed": seed_sha,
        "environment": spec.environment.model_dump(),
        "changed_files": sorted(changed_files),
        "collector_version": COLLECTOR_VERSION,
    })


def _observe_baseline(spec: TaskSpec, repo_dir: Path, image_tag: str,
                      changed_files: Sequence[str], workdir: Path,
                      baseline_cache: Path | None) -> VariantObservations:
    """The baseline side of `collect_pair`, with or without reuse.

    Without `baseline_cache` this is `collect_pair`'s original behavior: a
    fresh tree and a fresh container under `workdir`, every call. With it,
    the tree and artifacts live under a directory named by `_baseline_key`
    instead of under `workdir`, and a second call at the same key skips the
    container entirely, rehydrating the observation from the first call's
    artifacts through `read_variant`.
    """
    if baseline_cache is None:
        tree = workdir / "baseline"
        artifacts = workdir / "artifacts" / "baseline"
        if tree.exists():
            shutil.rmtree(tree)
        materialize(repo_dir, spec.repo.commit, tree)
        apply_patch(tree, Path(spec.seed.bug_patch))
        return observe_variant(spec, image_tag, tree, artifacts, "baseline", changed_files)

    entry = baseline_cache / _baseline_key(spec, changed_files)
    tree, artifacts, marker = entry / "tree", entry / "artifacts", entry / "observed.ok"
    if marker.is_file():
        return read_variant(spec, tree, artifacts, "baseline", changed_files)
    if tree.exists():
        shutil.rmtree(tree)
    materialize(repo_dir, spec.repo.commit, tree)
    apply_patch(tree, Path(spec.seed.bug_patch))
    observed = observe_variant(spec, image_tag, tree, artifacts, "baseline", changed_files)
    marker.write_text("")
    return observed


def collect_pair(spec: TaskSpec, repo_dir: Path, candidate: CandidateReport,
                 workdir: Path, baseline_cache: Path | None = None) -> ObservationPair:
    """Materialize both trees, observe each once, and pair the results.

    Two trees and two containers per pair by default, which is two overlay
    installs. The alternative is one container reused across both tree
    states, and row 72 scoped that to BUILD: a container that outlived one of
    the two states is contamination in the one place Skeptic is comparing
    them. `baseline_cache` trades one of those two containers for disk when
    the baseline has already been observed at the same key; see
    `_observe_baseline`.
    """
    if candidate.is_empty:
        raise SkepticInfraError(
            f"collect_pair was handed an empty candidate ({candidate.diff_path}). "
            f"An empty diff is FAIL(no-patch), which `skeptic build` already "
            f"exits on, and `git apply` on a file with no patches in it exits "
            f"128, so continuing would report an infra error where the plan "
            f"specifies a verdict. This is a harness bug. Next: have the caller "
            f"read `CandidateReport.is_empty` the way `cli.build` does, then "
            f"report the traceback."
        )
    workdir.mkdir(parents=True, exist_ok=True)
    # The image build context is a pristine export, and it is gone before
    # either judged tree exists. An export of the fixed source sitting next to
    # the trees under judgment is the thing the gitless seeded workspace exists
    # to prevent (`cli.py:219` does the same for BUILD).
    pristine = workdir / "image-context"
    if pristine.exists():
        shutil.rmtree(pristine)
    materialize(repo_dir, spec.repo.commit, pristine)
    image = ensure_repo_image(spec, pristine, workdir / "image")
    shutil.rmtree(pristine)

    # The candidate's changed files scope both reports. Task 13 reads the
    # candidate's, M4's per-mutant selection reads the candidate's, and the
    # baseline is measured against the same paths so the two are comparable.
    changed = tuple(candidate.changed_files)
    baseline = _observe_baseline(spec, repo_dir, image.tag, changed, workdir, baseline_cache)

    candidate_tree = workdir / "candidate"
    if candidate_tree.exists():
        shutil.rmtree(candidate_tree)
    materialize(repo_dir, spec.repo.commit, candidate_tree)
    apply_patch(candidate_tree, Path(spec.seed.bug_patch))
    apply_candidate(candidate_tree, candidate.diff_path)

    artifacts = workdir / "artifacts"
    observed_candidate = observe_variant(spec, image.tag, candidate_tree,
                                         artifacts / "candidate", "candidate", changed)
    return ObservationPair(
        spec=spec, baseline=baseline, candidate=observed_candidate,
        candidate_diff=candidate, artifacts_dir=artifacts,
    )


def _selection_key(selection: tuple[str, ...]) -> str:
    """A filesystem-safe id for one distinct selection set, shared by every
    mutant that resolved to it, so the batch times each selection once."""
    return hashlib.sha256("\x1f".join(selection).encode()).hexdigest()[:12]


def _mutation_argv(test_cmd: str, selection: tuple[str, ...]) -> list[str]:
    """`test_cmd`'s own argv, plus the selected nodeids, or none for `FULL_SUITE`."""
    argv = shlex.split(test_cmd)
    if selection == FULL_SUITE:
        return argv
    return argv + list(selection)


def _write_mutation_inputs(
    tree: Path, artifacts: Path, mutants: Sequence[Mutant],
    selections: Mapping[str, tuple[str, ...]],
) -> None:
    """Host-side layout for a runnable batch: originals, mutated sources,
    and a per-mutant selection file, all on the artifacts mount.

    Mutant source never reaches a shell: every mutated file lands through
    `write_text`, the way `mutated_source` was produced (`ast.unparse` over a
    parsed, harness-owned tree), never through interpolation into the batch
    script below.
    """
    originals_dir = artifacts / _MUT_ORIGINALS
    mutants_dir = artifacts / _MUT_MUTANTS
    written: set[str] = set()
    for m in mutants:
        if m.path not in written:
            dest = originals_dir / m.path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text((tree / m.path).read_text())
            written.add(m.path)
        mdir = mutants_dir / m.mutant_id
        mdir.mkdir(parents=True, exist_ok=True)
        (mdir / Path(m.path).name).write_text(m.mutated_source)
        selection = selections[m.mutant_id]
        (mdir / "selection.txt").write_text("".join(f"{nodeid}\n" for nodeid in selection))


def _mutation_script(
    test_cmd: str, mutants: Sequence[Mutant], selections: Mapping[str, tuple[str, ...]],
) -> str:
    """One calibration step per distinct selection, then one run per mutant.

    Calibration measures how long the selected tests take against the
    unmutated (already-copied-in) candidate source, in whole milliseconds via
    `date +%s%N`, and clamps `3x` that into the mutant cap in-script (POSIX
    arithmetic and `[ ]` tests only, no bashisms): a cap computed host-side
    would need the calibration run's real wall time before the batch script
    could even be written, and the whole point of one script per batch is
    paying the overlay install once. Each mutant then: copies its mutated
    file over `/workspace/<path>`, runs under that cap, records the exit code
    and its own wall time, and restores the original.

    The calibration run's own exit code is captured too (`echo $?` runs
    immediately after it, before the next `date` command substitution can
    overwrite `$?`): `observe_mutation` reads it back and refuses the whole
    batch on a nonzero one, since a selection that is already red on the
    unmutated candidate would make every mutant on it read `killed`
    regardless of what it changed.
    """
    distinct = sorted({selections[m.mutant_id] for m in mutants})
    lines: list[str] = []
    for selection in distinct:
        key = _selection_key(selection)
        cal_dir = f"{ARTIFACTS}/{_MUT_CALIBRATION}/{key}"
        argv = _mutation_argv(test_cmd, selection)
        lines.append(f"mkdir -p {cal_dir}")
        lines.append("CSTART=$(date +%s%N)")
        lines.append(f"timeout {_MUT_CAP_CEILING_S} {shlex.join(argv)} "
                     f"> {cal_dir}/out 2> {cal_dir}/err")
        lines.append(f"echo $? > {cal_dir}/exit")
        lines.append("CEND=$(date +%s%N)")
        lines.append("CAL_MS=$(( (CEND - CSTART) / 1000000 ))")
        lines.append("CAP=$(( CAL_MS * 3 / 1000 ))")
        lines.append(f'[ "$CAP" -lt {_MUT_CAP_FLOOR_S} ] && CAP={_MUT_CAP_FLOOR_S}')
        lines.append(f'[ "$CAP" -gt {_MUT_CAP_CEILING_S} ] && CAP={_MUT_CAP_CEILING_S}')
        lines.append(f'echo "$CAP" > {cal_dir}/cap')
        lines.append(f'echo "$CAL_MS" > {cal_dir}/calibration_ms')
    for m in mutants:
        selection = selections[m.mutant_id]
        key = _selection_key(selection)
        argv = _mutation_argv(test_cmd, selection)
        mdir = f"{ARTIFACTS}/{_MUT_MUTANTS}/{m.mutant_id}"
        source = shlex.quote(f"{mdir}/{Path(m.path).name}")
        workspace_path = shlex.quote(f"/workspace/{m.path}")
        original = shlex.quote(f"{ARTIFACTS}/{_MUT_ORIGINALS}/{m.path}")
        lines.append(f"CAP=$(cat {ARTIFACTS}/{_MUT_CALIBRATION}/{key}/cap)")
        lines.append(f"cp {source} {workspace_path}")
        lines.append("MSTART=$(date +%s%N)")
        lines.append(f'timeout "$CAP" {shlex.join(argv)} > {mdir}/out 2> {mdir}/err')
        lines.append(f"echo $? > {mdir}/exit")
        lines.append("MEND=$(date +%s%N)")
        lines.append(f"echo $(( (MEND - MSTART) / 1000000 )) > {mdir}/dur_ms")
        lines.append(f"cp {original} {workspace_path}")
    return "\n".join(lines)


def _mutation_host_budget(n_runnable: int, n_distinct: int) -> int:
    """The outer `container.run` timeout: every mutant and every calibration
    run at its worst case (the 60s ceiling), plus slack for the overlay
    install and the file copies. Computable before the batch runs, since it
    depends on counts alone, never on a measured duration."""
    return (n_runnable + n_distinct) * _MUT_CAP_CEILING_S + _MUT_SLACK_S


def _status_for_exit(code: int) -> MutantStatus:
    """The exit-code contract (DECISIONS row 112): 0 survived, 1 killed, 124
    (GNU `timeout`'s own sentinel) timeout, everything else import_failed."""
    if code == 0:
        return "survived"
    if code == 1:
        return "killed"
    if code == 124:
        return "timeout"
    return "import_failed"


def _read_mutation_int(path: Path, what: str) -> int:
    raw = path.read_text().strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise SkepticInfraError(
            f"{path} holds {raw[:40]!r} where {what} belongs. The batch script "
            f"writes one integer there, so a partial or empty file means the "
            f"container stopped while writing it. This is an infra failure, "
            f"never evidence. Next: read {path.parent}/err, then re-run the pair."
        ) from exc


def _guard_calibration(artifacts: Path, distinct: Iterable[tuple[str, ...]]) -> None:
    """Refuse the whole batch if any distinct selection's own baseline run was red.

    Read after the batch, once per distinct selection set. A selection
    already failing against the unmutated candidate source would make every
    mutant sampled onto it read `killed` regardless of what it changed,
    publishing a kill rate that measures nothing rather than test adequacy.
    """
    for selection in sorted(distinct):
        cal_dir = artifacts / _MUT_CALIBRATION / _selection_key(selection)
        exit_path = cal_dir / "exit"
        if not exit_path.is_file():
            raise SkepticInfraError(
                f"The calibration run for selection {selection} left no exit "
                f"code at {exit_path}. The batch script records one "
                f"immediately after the calibration run, so an absent file "
                f"means the container stopped before reaching it. This is an "
                f"infra failure for the whole mutation observation, never "
                f"evidence. Next: read {cal_dir}/err, then re-run the pair."
            )
        code = _read_mutation_int(exit_path, "an exit code")
        if code != 0:
            raise SkepticInfraError(
                f"The calibration run for selection {selection} exited {code}, "
                f"not 0. Skeptic times this selection against the unmutated "
                f"candidate before capping any mutant sampled onto it, and a "
                f"selection already red there would make every one of those "
                f"mutants read `killed` regardless of what it changed. This is "
                f"an infra failure, never evidence: the red candidate is "
                f"t1_outcomes' evidence to report, not this check's. Next: "
                f"read the t1_outcomes artifact for this pair, then re-run "
                f"the mutation batch once the selection is green."
            )


def observe_mutation(
    spec: TaskSpec, image_tag: str, tree: Path, artifacts: Path,
    mutants: Sequence[Mutant], selections: Mapping[str, tuple[str, ...] | None],
) -> MutationReport:
    """Run a budgeted mutant batch and read back one record per mutant.

    One fresh `RunContainer` for the whole batch (row 72's sibling: one tree
    state, so paying the overlay install once is safe), reused for as many
    mutants as `mutants` names. `mutants` is expected to already be the
    sampled, budget-capped set (`mutation.sample_mutants`'s output); this
    function does not sample. `selections[mutant_id]` is `None` for a mutant
    `mutation.select_tests` could not cover (uncovered, never run) and
    `mutation.FULL_SUITE` for a caller-population mutant (no per-line
    context to select from).

    Invalid and uncovered mutants short-circuit before any container work:
    when nothing in `mutants` is runnable, no container is started at all.
    Every runnable mutant's exit file is required after the run; a missing
    one is INFRA for the whole batch (`observe_variant`'s `_read_exit`
    pattern), since a partial batch says nothing trustworthy about any
    mutant in it, run or not.

    The candidate tree is left byte-identical to how it started: every
    mutant's own script lines restore the original file before the next
    mutant's lines run, so a mutant that timed out under `timeout` (which
    always returns control to the shell) never skips its own restore. Only a
    container-level failure external to the script (caught by the host-side
    timeout below) can leave the tree mutated, and that failure is already
    INFRA for the pair.
    """
    if artifacts.exists():
        shutil.rmtree(artifacts)
    artifacts.mkdir(parents=True)

    records: dict[str, MutantRecord] = {}
    runnable: list[Mutant] = []
    for m in mutants:
        if not m.valid:
            records[m.mutant_id] = MutantRecord(
                mutant_id=m.mutant_id, path=m.path, line=m.line, operator=m.operator,
                population=m.population, status="invalid", tests_run=(), dur_ms=None)
            continue
        if selections.get(m.mutant_id) is None:
            records[m.mutant_id] = MutantRecord(
                mutant_id=m.mutant_id, path=m.path, line=m.line, operator=m.operator,
                population=m.population, status="uncovered", tests_run=(), dur_ms=None)
            continue
        runnable.append(m)

    if runnable:
        selected: dict[str, tuple[str, ...]] = {m.mutant_id: selections[m.mutant_id] for m in runnable}
        _write_mutation_inputs(tree, artifacts, runnable, selected)
        distinct = {selected[m.mutant_id] for m in runnable}
        script = _mutation_script(spec.environment.test_cmd, runnable, selected)
        ro = (tuple(spec.environment.test_dirs)
              + tuple(spec.environment.config_files)
              + tuple(spec.environment.golden_dirs))
        container = RunContainer(
            image_tag, tree, ro_subpaths=ro,
            extra_mounts=((artifacts, ARTIFACTS, "rw"),), missing_ro="drop")
        budget_s = _mutation_host_budget(len(runnable), len(distinct))
        result = container.run(script, timeout_s=budget_s)
        if result.exit_code == -1:
            raise SkepticInfraError(
                f"The mutation batch of {len(runnable)} mutant(s) over {len(distinct)} "
                f"selection set(s) timed out after {budget_s}s. That budget is the "
                f"worst case of every mutant and every calibration run hitting the "
                f"60s ceiling, plus 120s slack, so reaching it means the container "
                f"itself stopped responding rather than any one mutant running long. "
                f"This is an infra failure, never evidence. Next: `docker ps -a` "
                f"and `docker system df` to check the daemon, then re-run the pair."
            )
        _guard_calibration(artifacts, distinct)
        for m in runnable:
            mdir = artifacts / _MUT_MUTANTS / m.mutant_id
            exit_path = mdir / "exit"
            if not exit_path.is_file():
                raise SkepticInfraError(
                    f"Mutant {m.mutant_id} ({m.path}:{m.line}) left no exit code at "
                    f"{exit_path}. The batch script records one after every mutant "
                    f"it runs, so an absent file means the container stopped "
                    f"mid-batch before reaching this mutant. This is an infra "
                    f"failure for the whole mutation observation, never evidence. "
                    f"Next: read {mdir}/err, then re-run the pair."
                )
            code = _read_mutation_int(exit_path, "an exit code")
            dur_path = mdir / "dur_ms"
            dur_ms = _read_mutation_int(dur_path, "a duration in ms") if dur_path.is_file() else None
            records[m.mutant_id] = MutantRecord(
                mutant_id=m.mutant_id, path=m.path, line=m.line, operator=m.operator,
                population=m.population, status=_status_for_exit(code),
                tests_run=selected[m.mutant_id], dur_ms=dur_ms)

    ordered = tuple(records[m.mutant_id] for m in mutants)
    return MutationReport(
        seed=spec.verification.mutation.seed, budget=spec.verification.mutation.budget_mutants,
        generated=len(mutants), records=ordered)
