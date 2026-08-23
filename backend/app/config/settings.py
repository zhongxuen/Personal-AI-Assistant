"""
Application configuration.

Settings are loaded from environment variables / a `.env` file at the
repository root, so the app behaves the same whether uvicorn is launched
from `backend/` or from the repo root. Nothing here is provider-specific
(no Gemini/Ollama config yet) — that arrives in later phases per
docs/../md-files/development-plan.md.
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

    # Placeholders only — unused until file 05 (Gemini) / 07 (Ollama fallback).
    # Declared now so .env.example is complete from day one (see 01-project-foundation.md).
    gemini_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance — import and call this, don't instantiate Settings() directly."""
    return Settings()
