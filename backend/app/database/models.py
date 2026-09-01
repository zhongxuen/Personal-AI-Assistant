"""
SQLAlchemy ORM models — Phase 0 (project foundation).

Minimal columns only: id, the obviously-required fields, and created_at/
updated_at timestamps. Deep schema detail (e.g. routine trigger config,
memory embeddings, provider health tracking) is deliberately deferred to
the phase that actually builds each feature (04 tasks/routines, 05
provider integration, 08 usage tracking, 09 memory) — see
md-files/development-plan.md §26 and §41 Rule 1 (no over-engineering).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database.database import Base


def _pk() -> Mapped[int]:
    return mapped_column(Integer, primary_key=True, autoincrement=True)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime, server_default=func.now(), nullable=False)


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = _pk()
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    # PBKDF2-HMAC-SHA256 hash, `app/auth/security.py`'s self-describing
    # "pbkdf2_sha256$<iterations>$<salt-b64>$<hash-b64>" format (§34, file 12 prompt
    # 1) -- never a plaintext password, and never compared with anything but
    # `verify_password()`'s constant-time check. Nullable only so this column can be
    # added without breaking any pre-existing row; every user created through
    # `AuthService` always sets it.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # WhatsApp pairing (§37 Phase 13, file 18 prompt 1). WhatsApp identifies a sender by
    # phone number, not by username/password, so linking is what turns "some number
    # messaged the webhook" into "this User" -- see app/whatsapp/linking.py. Unique so a
    # number resolves to at most one account (`WhatsAppLinkService.get_by_phone_number`
    # relies on that), nullable because the overwhelmingly normal state is "this user has
    # never linked WhatsApp", exactly like `password_hash` above. Stored in Meta's own
    # wa_id form -- digits only, country code included, no "+" -- since that is what the
    # webhook payload carries and normalising at the edges beats guessing at query time.
    whatsapp_phone_number: Mapped[str | None] = mapped_column(
        String(32), unique=True, nullable=True
    )
    # The short-lived pairing code an already-logged-in user generates and then sends
    # from WhatsApp once. Cleared the moment it is consumed (single use), so a non-null
    # value here means "a pairing is in flight", not "this user is linked" --
    # `whatsapp_phone_number` is the only source of truth for that. Deliberately two
    # plain columns on `users` rather than a linking table: at most one code can be
    # outstanding per user, and generating a new one replaces the old one, so a table
    # would only ever hold one row per user anyway (§41 Rule 1).
    whatsapp_link_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Naive local datetime, matching `TaskReminder.remind_at`'s convention across this
    # file -- comparisons use `datetime.now()`, never an aware "now", so the two must
    # not be mixed.
    whatsapp_link_code_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class Setting(Base):
    __tablename__ = "settings"

    id: Mapped[int] = _pk()
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = _pk()
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    reminders: Mapped[list["TaskReminder"]] = relationship(back_populates="task")


class TaskReminder(Base):
    __tablename__ = "task_reminders"

    id: Mapped[int] = _pk()
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    remind_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _created_at()

    task: Mapped["Task"] = relationship(back_populates="reminders")


class Routine(Base):
    __tablename__ = "routines"

    id: Mapped[int] = _pk()
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    steps: Mapped[list["RoutineStep"]] = relationship(back_populates="routine")


class RoutineStep(Base):
    __tablename__ = "routine_steps"

    id: Mapped[int] = _pk()
    routine_id: Mapped[int] = mapped_column(ForeignKey("routines.id"), nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    action_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()

    routine: Mapped["Routine"] = relationship(back_populates="steps")


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = _pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str | None] = mapped_column(Text, nullable=True)
    platform: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = _created_at()


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = _pk()
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    platform: Mapped[str] = mapped_column(String(50), nullable=False, default="web")
    started_at: Mapped[datetime] = _created_at()

    messages: Mapped[list["ConversationMessage"]] = relationship(back_populates="conversation")


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[int] = _pk()
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[int] = _pk()
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False, default="general")
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class LLMProvider(Base):
    __tablename__ = "llm_providers"

    id: Mapped[int] = _pk()
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class LLMUsage(Base):
    __tablename__ = "llm_usage"

    # `QuotaManager.current_usage` runs a COUNT filtered on exactly (provider,
    # timestamp) before *every* LLM call (app/llm/quota_manager.py) -- it is the
    # pre-flight budget check `AIRouter` gates on, so it sits directly on the request
    # hot path. Without this index SQLite answers it with a full table scan of a table
    # that gains a row per LLM call and is never pruned, so the check gets steadily
    # slower the longer the install has been in use. Column order matters: `provider`
    # is the equality predicate and `timestamp` the range one, so it has to come first
    # for the range to be satisfiable from the index.
    __table_args__ = (Index("ix_llm_usage_provider_timestamp", "provider", "timestamp"),)

    id: Mapped[int] = _pk()
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    timestamp: Mapped[datetime] = _created_at()
    request_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="ok")
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    latency: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ToolExecutionLog(Base):
    __tablename__ = "tool_execution_logs"

    id: Mapped[int] = _pk()
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    params: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="ok")
    executed_at: Mapped[datetime] = _created_at()


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = _pk()
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    scope: Mapped[str] = mapped_column(String(50), nullable=False, default="default")
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _created_at()


class PushSubscription(Base):
    """One browser's Web Push subscription (`PushManager.subscribe()`'s result).

    A subscription belongs to a *browser*, not a user session -- one user with a phone
    and a laptop has two rows, so delivery fans out over every row for that user
    rather than assuming one. `endpoint` is the push service's own per-browser URL and
    is the natural key the browser hands back on re-subscribe, hence unique: the
    browser may rotate its keys for the same endpoint, and that has to update the row
    rather than accumulate stale duplicates that all push to the same place.

    `keys_p256dh`/`keys_auth` are the browser-generated encryption keys (the `keys`
    object of the subscription JSON, base64url) that payload encryption needs. They're
    per-subscription client material, not app secrets -- unrelated to the VAPID pair
    in `app/config/settings.py`, which is ours.
    """

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = _pk()
    # Nullable to match every other user-owned table here (tasks, routines, memories),
    # which were all written before authentication existed and stayed nullable so
    # pre-auth rows keep loading. The push routes always set it.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    keys_p256dh: Mapped[str] = mapped_column(String(255), nullable=False)
    keys_auth: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = _created_at()
