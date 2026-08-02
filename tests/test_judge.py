"""`skeptic.judge`: the fail-closed parse, and `judge_diff`'s one call.

Same fake-client shape `tests/test_llm.py` and `tests/test_testgen.py` use:
a script of canned responses, kwargs recorded per call. Zero API calls here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from skeptic.judge import JUDGE_SYSTEM, judge_diff, parse_judge_response
from skeptic.llm import SKEPTIC_MODEL
from skeptic.trace import TraceWriter


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
    """Replays a script of responses; records the request kwargs."""

    def __init__(self, script):
        self._script = list(script)
        self.requests = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self._script.pop(0)


def _fake_client(text: str) -> FakeClient:
    return FakeClient([FakeResponse([FakeBlock("text", text=text)])])


# --- parse_judge_response -----------------------------------------------------


def test_parse_accepts_the_three_line_form():
    text = "flag: yes\ncategory: H5\nrationale: The fix returns a hardcoded string."

    flagged, category, rationale = parse_judge_response(text)

    assert flagged is True
    assert category == "H5"
    assert rationale == "The fix returns a hardcoded string."


def test_parse_fails_closed_on_garbage():
    flagged, category, rationale = parse_judge_response(
        "This is not a structured answer at all, just prose."
    )

    assert flagged is False
    assert category is None
    assert "unparseable_response" in rationale


def test_parse_fails_closed_on_unknown_category():
    text = "flag: yes\ncategory: H99\nrationale: names a category outside the taxonomy."

    flagged, category, rationale = parse_judge_response(text)

    assert flagged is False
    assert category is None
    assert "unparseable_response" in rationale


# --- judge_diff ----------------------------------------------------------------


def test_judge_diff_calls_once_with_temperature_zero(tmp_path):
    client = _fake_client("flag: no\nrationale: the fix computes the value directly.")
    trace = TraceWriter(tmp_path / "trace.jsonl", run_id="r", task_id="t")

    judge_diff(client, "diff --git a/x.py b/x.py\n", trace)

    assert len(client.requests) == 1
    request = client.requests[0]
    assert request["model"] == SKEPTIC_MODEL
    assert request["max_tokens"] == 2000
    assert request["system"] == JUDGE_SYSTEM
    assert request["temperature"] == 0
    assert request["messages"] == [
        {"role": "user", "content": "diff --git a/x.py b/x.py\n"}
    ]


def test_judge_diff_returns_verbatim_request_and_response(tmp_path):
    client = _fake_client("flag: yes\ncategory: H7\nrationale: swallows the real failure.")
    trace = TraceWriter(tmp_path / "trace.jsonl", run_id="r", task_id="t")

    report, io = judge_diff(client, "diff --git a/x.py b/x.py\n", trace)

    assert report.model == SKEPTIC_MODEL
    assert report.flagged is True
    assert report.category == "H7"
    assert report.rationale == "swallows the real failure."

    assert io["request"] == client.requests[0]
    assert io["response"]["text"] == (
        "flag: yes\ncategory: H7\nrationale: swallows the real failure."
    )
    assert io["response"]["usage"] == {"in_tok": 100, "out_tok": 50}
