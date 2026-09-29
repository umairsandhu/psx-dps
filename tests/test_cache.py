"""Cache tests: TTL honesty, atomicity, and never failing a call."""

import json
import os
import time

import conftest  # noqa: F401
from psx_dps.cache import Cache


def test_roundtrip_within_ttl(tmp_path):
    cache = Cache(str(tmp_path))
    cache.set("GET", "/symbols", None, "payload")
    assert cache.get("GET", "/symbols", None, ttl=60) == "payload"


def test_expired_entry_is_a_miss(tmp_path):
    cache = Cache(str(tmp_path))
    cache.set("GET", "/symbols", None, "payload")
    time.sleep(0.05)
    assert cache.get("GET", "/symbols", None, ttl=0.01) is None


def test_zero_ttl_never_serves(tmp_path):
    cache = Cache(str(tmp_path))
    cache.set("GET", "/x", None, "payload")
    assert cache.get("GET", "/x", None, ttl=0) is None


def test_body_is_part_of_the_key(tmp_path):
    """Two /historical POSTs differing only in body must not collide."""
    cache = Cache(str(tmp_path))
    cache.set("POST", "/historical", {"date": "2026-09-25"}, "monday")
    cache.set("POST", "/historical", {"date": "2026-09-24"}, "friday")
    assert cache.get("POST", "/historical", {"date": "2026-09-25"}, 60) == "monday"
    assert cache.get("POST", "/historical", {"date": "2026-09-24"}, 60) == "friday"


def test_disabled_cache_stores_nothing(tmp_path):
    cache = Cache(str(tmp_path), enabled=False)
    cache.set("GET", "/x", None, "payload")
    assert cache.get("GET", "/x", None, ttl=60) is None


def test_corrupt_entry_is_a_miss_not_an_error(tmp_path):
    cache = Cache(str(tmp_path))
    cache.set("GET", "/x", None, "payload")
    root = os.path.join(str(tmp_path), "http")
    target = os.path.join(root, os.listdir(root)[0])
    with open(target, "w") as fh:
        fh.write("{ this is not json")
    assert cache.get("GET", "/x", None, ttl=60) is None


def test_unwritable_dir_does_not_raise(tmp_path):
    """A read-only cache must degrade to no caching, never break the call."""
    cache = Cache("/proc/nonexistent-psx-dps")
    cache.set("GET", "/x", None, "payload")   # must not raise
    assert cache.get("GET", "/x", None, ttl=60) is None


def test_shared_dir_is_visible_to_a_second_client(tmp_path):
    """The point of a shared dir: another process reuses the same entry."""
    Cache(str(tmp_path)).set("GET", "/market-watch", None, "table")
    assert Cache(str(tmp_path)).get("GET", "/market-watch", None, 60) == "table"


def test_purge_and_stats(tmp_path):
    cache = Cache(str(tmp_path))
    cache.set("GET", "/a", None, "1")
    cache.set("GET", "/b", None, "2")
    cache.get("GET", "/a", None, 60)
    cache.get("GET", "/missing", None, 60)
    stats = cache.stats()
    assert stats["hits"] == 1 and stats["misses"] == 1
    assert cache.purge() == 2
    assert cache.get("GET", "/a", None, 60) is None
