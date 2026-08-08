from pathlib import Path
from types import SimpleNamespace

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
    # 2026-07-26 review finding 2: a decline must exit EXIT_INFRA (3), not
    # the EXIT_SUSPECT (1) that typer.confirm(abort=True) collided with.
    assert result.exit_code == 3
    assert "$2.00" in result.output   # click-0001's cost_ceiling_usd
    assert "Declined" in result.output


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


def test_build_cache_key_changes_with_green_rule_version(monkeypatch):
    # Today a change to the green rule also moves prompt_version(), because
    # the Builder-facing text had to change with it. That is luck: a future
    # edit to the predicate alone would leave the key still and serve a
    # cached `green` under the new rule's name.
    spec = make_task_spec()
    base = _build_cache_key(spec, "claude-sonnet-5", "img-id", "seed-hash")
    monkeypatch.setattr("skeptic.builder.GREEN_RULE_VERSION", "differential-2")
    changed = _build_cache_key(spec, "claude-sonnet-5", "img-id", "seed-hash")
    assert base != changed


# Task 15: attempt-salted BUILD cache keys and per-attempt build dirs.
#
# PRE_SALT_KEY_FOR_SPEC is the literal `_build_cache_key(make_task_spec(),
# "claude-opus-5", "sha256:abc", "seed")` produced on this tree before the
# `attempt` parameter existed. config_hash is a pure function of the
# payload dict, and the payload holds only spec-derived data (task id, repo
# commit, and the constraints/builder_input/environment sub-models, all
# plain strings/ints/lists per skeptic/spec.py, no absolute paths and
# nothing machine-local), so this literal is stable across machines.
PRE_SALT_KEY_FOR_SPEC = "06c04a648d53"


def test_two_attempts_are_two_cache_entries():
    spec = make_task_spec()
    a = _build_cache_key(spec, "claude-opus-5", "sha256:abc", "seed", attempt=1)
    b = _build_cache_key(spec, "claude-opus-5", "sha256:abc", "seed", attempt=2)
    assert a != b


def test_attempt_one_keeps_the_pre_attempt_key():
    """Attempt 1 must hash exactly as it did before the salt existed.

    Every cached BUILD in a live workdir was written under the old key. If
    attempt 1 changes shape, the whole existing cache misses and the base
    arm silently pays to re-run work it already has.
    """
    spec = make_task_spec()
    assert _build_cache_key(spec, "claude-opus-5", "sha256:abc", "seed",
                            attempt=1) == PRE_SALT_KEY_FOR_SPEC


def test_attempt_salts_the_trace_run_id_too():
    # cli.py builds run_id as f"build-{cache_key}", so a salted attempt's
    # cache key difference propagates to the trace identity for free, with
    # no separate wiring to test at the TraceWriter call site.
    spec = make_task_spec()
    key1 = _build_cache_key(spec, "claude-opus-5", "sha256:abc", "seed", attempt=1)
    key2 = _build_cache_key(spec, "claude-opus-5", "sha256:abc", "seed", attempt=2)
    assert f"build-{key1}" != f"build-{key2}"


def test_attempt_two_gets_its_own_build_dir(monkeypatch, tmp_path):
    from skeptic import workspace

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("skeptic.cli._docker_available", lambda: True)

    def _stop(*args, **kwargs):
        raise RuntimeError("stop-after-mkdir")

    monkeypatch.setattr(workspace, "clone_pinned", _stop)
    runner.invoke(app, ["build", "--task", "click-0001", "--attempt", "2",
                        "--workdir", str(tmp_path), "--yes"])
    assert (tmp_path / "click-0001" / "build" / "attempt-2").is_dir()


def test_attempt_one_keeps_todays_build_dir_path(monkeypatch, tmp_path):
    from skeptic import workspace

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("skeptic.cli._docker_available", lambda: True)

    def _stop(*args, **kwargs):
        raise RuntimeError("stop-after-mkdir")

    monkeypatch.setattr(workspace, "clone_pinned", _stop)
    runner.invoke(app, ["build", "--task", "click-0001",
                        "--workdir", str(tmp_path), "--yes"])
    assert (tmp_path / "click-0001" / "build").is_dir()
    assert not (tmp_path / "click-0001" / "build" / "attempt-1").exists()


def test_build_rejects_non_positive_attempt(tmp_path):
    for bad in ("0", "-1"):
        result = runner.invoke(app, ["build", "--task", "click-0001",
                                     "--attempt", bad, "--workdir", str(tmp_path)])
        assert result.exit_code == 3, result.output
        assert "--attempt" in result.output
        assert "Next:" in result.output


def test_build_writes_a_baseline_suite_trace_event_on_a_cache_hit(tmp_path, monkeypatch):
    # DECISIONS row 74 puts the baseline red set in the trace on every run.
    # A stage-cache hit never executes do_build, so the CLI replays the event
    # from the cached dict; a terminal line alone would not satisfy the row.
    from skeptic import candidate, cli, image, workspace
    from skeptic.orchestrator import StageCache
    from skeptic.spec import find_task
    from skeptic.trace import config_hash, read_trace

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    monkeypatch.setattr(workspace, "clone_pinned",
                        lambda url, commit, cache: cache)
    monkeypatch.setattr(workspace, "materialize",
                        lambda repo, commit, dest: dest.mkdir(parents=True))
    monkeypatch.setattr(workspace, "apply_patch", lambda ws, patch: None)
    monkeypatch.setattr(candidate, "snapshot", lambda src, dest: None)
    monkeypatch.setattr(
        image, "ensure_repo_image",
        lambda task_spec, context, out: SimpleNamespace(tag="t:1", image_id="img-id"))

    workdir = tmp_path.resolve()
    spec = find_task("click-0001", Path("tasks"))
    seed_hash = config_hash({"seed": Path(spec.seed.bug_patch).read_text()})
    key = _build_cache_key(spec, "claude-opus-5", "img-id", seed_hash)
    build_dir = workdir / spec.task_id / "build"
    cached = {
        "stop_reason": "green", "iterations": 2, "in_tokens": 10,
        "out_tokens": 5, "usd": 0.01, "green": True,
        "green_rule": "differential-1",
        "baseline_seed_red": ["tests/test_termui.py::test_progressbar_width"],
        "baseline_environmental_red": ["tests/test_pager.py::test_pager"],
        "baseline_total": 3, "baseline_collection_errors": 0,
        "candidate": str(build_dir / "candidate.diff"),
        "changed_files": ["src/click/termui.py"], "out_of_scope": [],
        "is_empty": False, "image_id": "img-id",
    }
    StageCache(build_dir / "cache").put(key, cached)

    result = runner.invoke(app, ["build", "--task", "click-0001",
                                 "--workdir", str(workdir), "--yes"])

    assert result.exit_code == 0, result.output
    assert "green: True" in result.output
    events, _ = read_trace(build_dir / "trace.jsonl")
    payload = next(e for e in events if e["event"] == "baseline_suite")["payload"]
    assert payload["cached"] is True
    assert payload["seed_red"] == cached["baseline_seed_red"]
    assert payload["environmental_red"] == cached["baseline_environmental_red"]
    assert payload["collection_errors"] == 0
