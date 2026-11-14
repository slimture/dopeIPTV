"""The supporter key: made with the site's key, checked here offline,
nothing about the person in it - and what it changes in the app: the
heart's mark and words, no more asking, still a way to donate again."""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys


from dopeiptv.core import supporter

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED, PUB = supporter.new_seed()


def test_a_key_made_with_the_sites_key_checks_out():
    day = dt.date(2026, 11, 14).toordinal()
    key = supporter.make(SEED, day, b"\x01\x02\x03\x04\x05\x06")
    assert key.startswith("DS1-") and len(key) < 110
    got = supporter.parse(key, PUB)
    assert got == {"since": dt.date(2026, 11, 14), "id": "010203040506"}
    # Pasted with spaces or a line break, or in lower case: the same key.
    assert supporter.parse(" " + key[:30] + "\n" + key[30:] + " ", PUB) == got


def test_anything_else_does_not():
    key = supporter.make(SEED)
    other_seed, other_pub = supporter.new_seed()
    assert supporter.parse(key, other_pub) is None                    # another site's key
    assert supporter.parse(supporter.make(other_seed), PUB) is None
    flipped = key[:-3] + ("A" if key[-3] != "A" else "B") + key[-2:]
    assert supporter.parse(flipped, PUB) is None                      # one letter changed
    for junk in ("", "DS1-", "DS1-!!!", "hello", key[:40], "DS2-" + key[4:]):
        assert supporter.parse(junk, PUB) is None, junk
    # No public key in the app yet: nothing checks out.
    assert supporter.PUBLIC_KEY_HEX == "" or len(bytes.fromhex(supporter.PUBLIC_KEY_HEX)) == 32
    if not supporter.PUBLIC_KEY_HEX:
        assert supporter.parse(key) is None


def test_nothing_about_the_person_is_in_it():
    import base64
    key = supporter.make(SEED, dt.date(2026, 1, 2).toordinal(), b"abcdef")
    raw = base64.urlsafe_b64decode(key[4:] + "=" * (-len(key[4:]) % 4))
    assert len(raw) == 11 + 64                       # version, day, random id, signature
    assert raw[5:11] == b"abcdef"


_WINDOW = r"""
import os, sys, json, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
from dopeiptv.core import supporter
seed, pub = supporter.new_seed()
supporter.PUBLIC_KEY_HEX = pub.hex()
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui import dialogs
app = QApplication.instance() or QApplication([])
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
s.setValue("donate_watch_min", 5000)           # used for real: the card would be due
w = MainWindow(DemoClient(), s, PlaylistStore(s))
opened = []
w.open_donate = lambda ref: opened.append(ref)
assert w._donate_card_due() and not w._sidebar_logo._supporter
w.heart_clicked("app-heart")
assert opened == ["app-heart"]
# The key pasted in its window: checked as it is typed, kept when good.
d = dialogs.SupporterDialog(w, s, donate=lambda: opened.append("supporter"))
d.key.setText("not a key")
assert d.supporter is None and s.value("supporter_key") in (None, "")
key = supporter.make(seed)
d.key.setText(key)
assert d.supporter is not None and s.value("supporter_key") == key
d.donate_btn.click()
assert opened[-1] == "supporter"               # a supporter can always donate again
w._donate_heart_refresh()
# A supporter's mark is the logo itself; the heart is gone.
assert w._sidebar_logo._supporter and "2026" in w._sidebar_logo.toolTip()
assert w._donate_btn.isHidden()
assert not w._donate_card_due()                # not asked any more
# The heart now opens the thank-you, which still offers to donate.
shown = []
dialogs.SupporterDialog.exec = lambda self: shown.append(self.supporter) or 1
w.heart_clicked("app-heart")
assert shown and shown[-1] is not None and opened[-1] == "supporter"
# Removed: as before.
s.remove("supporter_key")
w._donate_heart_refresh()
assert not w._sidebar_logo._supporter and w._donate_card_due()
# Where a key goes, in plain sight: the app menu, the heart's tip, the
# window saying where the key is; "I've already donated" asks for it.
from dopeiptv.i18n import tr
assert w._supporter_action.text() == tr("supporter_menu") + "…"
n = len(shown)
w._supporter_action.trigger()
assert len(shown) == n + 1
assert tr("heart_tip_key") in w._donate_btn.toolTip()
d2 = dialogs.SupporterDialog(w, s)
assert d2.where.isVisibleTo(d2) and "Stripe" in d2.where.text()
assert d2.heart.isVisibleTo(d2) and not d2.mark.isVisibleTo(d2)
d2.key.setText(key)
assert not d2.where.isVisibleTo(d2)
# The thank-you shows the supporter's mark, the month in words.
assert d2.mark.isVisibleTo(d2) and not d2.heart.isVisibleTo(d2) and d2.mark._supporter
assert "2026" in d2.sub.text() and "-11-" not in d2.sub.text()
s.remove("supporter_key")
w.already_donated()                           # closed without a key: taken at their word
assert s.value("donate_thanked") == "true"
print("SUPPORTER_OK")
"""


def test_the_window_thanks_a_supporter_and_stops_asking():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _WINDOW], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    assert "SUPPORTER_OK" in out.stdout, out.stdout + out.stderr[-3000:]
