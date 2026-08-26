"""
HealthManager tests (§6, file 06).

Exercises the in-memory state-machine bookkeeping in isolation from `AIRouter`/any
real provider: a fresh provider starts AVAILABLE, each `LLMResult` status drives the
transition §6 describes, a SUCCESS clears every failure signal (even a sticky
MISCONFIGURED), and `is_usable()` enforces cooldowns -- False while a cooldown is
still running, True once it has elapsed (via a monkeypatched clock, never a real
`sleep`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config.settings import Settings
from app.llm.base import LLMResult
from app.llm.health import HealthManager, ProviderHealthState, _utcnow


def _settings(**overrides) -> Settings:
    defaults: dict = dict(
        llm_quota_cooldown_seconds=60.0,
        llm_retryable_error_threshold=3,
        llm_retryable_cooldown_seconds=30.0,
        llm_permanent_error_threshold=3,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _mock_clock(monkeypatch, *, start: datetime | None = None):
    """Patches `app.llm.health._utcnow` to a controllable clock, returning a callable
    that advances it by a `timedelta`. Keeps this module's cooldown tests from ever
    depending on real wall-clock time / `sleep`.
    """
    current = {"now": start or datetime(2026, 1, 1, tzinfo=timezone.utc)}

    def _now() -> datetime:
        return current["now"]

    def _advance(delta: timedelta) -> None:
        current["now"] += delta

    monkeypatch.setattr("app.llm.health._utcnow", _now)
    return _advance


def test_unknown_provider_starts_available_and_usable():
    manager = HealthManager(settings=_settings())

    status = manager.get_status("gemini")

    assert status.state == ProviderHealthState.AVAILABLE
    assert status.healthy is True
    assert status.quota_state == "NORMAL"
    assert manager.is_usable("gemini") is True


def test_quota_exhausted_marks_unhealthy_and_starts_a_cooldown(monkeypatch):
    advance = _mock_clock(monkeypatch)
    manager = HealthManager(settings=_settings(llm_quota_cooldown_seconds=60.0))

    manager.record_result(
        "gemini", LLMResult(status="QUOTA_EXHAUSTED", error_type="resource_exhausted")
    )

    status = manager.get_status("gemini")
    assert status.state == ProviderHealthState.QUOTA_EXHAUSTED
    assert status.healthy is False
    assert status.quota_state == "EXHAUSTED"
    assert status.last_error == "resource_exhausted"
    assert manager.is_usable("gemini") is False

    # Cooldown still running -- not usable yet.
    advance(timedelta(seconds=59))
    assert manager.is_usable("gemini") is False

    # Cooldown elapsed -- usable again, and the state is restored to AVAILABLE.
    advance(timedelta(seconds=2))
    assert manager.is_usable("gemini") is True
    assert manager.get_status("gemini").state == ProviderHealthState.AVAILABLE


def test_retryable_errors_below_threshold_do_not_flip_state():
    manager = HealthManager(settings=_settings(llm_retryable_error_threshold=3))

    manager.record_result("gemini", LLMResult(status="RETRYABLE_ERROR", error_type="timeout"))
    manager.record_result("gemini", LLMResult(status="RETRYABLE_ERROR", error_type="timeout"))

    status = manager.get_status("gemini")
    assert status.consecutive_retryable_errors == 2
    assert status.state == ProviderHealthState.AVAILABLE
    assert manager.is_usable("gemini") is True


def test_retryable_errors_at_threshold_marks_unavailable_with_cooldown(monkeypatch):
    advance = _mock_clock(monkeypatch)
    manager = HealthManager(
        settings=_settings(llm_retryable_error_threshold=3, llm_retryable_cooldown_seconds=30.0)
    )

    for _ in range(3):
        manager.record_result("gemini", LLMResult(status="RETRYABLE_ERROR", error_type="timeout"))

    status = manager.get_status("gemini")
    assert status.state == ProviderHealthState.UNAVAILABLE
    assert status.healthy is False
    assert manager.is_usable("gemini") is False

    advance(timedelta(seconds=29))
    assert manager.is_usable("gemini") is False

    advance(timedelta(seconds=2))
    assert manager.is_usable("gemini") is True


def test_permanent_error_below_threshold_is_misconfigured_with_no_cooldown():
    manager = HealthManager(settings=_settings(llm_permanent_error_threshold=3))

    manager.record_result("gemini", LLMResult(status="PERMANENT_ERROR", error_type="invalid_api_key"))

    status = manager.get_status("gemini")
    assert status.state == ProviderHealthState.MISCONFIGURED
    assert status.healthy is False
    assert status.cooldown_until is None
    # MISCONFIGURED is sticky -- no cooldown to wait out, so it's simply not usable.
    assert manager.is_usable("gemini") is False


def test_permanent_error_at_threshold_is_disabled_and_stays_disabled_over_time(monkeypatch):
    advance = _mock_clock(monkeypatch)
    manager = HealthManager(settings=_settings(llm_permanent_error_threshold=3))

    for _ in range(3):
        manager.record_result("gemini", LLMResult(status="PERMANENT_ERROR", error_type="invalid_api_key"))

    status = manager.get_status("gemini")
    assert status.state == ProviderHealthState.DISABLED
    assert manager.is_usable("gemini") is False

    # DISABLED has no cooldown -- time passing alone never restores it.
    advance(timedelta(days=365))
    assert manager.is_usable("gemini") is False


def test_reset_clears_disabled_state_back_to_available():
    manager = HealthManager(settings=_settings(llm_permanent_error_threshold=1))

    manager.record_result("gemini", LLMResult(status="PERMANENT_ERROR", error_type="invalid_api_key"))
    assert manager.get_status("gemini").state == ProviderHealthState.DISABLED
    assert manager.is_usable("gemini") is False

    manager.reset("gemini")

    status = manager.get_status("gemini")
    assert status.state == ProviderHealthState.AVAILABLE
    assert status.healthy is True
    assert manager.is_usable("gemini") is True


def test_success_clears_every_failure_signal_including_sticky_misconfigured():
    manager = HealthManager(settings=_settings(llm_permanent_error_threshold=3))

    manager.record_result("gemini", LLMResult(status="PERMANENT_ERROR", error_type="invalid_api_key"))
    assert manager.get_status("gemini").state == ProviderHealthState.MISCONFIGURED

    manager.record_result("gemini", LLMResult(status="SUCCESS", text="ok"))

    status = manager.get_status("gemini")
    assert status.state == ProviderHealthState.AVAILABLE
    assert status.healthy is True
    assert status.quota_state == "NORMAL"
    assert status.last_error is None
    assert status.cooldown_until is None
    assert status.consecutive_retryable_errors == 0
    assert status.consecutive_permanent_errors == 0
    assert manager.is_usable("gemini") is True
