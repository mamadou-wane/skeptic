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

The scripts here are harness-composed. The only outside text in them is
`spec.environment.test_cmd`, which spec validation already restricts to a
plain argv with no shell metacharacters, and the artifact filenames are the
constants below. No candidate-supplied text reaches a shell: the candidate
arrives as a diff that `git apply` reads as a file.
"""
from __future__ import annotations

import re
import shlex
import shutil
from pathlib import Path

from skeptic.candidate import CandidateReport
from skeptic.checks.observations import (
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


def _suite_argv(spec: TaskSpec) -> list[str]:
    """The suite invocation, with junit written outside the judged tree.

    `builder_tools._suite_argv` is BUILD's and stays BUILD's: it writes junit
    into the workspace, which is right there and wrong here, and Task 12 makes
    this one coverage-instrumented while BUILD's stays plain. The flag both
    have to carry is `--continue-on-collection-errors` (DECISIONS row 78), so
    a candidate that broke one import is still observed instead of erasing
    the run.
    """
    return [*shlex.split(spec.environment.test_cmd),
            "--continue-on-collection-errors",
            f"--junitxml={ARTIFACTS}/{_JUNIT}", "-o", "junit_family=xunit1"]


def _unit_script(spec: TaskSpec) -> str:
    """One script per unit: collect-only, then the suite.

    Each step's exit code, stdout, and stderr land in separate files under the
    artifacts mount, so the host recovers both steps independently and a
    failure in one is never read as a failure in the other. `install.ok` is
    written first, inside the brace group `RunContainer.run` guards with the
    overlay install, so its absence after a nonzero run means the install
    failed rather than either pytest step. Neither step stops the other: a
    candidate whose collect exits 5 still runs the suite, and both empties
    become the same observation.
    """
    lines = [f"echo ok > {ARTIFACTS}/{_INSTALL_OK}"]
    for step, argv in (("collect", _collect_argv(spec.environment.test_cmd)),
                       ("suite", _suite_argv(spec))):
        lines.append(f"{shlex.join(argv)} > {ARTIFACTS}/{step}.out "
                     f"2> {ARTIFACTS}/{step}.err")
        lines.append(f"echo $? > {ARTIFACTS}/{step}.exit")
    return "\n".join(lines)


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
                    side: Side) -> VariantObservations:
    """Run one tree in one throwaway container and read back what it produced.

    The missing-mount policy is set here and it is side-specific. The baseline
    takes `RunContainer`'s strict default, because that tree is `git archive`
    plus the seed patch and a declared path missing from it is an authoring
    fault. The candidate takes `missing_ro="drop"`, because there the absent
    path is the hack, and the dropped list leaves on the observation as well as
    in the artifacts: the file is what a human reads, and only the field can
    become `ro_subpath_deleted` evidence (DECISIONS row 91).
    """
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
    # One budget for the whole unit: the overlay install, the collect-only
    # pass, and the suite. A timeout is infra on both sides.
    result = container.run(_unit_script(spec), timeout_s=spec.environment.timeout_s)
    if result.exit_code == -1:
        # Which exit files exist says where the budget went. The stderr tail
        # does not: on this path it is the harness's own timeout sentence.
        done = [step for step in ("collect", "suite")
                if (artifacts / f"{step}.exit").is_file()]
        if not done:
            where = ("No step recorded an exit code, so the time went to the "
                     "overlay install or the collect-only pass.")
        elif done == ["collect"]:
            where = ("The collect step recorded an exit code and the suite did "
                     "not, so the suite is what hung.")
        else:
            where = ("Both steps recorded exit codes, so the time went to "
                     "something after the suite: container teardown, or a "
                     "process the suite left behind.")
        raise SkepticInfraError(
            f"The {side} observation unit timed out after "
            f"{spec.environment.timeout_s}s. One unit is the overlay install, "
            f"a collect-only pass, and the suite, and the budget for all three "
            f"is environment.timeout_s. {where} A partial run says nothing "
            f"about the candidate, so this is an infra failure, never "
            f"evidence. Next: raise environment.timeout_s in the task spec, or "
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
    return VariantObservations(
        side=side, tree=tree, artifacts=artifacts, collected=collected,
        collect_exit=collect_exit, outcomes=outcomes,
        collection_errors=collection_errors, suite_exit=suite_exit,
        coverage=None, dropped_ro_subpaths=container.dropped_ro_subpaths,
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
    baseline = observe_variant(spec, image.tag, baseline_tree,
                               artifacts / "baseline", "baseline")
    observed_candidate = observe_variant(spec, image.tag, candidate_tree,
                                         artifacts / "candidate", "candidate")
    return ObservationPair(
        spec=spec, baseline=baseline, candidate=observed_candidate,
        candidate_diff=candidate, artifacts_dir=artifacts,
    )
