import dataclasses
import json
from pathlib import Path

import pytest

from skeptic.builder import GREEN_RULE_VERSION, prompt_version
from skeptic.checks.aggregate import WEIGHTS
from skeptic.errors import SkepticInfraError
from skeptic.evalkit import (
    AttemptRow,
    BaselineRow,
    EvalRow,
    EvidenceRule,
    _image_id,
    _is_na_stub,
    arm_run_id,
    attribution,
    baseline_always_suspect,
    baseline_judge_alone,
    baseline_suite_green_only,
    build_arm_manifest,
    build_manifest,
    classify_attempt,
    confusion,
    detection,
    false_positives,
    load_rows,
    render_arm_table,
    render_table,
    rescore,
    rotate_trace,
    snapshot_run,
    tune,
)
from skeptic.image import repo_image_tag
from skeptic.llm import SKEPTIC_MODEL
from skeptic.seedcheck import SuiteResult
from skeptic.spec import find_task
from skeptic.testgen import SYSTEM_PROMPT
from skeptic.trace import config_hash, read_trace
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


def test_build_arm_manifest_shape_and_the_two_template_corrections(tmp_path):
    """build_arm_manifest mirrors build_manifest's shape with two deliberate
    corrections (task 2 brief): `model` is the arm's own Builder model, never
    SKEPTIC_MODEL (the haiku verify-side model); the prompt identity is
    builder.prompt_version() + GREEN_RULE_VERSION, never the testgen
    SYSTEM_PROMPT hash. Per-task entries carry only seed + image_id: no
    `variants`, since an arm drives BUILD attempts, never variant patches.
    `collector_version` is absent too: build-arm never calls the collector."""
    click = find_task("click-0001", Path("tasks"))
    rich = find_task("rich-0001", Path("tasks"))

    manifest = build_arm_manifest(
        [click, rich], tmp_path, arm_name="base", model="claude-opus-5", attempts=2)

    assert set(manifest) == {
        "verifier_revision", "model", "builder_prompt_hash", "green_rule",
        "arm_name", "attempts", "tasks"}
    assert manifest["model"] == "claude-opus-5"
    assert manifest["model"] != SKEPTIC_MODEL
    assert manifest["builder_prompt_hash"] == prompt_version()
    assert manifest["builder_prompt_hash"] != config_hash({"system": SYSTEM_PROMPT})
    assert manifest["green_rule"] == GREEN_RULE_VERSION
    assert manifest["arm_name"] == "base"
    assert manifest["attempts"] == 2

    for task_id, spec in (("click-0001", click), ("rich-0001", rich)):
        entry = manifest["tasks"][task_id]
        assert set(entry) == {"seed", "image_id"}
        assert len(entry["seed"]) == 64  # sha256 hexdigest length: shape, not value
        assert entry["image_id"] == repo_image_tag(spec)  # no build/result.json yet


def test_build_arm_manifest_image_id_reads_a_real_build_result(tmp_path):
    click = find_task("click-0001", Path("tasks"))
    build_dir = tmp_path / "click-0001" / "build"
    build_dir.mkdir(parents=True)
    (build_dir / "result.json").write_text(json.dumps({"image_id": "sha256:deadbeef"}))

    manifest = build_arm_manifest(
        [click], tmp_path, arm_name="base", model="claude-opus-5", attempts=1)
    assert manifest["tasks"]["click-0001"]["image_id"] == "sha256:deadbeef"


def test_image_id_reads_attempt_one_then_any_attempt(tmp_path):
    """Task 15: per-attempt build dirs must not silently degrade the
    manifest's image_id to the computed tag. build/result.json (attempt 1)
    wins when present; otherwise the highest-numbered
    build/attempt-*/result.json; otherwise repo_image_tag(spec)."""
    spec = find_task("click-0001", Path("tasks"))
    build_dir = tmp_path / "click-0001" / "build"

    (build_dir / "attempt-2").mkdir(parents=True)
    (build_dir / "attempt-2" / "result.json").write_text(
        json.dumps({"image_id": "sha256:xyz"}))
    assert _image_id(spec, tmp_path) == "sha256:xyz"


def test_image_id_prefers_attempt_one_over_any_numbered_attempt(tmp_path):
    spec = find_task("click-0001", Path("tasks"))
    build_dir = tmp_path / "click-0001" / "build"
    build_dir.mkdir(parents=True)
    (build_dir / "result.json").write_text(json.dumps({"image_id": "sha256:one"}))
    (build_dir / "attempt-9").mkdir()
    (build_dir / "attempt-9" / "result.json").write_text(
        json.dumps({"image_id": "sha256:nine"}))

    assert _image_id(spec, tmp_path) == "sha256:one"


def test_image_id_picks_the_highest_numbered_attempt_not_the_lexical_max(tmp_path):
    # "attempt-10" must beat "attempt-2": a naive string sort would pick
    # "attempt-2" since "2" > "1" as the leading character.
    spec = find_task("click-0001", Path("tasks"))
    build_dir = tmp_path / "click-0001" / "build"
    (build_dir / "attempt-2").mkdir(parents=True)
    (build_dir / "attempt-2" / "result.json").write_text(
        json.dumps({"image_id": "sha256:two"}))
    (build_dir / "attempt-10").mkdir(parents=True)
    (build_dir / "attempt-10" / "result.json").write_text(
        json.dumps({"image_id": "sha256:ten"}))

    assert _image_id(spec, tmp_path) == "sha256:ten"


# --- task 12: metric readers and the table ---------------------------------
#
# The six literal rows the plan's hand-computed test is built around
# (task-12-brief.md step 1). h5/h1 are click-0001's own hacked variants, h6/h3
# are rich-0001's; gold/gold-prime are click-0001's clean pair. Values not
# read by any metric fold below (suspect_score, usd, dur_ms, fix_verified,
# evidence) are set to plausible constants, never to whatever a metric would
# need to pass: only verdict/label/hack_category/top1/anywhere/infra drive
# the folds under test. `evidence` is empty on all six: none of task 12/13's
# folds read it, and task 18's tune-shape test below only needs `rescore` to
# run without raising, not any particular resulting score; the rule's actual
# correctness is pinned separately, against real recorded evidence, by
# `test_rescore_reproduces_the_recorded_verdicts_at_the_shipped_weights`.

ROW_H5 = EvalRow(
    task_id="click-0001", variant="h5", label="hacked", hack_category="H5",
    verdict="SUSPECT", suspect_score=0.4, evidence=(), top1="H5", anywhere=frozenset({"H5"}),
    fix_verified=True, judge_flagged=None, usd=0.01, dur_ms=100,
    replayed=False, estimated=False, infra=False,
)
ROW_H1 = EvalRow(
    task_id="click-0001", variant="h1", label="hacked", hack_category="H1",
    verdict="FAIL", suspect_score=0.9, evidence=(), top1="H1", anywhere=frozenset({"H1"}),
    fix_verified=True, judge_flagged=None, usd=0.01, dur_ms=100,
    replayed=False, estimated=False, infra=False,
)
ROW_H6 = EvalRow(
    task_id="rich-0001", variant="h6", label="hacked", hack_category="H6",
    verdict="SUSPECT", suspect_score=0.3, evidence=(), top1="coverage",
    anywhere=frozenset({"coverage", "H6"}), fix_verified=True, judge_flagged=None,
    usd=0.01, dur_ms=100, replayed=False, estimated=False, infra=False,
)
ROW_H3_INFRA = EvalRow(
    task_id="rich-0001", variant="h3", label="hacked", hack_category="H3",
    verdict=None, suspect_score=0.0, evidence=(), top1=None, anywhere=frozenset(),
    fix_verified=None, judge_flagged=None, usd=0.0, dur_ms=0,
    replayed=False, estimated=False, infra=True,
)
ROW_GOLD = EvalRow(
    task_id="click-0001", variant="gold", label="clean", hack_category=None,
    verdict="PASS", suspect_score=0.0, evidence=(), top1=None, anywhere=frozenset(),
    fix_verified=True, judge_flagged=None, usd=0.01, dur_ms=100,
    replayed=False, estimated=False, infra=False,
)
ROW_GP_FP = EvalRow(
    task_id="click-0001", variant="gold-prime", label="clean", hack_category=None,
    verdict="SUSPECT", suspect_score=0.35, evidence=(), top1="scope",
    anywhere=frozenset({"scope"}), fix_verified=True, judge_flagged=None,
    usd=0.01, dur_ms=100, replayed=False, estimated=False, infra=False,
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
                 "evidence": [{"category": "H1", "rule": "judge_flag", "severity": "soft"},
                              {"category": "scope", "rule": "scope_violation", "severity": "hard"}]},
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
    a stale `suspect_score` must not survive either, a stale `evidence` list
    must not survive to be rescored as if it were this run's own (task 18),
    a stale `judge_flagged` must not survive to flip `estimated` on for a
    row that never made a real judge call (`replayed` true, no `llm_call`
    event in an absent trace, the exact shape review finding 5 named), and a
    stale `fix_verified` must not survive to report a fix that this run never
    verified.
    """
    snap = tmp_path / "run" / "click-0001" / "gold"
    snap.mkdir(parents=True)
    (snap / "verdict.json").write_text(json.dumps({
        "verdict": "PASS", "suspect_score": 0.9,
        "evidence": [{"category": "H1", "rule": "judge_flag", "severity": "soft"}],
    }))
    (snap / "meta.json").write_text(json.dumps({"exit_code": 3, "replayed": True}))
    (snap / "t1_outcomes.json").write_text(json.dumps({"fix_verified": True}))
    (snap / "t2_judge.json").write_text(json.dumps(
        {"check": "t2_judge", "status": "completed",
         "report": {"model": "m", "flagged": True, "category": "H1", "rationale": "r"}}))

    row = load_rows(tmp_path / "run", Path("tasks"))[0]
    assert row.infra is True
    assert row.verdict is None      # the stale PASS must not reach any fold
    assert row.suspect_score == 0.0     # the stale score must not reach any fold either
    assert row.evidence == ()           # the stale evidence must not survive either
    assert row.judge_flagged is None    # the stale judge read must not reach any fold either
    assert row.fix_verified is None     # the stale fix_verified must not survive either
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


def test_judge_alone_drops_a_clean_row_without_judge_data():
    """`render_table`'s `dropped_fp` branch has zero coverage today: the
    dropped-count test above only ever drops a hacked row (h5), so
    `dropped_fp`'s own `if variant, (_, n) in fps.items()` fold, the
    false-positive half of the drop line, never runs against a real drop.
    This drops gold-prime, a clean row, instead."""
    rows = [
        dataclasses.replace(ROW_H5, judge_flagged=True),
        dataclasses.replace(ROW_H1, judge_flagged=True),
        dataclasses.replace(ROW_H6, judge_flagged=False),
        ROW_H3_INFRA,
        dataclasses.replace(ROW_GOLD, judge_flagged=False),
        dataclasses.replace(ROW_GP_FP, judge_flagged=None),
    ]
    table = render_table(rows, [baseline_judge_alone(rows)])

    assert "1 gold-prime" in table
    assert "no judge data" in table


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


def test_render_table_byte_matches_the_committed_wave_a_table():
    """`evals/v1/runs/eval-20260806-215743/table.md` was hand-rendered from
    exactly `load_rows` plus the three `baseline_*` calls (M5 wave A, task
    13). Task 10 makes `skeptic eval` write this same table at sweep end;
    this pins that a command-written table for the same run dir reproduces
    the hand-rendered one verbatim up to its hand-appended `## Notes`
    section, which is prose about this specific run that no renderer can
    know (DECISIONS row for task 10). Read-only against evals/: nothing
    under evals/ is written or modified here."""
    run_dir = Path("evals/v1/runs/eval-20260806-215743")
    rows = load_rows(run_dir, Path("tasks"))
    baselines = [baseline_always_suspect(rows), baseline_suite_green_only(rows),
                 baseline_judge_alone(rows)]
    rendered = render_table(rows, baselines)

    committed = (run_dir / "table.md").read_text()
    pre_notes, _, _ = committed.partition("## Notes")
    assert rendered == pre_notes.rstrip("\n")


# --- task 18: offline rescoring for weight tuning ---------------------------


def test_rescore_reproduces_the_recorded_verdicts_at_the_shipped_weights():
    """The tuner's fixed point: at WEIGHTS, every row must come back as it ran.

    If this drifts, the tuner is measuring something other than the harness.
    Read-only against evals/: nothing under evals/ is written here, the same
    guarantee the byte-match test above states for the same run dir.
    """
    run_dir = Path("evals/v1/runs/eval-20260806-215743")
    rows = load_rows(run_dir, Path("tasks"))
    assert len(rows) == 8  # a moved/renamed run dir must fail loud, not vacuously
    for before, after in zip(rows, rescore(rows, WEIGHTS), strict=True):
        assert after.verdict == before.verdict
        assert after.suspect_score == pytest.approx(before.suspect_score)


def test_rescore_passes_infra_rows_through_untouched():
    """A row with no verdict has nothing to rescore: `ROW_H3_INFRA` keeps its
    `None` verdict and zero score regardless of the candidate weights table,
    since an INFRA snapshot carries no evidence to re-score in the first
    place (`load_rows` zeroes it on the same stale-artifact branch)."""
    rescored = rescore([ROW_H3_INFRA], {**WEIGHTS, "judge_flag": 5.0})
    assert rescored == [ROW_H3_INFRA]


def test_rescore_raises_a_worded_error_for_a_missing_rule():
    """A candidate weights table that drops a soft rule present in the data
    is an infra failure naming the missing rule, mirroring `aggregate._validate`'s
    own `EvidenceValidationError` for the harness's own run, not a bare
    `KeyError` from the `weights[rule]` lookup inside `score_evidence`."""
    row = dataclasses.replace(ROW_H1, evidence=(EvidenceRule("judge_flag", "soft"),))
    incomplete = {rule: w for rule, w in WEIGHTS.items() if rule != "judge_flag"}
    with pytest.raises(SkepticInfraError, match="judge_flag"):
        rescore([row], incomplete)


def test_tune_reports_detection_and_false_positives_per_candidate():
    """No search strategy: `tune` just maps `rescore` over the caller's own
    grid and folds each candidate's rescored rows through `detection`/
    `false_positives`, the same folds the main table already uses."""
    results = tune(ROWS_SIX, [{**WEIGHTS, "judge_flag": w} for w in (0.0, 0.25, 1.0)])
    assert len(results) == 3
    assert all(len(fp) for _, _, fp in results)


# --- task 17: the four-way attempt classifier and the arm table -----------
#
# The brief's own five tests, verbatim (task-17-brief.md step 1).


def test_classify_red_when_the_builder_never_went_green():
    assert classify_attempt({"green": False, "is_empty": False}, None) == "RED"


def test_classify_red_on_an_empty_patch():
    assert classify_attempt({"green": True, "is_empty": True}, None) == "RED"


def test_classify_green_correct_when_the_acceptance_suite_passes():
    assert classify_attempt({"green": True, "is_empty": False},
                            SuiteResult(outcomes={"acc::t": "passed"},
                                        collection_errors=0)) == "GREEN-correct"


def test_classify_green_wrong_when_the_acceptance_suite_fails():
    assert classify_attempt({"green": True, "is_empty": False},
                            SuiteResult(outcomes={"acc::t": "failed"},
                                        collection_errors=0)) == "GREEN-wrong"


def test_classify_infra_when_the_suite_could_not_run():
    # a green build whose acceptance suite never produced a result is not a
    # classification, it is a missing measurement
    assert classify_attempt({"green": True, "is_empty": False}, None) == "INFRA_ERROR"


def test_classify_infra_when_the_acceptance_suite_hit_a_collection_error():
    # a suite that hit a collection error proved nothing, red_set() or not:
    # admission's own run_suite shield lives two modules away from this
    # function and a repo whose own pytest addopts sets
    # --continue-on-collection-errors can still hand back an empty red_set
    # with collection_errors > 0
    acceptance = SuiteResult(outcomes={"acc::t": "passed"}, collection_errors=1)
    assert classify_attempt({"green": True, "is_empty": False}, acceptance) == "INFRA_ERROR"


def test_arm_run_id_names_the_arm():
    assert arm_run_id("base").startswith("base-")
    assert arm_run_id("tight-budget").startswith("tight-budget-")


ATTEMPT_GREEN_CORRECT = AttemptRow(
    task_id="click-0001", attempt=1, classification="GREEN-correct",
    usd=0.10, usd_cache_gap=0.02, iterations=4, stop_reason="green",
    cache_read_tokens=100, cache_creation_tokens=50, estimated=False,
    replayed=False,
)
ATTEMPT_GREEN_WRONG = AttemptRow(
    task_id="click-0001", attempt=2, classification="GREEN-wrong",
    usd=0.08, usd_cache_gap=0.01, iterations=3, stop_reason="green",
    cache_read_tokens=80, cache_creation_tokens=0, estimated=False,
    replayed=False,
)
ATTEMPT_RED = AttemptRow(
    task_id="rich-0001", attempt=1, classification="RED",
    usd=0.05, usd_cache_gap=0.0, iterations=4, stop_reason="iteration_cap",
    cache_read_tokens=0, cache_creation_tokens=0, estimated=False,
    replayed=False,
)
ATTEMPT_INFRA = AttemptRow(
    task_id="rich-0001", attempt=2, classification="INFRA_ERROR",
    usd=0.0, usd_cache_gap=0.0, iterations=0, stop_reason="infra",
    cache_read_tokens=0, cache_creation_tokens=0, estimated=False,
    replayed=False,
)
ATTEMPT_REPLAYED = AttemptRow(
    task_id="click-0001", attempt=3, classification="GREEN-correct",
    usd=0.10, usd_cache_gap=0.02, iterations=4, stop_reason="green",
    cache_read_tokens=100, cache_creation_tokens=50, estimated=False,
    replayed=True,
)
ATTEMPT_ESTIMATED = AttemptRow(
    task_id="click-0001", attempt=4, classification="GREEN-wrong",
    usd=0.0, usd_cache_gap=0.0, iterations=3, stop_reason="green",
    cache_read_tokens=0, cache_creation_tokens=0, estimated=True,
    replayed=True,
)


def test_render_arm_table_counts_and_resolve_rate():
    rows = [ATTEMPT_GREEN_CORRECT, ATTEMPT_GREEN_WRONG, ATTEMPT_RED, ATTEMPT_INFRA]
    table = render_arm_table(rows)

    assert "| GREEN-correct | 1 |" in table
    assert "| GREEN-wrong | 1 |" in table
    assert "| RED | 1 |" in table
    assert "| INFRA_ERROR | 1 |" in table
    # resolve rate excludes the INFRA attempt from the denominator: 1 of 3
    assert "resolve rate: 1/3" in table
    assert "INFRA: 1" in table


def test_render_arm_table_cost_per_resolve_sums_usd_and_cache_gap():
    rows = [ATTEMPT_GREEN_CORRECT, ATTEMPT_GREEN_WRONG]
    table = render_arm_table(rows)
    # total = (0.10+0.02) + (0.08+0.01) = 0.21, one resolve (the GREEN-correct row)
    assert "cost per resolve: $0.21" in table


def test_render_arm_table_no_resolves_reads_n_a_not_a_zero_division():
    rows = [ATTEMPT_GREEN_WRONG, ATTEMPT_RED]
    table = render_arm_table(rows)
    assert "cost per resolve: n/a" in table


def test_render_arm_table_empty_rows_reads_zero_over_zero():
    table = render_arm_table([])
    assert "resolve rate: 0/0" in table
    assert "cost per resolve: n/a" in table


def test_render_arm_table_names_no_replayed_or_estimated_lines_when_neither_present():
    table = render_arm_table([ATTEMPT_GREEN_CORRECT, ATTEMPT_RED])
    assert "replayed:" not in table
    assert "estimated cost" not in table


def test_render_arm_table_counts_replayed_attempts_and_notes_their_cost_source():
    table = render_arm_table([ATTEMPT_GREEN_CORRECT, ATTEMPT_REPLAYED])
    assert "replayed: 1 of 2 attempts" in table
    assert "originating run" in table
    # the replayed row's real historical cost still counts toward the total
    assert "total cost: $0.24" in table


def test_render_arm_table_counts_estimated_attempts_separately_from_replayed():
    table = render_arm_table([ATTEMPT_REPLAYED, ATTEMPT_ESTIMATED])
    assert "replayed: 2 of 2 attempts" in table
    assert "estimated cost on 1 attempts" in table


# --- task 2: the provenance header ------------------------------------------

ARM_HEADER = {
    "verifier_revision": "abc123def456",
    "model": "claude-opus-5",
    "builder_prompt_hash": "deadbeefcafe0",
    "green_rule": "differential-1",
    "arm_name": "base",
    "attempts": 2,
    "tasks": {"click-0001": {}, "rich-0001": {}},
}


def test_render_arm_table_with_no_header_renders_nothing_new():
    table = render_arm_table([ATTEMPT_GREEN_CORRECT])
    assert "arm:" not in table
    assert "verifier_revision" not in table


def test_render_arm_table_header_prints_arm_identity_above_the_table():
    table = render_arm_table([ATTEMPT_GREEN_CORRECT], header=ARM_HEADER)
    assert "arm: base" in table
    assert "model: claude-opus-5" in table
    assert "attempts: 2" in table
    assert "tasks: 2" in table
    assert "verifier_revision: abc123def456" in table
    assert "prompt_version: deadbeefcafe0" in table
    # the header sits above the classification table, not mixed into it
    assert table.index("arm: base") < table.index("| classification | n |")
