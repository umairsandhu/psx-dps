"""Diagnostics must name the right failure, not just say 'broken'.

The canaries matter most: a PSX payload change that still returns 200 is the
failure mode that silently corrupts a database, so these assert that a
changed shape is *caught*, not merely that a healthy one passes.
"""

import json

import conftest  # noqa: F401
import pytest
from conftest import fixture
from psx_dps import diagnose
from psx_dps.cache import Cache
from psx_dps.ratelimit import Breaker, Throttle
from psx_dps.transport import PROBE_PATH, Transport

GOOD, BAD = "10.0.0.16", "10.0.0.6"


def build(tmp_path, net, candidates=(BAD, GOOD)):
    transport = Transport(
        cache=Cache(str(tmp_path)),
        throttle=Throttle(min_interval=0, directory=str(tmp_path), daily_budget=0),
        breaker=Breaker(directory=str(tmp_path)),
    )
    transport._raw = net
    transport.candidates = lambda: list(candidates)
    transport.dns = lambda: [BAD]
    # No real sockets in tests -- the live TLS path is exercised by
    # `psx-dps diagnose` against the real host, not here.
    transport.verify_tls = lambda ip, timeout=10: (True, "psx.com.pk")
    return transport


def statuses(checks):
    return {c.name: c.status for c in checks}


# -- canaries catch shape changes -----------------------------------------

def test_symbols_canary_accepts_real_payload():
    payload = json.dumps([{"symbol": "MARI", "name": "Mari", "sectorName": "OIL",
                           "isETF": False, "isDebt": False}])
    assert "1 instruments" in diagnose._canary_symbols(payload)


def test_symbols_canary_catches_a_renamed_key():
    payload = json.dumps([{"ticker": "MARI", "name": "Mari"}])
    with pytest.raises(ValueError, match="missing keys"):
        diagnose._canary_symbols(payload)


def test_eod_canary_catches_a_changed_column_count():
    """If PSX ever adds/removes a column, every stored bar would shift."""
    payload = json.dumps({"status": 1, "data": [[1790593200, 635.09, 244578]]})
    with pytest.raises(ValueError, match="expected 4 values"):
        diagnose._canary_eod(payload)


def test_intraday_canary_catches_a_changed_column_count():
    payload = json.dumps({"status": 1, "data": [[1790593200, 630.29]]})
    with pytest.raises(ValueError, match="expected 3 values"):
        diagnose._canary_intraday(payload)


def test_market_watch_canary_accepts_the_real_table():
    assert "symbols" in diagnose._canary_market_watch(fixture("market_watch.html"))


def test_market_watch_canary_catches_dropped_columns():
    html = ('<table><thead><tr><th data-name="symbol">SYMBOL</th>'
            '<th data-name="close">CLOSE</th></tr></thead>'
            '<tbody><tr><td>MARI</td><td>630</td></tr></tbody></table>')
    with pytest.raises(ValueError, match="columns missing"):
        diagnose._canary_market_watch(html)


def test_market_watch_canary_catches_an_unparseable_body():
    with pytest.raises(ValueError, match="no rows"):
        diagnose._canary_market_watch("<div>we redesigned the site</div>")


# -- the chain names the right failure ------------------------------------

def test_reports_healthy_when_everything_works(tmp_path):
    def net(ip, method, path, body, timeout=None, reuse=True):
        return (200, "ok", None) if ip == GOOD else (404, "nope", None)

    checks, _fixed = diagnose.run(build(tmp_path, net), deep=False)
    assert statuses(checks)["node health"] == diagnose.OK


def test_distinguishes_offline_from_psx_being_broken(tmp_path):
    def net(ip, method, path, body, timeout=None, reuse=True):
        raise OSError("Network is unreachable")

    checks, _ = diagnose.run(build(tmp_path, net))
    node = next(c for c in checks if c.name == "node health")
    assert node.status == diagnose.FAIL
    assert "local network" in node.fix.lower()


def test_flags_renumbering_when_nodes_answer_but_serve_no_data(tmp_path):
    def net(ip, method, path, body, timeout=None, reuse=True):
        return 404, "nope", None

    checks, _ = diagnose.run(build(tmp_path, net))
    node = next(c for c in checks if c.name == "node health")
    assert node.status == diagnose.FAIL
    assert "renumbered" in node.fix.lower()


def test_repins_automatically_when_the_pin_is_stale(tmp_path):
    def net(ip, method, path, body, timeout=None, reuse=True):
        return (200, "ok", None) if ip == GOOD else (404, "nope", None)

    transport = build(tmp_path, net)
    transport._save_state(BAD)              # pinned to the broken node
    checks, fixed = diagnose.run(transport)
    assert any("re-pinned" in f for f in fixed), fixed
    assert transport.pinned_node() == GOOD


def test_surfaces_an_active_cooldown_as_a_warning(tmp_path):
    def net(ip, method, path, body, timeout=None, reuse=True):
        return (200, "ok", None) if ip == GOOD else (404, "nope", None)

    transport = build(tmp_path, net)
    transport.breaker.record_failure("HTTP 429 on /market-watch")
    checks, _ = diagnose.run(transport)
    cooldown = next(c for c in checks if c.name == "cooldown")
    assert cooldown.status == diagnose.WARN
    assert "429" in cooldown.detail


def test_deep_run_reports_a_shape_change_as_a_named_failure(tmp_path):
    """PSX returns 200 with a redesigned body -> must fail loudly."""
    def net(ip, method, path, body, timeout=None, reuse=True):
        if path == PROBE_PATH:
            return 200, '[{"name":"ADV","value":0.5}]', None
        if path == "/symbols":
            return 200, '[{"ticker":"MARI"}]', None      # key renamed
        return 200, "<div>redesigned</div>", None

    checks, _ = diagnose.run(build(tmp_path, net), deep=True)
    symbols = next(c for c in checks if c.name == "GET  /symbols")
    assert symbols.status == diagnose.FAIL
    assert "shape changed" in symbols.detail
    assert "parser needs updating" in symbols.fix
