import typer
from typer.testing import CliRunner

from skeptic.cli import app
from tests.helpers import write_fake_run

runner = CliRunner()


def test_eval_command_sweeps_every_variant_and_writes_manifest(monkeypatch, tmp_path):
    calls = []

    def fake_verify(**kw):
        calls.append((kw["task"], kw["variant"]))
        write_fake_run(tmp_path, kw["task"], kw["variant"])  # layout + artifacts
        raise typer.Exit(0)

    monkeypatch.setattr("skeptic.cli.verify", fake_verify)
    result = runner.invoke(app, ["eval", "--tasks", "click-0001,rich-0001",
                                 "--profile", "deterministic",
                                 "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals")])
    assert result.exit_code == 0, result.output
    assert ("click-0001", "gold") in calls and ("rich-0001", "gold-prime") in calls
    run_dirs = list((tmp_path / "evals" / "runs").iterdir())
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "manifest.json").is_file()
    assert (tmp_path / "evals" / "manifest.json").is_file()
    assert (run_dirs[0] / "click-0001" / "gold" / "verdict.json").is_file()


def test_eval_command_records_infra_and_exits_3(monkeypatch, tmp_path):
    monkeypatch.setattr("skeptic.cli.verify",
                        lambda **kw: (_ for _ in ()).throw(typer.Exit(3)))
    result = runner.invoke(app, ["eval", "--tasks", "click-0001",
                                 "--profile", "deterministic",
                                 "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals")])
    assert result.exit_code == 3
    assert "INFRA" in result.output
