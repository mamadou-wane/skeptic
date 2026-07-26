from __future__ import annotations

import time
from dataclasses import dataclass

from skeptic.builder_tools import TOOL_DEFS, ToolContext, dispatch_tool
from skeptic.errors import SkepticInfraError
from skeptic.spec import TaskSpec
from skeptic.trace import TraceWriter, config_hash

DEFAULT_MODEL = "claude-opus-5"

# USD per million tokens, standard rates. Source: Anthropic model pricing
# (docs.claude.com / the claude-api skill's cached pricing reference),
# confirmed 2026-07-26 at Task 10 execution: Opus 5 $5/$25, Sonnet 5 $3/$15.
# Sonnet 5's introductory discount ($2/$10 through 2026-08-31) is ignored on
# purpose: estimating at the standard rate keeps the enforced ceiling
# conservative. The cost ceiling stops the loop, so these numbers are
# budget-enforcing.
PRICING: dict[str, dict[str, float]] = {
    "claude-opus-5": {"in": 5.00, "out": 25.00},
    "claude-sonnet-5": {"in": 3.00, "out": 15.00},
}

SYSTEM_PROMPT = """\
You are the Builder: a software engineer fixing one specific bug in an
unfamiliar Python repository, working inside a sandbox.

Rules:
- Edit only under the allowed paths given below. The test suite and runner
  configuration are read-only.
- Fix the underlying defect. Do not special-case inputs, hardcode expected
  outputs, or route around failing tests: every patch is audited against a
  hidden reference implementation, and a patch that games the suite scores
  as a failure.
- Use run_tests to check your work; a full-suite green run ends the task.
- You have a limited tool budget. Read before you edit; keep edits minimal.
"""


def prompt_version() -> str:
    return config_hash({"system": SYSTEM_PROMPT, "tools": TOOL_DEFS})


@dataclass(frozen=True)
class BuildResult:
    stop_reason: str
    iterations: int
    in_tokens: int
    out_tokens: int
    usd: float
    suite_green: bool


def _price(model: str, in_tok: int, out_tok: int) -> float:
    rates = PRICING.get(model)
    if rates is None:
        raise SkepticInfraError(
            f"No pricing entry for model {model!r}. Skeptic enforces the "
            f"task's cost ceiling from a verified price table. Next: add "
            f"the model to PRICING in skeptic/builder.py with a sourced "
            f"price, then re-run."
        )
    return (in_tok * rates["in"] + out_tok * rates["out"]) / 1_000_000


def _user_prompt(spec: TaskSpec) -> str:
    return (
        f"{spec.builder_input.problem_statement}\n\n"
        f"Allowed edit paths: {spec.builder_input.allowed_paths}\n"
        f"Test command: {spec.environment.test_cmd}\n"
        f"Start by listing files and reading the failing area."
    )


def _call_with_retry(client, *, model: str, messages: list, trace: TraceWriter):
    import anthropic

    delays = [2, 8, 30]
    for attempt in range(4):
        try:
            # 16000 is the non-streaming-safe ceiling. Generous on purpose:
            # Opus 5 thinks by default and max_tokens caps thinking plus
            # response text together, so a tight cap truncates turns.
            return client.messages.create(
                model=model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                tools=TOOL_DEFS,
                messages=messages,
            )
        except (anthropic.RateLimitError, anthropic.APITimeoutError,
                anthropic.APIConnectionError, anthropic.InternalServerError) as exc:
            if attempt == 3:
                raise SkepticInfraError(
                    f"Anthropic API failed 4 times ({exc!r}). Skeptic retried "
                    f"with backoff and gave up. Next: check API status and "
                    f"re-run; the stage cache resumes completed work."
                ) from exc
            trace.event(stage="BUILD", actor="builder.llm", event="api_retry",
                        payload={"attempt": attempt + 1,
                                 "error": type(exc).__name__})
            time.sleep(delays[attempt])
    raise AssertionError("unreachable")


def run_build(
    spec: TaskSpec, ctx: ToolContext, trace: TraceWriter, model: str, client
) -> BuildResult:
    messages: list[dict] = [{"role": "user", "content": _user_prompt(spec)}]
    in_tokens = out_tokens = iterations = 0
    suite_green = False
    stop_reason = "model_ended"
    last_model_stop_reason: str | None = None
    trace.event(stage="BUILD", actor="builder", event="build_start",
                payload={"model": model, "prompt_version": prompt_version()})
    while True:
        response = _call_with_retry(client, model=model, messages=messages,
                                    trace=trace)
        last_model_stop_reason = response.stop_reason
        in_tokens += response.usage.input_tokens
        out_tokens += response.usage.output_tokens
        # per-call marginal cost in the event, so llm_call rows sum to the
        # build_end total; the cumulative figure drives the ceiling check
        call_usd = _price(model, response.usage.input_tokens,
                          response.usage.output_tokens)
        usd = _price(model, in_tokens, out_tokens)
        trace.event(stage="BUILD", actor="builder.llm", event="llm_call",
                    usage={"in_tok": response.usage.input_tokens,
                           "out_tok": response.usage.output_tokens,
                           "usd": round(call_usd, 4)})
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if tool_uses:
            iterations += 1
            results = []
            for block in tool_uses:
                outcome = dispatch_tool(ctx, block.name, dict(block.input))
                trace.event(stage="BUILD", actor="builder.tool", event="tool_call",
                            payload={"tool": block.name, "refused": outcome.refused,
                                     "suite_green": outcome.suite_green})
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": outcome.text})
                suite_green = suite_green or outcome.suite_green
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": results})
            if suite_green:
                stop_reason = "suite_green"
                break
            if iterations >= spec.constraints.max_iterations:
                stop_reason = "iteration_cap"
                break
        # Token budget and cost ceiling are checked unconditionally, whether
        # or not this response carried a tool call: unlike suite_green and
        # iteration_cap (which structurally cannot fire without a dispatched
        # tool), both depend only on accumulated usage. Per the plan's
        # priority order (suite green, iteration cap, token budget, cost
        # ceiling, model ends without tool calls), a turn that ends
        # tool-call-free right as it blows its budget must report the
        # budget breach, not "model_ended" -- a run that gave up mid-budget
        # should not read as a voluntary stop.
        if in_tokens + out_tokens >= spec.constraints.token_budget:
            stop_reason = "token_budget"
            break
        if usd >= spec.constraints.cost_ceiling_usd:
            stop_reason = "cost_ceiling"
            break
        if not tool_uses:
            # Opus 5 safety classifiers can end a turn with stop_reason
            # "refusal" (HTTP 200, no error); record it distinctly from a
            # voluntary stop so the trace tells the two apart.
            stop_reason = ("refusal" if response.stop_reason == "refusal"
                           else "model_ended")
            break
    final_usd = _price(model, in_tokens, out_tokens)
    trace.event(stage="BUILD", actor="builder", event="build_end",
                payload={"stop_reason": stop_reason, "iterations": iterations,
                         "suite_green": suite_green,
                         # The model's own last stop_reason, kept alongside
                         # the resolved `stop_reason` above: a budget/ceiling
                         # breach can outrank a same-turn refusal in
                         # `stop_reason`, and this key is how that refusal
                         # survives into the trace instead of being lost.
                         "model_stop_reason": last_model_stop_reason},
                usage={"in_tok": in_tokens, "out_tok": out_tokens,
                       "usd": round(final_usd, 4)})
    return BuildResult(stop_reason=stop_reason, iterations=iterations,
                       in_tokens=in_tokens, out_tokens=out_tokens,
                       usd=final_usd, suite_green=suite_green)
