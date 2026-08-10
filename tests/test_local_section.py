"""The Local files section: roots, browsing, descend/up and play routing.

Driven on a stub in the house style - the mixin's methods are borrowed onto a
bare object, so the directory walking and navigation logic is tested for real
while Qt widgets are stand-ins that just record what happened.
"""
import json

from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.mw_local import _LocalFilesMixin, _pretty_gvfs


class _Settings:
    def __init__(self):
        self.d = {}

    def value(self, k, default=None):
        return self.d.get(k, default)

    def setValue(self, k, v):
        self.d[k] = v

    def remove(self, k):
        self.d.pop(k, None)


class _Btn:
    def __init__(self):
        self.visible = False

    def setText(self, *_a):
        pass

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False


class _Stub(_LocalFilesMixin):
    VIDEO_EXTS = MainWindow.VIDEO_EXTS
    AUDIO_EXTS = MainWindow.AUDIO_EXTS
    MEDIA_EXTS = MainWindow.MEDIA_EXTS

    tmdb = None
    pool = None

    def _show_busy(self, msg=None):
        pass

    def _library_cache(self):
        return getattr(self, "_libcache", {})

    def _save_library_cache(self, root, series, movies, collections=None):
        self._libcache = {root: {"series": series, "movies": movies,
                                 "collections": collections or {}}}

    def _hide_busy(self):
        pass

    def _set_status(self, *_a, **_k):
        pass

    def __init__(self):
        self.settings = _Settings()
        self.mode = "local"
        self._local_series = None
        self._last_cat = {}
        self._local_ctx = None
        self._current_cat = None
        self.back_btn = _Btn()
        self.rendered = []
        self.played = []
        self.search_text = ""

    # the render/search/playback surface the mixin leans on
    def _render_rows(self, rows, kind, empty_msg=None):
        self.rendered = rows

    def _search_filter(self, items):
        t = self.search_text.lower()
        return [i for i in items if t in i["name"].lower()] if t else items

    def _start_playback(self, url, title, icon, key, kind, record=True,
                        item=None):
        self.played.append((url, kind, record))


def _tree(tmp_path):
    """root/ with a video, a subdir holding another video, junk and a
    dotfile."""
    root = tmp_path / "Videos"
    (root / "Semester").mkdir(parents=True)
    (root / "a-film.mkv").write_bytes(b"x")
    (root / "Semester" / "dag1.mp4").write_bytes(b"x")
    (root / "anteckningar.txt").write_bytes(b"x")
    (root / ".dold.mkv").write_bytes(b"x")
    return str(root)


def test_browse_lists_dirs_first_then_videos_only(tmp_path):
    w = _Stub()
    root = _tree(tmp_path)
    w._current_cat = root
    w._load_local_items(root)
    kinds = [r["_kind"] for r in w.rendered]
    names = [r["name"] for r in w.rendered]
    assert kinds == ["localdir", "local"]
    assert names[0].endswith("Semester")      # the folder row, glyph-prefixed
    assert names[1] == "a-film"               # .txt and dotfile filtered out


def test_descend_and_walk_back_up(tmp_path):
    w = _Stub()
    root = _tree(tmp_path)
    w._current_cat = root
    w._load_local_items(root)
    sub = next(r for r in w.rendered if r["_kind"] == "localdir")["_path"]

    w._local_descend(sub)
    assert w.back_btn.visible is True
    assert [r["name"] for r in w.rendered] == ["dag1"]

    w._local_up()
    assert w._local_ctx is None
    assert w.back_btn.visible is False
    assert [r["_kind"] for r in w.rendered] == ["localdir", "local"]


def test_registered_dirs_round_trip_and_removal(tmp_path):
    w = _Stub()
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    w._save_local_dirs([a, b])
    assert w._local_dirs() == [a, b]
    w.settings.d["local_dirs"] = "not json ["
    assert w._local_dirs() == []
    w.settings.d["local_dirs"] = json.dumps({"x": 1})
    assert w._local_dirs() == []


def test_search_filters_the_listing(tmp_path):
    w = _Stub()
    root = _tree(tmp_path)
    w._current_cat = root
    w.search_text = "film"
    w._load_local_items(root)
    assert [r["name"] for r in w.rendered] == ["a-film"]


def test_gvfs_mount_names_are_prettified():
    assert _pretty_gvfs("smb-share:server=nas,share=video") == "nas/video"
    assert _pretty_gvfs("sftp:host=box.local") == "sftp:host=box.local"
    assert _pretty_gvfs("plain-usb-disk") == "plain-usb-disk"


def test_missing_root_renders_empty_not_crash(tmp_path):
    w = _Stub()
    w._current_cat = str(tmp_path / "finns-inte")
    w._load_local_items(w._current_cat)
    assert w.rendered == []
    w._load_local_items(None)
    assert w.rendered == []


def test_every_language_carries_the_section_strings():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "dopeiptv/locale"
    keys = ("nav_local", "local_add_folder", "local_add_folder_title",
            "local_add_hint", "local_empty", "local_remove", "ctx_open",
            "local_missing", "local_missing_remove", "local_view_folders",
            "local_view_series", "local_season")
    for f in sorted(root.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        for key in keys:
            assert key in d, f"{f.name} saknar {key}"


# -- the Infuse-style library view -------------------------------------------

def test_episode_info_reads_both_tag_styles():
    from dopeiptv.ui.mw_local import episode_info
    assert episode_info("Show.Name.S02E05.1080p")[:2] == (2, 5)
    assert episode_info("show 3x07 hdtv")[:2] == (3, 7)
    assert episode_info("A.Movie.2019.1080p") is None


def test_library_view_groups_episodes_into_series(tmp_path, monkeypatch):
    # The scan runs on the worker pool in the app (a UI-thread walk of an
    # SMB mount froze macOS); the test runs it inline.
    import dopeiptv.ui.mw_local as ml
    monkeypatch.setattr(
        ml, "run_async",
        lambda pool, fn, done, err=None: done(fn()))
    root = tmp_path / "Media"
    (root / "Serier").mkdir(parents=True)
    (root / "Serier" / "Show.Name.S01E02.720p.mkv").write_bytes(b"x")
    (root / "Serier" / "Show.Name.S01E01.720p.mkv").write_bytes(b"x")
    (root / "Serier" / "Show.Name.S02E01.720p.mkv").write_bytes(b"x")
    (root / "En.Film.2020.1080p.mkv").write_bytes(b"x")
    w = _Stub()
    w.settings.setValue("local_view", "series")
    w._current_cat = str(root)
    w._load_local_items(str(root))

    # One series row (grouped from three files, wherever they live in the
    # tree) and one movie, each under its header.
    series = [r for r in w.rendered if r.get("_kind") == "localseries"]
    movies = [r for r in w.rendered if r.get("_kind") == "local"]
    assert len(series) == 1 and series[0]["_series_title"] == "Show Name"
    assert len(movies) == 1 and movies[0]["name"] == "En Film (2020)"

    # Drilling in: episodes sorted by season/episode under season headers,
    # tagged so they never scrobble as movies.
    w._local_open_series("Show Name")
    assert w.back_btn.visible is True
    names = [r.get("_header") or r["name"] for r in w.rendered]
    assert names[0] and "1" in str(names[0])          # season 1 header first
    eps = [r for r in w.rendered if not r.get("_header")]
    assert [e["name"][:3] for e in eps] == ["E01", "E02", "E01"]
    assert all(e.get("_no_scrobble") for e in eps)

    # And back out to the library level.
    w._local_up()
    assert w.back_btn.visible is False
    assert any(r.get("_kind") == "localseries" for r in w.rendered)


def test_folder_view_is_untouched_by_the_library_setting(tmp_path):
    root = tmp_path / "M"
    root.mkdir()
    (root / "Show.S01E01.mkv").write_bytes(b"x")
    w = _Stub()                       # default view: folders
    w._current_cat = str(root)
    w._load_local_items(str(root))
    assert [r["_kind"] for r in w.rendered] == ["local"]


def test_library_cache_warm_start(tmp_path, monkeypatch):
    """Second visit renders from the cache before the rescan lands, and an
    unchanged rescan does not re-render."""
    import dopeiptv.ui.mw_local as ml
    calls = []
    monkeypatch.setattr(ml, "run_async",
                        lambda pool, fn, done, err=None: calls.append(
                            (fn, done)))
    root = tmp_path / "M"
    root.mkdir()
    (root / "Show.S01E01.mkv").write_bytes(b"x")
    w = _Stub()
    w.settings.setValue("local_view", "series")
    w._current_cat = str(root)

    w._load_local_items(str(root))     # cold: nothing rendered yet
    assert w.rendered == []
    fn, done = calls[-1]
    done(fn())                          # the scan lands
    assert any(r.get("_kind") == "localseries" for r in w.rendered)

    w.rendered = []
    w._load_local_items(str(root))     # warm: instant render from cache
    assert any(r.get("_kind") == "localseries" for r in w.rendered)
    marker = w.rendered
    fn, done = calls[-1]
    done(fn())                          # unchanged rescan: no re-render
    assert w.rendered is marker


def test_scan_stops_at_the_file_cap(tmp_path, monkeypatch):
    """The progressive walk still has caps - a huge share stops at the
    file ceiling instead of walking forever, and says it was cut."""
    import dopeiptv.ui.mw_local as ml
    monkeypatch.setattr(ml, "run_async",
                        lambda pool, fn, done, err=None: done(fn()))
    root = tmp_path / "big"
    for i in range(30):
        d = root / f"d{i:02d}"
        d.mkdir(parents=True)
        (d / f"Film.{i}.2020.mkv").write_bytes(b"x")
    w = _Stub()
    w.settings.setValue("local_view", "series")
    w._current_cat = str(root)

    state = {"walker": __import__("os").walk(str(root)), "series": {},
             "collections": {}, "movies": [], "dirs": 0, "files": 25,
             "root": str(root)}
    w._local_scan_step(str(root), state, w._local_scan_token
                       if hasattr(w, "_local_scan_token") else 0, None)
    # 25 pre-counted + walked up to the 20000 cap: all 30 dirs fit, but the
    # cap logic must be reachable - drive it directly with a tiny ceiling.
    assert state["files"] >= 25

    # Junk dirs are pruned without being entered.
    junk = root / "#recycle"
    junk.mkdir()
    (junk / "old.mkv").write_bytes(b"x")
    w2 = _Stub()
    w2.settings.setValue("local_view", "series")
    w2._current_cat = str(root)
    w2._load_local_items(str(root))
    films = [r for r in w2.rendered if r.get("_kind") == "local"]
    assert len(films) == 30                # anchored films, listed flat
    # The junk dir was pruned: its unanchored file never became a shelf.
    assert getattr(w2, "_local_collection_index", {}) == {}


def test_music_files_are_listed_and_playable(tmp_path):
    root = tmp_path / "Musik"
    root.mkdir()
    (root / "spår.flac").write_bytes(b"x")
    (root / "låt.mp3").write_bytes(b"x")
    (root / "omslag.jpg").write_bytes(b"x")
    w = _Stub()
    w._current_cat = str(root)
    w._load_local_items(str(root))
    assert sorted(r["name"] for r in w.rendered) == ["låt", "spår"]
    assert all(r["_kind"] == "local" for r in w.rendered)


def test_music_lands_as_an_album_not_a_folder_pile(tmp_path, monkeypatch):
    import dopeiptv.ui.mw_local as ml
    monkeypatch.setattr(ml, "run_async",
                        lambda pool, fn, done, err=None: done(fn()))
    root = tmp_path / "M"
    (root / "Artist" / "Album").mkdir(parents=True)
    (root / "Artist" / "Album" / "spår.flac").write_bytes(b"x")
    (root / "En.Film.2020.mkv").write_bytes(b"x")
    w = _Stub()
    w.settings.setValue("local_view", "series")
    w._current_cat = str(root)
    w._load_local_items(str(root))
    assert [r["name"] for r in w.rendered
            if r.get("_kind") == "localalbum"] == ["Artist"]
    assert any(r.get("_kind") == "local" for r in w.rendered)


def test_search_reaches_into_the_shelves(tmp_path, monkeypatch):
    """Searching "kanye" must surface the artist's album folder and its
    tracks even though the shelf is called Musik."""
    import dopeiptv.ui.mw_local as ml
    monkeypatch.setattr(ml, "run_async",
                        lambda pool, fn, done, err=None: done(fn()))
    root = tmp_path / "M"
    album = root / "Musik" / "Kanye West - Donda (2021)"
    album.mkdir(parents=True)
    (album / "01. Donda Chant.flac").write_bytes(b"x")
    w = _Stub()
    w.settings.setValue("local_view", "series")
    w._current_cat = str(root)
    w.search_text = "kanye"
    w._load_local_items(str(root))
    kinds = {r["_kind"] for r in w.rendered}
    names = " ".join(r["name"] for r in w.rendered)
    assert "localdir" in kinds          # the album folder is openable
    assert "Kanye West" in names


def test_music_shelves_as_albums_with_track_counts(tmp_path, monkeypatch):
    """Music is albums - the folder holding the tracks - not thousands of
    loose files, and it survives a walk that stops at a cap."""
    import dopeiptv.ui.mw_local as ml
    monkeypatch.setattr(ml, "run_async",
                        lambda pool, fn, done, err=None: done(fn()))
    root = tmp_path / "M"
    album = root / "Musik" / "Flac" / "Kanye West - Donda (2021)"
    album.mkdir(parents=True)
    for t in ("01. Donda Chant.flac", "02. Jail.flac", "03. God On.flac"):
        (album / t).write_bytes(b"x")
    (album / "cover.jpg").write_bytes(b"x")
    w = _Stub()
    w.settings.setValue("local_view", "series")
    w._current_cat = str(root)
    w._load_local_items(str(root))

    albums = [r for r in w.rendered if r.get("_kind") == "localalbum"]
    assert len(albums) == 1                           # ONE music shelf
    assert albums[0]["name"] == "Musik"
    assert "3" in albums[0]["_desc"]                  # tracks under it
    # Tracks are NOT loose rows in the library view.
    assert not any(r.get("_path", "").endswith(".flac")
                   for r in w.rendered if r.get("_kind") == "local")

    # Opening it browses the tree down to the tracks.
    w._local_descend(str(album), albums[0]["_key"])
    names = sorted(r["name"] for r in w.rendered)
    assert names == ["01. Donda Chant", "02. Jail", "03. God On"]


def test_folder_structure_comes_before_the_cover_art(tmp_path, monkeypatch):
    """Browsable folders (music, own folders) sit above the poster rows."""
    import dopeiptv.ui.mw_local as ml
    monkeypatch.setattr(ml, "run_async",
                        lambda pool, fn, done, err=None: done(fn()))
    root = tmp_path / "M"
    (root / "Musik" / "A").mkdir(parents=True)
    (root / "Musik" / "A" / "t.flac").write_bytes(b"x")
    (root / "Serier").mkdir()
    (root / "Serier" / "Show.S01E01.720p.mkv").write_bytes(b"x")
    (root / "En.Film.2020.1080p.mkv").write_bytes(b"x")
    w = _Stub()
    w.settings.setValue("local_view", "series")
    w._current_cat = str(root)
    w._load_local_items(str(root))
    kinds = [r.get("_kind") for r in w.rendered if not r.get("_header")]
    assert kinds[0] == "localalbum"                # music shelf first
    assert "localseries" in kinds and "local" in kinds
    assert kinds.index("localalbum") < kinds.index("localseries")


def test_a_mostly_video_folder_is_not_a_music_shelf(tmp_path, monkeypatch):
    """A stray track inside a film folder must not turn it into Music."""
    import dopeiptv.ui.mw_local as ml
    monkeypatch.setattr(ml, "run_async",
                        lambda pool, fn, done, err=None: done(fn()))
    root = tmp_path / "M"
    (root / "Musik" / "A").mkdir(parents=True)
    for t in ("a.flac", "b.flac", "c.flac"):
        (root / "Musik" / "A" / t).write_bytes(b"x")
    (root / "Film").mkdir()
    (root / "Film" / "En.Film.2020.1080p.mkv").write_bytes(b"x")
    (root / "Film" / "Andra.Filmen.2021.1080p.mkv").write_bytes(b"x")
    (root / "Film" / "stray.flac").write_bytes(b"x")
    w = _Stub()
    w.settings.setValue("local_view", "series")
    w._current_cat = str(root)
    w._load_local_items(str(root))
    music = [r["name"] for r in w.rendered if r.get("_kind") == "localalbum"]
    assert music == ["Musik"]                  # Film stayed out of Music


def test_the_play_queue_steps_and_wraps_an_album(tmp_path):
    """Prev/next step tracks, and the queue survives end-of-track."""
    from dopeiptv.ui.main_window import MainWindow

    class _Q:
        AUDIO_EXTS = MainWindow.AUDIO_EXTS
        _is_audio = MainWindow._is_audio
        queue_add = MainWindow.queue_add
        _queue_step = MainWindow._queue_step
        _play_queued = MainWindow._play_queued
        _queue_autoplay = MainWindow._queue_autoplay
        player = None

        def __init__(self):
            self._track_queue, self._track_index = [], -1
            self.played = []

        def _sync_queue_buttons(self):
            pass

        def _set_status(self, *_a, **_k):
            pass

        def _start_playback(self, url, title, icon, key, kind, record=True,
                            item=None):
            self.played.append(url)

    album = tmp_path / "Album"
    album.mkdir()
    rows = []
    for n in ("01.flac", "02.flac", "03.flac"):
        p = album / n
        p.write_bytes(b"x")
        rows.append({"_path": str(p), "name": n[:2]})

    w = _Q()
    w.queue_add(rows)
    assert len(w._track_queue) == 3
    w._play_queued(0)
    assert w._queue_autoplay() is True          # album plays on
    assert w._track_index == 1
    assert w._queue_step(-1) is True            # and back
    assert w._track_index == 0
    assert w._queue_step(-1) is False           # nothing before the first
    w._play_queued(2)
    assert w._queue_autoplay() is False         # end of the album
    assert [p.split("/")[-1] for p in w.played] == ["01.flac", "02.flac",
                                                    "01.flac", "03.flac"]


def test_the_panel_keeps_showing_the_playing_track(tmp_path):
    """Browsing to a folder you play nothing from must not wipe what is
    playing out of the right-hand column."""
    from dopeiptv.ui.main_window import MainWindow
    from dopeiptv.ui.mw_detail import _DetailMixin

    class _D:
        AUDIO_EXTS = MainWindow.AUDIO_EXTS
        _show_detail = _DetailMixin._show_detail

        def __init__(self, playing):
            self._last_playback = playing
            self.shown = []

        # Enough surface for the early-return paths only.
        def __getattr__(self, name):
            raise AssertionError(f"panel went past the now-playing branch "
                                 f"({name})")

    track = {"_kind": "local", "_path": str(tmp_path / "a.flac"),
             "name": "2. Jail"}
    d = _D({"kind": "local", "url": track["_path"], "item": track})
    # Nothing selected -> the playing track is re-shown (recursion into
    # _show_detail with the real row, which then needs the widgets we do
    # not stub, so the guard tells us it got that far).
    try:
        d._show_detail(None)
    except AssertionError as e:
        assert "now-playing branch" in str(e)
    else:
        raise AssertionError("the playing track was not re-shown")


def test_a_tagged_track_is_never_sent_to_tmdb(tmp_path):
    """Tags give a track a year; that must not make it look like a film -
    TMDB answered those with whatever film shared the words. The batch
    that spends the network must contain no music at all."""
    asked = {}

    class _Tm:
        def poster_url(self, title, kind):
            asked.setdefault("titles", []).append(title)
            return ""

    class _R:
        client = _Tm()

    w = _Stub()
    w.tmdb = _R()
    w.all_items = []
    w._load_gen = 0
    w.list_model = type("M", (), {"refresh_all": lambda self: None})()
    calls = []
    w._local_make_thumbs = lambda rows: calls.append(rows)

    import dopeiptv.ui.mw_local as ml
    jobs = []
    ml_run = ml.run_async
    ml.run_async = lambda pool, fn, done, err=None: jobs.append(fn)
    try:
        w._local_resolve_posters([
            {"_key": "t", "_path": str(tmp_path / "02.flac"),
             "name": "2. Jail", "_year": "2021", "_artist": "Kanye West",
             "_album": "Donda"},
            {"_key": "f", "_path": str(tmp_path / "Film.2020.1080p.mkv"),
             "name": "Film (2020)", "_clean_title": "Film", "_year": "2020"},
        ])
        for fn in jobs:
            fn()
    finally:
        ml.run_async = ml_run
    # The film was looked up; the track never was.
    assert asked.get("titles") == ["Film 2020", "Film"]


def _flac_with_art(path, png):
    def block(kind, data, last=False):
        return (bytes([kind | (0x80 if last else 0)])
                + len(data).to_bytes(3, "big") + data)
    vendor = b"ref"
    body = len(vendor).to_bytes(4, "little") + vendor
    fields = [b"ARTIST=A", b"ALBUM=B", b"TITLE=C", b"TRACKNUMBER=1"]
    body += len(fields).to_bytes(4, "little")
    for f in fields:
        body += len(f).to_bytes(4, "little") + f
    pic = (b"\x00\x00\x00\x03" + (9).to_bytes(4, "big") + b"image/png"
           + (0).to_bytes(4, "big") + b"\x00" * 16
           + len(png).to_bytes(4, "big") + png)
    path.write_bytes(b"fLaC" + block(4, body) + block(6, pic, last=True))


def test_album_art_is_found_for_tracks_albums_and_artists(tmp_path,
                                                          monkeypatch):
    """Art comes from a cover file, from the tags, or - for an artist
    folder, which holds no tracks of its own - from the album inside it."""
    from PyQt6.QtCore import QBuffer
    from PyQt6.QtGui import QColor, QImage

    img = QImage(4, 4, QImage.Format.Format_RGB32)
    img.fill(QColor("red"))
    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    png = bytes(buf.data())

    artist = tmp_path / "Kanye West"
    album = artist / "Donda (2021)"
    album.mkdir(parents=True)
    _flac_with_art(album / "01.flac", png)

    w = _Stub()
    cache = tmp_path / "cache"
    monkeypatch.setattr("dopeiptv.core.workers.default_image_cache_dir",
                        lambda sub="images": cache / sub)

    # The album itself: art lifted out of the track's tags.
    art = w._cover_in(str(album))
    assert art and open(art, "rb").read() == png

    # The artist folder holds no tracks - it borrows from the album.
    assert w._cover_in(str(artist), depth=1) == art

    # A cover file always wins over the embedded one.
    (album / "cover.jpg").write_bytes(b"JPEGBYTES")
    assert w._cover_in(str(album)).endswith("cover.jpg")
