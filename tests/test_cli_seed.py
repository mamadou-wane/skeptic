from pathlib import Path

import pytest
import typer
import yaml
from typer.testing import CliRunner

from skeptic.cli import app
from skeptic.seedcheck import CheckReport, InvariantResult
from tests.helpers import make_minirepo_task

runner = CliRunner()

SPECS = Path(__file__).parent / "fixtures" / "specs"
TASK_ID = "click-0001"


@pytest.mark.slow
def test_seed_check_end_to_end(tmp_path):
    tasks_dir, task_id = make_minirepo_task(tmp_path)
    result = runner.invoke(app, [
        "seed", "--task", task_id, "--check",
        "--tasks-dir", str(tasks_dir),
        "--workdir", str(tmp_path / "workdir"),
    ])
    assert result.exit_code == 0, result.output
    assert "pristine-green-x2" in result.output
    assert "seed-red-exact" in result.output
    assert "CHECK PASSED" in result.output
    assert (tmp_path / "workdir" / task_id / "trace.jsonl").is_file()


def test_seed_without_check_flag_explains(tmp_path):
    result = runner.invoke(app, ["seed", "--task", "x", "--tasks-dir", str(tmp_path)])
    assert result.exit_code == 3
    assert "tasks list" in result.output or "No task named" in result.output


def test_seed_without_check_flag_explains_on_valid_task(tmp_path):
    tasks_dir, task_id = make_minirepo_task(tmp_path)
    result = runner.invoke(app, [
        "seed", "--task", task_id, "--tasks-dir", str(tasks_dir),
    ])
    assert result.exit_code == 3
    assert "not implemented yet" in result.output


def test_seed_check_rejects_non_venv_runner(tmp_path):
    tasks_dir, task_id = make_minirepo_task(tmp_path)
    result = runner.invoke(app, [
        "seed", "--task", task_id, "--check",
        "--tasks-dir", str(tasks_dir),
        "--workdir", str(tmp_path / "workdir"),
        "--runner", "docker",
    ])
    assert result.exit_code == 3
    assert "Only --runner venv is wired" in result.output


@pytest.fixture
def seed_check_passing_env(tmp_path, monkeypatch):
    """A tmp tasks dir (default --tasks-dir, cwd chdir'd there) holding
    TASK_ID with two clean variants (gold, gold-prime), plus check_task
    patched to a passing CheckReport, since --self-validate's precondition
    is a passing --check, not a real seedcheck run."""
    monkeypatch.chdir(tmp_path)
    data = yaml.safe_load((SPECS / "valid-task.yaml").read_text())
    gold = data["evaluation"]["variants"][0]
    data["evaluation"]["variants"].append({**gold, "id": "gold-prime"})
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / f"{TASK_ID}.yaml").write_text(yaml.dump(data))

    report = CheckReport(
        task_id=TASK_ID,
        results=[InvariantResult(name="pristine-green-x2", ok=True, detail="ok")],
    )
    monkeypatch.setattr("skeptic.seedcheck.check_task", lambda *a, **kw: report)


def test_self_validate_runs_verify_per_clean_variant(monkeypatch, seed_check_passing_env):
    calls = []

    def fake_verify(**kwargs):
        calls.append((kwargs["variant"], kwargs["profile"]))
        raise typer.Exit(0)

    monkeypatch.setattr("skeptic.cli.verify", fake_verify)
    result = runner.invoke(app, ["seed", "--task", TASK_ID, "--check", "--self-validate"])
    assert result.exit_code == 0
    assert calls == [("gold", "deterministic"), ("gold-prime", "deterministic")]


def test_self_validate_fails_on_non_pass_verdict(monkeypatch, seed_check_passing_env):
    monkeypatch.setattr("skeptic.cli.verify",
                        lambda **kw: (_ for _ in ()).throw(typer.Exit(1)))
    result = runner.invoke(app, ["seed", "--task", TASK_ID, "--check", "--self-validate"])
    assert result.exit_code == 2
    assert "self-validation" in result.output


def test_self_validate_infra_failure_passes_through(monkeypatch, seed_check_passing_env):
    # verify's own preflights (e.g. Docker unavailable) exit EXIT_INFRA (3):
    # an operational failure, not a verdict on the variant, so it must not be
    # relabeled as the corpus-bug FAIL case above.
    monkeypatch.setattr("skeptic.cli.verify",
                        lambda **kw: (_ for _ in ()).throw(typer.Exit(3)))
    result = runner.invoke(app, ["seed", "--task", TASK_ID, "--check", "--self-validate"])
    assert result.exit_code == 3
    assert "could not run" in result.output
    assert "corpus bug" not in result.output


def test_self_validate_requires_check(seed_check_passing_env):
    result = runner.invoke(app, ["seed", "--task", TASK_ID, "--self-validate"])
    assert result.exit_code == 3
    assert "requires --check" in result.output
