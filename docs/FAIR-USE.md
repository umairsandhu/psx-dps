# Fair use: staying light on PSX

`psx-dps` reads a public service that is not built to be an API and is not
paid for by us. PSX runs it for investors, not for our dashboards. Everything
here is about keeping the footprint small enough that nobody at PSX ever has
a reason to care that we exist.

This matters more than usual because the library is designed to be called by
several projects at once, on the fly. Five well-behaved projects can still
add up to badly-behaved traffic.

## What the defaults already do for you

You get these without configuring anything:

| Guard | Default | What it prevents |
|---|---|---|
| Shared on-disk cache | `~/.cache/psx-dps` | Every project on the machine re-fetching the same table |
| Market-aware TTLs | 60s trading / until next open | Overnight and weekend polling for data that cannot change |
| Cross-process throttle | 1 request/second, machine-wide | Several projects each "politely" doing 1/s |
| 24h budget fuse | 5,000 requests | A runaway loop quietly hammering PSX all day |
| Connection reuse | keep-alive | A TLS handshake per request |
| Retry backoff | exponential + jitter | Retry storms when PSX is already struggling |

The throttle state lives in a lock file, not in memory, so the 1 req/s limit
is **global to the machine** — three processes share the one budget rather
than getting one each. This is verified by a test.

## The rules that actually matter

**1. Share the cache directory.** The single highest-leverage thing. Leave
`cache_dir` alone so every project points at `~/.cache/psx-dps`. Ten projects
asking for the market watch inside a minute should cost PSX one request.

If your projects run in containers, mount one volume at `~/.cache/psx-dps`
across all of them, or set `PSX_DPS_CACHE_DIR` to a shared path.

**2. Ask for the market watch, not for symbols in a loop.** One request
carries all ~496 trading symbols. This is the difference between 1 request
and 500:

```python
# good - one upstream request, any number of symbols
rows = psx.quotes(["MARI", "OGDC", "LUCK", "HBL", "ENGROH"])

# bad - looks innocent, is not
for sym in my_watchlist:
    psx.quote(sym)          # cached, but still the wrong habit to teach
```

`quote()` and `quotes()` are both served from the one cached market-watch
table, so the loop above is not actually 500 requests — but write it the
first way anyway, because the day someone passes `force_refresh=True` the
difference becomes real.

**3. Never poll faster than the data changes.** PSX is not a tick feed. A
60-second interval during market hours is plenty for almost everything, and
outside 09:00–16:45 PKT Monday–Friday **nothing changes at all**. The default
TTLs already enforce this; you only break it by reaching past them.

**4. Do not set `force_refresh=True` in a loop.** It bypasses the cache and
is for one-off "I need this now" moments, not for schedulers.

**5. Do not disable the throttle to go faster.** If you need a lot of data,
you need *backfill*, not concurrency — see below.

**6. Backfill once, store it yourself.** For historical work, pull it once
into your own database and never ask again. Past trading days are immutable:

```python
# right: one pass, then it is yours forever
bars = psx.eod("MARI")                  # ~5 years in a single request
save_to_my_db(bars)
```

`eod()` returns about five years in **one** request. There is no reason to
walk `history_by_date()` day by day to build a series.

**7. Identify yourself.** The default User-Agent names the project and links
the repo, so anyone at PSX looking at logs can see what this is and contact
us. If you fork it, keep a contactable UA:

```python
Client(user_agent="my-research-bot/1.0 (+https://example.com/contact)")
```

Please do **not** disguise it as a browser. If PSX ever wants us to stop, we
want them to be able to tell us.

**8. Scale the throttle down, not up, when you add projects.** The knob
exists to be turned the polite way:

```python
Client(min_interval=2.0)    # a background job has no reason to rush
```

## Polling on a schedule (trackers, dashboards)

Measured, not estimated. A full market-wide sweep -- market watch, indices,
sector summary, breadth, top symbols, top sectors -- is **6 requests** and
covers all ~496 trading symbols:

| | |
|---|---|
| One full sweep | 6 requests, ~125 KiB on the wire (gzipped), ~6.5s |
| 5-minute polling, market hours | 72 polls/day -> **432 requests/day** |
| Per month (22 trading days) | ~9,500 requests |
| Against the 5,000/day fuse | 9% of it |

So **5-minute polling of the entire market is fine.** It is roughly one
request every 50 seconds during the session -- less than a single person
sitting on the portal with auto-refresh on.

Off-hours polling is free. Once Karachi closes, TTLs stretch to the next
open, so a plain `*/5 * * * *` cron running 24/7 makes **zero** upstream
requests overnight and at weekends. Verified: 12 consecutive off-hours polls,
0 requests, 100% cache hits. You do not need to gate the cron on market hours.

### The one thing that turns this abusive

Calling a **per-symbol** endpoint inside the poll loop:

| Pattern | Per poll | Per day |
|---|---|---|
| `market_watch()` once | 1 | 72 |
| `intraday()` for each of 496 symbols | 496 | **35,712** |

That is 7x over the default fuse and about 80x the traffic of doing it
properly. It will get you throttled, and it deserves to.

You almost never need it. `snapshot()` returns the whole market, numeric and
timestamped, in one request -- store one every five minutes and you have
built your own intraday series, including per-interval volume by differencing
the cumulative `volume` column:

```python
from psx_dps import Client

with Client() as psx:
    snap = psx.snapshot()          # 1 request, all ~496 symbols
    db.insert_many(snap["captured_at"], snap["rows"])
```

Reach for `intraday()` only for a handful of symbols a user is actually
looking at, never for the whole board on a timer.

### Sizing the budget

The 5,000/day fuse is machine-wide and shared with every other project on
the box. A 5-minute tracker uses ~432 of it. If the tracker is all you run,
the default is comfortable; if you add more consumers, raise it deliberately
rather than discovering it at 3am:

```python
Client(daily_budget=10_000)
```

## How not to get blocked

Blocks are almost never a response to volume — they are a response to how a
client behaves when the server is already in trouble. The library stands
down automatically when PSX pushes back, and raises `CoolingDown` rather
than retrying into it.

That has its own document, because it is the part most likely to be got
wrong: **[STAYING-UNBLOCKED.md](STAYING-UNBLOCKED.md)** — what a cooldown
is, what the library does for you, the six rules it cannot enforce, and a
runbook for when one trips.

## Legal and practical notes

- The data is published free on a public website with no login, no paywall
  and no click-through terms gating it. `dps.psx.com.pk` serves no
  `robots.txt`; `www.psx.com.pk`'s is permissive and sets no crawl delay.
  So there is no access restriction being circumvented here.
- That is not the same as a licence to redistribute. **Market data is PSX's
  to license.** Reading it for your own analysis is one thing; republishing
  a live feed, or reselling it, is a different conversation and one to have
  with PSX. If you plan to build a product on this, contact them.
- This project is unofficial and unaffiliated. Endpoints are undocumented
  and can change or vanish without notice — do not put this on a critical
  path without a fallback.
- Nothing here is investment advice, and the data comes with no warranty of
  accuracy or timeliness. Check against the official portal before trading.

## Knowing what you are actually costing them

Every client tracks it, so there is no excuse for not looking:

```python
with Client() as psx:
    ...
    print(psx.health())
    # {'upstream_requests': 2, 'cache': {'hits': 41, 'hit_rate': 0.95, ...}, ...}
```

```bash
psx-dps doctor     # includes "budget used: N requests in the last 24h"
psx-dps cache      # entries and size on disk
```

A healthy long-running integration sits at a high cache hit rate and a
request count in the dozens per day, not the thousands. If yours does not,
something is polling that should not be.
