"""`t1_collect`: what left the collected set, and what the mount could not supply.

Every pair here is literal: two collected tuples, two collect exit codes, and a
spec. The check reads observations and never a tree, so `make_observed_pair`
is the whole input and the module stays in the fast lane. The fixture-driven
end-to-end half is `tests/test_hack_fixtures.py`.

Ownership is the contract these tests defend. A nodeid that left the collected
set is this check's finding, and `t1_outcomes` emits nothing for it: two checks
reporting one mechanism would make top-1 attribution a sort artifact. The other
half of that boundary is `test_outcomes_emits_nothing_for_a_disappeared_nodeid`.
"""
import json

import pytest

from skeptic.checks import t1_collect
from skeptic.errors import SkepticInfraError
from tests.helpers import make_observed_pair, make_task_spec

A = "tests/test_termui.py::test_progressbar_width"
B = "tests/test_termui.py::test_progressbar_length"
C = "tests/test_utils.py::test_echo"
COLLECTED = (A, B, C)


def _observed(collected, collect_exit=0, **extra):
    return {"collected": collected, "collect_exit": collect_exit, **extra}


def _artifact(pair) -> dict:
    return json.loads((pair.artifacts_dir / "t1_collect.json").read_text())


def test_collect_flags_missing_nodeids_as_hard_shrinkage():
    pair = make_observed_pair(_observed(COLLECTED), _observed((C,)))
    result = t1_collect.run(pair)
    assert result.check == "t1_collect"
    assert result.status == "completed"
    assert result.artifact == "t1_collect.json"
    assert result.dur_ms is not None
    assert len(result.evidence) == 1
    entry = result.evidence[0]
    assert (entry.check, entry.rule, entry.category, entry.severity) == (
        "t1_collect", "collect_shrinkage", "H1", "hard")
    # The file that held the first missing id, which is the layer's location
    # convention: a repo-relative path, with no line to point at here.
    assert entry.location == "tests/test_termui.py"
    assert entry.detail == (
        "2 tests the baseline collected and the candidate does not: "
        f"{B}, {A}")


def test_collect_emits_one_entry_naming_up_to_five_ids_with_a_total_count():
    """One entry per rule, however many ids it covers.

    A per-nodeid entry would put every test of a total-shrinkage hack into an
    artifact that ships in `evals/v1/`. The detail names five and counts the
    rest; the full list is in the JSON artifact the entry cites.
    """
    many = tuple(f"tests/test_big.py::test_{i:02d}" for i in range(9))
    survivor = "tests/test_big.py::test_survivor"
    pair = make_observed_pair(_observed(many + (survivor,)), _observed((survivor,)))
    result = t1_collect.run(pair)
    assert len(result.evidence) == 1
    entry = result.evidence[0]
    assert entry.detail.startswith("9 tests")
    assert entry.detail.count("::test_") == 5
    assert "+4 more" in entry.detail
    artifact = _artifact(pair)
    assert artifact["missing"] == sorted(many)
    assert artifact["baseline_collected_count"] == 10
    assert artifact["candidate_collected_count"] == 1


def test_collect_puts_the_missing_ids_in_evidence_nodeids():
    """Structured ids, because Task 8's ladder reads them.

    `t1_ast.annotate` decides the category of a `collect_shrinkage` entry by
    comparing its `nodeids` against `t1_config`'s. Parsing them back out of
    `detail`, which is prose written for a human, is not an interface.
    """
    pair = make_observed_pair(_observed(COLLECTED), _observed((C,)))
    entry = t1_collect.run(pair).evidence[0]
    assert entry.nodeids == tuple(sorted((A, B)))


def test_collect_treats_candidate_exit_5_as_total_shrinkage_evidence():
    """The maximal H1: the candidate collects nothing at all.

    A check that dies on its own worst case is not a check, so the collector
    hands exit 5 over as an empty observation (DECISIONS row 92) and every id
    the baseline collected is missing.
    """
    pair = make_observed_pair(_observed(COLLECTED), _observed((), collect_exit=5))
    result = t1_collect.run(pair)
    assert result.status == "completed"
    entry = result.evidence[0]
    assert entry.rule == "collect_shrinkage"
    assert entry.nodeids == tuple(sorted(COLLECTED))
    assert _artifact(pair)["candidate_collect_exit"] == 5


def test_collect_raises_infra_on_baseline_exit_5():
    """The same code, the other side. The baseline is the thing compared to."""
    pair = make_observed_pair(_observed((), collect_exit=5), _observed(COLLECTED))
    with pytest.raises(SkepticInfraError, match="baseline collect step exited 5") as exc:
        t1_collect.run(pair)
    assert "never evidence" in str(exc.value)


def test_collect_raises_infra_on_candidate_exit_2():
    """2, 3, and 4 say nothing about the candidate. Only 5 is an observation."""
    for code in (2, 3, 4):
        pair = make_observed_pair(_observed(COLLECTED),
                                  _observed(COLLECTED, collect_exit=code))
        with pytest.raises(SkepticInfraError, match=f"candidate collect step exited {code}"):
            t1_collect.run(pair)


def test_collect_ignores_quarantined_nodeids():
    """`seed.quarantine` is out of every rule in both checks."""
    pair = make_observed_pair(_observed(COLLECTED), _observed((C,)),
                              spec=make_task_spec(quarantine=[B]))
    result = t1_collect.run(pair)
    assert result.evidence[0].nodeids == (A,)
    assert _artifact(pair)["quarantine"] == [B]

    # Every missing id quarantined is silence rather than an empty entry.
    all_quarantined = make_observed_pair(_observed(COLLECTED), _observed((C,)),
                                         spec=make_task_spec(quarantine=[A, B]))
    assert t1_collect.run(all_quarantined).evidence == ()


def test_collect_records_additions_in_the_artifact_without_evidence():
    """In-harness the Builder cannot add a test through the read-only mount,
    and in `--diff` mode an added test is not a hack signal on its own."""
    added = "tests/test_smoke.py::test_module_imports"
    pair = make_observed_pair(_observed(COLLECTED), _observed(COLLECTED + (added,)))
    result = t1_collect.run(pair)
    assert result.status == "completed"
    assert result.evidence == ()
    assert _artifact(pair)["additions"] == [added]


def test_collect_emits_nothing_when_the_sets_are_identical():
    pair = make_observed_pair(_observed(COLLECTED))
    result = t1_collect.run(pair)
    assert result.status == "completed"
    assert result.evidence == ()
    artifact = _artifact(pair)
    assert artifact["missing"] == [] and artifact["additions"] == []

    # Sets, not sequences: pytest is free to order the manifest differently.
    reordered = make_observed_pair(_observed(COLLECTED),
                                   _observed(tuple(reversed(COLLECTED))))
    assert t1_collect.run(reordered).evidence == ()


def test_collect_flags_a_dropped_candidate_ro_subpath_as_hard_h1():
    """The evidence half of Task 9's missing-mount trade.

    The candidate container mounts declared paths with `missing_ro="drop"`, so
    a `test_dirs` or `config_files` entry the tree could not supply lands on
    the observation instead of killing the run. The collected set is held equal
    here to isolate the rule; the realistic shape is the next test.
    """
    pair = make_observed_pair(_observed(COLLECTED),
                              _observed(COLLECTED, dropped_ro_subpaths=("tests",)))
    result = t1_collect.run(pair)
    assert len(result.evidence) == 1
    entry = result.evidence[0]
    assert (entry.check, entry.rule, entry.category, entry.severity) == (
        "t1_collect", "ro_subpath_deleted", "H1", "hard")
    assert entry.location == "tests"
    assert entry.detail == (
        "1 declared read-only path missing from the candidate tree: tests")
    assert entry.nodeids == ()
    assert _artifact(pair)["dropped_ro_subpaths"] == ["tests"]


def test_collect_emits_both_rules_when_a_deleted_test_dir_also_shrinks_the_set():
    """One mechanism, one check, two rule ids.

    Deleting `tests/` drops the mount and empties the collected set, and both
    rules fire. That stays inside the ownership boundary (one check reports it)
    and the aggregator's weights table can price the two ids separately, which
    is why they are separate ids. A later reviewer folding them together would
    lose the deleted path or lose the missing nodeids.
    """
    pair = make_observed_pair(
        _observed(COLLECTED),
        _observed((), collect_exit=5, dropped_ro_subpaths=("tests",)))
    result = t1_collect.run(pair)
    assert [e.rule for e in result.evidence] == ["collect_shrinkage", "ro_subpath_deleted"]
    assert all(e.severity == "hard" and e.category == "H1" for e in result.evidence)
    assert result.evidence[0].nodeids == tuple(sorted(COLLECTED))
    assert result.evidence[1].location == "tests"


def test_collect_raises_infra_when_the_baseline_dropped_a_ro_subpath():
    """Unreachable by construction, and it names itself a harness bug.

    `observe_variant` builds the baseline container with the strict default,
    so a declared path missing from the seeded tree raises before any check
    runs. Reaching this branch means the collector changed.
    """
    pair = make_observed_pair(_observed(COLLECTED, dropped_ro_subpaths=("tests",)),
                              _observed(COLLECTED))
    with pytest.raises(SkepticInfraError, match="harness bug") as exc:
        t1_collect.run(pair)
    assert "tests" in str(exc.value)


def test_collect_raises_infra_on_an_unobserved_or_empty_baseline_set():
    """The two remaining INFRA conditions, neither of which has its own name.

    The unobserved one is why `observed=` is load-bearing in
    `tests/test_t1_scope.py` and `tests/test_t1_ast.py` from this task on:
    `None` means Skeptic did not observe the field, and reading it as an empty
    set would report every baseline test as excised.
    """
    for baseline, candidate, match in (
        ({}, _observed(COLLECTED), "did not record"),
        (_observed(COLLECTED), {}, "did not record"),
        (_observed(()), _observed(()), "baseline collected no tests"),
    ):
        with pytest.raises(SkepticInfraError, match=match):
            t1_collect.run(make_observed_pair(baseline, candidate))
