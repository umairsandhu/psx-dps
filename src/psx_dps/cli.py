"""Command line front end. Everything here is a thin shell over Client."""

import argparse
import csv
import json
import sys

from . import __version__, market
from .client import ANNOUNCEMENT_TYPES, Client
from .errors import PSXError
from .transport import HOST, PROBE_PATH, SEED_NODES, Transport


def _emit(records, args, columns=None):
    if not records:
        print("psx-dps: no rows returned", file=sys.stderr)
        sys.exit(1)
    cols = columns or list(records[0].keys())
    if args.json:
        json.dump(records, sys.stdout, indent=2, default=str)
        print()
    elif args.csv:
        writer = csv.DictWriter(sys.stdout, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    else:
        widths = [
            max(len(c), max(len(str(r.get(c, ""))) for r in records)) for c in cols
        ]
        print("  ".join(c.upper().ljust(w) for c, w in zip(cols, widths)).rstrip())
        for row in records:
            print("  ".join(
                str(row.get(c, "")).ljust(w) for c, w in zip(cols, widths)
            ).rstrip())


def _client(args):
    return Client(
        cache=not args.no_cache,
        min_interval=args.min_interval,
        cache_dir=args.cache_dir,
    )


def _num(value):
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return float("-inf")


# -- commands -------------------------------------------------------------

def cmd_symbols(args):
    with _client(args) as psx:
        rows = psx.symbols(include_debt=args.all, etf_only=args.etf_only)
        if args.debt_only:
            rows = [r for r in psx.symbols(include_debt=True) if r.get("isDebt")]
        if args.sector:
            rows = [r for r in rows
                    if args.sector.upper() in (r.get("sectorName") or "").upper()]
        if args.grep:
            needle = args.grep.upper()
            rows = [r for r in rows
                    if needle in r["symbol"].upper()
                    or needle in (r.get("name") or "").upper()]
    _emit(rows, args, ["symbol", "name", "sectorName", "isETF", "isDebt"])


def cmd_quote(args):
    with _client(args) as psx:
        rows = psx.quotes(args.symbols)
        missing = {s.upper() for s in args.symbols} - {r["symbol"].upper() for r in rows}
    if missing:
        print(f"psx-dps: not trading today: {', '.join(sorted(missing))}",
              file=sys.stderr)
    _emit(rows, args, ["symbol", "ldcp", "open", "high", "low", "close",
                       "change", "percentChange", "volume"])


def cmd_market(args):
    with _client(args) as psx:
        rows = psx.market_watch()
    if args.sector:
        rows = [r for r in rows if args.sector.upper() in r.get("sector", "").upper()]
    if args.index:
        rows = [r for r in rows if args.index.upper() in r.get("listed", "").upper()]
    if args.sort == "symbol":
        rows.sort(key=lambda r: r.get("symbol", ""))
    else:
        key = {"volume": "volume", "change": "percentChange"}[args.sort]
        rows.sort(key=lambda r: _num(r.get(key)), reverse=True)
    if args.limit:
        rows = rows[: args.limit]
    _emit(rows, args, ["symbol", "sector", "ldcp", "open", "high", "low",
                       "close", "change", "percentChange", "volume"])


def cmd_intraday(args):
    with _client(args) as psx:
        rows = psx.intraday(args.symbol)
    rows = [{"time": r["time"].strftime("%Y-%m-%d %H:%M:%S"),
             "price": r["price"], "volume": r["volume"]} for r in rows]
    _emit(rows[-args.limit:] if args.limit else rows, args)


def cmd_eod(args):
    with _client(args) as psx:
        rows = psx.eod(args.symbol, since=args.since)
    _emit(rows[-args.limit:] if args.limit else rows, args)


def cmd_history(args):
    with _client(args) as psx:
        if args.symbol:
            if not (args.month and args.year):
                print("psx-dps: --month and --year are required with a symbol",
                      file=sys.stderr)
                sys.exit(2)
            rows = psx.history_by_month(args.symbol, args.month, args.year)
        else:
            if not args.date:
                print("psx-dps: give a SYMBOL with --month/--year, or --date",
                      file=sys.stderr)
                sys.exit(2)
            rows = psx.history_by_date(args.date)
    _emit(rows, args)


def cmd_announcements(args):
    with _client(args) as psx:
        rows = psx.announcements(
            kind=args.type, symbol=args.symbol, query=args.query,
            date_from=args.date_from, date_to=args.date_to,
            count=args.count, offset=args.offset,
        )
    _emit(rows, args)


def cmd_movers(args):
    with _client(args) as psx:
        payload = {
            "top_symbols_by_volume": psx.top_symbols(),
            "top_sectors_by_volume": psx.top_sectors(),
            "breadth": psx.breadth(),
        }
    if args.json:
        json.dump(payload, sys.stdout, indent=2)
        print()
        return
    print("TOP SYMBOLS BY VOLUME")
    _emit(payload["top_symbols_by_volume"], args, ["symbol", "name", "volume"])
    print("\nTOP SECTORS BY VOLUME")
    _emit(payload["top_sectors_by_volume"], args, ["code", "name", "volume"])
    print("\nBREADTH")
    _emit([{"bucket": k, "share": f"{v * 100:.1f}%"}
           for k, v in payload["breadth"].items()], args)


def cmd_indices(args):
    with _client(args) as psx:
        _emit(psx.indices(), args)


def cmd_index(args):
    with _client(args) as psx:
        _emit(psx.index_constituents(args.code), args)


def cmd_sectors(args):
    with _client(args) as psx:
        _emit(psx.sector_summary(), args)


def cmd_doctor(args):
    transport = Transport()
    dns = transport.dns()
    state = transport._state()
    print(f"psx-dps      {__version__}")
    print(f"host         {HOST}")
    print(f"DNS answer   {', '.join(dns) or '(none)'}")
    print(f"remembered   {', '.join(state.get('known', [])) or '(none)'}")
    print(f"pinned       {transport.pinned_node() or '(none)'}")
    print(f"session      {market.session_state()} "
          f"({market.now_pkt().strftime('%a %Y-%m-%d %H:%M %Z')})")
    print(f"cache dir    {transport.cache.dir}")
    print(f"budget used  {transport.throttle.spent()} requests in the last 24h")
    cooldown = transport.breaker.status()
    if cooldown["cooling_down"]:
        print(f"cooldown     STANDING DOWN for {cooldown['seconds_remaining']}s "
              f"after {cooldown['strikes']} strike(s): {cooldown['reason']}")
        print("             PSX pushed back. Let it expire; do not work around it.")
    else:
        print(f"cooldown     clear ({cooldown['strikes']} recent strike(s))")
    print(f"\nprobing {PROBE_PATH} on each candidate:")
    good = []
    for ip in transport.candidates():
        ok, detail = transport.probe(ip)
        tags = ",".join(
            t for t, cond in (
                ("dns", ip in dns),
                ("remembered", ip in state.get("known", [])),
                ("seed", ip in SEED_NODES),
            ) if cond
        )
        verdict = "OK - serves data routes" if ok else f"NO data routes ({detail})"
        print(f"  {ip:<16} {tags:<18} {verdict}")
        if ok:
            good.append(ip)
    if not good:
        print("\nno usable node found")
        sys.exit(1)
    transport._save_state(good[0], transport.candidates())
    print(f"\npinned {good[0]}")
    if dns and dns[0] not in good:
        print(f"note: DNS is advertising {dns[0]}, which does NOT serve the data\n"
              "      routes. This is the failure mode psx-dps exists to absorb.")


def cmd_cooldown(args):
    from .ratelimit import Breaker

    breaker = Breaker(directory=args.cache_dir)
    status = breaker.status()
    if args.clear:
        breaker._write({})
        print("cooldown cleared.")
        print("Only do this if you know why PSX pushed back and have fixed it "
              "-- clearing it to keep polling is how a warning becomes a block.")
        return
    if status["cooling_down"]:
        print(f"STANDING DOWN for {status['seconds_remaining']}s")
        print(f"strikes  {status['strikes']}")
        print(f"reason   {status['reason']}")
    else:
        print(f"clear ({status['strikes']} recent strike(s))")


def cmd_cache(args):
    from .cache import Cache

    store = Cache(args.cache_dir)
    if args.clear:
        print(f"removed {store.purge()} cache entries from {store.dir}")
    else:
        import os
        root = os.path.join(store.dir, "http")
        try:
            entries = os.listdir(root)
        except OSError:
            entries = []
        size = 0
        for name in entries:
            try:
                size += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
        print(f"cache dir  {store.dir}")
        print(f"entries    {len(entries)}")
        print(f"size       {size / 1024:.0f} KiB")
        print("\nclear it with: psx-dps cache --clear")


# -- parser ----------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(
        prog="psx-dps",
        description="Pakistan Stock Exchange data from dps.psx.com.pk (unofficial)",
    )
    ap.add_argument("--version", action="version", version=f"psx-dps {__version__}")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--csv", action="store_true", help="emit CSV")
    ap.add_argument("--no-cache", action="store_true",
                    help="bypass the shared cache (avoid in loops)")
    ap.add_argument("--cache-dir", help="override the shared cache directory")
    ap.add_argument("--min-interval", type=float, default=1.0,
                    help="seconds between upstream requests (default 1.0)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("symbols", help="the tradable universe")
    p.add_argument("--grep")
    p.add_argument("--sector")
    p.add_argument("--all", action="store_true", help="include debt instruments")
    p.add_argument("--debt-only", action="store_true")
    p.add_argument("--etf-only", action="store_true")
    p.set_defaults(func=cmd_symbols)

    p = sub.add_parser("quote", help="current quote for one or more symbols")
    p.add_argument("symbols", nargs="+")
    p.set_defaults(func=cmd_quote)

    p = sub.add_parser("market", help="the whole market watch")
    p.add_argument("--sector")
    p.add_argument("--index", help="filter by index membership, e.g. KSE100")
    p.add_argument("--sort", choices=["volume", "change", "symbol"], default="volume")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_market)

    p = sub.add_parser("intraday", help="today's tick series")
    p.add_argument("symbol")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_intraday)

    p = sub.add_parser("eod", help="daily bars, about five years")
    p.add_argument("symbol")
    p.add_argument("--since", help="YYYY-MM-DD")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_eod)

    p = sub.add_parser("history", help="official historical table")
    p.add_argument("symbol", nargs="?")
    p.add_argument("--date", help="YYYY-MM-DD: every symbol that day")
    p.add_argument("--month", type=int)
    p.add_argument("--year", type=int)
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("announcements", help="corporate / regulator announcements")
    p.add_argument("--type", choices=sorted(ANNOUNCEMENT_TYPES), default="companies")
    p.add_argument("--symbol")
    p.add_argument("--query")
    p.add_argument("--date-from")
    p.add_argument("--date-to")
    p.add_argument("--count", type=int, default=25)
    p.add_argument("--offset", type=int, default=0)
    p.set_defaults(func=cmd_announcements)

    p = sub.add_parser("movers", help="top volume symbols/sectors and breadth")
    p.set_defaults(func=cmd_movers)

    p = sub.add_parser("indices", help="all index levels")
    p.set_defaults(func=cmd_indices)

    p = sub.add_parser("index", help="constituents of one index")
    p.add_argument("code")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("sectors", help="sector summary")
    p.set_defaults(func=cmd_sectors)

    p = sub.add_parser("doctor", help="probe nodes, show session/cache state")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("cooldown", help="show or clear the shared back-off state")
    p.add_argument("--clear", action="store_true")
    p.set_defaults(func=cmd_cooldown)

    p = sub.add_parser("cache", help="inspect or clear the shared cache")
    p.add_argument("--clear", action="store_true")
    p.set_defaults(func=cmd_cache)

    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except PSXError as exc:
        print(f"psx-dps: {exc}", file=sys.stderr)
        sys.exit(1)
    except BrokenPipeError:
        pass
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
