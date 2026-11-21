"""dopeIPTV as a server (Nerd > Remote and sharing): every source's live
channels as one M3U playlist, and the guides the app has downloaded as one
XMLTV guide, for another player on the network - Kodi, TiviMate, VLC, a
television's IPTV app.

Each channel in the playlist is an address on this app
(/stream/<source>/<channel>) that sends the player on to the source's own
address at the moment it asks: a Stalker portal's link is made then, an
Xtream address is the usual one. The player fetches the stream itself.
Only channels the app listed can be asked for, and of those only what it
hands out - never one hidden, in a hidden or locked category or for adults,
as server mode's lineup (``kept_out``); nothing in a request is handed to a
source unchecked.
"""

from __future__ import annotations

import codecs
import hashlib
import html
import json
import re
import threading
import time
import zlib
from typing import Any, Callable, Iterator

from ..core import safe_xml
from ..core.log import log, redact_url
from ..core.privacy import for_log
from ..core.remote_api import stream_path

_STEP = 1 << 18
# The most one unpacking call gives: a crafted step of a gzipped guide
# would otherwise unpack to hundreds of MB at once (epg.py's _ZLIB_OUT).
_UNPACK_STEP = 1 << 22
# Held back from the end of each guide: room for "</tv>" and what trails it,
# and, when a guide was cut short, for the element it was cut in.
_HOLD = 1 << 16
_SELF_CLOSING = re.compile(rb"<channel\b[^<>]{0,4096}/>")


def _attr(value: Any) -> str:
    return re.sub(r"[\s\"]+", " ", str(value or "")).strip()


#: A source's filters for what is handed out: (the categories hidden or
#: locked, the channels hidden by hand, ...) - a host's ``filters(pid)``.
Filters = Callable[[str], tuple[set[str], set[str], Any]]


def kept_out(row: dict, cats: dict[str, str], excluded: set[str], hidden: set[str],
             adult: bool = False) -> bool:
    """Whether a channel is never handed to another device - server mode's
    lineup and the remote API's share alike: in a hidden or locked category
    (*excluded*: a locked one even while it is unlocked here), hidden by hand
    (*hidden*), or for adults (marked so, or in an adult category) unless
    server mode's setting lets those out (*adult*, ``nerd_server_adult``)."""
    from ..core.profiles import adult_category, adult_item
    cid = str(row.get("category_id"))
    if cid in excluded or str(row.get("stream_id")) in hidden:
        return True
    return not adult and (adult_item(row) or adult_category(cats.get(cid, "")))


class Share:
    LIST_TTL = 600.0

    def __init__(self, sources: Callable[[], list[dict]],
                 make_client: Callable[[dict], Any] | None = None,
                 guide_path: Callable[[str], str] | None = None,
                 ttl: float = LIST_TTL, filters: Filters | None = None,
                 adult: Callable[[], bool] | None = None) -> None:
        self._sources = sources
        #: Server mode's "Channels for adults" (``kept_out``'s *adult*).
        self._adult = adult
        self._make = make_client
        self._guide_path = guide_path
        self._filters = filters
        self.ttl = ttl
        self._lock = threading.Lock()
        self._clients: dict[str, tuple[str, Any]] = {}
        self._lists: dict[str, tuple[float, list[dict], dict[str, str]]] = {}

    # -- sources and their lists ---------------------------------------------
    def shared_sources(self) -> list[dict]:
        return [p for p in (self._sources() or []) if p.get("id") and p.get("kind") != "local"]

    def _client(self, pl: dict) -> Any:
        stamp = hashlib.sha256(json.dumps(pl, sort_keys=True, default=str).encode()).hexdigest()
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
        # A channel listed only for the guide (MythTV's) has nothing to share.
        rows = [r for r in (client.live_streams(None) or [])
                if r.get("stream_id") is not None and not r.get("_no_live")]
        cats: dict[str, str] = {}
        try:
            for c in client.live_categories() or []:
                cats[str(c.get("category_id"))] = str(c.get("category_name") or "")
        except Exception as e:
            log.info("share: %s has no categories: %s", pl.get("name"), e)
        with self._lock:
            self._lists[pid] = (time.monotonic(), rows, cats)
        return rows, cats

    def handed_out(self, pl: dict) -> tuple[list[dict], dict[str, str]]:
        """A source's channels another device may have (``kept_out``), with
        *filters*; all of ``channels`` without. A filter that cannot be read
        raises: the source is left out rather than handed out whole."""
        rows, cats = self.channels(pl)
        if self._filters is None:
            return rows, cats
        excluded, hidden, _numbers = self._filters(str(pl["id"]))
        try:
            adult = bool(self._adult()) if self._adult is not None else False
        except Exception as e:                      # unread: the safe side, off
            log.debug("share: the adult switch not read (%s)", e)
            adult = False
        return [r for r in rows if not kept_out(r, cats, excluded, hidden, adult)], cats

    # -- the playlist ------------------------------------------------------------
    def m3u(self, base: str, token: str) -> str:
        sources = self.shared_sources()
        q = f"?token={token}"
        out = [f'#EXTM3U url-tvg="{base}/guide.xml.gz{q}" x-tvg-url="{base}/guide.xml.gz{q}"']
        several = len(sources) > 1
        for pl in sources:
            # An unnamed source by its server - never with the login an M3U
            # address or a box's carries (core/log.redact_url).
            name = _attr(pl.get("name") or redact_url(pl.get("server") or "") or pl.get("id"))
            try:
                rows, cats = self.handed_out(pl)
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
                logo = public_logo(r.get("stream_icon"))
                if logo:
                    attrs.append(f'tvg-logo="{_attr(logo)}"')
                if r.get("num"):
                    attrs.append(f'tvg-chno="{_attr(r["num"])}"')
                attrs.append(f'group-title="{group}"')
                out.append(f"#EXTINF:-1 {' '.join(attrs)},{title}")
                out.append(base + stream_path(pl["id"], r["stream_id"]) + q)
        return "\n".join(out) + "\n"

    def stream_url(self, source_id: str, stream_id: str) -> str | None:
        """The source's own address for a channel this app listed and hands
        out (``handed_out``), or None."""
        pl = next((p for p in self.shared_sources() if str(p.get("id")) == source_id), None)
        if pl is None:
            return None
        rows, _cats = self.handed_out(pl)
        row = next((r for r in rows if str(r.get("stream_id")) == stream_id), None)
        if row is None:
            return None
        url = self._client(pl).live_url(row["stream_id"], "ts")
        log.info("share: %s from %s to a player", for_log(row.get("name")), pl.get("name"))
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
                yield from logins_out(guide_body(plain_chunks(path)))
            except FileNotFoundError:
                continue
            except (OSError, ValueError, zlib.error) as e:
                log.info("share: %s's guide left out (%s)", pl.get("name"), e)
            yield b"\n"
        yield b"</tv>\n"


def lineup_guide(guides: list[tuple[str, str]], channels: list[dict]) -> Iterator[bytes]:
    """The guides of *guides* - (source id, path) - as one XMLTV guide whose
    channel ids are a lineup's numbers (server mode's HDHomeRun
    lineup.json), so Plex and Jellyfin map it by themselves. Each channel
    of *channels* - {"number", "name", "source", "epg_id", "logo"} - finds
    its guide channel in its own source's guide by id, else by name, and
    gets that channel's programmes under its number; what no lineup channel
    has is left out. Read and written an element at a time (a guide is
    often hundreds of MB)."""
    import xml.etree.ElementTree as ET
    from xml.sax.saxutils import quoteattr

    told: set[str] = set()

    def channel_xml(ch: dict, names: list[str], icon: str) -> bytes:
        num = str(ch["number"])
        out = [f"<channel id={quoteattr(num)}>"]
        for n in dict.fromkeys([str(ch.get("name") or num), num, *names]):
            if n:
                out.append(f"<display-name>{_esc(n)}</display-name>")
        icon = public_logo(icon) or public_logo(ch.get("logo"))
        if icon:
            out.append(f"<icon src={quoteattr(icon)}/>")
        out.append("</channel>\n")
        told.add(num)
        return "".join(out).encode("utf-8")

    yield (b'<?xml version="1.0" encoding="UTF-8"?>\n'
           b'<!DOCTYPE tv SYSTEM "xmltv.dtd">\n<tv generator-info-name="dopeIPTV">\n')
    for source, path in guides:
        by_id: dict[str, list[dict]] = {}
        by_name: dict[str, list[dict]] = {}
        for ch in channels:
            if str(ch.get("source") or "") != source:
                continue
            if ch.get("epg_id"):
                by_id.setdefault(str(ch["epg_id"]), []).append(ch)
            by_name.setdefault(str(ch.get("name") or "").casefold(), []).append(ch)
        if not by_id and not by_name:
            continue
        found: dict[str, list[dict]] = {}   # the guide's channel id -> lineup channels
        parser: ET.XMLPullParser = ET.XMLPullParser(events=("start", "end"))
        # A guide kept as it was downloaded (guide_keeper) is the provider's
        # word: one with a DTD of its own is not parsed (core/safe_xml.py).
        prolog = safe_xml.Prolog("A shared guide")
        depth = 0
        root: Any = None
        try:
            for chunk in plain_chunks(path):
                prolog.feed(chunk)
                parser.feed(chunk)
                out: list[bytes] = []
                events: Any = parser.read_events()
                for event, elem in events:
                    if event == "start":
                        if depth == 0:
                            root = elem
                        depth += 1
                        continue
                    depth -= 1
                    if depth != 1 or root is None:
                        continue
                    if elem.tag == "channel":
                        cid = elem.get("id") or ""
                        names = [(d.text or "").strip() for d in elem.findall("display-name")]
                        mine: list[dict] = by_id.get(cid) or next(
                            (by_name[n.casefold()] for n in names if n.casefold() in by_name), [])
                        mine = [c for c in mine if str(c["number"]) not in told]
                        if mine:
                            found[cid] = found.get(cid, []) + mine
                            icon_el = elem.find("icon")
                            icon = icon_el.get("src", "") if icon_el is not None else ""
                            out += [channel_xml(c, names, icon) for c in mine]
                    elif elem.tag == "programme":
                        ref = elem.get("channel") or ""
                        if found.get(ref) or by_id.get(ref):
                            _public_only(elem)
                        for c in found.get(ref) or by_id.get(ref) or []:
                            elem.set("channel", str(c["number"]))
                            out.append(ET.tostring(elem, encoding="utf-8", xml_declaration=False)
                                       .rstrip() + b"\n")
                    root.clear()
                if out:
                    yield b"".join(out)
        except FileNotFoundError:
            continue
        except (OSError, ValueError, zlib.error, ET.ParseError) as e:
            log.info("server: a guide left out from where it broke off (%s)", e)
    rest = [ch for ch in channels if str(ch["number"]) not in told]
    if rest:
        yield b"".join(channel_xml(c, [], "") for c in rest)
    yield b"</tv>\n"


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def public_logo(url: Any) -> str:
    """*url* when it may be handed to another device as a channel's logo: a
    web address with no login in it - else "". A box's picon carries its
    user:password@ (providers/boxes.with_login), a Plex logo the account's
    X-Plex-Token; a Nerd picon is a file of this computer's."""
    text = str(url or "").strip()
    if not text.lower().startswith(("http://", "https://")):
        return ""
    return text if redact_url(text) == text else ""


def _with_login(text: str) -> bool:
    """Whether an address in a guide carries a login: a user:password@, a
    token or a key (core/log.redact_url would change it)."""
    text = html.unescape(text).strip()
    return bool(text) and redact_url(text) != text


def _public_only(elem: Any) -> None:
    """A programme handed to another device without the pictures and
    links that carry a login (a box's, a Plex or Jellyfin server's guide
    as the app writes it: their addresses with the login or token in)."""
    for child in list(elem):
        if (child.tag == "icon" and _with_login(child.get("src", ""))) or (
                child.tag in ("url", "image") and _with_login(child.text or "")):
            elem.remove(child)


# <icon src="..."/> (or <icon ...></icon>), <url>...</url> and XMLTV 1.1's
# <image>...</image>: what a guide passed on whole may carry a login in.
_ICON = re.compile(rb"""<icon\b[^<>]*?\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)')[^<>]*?"""
                   rb"""(?:/>|>[^<]*</icon\s*>)""")
_LINK = re.compile(rb"<(url|image)\b[^<>]*>([^<]*)</\1\s*>")


def _scrubbed(data: bytes) -> bytes:
    def icon(m: re.Match[bytes]) -> bytes:
        src = (m.group(1) if m.group(1) is not None else m.group(2)) or b""
        return b"" if _with_login(src.decode("utf-8", "replace")) else m.group(0)

    def link(m: re.Match[bytes]) -> bytes:
        return b"" if _with_login(m.group(2).decode("utf-8", "replace")) else m.group(0)
    return _LINK.sub(link, _ICON.sub(icon, data))


def _end_of(data: bytes, at: int, name: bytes) -> int | None:
    """Where the element that starts at *at* ends in *data* - its tag
    closed with "/>", or its end tag - or None while it goes on."""
    end = data.find(b">", at)
    if end < 0:
        return None
    if data[end - 1:end] == b"/":
        return end + 1
    close = data.find(b"</" + name, end)
    shut = data.find(b">", close) if close >= 0 else -1
    return shut + 1 if shut >= 0 else None


def logins_out(pieces: Iterator[bytes]) -> Iterator[bytes]:
    """A guide's body passed on (the remote API's guide.xml.gz) without an
    <icon>, <url> or <image> whose address carries a login - public_logo's
    rule. Each slice is held back from its last "<" (or an <url> or <image>
    not yet closed) so no element is cut in two; past 1 MiB it goes on."""
    rest = b""
    try:
        for piece in pieces:
            data = rest + piece
            cut = data.rfind(b"<")
            if cut < 0:
                cut = len(data)
            moved = True
            while moved:                # cut only ever moves back: it ends
                moved = False
                for tag in (b"<icon", b"<url", b"<image"):
                    i = data.rfind(tag, 0, cut)
                    if i >= 0:
                        end = _end_of(data, i, tag[1:])
                        if end is None or end > cut:
                            cut, moved = i, True
            if len(data) - cut > (1 << 20):
                cut = len(data)
            rest = data[cut:]
            if cut:
                yield _scrubbed(data[:cut])
    except Exception:
        # A guide cut short: guide_body gives what was whole, then says so -
        # what is held back here is the end of that, and goes out first.
        if rest:
            yield _scrubbed(rest)
        raise
    if rest:
        yield _scrubbed(rest)


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
                out = d.decompress(data, _UNPACK_STEP)
                while True:
                    if out:
                        yield out
                    # A full slice may have more behind it, in the input
                    # left over or inside zlib itself.
                    if d.eof or (not d.unconsumed_tail and len(out) < _UNPACK_STEP):
                        break
                    out = d.decompress(d.unconsumed_tail, _UNPACK_STEP)
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
        # Only a text encoding decodes a guide: "zlib", "base64" or "rot13"
        # named by a guide (the provider's word) leave its bytes as they
        # are, as an unknown name does - zlib's decoder raised
        # AssertionError and broke the whole answer.
        try:
            info = codecs.lookup(enc)
            if getattr(info, "_is_text_encoding", True) and info.incrementaldecoder is not None:
                tried = info.incrementaldecoder("replace")
                tried.decode(b"<tv>", False)    # idna refuses "replace" only when used
                tried.reset()
                decode = tried
        except (LookupError, TypeError, ValueError, AssertionError):
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
