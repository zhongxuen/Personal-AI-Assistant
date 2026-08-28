# LLM Providers

Two `LLMProvider` implementations exist so far: `GeminiProvider` (file 05,
`backend/app/llm/gemini.py`) and `OllamaProvider` (file 07,
`backend/app/llm/ollama.py`). Both speak the provider-agnostic contract in
`backend/app/llm/base.py` -- `AssistantCore`/`AIRouter` never touch a vendor-specific
type. This section covers each provider in isolation, then how they're registered
into the chain `AIRouter` actually walks.

## Provider chain (`ProviderManager`)

`backend/app/llm/provider_manager.py` assembles the ordered, enabled/disabled list of
providers `AIRouter` fails over across (`backend/app/llm/ai_router.py`). Today's
chain:

| Priority | Provider  | Enable/disable via     | Default   |
|---------:|-----------|-------------------------|-----------|
| 1        | Gemini    | `GEMINI_API_KEY` unset -> `is_available()` returns False (no separate on/off flag) | -- |
| 2        | Ollama    | `OLLAMA_ENABLED`        | `true`    |

Lower priority number runs first. `AIRouter.route()` walks the chain in that order,
skipping any provider that's disabled, over its quota budget, or currently unhealthy,
and calling the rest until one returns `SUCCESS` -- see `ai_router.py`'s docstring for
the full skip/fallback-logging behavior. `AIRouter` has no provider-specific branches
for either provider; it only reads `ProviderManager.get_chain()`.

`OLLAMA_ENABLED=false` removes Ollama's chain entry entirely (Gemini-only chain, no
failover target) -- distinct from Ollama simply being unreachable, which instead makes
its *enabled* entry skip itself per-request via `is_available()`/the pre-flight probe
in `generate()`. Use `OLLAMA_ENABLED=false` when there's no local Ollama server at all
and you'd rather not pay the (short-timeout) probe on every request; leave it `true`
(default) whenever a local server might be running.

## Gemini (`GeminiProvider`)

- Config: `GEMINI_API_KEY`, `GEMINI_MODEL` (default `gemini-2.5-flash`),
  `GEMINI_TIMEOUT_SECONDS`, `GEMINI_MAX_RETRIES`.
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
