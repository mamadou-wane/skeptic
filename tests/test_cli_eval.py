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


def test_eval_writes_the_table_beside_the_snapshots(monkeypatch, tmp_path):
    def fake_verify(**kw):
        write_fake_run(tmp_path, kw["task"], kw["variant"])
        raise typer.Exit(0)

    monkeypatch.setattr("skeptic.cli.verify", fake_verify)
    result = runner.invoke(app, ["eval", "--tasks", "click-0001",
                                 "--profile", "deterministic",
                                 "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals")])
    assert result.exit_code == 0, result.output
    run_dir = next((tmp_path / "evals" / "runs").iterdir())
    table = (run_dir / "table.md").read_text()
    assert "| detection (lenient) |" in table
    assert "| always-SUSPECT |" in table
    assert str(run_dir / "table.md") in result.output
    assert "tasks 12-13" not in result.output
    assert "render the table from the run dir" not in result.output


def test_eval_command_records_infra_and_exits_3(monkeypatch, tmp_path):
    monkeypatch.setattr("skeptic.cli.verify",
                        lambda **kw: (_ for _ in ()).throw(typer.Exit(3)))
    result = runner.invoke(app, ["eval", "--tasks", "click-0001",
                                 "--profile", "deterministic",
                                 "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals")])
    assert result.exit_code == 3
    assert "INFRA" in result.output
    run_dir = next((tmp_path / "evals" / "runs").iterdir())
    table = (run_dir / "table.md").read_text()
    assert "INFRA: 4" in table
    assert str(run_dir / "table.md") in result.output


# --- the paid preflight (review finding 3): pure arithmetic and
# confirmation, no API calls. `verify` is monkeypatched in every case below,
# the same as the deterministic tests above. -------------------------------


def _expected_cost_lines(n_pairs: int) -> tuple[str, str]:
    from skeptic.builder import _price
    from skeptic.llm import SKEPTIC_MODEL

    est_per_run = _price(SKEPTIC_MODEL, 30_000, 16_000) + _price(SKEPTIC_MODEL, 10_000, 2_000)
    return f"${est_per_run:.2f}", f"${est_per_run * n_pairs:.2f}"


def test_eval_paid_confirm_declined_exits_infra_without_calling_verify(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "skeptic.cli.verify",
        lambda **kw: calls.append(kw) or (_ for _ in ()).throw(typer.Exit(0)))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    result = runner.invoke(app, ["eval", "--tasks", "click-0001",
                                 "--profile", "paid",
                                 "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals")], input="n\n")
    assert result.exit_code == 3
    assert "Declined" in result.output
    assert "--yes" in result.output
    assert calls == []


def test_eval_paid_confirm_accepted_calls_verify_with_yes_and_prints_cost(monkeypatch, tmp_path):
    calls = []

    def fake_verify(**kw):
        calls.append(kw)
        write_fake_run(tmp_path, kw["task"], kw["variant"])
        raise typer.Exit(0)

    monkeypatch.setattr("skeptic.cli.verify", fake_verify)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    result = runner.invoke(app, ["eval", "--tasks", "click-0001",
                                 "--profile", "paid",
                                 "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals")], input="y\n")
    assert result.exit_code == 0, result.output
    per_run, sweep_max = _expected_cost_lines(n_pairs=4)  # click-0001: gold/gold-prime/h5/h1
    assert per_run in result.output
    assert sweep_max in result.output
    assert len(calls) == 4
    assert all(kw["yes"] is True and kw["profile"] == "paid" for kw in calls)


def test_eval_paid_yes_skips_confirm_and_drives_the_sweep(monkeypatch, tmp_path):
    calls = []

    def fake_verify(**kw):
        calls.append(kw)
        write_fake_run(tmp_path, kw["task"], kw["variant"])
        raise typer.Exit(0)

    monkeypatch.setattr("skeptic.cli.verify", fake_verify)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    result = runner.invoke(app, ["eval", "--tasks", "click-0001",
                                 "--profile", "paid", "--yes",
                                 "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals")])
    assert result.exit_code == 0, result.output
    assert "Proceed" not in result.output
    assert len(calls) == 4
    assert all(kw["yes"] is True for kw in calls)
