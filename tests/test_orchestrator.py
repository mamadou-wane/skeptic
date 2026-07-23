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
