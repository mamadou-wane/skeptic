import json
import typing
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from skeptic.cli import _build_cache_key, _build_dir, _run_attempt_acceptance, app
from skeptic.errors import SkepticInfraError
from skeptic.seedcheck import SuiteResult
from skeptic.spec import AcceptanceSuiteSpec
from tests.helpers import make_task_spec

runner = CliRunner()

# A green, non-empty BUILD result.json shape (task 16's usd_cache_gap fields
# included). Individual tests override the keys their scenario needs.
BASE_RESULT = {
    "stop_reason": "green", "iterations": 3, "usd": 0.1, "usd_cache_gap": 0.02,
    "cache_read_tokens": 10, "cache_creation_tokens": 5, "green": True,
    "is_empty": False, "candidate": "candidate.diff",
}


def _write_build_result(workdir, task: str, attempt: int, result: dict,
                        events: list[dict] | None = None) -> Path:
    build_dir = _build_dir(Path(workdir), task, attempt)
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "result.json").write_text(json.dumps(result))
    if events:
        with (build_dir / "trace.jsonl").open("a") as fh:
            for event in events:
                fh.write(json.dumps(event) + "\n")
    return build_dir


def _one_run_dir(out_root: Path) -> Path:
    return next((out_root / "arms").iterdir())


# --- the paid preflight, mirroring eval's own (test_cli_eval.py) ----------


def test_build_arm_requires_api_key_before_anything_else(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    called = []
    monkeypatch.setattr("skeptic.cli.build", lambda **kw: called.append(kw))
    result = runner.invoke(app, ["build-arm", "--name", "base", "--tasks", "click-0001",
                                 "--attempts", "1", "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "ANTHROPIC_API_KEY" in result.output
    assert called == []


def test_build_arm_refuses_unpriced_model(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    result = runner.invoke(app, ["build-arm", "--name", "base", "--tasks", "click-0001",
                                 "--attempts", "1", "--model", "no-such-model",
                                 "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "No pricing entry" in result.output


def test_build_arm_rejects_a_name_that_would_escape_the_arms_directory(tmp_path):
    # --name becomes a directory component under evals/v1/arms/ via
    # arm_run_id; unvalidated, "../.." walks the run dir straight out of
    # that directory.
    result = runner.invoke(app, ["build-arm", "--name", "../..", "--tasks", "click-0001",
                                 "--attempts", "1", "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "--name" in result.output
    assert "Next:" in result.output


def test_build_arm_rejects_non_positive_attempts(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    result = runner.invoke(app, ["build-arm", "--name", "base", "--tasks", "click-0001",
                                 "--attempts", "0", "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "--attempts" in result.output


def test_build_arm_confirm_declined_exits_infra_without_calling_build(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(
        "skeptic.cli.build",
        lambda **kw: calls.append(kw) or (_ for _ in ()).throw(typer.Exit(0)))
    result = runner.invoke(app, ["build-arm", "--name", "base", "--tasks", "click-0001",
                                 "--attempts", "1", "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals")], input="n\n")
    assert result.exit_code == 3
    assert "Declined" in result.output
    assert "--yes" in result.output
    assert calls == []


def test_build_arm_prints_estimated_max_as_attempts_times_ceiling(monkeypatch, tmp_path):
    # click-0001's cost_ceiling_usd is 2.00; 3 attempts on one task -> $6.00.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    result = runner.invoke(app, ["build-arm", "--name", "base", "--tasks", "click-0001",
                                 "--attempts", "3", "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals")], input="n\n")
    assert result.exit_code == 3
    assert "$6.00" in result.output
    assert "3 attempts" in result.output


def test_build_arm_confirm_message_uses_singular_nouns_at_one(monkeypatch, tmp_path):
    # part 1 review noted "1 attempts" at --attempts 1; one task and
    # --attempts 1 also singularize "tasks" and "builds" (n_builds = 1 x 1),
    # the same bug pattern task 2 review folded in alongside the rename.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    result = runner.invoke(app, ["build-arm", "--name", "base", "--tasks", "click-0001",
                                 "--attempts", "1", "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals")], input="n\n")
    assert result.exit_code == 3
    assert "1 attempts" not in result.output
    assert "1 tasks" not in result.output
    assert "1 builds" not in result.output
    assert "1 task x 1 attempt = 1 build " in result.output


def test_build_arm_yes_skips_confirm_and_drives_the_arm(monkeypatch, tmp_path):
    calls = []

    def fake_build(**kw):
        calls.append(kw)
        _write_build_result(kw["workdir"], kw["task"], kw["attempt"],
                            {**BASE_RESULT, "green": False})
        raise typer.Exit(0)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("skeptic.cli.build", fake_build)
    result = runner.invoke(app, ["build-arm", "--name", "base", "--tasks", "click-0001",
                                 "--attempts", "1", "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals"), "--yes"])
    assert result.exit_code == 0, result.output
    assert "Proceed" not in result.output
    assert len(calls) == 1
    assert all(kw["yes"] is True for kw in calls)


# --- task 2: the provenance manifest ----------------------------------------


def test_build_arm_writes_manifest_json_beside_arm_md(monkeypatch, tmp_path):
    def fake_build(**kw):
        _write_build_result(kw["workdir"], kw["task"], kw["attempt"], dict(BASE_RESULT),
                            events=[{"event": "llm_call", "usage": {"usd": 0.1}}])
        raise typer.Exit(0)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("skeptic.cli.build", fake_build)
    monkeypatch.setattr(
        "skeptic.cli._run_attempt_acceptance",
        lambda *a, **k: SuiteResult(outcomes={"acc::t": "passed"}, collection_errors=0))

    result = runner.invoke(app, ["build-arm", "--name", "base", "--tasks", "click-0001",
                                 "--attempts", "1", "--model", "claude-opus-5",
                                 "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals"), "--yes"])
    assert result.exit_code == 0, result.output
    run_dir = _one_run_dir(tmp_path / "evals")

    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["arm_name"] == "base"
    assert manifest["model"] == "claude-opus-5"
    assert manifest["attempts"] == 1
    assert set(manifest["tasks"]) == {"click-0001"}
    assert manifest["schema_version"] == 1  # write_manifest's own injection

    # render_arm_table's header is wired to this same manifest
    table = (run_dir / "arm.md").read_text()
    assert "arm: base" in table
    assert "model: claude-opus-5" in table


def test_build_arm_writes_manifest_even_when_every_attempt_is_infra(monkeypatch, tmp_path):
    # the manifest is written where run_dir is created, before the attempt
    # loop's outcome matters: an all-INFRA sweep still gets one.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("skeptic.cli.build", lambda **kw: (_ for _ in ()).throw(typer.Exit(3)))

    result = runner.invoke(app, ["build-arm", "--name", "base", "--tasks", "click-0001",
                                 "--attempts", "1", "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals"), "--yes"])
    assert result.exit_code == 3
    run_dir = _one_run_dir(tmp_path / "evals")

    manifest_path = run_dir / "manifest.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["arm_name"] == "base"
    assert set(manifest["tasks"]) == {"click-0001"}

    row = json.loads(
        (run_dir / "click-0001" / "attempt-1" / "classification.json").read_text())
    assert row["classification"] == "INFRA_ERROR"  # the loop itself did run and classify


def test_build_arm_manifest_survives_a_hard_abort_inside_the_attempt_loop(monkeypatch, tmp_path):
    # build() returning without exiting raises SkepticInfraError from INSIDE
    # the attempt loop, uncaught there: it propagates straight past arm.md's
    # own write. Writing the manifest before the loop starts, not after it
    # like eval's own manifest, is what makes it survive this abort.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("skeptic.cli.build", lambda **kw: None)  # never raises typer.Exit

    result = runner.invoke(app, ["build-arm", "--name", "base", "--tasks", "click-0001",
                                 "--attempts", "1", "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals"), "--yes"])
    assert result.exit_code == 3
    assert "returned without exiting" in result.output
    run_dir = _one_run_dir(tmp_path / "evals")
    assert (run_dir / "manifest.json").is_file()
    assert not (run_dir / "arm.md").exists()  # the loop aborted before the table ever wrote


# --- classification wiring --------------------------------------------------


def test_build_arm_classifies_red_without_running_acceptance(monkeypatch, tmp_path):
    calls = []

    def fake_build(**kw):
        calls.append(kw)
        _write_build_result(
            kw["workdir"], kw["task"], kw["attempt"],
            {**BASE_RESULT, "green": False, "stop_reason": "iteration_cap"},
            events=[{"event": "llm_call", "usage": {"usd": 0.1}}])
        raise typer.Exit(0)

    def fake_acceptance(*args, **kwargs):
        raise AssertionError("acceptance must not run for a non-green attempt")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("skeptic.cli.build", fake_build)
    monkeypatch.setattr("skeptic.cli._run_attempt_acceptance", fake_acceptance)

    result = runner.invoke(app, ["build-arm", "--name", "base", "--tasks", "click-0001",
                                 "--attempts", "1", "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals"), "--yes"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    run_dir = _one_run_dir(tmp_path / "evals")
    row = json.loads(
        (run_dir / "click-0001" / "attempt-1" / "classification.json").read_text())
    assert row["classification"] == "RED"
    assert (run_dir / "click-0001" / "attempt-1" / "result.json").is_file()


def test_build_arm_classifies_empty_candidate_as_red_without_acceptance(monkeypatch, tmp_path):
    def fake_build(**kw):
        _write_build_result(kw["workdir"], kw["task"], kw["attempt"],
                            {**BASE_RESULT, "green": True, "is_empty": True})
        raise typer.Exit(2)  # build's own EXIT_FAIL for an empty candidate

    def fake_acceptance(*args, **kwargs):
        raise AssertionError("acceptance must not run for an empty candidate")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("skeptic.cli.build", fake_build)
    monkeypatch.setattr("skeptic.cli._run_attempt_acceptance", fake_acceptance)

    result = runner.invoke(app, ["build-arm", "--name", "base", "--tasks", "click-0001",
                                 "--attempts", "1", "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals"), "--yes"])
    assert result.exit_code == 0, result.output
    run_dir = _one_run_dir(tmp_path / "evals")
    row = json.loads(
        (run_dir / "click-0001" / "attempt-1" / "classification.json").read_text())
    assert row["classification"] == "RED"


def test_build_arm_classifies_green_correct_and_green_wrong_by_attempt(monkeypatch, tmp_path):
    def fake_build(**kw):
        _write_build_result(kw["workdir"], kw["task"], kw["attempt"], dict(BASE_RESULT),
                            events=[{"event": "llm_call", "usage": {"usd": 0.1}}])
        raise typer.Exit(0)

    def fake_acceptance(spec, result, workdir, attempt):
        outcome = "passed" if attempt == 1 else "failed"
        return SuiteResult(outcomes={"acc::t": outcome}, collection_errors=0)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("skeptic.cli.build", fake_build)
    monkeypatch.setattr("skeptic.cli._run_attempt_acceptance", fake_acceptance)

    result = runner.invoke(app, ["build-arm", "--name", "base", "--tasks", "click-0001",
                                 "--attempts", "2", "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals"), "--yes"])
    assert result.exit_code == 0, result.output
    run_dir = _one_run_dir(tmp_path / "evals")
    row1 = json.loads(
        (run_dir / "click-0001" / "attempt-1" / "classification.json").read_text())
    row2 = json.loads(
        (run_dir / "click-0001" / "attempt-2" / "classification.json").read_text())
    assert row1["classification"] == "GREEN-correct"
    assert row2["classification"] == "GREEN-wrong"

    table = (run_dir / "arm.md").read_text()
    assert "| GREEN-correct | 1 |" in table
    assert "| GREEN-wrong | 1 |" in table
    assert str(run_dir / "arm.md") in result.output


def test_build_arm_infra_when_the_acceptance_run_itself_raises(monkeypatch, tmp_path):
    def fake_build(**kw):
        _write_build_result(kw["workdir"], kw["task"], kw["attempt"], dict(BASE_RESULT),
                            events=[{"event": "llm_call", "usage": {"usd": 0.1}}])
        raise typer.Exit(0)

    def fake_acceptance(*args, **kwargs):
        raise SkepticInfraError("no admission venv for this task")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("skeptic.cli.build", fake_build)
    monkeypatch.setattr("skeptic.cli._run_attempt_acceptance", fake_acceptance)

    result = runner.invoke(app, ["build-arm", "--name", "base", "--tasks", "click-0001",
                                 "--attempts", "1", "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals"), "--yes"])
    assert result.exit_code == 3
    assert "no admission venv" in result.output
    run_dir = _one_run_dir(tmp_path / "evals")
    row = json.loads(
        (run_dir / "click-0001" / "attempt-1" / "classification.json").read_text())
    assert row["classification"] == "INFRA_ERROR"


def test_build_arm_one_infra_task_does_not_end_the_arm(monkeypatch, tmp_path):
    calls = []

    def fake_build(**kw):
        calls.append(kw["task"])
        if kw["task"] == "click-0001":
            raise typer.Exit(3)  # e.g. docker unavailable: no result.json written
        _write_build_result(kw["workdir"], kw["task"], kw["attempt"], dict(BASE_RESULT),
                            events=[{"event": "llm_call", "usage": {"usd": 0.1}}])
        raise typer.Exit(0)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("skeptic.cli.build", fake_build)
    monkeypatch.setattr(
        "skeptic.cli._run_attempt_acceptance",
        lambda *a, **k: SuiteResult(outcomes={"acc::t": "passed"}, collection_errors=0))

    result = runner.invoke(app, ["build-arm", "--name", "base",
                                 "--tasks", "click-0001,rich-0001", "--attempts", "1",
                                 "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals"), "--yes"])
    assert result.exit_code == 3
    assert calls == ["click-0001", "rich-0001"]  # rich-0001 still ran
    run_dir = _one_run_dir(tmp_path / "evals")
    click_row = json.loads(
        (run_dir / "click-0001" / "attempt-1" / "classification.json").read_text())
    rich_row = json.loads(
        (run_dir / "rich-0001" / "attempt-1" / "classification.json").read_text())
    assert click_row["classification"] == "INFRA_ERROR"
    assert rich_row["classification"] == "GREEN-correct"
    assert "INFRA:" in result.output


def test_build_arm_a_build_that_returns_without_exiting_is_an_infra_error(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("skeptic.cli.build", lambda **kw: None)  # never raises typer.Exit
    result = runner.invoke(app, ["build-arm", "--name", "base", "--tasks", "click-0001",
                                 "--attempts", "1", "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals"), "--yes"])
    assert result.exit_code == 3
    assert "returned without exiting" in result.output


# --- trace rotation and the estimated-cost marker ---------------------------


def test_build_arm_rotates_the_trace_before_calling_build(monkeypatch, tmp_path):
    build_dir = _build_dir(tmp_path, "click-0001", 1)
    build_dir.mkdir(parents=True)
    (build_dir / "trace.jsonl").write_text(
        json.dumps({"event": "stale_llm_call_from_a_previous_invocation"}) + "\n")

    def fake_build(**kw):
        # a fresh run writes no trace.jsonl of its own here, so a surviving
        # trace.jsonl after the arm runs would mean rotation never happened
        (build_dir / "result.json").write_text(
            json.dumps({**BASE_RESULT, "green": False}))
        raise typer.Exit(0)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("skeptic.cli.build", fake_build)

    result = runner.invoke(app, ["build-arm", "--name", "base", "--tasks", "click-0001",
                                 "--attempts", "1", "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals"), "--yes"])
    assert result.exit_code == 0, result.output
    assert not (build_dir / "trace.jsonl").exists()
    assert (build_dir / "trace.prev.jsonl").is_file()
    assert json.loads((build_dir / "trace.prev.jsonl").read_text()) == {
        "event": "stale_llm_call_from_a_previous_invocation"}


def test_build_arm_a_replayed_attempt_joins_the_originating_runs_real_cost(monkeypatch, tmp_path):
    # a cache hit: run_stage returns the cached dict without calling
    # do_build, so trace.jsonl carries stage_cached and no llm_call. The
    # design doc (docs/superpowers/specs/2026-08-02-m5-publishable-core-
    # design.md:99) rules that a replayed row joins the originating run's
    # own cost, marked replayed, not zeroed as a guess.
    def fake_build(**kw):
        _write_build_result(kw["workdir"], kw["task"], kw["attempt"],
                            {**BASE_RESULT, "usd": 0.5, "usd_cache_gap": 0.1},
                            events=[{"event": "stage_cached"}])
        raise typer.Exit(0)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("skeptic.cli.build", fake_build)
    monkeypatch.setattr(
        "skeptic.cli._run_attempt_acceptance",
        lambda *a, **k: SuiteResult(outcomes={"acc::t": "passed"}, collection_errors=0))

    result = runner.invoke(app, ["build-arm", "--name", "base", "--tasks", "click-0001",
                                 "--attempts", "1", "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals"), "--yes"])
    assert result.exit_code == 0, result.output
    run_dir = _one_run_dir(tmp_path / "evals")
    row = json.loads(
        (run_dir / "click-0001" / "attempt-1" / "classification.json").read_text())
    assert row["classification"] == "GREEN-correct"
    assert row["replayed"] is True
    assert row["estimated"] is False
    assert row["usd"] == 0.5
    assert row["usd_cache_gap"] == 0.1


def test_build_arm_replay_after_two_direct_builds_snapshots_one_runs_prev_trace(
    monkeypatch, tmp_path,
):
    """Two REAL direct `build` invocations first (both cache hits on the same
    key, no sweep between them, each writing one `stage_cached` + one
    `baseline_suite` event), then a build-arm sweep on the same pair.

    build-arm's own sweep-level `rotate_trace` only ever MOVES whatever
    trace.jsonl currently holds into trace.prev.jsonl; it cannot un-combine a
    file the two prior direct calls already merged. Pre-fix, `build()` never
    rotates its own trace, so the two direct calls leave ONE trace.jsonl
    holding both calls' events (4 total: 2 `stage_cached` + 2
    `baseline_suite`), and the sweep's rotate carries that combined file into
    trace.prev.jsonl unchanged. Post-fix, each direct call rotates before it
    writes, so trace.jsonl always holds exactly one call's events (2), and
    the sweep's rotate carries forward a clean single run.
    """
    from types import SimpleNamespace

    from skeptic import candidate, cli, image, workspace
    from skeptic.orchestrator import StageCache
    from skeptic.spec import find_task
    from skeptic.trace import config_hash, read_trace

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    monkeypatch.setattr(workspace, "clone_pinned",
                        lambda url, commit, cache: cache)
    monkeypatch.setattr(workspace, "materialize",
                        lambda repo, commit, dest: dest.mkdir(parents=True))
    monkeypatch.setattr(workspace, "apply_patch", lambda ws, patch: None)
    monkeypatch.setattr(candidate, "snapshot", lambda src, dest: None)
    monkeypatch.setattr(
        image, "ensure_repo_image",
        lambda task_spec, context, out: SimpleNamespace(tag="t:1", image_id="img-id"))

    workdir = tmp_path.resolve()
    spec = find_task("click-0001", Path("tasks"))
    seed_hash = config_hash({"seed": Path(spec.seed.bug_patch).read_text()})
    key = _build_cache_key(spec, "claude-opus-5", "img-id", seed_hash)
    build_dir = _build_dir(workdir, "click-0001", 1)
    cached = {**BASE_RESULT, "green": False, "out_of_scope": [], "image_id": "img-id"}
    StageCache(build_dir / "cache").put(key, cached)

    for _ in range(2):  # two direct builds, both cache hits, no sweep between them
        result = runner.invoke(app, ["build", "--task", "click-0001",
                                     "--workdir", str(workdir), "--yes"])
        assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["build-arm", "--name", "base", "--tasks", "click-0001",
                                 "--attempts", "1", "--workdir", str(workdir),
                                 "--out", str(tmp_path / "evals"), "--yes"])
    assert result.exit_code == 0, result.output

    run_dir = _one_run_dir(tmp_path / "evals")
    snap_prev = run_dir / "click-0001" / "attempt-1" / "trace.prev.jsonl"
    assert snap_prev.is_file()
    events, _ = read_trace(snap_prev)
    stage_cached_count = sum(1 for e in events if e["event"] == "stage_cached")
    assert stage_cached_count == 1, (
        f"the snapshot's trace.prev.jsonl must hold exactly one run's "
        f"events (one stage_cached marker); found {stage_cached_count} "
        f"among {[e['event'] for e in events]}")


def test_build_arm_a_replayed_row_missing_usd_cache_gap_is_estimated(monkeypatch, tmp_path):
    # the one real zero-by-omission case: a cache entry written before
    # usd_cache_gap existed on this branch (commit b7f7f2e) carries usd but
    # not usd_cache_gap, and the harness has no figure for the missing key.
    def fake_build(**kw):
        pre_caching_result = {k: v for k, v in BASE_RESULT.items() if k != "usd_cache_gap"}
        _write_build_result(kw["workdir"], kw["task"], kw["attempt"], pre_caching_result,
                            events=[{"event": "stage_cached"}])
        raise typer.Exit(0)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("skeptic.cli.build", fake_build)
    monkeypatch.setattr(
        "skeptic.cli._run_attempt_acceptance",
        lambda *a, **k: SuiteResult(outcomes={"acc::t": "passed"}, collection_errors=0))

    result = runner.invoke(app, ["build-arm", "--name", "base", "--tasks", "click-0001",
                                 "--attempts", "1", "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals"), "--yes"])
    assert result.exit_code == 0, result.output
    run_dir = _one_run_dir(tmp_path / "evals")
    row = json.loads(
        (run_dir / "click-0001" / "attempt-1" / "classification.json").read_text())
    assert row["replayed"] is True
    assert row["estimated"] is True
    assert row["usd"] == BASE_RESULT["usd"]
    assert row["usd_cache_gap"] == 0.0


def test_build_arm_a_fresh_attempt_is_not_replayed_or_estimated(monkeypatch, tmp_path):
    def fake_build(**kw):
        _write_build_result(kw["workdir"], kw["task"], kw["attempt"], dict(BASE_RESULT),
                            events=[{"event": "llm_call", "usage": {"usd": 0.1}}])
        raise typer.Exit(0)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("skeptic.cli.build", fake_build)
    monkeypatch.setattr(
        "skeptic.cli._run_attempt_acceptance",
        lambda *a, **k: SuiteResult(outcomes={"acc::t": "passed"}, collection_errors=0))

    result = runner.invoke(app, ["build-arm", "--name", "base", "--tasks", "click-0001",
                                 "--attempts", "1", "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals"), "--yes"])
    assert result.exit_code == 0, result.output
    run_dir = _one_run_dir(tmp_path / "evals")
    row = json.loads(
        (run_dir / "click-0001" / "attempt-1" / "classification.json").read_text())
    assert row["replayed"] is False
    assert row["estimated"] is False
    assert row["usd"] == BASE_RESULT["usd"]
    assert row["usd_cache_gap"] == BASE_RESULT["usd_cache_gap"]


def test_build_arm_an_infra_attempt_publishes_the_spend_its_trace_shows(monkeypatch, tmp_path):
    # a build that dies on SkepticInfraError after spending (a stage_error
    # re-raise deep in do_build) writes no result.json, but real llm_call
    # events still landed in trace.jsonl before it died; that spend must
    # not publish as a free row.
    def fake_build(**kw):
        build_dir = _build_dir(Path(kw["workdir"]), kw["task"], kw["attempt"])
        build_dir.mkdir(parents=True, exist_ok=True)
        with (build_dir / "trace.jsonl").open("a") as fh:
            fh.write(json.dumps(
                {"event": "llm_call", "usage": {"usd": 0.3, "usd_cache_gap": 0.05}}) + "\n")
            fh.write(json.dumps(
                {"event": "llm_call", "usage": {"usd": 0.2, "usd_cache_gap": 0.0}}) + "\n")
        raise typer.Exit(3)  # no result.json: the build died mid-flight

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("skeptic.cli.build", fake_build)

    result = runner.invoke(app, ["build-arm", "--name", "base", "--tasks", "click-0001",
                                 "--attempts", "1", "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals"), "--yes"])
    assert result.exit_code == 3
    run_dir = _one_run_dir(tmp_path / "evals")
    row = json.loads(
        (run_dir / "click-0001" / "attempt-1" / "classification.json").read_text())
    assert row["classification"] == "INFRA_ERROR"
    assert row["usd"] == pytest.approx(0.5)
    assert row["usd_cache_gap"] == pytest.approx(0.05)
    assert row["estimated"] is False
    assert row["replayed"] is False


def test_build_arm_an_infra_attempt_with_no_trace_evidence_is_estimated(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("skeptic.cli.build", lambda **kw: (_ for _ in ()).throw(typer.Exit(3)))

    result = runner.invoke(app, ["build-arm", "--name", "base", "--tasks", "click-0001",
                                 "--attempts", "1", "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals"), "--yes"])
    assert result.exit_code == 3
    run_dir = _one_run_dir(tmp_path / "evals")
    row = json.loads(
        (run_dir / "click-0001" / "attempt-1" / "classification.json").read_text())
    assert row["classification"] == "INFRA_ERROR"
    assert row["usd"] == 0.0
    assert row["estimated"] is True


def test_build_arm_a_corrupt_acceptance_run_does_not_end_the_arm(monkeypatch, tmp_path):
    # the per-attempt catch widens past SkepticInfraError: a corrupt junit
    # report or any other unexpected failure inside acceptance must classify
    # this attempt INFRA_ERROR, not kill the whole arm.
    calls = []

    def fake_build(**kw):
        calls.append(kw["task"])
        _write_build_result(kw["workdir"], kw["task"], kw["attempt"], dict(BASE_RESULT),
                            events=[{"event": "llm_call", "usage": {"usd": 0.1}}])
        raise typer.Exit(0)

    def fake_acceptance(*args, **kwargs):
        raise ValueError("not a SkepticInfraError at all")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("skeptic.cli.build", fake_build)
    monkeypatch.setattr("skeptic.cli._run_attempt_acceptance", fake_acceptance)

    result = runner.invoke(app, ["build-arm", "--name", "base", "--tasks", "click-0001,rich-0001",
                                 "--attempts", "1", "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals"), "--yes"])
    assert result.exit_code == 3
    assert calls == ["click-0001", "rich-0001"]  # rich-0001 still ran
    assert "ValueError" in result.output


# classify_attempt's own collection-error case is pinned in tests/test_evalkit.py
# (test_classify_infra_when_the_acceptance_suite_hit_a_collection_error), the
# module classify_attempt itself lives in.


# --- _run_attempt_acceptance: the two named INFRA conditions, unit-level ---
# (no docker, no git: both conditions are checked, and raise, before either
# is ever needed)


def test_run_attempt_acceptance_raises_when_no_acceptance_suite_declared(tmp_path):
    spec = make_task_spec()  # the fixture spec carries no acceptance_suite
    with pytest.raises(SkepticInfraError, match="acceptance_suite"):
        _run_attempt_acceptance(spec, {"candidate": "x"}, tmp_path, 1)


def test_run_attempt_acceptance_raises_when_the_admission_venv_is_missing(tmp_path):
    spec = make_task_spec(acceptance_suite=AcceptanceSuiteSpec(
        path=str(tmp_path / "acc"), must_pass_on=["pristine"], must_fail_on=["seeded"]))
    with pytest.raises(SkepticInfraError, match="seed --task .* --check"):
        _run_attempt_acceptance(spec, {"candidate": "x"}, tmp_path, 1)


# --- pinning the classify path's own call sequence --------------------------
#
# The brief named these constraints "easy to get wrong in a new caller":
# classify on a tree under build-arm-classify/, never under build/; the
# candidate diff applied is THIS attempt's own; the venv is venvs/seeded;
# timeout_s and quarantine forward from the spec. Nothing else in this file
# fails if any one of them regresses, since every other test replaces
# `_run_attempt_acceptance` wholesale.


def test_run_attempt_acceptance_pins_the_classify_path_call_sequence(monkeypatch, tmp_path):
    from skeptic import sandbox, seedcheck, workspace

    spec = make_task_spec(
        acceptance_suite=AcceptanceSuiteSpec(
            path="acceptance/fake-suite", must_pass_on=["pristine"], must_fail_on=["seeded"]),
        quarantine=["tests/t.py::test_flaky"],
    )
    (tmp_path / spec.task_id / "venvs" / "seeded").mkdir(parents=True)

    calls = []

    def fake_clone_pinned(url, commit, cache):
        calls.append(("clone_pinned", url, commit, cache))
        return Path("/fake/repo")

    def fake_materialize(repo, commit, dest):
        calls.append(("materialize", repo, commit, dest))
        dest.mkdir(parents=True, exist_ok=True)
        return dest

    def fake_apply_patch(tree, patch_path):
        calls.append(("apply_patch", tree, patch_path))

    def fake_apply_candidate(tree, diff):
        calls.append(("apply_candidate", tree, diff))

    class FakeVenvRunner:
        instances: typing.ClassVar[list] = []

        def __init__(self, workspace, venv_dir):
            self.workspace = workspace
            self.venv_dir = venv_dir
            FakeVenvRunner.instances.append(self)

        def setup(self, install_cmds):
            pass

    def fake_run_acceptance(tree, acc_src, runner_factory, timeout_s, quarantine):
        calls.append(("run_acceptance", tree, acc_src, timeout_s, quarantine))
        runner_factory(tree)  # exercises the venv_dir wiring below
        return SuiteResult(outcomes={}, collection_errors=0)

    monkeypatch.setattr(workspace, "clone_pinned", fake_clone_pinned)
    monkeypatch.setattr(workspace, "materialize", fake_materialize)
    monkeypatch.setattr(workspace, "apply_patch", fake_apply_patch)
    monkeypatch.setattr(workspace, "apply_candidate", fake_apply_candidate)
    monkeypatch.setattr(seedcheck, "run_acceptance", fake_run_acceptance)
    monkeypatch.setattr(sandbox, "VenvRunner", FakeVenvRunner)

    result = {"candidate": "attempt-3-candidate.diff"}
    _run_attempt_acceptance(spec, result, tmp_path, attempt=3)

    assert [c[0] for c in calls] == [
        "clone_pinned", "materialize", "apply_patch", "apply_candidate", "run_acceptance"]

    assert calls[0] == (
        "clone_pinned", spec.repo.url, spec.repo.commit, tmp_path / spec.task_id / "repo-cache")

    tree = calls[1][3]
    assert tree == tmp_path / spec.task_id / "build-arm-classify" / "attempt-3" / "seeded"
    assert "build-arm-classify" in tree.parts
    assert tree.parts[tree.parts.index(spec.task_id) + 1] != "build"  # never under build/
    assert calls[1][1] == Path("/fake/repo")
    assert calls[1][2] == spec.repo.commit

    assert calls[2] == ("apply_patch", tree, Path(spec.seed.bug_patch))
    # the diff applied is THIS attempt's own candidate, not some other one
    assert calls[3] == ("apply_candidate", tree, Path("attempt-3-candidate.diff"))

    assert calls[4] == (
        "run_acceptance", tree, Path(spec.acceptance_suite.path),
        spec.environment.timeout_s, spec.seed.quarantine)

    assert FakeVenvRunner.instances[-1].venv_dir == tmp_path / spec.task_id / "venvs" / "seeded"
