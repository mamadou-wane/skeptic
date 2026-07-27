"""The collector, faked at the subprocess boundary.

Every fast test replaces `sandbox._run` with a function that writes the
artifact files the unit script would have written and hands back an
`ExecResult`. The real `RunContainer` and the real `docker_run_args` still
run, so mount policy, argv order, and the artifacts mount are exercised
rather than stubbed; only the daemon is absent.
"""
import json
import re
import shlex
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from skeptic.candidate import CandidateReport
from skeptic.checks.observations import VariantObservations
from skeptic.collector import (
    _collect_argv,
    collect_pair,
    coverage_test_cmd,
    observe_variant,
    read_coverage,
    render_coverage_rc,
)
from skeptic.errors import SkepticInfraError
from skeptic.image import ImageRef
from skeptic.sandbox import ExecResult
from skeptic.spec import TaskSpec
from skeptic.workspace import apply_candidate, clone_pinned
from tests.helpers import BUGGY, PRISTINE, make_minirepo_task, make_task_spec

NODE_A = "tests/test_x.py::test_a"
NODE_B = "tests/test_x.py::test_b"

# A real instrumented minirepo run, captured at execution time: the scoped
# JSON report and a SQL dump of the data file the same run wrote. See the
# `coverage.json.cmd` sidecar for the commands that produced both.
COVERAGE_SAMPLE = Path(__file__).parent / "fixtures" / "coverage" / "minirepo-gold"


def _sample_artifacts(tmp_path: Path) -> Path:
    """The sample as an artifacts directory: the JSON, and the replayed data file.

    `read_coverage` reads two files out of one directory, and the data file is
    committed as SQL rather than as a binary database (see the sidecar), so
    the test replays it.
    """
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "coverage.json").write_text((COVERAGE_SAMPLE / "coverage.json").read_text())
    with closing(sqlite3.connect(artifacts / ".coverage")) as data_file:
        data_file.executescript((COVERAGE_SAMPLE / "coverage.sql").read_text())
    return artifacts


def _junit(outcomes: dict[str, str]) -> str:
    """A xunit1 report `seedcheck.parse_junit` reads, one testcase per nodeid."""
    cases = []
    for nodeid, outcome in outcomes.items():
        file_attr, name = nodeid.split("::")
        classname = file_attr.removesuffix(".py").replace("/", ".")
        body = "" if outcome == "passed" else '<failure message="boom">x</failure>'
        cases.append(
            f'<testcase classname="{classname}" name="{name}" '
            f'file="{file_attr}" time="0.0">{body}</testcase>'
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" '
        f'tests="{len(outcomes)}">{"".join(cases)}</testsuite></testsuites>'
    )


def _artifacts_of(argv: list[str]) -> Path:
    """The host side of the /artifacts mount, read back out of the docker argv."""
    mount = next(a for a in argv if a.endswith(":/artifacts:rw"))
    return Path(mount.split(":")[0])


def _coverage_json(files: dict[str, dict]) -> str:
    """The shape `coverage json --show-contexts` writes, one entry per file."""
    return json.dumps({"meta": {"version": "7.15.2", "show_contexts": True},
                       "files": files})


# valid-task.yaml's src_dirs is ["src/click/"], so a changed file has to sit
# under it to be measurable at all.
CHANGED = ("src/click/mod.py",)
COVERED = {"src/click/mod.py": {"executed_lines": [1, 4], "missing_lines": [5],
                                "excluded_lines": [],
                                "contexts": {"1": [""], "4": ["test_x.test_a"]}}}


def _write_coverage_data(path: Path, contexts: tuple[str, ...]) -> None:
    """The one table `read_coverage` queries, in the shape coverage writes it.

    A fake at the subprocess boundary like the junit above: the real table is
    proven against the committed dump in the `read_coverage` tests, and this
    is what makes the wiring readable without a container.
    """
    with closing(sqlite3.connect(path)) as data_file:
        data_file.execute("create table context (id integer primary key, context text)")
        data_file.executemany("insert into context (context) values (?)",
                              [(name,) for name in contexts])
        data_file.commit()


def fake_unit(monkeypatch, *, collected=(), collect_exit=0, suite_exit=0,
              outcomes=None, install_ok=True, timed_out=False, covered=None):
    """Answer one `docker run` by writing what the unit script writes."""
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout_s, env):
        calls.append(cmd)
        if timed_out:
            return ExecResult(-1, "", f"command timed out after {timeout_s}s", 1000)
        if not install_ok:
            return ExecResult(1, "", "pip: could not install /workspace offline", 900)
        art = _artifacts_of(cmd)
        (art / "install.ok").write_text("ok\n")
        manifest = "".join(f"{n}\n" for n in collected)
        (art / "collect.out").write_text(f"{manifest}\n{len(collected)} tests collected\n")
        (art / "collect.err").write_text("")
        (art / "collect.exit").write_text(f"{collect_exit}\n")
        (art / "suite.out").write_text("1 passed\n")
        (art / "suite.err").write_text("")
        (art / "suite.exit").write_text(f"{suite_exit}\n")
        (art / "junit.xml").write_text(_junit(outcomes or {}))
        _write_coverage_data(art / ".coverage", ("", "test_x.test_a"))
        # The report step is in the script only when the patch carries a
        # measurable file, so the fake writes its files only then, the way the
        # unit does.
        if "coverage json" in cmd[-1]:
            (art / "coverage.json").write_text(
                _coverage_json(COVERED if covered is None else covered))
            (art / "coverage.out").write_text("Wrote JSON report to /artifacts/coverage.json\n")
            (art / "coverage.err").write_text("")
            (art / "coverage.exit").write_text("0\n")
        return ExecResult(0, "", "", 1200)

    monkeypatch.setattr("skeptic.sandbox._run", fake_run)
    return calls


def _tree(root: Path, with_tests: bool) -> Path:
    """A stand-in workspace carrying the paths valid-task.yaml declares ro."""
    tree = root / "tree"
    tree.mkdir(parents=True)
    if with_tests:
        (tree / "tests").mkdir()
    return tree


def _observed(spec: TaskSpec, root: Path, side: str, with_tests: bool = True,
              changed_files: tuple[str, ...] = CHANGED) -> VariantObservations:
    return observe_variant(spec, "img", _tree(root, with_tests),
                           root / "artifacts" / side, side, changed_files)


def _stale_artifacts(root: Path, side: str) -> Path:
    """A complete, self-consistent artifact set from an earlier run."""
    stale = root / "artifacts" / side
    stale.mkdir(parents=True)
    (stale / "install.ok").write_text("ok\n")
    (stale / "collect.out").write_text(f"{NODE_A}\n\n1 test collected\n")
    (stale / "collect.exit").write_text("0\n")
    (stale / "suite.out").write_text("1 passed\n")
    (stale / "suite.exit").write_text("0\n")
    (stale / "junit.xml").write_text(_junit({NODE_A: "passed"}))
    (stale / "ran-before.txt").write_text("run 1\n")
    return stale


def _fake_observe(spec, image_tag, tree, artifacts, side, changed_files):
    artifacts.mkdir(parents=True, exist_ok=True)
    return VariantObservations(
        side=side, tree=tree, artifacts=artifacts, collected=None, collect_exit=None,
        outcomes=None, collection_errors=None, suite_exit=None, coverage=None)


def _minirepo(tmp_path: Path) -> tuple[TaskSpec, Path, CandidateReport]:
    """The minirepo task, its clone, and the gold patch as a candidate."""
    from skeptic.spec import find_task

    tasks_dir, task_id = make_minirepo_task(tmp_path)
    spec = find_task(task_id, tasks_dir)
    repo = clone_pinned(spec.repo.url, spec.repo.commit, tmp_path / "cache")
    return spec, repo, _gold_candidate(spec)


def _gold_candidate(spec: TaskSpec) -> CandidateReport:
    gold = next(v for v in spec.evaluation.variants if v.label == "clean")
    return CandidateReport(diff_path=Path(gold.patch), changed_files=["minirepo.py"],
                           out_of_scope=[], is_empty=False)


def _stub_image(monkeypatch) -> None:
    monkeypatch.setattr(
        "skeptic.collector.ensure_repo_image",
        lambda spec, pristine, workdir: ImageRef(
            tag="img", image_id="sha256:stub", constraints_path=workdir / "constraints.txt"),
    )


def test_collect_pair_refuses_an_empty_candidate(tmp_path):
    empty = CandidateReport(diff_path=tmp_path / "candidate.diff", changed_files=[],
                            out_of_scope=[], is_empty=True)
    with pytest.raises(SkepticInfraError, match="harness bug"):
        collect_pair(make_task_spec(), tmp_path / "repo", empty, tmp_path / "work")


def test_collect_pair_materializes_two_independent_trees(tmp_path, monkeypatch):
    spec, repo, candidate = _minirepo(tmp_path)
    _stub_image(monkeypatch)
    scope: dict[str, tuple[str, ...]] = {}

    def record_scope(spec, image_tag, tree, artifacts, side, changed_files):
        scope[side] = tuple(changed_files)
        return _fake_observe(spec, image_tag, tree, artifacts, side, changed_files)

    monkeypatch.setattr("skeptic.collector.observe_variant", record_scope)

    pair = collect_pair(spec, repo, candidate, tmp_path / "work")

    # Both sides observe under the same scope: the coverage report is bounded
    # by the candidate's changed files, and the baseline is measured against
    # that same list or the two reports are not comparable.
    assert scope["baseline"] == scope["candidate"] == ("minirepo.py",)
    assert pair.baseline.tree != pair.candidate.tree
    assert BUGGY in (pair.baseline.tree / "minirepo.py").read_text()
    assert PRISTINE in (pair.candidate.tree / "minirepo.py").read_text()
    assert BUGGY not in (pair.candidate.tree / "minirepo.py").read_text()
    assert pair.candidate_diff is candidate
    assert pair.artifacts_dir.is_dir()


def test_collect_pair_removes_the_pristine_build_context(tmp_path, monkeypatch):
    spec, repo, candidate = _minirepo(tmp_path)
    seen: dict[str, object] = {}

    def record_image(spec, pristine, workdir):
        seen["pristine"] = pristine
        seen["materialized"] = (pristine / "minirepo.py").is_file()
        return ImageRef(tag="img", image_id="sha256:stub",
                        constraints_path=workdir / "constraints.txt")

    def record_observe(spec, image_tag, tree, artifacts, side, changed_files):
        seen[f"pristine_at_{side}"] = seen["pristine"].exists()
        return _fake_observe(spec, image_tag, tree, artifacts, side, changed_files)

    monkeypatch.setattr("skeptic.collector.ensure_repo_image", record_image)
    monkeypatch.setattr("skeptic.collector.observe_variant", record_observe)

    collect_pair(spec, repo, candidate, tmp_path / "work")

    assert seen["materialized"] is True
    assert seen["pristine_at_baseline"] is False
    assert seen["pristine_at_candidate"] is False
    assert not seen["pristine"].exists()


def test_observe_variant_writes_artifacts_outside_the_tree(tmp_path, monkeypatch):
    spec = make_task_spec()
    calls = fake_unit(monkeypatch, collected=(NODE_A,), outcomes={NODE_A: "passed"})
    stale = _stale_artifacts(tmp_path, "baseline")
    obs = _observed(spec, tmp_path, "baseline")

    # A reused workdir starts empty: the directory is rebuilt, never topped up.
    assert not (stale / "ran-before.txt").exists()
    assert obs.artifacts == tmp_path / "artifacts" / "baseline"
    assert (obs.artifacts / "junit.xml").is_file()
    assert (obs.artifacts / "collect.out").is_file()
    assert obs.tree.resolve() not in obs.artifacts.resolve().parents
    # Every path the unit script writes to is under the mount, never the tree.
    script = calls[0][-1]
    targets = re.findall(r"[12]?>\s*(\S+)", script) + re.findall(r"--junitxml=(\S+)", script)
    assert len(targets) >= 5
    assert all(t.startswith("/artifacts/") for t in targets)
    assert not list(obs.tree.rglob("*junit*"))
    assert not list(obs.tree.rglob(".coverage*"))


def test_observe_variant_maps_exit_5_to_an_empty_observation_on_the_candidate_side(
    tmp_path, monkeypatch
):
    spec = make_task_spec()
    fake_unit(monkeypatch, collected=(), collect_exit=5, suite_exit=5)
    obs = _observed(spec, tmp_path, "candidate")

    assert obs.collected == ()
    assert obs.outcomes == {}
    assert obs.collection_errors == 0
    assert obs.collect_exit == 5
    assert obs.suite_exit == 5


def test_observe_variant_raises_infra_on_exit_5_on_the_baseline_side(tmp_path, monkeypatch):
    spec = make_task_spec()
    fake_unit(monkeypatch, collected=(), collect_exit=5, suite_exit=5)
    with pytest.raises(SkepticInfraError, match="exited 5"):
        _observed(spec, tmp_path, "baseline")


def test_observe_variant_raises_infra_on_exit_2(tmp_path, monkeypatch):
    spec = make_task_spec()
    for side in ("baseline", "candidate"):
        fake_unit(monkeypatch, collected=(NODE_A,), collect_exit=2)
        with pytest.raises(SkepticInfraError, match="exited 2"):
            _observed(spec, tmp_path / f"{side}-collect", side)
        fake_unit(monkeypatch, collected=(NODE_A,), suite_exit=2)
        with pytest.raises(SkepticInfraError, match="exited 2"):
            _observed(spec, tmp_path / f"{side}-suite", side)


def test_observe_variant_raises_infra_when_an_outcome_id_is_not_in_the_collected_set(
    tmp_path, monkeypatch
):
    spec = make_task_spec()
    fake_unit(monkeypatch, collected=(NODE_A,), outcomes={NODE_B: "passed", NODE_A: "passed"})
    with pytest.raises(SkepticInfraError, match=re.escape(NODE_B)) as exc:
        _observed(spec, tmp_path, "candidate")
    # Only the divergent id is named: the shared one is not part of the fault.
    assert str(exc.value).count(NODE_A) == 0


def test_observe_variant_passes_continue_on_collection_errors_to_both_invocations(
    tmp_path, monkeypatch
):
    spec = make_task_spec()
    calls = fake_unit(monkeypatch, collected=(NODE_A,), outcomes={NODE_A: "passed"})
    _observed(spec, tmp_path, "candidate")

    script = calls[0][-1]
    assert script.count("--continue-on-collection-errors") == 2
    collect_line = next(ln for ln in script.splitlines() if "--collect-only" in ln)
    # One quiet flag exactly. `python -m pytest -q --collect-only -q` is
    # verbosity -2, which prints `path: count` lines instead of nodeids.
    assert [t for t in shlex.split(collect_line.split(">")[0]) if t in ("-q", "--quiet")] == ["-q"]
    # `--verbosity=N` sets the same counter and escapes a token-equality
    # strip; its separated form would leave the value behind as a path.
    tail = ["--collect-only", "-q", "--continue-on-collection-errors"]
    assert _collect_argv("python -m pytest --verbosity=2") == ["python", "-m", "pytest", *tail]
    assert _collect_argv("python -m pytest --verbosity 2 -k slow") == [
        "python", "-m", "pytest", "-k", "slow", *tail]


def test_observe_variant_raises_infra_on_a_missing_baseline_ro_subpath(tmp_path, monkeypatch):
    spec = make_task_spec()
    calls = fake_unit(monkeypatch)
    with pytest.raises(SkepticInfraError, match="does not exist"):
        _observed(spec, tmp_path, "baseline", with_tests=False)
    assert calls == []


def test_observe_variant_records_a_dropped_candidate_ro_subpath_on_the_observation(
    tmp_path, monkeypatch
):
    spec = make_task_spec()
    calls = fake_unit(monkeypatch, collected=(NODE_A,), outcomes={NODE_A: "passed"})
    obs = _observed(spec, tmp_path, "candidate", with_tests=False)

    assert obs.dropped_ro_subpaths == ("tests",)
    assert (obs.artifacts / "dropped-ro-subpaths.txt").read_text().split() == ["tests"]
    assert "/workspace/tests:ro" not in " ".join(calls[0])


def test_observe_variant_distinguishes_a_failed_install_from_a_failed_step(
    tmp_path, monkeypatch
):
    """The separability invariant at the point it is hardest to hold.

    A failed overlay install leaves no `install.ok` and no step files at all,
    so the message has to name the install and neither pytest step. Past the
    brief's 12 names: the install, timeout, and unreadable-exit branches were
    the ones the named tests left uncovered.
    """
    spec = make_task_spec()
    fake_unit(monkeypatch, install_ok=False)
    # An earlier run's artifacts are on disk and every one of them is
    # consistent. Reading them back would report run 1 as run 2's observation.
    _stale_artifacts(tmp_path / "install", "candidate")
    with pytest.raises(SkepticInfraError, match="overlay install failed") as exc:
        _observed(spec, tmp_path / "install", "candidate")
    assert "collect step" not in str(exc.value)
    assert "suite step" not in str(exc.value)

    fake_unit(monkeypatch, timed_out=True)
    with pytest.raises(SkepticInfraError, match="timed out") as exc:
        _observed(spec, tmp_path / "timeout", "candidate")
    assert "environment.timeout_s" in str(exc.value)
    # Which step ate the budget, rather than the harness quoting itself back.
    assert "No step recorded an exit code" in str(exc.value)
    assert "stderr tail" not in str(exc.value)

    # A partial exit file is the same family: the container stopped while
    # writing it, and the reader gets a what/why/next over a ValueError.
    fake_unit(monkeypatch, collect_exit="")
    with pytest.raises(SkepticInfraError, match="where an exit code belongs"):
        _observed(spec, tmp_path / "truncated", "candidate")


def test_coverage_test_cmd_refuses_a_non_pytest_test_cmd():
    """A runner this rewrite does not know stops the run instead of guessing.

    Spec validation already guarantees `test_cmd` is a plain argv, so what is
    left open is which runner it names. Skeptic knows one instrumented
    spelling, and a guess at a second produces a coverage number rather than
    a refusal.
    """
    for cmd in ("pytest -q", "python -m unittest discover", "tox -e py312", ""):
        with pytest.raises(SkepticInfraError, match="python -m pytest") as exc:
            coverage_test_cmd(cmd)
        assert "Next:" in str(exc.value)


def test_coverage_test_cmd_rewrites_only_the_leading_python_m():
    assert coverage_test_cmd("python -m pytest -q") == [
        "python", "-m", "coverage", "run", "-m", "pytest", "-q"]
    # The second `-m` is pytest's marker selector and stays a pytest argument:
    # rewriting it would hand coverage a module name it cannot import.
    assert coverage_test_cmd('python -m pytest -q -m "not slow"') == [
        "python", "-m", "coverage", "run", "-m", "pytest", "-q", "-m", "not slow"]


def test_render_coverage_rc_sets_dynamic_context_and_source_from_src_dirs():
    rc = render_coverage_rc(make_task_spec(), "/artifacts/.coverage")

    assert "dynamic_context = test_function" in rc
    # click's own pyproject sets branch = true and a two-entry source. Both
    # are overridden here, and both are written rather than defaulted.
    assert "\nsource =\n    src/click\n" in rc
    assert "branch = false" in rc
    # The report's file keys have to be the diff's paths, which is what
    # relative_files buys: the container works in /workspace.
    assert "relative_files = true" in rc


def test_render_coverage_rc_puts_the_data_file_under_artifacts(tmp_path, monkeypatch):
    """The rc the collector actually writes, read back off the artifacts mount.

    `render_coverage_rc` takes the data file as an argument, so the claim in
    this test's name belongs to the caller: `observe_variant` writes the rc
    outside the judged tree and points coverage at a data file next to it,
    and COVERAGE_RCFILE is the only mechanism that carries either.
    """
    spec = make_task_spec()
    calls = fake_unit(monkeypatch, collected=(NODE_A,), outcomes={NODE_A: "passed"})
    obs = _observed(spec, tmp_path, "candidate")

    rc = (obs.artifacts / "coveragerc").read_text()
    assert "data_file = /artifacts/.coverage" in rc
    assert not list(obs.tree.rglob("coveragerc"))
    argv = calls[0]
    assert "COVERAGE_RCFILE=/artifacts/coveragerc" in argv
    # One mechanism: no --rcfile anywhere, so the pin governs the report as
    # well as the run.
    assert "--rcfile" not in " ".join(argv)


def test_read_coverage_scopes_to_changed_files(tmp_path):
    sample = json.loads((COVERAGE_SAMPLE / "coverage.json").read_text())
    # Not vacuous: the captured run measured a second file.
    assert set(sample["files"]) == {"minirepo.py", "tests/test_minirepo.py"}

    report = read_coverage(_sample_artifacts(tmp_path), ["minirepo.py"])

    assert report.measured_files == ("minirepo.py",)
    assert set(report.statements) == set(report.executed) == {"minirepo.py"}
    assert set(report.contexts) == {"minirepo.py"}
    # The gold tree runs every statement in the file, so the two agree here
    # and the statement set is still the wider one by construction.
    assert set(report.statements["minirepo.py"]) >= set(report.executed["minirepo.py"])
    assert 6 in report.executed["minirepo.py"]


def test_read_coverage_preserves_per_line_contexts(tmp_path):
    report = read_coverage(_sample_artifacts(tmp_path), ["minirepo.py"])
    contexts = report.contexts["minirepo.py"]

    # `parse_range`'s body runs under the three tests that call it, named
    # module.function, with parametrizations collapsed (DECISIONS row 82).
    # The module is pytest's, so `tests/` contributes no package prefix.
    assert set(contexts[6]) == {
        "test_minirepo.test_parse_range_basic",
        "test_minirepo.test_parse_range_wide",
        "test_golden.test_golden_render_matches_expected",
    }
    # The empty context is import time and it survives the read: Task 13
    # scores a statement that only ever ran at import as uncovered, and it
    # cannot tell that from covered if the read drops the empty string.
    assert contexts[4] == ("",)
    # A context line is not always a statement. coverage traces the module
    # docstring and leaves it out of the analysis, so line 1 carries a
    # context and belongs to no statement list.
    assert contexts[1] == ("",)
    assert 1 not in report.statements["minirepo.py"]

    # `run_contexts` comes from the data file's context table rather than from
    # the scoped JSON, so it describes the run where everything else here
    # describes the patch: five distinct contexts over the four files the run
    # measured, against the one file this report carries. Task 13 reads it to
    # tell a `dynamic_context` that was never honored, where this tuple would
    # be `("",)`, from a patch that ran at import time only.
    assert report.run_contexts == (
        "",
        "test_golden.test_golden_render_matches_expected",
        "test_minirepo.test_clamp_bounds",
        "test_minirepo.test_parse_range_basic",
        "test_minirepo.test_parse_range_wide",
    )
    assert report.measured_files == ("minirepo.py",)


def test_observe_variant_instruments_the_suite_and_reports_on_the_changed_files(
    tmp_path, monkeypatch
):
    """The wiring, past the brief's six names: one run, three steps, one report.

    The named fast tests cover the three functions and the docker tests cover
    the container. This is the seam between them, and it is where a rewrite
    that never reached the script would go unnoticed.
    """
    spec = make_task_spec()
    calls = fake_unit(monkeypatch, collected=(NODE_A,), outcomes={NODE_A: "passed"})
    obs = _observed(spec, tmp_path, "candidate",
                    changed_files=("src/click/mod.py", "src/click/notes.md",
                                   "tests/test_mod.py"))

    script = calls[0][-1]
    suite = next(ln for ln in script.splitlines() if "--junitxml" in ln)
    assert suite.startswith("python -m coverage run -m pytest ")
    # One suite run: the junit and the coverage data come from the same
    # command, so a candidate cannot be observed twice and differenced.
    assert script.count("--junitxml") == 1
    assert script.count("coverage run") == 1
    # The report asks about the measurable files and no others: a golden or a
    # config file is not Python, and a test file is outside this spec's
    # src_dirs, so the rc would never have measured it.
    report = next(ln for ln in script.splitlines() if "coverage json" in ln)
    assert "--include=src/click/mod.py " in report
    assert "notes.md" not in report and "tests/test_mod.py" not in report
    assert "--show-contexts" in report

    assert obs.coverage.measured_files == ("src/click/mod.py",)
    assert obs.coverage.statements["src/click/mod.py"] == (1, 4, 5)
    assert obs.coverage.executed["src/click/mod.py"] == (1, 4)
    assert obs.coverage.run_contexts == ("", "test_x.test_a")


def test_observe_variant_leaves_coverage_unobserved_when_no_report_lands(
    tmp_path, monkeypatch
):
    """A patch with nothing measurable in it, and the absent-report case with it.

    `coverage json --include=` over a pattern list that matches nothing exits
    1 with "No data to report", so a patch whose files the rc would never
    measure gets no report step at all. That is what makes an absent report
    mean one thing: nothing here is measurable. Unobserved is None, and Task
    13 decides what an unobserved one means; the collector does not turn it
    into a verdict.

    Both shapes are here. A golden is not Python, and a test file is Python
    outside this spec's `src_dirs`, which is the case a repo like click has
    and the minirepo (`src_dirs: ["."]`) cannot show.
    """
    spec = make_task_spec()
    for case, changed in enumerate((("goldens/expected.txt",),
                                    ("tests/test_mod.py", "README.md"))):
        calls = fake_unit(monkeypatch, collected=(NODE_A,), outcomes={NODE_A: "passed"})
        obs = _observed(spec, tmp_path / f"case{case}", "candidate", changed_files=changed)

        assert "coverage json" not in calls[0][-1]
        assert obs.coverage is None


def test_apply_candidate_error_names_the_verify_context(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "mod.py").write_text("def add(a, b):\n    return a + b\n")
    good = tmp_path / "good.diff"
    good.write_text(
        "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n"
        "-    return a + b\n+    return a + b + 1\n"
    )
    apply_candidate(tree, good)
    assert "a + b + 1" in (tree / "mod.py").read_text()

    bad = tmp_path / "bad.diff"
    bad.write_text(
        "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n-def NOT_THERE():\n+def x():\n     pass\n"
    )
    with pytest.raises(SkepticInfraError) as exc:
        apply_candidate(tree, bad)
    message = str(exc.value)
    assert "seed --check" not in message
    assert "task invariant" not in message
    assert "harness bug" in message
    assert "bad.diff" in message


@pytest.mark.docker
@pytest.mark.slow
def test_collect_pair_on_the_minirepo_gold_fixture(tmp_path, minirepo_spec_and_repo):
    """Both sides for real: two trees, two containers, one image.

    Wall time for the pair is what `--durations` reports for this test, minus
    the one-time image build the session fixture shares.
    """
    spec, repo_dir = minirepo_spec_and_repo

    pair = collect_pair(spec, repo_dir, _gold_candidate(spec), tmp_path / "work")

    seeded_red = set(spec.seed.failing_tests)
    assert {k for k, v in pair.baseline.outcomes.items() if v in ("failed", "error")} == seeded_red
    assert all(pair.candidate.outcomes[nodeid] == "passed" for nodeid in seeded_red)
    assert set(pair.candidate.collected) == set(pair.baseline.collected)
    assert set(pair.baseline.collected) >= seeded_red
    assert pair.baseline.suite_exit == 1 and pair.candidate.suite_exit == 0
    assert pair.baseline.collect_exit == 0 and pair.candidate.collect_exit == 0
    assert pair.baseline.collection_errors == 0 and pair.candidate.collection_errors == 0
    assert pair.candidate.dropped_ro_subpaths == ()
    for side in (pair.baseline, pair.candidate):
        assert not list(side.tree.rglob("*junit*"))
        assert (side.artifacts / "junit.xml").is_file()
        assert (side.artifacts / "collect.exit").read_text().strip() == "0"


@pytest.mark.docker
@pytest.mark.slow
def test_instrumented_run_writes_junit_and_coverage_to_artifacts(
    tmp_path, minirepo_spec_and_repo
):
    """One run per variant produces both readings, and neither lands in the tree.

    The suite is the coverage run, so the junit report and the data file are
    two products of one command. A second, uninstrumented pass would be a
    second observation of the same tree, and nothing would make the two agree.
    """
    spec, repo_dir = minirepo_spec_and_repo

    pair = collect_pair(spec, repo_dir, _gold_candidate(spec), tmp_path / "work")

    for side in (pair.baseline, pair.candidate):
        assert (side.artifacts / "junit.xml").is_file()
        assert (side.artifacts / ".coverage").is_file()
        assert (side.artifacts / "coverage.json").is_file()
        assert (side.artifacts / "coveragerc").is_file()
        assert (side.artifacts / "coverage.exit").read_text().strip() == "0"
        # Everything coverage wrote is on the artifacts mount. A data file in
        # the tree would be one more path the candidate diff has to ignore.
        assert not list(side.tree.rglob(".coverage*"))
        assert not list(side.tree.rglob("coveragerc"))

    coverage = pair.candidate.coverage
    assert coverage.measured_files == ("minirepo.py",)
    # The gold patch's line, and the three tests that run it. Contexts are the
    # reason the rc is pinned: without dynamic_context every line here would
    # carry one empty string.
    assert 6 in coverage.executed["minirepo.py"]
    assert len(coverage.contexts["minirepo.py"][6]) == 3
    assert all(ctx for ctx in coverage.contexts["minirepo.py"][6])
    # The whole-run list, read from the data file rather than the report: one
    # entry per test that ran plus the import-time empty string. `("",)` alone
    # would mean dynamic_context never took effect.
    assert coverage.run_contexts[0] == ""
    assert len(coverage.run_contexts) == 5
    # The repo's own config never gets a say. The minirepo carries none, so
    # what this proves is that the pin is what the run read: click's
    # `branch = true` would put branch counts in the report.
    assert json.loads((pair.candidate.artifacts / "coverage.json").read_text())[
        "meta"]["branch_coverage"] is False


@pytest.mark.docker
@pytest.mark.slow
def test_instrumented_run_leaves_the_outcome_map_unchanged(
    tmp_path, minirepo_spec_and_repo, monkeypatch
):
    """The tracer changes the timing and nothing else, measured both ways.

    Coverage instruments both variants, so a tracer that moved an outcome
    would move it on both sides and the differential checks would subtract
    the change out. It would still be wrong: the candidate's real behavior is
    what T1 reports on. This runs the same fixture with the rewrite disabled
    and compares the two readings directly.
    """
    spec, repo_dir = minirepo_spec_and_repo
    candidate = _gold_candidate(spec)

    monkeypatch.setattr("skeptic.collector.coverage_test_cmd", shlex.split)
    plain = collect_pair(spec, repo_dir, candidate, tmp_path / "plain")
    monkeypatch.undo()
    instrumented = collect_pair(spec, repo_dir, candidate, tmp_path / "instrumented")

    for old, new in ((plain.baseline, instrumented.baseline),
                     (plain.candidate, instrumented.candidate)):
        assert new.collected == old.collected
        assert new.outcomes == old.outcomes
        assert new.suite_exit == old.suite_exit
        assert new.collect_exit == old.collect_exit
        assert new.collection_errors == old.collection_errors
    # Not vacuous: the uninstrumented run wrote no data file to read.
    assert plain.candidate.coverage is None
    assert instrumented.candidate.coverage is not None
