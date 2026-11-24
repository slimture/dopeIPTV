"""A source's lists as offline mode shows them (core/offline.py): what is
downloaded of it (core/downloads.py), read from the downloads' sidecars -
nothing asked of the provider, no network at all.

``DownloadedClient`` stands where the source's own client stood while
offline mode is on (ui/mw_offline.py) and answers every call a client has
(OfflineClient's): no channels and no guide; the films and the series with
a downloaded episode, as the lists' rows of them (``downloads.as_row``,
``series_row``) - so the Movies and Series places, a series' page, the
parental lock, a kids profile's limits and the phone remote's lists work on
them as on the source's own. Their categories are those the sidecars name
(an id with the provider's own name for it); a title whose sidecar names no
category's name is under one of the app's own, "Downloaded" (``CATEGORY``),
with its own category's id kept on its row - so the lock still knows it,
and a kids profile, whose lists show no row of a category the source does
not list, does not see it (it fails closed). A series' page lists its
downloaded episodes, and those the app knew of it before without one -
marked ``_dl_missing``, shown dimmed and never played - from what the
window kept of its episodes (``known``). An address is a downloaded file's
path; nothing else plays.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from ..core import downloads as dl
from .client import OfflineClient

#: The category of the app's own for a title whose sidecar names none.
CATEGORY = "__downloaded__"

#: (series id) -> the episodes the app knew of a series without a download
#: (core/downloads.known_episodes' rows), [] for none.
Known = Callable[[str], list[dict]]


class DownloadedClient(OfflineClient):
    """The source *source*'s downloads as its lists (the module's docstring).
    *category* - the words of the app's own category ("Downloaded")."""

    #: Says to the window that its lists are offline mode's.
    offline = True

    def __init__(self, downloads: dl.Downloads | None, source: str, category: str = "",
                 known: Known | None = None) -> None:
        super().__init__()
        self.downloads = downloads
        self.source = str(source or "")
        self.category = category or "Downloaded"
        self.known = known

    # -- what is downloaded of this source -------------------------------------------

    def _items(self, kind: str) -> list[dl.Item]:
        d = self.downloads
        if d is None or not self.source:
            return []
        if not d.scanned:
            try:
                d.scan()                # a worker's thread: the folder read now
            except Exception:
                return []
        return [i for i in d.items() if i.source == self.source and i.kind == kind]

    def _categories(self, items: list[dl.Item], id_key: str, name_key: str) -> list[dict]:
        named: dict[str, str] = {}
        unnamed = False
        for i in items:
            cid, name = i.info.get(id_key), i.info.get(name_key)
            if cid not in (None, "") and isinstance(name, str) and name.strip():
                named.setdefault(str(cid), name.strip())
            else:
                unnamed = True
        cats = [{"category_id": cid, "category_name": name}
                for cid, name in sorted(named.items(), key=lambda kv: kv[1].lower())]
        if unnamed:
            cats.append({"category_id": CATEGORY, "category_name": self.category})
        return cats

    @staticmethod
    def _in(item: dl.Item, category_id: Any, id_key: str, name_key: str) -> bool:
        if category_id in (None, ""):
            return True
        cid, name = item.info.get(id_key), item.info.get(name_key)
        has = cid not in (None, "") and isinstance(name, str) and bool(name.strip())
        if str(category_id) == CATEGORY:
            return not has
        return has and str(cid) == str(category_id)

    # -- films ----------------------------------------------------------------------

    def vod_categories(self) -> list[dict]:
        return self._categories(self._items("movie"), "category_id", "category_name")

    def vod_streams(self, category_id: str | None = None) -> list[dict]:
        items = [i for i in self._items("movie")
                 if self._in(i, category_id, "category_id", "category_name")]
        return [dl.as_row(i) for i in sorted(items, key=lambda i: i.title.lower())]

    def vod_info(self, vod_id: int | str) -> dict:
        item = self._find("movie", vod_id)
        if item is None:
            return {}
        row = dl.as_row(item)
        info: dict[str, Any] = {"name": item.title, "plot": row.get("plot") or "",
                                "movie_image": row.get("stream_icon") or ""}
        if row.get("year"):
            info["releasedate"] = str(row["year"])
        if row.get("_dl_length"):
            info["duration_secs"] = row["_dl_length"]
        if row.get("genre"):
            info["genre"] = row["genre"]
        return {"info": info, "movie_data": {"stream_id": item.stream_id, "name": item.title}}

    def vod_url(self, stream_id: int | str, ext: str | None = None) -> str:
        item = self._find("movie", stream_id)
        return item.path if item is not None else ""

    # -- series ---------------------------------------------------------------------

    def _series(self) -> dict[str, list[dl.Item]]:
        groups: dict[str, list[dl.Item]] = {}
        for i in self._items("episode"):
            key = i.series_id or str(i.info.get("series_title") or "")
            if key:
                groups.setdefault(key, []).append(i)
        return groups

    def series_categories(self) -> list[dict]:
        firsts = [eps[0] for eps in self._series().values()]
        return self._categories(firsts, "series_category_id", "series_category_name")

    def series_list(self, category_id: str | None = None) -> list[dict]:
        rows = []
        for key, eps in self._series().items():
            first = eps[0]
            if not self._in(first, category_id, "series_category_id", "series_category_name"):
                continue
            row = dl.series_row(first)
            row["series_id"] = row.get("series_id") or key
            row["_dl_source"] = self.source
            row["last_modified"] = str(int(max(e.added for e in eps)))
            rows.append(row)
        return sorted(rows, key=lambda r: str(r.get("name") or "").lower())

    def series_info(self, series_id: int | str) -> dict:
        eps = self._series().get(str(series_id)) or []
        if not eps:
            return {}
        first = eps[0]
        series = dl.series_row(first)
        seasons: dict[str, list[dict]] = {}
        have: set[str] = set()
        for e in eps:
            row = dl.as_row(e)
            ep = {"id": row["id"], "episode_num": row.get("episode_num"),
                  "title": row.get("title") or e.title, "info": dict(row.get("info") or {}),
                  "container_extension": os.path.splitext(e.path)[1].lstrip(".") or "mkv",
                  "_dl_path": e.path, "_dl_size": e.size, "_dl_source": self.source}
            if row.get("stream_icon"):
                ep["info"]["movie_image"] = row["stream_icon"]
            have.add(str(row["id"]))
            seasons.setdefault(str(row.get("season") if row.get("season") is not None
                                   else "0"), []).append(ep)
        for k in self._known(str(series_id)):
            eid = str(k.get("id") or "")
            if not eid or eid in have:
                continue
            have.add(eid)
            season = k.get("season")
            seasons.setdefault(str(season if season is not None else "0"), []).append(
                {"id": eid, "episode_num": k.get("episode"), "title": k.get("title") or "",
                 "info": {}, "_dl_missing": True})
        for rows in seasons.values():
            rows.sort(key=lambda r: (dl._whole(r.get("episode_num")) or 10 ** 6,
                                     str(r.get("title") or "")))
        info = {"name": series.get("name") or "", "plot": series.get("plot") or "",
                "cover": series.get("cover") or ""}
        if first.info.get("series_year"):
            info["releaseDate"] = str(first.info["series_year"])
        return {"info": info, "seasons": [], "episodes": seasons}

    def _known(self, series_id: str) -> list[dict]:
        if self.known is None:
            return []
        try:
            got = self.known(series_id)
        except Exception:
            return []
        return [k for k in got or [] if isinstance(k, dict)]

    def episode_url(self, episode_id: int | str, ext: str | None = None) -> str:
        item = self._find("episode", episode_id)
        return item.path if item is not None else ""

    # -- a downloaded title --------------------------------------------------------

    def _find(self, kind: str, stream_id: Any) -> dl.Item | None:
        d = self.downloads
        if d is None or stream_id in (None, ""):
            return None
        hit = d.find(self.source, kind, stream_id)
        return hit if hit is not None and os.path.isfile(hit.path) else None
