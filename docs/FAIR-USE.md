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

## What to do if PSX pushes back

If you start seeing hangs, 429s or 503s, **stop and back off** — do not
retry harder. The library already backs off exponentially with jitter and
gives up rather than looping. During development a burst of a few dozen
rapid requests caused every connection to hang for a couple of minutes
before recovering on its own; that is the signal that you are going too fast.

If PSX ever blocks this client or asks for it to stop, that request should be
honoured, not routed around. Do not add proxy rotation, and do not spoof
browser fingerprints to evade a block — that turns a tolerated read into
something PSX is actively trying to prevent.

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
