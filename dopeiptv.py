#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dopeIPTV - an elegant IPTV client for Xtream Codes with EPG.
Plays back via mpv or VLC. Requires: python3, PyQt6, requests.

    pip install PyQt6 requests
    sudo apt install mpv vlc

Run with:  python3 dopeiptv.py
"""

import base64
import hashlib
import html
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests
from PyQt6.QtCore import (
    QAbstractListModel, QByteArray, QModelIndex, QObject, QRect, QRectF,
    QRunnable, QSettings, QSize, Qt, QThreadPool, QTimer, pyqtSignal, pyqtSlot,
)
from PyQt6.QtGui import (
    QColor, QFont, QIcon, QKeySequence, QOpenGLContext, QPainter,
    QPainterPath, QPixmap, QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListView, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QStyle, QStyledItemDelegate, QTabWidget, QVBoxLayout, QWidget,
)
from PyQt6.QtOpenGLWidgets import QOpenGLWidget

APP_NAME = "dopeIPTV"
ORG = "dopeiptv"
VERSION = "0.0.2-alpha"

# Optional embedded playback via libmpv (python-mpv). Imported lazily so the
# app still runs fine without it - playback then falls back to the reused
# external mpv window. The exception is kept (not just swallowed) so the UI
# can explain *why* embedding isn't available instead of silently falling
# back to an external window with no explanation.
_libmpv_error = None
try:
    import mpv as _libmpv          # pip install python-mpv (needs libmpv)
except Exception as _e:
    _libmpv = None
    _libmpv_error = f"{type(_e).__name__}: {_e}"

# Optional Chromecast support (pip install pychromecast). Imported lazily so
# the app runs fine without it; the Cast menu explains what to install.
try:
    import pychromecast as _pychromecast
except Exception:
    _pychromecast = None


def embedded_playback_reason():
    """Returns None if in-app video is available, otherwise a short
    human-readable explanation of why it isn't (shown in Settings). Embedding
    uses libmpv's OpenGL render API (frames drawn into a QOpenGLWidget), not
    native window embedding, so it doesn't depend on X11 vs. Wayland or on
    any particular compositor - it needs only python-mpv/libmpv and a
    working OpenGL context, which Qt provides on Linux, macOS and Windows
    alike."""
    if _libmpv is None:
        return f"python-mpv/libmpv failed to load ({_libmpv_error})"
    if not hasattr(_libmpv, "MpvRenderContext"):
        return "installed python-mpv is too old (needs the render-api support)"
    return None


def embedded_playback_supported():
    return embedded_playback_reason() is None

# ----------------------------------------------------------------------------
#  Xtream Codes API client
# ----------------------------------------------------------------------------

class XtreamClient:
    def __init__(self, server: str, username: str, password: str):
        self.server = server.rstrip("/")
        if not self.server.startswith(("http://", "https://")):
            self.server = "http://" + self.server
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "dopeIPTV/1.0"

    def _api(self, **params):
        url = f"{self.server}/player_api.php"
        base = {"username": self.username, "password": self.password}
        base.update(params)
        r = self.session.get(url, params=base, timeout=20)
        r.raise_for_status()
        return r.json()

    def authenticate(self):
        data = self._api()
        if not isinstance(data, dict) or "user_info" not in data:
            raise RuntimeError("Unexpected response from the server.")
        if str(data["user_info"].get("auth", 0)) != "1":
            raise RuntimeError("Wrong username or password.")
        return data

    def live_categories(self):
        return self._api(action="get_live_categories") or []

    def live_streams(self, category_id=None):
        p = {"action": "get_live_streams"}
        if category_id:
            p["category_id"] = category_id
        return self._api(**p) or []

    def vod_categories(self):
        return self._api(action="get_vod_categories") or []

    def vod_streams(self, category_id=None):
        p = {"action": "get_vod_streams"}
        if category_id:
            p["category_id"] = category_id
        return self._api(**p) or []

    def series_categories(self):
        return self._api(action="get_series_categories") or []

    def series_list(self, category_id=None):
        p = {"action": "get_series"}
        if category_id:
            p["category_id"] = category_id
        return self._api(**p) or []

    def series_info(self, series_id):
        return self._api(action="get_series_info", series_id=series_id) or {}

    def vod_info(self, vod_id):
        return self._api(action="get_vod_info", vod_id=vod_id) or {}

    def short_epg(self, stream_id, limit=8):
        data = self._api(action="get_short_epg", stream_id=stream_id, limit=limit)
        return (data or {}).get("epg_listings", [])

    def epg_table(self, stream_id):
        """Full EPG table - fallback when get_short_epg returns nothing."""
        data = self._api(action="get_simple_data_table", stream_id=stream_id)
        return (data or {}).get("epg_listings", [])

    def xmltv(self):
        """The provider's full XMLTV guide - last resort when player_api has no EPG."""
        r = self.session.get(f"{self.server}/xmltv.php",
                             params={"username": self.username,
                                     "password": self.password},
                             timeout=(20, 180))
        r.raise_for_status()
        return r.content

    def live_url(self, stream_id, fmt="ts"):
        ext = "m3u8" if fmt == "m3u8" else "ts"
        return f"{self.server}/live/{self.username}/{self.password}/{stream_id}.{ext}"

    def vod_url(self, stream_id, ext):
        ext = ext or "mp4"
        return f"{self.server}/movie/{self.username}/{self.password}/{stream_id}.{ext}"

    def episode_url(self, episode_id, ext):
        ext = ext or "mp4"
        return f"{self.server}/series/{self.username}/{self.password}/{episode_id}.{ext}"


def b64(text):
    """Xtream sends EPG text fields as base64."""
    if not text:
        return ""
    try:
        return html.unescape(base64.b64decode(text).decode("utf-8", "replace")).strip()
    except Exception:
        return str(text)


def epg_times(entry):
    """Returns (start, stop) as local datetimes, or (None, None)."""
    def parse(ts_key, str_key):
        v = entry.get(ts_key)
        if v:
            try:
                return datetime.fromtimestamp(int(v), tz=timezone.utc).astimezone()
            except Exception:
                pass
        v = entry.get(str_key)
        if v:
            for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    return datetime.strptime(v, f).astimezone()
                except Exception:
                    continue
        return None
    return parse("start_timestamp", "start"), parse("stop_timestamp", "end")

# ----------------------------------------------------------------------------
#  Player executable lookup (handles macOS app bundles not on PATH)
# ----------------------------------------------------------------------------

def find_player_executable(player):
    """Locates the mpv/vlc binary, including common macOS install paths that
    aren't on PATH when the app is launched outside a terminal."""
    if player == "mpv":
        candidates = ["mpv"]
        if sys.platform == "darwin":
            candidates += ["/opt/homebrew/bin/mpv", "/usr/local/bin/mpv"]
    else:
        candidates = ["vlc", "cvlc"]
        if sys.platform == "darwin":
            candidates += ["/Applications/VLC.app/Contents/MacOS/VLC"]
    for c in candidates:
        if os.path.isabs(c):
            if os.path.isfile(c) and os.access(c, os.X_OK):
                return c
        else:
            found = shutil.which(c)
            if found:
                return found
    return None

# ----------------------------------------------------------------------------
#  XMLTV guide (last-resort EPG source)
# ----------------------------------------------------------------------------

def _normalize_name(s):
    """Normalizes a channel name for matching ("SE: SVT 1 HD" ~ "svt1hd")."""
    return "".join(c for c in (s or "").lower() if c.isalnum())


def _parse_xmltv_time(s):
    """Parses an XMLTV timestamp: YYYYMMDDHHMMSS [+-ZZZZ]."""
    s = (s or "").strip()
    try:
        if len(s) > 14:
            return datetime.strptime(s, "%Y%m%d%H%M%S %z").astimezone()
        return datetime.strptime(s[:14], "%Y%m%d%H%M%S").astimezone()
    except ValueError:
        return None


class XmltvGuide:
    """Downloads and indexes the provider's XMLTV guide (xmltv.php).

    Many providers send no EPG via player_api but do have a full schedule
    in XMLTV format. The guide is fetched at most once per session, in a
    background thread, the first time it's needed. Once loaded, lookups
    are pure in-memory dict access, so the main channel list can show
    "now playing" for every visible row without any extra network calls.
    """

    def __init__(self, client, custom_url=None):
        self.client = client
        self.custom_url = custom_url    # user-supplied XMLTV URL, if any
        self._lock = threading.Lock()
        self._loaded = False
        self._failed = False
        self._by_id = {}        # channel id -> entries sorted by start time
        self._by_name = {}      # normalized display name -> channel id

    def _fetch(self):
        if self.custom_url:
            r = requests.get(self.custom_url, timeout=(20, 180))
            r.raise_for_status()
            return r.content
        return self.client.xmltv()

    def ensure_loaded(self):
        """Fetches and indexes the guide if needed. Returns True if it's
        available in memory (safe to call repeatedly; blocks briefly if
        another thread is already loading it)."""
        with self._lock:
            if not self._loaded and not self._failed:
                try:
                    self._parse(self._fetch())
                    self._loaded = True
                except Exception:
                    self._failed = True
            return self._loaded

    def _entries_for(self, item):
        cid = (item.get("epg_channel_id") or "").strip().lower()
        entries = self._by_id.get(cid)
        if entries is None:       # no id match - fall back to channel name
            cid = self._by_name.get(_normalize_name(item.get("name")))
            entries = self._by_id.get(cid, [])
        return entries

    def listings_for(self, item, limit=8):
        if not self.ensure_loaded():
            return []
        now = datetime.now().astimezone().timestamp()
        return [p for p in self._entries_for(item)
                if p["stop_timestamp"] > now][:limit]

    def now_for(self, item):
        """(title, percent) for the currently airing programme, or None.
        Pure in-memory lookup - safe to call from the paint path."""
        if not self._loaded:
            return None
        now = datetime.now().astimezone().timestamp()
        for p in self._entries_for(item):
            if p["start_timestamp"] <= now < p["stop_timestamp"]:
                length = p["stop_timestamp"] - p["start_timestamp"]
                pct = (now - p["start_timestamp"]) / length * 100 if length else 0
                return p["title"], pct
        return None

    def _parse(self, data):
        cutoff = datetime.now().astimezone().timestamp() - 3 * 3600
        by_id, by_name = {}, {}
        seen = set()
        for _, el in ET.iterparse(io.BytesIO(data)):
            if el.tag == "channel":
                cid = (el.get("id") or "").strip().lower()
                if cid:
                    for dn in el.findall("display-name"):
                        name = _normalize_name(dn.text)
                        if name:
                            by_name.setdefault(name, cid)
                el.clear()
            elif el.tag == "programme":
                cid = (el.get("channel") or "").strip().lower()
                start = _parse_xmltv_time(el.get("start"))
                stop = _parse_xmltv_time(el.get("stop"))
                title = (el.findtext("title") or "").strip()
                if cid and start and stop and stop.timestamp() > cutoff:
                    dedup_key = (cid, int(start.timestamp()), title.lower())
                    if dedup_key not in seen:
                        seen.add(dedup_key)
                        by_id.setdefault(cid, []).append({
                            "_plain": True,
                            "title": title,
                            "description": (el.findtext("desc") or "").strip(),
                            "start_timestamp": int(start.timestamp()),
                            "stop_timestamp": int(stop.timestamp()),
                        })
                el.clear()
        for lst in by_id.values():
            lst.sort(key=lambda p: p["start_timestamp"])
        self._by_id, self._by_name = by_id, by_name

# ----------------------------------------------------------------------------
#  Favorites
# ----------------------------------------------------------------------------

class FavoriteStore:
    """Favorite channels in user-defined groups, persisted via QSettings.
    The key is per-playlist so favorites follow the provider they belong to
    (stream ids are only meaningful within one provider)."""

    def __init__(self, settings, key="favorites"):
        self.settings = settings
        self.key = key
        try:
            self.groups = json.loads(settings.value(key, "") or "{}")
        except Exception:
            self.groups = {}
        if not isinstance(self.groups, dict):
            self.groups = {}

    def _save(self):
        self.settings.setValue(self.key, json.dumps(self.groups))

    def group_names(self):
        return sorted(self.groups, key=str.lower)

    def add(self, group, item):
        items = self.groups.setdefault(group, [])
        stream_id = item.get("stream_id")
        if not any(x.get("stream_id") == stream_id for x in items):
            items.append(item)
        self._save()

    def remove(self, stream_id, group=None):
        """Removes the channel from one group, or from all groups if group is None."""
        for g in ([group] if group else list(self.groups)):
            self.groups[g] = [x for x in self.groups.get(g, [])
                              if x.get("stream_id") != stream_id]
        self._save()

    def remove_group(self, group):
        self.groups.pop(group, None)
        if group in self.locked_groups():
            self.set_group_locked(group, False)
        self._save()

    # -- parental locking (stored beside the groups, backward compatible) ----
    def locked_groups(self):
        try:
            locked = json.loads(self.settings.value(f"{self.key}_locked", "") or "[]")
        except Exception:
            locked = []
        return set(locked) if isinstance(locked, list) else set()

    def set_group_locked(self, group, locked):
        current = self.locked_groups()
        (current.add if locked else current.discard)(group)
        self.settings.setValue(f"{self.key}_locked", json.dumps(sorted(current)))

    def is_locked(self, group):
        return group in self.locked_groups()

    def items(self, group=None, exclude_groups=()):
        if group:
            return list(self.groups.get(group, []))
        result, seen = [], set()
        for g in self.group_names():
            if g in exclude_groups:
                continue
            for it in self.groups[g]:
                stream_id = it.get("stream_id")
                if stream_id not in seen:
                    seen.add(stream_id)
                    result.append(it)
        return result

# ----------------------------------------------------------------------------
#  Watch history
# ----------------------------------------------------------------------------

class HistoryStore:
    """Recently played items, persisted via QSettings. Stores a resolved
    playback URL directly so replaying an entry doesn't depend on the
    original category/series context still being available."""

    MAX_ENTRIES = 300

    def __init__(self, settings, key="history"):
        self.settings = settings
        self.key = key
        try:
            self.entries = json.loads(settings.value(key, "") or "[]")
        except Exception:
            self.entries = []
        if not isinstance(self.entries, list):
            self.entries = []

    def _save(self):
        self.settings.setValue(self.key, json.dumps(self.entries[:self.MAX_ENTRIES]))

    def add(self, url, title, icon_url, key, kind):
        if not url:
            return
        self.entries = [e for e in self.entries
                        if not (e.get("_key") == key and e.get("_kind") == kind)]
        self.entries.insert(0, {
            "name": title, "stream_icon": icon_url,
            "_url": url, "_key": key, "_kind": kind,
            "_watched_at": datetime.now().isoformat(),
        })
        self.entries = self.entries[:self.MAX_ENTRIES]
        self._save()

    def remove(self, key, kind):
        self.entries = [e for e in self.entries
                        if not (e.get("_key") == key and e.get("_kind") == kind)]
        self._save()

    def clear(self):
        self.entries = []
        self._save()

    def items(self):
        return list(self.entries)

# ----------------------------------------------------------------------------
#  Parental control (PIN) and per-category overrides (content manager)
# ----------------------------------------------------------------------------

class ParentalControl:
    """A PIN (stored salted+hashed, never in clear text) gating locked
    categories and locked favorite groups. Once entered, the unlock lasts
    for the session or until 'Lock now' is used."""

    def __init__(self, settings):
        self.settings = settings
        self.session_unlocked = False

    def has_pin(self):
        return bool(self.settings.value("parental_pin_hash", ""))

    @staticmethod
    def _hash(salt, pin):
        return hashlib.sha256((salt + pin).encode()).hexdigest()

    def set_pin(self, pin):
        # Deliberately does NOT unlock the session: the common flow is
        # "create PIN, then lock something" - if creating the PIN unlocked
        # the session, the freshly locked content would stay browsable until
        # the next restart, defeating the point of locking it.
        salt = uuid.uuid4().hex
        self.settings.setValue("parental_salt", salt)
        self.settings.setValue("parental_pin_hash", self._hash(salt, pin))
        self.session_unlocked = False

    def clear_pin(self):
        self.settings.remove("parental_pin_hash")
        self.settings.remove("parental_salt")
        self.session_unlocked = False

    def verify(self, pin):
        salt = self.settings.value("parental_salt", "")
        stored = self.settings.value("parental_pin_hash", "")
        return bool(stored) and self._hash(salt, pin) == stored

    def lock_session(self):
        self.session_unlocked = False


class CategoryOverrides:
    """Per-playlist category customizations for the content manager:
    hidden (not listed, contents excluded from 'All'), a display-name
    override, and locked (requires the parental PIN to open; contents
    excluded from 'All' while locked)."""

    def __init__(self, settings, key="category_overrides"):
        self.settings = settings
        self.key = key
        try:
            self.data = json.loads(settings.value(key, "") or "{}")
        except Exception:
            self.data = {}
        if not isinstance(self.data, dict):
            self.data = {}

    def _save(self):
        self.settings.setValue(self.key, json.dumps(self.data))

    def get(self, mode, cid):
        return self.data.get(mode, {}).get(str(cid), {})

    def update(self, mode, cid, **fields):
        entry = self.data.setdefault(mode, {}).setdefault(str(cid), {})
        entry.update(fields)
        # drop empty entries to keep the stored JSON tidy
        if not any(entry.values()):
            del self.data[mode][str(cid)]
        self._save()

    def display_name(self, mode, cid, default):
        return self.get(mode, cid).get("name") or default

    def is_hidden(self, mode, cid):
        return bool(self.get(mode, cid).get("hidden"))

    def is_locked(self, mode, cid):
        return bool(self.get(mode, cid).get("locked"))

    def excluded_ids(self, mode, include_locked=True):
        """Category ids whose contents should be excluded from 'All'."""
        out = set()
        for cid, entry in self.data.get(mode, {}).items():
            if entry.get("hidden") or (include_locked and entry.get("locked")):
                out.add(str(cid))
        return out

# ----------------------------------------------------------------------------
#  Playlists (multiple providers/accounts)
# ----------------------------------------------------------------------------

class PlaylistStore:
    """User playlists (Xtream accounts/providers), persisted via QSettings.
    Each playlist: id, name, server, username, password, epg_url (optional
    custom XMLTV guide URL) and refresh (auto-refresh cadence). Migrates the
    pre-playlist single server/username/password settings into a 'Default'
    playlist on first run, carrying favorites/history along."""

    def __init__(self, settings):
        self.settings = settings
        try:
            data = json.loads(settings.value("playlists", "") or "[]")
        except Exception:
            data = []
        self.items = data if isinstance(data, list) else []
        self.active_id = settings.value("active_playlist", "")
        if not self.items:
            server = settings.value("server", "")
            user = settings.value("username", "")
            pw = settings.value("password", "")
            if server and user and pw:
                self.items = [{"id": "default", "name": "Default",
                               "server": server, "username": user,
                               "password": pw, "epg_url": "",
                               "refresh": "never"}]
                self.active_id = "default"
                for legacy in ("favorites", "history"):
                    value = settings.value(legacy, "")
                    if value:
                        settings.setValue(f"{legacy}_default", value)
                self._save()

    def _save(self):
        self.settings.setValue("playlists", json.dumps(self.items))
        self.settings.setValue("active_playlist", self.active_id)

    def playlists(self):
        return list(self.items)

    def get(self, pid):
        return next((p for p in self.items if p.get("id") == pid), None)

    def active(self):
        return self.get(self.active_id) or (self.items[0] if self.items else None)

    def add(self, playlist):
        playlist.setdefault("id", uuid.uuid4().hex[:8])
        self.items.append(playlist)
        if not self.active_id:
            self.active_id = playlist["id"]
        self._save()
        return playlist

    def update(self, pid, **fields):
        p = self.get(pid)
        if p:
            p.update(fields)
            self._save()

    def remove(self, pid):
        self.items = [p for p in self.items if p.get("id") != pid]
        if self.active_id == pid:
            self.active_id = self.items[0]["id"] if self.items else ""
        self._save()

    def set_active(self, pid):
        if self.get(pid):
            self.active_id = pid
            self._save()

# ----------------------------------------------------------------------------
#  Thread-pool workers
# ----------------------------------------------------------------------------

class WorkerSignals(QObject):
    done = pyqtSignal(object)
    fail = pyqtSignal(str)
    finished = pyqtSignal()


class Worker(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        # QThreadPool must not delete us on the pool thread: WorkerSignals
        # lives in the main thread and would otherwise be destroyed from the
        # wrong thread mid-signal-delivery (segfault). Lifetime is instead
        # managed by _ACTIVE_WORKERS below.
        self.setAutoDelete(False)
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = WorkerSignals()

    @pyqtSlot()
    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as e:
            self.signals.fail.emit(str(e))
        else:
            self.signals.done.emit(result)
        finally:
            self.signals.finished.emit()


_ACTIVE_WORKERS = set()


def run_async(pool, fn, on_done, on_fail=None, *args, **kwargs):
    w = Worker(fn, *args, **kwargs)
    w.signals.done.connect(on_done)
    if on_fail:
        w.signals.fail.connect(on_fail)
    # Keep a reference until all queued signals are delivered on the main
    # thread, so the worker (and its signals object) is freed there.
    _ACTIVE_WORKERS.add(w)
    w.signals.finished.connect(lambda: _ACTIVE_WORKERS.discard(w))
    pool.start(w)
    return w

# ----------------------------------------------------------------------------
#  Logo cache (asynchronous download)
# ----------------------------------------------------------------------------

class LogoLoader(QObject):
    def __init__(self, pool):
        super().__init__()
        self.pool = pool
        self.cache = {}
        self.waiting = {}          # url -> [callbacks]

    def get(self, url, callback):
        if not url:
            return
        if url in self.cache:
            callback(self.cache[url])
            return
        if url in self.waiting:
            self.waiting[url].append(callback)
            return
        self.waiting[url] = [callback]

        def fetch(u=url):
            r = requests.get(u, timeout=10)
            r.raise_for_status()
            return u, r.content

        def done(result):
            u, data = result
            callbacks = self.waiting.pop(u, [])
            pm = QPixmap()
            if pm.loadFromData(data):
                pm = pm.scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
                self.cache[u] = pm
                for cb in callbacks:
                    try:
                        cb(pm)
                    except RuntimeError:
                        pass   # the widget was already deleted (list rebuilt)

        run_async(self.pool, fetch, done, lambda _: self.waiting.pop(url, None))

# ----------------------------------------------------------------------------
#  External playback: mpv / VLC (spawns a new process each time)
# ----------------------------------------------------------------------------

def launch_player(player, url, title, parent=None):
    title = title or "dopeIPTV"
    exe = find_player_executable(player)
    if player == "mpv":
        cmd = [exe, "--force-media-title=" + title,
               "--user-agent=dopeIPTV/1.0", url] if exe else None
        name = "mpv"
    else:
        cmd = [exe, "--meta-title", title, "--http-user-agent=dopeIPTV/1.0",
               url] if exe else None
        name = "VLC"
    if not cmd:
        QMessageBox.warning(parent, "Player not found",
                            f"{name} was not found. Install it, e.g.\n\n"
                            f"  sudo apt install {name.lower()}")
        return
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)

# ----------------------------------------------------------------------------
#  Persistent mpv playback via its JSON IPC socket (channel zapping)
# ----------------------------------------------------------------------------

class MpvIpcPlayer:
    """Controls a single mpv process over its JSON IPC socket, so switching
    channels reuses the existing mpv window instead of spawning a new
    process each time. Falls back to reporting failure if mpv is missing;
    the caller decides whether to fall back to launch_player()."""

    def __init__(self):
        self.proc = None
        self.sock = None
        self.socket_path = os.path.join(
            tempfile.gettempdir(), f"dopeiptv-mpv-{os.getpid()}.sock")

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def _spawn(self):
        exe = find_player_executable("mpv")
        if not exe:
            return False
        try:
            os.remove(self.socket_path)
        except OSError:
            pass
        cmd = [exe, f"--input-ipc-server={self.socket_path}",
               "--idle=yes", "--force-window=yes",
               "--user-agent=dopeIPTV/1.0"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL,
                                     start_new_session=True)
        for _ in range(60):          # wait up to ~3s for the socket to appear
            if os.path.exists(self.socket_path):
                return True
            time.sleep(0.05)
        return False

    def _connect(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.socket_path)

    def _send(self, command):
        payload = (json.dumps({"command": command}) + "\n").encode()
        self.sock.sendall(payload)

    def load(self, url, title):
        """Loads url into the running mpv instance, starting one if needed.
        Call from a worker thread: spawning can block briefly."""
        if not self.is_running():
            if not self._spawn():
                return False
            self._connect()
        if self.sock is None:
            self._connect()
        try:
            self._send(["loadfile", url, "replace"])
            self._send(["set_property", "force-media-title", title])
            return True
        except OSError:
            self.proc = None
            if self._spawn():
                self._connect()
                try:
                    self._send(["loadfile", url, "replace"])
                    self._send(["set_property", "force-media-title", title])
                    return True
                except OSError:
                    return False
            return False

    def stop(self):
        if self.is_running():
            try:
                self._send(["quit"])
            except OSError:
                pass
        self.proc = None
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

def _register_error_callback(mpv_instance, signal):
    """Emits `signal` with a human-readable message whenever a loaded file/
    stream ends because of an error (unreachable server, dead stream, ...).
    The callback fires on mpv's event thread; emitting a Qt signal is the
    thread-safe way to get back onto the main thread."""
    @mpv_instance.event_callback("end-file")
    def _on_end_file(evt):
        try:
            data = evt.data
            if getattr(data, "reason", None) == _libmpv.MpvEventEndFile.ERROR:
                try:
                    msg = _libmpv.ErrorCode.human_readable(data.error)
                except Exception:
                    msg = "playback failed"
                signal.emit(msg)
        except Exception:
            pass

# ----------------------------------------------------------------------------
#  Persistent mpv window via python-mpv (in-process, key-bindable)
#
#  MpvIpcPlayer above spawns mpv as a *separate process*; Qt shortcuts like
#  Ctrl+Right only fire while the dopeIPTV window itself has keyboard focus,
#  so zapping stops working the moment the user clicks into that external
#  window to actually watch. python-mpv instead embeds libmpv in this same
#  process (no subprocess, no socket), so we can bind zap keys *inside mpv*
#  - mpv intercepts them itself and calls back into Python regardless of
#  which window currently has focus. Used automatically when python-mpv is
#  available; falls back to MpvIpcPlayer (needs only the mpv binary) when it
#  isn't installed.
# ----------------------------------------------------------------------------

class MpvWindowPlayer(QObject):
    zap_requested = pyqtSignal(int)   # emitted from mpv's own event thread
    playback_error = pyqtSignal(str)
    closed = pyqtSignal()             # window closed / quit by the user

    def __init__(self):
        super().__init__()
        self._mpv = None
        # Closing (q / window X) fires on mpv's event thread; terminating the
        # core from there can deadlock, so bounce to the main thread.
        self.closed.connect(self._on_closed)

    def _ensure_mpv(self):
        if self._mpv is None:
            # Explicitly request mpv's normal standalone behavior (fullscreen
            # via 'f', quit via 'q', on-screen controller) - without these,
            # the window mpv opens doesn't respond to its own default keys
            # at all, which read as "can't fullscreen / can't close it".
            m = _libmpv.MPV(force_window=True, input_default_bindings=True,
                            input_vo_keyboard=True, osc=True,
                            title="dopeIPTV", user_agent="dopeIPTV/1.0",
                            keep_open="yes")
            m.on_key_press("ctrl+right")(lambda: self.zap_requested.emit(1))
            m.on_key_press("ctrl+left")(lambda: self.zap_requested.emit(-1))
            # Also give an explicit close key and route the window's own quit
            # (q / titlebar X -> SHUTDOWN) back to us so the window actually
            # goes away and the next play can spin up a fresh one.
            m.on_key_press("q")(lambda: self.closed.emit())
            _register_error_callback(m, self.playback_error)

            @m.event_callback("shutdown")
            def _on_shutdown(_evt):
                self.closed.emit()

            self._mpv = m
        return self._mpv

    def _on_closed(self):
        self.shutdown()

    def play(self, url, title):
        for attempt in range(2):
            try:
                m = self._ensure_mpv()
                try:
                    m["force-media-title"] = title or "dopeIPTV"
                except Exception:
                    pass
                m.play(url)
                return True
            except Exception as e:
                print(f"[dopeIPTV] mpv window playback failed: "
                     f"{type(e).__name__}: {e}", file=sys.stderr)
                self._mpv = None   # drop a possibly-dead core, retry once
        return False

    def toggle_fullscreen(self):
        if not self._mpv:
            return
        try:
            self._mpv.fullscreen = not self._mpv.fullscreen
        except Exception as e:
            print(f"[dopeIPTV] mpv fullscreen toggle failed: "
                 f"{type(e).__name__}: {e}", file=sys.stderr)

    def is_active(self):
        return self._mpv is not None

    def stop(self):
        if self._mpv:
            try:
                self._mpv.command("stop")
            except Exception:
                pass

    def shutdown(self):
        if self._mpv:
            try:
                self._mpv.terminate()
            except Exception:
                pass
            self._mpv = None

# ----------------------------------------------------------------------------
#  Chromecast casting (optional, via pychromecast)
# ----------------------------------------------------------------------------

def cast_content_type(url):
    """Best-effort MIME type for the Chromecast receiver."""
    u = (url or "").lower().split("?")[0]
    if u.endswith(".m3u8"):
        return "application/x-mpegURL"
    if u.endswith(".ts"):
        return "video/mp2t"
    if u.endswith(".mkv"):
        return "video/x-matroska"
    if u.endswith(".webm"):
        return "video/webm"
    return "video/mp4"


class ChromecastManager:
    """Discovers Chromecast devices on the LAN and plays a stream URL on
    one. All methods that hit the network (scan/cast/stop) block and must be
    called from a worker thread (run_async)."""

    def __init__(self):
        self.devices = []
        self.active = None
        self._browser = None

    @staticmethod
    def available():
        return _pychromecast is not None

    def scan(self):
        if self._browser is not None:
            try:
                self._browser.stop_discovery()
            except Exception:
                pass
            self._browser = None
        devices, browser = _pychromecast.get_chromecasts(timeout=6)
        self._browser = browser
        self.devices = devices
        return sorted(cc.name for cc in devices)

    def cast(self, device_name, url, title):
        cc = next((c for c in self.devices if c.name == device_name), None)
        if cc is None:
            raise RuntimeError(f"device '{device_name}' not found - rescan")
        cc.wait(timeout=10)
        mc = cc.media_controller
        mc.play_media(url, cast_content_type(url), title=title or "dopeIPTV")
        mc.block_until_active(timeout=10)
        self.active = cc
        return device_name

    def stop(self):
        if self.active:
            try:
                self.active.media_controller.stop()
            except Exception:
                pass
            self.active = None

    def shutdown(self):
        self.stop()
        if self._browser is not None:
            try:
                self._browser.stop_discovery()
            except Exception:
                pass
        for cc in self.devices:
            try:
                cc.disconnect(timeout=2)
            except Exception:
                pass


class CastDialog(QDialog):
    """Scan for Chromecast devices and cast the given stream to one."""

    def __init__(self, window, url, title):
        super().__init__(window)
        self.window = window
        self.url = url
        self.stream_title = title
        self.setWindowTitle("Cast to Chromecast")
        self.setMinimumWidth(400)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)

        self.status = QLabel("Scanning for Chromecast devices...")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self._cast())
        lay.addWidget(self.list, 1)

        btns = QHBoxLayout()
        self.rescan_btn = QPushButton("Rescan")
        self.cast_btn = QPushButton("Cast", objectName="Primary")
        self.stop_btn = QPushButton("Stop casting")
        close_btn = QPushButton("Close")
        for b in (self.rescan_btn, self.cast_btn, self.stop_btn, close_btn):
            btns.addWidget(b)
        lay.addLayout(btns)

        self.rescan_btn.clicked.connect(self._scan)
        self.cast_btn.clicked.connect(self._cast)
        self.stop_btn.clicked.connect(self._stop)
        close_btn.clicked.connect(self.accept)
        self._scan()

    def _set_status(self, text):
        try:
            self.status.setText(text)
        except RuntimeError:
            pass                      # dialog already closed

    def _scan(self):
        self._set_status("Scanning for Chromecast devices...")
        self.rescan_btn.setEnabled(False)

        def done(names):
            try:
                self.rescan_btn.setEnabled(True)
                self.list.clear()
                for name in names or []:
                    self.list.addItem(name)
                self._set_status(
                    f"{len(names)} device(s) found." if names
                    else "No Chromecast devices found on this network.")
                if names:
                    self.list.setCurrentRow(0)
            except RuntimeError:
                pass

        def fail(msg):
            try:
                self.rescan_btn.setEnabled(True)
            except RuntimeError:
                return
            self._set_status(f"Scan failed: {msg}")

        run_async(self.window.pool, self.window.cast.scan, done, fail)

    def _cast(self):
        item = self.list.currentItem()
        if not item:
            return
        name = item.text()
        self._set_status(f"Starting cast to {name}...")
        run_async(self.window.pool,
                  lambda: self.window.cast.cast(name, self.url, self.stream_title),
                  lambda n: self._set_status(f"Casting to {n}."),
                  lambda msg: self._set_status(f"Cast failed: {msg}"))

    def _stop(self):
        run_async(self.window.pool, self.window.cast.stop,
                  lambda _: self._set_status("Casting stopped."),
                  lambda msg: self._set_status(f"Stop failed: {msg}"))

# ----------------------------------------------------------------------------
#  Embedded in-app video (libmpv's OpenGL render API)
#
#  An earlier version used mpv's --wid option, which embeds by reparenting
#  a native X11 window - that relies on X11 window-manager cooperation that
#  several Wayland compositors (GNOME/Mutter in particular) don't provide
#  for XWayland clients: mpv reports success, but the video renders as its
#  own floating window instead of inside the app. The render API sidesteps
#  window embedding entirely - mpv just draws video frames into an OpenGL
#  framebuffer we hand it - so it works the same way regardless of
#  compositor, and (being pure OpenGL) is the same approach usable on
#  macOS, unlike --wid which libmpv never supported there at all.
# ----------------------------------------------------------------------------

class _MpvGLWidget(QOpenGLWidget):
    """The actual video surface. Owns the mpv render context; EmbeddedPlayer
    wraps this with the title/stop/fullscreen bar around it."""

    frame_ready = pyqtSignal()
    playback_error = pyqtSignal(str)   # emitted from mpv's event thread

    # Extra mpv options, overridable by tests (e.g. {"vo": "null"} headless).
    EXTRA_OPTS = {}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.mpv = None
        self._ctx = None
        self.frame_ready.connect(self.update)

    def _get_proc_address(self, _, name):
        glctx = QOpenGLContext.currentContext()
        if glctx is None:
            return 0
        return int(glctx.getProcAddress(QByteArray(name)))

    def initializeGL(self):
        opts = {"vo": "libmpv", "user_agent": "dopeIPTV/1.0", "keep_open": "yes"}
        opts.update(self.EXTRA_OPTS)
        self.mpv = _libmpv.MPV(**opts)
        # get_proc_address must be a real ctypes callback - mpv holds a raw
        # function pointer to it, so the wrapped object is kept alive on
        # self for as long as the render context exists.
        self._proc_address_fn = _libmpv.MpvGlGetProcAddressFn(self._get_proc_address)
        self._ctx = _libmpv.MpvRenderContext(
            self.mpv, "opengl",
            opengl_init_params={"get_proc_address": self._proc_address_fn})
        # Fires on mpv's render thread - only touch Qt via the signal.
        self._ctx.update_cb = lambda: self.frame_ready.emit()
        _register_error_callback(self.mpv, self.playback_error)

    def paintGL(self):
        if not self._ctx:
            return
        ratio = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1
        self._ctx.render(flip_y=True, opengl_fbo={
            "w": int(self.width() * ratio), "h": int(self.height() * ratio),
            "fbo": self.defaultFramebufferObject(),
        })

    def shutdown(self):
        if self._ctx:
            try:
                self._ctx.free()
            except Exception:
                pass
            self._ctx = None
        if self.mpv:
            try:
                self.mpv.terminate()
            except Exception:
                pass
            self.mpv = None


class EmbeddedPlayer(QWidget):
    """Video pane inside the app, rendered via libmpv's OpenGL render API.
    In fullscreen the control bar is hidden entirely; instead a translucent
    overlay with the channel/EPG info fades in on mouse movement and hides
    itself after a few seconds, so the video keeps the whole screen."""

    double_clicked = pyqtSignal()
    playback_error = pyqtSignal(str)

    OVERLAY_HIDE_MS = 3000

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.video = _MpvGLWidget(self)
        self.video.installEventFilter(self)
        self.video.setMouseTracking(True)
        self.video.playback_error.connect(self.playback_error)
        lay.addWidget(self.video, 1)

        self.bar = QWidget()
        bl = QHBoxLayout(self.bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(8)
        self.title_lbl = QLabel("", objectName="DetailMeta")
        self.stop_btn = QPushButton("Stop", objectName="MiniBtn")
        self.stop_btn.clicked.connect(self.stop)
        self.fs_btn = QPushButton("Fullscreen", objectName="MiniBtn")
        bl.addWidget(self.title_lbl, 1)
        bl.addWidget(self.stop_btn)
        bl.addWidget(self.fs_btn)
        lay.addWidget(self.bar)

        # Auto-hiding info overlay (fullscreen only)
        self.overlay = QLabel("", self)
        self.overlay.setStyleSheet(
            "background: rgba(16,16,20,210); color:#ECECF1;"
            "border-radius:10px; padding:10px 14px; font-size:13px;")
        self.overlay.setWordWrap(True)
        self.overlay.hide()
        self._fs_ui = False
        self._overlay_text = ""
        self._overlay_timer = QTimer(self)
        self._overlay_timer.setSingleShot(True)
        self._overlay_timer.setInterval(self.OVERLAY_HIDE_MS)
        self._overlay_timer.timeout.connect(self.overlay.hide)

    def eventFilter(self, obj, event):
        if obj is self.video:
            if event.type() == event.Type.MouseButtonDblClick:
                self.double_clicked.emit()
                return True
            if event.type() == event.Type.MouseMove and self._fs_ui:
                self._show_overlay()
        return super().eventFilter(obj, event)

    # -- overlay -----------------------------------------------------------
    def set_overlay_info(self, text):
        self._overlay_text = text or ""
        if self._fs_ui and self.overlay.isVisible():
            self.overlay.setText(self._overlay_text)
            self._place_overlay()

    def set_fullscreen_ui(self, fullscreen):
        self._fs_ui = fullscreen
        self.bar.setVisible(not fullscreen)
        if fullscreen:
            self._show_overlay()
        else:
            self.overlay.hide()
            self._overlay_timer.stop()

    def _show_overlay(self):
        if not self._overlay_text:
            return
        self.overlay.setText(self._overlay_text)
        self._place_overlay()
        self.overlay.show()
        self.overlay.raise_()
        self._overlay_timer.start()

    def _place_overlay(self):
        margin = 24
        self.overlay.setFixedWidth(min(self.width() - 2 * margin, 640))
        self.overlay.adjustSize()
        self.overlay.move(margin, self.height() - self.overlay.height() - margin)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.overlay.isVisible():
            self._place_overlay()

    def play(self, url, title):
        try:
            self.title_lbl.setText(title or "")
            if self.video.mpv is None:
                # First use: force Qt to create the GL context and run
                # initializeGL() now (it otherwise only happens lazily on
                # the next natural paint event).
                self.video.show()
                QApplication.instance().processEvents()
            if self.video.mpv is None:
                raise RuntimeError("OpenGL context not ready")
            m = self.video.mpv
            try:
                m["force-media-title"] = title or "dopeIPTV"
            except Exception:
                pass
            m.play(url)
            return True
        except Exception as e:
            print(f"[dopeIPTV] Embedded playback failed: "
                 f"{type(e).__name__}: {e}", file=sys.stderr)
            return False

    def stop(self):
        if self.video.mpv:
            try:
                self.video.mpv.command("stop")
            except Exception:
                pass
        self.title_lbl.setText("")

    def shutdown(self):
        self.video.shutdown()

# ----------------------------------------------------------------------------
#  Style - dark, macOS-inspired (dopeIPTV look), unified across all widgets
# ----------------------------------------------------------------------------

ACCENT = "#4C8DFF"

STYLE = f"""
* {{
    font-family: "SF Pro Text", "Inter", "Cantarell", "Noto Sans", sans-serif;
    color: #ECECF1;
}}
QMainWindow, QDialog {{ background: #17171C; }}

/* Sidebar */
#Sidebar {{
    background: #101014;
    border-right: 1px solid #232329;
}}
#AppTitle {{ font-size: 15px; font-weight: 700; letter-spacing: 0.5px; }}
#AppSub   {{ color: #7A7A85; font-size: 11px; }}

QPushButton#NavBtn {{
    background: transparent; border: none; border-radius: 8px;
    padding: 8px 12px; text-align: left; font-size: 13px; color: #C9C9D2;
}}
QPushButton#NavBtn:hover  {{ background: #1D1D24; }}
QPushButton#NavBtn:checked {{ background: {ACCENT}; color: white; font-weight: 600; }}

#SectionLabel {{
    color: #6E6E79; font-size: 10px; font-weight: 700;
    letter-spacing: 1.2px; padding: 10px 14px 4px 14px;
}}

QListWidget {{
    background: transparent; border: none; outline: none; font-size: 13px;
}}
QListWidget::item {{ border-radius: 8px; padding: 7px 10px; margin: 1px 6px; color: #C9C9D2; }}
QListWidget::item:hover    {{ background: #1D1D24; }}
QListWidget::item:selected {{ background: #26262E; color: white; }}

/* Middle column */
#MiddlePane {{ background: #17171C; }}
QLineEdit#Search {{
    background: #222229; border: 1px solid #2C2C34; border-radius: 9px;
    padding: 8px 12px; font-size: 13px;
}}
QLineEdit#Search:focus {{ border: 1px solid {ACCENT}; }}

QListView#Channels {{
    background: transparent; border: none; outline: none; font-size: 13px;
}}

#ChNum   {{ font-size: 11px; color: #5A5A64; }}

QProgressBar#LoadBar {{
    background: transparent; border: none; max-height: 3px;
}}
QProgressBar#LoadBar::chunk {{ background: {ACCENT}; }}

QProgressBar#EpgBar {{
    background: #2A2A32; border: none; border-radius: 2px; max-height: 4px;
}}
QProgressBar#EpgBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}

/* Detail panel */
#DetailPane {{ background: #1B1B21; border-left: 1px solid #232329; }}
#DetailTitle {{ font-size: 20px; font-weight: 700; }}
#DetailMeta  {{ color: #8B8B96; font-size: 12px; }}
#NowTitle    {{ font-size: 14px; font-weight: 600; }}
#NowTime     {{ color: {ACCENT}; font-size: 11px; font-weight: 600; }}
#NowDesc     {{ color: #A7A7B1; font-size: 12px; }}

QFrame#Card {{
    background: #222229; border: 1px solid #2C2C34; border-radius: 12px;
}}
QLabel#EpgRowTime  {{ color: {ACCENT}; font-size: 11px; font-weight: 600; }}
QLabel#EpgRowTitle {{ font-size: 12px; }}

QPushButton {{
    background: #2A2A32; border: 1px solid #34343E; border-radius: 9px;
    padding: 9px 16px; font-size: 13px; font-weight: 600;
}}
QPushButton:hover  {{ background: #34343E; }}
QPushButton#Primary {{ background: {ACCENT}; border: none; color: white; }}
QPushButton#Primary:hover {{ background: #5E99FF; }}
QPushButton#MiniBtn {{ padding: 4px 10px; font-size: 11px; border-radius: 7px; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #33333C; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #45454F; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}

/* Menu bar + context menus: dark on every platform (Linux GTK/Qt themes
   default to a white menu bar and popup unless styled explicitly; macOS's
   native dark menu was the look we want everywhere). */
QMenuBar {{
    background: #101014; color: #C9C9D2; border-bottom: 1px solid #232329;
}}
QMenuBar::item {{
    background: transparent; padding: 4px 10px; margin: 0; border-radius: 6px;
    font-size: 12px;
}}
QMenuBar::item:selected {{ background: #1D1D24; }}
QMenuBar::item:pressed {{ background: {ACCENT}; color: white; }}

QMenu {{
    background: #1D1D24; border: 1px solid #2C2C34; border-radius: 8px;
    padding: 5px; font-size: 12px;
}}
QMenu::item {{
    background: transparent; color: #ECECF1; border-radius: 6px;
    padding: 5px 20px 5px 10px; font-size: 12px;
}}
QMenu::item:selected {{ background: {ACCENT}; color: white; }}
QMenu::item:disabled {{ color: #6E6E79; }}
QMenu::separator {{ height: 1px; background: #2C2C34; margin: 5px 8px; }}

QToolTip {{
    background: #1D1D24; color: #ECECF1; border: 1px solid #2C2C34;
    padding: 4px 6px;
}}

/* Tab widget (Settings): the platform default renders a white pane/tab bar */
QTabWidget::pane {{
    border: 1px solid #2C2C34; border-radius: 8px; background: #1B1B21;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent; color: #C9C9D2; padding: 7px 16px;
    border-radius: 7px; margin: 2px; font-size: 12px;
}}
QTabBar::tab:selected {{ background: #2A2A32; color: white; }}
QTabBar::tab:hover:!selected {{ background: #1D1D24; }}

QComboBox {{
    background: #222229; border: 1px solid #2C2C34; border-radius: 8px;
    padding: 5px 10px; font-size: 12px;
    combobox-popup: 0;   /* plain dropdown sized to its items - no scrolling */
}}
QComboBox QAbstractItemView {{
    background: #222229; border: 1px solid #2C2C34; border-radius: 6px;
    selection-background-color: {ACCENT}; selection-color: white;
    outline: none; font-size: 12px; padding: 3px;
}}
QComboBox QAbstractItemView::item {{ min-height: 22px; padding: 3px 8px; }}
QComboBox#InlineCombo {{ padding: 3px 8px; font-size: 11px; }}
QPushButton#InlineToggle {{
    padding: 4px 12px; font-size: 11px; border-radius: 7px;
}}
QPushButton#InlineToggle:checked {{ background: {ACCENT}; border: none; color: white; }}
#MiddlePane QLabel {{ color: #6E6E79; font-size: 11px; }}
QLineEdit {{
    background: #222229; border: 1px solid #2C2C34; border-radius: 8px;
    padding: 8px 10px;
}}
QLineEdit:focus {{ border: 1px solid {ACCENT}; }}
"""

# ----------------------------------------------------------------------------
#  Login dialog
# ----------------------------------------------------------------------------

class LoginDialog(QDialog):
    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect to an Xtream server")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(14)

        heading = QLabel("dopeIPTV")
        heading.setStyleSheet("font-size:20px; font-weight:700;")
        subtitle = QLabel("Sign in with your Xtream Codes credentials.")
        subtitle.setStyleSheet("color:#8B8B96;")
        lay.addWidget(heading)
        lay.addWidget(subtitle)

        form = QFormLayout()
        form.setSpacing(10)
        self.server = QLineEdit(settings.value("server", ""))
        self.server.setPlaceholderText("http://server:port")
        self.user = QLineEdit(settings.value("username", ""))
        self.user.setPlaceholderText("username")
        self.pw = QLineEdit(settings.value("password", ""))
        self.pw.setPlaceholderText("password")
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Server", self.server)
        form.addRow("Username", self.user)
        form.addRow("Password", self.pw)
        lay.addLayout(form)

        self.status = QLabel("")
        self.status.setStyleSheet("color:#FF6B6B; font-size:12px;")
        lay.addWidget(self.status)

        buttons = QDialogButtonBox()
        self.ok = buttons.addButton("Connect", QDialogButtonBox.ButtonRole.AcceptRole)
        self.ok.setObjectName("Primary")
        buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def values(self):
        return self.server.text().strip(), self.user.text().strip(), self.pw.text().strip()

# ----------------------------------------------------------------------------
#  Playlist add/edit dialog
# ----------------------------------------------------------------------------

class PlaylistDialog(QDialog):
    REFRESH_OPTIONS = [("never", "Never"), ("startup", "At startup"),
                       ("2h", "Every 2 hours"), ("6h", "Every 6 hours"),
                       ("12h", "Every 12 hours"), ("24h", "Daily"),
                       ("1w", "Weekly")]

    def __init__(self, parent=None, playlist=None):
        super().__init__(parent)
        playlist = playlist or {}
        self.setWindowTitle("Edit playlist" if playlist else "Add playlist")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 22, 22, 22)
        form = QFormLayout()
        form.setSpacing(10)
        self.name = QLineEdit(playlist.get("name", ""))
        self.name.setPlaceholderText("e.g. My provider")
        self.server = QLineEdit(playlist.get("server", ""))
        self.server.setPlaceholderText("http://server:port")
        self.user = QLineEdit(playlist.get("username", ""))
        self.pw = QLineEdit(playlist.get("password", ""))
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.epg_url = QLineEdit(playlist.get("epg_url", ""))
        self.epg_url.setPlaceholderText("optional - overrides the provider's xmltv.php")
        self.refresh = QComboBox()
        for value, label in self.REFRESH_OPTIONS:
            self.refresh.addItem(label, value)
        idx = self.refresh.findData(playlist.get("refresh", "never"))
        if idx >= 0:
            self.refresh.setCurrentIndex(idx)
        form.addRow("Name", self.name)
        form.addRow("Server", self.server)
        form.addRow("Username", self.user)
        form.addRow("Password", self.pw)
        form.addRow("Custom TV guide URL", self.epg_url)
        form.addRow("Auto-refresh", self.refresh)
        lay.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                  QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _validate(self):
        if not (self.server.text().strip() and self.user.text().strip()
                and self.pw.text().strip()):
            QMessageBox.warning(self, "Playlist",
                                "Server, username and password are required.")
            return
        self.accept()

    def values(self):
        name = self.name.text().strip()
        if not name:
            # fall back to the server host as a display name
            name = self.server.text().strip().split("//")[-1].split("/")[0]
        return {"name": name,
                "server": self.server.text().strip(),
                "username": self.user.text().strip(),
                "password": self.pw.text().strip(),
                "epg_url": self.epg_url.text().strip(),
                "refresh": self.refresh.currentData()}

# ----------------------------------------------------------------------------
#  Channel list: virtualized model + delegate
#
#  QListWidget with a QWidget per row (the previous approach) creates one
#  persistent widget subtree per item; with a few thousand channels that
#  freezes the UI on load and on every search keystroke. A QListView with a
#  plain data model and a custom-painted delegate only ever touches the
#  handful of rows actually on screen, so it stays fast regardless of how
#  many channels a provider has - no artificial cap needed.
# ----------------------------------------------------------------------------

class ChannelListModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self.kind = "live"

    def rowCount(self, parent=QModelIndex()):
        return len(self._items)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._items)):
            return None
        it = self._items[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return it
        if role == Qt.ItemDataRole.DisplayRole:
            return it.get("name") or it.get("title") or "?"
        return None

    def set_items(self, items, kind):
        self.beginResetModel()
        self._items = items
        self.kind = kind
        self.endResetModel()

    def item_at(self, row):
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def refresh_all(self):
        if self._items:
            self.dataChanged.emit(self.index(0), self.index(len(self._items) - 1))


class ChannelDelegate(QStyledItemDelegate):
    # List mode: (row height, logo size, name pt, sub pt) per density level
    DENSITIES = {"compact": (50, 32, 10, 8),
                 "medium":  (66, 44, 11, 9),
                 "large":   (92, 64, 13, 10)}
    # Grid mode: (cell w, cell h, logo size, name pt) per density level
    GRID = {"compact": (108, 116, 60, 9),
            "medium":  (140, 150, 84, 10),
            "large":   (184, 196, 120, 11)}

    def __init__(self, window, density="medium", grid=False):
        super().__init__(window)
        self.window = window
        self.grid = grid
        self.set_density(density)

    def set_density(self, level):
        self.level = level if level in self.DENSITIES else "medium"
        self.row_h, self.logo_sz, self.name_pt, self.sub_pt = \
            self.DENSITIES[self.level]
        self.cell_w, self.cell_h, self.grid_logo, self.grid_name_pt = \
            self.GRID[self.level]

    def set_grid(self, grid):
        self.grid = grid

    def grid_size(self):
        return QSize(self.cell_w, self.cell_h)

    def sizeHint(self, option, index):
        if self.grid:
            return QSize(self.cell_w, self.cell_h)
        return QSize(0, self.row_h)

    def paint(self, painter, option, index):
        if self.grid:
            self._paint_grid(painter, option, index)
        else:
            self._paint_list(painter, option, index)

    def _paint_grid(self, painter, option, index):
        it = index.data(Qt.ItemDataRole.UserRole) or {}
        rect = option.rect
        logo_sz = self.grid_logo
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        inner = rect.adjusted(5, 5, -5, -5)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#26262E"))
            painter.drawRoundedRect(inner, 12, 12)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#1D1D24"))
            painter.drawRoundedRect(inner, 12, 12)

        name = it.get("name") or it.get("title") or "?"
        logo_x = rect.left() + (rect.width() - logo_sz) // 2
        logo_y = rect.top() + 12
        logo_rect = QRect(logo_x, logo_y, logo_sz, logo_sz)
        radius = max(8, logo_sz // 5)
        url = it.get("stream_icon") or it.get("cover")
        pm = self.window.logos.cache.get(url) if url else None
        if pm:
            path = QPainterPath()
            path.addRoundedRect(QRectF(logo_rect), radius, radius)
            painter.setClipPath(path)
            scaled = pm.scaled(logo_sz, logo_sz, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap(logo_x + (logo_sz - scaled.width()) // 2,
                               logo_y + (logo_sz - scaled.height()) // 2, scaled)
            painter.setClipping(False)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#26262E"))
            painter.drawRoundedRect(logo_rect, radius, radius)
            painter.setPen(QColor("#ECECF1"))
            f = QFont(); f.setPointSize(max(14, logo_sz // 3)); f.setBold(True)
            painter.setFont(f)
            painter.drawText(logo_rect, Qt.AlignmentFlag.AlignCenter,
                             name.strip()[:1].upper())
            if url and url not in self.window.logos.waiting:
                self.window.logos.get(url, lambda _pm: self.window.listw.viewport().update())

        painter.setPen(QColor("#ECECF1"))
        fname = QFont(); fname.setPointSize(self.grid_name_pt); fname.setBold(True)
        painter.setFont(fname)
        text_rect = QRect(rect.left() + 4, logo_y + logo_sz + 6,
                          rect.width() - 8, rect.bottom() - (logo_y + logo_sz + 6))
        fm = painter.fontMetrics()
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                         fm.elidedText(name, Qt.TextElideMode.ElideRight, text_rect.width()))
        painter.restore()

    def _paint_list(self, painter, option, index):
        it = index.data(Qt.ItemDataRole.UserRole) or {}
        kind = index.model().kind
        rect = option.rect
        logo_sz = self.logo_sz
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, QColor("#26262E"))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(rect, QColor("#1D1D24"))

        name = it.get("name") or it.get("title") or "?"
        logo_rect = QRect(rect.left() + 10,
                          rect.top() + (rect.height() - logo_sz) // 2,
                          logo_sz, logo_sz)
        radius = max(6, logo_sz // 4)
        url = it.get("stream_icon") or it.get("cover")
        pm = self.window.logos.cache.get(url) if url else None
        if pm:
            path = QPainterPath()
            path.addRoundedRect(QRectF(logo_rect), radius, radius)
            painter.setClipPath(path)
            scaled = pm.scaled(logo_sz, logo_sz,
                               Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            x = logo_rect.x() + (logo_sz - scaled.width()) // 2
            y = logo_rect.y() + (logo_sz - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
            painter.setClipping(False)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#26262E"))
            painter.drawRoundedRect(logo_rect, radius, radius)
            painter.setPen(QColor("#ECECF1"))
            f = QFont()
            f.setPointSize(max(12, logo_sz // 3))
            f.setBold(True)
            painter.setFont(f)
            painter.drawText(logo_rect, Qt.AlignmentFlag.AlignCenter,
                             name.strip()[:1].upper())
            if url and url not in self.window.logos.waiting:
                self.window.logos.get(url, lambda _pm: self.window.listw.viewport().update())

        num_w = 0
        if kind in ("live", "fav") and it.get("num"):
            num_w = 34
            painter.setPen(QColor("#5A5A64"))
            fnum = QFont()
            fnum.setPointSize(10)
            painter.setFont(fnum)
            num_rect = QRect(rect.right() - 12 - num_w, rect.top(), num_w, rect.height())
            painter.drawText(num_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                             str(it["num"]))

        text_x = logo_rect.right() + 12
        text_w = max(0, rect.right() - 12 - num_w - text_x)
        now = self.window.xmltv.now_for(it) if kind in ("live", "fav") else None

        # Vertically center the text block (name [+ now line + progress bar])
        # so it stays balanced at every density.
        name_h = self.name_pt + 8
        sub_h = (self.sub_pt + 6) if now else 0
        bar_h = 6 if now else 0
        block_h = name_h + sub_h + bar_h
        y = rect.top() + (rect.height() - block_h) // 2

        painter.setPen(QColor("#ECECF1"))
        fname = QFont()
        fname.setPointSize(self.name_pt)
        fname.setBold(True)
        painter.setFont(fname)
        fm = painter.fontMetrics()
        painter.drawText(QRect(text_x, y, text_w, name_h),
                         Qt.AlignmentFlag.AlignVCenter,
                         fm.elidedText(name, Qt.TextElideMode.ElideRight, text_w))

        if now:
            title, pct = now
            painter.setPen(QColor("#8B8B96"))
            fsub = QFont()
            fsub.setPointSize(self.sub_pt)
            painter.setFont(fsub)
            fm2 = painter.fontMetrics()
            painter.drawText(QRect(text_x, y + name_h, text_w, sub_h),
                             Qt.AlignmentFlag.AlignVCenter,
                             fm2.elidedText("Now: " + title, Qt.TextElideMode.ElideRight, text_w))
            bar_rect = QRect(text_x, y + name_h + sub_h, text_w, 4)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#2A2A32"))
            painter.drawRoundedRect(bar_rect, 2, 2)
            fill_w = int(bar_rect.width() * max(0, min(100, pct)) / 100)
            if fill_w > 0:
                painter.setBrush(QColor(ACCENT))
                painter.drawRoundedRect(QRect(bar_rect.x(), bar_rect.y(), fill_w,
                                              bar_rect.height()), 2, 2)
        painter.restore()

# ----------------------------------------------------------------------------
#  EPG guide dialog (channel schedule overview)
# ----------------------------------------------------------------------------

class EpgGuideDialog(QDialog):
    MAX_ROWS = 2000

    def __init__(self, window, channels):
        super().__init__(window)
        self.window = window
        self.channels = channels
        self.setWindowTitle("EPG Guide")
        self.resize(560, 640)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)

        self.search = QLineEdit(placeholderText="Filter channels...")
        self.search.textChanged.connect(self._populate)
        lay.addWidget(self.search)

        self.info_lbl = QLabel("")
        self.info_lbl.setStyleSheet("color:#6E6E79; font-size:11px;")
        lay.addWidget(self.info_lbl)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._play_selected)
        lay.addWidget(self.list, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons)

        self._populate()

    def _populate(self, _text=None):
        self.list.clear()
        text = self.search.text().lower().strip()
        shown = 0
        for it in self.channels:
            name = it.get("name") or "?"
            if text and text not in name.lower():
                continue
            shown += 1
            if shown > self.MAX_ROWS:
                break
            now = self.window.xmltv.now_for(it)
            if now:
                label = f"{name}\nNow: {now[0]}  ({int(now[1])}%)"
            else:
                label = f"{name}\nNo programme data"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, it)
            self.list.addItem(item)
        note = f"{shown} channels"
        if shown > self.MAX_ROWS:
            note += f" (showing first {self.MAX_ROWS} - narrow your search)"
        self.info_lbl.setText(note)

    def _play_selected(self, item):
        it = item.data(Qt.ItemDataRole.UserRole)
        self.window.play_live_channel(it)
        self.accept()

# ----------------------------------------------------------------------------
#  Content manager dialog (hide / rename / lock categories)
# ----------------------------------------------------------------------------

class ContentManagerDialog(QDialog):
    def __init__(self, window, mode, categories, overrides):
        super().__init__(window)
        self.window = window
        self.mode = mode
        self.categories = categories
        self.overrides = overrides
        self.setWindowTitle("Manage categories")
        self.resize(460, 520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)

        hint = QLabel("Hidden categories disappear from the sidebar and their "
                      "channels are left out of 'All'. Locked categories need "
                      "the parental PIN to open.")
        hint.setStyleSheet("color:#6E6E79; font-size:11px;")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.list = QListWidget()
        lay.addWidget(self.list, 1)

        btns = QHBoxLayout()
        rename_btn = QPushButton("Rename...")
        self.hide_btn = QPushButton("Hide")
        self.lock_btn = QPushButton("Lock")
        for b in (rename_btn, self.hide_btn, self.lock_btn):
            btns.addWidget(b)
        lay.addLayout(btns)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons)

        rename_btn.clicked.connect(self._rename)
        self.hide_btn.clicked.connect(self._toggle_hidden)
        self.lock_btn.clicked.connect(self._toggle_locked)
        self.list.currentItemChanged.connect(lambda *_: self._update_buttons())
        self._populate()

    def _selected_cid(self):
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _populate(self):
        selected = self._selected_cid()
        self.list.clear()
        for c in self.categories:
            cid = c.get("category_id")
            name = self.overrides.display_name(
                self.mode, cid, c.get("category_name", "?"))
            flags = []
            if self.overrides.is_hidden(self.mode, cid):
                flags.append("hidden")
            if self.overrides.is_locked(self.mode, cid):
                flags.append("locked")
            label = name + (f"   [{', '.join(flags)}]" if flags else "")
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, cid)
            self.list.addItem(item)
            if cid == selected:
                self.list.setCurrentItem(item)
        self._update_buttons()

    def _update_buttons(self):
        cid = self._selected_cid()
        hidden = cid is not None and self.overrides.is_hidden(self.mode, cid)
        locked = cid is not None and self.overrides.is_locked(self.mode, cid)
        self.hide_btn.setText("Unhide" if hidden else "Hide")
        self.lock_btn.setText("Unlock" if locked else "Lock")

    def _rename(self):
        cid = self._selected_cid()
        if cid is None:
            return
        current = self.overrides.display_name(
            self.mode, cid,
            next((c.get("category_name", "") for c in self.categories
                  if c.get("category_id") == cid), ""))
        name, ok = QInputDialog.getText(self, "Rename category",
                                        "New name:", text=current)
        if ok:
            self.overrides.update(self.mode, cid, name=name.strip())
            self._populate()

    def _toggle_hidden(self):
        cid = self._selected_cid()
        if cid is None:
            return
        hidden = not self.overrides.is_hidden(self.mode, cid)
        self.overrides.update(self.mode, cid, hidden=hidden)
        self._populate()

    def _toggle_locked(self):
        cid = self._selected_cid()
        if cid is None:
            return
        locked = not self.overrides.is_locked(self.mode, cid)
        if locked and not self.window._ensure_pin_configured():
            return
        if locked:
            self.window.parental.lock_session()   # locked means locked *now*
        self.overrides.update(self.mode, cid, locked=locked)
        self._populate()

# ----------------------------------------------------------------------------
#  Main window
# ----------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, client: XtreamClient, settings: QSettings,
                 playlists: "PlaylistStore | None" = None):
        super().__init__()
        self.client = client
        self.settings = settings
        self.playlist_store = playlists
        active_pl = playlists.active() if playlists else None
        self.pool = QThreadPool.globalInstance()
        self.logos = LogoLoader(self.pool)
        self.xmltv = XmltvGuide(client, (active_pl or {}).get("epg_url") or None)
        pid = (active_pl or {}).get("id")
        self.favs = FavoriteStore(
            settings, f"favorites_{pid}" if pid else "favorites")
        self.history = HistoryStore(
            settings, f"history_{pid}" if pid else "history")
        self.overrides = CategoryOverrides(
            settings, f"category_overrides_{pid}" if pid else "category_overrides")
        self.parental = ParentalControl(settings)
        self.cast = ChromecastManager()
        self._raw_categories = []          # unfiltered, for the content manager
        self.mpv = MpvIpcPlayer()
        self.mpv_window = MpvWindowPlayer() if _libmpv is not None else None
        if self.mpv_window:
            self.mpv_window.zap_requested.connect(self._zap)
            self.mpv_window.playback_error.connect(self._playback_error)
        # One-time migration: older versions auto-persisted playback_mode
        # ("window") via the Settings dialog while embedding was unreliable
        # on Wayland compositors. The OpenGL render API made embedded the
        # right default everywhere, so clear that stale value once - users
        # who prefer another mode simply pick it again in Settings.
        if not settings.value("playback_mode_v2"):
            settings.remove("playback_mode")
            settings.setValue("playback_mode_v2", "1")
        self.mode = "live"                 # live | vod | series | fav | history
        self.all_items = []                # current (unfiltered) list
        self.series_ctx = None             # selected series when browsing episodes
        self._info_cache = {}               # (kind, id) -> info dict
        self._current_key = None            # identity of the selected row
        self._last_player = None
        self._last_playlist_refresh = time.time()

        self.setWindowTitle(
            f"{APP_NAME} - {active_pl['name']}" if active_pl else APP_NAME)
        self.resize(1240, 780)
        self._build_ui()
        self._load_categories()

        # Playlist auto-refresh: check every 5 minutes whether the active
        # playlist's cadence (2h ... weekly) has elapsed.
        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.timeout.connect(self._maybe_auto_refresh)
        self._auto_refresh_timer.start(5 * 60_000)

    # -- UI construction -------------------------------------------------------
    def _build_ui(self):
        # Menu bar (also gives GNOME's top bar a real app menu, not "python3")
        menubar = self.menuBar()
        app_menu = menubar.addMenu(APP_NAME)
        settings_action = app_menu.addAction("Settings...")
        settings_action.triggered.connect(self.open_settings)
        app_menu.addSeparator()
        about_action = app_menu.addAction("About dopeIPTV")
        about_action.triggered.connect(self.show_about)
        quit_action = app_menu.addAction("Quit")
        quit_action.triggered.connect(self.close)

        root = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(root)

        # ---------- Sidebar ----------
        side = QWidget(objectName="Sidebar")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 16, 12, 12)
        sl.setSpacing(4)

        title = QLabel("dopeIPTV", objectName="AppTitle")
        sub = QLabel("for Linux & macOS", objectName="AppSub")
        sl.addWidget(title)
        sl.addWidget(sub)
        sl.addSpacing(14)

        self.nav_btns = {}
        for key, text in (("live", "TV"), ("vod", "Movies"), ("series", "Series"),
                          ("fav", "Favorites"), ("history", "History")):
            b = QPushButton(text, objectName="NavBtn")
            b.setCheckable(True)
            b.setFlat(True)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda _, k=key: self.switch_mode(k))
            sl.addWidget(b)
            self.nav_btns[key] = b
        self.nav_btns["live"].setChecked(True)

        sl.addWidget(QLabel("CATEGORIES", objectName="SectionLabel"))
        self.cat_list = QListWidget()
        self.cat_list.currentItemChanged.connect(self._category_changed)
        self.cat_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.cat_list.customContextMenuRequested.connect(self._cat_menu)
        sl.addWidget(self.cat_list, 1)

        guide_btn = QPushButton("EPG Guide")
        guide_btn.clicked.connect(self._open_epg_guide)
        sl.addWidget(guide_btn)

        settings_btn = QPushButton("Settings")
        settings_btn.clicked.connect(self.open_settings)
        sl.addWidget(settings_btn)

        # ---------- Middle column ----------
        mid = QWidget(objectName="MiddlePane")
        ml = QVBoxLayout(mid)
        ml.setContentsMargins(14, 14, 14, 10)
        ml.setSpacing(10)

        self.loading_bar = QProgressBar(objectName="LoadBar")
        self.loading_bar.setRange(0, 0)
        self.loading_bar.setTextVisible(False)
        self.loading_bar.hide()
        ml.addWidget(self.loading_bar)

        self.search = QLineEdit(objectName="Search")
        self.search.setPlaceholderText("Search channels, movies or series...")
        # Debounce for the auto-playing preview: arrowing quickly through the
        # channel list shouldn't open a stream per row, only for the row the
        # user actually settles on.
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._play_preview)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_filter)
        self.search.textChanged.connect(lambda _t: self._search_timer.start(220))
        ml.addWidget(self.search)

        # Inline view controls: size, sort, and grid toggle - so these don't
        # require opening Settings. They read/write the same settings keys.
        ctl = QHBoxLayout()
        ctl.setSpacing(6)
        self.size_box = self._combo(
            [("compact", "Compact"), ("medium", "Medium"), ("large", "Large")],
            self.settings.value("view_density", "medium"))
        self.size_box.setObjectName("InlineCombo")
        self.size_box.currentIndexChanged.connect(self._inline_view_changed)
        self.sort_box = self._combo(
            [("default", "Default"), ("alpha_asc", "A→Z"), ("alpha_desc", "Z→A"),
             ("recent", "Recent")],
            self.settings.value("sort_order", "default"))
        self.sort_box.setObjectName("InlineCombo")
        self.sort_box.currentIndexChanged.connect(self._inline_view_changed)
        self.grid_btn = QPushButton("Grid", objectName="InlineToggle")
        self.grid_btn.setCheckable(True)
        self.grid_btn.setChecked(self.settings.value("view_grid", "false") == "true")
        self.grid_btn.toggled.connect(self._inline_view_changed)
        ctl.addWidget(QLabel("Size"))
        ctl.addWidget(self.size_box)
        ctl.addWidget(QLabel("Sort"))
        ctl.addWidget(self.sort_box)
        ctl.addStretch()
        ctl.addWidget(self.grid_btn)
        ml.addLayout(ctl)

        self.back_btn = QPushButton("<-  Back to series")
        self.back_btn.hide()
        self.back_btn.clicked.connect(self._leave_series)
        ml.addWidget(self.back_btn)

        self.clear_history_btn = QPushButton("Clear history")
        self.clear_history_btn.hide()
        self.clear_history_btn.clicked.connect(self._clear_history)
        ml.addWidget(self.clear_history_btn)

        self.listw = QListView(objectName="Channels")
        self.list_model = ChannelListModel()
        self.listw.setModel(self.list_model)
        self.delegate = ChannelDelegate(
            self, self.settings.value("view_density", "medium"))
        self.listw.setItemDelegate(self.delegate)
        self.listw.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.listw.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.listw.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.listw.setUniformItemSizes(True)
        self.listw.setMouseTracking(True)
        self.listw.selectionModel().currentChanged.connect(self._on_current_changed)
        self.listw.doubleClicked.connect(lambda _idx: self.play())
        self.listw.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.listw.customContextMenuRequested.connect(self._context_menu)
        ml.addWidget(self.listw, 1)

        self.count_lbl = QLabel("")
        self.count_lbl.setStyleSheet("color:#5A5A64; font-size:11px;")
        ml.addWidget(self.count_lbl)

        # ---------- Detail panel ----------
        det = QWidget(objectName="DetailPane")
        dl = QVBoxLayout(det)
        dl.setContentsMargins(20, 22, 20, 18)
        dl.setSpacing(12)

        self.player = None
        if embedded_playback_supported():
            self.player = EmbeddedPlayer()
            self.player.hide()
            self.player.fs_btn.clicked.connect(self._toggle_player_fullscreen)
            self.player.double_clicked.connect(self._toggle_player_fullscreen)
            self.player.playback_error.connect(self._playback_error)
            self.player.stop_btn.clicked.connect(self.player.hide)
            dl.addWidget(self.player, 2)

        self.stream_error = QLabel("")
        self.stream_error.setStyleSheet("color:#FF6B6B; font-size:12px;")
        self.stream_error.setWordWrap(True)
        self.stream_error.hide()
        dl.addWidget(self.stream_error)

        self.d_logo = QLabel()
        self.d_logo.setFixedSize(84, 84)
        self.d_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.d_logo.setStyleSheet(
            "background:#26262E; border-radius:18px; font-size:30px; font-weight:700;")
        dl.addWidget(self.d_logo)

        self.d_title = QLabel("Select something from the list", objectName="DetailTitle")
        self.d_title.setWordWrap(True)
        dl.addWidget(self.d_title)

        self.d_meta = QLabel("", objectName="DetailMeta")
        self.d_meta.setWordWrap(True)
        dl.addWidget(self.d_meta)

        self.now_card = QFrame(objectName="Card")
        nc = QVBoxLayout(self.now_card)
        nc.setContentsMargins(14, 12, 14, 12)
        nc.setSpacing(6)
        self.now_time = QLabel("", objectName="NowTime")
        self.now_title = QLabel("", objectName="NowTitle")
        self.now_title.setWordWrap(True)
        self.now_bar = QProgressBar(objectName="EpgBar")
        self.now_bar.setTextVisible(False)
        self.now_bar.setRange(0, 100)
        self.now_desc = QLabel("", objectName="NowDesc")
        self.now_desc.setWordWrap(True)
        for w in (self.now_time, self.now_title, self.now_bar, self.now_desc):
            nc.addWidget(w)
        self.now_card.hide()
        dl.addWidget(self.now_card)

        self.epg_refresh = QPushButton("Refresh EPG")
        self.epg_refresh.clicked.connect(self._request_epg)
        self.epg_refresh.hide()
        dl.addWidget(self.epg_refresh)

        self.epg_scroll = QScrollArea()
        self.epg_scroll.setWidgetResizable(True)
        self.epg_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.epg_holder = QWidget()
        self.epg_lay = QVBoxLayout(self.epg_holder)
        self.epg_lay.setContentsMargins(0, 0, 0, 0)
        self.epg_lay.setSpacing(8)
        self.epg_lay.addStretch()
        self.epg_scroll.setWidget(self.epg_holder)
        dl.addWidget(self.epg_scroll, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.play_mpv = QPushButton("Play in mpv", objectName="Primary")
        self.play_mpv.clicked.connect(lambda: self.play("mpv"))
        self.play_vlc = QPushButton("Play in VLC")
        self.play_vlc.clicked.connect(lambda: self.play("vlc"))
        row.addWidget(self.play_mpv)
        row.addWidget(self.play_vlc)
        dl.addLayout(row)

        root.addWidget(side)
        root.addWidget(mid)
        root.addWidget(det)
        root.setSizes([220, 560, 380])
        root.setCollapsible(0, False)
        root.setCollapsible(2, False)
        self._side, self._mid, self._det = side, mid, det

        self.tick = QTimer(self)
        self.tick.timeout.connect(self._refresh_progress)
        self.tick.start(60_000)
        self._current_epg = None
        self._player_fs = False

        QShortcut(QKeySequence("Ctrl+Right"), self, activated=lambda: self._zap(1))
        QShortcut(QKeySequence("Ctrl+Left"), self, activated=lambda: self._zap(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self,
                  activated=self._exit_player_fullscreen)
        QShortcut(QKeySequence(Qt.Key.Key_F), self,
                  activated=self._toggle_fullscreen_shortcut)

        # Apply the saved list/grid view mode before any content loads.
        self._apply_view_settings()

    # -- fullscreen (F key covers both the embedded pane and, as a fallback,
    #    the reused external mpv window - no extra buttons for the latter) --
    def _toggle_fullscreen_shortcut(self):
        if self.player and self.player.isVisible():
            self._toggle_player_fullscreen()
        elif self.mpv_window and self.mpv_window.is_active():
            self.mpv_window.toggle_fullscreen()

    # -- embedded-player fullscreen ---------------------------------------------
    def _toggle_player_fullscreen(self):
        if not self.player or not self.player.isVisible():
            return
        if self._player_fs:
            self._exit_player_fullscreen()
            return
        self._player_fs = True
        # No reparenting (that would tear down the GL context mpv renders
        # into) - instead hide everything around the video and fullscreen
        # the main window so the player pane stretches to fill it.
        # Iterating children() catches every direct child widget, including
        # ones living inside nested layouts (e.g. the Play in mpv/VLC button
        # row) - iterating the layout's own items misses those, because
        # layout items whose content is a sub-layout return None from
        # .widget().
        self._side.hide()
        self._mid.hide()
        self._det_hidden = []
        for w in self._det.children():
            if (isinstance(w, QWidget) and w is not self.player
                    and w.isVisible()):
                self._det_hidden.append(w)
                w.hide()
        # The detail pane's layout keeps its 20px margins otherwise, leaving a
        # black frame around the video - drop them (and the pane's border)
        # while fullscreen so the video truly fills the screen.
        det_lay = self._det.layout()
        self._det_margins = det_lay.contentsMargins()
        det_lay.setContentsMargins(0, 0, 0, 0)
        self._det.setStyleSheet("#DetailPane { background:#000000; border:none; }")
        self.menuBar().hide()          # no grey menu bar over the video
        self.player.set_fullscreen_ui(True)
        self._was_fullscreen = self.isFullScreen()
        self.showFullScreen()

    def _exit_player_fullscreen(self):
        if not self._player_fs:
            return
        self._player_fs = False
        self._side.show()
        self._mid.show()
        for w in getattr(self, "_det_hidden", []):
            w.show()
        self._det_hidden = []
        m = getattr(self, "_det_margins", None)
        if m is not None:
            self._det.layout().setContentsMargins(m.left(), m.top(),
                                                  m.right(), m.bottom())
        self._det.setStyleSheet("")
        self.menuBar().show()
        self.player.set_fullscreen_ui(False)
        if not getattr(self, "_was_fullscreen", False):
            self.showNormal()

    # -- playlists ---------------------------------------------------------------
    REFRESH_SECONDS = {"2h": 2 * 3600, "6h": 6 * 3600, "12h": 12 * 3600,
                       "24h": 24 * 3600, "1w": 7 * 24 * 3600}

    def _maybe_auto_refresh(self):
        pl = self.playlist_store.active() if self.playlist_store else None
        secs = self.REFRESH_SECONDS.get((pl or {}).get("refresh", ""))
        if secs and time.time() - self._last_playlist_refresh >= secs:
            self.refresh_playlist()

    def refresh_playlist(self):
        """Re-fetches categories/content and resets the XMLTV guide so it is
        downloaded fresh the next time EPG data is needed."""
        self._last_playlist_refresh = time.time()
        pl = self.playlist_store.active() if self.playlist_store else None
        self.xmltv = XmltvGuide(self.client, (pl or {}).get("epg_url") or None)
        self._info_cache.clear()
        self._load_categories()

    def switch_playlist(self, pid):
        """Connects to another saved playlist and reloads everything."""
        pl = self.playlist_store.get(pid) if self.playlist_store else None
        if not pl:
            return
        self.loading_bar.show()
        self.count_lbl.setText(f"Connecting to {pl['name']}...")
        candidate = XtreamClient(pl["server"], pl["username"], pl["password"])

        def done(_auth):
            self.loading_bar.hide()
            self.playlist_store.set_active(pid)
            self.client = candidate
            self.favs = FavoriteStore(self.settings, f"favorites_{pid}")
            self.history = HistoryStore(self.settings, f"history_{pid}")
            self.setWindowTitle(f"{APP_NAME} - {pl['name']}")
            self.refresh_playlist()

        def fail(msg):
            self.loading_bar.hide()
            self.count_lbl.setText("")
            QMessageBox.warning(self, "Playlist",
                                f"Could not connect to {pl['name']}: {msg}")

        run_async(self.pool, candidate.authenticate, done, fail)

    # -- modes and categories --------------------------------------------------
    def switch_mode(self, mode):
        for k, b in self.nav_btns.items():
            b.setChecked(k == mode)
        self.mode = mode
        self.series_ctx = None
        self.back_btn.hide()
        self.clear_history_btn.setVisible(mode == "history")
        self.search.clear()
        self._load_categories()

    def _load_categories(self):
        self.cat_list.clear()
        self.list_model.set_items([], self.mode)
        if self.mode in ("fav", "history"):
            self.cat_list.blockSignals(True)
            all_item = QListWidgetItem("All")
            all_item.setData(Qt.ItemDataRole.UserRole, None)
            self.cat_list.addItem(all_item)
            if self.mode == "fav":
                for g in self.favs.group_names():
                    locked = (self.favs.is_locked(g)
                              and not self.parental.session_unlocked)
                    it = QListWidgetItem(f"{g}  [locked]" if locked else g)
                    it.setData(Qt.ItemDataRole.UserRole, g)
                    self.cat_list.addItem(it)
            self.cat_list.blockSignals(False)
            self.cat_list.setCurrentRow(0)
            return
        self.loading_bar.show()
        self.count_lbl.setText("Loading categories...")
        fn = {"live": self.client.live_categories,
              "vod": self.client.vod_categories,
              "series": self.client.series_categories}[self.mode]
        request_mode = self.mode

        def done(cats):
            if self.mode != request_mode:
                return       # stale response - the user already switched mode
            self.loading_bar.hide()
            self._raw_categories = cats or []
            self.cat_list.blockSignals(True)
            all_item = QListWidgetItem("All")
            all_item.setData(Qt.ItemDataRole.UserRole, None)
            self.cat_list.addItem(all_item)
            for c in cats:
                cid = c.get("category_id")
                if self.overrides.is_hidden(self.mode, cid):
                    continue
                name = self.overrides.display_name(
                    self.mode, cid, c.get("category_name", "?"))
                if (self.overrides.is_locked(self.mode, cid)
                        and not self.parental.session_unlocked):
                    name += "  [locked]"
                it = QListWidgetItem(name)
                it.setData(Qt.ItemDataRole.UserRole, cid)
                self.cat_list.addItem(it)
            self.cat_list.blockSignals(False)
            self.cat_list.setCurrentRow(0)

        run_async(self.pool, fn, done, self._error)

    def _category_changed(self, cur, _prev=None):
        if not cur:
            return
        cat = cur.data(Qt.ItemDataRole.UserRole)
        # Parental gate: locked categories / locked favorite groups need the
        # PIN before their contents load.
        locked = False
        if cat is not None:
            if self.mode == "fav":
                locked = self.favs.is_locked(cat)
            elif self.mode in ("live", "vod", "series"):
                locked = self.overrides.is_locked(self.mode, cat)
        if locked and not self.parental.session_unlocked:
            if not self._request_unlock():
                self.cat_list.blockSignals(True)
                self.cat_list.setCurrentRow(0)
                self.cat_list.blockSignals(False)
                self._load_items(None)
                return
            self._load_categories()   # redraw without [locked] suffixes
            return
        self.series_ctx = None
        self.back_btn.hide()
        self._load_items(cat)

    def _load_items(self, category_id):
        if self.mode == "fav":
            exclude = (() if self.parental.session_unlocked
                       else self.favs.locked_groups())
            self.all_items = self.favs.items(category_id, exclude_groups=exclude)
            self._apply_filter()
            return
        if self.mode == "history":
            self.all_items = self.history.items()
            self._apply_filter()
            return
        self.loading_bar.show()
        self.count_lbl.setText("Loading content...")
        fn = {"live": self.client.live_streams,
              "vod": self.client.vod_streams,
              "series": self.client.series_list}[self.mode]
        mode = self.mode

        def done(items):
            if self.mode != mode:
                return       # stale response - the user already switched mode
            self.loading_bar.hide()
            items = items or []
            if category_id is None:
                # 'All': leave out contents of hidden categories, and of
                # locked ones while the session is still locked.
                excluded = self.overrides.excluded_ids(
                    mode, include_locked=not self.parental.session_unlocked)
                if excluded:
                    items = [it for it in items
                             if str(it.get("category_id")) not in excluded]
            self.all_items = items
            self._apply_filter()
            if self.mode == "live":
                self._ensure_xmltv_loaded()

        run_async(self.pool, lambda: fn(category_id), done, self._error)

    def _ensure_xmltv_loaded(self):
        if self.xmltv._loaded or self.xmltv._failed:
            return
        run_async(self.pool, self.xmltv.ensure_loaded,
                  lambda ok: self.list_model.refresh_all() if ok else None)

    # -- list and filtering ------------------------------------------------------
    LABELS = {"live": "channels", "vod": "movies", "series": "series",
              "episode": "episodes", "fav": "favorites", "history": "history items"}

    @staticmethod
    def _sort_key_name(it):
        return (it.get("name") or it.get("title") or "").lower()

    def _sorted(self, items):
        """Applies the current sort order. 'default' preserves provider order
        (and for History/Favorites their own natural order); 'recent' uses the
        provider's added timestamp when present, newest first."""
        order = self.settings.value("sort_order", "default")
        if order == "alpha_asc":
            return sorted(items, key=MainWindow._sort_key_name)
        if order == "alpha_desc":
            return sorted(items, key=MainWindow._sort_key_name, reverse=True)
        if order == "recent":
            def added(it):
                try:
                    return int(it.get("added") or 0)
                except (TypeError, ValueError):
                    return 0
            # Only meaningfully reorders when the provider supplies 'added';
            # otherwise it's a stable no-op that keeps the original order.
            return sorted(items, key=added, reverse=True)
        return items

    def _apply_filter(self):
        text = self.search.text().lower().strip()
        kind = "episode" if self.series_ctx else self.mode
        if text:
            filtered = [it for it in self.all_items
                       if text in (it.get("name") or it.get("title") or "").lower()]
        else:
            filtered = list(self.all_items)
        filtered = self._sorted(filtered)
        self.list_model.set_items(filtered, kind)
        self.count_lbl.setText(f"{len(filtered)} {self.LABELS[kind]}")
        if kind == "fav" and not self.all_items:
            self.count_lbl.setText("No favorites yet - right-click a channel in TV to add one.")
        elif kind == "history" and not self.all_items:
            self.count_lbl.setText("No watch history yet.")

    # -- item identity -----------------------------------------------------------
    @staticmethod
    def _item_key(it):
        if not it:
            return None
        return it.get("stream_id") or it.get("series_id") or it.get("id") or it.get("_key")

    def _history_kind(self):
        if self.series_ctx:
            return "episode"
        return {"live": "live", "fav": "live", "vod": "movie"}.get(self.mode, "other")

    # -- selection, EPG and detail panel -------------------------------------------
    def _on_current_changed(self, current, _previous=None):
        it = self.list_model.item_at(current.row()) if current.isValid() else None
        self._current_key = self._item_key(it)
        self._show_detail(it)

    def _show_detail(self, it):
        self.now_card.hide()
        self._clear_epg_rows()
        self._current_epg = None
        self.epg_refresh.hide()
        if not it:
            self.d_title.setText("Select something from the list")
            self.d_meta.setText("")
            self.d_logo.setPixmap(QPixmap())
            self.d_logo.setText("")
            return
        name = it.get("name") or it.get("title") or "?"
        self.d_title.setText(name)
        self.d_logo.setPixmap(QPixmap())
        self.d_logo.setText(name.strip()[:1].upper())
        url = it.get("stream_icon") or it.get("cover")
        if url:
            self.logos.get(url, self._set_detail_logo)

        if self.mode == "history":
            self.d_meta.setText({"live": "Live channel", "movie": "Movie",
                                 "episode": "Episode"}.get(it.get("_kind"), ""))
            return

        if self.series_ctx:
            info = it.get("info") if isinstance(it.get("info"), dict) else {}
            meta = " * ".join(x for x in (
                f"Season {it.get('season')}" if it.get("season") else "",
                info.get("duration", ""),) if x)
            self.d_meta.setText(meta)
            self._show_media_info(info, self._current_key)
        elif self.mode in ("live", "fav"):
            self.d_meta.setText("Live channel")
            # "is not None": a perfectly valid stream_id of 0 is falsy
            if it.get("stream_id") is not None:
                if not self._player_fs:
                    self.epg_refresh.show()
                self._request_epg()
                if (self.player and self._autoplay_preview()
                        and self.playback_mode() == "embedded"
                        and self.settings.value("player", "mpv") == "mpv"):
                    self._preview_timer.start(350)
        elif self.mode == "vod":
            meta = " * ".join(x for x in (
                str(it.get("year") or ""),
                f"* {it['rating']}" if it.get("rating") else "",) if x)
            self.d_meta.setText(meta or "Movie")
            if it.get("stream_id") is not None:
                self._request_media_info("vod", it["stream_id"], self._current_key)
        else:
            self.d_meta.setText("Series - double-click for episodes")
            if it.get("series_id") is not None:
                self._request_media_info("series", it["series_id"], self._current_key)

    def _set_detail_logo(self, pm):
        rounded = QPixmap(84, 84)
        rounded.fill(Qt.GlobalColor.transparent)
        p = QPainter(rounded)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, 84, 84, 18, 18)
        p.setClipPath(path)
        s = pm.scaled(84, 84, Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap((84 - s.width()) // 2, (84 - s.height()) // 2, s)
        p.end()
        self.d_logo.setText("")
        self.d_logo.setPixmap(rounded)

    def _clear_epg_rows(self):
        while self.epg_lay.count() > 1:
            w = self.epg_lay.takeAt(0).widget()
            if w:
                w.deleteLater()

    def _epg_note(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#6E6E79; font-size:12px;")
        lbl.setWordWrap(True)
        self.epg_lay.insertWidget(self.epg_lay.count() - 1, lbl)

    def _request_epg(self):
        it = self.list_model.item_at(self.listw.currentIndex().row())
        if not it or self.mode not in ("live", "fav") or self.series_ctx:
            return
        sid = it.get("stream_id")
        if sid is None:
            return
        key = self._item_key(it)
        self._clear_epg_rows()
        self._epg_note("Loading programme guide...")

        def fetch():
            listings = self.client.short_epg(sid, 8)
            if not listings:
                listings = self.client.epg_table(sid)
            if not listings:
                listings = self.xmltv.listings_for(it)
            return listings

        run_async(self.pool, fetch,
                  lambda e: self._show_epg(e, key),
                  lambda _: self._epg_error(key))

    def _epg_error(self, key):
        if key != self._current_key:
            return
        self.now_card.hide()
        self._clear_epg_rows()
        self._epg_note("Could not load the programme guide.")

    # -- movie/series info -------------------------------------------------------
    def _request_media_info(self, kind, mid, key):
        cached = self._info_cache.get((kind, mid))
        if cached is not None:
            self._show_media_info(cached, key)
            return
        self._epg_note("Loading information...")
        if kind == "vod":
            fetch = lambda: (self.client.vod_info(mid) or {}).get("info") or {}
        else:
            fetch = lambda: (self.client.series_info(mid) or {}).get("info") or {}

        def done(info):
            if not isinstance(info, dict):
                info = {}
            self._info_cache[(kind, mid)] = info
            self._show_media_info(info, key)

        run_async(self.pool, fetch, done, lambda _: None)

    def _show_media_info(self, info, key):
        if key != self._current_key:
            return
        self._clear_epg_rows()
        plot = str(info.get("plot") or info.get("description") or "").strip()
        if plot:
            card = QFrame(objectName="Card")
            kl = QVBoxLayout(card)
            kl.setContentsMargins(14, 12, 14, 12)
            desc = QLabel(plot, objectName="NowDesc")
            desc.setWordWrap(True)
            kl.addWidget(desc)
            self.epg_lay.insertWidget(self.epg_lay.count() - 1, card)
        rows = (("Genre", info.get("genre")),
               ("Cast", info.get("cast") or info.get("actors")),
               ("Director", info.get("director")),
               ("Released", info.get("releasedate") or info.get("releaseDate")),
               ("Duration", info.get("duration")),
               ("Rating", info.get("rating")))
        has_content = bool(plot)
        for label, value in rows:
            value = str(value or "").strip()
            if value:
                self._epg_note(f"{label}: {value}")
                has_content = True
        if not has_content:
            self._epg_note("No further information available.")

    def _show_epg(self, listings, key):
        if key != self._current_key:
            return                      # the user already switched channels
        self.now_card.hide()
        self._clear_epg_rows()
        self._current_epg = None
        now = datetime.now().astimezone()
        all_posts, upcoming = [], []
        current = None
        seen = set()
        for e in listings or []:
            start, stop = epg_times(e)
            if e.get("_plain"):     # XMLTV entries are already decoded
                title, desc = e.get("title") or "", e.get("description") or ""
            else:
                title, desc = b64(e.get("title")), b64(e.get("description"))
            dedup_key = (int(start.timestamp()) if start else None, title.strip().lower())
            if dedup_key in seen:
                continue               # some providers list the same slot twice
            seen.add(dedup_key)
            post = {"title": title, "desc": desc, "start": start, "stop": stop}
            all_posts.append(post)
            if start and stop and start <= now < stop and not current:
                current = post
            elif start and start > now:
                upcoming.append(post)
        upcoming.sort(key=lambda p: p["start"])

        if current:
            self._current_epg = current
            self.now_time.setText(f"NOW * {current['start']:%H:%M}-{current['stop']:%H:%M}")
            self.now_title.setText(current["title"] or "Unknown programme")
            self.now_desc.setText(current["desc"][:400])
            self._refresh_progress()
            if not self._player_fs:      # in fullscreen the overlay shows it
                self.now_card.show()
            if self.player:
                info = (f"{self.d_title.text()}\n"
                        f"{current['title'] or 'Unknown programme'}   "
                        f"{current['start']:%H:%M}-{current['stop']:%H:%M}")
                nxt = upcoming[0] if upcoming else None
                if nxt:
                    info += f"\nNext: {nxt['title'] or '?'} at {nxt['start']:%H:%M}"
                self.player.set_overlay_info(info)

        for post in upcoming[:6]:
            self._epg_card(post)

        if not current and not upcoming:
            dated = sorted((p for p in all_posts if p["start"]), key=lambda p: p["start"])
            if dated:
                self._epg_note("The server's schedule times look wrong - "
                               "showing the most recent entries anyway.")
                for post in dated[-6:]:
                    self._epg_card(post, with_date=True)
            else:
                self._epg_note("No programme guide available for this channel.")

    def _epg_card(self, post, with_date=False):
        card = QFrame(objectName="Card")
        kl = QVBoxLayout(card)
        kl.setContentsMargins(12, 9, 12, 9)
        kl.setSpacing(2)
        fmt = "%-d/%-m %H:%M" if with_date else "%H:%M"
        t = QLabel(post["start"].strftime(fmt), objectName="EpgRowTime")
        ti = QLabel(post["title"] or "Unknown", objectName="EpgRowTitle")
        ti.setWordWrap(True)
        kl.addWidget(t)
        kl.addWidget(ti)
        self.epg_lay.insertWidget(self.epg_lay.count() - 1, card)

    def _refresh_progress(self):
        e = self._current_epg
        if not e:
            return
        now = datetime.now().astimezone()
        if now >= e["stop"]:
            self._current_epg = None
            self._request_epg()
            return
        total = (e["stop"] - e["start"]).total_seconds()
        if total > 0:
            pct = (now - e["start"]).total_seconds() / total * 100
            self.now_bar.setValue(max(0, min(100, int(pct))))

    # -- series -> episodes ---------------------------------------------------------
    def _enter_series(self, series):
        sid = series.get("series_id")
        if sid is None:
            return
        self.loading_bar.show()
        self.count_lbl.setText("Loading episodes...")

        def done(info):
            self.loading_bar.hide()
            episodes = []
            for season, eps in (info.get("episodes") or {}).items():
                for ep in eps:
                    ep["season"] = season
                    ep["name"] = f"S{season} * E{ep.get('episode_num', '?')} - " \
                                 f"{ep.get('title') or 'Episode'}"
                    episodes.append(ep)
            self.series_ctx = series
            self.all_items = episodes
            self.back_btn.show()
            self.search.clear()
            self._apply_filter()

        run_async(self.pool, lambda: self.client.series_info(sid), done, self._error)

    def _leave_series(self):
        self.series_ctx = None
        self.back_btn.hide()
        cur = self.cat_list.currentItem()
        self._load_items(cur.data(Qt.ItemDataRole.UserRole) if cur else None)

    # -- playback ----------------------------------------------------------------
    def _stream_for(self, it):
        title = it.get("name") or it.get("title") or "dopeIPTV"
        if self.series_ctx:
            return self.client.episode_url(
                it.get("id"), it.get("container_extension")), title
        if self.mode in ("live", "fav"):
            fmt = self.settings.value("stream_format", "ts")
            return self.client.live_url(it.get("stream_id"), fmt), title
        if self.mode == "vod":
            return self.client.vod_url(
                it.get("stream_id"), it.get("container_extension")), title
        return None, title

    def play_live_channel(self, it):
        """Plays a channel directly (used by the EPG Guide dialog)."""
        fmt = self.settings.value("stream_format", "ts")
        url = self.client.live_url(it.get("stream_id"), fmt)
        title = it.get("name") or "dopeIPTV"
        self._start_playback(url, title, it.get("stream_icon"),
                             self._item_key(it), "live")

    def play(self, player=None, external=False):
        it = self.list_model.item_at(self.listw.currentIndex().row())
        if not it:
            return
        if self.mode == "series" and not self.series_ctx:
            self._enter_series(it)
            return
        if self.mode == "history":
            url, title = it.get("_url"), it.get("name") or "dopeIPTV"
            icon, key, kind = it.get("stream_icon"), it.get("_key"), it.get("_kind")
        else:
            url, title = self._stream_for(it)
            icon = it.get("stream_icon") or it.get("cover")
            key, kind = self._item_key(it), self._history_kind()
        if not url:
            return

        if player:
            self.settings.setValue("player", player)

        if external:
            chosen = player or self.settings.value("player", "mpv")
            launch_player(chosen, url, title, self)
            if self.mode != "history":
                self.history.add(url, title, icon, key, kind)
            return

        self._start_playback(url, title, icon, key, kind if self.mode != "history" else None)

    def _open_cast_dialog(self, it):
        if not ChromecastManager.available():
            QMessageBox.information(
                self, "Chromecast",
                "Casting needs the pychromecast package:\n\n"
                "  pip install pychromecast")
            return
        if self.mode == "history":
            url, title = it.get("_url"), it.get("name") or "dopeIPTV"
        else:
            url, title = self._stream_for(it)
            if (self.mode in ("live", "fav")
                    and it.get("stream_id") is not None):
                # Chromecast receivers can't demux raw MPEG-TS; use HLS
                url = self.client.live_url(it["stream_id"], "m3u8")
        if not url:
            return
        CastDialog(self, url, title).exec()

    def _open_external_both(self, it):
        """Launches the same stream in both external mpv and VLC at once."""
        if self.mode == "history":
            url, title = it.get("_url"), it.get("name") or "dopeIPTV"
            icon, key, kind = it.get("stream_icon"), it.get("_key"), it.get("_kind")
        else:
            url, title = self._stream_for(it)
            icon = it.get("stream_icon") or it.get("cover")
            key, kind = self._item_key(it), self._history_kind()
        if not url:
            return
        launch_player("mpv", url, title, self)
        launch_player("vlc", url, title, self)
        if self.mode != "history":
            self.history.add(url, title, icon, key, kind)

    def _autoplay_preview(self):
        return self.settings.value("autoplay_preview", "true") == "true"

    def _play_preview(self):
        """Auto-starts the selected live channel in the embedded preview pane.
        Intentionally does NOT record history - only explicit plays do."""
        it = self.list_model.item_at(self.listw.currentIndex().row())
        if (not it or self.mode not in ("live", "fav") or self.series_ctx
                or not self.player or self.playback_mode() != "embedded"):
            return
        url, title = self._stream_for(it)
        if not url:
            return
        self.stream_error.hide()
        self.player.show()
        self.player.set_overlay_info(title)
        self.player.play(url, title)

    def playback_mode(self):
        default = "embedded" if self.player else "window"
        mode = self.settings.value("playback_mode", default)
        if mode == "embedded" and not self.player:
            mode = "window"
        return mode

    def _start_playback(self, url, title, icon_url, key, history_kind):
        if history_kind:
            self.history.add(url, title, icon_url, key, history_kind)
        self.stream_error.hide()
        chosen = self.settings.value("player", "mpv")
        self._last_player = chosen
        mode = self.playback_mode()
        print(f"[dopeIPTV] Playing via player={chosen} mode={mode} "
              f"(embedded pane: {'yes' if self.player else 'no'})",
              file=sys.stderr)
        if chosen == "mpv" and mode == "embedded" and self.player:
            self.player.show()
            self.player.set_overlay_info(title)
            if not self.player.play(url, title):
                self.player.hide()
                launch_player(chosen, url, title, self)
        elif chosen == "mpv" and mode == "window":
            # A single reused mpv window (zap-able). python-mpv drives it
            # in-process; without python-mpv, fall back to controlling a
            # separate mpv process over its IPC socket - still reused, not a
            # fresh window each time.
            if self.mpv_window:
                if not self.mpv_window.play(url, title):
                    launch_player("mpv", url, title, self)
            else:
                run_async(self.pool, lambda: self.mpv.load(url, title),
                         lambda ok: None if ok else self._player_missing("mpv"))
        else:
            launch_player(chosen, url, title, self)

    def _player_missing(self, name):
        QMessageBox.warning(self, "Player not found",
                           f"{name} was not found. Install it and try again.")

    def _playback_error(self, msg):
        """A stream failed to play (dead/unreachable channel etc.)."""
        self.count_lbl.setText(f"Stream error: {msg}")
        if self._player_fs and self.player:
            self.player.set_overlay_info(f"Stream error: {msg}")
        else:
            self.stream_error.setText(f"Stream error: {msg}")
            self.stream_error.show()
        if self.player:
            self.player.title_lbl.setText("")

    def _zap(self, direction):
        if self.mode not in ("live", "fav"):
            return
        count = self.list_model.rowCount()
        if count == 0:
            return
        row = self.listw.currentIndex().row()
        new_row = (row + direction) % count if row >= 0 else 0
        idx = self.list_model.index(new_row)
        self.listw.setCurrentIndex(idx)
        self.listw.scrollTo(idx)
        self.play()

    def _context_menu(self, pos):
        idx = self.listw.indexAt(pos)
        if not idx.isValid():
            return
        self.listw.setCurrentIndex(idx)
        it = self.list_model.item_at(idx.row())
        if not it:
            return
        m = QMenu(self)
        m.addAction("Play in mpv", lambda: self.play("mpv"))
        m.addAction("Play in VLC", lambda: self.play("vlc"))
        ext = m.addMenu("Open externally")
        ext.addAction("mpv", lambda: self.play("mpv", external=True))
        ext.addAction("VLC", lambda: self.play("vlc", external=True))
        ext.addAction("mpv + VLC (both)", lambda: self._open_external_both(it))
        if not (self.mode == "series" and not self.series_ctx):
            m.addAction("Cast to Chromecast...",
                       lambda: self._open_cast_dialog(it))
        if self.mode in ("live", "fav") and it.get("stream_id"):
            m.addSeparator()
            fav_menu = m.addMenu("Add to favorites group")
            for g in self.favs.group_names():
                fav_menu.addAction(g, lambda g=g: self._add_fav(g, it))
            if self.favs.group_names():
                fav_menu.addSeparator()
            fav_menu.addAction("New group...", lambda: self._add_fav(None, it))
            if self.mode == "fav":
                m.addAction("Remove from favorites", lambda: self._remove_fav(it))
        if self.mode == "history":
            m.addSeparator()
            m.addAction("Remove from history",
                       lambda: self._remove_history(it))
        if not (self.mode == "series" and not self.series_ctx) and self.mode != "history":
            url, _ = self._stream_for(it)
            if url:
                m.addSeparator()
                m.addAction("Copy stream URL",
                           lambda: QApplication.clipboard().setText(url))
        m.exec(self.listw.viewport().mapToGlobal(pos))

    # -- favorites -------------------------------------------------------------
    def _add_fav(self, group, item):
        if group is None:
            group, ok = QInputDialog.getText(
                self, "New favorites group", "Group name:")
            group = (group or "").strip()
            if not ok or not group:
                return
        self.favs.add(group, item)
        if self.mode == "fav":
            self._load_categories()

    def _remove_fav(self, item):
        cur = self.cat_list.currentItem()
        group = cur.data(Qt.ItemDataRole.UserRole) if cur else None
        self.favs.remove(item.get("stream_id"), group)
        self._load_categories()

    # -- parental control ---------------------------------------------------------
    def _request_unlock(self):
        """Prompts for the parental PIN. Returns True when unlocked."""
        if self.parental.session_unlocked:
            return True
        if not self.parental.has_pin():
            return True                     # nothing configured -> not locked
        pin, ok = QInputDialog.getText(
            self, "Parental control", "Enter PIN:",
            QLineEdit.EchoMode.Password)
        if not ok:
            return False
        if self.parental.verify(pin.strip()):
            self.parental.session_unlocked = True
            return True
        QMessageBox.warning(self, "Parental control", "Wrong PIN.")
        return False

    def _ensure_pin_configured(self):
        """Makes sure a PIN exists before something can be locked."""
        if self.parental.has_pin():
            return True
        pin, ok = QInputDialog.getText(
            self, "Parental control",
            "No PIN is set yet. Choose a PIN to protect locked content:",
            QLineEdit.EchoMode.Password)
        pin = (pin or "").strip()
        if ok and pin:
            self.parental.set_pin(pin)
            return True
        return False

    # -- category context menu (content manager + favorites groups) ---------------
    def _cat_menu(self, pos):
        it = self.cat_list.itemAt(pos)
        data = it.data(Qt.ItemDataRole.UserRole) if it else None
        m = QMenu(self)

        if self.mode == "fav":
            if not data:
                return
            group = data
            m.addAction(f'Remove group "{group}"',
                       lambda: (self.favs.remove_group(group),
                                self._load_categories()))
            if self.favs.is_locked(group):
                m.addAction("Unlock group (remove protection)",
                           lambda: self._set_fav_lock(group, False))
            else:
                m.addAction("Lock group (parental control)",
                           lambda: self._set_fav_lock(group, True))
            m.exec(self.cat_list.mapToGlobal(pos))
            return

        if self.mode not in ("live", "vod", "series"):
            return
        if data is not None:
            cid = data
            m.addAction("Rename category...",
                       lambda: self._rename_category(cid))
            m.addAction("Hide category",
                       lambda: self._set_category_flag(cid, hidden=True))
            if self.overrides.is_locked(self.mode, cid):
                m.addAction("Unlock category (remove protection)",
                           lambda: self._set_category_flag(cid, locked=False))
            else:
                m.addAction("Lock category (parental control)",
                           lambda: self._lock_category(cid))
            m.addSeparator()
        m.addAction("Manage categories...", self._open_content_manager)
        m.exec(self.cat_list.mapToGlobal(pos))

    def _set_fav_lock(self, group, locked):
        if locked and not self._ensure_pin_configured():
            return
        self.favs.set_group_locked(group, locked)
        if locked:
            # locking means locked *now*, not after the next restart
            self.parental.lock_session()
        self._load_categories()

    def _rename_category(self, cid):
        current = self.overrides.display_name(
            self.mode, cid,
            next((c.get("category_name", "") for c in self._raw_categories
                  if c.get("category_id") == cid), ""))
        name, ok = QInputDialog.getText(self, "Rename category",
                                        "New name:", text=current)
        if ok:
            self.overrides.update(self.mode, cid, name=name.strip())
            self._load_categories()

    def _set_category_flag(self, cid, **fields):
        self.overrides.update(self.mode, cid, **fields)
        self._load_categories()

    def _lock_category(self, cid):
        if not self._ensure_pin_configured():
            return
        self.parental.lock_session()   # locked means locked *now*
        self._set_category_flag(cid, locked=True)

    def _open_content_manager(self):
        if self.mode not in ("live", "vod", "series"):
            return
        ContentManagerDialog(self, self.mode, self._raw_categories,
                             self.overrides).exec()
        self._load_categories()

    # -- history -----------------------------------------------------------------
    def _remove_history(self, item):
        self.history.remove(item.get("_key"), item.get("_kind"))
        self._load_items(None)

    def _clear_history(self):
        if QMessageBox.question(self, "Clear history",
                               "Remove all watch history?") == QMessageBox.StandardButton.Yes:
            self.history.clear()
            self._load_items(None)

    # -- EPG guide ---------------------------------------------------------------
    def _open_epg_guide(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("EPG Guide")
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("Loading channels..."))
        dlg.resize(300, 100)
        dlg.show()

        def done(channels):
            dlg.close()
            self._ensure_xmltv_loaded()
            EpgGuideDialog(self, channels or []).exec()

        run_async(self.pool, lambda: self.client.live_streams(None), done,
                 lambda _: dlg.close())

    # -- view settings -----------------------------------------------------------
    def _apply_view_settings(self):
        """Re-reads density/sort/grid from settings, syncs the inline controls,
        switches the list between vertical rows and a wrapping grid, and
        refreshes so the view re-queries item sizes."""
        density = self.settings.value("view_density", "medium")
        grid = self.settings.value("view_grid", "false") == "true"
        self.delegate.set_density(density)
        self.delegate.set_grid(grid)
        if grid:
            self.listw.setViewMode(QListView.ViewMode.IconMode)
            self.listw.setFlow(QListView.Flow.LeftToRight)
            self.listw.setWrapping(True)
            self.listw.setResizeMode(QListView.ResizeMode.Adjust)
            self.listw.setGridSize(self.delegate.grid_size())
        else:
            self.listw.setViewMode(QListView.ViewMode.ListMode)
            self.listw.setFlow(QListView.Flow.TopToBottom)
            self.listw.setWrapping(False)
            self.listw.setGridSize(QSize())
        # Keep the inline controls in sync (e.g. when changed from Settings).
        if hasattr(self, "size_box"):
            for box, key in ((self.size_box, density),
                             (self.sort_box, self.settings.value("sort_order", "default"))):
                box.blockSignals(True)
                i = box.findData(key)
                if i >= 0:
                    box.setCurrentIndex(i)
                box.blockSignals(False)
            self.grid_btn.blockSignals(True)
            self.grid_btn.setChecked(grid)
            self.grid_btn.blockSignals(False)
        self._apply_filter()

    def _inline_view_changed(self, *_):
        """Persists the inline size/sort/grid controls and re-applies them."""
        self.settings.setValue("view_density", self.size_box.currentData())
        self.settings.setValue("sort_order", self.sort_box.currentData())
        self.settings.setValue("view_grid",
                               "true" if self.grid_btn.isChecked() else "false")
        self._apply_view_settings()

    # -- settings and errors -------------------------------------------------------
    @staticmethod
    def _combo(items, current):
        """items: list of (value, label). Returns a QComboBox with userData."""
        box = QComboBox()
        for value, label in items:
            box.addItem(label, value)
        idx = box.findData(current)
        if idx >= 0:
            box.setCurrentIndex(idx)
        return box

    def open_settings(self):
        d = QDialog(self)
        d.setWindowTitle("Settings")
        d.setMinimumWidth(440)
        outer = QVBoxLayout(d)
        outer.setContentsMargins(18, 18, 18, 18)
        tabs = QTabWidget()
        outer.addWidget(tabs)

        # ---- Playback tab ----
        play_tab = QWidget()
        pf = QFormLayout(play_tab)
        pf.setSpacing(10)
        player_box = self._combo([("mpv", "mpv"), ("vlc", "VLC")],
                                 self.settings.value("player", "mpv"))
        mode_items = [("embedded", "Embedded (in app)"),
                      ("window", "Reused mpv window"),
                      ("external", "External player")]
        if not self.player:
            mode_items = [m for m in mode_items if m[0] != "embedded"]
        mode_box = self._combo(mode_items, self.playback_mode())
        autoplay_box = self._combo([("true", "Yes"), ("false", "No")],
                                   "true" if self._autoplay_preview() else "false")
        fmt_box = self._combo([("ts", "ts"), ("m3u8", "m3u8")],
                              self.settings.value("stream_format", "ts"))
        pf.addRow("Default player", player_box)
        pf.addRow("Playback mode (mpv)", mode_box)
        pf.addRow("Auto-play preview on selection", autoplay_box)
        pf.addRow("Live stream format", fmt_box)
        mode_hint = QLabel("Embedded plays in the app. Reused mpv window keeps "
                           "one external window you can zap in (Ctrl+←/→). "
                           "External opens a fresh window each time.")
        mode_hint.setStyleSheet("color:#6E6E79; font-size:11px;")
        mode_hint.setWordWrap(True)
        pf.addRow(mode_hint)
        if not self.player:
            reason = embedded_playback_reason() or "unknown reason"
            hint = QLabel(f"Embedded playback unavailable: {reason}")
            hint.setStyleSheet("color:#6E6E79; font-size:11px;")
            hint.setWordWrap(True)
            pf.addRow(hint)
        tabs.addTab(play_tab, "Playback")

        # ---- Interface tab ----
        ui_tab = QWidget()
        uf = QFormLayout(ui_tab)
        uf.setSpacing(10)
        density_box = self._combo(
            [("compact", "Compact"), ("medium", "Medium"), ("large", "Large")],
            self.settings.value("view_density", "medium"))
        sort_box = self._combo(
            [("default", "Default (provider order)"),
             ("alpha_asc", "Name A -> Z"),
             ("alpha_desc", "Name Z -> A"),
             ("recent", "Recently added")],
            self.settings.value("sort_order", "default"))
        uf.addRow("List size", density_box)
        uf.addRow("Sort lists by", sort_box)
        tabs.addTab(ui_tab, "Interface")

        # ---- Playlists tab ----
        pl_tab = QWidget()
        pv = QVBoxLayout(pl_tab)
        pv.setSpacing(10)
        pl_list = QListWidget()
        pv.addWidget(pl_list, 1)
        pl_btns = QHBoxLayout()
        add_btn = QPushButton("Add...")
        edit_btn = QPushButton("Edit...")
        remove_btn = QPushButton("Remove")
        use_btn = QPushButton("Use", objectName="Primary")
        for b in (add_btn, edit_btn, remove_btn, use_btn):
            pl_btns.addWidget(b)
        pv.addLayout(pl_btns)
        tabs.addTab(pl_tab, "Playlists")

        # ---- Parental tab ----
        par_tab = QWidget()
        parv = QVBoxLayout(par_tab)
        parv.setSpacing(10)
        pin_status = QLabel()
        parv.addWidget(pin_status)
        set_pin_btn = QPushButton("Set / change PIN...")
        remove_pin_btn = QPushButton("Remove PIN")
        lock_now_btn = QPushButton("Lock now")
        parv.addWidget(set_pin_btn)
        parv.addWidget(remove_pin_btn)
        parv.addWidget(lock_now_btn)
        par_hint = QLabel("Lock favorite groups (right-click a group under "
                          "Favorites) or whole categories (right-click a "
                          "category in TV/Movies/Series). Locked content is "
                          "hidden - including from 'All' - until the PIN is "
                          "entered.")
        par_hint.setStyleSheet("color:#6E6E79; font-size:11px;")
        par_hint.setWordWrap(True)
        parv.addWidget(par_hint)
        parv.addStretch()
        tabs.addTab(par_tab, "Parental")

        def refresh_pin_status():
            if self.parental.has_pin():
                state = ("unlocked for this session"
                         if self.parental.session_unlocked else "locked")
                pin_status.setText(f"PIN is set - currently {state}.")
            else:
                pin_status.setText("No PIN set.")
            remove_pin_btn.setEnabled(self.parental.has_pin())
            lock_now_btn.setEnabled(self.parental.has_pin()
                                    and self.parental.session_unlocked)

        def set_pin():
            if self.parental.has_pin() and not self._request_unlock():
                return
            pin, ok = QInputDialog.getText(
                d, "Parental control", "New PIN:", QLineEdit.EchoMode.Password)
            pin = (pin or "").strip()
            if ok and pin:
                self.parental.set_pin(pin)
            refresh_pin_status()

        def remove_pin():
            if not self._request_unlock():
                return
            self.parental.clear_pin()
            refresh_pin_status()
            self._load_categories()

        def lock_now():
            self.parental.lock_session()
            refresh_pin_status()
            self._load_categories()

        set_pin_btn.clicked.connect(set_pin)
        remove_pin_btn.clicked.connect(remove_pin)
        lock_now_btn.clicked.connect(lock_now)
        refresh_pin_status()

        store = self.playlist_store

        def reload_pl_list():
            pl_list.clear()
            if not store:
                pl_list.addItem("Playlist management unavailable")
                return
            for p in store.playlists():
                suffix = "   (active)" if p["id"] == store.active_id else ""
                item = QListWidgetItem(f"{p['name']}  -  {p['server']}{suffix}")
                item.setData(Qt.ItemDataRole.UserRole, p["id"])
                pl_list.addItem(item)

        def selected_pid():
            item = pl_list.currentItem()
            return item.data(Qt.ItemDataRole.UserRole) if item else None

        def add_playlist():
            dlg = PlaylistDialog(d)
            if dlg.exec():
                store.add(dlg.values())
                reload_pl_list()

        def edit_playlist():
            pid = selected_pid()
            pl = store.get(pid) if (store and pid) else None
            if not pl:
                return
            dlg = PlaylistDialog(d, pl)
            if dlg.exec():
                store.update(pid, **dlg.values())
                reload_pl_list()
                if pid == store.active_id:
                    self.switch_playlist(pid)   # reconnect with new details

        def remove_playlist():
            pid = selected_pid()
            if not (store and pid):
                return
            if QMessageBox.question(
                    d, "Remove playlist",
                    "Remove this playlist? Its favorites and history are "
                    "kept until you re-add and clear them.") \
                    == QMessageBox.StandardButton.Yes:
                store.remove(pid)
                reload_pl_list()

        def use_playlist():
            pid = selected_pid()
            if store and pid and pid != store.active_id:
                self.switch_playlist(pid)
                reload_pl_list()

        add_btn.clicked.connect(add_playlist)
        edit_btn.clicked.connect(edit_playlist)
        remove_btn.clicked.connect(remove_playlist)
        use_btn.clicked.connect(use_playlist)
        if not store:
            for b in (add_btn, edit_btn, remove_btn, use_btn):
                b.setEnabled(False)
        reload_pl_list()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                  QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(d.accept)
        buttons.rejected.connect(d.reject)
        outer.addWidget(buttons)

        if d.exec():
            self.settings.setValue("player", player_box.currentData())
            self.settings.setValue("stream_format", fmt_box.currentData())
            self.settings.setValue("autoplay_preview", autoplay_box.currentData())
            if mode_box.currentData():
                self.settings.setValue("playback_mode", mode_box.currentData())
            self.settings.setValue("view_density", density_box.currentData())
            self.settings.setValue("sort_order", sort_box.currentData())
            self._apply_view_settings()

    def show_about(self):
        QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> {VERSION}<br><br>"
            "An elegant IPTV client for Xtream Codes with EPG,<br>"
            "embedded playback, favorites and history.<br><br>"
            "Playback via mpv (embedded/window) or VLC.")

    def _error(self, msg):
        self.loading_bar.hide()
        self.count_lbl.setText("Error: " + msg)

    def closeEvent(self, event):
        if self.player:
            self.player.shutdown()
        if self.mpv_window:
            self.mpv_window.shutdown()
        self.mpv.stop()
        # Chromecast teardown talks to the network - don't block app exit
        threading.Thread(target=self.cast.shutdown, daemon=True).start()
        super().closeEvent(event)

# ----------------------------------------------------------------------------
#  Application icon
# ----------------------------------------------------------------------------

def make_app_icon():
    """Draws the app icon (a rounded blue tile with a play symbol) at several sizes."""
    icon = QIcon()
    for s in (256, 128, 64, 48, 32):
        pm = QPixmap(s, s)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        tile = QPainterPath()
        tile.addRoundedRect(0, 0, s, s, s * 0.22, s * 0.22)
        p.fillPath(tile, QColor(ACCENT))
        tri = QPainterPath()
        tri.moveTo(s * 0.40, s * 0.28)
        tri.lineTo(s * 0.40, s * 0.72)
        tri.lineTo(s * 0.76, s * 0.50)
        tri.closeSubpath()
        p.fillPath(tri, QColor("white"))
        p.end()
        icon.addPixmap(pm)
    return icon


def install_icon(icon):
    """Saves the icon as 'dopeiptv' in the user's icon theme so the desktop
    file (Icon=dopeiptv) can find it in the application menu."""
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    target = base / "icons" / "hicolor" / "256x256" / "apps" / "dopeiptv.png"
    if target.exists():
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        icon.pixmap(256, 256).save(str(target), "PNG")
    except OSError:
        pass

# ----------------------------------------------------------------------------
#  Startup
# ----------------------------------------------------------------------------

def main():
    if _libmpv is None:
        print(f"[dopeIPTV] Embedded playback disabled: {_libmpv_error}",
              file=sys.stderr)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG)
    app.setApplicationDisplayName(APP_NAME)
    # Wayland shows the app_id in the taskbar; without this it would be
    # "python3". The id must match the desktop file (dopeiptv.desktop).
    app.setDesktopFileName("dopeiptv")
    icon = make_app_icon()
    app.setWindowIcon(icon)
    install_icon(icon)
    app.setStyleSheet(STYLE)
    print(f"[dopeIPTV] Qt platform: {app.platformName()}", file=sys.stderr)
    if _libmpv is not None:
        reason = embedded_playback_reason()
        if reason:
            print(f"[dopeIPTV] Embedded playback disabled: {reason}",
                  file=sys.stderr)
        else:
            print("[dopeIPTV] Embedded playback: enabled", file=sys.stderr)
    settings = QSettings(ORG, ORG)
    store = PlaylistStore(settings)

    client = None
    while client is None:
        pl = store.active()
        if pl is None:
            dlg = LoginDialog(settings)
            if not dlg.exec():
                return 0
            server, user, pw = dlg.values()
            name = server.split("//")[-1].split("/")[0] or "My playlist"
            pl = store.add({"name": name, "server": server, "username": user,
                            "password": pw, "epg_url": "", "refresh": "never"})
            store.set_active(pl["id"])

        candidate = XtreamClient(pl["server"], pl["username"], pl["password"])
        try:
            candidate.authenticate()
            client = candidate
            # keep the legacy single-account keys in sync (used by the login
            # dialog's prefill and as a fallback for tooling)
            settings.setValue("server", pl["server"])
            settings.setValue("username", pl["username"])
            settings.setValue("password", pl["password"])
        except Exception as e:
            QMessageBox.critical(None, "Connection failed",
                                 f"{pl['name']}: {e}")
            dlg = PlaylistDialog(None, pl)
            if not dlg.exec():
                return 0
            store.update(pl["id"], **dlg.values())

    w = MainWindow(client, settings, store)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
