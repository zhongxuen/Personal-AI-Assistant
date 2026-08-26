"""
Context selection (§16, file 08 prompt 3).

`AssistantCore._handle_needs_llm` (file 05) used to build `LLMRequest.context` from
nothing but `{"user_id": ..., "platform": ...}` -- never any conversation history, never
memory -- so there was nothing to narrow yet. `build_context` replaces that with the
real selection step: current message context, plus a *bounded* number of the most
recent turns for this same `conversation_id` (never the full history), plus whatever
memory (file 09) is relevant to the message, if a memory service actually exists.

Conversation turns come from the `conversations`/`conversation_messages` tables (already
defined in `app.database.models`, file 01) -- `AssistantRequest.conversation_id` is the
string form of a `Conversation.id` primary key. Nothing in the codebase persists turns
into those tables yet (no platform adapter/AssistantCore writes them -- that's a future
phase's job), so today `_recent_turns` almost always returns `[]`; the bounding logic is
exercised now by test_context_reduction.py seeding rows directly, and stays correct
once a write path lands. Until then, `conversation_id` not parsing as an int, or
matching no persisted conversation, is not an error (§41 Rule 3) -- it just means no
history is available for this turn.

Memory retrieval goes through `app.memory.retrieval.retrieve_relevant` (file 09 prompt
3), which maps the message's classified tool category (`app.tools.relevance`) to the
`MemoryService` categories worth pulling -- see that module's docstring for the actual
category mapping. `_relevant_memory` keeps the import guarded (rather than a hard
top-level import) and still degrades to a no-op (empty dict) if that module is ever
absent or raises, so a memory-layer bug never takes down context building for the rest
of the turn (§41 Rule 3) -- but in normal operation this is real retrieval, not a
placeholder.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.models import AssistantRequest
from app.database.models import ConversationMessage

logger = logging.getLogger(__name__)

# "Bounded", not "the entire history" -- three user/assistant exchanges is enough for
# the model to keep the immediate thread of conversation without the prompt growing
# unbounded as a conversation gets long (the whole point of this file, per §16).
MAX_RECENT_TURNS = 6


def _recent_turns(db: Session | None, conversation_id: str | None, limit: int = MAX_RECENT_TURNS) -> list[dict[str, str]]:
    """The most recent `limit` turns for `conversation_id`, oldest first, as plain
    `{"role", "content"}` dicts -- never the whole conversation. Returns `[]` (never
    raises) whenever there's no db, no conversation_id, it doesn't resolve to a
    persisted conversation, or the query itself fails -- absence of history is always a
    safe, silent no-op here (§41 Rule 3), not something callers need to branch on.
    """
    if db is None or not conversation_id:
        return []

    try:
        conversation_pk = int(conversation_id)
    except (TypeError, ValueError):
        # Not (yet) a persisted Conversation's primary key -- e.g. a platform-generated
        # session token from before any turn was ever recorded. No history to pull.
        return []

    try:
        rows = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.conversation_id == conversation_pk)
            .order_by(ConversationMessage.id.desc())
            .limit(limit)
            .all()
        )
    except Exception:  # noqa: BLE001 -- a context-building step must never crash the turn
        logger.warning("Failed to load recent conversation turns for context.", exc_info=True)
        return []

    rows.reverse()  # chronological order, oldest first, for the prompt
    return [{"role": row.role, "content": row.content} for row in rows]


def _relevant_memory(message: str) -> dict[str, Any]:
    """Whatever memory (file 09, `app.memory.retrieval.retrieve_relevant`) is relevant
    to `message`, or `{}` if that module is absent / errors -- see module docstring.
    """
    try:
        from app.memory.retrieval import retrieve_relevant
    except ImportError:
        return {}

    try:
        return retrieve_relevant(message) or {}
    except Exception:  # noqa: BLE001 -- same no-op-on-failure rule as conversation turns
        logger.warning("Memory retrieval failed; continuing without it.", exc_info=True)
        return {}


def build_context(request: AssistantRequest, db: Session | None = None) -> dict[str, Any]:
    """The full `LLMRequest.context` payload for `request` -- current-message context
    plus bounded recent history plus relevant memory, replacing file 05's naive
    `{"user_id", "platform"}`-only context. `recent_turns`/`memory` keys are omitted
    entirely (not sent as empty lists/dicts) when there's nothing to include, so a
    narrow request's prompt doesn't grow just from key noise.
    """
    context: dict[str, Any] = {
        "user_id": request.user_id,
        "platform": request.platform,
    }

    recent_turns = _recent_turns(db, request.conversation_id)
    if recent_turns:
        context["recent_turns"] = recent_turns

    memory = _relevant_memory(request.message)
    if memory:
        context["memory"] = memory

    return context
