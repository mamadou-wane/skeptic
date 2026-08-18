import hashlib
import json
from pathlib import Path

import typer
from typer.testing import CliRunner

from skeptic import cli
from skeptic.cli import app
from skeptic.sandbox import DockerDiagnosis

_DIAG_DOWN = DockerDiagnosis("unreachable", "test")
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
    # and nowhere else: the top-level write used to overwrite the published
    # evals/v1/manifest.json on any default-`--out` sweep
    assert not (tmp_path / "evals" / "manifest.json").exists()
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


def test_eval_drives_the_real_verify_and_the_guard_does_not_misfire(monkeypatch, tmp_path):
    """Regression, task 2b review finding 1: every other test in this file
    monkeypatches `skeptic.cli.verify` with a `**kw` fake, so none of them
    exercise the real function's own parameter defaults. `eval`'s sweep loop
    calls `verify()` as a plain Python function, never through typer's CLI
    parsing, so a parameter it does not pass explicitly falls back to its
    `typer.Option(...)` default: an `OptionInfo` object, not the `None` the
    `Path | None` type hint promises. `candidate_diff` used to be exactly
    that unpassed parameter, so `verify`'s own exactly-one-of guard read it
    as "given" (`is None` false) and fired against every legitimate
    `--variant`-only call reachable from `eval`, no matter what was passed.
    Docker unavailable is what should actually stop this run; the guard
    firing instead, with `--variant` correctly supplied, was the bug."""
    monkeypatch.setattr(cli, "_docker_diagnosis", lambda: _DIAG_DOWN)
    result = runner.invoke(app, ["eval", "--tasks", "click-0001",
                                 "--profile", "deterministic",
                                 "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals")])
    assert "Exactly one of" not in result.output
    assert "Docker daemon unavailable" in result.output
    assert result.exit_code == cli.EXIT_INFRA


# --- M6: `--registry`, the holdout lane ------------------------------------


def _holdout_registry(tmp_path, rows) -> Path:
    """A registry yaml plus the patch files it names. `rows` is
    (task_id, variant_id, hack_category)."""
    lines = ["variants:"]
    for task_id, variant_id, category in rows:
        patch = tmp_path / f"{task_id}-{variant_id}.diff"
        patch.write_text(f"--- a/{variant_id}\n+++ b/{variant_id}\n")
        digest = hashlib.sha256(patch.read_bytes()).hexdigest()
        lines += [f"- task_id: {task_id}", f"  variant_id: {variant_id}",
                  f"  hack_category: {category}", f"  patch: {patch}",
                  f"  sha256: {digest}"]
    path = tmp_path / "registry.yaml"
    path.write_text("\n".join(lines) + "\n")
    return path


def test_eval_registry_sweeps_by_registry_id_and_records_the_patch_hashes(
    monkeypatch, tmp_path
):
    """The holdout sweep: one verify run per registry row, driven through
    `--variant-patch` so the run identity, the snapshot directory and the
    table row all carry the registry id. The manifest records each patch's
    sha256 under `holdout`, without which a published holdout row traces to
    nothing."""
    calls = []

    def fake_verify(**kw):
        calls.append(kw)
        write_fake_run(tmp_path, kw["task"], kw["variant_patch"].split(":", 1)[0])
        raise typer.Exit(0)

    monkeypatch.setattr("skeptic.cli.verify", fake_verify)
    registry = _holdout_registry(tmp_path, [("click-0001", "h5-holdout", "H5"),
                                            ("rich-0001", "h6-holdout", "H6")])
    result = runner.invoke(app, ["eval", "--registry", str(registry),
                                 "--profile", "deterministic",
                                 "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals")])

    assert result.exit_code == 0, result.output
    assert [kw["variant"] for kw in calls] == [None, None]
    assert [kw["variant_patch"].split(":", 1)[0] for kw in calls] == [
        "h5-holdout", "h6-holdout"]
    assert [kw["task"] for kw in calls] == ["click-0001", "rich-0001"]

    run_dir = next((tmp_path / "evals" / "runs").iterdir())
    assert (run_dir / "click-0001" / "h5-holdout" / "verdict.json").is_file()
    assert (run_dir / "rich-0001" / "h6-holdout" / "verdict.json").is_file()

    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert set(manifest["holdout"]) == {"click-0001", "rich-0001"}
    assert set(manifest["holdout"]["click-0001"]) == {"h5-holdout"}
    assert "weights_sha256" in manifest

    # both rows labeled hacked from the registry: without it, load_rows would
    # have raised on a variant no task yaml declares
    table = (run_dir / "table.md").read_text()
    assert "| detection (lenient) | 0/2 |" in table
    assert "| H5 | PASS | 1 |" in table


def test_eval_registry_and_tasks_are_mutually_exclusive(tmp_path):
    registry = _holdout_registry(tmp_path, [("click-0001", "h5-holdout", "H5")])
    result = runner.invoke(app, ["eval", "--tasks", "click-0001",
                                 "--registry", str(registry),
                                 "--profile", "deterministic",
                                 "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals")])
    assert result.exit_code == 3
    assert "Exactly one of --tasks or --registry" in result.output
    assert "Next:" in result.output


def test_eval_needs_a_subject(tmp_path):
    result = runner.invoke(app, ["eval", "--profile", "deterministic",
                                 "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals")])
    assert result.exit_code == 3
    assert "Exactly one of --tasks or --registry" in result.output


def test_eval_registry_refuses_a_patch_whose_bytes_moved(tmp_path, monkeypatch):
    """The registry's digest is the holdout's provenance claim, so a sweep
    over an edited patch never starts: verify is not called at all."""
    called = []
    monkeypatch.setattr("skeptic.cli.verify", lambda **kw: called.append(kw))
    registry = _holdout_registry(tmp_path, [("click-0001", "h5-holdout", "H5")])
    (tmp_path / "click-0001-h5-holdout.diff").write_text("--- a/edited\n+++ b/edited\n")

    result = runner.invoke(app, ["eval", "--registry", str(registry),
                                 "--profile", "deterministic",
                                 "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals")])
    assert result.exit_code == 3
    assert "hashes to" in result.output
    assert called == []


def test_eval_registry_paid_confirm_names_the_registry(monkeypatch, tmp_path):
    monkeypatch.setattr("skeptic.cli.verify", lambda **kw: (_ for _ in ()).throw(typer.Exit(0)))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    registry = _holdout_registry(tmp_path, [("click-0001", "h5-holdout", "H5"),
                                            ("rich-0001", "h6-holdout", "H6")])
    result = runner.invoke(app, ["eval", "--registry", str(registry),
                                 "--profile", "paid",
                                 "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals")], input="n\n")
    assert result.exit_code == 3
    per_run, sweep_max = _expected_cost_lines(n_pairs=2)  # one run per registry row
    assert per_run in result.output and sweep_max in result.output
    assert f"--registry {registry}" in result.output
