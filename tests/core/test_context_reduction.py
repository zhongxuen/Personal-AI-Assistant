"""
Context reduction tests (§16, §38, file 08 prompt 3).

Runs `AssistantCore.handle()` -- the same entrypoint every platform adapter calls --
against a real SQLite-backed db and a real, fully-populated `ToolRegistry`, and asserts
the `LLMRequest` handed to a mocked `AIRouter.route` is actually narrowed on every axis
file 08 promised, not just that the underlying helpers (`select_relevant_tools`,
`app.core.context_manager`) are individually correct in isolation:

  1. tools -- only the category-relevant subset (`app.tools.relevance`), never the full
     registered set (see `tests/tools/test_relevance.py` for that filter's own unit
     tests -- this file confirms `AssistantCore` actually wires it in).
  2. conversation history -- only turns belonging to *this* conversation_id, bounded to
     `context_manager.MAX_RECENT_TURNS`, never another conversation's turns and never
     the entire history once it exceeds that bound.
  3. memory -- no `memory` key sent at all, since no `MemoryService` exists yet (file
     09) -- `app.core.context_manager._relevant_memory` is a guarded no-op until then.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.assistant import AssistantCore
from app.core.context_manager import MAX_RECENT_TURNS
from app.core.models import AssistantRequest
from app.database import models  # noqa: F401 -- registers tables on Base.metadata
from app.database.database import Base
from app.database.models import Conversation, ConversationMessage
from app.llm.base import LLMResult
from app.tools.applications import close_application_tool, open_application_tool
from app.tools.notifications import show_notification_tool
from app.tools.registry import ToolRegistry
from app.tools.routines import RunRoutineTool
from app.tools.system import get_system_info_tool, get_time_tool
from app.tools.tasks import (
    complete_task_tool,
    create_task_tool,
    delete_task_tool,
    edit_task_tool,
    list_tasks_tool,
)
from app.tools.timers import StartTimerTool

# Contains "task" (app.tools.relevance's task-category keyword) but matches no
# registered trigger/alias verbatim or as a prefix -- CommandRouter still falls
# through to classification LLM_REQUIRED for it, same as tests/core/test_assistant_llm_path.py's
# UNRESOLVABLE_MESSAGE.
TASK_RELATED_UNRESOLVABLE_MESSAGE = "what tasks do I have due today?"


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = session_factory()
    yield session
    session.close()
    engine.dispose()


def _registry_with_every_category() -> ToolRegistry:
    """Every tool category `app.tools.relevance` knows about, so a narrowed result is
    actual proof of narrowing rather than "the only tools registered happen to be task
    tools".
    """
    registry = ToolRegistry()
    registry.register(create_task_tool)
    registry.register(list_tasks_tool)
    registry.register(complete_task_tool)
    registry.register(edit_task_tool)
    registry.register(delete_task_tool)
    # RunRoutineTool/StartTimerTool need *some* registry passed to their constructor
    # (same pattern as tests/tools/test_relevance.py) -- a throwaway one, since
    # `.handler` is never called on either in this file.
    helper_registry = ToolRegistry()
    registry.register(RunRoutineTool(helper_registry))
    registry.register(StartTimerTool(helper_registry))
    registry.register(show_notification_tool)
    registry.register(open_application_tool)
    registry.register(close_application_tool)
    registry.register(get_time_tool)
    registry.register(get_system_info_tool)
    return registry


def _seed_conversation(db_session, *, message_count: int) -> Conversation:
    conversation = Conversation(user_id=None, platform="desktop")
    db_session.add(conversation)
    db_session.commit()
    db_session.refresh(conversation)

    for i in range(message_count):
        db_session.add(
            ConversationMessage(
                conversation_id=conversation.id,
                role="user" if i % 2 == 0 else "assistant",
                content=f"turn {i}",
            )
        )
    db_session.commit()
    return conversation


def _handle(db_session, conversation_id: str) -> AsyncMock:
    """Runs the LLM_REQUIRED path for `TASK_RELATED_UNRESOLVABLE_MESSAGE` and returns
    the mocked `AIRouter.route` so callers can inspect the `LLMRequest` it was awaited
    with.
    """
    registry = _registry_with_every_category()
    core = AssistantCore(registry, db=db_session)
    core.ai_router.route = AsyncMock(return_value=LLMResult(status="SUCCESS", text="ok"))

    request = AssistantRequest(
        user_id="u1",
        platform="desktop",
        message=TASK_RELATED_UNRESOLVABLE_MESSAGE,
        conversation_id=conversation_id,
    )
    core.handle(request)
    return core.ai_router.route


def test_narrow_request_excludes_full_tool_registry(db_session):
    conversation = _seed_conversation(db_session, message_count=0)

    route_mock = _handle(db_session, str(conversation.id))

    llm_request = route_mock.await_args.args[0]
    tool_names = {tool["name"] for tool in llm_request.tools}
    assert tool_names == {
        "create_task",
        "list_tasks",
        "complete_task",
        "edit_task",
        "delete_task",
    }
    # Every non-task tool registered above stayed out of the prompt -- the request
    # never saw the full registry.
    assert tool_names.isdisjoint(
        {"run_routine", "start_timer", "show_notification", "open_application",
         "close_application", "get_time", "get_system_info"}
    )


def test_conversation_history_is_bounded_not_the_entire_history(db_session):
    conversation = _seed_conversation(db_session, message_count=MAX_RECENT_TURNS + 4)

    route_mock = _handle(db_session, str(conversation.id))

    llm_request = route_mock.await_args.args[0]
    recent_turns = llm_request.context["recent_turns"]
    assert len(recent_turns) == MAX_RECENT_TURNS
    # The oldest seeded turns were dropped; only the most recent MAX_RECENT_TURNS
    # survive, still in chronological order.
    assert [turn["content"] for turn in recent_turns] == [
        f"turn {i}" for i in range(4, MAX_RECENT_TURNS + 4)
    ]


def test_conversation_history_excludes_other_conversations(db_session):
    _seed_conversation(db_session, message_count=3)  # an unrelated conversation
    this_conversation = _seed_conversation(db_session, message_count=2)

    route_mock = _handle(db_session, str(this_conversation.id))

    llm_request = route_mock.await_args.args[0]
    recent_turns = llm_request.context["recent_turns"]
    assert [turn["content"] for turn in recent_turns] == ["turn 0", "turn 1"]


def test_no_recent_turns_key_when_conversation_has_no_history(db_session):
    conversation = _seed_conversation(db_session, message_count=0)

    route_mock = _handle(db_session, str(conversation.id))

    llm_request = route_mock.await_args.args[0]
    assert "recent_turns" not in llm_request.context


def test_no_memory_key_sent_when_no_memory_service_exists(db_session):
    conversation = _seed_conversation(db_session, message_count=0)

    route_mock = _handle(db_session, str(conversation.id))

    llm_request = route_mock.await_args.args[0]
    assert "memory" not in llm_request.context
