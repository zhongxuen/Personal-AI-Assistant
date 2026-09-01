"""
Ollama LLM provider (§3, §6, file 07).

Concrete `LLMProvider` implementation talking to a local Ollama server over its REST
API (`/api/tags` for the installed model list, `/api/chat` for generation). Every
Ollama-specific detail (its JSON request/response shape) is translated to/from the
provider-agnostic shapes in `app.llm.base` right here, same convention as
`app.llm.gemini`.

Unlike Gemini, Ollama is a *local* service the app cannot assume is installed or
running at all (development plan §3: "Do not assume Ollama will always be installed
or running. The system must detect availability."). So where `GeminiProvider.
is_available()` is a pure local check (an API key is either configured or it isn't),
`is_available()` here does a short-timeout network probe -- see its docstring. That
probe additionally confirms the configured `OLLAMA_MODEL` is actually pulled, so a
model that was never `ollama pull`-ed is caught here rather than surfacing as a
confusing failure from `generate()`. Neither check ever raises: a connection error,
DNS failure, or timeout is always just "not available right now".

Error classification (§5) mirrors Gemini's:
    server unreachable / model not pulled     -> PERMANENT_ERROR (nothing to retry --
                                                    the request can't succeed until an
                                                    operator starts Ollama or pulls the
                                                    model)
    timeout / transient connection failure    -> RETRYABLE_ERROR (retried internally
                                                    with exponential backoff, then
                                                    given up)
    HTTP 5xx from the Ollama server           -> RETRYABLE_ERROR
QUOTA_EXHAUSTED is part of the shared `LLMStatus` taxonomy but realistically never
returned here -- a local model has no request quota to exhaust.

`generate_stream()` is the incremental form, over the same `/api/chat` endpoint with
`"stream": true` (newline-delimited JSON, one object per token batch). It matters more
here than on the Gemini side: this provider serves precisely the turns Gemini couldn't,
and a local model on CPU regularly takes 15-20 seconds to produce a couple of sentences
-- the slowest replies the assistant ever gives, and the ones where a motionless typing
indicator is most likely to read as "it has hung". Unlike `_generate`, it does not
retry: once a delta has been sent to the client it cannot be unsent, so a retry would
duplicate visible text. `AIRouter.route_stream` fails over instead, which it can still
do safely as long as nothing has been emitted yet.

Tool/function-calling limitation: not every model Ollama can run supports tool
calling. When a model doesn't, Ollama's `/api/chat` rejects a request that includes a
`tools` payload with an HTTP 400 rather than silently ignoring it. `generate()`
detects that specific case and retries once without `tools`, so the caller still gets
a normal SUCCESS reply (with an empty `tool_calls` list) instead of the whole turn
failing outright -- see `docs/llm-providers.md` for the user-facing version of this
note.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import anyio.to_thread
import httpx
from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.database.models import LLMUsage
from app.llm.base import LLMRequest, LLMResult, LLMStreamChunk, ToolCallRequest

logger = logging.getLogger(__name__)


def _elapsed_ms(start: float) -> float:
    return (time.monotonic() - start) * 1000


class OllamaProvider:
    """`LLMProvider` backed by a local Ollama server's REST API."""

    name = "ollama"

    def __init__(self, settings: Settings | None = None, db: Session | None = None) -> None:
        self._settings = settings or get_settings()
        # Optional, same convention as `GeminiProvider` -- usage logging is skipped
        # (not raised) when no session is wired up.
        self._db = db

    def is_available(self) -> bool:
        """Short-timeout probe of the Ollama server, per the module docstring.

        Deliberately *not* the cheap-local-only check `LLMProvider`'s docstring
        describes for providers like Gemini -- a local service that may simply not be
        running can't be assumed available from config alone, so this does perform a
        bounded network call (`ollama_availability_timeout_seconds`, 2s by default).
        Never raises: any connection error, DNS failure, or timeout is treated as
        "not available", never as "must be running" (§41 Rule 3).
        """
        return self._probe() is None

    def _probe(self) -> str | None:
        """Returns `None` when the server is reachable and `OLLAMA_MODEL` is pulled
        and ready to use; otherwise a short error_type describing why not
        ("ollama_unreachable" or "model_not_found"). The one seam both
        `is_available()` and `generate()`'s pre-flight check share, so there's a
        single place that decides what "available" means for this provider.
        """
        try:
            response = httpx.get(
                f"{self._settings.ollama_base_url}/api/tags",
                timeout=self._settings.ollama_availability_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 -- any failure here means "unavailable"
            logger.info("Ollama availability probe failed: %s", exc)
            return "ollama_unreachable"

        models = payload.get("models") or []
        names = {
            entry.get("name") or entry.get("model")
            for entry in models
            if isinstance(entry, dict)
        }
        if not self._model_is_configured(names):
            return "model_not_found"
        return None

    def _model_is_configured(self, available_names: set[str | None]) -> bool:
        configured = self._settings.ollama_model
        if configured in available_names:
            return True
        # Ollama's /api/tags reports fully tagged names (e.g. "llama3.2:latest") even
        # when the user pulled/configured just the bare name -- compare the untagged
        # form too so a bare `OLLAMA_MODEL=llama3.2` still matches its default tag.
        configured_base = configured.split(":")[0]
        return any(name and name.split(":")[0] == configured_base for name in available_names)

    def _build_system_instruction(self, context: dict[str, Any]) -> str | None:
        if not context:
            return None
        # Same convention as GeminiProvider -- file 09 will refine what "relevant
        # context" means; for now whatever the caller put in LLMRequest.context is
        # serialized verbatim as a system-message preamble.
        return f"Context available to you for this request:\n{json.dumps(context, default=str)}"

    def _build_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        """Translate registered tool schemas into Ollama's OpenAI-style tool format.
        `None` (not an empty list) when there are no tools, so callers can omit the
        `tools` key entirely rather than sending one Ollama would have to parse.
        """
        if not tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": schema["name"],
                    "description": schema.get("description", ""),
                    "parameters": schema.get("parameters") or {"type": "object", "properties": {}},
                },
            }
            for schema in tools
        ]

    def _build_payload(self, request: LLMRequest) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        system_instruction = self._build_system_instruction(request.context)
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": request.message})

        payload: dict[str, Any] = {
            "model": self._settings.ollama_model,
            "messages": messages,
            "stream": False,
        }
        tools = self._build_tools(request.tools)
        if tools:
            payload["tools"] = tools
        return payload

    def _to_result(self, data: dict[str, Any], start: float) -> LLMResult:
        # `message["tool_calls"]` is Ollama's OpenAI-style list of every tool call the
        # model asked for in this one `/api/chat` response (§12 call consolidation) --
        # a model that requests several tool calls in one turn gets them all folded
        # into this single list, with no repeated call back into Ollama per tool call.
        # See `AssistantCore._handle_llm_success`'s docstring for the round trip that
        # *does* still remain (tool results aren't sent back for a second, synthesis
        # generation call) -- unrelated to what Ollama returns here.
        message = data.get("message") or {}
        tool_calls = [
            ToolCallRequest(
                tool_name=call["function"]["name"],
                params=dict(call["function"].get("arguments") or {}),
            )
            for call in (message.get("tool_calls") or [])
            if isinstance(call, dict) and call.get("function", {}).get("name")
        ]
        return LLMResult(
            text=message.get("content") or "",
            tool_calls=tool_calls,
            request_tokens=data.get("prompt_eval_count") or 0,
            response_tokens=data.get("eval_count") or 0,
            status="SUCCESS",
            latency_ms=_elapsed_ms(start),
        )

    def _log_usage(self, result: LLMResult, fallback_used: bool) -> None:
        """Write one `llm_usage` row per §5/file-05's convention -- every
        `generate()` call, success or failure alike.
        """
        if self._db is None:
            return
        self._db.add(
            LLMUsage(
                provider=self.name,
                model=self._settings.ollama_model,
                request_tokens=result.request_tokens,
                response_tokens=result.response_tokens,
                status=result.status,
                error_type=result.error_type,
                latency=int(result.latency_ms),
                fallback_used=fallback_used,
            )
        )
        self._db.commit()

    async def generate(self, request: LLMRequest, *, fallback_used: bool = False) -> LLMResult:
        """Public entrypoint -- delegates to `_generate` for the actual call/retry
        logic, then unconditionally logs the outcome to `llm_usage`, same pattern as
        `GeminiProvider.generate()`.
        """
        result = await self._generate(request)
        self._log_usage(result, fallback_used=fallback_used)
        return result

    async def generate_stream(
        self, request: LLMRequest, *, fallback_used: bool = False
    ) -> AsyncIterator[LLMStreamChunk]:
        """Streaming counterpart to `generate()`, over Ollama's newline-delimited JSON
        (`/api/chat` with `"stream": true`).

        This matters more here than it does for Gemini, not less. Ollama is the fallback
        that serves precisely the turns where Gemini was unavailable or out of budget,
        and a local model on CPU regularly takes 15-20s to produce a couple of
        sentences. Without streaming that is 15-20 seconds of a motionless typing
        indicator on the slowest replies the assistant ever gives -- the exact case the
        user is most likely to read as "it's broken".

        Same contract as every streaming provider: exactly one terminal chunk, never
        raises, logs `llm_usage` once. Like Gemini's, it doesn't retry -- once text has
        been emitted a retry would duplicate it, and `AIRouter.route_stream` handles
        pre-output failure by moving on.
        """
        start = time.monotonic()

        unavailable_reason = await anyio.to_thread.run_sync(self._probe)
        if unavailable_reason is not None:
            result = LLMResult(
                status="PERMANENT_ERROR",
                error_type=unavailable_reason,
                latency_ms=_elapsed_ms(start),
            )
            self._log_usage(result, fallback_used=fallback_used)
            yield LLMStreamChunk(final=result)
            return

        payload = {**self._build_payload(request), "stream": True}
        url = f"{self._settings.ollama_base_url}/api/chat"

        text_parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []
        request_tokens = 0
        response_tokens = 0

        try:
            async with httpx.AsyncClient(timeout=self._settings.ollama_timeout_seconds) as client:
                async with client.stream("POST", url, json=payload) as response:
                    if response.status_code != 200:
                        # The body hasn't been read yet on a streaming response, so it
                        # has to be pulled in explicitly before it can be inspected.
                        await response.aread()
                        result = self._stream_error_result(response, payload, start)
                        self._log_usage(result, fallback_used=fallback_used)
                        yield LLMStreamChunk(final=result)
                        return

                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            # A partial or malformed line is not worth ending the turn
                            # over; the `done` object may still be coming.
                            continue

                        message = data.get("message") or {}
                        for call in message.get("tool_calls") or []:
                            name = (call.get("function") or {}).get("name") if isinstance(call, dict) else None
                            if name:
                                tool_calls.append(
                                    ToolCallRequest(
                                        tool_name=name,
                                        params=dict((call["function"]).get("arguments") or {}),
                                    )
                                )

                        delta = message.get("content") or ""
                        if delta:
                            text_parts.append(delta)
                            yield LLMStreamChunk(delta=delta)

                        if data.get("done"):
                            # Token accounting only appears on the terminal object.
                            request_tokens = data.get("prompt_eval_count") or 0
                            response_tokens = data.get("eval_count") or 0
        except httpx.TimeoutException:
            result = LLMResult(
                status="RETRYABLE_ERROR", error_type="timeout", latency_ms=_elapsed_ms(start)
            )
            self._log_usage(result, fallback_used=fallback_used)
            yield LLMStreamChunk(final=result)
            return
        except Exception as exc:  # noqa: BLE001 -- transport failure mid-stream
            logger.warning("Ollama streaming call failed: %s", exc, exc_info=True)
            result = LLMResult(
                status="RETRYABLE_ERROR",
                error_type=f"stream_error:{type(exc).__name__}",
                latency_ms=_elapsed_ms(start),
            )
            self._log_usage(result, fallback_used=fallback_used)
            yield LLMStreamChunk(final=result)
            return

        result = LLMResult(
            text="".join(text_parts),
            tool_calls=tool_calls,
            request_tokens=request_tokens,
            response_tokens=response_tokens,
            status="SUCCESS",
            latency_ms=_elapsed_ms(start),
        )
        self._log_usage(result, fallback_used=fallback_used)
        yield LLMStreamChunk(final=result)

    def _stream_error_result(self, response, payload: dict[str, Any], start: float) -> LLMResult:
        """Classify a non-200 from the streaming `/api/chat` call, matching
        `_generate`'s taxonomy.

        The tool-calling 400 that `_generate` recovers from by retrying without `tools`
        is deliberately *not* retried here -- it's reported as RETRYABLE_ERROR so
        `AIRouter.route_stream` can fail over cleanly. Nothing has been emitted at this
        point (the status arrives before any body), so nothing is lost by that, and it
        keeps this path free of a second, subtly different retry loop.
        """
        status_code = response.status_code
        if status_code == 404:
            return LLMResult(
                status="PERMANENT_ERROR", error_type="model_not_found", latency_ms=_elapsed_ms(start)
            )
        if status_code == 400 and payload.get("tools") and "tool" in response.text.lower():
            logger.info(
                "Ollama model '%s' rejected tool calling on the streaming endpoint.",
                self._settings.ollama_model,
            )
            return LLMResult(
                status="RETRYABLE_ERROR",
                error_type="tools_unsupported",
                latency_ms=_elapsed_ms(start),
            )
        if 500 <= status_code < 600:
            return LLMResult(
                status="RETRYABLE_ERROR",
                error_type=f"server_error_{status_code}",
                latency_ms=_elapsed_ms(start),
            )
        return LLMResult(
            status="PERMANENT_ERROR",
            error_type=f"http_{status_code}",
            latency_ms=_elapsed_ms(start),
        )

    async def _generate(self, request: LLMRequest) -> LLMResult:
        start = time.monotonic()

        # `_probe()` is deliberately still the *sync* `httpx.get` shared with
        # `is_available()` (one definition of "available" for both, per its docstring),
        # so it's run on a worker thread rather than called directly: this coroutine now
        # runs on uvicorn's long-lived event loop (routes became `async def` for
        # streaming), where a blocking call that can sit for
        # `ollama_availability_timeout_seconds` would stall *every* other in-flight
        # request, not just this one.
        unavailable_reason = await anyio.to_thread.run_sync(self._probe)
        if unavailable_reason is not None:
            return LLMResult(
                status="PERMANENT_ERROR",
                error_type=unavailable_reason,
                latency_ms=_elapsed_ms(start),
            )

        payload = self._build_payload(request)
        url = f"{self._settings.ollama_base_url}/api/chat"
        # ollama_max_retries is a *retry* count -- max_attempts=3 for the default of 2
        # means one initial attempt plus up to two retries, RETRYABLE_ERROR only.
        max_attempts = max(1, self._settings.ollama_max_retries + 1)
        error_type = "unknown_error"
        tools_stripped = False

        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=self._settings.ollama_timeout_seconds) as client:
                    response = await client.post(url, json=payload)

                if response.status_code == 200:
                    return self._to_result(response.json(), start)

                if (
                    response.status_code == 400
                    and not tools_stripped
                    and payload.get("tools")
                    and "tool" in response.text.lower()
                ):
                    # See module docstring: this model doesn't support tool/function
                    # calling. Fall back to a plain-text request instead of failing
                    # the whole turn -- the resulting SUCCESS just has no tool_calls.
                    logger.info(
                        "Ollama model '%s' rejected tool calling -- retrying as plain text.",
                        self._settings.ollama_model,
                    )
                    payload = {k: v for k, v in payload.items() if k != "tools"}
                    tools_stripped = True
                    continue

                if response.status_code == 404:
                    return LLMResult(
                        status="PERMANENT_ERROR",
                        error_type="model_not_found",
                        latency_ms=_elapsed_ms(start),
                    )

                if 500 <= response.status_code < 600:
                    error_type = f"server_error_{response.status_code}"
                else:
                    return LLMResult(
                        status="PERMANENT_ERROR",
                        error_type=f"http_{response.status_code}",
                        latency_ms=_elapsed_ms(start),
                    )
            except httpx.TimeoutException:
                error_type = "timeout"
            except httpx.HTTPError as exc:
                # Covers connection failures (httpx.NetworkError) alongside timeouts
                # not already caught above.
                error_type = f"network_error:{type(exc).__name__}"
            except Exception as exc:  # noqa: BLE001 -- unexpected transport failure
                logger.warning("Unexpected error calling Ollama: %s", exc, exc_info=True)
                error_type = f"unexpected_error:{type(exc).__name__}"

            if attempt < max_attempts:
                backoff_seconds = self._settings.ollama_retry_base_delay_seconds * (2 ** (attempt - 1))
                await asyncio.sleep(backoff_seconds)

        logger.warning("Ollama call failed after %d attempt(s): %s", max_attempts, error_type)
        return LLMResult(status="RETRYABLE_ERROR", error_type=error_type, latency_ms=_elapsed_ms(start))
