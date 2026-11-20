"""Unit tests for the extracted CoverArtService (window-agnostic artwork)."""

from dopeiptv.services.coverart import CoverArtService


class FakeSettings:
    def __init__(self, values=None):
        self._v = values or {}

    def value(self, key, default=None):
        return self._v.get(key, default)


class FakeLogos:
    """Just the two attributes the service consults - no rendering."""
    def __init__(self, dead=(), waiting=()):
        self._dead = set(dead)
        self.waiting = set(waiting)

    def is_dead(self, url):
        return url in self._dead


def _service(settings_values=None, dead=(), waiting=()):
    calls = []
    svc = CoverArtService(FakeSettings(settings_values),
                          FakeLogos(dead, waiting),
                          lambda: calls.append(1))
    return svc, calls


def test_no_key_means_no_resolver():
    # No bundled key ships in a dev checkout and none is configured, so the
    # resolver stays None but the service is still fully usable.
    svc, _ = _service({"metadata_source": "tmdb"})
    assert svc.resolver is None
    # source "tmdb" still means the *list* would prefer TMDB art if present
    assert svc.prefer_tmdb is True


def test_provider_source_disables_tmdb_preference():
    svc, _ = _service({"metadata_source": "playlist"})
    assert svc.prefer_tmdb is False


def test_cover_url_falls_back_to_provider_image():
    svc, _ = _service({"metadata_source": "playlist"})
    it = {"name": "Some Movie", "stream_icon": "http://panel/x.jpg"}
    assert svc.cover_url(it, "vod") == "http://panel/x.jpg"


def test_cover_url_none_when_no_art():
    svc, _ = _service({"metadata_source": "playlist"})
    assert svc.cover_url({"name": "Live 1"}, "live") is None


def test_cover_kind_maps_snapshot_rows():
    svc, _ = _service()
    assert svc._cover_kind({"_kind": "movie"}, "watched") == "vod"
    assert svc._cover_kind({"_kind": "episode"}, "history") == "series"
    assert svc._cover_kind({}, "vod") == "vod"


def test_should_fetch_gating():
    svc, _ = _service({"metadata_source": "playlist"},
                      dead={"http://dead"}, waiting={"http://busy"})
    it = {"name": "M"}
    assert svc.should_fetch(None, it, "vod") is False
    assert svc.should_fetch("http://dead", it, "vod") is False
    assert svc.should_fetch("http://busy", it, "vod") is False
    # image.tmdb.org always fetches immediately
    assert svc.should_fetch("https://image.tmdb.org/p/x.jpg", it, "vod") is True
    # provider art, no TMDB preference -> fetch straight away
    assert svc.should_fetch("http://panel/x.jpg", it, "vod") is True


def test_trakt_only_row_uses_its_own_poster():
    # A Trakt-only row carries a real TMDB poster as stream_icon and must not
    # trigger a title search (resolver is None here anyway).
    svc, _ = _service({"metadata_source": "tmdb"})
    it = {"name": "Show", "_trakt_only": True,
          "stream_icon": "https://image.tmdb.org/p/poster.jpg"}
    assert svc.cover_url(it, "series") == "https://image.tmdb.org/p/poster.jpg"


def test_is_tmdb_image_url_checks_host_not_substring():
    """The TMDB classification must key on the URL's HOST - a substring
    match would also accept provider URLs that merely embed the string
    (CodeQL py/incomplete-url-substring-sanitization)."""
    from dopeiptv.core.workers import is_tmdb_image_url
    assert is_tmdb_image_url("https://image.tmdb.org/t/p/w500/x.jpg")
    assert not is_tmdb_image_url("http://panel.example/image.tmdb.org/x.jpg")
    assert not is_tmdb_image_url("http://evil-image.tmdb.org.example/x.jpg")
    assert not is_tmdb_image_url("http://prefix-image.tmdb.org/x.jpg")
    assert not is_tmdb_image_url(None)
    assert not is_tmdb_image_url("")


def test_tmdb_off_asks_tmdb_nothing(monkeypatch):
    # Settings > Posters & info, "Provider artwork, TMDB off": no resolver at
    # all, even with a key - so no title search, no ids for Trakt or Simkl,
    # no chart; and no provider picture moved onto image.tmdb.org.
    import inspect

    from dopeiptv.services import coverart
    made = []
    monkeypatch.setattr(coverart, "TmdbClient", lambda key: made.append(key))
    monkeypatch.setattr(coverart, "bundled_tmdb_key", lambda: "built-in")
    svc, _ = _service({"metadata_source": "off", "tmdb_api_key": "mine"})
    assert svc.off and svc.resolver is None and not svc.prefer_tmdb
    assert made == [], "no TMDB client is made"
    assert coverart.tmdb_off(FakeSettings({"metadata_source": "off"}))
    assert not coverart.tmdb_off(FakeSettings({"metadata_source": "playlist"}))
    proxied = "http://panel:2095/images/movies/AbCdEfGhIjKlMnOpQrStUvWxYz1.jpg"
    it = {"name": "Some Movie", "stream_icon": proxied}
    assert svc.cover_url(it, "vod") == proxied, "the provider's own address, as it is"
    monkeypatch.setattr(coverart, "bundled_tmdb_key", lambda: "")
    on, _ = _service({"metadata_source": "playlist"})
    assert on.cover_url(it, "vod").startswith("https://image.tmdb.org/"), "as before when TMDB is on"
    # The other places that would move a provider's picture onto TMDB's
    # server, and Popular right now's chart kept on disk, ask the switch.
    from dopeiptv.ui import mw_detail, mw_popular, mw_series_page
    assert '"off", False)' in inspect.getsource(mw_detail._DetailMixin._load_detail_poster)
    assert "self.cover.off" in inspect.getsource(mw_series_page)
    chart = inspect.getsource(mw_popular._PopularMixin._popular_chart_now)
    assert chart.index('"off", False)') < chart.index("popular.load(")
    # And the setting is offered, with its note.
    from dopeiptv.ui import set_content
    src = inspect.getsource(set_content.build_metadata)
    assert '("off", tr("meta_src_off"))' in src and 'tr("meta_hint_off")' in src
