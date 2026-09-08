"""Closeout contracts: complete candidate changes and complete evaluation inputs."""

import pytest

from skeptic.candidate import extract_candidate
from skeptic.checks import t1_outcomes, t2_judge
from skeptic.checks.aggregate import LayerOutcome, aggregate
from skeptic.checks.evidence import CheckResult
from skeptic.errors import SkepticInfraError
from skeptic.judge import judge_diff
from skeptic.trace import TraceWriter
from tests.helpers import make_observed_pair, make_task_spec
from tests.test_judge import _fake_client

SEEDED = "tests/test_x.py::test_fix"
OTHER = "tests/test_x.py::test_other"


@pytest.mark.parametrize("missing", [SEEDED, OTHER])
def test_collected_test_without_terminal_outcome_is_incomplete(missing):
    baseline = {"collected": (SEEDED, OTHER), "collect_exit": 0,
                "outcomes": {SEEDED: "failed", OTHER: "passed"},
                "suite_exit": 1, "collection_errors": 0}
    candidate = {**baseline, "outcomes": {n: "passed" for n in (SEEDED, OTHER) if n != missing},
                 "suite_exit": 0}
    pair = make_observed_pair(baseline, candidate,
                              spec=make_task_spec(failing_tests=[SEEDED]))
    with pytest.raises(SkepticInfraError, match="terminal outcome"):
        t1_outcomes.run(pair)
    if missing == SEEDED:
        assert t1_outcomes.compute_fix_verified(pair) is None


def test_unknown_fix_outcome_cannot_become_fail_or_pass_without_evidence():
    complete = CheckResult(check="t1_outcomes", status="completed", evidence=(),
                           artifact=None, dur_ms=0)
    verdict = aggregate(LayerOutcome((complete,), {}), fix_verified=None,
                        run_id="r", task_id="t", variant="v", isolation="docker",
                        profile="deterministic", mandatory=["t1_outcomes"])
    assert verdict.status == "INFRA_ERROR"
    assert verdict.verdict is None
    assert verdict.evidence == []


@pytest.mark.parametrize("text", ["garbage", "flag: no\nflag: yes\nrationale: contradictory"])
def test_uninterpretable_judge_is_not_a_completed_negative(tmp_path, text):
    trace = TraceWriter(tmp_path / "trace.jsonl", "r", "t")
    report, _ = judge_diff(_fake_client(text), "patch", trace)
    pair = make_observed_pair({})
    pair = pair.model_copy(update={"candidate": pair.candidate.model_copy(
        update={"judge": report})})
    with pytest.raises(SkepticInfraError, match="interpretable"):
        t2_judge.run(pair)


@pytest.mark.parametrize("name", ["odd\tname.py", "spaced name.py", ".gitattributes", ".gitignore",
                                  ".GITATTRIBUTES", ".GITIGNORE"])
def test_extraction_refuses_whole_mixed_candidate_instead_of_dropping_changes(tmp_path, name):
    baseline, candidate = tmp_path / "base", tmp_path / "candidate"
    baseline.mkdir()
    candidate.mkdir()
    (baseline / "mod.py").write_text("value = 1\n")
    (candidate / "mod.py").write_text("value = 2\n")
    (candidate / name).write_text("candidate change\n")
    with pytest.raises(SkepticInfraError, match="unsupported|control"):
        extract_candidate(baseline, candidate, tmp_path / "admitted.diff", ["mod.py"])
    assert not (tmp_path / "admitted.diff").exists()


def test_binary_payload_size_is_not_misread_as_a_gitlink(tmp_path):
    from skeptic.candidate import validate_submitted_patch
    baseline, candidate = tmp_path / "base", tmp_path / "candidate"
    baseline.mkdir()
    candidate.mkdir()
    (candidate / "data.bin").write_bytes(b"\0" * 160000)
    report = extract_candidate(baseline, candidate, tmp_path / "input.diff", ["data.bin"])
    validate_submitted_patch(report.diff_path)


def test_supported_modes_links_and_header_like_content_round_trip(tmp_path):
    import shutil
    import stat

    from skeptic.candidate import tree_inventory
    from skeptic.workspace import apply_candidate
    baseline, candidate = tmp_path / "base", tmp_path / "candidate"
    baseline.mkdir()
    (baseline / "entry.py").write_bytes(b"# original\r\nprint(1)\r\n")
    shutil.copytree(baseline, candidate)
    (candidate / "entry.py").chmod(0o755)
    (candidate / "data.txt").write_bytes(b"a/baseline/data\r\n--- b/workspace/data\r\n")
    (candidate / "link.py").symlink_to("entry.py")
    report = extract_candidate(baseline, candidate, tmp_path / "input.diff", ["entry.py"])
    replay = tmp_path / "replay"
    shutil.copytree(baseline, replay)
    apply_candidate(replay, report.diff_path)
    assert tree_inventory(replay) == tree_inventory(candidate)
    assert (replay / "data.txt").read_bytes() == b"a/baseline/data\r\n--- b/workspace/data\r\n"
    assert (replay / "entry.py").stat().st_mode & stat.S_IXUSR
    assert (replay / "link.py").is_symlink()


@pytest.mark.parametrize("name", [".gitignore", "__pycache__/x.pyc", "../escape", "C:/escape"])
def test_explicit_unsupported_patch_is_refused_before_application(tmp_path, name):
    from skeptic.workspace import apply_candidate
    patch = tmp_path / "input.diff"
    patch.write_text(f"diff --git a/{name} b/{name}\nnew file mode 100644\n"
                     f"--- /dev/null\n+++ b/{name}\n@@ -0,0 +1 @@\n+data\n")
    tree = tmp_path / "tree"
    tree.mkdir()
    with pytest.raises(SkepticInfraError, match="unsupported"):
        apply_candidate(tree, patch)
    assert list(tree.iterdir()) == []


@pytest.mark.parametrize("target", ["missing.py", "../outside.py"])
def test_candidate_execution_refuses_unresolvable_or_escaping_links(tmp_path, target):
    from skeptic.workspace import apply_candidate
    (tmp_path / "outside.py").write_text("outside")
    baseline, candidate = tmp_path / "base", tmp_path / "candidate"
    baseline.mkdir()
    candidate.mkdir()
    (candidate / "link.py").symlink_to(target)
    report = extract_candidate(baseline, candidate, tmp_path / "input.diff", ["link.py"])
    with pytest.raises(SkepticInfraError, match="symlink"):
        apply_candidate(baseline, report.diff_path)
