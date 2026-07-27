"""The collector, faked at the subprocess boundary.

Every fast test replaces `sandbox._run` with a function that writes the
artifact files the unit script would have written and hands back an
`ExecResult`. The real `RunContainer` and the real `docker_run_args` still
run, so mount policy, argv order, and the artifacts mount are exercised
rather than stubbed; only the daemon is absent.
"""
import re
import shlex
from pathlib import Path

import pytest

from skeptic.candidate import CandidateReport
from skeptic.checks.observations import VariantObservations
from skeptic.collector import _collect_argv, collect_pair, observe_variant
from skeptic.errors import SkepticInfraError
from skeptic.image import ImageRef
from skeptic.sandbox import ExecResult
from skeptic.spec import TaskSpec
from skeptic.workspace import apply_candidate, clone_pinned
from tests.helpers import BUGGY, PRISTINE, make_minirepo_task, make_task_spec

NODE_A = "tests/test_x.py::test_a"
NODE_B = "tests/test_x.py::test_b"


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


def fake_unit(monkeypatch, *, collected=(), collect_exit=0, suite_exit=0,
              outcomes=None, install_ok=True, timed_out=False):
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


def _observed(spec: TaskSpec, root: Path, side: str,
              with_tests: bool = True) -> VariantObservations:
    return observe_variant(spec, "img", _tree(root, with_tests),
                           root / "artifacts" / side, side)


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


def _fake_observe(spec, image_tag, tree, artifacts, side):
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
    monkeypatch.setattr("skeptic.collector.observe_variant", _fake_observe)

    pair = collect_pair(spec, repo, candidate, tmp_path / "work")

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

    def record_observe(spec, image_tag, tree, artifacts, side):
        seen[f"pristine_at_{side}"] = seen["pristine"].exists()
        return _fake_observe(spec, image_tag, tree, artifacts, side)

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
