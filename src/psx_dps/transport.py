"""HTTP transport: node selection, connection reuse, throttling, retries.

The node problem is the reason this module is not thirty lines of requests.
See docs/DISCOVERY.md for the full story; the short version is that
dps.psx.com.pk is served by several nodes, some of which answer 404 for every
JSON/XHR data route while serving ordinary HTML pages with 200 -- and the
zone publishes a single rotating A record, so DNS will happily hand you only
a broken one. We keep a candidate pool, probe it for a node that actually
serves data, pin the winner, and re-probe when a pinned node starts 404ing.

Connections are kept alive (PSX supports it; verified) so a burst of calls
costs one TLS handshake instead of N.
"""

import gzip
import http.client
import json
import os
import socket
import ssl
import threading
import time
import zlib

from .cache import Cache
from .errors import (
    CoolingDown,
    NoHealthyNode,
    TransportError,
    UpstreamError,
)
from .ratelimit import Breaker, Throttle, backoff_delays

HOST = "dps.psx.com.pk"
USER_AGENT = os.environ.get(
    "PSX_DPS_USER_AGENT",
    "psx-dps/0.1 (+https://github.com/umairsandhu/psx-dps)",
)
# Smallest data-only route we know of (~180 bytes) -- an ideal health probe.
PROBE_PATH = "/data/symbol-position"
# Addresses observed serving data, tried when the rotating A record hides one.
SEED_NODES = ["52.128.23.16", "52.128.23.6"]
NODE_TTL = 6 * 3600


class Transport:
    def __init__(
        self,
        cache=None,
        throttle=None,
        timeout=30.0,
        retries=3,
        state_dir=None,
        user_agent=None,
        breaker=None,
    ):
        self.cache = cache if cache is not None else Cache()
        self.throttle = throttle if throttle is not None else Throttle()
        self.breaker = breaker if breaker is not None else Breaker(
            directory=self.cache.dir if cache is not None else None
        )
        self.timeout = timeout
        self.retries = max(1, int(retries))
        self.user_agent = user_agent or USER_AGENT
        self.state_dir = os.path.expanduser(state_dir or self.cache.dir)
        self.node_path = os.path.join(self.state_dir, "node.json")
        self._node = None
        self._conn = None
        self._lock = threading.RLock()
        self.requests_made = 0
        self.bytes_downloaded = 0

    # -- node bookkeeping ------------------------------------------------

    def _state(self):
        try:
            with open(self.node_path) as fh:
                state = json.load(fh)
            return state if isinstance(state, dict) else {}
        except (OSError, ValueError):
            return {}

    def record_observation(self, ip, serves_data):
        """Keep a dated record of what each address was doing.

        This is what makes "when did the IP change?" answerable after the
        fact. Without it, a renumbering is invisible until everything breaks
        and nobody can say what the old address was or when it stopped
        working. Bounded by the number of addresses ever seen, so it cannot
        grow without limit.
        """
        now = time.time()
        state = self._state()
        history = state.get("history", {})
        entry = history.get(ip) or {"first_seen": now, "last_ok": None}
        entry["last_seen"] = now
        entry["serves_data"] = bool(serves_data)
        if serves_data:
            entry["last_ok"] = now
        history[ip] = entry
        state["history"] = history
        try:
            os.makedirs(os.path.dirname(self.node_path), exist_ok=True)
            with open(self.node_path, "w") as fh:
                json.dump(state, fh)
        except OSError:
            pass

    def history(self):
        return self._state().get("history", {})

    def _save_state(self, ip, candidates=()):
        state = self._state()
        known = state.get("known", [])
        for cand in list(candidates) + [ip]:
            if cand and cand not in known:
                known.append(cand)
        try:
            os.makedirs(os.path.dirname(self.node_path), exist_ok=True)
            with open(self.node_path, "w") as fh:
                json.dump({"ip": ip, "at": time.time(), "known": known}, fh)
        except OSError:
            pass

    @staticmethod
    def dns():
        try:
            infos = socket.getaddrinfo(HOST, 443, socket.AF_INET, socket.SOCK_STREAM)
        except socket.gaierror:
            return []
        out = []
        for info in infos:
            ip = info[4][0]
            if ip not in out:
                out.append(ip)
        return out

    def candidates(self):
        """Nodes to try, best guess first: env pin, DNS, remembered, seeds."""
        env = os.environ.get("PSX_NODE")
        out = []
        for ip in (
            ([env] if env else [])
            + self.dns()
            + self._state().get("known", [])
            + SEED_NODES
        ):
            if ip and ip not in out:
                out.append(ip)
        return out

    def pinned_node(self):
        state = self._state()
        if time.time() - state.get("at", 0) > NODE_TTL:
            return None
        return state.get("ip")

    def probe(self, ip):
        """Does this node serve the data routes? Returns (ok, status_or_err)."""
        try:
            status = self._raw(ip, "GET", PROBE_PATH, None, timeout=12,
                               reuse=False)[0]
            self.record_observation(ip, status == 200)
            return status == 200, status
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            self.record_observation(ip, False)
            return False, exc

    def verify_tls(self, ip, timeout=10):
        """Confirm the cert is valid for HOST even though we dial an IP.

        Pinning by address must never mean trusting the address: the TLS
        handshake still uses SNI for the real hostname, so a wrong or
        hijacked node fails here rather than silently serving us data.
        """
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((ip, 443), timeout=timeout) as raw:
                with ctx.wrap_socket(raw, server_hostname=HOST) as tls:
                    cert = tls.getpeercert()
            subject = dict(x[0] for x in cert["subject"])
            return True, subject.get("commonName", HOST)
        except (OSError, ssl.SSLError, KeyError, TypeError) as exc:
            return False, exc

    def select_node(self, force=False, report=None):
        with self._lock:
            if not force:
                pinned = self.pinned_node()
                if pinned:
                    self._node = pinned
                    return pinned
            tried = self.candidates()
            unreachable = 0
            for ip in tried:
                ok, detail = self.probe(ip)
                if report:
                    report(ip, ok, detail)
                if ok:
                    self._save_state(ip, tried)
                    self._set_node(ip)
                    return ip
                if isinstance(detail, Exception):
                    unreachable += 1
            # Two very different failures wear the same exception, so say
            # which one happened: "every node refused to connect" is almost
            # always a local network problem, not PSX changing its addresses.
            if tried and unreachable == len(tried):
                raise NoHealthyNode(
                    f"could not connect to any of {', '.join(tried)} on port 443. "
                    "This usually means no network, a proxy, or a firewall -- "
                    f"not a {HOST} problem."
                )
            raise NoHealthyNode(
                f"no {HOST} node served {PROBE_PATH}; tried "
                f"{', '.join(tried) or 'nothing'}. PSX may have renumbered its "
                "nodes. Force one with PSX_NODE=<ip>, or run `psx-dps doctor`."
            )

    def _set_node(self, ip):
        if ip != self._node:
            self._close()
            self._node = ip

    # -- connection handling ---------------------------------------------

    def _close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None

    def _open(self, ip, timeout):
        """TLS to a pinned IP, still validated against the real hostname."""
        ctx = ssl.create_default_context()
        sock = socket.create_connection((ip, 443), timeout=timeout)
        conn = http.client.HTTPSConnection(HOST, timeout=timeout)
        conn.sock = ctx.wrap_socket(sock, server_hostname=HOST)
        return conn

    @staticmethod
    def _retry_after(resp):
        """Seconds PSX asked us to wait, if it said so. Supports both forms."""
        raw = resp.getheader("Retry-After")
        if not raw:
            return None
        raw = raw.strip()
        if raw.isdigit():
            return float(raw)
        try:  # HTTP-date form
            from email.utils import parsedate_to_datetime

            target = parsedate_to_datetime(raw)
            import datetime as _dt

            now = _dt.datetime.now(tz=target.tzinfo)
            return max(0.0, (target - now).total_seconds())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _decode(resp, raw):
        """Inflate the body if PSX compressed it."""
        encoding = (resp.getheader("Content-Encoding") or "").lower()
        try:
            if "gzip" in encoding:
                raw = gzip.decompress(raw)
            elif "deflate" in encoding:
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        except (OSError, zlib.error):
            pass  # not actually compressed; fall through to the raw bytes
        return raw.decode("utf-8", "replace")

    def _raw(self, ip, method, path, body, timeout=None, reuse=True):
        """One HTTP round trip. Returns (status, text)."""
        timeout = timeout or self.timeout
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            # PSX gzips on request and the win is large -- the market watch
            # drops from ~476 KB to ~60 KB. At polling cadences that is the
            # difference between tens of megabytes a day and a few.
            "Accept-Encoding": "gzip, deflate",
            "Referer": f"https://{HOST}/",
        }
        payload = None
        if body is not None:
            from urllib.parse import urlencode

            payload = urlencode(body).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        if not reuse:
            conn = self._open(ip, timeout)
            try:
                conn.request(method, path, body=payload, headers=headers)
                resp = conn.getresponse()
                return (resp.status, self._decode(resp, resp.read()),
                        self._retry_after(resp))
            finally:
                conn.close()

        with self._lock:
            if self._conn is None:
                self._conn = self._open(ip, timeout)
            try:
                self._conn.request(method, path, body=payload, headers=headers)
                resp = self._conn.getresponse()
                text = self._decode(resp, resp.read())
            except (http.client.HTTPException, OSError):
                # A kept-alive socket the server has since dropped: one clean
                # retry on a fresh connection before treating it as an error.
                self._close()
                self._conn = self._open(ip, timeout)
                self._conn.request(method, path, body=payload, headers=headers)
                resp = self._conn.getresponse()
                text = self._decode(resp, resp.read())
            if resp.will_close:
                self._close()
            return resp.status, text, self._retry_after(resp)

    # -- public API --------------------------------------------------------

    def request(self, path, body=None, ttl=0, force_refresh=False):
        """Fetch `path`, honouring the cache, throttle, retries and node pool."""
        method = "POST" if body is not None else "GET"
        if not force_refresh:
            cached = self.cache.get(method, path, body, ttl)
            if cached is not None:
                return cached

        # Stand down before spending a request if PSX pushed back recently.
        self.breaker.check()

        node = self._node or self.select_node()
        last_error = None
        reprobed = False

        for delay in backoff_delays(self.retries):
            if delay:
                time.sleep(delay)
            self.throttle.acquire()
            try:
                status, text, retry_after = self._raw(node, method, path, body)
                self.requests_made += 1
                self.bytes_downloaded += len(text)
            except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
                last_error = exc
                self._close()
                if not reprobed:
                    node, reprobed = self.select_node(force=True), True
                continue

            if status == 200:
                self.breaker.record_success()
                if ttl > 0:
                    self.cache.set(method, path, body, text)
                return text

            if status == 404 and not reprobed:
                # Overwhelmingly this is the wrong-node failure, not a real
                # 404 -- re-probe the pool once before believing it.
                node, reprobed = self.select_node(force=True), True
                last_error = UpstreamError(f"{path}: HTTP 404 on node {node}")
                continue

            if status in (429, 503):
                # An explicit "you are asking too much". Do not burn the
                # remaining retries on it -- stand down so the next poll,
                # and every other project on this machine, backs off too.
                waited = self.breaker.record_failure(f"HTTP {status} on {path}",
                                                     retry_after)
                raise CoolingDown(
                    f"{path}: PSX returned HTTP {status}. Standing down for "
                    f"{int(waited)}s across all psx-dps processes here."
                )

            if status in (500, 502, 504):
                last_error = UpstreamError(f"{path}: HTTP {status}")
                continue

            raise UpstreamError(f"{path}: HTTP {status}")

        # Repeated failure is itself a signal; cool down rather than let the
        # next scheduled run walk straight back into it.
        self.breaker.record_failure(f"{self.retries} failed attempts on {path}")
        raise TransportError(
            f"{path} failed after {self.retries} attempts: {last_error}"
        )

    def close(self):
        with self._lock:
            self._close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
