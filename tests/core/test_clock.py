"""
Timezone correctness for every user-facing wall clock (`app/core/clock.py`).

Regression cover for a real, reported bug: asked "what time is it", the deployed
assistant answered 09:16 AM to a user whose own clock read 5:16 PM. The date was right,
which is exactly what made it confusing -- `get_time` was reading `datetime.now()`, and
"now" on a Render container is UTC, eight hours behind the user. The same host-clock
assumption silently mis-scheduled every "remind me tomorrow at 8pm".

These tests are deliberately written to be *host-independent*: they never assert "the
time equals my machine's time" (which would pass on the developer's laptop for the
exact wrong reason, since it sits in the configured zone), but instead pin two zones a
known offset apart and assert the answers differ by that offset.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.config.settings import Settings
from app.core import clock
from app.core.clock import local_now, local_now_aware, timezone_label
from app.tools.system import get_time_tool


@pytest.fixture()
def set_timezone(monkeypatch):
    """Pin `ASSISTANT_TIMEZONE` for one test, bypassing the real .env/`lru_cache`."""

    def _set(name: str) -> None:
        settings = Settings(_env_file=None, assistant_timezone=name)
        monkeypatch.setattr(clock, "get_settings", lambda: settings)
        clock._warned_names.clear()  # a fresh warn-once state per test

    return _set


def test_local_now_follows_the_configured_zone_not_the_host(set_timezone):
    set_timezone("UTC")
    in_utc = local_now()

    set_timezone("Asia/Kuala_Lumpur")
    in_kl = local_now()

    # KL is a fixed UTC+8 (no DST), so the two readings must differ by 8h regardless of
    # which timezone the machine running this test is in -- the whole point of the fix.
    offset_hours = (in_kl - in_utc).total_seconds() / 3600
    assert 7.9 < offset_hours < 8.1


def test_local_now_is_naive_so_it_stays_comparable_with_stored_datetimes(set_timezone):
    # Everything persisted in this app is naive local (see app/database/models.py);
    # an aware `local_now()` would raise "can't compare offset-naive and offset-aware"
    # the first time a reminder was checked.
    set_timezone("Asia/Kuala_Lumpur")

    assert local_now().tzinfo is None


def test_local_now_aware_carries_the_offset(set_timezone):
    set_timezone("Asia/Kuala_Lumpur")

    aware = local_now_aware()

    assert aware.tzinfo is not None
    assert aware.utcoffset().total_seconds() == 8 * 3600


def test_blank_timezone_falls_back_to_the_host_clock(set_timezone):
    """Unset is a valid configuration, not an error -- it means "this backend runs on
    the user's own machine", which is true for the desktop install.
    """
    set_timezone("")

    assert clock.get_timezone() is None
    assert abs((local_now() - datetime.now()).total_seconds()) < 5


def test_unknown_timezone_degrades_instead_of_crashing(set_timezone, caplog):
    set_timezone("Not/AZone")

    with caplog.at_level("WARNING"):
        now = local_now()

    assert abs((now - datetime.now()).total_seconds()) < 5
    assert "ASSISTANT_TIMEZONE" in caplog.text


# --- get_time, the tool the bug was actually reported against -------------------------


def test_get_time_reports_the_configured_zone(set_timezone):
    set_timezone("UTC")
    utc_iso = get_time_tool.handler().data["iso"]

    set_timezone("Asia/Kuala_Lumpur")
    kl_result = get_time_tool.handler()

    assert kl_result.success is True
    # Compare the *wall clock* each call reports (tzinfo dropped) -- both `iso` values
    # name the same instant by design, and it's the displayed clock face that was wrong.
    kl = datetime.fromisoformat(kl_result.data["iso"]).replace(tzinfo=None)
    utc = datetime.fromisoformat(utc_iso).replace(tzinfo=None)
    offset_hours = (kl - utc).total_seconds() / 3600
    # Same 8h check as above, but through the tool the user actually hit: reading the
    # host clock, both calls would have returned the identical (server-timezone) face.
    assert 7.9 < offset_hours < 8.1
    assert kl.hour == datetime.now(ZoneInfo("Asia/Kuala_Lumpur")).hour
    assert kl_result.data["timezone"] == "Asia/Kuala_Lumpur"


def test_get_time_message_names_the_zone_and_drops_the_padding_zero(set_timezone):
    set_timezone("Asia/Kuala_Lumpur")

    message = get_time_tool.handler().data["message"]

    assert "Asia/Kuala_Lumpur" in message
    # "05:16 PM" reads as a timestamp, not as a person telling you the time.
    assert "It's currently 0" not in message


def test_timezone_label_is_the_configured_name(set_timezone):
    set_timezone("Asia/Kuala_Lumpur")

    assert timezone_label() == "Asia/Kuala_Lumpur"
