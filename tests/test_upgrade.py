"""Today's app started on the settings an older release left behind.

tests/upgrade/<tag>/ holds what each release in tools/upgrade_fixtures.py's
RELEASES wrote with its own window and stores (made.json says what it was
told and did): two sources with a login, a favourite in a folder of its
own, a channel in History, a film part watched, a followed series, a
parental PIN, a language, a theme, subtitle and audio languages, the rewind
buffer and a Nerd setting.

Each is copied where the app keeps its settings and started the way main()
starts (app._settings_at_start, _profiles_at_start, _sources_at_start), the
window then made on it. Everything the release kept must still be there -
the sources with their logins and the one in use, every value, the first
profile in use, the favourite in its folder, History, the resume point, the
followed series, the PIN (an older release's SHA-256 form too) - and a
second start must change nothing more.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "upgrade"
HOME_MARK = "@UPGRADE_HOME@"
RELEASES = sorted(p.name for p in FIXTURES.iterdir() if (p / "made.json").is_file())

_CHILD = r"""
import json, os, sys, time
fixture = sys.argv[1]
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
app = QApplication([])
from dopeiptv import ORG
from dopeiptv import app as start
from dopeiptv.core import nerd, profiles
from dopeiptv.i18n import set_language, tr

made = json.load(open(os.path.join(fixture, "made.json"), encoding="utf-8"))
exp = made["expect"]
folder = os.path.join(os.environ["XDG_CONFIG_HOME"], ORG)


def pump(secs):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def started():
    # main()'s own steps, in its order, before the window.
    settings = start._settings_at_start()
    set_language(settings.value("language", "en"))
    start._profiles_at_start(settings, tr("profile_me"))
    store = start._sources_at_start(settings)
    nerd.install(settings)
    return settings, store


def on_disk():
    # Every file of the app's settings as key -> value, read afresh.
    out = {}
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        q = QSettings(path, QSettings.Format.IniFormat)
        for key in q.allKeys():
            out[f"{name}:{key}"] = str(q.value(key))
    return out


settings, store = started()

# -- the sources, with their logins, and the one in use
by_name = {p.get("name"): p for p in store.playlists()}
x, m = by_name[exp["xtream"]["name"]], by_name[exp["m3u"]["name"]]
for k in ("kind", "server", "username", "password"):
    assert x.get(k) == exp["xtream"][k], (k, x.get(k))
assert m.get("kind") == "m3u" and m.get("server") == exp["m3u"]["server"], m
assert len(by_name) == 2, sorted(by_name)
assert store.active_id == made["ids"]["xtream"], store.active_id

# -- every value as it was set, the profile's own and the household's
for k, v in exp["values"].items():
    assert str(settings.value(k)) == v, (k, settings.value(k))
assert profiles.active_id() == "me", profiles.active_id()

# -- the window on them: favourites, History, resume, followed, the PIN
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow
w = MainWindow(DemoClient(), settings, store)
w.show()
pump(1.0)
fav = made["favorite"]
assert exp["folder"] in w.favs.group_names(), w.favs.group_names()
assert any(str(i.get("stream_id")) == str(fav["stream_id"]) and i.get("name") == fav["name"]
           for i in w.favs.items(exp["folder"])), w.favs.items(exp["folder"])
h = exp["history"]
assert any(str(e.get("_key")) == h["key"] and e.get("name") == h["title"]
           for e in w.history.items()), w.history.items()
r = exp["resume"]
kind = {"vod": "movie"}.get(r["group"], r["group"])     # a film's group is "vod"
assert abs(w.resume.saved_position(r["key"], kind) - r["pos"]) < 1.5, \
    w.resume.saved_position(r["key"], kind)
assert w.followed.is_followed(exp["series"]["series_id"])
assert w.parental.has_pin()
assert not w.parental.verify("0000")
assert w.parental.verify(exp["pin"])
w.close()
pump(0.5)
for q in (settings, getattr(w, "_resume_settings", None), getattr(w, "_follow_settings", None),
          getattr(w, "_cache_settings", None)):
    if q is not None:
        q.sync()

# -- a second start on what this one left moves nothing more
before = on_disk()
settings, store = started()
settings.sync()
after = on_disk()
moved = {k: (before.get(k), after.get(k)) for k in set(before) | set(after)
         if before.get(k) != after.get(k)}
assert not moved, moved
print("UPGRADE_OK", made["version"], flush=True)
os._exit(0)
"""


def _place(tag: str, home: Path) -> Path:
    """The release's files where the app keeps its settings, under *home*."""
    folder = home / "config" / "dopeiptv"
    folder.mkdir(parents=True)
    for f in (FIXTURES / tag).iterdir():
        if f.name == "made.json":
            continue
        text = f.read_text(encoding="utf-8").replace(HOME_MARK, str(home))
        (folder / f.name).write_text(text, encoding="utf-8")
    return folder


def test_every_release_tools_upgrade_fixtures_names_is_here():
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import upgrade_fixtures
    finally:
        sys.path.pop(0)
    assert RELEASES == sorted(upgrade_fixtures.RELEASES), (RELEASES, upgrade_fixtures.RELEASES)
    for tag in RELEASES:
        made = json.loads((FIXTURES / tag / "made.json").read_text(encoding="utf-8"))
        assert made["failed"] == {}, (tag, made["failed"])
        assert set(made["done"]) == {"favorite", "history", "resume", "follow", "pin"}, made["done"]


@pytest.mark.parametrize("tag", RELEASES)
def test_todays_app_starts_on_what_this_release_left(tag, tmp_path):
    home = tmp_path / "home"
    for sub in ("cache", "data", "run"):
        (home / sub).mkdir(parents=True)
    (home / "run").chmod(0o700)
    _place(tag, home)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", HOME=str(home),
               XDG_CONFIG_HOME=str(home / "config"), XDG_CACHE_HOME=str(home / "cache"),
               XDG_DATA_HOME=str(home / "data"), XDG_RUNTIME_DIR=str(home / "run"))
    out = subprocess.run([sys.executable, "-c", _CHILD, str(FIXTURES / tag)], cwd=str(ROOT),
                         env=env, capture_output=True, text=True, timeout=180)
    assert "UPGRADE_OK" in out.stdout, (tag, out.stdout[-2000:] + out.stderr[-4000:])


_NAMED_CHILD = r"""
import json, os, sys
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
app = QApplication([])
from dopeiptv import ORG
from dopeiptv import app as start
from dopeiptv.i18n import set_language, tr

folder = os.path.join(os.environ["XDG_CONFIG_HOME"], ORG)
main = QSettings(os.path.join(folder, ORG + ".conf"), QSettings.Format.IniFormat)
rows = json.loads(main.value("playlists"))
server = "http://kund:hemligt@line.invalid:8080"
# What 1.6.12 named a source added with no name (mw_onboarding._wizard_connect
# and the quick form's values(), word for word): its login with it.
rows.append({"name": server.split("//")[-1].split("/")[0], "kind": "xtream", "server": server,
             "username": "kund", "password": "hemligt", "id": "c0ffee12"})
main.setValue("playlists", json.dumps(rows))
main.sync()
del main


def started():
    settings = start._settings_at_start()
    set_language(settings.value("language", "en"))
    start._profiles_at_start(settings, tr("profile_me"))
    return settings, start._sources_at_start(settings)


settings, store = started()
by_id = {p["id"]: p for p in store.playlists()}
named = by_id["c0ffee12"]
assert named["name"] == "line.invalid:8080", named["name"]
assert (named["server"], named["username"], named["password"]) == (server, "kund", "hemligt")
assert sorted(p["name"] for p in store.playlists()) == [
    "Upgrade M3U", "Upgrade Xtream", "line.invalid:8080"], store.playlists()
assert "kund:hemligt@line" not in json.dumps([p["name"] for p in store.playlists()])
settings.sync()
before = QSettings(os.path.join(folder, ORG + ".conf"), QSettings.Format.IniFormat).value("playlists")
settings, store = started()
settings.sync()
after = QSettings(os.path.join(folder, ORG + ".conf"), QSettings.Format.IniFormat).value("playlists")
assert before == after, "a second start renames nothing"
print("NAMED_OK", flush=True)
os._exit(0)
"""


def test_a_source_1612_named_by_its_login_address_goes_by_its_host(tmp_path):
    """1.6.12's settings with a source it named by its address - the login
    with it ("kund:hemligt@line.invalid:8080"): today's start names it by
    its host and port, its login kept where a login is kept, once."""
    home = tmp_path / "home"
    for sub in ("cache", "data", "run"):
        (home / sub).mkdir(parents=True)
    (home / "run").chmod(0o700)
    _place("v1.6.12", home)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", HOME=str(home),
               XDG_CONFIG_HOME=str(home / "config"), XDG_CACHE_HOME=str(home / "cache"),
               XDG_DATA_HOME=str(home / "data"), XDG_RUNTIME_DIR=str(home / "run"))
    out = subprocess.run([sys.executable, "-c", _NAMED_CHILD], cwd=str(ROOT), env=env,
                         capture_output=True, text=True, timeout=180)
    assert "NAMED_OK" in out.stdout, out.stdout[-2000:] + out.stderr[-4000:]
