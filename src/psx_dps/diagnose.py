"""Self-diagnosis: work out *which* thing broke, and fix what can be fixed.

PSX breaks in a handful of distinct ways that all look the same from the
outside -- "no data" -- but need completely different responses. This module
separates them, so neither a human nor an agent has to guess:

    local network   -> nothing to do with PSX
    node selection  -> the 404-on-some-nodes problem; re-pin
    cooldown        -> we are deliberately quiet; wait
    budget          -> our own fuse; raise it or fix the loop
    reachable but
      wrong shape   -> PSX changed the payload; the parser needs updating
    all gone        -> PSX moved or removed the route; re-derive it

The last two are the ones that need a human, and they are exactly the ones a
plain "is it up?" check cannot tell apart. So every endpoint check validates
the *shape* of what came back, not just the status code. A route that
returns 200 and an unparseable body is a failure here, loudly, rather than
silently feeding an empty table into your database.
"""

import json
import os
import socket

from . import market
from .cache import Cache
from .errors import PSXError
from .parsing import table_records
from .ratelimit import Breaker
from .transport import HOST, PROBE_PATH, SEED_NODES, Transport

OK, WARN, FAIL = "ok", "warn", "fail"


class Check:
    def __init__(self, name, status, detail, fix=None):
        self.name = name
        self.status = status
        self.detail = detail
        self.fix = fix

    def as_dict(self):
        return {"check": self.name, "status": self.status,
                "detail": self.detail, "fix": self.fix}


# -- endpoint canaries -----------------------------------------------------
#
# Each asserts the *shape* we parse against. When PSX redesigns something
# these are what turn a silent wrong answer into a named failure.

def _canary_symbols(text):
    rows = json.loads(text)
    if not isinstance(rows, list) or not rows:
        raise ValueError("expected a non-empty JSON list")
    missing = {"symbol", "name", "sectorName", "isETF", "isDebt"} - set(rows[0])
    if missing:
        raise ValueError(f"row is missing keys: {sorted(missing)}")
    return f"{len(rows)} instruments"


def _canary_market_watch(text):
    rows = table_records(text)
    if not rows:
        raise ValueError("no rows parsed from the table")
    needed = {"symbol", "open", "high", "low", "close", "volume"}
    missing = needed - set(rows[0])
    if missing:
        raise ValueError(
            f"columns missing: {sorted(missing)}; got {sorted(rows[0])[:12]}"
        )
    return f"{len(rows)} trading symbols"


def _canary_eod(text):
    blob = json.loads(text)
    data = blob.get("data") or []
    if blob.get("status") != 1 or not data:
        raise ValueError(f"status={blob.get('status')} rows={len(data)}")
    if len(data[0]) != 4:
        raise ValueError(
            f"expected 4 values per row [epoch, close, volume, open], "
            f"got {len(data[0])}: {data[0]}"
        )
    return f"{len(data)} daily bars"


def _canary_intraday(text):
    blob = json.loads(text)
    if blob.get("status") != 1:
        return f"status={blob.get('status')} ({blob.get('message')})"
    data = blob.get("data") or []
    if data and len(data[0]) != 3:
        raise ValueError(
            f"expected 3 values per row [epoch, price, volume], got {data[0]}"
        )
    return f"{len(data)} ticks"


def _canary_indices(text):
    rows = table_records(text)
    if not rows:
        raise ValueError("no rows parsed")
    return f"{len(rows)} indices"


def _canary_breadth(text):
    rows = json.loads(text)
    if not isinstance(rows, list) or not rows or "value" not in rows[0]:
        raise ValueError(f"unexpected shape: {str(rows)[:80]}")
    return ", ".join(f"{r['name']}={r['value']:.0%}" for r in rows)


def _canary_announcements(text):
    rows = table_records(text, link_field="url", host=HOST)
    if not rows:
        raise ValueError("no rows parsed")
    return f"{len(rows)} rows, {sum(1 for r in rows if r['url'])} with documents"


def _canary_historical(text):
    rows = table_records(text)
    if not rows:
        raise ValueError("no rows parsed (non-trading day, or layout changed)")
    return f"{len(rows)} rows"


ENDPOINT_CANARIES = [
    ("GET  /symbols", "/symbols", None, _canary_symbols),
    ("GET  /market-watch", "/market-watch", None, _canary_market_watch),
    ("GET  /timeseries/eod/MARI", "/timeseries/eod/MARI", None, _canary_eod),
    ("GET  /timeseries/int/MARI", "/timeseries/int/MARI", None, _canary_intraday),
    ("GET  /indices", "/indices", None, _canary_indices),
    ("GET  /data/symbol-position", "/data/symbol-position", None, _canary_breadth),
    ("POST /announcements", "/announcements",
     {"type": "C", "symbol": "", "query": "", "count": "5", "offset": "0",
      "date_from": "", "date_to": "", "page": "annc"}, _canary_announcements),
]


def _last_trading_day():
    """Most recent weekday, as a date string, for the historical canary."""
    import datetime as dt

    day = market.now_pkt().date()
    for _ in range(7):
        if day.weekday() < 5 and day != market.now_pkt().date():
            return day.isoformat()
        day -= dt.timedelta(days=1)
    return day.isoformat()


# -- the checks ------------------------------------------------------------

def run(transport=None, deep=False, rescan=False, log=None):
    """Run the diagnostic chain. Returns (checks, fixed).

    `deep` also exercises every endpoint canary (costs ~8 requests).
    `rescan` hunts for renumbered nodes near known addresses.
    """
    log = log or (lambda *_: None)
    transport = transport or Transport()
    checks, fixed = [], []

    # 1. DNS
    dns = transport.dns()
    if dns:
        checks.append(Check("dns", OK, f"{HOST} -> {', '.join(dns)}"))
    else:
        checks.append(Check(
            "dns", FAIL, f"cannot resolve {HOST}",
            "Check your resolver/VPN. Nothing below will work until this does.",
        ))
        return checks, fixed

    # 2. Candidate pool
    candidates = transport.candidates()
    state = transport._state()
    checks.append(Check(
        "node pool", OK,
        f"{len(candidates)} candidate(s): {', '.join(candidates)} "
        f"(remembered: {', '.join(state.get('known', [])) or 'none'})",
    ))

    # 3. Probe every candidate, separating "refused" from "no data routes"
    good, reachable, refused = [], [], []
    for ip in candidates:
        ok, detail = transport.probe(ip)
        if ok:
            good.append(ip)
        elif isinstance(detail, Exception):
            refused.append((ip, detail))
        else:
            reachable.append((ip, detail))

    if good:
        checks.append(Check(
            "node health", OK,
            f"serving data: {', '.join(good)}"
            + (f" | serving pages but NOT data: "
               f"{', '.join(ip for ip, _ in reachable)}" if reachable else ""),
        ))
        if transport.pinned_node() not in good:
            transport._save_state(good[0], candidates)
            transport._set_node(good[0])
            fixed.append(f"re-pinned to {good[0]}")
        else:
            transport._set_node(transport.pinned_node())
    elif refused and not reachable:
        checks.append(Check(
            "node health", FAIL,
            f"every candidate refused to connect ({refused[0][1]})",
            "This is a local network/firewall/proxy problem, not PSX.",
        ))
        return checks, fixed
    else:
        checks.append(Check(
            "node health", FAIL,
            f"{len(reachable)} node(s) answered but none serve the data routes",
            "PSX may have renumbered. Re-run with --rescan, or set PSX_NODE=<ip>.",
        ))
        if rescan:
            found = _rescan(transport, candidates, log)
            if found:
                transport._save_state(found, candidates + [found])
                transport._set_node(found)
                fixed.append(f"found a working node by rescan: {found}")
                checks.append(Check("rescan", OK, f"recovered node {found}"))
                good = [found]
            else:
                checks.append(Check(
                    "rescan", FAIL, "no working node found near known addresses",
                    "PSX has moved further than a nearby scan can find. See "
                    "docs/TROUBLESHOOTING.md -> 'Re-deriving everything'.",
                ))
        if not good:
            return checks, fixed

    # 4. TLS is validated against the hostname even though we dial an IP
    ok, detail = transport.verify_tls(good[0])
    if ok:
        checks.append(Check("tls", OK, f"valid certificate for {detail}"))
    else:
        checks.append(Check("tls", FAIL, f"certificate problem: {detail}",
                            "Do not disable verification. Investigate the proxy."))

    # 5. Our own guard rails
    cooldown = transport.breaker.status()
    if cooldown["cooling_down"]:
        checks.append(Check(
            "cooldown", WARN,
            f"standing down {cooldown['seconds_remaining']}s after "
            f"{cooldown['strikes']} strike(s): {cooldown['reason']}",
            "Expected after PSX pushes back. Wait it out; do not clear it "
            "unless you have fixed the cause.",
        ))
    else:
        checks.append(Check("cooldown", OK,
                            f"clear ({cooldown['strikes']} recent strike(s))"))

    spent = transport.throttle.spent()
    budget = transport.throttle.daily_budget
    status = WARN if budget and spent > budget * 0.8 else OK
    checks.append(Check(
        "budget", status, f"{spent} requests in the last 24h"
        + (f" of {budget}" if budget else " (no limit)"),
        "Near the fuse. Find what is looping before raising it."
        if status == WARN else None,
    ))

    # 6. Cache must be writable or everything gets slower and noisier
    cache = transport.cache
    probe_path = os.path.join(cache.dir, ".diagnose")
    try:
        os.makedirs(cache.dir, exist_ok=True)
        with open(probe_path, "w") as fh:
            fh.write("ok")
        os.remove(probe_path)
        checks.append(Check("cache", OK, f"writable at {cache.dir}"))
    except OSError as exc:
        checks.append(Check(
            "cache", WARN, f"not writable ({exc})",
            "Without a cache every call hits PSX. Fix permissions or set "
            "PSX_DPS_CACHE_DIR.",
        ))

    checks.append(Check("market session", OK,
                        f"{market.session_state()} "
                        f"({market.now_pkt().strftime('%a %Y-%m-%d %H:%M %Z')})"))

    # 7. Endpoint canaries -- shape, not just status
    if deep:
        canaries = list(ENDPOINT_CANARIES)
        canaries.append(("POST /historical", "/historical",
                         {"date": _last_trading_day()}, _canary_historical))
        for label, path, body, validate in canaries:
            try:
                text = transport.request(path, body=body, ttl=300)
            except PSXError as exc:
                checks.append(Check(
                    label, FAIL, f"request failed: {exc}",
                    "Route may have moved or been removed. See "
                    "docs/TROUBLESHOOTING.md -> 'Re-deriving everything'.",
                ))
                continue
            try:
                checks.append(Check(label, OK, validate(text)))
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                checks.append(Check(
                    label, FAIL, f"responded, but the shape changed: {exc}",
                    "PSX altered this payload. The parser needs updating -- "
                    "this is the case that silently corrupts data, so fix it "
                    "before trusting anything downstream.",
                ))

    return checks, fixed


def _rescan(transport, known, log):
    """Last resort: look for a renumbered node near addresses we knew.

    Deliberately narrow -- a handful of addresses either side of each known
    IP, not a subnet sweep. This is a recovery action for when PSX has
    renumbered and every candidate is dead; it is not something to run on a
    schedule, and the CLI only does it when asked.
    """
    seen, targets = set(), []
    for ip in list(known) + SEED_NODES:
        parts = ip.split(".")
        if len(parts) != 4 or not parts[3].isdigit():
            continue
        base, last = ".".join(parts[:3]), int(parts[3])
        for offset in range(-8, 9):
            candidate = f"{base}.{last + offset}"
            if 0 < last + offset < 255 and candidate not in seen:
                seen.add(candidate)
                targets.append(candidate)
    log(f"  rescanning {len(targets)} nearby addresses...")
    for ip in targets:
        try:
            with socket.create_connection((ip, 443), timeout=2):
                pass
        except OSError:
            continue
        ok, _ = transport.probe(ip)
        log(f"    {ip}: {'SERVES DATA' if ok else 'reachable, no data routes'}")
        if ok:
            return ip
    return None
