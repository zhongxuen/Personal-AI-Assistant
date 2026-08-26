"""
Mobile platform end-to-end capability tests (§20-22, file 14).

`app/platforms/mobile.py` is a docs-only stub, not a server-side adapter class --
per docs/architecture.md's "thin client + shared HTTP route" shape, a mobile client
builds an already-`AssistantRequest`-shaped JSON body with `platform="mobile"` and
POSTs it straight to the same `POST /api/assistant/message` route (`app/api/routes/
assistant.py`) desktop/web/discord all funnel through -- there's no adapter class to
unit-test the way `tests/platforms/test_discord_adapter.py` unit-tests `DiscordAdapter`
(see that route's docstring and `app/platforms/mobile.py`'s docstring for why).

So the honest equivalent of `tests/platforms/test_discord_capability.py` for this
platform is driving the *real* HTTP route through FastAPI's `TestClient` end to end --
JSON in, auth boundary, `AssistantCore`, JSON out -- rather than calling
`AssistantCore.handle()` directly, since that HTTP route (with its platform-conditional
auth check, `app/api/routes/assistant.py`) *is* the mobile platform's actual production
entrypoint, unlike Discord's bot-wired adapter which calls `AssistantCore.handle()`
in-process with no HTTP hop at all.

`tests/api/test_auth.py` already covers the "mobile" auth boundary itself (401 with no
token, 200 with a valid one) generically, since that boundary is platform-agnostic.
This file covers the other half: that a real request making it through that boundary
still gets the right §22 platform-capability treatment from `ToolExecutor` -- an
allowed action resolves normally, a desktop-only action is rejected -- mirroring three
of `tests/platforms/test_discord_capability.py`'s four scenarios (the fourth, "what are
my tasks?" resolving via the LLM tool-calling path, is already proven generically by
`tests/core/test_assistant_llm_path.py` and isn't platform-specific, so it isn't
duplicated here).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_db, get_health_manager, get_optional_current_user, get_tool_registry
from app.llm.health import HealthManager
from app.tools import applications as applications_module
from app.tools import register_default_tools
from app.tools.registry import ToolRegistry
from main import app

STUB_USER = SimpleNamespace(id=1, username="stub-mobile-user")


@pytest.fixture()
def client(test_db, monkeypatch):
    """Real `AssistantCore`/`ToolExecutor`/tool registry behind the real route -- only
    `open_application`'s actual OS side effects are mocked (same convention as
    `tests/platforms/test_discord_capability.py`'s `core` fixture), so a regression
    that skips the platform check would be caught here rather than actually launching
    something in CI.
    """
    monkeypatch.setattr(applications_module.os, "startfile", Mock(), raising=False)
    monkeypatch.setattr(applications_module.subprocess, "Popen", Mock())

    registry = ToolRegistry()
    register_default_tools(registry)

    def override_get_db():
        db = test_db()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_tool_registry] = lambda: registry
    app.dependency_overrides[get_health_manager] = lambda: HealthManager()
    # Auth itself is covered by tests/api/test_auth.py -- stubbed here to a fixed user
    # so these tests exercise only the platform-capability question, same rationale as
    # tests/api/test_desktop_local_only.py's client fixture.
    app.dependency_overrides[get_optional_current_user] = lambda: STUB_USER

    yield TestClient(app)
    app.dependency_overrides.clear()


def _post(client: TestClient, message: str):
    return client.post(
        "/api/assistant/message",
        json={"user_id": "ignored-overwritten-by-auth", "platform": "mobile", "message": message},
    )


def test_mobile_open_vscode_gets_capability_rejection(client):
    """"open VS Code" from a mobile client must not open anything -- the same §22-style
    "this platform can't do that" response ToolExecutor produces for any other
    unsupported-platform call, routed through the real shared HTTP route end to end.
    """
    response = _post(client, "open VS Code")
    assert response.status_code == 200
    body = response.json()

    assert body["used_llm"] is False  # resolved deterministically (CommandRouter's "open" alias)
    assert body["tool_calls"][0]["tool_name"] == "open_application"
    assert body["tool_calls"][0]["result"]["success"] is False

    assert "mobile" in body["text"].lower()
    assert "open_application" not in body["text"]  # a human-facing message, not a raw tool name

    applications_module.os.startfile.assert_not_called()
    applications_module.subprocess.Popen.assert_not_called()


def test_mobile_get_time_is_allowed(client):
    """Contrast case: `get_time` (`platforms=["desktop", "web", "discord", "mobile"]`)
    is exactly the read-only, chat-appropriate command §22 means to allow.
    """
    response = _post(client, "what time is it")
    assert response.status_code == 200
    body = response.json()

    assert body["tool_calls"][0]["tool_name"] == "get_time"
    assert body["tool_calls"][0]["result"]["success"] is True
    assert "mobile" not in body["text"].lower()


def test_mobile_remind_me_to_finish_report_tomorrow_creates_a_task(client):
    """Resolves deterministically via CommandRouter's "remind me to" alias plus local
    due-date parsing -- no LLM involved -- and must actually persist a task, the same
    as it would from any other platform.
    """
    response = _post(client, "remind me to finish my report tomorrow")
    assert response.status_code == 200
    body = response.json()

    assert body["used_llm"] is False
    call = body["tool_calls"][0]
    assert call["tool_name"] == "create_task"
    assert call["result"]["success"] is True
    assert call["result"]["data"]["title"] == "finish my report"
    assert call["result"]["data"]["due"] is not None  # "tomorrow" was parsed out of the remainder
    assert "finish my report" in body["text"]


def test_mobile_start_coding_routine_gets_capability_rejection(client):
    """`run_routine` stays `platforms=["desktop"]` on purpose (see
    `app/tools/routines.py`'s docstring, and `app/platforms/mobile.py`'s) -- a mobile
    request that reaches it must be rejected the same §22 way `open_application` is,
    and must not launch anything for real.
    """
    response = _post(client, "start coding")
    assert response.status_code == 200
    body = response.json()

    assert body["used_llm"] is False
    assert body["tool_calls"][0]["tool_name"] == "run_routine"
    assert body["tool_calls"][0]["result"]["success"] is False
    assert "mobile" in body["text"].lower()

    applications_module.os.startfile.assert_not_called()
    applications_module.subprocess.Popen.assert_not_called()
