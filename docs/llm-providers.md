# LLM Providers

Three `LLMProvider` implementations exist: `OpenAIProvider`
(`backend/app/llm/openai.py`, the primary), `OllamaProvider` (file 07,
`backend/app/llm/ollama.py`, the local fallback) and `GeminiProvider` (file 05,
`backend/app/llm/gemini.py`, opt-in only). All speak the provider-agnostic contract in
`backend/app/llm/base.py` -- `AssistantCore`/`AIRouter` never touch a vendor-specific
type. This section covers how they're registered into the chain `AIRouter` walks, then
each provider in isolation.

## Provider chain (`ProviderManager`)

`backend/app/llm/provider_manager.py` assembles the ordered, enabled/disabled list of
providers `AIRouter` fails over across (`backend/app/llm/ai_router.py`). Today's
chain:

| Priority | Provider  | Enable/disable via     | Default   |
|---------:|-----------|-------------------------|-----------|
| 1        | OpenAI    | `OPENAI_ENABLED` (and `OPENAI_API_KEY` unset -> `is_available()` False) | `true` |
| 2        | Ollama    | `OLLAMA_ENABLED`        | `true`    |
| 3        | Gemini    | `GEMINI_ENABLED`        | `false`   |

Gemini is deliberately **off by default and last**: its free tier lets Google use
submitted content to improve its products, so it must never be a turn's *silent*
failover target. Turn it on only with a paid key, or if that trade-off is acceptable.

Lower priority number runs first. `AIRouter.route()` walks the chain in that order,
skipping any provider that's disabled, over its quota budget, or currently unhealthy,
and calling the rest until one returns `SUCCESS` -- see `ai_router.py`'s docstring for
the full skip/fallback-logging behavior. `AIRouter` has no provider-specific branches
for either provider; it only reads `ProviderManager.get_chain()`.

`OLLAMA_ENABLED=false` removes Ollama's chain entry entirely (OpenAI-only chain with
Gemini off, no failover target) -- distinct from Ollama simply being unreachable, which instead makes
its *enabled* entry skip itself per-request via `is_available()`/the pre-flight probe
in `generate()`. Use `OLLAMA_ENABLED=false` when there's no local Ollama server at all
and you'd rather not pay the (short-timeout) probe on every request; leave it `true`
(default) whenever a local server might be running.

## OpenAI (`OpenAIProvider`)

- Config: `OPENAI_API_KEY`, `OPENAI_MODEL` (default `gpt-5.4-mini` -- must be a model the
  key can use), `OPENAI_TIMEOUT_SECONDS` (30), `OPENAI_MAX_RETRIES` (1),
  `OPENAI_RETRY_BASE_DELAY_SECONDS` (0.4), optional `OPENAI_REASONING_EFFORT`.
- **Privacy, enforced in code** (tests: `tests/llm/test_openai_privacy.py`): every call
  uses the Responses API with `store=False`, built by one helper (`_build_params`) and
  sent by one other (`_send`, which refuses anything else). No `previous_response_id`,
  Conversations API, Files API, vector stores, Assistants/threads or background mode --
  history is resent by us on every request, attachments go inline as base64
  (`input_file`/`input_image`), and `metadata` is never set. Multi-step tool use with a
  reasoning model works statelessly via `include=["reasoning.encrypted_content"]`: we
  hold the encrypted reasoning items and replay them on the next step.
- Prompt layout is static-first so OpenAI's automatic prompt caching applies: fixed
  `instructions` -> tools (strict, sorted by name) -> a developer message with the
  per-request context (time, platform, memory) -> recent turns as real user/assistant
  items -> the user message. `prompt_cache_key` is a hash of the user id. Cached input
  tokens are logged to `llm_usage.cached_tokens`.
- Tools are sent as strict function tools: every object closed, every property
  required, originally-optional properties made nullable (nulls are dropped again
  before `ToolExecutor` sees the call). A free-form object property, which strict mode
  can't express, travels as a JSON-encoded string and is decoded back.
- Error classification mirrors Gemini's: 429 / `insufficient_quota` -> `QUOTA_EXHAUSTED`;
  timeout / connection error / 5xx -> `RETRYABLE_ERROR` (retried with backoff; when
  streaming, only until the first delta); 404 -> `PERMANENT_ERROR`
  `model_not_found:<OPENAI_MODEL>`; 400/401/403 -> `PERMANENT_ERROR`.
- **Budget is spend, not requests.** Each `llm_usage` row carries `cost_usd`, estimated
  from token counts x `OPENAI_INPUT_PRICE_PER_MTOK` / `OPENAI_CACHED_INPUT_PRICE_PER_MTOK`
  / `OPENAI_OUTPUT_PRICE_PER_MTOK`. Those prices are placeholders -- keep them in sync
  with OpenAI's pricing page for the configured model. `QuotaManager` sums today's (and,
  if `OPENAI_MONTHLY_BUDGET_USD` is set, this month's) cost against
  `OPENAI_DAILY_BUDGET_USD` with the usual NORMAL/WARNING/CRITICAL/FAILOVER thresholds.
- It is the only provider with `supports_tool_loop = True`: `AssistantCore` sends tool
  results back to it until it answers in text or `LLM_MAX_TOOL_STEPS` rounds have run.

## Gemini (`GeminiProvider`)

- **Opt-in** (`GEMINI_ENABLED=true`), priority 3 -- see the chain table above for why.

- Config: `GEMINI_API_KEY`, `GEMINI_MODEL` (default `gemini-3.6-flash`),
  `GEMINI_TIMEOUT_SECONDS` (default 15), `GEMINI_MAX_RETRIES` (default 1).
- Those two defaults are tuned for someone waiting on a chat reply rather than for
  maximum persistence. They were 30s and 2 retries, which combined with backoff let a
  single failing message occupy ~93 seconds before Gemini gave up and Ollama was even
  tried. Failover to a local model is a better use of the user's next second than a
  third attempt at a service that has already failed twice -- failover *is* the retry.
- `is_available()` is a pure local check -- `GEMINI_API_KEY` unset/empty means
  unavailable, with no network call.
- Error classification: HTTP 429 / `RESOURCE_EXHAUSTED` -> `QUOTA_EXHAUSTED` (never
  retried); timeout / network blip / 5xx -> `RETRYABLE_ERROR` (retried with
  exponential backoff up to `GEMINI_MAX_RETRIES`); HTTP 404 / `NOT_FOUND` ->
  `PERMANENT_ERROR` with `error_type` `model_not_found:<GEMINI_MODEL>` (the model name
  is carried through so the Provider Status page says *which* model is wrong);
  missing/invalid key or other 4xx -> `PERMANENT_ERROR`.
- A `PERMANENT_ERROR` is the most disruptive outcome of the three, because
  `HealthManager` marks the provider `MISCONFIGURED` on the *first* one and that state
  is sticky -- no cooldown clears it, so `AIRouter` skips the provider for the rest of
  the process's life and every request falls through to "I can't reach any reasoning
  provider right now". Fix the config, then either restart the backend or clear the
  state in place with `POST /api/diagnostics/providers/{name}/reset` (authenticated;
  surfaced as the **Reset health** button on an unhealthy provider's card in the
  Provider Status page). The reset is bookkeeping only -- it doesn't call the provider
  or change any config, so if the root cause is still there the next request re-benches
  the provider immediately.
- `PERMANENT_ERROR`s do *not* count against `GEMINI_DAILY_REQUEST_BUDGET`
  (`QuotaManager._UNBILLED_STATUSES`): the real provider quota isn't charged for a
  rejected key or an unknown model, so neither is the internal budget. They still show
  up in the Provider Status page's request/failure counts, which report every attempt.
- No tool-result loop: Gemini keeps the single-shot behavior (its tool calls run once
  and their results are reported directly). If a turn fails over to it mid-loop, the
  earlier tool results reach it as `tool_results` in its context. Attachments are not
  sent to it.

## Ollama (`OllamaProvider`)

- Config: `OLLAMA_ENABLED` (default `true` -- set `false` to remove Ollama from the
  chain entirely, see [Provider chain](#provider-chain-providermanager) below),
  `OLLAMA_BASE_URL` (default `http://localhost:11434`), `OLLAMA_MODEL`
  (default `llama3.2` -- must be a model already `ollama pull`-ed on that server),
  `OLLAMA_TIMEOUT_SECONDS` (generation call timeout), `OLLAMA_AVAILABILITY_TIMEOUT_SECONDS`
  (short timeout for the health-check probe below), `OLLAMA_MAX_RETRIES`.
- **Availability is never assumed.** Unlike Gemini, Ollama is a local service that may
  not be installed or running at all, so `is_available()` performs a short-timeout GET
  against the server's `/api/tags` endpoint rather than a local-only check. Any
  connection error, DNS failure, or timeout is treated as "not available" and never
  raises. If the server *is* reachable but `OLLAMA_MODEL` isn't in the returned model
  list, the provider is treated as available-but-misconfigured -- a `generate()` call
  fails fast with `PERMANENT_ERROR`/`model_not_found` instead of surfacing as a
  confusing mid-request error.
- Error classification: server unreachable or model not pulled -> `PERMANENT_ERROR`
  (`ollama_unreachable` / `model_not_found`); timeout / transient connection failure /
  HTTP 5xx -> `RETRYABLE_ERROR`, retried with the same exponential-backoff pattern as
  Gemini. `QUOTA_EXHAUSTED` is part of the shared status taxonomy but realistically
  never returned here -- a local model has no request quota to exhaust.
- **Tool/function-calling limitation:** not every model Ollama can run supports tool
  calling. When the configured model doesn't, `/api/chat` rejects a request that
  includes a `tools` payload with an HTTP 400 instead of silently ignoring it.
  `OllamaProvider.generate()` detects this and retries once without `tools`, so the
  caller still gets a normal `SUCCESS` reply -- just with an empty `tool_calls` list --
  rather than the whole turn failing. If tool calling matters for a given request,
  pick a model known to support it (e.g. `llama3.1`, `qwen2.5`, `mistral-nemo`) rather
  than relying on this fallback.

- Like Gemini: no tool-result loop (single-shot tool calls; earlier loop results reach
  it as `tool_results` in its system context on a mid-loop failover), and attachments
  are not sent to it.

## Streaming

All three providers implement `generate_stream()`, the incremental form of
`generate()`: it yields `LLMStreamChunk` text deltas as the model produces them, then
exactly one terminal chunk carrying the complete `LLMResult`. `AIRouter.route_stream()`
walks the same chain, with the same quota and health gating, and
`AssistantCore.handle_stream()` turns the result into `AssistantStreamEvent`s for
`POST /api/assistant/stream` (see `docs/architecture.md`).

Why it exists: without streaming, time-to-first-character equals the entire generation
time, so the chat sits on a motionless typing indicator for the whole turn. That is
worst on the Ollama fallback, where a local CPU model routinely takes 15-20 seconds for
a couple of sentences -- the slowest replies the assistant gives are exactly the ones
that showed no sign of life. Total duration is unchanged; when the user first sees
something is not.

Three rules the implementations share, and any future provider must too:

- **Exactly one terminal chunk, always.** On success and on every failure alike. A
  stream must never raise where `generate()` would have returned a classified
  non-SUCCESS `LLMResult` -- by then HTTP 200 and some response body are already sent,
  so an exception can't become an error status any more.
- **Log `llm_usage` exactly once**, same as `generate()`. Otherwise streaming would be
  a way to spend provider quota without it counting against the internal budget.
- **No retries once text is out.** Once a delta has been sent it cannot be unsent, so
  retrying would duplicate visible text. (`OpenAIProvider` retries a RETRYABLE failure
  only while nothing has been emitted; Gemini and Ollama don't retry streams at all.)
  `AIRouter.route_stream` fails over instead -- but only while nothing has been emitted
  yet. A provider that fails *after* emitting text ends the
  turn with its own error rather than letting a second provider overwrite what the user
  is already reading.

`generate_stream` is optional on the `LLMProvider` protocol: `route_stream` falls back
to buffering `generate()` into a single terminal chunk for any provider that lacks it,
so such a provider stays fully usable and simply contributes no early text.
