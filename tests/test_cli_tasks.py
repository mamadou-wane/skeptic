from typer.testing import CliRunner

from skeptic.cli import app

runner = CliRunner()


def test_tasks_lists_the_corpus():
    result = runner.invoke(app, ["tasks"])
    assert result.exit_code == 0
    assert "click-0001" in result.output and "rich-0001" in result.output
    assert "acceptance" in result.output


def test_tasks_on_an_empty_dir_says_so_and_names_the_next_step(tmp_path):
    result = runner.invoke(app, ["tasks", "--tasks-dir", str(tmp_path)])
    assert result.exit_code == 3
    assert "no task specs" in result.output
