# Python API reference

```python
from psx_dps import Client

with Client() as psx:
    ...
```

The client is reusable and keeps a connection alive, so make one and hold
it rather than constructing one per call. It is safe to use from multiple
threads.

---

## Reference data

### `symbols(include_debt=False, etf_only=False, force_refresh=False)`
The tradable universe: `[{symbol, name, sectorName, isETF, isDebt}]`.
~1,029 instruments; debt is excluded by default. Cached 24h.

### `search(needle, **kw)`
Substring match on symbol **or** company name. The way to find a ticker
after a corporate action renamed it.

---

## Live market

### `market_watch(force_refresh=False)`
**Every trading symbol in one request** (~496 rows) — `symbol, sector,
listed, ldcp, open, high, low, close, change, percentChange, volume,
obq, obc, osq, osc`. Always prefer this to looping `quote()`.

### `snapshot(at=None)`
The whole market, timestamped and numeric, built for pollers:

```python
{"captured_at": "2026-09-29T16:28:12+05:00",
 "session": "open" | "closed" | "weekend",
 "rows": [{..., "close": 630.3, "volume": 337455.0,
           "listed": ["KSE100", "KMI30", ...]}]}
```

Store one every few minutes and you have your own intraday series —
per-interval volume comes from differencing the cumulative `volume`. One
request, regardless of how many symbols you care about.

### `quote(symbol)` / `quotes([symbols])`
One or many rows, served from the cached market watch. Still one upstream
request.

---

## Time series

### `eod(symbol, since=None)`
~5 years of daily bars, **oldest first**: `[{date, open, close, volume}]`.
One request. No high/low on this route — use `history_by_month()`.

### `intraday(symbol)`
Today's ticks, oldest first: `[{time, price, volume}]`. `time` is a
timezone-aware datetime in PKT.

---

## Historical

### `history_by_date(date)`
Every symbol on one trading day. Past days are immutable, so cached 24h.
Raises `NoData` on a weekend or public holiday.

### `history_by_month(symbol, month, year)`
One symbol's daily bars for a month — **includes high and low**, which
`eod()` does not.

---

## Indices, sectors, breadth

| Method | Returns |
|---|---|
| `indices()` | Levels for every index: high, low, current, change, % change |
| `index_constituents(code)` | Members **with index weights and free float**. `KSE100`, `KSE30`, `KMI30`, `ALLSHR`, `KMIALLSHR`, `KSE100PR` |
| `sector_summary()` | All 38 sectors: advance/decline/unchanged, turnover, market cap |
| `top_symbols()` / `top_sectors()` | Volume leaders |
| `breadth()` | `{"ADV": 0.30, "DEC": 0.63, ...}` as fractions |

Index membership is already in `market_watch()`'s `listed` column, so
filtering by index costs nothing extra:

```python
kse100 = [r for r in psx.market_watch() if "KSE100" in r["listed"].split(",")]
```

---

## Announcements

### `announcements(kind="companies", symbol=None, query=None, date_from=None, date_to=None, count=25, offset=0)`

224k+ filings, each with the URL of its PDF.

`kind` is one of `companies`, `psx`, `secp`, `cdc`, `nccpl`.

```python
psx.announcements(symbol="ENGROH", count=5)
psx.announcements(kind="psx", query="trading halt")
```

---

## Configuration

```python
Client(
    cache_dir=None,        # shared cache; leave alone so projects share it
    min_interval=1.0,      # seconds between upstream requests, machine-wide
    daily_budget=5000,     # rolling 24h fuse; raises RateLimited locally
    cache=True,            # False bypasses the cache (please not in a loop)
    timeout=30.0,
    retries=3,
    fresh_ttl=None,        # override the open-market TTL, seconds
    stale_ttl=None,        # override the closed-market TTL
    user_agent=None,       # name your project and stay contactable
    is_open=None,          # supply your own trading-calendar callable
    respect_cooldown=True, # do not disable this
)
```

Every read method also takes `force_refresh=True` to bypass the cache for
one call. Do not put that in a scheduled loop.

### `health()`

What the client is doing and what it has cost PSX:

```python
{"node": "52.128.23.16", "session": "closed",
 "upstream_requests": 6, "bytes_downloaded": 1021948,
 "budget_used_24h": 30,
 "cooldown": {"cooling_down": False, "strikes": 0},
 "cache": {"hits": 41, "misses": 6, "hit_rate": 0.87}}
```

---

## Exceptions

All derive from `PSXError`, so one `except` catches everything.

| Exception | Means | Do |
|---|---|---|
| `UnknownSymbol` | Not a PSX symbol | Check `search()` — it may have been renamed |
| `NoData` | Valid request, nothing to return | Usually a non-trading day or an untraded symbol. Skip it. |
| `CoolingDown` | We are deliberately not calling PSX | **Skip this cycle.** Never retry around it |
| `NoHealthyNode` | No node served data, or you are offline | `psx-dps diagnose` |
| `RateLimited` | Your own 24h budget fuse is spent | Find the loop before raising it |
| `TransportError` | Network failure after retries | Transient; the next cycle will likely work |
| `UpstreamError` | Unexpected HTTP status or unusable body | Check [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |

`NoData` and `UnknownSymbol` are deliberately different — a symbol can be
listed but not trading today, which is not a typo:

```python
psx.quote("ENGRO")     # NoData        — listed, absent from today's watch
psx.quote("ENGROH")    # fine          — ENGRO became ENGROH in 2024
psx.quote("NOTREAL")   # UnknownSymbol — not a PSX symbol at all
```

`CoolingDown` is the one to handle explicitly:

```python
from psx_dps import Client, CoolingDown

try:
    store(psx.snapshot())
except CoolingDown as exc:
    log.warning("PSX asked us to back off: %s", exc)
    return                  # no retry — retrying is what gets you blocked
```

See [STAYING-UNBLOCKED.md](STAYING-UNBLOCKED.md).

---

## Diagnostics

```python
from psx_dps import diagnose

checks, fixed = diagnose.run(deep=True)
for c in checks:
    print(c.status, c.name, c.detail, c.fix)
```

`deep=True` validates every endpoint's payload **shape**, which is what
catches PSX changing a response format — the failure that otherwise writes
garbage into your database silently. Costs ~8 requests; don't put it in the
poll loop.

---

## Gotchas

- **EOD rows are `[epoch, close, volume, open]`** — not OHLC. The library
  handles it; raw callers routinely get this wrong.
- **Timestamps are PKT wall clock.** Parsed as UTC+5 regardless of the
  host's timezone, so a server in UTC gets the same dates.
- **`/symbols` (1,029) ≠ `/market-watch` (496).** See [SYMBOLS.md](SYMBOLS.md).
- **Friday has a split session** for Jummah; the TTL calendar knows.
- **Public holidays are not modelled** — PSX's list changes yearly and a
  stale table would be worse than none. On a holiday the client uses short
  TTLs against unchanging data: wasteful by a few requests, never wrong.
  Pass your own `is_open` callable if you need it exact.
