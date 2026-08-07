"""The check: t2_advtests.run over hand-built adversarial reports.

No driver or bridge layer here, unlike `t2_mutation`/`t2_probe`: Task 6
already built the ladder and `collector.observe_advtests`, so this module's
whole job is the pure fold over an `AdversarialReport` the enrichment step
already produced. Reports are built by hand, the same shape
`tests/test_t2_probe.py` uses for `ProbeReport`. No docker mark: nothing here
runs a container.
"""
from __future__ import annotations

import json

import pytest

from skeptic.checks import aggregate, t2_advtests
from skeptic.checks.observations import (
    AdvCandidate,
    AdvDivergence,
    AdversarialReport,
    ObservationPair,
)
from skeptic.errors import SkepticInfraError
from tests.helpers import make_observed_pair


def _candidate(candidate_id, status, *, rejected_at=None, detail="ok") -> AdvCandidate:
    return AdvCandidate(candidate_id=candidate_id, source=f"# {candidate_id}\n",
                        status=status, rejected_at=rejected_at, detail=detail)


def _pair_with_advtests(report: AdversarialReport | None) -> ObservationPair:
    pair = make_observed_pair({})
    return pair.model_copy(update={
        "candidate": pair.candidate.model_copy(update={"advtests": report})})


def _artifact(pair, name: str) -> dict:
    return json.loads((pair.artifacts_dir / name).read_text())


def test_missing_report_is_infra_with_the_sibling_message():
    pair = _pair_with_advtests(None)

    with pytest.raises(SkepticInfraError, match="no adversarial-test report") as exc:
        t2_advtests.run(pair)
    assert "harness bug, never evidence" in str(exc.value)
    assert "not the same claim" in str(exc.value)
    assert "Next:" in str(exc.value)


def test_divergence_emits_one_soft_h6_row_at_weight_bearing_rule():
    report = AdversarialReport(
        model="haiku", n_candidates=1,
        candidates=(_candidate("c1", "trusted"),),
        trusted=("c1",),
        divergences=(AdvDivergence(candidate_id="c1",
                                   nodeids=("tests/test_x.py::test_one",)),),
    )
    pair = _pair_with_advtests(report)

    result = t2_advtests.run(pair)

    assert result.status == "completed"
    rows = [(e.rule, e.category, e.severity) for e in result.evidence]
    assert rows == [("advtest_divergence", "H6", "soft")]
    entry = result.evidence[0]
    assert entry.location is None
    assert entry.nodeids == ("tests/test_x.py::test_one",)
    assert "c1" in entry.detail
    assert entry.rule in aggregate.WEIGHTS
    artifact = _artifact(pair, result.artifact)
    assert artifact["report"]["model"] == "haiku"


def test_multiple_divergences_still_one_row():
    report = AdversarialReport(
        model="haiku", n_candidates=2,
        candidates=(_candidate("c1", "trusted"), _candidate("c2", "trusted")),
        trusted=("c1", "c2"),
        divergences=(
            AdvDivergence(candidate_id="c1", nodeids=("tests/test_x.py::test_one",)),
            AdvDivergence(candidate_id="c2", nodeids=("tests/test_y.py::test_two",)),
        ),
    )
    pair = _pair_with_advtests(report)

    result = t2_advtests.run(pair)

    assert result.status == "completed"
    assert len(result.evidence) == 1
    entry = result.evidence[0]
    assert entry.rule == "advtest_divergence"
    # nodeids on the row come from the first divergence only, not a merge of
    # every diverging candidate's nodeids.
    assert entry.nodeids == ("tests/test_x.py::test_one",)
    assert "c1" in entry.detail
    assert "c2" in entry.detail


def test_zero_trusted_emits_info_row_with_yield_stat():
    report = AdversarialReport(
        model="haiku", n_candidates=4,
        candidates=(
            _candidate("c1", "rejected", rejected_at="reference", detail="disagreed"),
            _candidate("c2", "rejected", rejected_at="import_screen", detail="broke"),
        ),
        trusted=(), divergences=(),
    )
    pair = _pair_with_advtests(report)

    result = t2_advtests.run(pair)

    assert result.status == "completed"
    rows = [(e.rule, e.category, e.severity) for e in result.evidence]
    assert rows == [("advtest_zero_trusted", "H6", "info")]
    entry = result.evidence[0]
    assert entry.rule not in aggregate.WEIGHTS
    assert "4" in entry.detail
    artifact = _artifact(pair, result.artifact)
    assert artifact["yield"]["n_candidates"] == 4
    assert artifact["yield"]["generated"] == 2
    assert artifact["yield"]["reference"] == 1
    assert artifact["yield"]["import_screen"] == 1
    assert artifact["yield"]["gold_prime"] == 0


def test_yield_detail_lists_rungs_in_ladder_order():
    """`_RUNGS`'s ordering drives the shipped yield-stat detail string, and
    it is unpinned today: it happens to already match ladder order, but
    nothing stops a future edit from reordering it for readability and
    silently changing what "rejected N at <rung>, ..." says."""
    report = AdversarialReport(
        model="haiku", n_candidates=2,
        candidates=(
            _candidate("c1", "rejected", rejected_at="seeded_green", detail="non-discriminating"),
            _candidate("c2", "rejected", rejected_at="reference", detail="disagreed"),
        ),
        trusted=(), divergences=(),
    )
    pair = _pair_with_advtests(report)

    result = t2_advtests.run(pair)

    entry = result.evidence[0]
    assert entry.detail.index("at reference") < entry.detail.index("at seeded_green")


def test_all_trusted_green_is_completed_and_silent():
    report = AdversarialReport(
        model="haiku", n_candidates=1,
        candidates=(_candidate("c1", "trusted"),),
        trusted=("c1",), divergences=(),
    )
    pair = _pair_with_advtests(report)

    result = t2_advtests.run(pair)

    assert result.status == "completed"
    assert result.evidence == ()


def test_artifact_written_before_evidence_and_referenced():
    report = AdversarialReport(
        model="haiku", n_candidates=1,
        candidates=(_candidate("c1", "trusted"),),
        trusted=("c1",),
        divergences=(AdvDivergence(candidate_id="c1",
                                   nodeids=("tests/test_x.py::test_one",)),),
    )
    pair = _pair_with_advtests(report)

    result = t2_advtests.run(pair)

    assert (pair.artifacts_dir / result.artifact).exists()
    for entry in result.evidence:
        assert entry.artifact == result.artifact
    artifact = _artifact(pair, result.artifact)
    assert artifact["report"]["divergences"][0]["candidate_id"] == "c1"


def test_dur_ms_present():
    report = AdversarialReport(
        model="haiku", n_candidates=1,
        candidates=(_candidate("c1", "trusted"),),
        trusted=("c1",), divergences=(),
    )
    pair = _pair_with_advtests(report)

    result = t2_advtests.run(pair)

    assert result.dur_ms is not None
    assert result.dur_ms >= 0


def test_registry_contains_advtests_in_precedence_order():
    names = [name for name, _ in aggregate.T2_REGISTRY]
    assert names == ["t2_mutation", "t2_advtests", "t2_probe", "t2_judge"]
