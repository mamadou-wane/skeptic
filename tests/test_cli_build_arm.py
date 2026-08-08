import json
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from skeptic.cli import _build_dir, _run_attempt_acceptance, app
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


def test_build_arm_marks_a_replayed_attempt_estimated_and_zeroes_its_cost(monkeypatch, tmp_path):
    def fake_build(**kw):
        # a cache hit: run_stage returns the cached dict without calling
        # do_build, so trace.jsonl carries stage_cached and no llm_call
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
    assert row["estimated"] is True
    assert row["usd"] == 0.0
    assert row["usd_cache_gap"] == 0.0


def test_build_arm_a_fresh_attempt_is_not_estimated_and_keeps_its_real_cost(monkeypatch, tmp_path):
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
    assert row["estimated"] is False
    assert row["usd"] == BASE_RESULT["usd"]
    assert row["usd_cache_gap"] == BASE_RESULT["usd_cache_gap"]


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
