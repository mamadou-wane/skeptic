import pytest
from typer.testing import CliRunner

from skeptic.cli import app
from tests.helpers import make_minirepo_task

runner = CliRunner()


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
