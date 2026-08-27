"""
DiagnosticsService: the "run the whole system to see what's broken" self-test.

One button on the dashboard (Status tab) runs every check below and reports pass/fail
per component -- database, tool registry, at-least-one-user auth, both LLM providers,
both voice providers, the Discord bot, and the routine registry -- instead of an
operator having to guess which one is broken from backend logs. `run(only=...)` lets
the dashboard re-test a single suspect component (e.g. just Ollama after restarting it)
without re-running everything else.

Every `_check_*` method has the same contract: takes no args beyond `self`, never
raises, and returns a `CheckResult`. `run()` still wraps each call in a `try/except`
regardless -- one check's unexpected bug must not take down the whole diagnostic run
(a broken Ollama probe must not stop the database check from being reported) -- but
each check is also expected to do its own internal `try/except` where it calls out to
something that can genuinely fail (same "never raise, always report" convention
`is_available()` follows across every provider protocol in `app.llm`/`app.voice`, §41
Rule 3).

Every check here is read-only / non-mutating against real external services: no
message is sent, no cron job triggered, no file written. `RoutineEngine.run()` (running
an actual routine's steps) is deliberately NOT one of these checks for that reason --
a routine's steps can have real side effects (opening an application, sending a Discord
message), which a diagnostic sweep must never risk triggering just to find out "does
this work".
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.database.database import SessionLocal
from app.database.models import Routine, User
from app.llm.gemini import GeminiProvider
from app.llm.health import HealthManager
from app.llm.ollama import OllamaProvider
from app.platforms.discord import DiscordBotManager
from app.tools.registry import ToolRegistry
from app.voice.stt import SpeechToTextProvider
from app.voice.tts import TextToSpeechProvider


@dataclass
class CheckResult:
    """One component's outcome. `ok=False` means "broken or needs attention";
    `message` is always human-readable enough to show directly in the dashboard --
    no separate error-code lookup needed.
    """

    name: str
    label: str
    ok: bool
    message: str
    duration_ms: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)


def _timed(fn: Callable[[], CheckResult]) -> CheckResult:
    start = time.monotonic()
    result = fn()
    result.duration_ms = round((time.monotonic() - start) * 1000, 1)
    return result


class DiagnosticsService:
    """Runs the check battery. Every dependency is injected (mirrors `RoutineEngine`/
    `ToolExecutor`'s own constructor style) rather than reached for globally, so tests
    can swap in fakes the same way `tests/api/test_discord.py` overrides
    `get_discord_bot_manager` -- except here the swap happens at construction time
    since this class isn't itself a FastAPI dependency.
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        discord_manager: DiscordBotManager,
        stt_provider: SpeechToTextProvider,
        tts_provider: TextToSpeechProvider,
        health_manager: HealthManager | None = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._discord_manager = discord_manager
        self._stt_provider = stt_provider
        self._tts_provider = tts_provider
        self._health_manager = health_manager

    # Name -> (label, bound check method). Order here is the order results come back
    # in, roughly "most foundational first" (a broken database makes every other
    # check's own DB access suspect too, so it's worth seeing first).
    def _checks(self) -> list[tuple[str, str, Callable[[], CheckResult]]]:
        return [
            ("database", "Database", self._check_database),
            ("tool_registry", "Tool registry", self._check_tool_registry),
            ("auth", "Auth (user account)", self._check_auth),
            ("llm_gemini", "Gemini (LLM)", self._check_gemini),
            ("llm_ollama", "Ollama (LLM fallback)", self._check_ollama),
            ("voice_stt", "Speech-to-text", self._check_stt),
            ("voice_tts", "Text-to-speech", self._check_tts),
            ("discord_bot", "Discord bot", self._check_discord),
            ("routines", "Routine registry", self._check_routines),
        ]

    def list_checks(self) -> list[dict[str, str]]:
        """Component catalog for the dashboard's checkbox list -- name/label pairs
        only, no results, so `GET /api/diagnostics/checks` can render "which
        components exist" before anything has actually been run.
        """
        return [{"name": name, "label": label} for name, label, _ in self._checks()]

    def run(self, only: list[str] | None = None) -> list[CheckResult]:
        """Run every check, or just the ones named in `only` (customize which
        component gets tested, e.g. re-checking just Ollama). An unknown name in
        `only` is silently ignored rather than raising -- the route layer is
        responsible for rejecting genuinely unknown names before this is called, same
        split as `app.api.routes.routines`' `_as_step_tuples` validating tool names up
        front.
        """
        selected = set(only) if only is not None else None
        results: list[CheckResult] = []
        for name, label, check_fn in self._checks():
            if selected is not None and name not in selected:
                continue
            try:
                results.append(_timed(check_fn))
            except Exception as exc:  # noqa: BLE001 -- a check's own bug must not
                # take the rest of the sweep down with it (see module docstring).
                results.append(
                    CheckResult(name=name, label=label, ok=False, message=f"Check crashed: {exc}")
                )
        return results

    # -- individual checks ------------------------------------------------------

    def _check_database(self) -> CheckResult:
        try:
            with SessionLocal() as db:
                db.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            return CheckResult("database", "Database", False, f"Unreachable: {exc}")
        return CheckResult("database", "Database", True, "Connected.")

    def _check_tool_registry(self) -> CheckResult:
        tools = self._tool_registry.list()
        if not tools:
            return CheckResult(
                "tool_registry", "Tool registry", False, "No tools registered."
            )
        return CheckResult(
            "tool_registry", "Tool registry", True, f"{len(tools)} tool(s) registered."
        )

    def _check_auth(self) -> CheckResult:
        try:
            with SessionLocal() as db:
                count = db.query(User).count()
        except Exception as exc:  # noqa: BLE001
            return CheckResult("auth", "Auth (user account)", False, f"Query failed: {exc}")
        if count == 0:
            return CheckResult(
                "auth",
                "Auth (user account)",
                False,
                "No user account exists yet -- set AUTH_SEED_USERNAME/AUTH_SEED_PASSWORD "
                "or register one.",
            )
        return CheckResult("auth", "Auth (user account)", True, f"{count} user account(s).")

    def _check_gemini(self) -> CheckResult:
        settings = get_settings()
        provider = GeminiProvider(settings=settings)
        if not provider.is_available():
            return CheckResult(
                "llm_gemini", "Gemini (LLM)", False, "GEMINI_API_KEY isn't configured."
            )
        message = "API key configured."
        if self._health_manager is not None:
            status = self._health_manager.get_status("gemini")
            message = f"API key configured. Live health: {status.state.value}."
            if not status.healthy and status.last_error:
                return CheckResult(
                    "llm_gemini", "Gemini (LLM)", False, f"{message} Last error: {status.last_error}"
                )
        return CheckResult("llm_gemini", "Gemini (LLM)", True, message)

    def _check_ollama(self) -> CheckResult:
        settings = get_settings()
        provider = OllamaProvider(settings=settings)
        if provider.is_available():
            return CheckResult(
                "llm_ollama",
                "Ollama (LLM fallback)",
                True,
                f"Reachable at {settings.ollama_base_url}, model '{settings.ollama_model}' pulled.",
            )
        if not settings.ollama_enabled:
            return CheckResult(
                "llm_ollama", "Ollama (LLM fallback)", True, "Disabled via OLLAMA_ENABLED=false."
            )
        return CheckResult(
            "llm_ollama",
            "Ollama (LLM fallback)",
            False,
            f"Unreachable at {settings.ollama_base_url}, or model "
            f"'{settings.ollama_model}' isn't pulled.",
        )

    def _check_stt(self) -> CheckResult:
        if self._stt_provider.is_available():
            return CheckResult("voice_stt", "Speech-to-text", True, "faster-whisper is installed.")
        return CheckResult(
            "voice_stt", "Speech-to-text", False, "faster-whisper isn't installed."
        )

    def _check_tts(self) -> CheckResult:
        if self._tts_provider.is_available():
            return CheckResult("voice_tts", "Text-to-speech", True, "pyttsx3 voice driver bound.")
        return CheckResult(
            "voice_tts", "Text-to-speech", False, "No pyttsx3 voice driver available on this OS."
        )

    def _check_discord(self) -> CheckResult:
        status = self._discord_manager.status()
        state = status.get("state")
        if state == "error":
            return CheckResult(
                "discord_bot", "Discord bot", False, status.get("error") or "Bot connection failed."
            )
        if state == "disabled":
            return CheckResult(
                "discord_bot", "Discord bot", True, "DISCORD_BOT_TOKEN isn't set -- not in use."
            )
        if state == "connected":
            username = status.get("username") or "unknown user"
            return CheckResult("discord_bot", "Discord bot", True, f"Connected as {username}.")
        return CheckResult("discord_bot", "Discord bot", True, f"State: {state}.")

    def _check_routines(self) -> CheckResult:
        try:
            with SessionLocal() as db:
                count = self._count_routines(db)
        except Exception as exc:  # noqa: BLE001
            return CheckResult("routines", "Routine registry", False, f"Query failed: {exc}")
        return CheckResult("routines", "Routine registry", True, f"{count} routine(s) registered.")

    @staticmethod
    def _count_routines(db: Session) -> int:
        return db.query(Routine).count()
