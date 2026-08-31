"""
Gemini LLM provider (§3, §4, §30, file 05).

Concrete `LLMProvider` implementation wrapping Google's `google-genai` SDK. Every
Gemini-specific type (`google.genai.types.*`, `google.genai.errors.*`) is translated
to/from the provider-agnostic shapes in `app.llm.base` right here -- nothing vendor-
specific is allowed to leak past this module (see `LLMProvider`'s docstring).

Error classification (§5) -- this module's job is correct *classification*; whether/how
to act on a non-SUCCESS status (fail over, surface to the user, ...) is file 06's job:
    HTTP 429 / RESOURCE_EXHAUSTED           -> QUOTA_EXHAUSTED   (never retried)
    timeout / network blip / HTTP 5xx       -> RETRYABLE_ERROR   (retried internally
                                                                    with exponential
                                                                    backoff, then given up)
    HTTP 404 / NOT_FOUND (unknown model)    -> PERMANENT_ERROR   (never retried;
                                                                    error_type names
                                                                    the model)
    missing/invalid API key / other 4xx     -> PERMANENT_ERROR   (never retried)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx
from google.genai import Client
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.database.models import LLMUsage
from app.llm.base import LLMRequest, LLMResult, LLMStatus, ToolCallRequest

logger = logging.getLogger(__name__)

# `google.genai.errors.ClientError.status` strings that mean "out of quota" -- matched
# alongside the plain HTTP 429 code, since the SDK surfaces both.
_QUOTA_STATUSES = {"RESOURCE_EXHAUSTED"}
# ... and strings that mean "this request can never succeed as-is" (bad/missing key,
# provider disabled, malformed request) -- distinct from a transient 5xx.
_PERMANENT_STATUSES = {"UNAUTHENTICATED", "PERMISSION_DENIED", "INVALID_ARGUMENT"}


def _elapsed_ms(start: float) -> float:
    return (time.monotonic() - start) * 1000


class GeminiProvider:
    """`LLMProvider` backed by Google's Gemini API via the `google-genai` SDK."""

    name = "gemini"

    def __init__(self, settings: Settings | None = None, db: Session | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: Client | None = None
        # Optional -- when absent (e.g. unit tests exercising the provider in
        # isolation), usage logging is skipped rather than raising, same convention
        # as ToolExecutor's `db` parameter.
        self._db = db

    def is_available(self) -> bool:
        """Cheap local check only -- §41 Rule 3: never a network call to find this out."""
        return bool(self._settings.gemini_api_key)

    def _get_client(self) -> Client:
        # Built lazily (not in __init__) so constructing a GeminiProvider never touches
        # the SDK when no key is configured -- is_available() stays a pure local check.
        if self._client is None:
            self._client = Client(api_key=self._settings.gemini_api_key)
        return self._client

    def _build_tools(self, tools: list[dict[str, Any]]) -> list[genai_types.Tool] | None:
        """Translate registered tool schemas into Gemini function-calling `Tool`s.

        Tool schemas already arrive as plain JSON Schema (`Tool.parameters` in
        `app.tools.base`), which `FunctionDeclaration.parameters_json_schema` accepts
        directly -- no per-field translation into `google.genai.types.Schema` needed.
        """
        if not tools:
            return None
        declarations = [
            genai_types.FunctionDeclaration(
                name=schema["name"],
                description=schema.get("description", ""),
                parameters_json_schema=schema.get("parameters") or {"type": "object", "properties": {}},
            )
            for schema in tools
        ]
        # A single Tool with every declaration (rather than one Tool per function) is
        # what lets Gemini return multiple function calls in one response (§12 call
        # consolidation) instead of us looping back after each individual tool result.
        return [genai_types.Tool(function_declarations=declarations)]

    def _build_system_instruction(self, context: dict[str, Any]) -> str | None:
        if not context:
            return None
        # file 09 will refine what "relevant context" means and how it's shaped for
        # the model; for now, whatever the caller put in `LLMRequest.context` is
        # serialized verbatim as a system-instruction preamble.
        return f"Context available to you for this request:\n{json.dumps(context, default=str)}"

    def _classify_client_error(self, exc: genai_errors.ClientError) -> tuple[LLMStatus, str]:
        code = exc.code
        status = (exc.status or "").upper()

        if code == 429 or status in _QUOTA_STATUSES:
            return "QUOTA_EXHAUSTED", status or "resource_exhausted"

        if code == 408:
            # Request Timeout is the one 4xx that's actually transient.
            return "RETRYABLE_ERROR", "request_timeout"

        if code == 404 or status == "NOT_FOUND":
            # The one 4xx that points at *our* config rather than our credentials:
            # `gemini_model` names a model this API key can't reach (typo, or a model
            # that's since been retired). A bare "NOT_FOUND" on the status page can't
            # be acted on, so name the model -- that's the value the operator has to
            # go change (GEMINI_MODEL). Still PERMANENT_ERROR: retrying an unchanged
            # model name against unchanged credentials can't start working.
            return "PERMANENT_ERROR", f"model_not_found:{self._settings.gemini_model}"

        if code in (400, 401, 403) or status in _PERMANENT_STATUSES:
            # Covers a missing/invalid API key and a provider-disabled response --
            # retrying an unchanged request against unchanged credentials can't help.
            return "PERMANENT_ERROR", status or f"http_{code}"

        # Any other 4xx we haven't seen in practice -- treat as permanent rather than
        # retrying a request that's unlikely to change on retry.
        return "PERMANENT_ERROR", status or f"http_{code}"

    def _to_result(self, response: genai_types.GenerateContentResponse, start: float) -> LLMResult:
        # `response.function_calls` already collects every function call the SDK
        # parsed out of this one response (§12 call consolidation) -- when the model
        # asks for several tool calls in a single turn, they all land in this one list
        # and `AssistantCore._handle_llm_success` executes all of them without ever
        # calling back into Gemini per call. See that method's docstring for the
        # round trip that *does* still remain (tool results aren't sent back for a
        # second, synthesis generation call) -- not something the SDK gates here.
        tool_calls = [
            ToolCallRequest(tool_name=call.name, params=dict(call.args or {}))
            for call in (response.function_calls or [])
            if call.name
        ]
        usage = response.usage_metadata
        return LLMResult(
            text=response.text or "",
            tool_calls=tool_calls,
            request_tokens=(usage.prompt_token_count or 0) if usage else 0,
            response_tokens=(usage.candidates_token_count or 0) if usage else 0,
            status="SUCCESS",
            latency_ms=_elapsed_ms(start),
        )

    def _log_usage(self, result: LLMResult, fallback_used: bool) -> None:
        """Write one `llm_usage` row per §5/file-05 -- every `generate()` call, success
        or failure alike, so quota/error-rate reporting (file 06+) has a complete
        record. `fallback_used` comes from the caller (`AIRouter`, file 06, sets it True
        for any provider beyond the first attempted in a chain); direct callers default
        to False, matching file 05's original behavior.
        """
        if self._db is None:
            return
        self._db.add(
            LLMUsage(
                provider=self.name,
                model=self._settings.gemini_model,
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
        logic, then unconditionally logs the outcome to `llm_usage` before returning
        it, so no caller can bypass usage tracking by hitting an early-return path.
        """
        result = await self._generate(request)
        self._log_usage(result, fallback_used=fallback_used)
        return result

    async def _generate(self, request: LLMRequest) -> LLMResult:
        start = time.monotonic()

        if not self.is_available():
            return LLMResult(
                status="PERMANENT_ERROR",
                error_type="missing_api_key",
                latency_ms=_elapsed_ms(start),
            )

        config = genai_types.GenerateContentConfig(
            tools=self._build_tools(request.tools),
            system_instruction=self._build_system_instruction(request.context),
            http_options=genai_types.HttpOptions(
                timeout=int(self._settings.gemini_timeout_seconds * 1000)
            ),
        )

        # gemini_max_retries is a *retry* count -- max_attempts=3 for the default of 2
        # means one initial attempt plus up to two retries, RETRYABLE_ERROR only.
        max_attempts = max(1, self._settings.gemini_max_retries + 1)
        error_type = "unknown_error"

        for attempt in range(1, max_attempts + 1):
            try:
                client = self._get_client()
                response = await client.aio.models.generate_content(
                    model=self._settings.gemini_model,
                    contents=request.message,
                    config=config,
                )
                return self._to_result(response, start)
            except genai_errors.ClientError as exc:
                status, error_type = self._classify_client_error(exc)
                if status != "RETRYABLE_ERROR":
                    return LLMResult(status=status, error_type=error_type, latency_ms=_elapsed_ms(start))
                # else: 408 -- fall through to the shared retry/backoff below.
            except genai_errors.ServerError as exc:
                # Any 5xx -- always retryable per §5.
                error_type = f"server_error_{exc.code}"
            except httpx.HTTPError as exc:
                # Covers timeouts (httpx.TimeoutException) and transient network
                # failures (httpx.NetworkError) -- both subclass httpx.HTTPError.
                error_type = f"network_error:{type(exc).__name__}"
            except (asyncio.TimeoutError, TimeoutError):
                error_type = "timeout"
            except Exception as exc:  # noqa: BLE001 -- unexpected SDK/transport failure
                logger.warning("Unexpected error calling Gemini: %s", exc, exc_info=True)
                error_type = f"unexpected_error:{type(exc).__name__}"

            if attempt < max_attempts:
                backoff_seconds = self._settings.gemini_retry_base_delay_seconds * (2 ** (attempt - 1))
                await asyncio.sleep(backoff_seconds)

        logger.warning("Gemini call failed after %d attempt(s): %s", max_attempts, error_type)
        return LLMResult(status="RETRYABLE_ERROR", error_type=error_type, latency_ms=_elapsed_ms(start))
