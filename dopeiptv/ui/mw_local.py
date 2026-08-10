"""Local-files section mixin for MainWindow (mode "local").

Browse and play videos from registered folders and from network shares the OS
has already mounted (a gvfs SMB mount on Linux or a /Volumes share on macOS is
an ordinary path - the same reasoning as open_local_video, so there is no
in-app SMB client to go wrong). Modeled on the Recordings section: roots in
the category column, files in the middle list, playback through the proven
local-file path with resume keyed on the file path.
"""
from __future__ import annotations

import json
import os
import sys
import time

import re

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QListWidgetItem

from ..core.log import log
from ..core.workers import run_async
from ..i18n import tr

# Everything after (and including) a year/quality/codec tag is release
# annotation, not title: "Movie.Name.2019.1080p.BluRay.x265-GRP" -> the
# title is what comes before the first tag.
_TAG = re.compile(
    r"(?ix)\b(19\d{2}|20\d{2}|2160p|1080p|720p|480p|4k|uhd|bluray|blu-ray"
    r"|bdrip|brrip|webrip|web-dl|webdl|hdtv|dvdrip|hdr(10)?|dv|x26[45]"
    r"|h\.?26[45]|hevc|avc|aac|ac3|eac3|dts|remux|proper|repack|extended"
    r"|unrated|swesub|multi(sub)?|nordic)\b.*$")


def clean_title(stem: str) -> tuple[str, str]:
    """("Movie Name", "2019") out of a release-style file name. The year is
    kept apart so a TMDB search can use it and the list can show it."""
    year = ""
    m = re.search(r"\b(19\d{2}|20\d{2})\b", stem)
    if m:
        year = m.group(1)
    t = _TAG.sub("", stem)
    t = re.sub(r"[._]+", " ", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" -–")
    return (t or stem, year)


# S01E02 / 1x02 style episode tags.
_EP = re.compile(r"(?i)\b[sS](\d{1,2})[eE](\d{1,3})\b|\b(\d{1,2})x(\d{2,3})\b")


def episode_info(stem: str) -> tuple[int, int, int, int] | None:
    """(season, episode, start, end) of the episode tag in *stem*, or None
    for something that doesn't look like an episode."""
    m = _EP.search(stem)
    if not m:
        return None
    if m.group(1) is not None:
        return int(m.group(1)), int(m.group(2)), m.start(), m.end()
    return int(m.group(3)), int(m.group(4)), m.start(), m.end()


def _pretty_gvfs(name: str) -> str:
    """A gvfs mount dir is named "smb-share:server=nas,share=video" - label it
    "nas/video". Unknown schemes keep their raw mount name."""
    if ":" in name:
        _scheme, _, rest = name.partition(":")
        kv = dict(p.partition("=")[::2] for p in rest.split(",") if "=" in p)
        host, share = kv.get("server"), kv.get("share")
        if host and share:
            return f"{host}/{share}"
        if host:
            return host
    return name


class _LocalFilesMixin:
    """MainWindow mixin: the Local files section."""

    # -- the roots (registered folders + OS mounts) --------------------------

    def _local_dirs(self) -> list[str]:
        try:
            v = json.loads(self.settings.value("local_dirs", "") or "[]")
        except ValueError:
            return []
        return [p for p in v if isinstance(p, str)] if isinstance(v, list) \
            else []

    def _save_local_dirs(self, dirs: list[str]) -> None:
        self.settings.setValue("local_dirs", json.dumps(dirs))

    @staticmethod
    def _mounted_roots() -> list[tuple[str, str]]:
        """(label, path) for network shares / volumes the OS has mounted.
        Best effort: a missing directory just contributes nothing."""
        out: list[tuple[str, str]] = []
        try:
            if sys.platform.startswith("linux"):
                # GNOME/KDE mount SMB/NFS through gvfs, one dir per mount.
                gvfs = f"/run/user/{os.getuid()}/gvfs"
                if os.path.isdir(gvfs):
                    for n in sorted(os.listdir(gvfs)):
                        p = os.path.join(gvfs, n)
                        if os.path.isdir(p):
                            out.append((_pretty_gvfs(n), p))
                # USB disks and fstab CIFS mounts land under /media/<user>.
                media = os.path.join("/media", os.environ.get("USER", ""))
                if os.path.isdir(media):
                    for n in sorted(os.listdir(media)):
                        p = os.path.join(media, n)
                        if os.path.ismount(p):
                            out.append((n, p))
            elif sys.platform == "darwin":
                for n in sorted(os.listdir("/Volumes")):
                    p = os.path.join("/Volumes", n)
                    # The boot volume is a symlink to / - not a share.
                    if (os.path.isdir(p) and not os.path.islink(p)
                            and os.path.ismount(p)):
                        out.append((n, p))
        except OSError as e:
            log.warning("mounted-share scan failed: %s", e)
        return out

    # -- category column -----------------------------------------------------

    def _load_local_categories(self) -> None:
        dirs = self._local_dirs()
        self.cat_list.blockSignals(True)
        for path in dirs:
            it = QListWidgetItem(os.path.basename(path.rstrip(os.sep))
                                 or path)
            it.setData(Qt.ItemDataRole.UserRole, path)
            it.setToolTip(path)
            self.cat_list.addItem(it)
        for label, path in self._mounted_roots():
            if path in dirs:
                continue
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, path)
            it.setToolTip(path)
            self.cat_list.addItem(it)
        add = QListWidgetItem(tr("local_add_folder"))
        add.setData(Qt.ItemDataRole.UserRole, "__add__")
        self.cat_list.addItem(add)
        self.cat_list.blockSignals(False)
        if self.cat_list.count() > 1:
            self._select_remembered_cat()
        else:
            # Nothing registered and nothing mounted: don't auto-select the
            # add row (that would pop the picker on every visit) - just say
            # what the + row is for.
            self._render_rows([], "rec", tr("local_add_hint"))

    def _local_add_folder(self) -> None:
        from PyQt6.QtWidgets import QFileDialog
        start = (self.settings.value("local_open_dir", "")
                 or os.path.expanduser("~"))
        path = QFileDialog.getExistingDirectory(
            self, tr("local_add_folder_title"), start,
            QFileDialog.Option.DontUseNativeDialog
            | QFileDialog.Option.ShowDirsOnly)
        dirs = self._local_dirs()
        if path and path not in dirs:
            self._save_local_dirs(dirs + [path])
            self._last_cat[self.mode] = path   # land on the new folder
        self.cat_list.blockSignals(True)
        self.cat_list.clear()
        self.cat_list.blockSignals(False)
        self._load_local_categories()

    def _local_remove_folder(self, path: str) -> None:
        """Forget a registered folder (nothing on disk is touched)."""
        self._save_local_dirs([d for d in self._local_dirs() if d != path])
        self._last_cat.pop(self.mode, None)
        self.cat_list.blockSignals(True)
        self.cat_list.clear()
        self.cat_list.blockSignals(False)
        self._load_local_categories()

    def _local_view(self) -> str:
        """"folders" (plain browsing) or "series" (the library view that
        groups episode-tagged files into series and seasons, Infuse-style)."""
        v = self.settings.value("local_view", "folders")
        return v if v in ("folders", "series") else "folders"

    def _local_toggle_view(self) -> None:
        self.settings.setValue(
            "local_view",
            "series" if self._local_view() == "folders" else "folders")
        self._sync_local_view_btn()
        self._local_ctx = None
        self._local_series = None
        self.back_btn.hide()
        self._load_local_items(self._current_cat)

    def _sync_local_view_btn(self) -> None:
        btn = getattr(self, "local_view_btn", None)
        if btn is None:
            return
        series = self._local_view() == "series"
        btn.setChecked(series)
        btn.setText(tr("local_view_series") if series
                    else tr("local_view_folders"))

    # -- settings-tab helpers ------------------------------------------------

    def _local_clear_library_cache(self) -> None:
        """Forget every scanned library (the walk starts fresh next visit).
        Nothing on disk beyond our own cache file is touched."""
        try:
            os.unlink(self._library_cache_path())
        except OSError:
            pass
        self._local_scan_done = {}
        self._local_series_index = {}
        self._local_collection_index = {}
        self._local_movies_rows = []

    def _local_clear_poster_cache(self) -> None:
        self.settings.remove("local_tmdb_posters_v2")

    def _local_clear_thumbs(self) -> None:
        from ..core.workers import clear_directory, default_image_cache_dir
        try:
            clear_directory(default_image_cache_dir("thumbs"))
        except OSError as e:
            log.warning("thumbs clear failed: %s", e)

    # -- the middle list (directory browsing) --------------------------------

    def _load_local_items(self, root) -> None:
        if self._local_view() == "series" and isinstance(root, str) \
                and not getattr(self, "_local_ctx", None):
            if getattr(self, "_local_series", None):
                self._load_local_episodes()
            else:
                self._load_local_library(root)
            return
        base = getattr(self, "_local_ctx", None) or root
        if not base or not isinstance(base, str) or not os.path.isdir(base):
            self._render_rows([], "rec", tr("local_empty"))
            return
        try:
            names = sorted(os.listdir(base), key=str.lower)
        except OSError as e:
            log.warning("local browse failed for %s: %s", base, e)
            names = []
        dirs: list[dict] = []
        files: list[dict] = []
        cover = next((os.path.join(base, n) for n in names
                      if n.lower() in ("cover.jpg", "cover.jpeg",
                                       "cover.png", "folder.jpg",
                                       "folder.png", "front.jpg",
                                       "album.jpg")), "")
        for n in names:
            if n.startswith("."):
                continue
            p = os.path.join(base, n)
            if os.path.isdir(p):
                dirs.append({"name": n, "_kind": "localdir",
                             "_path": p, "_key": p,
                             "stream_icon": self._folder_tile()})
            elif n.lower().endswith(self.MEDIA_EXTS):
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                stem = os.path.splitext(n)[0]
                if n.lower().endswith(self.AUDIO_EXTS):
                    # A track keeps its name as written; the release-tag
                    # cleaner is for film files and mangles numbering.
                    title, year = stem, ""
                else:
                    title, year = clean_title(stem)
                files.append({"name": f"{title} ({year})" if year else title,
                              "_kind": "local", "_clean_title": title,
                              "_year": year, "_cast_url": p,
                              "_path": p, "_key": p, "_size": st.st_size,
                              "added": str(int(st.st_mtime)),
                              "stream_icon": cover if n.lower().endswith(
                                  self.AUDIO_EXTS) else "",
                              "_filename": stem})
        rows = self._search_filter(dirs) + self._search_filter(files)
        self._render_rows(rows, "rec", tr("local_empty"))
        self._local_resolve_posters([r for r in rows
                                     if r.get("_kind") == "local"])

    def _local_descend(self, path: str, from_key=None) -> None:
        self._local_push_nav(from_key or path)
        self._local_clear_search()
        self._local_ctx = path
        self.back_btn.setText("<-  " + tr("btn_back"))
        self.back_btn.show()
        self._load_local_items(self._current_cat)

    def _local_up(self) -> None:
        """One level up; at the category root the back button retires. The
        row that was drilled into is re-selected, so backing out lands where
        the user was instead of at the top of the list."""
        came_from = None
        stack = getattr(self, "_local_nav_stack", None)
        if stack:
            came_from = stack.pop()
        if getattr(self, "_local_series", None):
            self._local_series = None
            self.back_btn.hide()
            self._apply_library(getattr(self, "_local_series_index", {}),
                                getattr(self, "_local_movies_rows", []),
                                getattr(self, "_local_collection_index", {}))
            self._local_select_key(came_from)
            return
        root = self._current_cat if isinstance(self._current_cat, str) else ""
        cur = getattr(self, "_local_ctx", None)
        if not cur or not root \
                or os.path.normpath(cur) == os.path.normpath(root):
            self._local_ctx = None
        else:
            parent = os.path.dirname(cur.rstrip(os.sep))
            self._local_ctx = (None if os.path.normpath(parent)
                               == os.path.normpath(root) else parent)
        if self._local_ctx is None:
            self.back_btn.hide()
        self._load_local_items(root)
        self._local_select_key(came_from)

    def _local_push_nav(self, key) -> None:
        if not hasattr(self, "_local_nav_stack"):
            self._local_nav_stack = []
        self._local_nav_stack.append(key)

    def _local_clear_search(self) -> None:
        """Entering a folder drops an active search: the query matched the
        SHELF (e.g. the artist), and filtering the album's tracks by it
        rendered the folder empty."""
        sb = getattr(self, "search", None)
        if sb is not None and sb.text():
            sb.blockSignals(True)
            sb.clear()
            sb.blockSignals(False)

    def _local_select_key(self, key) -> None:
        model = getattr(self, "list_model", None)
        if not key or model is None:
            return
        try:
            for row in range(model.rowCount()):
                it = model.item_at(row)
                if it and it.get("_key") == key:
                    ix = model.index(row)
                    self.listw.setCurrentIndex(ix)
                    self.listw.scrollTo(ix)
                    return
        except Exception:
            pass

    # -- the library view (Infuse-style series grouping) ----------------------

    def _local_file_row(self, p: str, stem: str | None = None,
                        stat: bool = True) -> dict | None:
        """A playable row for the file. stat=False skips the size/mtime
        lookup - over SMB that is a network round trip per file, and the
        library scan does not need either to show the title."""
        size, mtime = 0, 0
        if stat:
            try:
                st = os.stat(p)
                size, mtime = st.st_size, int(st.st_mtime)
            except OSError:
                return None
        stem = stem or os.path.splitext(os.path.basename(p))[0]
        if p.lower().endswith(self.AUDIO_EXTS):
            # A track name is already the title - cleaning it mangles
            # numbering and punctuation ("01. Jail" -> "01 Jail").
            return {"name": stem, "_kind": "local", "_clean_title": "",
                    "_year": "", "_cast_url": p, "_path": p, "_key": p,
                    "_size": 0, "added": "0", "stream_icon": "",
                    "_filename": stem}
        title, year = clean_title(stem)
        return {"name": f"{title} ({year})" if year else title,
                "_kind": "local", "_clean_title": title, "_year": year,
                "_cast_url": p, "_path": p, "_key": p, "_size": size,
                "added": str(mtime),
                "stream_icon": "", "_filename": stem}

    # NAS housekeeping dirs that hold thumbnails/recycled files by the
    # thousand - walking them over SMB is pure cost.
    _SCAN_JUNK = {"@eadir", "#recycle", "$recycle.bin", "lost+found",
                  "system volume information", "@__thumb", ".appledouble"}

    def _load_local_library(self, root: str) -> None:
        """The library, built progressively: the walk runs in ~4 s slices on
        the worker pool and every slice renders what has been found so far,
        so a big SMB share fills the view as it goes instead of all at the
        end. Bounded by directory/file caps rather than wall time - the
        slices make time limits unnecessary."""
        if getattr(self, "_local_scan_active", False) \
                and getattr(self, "_local_scan_root", None) == root:
            # This very root is mid-walk: show what it has and let it keep
            # going. Restarting on every category click threw the progress
            # away each time.
            if not getattr(self, "_local_series", None):
                self._apply_library(
                    getattr(self, "_local_series_index", {}),
                    getattr(self, "_local_movies_rows", []),
                    getattr(self, "_local_collection_index", {}))
                self._set_status(self._local_scan_note())
            return
        self._local_scan_root = root
        self._local_scan_token = getattr(self, "_local_scan_token", 0) + 1
        token = self._local_scan_token
        cached = self._library_cache().get(root)
        if cached is not None and not (cached.get("series")
                                       or cached.get("movies")
                                       or cached.get("collections")
                                       or cached.get("albums")):
            cached = None
        if cached is not None:
            self._local_album_index = cached.get("albums") or {}
            self._local_cover_index = cached.get("covers") or {}
            self._apply_library(cached.get("series") or {},
                                cached.get("movies") or [],
                                cached.get("collections") or {})
            last = getattr(self, "_local_scan_done", {}).get(root, 0.0)
            if time.monotonic() - last < 120.0:
                return   # scanned moments ago: the cache render is current
        self._local_scan_active = True
        self._show_busy(tr("local_scanning"))
        self._local_pulse_start()
        log.info("library scan starting: %s", root)
        state = {"walker": os.walk(root), "series": {}, "collections": {},
                 "movies": [], "albums": {}, "dirs": 0, "files": 0,
                 "root": os.path.normpath(root)}
        self._local_scan_step(root, state, token, cached)

    def _local_scan_step(self, root: str, state: dict, token: int,
                         cached: dict | None) -> None:
        def job():
            import time
            t0 = time.monotonic()
            walker = state["walker"]
            while time.monotonic() - t0 < 4.0:
                if state["dirs"] >= 50000 or state["files"] >= 20000:
                    return True, True          # done, and cut short
                try:
                    dirpath, dirnames, names = next(walker)
                except StopIteration:
                    state["dircover_map"] = dict(state.get("dircover", {}))
                    state["covers"] = {
                        name: cov for name in state["collections"]
                        for d, cov in state.get("dircover", {}).items()
                        if d.startswith(
                            os.path.join(state["root"], name))}
                    return True, False
                state["dirs"] += 1
                state["at"] = dirpath
                dirnames[:] = [d for d in dirnames
                               if not d.startswith(".")
                               and d.lower() not in self._SCAN_JUNK]
                for n in names:
                    if n.lower() in ("cover.jpg", "cover.jpeg", "cover.png",
                                     "folder.jpg", "folder.png", "front.jpg",
                                     "album.jpg"):
                        state.setdefault("dircover", {})[dirpath] = \
                            os.path.join(dirpath, n)
                        break
                for n in sorted(names, key=str.lower):
                    if n.startswith(".") or not n.lower().endswith(
                            self.MEDIA_EXTS):
                        continue
                    state["files"] += 1
                    self._classify(state, os.path.join(dirpath, n))
            return False, False                # slice over, more to walk

        def done(result):
            if token != getattr(self, "_local_scan_token", 0) \
                    or self.mode != "local":
                self._local_pulse_stop()
                return
            finished, cut = result
            self._local_cover_index = state.get("covers", {})
            self._local_dircover = state.get("dircover_map",
                                             state.get("dircover", {}))
            series = state["series"]
            self._local_album_index = state.get("albums", {})
            drilled = (getattr(self, "_local_series", None)
                       or getattr(self, "_local_ctx", None))
            collections = state["collections"]
            movies = state["movies"]
            if not finished:
                # Mid-walk: render the UNION of the cached view and what the
                # walk has found so far. Rows the walk hasn't re-reached yet
                # stay (no vanishing), new files appear as they are found,
                # and files deleted on disk are dropped only when the walk
                # completes. Never re-render under a drilled-in user.
                if cached is not None:
                    ms = dict(cached.get("series") or {})
                    ms.update(series)
                    mc = dict(cached.get("collections") or {})
                    mc.update(collections)
                    seen = {r.get("_key") for r in movies}
                    mm = movies + [r for r in (cached.get("movies") or [])
                                   if r.get("_key") not in seen]
                else:
                    ms, mc, mm = series, collections, movies
                self._local_series_index = ms
                self._local_collection_index = mc
                self._local_movies_rows = mm
                self._local_album_index = state.get("albums", {})
                self._local_scan_state = state
                if not getattr(self, "_local_series", None) \
                        and not getattr(self, "_local_ctx", None):
                    self._apply_library(ms, mm, mc)
                else:
                    # Drilled in: the list is not ours to touch, but the
                    # progress note still is.
                    self._set_status(self._local_scan_note(state))
                self._show_busy(self._local_scan_note(state))
                # Persist the merged partial view every ~15 s, so quitting
                # or switching away mid-walk keeps everything found so far
                # instead of starting over from nothing.
                now = time.monotonic()
                if now - getattr(self, "_local_cache_saved", 0.0) > 15.0:
                    self._local_cache_saved = now
                    self._save_library_cache(root, series, movies,
                                             collections)
                self._local_scan_step(root, state, token, cached)
                return
            self._local_pulse_stop()
            self._local_scan_active = False
            self._local_scan_state = None
            self._hide_busy()
            done_at = dict(getattr(self, "_local_scan_done", {}))
            done_at[root] = time.monotonic()
            self._local_scan_done = done_at
            log.info("library scan %s: %d series, %d collections, "
                     "%d movies%s", root, len(series), len(collections),
                     len(movies),
                     " (walk cut short - dir/file cap hit)" if cut else "")
            self._save_library_cache(root, series, movies, collections)
            self._local_series_index = series
            self._local_collection_index = collections
            if drilled:
                return     # drilled in: the fresh index serves the way back
            if cached is not None \
                    and cached.get("series") == series \
                    and cached.get("collections") == collections \
                    and cached.get("movies") == movies:
                return     # nothing changed: the cache render stands
            self._apply_library(series, movies, collections)

        def fail(e):
            if token == getattr(self, "_local_scan_token", 0):
                self._local_pulse_stop()
                self._local_scan_active = False
                self._hide_busy()
                log.warning("library scan FAILED for %s: %r", root, e)
                if cached is None:
                    self._render_rows([], "rec", tr("local_empty"))

        run_async(self.pool, job, done, fail)

    def _classify(self, state: dict, p: str,
                  defer_audio: bool = True) -> None:
        if p.lower().endswith(self.AUDIO_EXTS):
            # Music is shelved by ALBUM - the folder that directly holds
            # the tracks - not as thousands of loose files.
            d = os.path.dirname(p)
            a = state.setdefault("albums", {}).setdefault(
                d, {"n": 0, "root": False})
            a["n"] += 1
            a["root"] = (os.path.normpath(d) == state["root"])
            return
        stem = os.path.splitext(os.path.basename(p))[0]
        se = episode_info(stem)
        if se:
            sname = clean_title(stem[:se[2]])[0].strip(" -–")
            if not sname:
                sname = os.path.basename(os.path.dirname(p))
            state["series"].setdefault(sname, []).append(
                [se[0], se[1], p, stem])
            return
        rel = os.path.relpath(os.path.normpath(p), state["root"])
        parts = rel.split(os.sep)
        row = self._local_file_row(p, stem, stat=False)
        if row is None:
            return
        anchored = bool(row.get("_year")) or bool(_TAG.search(stem))
        if anchored:
            state["movies"].append(row)
        elif len(parts) > 1:
            state["collections"].setdefault(parts[0], []).append(p)
        else:
            row["_home"] = True       # rendered under its own header
            state["movies"].append(row)

    def _album_rows(self) -> list[dict]:
        """One row per album - the folder that directly holds the tracks -
        with its own cover art and track count, the way a music library
        shelves things. Opening one browses that folder's tracks."""
        albums = getattr(self, "_local_album_index", {})
        if not albums:
            return []
        dircover = getattr(self, "_local_dircover", {})
        rows: list[dict] = [{"_header": tr("local_music")}]
        for d in sorted(albums, key=lambda x: os.path.basename(x).lower()):
            info = albums[d]
            n = info.get("n", 0) if isinstance(info, dict) else int(info or 0)
            name = os.path.basename(d.rstrip(os.sep)) or d
            rows.append({"name": name, "_kind": "localalbum",
                         "_path": d, "_key": f"localalbum::{d}",
                         "stream_icon": dircover.get(d)
                         or self._album_cover(d) or self._folder_tile(),
                         "_desc": tr("local_tracks", n=n)})
        return rows

    @staticmethod
    def _album_cover(d: str) -> str:
        for n in ("cover.jpg", "cover.jpeg", "cover.png", "folder.jpg",
                  "folder.png", "front.jpg", "album.jpg"):
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
        return ""

    def _folder_tile(self) -> str:
        """A drawn folder icon as an image file the row art loader can
        serve - the delegate otherwise painted its letter placeholder AND
        the name carried a folder emoji, which read as two ugly icons."""
        try:
            from ..core.workers import default_image_cache_dir
            from .theme import P
            color = P["text2"].lstrip("#")
            path = default_image_cache_dir("thumbs") / f"folder-{color}.png"
            if not path.exists():
                os.makedirs(path.parent, exist_ok=True)
                self._action_pixmap("folder", 128, P["text2"]).save(
                    str(path), "PNG")
            return str(path)
        except Exception:
            return ""

    def _local_pulse_start(self) -> None:
        try:
            from PyQt6.QtCore import QTimer
            t = getattr(self, "_local_scan_pulse", None)
            if t is None:
                t = QTimer()
                t.setInterval(15000)
                t.timeout.connect(
                    lambda: self._show_busy(self._local_scan_note()))
                self._local_scan_pulse = t
            t.start()
        except Exception:
            pass   # headless tests: no indicator, no harm

    def _local_pulse_stop(self) -> None:
        t = getattr(self, "_local_scan_pulse", None)
        if t is not None:
            t.stop()

    def _apply_library(self, series: dict, movies: list[dict],
                       collections: dict | None = None) -> None:
        self._local_series_index = series
        self._local_collection_index = collections or {}
        self._local_movies_rows = movies
        rows: list[dict] = []
        if series:
            rows.append({"_header": tr("nav_series")})
            for name in sorted(series, key=str.lower):
                eps = series[name]
                seasons = {e[0] for e in eps}
                rows.append({"name": name, "_kind": "localseries",
                             "_series_title": name,
                             "_key": f"localseries::{name}",
                             "_clean_title": name, "_poster_kind": "tv",
                             "stream_icon": "",
                             "_desc": f"{len(seasons)} × {len(eps)}"})
        films = [m for m in movies if not m.get("_home")]
        home = [m for m in movies if m.get("_home")]
        if films:
            rows.append({"_header": tr("nav_movies")})
            rows += films
        rows += self._album_rows()
        covers = getattr(self, "_local_cover_index", {})
        root = self._current_cat if isinstance(self._current_cat, str) else ""
        if collections:
            rows.append({"_header": tr("local_collections")})
            for name in sorted(collections, key=str.lower):
                rows.append({"name": name,
                             "_kind": "localcollection",
                             "_series_title": name,
                             "_path": os.path.join(root, name),
                             "_key": f"localcollection::{name}",
                             "stream_icon": covers.get(name)
                             or self._folder_tile(),
                             "_desc": str(len(collections[name]))})
        if home:
            rows.append({"_header": tr("local_home_videos")})
            rows += home
        q = self._local_search_text()
        if q:
            # A search reaches INTO the shelves: the query may name an
            # artist or an album that lives inside "Musik", not the shelf
            # itself. Matching folders anywhere in the tree come back as
            # openable rows, matching files as playable ones.
            rows = ([r for r in rows if not r.get("_header")
                     and q in r.get("name", "").lower()]
                    + self._local_deep_search(q, collections))
            seen = set()
            rows = [r for r in rows
                    if not (r.get("_key") in seen or seen.add(r.get("_key")))]
        else:
            rows = [r for r in rows if r.get("_header")
                    or self._search_filter([r])]
        self._render_rows(rows, "rec", tr("local_empty"))
        if getattr(self, "_local_scan_active", False):
            self._set_status(self._local_scan_note())
        self._local_resolve_posters(
            [r for r in rows if r.get("_kind") in ("local", "localseries")])

    def _local_scan_note(self, state: dict | None = None) -> str:
        st = state if state is not None else getattr(
            self, "_local_scan_state", None)
        if not st:
            return tr("local_scanning")
        where = os.path.basename(
            (st.get("at") or "").rstrip(os.sep))
        note = tr("local_scanning_n", files=st.get("files", 0),
                  folders=st.get("dirs", 0))
        return f"{note}  ·  {where}" if where else note

    def _local_search_text(self) -> str:
        sb = getattr(self, "search", None)
        if sb is not None:
            try:
                return sb.text().strip().lower()
            except Exception:
                pass
        return (getattr(self, "search_text", "") or "").strip().lower()

    def _local_deep_search(self, q: str,
                           collections: dict | None) -> list[dict]:
        """Folder and file matches anywhere under the shelves, bounded."""
        dirs_seen: dict[str, str] = {}
        files: list[dict] = []
        # Albums are shelves too - "kanye" must find the album folder.
        for d in getattr(self, "_local_album_index", {}):
            name = os.path.basename(d.rstrip(os.sep))
            if q in name.lower() or q in d.lower():
                dirs_seen[d] = name
        for paths in (collections or {}).values():
            for p in paths:
                d = os.path.dirname(p)
                name = os.path.basename(d)
                if q in name.lower() and d not in dirs_seen:
                    dirs_seen[d] = name
                if q in os.path.basename(p).lower() and len(files) < 200:
                    row = self._local_file_row(p, stat=False)
                    if row:
                        files.append(row)
        dir_rows = [{"name": n, "_kind": "localdir", "_path": d,
                     "_key": d, "stream_icon": self._folder_tile()}
                    for d, n in sorted(dirs_seen.items(),
                                       key=lambda kv: kv[1].lower())[:100]]
        return dir_rows + files

    def _library_cache_path(self) -> str:
        from ..core.workers import default_image_cache_dir
        return str(default_image_cache_dir("meta") / "local_library.json")

    # Bumped whenever classification changes shape (collections went from
    # full paths to top-level shelves, movies stopped swallowing home
    # videos, ...) - an old cache would re-import rows filed under the old
    # rules forever, so it is discarded once instead.
    _LIB_CACHE_VER = 3

    def _library_cache(self) -> dict:
        try:
            with open(self._library_cache_path(), encoding="utf-8") as fh:
                v = json.load(fh)
            if not isinstance(v, dict) \
                    or v.get("_v") != self._LIB_CACHE_VER:
                return {}
            roots = v.get("roots")
            return roots if isinstance(roots, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_library_cache(self, root: str, series: dict,
                            movies: list[dict],
                            collections: dict | None = None) -> None:
        try:
            cache = self._library_cache()
            cache[root] = {"series": series, "movies": movies,
                           "collections": collections or {},
                           "albums": getattr(self, "_local_album_index", {}),
                           "covers": getattr(self, "_local_cover_index", {})}
            # Only the latest handful of roots - this is a warm-start, not
            # a database.
            for k in list(cache)[:-8]:
                cache.pop(k, None)
            path = self._library_cache_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"_v": self._LIB_CACHE_VER, "roots": cache}, fh)
        except OSError as e:
            log.warning("library cache save failed: %s", e)

    def _local_open_series(self, title: str) -> None:
        if not title:
            return
        self._local_push_nav(f"localseries::{title}")
        self._local_clear_search()
        self._local_series = title
        self.back_btn.setText("<-  " + tr("btn_back"))
        self.back_btn.show()
        self._load_local_episodes()

    def _load_local_episodes(self) -> None:
        coll = getattr(self, "_local_collection_index", {}).get(
            self._local_series or "")
        if coll is not None:
            rows = []
            for p in sorted(coll, key=str.lower):
                row = self._local_file_row(p, stat=False)
                if row:
                    rows.append(row)
            rows = [r for r in rows if self._search_filter([r])]
            self._render_rows(rows, "rec", tr("local_empty"))
            self._local_resolve_posters(rows)
            return
        eps = getattr(self, "_local_series_index", {}).get(
            self._local_series or "", [])
        rows: list[dict] = []
        season_now = None
        for s_no, e_no, p, stem in sorted(eps):
            if s_no != season_now:
                season_now = s_no
                rows.append({"_header": tr("local_season", n=s_no)})
            row = self._local_file_row(p, stem, stat=False)
            if not row:
                continue
            se = episode_info(stem)
            label = clean_title(stem[se[3]:])[0] if se else ""
            row["name"] = f"E{e_no:02d}" + (f" · {label}" if label else "")
            # An episode must not scrobble as a movie under the show's name.
            row["_no_scrobble"] = True
            row["_clean_title"] = ""
            rows.append(row)
        rows = [r for r in rows if r.get("_header")
                or self._search_filter([r])]
        self._render_rows(rows, "rec", tr("local_empty"))

    # -- TMDB artwork --------------------------------------------------------

    def _local_poster_cache(self) -> dict:
        # v2: the v1 cache was poisoned by the vod/tv kind bug - every miss
        # it recorded was a film searched among TV shows, and misses are
        # cached for good. A new key makes every title ask again once.
        self.settings.remove("local_tmdb_posters")
        try:
            v = json.loads(
                self.settings.value("local_tmdb_posters_v2", "") or "{}")
            return v if isinstance(v, dict) else {}
        except ValueError:
            return {}

    def _local_resolve_posters(self, files: list[dict]) -> None:
        """Fill file rows with TMDB poster art, resolved off the UI thread by
        cleaned title and cached in settings (a miss is cached too, so an
        unmatchable home video is asked about exactly once)."""
        tm = getattr(self.tmdb, "client", self.tmdb)
        if not files:
            return
        if tm is None:
            log.info("tmdb: no resolver (no API key configured) - "
                     "falling back to frame-grab thumbnails")
            self._local_make_thumbs(
                [r for r in files if not r.get("stream_icon")])
            return
        cache = self._local_poster_cache()
        todo = []
        for f in files:
            if f.get("stream_icon"):
                continue
            if (f.get("_path") or "").lower().endswith(self.AUDIO_EXTS):
                continue          # music is covers/thumbs, never TMDB
            t = f.get("_clean_title") or f.get("name") or ""
            if not re.search(r"[A-Za-zÀ-ÿ]", t):
                continue          # datestamp home videos: nothing to match
            if (f.get("_poster_kind") or "vod") == "vod" \
                    and not f.get("_year") \
                    and not _TAG.search(f.get("_filename") or ""):
                # An unanchored film match is a guess, and the guesses put
                # movie posters on home videos ("Clip #1"). A year or a
                # release tag (1080p/BluRay/x265/...) in the file name says
                # "this is a released film"; anything with neither gets an
                # honest frame-grab thumbnail instead.
                continue
            ck = f"{t} {f.get('_year') or ''}".strip()
            if cache.get(ck) == "":
                continue          # known miss must not hog the batch
            todo.append({"_key": f["_key"], "t": t,
                         "y": f.get("_year") or "",
                         "k": f.get("_poster_kind") or "vod"})
            if len(todo) >= 80:
                break
        if not todo:
            return
        gen = self._load_gen

        def job():
            out, changed, errors = {}, False, 0
            for f in todo:
                ck = f"{f['t']} {f['y']}".strip()
                if ck in cache:
                    url = cache[ck]
                else:
                    try:
                        url = tm.poster_url(ck, f["k"]) \
                            or tm.poster_url(f["t"], f["k"]) or ""
                    except Exception as e:
                        if not errors:
                            log.warning("tmdb lookup failed (%r): %r",
                                        ck, e)
                        errors += 1
                        continue   # not cached: retried next visit
                    cache[ck] = url
                    changed = True
                if url:
                    out[f["_key"]] = url
            if changed:
                self.settings.setValue(
                    "local_tmdb_posters_v2", json.dumps(cache))
            log.info("tmdb posters: %d/%d resolved (%d errors)",
                     len(out), len(todo), errors)
            return out

        def done(out):
            if gen != self._load_gen:
                return
            hit = False
            for r in self.all_items:
                u = (out or {}).get(r.get("_key"))
                if u and not r.get("stream_icon"):
                    r["stream_icon"] = u
                    hit = True
            if hit:
                self.list_model.refresh_all()
            # Whatever TMDB could not name gets a frame grab instead.
            self._local_make_thumbs(
                [r for r in self.all_items
                 if r.get("_kind") == "local"
                 and not r.get("stream_icon")])

        run_async(self.pool, job, done, lambda _e: None)

    def _local_make_thumbs(self, rows: list[dict]) -> None:
        """A real thumbnail for a file TMDB knows nothing about: grab one
        frame with ffmpeg into the image cache and point the row at the
        file - the loader reads local paths directly. Bounded per batch,
        30 s per file, misses just stay on the letter placeholder."""
        import hashlib
        import shutil
        import subprocess
        ff = shutil.which("ffmpeg")
        todo = [(r["_key"], r["_path"]) for r in rows
                if r.get("_path")
                and r["_path"].lower().endswith(self.VIDEO_EXTS)][:24]
        if not ff or not todo:
            return
        from ..core.workers import default_image_cache_dir
        tdir = str(default_image_cache_dir("thumbs"))
        gen = self._load_gen

        def job():
            os.makedirs(tdir, exist_ok=True)
            out = {}
            for key, path in todo:
                tp = os.path.join(
                    tdir,
                    hashlib.sha1(path.encode("utf-8")).hexdigest() + ".jpg")
                if not os.path.isfile(tp):
                    for ss in ("120", "1"):   # short clips: retry from start
                        try:
                            subprocess.run(
                                [ff, "-nostdin", "-v", "error", "-ss", ss,
                                 "-i", path, "-frames:v", "1",
                                 "-vf", "scale=480:-2", "-q:v", "4",
                                 "-y", tp],
                                timeout=30, check=True,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
                            break
                        except Exception:
                            continue
                if os.path.isfile(tp) and os.path.getsize(tp) > 0:
                    out[key] = tp
            return out

        def done(out):
            if not out or gen != self._load_gen:
                return
            hit = False
            for r in self.all_items:
                u = out.get(r.get("_key"))
                if u and not r.get("stream_icon"):
                    r["stream_icon"] = u
                    hit = True
            if hit:
                self.list_model.refresh_all()

        run_async(self.pool, job, done, lambda _e: None)

    # -- multiview -----------------------------------------------------------

    def _local_add_to_multiview(self, it, cell=None) -> None:
        path = it.get("_path")
        if not path:
            return
        try:
            self.history.add(path, it.get("name") or "", it.get("stream_icon"),
                             path, "local")
        except Exception:
            pass
        self.add_to_multiview(
            path, it.get("name") or "", cell,
            item=it, client=self.client, guide=None,
            playlist=self._active_playlist_name())

    # -- context menus -------------------------------------------------------

    def _local_context_menu(self, pos, it) -> None:
        from PyQt6.QtWidgets import QMenu
        m = QMenu(self)
        if it.get("_kind") == "localdir":
            m.addAction(tr("ctx_open"),
                        lambda: self._local_descend(it.get("_path")))
        elif it.get("_kind") == "localseries":
            m.addAction(tr("ctx_open"),
                        lambda: self._local_open_series(
                            it.get("_series_title") or ""))
        elif it.get("_kind") in ("localcollection", "localalbum"):
            m.addAction(tr("ctx_open"),
                        lambda: self._local_descend(it.get("_path")))
        else:
            m.addAction(tr("ctx_play_in_mpv"),
                        lambda: self.play_item(it, "mpv"))
            ext = m.addMenu(tr("ctx_open_externally"))
            ext.addAction("mpv",
                          lambda: self.play_item(it, "mpv", external=True))
            ext.addAction("VLC",
                          lambda: self.play_item(it, "vlc", external=True))
            m.addAction(tr("ctx_cast_to_chromecast"),
                        lambda: self._open_cast_dialog(it))
            mv = m.addMenu(tr("mv_add"))
            mvw = getattr(self, "_multiview_win", None)
            for n in range(4):
                occupant = ""
                if (mvw is not None and n < len(mvw.cells)
                        and mvw.cells[n].title):
                    occupant = f"  —  {mvw.cells[n].title}"
                mv.addAction(tr("mv_cell", n=n + 1) + occupant,
                             lambda it=it, n=n:
                             self._local_add_to_multiview(it, n))
        m.exec(self.listw.viewport().mapToGlobal(pos))
