"""A login on plain http to the internet is said under the address, in the
add-a-source form and on a source's own page - a note, nothing refused."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from dopeiptv.ui.dialogs import PlaylistDialog  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _form(kind: str, server: str) -> PlaylistDialog:
    d = PlaylistDialog(None, {"type": kind, "server": server})
    i = d.kind.findData(kind)
    if i >= 0:
        d.kind.setCurrentIndex(i)
    d.server.setText(server)
    return d


def test_plain_http_to_the_internet_is_said():
    for server in ("http://provider.example:8080", "provider.example:8080"):
        d = _form("xtream", server)
        assert d.plain_http(), server
        assert not d.http_note.isHidden(), server


def test_https_and_home_addresses_say_nothing():
    for server in ("https://provider.example", "http://192.168.1.20:8080", "http://box.local", ""):
        d = _form("xtream", server)
        assert not d.plain_http(), server
        assert d.http_note.isHidden(), server


def test_other_kinds_say_nothing():
    d = _form("m3u", "http://provider.example/list.m3u")
    assert not d.plain_http()
