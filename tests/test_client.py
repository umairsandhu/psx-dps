"""Client tests against a stubbed transport -- no network.

The TTL assertions are the load-protection contract: when Karachi is shut,
repeated calls must not generate repeated upstream traffic.
"""

import datetime as dt
import json

import conftest
import pytest
from conftest import fixture
from psx_dps import market
from psx_dps.client import Client
from psx_dps.errors import NoData, UnknownSymbol

SYMBOLS = json.dumps([
    {"symbol": "MARI", "name": "Mari Energies", "sectorName": "OIL", "isETF": False, "isDebt": False},
    {"symbol": "ENGRO", "name": "Engro Corp", "sectorName": "FERT", "isETF": False, "isDebt": False},
    {"symbol": "AKBLTFC6", "name": "Askari TFC", "sectorName": "BONDS", "isETF": False, "isDebt": True},
    {"symbol": "NITGETF", "name": "NIT ETF", "sectorName": "ETF", "isETF": True, "isDebt": False},
])

EOD = json.dumps({"status": 1, "message": "", "data": [
    [1790593200, 635.09, 244578, 640.97],
    [1790334000, 639.62, 286082, 639.0],
]})


class StubTransport:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def request(self, path, body=None, ttl=0, force_refresh=False):
        self.calls.append({"path": path, "ttl": ttl, "body": body})
        for prefix, payload in self.routes.items():
            if path.startswith(prefix):
                return payload
        raise AssertionError(f"unstubbed path {path}")

    def close(self):
        pass


def client(routes, **kw):
    psx = Client(cache=False, **kw)
    psx.transport = StubTransport(routes)
    return psx


def test_symbols_filters_debt_by_default():
    psx = client({"/symbols": SYMBOLS})
    assert {r["symbol"] for r in psx.symbols()} == {"MARI", "ENGRO", "NITGETF"}
    assert len(psx.symbols(include_debt=True)) == 4
    assert [r["symbol"] for r in psx.symbols(etf_only=True)] == ["NITGETF"]


def test_search_matches_symbol_or_company_name():
    psx = client({"/symbols": SYMBOLS})
    assert [r["symbol"] for r in psx.search("mari")] == ["MARI"]
    assert [r["symbol"] for r in psx.search("Engro Corp")] == ["ENGRO"]


def test_quotes_cost_one_request_for_many_symbols():
    """The anti-hammering property: N symbols, 1 upstream call."""
    psx = client({"/market-watch": fixture("market_watch.html")})
    rows = psx.market_watch()
    wanted = [r["symbol"] for r in rows][:3]
    psx.quotes(wanted)
    paths = [c["path"] for c in psx.transport.calls]
    assert paths.count("/market-watch") == 2  # one per call, not one per symbol


def test_unknown_symbol_and_untraded_symbol_differ():
    psx = client({
        "/market-watch": fixture("market_watch.html"),
        "/symbols": SYMBOLS,
    })
    with pytest.raises(NoData):
        psx.quote("ENGRO")          # listed, absent from the watch
    with pytest.raises(UnknownSymbol):
        psx.quote("NOTREAL")        # not listed at all


def test_eod_is_chronological_and_dated():
    psx = client({"/timeseries/eod/": EOD})
    bars = psx.eod("MARI")
    assert [b["date"] for b in bars] == ["2026-09-25", "2026-09-28"]
    assert bars[0]["open"] == 639.0 and bars[0]["close"] == 639.62


def test_eod_since_filter():
    psx = client({"/timeseries/eod/": EOD})
    assert len(psx.eod("MARI", since="2026-09-28")) == 1


def test_empty_series_reads_as_unknown_symbol():
    """The eod route answers status=1 with [] for symbols it does not know."""
    psx = client({"/timeseries/eod/": json.dumps({"status": 1, "data": []})})
    with pytest.raises(UnknownSymbol):
        psx.eod("NOPE")


def test_ttl_is_short_while_the_market_trades():
    psx = client({"/market-watch": fixture("market_watch.html")},
                 is_open=lambda: True)
    psx.market_watch()
    assert psx.transport.calls[0]["ttl"] == 60


def test_ttl_stretches_to_the_next_open_once_closed(monkeypatch):
    """Nothing can change overnight, so stop asking until the bell."""
    saturday = dt.datetime(2026, 9, 26, 11, 0, tzinfo=market.PKT)
    monkeypatch.setattr(market, "now_pkt", lambda: saturday)
    psx = client({"/market-watch": fixture("market_watch.html")},
                 is_open=lambda: False)
    psx.market_watch()
    ttl = psx.transport.calls[0]["ttl"]
    assert ttl >= 3600, f"weekend TTL was only {ttl}s"


def test_past_days_are_cached_for_a_day():
    """A closed trading day is immutable -- never re-fetch it within 24h."""
    psx = client({"/historical": fixture("history_month.html")})
    psx.history_by_date("2020-01-02")
    assert psx.transport.calls[0]["ttl"] == 24 * 3600


def test_announcements_validate_kind():
    psx = client({"/announcements": fixture("announcements.html")})
    with pytest.raises(ValueError):
        psx.announcements(kind="nonsense")


def test_announcements_carry_pdf_urls():
    psx = client({"/announcements": fixture("announcements.html")})
    rows = psx.announcements(count=3)
    assert any(r.get("url", "").endswith(".pdf") for r in rows)


def test_epoch_reading_ignores_host_timezone(monkeypatch):
    """PSX stamps are PKT wall clock; a US laptop must see the same date."""
    import os
    import time as _time
    from psx_dps.client import _epoch

    original = os.environ.get("TZ")
    seen = set()
    try:
        for zone in ("UTC", "America/New_York", "Asia/Karachi"):
            os.environ["TZ"] = zone
            _time.tzset()
            seen.add(_epoch(1790593200).strftime("%Y-%m-%d %H:%M"))
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        _time.tzset()
    assert len(seen) == 1, seen
