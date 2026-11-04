"""
OpenAI LLM provider -- the primary provider (priority 1 in `ProviderManager`).

Concrete `LLMProvider` over OpenAI's Responses API via the official `openai` SDK, reached only through
`app.llm.openai_guard` (the one client -- see OPENAI-KEY-RULES.md, local). Like
`app.llm.gemini`, every OpenAI-specific type and error is translated to/from the
provider-agnostic shapes in `app.llm.base` right here and nowhere else.

## Spend rules (OPENAI-KEY-RULES.md rules 4-6)

- Two model tiers from env: `LLMRequest.tier` picks `openai_model_bulk` or
  `openai_model_quality` (default quality).
- Every call is checked against the app's own USD budget (`QuotaManager`: daily
  `openai_daily_budget_usd`, monthly `ai_monthly_budget_usd`) *here*, before it is sent,
  not only by `AIRouter` -- so no caller can skip it.
- Every call is cost-logged to `llm_usage` (job, model, tokens, estimated USD from
  `OPENAI_PRICES`; an unknown model is costed at the dearest known price).
- Every call sets `max_output_tokens`.
- `credit_balance_exhausted`, `insufficient_quota` and hard spend-limit errors are
  logged at ERROR and carried to the reply as `LLMResult.spend_stop`.

## Privacy rules (a violation is a bug, and tests/llm enforce them)

1. Every request is built by ONE helper, `_build_params`, and leaves through ONE other
   helper, `_send`. Storage on OpenAI is allowed under the current key rules, but
   `store=False` is still sent (harmless; nothing needs it stored).
2. Nothing server-side holds our state between requests: no `previous_response_id`,
   no Conversations API, no Files API uploads, no vector stores, no Assistants/
   threads, no background mode. History is sent by us on every request, and
   attachments go inline as base64 content parts for that one request only.
   Reasoning is carried between tool-loop steps with
   `include=["reasoning.encrypted_content"]` -- opaque blobs *we* hold and replay,
   which is what makes multi-step tool use work with nothing stored on OpenAI.
3. `metadata`, `user`, `safety_identifier` and `prompt_cache_key` are never set, so
   nothing identifying the project, the task or the user reaches OpenAI.
4. Every request also passes through `app.llm.openai_guard`'s transport, which
   enforces the same rules again below the SDK (allowed endpoints only, banned fields
   stripped/refused).

## Prompt layout (static-first, for OpenAI's automatic prompt caching)

`instructions` (`_INSTRUCTIONS`, byte-identical on every request) -> `tools` (sorted
by name) -> a developer message with this request's dynamic context (time, platform,
memory) -> recent turns as real user/assistant items -> the user message (+ inline
attachments) -> any earlier tool-loop steps. Caching only ever matches a prefix, so
everything that changes per request sits after everything that doesn't. No
`prompt_cache_key` is sent; automatic caching still applies.

## Error classification (§5) -- mirrors GeminiProvider
    app budget used up (checked locally)   -> QUOTA_EXHAUSTED  `app_budget_exhausted`
                                                               (nothing sent)
    `credit_balance_exhausted` / hard
      spend limit / `insufficient_quota`   -> QUOTA_EXHAUSTED  `billing:<code>` + spend_stop
    HTTP 429 (rate limit)                  -> QUOTA_EXHAUSTED  (never retried)
    timeout / connection error / 5xx       -> RETRYABLE_ERROR  (retried with backoff;
                                                                streaming: only until
                                                                the first delta)
    HTTP 404 (unknown model)               -> PERMANENT_ERROR  `model_not_found:<model>`
    HTTP 400 / 401 / 403 / other 4xx       -> PERMANENT_ERROR  (never retried)
    `incomplete` with no text / tool call  -> PERMANENT_ERROR  `incomplete:<reason>`
                                              (tokens and cost still logged)
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.database.models import LLMUsage
from app.llm.base import (
    LLMAttachment,
    LLMRequest,
    LLMResult,
    LLMStatus,
    LLMStreamChunk,
    ToolCallRequest,
    ToolExchange,
)
from app.llm.clients import get_loop_client
from app.llm.openai_guard import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    guarded_async_client,
    is_policy_error,
)
from app.llm.quota_manager import QuotaManager

logger = logging.getLogger(__name__)

# US$ per 1M tokens: (input, cached input, output). Must list every model the tiers
# use (OPENAI-KEY-RULES.md rule 4) -- check https://openai.com/api/pricing when a tier
# changes. Cached input is costed at the full input price: the rules give no cached
# rate for these models, and the cap must never undercount.
OPENAI_PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-6-luna": (0.10, 0.10, 0.50),
    "gpt-6.1-terra": (1.00, 1.00, 5.00),
}
# A model not in the table is costed at the dearest known rate, per column.
_FALLBACK_PRICE = tuple(max(prices[i] for prices in OPENAI_PRICES.values()) for i in range(3))
_SNAPSHOT_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def price_for(model: str) -> tuple[float, float, float]:
    """(input, cached input, output) US$/1M tokens for `model`. OpenAI may answer with a
    dated snapshot name (`gpt-6-luna-2026-10-01`), which counts as its base model. Any
    other suffix (`-pro`, `-max`) is a different model and gets the fallback price."""
    base = _SNAPSHOT_SUFFIX.sub("", model)
    return OPENAI_PRICES.get(base, _FALLBACK_PRICE)

# The assistant's fixed persona/rules. Deliberately static -- no dates, names or
# per-request values -- because it is the head of the cached prompt prefix: changing a
# single byte here per request would make every request a cache miss. Per-request
# facts go in the developer context message instead (`_context_message`).
_INSTRUCTIONS = """\
You are JARVIS, a personal AI assistant working for a single user.

How to behave:
- Be concise and direct. Answer in plain text; short lists are fine when they help.
- When the user asks you to do something a tool can do, call the tool rather than \
describing what you would do. Never claim an action happened unless a tool result \
confirms it.
- When a tool result comes back, answer from what it actually says. If it reports an \
error or a permission/platform rejection, tell the user plainly and suggest a next step.
- A developer message titled "Context for this request" carries the current date and \
time in the user's timezone, the platform they are using, and any saved memory that \
may be relevant. Trust it over your own sense of the date, and don't recite it unless \
asked.
- Some actions (opening apps, files, the clipboard, the terminal) only work in the \
desktop app. If a tool says an action isn't available on this platform, explain that.
- Files the user attaches are included with their message. Read them before answering \
questions about them.
- If you don't know something and no tool can find it, say so instead of guessing.\
"""

# Request fields that would put our data or state on OpenAI's side, or that we must
# never set ourselves. `_build_params` never sets them, and `_assert_private` refuses
# to send a request that somehow carries one.
_BANNED_PARAMS = frozenset(
    {
        "previous_response_id",  # server-side conversation chaining
        "conversation",  # Conversations API
        "background",  # background mode stores the response
        "prompt",  # server-stored prompt templates
        "metadata",  # rule 3: nothing identifying reaches OpenAI
        "user",
        "safety_identifier",
        "prompt_cache_key",
    }
)

# Models that do hidden reasoning and so need their reasoning items carried across
# tool-loop steps (o-series, GPT-5 and later). A non-reasoning model would reject
# `include=["reasoning.encrypted_content"]`, so this only ever turns it *on*.
_REASONING_MODEL = re.compile(r"^(o\d|gpt-([5-9]|\d{2,}))")

# A single tool result can be large (list_tasks on a long backlog). It's resent on
# every later step of the turn, so it's capped rather than allowed to dominate cost.
_MAX_TOOL_OUTPUT_CHARS = 8_000

# Anything shaped like an API key, masked before an error message is logged.
_KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]*")

# OpenAI error codes (body `code`, or a streamed `error`/`response.failed` code) that
# mean money ran out: the org's prepaid balance, or a project/org hard spend limit.
# These stop EVERY call until the owner acts, so they're logged at ERROR and surfaced
# in the reply (`spend_stop`) rather than treated like a passing rate limit. The list
# is deliberately broad -- an unrecognised billing code would otherwise fail quietly as
# a generic 4xx -- and `_looks_like_billing` also catches them by message.
_BILLING_CODES = {
    "credit_balance_exhausted",
    "insufficient_quota",
    "billing_hard_limit_reached",
    "billing_limit_reached",
    "hard_limit_reached",
    "spend_limit_reached",
    "budget_exceeded",
}
_BILLING_MESSAGE = re.compile(
    r"credit balance|hard limit|spend(ing)? limit|billing limit|budget (limit|exceeded)|exceeded your current quota",
    re.I,
)
_QUOTA_CODES = {"rate_limit_exceeded"}
_RETRYABLE_CODES = {"server_error", "server_is_overloaded", "slow_down"}


def _spend_stop_message(code: str) -> str:
    if code == "app_budget_exhausted":
        return (
            "The app's own OpenAI spend cap (AI_MONTHLY_BUDGET_USD / "
            "OPENAI_DAILY_BUDGET_USD) has been reached, so OpenAI calls are paused until "
            "it resets or is raised."
        )
    if code == "credit_balance_exhausted":
        return (
            "OpenAI's prepaid credit balance is used up (credit_balance_exhausted). Top it "
            "up, or check auto-recharge, under Billing on platform.openai.com."
        )
    return (
        f"OpenAI refused the call over billing ({code}): the project or org spend limit "
        "was hit or the balance is empty. Check Billing and Limits on platform.openai.com."
    )


def _looks_like_billing(code: str | None, message: str | None) -> bool:
    return code in _BILLING_CODES or bool(message and _BILLING_MESSAGE.search(message))


def _elapsed_ms(start: float) -> float:
    return (time.monotonic() - start) * 1000


# --- Tool schemas -------------------------------------------------------------------


def _nullable(prop: dict[str, Any]) -> dict[str, Any]:
    """`prop` widened to also accept null -- how strict mode expresses "optional"
    (every property must be listed in `required`, so absence isn't an option)."""
    json_type = prop.get("type")
    if json_type is None:
        return {"anyOf": [prop, {"type": "null"}]}
    types = list(json_type) if isinstance(json_type, list) else [json_type]
    if "null" not in types:
        types.append("null")
    prop = {**prop, "type": types}
    if "enum" in prop and None not in prop["enum"]:
        prop["enum"] = [*prop["enum"], None]
    return prop


def _is_free_form_object(prop: dict[str, Any]) -> bool:
    json_type = prop.get("type")
    types = json_type if isinstance(json_type, list) else [json_type]
    return "object" in types and not prop.get("properties")


def _strict_object(schema: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
    """A tool's parameter schema rewritten for strict function calling: every object
    closed (`additionalProperties: false`), every property listed in `required`, and
    originally-optional properties made nullable.

    Strict mode cannot express an object with arbitrary keys, so a free-form object
    property (e.g. `run_terminal_command`'s `args`) becomes a JSON-encoded string
    instead. Returns the rewritten schema plus the names of those properties, so
    `_decode_arguments` can turn them back into objects before `ToolExecutor` sees them.
    """
    schema = copy.deepcopy(schema) if schema else {}
    properties: dict[str, Any] = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    json_encoded: set[str] = set()

    strict_properties: dict[str, Any] = {}
    for name, prop in properties.items():
        prop = dict(prop or {})
        if _is_free_form_object(prop):
            description = prop.get("description", "")
            prop = {
                "type": "string",
                "description": f"{description} A JSON object, encoded as a string.".strip(),
            }
            json_encoded.add(name)
        elif prop.get("type") == "object" and prop.get("properties"):
            prop, _nested = _strict_object(prop)
        elif prop.get("type") == "array" and isinstance(prop.get("items"), dict):
            items = prop["items"]
            if items.get("type") == "object" and items.get("properties"):
                items, _nested = _strict_object(items)
            prop["items"] = items
        if name not in required:
            prop = _nullable(prop)
        strict_properties[name] = prop

    strict = {
        **{k: v for k, v in schema.items() if k not in ("properties", "required")},
        "type": "object",
        "properties": strict_properties,
        "required": list(strict_properties),
        "additionalProperties": False,
    }
    return strict, json_encoded


def _decode_arguments(raw: str, schema: dict[str, Any] | None) -> dict[str, Any]:
    """A function call's `arguments` JSON as the params `ToolExecutor` expects -- i.e.
    undoing `_strict_object`: nulls for originally-optional properties are dropped (the
    tool handlers treat "absent" as "use the default"; a `None` would fail the
    executor's type check), and JSON-string-encoded objects are parsed back."""
    try:
        args = json.loads(raw or "{}")
    except json.JSONDecodeError:
        logger.warning("OpenAI returned function arguments that aren't valid JSON.")
        return {}
    if not isinstance(args, dict):
        return {}

    schema = schema or {}
    required = set(schema.get("required") or [])
    properties = schema.get("properties") or {}
    params: dict[str, Any] = {}
    for name, value in args.items():
        if value is None and name not in required:
            continue
        prop = properties.get(name) or {}
        if isinstance(value, str) and _is_free_form_object(prop):
            try:
                value = json.loads(value) if value.strip() else {}
            except json.JSONDecodeError:
                pass  # leave it as-is; ToolExecutor's type check reports it
        params[name] = value
    return params


# --- Provider ------------------------------------------------------------------------


class OpenAIProvider:
    """`LLMProvider` backed by OpenAI's Responses API, with nothing stored on OpenAI."""

    name = "openai"
    # Understands `LLMRequest.tool_exchanges` natively (function_call_output items +
    # replayed encrypted reasoning), so AssistantCore may run its tool loop through it.
    supports_tool_loop = True

    def __init__(self, settings: Settings | None = None, db: Session | None = None) -> None:
        self._settings = settings or get_settings()
        # Tests inject a stub here; see `_get_client`.
        self._client: Any = None
        # Optional, same convention as GeminiProvider: no session -> no usage row.
        self._db = db

    def is_available(self) -> bool:
        """Cheap local check only -- §41 Rule 3: never a network call to find this out."""
        return bool(self._settings.openai_api_key)

    def _get_client(self) -> AsyncOpenAI:
        # Cached per event loop, not per instance -- same reasoning as
        # GeminiProvider._get_client (a fresh provider is built per request). The SDK's
        # own retries are off: retry policy is ours (`_generate`), so it can respect
        # the "never retry quota/permanent errors" rule and the streaming rule.
        if self._client is not None:
            return self._client
        api_key = self._settings.openai_api_key
        # The namespace is logged at debug level by app.llm.clients -- hash the key
        # rather than embedding it, so a debug log can never contain the secret.
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()[:12]
        return get_loop_client(
            f"openai:{key_hash}",
            lambda: guarded_async_client(
                api_key=api_key,
                timeout=self._settings.openai_timeout_seconds,
            ),
        )

    # -- request building --------------------------------------------------------

    def model_for(self, request: LLMRequest) -> str:
        """The configured model for `request`'s tier (rule 5)."""
        s = self._settings
        return s.openai_model_bulk if request.tier == "bulk" else s.openai_model_quality

    def _reasoning_enabled(self, model: str) -> bool:
        return bool(self._settings.openai_reasoning_effort) or bool(_REASONING_MODEL.match(model))

    def _build_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Our JSON-schema tools as strict Responses function tools, sorted by name so
        the same tool set always serializes identically (a stable cache prefix)."""
        built = []
        for schema in sorted(tools, key=lambda t: t["name"]):
            parameters, _json_encoded = _strict_object(
                schema.get("parameters") or {"type": "object", "properties": {}}
            )
            built.append(
                {
                    "type": "function",
                    "name": schema["name"],
                    "description": schema.get("description", ""),
                    "parameters": parameters,
                    "strict": True,
                }
            )
        return built

    @staticmethod
    def _context_message(context: dict[str, Any]) -> str | None:
        """Per-request facts for the developer context message. `recent_turns` are
        sent as real conversation items instead, and `user_id` is left out -- the model
        has no use for it."""
        dynamic = {
            key: value
            for key, value in context.items()
            if key not in ("recent_turns", "user_id")
        }
        if not dynamic:
            return None
        return f"Context for this request:\n{json.dumps(dynamic, default=str)}"

    @staticmethod
    def _attachment_part(attachment: LLMAttachment) -> dict[str, Any]:
        """One attachment as an inline content part -- base64 in the request body,
        never a `file_id` (privacy rule 2: nothing goes to the Files API)."""
        mime = attachment.mime_type
        if mime.startswith("image/"):
            encoded = base64.b64encode(attachment.data).decode("ascii")
            return {"type": "input_image", "image_url": f"data:{mime};base64,{encoded}", "detail": "auto"}
        if mime == "application/pdf":
            encoded = base64.b64encode(attachment.data).decode("ascii")
            return {
                "type": "input_file",
                "filename": attachment.filename,
                "file_data": f"data:{mime};base64,{encoded}",
            }
        # Plain text / markdown: sent as text, which every model reads natively.
        text = attachment.data.decode("utf-8", errors="replace")
        return {"type": "input_text", "text": f"Attached file '{attachment.filename}':\n\n{text}"}

    def _exchange_items(self, exchange: ToolExchange, step: int) -> list[dict[str, Any]]:
        """One earlier tool-loop step as input items: what the model produced (its
        reasoning and function calls), then each call's output."""
        items: list[dict[str, Any]] = []
        call_ids: list[str] = []

        if exchange.provider == self.name and exchange.provider_items:
            # Our own previous output: replay it as-is (encrypted reasoning included)
            # so a reasoning model keeps its chain of thought across steps.
            items.extend(copy.deepcopy(exchange.provider_items))
            call_ids = [call.call_id or f"call_{step}_{i}" for i, call in enumerate(exchange.calls)]
        else:
            # A step another provider answered (the turn failed over mid-loop): no
            # native items to replay, so synthesize function_call items from the
            # provider-agnostic calls.
            for i, call in enumerate(exchange.calls):
                call_id = call.call_id or f"call_{step}_{i}"
                call_ids.append(call_id)
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": call.tool_name,
                        "arguments": json.dumps(call.params, default=str),
                    }
                )

        for call_id, output in zip(call_ids, exchange.outputs):
            if len(output) > _MAX_TOOL_OUTPUT_CHARS:
                output = output[:_MAX_TOOL_OUTPUT_CHARS] + "... [truncated]"
            items.append({"type": "function_call_output", "call_id": call_id, "output": output})
        return items

    def _build_input(self, request: LLMRequest) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []

        context_text = self._context_message(request.context)
        if context_text:
            items.append({"role": "developer", "content": context_text})

        for turn in request.context.get("recent_turns") or []:
            role = turn.get("role")
            content = turn.get("content")
            if role in ("user", "assistant") and content:
                items.append({"role": role, "content": content})

        if request.attachments:
            content: Any = [self._attachment_part(a) for a in request.attachments]
            content.append({"type": "input_text", "text": request.message})
        else:
            content = request.message
        items.append({"role": "user", "content": content})

        for step, exchange in enumerate(request.tool_exchanges):
            items.extend(self._exchange_items(exchange, step))
        return items

    def _build_params(self, request: LLMRequest, *, stream: bool) -> dict[str, Any]:
        """THE one place a Responses request is built (privacy rule 1). Streaming and
        non-streaming calls differ only in `stream`. No `temperature`/`top_p`: newer
        models reject them."""
        model = self.model_for(request)
        params: dict[str, Any] = {
            "model": model,
            "instructions": _INSTRUCTIONS,
            "input": self._build_input(request),
            "stream": stream,
            # Rule 6: every call is capped (reasoning tokens count toward it).
            "max_output_tokens": self._settings.openai_max_output_tokens,
        }

        tools = self._build_tools(request.tools)
        if tools:
            params["tools"] = tools
            params["parallel_tool_calls"] = True
            if not request.allow_tool_calls:
                # Keep the tools declared (so the cached prefix is unchanged) but
                # forbid calling them -- the tool loop's last step must answer in text.
                params["tool_choice"] = "none"

        if self._reasoning_enabled(model):
            # Reasoning items come back encrypted so we can carry them between tool
            # steps ourselves -- the stateless alternative to previous_response_id.
            params["include"] = ["reasoning.encrypted_content"]
            if self._settings.openai_reasoning_effort:
                params["reasoning"] = {"effort": self._settings.openai_reasoning_effort}

        # Applied last, unconditionally: nothing above -- and no future edit to it --
        # can carry server-side state or identifying fields to OpenAI. (store=False is
        # no longer required by the key rules; it's kept because nothing needs storing.)
        for banned in _BANNED_PARAMS:
            params.pop(banned, None)
        params["store"] = False
        return params

    @staticmethod
    def _assert_private(params: dict[str, Any]) -> None:
        """Refuse to send anything that breaks the privacy rules. Raises rather than
        repairing: reaching this means a bug in `_build_params`."""
        leaked = _BANNED_PARAMS.intersection(params)
        if leaked:
            raise RuntimeError(f"Refusing to send an OpenAI request with {sorted(leaked)}.")
        for item in params.get("input") or []:
            content = item.get("content") if isinstance(item, dict) else None
            for part in content if isinstance(content, list) else []:
                if isinstance(part, dict) and "file_id" in part:
                    raise RuntimeError("Refusing to send an OpenAI request referencing a file_id.")

    async def _send(self, params: dict[str, Any]) -> Any:
        """THE one place a request leaves for OpenAI (privacy rule 1)."""
        self._assert_private(params)
        return await self._get_client().responses.create(**params)

    # -- response parsing --------------------------------------------------------

    @staticmethod
    def _response_text(response: Any) -> str:
        parts: list[str] = []
        for item in getattr(response, "output", None) or []:
            if getattr(item, "type", None) != "message":
                continue
            for content in getattr(item, "content", None) or []:
                content_type = getattr(content, "type", None)
                if content_type == "output_text":
                    parts.append(content.text or "")
                elif content_type == "refusal":
                    parts.append(content.refusal or "")
        return "".join(parts)

    def _to_result(self, response: Any, request: LLMRequest, start: float) -> LLMResult:
        schemas = {tool["name"]: tool.get("parameters") for tool in request.tools}
        tool_calls: list[ToolCallRequest] = []
        provider_items: list[dict[str, Any]] = []

        for item in getattr(response, "output", None) or []:
            item_type = getattr(item, "type", None)
            if item_type == "function_call":
                tool_calls.append(
                    ToolCallRequest(
                        tool_name=item.name,
                        params=_decode_arguments(item.arguments, schemas.get(item.name)),
                        call_id=item.call_id,
                    )
                )
                provider_items.append(
                    {
                        "type": "function_call",
                        "call_id": item.call_id,
                        "name": item.name,
                        "arguments": item.arguments,
                    }
                )
            elif item_type == "reasoning" and getattr(item, "encrypted_content", None):
                provider_items.append(
                    {
                        "type": "reasoning",
                        "id": item.id,
                        "summary": [
                            part.model_dump() if hasattr(part, "model_dump") else part
                            for part in (item.summary or [])
                        ],
                        "encrypted_content": item.encrypted_content,
                    }
                )

        usage = getattr(response, "usage", None)
        details = getattr(usage, "input_tokens_details", None) if usage else None
        result = LLMResult(
            text=self._response_text(response),
            tool_calls=tool_calls,
            provider_items=provider_items,
            request_tokens=(getattr(usage, "input_tokens", 0) or 0) if usage else 0,
            response_tokens=(getattr(usage, "output_tokens", 0) or 0) if usage else 0,
            cached_tokens=(getattr(details, "cached_tokens", 0) or 0) if details else 0,
            status="SUCCESS",
            latency_ms=_elapsed_ms(start),
        )
        return self._check_incomplete(response, result)

    @staticmethod
    def _check_incomplete(response: Any, result: LLMResult) -> LLMResult:
        """An `incomplete` reply with nothing usable -- typically a reasoning model that
        spent all of `max_output_tokens` thinking -- is a failure, not an empty answer.
        Its tokens stay on the result: they were billed, so they're cost-logged."""
        if getattr(response, "status", None) != "incomplete" or result.text or result.tool_calls:
            return result
        details = getattr(response, "incomplete_details", None)
        reason = getattr(details, "reason", None) or "unknown"
        logger.warning(
            "OpenAI reply incomplete (%s) with no text after %d output tokens -- raise "
            "OPENAI_MAX_OUTPUT_TOKENS or lower OPENAI_REASONING_EFFORT.",
            reason,
            result.response_tokens,
        )
        result.status = "PERMANENT_ERROR"
        result.error_type = f"incomplete:{reason}"
        return result

    # -- error classification ----------------------------------------------------

    @staticmethod
    def _billing(code: str | None) -> tuple[LLMStatus, str]:
        """Money ran out. Never retried, failed over like a quota stop, and loud: the
        owner has to act before any OpenAI call can succeed again."""
        error_type = f"billing:{code or 'spend_limit'}"
        logger.error("OpenAI billing stop (%s): %s", error_type, _spend_stop_message(code or "spend_limit"))
        return "QUOTA_EXHAUSTED", error_type

    def _classify_code(
        self, code: str | None, model: str, message: str | None = None
    ) -> tuple[LLMStatus, str]:
        """An error *code* from the API body or a streamed failure event."""
        if _looks_like_billing(code, message):
            return self._billing(code)
        code = code or "unknown_error"
        if code in _QUOTA_CODES:
            return "QUOTA_EXHAUSTED", code
        if code in _RETRYABLE_CODES:
            return "RETRYABLE_ERROR", code
        if code == "model_not_found":
            return "PERMANENT_ERROR", f"model_not_found:{model}"
        return "PERMANENT_ERROR", code

    def _classify_status_error(self, exc: APIStatusError, model: str) -> tuple[LLMStatus, str]:
        status_code = exc.status_code
        code = getattr(exc, "code", None)

        if _looks_like_billing(code, getattr(exc, "message", None)):
            return self._billing(code)
        if status_code == 429:
            # A rate limit is "stop calling for now" -- fail over, never retry (§5).
            return "QUOTA_EXHAUSTED", code or "rate_limited"
        if status_code == 404:
            # Names the model, because that's the value an operator has to change
            # (OPENAI_MODEL_BULK / OPENAI_MODEL_QUALITY) -- same convention as Gemini.
            return "PERMANENT_ERROR", f"model_not_found:{model}"
        if status_code in (408, 409):
            # Request timeout / conflict: transient by definition.
            return "RETRYABLE_ERROR", f"http_{status_code}"
        if status_code >= 500:
            return "RETRYABLE_ERROR", f"server_error_{status_code}"
        # 400 (bad request), 401 (bad key), 403 (key/project lacks access), and any
        # other 4xx -- an unchanged request can't start succeeding on retry.
        return "PERMANENT_ERROR", code or f"http_{status_code}"

    def _classify_exception(self, exc: Exception, model: str) -> tuple[LLMStatus, str]:
        if is_policy_error(exc):
            # The transport guard refused the request -- never retried.
            logger.error("OpenAI policy guard tripped: %s", exc)
            return "PERMANENT_ERROR", "privacy_guard"
        if isinstance(exc, APIStatusError):
            return self._classify_status_error(exc, model)
        if isinstance(exc, APITimeoutError):
            return "RETRYABLE_ERROR", "timeout"
        if isinstance(exc, APIConnectionError):
            cause = exc.__cause__ or exc
            # The cause's message says what actually broke (e.g. which header httpcore
            # rejected). It can echo request headers, so any key-shaped text is masked.
            logger.warning(
                "OpenAI connection error: %s: %s",
                type(cause).__name__,
                _KEY_PATTERN.sub("sk-***", str(cause)),
            )
            return "RETRYABLE_ERROR", f"network_error:{type(cause).__name__}"
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            return "RETRYABLE_ERROR", "timeout"
        if isinstance(exc, RuntimeError) and str(exc).startswith("Refusing to send"):
            # `_assert_private` tripped -- a bug on our side, never retried.
            logger.error("%s", exc)
            return "PERMANENT_ERROR", "privacy_guard"
        logger.warning("Unexpected error calling OpenAI: %s", exc, exc_info=True)
        return "RETRYABLE_ERROR", f"unexpected_error:{type(exc).__name__}"

    # -- budget + usage logging (rule 4) -----------------------------------------

    @staticmethod
    def _cost_usd(result: LLMResult, model: str) -> float:
        """Estimated spend from token counts x `OPENAI_PRICES` for `model`."""
        input_price, cached_price, output_price = price_for(model)
        cached = min(result.cached_tokens, result.request_tokens)
        uncached = result.request_tokens - cached
        return (
            uncached * input_price + cached * cached_price + result.response_tokens * output_price
        ) / 1_000_000

    def _over_budget(self) -> bool:
        """The pre-send budget check. `AIRouter` already skips a provider that's over
        budget, but checking here too means no caller can send an OpenAI call that
        skips it. No DB session -> no usage to meter (QuotaManager fails open), the
        same convention as usage logging below."""
        if self._db is None:
            return False
        if QuotaManager(settings=self._settings, db=self._db).within_budget(self.name):
            return False
        logger.error("OpenAI call not sent: %s", _spend_stop_message("app_budget_exhausted"))
        return True

    @staticmethod
    def _failure(status: LLMStatus, error_type: str, start: float) -> LLMResult:
        result = LLMResult(status=status, error_type=error_type, latency_ms=_elapsed_ms(start))
        if error_type == "app_budget_exhausted" or error_type.startswith("billing:"):
            result.spend_stop = _spend_stop_message(error_type.removeprefix("billing:"))
        return result

    def _log_usage(self, result: LLMResult, request: LLMRequest, fallback_used: bool) -> None:
        """One `llm_usage` row per call, success or failure -- same convention as
        GeminiProvider._log_usage -- with the job, the model, cached tokens and the
        estimated cost QuotaManager meters the USD budget against."""
        if self._db is None:
            return
        model = self.model_for(request)
        self._db.add(
            LLMUsage(
                provider=self.name,
                model=model,
                job=request.job,
                request_tokens=result.request_tokens,
                response_tokens=result.response_tokens,
                cached_tokens=result.cached_tokens,
                cost_usd=self._cost_usd(result, model),
                status=result.status,
                error_type=result.error_type,
                latency=int(result.latency_ms),
                fallback_used=fallback_used,
            )
        )
        self._db.commit()

    # -- public API --------------------------------------------------------------

    async def generate(self, request: LLMRequest, *, fallback_used: bool = False) -> LLMResult:
        result = await self._generate(request)
        self._log_usage(result, request, fallback_used=fallback_used)
        return result

    def _blocked(self, start: float) -> LLMResult | None:
        """A failure result if the call must not be sent at all, else None."""
        if not self.is_available():
            return self._failure("PERMANENT_ERROR", "missing_api_key", start)
        if self._over_budget():
            return self._failure("QUOTA_EXHAUSTED", "app_budget_exhausted", start)
        return None

    async def _generate(self, request: LLMRequest) -> LLMResult:
        start = time.monotonic()
        blocked = self._blocked(start)
        if blocked is not None:
            return blocked

        params = self._build_params(request, stream=False)
        model = params["model"]
        max_attempts = max(1, self._settings.openai_max_retries + 1)
        error_type = "unknown_error"

        for attempt in range(1, max_attempts + 1):
            try:
                response = await self._send(params)
                if getattr(response, "status", None) == "failed":
                    error = getattr(response, "error", None)
                    status, error_type = self._classify_code(
                        getattr(error, "code", None), model, getattr(error, "message", None)
                    )
                    if status != "RETRYABLE_ERROR":
                        return self._failure(status, error_type, start)
                else:
                    return self._to_result(response, request, start)
            except Exception as exc:  # noqa: BLE001 -- classified below, never raised
                status, error_type = self._classify_exception(exc, model)
                if status != "RETRYABLE_ERROR":
                    return self._failure(status, error_type, start)

            if attempt < max_attempts:
                await asyncio.sleep(
                    self._settings.openai_retry_base_delay_seconds * (2 ** (attempt - 1))
                )

        logger.warning("OpenAI call failed after %d attempt(s): %s", max_attempts, error_type)
        return self._failure("RETRYABLE_ERROR", error_type, start)

    async def generate_stream(
        self, request: LLMRequest, *, fallback_used: bool = False
    ) -> AsyncIterator[LLMStreamChunk]:
        """Streaming counterpart to `generate()` -- same contract as every provider's:
        text deltas as they arrive, then exactly one terminal chunk, never raising, one
        `llm_usage` row.

        A RETRYABLE failure is retried (with backoff) only while nothing has been
        emitted; once a delta has reached the client a retry would duplicate it, so the
        failure is returned as-is.
        """
        start = time.monotonic()
        blocked = self._blocked(start)
        if blocked is not None:
            self._log_usage(blocked, request, fallback_used=fallback_used)
            yield LLMStreamChunk(final=blocked)
            return

        params = self._build_params(request, stream=True)
        model = params["model"]
        max_attempts = max(1, self._settings.openai_max_retries + 1)
        emitted = False
        result: LLMResult | None = None

        for attempt in range(1, max_attempts + 1):
            text_parts: list[str] = []
            final_response: Any = None
            failure: tuple[LLMStatus, str] | None = None

            try:
                stream = await self._send(params)
                async for event in stream:
                    event_type = getattr(event, "type", None)
                    if event_type in ("response.output_text.delta", "response.refusal.delta"):
                        delta = event.delta or ""
                        if delta:
                            text_parts.append(delta)
                            emitted = True
                            yield LLMStreamChunk(delta=delta)
                    elif event_type in ("response.completed", "response.incomplete"):
                        final_response = event.response
                    elif event_type == "response.failed":
                        error = getattr(event.response, "error", None)
                        failure = self._classify_code(
                            getattr(error, "code", None), model, getattr(error, "message", None)
                        )
                    elif event_type == "error":
                        failure = self._classify_code(
                            getattr(event, "code", None), model, getattr(event, "message", None)
                        )
            except Exception as exc:  # noqa: BLE001 -- classified below, never raised
                failure = self._classify_exception(exc, model)

            if failure is None and final_response is not None:
                result = self._to_result(final_response, request, start)
                streamed = "".join(text_parts)
                if streamed:
                    # The streamed text is what the user already saw -- keep the final
                    # result consistent with it. Text reached them, so a truncated
                    # (`incomplete`) reply still counts as an answer.
                    result.text = streamed
                    if result.status != "SUCCESS":
                        result.status, result.error_type = "SUCCESS", None
                break

            status, error_type = failure or ("RETRYABLE_ERROR", "stream_ended_without_result")
            result = self._failure(status, error_type, start)
            if status != "RETRYABLE_ERROR" or emitted or attempt == max_attempts:
                break
            await asyncio.sleep(self._settings.openai_retry_base_delay_seconds * (2 ** (attempt - 1)))

        assert result is not None  # the loop always runs at least once
        result.latency_ms = _elapsed_ms(start)
        self._log_usage(result, request, fallback_used=fallback_used)
        yield LLMStreamChunk(final=result)
