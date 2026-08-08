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
    monkeypatch.setattr(
        checks, "run_verify_layer", lambda p, profile="deterministic": _pass_layer_outcome())


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


# --- task 2b: `--candidate-diff`, mutually exclusive with `--variant`. The
# guard needs neither `spec` nor docker, so it fires before either (`called`
# stays empty), the same "guard comes before find_task and docker" shape the
# profile/runner guards above already hold.

def test_verify_rejects_neither_variant_nor_candidate_diff(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(cli, "_docker_available", lambda: called.append("docker") or True)
    result = runner.invoke(app, ["verify", "--task", "no-such-task",
                                 "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "--variant" in result.output and "--candidate-diff" in result.output
    assert "Next:" in result.output
    assert called == []


def test_verify_rejects_both_variant_and_candidate_diff(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(cli, "_docker_available", lambda: called.append("docker") or True)
    diff_path = tmp_path / "candidate.diff"
    diff_path.write_text("--- a/x\n+++ b/x\n")
    result = runner.invoke(app, ["verify", "--task", "no-such-task",
                                 "--variant", "gold", "--candidate-diff", str(diff_path),
                                 "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "--variant" in result.output and "--candidate-diff" in result.output
    assert "Next:" in result.output
    assert called == []


def test_verify_candidate_diff_missing_path_is_infra_error(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    missing = tmp_path / "nope.diff"
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--candidate-diff", str(missing),
                                 "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert str(missing) in result.output
    assert "Next:" in result.output


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
    cache_key = _verify_cache_key(spec, variant_spec, "deterministic")
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
    assert "checks: 11 completed · 0 n/a · 0 infra" in result.output
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


def test_verify_rotates_its_own_trace_before_a_second_direct_run(tmp_path, monkeypatch):
    # Direct-verify twin of test_cli_build's
    # test_build_rotates_its_own_trace_before_a_second_direct_run. run_id is
    # deterministic per cache key (f"verify-{cache_key}"), and TraceWriter
    # opens in append mode: two direct `skeptic verify` calls for the same
    # task+variant, with no sweep rotating between them, used to share one
    # growing trace.jsonl. verify() now rotates its own trace dir before the
    # cache check (cli.py:949), so a second direct call starts a clean
    # trace.jsonl regardless of whether a sweep ever drives it. This reuses
    # test_verify_cache_hit_skips_collection_and_replays_the_banner's own
    # fixture shape: the first invocation is a real (uncached) run and the
    # second is a cache hit, so the two runs' event sets are naturally
    # disjoint (stage_start/mutation_batch/probe_batch/stage_end only on the
    # first; stage_cached only on the second) and make a clean rotation
    # probe.
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

    verify_dir = workdir / "click-0001" / "verify" / "gold"
    first_events, _ = read_trace(verify_dir / "trace.jsonl")
    assert "stage_end" in [e["event"] for e in first_events]

    second = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--workdir", str(workdir)])
    assert second.exit_code == 0, second.output
    assert calls == [1]          # cache hit: do_verify (the faked collector) not re-run

    second_events, _ = read_trace(verify_dir / "trace.jsonl")
    assert [e["event"] for e in second_events] == ["spec_loaded", "stage_cached"], (
        "the second run's own trace.jsonl must hold only its own events, "
        "not the first run's stage_start/mutation_batch/probe_batch/stage_end")

    prev_events, _ = read_trace(verify_dir / "trace.prev.jsonl")
    assert [e["event"] for e in prev_events] == [e["event"] for e in first_events], (
        "trace.prev.jsonl must hold exactly the first run's events")


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
    base = _verify_cache_key(spec, variant, "deterministic")

    # 1. the variant's patch bytes change (same path family, different tmp file)
    patch_copy = tmp_path / "variant.diff"
    patch_copy.write_bytes(Path(variant.patch).read_bytes() + b"\n# extra\n")
    changed_patch = variant.model_copy(update={"patch": str(patch_copy)})
    assert _verify_cache_key(spec, changed_patch, "deterministic") != base

    # 2. the detector source moves (verifier_revision)
    monkeypatch.setattr("skeptic.orchestrator.verifier_revision", lambda: "deadbeefcafe")
    assert _verify_cache_key(spec, variant, "deterministic") != base
    monkeypatch.undo()

    # 3. a verification config field changes
    edited_spec = spec.model_copy(update={
        "verification": spec.verification.model_copy(
            update={"patch_coverage_min": spec.verification.patch_coverage_min + 0.05}),
    })
    assert _verify_cache_key(edited_spec, variant, "deterministic") != base

    # 4. the seed sub-spec changes: t1_outcomes reads failing_tests/quarantine
    # directly, and t1_collect reads quarantine, so the key has to move even
    # though the seed patch bytes themselves are untouched.
    reseeded_spec = spec.model_copy(update={
        "seed": spec.seed.model_copy(
            update={"failing_tests": [*spec.seed.failing_tests,
                                      "tests/test_extra.py::test_new"]}),
    })
    assert _verify_cache_key(reseeded_spec, variant, "deterministic") != base


def _flip_commit(tmp_path: Path) -> tuple[str, str]:
    spec = make_task_spec()
    variant = spec.evaluation.variants[0]
    base = _verify_cache_key(spec, variant, "deterministic")
    flipped = spec.model_copy(update={
        "repo": spec.repo.model_copy(update={"commit": "1" * 40}),
    })
    return base, _verify_cache_key(flipped, variant, "deterministic")


def _flip_environment(tmp_path: Path) -> tuple[str, str]:
    spec = make_task_spec()
    variant = spec.evaluation.variants[0]
    base = _verify_cache_key(spec, variant, "deterministic")
    flipped = spec.model_copy(update={
        "environment": spec.environment.model_copy(
            update={"timeout_s": spec.environment.timeout_s + 1}),
    })
    return base, _verify_cache_key(flipped, variant, "deterministic")


def _flip_builder_input(tmp_path: Path) -> tuple[str, str]:
    spec = make_task_spec()
    variant = spec.evaluation.variants[0]
    base = _verify_cache_key(spec, variant, "deterministic")
    flipped = spec.model_copy(update={
        "builder_input": spec.builder_input.model_copy(
            update={"allowed_paths": [*spec.builder_input.allowed_paths, "extra/"]}),
    })
    return base, _verify_cache_key(flipped, variant, "deterministic")


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
    base = _verify_cache_key(pinned, variant, "deterministic")
    seed_copy.write_bytes(original + b"\n# extra\n")
    return base, _verify_cache_key(pinned, variant, "deterministic")


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


# --- the paid profile (wave B, task 9) --------------------------------------


def test_unknown_profile_names_both_lanes(tmp_path, monkeypatch):
    """The explain-and-exit contract, restated: an unknown profile's message
    now names both lanes, not just `deterministic`."""
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--profile", "stochastic",
                                 "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "deterministic" in result.output
    assert "paid" in result.output


def test_verify_profile_demo_is_still_unreachable(tmp_path, monkeypatch):
    """`demo` (wave B part 1, `aggregate.EXCUSED_BY_PROFILE`) is deliberately
    not a `verify --profile` value: the future `skeptic demo` command calls
    `run_verify_layer(pair, profile="demo")` directly, and `--profile demo`
    gets the same rejection as any other unrecognized name, still naming
    only the two CLI-visible lanes."""
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--profile", "demo",
                                 "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "Unknown profile 'demo'" in result.output
    assert "deterministic" in result.output
    assert "paid" in result.output


def test_paid_requires_api_key_before_any_image_work(tmp_path, monkeypatch):
    """Mirrors `test_build_requires_api_key_before_docker_work`: the key
    check fails in well under a second, before `_docker_available()` (and
    therefore before any image work) ever runs."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    called = []
    monkeypatch.setattr(cli, "_docker_available", lambda: called.append("docker") or True)
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--profile", "paid",
                                 "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "ANTHROPIC_API_KEY" in result.output
    assert called == []


def test_paid_requires_a_pricing_row(tmp_path, monkeypatch):
    """SKEPTIC_MODEL is a module-level name on `cli` precisely so a test can
    swap it for an unpriced model without touching the real PRICING table."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(cli, "SKEPTIC_MODEL", "no-such-model")
    called = []
    monkeypatch.setattr(cli, "_docker_available", lambda: called.append("docker") or True)
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--profile", "paid",
                                 "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "No pricing entry" in result.output
    assert called == []


def test_paid_confirm_declined_exits_infra_without_spend(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    called = []
    monkeypatch.setattr(cli, "_docker_available", lambda: called.append("docker") or True)
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--profile", "paid",
                                 "--workdir", str(tmp_path)], input="n\n")
    assert result.exit_code == 3
    assert "Declined" in result.output
    assert "--yes" in result.output
    assert called == []          # declined before _docker_available(): no image work


def test_paid_yes_skips_the_confirm(tmp_path, monkeypatch):
    """`--yes` reaches the docker check with no prompt: docker unavailable
    (rather than the confirm's own stdin prompt) is what ends the run, and
    the confirm's own "Proceed" text never appears."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(cli, "_docker_available", lambda: False)
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--profile", "paid", "--yes",
                                 "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "Docker" in result.output
    assert "Proceed" not in result.output


def test_deterministic_never_prompts(tmp_path, monkeypatch):
    """The paid preflight is skipped outright under the default profile: no
    API key required, no cost line, docker unavailable is reached directly."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cli, "_docker_available", lambda: False)
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "Docker" in result.output
    assert "ANTHROPIC_API_KEY" not in result.output


def _fake_heavy_stages_real_registry(monkeypatch, pair):
    """Fakes the same git/docker-touching steps `_fake_heavy_stages` does,
    but leaves `run_verify_layer` real: used by the paid-profile tests below,
    which need the real registry (and the real aggregator's profile gating)
    to observe `t2_advtests`/`t2_judge` actually run, rather than a canned
    `LayerOutcome`. `mutation.generate_mutants` degrades to a harmless, real
    `()` and `observe_probe` is faked to a harmless, real `ProbeReport(calls=
    ())`, the same "keep the sibling lanes healthy" shape
    `_fake_heavy_stages_dead_probe` uses, so the mutation and probe lanes
    complete clean and only the paid lane under test varies. `apply_candidate`
    is faked alongside `apply_patch` (task 2b): a `--candidate-diff` run calls
    it instead of `apply_patch`, onto a `variant-tree` this harness's own
    `materialize`/`snapshot` fakes never actually populate, so the real
    function would try to `git apply` into a directory with no tree in it."""
    from skeptic import candidate, collector, mutation, workspace
    from skeptic.checks.observations import ProbeReport

    monkeypatch.setattr(workspace, "clone_pinned", lambda url, commit, cache: cache)
    monkeypatch.setattr(
        workspace, "materialize",
        lambda repo, commit, dest: dest.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(workspace, "apply_patch", lambda ws, patch: None)
    monkeypatch.setattr(workspace, "apply_candidate", lambda ws, diff: None)
    monkeypatch.setattr(candidate, "snapshot", lambda src, dest: None)
    monkeypatch.setattr(
        candidate, "extract_candidate",
        lambda baseline, workspace, out_diff, allowed_paths: pair.candidate_diff)
    monkeypatch.setattr(
        collector, "collect_pair",
        lambda spec, repo_dir, report, workdir, baseline_cache=None: pair)
    monkeypatch.setattr(mutation, "generate_mutants", lambda pair: ())
    monkeypatch.setattr(collector, "observe_probe", lambda *a, **k: ProbeReport(calls=()))


def test_verify_candidate_diff_drives_the_real_path_and_stamps_candidate_identity(
    tmp_path, monkeypatch
):
    """Task 2b's whole point: `--candidate-diff` runs the real path (the
    build-arm classifier's own shape: materialize + seed patch +
    apply_candidate, in place of a corpus variant's own patch) through to a
    genuine verdict, and the identity flowing into the verify dir, the trace
    run_id, and the verdict's `variant` field is `candidate:<diff stem>`,
    never a corpus variant id. Deterministic profile: task 2b builds only the
    injection, not a paid-profile combination, and a would-be-PASS pair keeps
    the assertion about a genuine verdict rather than an incidental one."""
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    pair = _would_be_pass_pair()
    _fake_heavy_stages_real_registry(monkeypatch, pair)

    diff_path = tmp_path / "build" / "attempt-3" / "candidate.diff"
    diff_path.parent.mkdir(parents=True)
    diff_path.write_text("--- a/x\n+++ b/x\n")

    workdir = (tmp_path / "workdir").resolve()
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--candidate-diff", str(diff_path),
                                 "--workdir", str(workdir)])

    assert result.exit_code == 0, result.output
    assert "VERDICT PASS" in result.output

    identity = "candidate:candidate"      # candidate.diff's own stem
    # The on-disk verify dir swaps ':' for '-': a colon in the host path
    # breaks docker's `-v host:container:ro` mount spec (too many colons),
    # which RunContainer hits for real once collect_pair/enrichment run
    # unfaked (see the docker end-to-end twin of this test below). The
    # colon form still lands in the trace and the verdict (asserted below).
    verify_dir = workdir / "click-0001" / "verify" / "candidate-candidate"
    events, _ = read_trace(verify_dir / "trace.jsonl")
    assert events, "the candidate-scoped verify dir must carry its own trace"
    assert all(e["run_id"] == events[0]["run_id"] for e in events)
    assert events[0]["run_id"].startswith("verify-")

    verdict_path = pair.artifacts_dir / "verdict.json"
    assert verdict_path.is_file()
    saved = json.loads(verdict_path.read_text())
    assert saved["verdict"] == "PASS"
    assert saved["variant"] == identity


def test_verify_candidate_diff_cache_replays_same_bytes_and_misses_on_different_bytes(
    tmp_path, monkeypatch
):
    """The end-to-end half of task 2b's cache proof (the unit-level half is
    `test_verify_cache_key_candidate_diff_is_bytes_not_path` above): the same
    diff bytes at the same path, run twice, replays (`stage_cached`, the
    faked collector not called again); overwriting that path with different
    bytes is a fresh entry (the faked collector called again), mirroring
    `test_verify_cache_hit_skips_collection_and_replays_the_banner`'s own
    replay proof for a corpus variant."""
    from skeptic import collector

    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    pair = _would_be_pass_pair()
    _fake_heavy_stages_real_registry(monkeypatch, pair)
    calls: list[int] = []
    faked_collect_pair = collector.collect_pair
    monkeypatch.setattr(
        collector, "collect_pair",
        lambda *a, **k: (calls.append(1), faked_collect_pair(*a, **k))[1])

    diff_path = tmp_path / "attempt.diff"
    diff_path.write_text("--- a/x\n+++ b/x\n")
    workdir = (tmp_path / "workdir").resolve()

    first = runner.invoke(app, ["verify", "--task", "click-0001",
                                "--candidate-diff", str(diff_path),
                                "--workdir", str(workdir)])
    assert first.exit_code == 0, first.output
    assert calls == [1]

    second = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--candidate-diff", str(diff_path),
                                 "--workdir", str(workdir)])
    assert second.exit_code == 0, second.output
    assert calls == [1]           # cache hit: collect_pair not called again
    assert "(cached)" in second.output

    # On-disk dir is the hyphenated form (verify_dir never carries a colon:
    # see the sibling identity test above); the colon form is the verdict's
    # own `variant` field, checked in the sibling docker end-to-end test.
    events, _ = read_trace(workdir / "click-0001" / "verify" / "candidate-attempt" / "trace.jsonl")
    assert "stage_cached" in [e["event"] for e in events]

    diff_path.write_text("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-1\n+2\n")
    third = runner.invoke(app, ["verify", "--task", "click-0001",
                                "--candidate-diff", str(diff_path),
                                "--workdir", str(workdir)])
    assert third.exit_code == 0, third.output
    assert calls == [1, 1]        # different bytes: fresh entry, collect_pair called again


def _fake_advtests_and_judge(monkeypatch):
    """Fakes `cli.generate_candidates`, `cli.observe_advtests`,
    `cli.judge_diff`, and `anthropic.Anthropic` at the module boundary the
    brief specifies: one trusted, non-diverging candidate and an unflagged
    judge read, so a would-be-PASS pair stays PASS with both paid checks
    genuinely completed rather than captured or excused."""
    import anthropic

    from skeptic.checks.observations import AdvCandidate, AdversarialReport, JudgeReport

    advtests_candidate = AdvCandidate(
        candidate_id="c1", source="def test_c1():\n    pass\n",
        status="trusted", rejected_at=None, detail="cleared the ladder")
    advtests_report = AdversarialReport(
        model="fake-model", n_candidates=1, candidates=(advtests_candidate,),
        trusted=("c1",), divergences=())
    judge_report = JudgeReport(model="fake-model", flagged=False, category=None,
                               rationale="clean")
    judge_io = {"request": {"model": "fake-model", "max_tokens": 2000},
               "response": {"text": "flag: no\nrationale: clean",
                            "usage": {"in_tok": 10, "out_tok": 5}}}
    testgen_io = {"model": "fake", "system": "", "prompt": "", "responses": []}

    monkeypatch.setattr(
        cli, "generate_candidates",
        lambda client, spec, sources, trace: ((), testgen_io))
    monkeypatch.setattr(
        cli, "observe_advtests",
        lambda spec, image_tag, repo_dir, pair, artifacts, candidates, model: advtests_report)
    monkeypatch.setattr(
        cli, "judge_diff", lambda client, diff_text, trace: (judge_report, judge_io))
    monkeypatch.setattr(anthropic, "Anthropic", lambda: object())
    return advtests_report, judge_report, judge_io, testgen_io


def test_paid_runs_enrichments_and_stamps_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    pair = _would_be_pass_pair()
    _fake_heavy_stages_real_registry(monkeypatch, pair)
    _fake_advtests_and_judge(monkeypatch)

    workdir = tmp_path.resolve()
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--profile", "paid", "--yes",
                                 "--workdir", str(workdir)])

    assert result.exit_code == 0, result.output
    assert "VERDICT PASS" in result.output
    assert "profile paid" in result.output

    saved = json.loads((pair.artifacts_dir / "verdict.json").read_text())
    assert saved["verdict"] == "PASS"
    assert saved["profile"] == "paid"
    assert "t2_advtests" in saved["checks_completed"]
    assert "t2_judge" in saved["checks_completed"]


def test_verify_sources_include_one_hop(monkeypatch, tmp_path):
    """The one-hop widening (task 9): cli's advtests sources dict merges in
    `one_hop_sources`. `_fake_heavy_stages_real_registry` fakes
    `workspace.materialize` to a bare mkdir, so the advtests-sources tree it
    builds is empty; re-faked here to write the changed file (which imports
    one_hop_target) plus src/click/one_hop_target.py into whichever tree it's
    asked to build, with the candidate diff's changed_files naming the
    importer under click's real src_dirs so the resolver has something to
    walk. `cli.generate_candidates` is re-faked on top of
    `_fake_advtests_and_judge`'s own fake to record the sources it's called
    with instead of just returning a canned report."""
    import dataclasses

    from skeptic import workspace

    pair = _would_be_pass_pair()
    pair = pair.model_copy(update={
        "candidate_diff": dataclasses.replace(
            pair.candidate_diff, changed_files=["src/click/importer.py"]),
    })
    _fake_heavy_stages_real_registry(monkeypatch, pair)
    _fake_advtests_and_judge(monkeypatch)

    def fake_materialize(repo, commit, dest):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "src" / "click").mkdir(parents=True, exist_ok=True)
        (dest / "src/click/importer.py").write_text("from . import one_hop_target\n")
        (dest / "src/click/one_hop_target.py").write_text("TARGET = 1\n")

    monkeypatch.setattr(workspace, "materialize", fake_materialize)

    seen = {}

    def fake_generate(client, spec, sources, trace):
        seen.update(sources)
        return (), {"model": "fake", "system": "", "prompt": "", "responses": []}

    monkeypatch.setattr(cli, "generate_candidates", fake_generate)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(cli, "_docker_available", lambda: True)

    workdir = tmp_path.resolve()
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--profile", "paid", "--yes",
                                 "--workdir", str(workdir)])

    assert result.exit_code == 0, result.output
    assert any(path.endswith("one_hop_target.py") for path in seen)


HOLDOUT_SENTINEL = "SENTINEL_FROM_A_HELD_OUT_TEST_FILE"


def _write(tmp_path: Path, relpath: str, content: str) -> None:
    path = tmp_path / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_testgen_prompt_never_sees_a_changed_test_file(monkeypatch, tmp_path):
    """The wave A final review's finding, pinned on the real path.

    A candidate diff that touches `tests/` used to put that file's pristine
    body straight into the `sources` dict (cli.py's comprehension filtered on
    `is_file()` alone), and from there into the testgen prompt. Two of the
    eight published wave A runs hit it. The guard test that existed pinned
    `build_testgen_prompt`'s two-parameter signature, which cannot bound a
    dict the caller fills, so this one asserts on what the caller built.

    The source and test paths below sit under click-0001's real
    `src_dirs`/`test_dirs` (`src/click/`, `tests/`), the same real spec the
    CLI's `--task click-0001` resolves to, since the filter under test reads
    `spec.environment.src_dirs`.
    """
    import dataclasses

    from skeptic import workspace

    seen = {}

    def fake_generate(client, spec, sources, trace):
        seen.update(sources)
        return (), {"model": "fake", "system": "", "prompt": "", "responses": []}

    def fake_materialize(repo_dir, commit, dest):
        _write(dest, "src/click/target.py", "def f():\n    return 1\n")
        _write(dest, "tests/test_target.py",
               f"# {HOLDOUT_SENTINEL}\ndef test_f():\n    pass\n")
        return dest

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    pair = _would_be_pass_pair()
    pair = pair.model_copy(update={
        "candidate_diff": dataclasses.replace(
            pair.candidate_diff,
            changed_files=["src/click/target.py", "tests/test_target.py"]),
    })
    _fake_heavy_stages_real_registry(monkeypatch, pair)
    _fake_advtests_and_judge(monkeypatch)
    monkeypatch.setattr(cli, "generate_candidates", fake_generate)
    monkeypatch.setattr(workspace, "materialize", fake_materialize)

    workdir = tmp_path.resolve()
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--profile", "paid", "--yes",
                                 "--workdir", str(workdir)])

    assert result.exit_code == 0, result.output
    assert seen, "the paid lane never reached testgen; the harness is wrong"
    assert not any(path.startswith("tests/") for path in seen)
    assert not any(HOLDOUT_SENTINEL in body for body in seen.values())
    assert "src/click/target.py" in seen


def test_one_hop_sources_ignores_a_held_out_test_files_own_imports(monkeypatch, tmp_path):
    """The `one_hop_sources` half of the holdout fix: cli.py hands it
    `src_changed`, not the raw `changed_files`.

    `one_hop_sources`'s own resolver already refuses to return a `tests/`
    path (bounded by `src_dirs` on both branches), so a held-out test file
    with no imports of its own, the shape the sibling
    `test_testgen_prompt_never_sees_a_changed_test_file` uses, cannot tell
    filtered from unfiltered apart: revert cli.py's `one_hop_sources` call
    back to the raw list and that test still passes. This one gives the
    held-out test file an import of a real `src_dirs` module nothing else
    reaches, so the observable difference is the module's presence in
    `sources`, not its own body: unfiltered, the test file's AST seeds the
    one-hop walk and the module leaks in; filtered, the test file is never
    parsed and the module never surfaces.
    """
    import dataclasses

    from skeptic import workspace

    seen = {}

    def fake_generate(client, spec, sources, trace):
        seen.update(sources)
        return (), {"model": "fake", "system": "", "prompt": "", "responses": []}

    def fake_materialize(repo_dir, commit, dest):
        _write(dest, "src/click/target.py", "def f():\n    return 1\n")
        _write(dest, "src/click/test_only_target.py", "TEST_ONLY = 1\n")
        _write(dest, "tests/test_target.py",
               "import click.test_only_target\n\n\ndef test_f():\n    pass\n")
        return dest

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    pair = _would_be_pass_pair()
    pair = pair.model_copy(update={
        "candidate_diff": dataclasses.replace(
            pair.candidate_diff,
            changed_files=["src/click/target.py", "tests/test_target.py"]),
    })
    _fake_heavy_stages_real_registry(monkeypatch, pair)
    _fake_advtests_and_judge(monkeypatch)
    monkeypatch.setattr(cli, "generate_candidates", fake_generate)
    monkeypatch.setattr(workspace, "materialize", fake_materialize)

    workdir = tmp_path.resolve()
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--profile", "paid", "--yes",
                                 "--workdir", str(workdir)])

    assert result.exit_code == 0, result.output
    assert seen, "the paid lane never reached testgen; the harness is wrong"
    assert "src/click/test_only_target.py" not in seen


def test_paid_judge_io_artifact_persists_request_and_response(tmp_path, monkeypatch):
    """The amended judge-io-artifact contract (DECISIONS row 132's flagged
    gap): `t2_judge_io.json` exists after a faked paid run and carries
    `judge_diff`'s own `{"request", "response"}` shape. `t2_advtests_io.json`
    lands beside it on the same judge-IO precedent, task 7's own contract
    (DECISIONS row 143): raw response text and stop_reason persisted so a
    1-block gold-prime response is inspectable after the fact."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    pair = _would_be_pass_pair()
    _fake_heavy_stages_real_registry(monkeypatch, pair)
    _, _, judge_io, testgen_io = _fake_advtests_and_judge(monkeypatch)

    workdir = tmp_path.resolve()
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--profile", "paid", "--yes",
                                 "--workdir", str(workdir)])

    assert result.exit_code == 0, result.output
    io_path = pair.artifacts_dir / "t2_judge_io.json"
    assert io_path.is_file()
    saved = json.loads(io_path.read_text())
    assert set(saved) == {"request", "response"}
    assert saved == judge_io

    advtests_io_path = pair.artifacts_dir / "t2_advtests_io.json"
    assert advtests_io_path.is_file()
    saved_advtests_io = json.loads(advtests_io_path.read_text())
    assert set(saved_advtests_io) == {"model", "system", "prompt", "responses"}
    assert saved_advtests_io == testgen_io


def test_advtests_io_persists_when_the_ladder_dies(tmp_path, monkeypatch):
    """Row 143's write-before-the-call, proven on the failure path.

    `t2_advtests_io.json` is written before `observe_advtests` runs, on
    purpose: when the acceptance ladder dies, the raw model response and
    stop_reason are the only evidence left of why a run produced nothing.
    `test_paid_judge_io_artifact_persists_request_and_response` above only
    fakes `observe_advtests` to a successful canned report, so it cannot
    tell a write-before-the-call from a write-after: moving the write past
    the call would keep that test green while losing the one artifact that
    explains a dead ladder. This one fakes `observe_advtests` to raise and
    checks the artifact survives.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    pair = _would_be_pass_pair()
    _fake_heavy_stages_real_registry(monkeypatch, pair)
    _fake_advtests_and_judge(monkeypatch)

    testgen_io = {"model": "fake", "system": "", "prompt": "",
                 "responses": [{"in_tok": 100, "out_tok": 50,
                               "text": "def test_c1():\n    pass\n",
                               "stop_reason": "end_turn"}]}
    monkeypatch.setattr(
        cli, "generate_candidates", lambda client, spec, sources, trace: ((), testgen_io))

    def boom(*args, **kwargs):
        raise SkepticInfraError("container died")

    monkeypatch.setattr(cli, "observe_advtests", boom)

    workdir = tmp_path.resolve()
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--profile", "paid", "--yes",
                                 "--workdir", str(workdir)])

    # A dead paid enrichment on a would-be-PASS pair reports INFRA_ERROR
    # (the same shape the dead-mutation and dead-probe tests pin), never a
    # silent PASS: the command completes rather than crashing.
    assert result.exit_code == 3, result.output
    assert "INFRA ERROR" in result.output

    io_path = pair.artifacts_dir / "t2_advtests_io.json"
    assert io_path.is_file()
    assert json.loads(io_path.read_text())["responses"]


def test_judge_reads_a_bad_byte_in_the_candidate_diff_through_read_source(
    tmp_path, monkeypatch
):
    """Task 3's review found the identical latent shape one lane over: this
    judge block used to read the candidate diff with a bare `.read_text()`
    inside the same paid-isolation `except Exception` block task 3 hardened
    for the advtests lane. A source file carrying one non-UTF-8 byte lands
    that byte in the unified diff verbatim, and the bare read died there with
    UnicodeDecodeError, leaving only a judge_enrichment_failed trace event.

    `_fake_heavy_stages` (not `_fake_heavy_stages_real_registry`) fakes
    `run_verify_layer` wholesale: the real `t1_coverage` check and
    `mutation.generate_mutants` also call `diff_path.read_text()` on their
    own, unrelated to this fix, and would choke on the identical byte if
    left real, muddying which read this test is isolating.
    """
    from skeptic.checks.observations import JudgeReport

    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    spec = find_task("click-0001", Path("tasks"))
    pair = _fake_pair(spec)
    pair.candidate_diff.diff_path.write_bytes(
        b"--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new \xff comment\n")
    calls: list[int] = []
    _fake_heavy_stages(monkeypatch, pair, calls)
    _fake_advtests_and_judge(monkeypatch)

    seen_diff_text = []

    def fake_judge_diff(client, diff_text, trace):
        seen_diff_text.append(diff_text)
        return (JudgeReport(model="fake-model", flagged=False, category=None,
                            rationale="clean"),
                {"request": {}, "response": {}})

    monkeypatch.setattr(cli, "judge_diff", fake_judge_diff)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    workdir = tmp_path.resolve()
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--profile", "paid", "--yes",
                                 "--workdir", str(workdir)])

    assert result.exit_code == 0, result.output
    assert seen_diff_text, "judge_diff was never reached: the bad byte killed the read first"
    assert "�" in seen_diff_text[0]

    events, _ = read_trace(workdir / "click-0001" / "verify" / "gold" / "trace.jsonl")
    assert any(e["event"] == "judge_call" for e in events)
    assert not any(e["event"] == "judge_enrichment_failed" for e in events)


def test_deterministic_verdict_carries_two_not_applicable_rows(tmp_path, monkeypatch):
    """The contract from the brief's own words: a deterministic run is
    byte-identical to wave A's except two NA rows in the verdict and the two
    synthetic NA artifacts. `run_verify_layer` is left real here (unlike
    `_fake_heavy_stages`'s canned `LayerOutcome`), so `aggregate.
    PAID_ONLY_CHECKS`'s own synthesis is what this test observes."""
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    pair = _would_be_pass_pair()
    _fake_heavy_stages_real_registry(monkeypatch, pair)

    workdir = tmp_path.resolve()
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--workdir", str(workdir)])

    assert result.exit_code == 0, result.output
    assert "VERDICT PASS" in result.output

    saved = json.loads((pair.artifacts_dir / "verdict.json").read_text())
    assert saved["profile"] == "deterministic"
    assert set(saved["not_applicable"]) & {"t2_advtests", "t2_judge"} == {
        "t2_advtests", "t2_judge"}
    for name in ("t2_advtests", "t2_judge"):
        artifact = pair.artifacts_dir / f"{name}.json"
        assert artifact.is_file()
        payload = json.loads(artifact.read_text())
        assert payload["status"] == "not_applicable"


def test_enrichment_failure_surfaces_as_check_infra_not_crash(tmp_path, monkeypatch):
    """A dead advtests batch lands `t2_advtests` in `checks_infra` (its own
    INFRA-on-`None` guard, `t2_advtests.run`), not a crash, and does not take
    the independently-healthy judge check down with it (DECISIONS row 116's
    isolation rule, extended to the two paid checks)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    pair = _would_be_pass_pair()
    _fake_heavy_stages_real_registry(monkeypatch, pair)
    _fake_advtests_and_judge(monkeypatch)

    def _boom(client, spec, sources, trace):
        raise RuntimeError("generation boom")

    monkeypatch.setattr(cli, "generate_candidates", _boom)

    workdir = tmp_path.resolve()
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--profile", "paid", "--yes",
                                 "--workdir", str(workdir)])

    assert result.exit_code == 3, result.output
    assert "INFRA ERROR" in result.output

    saved = json.loads((pair.artifacts_dir / "verdict.json").read_text())
    assert saved["status"] == "INFRA_ERROR"
    assert "t2_advtests" in saved["checks_infra"]
    assert "t2_judge" in saved["checks_completed"]


def test_client_construction_failure_infra_lists_both_paid_checks(tmp_path, monkeypatch):
    """Review round 1, finding 1: a non-`SkepticInfraError` raise out of
    `anthropic.Anthropic()` must not escape both isolation blocks and kill
    the run with a traceback. `client` is built lazily inside each block's
    own `try`, so a dead constructor is captured by the advtests block's
    `except` first and then by the judge block's `except` when it tries
    again from scratch: both paid checks land in `checks_infra`, and the run
    still ends in a full, cleanly-reported INFRA_ERROR verdict rather than
    an uncaught exception."""
    import anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    pair = _would_be_pass_pair()
    _fake_heavy_stages_real_registry(monkeypatch, pair)

    def _boom():
        raise RuntimeError("no credentials configured")

    monkeypatch.setattr(anthropic, "Anthropic", _boom)

    workdir = tmp_path.resolve()
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--variant", "gold", "--profile", "paid", "--yes",
                                 "--workdir", str(workdir)])

    assert result.exit_code == 3, result.output
    assert "INFRA ERROR" in result.output

    saved = json.loads((pair.artifacts_dir / "verdict.json").read_text())
    assert saved["status"] == "INFRA_ERROR"
    assert "t2_advtests" in saved["checks_infra"]
    assert "t2_judge" in saved["checks_infra"]


def test_cache_key_differs_by_profile():
    spec = make_task_spec()
    variant = spec.evaluation.variants[0]
    assert (_verify_cache_key(spec, variant, "deterministic")
            != _verify_cache_key(spec, variant, "paid"))


def test_verify_cache_key_candidate_diff_is_bytes_not_path(tmp_path):
    """Task 2b's cache-key extension: a candidate-diff run has no variant id
    or variant patch, so the key's `variant`/`variant_patch` ingredients come
    from the diff's own identity (`candidate:<stem>`) and its sha256 instead.
    Two build-arm attempts share the classifier's own filename convention
    (`candidate.diff` under an `attempt-N` directory, task 2b's brief), so two
    different diffs with the SAME stem/identity still need two cache entries:
    the sha256 ingredient, not the stem alone, is what tells them apart. A
    same-path edit (the seed-patch precedent's own shape, `_flip_seed_patch_
    bytes` above) is a fresh entry too, and the candidate namespace never
    collides with a corpus variant's own key on the same spec."""
    spec = make_task_spec()
    variant = spec.evaluation.variants[0]
    attempt_1 = tmp_path / "attempt-1" / "candidate.diff"
    attempt_2 = tmp_path / "attempt-2" / "candidate.diff"
    attempt_1.parent.mkdir(parents=True)
    attempt_2.parent.mkdir(parents=True)
    attempt_1.write_bytes(b"--- a/x\n+++ b/x\n@@ -1 +1 @@\n-1\n+2\n")
    attempt_2.write_bytes(b"--- a/x\n+++ b/x\n@@ -1 +1 @@\n-1\n+3\n")

    key_1 = _verify_cache_key(spec, None, "deterministic", candidate_diff=attempt_1)
    key_2 = _verify_cache_key(spec, None, "deterministic", candidate_diff=attempt_2)
    assert key_1 != key_2      # same stem/identity ("candidate"), different bytes: two entries

    # Same path, bytes unchanged: a second, identical call replays.
    assert key_1 == _verify_cache_key(spec, None, "deterministic", candidate_diff=attempt_1)

    # Same path, bytes edited: a fresh entry.
    attempt_1.write_bytes(attempt_1.read_bytes() + b"\n# extra\n")
    assert _verify_cache_key(spec, None, "deterministic", candidate_diff=attempt_1) != key_1

    # The candidate: namespace never collides with a corpus variant's own key.
    assert key_1 != _verify_cache_key(spec, variant, "deterministic")


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
def test_verify_minirepo_deterministic_profile_carries_na_rows_end_to_end(
    tmp_path, minirepo_spec_and_repo
):
    """The one real, docker-backed smoke for the paid-profile wiring (wave B,
    task 9): a genuine deterministic run against the minirepo fixture still
    lists `t2_advtests`/`t2_judge` as `not_applicable`, with their own
    synthetic artifacts on disk, through a real container rather than a
    faked check layer."""
    spec, repo_dir = minirepo_spec_and_repo
    tasks_dir = repo_dir.parent / "tasks"
    workdir = (tmp_path / "workdir").resolve()

    result = runner.invoke(app, ["verify", "--task", spec.task_id, "--variant", "gold",
                                 "--tasks-dir", str(tasks_dir), "--workdir", str(workdir)])

    assert result.exit_code == 0, result.output
    assert "VERDICT PASS" in result.output
    assert "profile deterministic" in result.output
    artifacts_dir = workdir / spec.task_id / "verify" / "gold" / "collect" / "artifacts"
    verdict_path = artifacts_dir / "verdict.json"
    assert verdict_path.is_file()
    saved = json.loads(verdict_path.read_text())
    assert saved["verdict"] == "PASS"
    assert saved["profile"] == "deterministic"
    assert set(saved["not_applicable"]) & {"t2_advtests", "t2_judge"} == {
        "t2_advtests", "t2_judge"}
    for name in ("t2_advtests", "t2_judge"):
        payload = json.loads((artifacts_dir / f"{name}.json").read_text())
        assert payload["status"] == "not_applicable"


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


@pytest.mark.docker
@pytest.mark.slow
def test_verify_candidate_diff_minirepo_gold_passes_end_to_end(tmp_path, minirepo_spec_and_repo):
    """The real, docker-backed smoke for `--candidate-diff` (task 2b): the
    gold variant's own patch bytes, applied as a candidate diff instead of a
    corpus variant (the identical `git apply` mechanics `apply_candidate` and
    `apply_patch` share, `workspace.py`'s own docstrings), drive the real
    materialize + seed patch + apply_candidate path through to a genuine
    PASS, with verdict.json landing under the candidate-scoped verify dir
    rather than a variant one. Twin of `test_verify_minirepo_gold_passes_
    end_to_end` above, `--candidate-diff` in place of `--variant gold`."""
    spec, repo_dir = minirepo_spec_and_repo
    tasks_dir = repo_dir.parent / "tasks"
    workdir = (tmp_path / "workdir").resolve()
    gold_variant = next(v for v in spec.evaluation.variants if v.id == "gold")

    result = runner.invoke(app, ["verify", "--task", spec.task_id,
                                 "--candidate-diff", gold_variant.patch,
                                 "--tasks-dir", str(tasks_dir), "--workdir", str(workdir)])

    assert result.exit_code == 0, result.output
    assert "VERDICT PASS" in result.output
    identity = f"candidate:{Path(gold_variant.patch).stem}"
    # The on-disk verify dir is the hyphenated form (a colon in the host path
    # breaks docker's `-v host:container:ro` mount spec, "too many colons",
    # which RunContainer would hit on every mount under this dir); the colon
    # form is what verdict.json's own `variant` field still carries.
    verdict_path = (workdir / spec.task_id / "verify" / identity.replace(":", "-")
                     / "collect" / "artifacts" / "verdict.json")
    assert verdict_path.is_file()
    saved = json.loads(verdict_path.read_text())
    assert saved["verdict"] == "PASS"
    assert saved["task_id"] == spec.task_id
    assert saved["variant"] == identity
