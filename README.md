# psx-dps

[![tests](https://github.com/umairsandhu/psx-dps/actions/workflows/tests.yml/badge.svg)](https://github.com/umairsandhu/psx-dps/actions/workflows/tests.yml)

Unofficial Python client and CLI for the **Pakistan Stock Exchange** data
portal ([dps.psx.com.pk](https://dps.psx.com.pk)).

No API key. No account. No dependencies — Python 3.8+ and the standard
library. Built to be imported by several projects at once without any of
them having to think about being polite to PSX.

```python
from psx_dps import Client

with Client() as psx:
    print(psx.quote("MARI")["close"])          # 630.3
    bars = psx.eod("LUCK", since="2026-01-01") # ~5y of daily bars, 1 request
    kse  = psx.index_constituents("KSE100")    # all 100, with weights
```

```bash
psx-dps quote MARI OGDC
psx-dps market --index KSE100 --sort change --limit 10
psx-dps eod LUCK --since 2026-01-01 --csv > luck.csv
```

## Install

```bash
pip install git+https://github.com/umairsandhu/psx-dps
```

Imports as `psx_dps`, not `psx` — the top-level `psx` name is already taken
on PyPI by the unrelated `psx-data-reader`, and colliding with it would
break anyone using both.

## Why this exists

PSX has no public API, but the data portal's front end talks to undocumented
JSON endpoints. Reading them is easy. Reading them *reliably* is not, for one
non-obvious reason:

> `dps.psx.com.pk` is served by several nodes, and some of them return **404
> for every data route** while serving the ordinary HTML pages with 200. The
> zone publishes a single rotating A record, so DNS will cheerfully hand you
> one of the broken ones.

Measured live, with every public resolver agreeing on the bad node:

```
52.128.23.6    /data/symbol-position -> 404    /company/MARI -> 200
52.128.23.16   /data/symbol-position -> 200    /company/MARI -> 200
```

So `requests.get("https://dps.psx.com.pk/market-watch")` is a coin flip, and
when it loses it looks exactly like "PSX removed the API". It didn't. This is
most likely why several published PSX scrapers are reported as broken.

`psx-dps` keeps a pool of candidate nodes, probes for one that genuinely
serves data, pins it, and re-probes when a pinned node starts 404ing — while
still validating TLS against the real hostname. The whole investigation,
including the dead ends, is written up in [docs/DISCOVERY.md](docs/DISCOVERY.md).

## Being a good citizen

This reads a free public service that PSX runs for investors, not for us.
Since the point of this package is that several of your projects call it on
the fly, the defaults do the work:

- **Shared cache** at `~/.cache/psx-dps` — ten projects asking for the market
  watch in the same minute cost PSX one request, not ten.
- **Market-aware TTLs** — 60s while Karachi trades; once the bell goes,
  cached until the next open, because nothing can change. No overnight or
  weekend polling.
- **Machine-wide throttle** of 1 req/s, enforced across *processes* via a
  lock file, so three projects share one budget instead of getting one each.
- **24h budget fuse** (5,000 requests) so a runaway loop fails locally
  instead of hammering PSX all day.
- **Keep-alive**, **gzip** and **jittered exponential backoff**.
- **Stands down when PSX pushes back** — a 429 or 503 stops immediately with
  no retries and starts a shared, escalating cooldown (60s → 1h) across every
  process on the machine, honouring `Retry-After`. Cached data keeps serving,
  so you slow down without going blind.

**Polling on a schedule?** A full market-wide sweep is 6 requests, so
5-minute polling costs ~432 requests/day — 9% of the built-in daily fuse —
and off-hours polling is free because TTLs stretch to the next open. See
[Polling on a schedule](docs/FAIR-USE.md#polling-on-a-schedule-trackers-dashboards)
for the numbers and the one pattern that would turn it abusive.

Two documents worth reading before you wire this into anything scheduled:

- **[docs/FAIR-USE.md](docs/FAIR-USE.md)** — polling budgets and the rules
  that keep the footprint small. The two that matter most: share the cache
  directory, and fetch the market watch once instead of looping over symbols.
- **[docs/STAYING-UNBLOCKED.md](docs/STAYING-UNBLOCKED.md)** — what happens
  when PSX pushes back, what `CoolingDown` means, and the runbook for it.
- **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** — how PSX has broken
  before, how each break was diagnosed, and what to do next time.
- **[docs/SYMBOLS.md](docs/SYMBOLS.md)** — what a PSX ticker tells you: the
  1,029-vs-496 trap, debt maturity dates encoded in symbols, rights issues,
  sector codes, index membership, and tickers that rename silently.

Check your own footprint any time:

```python
psx.health()   # {'upstream_requests': 2, 'cache': {'hit_rate': 0.95, ...}, ...}
```

## Library API

```python
from psx_dps import Client
psx = Client()
```

| Method | Returns |
|---|---|
| `symbols(include_debt=False, etf_only=False)` | the universe (~1,030 rows) |
| `search("engro")` | symbol/name substring match |
| `market_watch()` | every trading symbol in **one** request (~496) |
| `snapshot()` | timestamped numeric snapshot of the whole market, for pollers |
| `quote("MARI")` / `quotes([...])` | one/many rows from the cached watch |
| `intraday("MARI")` | today's ticks, oldest first |
| `eod("MARI", since="2026-01-01")` | ~5y daily bars, oldest first |
| `history_by_date("2026-09-25")` | every symbol that trading day |
| `history_by_month("MARI", 9, 2026)` | one symbol's month, with high/low |
| `announcements(kind="companies", symbol=...)` | announcements + PDF URLs |
| `top_symbols()` / `top_sectors()` / `breadth()` | volume leaders, advance/decline |
| `indices()` / `index_constituents("KSE100")` | index levels / members |
| `sector_summary()` | per-sector advance/decline/turnover |
| `health()` | node, session, cache and request counts |

Useful knobs:

```python
Client(
    min_interval=2.0,          # slow down a background job
    daily_budget=500,          # tighter runaway fuse
    cache_dir="/srv/psx-cache" # share across containers
    user_agent="my-bot/1.0 (+https://example.com/contact)",
)
```

Errors all derive from `PSXError`:

| Exception | Means |
|---|---|
| `UnknownSymbol` | not a PSX symbol |
| `NoData` | valid request, nothing to return (non-trading day, untraded symbol) |
| `CoolingDown` | we are deliberately not calling PSX right now — see below |
| `NoHealthyNode` | no node served the data routes, or you are offline |
| `RateLimited` | your own 24h budget fuse is spent |
| `TransportError` / `UpstreamError` | network failure / unexpected HTTP status |

`CoolingDown` is the one to handle explicitly. PSX asked us to slow down, so
the library stopped — like a trip switch in a fuse box, the circuit is dead
on purpose. Skip the cycle rather than retrying around it:

```python
from psx_dps import Client, CoolingDown

try:
    store(psx.snapshot())
except CoolingDown as exc:
    log.warning("PSX asked us to back off: %s", exc)
    return                      # no retry - retrying is what gets you blocked
```

It is shared across processes, escalates 60s → 5m → 15m → 1h, forgives after
a clean stretch, and keeps serving cached data throughout.
[Full explanation and runbook](docs/STAYING-UNBLOCKED.md).

`NoData` and `UnknownSymbol` are deliberately different — a symbol can be
listed but not trading today, which is not a typo:

```python
psx.quote("ENGRO")    # NoData: listed but absent from today's market watch
psx.quote("ENGROH")   # fine - ENGRO became ENGROH after the 2024 merger
psx.quote("NOTREAL")  # UnknownSymbol
```

## CLI

| Command | Does |
|---|---|
| `symbols [--grep] [--sector] [--all\|--debt-only\|--etf-only]` | the universe |
| `quote SYM...` | current quotes |
| `market [--index] [--sector] [--sort] [--limit]` | whole market watch |
| `intraday SYM [--limit]` | today's ticks |
| `eod SYM [--since] [--limit]` | daily bars |
| `history [SYM --month M --year Y] \| [--date D]` | official historical table |
| `announcements [--type] [--symbol] [--query] [--count]` | announcements |
| `movers` · `indices` · `index CODE` · `sectors` | breadth, indices, sectors |
| `diagnose [--deep] [--rescan]` | find what broke, fix what it can, exit 1 on failure |
| `nodes [--sample N]` | which addresses work and when that last changed |
| `doctor` | probe nodes, show session/cache/budget state |
| `cache [--clear]` | inspect or clear the shared cache |

Global flags: `--json`, `--csv`, `--no-cache`, `--cache-dir`,
`--min-interval`. Exit status is 1 on no rows or unknown symbol, so it
composes in scripts.

**Something broken?** `psx-dps diagnose --deep` works out *which* thing
broke — local network, node selection, an active cooldown, or PSX changing a
payload — fixes what it can, and names what it cannot. Exit status is
non-zero on failure, so it drops into a health check:

```
[  ok  ] node health     serving data: 52.128.23.16 | serving pages but NOT data: 52.128.23.6
[  ok  ] tls             valid certificate for psx.com.pk
[  ok  ] cooldown        clear (0 recent strike(s))
[  ok  ] GET  /symbols               1029 instruments
[  ok  ] GET  /market-watch          496 trading symbols
[  ok  ] GET  /timeseries/eod/MARI   1239 daily bars
```

The endpoint checks validate the **shape** of each response, not just the
status code — a route returning 200 with a redesigned body is the failure
that silently corrupts a database for a week before anyone notices.
[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) has the full runbook,
including how to re-derive the endpoints from scratch if PSX moves them.

`psx-dps doctor` is a quicker look at node and session state:

```
DNS answer   52.128.23.6
pinned       52.128.23.16
session      closed (Tue 2026-09-29 16:11 PKT)
budget used  2 requests in the last 24h

probing /data/symbol-position on each candidate:
  52.128.23.6      dns,remembered,seed   NO data routes (404)
  52.128.23.16     remembered,seed       OK - serves data routes

note: DNS is advertising 52.128.23.6, which does NOT serve the data
      routes. This is the failure mode psx-dps exists to absorb.
```

`PSX_NODE=<ip>` forces a node; `PSX_DPS_CACHE_DIR` moves the cache.

## Gotchas worth knowing

- **EOD rows are `[epoch, close, volume, open]`** — not OHLC. No high/low on
  that route; use `history_by_month()` for those.
- **Timestamps are PKT wall clock.** This library reads them as UTC+5 so a
  laptop in any timezone gets the same dates.
- **`/symbols` (1,029) ≠ `/market-watch` (496).** The watch is only what's
  trading; 534 listed instruments are legitimately absent. See
  [docs/SYMBOLS.md](docs/SYMBOLS.md).
- **A `403` is not a block.** There is no WAF. It means you're on a node
  that doesn't serve data routes — the same condition that gives `404`
  without an `X-Requested-With` header. Never respond by rotating
  User-Agents or proxies.
- **Friday has a split session** for Jummah; the TTL calendar knows.
- **Public holidays are not modelled** — PSX's list changes yearly and a
  stale table is worse than none. On a holiday the client just uses short
  TTLs against unchanging data. Pass your own `is_open` callable if you care.

## Endpoints

Full reference, including the routes this client doesn't wrap:
[docs/ENDPOINTS.md](docs/ENDPOINTS.md).

## Already have a PSX integration?

[docs/EVALUATION-PROMPT.md](docs/EVALUATION-PROMPT.md) is a ready-made brief
for a coding agent: it audits your existing client against eleven documented
PSX failure modes, verifies this library's claims rather than trusting them,
and is explicitly told to report where `psx-dps` is the worse choice.

## Tests

```bash
pip install pytest && python -m pytest
```

54 tests, all offline against saved fixtures — the suite never touches PSX.
They cover the node-recovery logic with a fake network, the cross-process
rate limit with real subprocesses, and the timezone independence of both the
trading calendar and timestamp parsing.

## Status and disclaimer

Unofficial and unaffiliated. Not endorsed by the Pakistan Stock Exchange.
The endpoints are undocumented and can change without notice, so don't put
this on a critical path without a fallback.

Data is PSX's. Reading it for your own analysis is one thing; redistributing
or reselling a live feed is a conversation to have with PSX first.

Nothing here is investment advice, and there's no warranty of accuracy or
timeliness. MIT licensed.
