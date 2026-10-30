"""
Provider chain configuration (§37 Phase 5, files 06-07).

`AIRouter` needs an ordered, enabled/disabled list of providers to walk on every
request -- this module is where that chain is assembled and owned. `AIRouter` itself
never hardcodes provider names/order/priority; it only asks `get_chain()` for whatever
is currently enabled, in priority order, and tries each in turn.

Three providers are registered:

  1. `OpenAIProvider` -- primary. Every request is sent with store=False (see
     `app.llm.openai`), so nothing is retained on OpenAI.
  2. `OllamaProvider` -- local fallback; nothing leaves the machine at all.
  3. `GeminiProvider` -- last, and **off by default** (`gemini_enabled=False`). The
     free Gemini tier lets Google use submitted content to improve its products, so
     it must never be something a turn silently fails over to; an operator opts in.

Each has its own `*_enabled` settings flag an operator can use to pull it out of the
chain entirely without touching code -- distinct from `is_available()`, which each
provider still checks per-request regardless of `enabled`. Adding another provider is
the same shape: one more `ProviderEntry` here, no changes to `AIRouter`.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.llm.base import LLMProvider
from app.llm.gemini import GeminiProvider
from app.llm.ollama import OllamaProvider
from app.llm.openai import OpenAIProvider


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
    real chain from `Settings` -- OpenAI (1), Ollama (2), Gemini (3), each gated by its
    own `*_enabled` setting (see the module docstring for why Gemini defaults off).
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
                    provider=OpenAIProvider(settings=self._settings, db=db),
                    priority=1,
                    enabled=self._settings.openai_enabled,
                ),
                ProviderEntry(
                    provider=OllamaProvider(settings=self._settings, db=db),
                    priority=2,
                    enabled=self._settings.ollama_enabled,
                ),
                ProviderEntry(
                    provider=GeminiProvider(settings=self._settings, db=db),
                    priority=3,
                    enabled=self._settings.gemini_enabled,
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
