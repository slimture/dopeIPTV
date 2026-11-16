"""Radio and web streams in the real window (offscreen, in a child process
as the other window tests): Radio as a place beside Local files, its lists
from a fake radio-browser.info, a country opened and left, the directory
searched, a station played as radio - a click told only when Settings says
so - and kept as a favourite of its own kind; a YouTube/Twitch address
through yt-dlp (mpv's hook, or yt-dlp asked by the app where the libmpv has
none), the offer to download yt-dlp when there is none, and the address
saved with its LIVE badge."""
from __future__ import annotations

import os
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os, sys, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(var, None)
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
from dopeiptv.core import ytdlp
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers import radio_browser as rb
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow
from tests.fake_radio_browser import FakeRadioBrowser

fake = FakeRadioBrowser()
w = MainWindow(DemoClient(), s, PlaylistStore(s))
w._radio_client = rb.RadioBrowser([fake.host], scheme="http")
plays = []


def start(url, title, icon, key, kind, record=True, item=None, **kw):
    plays.append((url, title, key, kind, record))
    w._last_playback = {"url": url, "title": title, "key": key, "kind": kind, "item": item}


w._start_playback = start


def wait(cond, secs=6.0):
    end = time.monotonic() + secs
    while not cond() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    return cond()


def rows():
    return [w.list_model.item_at(i) for i in range(w.list_model.rowCount())]


def names():
    return [r.get("name") for r in rows() if not r.get("_header")]


w.resize(1200, 800)
w.show()
# -- the place, beside Local files ------------------------------------------------
keys = list(w.nav_btns)
assert keys.index("radio") == keys.index("local") + 1, keys
w._on_nav_clicked("radio")
assert w.mode == "radio" and w.cat_list.count() == 7
assert w.cat_list.currentRow() == 1                       # no favourites yet: Most voted
assert wait(lambda: names() == ["Jazz Radio", "Rock FM", "News 24"]), names()
assert "Sweden" in rows()[0]["_sub"] and "jazz" in rows()[0]["_sub"]
w.listw.viewport().grab()                                 # the rows paint

# -- a country opened, and left again -----------------------------------------------
w.cat_list.setCurrentRow(4)
assert wait(lambda: names() == ["Germany", "Sweden"]), names()
w.listw.viewport().grab()                                 # with their flags
w.play_item(rows()[1])
assert wait(lambda: names() == ["Jazz Radio", "News 24"]), names()
assert not w.back_btn.isHidden()
w._leave_series()
assert wait(lambda: names() == ["Germany", "Sweden"]), names()
assert w.listw.currentIndex().row() == 1 and w.back_btn.isHidden()

# -- the whole directory searched ---------------------------------------------------
w.cat_list.setCurrentRow(3)
assert wait(lambda: w.list_model.rowCount() == 0)
assert w.search.placeholderText() == tr("radio_search_placeholder")
w.search.setText("rock")
assert wait(lambda: names() == ["Rock FM"]), names()

# -- played as radio; a click only when it is turned on ----------------------------------
station = rows()[0]
w.play_item(station)
assert plays[-1] == ("http://example.invalid/rock", "Rock FM", "radio:u-rock", "radio", True), plays[-1]
time.sleep(0.3)
app.processEvents()
assert not [p for p in fake.paths() if p.startswith("/json/url/")]
s.setValue(rb.SETTING_CLICKS, "true")
from dopeiptv.core import privacy
privacy.set_private(True)                                 # never while viewing privately
w.play_item(station)
time.sleep(0.3)
app.processEvents()
assert not [p for p in fake.paths() if p.startswith("/json/url/")]
privacy.set_private(False)
w.play_item(station)
assert wait(lambda: "/json/url/u-rock" in fake.paths())
w._show_detail(station)
assert "Rock FM" in w.media_plot.text() and "Germany" in w.media_plot.text()

# -- a favourite of its own kind ---------------------------------------------------------
w._radio_toggle_fav(station)
assert w.radio_favs.is_favorite("u-rock") and w.is_favorite_item(station, "radio")
assert s.value("radio_favorites")
w.switch_mode("fav")
labels = [w.cat_list.item(i).text() for i in range(w.cat_list.count())]
assert tr("fav_radio") in labels, labels
w.cat_list.setCurrentRow(0)
assert any(r.get("_header") == tr("fav_radio") for r in rows())
fav_row = next(r for r in rows() if r.get("_kind") == "radio")
w.play_item(fav_row)
assert plays[-1][3] == "radio" and plays[-1][2] == "radio:u-rock"
w._on_nav_clicked("radio")                                # back where it was left
assert w.cat_list.currentRow() == 3

# -- a web page through yt-dlp -----------------------------------------------------------
offers = []
real_offer = w._ytdlp_offer
w._ytdlp_offer = offers.append
ytdlp.find = lambda *a, **k: None
before = len(plays)
assert w.handle_command({"play": "https://www.twitch.tv/somechannel"}) == {"ok": True}
assert not offers                                         # answered first, offered after
assert wait(lambda: offers) and len(plays) == before      # nothing plays without yt-dlp
if w.player is not None:
    hooked = []
    w.player.has_ytdl_hook = lambda: True
    w.player.set_ytdl_path = hooked.append
    ytdlp.find = lambda *a, **k: ytdlp.Found("/opt/fake/yt-dlp", False)
    w._play_address("https://www.twitch.tv/somechannel")
    assert plays[-1] == ("ytdl://https://www.twitch.tv/somechannel", "twitch.tv/somechannel",
                         "https://www.twitch.tv/somechannel", "web", False), plays[-1]
    assert hooked == ["/opt/fake/yt-dlp"]
    assert s.value(ytdlp.SETTING_CHECKED) is None         # the system's: never updated
    # A libmpv without the hook: yt-dlp is asked by the app.
    w.player.has_ytdl_hook = lambda: False
    ytdlp.resolve = lambda path, url, **k: {"url": "https://video.example/live.m3u8",
                                            "title": "Night stream", "live": True, "headers": {}}
    w._play_address("https://www.youtube.com/watch?v=abc")
    assert wait(lambda: plays[-1][0] == "https://video.example/live.m3u8"), plays[-1]
    assert plays[-1][1:4] == ("Night stream", "https://www.youtube.com/watch?v=abc", "web")
    w._web_fav_add("https://www.youtube.com/watch?v=abc")
    saved = w.web_favs.items()[0]
    assert saved["name"] == "Night stream" and saved["_live"] is True, saved

    # None found: the app's own copy downloaded (asked), then the page plays.
    w._ytdlp_offer = real_offer
    w._choice_dialog = lambda *a, **k: 0
    managed = ytdlp.Found(os.path.join(tmp, "yt-dlp"), True)

    def install(**kw):
        ytdlp.find = lambda *a, **k: managed
        return "2099.01.02"
    updates = []
    ytdlp.install = install
    ytdlp.update = lambda *a, **k: updates.append(1)
    ytdlp.find = lambda *a, **k: None
    w.player.has_ytdl_hook = lambda: True
    w._play_address("https://kick.com/someone")
    assert wait(lambda: plays[-1][0] == "ytdl://https://kick.com/someone"), plays[-1]
    assert hooked[-1] == managed.path and s.value(ytdlp.SETTING_CHECKED)
    # Just downloaded: no update check the same day.
    assert wait(lambda: True) and updates == []
else:
    print("NO_PLAYER", flush=True)

# -- saved with a LIVE badge, under Favourites -------------------------------------
w._web_fav_add("https://www.twitch.tv/somechannel", "Some channel")
w.switch_mode("fav")
labels = [w.cat_list.item(i).text() for i in range(w.cat_list.count())]
assert tr("fav_web") in labels, labels
w.cat_list.setCurrentRow(labels.index(tr("fav_web")))
web = next(r for r in rows() if r.get("url") == "https://www.twitch.tv/somechannel")
assert web["_live"] is True and web["_kind"] == "web"
assert w.is_favorite_item(web, "web")
w.listw.viewport().grab()                                 # the LIVE pill paints
w._show_detail(web)
assert "Some channel" in w.media_plot.text() and "twitch.tv/somechannel" in w.media_plot.text()
s.setValue("view_grid", "true")                           # and the poster grid
w._load_items(w._current_cat)
w.listw.viewport().grab()
w.cat_list.setCurrentRow(0)
w.listw.viewport().grab()
s.setValue("view_grid", "false")

# -- the two settings, where AGENTS.md puts them ----------------------------------------
from PyQt6.QtWidgets import QDialog, QPushButton
from dopeiptv.ui.settings_ui import SettingsShell, ToggleSwitch
calls = []
ytdlp.find = lambda *a, **k: ytdlp.Found(os.path.join(tmp, "yt-dlp"), True)
ytdlp.update = lambda *a, **k: calls.append("update")


def check(self):
    shell = self.findChild(SettingsShell)
    shell.build_all()                   # each page is built as it is needed
    pages = {p.key: p for p in shell.pages}
    switches = {x.accessibleName(): x for x in self.findChildren(ToggleSwitch)}
    managed, clicks = switches[tr("ytdlp_managed")], switches[tr("radio_report_clicks")]
    assert pages["playback"].isAncestorOf(managed) and managed.isChecked()
    assert pages["integrations"].isAncestorOf(clicks) and clicks.isChecked()
    btn = next(b for b in self.findChildren(QPushButton) if b.text() == tr("ytdlp_update_now"))
    assert pages["playback"].isAncestorOf(btn) and btn.isEnabled()
    managed.setChecked(False)
    assert s.value(ytdlp.SETTING_MANAGED) == "false" and not btn.isEnabled()
    managed.setChecked(True)
    btn.click()
    assert wait(lambda: calls == ["update"])          # Update now: only when newer
    clicks.setChecked(False)
    assert s.value(rb.SETTING_CLICKS) == "false"
    return 0


QDialog.exec = check
w.open_settings()
fake.close()
print("STREAMS_OK", flush=True)
os._exit(0)
"""


def test_radio_and_web_streams_in_the_window():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "STREAMS_OK" in out.stdout, out.stdout[-2000:] + out.stderr[-4000:]
