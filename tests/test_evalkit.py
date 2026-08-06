from skeptic.evalkit import rotate_trace, snapshot_run
from skeptic.trace import read_trace
from tests.helpers import append_trace, fake_verify_layout, write_fake_artifacts


def test_rotate_then_snapshot_holds_exactly_one_runs_events(tmp_path):
    verify_dir = fake_verify_layout(tmp_path, trace_events=[{"event": "old_run_event_name"}])
    rotate_trace(verify_dir)
    append_trace(verify_dir, [{"event": "new_run_event_name"}])  # simulates the driven run
    write_fake_artifacts(verify_dir)  # verdict.json + t1_outcomes.json
    meta = snapshot_run(verify_dir, tmp_path / "snap")
    events, _ = read_trace(tmp_path / "snap" / "trace.jsonl")
    assert [e["event"] for e in events] == ["new_run_event_name"]
    assert meta["replayed"] is False


def test_snapshot_marks_replayed_on_stage_cached(tmp_path):
    verify_dir = fake_verify_layout(tmp_path, trace_events=[{"event": "stage_cached"}])
    write_fake_artifacts(verify_dir)
    meta = snapshot_run(verify_dir, tmp_path / "snap")
    assert meta["replayed"] is True
