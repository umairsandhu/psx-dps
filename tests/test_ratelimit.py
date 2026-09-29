"""Rate limiter tests, including the cross-process guarantee."""

import subprocess
import sys
import textwrap
import time

import conftest  # noqa: F401
import pytest
from psx_dps.errors import RateLimited
from psx_dps.ratelimit import Throttle, backoff_delays


def test_paces_requests(tmp_path):
    throttle = Throttle(min_interval=0.2, directory=str(tmp_path), daily_budget=0)
    start = time.time()
    for _ in range(3):
        throttle.acquire()
    assert time.time() - start >= 0.4


def test_daily_budget_is_a_fuse(tmp_path):
    throttle = Throttle(min_interval=0, directory=str(tmp_path), daily_budget=2)
    throttle.acquire()
    throttle.acquire()
    with pytest.raises(RateLimited):
        throttle.acquire()


def test_budget_of_zero_means_unlimited(tmp_path):
    throttle = Throttle(min_interval=0, directory=str(tmp_path), daily_budget=0)
    for _ in range(50):
        throttle.acquire()
    assert throttle.spent() >= 50


def test_spent_counts_across_instances(tmp_path):
    Throttle(min_interval=0, directory=str(tmp_path), daily_budget=0).acquire()
    assert Throttle(min_interval=0, directory=str(tmp_path)).spent() == 1


@pytest.mark.skipif(sys.platform.startswith("win"), reason="needs fcntl")
def test_limit_holds_across_processes(tmp_path):
    """The reason the state is on disk: two projects must not each get 1/s."""
    script = textwrap.dedent(
        f"""
        import sys, time
        sys.path.insert(0, {str(conftest.FIXTURES + "/../../src")!r})
        from psx_dps.ratelimit import Throttle
        t = Throttle(min_interval=0.3, directory={str(tmp_path)!r}, daily_budget=0)
        for _ in range(3):
            t.acquire()
            print(time.time(), flush=True)
        """
    )
    procs = [
        subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE,
                         text=True)
        for _ in range(3)
    ]
    stamps = sorted(
        float(line) for p in procs for line in p.communicate()[0].splitlines()
    )
    assert len(stamps) == 9
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert min(gaps) >= 0.25, f"requests came {min(gaps):.3f}s apart"


def test_backoff_is_bounded_and_jittered():
    delays = list(backoff_delays(5, base=0.5, cap=4.0))
    assert len(delays) == 5
    assert all(0 <= d <= 4.0 for d in delays)


# -- circuit breaker -------------------------------------------------------

def test_penalty_escalates_then_forgives(tmp_path):
    from psx_dps.ratelimit import Breaker

    breaker = Breaker(directory=str(tmp_path))
    assert breaker.record_failure("429") == 60
    assert breaker.record_failure("429") == 300
    assert breaker.record_failure("429") == 900
    assert breaker.record_failure("429") == 3600
    assert breaker.record_failure("429") == 3600, "should cap, not grow forever"


def test_explicit_retry_after_wins(tmp_path):
    from psx_dps.ratelimit import Breaker

    breaker = Breaker(directory=str(tmp_path))
    assert breaker.record_failure("503", retry_after=1800) == 1800


def test_check_raises_while_cooling_and_clears_after(tmp_path):
    from psx_dps.errors import CircuitOpen
    from psx_dps.ratelimit import Breaker

    breaker = Breaker(directory=str(tmp_path))
    breaker.check()                       # clear: no raise
    breaker.record_failure("429")
    with pytest.raises(CircuitOpen):
        breaker.check()
    assert breaker.status()["cooling_down"] is True


def test_cooldown_is_visible_to_another_process(tmp_path):
    from psx_dps.errors import CircuitOpen
    from psx_dps.ratelimit import Breaker

    Breaker(directory=str(tmp_path)).record_failure("429")
    with pytest.raises(CircuitOpen):
        Breaker(directory=str(tmp_path)).check()


def test_breaker_can_be_disabled(tmp_path):
    from psx_dps.ratelimit import Breaker

    breaker = Breaker(directory=str(tmp_path), enabled=False)
    breaker.record_failure("429")
    breaker.check()                       # must not raise
