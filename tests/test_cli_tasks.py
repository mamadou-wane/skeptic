from pathlib import Path

from typer.testing import CliRunner

from skeptic.cli import app

runner = CliRunner()

FIXTURES = Path(__file__).parent / "fixtures" / "specs"


def test_tasks_lists_the_corpus():
    result = runner.invoke(app, ["tasks"])
    assert result.exit_code == 0
    assert "click-0001" in result.output and "rich-0001" in result.output
    assert "· acceptance" in result.output
    assert "no acceptance suite" not in result.output


def test_tasks_on_an_empty_dir_says_so_and_names_the_next_step(tmp_path):
    result = runner.invoke(app, ["tasks", "--tasks-dir", str(tmp_path)])
    assert result.exit_code == 3
    assert "no task specs" in result.output


def test_tasks_marks_a_task_with_no_acceptance_suite(tmp_path):
    (tmp_path / "click-0001.yaml").write_text((FIXTURES / "valid-task.yaml").read_text())
    result = runner.invoke(app, ["tasks", "--tasks-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "no acceptance suite" in result.output


def test_tasks_on_a_corrupt_yaml_reports_infra_error_not_a_traceback(tmp_path):
    text = (FIXTURES / "valid-task.yaml").read_text().replace(
        "  bug_patch: patches/click-0001-seed.diff\n", ""
    )
    (tmp_path / "click-0001.yaml").write_text(text)
    result = runner.invoke(app, ["tasks", "--tasks-dir", str(tmp_path)])
    assert result.exit_code == 3
    assert "INFRA ERROR" in result.output
    assert "bug_patch" in result.output
    assert "Traceback" not in result.output
