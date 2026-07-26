import json

import pytest

from skeptic.orchestrator import StageCache, run_stage
from skeptic.trace import TraceWriter, read_trace


def test_stage_runs_once_then_cache_hits(tmp_path):
    cache = StageCache(tmp_path / "stages")
    trace = TraceWriter(tmp_path / "t.jsonl", run_id="r", task_id="t")
    calls = []

    def fn():
        calls.append(1)
        return {"result": 42}

    first = run_stage(cache, "SEED", "k1", fn, trace)
    second = run_stage(cache, "SEED", "k1", fn, trace)
    assert first == second == {"result": 42}
    assert len(calls) == 1
    events, _ = read_trace(tmp_path / "t.jsonl")
    names = [e["event"] for e in events]
    assert names == ["stage_start", "stage_end", "stage_cached"]


def test_different_key_recomputes(tmp_path):
    cache = StageCache(tmp_path / "stages")
    trace = TraceWriter(tmp_path / "t.jsonl", run_id="r", task_id="t")
    calls = []

    def fn():
        calls.append(1)
        return {"n": len(calls)}

    assert run_stage(cache, "SEED", "k1", fn, trace) == {"n": 1}
    assert run_stage(cache, "SEED", "k2", fn, trace) == {"n": 2}
    assert len(calls) == 2


def test_cache_survives_new_instance(tmp_path):
    trace = TraceWriter(tmp_path / "t.jsonl", run_id="r", task_id="t")
    run_stage(StageCache(tmp_path / "s"), "LOAD", "k", lambda: {"v": 1}, trace)
    calls = []
    result = run_stage(
        StageCache(tmp_path / "s"), "LOAD", "k",
        lambda: calls.append(1) or {"v": 2}, trace,
    )
    assert result == {"v": 1}
    assert calls == []


def test_put_is_atomic_no_tmp_left_behind(tmp_path):
    cache = StageCache(tmp_path)
    cache.put("k", {"a": 1})
    assert [p.name for p in tmp_path.iterdir()] == ["k.json"]


def test_get_treats_corrupt_cache_as_miss(tmp_path):
    cache = StageCache(tmp_path)
    (tmp_path / "k.json").write_text('{"truncat')
    assert cache.get("k") is None


def test_run_stage_emits_stage_error_on_exception(tmp_path):
    cache = StageCache(tmp_path / "c")
    trace = TraceWriter(tmp_path / "t.jsonl", run_id="r", task_id="t")

    def boom() -> dict:
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        run_stage(cache, "BUILD", "k", boom, trace)
    events = [json.loads(line)["event"]
              for line in (tmp_path / "t.jsonl").read_text().splitlines()]
    assert events == ["stage_start", "stage_error"]
