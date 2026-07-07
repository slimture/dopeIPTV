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
    QAbstractListModel, QByteArray, QDateTime, QModelIndex, QObject, QRect,
    QRectF, QRunnable, QSettings, QSize, QStandardPaths, Qt, QThreadPool,
    QTimer, pyqtSignal, pyqtSlot,
)
from PyQt6.QtGui import (
    QColor, QFont, QIcon, QKeySequence, QOpenGLContext, QPainter,
    QPainterPath, QPen, QPixmap, QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDateTimeEdit, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QSlider, QSplitter, QStyle, QStyledItemDelegate, QTabWidget,
    QVBoxLayout, QWidget,
)
from PyQt6.QtOpenGLWidgets import QOpenGLWidget

APP_NAME = "dopeIPTV"
ORG = "dopeiptv"
VERSION = "0.1.0-beta.6"

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

    def timeshift_url(self, stream_id, start_dt, duration_min):
        """Catch-up/timeshift chunk: starts at start_dt and is duration_min
        minutes long. Only works for channels the provider archives
        (tv_archive)."""
        stamp = start_dt.strftime("%Y-%m-%d:%H-%M")
        return (f"{self.server}/timeshift/{self.username}/{self.password}/"
                f"{int(duration_min)}/{stamp}/{stream_id}.ts")


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


def epg_cache_path(playlist_id):
    """Per-playlist file for the cached XMLTV guide."""
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.CacheLocation)
    if not base:
        base = str(Path.home() / ".cache" / "dopeiptv")
    return str(Path(base) / f"epg_{playlist_id or 'default'}.xml")


class XmltvGuide:
    """Downloads and indexes the provider's XMLTV guide (xmltv.php).

    Many providers send no EPG via player_api but do have a full schedule
    in XMLTV format. Once loaded, lookups are pure in-memory dict access,
    so the main channel list can show "now playing" for every visible row
    without any extra network calls.

    The raw XML is cached on disk (per playlist) so a restart shows EPG
    immediately from the previous session: a cache younger than CACHE_TTL
    is used without hitting the network, and if a download fails the stale
    cache still beats no data. Downloads stream in chunks and report
    progress through progress_cb (0-100, or -1 when the server sends no
    content length) - called from the worker thread, so hook it up via a
    Qt signal.
    """

    CACHE_TTL = 6 * 3600      # re-download after this; guides update ~daily

    def __init__(self, client, custom_url=None, cache_path=None,
                 progress_cb=None):
        self.client = client
        self.custom_url = custom_url    # user-supplied XMLTV URL, if any
        self.cache_path = Path(cache_path) if cache_path else None
        self.progress_cb = progress_cb
        self._lock = threading.Lock()
        self._loaded = False
        self._failed = False
        self._by_id = {}        # channel id -> entries sorted by start time
        self._by_name = {}      # normalized display name -> channel id

    def _read_cache(self, max_age=None):
        if not self.cache_path or not self.cache_path.exists():
            return None
        try:
            if (max_age is not None
                    and time.time() - self.cache_path.stat().st_mtime > max_age):
                return None
            return self.cache_path.read_bytes()
        except OSError:
            return None

    def _write_cache(self, data):
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_bytes(data)
        except OSError:
            pass

    def _download(self):
        if self.custom_url:
            r = requests.get(self.custom_url, stream=True, timeout=(20, 300))
        else:
            r = self.client.session.get(
                f"{self.client.server}/xmltv.php",
                params={"username": self.client.username,
                        "password": self.client.password},
                stream=True, timeout=(20, 300))
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        chunks, received = [], 0
        for chunk in r.iter_content(64 * 1024):
            chunks.append(chunk)
            received += len(chunk)
            if self.progress_cb:
                self.progress_cb(min(99, received * 100 // total)
                                 if total else -1)
        if self.progress_cb:
            self.progress_cb(100)
        data = b"".join(chunks)
        self._write_cache(data)
        return data

    def ensure_loaded(self, force=False):
        """Loads the guide if needed (cache first, then network, then stale
        cache). force=True always re-downloads. Returns True when guide data
        is available in memory."""
        with self._lock:
            if self._loaded and not force:
                return True
            if self._failed and not force:
                return False
            data = None if force else self._read_cache(max_age=self.CACHE_TTL)
            source = "cache"
            if data is None:
                stale = None if force else self._read_cache(max_age=None)
                if stale is not None and not self._loaded:
                    # Show last session's guide immediately - the fresh
                    # download replaces it below without blocking the UI on
                    # an empty EPG in the meantime.
                    try:
                        self._parse(stale)
                        self._loaded = True
                    except Exception:
                        pass
                try:
                    data = self._download()
                    source = "download"
                except Exception:
                    data = stale
                    source = "stale cache (download failed)"
            if data is None:
                self._failed = True
                return False
            try:
                self._parse(data)
                self._loaded = True
                self._failed = False
                print(f"[dopeIPTV] EPG loaded from {source} "
                      f"({len(data) // 1024} KB)", file=sys.stderr)
            except Exception:
                self._failed = self._loaded is False
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
        p = self.current_programme(item)
        if p is None:
            return None
        length = p["stop_timestamp"] - p["start_timestamp"]
        now = datetime.now().astimezone().timestamp()
        pct = (now - p["start_timestamp"]) / length * 100 if length else 0
        return p["title"], pct

    def current_programme(self, item):
        """The full entry for the currently airing programme, or None -
        used by timeshift's 'watch from start'."""
        if not self._loaded:
            return None
        now = datetime.now().astimezone().timestamp()
        for p in self._entries_for(item):
            if p["start_timestamp"] <= now < p["stop_timestamp"]:
                return p
        return None

    # Keep this much of the past when parsing - catch-up/timeshift browsing
    # needs old programmes, not just current+future ones. Providers rarely
    # archive more than a week.
    KEEP_PAST_DAYS = 7

    def past_programmes(self, item, days):
        """Programmes that already ended, newest first, at most `days` back -
        the ones an archiving provider can still play via timeshift."""
        if not self.ensure_loaded():
            return []
        now = datetime.now().astimezone().timestamp()
        cutoff = now - days * 86400
        out = [p for p in self._entries_for(item)
               if p["stop_timestamp"] <= now
               and p["start_timestamp"] >= cutoff]
        out.reverse()
        return out

    def _parse(self, data):
        cutoff = (datetime.now().astimezone().timestamp()
                  - self.KEEP_PAST_DAYS * 86400)
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


class ChannelOverrides:
    """Per-playlist channel customizations: rename or hide individual
    channels/movies/series. This effectively becomes the user's own edited
    playlist while 'Restore default channels' brings back exactly what the
    provider sends."""

    def __init__(self, settings, key="channel_overrides"):
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

    def get(self, mode, key):
        return self.data.get(mode, {}).get(str(key), {})

    def update(self, mode, key, **fields):
        entry = self.data.setdefault(mode, {}).setdefault(str(key), {})
        entry.update(fields)
        if not any(entry.values()):
            del self.data[mode][str(key)]
        self._save()

    def display_name(self, mode, key, default):
        return self.get(mode, key).get("name") or default

    def is_hidden(self, mode, key):
        return bool(self.get(mode, key).get("hidden"))

    def has_overrides(self, mode):
        return bool(self.data.get(mode))

    def reset_mode(self, mode):
        self.data.pop(mode, None)
        self._save()

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


class WakeLock:
    """Keeps the screen and system awake while video plays - fullscreen or
    mini player. Linux: DBus inhibitors (org.freedesktop.ScreenSaver +
    PowerManagement, honored by GNOME and KDE). macOS: a caffeinate child
    process. Acquire/release are idempotent."""

    DBUS_SERVICES = (
        ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver",
         "org.freedesktop.ScreenSaver"),
        ("org.freedesktop.PowerManagement.Inhibit",
         "/org/freedesktop/PowerManagement/Inhibit",
         "org.freedesktop.PowerManagement.Inhibit"),
    )

    def __init__(self):
        self._cookies = []       # (QDBusInterface, cookie)
        self._proc = None

    @property
    def held(self):
        return bool(self._cookies or self._proc)

    def acquire(self, reason="Playing video"):
        if self.held:
            return
        if sys.platform == "darwin":
            try:
                self._proc = subprocess.Popen(
                    ["caffeinate", "-di"], stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
            return
        try:
            from PyQt6.QtDBus import QDBusConnection, QDBusInterface
        except Exception:
            return
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return
        for svc, path, iface_name in self.DBUS_SERVICES:
            iface = QDBusInterface(svc, path, iface_name, bus)
            if not iface.isValid():
                continue
            reply = iface.call("Inhibit", APP_NAME, reason)
            args = reply.arguments()
            if args and isinstance(args[0], int):
                self._cookies.append((iface, args[0]))

    def release(self):
        for iface, cookie in self._cookies:
            try:
                iface.call("UnInhibit", cookie)
            except Exception:
                pass
        self._cookies = []
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None


def safe_filename(name):
    """Strips characters that are unsafe in filenames."""
    cleaned = "".join(c for c in (name or "recording")
                      if c not in '/\\:*?"<>|').strip()
    return cleaned[:120] or "recording"


def format_size(nbytes):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if nbytes < 1024 or unit == "TB":
            return (f"{nbytes:.1f} {unit}" if unit not in ("B", "KB")
                    else f"{int(nbytes)} {unit}")
        nbytes /= 1024


class RecordingManager(QObject):
    """Records live streams to local files, immediately or on a start/stop
    timer. Uses ffmpeg (stream copy, no re-encode) when available, otherwise
    mpv's --stream-record. A 5-second tick starts due jobs, stops jobs that
    reached their stop time, and reaps recorder processes that exited on
    their own. Scheduled-but-not-yet-started jobs survive an app restart
    (persisted via QSettings); an active recording dies with the app."""

    jobs_changed = pyqtSignal()
    recording_stopped = pyqtSignal(str, str)   # title, reason - for loud UI

    VIDEO_EXTS = {".ts", ".mp4", ".mkv", ".avi", ".mov", ".webm", ".m2ts"}

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.jobs = []
        # Session-only size-cap override from the player's REC menu:
        # None = follow Settings, 0 = no limit, >0 = bytes.
        self.session_cap = None
        self._load()
        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self.tick)
        self._timer.start()

    # -- storage location ---------------------------------------------------
    def directory(self):
        d = self.settings.value("recordings_dir", "")
        if not d:
            d = os.path.join(os.path.expanduser("~"), "Videos", "dopeIPTV")
        return d

    def set_directory(self, d):
        self.settings.setValue("recordings_dir", d)

    def folders(self):
        """All subfolders (relative paths) below the recordings directory."""
        root = self.directory()
        found = []
        if os.path.isdir(root):
            for base, dirs, _files in os.walk(root):
                dirs.sort()
                for d in dirs:
                    found.append(os.path.relpath(os.path.join(base, d), root))
        return found

    def files(self, folder=None):
        """Recording files as list items. folder=None -> everything
        (recursive); otherwise only the given subfolder."""
        root = self.directory()
        base = os.path.join(root, folder) if folder else root
        out = []
        if not os.path.isdir(base):
            return out
        walker = (os.walk(base) if folder is None or folder == ""
                  else [(base, [], os.listdir(base))])
        for dirpath, _dirs, names in walker:
            for n in names:
                p = os.path.join(dirpath, n)
                if (os.path.splitext(n)[1].lower() in self.VIDEO_EXTS
                        and os.path.isfile(p)):
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    out.append({"name": os.path.splitext(n)[0], "_path": p,
                                "_key": p, "_kind": "recording",
                                "_size": st.st_size,
                                "added": str(int(st.st_mtime))})
        out.sort(key=lambda f: f["added"], reverse=True)
        return out

    # -- recorder backend -----------------------------------------------------
    @staticmethod
    def recorder():
        """(kind, executable) of the available recorder, or (None, None)."""
        ff = shutil.which("ffmpeg")
        if ff:
            return "ffmpeg", ff
        mpv = find_player_executable("mpv")
        if mpv:
            return "mpv", mpv
        return None, None

    # -- jobs -----------------------------------------------------------------
    def _load(self):
        try:
            data = json.loads(self.settings.value("recording_jobs", "") or "[]")
        except Exception:
            data = []
        now = time.time()
        for j in data if isinstance(data, list) else []:
            # stop=None means "record until stopped manually"
            if (j.get("status") == "scheduled"
                    and (j.get("stop") is None or j["stop"] > now)):
                j["proc"] = None
                self.jobs.append(j)

    def _save(self):
        keep = [{k: v for k, v in j.items() if k != "proc"}
                for j in self.jobs if j.get("status") == "scheduled"]
        self.settings.setValue("recording_jobs", json.dumps(keep))

    def add_job(self, url, title, start_ts, stop_ts, folder=""):
        job = {"id": uuid.uuid4().hex[:10], "url": url, "title": title,
               "start": start_ts, "stop": stop_ts, "folder": folder or "",
               "status": "scheduled", "path": "", "error": "", "proc": None}
        self.jobs.append(job)
        self._save()
        self.tick()
        self.jobs_changed.emit()
        return job

    def add_inplayer_job(self, title, path, stop_ts, url=""):
        """Registers a recording that rides on the embedded player's own
        stream (mpv stream-record) - the app never opens a second
        connection to the provider for these. The player side is started/
        stopped by the caller; stop_inplayer_cb is invoked when the job
        must end (timer, cancel, channel switch)."""
        job = {"id": uuid.uuid4().hex[:10], "url": url, "title": title,
               "start": time.time(), "stop": stop_ts, "folder": "",
               "status": "recording", "path": path, "error": "",
               "proc": None, "inplayer": True}
        self.jobs.append(job)
        self.jobs_changed.emit()
        return job

    stop_inplayer_cb = None      # set by the UI: stops mpv's stream-record

    def finish_inplayer(self, job_id, reason=""):
        for j in self.jobs:
            if (j["id"] != job_id or not j.get("inplayer")
                    or j["status"] != "recording"):
                continue
            if self.stop_inplayer_cb:
                try:
                    self.stop_inplayer_cb()
                except Exception:
                    pass
            j["status"] = "done"
            self.recording_stopped.emit(j["title"], reason or "stopped")
            self.jobs_changed.emit()

            # mpv buffers the recording and only flushes it to disk when
            # stream-record is cleared - validate the file after the flush
            # has had a moment to land, not right now.
            def validate(j=j, reason=reason):
                if (j.get("path") and os.path.exists(j["path"])
                        and os.path.getsize(j["path"]) > 0):
                    return
                j["status"] = "failed"
                j["error"] = reason or "no data captured"
                self.jobs_changed.emit()

            QTimer.singleShot(1500, validate)
            return

    def finish_all_inplayer(self, reason=""):
        for j in list(self.jobs):
            if j.get("inplayer") and j["status"] == "recording":
                self.finish_inplayer(j["id"], reason)

    def cancel(self, job_id):
        """Cancels a scheduled job or stops an active recording (the partial
        file is kept)."""
        for j in self.jobs:
            if j["id"] != job_id:
                continue
            if j.get("inplayer") and j["status"] == "recording":
                self.finish_inplayer(job_id)
            elif j["status"] == "recording":
                self._stop_proc(j)
                j["status"] = "done"
            elif j["status"] == "scheduled":
                j["status"] = "cancelled"
            self._save()
            self.jobs_changed.emit()
            return

    def remove_job(self, job_id):
        """Drops a finished/failed/cancelled job from the list."""
        self.jobs = [j for j in self.jobs
                     if j["id"] != job_id
                     or j["status"] in ("recording", "scheduled")]
        self._save()
        self.jobs_changed.emit()

    def clear_finished(self):
        """Drops every finished/failed/cancelled job from the list."""
        self.jobs = [j for j in self.jobs
                     if j["status"] in ("recording", "scheduled")]
        self.jobs_changed.emit()

    def prune_path(self, path):
        """Forgets finished jobs whose file was just deleted."""
        self.jobs = [j for j in self.jobs
                     if j["status"] in ("recording", "scheduled")
                     or j.get("path") != path]
        self.jobs_changed.emit()

    def active_count(self):
        return sum(1 for j in self.jobs if j["status"] == "recording")

    def build_path(self, title, folder=""):
        """A unique target file for a new recording (creates the folder).
        Raises OSError when the folder can't be created."""
        stamp = datetime.fromtimestamp(time.time()).strftime("%Y-%m-%d %H.%M")
        target_dir = os.path.join(self.directory(), folder or "")
        os.makedirs(target_dir, exist_ok=True)
        path = os.path.join(target_dir,
                            f"{safe_filename(title)} {stamp}.ts")
        n = 1
        while os.path.exists(path):
            path = os.path.join(target_dir,
                                f"{safe_filename(title)} {stamp} ({n}).ts")
            n += 1
        return path

    def _spawn(self, j):
        kind, exe = self.recorder()
        if not exe:
            j["status"] = "failed"
            j["error"] = "neither ffmpeg nor mpv found"
            return
        try:
            path = self.build_path(j["title"], j.get("folder") or "")
        except OSError as e:
            j["status"] = "failed"
            j["error"] = str(e)
            return
        # stop=None -> open-ended: no duration cap, runs until cancel()
        secs = (max(1, int(j["stop"] - time.time()))
                if j.get("stop") else None)
        if kind == "ffmpeg":
            cmd = [exe, "-y", "-loglevel", "error", "-i", j["url"],
                   "-c", "copy"]
            if secs:
                cmd += ["-t", str(secs)]
            cmd.append(path)
        else:
            cmd = [exe, j["url"], f"--stream-record={path}", "--vo=null",
                   "--ao=null", "--no-terminal"]
            if secs:
                cmd.append(f"--length={secs}")
        try:
            j["proc"] = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True)
            j["path"] = path
            j["status"] = "recording"
        except Exception as e:
            j["status"] = "failed"
            j["error"] = str(e)

    @staticmethod
    def _stop_proc(j):
        p = j.get("proc")
        if p and p.poll() is None:
            p.terminate()          # SIGTERM lets ffmpeg finalize the file
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()

    def _max_bytes(self):
        """Optional size cap for recordings: the session override from the
        player's REC menu wins; otherwise the Settings value (0 = no cap)."""
        if self.session_cap is not None:
            return self.session_cap
        try:
            val = float(self.settings.value("rec_max_value", 0) or 0)
        except (TypeError, ValueError):
            return 0
        mult = {"MB": 10**6, "GB": 10**9, "TB": 10**12}.get(
            self.settings.value("rec_max_unit", "GB"), 10**9)
        return int(val * mult) if val > 0 else 0

    def _over_size_cap(self, j, cap):
        try:
            return (cap and j.get("path") and os.path.exists(j["path"])
                    and os.path.getsize(j["path"]) >= cap)
        except OSError:
            return False

    def tick(self):
        now = time.time()
        cap = self._max_bytes()
        changed = False
        for j in self.jobs:
            if j["status"] == "scheduled" and j["start"] <= now:
                if j.get("stop") is not None and j["stop"] <= now:
                    j["status"] = "failed"
                    j["error"] = "stop time passed before the app could start it"
                else:
                    self._spawn(j)
                self._save()
                changed = True
            elif j["status"] == "recording" and j.get("inplayer"):
                if j.get("stop") is not None and j["stop"] <= now:
                    self.finish_inplayer(j["id"], "finished")
                elif self._over_size_cap(j, cap):
                    self.finish_inplayer(j["id"], "size limit reached")
            elif j["status"] == "recording":
                rc = j["proc"].poll() if j.get("proc") else 0
                if ((j.get("stop") is not None and j["stop"] <= now)
                        or self._over_size_cap(j, cap)):
                    self._stop_proc(j)
                    j["status"] = "done"
                    self.recording_stopped.emit(
                        j["title"],
                        "size limit reached" if self._over_size_cap(j, cap)
                        else "finished")
                    changed = True
                elif rc is not None:
                    ok = (rc == 0 and j.get("path")
                          and os.path.exists(j["path"])
                          and os.path.getsize(j["path"]) > 0)
                    j["status"] = "done" if ok else "failed"
                    if not ok:
                        j["error"] = f"recorder exited early (code {rc})"
                    self.recording_stopped.emit(
                        j["title"], "finished" if ok
                        else "recorder stopped unexpectedly")
                    changed = True
        if changed:
            self.jobs_changed.emit()

    def shutdown(self):
        self.finish_all_inplayer("app closed")
        for j in self.jobs:
            if j["status"] == "recording" and not j.get("inplayer"):
                self._stop_proc(j)
        self._save()


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


class _SeekSlider(QSlider):
    """Horizontal seek bar where a click jumps straight to that position
    (Qt's default is page-stepping) and dragging scrubs; the seek itself is
    only requested on release so mpv isn't hammered mid-drag."""

    seek_requested = pyqtSignal(int)   # seconds

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.dragging = False

    def _value_for(self, event):
        ratio = event.position().x() / max(1, self.width())
        span = self.maximum() - self.minimum()
        return int(self.minimum() + max(0.0, min(1.0, ratio)) * span)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.setValue(self._value_for(event))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.dragging:
            self.setValue(self._value_for(event))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.dragging and event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            self.seek_requested.emit(self.value())
            event.accept()
            return
        super().mouseReleaseEvent(event)


def _format_time(seconds):
    seconds = max(0, int(seconds or 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class EmbeddedPlayer(QWidget):
    """Video pane inside the app, rendered via libmpv's OpenGL render API.
    In fullscreen the control bar is hidden entirely; instead a translucent
    overlay with the channel/EPG info fades in on mouse movement and hides
    itself after a few seconds, so the video keeps the whole screen."""

    double_clicked = pyqtSignal()
    playback_error = pyqtSignal(str)
    zap = pyqtSignal(int)             # -1 previous / +1 next channel
    exit_fullscreen = pyqtSignal()    # the in-video "minimize" button
    timeshift_menu = pyqtSignal(object)   # anchor widget for the ⏪ menu
    record_menu = pyqtSignal(object)      # anchor widget for the REC menu

    OVERLAY_HIDE_MS = 3000

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self._settings = settings
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
        self.prev_btn = QPushButton("Back", objectName="MiniBtn")
        self.prev_btn.setToolTip("Previous channel (Ctrl+Left)")
        self.prev_btn.clicked.connect(lambda: self.zap.emit(-1))
        self.next_btn = QPushButton("Next", objectName="MiniBtn")
        self.next_btn.setToolTip("Next channel (Ctrl+Right)")
        self.next_btn.clicked.connect(lambda: self.zap.emit(1))
        self.pause_btn = QPushButton("⏸", objectName="MiniBtn")
        self.pause_btn.setToolTip("Pause / resume")
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.back_btn = QPushButton("-10s", objectName="MiniBtn")
        self.back_btn.setToolTip("Back 10 seconds")
        self.back_btn.clicked.connect(lambda: self._relative_seek(-10))
        self.back_btn.hide()
        self.fwd_btn = QPushButton("+30s", objectName="MiniBtn")
        self.fwd_btn.setToolTip("Forward 30 seconds")
        self.fwd_btn.clicked.connect(lambda: self._relative_seek(30))
        self.fwd_btn.hide()
        self.title_lbl = QLabel("", objectName="DetailMeta")
        # A long stream title must never force the pane (and with it the
        # whole app layout) wider - let the label clip instead.
        self.title_lbl.setSizePolicy(QSizePolicy.Policy.Ignored,
                                     QSizePolicy.Policy.Preferred)
        self.seek = _SeekSlider()
        self.seek.seek_requested.connect(self._do_seek)
        self.seek.hide()
        self.time_lbl = QLabel("", objectName="DetailMeta")
        self.time_lbl.hide()
        self.mute_btn = QPushButton("🔊", objectName="MiniBtn")
        self.mute_btn.setToolTip("Mute / unmute")
        self.mute_btn.clicked.connect(self.toggle_mute)
        self.vol = QSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setFixedWidth(80)
        self.vol.setToolTip("Volume")
        self.vol.valueChanged.connect(self._set_volume)
        self.ts_btn = QPushButton("⏪", objectName="MiniBtn")
        self.ts_btn.setToolTip("Timeshift / catch-up")
        self.ts_btn.clicked.connect(
            lambda: self.timeshift_menu.emit(self.ts_btn))
        self.ts_btn.hide()          # shown when the channel has an archive
        self.rec_btn = QPushButton("REC", objectName="MiniBtn")
        self.rec_btn.setToolTip("Record this channel")
        self.rec_btn.clicked.connect(
            lambda: self.record_menu.emit(self.rec_btn))
        self.rec_btn.hide()         # shown when a live channel is playing
        self.opts_btn = QPushButton("⚙", objectName="MiniBtn")
        self.opts_btn.setToolTip("Audio / subtitles / aspect / buffer")
        self.opts_btn.clicked.connect(
            lambda: self._show_options_menu(self.opts_btn))
        self.stop_btn = QPushButton("Stop", objectName="MiniBtn")
        self.stop_btn.clicked.connect(self.stop)
        self.fs_btn = QPushButton("Fullscreen", objectName="MiniBtn")
        bl.addWidget(self.prev_btn)
        bl.addWidget(self.next_btn)
        bl.addWidget(self.pause_btn)
        bl.addWidget(self.back_btn)
        bl.addWidget(self.fwd_btn)
        bl.addWidget(self.title_lbl, 1)
        bl.addWidget(self.seek, 2)
        bl.addWidget(self.time_lbl)
        bl.addWidget(self.mute_btn)
        bl.addWidget(self.vol)
        bl.addWidget(self.ts_btn)
        bl.addWidget(self.rec_btn)
        bl.addWidget(self.opts_btn)
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

        # Floating zap controls (fullscreen only) - appear together with the
        # overlay on mouse movement and auto-hide on inactivity.
        self.fs_controls = QWidget(self)
        self.fs_controls.setStyleSheet(
            "background: rgba(16,16,20,210); border-radius: 10px;")
        fc = QHBoxLayout(self.fs_controls)
        fc.setContentsMargins(8, 6, 8, 6)
        fc.setSpacing(8)
        self.fs_prev_btn = QPushButton("Back", objectName="MiniBtn")
        self.fs_prev_btn.setToolTip("Previous channel (Left)")
        self.fs_prev_btn.clicked.connect(lambda: self.zap.emit(-1))
        self.fs_next_btn = QPushButton("Next", objectName="MiniBtn")
        self.fs_next_btn.setToolTip("Next channel (Right)")
        self.fs_next_btn.clicked.connect(lambda: self.zap.emit(1))
        self.fs_pause_btn = QPushButton("⏸", objectName="MiniBtn")
        self.fs_pause_btn.setToolTip("Pause / resume")
        self.fs_pause_btn.clicked.connect(self.toggle_pause)
        self.fs_back_btn = QPushButton("-10s", objectName="MiniBtn")
        self.fs_back_btn.clicked.connect(lambda: self._relative_seek(-10))
        self.fs_back_btn.hide()
        self.fs_fwd_btn = QPushButton("+30s", objectName="MiniBtn")
        self.fs_fwd_btn.clicked.connect(lambda: self._relative_seek(30))
        self.fs_fwd_btn.hide()
        self.fs_seek = _SeekSlider()
        self.fs_seek.seek_requested.connect(self._do_seek)
        self.fs_seek.hide()
        self.fs_time_lbl = QLabel("", objectName="DetailMeta")
        self.fs_time_lbl.hide()
        self.fs_mute_btn = QPushButton("🔊", objectName="MiniBtn")
        self.fs_mute_btn.setToolTip("Mute / unmute")
        self.fs_mute_btn.clicked.connect(self.toggle_mute)
        self.fs_vol = QSlider(Qt.Orientation.Horizontal)
        self.fs_vol.setRange(0, 100)
        self.fs_vol.setFixedWidth(80)
        self.fs_vol.setToolTip("Volume")
        self.fs_vol.valueChanged.connect(self._set_volume)
        self.fs_ts_btn = QPushButton("⏪", objectName="MiniBtn")
        self.fs_ts_btn.setToolTip("Timeshift / catch-up")
        self.fs_ts_btn.clicked.connect(
            lambda: self.timeshift_menu.emit(self.fs_ts_btn))
        self.fs_ts_btn.hide()
        self.fs_rec_btn = QPushButton("REC", objectName="MiniBtn")
        self.fs_rec_btn.setToolTip("Record this channel")
        self.fs_rec_btn.clicked.connect(
            lambda: self.record_menu.emit(self.fs_rec_btn))
        self.fs_rec_btn.hide()
        self.fs_opts_btn = QPushButton("⚙", objectName="MiniBtn")
        self.fs_opts_btn.setToolTip("Audio / subtitles / aspect / buffer")
        self.fs_opts_btn.clicked.connect(
            lambda: self._show_options_menu(self.fs_opts_btn))
        self.fs_exit_btn = QPushButton("Exit fullscreen", objectName="MiniBtn")
        self.fs_exit_btn.setToolTip("Back to the mini player (Esc)")
        self.fs_exit_btn.clicked.connect(self.exit_fullscreen.emit)
        fc.addWidget(self.fs_prev_btn)
        fc.addWidget(self.fs_next_btn)
        fc.addWidget(self.fs_pause_btn)
        fc.addWidget(self.fs_back_btn)
        fc.addWidget(self.fs_fwd_btn)
        fc.addWidget(self.fs_seek, 1)
        fc.addWidget(self.fs_time_lbl)
        fc.addWidget(self.fs_mute_btn)
        fc.addWidget(self.fs_vol)
        fc.addWidget(self.fs_ts_btn)
        fc.addWidget(self.fs_rec_btn)
        fc.addWidget(self.fs_opts_btn)
        fc.addWidget(self.fs_exit_btn)
        self.fs_controls.hide()
        for wdg in (self.fs_controls, self.fs_prev_btn, self.fs_next_btn,
                    self.fs_pause_btn, self.fs_back_btn, self.fs_fwd_btn,
                    self.fs_seek, self.fs_time_lbl, self.fs_mute_btn,
                    self.fs_vol, self.fs_ts_btn,
                    self.fs_rec_btn, self.fs_opts_btn,
                    self.fs_exit_btn):
            wdg.setMouseTracking(True)
            wdg.installEventFilter(self)

        # Poll position/duration for the seek bar (only meaningful content
        # is seekable - live streams keep the bar hidden).
        self._pos_timer = QTimer(self)
        self._pos_timer.setInterval(500)
        self._pos_timer.timeout.connect(self._poll_position)

        self._fs_ui = False
        self.current_url = None      # what the mpv core is playing right now
        self._muted = False
        try:
            vol = int(self._settings.value("volume", 100)) if self._settings else 100
        except (TypeError, ValueError):
            vol = 100
        for s in (self.vol, self.fs_vol):
            s.blockSignals(True)
            s.setValue(vol)
            s.blockSignals(False)
        self._overlay_text = ""
        self._overlay_timer = QTimer(self)
        self._overlay_timer.setSingleShot(True)
        self._overlay_timer.setInterval(self.OVERLAY_HIDE_MS)
        self._overlay_timer.timeout.connect(self._hide_fs_ui)

    def eventFilter(self, obj, event):
        if obj is self.video:
            if event.type() == event.Type.MouseButtonDblClick:
                self.double_clicked.emit()
                return True
            if event.type() == event.Type.MouseMove and self._fs_ui:
                self._show_overlay()
        elif self._fs_ui and event.type() in (event.Type.Enter,
                                              event.Type.MouseMove):
            # hovering the floating controls keeps them alive
            self._overlay_timer.start()
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
        self._lock_video_box()
        if fullscreen:
            self._show_overlay()
        else:
            self._hide_fs_ui()
            self._overlay_timer.stop()
            self.unsetCursor()
            self.video.unsetCursor()

    def _hide_fs_ui(self):
        self.overlay.hide()
        self.fs_controls.hide()
        if self._fs_ui:
            # Inactivity in fullscreen also hides the mouse cursor. Set it
            # on the whole player widget, not just the video surface - the
            # pointer may rest on a margin or where a control just
            # disappeared, and then a video-only cursor never applied.
            self.setCursor(Qt.CursorShape.BlankCursor)
            self.video.setCursor(Qt.CursorShape.BlankCursor)

    def _show_overlay(self):
        self.unsetCursor()
        self.video.unsetCursor()
        if self._overlay_text:
            self.overlay.setText(self._overlay_text)
            self.overlay.show()
        self.fs_controls.show()
        self._place_overlay()
        self.overlay.raise_()
        self.fs_controls.raise_()
        self._overlay_timer.start()

    def _place_overlay(self):
        margin = 24
        if self.fs_seek.isVisibleTo(self.fs_controls):
            # seekable content: stretch the control row across the bottom
            controls_w = self.width() - 2 * margin
        else:
            controls_w = self.fs_controls.sizeHint().width()
        self.fs_controls.setFixedWidth(max(80, controls_w))
        self.fs_controls.adjustSize()
        self.fs_controls.move(self.width() - self.fs_controls.width() - margin,
                              self.height() - self.fs_controls.height() - margin)
        self.overlay.setFixedWidth(max(120, min(self.width() - 2 * margin, 640)))
        self.overlay.adjustSize()
        # the overlay sits above the control row so they never overlap
        self.overlay.move(margin, self.height() - self.fs_controls.height()
                          - margin - 8 - self.overlay.height())

    # Windowed mini-player video height. A constant, deliberately NOT
    # derived from the pane width: dragging the splitter or resizing the
    # window must never change the video's size (it did when the height
    # tracked the width).
    VIDEO_BOX_HEIGHT = 260

    def _lock_video_box(self):
        """Pins the video surface to a constant height in windowed mode.
        Whatever happens around it - splitter drags, window resizes, aspect
        overrides, EPG cards appearing below - the mini player keeps the
        same size and the content letterboxes inside it. Fullscreen lifts
        the pin so the video can fill the screen."""
        if self._fs_ui:
            self.video.setMinimumHeight(190)
            self.video.setMaximumHeight(16777215)
        else:
            self.video.setFixedHeight(self.VIDEO_BOX_HEIGHT)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._lock_video_box()
        if self.overlay.isVisible() or self.fs_controls.isVisible():
            self._place_overlay()

    def apply_default_options(self):
        """Applies the persistent playback defaults from Settings (audio
        language, subtitles, aspect ratio, network buffer) to the mpv core.
        Called on every play and when Settings are saved, so per-session ⚙
        tweaks still win until the next play."""
        m = self.video.mpv
        if m is None or self._settings is None:
            return
        s = self._settings

        def set_opt(prop, value):
            try:
                m[prop] = value
            except Exception:
                pass

        set_opt("alang", s.value("audio_lang", "") or "")
        sub_mode = s.value("sub_mode", "auto")
        if sub_mode == "off":
            set_opt("sid", "no")
        else:
            set_opt("sid", "auto")
            set_opt("slang", (s.value("sub_lang", "") or "")
                    if sub_mode == "lang" else "")
        aspect = s.value("aspect_mode", "auto")
        if aspect == "stretch":
            set_opt("keepaspect", False)
        else:
            set_opt("keepaspect", True)
            set_opt("video-aspect-override",
                    aspect if aspect != "auto" else "-1")

    def play(self, url, title):
        try:
            self.title_lbl.setText(title or "")
            self._hide_seek_ui()          # until the new content proves seekable
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
            try:
                m["cache"] = "yes"
                m["cache-secs"] = float(self._cache_secs())
            except Exception:
                pass
            self.apply_default_options()
            try:
                m["volume"] = float(self.vol.value())
                m["mute"] = self._muted
            except Exception:
                pass
            self._sync_pause_label(False)
            try:
                m.pause = False           # a new channel always starts playing
            except Exception:
                pass
            m.play(url)
            self.current_url = url
            self._pos_timer.start()
            return True
        except Exception as e:
            print(f"[dopeIPTV] Embedded playback failed: "
                 f"{type(e).__name__}: {e}", file=sys.stderr)
            self.current_url = None
            return False

    # -- seeking (movies/series/catch-up; live streams aren't seekable) -------
    def _seek_widgets(self):
        return (self.seek, self.time_lbl, self.back_btn, self.fwd_btn,
                self.fs_seek, self.fs_time_lbl, self.fs_back_btn,
                self.fs_fwd_btn)

    def _hide_seek_ui(self):
        for wdg in self._seek_widgets():
            wdg.hide()

    def _do_seek(self, seconds):
        m = self.video.mpv
        if m is None:
            return
        try:
            m.command("seek", seconds, "absolute")
        except Exception:
            pass

    def _relative_seek(self, seconds):
        m = self.video.mpv
        if m is None:
            return
        try:
            m.command("seek", seconds)
        except Exception:
            pass

    def toggle_pause(self):
        m = self.video.mpv
        if m is None:
            return
        try:
            m.pause = not m.pause
            self._sync_pause_label(m.pause)
        except Exception:
            pass

    def _sync_pause_label(self, paused):
        label = "▶" if paused else "⏸"
        self.pause_btn.setText(label)
        self.fs_pause_btn.setText(label)

    def _show_options_menu(self, anchor):
        """Audio track / subtitles / audio delay / aspect ratio / buffer."""
        m = self.video.mpv
        menu = QMenu(self)

        def tracks(kind):
            try:
                return [t for t in (m.track_list or []) if t.get("type") == kind]
            except Exception:
                return []

        def track_label(t):
            parts = [t.get("lang") or "", t.get("title") or ""]
            label = " ".join(p for p in parts if p).strip()
            return label or f"Track {t.get('id')}"

        audio = menu.addMenu("Audio track")
        for t in (tracks("audio") if m else []):
            act = audio.addAction(track_label(t))
            act.setCheckable(True)
            act.setChecked(bool(t.get("selected")))
            act.triggered.connect(
                lambda _c, tid=t.get("id"): self._set_mpv("aid", tid))
        if audio.isEmpty():
            audio.addAction("(no audio tracks)").setEnabled(False)

        subs = menu.addMenu("Subtitles")
        off = subs.addAction("Off")
        off.setCheckable(True)
        sub_tracks = tracks("sub") if m else []
        off.setChecked(not any(t.get("selected") for t in sub_tracks))
        off.triggered.connect(lambda _c: self._set_mpv("sid", "no"))
        for t in sub_tracks:
            act = subs.addAction(track_label(t))
            act.setCheckable(True)
            act.setChecked(bool(t.get("selected")))
            act.triggered.connect(
                lambda _c, tid=t.get("id"): self._set_mpv("sid", tid))

        delay = menu.addMenu("Audio delay")
        current_delay = 0.0
        try:
            current_delay = float(m["audio-delay"]) if m else 0.0
        except Exception:
            pass
        for val in (-1.0, -0.5, -0.25, 0.0, 0.25, 0.5, 1.0):
            act = delay.addAction(f"{val:+.2f} s" if val else "0 s (default)")
            act.setCheckable(True)
            act.setChecked(abs(current_delay - val) < 0.01)
            act.triggered.connect(
                lambda _c, v=val: self._set_mpv("audio-delay", v))

        aspect = menu.addMenu("Aspect ratio")
        for label, val in (("Auto", "-1"), ("16:9", "16:9"),
                           ("4:3", "4:3"), ("2.35:1", "2.35:1")):
            act = aspect.addAction(label)
            act.triggered.connect(
                lambda _c, v=val: self._set_mpv("video-aspect-override", v))
        stretch = aspect.addAction("Stretch to window")
        stretch.triggered.connect(lambda _c: self._set_mpv("keepaspect", False))

        buf = menu.addMenu("Network buffer")
        current_buf = self._cache_secs()
        for secs in (1, 3, 5, 10, 30):
            act = buf.addAction(f"{secs} s")
            act.setCheckable(True)
            act.setChecked(secs == current_buf)
            act.triggered.connect(lambda _c, s=secs: self._set_cache_secs(s))

        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _set_mpv(self, prop, value):
        m = self.video.mpv
        if m is None:
            return
        try:
            m[prop] = value
        except Exception as e:
            print(f"[dopeIPTV] set {prop}={value} failed: {e}", file=sys.stderr)

    def _cache_secs(self):
        try:
            return int(self._settings.value("cache_secs", 10)) \
                if self._settings else 10
        except (TypeError, ValueError):
            return 10

    def _set_cache_secs(self, secs):
        if self._settings:
            self._settings.setValue("cache_secs", str(secs))
        self._set_mpv("cache-secs", float(secs))

    def _poll_position(self):
        m = self.video.mpv
        if m is None:
            return
        try:
            dur = m.duration
            pos = m.playback_time
            paused = bool(m.pause)
        except Exception:
            return
        self._sync_pause_label(paused)
        seekable = bool(dur) and dur > 1
        if not seekable:
            self._hide_seek_ui()
            return
        text = f"{_format_time(pos)} / {_format_time(dur)}"
        for slider, label in ((self.seek, self.time_lbl),
                              (self.fs_seek, self.fs_time_lbl)):
            label.setText(text)
            slider.setVisible(True)
            label.setVisible(True)
            if not slider.dragging:
                slider.setMaximum(int(dur))
                slider.setValue(int(pos or 0))
        for btn in (self.back_btn, self.fwd_btn,
                    self.fs_back_btn, self.fs_fwd_btn):
            btn.setVisible(True)

    # -- volume ---------------------------------------------------------------
    def _set_volume(self, value):
        """Volume slider moved (either of them) - drive mpv, keep both
        sliders in sync, persist across sessions."""
        m = self.video.mpv
        if m is not None:
            try:
                m["volume"] = float(value)
            except Exception:
                pass
        for s in (self.vol, self.fs_vol):
            if s.value() != value:
                s.blockSignals(True)
                s.setValue(value)
                s.blockSignals(False)
        if self._settings is not None:
            self._settings.setValue("volume", int(value))

    def toggle_mute(self):
        self._muted = not self._muted
        m = self.video.mpv
        if m is not None:
            try:
                m["mute"] = self._muted
            except Exception:
                pass
        label = "🔇" if self._muted else "🔊"
        self.mute_btn.setText(label)
        self.fs_mute_btn.setText(label)

    # -- recording the watched stream itself ---------------------------------
    # mpv's stream-record dumps the exact stream this player is already
    # receiving to a file - no second connection to the provider, which
    # matters because most IPTV accounts allow only one stream at a time.

    def start_stream_record(self, path):
        m = self.video.mpv
        if m is None:
            return False
        try:
            m["stream-record"] = path
            return True
        except Exception as e:
            print(f"[dopeIPTV] stream-record failed: {e}", file=sys.stderr)
            return False

    def stop_stream_record(self):
        m = self.video.mpv
        if m is None:
            return
        try:
            m["stream-record"] = ""
        except Exception:
            pass

    def stop(self):
        self.stop_stream_record()
        self.current_url = None
        if self.video.mpv:
            try:
                self.video.mpv.command("stop")
            except Exception:
                pass
        self.title_lbl.setText("")
        self._hide_seek_ui()

    def shutdown(self):
        self.stop_stream_record()
        self._pos_timer.stop()
        self.video.shutdown()

# ----------------------------------------------------------------------------
#  Style - dark, macOS-inspired (dopeIPTV look), unified across all widgets
# ----------------------------------------------------------------------------

# ----------------------------------------------------------------------------
#  Themes
#
#  Every color in the app comes from the active palette P. The stylesheet is
#  rebuilt from it (build_style), and the delegate/labels read P at paint
#  time, so switching theme or accent in Settings applies live.
# ----------------------------------------------------------------------------

ACCENTS = {
    "blue":   ("Blue",   "#4C8DFF", "#5E99FF"),
    "purple": ("Purple", "#8E6BFF", "#A184FF"),
    "teal":   ("Teal",   "#2AC3C3", "#4AD4D4"),
    "green":  ("Green",  "#2FBF71", "#4CD08A"),
    "orange": ("Orange", "#FF9F43", "#FFB160"),
    "pink":   ("Pink",   "#FF5C8A", "#FF7AA1"),
    "red":    ("Red",    "#FF5C5C", "#FF7A7A"),
}

THEMES = {
    # role keys: bg (main), side (sidebar/menubar), pane (detail),
    # hover, sel (selection), input, btn, btn_hover, border, border_in,
    # scroll, scroll_hover, text, text2, text3, muted..muted4, error, rec
    "graphite": {
        "name": "Graphite (default)",
        "bg": "#17171C", "side": "#101014", "pane": "#1B1B21",
        "hover": "#1D1D24", "sel": "#26262E", "input": "#222229",
        "btn": "#2A2A32", "btn_hover": "#34343E",
        "border": "#232329", "border_in": "#2C2C34",
        "scroll": "#33333C", "scroll_hover": "#45454F",
        "text": "#ECECF1", "text2": "#C9C9D2", "text3": "#A7A7B1",
        "muted": "#8B8B96", "muted2": "#6E6E79", "muted3": "#5A5A64",
        "muted4": "#7A7A85", "error": "#FF6B6B", "rec": "#FF5C5C",
    },
    "midnight": {
        "name": "Midnight (blue)",
        "bg": "#0E1526", "side": "#0A101E", "pane": "#121A2E",
        "hover": "#182238", "sel": "#1E2A45", "input": "#16203A",
        "btn": "#1C2740", "btn_hover": "#28345A",
        "border": "#1C2740", "border_in": "#243052",
        "scroll": "#2A3654", "scroll_hover": "#3A4870",
        "text": "#E6EAF2", "text2": "#C3CBD9", "text3": "#9AA5B8",
        "muted": "#8E99AF", "muted2": "#6B7690", "muted3": "#57627B",
        "muted4": "#77829C", "error": "#FF6B6B", "rec": "#FF5C5C",
    },
    "oled": {
        "name": "OLED (pure black)",
        "bg": "#000000", "side": "#000000", "pane": "#0A0A0C",
        "hover": "#16161A", "sel": "#202026", "input": "#121216",
        "btn": "#1A1A20", "btn_hover": "#26262E",
        "border": "#1C1C22", "border_in": "#26262C",
        "scroll": "#2E2E36", "scroll_hover": "#3E3E48",
        "text": "#F2F2F6", "text2": "#CFCFD8", "text3": "#A8A8B4",
        "muted": "#8B8B96", "muted2": "#6E6E79", "muted3": "#5A5A64",
        "muted4": "#7A7A85", "error": "#FF6B6B", "rec": "#FF5C5C",
    },
    "nord": {
        "name": "Nord",
        "bg": "#2E3440", "side": "#272C36", "pane": "#333947",
        "hover": "#3B4252", "sel": "#434C5E", "input": "#3B4252",
        "btn": "#434C5E", "btn_hover": "#4C566A",
        "border": "#262B35", "border_in": "#4C566A",
        "scroll": "#4C566A", "scroll_hover": "#5E6A82",
        "text": "#ECEFF4", "text2": "#D8DEE9", "text3": "#B8C0D0",
        "muted": "#94A0B8", "muted2": "#7B879D", "muted3": "#6A7590",
        "muted4": "#8590A6", "error": "#FF6B6B", "rec": "#FF5C5C",
    },
    "light": {
        "name": "Light",
        "bg": "#F5F5F7", "side": "#ECECEF", "pane": "#FFFFFF",
        "hover": "#E4E4E9", "sel": "#D8D8DF", "input": "#FFFFFF",
        "btn": "#E8E8EC", "btn_hover": "#DCDCE2",
        "border": "#D9D9DE", "border_in": "#C9C9D2",
        "scroll": "#C5C5CE", "scroll_hover": "#ADADB8",
        "text": "#1B1B1F", "text2": "#3A3A42", "text3": "#55555F",
        "muted": "#6E6E79", "muted2": "#8B8B96", "muted3": "#8B8B96",
        "muted4": "#7A7A85", "error": "#D93025", "rec": "#D93025",
    },
}

P = {}                      # the active palette (roles + accent/accent_hi)
ACCENT = ACCENTS["blue"][1]  # kept in sync by apply_theme()


def apply_theme(settings=None, theme=None, accent=None):
    """Activates a theme + accent into the global palette P."""
    global ACCENT
    if settings is not None:
        theme = theme or settings.value("theme", "graphite")
        accent = accent or settings.value("accent", "blue")
    base = THEMES.get(theme or "graphite", THEMES["graphite"])
    acc = ACCENTS.get(accent or "blue", ACCENTS["blue"])
    P.clear()
    P.update(base)
    P["accent"], P["accent_hi"] = acc[1], acc[2]
    ACCENT = acc[1]


apply_theme()               # defaults until main() reads the settings


def build_style():
    p = dict(P)
    return f"""
* {{
    font-family: "SF Pro Text", "Inter", "Cantarell", "Noto Sans", sans-serif;
    color: {p['text']};
}}
QMainWindow, QDialog {{ background: {p['bg']}; }}

/* Sidebar */
#Sidebar {{
    background: {p['side']};
    border-right: 1px solid {p['border']};
}}
#AppTitle {{ font-size: 15px; font-weight: 700; letter-spacing: 0.5px; }}
#AppSub   {{ color: {p['muted4']}; font-size: 11px; }}

QPushButton#NavBtn {{
    background: transparent; border: none; border-radius: 8px;
    padding: 8px 12px; text-align: left; font-size: 13px; color: {p['text2']};
}}
QPushButton#NavBtn:hover  {{ background: {p['hover']}; }}
QPushButton#NavBtn:checked {{ background: {ACCENT}; color: white; font-weight: 600; }}

#SectionLabel {{
    color: {p['muted2']}; font-size: 10px; font-weight: 700;
    letter-spacing: 1.2px; padding: 10px 14px 4px 14px;
}}

QListWidget {{
    background: transparent; border: none; outline: none; font-size: 13px;
}}
QListWidget::item {{ border-radius: 8px; padding: 7px 10px; margin: 1px 6px; color: {p['text2']}; }}
QListWidget::item:hover    {{ background: {p['hover']}; }}
QListWidget::item:selected {{ background: {p['sel']}; color: {p['text']}; }}

/* Middle column */
#MiddlePane {{ background: {p['bg']}; }}
QLineEdit#Search {{
    background: {p['input']}; border: 1px solid {p['border_in']}; border-radius: 9px;
    padding: 8px 12px; font-size: 13px;
}}
QLineEdit#Search:focus {{ border: 1px solid {ACCENT}; }}

QListView#Channels {{
    background: transparent; border: none; outline: none; font-size: 13px;
}}

#ChNum   {{ font-size: 11px; color: {p['muted3']}; }}

QProgressBar#LoadBar {{
    background: transparent; border: none; max-height: 3px;
}}
QProgressBar#LoadBar::chunk {{ background: {ACCENT}; }}

QProgressBar#EpgBar {{
    background: {p['btn']}; border: none; border-radius: 2px; max-height: 4px;
}}
QProgressBar#EpgBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}

QSlider::groove:horizontal {{
    background: {p['btn']}; height: 4px; border-radius: 2px;
}}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {ACCENT}; width: 12px; height: 12px;
    margin: -4px 0; border-radius: 6px;
}}
QSlider::handle:horizontal:hover {{ background: {p['accent_hi']}; }}

/* Detail panel */
#DetailPane {{ background: {p['pane']}; border-left: 1px solid {p['border']}; }}
#DetailTitle {{ font-size: 20px; font-weight: 700; }}
#DetailMeta  {{ color: {p['muted']}; font-size: 12px; }}
#NowTitle    {{ font-size: 14px; font-weight: 600; }}
#NowTime     {{ color: {ACCENT}; font-size: 11px; font-weight: 600; }}
#NowDesc     {{ color: {p['text3']}; font-size: 12px; }}

QFrame#Card {{
    background: {p['input']}; border: 1px solid {p['border_in']}; border-radius: 12px;
}}
QLabel#EpgRowTime  {{ color: {ACCENT}; font-size: 11px; font-weight: 600; }}
QLabel#EpgRowTitle {{ font-size: 12px; }}

QPushButton {{
    background: {p['btn']}; border: 1px solid {p['btn_hover']}; border-radius: 9px;
    padding: 9px 16px; font-size: 13px; font-weight: 600;
}}
QPushButton:hover  {{ background: {p['btn_hover']}; }}
QPushButton#Primary {{ background: {ACCENT}; border: none; color: white; }}
QPushButton#Primary:hover {{ background: {p['accent_hi']}; }}
QPushButton#MiniBtn {{ padding: 4px 10px; font-size: 11px; border-radius: 7px; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p['scroll']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {p['scroll_hover']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}

/* Menu bar + context menus: themed on every platform (Linux GTK/Qt themes
   default to a white menu bar and popup unless styled explicitly). */
QMenuBar {{
    background: {p['side']}; color: {p['text2']}; border-bottom: 1px solid {p['border']};
}}
QMenuBar::item {{
    background: transparent; padding: 4px 10px; margin: 0; border-radius: 6px;
    font-size: 12px;
}}
QMenuBar::item:selected {{ background: {p['hover']}; }}
QMenuBar::item:pressed {{ background: {ACCENT}; color: white; }}

QMenu {{
    background: {p['hover']}; border: 1px solid {p['border_in']}; border-radius: 8px;
    padding: 5px; font-size: 12px;
}}
QMenu::item {{
    background: transparent; color: {p['text']}; border-radius: 6px;
    padding: 5px 20px 5px 10px; font-size: 12px;
}}
QMenu::item:selected {{ background: {ACCENT}; color: white; }}
QMenu::item:disabled {{ color: {p['muted2']}; }}
QMenu::separator {{ height: 1px; background: {p['border_in']}; margin: 5px 8px; }}

QToolTip {{
    background: {p['hover']}; color: {p['text']}; border: 1px solid {p['border_in']};
    padding: 4px 6px;
}}

/* Tab widget (Settings): the platform default renders a white pane/tab bar */
QTabWidget::pane {{
    border: 1px solid {p['border_in']}; border-radius: 8px; background: {p['pane']};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent; color: {p['text2']}; padding: 7px 16px;
    border-radius: 7px; margin: 2px; font-size: 12px;
}}
QTabBar::tab:selected {{ background: {p['btn']}; color: {p['text']}; }}
QTabBar::tab:hover:!selected {{ background: {p['hover']}; }}

QComboBox {{
    background: {p['input']}; border: 1px solid {p['border_in']}; border-radius: 8px;
    padding: 5px 10px; font-size: 12px;
    combobox-popup: 0;   /* plain dropdown sized to its items - no scrolling */
}}
QComboBox QAbstractItemView {{
    background: {p['input']}; border: 1px solid {p['border_in']}; border-radius: 6px;
    selection-background-color: {ACCENT}; selection-color: white;
    outline: none; font-size: 12px; padding: 3px;
}}
QComboBox QAbstractItemView::item {{ min-height: 22px; padding: 3px 8px; }}
QComboBox#InlineCombo {{ padding: 3px 8px; font-size: 11px; }}
QPushButton#InlineToggle {{
    padding: 4px 12px; font-size: 11px; border-radius: 7px;
}}
QPushButton#InlineToggle:checked {{ background: {ACCENT}; border: none; color: white; }}
#MiddlePane QLabel {{ color: {p['muted2']}; font-size: 11px; }}
QLineEdit {{
    background: {p['input']}; border: 1px solid {p['border_in']}; border-radius: 8px;
    padding: 8px 10px;
}}
QLineEdit:focus {{ border: 1px solid {ACCENT}; }}

/* The non-native file dialog (recordings folder picker): without these its
   views keep the platform's white background under our light text. */
QFileDialog QTreeView, QFileDialog QListView {{
    background: {p['input']}; border: 1px solid {p['border_in']};
    border-radius: 6px; color: {p['text']}; outline: none;
}}
QFileDialog QTreeView::item, QFileDialog QListView::item {{
    padding: 3px 6px; color: {p['text']};
}}
QFileDialog QTreeView::item:hover, QFileDialog QListView::item:hover {{
    background: {p['hover']};
}}
QFileDialog QTreeView::item:selected, QFileDialog QListView::item:selected {{
    background: {ACCENT}; color: white;
}}
QHeaderView::section {{
    background: {p['btn']}; color: {p['text2']}; border: none;
    padding: 4px 8px; font-size: 11px;
}}
QFileDialog QToolButton {{
    background: {p['btn']}; border: 1px solid {p['btn_hover']};
    border-radius: 6px; padding: 4px 8px;
}}
QFileDialog QToolButton:hover {{ background: {p['btn_hover']}; }}
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
        subtitle.setStyleSheet(f"color:{P['muted']};")
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
        self.status.setStyleSheet(f"color:{P['error']}; font-size:12px;")
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

class ChannelListView(QListView):
    """Right-clicking must never move the selection: selecting is what
    starts the preview player, so a right-click on another channel would
    switch away from what's playing. The press is swallowed here; Qt still
    delivers the QContextMenuEvent separately, so the context menu (which
    targets the item under the cursor, not the selection) works as before."""

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            e.accept()
            return
        super().mousePressEvent(e)


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

    def _is_playing(self, it, kind):
        """True when this row is the item currently playing. Compared within
        the matching list kind only - a movie and a live channel can share
        the same numeric id."""
        group = {"live": "live", "fav": "live", "vod": "vod",
                 "episode": "episode", "history": "history",
                 "rec": "rec"}.get(kind)
        w = self.window
        if w._playing_key is None:
            return False
        if kind == "history":
            # history rows carry their own key/kind
            return (it.get("_key") == w._playing_key
                    and {"live": "live", "movie": "vod",
                         "episode": "episode"}.get(it.get("_kind"))
                    == w._playing_group)
        return group == w._playing_group and w._item_key(it) == w._playing_key

    def _paint_grid(self, painter, option, index):
        it = index.data(Qt.ItemDataRole.UserRole) or {}
        kind = index.model().kind
        rect = option.rect
        logo_sz = self.grid_logo
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        playing = self._is_playing(it, kind)
        inner = rect.adjusted(5, 5, -5, -5)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(P["sel"]))
            painter.drawRoundedRect(inner, 12, 12)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(P["hover"]))
            painter.drawRoundedRect(inner, 12, 12)
        if playing:
            pen = QPen(QColor(ACCENT))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(inner, 12, 12)

        name = self.window.channel_display_name(it)
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
            painter.setBrush(QColor(P["sel"]))
            painter.drawRoundedRect(logo_rect, radius, radius)
            painter.setPen(QColor(P["text"]))
            f = QFont(); f.setPointSize(max(14, logo_sz // 3)); f.setBold(True)
            painter.setFont(f)
            painter.drawText(logo_rect, Qt.AlignmentFlag.AlignCenter,
                             name.strip()[:1].upper())
            if url and url not in self.window.logos.waiting:
                self.window.logos.get(url, lambda _pm: self.window.listw.viewport().update())

        painter.setPen(QColor(ACCENT) if playing else QColor(P["text"]))
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

        playing = self._is_playing(it, kind)

        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, QColor(P["sel"]))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(rect, QColor(P["hover"]))
        if playing:
            # accent bar on the left edge marks the channel that's playing
            painter.fillRect(QRect(rect.left(), rect.top() + 4, 3,
                                   rect.height() - 8), QColor(ACCENT))

        name = self.window.channel_display_name(it)
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
            painter.setBrush(QColor(P["sel"]))
            painter.drawRoundedRect(logo_rect, radius, radius)
            painter.setPen(QColor(P["text"]))
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
            # ⏪ marks channels whose provider keeps a catch-up archive
            has_archive = self.window._timeshift_days(it) > 0
            num_w = 52 if has_archive else 34
            painter.setPen(QColor(P["muted3"]))
            fnum = QFont()
            fnum.setPointSize(10)
            painter.setFont(fnum)
            num_rect = QRect(rect.right() - 12 - num_w, rect.top(), num_w, rect.height())
            painter.drawText(num_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                             ("⏪ " if has_archive else "") + str(it["num"]))

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

        painter.setPen(QColor(ACCENT) if playing else QColor(P["text"]))
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
            painter.setPen(QColor(P["muted"]))
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
        self.info_lbl.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
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
        hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
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
    epg_progress = pyqtSignal(int)   # guide download progress, worker thread

    def __init__(self, client: XtreamClient, settings: QSettings,
                 playlists: "PlaylistStore | None" = None):
        super().__init__()
        self.client = client
        self.settings = settings
        self.playlist_store = playlists
        active_pl = playlists.active() if playlists else None
        self.pool = QThreadPool.globalInstance()
        self.logos = LogoLoader(self.pool)
        self.epg_progress.connect(self._on_epg_progress)
        pid = (active_pl or {}).get("id")
        self.xmltv = XmltvGuide(client, (active_pl or {}).get("epg_url") or None,
                                cache_path=epg_cache_path(pid) if pid else None,
                                progress_cb=self.epg_progress.emit)
        self.favs = FavoriteStore(
            settings, f"favorites_{pid}" if pid else "favorites")
        self.history = HistoryStore(
            settings, f"history_{pid}" if pid else "history")
        self.overrides = CategoryOverrides(
            settings, f"category_overrides_{pid}" if pid else "category_overrides")
        self.channel_ov = ChannelOverrides(
            settings, f"channel_overrides_{pid}" if pid else "channel_overrides")
        self.parental = ParentalControl(settings)
        self.cast = ChromecastManager()
        self.rec = RecordingManager(settings, self)
        self.rec.jobs_changed.connect(self._recordings_changed)
        self.rec.recording_stopped.connect(self._on_recording_stopped)
        self.wake = WakeLock()   # no screensaver/suspend while video plays
        self._raw_categories = []          # unfiltered, for the content manager
        self.mpv = MpvIpcPlayer()
        self.mpv_window = MpvWindowPlayer() if _libmpv is not None else None
        if self.mpv_window:
            self.mpv_window.zap_requested.connect(self._zap)
            self.mpv_window.playback_error.connect(self._playback_error)
            self.mpv_window.closed.connect(lambda: self.wake.release())
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
        self._playing_key = None            # identity of the playing item
        self._playing_group = None          # which list kind that key is from
        self._playing_item = None           # live channel item, for ⏪/REC
        self._last_player = None
        self._last_playlist_refresh = time.time()

        # Qt appends the application display name ("... - dopeIPTV") itself,
        # so the window title is just the context: the playlist name, or the
        # playing channel while something plays.
        self._base_title = (active_pl or {}).get("name", "")
        self.setWindowTitle(self._base_title)
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
                          ("fav", "Favorites"), ("rec", "Recordings"),
                          ("history", "History")):
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

        self.listw = ChannelListView(objectName="Channels")
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
        self.count_lbl.setStyleSheet(f"color:{P['muted3']}; font-size:11px;")
        status_row = QHBoxLayout()
        status_row.addWidget(self.count_lbl, 1)
        # Persistent "something is recording" indicator - click to stop a
        # recording or jump to the Recordings section.
        self.rec_indicator = QPushButton("● REC")
        self.rec_indicator.setFlat(True)
        self.rec_indicator.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rec_indicator.setStyleSheet(
            f"color:{P['rec']}; font-weight:700; font-size:11px;"
            "border:none; background:transparent; padding:0 4px;")
        self.rec_indicator.clicked.connect(self._rec_indicator_menu)
        self.rec_indicator.hide()
        status_row.addWidget(self.rec_indicator)
        ml.addLayout(status_row)

        # ---------- Detail panel ----------
        det = QWidget(objectName="DetailPane")
        dl = QVBoxLayout(det)
        dl.setContentsMargins(20, 22, 20, 18)
        dl.setSpacing(12)

        self.player = None
        if embedded_playback_supported():
            self.player = EmbeddedPlayer(settings=self.settings)
            self.player.hide()
            self.player.fs_btn.clicked.connect(self._toggle_player_fullscreen)
            self.player.double_clicked.connect(self._toggle_player_fullscreen)
            self.player.exit_fullscreen.connect(self._exit_player_fullscreen)
            self.player.timeshift_menu.connect(self._player_timeshift_menu)
            self.player.record_menu.connect(self._player_record_menu)
            # In-player recordings ride on this player's stream; the manager
            # calls back here to stop mpv's stream-record when a job ends.
            self.rec.stop_inplayer_cb = self.player.stop_stream_record
            self.player.stop_btn.clicked.connect(
                lambda: self.rec.finish_all_inplayer("playback stopped"))
            self.player.stop_btn.clicked.connect(
                lambda: self.wake.release())
            self.player.playback_error.connect(self._playback_error)
            self.player.zap.connect(self._zap)
            self.player.stop_btn.clicked.connect(self.player.hide)
            dl.addWidget(self.player, 2)

        self.stream_error = QLabel("")
        self.stream_error.setStyleSheet(f"color:{P['error']}; font-size:12px;")
        self.stream_error.setWordWrap(True)
        self.stream_error.hide()
        dl.addWidget(self.stream_error)

        self.d_logo = QLabel()
        self.d_logo.setFixedSize(84, 84)
        self.d_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.d_logo.setStyleSheet(
            f"background:{P['sel']}; border-radius:18px; font-size:30px; font-weight:700;")
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
        self.epg_refresh.clicked.connect(self._refresh_epg_clicked)
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
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self,
                  activated=self._delete_pressed)

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
        # A double-click on the Fullscreen button fires two toggles back to
        # back; on Wayland the enter/exit then race the compositor and the
        # window can end up fullscreen with no player-fullscreen state left
        # to exit from. Debounce, and let Esc rescue any stray state (below).
        now = time.time()
        if now - getattr(self, "_fs_toggled_at", 0.0) < 0.4:
            return
        self._fs_toggled_at = now
        if self._player_fs:
            self._exit_player_fullscreen()
            return
        self._player_fs = True
        self._fs_return_index = self.listw.currentIndex()
        self._fs_return_scroll = self.listw.verticalScrollBar().value()
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
            # Rescue hatch: if the window itself got stuck fullscreen (e.g.
            # a double-clicked toggle raced the compositor), Esc still
            # restores a normal window.
            if self.isFullScreen():
                self.showNormal()
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
        # Coming back from fullscreen must land on the playing channel, not
        # at the top of the list.
        idx = getattr(self, "_fs_return_index", None)
        scroll = getattr(self, "_fs_return_scroll", None)
        if idx is not None and idx.isValid():
            QTimer.singleShot(0, lambda: (
                self.listw.setCurrentIndex(idx),
                self.listw.scrollTo(
                    idx, QAbstractItemView.ScrollHint.PositionAtCenter)))
        elif scroll is not None:
            # Nothing was selected - at least put the scroll position back
            # where the user left it instead of jumping to the top.
            QTimer.singleShot(0, lambda: (
                self.listw.verticalScrollBar().setValue(scroll)))

    # -- playlists ---------------------------------------------------------------
    REFRESH_SECONDS = {"2h": 2 * 3600, "6h": 6 * 3600, "12h": 12 * 3600,
                       "24h": 24 * 3600, "1w": 7 * 24 * 3600}

    def _maybe_auto_refresh(self):
        pl = self.playlist_store.active() if self.playlist_store else None
        secs = self.REFRESH_SECONDS.get((pl or {}).get("refresh", ""))
        if secs and time.time() - self._last_playlist_refresh >= secs:
            self.refresh_playlist()

    def refresh_playlist(self):
        """Re-fetches categories/content and re-downloads the XMLTV guide."""
        self._last_playlist_refresh = time.time()
        pl = self.playlist_store.active() if self.playlist_store else None
        pid = (pl or {}).get("id")
        self.xmltv = XmltvGuide(self.client, (pl or {}).get("epg_url") or None,
                                cache_path=epg_cache_path(pid) if pid else None,
                                progress_cb=self.epg_progress.emit)
        self._info_cache.clear()
        self._load_categories()
        run_async(self.pool, lambda: self.xmltv.ensure_loaded(force=True),
                  lambda ok: (self._epg_progress_finished(),
                              self.list_model.refresh_all() if ok else None),
                  lambda _: self._epg_progress_finished())

    def switch_playlist(self, pid):
        """Connects to another saved playlist and reloads everything."""
        pl = self.playlist_store.get(pid) if self.playlist_store else None
        if not pl:
            return
        self.loading_bar.show()
        self._set_status(f"Connecting to {pl['name']}...")
        candidate = XtreamClient(pl["server"], pl["username"], pl["password"])

        def done(_auth):
            self.loading_bar.hide()
            self.playlist_store.set_active(pid)
            self.client = candidate
            self.favs = FavoriteStore(self.settings, f"favorites_{pid}")
            self.history = HistoryStore(self.settings, f"history_{pid}")
            self._base_title = pl["name"]
            self.setWindowTitle(self._base_title)
            self.refresh_playlist()

        def fail(msg):
            self.loading_bar.hide()
            self._set_status("")
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
        # History and Recordings support multi-select (Ctrl/Shift-click,
        # Ctrl+A, rubber band) so several entries can be removed at once.
        self.listw.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
            if mode in ("history", "rec")
            else QAbstractItemView.SelectionMode.SingleSelection)
        self.search.clear()
        self._load_categories()

    def _load_categories(self):
        self.cat_list.clear()
        self.list_model.set_items([], self.mode)
        if self.mode == "rec":
            self.cat_list.blockSignals(True)
            for label, data in [("All recordings", None),
                                ("Active & scheduled", "__jobs__")]:
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, data)
                self.cat_list.addItem(item)
            for rel in self.rec.folders():
                item = QListWidgetItem(rel)
                item.setData(Qt.ItemDataRole.UserRole, rel)
                self.cat_list.addItem(item)
            self.cat_list.blockSignals(False)
            self.cat_list.setCurrentRow(0)
            return
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
        self._set_status("Loading categories...")
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
        if self.mode == "rec":
            if category_id == "__jobs__":
                self.all_items = [self._job_item(j)
                                  for j in reversed(self.rec.jobs)]
            else:
                self.all_items = self.rec.files(category_id)
            self._apply_filter()
            return
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
        self._set_status("Loading content...")
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
              "episode": "episodes", "fav": "favorites",
              "history": "history items", "rec": "recordings"}

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

    def channel_display_name(self, it):
        """The channel's name with any user rename applied."""
        base = it.get("name") or it.get("title") or "?"
        mode = "episode" if self.series_ctx else self.mode
        if mode in ("live", "vod", "series", "fav"):
            key = self._item_key(it)
            if key is not None:
                ov_mode = "live" if mode == "fav" else mode
                return self.channel_ov.display_name(ov_mode, key, base)
        return base

    def _channel_hidden(self, it, kind):
        if kind not in ("live", "vod", "series", "fav"):
            return False
        key = self._item_key(it)
        if key is None:
            return False
        ov_mode = "live" if kind == "fav" else kind
        return self.channel_ov.is_hidden(ov_mode, key)

    def _apply_filter(self):
        text = self.search.text().lower().strip()
        kind = "episode" if self.series_ctx else self.mode
        items = [it for it in self.all_items
                 if not self._channel_hidden(it, kind)]
        if text:
            filtered = [it for it in items
                        if text in self.channel_display_name(it).lower()]
        else:
            filtered = items
        filtered = self._sorted(filtered)
        self.list_model.set_items(filtered, kind)
        self._set_status(f"{len(filtered)} {self.LABELS[kind]}")
        if kind == "fav" and not self.all_items:
            self._set_status("No favorites yet - right-click a channel in TV to add one.")
        elif kind == "history" and not self.all_items:
            self._set_status("No watch history yet.")

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
        name = self.channel_display_name(it)
        self.d_title.setText(name)
        self.d_logo.setPixmap(QPixmap())
        self.d_logo.setStyleSheet(self.PLACEHOLDER_LOGO_STYLE)
        self.d_logo.setText(name.strip()[:1].upper())
        url = it.get("stream_icon") or it.get("cover")
        if url:
            self.logos.get(url, self._set_detail_logo)

        if self.mode == "rec":
            if it.get("_kind") == "recjob":
                status = {"recording": "Recording now",
                          "scheduled": "Scheduled",
                          "done": "Finished", "failed": "Failed",
                          "cancelled": "Cancelled"}.get(it.get("_status"), "")
                err = it.get("_error")
                self.d_meta.setText(f"{status} - {err}" if err else status)
            else:
                try:
                    mtime = datetime.fromtimestamp(
                        os.stat(it["_path"]).st_mtime).strftime("%Y-%m-%d %H:%M")
                except OSError:
                    mtime = "?"
                self.d_meta.setText(
                    f"Recording * {format_size(it.get('_size') or 0)} * {mtime}")
            return

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
            days = self._timeshift_days(it)
            self.d_meta.setText(
                f"Live channel * ⏪ Catch-up: {days} "
                f"day{'s' if days != 1 else ''}" if days else "Live channel")
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

    @property
    def PLACEHOLDER_LOGO_STYLE(self):
        return (f"background:{P['sel']}; border-radius:18px; "
                "font-size:30px; font-weight:700;")

    def _set_detail_logo(self, pm):
        # No clipping box: draw the logo keep-aspect on a fully transparent
        # tile and drop the grey placeholder background, so wide/transparent
        # logos aren't cropped by an ugly frame.
        tile = QPixmap(84, 84)
        tile.fill(Qt.GlobalColor.transparent)
        p = QPainter(tile)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        s = pm.scaled(84, 84, Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap((84 - s.width()) // 2, (84 - s.height()) // 2, s)
        p.end()
        self.d_logo.setStyleSheet("background:transparent;")
        self.d_logo.setText("")
        self.d_logo.setPixmap(tile)

    def _clear_epg_rows(self):
        while self.epg_lay.count() > 1:
            w = self.epg_lay.takeAt(0).widget()
            if w:
                w.deleteLater()

    def _epg_note(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color:{P['muted2']}; font-size:12px;")
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
        self._set_status("Loading episodes...")

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
                             self._item_key(it), "live", item=it)

    def play(self, player=None, external=False):
        it = self.list_model.item_at(self.listw.currentIndex().row())
        self.play_item(it, player, external)

    def play_item(self, it, player=None, external=False):
        """Plays a specific item. 'Play in VLC' is always a one-off external
        launch - it must never change the default player or take the
        embedded mini player out of action for subsequent plays."""
        if not it:
            return
        if self.mode == "series" and not self.series_ctx:
            self._enter_series(it)
            return
        if self.mode == "rec":
            # Local files: recordings, or a job that has started writing one
            # (an in-progress recording is watchable while it records).
            path = it.get("_path")
            if not path or not os.path.exists(path):
                return
            title = it.get("name") or "Recording"
            if external or player == "vlc":
                launch_player(player or "mpv", path, title, self)
                return
            self._start_playback(path, title, None, path, "recording",
                                 record=False)
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

        if external or player == "vlc":
            launch_player(player or "mpv", url, title, self)
            if self.mode != "history":
                self.history.add(url, title, icon, key, kind)
            return

        self._start_playback(url, title, icon, key, kind,
                             record=self.mode != "history", item=it)

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
        if self.player.current_url == url:
            # Already playing this exact stream (e.g. the preview timer
            # firing right after an explicit play/zap) - reloading it would
            # reconnect to the provider for nothing.
            return
        if not self._guard_stream_switch(url, title):
            return
        self.stream_error.hide()
        self._playing_key = self._item_key(it)
        self._playing_group = "live"
        self._playing_item = it
        self._sync_player_buttons()
        self.listw.viewport().update()
        self.setWindowTitle(title or self._base_title)
        self._set_status(f"Playing: {title}")
        self.rec.finish_all_inplayer("channel changed")
        self.player.show()
        self.player.set_overlay_info(title)
        if self.player.play(url, title):
            self.wake.acquire(f"Playing {title}")

    def playback_mode(self):
        default = "embedded" if self.player else "window"
        mode = self.settings.value("playback_mode", default)
        if mode == "embedded" and not self.player:
            mode = "window"
        return mode

    def _start_playback(self, url, title, icon_url, key, kind, record=True,
                        item=None):
        if not self._guard_stream_switch(url, title):
            return
        if record and kind:
            self.history.add(url, title, icon_url, key, kind)
        self.stream_error.hide()
        # The live channel item behind this playback (None for movies etc.):
        # drives the in-player Timeshift/Record buttons.
        self._playing_item = item if kind == "live" else None
        self._sync_player_buttons()
        self._playing_key = key
        # Which list kind this key belongs to, so the playing highlight only
        # lights up in the matching list (a movie and a channel can share the
        # same numeric id).
        self._playing_group = {"live": "live", "movie": "vod",
                               "episode": "episode",
                               "recording": "rec"}.get(kind)
        self.listw.viewport().update()      # repaint the playing highlight
        self.setWindowTitle(title or self._base_title)
        self._set_status(f"Playing: {title}")
        mode = self.playback_mode()
        print(f"[dopeIPTV] Playing via mode={mode} "
              f"(embedded pane: {'yes' if self.player else 'no'})",
              file=sys.stderr)
        if mode == "embedded" and self.player:
            # An in-player recording taps the *current* stream - switching
            # away would silently record the wrong channel into the file.
            self.rec.finish_all_inplayer("channel changed")
            self.player.show()
            self.player.set_overlay_info(title)
            if self.player.play(url, title):
                self.wake.acquire(f"Playing {title}")
            else:
                self.player.hide()
                launch_player("mpv", url, title, self)
        elif mode == "window":
            # A single reused mpv window (zap-able). python-mpv drives it
            # in-process; without python-mpv, fall back to controlling a
            # separate mpv process over its IPC socket - still reused, not a
            # fresh window each time.
            if self.mpv_window:
                if self.mpv_window.play(url, title):
                    self.wake.acquire(f"Playing {title}")
                else:
                    launch_player("mpv", url, title, self)
            else:
                run_async(self.pool, lambda: self.mpv.load(url, title),
                         lambda ok: None if ok else self._player_missing("mpv"))
        else:
            launch_player(self.settings.value("player", "mpv"),
                          url, title, self)

    def _player_missing(self, name):
        QMessageBox.warning(self, "Player not found",
                           f"{name} was not found. Install it and try again.")

    def _set_status(self, text, error=False):
        """Status label under the list: red+bold for errors so they stand
        out, normal grey otherwise. New activity always replaces old errors,
        so a failed channel doesn't stay on screen after a working one."""
        self.count_lbl.setStyleSheet(
            f"color:{P['error']}; font-size:11px; font-weight:600;" if error
            else f"color:{P['muted3']}; font-size:11px;")
        self.count_lbl.setText(text)

    def _playback_error(self, msg):
        """A stream failed to play (dead/unreachable channel etc.)."""
        self.rec.finish_all_inplayer("stream error")
        self.wake.release()
        if self.player:
            # let a re-selection of the same channel retry the stream
            self.player.current_url = None
        self._set_status(f"Stream error: {msg}", error=True)
        if self._player_fs and self.player:
            self.player.set_overlay_info(f"Stream error: {msg}")
        else:
            self.stream_error.setText(f"Stream error: {msg}")
            self.stream_error.show()
        if self.player:
            self.player.title_lbl.setText("")

    def _zap(self, direction):
        # Works in TV/Favorites, Movies, and inside a series' episode list.
        # A list of *series* is excluded: 'playing' the next series would
        # open its episode list rather than actually play anything.
        if self.mode not in ("live", "fav", "vod", "series", "history", "rec"):
            return
        if self.mode == "series" and not self.series_ctx:
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
        # Deliberately do NOT change the selection: right-clicking another
        # channel must not switch away from the one that's playing. All menu
        # actions target the clicked item directly; only left-click selects.
        it = self.list_model.item_at(idx.row())
        if not it:
            return
        if self.mode == "rec":
            self._rec_context_menu(pos, it)
            return
        m = QMenu(self)
        m.addAction("Play in mpv", lambda: self.play_item(it, "mpv"))
        m.addAction("Play in VLC", lambda: self.play_item(it, "vlc"))
        ext = m.addMenu("Open externally")
        ext.addAction("mpv", lambda: self.play_item(it, "mpv", external=True))
        ext.addAction("VLC", lambda: self.play_item(it, "vlc", external=True))
        ext.addAction("mpv + VLC (both)", lambda: self._open_external_both(it))
        if not (self.mode == "series" and not self.series_ctx):
            m.addAction("Cast to Chromecast...",
                       lambda: self._open_cast_dialog(it))
        if self.mode in ("live", "fav") and it.get("stream_id") is not None:
            if self._timeshift_days(it):
                m.addSeparator()
                self._build_timeshift_menu(
                    m.addMenu("Timeshift / catch-up"), it)
            m.addSeparator()
            self._build_record_menu(m.addMenu("Record"), it)
        if self.mode in ("live", "fav") and it.get("stream_id") is not None:
            m.addSeparator()
            fav_menu = m.addMenu("Add to favorites group")
            for g in self.favs.group_names():
                fav_menu.addAction(g, lambda g=g: self._add_fav(g, it))
            if self.favs.group_names():
                fav_menu.addSeparator()
            fav_menu.addAction("New group...", lambda: self._add_fav(None, it))
            if self.mode == "fav":
                m.addAction("Remove from favorites", lambda: self._remove_fav(it))
        if self.mode in ("live", "vod", "series") and not self.series_ctx:
            ov_mode = self.mode
            key = self._item_key(it)
            m.addSeparator()
            m.addAction("Rename channel..." if ov_mode == "live"
                        else "Rename...",
                        lambda: self._rename_channel(ov_mode, key, it))
            m.addAction("Hide channel" if ov_mode == "live" else "Hide",
                        lambda: self._hide_channel(ov_mode, key))
            if self.channel_ov.get(ov_mode, key):
                m.addAction("Reset this channel's customizations",
                            lambda: self._reset_channel(ov_mode, key))
            if self.channel_ov.has_overrides(ov_mode):
                m.addAction("Restore default channels...",
                            lambda: self._restore_default_channels(ov_mode))
        if self.mode == "history":
            m.addSeparator()
            m.addAction("Remove selected from history",
                       lambda: self._remove_history_selected(it))
        if not (self.mode == "series" and not self.series_ctx) and self.mode != "history":
            url, _ = self._stream_for(it)
            if url:
                m.addSeparator()
                m.addAction("Copy stream URL",
                           lambda: QApplication.clipboard().setText(url))
        m.exec(self.listw.viewport().mapToGlobal(pos))

    # -- channel customizations (rename/hide with restore) -----------------------
    def _rename_channel(self, mode, key, it):
        if key is None:
            return
        current = self.channel_ov.display_name(
            mode, key, it.get("name") or it.get("title") or "")
        name, ok = QInputDialog.getText(self, "Rename channel",
                                        "New name:", text=current)
        if ok:
            self.channel_ov.update(mode, key, name=name.strip())
            self._apply_filter()

    def _hide_channel(self, mode, key):
        if key is None:
            return
        self.channel_ov.update(mode, key, hidden=True)
        self._apply_filter()

    def _reset_channel(self, mode, key):
        self.channel_ov.update(mode, key, name="", hidden=False)
        self._apply_filter()

    def _restore_default_channels(self, mode):
        if QMessageBox.question(
                self, "Restore default channels",
                "Undo all channel renames and hides for this section and "
                "go back to the provider's original list?") \
                == QMessageBox.StandardButton.Yes:
            self.channel_ov.reset_mode(mode)
            self._apply_filter()

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

    def _remove_history_selected(self, clicked_item=None):
        """Removes every selected history entry (falls back to the
        right-clicked one when nothing is selected)."""
        items = [self.list_model.item_at(ix.row())
                 for ix in self.listw.selectionModel().selectedRows()]
        items = [it for it in items if it]
        if not items and clicked_item:
            items = [clicked_item]
        for it in items:
            self.history.remove(it.get("_key"), it.get("_kind"))
        if items:
            self._load_items(None)

    def _delete_pressed(self):
        if self.mode == "history":
            self._remove_history_selected()
        elif self.mode == "rec":
            self._delete_recordings_selected()

    # -- recordings ---------------------------------------------------------------
    def _job_item(self, j):
        """A recording job (active/scheduled/finished) as a list item."""
        label = {"recording": "● REC", "scheduled": "Scheduled",
                 "done": "Done", "failed": "Failed",
                 "cancelled": "Cancelled"}.get(j["status"], j["status"])
        start = datetime.fromtimestamp(j["start"]).strftime("%a %d %b %H:%M")
        stop = ("until stopped" if j.get("stop") is None
                else datetime.fromtimestamp(j["stop"]).strftime("%H:%M"))
        return {"name": f"[{label}] {j['title']}  ({start} – {stop})",
                "_job": j["id"], "_key": f"job:{j['id']}",
                "_kind": "recjob", "_status": j["status"],
                "_error": j.get("error") or "", "_path": j.get("path") or ""}

    def _recordings_changed(self):
        """A recording started/stopped/failed - refresh the view and the
        persistent ● REC indicator."""
        n = self.rec.active_count()
        self.rec_indicator.setText(f"● REC ({n})" if n > 1 else "● REC")
        self.rec_indicator.setVisible(n > 0)
        if self.player:
            # the in-player REC button doubles as a live indicator
            label = "● REC" if n else "REC"
            self.player.rec_btn.setText(label)
            self.player.fs_rec_btn.setText(label)
        if self.mode == "rec":
            cur = self.cat_list.currentItem()
            self._load_items(cur.data(Qt.ItemDataRole.UserRole) if cur else None)
        elif n:
            self._set_status(
                f"● Recording {n} stream{'s' if n > 1 else ''}...")

    def _on_recording_stopped(self, title, reason):
        """A recording ended - make it clearly visible, especially when it
        wasn't a planned stop."""
        abnormal = reason not in ("finished", "stopped")
        self._set_status(f"● Recording stopped: {title} ({reason})",
                         error=abnormal)
        if self._player_fs and self.player:
            self.player.set_overlay_info(
                f"Recording stopped: {title} ({reason})")

    def _guard_stream_switch(self, url, title):
        """Called before starting a new provider stream while something is
        recording. Most IPTV accounts allow a single stream, so switching
        can kill the recording. Returns False when the user backs out."""
        if not url or not str(url).startswith("http"):
            return True          # local files don't touch the provider
        active = [j for j in self.rec.jobs if j["status"] == "recording"]
        if not active:
            return True
        inplayer = [j for j in active if j.get("inplayer")]
        if not inplayer and getattr(self, "_multi_stream_ok", False):
            # The user already said their account handles several streams.
            # That only waives the extra-connection warning - an in-player
            # recording still dies with the stream it rides, so that dialog
            # is never skipped.
            return True
        if inplayer:
            if self.player and url == self.player.current_url:
                return True      # same stream - recording is unaffected
            j = inplayer[0]
            box = QMessageBox(self)
            box.setWindowTitle("Recording in progress")
            box.setText(f"'{j['title']}' is being recorded from the stream "
                        f"you're watching.\n\nSwitching to '{title}' will "
                        "STOP that recording - unless you continue it over "
                        "a second connection (needs a multi-stream account).")
            stop_btn = box.addButton("Stop recording & switch",
                                     QMessageBox.ButtonRole.AcceptRole)
            cont_btn = (box.addButton(
                "Switch & keep recording (new connection)",
                QMessageBox.ButtonRole.ActionRole) if j.get("url") else None)
            box.addButton("Keep watching (recording continues)",
                          QMessageBox.ButtonRole.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is stop_btn:
                self.rec.finish_all_inplayer("stopped")
                return True
            if cont_btn is not None and clicked is cont_btn:
                # Hand the recording over to its own ffmpeg connection and
                # keep going into a continuation file.
                self.rec.finish_all_inplayer("continued over a new connection")
                self.rec.add_job(j["url"], f"{j['title']} (cont.)",
                                 time.time(), j.get("stop"))
                self._multi_stream_ok = True
                return True
            return False
        # Separate-connection (ffmpeg) recording running
        j = active[0]
        box = QMessageBox(self)
        box.setWindowTitle("Recording in progress")
        box.setText(f"'{j['title']}' is being recorded over its own "
                    f"connection.\n\nIf your account only allows one stream "
                    f"at a time, starting '{title}' can kill that "
                    "recording.")
        watch_btn = box.addButton("Watch the recorded channel (no new stream)",
                                  QMessageBox.ButtonRole.AcceptRole)
        anyway_btn = box.addButton("Play anyway (I have multiple streams)",
                                   QMessageBox.ButtonRole.ActionRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is watch_btn:
            self._watch_recording_file(j)
            return False
        if clicked is anyway_btn:
            # remember for this session - the user knows their stream limit
            self._multi_stream_ok = True
            return True
        return False

    def _watch_recording_file(self, j):
        """Plays the growing file of an active recording - same data, no
        extra provider connection."""
        path = j.get("path")
        if not path or not os.path.exists(path):
            QMessageBox.information(
                self, "Recording",
                "The recording file hasn't been created yet - try again in "
                "a few seconds.")
            return
        self._start_playback(path, f"{j['title']} (recording)", None, path,
                             "recording", record=False)

    def _rec_indicator_menu(self):
        m = QMenu(self)
        active = [j for j in self.rec.jobs if j["status"] == "recording"]
        for j in active:
            since = datetime.fromtimestamp(j["start"]).strftime("%H:%M")
            m.addAction(f"Stop recording: {j['title']} (since {since})",
                        lambda jid=j["id"]: self.rec.cancel(jid))
        if active:
            m.addSeparator()
        m.addAction("Open Recordings", lambda: self.switch_mode("rec"))
        m.exec(self.rec_indicator.mapToGlobal(
            self.rec_indicator.rect().bottomLeft()))

    def _sync_player_buttons(self):
        """Shows the in-player ⏪ (timeshift) and REC buttons whenever the
        playing stream is a live channel that supports them - so both are
        reachable from the video player itself, windowed or fullscreen."""
        if not self.player:
            return
        it = self._playing_item
        live = bool(it) and it.get("stream_id") is not None
        ts = live and self._timeshift_days(it) > 0
        for b in (self.player.ts_btn, self.player.fs_ts_btn):
            b.setVisible(ts)
        for b in (self.player.rec_btn, self.player.fs_rec_btn):
            b.setVisible(live)

    def _player_timeshift_menu(self, anchor):
        it = self._playing_item
        if not it or not self._timeshift_days(it):
            return
        m = QMenu(self)
        self._build_timeshift_menu(m, it)
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _player_record_menu(self, anchor):
        it = self._playing_item
        if not it or it.get("stream_id") is None:
            return
        m = QMenu(self)
        self._build_record_menu(m, it)
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _recorder_ready(self):
        if self.rec.recorder()[1]:
            return True
        QMessageBox.warning(
            self, "Recording",
            "Recording needs ffmpeg (recommended) or mpv on the PATH.\n\n"
            "Install ffmpeg, e.g.:  sudo apt install ffmpeg")
        return False

    def _build_record_menu(self, rec_menu, it):
        active = [j for j in self.rec.jobs if j["status"] == "recording"]
        for j in active:
            rec_menu.addAction(f"■ Stop recording: {j['title']}",
                               lambda jid=j["id"]: self.rec.cancel(jid))
        if active:
            rec_menu.addSeparator()
        rec_menu.addAction("Record now - until stopped",
                           lambda: self._record_now(it, None))
        for label, mins in (("Record now - 30 min", 30),
                            ("Record now - 1 hour", 60),
                            ("Record now - 2 hours", 120),
                            ("Record now - 4 hours", 240)):
            rec_menu.addAction(label,
                               lambda mins=mins: self._record_now(it, mins))
        rec_menu.addSeparator()
        rec_menu.addAction("Schedule recording...",
                           lambda: self._schedule_recording(it))
        cap_menu = rec_menu.addMenu("Size limit (this session)")
        current = self.rec.session_cap
        for label, cap in (("From Settings", None), ("No limit", 0),
                           ("250 MB", 250 * 10**6), ("500 MB", 500 * 10**6),
                           ("1 GB", 10**9), ("2 GB", 2 * 10**9),
                           ("5 GB", 5 * 10**9), ("10 GB", 10 * 10**9)):
            act = cap_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(cap == current)
            act.triggered.connect(
                lambda _c, cap=cap: setattr(self.rec, "session_cap", cap))

    def _record_now(self, it, minutes):
        """minutes=None records open-ended, until stopped via the ● REC
        indicator or the Recordings section.

        When the channel to record is the one already playing in the
        embedded player, the recording taps that player's own stream
        (mpv stream-record) - no second connection to the provider, which
        matters because most IPTV accounts allow only one stream at a
        time. Recording a *different* channel (or a scheduled job firing
        later) necessarily opens its own connection."""
        if it.get("stream_id") is None:
            return
        title = self.channel_display_name(it)
        now = time.time()
        stop_ts = None if minutes is None else now + minutes * 60
        length = "until stopped" if minutes is None else f"for {minutes} min"

        watching_this = (self.player is not None
                         and self.player.isVisible()
                         and self.playback_mode() == "embedded"
                         and self._playing_group == "live"
                         and self._playing_key == self._item_key(it))
        if watching_this:
            try:
                path = self.rec.build_path(title)
            except OSError as e:
                QMessageBox.warning(self, "Recording", str(e))
                return
            if self.player.start_stream_record(path):
                self.rec.add_inplayer_job(
                    title, path, stop_ts,
                    url=self.client.live_url(it["stream_id"], "ts"))
                self._set_status(f"● Recording {title} {length} - capturing "
                                 "the stream you're watching (no extra "
                                 "connection)")
                return
            # stream-record refused (no mpv core?) - fall through to ffmpeg

        if not self._recorder_ready():
            return
        url = self.client.live_url(it["stream_id"], "ts")
        self.rec.add_job(url, title, now, stop_ts)
        self._set_status(f"● Recording {title} {length} "
                         f"→ {self.rec.directory()}")

    def _schedule_recording(self, it):
        if not self._recorder_ready() or it.get("stream_id") is None:
            return
        d = QDialog(self)
        d.setWindowTitle("Schedule recording")
        d.setMinimumWidth(380)
        f = QFormLayout(d)
        f.setSpacing(10)
        name_edit = QLineEdit(self.channel_display_name(it))
        start_edit = QDateTimeEdit(QDateTime.currentDateTime())
        start_edit.setCalendarPopup(True)
        start_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        stop_edit = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        stop_edit.setCalendarPopup(True)
        stop_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        folder_box = QComboBox()
        folder_box.addItem("(Recordings folder)", "")
        for rel in self.rec.folders():
            folder_box.addItem(rel, rel)
        f.addRow("Name", name_edit)
        f.addRow("Start", start_edit)
        f.addRow("Stop", stop_edit)
        f.addRow("Save in", folder_box)
        hint = QLabel(f"Saved under {self.rec.directory()} - change the "
                      "location in Settings → Recording. The app must be "
                      "running when the recording starts.")
        hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        hint.setWordWrap(True)
        f.addRow(hint)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        f.addRow(bb)
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        start_ts = start_edit.dateTime().toSecsSinceEpoch()
        stop_ts = stop_edit.dateTime().toSecsSinceEpoch()
        if stop_ts <= start_ts or stop_ts <= time.time():
            QMessageBox.warning(self, "Schedule recording",
                                "The stop time must be in the future and "
                                "after the start time.")
            return
        url = self.client.live_url(it["stream_id"], "ts")
        title = name_edit.text().strip() or self.channel_display_name(it)
        self.rec.add_job(url, title, start_ts, stop_ts,
                         folder_box.currentData())
        when = datetime.fromtimestamp(start_ts).strftime("%a %d %b %H:%M")
        self._set_status(f"Recording of {title} scheduled for {when}")

    def _selected_recordings(self, clicked_item=None):
        items = [self.list_model.item_at(ix.row())
                 for ix in self.listw.selectionModel().selectedRows()]
        items = [it for it in items if it and it.get("_path")
                 and it.get("_kind") == "recording"]
        if not items and clicked_item and clicked_item.get("_path") \
                and clicked_item.get("_kind") == "recording":
            items = [clicked_item]
        return items

    def _remove_jobs_selected(self, clicked_item=None):
        """Removes every selected job row from Active & scheduled: finished
        ones are dropped, scheduled ones cancelled first; recordings that
        are still running are skipped (stop them explicitly)."""
        items = [self.list_model.item_at(ix.row())
                 for ix in self.listw.selectionModel().selectedRows()]
        items = [it for it in items if it and it.get("_job")]
        if not items and clicked_item and clicked_item.get("_job"):
            items = [clicked_item]
        for it in items:
            if it.get("_status") == "recording":
                continue
            if it.get("_status") == "scheduled":
                self.rec.cancel(it["_job"])
            self.rec.remove_job(it["_job"])

    def _delete_recordings_selected(self, clicked_item=None):
        cur = self.cat_list.currentItem()
        if cur and cur.data(Qt.ItemDataRole.UserRole) == "__jobs__":
            self._remove_jobs_selected(clicked_item)
            return
        items = self._selected_recordings(clicked_item)
        if not items:
            return
        what = (f"{len(items)} recordings" if len(items) > 1
                else f"'{items[0]['name']}'")
        if QMessageBox.question(self, "Delete recording",
                                f"Delete {what} from disk?") \
                != QMessageBox.StandardButton.Yes:
            return
        for it in items:
            try:
                os.remove(it["_path"])
                self.rec.prune_path(it["_path"])   # drop its job entry too
            except OSError as e:
                self._set_status(f"Could not delete: {e}", error=True)
        cur = self.cat_list.currentItem()
        self._load_items(cur.data(Qt.ItemDataRole.UserRole) if cur else None)

    def _rename_recording(self, it):
        path = it.get("_path")
        if not path:
            return
        name, ok = QInputDialog.getText(self, "Rename recording",
                                        "New name:", text=it.get("name", ""))
        name = safe_filename(name.strip()) if ok and name.strip() else ""
        if not name:
            return
        new_path = os.path.join(os.path.dirname(path),
                                name + os.path.splitext(path)[1])
        try:
            os.rename(path, new_path)
        except OSError as e:
            QMessageBox.warning(self, "Rename recording", str(e))
        cur = self.cat_list.currentItem()
        self._load_items(cur.data(Qt.ItemDataRole.UserRole) if cur else None)

    def _move_recordings(self, items, folder):
        """Moves recordings into a subfolder ('' = the recordings root)."""
        target = os.path.join(self.rec.directory(), folder)
        try:
            os.makedirs(target, exist_ok=True)
            for it in items:
                shutil.move(it["_path"], os.path.join(
                    target, os.path.basename(it["_path"])))
        except OSError as e:
            QMessageBox.warning(self, "Move recording", str(e))
        self._load_categories()

    def _new_rec_folder(self, items=None):
        """Creates a subfolder; optionally moves recordings into it."""
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        name = safe_filename(name.strip()) if ok and name.strip() else ""
        if not name:
            return
        try:
            os.makedirs(os.path.join(self.rec.directory(), name),
                        exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "New folder", str(e))
            return
        if items:
            self._move_recordings(items, name)
        else:
            self._load_categories()

    # -- timeshift / catch-up -------------------------------------------------------
    @staticmethod
    def _timeshift_days(it):
        """Days of catch-up archive the provider keeps for this channel
        (0 = no timeshift). Xtream reports tv_archive/tv_archive_duration
        on each live stream."""
        try:
            if int(it.get("tv_archive") or 0):
                return int(it.get("tv_archive_duration") or 1) or 1
        except (TypeError, ValueError):
            pass
        return 0

    def _play_timeshift(self, it, back_min=None, prog=None):
        """Plays a channel's archive: either a specific (possibly old) EPG
        programme, or N minutes back from now. The chunk the server sends is
        finite, so the seek bar and skip buttons work like for a movie."""
        sid = it.get("stream_id")
        days = self._timeshift_days(it)
        if sid is None or not days:
            return
        now = time.time()
        if prog:
            start = prog["start_timestamp"]
            # the whole programme (+2 min margin for skewed provider clocks)
            duration_min = max(
                1, int((prog["stop_timestamp"] - start) // 60) + 2)
            what = prog.get("title") or "programme"
        else:
            start = now - (back_min or 30) * 60
            duration_min = max(1, int((now - start) // 60) + 1)
            what = None
        start = max(start, now - days * 86400)
        url = self.client.timeshift_url(
            sid, datetime.fromtimestamp(start), duration_min)
        name = self.channel_display_name(it)
        title = (f"{what} ({name}, timeshift)" if what
                 else f"{name} (timeshift)")
        self._start_playback(url, title, it.get("stream_icon"),
                             self._item_key(it), "live", record=False,
                             item=it)

    TIMESHIFT_STEPS = ((30, "Go back 30 minutes"), (60, "Go back 1 hour"),
                       (120, "Go back 2 hours"), (360, "Go back 6 hours"),
                       (720, "Go back 12 hours"), (1440, "Go back 1 day"),
                       (2880, "Go back 2 days"), (4320, "Go back 3 days"),
                       (7200, "Go back 5 days"), (10080, "Go back 7 days"))

    def _build_timeshift_menu(self, ts_menu, it):
        """Fills a Timeshift/catch-up menu for a channel: watch the current
        programme from the start, browse old programmes from the EPG, or
        jump back - with steps that scale with how deep the provider's
        archive actually is."""
        days = self._timeshift_days(it)
        prog = self.xmltv.current_programme(it)
        if prog:
            ts_menu.addAction(
                f"Watch '{prog['title']}' from the start",
                lambda: self._play_timeshift(it, prog=prog))
        ts_menu.addAction("Browse past programmes (EPG)...",
                          lambda: self._open_catchup_dialog(it))
        ts_menu.addSeparator()
        for mins, label in self.TIMESHIFT_STEPS:
            if mins > days * 1440:
                break
            ts_menu.addAction(
                label, lambda mins=mins: self._play_timeshift(
                    it, back_min=mins))
        note = ts_menu.addAction(
            f"Archive depth: {days} day{'s' if days != 1 else ''}")
        note.setEnabled(False)

    def _open_catchup_dialog(self, it):
        """Browse the channel's past programmes (from the EPG) and play one
        via timeshift. Uses the XMLTV guide first; falls back to the
        provider's full EPG table which sometimes reaches further back."""
        days = self._timeshift_days(it)
        if not days:
            return
        d = QDialog(self)
        d.setWindowTitle(f"Catch-up - {self.channel_display_name(it)}")
        d.setMinimumSize(480, 500)
        lay = QVBoxLayout(d)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)
        info = QLabel("Loading past programmes from the guide...")
        info.setWordWrap(True)
        lay.addWidget(info)
        lst = QListWidget()
        lay.addWidget(lst, 1)
        btns = QHBoxLayout()
        watch_btn = QPushButton("Watch", objectName="Primary")
        close_btn = QPushButton("Close")
        btns.addStretch()
        btns.addWidget(watch_btn)
        btns.addWidget(close_btn)
        lay.addLayout(btns)
        close_btn.clicked.connect(d.reject)

        def watch(_item=None):
            cur = lst.currentItem()
            p = cur.data(Qt.ItemDataRole.UserRole) if cur else None
            if p:
                self._play_timeshift(it, prog=p)
                d.accept()

        watch_btn.clicked.connect(watch)
        lst.itemDoubleClicked.connect(watch)

        def fetch():
            progs = self.xmltv.past_programmes(it, days)
            if progs or it.get("stream_id") is None:
                return progs
            # Fallback: the provider's own full table
            now = time.time()
            out = []
            for e in self.client.epg_table(it["stream_id"]):
                start, stop = epg_times(e)
                if not start or not stop:
                    continue
                start_ts, stop_ts = start.timestamp(), stop.timestamp()
                if stop_ts <= now and start_ts >= now - days * 86400:
                    out.append({"title": b64(e.get("title")) or "?",
                                "start_timestamp": int(start_ts),
                                "stop_timestamp": int(stop_ts)})
            out.sort(key=lambda p: p["start_timestamp"], reverse=True)
            return out

        def done(progs):
            if not progs:
                info.setText("The guide has no past programmes for this "
                             "channel - use 'Go back ...' instead.")
                return
            info.setText(f"{len(progs)} programmes - the provider archives "
                         f"{days} day{'s' if days != 1 else ''} back. "
                         "Double-click to watch.")
            last_day = None
            for p in progs:
                start = datetime.fromtimestamp(p["start_timestamp"])
                stop = datetime.fromtimestamp(p["stop_timestamp"])
                day = start.strftime("%A %d %B")
                if day != last_day:
                    last_day = day
                    head = QListWidgetItem(f"—  {day}  —")
                    head.setFlags(Qt.ItemFlag.NoItemFlags)
                    lst.addItem(head)
                row = QListWidgetItem(
                    f"{start.strftime('%H:%M')}–{stop.strftime('%H:%M')}   "
                    f"{p.get('title') or '?'}")
                row.setData(Qt.ItemDataRole.UserRole, p)
                lst.addItem(row)

        run_async(self.pool, fetch, done,
                  lambda e: info.setText(f"Could not load the guide: {e}"))
        d.exec()

    def _rec_context_menu(self, pos, it):
        m = QMenu(self)
        if it.get("_kind") == "recjob":
            status = it.get("_status")
            if it.get("_path"):
                m.addAction("Watch", lambda: self.play_item(it))
            if status == "recording":
                m.addAction("Stop recording",
                            lambda: self.rec.cancel(it["_job"]))
            elif status == "scheduled":
                m.addAction("Cancel scheduled recording",
                            lambda: self.rec.cancel(it["_job"]))
            else:
                m.addAction("Remove selected from list",
                            lambda: self._remove_jobs_selected(it))
            m.addSeparator()
            m.addAction("Clear all finished from list",
                        lambda: self.rec.clear_finished())
        else:
            items = self._selected_recordings(it)
            many = len(items) > 1
            m.addAction("Play in mpv", lambda: self.play_item(it, "mpv"))
            m.addAction("Play in VLC", lambda: self.play_item(it, "vlc"))
            m.addSeparator()
            m.addAction("Rename...", lambda: self._rename_recording(it))
            move = m.addMenu("Move to" if not many
                             else f"Move {len(items)} recordings to")
            move.addAction("(Recordings folder)",
                           lambda: self._move_recordings(items, ""))
            for rel in self.rec.folders():
                move.addAction(rel,
                               lambda rel=rel: self._move_recordings(items, rel))
            move.addSeparator()
            move.addAction("New folder...",
                           lambda: self._new_rec_folder(items))
            m.addAction("Delete" if not many
                        else f"Delete {len(items)} recordings",
                        lambda: self._delete_recordings_selected(it))
        m.addSeparator()
        m.addAction("New folder...", lambda: self._new_rec_folder())
        m.addAction("Change recordings folder...",
                    lambda: self._choose_rec_dir())
        m.exec(self.listw.viewport().mapToGlobal(pos))

    def _choose_rec_dir(self):
        d = QFileDialog.getExistingDirectory(
            self, "Choose recordings folder", self.rec.directory(),
            QFileDialog.Option.DontUseNativeDialog
            | QFileDialog.Option.ShowDirsOnly)
        if d:
            self.rec.set_directory(d)
            if self.mode == "rec":
                self._load_categories()

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
        # Pixel scrolling with a decent wheel step - IconMode otherwise
        # crawls compared to the list view.
        self.listw.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        step = (self.delegate.grid_size().height() // 2 if grid
                else self.delegate.row_h)
        self.listw.verticalScrollBar().setSingleStep(max(30, step))
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

    def _set_theme(self, theme, accent):
        """Persists and applies a theme + accent live: rebuilds the global
        stylesheet and repaints the palette-driven parts (channel list,
        status label, detail placeholder)."""
        self.settings.setValue("theme", theme)
        self.settings.setValue("accent", accent)
        apply_theme(self.settings)
        QApplication.instance().setStyleSheet(build_style())
        self.listw.viewport().update()
        self.count_lbl.setStyleSheet(f"color:{P['muted3']}; font-size:11px;")
        # Deliberately NOT re-running _show_detail here: it restarts the
        # preview and refetches EPG - a theme change must leave the playing
        # video completely untouched. Only restyle the placeholder tile.
        if self.d_logo.text():
            self.d_logo.setStyleSheet(self.PLACEHOLDER_LOGO_STYLE)

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
        d.setMinimumSize(620, 580)
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
        LANGS = [("", "Auto / provider default"), ("swe", "Swedish"),
                 ("eng", "English"), ("nor", "Norwegian"), ("dan", "Danish"),
                 ("fin", "Finnish"), ("ger", "German"), ("fre", "French"),
                 ("spa", "Spanish"), ("ita", "Italian"), ("por", "Portuguese"),
                 ("pol", "Polish"), ("ara", "Arabic"), ("tur", "Turkish")]
        alang_box = self._combo(LANGS, self.settings.value("audio_lang", ""))
        sub_box = self._combo(
            [("off", "Off"), ("auto", "On (player default)"),
             ("lang", "On - preferred language")],
            self.settings.value("sub_mode", "auto"))
        slang_box = self._combo(LANGS, self.settings.value("sub_lang", ""))
        aspect_box = self._combo(
            [("auto", "Auto"), ("16:9", "16:9"), ("4:3", "4:3"),
             ("2.35:1", "2.35:1"), ("stretch", "Stretch to window")],
            self.settings.value("aspect_mode", "auto"))
        buf_box = self._combo(
            [("1", "1 s"), ("3", "3 s"), ("5", "5 s"), ("10", "10 s"),
             ("30", "30 s")],
            str(self.settings.value("cache_secs", "10")))
        pf.addRow("Default player", player_box)
        pf.addRow("Playback mode (mpv)", mode_box)
        pf.addRow("Auto-play preview on selection", autoplay_box)
        pf.addRow("Live stream format", fmt_box)
        pf.addRow("Preferred audio language", alang_box)
        pf.addRow("Subtitles", sub_box)
        pf.addRow("Preferred subtitle language", slang_box)
        pf.addRow("Aspect ratio", aspect_box)
        pf.addRow("Network buffer", buf_box)
        mode_hint = QLabel("Embedded plays in the app. Reused mpv window keeps "
                           "one external window you can zap in (Ctrl+←/→). "
                           "External opens a fresh window each time.")
        mode_hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        mode_hint.setWordWrap(True)
        pf.addRow(mode_hint)
        if not self.player:
            reason = embedded_playback_reason() or "unknown reason"
            hint = QLabel(f"Embedded playback unavailable: {reason}")
            hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
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
        theme_box = self._combo(
            [(key, t["name"]) for key, t in THEMES.items()],
            self.settings.value("theme", "graphite"))
        accent_box = self._combo(
            [(key, a[0]) for key, a in ACCENTS.items()],
            self.settings.value("accent", "blue"))
        theme_box.currentIndexChanged.connect(
            lambda _i: self._set_theme(theme_box.currentData(),
                                       accent_box.currentData()))
        accent_box.currentIndexChanged.connect(
            lambda _i: self._set_theme(theme_box.currentData(),
                                       accent_box.currentData()))
        uf.addRow("List size", density_box)
        uf.addRow("Sort lists by", sort_box)
        uf.addRow("Theme", theme_box)
        uf.addRow("Accent color", accent_box)
        theme_hint = QLabel("Theme and accent apply immediately.")
        theme_hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        theme_hint.setWordWrap(True)
        uf.addRow(theme_hint)
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
        par_hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        par_hint.setWordWrap(True)
        parv.addWidget(par_hint)
        parv.addStretch()
        tabs.addTab(par_tab, "Parental")

        # ---- Recording tab ----
        rec_tab = QWidget()
        recv = QVBoxLayout(rec_tab)
        recv.setSpacing(10)
        rec_dir_lbl = QLabel(self.rec.directory())
        rec_dir_lbl.setWordWrap(True)
        recv.addWidget(QLabel("Recordings are saved in:"))
        recv.addWidget(rec_dir_lbl)
        rec_dir_btn = QPushButton("Choose folder...")

        def pick_rec_dir():
            # Qt's own dialog, NOT the native/portal one: the native picker
            # can open behind the window or arrive seconds late on some
            # Linux desktops.
            path = QFileDialog.getExistingDirectory(
                d, "Choose recordings folder", self.rec.directory(),
                QFileDialog.Option.DontUseNativeDialog
                | QFileDialog.Option.ShowDirsOnly)
            if path:
                self.rec.set_directory(path)
                rec_dir_lbl.setText(path)
                if self.mode == "rec":
                    self._load_categories()

        rec_dir_btn.clicked.connect(pick_rec_dir)
        recv.addWidget(rec_dir_btn)
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Stop a recording when the file reaches"))
        rec_max_edit = QLineEdit(str(self.settings.value("rec_max_value", "")))
        rec_max_edit.setPlaceholderText("no limit")
        rec_max_edit.setMaximumWidth(90)
        rec_max_unit = self._combo([("MB", "MB"), ("GB", "GB"), ("TB", "TB")],
                                   self.settings.value("rec_max_unit", "GB"))
        size_row.addWidget(rec_max_edit)
        size_row.addWidget(rec_max_unit)
        size_row.addStretch()
        recv.addLayout(size_row)
        rk, rexe = self.rec.recorder()
        rec_hint = QLabel(
            f"Recorder: {rk} ({rexe})" if rexe else
            "No recorder found - install ffmpeg (recommended) or mpv.")
        rec_hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        rec_hint.setWordWrap(True)
        recv.addWidget(rec_hint)
        rec_hint2 = QLabel("Right-click a TV channel → Record to record "
                           "immediately or on a start/stop timer. Scheduled "
                           "recordings need the app to be running when they "
                           "start. Manage files under Recordings in the "
                           "sidebar.")
        rec_hint2.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        rec_hint2.setWordWrap(True)
        recv.addWidget(rec_hint2)
        recv.addStretch()
        tabs.addTab(rec_tab, "Recording")

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
            self.settings.setValue("audio_lang", alang_box.currentData())
            self.settings.setValue("sub_mode", sub_box.currentData())
            self.settings.setValue("sub_lang", slang_box.currentData())
            self.settings.setValue("aspect_mode", aspect_box.currentData())
            self.settings.setValue("cache_secs", buf_box.currentData())
            try:
                val = float(rec_max_edit.text().replace(",", ".") or 0)
            except ValueError:
                val = 0
            self.settings.setValue("rec_max_value", val if val > 0 else "")
            self.settings.setValue("rec_max_unit", rec_max_unit.currentData())
            if self.player:
                self.player.apply_default_options()
            self._apply_view_settings()

    def show_about(self):
        QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> {VERSION}<br><br>"
            "An elegant IPTV client for Xtream Codes with EPG,<br>"
            "embedded playback, favorites and history.<br><br>"
            "Playback via mpv (embedded/window) or VLC.")

    # -- EPG refresh with progress -------------------------------------------------
    def _on_epg_progress(self, value):
        """Guide download progress (emitted from the worker thread)."""
        self.loading_bar.show()
        if value < 0:                       # server sent no content length
            self.loading_bar.setRange(0, 0)
        else:
            self.loading_bar.setRange(0, 100)
            self.loading_bar.setValue(value)

    def _epg_progress_finished(self):
        self.loading_bar.setRange(0, 0)     # back to indeterminate default
        self.loading_bar.hide()

    def _refresh_epg_clicked(self):
        """Explicit 'Refresh EPG': re-download the guide (with a progress
        bar), then re-fetch the selected channel's listings."""
        self.epg_refresh.setEnabled(False)
        self._clear_epg_rows()
        self._epg_note("Refreshing programme guide...")

        def done(_ok):
            self.epg_refresh.setEnabled(True)
            self._epg_progress_finished()
            self.list_model.refresh_all()   # update the Now-lines in the list
            self._request_epg()

        def fail(_msg):
            self.epg_refresh.setEnabled(True)
            self._epg_progress_finished()
            self._request_epg()

        run_async(self.pool, lambda: self.xmltv.ensure_loaded(force=True),
                  done, fail)

    def _error(self, msg):
        self.loading_bar.hide()
        self._set_status("Error: " + msg, error=True)

    def keyPressEvent(self, event):
        # In player fullscreen the list isn't visible, so plain Left/Right
        # can zap. Outside fullscreen the arrows keep their normal meaning
        # (list navigation, cursor movement) and Ctrl+Left/Right zap.
        if self._player_fs:
            if event.key() == Qt.Key.Key_Right:
                self._zap(1)
                return
            if event.key() == Qt.Key.Key_Left:
                self._zap(-1)
                return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self.wake.release()
        self.rec.shutdown()      # stop recorder processes, persist schedules
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
    settings = QSettings(ORG, ORG)
    apply_theme(settings)
    app.setStyleSheet(build_style())
    print(f"[dopeIPTV] Qt platform: {app.platformName()}", file=sys.stderr)
    if _libmpv is not None:
        reason = embedded_playback_reason()
        if reason:
            print(f"[dopeIPTV] Embedded playback disabled: {reason}",
                  file=sys.stderr)
        else:
            print("[dopeIPTV] Embedded playback: enabled", file=sys.stderr)
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
        offline = False
        try:
            candidate.authenticate()
            client = candidate
            # keep the legacy single-account keys in sync (used by the login
            # dialog's prefill and as a fallback for tooling)
            settings.setValue("server", pl["server"])
            settings.setValue("username", pl["username"])
            settings.setValue("password", pl["password"])
        except Exception as e:
            box = QMessageBox(QMessageBox.Icon.Warning, "Connection failed",
                              f"{pl['name']}: {e}\n\n"
                              "You can start anyway - content will load once "
                              "the server is reachable again (retry by "
                              "switching category, or manage playlists in "
                              "Settings) - or fix the playlist details now.",
                              parent=None)
            start_btn = box.addButton("Start anyway",
                                      QMessageBox.ButtonRole.AcceptRole)
            edit_btn = box.addButton("Edit playlist...",
                                     QMessageBox.ButtonRole.ActionRole)
            quit_btn = box.addButton("Quit", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(start_btn)
            box.exec()
            clicked = box.clickedButton()
            if clicked is quit_btn:
                return 0
            if clicked is edit_btn:
                dlg = PlaylistDialog(None, pl)
                if dlg.exec():
                    store.update(pl["id"], **dlg.values())
                continue
            client = candidate           # offline start
            offline = True

    w = MainWindow(client, settings, store)
    if offline:
        w.setWindowTitle(w.windowTitle() + "  (offline)")
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
