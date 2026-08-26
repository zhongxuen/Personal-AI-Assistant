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
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
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

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance — import and call this, don't instantiate Settings() directly."""
    return Settings()
