from typer.testing import CliRunner

from skeptic import __version__
from skeptic.cli import app

runner = CliRunner()


def test_version_flag_prints_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_no_args_shows_help_not_traceback():
    result = runner.invoke(app, [])
    assert "Usage" in result.output
