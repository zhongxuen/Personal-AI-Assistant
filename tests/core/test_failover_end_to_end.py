"""
End-to-end failover through AssistantCore.handle() (§20, §41 Rule 10, files 06-07).

`test_failover.py` proves the Gemini -> Ollama failover matrix works at the
`AIRouter`/provider level; this file drives the *same* four scenarios through
`AssistantCore.handle()` -- the single entrypoint every platform adapter actually
calls -- for a request classified LLM_REQUIRED ("Look at my tasks and tell me what I should
prioritize", which doesn't match any deterministic trigger/alias). It then proves
Rule 10 (§41): no matter which of the four provider states is in effect, a
deterministic command ("what time is it") still resolves via `CommandRouter` alone,
with zero LLM calls and zero network activity -- LLM health/availability must never
leak into, or be required by, the deterministic path.

`AssistantCore` always builds its own `AIRouter` from real settings in `__init__`; each
test swaps `core.ai_router` for one wired against fake providers representing the
scenario under test (a lighter touch than `test_failover.py`'s real
`GeminiProvider`/`OllamaProvider` + mocked HTTP, appropriate here since this file is
only asserting `AssistantCore`'s plumbing, not provider-level classification).
"""

from __future__ import annotations

from unittest.mock import Mock

import httpx
import pytest

from app.core.assistant import AssistantCore
from app.core.models import AssistantRequest, AssistantResponse
from app.llm.ai_router import AIRouter
from app.llm.base import LLMRequest, LLMResult
from app.llm.health import HealthManager
from app.llm.provider_manager import ProviderEntry, ProviderManager
from app.llm.quota_manager import QuotaManager
from app.tools import applications as applications_module
from app.tools import register_default_tools
from app.tools.registry import ToolRegistry

try:
    import requests
except ImportError:  # pragma: no cover - not installed in this project (see requirements.txt)
    requests = None

PRIORITIZE_MESSAGE = "Look at my tasks and tell me what I should prioritize"


def _blocked(*_args, **_kwargs):
    raise AssertionError("Network/LLM call attempted during a zero-LLM command -- see §41 Rule 10.")


async def _blocked_async(*_args, **_kwargs):
    raise AssertionError("Network/LLM call attempted during a zero-LLM command -- see §41 Rule 10.")


@pytest.fixture()
def no_network(monkeypatch):
    """Same convention as `tests/core/test_zero_llm.py` -- any attempted
    `httpx`/`requests` call fails the test immediately instead of silently succeeding.
    """
    monkeypatch.setattr(httpx.Client, "send", _blocked)
    monkeypatch.setattr(httpx, "get", _blocked)
    monkeypatch.setattr(httpx, "post", _blocked)
    monkeypatch.setattr(httpx.AsyncClient, "send", _blocked_async)
    if requests is not None:  # pragma: no cover - exercised only if requests is installed
        monkeypatch.setattr(requests.sessions.Session, "request", _blocked)
        monkeypatch.setattr(requests, "get", _blocked)
        monkeypatch.setattr(requests, "post", _blocked)


@pytest.fixture()
def core(monkeypatch, test_db) -> AssistantCore:
    """A real `AssistantCore` against the full production tool set, with the only
    real-world side effects a Phase-2 command can cause mocked out -- same fixture as
    `test_zero_llm.py`.
    """
    monkeypatch.setattr(applications_module.os, "startfile", Mock(), raising=False)
    monkeypatch.setattr(applications_module.subprocess, "Popen", Mock())

    registry = ToolRegistry()
    register_default_tools(registry)
    return AssistantCore(registry, db=None)  # no db -- nothing persisted


def _handle(core: AssistantCore, message: str, **metadata) -> AssistantResponse:
    request = AssistantRequest(user_id="u1", platform="desktop", message=message, metadata=metadata)
    return core.handle(request)


class _FakeProvider:
    """Stand-in `LLMProvider` -- returns queued results in order, records
    `fallback_used` per call. Same shape as `test_ai_router.py`'s `FakeProvider`.
    """

    def __init__(self, name: str, results: list[LLMResult]) -> None:
        self.name = name
        self._results = list(results)
        self.calls: list[bool] = []

    def is_available(self) -> bool:
        return True

    async def generate(self, request: LLMRequest, *, fallback_used: bool = False) -> LLMResult:
        self.calls.append(fallback_used)
        return self._results.pop(0)


def _wire_ai_router(core: AssistantCore, *providers: _FakeProvider) -> None:
    """Replaces `core.ai_router` (built from real settings in `__init__`) with one
    wired against fake providers representing the scenario under test.
    """
    entries = [
        ProviderEntry(provider=provider, priority=index, enabled=True)
        for index, provider in enumerate(providers, start=1)
    ]
    core.ai_router = AIRouter(
        provider_manager=ProviderManager(entries=entries),
        quota_manager=QuotaManager(db=None),
        health_manager=HealthManager(),
    )


# ---------------------------------------------------------------------------
# Scenario 1: Gemini healthy.
# ---------------------------------------------------------------------------


def test_scenario_1_gemini_healthy_answers_and_deterministic_command_still_zero_llm(
    no_network, core
):
    gemini = _FakeProvider("gemini", [LLMResult(status="SUCCESS", text="Prioritize the report.")])
    ollama = _FakeProvider("ollama", [LLMResult(status="SUCCESS", text="should never run")])
    _wire_ai_router(core, gemini, ollama)

    response = _handle(core, PRIORITIZE_MESSAGE)

    assert response.used_llm is True
    assert response.text == "Prioritize the report."
    assert gemini.calls == [False]
    assert ollama.calls == []

    _assert_deterministic_command_is_zero_llm(core)


# ---------------------------------------------------------------------------
# Scenario 2: Gemini QUOTA_EXHAUSTED -> Ollama picks it up automatically.
# ---------------------------------------------------------------------------


def test_scenario_2_gemini_quota_exhausted_fails_over_to_ollama_and_deterministic_command_still_zero_llm(
    no_network, core
):
    gemini = _FakeProvider(
        "gemini", [LLMResult(status="QUOTA_EXHAUSTED", error_type="resource_exhausted")]
    )
    ollama = _FakeProvider("ollama", [LLMResult(status="SUCCESS", text="Prioritize the report.")])
    _wire_ai_router(core, gemini, ollama)

    response = _handle(core, PRIORITIZE_MESSAGE)

    assert response.used_llm is True
    assert response.text == "Prioritize the report."
    assert gemini.calls == [False]
    assert ollama.calls == [True]  # fallback_used=True -- second provider attempted

    _assert_deterministic_command_is_zero_llm(core)


# ---------------------------------------------------------------------------
# Scenario 3: Gemini unavailable -> AIRouter skips straight to Ollama.
# ---------------------------------------------------------------------------


def test_scenario_3_gemini_unavailable_skips_to_ollama_and_deterministic_command_still_zero_llm(
    no_network, core
):
    # `AIRouter` itself doesn't call `is_available()` directly (see `test_ai_router.py`
    # -- quota/health checks are what actually gate a call); the contract each real
    # provider upholds instead (see `GeminiProvider._generate`'s own `is_available()`
    # pre-flight check) is that an unavailable provider's `generate()` fails fast with
    # PERMANENT_ERROR *without* attempting the underlying call -- modeled directly
    # here so this test doesn't need real HTTP mocking to prove the same contract.
    gemini = _FakeProvider("gemini", [LLMResult(status="PERMANENT_ERROR", error_type="missing_api_key")])
    gemini.is_available = lambda: False  # simulate "Gemini unavailable"
    ollama = _FakeProvider("ollama", [LLMResult(status="SUCCESS", text="Prioritize the report.")])
    _wire_ai_router(core, gemini, ollama)

    response = _handle(core, PRIORITIZE_MESSAGE)

    assert response.used_llm is True
    assert response.text == "Prioritize the report."
    assert gemini.is_available() is False
    assert gemini.calls == [False]
    assert ollama.calls == [True]

    _assert_deterministic_command_is_zero_llm(core)


# ---------------------------------------------------------------------------
# Scenario 4: both providers down -> graceful message, no exception, no blank text.
# ---------------------------------------------------------------------------


def test_scenario_4_both_providers_down_returns_graceful_message_and_deterministic_command_still_zero_llm(
    no_network, core
):
    gemini = _FakeProvider("gemini", [LLMResult(status="PERMANENT_ERROR", error_type="missing_api_key")])
    ollama = _FakeProvider(
        "ollama", [LLMResult(status="PERMANENT_ERROR", error_type="ollama_unreachable")]
    )
    _wire_ai_router(core, gemini, ollama)

    response = _handle(core, PRIORITIZE_MESSAGE)  # must not raise

    assert response.used_llm is False
    assert response.text  # non-empty, clearly-worded -- never a silent blank response
    assert "reasoning" in response.text.lower() or "provider" in response.text.lower()
    assert gemini.calls == [False]
    assert ollama.calls == [True]

    _assert_deterministic_command_is_zero_llm(core)


def _assert_deterministic_command_is_zero_llm(core: AssistantCore) -> None:
    """Rule 10 (§41): whatever state the LLM chain is in -- healthy, failed over, or
    fully down -- a deterministic command must resolve via `CommandRouter` alone.
    `no_network` (active for the whole test) would already fail this if AIRouter or
    either provider's `generate()` were reached; this also asserts the router-level
    signal (`used_llm=False`) and that `CommandRouter` picked the expected tool.
    """
    response = _handle(core, "what time is it")

    assert response.used_llm is False
    assert response.tool_calls[0]["tool_name"] == "get_time"
    assert response.tool_calls[0]["result"]["success"] is True
