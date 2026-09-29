"""Trading calendar tests.

These pin the behaviour that matters: the verdict about Karachi must not
depend on the machine's own timezone, and Friday's split session is real.
"""

import datetime as dt
import os
import time

import conftest  # noqa: F401  (puts src on the path)
from psx_dps import market


def at(y, m, d, hh, mm):
    return dt.datetime(y, m, d, hh, mm, tzinfo=market.PKT)


def test_weekday_session():
    assert market.is_open(at(2026, 9, 29, 11, 0))      # Tuesday mid-morning
    assert not market.is_open(at(2026, 9, 29, 17, 0))  # after the close
    assert not market.is_open(at(2026, 9, 29, 7, 0))   # before the open


def test_friday_splits_for_jummah():
    assert market.is_open(at(2026, 9, 25, 10, 0))
    assert not market.is_open(at(2026, 9, 25, 13, 0)), "Jummah break"
    assert market.is_open(at(2026, 9, 25, 15, 0))


def test_weekend_is_closed():
    assert not market.is_open(at(2026, 9, 26, 11, 0))  # Saturday
    assert not market.is_open(at(2026, 9, 27, 11, 0))  # Sunday
    assert market.session_state(at(2026, 9, 26, 11, 0)) == "weekend"


def test_next_open_skips_the_weekend():
    nxt = market.next_open(at(2026, 9, 26, 11, 0))     # Saturday
    assert nxt.date() == dt.date(2026, 9, 28)          # Monday
    assert nxt.hour == 9


def test_state_names():
    assert market.session_state(at(2026, 9, 29, 11, 0)) == "open"
    assert market.session_state(at(2026, 9, 29, 20, 0)) == "closed"


def test_verdict_ignores_the_host_timezone():
    """A laptop in Los Angeles must agree with one in Karachi."""
    moment = at(2026, 9, 29, 11, 0)
    original = os.environ.get("TZ")
    seen = set()
    try:
        for zone in ("UTC", "America/Los_Angeles", "Asia/Karachi", "Pacific/Auckland"):
            os.environ["TZ"] = zone
            time.tzset()
            seen.add((market.is_open(moment), market.session_state(moment)))
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()
    assert seen == {(True, "open")}, seen
