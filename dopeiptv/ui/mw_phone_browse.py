"""The phone remote's lists beyond the channels, in the window
(core/phone_browse.py is the pure part, core/phone_page.py shows them):
films and series by category, a series' episodes, Recent (Continue
watching and History), local files with the folder search, radio (the
favourite stations and a search) and recordings - and each played
through the window's own path, so resume points, watched marks, Trakt and
Simkl, the connection count and the one-connection rule hold as when it
is played on the computer. The phone never opens a stream itself.

Threads: what the window holds is read on its thread in one short call
per request - the lists' rules (a hidden or locked category, a kids
profile, who is watching), the marks of the rows of one page. The rest -
a source's lists, a folder on disk, a series' episodes the series page
does not have yet, a station search, a picture made small - is done on
the server's thread and kept (phone_browse.Lists), built again only when
what it depends on changed. A portal's lists are only what was loaded
before (``paged_lists``): the phone never pages through a portal.

What may be shown is what the computer shows: a hidden category is not
there, nor a locked one while the lock is shut (the phone cannot give the
PIN), nor anything for adults for a kids profile; while who is watching
waits for a PIN nothing of the profile's is listed. Titles the phone
plays start where they got to - Continue, as on a series' page - not
with the resume question, which nobody at the computer would answer.
"""

from __future__ import annotations

import copy
import hashlib
import os
import threading
import time
from collections import OrderedDict
from typing import Any

from ..core import phone_browse as pb
from ..core import search_all as sa
from ..core import series_view as sv
from ..core.log import log
from ..i18n import tr

#: A picture made for the phone: its longer side, and how many are kept.
THUMB_PX = 192
THUMBS_MAX = 400
#: How long a folder's listing, a station search and the recordings are kept.
FOLDER_SECS = 30
RADIO_SECS = 300
#: The most voted stations the radio section shows under My stations.
RADIO_TOP = 60
REC_SECS = 30
#: How long a folder search may walk for one answer.
SEARCH_BUDGET = 3.0
#: The kinds of row a play goes through, by the id's kind.
_PLAYS = frozenset({"movie", "episode", "continue", "history", "local", "radio", "rec"})

_INIT = threading.Lock()


def _bucket(secs: float) -> int:
    return int(time.time() // secs)


def _image(row: dict) -> str:
    """A row's picture when it is on the web (never a file here)."""
    for key in ("cover", "stream_icon", "cover_big", "movie_image"):
        v = str(row.get(key) or "").strip()
        if v.lower().startswith(("http://", "https://")):
            return v
    return ""


def _source_rows(client: Any, mode: str, cid: Any) -> list[dict]:
    """(server's thread) A category's films or series, as the client has
    them - a portal's only as loaded before."""
    if getattr(client, "paged_lists", False):
        loaded = getattr(client, "loaded", None)
        if not callable(loaded):
            return []
        rows, _cats, _whole = loaded(mode)
        return [r for r in rows or [] if isinstance(r, dict) and str(r.get("category_id")) == str(cid)]
    fn = getattr(client, "vod_streams" if mode == "vod" else "series_list", None)
    return [r for r in (fn(cid) if callable(fn) else []) or [] if isinstance(r, dict)]


def _source_cats(client: Any, mode: str) -> list[dict]:
    if getattr(client, "paged_lists", False):
        loaded = getattr(client, "loaded", None)
        if not callable(loaded):
            return []
        _rows, cats, _whole = loaded(mode)
        return [c for c in cats or [] if isinstance(c, dict)]
    fn = getattr(client, "vod_categories" if mode == "vod" else "series_categories", None)
    return [c for c in (fn() if callable(fn) else []) or [] if isinstance(c, dict)]


def _under(path: str, roots: list[str]) -> bool:
    """Whether *path* is one of *roots* or inside one (links resolved)."""
    try:
        real = os.path.realpath(path)
        for root in roots:
            base = os.path.realpath(root)
            if real == base or os.path.commonpath([real, base]) == base:
                return True
    except (OSError, ValueError):
        return False
    return False


def thumbnail(path: str, size: int = THUMB_PX) -> bytes:
    """(server's thread) A picture file made small: JPEG, or PNG when it
    has transparency (a station's logo); b"" when it is no picture."""
    from PyQt6.QtCore import QBuffer, Qt

    from ..core import picture_files
    from ..core.workers import decode_image
    # Read and decoded as the image loader does: a plain file of a
    # picture's size, an SVG screened (the cache may hold one from outside).
    data = picture_files.read_file(path)
    img = decode_image(data) if data else None
    if img is None:
        return b""
    if img.width() > size or img.height() > size:
        img = img.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    ok = img.save(buf, "PNG") if img.hasAlphaChannel() else img.save(buf, "JPEG", 80)
    buf.close()
    return bytes(buf.data()) if ok else b""


class _PhoneBrowseMixin:
    _pb_parts: tuple | None = None

    def _pb(self) -> tuple[pb.Lists, pb.Refs, OrderedDict, threading.Lock]:
        parts = self._pb_parts
        if parts is None:
            with _INIT:
                parts = self._pb_parts
                if parts is None:
                    parts = self._pb_parts = (pb.Lists(), pb.Refs(), OrderedDict(), threading.Lock())
        return parts

    # -- the server's thread ------------------------------------------------------------

    def _phone_browse(self, bridge: Any, params: dict) -> dict[str, Any]:
        """One page of a list (``p``: its path, ``q``: a search, ``o``:
        where the page starts)."""
        path = pb.clean_path(params.get("p"))
        query = pb.query_of(params.get("q"))
        offset = pb.offset_of(params.get("o"))
        head, _, rest = path.partition("/")
        if head not in ("vod", "series", "recent", "local", "radio", "rec"):
            raise ValueError("no such list")
        snap = bridge.call("_pb_snap", {"mode": head})
        out: dict[str, Any] = {"path": path, "q": query, "title": ""}
        if snap.get("locked"):
            out.update(pb.paged([], 0), locked=True)
            return out
        if snap.get("kids") and head in ("local", "rec"):
            # Never a kids profile's (mw_profiles.KIDS_NEVER), whatever asks.
            out.update(pb.paged([], 0))
            return out
        if head in ("vod", "series"):
            title, rows = (self._pb_titles(snap, head, rest) if rest
                           else (snap["section"], self._pb_categories(snap, head)))
        elif head == "recent":
            title, rows = snap["section"], self._pb_recent(snap)
        elif head == "local":
            title, rows = self._pb_local(snap, rest, query)
        elif head == "radio":
            title, rows = snap["section"], self._pb_radio(snap, query)
        else:
            title, rows = snap["section"], self._pb_recordings(snap)
        if query and head not in ("local", "radio"):
            rows = pb.matching([r for r in rows if r.get("k") != "head"], query)
        page = pb.paged(rows, offset)
        marks = [r["ref"] for r in page["items"] if r.get("k") == "play" and r.get("mark")]
        got = bridge.call("_pb_marks", {"refs": marks}) if marks else {}
        # Copies: the rows kept for the next page stay as they were built.
        page["items"] = [dict({k: v for k, v in r.items() if k != "mark"}, **got.get(r.get("ref"), {}))
                         for r in page["items"]]
        out.update(page, title=title)
        return out

    def _pb_categories(self, snap: dict, mode: str) -> list[dict]:
        lists, refs, _t, _l = self._pb()
        client, shown = snap["client"], snap["shown"]

        def build() -> list[dict]:
            from ..core.profiles import adult_category
            out = []
            for c in _source_cats(client, mode):
                cid = c.get("category_id")
                if cid is None or str(cid) in shown.excluded:
                    continue
                name = shown.names.get(str(cid)) or str(c.get("category_name") or "?")
                if snap["kids"] and adult_category(name, c.get("category_name")):
                    continue
                ref = refs.put("cat", f"{mode}:{cid}", (mode, cid, name))
                out.append({"k": "dir", "p": f"{mode}/{ref}", "name": name})
            return out
        rows, _built = lists.get(("cats", mode, snap["cid"]), snap["version"], build)
        return rows

    def _pb_titles(self, snap: dict, mode: str, cat: str) -> tuple[str, list[dict]]:
        lists, refs, _t, _l = self._pb()
        got = refs.get(pb.ref_of(cat))
        if got is None or got[0] != "cat" or got[1][0] != mode:
            raise ValueError("no such list")
        _mode, cid, name = got[1]
        client, shown, keep = snap["client"], snap["shown"], snap["keep"]
        if str(cid) in shown.excluded:
            return name, []

        def build() -> list[dict]:
            rows = sa.visible(_source_rows(client, mode, cid), shown.keep, snap["kids"], frozenset())
            if keep is not None:
                keep.ready()
                rows = [r for r in rows if keep(r)]
            out = []
            for r in rows:
                ident = r.get("stream_id" if mode == "vod" else "series_id")
                if ident is None:
                    continue
                line = {"name": sa.title_of(r, shown.titles), "sub": sv.year_of(
                    r.get("releaseDate"), r.get("releasedate"), r.get("year")),
                    "img": 1 if _image(r) else 0}
                if mode == "vod":
                    line.update(k="play", ref=refs.put("movie", ident, r), mark=1)
                else:
                    line.update(k="series", sid=str(ident), ref=refs.put("series", ident, r))
                out.append(line)
            return out
        rows, _built = lists.get(("titles", mode, snap["cid"], str(cid)), snap["version"], build)
        return name, rows

    def _pb_recent(self, snap: dict) -> list[dict]:
        lists, refs, _t, _l = self._pb()
        cont, hist = snap["continue"], snap["history"]
        version = hashlib.sha1(repr(([(r.get("_kind"), self._item_key(r), r.get("_ts")) for r in cont],
                                     [(h.get("_kind"), h.get("_key")) for h in hist])).encode(
            "utf-8", "replace")).hexdigest()

        def build() -> list[dict]:
            kinds = {"live": tr("fav_channels"), "movie": tr("nav_movies"),
                     "episode": tr("nav_series"), "local": tr("nav_local"),
                     "radio": tr("nav_radio"), "recording": tr("nav_recordings")}
            out: list[dict] = []
            if cont:
                out.append({"k": "head", "name": snap["continue_title"]})
            for r in cont:
                kind = r.get("_kind")
                out.append({"k": "play", "ref": refs.put("continue", f"{kind}:{self._item_key(r)}", r),
                            "name": str(r.get("name") or ""),
                            "sub": kinds["episode" if kind == "episode" else "movie"],
                            "pct": pb.percent(r.get("_resume_pos"), r.get("_resume_dur")),
                            "img": 1 if _image(r) else 0})
            if hist:
                out.append({"k": "head", "name": snap["history_title"]})
            for h in hist:
                kind = str(h.get("_kind") or "")
                out.append({"k": "play", "ref": refs.put("history", f"{kind}:{h.get('_key')}", h),
                            "name": str(h.get("name") or ""), "sub": kinds.get(kind, ""),
                            "img": 1 if _image(h) else 0})
            return out
        rows, _built = lists.get(("recent", snap["cid"]), (snap["version"], version), build)
        return rows

    def _pb_local(self, snap: dict, rest: str, query: str) -> tuple[str, list[dict]]:
        lists, refs, _t, _l = self._pb()
        roots = list(snap["dirs"])
        for _label, path in self._mounted_roots():
            if path not in roots:
                roots.append(path)
        if not rest:
            rows = [{"k": "dir", "p": "local/" + refs.put("dir", p, p),
                     "name": os.path.basename(p.rstrip(os.sep)) or p} for p in roots]
            return snap["section"], rows
        got = refs.get(pb.ref_of(rest))
        if got is None or got[0] != "dir" or not _under(got[1], roots):
            raise ValueError("no such folder")
        base = got[1]
        title = os.path.basename(base.rstrip(os.sep)) or base
        exts, audio = snap["exts"], snap["audio"]
        if query:
            def search() -> list[dict]:
                from ..core.local_search import TreeSearch
                walk = TreeSearch(base, query, exts, skip=snap["junk"])
                end = time.monotonic() + SEARCH_BUDGET
                while not walk.step(0.5) and time.monotonic() < end:
                    pass
                walk.cancel()
                out = []
                for h in walk.hits:
                    if h.is_dir:
                        out.append({"k": "dir", "p": "local/" + refs.put("dir", h.path, h.path),
                                    "name": os.path.basename(h.path), "sub": h.folder})
                    else:
                        folder = os.path.dirname(h.path)
                        files = walk.siblings.get(folder) or [h.path]
                        out.append({"k": "play", "name": os.path.basename(h.path), "sub": h.folder,
                                    "ref": refs.put("local", h.path, (h.path, files))})
                return out
            rows, _b = lists.get(("lsearch", base, query), _bucket(FOLDER_SECS), search)
            return title, rows

        def listing() -> list[dict]:
            try:
                names = sorted(os.listdir(base), key=str.lower)
            except OSError as e:
                log.info("phone remote: a folder could not be read: %s", type(e).__name__)
                return []
            dirs, files = [], []
            for n in names:
                if n.startswith("."):
                    continue
                p = os.path.join(base, n)
                if os.path.isdir(p):
                    if n.lower() not in snap["junk"]:
                        dirs.append({"k": "dir", "p": "local/" + refs.put("dir", p, p), "name": n})
                elif n.lower().endswith(exts):
                    files.append(p)
            out = dirs
            music = [p for p in files if p.lower().endswith(audio)]
            video = [p for p in files if not p.lower().endswith(audio)]
            for p in files:
                same = music if p.lower().endswith(audio) else video
                out.append({"k": "play", "name": os.path.basename(p),
                            "ref": refs.put("local", p, (p, same))})
            return out
        rows, _b = lists.get(("folder", base), _bucket(FOLDER_SECS), listing)
        return title, rows

    def _pb_radio(self, snap: dict, query: str) -> list[dict]:
        lists, refs, _t, _l = self._pb()
        from .mw_radio import station_sub

        def rows_of(stations: list[dict]) -> list[dict]:
            out = []
            for st in stations:
                uuid = str(st.get("stationuuid") or "")
                if not uuid or not (st.get("url") or st.get("_url")):
                    continue
                out.append({"k": "play", "ref": refs.put("radio", uuid, st),
                            "name": str(st.get("name") or ""), "sub": station_sub(st),
                            "img": 1 if _image(st) else 0})
            return out
        client = snap["radio"]
        if not query:
            # My stations first, then the most voted - never an empty
            # section for someone who has kept no station yet.
            mine = rows_of(snap["favs"])

            def top() -> list[dict]:
                if client is None:
                    return []
                try:
                    from ..providers.radio_browser import TOP_VOTED
                    return rows_of(list(client.top(TOP_VOTED, RADIO_TOP) or []))
                except Exception as e:
                    log.info("phone remote: the radio stations failed: %s", type(e).__name__)
                    return []
            popular, _b = lists.get(("radio", ""), _bucket(RADIO_SECS), top)
            if not mine:
                return popular
            if not popular:
                return mine
            return ([{"k": "head", "name": tr("radio_cat_favorites")}] + mine
                    + [{"k": "head", "name": tr("radio_cat_top_voted")}] + popular)

        def search() -> list[dict]:
            try:
                return rows_of(list(client.search(query) or []))
            except Exception as e:
                log.info("phone remote: the radio search failed: %s", type(e).__name__)
                return []
        rows, _b = lists.get(("radio", query), _bucket(RADIO_SECS), search)
        return rows

    def _pb_recordings(self, snap: dict) -> list[dict]:
        lists, refs, _t, _l = self._pb()
        root, exts, info = snap["dir"], snap["rec_exts"], snap["read_info"]

        def build() -> list[dict]:
            found = []
            if os.path.isdir(root):
                for dirpath, _dirs, names in os.walk(root):
                    for n in names:
                        p = os.path.join(dirpath, n)
                        if os.path.splitext(n)[1].lower() in exts and os.path.isfile(p):
                            try:
                                found.append((os.stat(p).st_mtime, p))
                            except OSError:
                                continue
            found.sort(reverse=True)
            out = []
            for mtime, p in found:
                title = info(p).get("title") or os.path.splitext(os.path.basename(p))[0]
                out.append({"k": "play", "ref": refs.put("rec", p, (p, title)), "name": title,
                            "sub": time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))})
            return out
        rows, _b = lists.get(("rec", root), _bucket(REC_SECS), build)
        jobs = snap.get("jobs") or []
        if not jobs:
            return rows
        # What records now and what is to come, over the files: a tap stops
        # one or cancels it (always asked on the page), a held one goes ahead.
        out: list[dict] = []
        for status, head in (("recording", snap["now_title"]), ("scheduled", snap["later_title"])):
            these = [j for j in jobs if j["status"] == status]
            if these:
                out.append({"k": "head", "name": head})
            for j in sorted(these, key=lambda j: j["start"]):
                when = time.strftime("%H:%M", time.localtime(j["start"])) if status == "recording" \
                    else time.strftime("%Y-%m-%d %H:%M", time.localtime(j["start"]))
                sub = " · ".join(t for t in (j["channel"], when, snap["waiting"] if j["held"] else "") if t)
                out.append({"k": "job", "id": j["id"], "name": j["title"], "sub": sub,
                            "state": "rec" if status == "recording" else "held" if j["held"] else "sched"})
        if rows:
            out.append({"k": "head", "name": snap["files_title"]})
        return out + rows

    def _phone_episodes(self, bridge: Any, params: dict) -> dict[str, Any]:
        """A series' Episodes view: its seasons and their episodes, which
        plays and which is next - built on change only (``v``: the version
        the state hands out while one of its episodes plays)."""
        lists, refs, _t, _l = self._pb()
        sid = str(params.get("sid") or "").strip()[:64]
        if not sid:
            raise ValueError('"sid" is the series')
        listed = None
        ref = pb.ref_of(params.get("ref"))
        if ref:
            got = refs.get(ref)
            if got is not None and got[0] == "series":
                listed = got[1]
        got_series = bridge.call("_pb_series", {"sid": sid, "listed": listed})
        if got_series.get("locked"):
            return {"sid": sid, "series": "", "seasons": [], "open": 0, "locked": True}
        series = got_series.get("series")
        if series is None:
            raise ValueError("no such series")
        client, info = got_series["client"], got_series.get("info")
        if info is None:
            from .mw_series_page import _fetch
            ok, info = _fetch(client, sid, series, got_series.get("keep"))
            if not ok:
                raise ValueError("no such series")
            bridge.call("_pb_keep_series", {"client": client, "sid": sid, "info": info})
        structure, _b = lists.get(("eps", id(client), sid), id(info),
                                  lambda: self._pb_structure(series, info))
        seasons, rows_by_key = structure
        version = (id(info), str(params.get("v") or ""), got_series.get("playing") or "")

        def build() -> dict[str, Any]:
            states = bridge.call("_pb_episode_states", {"series": series, "episodes": list(rows_by_key.values())})
            order = sv.flow(seasons)
            nxt = sv.next_up(order, lambda e: bool(states.get(sv.episode_key(e), (False,))[0]),
                             lambda e: sv.Progress(*states.get(sv.episode_key(e), (False, 0.0, 0.0))[1:]))
            name = str(series.get("name") or series.get("title") or "")
            labels = [self._sp_season_label(s)[0] for s in seasons]

            def short(ep: dict) -> str:
                n = sv.episode_number(ep)
                return tr("sp_episode_short", n=n) if n is not None else ""

            def ref_of(ep: dict) -> str:
                return refs.put("episode", f"{sid}:{sv.episode_key(ep)}", (series, seasons, ep))
            shown = pb.episodes(seasons, name, labels, short, states, ref_of)
            playing = got_series.get("playing") or ""
            next_key = sv.episode_key(nxt.episode) if nxt.episode is not None else ""
            return {"sid": sid, "series": name, "seasons": shown, "playing": playing,
                    "next": next_key, "open": pb.opening(shown, playing, next_key)}
        payload, _b = lists.get(("epv", id(client), sid), version, build)
        return payload

    def _pb_structure(self, series: dict, info: dict) -> tuple[list, dict]:
        """(server's thread) A series_info answer as seasons of the
        window's own episode rows - a copy: the series page has its own."""
        rows = self._episode_rows(copy.deepcopy(info), series)
        seasons = sv.seasons(rows)
        return seasons, {sv.episode_key(e): e for s in seasons for e in s.episodes}

    def _phone_play_ref(self, bridge: Any, params: dict) -> dict[str, Any]:
        ref = pb.ref_of(params.get("ref"))
        got = self._pb()[1].get(ref) if ref else None
        if got is None or got[0] not in _PLAYS:
            raise ValueError("no such title")
        return bridge.call("_pb_play", {"kind": got[0], "value": got[1], "answers": params.get("answers")})

    def _phone_thumb(self, params: dict) -> bytes | None:
        """A row's picture made small, kept for the session (the image
        cache's copy, fetched there once when it is not yet)."""
        _lists, refs, thumbs, lock = self._pb()
        got = refs.get(pb.ref_of(params.get("id")))
        if got is None:
            return None
        kind, value = got
        row = value[2] if kind == "episode" else value
        url = _image(row) if isinstance(row, dict) else ""
        if not url:
            return None
        with lock:
            hit = thumbs.get(url)
            if hit is not None:
                thumbs.move_to_end(url)
                return hit
        path = self._phone_logo_file(url)
        data = thumbnail(path) if path else b""
        if not data:
            return None
        with lock:
            thumbs[url] = data
            while len(thumbs) > THUMBS_MAX:
                thumbs.popitem(last=False)
        return data

    # -- the window's thread --------------------------------------------------------------

    def _pb_do(self, action: str, params: dict) -> Any:
        if action == "_pb_snap":
            return self._pb_snapshot(str(params.get("mode") or ""))
        if action == "_pb_marks":
            return self._pb_marks(params.get("refs") or [])
        if action == "_pb_series":
            return self._pb_series(str(params.get("sid") or ""), params.get("listed"))
        if action == "_pb_keep_series":
            self._pb_keep_series(params["client"], params["sid"], params["info"])
            return None
        if action == "_pb_episode_states":
            return self._pb_episode_states(params["series"], params["episodes"])
        if action == "_pb_play":
            return self._pb_play(params["kind"], params["value"])
        raise ValueError(f"unknown action: {action}")

    def _pb_version(self) -> tuple:
        from ..core import profiles
        return (id(self.client), profiles.active_id(),
                bool(getattr(self.parental, "session_unlocked", False)),
                bool(self._profile_kids()), self.__dict__.get("_kids_ver", 0))

    def _pb_snapshot(self, mode: str) -> dict[str, Any]:
        """What a list of *mode* needs of the window, read at once."""
        self._pb_era_kept()             # another source or profile: its ids forgotten
        snap: dict[str, Any] = {"locked": bool(self._profile_locked()), "client": self.client,
                                "cid": id(self.client), "kids": bool(self._profile_kids())}
        if snap["locked"]:
            return snap
        snap["version"] = self._pb_version()
        if mode in ("vod", "series"):
            from ..services.failover import Shown
            unlocked = bool(getattr(self.parental, "session_unlocked", False))
            shown = Shown.of(self.overrides, self.channel_ov, locked=not unlocked, mode=mode)
            snap.update(shown=shown, keep=self._kids_keep(mode),
                        section=tr("nav_movies" if mode == "vod" else "nav_series"))
            snap["version"] = (snap["version"], shown.excluded, shown.hidden,
                               tuple(sorted(shown.names.items())), tuple(sorted(shown.titles.items())))
        elif mode == "recent":
            hist = [h for h in self.history.items() if h.get("_url") or h.get("_kind") == "radio"]
            snap.update(section=tr("label_recent"),
                        continue_title=tr("cat_continue"), history_title=tr("nav_history"))
            snap["continue"] = [dict(r) for r in self._continue_rows()]
            snap["history"] = [dict(h) for h in self._pb_history_shown(hist)]
        elif mode == "local":
            snap.update(dirs=self._local_dirs(), exts=tuple(self.MEDIA_EXTS),
                        audio=tuple(self.AUDIO_EXTS), junk=frozenset(self._SCAN_JUNK),
                        section=tr("nav_local"))
        elif mode == "radio":
            snap.update(favs=[dict(s) for s in self.radio_favs.items()], radio=self._radio(),
                        section=tr("nav_radio"))
        elif mode == "rec":
            snap.update(dir=self.rec.directory(), rec_exts=tuple(self.rec.VIDEO_EXTS),
                        read_info=self.rec.read_info, section=tr("nav_recordings"),
                        jobs=self._phone_rec_jobs(), now_title=tr("rec_status_recording"),
                        later_title=tr("rec_upcoming"), files_title=tr("nav_recordings"),
                        waiting=tr("rec_waiting_room"))
        return snap

    def _pb_history_shown(self, rows: list[dict]) -> list[dict]:
        """History as the phone may list it: a kids profile's limits, and
        no channel, film or episode the lists leave out now - one kept
        without its category held back until the catalogue said it."""
        unlocked = bool(getattr(self.parental, "session_unlocked", False))
        rows = self._lock_filter(rows)
        out = []
        excluded = {m: self.overrides.excluded_ids(m, include_locked=not unlocked)
                    for m in ("live", "vod", "series")}
        keeps = {m: self._kids_keep(m) for m in ("live", "vod", "series")}
        for h in rows:
            kind = h.get("_kind")
            mode = {"live": "live", "movie": "vod", "episode": "series"}.get(str(kind))
            if mode is not None:
                probe = h.get("_series_ctx") if mode == "series" else h
                if str((probe or {}).get("category_id")) in excluded[mode]:
                    continue
                keep = keeps[mode]
                if keep is not None and not keep(h):
                    continue
            out.append(h)
        return out

    def _pb_marks(self, refs_wanted: list) -> dict[str, dict]:
        """How far the films of one page got, and which are watched."""
        refs = self._pb()[1]
        out: dict[str, dict] = {}
        for ref in refs_wanted[:pb.PAGE]:
            got = refs.get(str(ref))
            if got is None or got[0] != "movie":
                continue
            row = got[1]
            pos, dur, _at = self.resume.progress(self._item_key(row), "movie")
            watched = bool(self.is_movie_watched(row))
            out[str(ref)] = {"pct": 0 if watched else pb.percent(pos, dur), "w": 1 if watched else 0}
        return out

    def _pb_series(self, sid: str, listed: Any) -> dict[str, Any]:
        """The series *sid* - the one playing, the series page's, or one the
        phone listed - with what the window already holds of its episodes
        (the series page's answer, kept ten minutes)."""
        if not self._pb_era_kept():
            listed = None               # listed for another source or profile
        if self._profile_locked():
            return {"locked": True}
        lp = getattr(self, "_last_playback", None) or {}
        series = None
        playing = ""
        ctx = lp.get("series_ctx") or {}
        if lp.get("kind") == "episode" and str(ctx.get("series_id")) == sid:
            series = ctx
            if getattr(self, "_playing_key", None) is not None:
                playing = str(self._playing_key)
        page = getattr(self, "_sp_ctx", None) or {}
        if series is None and getattr(self, "_sp_local", None) is None \
                and str(page.get("series_id")) == sid:
            series = page
        if series is None and isinstance(listed, dict) and str(listed.get("series_id")) == sid:
            series = listed
        if series is None:
            return {"series": None}
        if not self._pb_series_allowed(series, playing):
            return {"series": None}
        from .mw_series_page import CACHE_SECS
        hit = (getattr(self, "_sp_cache", None) or {}).get((id(self.client), sid))
        info = hit[1] if hit is not None and time.monotonic() - hit[0] < CACHE_SECS else None
        return {"series": dict(series), "client": self.client, "info": info,
                "keep": self._kids_keep("series"), "playing": playing}

    def _pb_series_allowed(self, series: dict, playing: str) -> bool:
        """A kids profile never sees one for adults; a locked category's only
        while the lock is open (or its episode plays: it was opened here)."""
        from ..core.profiles import adult_item
        if self._profile_kids() and (adult_item(series) or not self._kids_allows(series, "series")):
            return False
        cat = series.get("category_id")
        if (cat is not None and not playing and self.overrides.is_locked("series", cat)
                and not self.parental.session_unlocked):
            return False
        return True

    def _pb_keep_series(self, client: Any, sid: str, info: dict) -> None:
        """A series_info the phone asked for, kept as the series page keeps
        its own - the page opens on it at once."""
        if client is not self.client:
            return
        from .mw_series_page import CACHE_MAX
        cache = self._sp_cache if self._sp_cache is not None else {}
        cache[(id(client), sid)] = (time.monotonic(), info)
        while len(cache) > CACHE_MAX:
            cache.pop(next(iter(cache)))
        self._sp_cache = cache

    def _pb_episode_states(self, series: dict, episodes: list[dict]) -> dict[str, tuple]:
        """Each episode's (watched, position, length), as the series page
        works them out."""
        saved = self.series_ctx
        self.series_ctx = series
        out: dict[str, tuple] = {}
        try:
            for ep in episodes:
                key = self._item_key(ep)
                watched = bool(self.watched_source(ep, "episode"))
                pos, dur, _at = self.resume.progress(key, "episode")
                srv = self._ms_resume_at(key, "episode", ep, pos)
                out[sv.episode_key(ep)] = (watched, float(srv), float(dur))
        finally:
            self.series_ctx = saved
        return out

    def _pb_era(self) -> tuple:
        """Whose the ids the phone was given are: the source and the profile
        in use when they were listed."""
        from ..core import profiles
        return (id(self.client), profiles.active_id())

    def _pb_era_kept(self) -> bool:
        """(the window's thread) Whether the ids handed out are still of the
        source and profile in use; when not, they are forgotten with the
        lists that carry them - a row listed for another profile or source
        never plays, and every list is built again with ids of its own."""
        era = self._pb_era()
        if self.__dict__.get("_pb_refs_for") == era:
            return True
        lists, refs, _t, _l = self._pb()
        lists.clear()
        refs.clear()
        self._pb_refs_for = era
        return False

    def _pb_play(self, kind: str, value: Any) -> dict[str, Any]:
        """A row the phone listed, played through the window's own path -
        if the lists would show it now (the lock, a kids profile)."""
        self._profile_refuse_locked()
        if not self._pb_era_kept():
            raise ValueError("no such title")
        self._phone_asked()
        self._come_forward()
        if kind in ("local", "rec") and self._profile_kids():
            raise ValueError("no such title")       # never a kids profile's
        if kind == "continue" and not self._pb_continue_allowed(value):
            raise ValueError("no such title")
        if kind == "history" and not self._pb_history_shown([value]):
            raise ValueError("no such title")
        if kind == "movie":
            if not self._pb_movie_allowed(value):
                raise ValueError("no such title")
            return {"ok": self._pb_play_movie(value)}
        if kind == "episode":
            series, seasons, ep = value
            if not self._pb_series_allowed(series, ""):
                raise ValueError("no such title")
            key = self._item_key(ep)
            pos = self._ms_resume_at(key, "episode", ep, self.resume.saved_position(key, "episode"))
            return {"ok": self._play_series_episode(series, seasons, ep, pos if pos > 0 else None)}
        if kind == "continue":
            return {"ok": self._pb_play_continue(value)}
        if kind == "history":
            return {"ok": self._pb_play_history(value)}
        if kind == "local":
            path, folder = value
            return {"ok": self._pb_play_local(path, folder)}
        if kind == "radio":
            self._radio_play(value)
            return {"ok": True}
        if kind == "rec":
            path, title = value
            if not os.path.isfile(path):
                raise ValueError("no such recording")
            self._start_at_once(path, "recording")
            try:
                self._start_playback(path, title, None, path, "recording", record=False)
            finally:
                self._sp_start_once = None
            return {"ok": True}
        raise ValueError("no such title")

    def _pb_continue_allowed(self, row: dict) -> bool:
        """A Continue watching row as the lists show it now."""
        if not isinstance(row, dict) or not self._lock_filter([row]):
            return False
        if row.get("_kind") == "episode":
            ctx = row.get("_series_ctx")
            return (self._pb_series_allowed(ctx if isinstance(ctx, dict) else {}, "")
                    and self._kids_allows(row, "series"))
        return self._pb_movie_allowed(row)

    def _pb_movie_allowed(self, row: dict) -> bool:
        unlocked = bool(getattr(self.parental, "session_unlocked", False))
        if str(row.get("category_id")) in self.overrides.excluded_ids("vod", include_locked=not unlocked):
            return False
        return self._kids_allows(row, "vod")

    def _pb_play_movie(self, row: dict, at: float | None = None) -> bool:
        """A film as the list plays it - where it got to, without the
        question (the phone's Continue)."""
        sid = row.get("stream_id")
        if sid is None:
            return False
        url = self.client.vod_url(sid, row.get("container_extension")) if sid is not None else ""
        if not url:
            url = row.get("_url") or ""
        if not url:
            return False
        key = self._item_key(row)
        if at is None:
            self._start_at_once(key, "movie", row)
        else:
            self._sp_start_once = (key, at) if at > 0 else None
        try:
            self._start_playback(url, row.get("name") or row.get("title") or "dopeIPTV",
                                 row.get("stream_icon") or row.get("cover"), key, "movie", item=row)
        finally:
            self._sp_start_once = None
        return True

    def _pb_play_continue(self, row: dict) -> bool:
        if row.get("_kind") == "episode":
            key = self._item_key(row)
            pos = float(row.get("_resume_pos") or 0)
            self._sp_start_once = (key, pos) if pos > 0 else None
            try:
                self._play_continue_episode(row)
            finally:
                self._sp_start_once = None
            return True
        return self._pb_play_movie(row, float(row.get("_resume_pos") or 0))

    def _pb_play_history(self, it: dict) -> bool:
        """A History row, as Mine replays one: from its stored address and
        kind, an episode under its series."""
        kind = str(it.get("_kind") or "live")
        if kind == "radio":
            self._radio_play(it)
            return True
        url = it.get("_url")
        if not url:
            return False
        if kind == "local":
            return self._pb_play_local(url, [url])
        if kind == "live" and it.get("stream_id") is not None:
            if not self._chno_rows([it]):
                raise ValueError("no such channel")
            self.play_live_channel(it)
            return True
        ctx = it.get("_series_ctx") or {}
        if kind == "episode" and ctx.get("series_id") is None:
            kind = "movie"
        key = it.get("_key")
        saved = self.series_ctx
        if kind == "episode":
            self.series_ctx = ctx
            self._ep_queue_override, self._ep_index_override = [], -1
        if kind in self._RESUMABLE:
            self._start_at_once(key, kind, it)
        try:
            self._start_playback(url, it.get("name") or "dopeIPTV", it.get("stream_icon"), key,
                                 kind, item=it)
        finally:
            self.series_ctx = saved
            self._sp_start_once = None
            if kind == "episode":
                self._ep_queue_override, self._ep_index_override = None, -1
        return True

    def _pb_play_local(self, path: str, folder: list) -> bool:
        """A file of a local folder, the folder's files of the same kind
        (music, else video) its play queue - as the list plays one."""
        if not path or not os.path.isfile(path):
            raise ValueError("no such file")
        q = getattr(self, "_track_queue", []) or []
        if not (q and getattr(self, "_queue_explicit", False)):
            self._track_queue = [{"_path": p, "name": os.path.splitext(os.path.basename(p))[0],
                                  "_key": p, "_kind": "local"} for p in folder or [path]]
            self._queue_explicit = False
        title = os.path.splitext(os.path.basename(path))[0]
        self._start_at_once(path, "local")
        try:
            self._start_playback(path, title, None, path, "local", record=True,
                                 item={"name": title, "_path": path, "_key": path, "_kind": "local"})
        finally:
            self._sp_start_once = None
        return True
