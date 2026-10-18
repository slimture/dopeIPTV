"""Dropping an M3U file or a provider link on the window adds a playlist.
A dropped video still plays, as before."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402

from PyQt6.QtCore import QMimeData, QPointF, Qt, QUrl  # noqa: E402
from PyQt6.QtGui import QDragEnterEvent, QDropEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from dopeiptv.ui import main_window as mw  # noqa: E402
from dopeiptv.ui.main_window import MainWindow  # noqa: E402

app = QApplication.instance() or QApplication([])


def _mime(*, urls=(), text=None):
    m = QMimeData()
    if urls:
        m.setUrls([QUrl(u) if "://" in u else QUrl.fromLocalFile(u)
                   for u in urls])
    if text is not None:
        m.setText(text)
    return m


def _drop(mime):
    ev = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier)
    ev._mime = mime         # the event only points at it: keep it alive
    return ev


def _enter(mime):
    ev = QDragEnterEvent(QPointF(5, 5).toPoint(), Qt.DropAction.CopyAction,
                         mime, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
    ev._mime = mime
    return ev


class _Store:
    def __init__(self):
        self.items = []

    def add(self, p):
        p = dict(p, id=f"p{len(self.items)}")
        self.items.append(p)
        return p


def _window(client):
    calls = []
    w = SimpleNamespace(
        MEDIA_EXTS=MainWindow.MEDIA_EXTS,
        _dropped_sources=MainWindow._dropped_sources,
        playlist_store=_Store(), client=client,
        _update_playlist_btn=lambda: None,
        switch_playlist=lambda pid: calls.append(("switch", pid)),
        _play_local_path=lambda p: calls.append(("play", p)))
    w._add_dropped_playlist = (
        lambda found: MainWindow._add_dropped_playlist(w, found))
    return w, calls


def test_a_dropped_m3u_file_opens_the_add_dialog_filled_in(monkeypatch):
    seen = {}

    class _Dialog:
        def __init__(self, _parent, prefill=None):
            seen.update(prefill)

        def exec(self):
            return True

        def values(self):
            return dict(seen, epg_url="", refresh="never")

    monkeypatch.setattr(mw, "PlaylistDialog", _Dialog)
    monkeypatch.setattr(mw.QTimer, "singleShot",
                        staticmethod(lambda _ms, fn: fn()))
    w, calls = _window(mw.OfflineClient())

    mime = _mime(urls=["/home/u/Sport channels.m3u"])
    enter = _enter(mime)
    MainWindow.dragEnterEvent(w, enter)
    assert enter.isAccepted()
    MainWindow.dropEvent(w, _drop(mime))
    assert seen["kind"] == "m3u" and seen["name"] == "Sport channels"
    assert seen["server"].endswith("Sport channels.m3u")
    assert w.playlist_store.items[0]["kind"] == "m3u"
    # The first real provider is used straight away.
    assert calls == [("switch", "p0")]


def test_with_a_provider_playing_the_switch_is_asked(monkeypatch):
    class _Dialog:
        def __init__(self, _parent, prefill=None):
            self.prefill = prefill

        def exec(self):
            return True

        def values(self):
            return dict(self.prefill, name="Mine", epg_url="",
                        refresh="never")

    asked = []
    monkeypatch.setattr(mw, "PlaylistDialog", _Dialog)
    monkeypatch.setattr(mw.QTimer, "singleShot",
                        staticmethod(lambda _ms, fn: fn()))
    monkeypatch.setattr(mw, "confirm",
                        lambda *_a, **_k: asked.append(1) or False)
    w, calls = _window(object())          # a real provider is in use

    MainWindow.dropEvent(w, _drop(_mime(
        text="http://h.tv:8080/get.php?username=a&password=b")))
    assert asked and calls == []           # added, not switched to
    assert w.playlist_store.items[0]["username"] == "a"


def test_a_dropped_video_still_plays_and_other_files_are_refused():
    w, calls = _window(object())
    MainWindow.dropEvent(w, _drop(_mime(urls=["/v/film.mkv"])))
    assert calls == [("play", "/v/film.mkv")]
    enter = _enter(_mime(urls=["/v/notes.txt"]))
    enter.ignore()
    MainWindow.dragEnterEvent(w, enter)
    assert not enter.isAccepted()
