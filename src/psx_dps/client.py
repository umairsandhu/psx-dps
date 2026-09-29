"""The high-level client other projects are meant to import.

    from psx_dps import Client
    with Client() as psx:
        print(psx.quote("MARI"))

Caching policy lives here rather than in the cache, because how long a
response stays fresh depends on what it is *and* on whether Karachi is
trading. During a session prices move and TTLs are seconds; once the bell
goes the numbers are frozen until the next open, so we cache until then and
stop asking. That single rule is what keeps a fleet of dashboards from
generating steady overnight traffic against PSX for data that cannot change.
"""

import datetime as _dt

from . import market
from .cache import Cache
from .errors import NoData, UnknownSymbol, UpstreamError
from .parsing import drop_widget_columns, table_records, to_number
from .ratelimit import Throttle
from .transport import HOST, Transport

ANNOUNCEMENT_TYPES = {
    "cdc": "A",
    "secp": "B",
    "companies": "C",
    "nccpl": "D",
    "psx": "E",
}

INDEX_CODES = ("KSE100", "KSE30", "KMI30", "ALLSHR", "KMIALLSHR", "KSE100PR")


class Client:
    """A polite, caching client for the PSX data portal.

    Args:
        cache_dir: shared cache location. Leave it alone unless you have a
            reason -- the default is shared so several projects on one
            machine collapse into one request.
        min_interval: seconds between outbound requests, machine-wide.
        daily_budget: rolling 24h request fuse; raises RateLimited locally.
        cache: set False to bypass the cache (please don't, in a loop).
        fresh_ttl / stale_ttl: override the open/closed TTLs in seconds.
    """

    def __init__(
        self,
        cache_dir=None,
        min_interval=1.0,
        daily_budget=5000,
        cache=True,
        timeout=30.0,
        retries=3,
        fresh_ttl=None,
        stale_ttl=None,
        user_agent=None,
        is_open=None,
    ):
        store = Cache(cache_dir, enabled=cache)
        self.cache = store
        self.transport = Transport(
            cache=store,
            throttle=Throttle(min_interval, directory=store.dir,
                              daily_budget=daily_budget),
            timeout=timeout,
            retries=retries,
            user_agent=user_agent,
        )
        self._fresh_ttl = fresh_ttl
        self._stale_ttl = stale_ttl
        self._is_open = is_open or market.is_open

    # -- caching policy ----------------------------------------------------

    def _live_ttl(self, fresh=60):
        """TTL for anything that only changes while the market is trading."""
        if self._fresh_ttl is not None and self._is_open():
            return self._fresh_ttl
        if self._stale_ttl is not None and not self._is_open():
            return self._stale_ttl
        if self._is_open():
            return fresh
        # Closed: nothing can change until the bell. Cache until then (capped
        # so a wrong holiday guess cannot pin stale data for a whole weekend).
        return max(fresh, min(market.seconds_until_open(), 6 * 3600))

    def _get(self, path, ttl, body=None, force_refresh=False):
        return self.transport.request(
            path, body=body, ttl=ttl, force_refresh=force_refresh
        )

    def _get_json(self, path, ttl, force_refresh=False):
        import json

        text = self._get(path, ttl, force_refresh=force_refresh)
        try:
            return json.loads(text)
        except ValueError:
            raise UpstreamError(f"{path} did not return JSON (got {text[:80]!r})")

    # -- reference data ----------------------------------------------------

    def symbols(self, include_debt=False, etf_only=False, force_refresh=False):
        """The tradable universe: symbol, name, sectorName, isETF, isDebt."""
        rows = self._get_json("/symbols", ttl=24 * 3600, force_refresh=force_refresh)
        if etf_only:
            return [r for r in rows if r.get("isETF")]
        if not include_debt:
            return [r for r in rows if not r.get("isDebt")]
        return rows

    def search(self, needle, **kw):
        needle = needle.upper()
        return [
            r for r in self.symbols(**kw)
            if needle in r["symbol"].upper() or needle in (r.get("name") or "").upper()
        ]

    # -- live market -------------------------------------------------------

    def market_watch(self, force_refresh=False):
        """Every symbol's OHLC/volume/order book in ONE request.

        Always prefer this over looping `quote()` -- it is the same single
        call whether you want 1 symbol or 500.
        """
        html = self._get("/market-watch", ttl=self._live_ttl(60),
                         force_refresh=force_refresh)
        rows = table_records(html)
        if not rows:
            raise NoData("market watch returned no rows")
        return rows

    def quote(self, symbol, **kw):
        """One symbol from the market watch (served from the cached table).

        The market watch carries only what is actually trading -- roughly 500
        of the ~1,030 rows in `symbols()`. A listed-but-absent symbol is a
        different problem from a misspelt one, so they raise different
        errors: NoData for the former, UnknownSymbol for the latter.
        """
        wanted = symbol.upper()
        for row in self.market_watch(**kw):
            if row.get("symbol", "").upper() == wanted:
                return row
        known = {r["symbol"].upper() for r in self.symbols(include_debt=True)}
        if wanted in known:
            raise NoData(
                f"{wanted} is listed but absent from today's market watch "
                "(not traded today, suspended, or on a board the watch omits)"
            )
        raise UnknownSymbol(
            f"{wanted} is not a PSX symbol. Check `Client().search({wanted!r})` "
            "-- symbols change on mergers (ENGRO -> ENGROH, for example)."
        )

    def quotes(self, symbols, **kw):
        """Several symbols, still one upstream request."""
        wanted = {s.upper() for s in symbols}
        return [
            r for r in self.market_watch(**kw)
            if r.get("symbol", "").upper() in wanted
        ]

    # -- time series -------------------------------------------------------

    def _timeseries(self, kind, symbol, force_refresh=False, ttl=None):
        sym = symbol.upper()
        path = f"/timeseries/{kind}/{sym}"
        blob = self._get_json(
            path,
            ttl if ttl is not None else self._live_ttl(60),
            force_refresh=force_refresh,
        )
        if blob.get("status") != 1:
            raise UnknownSymbol(blob.get("message") or f"{sym}: no data")
        data = blob.get("data") or []
        if not data:
            # The eod route answers status=1 with an empty list for a symbol
            # it does not know, so emptiness is the only signal available.
            raise UnknownSymbol(
                f"{sym}: no {kind} data (unknown symbol, or never traded)"
            )
        return data

    def intraday(self, symbol, **kw):
        """Today's ticks, oldest first: [{time, price, volume}]."""
        rows = [
            {"time": _epoch(d[0]), "price": d[1], "volume": d[2]}
            for d in self._timeseries("int", symbol, **kw)
        ]
        rows.reverse()
        return rows

    def eod(self, symbol, since=None, **kw):
        """~5 years of daily bars, oldest first: [{date, open, close, volume}].

        EOD only changes once a day, so it is cached for 6h while trading and
        until the next open otherwise.
        """
        kw.setdefault("ttl", self._live_ttl(6 * 3600))
        rows = [
            {
                "date": _epoch(d[0]).date().isoformat(),
                "open": d[3],
                "close": d[1],
                "volume": d[2],
            }
            for d in self._timeseries("eod", symbol, **kw)
        ]
        rows.reverse()
        if since:
            since = str(since)
            rows = [r for r in rows if r["date"] >= since]
        return rows

    # -- historical --------------------------------------------------------

    def history_by_date(self, date, force_refresh=False):
        """Every symbol on one trading day. Past days are immutable -> 24h TTL."""
        date = str(date)
        today = market.now_pkt().date().isoformat()
        ttl = self._live_ttl(300) if date >= today else 24 * 3600
        rows = table_records(
            self._get("/historical", ttl, body={"date": date},
                      force_refresh=force_refresh)
        )
        if not rows:
            raise NoData(f"{date}: no rows (weekend, public holiday, or too old)")
        return rows

    def history_by_month(self, symbol, month, year, force_refresh=False):
        """One symbol's daily bars for a month. Closed months cache for a day."""
        now = market.now_pkt()
        current = (int(year), int(month)) >= (now.year, now.month)
        ttl = self._live_ttl(3600) if current else 24 * 3600
        rows = table_records(
            self._get(
                "/historical",
                ttl,
                body={"month": str(int(month)), "year": str(int(year)),
                      "symbol": symbol.upper()},
                force_refresh=force_refresh,
            )
        )
        if not rows:
            raise NoData(f"{symbol} {year}-{month:02d}: no rows")
        return rows

    # -- breadth, indices, sectors ----------------------------------------

    def top_symbols(self, **kw):
        return self._get_json("/data/top-10-symbols", self._live_ttl(60), **kw)

    def top_sectors(self, **kw):
        return self._get_json("/data/top-10-sectors", self._live_ttl(60), **kw)

    def breadth(self, **kw):
        """Advancers/decliners/unchanged as fractions of the market."""
        rows = self._get_json("/data/symbol-position", self._live_ttl(60), **kw)
        return {r["name"]: r["value"] for r in rows}

    def indices(self, **kw):
        return table_records(self._get("/indices", self._live_ttl(60), **kw))

    def index_constituents(self, code, **kw):
        html = self._get(f"/indices/{code.upper()}", self._live_ttl(60), **kw)
        rows = table_records(html)
        if not rows:
            raise NoData(f"{code}: no constituents (unknown index code?)")
        return rows

    def sector_summary(self, **kw):
        return table_records(
            self._get("/sector-summary/sectorwise", self._live_ttl(60), **kw)
        )

    # -- announcements -----------------------------------------------------

    def announcements(
        self,
        kind="companies",
        symbol=None,
        query=None,
        date_from=None,
        date_to=None,
        count=25,
        offset=0,
        force_refresh=False,
    ):
        """Corporate/regulator announcements, each with its PDF URL."""
        if kind not in ANNOUNCEMENT_TYPES:
            raise ValueError(
                f"kind must be one of {sorted(ANNOUNCEMENT_TYPES)}, not {kind!r}"
            )
        body = {
            "type": ANNOUNCEMENT_TYPES[kind],
            "symbol": (symbol or "").upper(),
            "query": query or "",
            "count": str(count),
            "offset": str(offset),
            "date_from": str(date_from or ""),
            "date_to": str(date_to or ""),
            "page": "annc",
        }
        html = self._get("/announcements", self._live_ttl(300), body=body,
                         force_refresh=force_refresh)
        rows = table_records(html, link_field="url", host=HOST)
        if not rows:
            raise NoData("no announcements matched")
        return drop_widget_columns(rows)

    # -- housekeeping ------------------------------------------------------

    def health(self):
        """What the client is doing: node, session, cache and request counts."""
        return {
            "node": self.transport._node or self.transport.pinned_node(),
            "dns": self.transport.dns(),
            "candidates": self.transport.candidates(),
            "session": market.session_state(),
            "pkt_now": market.now_pkt().isoformat(timespec="seconds"),
            "upstream_requests": self.transport.requests_made,
            "cache": self.cache.stats(),
        }

    def close(self):
        self.transport.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _epoch(seconds):
    """PSX stamps are PKT wall-clock seconds.

    Read them as UTC and label them PKT: that reproduces the wall clock PSX
    meant, regardless of the host's own timezone.
    """
    return _dt.datetime.fromtimestamp(seconds, _dt.timezone.utc).replace(
        tzinfo=market.PKT
    )


__all__ = ["Client", "ANNOUNCEMENT_TYPES", "INDEX_CODES", "to_number"]
