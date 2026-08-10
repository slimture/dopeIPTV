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


class _Btn:
    def __init__(self):
        self.visible = False

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False


class _Stub(_LocalFilesMixin):
    VIDEO_EXTS = MainWindow.VIDEO_EXTS

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


def test_scan_is_bounded_and_reports_truncation(tmp_path):
    """A share full of directories must not be walked forever - the scan
    stops at its caps and says it was cut short (the SMB torrent-share
    hang)."""
    root = tmp_path / "big"
    for i in range(30):
        d = root / f"d{i:02d}"
        d.mkdir(parents=True)
        (d / f"Film.{i}.2020.mkv").write_bytes(b"x")
    w = _Stub()

    paths, cut = w._local_scan(str(root), max_dirs=10)
    assert cut is True
    assert 0 < len(paths) < 30           # partial result, not nothing

    paths, cut = w._local_scan(str(root), max_files=5)
    assert cut is True and len(paths) == 5

    paths, cut = w._local_scan(str(root))
    assert cut is False and len(paths) == 30

    # NAS junk dirs are pruned without being entered.
    junk = root / "#recycle"
    junk.mkdir()
    (junk / "old.mkv").write_bytes(b"x")
    paths, cut = w._local_scan(str(root))
    assert len(paths) == 30              # the recycled file never seen


def test_own_videos_group_by_their_folder(tmp_path, monkeypatch):
    """Untagged files in a subfolder become a browsable collection; only
    files directly in the root land under Movies."""
    import dopeiptv.ui.mw_local as ml
    monkeypatch.setattr(ml, "run_async",
                        lambda pool, fn, done, err=None: done(fn()))
    root = tmp_path / "M"
    (root / "Semester 2024").mkdir(parents=True)
    (root / "Semester 2024" / "dag1.mkv").write_bytes(b"x")
    (root / "Semester 2024" / "dag2.mkv").write_bytes(b"x")
    (root / "En.Film.2020.mkv").write_bytes(b"x")
    w = _Stub()
    w.settings.setValue("local_view", "series")
    w._current_cat = str(root)
    w._load_local_items(str(root))

    colls = [r for r in w.rendered if r.get("_kind") == "localcollection"]
    movies = [r for r in w.rendered if r.get("_kind") == "local"]
    assert len(colls) == 1 and "Semester 2024" in colls[0]["name"]
    assert len(movies) == 1

    w._local_open_series("Semester 2024")
    assert [r["name"] for r in w.rendered] == ["dag1", "dag2"]
    w._local_up()
    assert any(r.get("_kind") == "localcollection" for r in w.rendered)
