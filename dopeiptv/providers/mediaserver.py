"""Media servers (Jellyfin, Emby, Plex) behind XtreamClient's interface, on
BoxClient's catalogue (providers/boxes.py).

What the three share: a sign-in that leaves a token and never the password,
one device id per install (``device_id``), and a catalogue loaded once and
kept when a refresh fails - live channels with the server's guide handed to
the guide as XMLTV, films by library, series with their seasons and episodes,
the server's recordings - with the server's own artwork, resume points and
watched marks on the rows (``_server_pos``, ``_played``, ``_last_played``).

What is played goes back to the server on a thread of its own (``report``,
``mark``: a queue the window only puts into), so the server's resume point
and watched mark stay in step with what was watched here.

A kind gives ``_probe`` (who is signed in, or an error), ``_catalogue`` (the
neutral items below), ``_episodes_of`` (a series' episodes), ``_send`` (one
report or mark, on the reporter's thread) and optionally ``_guide`` and
``_live_open`` (a live channel that has to be opened before it plays).

Neutral items, as a kind hands them over:
  movie   - key, title, category, url, and optionally icon, backdrop, added,
            ext, plot, genre, director, cast, year, released, secs, rating,
            official, played, pos, last_played, tmdb
  series  - key, title, category, and optionally icon, backdrop, added,
            updated, plot, genre, cast, year, released, rating, played, tmdb
  episode - key, title, season, episode, url, and optionally icon, plot,
            secs, released, ext, played, pos, last_played, added
"""

from __future__ import annotations

import queue
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from ..core._lazy_requests import requests
from ..core.log import log, register_secrets
from ..i18n import LocalizedError, tr
from .boxes import RECORDINGS, BoxClient, _hms, clean_text, stable_id

DEVICE_KEY = "media_device_id"
PRODUCT = "dopeIPTV"
_ID = re.compile(r"^[0-9a-f]{32}$")
_FRACTION = re.compile(r"(\.\d{1,6})\d*")

_device_lock = threading.Lock()
_device: dict[str, Any] = {"id": "", "settings": None}


def install_device_store(settings: Any) -> None:
    """Where the install's device id lives (the app's settings), set as the
    window starts. Without one an id is made for this run only."""
    with _device_lock:
        _device["settings"] = settings
        _device["id"] = ""


def device_id() -> str:
    """This install's device id: what Jellyfin and Emby tie a sign-in to and
    Plex knows the app by (X-Plex-Client-Identifier). Random, the same on
    every start, never the computer's name - and never in a backup."""
    with _device_lock:
        if _device["id"]:
            return str(_device["id"])
        s = _device["settings"]
        value = ""
        if s is not None:
            try:
                value = str(s.value(DEVICE_KEY, "") or "")
            except Exception:
                value = ""
        if not _ID.match(value):
            value = uuid.uuid4().hex
            if s is not None:
                try:
                    s.setValue(DEVICE_KEY, value)
                except Exception:
                    log.debug("media server: device id not stored", exc_info=True)
        _device["id"] = value
        return value


def ticks(value: Any) -> float:
    """Seconds from .NET ticks (Jellyfin, Emby: 10,000,000 a second)."""
    try:
        return max(0.0, float(value or 0) / 10_000_000)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def iso_time(text: Any) -> int:
    """Unix time of an ISO 8601 stamp ("2024-05-01T10:00:00.0000000Z"), 0
    for anything else. A stamp without a zone is UTC, as the servers mean."""
    s = str(text or "").strip()
    if not s or s.startswith("0001-"):
        return 0
    s = _FRACTION.sub(r"\1", s.replace("Z", "+00:00"))
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return max(0, int(dt.timestamp()))
    except (OverflowError, OSError, ValueError):
        return 0


def as_dict(value: Any) -> dict:
    """*value* when it is a dict, else an empty one (a server's JSON)."""
    return value if isinstance(value, dict) else {}


def to_int(value: Any) -> int | None:
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def query_of(url: str) -> dict[str, str]:
    """A URL's query, the first of each name, names in lower case."""
    try:
        q = parse_qs(urlsplit(url or "").query, keep_blank_values=True)
    except ValueError:
        return {}
    return {k.lower(): v[0] for k, v in q.items() if v}


def _secs_text(secs: float) -> str:
    return _hms(int(secs)) if secs > 0 else ""


def _rating5(value: Any) -> float:
    try:
        r = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(10.0, r)) / 2, 1)


def _named(title: str, year: Any) -> str:
    """A title as the app's lists name one: with its year, as providers
    send them (the year is what tells two of one name apart)."""
    y = to_int(year)
    if y and not re.search(r"\(\d{4}\)\s*$", title):
        return f"{title} ({y})"
    return title


class _Reporter:
    """One thread per client that sends what the window queued, in order;
    it goes when there has been nothing to send for a minute and comes back
    with the next job. A full queue drops the job rather than wait."""

    IDLE_EXIT = 60.0

    def __init__(self, send: Callable[[tuple], None], name: str) -> None:
        self._send = send
        self._name = name
        self._q: queue.Queue[tuple] = queue.Queue(maxsize=200)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def put(self, job: tuple) -> None:
        try:
            self._q.put_nowait(job)
        except queue.Full:
            log.debug("%s: report queue full - one dropped", self._name)
            return
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name=f"{self._name}-report",
                                                daemon=True)
                self._thread.start()

    def _run(self) -> None:
        while True:
            try:
                job = self._q.get(timeout=self.IDLE_EXIT)
            except queue.Empty:
                with self._lock:
                    if self._q.empty():
                        self._thread = None
                        return
                continue
            try:
                self._send(job)
            except LocalizedError as e:
                log.info("%s: %s not sent - %s", self._name, job[0], e.key)
            except Exception as e:
                log.info("%s: %s not sent - %s", self._name, job[0], type(e).__name__)
            finally:
                self._q.task_done()

    def wait(self, timeout: float) -> bool:
        """Whether everything queued was sent within *timeout* seconds."""
        end = time.monotonic() + max(0.0, timeout)
        while self._q.unfinished_tasks:
            if time.monotonic() >= end:
                return False
            time.sleep(0.02)
        return True


class MediaServerClient(BoxClient):
    """The shared half of a media server (see the module's docstring)."""

    LOG_NAME = "media server"
    KIND = "media server"
    DEFAULT_PORT = 8096
    TIMEOUT = (6.0, 45.0)
    # Read timeout for a library's list (thousands of titles).
    LIST_TIMEOUT = (6.0, 120.0)
    # A series' episodes are asked again after this long.
    SERIES_TTL = 120.0
    # An opened live channel's address is good for this long.
    LIVE_TTL = 20.0
    # The window reports what plays this often (mw_media).
    REPORT_EVERY = 10.0
    reports_playback = True

    def __init__(self, server: str, token: str, user_id: str = "", account: str = "",
                 options: dict | None = None) -> None:
        super().__init__(server, "", "", options)
        self.token = (token or "").strip()
        register_secrets(self.token)
        self.user_id = (user_id or "").strip()
        self.account = (account or "").strip()
        self.device = device_id()
        # Why the server has no live TV, in the user's language ("" = it has).
        self.live_note = ""
        self._movies: list[dict] = []
        self._movie_by_id: dict[int, dict] = {}
        self._libraries: list[tuple[str, str]] = []
        self._shows: list[dict] = []
        self._show_by_id: dict[int, dict] = {}
        self._show_libraries: list[tuple[str, str]] = []
        self._ep_ids: dict[str, int] = {}
        self._ep_taken: set[int] = set()
        self._episodes: dict[int, dict] = {}
        self._series_cache: dict[int, tuple[float, dict]] = {}
        self._resume_rows: list[dict] = []
        self._by_key: dict[str, dict] = {}
        self._live_cache: dict[int, tuple[float, str]] = {}
        self._live_lock = threading.Lock()
        self._info_cache: dict[int, dict] = {}
        self._reporter = _Reporter(self._send, self.LOG_NAME)

    # -- requests -------------------------------------------------------------

    def get(self, path: str, params: dict | None = None,
            timeout: tuple[float, float] | None = None,
            absolute: bool = False, stream: bool = False) -> Any:
        return self.request("GET", path, params=params, timeout=timeout, absolute=absolute,
                            stream=stream)

    def request(self, method: str, path: str, params: dict | None = None,
                body: Any = None, timeout: tuple[float, float] | None = None,
                absolute: bool = False, headers: dict | None = None,
                stream: bool = False) -> Any:
        """One call to the server with the sign-in on it. A 401 is a
        sign-in the server no longer takes (LocalizedError). *stream*: as
        BoxClient.get's."""
        url = path if absolute else self.url(path)
        more = {"stream": True} if stream else {}
        r = self.session.request(method, url, params=params, json=body, headers=headers,
                                 timeout=timeout or self.TIMEOUT, **more)
        if stream and not r.ok:
            r.close()
        if r.status_code == 401:
            raise LocalizedError("err_ms_signed_out", kind=self.KIND)
        r.raise_for_status()
        return r

    def json(self, path: str, params: dict | None = None,
             timeout: tuple[float, float] | None = None, method: str = "GET",
             body: Any = None) -> Any:
        r = self.request(method, path, params=params, body=body, timeout=timeout)
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            raise LocalizedError("err_unexpected_response") from None

    # -- what a kind gives ----------------------------------------------------

    def _catalogue(self) -> dict:
        """{"channels", "recordings", "movies", "movie_libraries", "series",
        "series_libraries", "resume"}: channels and recordings as BoxClient
        takes them, libraries as (id, name), the rest neutral items ("resume":
        movies and episodes, an episode with "series_key")."""
        raise NotImplementedError

    def _episodes_of(self, series_key: str) -> list[dict]:
        raise NotImplementedError

    def _details(self, key: str) -> dict:
        """More of a film than its list has (cast, director): {} by default."""
        return {}

    def _send(self, job: tuple) -> None:
        """One queued report or mark, on the reporter's thread."""
        raise NotImplementedError

    def _live_open(self, row: dict) -> str:
        """The address of a live channel that has to be opened first."""
        return str(row.get("_url") or "")

    # -- the catalogue --------------------------------------------------------

    def authenticate(self) -> dict:
        if not self.server or not self.token:
            raise LocalizedError("err_ms_signed_out", kind=self.KIND)
        try:
            self.box_info = self._probe() or {}
        except LocalizedError:
            raise
        except requests.HTTPError as e:
            code = getattr(e.response, "status_code", 0)
            if code == 403:
                raise LocalizedError("err_ms_signed_out", kind=self.KIND) from None
            if code in (404, 405, 501):
                raise LocalizedError("err_box_not_found", kind=self.KIND) from None
            raise
        except ValueError:
            raise LocalizedError("err_box_not_found", kind=self.KIND) from None
        cat = self._catalogue()
        channels = list(cat.get("channels") or [])
        self._take(channels, list(cat.get("recordings") or []))
        opened = {str(c.get("key")) for c in channels if c.get("_open")}
        for row in self._rows:
            if row["_key"] in opened:
                row["_open"] = True
        with self._live_lock:
            self._live_cache = {}
        self._take_media(cat)
        self._loaded = True
        self._stale = False
        log.info("%s: %d film(s), %d series, %d channel(s), %d recording(s)", self.LOG_NAME,
                 len(self._movies), len(self._shows), len(self._by_id), len(self._recs))
        return {"user_info": {"auth": 1}}

    def _progress(self, row: dict, item: dict) -> None:
        """The server's resume point and watched mark on a row."""
        secs = float(item.get("secs") or 0)
        pos = float(item.get("pos") or 0)
        row["_played"] = bool(item.get("played"))
        row["_server_pos"] = pos
        row["_server_dur"] = secs
        row["_last_played"] = int(item.get("last_played") or 0)
        row.pop("_progress_pct", None)
        if pos > 0 and secs > pos:
            row["_progress_pct"] = max(1, min(99, round(100 * pos / secs)))

    def _movie_row(self, item: dict, sid: int, num: int) -> dict:
        secs = float(item.get("secs") or 0)
        plot = clean_text(item.get("plot"), lines=True)
        released = str(item.get("released") or "")[:10]
        info = {"plot": plot, "description": plot, "genre": clean_text(item.get("genre")),
                "director": clean_text(item.get("director")), "cast": clean_text(item.get("cast")),
                "duration": _secs_text(secs), "duration_secs": int(secs),
                "releasedate": released, "o_name": "",
                "rating": str(item.get("official") or ""),
                "backdrop_path": list(item.get("backdrop") or [])}
        rating = _rating5(item.get("rating"))
        row: dict[str, Any] = {
            "num": num, "name": _named(clean_text(item.get("title")) or "?", item.get("year")),
            "stream_type": "movie", "stream_id": sid, "stream_icon": str(item.get("icon") or ""),
            "added": str(int(item.get("added") or 0)), "category_id": str(item.get("category") or ""),
            "category_ids": [], "container_extension": str(item.get("ext") or "mkv").lstrip(".") or "mkv",
            "rating": str(round(rating * 2, 1)) if rating else "", "rating_5based": rating,
            "is_adult": 0, "custom_sid": "", "direct_source": "", "year": str(item.get("year") or ""),
            "_url": str(item.get("url") or ""), "_info": info, "_ms_id": str(item["key"])}
        tmdb = to_int(item.get("tmdb"))
        if tmdb:
            row["_tmdb_id"] = tmdb
        self._progress(row, item)
        return row

    def _series_row(self, item: dict, sid: int, num: int) -> dict:
        plot = clean_text(item.get("plot"), lines=True)
        rating = _rating5(item.get("rating"))
        row: dict[str, Any] = {
            "num": num, "name": _named(clean_text(item.get("title")) or "?", item.get("year")),
            "series_id": sid, "cover": str(item.get("icon") or ""), "plot": plot,
            "cast": clean_text(item.get("cast")), "director": clean_text(item.get("director")),
            "genre": clean_text(item.get("genre")), "releaseDate": str(item.get("released") or "")[:10],
            "last_modified": str(int(item.get("updated") or item.get("added") or 0)),
            "rating": str(round(rating * 2, 1)) if rating else "", "rating_5based": rating,
            "backdrop_path": list(item.get("backdrop") or []), "youtube_trailer": "",
            "episode_run_time": "", "category_id": str(item.get("category") or ""),
            "category_ids": [], "_ms_id": str(item["key"]), "_played": bool(item.get("played"))}
        tmdb = to_int(item.get("tmdb"))
        if tmdb:
            row["_tmdb_id"] = tmdb
        return row

    def _episode_id(self, key: str) -> int:
        eid = self._ep_ids.get(key)
        if eid is None:
            eid = self._ep_ids[key] = stable_id("e:" + key, self._ep_taken)
        return eid

    def _episode_row(self, item: dict) -> dict:
        key = str(item["key"])
        secs = float(item.get("secs") or 0)
        plot = clean_text(item.get("plot"), lines=True)
        try:
            season = int(item.get("season") or 0)
        except (TypeError, ValueError):
            season = 0
        try:
            number = int(item.get("episode") or 0)
        except (TypeError, ValueError):
            number = 0
        row: dict[str, Any] = {
            "id": self._episode_id(key), "episode_num": number,
            "title": clean_text(item.get("title")) or tr("misc_episode"),
            "container_extension": str(item.get("ext") or "mkv").lstrip(".") or "mkv",
            "info": {"movie_image": str(item.get("icon") or ""), "plot": plot,
                     "duration_secs": int(secs), "duration": _secs_text(secs),
                     "releasedate": str(item.get("released") or "")[:10], "rating": ""},
            "season": season, "custom_sid": "", "added": str(int(item.get("added") or 0)),
            "direct_source": "", "_url": str(item.get("url") or ""), "_ms_id": key}
        self._progress(row, item)
        old = self._episodes.get(row["id"])
        if old is not None:
            # The same dict, so a list the window holds sees the change (and
            # keeps what the window put on it: its name, its artwork).
            if "_progress_pct" not in row:
                old.pop("_progress_pct", None)
            old.update(row)
            row = old
        self._episodes[row["id"]] = row
        self._by_key[key] = row
        return row

    def _take_media(self, cat: dict) -> None:
        taken = set(self._rec_by_id)
        ids: dict[str, int] = {}
        movies: list[dict] = []
        by_id: dict[int, dict] = {}
        by_key: dict[str, dict] = {}
        for item in cat.get("movies") or []:
            key = str(item.get("key") or "")
            if not key or not item.get("url") or key in ids:
                continue
            sid = ids[key] = stable_id("m:" + key, taken)
            row = self._movie_row(item, sid, len(movies) + 1)
            movies.append(row)
            by_id[sid] = row
            by_key[key] = row
        shows: list[dict] = []
        show_by_id: dict[int, dict] = {}
        staken: set[int] = set()
        sids: dict[str, int] = {}
        for item in cat.get("series") or []:
            key = str(item.get("key") or "")
            if not key or key in sids:
                continue
            sid = sids[key] = stable_id("s:" + key, staken)
            row = self._series_row(item, sid, len(shows) + 1)
            shows.append(row)
            show_by_id[sid] = row
            by_key[key] = row
        for row in self._recs:
            by_key.setdefault("r:" + str(row["stream_id"]), row)
        self._movies, self._movie_by_id = movies, by_id
        self._shows, self._show_by_id = shows, show_by_id
        self._libraries = [(str(i), str(n)) for i, n in cat.get("movie_libraries") or []]
        self._show_libraries = [(str(i), str(n)) for i, n in cat.get("series_libraries") or []]
        self._by_key = by_key
        self._series_cache = {}
        self._info_cache = {}
        self._resume_rows = self._resume(cat.get("resume") or [])

    def _resume(self, items: list[dict]) -> list[dict]:
        """The server's "continue watching" as the app's Continue watching
        rows (services/resume.py's shape): a film's own row, an episode's
        with its series to play it from."""
        out: list[dict] = []
        shows = {r["_ms_id"]: r for r in self._shows}
        for item in items:
            key = str(item.get("key") or "")
            pos = float(item.get("pos") or 0)
            if not key or pos <= 0:
                continue
            series_key = str(item.get("series_key") or "")
            if series_key:
                show = shows.get(series_key)
                if show is None or not item.get("url"):
                    continue
                ep = self._episode_row(item)
                row = dict(ep, _kind="episode")
                ctx = {k: show[k] for k in ("series_id", "name", "cover", "category_id")}
                if show.get("_tmdb_id"):
                    ctx["_tmdb_id"] = show["_tmdb_id"]
                row["_series_ctx"] = ctx
                row["stream_icon"] = ep["info"]["movie_image"] or show["cover"]
                row["cover"] = show["cover"]
                label = f"S{ep['season']} * E{ep['episode_num']} - {ep['title']}"
                row["name"] = f"{show['name']} · {label}"
                row["_series_title"] = show["name"]
            else:
                movie = self._by_key.get(key)
                if movie is None or "stream_id" not in movie:
                    continue
                self._progress(movie, item)
                row = dict(movie, _kind="vod")
            secs = float(item.get("secs") or 0)
            row["_resume_pos"] = pos
            row["_resume_dur"] = secs
            row["_progress_pct"] = round(100 * pos / secs) if secs else 0
            row["_ts"] = int(item.get("last_played") or 0)
            out.append(row)
        out.sort(key=lambda r: r.get("_ts", 0), reverse=True)
        return out

    # -- interface ------------------------------------------------------------

    def link_ready(self, method: str, ident: int | str) -> bool:
        if method == "live_url" and self._loaded:
            row = self._channel(ident)
            if row is not None and row.get("_open"):
                return self._fresh_live(row["stream_id"]) is not None
        return super().link_ready(method, ident)

    def _fresh_live(self, sid: int) -> str | None:
        with self._live_lock:
            hit = self._live_cache.get(sid)
        if hit and time.monotonic() - hit[0] < self.LIVE_TTL:
            return hit[1]
        return None

    #: A channel that opens has an address of its own to hand out, played as
    #: a file and opened by the server then (Jellyfin's /Videos/<id>/stream);
    #: Plex's is the tune itself.
    LIVE_FILE = True

    def copy_url(self, stream_id: int | str) -> str:
        """A live channel's address to copy (Nerd), with nothing asked of
        the server: never PlaybackInfo or a tune, which open the channel -
        a tuner - for an address nobody may ever play. One that opens gives
        the address it plays from as a file where the server has one, else
        the one opened a moment ago; "" when there is neither."""
        row = self._channel(stream_id) if self._loaded else None
        if row is None:
            return ""
        if not row.get("_open") or (self.LIVE_FILE and row.get("_url")):
            return str(row.get("_url") or "")
        return self._fresh_live(int(row["stream_id"])) or ""

    def live_url(self, stream_id: int | str, fmt: str = "ts") -> str:
        self._ensure(refresh=False)
        row = self._channel(stream_id)
        if row is None:
            return ""
        if not row.get("_open"):
            return str(row.get("_url") or "")
        sid = int(row["stream_id"])
        hit = self._fresh_live(sid)
        if hit is not None:
            return hit
        try:
            url = self._live_open(row)
        except Exception as e:
            log.warning("%s: the channel did not open - %s: %s", self.LOG_NAME,
                        type(e).__name__, e)
            url = str(row.get("_url") or "")
        with self._live_lock:
            self._live_cache[sid] = (time.monotonic(), url)
        return url

    def vod_categories(self) -> list[dict]:
        self._ensure()
        out = [{"category_id": i, "category_name": n} for i, n in self._libraries]
        if self._recs:
            out.append({"category_id": RECORDINGS, "category_name": tr("ms_recordings")})
        return out

    def vod_streams(self, category_id: str | None = None) -> list[dict]:
        self._ensure()
        if category_id is None:
            return list(self._movies) + list(self._recs)
        if category_id == RECORDINGS:
            return list(self._recs)
        return [r for r in self._movies if r["category_id"] == str(category_id)]

    def _movie_of(self, vod_id: int | str) -> dict | None:
        try:
            return self._movie_by_id.get(int(vod_id))
        except (TypeError, ValueError):
            return None

    def vod_info(self, vod_id: int | str) -> dict:
        row = self._movie_of(vod_id)
        if row is None:
            return super().vod_info(vod_id)
        sid = int(row["stream_id"])
        extra = self._info_cache.get(sid)
        if extra is None:
            try:
                extra = self._details(row["_ms_id"]) or {}
            except Exception as e:
                log.debug("%s: no details for a film (%s)", self.LOG_NAME, e)
                extra = {}
            self._info_cache[sid] = extra
        info = dict(row["_info"], name=row["name"], movie_image=row["stream_icon"],
                    cover_big=row["stream_icon"])
        for k, v in extra.items():
            if v:
                info[k] = v
        data = {k: row[k] for k in ("stream_id", "name", "added", "category_id",
                                    "container_extension", "custom_sid", "direct_source")}
        return {"info": info, "movie_data": data}

    def vod_url(self, stream_id: int | str, ext: str | None = None) -> str:
        self._ensure(refresh=False)
        row = self._movie_of(stream_id)
        if row is not None:
            return str(row.get("_url") or "")
        return super().vod_url(stream_id, ext)

    def series_categories(self) -> list[dict]:
        self._ensure()
        return [{"category_id": i, "category_name": n} for i, n in self._show_libraries]

    def series_list(self, category_id: str | None = None) -> list[dict]:
        self._ensure()
        if category_id is None:
            return list(self._shows)
        return [r for r in self._shows if r["category_id"] == str(category_id)]

    def series_info(self, series_id: int | str) -> dict:
        self._ensure(refresh=False)
        try:
            show = self._show_by_id.get(int(series_id))
        except (TypeError, ValueError):
            show = None
        if show is None:
            return {}
        sid = int(show["series_id"])
        hit = self._series_cache.get(sid)
        if hit and time.monotonic() - hit[0] < self.SERIES_TTL:
            return hit[1]
        episodes: dict[str, list[dict]] = {}
        for item in self._episodes_of(show["_ms_id"]):
            if not item.get("key") or not item.get("url"):
                continue
            row = self._episode_row(item)
            episodes.setdefault(str(row["season"]), []).append(row)
        for eps in episodes.values():
            eps.sort(key=lambda e: (e["episode_num"] or 10 ** 6, e["title"].lower()))
        ordered = {s: episodes[s] for s in sorted(episodes, key=lambda s: (int(s) == 0, int(s)))}
        seasons = [{"season_number": int(s), "episode_count": len(eps)} for s, eps in ordered.items()]
        info = {k: show.get(k) for k in ("name", "cover", "plot", "cast", "director", "genre",
                                         "releaseDate", "rating", "backdrop_path", "category_id")}
        out = {"seasons": seasons, "info": info, "episodes": ordered}
        self._series_cache[sid] = (time.monotonic(), out)
        return out

    def episode_url(self, episode_id: int | str, ext: str | None = None) -> str:
        try:
            row = self._episodes.get(int(episode_id))
        except (TypeError, ValueError):
            row = None
        return str((row or {}).get("_url") or "")

    def resume_items(self) -> list[dict]:
        """The server's partly watched films and episodes as Continue
        watching rows, newest first (copies: the window may change them)."""
        return [dict(r) for r in self._resume_rows]

    def server_state(self, kind: str, key: Any) -> dict | None:
        """What the server said of a title the app keys as *key*: {"pos",
        "played", "last_played"}, or None when it is not one of the server's."""
        row: dict | None = None
        try:
            if kind in ("movie", "vod"):
                row = self._movie_by_id.get(int(key))
            elif kind == "episode":
                row = self._episodes.get(int(key))
        except (TypeError, ValueError):
            row = None
        if row is None or "_played" not in row:
            return None
        return {"pos": float(row.get("_server_pos") or 0), "played": bool(row.get("_played")),
                "last_played": int(row.get("_last_played") or 0)}

    # -- what is played, back to the server -----------------------------------

    def report_target(self, key: Any, kind: str, item: dict | None, url: str) -> dict | None:
        """What a report about this playback names: the server's item id and
        what the play address says of the session. None for something that
        is not the server's."""
        it = item or {}
        ms = str(it.get("_ms_id") or "")
        if not ms:
            row: dict | None = None
            try:
                if kind == "movie":
                    row = self._movie_by_id.get(int(key)) or self._rec_by_id.get(int(key))
                elif kind == "episode":
                    row = self._episodes.get(int(key))
                elif kind == "live":
                    row = self._by_id.get(int(key))
            except (TypeError, ValueError):
                row = None
            ms = str((row or {}).get("_ms_id") or (row or {}).get("_key") or "")
        if not ms:
            ms = self.item_of_url(url)
        if not ms:
            return None
        q = query_of(url)
        try:
            hls = urlsplit(url or "").path.lower().endswith(".m3u8")
        except ValueError:
            hls = False
        return {"id": ms, "kind": kind, "query": q, "hls": hls,
                "session": q.get("playsessionid") or q.get("session") or uuid.uuid4().hex}

    def item_of_url(self, url: str) -> str:
        """The server's item id in one of its own play addresses, or ""."""
        return ""

    def report(self, event: str, target: dict, pos: float, dur: float,
               paused: bool = False) -> None:
        """Queue a playback report - "start", "progress" or "stop" - never
        waits. A stop also moves the row's resume point here at once."""
        self._reporter.put(("report", event, dict(target), float(pos or 0), float(dur or 0),
                            bool(paused)))
        if event == "stop":
            self._note(target, pos, dur, None)

    def mark(self, target: dict, played: bool) -> None:
        """Queue a watched (or not) mark for the server."""
        self._reporter.put(("mark", bool(played), dict(target)))
        self._note(target, 0.0, 0.0, played)

    def flush_reports(self, timeout: float = 1.0) -> bool:
        return self._reporter.wait(timeout)

    def _note(self, target: dict, pos: float, dur: float, played: bool | None) -> None:
        """The row the server will soon say this of, changed now."""
        row = self._by_key.get(str(target.get("id") or ""))
        if row is None or "_played" not in row:
            return
        if played is not None:
            row["_played"] = played
            row["_server_pos"] = 0.0
            row.pop("_progress_pct", None)
        else:
            dur = dur or float(row.get("_server_dur") or 0)
            done = bool(dur) and pos >= dur * 0.9
            item = {"secs": dur, "pos": 0.0 if done else pos,
                    "played": True if done else row.get("_played"), "last_played": int(time.time())}
            self._progress(row, item)
        row["_last_played"] = int(time.time())
        self._resume_rows = [r for r in self._resume_rows if r.get("_ms_id") != row.get("_ms_id")]
