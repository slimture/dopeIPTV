#!/usr/bin/env python3
"""Settings files as older releases wrote them, for tests/test_upgrade.py.

    python3 tools/upgrade_fixtures.py            # every release in RELEASES
    python3 tools/upgrade_fixtures.py v1.6.8     # one

Each release's own code, taken from its tag (git archive), is started
offscreen with its own window and stores, and used as a person would: two
sources (an Xtream login and an M3U list), a favourite in a folder of its
own, a channel in History, a film part watched, a followed series, a
parental PIN, a language, a theme, subtitle and audio languages, the rewind
buffer and a Nerd setting. What it then has on disk - the main settings and
the resume, follow and cache files beside it - is copied into
tests/upgrade/<tag>/, with what it did and what it was told
(``made.json``), so the test starts today's app on exactly what that
release left behind.

Run it again when a release is added to RELEASES; the files are committed.
The release's code is the project's own, from its tags. Nothing is fetched
from the network: the window is given the demo source.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "upgrade"

#: The releases whose settings today's app must start on: the last of each
#: line, and those around a change of how settings are kept (profiles in
#: 1.6.0, the PIN's PBKDF2 in 1.6.11).
RELEASES = ("v1.2.18", "v1.4.5", "v1.5.0", "v1.6.0", "v1.6.5", "v1.6.8", "v1.6.11", "v1.6.12")

#: What the release is told, and the test then looks for.
EXPECT = {
    "xtream": {"name": "Upgrade Xtream", "kind": "xtream",
               "server": "http://provider.invalid:8080",
               "username": "olduser", "password": "oldpass"},
    "m3u": {"name": "Upgrade M3U", "kind": "m3u",
            "server": "http://lists.invalid/list.m3u"},
    "values": {"language": "sv", "theme": "nord", "audio_lang": "eng",
               "sub_lang": "swe", "live_rewind_minutes": "10",
               "nerd_enabled": "true", "nerd_user_agent": "kodi"},
    "pin": "4321",
    "folder": "Upgrade",
    "history": {"url": "http://provider.invalid:8080/live/olduser/oldpass/101.ts",
                "title": "Upgrade Channel", "key": "101", "kind": "live"},
    "resume": {"group": "vod", "key": "9001", "pos": 600.0, "dur": 3600.0,
               "item": {"stream_id": 9001, "name": "Upgrade Film",
                        "container_extension": "mp4"}},
    "series": {"series_id": 77, "name": "Upgrade Series"},
}

#: Placeholder for the folder the release ran in, in the files kept.
HOME_MARK = "@UPGRADE_HOME@"

_WRITER = r'''
import json, os, sys, time
src, out, expect = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
sys.path.insert(0, src)
from PyQt6.QtCore import QSettings, QTimer
from PyQt6.QtWidgets import QApplication
app = QApplication([])
from dopeiptv import ORG, __version__
made = {"version": __version__, "done": [], "failed": {}, "ids": {}}


def do(name, fn):
    try:
        fn()
        made["done"].append(name)
    except Exception as e:
        made["failed"][name] = f"{type(e).__name__}: {e}"


def pump(secs):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


ini, user = QSettings.Format.IniFormat, QSettings.Scope.UserScope
try:
    from dopeiptv.core import profiles
except ImportError:
    profiles = None
if profiles is not None and hasattr(profiles, "ProfileSettings"):
    s = profiles.ProfileSettings(ORG, ORG)
    files = [QSettings(ini, user, ORG, n) for n in ("resume", "follow", "cache")]
    try:
        profiles.start(s, files, "Me", __version__)
    except TypeError:
        profiles.start(s, files, "Me")
    made["profiles"] = True
else:
    s = QSettings(ORG, ORG)
from dopeiptv.core.stores import PlaylistStore
store = PlaylistStore(s)
x = store.add(dict(expect["xtream"]))
m = store.add(dict(expect["m3u"]))
store.set_active(x["id"])
made["ids"] = {"xtream": x["id"], "m3u": m["id"]}
for k, v in expect["values"].items():
    s.setValue(k, v)
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow
w = MainWindow(DemoClient(), s, store)
w.show()
pump(1.5)
row = None
try:
    row = dict(w.client.live_streams(None)[0])
except Exception as e:
    made["failed"]["demo_row"] = repr(e)
if row is not None:
    made["favorite"] = {k: row.get(k) for k in ("stream_id", "name")}
    do("favorite", lambda: w.favs.add(expect["folder"], row))
h = expect["history"]
do("history", lambda: w.history.add(h["url"], h["title"], None, h["key"], h["kind"]))
r = expect["resume"]
do("resume", lambda: w.resume.record(r["group"], r["key"], r["pos"], r["dur"], item=r["item"]))
do("follow", lambda: w.followed.follow(dict(expect["series"])))
do("pin", lambda: w.parental.set_pin(expect["pin"]))
pump(0.5)
w.close()
pump(1.0)
for q in [s, getattr(w, "_resume_settings", None), getattr(w, "_follow_settings", None),
          getattr(w, "_cache_settings", None)]:
    if q is not None:
        try:
            q.sync()
        except Exception:
            pass
with open(os.path.join(out, "made.json"), "w", encoding="utf-8") as f:
    json.dump(made, f, indent=2, sort_keys=True)
print("WRITTEN", __version__, made["done"], made["failed"], flush=True)
os._exit(0)
'''


def _source(tag: str, into: Path) -> Path:
    """The release's package from its tag, into *into*/src."""
    src = into / "src"
    src.mkdir()
    tar = subprocess.run(["git", "archive", "--format=tar", tag, "dopeiptv"],
                         cwd=ROOT, capture_output=True, check=True).stdout
    archive = into / "src.tar"
    archive.write_bytes(tar)
    with tarfile.open(archive) as t:
        t.extractall(src, filter="data")
    return src


def make(tag: str) -> Path:
    with tempfile.TemporaryDirectory(prefix="dopeiptv-upgrade-") as tmp:
        work = Path(tmp)
        src = _source(tag, work)
        home = work / "home"
        for sub in ("config", "cache", "data", "run"):
            (home / sub).mkdir(parents=True)
        (home / "run").chmod(0o700)
        out = work / "out"
        out.mkdir()
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", HOME=str(home),
                   XDG_CONFIG_HOME=str(home / "config"), XDG_CACHE_HOME=str(home / "cache"),
                   XDG_DATA_HOME=str(home / "data"), XDG_RUNTIME_DIR=str(home / "run"),
                   DOPEIPTV_LOG="warning")
        env.pop("PYTHONPATH", None)
        done = subprocess.run([sys.executable, "-c", _WRITER, str(src), str(out),
                               json.dumps(EXPECT)],
                              cwd=str(work), env=env, capture_output=True, text=True, timeout=300)
        if "WRITTEN" not in done.stdout:
            raise SystemExit(f"{tag}: the release did not write its settings:\n"
                             f"{done.stdout[-2000:]}\n{done.stderr[-4000:]}")
        print(done.stdout.strip().splitlines()[-1])
        folder = home / "config" / "dopeiptv"
        dest = OUT / tag
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        for f in sorted(folder.iterdir()):
            if f.is_file():
                text = f.read_text(encoding="utf-8").replace(str(home), HOME_MARK)
                (dest / f.name).write_text(text, encoding="utf-8")
        made = json.loads((out / "made.json").read_text(encoding="utf-8"))
        made["tag"] = tag
        made["expect"] = EXPECT
        (dest / "made.json").write_text(json.dumps(made, indent=2, sort_keys=True) + "\n",
                                        encoding="utf-8")
        return dest


def main(argv: list[str]) -> int:
    for tag in argv or RELEASES:
        print(tag, "->", make(tag).relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
