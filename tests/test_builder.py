from dataclasses import dataclass, field
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from skeptic.builder import SYSTEM_PROMPT, run_build
from skeptic.builder_tools import ToolContext
from skeptic.errors import SkepticInfraError
from skeptic.sandbox import ExecResult
from skeptic.trace import TraceWriter, read_trace
from tests.helpers import make_task_spec


@dataclass
class FakeBlock:
    type: str
    text: str = ""
    id: str = "tu_1"
    name: str = ""
    input: dict = field(default_factory=dict)


@dataclass
class FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 50


@dataclass
class FakeResponse:
    content: list
    usage: FakeUsage = field(default_factory=FakeUsage)
    stop_reason: str = "tool_use"


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


@pytest.fixture
def build_env(tmp_path):
    ws = tmp_path / "ws"
    (ws / "pkg").mkdir(parents=True)
    (ws / "pkg" / "mod.py").write_text("value = 1\n")
    spec = make_task_spec(allowed_paths=["pkg/"])

    class GreenSession:
        def exec_shell(self, cmd, timeout_s, env=None):
            return ExecResult(0, "", "", 1)

        def exec_argv(self, argv, timeout_s, env=None):
            (ws / ".skeptic-junit-build.xml").write_text(
                '<?xml version="1.0"?><testsuites><testsuite name="p">'
                '<testcase classname="tests.t" file="tests/t.py" name="test_a"/>'
                "</testsuite></testsuites>")
            return ExecResult(0, "1 passed", "", 5)

    ctx = ToolContext(workspace=ws, session=GreenSession(), spec=spec)
    trace = TraceWriter(tmp_path / "trace.jsonl", run_id="r", task_id=spec.task_id)
    return spec, ctx, trace


def test_system_prompt_states_no_selector_completion_rule():
    # A Builder that fixes the bug and verifies with a selector-scoped
    # run_tests call never sees suite_green=True; the prompt must say the
    # completion rule requires a full run with no selector, or the loop
    # structurally never reaches the completion signal.
    assert "no selector" in SYSTEM_PROMPT
    assert "full suite" in SYSTEM_PROMPT


def test_run_build_stops_on_suite_green(build_env):
    spec, ctx, trace = build_env
    client = FakeClient([
        FakeResponse([FakeBlock("tool_use", name="run_tests", input={})]),
    ])
    result = run_build(spec, ctx, trace, model="claude-sonnet-5", client=client)
    assert result.stop_reason == "suite_green"
    assert result.suite_green and result.iterations == 1
    assert result.in_tokens == 100 and result.out_tokens == 50


def test_run_build_stops_at_iteration_cap(build_env):
    spec, ctx, trace = build_env
    read = FakeResponse([FakeBlock("tool_use", name="read_file",
                                   input={"path": "pkg/mod.py"})])
    client = FakeClient([read] * spec.constraints.max_iterations)
    result = run_build(spec, ctx, trace, model="claude-sonnet-5", client=client)
    assert result.stop_reason == "iteration_cap"
    assert result.iterations == spec.constraints.max_iterations


def test_run_build_stops_when_model_ends_without_tools(build_env):
    spec, ctx, trace = build_env
    client = FakeClient([FakeResponse([FakeBlock("text", text="I give up")],
                                      stop_reason="end_turn")])
    result = run_build(spec, ctx, trace, model="claude-sonnet-5", client=client)
    assert result.stop_reason == "model_ended"
    assert not result.suite_green


def test_run_build_records_refusal_stop(build_env):
    spec, ctx, trace = build_env
    client = FakeClient([FakeResponse([], stop_reason="refusal")])
    result = run_build(spec, ctx, trace, model="claude-opus-5", client=client)
    assert result.stop_reason == "refusal"
    assert not result.suite_green


def test_run_build_enforces_token_budget(build_env):
    spec, ctx, trace = build_env
    spec = spec.model_copy(deep=True)
    spec.constraints.token_budget = 120   # first response's 150 total exceeds it
    read = FakeResponse([FakeBlock("tool_use", name="read_file",
                                   input={"path": "pkg/mod.py"})])
    client = FakeClient([read] * 5)
    result = run_build(spec, ctx, trace, model="claude-sonnet-5", client=client)
    assert result.stop_reason == "token_budget"
    assert result.iterations == 1


def test_run_build_reports_token_budget_when_model_ends_without_tools(build_env):
    # Priority order (suite green, iteration cap, token budget, cost
    # ceiling, model ends without tool calls) means a tool-call-free turn
    # that also blows the token budget must report the budget breach, not
    # "model_ended" -- otherwise a run that gave up mid-budget reads as a
    # voluntary stop.
    spec, ctx, trace = build_env
    spec = spec.model_copy(deep=True)
    spec.constraints.token_budget = 100   # first response's 150 total exceeds it
    client = FakeClient([FakeResponse([FakeBlock("text", text="I'm done")],
                                      stop_reason="end_turn")])
    result = run_build(spec, ctx, trace, model="claude-sonnet-5", client=client)
    assert result.stop_reason == "token_budget"
    assert not result.suite_green


def test_run_build_reports_cost_ceiling_when_model_ends_without_tools(build_env):
    spec, ctx, trace = build_env
    spec = spec.model_copy(deep=True)
    # sonnet-5 rates: (100 in * $3 + 50 out * $15) / 1e6 = $0.00105
    spec.constraints.cost_ceiling_usd = 0.0005
    client = FakeClient([FakeResponse([FakeBlock("text", text="I'm done")],
                                      stop_reason="end_turn")])
    result = run_build(spec, ctx, trace, model="claude-sonnet-5", client=client)
    assert result.stop_reason == "cost_ceiling"
    assert not result.suite_green


def test_run_build_records_refusal_in_trace_when_budget_wins(build_env):
    spec, ctx, trace = build_env
    spec = spec.model_copy(deep=True)
    spec.constraints.token_budget = 100   # first response's 150 total exceeds it
    client = FakeClient([FakeResponse([], stop_reason="refusal")])
    result = run_build(spec, ctx, trace, model="claude-opus-5", client=client)
    assert result.stop_reason == "token_budget"
    events, _ = read_trace(trace.path)
    build_end = next(e for e in events if e["event"] == "build_end")
    assert build_end["payload"]["model_stop_reason"] == "refusal"


def test_run_build_converts_non_retried_api_error(build_env):
    # 2026-07-26 review finding 2: AuthenticationError (and the rest of
    # APIError's surface not already retried) must reach the caller as
    # SkepticInfraError with a what/why/next message, not a raw SDK
    # traceback, and must not be retried like the transient errors are.
    spec, ctx, trace = build_env
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(401, request=request, json={
        "type": "error",
        "error": {"type": "authentication_error", "message": "invalid x-api-key"},
    })
    exc = anthropic.AuthenticationError("invalid x-api-key", response=response, body=None)
    client = RaisingClient(exc)

    with pytest.raises(SkepticInfraError) as excinfo:
        run_build(spec, ctx, trace, model="claude-sonnet-5", client=client)

    assert "AuthenticationError" in str(excinfo.value)
    assert "ANTHROPIC_API_KEY" in str(excinfo.value)
    assert client.calls == 1


def test_run_build_retries_overloaded_error(build_env, monkeypatch):
    # Task 12 spends real money against this path: OverloadedError (HTTP
    # 529) is transient, so it belongs in the retry tuple, not the broad
    # APIError clause added for finding 2. A build that hits one 529 and
    # then succeeds must complete, not abort as an infra error.
    monkeypatch.setattr("skeptic.builder.time.sleep", lambda seconds: None)
    spec, ctx, trace = build_env
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(529, request=request, json={
        "type": "error",
        "error": {"type": "overloaded_error", "message": "Overloaded"},
    })
    exc = anthropic.OverloadedError("Overloaded", response=response, body=None)
    client = FlakyClient(exc, [
        FakeResponse([FakeBlock("tool_use", name="run_tests", input={})]),
    ])

    result = run_build(spec, ctx, trace, model="claude-sonnet-5", client=client)

    assert result.stop_reason == "suite_green"
    assert client.calls > 1        # the first 529 was retried, not raised
