"""
WhatsApp end-to-end capability tests (§20-22, file 18 prompt 3).

`tests/platforms/test_whatsapp_adapter.py` proves `WhatsAppAdapter` translates
correctly in isolation; `tests/api/test_whatsapp_webhook.py` proves the HTTP boundary in
front of it. Neither exercises the pipeline the webhook's background task actually
drives, which is what this file covers -- the direct counterpart of
`tests/platforms/test_discord_capability.py`:

    webhook payload -> WhatsAppAdapter.to_request -> AssistantCore.handle
                    -> WhatsAppAdapter.to_platform_output -> outbound Cloud API JSON

Three scenarios, one per §22 outcome a WhatsApp sender can get:

  * A linked user's "what are my tasks?" resolves normally. That message isn't a
    registered `CommandRouter` alias (only "show my tasks"/"list tasks" are), so it
    genuinely falls through to classification LLM_REQUIRED -- `AIRouter` is mocked the
    same way `tests/core/test_assistant_llm_path.py` mocks it, so what this proves is
    that the WhatsApp pipeline hands the message to the LLM path and still runs the
    requested `list_tasks` call through the real `ToolExecutor` and its platform check,
    not that Gemini works.
  * "open VS Code" gets the §22 rejection, verbatim. `open_application` stays
    `platforms=["desktop"]` (file 11), so the exact string `ToolExecutor` produces --
    "This action isn't available on whatsapp." -- has to survive all the way into the
    outbound `text.body`, and nothing may actually launch.
  * An unlinked number gets `UNLINKED_REPLY` and **no tool execution is attempted at
    all**. This is the case with no Discord analogue: a Discord author id *is* an
    identity, whereas a phone number proves only "I control this number", so the
    rejection here happens a whole layer earlier than §22 -- before `AssistantCore`
    exists, let alone `ToolExecutor`.

`ToolExecutor.execute` is wrapped by a spy for every test (`executed`), so "no tool
execution attempted" is asserted at the choke point every tool call must pass through
(§41 Rule 6) rather than inferred from an absent side effect. The unlinked case drives
the webhook route's own `_build_outbound` rather than the adapter directly, because the
three-way branch (unlinked / pairing code / command) lives there -- calling the adapter
alone would only prove `UnlinkedSenderError` is raised, not that the user gets the right
reply and nothing runs.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from app.api.routes import whatsapp_webhook as webhook_module
from app.auth.service import AuthService
from app.core.assistant import AssistantCore
from app.core.tool_executor import ToolExecutor
from app.llm.base import LLMResult, ToolCallRequest
from app.llm.health import HealthManager
from app.platforms.whatsapp import UnlinkedSenderError, WhatsAppAdapter, extract_inbound_message
from app.tools import applications as applications_module
from app.tools import register_default_tools
from app.tools.registry import ToolRegistry
from app.whatsapp.linking import UNLINKED_REPLY

USERNAME = "zhongxuen"
PASSWORD = "correct horse battery staple"
PHONE = "60123456789"
UNLINKED_PHONE = "60199999999"

# The exact string `ToolExecutor` produces for a platform it doesn't allow, spelled out
# here rather than built from `context.platform` so a change to that message has to be a
# deliberate edit of this assertion too.
REJECTION = "This action isn't available on whatsapp."


def _payload(text: str, *, from_number: str = PHONE) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "9876543210",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "111222333"},
                            "contacts": [{"wa_id": from_number}],
                            "messages": [
                                {
                                    "from": from_number,
                                    "id": "wamid.ABC",
                                    "timestamp": "1756339200",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


@pytest.fixture()
def executed(monkeypatch) -> list[str]:
    """Names of every tool that reached `ToolExecutor.execute`, in order.

    A spy that still calls through, not a stub -- the real executor has to run for the
    platform check to be the thing under test. Patched on the class because
    `AssistantCore` builds its own executor internally.
    """
    calls: list[str] = []
    original = ToolExecutor.execute

    def spy(self, tool_name, params, context):
        calls.append(tool_name)
        return original(self, tool_name, params, context)

    monkeypatch.setattr(ToolExecutor, "execute", spy)
    return calls


@pytest.fixture()
def registry(monkeypatch) -> ToolRegistry:
    """The full production tool set, with only `open_application`'s real OS side effects
    mocked (same convention as `tests/platforms/test_discord_capability.py`'s `core`
    fixture) -- so a regression that skipped the platform check would be caught here
    rather than actually launching something in CI.
    """
    monkeypatch.setattr(applications_module.os, "startfile", Mock(), raising=False)
    monkeypatch.setattr(applications_module.subprocess, "Popen", Mock())

    registry = ToolRegistry()
    register_default_tools(registry)
    return registry


@pytest.fixture()
def linked_db(test_db):
    """A session with `PHONE` linked to a real `User`. Written directly rather than via
    the pairing flow -- that flow is `tests/whatsapp/test_linking.py`'s subject.
    """
    db = test_db()
    user = AuthService(db).create_user(USERNAME, PASSWORD)
    user.whatsapp_phone_number = PHONE
    db.commit()
    yield db
    db.close()


def _core(registry: ToolRegistry) -> AssistantCore:
    """`db=None` matches `tests/platforms/test_discord_capability.py`: the task tools
    open their own `SessionLocal` (patched onto the in-memory DB by `tests/conftest.py`)
    rather than taking the adapter's session.
    """
    return AssistantCore(registry, db=None)


# --- a linked user's allowed command ------------------------------------------------------


def test_linked_user_what_are_my_tasks_resolves_normally(linked_db, registry, executed):
    """"what are my tasks?" from a linked number goes down the LLM tool-calling path and
    its `list_tasks` call is *allowed* -- `list_tasks` carries "whatsapp" in the shared
    `PLATFORMS` list (app/tools/tasks.py), so §22 lets it through the same way it lets
    Discord and mobile through.
    """
    adapter = WhatsAppAdapter(linked_db)
    core = _core(registry)
    core.ai_router.route = AsyncMock(
        return_value=LLMResult(
            status="SUCCESS",
            tool_calls=[ToolCallRequest(tool_name="list_tasks", params={})],
        )
    )

    request = adapter.to_request(_payload("what are my tasks?"))
    assert request.platform == "whatsapp"
    assert request.user_id == USERNAME  # resolved to the linked account, not the number

    response = core.handle(request)
    outbound = adapter.to_platform_output(response, PHONE)

    core.ai_router.route.assert_awaited_once()
    assert response.used_llm is True
    assert executed == ["list_tasks"]
    assert response.tool_calls[0]["tool_name"] == "list_tasks"
    assert response.tool_calls[0]["result"]["success"] is True

    assert outbound["to"] == PHONE
    assert outbound["text"]["body"]
    assert REJECTION not in outbound["text"]["body"]


def test_linked_user_get_time_is_allowed(linked_db, registry, executed):
    """The deterministic contrast case: `get_time` (app/tools/system.py) also carries
    "whatsapp" now, so it resolves with no LLM involved and no capability rejection.
    """
    adapter = WhatsAppAdapter(linked_db)

    response = _core(registry).handle(adapter.to_request(_payload("what time is it")))
    outbound = adapter.to_platform_output(response, PHONE)

    assert response.used_llm is False
    assert executed == ["get_time"]
    assert response.tool_calls[0]["result"]["success"] is True
    assert "whatsapp" not in outbound["text"]["body"].lower()


# --- a linked user's desktop-only command -------------------------------------------------


def test_linked_user_open_vs_code_gets_the_exact_capability_rejection(
    linked_db, registry, executed
):
    """"open VS Code" from WhatsApp must not open anything. `open_application` stays
    `platforms=["desktop"]`, so the call reaches `ToolExecutor`, is refused there, and
    the refusal is what gets sent back -- verbatim, since `AssistantCore.handle` renders
    a failed deterministic call as `result.error`.
    """
    adapter = WhatsAppAdapter(linked_db)

    request = adapter.to_request(_payload("open VS Code"))
    assert request.message == "open VS Code"  # no prefix stripping on this platform

    response = _core(registry).handle(request)
    outbound = adapter.to_platform_output(response, PHONE)

    # Resolved deterministically (CommandRouter's "open" alias), not via the LLM -- this
    # is a pure platform-capability rejection, nothing to do with reasoning.
    assert response.used_llm is False
    assert executed == ["open_application"]
    assert response.tool_calls[0]["tool_name"] == "open_application"
    assert response.tool_calls[0]["result"]["success"] is False
    assert response.tool_calls[0]["result"]["error"] == REJECTION

    assert outbound["text"]["body"] == REJECTION
    assert "open_application" not in outbound["text"]["body"]  # human-facing, not a raw tool name

    applications_module.os.startfile.assert_not_called()
    applications_module.subprocess.Popen.assert_not_called()


def test_linked_user_run_routine_gets_the_exact_capability_rejection(
    linked_db, registry, executed
):
    """`run_routine` (app/tools/routines.py) is deliberately still `platforms=["desktop"]`
    and was *not* extended for WhatsApp, for the reason that file's docstring gives:
    `RunRoutineTool.handler()` calls `RoutineEngine.run()` with no context, so the engine
    defaults to `RequesterContext(platform="desktop")` internally -- adding "whatsapp"
    without first threading the real caller's platform through would let a WhatsApp
    message run a routine whose own steps are desktop-only, silently bypassing the exact
    check this file verifies.
    """
    adapter = WhatsAppAdapter(linked_db)

    response = _core(registry).handle(adapter.to_request(_payload("start coding")))
    outbound = adapter.to_platform_output(response, PHONE)

    assert executed == ["run_routine"]
    assert response.tool_calls[0]["result"]["success"] is False
    assert outbound["text"]["body"] == REJECTION

    applications_module.os.startfile.assert_not_called()
    applications_module.subprocess.Popen.assert_not_called()


# --- an unlinked number -------------------------------------------------------------------


def test_unlinked_number_gets_the_link_reply_and_no_tool_is_executed(
    linked_db, registry, executed, monkeypatch
):
    """An unrecognised number is stopped a layer *above* §22: it never becomes an
    `AssistantRequest` at all, so no tool -- allowed or not -- is even attempted.

    Driven through the webhook route's own `_build_outbound` because the unlinked branch
    lives there, and with `AssistantCore` patched in that module so "the assistant was
    never reached" is asserted directly rather than inferred.
    """
    monkeypatch.setattr(webhook_module, "SessionLocal", lambda: linked_db)
    monkeypatch.setattr(linked_db, "close", lambda: None)  # the `with` block must not close it
    core_class = Mock()
    monkeypatch.setattr(webhook_module, "AssistantCore", core_class)

    payload = _payload("what are my tasks?", from_number=UNLINKED_PHONE)
    inbound = extract_inbound_message(payload)
    assert inbound is not None

    outbound = webhook_module._build_outbound(payload, inbound, registry, HealthManager())

    assert outbound["to"] == UNLINKED_PHONE
    assert outbound["text"]["body"] == UNLINKED_REPLY
    core_class.assert_not_called()  # AssistantCore was never even constructed
    assert executed == []  # and so no tool call was attempted


def test_unlinked_number_cannot_reach_a_desktop_tool_either(
    linked_db, registry, executed, monkeypatch
):
    """The same guarantee for a message that *would* have been rejected by §22 anyway:
    the reply is still the link instructions, not a capability rejection. An unlinked
    sender must not be able to learn anything about what the assistant can or can't do.
    """
    monkeypatch.setattr(webhook_module, "SessionLocal", lambda: linked_db)
    monkeypatch.setattr(linked_db, "close", lambda: None)

    payload = _payload("open VS Code", from_number=UNLINKED_PHONE)
    inbound = extract_inbound_message(payload)
    assert inbound is not None

    outbound = webhook_module._build_outbound(payload, inbound, registry, HealthManager())

    assert outbound["text"]["body"] == UNLINKED_REPLY
    assert REJECTION not in outbound["text"]["body"]
    assert executed == []
    applications_module.os.startfile.assert_not_called()
    applications_module.subprocess.Popen.assert_not_called()


def test_adapter_refuses_to_build_a_request_for_an_unlinked_number(linked_db, executed):
    """The adapter-level half of the same rule, stated as the invariant the route relies
    on: there is no code path that produces an `AssistantRequest` for an unlinked sender,
    so no later layer has to remember to check.
    """
    with pytest.raises(UnlinkedSenderError):
        WhatsAppAdapter(linked_db).to_request(_payload("hello", from_number=UNLINKED_PHONE))

    assert executed == []
