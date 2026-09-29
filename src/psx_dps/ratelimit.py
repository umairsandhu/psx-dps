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

from .errors import CircuitOpen, RateLimited

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


class Breaker:
    """A machine-wide cooldown that trips when PSX signals distress.

    The failure mode that gets a client blocked is not steady traffic, it is
    *amplification*: the server starts returning 429s or 503s, every poller
    retries harder, and what was a wobble becomes an outage the operator
    fixes with a firewall rule. So when PSX pushes back we stand down, and
    we do it in shared state -- one project's 429 silences the others on the
    same machine rather than each of them rediscovering it.

    Penalties escalate while the pushback continues and reset after a clean
    run, so a single blip costs a minute and a sustained problem costs an
    hour. Cooldowns are *raised*, not slept through: a scheduled job should
    skip the cycle, not hold a process open for an hour.
    """

    # Escalating stand-down, in seconds, indexed by consecutive strikes.
    PENALTIES = (60, 300, 900, 3600)
    # A clean run this long forgives the accumulated strikes.
    RECOVERY = 900

    def __init__(self, directory=None, enabled=True):
        base = os.path.expanduser(directory or "~/.cache/psx-dps")
        self.enabled = enabled
        self.state_path = os.path.join(base, "cooldown.json")
        self.lock_path = os.path.join(base, "cooldown.lock")

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

    def remaining(self):
        """Seconds left on the current cooldown, 0 if clear."""
        if not self.enabled:
            return 0.0
        return max(0.0, self._read().get("until", 0) - time.time())

    def check(self):
        """Raise CircuitOpen if we are standing down."""
        left = self.remaining()
        if left > 0:
            state = self._read()
            raise CircuitOpen(
                f"standing down for another {int(left)}s after "
                f"{state.get('strikes', 1)} rejection(s) from PSX "
                f"(last: {state.get('reason', 'unknown')}). "
                "This is a deliberate cooldown shared by every psx-dps "
                "process on this machine -- skip this cycle rather than "
                "retrying around it."
            )

    def record_failure(self, reason, retry_after=None):
        """Trip or extend the cooldown. Returns the cooldown in seconds."""
        if not self.enabled:
            return 0.0
        with _FileLock(self.lock_path):
            state = self._read()
            now = time.time()
            # Strikes decay after a clean stretch, so old blips do not
            # make today's first hiccup expensive.
            if now - state.get("last_failure", 0) > self.RECOVERY:
                state["strikes"] = 0
            strikes = min(state.get("strikes", 0) + 1, len(self.PENALTIES))
            penalty = self.PENALTIES[strikes - 1]
            # PSX asking for a specific wait always wins, even if it is long.
            if retry_after is not None:
                penalty = max(penalty, float(retry_after))
            state.update(
                strikes=strikes,
                last_failure=now,
                until=max(state.get("until", 0), now + penalty),
                reason=str(reason),
            )
            self._write(state)
            return penalty

    def record_success(self):
        if not self.enabled:
            return
        state = self._read()
        if not state:
            return
        # Only pay for a write when there is something to clear.
        if state.get("strikes") and time.time() - state.get("last_failure", 0) > self.RECOVERY:
            with _FileLock(self.lock_path):
                state = self._read()
                state["strikes"] = 0
                self._write(state)

    def status(self):
        state = self._read()
        return {
            "cooling_down": self.remaining() > 0,
            "seconds_remaining": int(self.remaining()),
            "strikes": state.get("strikes", 0),
            "reason": state.get("reason"),
        }
