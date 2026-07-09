"""End-to-end cover-loading flow tests.

Covers the whole 'movie row in the list gets a poster on it' pipeline
against mocked TmdbClient + network so regressions in the resolver /
delegate feedback loop stop shipping. Named scenarios match how the
user experiences failure modes:

  - 'has TMDB match' -> TMDB poster URL is used
  - 'no TMDB match'  -> provider stream_icon is used
  - 'TMDB errors'    -> same as 'no match' (row can't stay on placeholder)
  - 'pending'        -> row shows nothing (no premature fallback)
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest


def _pool():
    """A real QThreadPool - single-worker so tests are deterministic."""
    from PyQt6.QtCore import QThreadPool
    p = QThreadPool()
    p.setMaxThreadCount(1)
    return p


def _valid_png_bytes() -> bytes:
    """A minimal but genuinely-decodeable 4x4 red PNG built through Qt
    itself so QImage.loadFromData accepts it in the tests."""
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
    from PyQt6.QtGui import QImage
    img = QImage(4, 4, QImage.Format.Format_RGB32)
    img.fill(0xff0000)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def _drain_pool(pool, app, timeout_ms: int = 2000) -> None:
    """Run the Qt event loop until every pool worker has finished and
    every queued signal callback has been delivered on the main thread."""
    import time as _time
    deadline = _time.monotonic() + timeout_ms / 1000
    while _time.monotonic() < deadline:
        pool.waitForDone(50)
        app.processEvents()
        if pool.activeThreadCount() == 0:
            # Give queued signals one last chance to land on the main
            # thread even though the worker is done.
            for _ in range(5):
                app.processEvents()
            return
    raise RuntimeError("pool did not drain in time")


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture
def settings(tmp_path):
    from PyQt6.QtCore import QSettings
    return QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)


# -- PosterResolver: happy / no-match / error paths --------------------------


def test_get_full_returns_none_while_pending(qapp, settings):
    """A first call for an unresolved title kicks the fetch and returns
    None; the callback runs once the fetch delivers."""
    from dopeiptv.metadata import PosterResolver
    client = MagicMock()
    client.fetch_details.return_value = {
        "tmdb_id": 1, "poster_url": "https://tmdb/poster.jpg"}
    r = PosterResolver(_pool(), settings, client)

    got = []
    result = r.get_full("The Matrix", "vod", lambda d: got.append(d))
    assert result is None, "first call returns None while resolving"
    _drain_pool(r.pool, qapp)
    assert len(got) == 1
    assert got[0]["poster_url"] == "https://tmdb/poster.jpg"
    assert r.is_resolved("The Matrix", "vod") is True


def test_get_full_returns_cached_dict_second_call(qapp, settings):
    from dopeiptv.metadata import PosterResolver
    client = MagicMock()
    client.fetch_details.return_value = {"tmdb_id": 42}
    r = PosterResolver(_pool(), settings, client)

    r.get_full("Foo", "vod", lambda d: None)
    _drain_pool(r.pool, qapp)
    result = r.get_full("Foo", "vod", lambda d: None)
    assert result == {"tmdb_id": 42}
    # We didn't re-fetch.
    assert client.fetch_details.call_count == 1


def test_get_full_no_match_still_resolves(qapp, settings):
    """A title with no TMDB match caches an empty dict and calls
    the pending callbacks with it. Without this, delegate stays on
    the placeholder forever."""
    from dopeiptv.metadata import PosterResolver
    client = MagicMock()
    client.fetch_details.return_value = None  # no match
    r = PosterResolver(_pool(), settings, client)

    got = []
    r.get_full("Sport Rerun 2019", "vod", lambda d: got.append(d))
    _drain_pool(r.pool, qapp)
    assert len(got) == 1
    assert got[0] == {}
    assert r.is_resolved("Sport Rerun 2019", "vod") is True


def test_get_full_client_error_fires_callback(qapp, settings):
    """The regression that shipped in bcc7a05: a TMDB request that
    raised was clearing self._waiting without notifying anyone, so
    the delegate never learned to fall back to the provider cover.
    Fixed in 5bafc16 - stays fixed here."""
    from dopeiptv.metadata import PosterResolver
    client = MagicMock()
    client.fetch_details.side_effect = RuntimeError("upstream 503")
    r = PosterResolver(_pool(), settings, client)

    got = []
    r.get_full("Failing Title", "vod", lambda d: got.append(d))
    _drain_pool(r.pool, qapp)
    assert len(got) == 1, "fail() must call waiting callbacks"
    assert got[0] == {}
    assert r.is_resolved("Failing Title", "vod") is True


def test_multiple_callers_all_notified(qapp, settings):
    """Two independent callers subscribe to the same title while the
    fetch is in flight; both fire once the fetch resolves."""
    from dopeiptv.metadata import PosterResolver
    client = MagicMock()
    client.fetch_details.return_value = {"tmdb_id": 7}
    r = PosterResolver(_pool(), settings, client)

    got_a, got_b = [], []
    r.get_full("Dune", "vod", lambda d: got_a.append(d))
    r.get_full("Dune", "vod", lambda d: got_b.append(d))
    _drain_pool(r.pool, qapp)
    assert len(got_a) == 1 and len(got_b) == 1
    # Only one network call was made.
    assert client.fetch_details.call_count == 1


def test_get_returns_url_and_callback_wrapper(qapp, settings):
    """PosterResolver.get() is the delegate's entry point - it wraps
    the full-details callback so the delegate only sees the URL."""
    from dopeiptv.metadata import PosterResolver
    client = MagicMock()
    client.fetch_details.return_value = {
        "tmdb_id": 1, "poster_url": "https://tmdb/x.jpg"}
    r = PosterResolver(_pool(), settings, client)

    got = []
    result = r.get("X", "vod", lambda url: got.append(url))
    assert result is None
    _drain_pool(r.pool, qapp)
    assert got == ["https://tmdb/x.jpg"]

    # Now cached - synchronous return.
    got.clear()
    result = r.get("X", "vod", lambda url: got.append(url))
    assert result == "https://tmdb/x.jpg"
    assert got == []  # cache hit doesn't invoke callback


def test_tmdb_id_for_and_is_resolved_reflect_cache(qapp, settings):
    from dopeiptv.metadata import PosterResolver
    client = MagicMock()
    client.fetch_details.return_value = {
        "tmdb_id": 999, "poster_url": "https://x/y.jpg"}
    r = PosterResolver(_pool(), settings, client)

    assert r.is_resolved("Foo", "vod") is False
    assert r.tmdb_id_for("Foo", "vod") is None
    r.get_full("Foo", "vod", lambda d: None)
    _drain_pool(r.pool, qapp)
    assert r.is_resolved("Foo", "vod") is True
    assert r.tmdb_id_for("Foo", "vod") == 999


def test_is_resolved_true_after_error(qapp, settings):
    from dopeiptv.metadata import PosterResolver
    client = MagicMock()
    client.fetch_details.side_effect = RuntimeError("nope")
    r = PosterResolver(_pool(), settings, client)

    assert r.is_resolved("Bad", "vod") is False
    r.get_full("Bad", "vod", lambda d: None)
    _drain_pool(r.pool, qapp)
    assert r.is_resolved("Bad", "vod") is True, (
        "errored fetches must mark the entry resolved-with-no-match "
        "so the delegate can fall back to stream_icon")


# -- LogoLoader: disk cache, dead-URL blacklist ------------------------------


def test_logo_loader_disk_cache_roundtrip(qapp, tmp_path, monkeypatch):
    """A URL fetched once is written to disk, evicted from RAM, then
    served straight from disk on the next request - no second network
    call."""
    from dopeiptv.workers import LogoLoader
    from PyQt6.QtCore import QThreadPool

    red_png = _valid_png_bytes()

    calls = {"n": 0}

    class FakeResp:
        status_code = 200
        content = red_png
        def raise_for_status(self): pass

    def fake_get(url, headers=None, timeout=None):
        calls["n"] += 1
        return FakeResp()

    monkeypatch.setattr("dopeiptv.workers.requests.get", fake_get)

    pool = _pool()
    loader = LogoLoader(pool, max_size=16, cache_dir=tmp_path)
    got = []
    loader.get("https://cdn/x.jpg", lambda pm: got.append(pm))
    _drain_pool(pool, qapp)
    assert len(got) == 1 and not got[0].isNull()
    assert calls["n"] == 1

    # Drop RAM cache to force disk hit.
    loader.cache.clear()
    got.clear()
    loader.get("https://cdn/x.jpg", lambda pm: got.append(pm))
    _drain_pool(pool, qapp)
    assert len(got) == 1 and not got[0].isNull()
    assert calls["n"] == 1, "second load served from disk, no network"


def test_logo_loader_corrupt_disk_file_recovers(qapp, tmp_path, monkeypatch):
    """A truncated cache file (previous crash mid-write) gets
    unlinked and refetched from the network instead of poisoning
    the loader with a broken pixmap forever."""
    from dopeiptv.workers import LogoLoader
    import hashlib

    red_png = _valid_png_bytes()

    class FakeResp:
        status_code = 200
        content = red_png
        def raise_for_status(self): pass

    monkeypatch.setattr(
        "dopeiptv.workers.requests.get", lambda u, headers=None, timeout=None: FakeResp())

    pool = _pool()
    loader = LogoLoader(pool, max_size=16, cache_dir=tmp_path)
    # Pre-plant a corrupt file at the disk-cache location.
    url = "https://cdn/bad.jpg"
    h = hashlib.sha1(url.encode()).hexdigest()
    dp = tmp_path / h[:2] / h[2:]
    dp.parent.mkdir(parents=True, exist_ok=True)
    dp.write_bytes(b"not-a-png")

    got = []
    loader.get(url, lambda pm: got.append(pm))
    _drain_pool(pool, qapp)
    assert len(got) == 1 and not got[0].isNull()
    # Corrupt file was replaced with the freshly-fetched bytes.
    assert dp.exists()
    assert dp.read_bytes() == red_png


def test_logo_loader_marks_404_dead_long_ttl(qapp, tmp_path, monkeypatch):
    from dopeiptv.workers import LogoLoader

    class Resp404:
        status_code = 404
        content = b""
        def raise_for_status(self):
            raise RuntimeError("404")

    monkeypatch.setattr(
        "dopeiptv.workers.requests.get", lambda u, headers=None, timeout=None: Resp404())

    pool = _pool()
    loader = LogoLoader(pool, max_size=16, cache_dir=tmp_path)
    loader.get("https://cdn/dead.jpg", lambda pm: None)
    _drain_pool(pool, qapp)
    assert loader.is_dead("https://cdn/dead.jpg") is True


def test_logo_loader_transient_error_short_ttl(qapp, tmp_path, monkeypatch):
    from dopeiptv.workers import LogoLoader

    monkeypatch.setattr(
        "dopeiptv.workers.requests.get",
        lambda u, headers=None, timeout=None: (_ for _ in ()).throw(RuntimeError("timeout")))

    pool = _pool()
    loader = LogoLoader(pool, max_size=16, cache_dir=tmp_path)
    loader.get("https://cdn/flaky.jpg", lambda pm: None)
    _drain_pool(pool, qapp)
    # Was marked dead but with short TTL - the value in the map is
    # short compared to the permanent one.
    import time
    exp = loader.dead["https://cdn/flaky.jpg"]
    assert exp - time.monotonic() <= loader.dead_ttl_transient + 1
    assert exp - time.monotonic() < loader.dead_ttl_permanent


# -- Simulated delegate decision matrix --------------------------------------


class FakeDelegate:
    """Replicates the branching in ChannelDelegate.paint that picks
    which URL to hand to LogoLoader. Kept in the tests so a refactor
    in the real delegate is deliberate, not accidental."""

    def __init__(self, window):
        self.window = window

    def pick_url(self, it, kind):
        w = self.window
        tmdb_url = w.poster_for(it, kind)
        if tmdb_url and w.logos.is_dead(tmdb_url):
            tmdb_url = None
        url = (tmdb_url or it.get("stream_icon")
               or it.get("cover"))
        if url and w.logos.is_dead(url):
            url = None
        return url

    def should_fetch(self, it, kind):
        """Mirrors the delegate's queue-a-network-fetch condition: the
        provider fallback is only fetched once TMDB has answered, so
        pending rows don't waste traffic on art that's about to be
        replaced."""
        w = self.window
        tmdb_url = w.poster_for(it, kind)
        if tmdb_url and w.logos.is_dead(tmdb_url):
            tmdb_url = None
        url = self.pick_url(it, kind)
        if not url or w.logos.is_dead(url):
            return False
        return url == tmdb_url or w.tmdb_resolved(it, kind)


class FakeWindow:
    def __init__(self, tmdb, logos):
        self.tmdb = tmdb
        self.logos = logos

    def poster_for(self, it, kind):
        if not self.tmdb or kind not in ("vod", "series"):
            return None
        title = it.get("name") or it.get("title") or ""
        if not title:
            return None
        return self.tmdb.get(title, kind, lambda url: None)

    def tmdb_resolved(self, it, kind):
        if not self.tmdb or kind not in ("vod", "series"):
            return True
        title = it.get("name") or it.get("title") or ""
        if not title:
            return True
        return self.tmdb.is_resolved(title, kind)


class _StubLogos:
    """LogoLoader stand-in exposing only what the delegate touches."""
    def __init__(self):
        self.dead = {}
    def is_dead(self, url):
        return url in self.dead


def test_delegate_prefers_tmdb_when_resolved(qapp, settings):
    """A resolved TMDB match wins over the provider icon - it's the
    same artwork the detail panel shows so the columns agree, and
    on providers whose image hosts are down it's the only URL that
    actually loads."""
    from dopeiptv.metadata import PosterResolver
    client = MagicMock()
    client.fetch_details.return_value = {
        "tmdb_id": 1, "poster_url": "https://tmdb/great.jpg"}
    tmdb = PosterResolver(_pool(), settings, client)
    tmdb.get_full("Great Movie", "vod", lambda d: None)
    _drain_pool(tmdb.pool, qapp)

    win = FakeWindow(tmdb, _StubLogos())
    d = FakeDelegate(win)
    it = {"name": "Great Movie", "stream_icon": "https://provider/x.jpg"}
    assert d.pick_url(it, "vod") == "https://tmdb/great.jpg"


def test_delegate_uses_tmdb_when_provider_icon_missing(qapp, settings):
    from dopeiptv.metadata import PosterResolver
    client = MagicMock()
    client.fetch_details.return_value = {
        "tmdb_id": 1, "poster_url": "https://tmdb/great.jpg"}
    tmdb = PosterResolver(_pool(), settings, client)
    tmdb.get_full("Great Movie", "vod", lambda d: None)
    _drain_pool(tmdb.pool, qapp)

    win = FakeWindow(tmdb, _StubLogos())
    d = FakeDelegate(win)
    it = {"name": "Great Movie"}  # provider gave no stream_icon
    assert d.pick_url(it, "vod") == "https://tmdb/great.jpg"


def test_delegate_uses_tmdb_when_provider_icon_dead(qapp, settings):
    """Provider icon 404s -> the TMDB poster steps in."""
    from dopeiptv.metadata import PosterResolver
    client = MagicMock()
    client.fetch_details.return_value = {
        "tmdb_id": 1, "poster_url": "https://tmdb/great.jpg"}
    tmdb = PosterResolver(_pool(), settings, client)
    tmdb.get_full("Great Movie", "vod", lambda d: None)
    _drain_pool(tmdb.pool, qapp)

    import time
    logos = _StubLogos()
    logos.dead["https://provider/broken.jpg"] = time.monotonic() + 3600
    win = FakeWindow(tmdb, logos)
    d = FakeDelegate(win)
    it = {"name": "Great Movie",
          "stream_icon": "https://provider/broken.jpg"}
    assert d.pick_url(it, "vod") == "https://tmdb/great.jpg"


def test_delegate_falls_back_when_tmdb_has_no_match(qapp, settings):
    from dopeiptv.metadata import PosterResolver
    client = MagicMock()
    client.fetch_details.return_value = None
    tmdb = PosterResolver(_pool(), settings, client)
    tmdb.get_full("Sport Rerun", "vod", lambda d: None)
    _drain_pool(tmdb.pool, qapp)

    win = FakeWindow(tmdb, _StubLogos())
    d = FakeDelegate(win)
    it = {"name": "Sport Rerun", "stream_icon": "https://provider/x.jpg"}
    assert d.pick_url(it, "vod") == "https://provider/x.jpg"


def test_delegate_falls_back_when_tmdb_errored(qapp, settings):
    from dopeiptv.metadata import PosterResolver
    client = MagicMock()
    client.fetch_details.side_effect = RuntimeError("upstream 500")
    tmdb = PosterResolver(_pool(), settings, client)
    tmdb.get_full("Errored Movie", "vod", lambda d: None)
    _drain_pool(tmdb.pool, qapp)

    win = FakeWindow(tmdb, _StubLogos())
    d = FakeDelegate(win)
    it = {"name": "Errored Movie",
          "stream_icon": "https://provider/y.jpg"}
    # This is the scenario the user hit: TMDB fetch raised, delegate
    # was left with no URL and the placeholder stuck. Fixed now.
    assert d.pick_url(it, "vod") == "https://provider/y.jpg"


def test_delegate_uses_provider_url_while_tmdb_pending(qapp, settings):
    """While TMDB is still fetching we prefer the provider stream_icon
    over an empty placeholder. Yes, a fast TMDB response may swap
    the poster once - that's a brief cosmetic flicker; the previous
    'wait for TMDB' behaviour left users with no covers at all for
    every row TMDB couldn't answer for, which was much worse."""
    from dopeiptv.metadata import PosterResolver
    client = MagicMock()
    # Leave fetch unresolved by not draining the pool.
    client.fetch_details.return_value = {"tmdb_id": 1, "poster_url": "u"}
    tmdb = PosterResolver(_pool(), settings, client)

    win = FakeWindow(tmdb, _StubLogos())
    d = FakeDelegate(win)
    it = {"name": "Pending Movie", "stream_icon": "https://provider/x.jpg"}
    url = d.pick_url(it, "vod")
    assert url == "https://provider/x.jpg"


def test_delegate_defers_provider_fetch_while_tmdb_pending(qapp, settings):
    """While TMDB is mid-lookup the provider URL may PAINT (if it's
    already cached) but must not be FETCHED - queueing it just burns
    a request on art that gets replaced seconds later, and on flaky
    panel hosts the burst gets us rate-limited."""
    from dopeiptv.metadata import PosterResolver
    client = MagicMock()
    client.fetch_details.return_value = {
        "tmdb_id": 1, "poster_url": "https://tmdb/x.jpg"}
    tmdb = PosterResolver(_pool(), settings, client)

    win = FakeWindow(tmdb, _StubLogos())
    d = FakeDelegate(win)
    it = {"name": "Pending Movie", "stream_icon": "https://provider/x.jpg"}
    # Kicked but not drained: the lookup is in flight.
    assert d.pick_url(it, "vod") == "https://provider/x.jpg"
    assert d.should_fetch(it, "vod") is False

    _drain_pool(tmdb.pool, qapp)
    # Resolved with a match: now it's the TMDB URL and it fetches.
    assert d.pick_url(it, "vod") == "https://tmdb/x.jpg"
    assert d.should_fetch(it, "vod") is True


def test_delegate_fetches_provider_after_nomatch(qapp, settings):
    from dopeiptv.metadata import PosterResolver
    client = MagicMock()
    client.fetch_details.return_value = None  # no TMDB match
    tmdb = PosterResolver(_pool(), settings, client)
    win = FakeWindow(tmdb, _StubLogos())
    d = FakeDelegate(win)
    it = {"name": "Sport Rerun", "stream_icon": "https://provider/x.jpg"}
    d.pick_url(it, "vod")  # kicks the lookup
    _drain_pool(tmdb.pool, qapp)
    assert d.pick_url(it, "vod") == "https://provider/x.jpg"
    assert d.should_fetch(it, "vod") is True


def test_delegate_uses_provider_url_when_tmdb_disabled(qapp, settings):
    """User selected 'IPTV provider' metadata mode: self.tmdb is None
    and every VOD/series row should paint the provider stream_icon.
    This is what the user hit when covers stopped loading after
    switching away from TMDB."""
    win = FakeWindow(tmdb=None, logos=_StubLogos())
    d = FakeDelegate(win)
    it = {"name": "Any Movie", "stream_icon": "https://provider/x.jpg"}
    assert d.pick_url(it, "vod") == "https://provider/x.jpg"


def test_delegate_uses_provider_url_for_live_kind(qapp, settings):
    """Live channels don't go through TMDB at all - the provider
    stream_icon is the canonical logo. tmdb_resolved returns True
    for kinds other than vod/series so the fallback fires
    immediately."""
    from dopeiptv.metadata import PosterResolver
    client = MagicMock()
    tmdb = PosterResolver(_pool(), settings, client)
    win = FakeWindow(tmdb, _StubLogos())
    d = FakeDelegate(win)
    it = {"name": "SVT1", "stream_icon": "https://provider/svt1.png"}
    assert d.pick_url(it, "live") == "https://provider/svt1.png"


def test_logo_loader_host_circuit_breaker(qapp, tmp_path, monkeypatch):
    """Three straight connection failures on one host put the whole
    host on cooldown, so the hundreds of remaining URLs on a dead
    provider panel short-circuit instantly instead of each burning a
    worker for a connect timeout."""
    from dopeiptv.workers import LogoLoader

    monkeypatch.setattr(
        "dopeiptv.workers.requests.get",
        lambda u, headers=None, timeout=None: (_ for _ in ()).throw(
            RuntimeError("Connection reset by peer")))

    pool = _pool()
    loader = LogoLoader(pool, max_size=16, cache_dir=tmp_path)
    for i in range(3):
        loader.get(f"http://deadpanel:2095/images/{i}.jpg", lambda pm: None)
        _drain_pool(pool, qapp)

    # A NEVER-fetched URL on the same host is now considered dead...
    assert loader.is_dead("http://deadpanel:2095/images/other.jpg")
    # ...but other hosts are unaffected.
    assert not loader.is_dead("https://image.tmdb.org/t/p/w500/x.jpg")


def test_logo_loader_host_breaker_resets_on_success(
        qapp, tmp_path, monkeypatch):
    from dopeiptv.workers import LogoLoader

    png = _valid_png_bytes()
    fail = {"on": True}

    class OkResp:
        status_code = 200
        content = png
        def raise_for_status(self): pass

    def fake_get(u, headers=None, timeout=None):
        if fail["on"]:
            raise RuntimeError("reset")
        return OkResp()

    monkeypatch.setattr("dopeiptv.workers.requests.get", fake_get)
    pool = _pool()
    loader = LogoLoader(pool, max_size=16, cache_dir=tmp_path)
    loader.host_cooldown = 0.0  # let the breaker expire immediately
    for i in range(3):
        loader.get(f"http://flaky:2095/{i}.jpg", lambda pm: None)
        _drain_pool(pool, qapp)
    # Breaker tripped but with zero cooldown it expires on next check.
    fail["on"] = False
    got = []
    loader.get("http://flaky:2095/new.jpg", lambda pm: got.append(pm))
    _drain_pool(pool, qapp)
    assert len(got) == 1 and not got[0].isNull()
    assert not loader.is_dead("http://flaky:2095/9.jpg")


def test_host_breaker_ignores_http_errors(qapp, tmp_path, monkeypatch):
    """404s must NOT trip the host breaker - a server answering 404
    is alive, only those specific files are missing. Tripping the
    breaker on them blocks the covers on that host that DO exist
    (the ptv.is regression from the user's second debug log)."""
    from dopeiptv.workers import LogoLoader
    import requests as _requests

    class Resp404:
        status_code = 404
        content = b""
        def raise_for_status(self):
            raise _requests.exceptions.HTTPError(
                "404 Client Error: Not Found for url: x")

    monkeypatch.setattr(
        "dopeiptv.workers.requests.get",
        lambda u, headers=None, timeout=None: Resp404())

    pool = _pool()
    loader = LogoLoader(pool, max_size=16, cache_dir=tmp_path)
    for i in range(5):
        loader.get(f"http://alivepanel:2095/missing{i}.jpg",
                   lambda pm: None)
        _drain_pool(pool, qapp)

    # The individual URLs are dead (they really are 404)...
    assert loader.is_dead("http://alivepanel:2095/missing0.jpg")
    # ...but the host stays open for the covers that exist.
    assert "alivepanel:2095" not in loader.dead_hosts
    assert not loader.is_dead("http://alivepanel:2095/exists.jpg")


def test_http_response_resets_host_strikes(qapp, tmp_path, monkeypatch):
    """Two connection failures followed by an HTTP response (even an
    error one) must reset the strike counter - the host answered."""
    from dopeiptv.workers import LogoLoader
    import requests as _requests

    mode = {"fail": True}

    class Resp404:
        status_code = 404
        content = b""
        def raise_for_status(self):
            raise _requests.exceptions.HTTPError(
                "404 Client Error: Not Found for url: x")

    def fake_get(u, headers=None, timeout=None):
        if mode["fail"]:
            raise RuntimeError("Connection reset by peer")
        return Resp404()

    monkeypatch.setattr("dopeiptv.workers.requests.get", fake_get)
    pool = _pool()
    loader = LogoLoader(pool, max_size=16, cache_dir=tmp_path)
    # Two connection strikes (limit is 3)...
    for i in range(2):
        loader.get(f"http://wobbly:2095/{i}.jpg", lambda pm: None)
        _drain_pool(pool, qapp)
    # ...then the host answers (404): strikes reset.
    mode["fail"] = False
    loader.get("http://wobbly:2095/answers.jpg", lambda pm: None)
    _drain_pool(pool, qapp)
    # A third connection failure is now strike 1, not strike 3.
    mode["fail"] = True
    loader.get("http://wobbly:2095/again.jpg", lambda pm: None)
    _drain_pool(pool, qapp)
    assert "wobbly:2095" not in loader.dead_hosts


def test_logo_loader_sends_browser_user_agent(qapp, tmp_path, monkeypatch):
    """Several image hosts (Wikipedia's 403, some Xtream panels'
    TCP resets) reject python-requests' default UA. Every image
    fetch must carry the browser-style header."""
    from dopeiptv.workers import LogoLoader

    seen = {}
    png = _valid_png_bytes()

    class OkResp:
        status_code = 200
        content = png
        def raise_for_status(self): pass

    def fake_get(u, headers=None, timeout=None):
        seen["headers"] = headers or {}
        seen["timeout"] = timeout
        return OkResp()

    monkeypatch.setattr("dopeiptv.workers.requests.get", fake_get)
    pool = _pool()
    loader = LogoLoader(pool, max_size=16, cache_dir=tmp_path)
    loader.get("https://cdn/ua.jpg", lambda pm: None)
    _drain_pool(pool, qapp)
    assert "Mozilla/5.0" in seen["headers"].get("User-Agent", "")
    # Connect timeout is the short one so dead hosts fail fast.
    assert seen["timeout"][0] < 5


# -- Provider-title cleaning: the [IMDB] / [MULTI] / codec noise -----------


def test_clean_title_strips_bracketed_suffixes():
    from dopeiptv.metadata import PosterResolver
    for raw, expected in [
        ("The Matrix [IMDB]", "The Matrix"),
        ("Barbie [ ]", "Barbie"),
        ("Dune (2021) [MULTI]", "Dune"),
        ("Interstellar [HDR]", "Interstellar"),
        ("Oppenheimer [SUB]", "Oppenheimer"),
    ]:
        cleaned, _ = PosterResolver.clean_title(raw)
        assert cleaned == expected, f"{raw!r} -> {cleaned!r}"


def test_clean_title_strips_language_prefix_and_codec_tail():
    from dopeiptv.metadata import PosterResolver
    for raw, expected in [
        ("EN | Sicario", "Sicario"),
        ("SV - Snatch (2000)", "Snatch"),
        ("The Matrix 1999 1080p WEB-DL x265", "The Matrix"),
        ("Dune 2021 BluRay x264 AC3", "Dune"),
    ]:
        cleaned, _ = PosterResolver.clean_title(raw)
        assert cleaned == expected, f"{raw!r} -> {cleaned!r}"


def test_clean_title_extracts_year():
    from dopeiptv.metadata import PosterResolver
    assert PosterResolver.clean_title("Dune (2021)")[1] == 2021
    assert PosterResolver.clean_title("Dune 2021")[1] == 2021
    assert PosterResolver.clean_title("Dune")[1] is None


def test_auto_fetch_cleans_title_before_calling_client(qapp, settings):
    """The auto-matcher used to hand the raw provider title straight
    to TmdbClient.fetch_details; TMDB then failed to find a match
    because the title had bracketed noise. Now clean_title runs
    first so the search query is the naked title."""
    from dopeiptv.metadata import PosterResolver
    client = MagicMock()
    client.fetch_details.return_value = {
        "tmdb_id": 1, "poster_url": "https://tmdb/x.jpg"}
    r = PosterResolver(_pool(), settings, client)
    r.get_full("The Matrix (1999) 1080p WEB-DL [IMDB]", "vod",
               lambda d: None)
    _drain_pool(r.pool, qapp)
    # First positional arg passed to fetch_details is the cleaned
    # search query, not the raw provider title.
    call_args, _ = client.fetch_details.call_args
    assert call_args[0] == "The Matrix", call_args
    assert call_args[1] == "vod"


# -- Manual TMDB match with preview seed ------------------------------------


def test_set_manual_match_caches_preview_immediately(qapp, settings):
    """The dialog already knew a poster URL from the search step - the
    manual pick must cache that URL right away so the delegate can
    show it on the very next paint, without having to wait for
    fetch_details_by_id."""
    from dopeiptv.metadata import PosterResolver
    client = MagicMock()
    client.fetch_details_by_id.return_value = {
        "tmdb_id": 42, "poster_url": "https://tmdb/x.jpg",
        "rating": 8.1, "cast": []}
    r = PosterResolver(_pool(), settings, client)

    got = []
    r.set_manual_match(
        "Some Movie [IMDB]", "vod", 42,
        callback=lambda d: got.append(d),
        preview={"poster_url": "https://tmdb/preview.jpg",
                 "title": "Some Movie", "year": 2020})

    # Immediate seed: cache and first callback fired synchronously.
    assert r.is_resolved("Some Movie [IMDB]", "vod") is True
    assert r.tmdb_id_for("Some Movie [IMDB]", "vod") == 42
    assert len(got) >= 1
    assert got[0]["poster_url"] == "https://tmdb/preview.jpg"
    assert got[0]["manual"] is True

    # After the details call resolves, a richer dict overwrites the
    # seed (but poster_url from search wins over any None from details).
    _drain_pool(r.pool, qapp)
    final = r._cache[r._key("Some Movie [IMDB]", "vod")]
    assert final["rating"] == 8.1
    assert final["manual"] is True


def test_set_manual_match_survives_details_error(qapp, settings):
    """If fetch_details_by_id blows up (network flake, TMDB 500) the
    preview poster URL must still be cached so the row picks up the
    user's choice on the next paint. Previously the failure was
    silent and the cover never appeared."""
    from dopeiptv.metadata import PosterResolver
    client = MagicMock()
    client.fetch_details_by_id.side_effect = RuntimeError("upstream 500")
    r = PosterResolver(_pool(), settings, client)

    got = []
    r.set_manual_match(
        "Movie", "vod", 99,
        callback=lambda d: got.append(d),
        preview={"poster_url": "https://tmdb/y.jpg"})
    _drain_pool(r.pool, qapp)

    cached = r._cache[r._key("Movie", "vod")]
    assert cached["poster_url"] == "https://tmdb/y.jpg", (
        "preview URL must survive when details call fails")
    assert cached["manual"] is True


def test_delegate_falls_back_when_tmdb_url_is_dead(qapp, settings):
    """TMDB returned a poster URL but the CDN 404s. Delegate should
    reach for the provider stream_icon."""
    from dopeiptv.metadata import PosterResolver
    client = MagicMock()
    client.fetch_details.return_value = {
        "tmdb_id": 1, "poster_url": "https://dead-tmdb-cdn/x.jpg"}
    tmdb = PosterResolver(_pool(), settings, client)
    tmdb.get_full("Movie", "vod", lambda d: None)
    _drain_pool(tmdb.pool, qapp)

    logos = _StubLogos()
    import time
    logos.dead["https://dead-tmdb-cdn/x.jpg"] = time.monotonic() + 3600
    win = FakeWindow(tmdb, logos)
    d = FakeDelegate(win)
    it = {"name": "Movie", "stream_icon": "https://provider/x.jpg"}
    assert d.pick_url(it, "vod") == "https://provider/x.jpg"
