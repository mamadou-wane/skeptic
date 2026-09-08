"""Corpus admission: the junit parser, the suite runner, and `seed --check`.

Admission refuses a tree that does not collect cleanly, and BUILD and VERIFY
lean on that. `_run_trusted_suite` raises on any pytest exit outside (0, 1), so a
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
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from defusedxml import ElementTree as ET

from skeptic.candidate import snapshot
from skeptic.errors import SkepticInfraError
from skeptic.sandbox import ExecResult, _run
from skeptic.spec import TaskSpec
from skeptic.workspace import (
    apply_patch,
    assert_no_git,
    assert_pristine_unreachable,
    clone_pinned,
    materialize,
)


class _TrustedCorpusVenvRunner:
    """Private host runner for owner-trusted corpus admission only."""

    def __init__(self, workspace: Path, venv_dir: Path) -> None:
        self.workspace = workspace
        self.venv_dir = venv_dir

    @property
    def isolation(self) -> str:
        return "venv-reduced-isolation"

    @property
    def _python(self) -> Path:
        return self.venv_dir / "bin" / "python"

    def setup(self, install_cmds: list[str], python: str = "python3.12",
              constraints: Path | None = None) -> None:
        if not self.venv_dir.exists():
            resolved = shutil.which(python)
            if resolved is None:
                raise SkepticInfraError(
                    f"Interpreter {python!r} not found on PATH. "
                    f"Skeptic builds the verify venv with the interpreter "
                    f"named in repo.python. "
                    f"Next: install {python!r}, or fix repo.python in the "
                    f"task spec."
                )
            proc = subprocess.run(
                [resolved, "-m", "venv", str(self.venv_dir)],
                capture_output=True, text=True, check=False,
            )
            if proc.returncode != 0:
                raise SkepticInfraError(
                    f"venv creation failed for {python!r} ({resolved}) "
                    f"(exit {proc.returncode}).\n"
                    f"stderr tail:\n{proc.stderr[-2000:]}\n"
                    f"Skeptic needs a working venv to install and run the "
                    f"target repo's tests. "
                    f"Next: check {resolved} is a working interpreter, or "
                    f"fix repo.python in the task spec, then re-run "
                    f"`skeptic seed --task <id> --check`."
                )
        # The install lines run verbatim, so the pin reaches pip the one way
        # that covers every command as written: its environment. Absent a
        # pin, no key is set and the install resolves as it always did.
        pin_env = {"PIP_CONSTRAINT": str(constraints.resolve())} if constraints else None
        for cmd in install_cmds:
            result = self.exec(cmd, timeout_s=900, env=pin_env)
            if result.exit_code != 0:
                raise SkepticInfraError(
                    f"Install command failed in venv runner: {cmd!r} "
                    f"(exit {result.exit_code}).\nstderr tail:\n{result.stderr[-2000:]}\n"
                    f"Skeptic needs the target repo installed to run its tests. "
                    f"Next: fix the environment.install commands in the task spec, "
                    f"then re-run `skeptic seed --task <id> --check`."
                )
        if constraints is not None:
            # Read the closure back, as the image build does: a constraint pip
            # did not honor is silent otherwise. The venv installs a subset of
            # the pin (no build backends, no harness tooling), so the check is
            # that every version present is one the pin names.
            frozen = self.exec("pip freeze --exclude-editable", timeout_s=120)
            named = set(constraints.read_text().splitlines())
            off = [line for line in frozen.stdout.splitlines() if line and line not in named]
            if frozen.exit_code != 0 or off:
                raise SkepticInfraError(
                    f"the venv at {self.venv_dir} resolved versions the pin "
                    f"{constraints} does not name: {', '.join(off[:8]) or frozen.stderr[-300:]}.\n"
                    f"Skeptic pins task installs so a fresh machine measures "
                    f"what the corpus measured. Next: rewrite the pin from a "
                    f"closure you stand behind and record the move in "
                    f"DECISIONS.md, or fix the install lines the pin does not cover."
                )

    def exec(self, cmd: str, timeout_s: int, env: dict[str, str] | None = None) -> ExecResult:
        venv_bin = str(self.venv_dir / "bin")
        # COLUMNS is deliberately absent. Pinning it looks like determinism and
        # is not: a suite that renders to a terminal width sets that width
        # explicitly, while a suite that probes terminal-size *fallback* is
        # testing the behavior when COLUMNS is unset, and pinning it fails those
        # tests for a reason unrelated to any seeded bug. Measured on both
        # corpus repos: rich fails 3 tests with COLUMNS pinned, and click's
        # 1939 pass identically either way, so the pin cost coverage and bought
        # nothing (DECISIONS.md #68).
        #
        # Locale and timezone ARE pinned, because those change program output
        # without any test opting in.
        # venv_env keeps this distinct from the module-level base_env(),
        # which builds the container environment. This one is the host venv's.
        venv_env = {
            "PATH": f"{venv_bin}:/usr/bin:/bin",
            "VIRTUAL_ENV": str(self.venv_dir),
            "HOME": str(self.workspace),
            "TERM": "dumb",
            "NO_COLOR": "1",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
        }
        if env:
            venv_env.update(env)
        # sh -c on purpose, matching the container runners: the same command
        # string must mean the same thing on every runner (M1 review
        # deferral, DECISIONS.md #70). Commands here are spec-authored
        # trusted input. A missing binary is exit 127 from sh; callers
        # convert nonzero exits into SkepticInfraError with the stderr tail.
        return _run(["sh", "-c", cmd], cwd=self.workspace, timeout_s=timeout_s, env=venv_env)


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


def _run_trusted_suite(
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


def _run_trusted_acceptance(
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

    This private runner-factory seam belongs only to trusted corpus admission.
    Agent-produced candidates use candidate_runtime and cannot supply a runner.

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
    result = _run_trusted_suite(acc_runner, "python -m pytest -q .skeptic-acceptance",
                       timeout_s, tree / ".skeptic-acceptance-junit.xml")
    return _drop_quarantined(result, quarantine)


def _check_trusted_task(
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
    first = _run_trusted_suite(runner, env.test_cmd, env.timeout_s, pristine_ws / ".skeptic-junit-1.xml")
    second = _run_trusted_suite(runner, env.test_cmd, env.timeout_s, pristine_ws / ".skeptic-junit-2.xml")
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
    seeded = _run_trusted_suite(seeded_runner, env.test_cmd, env.timeout_s,
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
        gold = _run_trusted_suite(gold_runner, env.test_cmd, env.timeout_s,
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
        hack = _run_trusted_suite(hack_runner, env.test_cmd, env.timeout_s,
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
        return _run_trusted_acceptance(tree, acc_src, runner_factory, env.timeout_s,
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


def check_trusted_task(spec: TaskSpec, workroot: Path, repo_cache: Path,
                       venv_root: Path) -> CheckReport:
    """Admit owner-trusted corpus material; no arbitrary candidate or runner input.

    Candidate acceptance uses candidate_runtime's Docker-only entry points.
    This reduced-isolation API only evaluates variants registered in the spec.
    """
    def runner_factory(workspace: Path) -> _TrustedCorpusVenvRunner:
        runner = _TrustedCorpusVenvRunner(workspace, venv_root / workspace.name)
        runner.setup(spec.environment.install, constraints=spec.environment.constraints_file)
        return runner

    return _check_trusted_task(spec, workroot, runner_factory, repo_cache)
