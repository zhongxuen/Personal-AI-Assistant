"""
Application configuration.

Settings are loaded from environment variables / a `.env` file at the
repository root, so the app behaves the same whether uvicorn is launched
from `backend/` or from the repo root. Provider-specific config: Gemini
arrived in file 05, Ollama in file 07 — see md-files/development-plan.md.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config/settings.py -> repo root is 3 levels up
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173"

    database_url: str = "sqlite:///./jarvis.db"

    # Gemini (file 05). `gemini_api_key` unset/empty -> GeminiProvider.is_available()
    # returns False without a network call (§41 Rule 3). `gemini_model` is deliberately
    # configurable, not hardcoded (§30) -- swap models via env, not code.
    #
    # The default was "gemini-2.5-flash" until 2026-08-30, when Google retired it:
    # generateContent answers HTTP 404 "no longer available to new users ... use
    # models/gemini-3.6-flash". GeminiProvider classifies 404 as PERMANENT_ERROR and
    # HealthManager turns repeats into a sticky MISCONFIGURED, so a retired default
    # silently drops Gemini out of the provider chain on any environment that doesn't
    # set GEMINI_MODEL. Keep this default on a model the key can actually reach.
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.6-flash"
    gemini_timeout_seconds: float = 30.0
    # Retries apply only to RETRYABLE_ERROR (timeout/network/5xx) -- never
    # QUOTA_EXHAUSTED or PERMANENT_ERROR (§5). This is a *retry* count, so
    # gemini_max_retries=2 means up to 3 total attempts.
    gemini_max_retries: int = 2
    gemini_retry_base_delay_seconds: float = 1.0

    # Ollama fallback (file 07). `ollama_model` must be a model actually pulled on the
    # local server -- OllamaProvider.is_available() checks this against the server's
    # own model list rather than assuming, same spirit as Gemini's key check but via a
    # (short-timeout) network probe, since a local service can't be assumed installed
    # or running the way "an env var is set" can (§3 of the development plan).
    # `ollama_enabled` is the operator-facing on/off switch `ProviderManager` reads to
    # decide whether OllamaProvider's chain entry is included at all -- separate from
    # `is_available()`, which still governs whether an *enabled* entry gets called on
    # any given request.
    ollama_enabled: bool = True
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    # Timeout for the actual chat/generate call -- deliberately separate from
    # ollama_availability_timeout_seconds below, since generation legitimately takes
    # much longer than a health-check GET.
    ollama_timeout_seconds: float = 30.0
    # Short timeout for is_available()'s GET to /api/tags -- this runs before every
    # generate() call (and may run from AIRouter probing multiple providers), so it
    # must fail fast rather than hang waiting on a service that isn't there.
    ollama_availability_timeout_seconds: float = 2.0
    # Same *retry count* convention as gemini_max_retries -- applies only to
    # RETRYABLE_ERROR, and ollama_max_retries=2 means up to 3 total attempts.
    ollama_max_retries: int = 2
    ollama_retry_base_delay_seconds: float = 1.0

    # Gemini usage budget (§8, file 06+). These are *internal* budgets the app
    # enforces on itself, deliberately set BELOW Google's actual quota -- they are
    # placeholders, NOT Google's real Gemini rate limits (§41 Rule 9: never hardcode
    # the real provider quota, since it can change without notice). Configure via env
    # to match whatever the account's actual quota/comfort margin is.
    gemini_daily_request_budget: int = 80
    # Fraction of the daily budget (0-1) at which QuotaManager.status() reports
    # WARNING instead of NORMAL.
    gemini_warning_threshold: float = 0.80
    # Fraction of the daily budget (0-1) at which QuotaManager.status() reports
    # CRITICAL instead of WARNING. At/above the full budget (1.0) status is FAILOVER.
    gemini_critical_threshold: float = 0.90

    # Provider health (§6, file 06). Provider-agnostic -- `HealthManager` applies these
    # to whichever provider name it's tracking (gemini, ollama, ...), not just Gemini.
    # Cooldown after a QUOTA_EXHAUSTED result -- never retried before this elapses.
    llm_quota_cooldown_seconds: float = 60.0
    # Consecutive RETRYABLE_ERRORs (timeout/network/5xx) before a provider is treated
    # as temporarily unusable rather than just unlucky once.
    llm_retryable_error_threshold: int = 3
    llm_retryable_cooldown_seconds: float = 30.0
    # Consecutive PERMANENT_ERRORs before a merely MISCONFIGURED provider is escalated
    # to DISABLED (i.e. stops being retried even after a config fix, until reset()).
    llm_permanent_error_threshold: int = 3

    # Speech-to-text (§24, §25, file 10). Local-only by default -- faster-whisper runs
    # entirely on-device, no API key (development plan §25: "Prefer local/free speech
    # processing where practical to maintain the zero-cost objective"). Model size
    # trades accuracy for load time/memory ("tiny" .. "large-v3"); compute_type
    # "int8" keeps CPU-only inference fast without a GPU.
    stt_whisper_model_size: str = "base"
    stt_whisper_device: str = "cpu"
    stt_whisper_compute_type: str = "int8"

    # Text-to-speech (§24, §25, file 10). pyttsx3 drives each OS's native offline
    # voice (SAPI5/NSSpeechSynthesizer/espeak) -- no API key, no per-request cost.
    # tts_voice_id left unset uses the OS default voice; set to one of the voice ids
    # `pyttsx3.init().getProperty("voices")` reports to pick a specific one.
    tts_rate_wpm: int = 175
    tts_volume: float = 1.0
    tts_voice_id: str | None = None

    # File-tool sandbox (§33, file 11). Comma-separated allow-list of base directories
    # `app/tools/files.py` (via `app/tools/path_safety.py`) may touch -- same
    # string-plus-derived-list convention as `cors_origins` above, so it's configurable
    # via env/.env without a code change (§30). Left empty by default;
    # `allowed_file_directories_list` falls back to the user's home Desktop/Documents
    # folders so the file tools are useful out of the box without granting free rein
    # over the whole disk -- tighten or widen via env as needed.
    allowed_file_directories: str = ""

    # Authentication (§34, file 12 prompt 1). HS256 JWT issued by POST /api/auth/login
    # and required by `app.api.dependencies.get_current_user` on every non-desktop-local
    # route -- see docs/security.md. `auth_secret_key`'s default is intentionally
    # obviously-insecure so it's never mistaken for a real secret; it MUST be
    # overridden via env for any deployment reachable from outside this machine (file
    # 12's Vercel/web deployment) -- `main.py`'s startup logs a warning if it's still
    # the default outside `app_env == "development"`.
    auth_secret_key: str = "dev-only-insecure-secret-change-me"
    auth_algorithm: str = "HS256"
    auth_token_expire_minutes: int = 60 * 24  # 24h -- personal single-user session, not a short-lived web SSO token

    # Optional bootstrap user (§34): if both are set and no user with this username
    # exists yet, `main.py`'s startup creates it via `AuthService.seed_default_user`.
    # Deliberately env-configured rather than a public /api/auth/register endpoint --
    # this app has exactly one personal user today and open self-registration isn't
    # appropriate for it (see app/auth/service.py's docstring).
    auth_seed_username: str | None = None
    auth_seed_password: str | None = None

    # Discord adapter (§37 Phase 12, file 13). Backend-only secret -- never sent to
    # any frontend, never committed as a real value (see .env.example). Unset ->
    # `DiscordBotManager.start()` (app/platforms/discord.py) no-ops rather than trying
    # to connect, so a dev machine without a Discord app configured is unaffected (same
    # "absence is a valid, non-crashing state" convention as gemini_api_key above).
    discord_bot_token: str | None = None

    # WhatsApp Cloud API adapter (§37 Phase 13, file 18). Four backend-only values, all
    # from Meta's Developer dashboard -- see docs/deployment.md's "WhatsApp Cloud API
    # setup" for where each one is found. Same "absence is a valid, non-crashing state"
    # convention as discord_bot_token above: any of these unset -> the WhatsApp feature
    # no-ops rather than erroring, so a dev machine with no Meta app configured is
    # unaffected. None of them ever crosses the frontend boundary (docs/security.md) --
    # unlike `vapid_public_key` below, there is no half of this that a browser needs.
    #
    #   - `whatsapp_access_token` authorizes our outbound Graph API calls. Meta's
    #     dashboard hands out a 24-hour temporary token (fine for early dev, expires
    #     silently) and a non-expiring System User token (what any deployment should
    #     use) -- this setting takes whichever, since they're the same kind of bearer
    #     credential to us.
    #   - `whatsapp_phone_number_id` is the *Cloud API's* id for the sending number
    #     (a numeric string), not the number itself -- outbound sends POST to
    #     `/{phone_number_id}/messages`. Meta's free test number has one of these too,
    #     so development needs no purchased number.
    #   - `whatsapp_verify_token` is a string *we* choose and type into both this env
    #     and Meta's webhook config; Meta echoes it back on the GET handshake so the
    #     webhook route can prove the subscription request is for this deployment.
    #     Not issued by Meta, but still a shared secret -- a guessable value lets
    #     anyone re-point a webhook subscription at us.
    #   - `whatsapp_app_secret` is the Meta *app* secret used to verify the
    #     `X-Hub-Signature-256` HMAC on every inbound webhook POST. It's what
    #     establishes caller identity on the inbound path (the role Discord's bot
    #     token plays before `DiscordAdapter.to_request()` runs), so unset must mean
    #     "reject/no-op", never "skip the check".
    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_verify_token: str | None = None
    whatsapp_app_secret: str | None = None

    # Web push (browser notifications). VAPID is an asymmetric key pair, so the two
    # halves have deliberately *different* exposure rules -- unlike every other secret
    # in this file, which is uniformly backend-only:
    #   - `vapid_private_key` is backend-only, exactly like discord_bot_token above:
    #     it signs the VAPID JWT sent to the push service, and nothing else ever needs
    #     it. Never sent to any frontend, never committed as a real value.
    #   - `vapid_public_key` is deliberately *also* exposed to the frontend. The
    #     browser cannot subscribe without it: `PushManager.subscribe()` takes it as
    #     `applicationServerKey`, and the push service later uses it to verify the
    #     signature on our push requests. That's what a public key is for -- shipping
    #     it to the browser leaks nothing, since a subscription created with it can
    #     still only be pushed to by whoever holds the matching private key. So the
    #     usual "secrets never cross the frontend boundary" rule (docs/security.md)
    #     doesn't apply to this one field, and that's by design of the Web Push spec,
    #     not an oversight.
    # Either one unset -> the feature no-ops rather than erroring, same "absence is a
    # valid, non-crashing state" convention as discord_bot_token/gemini_api_key: the
    # push routes still accept and store subscriptions (harmless), and any later
    # delivery path has nothing to sign with and simply doesn't send. Generate a pair
    # with `vapid --gen` (py_vapid, installed with pywebpush) or any P-256 keygen, and
    # keep both base64url-encoded, unpadded.
    vapid_public_key: str | None = None
    vapid_private_key: str | None = None
    # The VAPID `sub` claim: a contact URI ("mailto:..." or "https://...") the push
    # service can use to reach whoever operates this backend if our pushes misbehave.
    # Required by the spec on every signed request (py_vapid refuses to sign without
    # it), which is why this one has a default instead of following the `None` ->
    # no-op pattern above: it is a contact address, not a secret, and a wrong-but-
    # present value degrades to "the push service can't reach us", not "nothing sends".
    # Override it with a real address on any deployment that pushes to real devices.
    vapid_subject: str = "mailto:jarvis@localhost"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def allowed_file_directories_list(self) -> list[str]:
        configured = [d.strip() for d in self.allowed_file_directories.split(",") if d.strip()]
        if configured:
            return configured
        return [str(Path.home() / "Desktop"), str(Path.home() / "Documents")]


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance — import and call this, don't instantiate Settings() directly."""
    return Settings()
