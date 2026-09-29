# How this was reverse-engineered

PSX publishes no API and no documentation for one. This is the full record of
how the endpoints in [ENDPOINTS.md](ENDPOINTS.md) were found, including the
wrong turns — mostly so that when PSX changes something, the next person can
repeat the method instead of guessing.

## 1. The data portal is a separate app from the main site

`www.psx.com.pk` is a WordPress marketing site. The actual market data lives
at `dps.psx.com.pk` ("data portal services"), a server-rendered app. Every
useful route is on `dps`.

## 2. The front end tells you its own endpoints

The portal ships one bundle, `/static/script.js` (~2.8 MB, minified). It is
not obfuscated, so the endpoint list is simply readable:

```bash
curl -s https://dps.psx.com.pk/static/script.js | \
  grep -oE '(fetch\(|\$\.(get|post|ajax)\(|url:\s*)[^,;)]{0,120}' | sort -u
```

That produced `/symbols`, `/market-watch`, `/performers`, `/historical`,
`/announcements`, `/data/top-10-symbols`, `/data/top-10-sectors`,
`/data/symbol-position`, `/sector-summary/sectorwise`, `/indices/{code}`, and
more. The time-series call was a template rather than a literal:

```js
$.getJSON("/timeseries/" + type + "/" + this.symbol, ...)
```

Reading the surrounding code gave the three valid values of `type` —
`int`, `eod`, `nav` — and the shape of a row:

```js
{ time: new Date(1e3*d[0]), value: d[1], volume: d[2], open: d[3] }
```

which is how we know `eod` rows are `[epoch, close, volume, open]` and not
the OHLC order you would expect. The POST bodies for `/historical` and
`/announcements` came from the same bundle, out of their click handlers.

A note for anyone repeating this: `psx.helpers.getUrl()` appears all over the
bundle and looks like it might rewrite paths. It does not — it is identity
except under `/sukuk`.

## 3. Then every data endpoint returned 404

With the paths in hand, all of them 404'd, while ordinary pages like
`/company/MARI` returned 200. That pattern — pages fine, data routes gone —
looks exactly like "PSX removed the API", and it is almost certainly why
several published PSX scrapers are reported as broken.

It was wrong. Three observations disproved it:

1. The live bundle served *that same day* still called those routes. If they
   were gone, the portal's own charts would be broken. They were not.
2. The 404 body was the app's own styled 404 page, not a WAF block page.
3. `dig` had, earlier in the session, returned **two** A records.

Pinning the request to each address separately settled it:

```
52.128.23.6    /data/symbol-position -> 404    /company/MARI -> 200
52.128.23.16   /data/symbol-position -> 200    /company/MARI -> 200
```

**Some nodes behind `dps.psx.com.pk` do not serve the data routes at all,
while serving the HTML site perfectly.**

## 4. …and DNS hands out the broken one

Worse than a flaky node. The zone publishes a *single* rotating A record with
a 300-second TTL. During testing it settled on the bad node, and

```bash
dig +short A dps.psx.com.pk            # 52.128.23.6
dig +short @8.8.8.8 A dps.psx.com.pk   # 52.128.23.6
dig +short @1.1.1.1 A dps.psx.com.pk   # 52.128.23.6
```

all agreed — the good node was not in DNS at all. So a plain
`requests.get("https://dps.psx.com.pk/market-watch")` is a coin flip decided
by which node the rotation is advertising when you call.

This is why `psx-dps` keeps a candidate pool (DNS answer + every address DNS
has ever returned, persisted + a seed list), probes for one that really
serves data, pins it, and re-probes on a 404 instead of believing it. TLS is
still validated against `dps.psx.com.pk` via SNI, so pinning by IP costs
nothing in security.

Things ruled out along the way, so nobody re-tests them: it is not the User-
Agent (curl, browser and httpx UAs behave identically), not cookies, not the
`Referer`, and not geo-blocking.

### There is no WAF blocking us

Worth stating plainly, because the symptoms invite the opposite conclusion.

An early run sent `X-Requested-With: XMLHttpRequest` and got an empty `403`
on exactly the data routes, which looked like a bot filter. It is not. That
run was also landing on the bad node, and the two were confounded. A
controlled test across both nodes settles it:

| Node | Path | `X-Requested-With` | Status |
|---|---|---|---|
| `.16` (good) | `/symbols` | absent | 200 |
| `.16` (good) | `/symbols` | **sent** | **200** |
| `.16` (good) | `/company/MARI` | sent | 200 |
| `.6` (bad) | `/symbols` | absent | 404 |
| `.6` (bad) | `/symbols` | **sent** | **403** |
| `.6` (bad) | `/company/MARI` | sent | 200 |

So the header is harmless on a node that works. The `403` is just the bad
node's other way of saying "I do not serve data routes" — 404 without the
header, 403 with it.

This is useful diagnostically: **a 403 on a data route is a positive
signal that you are on the wrong node**, and a more specific one than a 404.

No block page, no CAPTCHA, no challenge, no `Retry-After`, and no
`robots.txt` on `dps` at all have ever been observed. The only real limit is
an unpublished rate limit that manifests as connections hanging for a minute
or two — see below.

## 5. Load-shaping facts, established by measurement

- **No `ETag`, no `Last-Modified`** on any data route, and
  `Cache-Control: no-cache, private, no-store`. Conditional requests are
  therefore impossible: you cannot cheaply ask "has this changed?". The only
  way to be light on PSX is a client-side TTL cache, which is why the cache
  in this package is not optional garnish.
- **Keep-alive works.** A second request on the same connection reports
  `num_connects=0`, so a burst costs one TLS handshake, not N.
- **`dps.psx.com.pk` serves no `robots.txt`** (404). `www.psx.com.pk` does,
  and it is permissive: `User-agent: * / Disallow:` with only `/cgi-bin/`
  excluded, and no `Crawl-delay`. There is no published crawl policy to
  follow, so this client imposes its own — see [FAIR-USE.md](FAIR-USE.md).
- **Probing fast gets you throttled.** A burst of a few dozen requests while
  mapping endpoints caused every connection to hang (`curl` exit 28) for a
  couple of minutes before recovering on its own. Nothing was banned, but the
  lesson stuck and became this package's default 1 req/s.

## 6. Data-shape surprises worth knowing

- `/symbols` lists ~1,030 instruments; `/market-watch` carries ~496. The
  difference is not an error — the watch only holds what is actually trading.
  A symbol can be listed and absent, which is why `quote()` distinguishes
  `NoData` from `UnknownSymbol`.
- Symbols change on corporate actions. `ENGRO` no longer trades; it became
  `ENGROH` after the 2024 Dawood Hercules / Engro scheme of arrangement.
- Numeric table cells hide the clean value in `data-order` behind a rendered
  string (`data-order="-0.23"` under `▼ -0.23`). On **date** columns that
  same attribute is a unix epoch and the text is the readable date, so the
  choice has to be per column.
- `/sector-summary/sectorwise` returns the summary table followed by one
  table per sector. Parsing "all tables" interleaves different row shapes.
- An unknown symbol gives `status: 0` + a message on `/timeseries/int`, but
  `status: 1` with an **empty** `data` array on `/timeseries/eod`.
- Timestamps are Pakistan wall-clock seconds. Format them in UTC (or fixed
  +05:00); using the host's local timezone shifts the dates.
- A weekend or holiday `POST /historical` returns a near-empty body, not an
  error.
