from __future__ import annotations

import time

from skeptic.builder import _price
from skeptic.errors import SkepticInfraError
from skeptic.trace import TraceWriter

SKEPTIC_MODEL = "claude-haiku-4-5"


def call_with_retry(client, *, model: str, max_tokens: int, system: str,
                    messages: list, trace: TraceWriter, stage: str,
                    actor: str, temperature: float | None = None):
    import anthropic

    delays = [2, 8, 30]
    for attempt in range(4):
        try:
            kwargs = {"model": model, "max_tokens": max_tokens, "system": system,
                     "messages": messages}
            if temperature is not None:
                kwargs["temperature"] = temperature
            response = client.messages.create(**kwargs)
        except (anthropic.RateLimitError, anthropic.APITimeoutError,
                anthropic.APIConnectionError, anthropic.InternalServerError,
                anthropic.OverloadedError) as exc:
            if attempt == 3:
                raise SkepticInfraError(
                    f"Anthropic API failed 4 times ({exc!r}). Skeptic retried "
                    f"with backoff and gave up. Next: check API status and "
                    f"re-run; the stage cache resumes completed work."
                ) from exc
            trace.event(stage=stage, actor=actor, event="api_retry",
                        payload={"attempt": attempt + 1,
                                 "error": type(exc).__name__})
            time.sleep(delays[attempt])
            continue
        except anthropic.APIError as exc:
            # Same non-transient conversion as builder._call_with_retry: the
            # rest of APIError's surface (AuthenticationError,
            # BadRequestError, PermissionDeniedError, ...) is not transient,
            # so convert once, on the first attempt, to the what/why/next
            # contract instead of a raw SDK traceback.
            raise SkepticInfraError(
                f"Anthropic API call failed with {type(exc).__name__}: {exc}. "
                f"Skeptic's verify-side checks call the Anthropic API from "
                f"the host. Next: verify ANTHROPIC_API_KEY is valid, then "
                f"re-run; the stage cache resumes completed work."
            ) from exc

        usd = _price(model, response.usage.input_tokens,
                     response.usage.output_tokens)
        trace.event(stage=stage, actor=actor, event="llm_call",
                    usage={"in_tok": response.usage.input_tokens,
                           "out_tok": response.usage.output_tokens,
                           "usd": round(usd, 4)})
        return response
    raise AssertionError("unreachable")


def response_text(response) -> str:
    return "".join(block.text for block in response.content if block.type == "text")
