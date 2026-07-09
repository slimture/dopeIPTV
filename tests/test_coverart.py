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
