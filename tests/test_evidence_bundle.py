import hashlib
import json
import shutil

import pytest

from skeptic.errors import SkepticInfraError
from skeptic.evalkit import snapshot_run
from tests.helpers import fake_verify_layout, write_fake_artifacts


def prepared(tmp_path):
    run = fake_verify_layout(tmp_path)
    write_fake_artifacts(run)
    report = run / "collect/artifacts/t2_advtests.json"
    report.write_text('{"source":"def test_fix(): assert repaired()","trusted":["c1"]}')
    raw = run / "model-response.json"
    raw.write_text('{"request":{"max_tokens":16000},"response":{"text":"original draw"}}')
    files = {
        name: {"source": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        for name, p in [("checks/t2_advtests.json", report), ("model/response.json", raw)]
    }
    (run / "evidence-plan.json").write_text(
        json.dumps({"version": 1, "status": "complete", "files": files})
    )
    return run


def test_export_keeps_decision_sources_after_workdir_removal(tmp_path):
    run = prepared(tmp_path)
    dest = tmp_path / "export"
    snapshot_run(run, dest)
    shutil.rmtree(run)
    assert (dest / "evidence/checks/t2_advtests.json").is_file()
    assert (
        json.loads((dest / "evidence/model/response.json").read_text())["response"]["text"]
        == "original draw"
    )
    index = json.loads((dest / "evidence/index.json").read_text())
    assert index["status"] == "complete"
    assert index["omissions"]


def test_missing_declared_evidence_prevents_complete_export(tmp_path):
    run = prepared(tmp_path)
    (run / "model-response.json").unlink()
    with pytest.raises(SkepticInfraError):
        snapshot_run(run, tmp_path / "export")
    assert not (tmp_path / "export/evidence/index.json").exists()


def test_changed_declared_evidence_is_not_recertified(tmp_path):
    run = prepared(tmp_path)
    (run / "model-response.json").write_text("replacement")
    with pytest.raises(SkepticInfraError):
        snapshot_run(run, tmp_path / "export")


def test_existing_export_is_not_replaceable(tmp_path):
    run = prepared(tmp_path)
    dest = tmp_path / "export"
    snapshot_run(run, dest)
    with pytest.raises(SkepticInfraError):
        snapshot_run(run, dest)


def test_large_decisive_measurement_is_compressed_not_omitted(tmp_path):
    run = prepared(tmp_path)
    source = run / "measurement"
    source.write_bytes(b"line,context\n" * 150000)
    plan = json.loads((run / "evidence-plan.json").read_text())
    plan["files"]["candidate/coverage.json"] = {
        "source": str(source),
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    (run / "evidence-plan.json").write_text(json.dumps(plan))
    snapshot_run(run, tmp_path / "export")
    import gzip

    stored = tmp_path / "export/evidence/candidate/coverage.json.gz"
    assert gzip.decompress(stored.read_bytes()) == source.read_bytes()
    assert stored.stat().st_size < source.stat().st_size // 10


def test_bundle_validation_rejects_damage_and_missing_index(tmp_path):
    from skeptic.evidence_bundle import validate_bundle

    run = prepared(tmp_path)
    dest = tmp_path / "export"
    snapshot_run(run, dest)
    validate_bundle(dest / "evidence")
    (dest / "evidence/model/response.json").write_text("changed")
    with pytest.raises(SkepticInfraError):
        validate_bundle(dest / "evidence")
    (dest / "evidence/index.json").unlink()
    with pytest.raises(SkepticInfraError):
        validate_bundle(dest / "evidence")


def test_actual_paid_request_and_raw_response_are_retained(tmp_path):
    from types import SimpleNamespace

    from skeptic.llm import call_with_retry
    from skeptic.trace import TraceWriter

    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="original response")],
        usage=SimpleNamespace(input_tokens=3, output_tokens=4),
        stop_reason="end_turn",
    )
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: response))
    trace = TraceWriter(tmp_path / "trace.jsonl", "run", "task")
    call_with_retry(
        client,
        model="claude-haiku-4-5",
        max_tokens=16000,
        system="system",
        messages=[{"role": "user", "content": "base\nProduce exactly 3 tests."}],
        trace=trace,
        stage="VERIFY",
        actor="testgen",
    )
    requests = list((tmp_path / "model-io").glob("*-request.json"))
    assert len(requests) == 1
    assert (
        json.loads(requests[0].read_text())["messages"][0]["content"]
        == "base\nProduce exactly 3 tests."
    )
    responses = list((tmp_path / "model-io").glob("*-response.json"))
    assert json.loads(responses[0].read_text())["content"][0]["text"] == "original response"


def test_builder_paid_request_is_retained(tmp_path):
    from types import SimpleNamespace

    from skeptic.builder import _call_with_retry
    from skeptic.trace import TraceWriter

    response = SimpleNamespace(content=[], usage=SimpleNamespace(input_tokens=1, output_tokens=1))
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: response))
    _call_with_retry(
        client,
        model="test-model",
        messages=[{"role": "user", "content": "task"}],
        trace=TraceWriter(tmp_path / "trace.jsonl", "r", "t"),
    )
    requests = list((tmp_path / "model-io").glob("*-request.json"))
    assert len(requests) == 1
    assert json.loads(requests[0].read_text())["messages"] == [{"role": "user", "content": "task"}]


def test_snapshot_reader_refuses_a_missing_bundle_index(tmp_path):
    from skeptic.evalkit import load_rows

    run = prepared(tmp_path)
    dest = tmp_path / "published/click-0001/gold"
    snapshot_run(run, dest)
    (dest / "evidence/index.json").unlink()
    with pytest.raises(SkepticInfraError):
        load_rows(tmp_path / "published", __import__("pathlib").Path("tasks"))


def test_incomplete_new_run_cannot_export_stale_pass(tmp_path):
    run = prepared(tmp_path)
    (run / "evidence-plan.json").write_text(
        json.dumps({"version": 1, "status": "incomplete", "files": {}})
    )
    dest = tmp_path / "export"
    snapshot_run(run, dest, exit_code=3)
    assert not (dest / "verdict.json").exists()


def test_partial_export_and_removed_version_marker_are_refused(tmp_path):
    from pathlib import Path

    from skeptic.evalkit import load_rows

    run = prepared(tmp_path)
    dest = tmp_path / "published/click-0001/gold"
    snapshot_run(run, dest)
    meta = json.loads((dest / "meta.json").read_text())
    meta.pop("evidence_version")
    (dest / "meta.json").write_text(json.dumps(meta))
    with pytest.raises(SkepticInfraError):
        load_rows(tmp_path / "published", Path("tasks"))
    (dest / "meta.json").unlink()
    (dest / "verdict.json").unlink()
    with pytest.raises(SkepticInfraError):
        load_rows(tmp_path / "published", Path("tasks"))


def test_origin_trace_survives_two_cache_replays(tmp_path):
    from skeptic.evidence_bundle import decision_plan, write_plan
    from skeptic.orchestrator import StageCache, run_stage
    from skeptic.trace import TraceWriter
    from tests.helpers import make_task_spec

    run = prepared(tmp_path)
    inputs = run / "inputs"
    inputs.mkdir()
    trace = TraceWriter(run / "trace.jsonl", "r", "t")
    cache = StageCache(run / "cache")

    def execute():
        trace.event(stage="VERIFY", actor="test", event="llm_call", usage={"usd": 1.25})
        plan = decision_plan(run, make_task_spec(), inputs, {}, trace)
        return {
            "_evidence_plan": plan,
            "_cache_artifacts": [e["source"] for e in plan["files"].values()],
        }

    outcome = run_stage(cache, "VERIFY", "key", execute, trace)
    for i in range(2):
        trace.path.write_text("")
        outcome = run_stage(
            cache, "VERIFY", "key", lambda: pytest.fail("unexpected execution"), trace
        )
    write_plan(run, outcome["_evidence_plan"])
    snapshot_run(run, tmp_path / "export")
    events = [
        json.loads(line)
        for line in (tmp_path / "export/evidence/origin-trace.jsonl").read_text().splitlines()
    ]
    assert any(e["event"] == "llm_call" and e["usage"]["usd"] == 1.25 for e in events)
    assert any(e["event"] == "stage_end" for e in events)


def test_builder_report_bytes_survive_session_report_replacement(tmp_path):
    import base64
    from types import SimpleNamespace

    from skeptic.builder_tools import _report

    xml = b'<testsuite><testcase classname="tests.test_x" name="test_fix"/></testsuite>'
    session = SimpleNamespace(
        file_operation=lambda *a: {"refused": False, "text": base64.b64encode(xml).decode()}
    )
    _report(session, "junit.xml", evidence_dir=tmp_path / "retained")
    files = list((tmp_path / "retained").glob("*.xml"))
    assert len(files) == 1
    assert files[0].read_bytes() == xml


def test_arm_classification_is_bound_after_acceptance(tmp_path):
    from skeptic import evalkit

    run = prepared(tmp_path)
    dest = tmp_path / "arm"
    snapshot_run(run, dest, defer_evidence=True)
    assert not (dest / "evidence/index.json").exists()
    (dest / "classification.json").write_text('{"classification":"legit"}')
    evalkit.finalize_snapshot(run, dest)
    index = json.loads((dest / "evidence/index.json").read_text())
    assert "classification.json" in index["files"]


def test_unknown_bundle_encoding_is_not_treated_as_identity(tmp_path):
    from skeptic.evidence_bundle import validate_bundle

    run = prepared(tmp_path)
    dest = tmp_path / "export"
    snapshot_run(run, dest)
    path = dest / "evidence/index.json"
    index = json.loads(path.read_text())
    index["files"]["model/response.json"]["encoding"] = "unknown"
    path.write_text(json.dumps(index))
    with pytest.raises(SkepticInfraError):
        validate_bundle(dest / "evidence")


def test_origin_publication_failure_is_infrastructure_error(tmp_path, monkeypatch):
    from pathlib import Path

    from skeptic.evidence_bundle import finish_origin
    from skeptic.trace import TraceWriter

    trace = TraceWriter(tmp_path / "trace.jsonl", "r", "t")
    trace.event(stage="VERIFY", actor="test", event="stage_end")
    origin = tmp_path / "origin.jsonl"
    origin.write_text("original")

    def denied(*args, **kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "write_bytes", denied)
    with pytest.raises(SkepticInfraError):
        finish_origin({"files": {"origin-trace.jsonl": {"source": str(origin)}}}, trace)


def test_snapshot_storage_failure_is_infrastructure_error(tmp_path, monkeypatch):
    from pathlib import Path

    run = prepared(tmp_path)

    def denied(*args, **kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "mkdir", denied)
    with pytest.raises(SkepticInfraError):
        snapshot_run(run, tmp_path / "export")


def test_arm_summary_cannot_recertify_a_changed_candidate_patch(tmp_path):
    from skeptic.evalkit import finalize_snapshot

    run = prepared(tmp_path)
    patch = run / "candidate.diff"
    patch.write_text("original candidate")
    plan = json.loads((run / "evidence-plan.json").read_text())
    plan["files"]["candidate.diff"] = {
        "source": str(patch),
        "sha256": hashlib.sha256(patch.read_bytes()).hexdigest(),
    }
    (run / "evidence-plan.json").write_text(json.dumps(plan))
    dest = tmp_path / "arm"
    snapshot_run(run, dest, defer_evidence=True)
    (dest / "candidate.diff").write_text("changed after classification")
    with pytest.raises(SkepticInfraError):
        finalize_snapshot(run, dest)
