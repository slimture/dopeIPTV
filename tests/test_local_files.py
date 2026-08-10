"""Playing a video from the local disk (menu Open + drag-and-drop).

Local playback itself is the recordings path, already covered elsewhere -
these tests pin the entry points: what a dropped URL must look like to be
accepted, and that a picked path reaches _start_playback with the recording
kind (resume keyed on the path) and no recording of a local file.
"""
from dopeiptv.ui.main_window import MainWindow


class _Url:
    def __init__(self, path: str, local: bool = True):
        self._path, self._local = path, local

    def isLocalFile(self) -> bool:
        return self._local

    def toLocalFile(self) -> str:
        return self._path if self._local else ""


class _Mime:
    def __init__(self, urls):
        self._urls = urls

    def urls(self):
        return self._urls


class _Event:
    def __init__(self, urls):
        self.mime = _Mime(urls)
        self.accepted = False

    def mimeData(self):
        return self.mime

    def acceptProposedAction(self):
        self.accepted = True


class _Stub:
    VIDEO_EXTS = MainWindow.VIDEO_EXTS
    dragEnterEvent = MainWindow.dragEnterEvent
    dropEvent = MainWindow.dropEvent
    _play_local_path = MainWindow._play_local_path

    def __init__(self):
        self.played = []

    def _start_playback(self, url, title, icon, key, kind, record=True,
                        item=None):
        self.played.append((url, title, key, kind, record))


def test_a_video_file_is_played_as_first_class_local_content(tmp_path):
    f = tmp_path / "Semlor och TV.mkv"
    f.write_bytes(b"x")
    w = _Stub()
    w._play_local_path(str(f))
    assert len(w.played) == 1
    url, title, key, kind, record = w.played[0]
    assert url == str(f)
    assert title == "Semlor och TV"       # extension off, name kept
    assert key == str(f)                  # resume is keyed on the path
    assert kind == "local"                # own kind: resume group + History
    assert record is True                 # local plays land in History


def test_a_missing_path_is_ignored(tmp_path):
    w = _Stub()
    w._play_local_path(str(tmp_path / "finns-inte.mkv"))
    w._play_local_path("")
    assert w.played == []


def test_drop_accepts_only_local_video_files(tmp_path):
    f = tmp_path / "film.mp4"
    f.write_bytes(b"x")
    w = _Stub()

    # A dropped video file plays.
    e = _Event([_Url(str(f))])
    w.dropEvent(e)
    assert e.accepted and w.played and w.played[0][0] == str(f)

    # A text file or a remote URL is not for us - the drag is never accepted.
    e = _Event([_Url(str(tmp_path / "anteckningar.txt"))])
    w.dragEnterEvent(e)
    assert e.accepted is False
    e = _Event([_Url("http://example.com/film.mp4", local=False)])
    w.dragEnterEvent(e)
    assert e.accepted is False

    # Mixed drop: the video in the selection is the one that plays.
    w2 = _Stub()
    g = tmp_path / "b.webm"
    g.write_bytes(b"x")
    e = _Event([_Url(str(tmp_path / "a.txt")), _Url(str(g))])
    w2.dragEnterEvent(e)
    assert e.accepted is True
    w2.dropEvent(e)
    assert w2.played[0][0] == str(g)


def test_case_insensitive_extensions(tmp_path):
    f = tmp_path / "FILM.MKV"
    f.write_bytes(b"x")
    w = _Stub()
    e = _Event([_Url(str(f))])
    w.dragEnterEvent(e)
    assert e.accepted is True


def test_the_menu_strings_exist():
    from dopeiptv.i18n import tr
    assert tr("menu_open_video")
    assert tr("open_video_title")
    assert tr("open_video_filter")


def test_every_language_carries_the_new_strings():
    import json
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "dopeiptv/locale"
    for f in sorted(root.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        for key in ("menu_open_video", "open_video_title",
                    "open_video_filter"):
            assert key in d, f"{f.name} saknar {key}"
