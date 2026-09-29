"""Cross-process rate limiting and a runaway-loop budget.

An in-process limiter is not enough here. The whole point of this library is
that several projects call it independently -- a cron job, a dashboard, a
notebook -- and each one politely limiting *itself* to 1 req/s still lets
five of them put 5 req/s on PSX. So the state lives in a file under the
shared cache directory and is guarded by an flock, which makes the limit
global to the machine.

Two separate guards:

  Throttle  paces requests, minimum interval between the *starts* of any two
            requests from any process. The lock is released before the HTTP
            call, so a slow response never blocks other callers beyond the
            interval itself.

  Budget    a rolling 24h ceiling on total requests. This is not politeness,
            it is a fuse: a bug that loops forever should raise RateLimited
            locally rather than quietly spend all day hitting PSX.

Windows has no fcntl; there the lock degrades to a no-op and the limiter
still works per-process. That is a documented weakening, not a silent one.
"""

import json
import os
import random
import time

from .errors import RateLimited

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None


class _FileLock:
    """Best-effort advisory lock; a no-op where fcntl is unavailable."""

    def __init__(self, path):
        self.path = path
        self._fh = None

    def __enter__(self):
        if fcntl is None:
            return self
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            self._fh = open(self.path, "a+")
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        except OSError:
            self._fh = None
        return self

    def __exit__(self, *exc):
        if self._fh is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None
        return False


class Throttle:
    """Machine-wide minimum interval between outbound PSX requests."""

    def __init__(self, min_interval=1.0, directory=None, daily_budget=5000):
        self.min_interval = float(min_interval)
        self.daily_budget = int(daily_budget)
        base = os.path.expanduser(directory or "~/.cache/psx-dps")
        self.state_path = os.path.join(base, "throttle.json")
        self.lock_path = os.path.join(base, "throttle.lock")

    def _read(self):
        try:
            with open(self.state_path) as fh:
                state = json.load(fh)
            return state if isinstance(state, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, state):
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            with open(self.state_path, "w") as fh:
                json.dump(state, fh)
        except OSError:
            pass

    def acquire(self):
        """Block until the next request is allowed. Returns seconds waited."""
        waited = 0.0
        while True:
            with _FileLock(self.lock_path):
                state = self._read()
                now = time.time()

                # Rolling 24h fuse.
                if now - state.get("window_start", 0) > 86400:
                    state["window_start"], state["count"] = now, 0
                if self.daily_budget and state.get("count", 0) >= self.daily_budget:
                    raise RateLimited(
                        f"local budget of {self.daily_budget} requests/24h is spent "
                        f"({int(86400 - (now - state['window_start']))}s until reset). "
                        "This is a runaway-loop fuse, not a PSX limit -- raise "
                        "daily_budget if the workload is genuinely this large."
                    )

                gap = now - state.get("last", 0)
                if gap >= self.min_interval:
                    state["last"] = now
                    state["count"] = state.get("count", 0) + 1
                    self._write(state)
                    return waited
                sleep_for = self.min_interval - gap

            # Sleep outside the lock so other processes can queue behind us.
            # Jitter stops several waiters waking in lockstep.
            sleep_for += random.uniform(0, 0.05)
            time.sleep(sleep_for)
            waited += sleep_for

    def spent(self):
        state = self._read()
        if time.time() - state.get("window_start", 0) > 86400:
            return 0
        return state.get("count", 0)


def backoff_delays(attempts, base=0.5, cap=8.0):
    """Exponential backoff with full jitter, for transient failures."""
    for attempt in range(attempts):
        yield random.uniform(0, min(cap, base * (2 ** attempt)))
