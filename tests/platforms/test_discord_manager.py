"""
DiscordBotManager unit tests (web dashboard follow-up to file 13).

Exercises start()/stop()/status() against a fake discord.Client stand-in --
`build_discord_client()` is monkeypatched so no real discord.py `Client` (which needs
actual network I/O to shut down cleanly) is ever constructed, same "fake the SDK
boundary, exercise the real orchestration around it" approach
tests/platforms/test_discord_capability.py's `core` fixture uses for the OS-level
`open_application` calls. Covers: disabled (no token) status, stopped -> starting ->
connected, `stop()` tearing a connected bot back down, `start()` being a no-op while
already running, and a `client.start()` that raises surfacing as "error" instead of
crashing the caller.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import app.platforms.discord as discord_module
from app.platforms.discord import DiscordBotManager


class _FakeClient:
    """Stands in for `discord.Client`. `start()` mirrors real discord.py's contract --
    it blocks until `close()` is called -- via an `asyncio.Event` this test controls,
    instead of ever touching the network. `fail=True` makes `start()` raise instead,
    covering the "bad token" / connection-failure path.
    """

    def __init__(self, *, fail: bool = False) -> None:
        self._ready = False
        self._closed = False
        self._close_event = asyncio.Event()
        self._fail = fail
        self.user = "TestBot#0001"

    def is_ready(self) -> bool:
        return self._ready

    def is_closed(self) -> bool:
        return self._closed

    async def start(self, token: str) -> None:
        if self._fail:
            raise RuntimeError("bad token")
        self._ready = True
        await self._close_event.wait()

    async def close(self) -> None:
        self._closed = True
        self._ready = False
        self._close_event.set()


def _configure(monkeypatch: pytest.MonkeyPatch, token: str | None) -> None:
    monkeypatch.setattr(discord_module, "get_settings", lambda: SimpleNamespace(discord_bot_token=token))


@pytest.fixture()
def manager() -> DiscordBotManager:
    return DiscordBotManager()


def test_status_disabled_when_no_token(manager: DiscordBotManager, monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, None)
    assert manager.status() == {"configured": False, "state": "disabled", "username": None, "error": None}


@pytest.mark.asyncio
async def test_start_is_noop_when_not_configured(manager: DiscordBotManager, monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, None)
    await manager.start()
    assert manager.running is False
    assert manager.status()["state"] == "disabled"


@pytest.mark.asyncio
async def test_start_connects_then_stop_disconnects(manager: DiscordBotManager, monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, "fake-token")
    fake_client = _FakeClient()
    monkeypatch.setattr(discord_module, "build_discord_client", lambda: fake_client)

    await manager.start()
    await asyncio.sleep(0)  # let the background task's client.start() actually run

    status = manager.status()
    assert status == {"configured": True, "state": "connected", "username": "TestBot#0001", "error": None}
    assert manager.running is True

    await manager.stop()
    assert manager.running is False
    assert manager.status()["state"] == "stopped"
    assert fake_client.is_closed() is True


@pytest.mark.asyncio
async def test_start_is_noop_while_already_running(manager: DiscordBotManager, monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, "fake-token")
    built: list[_FakeClient] = []

    def _build() -> _FakeClient:
        client = _FakeClient()
        built.append(client)
        return client

    monkeypatch.setattr(discord_module, "build_discord_client", _build)

    await manager.start()
    await asyncio.sleep(0)
    await manager.start()  # second call, while still connected -- must not build a new client

    assert len(built) == 1
    await manager.stop()


@pytest.mark.asyncio
async def test_failed_connection_surfaces_as_error_state(
    manager: DiscordBotManager, monkeypatch: pytest.MonkeyPatch
):
    _configure(monkeypatch, "fake-token")
    monkeypatch.setattr(discord_module, "build_discord_client", lambda: _FakeClient(fail=True))

    await manager.start()
    await asyncio.sleep(0)  # let the background task's client.start() raise

    status = manager.status()
    assert status["state"] == "error"
    assert status["error"] == "bad token"
    assert manager.running is False  # the task finished (with an exception), not still running


@pytest.mark.asyncio
async def test_stop_is_noop_when_never_started(manager: DiscordBotManager, monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, "fake-token")
    await manager.stop()  # must not raise
    assert manager.status()["state"] == "stopped"
