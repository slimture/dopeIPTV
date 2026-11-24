"""Downloads (core/downloads.py), against a provider of our own on this
computer: a film downloaded whole; carried on with Range after a cut; a
server that ignores Range (read from its start again, or - cut too often -
given up and said); a page instead of a video refused; the space limit and
the disk's floor; cancel takes the .part away; a hostile title never names
a file outside the folder; the sidecar holds no address and no login;
private viewing refuses and pauses; the queue kept in the settings, never
its address."""
from __future__ import annotations

import json
import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dopeiptv.core import downloads as dl
from dopeiptv.core import privacy

USER, PASSWORD = "kund4711", "hemligt-lösen-9"
#: A film: an MP4's first box, then bytes no text has.
FILM = b"\x00\x00\x00\x20ftypisom" + random.Random(7).randbytes(3 * 1024 * 1024)
PAGE = (b"<!DOCTYPE html><html><head><title>Not found</title></head><body>"
        + b"<p>The film is not here.</p>" * 50 + b"</body></html>")


class Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value


class Provider(ThreadingHTTPServer):
    """The film at /movie/<user>/<password>/9001.mp4 - with Range unless
    *ignore_range*; the first *cuts* answers end after *cut_at* bytes."""
    daemon_threads = True

    def __init__(self) -> None:
        self.data = FILM
        self.ignore_range = False
        self.cuts = 0
        self.cut_at = 512 * 1024
        self.ranges: list[str] = []
        self.paths: list[str] = []
        self.hold = threading.Event()
        self.held = False
        super().__init__(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()

    def url(self, path: str = f"/movie/{USER}/{PASSWORD}/9001.mp4") -> str:
        return f"http://127.0.0.1:{self.server_address[1]}{path}"


class _Handler(BaseHTTPRequestHandler):
    server: Provider
    protocol_version = "HTTP/1.1"

    def log_message(self, *a) -> None:
        pass

    def do_GET(self) -> None:
        srv = self.server
        srv.paths.append(self.path)
        if self.path.startswith("/page"):
            self._answer(200, PAGE, "text/html; charset=utf-8")
            return
        if self.path.startswith("/error"):
            self._answer(200, b"Error: the account has no free connection right now.\n" * 3,
                         "application/octet-stream")
            return
        if self.path.startswith("/gone"):
            self._answer(404, b"no", "text/plain")
            return
        data = srv.data
        rng = self.headers.get("Range")
        srv.ranges.append(rng or "")
        start = 0
        if rng and not srv.ignore_range:
            start = int(rng.split("=", 1)[1].split("-", 1)[0])
            if start >= len(data):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(data)}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(data) - start))
        self.end_headers()
        body = data[start:]
        try:
            if srv.cuts > 0:
                srv.cuts -= 1
                self.wfile.write(body[:srv.cut_at])
                self.wfile.flush()
                self.close_connection = True
                return
            if srv.held:
                self.wfile.write(body[:256 * 1024])
                self.wfile.flush()
                srv.hold.wait(10)
            self.wfile.write(body)
        except OSError:
            pass

    def _answer(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def provider():
    p = Provider()
    yield p
    p.hold.set()
    p.shutdown()
    p.server_close()


@pytest.fixture(autouse=True)
def _not_private():
    privacy.set_private(False)
    yield
    privacy.set_private(False)


def _manager(tmp_path, url, **kw) -> tuple[dl.Downloads, Settings]:
    s = Settings({dl.DIR_KEY: str(tmp_path / "Downloads")})
    asked: list = []

    def resolve(job):
        asked.append(job.id)
        return (url() if callable(url) else url), None

    m = dl.Downloads(s, resolve=resolve, gated=False, free=lambda _p: 10 ** 13, **kw)
    m.asked = asked
    m.start()
    return m, s


def _until(cond, secs: float = 20.0) -> bool:
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def _film(m: dl.Downloads, **kw) -> dl.Job:
    job = m.add("movie", "a1b2c3d4", 9001, key=9001, title=kw.pop("title", "Hemma"), ext="mp4",
                meta={"year": "2021", "plot": "Ett hus vid havet.", "length": 5400,
                      "category_id": "12", **kw.pop("meta", {})}, **kw)
    assert job is not None
    return job


def _done(m: dl.Downloads) -> bool:
    return m.find("a1b2c3d4", "movie", "9001") is not None


def test_a_film_downloaded_whole(tmp_path, provider):
    m, s = _manager(tmp_path, provider.url())
    try:
        _film(m)
        assert _until(lambda: _done(m)), m.jobs()
        item = m.find("a1b2c3d4", "movie", "9001")
        assert open(item.path, "rb").read() == FILM
        assert item.path.endswith(os.path.join("Movies", "Hemma (2021).mp4"))
        assert item.size == len(FILM) and not m.jobs()
        assert not [f for f in os.listdir(os.path.dirname(item.path)) if f.endswith(".part")]
        events = m.take_events()
        assert [e[0] for e in events] == ["done"]
        # The queue kept nothing of it once done, and never its address.
        m.save()
        assert json.loads(s[dl.QUEUE_KEY]) == []
        # Read back from the disk alone (the disk is the index).
        again = dl.Downloads(s, gated=False)
        again.scan()
        found = again.find("a1b2c3d4", "movie", 9001)
        assert found is not None and found.title == "Hemma" and found.key == "9001"
        row = dl.as_row(found)
        assert row["stream_id"] == "9001" and row["_dl_path"] == item.path and row["year"] == "2021"
    finally:
        m.close()


def test_the_sidecar_holds_no_address_and_no_login(tmp_path, provider):
    m, _s = _manager(tmp_path, provider.url())
    try:
        _film(m, meta={"plot": f"See http://127.0.0.1/{USER}/{PASSWORD} for more."},
              poster=provider.url(f"/images/{USER}.jpg"))
        assert _until(lambda: _done(m))
        item = m.find("a1b2c3d4", "movie", "9001")
        text = open(item.path + dl.SIDECAR, encoding="utf-8").read()
        for word in (USER, PASSWORD, "127.0.0.1", "http", "://", str(provider.server_address[1])):
            assert word not in text, (word, text)
        info = json.loads(text)
        assert info["kind"] == "movie" and info["source"] == "a1b2c3d4"
        assert info["stream_id"] == "9001" and info["title"] == "Hemma"
        assert set(info) <= {"format", "kind", "source", "stream_id", "key", "title", "file",
                             "size", "added", "poster", "series_poster", *dl.SIDECAR_FIELDS}
        # The queue in the settings keeps no address either (asked at start).
        assert all(provider.url() not in json.dumps(j.to_dict()) for j in m.jobs())
    finally:
        m.close()


def test_carried_on_with_range_after_a_cut(tmp_path, provider):
    provider.cuts = 2
    m, _s = _manager(tmp_path, provider.url())
    try:
        _film(m)
        assert _until(lambda: _done(m), 40), m.jobs()
        item = m.find("a1b2c3d4", "movie", "9001")
        assert open(item.path, "rb").read() == FILM
        assert provider.ranges[0] == ""
        assert provider.ranges[1] == f"bytes={provider.cut_at}-"
        assert provider.ranges[2] == f"bytes={2 * provider.cut_at}-"
        assert len(m.asked) == 1                     # its address asked once
    finally:
        m.close()


def test_a_server_that_ignores_range_is_read_from_its_start(tmp_path, provider):
    provider.ignore_range = True
    provider.cuts = 1
    m, _s = _manager(tmp_path, provider.url())
    try:
        _film(m)
        assert _until(lambda: _done(m), 40), m.jobs()
        assert open(m.find("a1b2c3d4", "movie", "9001").path, "rb").read() == FILM
        assert provider.ranges[1].startswith("bytes=")   # asked to carry on, sent it all
    finally:
        m.close()


def test_cut_too_often_by_a_server_that_cannot_carry_on_is_said(tmp_path, provider):
    provider.ignore_range = True
    provider.cuts = 99
    m, _s = _manager(tmp_path, provider.url())
    try:
        job = _film(m)
        assert _until(lambda: (m.job(job.id) or job).state == dl.FAILED, 60), m.jobs()
        failed = m.job(job.id)
        assert failed.why == dl.WHY_RESUME
        assert not _done(m)
        assert [e[0] for e in m.take_events()] == ["failed"]
    finally:
        m.close()


@pytest.mark.parametrize("path, what", [("/page.mp4", "a web page"), ("/error.mp4", "text")])
def test_a_page_instead_of_a_video_is_refused(tmp_path, provider, path, what):
    m, _s = _manager(tmp_path, provider.url(path))
    try:
        job = _film(m)
        assert _until(lambda: m.job(job.id).state == dl.FAILED)
        failed = m.job(job.id)
        assert (failed.why, failed.detail) == (dl.WHY_NOT_VIDEO, what)
        assert not _done(m)
        assert not any(f.endswith((".mp4", ".mkv")) for _r, _d, fs in os.walk(tmp_path)
                       for f in fs)
    finally:
        m.close()


def test_the_servers_no_is_said(tmp_path, provider):
    m, _s = _manager(tmp_path, provider.url("/gone/1.mp4"))
    try:
        job = _film(m)
        assert _until(lambda: m.job(job.id).state == dl.FAILED)
        assert (m.job(job.id).why, m.job(job.id).detail) == (dl.WHY_STATUS, "404")
    finally:
        m.close()


def test_the_space_limit_and_the_disks_floor(tmp_path, provider):
    m, s = _manager(tmp_path, provider.url())
    try:
        m.set_cap_gb(0.001)                         # 1 MB: a 3 MB film is past it
        job = _film(m)
        assert _until(lambda: m.job(job.id).state == dl.FAILED)
        assert m.job(job.id).why == dl.WHY_CAP and s[dl.CAP_KEY] == "0.001"
        m.set_cap_gb(0)                             # no limit: the disk's floor stands
        m._free = lambda _p: m.floor + 1024 * 1024
        m.resume(job.id)
        assert _until(lambda: m.job(job.id).state == dl.FAILED and m.job(job.id).why == dl.WHY_DISK)
        m._free = lambda _p: 10 ** 13
        m.resume(job.id)
        assert _until(lambda: _done(m))
        assert m.used_bytes() == len(FILM)
    finally:
        m.close()


def test_cancel_takes_the_part_away_and_pause_keeps_it(tmp_path, provider):
    provider.held = True
    m, _s = _manager(tmp_path, provider.url())
    try:
        job = _film(m)
        assert _until(lambda: (m.job(job.id).done or 0) > 0)
        part = m.job(job.id).part
        assert part.endswith(dl.PART) and os.path.exists(part)
        m.pause(job.id)
        assert _until(lambda: m.job(job.id).state == dl.PAUSED)
        assert os.path.exists(part)                 # kept: carried on from here
        m.resume(job.id)
        assert _until(lambda: m.job(job.id).state == dl.RUNNING)
        m.cancel(job.id)
        assert _until(lambda: m.job(job.id) is None)
        assert _until(lambda: not os.path.exists(part))
        assert not _done(m)
    finally:
        m.close()


@pytest.mark.parametrize("title", ["../../../../etc/passwd", "..", "/tmp/x", "a\x00b",
                                   "C:\\Windows\\win.ini", "CON", "x" * 600])
def test_a_hostile_title_never_names_a_file_outside_the_folder(tmp_path, provider, title):
    m, _s = _manager(tmp_path, provider.url())
    try:
        _film(m, title=title, meta={"year": ""})
        assert _until(lambda: _done(m)), m.jobs()
        item = m.find("a1b2c3d4", "movie", "9001")
        root = os.path.realpath(str(tmp_path / "Downloads"))
        assert os.path.commonpath([root, os.path.realpath(item.path)]) == root
        assert os.path.dirname(item.path) == os.path.join(root, dl.MOVIES_DIR)
        assert len(os.path.basename(item.path).encode()) < 255
    finally:
        m.close()


def test_a_link_on_the_way_is_not_followed(tmp_path, provider):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    root = tmp_path / "Downloads"
    root.mkdir()
    os.symlink(outside, root / dl.MOVIES_DIR)
    m, _s = _manager(tmp_path, provider.url())
    try:
        job = _film(m)
        assert _until(lambda: m.job(job.id).state == dl.FAILED)
        assert m.job(job.id).why == dl.WHY_FOLDER and not os.listdir(outside)
    finally:
        m.close()


def test_private_viewing_refuses_and_pauses(tmp_path, provider):
    provider.held = True
    m, _s = _manager(tmp_path, provider.url())
    try:
        job = _film(m)
        assert _until(lambda: (m.job(job.id).done or 0) > 0)
        privacy.set_private(True)
        m.private_on()
        assert _until(lambda: m.job(job.id).state == dl.WAITING)
        assert m.add("episode", "a1b2c3d4", 5, title="Avsnitt") is None   # nothing new
        time.sleep(0.5)
        assert m.job(job.id).state == dl.WAITING and m.current() is None
        provider.held = False
        privacy.set_private(False)
        m.wake()
        assert _until(lambda: _done(m), 30)
        assert open(m.find("a1b2c3d4", "movie", "9001").path, "rb").read() == FILM
    finally:
        m.close()


def test_a_job_waits_for_the_windows_word(tmp_path, provider):
    s = Settings({dl.DIR_KEY: str(tmp_path / "Downloads")})
    m = dl.Downloads(s, resolve=lambda _j: (provider.url(), None), free=lambda _p: 10 ** 13)
    m.start()
    try:
        job = _film(m)
        time.sleep(0.6)
        assert m.job(job.id).state == dl.WAITING and not provider.paths    # not said: waits
        m.set_holds({job.id: "connection"})
        time.sleep(0.6)
        assert not provider.paths
        m.set_holds({job.id: ""})
        assert _until(lambda: _done(m))
    finally:
        m.close()


def test_the_queue_survives_a_restart_and_carries_on(tmp_path, provider):
    provider.held = True
    m, s = _manager(tmp_path, provider.url())
    job = _film(m)
    assert _until(lambda: (m.job(job.id).done or 0) > 0)
    m.close()
    m.save()
    kept = json.loads(s[dl.QUEUE_KEY])
    assert [k["id"] for k in kept] == [job.id] and kept[0]["state"] == dl.WAITING
    assert provider.url() not in s[dl.QUEUE_KEY] and PASSWORD not in s[dl.QUEUE_KEY]
    provider.held = False
    provider.ranges.clear()
    m2 = dl.Downloads(s, resolve=lambda _j: (provider.url(), None), gated=False,
                      free=lambda _p: 10 ** 13)
    m2.start()
    try:
        assert _until(lambda: m2.find("a1b2c3d4", "movie", "9001") is not None)
        assert provider.ranges[0].startswith("bytes=")          # carried on
        assert open(m2.find("a1b2c3d4", "movie", "9001").path, "rb").read() == FILM
    finally:
        m2.close()


def test_an_episode_into_its_series_folder_and_deleted(tmp_path, provider):
    pictures = {"https://img.example/show.jpg": b"\xff\xd8\xff\xe0" + b"j" * 200}
    m, _s = _manager(tmp_path, provider.url(), picture=pictures.get)
    try:
        job = m.add("episode", "a1b2c3d4", "501", key="501", title="S1 E2 - Piloten",
                    meta={"series_id": "77", "series_title": "Huset", "season": 1, "episode": 2,
                          "episode_title": "Piloten", "series_plot": "Om ett hus."},
                    series_poster="https://img.example/show.jpg")
        assert job is not None
        assert _until(lambda: m.find("a1b2c3d4", "episode", "501") is not None)
        item = m.find("a1b2c3d4", "episode", "501")
        assert item.path.endswith(os.path.join("Series", "Huset", "Huset S01E02 - Piloten.mp4"))
        assert os.path.basename(item.series_poster) == "series.poster.jpg"
        assert open(item.series_poster, "rb").read() == pictures["https://img.example/show.jpg"]
        row = dl.as_row(item)
        assert row["_series_ctx"]["series_id"] == "77" and row["_series_ctx"]["name"] == "Huset"
        assert row["season"] == 1 and row["episode_num"] == 2
        folder = os.path.dirname(item.path)
        assert m.delete("a1b2c3d4", "episode", "501")
        assert not os.path.exists(folder)           # nothing of it left
        assert m.find("a1b2c3d4", "episode", "501") is None
        assert not m.delete("a1b2c3d4", "episode", "501")
    finally:
        m.close()


def test_a_damaged_queue_and_sidecar_are_passed_over(tmp_path):
    s = Settings({dl.DIR_KEY: str(tmp_path), dl.QUEUE_KEY: json.dumps(
        [1, {"kind": "movie"}, {"kind": "live", "source": "a", "stream_id": "1"},
         {"kind": "movie", "source": "a", "stream_id": "1", "state": "running", "done": -5},
         {"kind": "movie", "source": "a", "stream_id": "1"}])})
    m = dl.Downloads(s, gated=False)
    jobs = m.jobs()
    assert len(jobs) == 1 and jobs[0].state == dl.WAITING and jobs[0].done == 0
    (tmp_path / "x.mp4").write_bytes(b"x" * 10)
    (tmp_path / "x.mp4.dl.json").write_text("{not json")
    (tmp_path / "y.mp4").write_bytes(b"x" * 10)
    (tmp_path / "y.mp4.dl.json").write_text(json.dumps({"kind": "movie", "source": "a",
                                                         "stream_id": "2",
                                                         "poster": "../../etc/passwd"}))
    m.scan()
    (only,) = m.items()
    assert only.stream_id == "2" and only.poster == ""


@pytest.mark.parametrize("bad", ["1e999", "NaN", "-1e999", "1" + "0" * 400, "-5", "true",
                                 '"12"', "[1]"])
def test_one_bad_number_in_a_sidecar_or_the_queue_hides_nothing(tmp_path, bad):
    """JSON allows 1e999, NaN and 400 digits: in a damaged or edited
    sidecar's added, size or format - or the queue's - they are no number,
    and every download is still listed, sorted, grouped into its series."""
    from dopeiptv.providers.downloaded import DownloadedClient
    series = tmp_path / "Series" / "Show"
    series.mkdir(parents=True)
    for name, raw in (
            ("good.mkv", {"format": 1, "kind": "movie", "source": "abc", "stream_id": "1",
                          "title": "Good", "added": 5}),
            ("again.mkv", None),           # the same film again: the scan compares them
            ("Series/Show/e.mkv", None)):
        (tmp_path / name).write_bytes(b"x" * 2000)
        text = json.dumps(raw) if raw else (
            '{"format":%s,"kind":"movie","source":"abc","stream_id":"1","title":"Again",'
            '"added":%s,"size":%s}' % (bad, bad, bad) if name == "again.mkv" else
            '{"format":1,"kind":"episode","source":"abc","stream_id":"9","title":"E",'
            '"series_id":"7","series_title":"Show","added":%s,"size":%s}' % (bad, bad))
        (tmp_path / (name + dl.SIDECAR)).write_text(text)
    s = Settings({dl.DIR_KEY: str(tmp_path), dl.QUEUE_KEY: json.dumps(
        [{"kind": "movie", "source": "abc", "stream_id": "2", "title": "Queued"}]).replace(
        '"title"', '"added": %s, "title"' % bad)})
    m = dl.Downloads(s, gated=False)
    (job,) = m.jobs()
    assert job.title == "Queued" and isinstance(job.added, float) and 0 <= job.added < 1e12
    m.scan()
    assert m.scanned and sorted(i.kind for i in m.items()) == ["episode", "movie"]
    for i in m.items():
        assert isinstance(i.added, float) and 0 <= i.added < 1e12
        for k in ("added", "size", "format"):
            v = i.info.get(k)
            assert v is None or (isinstance(v, (int, float)) and 0 <= v <= 1e15), (k, v)
    assert sorted(m.items(), key=lambda i: -i.added)
    c = DownloadedClient(m, "abc")
    assert [r["name"] for r in c.series_list()] == ["Show"]
    assert c.series_list()[0]["last_modified"].isdigit()
    assert len(c.vod_streams()) == 1
    assert dl.Item("/x", 1, {"added": 10 ** 400}).added == 0.0
    assert dl.Item("/x", 1, {"added": float("nan")}).added == 0.0


def test_a_source_name_with_a_login_never_goes_into_a_sidecar(tmp_path):
    """A source an older release named "user:password@host" (core/names):
    its name is left out of what a sidecar keeps - and a sidecar a test
    build wrote with it is written again without it as the folder is read."""
    from dopeiptv.core import site_out
    for name in ("kund:hemligt@line.example.com:8080", "kund@line.example",
                 "line.example?username=kund&password=x", "http://kund:x@line.example"):
        assert "source_name" not in dl.clean_meta({"source_name": name}), name
    assert dl.clean_meta({"source_name": "Pirat"}) == {"source_name": "Pirat"}
    site_out.forget_for_tests()
    try:
        site_out.learn([{"username": "kund4711", "password": "hemligt-lösen-9"}])
        assert "source_name" not in dl.clean_meta({"source_name": "kund4711 at home"})
    finally:
        site_out.forget_for_tests()
    (tmp_path / "f.mkv").write_bytes(b"x" * 2000)
    side = tmp_path / ("f.mkv" + dl.SIDECAR)
    side.write_text(json.dumps({"format": 1, "kind": "movie", "source": "abc", "stream_id": "1",
                                "title": "Film", "source_name": "kund:hemligt@line.example.com"}))
    m = dl.Downloads(Settings({dl.DIR_KEY: str(tmp_path)}), gated=False)
    m.scan()
    (item,) = m.items()
    assert "source_name" not in item.info and item.title == "Film"
    text = side.read_text()
    assert "hemligt" not in text and "kund" not in text and json.loads(text)["title"] == "Film"


def test_clean_meta_keeps_only_what_a_sidecar_may():
    got = dl.clean_meta({"year": 2021, "plot": "Se https://x.example/u/p nu", "length": "5400",
                         "password": "x", "url": "http://a/b", "rating": "7.4", "season": True,
                         "episode": "3", "category_id": 12})
    assert got == {"year": "2021", "plot": "Se nu", "length": 5400, "rating": 7.4,
                   "episode": "3", "category_id": "12"}


def test_downloads_are_never_synced_nor_in_a_backup():
    from dopeiptv.core import backup, sync
    for key in (dl.QUEUE_KEY, dl.DIR_KEY, dl.CAP_KEY, "downloads_view", "downloads_sort"):
        assert not sync.syncable(key), key
        assert backup.left_out(key), key


def test_the_folder_is_the_platforms_usual_videos_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert dl.default_folder("darwin") == str(tmp_path / "Movies" / "dopeIPTV Downloads")
    assert dl.default_folder("win32") == str(tmp_path / "Videos" / "dopeIPTV Downloads")
    assert dl.default_folder("linux") == str(tmp_path / "Videos" / "dopeIPTV Downloads")
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "user-dirs.dirs").write_text('XDG_VIDEOS_DIR="$HOME/Filmer"\n')
    assert dl.default_folder("linux") == str(tmp_path / "Filmer" / "dopeIPTV Downloads")
    # Never inside the recordings' own folder (~/Videos/dopeIPTV), which lists
    # what lies under it as recordings.
    assert not dl.default_folder("win32").startswith(str(tmp_path / "Videos" / "dopeIPTV") + os.sep)


def test_a_download_under_the_recordings_folder_is_no_recording(tmp_path):
    from PyQt6.QtCore import QSettings
    from dopeiptv.core.recording import RecordingManager
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.setValue("recordings_dir", str(tmp_path / "rec"))
    (tmp_path / "rec" / "dl").mkdir(parents=True)
    (tmp_path / "rec" / "News 2026-01-01 21.00.ts").write_bytes(b"x" * 10)
    (tmp_path / "rec" / "dl" / "Film.mp4").write_bytes(b"x" * 10)
    (tmp_path / "rec" / "dl" / "Film.mp4.dl.json").write_text("{}")
    rec = RecordingManager(s)
    try:
        assert [f["_filename"] for f in rec.files(None)] == ["News 2026-01-01 21.00"]
    finally:
        rec._timer.stop()


# -- the pace and the rest between downloads -------------------------------------------

def test_the_pace_is_a_few_times_its_bit_rate_within_its_bounds():
    # 1.5 GB over two hours: five times its 1.7 Mbit/s.
    assert dl.pace_rate(1_500_000_000, 7200) == pytest.approx(5 * 1_500_000_000 / 7200)
    assert dl.pace_rate(100_000_000, 7200) == dl.PACE_MIN
    assert dl.pace_rate(40_000_000_000, 7200) == dl.PACE_MAX
    for total, length in ((None, 7200), (1_500_000_000, None), (0, 7200), (1_500_000_000, 0),
                          ("x", "1e999"), (1_500_000_000, 10), (float("nan"), 7200),
                          (True, 7200), (1_500_000_000, "Infinity")):
        assert dl.pace_rate(total, length) == dl.PACE_UNKNOWN


def test_the_pace_lets_its_start_through_then_waits_and_earns_no_burst():
    now = [100.0]
    pace = dl.Pace(1000.0, clock=lambda: now[0])
    assert pace.wait_after(2000) == 0.0                 # its first two seconds at once
    assert pace.wait_after(1000) == pytest.approx(1.0)
    now[0] += 1.0
    assert pace.wait_after(0) == 0.0
    now[0] += 60.0                                      # a slow stretch...
    assert pace.wait_after(2000) == 0.0                 # ...earns two seconds, no more
    assert pace.wait_after(1000) == pytest.approx(1.0)


def test_full_speed_is_read_and_kept():
    assert not dl.full_speed(Settings({})) and not dl.full_speed(None)
    assert dl.full_speed(Settings({dl.FULL_KEY: "true"})) and dl.full_speed(Settings({dl.FULL_KEY: True}))
    assert not dl.full_speed(Settings({dl.FULL_KEY: "false"}))
    s = Settings({})
    m = dl.Downloads(s, gated=False)
    assert not m.full_speed()
    m.set_full_speed(True)
    assert s[dl.FULL_KEY] is True and m.full_speed() and dl.Downloads(s).full_speed()


def test_a_paced_download_takes_its_time_hears_a_stop_and_goes_at_full_speed(
        tmp_path, provider, monkeypatch):
    monkeypatch.setattr(dl, "PACE_BURST_S", 0.0)
    monkeypatch.setattr(dl, "PACE_MIN", 2_000_000)
    m, s = _manager(tmp_path, provider.url())
    try:
        began = time.monotonic()
        _film(m)
        assert _until(lambda: _done(m))
        # 3 MB at 2 MB a second: no quicker than its pace.
        assert time.monotonic() - began >= 1.2
    finally:
        m.close()
    monkeypatch.setattr(dl, "PACE_MIN", 100_000)
    m, s = _manager(tmp_path / "b", provider.url())
    try:
        job = _film(m)
        assert _until(lambda: (m.job(job.id).done or 0) > 0)
        m.pause(job.id)
        assert _until(lambda: m.job(job.id).state == dl.PAUSED, 2.0)    # heard in its pause
        m.resume(job.id)
        assert _until(lambda: (m.current() or job).state == dl.RUNNING)
        m.set_full_speed(True)                          # the running one too
        assert _until(lambda: _done(m), 10.0)
    finally:
        m.close()


def test_the_next_download_rests_first_unless_started_or_at_full_speed(tmp_path, provider):
    m, s = _manager(tmp_path, provider.url(), rest_s=60.0)
    try:
        _film(m)
        assert _until(lambda: _done(m))
        assert m.rest_left() == 0.0                     # nothing waits
        b = m.add("movie", "a1b2c3d4", 9002, key=9002, title="Borta", ext="mp4")
        time.sleep(1.2)
        assert m.job(b.id).state == dl.WAITING and m.rest_left() > 50
        m.skip_rest()
        assert _until(lambda: m.find("a1b2c3d4", "movie", "9002") is not None)
        c = m.add("movie", "a1b2c3d4", 9003, key=9003, title="Tredje", ext="mp4")
        time.sleep(1.2)
        assert m.job(c.id).state == dl.WAITING and m.rest_left() > 50
        m.set_full_speed(True)
        assert m.rest_left() == 0.0
        assert _until(lambda: m.find("a1b2c3d4", "movie", "9003") is not None)
        d = m.add("movie", "a1b2c3d4", 9004, key=9004, title="Fjärde", ext="mp4")
        assert _until(lambda: m.find("a1b2c3d4", "movie", "9004") is not None)   # no rest
        assert d is not None and m.rest_left() == 0.0
    finally:
        m.close()


def test_the_speed_over_the_last_seconds(monkeypatch):
    m = dl.Downloads(Settings({}), gated=False)
    clock = [1000.0]
    monkeypatch.setattr(dl.time, "monotonic", lambda: clock[0])
    assert m.speed() is None                            # none running
    m._current = dl.Job(id="j", kind="movie", source="s", stream_id="1", key="1", title="T",
                        state=dl.RUNNING)
    for i in range(5):
        clock[0] = 1000.0 + i
        m._took(i * 1_000_000)
    assert m.speed() == pytest.approx(1_000_000)
    clock[0] = 1004.5                                   # the last piece is fresh
    assert m.speed() == pytest.approx(1_000_000)
    clock[0] = 1008.0                                   # stalled: it slows down
    assert m.speed() == pytest.approx(4_000_000 / 8)
    clock[0] = 1020.0                                   # stalled long: nothing said
    assert m.speed() is None
    m._took(0)                                          # from the start again
    assert list(m._samples) == [(1020.0, 0)]
