import copy
from dataclasses import dataclass, field
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from skeptic.builder import SYSTEM_PROMPT, prompt_version, run_build
from skeptic.builder_tools import ToolContext
from skeptic.errors import SkepticInfraError
from skeptic.sandbox import ExecResult
from skeptic.trace import TraceWriter, read_trace
from tests.helpers import make_task_spec

# The pre-caching prompt_version() literal, computed once against the tree at
# 10136e8 (before this task's changes) via `.venv/bin/python -c "from
# skeptic.builder import prompt_version; print(prompt_version())"`. Same
# procedure as task 15's PRE_SALT_KEY_FOR_SPEC: run three times from the repo
# root and once with PYTHONPATH set from /tmp, identical every time.
# config_hash is a pure sha256(json.dumps(..., sort_keys=True)) over
# {"system": SYSTEM_PROMPT, "tools": TOOL_DEFS}, neither of which contains a
# path, timestamp, or hostname, so the literal is stable across machines.
PRE_CACHING_PROMPT_VERSION = "b13a3e94a723"


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
class FakeUsageWithCache:
    """A response.usage carrying the cache tiers, unlike FakeUsage above
    (which models every existing fake and every older API shape: no cache
    fields at all)."""
    input_tokens: int = 100
    output_tokens: int = 50
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class FakeResponse:
    content: list
    usage: FakeUsage = field(default_factory=FakeUsage)
    stop_reason: str = "tool_use"


class FakeClient:
    """Replays a script of responses; records the request kwargs.

    Records a deep copy, not the live kwargs dict: `messages=messages` in
    _call_with_retry passes run_build's own list by reference, and that list
    keeps getting appended to and mutated (cache_control set and cleared by
    _mark_cache_boundary) for the rest of the build. Without the copy,
    every recorded request's "messages" would alias the same list and end
    up showing its final state, not what was actually sent at call time."""

    def __init__(self, script):
        self._script = list(script)
        self.requests = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
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
    # failing_tests points at the nodeid GreenSession's junit reports, or the
    # first green clause could never hold here.
    spec = make_task_spec(allowed_paths=["pkg/"],
                          failing_tests=["tests/t.py::test_a"])

    class GreenSession:
        def exec_shell(self, cmd, timeout_s, env=None):
            return ExecResult(0, "", "", 1)

        def exec_argv(self, argv, timeout_s, env=None):
            (ws / ".skeptic-junit-build.xml").write_text(
                '<?xml version="1.0"?><testsuites><testsuite name="p">'
                '<testcase classname="tests.t" file="tests/t.py" name="test_a"/>'
                "</testsuite></testsuites>")
            return ExecResult(0, "1 passed", "", 5)

    ctx = ToolContext(workspace=ws, session=GreenSession(), spec=spec,
                      baseline_passed=frozenset(), baseline_collection_errors=0)
    trace = TraceWriter(tmp_path / "trace.jsonl", run_id="r", task_id=spec.task_id)
    return spec, ctx, trace


def test_system_prompt_states_no_selector_completion_rule():
    # A Builder that fixes the bug and verifies with a selector-scoped
    # run_tests call never sees green=True; the prompt must say the
    # completion rule requires a full run with no selector, or the loop
    # structurally never reaches the completion signal.
    assert "no selector" in SYSTEM_PROMPT
    assert "full suite" in SYSTEM_PROMPT
    # And the differential rule itself (row 74): on click-0001 the Builder
    # sees 24 red tests in the run that ends the task, and nothing else tells
    # it those are environmental.
    assert "already passing" in SYSTEM_PROMPT
    assert "environmental" in SYSTEM_PROMPT


def test_prompt_version_does_not_move():
    # Task 16 converts system= to a block list at the _call_with_retry call
    # site, leaving the SYSTEM_PROMPT string and TOOL_DEFS objects untouched.
    # prompt_version() hashes those two objects, not the wire shape, so it
    # must read exactly as it did before caching landed.
    assert prompt_version() == PRE_CACHING_PROMPT_VERSION


def test_system_is_sent_as_a_cached_block(build_env):
    spec, ctx, trace = build_env
    client = FakeClient([
        FakeResponse([FakeBlock("tool_use", name="run_tests", input={})]),
    ])
    run_build(spec, ctx, trace, model="claude-sonnet-5", client=client)
    system = client.requests[0]["system"]
    assert system == [{"type": "text", "text": SYSTEM_PROMPT,
                       "cache_control": {"type": "ephemeral"}}]


def test_run_build_reports_cache_tokens_in_trace_and_result(build_env):
    spec, ctx, trace = build_env
    usage = FakeUsageWithCache(cache_read_input_tokens=1200,
                               cache_creation_input_tokens=800)
    client = FakeClient([
        FakeResponse([FakeBlock("tool_use", name="run_tests", input={})],
                    usage=usage),
    ])
    result = run_build(spec, ctx, trace, model="claude-sonnet-5", client=client)
    events, _ = read_trace(trace.path)
    call = next(e for e in events if e["event"] == "llm_call")
    assert call["usage"]["cache_read_tok"] == 1200
    assert call["usage"]["cache_creation_tok"] == 800
    assert result.cache_read_tokens == 1200
    assert result.cache_creation_tokens == 800
    # sonnet-5 rate_in $3: (1200 * 0.1 * 3 + 800 * 1.25 * 3) / 1e6 = 0.00336.
    # The trace rounds to 4 places like every other usage figure; BuildResult
    # keeps the raw value, same convention as its usd field. Single-iteration
    # build, so cumulative == per-call.
    assert call["usage"]["usd_cache_gap"] == 0.0034
    assert result.usd_cache_gap == pytest.approx(0.00336)


def test_cache_boundary_moves_to_the_newest_user_message(build_env):
    # The second breakpoint (the first sits on the system block) must track
    # the newest user message each turn, not stay pinned to the first one:
    # the loop's dominant cost is the growing tool-result history, not the
    # ~800-token system+tools prefix alone.
    spec, ctx, trace = build_env
    read = FakeResponse([FakeBlock("tool_use", name="read_file",
                                   input={"path": "pkg/mod.py"})])
    green = FakeResponse([FakeBlock("tool_use", name="run_tests", input={})])
    client = FakeClient([read, green])
    run_build(spec, ctx, trace, model="claude-sonnet-5", client=client)

    first_messages = client.requests[0]["messages"]
    assert len(first_messages) == 1
    assert first_messages[0]["content"][-1]["cache_control"] == {
        "type": "ephemeral"}

    second_messages = client.requests[1]["messages"]
    assert len(second_messages) == 3
    # The message that carried the marker on call 1 is no longer newest;
    # its marker must be cleared, not just superseded by a second one.
    assert "cache_control" not in second_messages[0]["content"][-1]
    # The new newest message (the tool_result reply) carries it instead.
    assert second_messages[-1]["content"][-1]["cache_control"] == {
        "type": "ephemeral"}


def test_run_build_enforces_cost_ceiling_using_cache_gap(build_env):
    # usd alone ($0.00105, the existing sonnet-5 fixture math) sits under
    # this ceiling; only usd + usd_cache_gap ($0.00441 with these cache
    # fields) reaches it. If the ceiling check compared usd alone, this
    # build would run past its real cost ceiling undetected.
    spec, ctx, trace = build_env
    spec = spec.model_copy(deep=True)
    spec.constraints.cost_ceiling_usd = 0.002
    usage = FakeUsageWithCache(cache_read_input_tokens=1200,
                               cache_creation_input_tokens=800)
    client = FakeClient([FakeResponse([FakeBlock("text", text="I'm done")],
                                      stop_reason="end_turn", usage=usage)])
    result = run_build(spec, ctx, trace, model="claude-sonnet-5", client=client)
    assert result.stop_reason == "cost_ceiling"


def test_run_build_stops_on_green(build_env):
    spec, ctx, trace = build_env
    client = FakeClient([
        FakeResponse([FakeBlock("tool_use", name="run_tests", input={})]),
    ])
    result = run_build(spec, ctx, trace, model="claude-sonnet-5", client=client)
    assert result.stop_reason == "green"
    assert result.green and result.iterations == 1
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
    assert not result.green


def test_run_build_records_refusal_stop(build_env):
    spec, ctx, trace = build_env
    client = FakeClient([FakeResponse([], stop_reason="refusal")])
    result = run_build(spec, ctx, trace, model="claude-opus-5", client=client)
    assert result.stop_reason == "refusal"
    assert not result.green


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
    # Priority order (green, iteration cap, token budget, cost ceiling, model
    # ends without tool calls) means a tool-call-free turn that also blows
    # the token budget must report the budget breach, not "model_ended" --
    # otherwise a run that gave up mid-budget reads as a voluntary stop.
    spec, ctx, trace = build_env
    spec = spec.model_copy(deep=True)
    spec.constraints.token_budget = 100   # first response's 150 total exceeds it
    client = FakeClient([FakeResponse([FakeBlock("text", text="I'm done")],
                                      stop_reason="end_turn")])
    result = run_build(spec, ctx, trace, model="claude-sonnet-5", client=client)
    assert result.stop_reason == "token_budget"
    assert not result.green


def test_run_build_reports_cost_ceiling_when_model_ends_without_tools(build_env):
    spec, ctx, trace = build_env
    spec = spec.model_copy(deep=True)
    # sonnet-5 rates: (100 in * $3 + 50 out * $15) / 1e6 = $0.00105
    spec.constraints.cost_ceiling_usd = 0.0005
    client = FakeClient([FakeResponse([FakeBlock("text", text="I'm done")],
                                      stop_reason="end_turn")])
    result = run_build(spec, ctx, trace, model="claude-sonnet-5", client=client)
    assert result.stop_reason == "cost_ceiling"
    assert not result.green


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


def test_run_build_records_exception_type_in_tool_call_trace(build_env, monkeypatch):
    # 2026-07-26 review finding 3: dispatch_tool's broad except now swallows
    # every exception class, so a genuine harness bug (an AttributeError
    # after a refactor, a SkepticInfraError from the sandbox) must still be
    # diagnosable in trace.jsonl. A handler that raises an unexpected
    # exception must produce a refusal whose tool_call trace payload names
    # the exception type.
    from skeptic import builder_tools

    def _boom(_ctx, _args):
        raise AttributeError("no such attribute")

    monkeypatch.setitem(builder_tools._HANDLERS, "boom_tool", _boom)
    spec, ctx, trace = build_env
    client = FakeClient([
        FakeResponse([FakeBlock("tool_use", name="boom_tool", input={})]),
        FakeResponse([FakeBlock("tool_use", name="run_tests", input={})]),
    ])
    result = run_build(spec, ctx, trace, model="claude-sonnet-5", client=client)
    assert result.stop_reason == "green"
    events, _ = read_trace(trace.path)
    tool_calls = [e for e in events if e["event"] == "tool_call"]
    assert tool_calls[0]["payload"]["refused"] is True
    assert tool_calls[0]["payload"]["exception_type"] == "AttributeError"
    # An ordinary tool call carries no exception_type key at all.
    assert "exception_type" not in tool_calls[1]["payload"]


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

    assert result.stop_reason == "green"
    assert client.calls > 1        # the first 529 was retried, not raised
