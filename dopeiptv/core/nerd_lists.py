"""Nerd's touches on a source's channel list: logos from a local picon
folder, a channel's guide chosen by hand, the user's own channel numbers
(core/channel_numbers.py), multicast through udpxy.

All of it applies only with Nerd on, and only what is set: a list from a
source with none of these is returned as it came.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import unicodedata
from typing import Any
from urllib.parse import urlsplit

from . import channel_numbers, nerd
from .log import log

PICON_EXTS = (".png", ".svg", ".webp", ".jpg")
_index_lock = threading.Lock()
_index: dict[str, Any] = {"dir": "", "mtime": 0.0, "by_name": {}}


def snp_name(name: str) -> str:
    """A channel name as service-name picons spell it (SNP): lower case,
    accents off, & + * written out, nothing but letters and digits."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii").lower()
    s = s.replace("&", "and").replace("+", "plus").replace("*", "star")
    return re.sub(r"[^a-z0-9]", "", s)


def _picon_names(row: dict) -> list[str]:
    """The file names a channel's picon may have: the service reference
    form (1_0_19_283D_...) for an Enigma2 channel, then its SNP name, with
    and without a trailing "hd"."""
    names = []
    key = str(row.get("_key") or "")
    parts = key.split(":")
    if len(parts) >= 10 and parts[0].isdigit():
        f = parts[:10]
        f[0] = "1"
        names.append("_".join(f).strip("_").upper())
    snp = snp_name(str(row.get("name") or ""))
    if snp:
        names.append(snp)
        if snp.endswith("hd") and len(snp) > 2:
            names.append(snp[:-2])
    return names


def picon_index(folder: str) -> dict[str, str]:
    """{file name without extension (lower case): path} of a picon folder,
    read again when the folder changes."""
    folder = os.path.expanduser(folder or "")
    try:
        mtime = os.stat(folder).st_mtime
    except OSError:
        return {}
    with _index_lock:
        if _index["dir"] == folder and _index["mtime"] == mtime:
            return _index["by_name"]
        by_name: dict[str, str] = {}
        try:
            for entry in os.scandir(folder):
                stem, ext = os.path.splitext(entry.name)
                if ext.lower() in PICON_EXTS and entry.is_file():
                    by_name.setdefault(stem.lower(), entry.path)
        except OSError as e:
            log.info("nerd: picon folder %s unreadable: %s", folder, e)
        _index.update(dir=folder, mtime=mtime, by_name=by_name)
        log.info("nerd: %d picon(s) in %s", len(by_name), folder)
        return by_name


def logo_url(path: str) -> str:
    """A local file as the logo loader takes it."""
    return path if not sys.platform.startswith("win") else "file://" + path


def epg_map(pid: str | None) -> dict[str, str]:
    """{stream_id: guide channel id} chosen by hand for one source."""
    n = nerd.current()
    if not (n.on and pid):
        return {}
    try:
        data = json.loads(n.get(f"nerd_epg_map_{pid}") or "{}")
    except ValueError:
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def set_epg_map(settings: Any, pid: str, stream_id: int | str, epg_id: str | None) -> None:
    key = f"nerd_epg_map_{pid}"
    try:
        data = json.loads(settings.value(key, "") or "{}")
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if epg_id:
        data[str(stream_id)] = epg_id
    else:
        data.pop(str(stream_id), None)
    settings.setValue(key, json.dumps(data))


def decorate(rows: list[dict], source: dict | None, pid: str | None) -> list[dict]:
    """A source's channels with Nerd's picons, guide mapping and the
    user's channel numbers applied (the source's numbers come back with
    Nerd off)."""
    channel_numbers.apply(rows, pid)
    n = nerd.current()
    if not n.on or not rows:
        return rows
    folder = n.get("nerd_picon_dir").strip()
    index = picon_index(folder) if folder else {}
    override = n.flag("nerd_picon_override")
    mapping = epg_map(pid)
    if not index and not mapping:
        return rows
    for row in rows:
        if index and (override or not row.get("stream_icon")):
            for name in _picon_names(row):
                path = index.get(name.lower())
                if path:
                    row["stream_icon"] = logo_url(path)
                    break
        if mapping:
            mapped = mapping.get(str(row.get("stream_id")))
            if mapped:
                if "_epg_own" not in row:
                    row["_epg_own"] = row.get("epg_channel_id") or ""
                row["epg_channel_id"] = mapped
    return rows


def udpxy_url(url: str, source: dict | None = None) -> str:
    """udp:// and rtp:// multicast through a udpxy proxy, where one is set
    (the source's own, else the Nerd page's): udp://@239.1.1.1:1234
    becomes http://router:4022/udp/239.1.1.1:1234."""
    if not url or not url.lower().startswith(("udp://", "rtp://")):
        return url
    n = nerd.current()
    if not n.on:
        return url
    proxy = str((source or {}).get("udpxy") or n.get("nerd_udpxy") or "").strip().rstrip("/")
    if not proxy:
        return url
    if "://" not in proxy:
        proxy = "http://" + proxy
    u = urlsplit(url)
    target = u.netloc.lstrip("@")
    if not target:
        return url
    return f"{proxy}/{u.scheme.lower()}/{target}"
