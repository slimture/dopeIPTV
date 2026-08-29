"""A log gets pasted into a bug report, so a password in a log is a
password published - and an Xtream stream URL carries the whole account:

    http://host:8080/live/USERNAME/PASSWORD/1234.ts

CodeQL found four of these (players.py, main_window.py, mw_detail.py,
cast_bridge.py); the same shape existed at three more sites it did not
flag. These tests pin both layers of the fix - the structural one that
understands URL shapes, and the value one that masks the account itself
wherever it turns up.
"""
import logging

import pytest

from dopeiptv.core import log as logmod
from dopeiptv.core.log import _RedactFilter, redact, redact_url


@pytest.fixture(autouse=True)
def _clean_secrets():
    """Each test starts with an empty secret set and leaves one behind -
    the module-level set outlives any single test otherwise."""
    saved = set(logmod._secrets)
    logmod._secrets.clear()
    yield
    logmod._secrets.clear()
    logmod._secrets.update(saved)


def test_the_xtream_stream_url_loses_its_account():
    """The account is two path segments, and every stream URL the app
    plays is built that way."""
    for kind in ("live", "movie", "series"):
        out = redact_url(f"http://box.example:8080/{kind}/joe/hunter2/55.ts")
        assert "joe" not in out
        assert "hunter2" not in out
        # What is left still identifies the stream, or the log is useless.
        assert "box.example:8080" in out
        assert f"/{kind}/" in out and "55.ts" in out

    # Timeshift has the same account in the same place, with more after it.
    out = redact_url("http://box.example:8080/timeshift/joe/hunter2/"
                     "60/2026-01-01:20-00/55.ts")
    assert "joe" not in out and "hunter2" not in out
    assert "2026-01-01:20-00" in out


def test_the_api_call_loses_its_query_credentials():
    out = redact_url("http://box.example:8080/player_api.php"
                     "?username=joe&password=hunter2&action=get_live_streams")
    assert "joe" not in out and "hunter2" not in out
    # The action is the whole diagnostic value of the line.
    assert "action=get_live_streams" in out

    # Whatever the provider calls it.
    for param in ("token", "auth", "api_key", "pwd", "sig"):
        assert "s3cret" not in redact_url(
            f"http://box.example/x.m3u8?{param}=s3cret")


def test_userinfo_credentials_go_too():
    out = redact_url("http://joe:hunter2@box.example/stream.ts")
    assert "joe" not in out and "hunter2" not in out
    assert "box.example" in out


def test_a_local_path_is_left_readable():
    """Most of what gets logged is not a URL at all, and mangling a file
    path would cost the log its usefulness for nothing."""
    for plain in ("/home/pontus/Filmer/Dune (2021).mkv",
                  "C:\\Users\\pontus\\Videos\\ep1.mkv", ""):
        assert redact_url(plain) == plain

    # A plain http URL with nothing sensitive in it survives intact.
    keep = "https://image.tmdb.org/t/p/w500/abc.jpg"
    assert redact_url(keep) == keep


def test_redact_url_never_raises():
    """It is called from inside log calls, including ones reporting a
    failure. Blowing up there would lose the very line worth reading."""
    for junk in (None, 12345, object(), "http://[oops", "%%%"):
        redact_url(junk)          # no exception is the assertion


def test_the_registered_account_is_masked_in_any_shape():
    """The structural pass cannot know every URL layout a provider will
    invent - and the bare /user/pass/id form is indistinguishable from an
    ordinary three-segment path. Masking the values themselves covers
    both, and covers a requests exception quoting the whole URL."""
    logmod.register_secrets("joe_smith", "hunter2pass")

    bare = redact_url("http://box.example:8080/joe_smith/hunter2pass/55")
    assert "joe_smith" not in bare and "hunter2pass" not in bare

    quoted = redact("HTTPSConnectionPool(host='box.example'): "
                    "/live/joe_smith/hunter2pass/55.ts timed out")
    assert "hunter2pass" not in quoted
    assert "timed out" in quoted


def test_a_very_short_credential_is_not_registered():
    """Masking a three-character string would blank out ordinary words all
    over the log, and a credential that short protects nothing anyway."""
    logmod.register_secrets("ab", "abc", None, "")
    assert redact("abc is not a secret") == "abc is not a secret"


def test_the_filter_masks_records_the_call_site_forgot():
    """The safety net: every record, whether or not somebody remembered to
    call redact_url - including sites added after this was written."""
    logmod.register_secrets("hunter2pass")
    f = _RedactFilter()

    rec = logging.LogRecord("dopeiptv", logging.INFO, __file__, 1,
                            "playing %s at %d%%",
                            ("http://box/live/joe/hunter2pass/1.ts", 50),
                            None)
    assert f.filter(rec) is True
    msg = rec.getMessage()
    assert "hunter2pass" not in msg
    # The %d argument kept its type, so formatting still works.
    assert "at 50%" in msg


def test_the_filter_leaves_a_record_alone_when_it_cannot_help():
    """It runs on every log call in the app; it must never be the reason a
    line is lost."""
    f = _RedactFilter()

    class _Boom:
        def __str__(self):
            raise RuntimeError("no")

    rec = logging.LogRecord("dopeiptv", logging.INFO, __file__, 1,
                            _Boom(), None, None)
    assert f.filter(rec) is True

    # With nothing registered there is nothing to do and nothing to break.
    rec2 = logging.LogRecord("dopeiptv", logging.INFO, __file__, 1,
                             "plain %s", ("text",), None)
    assert f.filter(rec2) is True
    assert rec2.getMessage() == "plain text"


def test_the_client_registers_its_own_account():
    """Nobody has to remember to do it at the call sites - constructing a
    client is what arms the masking."""
    from dopeiptv.providers.client import XtreamClient

    XtreamClient("http://box.example", "joe_smith", "hunter2pass")
    assert "hunter2pass" not in redact_url(
        "http://box.example/joe_smith/hunter2pass/9.ts")
