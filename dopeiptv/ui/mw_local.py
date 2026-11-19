"""Local-files section mixin for MainWindow (mode "local").

Browse and play videos from registered folders and from network shares the OS
has already mounted (a gvfs SMB mount on Linux or a /Volumes share on macOS is
an ordinary path - the same reasoning as open_local_video, so there is no
in-app SMB client to go wrong). Modeled on the Recordings section: roots in
the category column, files in the middle list, playback through the proven
local-file path with resume keyed on the file path.

The list here is the source of truth; the place of the new look draws it
(ui/mw_place_local.py), and what changes on disk is read again by itself
(ui/mw_local_watch.py). The library view's walk and its pictures are
ui/mw_local_library.py, the base of the mixin here.
"""
from __future__ import annotations

import json
import os
import sys
import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QListWidgetItem

from ..core.audiotags import display_name, read_tags
from ..core import local_listing as local_order
from ..core.log import log
from ..core.workers import run_async
from ..i18n import tr
from .widgets import file_dialog_options
# The file-name helpers live with the library; episode_info stays
# importable from here.
from .mw_local_library import _TAG, _LocalLibraryMixin, clean_title, episode_info  # noqa: F401


def _file_row(p: str, audio_exts: tuple, size: int = 0,
              mtime: int = 0) -> dict:
    """A file's row as its folder's listing shows it, without touching the
    disk: a track named from its file until the enrich pass reads its tags,
    a video from its cleaned title."""
    stem = os.path.splitext(os.path.basename(p))[0]
    if p.lower().endswith(audio_exts):
        row = {"name": stem, "_kind": "local", "_clean_title": "",
               "_year": "", "_needs_tags": True}
    else:
        title, year = clean_title(stem)
        row = {"name": f"{title} ({year})" if year else title,
               "_kind": "local", "_clean_title": title, "_year": year}
    row.update({"_cast_url": p, "_path": p, "_key": p, "_size": size,
                "added": str(int(mtime)), "stream_icon": "",
                "_filename": stem})
    return row


def _dir_row(p: str) -> dict:
    """A folder's row: one to open. "12 Years a Slave (2013)" or a
    release-tagged folder is a film, and gets a poster like one - a wall of
    identical folder glyphs is not a library."""
    n = os.path.basename(p.rstrip(os.sep)) or p
    row = {"name": n, "_kind": "localdir", "_path": p, "_key": p,
           "stream_icon": ""}
    t, y = clean_title(n)
    if y or _TAG.search(n):
        row.update({"_clean_title": t, "_year": y})
    return row


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


class _LocalFilesMixin(_LocalLibraryMixin):
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
        self._local_trust_pictures()

    def _local_trust_pictures(self) -> None:
        """Local files' folders - those added, and where the system mounts
        shares and discs - are where covers of the person's own are: the
        image loader reads a file as a picture only from such a folder
        (core/picture_files.py). Names only, nothing read from the disk."""
        from ..core import picture_files
        picture_files.trust("local", [*self._local_dirs(), *picture_files.mount_parents()])

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
            file_dialog_options(QFileDialog.Option.ShowDirsOnly))
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

    # -- Rescan ------------------------------------------------------------------

    def _local_rescan(self) -> None:
        """Rescan (the place's button, F5): what Local files shows read
        from the disk again, now - changes show by themselves too, a moment
        after they happen (mw_local_watch), but a share sends no word of
        what another computer did there. A folder (Folders view,
        or one opened from the library) is listed again, the row that was
        selected selected again as it comes back; a search in it walks
        again. The library walks its folder again at once, the list as it
        is meanwhile and the progress as on a first visit; a walk of that
        folder still under way goes on. Nothing else is forgotten: posters,
        art and thumbnails stay."""
        if self.mode != "local":
            return
        root = self._current_cat
        if not isinstance(root, str) or root == "__add__":
            return
        if self._local_view() == "series" \
                and not getattr(self, "_local_ctx", None):
            done = dict(getattr(self, "_local_scan_done", {}) or {})
            done.pop(root, None)
            self._local_scan_done = done
            self._local_shelf_art = {}     # a cover put in since is found
            log.info("local files: rescan of the library %s", root)
            self._load_local_library(root, show_cache=False)
            return
        key = self._local_current_key()
        log.info("local files: rescan of a folder")
        self._load_local_items(root)
        self._local_select_key(key)

    def _local_rescan_shown(self) -> bool:
        """Is Local files' list what the window shows - the one Rescan's
        key may act on? Not another place, not under the Guide, the Search
        page or a series' page, not in TV mode or full screen."""
        if self.mode != "local" or getattr(self, "_tv_on", False) \
                or getattr(self, "_player_fs", False):
            return False
        for name in ("_guide_page_showing", "_search_page_showing",
                     "_series_page_showing"):
            shows = getattr(self, name, None)
            if shows is not None and shows():
                return False
        return True

    def _shortcut_local_rescan(self) -> None:
        """Rescan's key (F5 unless the user gave it another): only for the
        window that has the keys, and only while it shows Local files."""
        if self.isActiveWindow() and self._local_rescan_shown():
            self._local_rescan()

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
        # A copy still on its way to the file does not bring it back.
        self._local_cache_floor = getattr(self, "_local_cache_seq", 0)
        self._local_lib_mem = None
        try:
            os.unlink(self._library_cache_path())
        except OSError:
            pass
        self._local_scan_done = {}
        self._local_series_index = {}
        self._local_collection_index = {}
        self._local_movies_rows = []
        self._local_shelf_art = {}

    def _local_clear_poster_cache(self) -> None:
        self.settings.remove("local_tmdb_posters_v2")
        self.settings.remove("music_art_v1")
        self._local_art_found = {}

    def _local_clear_thumbs(self) -> None:
        from ..core.workers import clear_directory, default_image_cache_dir
        self._local_tile = None          # the folder glyph is drawn again
        try:
            clear_directory(default_image_cache_dir("thumbs"))
        except OSError as e:
            log.warning("thumbs clear failed: %s", e)

    # -- the middle list (directory browsing) --------------------------------

    def _load_local_items(self, root) -> None:
        # Whatever was searched or listed before is not what is wanted now.
        self._local_cancel_walk()
        self._local_cancel_listing()
        if self._local_view() == "series" and isinstance(root, str) \
                and not getattr(self, "_local_ctx", None):
            self._sync_search_hint()
            if getattr(self, "_local_series", None):
                self._load_local_episodes()
            else:
                self._load_local_library(root)
            return
        self._sync_search_hint()
        base = getattr(self, "_local_ctx", None) or root
        if not base or not isinstance(base, str):
            self._render_rows([], "rec", tr("local_empty"))
            return
        # Listing a folder means a stat per file, and labelling music means
        # reading a tag header per track - both a network round trip each
        # over SMB. Doing that on the UI thread is what made every click on
        # macOS spin the beachball, so the whole listing happens on the
        # worker pool and the rows arrive as they are read.
        self._local_list_token = getattr(self, "_local_list_token", 0) + 1
        token = self._local_list_token
        q = self._local_search_text()
        self._local_query = q
        if q:
            # A search looks through this folder and every subfolder under
            # it (#33), on the pool too.
            self._local_search_walk(base, q)
            return
        self._local_list_folder(base, token)

    # -- a folder's listing, in slices -----------------------------------------

    # The first slice is short: what is read by then shows at once, and a
    # folder on the local disk is whole in it - one render, no flicker.
    _LIST_FIRST = 0.08

    def _local_cancel_listing(self) -> None:
        listing = getattr(self, "_local_listing", None)
        if listing is None:
            return
        self._local_listing = None
        listing.cancel()
        self._hide_busy()

    def _local_list_folder(self, base: str, token: int) -> None:
        """The folder's rows, read on the search's own threads in slices
        (core/local_listing.py): the folders, then the files in order, each
        slice's rows added below the ones there - the list on screen until
        the first ones come, and nothing on the window's thread but making
        the model longer. On a share each entry is a round trip or two, and
        a folder of thousands stood the window still (or, read in one job,
        showed nothing for a minute)."""
        from ..core.local_listing import FolderListing
        listing = FolderListing(base, self.MEDIA_EXTS)
        self._local_listing = listing
        self._local_list_names = None
        self._local_want_key = None
        state: dict = {"shown": None, "fresh": [], "painted": None,
                       "named": False, "dirs": [], "files": [],
                       "t0": time.monotonic()}
        self._local_folder_gone = False
        self._local_list_step(listing, token, state, self._LIST_FIRST)

    def _local_list_step(self, listing, token: int, state: dict,
                         budget: float) -> None:
        aexts = self.AUDIO_EXTS

        def job():
            finished = listing.step(budget)
            # The rows are made here too: a name cleaned per file is
            # thousands of regular expressions in a big folder.
            rows = [_dir_row(i.path) if i.is_dir
                    else _file_row(i.path, aexts, i.size, i.mtime)
                    for i in listing.take()]
            names = None if state["named"] else listing.media_names()
            return finished, rows, names, listing.told

        def done(result):
            if token != getattr(self, "_local_list_token", 0) \
                    or listing is not getattr(self, "_local_listing", None):
                return                      # a newer click won
            if self.mode != "local":
                self._local_cancel_listing()
                return
            finished, rows, names, told = result
            if names is not None:
                self._local_list_names = (os.path.normpath(listing.base),
                                          names)
                state["named"] = told
            state["fresh"].extend(r for r in rows if self._search_filter([r]))
            state["gone"] = listing.missing
            now = time.monotonic()
            if finished or (state["fresh"] and (
                    state["painted"] is None
                    or now - state["painted"] >= self._SEARCH_PAINT)):
                state["painted"] = now
                self._local_list_paint(state)
            if finished:
                self._local_listing = None
                self._local_list_names = None
                self._local_want_key = None
                self._local_folder_gone = listing.missing
                self._hide_busy()
                # Read in the listing's order (by name, as it comes); any
                # other order once it is all there - one layout.
                if self._local_order() != local_order.NAME:
                    self._local_resort()
                # What changed on disk meanwhile is read once this is done
                # (mw_local_watch).
                ended = getattr(self, "_lw_walk_ended", None)
                if callable(ended):
                    ended(time.monotonic() - state.get("t0", now))
                head = getattr(self, "_lw_place_head", None)
                if callable(head):
                    head()
                return
            self._show_busy()
            self._local_list_step(listing, token, state, self._SEARCH_SLICE)

        def fail(e):
            if listing is getattr(self, "_local_listing", None):
                log.warning("local browse failed for %s: %r", listing.base, e)
                listing.done = True
                done((True, [], None, True))

        run_async(self._local_walk_pool(), job, done, fail)

    def _local_list_paint(self, state: dict) -> None:
        """The rows read since the last paint put in their places - the new
        folders after the folders there, the new files after the files -
        without laying the list out again (the selection and the scroll
        position stay), and their tags and art asked for, on the pool."""
        fresh, state["fresh"] = state["fresh"], []
        new_dirs = [r for r in fresh if r.get("_kind") == "localdir"]
        new_files = [r for r in fresh if r.get("_kind") != "localdir"]
        shown = state["shown"]
        model = getattr(self, "list_model", None)
        if (shown is not None and model is not None
                and self.all_items is shown
                and model.rowCount() == len(shown)):
            model.insert_items(len(state["dirs"]), new_dirs)
            model.append_items(new_files)
            self._set_list_status(self._count_text("rec", len(shown)))
        else:
            self._render_rows(state["dirs"] + new_dirs
                              + state["files"] + new_files,
                              "rec", tr("local_gone") if state.get("gone")
                              else tr("local_empty"))
            state["shown"] = self.all_items
        state["dirs"] += new_dirs
        state["files"] += new_files
        want = getattr(self, "_local_want_key", None)
        if want and self._local_select_key(want):
            self._local_want_key = None
        if fresh:
            self._local_enrich(fresh)
            self._local_fetch_album_art(fresh)

    def _local_listing_rows(self, url: str, item) -> list[dict] | None:
        """What a file plays on into while its folder is still being read
        (main_window._place_in_queue): every media file of the folder in
        the listing's order - the rows not shown yet made from their names,
        their tags read on the pool - not only those read so far. None
        once the listing is whole: the list on screen is the folder."""
        if getattr(self, "_local_listing", None) is None:
            return None
        got = getattr(self, "_local_list_names", None)
        folder = os.path.normpath(os.path.dirname(url))
        if not got or got[0] != folder \
                or os.path.basename(url) not in got[1]:
            return None
        have = {r.get("_path"): r for r in (self.all_items or [])
                if r.get("_kind") == "local"}
        rows: list[dict] = []
        at = 0
        for n in got[1]:
            p = os.path.join(os.path.dirname(url), n)
            if p == url:
                at = len(rows)
                rows.append(item)
            else:
                rows.append(have.get(p) or _file_row(p, self.AUDIO_EXTS))
        self._local_tag_rows(rows, at)
        return rows

    # A tag read is a network round trip over SMB, so they go in batches -
    # chained, or a folder of thousands stopped dead after the first 300.
    _ENRICH_BATCH = 300
    _ENRICH_ROUNDS = 40

    def _local_enrich(self, rows: list[dict], _round: int = 0) -> None:
        """Fill in what costs a file read - track names from the tags, and
        artwork for folders and albums - off the UI thread, then repaint.
        The list is already on screen by then; this only makes it better."""
        cand = [r for r in rows
                if r.get("_needs_tags") or (r.get("_kind") == "localdir"
                                            and not r.get("stream_icon"))]
        todo = cand[:self._ENRICH_BATCH]
        more = len(cand) > len(todo)
        if todo and getattr(self, "pool", None) is not None:
            self._local_enrich_tags(rows, todo, more, _round)
        if _round:
            return          # the poster chain was started by round 0
        # Film-named FOLDERS get posters too, not just files: a library
        # laid out as "Movies/12 Years a Slave (2013)/movie.mkv" showed
        # nothing but grey folder glyphs before. A folder of video files
        # alone has nothing for the tag pass, and its files still get theirs
        # (a poster, else a frame).
        self._local_resolve_posters(
            [r for r in rows
             if r.get("_kind") == "local"
             or (r.get("_kind") == "localdir"
                 and (r.get("_year") or r.get("_clean_title")))])

    def _local_enrich_tags(self, rows: list[dict], todo: list[dict], more: bool,
                           _round: int) -> None:
        """_local_enrich's file reads - tags, a folder's cover - on the pool,
        a batch at a time, chained."""
        token = getattr(self, "_local_list_token", 0)
        paths = [(r["_key"], r.get("_path", ""), bool(r.get("_needs_tags")),
                  r.get("_kind")) for r in todo]

        def job():
            out = {}
            # An album's tracks share their folder's cover: looked for once,
            # not once per track (a folder listed each time, on a share).
            covers: dict[str, str] = {}
            for key, path, needs_tags, kind in paths:
                info = {}
                if needs_tags:
                    tags = read_tags(path)[0]
                    info["name"] = display_name(path, tags)
                    info["_artist"] = (tags.get("artist")
                                       or tags.get("albumartist", ""))
                    info["_album"] = tags.get("album", "")
                    info["_year"] = tags.get("date", "")[:4]
                    d = os.path.dirname(path)
                    if d not in covers:
                        covers[d] = self._cover_in(d)
                    info["stream_icon"] = covers[d]
                elif kind == "localdir":
                    info["stream_icon"] = self._cover_in(path, depth=1)
                if info:
                    out[key] = info
            return out

        def done(out):
            if not out or token != getattr(self, "_local_list_token", 0):
                return
            folder = self._folder_tile()
            hit = False
            for r in self.all_items:
                info = out.get(r.get("_key"))
                if not info:
                    continue
                for k, v in info.items():
                    if k == "stream_icon" and not v:
                        v = folder if r.get("_kind") == "localdir" else ""
                    # A poster that landed first outranks the placeholder;
                    # without this the enrich pass painted grey folders
                    # back over films it had just resolved.
                    if k == "stream_icon" and self._is_folder_tile(v) \
                            and r.get("stream_icon") \
                            and not self._is_folder_tile(r["stream_icon"]):
                        continue
                    if v:
                        r[k] = v
                r.pop("_needs_tags", None)
                hit = True
            if hit:
                self.list_model.refresh_all()
            # Every row this round touched now carries art or has lost its
            # _needs_tags flag, so the next round starts on the ones after
            # them and the chain runs out on its own. An empty answer is
            # no progress and returned above, which also stops it.
            if more and _round + 1 < self._ENRICH_ROUNDS:
                self._local_enrich(rows, _round + 1)

        run_async(self.pool, job, done, lambda _e: None)

    # -- the search: a folder and its subfolders (#33) ------------------------

    # A slice of the walk per pool job: a slow share never holds a thread
    # for long, and the matches show as they are found. In the walk's own
    # order they are added below the rows there (nothing moves under the
    # user) at most every _SEARCH_PAINT; a list sorted otherwise is laid out
    # again, so less often.
    _SEARCH_SLICE = 0.25
    _SEARCH_PAINT = 0.4
    _SEARCH_RESORT = 1.5

    def _local_walk_mode(self) -> bool:
        """Is the list a folder's listing - folder view, or a folder opened
        from the library? There the search walks the subfolders too; the
        library's own rows (series, films, shelves) are filtered by name as
        before, and so is a series' episode list."""
        if self.mode != "local" or getattr(self, "_local_series", None):
            return False
        if getattr(self, "_local_ctx", None):
            return True
        cat = getattr(self, "_current_cat", None)
        return (self._local_view() == "folders" and isinstance(cat, str)
                and cat != "__add__")

    def _local_search_hook(self) -> bool:
        """The search box in a folder (main_window._apply_filter): a new
        query walks the folder and its subfolders, an emptied one lists the
        folder again. True when it took the filter over."""
        if not self._local_walk_mode():
            return False
        q = self._local_search_text()
        if q != getattr(self, "_local_query", ""):
            self._load_local_items(self._current_cat)
            return True
        if q:
            # The same matches again (a sort or a view change): the name
            # filter would drop one found by its file name.
            walk = getattr(self, "_local_walk", None)
            if walk is None:
                rows = [r for r in (self.all_items or [])
                        if not r.get("_header")]
                self._render_rows(self._sorted(rows), "rec",
                                  tr("local_search_none"))
                self._set_list_status(
                    getattr(self, "_local_search_said", "")
                    or tr("local_search_none"))
            return True
        return False

    def _local_cancel_walk(self) -> None:
        walk = getattr(self, "_local_walk", None)
        if walk is None:
            return
        self._local_walk = None
        walk.cancel()
        self._hide_busy()

    def _local_walk_pool(self):
        """The search's own threads. On the shared pool a slice waited
        behind the tag reads, art lookups and ffmpeg thumbnails the matches
        themselves set off - each a network round trip on a share, a
        thumbnail up to a minute - and the search stood still meanwhile.
        Two: a cancelled walk ends its folder while the next one starts."""
        pool = getattr(self, "_local_walk_threads", None)
        if pool is None:
            from PyQt6.QtCore import QThreadPool
            pool = QThreadPool()
            pool.setMaxThreadCount(2)
            self._local_walk_threads = pool
        return pool

    def _local_search_walk(self, base: str, q: str) -> None:
        from ..core.local_search import TreeSearch
        search = TreeSearch(base, q, self.MEDIA_EXTS, skip=self._SCAN_JUNK)
        self._local_walk = search
        self._local_hit_dirs = {}
        self._local_search_said = ""
        # Plain words: where it searches, and how many it found so far.
        self._local_search_where = tr("notice_searching_in",
                                      name=os.path.basename(base.rstrip(os.sep)) or base)
        self._show_busy(self._local_search_where, kind="search")
        self._local_search_step(search, {}, {"painted": None, "n": -1})

    def _local_walk_order(self) -> bool:
        """Is the list in the order the walk finds things (no sort of its
        own)? _sorted hands that order back as the very list it was given."""
        probe: list = []
        return self._sorted(probe) is probe

    def _local_search_step(self, search, rows: dict, state: dict) -> None:
        def job():
            return search.step(self._SEARCH_SLICE)

        def done(finished):
            if search is not getattr(self, "_local_walk", None):
                return          # a newer query, another folder, or Stop
            if self.mode != "local":
                self._local_cancel_walk()
                return
            now = time.monotonic()
            every = self._SEARCH_PAINT if self._local_walk_order() \
                else self._SEARCH_RESORT
            if finished or state["painted"] is None or (
                    len(search.hits) != state["n"]
                    and now - state["painted"] >= every):
                state["painted"], state["n"] = now, len(search.hits)
                self._local_search_paint(search, rows, state)
            if not finished:
                # The rows put in place took the strip down; the walk goes on.
                where = getattr(self, "_local_search_where", "") or tr("local_searching")
                self._show_busy(where + (" · " + tr("notice_found", n=len(search.hits))
                                         if search.hits else ""), kind="search")
                self._local_search_step(search, rows, state)
                return
            self._local_walk = None
            self._hide_busy()
            log.info("local search: %d matches in %d folders (%d entries)%s",
                     len(search.hits), search.folders, search.entries,
                     " - capped" if search.capped
                     else " - not every folder" if search.partial else "")

        def fail(e):
            if search is getattr(self, "_local_walk", None):
                log.warning("local search failed: %r", e)
                search.done = True
                done(True)

        run_async(self._local_walk_pool(), job, done, fail)

    def _local_search_paint(self, search, rows: dict,
                            state: dict | None = None) -> None:
        """The matches found so far. A row made once keeps what the enrich
        pass gave it (a track's tags, art), so only new ones are enriched.
        In the walk's order the new ones go below the rows already there,
        the selection and the scroll position untouched; nothing here reads
        the disk - the walk read what a row shows, and what costs a file
        read more (tags, art) is fetched on the pool."""
        new = []
        for h in search.hits:
            if h.path not in rows:
                rows[h.path] = self._local_hit_row(h)
                new.append(rows[h.path])
        self._local_hit_dirs = dict(search.siblings)
        shown = [rows[h.path] for h in search.hits]
        said = self._local_search_note(search, len(shown))
        self._local_search_said = said
        ordered = self._sorted(shown)
        model = getattr(self, "list_model", None)
        last = state.get("shown") if state is not None else None
        if (last is not None and ordered is shown and model is not None
                and self.all_items is last and len(last) <= len(shown)
                and model.rowCount() == len(last)
                and all(a is b for a, b in zip(last, shown, strict=False))):
            model.append_items(shown[len(last):])
            self._set_list_status(said)
        else:
            key = self._local_current_key()
            self._render_rows(ordered, "rec", said)
            self._local_select_key(key)
            self._set_list_status(said)
            if state is not None:
                state["shown"] = self.all_items
        if new:
            self._local_enrich(new)
            self._local_fetch_album_art(new)

    def _local_hit_row(self, h) -> dict:
        row = _dir_row(h.path) if h.is_dir else _file_row(
            h.path, self.AUDIO_EXTS, h.size, h.mtime)
        # Where it is, muted under its name: the subfolder relative to the
        # folder searched (nothing for that folder itself).
        row["_sub"] = h.folder
        row["_tip"] = os.path.join(h.folder, os.path.basename(h.path)) \
            if h.folder else os.path.basename(h.path)
        row["_hit"] = True
        return row

    @staticmethod
    def _local_search_note(search, n: int) -> str:
        if not search.done:
            return tr("local_search_count", n=n) if n \
                else tr("local_searching")
        said = tr("local_search_count", n=n) if n \
            else tr("local_search_none")
        if search.capped:
            said += "  ·  " + tr("local_search_more", n=search.max_hits)
        elif search.partial:
            said += "  ·  " + tr("local_search_partial")
        return said

    # -- the order picked ---------------------------------------------------------

    def _local_order(self) -> str:
        return local_order.order_of(self.settings)

    def _local_resort(self) -> None:
        """What the list shows put in the order picked - folders first, a
        heading's part on its own - the row picked kept; a search keeps its
        own order."""
        if self.mode != "local" or self._local_search_text():
            return
        rows = list(getattr(self, "all_items", None) or [])
        ordered = local_order.order_rows(rows, self._local_order())
        if [r.get("_key") for r in ordered] == [r.get("_key") for r in rows]:
            return
        key = self._local_current_key()
        self._render_rows(ordered, "rec", tr("local_empty"))
        if key:
            self._local_select_key(key)

    def _local_sort_picked(self, order: str) -> None:
        """An order picked for every folder: kept, and what shows put in it
        - by name read again in the listing's own order (the library from
        what it holds)."""
        if order not in local_order.ORDERS:
            return
        self.settings.setValue(local_order.ORDER_KEY, order)
        if order != local_order.NAME:
            self._local_resort()
        elif self._local_view() == "series" and not getattr(self, "_local_ctx", None):
            root = self._current_cat
            if isinstance(root, str) and root and root != "__add__":
                self._load_local_library(root)
        else:
            self._local_rescan()

    def _local_current_key(self):
        try:
            it = self.list_model.item_at(self.listw.currentIndex().row())
            return it.get("_key") if it else None
        except Exception:
            return None

    def _local_hit_folder(self, url: str, item: dict) -> list[dict] | None:
        """What a search match plays on into (main_window._place_in_queue):
        its own folder's files, as that folder's listing has them - never
        the other matches. None when the walk did not keep the folder."""
        d = os.path.dirname(url)
        names = (getattr(self, "_local_hit_dirs", None) or {}).get(d)
        if not names or os.path.basename(url) not in names:
            return None
        rows = []
        at = 0
        for n in names:
            p = os.path.join(d, n)
            if p == url:
                at = len(rows)
                rows.append(item)
            else:
                rows.append(_file_row(p, self.AUDIO_EXTS))
        self._local_tag_rows(rows, at)
        return rows

    def _local_tag_rows(self, rows: list[dict], first: int = 0) -> None:
        """Name a queue's tracks from their tags off the window's thread,
        the ones from *first* on before those ahead of it, as the enrich
        pass names a listing's."""
        order = rows[first:] + rows[:first]
        todo = [r for r in order if r.get("_needs_tags")][:self._ENRICH_BATCH]
        if not todo or getattr(self, "pool", None) is None:
            return
        paths = [r["_path"] for r in todo]

        def job():
            cover = self._cover_in(os.path.dirname(paths[0]))
            out = []
            for p in paths:
                tags = read_tags(p)[0]
                out.append({"name": display_name(p, tags),
                            "_artist": tags.get("artist")
                            or tags.get("albumartist", ""),
                            "_album": tags.get("album", ""),
                            "_year": tags.get("date", "")[:4],
                            "stream_icon": cover})
            return out

        def done(out):
            for r, info in zip(todo, out or [], strict=False):
                if not r.get("_needs_tags"):
                    continue    # the listing's own pass got there first
                for k, v in info.items():
                    if v and not (k == "stream_icon" and r.get(k)):
                        r[k] = v
                r.pop("_needs_tags", None)

        run_async(self.pool, job, done, lambda _e: None)

    def _local_remember_place(self) -> None:
        """Where we are right now, so leaving for TV and coming back lands
        here instead of at the root."""
        self._local_place = {
            "cat": self._current_cat,
            "ctx": getattr(self, "_local_ctx", None),
            "series": getattr(self, "_local_series", None),
            "nav": list(getattr(self, "_local_nav_stack", []) or []),
        }

    def _local_restore_place(self) -> bool:
        """Put the section back where it was left. False when there is
        nothing remembered (a first visit)."""
        place = getattr(self, "_local_place", None)
        if not place or place.get("cat") != self._current_cat:
            return False
        self._local_ctx = place.get("ctx")
        self._local_series = place.get("series")
        self._local_nav_stack = list(place.get("nav") or [])
        deep = bool(self._local_ctx or self._local_series)
        if deep:
            self.back_btn.setText("<-  " + tr("btn_back"))
            self.back_btn.show()
        else:
            self.back_btn.hide()
        self._load_local_items(self._current_cat)
        return True

    def _local_descend(self, path: str, from_key=None) -> None:
        self._local_push_nav(from_key or path)
        self._local_clear_search()
        self._local_ctx = path
        self.back_btn.setText("<-  " + tr("btn_back"))
        self.back_btn.show()
        self._load_local_items(self._current_cat)
        self._local_remember_place()

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
            self._local_remember_place()
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
        self._local_remember_place()

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

    def _local_select_key(self, key) -> bool:
        """Select the row of *key*. While a folder is still being read its
        row may not be there yet, and the rows there are the old list's:
        it is selected again as the folder's rows come in (_local_list_paint)
        - backing out of a folder, Rescan, a file revealed."""
        if key and getattr(self, "_local_listing", None) is not None:
            self._local_want_key = key
        model = getattr(self, "list_model", None)
        if not key or model is None:
            return False
        try:
            for row in range(model.rowCount()):
                it = model.item_at(row)
                if it and it.get("_key") == key:
                    ix = model.index(row)
                    self.listw.setCurrentIndex(ix)
                    self.listw.scrollTo(ix)
                    return True
        except Exception:
            pass
        return False

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
            # The tags name the track properly ("2. Jail" by Kanye West);
            # the file name is only the fallback. Reading them touches a
            # few KB of the header, not the file.
            tags = read_tags(p)[0]
            return {"name": display_name(p, tags), "_kind": "local",
                    "_clean_title": "", "_year": tags.get("date", "")[:4],
                    "_cast_url": p, "_path": p, "_key": p,
                    "_size": 0, "added": "0",
                    "stream_icon": self._album_cover(os.path.dirname(p)),
                    "_artist": tags.get("artist")
                    or tags.get("albumartist", ""),
                    "_album": tags.get("album", ""),
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

    def _local_reveal(self, path: str) -> None:
        """Browse to the folder holding *path* and select the file - what
        "jump to what is playing" means for a local file."""
        d = os.path.dirname(path)
        root = None
        for cand in list(self._local_dirs()) + \
                [p for _n, p in self._mounted_roots()]:
            if os.path.normpath(d).startswith(os.path.normpath(cand)):
                root = cand
                break
        if root and self._current_cat != root:
            for i in range(self.cat_list.count()):
                if self.cat_list.item(i).data(
                        Qt.ItemDataRole.UserRole) == root:
                    self.cat_list.setCurrentRow(i)
                    break
        self._local_series = None
        self._local_ctx = d
        self.back_btn.setText("<-  " + tr("btn_back"))
        self.back_btn.show()
        self._load_local_items(self._current_cat)
        self._local_select_key(path)

    def _local_selected_rows(self) -> list[dict]:
        try:
            rows = [self.list_model.item_at(ix.row())
                    for ix in self.listw.selectionModel().selectedRows()]
            return [r for r in rows if r and not r.get("_header")]
        except Exception:
            return []

    def _audio_rows_under(self, path: str, limit: int = 500) -> list[dict]:
        """Every track under *path*, in listening order."""
        out: list[dict] = []
        if not path or not os.path.isdir(path):
            return out
        for dirpath, dirnames, names in os.walk(path):
            dirnames[:] = sorted(d for d in dirnames
                                 if not d.startswith("."))
            for n in sorted(names, key=str.lower):
                if n.startswith(".") or not n.lower().endswith(
                        self.AUDIO_EXTS):
                    continue
                row = self._local_file_row(os.path.join(dirpath, n),
                                           stat=False)
                if row:
                    out.append(row)
                if len(out) >= limit:
                    return out
        return out

    def _local_context_menu(self, pos, it, at=None) -> None:
        """A row's menu at the list's *pos* - or, from the place's cards,
        at the global point *at*."""
        from PyQt6.QtWidgets import QMenu
        m = QMenu(self)
        if it.get("_kind") == "localdir":
            m.addAction(tr("ctx_open"),
                        lambda: self._local_descend(it.get("_path")))
            m.addAction(tr("queue_add_folder"),
                        lambda: self.queue_add(
                            self._audio_rows_under(it.get("_path"))))
        elif it.get("_kind") == "localseries":
            m.addAction(tr("ctx_open"),
                        lambda: self._local_open_series(
                            it.get("_series_title") or "", it))
        elif it.get("_kind") in ("localcollection", "localalbum"):
            m.addAction(tr("ctx_open"),
                        lambda: self._local_descend(it.get("_path")))
            m.addAction(tr("queue_add_folder"),
                        lambda: self.queue_add(
                            self._audio_rows_under(it.get("_path"))))
        else:
            m.addAction(tr("ctx_play_in_mpv"),
                        lambda: self.play_item(it, "mpv"))
            if self._is_audio(it.get("_path")):
                sel = self._local_selected_rows() or [it]
                m.addAction(tr("queue_play_next"),
                            lambda: self.queue_add(sel, play_next=True))
                m.addAction(tr("queue_add"),
                            lambda: self.queue_add(sel))
                m.addAction(tr("queue_show"), self.open_queue)
            ext = m.addMenu(tr("ctx_open_externally"))
            ext.addAction("mpv",
                          lambda: self.play_item(it, "mpv", external=True))
            ext.addAction("VLC",
                          lambda: self.play_item(it, "vlc", external=True))
            m.addAction(tr("ctx_cast_to_chromecast"),
                        lambda: self._open_cast_dialog(it))
            mv = m.addMenu(tr("mv_add"))
            mvw = getattr(self, "_multiview_win", None)
            for n in range(self._multiview_cell_count()):
                occupant = ""
                if (mvw is not None and n < len(mvw.cells)
                        and mvw.cells[n].title):
                    occupant = f"  —  {mvw.cells[n].title}"
                mv.addAction(tr("mv_cell", n=n + 1) + occupant,
                             lambda it=it, n=n:
                             self._local_add_to_multiview(it, n))
        m.exec(at if at is not None else self.listw.viewport().mapToGlobal(pos))
