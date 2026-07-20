"""Unit tests for dopeiptv.providers.client utilities."""

from dopeiptv.providers.client import b64, parse_xtream_url


def test_b64_decodes_valid():
    assert b64("aGVsbG8=") == "hello"


def test_b64_empty():
    assert b64("") == ""
    assert b64(None) == ""


def test_b64_invalid_returns_empty():
    assert b64("!!!") == ""


def test_parse_xtream_url_get_php():
    assert parse_xtream_url(
        "http://host.tv:8080/get.php?username=abc&password=xyz"
        "&type=m3u_plus&output=ts") == ("http://host.tv:8080", "abc", "xyz")


def test_parse_xtream_url_player_api():
    assert parse_xtream_url(
        "https://host.tv/player_api.php?username=u1&password=p1"
    ) == ("https://host.tv", "u1", "p1")


def test_parse_xtream_url_stream_path():
    # /live/USER/PASS/ID.ext and the bare /USER/PASS/ID form both work.
    assert parse_xtream_url(
        "http://host.tv:8080/live/abc/xyz/1234.ts"
    ) == ("http://host.tv:8080", "abc", "xyz")
    assert parse_xtream_url(
        "http://host.tv:8080/abc/xyz/1234"
    ) == ("http://host.tv:8080", "abc", "xyz")


def test_parse_xtream_url_rejects_non_links():
    # No credentials, not a URL, empty, or a plain M3U link -> None, so a
    # manually-typed host/username/password is never disturbed.
    for bad in ("", "   ", "host.tv:8080", "http://host.tv:8080",
                "https://example.com/playlist.m3u", "not a url at all"):
        assert parse_xtream_url(bad) is None
