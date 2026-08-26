"""
Discord end-to-end capability tests (§20-22, file 13 prompt 3).

`tests/platforms/test_discord_adapter.py` proves `DiscordAdapter` translates correctly
in isolation. `tests/core/test_platform_capability.py` proves the underlying rule --
ToolExecutor rejects a desktop-only tool for `context.platform="discord"` -- against
`FakeTool` and a couple of file-11 tools called directly with a hand-built
`RequesterContext`. Neither exercises the real pipeline `on_message` in
`app.platforms.discord` actually drives: `DiscordAdapter.to_request` ->
`AssistantCore.handle` -> `DiscordAdapter.to_platform_output`, back to back, for the
plan's own example commands (development-plan.md §37 Phase 12,
md-files/13-discord-adapter.md prompt 3).

This file closes that gap for four of the plan's examples:

  - "Jarvis, what are my tasks?" resolves normally (via the LLM tool-calling path,
    mocked the same way tests/core/test_assistant_llm_path.py mocks AIRouter --
    "what are my tasks?" isn't a registered CommandRouter alias, only "show my
    tasks"/"list tasks" are, so this message genuinely falls through to
    classification LLM_REQUIRED rather than matching deterministically).
  - "Jarvis, remind me to finish my report tomorrow" creates a task deterministically
    (CommandRouter's "remind me to" alias + local due-date parsing, no LLM involved).
  - "Jarvis, open VS Code" produces the existing §22-style capability-rejection
    response.
  - "Jarvis, start coding" -- see NOTE below -- also produces a capability-rejection,
    because `RunRoutineTool` (app/tools/routines.py) is *deliberately* still
    `platforms=["desktop"]` and not extended to "discord", unlike task/get_time tools.
    That file's docstring explains why: `RunRoutineTool.handler()` calls
    `RoutineEngine.run(routine_name)` with no `context`, so `RoutineEngine.run()`
    always defaults to `RequesterContext(platform="desktop", ...)` internally --
    adding "discord" to the tool's `platforms` without first threading the real
    caller's platform through would let a Discord message run a routine whose own
    steps are desktop-only (`open_application`), silently bypassing the exact §22
    check this file is otherwise verifying. So the routine case below asserts the
    *rejection*, matching real current behavior, not the plan's literal "triggers the
    routine" wording -- that would require the context-threading fix as separate,
    deliberate feature work, not a test-only change.

NOTE on phrasing: the plan's example is "Jarvis, start my coding routine", but that
exact phrase never reaches `run_routine` at all -- CommandRouter's trigger-prefix
match picks the single-word "start" alias (registered for `open_application`, file 03)
over the fixed phrase "start coding" (registered for `run_routine`), since "start my
coding routine" doesn't start with "start coding ". That's a pre-existing alias-
coverage gap independent of the platform-capability question this file covers, so the
test below uses "Jarvis, start coding" -- the phrase that actually reaches
`run_routine` -- to prove the routine capability itself is rejected on Discord, not
just that "start ..." incidentally collides with `open_application`.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.assistant import AssistantCore
from app.llm.base import LLMResult, ToolCallRequest
from app.platforms.discord import DiscordAdapter
from app.tools import applications as applications_module
from app.tools import register_default_tools
from app.tools.registry import ToolRegistry


def _discord_message(content: str, *, author_id: int = 42, channel_id: int = 99) -> SimpleNamespace:
    """A minimal stand-in for `discord.Message` -- only the attributes
    `DiscordAdapter.to_request` actually reads (`content`, `author.id`, `channel.id`),
    matching the `DiscordMessage` Protocol in `app.platforms.discord`.
    """
    return SimpleNamespace(
        content=content,
        author=SimpleNamespace(id=author_id),
        channel=SimpleNamespace(id=channel_id),
    )


@pytest.fixture()
def core(monkeypatch, test_db) -> AssistantCore:
    """Real `AssistantCore` against the full production tool set (same pattern as
    `tests/core/test_zero_llm.py`'s `core` fixture) -- `open_application`'s real OS
    side effects are mocked so a regression that skips the platform check would be
    caught here rather than actually launching something in CI.
    """
    monkeypatch.setattr(applications_module.os, "startfile", Mock(), raising=False)
    monkeypatch.setattr(applications_module.subprocess, "Popen", Mock())

    registry = ToolRegistry()
    register_default_tools(registry)
    return AssistantCore(registry, db=None)


def test_discord_open_vscode_gets_capability_rejection(core: AssistantCore):
    """"Jarvis, open VS Code" from a Discord channel must not open anything -- it
    should come back as the same §22-style "this platform can't do that" response
    ToolExecutor produces for any other unsupported-platform call, routed through the
    real DiscordAdapter end to end.
    """
    adapter = DiscordAdapter()
    message = _discord_message("Jarvis, open VS Code")

    request = adapter.to_request(message)
    assert request.platform == "discord"
    assert request.message == "open VS Code"  # bot-prefix stripped before AssistantCore sees it

    response = core.handle(request)
    reply = adapter.to_platform_output(response)

    # Resolved deterministically (CommandRouter's "open" alias), not via the LLM --
    # this is a pure platform-capability rejection, nothing to do with reasoning.
    assert response.used_llm is False
    assert response.tool_calls[0]["tool_name"] == "open_application"
    assert response.tool_calls[0]["result"]["success"] is False

    assert "discord" in reply.lower()
    assert "open_application" not in reply  # a human-facing message, not a raw tool name

    applications_module.os.startfile.assert_not_called()
    applications_module.subprocess.Popen.assert_not_called()


def test_discord_get_time_is_allowed(core: AssistantCore):
    """Contrast case: `get_time` (file 03, now `platforms=["desktop", "web",
    "discord"]`) is exactly the kind of read-only, chat-appropriate command §22 means
    to allow -- the same Discord message pipeline should let it through instead of
    rejecting it.
    """
    adapter = DiscordAdapter()
    message = _discord_message("Jarvis, what time is it")

    response = core.handle(adapter.to_request(message))
    reply = adapter.to_platform_output(response)

    assert response.tool_calls[0]["tool_name"] == "get_time"
    assert response.tool_calls[0]["result"]["success"] is True
    assert "discord" not in reply.lower()


def test_discord_what_are_my_tasks_resolves_via_llm_tool_calling(core: AssistantCore):
    """"Jarvis, what are my tasks?" isn't a registered CommandRouter alias (only "show
    my tasks"/"list tasks" are, file 03) so it genuinely falls through to
    classification LLM_REQUIRED. AIRouter is mocked the same way
    tests/core/test_assistant_llm_path.py mocks it -- no real network call -- so this
    proves the Discord pipeline hands the message to the LLM path and still runs its
    requested `list_tasks` call through the real ToolExecutor/platform-capability
    check (list_tasks is `platforms=["desktop", "web", "discord"]`), rather than that
    AIRouter/Gemini itself works.
    """
    core.ai_router.route = AsyncMock(
        return_value=LLMResult(
            status="SUCCESS",
            tool_calls=[ToolCallRequest(tool_name="list_tasks", params={})],
        )
    )

    adapter = DiscordAdapter()
    message = _discord_message("Jarvis, what are my tasks?")

    request = adapter.to_request(message)
    assert request.message == "what are my tasks?"  # bot-prefix stripped

    response = core.handle(request)
    reply = adapter.to_platform_output(response)

    core.ai_router.route.assert_awaited_once()
    assert response.used_llm is True
    assert response.tool_calls[0]["tool_name"] == "list_tasks"
    assert response.tool_calls[0]["result"]["success"] is True
    assert reply  # a real human-facing reply was rendered, not an empty string


def test_discord_remind_me_to_finish_report_tomorrow_creates_a_task(core: AssistantCore):
    """"Jarvis, remind me to finish my report tomorrow" resolves deterministically via
    CommandRouter's "remind me to" alias (file 03) plus its local due-date parsing
    (file 08 prompt 1's `split_title_and_due`, file 09) -- no LLM involved -- and must
    actually persist a task, the same as it would from any other platform.
    """
    adapter = DiscordAdapter()
    message = _discord_message("Jarvis, remind me to finish my report tomorrow")

    request = adapter.to_request(message)
    assert request.message == "remind me to finish my report tomorrow"

    response = core.handle(request)
    reply = adapter.to_platform_output(response)

    assert response.used_llm is False
    call = response.tool_calls[0]
    assert call["tool_name"] == "create_task"
    assert call["result"]["success"] is True
    assert call["result"]["data"]["title"] == "finish my report"
    assert call["result"]["data"]["due"] is not None  # "tomorrow" was parsed out of the remainder
    assert "finish my report" in reply


def test_discord_start_coding_routine_gets_capability_rejection(core: AssistantCore):
    """`run_routine` (file 09) stays `platforms=["desktop"]` on purpose -- see this
    module's docstring. A Discord message that reaches it ("Jarvis, start coding",
    CommandRouter's exact registered alias for the routine) must be rejected the same
    §22 way `open_application` is, and must not launch anything for real.
    """
    adapter = DiscordAdapter()
    message = _discord_message("Jarvis, start coding")

    request = adapter.to_request(message)
    assert request.message == "start coding"

    response = core.handle(request)
    reply = adapter.to_platform_output(response)

    assert response.used_llm is False
    assert response.tool_calls[0]["tool_name"] == "run_routine"
    assert response.tool_calls[0]["result"]["success"] is False
    assert "discord" in reply.lower()

    applications_module.os.startfile.assert_not_called()
    applications_module.subprocess.Popen.assert_not_called()
