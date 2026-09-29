"""PSX trading calendar, in Pakistan Standard Time.

Only used to decide how long a cached response stays fresh: during a session
prices move, so TTLs are seconds; once the market is shut the numbers are
frozen until the next open, so we can cache hard and stop bothering PSX.

PKT is UTC+5 year-round with no daylight saving, so it is expressed as a
fixed offset rather than a tzdata lookup. Everything here is computed from
that offset and never from the host's local timezone -- a laptop set to UTC
or US/Pacific must reach the same verdict about whether Karachi is trading.
"""

import datetime as _dt

PKT = _dt.timezone(_dt.timedelta(hours=5), "PKT")

# Deliberately a little wider than the published bells. Being early/late by a
# few minutes only means we keep using short TTLs slightly longer, which errs
# toward fresh data; being too narrow would serve stale prices as if final.
SESSIONS = {
    0: [(("09:00"), ("15:45"))],  # Monday
    1: [(("09:00"), ("15:45"))],
    2: [(("09:00"), ("15:45"))],
    3: [(("09:00"), ("15:45"))],
    4: [("09:00", "12:15"), ("14:15", "16:45")],  # Friday splits for Jummah
    5: [],  # Saturday
    6: [],  # Sunday
}


def now_pkt():
    return _dt.datetime.now(tz=PKT)


def _at(day, hhmm):
    hour, minute = (int(x) for x in hhmm.split(":"))
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def is_trading_day(when=None):
    """Weekday check only -- public holidays are not modelled (see below)."""
    when = when or now_pkt()
    return bool(SESSIONS[when.weekday()])


def is_open(when=None):
    """True if Karachi is inside a trading session right now.

    PSX closes for a dozen-odd public holidays a year and the list changes
    annually, so it is not hardcoded: a stale holiday table would be worse
    than none. On a holiday this returns True and we simply use short TTLs
    against data that is not moving -- wasteful by a few requests, never
    wrong. Callers that care can pass their own `is_open` to the Client.
    """
    when = when or now_pkt()
    return any(
        _at(when, start) <= when <= _at(when, end)
        for start, end in SESSIONS[when.weekday()]
    )


def next_open(when=None):
    """Start of the next session, used to cache until the market reopens."""
    when = when or now_pkt()
    for offset in range(0, 8):
        day = when + _dt.timedelta(days=offset)
        for start, _end in SESSIONS[day.weekday()]:
            opens = _at(day, start)
            if opens > when:
                return opens
    return when + _dt.timedelta(days=1)  # unreachable in practice


def seconds_until_open(when=None):
    when = when or now_pkt()
    return max(0.0, (next_open(when) - when).total_seconds())


def session_state(when=None):
    when = when or now_pkt()
    if is_open(when):
        return "open"
    return "closed" if is_trading_day(when) else "weekend"
