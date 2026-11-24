"""What the player and the certificate question show from outside is shown
as words.

The player's fullscreen overlay takes a radio station's ICY title, a
programme's and a channel's name; its number badge takes a track's own name
in TV mode. Both were AutoText labels: a title that looked like a tag was
read as rich text over the picture - a 900 px line, a picture from the disk.
The "certificate changed" question showed the source's name the same way.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytest.importorskip("PyQt6.QtWidgets")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOSTILE = 'Radio <span style="font-size:900px">x</span><img src="/tmp/x.png">'

_CHILD = r"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from dopeiptv.media.embedded import EmbeddedPlayer

HOSTILE = sys.argv[1]
app = QApplication.instance() or QApplication([])
p = EmbeddedPlayer()
p.resize(900, 500)
assert Qt.mightBeRichText(HOSTILE), "the title must be one AutoText reads as markup"

# The fullscreen overlay: the window's set_overlay_info, a station's title.
assert p.overlay.textFormat() == Qt.TextFormat.PlainText
p._fs_ui = True
p.set_overlay_info(HOSTILE)
p._show_overlay()
assert p.overlay.text() == HOSTILE
assert p.overlay.sizeHint().height() < 200, p.overlay.sizeHint()

# The number badge: TV mode's track name, the digits typed.
assert p.number_badge.textFormat() == Qt.TextFormat.PlainText
p.show_number_entry(HOSTILE)
assert p.number_badge.text() == HOSTILE
print("ok")
"""


def test_the_players_overlay_and_number_badge_show_words():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=_REPO_ROOT)
    proc = subprocess.run([sys.executable, "-c", _CHILD, HOSTILE], capture_output=True,
                          text=True, timeout=120, env=env, cwd=_REPO_ROOT)
    assert proc.returncode == 0 and "ok" in proc.stdout, proc.stdout + proc.stderr


def test_the_certificate_question_shows_the_sources_name_as_words(monkeypatch):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication, QLabel, QWidget

    from dopeiptv.ui import cert_trust, message_box

    app = QApplication.instance() or QApplication([])
    assert app is not None
    shown: list = []

    def seen(box):
        shown.append([(lbl.textFormat(), lbl.accessibleName()) for lbl in box.findChildren(QLabel)])
        return 0
    monkeypatch.setattr(message_box.MessageDialog, "exec", seen)
    window = QWidget()
    review = cert_trust._Review(window, None, {"name": HOSTILE, "options": {}}, None)
    review.ask("example.org", 443)
    assert len(shown) == 1
    body = next(words for _f, words in shown[0] if "example.org:443" in words)
    assert HOSTILE in body                              # as written, never escaped
    assert all(fmt == Qt.TextFormat.PlainText for fmt, _w in shown[0])
