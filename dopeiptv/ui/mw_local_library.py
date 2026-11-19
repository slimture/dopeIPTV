"""Local files' library (MainWindow mixin): the library view's walk and
its pictures. A library root walked on the worker pool in slices of about
four seconds, each slice drawn as it comes (``_load_local_library``,
``_local_scan_step``): every file sorted into a series, a film, a
collection or an album (``_classify``), the result laid out in the list
(``_apply_library``) and kept on disk for the next start
(``_library_cache``, ``_save_library_cache``); the search through it
(``_local_deep_search``) and a series' episodes (``_local_open_series``).
Its pictures, each looked for on the pool: TMDB's posters by cleaned title
(``_local_resolve_posters``), a frame from a file TMDB does not know
(``_local_make_thumbs``), an album's cover from its folder, its tracks'
tags or the iTunes search (``_cover_in``, ``_embedded_cover``,
``_local_fetch_album_art``) and the music shelves' (``_local_find_shelf_art``).

The file-name helpers (``clean_title``, ``episode_info``) live here too:
ui/mw_local.py's folder listing uses them, and its ``_LocalFilesMixin``
has this as its base.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

from ..core.audiotags import cover_bytes, read_tags
from ..core import local_listing as local_order
from ..core.local_watch import CAP as WATCH_CAP
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
    # The tag often sits inside brackets - "12 Years a Slave (2013)" cuts
    # to "12 Years a Slave (" - and TMDB does not match a dangling one.
    # Only the tail is trimmed: "(500) Days of Summer" keeps its own.
    t = t.rstrip(" -–._([{")
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


def _merged(old: list, new: list, key) -> list:
    """*old* in its order with each item *new* has again (by *key*) in its
    place, then *new*'s own: a library walked again shown as it was, the
    walk's finds in it."""
    if not old:
        return list(new)
    got = {key(x): x for x in new}
    had = {key(x) for x in old}
    return [got.get(key(x), x) for x in old] + \
        [x for x in new if key(x) not in had]


def _top_folder(path: str, base: str) -> str | None:
    """The first folder of *path* under *base* (normalised; "" for *base*
    itself), None for a path outside it: what os.path.relpath's first part
    says, without its abspath and common prefix per row - a library's
    thousands of rows at every render."""
    if not base or not path:
        return None
    p = os.path.normpath(path)
    if p == base:
        return ""
    head = base if base.endswith(os.sep) else base + os.sep
    if not p.startswith(head):
        return None
    return p[len(head):].split(os.sep, 1)[0]


class _LocalLibraryMixin:
    """MainWindow mixin: the Local files library and its pictures."""

    def _load_local_library(self, root: str, show_cache: bool = True,
                            quiet: bool = False) -> None:
        """The library, built progressively: the walk runs in ~4 s slices on
        the worker pool and every slice renders what has been found so far,
        so a big SMB share fills the view as it goes instead of all at the
        end. Bounded by directory/file caps rather than wall time - the
        slices make time limits unnecessary. *show_cache* False (Rescan):
        the list on screen is the library already, the walk's first slice
        renders over it. *quiet* (a change on disk, mw_local_watch): no
        notice and nothing laid out until the walk ends - then only what
        changed, the row chosen kept."""
        if getattr(self, "_local_scan_active", False) \
                and getattr(self, "_local_scan_root", None) == root:
            # This very root is mid-walk: show what it has and let it keep
            # going. Restarting on every category click threw the progress
            # away each time. A quiet walk asked for by the person says
            # what it does from now on.
            if quiet:
                return
            if getattr(self, "_local_scan_quiet", False):
                self._local_scan_quiet = False
                self._show_busy(tr("local_scanning"), kind="folder")
                self._local_pulse_start()
            if not getattr(self, "_local_series", None):
                self._apply_library(
                    getattr(self, "_local_series_index", {}),
                    getattr(self, "_local_movies_rows", []),
                    getattr(self, "_local_collection_index", {}))
                self._busy_say(self._local_scan_note())
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
            # Rows of its own: what is shown gets posters and art written
            # into it, and the cache kept in memory must stay as walked.
            cached = dict(cached)
            cached["movies"] = [dict(r) for r in cached.get("movies") or []]
        if cached is not None:
            self._local_album_index = cached.get("albums") or {}
            self._local_cover_index = cached.get("covers") or {}
            if show_cache and not getattr(self, "_local_series", None):
                self._apply_library(cached.get("series") or {},
                                    cached.get("movies") or [],
                                    cached.get("collections") or {})
            last = getattr(self, "_local_scan_done", {}).get(root, 0.0)
            if time.monotonic() - last < 120.0:
                return   # scanned moments ago: the cache render is current
        self._local_scan_active = True
        self._local_scan_quiet = quiet
        if not quiet:
            self._show_busy(tr("local_scanning"), kind="folder")
            self._local_pulse_start()
        log.info("library scan starting%s: %s", " (a change on disk)" if quiet else "",
                 root)
        state = {"walker": os.walk(root), "series": {}, "collections": {},
                 "movies": [], "albums": {}, "dirs": 0, "files": 0,
                 "root": os.path.normpath(root), "t0": time.monotonic(),
                 "dirlist": []}
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
                if len(state["dirlist"]) <= WATCH_CAP:
                    state["dirlist"].append(dirpath)    # what is watched after
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
            if token != getattr(self, "_local_scan_token", 0):
                self._local_pulse_stop()
                return                  # a newer scan owns the state now
            if self.mode != "local":
                # Left the section mid-walk: the walk stops here, so say so.
                # Left marked active, the next visit took it for a walk still
                # running and never started one - the library stayed half
                # scanned for the rest of the session.
                self._local_pulse_stop()
                self._local_scan_active = False
                self._local_scan_state = None
                self._hide_busy()
                ended = getattr(self, "_lw_walk_ended", None)
                if callable(ended):
                    ended()
                return
            finished, cut = result
            quiet = getattr(self, "_local_scan_quiet", False)
            self._local_cover_index = state.get("covers", {})
            self._local_dircover = state.get("dircover_map",
                                             state.get("dircover", {}))
            series = state["series"]
            self._local_album_index = state.get("albums", {})
            # Drilled in - or the list is not this library's any more (the
            # Folders view, another root): the list is not ours to touch.
            drilled = (getattr(self, "_local_series", None)
                       or getattr(self, "_local_ctx", None)
                       or self._local_view() != "series"
                       or getattr(self, "_current_cat", None) != root)
            collections = state["collections"]
            movies = state["movies"]
            if not finished:
                # Mid-walk: render the UNION of the cached view and what the
                # walk has found so far. Rows the walk hasn't re-reached yet
                # stay (no vanishing), new files appear as they are found,
                # and files deleted on disk are dropped only when the walk
                # completes. Never re-render under a drilled-in user.
                if cached is not None:
                    # Each in the cached order, what the walk found in its
                    # place and the new after: a library that has not
                    # changed renders as it is shown - _apply_library then
                    # leaves it be - not reshuffled, nor its counts dropping
                    # and climbing back, at each slice.
                    ms = dict(cached.get("series") or {})
                    for name, eps in series.items():
                        ms[name] = _merged(ms.get(name) or [], eps,
                                           lambda e: e[2])
                    mc = dict(cached.get("collections") or {})
                    for name, paths in collections.items():
                        mc[name] = _merged(mc.get(name) or [], paths,
                                           lambda p: p)
                    mm = _merged(cached.get("movies") or [], movies,
                                 lambda r: r.get("_key"))
                    ma = dict(cached.get("albums") or {})
                    ma.update(state.get("albums", {}))
                    # The folders' covers are worked out as the walk ends:
                    # the cached ones meanwhile, not the folder glyph.
                    self._local_cover_index = dict(cached.get("covers") or {})
                else:
                    ms, mc, mm = series, collections, movies
                    ma = state.get("albums", {})
                self._local_series_index = ms
                self._local_collection_index = mc
                self._local_movies_rows = mm
                self._local_album_index = ma
                self._local_scan_state = state
                if quiet:
                    pass            # a change on disk: laid out once it ends
                elif not drilled:
                    self._apply_library(ms, mm, mc)
                else:
                    # Drilled in: the list is not ours to touch, but the
                    # progress note still is.
                    self._busy_say(self._local_scan_note(state))
                if not quiet:
                    self._show_busy(self._local_scan_note(state), kind="folder")
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
            if not quiet:
                self._hide_busy()
            # The shelves' covers are looked for once more, in the whole
            # library this time.
            self._local_shelf_art = {}
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
            # What is watched on disk: the folders this walk went through.
            walked = getattr(self, "_local_walk_done", None)
            if callable(walked):
                walked(root, state.get("dirlist") or [],
                       time.monotonic() - state.get("t0", time.monotonic()))
            if drilled:
                return     # drilled in: the fresh index serves the way back
            if cached is not None \
                    and cached.get("series") == series \
                    and cached.get("collections") == collections \
                    and cached.get("movies") == movies:
                return     # nothing changed: the cache render stands
            key = self._local_current_key() if quiet else None
            self._apply_library(series, movies, collections)
            if key:
                self._local_select_key(key)

        def fail(e):
            if token == getattr(self, "_local_scan_token", 0):
                self._local_pulse_stop()
                self._local_scan_active = False
                self._hide_busy()
                log.warning("library scan FAILED for %s: %r", root, e)
                ended = getattr(self, "_lw_walk_ended", None)
                if callable(ended):
                    ended()
                if cached is None:
                    self._render_rows([], "rec", tr("local_empty"))

        run_async(self.pool, job, done, fail)

    def _classify(self, state: dict, p: str) -> None:
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
        """Music as ONE shelf per top-level folder that holds it - a row
        per album buried the list under every CD1/CD2 and every artist.
        Opening it browses the tree (artist -> album -> tracks)."""
        albums = getattr(self, "_local_album_index", {})
        if not albums:
            return []
        root = self._current_cat if isinstance(self._current_cat, str) else ""
        base = os.path.normpath(root) if root else ""
        shelves: dict[str, int] = {}
        for d, info in albums.items():
            n = info.get("n", 0) if isinstance(info, dict) else int(info or 0)
            top = _top_folder(d, base)
            if top is not None:
                shelves[top] = shelves.get(top, 0) + n
        # How much VIDEO each top-level folder holds, so a folder that is
        # mostly films with a stray track in it is not called music.
        vids: dict[str, int] = {}
        for r in (getattr(self, "_local_movies_rows", []) or []):
            top = _top_folder(r.get("_path") or "", base)
            if top:
                vids[top] = vids.get(top, 0) + 1
        for name, paths in (getattr(self, "_local_collection_index", {})
                            or {}).items():
            vids[name] = vids.get(name, 0) + len(paths)

        rows: list[dict] = []
        music_tops = []
        tile = self._folder_tile()
        known = self._local_shelf_memo()
        # A shelf looked for again (after a walk, on Rescan) keeps the cover
        # it shows until the new answer: no flash of the folder glyph.
        showing = {r.get("_path"): r.get("stream_icon")
                   for r in (getattr(self, "all_items", None) or [])
                   if r.get("_kind") == "localalbum"}
        pending: list[str] = []
        for top in sorted(shelves, key=str.lower):
            if not top:
                continue        # loose tracks in the root: not a shelf
            tracks = shelves[top]
            if vids.get(top, 0) > tracks:
                continue        # mostly video: it is not a music shelf
            music_tops.append(top)
            path = os.path.join(root, top)
            icon = known.get(path) if path in known else showing.get(path)
            row = {"name": top, "_kind": "localalbum", "_path": path,
                   "_key": f"localmusic::{top}",
                   "stream_icon": icon or tile,
                   "_desc": tr("local_tracks", n=tracks)}
            if path not in known:
                # Its cover is still being looked for: not one to ask
                # iTunes about meanwhile (_local_fetch_album_art).
                row["_shelf_pending"] = True
                pending.append(path)
            rows.append(row)
        self._local_music_tops = set(music_tops)
        if pending:
            self._local_find_shelf_art(pending)
        return [{"_header": tr("local_music")}] + rows if rows else []

    def _local_shelf_memo(self) -> dict[str, str]:
        """Each music shelf's cover as found ("" for none), for this walk:
        forgotten when a walk ends, on Rescan and with the library cache."""
        memo = getattr(self, "_local_shelf_art", None)
        if memo is None:
            memo = self._local_shelf_art = {}
        return memo

    def _local_find_shelf_art(self, paths: list[str]) -> None:
        """The music shelves' covers looked for on the pool - a folder
        listed per album, a track's embedded art read, each a round trip on
        a share: on the window's thread a big shelf with no art froze it for
        seconds at every slice of the library's walk. A shelf found with
        none is left to the online lookup, as before."""
        busy = getattr(self, "_local_shelf_busy", None)
        if busy is None:
            busy = self._local_shelf_busy = set()
        todo = [p for p in paths if p not in busy]
        if not todo or getattr(self, "pool", None) is None:
            return
        busy.update(todo)
        # Copies: the walk's next slice goes on adding to the index.
        albums = list(getattr(self, "_local_album_index", {}) or {})
        dircover = dict(getattr(self, "_local_dircover", {}) or {})

        def job():
            return {p: self._music_shelf_cover(p, albums, dircover)
                    for p in todo}

        def done(found):
            busy.difference_update(todo)
            found = found or {}
            self._local_shelf_memo().update(found)
            tile = self._folder_tile()
            hit = False
            none = []
            for r in self.all_items or []:
                p = r.get("_path")
                if r.get("_kind") != "localalbum" or p not in found \
                        or not r.pop("_shelf_pending", False):
                    continue
                if found[p] or r.get("stream_icon") != tile:
                    r["stream_icon"] = found[p] or tile
                    hit = True
                if not found[p]:
                    none.append(r)
            if hit:
                self.list_model.refresh_all()
            if none:
                self._local_fetch_album_art(none)

        def fail(e):
            busy.difference_update(todo)
            log.debug("music shelf art failed: %r", e)

        run_async(self.pool, job, done, fail)

    def _music_shelf_cover(self, top_path: str, albums=None,
                           dircover: dict | None = None) -> str:
        """Borrow an album cover from inside the shelf, so "Musik" shows
        real artwork instead of a blank folder. Reads the disk: on the pool
        (_local_find_shelf_art), with copies of the walk's index."""
        if albums is None:
            albums = list(getattr(self, "_local_album_index", {}) or {})
        if dircover is None:
            dircover = getattr(self, "_local_dircover", {}) or {}
        for d in sorted(albums, key=str.lower):
            if not os.path.normpath(d).startswith(os.path.normpath(top_path)):
                continue
            cov = dircover.get(d) or self._cover_in(d)
            if cov:
                return cov
        # Nothing indexed yet (a first visit renders before the walk has
        # got there): look down the tree ourselves.
        return self._cover_in(top_path, depth=2)

    def _embedded_cover(self, track: str) -> str:
        """Album art carried INSIDE the files - the usual case for a
        tagged library with no cover.jpg lying about. Extracted once per
        FOLDER into the image cache.

        Several tracks are tried before giving up: art often sits on the
        album's tracks but not on a stray intro or a hidden track, and
        asking only the first one marked whole albums artless."""
        d = os.path.dirname(track)
        try:
            import hashlib
            from ..core.workers import default_image_cache_dir
            tdir = default_image_cache_dir("thumbs")
            out = str(tdir / ("art-" + hashlib.sha1(
                d.encode("utf-8")).hexdigest() + ".img"))
            if os.path.isfile(out):
                return out if os.path.getsize(out) else ""
            data = cover_bytes(track)
            if not data:
                try:
                    names = sorted(os.listdir(d), key=str.lower)
                except OSError:
                    names = []
                tried = 0
                for n in names:
                    p = os.path.join(d, n)
                    if p == track or not n.lower().endswith(self.AUDIO_EXTS):
                        continue
                    data = cover_bytes(p)
                    tried += 1
                    if data or tried >= 5:
                        break
            os.makedirs(tdir, exist_ok=True)
            with open(out, "wb") as fh:
                fh.write(data or b"")     # empty marks "asked, none there"
            return out if data else ""
        except OSError as e:
            log.debug("embedded cover failed for %s: %s", track, e)
            return ""

    def _album_cover(self, d: str) -> str:
        return self._cover_in(d, depth=1)

    _COVER_FILES = ("cover.jpg", "cover.jpeg", "cover.png", "folder.jpg",
                    "folder.png", "front.jpg", "album.jpg")

    def _cover_in(self, d: str, depth: int = 0) -> str:
        """Artwork for folder *d*: a cover file, else the art embedded in a
        track inside it. An ARTIST folder holds no tracks of its own - only
        album folders - so with depth it borrows from the first album that
        has any, which is what left artist rows blank."""
        try:
            names = sorted(os.listdir(d), key=str.lower)
        except OSError:
            return ""
        lower = {n.lower(): n for n in names}
        for want in self._COVER_FILES:
            if want in lower:
                return os.path.join(d, lower[want])
        for n in names[:60]:
            if n.lower().endswith(self.AUDIO_EXTS):
                art = self._embedded_cover(os.path.join(d, n))
                if art:
                    return art
                break            # this folder's tracks carry none
        if depth > 0:
            for n in names[:40]:
                sub = os.path.join(d, n)
                if n.startswith(".") or not os.path.isdir(sub):
                    continue
                art = self._cover_in(sub, depth - 1)
                if art:
                    return art
        return ""

    def _is_folder_tile(self, url: str) -> bool:
        """The drawn folder glyph is a placeholder, not artwork: a film
        folder wearing one must still be allowed to fetch its poster and
        paint over it, or every film in folder view stays a grey folder."""
        tile = self._folder_tile()
        return bool(tile) and url == tile

    def _folder_tile(self) -> str:
        """A drawn folder icon as an image file the row art loader can
        serve - the delegate otherwise painted its letter placeholder AND
        the name carried a folder emoji, which read as two ugly icons."""
        try:
            from ..core.workers import default_image_cache_dir
            from .theme import P
            color = P["text2"].lstrip("#")
            # Kept once made: asked per row (is this art or the glyph?), a
            # stat each, and a library has thousands of rows.
            memo = getattr(self, "_local_tile", None)
            if memo and memo[0] == color:
                return memo[1]
            path = default_image_cache_dir("thumbs") / f"folder-{color}.png"
            if not path.exists():
                os.makedirs(path.parent, exist_ok=True)
                self._action_pixmap("folder", 128, P["text2"]).save(
                    str(path), "PNG")
            self._local_tile = (color, str(path))
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
                    lambda: self._show_busy(self._local_scan_note(), kind="folder"))
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
        covers = getattr(self, "_local_cover_index", {})
        root = self._current_cat if isinstance(self._current_cat, str) else ""
        # Folders first (music, then your own), then the cover art -
        # browsable structure at the top, posters below it.
        head: list[dict] = self._album_rows()
        music_tops = getattr(self, "_local_music_tops", set())
        collections = {k: v for k, v in (collections or {}).items()
                       if k not in music_tops}
        if collections:
            head.append({"_header": tr("local_collections")})
            for name in sorted(collections, key=str.lower):
                head.append({"name": name,
                             "_kind": "localcollection",
                             "_series_title": name,
                             "_path": os.path.join(root, name),
                             "_key": f"localcollection::{name}",
                             "stream_icon": covers.get(name)
                             or self._folder_tile(),
                             "_desc": str(len(collections[name]))})
        rows = head + rows
        if films:
            rows.append({"_header": tr("nav_movies")})
            rows += films
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
            order = self._local_order()
            if order != local_order.NAME:
                rows = local_order.order_rows(rows, order)
        # The same rows as on screen (a slice of a walk, Rescan, that found
        # nothing new): left as they are. A list with section headers is
        # laid out row by row - a library of 19,000 films took the window
        # 0.45 s each time, at every slice of a walk.
        sig = (self._grid_on() if hasattr(self, "_grid_on") else None,
               [(r.get("_key"), r.get("name"), r.get("_desc"),
                 r.get("_header")) for r in rows])
        model = getattr(self, "list_model", None)
        shown = getattr(self, "_local_lib_shown", None)
        if (model is not None and shown is not None
                and self.all_items is shown[0] and sig == shown[1]
                and model.rowCount() == len(self.all_items)):
            if getattr(self, "_local_scan_active", False):
                self._set_status(self._local_scan_note())
            return
        # A row walked again keeps the picture it shows until its own is
        # found again: no flash of initials over a library walked anew.
        icons = {r.get("_key"): r.get("stream_icon")
                 for r in (getattr(self, "all_items", None) or [])
                 if isinstance(r, dict) and r.get("_key") and r.get("stream_icon")}
        if icons:
            for r in rows:
                if not r.get("stream_icon") and r.get("_key") in icons:
                    r["stream_icon"] = icons[r["_key"]]
        self._render_rows(rows, "rec", tr("local_empty"))
        self._local_lib_shown = (self.all_items, sig)
        if getattr(self, "_local_scan_active", False):
            self._busy_say(self._local_scan_note())
        self._local_resolve_posters(
            [r for r in rows if r.get("_kind") in ("local", "localseries")])
        self._local_fetch_album_art(rows)

    def _local_scan_note(self, state: dict | None = None) -> str:
        st = state if state is not None else getattr(
            self, "_local_scan_state", None)
        if not st:
            return tr("local_scanning")
        where = os.path.basename(
            (st.get("at") or "").rstrip(os.sep))
        if where:
            # Plain words: the folder it reads, and what it found so far.
            return (tr("notice_reading_folder", name=where) + " · "
                    + tr("notice_files", n=st.get("files", 0)))
        return tr("local_scanning_n", files=st.get("files", 0),
                  folders=st.get("dirs", 0))

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
        """The cached libraries by folder. Read from the file once a
        session and kept as last written: a big library is megabytes of
        JSON, and parsing it holds the interpreter (whichever thread does
        it) for tens of ms - at every visit to the library, before. A copy
        of the roots: a writer adds its own to it."""
        mem = getattr(self, "_local_lib_mem", None)
        if mem is not None:
            return dict(mem)
        roots: dict = {}
        try:
            with open(self._library_cache_path(), encoding="utf-8") as fh:
                v = json.load(fh)
            if isinstance(v, dict) and v.get("_v") == self._LIB_CACHE_VER \
                    and isinstance(v.get("roots"), dict):
                roots = v["roots"]
        except (OSError, ValueError):
            pass
        self._local_lib_mem = roots
        return dict(roots)

    _LIB_CACHE_LOCK = threading.Lock()

    def _save_library_cache(self, root: str, series: dict,
                            movies: list[dict],
                            collections: dict | None = None) -> None:
        """The library as walked, kept for the next visit. Copied here -
        the walk's next slice goes on adding to the same lists - and written
        on the pool: read back, merged and dumped, a library of 20,000 files
        is megabytes of JSON, tens of ms on the window's thread every 15 s
        of a walk. Written in the order asked, an older copy never over a
        newer one."""
        entry = {"series": {k: list(v) for k, v in (series or {}).items()},
                 "movies": list(movies or []),
                 "collections": {k: list(v)
                                 for k, v in (collections or {}).items()},
                 "albums": {k: dict(v) if isinstance(v, dict) else v
                            for k, v in (getattr(self, "_local_album_index",
                                                 {}) or {}).items()},
                 "covers": dict(getattr(self, "_local_cover_index", {})
                                or {})}
        seq = self._local_cache_seq = getattr(self, "_local_cache_seq", 0) + 1
        pool = getattr(self, "pool", None)
        if pool is None:
            self._write_library_cache(root, entry, seq)
            return
        run_async(pool, lambda: self._write_library_cache(root, entry, seq),
                  lambda _r: None,
                  lambda e: log.warning("library cache save failed: %r", e))

    def _write_library_cache(self, root: str, entry: dict, seq: int) -> None:
        with self._LIB_CACHE_LOCK:
            if seq < getattr(self, "_local_cache_written", 0) \
                    or seq <= getattr(self, "_local_cache_floor", 0):
                return          # a newer copy is there, or it was cleared
            try:
                cache = self._library_cache()
                cache[root] = entry
                # Only the latest handful of roots - this is a warm-start,
                # not a database.
                for k in list(cache)[:-8]:
                    cache.pop(k, None)
                path = self._library_cache_path()
                os.makedirs(os.path.dirname(path), exist_ok=True)
                # Beside it, then over it: the window reads the file while
                # this writes.
                part = f"{path}.{threading.get_ident()}.part"
                with open(part, "w", encoding="utf-8") as fh:
                    json.dump({"_v": self._LIB_CACHE_VER, "roots": cache}, fh)
                os.replace(part, path)
                self._local_cache_written = seq
                self._local_lib_mem = cache
            except OSError as e:
                log.warning("library cache save failed: %s", e)

    def _local_open_series(self, title: str, row: dict | None = None) -> None:
        if not title:
            return
        if (getattr(self, "_local_series_index", {}) or {}).get(title):
            # Its own page, over the library as it is (mw_series_page).
            self._open_local_series_page(title, row)
            return
        self._local_push_nav(f"localseries::{title}")
        self._local_clear_search()
        self._local_series = title
        self.back_btn.setText("<-  " + tr("btn_back"))
        self.back_btn.show()
        self._load_local_episodes()
        self._local_remember_place()

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
            self._local_fetch_album_art(rows)
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

    # One batch is a burst of TMDB round trips; 80 keeps the worker from
    # holding the pool for a minute at a time. Batches chain until the
    # shelf is done, so a 900-film folder is not "the first 80 only".
    _POSTER_BATCH = 80
    _POSTER_ROUNDS = 40

    def _local_resolve_posters(self, files: list[dict],
                               _round: int = 0) -> None:
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
        cand: list[dict] = []
        for f in files:
            if f.get("stream_icon") and not self._is_folder_tile(
                    f["stream_icon"]):
                continue
            if (f.get("_path") or "").lower().endswith(self.AUDIO_EXTS) \
                    or f.get("_album") or f.get("_artist"):
                continue          # music is covers/tags, never TMDB
            t = f.get("_clean_title") or f.get("name") or ""
            if not re.search(r"[A-Za-zÀ-ÿ]", t):
                continue          # datestamp home videos: nothing to match
            if (f.get("_poster_kind") or "vod") == "vod" \
                    and not f.get("_year") \
                    and not _TAG.search(f.get("_filename") or f.get("name")
                                        or ""):
                # An unanchored film match is a guess, and the guesses put
                # movie posters on home videos ("Clip #1"). A year or a
                # release tag (1080p/BluRay/x265/...) in the file name says
                # "this is a released film"; anything with neither gets an
                # honest frame-grab thumbnail instead.
                continue
            ck = f"{t} {f.get('_year') or ''}".strip()
            if cache.get(ck) == "":
                continue          # known miss must not hog the batch
            cand.append({"_key": f["_key"], "t": t,
                         "y": f.get("_year") or "",
                         "k": f.get("_poster_kind") or "vod"})
        if not cand:
            return
        todo = cand[:self._POSTER_BATCH]
        more = len(cand) > len(todo)
        gen = self._load_gen
        token = getattr(self, "_local_list_token", 0)
        state: dict[str, bool] = {}

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
            # Progress is either art to show or a miss written down -
            # both shrink the next round. A round with neither (every
            # lookup errored) must not chain, or a dead API key spins.
            state["progressed"] = bool(changed or out)
            log.info("tmdb posters: %d/%d resolved (%d errors, %d left)",
                     len(out), len(todo), errors, len(cand) - len(todo))
            return out

        def done(out):
            if gen != self._load_gen \
                    or token != getattr(self, "_local_list_token", 0):
                return
            hit = False
            for r in self.all_items:
                u = (out or {}).get(r.get("_key"))
                if u and (not r.get("stream_icon")
                          or self._is_folder_tile(r["stream_icon"])):
                    r["stream_icon"] = u
                    hit = True
            if hit:
                self.list_model.refresh_all()
            if more and state.get("progressed") \
                    and _round + 1 < self._POSTER_ROUNDS:
                # The rest of the shelf. Resolved rows now carry art and
                # misses are cached as "", so each round starts from a
                # strictly smaller candidate set and this terminates.
                self._local_resolve_posters(files, _round + 1)
                return
            if more:
                log.info("tmdb posters: stopping with %d unresolved "
                         "(round %d, progressed=%s)",
                         len(cand) - len(todo), _round,
                         state.get("progressed"))
            # Whatever TMDB could not name gets a frame grab instead -
            # but only once the lookups are done, or we would spend
            # ffmpeg on files that were still queued for a poster.
            self._local_make_thumbs(
                [r for r in self.all_items
                 if r.get("_kind") == "local"
                 and not r.get("stream_icon")])

        run_async(self.pool, job, done, lambda _e: None)

    _THUMB_BATCH = 24
    _THUMB_ROUNDS = 40

    def _local_make_thumbs(self, rows: list[dict],
                           _round: int = 0) -> None:
        """A real thumbnail for a file TMDB knows nothing about: grab one
        frame with ffmpeg into the image cache and point the row at the
        file - the loader reads local paths directly. Bounded per batch and
        chained until the shelf is done, 30 s per file; a file ffmpeg can
        make nothing of is remembered so it is not tried again."""
        import hashlib
        import subprocess
        # The same lookup the cast bridge uses: the ffmpeg shipped inside a
        # frozen build first, then PATH and the usual install places. PATH
        # alone found nothing in the Windows and macOS bundles (and nothing
        # Homebrew-installed for an app launched from Finder), so those
        # never got a single thumbnail.
        from ..core.player_exec import NO_WINDOW
        from ..providers.cast_bridge import ffmpeg_path
        ff = ffmpeg_path()
        bad = getattr(self, "_local_thumb_fail", None)
        if bad is None:
            bad = self._local_thumb_fail = set()
        # Files a job is grabbing now: a list read in slices asks for its
        # rows' thumbnails at each, and one ffmpeg per file is enough.
        busy = getattr(self, "_local_thumb_busy", None)
        if busy is None:
            busy = self._local_thumb_busy = set()
        cand = [(r["_key"], r["_path"]) for r in rows
                if r.get("_path") and not r.get("stream_icon")
                and r["_path"].lower().endswith(self.VIDEO_EXTS)
                and r["_path"] not in bad and r["_path"] not in busy]
        todo = cand[:self._THUMB_BATCH]
        more = len(cand) > len(todo)
        if not ff or not todo:
            return
        busy.update(p for _k, p in todo)
        from ..core.workers import default_image_cache_dir
        tdir = str(default_image_cache_dir("thumbs"))
        gen = self._load_gen
        token = getattr(self, "_local_list_token", 0)

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
                                stderr=subprocess.DEVNULL,
                                creationflags=NO_WINDOW)
                        except Exception:
                            continue
                        # A seek past a short clip's end succeeds with no
                        # frame at all: from its start then.
                        if os.path.isfile(tp) and os.path.getsize(tp) > 0:
                            break
                if os.path.isfile(tp) and os.path.getsize(tp) > 0:
                    out[key] = tp
            return out

        def done(out):
            busy.difference_update(p for _k, p in todo)
            if gen != self._load_gen \
                    or token != getattr(self, "_local_list_token", 0):
                return
            out = out or {}
            # A file ffmpeg could not grab from stays iconless forever;
            # without remembering it, the next round would pick the same
            # 24 and never reach the rest of the folder.
            bad.update(p for k, p in todo if k not in out)
            hit = False
            for r in self.all_items:
                u = out.get(r.get("_key"))
                if u and not r.get("stream_icon"):
                    r["stream_icon"] = u
                    hit = True
            if hit:
                self.list_model.refresh_all()
            if more and _round + 1 < self._THUMB_ROUNDS:
                self._local_make_thumbs(rows, _round + 1)

        run_async(self.pool, job, done,
                  lambda _e: busy.difference_update(p for _k, p in todo))

    # -- album art from the internet -----------------------------------------

    def _music_art_cache(self) -> dict:
        try:
            v = json.loads(
                self.settings.value("music_art_v1", "") or "{}")
            return v if isinstance(v, dict) else {}
        except ValueError:
            return {}

    @staticmethod
    def _album_id(d: str) -> tuple[str, str]:
        """(artist, album) for a folder, from a track's tags - falling back
        to the folder name, which by convention is "Artist - Album"."""
        try:
            for n in sorted(os.listdir(d), key=str.lower)[:40]:
                if n.lower().endswith(
                        (".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav")):
                    t = read_tags(os.path.join(d, n))[0]
                    a = t.get("albumartist") or t.get("artist") or ""
                    b = t.get("album") or ""
                    if a or b:
                        return a, b
                    break
        except OSError:
            pass
        base = os.path.basename(d.rstrip(os.sep))
        artist, _, album = base.partition(" - ")
        return (artist, album) if album else ("", base)

    _ART_BATCH = 40
    # Folders a job reads for their album's name at most - a known miss
    # costs that read too, and on a share each is a round trip.
    _ART_LOOK = 200
    _ART_ROUNDS = 40

    def _local_fetch_album_art(self, rows: list[dict], _round: int = 0,
                               _dirs: list[str] | None = None) -> None:
        """Cover art for albums that carry none of their own, looked up by
        artist and album on the iTunes search service - no API key, one
        request per album, and the answer (misses included) is remembered
        so the same album is never asked about twice. Batched and chained,
        so a big music shelf is not "the first forty albums only".

        Here only the folders are gathered: naming an album lists its folder
        and reads a track's tags, a network round trip each on a share, and
        on the window's thread a search's hundreds of matches froze it for
        seconds. The job reads them, a known miss passed over there (it
        never takes a lookup's place in the batch), and the next round goes
        on from the folder the last one stopped at. What a folder came to
        is kept for the session (_local_art_memo): a row made again - the
        library's at every slice of its walk, a folder's rows read in slices
        - takes its art from there, and a folder is never read twice for it
        while a job for it is under way."""
        if self.settings.value("music_art_online", "true") != "true":
            return
        memo = self._local_art_memo()
        busy = getattr(self, "_local_art_busy", None)
        if busy is None:
            busy = self._local_art_busy = set()
        if _dirs is None:
            _dirs = []
            seen: set[str] = set()
            known = False
            for r in rows:
                if r.get("_kind") not in ("localalbum", "localdir", "local") \
                        or r.get("_shelf_pending"):
                    continue
                icon = r.get("stream_icon") or ""
                if icon and "/folder-" not in icon:
                    continue          # already has real art
                d = (r.get("_path") if r.get("_kind") != "local"
                     else os.path.dirname(r.get("_path") or ""))
                if not d:
                    continue
                if d in memo:
                    if memo[d]:
                        r["stream_icon"] = memo[d]
                        known = True
                    continue          # found before, or a known miss
                if d not in seen and d not in busy:
                    seen.add(d)
                    _dirs.append(d)
            if known and getattr(self, "list_model", None) is not None:
                self.list_model.refresh_all()
            busy.update(_dirs)
        if not _dirs or getattr(self, "pool", None) is None:
            busy.difference_update(_dirs)
            return
        dirs = _dirs
        cache = self._music_art_cache()
        gen = getattr(self, "_load_gen", 0)
        token = getattr(self, "_local_list_token", 0)
        state: dict = {}

        def job():
            import urllib.parse

            from ..core._lazy_requests import requests
            out, came, changed = {}, {}, False
            asked = failed = looked = 0
            for d in dirs:
                if asked >= self._ART_BATCH or looked >= self._ART_LOOK:
                    break
                looked += 1
                artist, album = self._album_id(d)
                if not (artist or album):
                    came[d] = ""
                    continue
                ck = f"{artist}|{album}".lower()
                if ck in cache:
                    url = cache[ck]
                else:
                    asked += 1
                    q = urllib.parse.urlencode(
                        {"term": f"{artist} {album}".strip(),
                         "entity": "album", "limit": 1})
                    try:
                        # requests, not urllib: its certificates travel with
                        # the app (a bundled Python on macOS has none).
                        res = requests.get("https://itunes.apple.com/search?" + q,
                                           timeout=8).json()
                        hit = (res.get("results") or [None])[0] or {}
                        url = (hit.get("artworkUrl100") or "").replace(
                            "100x100bb", "600x600bb")
                    except Exception as e:
                        log.debug("album art lookup failed (%s): %s", ck, e)
                        failed += 1
                        continue          # not cached: asked again later
                    cache[ck] = url
                    changed = True
                came[d] = url
                if url:
                    out[d] = url
            if changed:
                self.settings.setValue("music_art_v1", json.dumps(cache))
            state["next"] = looked
            # Every lookup failed (no network, the service down): no next
            # round, or a dead connection is asked about the whole shelf.
            state["stuck"] = asked > 0 and failed == asked
            log.info("album art: %d resolved online (%d folders, %d asked)",
                     len(out), looked, asked)
            return out, came

        def done(result):
            out, came = result or ({}, {})
            memo.update(came)
            at = state.get("next", len(dirs))
            busy.difference_update(dirs[:at])
            rest = dirs[at:]
            if gen != getattr(self, "_load_gen", 0):
                busy.difference_update(rest)
                return
            hit = False
            # By folder, so the rows on screen now get it - whichever list
            # asked.
            for r in self.all_items if out else ():
                d = (r.get("_path") if r.get("_kind") != "local"
                     else os.path.dirname(r.get("_path") or ""))
                url = out.get(d)
                icon = r.get("stream_icon") or ""
                if url and (not icon or "/folder-" in icon):
                    r["stream_icon"] = url
                    hit = True
            if hit:
                self.list_model.refresh_all()
            if rest and not state.get("stuck") \
                    and token == getattr(self, "_local_list_token", 0) \
                    and _round + 1 < self._ART_ROUNDS:
                self._local_fetch_album_art(rows, _round + 1, rest)
            else:
                busy.difference_update(rest)

        run_async(getattr(self, "pool", None), job, done,
                  lambda _e: busy.difference_update(dirs))

    def _local_art_memo(self) -> dict[str, str]:
        """Each folder's album art as the online lookup came to it ("" for
        none), for the session: forgotten with the poster cache."""
        memo = getattr(self, "_local_art_found", None)
        if memo is None:
            memo = self._local_art_found = {}
        return memo
