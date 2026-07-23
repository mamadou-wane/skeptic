import json

from skeptic.trace import TraceWriter, config_hash, read_trace, write_manifest


def test_events_append_as_jsonl_with_schema_version(tmp_path):
    p = tmp_path / "trace.jsonl"
    w = TraceWriter(p, run_id="r_test", task_id="click-0001")
    w.event(stage="SEED", actor="orchestrator", event="stage_start")
    w.event(stage="SEED", actor="orchestrator", event="stage_end", dur_ms=12,
            payload={"ok": True})
    events, skipped = read_trace(p)
    assert skipped == 0
    assert len(events) == 2
    assert events[0]["schema_version"] == 1
    assert events[0]["run_id"] == "r_test"
    assert events[0]["task_id"] == "click-0001"
    assert events[1]["payload"] == {"ok": True}
    assert events[1]["dur_ms"] == 12
    assert "ts" in events[0]


def test_reader_skips_corrupt_lines_and_counts_them(tmp_path):
    p = tmp_path / "trace.jsonl"
    w = TraceWriter(p, run_id="r", task_id="t")
    w.event(stage="LOAD", actor="orchestrator", event="a")
    with p.open("a") as fh:
        fh.write("{corrupt json\n")
    w.event(stage="LOAD", actor="orchestrator", event="b")
    events, skipped = read_trace(p)
    assert [e["event"] for e in events] == ["a", "b"]
    assert skipped == 1


def test_config_hash_is_stable_and_order_independent():
    a = config_hash({"model": "m1", "budget": 30, "nested": {"x": 1, "y": 2}})
    b = config_hash({"nested": {"y": 2, "x": 1}, "budget": 30, "model": "m1"})
    assert a == b
    assert len(a) == 12
    assert config_hash({"model": "m2", "budget": 30, "nested": {"x": 1, "y": 2}}) != a


def test_manifest_written_with_schema_version(tmp_path):
    p = tmp_path / "manifest.json"
    write_manifest(p, {"run_id": "r_1", "config_hash": "abc"})
    data = json.loads(p.read_text())
    assert data["schema_version"] == 1
    assert data["run_id"] == "r_1"


def test_manifest_injected_schema_version_wins_over_caller_key(tmp_path):
    p = tmp_path / "manifest.json"
    write_manifest(p, {"schema_version": 99, "run_id": "r_2"})
    data = json.loads(p.read_text())
    assert data["schema_version"] == 1
    assert data["run_id"] == "r_2"
