"""Enigma2 receivers (VU+, Dreambox, Zgemma, Octagon, Gigablue... on
OpenATV, OpenPLi, OpenViX, Dreambox OS) through their web interface.

Only the classic ``/web/`` XML API is used, which OpenWebif and the old
Dreambox WebInterface both serve, so one client covers a box from 2008 and
one from this year: bouquets are the categories, a channel streams from the
box's stream port (8001, or 17999 for OSCam's stream relay), the guide comes
from the box's EPG cache, the recordings on its disk are films, and on
OpenWebif the picons are the logos. An IPTV entry in a bouquet (4097/5001/
5002) plays straight from its own address instead of through the box.
"""

from __future__ import annotations

from typing import Callable
from urllib.parse import quote, unquote, urlsplit

from ..core.log import log
from ..i18n import tr
from .boxes import BoxClient, build_xmltv, child_text, clean_text

TV_BOUQUETS = '1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "bouquets.tv" ORDER BY bouquet'
RADIO_BOUQUETS = '1:7:2:0:0:0:0:0:0:0:FROM BOUQUET "bouquets.radio" ORDER BY bouquet'
# Service types that carry an address of their own (IPTV in a bouquet).
_IPTV_TYPES = {"4097", "5001", "5002", "5003", "8193", "8739"}
_MARKER, _NUMBERED_MARKER, _HIDDEN, _DIRECTORY = 64, 256, 512, 7


def ref_fields(sref: str) -> list[str]:
    return sref.split(":")


def ref_flags(sref: str) -> int:
    f = ref_fields(sref)
    try:
        return int(f[1]) if len(f) > 1 else 0
    except ValueError:
        return 0


def is_marker(sref: str) -> bool:
    fl = ref_flags(sref)
    return bool(fl & _MARKER) and not fl & _NUMBERED_MARKER


def is_bouquet(sref: str) -> bool:
    return "FROM BOUQUET" in sref.upper() or (ref_flags(sref) & _DIRECTORY) == _DIRECTORY


def iptv_address(sref: str) -> str:
    """The address an IPTV entry carries in its 11th field, or ""."""
    f = ref_fields(sref)
    if len(f) > 10 and (f[0] in _IPTV_TYPES or "%3a" in f[10].lower()):
        url = unquote(f[10])
        if "://" in url:
            return url
    return ""


def ref_key(sref: str) -> str:
    """What names a service: its first ten fields. An IPTV entry whose ids
    are all zero is named by its address too, or every such entry in a
    bouquet would be one channel."""
    f = ref_fields(sref)
    base = ":".join(f[:10]).upper()
    if len(f) > 10 and all(x in ("0", "") for x in f[3:7]) and f[10]:
        return base + ":" + unquote(f[10])
    return base


def picon_name(sref: str) -> str:
    """The picon file a service has under the usual naming (1_0_19_283D_...):
    an IPTV entry's type is written as 1, the trailing colon dropped."""
    f = ref_fields(sref)[:10]
    if f and f[0] != "1":
        f[0] = "1"
    return "_".join(f).strip("_").upper()


class Enigma2Client(BoxClient):
    LOG_NAME = "enigma2"
    KIND = "Enigma2"
    DEFAULT_PORT = 80
    # Bouquets inside bouquets: as deep as anyone nests them.
    MAX_DEPTH = 3

    def __init__(self, server: str, username: str = "", password: str = "",
                 options: dict | None = None) -> None:
        super().__init__(server, username, password, options)
        self._srefs: dict[str, str] = {}
        self._brefs: list[str] = []

    @property
    def openwebif(self) -> bool:
        return bool(self.box_info.get("openwebif"))

    def _probe(self) -> dict:
        root = self.get_xml("/web/about")
        about = root.find("e2about") if root.tag != "e2about" else root
        if root.tag not in ("e2abouts", "e2about") or about is None:
            raise ValueError("not an Enigma2 web interface")
        webif = child_text(about, "e2webifversion")
        model = child_text(about, "e2model")
        image = " ".join(x for x in (child_text(about, "e2distroversion"),
                                     child_text(about, "e2imageversion")) if x)
        owif = "owif" in webif.lower() or "openwebif" in webif.lower()
        tuners = [child_text(n, "name") for n in about.iter("e2nim")]
        return {"kind": "enigma2", "name": " ".join(x for x in (model, f"({image})" if image else "") if x),
                "model": model, "image": image, "webif": webif, "openwebif": owif,
                "enigma": child_text(about, "e2enigmaversion"),
                "tuners": [t for t in tuners if t]}

    # -- channels -------------------------------------------------------------

    def _services(self, sref: str) -> list[tuple[str, str]]:
        root = self.get_xml("/web/getservices", {"sRef": sref})
        return [(child_text(s, "e2servicereference"), child_text(s, "e2servicename"))
                for s in root.iter("e2service")]

    def _all_services(self, radio: bool) -> list[tuple[str, str, list[tuple[str, str]]]] | None:
        """OpenWebif's every bouquet with its services in one answer, or None
        where the box has no such call (the Dreambox WebInterface)."""
        try:
            root = self.get_xml("/web/getallservices", {"type": "radio"} if radio else None,
                                timeout=(6.0, 90.0))
        except Exception as e:
            log.debug("enigma2: no getallservices (%s) - one bouquet at a time", e)
            return None
        if root.tag != "e2servicelistrecursive":
            return None
        out = []
        for b in root.findall("e2bouquet"):
            items = [(child_text(s, "e2servicereference"), child_text(s, "e2servicename"))
                     for s in b.iter("e2service")]
            out.append((child_text(b, "e2servicereference"), child_text(b, "e2servicename"), items))
        return out

    def _bouquets(self, radio: bool) -> list[tuple[str, str, list[tuple[str, str]]]]:
        got = self._all_services(radio) if self.openwebif else None
        if got:
            return got
        return [(ref, name, self._services(ref))
                for ref, name in self._services(RADIO_BOUQUETS if radio else TV_BOUQUETS)
                if ref and is_bouquet(ref)]

    def _stream_url(self, sref: str) -> str:
        direct = iptv_address(sref)
        if direct and self.opt_flag("iptv_direct", True):
            return direct
        port = self.opt_int("stream_port", 8001)
        host = urlsplit(self.server).hostname or ""
        if ":" in host:
            host = f"[{host}]"
        ref = sref if sref.endswith(":") or iptv_address(sref) else sref + ":"
        url = f"http://{host}:{port}/{quote(ref, safe=':%')}"
        return self.login_url(url) if self.opt_flag("stream_login", True) else url

    def _icon(self, sref: str) -> str:
        if not (self.openwebif and self.opt_flag("picons", True)):
            return ""
        return self.login_url(self.url(f"/picon/{picon_name(sref)}.png"))

    def _channel_rows(self, items: list[tuple[str, str]], category: str,
                      depth: int, out: list[dict], bref: str = "") -> None:
        for sref, name in items:
            if not sref or is_marker(sref) or ref_flags(sref) & _HIDDEN:
                continue
            if is_bouquet(sref):
                if depth < self.MAX_DEPTH:
                    sub = f"{category} / {clean_text(name)}" if name else category
                    try:
                        self._channel_rows(self._services(sref), sub, depth + 1, out, sref)
                    except Exception as e:
                        log.debug("enigma2: sub-bouquet %r: %s", name, e)
                continue
            key = ref_key(sref)
            out.append({"key": key, "name": name or iptv_address(sref) or key,
                        "category": category, "url": self._stream_url(sref),
                        "icon": self._icon(sref), "epg_id": key, "_sref": sref, "_bref": bref})

    def _load(self) -> tuple[list[dict], list[dict]]:
        rows: list[dict] = []
        kinds = [False] + ([True] if self.opt_flag("radio", True) else [])
        for radio in kinds:
            try:
                bouquets = self._bouquets(radio)
            except Exception:
                if radio:
                    continue
                raise
            for ref, name, items in bouquets:
                label = clean_text(name) or tr("box_channels")
                if radio:
                    label = tr("box_radio", name=label)
                self._channel_rows(items, label, 1, rows, ref)
        return rows, self._recordings() if self.opt_flag("recordings", True) else []

    # -- recordings -----------------------------------------------------------

    def _recordings(self) -> list[dict]:
        try:
            root = self.get_xml("/web/movielist", timeout=(6.0, 60.0))
        except Exception as e:
            log.info("enigma2: no recordings list (%s)", e)
            return []
        out = []
        for m in root.iter("e2movie"):
            path = child_text(m, "e2filename") or (
                child_text(m, "e2servicereference").split(":", 10)[-1])
            if not path.startswith("/"):
                continue
            desc = child_text(m, "e2description", True)
            ext = child_text(m, "e2descriptionextended", True)
            plot = f"{desc}\n\n{ext}" if desc and ext and ext != desc else (desc or ext)
            url = self.url(f"/file?action=download&file={quote(path, safe='/')}")
            out.append({"key": path, "title": child_text(m, "e2title") or path.rsplit("/", 1)[-1],
                        "plot": plot, "channel": child_text(m, "e2servicename"),
                        "start": _int(child_text(m, "e2time")), "secs": _length(child_text(m, "e2length")),
                        "ext": path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else "ts",
                        "url": self.login_url(url)})
        return out

    # -- guide ----------------------------------------------------------------

    def _events(self, root) -> list[dict]:
        out = []
        for ev in root.iter("e2event"):
            start = _int(child_text(ev, "e2eventstart"))
            secs = _int(child_text(ev, "e2eventduration"))
            title = child_text(ev, "e2eventtitle")
            sref = child_text(ev, "e2eventservicereference")
            if not (start and secs and title and sref):
                continue
            short = child_text(ev, "e2eventdescription", True)
            long_ = child_text(ev, "e2eventdescriptionextended", True)
            genre = ev.find("e2eventgenre")
            out.append({"channel": ref_key(sref), "start": start, "stop": start + secs,
                        "title": title, "subtitle": short if long_ and short != long_ else "",
                        "desc": long_ or short,
                        "category": clean_text(genre.text) if genre is not None else ""})
        return out

    def _guide(self, progress: Callable[[int], None] | None) -> bytes:
        channels = self.guide_channels()
        wanted = {cid for cid, _ in channels}
        events: list[dict] = []
        bouquets = list(self._brefs)
        done_multi = False
        if self.openwebif and bouquets:
            try:
                for i, bref in enumerate(bouquets):
                    events += self._events(self.get_xml("/web/epgmulti", {"bRef": bref},
                                                        timeout=(6.0, 120.0)))
                    if progress:
                        progress(min(99, (i + 1) * 100 // len(bouquets)))
                done_multi = True
            except Exception as e:
                log.info("enigma2: epgmulti failed (%s) - one channel at a time", e)
                events = []
        if not done_multi:
            refs = list({key: sref for sref, key in self._srefs.items()}.values())
            for i, sref in enumerate(refs):
                try:
                    events += self._events(self.get_xml("/web/epgservice", {"sRef": sref},
                                                        timeout=(6.0, 30.0)))
                except Exception as e:
                    log.debug("enigma2: no guide for %s (%s)", sref, e)
                if progress and refs:
                    progress(min(99, (i + 1) * 100 // len(refs)))
        events = [e for e in events if e["channel"] in wanted]
        log.info("enigma2: %d programme(s) for %d channel(s) from the box", len(events), len(wanted))
        return build_xmltv(channels, events) if events else b""

    def _take(self, channels: list[dict], recordings: list[dict]) -> None:
        # The guide asks per bouquet (OpenWebif) or per service: keep both.
        self._srefs = {ch["_sref"]: ch["key"] for ch in channels if ch.get("_sref")}
        self._brefs = list(dict.fromkeys(ch["_bref"] for ch in channels if ch.get("_bref")))
        super()._take(channels, recordings)

    # -- the box itself (Nerd: zap along, wake, standby) ----------------------

    def zap(self, stream_id: int | str) -> bool:
        """Switch the box's own picture to this channel."""
        sref = self._sref_of(stream_id)
        if not sref:
            return False
        root = self.get_xml("/web/zap", {"sRef": sref})
        return child_text(root, "e2state").lower() == "true"

    def power(self, state: int) -> bool:
        """/web/powerstate: 0 toggle standby, 4 wake up, 5 standby,
        1 deep standby, 2 reboot, 3 restart enigma2."""
        root = self.get_xml("/web/powerstate", {"newstate": str(int(state))})
        return root.tag == "e2powerstate"

    def _sref_of(self, stream_id: int | str) -> str:
        row = self._channel(stream_id)
        if row is None:
            return ""
        key = row["_key"]
        return next((s for s, k in self._srefs.items() if k == key), "")


def _int(text: str) -> int:
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def _length(text: str) -> int:
    """e2length: "45:12" (minutes:seconds), "1:02:03", or "?:??"."""
    parts = (text or "").strip().split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return 0
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    return 0
