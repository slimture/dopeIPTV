"""dopeIPTV as a server (Nerd > Remote and sharing): every source's live
channels as one M3U playlist, and the guides the app has downloaded as one
XMLTV guide, for another player on the network - Kodi, TiviMate, VLC, a
television's IPTV app.

Each channel in the playlist is an address on this app
(/stream/<source>/<channel>) that sends the player on to the source's own
address at the moment it asks: a Stalker portal's link is made then, an
Xtream address is the usual one. The player fetches the stream itself.
Only channels the app listed can be asked for; nothing in a request is
handed to a source unchecked.
"""

from __future__ import annotations

import codecs
import hashlib
import json
import re
import threading
import time
import zlib
from typing import Any, Callable, Iterator

from ..core.log import log
from ..core.remote_api import stream_path

_STEP = 1 << 18
# Held back from the end of each guide: room for "</tv>" and what trails it,
# and, when a guide was cut short, for the element it was cut in.
_HOLD = 1 << 16
_SELF_CLOSING = re.compile(rb"<channel\b[^<>]{0,4096}/>")


def _attr(value: Any) -> str:
    return re.sub(r"[\s\"]+", " ", str(value or "")).strip()


class Share:
    LIST_TTL = 600.0

    def __init__(self, sources: Callable[[], list[dict]],
                 make_client: Callable[[dict], Any] | None = None,
                 guide_path: Callable[[str], str] | None = None,
                 ttl: float = LIST_TTL) -> None:
        self._sources = sources
        self._make = make_client
        self._guide_path = guide_path
        self.ttl = ttl
        self._lock = threading.Lock()
        self._clients: dict[str, tuple[str, Any]] = {}
        self._lists: dict[str, tuple[float, list[dict], dict[str, str]]] = {}

    # -- sources and their lists ---------------------------------------------
    def shared_sources(self) -> list[dict]:
        return [p for p in (self._sources() or []) if p.get("id") and p.get("kind") != "local"]

    def _client(self, pl: dict) -> Any:
        stamp = hashlib.sha1(json.dumps(pl, sort_keys=True, default=str).encode()).hexdigest()
        pid = str(pl["id"])
        with self._lock:
            held = self._clients.get(pid)
            if held is not None and held[0] == stamp:
                return held[1]
        if self._make is None:
            from ..providers.client import make_client
            self._make = make_client
        client = self._make(pl)
        with self._lock:
            self._clients[pid] = (stamp, client)
            self._lists.pop(pid, None)
        return client

    def channels(self, pl: dict) -> tuple[list[dict], dict[str, str]]:
        """A source's channels and its category names, kept a while."""
        pid = str(pl["id"])
        client = self._client(pl)
        with self._lock:
            held = self._lists.get(pid)
            if held is not None and time.monotonic() - held[0] < self.ttl:
                return held[1], held[2]
        rows = [r for r in (client.live_streams(None) or []) if r.get("stream_id") is not None]
        cats: dict[str, str] = {}
        try:
            for c in client.live_categories() or []:
                cats[str(c.get("category_id"))] = str(c.get("category_name") or "")
        except Exception as e:
            log.info("share: %s has no categories: %s", pl.get("name"), e)
        with self._lock:
            self._lists[pid] = (time.monotonic(), rows, cats)
        return rows, cats

    # -- the playlist ------------------------------------------------------------
    def m3u(self, base: str, token: str) -> str:
        sources = self.shared_sources()
        q = f"?token={token}"
        out = [f'#EXTM3U url-tvg="{base}/guide.xml.gz{q}" x-tvg-url="{base}/guide.xml.gz{q}"']
        several = len(sources) > 1
        for pl in sources:
            name = _attr(pl.get("name") or pl.get("server") or pl.get("id"))
            try:
                rows, cats = self.channels(pl)
            except Exception as e:
                log.info("share: %s left out (%s)", name, e)
                continue
            for r in rows:
                title = _attr(r.get("name")) or "?"
                cat = _attr(cats.get(str(r.get("category_id")), ""))
                group = f"{name} · {cat}" if several and cat else (cat or name)
                attrs = []
                if r.get("epg_channel_id"):
                    attrs.append(f'tvg-id="{_attr(r["epg_channel_id"])}"')
                attrs.append(f'tvg-name="{title}"')
                if r.get("stream_icon"):
                    attrs.append(f'tvg-logo="{_attr(r["stream_icon"])}"')
                if r.get("num"):
                    attrs.append(f'tvg-chno="{_attr(r["num"])}"')
                attrs.append(f'group-title="{group}"')
                out.append(f"#EXTINF:-1 {' '.join(attrs)},{title}")
                out.append(base + stream_path(pl["id"], r["stream_id"]) + q)
        return "\n".join(out) + "\n"

    def stream_url(self, source_id: str, stream_id: str) -> str | None:
        """The source's own address for a channel this app listed, or None."""
        pl = next((p for p in self.shared_sources() if str(p.get("id")) == source_id), None)
        if pl is None:
            return None
        rows, _cats = self.channels(pl)
        row = next((r for r in rows if str(r.get("stream_id")) == stream_id), None)
        if row is None:
            return None
        url = self._client(pl).live_url(row["stream_id"], "ts")
        log.info("share: %s from %s to a player", row.get("name"), pl.get("name"))
        return str(url) if url else None

    # -- the guide -------------------------------------------------------------------
    def guide(self, gz: bool = False) -> Iterator[bytes]:
        """The guides the app has for the shared sources, one after the
        other inside one <tv>, read and written a slice at a time (a guide
        is often hundreds of MB)."""
        pieces = self._guide_pieces()
        if not gz:
            yield from pieces
            return
        c = zlib.compressobj(6, zlib.DEFLATED, 31)
        for p in pieces:
            out = c.compress(p)
            if out:
                yield out
        yield c.flush()

    def _guide_pieces(self) -> Iterator[bytes]:
        if self._guide_path is None:
            from ..providers.epg import epg_cache_path
            self._guide_path = epg_cache_path
        yield (b'<?xml version="1.0" encoding="UTF-8"?>\n'
               b'<!DOCTYPE tv SYSTEM "xmltv.dtd">\n<tv generator-info-name="dopeIPTV">\n')
        for pl in self.shared_sources():
            path = self._guide_path(str(pl["id"]))
            try:
                yield from guide_body(plain_chunks(path))
            except FileNotFoundError:
                continue
            except (OSError, ValueError, zlib.error) as e:
                log.info("share: %s's guide left out (%s)", pl.get("name"), e)
            yield b"\n"
        yield b"</tv>\n"


def plain_chunks(path: str) -> Iterator[bytes]:
    """A cached guide's XML, gzip or not, every gzip member of it."""
    with open(path, "rb") as fh:
        head = fh.read(2)
        fh.seek(0)
        if head != b"\x1f\x8b":
            while True:
                b = fh.read(_STEP)
                if not b:
                    return
                yield b
        d = zlib.decompressobj(31)
        pending = b""
        started = False
        while True:
            data = pending + fh.read(_STEP)
            pending = b""
            if not data:
                if started:
                    raise ValueError("the guide was cut short")
                return
            while data:
                started = True
                out = d.decompress(data)
                if out:
                    yield out
                if not d.eof:
                    data = b""
                    continue
                rest = d.unused_data
                d = zlib.decompressobj(31)
                started = False
                if len(rest) < 2:
                    pending, data = rest, b""
                elif rest[:2] == b"\x1f\x8b":
                    data = rest
                else:
                    return                  # padding after the last member


def _open_tag_end(buf: bytes) -> int | None:
    """Where the root <tv ...> ends in *buf*: an index, -1 for <tv/>, None
    when more is needed."""
    start = 0
    while True:
        i = buf.find(b"<tv", start)
        if i < 0 or i + 3 >= len(buf):
            return None
        if buf[i + 3:i + 4] in (b" ", b"\t", b"\r", b"\n", b">", b"/"):
            j = buf.find(b">", i)
            if j < 0:
                return None
            return -1 if buf[j - 1:j] == b"/" else j + 1
        start = i + 3


def guide_body(chunks: Iterator[bytes]) -> Iterator[bytes]:
    """What is inside a guide's <tv>, as UTF-8, a slice at a time."""
    it = iter(chunks)
    buf = b""
    pos: int | None = None
    for c in it:
        buf += c
        pos = _open_tag_end(buf)
        if pos is not None:
            break
        if len(buf) > (1 << 20):
            raise ValueError("not an XMLTV guide")
    if pos is None:
        if buf.strip():
            raise ValueError("not an XMLTV guide")
        return
    if pos < 0:
        return
    m = re.search(rb"""encoding\s*=\s*["']([A-Za-z0-9._-]{1,40})["']""", buf[:pos])
    enc = (m.group(1).decode("ascii") if m else "utf-8").lower()
    decode = None
    if enc not in ("utf-8", "utf8", "us-ascii", "ascii"):
        try:
            decode = codecs.getincrementaldecoder(enc)("replace")
        except LookupError:
            decode = None

    def conv(data: bytes, final: bool = False) -> bytes:
        return decode.decode(data, final).encode("utf-8") if decode is not None else data

    tail = buf[pos:]
    cut_short = False
    try:
        for c in it:
            data = tail + c
            if len(data) <= _HOLD:
                tail = data
                continue
            tail = data[-_HOLD:]
            yield conv(data[:-_HOLD])
    except (ValueError, zlib.error):
        cut_short = True
    end = tail.rfind(b"</tv>")
    if end >= 0 and not cut_short:
        yield conv(tail[:end], True)
        return
    # Cut short (a download that stopped, a damaged cache): what was whole.
    yield conv(tail[:_last_whole(tail)], True)
    raise ValueError("the guide was cut short; what was whole is kept")


def _last_whole(data: bytes) -> int:
    """Where the last complete channel or programme in *data* ends (0: none)."""
    ends = [data.rfind(b"</programme>") + len(b"</programme>") if b"</programme>" in data else 0,
            data.rfind(b"</channel>") + len(b"</channel>") if b"</channel>" in data else 0]
    ends += [m.end() for m in _SELF_CLOSING.finditer(data)][-1:]
    return max(ends)
