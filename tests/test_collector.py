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

from skeptic.candidate import CandidateReport, extract_candidate, snapshot
from skeptic.checks.observations import AdvCandidate, VariantObservations
from skeptic.collector import (
    _ADV_CP_FAILED,
    _LADDER_EXIT_DETAIL,
    _advtest_reference_script,
    _advtest_script,
    _collect_argv,
    _coverage_report_step_detail,
    _gold_prime_rung_detail,
    _read_ladder_exit,
    _reference_outcome_detail,
    _rescreen_import_rejections,
    _seeded_rung_detail,
    _target_coverage_rung_detail,
    _tree_allowed_packages,
    collect_pair,
    coverage_test_cmd,
    observe_advtests,
    observe_variant,
    read_coverage,
    render_coverage_rc,
)
from skeptic.errors import SkepticInfraError
from skeptic.image import ImageRef
from skeptic.sandbox import ExecResult
from skeptic.spec import TaskSpec
from skeptic.workspace import apply_candidate, apply_patch, clone_pinned, materialize
from tests.helpers import (
    BUGGY,
    FIXTURE,
    PRISTINE,
    apply_fixture,
    make_minirepo_task,
    make_task_spec,
)

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


def test_apply_candidate_authored_blames_the_patch_not_the_harness(tmp_path):
    """M6 finding 7: a --variant-patch run's patch is hand-authored blind
    against the seeded tree, never extracted from a workspace this harness
    built, so "a failure here is a harness bug" is the wrong first thing to
    tell a reader. Same split `apply_audited_diff` makes for the diff lane."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "mod.py").write_text("def add(a, b):\n    return a + b\n")
    bad = tmp_path / "holdout.diff"
    bad.write_text(
        "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n-def NOT_THERE():\n+def x():\n     pass\n"
    )

    with pytest.raises(SkepticInfraError) as exc:
        apply_candidate(tree, bad, authored=True)
    message = str(exc.value)
    assert "harness bug" not in message
    assert "different tree" in message
    assert "holdout.diff" in message
    assert "Next:" in message


def test_collect_pair_reuses_a_keyed_baseline(tmp_path, monkeypatch):
    spec, repo, candidate = _minirepo(tmp_path)
    _stub_image(monkeypatch)
    calls = fake_unit(monkeypatch, collected=(NODE_A,), outcomes={NODE_A: "passed"})
    baseline_cache = tmp_path / "baseline-cache"

    first = collect_pair(spec, repo, candidate, tmp_path / "work1",
                         baseline_cache=baseline_cache)
    assert len(calls) == 2          # one baseline container, one candidate container

    second = collect_pair(spec, repo, candidate, tmp_path / "work2",
                          baseline_cache=baseline_cache)
    assert len(calls) == 3          # only the candidate ran; the baseline was reused
    assert second.baseline.outcomes == first.baseline.outcomes
    assert second.baseline.tree == first.baseline.tree


def test_baseline_key_includes_changed_files(tmp_path, monkeypatch):
    spec, repo, candidate = _minirepo(tmp_path)
    _stub_image(monkeypatch)
    calls = fake_unit(monkeypatch, collected=(NODE_A,), outcomes={NODE_A: "passed"})
    baseline_cache = tmp_path / "baseline-cache"

    collect_pair(spec, repo, candidate, tmp_path / "work1", baseline_cache=baseline_cache)
    assert len(calls) == 2

    different = CandidateReport(diff_path=candidate.diff_path, changed_files=["other.py"],
                                out_of_scope=[], is_empty=False)
    collect_pair(spec, repo, different, tmp_path / "work2", baseline_cache=baseline_cache)
    assert len(calls) == 4          # a different changed-files scope misses the baseline cache


# The adversarial-test acceptance ladder (Task 6).


def test_advtest_script_copies_candidates_in_and_removes_scratch_last():
    script = _advtest_script(make_task_spec(), "seeded", ["c1", "c2"])
    lines = script.splitlines()

    # The shared scratch dir is set up once, before either candidate's own
    # guarded copy.
    assert lines[2] == "mkdir -p .skeptic-advtests"
    for cid in ("c1", "c2"):
        copy_i = next(i for i, ln in enumerate(lines)
                      if ln.strip().startswith("if cp") and f"test_{cid}.py" in ln)
        run_i = next(i for i, ln in enumerate(lines)
                    if ln.strip().startswith("timeout") and f"test_{cid}.py" in ln)
        assert copy_i < run_i
    # The scratch dir comes off last, after every candidate has run.
    assert lines[-1] == "rm -rf .skeptic-advtests"
    assert lines.count("rm -rf .skeptic-advtests") == 1


def test_advtest_script_never_touches_paths_outside_scratch():
    script = _advtest_script(make_task_spec(), "gold", ["c1"])

    for raw in script.splitlines():
        line = raw.strip()
        if line.startswith("if cp "):
            tokens = line.split()
            src, dest = tokens[2], tokens[3].rstrip(";")
            assert src.startswith("/artifacts/")
            assert dest.startswith(".skeptic-advtests/")
        elif line in ("else", "fi"):
            continue
        elif ">" in line:
            # Every redirect target (`> x`, `2> y`) sits under /artifacts,
            # whether it is the timeout-wrapped run or the guard's own
            # `echo cp-failed > .../exit` line.
            targets = re.findall(r"[12]?>\s*(\S+)", line)
            assert targets, line
            assert all(t.startswith("/artifacts/") for t in targets)
        elif line.startswith(("mkdir -p", "rm -rf")):
            target = line.split(None, 2)[-1]
            assert target.startswith("/artifacts/") or target == ".skeptic-advtests"


def test_advtest_script_guards_the_copy_in_with_a_cp_failed_sentinel():
    script = _advtest_script(make_task_spec(), "gold", ["c1"])
    lines = script.splitlines()

    guard_i = next(i for i, ln in enumerate(lines) if ln.strip().startswith("if cp "))
    assert lines[guard_i].strip().endswith("; then")
    assert lines[guard_i + 1].strip().startswith("timeout")
    else_i = next(i for i, ln in enumerate(lines) if ln.strip() == "else")
    assert else_i > guard_i
    assert lines[else_i + 1].strip() == f"echo {_ADV_CP_FAILED} > /artifacts/gold/c1/exit"
    assert lines[else_i + 2].strip() == "fi"


def test_advtest_reference_script_wraps_coverage_and_junit():
    spec = make_task_spec()
    script = _advtest_reference_script(spec, ["c1"], ["src/click/mod.py"])

    assert "python -m coverage run" in script
    assert "--junitxml=/artifacts/reference/c1/junit.xml" in script
    assert "COVERAGE_RCFILE=/artifacts/reference/c1/coveragerc" in script
    report_line = next(ln for ln in script.splitlines() if "coverage json" in ln)
    assert "--show-contexts" in report_line
    assert "--include=src/click/mod.py" in report_line
    assert "-o /artifacts/reference/c1/coverage.json" in report_line
    # The report step reads already-collected data rather than running
    # candidate code, so only the suite step carries its own timeout: with
    # both capped, the tree's own worst case would double what the shared
    # `n * 60 + 120` host-budget formula accounts for.
    assert "timeout" not in report_line
    suite_line = next(ln for ln in script.splitlines() if "--junitxml" in ln)
    assert suite_line.strip().startswith("COVERAGE_RCFILE=/artifacts/reference/c1/coveragerc timeout")
    assert script.splitlines()[-1] == "rm -rf .skeptic-advtests"
    # Guarded the same way the plain script's copy-in is.
    assert any(ln.strip().startswith("if cp ") for ln in script.splitlines())
    assert f"echo {_ADV_CP_FAILED} > /artifacts/reference/c1/exit" in script


def test_advtest_reference_script_skips_the_report_step_with_no_measurable_file():
    script = _advtest_reference_script(make_task_spec(), ["c1"], [])

    assert "coverage json" not in script
    assert "python -m coverage run" in script  # rung 1 still runs coverage-wrapped


def test_advtest_rung_reader_rejects_skipped_candidates():
    detail = _reference_outcome_detail({".skeptic-advtests/test_c1.py::test_a": "skipped"})

    assert detail is not None
    assert "skipped" in detail


def test_advtest_rung_reader_accepts_an_all_passed_outcome_map():
    assert _reference_outcome_detail({".skeptic-advtests/test_c1.py::test_a": "passed"}) is None


def test_advtest_rung_reader_rejects_empty_collection():
    detail = _reference_outcome_detail({})

    assert detail is not None
    assert "no tests" in detail


def test_advtest_rung_reader_maps_seeded_exit_zero_to_non_discriminating():
    detail = _seeded_rung_detail(0)

    assert detail is not None
    assert "non-discriminating" in detail
    assert _seeded_rung_detail(1) is None


def test_advtest_rung_reader_maps_gold_prime_exit_zero_to_pass():
    assert _gold_prime_rung_detail(0) is None
    assert _gold_prime_rung_detail(1) == "failed"


def test_advtest_rung_reader_maps_timeout_to_rejection_not_infra():
    detail = _seeded_rung_detail(124)

    assert detail is not None
    assert "timeout" in detail


def test_advtest_unknown_exit_is_infra(tmp_path):
    exit_path = tmp_path / "exit"
    exit_path.write_text("137\n")

    with pytest.raises(SkepticInfraError, match="does not name"):
        _read_ladder_exit(exit_path, who="candidate c1 on the reference tree")


def test_advtest_ladder_exit_reader_raises_on_a_missing_file(tmp_path):
    with pytest.raises(SkepticInfraError, match="left no exit code"):
        _read_ladder_exit(tmp_path / "exit", who="candidate c1 on the seeded tree")


def test_advtest_ladder_exit_reader_accepts_every_contract_code(tmp_path):
    for code in (0, 1, 2, 3, 4, 5, 124):
        path = tmp_path / f"exit-{code}"
        path.write_text(f"{code}\n")
        assert _read_ladder_exit(path, who="x") == code


def test_advtest_coverage_rung_requires_changed_file_context(tmp_path):
    import_time_only = {
        "minirepo.py": {"executed_lines": [1, 4], "missing_lines": [],
                        "excluded_lines": [], "contexts": {"1": [""], "4": [""]}},
    }
    ran_under_a_test = {
        "minirepo.py": {"executed_lines": [1, 4], "missing_lines": [],
                        "excluded_lines": [], "contexts": {"1": [""], "4": ["test_c1.test_x"]}},
    }

    artifacts = tmp_path / "import-only"
    artifacts.mkdir()
    (artifacts / "coverage.json").write_text(_coverage_json(import_time_only))
    _write_coverage_data(artifacts / ".coverage", ("",))
    report = read_coverage(artifacts, ["minirepo.py"])
    assert _target_coverage_rung_detail(report) is not None

    artifacts2 = tmp_path / "real-test"
    artifacts2.mkdir()
    (artifacts2 / "coverage.json").write_text(_coverage_json(ran_under_a_test))
    _write_coverage_data(artifacts2 / ".coverage", ("", "test_c1.test_x"))
    report2 = read_coverage(artifacts2, ["minirepo.py"])
    assert _target_coverage_rung_detail(report2) is None


def test_advtest_coverage_report_step_exit_disambiguates_no_data_from_other_failures():
    # Exit 1 is `coverage json`'s own "No data to report".
    assert _coverage_report_step_detail(1) == (
        "the coverage report recorded no data for the changed files")
    # Every other code in the ladder's exit contract is a different finding
    # (DECISIONS row 124's own distinction for _guard_calibration, restated):
    # conflating a timeout with "no data" sends the next reader to the wrong
    # artifact.
    assert _coverage_report_step_detail(124) == _LADDER_EXIT_DETAIL[124]
    assert "timeout" in _coverage_report_step_detail(124)
    for code in (2, 3, 4, 5):
        assert _coverage_report_step_detail(code) == _LADDER_EXIT_DETAIL[code]
        assert "no data" not in _coverage_report_step_detail(code)


def test_advtest_cp_failed_sentinel_routes_to_whole_observation_infra(tmp_path):
    exit_path = tmp_path / "exit"
    exit_path.write_text(f"{_ADV_CP_FAILED}\n")

    with pytest.raises(SkepticInfraError, match="copy from the shared mount"):
        _read_ladder_exit(exit_path, who="candidate c1 on the reference tree")


def test_advtest_tree_allowed_packages_finds_a_flat_module_the_basename_heuristic_misses(tmp_path):
    tasks_dir, task_id = make_minirepo_task(tmp_path)
    from skeptic.spec import find_task
    spec = find_task(task_id, tasks_dir)
    repo = clone_pinned(spec.repo.url, spec.repo.commit, tmp_path / "cache")
    tree = materialize(repo, spec.repo.commit, tmp_path / "tree")

    packages = _tree_allowed_packages(tree, spec)

    # src_dirs: ["."] basenames to ".", which imports nothing; the real
    # listing finds the target module the heuristic cannot.
    assert packages == {"minirepo"}
    assert "tests" not in packages  # excluded via environment.test_dirs
    assert "conftest" not in packages


def test_advtest_tree_allowed_packages_root_src_dir_never_names_the_tree_directory(tmp_path):
    """`src_dirs: ["."]` whose root DOES carry `__init__.py` must not read
    back this function's own materialization directory name: `Path / "."`
    normalizes away, so `root.name` would otherwise be an artifact of
    wherever the caller happened to put the tree (reviewer Minor 3).
    """
    tree = tmp_path / "some-materialization-dir-name"
    tree.mkdir()
    (tree / "__init__.py").write_text("")
    (tree / "mod.py").write_text("x = 1\n")
    spec = make_task_spec().model_copy(update={
        "environment": make_task_spec().environment.model_copy(update={"src_dirs": ["."]})})

    packages = _tree_allowed_packages(tree, spec)

    assert "some-materialization-dir-name" not in packages
    assert packages == {"mod"}


def test_advtest_rescreen_recovers_a_basename_false_rejection():
    rejected = AdvCandidate(
        candidate_id="c1", source="import minirepo\n\n\ndef test_x():\n    assert True\n",
        status="rejected", rejected_at="import_screen",
        detail="import of 'minirepo', which is not stdlib, pytest, or a package under src_dirs")
    unrelated = AdvCandidate(
        candidate_id="c2", source="import numpy\n\n\ndef test_y():\n    pass\n",
        status="rejected", rejected_at="import_screen", detail="import of 'numpy'")

    rescreened = _rescreen_import_rejections((rejected, unrelated), frozenset({"minirepo"}))

    assert rescreened[0].status == "trusted"
    assert rescreened[0].rejected_at is None
    assert rescreened[1] is unrelated  # numpy is still not allowed; passes through unchanged


def _advtest_fake_runs(monkeypatch, plans: list[dict[str, dict]]):
    """Answer each expected `container.run` call with the next plan entry, in
    order: reference, seeded, each clean variant, then the candidate tree.

    Each plan entry maps candidate id to the files that candidate's run
    should produce: `exit` always, `outcomes` for a junit report, and
    `coverage_exit`/`covered` for the reference tree's report step.
    """
    state = {"i": 0}

    def fake_run(cmd, cwd, timeout_s, env):
        script = cmd[-1]
        mount = next(a for a in cmd if a.endswith(":/artifacts:rw"))
        host_root = Path(mount.split(":")[0])
        install_line = next(
            ln for ln in script.splitlines() if ln.startswith("echo ok > /artifacts/"))
        tree_key = install_line.removeprefix("echo ok > /artifacts/").removesuffix("/install.ok")
        plan = plans[state["i"]]
        state["i"] += 1
        tree_dir = host_root / tree_key
        tree_dir.mkdir(parents=True, exist_ok=True)
        (tree_dir / "install.ok").write_text("ok\n")
        for cid, spec_ in plan.items():
            cdir = tree_dir / cid
            cdir.mkdir(parents=True, exist_ok=True)
            (cdir / "out").write_text("")
            (cdir / "err").write_text("")
            (cdir / "exit").write_text(f"{spec_['exit']}\n")
            if "outcomes" in spec_:
                (cdir / "junit.xml").write_text(_junit(spec_["outcomes"]))
            if "coverage_exit" in spec_:
                (cdir / "coverage.exit").write_text(f"{spec_['coverage_exit']}\n")
                (cdir / "coverage.out").write_text("")
                (cdir / "coverage.err").write_text("")
                if spec_["coverage_exit"] == 0:
                    (cdir / "coverage.json").write_text(_coverage_json(spec_["covered"]))
                    _write_coverage_data(cdir / ".coverage", spec_.get("contexts", ("",)))
        return ExecResult(0, "", "", 500)

    monkeypatch.setattr("skeptic.sandbox._run", fake_run)


def test_advtest_report_orders_trusted_and_divergences_by_candidate(tmp_path, monkeypatch):
    """Input order (c2, c1, c3), never sorted: c2 diverges, c1 rejects at the
    reference rung, c3 clears the ladder clean. `trusted` and `candidates`
    both keep the input's relative order rather than any processing order.
    """
    spec, repo, _ = _minirepo(tmp_path)
    covered = {"minirepo.py": {"executed_lines": [1, 6], "missing_lines": [],
                               "excluded_lines": [], "contexts": {"1": [""], "6": ["t.x"]}}}
    candidates = (
        AdvCandidate(candidate_id="c2", source="def test_b():\n    pass\n",
                     status="trusted", rejected_at=None, detail="provisional"),
        AdvCandidate(candidate_id="c1", source="def test_a():\n    assert False\n",
                     status="trusted", rejected_at=None, detail="provisional"),
        AdvCandidate(candidate_id="c3", source="def test_c():\n    pass\n",
                     status="trusted", rejected_at=None, detail="provisional"),
    )
    reference_plan = {
        "c2": {"exit": 0, "outcomes": {".skeptic-advtests/test_c2.py::test_b": "passed"},
              "coverage_exit": 0, "covered": covered},
        "c1": {"exit": 1, "outcomes": {".skeptic-advtests/test_c1.py::test_a": "failed"}},
        "c3": {"exit": 0, "outcomes": {".skeptic-advtests/test_c3.py::test_c": "passed"},
              "coverage_exit": 0, "covered": covered},
    }
    # Every tree run covers every still-provisional candidate, c1 included,
    # even though it already failed `reference`: the ladder does not shrink
    # the batch between rungs, only the final candidate-tree run does.
    seeded_plan = {"c1": {"exit": 0}, "c2": {"exit": 1}, "c3": {"exit": 1}}
    gold_plan = {"c1": {"exit": 0}, "c2": {"exit": 0}, "c3": {"exit": 0}}
    candidate_plan = {
        "c2": {"exit": 1, "outcomes": {".skeptic-advtests/test_c2.py::test_b": "failed"}},
        "c3": {"exit": 0},
    }
    _advtest_fake_runs(monkeypatch, [reference_plan, seeded_plan, gold_plan, candidate_plan])

    # `make_observed_pair` builds a pair with no tree materialized and no
    # container started; `observe_advtests` only reads `candidate.tree` as a
    # mount path for the final run, and that run's own container is faked
    # above, so the unmaterialized path is never actually touched.
    from tests.helpers import make_observed_pair
    pair = make_observed_pair(baseline={}, spec=spec)
    pair = pair.model_copy(update={
        "candidate_diff": CandidateReport(
            diff_path=pair.candidate_diff.diff_path, changed_files=["minirepo.py"],
            out_of_scope=[], is_empty=False)})

    report = observe_advtests(
        spec, "img", repo, pair, tmp_path / "artifacts", candidates, model="test-model")

    assert report.trusted == ("c2", "c3")
    assert [c.candidate_id for c in report.candidates] == ["c2", "c1", "c3"]
    by_id = {c.candidate_id: c for c in report.candidates}
    assert by_id["c1"].status == "rejected"
    assert by_id["c1"].rejected_at == "reference"
    assert by_id["c2"].status == "trusted"
    assert by_id["c3"].status == "trusted"
    assert len(report.divergences) == 1
    assert report.divergences[0].candidate_id == "c2"
    assert report.divergences[0].nodeids == (".skeptic-advtests/test_c2.py::test_b",)
    assert report.n_candidates == spec.verification.adversarial_tests.n_candidates
    # Task 3's forward requirement, restated as the literal invariant: the
    # model validator only checks trusted is a subset of trusted-status ids;
    # producer correctness (the equality) is this function's to pin.
    assert report.trusted == tuple(
        c.candidate_id for c in report.candidates if c.status == "trusted")


def test_advtest_gold_prime_rung_short_circuits_after_the_first_rejected_variant(
    tmp_path, monkeypatch
):
    """Two clean variants (`gold`, `gold2`): `gold` rejects c1 and the loop
    never reads a second exit file for it against `gold2`; c2 passes `gold`
    but fails `gold2`, proving the loop keeps checking later variants for a
    candidate that has not failed yet; c3 clears both.
    """
    pristine_source = (FIXTURE / "minirepo.py").read_text()
    tasks_dir, task_id = make_minirepo_task(
        tmp_path, extra_variants=[("gold2", "clean", {"minirepo.py": pristine_source})])
    from skeptic.spec import find_task
    spec = find_task(task_id, tasks_dir)
    repo = clone_pinned(spec.repo.url, spec.repo.commit, tmp_path / "cache")

    covered = {"minirepo.py": {"executed_lines": [1, 6], "missing_lines": [],
                               "excluded_lines": [], "contexts": {"1": [""], "6": ["t.x"]}}}
    candidates = tuple(
        AdvCandidate(candidate_id=cid, source=f"def test_{cid}():\n    pass\n",
                     status="trusted", rejected_at=None, detail="provisional")
        for cid in ("c1", "c2", "c3")
    )
    reference_plan = {
        cid: {"exit": 0, "outcomes": {f".skeptic-advtests/test_{cid}.py::test_x": "passed"},
             "coverage_exit": 0, "covered": covered}
        for cid in ("c1", "c2", "c3")
    }
    seeded_plan = {cid: {"exit": 1} for cid in ("c1", "c2", "c3")}
    gold_plan = {"c1": {"exit": 1}, "c2": {"exit": 0}, "c3": {"exit": 0}}
    # c1 is deliberately absent from gold2's plan: the loop must reject c1 at
    # "gold" and never attempt to read a second exit file for it.
    gold2_plan = {"c2": {"exit": 1}, "c3": {"exit": 0}}
    candidate_plan = {"c3": {"exit": 0}}
    _advtest_fake_runs(
        monkeypatch, [reference_plan, seeded_plan, gold_plan, gold2_plan, candidate_plan])

    from tests.helpers import make_observed_pair
    pair = make_observed_pair(baseline={}, spec=spec)
    pair = pair.model_copy(update={
        "candidate_diff": CandidateReport(
            diff_path=pair.candidate_diff.diff_path, changed_files=["minirepo.py"],
            out_of_scope=[], is_empty=False)})

    report = observe_advtests(
        spec, "img", repo, pair, tmp_path / "artifacts", candidates, model="test-model")

    by_id = {c.candidate_id: c for c in report.candidates}
    assert by_id["c1"].status == "rejected"
    assert by_id["c1"].rejected_at == "gold_prime"
    assert "gold" in by_id["c1"].detail
    assert "gold2" not in by_id["c1"].detail
    assert by_id["c2"].status == "rejected"
    assert by_id["c2"].rejected_at == "gold_prime"
    assert "gold2" in by_id["c2"].detail
    assert by_id["c3"].status == "trusted"
    assert report.trusted == ("c3",)
    assert report.trusted == tuple(
        c.candidate_id for c in report.candidates if c.status == "trusted")


def test_rejected_at_names_the_first_rung_not_the_last(tmp_path, monkeypatch):
    """`_ADV_RUNG_ORDER`'s middle two entries, `target_coverage` and
    `seeded_green`, are only pinned today for `reference`-before-the-rest.

    A trivial `assert 1 + 1 == 2` candidate fails both: it never executes
    the changed file (rung `target_coverage`) and it passes the seeded
    (buggy) tree, non-discriminating (rung `seeded_green`). `rejected_at`
    has to name the earlier rung in ladder order, `target_coverage`, not
    whichever of the two the reader happens to check first.
    """
    spec, repo, _ = _minirepo(tmp_path)
    candidates = (
        AdvCandidate(candidate_id="c1", source="def test_trivial():\n    assert 1 + 1 == 2\n",
                     status="trusted", rejected_at=None, detail="provisional"),
    )
    # Every changed-file context is the empty string: import-time only,
    # never executed under a real test, so target_coverage rejects it.
    no_context = {"minirepo.py": {"executed_lines": [1, 6], "missing_lines": [],
                                  "excluded_lines": [], "contexts": {"1": [""], "6": [""]}}}
    reference_plan = {
        "c1": {"exit": 0, "outcomes": {".skeptic-advtests/test_c1.py::test_trivial": "passed"},
              "coverage_exit": 0, "covered": no_context},
    }
    # Exit 0 on the seeded (buggy) tree: non-discriminating, so seeded_green
    # rejects it too.
    seeded_plan = {"c1": {"exit": 0}}
    gold_plan = {"c1": {"exit": 0}}
    _advtest_fake_runs(monkeypatch, [reference_plan, seeded_plan, gold_plan])

    from tests.helpers import make_observed_pair
    pair = make_observed_pair(baseline={}, spec=spec)
    pair = pair.model_copy(update={
        "candidate_diff": CandidateReport(
            diff_path=pair.candidate_diff.diff_path, changed_files=["minirepo.py"],
            out_of_scope=[], is_empty=False)})

    report = observe_advtests(
        spec, "img", repo, pair, tmp_path / "artifacts", candidates, model="test-model")

    by_id = {c.candidate_id: c for c in report.candidates}
    assert by_id["c1"].status == "rejected"
    assert by_id["c1"].rejected_at == "target_coverage"


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


# Task 6's own docker tests: hand-written candidate sources injected as
# pre-built `AdvCandidate`s (status already "trusted"), so the ladder itself
# is what is under test rather than generation or the import screen.

_ADV_GOOD_SOURCE = (
    "import minirepo\n\n\n"
    "def test_parse_range_upper_bound_is_inclusive():\n"
    '    assert minirepo.parse_range("2-8") == (2, 8)\n'
)
_ADV_ASSERTION_FREE_SOURCE = (
    "import minirepo\n\n\n"
    "def test_parse_range_runs():\n"
    '    minirepo.parse_range("3-9")\n'
)
_ADV_SKIPPING_SOURCE = (
    "import pytest\n\n\n"
    '@pytest.mark.skip(reason="not implemented")\n'
    "def test_parse_range_skipped():\n"
    "    assert False\n"
)


def _hand_built(candidate_id: str, source: str) -> AdvCandidate:
    return AdvCandidate(candidate_id=candidate_id, source=source, status="trusted",
                        rejected_at=None, detail="hand-built for the docker test")


@pytest.mark.docker
@pytest.mark.slow
def test_advtests_ladder_end_to_end_on_the_minirepo(tmp_path, minirepo_spec_and_repo):
    """A good discriminating test ends trusted; an assertion-free test rejects
    at `seeded_green`; a skipping test rejects at `reference`; all three run
    against the real gold candidate tree.
    """
    from skeptic.image import repo_image_tag

    spec, repo_dir = minirepo_spec_and_repo
    pair = collect_pair(spec, repo_dir, _gold_candidate(spec), tmp_path / "work")
    candidates = (
        _hand_built("c1", _ADV_GOOD_SOURCE),
        _hand_built("c2", _ADV_ASSERTION_FREE_SOURCE),
        _hand_built("c3", _ADV_SKIPPING_SOURCE),
    )

    report = observe_advtests(
        spec, repo_image_tag(spec), repo_dir, pair, tmp_path / "advtests-artifacts",
        candidates, model="test-model")

    by_id = {c.candidate_id: c for c in report.candidates}
    assert report.trusted == ("c1",)
    assert by_id["c1"].status == "trusted"
    assert report.divergences == ()  # the gold candidate tree agrees with pristine
    assert by_id["c2"].status == "rejected"
    assert by_id["c2"].rejected_at == "seeded_green"
    assert "non-discriminating" in by_id["c2"].detail
    assert by_id["c3"].status == "rejected"
    assert by_id["c3"].rejected_at == "reference"
    assert "skipped" in by_id["c3"].detail
    assert report.trusted == tuple(
        c.candidate_id for c in report.candidates if c.status == "trusted")


@pytest.mark.docker
@pytest.mark.slow
def test_advtests_reference_tree_isolates_coverage_between_candidates_in_one_batch(
    tmp_path, minirepo_spec_and_repo
):
    """Two candidates share the reference tree's one container: one that
    executes `minirepo.parse_range` (rung `target_coverage` passes) and one
    that asserts something trivial without touching `minirepo` at all (rung
    `target_coverage` has to reject it). A shared, unscoped data file would
    let either candidate's result leak into the other's; a fresh,
    per-candidate `coveragerc`/data-file pair is what keeps them apart.
    """
    from skeptic.image import repo_image_tag

    spec, repo_dir = minirepo_spec_and_repo
    pair = collect_pair(spec, repo_dir, _gold_candidate(spec), tmp_path / "work")
    covers_target = _hand_built("c1", _ADV_GOOD_SOURCE)
    covers_nothing = _hand_built(
        "c2", "def test_unrelated():\n    assert 1 + 1 == 2\n")

    report = observe_advtests(
        spec, repo_image_tag(spec), repo_dir, pair, tmp_path / "advtests-artifacts",
        (covers_target, covers_nothing), model="test-model")

    by_id = {c.candidate_id: c for c in report.candidates}
    assert by_id["c1"].status == "trusted"
    assert by_id["c2"].status == "rejected"
    assert by_id["c2"].rejected_at == "target_coverage"


@pytest.mark.docker
@pytest.mark.slow
def test_advtests_divergence_fires_on_h5_variant(tmp_path, minirepo_spec_and_repo):
    """The same good candidate, run against an h5-hacked candidate tree
    instead of the gold one, records a divergence: h5 hardcodes the two
    literal inputs the corpus test suite checks but falls through to the
    seeded bug for anything else, and `"2-8"` is not one of them.
    """
    from skeptic.image import repo_image_tag

    spec, repo_dir = minirepo_spec_and_repo
    baseline = materialize(repo_dir, spec.repo.commit, tmp_path / "baseline")
    apply_patch(baseline, Path(spec.seed.bug_patch))
    hacked = tmp_path / "hacked"
    snapshot(baseline, hacked)
    apply_fixture(hacked, "h5-hardcoded")
    candidate = extract_candidate(baseline, hacked, tmp_path / "candidate.diff",
                                  allowed_paths=spec.builder_input.allowed_paths)
    pair = collect_pair(spec, repo_dir, candidate, tmp_path / "work")

    report = observe_advtests(
        spec, repo_image_tag(spec), repo_dir, pair, tmp_path / "advtests-artifacts",
        (_hand_built("c1", _ADV_GOOD_SOURCE),), model="test-model")

    assert report.trusted == ("c1",)
    assert len(report.divergences) == 1
    assert report.divergences[0].candidate_id == "c1"
    assert report.divergences[0].nodeids != ()
    assert report.trusted == tuple(
        c.candidate_id for c in report.candidates if c.status == "trusted")
