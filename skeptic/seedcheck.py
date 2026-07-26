from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from defusedxml import ElementTree as ET

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

    def outcome_map_equal(self, other: SuiteResult) -> bool:
        return self.outcomes == other.outcomes


def parse_junit(path: Path) -> SuiteResult:
    if not path.is_file():
        raise SkepticInfraError(
            f"junit report missing at {path} — the test run did not produce a "
            f"report, so results cannot be trusted. This is an infra failure, "
            f"never evidence. Next: re-run; if it persists check the test_cmd."
        )
    root = ET.parse(path).getroot()
    outcomes: dict[str, str] = {}
    collection_errors = 0
    for case in root.iter("testcase"):
        file_attr = case.get("file")
        name = case.get("name", "")
        if file_attr is None:
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
                f"file's module path {module_dotted!r} in {path}. Skeptic "
                f"reconstructs pytest nodeids from file and classname, and an "
                f"unmappable classname would corrupt the outcome map. Next: "
                f"inspect the junit XML; if a plugin rewrites classnames this "
                f"repo needs a dedicated mapping before admission."
            )
        if nodeid in outcomes:
            raise SkepticInfraError(
                f"Duplicate reconstructed test id {nodeid!r} in junit report "
                f"{path}. Skeptic reconstructs pytest nodeids from file, "
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
                outcome = "skipped"
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
            f"pytest exited {result.exit_code} (2=usage error, 3=internal, "
            f"4=cli usage, 5=no tests collected) — an operational failure, not "
            f"a test outcome. stderr tail:\n{result.stderr[-800:]}\n"
            f"stdout tail:\n{result.stdout[-800:]}\n"
            f"Next: run the test_cmd by hand inside the workspace."
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
    stable = first.outcome_map_equal(second) and first.collection_errors == 0
    green = not first.red_set()
    report.results.append(InvariantResult(
        "pristine-green-x2", stable and green,
        "stable and green" if stable and green else
        f"red={sorted(first.red_set())[:5]} stable={stable} "
        f"collection_errors={first.collection_errors}",
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
    expected_red = set(spec.seed.failing_tests)
    actual_red = seeded.red_set()
    ok4 = actual_red == expected_red and seeded.collection_errors == 0
    report.results.append(InvariantResult(
        "seed-red-exact", ok4,
        "red set matches failing_tests" if ok4 else
        f"unexpected={sorted(actual_red - expected_red)} "
        f"missing={sorted(expected_red - actual_red)} "
        f"collection_errors={seeded.collection_errors}",
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
        if gold.outcome_map_equal(baseline):
            detail5 = f"{variant.id} matches pristine baseline"
        else:
            diff_keys = [k for k in set(gold.outcomes) | set(baseline.outcomes)
                         if gold.outcomes.get(k) != baseline.outcomes.get(k)]
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
        if hack.red_set():
            ok6 = False
            detail6 = f"{variant.id} still red: {sorted(hack.red_set())[:5]}"
            break
        detail6 = f"{len(hacked)} hacked variant(s) green"
    report.results.append(InvariantResult("hacked-variants-green", ok6, detail6))

    return report
