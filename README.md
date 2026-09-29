<h1 align="center">psx-dps</h1>

<p align="center">
  <strong>Pakistan Stock Exchange market data in Python — no API key, no dependencies, no scraping headaches.</strong>
</p>

<p align="center">
  Live quotes, KSE-100 constituents, 5 years of end-of-day history, intraday ticks,<br>
  corporate announcements and sector data from the official PSX data portal.
</p>

<p align="center">
  <a href="https://github.com/umairsandhu/psx-dps/actions/workflows/tests.yml"><img src="https://github.com/umairsandhu/psx-dps/actions/workflows/tests.yml/badge.svg" alt="tests"></a>
  <img src="https://img.shields.io/badge/python-3.8%2B-blue" alt="Python 3.8+">
  <img src="https://img.shields.io/badge/dependencies-none-brightgreen" alt="Zero dependencies">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT licence">
  <img src="https://img.shields.io/badge/API%20key-not%20required-blueviolet" alt="No API key required">
</p>

---

```bash
pip install git+https://github.com/umairsandhu/psx-dps
```

```python
from psx_dps import Client

with Client() as psx:
    psx.quote("MARI")                      # live quote: OHLC, volume, order book
    psx.eod("LUCK", since="2026-01-01")    # daily bars — 5 years in one request
    psx.index_constituents("KSE100")       # all 100, with index weights
    psx.snapshot()                         # the whole market, one request
```

```bash
psx-dps quote MARI OGDC HBL
psx-dps market --index KSE100 --sort change --limit 10
psx-dps eod LUCK --since 2026-01-01 --csv > luck.csv
```

That's the whole setup. No account, no token, no `requests`, no `pandas`.
Full method reference: **[docs/API.md](docs/API.md)**.

---

## Why another PSX library?

Because the hard part isn't calling the endpoints — it's that **calling them
naively fails most of the time, in a way that looks permanent.**

`dps.psx.com.pk` is served by several nodes, and some of them return
**HTTP 404 for every data route** while serving the ordinary website with
200. The zone publishes a *single rotating A record*, so DNS will hand you a
broken node and you'll keep landing on it for the TTL.

```
52.128.23.6    /data/symbol-position → 404      /company/MARI → 200
52.128.23.16   /data/symbol-position → 200      /company/MARI → 200
```

Measured live, with `dig`, `8.8.8.8` and `1.1.1.1` all returning only the
broken address. A plain `requests.get("https://dps.psx.com.pk/market-watch")`
is a coin flip, and when it loses it looks exactly like *"PSX removed the
API"*.

It didn't. **`psx-dps` keeps a pool of candidate nodes, probes for one that
genuinely serves data, pins it, and re-probes instead of believing a 404** —
while still validating TLS against the real hostname.

The full investigation, including the dead ends, is in
[DISCOVERY.md](docs/DISCOVERY.md).

## What you get

| | |
|---|---|
| **Live quotes** | OHLC, LDCP, volume and full order book for ~496 trading symbols |
| **Whole market in 1 request** | `market_watch()` / `snapshot()` — never loop over symbols |
| **End-of-day history** | ~5 years of daily bars per symbol, one request |
| **Intraday ticks** | Today's full tick series |
| **Indices** | KSE-100, KSE-30, KMI-30, ALLSHR and more — levels *and* constituents with weights |
| **Sector data** | Advance/decline, turnover and market cap for all 38 sectors |
| **Announcements** | 224k+ corporate and regulator filings, each with its PDF URL |
| **Historical tables** | Every symbol on any trading day, or one symbol's month with high/low |
| **Market breadth** | Advancers, decliners, unchanged |

## Built to be left running

Most of this library is the boring part that scrapers skip — because it's
reading a free public service that PSX runs for investors, not an API we pay
for.

| | |
|---|---|
| 🔁 **Node failover** | Survives the 404 problem above, automatically |
| 💾 **Shared cache** | Ten projects on one machine cost PSX *one* request |
| 🕰️ **Market-aware TTLs** | 60s while trading; cached until the next open once shut — **off-hours polling is free** |
| 🚦 **Cross-process rate limit** | 1 req/s enforced machine-wide via a lock file, not per-process |
| 🛑 **Backs off when pushed** | A 429 stops instantly and starts a shared, escalating cooldown. No retry storms. |
| 🔌 **Zero dependencies** | Python 3.8+ and the standard library |
| 🩺 **Self-diagnosis** | `psx-dps diagnose --deep` names what broke and fixes what it can |
| ✅ **80 offline tests** | The suite never touches PSX |

**5-minute polling of the entire market costs ~432 requests/day** — about
one request every 50 seconds during the session, less than a single person
with the portal open on auto-refresh. [The arithmetic](docs/FAIR-USE.md#polling-on-a-schedule-trackers-dashboards).

## Command line

```bash
psx-dps quote MARI OGDC          # live quotes
psx-dps market --index KSE100    # the whole board, filtered
psx-dps eod LUCK --since 2026-01-01 --csv
psx-dps announcements --symbol ENGROH
psx-dps movers                   # volume leaders + breadth
psx-dps diagnose --deep          # what's broken, and fix it
psx-dps nodes                    # which PSX address works, and since when
```

Every command takes `--json` or `--csv`, so it pipes into anything.

## Documentation

| Guide | What's in it |
|---|---|
| [API.md](docs/API.md) | Full Python reference — every method, option and exception |
| [ENDPOINTS.md](docs/ENDPOINTS.md) | Every PSX endpoint, payload shapes and quirks |
| [SYMBOLS.md](docs/SYMBOLS.md) | What a ticker tells you — maturity dates, rights issues, silent renames |
| [DISCOVERY.md](docs/DISCOVERY.md) | How the endpoints were reverse-engineered, dead ends included |
| [FAIR-USE.md](docs/FAIR-USE.md) | Polling budgets and keeping the footprint small |
| [STAYING-UNBLOCKED.md](docs/STAYING-UNBLOCKED.md) | What happens when PSX pushes back |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | How PSX has broken before, and the runbook |
| [EVALUATION-PROMPT.md](docs/EVALUATION-PROMPT.md) | Already have a PSX integration? Audit it against this one |

## FAQ

<details>
<summary><strong>Does the Pakistan Stock Exchange have an official API?</strong></summary>

No. PSX publishes no public API and no documentation for one. The data
portal at `dps.psx.com.pk` has undocumented JSON endpoints that its own
front end uses, which is what this library reads. They can change without
notice — see [ENDPOINTS.md](docs/ENDPOINTS.md).
</details>

<details>
<summary><strong>Why do I get 404 from dps.psx.com.pk when the website works?</strong></summary>

You're almost certainly on a node that doesn't serve the data routes. It's
not you and the API wasn't removed — see [Why another PSX library?](#why-another-psx-library)
above. `psx-dps diagnose` confirms it in seconds.
</details>

<details>
<summary><strong>Why did I get a 403? Am I blocked?</strong></summary>

No. There's **no WAF** on this service. A 403 on a data route means you're
on a node that doesn't serve them — the same condition that gives 404
without an `X-Requested-With` header. Don't respond by rotating
User-Agents or proxies; that's the one thing likely to get you *genuinely*
blocked. [Details](docs/TROUBLESHOOTING.md#it-is-not-a-waf-and-you-are-not-blocked).
</details>

<details>
<summary><strong>Do I need an API key or account?</strong></summary>

No. Every endpoint this library uses is unauthenticated.
</details>

<details>
<summary><strong>How do I get KSE-100 index data?</strong></summary>

```python
psx.indices()                        # levels for KSE100, KSE30, KMI30, ALLSHR…
psx.index_constituents("KSE100")     # all 100 members with index weights
```
</details>

<details>
<summary><strong>How far back does historical data go?</strong></summary>

About five years of daily bars per symbol via `eod()`, in a single request.
`history_by_date()` gives every symbol on a given trading day, and
`history_by_month()` adds high/low.
</details>

<details>
<summary><strong>Can I use this for a real-time tracker or dashboard?</strong></summary>

Yes — it's designed for it. Poll `snapshot()` on a schedule and store the
result; that's one request for all ~496 symbols and gives you your own
intraday series. Read [FAIR-USE.md](docs/FAIR-USE.md) first for the
budgets and the one pattern that would turn it abusive.
</details>

<details>
<summary><strong>Is scraping PSX data legal?</strong></summary>

The data is published free on a public website with no login, no paywall
and no click-through terms. `dps.psx.com.pk` serves no `robots.txt`;
`www.psx.com.pk`'s is permissive with no crawl delay. So nothing is being
circumvented. That is **not** a licence to redistribute — market data is
PSX's to license, and reselling or republishing a live feed is a
conversation to have with them. Not legal advice.
</details>

<details>
<summary><strong>Why does it import as <code>psx_dps</code> and not <code>psx</code>?</strong></summary>

The top-level `psx` name already belongs to the unrelated `psx-data-reader`
package on PyPI. Colliding with it would break anyone using both.
</details>

<details>
<summary><strong>A symbol disappeared. Where did it go?</strong></summary>

Either it isn't trading today (the market watch carries ~496 of ~1,029
listed instruments), or a corporate action renamed it — `ENGRO` became
`ENGROH` in 2024, silently. `psx.search("engro")` finds the successor.
[SYMBOLS.md](docs/SYMBOLS.md) covers both.
</details>

## Contributing

Issues and PRs welcome — particularly new PSX node addresses if you find one
(`SEED_NODES` in `src/psx_dps/transport.py`), and parser fixes when PSX
changes a payload.

```bash
git clone https://github.com/umairsandhu/psx-dps && cd psx-dps
pip install -e . pytest && python -m pytest     # 80 tests, all offline
```

## Disclaimer

Unofficial and unaffiliated. Not endorsed by the Pakistan Stock Exchange.
The endpoints are undocumented and can change without notice — don't put
this on a critical path without a fallback. Nothing here is investment
advice, and there's no warranty of accuracy or timeliness. MIT licensed.

---

<p align="center">
  <sub>
  Keywords: Pakistan Stock Exchange API · PSX API Python · KSE-100 data ·
  Karachi Stock Exchange · PSX market data · dps.psx.com.pk ·
  Pakistan stock market data · PSX historical data · KSE-100 constituents ·
  PSX scraper · Pakistani equities
  </sub>
</p>
