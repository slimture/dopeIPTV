"""Subtitles from OpenSubtitles.com: the file hash, what is asked of the
API and how its answers are read, and the player and window around it.
The API itself is played by a stand-in session - nothing goes online."""
import os
import subprocess
import sys

import pytest

from dopeiptv import VERSION
from dopeiptv.i18n import LocalizedError
from dopeiptv.providers import opensubtitles as osub

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_movie_hash_is_size_plus_little_endian_word_sums(tmp_path):
    f = tmp_path / "zeros.mkv"
    f.write_bytes(bytes(200_000))
    assert osub.movie_hash(f) == f"{200_000:016x}"
    # One word set in the head and one in the tail: added, little-endian.
    data = bytearray(200_000)
    data[0] = 1
    data[-8:] = (2).to_bytes(8, "little")
    f.write_bytes(bytes(data))
    assert osub.movie_hash(f) == f"{200_000 + 3:016x}"
    # Words wrap at 64 bits.
    data[:8] = (2**64 - 1).to_bytes(8, "little")
    f.write_bytes(bytes(data))
    assert osub.movie_hash(f) == f"{200_000 + 1:016x}"
    small = tmp_path / "small.srt"
    small.write_bytes(b"x" * 1000)
    assert osub.movie_hash(small) is None
    assert osub.movie_hash(tmp_path / "missing") is None


def test_search_language_follows_settings_then_the_app():
    assert osub.search_language("swe", "en") == "sv"
    assert osub.search_language("ger", "sv") == "de"
    assert osub.search_language("", "sv") == "sv"
    assert osub.search_language("", "pt") == "pt-BR"
    assert osub.search_language("", "zh") == "zh-CN"
    assert osub.search_language("", "xx") == "en"
    assert osub.search_language("por", "en") == "pt-PT"


def _hit(fid, lang="en", dl=10, files=1, **attrs):
    return {"id": str(fid), "type": "subtitle", "attributes": dict({
        "language": lang, "download_count": dl, "release": f"Rel.{fid}",
        "files": [{"file_id": fid + i, "file_name": f"{fid}.srt"}
                  for i in range(files)],
        "feature_details": {"title": "Big Buck Bunny"}}, **attrs)}


def test_rows_are_read_and_ranked():
    rows = [osub._row(h) for h in (
        _hit(1, dl=500), _hit(2, dl=900, machine_translated=True),
        _hit(3, dl=5, moviehash_match=True), _hit(4, dl=50, from_trusted=True),
        _hit(5, files=2), {"attributes": None}, None)]
    assert rows[4] is None and rows[5] is None and rows[6] is None
    ranked = osub.rank([r for r in rows if r])
    assert [r["file_id"] for r in ranked] == [3, 4, 1, 2]
    assert ranked[0]["hash_match"] and ranked[-1]["machine"]
    assert ranked[0]["title"] == "Big Buck Bunny"


class _Resp:
    def __init__(self, status=200, data=None, body=b""):
        self.status_code = status
        self._data = data
        self._body = body

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise osub.requests.HTTPError(str(self.status_code))

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]


class _Session:
    """Answers by path; records every request."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def request(self, method, url, headers=None, timeout=None, **kw):
        self.calls.append((method, url, headers, kw))
        answer = self.routes[(method, url.split("/api/v1", 1)[1])]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def get(self, url, **kw):
        self.calls.append(("FILE", url, kw.get("headers"), {}))
        return self.routes[("FILE", url)]


def test_search_sends_the_key_the_app_and_sorted_parameters():
    s = _Session({("GET", "/subtitles"): _Resp(data={"data": [_hit(9)]})})
    c = osub.OpenSubtitles(" k3y ", session=s)
    rows = c.search(languages="sv,en", query="Ignored When Ids",
                    parent_tmdb_id=1399, season=2, episode=5)
    assert [r["file_id"] for r in rows] == [9]
    method, url, headers, kw = s.calls[0]
    assert url == "https://api.opensubtitles.com/api/v1/subtitles"
    assert headers["Api-Key"] == "k3y"
    assert headers["User-Agent"] == f"dopeIPTV v{VERSION}"
    assert "Authorization" not in headers
    assert kw["params"] == [("episode_number", 5), ("languages", "en,sv"),
                            ("parent_tmdb_id", 1399), ("season_number", 2)]
    c.search(languages="sv", query="  Big Buck BUNNY ", moviehash="ab" * 8)
    assert s.calls[1][3]["params"] == [
        ("languages", "sv"), ("moviehash", "ab" * 8),
        ("query", "big buck bunny")]
    c.search(tmdb_id=10378)
    assert s.calls[2][3]["params"] == [("tmdb_id", 10378)]


def test_errors_say_what_went_wrong():
    s = _Session({
        ("GET", "/subtitles"): _Resp(401, data={}),
        ("POST", "/download"): _Resp(406, data={
            "message": "You have downloaded your allowed 5 subtitles"}),
    })
    c = osub.OpenSubtitles("k", session=s)
    with pytest.raises(LocalizedError) as e:
        c.search(query="x")
    assert e.value.key == "subs_err_key"
    with pytest.raises(LocalizedError) as e:
        c.download(1)
    assert "allowed 5 subtitles" in e.value.localized()
    s.routes[("GET", "/subtitles")] = osub.requests.ConnectionError("dns")
    with pytest.raises(LocalizedError) as e:
        c.search(query="x")
    assert e.value.key == "subs_err_network"
    s.routes[("GET", "/subtitles")] = _Resp(502, data=None)
    with pytest.raises(LocalizedError) as e:
        c.search(query="x")
    assert e.value.key == "subs_err_http"
    with pytest.raises(LocalizedError) as e:
        osub.OpenSubtitles("", session=s).search(query="x")
    assert e.value.key == "subs_need_key"


def test_download_signs_in_once_and_fetches_the_file():
    link = "https://dl.opensubtitles.com/file/123.srt"
    s = _Session({
        ("POST", "/login"): _Resp(data={"token": "T0K",
                                        "base_url": "vip-api.opensubtitles.com"}),
        ("POST", "/download"): _Resp(data={"link": link, "file_name": "a.srt",
                                           "remaining": 19}),
        ("FILE", link): _Resp(body=b"1\n00:00:01,000 --> 00:00:02,000\nHej\n"),
    })
    c = osub.OpenSubtitles("k", "me", "pw", session=s)
    name, data = c.download(123)
    assert name == "a.srt" and data.startswith(b"1\n00:00:01")
    assert c.remaining == 19
    login, dl = s.calls[0], s.calls[1]
    assert login[3]["json"] == {"username": "me", "password": "pw"}
    # A paying member's own host, and the token, from then on.
    assert dl[1] == "https://vip-api.opensubtitles.com/api/v1/download"
    assert dl[2]["Authorization"] == "Bearer T0K"
    assert dl[3]["json"] == {"file_id": 123}
    c.download(123)
    assert [x[1].rsplit("/", 1)[-1] for x in s.calls].count("login") == 1
    # The last one of the day says so.
    s.routes[("POST", "/download")] = _Resp(data={"link": link,
                                                  "remaining": 0})
    c.download(123)
    assert c.remaining == 0


def test_an_expired_sign_in_is_renewed_once():
    link = "https://dl.opensubtitles.com/file/1.srt"
    answers = [_Resp(401, data={"message": "token expired"}),
               _Resp(data={"link": link})]

    class S(_Session):
        def request(self, method, url, headers=None, timeout=None, **kw):
            if url.endswith("/download"):
                self.calls.append((method, url, headers, kw))
                return answers.pop(0)
            return super().request(method, url, headers, timeout, **kw)

    s = S({("POST", "/login"): _Resp(data={"token": "NEW"}),
           ("FILE", link): _Resp(body=b"1")})
    c = osub.OpenSubtitles("k", "me", "pw", session=s)
    c._token = "OLD"
    assert c.download(1) == ("1.srt", b"1")
    tokens = [x[2].get("Authorization") for x in s.calls
              if x[1].endswith("/download")]
    assert tokens == ["Bearer OLD", "Bearer NEW"]


def test_a_login_cannot_send_the_token_elsewhere():
    s = _Session({("POST", "/login"): _Resp(data={
        "token": "T", "base_url": "evilopensubtitles.com"})})
    c = osub.OpenSubtitles("k", "me", "pw", session=s)
    c.login()
    assert c._base == osub.API
    s.routes[("POST", "/login")] = _Resp(data={
        "token": "T", "base_url": "opensubtitles.com.evil.net"})
    c.login()
    assert c._base == osub.API


def test_a_download_that_is_not_a_subtitle_is_refused():
    s = _Session({("POST", "/download"): _Resp(data={"link": "http://x/a"})})
    c = osub.OpenSubtitles("k", session=s)
    with pytest.raises(LocalizedError) as e:
        c.download(1)
    assert e.value.key == "subs_err_bad_file"
    big = "https://dl.opensubtitles.com/big"
    s.routes[("POST", "/download")] = _Resp(data={"link": big})
    s.routes[("FILE", big)] = _Resp(body=b"x" * (osub.MAX_BYTES + 10))
    with pytest.raises(LocalizedError) as e:
        c.download(1)
    assert e.value.key == "subs_err_bad_file"


def test_saved_subtitles_are_found_again(tmp_path, monkeypatch):
    pytest.importorskip("PyQt6")
    from dopeiptv.ui import subtitle_search as ss
    monkeypatch.setattr(ss, "subtitle_dir", lambda: tmp_path / "subs")
    row = {"file_id": 4242, "release": "Big.Buck.Bunny/2008:1080p",
           "language": "sv"}
    path = ss.save_subtitle(row, "whatever.SRT", b"data")
    assert path.name == "Big.Buck.Bunny_2008_1080p [4242].sv.srt"
    assert path.read_bytes() == b"data"
    assert ss.cached_file(4242) == path
    assert ss.cached_file(424) is None
    odd = ss.save_subtitle({"file_id": 7, "release": "", "language": "pt-BR"},
                           "x.exe!", b"d")
    assert odd.name == "subtitle [7].pt-BR.srt"


def test_language_names_are_their_own():
    pytest.importorskip("PyQt6")
    from dopeiptv.ui.subtitle_search import language_name
    assert language_name("sv") == "Svenska"
    assert language_name("de") == "Deutsch"
    # Not the language of one country: "American English", "español de
    # España" is what Qt calls these.
    assert language_name("en") == "English"
    assert language_name("es") == "Español"
    assert language_name("pt-BR") == "Português (Brasil)"
    assert language_name("zz") == "zz"


class FakeSettings:
    def __init__(self, **values):
        self._v = dict(values)

    def value(self, key, default=None):
        return self._v.get(key, default)

    def setValue(self, key, val):
        self._v[key] = val


def _window(lp, tmp_path=None, **settings):
    pytest.importorskip("PyQt6")
    from types import SimpleNamespace

    from dopeiptv.ui.mw_subtitles import _SubtitlesMixin

    class Win(_SubtitlesMixin):
        _RESUMABLE = ("movie", "episode", "recording", "local")

        def __init__(self):
            self.settings = FakeSettings(**settings)
            self._last_playback = lp
            self.tmdb = SimpleNamespace(
                tmdb_id_for=lambda title, kind: 1399 if (
                    title, kind) == ("Game of Thrones", "series") else None)

        def _tmdb_id_for_item(self, item, kind):
            return 10378 if item.get("name", "").startswith("EN") else None

    return Win()


def test_the_search_knows_what_is_playing(tmp_path):
    movie = _window({"kind": "movie", "key": 7, "item": {
        "name": "EN - Big Buck Bunny (2008) [MULTI]"}})
    ctx = movie._subtitle_ctx()
    assert ctx["query"] == "Big Buck Bunny" and ctx["tmdb_id"] == 10378
    ep = _window({"kind": "episode", "key": 55,
                  "item": {"season": "2", "episode_num": "5"},
                  "series_ctx": {"name": "Game of Thrones"}})
    ctx = ep._subtitle_ctx()
    assert ctx["parent_tmdb_id"] == 1399
    assert (ctx["season"], ctx["episode"]) == (2, 5)
    assert ctx["query"] == "Game of Thrones S02E05"
    f = tmp_path / "Sintel.2010.mkv"
    f.write_bytes(b"x")
    local = _window({"kind": "local", "key": str(f), "url": str(f),
                     "item": {"_path": str(f), "_clean_title": "Sintel"}})
    ctx = local._subtitle_ctx()
    assert ctx["path"] == str(f) and ctx["query"] == "Sintel"
    assert _window({"kind": "live", "item": {}})._subtitle_ctx() is None


def test_a_subtitle_comes_back_with_its_title(tmp_path):
    sub = tmp_path / "a [1].sv.srt"
    sub.write_text("x")
    w = _window({"kind": "movie", "key": 7, "item": {}})
    w._on_subtitle_added(str(sub))
    assert w._remembered_subtitles("movie", 7) == [str(sub)]
    assert w._remembered_subtitles("movie", 8) == []
    sub.unlink()                    # the cache was cleared
    assert w._remembered_subtitles("movie", 7) == []
    live = _window({"kind": "live", "key": 1, "item": {}})
    live._on_subtitle_added(str(sub))
    assert live._subtitle_memory() == {}


def test_the_key_in_settings_wins_and_the_client_is_kept(monkeypatch):
    monkeypatch.setattr("dopeiptv.ui.mw_subtitles.bundled_key", lambda: "")
    w = _window({})
    assert w._subtitle_client() is None
    w.settings.setValue("os_api_key", "mine")
    c = w._subtitle_client()
    assert c.api_key == "mine" and w._subtitle_client() is c
    w.settings.setValue("os_username", "me")
    assert w._subtitle_client() is not c
    monkeypatch.setattr("dopeiptv.ui.mw_subtitles.bundled_key",
                        lambda: "built-in")
    w2 = _window({})
    assert w2._subtitle_client().api_key == "built-in"


_PLAYER_CHILD = r"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication, QMenu
from dopeiptv.i18n import tr
from dopeiptv.media.embedded import EmbeddedPlayer

app = QApplication.instance() or QApplication([])
p = EmbeddedPlayer()


class FakeMpv(dict):
    pause = False
    track_list = []

    def __init__(self):
        super().__init__()
        self.cmds = []

    def command(self, *args):
        self.cmds.append(args)

    def play(self, url):
        self.cmds.append(("play", url))

    def __getattr__(self, n):
        return None


m = p.video.mpv = FakeMpv()
added = []
p.subtitle_added.connect(added.append)

# Nothing playing: nothing to add a subtitle to.
assert p.add_subtitle("/tmp/a.srt") is False

# A remembered subtitle rides along with the next play, as one list item
# however odd its path, and is shown.
p.set_sub_files(["/tmp/odd:name,1.srt"])
assert p.play("http://x/movie/1.mkv", "Film")
assert ("change-list", "sub-files", "clr", "") in m.cmds
assert ("change-list", "sub-files", "append", "/tmp/odd:name,1.srt") in m.cmds
assert m["sid"] == "auto" and m["sub-visibility"] is True
assert m.cmds.index(("change-list", "sub-files", "append",
                     "/tmp/odd:name,1.srt")) < m.cmds.index(
    ("play", "http://x/movie/1.mkv"))

# One-shot: the next stream starts without it.
m.cmds.clear()
p.play("http://x/movie/2.mkv", "Other")
assert ("change-list", "sub-files", "clr", "") in m.cmds
assert not [c for c in m.cmds if c[:3] == ("change-list", "sub-files", "append")]

# Loaded while playing: selected, shown, announced.
assert p.add_subtitle("/tmp/b.srt", "Rel", "sv")
assert ("sub-add", "/tmp/b.srt", "select", "Rel", "sv") in m.cmds
assert added == ["/tmp/b.srt"]


# mpv 0.37 renamed sub-forced-only to sub-forced-events-only. On a newer mpv
# (Homebrew's, the macOS build's) setting the old name raised, and a
# subtitle that had loaded fine was reported as "not a usable file".
class NewMpv(FakeMpv):
    def __setitem__(self, key, value):
        if key == "sub-forced-only":
            raise AttributeError(key)
        super().__setitem__(key, value)


class OldMpv(FakeMpv):
    def __setitem__(self, key, value):
        if key == "sub-forced-events-only":
            raise AttributeError(key)
        super().__setitem__(key, value)


new = p.video.mpv = NewMpv()
assert p.add_subtitle("/tmp/c.srt", "Rel", "en")
assert new["sub-forced-events-only"] is False
assert new["sub-visibility"] is True
old = p.video.mpv = OldMpv()
assert p.add_subtitle("/tmp/d.srt")
assert old["sub-forced-only"] is False
# The same for a subtitle remembered with its title, and for the
# "forced subtitles only" setting.
p.video.mpv = new
p.set_sub_files(["/tmp/e.srt"])
assert p.play("http://x/movie/3.mkv", "Film")
assert new["sub-forced-events-only"] is False
assert new["sub-visibility"] is True
p.video.mpv = m

# The Subtitles menu offers a file always, the search for seekable titles.
p.find_subtitles = lambda: None
def entries():
    menu = QMenu()
    p.populate_options_menu(menu)
    subs = [a for a in menu.actions() if a.text() == tr("opt_subtitles")][0]
    return [a.text() for a in subs.menu().actions()]
assert tr("opt_sub_load_file") in entries()
assert tr("opt_sub_find_online") in entries()
p._seek_mode = "live"
assert tr("opt_sub_find_online") not in entries()
assert tr("opt_sub_load_file") in entries()
print("PLAYER_SUBS_OK")
"""


def test_the_player_loads_and_remembers_subtitle_files():
    pytest.importorskip("PyQt6")
    proc = subprocess.run(
        [sys.executable, "-c", _PLAYER_CHILD], capture_output=True, text=True,
        cwd=_REPO_ROOT, timeout=180,
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
    assert "PLAYER_SUBS_OK" in proc.stdout, (
        f"stdout={proc.stdout!r}\nstderr={proc.stderr[-2500:]!r}")
