"""The active source in plain sight: its name over the categories with its
kind in a chip (two sources or more), the source menu on a click, the list's
source in the status line and what plays from another source said so there,
its name in TV mode's header - and all of it following a switch, a rename
and a removal."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytest.importorskip("PyQt6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from dopeiptv.ui import theme  # noqa: E402
from dopeiptv.ui.nav_widgets import SourceLabel  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _label(name: str, kind: str, width: int) -> SourceLabel:
    lbl = SourceLabel()
    lbl.set_source(name, kind)
    lbl.resize(width, SourceLabel.HEIGHT)
    return lbl


def test_the_chip_follows_the_name_and_the_chevron_ends_the_row(app):
    lbl = _label("Jellyfin test", "Jellyfin", 216)
    name, chip, chevron, text = lbl.boxes()
    assert text == "Jellyfin test"
    assert chip is not None
    assert name.right() < chip.left() < chip.right() < chevron.left()
    assert name.left() == SourceLabel.PAD


def test_right_to_left_mirrors_the_row(app):
    lbl = _label("Jellyfin test", "Jellyfin", 216)
    ltr = lbl.boxes()
    lbl.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    name, chip, chevron, _text = lbl.boxes()
    assert chip is not None
    assert chevron.right() < chip.left() < chip.right() < name.left()
    assert abs(name.right() - (216 - SourceLabel.PAD)) < 0.01
    assert abs(name.width() - ltr[0].width()) < 0.01


def test_a_long_name_is_cut_and_a_narrow_row_keeps_the_name(app):
    long = "Familjens stora IPTV-abonnemang med alla kanaler"
    lbl = _label(long, "Xtream", 216)
    name, chip, chevron, text = lbl.boxes()
    assert text != long and text.endswith("…")
    assert chip is not None and chip.right() < chevron.left()
    # Too narrow for both: the chip goes before the name is cut short.
    lbl.resize(110, SourceLabel.HEIGHT)
    name, chip, chevron, text = lbl.boxes()
    assert chip is None and name.right() <= chevron.left()


def test_one_source_is_its_name_alone(app):
    _name, chip, _chevron, text = _label("Pirat", "", 216).boxes()
    assert chip is None and text == "Pirat"


def test_the_chip_reads_on_every_theme(app):
    for key in theme.THEMES:
        theme.apply_theme(theme=key)
        fill, ink = SourceLabel.chip_colors()
        assert theme.contrast(ink, fill) >= 4.5, (key, fill, ink)
    theme.apply_theme(theme="graphite")


_CHILD = r"""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings, Qt
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtWidgets import QApplication, QMenu
app = QApplication.instance() or QApplication([])

from dopeiptv import i18n
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw
from dopeiptv.ui import theme

i18n.set_language("en")
menus = []


def fake_exec(self, *a, **k):
    menus.append((self, a[0] if a else None))


QMenu.exec = fake_exec


def pump(n=10):
    for _ in range(n):
        app.processEvents()
        w.pool.waitForDone(5)


s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
store = PlaylistStore(s)
pirat = store.add({"name": "Pirat", "server": "http://pirat.invalid", "username": "u", "password": "p"})
pappa = store.add({"name": "Pappa", "server": "http://pappa.invalid", "username": "u", "password": "p"})
jelly = store.add({"name": "Jellyfin test", "kind": "jellyfin", "server": "http://jf.invalid"})
store.set_active(pirat["id"])
theme.apply_theme(s)
app.setStyleSheet(theme.build_style())
w = mw.MainWindow(DemoClient(), s, store)
w.resize(1280, 800)
w.show()
pump(30)
w._set_sidebar_collapsed(False)
w.switch_mode("live")
pump(20)
lbl = w._source_label
switched = []
w.switch_playlist = lambda pid: switched.append(pid)


def count_line():
    n = sum(1 for r in range(w.list_model.rowCount())
            if not (w.list_model.item_at(r) or {}).get("_header"))
    return tr("count_channels", n=n)


def switch_to(pl):
    store.set_active(pl["id"])
    w._update_playlist_btn()
    w._apply_filter()           # the new source's list, as a switch loads it
    pump(2)


# -- over the categories: the name, its kind in a chip ------------------------------
assert lbl.isVisible()
assert (lbl.name(), lbl.kind()) == ("Pirat", "Xtream"), (lbl.name(), lbl.kind())
assert "Pirat · Xtream" in lbl.toolTip() and tr("source_label_hint") in lbl.toolTip()
assert lbl.accessibleName() == tr("source_label_name", name="Pirat · Xtream")
cats = w._cat_section_label
assert lbl.mapTo(w, lbl.rect().bottomLeft()).y() <= cats.mapTo(w, cats.rect().topLeft()).y()
assert lbl.mapTo(w, lbl.rect().topLeft()).y() >= \
    w._library_box.mapTo(w, w._library_box.rect().bottomLeft()).y()

# -- the status line: the list's source, nothing playing ----------------------------
assert w.list_model.rowCount() > 0
assert w.count_lbl.text() == tr("status_list_source", source="Pirat", text=count_line()), \
    w.count_lbl.text()

# -- a click opens the avatar's menu, under the name --------------------------------
lbl.click()
pump()
assert menus, "no menu"
menu, pos = menus[-1]
acts = [(a.text(), a.isChecked()) for a in menu.actions()]
assert acts == [("Pirat", True), ("Pappa", False), ("Jellyfin test", False), ("", False),
                (tr("src_settings_menu"), False), ("", False),
                (tr("profile_manage"), False)], acts
assert pos is not None and pos.y() >= lbl.mapToGlobal(lbl.rect().bottomLeft()).y()
next(a for a in menu.actions() if a.text() == "Pappa").trigger()
assert switched == [pappa["id"]], switched

# -- another source: the name and kind follow, the status line too ------------------
switch_to(jelly)
assert (lbl.name(), lbl.kind()) == ("Jellyfin test", "Jellyfin")
assert w.count_lbl.text() == tr("status_list_source", source="Jellyfin test", text=count_line())

# -- something plays from Pirat while Jellyfin test's list shows ----------------------
url = "http://pirat.invalid/live/1.ts"
switch_to(pirat)
w._note_playing_source(url, "k1", "live")
w._playing_key = "k1"
w._last_playback = {"url": url, "title": "SVT1 HD", "kind": "live"}
assert w._status_playing("SVT1 HD") == tr("status_playing", title="SVT1 HD")
w._set_status(w._status_playing("SVT1 HD"))
switch_to(jelly)
want = tr("status_playing_from", title="SVT1 HD", source="Pirat")
assert want == "Playing: SVT1 HD - from Pirat"
assert w.count_lbl.text() == want, w.count_lbl.text()
# Play after Stop: the same address is still Pirat's.
w._note_playing_source(url, "k1", "live")
assert w._playing_elsewhere() == "Pirat"
# Failover's title names its source already.
w._failover_key = "k1"
assert w._status_playing("SVT1 HD · Pirat") == tr("status_playing", title="SVT1 HD · Pirat")
w._failover_key = None
# A file of one's own comes from no source.
w._note_playing_source("/home/x/film.mkv", "f", "local")
assert w._playing_elsewhere() == ""
w._note_playing_source(url, "k1", "live")
assert w._playing_src[1] == "Jellyfin test"          # the active one at the time
assert w._playing_elsewhere() == ""
w._playing_src = (pirat["id"], "Pirat")
w._set_status(w._status_playing("SVT1 HD"))
# Stop: the list's own line again, with its source.
w._on_player_stopped()
assert w.count_lbl.text() == tr("status_list_source", source="Jellyfin test", text=count_line()), \
    w.count_lbl.text()

# -- a rename: the name and the status line follow -----------------------------------
store.update(jelly["id"], name="Jellyfin hemma")
w._update_playlist_btn()
assert lbl.name() == "Jellyfin hemma"
assert w.count_lbl.text() == tr("status_list_source", source="Jellyfin hemma", text=count_line())

# -- TV mode's header: the source beside the clock, following a switch ---------------
from dopeiptv.ui.tv_mode import TvPage
page = TvPage(w)
w._tv_page = page
assert page.source_lbl.text() == "Jellyfin hemma" and not page.source_lbl.isHidden()
switch_to(pappa)
assert page.source_lbl.text() == "Pappa"
assert (lbl.name(), lbl.kind()) == ("Pappa", "Xtream")
w._tv_page = None
page.deleteLater()

# -- a profile's refresh shows the source as it is ----------------------------------
store.set_active(pirat["id"])
w._profile_refresh()
pump(10)
assert lbl.name() == "Pirat"

# -- the rail: the avatar says it, the name goes -------------------------------------
w._set_sidebar_collapsed(True)
pump(20)
assert not lbl.isVisible() and w._playlist_btn.isVisible()
w._set_sidebar_collapsed(False)
pump(20)
assert lbl.isVisible()

# -- right to left: the row mirrored -------------------------------------------------
app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
pump(10)
name_box, chip_box, chevron, _text = lbl.boxes()
assert chip_box is not None and chevron.right() < chip_box.left() < name_box.left()
app.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
pump(10)

# -- removals: one source left is its name alone; none hides it ----------------------
store.remove(pappa["id"])
store.remove(jelly["id"])
w._update_playlist_btn()
w._apply_filter()
assert (lbl.name(), lbl.kind()) == ("Pirat", "") and lbl.isVisible()
assert w.count_lbl.text() == count_line(), w.count_lbl.text()
store.remove(pirat["id"])
w._update_playlist_btn()
assert not lbl.isVisible()
print("SOURCE_LABEL_OK", flush=True)
os._exit(0)
"""


def _child_env(tmp_path) -> dict:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
                "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                "XDG_CACHE_HOME"):
        env[var] = str(tmp_path / var.lower())
    return env


def test_the_source_on_a_real_window(tmp_path):
    for _attempt in (1, 2):
        out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT,
                             env=_child_env(tmp_path), capture_output=True,
                             text=True, timeout=180)
        if "SOURCE_LABEL_OK" in out.stdout:
            return
        if "AssertionError" in out.stderr:
            break
    raise AssertionError(out.stdout + out.stderr[-4000:])
