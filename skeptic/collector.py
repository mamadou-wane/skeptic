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

import json
import re
import shlex
import shutil
import sqlite3
from collections.abc import Iterable, Sequence
from contextlib import closing
from pathlib import Path

from skeptic.candidate import CandidateReport
from skeptic.checks.observations import (
    CoverageReport,
    ObservationPair,
    Side,
    VariantObservations,
    parse_collect_manifest,
)
from skeptic.errors import SkepticInfraError
from skeptic.image import ensure_repo_image
from skeptic.sandbox import RunContainer
from skeptic.seedcheck import parse_junit
from skeptic.spec import TaskSpec
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
    """Run one tree in one throwaway container and read back what it produced.

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
    return VariantObservations(
        side=side, tree=tree, artifacts=artifacts, collected=collected,
        collect_exit=collect_exit, outcomes=outcomes,
        collection_errors=collection_errors, suite_exit=suite_exit,
        coverage=coverage, dropped_ro_subpaths=container.dropped_ro_subpaths,
    )


def collect_pair(spec: TaskSpec, repo_dir: Path, candidate: CandidateReport,
                 workdir: Path) -> ObservationPair:
    """Materialize both trees, observe each once, and pair the results.

    Two trees and two containers per pair, which is two overlay installs. The
    alternative is one container reused across both tree states, and row 72
    scoped that to BUILD: a container that outlived one of the two states is
    contamination in the one place Skeptic is comparing them.
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

    baseline_tree, candidate_tree = workdir / "baseline", workdir / "candidate"
    for tree in (baseline_tree, candidate_tree):
        if tree.exists():
            shutil.rmtree(tree)
        materialize(repo_dir, spec.repo.commit, tree)
        apply_patch(tree, Path(spec.seed.bug_patch))
    apply_candidate(candidate_tree, candidate.diff_path)

    artifacts = workdir / "artifacts"
    # The candidate's changed files scope both reports. Task 13 reads the
    # candidate's, M4's per-mutant selection reads the candidate's, and the
    # baseline is measured against the same paths so the two are comparable.
    changed = tuple(candidate.changed_files)
    baseline = observe_variant(spec, image.tag, baseline_tree,
                               artifacts / "baseline", "baseline", changed)
    observed_candidate = observe_variant(spec, image.tag, candidate_tree,
                                         artifacts / "candidate", "candidate", changed)
    return ObservationPair(
        spec=spec, baseline=baseline, candidate=observed_candidate,
        candidate_diff=candidate, artifacts_dir=artifacts,
    )
