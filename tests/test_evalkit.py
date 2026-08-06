import json
from pathlib import Path

from skeptic.evalkit import build_manifest, rotate_trace, snapshot_run
from skeptic.image import repo_image_tag
from skeptic.spec import find_task
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


def test_snapshot_skips_na_stub_t2_judge_but_keeps_a_real_one(tmp_path):
    """`checks.aggregate.run_verify_layer` writes a `t2_judge.json` NA stub
    under every non-paid profile (review finding 1): the file exists, but
    the snapshot's own absence-means-no-data contract requires it to be left
    out, keyed on the artifact's own `status` rather than the filename."""
    na_dir = fake_verify_layout(tmp_path, task="click-0001", variant="gold")
    write_fake_artifacts(na_dir, t2_judge={
        "check": "t2_judge", "status": "not_applicable",
        "reason": "excluded by profile: deterministic"})
    snapshot_run(na_dir, tmp_path / "snap-na")
    assert not (tmp_path / "snap-na" / "t2_judge.json").exists()

    real_dir = fake_verify_layout(tmp_path, task="click-0001", variant="gold-prime")
    write_fake_artifacts(real_dir, t2_judge={
        "check": "t2_judge", "status": "completed",
        "report": {"model": "m", "flagged": False, "category": None, "rationale": "clean"}})
    snapshot_run(real_dir, tmp_path / "snap-real")
    assert (tmp_path / "snap-real" / "t2_judge.json").is_file()


def test_build_manifest_shape_and_image_id_fallback(tmp_path):
    """Review finding 2: build_manifest/_image_id had no committed test.
    click-0001 gets a build/result.json image_id (used); rich-0001 gets none
    (falls back to repo_image_tag). Pins the manifest's shape, not any
    hash's literal value."""
    click = find_task("click-0001", Path("tasks"))
    rich = find_task("rich-0001", Path("tasks"))

    build_dir = tmp_path / "click-0001" / "build"
    build_dir.mkdir(parents=True)
    (build_dir / "result.json").write_text(json.dumps({"image_id": "sha256:deadbeef"}))

    manifest = build_manifest([click, rich], tmp_path)

    assert set(manifest) == {
        "verifier_revision", "collector_version", "model", "prompt_hash", "tasks"}
    assert manifest["tasks"]["click-0001"]["image_id"] == "sha256:deadbeef"
    assert manifest["tasks"]["rich-0001"]["image_id"] == repo_image_tag(rich)

    for task_id, spec in (("click-0001", click), ("rich-0001", rich)):
        entry = manifest["tasks"][task_id]
        assert set(entry) == {"seed", "variants", "mutation_seed", "image_id"}
        assert entry["mutation_seed"] == spec.verification.mutation.seed
        assert set(entry["variants"]) == {v.id for v in spec.evaluation.variants}
        assert len(entry["seed"]) == 64  # sha256 hexdigest length: shape, not value
        assert all(len(h) == 64 for h in entry["variants"].values())
