import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from skeptic import cli
from skeptic.candidate import CandidateReport
from skeptic.checks.aggregate import LayerOutcome
from skeptic.checks.evidence import MANDATORY_CHECKS, CheckResult
from skeptic.cli import _verify_cache_key, app
from skeptic.errors import SkepticInfraError
from skeptic.orchestrator import StageCache
from skeptic.spec import find_task
from skeptic.trace import read_trace
from tests.helpers import make_observed_pair, make_pure_pair, make_task_spec

runner = CliRunner()


def _fake_pair(spec):
    """A fully-observed, tree-free pair (`make_observed_pair`) whose seeded
    ids all passed on the candidate side: `compute_fix_verified` reads this
    for real even though the check layer is faked around it."""
    seeded = tuple(spec.seed.failing_tests)
    return make_observed_pair(
        baseline={"collected": seeded, "collect_exit": 0,
                 "outcomes": dict.fromkeys(seeded, "failed"),
                 "collection_errors": 0, "suite_exit": 1},
        candidate={"collected": seeded, "collect_exit": 0,
                  "outcomes": dict.fromkeys(seeded, "passed"),
                  "collection_errors": 0, "suite_exit": 0},
        spec=spec,
    )


def _pass_layer_outcome() -> LayerOutcome:
    """Every mandatory check reporting completed, no evidence: a genuine PASS
    once the real `aggregate()` folds it."""
    results = tuple(
        CheckResult(check=name, status="completed", evidence=(), artifact=None, dur_ms=1)
        for name in MANDATORY_CHECKS
    )
    return LayerOutcome(results=results, infra={})


def _fake_heavy_stages(monkeypatch, pair, calls):
    """Fake every git/docker-touching step `do_verify` calls before
    `collect_pair`, and fake `collect_pair` itself plus the check layer, so
    the command runs end to end against `pair` with no real subprocess.
    `aggregate()` (and `compute_fix_verified`) run for real.

    `generate_mutants` is faked to a bare `()`: `pair` here is a tree-free
    `make_observed_pair` (candidate.tree and candidate_diff.diff_path both
    name paths nothing ever wrote), and Task 9's enrichment reads both before
    `run_verify_layer` is reached. An empty mutant list makes every later
    enrichment step a no-op (`sample_mutants` on nothing, an empty
    `selections` map, and `observe_mutation` never touches `pair.candidate.
    tree` when its own `mutants` argument is empty), so the CLI plumbing this
    test exercises stays exercised without needing a real tree or container
    for the mutation batch specifically. `observe_probe` (Task 10) is faked to
    a bare `None` for the same reason: its own enrichment block runs before
    `run_verify_layer` too, and the real function would otherwise try to
    mount `pair.candidate.tree`, a path nothing ever created, into a
    container. `run_verify_layer` is faked here regardless, so the returned
    `None` is never read by a real `t2_probe.run`.
    """
    from skeptic import candidate, checks, collector, mutation, workspace

    monkeypatch.setattr(workspace, "clone_pinned", lambda url, commit, cache: cache)
    monkeypatch.setattr(workspace, "materialize", lambda repo, commit, dest: dest.mkdir(parents=True))
    monkeypatch.setattr(workspace, "apply_patch", lambda ws, patch: None)
    monkeypatch.setattr(candidate, "snapshot", lambda src, dest: None)
    monkeypatch.setattr(
        candidate, "extract_candidate",
        lambda baseline, workspace, out_diff, allowed_paths: CandidateReport(
            diff_path=out_diff, changed_files=["src/click/termui.py"],
            out_of_scope=[], is_empty=False))

    def fake_collect_pair(spec, repo_dir, report, workdir, baseline_cache=None):
        calls.append(1)
        return pair

    monkeypatch.setattr(collector, "collect_pair", fake_collect_pair)
    monkeypatch.setattr(mutation, "generate_mutants", lambda pair: ())
    monkeypatch.setattr(collector, "observe_probe", lambda *a, **k: None)
    monkeypatch.setattr(checks, "run_verify_layer", lambda p: _pass_layer_outcome())


# --- enrichment isolation (review round 1, fix 1): a dead enrichment must
# not take sibling T1 evidence down with it, and must not fake a PASS. Unlike
# `_fake_heavy_stages` above, `run_verify_layer` runs for real here: the
# point is to prove real sibling evidence (or its real absence) survives a
# faulted `generate_mutants`, which needs the real registry, not a canned
# `LayerOutcome`. `pair` is a real, tree-backed, container-free pair
# (`tests.helpers.make_pure_pair`), so every T1 check that reads a real file
# off the tree or the diff (`t1_config`, `t1_patterns`, `t1_ast`, ...) has one
# to read.

_H1_SEEDED = (
    "tests/test_golden.py::test_golden_render_matches_expected",
    "tests/test_minirepo.py::test_parse_range_basic",
    "tests/test_minirepo.py::test_parse_range_wide",
)
_H1_SURVIVOR = "tests/test_minirepo.py::test_clamp_bounds"


def _h1_shaped_pair():
    """h1-excision, real tree and diff, hand-supplied observations shaped
    the way the real collector's would read (`tests/test_hack_fixtures.py`'s
    own `VERIFY_ROWS` entry for this fixture): the candidate's collected set
    drops the three seeded ids entirely. `t1_collect`'s `collect_shrinkage`
    and `t1_scope`'s `scope_violation` (the deleted test file sits outside
    `allowed_paths`) both fire for real, giving these tests genuine sibling
    hard evidence to stand on."""
    baseline_observed = {
        "collected": _H1_SEEDED + (_H1_SURVIVOR,), "collect_exit": 0,
        "outcomes": {**dict.fromkeys(_H1_SEEDED, "failed"), _H1_SURVIVOR: "passed"},
        "collection_errors": 0, "suite_exit": 1,
    }
    candidate_observed = {
        "collected": (_H1_SURVIVOR,), "collect_exit": 0,
        "outcomes": {_H1_SURVIVOR: "passed"},
        "collection_errors": 0, "suite_exit": 0,
    }
    return make_pure_pair(
        "h1-excision", observed=baseline_observed, candidate_observed=candidate_observed)


def _would_be_pass_pair():
    """h2-weakening with `allowed_paths` widened to include `tests/`, so its
    only in-harness row (`t1_scope`'s `scope_violation`, per the corpus
    table) never fires. Every mandatory T1 check completes clean or is
    not_applicable: the "would-be-PASS" baseline the INFRA_ERROR case needs,
    the same widening trick `t1_ast`'s own suppression rule tolerates (a
    non-empty `allowed_paths`, merely a wider one, still suppresses its H2
    row)."""
    greened = {
        "collected": _H1_SEEDED + (_H1_SURVIVOR,), "collect_exit": 0,
        "outcomes": dict.fromkeys(_H1_SEEDED + (_H1_SURVIVOR,), "passed"),
        "collection_errors": 0, "suite_exit": 0,
    }
    return make_pure_pair(
        "h2-weakening", allowed_paths=["minirepo.py", "tests/"], observed=greened)


def _fake_heavy_stages_dead_enrichment(monkeypatch, pair, enrichment_error: Exception):
    """Like `_fake_heavy_stages`, but `run_verify_layer` is left real and
    `generate_mutants` raises `enrichment_error` instead of degrading to `()`.

    `observe_probe` is faked to a harmless, real `ProbeReport(calls=())`
    rather than left real: `pair` here is a tree-backed `make_pure_pair`
    fixture (a real minirepo tree), but the outer `spec` `do_verify` reads for
    the probe's own image tag and budget is click-0001's (the CLI invocation
    below always asks for `--task click-0001`), and click's image is never
    built in this test. Left real, the probe enrichment would try to run a
    real container against a real tree but a nonexistent click image tag,
    which is either a slow local failure or a network-dependent one,
    completely unrelated to what these two tests pin (mutation-enrichment
    isolation specifically). A non-`None` report keeps `t2_probe` completed
    and silent, which is what isolates the probe from mutation's own faulted
    `generate_mutants` in both tests below.
    """
    from skeptic import candidate, collector, mutation, workspace
    from skeptic.checks.observations import ProbeReport

    monkeypatch.setattr(workspace, "clone_pinned", lambda url, commit, cache: cache)
    monkeypatch.setattr(workspace, "materialize", lambda repo, commit, dest: dest.mkdir(parents=True))
    monkeypatch.setattr(workspace, "apply_patch", lambda ws, patch: None)
    monkeypatch.setattr(candidate, "snapshot", lambda src, dest: None)
    monkeypatch.setattr(
        candidate, "extract_candidate",
        lambda baseline, workspace, out_diff, allowed_paths: CandidateReport(
            diff_path=out_diff, changed_files=["src/click/termui.py"],
            out_of_scope=[], is_empty=False))
    monkeypatch.setattr(
        collector, "collect_pair",
        lambda spec, repo_dir, report, workdir, baseline_cache=None: pair)
    monkeypatch.setattr(collector, "observe_probe", lambda *a, **k: ProbeReport(calls=()))

    def _boom(_pair):
        raise enrichment_error

    monkeypatch.setattr(mutation, "generate_mutants", _boom)


@pytest.mark.parametrize("enrichment_error", [
    SkepticInfraError("daemon down mid-batch"),
    RuntimeError("boom"),
], ids=["skepticinfraerror", "runtimeerror"])
def test_verify_isolates_a_dead_enrichment_when_sibling_evidence_is_hard(
    tmp_path, monkeypatch, enrichment_error
):
    """Fix 1, cases (a) and (b): either exception class must not erase the
    hard evidence a sibling T1 check found for real. `t2_mutation.run`'s own
    INFRA-on-`None` branch is what lands `t2_mutation` in `checks_infra`."""
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    pair = _h1_shaped_pair()
    _fake_heavy_stages_dead_enrichment(monkeypatch, pair, enrichment_error)

    workdir = tmp_path.resolve()
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--workdir", str(workdir)])

    assert result.exit_code == 2, result.output
    assert "VERDICT FAIL" in result.output

    verdict_path = pair.artifacts_dir / "verdict.json"
    assert verdict_path.is_file()
    saved = json.loads(verdict_path.read_text())
    assert saved["verdict"] == "FAIL"
    assert "t2_mutation" in saved["checks_infra"]
    assert any(e["rule"] in ("collect_shrinkage", "scope_violation") for e in saved["evidence"])


def test_verify_dead_enrichment_on_a_would_be_pass_is_infra_error(tmp_path, monkeypatch):
    """Fix 1, case (c): a candidate that would otherwise PASS reports
    INFRA_ERROR, naming `t2_mutation`, once enrichment dies before it can
    set `candidate.mutation`."""
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    pair = _would_be_pass_pair()
    _fake_heavy_stages_dead_enrichment(
        monkeypatch, pair, SkepticInfraError("daemon down mid-batch"))

    workdir = tmp_path.resolve()
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--workdir", str(workdir)])

    assert result.exit_code == 3, result.output
    assert "INFRA ERROR" in result.output

    verdict_path = pair.artifacts_dir / "verdict.json"
    assert verdict_path.is_file()
    saved = json.loads(verdict_path.read_text())
    assert saved["status"] == "INFRA_ERROR"
    assert saved["verdict"] is None
    assert saved["checks_infra"] == ["t2_mutation"]
    assert "t2_mutation" in saved["infra_reason"]


def _fake_heavy_stages_dead_probe(monkeypatch, pair, enrichment_error: Exception):
    """The probe-side mirror of `_fake_heavy_stages_dead_enrichment` above:
    the fault sits in the probe block instead of the mutation one.

    `mutation.generate_mutants` degrades to a harmless, real `()`, the same
    way `_fake_heavy_stages` keeps the mutation lane HEALTHY for the
    passing-banner tests: an empty mutant list makes `sample_mutants` and the
    real `observe_mutation` both no-ops (`observe_mutation` never starts a
    container when its own `mutants` argument leaves nothing runnable), so
    `t2_mutation` completes for real with no evidence, never touching docker.
    `observe_probe` raises `enrichment_error` instead. This is Task 10's own
    isolation block (DECISIONS row 116) exercised in the other direction: the
    two t2 lanes must degrade independently, so a dead probe run must not
    take a healthy mutation lane's evidence down with it either.
    """
    from skeptic import candidate, collector, mutation, workspace

    monkeypatch.setattr(workspace, "clone_pinned", lambda url, commit, cache: cache)
    monkeypatch.setattr(workspace, "materialize", lambda repo, commit, dest: dest.mkdir(parents=True))
    monkeypatch.setattr(workspace, "apply_patch", lambda ws, patch: None)
    monkeypatch.setattr(candidate, "snapshot", lambda src, dest: None)
    monkeypatch.setattr(
        candidate, "extract_candidate",
        lambda baseline, workspace, out_diff, allowed_paths: CandidateReport(
            diff_path=out_diff, changed_files=["src/click/termui.py"],
            out_of_scope=[], is_empty=False))
    monkeypatch.setattr(
        collector, "collect_pair",
        lambda spec, repo_dir, report, workdir, baseline_cache=None: pair)
    monkeypatch.setattr(mutation, "generate_mutants", lambda pair: ())

    def _boom(*args, **kwargs):
        raise enrichment_error

    monkeypatch.setattr(collector, "observe_probe", _boom)


@pytest.mark.parametrize("enrichment_error", [
    SkepticInfraError("daemon down mid-batch"),
    RuntimeError("boom"),
], ids=["skepticinfraerror", "runtimeerror"])
def test_verify_isolates_a_dead_probe_when_sibling_evidence_is_hard(
    tmp_path, monkeypatch, enrichment_error
):
    """Task 10's own isolation block, the probe-side mirror of Fix 1: either
    exception class raised out of the probe block must not erase hard
    evidence a sibling T1 check found for real, nor take the mutation lane's
    own (HEALTHY, empty) evidence down with it. `t2_probe.run`'s own
    INFRA-on-`None` branch is what lands `t2_probe` in `checks_infra`."""
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    pair = _h1_shaped_pair()
    _fake_heavy_stages_dead_probe(monkeypatch, pair, enrichment_error)

    workdir = tmp_path.resolve()
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--workdir", str(workdir)])

    assert result.exit_code == 2, result.output
    assert "VERDICT FAIL" in result.output

    verdict_path = pair.artifacts_dir / "verdict.json"
    assert verdict_path.is_file()
    saved = json.loads(verdict_path.read_text())
    assert saved["verdict"] == "FAIL"
    # Exact-list, not containment: a regression that collapsed the mutation
    # and probe isolation blocks into one (computing both reports, then
    # applying both in a single model_copy at the end) would let a probe
    # raise discard the already-computed HEALTHY mutation result too,
    # landing t2_mutation in checks_infra alongside t2_probe. hard_present
    # drives FAIL regardless of checks_infra's contents, so exit code,
    # VERDICT FAIL, and the T1-evidence assertion below all stay green
    # through exactly that collapse; only this exact-list form catches it.
    assert saved["checks_infra"] == ["t2_probe"]
    assert any(e["rule"] in ("collect_shrinkage", "scope_violation") for e in saved["evidence"])


def test_verify_dead_probe_on_a_would_be_pass_is_infra_error(tmp_path, monkeypatch):
    """The probe-side mirror of Fix 1, case (c): a candidate that would
    otherwise PASS reports INFRA_ERROR, naming `t2_probe`, once the probe
    enrichment dies before it can set `candidate.probe`, with the mutation
    lane's own HEALTHY empty batch never surfacing in `checks_infra`."""
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    pair = _would_be_pass_pair()
    _fake_heavy_stages_dead_probe(
        monkeypatch, pair, SkepticInfraError("daemon down mid-batch"))

    workdir = tmp_path.resolve()
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--workdir", str(workdir)])

    assert result.exit_code == 3, result.output
    assert "INFRA ERROR" in result.output

    verdict_path = pair.artifacts_dir / "verdict.json"
    assert verdict_path.is_file()
    saved = json.loads(verdict_path.read_text())
    assert saved["status"] == "INFRA_ERROR"
    assert saved["verdict"] is None
    assert saved["checks_infra"] == ["t2_probe"]
    assert "t2_probe" in saved["infra_reason"]


def test_verify_refuses_an_unknown_profile_before_any_work(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(cli, "_docker_available", lambda: called.append("docker") or True)
    result = runner.invoke(app, ["verify", "--task", "no-such-task",
                                 "--variant", "gold", "--profile", "stochastic",
                                 "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "stochastic" in result.output
    assert called == []          # profile guard comes before find_task and docker


def test_verify_refuses_the_venv_runner_with_the_wiring_message(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(cli, "_docker_available", lambda: called.append("docker") or True)
    result = runner.invoke(app, ["verify", "--task", "no-such-task",
                                 "--variant", "gold", "--runner", "venv",
                                 "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "not wired" in result.output
    assert called == []          # runner guard also comes before find_task and docker


def test_verify_names_known_variants_on_an_unknown_variant_id(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "no-such-variant",
                                 "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "no-such-variant" in result.output
    assert "gold" in result.output


def test_verify_requires_docker_daemon(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_docker_available", lambda: False)
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "Docker" in result.output


@pytest.mark.parametrize("status,verdict_name,expected_exit", [
    ("ok", "PASS", 0),
    ("ok", "SUSPECT", 1),
    ("ok", "FAIL", 2),
    ("INFRA_ERROR", None, 3),
])
def test_verify_exit_codes_follow_the_verdict(
    tmp_path, monkeypatch, status, verdict_name, expected_exit
):
    # A fully-faked verdict, pre-populated straight into the stage cache: no
    # do_verify call happens at all, matching test_cli_build's cache-hit
    # style (test_build_writes_a_baseline_suite_trace_event_on_a_cache_hit).
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    workdir = tmp_path.resolve()
    spec = find_task("click-0001", Path("tasks"))
    variant_spec = spec.evaluation.variants[0]
    cache_key = _verify_cache_key(spec, variant_spec)
    verify_dir = workdir / spec.task_id / "verify" / variant_spec.id
    fake = {
        "schema_version": 1, "run_id": "r", "task_id": spec.task_id,
        "variant": variant_spec.id, "status": status, "verdict": verdict_name,
        "suspect_score": 0.0, "checks_completed": [], "not_applicable": [],
        "checks_infra": [], "evidence": [], "isolation": "docker-run",
        "profile": "deterministic",
        "infra_reason": None if status == "ok" else "boom",
        "fix_verified": True,
        "artifacts_dir": str(verify_dir / "collect" / "artifacts"),
    }
    StageCache(verify_dir / "cache").put(cache_key, fake)

    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", variant_spec.id,
                                 "--workdir", str(workdir)])

    assert result.exit_code == expected_exit, result.output


def test_verify_writes_verdict_json_and_prints_the_banner(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    spec = find_task("click-0001", Path("tasks"))
    pair = _fake_pair(spec)
    calls: list[int] = []
    _fake_heavy_stages(monkeypatch, pair, calls)

    workdir = tmp_path.resolve()
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--workdir", str(workdir)])

    assert result.exit_code == 0, result.output
    assert "VERDICT PASS" in result.output
    assert "score 0.00" in result.output
    assert "checks: 9 completed · 0 n/a · 0 infra" in result.output
    assert "fix_verified: True" in result.output
    assert "profile deterministic · isolation docker-run" in result.output
    assert calls == [1]

    verdict_path = pair.artifacts_dir / "verdict.json"
    assert verdict_path.is_file()
    saved = json.loads(verdict_path.read_text())
    assert saved["verdict"] == "PASS"
    assert saved["task_id"] == "click-0001"


def test_verify_cache_hit_skips_collection_and_replays_the_banner(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    spec = find_task("click-0001", Path("tasks"))
    pair = _fake_pair(spec)
    calls: list[int] = []
    _fake_heavy_stages(monkeypatch, pair, calls)

    workdir = tmp_path.resolve()
    first = runner.invoke(app, ["verify", "--task", "click-0001",
                                "--variant", "gold", "--workdir", str(workdir)])
    assert first.exit_code == 0, first.output
    assert calls == [1]

    verdict_path = pair.artifacts_dir / "verdict.json"
    assert verdict_path.is_file()
    # Simulate the artifacts directory having been cleaned up between runs: a
    # cache-hit replay must still (re)write verdict.json, not only the banner.
    shutil.rmtree(pair.artifacts_dir)

    second = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--workdir", str(workdir)])
    assert second.exit_code == 0, second.output
    assert calls == [1]          # collect_pair (the faked collector) not called again
    assert "(cached)" in second.output
    assert verdict_path.is_file()

    events, _ = read_trace(workdir / "click-0001" / "verify" / "gold" / "trace.jsonl")
    assert "stage_cached" in [e["event"] for e in events]


def test_mutation_batch_trace_event_carries_the_voided_count(tmp_path, monkeypatch):
    """m4-followups batch 1, item 5: `mutation_batch`'s summary payload has to
    let a trace reader reconcile `generated` against the `mutant_result`
    events actually emitted for records; a calibration-voided mutant leaves
    no `mutant_result` event at all (Task 9's own contract), so without a
    `voided` count the arithmetic silently does not close."""
    from skeptic import collector as collector_mod
    from skeptic import mutation as mutation_mod
    from skeptic.checks.observations import CalibrationVoid, MutationReport

    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    spec = find_task("click-0001", Path("tasks"))
    pair = _fake_pair(spec)
    calls: list[int] = []
    _fake_heavy_stages(monkeypatch, pair, calls)

    dummy_mutant = mutation_mod.Mutant(
        mutant_id="k1", path="src/click/termui.py", line=1, operator="off_by_one",
        function="f", population="caller", mutated_source="x = 2\n", valid=True)
    monkeypatch.setattr(mutation_mod, "generate_mutants", lambda pair: (dummy_mutant,))
    monkeypatch.setattr(mutation_mod, "sample_mutants", lambda mutants, budget, seed: mutants)
    void = CalibrationVoid(
        selection=mutation_mod.FULL_SUITE, calibration_exit=1,
        excluded_mutant_ids=("k1",), reason="calibrated at exit 1")
    report = MutationReport(
        seed=spec.verification.mutation.seed, budget=spec.verification.mutation.budget_mutants,
        generated=1, records=(), calibration_void=(void,))
    monkeypatch.setattr(collector_mod, "observe_mutation", lambda *a, **k: report)

    workdir = tmp_path.resolve()
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--workdir", str(workdir)])

    assert result.exit_code == 0, result.output
    events, _ = read_trace(workdir / "click-0001" / "verify" / "gold" / "trace.jsonl")
    batch = next(e for e in events if e["event"] == "mutation_batch")
    assert batch["payload"]["generated"] == 1
    assert [e for e in events if e["event"] == "mutant_result"] == []
    assert batch["payload"]["voided"] == 1


def test_verify_cache_key_changes_with_patch_bytes_and_source_and_config(tmp_path, monkeypatch):
    spec = make_task_spec()
    variant = spec.evaluation.variants[0]
    base = _verify_cache_key(spec, variant)

    # 1. the variant's patch bytes change (same path family, different tmp file)
    patch_copy = tmp_path / "variant.diff"
    patch_copy.write_bytes(Path(variant.patch).read_bytes() + b"\n# extra\n")
    changed_patch = variant.model_copy(update={"patch": str(patch_copy)})
    assert _verify_cache_key(spec, changed_patch) != base

    # 2. the detector source moves (verifier_revision)
    monkeypatch.setattr("skeptic.orchestrator.verifier_revision", lambda: "deadbeefcafe")
    assert _verify_cache_key(spec, variant) != base
    monkeypatch.undo()

    # 3. a verification config field changes
    edited_spec = spec.model_copy(update={
        "verification": spec.verification.model_copy(
            update={"patch_coverage_min": spec.verification.patch_coverage_min + 0.05}),
    })
    assert _verify_cache_key(edited_spec, variant) != base

    # 4. the seed sub-spec changes: t1_outcomes reads failing_tests/quarantine
    # directly, and t1_collect reads quarantine, so the key has to move even
    # though the seed patch bytes themselves are untouched.
    reseeded_spec = spec.model_copy(update={
        "seed": spec.seed.model_copy(
            update={"failing_tests": [*spec.seed.failing_tests,
                                      "tests/test_extra.py::test_new"]}),
    })
    assert _verify_cache_key(reseeded_spec, variant) != base


def _flip_commit(tmp_path: Path) -> tuple[str, str]:
    spec = make_task_spec()
    variant = spec.evaluation.variants[0]
    base = _verify_cache_key(spec, variant)
    flipped = spec.model_copy(update={
        "repo": spec.repo.model_copy(update={"commit": "1" * 40}),
    })
    return base, _verify_cache_key(flipped, variant)


def _flip_environment(tmp_path: Path) -> tuple[str, str]:
    spec = make_task_spec()
    variant = spec.evaluation.variants[0]
    base = _verify_cache_key(spec, variant)
    flipped = spec.model_copy(update={
        "environment": spec.environment.model_copy(
            update={"timeout_s": spec.environment.timeout_s + 1}),
    })
    return base, _verify_cache_key(flipped, variant)


def _flip_builder_input(tmp_path: Path) -> tuple[str, str]:
    spec = make_task_spec()
    variant = spec.evaluation.variants[0]
    base = _verify_cache_key(spec, variant)
    flipped = spec.model_copy(update={
        "builder_input": spec.builder_input.model_copy(
            update={"allowed_paths": [*spec.builder_input.allowed_paths, "extra/"]}),
    })
    return base, _verify_cache_key(flipped, variant)


def _flip_seed_patch_bytes(tmp_path: Path) -> tuple[str, str]:
    """Same path both sides, only the bytes on disk move: the "bytes, not
    path" contract `_verify_cache_key`'s own docstring claims for the seed
    patch (`bug_patch` swapped for its sha256 "so the same bytes-not-path
    rule" the variant patch gets "applies there too"), which a same-path
    edit is the only way to actually exercise. The variant-patch case above
    and the reseeded-spec case both vary path or fields instead, so neither
    would catch a regression that hashed the wrong file or dropped the
    override back to the raw path string.
    """
    spec = make_task_spec()
    variant = spec.evaluation.variants[0]
    seed_copy = tmp_path / "seed.diff"
    original = Path(spec.seed.bug_patch).read_bytes()
    seed_copy.write_bytes(original)
    pinned = spec.model_copy(update={
        "seed": spec.seed.model_copy(update={"bug_patch": str(seed_copy)}),
    })
    base = _verify_cache_key(pinned, variant)
    seed_copy.write_bytes(original + b"\n# extra\n")
    return base, _verify_cache_key(pinned, variant)


@pytest.mark.parametrize("flip", [
    _flip_commit, _flip_environment, _flip_builder_input, _flip_seed_patch_bytes,
], ids=["commit", "environment", "builder_input", "seed_patch_bytes"])
def test_verify_cache_key_changes_with_the_remaining_key_inputs(tmp_path, flip):
    """The sibling test above pins four of `_verify_cache_key`'s inputs
    (variant patch bytes, verifier_revision, a verification config field, the
    seed sub-spec's own fields). The M4 wave A final review named four more a
    refactor could drop from the key while the suite stayed green:
    `repo.commit`, `environment`, `builder_input`, and the seed patch's own
    bytes. Parametrized so dropping any one of the four reddens exactly that
    case rather than leaving the whole input silently uncovered.
    """
    base, flipped = flip(tmp_path)
    assert flipped != base


@pytest.mark.docker
@pytest.mark.slow
def test_verify_minirepo_gold_passes_end_to_end(tmp_path, minirepo_spec_and_repo):
    spec, repo_dir = minirepo_spec_and_repo
    tasks_dir = repo_dir.parent / "tasks"
    workdir = (tmp_path / "workdir").resolve()

    result = runner.invoke(app, ["verify", "--task", spec.task_id, "--variant", "gold",
                                 "--tasks-dir", str(tasks_dir), "--workdir", str(workdir)])

    assert result.exit_code == 0, result.output
    assert "VERDICT PASS" in result.output
    verdict_path = workdir / spec.task_id / "verify" / "gold" / "collect" / "artifacts" / "verdict.json"
    assert verdict_path.is_file()
    saved = json.loads(verdict_path.read_text())
    assert saved["verdict"] == "PASS"
    assert saved["task_id"] == spec.task_id


@pytest.mark.docker
@pytest.mark.slow
def test_verify_minirepo_h1_fails_end_to_end(tmp_path):
    from tests.helpers import load_hack_fixture, make_minirepo_task

    tasks_dir, task_id = make_minirepo_task(
        tmp_path, extra_variants=[("h1", "hacked", load_hack_fixture("h1-excision"))])
    workdir = (tmp_path / "workdir").resolve()

    result = runner.invoke(app, ["verify", "--task", task_id, "--variant", "h1",
                                 "--tasks-dir", str(tasks_dir), "--workdir", str(workdir)])

    assert result.exit_code == 2, result.output
    assert "collect_shrinkage" in result.output
