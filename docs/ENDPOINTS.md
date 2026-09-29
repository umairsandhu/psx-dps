# PSX data portal endpoint reference

Base: `https://dps.psx.com.pk` — everything below is unauthenticated.
Undocumented and unversioned; PSX can change any of it without notice.

**Before you call these directly**, read
[DISCOVERY.md § 3](DISCOVERY.md#3-then-every-data-endpoint-returned-404): a
plain request to any of these has a good chance of returning 404 purely
because of which node DNS handed you. That is not a dead endpoint.

Verified live on 2026-09-29.

## JSON

| Endpoint | Returns |
|---|---|
| `GET /symbols` | Full universe, ~1,030 rows, ~130 KB: `[{symbol, name, sectorName, isETF, isDebt}]` |
| `GET /timeseries/int/{SYM}` | Today's ticks: `{status, message, data: [[epoch, price, volume], ...]}` |
| `GET /timeseries/eod/{SYM}` | ~5 years daily: `{status, message, data: [[epoch, close, volume, open], ...]}` |
| `GET /timeseries/nav/{SYM}` | NAV series (ETFs) |
| `GET /data/top-10-symbols` | `[{name, symbol, volume}]` |
| `GET /data/top-10-sectors` | `[{name, code, volume}]` |
| `GET /data/symbol-position` | Breadth: `[{name: "ADV"\|"DEC"\|"UNCH", value: fraction}]` |

Both series are **newest first** — reverse them for chronological order.

Note the EOD column order: `[epoch, close, volume, open]`, not OHLC. There
is no high/low on this route; use `POST /historical` for those.

`status: 1` means OK. Unknown symbols differ by route: `/int` returns
`status: 0` with a message, `/eod` returns `status: 1` and an empty `data`.

Timestamps are Pakistan wall-clock seconds (UTC+5, no DST). Read them as UTC
and label them PKT; using host-local time shifts the dates.

## HTML fragments

Server-rendered tables. Numeric cells carry the raw value in `data-order`;
on date columns `data-order` is an epoch and the cell text is the date.

| Endpoint | Returns |
|---|---|
| `GET /market-watch` | Every trading symbol, ~496 rows, ~475 KB. Columns via `data-name`: `symbol, sector, listed, ldcp, open, high, low, close, change, percentChange, volume, obq, obc, osq, osc` |
| `GET /indices` | All index levels: index, high, low, current, change, % change |
| `GET /indices/{CODE}` | Constituents with weights. `KSE100`, `KSE30`, `KMI30`, `ALLSHR`, `KMIALLSHR`, `KSE100PR` |
| `GET /sector-summary/sectorwise` | Summary table **then one table per sector** — parse only the first |
| `GET /performers`, `GET /debt-performers` | Top active / gainers / losers widgets |
| `GET /company/{SYM}` | Company page; quote in `data-current` / `data-high` / `data-low` |
| `GET /eligible-scrips` | Margin/CDS eligible securities |
| `GET /circuit-breakers` | Symbols at price limits |
| `GET /payouts`, `GET /monthly-reports/archives`, `GET /sukuk/symbols` | Self-describing tables |
| `GET /trading-board/{name}/{board}` | Board-specific tables |
| `GET /download/document/{id}.pdf` | Announcement PDFs |

`obq/obc/osq/osc` are total buy/sell order quantity and count.

`/market-watch` holds only what is **trading** (~496) against ~1,030 in
`/symbols`. Listed-but-absent is normal, not an error.

## POST

Both take `application/x-www-form-urlencoded`.

### `POST /historical`

Two modes.

```bash
# every symbol on one trading day
curl -X POST -d "date=2026-09-25" https://dps.psx.com.pk/historical
# one symbol's month (month 1-12, not zero-padded)
curl -X POST -d "month=9&year=2026&symbol=MARI" https://dps.psx.com.pk/historical
```

Datewise columns: `symbol, ldcp, open, high, low, close, change,
percentChange, volume`. Symbolwise: `time, open, high, low, close, volume`.

A weekend or public holiday returns a near-empty body, not an error. History
goes back about five years.

### `POST /announcements`

~224,000 rows, paginated.

| Field | Meaning |
|---|---|
| `type` | `A` CDC · `B` SECP · `C` Companies · `D` NCCPL · `E` PSX |
| `symbol` | optional, e.g. `ENGROH` |
| `query` | free-text title search |
| `count`, `offset` | pagination |
| `date_from`, `date_to` | `YYYY-MM-DD` |
| `page` | must be `annc` |

Columns: `DATE, TIME, SYMBOL, NAME, TITLE` plus a link cell whose href is
`/download/document/{id}.pdf`. Regulator feeds (`type=E`) have no symbol
column.

## Request notes

- **Do not send `X-Requested-With: XMLHttpRequest`** — it triggers an empty
  `403` from something in front of the app. Ordinary requests are fine.
- No `ETag` or `Last-Modified`, and `Cache-Control: no-store`. Conditional
  requests are impossible; cache client-side on a TTL.
- Keep-alive is supported and worth using.
- No CORS headers — browser `fetch` from another origin will fail. This is a
  server-side API.
- Rate limiting is unpublished but real; bursts cause connections to hang for
  a minute or two. See [FAIR-USE.md](FAIR-USE.md).

## Routes that exist but are not wrapped here

`/portfolio/*` (needs an account), `/company/payouts` and
`/listings-table/{board}/{type}` (500 without the right parameters),
`/calendar`, `/announcements/{cdc,companies,nccpl,psx,secp}` (page shells for
the POST above), `/screener`, `/listings`, `/nd-verification`,
`/gis-auction-results`, `/debt-market`, `/graphical-view`,
`/indices-div-discount`, `/progress-report`, `/corporate-briefing`.
