from dataclasses import dataclass, field
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from skeptic.builder import _price
from skeptic.errors import SkepticInfraError
from skeptic.llm import SKEPTIC_MODEL, call_with_retry, response_text
from skeptic.trace import TraceWriter, read_trace


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


class RaisingClient:
    """Always raises the given exception from messages.create; counts calls
    so a test can assert a non-retried error was not retried."""

    def __init__(self, exc):
        self._exc = exc
        self.calls = 0
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls += 1
        raise self._exc


class FlakyClient:
    """Raises the given exception on the first call, then replays a script
    of responses; counts calls so a test can assert a retry happened."""

    def __init__(self, exc, script):
        self._exc = exc
        self._script = list(script)
        self.calls = 0
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls += 1
        if self._exc is not None:
            exc, self._exc = self._exc, None
            raise exc
        return self._script.pop(0)


def _overloaded_error():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(529, request=request, json={
        "type": "error",
        "error": {"type": "overloaded_error", "message": "Overloaded"},
    })
    return anthropic.OverloadedError("Overloaded", response=response, body=None)


def _authentication_error():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(401, request=request, json={
        "type": "error",
        "error": {"type": "authentication_error", "message": "invalid x-api-key"},
    })
    return anthropic.AuthenticationError("invalid x-api-key", response=response, body=None)


def test_call_with_retry_returns_first_success(tmp_path):
    trace = TraceWriter(tmp_path / "trace.jsonl", run_id="r", task_id="t")
    client = FakeClient([FakeResponse([FakeBlock("text", text="hi")])])

    response = call_with_retry(
        client, model=SKEPTIC_MODEL, max_tokens=100, system="sys",
        messages=[{"role": "user", "content": "hello"}], trace=trace,
        stage="VERIFY", actor="verify.llm",
    )

    assert response_text(response) == "hi"
    assert len(client.requests) == 1
    request = client.requests[0]
    assert request["model"] == SKEPTIC_MODEL
    assert request["max_tokens"] == 100
    assert request["system"] == "sys"
    assert "tools" not in request


def test_call_with_retry_retries_transient_then_succeeds(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr("skeptic.llm.time.sleep", lambda seconds: sleeps.append(seconds))
    trace = TraceWriter(tmp_path / "trace.jsonl", run_id="r", task_id="t")
    exc = _overloaded_error()
    client = FlakyClient(exc, [FakeResponse([FakeBlock("text", text="ok")])])

    response = call_with_retry(
        client, model=SKEPTIC_MODEL, max_tokens=100, system="sys",
        messages=[{"role": "user", "content": "hi"}], trace=trace,
        stage="VERIFY", actor="verify.llm",
    )

    assert response_text(response) == "ok"
    assert client.calls == 2
    assert sleeps == [2]
    events, _ = read_trace(trace.path)
    retry_events = [e for e in events if e["event"] == "api_retry"]
    assert len(retry_events) == 1
    assert retry_events[0]["stage"] == "VERIFY"
    assert retry_events[0]["actor"] == "verify.llm"
    assert retry_events[0]["payload"]["attempt"] == 1
    assert retry_events[0]["payload"]["error"] == "OverloadedError"


def test_call_with_retry_gives_up_after_four_transient_failures(tmp_path, monkeypatch):
    monkeypatch.setattr("skeptic.llm.time.sleep", lambda seconds: None)
    trace = TraceWriter(tmp_path / "trace.jsonl", run_id="r", task_id="t")
    client = RaisingClient(_overloaded_error())

    with pytest.raises(SkepticInfraError) as excinfo:
        call_with_retry(
            client, model=SKEPTIC_MODEL, max_tokens=100, system="sys",
            messages=[{"role": "user", "content": "hi"}], trace=trace,
            stage="VERIFY", actor="verify.llm",
        )

    assert client.calls == 4
    assert "4 times" in str(excinfo.value)


def test_call_with_retry_converts_non_transient_immediately(tmp_path):
    trace = TraceWriter(tmp_path / "trace.jsonl", run_id="r", task_id="t")
    client = RaisingClient(_authentication_error())

    with pytest.raises(SkepticInfraError) as excinfo:
        call_with_retry(
            client, model=SKEPTIC_MODEL, max_tokens=100, system="sys",
            messages=[{"role": "user", "content": "hi"}], trace=trace,
            stage="VERIFY", actor="verify.llm",
        )

    assert client.calls == 1
    assert "AuthenticationError" in str(excinfo.value)
    assert "ANTHROPIC_API_KEY" in str(excinfo.value)


def test_llm_call_event_carries_usage_with_priced_usd(tmp_path):
    trace = TraceWriter(tmp_path / "trace.jsonl", run_id="r", task_id="t")
    usage = FakeUsage(input_tokens=1000, output_tokens=1000)
    client = FakeClient([FakeResponse([FakeBlock("text", text="hi")], usage=usage)])

    call_with_retry(
        client, model=SKEPTIC_MODEL, max_tokens=100, system="sys",
        messages=[{"role": "user", "content": "hi"}], trace=trace,
        stage="VERIFY", actor="verify.llm",
    )

    events, _ = read_trace(trace.path)
    llm_calls = [e for e in events if e["event"] == "llm_call"]
    assert len(llm_calls) == 1
    assert llm_calls[0]["stage"] == "VERIFY"
    assert llm_calls[0]["actor"] == "verify.llm"
    assert llm_calls[0]["usage"] == {"in_tok": 1000, "out_tok": 1000, "usd": 0.006}


def test_response_text_concatenates_text_blocks():
    response = FakeResponse([
        FakeBlock("text", text="hello "),
        FakeBlock("text", text="world"),
    ])
    assert response_text(response) == "hello world"


def test_response_text_returns_empty_string_for_no_text_blocks():
    response = FakeResponse([])
    assert response_text(response) == ""


def test_price_knows_haiku():
    assert _price(SKEPTIC_MODEL, 1_000_000, 0) == 1.00
    assert _price(SKEPTIC_MODEL, 0, 1_000_000) == 5.00
