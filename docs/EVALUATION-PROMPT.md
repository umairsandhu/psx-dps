# Evaluation prompt

Hand this to a coding agent working inside an existing PSX app to get an
evidence-based verdict on whether adopting `psx-dps` is worth it. It is
written to be sceptical: the agent is told to verify claims and to report
where this library is *worse*, not to sell it.

Copy everything below the line.

---

You are auditing this project's Pakistan Stock Exchange (PSX) data layer
against an open-source alternative. I want an evidence-based verdict, not a
recommendation to adopt. Verify every claim yourself — do not trust the
other project's README.

## The alternative

Repository: **https://github.com/umairsandhu/psx-dps**

Read these before testing anything (raw URLs so you can fetch them directly):

| Document | What it covers | Raw URL |
|---|---|---|
| README | API surface, install, gotchas | https://raw.githubusercontent.com/umairsandhu/psx-dps/main/README.md |
| ENDPOINTS.md | Every PSX endpoint, payload shapes, quirks | https://raw.githubusercontent.com/umairsandhu/psx-dps/main/docs/ENDPOINTS.md |
| DISCOVERY.md | How the endpoints were reverse-engineered, incl. dead ends | https://raw.githubusercontent.com/umairsandhu/psx-dps/main/docs/DISCOVERY.md |
| FAIR-USE.md | Load budgets, polling arithmetic, legal notes | https://raw.githubusercontent.com/umairsandhu/psx-dps/main/docs/FAIR-USE.md |
| STAYING-UNBLOCKED.md | Back-off behaviour and the runbook for it | https://raw.githubusercontent.com/umairsandhu/psx-dps/main/docs/STAYING-UNBLOCKED.md |

Install (Python 3.8+, no dependencies). Note it imports as `psx_dps`, **not**
`psx` — the `psx` name belongs to the unrelated `psx-data-reader` on PyPI:

```bash
pip install git+https://github.com/umairsandhu/psx-dps
python -c "from psx_dps import Client; print(Client().quote('MARI'))"
psx-dps doctor
```

## Step 1 — find and describe what we do today

Locate every place this project talks to PSX. For each, record: the URL, the
HTTP method, how the response is parsed, how often it is called, and where
the result is stored. Produce a table before you compare anything.

## Step 2 — test our current client against these specific failure modes

Each of these is a real, documented PSX behaviour. For every one, determine
whether **our existing code** handles it, and show the evidence — the line of
code, or a command and its output. Answer "handled" / "not handled" /
"not applicable", never "probably".

**1. Rotating DNS onto a node that 404s the data routes.**
`dps.psx.com.pk` is served by multiple nodes. Some return 404 for every
JSON/XHR data route (`/symbols`, `/timeseries/*`, `/market-watch`, `/data/*`)
while serving ordinary HTML pages with 200. The zone publishes a single
rotating A record (TTL 300), so DNS will hand you a broken node. Verify:

```bash
dig +short A dps.psx.com.pk
for ip in 52.128.23.16 52.128.23.6; do
  echo -n "$ip /data/symbol-position -> "
  curl -s -o /dev/null -w '%{http_code}\n' --resolve dps.psx.com.pk:443:$ip \
    https://dps.psx.com.pk/data/symbol-position
done
```

Does our client survive landing on the 404 node? If it retries the same
hostname, it will keep landing on the same bad node for the TTL.

**2. gzip.** PSX compresses on request but not by default. Market watch is
~476 KB plain, ~60 KB gzipped — 8x. Are we sending `Accept-Encoding: gzip`?

**3. Timestamps.** PSX epochs are Pakistan wall-clock seconds (UTC+5, no
DST). Parsing them in host-local time shifts the dates. What does our code
do, and what happens if the server runs in UTC?

**4. EOD column order.** `/timeseries/eod/{SYM}` rows are
`[epoch, close, volume, open]` — *not* OHLC. Misreading this silently
corrupts every historical series. Check ours against a known value.

**5. `/symbols` is not `/market-watch`.** ~1,030 listed instruments vs ~496
actually trading. Do we treat a listed-but-untraded symbol as an error?
Test with `ENGRO`, which stopped trading after the 2024 merger into `ENGROH`.

**6. Per-symbol calls in a loop.** Do we ever call a per-symbol endpoint for
many symbols? One `/market-watch` request returns all ~496. At 5-minute
polling, per-symbol is ~35,700 requests/day vs 72.

**7. HTTP 429 / 503.** What do we do? If we retry into it, that is the
single most likely reason to get blocked. Do we honour `Retry-After`?

**8. Caching.** Do we cache at all? PSX sends no `ETag`/`Last-Modified` and
`Cache-Control: no-store`, so HTTP caching is impossible — a client-side TTL
cache is the only option. Do multiple processes share one, or duplicate work?

**9. Off-hours polling.** Do we keep polling overnight and at weekends for
data that cannot change?

**10. Friday's split session.** PSX breaks for Jummah. Does any market-hours
logic we have know that?

**11. `X-Requested-With`.** Sending `X-Requested-With: XMLHttpRequest`
triggers an empty 403. Do we send it?

## Step 3 — compare data coverage

Build a table: for every field and endpoint our app uses, can `psx-dps`
supply it? Flag anything we depend on that it does **not** wrap — it
deliberately omits `/portfolio/*` (needs an account), `/screener`,
`/listings`, and others listed at the end of ENDPOINTS.md. A single missing
field we rely on may be decisive.

Then the reverse: what does `psx-dps` expose that we are not currently
using but would want? (`snapshot()`, announcements with PDF URLs, index
constituents with weights, sector summaries, breadth.)

## Step 4 — verify its claims, don't take them

Test at least these against the live service, and report any that fail:

- A full market sweep is 6 requests and covers ~496 symbols
- `snapshot()` is 1 request for the whole market
- Off-hours polling makes 0 upstream requests after the first (cache TTLs
  stretch to the next market open)
- The cooldown is shared across processes
- `market_watch()` and `quote()` agree with the live PSX website for 3
  symbols you pick, including the change and volume figures

Also run its test suite (`pip install pytest && python -m pytest`) and report
pass/fail. It should be 67 tests, all offline.

**Important:** do not load-test PSX. Do not deliberately trigger a 429. Do
not run tight loops against it. This is a free public service and the point
of the exercise is to be gentler on it, not to prove we can break it.

## Step 5 — the verdict

Give me:

1. **A table** of the 11 failure modes: handled by us / handled by psx-dps.
2. **Concrete risks in our current code**, ordered by likelihood of causing
   wrong data or a block, each with the file and line.
3. **What we would lose** by switching — missing endpoints, new dependency
   on an unofficial third-party repo, Python-only if we are not Python,
   migration effort in hours, anything its docs admit is untested.
4. **A recommendation**, one of:
   - adopt wholesale
   - adopt for the transport/safeguards only, keep our own parsing
   - copy specific fixes into our code and do not take the dependency
   - do not adopt, because <reason>
5. If you recommend adopting, **a migration plan** with a rollback path,
   and how we would run both side by side to diff outputs before cutting
   over.

Be direct about where our existing code is already fine. If we are doing
something better than `psx-dps` does, say so — I will upstream it.
