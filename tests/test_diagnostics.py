"""Unit tests for the stream-failure diagnosis.

Pure logic (account heuristics + URL probe) with the network stubbed, so it
turns an opaque "loading failed" into a specific human reason.
"""
import time

import pytest

from dopeiptv import i18n
from dopeiptv.providers import diagnostics as diag


@pytest.fixture(autouse=True)
def english():
    i18n.set_language("en")
    yield


class _Client:
    def __init__(self, user_info):
        self._info = user_info

    def account_info(self):
        return {"user_info": self._info}


def test_expired_account_reported():
    past = str(int(time.time()) - 86400)
    r = diag._account_reason(_Client({"status": "Active", "exp_date": past}))
    assert r and "expired" in r.lower()


def test_status_not_active_reported():
    r = diag._account_reason(_Client({"status": "Disabled"}))
    assert r and "disabled" in r.lower()


def test_connection_limit_reported():
    r = diag._account_reason(
        _Client({"status": "Active", "active_cons": "1", "max_connections": "1"}))
    assert r and "1/1" in r


def test_healthy_account_gives_no_reason():
    future = str(int(time.time()) + 86400)
    r = diag._account_reason(
        _Client({"status": "Active", "exp_date": future,
                 "active_cons": "0", "max_connections": "2"}))
    assert r is None


def test_no_client_or_no_account_info():
    assert diag._account_reason(None) is None
    assert diag._account_reason(object()) is None


def _fake_get(status=None, exc=None):
    class _Resp:
        status_code = status

        def close(self):
            pass

    def _get(*a, **k):
        if exc is not None:
            raise exc
        return _Resp()
    return _get


def test_probe_forbidden(monkeypatch):
    monkeypatch.setattr(diag.requests, "get", _fake_get(status=403))
    assert "403" in diag._probe_url("http://x/live/1.ts")


def test_probe_not_found(monkeypatch):
    monkeypatch.setattr(diag.requests, "get", _fake_get(status=404))
    assert "404" in diag._probe_url("http://x/live/1.ts")


def test_probe_ok_but_unplayable(monkeypatch):
    monkeypatch.setattr(diag.requests, "get", _fake_get(status=200))
    assert "format" in diag._probe_url("http://x/live/1.ts").lower()


def test_probe_timeout(monkeypatch):
    monkeypatch.setattr(diag.requests, "get",
                        _fake_get(exc=diag.requests.Timeout()))
    assert "timeout" in diag._probe_url("http://x/live/1.ts").lower()


def test_probe_unreachable(monkeypatch):
    monkeypatch.setattr(diag.requests, "get",
                        _fake_get(exc=diag.requests.ConnectionError()))
    assert diag._probe_url("http://x/live/1.ts")


def test_account_reason_wins_over_probe(monkeypatch):
    # A definitive account problem should short-circuit before any URL probe.
    called = {"n": 0}

    def _get(*a, **k):
        called["n"] += 1
        raise AssertionError("probe should not run")
    monkeypatch.setattr(diag.requests, "get", _get)
    r = diag.diagnose_stream(
        "http://x/live/1.ts",
        _Client({"status": "Active", "active_cons": "2", "max_connections": "2"}))
    assert "2/2" in r
    assert called["n"] == 0
