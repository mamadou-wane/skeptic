from typer.testing import CliRunner

from skeptic.cli import _build_cache_key, app
from tests.helpers import make_task_spec

runner = CliRunner()


def test_build_refuses_venv_runner(tmp_path):
    result = runner.invoke(app, ["build", "--task", "click-0001",
                                 "--runner", "venv", "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "verify-only" in result.output


def test_build_requires_api_key_before_docker_work(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    called = []
    monkeypatch.setattr("skeptic.cli._docker_available",
                        lambda: called.append("docker") or True)
    result = runner.invoke(app, ["build", "--task", "click-0001",
                                 "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "ANTHROPIC_API_KEY" in result.output
    assert called == []          # key check comes first, plan section 12


def test_build_refuses_unpriced_model_before_docker_work(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    called = []
    monkeypatch.setattr("skeptic.cli._docker_available",
                        lambda: called.append("docker") or True)
    result = runner.invoke(app, ["build", "--task", "click-0001",
                                 "--model", "no-such-model",
                                 "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "No pricing entry" in result.output
    assert called == []          # pricing check comes before the daemon check


def test_build_requires_docker_daemon(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("skeptic.cli._docker_available", lambda: False)
    result = runner.invoke(app, ["build", "--task", "click-0001",
                                 "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "Docker" in result.output


def test_build_prints_cost_and_aborts_without_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("skeptic.cli._docker_available", lambda: True)
    result = runner.invoke(app, ["build", "--task", "click-0001",
                                 "--workdir", str(tmp_path)], input="n\n")
    assert result.exit_code != 0
    assert "$2.00" in result.output   # click-0001's cost_ceiling_usd


# 2026-07-26 review finding 1: the cache key must change whenever a spec
# field that shapes the Builder's run (or its first message) changes, or a
# resumed run silently replays a stale candidate for the edited spec.

def test_build_cache_key_changes_with_problem_statement():
    spec = make_task_spec()
    base = _build_cache_key(spec, "claude-sonnet-5", "img-id", "seed-hash")
    edited = spec.model_copy(update={
        "builder_input": spec.builder_input.model_copy(update={
            "problem_statement": spec.builder_input.problem_statement + " (edited)",
        }),
    })
    changed = _build_cache_key(edited, "claude-sonnet-5", "img-id", "seed-hash")
    assert base != changed


def test_build_cache_key_changes_with_repo_commit():
    spec = make_task_spec()
    base = _build_cache_key(spec, "claude-sonnet-5", "img-id", "seed-hash")
    edited = spec.model_copy(update={
        "repo": spec.repo.model_copy(update={"commit": "0" * 40}),
    })
    assert edited.repo.commit != spec.repo.commit
    changed = _build_cache_key(edited, "claude-sonnet-5", "img-id", "seed-hash")
    assert base != changed
