"""The loopback server mpv plays the app-kept buffer from: a token in every
path, loopback only, a live stream that follows the store."""
from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request

import pytest

from dopeiptv.core.dvr_server import LocalServer
from dopeiptv.core.dvr_store import SegmentStore


@pytest.fixture
def served(tmp_path):
    s = SegmentStore(tmp_path / "s", 60.0, 10**9, min_free=0)
    s.append(b"A" * 188 * 4)
    srv = LocalServer(s)
    yield s, srv
    s.close()
    srv.close()


def test_loopback_only_with_a_token(served):
    s, srv = served
    assert srv._srv.server_address[0] == "127.0.0.1"
    assert len(srv.token) >= 20
    bad = f"http://127.0.0.1:{srv.port}/not-the-token/s/0.ts"
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(bad, timeout=5)
    assert e.value.code == 404
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/", timeout=5)


def test_serves_the_bytes_and_follows_new_ones(served):
    s, srv = served

    def later():
        time.sleep(0.3)
        s.append(b"B" * 188)
        time.sleep(0.3)
        s.new_run()                       # ends what this request may read

    threading.Thread(target=later, daemon=True).start()
    with urllib.request.urlopen(srv.url(0), timeout=10) as r:
        assert r.headers["Content-Type"] == "video/mp2t"
        assert r.headers.get("Content-Length") is None
        assert r.read() == b"A" * 188 * 4 + b"B" * 188
    assert srv.last_opened == 0


def test_a_missing_segment_is_a_404(served):
    _s, srv = served
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(srv.url(99), timeout=5)
    assert e.value.code == 404


def test_live_joins_a_few_seconds_back_and_says_where(tmp_path):
    clock = [1000.0]
    s = SegmentStore(tmp_path / "live", 60.0, 10**9, min_free=0,
                     clock=lambda: clock[0])
    for i in range(6):
        s.append(bytes([65 + i]) * 188)
        clock[0] += 2.5
    srv = LocalServer(s)
    try:
        def end():
            time.sleep(0.3)
            s.new_run()
        threading.Thread(target=end, daemon=True).start()
        with urllib.request.urlopen(srv.live_url(), timeout=10) as r:
            body = r.read()
        # The newest byte is at 1012.5; four seconds back is in seq 3.
        assert srv.last_opened == 3 and srv.last_opened_t0 == 1007.5
        assert body == b"D" * 188 + b"E" * 188 + b"F" * 188
    finally:
        s.close()
        srv.close()


def test_a_hung_up_player_prints_no_traceback(served, capsys):
    # mpv often hangs up before a live request is answered (the channel was
    # changed while it waited for the provider): the 404 then went to a closed
    # socket, and socketserver printed a BrokenPipeError traceback.
    import socket as _socket
    s, srv = served
    c = _socket.create_connection(("127.0.0.1", srv.port), timeout=5)
    c.sendall(b"GET /wrong-token/s/0.ts HTTP/1.0\r\n\r\n")
    c.close()                                           # gone before the 404
    time.sleep(0.3)
    srv._srv.handle_error(None, ("127.0.0.1", 0))      # outside an exception
    try:
        raise BrokenPipeError(32, "Broken pipe")
    except BrokenPipeError:
        srv._srv.handle_error(None, ("127.0.0.1", 0))
    err = capsys.readouterr().err
    assert "Traceback" not in err and "BrokenPipeError" not in err, err
