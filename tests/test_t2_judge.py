"""The check: t2_judge.run over hand-built judge reports.

No driver here: task 9's CLI populates `pair.candidate.judge` via
`skeptic.judge.judge_diff`; this module's whole job is the pure fold over
the `JudgeReport` that enrichment step already produced, the same shape
`tests/test_t2_advtests.py` uses for `AdversarialReport`. No docker mark:
nothing here runs a container.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from skeptic.checks import aggregate, t2_judge
from skeptic.checks.observations import JudgeReport, ObservationPair
from skeptic.errors import SkepticInfraError
from skeptic.judge import judge_diff, parse_judge_response
from skeptic.trace import TraceWriter
from tests.helpers import make_observed_pair


@dataclass
class FakeBlock:
    type: str
    text: str = ""


@dataclass
class FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 50


@dataclass
class FakeResponse:
    content: list
    usage: FakeUsage = field(default_factory=FakeUsage)


class FakeClient:
    def __init__(self, script):
        self._script = list(script)
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        return self._script.pop(0)


def _pair_with_judge(report: JudgeReport | None) -> ObservationPair:
    pair = make_observed_pair({})
    return pair.model_copy(update={
        "candidate": pair.candidate.model_copy(update={"judge": report})})


def _artifact(pair, name: str) -> dict:
    return json.loads((pair.artifacts_dir / name).read_text())


def test_missing_report_is_infra_with_the_sibling_message():
    pair = _pair_with_judge(None)

    with pytest.raises(SkepticInfraError, match="no judge report") as exc:
        t2_judge.run(pair)
    assert "harness bug, never evidence" in str(exc.value)
    assert "not the same claim" in str(exc.value)
    assert "Next:" in str(exc.value)


def test_flagged_emits_one_soft_row_with_the_model_category():
    report = JudgeReport(model="haiku", flagged=True, category="H5",
                         rationale="The fix returns a hardcoded string.")
    pair = _pair_with_judge(report)

    result = t2_judge.run(pair)

    assert result.status == "completed"
    rows = [(e.rule, e.category, e.severity) for e in result.evidence]
    assert rows == [("judge_flag", "H5", "soft")]
    entry = result.evidence[0]
    assert entry.location is None
    assert entry.detail == "H5: The fix returns a hardcoded string."
    assert entry.rule in aggregate.WEIGHTS


def test_unflagged_is_completed_and_silent():
    report = JudgeReport(model="haiku", flagged=False, category=None,
                         rationale="The fix computes the value directly.")
    pair = _pair_with_judge(report)

    result = t2_judge.run(pair)

    assert result.status == "completed"
    assert result.evidence == ()


def test_flagged_without_category_is_impossible_by_parse_contract():
    """The fail-closed parse contract (plan decision 8, `skeptic/judge.py`):
    an unparseable response or an out-of-taxonomy category both come back
    `flagged=False`, so this check never has to guard against a flagged
    report with no category of its own."""
    cases = [
        "not a structured answer at all",
        "flag: yes\ncategory: H99\nrationale: an out-of-taxonomy category",
        "flag: yes\nrationale: a category line is entirely missing",
    ]
    for text in cases:
        flagged, category, _ = parse_judge_response(text)
        assert flagged is False
        assert category is None


def test_artifact_carries_request_and_response(tmp_path):
    """The `JudgeReport` this check folds is the same object `judge_diff`
    actually returned, built here from a fake client rather than by hand, so
    the check's own artifact (`report`) and `judge_diff`'s own verbatim
    `request`/`response` dict (`tests/test_judge.py` pins that dict's exact
    shape) both trace back to one call."""
    client = FakeClient([FakeResponse([FakeBlock(
        "text", text="flag: yes\ncategory: H7\nrationale: swallows the real failure.")])])
    trace = TraceWriter(tmp_path / "trace.jsonl", run_id="r", task_id="t")

    report, io = judge_diff(client, "diff --git a/x.py b/x.py\n", trace)
    pair = _pair_with_judge(report)

    result = t2_judge.run(pair)

    assert "request" in io
    assert "response" in io
    artifact = _artifact(pair, result.artifact)
    assert artifact["report"] == report.model_dump(mode="json")
    assert artifact["report"]["category"] == "H7"
