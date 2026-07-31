import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from skeptic import cli
from skeptic.candidate import CandidateReport
from skeptic.checks.aggregate import LayerOutcome
from skeptic.checks.evidence import MANDATORY_CHECKS, CheckResult
from skeptic.cli import _verify_cache_key, app
from skeptic.orchestrator import StageCache
from skeptic.spec import find_task
from skeptic.trace import read_trace
from tests.helpers import make_observed_pair, make_task_spec

runner = CliRunner()


def _fake_pair(spec):
    """A fully-observed, tree-free pair (`make_observed_pair`) whose seeded
    ids all passed on the candidate side: `compute_fix_verified` reads this
    for real even though the check layer is faked around it."""
    seeded = tuple(spec.seed.failing_tests)
    return make_observed_pair(
        baseline={"collected": seeded, "collect_exit": 0,
                 "outcomes": dict.fromkeys(seeded, "failed"),
                 "collection_errors": 0, "suite_exit": 1},
        candidate={"collected": seeded, "collect_exit": 0,
                  "outcomes": dict.fromkeys(seeded, "passed"),
                  "collection_errors": 0, "suite_exit": 0},
        spec=spec,
    )


def _pass_layer_outcome() -> LayerOutcome:
    """Every mandatory check reporting completed, no evidence: a genuine PASS
    once the real `aggregate()` folds it."""
    results = tuple(
        CheckResult(check=name, status="completed", evidence=(), artifact=None, dur_ms=1)
        for name in MANDATORY_CHECKS
    )
    return LayerOutcome(results=results, infra={})


def _fake_heavy_stages(monkeypatch, pair, calls):
    """Fake every git/docker-touching step `do_verify` calls before
    `collect_pair`, and fake `collect_pair` itself plus the check layer, so
    the command runs end to end against `pair` with no real subprocess.
    `aggregate()` (and `compute_fix_verified`) run for real.
    """
    from skeptic import candidate, checks, collector, workspace

    monkeypatch.setattr(workspace, "clone_pinned", lambda url, commit, cache: cache)
    monkeypatch.setattr(workspace, "materialize", lambda repo, commit, dest: dest.mkdir(parents=True))
    monkeypatch.setattr(workspace, "apply_patch", lambda ws, patch: None)
    monkeypatch.setattr(candidate, "snapshot", lambda src, dest: None)
    monkeypatch.setattr(
        candidate, "extract_candidate",
        lambda baseline, workspace, out_diff, allowed_paths: CandidateReport(
            diff_path=out_diff, changed_files=["src/click/termui.py"],
            out_of_scope=[], is_empty=False))

    def fake_collect_pair(spec, repo_dir, report, workdir, baseline_cache=None):
        calls.append(1)
        return pair

    monkeypatch.setattr(collector, "collect_pair", fake_collect_pair)
    monkeypatch.setattr(checks, "run_verify_layer", lambda p: _pass_layer_outcome())


def test_verify_refuses_an_unknown_profile_before_any_work(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(cli, "_docker_available", lambda: called.append("docker") or True)
    result = runner.invoke(app, ["verify", "--task", "no-such-task",
                                 "--variant", "gold", "--profile", "stochastic",
                                 "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "stochastic" in result.output
    assert called == []          # profile guard comes before find_task and docker


def test_verify_refuses_the_venv_runner_with_the_wiring_message(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(cli, "_docker_available", lambda: called.append("docker") or True)
    result = runner.invoke(app, ["verify", "--task", "no-such-task",
                                 "--variant", "gold", "--runner", "venv",
                                 "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "not wired" in result.output
    assert called == []          # runner guard also comes before find_task and docker


def test_verify_names_known_variants_on_an_unknown_variant_id(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "no-such-variant",
                                 "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "no-such-variant" in result.output
    assert "gold" in result.output


def test_verify_requires_docker_daemon(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_docker_available", lambda: False)
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "Docker" in result.output


@pytest.mark.parametrize("status,verdict_name,expected_exit", [
    ("ok", "PASS", 0),
    ("ok", "SUSPECT", 1),
    ("ok", "FAIL", 2),
    ("INFRA_ERROR", None, 3),
])
def test_verify_exit_codes_follow_the_verdict(
    tmp_path, monkeypatch, status, verdict_name, expected_exit
):
    # A fully-faked verdict, pre-populated straight into the stage cache: no
    # do_verify call happens at all, matching test_cli_build's cache-hit
    # style (test_build_writes_a_baseline_suite_trace_event_on_a_cache_hit).
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    workdir = tmp_path.resolve()
    spec = find_task("click-0001", Path("tasks"))
    variant_spec = spec.evaluation.variants[0]
    cache_key = _verify_cache_key(spec, variant_spec)
    verify_dir = workdir / spec.task_id / "verify" / variant_spec.id
    fake = {
        "schema_version": 1, "run_id": "r", "task_id": spec.task_id,
        "variant": variant_spec.id, "status": status, "verdict": verdict_name,
        "suspect_score": 0.0, "checks_completed": [], "not_applicable": [],
        "checks_infra": [], "evidence": [], "isolation": "docker-run",
        "profile": "deterministic",
        "infra_reason": None if status == "ok" else "boom",
        "fix_verified": True,
        "artifacts_dir": str(verify_dir / "collect" / "artifacts"),
    }
    StageCache(verify_dir / "cache").put(cache_key, fake)

    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", variant_spec.id,
                                 "--workdir", str(workdir)])

    assert result.exit_code == expected_exit, result.output


def test_verify_writes_verdict_json_and_prints_the_banner(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    spec = find_task("click-0001", Path("tasks"))
    pair = _fake_pair(spec)
    calls: list[int] = []
    _fake_heavy_stages(monkeypatch, pair, calls)

    workdir = tmp_path.resolve()
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--workdir", str(workdir)])

    assert result.exit_code == 0, result.output
    assert "VERDICT PASS" in result.output
    assert "score 0.00" in result.output
    assert "checks: 6 completed · 0 n/a · 0 infra" in result.output
    assert "fix_verified: True" in result.output
    assert "profile deterministic · isolation docker-run" in result.output
    assert calls == [1]

    verdict_path = pair.artifacts_dir / "verdict.json"
    assert verdict_path.is_file()
    saved = json.loads(verdict_path.read_text())
    assert saved["verdict"] == "PASS"
    assert saved["task_id"] == "click-0001"


def test_verify_cache_hit_skips_collection_and_replays_the_banner(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    spec = find_task("click-0001", Path("tasks"))
    pair = _fake_pair(spec)
    calls: list[int] = []
    _fake_heavy_stages(monkeypatch, pair, calls)

    workdir = tmp_path.resolve()
    first = runner.invoke(app, ["verify", "--task", "click-0001",
                                "--variant", "gold", "--workdir", str(workdir)])
    assert first.exit_code == 0, first.output
    assert calls == [1]

    second = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--workdir", str(workdir)])
    assert second.exit_code == 0, second.output
    assert calls == [1]          # collect_pair (the faked collector) not called again
    assert "(cached)" in second.output

    events, _ = read_trace(workdir / "click-0001" / "verify" / "gold" / "trace.jsonl")
    assert "stage_cached" in [e["event"] for e in events]


def test_verify_cache_key_changes_with_patch_bytes_and_source_and_config(tmp_path, monkeypatch):
    spec = make_task_spec()
    variant = spec.evaluation.variants[0]
    base = _verify_cache_key(spec, variant)

    # 1. the variant's patch bytes change (same path family, different tmp file)
    patch_copy = tmp_path / "variant.diff"
    patch_copy.write_bytes(Path(variant.patch).read_bytes() + b"\n# extra\n")
    changed_patch = variant.model_copy(update={"patch": str(patch_copy)})
    assert _verify_cache_key(spec, changed_patch) != base

    # 2. the detector source moves (verifier_revision)
    monkeypatch.setattr("skeptic.orchestrator.verifier_revision", lambda: "deadbeefcafe")
    assert _verify_cache_key(spec, variant) != base
    monkeypatch.undo()

    # 3. a verification config field changes
    edited_spec = spec.model_copy(update={
        "verification": spec.verification.model_copy(
            update={"patch_coverage_min": spec.verification.patch_coverage_min + 0.05}),
    })
    assert _verify_cache_key(edited_spec, variant) != base


@pytest.mark.docker
@pytest.mark.slow
def test_verify_minirepo_gold_passes_end_to_end(tmp_path, minirepo_spec_and_repo):
    spec, repo_dir = minirepo_spec_and_repo
    tasks_dir = repo_dir.parent / "tasks"
    workdir = (tmp_path / "workdir").resolve()

    result = runner.invoke(app, ["verify", "--task", spec.task_id, "--variant", "gold",
                                 "--tasks-dir", str(tasks_dir), "--workdir", str(workdir)])

    assert result.exit_code == 0, result.output
    assert "VERDICT PASS" in result.output
    verdict_path = workdir / spec.task_id / "verify" / "gold" / "collect" / "artifacts" / "verdict.json"
    assert verdict_path.is_file()
    saved = json.loads(verdict_path.read_text())
    assert saved["verdict"] == "PASS"
    assert saved["task_id"] == spec.task_id


@pytest.mark.docker
@pytest.mark.slow
def test_verify_minirepo_h1_fails_end_to_end(tmp_path):
    from tests.helpers import load_hack_fixture, make_minirepo_task

    tasks_dir, task_id = make_minirepo_task(
        tmp_path, extra_variants=[("h1", "hacked", load_hack_fixture("h1-excision"))])
    workdir = (tmp_path / "workdir").resolve()

    result = runner.invoke(app, ["verify", "--task", task_id, "--variant", "h1",
                                 "--tasks-dir", str(tasks_dir), "--workdir", str(workdir)])

    assert result.exit_code == 2, result.output
    assert "collect_shrinkage" in result.output
