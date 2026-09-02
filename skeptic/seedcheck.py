"""Corpus admission: the junit parser, the suite runner, and `seed --check`.

Admission refuses a tree that does not collect cleanly, and BUILD and VERIFY
lean on that. `run_suite` raises on any pytest exit outside (0, 1), so a
collection failure stops the check before an invariant is computed, and
`pristine-green-x2` and `seed-red-exact` both fold `collection_errors == 0`
into their pass condition. That is the contract behind
`--continue-on-collection-errors` downstream (DECISIONS row 78): BUILD and
VERIFY ask what a candidate did and have to survive a broken import in order
to observe it, and they can read a collection error as candidate-caused only
because the seeded tree was known to collect before the candidate touched it.
"""
from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from defusedxml import ElementTree as ET

from skeptic.candidate import snapshot
from skeptic.errors import SkepticInfraError
from skeptic.spec import TaskSpec
from skeptic.workspace import (
    apply_patch,
    assert_no_git,
    assert_pristine_unreachable,
    clone_pinned,
    materialize,
)


class SandboxRunnerLike(Protocol):
    def exec(self, cmd: str, timeout_s: int, env: dict[str, str] | None = None): ...


@dataclass
class SuiteResult:
    outcomes: dict[str, str]
    collection_errors: int

    def red_set(self) -> set[str]:
        return {k for k, v in self.outcomes.items() if v in ("failed", "error")}

    def passed_set(self) -> set[str]:
        return {k for k, v in self.outcomes.items() if v == "passed"}

    def outcome_map_equal(self, other: SuiteResult) -> bool:
        return self.outcomes == other.outcomes


def parse_junit(path: Path) -> SuiteResult:
    if not path.is_file():
        raise SkepticInfraError(
            f"junit report missing at {path}: the test run did not produce a "
            f"report, so results cannot be trusted. This is an infra failure, "
            f"never evidence. Next: re-run; if it persists check the test_cmd."
        )
    return parse_junit_bytes(path.read_bytes(), str(path))


def parse_junit_bytes(data: bytes, source: str) -> SuiteResult:
    root = ET.fromstring(data)
    outcomes: dict[str, str] = {}
    collection_errors = 0
    for case in root.iter("testcase"):
        file_attr = case.get("file")
        name = case.get("name", "")
        if file_attr is None:
            collection_errors += 1
            continue
        # The ordinary import error. Measured with pytest 9.1.1 under
        # --continue-on-collection-errors, the entry carries a file attribute
        # and an empty classname: <testcase classname="" name="tests.test_broken"
        # file="tests/test_broken.py"><error message="collection failure">. The
        # literal is _pytest/junitxml.py:210. Reconstructing a nodeid from it
        # invents tests/test_broken.py::tests.test_broken and scores a test
        # that never existed as red, so it is counted and dropped instead.
        # Sample: tests/fixtures/pytest-output/*-collect-error-junit.xml.
        if any(child.tag == "error" and child.get("message") == "collection failure"
               for child in case):
            collection_errors += 1
            continue
        classname = case.get("classname") or ""
        module_dotted = file_attr.removesuffix(".py").replace("/", ".")
        if classname in ("", module_dotted):
            nodeid = f"{file_attr}::{name}"
        elif classname.startswith(module_dotted + "."):
            class_chain = classname[len(module_dotted) + 1:]
            nodeid = f"{file_attr}::{class_chain.replace('.', '::')}::{name}"
        else:
            raise SkepticInfraError(
                f"junit testcase classname {classname!r} does not extend its "
                f"file's module path {module_dotted!r} in {source}. Skeptic "
                f"reconstructs pytest nodeids from file and classname, and an "
                f"unmappable classname would corrupt the outcome map. Next: "
                f"inspect the junit XML; if a plugin rewrites classnames this "
                f"repo needs a dedicated mapping before admission."
            )
        if nodeid in outcomes:
            raise SkepticInfraError(
                f"Duplicate reconstructed test id {nodeid!r} in junit report "
                f"{source}. Skeptic reconstructs pytest nodeids from file, "
                f"classname, and name, and duplicate full nodeids indicate "
                f"corrupt junit data. Next: inspect the junit XML and test "
                f"discovery in this repo."
            )
        outcome = "passed"
        for child in case:
            if child.tag == "failure":
                outcome = "failed"
            elif child.tag == "error":
                outcome = "error"
            elif child.tag == "skipped":
                # xunit1 writes pytest.skip and pytest.xfail as the same tag
                # and separates them only by the type attribute. Neither is
                # red, and both sides of a gold run map identically, so
                # red_set() and outcome_map_equal are unaffected. A non-strict
                # xpass writes no child at all and is invisible here: see
                # skeptic/checks/observations.py's module docstring.
                child_type = child.get("type") or ""
                outcome = "xfailed" if child_type.startswith("pytest.xfail") else "skipped"
        outcomes[nodeid] = outcome
    return SuiteResult(outcomes=outcomes, collection_errors=collection_errors)


def run_suite(
    runner: SandboxRunnerLike, test_cmd: str, timeout_s: int, junit_path: Path
) -> SuiteResult:
    cmd = f"{test_cmd} --junitxml={junit_path} -o junit_family=xunit1"
    result = runner.exec(cmd, timeout_s=timeout_s)
    if result.exit_code == -1:
        raise SkepticInfraError(
            f"Test suite timed out after {timeout_s}s. Raise environment."
            f"timeout_s in the task spec, or investigate hanging tests. "
            f"stderr tail:\n{result.stderr[-800:]}"
        )
    if result.exit_code not in (0, 1):
        raise SkepticInfraError(
            f"pytest exited {result.exit_code}. Start with collection: exit 2 "
            f"is what pytest returns when a test module fails to import, and "
            f"then no test ran at all. The other codes are 3=internal error, "
            f"4=cli usage, 5=no tests collected. Every one of them is an "
            f"operational failure. Admission refuses a tree that cannot "
            f"collect its own tests, which is what lets BUILD and VERIFY run "
            f"with --continue-on-collection-errors and still read a "
            f"collection error as the candidate's doing.\n"
            f"stderr tail:\n{result.stderr[-800:]}\n"
            f"stdout tail:\n{result.stdout[-800:]}\n"
            f"Next: run `{test_cmd}` by hand inside the workspace."
        )
    return parse_junit(junit_path)


@dataclass
class InvariantResult:
    name: str
    ok: bool
    detail: str


@dataclass
class CheckReport:
    task_id: str
    results: list[InvariantResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)


def _fresh_seeded(spec: TaskSpec, repo: Path, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    materialize(repo, spec.repo.commit, dest)
    apply_patch(dest, Path(spec.seed.bug_patch))
    return dest


def _drop_quarantined(result: SuiteResult, quarantine: list[str]) -> SuiteResult:
    """The invariant view of a suite run: quarantined nodeids removed.

    Admission-time counterpart of the exclusion t1_collect and t1_outcomes
    already apply (spec.py's SeedSpec.quarantine comment): a known-flaky test
    must not be able to fail its own task's re-admission, in either direction
    (a red flake breaking green checks, or an outcome flip breaking map
    equality). collection_errors pass through untouched.
    """
    if not quarantine:
        return result
    q = set(quarantine)
    return SuiteResult(
        outcomes={k: v for k, v in result.outcomes.items() if k not in q},
        collection_errors=result.collection_errors,
    )


def run_acceptance(
    tree: Path,
    acc_src: Path,
    runner_factory: Callable[[Path], SandboxRunnerLike],
    timeout_s: int,
    quarantine: list[str],
) -> SuiteResult:
    """Run the acceptance suite against `tree`, quarantine dropped.

    Copies `acc_src` to `tree/.skeptic-acceptance` and runs it there with
    `runner_factory(tree)`. `tree` must be a fresh materialized tree, never
    a BUILD workspace: `candidate.EXCLUDE_GLOBS` does not match
    `.skeptic-acceptance`, so a copy landing in the workspace a BUILD ran in
    would leak into the candidate diff the next time `extract_candidate`
    read it.

    Lifted out of `check_task`'s own `acceptance_run` closure (task 3) to a
    module-level function (task 17) so a second caller, `skeptic build-arm`'s
    attempt classifier, can run the same suite against its own fresh tree
    without re-deriving admission's mechanics. `check_task` below calls this
    with its own closed-over `acc_src`/`runner_factory`/`env.timeout_s`/
    `spec.seed.quarantine`; behavior is unchanged from before the lift.

    `snapshot` rather than a bare `copytree` (issue #34): a pytest-rewritten
    pyc under `acc_src/__pycache__` survives a plain copy with its mtime and
    size, so pytest loads it in the copied tree and the junit `file`
    attribute carries the source path from its `co_filename`. `parse_junit`
    then refuses the classname as unmappable, and the check reports INFRA on
    a clean suite.
    """
    dest = tree / ".skeptic-acceptance"
    if dest.exists():
        shutil.rmtree(dest)
    snapshot(acc_src, dest)
    acc_runner = runner_factory(tree)
    result = run_suite(acc_runner, "python -m pytest -q .skeptic-acceptance",
                       timeout_s, tree / ".skeptic-acceptance-junit.xml")
    return _drop_quarantined(result, quarantine)


def check_task(
    spec: TaskSpec,
    workroot: Path,
    runner_factory: Callable[[Path], SandboxRunnerLike],
    repo_cache: Path,
) -> CheckReport:
    report = CheckReport(task_id=spec.task_id)
    env = spec.environment
    repo = clone_pinned(spec.repo.url, spec.repo.commit, repo_cache)
    workroot.mkdir(parents=True, exist_ok=True)

    # 1. pristine green x2 (flakiness screen)
    pristine_ws = workroot / "pristine"
    if pristine_ws.exists():
        shutil.rmtree(pristine_ws)
    materialize(repo, spec.repo.commit, pristine_ws)
    runner = runner_factory(pristine_ws)
    first = run_suite(runner, env.test_cmd, env.timeout_s, pristine_ws / ".skeptic-junit-1.xml")
    second = run_suite(runner, env.test_cmd, env.timeout_s, pristine_ws / ".skeptic-junit-2.xml")
    first_view = _drop_quarantined(first, spec.seed.quarantine)
    second_view = _drop_quarantined(second, spec.seed.quarantine)
    stable = first_view.outcome_map_equal(second_view) and first_view.collection_errors == 0
    green = not first_view.red_set()
    report.results.append(InvariantResult(
        "pristine-green-x2", stable and green,
        "stable and green" if stable and green else
        f"red={sorted(first_view.red_set())[:5]} stable={stable} "
        f"collection_errors={first_view.collection_errors}",
    ))
    baseline = first

    # seeded workspace
    seeded_ws = _fresh_seeded(spec, repo, workroot / "seeded")

    # 2. no .git
    try:
        assert_no_git(seeded_ws)
        report.results.append(InvariantResult("workspace-gitless", True, "no .git present"))
    except SkepticInfraError as exc:
        report.results.append(InvariantResult("workspace-gitless", False, str(exc)))

    # 3. pristine text unreachable (whole-line matching; owner decision 2026-07-23)
    try:
        assert_pristine_unreachable(seeded_ws, Path(spec.seed.bug_patch))
        report.results.append(InvariantResult(
            "pristine-text-unreachable", True, "removed pristine lines not reachable as complete lines"))
    except SkepticInfraError as exc:
        report.results.append(InvariantResult("pristine-text-unreachable", False, str(exc)))

    # 4. seed red exact
    seeded_runner = runner_factory(seeded_ws)
    seeded = run_suite(seeded_runner, env.test_cmd, env.timeout_s,
                       seeded_ws / ".skeptic-junit.xml")
    seeded_view = _drop_quarantined(seeded, spec.seed.quarantine)
    expected_red = set(spec.seed.failing_tests)
    actual_red = seeded_view.red_set()
    ok4 = actual_red == expected_red and seeded_view.collection_errors == 0
    report.results.append(InvariantResult(
        "seed-red-exact", ok4,
        "red set matches failing_tests" if ok4 else
        f"unexpected={sorted(actual_red - expected_red)} "
        f"missing={sorted(expected_red - actual_red)} "
        f"collection_errors={seeded_view.collection_errors}",
    ))

    # 5. gold restores baseline
    gold_variants = [v for v in spec.evaluation.variants if v.label == "clean"]
    ok5, detail5 = True, "no clean variants"
    for variant in gold_variants:
        gold_ws = _fresh_seeded(spec, repo, workroot / f"gold-{variant.id}")
        apply_patch(gold_ws, Path(variant.patch))
        gold_runner = runner_factory(gold_ws)
        gold = run_suite(gold_runner, env.test_cmd, env.timeout_s,
                         gold_ws / ".skeptic-junit.xml")
        gold_view = _drop_quarantined(gold, spec.seed.quarantine)
        baseline_view = _drop_quarantined(baseline, spec.seed.quarantine)
        if gold_view.outcome_map_equal(baseline_view):
            detail5 = f"{variant.id} matches pristine baseline"
        else:
            diff_keys = [k for k in set(gold_view.outcomes) | set(baseline_view.outcomes)
                         if gold_view.outcomes.get(k) != baseline_view.outcomes.get(k)]
            ok5, detail5 = False, f"{variant.id} diverges on {sorted(diff_keys)[:5]}"
            break
    report.results.append(InvariantResult("gold-restores-baseline", ok5, detail5))

    # 6. hacked variants green
    hacked = [v for v in spec.evaluation.variants if v.label == "hacked"]
    ok6, detail6 = True, "no hacked variants"
    for variant in hacked:
        hack_ws = _fresh_seeded(spec, repo, workroot / f"hack-{variant.id}")
        apply_patch(hack_ws, Path(variant.patch))
        hack_runner = runner_factory(hack_ws)
        hack = run_suite(hack_runner, env.test_cmd, env.timeout_s,
                         hack_ws / ".skeptic-junit.xml")
        hack_view = _drop_quarantined(hack, spec.seed.quarantine)
        if hack_view.red_set():
            ok6 = False
            detail6 = f"{variant.id} still red: {sorted(hack_view.red_set())[:5]}"
            break
        detail6 = f"{len(hacked)} hacked variant(s) green"
    report.results.append(InvariantResult("hacked-variants-green", ok6, detail6))

    # 7. acceptance matrix (plan invariant 5). Declared-if-present in wave A,
    # the hacked-variants-green precedent: a task mid-authoring admits without
    # a suite and says so; the wave B corpus gate requires presence.
    acc = spec.acceptance_suite
    if acc is None:
        report.results.append(InvariantResult(
            "acceptance-matrix", True, "no acceptance suite declared"))
        return report
    acc_src = Path(acc.path)

    def acceptance_run(tree: Path) -> SuiteResult:
        return run_acceptance(tree, acc_src, runner_factory, env.timeout_s,
                              spec.seed.quarantine)

    def resolve_tree(name: str) -> Path:
        if name == "pristine":
            dest = workroot / "acc-pristine"
            if dest.exists():
                shutil.rmtree(dest)
            materialize(repo, spec.repo.commit, dest)
            return dest
        if name == "seeded":
            return _fresh_seeded(spec, repo, workroot / "acc-seeded")
        variant = next(v for v in spec.evaluation.variants if v.id == name)
        tree = _fresh_seeded(spec, repo, workroot / f"acc-{variant.id}")
        apply_patch(tree, Path(variant.patch))
        return tree

    ok7, details7 = True, []
    for name in acc.must_pass_on:
        red = acceptance_run(resolve_tree(name)).red_set()
        if red:
            ok7 = False
            details7.append(f"{name} red on {sorted(red)[:3]}")
    for name in acc.must_fail_on:
        red = acceptance_run(resolve_tree(name)).red_set()
        if not red:
            ok7 = False
            details7.append(f"{name} green (suite does not discriminate)")
    report.results.append(InvariantResult(
        "acceptance-matrix", ok7,
        "; ".join(details7) if details7 else
        f"pass on {acc.must_pass_on}, fail on {acc.must_fail_on}"))

    return report
