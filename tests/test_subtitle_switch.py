"""Turning subtitles on must not freeze a film.

mpv answers a subtitle track chosen during playback with a refresh seek:
it drops its buffer and asks the provider for the stream again from a
little earlier - five to seven seconds of frozen picture on a slow panel
(measured against a throttled HTTP server). So a film opens with a track
chosen even when none is to show, hidden; Off hides it and On shows it,
and the track is only changed when another one is asked for. A live
channel cannot be sought and opens as before.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QMenu
from dopeiptv.i18n import tr
from dopeiptv.media.embedded import EmbeddedPlayer

app = QApplication.instance() or QApplication([])
settings = QSettings("dopeiptv-test", "sub-switch")
settings.clear()
p = EmbeddedPlayer(settings=settings)


class FakeMpv(dict):
    pause = False
    sid = False
    track_list = []

    def __init__(self):
        super().__init__()
        self.cmds = []
        self.sets = []

    def __setitem__(self, k, v):
        self.sets.append((k, v))
        super().__setitem__(k, v)

    def command(self, *args):
        self.cmds.append(args)

    def play(self, url):
        self.cmds.append(("play", url))

    def __getattr__(self, n):
        return None


m = p.video.mpv = FakeMpv()

def menu():
    mm = QMenu()
    p.populate_options_menu(mm)
    sm = [a for a in mm.actions() if a.text() == tr("opt_subtitles")][0]
    return {a.text(): a.isChecked() for a in sm.menu().actions()
            if a.isCheckable()}

def subs(sel=1, default=False):
    return [{"type": "video", "id": 1, "selected": True},
            {"type": "sub", "id": 1, "lang": "swe", "selected": sel == 1,
             "default": default},
            {"type": "sub", "id": 2, "lang": "eng", "selected": sel == 2}]

# A film with subtitles off: a track is chosen, hidden.
settings.setValue("sub_mode", "off")
settings.setValue("sub_lang", "swe")
assert p.play("http://x/movie/1.mkv", "Film")
assert m["sid"] == "auto" and m["subs-fallback"] == "yes"
# The languages asked for first, then the app's own (English here).
assert m["slang"] == "swe,eng,en" and m["sub-visibility"] is False
m.track_list = subs(1)
m.sid = 1
assert menu()[tr("opt_off")] is True and menu()["swe"] is False

# On: shown, the track left alone - no refresh seek.
m.sets.clear()
p._pick_track("sid", 1)
assert ("sid", 1) not in m.sets, m.sets
assert m["sub-visibility"] is True
assert menu()[tr("opt_off")] is False and menu()["swe"] is True
# Off: hidden, still chosen - so on again is instant too.
m.sets.clear()
p._pick_track("sid", "no")
assert not [k for k, _v in m.sets if k == "sid"], m.sets
assert m["sub-visibility"] is False
# Another language is another track: that one is chosen - and remembered,
# so the next film opens with a track in it chosen, hidden.
p._pick_track("sid", 2)
assert ("sid", 2) in m.sets and m["sub-visibility"] is True
assert settings.value("sub_last_lang") == "eng"

# "auto": hidden at first; shown once the tracks are known if the file
# flags the chosen one - the track mpv itself showed before.
settings.setValue("sub_mode", "auto")
settings.setValue("sub_last_lang", "swe")
p.play("http://x/movie/2.mkv", "Film")
# Hidden, chosen by the language turned on last, then the app's.
assert m["sub-visibility"] is False and m["slang"] == "swe,eng,en"
m.track_list = []
p._poll_position()                       # not loaded yet: wait
assert m["sub-visibility"] is False and p._sub_rule is not None
m.track_list = subs(1, default=True)
p._poll_position()
assert m["sub-visibility"] is True and p._sub_rule is None
p.play("http://x/movie/3.mkv", "Film")
m.track_list = subs(1, default=False)
p._poll_position()
assert m["sub-visibility"] is False      # nothing flagged: stays off
# The file flags a track other than the one chosen by language: that one
# is taken at the start and shown, as mpv itself did before.
p.play("http://x/movie/3b.mkv", "Film")
m.sets.clear()
m.track_list = subs(2, default=True)     # eng chosen, swe flagged
p._poll_position()
assert ("sid", 1) in m.sets and m["sub-visibility"] is True

# By language: shown when the chosen one is in a language asked for.
settings.setValue("sub_mode", "lang")
settings.setValue("sub_lang", "eng")
p.play("http://x/movie/4.mkv", "Film")
assert m["slang"].split(",")[0] == "eng"
m.track_list = subs(2)
p._poll_position()
assert m["sub-visibility"] is True

# Remembered "off" for this title: hidden, never shown by the rule.
settings.setValue("sub_mode", "auto")
p.set_track_prefs(sid="no")
p.play("http://x/movie/5.mkv", "Film")
assert m["sid"] == "auto" and m["sub-visibility"] is False
assert p._sub_rule is None

# A live channel opens as before: off is no track at all.
settings.setValue("sub_mode", "off")
m.sets.clear()
p.play("http://x/live/1.ts", "Channel", local_buffer=True)
assert m["sid"] == "no" and m["sub-visibility"] is True
assert m["subs-fallback"] == "default"
p.stop()

# A Settings save mid-film does not choose a track: off only hides.
settings.setValue("sub_mode", "off")
m.sets.clear()
p.apply_default_options()
assert not [k for k, _v in m.sets if k == "sid"], m.sets
assert m["sub-visibility"] is False
# Out before Qt's teardown: the GL widget can crash it on the offscreen
# platform (the app sidesteps it the same way), and a crash there lost the
# marker still sitting in the stdout buffer - CI saw stdout='' and -11.
print("SUBSWITCH_OK", flush=True)
os._exit(0)
"""


def test_turning_subtitles_on_does_not_choose_a_track_again():
    pytest.importorskip("PyQt6")
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD], capture_output=True, text=True,
        cwd=_REPO_ROOT, timeout=180,
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
    assert "SUBSWITCH_OK" in proc.stdout, (
        f"stdout={proc.stdout!r}\nstderr={proc.stderr[-2500:]!r}")
