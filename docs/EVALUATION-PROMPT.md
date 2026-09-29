# Evaluation prompt

Hand this to a coding agent working inside an existing PSX app. It produces
an evidence-based verdict on whether adopting `psx-dps` is worth it, plus a
gap analysis of the current integration — both the data it is *not*
collecting and the data it may be storing *wrong*.

It is written to be sceptical. The agent is told to verify claims rather
than trust them, and to report where this library is the worse choice.

Copy everything below the line.

---

You are auditing this project's Pakistan Stock Exchange (PSX) data layer
against an open-source alternative, and producing a gap analysis of what we
collect today. I want evidence, not a recommendation to adopt. Verify every
claim yourself — do not trust the other project's README.

## The alternative

Repository: **https://github.com/umairsandhu/psx-dps**
Docs site: **https://umairsandhu.github.io/psx-dps/**

Read these before testing anything (raw URLs, fetch them directly):

| Document | Covers | Raw URL |
|---|---|---|
| README | Overview, install, FAQ | `https://raw.githubusercontent.com/umairsandhu/psx-dps/main/README.md` |
| API.md | Every method, option and exception | `https://raw.githubusercontent.com/umairsandhu/psx-dps/main/docs/API.md` |
| ENDPOINTS.md | Every PSX endpoint and payload shape | `https://raw.githubusercontent.com/umairsandhu/psx-dps/main/docs/ENDPOINTS.md` |
| SYMBOLS.md | Ticker conventions, listed-vs-trading, renames | `https://raw.githubusercontent.com/umairsandhu/psx-dps/main/docs/SYMBOLS.md` |
| DISCOVERY.md | How the endpoints were found, incl. dead ends | `https://raw.githubusercontent.com/umairsandhu/psx-dps/main/docs/DISCOVERY.md` |
| FAIR-USE.md | Load budgets and polling arithmetic | `https://raw.githubusercontent.com/umairsandhu/psx-dps/main/docs/FAIR-USE.md` |
| STAYING-UNBLOCKED.md | Back-off behaviour and its runbook | `https://raw.githubusercontent.com/umairsandhu/psx-dps/main/docs/STAYING-UNBLOCKED.md` |
| TROUBLESHOOTING.md | How PSX breaks and how to diagnose it | `https://raw.githubusercontent.com/umairsandhu/psx-dps/main/docs/TROUBLESHOOTING.md` |

Install (Python 3.8+, no dependencies). It imports as `psx_dps`, **not**
`psx` — that name belongs to the unrelated `psx-data-reader` on PyPI:

```bash
pip install git+https://github.com/umairsandhu/psx-dps
psx-dps diagnose --deep
python -c "from psx_dps import Client; print(Client().quote('MARI'))"
```

---

## Step 1 — map what we do today

Find every place this project touches PSX. For each: the URL, HTTP method,
how the response is parsed, how often it runs, and which table or file the
result lands in. Produce that table before comparing anything.

Then map our **storage**: which fields do we persist, at what granularity,
and how far back does the history go?

## Step 2 — test our client against 11 documented PSX behaviours

Each is a real, documented behaviour. For every one, say whether *our* code
handles it and show evidence — a line of code, or a command and its output.
Answer "handled" / "not handled" / "n/a", never "probably".

**1. Rotating DNS onto a node that 404s the data routes.** Some
`dps.psx.com.pk` nodes return 404 for every JSON route (`/symbols`,
`/timeseries/*`, `/market-watch`, `/data/*`) while serving HTML pages with
200. A single rotating A record (TTL 300) means DNS hands you a broken one.

```bash
dig +short A dps.psx.com.pk
for ip in 52.128.23.16 52.128.23.6; do
  echo -n "$ip -> "
  curl -s -o /dev/null -w '%{http_code}\n' --resolve dps.psx.com.pk:443:$ip \
    https://dps.psx.com.pk/data/symbol-position
done
```

Does our client survive landing on the 404 node, or does it retry the same
hostname and keep hitting it for the TTL?

**2. gzip.** PSX compresses on request but not by default — market watch is
~476 KB plain, ~60 KB gzipped. Do we send `Accept-Encoding: gzip`?

**3. Timestamps.** PSX epochs are Pakistan wall-clock seconds (UTC+5, no
DST). Parsing them in host-local time shifts dates. What do we do, and what
happens when the server runs in UTC?

**4. EOD column order.** `/timeseries/eod/{SYM}` rows are
`[epoch, close, volume, open]` — *not* OHLC. Misreading silently corrupts
every series.

**5. `/symbols` ≠ `/market-watch`.** ~1,029 listed vs ~496 trading. Do we
treat a listed-but-untraded symbol as an error? Test with `ENGRO`.

**6. Per-symbol calls in a loop.** One `/market-watch` request returns all
~496. At 5-minute polling, per-symbol is ~35,700 requests/day versus 72.

**7. HTTP 429 / 503.** What do we do? Retrying into it is the most likely
reason to get blocked. Do we honour `Retry-After`?

**8. Caching.** PSX sends no `ETag`/`Last-Modified` and `no-store`, so HTTP
caching is impossible and a client-side TTL cache is the only option. Do we
have one? Do multiple processes share it or duplicate the work?

**9. Off-hours polling.** Do we keep polling overnight and at weekends for
data that cannot change?

**10. Friday's split session.** PSX breaks for Jummah. Does any
market-hours logic we have know that?

**11. Misreading a 403 as a block.** There is no WAF on this service. A 403
on a data route means you are on a node that does not serve them — the same
condition that gives 404 without an `X-Requested-With` header. Does our code
treat 403 as a ban, disable itself, or start rotating User-Agents/proxies?
The last one is the behaviour most likely to get us *genuinely* blocked.

## Step 3 — audit the data we have already stored

This is the part that matters most, because these failures are silent: the
requests succeed and nothing alarms. Query our actual database.

**Correctness**

- Pick 3 symbols and 3 past trading days. Compare our stored OHLC and volume
  against the live PSX website. Do they match exactly?
- Are our dates shifted by a day anywhere? Check a bar near a month boundary.
- Do `open` and `close` look transposed on any historical series? (Failure
  mode #4 produces exactly this.)
- Do we have rows on weekends or public holidays? We should not.

**Completeness**

- How many distinct symbols do we track, against ~496 trading and ~1,029
  listed? What is the gap, and is it deliberate?
- Are there trading days missing from our history entirely? Those are
  probably days the 404-node problem won a coin flip.
- Which symbols **stopped updating** and never resumed? Each is either a
  delisting or a silent rename. `ENGRO` became `ENGROH` in 2024 with no
  redirect and no error — anything still polling `ENGRO` has recorded
  nothing since. List every such symbol with its last-seen date.

**Freshness**

- What is the actual lag between a PSX price change and it appearing in our
  store, during market hours?

## Step 4 — what we are not collecting

`psx-dps` exposes the list below. For each, state whether we collect it
today, and whether it would be useful to us. The goal is to find data we
could have for free and are not taking.

| Available | Method | Notes |
|---|---|---|
| Full order book depth | `market_watch()` | `obq`, `obc`, `osq`, `osc` — total buy/sell order quantity and count, per symbol |
| Index membership per symbol | `market_watch()` `listed` | Which of 17 indices each symbol belongs to, free in the row you already fetched |
| Index constituents + weights | `index_constituents()` | KSE-100 members **with index weight and free float** |
| Index levels | `indices()` | High, low, current, change for all 18 indices |
| Sector aggregates | `sector_summary()` | Advance/decline/turnover/market cap for 38 sectors |
| Market breadth | `breadth()` | Advancers / decliners / unchanged as fractions |
| Corporate announcements | `announcements()` | 224k+ filings with PDF URLs; filter by symbol, free text or date |
| Regulator notices | `announcements(kind=...)` | PSX, SECP, CDC, NCCPL feeds |
| Intraday tick series | `intraday()` | Every tick today, per symbol |
| 5 years of EOD | `eod()` | One request per symbol |
| Day's full board | `history_by_date()` | Every symbol on any past trading day |
| Monthly bars with high/low | `history_by_month()` | `eod()` has no high/low; this does |
| Debt instruments | `symbols(include_debt=True)` | 265 government and corporate debt lines, maturity encoded in the symbol |
| ETFs | `symbols(etf_only=True)` | 9 of them |
| Timestamped whole-market snapshot | `snapshot()` | Numeric, one request — build your own intraday series |

Flag the reverse too: anything we depend on that `psx-dps` does **not**
wrap. It deliberately omits `/portfolio/*` (needs an account), `/screener`,
`/listings` and others listed at the end of ENDPOINTS.md. One missing field
we rely on may be decisive.

## Step 5 — verify its claims, do not take them

Test these against the live service and report any that fail:

- A full market sweep is 6 requests and covers ~496 symbols
- `snapshot()` is 1 request for the whole market
- Off-hours polling makes 0 upstream requests after the first
- The cooldown is shared across processes
- `market_watch()` matches the live PSX website for 3 symbols you pick,
  including change and volume

Run its suite (`pip install pytest && python -m pytest`) — should be 80
tests, all offline. Run `psx-dps diagnose --deep` and report the output.

**Do not load-test PSX. Do not deliberately trigger a 429.** It is a free
public service and the point of this exercise is to be gentler on it.

## Step 6 — the verdict

Give me:

1. **A table** of the 11 failure modes: handled by us / handled by psx-dps.
2. **Data quality findings** from Step 3, ordered by how much of our stored
   history is affected. Say plainly if any of it needs re-fetching.
3. **The gap list** from Step 4: data available and unused, ranked by
   usefulness to this app.
4. **Risks in our current code**, ordered by likelihood of producing wrong
   data or getting us blocked, each with file and line.
5. **What we would lose** by switching — missing endpoints, a dependency on
   an unofficial third-party repo, Python-only if we are not Python,
   migration effort in hours, anything its docs admit is untested.
6. **A recommendation**, one of:
   - adopt wholesale
   - adopt for the transport and safeguards only, keep our own parsing
   - copy specific fixes into our code, do not take the dependency
   - do not adopt, because `<reason>`
7. If adopting, **a migration plan** with a rollback path and a period
   running both side by side to diff outputs before cutting over.

Be direct about where our existing code is already fine. If we are doing
something better than `psx-dps`, say so — I will upstream it.
