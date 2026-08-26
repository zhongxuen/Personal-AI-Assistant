"""
Provider chain configuration (§37 Phase 5, files 06-07).

`AIRouter` needs an ordered, enabled/disabled list of providers to walk on every
request -- this module is where that chain is assembled and owned. `AIRouter` itself
never hardcodes provider names/order/priority; it only asks `get_chain()` for whatever
is currently enabled, in priority order, and tries each in turn.

Two providers are registered: `GeminiProvider` at priority 1, `OllamaProvider` at
priority 2 as its local fallback. Each has its own `*_enabled` settings flag
(`ollama_enabled`, default True) an operator can use to pull it out of the chain
entirely without touching code -- distinct from `is_available()`, which each provider
still checks per-request regardless of `enabled`. Adding a third provider is the same
shape: one more `ProviderEntry` here, no changes to `AIRouter`'s chain-walking logic.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.llm.base import LLMProvider
from app.llm.gemini import GeminiProvider
from app.llm.ollama import OllamaProvider


@dataclass
class ProviderEntry:
    """One entry in the chain. `priority` is ascending -- lower runs first.
    `enabled` lets an operator (or a future settings toggle) pull a provider out of
    the chain without deleting its configuration.
    """

    provider: LLMProvider
    priority: int
    enabled: bool = True


class ProviderManager:
    """Owns the provider chain. Construct with `entries=` to inject a custom chain
    (tests, or a future settings-driven registry); otherwise it builds the current
    real chain from `Settings` -- `GeminiProvider` at priority 1, `OllamaProvider` at
    priority 2, each gated by its own `*_enabled` setting.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        db: Session | None = None,
        entries: list[ProviderEntry] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        if entries is not None:
            self._entries = list(entries)
        else:
            self._entries = [
                ProviderEntry(
                    provider=GeminiProvider(settings=self._settings, db=db),
                    priority=1,
                    enabled=True,
                ),
                ProviderEntry(
                    provider=OllamaProvider(settings=self._settings, db=db),
                    priority=2,
                    enabled=self._settings.ollama_enabled,
                ),
            ]

    def get_chain(self) -> list[LLMProvider]:
        """Enabled providers only, ordered by ascending priority (lower first). This
        is the *only* thing `AIRouter` reads from this class -- it has no opinion on
        quota/health, just "what's the configured order".
        """
        return [
            entry.provider
            for entry in sorted(self._entries, key=lambda entry: entry.priority)
            if entry.enabled
        ]

    def all_provider_names(self) -> list[str]:
        """Every configured provider's name, in priority order, *including* disabled
        ones (unlike `get_chain()`). `AIRouter` has no use for a disabled provider,
        but the usage/status dashboard (`app.api.routes.llm_usage`) does -- an
        operator who flipped `ollama_enabled=False` should still see Ollama listed as
        disabled, not have it silently disappear from the panel.
        """
        return [entry.provider.name for entry in sorted(self._entries, key=lambda entry: entry.priority)]
