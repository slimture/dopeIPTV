"""A login in a stream's address, answered as the server asks - basic or
digest - wherever the app fetches the stream itself: the rewind buffer's
ingest used to send only basic (requests' own way), so a Tvheadend channel
behind its default digest login failed there while mpv played it."""
from __future__ import annotations

import time

import pytest

from dopeiptv.core.dvr_ingest import Ingest
from dopeiptv.core.http_login import get_with_login, split_login
from dopeiptv.providers.boxes import with_login
from tests.test_nerd import TS, Recorder, _libmpv, _plays


@pytest.fixture
def server():
    made = []

    def make(auth=None):
        r = Recorder(auth)
        made.append(r)
        return r
    yield make
    for r in made:
        r.close()


def test_split_login():
    assert split_login("http://a%20b:c%40d@h:9981/x?y=1") == ("http://h:9981/x?y=1", "a b", "c@d")
    assert split_login("http://u@[fd00::1]:80/p") == ("http://[fd00::1]:80/p", "u", "")
    assert split_login("http://h/x") is None


@pytest.mark.parametrize("kind", ["basic", "digest"])
def test_each_kind_of_login(server, kind):
    srv = server(auth=(kind, "kodi", "s3 cret"))
    r = get_with_login(with_login(f"http://127.0.0.1:{srv.port}/stream/channel/c1", "kodi", "s3 cret"),
                       timeout=5)
    assert r.status_code == 200 and r.content == TS
    assert get_with_login(with_login(f"http://127.0.0.1:{srv.port}/x", "kodi", "nope"),
                          timeout=5).status_code == 401


def test_no_login_is_a_plain_get(server):
    srv = server()
    assert get_with_login(f"http://127.0.0.1:{srv.port}/a.ts", timeout=5).content == TS
    assert len(srv.requests) == 1


def test_the_rewind_buffer_ingest_opens_a_digest_channel(server):
    srv = server(auth=("digest", "kodi", "s3cret"))
    url = with_login(f"http://127.0.0.1:{srv.port}/stream/channel/c1?profile=pass", "kodi", "s3cret")
    r = Ingest._default_open(url, stream=True, timeout=5, headers={"User-Agent": "dopeIPTV/1.0"})
    assert r.status_code == 200 and r.raw.read() == TS
    assert srv.requests[-1]["headers"]["authorization"].lower().startswith("digest ")


def test_a_digest_channel_plays_through_the_rewind_buffer(server, tmp_path):
    """The whole way: the app's own buffer reads a Tvheadend channel behind
    digest, and a real mpv plays the buffer's local server."""
    from dopeiptv.core.dvr_session import DvrSession
    m = _libmpv()
    srv = server(auth=("digest", "kodi", "s3cret"))
    url = with_login(f"http://127.0.0.1:{srv.port}/stream/channel/c1", "kodi", "s3cret")
    s = DvrSession(url, tmp_path, keep_seconds=120, max_bytes=64 * 1024 * 1024)
    try:
        end = time.monotonic() + 10
        while time.monotonic() < end and not any(
                r["headers"].get("authorization", "").lower().startswith("digest")
                for r in srv.requests):
            time.sleep(0.05)
        assert _plays(m, s.live_url())
    finally:
        m.terminate()
        s.close()
