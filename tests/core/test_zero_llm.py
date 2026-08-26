"""
Zero-LLM commands test (§38, §9, §41 Rule 4).

Runs every deterministic command from file 03 (`md-files/03-basic-deterministic-tools.md`)
through `AssistantCore.handle()` -- the same entrypoint every platform adapter calls --
against the real, fully-registered tool set (`register_default_tools`), and asserts:

  1. `AssistantResponse.used_llm` is `False` for every one of them (CommandRouter
     resolved them deterministically; classification LLM_REQUIRED was never hit), and
  2. no network call was ever attempted -- `httpx`/`requests` are monkeypatched to raise
     if called at all (including `httpx.AsyncClient.send`, the path Gemini's async SDK
     actually uses -- see file 05), so any accidental LLM/network call would be caught.
     A message classified LLM_REQUIRED now does call GeminiProvider for real (file 05); these tests only cover
     commands that resolve deterministically, so GeminiProvider.generate is never
     reached and this block never fires for them.

OS-level side effects (launching VS Code/Chrome, the routine's app-open steps) are
mocked out the same way tests/tools/test_deterministic_tools.py does -- nothing actually
launches in CI.
"""

from __future__ import annotations

from unittest.mock import Mock

import httpx
import pytest

from app.core.assistant import AssistantCore
from app.core.models import AssistantRequest, AssistantResponse
from app.tools import applications as applications_module
from app.tools import register_default_tools
from app.tools.registry import ToolRegistry

try:
    import requests
except ImportError:  # pragma: no cover - not installed in this project (see requirements.txt)
    requests = None


def _blocked(*_args, **_kwargs):
    raise AssertionError("Network call attempted during a zero-LLM command -- see §38/§9.")


async def _blocked_async(*_args, **_kwargs):
    raise AssertionError("Network call attempted during a zero-LLM command -- see §38/§9.")


@pytest.fixture()
def no_network(monkeypatch):
    """Any attempted `httpx`/`requests` call fails the test immediately instead of
    silently succeeding or hanging -- see module docstring.
    """
    monkeypatch.setattr(httpx.Client, "send", _blocked)
    monkeypatch.setattr(httpx, "get", _blocked)
    monkeypatch.setattr(httpx, "post", _blocked)
    # google-genai's async SDK (used by GeminiProvider.generate, file 05) sends
    # through httpx.AsyncClient, not httpx.Client -- without this, a real request
    # slips out whenever GEMINI_API_KEY happens to be set in the environment.
    monkeypatch.setattr(httpx.AsyncClient, "send", _blocked_async)
    if requests is not None:  # pragma: no cover - exercised only if requests is installed
        monkeypatch.setattr(requests.sessions.Session, "request", _blocked)
        monkeypatch.setattr(requests, "get", _blocked)
        monkeypatch.setattr(requests, "post", _blocked)


@pytest.fixture()
def core(monkeypatch, test_db) -> AssistantCore:
    """A real AssistantCore wired against the full production tool set, with the only
    two real-world side effects a Phase-2 command can cause -- launching an
    application, and psutil process enumeration for close_application -- mocked out.
    """
    mock_startfile = Mock()
    mock_popen = Mock()
    monkeypatch.setattr(applications_module.os, "startfile", mock_startfile, raising=False)
    monkeypatch.setattr(applications_module.subprocess, "Popen", mock_popen)

    registry = ToolRegistry()
    register_default_tools(registry)
    return AssistantCore(registry, db=None)  # no db -- nothing persisted, matches other core tests


def _handle(core: AssistantCore, message: str, **metadata) -> AssistantResponse:
    request = AssistantRequest(user_id="u1", platform="desktop", message=message, metadata=metadata)
    return core.handle(request)


def test_get_time_is_zero_llm(no_network, core):
    response = _handle(core, "what time is it")

    assert response.used_llm is False
    assert response.tool_calls[0]["tool_name"] == "get_time"
    assert response.tool_calls[0]["result"]["success"] is True


def test_get_system_info_is_zero_llm(no_network, core):
    response = _handle(core, "get_system_info")

    assert response.used_llm is False
    assert response.tool_calls[0]["tool_name"] == "get_system_info"
    assert response.tool_calls[0]["result"]["success"] is True


def test_open_application_is_zero_llm(no_network, core):
    response = _handle(core, "open vscode")

    assert response.used_llm is False
    assert response.tool_calls[0]["tool_name"] == "open_application"
    assert response.tool_calls[0]["result"]["success"] is True


def test_close_application_is_zero_llm(no_network, core):
    # CONFIRM tool -- needs explicit confirmation, same as any other CONFIRM tool
    # (§19); whether a matching process happens to be running in CI is irrelevant to
    # what this test checks (zero LLM calls), so success/failure of the close itself
    # isn't asserted.
    response = _handle(core, "close vscode", confirmed=True)

    assert response.used_llm is False
    assert response.tool_calls[0]["tool_name"] == "close_application"


def test_create_task_is_zero_llm(no_network, core):
    response = _handle(core, "remind me to buy milk")

    assert response.used_llm is False
    assert response.tool_calls[0]["tool_name"] == "create_task"
    assert response.tool_calls[0]["result"]["success"] is True


def test_list_tasks_is_zero_llm(no_network, core):
    response = _handle(core, "show my tasks")

    assert response.used_llm is False
    assert response.tool_calls[0]["tool_name"] == "list_tasks"
    assert response.tool_calls[0]["result"]["success"] is True


def test_complete_task_is_zero_llm(no_network, core):
    created = _handle(core, "remind me to buy milk")
    task_id = created.tool_calls[0]["result"]["data"]["id"]

    response = _handle(core, f"complete_task {task_id}")

    assert response.used_llm is False
    assert response.tool_calls[0]["tool_name"] == "complete_task"
    assert response.tool_calls[0]["result"]["success"] is True
    assert response.tool_calls[0]["result"]["data"]["status"] == "completed"


def test_start_timer_is_zero_llm(no_network, core):
    response = _handle(core, "set a timer for 1 minutes")

    assert response.used_llm is False
    assert response.tool_calls[0]["tool_name"] == "start_timer"
    assert response.tool_calls[0]["result"]["success"] is True


def test_run_routine_is_zero_llm(no_network, core):
    response = _handle(core, "start coding")

    assert response.used_llm is False
    assert response.tool_calls[0]["tool_name"] == "run_routine"
    assert response.tool_calls[0]["result"]["success"] is True
    assert response.tool_calls[0]["result"]["data"]["routine"] == "coding"


def test_unrecognized_message_needs_llm_but_still_makes_no_network_call(no_network, core):
    # The one message in this file that *doesn't* resolve deterministically -- proves
    # classification LLM_REQUIRED falls through to the placeholder response rather than an actual call,
    # since no AI Router exists yet (file 06).
    response = _handle(core, "what's the weather like tomorrow?")

    assert response.used_llm is False
