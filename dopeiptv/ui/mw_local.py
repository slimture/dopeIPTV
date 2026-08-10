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
        view = QListWidgetItem(
            tr("local_view_series") if self._local_view() == "series"
            else tr("local_view_folders"))
        view.setData(Qt.ItemDataRole.UserRole, "__view__")
        self.cat_list.addItem(view)
        self.cat_list.blockSignals(False)
        if self.cat_list.count() > 2:
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
        self._local_ctx = None
        self._local_series = None
        self.back_btn.hide()
        self.cat_list.blockSignals(True)
        self.cat_list.clear()
        self.cat_list.blockSignals(False)
        self._load_local_categories()

    # -- the middle list (directory browsing) --------------------------------

    def _load_local_items(self, root) -> None:
        if self._local_view() == "series" and isinstance(root, str):
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
        for n in names:
            if n.startswith("."):
                continue
            p = os.path.join(base, n)
            if os.path.isdir(p):
                dirs.append({"name": "📁  " + n, "_kind": "localdir",
                             "_path": p, "_key": p, "stream_icon": ""})
            elif n.lower().endswith(self.MEDIA_EXTS):
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                stem = os.path.splitext(n)[0]
                title, year = clean_title(stem)
                files.append({"name": f"{title} ({year})" if year else title,
                              "_kind": "local", "_clean_title": title,
                              "_year": year, "_cast_url": p,
                              "_path": p, "_key": p, "_size": st.st_size,
                              "added": str(int(st.st_mtime)),
                              "stream_icon": "", "_filename": stem})
        rows = self._search_filter(dirs) + self._search_filter(files)
        self._render_rows(rows, "rec", tr("local_empty"))
        self._local_resolve_posters([r for r in rows
                                     if r.get("_kind") == "local"])

    def _local_descend(self, path: str) -> None:
        self._local_ctx = path
        self.back_btn.show()
        self._load_local_items(self._current_cat)

    def _local_up(self) -> None:
        """One level up; at the category root the back button retires."""
        if getattr(self, "_local_series", None):
            self._local_series = None
            self.back_btn.hide()
            self._load_local_items(self._current_cat)
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
        self._local_scan_token = getattr(self, "_local_scan_token", 0) + 1
        token = self._local_scan_token
        cached = self._library_cache().get(root)
        if cached is not None and not (cached.get("series")
                                       or cached.get("movies")
                                       or cached.get("collections")):
            cached = None
        if cached is not None:
            self._apply_library(cached.get("series") or {},
                                cached.get("movies") or [],
                                cached.get("collections") or {})
        self._show_busy(tr("local_scanning"))
        self._local_pulse_start()
        log.info("library scan starting: %s", root)
        state = {"walker": os.walk(root), "series": {}, "collections": {},
                 "movies": [], "dirs": 0, "files": 0,
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
                    return True, False
                state["dirs"] += 1
                dirnames[:] = [d for d in dirnames
                               if not d.startswith(".")
                               and d.lower() not in self._SCAN_JUNK]
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
            series = state["series"]
            collections = state["collections"]
            movies = state["movies"]
            if not finished:
                # Mid-walk: refresh the top level with what exists so far -
                # but never yank the user out of a series/collection they
                # have drilled into; they get the fresh index on the way
                # back instead.
                self._local_series_index = series
                self._local_collection_index = collections
                if not getattr(self, "_local_series", None):
                    self._apply_library(series, movies, collections)
                self._local_scan_step(root, state, token, cached)
                return
            self._local_pulse_stop()
            self._hide_busy()
            log.info("library scan %s: %d series, %d collections, "
                     "%d movies%s", root, len(series), len(collections),
                     len(movies),
                     " (walk cut short - dir/file cap hit)" if cut else "")
            self._save_library_cache(root, series, movies, collections)
            self._local_series_index = series
            self._local_collection_index = collections
            if getattr(self, "_local_series", None):
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
                self._hide_busy()
                log.warning("library scan FAILED for %s: %r", root, e)
                if cached is None:
                    self._render_rows([], "rec", tr("local_empty"))

        run_async(self.pool, job, done, fail)

    def _classify(self, state: dict, p: str) -> None:
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
        if len(parts) > 1:
            state["collections"].setdefault(parts[0], []).append(p)
        else:
            row = self._local_file_row(p, stem, stat=False)
            if row:
                state["movies"].append(row)

    def _local_pulse_start(self) -> None:
        try:
            from PyQt6.QtCore import QTimer
            t = getattr(self, "_local_scan_pulse", None)
            if t is None:
                t = QTimer()
                t.setInterval(15000)
                t.timeout.connect(
                    lambda: self._show_busy(tr("local_scanning")))
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
        if collections:
            rows.append({"_header": tr("local_collections")})
            for name in sorted(collections, key=str.lower):
                rows.append({"name": "📁  " + name,
                             "_kind": "localcollection",
                             "_series_title": name,
                             "_key": f"localcollection::{name}",
                             "stream_icon": "",
                             "_desc": str(len(collections[name]))})
        if movies:
            rows.append({"_header": tr("nav_movies")})
            rows += movies
        rows = [r for r in rows if r.get("_header")
                or self._search_filter([r])]
        self._render_rows(rows, "rec", tr("local_empty"))
        self._local_resolve_posters(
            [r for r in rows if r.get("_kind") in ("local", "localseries")])

    def _library_cache_path(self) -> str:
        from ..core.workers import default_image_cache_dir
        return str(default_image_cache_dir("meta") / "local_library.json")

    def _library_cache(self) -> dict:
        try:
            with open(self._library_cache_path(), encoding="utf-8") as fh:
                v = json.load(fh)
            return v if isinstance(v, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_library_cache(self, root: str, series: dict,
                            movies: list[dict],
                            collections: dict | None = None) -> None:
        try:
            cache = self._library_cache()
            cache[root] = {"series": series, "movies": movies,
                           "collections": collections or {}}
            # Only the latest handful of roots - this is a warm-start, not
            # a database.
            for k in list(cache)[:-8]:
                cache.pop(k, None)
            path = self._library_cache_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(cache, fh)
        except OSError as e:
            log.warning("library cache save failed: %s", e)

    def _local_open_series(self, title: str) -> None:
        if not title:
            return
        self._local_series = title
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
        try:
            v = json.loads(
                self.settings.value("local_tmdb_posters", "") or "{}")
            return v if isinstance(v, dict) else {}
        except ValueError:
            return {}

    def _local_resolve_posters(self, files: list[dict]) -> None:
        """Fill file rows with TMDB poster art, resolved off the UI thread by
        cleaned title and cached in settings (a miss is cached too, so an
        unmatchable home video is asked about exactly once)."""
        tm = self.tmdb
        if not files:
            return
        if tm is None:
            self._local_make_thumbs(
                [r for r in files if not r.get("stream_icon")])
            return
        cache = self._local_poster_cache()
        todo = [{"_key": f["_key"],
                 "t": f.get("_clean_title") or f["name"],
                 "y": f.get("_year") or "",
                 "k": f.get("_poster_kind") or "vod"}
                for f in files
                if not f.get("stream_icon") and (f.get("_clean_title")
                                                 or f.get("name"))][:80]
        if not todo:
            return
        gen = self._load_gen

        def job():
            out, changed = {}, False
            for f in todo:
                ck = f"{f['t']} {f['y']}".strip()
                if ck in cache:
                    url = cache[ck]
                else:
                    try:
                        url = tm.poster_url(ck, f["k"]) \
                            or tm.poster_url(f["t"], f["k"]) or ""
                    except Exception:
                        continue   # network hiccup: retry next visit
                    cache[ck] = url
                    changed = True
                if url:
                    out[f["_key"]] = url
            if changed:
                self.settings.setValue(
                    "local_tmdb_posters", json.dumps(cache))
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
        elif it.get("_kind") in ("localseries", "localcollection"):
            m.addAction(tr("ctx_open"),
                        lambda: self._local_open_series(
                            it.get("_series_title") or ""))
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
