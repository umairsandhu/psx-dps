"""A small on-disk response cache, shared between processes.

PSX sends `Cache-Control: no-store` and no ETag or Last-Modified on any data
route, so there is nothing to revalidate against and HTTP-level caching is
impossible. If we want to be light on their servers -- and we do, since this
library is meant to be called on the fly by several projects -- the cache has
to live here.

The cache directory is shared on purpose. Five of your projects asking for
the market watch inside a minute should cost PSX one request, not five, and
that only works if they all point at the same directory (the default,
~/.cache/psx-dps, does).

Entries are written atomically (tempfile + rename) so a reader never sees a
half-written file, and a corrupt or unreadable entry is treated as a miss
rather than an error -- a cache must never be the reason a call fails.
"""

import hashlib
import json
import os
import tempfile
import time

DEFAULT_DIR = os.path.expanduser(
    os.environ.get("PSX_DPS_CACHE_DIR") or "~/.cache/psx-dps"
)


def _key(method, path, body=None):
    raw = json.dumps([method, path, body or {}], sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()[:32]


class Cache:
    """TTL cache keyed on (method, path, body)."""

    def __init__(self, directory=None, enabled=True):
        self.dir = os.path.expanduser(directory or DEFAULT_DIR)
        self.enabled = enabled
        self.hits = 0
        self.misses = 0

    def _path(self, key):
        return os.path.join(self.dir, "http", f"{key}.json")

    def get(self, method, path, body=None, ttl=0):
        """Return a cached body, or None when absent/expired/unreadable."""
        if not self.enabled or ttl <= 0:
            self.misses += 1
            return None
        try:
            with open(self._path(_key(method, path, body))) as fh:
                entry = json.load(fh)
            if time.time() - entry["ts"] <= ttl:
                self.hits += 1
                return entry["body"]
        except (OSError, ValueError, KeyError):
            pass  # a bad entry is a miss, never an error
        self.misses += 1
        return None

    def set(self, method, path, body, text):
        if not self.enabled:
            return
        target = self._path(_key(method, path, body))
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target))
            with os.fdopen(fd, "w") as fh:
                json.dump({"ts": time.time(), "body": text}, fh)
            os.replace(tmp, target)  # atomic: readers see old or new, never half
        except OSError:
            pass  # a read-only cache dir must not break the call

    def age(self, method, path, body=None):
        """Seconds since this entry was stored, or None if absent."""
        try:
            with open(self._path(_key(method, path, body))) as fh:
                return time.time() - json.load(fh)["ts"]
        except (OSError, ValueError, KeyError):
            return None

    def purge(self, older_than=0):
        """Delete cache entries; returns how many went. 0 clears everything."""
        removed = 0
        root = os.path.join(self.dir, "http")
        try:
            names = os.listdir(root)
        except OSError:
            return 0
        for name in names:
            full = os.path.join(root, name)
            try:
                if older_than and time.time() - os.path.getmtime(full) < older_than:
                    continue
                os.remove(full)
                removed += 1
            except OSError:
                pass
        return removed

    def stats(self):
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": (self.hits / total) if total else 0.0,
            "dir": self.dir,
        }
