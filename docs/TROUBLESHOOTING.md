# When PSX stops working

PSX is an undocumented, unversioned service that can change without notice.
This is the record of how it has broken, how each break was diagnosed, and
what to do next time — written so that a person **or an agent** can work
through it without re-deriving anything.

**Start here. It fixes what it can and names what it cannot:**

```bash
psx-dps diagnose --deep
```

---

## What `diagnose` is actually doing

Every PSX failure looks identical from the outside — "no data" — but the
responses are completely different. The command's job is to tell them apart.

| It finds | Meaning | You should |
|---|---|---|
| `dns` FAIL | Cannot resolve the host | Fix your resolver/VPN. Not PSX. |
| `node health` FAIL, all refused | Nothing accepted a connection | Local network, firewall or proxy. Not PSX. |
| `node health` FAIL, some answered | Nodes alive, none serve data routes | PSX likely renumbered → `--rescan` |
| `node health` OK, re-pinned | The classic problem, already fixed | Nothing |
| `cooldown` WARN | We are deliberately quiet | Wait. See [STAYING-UNBLOCKED](STAYING-UNBLOCKED.md) |
| `budget` WARN | Our own fuse is near | Find the loop before raising it |
| `cache` WARN | Cache not writable | Fix permissions — every call hits PSX otherwise |
| An endpoint FAIL "shape changed" | **PSX altered a payload** | Update the parser. Details below. |
| An endpoint FAIL "request failed" | Route moved or was removed | Re-derive it. Details below. |

Exit status is non-zero on any failure, so it drops straight into a health
check or a CI job.

The `--deep` endpoint checks validate the **shape** of each response, not
just the status code. That matters more than it sounds: a route returning
`200` with a redesigned body is the failure that silently writes garbage
into your database for a week before anyone notices. `diagnose --deep`
turns that into a named, loud failure.

---

## The one that has actually happened

### Symptom: every data endpoint returns 404, but the website works fine

This is the big one, and it is not what it looks like.

```
GET /symbols              -> 404
GET /market-watch         -> 404
GET /timeseries/eod/MARI  -> 404
GET /company/MARI         -> 200      <- the site is clearly up
```

**The wrong conclusion** — and the one most published PSX scrapers appear to
have reached — is "PSX removed the API".

**What is actually happening:** `dps.psx.com.pk` is served by more than one
node, and some of them do not serve the JSON/XHR data routes at all, while
serving the ordinary HTML site perfectly. The zone publishes a *single
rotating A record* with a 300-second TTL, so DNS will hand you a broken node
and you will keep landing on it.

Proof, and the check to run any time you suspect this:

```bash
dig +short A dps.psx.com.pk          # may show only ONE address
for ip in 52.128.23.16 52.128.23.6; do
  echo -n "$ip -> "
  curl -s -o /dev/null -w '%{http_code}\n' --resolve dps.psx.com.pk:443:$ip \
    https://dps.psx.com.pk/data/symbol-position
done
```

Observed live on 2026-09-29, with every public resolver agreeing on the bad
address:

```
52.128.23.6    -> 404      (dig, 8.8.8.8 and 1.1.1.1 all returned this one)
52.128.23.16   -> 200
```

**How it was ruled in.** Three things disproved "the API is gone":

1. The portal's own JS bundle, served that same day, still called those
   routes. If they were gone, PSX's own charts would be broken. They were not.
2. The 404 body was the app's own styled 404 page, not a WAF block page.
3. `dig` had returned *two* A records earlier in the session.

**Fix:** handled automatically. `psx-dps` keeps a candidate pool, probes for
a node that really serves data, pins it, and re-probes rather than believing
a 404. `psx-dps diagnose` re-pins and reports it.

### Dead ends, so nobody retests them

All of these were tried and made no difference: browser vs curl vs httpx
User-Agents, cookies from the homepage, the `Referer` header, HTTP vs HTTPS,
trailing slashes, and geo-blocking. One thing is actively harmful:

> Sending `X-Requested-With: XMLHttpRequest` triggers an empty **403**.
> Do not send it.

---

## Failures that have not happened yet, and what to do

### PSX renumbered its nodes

`diagnose` reports nodes answering but none serving data, and `--rescan`
finds nothing.

```bash
psx-dps diagnose --rescan
```

`--rescan` checks a handful of addresses either side of every address we
have ever known — deliberately narrow, a recovery action and not something
to run on a schedule.

If that fails, the pool needs reseeding. Because the A record rotates, **the
good node may simply not be in DNS right now**. Sample DNS over time rather
than in one burst:

```bash
# collect the rotation over ~15 minutes; the record changes every ~300s
for i in $(seq 1 15); do dig +short A dps.psx.com.pk; sleep 60; done | sort -u
```

Then test each address found, and pin the winner:

```bash
export PSX_NODE=<the address that returns 200>
```

Permanent fix: add it to `SEED_NODES` in `src/psx_dps/transport.py` and open
a PR — that address is then remembered for everyone.

### PSX changed a payload shape

`diagnose --deep` reports `responded, but the shape changed`. **Stop
trusting downstream data until this is fixed** — the request is succeeding,
so nothing else will alarm.

1. Fetch the raw body and look at it:
   ```bash
   curl -s --resolve dps.psx.com.pk:443:$(psx-dps doctor | awk '/^pinned/{print $2}') \
     https://dps.psx.com.pk/market-watch | head -c 2000
   ```
2. For an HTML table, compare the `data-name` attributes against the
   expected columns in [ENDPOINTS.md](ENDPOINTS.md).
3. Update the canary in `src/psx_dps/diagnose.py` *and* the parser, capture
   a new fixture into `tests/fixtures/`, and add a test that would have
   caught it.

### A route moved or disappeared

`diagnose --deep` reports `request failed` for one endpoint while others are
healthy. Re-derive it from the portal's own front end — this is the method
that found every endpoint in the first place, and it still works:

```bash
# 1. the portal ships one bundle and it is not obfuscated
curl -s https://dps.psx.com.pk/static/script.js -o /tmp/psx.js

# 2. every endpoint it calls
grep -oE '(fetch\(|\$\.(get|post|ajax)\(|url:\s*)[^,;)]{0,120}' /tmp/psx.js | sort -u

# 3. every quoted path
grep -oE '"/[a-z0-9][a-z0-9/_-]{2,40}"' /tmp/psx.js | sort -u
```

Templated calls need reading in context. `/timeseries/` was found this way:

```js
$.getJSON("/timeseries/" + type + "/" + this.symbol, ...)
```

and the surrounding code gave both the valid values of `type`
(`int`, `eod`, `nav`) and the row layout:

```js
{ time: new Date(1e3*d[0]), value: d[1], volume: d[2], open: d[3] }
```

which is why EOD rows are `[epoch, close, volume, open]` and not OHLC. POST
bodies for `/historical` and `/announcements` came from their click handlers
in the same bundle.

One trap while reading it: `psx.helpers.getUrl()` looks like it rewrites
paths but is identity except under `/sukuk`.

Full write-up: [DISCOVERY.md](DISCOVERY.md).

### PSX is pushing back

`diagnose` reports a cooldown. This is the system working, not a fault.
Do not clear it to keep polling — see
[STAYING-UNBLOCKED.md](STAYING-UNBLOCKED.md) for the runbook.

### Everything is fine but the numbers look wrong

| Looks like | Likely cause |
|---|---|
| Dates off by a day | PSX epochs are PKT wall clock; you parsed them in local time |
| Historical series scrambled | EOD is `[epoch, close, volume, open]`, not OHLC |
| A symbol "missing" | `/market-watch` (~496 trading) ≠ `/symbols` (~1,030 listed) |
| A symbol vanished for good | Renamed by a corporate action — `ENGRO` became `ENGROH` |
| Stale prices | Check `session` in `diagnose` — the market may simply be shut |
| Weekend returns nothing | Correct. A non-trading day returns an empty body |

---

## Wiring this into your own app

`diagnose` is importable, and `--json` is built for automation:

```python
from psx_dps import diagnose

checks, fixed = diagnose.run(deep=True)
broken = [c for c in checks if c.status == diagnose.FAIL]
if broken:
    alert("PSX integration degraded:\n" + "\n".join(
        f"{c.name}: {c.detail}\n  fix: {c.fix}" for c in broken
    ))
```

```bash
psx-dps diagnose --deep --json > health.json   # exit 1 if anything failed
```

A sensible cadence is once an hour, or on the first failure after a run of
successes. The deep checks cost about 8 requests, so do not put them in the
poll loop itself.

**A note on `--deep` in CI:** it makes real requests to PSX. Run it on a
schedule, not on every commit. The unit test suite is entirely offline and
is the thing that belongs in CI.
