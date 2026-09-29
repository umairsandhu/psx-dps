"""Transport tests with a fake network.

These encode the core discovery: some PSX nodes 404 every data route while
serving HTML pages fine, and DNS will hand you one of those. Nothing here
touches the real internet.
"""

import conftest  # noqa: F401
import pytest
from psx_dps.cache import Cache
from psx_dps.errors import NoHealthyNode, TransportError, UpstreamError
from psx_dps.ratelimit import Throttle
from psx_dps.transport import PROBE_PATH, Transport

GOOD = "10.0.0.16"
BAD = "10.0.0.6"


class FakeNet:
    """A node pool where only `good` serves data routes."""

    def __init__(self, good=(GOOD,), fail_times=0):
        self.good = set(good)
        self.fail_times = fail_times
        self.calls = []

    def __call__(self, ip, method, path, body, timeout=None, reuse=True):
        self.calls.append((ip, path))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise OSError("connection reset")
        if ip in self.good:
            return 200, f"payload from {ip}"
        return 404, "<html>Not Found</html>"


def build(tmp_path, net, candidates=(BAD, GOOD), **kw):
    transport = Transport(
        cache=Cache(str(tmp_path)),
        throttle=Throttle(min_interval=0, directory=str(tmp_path), daily_budget=0),
        **kw,
    )
    transport._raw = net
    transport.candidates = lambda: list(candidates)
    return transport


def test_skips_the_node_that_404s_data_routes(tmp_path):
    net = FakeNet()
    transport = build(tmp_path, net)
    assert transport.select_node() == GOOD
    assert (BAD, PROBE_PATH) in net.calls, "should have probed the bad node first"


def test_request_succeeds_despite_dns_pointing_at_the_bad_node(tmp_path):
    transport = build(tmp_path, FakeNet())
    assert transport.request("/symbols", ttl=0) == f"payload from {GOOD}"


def test_pinned_node_going_bad_triggers_a_reprobe(tmp_path):
    """A node can stop serving data mid-session; a 404 must not be believed."""
    net = FakeNet()
    transport = build(tmp_path, net)
    transport._node = BAD          # pretend we were pinned to the bad one
    assert transport.request("/symbols", ttl=0) == f"payload from {GOOD}"


def test_no_healthy_node_raises_clearly(tmp_path):
    transport = build(tmp_path, FakeNet(good=()))
    with pytest.raises(NoHealthyNode) as excinfo:
        transport.select_node()
    assert "PSX_NODE" in str(excinfo.value)


def test_transient_connection_error_is_retried(tmp_path):
    transport = build(tmp_path, FakeNet(fail_times=1), retries=3)
    assert transport.request("/symbols", ttl=0) == f"payload from {GOOD}"


def test_gives_up_after_retries(tmp_path):
    """Health probe fine, but this one path keeps dying -> TransportError."""

    def net(ip, method, path, body, timeout=None, reuse=True):
        if path == PROBE_PATH:
            return 200, "ok"
        raise OSError("connection reset")

    transport = build(tmp_path, net, retries=2)
    with pytest.raises(TransportError):
        transport.request("/symbols", ttl=0)


def test_being_offline_does_not_blame_psx(tmp_path):
    """Every node unreachable is a local problem; the message must say so."""

    def net(ip, method, path, body, timeout=None, reuse=True):
        raise OSError("Network is unreachable")

    transport = build(tmp_path, net)
    with pytest.raises(NoHealthyNode) as excinfo:
        transport.select_node()
    message = str(excinfo.value)
    assert "network" in message.lower()
    assert "renumbered" not in message


def test_cache_prevents_a_second_upstream_call(tmp_path):
    net = FakeNet()
    transport = build(tmp_path, net)
    transport.request("/symbols", ttl=60)
    before = len(net.calls)
    transport.request("/symbols", ttl=60)
    assert len(net.calls) == before, "second call should have been served locally"


def test_force_refresh_bypasses_the_cache(tmp_path):
    net = FakeNet()
    transport = build(tmp_path, net)
    transport.request("/symbols", ttl=60)
    before = len(net.calls)
    transport.request("/symbols", ttl=60, force_refresh=True)
    assert len(net.calls) > before


def test_remembers_good_nodes_for_next_time(tmp_path):
    """The rotating A record may hide the good node on a later run."""
    build(tmp_path, FakeNet()).select_node()
    assert GOOD in Transport(cache=Cache(str(tmp_path)))._state()["known"]


def test_unexpected_status_raises_upstream_error(tmp_path):
    def net(ip, method, path, body, timeout=None, reuse=True):
        return (200, "ok") if path == PROBE_PATH else (403, "denied")

    transport = build(tmp_path, net)
    with pytest.raises(UpstreamError):
        transport.request("/symbols", ttl=0)
