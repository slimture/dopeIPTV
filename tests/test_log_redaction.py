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
import sys

import pytest

from dopeiptv.core import log as logmod
from dopeiptv.core.log import _RedactFilter, redact, redact_url


@pytest.fixture(autouse=True)
def _clean_secrets():
    """Each test starts with an empty secret set and leaves one behind -
    the module-level set outlives any single test otherwise."""
    saved = set(logmod._secrets)
    short = set(logmod._short)
    logmod._secrets.clear()
    logmod._short.clear()
    yield
    logmod._secrets.clear()
    logmod._secrets.update(saved)
    logmod._short.clear()
    logmod._short.update(short)


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


def test_a_very_short_credential_is_masked_as_a_word_of_its_own():
    """Masking a three-character string inside words would blank out
    ordinary words all over the log: one that short is masked only where
    it stands alone - in a quoted value, between separators."""
    logmod.register_secrets("ab", "abc", None, "", " ")
    assert redact("abc is not a secret") == "*** is not a secret"
    assert redact("login failed for user=abc pass=ab") == "login failed for user=*** pass=***"
    assert redact("tabcat abcd ab1 xab") == "tabcat abcd ab1 xab", "inside a word: left alone"
    assert redact("a b c") == "a b c"


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


def test_an_exception_logged_as_it_is_loses_the_account_too():
    """log.info("...: %s", e) and exc_info=True are how most failures reach
    the log, and a requests error quotes the whole URL it failed on - the
    account in it. Only text arguments were masked, so these went out as
    they were (DOPEIPTV_LOG=debug, the log file, Recent lines)."""
    import io
    logmod.register_secrets("hunter2pass", "PLEXTOKEN99")
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter("%(message)s"))
    lg = logging.getLogger("dopeiptv.test_redaction")
    lg.propagate = False
    lg.setLevel(logging.DEBUG)
    lg.addHandler(h)
    f = _RedactFilter()
    lg.addFilter(f)
    try:
        err = ConnectionError("Max retries exceeded with url: /live/joe/hunter2pass/1.ts")
        lg.warning("stream failed: %s (%r) after %d tries", err, err, 3)
        try:
            raise ConnectionError("url: /:/timeline?X-Plex-Token=PLEXTOKEN99")
        except ConnectionError:
            lg.debug("media server: progress", exc_info=True)
        lg.warning(ConnectionError("hunter2pass as the message"))
        lg.info("left alone: %s %r", ValueError("nothing here"), {"n": 1})
    finally:
        lg.removeHandler(h)
        lg.removeFilter(f)
    out = buf.getvalue()
    assert "hunter2pass" not in out and "PLEXTOKEN99" not in out
    assert "after 3 tries" in out and "Traceback" in out and "X-Plex-Token=***" in out
    assert "left alone: nothing here {'n': 1}" in out


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


def test_an_uncaught_exception_reaches_the_log_masked():
    # The crash hook wrote tracebacks to stderr only, so the log file that
    # gets attached to a bug report never had the one thing that mattered -
    # and nothing in it went through the masking either.
    import sys
    import threading

    from dopeiptv import app

    saved = (sys.excepthook, threading.excepthook, sys.unraisablehook)
    records = []

    class Grab(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    h = Grab()
    logmod.log.addHandler(h)
    filt = _RedactFilter()
    logmod.log.addFilter(filt)
    try:
        logmod.register_secrets("hunter2pw")
        app._install_crash_hooks()
        try:
            raise ValueError("boom http://h.tv/u/hunter2pw/1.ts")
        except ValueError as e:
            sys.excepthook(type(e), e, e.__traceback__)
    finally:
        sys.excepthook, threading.excepthook, sys.unraisablehook = saved
        logmod.log.removeHandler(h)
        logmod.log.removeFilter(filt)
    assert records and "CRASH" in records[0]
    assert "ValueError: boom" in records[0]
    assert "hunter2pw" not in records[0]


def _filtered(fmt, *args):
    rec = logging.LogRecord("dopeiptv", logging.INFO, __file__, 1, fmt, args or None, None)
    assert _RedactFilter().filter(rec) is True
    return rec.getMessage()


def test_an_ffmpeg_line_loses_the_address_s_token_and_login_with_nothing_registered():
    """ffmpeg and mpv print the address they failed on, and their lines go
    to the log as they are (cast bridge, cast feed, AirPlay bridge). A
    plain M3U's address carries its token in the query, or an Xtream
    account in the bare /user/pass/id form, and neither is a registered
    value - the filter let them through, and did nothing at all while no
    account was registered."""
    assert not logmod._secrets
    line = ("[in#0 @ 0x55d0] Error opening input: Server returned 403 Forbidden "
            "for http://cdn.example:8080/hls/x.m3u8?token=ab12cd34ef&exp=1700000000&action=play.")
    out = _filtered("cast bridge: ffmpeg: %s", line)
    assert "ab12cd34ef" not in out and "1700000000" not in out, out
    assert "token=***" in out and "action=play" in out and out.endswith(".")
    assert "cdn.example:8080/hls/x.m3u8" in out, "where it went still shows"

    for addr, gone in (("http://joe:hunter2@box.example/stream.ts", ("joe", "hunter2")),
                       ("http://box.example:8080/joe99/hunter2/1234.ts", ("joe99", "hunter2")),
                       ("http://box.example:8080/live/joe99/hunter2/1234.ts", ("joe99", "hunter2")),
                       ("https://cdn.example/seg?Policy=abc&Signature=def&Key-Pair-Id=ghi",
                        ("abc", "def", "ghi"))):
        out = _filtered("mpv: Failed to open %s.", addr)
        assert not [g for g in gone if g in out], out
        assert "box.example" in out or "cdn.example" in out
    # A message with no arguments: its own text, masked the same way.
    out = _filtered("Opening 'http://box.example/get.php?username=joe99&password=hunter2' for reading")
    assert "joe99" not in out and "hunter2" not in out and "for reading" in out, out


def test_masking_an_address_never_breaks_the_formatting():
    """An address split between the message and an argument, a %s in a
    query, a percent sign: the line comes out whole."""
    out = _filtered("probe http://%s:%d/live.ts?token=%s at %d%%", "box.example", 8080, "s3cr3t", 50)
    assert out == "probe http://box.example:8080/live.ts?token=*** at 50%", out
    # A local path and a plain address stay readable.
    keep = "using /home/pontus/Videos/1.ts and https://image.tmdb.org/t/p/w500/abc.jpg"
    assert _filtered("%s", keep) == keep


def test_a_login_outside_an_address_is_masked_too():
    """The filter only looked inside text with "://" in it: requests
    quotes the path and query alone ("Max retries exceeded with url:
    /get.php?username=..."), a header goes to a log as text, an answer as
    JSON - all passed through while nothing was registered."""
    assert not logmod._secrets
    out = _filtered("guide: %s", "HTTPConnectionPool(host='box.example', port=8080): Max retries "
                    "exceeded with url: /get.php?username=joe99&password=hunter2&type=m3u_plus "
                    "(Caused by NewConnectionError)")
    assert "joe99" not in out and "hunter2" not in out, out
    assert "type=m3u_plus" in out and "box.example" in out, "what was asked still shows"
    out = _filtered("url: /live/joe99/hunter2/1234.ts failed")
    assert "joe99" not in out and "hunter2" not in out and "1234.ts failed" in out, out
    for line, gone in (("sent Authorization: Bearer abcdefgh12345678", "abcdefgh12345678"),
                       ("Cookie: PHPSESSID=s3ss10n; mac=00:1A:79:12:34:56", "s3ss10n"),
                       ('answer {"access_token": "tok3n-value", "expires_in": 7776000}', "tok3n-value"),
                       ("{'refresh_token': 'r3fresh', 'scope': 'public'}", "r3fresh"),
                       ("X-Emby-Token: 0123456789abcdef", "0123456789abcdef"),
                       ("X-Api-Key=k3yk3yk3y", "k3yk3yk3y"),
                       ("portal box 00:1A:79:AB:CD:EF answered", "00:1A:79:AB:CD:EF")):
        out = _filtered("%s", line)
        assert gone not in out, out
    # The server a line is about stays, and so does a line of Python.
    assert _filtered("host: box.example login: joe") == "host: box.example login: joe"
    code = '    key = str(settings.value("theme_light", "") or "")'
    assert _filtered("%s", code) == code


def test_an_address_s_new_names_lose_their_values():
    for param in ("X-Emby-Token", "access_token", "refresh_token", "api-key", "X-Api-Key"):
        out = redact_url(f"http://box.example/x?{param}=s3cretvalue&action=a")
        assert "s3cretvalue" not in out and "action=a" in out, out


def test_the_log_s_login_patterns_are_the_report_s():
    """The report and the log mask the same headers, tokens and MACs; the
    log's names are the report's but the login and the host."""
    from dopeiptv.core import report
    for name in ("AUTH_LINE", "BEARER", "MAC"):
        assert getattr(logmod, name).pattern == getattr(report, "_" + name).pattern, name
    names = report._KEYVAL.pattern.split("(", 2)[1]
    assert logmod.KEYVAL.pattern.split("(", 2)[1] == names.replace("|login|host", "")


def test_a_control_character_is_shown_not_acted_on():
    """An escape sequence in a stream's title or a server's answer would
    recolour the terminal, move the cursor or rewrite the line before it
    in a log viewer."""
    out = _filtered("title: %s", "News\x1b[2J\x1b]0;owned\x07\rFAKE\x85end\tok\nnext")
    assert out == "title: News\\x1b[2J\\x1b]0;owned\\x07\\x0dFAKE\\x85end\tok\nnext", out
    rec = logging.LogRecord("dopeiptv", logging.INFO, __file__, 1, "bare\x1b[31m", None, None)
    _RedactFilter().filter(rec)
    assert rec.getMessage() == "bare\\x1b[31m"


def test_a_log_file_and_the_crash_file_are_this_user_s_alone(tmp_path, monkeypatch):
    import os
    import stat

    from dopeiptv.core import report
    if os.name != "posix":
        pytest.skip("modes are POSIX")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(logmod.sys, "platform", "linux")
    old = os.umask(0o022)
    try:
        path = logmod.set_file_logging(True)
        try:
            assert path is not None
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
            assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700
            handler = logmod._file_handler
            handler.doRollover()
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
            assert stat.S_IMODE(os.stat(path + ".1").st_mode) == 0o600
        finally:
            logmod.set_file_logging(False)
        # One left from before at 0644 is narrowed as it is opened.
        loose = tmp_path / "loose.log"
        loose.write_text("x")
        os.chmod(loose, 0o644)
        fh = logmod._private_handler(str(loose))
        fh.close()
        assert stat.S_IMODE(os.stat(loose).st_mode) == 0o600
        crash = report.open_crash_file(str(tmp_path / "crash"))
        crash.close()
        assert stat.S_IMODE(os.stat(report.crash_path(str(tmp_path / "crash"))).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(tmp_path / "crash").st_mode) == 0o700
    finally:
        os.umask(old)


def test_sign_ins_and_codes_are_registered_where_they_are_read(monkeypatch):
    """Trakt's and Simkl's tokens, the sync code and a room's code - each
    masked in every log line from the moment the app reads it."""
    import dopeiptv.core.workers as workers
    from dopeiptv.core import sync, together
    from dopeiptv.core.stores import KeptValues

    class S(dict):
        def value(self, key, default=None):
            return self.get(key, default)

    monkeypatch.setattr(workers, "on_gui_thread", lambda *a, **k: None)
    KeptValues(S(trakt_access_token="trakt-acc3ss", trakt_expires_at=1700000000),
               ["trakt_access_token", "trakt_expires_at"])
    KeptValues(S(), ["simkl_access_token"]).put({"simkl_access_token": "simkl-t0ken"})
    raw = bytes(range(32))
    sync.code_of(S(sync_code=sync.format_code(raw)))
    room = together.new_code()
    together.derive(room)
    gone = ("trakt-acc3ss", "simkl-t0ken", sync.format_code(raw), room, room.replace("-", ""))
    out = redact(" / ".join(gone))
    assert not [g for g in gone if g in out], out
    assert "1700000000" not in logmod._secrets


def test_a_line_that_could_not_be_masked_is_withheld_not_written_as_it_was(monkeypatch):
    """The filter fails closed: a fault in the masking, arguments that do
    not fit the message, or a traceback that could not be masked never
    reach a handler as they were - a line saying one was withheld does."""
    logmod.register_secrets("hunter2pw")

    def broken(_text):
        raise RuntimeError("masking broke")

    monkeypatch.setattr(logmod, "redact_text", broken)
    rec = logging.LogRecord("dopeiptv", logging.WARNING, __file__, 1,
                            "login %s", ("hunter2pw",), None)
    assert _RedactFilter().filter(rec) is True
    assert "hunter2pw" not in rec.getMessage()
    assert rec.getMessage().startswith(logmod.WITHHELD)
    try:
        raise ValueError("boom hunter2pw")
    except ValueError:
        info = sys.exc_info()
    rec = logging.LogRecord("dopeiptv", logging.ERROR, __file__, 1, "failed", None, info)
    _RedactFilter().filter(rec)
    out = logging.Formatter().format(rec)
    assert "hunter2pw" not in out
    assert logmod.WITHHELD in out
    monkeypatch.undo()
    # Arguments that do not fit: never the message and its arguments as
    # logging's own error report would print them.
    rec = logging.LogRecord("dopeiptv", logging.INFO, __file__, 1,
                            "two %s %s", ("hunter2pw",), None)
    _RedactFilter().filter(rec)
    assert "hunter2pw" not in rec.getMessage()
    assert rec.getMessage().startswith(logmod.WITHHELD)


def test_a_login_inside_a_longer_word_takes_the_word_with_it():
    """"not st***d yet" told a login of "arte" by where the mask began and
    ended: the whole word goes, never a part of it."""
    logmod.register_secrets("arte")
    out = redact("Video: own thread (not started yet) /live/arte/x/1.ts")
    assert out == "Video: own thread (not *** yet) /live/***/x/1.ts"
    assert "st***" not in out and "***d" not in out
