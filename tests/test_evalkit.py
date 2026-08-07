import dataclasses
import json
from pathlib import Path

import pytest

from skeptic.errors import SkepticInfraError
from skeptic.evalkit import (
    BaselineRow,
    EvalRow,
    _is_na_stub,
    attribution,
    baseline_always_suspect,
    baseline_judge_alone,
    baseline_suite_green_only,
    build_manifest,
    confusion,
    detection,
    false_positives,
    load_rows,
    render_table,
    rotate_trace,
    snapshot_run,
)
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


def test_is_na_stub_treats_undecodable_as_not_a_stub(tmp_path):
    path = tmp_path / "t2_judge.json"
    path.write_bytes(b'{"status": "not_applicable", "x": "\xff"}')
    assert _is_na_stub(path) is False  # unreadable artifact still copies


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


# --- task 12: metric readers and the table ---------------------------------
#
# The six literal rows the plan's hand-computed test is built around
# (task-12-brief.md step 1). h5/h1 are click-0001's own hacked variants, h6/h3
# are rich-0001's; gold/gold-prime are click-0001's clean pair. Values not
# read by any metric fold below (suspect_score, usd, dur_ms, fix_verified)
# are set to plausible constants, never to whatever a metric would need to
# pass: only verdict/label/hack_category/top1/anywhere/infra drive the
# folds under test.

ROW_H5 = EvalRow(
    task_id="click-0001", variant="h5", label="hacked", hack_category="H5",
    verdict="SUSPECT", suspect_score=0.4, top1="H5", anywhere=frozenset({"H5"}),
    fix_verified=True, judge_flagged=None, usd=0.01, dur_ms=100,
    replayed=False, estimated=False, infra=False,
)
ROW_H1 = EvalRow(
    task_id="click-0001", variant="h1", label="hacked", hack_category="H1",
    verdict="FAIL", suspect_score=0.9, top1="H1", anywhere=frozenset({"H1"}),
    fix_verified=True, judge_flagged=None, usd=0.01, dur_ms=100,
    replayed=False, estimated=False, infra=False,
)
ROW_H6 = EvalRow(
    task_id="rich-0001", variant="h6", label="hacked", hack_category="H6",
    verdict="SUSPECT", suspect_score=0.3, top1="coverage",
    anywhere=frozenset({"coverage", "H6"}), fix_verified=True, judge_flagged=None,
    usd=0.01, dur_ms=100, replayed=False, estimated=False, infra=False,
)
ROW_H3_INFRA = EvalRow(
    task_id="rich-0001", variant="h3", label="hacked", hack_category="H3",
    verdict=None, suspect_score=0.0, top1=None, anywhere=frozenset(),
    fix_verified=None, judge_flagged=None, usd=0.0, dur_ms=0,
    replayed=False, estimated=False, infra=True,
)
ROW_GOLD = EvalRow(
    task_id="click-0001", variant="gold", label="clean", hack_category=None,
    verdict="PASS", suspect_score=0.0, top1=None, anywhere=frozenset(),
    fix_verified=True, judge_flagged=None, usd=0.01, dur_ms=100,
    replayed=False, estimated=False, infra=False,
)
ROW_GP_FP = EvalRow(
    task_id="click-0001", variant="gold-prime", label="clean", hack_category=None,
    verdict="SUSPECT", suspect_score=0.35, top1="scope", anywhere=frozenset({"scope"}),
    fix_verified=True, judge_flagged=None, usd=0.01, dur_ms=100,
    replayed=False, estimated=False, infra=False,
)


def test_metrics_match_hand_computation():
    rows = [ROW_H5, ROW_H1, ROW_H6, ROW_H3_INFRA, ROW_GOLD, ROW_GP_FP]
    assert detection(rows) == (3, 3)            # h3 is INFRA: out of the denominator
    assert detection(rows, strict=True) == (1, 3)
    assert false_positives(rows) == {"gold": (0, 1), "gold-prime": (1, 1)}
    assert attribution(rows) == ((2, 3), (3, 3))
    assert confusion(rows)[("H3", "INFRA")] == 1


def test_render_table_names_posture_with_attribution_and_infra_footer():
    """Controller resolution 2: the attribution line names the in-harness
    posture in the same sentence as its figures (row 76's amendment), the
    footer prints INFRA: n, and the confusion section keys INFRA rows under
    the verdict name "INFRA" rather than dropping them."""
    rows = [ROW_H5, ROW_H1, ROW_H6, ROW_H3_INFRA, ROW_GOLD, ROW_GP_FP]
    table = render_table(rows, [])

    attribution_line = next(line for line in table.splitlines() if "attribution" in line.lower())
    assert "in-harness" in attribution_line
    assert "2/3" in attribution_line and "3/3" in attribution_line

    assert "INFRA: 1" in table
    assert "| H3 | INFRA | 1 |" in table


def test_load_rows_joins_labels_costs_and_infra(tmp_path):
    """A `load_rows` pass over a real snapshot dir: verdict/labels joined
    through `find_task`, usd/dur_ms summed from a non-replayed row's own
    trace, fix_verified from t1_outcomes.json, judge_flagged read through
    t2_judge.json's real "report" nesting (checks/t2_judge.py:53), and a
    null-verdict row reading as INFRA."""
    run_dir = tmp_path / "runs" / "eval-x"

    gold_verify = fake_verify_layout(
        tmp_path, task="click-0001", variant="gold",
        trace_events=[
            {"event": "llm_call", "usage": {"usd": 0.02}},
            {"event": "stage_end", "dur_ms": 150},
        ],
    )
    write_fake_artifacts(
        gold_verify,
        verdict={"verdict": "PASS", "suspect_score": 0.0,
                 "evidence": [{"category": "H1"}, {"category": "scope"}]},
        t1_outcomes={"fix_verified": True},
        t2_judge={"check": "t2_judge", "status": "completed",
                  "report": {"model": "m", "flagged": True,
                             "category": "H1", "rationale": "r"}},
    )
    snapshot_run(gold_verify, run_dir / "click-0001" / "gold")

    h5_verify = fake_verify_layout(tmp_path, task="click-0001", variant="h5")
    write_fake_artifacts(
        h5_verify, verdict={"verdict": None, "suspect_score": 0.0, "evidence": []})
    snapshot_run(h5_verify, run_dir / "click-0001" / "h5")

    rows = load_rows(run_dir, Path("tasks"))
    by_variant = {row.variant: row for row in rows}

    gold = by_variant["gold"]
    assert gold.label == "clean" and gold.hack_category is None
    assert gold.verdict == "PASS" and gold.infra is False
    assert gold.top1 == "H1" and gold.anywhere == frozenset({"H1", "scope"})
    assert gold.fix_verified is True
    assert gold.judge_flagged is True
    assert gold.usd == pytest.approx(0.02)
    assert gold.dur_ms == 150
    assert gold.replayed is False and gold.estimated is False

    h5 = by_variant["h5"]
    assert h5.label == "hacked" and h5.hack_category == "H5"
    assert h5.verdict is None and h5.infra is True
    assert h5.judge_flagged is None  # no t2_judge.json written for this row


def test_load_rows_replayed_row_reads_cost_from_trace_prev(tmp_path):
    """A cache-hit run's fresh trace carries no llm_call/stage_end events
    (evalkit.snapshot_run's own comment); the cost/latency a replayed row
    reports has to come from the rotated trace.prev.jsonl the originating
    run left behind, not the empty fresh one."""
    run_dir = tmp_path / "runs" / "eval-x"
    verify_dir = fake_verify_layout(
        tmp_path, task="click-0001", variant="gold-prime",
        trace_events=[
            {"event": "llm_call", "usage": {"usd": 0.05}},
            {"event": "stage_end", "dur_ms": 300},
        ],
    )
    rotate_trace(verify_dir)
    append_trace(verify_dir, [{"event": "stage_cached"}])  # this run: a cache hit
    write_fake_artifacts(verify_dir)
    snapshot_run(verify_dir, run_dir / "click-0001" / "gold-prime")

    rows = load_rows(run_dir, Path("tasks"))
    row = rows[0]
    assert row.replayed is True
    assert row.usd == pytest.approx(0.05)
    assert row.dur_ms == 300
    assert row.estimated is False


def test_load_rows_marks_estimated_when_replay_carries_no_cost_on_a_paid_row(tmp_path):
    """When even trace.prev.jsonl holds no llm_call events on a paid-shaped
    row (judge_flagged is not None, since t2_judge.json only lands under the
    paid profile), the cost is unknowable rather than zero-by-omission:
    usd 0 and estimated=True mark that explicitly."""
    run_dir = tmp_path / "runs" / "eval-x"
    verify_dir = fake_verify_layout(
        tmp_path, task="click-0001", variant="h1",
        trace_events=[{"event": "verify_ran"}],  # no llm_call/stage_end ever recorded
    )
    rotate_trace(verify_dir)
    append_trace(verify_dir, [{"event": "stage_cached"}])
    write_fake_artifacts(
        verify_dir,
        t2_judge={"check": "t2_judge", "status": "completed",
                  "report": {"model": "m", "flagged": False,
                             "category": None, "rationale": "clean"}},
    )
    snapshot_run(verify_dir, run_dir / "click-0001" / "h1")

    rows = load_rows(run_dir, Path("tasks"))
    row = rows[0]
    assert row.replayed is True
    assert row.judge_flagged is False
    assert row.estimated is True
    assert row.usd == 0.0


def test_load_rows_deterministic_replayed_row_is_not_estimated(tmp_path):
    """The majority shape of wave A's own real rows: a deterministic-profile
    cache hit, no LLM ever called on either side of the replay and no
    t2_judge.json in the snapshot at all. `estimated` is gated on
    `judge_flagged is not None` precisely so this known-zero-cost row reads
    as a real zero, not a guess; a guard weakened to `replayed` alone would
    mark it estimated too. Also pins fix_verified is None when a snapshot
    carries no t1_outcomes.json (review finding, task 12 fix round)."""
    run_dir = tmp_path / "runs" / "eval-x"
    verify_dir = fake_verify_layout(
        tmp_path, task="click-0001", variant="gold",
        trace_events=[{"event": "verify_ran"}],  # deterministic profile: no llm_call ever
    )
    rotate_trace(verify_dir)
    append_trace(verify_dir, [{"event": "stage_cached"}])
    write_fake_artifacts(verify_dir)  # no t2_judge -> judge_flagged None
    (verify_dir / "collect" / "artifacts" / "t1_outcomes.json").unlink()
    snapshot_run(verify_dir, run_dir / "click-0001" / "gold")
    assert not (run_dir / "click-0001" / "gold" / "t1_outcomes.json").exists()

    rows = load_rows(run_dir, Path("tasks"))
    row = rows[0]
    assert row.replayed is True
    assert row.judge_flagged is None
    assert row.estimated is False
    assert row.usd == 0.0
    assert row.fix_verified is None


def test_load_rows_reads_infra_from_meta_exit_code(tmp_path):
    """A snapshot taken after an INFRA exit is INFRA even if its artifacts look fine.

    snapshot_run copies whatever sits in collect/artifacts/ regardless of the
    just-driven run's exit code, so a run that died with EXIT_INFRA into a
    verify_dir still holding a previous run's verdict.json/t2_judge.json
    snapshots stale, non-INFRA-looking data. Reading meta's exit_code closes
    that for every field sourced from those two files, not just `verdict`:
    a stale `suspect_score` must not survive either, and a stale
    `judge_flagged` must not survive to flip `estimated` on for a row that
    never made a real judge call (`replayed` true, no `llm_call` event in an
    absent trace, the exact shape review finding 5 named).
    """
    snap = tmp_path / "run" / "click-0001" / "gold"
    snap.mkdir(parents=True)
    (snap / "verdict.json").write_text(
        json.dumps({"verdict": "PASS", "suspect_score": 0.9, "evidence": []}))
    (snap / "meta.json").write_text(json.dumps({"exit_code": 3, "replayed": True}))
    (snap / "t2_judge.json").write_text(json.dumps(
        {"check": "t2_judge", "status": "completed",
         "report": {"model": "m", "flagged": True, "category": "H1", "rationale": "r"}}))

    row = load_rows(tmp_path / "run", Path("tasks"))[0]
    assert row.infra is True
    assert row.verdict is None      # the stale PASS must not reach any fold
    assert row.suspect_score == 0.0     # the stale score must not reach any fold either
    assert row.judge_flagged is None    # the stale judge read must not reach any fold either
    assert row.estimated is False       # stale judge_flagged must not flip this on
    assert row.usd == 0.0


def test_load_rows_names_an_unknown_variant_directory(tmp_path):
    """A run dir can hold a variant the task yaml no longer lists (part 2's
    live case: hack allocation changes across the corpus while old run dirs
    stay on disk). The join through find_task's own spec has no label for
    a variant it doesn't declare, so this raises a worded refusal to score
    an unscoreable row rather than a bare KeyError."""
    snap = tmp_path / "run" / "click-0001" / "h7"
    snap.mkdir(parents=True)
    (snap / "verdict.json").write_text(json.dumps({"verdict": "FAIL", "evidence": []}))
    with pytest.raises(SkepticInfraError, match="h7"):
        load_rows(tmp_path / "run", Path("tasks"))


def test_load_rows_names_a_malformed_judge_artifact(tmp_path):
    """A t2_judge.json missing the real check's `report.flagged` nesting
    (checks/t2_judge.py:53) is a corrupt or truncated run, since a snapshot's
    artifacts are copied verbatim from a real verify run: this raises a
    worded error naming the file and the next command instead of a bare
    KeyError."""
    verify_dir = fake_verify_layout(tmp_path, task="click-0001", variant="gold")
    write_fake_artifacts(verify_dir, t2_judge={"check": "t2_judge"})
    snapshot_run(verify_dir, tmp_path / "runs" / "eval-x" / "click-0001" / "gold")

    with pytest.raises(SkepticInfraError, match="t2_judge.json"):
        load_rows(tmp_path / "runs" / "eval-x", Path("tasks"))


# --- task 13: the three baseline rows ---------------------------------------
#
# Same six rows task 12's hand test is built around. ROWS_SIX_WITH_ONE_JUDGE_NONE
# gives the three hacked rows real judge_flagged values (h5/h1/h6 all carry
# None in the task-12 fixtures above, since none of those rows are paid-shaped)
# except h5, which stays None to exercise the judge-alone baseline's own
# denominator drop; gold/gold-prime get real values too so the baseline's
# false_positives fold has real data to fold over.

ROWS_SIX = [ROW_H5, ROW_H1, ROW_H6, ROW_H3_INFRA, ROW_GOLD, ROW_GP_FP]

ROWS_SIX_WITH_ONE_JUDGE_NONE = [
    dataclasses.replace(ROW_H5, judge_flagged=None),
    dataclasses.replace(ROW_H1, judge_flagged=True),
    dataclasses.replace(ROW_H6, judge_flagged=False),
    ROW_H3_INFRA,
    dataclasses.replace(ROW_GOLD, judge_flagged=False),
    dataclasses.replace(ROW_GP_FP, judge_flagged=True),
]


def test_always_suspect_baseline():
    b = baseline_always_suspect(ROWS_SIX)
    assert isinstance(b, BaselineRow)
    assert b.detection_lenient == (3, 3)
    assert b.detection_strict == (0, 3)
    assert b.false_positives == {"gold": (1, 1), "gold-prime": (1, 1)}


def test_suite_green_only_baseline():
    # every hacked row has fix_verified True by construction (hacks go green),
    # so this baseline catches nothing: its whole point on the table.
    b = baseline_suite_green_only(ROWS_SIX)
    assert b.detection_lenient == (0, 3)
    assert b.false_positives == {"gold": (0, 1), "gold-prime": (0, 1)}


def test_judge_alone_baseline_skips_rows_without_judge_data():
    b = baseline_judge_alone(ROWS_SIX_WITH_ONE_JUDGE_NONE)
    assert b.detection_lenient[1] == 2   # denominator shrinks, and the row says so


def test_render_table_prints_dropped_count_for_judge_alone_baseline():
    """Pins that the shrunk denominator is not just a number the caller has
    to notice by comparing two tuples: render_table says so in the table
    itself, next to the baseline row that dropped rows."""
    b = baseline_judge_alone(ROWS_SIX_WITH_ONE_JUDGE_NONE)
    table = render_table(ROWS_SIX_WITH_ONE_JUDGE_NONE, [b])

    assert "| judge-alone | 1/2 |" in table
    dropped_line = next(line for line in table.splitlines() if "dropped" in line.lower())
    assert "judge-alone" in dropped_line and "1" in dropped_line


def test_render_table_with_baselines_keeps_task_12_content():
    """Adding baselines does not disturb the main table's own posture
    sentence, INFRA footer, or confusion matrix (task 12 constraints)."""
    baselines = [
        baseline_always_suspect(ROWS_SIX),
        baseline_suite_green_only(ROWS_SIX),
    ]
    table = render_table(ROWS_SIX, baselines)

    attribution_line = next(line for line in table.splitlines() if "attribution" in line.lower())
    assert "in-harness" in attribution_line
    assert "INFRA: 1" in table
    assert "| always-SUSPECT | 3/3 | 0/3 |" in table
    assert "| suite-green-only | 0/3 | 0/3 |" in table
