"""MainWindow mixin: Local files notices changes on disk by itself (#34).

A file renamed, added or removed shows without Rescan. While Local files
is the place, the folder shown in the Folders view (or a folder opened
from the library) is watched, or the library's folders - at most
``core/local_watch.CAP`` of them - by a watcher on a thread of its own
(ui/local_watcher.py: adding a folder to a watch asks the file system,
never on the window's thread). Which folders, and which are on the network,
is worked out on the pool (the mount table read there): a network share
(gvfs, SMB, NFS...) sends no event for what another computer does, so it -
or a library past the cap - is read again lightly while Local files is on
screen, no more often than every few minutes (``core/local_watch.poll_due``).

Changes wait for a quiet moment (``core/local_watch.Refresh``: two
seconds after the last, ten at most after the first) and are read again
in the background through the same slices as ever - a folder's
``core/local_listing.FolderListing`` on the search's own threads, the
library's walk on the pool (quietly: no notice, nothing laid out until
it ends, and then only when something changed) - never while a walk is
already under way: one is queued for after it. A folder's rows are then
brought up to date in place (``core/local_watch.diff``: the rows gone
taken out, the new put in where they belong, a file still being copied
its new size), its scroll and selection kept, the new rows' art and tags
asked for on the pool; a folder that went away says so quietly. Rescan
and F5 stay for "now".
"""

from __future__ import annotations

import os
import sys
import time
from functools import partial

from PyQt6.QtCore import QTimer

from ..core import local_listing as local_order
from ..core import local_watch as lw
from ..core.log import log
from ..core.workers import run_async
from ..i18n import tr

#: How often the light re-walk looks whether it is due.
POLL_TICK_MS = 30_000


def _remote_drives() -> list[str]:
    """Windows: the drive letters mapped to network shares."""
    if not sys.platform.startswith("win"):
        return []
    try:
        import ctypes
        import string
        get = ctypes.windll.kernel32.GetDriveTypeW      # type: ignore[attr-defined]
        mask = ctypes.windll.kernel32.GetLogicalDrives()  # type: ignore[attr-defined]
        return [f"{c}:" for i, c in enumerate(string.ascii_uppercase)
                if mask >> i & 1 and get(f"{c}:\\") == 4]   # DRIVE_REMOTE
    except Exception:
        return []


def plan_job(dirs: list[str], kind: str = "folder") -> lw.Plan:
    """On the pool: what to watch of *dirs* (a "folder" or a "library") -
    the mount table read here, a folder gone since left out."""
    mounts: list[tuple[str, str]] = []
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/self/mounts", encoding="utf-8", errors="replace") as fh:
                mounts = lw.parse_mounts(fh.read())
        except OSError:
            mounts = []
    drives = _remote_drives()
    got = lw.plan(dirs, lambda p: lw.on_network(p, mounts, remote_drives=drives),
                  cap=lw.cap_for(kind))
    watch = tuple(d for d in got.watch if os.path.isdir(d))
    return lw.Plan(watch, got.poll, got.why)


class _LocalWatchMixin:
    """MainWindow mixin: Local files' folders watched for changes."""

    _lw = None
    _lw_target: tuple | None = None
    _lw_plan: lw.Plan | None = None
    _lw_gen = 0
    _lw_state: lw.Refresh | None = None
    _lw_timer: QTimer | None = None
    _lw_poll: QTimer | None = None
    _lw_last = 0.0
    _lw_cost = 0.0
    _lw_quiet = None
    _lw_started = 0.0
    _lw_down = False
    #: The folders the library's last walk went through, per root.
    _local_walked: dict | None = None

    # -- what is watched --------------------------------------------------------------

    def _lw_what(self) -> tuple | None:
        """What Local files shows now that is to be watched: ("folder",
        path) for a folder's listing, ("library", root) for the library;
        None for a search, a series' episodes, no folder, another place."""
        if getattr(self, "mode", "") != "local" or getattr(self, "_tv_on", False):
            return None
        root = getattr(self, "_current_cat", None)
        if not isinstance(root, str) or not root or root == "__add__":
            return None
        if self._local_search_text() or getattr(self, "_local_series", None):
            return None
        ctx = getattr(self, "_local_ctx", None)
        if ctx:
            return ("folder", os.path.normpath(ctx))
        if self._local_view() == "series":
            return ("library", os.path.normpath(root))
        return ("folder", os.path.normpath(root))

    def _lw_sync(self, force: bool = False) -> None:
        """Watch what Local files shows now (the place calls it as its list
        changes; idempotent). *force*: the same, planned again - the
        library walked, with folders new since."""
        if self._lw_down:
            return
        target = self._lw_what()
        if target == self._lw_target and not force:
            return
        if target != self._lw_target:
            self._lw_cancel_quiet()
            if self._lw_state is not None:
                self._lw_state.forget()
            # What shows was just read: the light re-walk counts from now.
            self._lw_last = time.monotonic()
        self._lw_target = target
        self._lw_gen += 1
        gen = self._lw_gen
        if target is None:
            self._lw_plan = None
            if self._lw is not None:
                self._lw.clear()
            if self._lw_poll is not None:
                self._lw_poll.stop()
            return
        dirs = [target[1]] if target[0] == "folder" else self._lw_library_dirs(target[1])
        pool = getattr(self, "pool", None)
        if pool is None:
            return
        run_async(pool, partial(plan_job, dirs, target[0]), partial(self._lw_planned, gen, target),
                  lambda e: log.debug("local watch: not planned: %r", e))

    def _lw_library_dirs(self, root: str) -> list[str]:
        """The library's folders: the ones its last walk went through, else
        those its index holds files in (and every one between)."""
        walked = (self._local_walked or {}).get(os.path.normpath(root))
        if walked:
            return list(walked)
        paths: list[str] = []
        for eps in (getattr(self, "_local_series_index", None) or {}).values():
            paths += [str(e[2]) for e in eps if len(e) > 2]
        paths += [str(r.get("_path") or "") for r in
                  (getattr(self, "_local_movies_rows", None) or [])]
        for files in (getattr(self, "_local_collection_index", None) or {}).values():
            paths += [str(p) for p in files]
        paths += [os.path.join(str(d), "x") for d in
                  (getattr(self, "_local_album_index", None) or {})]
        return lw.library_dirs(root, paths)

    def _lw_planned(self, gen: int, target: tuple, plan: lw.Plan) -> None:
        if gen != self._lw_gen or target != self._lw_target or self._lw_down:
            return
        self._lw_plan = plan
        watcher = self._lw_watcher()
        if watcher is not None:
            watcher.watch(plan.watch)
        log.debug("local watch: %d folders%s", len(plan.watch),
                  f", read again lightly ({plan.why})" if plan.poll else "")
        if plan.poll:
            if self._lw_poll is None:
                t = QTimer(self)
                t.setInterval(POLL_TICK_MS)
                t.timeout.connect(self._lw_poll_tick)
                self._lw_poll = t
            self._lw_poll.start()
        elif self._lw_poll is not None:
            self._lw_poll.stop()

    def _lw_watcher(self):
        if self._lw is None and not self._lw_down:
            try:
                from .local_watcher import DirWatcher
                self._lw = DirWatcher()
                self._lw.changed.connect(self._lw_changed)
            except Exception as e:      # noqa: BLE001 - no watching: Rescan stays
                log.warning("local watch: not started: %s", e)
                self._lw_down = True
                return None
        return self._lw

    # -- a change ------------------------------------------------------------------------

    def _lw_changed(self, _path: str = "") -> None:
        if self._lw_target is None or self._lw_down:
            return
        if self._lw_state is None:
            self._lw_state = lw.Refresh()
        self._lw_state.changed(time.monotonic())
        self._lw_kick()

    def _lw_kick(self) -> None:
        state = self._lw_state
        due = state.due_in(time.monotonic()) if state is not None else None
        if due is None:
            return
        if self._lw_timer is None:
            t = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._lw_due)
            self._lw_timer = t
        self._lw_timer.start(int(due * 1000) + 50)

    def _lw_busy(self) -> bool:
        """A walk under way: a folder listed, a search, the library's walk,
        a read of the watch's own."""
        return (getattr(self, "_local_listing", None) is not None
                or getattr(self, "_local_walk", None) is not None
                or bool(getattr(self, "_local_scan_active", False))
                or self._lw_quiet is not None)

    def _lw_due(self) -> None:
        state = self._lw_state
        if state is None or self._lw_target is None:
            return
        busy = self._lw_busy()
        if state.take(time.monotonic(), busy):
            self._lw_run()
        elif state.pending and not busy:
            self._lw_kick()
        # Busy: it stays pending - _lw_walk_ended sees to it.

    def _lw_walk_ended(self, cost: float | None = None) -> None:
        """A walk of what shows ended (mw_local: a folder's listing, the
        library's walk; a read of the watch's own): the light re-walk counts
        from now, and a read queued meanwhile is due."""
        self._lw_last = time.monotonic()
        if cost is not None:
            self._lw_cost = max(0.0, float(cost))
        if self._lw_state is not None and self._lw_state.pending:
            self._lw_kick()

    def _lw_poll_tick(self) -> None:
        plan = self._lw_plan
        if plan is None or not plan.poll or self._lw_target is None or self._lw_busy():
            return
        if not self._lw_on_screen():
            return
        if lw.poll_due(self._lw_last, self._lw_cost, time.monotonic()):
            log.debug("local watch: read again lightly (%s)", plan.why)
            self._lw_run()

    def _lw_on_screen(self) -> bool:
        showing = getattr(self, "_place_local_showing", None)
        try:
            return (bool(showing()) if callable(showing) else True) and self.isVisible() \
                and not self.isMinimized() and not getattr(self, "_tv_on", False)
        except Exception:
            return False

    # -- reading again ---------------------------------------------------------------

    def _lw_run(self) -> None:
        target = self._lw_target
        if target is None or target != self._lw_what():
            return
        self._lw_started = time.monotonic()
        if target[0] == "folder":
            self._lw_reread_folder(target[1], target)
        else:
            self._lw_reread_library(target[1])

    def _lw_cancel_quiet(self) -> None:
        listing, self._lw_quiet = self._lw_quiet, None
        if listing is not None:
            listing.cancel()

    def _lw_reread_folder(self, base: str, target: tuple) -> None:
        """The folder read again in slices on the search's own threads, its
        rows made there; nothing shown until it is whole."""
        from ..core.local_listing import FolderListing
        from .mw_local import _dir_row, _file_row
        listing = FolderListing(base, self.MEDIA_EXTS)
        self._lw_quiet = listing
        token = getattr(self, "_local_list_token", 0)
        aexts = self.AUDIO_EXTS
        rows: list[dict] = []
        started = self._lw_started

        def job():
            finished = listing.step(self._SEARCH_SLICE)
            made = [_dir_row(i.path) if i.is_dir else _file_row(i.path, aexts, i.size, i.mtime)
                    for i in listing.take()]
            return finished, made

        def done(result):
            if listing is not self._lw_quiet:
                return                          # given up for something newer
            finished, made = result
            rows.extend(made)
            if not finished:
                run_async(self._local_walk_pool(), job, done, fail)
                return
            self._lw_quiet = None
            if (token == getattr(self, "_local_list_token", 0) and target == self._lw_target
                    and target == self._lw_what()):
                try:
                    self._lw_apply_folder(rows, listing.missing)
                except Exception:
                    log.exception("local watch: the folder was not brought up to date")
            self._lw_walk_ended(time.monotonic() - started)
            # A folder made again since is watched again (the old watch went
            # with it).
            if self._lw is not None and self._lw_plan is not None and target == self._lw_target:
                self._lw.watch(self._lw_plan.watch)

        def fail(e):
            if listing is self._lw_quiet:
                self._lw_quiet = None
                log.debug("local watch: reading again failed: %r", e)
                self._lw_walk_ended()

        run_async(self._local_walk_pool(), job, done, fail)

    def _lw_apply_folder(self, rows: list[dict], missing: bool = False) -> None:
        """The folder's new listing over the list shown, in place: the rows
        gone taken out, the new put in where they belong, a file's new size
        and time written into its row (a copy still under way) - the scroll
        and the selection kept, what the rows kept (tags, art) kept."""
        shown = self.all_items
        model = self.list_model
        self._local_folder_gone = bool(missing)
        if self._local_search_text():
            return
        filt = getattr(self, "_search_filter", None)
        if callable(filt):
            rows = [r for r in rows if filt([r])]
        order = local_order.order_of(self.settings)
        if order != local_order.NAME:
            rows = local_order.order_rows(rows, order)      # as the list shows them
        by_key = {r.get("_key"): r for r in shown if isinstance(r, dict)}
        touched = False
        for r in rows:
            o = by_key.get(r.get("_key"))
            if o is not None and o is not r and o.get("_kind") == "local" \
                    and (o.get("_size"), o.get("added")) != (r.get("_size"), r.get("added")):
                o["_size"], o["added"] = r.get("_size"), r.get("added")
                touched = True
        up = lw.diff([r.get("_key") for r in shown if isinstance(r, dict)],
                     [r.get("_key") for r in rows])
        empty = tr("local_gone") if missing else tr("local_empty")
        if up is None:
            if touched:
                model.refresh_all()
            if missing and not shown:
                self._set_list_status(empty)
                self._lw_place_head()
            return
        fresh = {r.get("_key"): r for r in rows if r.get("_key") not in by_key}
        in_place = (not up.reset and model.items() is shown
                    and hasattr(model, "remove_rows")
                    and not any(isinstance(r, dict) and r.get("_header") for r in shown))
        if in_place:
            if up.removed:
                model.remove_rows(up.removed)
            ins = list(up.inserted)
            i = 0
            while i < len(ins):
                at = ins[i][0]
                batch = [fresh[ins[i][1]]]
                j = i + 1
                while j < len(ins) and ins[j][0] == at + (j - i):
                    batch.append(fresh[ins[j][1]])
                    j += 1
                model.insert_items(at, batch)
                i = j
            n = len(model.items())
            self._set_list_status(self._count_text("rec", n) if n else empty)
            if touched:
                model.refresh_all()
        else:
            key = self._local_current_key()
            self._render_rows([by_key.get(r.get("_key")) or r for r in rows], "rec", empty)
            self._local_select_key(key)
        log.info("local files: a folder changed on disk - %d in, %d out%s",
                 len(up.inserted), len(up.removed), "" if in_place else " (laid out anew)")
        self._lw_place_head()
        new_rows = list(fresh.values())
        if new_rows:
            self._local_enrich(new_rows)
            self._local_fetch_album_art(new_rows)

    def _lw_place_head(self) -> None:
        head = getattr(self, "_lp_head", None)
        if callable(head) and getattr(self, "_lp_page", None) is not None:
            head()

    def _lw_reread_library(self, root: str) -> None:
        """The library walked again on the pool, quietly: nothing laid out
        until it ends, and then only if something changed."""
        done = dict(getattr(self, "_local_scan_done", {}) or {})
        done.pop(root, None)
        self._local_scan_done = done
        self._load_local_library(root, show_cache=False, quiet=True)

    def _local_walk_done(self, root: str, dirs: list[str], cost: float) -> None:
        """mw_local: the library's walk of *root* ended, through *dirs*."""
        key = os.path.normpath(root)
        walked = dict(self._local_walked or {})
        walked.pop(key, None)
        walked[key] = list(dirs)
        while len(walked) > 4:
            walked.pop(next(iter(walked)))
        self._local_walked = walked
        self._lw_walk_ended(cost)
        if self._lw_target == ("library", key):
            self._lw_sync(force=True)

    # -- the end ------------------------------------------------------------------------

    def _lw_shutdown(self) -> None:
        """The window closes: nothing watched, the watcher's thread ended."""
        self._lw_down = True
        for t in (self._lw_timer, self._lw_poll):
            if t is not None:
                t.stop()
        self._lw_cancel_quiet()
        w, self._lw = self._lw, None
        if w is not None:
            w.stop()

    def _lw_status(self) -> dict:
        """What is watched, for a test and the log."""
        w = self._lw
        plan = self._lw_plan
        return {"target": self._lw_target, "watching": len(w.watching) if w else 0,
                "count": w.count if w else 0, "poll": bool(plan and plan.poll),
                "why": plan.why if plan else "",
                "pending": bool(self._lw_state and self._lw_state.pending),
                "running": w.running() if w else False}
