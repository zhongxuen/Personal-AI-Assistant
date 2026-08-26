"""
Provider chain configuration (§37 Phase 5, file 06).

`AIRouter` needs an ordered, enabled/disabled list of providers to walk on every
request -- this module is where that chain is assembled and owned. `AIRouter` itself
never hardcodes provider names/order/priority; it only asks `get_chain()` for whatever
is currently enabled, in priority order, and tries each in turn.

Only `GeminiProvider` is registered right now (priority 1). Adding `OllamaProvider`
(file 07) means adding another `ProviderEntry` here -- `AIRouter`'s failover logic
requires no changes to support it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.llm.base import LLMProvider
from app.llm.gemini import GeminiProvider


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
    real chain -- just `GeminiProvider` at priority 1 -- from `Settings`.
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
