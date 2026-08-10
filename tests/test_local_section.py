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

    def __init__(self):
        self.settings = _Settings()
        self.mode = "local"
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
    assert kinds == ["localdir", "recording"]
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
    assert [r["_kind"] for r in w.rendered] == ["localdir", "recording"]


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
            "local_add_hint", "local_empty", "local_remove", "ctx_open")
    for f in sorted(root.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        for key in keys:
            assert key in d, f"{f.name} saknar {key}"
