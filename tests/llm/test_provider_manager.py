"""
ProviderManager tests (§37 Phase 5, files 06-07).

Exercises the chain-assembly logic in isolation from `AIRouter`: the default chain is
`GeminiProvider` at priority 1 then `OllamaProvider` at priority 2, `get_chain()`
orders by ascending priority, and disabled entries -- including Ollama when
`ollama_enabled=False` -- never appear in the chain it hands back.
"""

from __future__ import annotations

from app.config.settings import Settings
from app.llm.gemini import GeminiProvider
from app.llm.ollama import OllamaProvider
from app.llm.provider_manager import ProviderEntry, ProviderManager


class _FakeProvider:
    def __init__(self, name: str) -> None:
        self.name = name

    def is_available(self) -> bool:
        return True

    async def generate(self, request, *, fallback_used: bool = False):  # pragma: no cover
        raise NotImplementedError


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, gemini_api_key="test-api-key", **overrides)


def test_default_chain_is_gemini_then_ollama():
    manager = ProviderManager(settings=_settings())

    chain = manager.get_chain()

    assert len(chain) == 2
    assert isinstance(chain[0], GeminiProvider)
    assert isinstance(chain[1], OllamaProvider)


def test_default_chain_excludes_ollama_when_disabled_via_settings():
    manager = ProviderManager(settings=_settings(ollama_enabled=False))

    chain = manager.get_chain()

    assert len(chain) == 1
    assert isinstance(chain[0], GeminiProvider)


def test_get_chain_orders_by_ascending_priority():
    low = _FakeProvider("low_priority")
    high = _FakeProvider("high_priority")
    manager = ProviderManager(
        entries=[
            ProviderEntry(provider=low, priority=5, enabled=True),
            ProviderEntry(provider=high, priority=1, enabled=True),
        ]
    )

    chain = manager.get_chain()

    assert [p.name for p in chain] == ["high_priority", "low_priority"]


def test_get_chain_excludes_disabled_entries():
    enabled = _FakeProvider("enabled")
    disabled = _FakeProvider("disabled")
    manager = ProviderManager(
        entries=[
            ProviderEntry(provider=enabled, priority=1, enabled=True),
            ProviderEntry(provider=disabled, priority=2, enabled=False),
        ]
    )

    chain = manager.get_chain()

    assert [p.name for p in chain] == ["enabled"]
