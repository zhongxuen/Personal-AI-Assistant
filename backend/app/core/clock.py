"""
Wall-clock time in the *user's* timezone, not the server's.

Every "what time is it"-shaped answer in this app used to come straight from
`datetime.now()`, i.e. whatever timezone the machine running the backend happens to be
in. That is correct on a desktop install (the machine is the user's machine) and wrong
the moment the backend is deployed: Render runs in UTC, so `get_time` cheerfully
reported 09:16 to a user whose clock said 17:16 -- right date, eight-hour-wrong time.

`local_now()` is the fix and the convention: anywhere the app means "the wall clock the
user is reading" -- reporting the time, resolving "tomorrow at 8pm", deciding whether a
reminder is due -- call this instead of `datetime.now()`. It returns a *naive* datetime
in `Settings.assistant_timezone`, deliberately: every datetime persisted in this app is
naive local (see `app.database.models`' note on `TaskReminder.remind_at`), so returning
an aware value here would poison those comparisons with "can't compare offset-naive and
offset-aware datetime" errors. Shifting the reference clock without changing the naive
convention keeps every existing comparison valid -- it just anchors them all to the
user's zone instead of the host's.

Not everything that calls `datetime.now()` should switch: infrastructure timing that
means "the host's real clock" (APScheduler's `next_run_time`, monotonic latency
measurement, log timestamps) must keep using the real system clock, or a job scheduled
"now" would be queued hours away. The rule is about *user-facing wall clock*, nothing
else.

An unset or unrecognized `assistant_timezone` falls back to the system clock -- the old
behavior, which is still the right answer for a desktop install -- with one warning
logged rather than an exception (§41 Rule 3: a config problem degrades, never crashes).
"""

from __future__ import annotations

import logging
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

# Names already warned about, so a misconfigured timezone logs once at first use
# instead of on every single request that asks for the time.
_warned_names: set[str] = set()


def get_timezone() -> tzinfo | None:
    """The configured `ZoneInfo`, or None to mean "use the system clock" -- either
    because `assistant_timezone` is blank or because it doesn't name a real IANA zone
    (which is logged once, then treated the same as blank).
    """
    name = (get_settings().assistant_timezone or "").strip()
    if not name:
        return None
    try:
        # ZoneInfo caches instances internally, so this is cheap to call per request.
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        if name not in _warned_names:
            _warned_names.add(name)
            logger.warning(
                "ASSISTANT_TIMEZONE=%r is not a known IANA timezone name (e.g. "
                "'Asia/Kuala_Lumpur', 'UTC'); falling back to this machine's local "
                "clock, which is wrong for any non-local deployment.",
                name,
            )
        return None


def timezone_label() -> str:
    """Short human-readable name for the zone answers are given in -- the configured
    IANA name, or the system clock's abbreviation (e.g. "Malay Peninsula Standard
    Time") when nothing is configured. For display only; never parse this.
    """
    tz = get_timezone()
    if tz is not None:
        return str(tz)
    return datetime.now().astimezone().tzname() or "local time"


def local_now() -> datetime:
    """Now, as a *naive* datetime in the configured timezone -- the app-wide
    replacement for `datetime.now()` wherever "now" means the user's wall clock. See
    the module docstring for why this is naive rather than aware.
    """
    tz = get_timezone()
    if tz is None:
        return datetime.now()
    return datetime.now(tz).replace(tzinfo=None)


def local_now_aware() -> datetime:
    """Same instant as `local_now()` but timezone-aware, for the rare caller that
    needs the offset visible -- `get_time`'s machine-readable `iso` field, so a client
    reading it can tell "17:16+08:00" from "17:16" with no offset at all.
    """
    tz = get_timezone()
    if tz is None:
        return datetime.now().astimezone()
    return datetime.now(tz)
