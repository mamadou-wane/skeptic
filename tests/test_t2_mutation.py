"""The bridge, the batch runner, and the check: Task 9's three layers.

Bridge tests build a bare `CoverageReport` and never touch a container.
Execution tests fake the subprocess boundary the way `tests/test_collector.py`
does (`sandbox._run` replaced with a function that writes the exit files the
batch script would have written), so `collector.observe_mutation`'s read-back
and status-mapping logic runs for real against a real `RunContainer` and a
real docker argv, minus the daemon. Check tests build `MutationReport`/
`MutantRecord` by hand and read `t2_mutation.run`'s artifact back to verify
its rates are hand-computable. Docker tests run the whole pipeline for real
against the minirepo fixture corpus, through the session-scoped `enriched_pair`
fixture (`tests/test_hack_fixtures.py`), which mirrors that module's own
`layer_pair`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from skeptic import collector, mutation
from skeptic.checks import t2_mutation
from skeptic.checks.observations import (
    CalibrationVoid,
    CoverageReport,
    MutantRecord,
    MutationReport,
)
from skeptic.errors import SkepticInfraError
from skeptic.sandbox import ExecResult
from tests.helpers import make_observed_pair, make_task_spec

# `enriched_pair` depends on `layer_pair` by fixture name, and pytest resolves
# a fixture dependency within the requesting module's own scope chain, so
# both names have to be imported here even though only `enriched_pair` is
# used directly.
from tests.test_hack_fixtures import enriched_pair, layer_pair  # noqa: F401

# --- the bridge: mutation.select_tests --------------------------------------


def _coverage(contexts: dict[str, dict[int, tuple[str, ...]]]) -> CoverageReport:
    return CoverageReport(statements={}, executed={}, contexts=contexts,
                          measured_files=tuple(contexts), run_contexts=())


def test_select_tests_maps_module_and_function_to_a_nodeid():
    coverage = _coverage({"minirepo.py": {6: ("test_minirepo.test_parse_range_basic",)}})
    collected = ("tests/test_minirepo.py::test_parse_range_basic",
                "tests/test_minirepo.py::test_parse_range_wide")

    result = mutation.select_tests(coverage, collected, "minirepo.py", 6)

    assert result == ("tests/test_minirepo.py::test_parse_range_basic",)


def test_select_tests_collapses_a_parametrized_family_to_a_superset():
    coverage = _coverage({"m.py": {10: ("test_m.test_thing",)}})
    collected = ("tests/test_m.py::test_thing[a]", "tests/test_m.py::test_thing[b]",
                "tests/test_m.py::test_other")

    result = mutation.select_tests(coverage, collected, "m.py", 10)

    # A superset: both parametrizations of the family the context names,
    # since the context alone cannot say which one the covering run took.
    assert set(result) == {"tests/test_m.py::test_thing[a]", "tests/test_m.py::test_thing[b]"}


def test_select_tests_handles_a_class_based_context():
    coverage = _coverage({"m.py": {5: ("test_m.TestCls.test_x",)}})
    collected = ("tests/test_m.py::TestCls::test_x", "tests/test_m.py::TestCls::test_y")

    result = mutation.select_tests(coverage, collected, "m.py", 5)

    assert result == ("tests/test_m.py::TestCls::test_x",)


def test_select_tests_returns_none_for_an_uncovered_line():
    # The line is absent from the per-file map entirely.
    absent = _coverage({"m.py": {}})
    assert mutation.select_tests(absent, ("tests/test_m.py::test_x",), "m.py", 9) is None

    # The line carries only the empty (import-time) context.
    import_only = _coverage({"m.py": {5: ("",)}})
    assert mutation.select_tests(import_only, ("tests/test_m.py::test_x",), "m.py", 5) is None


def test_select_tests_raises_on_an_unmatched_context():
    coverage = _coverage({"m.py": {5: ("test_ghost.test_missing",)}})

    with pytest.raises(SkepticInfraError, match="test_ghost.test_missing") as exc:
        mutation.select_tests(coverage, ("tests/test_m.py::test_x",), "m.py", 5)
    assert "infra failure, never evidence" in str(exc.value)


def test_select_tests_resolves_a_nested_package_context():
    """click's own shape: `tests/` carries no `__init__.py` (contributes no
    package prefix) but `tests/test_utils/` does, so a test under it imports
    as `test_utils.<file>`, two dotted segments before the qualname. Measured
    on the real corpus (Task 11): the context on `src/click/utils.py:112` is
    `test_utils.test_make_default_short_help.test_make_default_short_help`,
    against a nodeid under `tests/test_utils/`."""
    coverage = _coverage(
        {"m.py": {6: ("test_utils.test_x.test_fn",)}})
    collected = ("tests/test_utils/test_x.py::test_fn",
                "tests/test_other.py::test_fn")

    result = mutation.select_tests(coverage, collected, "m.py", 6)

    assert result == ("tests/test_utils/test_x.py::test_fn",)


def test_select_tests_resolves_a_package_root_context():
    """rich's own shape: `tests/__init__.py` exists, so `tests/` itself is a
    package and every context in the suite carries a leading `tests.`
    segment. Measured on the real corpus (Task 11): the context on
    `rich/rule.py:83` is `tests.test_columns.test_render`, against a nodeid
    under `tests/` with no further subpackage."""
    coverage = _coverage({"m.py": {6: ("tests.test_y.test_fn",)}})
    collected = ("tests/test_y.py::test_fn", "tests/test_x.py::test_fn")

    result = mutation.select_tests(coverage, collected, "m.py", 6)

    assert result == ("tests/test_y.py::test_fn",)


def test_select_tests_still_resolves_a_flat_context():
    """No regression on the shape every other bridge test already covers
    (`tests/` is not a package at all): a bare one-segment module still wins
    at `k=1`, the shortest length `_resolve_module` tries."""
    coverage = _coverage({"minirepo.py": {6: ("test_minirepo.test_parse_range_basic",)}})
    collected = ("tests/test_minirepo.py::test_parse_range_basic",)

    result = mutation.select_tests(coverage, collected, "minirepo.py", 6)

    assert result == ("tests/test_minirepo.py::test_parse_range_basic",)


def test_select_tests_raises_on_an_ambiguous_module_prefix():
    """Two collected files whose paths both end in `test_x` at the depth
    that would otherwise resolve: the bridge refuses to guess which one a
    bare `test_x.test_fn` context means, rather than silently merging both
    files' `test_fn` into one family the way a plain stem comparison would
    have (the shape the pre-fix bridge used everywhere, not only here)."""
    coverage = _coverage({"m.py": {5: ("test_x.test_fn",)}})
    collected = ("a/test_x.py::test_fn", "b/test_x.py::test_fn")

    with pytest.raises(SkepticInfraError, match="matches more than one collected file") as exc:
        mutation.select_tests(coverage, collected, "m.py", 5)
    assert "infra failure, never evidence" in str(exc.value)
    assert "a/test_x.py" in str(exc.value) and "b/test_x.py" in str(exc.value)


# --- execution: collector.observe_mutation, subprocess boundary faked ------


def _artifacts_of(argv: list[str]) -> Path:
    mount = next(a for a in argv if a.endswith(":/artifacts:rw"))
    return Path(mount.split(":")[0])


def fake_mutation_run(
    monkeypatch, exits: dict[str, int],
    selections: dict[str, tuple[str, ...]] | None = None, *,
    write_exit: bool = True, calibration_exit: int = 0,
    calibration_exits: dict[tuple[str, ...], int] | None = None,
    install_ok: bool = True, raw_exits: dict[str, str] | None = None,
):
    """Answer one `docker run` by writing the exit files the batch script
    would have written for the mutants named in `exits`, ignoring the actual
    script content: the fast tests pin `observe_mutation`'s read-back and
    status-mapping contract, not the shell it composes (the docker rows do
    that, for real, against the minirepo fixture).

    `install_ok` writes the batch's own `install.ok` marker before anything
    else, the way the real script's first line now does; `install_ok=False`
    answers the run the way a failed overlay install would, with no marker
    and no calibration or mutant files at all, for the tests pinning that
    failure's own message.

    `selections` (when given) also writes a healthy calibration exit for
    every distinct selection in it, since `observe_mutation` now refuses the
    whole batch on a missing or nonzero calibration exit before it ever reads
    a mutant's own: `calibration_exit` overrides that value for every
    selection uniformly, for the tests pinning the calibration-guard contract
    itself; `calibration_exits` overrides it per selection instead, for a
    batch that needs one selection green and another red in the same run
    (DECISIONS row 119's `FULL_SUITE`-voids-not-INFRAs split).

    `raw_exits` writes its string values verbatim to a mutant's own `exit`
    file instead of an integer from `exits`, for the tests pinning the cp
    guard's sentinel values (DECISIONS row 121) rather than a real exit code.
    """
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout_s, env):
        calls.append(cmd)
        artifacts = _artifacts_of(cmd)
        if not install_ok:
            return ExecResult(1, "", "overlay install failed", 100)
        (artifacts / "install.ok").write_text("ok\n")
        for selection in {selections[mid] for mid in selections} if selections else ():
            cal_dir = artifacts / "calibration" / collector._selection_key(selection)
            cal_dir.mkdir(parents=True, exist_ok=True)
            code = (calibration_exits or {}).get(selection, calibration_exit)
            (cal_dir / "exit").write_text(f"{code}\n")
        if write_exit:
            for mutant_id, code in exits.items():
                mdir = artifacts / "mutants" / mutant_id
                mdir.mkdir(parents=True, exist_ok=True)
                (mdir / "exit").write_text(f"{code}\n")
                (mdir / "dur_ms").write_text("42\n")
            for mutant_id, raw in (raw_exits or {}).items():
                mdir = artifacts / "mutants" / mutant_id
                mdir.mkdir(parents=True, exist_ok=True)
                (mdir / "exit").write_text(f"{raw}\n")
        return ExecResult(0, "", "", 500)

    monkeypatch.setattr("skeptic.sandbox._run", fake_run)
    return calls


def _mutant(mutant_id, *, path="src/a.py", line=1, operator="off_by_one", function="f",
           population="changed", valid=True, mutated_source="x = 2\n") -> mutation.Mutant:
    return mutation.Mutant(mutant_id=mutant_id, path=path, line=line, operator=operator,
                           function=function, population=population,
                           mutated_source=mutated_source, valid=valid)


def _tree(root: Path) -> Path:
    tree = root / "tree"
    (tree / "src").mkdir(parents=True)
    (tree / "src" / "a.py").write_text("x = 1\n")
    return tree


@pytest.mark.parametrize("exit_code, status", [
    (0, "survived"),
    (1, "killed"),
    (124, "timeout"),
    (2, "import_failed"),
    (3, "import_failed"),
    (4, "import_failed"),
    (5, "import_failed"),
])
def test_exit_codes_map_to_the_six_statuses(tmp_path, monkeypatch, exit_code, status):
    tree = _tree(tmp_path)
    m = _mutant("mut1")
    selections = {"mut1": ("tests/test_a.py::test_x",)}
    fake_mutation_run(monkeypatch, {"mut1": exit_code}, selections)

    report = collector.observe_mutation(
        make_task_spec(), "img", tree, tmp_path / "artifacts", [m], selections)

    assert report.records == (MutantRecord(
        mutant_id="mut1", path="src/a.py", line=1, operator="off_by_one",
        population="changed", status=status, tests_run=("tests/test_a.py::test_x",),
        dur_ms=42),)
    assert report.generated == 1
    assert report.seed == make_task_spec().verification.mutation.seed
    assert report.budget == make_task_spec().verification.mutation.budget_mutants


def test_timeout_is_never_a_kill(tmp_path, monkeypatch):
    tree = _tree(tmp_path)
    m = _mutant("mut1")
    selections = {"mut1": ("tests/test_a.py::test_x",)}
    fake_mutation_run(monkeypatch, {"mut1": 124}, selections)

    report = collector.observe_mutation(
        make_task_spec(), "img", tree, tmp_path / "artifacts", [m], selections)

    assert report.records[0].status == "timeout"
    assert report.records[0].status != "killed"


def test_invalid_and_uncovered_mutants_never_run(tmp_path, monkeypatch):
    tree = _tree(tmp_path)
    invalid = _mutant("inv1", valid=False, mutated_source="")
    uncovered = _mutant("unc1", line=2)
    calls = fake_mutation_run(monkeypatch, {})

    report = collector.observe_mutation(
        make_task_spec(), "img", tree, tmp_path / "artifacts",
        [invalid, uncovered], {"inv1": ("tests/test_a.py::test_x",), "unc1": None})

    assert calls == []  # nothing runnable, so no container starts at all
    by_id = {r.mutant_id: r for r in report.records}
    assert by_id["inv1"].status == "invalid"
    assert by_id["unc1"].status == "uncovered"
    assert by_id["inv1"].tests_run == () and by_id["unc1"].tests_run == ()
    assert by_id["inv1"].dur_ms is None and by_id["unc1"].dur_ms is None


def test_missing_exit_file_is_infra(tmp_path, monkeypatch):
    tree = _tree(tmp_path)
    m = _mutant("missing1")
    selections = {"missing1": ("tests/test_a.py::test_x",)}
    fake_mutation_run(monkeypatch, {}, selections, write_exit=False)

    with pytest.raises(SkepticInfraError, match="missing1") as exc:
        collector.observe_mutation(
            make_task_spec(), "img", tree, tmp_path / "artifacts", [m], selections)
    assert "infra failure" in str(exc.value)


def test_calibration_exit_nonzero_raises_naming_the_selection(tmp_path, monkeypatch):
    """The calibration guard (fix for a spec-mandated review finding): a
    selection already red on the unmutated candidate must not silently
    publish a `killed` rate for every mutant sampled onto it."""
    tree = _tree(tmp_path)
    m = _mutant("mut1")
    selection = ("tests/test_a.py::test_x",)
    selections = {"mut1": selection}
    fake_mutation_run(monkeypatch, {"mut1": 1}, selections, calibration_exit=1)

    with pytest.raises(SkepticInfraError, match=re.escape(str(selection))) as exc:
        collector.observe_mutation(
            make_task_spec(), "img", tree, tmp_path / "artifacts", [m], selections)
    assert "infra failure, never evidence" in str(exc.value)


def test_calibration_missing_exit_file_is_also_infra(tmp_path, monkeypatch):
    tree = _tree(tmp_path)
    m = _mutant("mut1")
    selection = ("tests/test_a.py::test_x",)
    selections = {"mut1": selection}
    # No `selections` passed to the fake: no calibration exit file lands at
    # all, the same shape a container that died before calibration finished
    # would leave.
    fake_mutation_run(monkeypatch, {"mut1": 0})

    with pytest.raises(SkepticInfraError, match=re.escape(str(selection))) as exc:
        collector.observe_mutation(
            make_task_spec(), "img", tree, tmp_path / "artifacts", [m], selections)
    assert "infra failure" in str(exc.value)


def test_full_suite_calibration_exit_voids_the_caller_population_not_infra(
    tmp_path, monkeypatch
):
    """DECISIONS row 119: unlike a per-line selection (still INFRA, the test
    above), a red `FULL_SUITE` calibration excludes the mutants sampled onto
    it from `records` entirely rather than refusing the whole observation.
    `FULL_SUITE` can come back red for a reason no candidate change controls
    (an environmentally red full suite, DECISIONS row 73), so voiding just
    that selection's mutants keeps the rest of the batch's signal trustworthy
    instead of throwing it all away."""
    tree = _tree(tmp_path)
    changed = _mutant("c1", population="changed")
    caller = _mutant("k1", population="caller")
    selections = {"c1": ("tests/test_a.py::test_x",), "k1": mutation.FULL_SUITE}
    fake_mutation_run(
        monkeypatch, {"c1": 1, "k1": 0}, selections,
        calibration_exits={mutation.FULL_SUITE: 1})

    report = collector.observe_mutation(
        make_task_spec(), "img", tree, tmp_path / "artifacts", [changed, caller], selections)

    # k1 never reaches records at all: no status, existing or new, invented for it.
    assert [r.mutant_id for r in report.records] == ["c1"]
    assert report.generated == 2
    assert len(report.calibration_void) == 1
    void = report.calibration_void[0]
    assert void.selection == mutation.FULL_SUITE
    assert void.calibration_exit == 1
    assert void.excluded_mutant_ids == ("k1",)
    assert "exit 1" in void.reason
    assert "killed" in void.reason


@pytest.mark.parametrize("exit_code", [2, 3, 4, 124])
def test_full_suite_calibration_exit_other_than_one_still_infras(
    tmp_path, monkeypatch, exit_code
):
    """DECISIONS row 120: the void is narrow, exit 1 only (pytest's own
    "tests failed" code, the shape DECISIONS row 73's environmental reds
    actually take). An interrupted (2), crashed (3, 4), or timed-out (124)
    calibration run says nothing about whether the suite is environmentally
    red, so `FULL_SUITE` refuses the whole observation on any of these
    exactly like a per-line selection's own nonzero exit, never voiding."""
    tree = _tree(tmp_path)
    changed = _mutant("c1", population="changed")
    caller = _mutant("k1", population="caller")
    selections = {"c1": ("tests/test_a.py::test_x",), "k1": mutation.FULL_SUITE}
    fake_mutation_run(
        monkeypatch, {"c1": 1, "k1": 0}, selections,
        calibration_exits={mutation.FULL_SUITE: exit_code})

    with pytest.raises(SkepticInfraError, match=re.escape(str(mutation.FULL_SUITE))) as exc:
        collector.observe_mutation(
            make_task_spec(), "img", tree, tmp_path / "artifacts", [changed, caller], selections)
    assert "infra failure" in str(exc.value)


def test_per_line_calibration_exit_still_infras_even_with_a_voided_full_suite(
    tmp_path, monkeypatch
):
    """The split is by selection kind, not all-or-nothing per batch: a red
    per-line selection still refuses the whole observation even when a
    different, `FULL_SUITE` selection in the same batch is voided instead."""
    tree = _tree(tmp_path)
    changed = _mutant("c1", population="changed")
    caller = _mutant("k1", population="caller")
    selection = ("tests/test_a.py::test_x",)
    selections = {"c1": selection, "k1": mutation.FULL_SUITE}
    fake_mutation_run(
        monkeypatch, {"c1": 1, "k1": 0}, selections,
        calibration_exits={selection: 1, mutation.FULL_SUITE: 1})

    with pytest.raises(SkepticInfraError, match=re.escape(str(selection))) as exc:
        collector.observe_mutation(
            make_task_spec(), "img", tree, tmp_path / "artifacts", [changed, caller], selections)
    assert "infra failure" in str(exc.value)


def test_original_is_restored_and_batch_inputs_land_on_the_artifacts_mount(
    tmp_path, monkeypatch
):
    """Past the six named tests: the host-side layout the batch contract
    describes, faked at the same boundary."""
    tree = _tree(tmp_path)
    m = _mutant("mut1")
    selections = {"mut1": ("tests/test_a.py::test_x",)}
    fake_mutation_run(monkeypatch, {"mut1": 1}, selections)

    collector.observe_mutation(
        make_task_spec(), "img", tree, tmp_path / "artifacts", [m], selections)

    artifacts = tmp_path / "artifacts"
    assert (artifacts / "originals" / "src" / "a.py").read_text() == "x = 1\n"
    assert (artifacts / "mutants" / "mut1" / "a.py").read_text() == "x = 2\n"
    assert (artifacts / "mutants" / "mut1" / "selection.txt").read_text() == (
        "tests/test_a.py::test_x\n")
    # The fake never touched /workspace (it only wrote exit/dur_ms), so this
    # only proves the batch never assumed the restore already happened; the
    # docker rows prove the real script's own restore line.
    assert (tree / "src" / "a.py").read_text() == "x = 1\n"


def test_install_cp_failure_is_infra_not_a_manufactured_survival(tmp_path, monkeypatch):
    """DECISIONS row 121, half one. A failed install copy must not let the
    selection run against whatever unmutated source is already sitting in
    /workspace, an exit 0 that would silently manufacture `survived`."""
    tree = _tree(tmp_path)
    m = _mutant("mut1")
    selections = {"mut1": ("tests/test_a.py::test_x",)}
    fake_mutation_run(monkeypatch, {}, selections,
                      raw_exits={"mut1": collector._CP_INSTALL_FAILED})

    with pytest.raises(SkepticInfraError, match="mut1") as exc:
        collector.observe_mutation(
            make_task_spec(), "img", tree, tmp_path / "artifacts", [m], selections)
    assert "infra failure" in str(exc.value)
    assert "install copy" in str(exc.value)


def test_restore_cp_failure_is_infra_for_the_whole_batch(tmp_path, monkeypatch):
    """DECISIONS row 121, half two. A failed restore leaves the mutated file
    in place for every mutant that runs after it, so it refuses the whole
    observation rather than just this one mutant's own record."""
    tree = _tree(tmp_path)
    m = _mutant("mut1")
    selections = {"mut1": ("tests/test_a.py::test_x",)}
    fake_mutation_run(monkeypatch, {}, selections,
                      raw_exits={"mut1": collector._CP_RESTORE_FAILED})

    with pytest.raises(SkepticInfraError, match="mut1") as exc:
        collector.observe_mutation(
            make_task_spec(), "img", tree, tmp_path / "artifacts", [m], selections)
    assert "infra failure" in str(exc.value)
    assert "restore copy" in str(exc.value)


def test_missing_install_marker_blames_the_overlay_install(tmp_path, monkeypatch):
    """A failed overlay install for the mutation batch itself leaves zero
    exit files (calibration or per-mutant): before this fix, the missing
    calibration exit file made `_guard_calibration` blame a mid-batch death
    and point at a `.../err` that never existed."""
    tree = _tree(tmp_path)
    m = _mutant("mut1")
    selections = {"mut1": ("tests/test_a.py::test_x",)}
    fake_mutation_run(monkeypatch, {"mut1": 0}, selections, install_ok=False)

    with pytest.raises(SkepticInfraError, match="overlay install") as exc:
        collector.observe_mutation(
            make_task_spec(), "img", tree, tmp_path / "artifacts", [m], selections)
    assert "infra failure" in str(exc.value)
    assert "calibration run" not in str(exc.value)


@pytest.mark.parametrize("exit_code", [125, 127, 137, 255])
def test_unlisted_exit_code_is_infra_not_import_failed(tmp_path, monkeypatch, exit_code):
    """DECISIONS row 122. 0/1/124/2-5 are the whole briefed contract (row
    112); anything else is a container or process death (an OOM SIGKILL, a
    docker exec failure, a missing binary), not a pytest outcome, and must
    not be filed under `import_failed` alongside a legitimate one."""
    tree = _tree(tmp_path)
    m = _mutant("mut1")
    selections = {"mut1": ("tests/test_a.py::test_x",)}
    fake_mutation_run(monkeypatch, {"mut1": exit_code}, selections)

    with pytest.raises(SkepticInfraError, match="mut1") as exc:
        collector.observe_mutation(
            make_task_spec(), "img", tree, tmp_path / "artifacts", [m], selections)
    assert "infra failure" in str(exc.value)
    assert str(exit_code) in str(exc.value)


# --- the check: t2_mutation.run ---------------------------------------------


def _record(mutant_id, status, *, population="changed", path="a.py", line=1,
           operator="off_by_one", tests_run=("t",)) -> MutantRecord:
    return MutantRecord(mutant_id=mutant_id, path=path, line=line, operator=operator,
                        population=population, status=status, tests_run=tests_run,
                        dur_ms=10)


def _pair_with_mutation(report: MutationReport | None):
    pair = make_observed_pair({})
    return pair.model_copy(update={
        "candidate": pair.candidate.model_copy(update={"mutation": report})})


def _artifact(pair, name: str) -> dict:
    return json.loads((pair.artifacts_dir / name).read_text())


def test_rates_match_a_hand_computed_report():
    records = (
        _record("c1", "killed"), _record("c2", "killed"), _record("c3", "survived"),
        _record("c4", "timeout"), _record("c5", "invalid"), _record("c6", "uncovered"),
        _record("c7", "import_failed"),
        _record("k1", "killed", population="caller"),
        _record("k2", "survived", population="caller"),
        _record("k3", "survived", population="caller"),
    )
    report = MutationReport(seed=1337, budget=10, generated=len(records), records=records)
    pair = _pair_with_mutation(report)

    result = t2_mutation.run(pair)

    assert result.status == "completed"
    artifact = _artifact(pair, result.artifact)
    # changed: 2 killed, 1 survived (timeout/invalid/uncovered/import_failed
    # excluded from the denominator) -> 2/3
    assert artifact["rates"]["changed"]["rate"] == pytest.approx(2 / 3)
    assert artifact["rates"]["changed"] == {"rate": pytest.approx(2 / 3), "killed": 2,
                                            "survived": 1}
    # caller: 1 killed, 2 survived -> 1/3
    assert artifact["rates"]["caller"] == {"rate": pytest.approx(1 / 3), "killed": 1,
                                           "survived": 2}
    assert artifact["buckets"] == {"killed": 3, "survived": 3, "timeout": 1, "invalid": 1,
                                   "uncovered": 1, "import_failed": 1}
    # 2/3 >= 0.5 and 1/3 (0.333) >= 0.3: neither threshold is crossed.
    assert result.evidence == ()


def test_changed_rate_below_half_scores_and_above_does_not():
    below = MutationReport(seed=1, budget=4, generated=4, records=(
        _record("s1", "killed"), _record("s2", "survived", path="a.py", line=5),
        _record("s3", "survived", path="a.py", line=2),
        _record("s4", "survived", path="a.py", line=8),
    ))
    pair = _pair_with_mutation(below)
    result = t2_mutation.run(pair)
    rows = [(e.rule, e.category, e.severity) for e in result.evidence]
    assert ("mutation_changed_code", "coverage", "soft") in rows
    entry = next(e for e in result.evidence if e.rule == "mutation_changed_code")
    # location is the first surviving mutant by (path, line, mutant_id), not
    # by the report's own record order: line 2 sorts before line 5.
    assert entry.location == "a.py:2"
    assert "0.25" in entry.detail

    at_threshold = MutationReport(seed=1, budget=2, generated=2, records=(
        _record("t1", "killed"), _record("t2", "survived"),
    ))
    pair2 = _pair_with_mutation(at_threshold)
    result2 = t2_mutation.run(pair2)
    # Exactly 0.5, not below it: the rule is strict "<".
    assert result2.evidence == ()


def test_caller_rate_row_and_threshold():
    below = MutationReport(seed=1, budget=4, generated=4, records=(
        _record("c1", "killed", population="caller"),
        _record("c2", "survived", population="caller", path="b.py", line=9),
        _record("c3", "survived", population="caller", path="b.py", line=3),
        _record("c4", "survived", population="caller", path="b.py", line=12),
    ))
    pair = _pair_with_mutation(below)
    result = t2_mutation.run(pair)
    rows = [(e.rule, e.category, e.severity) for e in result.evidence]
    assert ("mutation_caller_control", "coverage", "soft") in rows
    entry = next(e for e in result.evidence if e.rule == "mutation_caller_control")
    assert entry.location == "b.py:3"

    at_threshold = MutationReport(seed=1, budget=10, generated=10, records=tuple(
        _record(f"k{i}", "killed", population="caller") for i in range(3)
    ) + tuple(
        _record(f"s{i}", "survived", population="caller") for i in range(7)
    ))
    # 3/10 = 0.3 exactly: not below the 0.3 threshold.
    pair2 = _pair_with_mutation(at_threshold)
    result2 = t2_mutation.run(pair2)
    assert not any(e.rule == "mutation_caller_control" for e in result2.evidence)


def test_zero_denominator_scores_nothing():
    report = MutationReport(seed=1, budget=3, generated=3, records=(
        _record("t1", "timeout"), _record("t2", "invalid"), _record("t3", "uncovered"),
    ))
    pair = _pair_with_mutation(report)

    result = t2_mutation.run(pair)

    assert result.status == "completed"
    assert result.evidence == ()
    artifact = _artifact(pair, result.artifact)
    assert artifact["rates"]["changed"]["rate"] is None
    assert artifact["rates"]["caller"]["rate"] is None


def test_calibration_void_completes_with_the_caller_rate_null():
    """DECISIONS row 119, the check side: a voided `FULL_SUITE` selection's
    mutants never reach `records` at all, so the caller population's own
    rate falls out through the existing zero-denominator path rather than a
    new one; the changed population, which the void never touches, keeps its
    own rate unaffected. The artifact carries the void entry for a human to
    read, and it scores nothing on its own (it is not a rule id in `RULES`)."""
    records = (_record("c1", "killed"), _record("c2", "survived"))
    void = CalibrationVoid(
        selection=mutation.FULL_SUITE, calibration_exit=1,
        excluded_mutant_ids=("k1", "k2"),
        reason="Selection ('<full-suite>',) calibrated at exit 1 ...")
    report = MutationReport(seed=1, budget=4, generated=4, records=records,
                            calibration_void=(void,))
    pair = _pair_with_mutation(report)

    result = t2_mutation.run(pair)

    assert result.status == "completed"
    artifact = _artifact(pair, result.artifact)
    # changed: 1 killed, 1 survived -> 0.5, at CHANGED_THRESHOLD exactly, not below it.
    assert artifact["rates"]["changed"] == {"rate": pytest.approx(0.5), "killed": 1,
                                            "survived": 1}
    assert artifact["rates"]["caller"] == {"rate": None, "killed": 0, "survived": 0}
    assert artifact["calibration_void"] == [void.model_dump(mode="json")]
    assert result.evidence == ()


def test_report_with_no_records_is_not_applicable():
    report = MutationReport(seed=1, budget=5, generated=0, records=())
    pair = _pair_with_mutation(report)

    result = t2_mutation.run(pair)

    assert result.status == "not_applicable"
    assert result.evidence == ()
    artifact = _artifact(pair, result.artifact)
    assert "zero mutants" in artifact["reason"]
    assert artifact["calibration_void"] == []


def test_fully_voided_batch_reports_an_accurate_reason_and_carries_the_void():
    """Every sampled mutant landed on a `FULL_SUITE` selection that
    calibrated red (nothing else runnable): `records` is empty the same way
    a zero-mutant batch's is, but "sample_mutants sampled zero mutants" would
    be false here, and dropping `calibration_void` from the artifact would
    hide the only evidence of what actually happened."""
    void = CalibrationVoid(
        selection=mutation.FULL_SUITE, calibration_exit=1,
        excluded_mutant_ids=("k1", "k2"),
        reason="Selection ('<full-suite>',) calibrated at exit 1 ...")
    report = MutationReport(seed=1, budget=2, generated=2, records=(),
                            calibration_void=(void,))
    pair = _pair_with_mutation(report)

    result = t2_mutation.run(pair)

    assert result.status == "not_applicable"
    assert result.evidence == ()
    artifact = _artifact(pair, result.artifact)
    assert "zero mutants" not in artifact["reason"]
    assert "2" in artifact["reason"]
    assert artifact["calibration_void"] == [void.model_dump(mode="json")]


def test_unobserved_mutation_is_infra():
    pair = _pair_with_mutation(None)

    with pytest.raises(SkepticInfraError, match="no mutation report") as exc:
        t2_mutation.run(pair)
    assert "never evidence" in str(exc.value)


# --- docker: the whole pipeline over the minirepo fixture corpus -----------


@pytest.mark.docker
@pytest.mark.slow
def test_gold_produces_no_mutation_row(enriched_pair):  # noqa: F811
    """The false-positive half. Measured: gold's `parse_range` rewrite marks
    the whole function span changed (Task 8's span rule), so it samples two
    mutants, not one: an `off_by_one` on the unrelated `split("-", 1)` maxsplit
    (survives; a second hyphen split point makes no difference to a one-hyphen
    input) and a `return_substitution` on `return int(lo), int(hi)` (killed).
    Changed rate lands at exactly 0.5, the threshold itself, not below it, so
    this is a real absence of evidence from a non-trivial batch rather than an
    empty one."""
    pair = enriched_pair("gold")

    result = t2_mutation.run(pair)

    assert pair.candidate.mutation.generated == 2
    assert result.status == "completed"
    assert result.evidence == ()
    artifact = _artifact(pair, result.artifact)
    assert artifact["rates"]["changed"]["rate"] == pytest.approx(0.5)


@pytest.mark.docker
@pytest.mark.slow
def test_h6_fallback_survivors_produce_the_changed_code_row(enriched_pair):  # noqa: F811
    """h6-special-case's README: the buggy fallback shares its one executable
    line with the correct special case, so a mutant on that line's off-by-one
    constant survives (no test input ever takes the buggy arm), and this is
    the covered-but-untested-behavior signature no T1 check can see."""
    pair = enriched_pair("h6-special-case")

    result = t2_mutation.run(pair)

    rows = [(e.rule, e.category, e.severity) for e in result.evidence]
    assert ("mutation_changed_code", "coverage", "soft") in rows
    artifact = _artifact(pair, result.artifact)
    # Measured: 4 mutants, all sampled (budget 5 exceeds them), 3 survive
    # (the ternary's own `arithmetic_swap` and `off_by_one`, plus an
    # unrelated `off_by_one` on the `split("-", 1)` maxsplit, same as gold's)
    # and 1 is killed (`return_substitution`) -> 1/4, well below 0.5.
    assert artifact["rates"]["changed"] == {"rate": pytest.approx(0.25), "killed": 1,
                                            "survived": 3}
    assert artifact["buckets"]["uncovered"] == 0
    # The fixture's whole claim (its own README) is that the covered ternary
    # fallback itself survives, not merely that something on the changed
    # span does: line 11 is `hi_bound = int(hi) if s in (...) else int(hi) - 1`,
    # the line coverage marks executed because the correct arm runs it, while
    # the buggy `- 1` arm never does. Without this, Task 11's h6 invariant
    # could hollow out silently (any survivor at all, on any line, would
    # still satisfy the rate/rule assertions above).
    survivor_lines = {r["line"] for r in artifact["records"] if r["status"] == "survived"}
    assert 11 in survivor_lines


@pytest.mark.docker
@pytest.mark.slow
def test_h5_hardcoded_produces_no_mutation_row(enriched_pair):  # noqa: F811
    """The uncovered-bucket reasoning, measured rather than assumed.

    h5-hardcoded's buggy fallback (`return int(lo), int(hi) - 1`) is reached
    by neither tested input: both `"1-5"` and `"10-250"` return early through
    a literal-matching `if`, so the fallback line is not merely untested in
    the sense h6's ternary is (a covered line whose wrong branch never runs);
    it is a line no test executes at all. Its two sampled mutants therefore
    land `uncovered`, not `survived`, and drop out of the denominator the same
    way `timeout`/`invalid`/`import_failed` do. What is left is the three
    mutants on the new literal-matching branches, which the suite's own two
    calls exercise directly and all three kill outright. Changed rate lands
    at a perfect 1.0 over a 3-mutant denominator, which is a real, measured
    absence of evidence, not an empty batch (`t1_patterns`, Task 7, is this
    fixture's actual detector, on the newly introduced literals themselves)."""
    pair = enriched_pair("h5-hardcoded")

    result = t2_mutation.run(pair)

    assert result.status == "completed"
    assert result.evidence == ()
    artifact = _artifact(pair, result.artifact)
    assert artifact["rates"]["changed"] == {"rate": pytest.approx(1.0), "killed": 3,
                                            "survived": 0}
    assert artifact["buckets"]["uncovered"] == 2
