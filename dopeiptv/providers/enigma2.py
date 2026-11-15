"""Enigma2 receivers (VU+, Dreambox, Zgemma, Octagon, Gigablue... on
OpenATV, OpenPLi, OpenViX, Dreambox OS) through their web interface.

Only the classic ``/web/`` XML API is used, which OpenWebif and the old
Dreambox WebInterface both serve, so one client covers a box from 2008 and
one from this year: bouquets are the categories, a channel streams from the
box's stream port (8001, or 17999 for OSCam's stream relay), the guide comes
from the box's EPG cache, the recordings on its disk are films, and on
OpenWebif the picons are the logos. An IPTV entry in a bouquet (4097/5001/
5002) plays straight from its own address instead of through the box.

The box itself, beside its channels: the tuner's signal (``/web/signal``),
a picture of what the TV shows (``/grab``), its remote control
(``/web/remotecontrol``), its recordings renamed, moved and deleted
(``/web/movierename``, ``/web/moviemove``, ``/web/moviedelete``), a channel
transcoded by a box with a hardware encoder (its stream port 8002, asked
for a bitrate and a size its transcoding accepts, ``/transcoding``), and
whether a channel that will not play is one the box cannot decrypt (the
first packets of its stream, core/ts_scramble.py).
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from ..core import ts_scramble
from ..core._lazy_requests import requests
from ..core.log import log, redact_url
from ..i18n import LocalizedError, tr
from .boxes import BoxClient, build_xmltv, child_text, clean_text

TV_BOUQUETS = '1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "bouquets.tv" ORDER BY bouquet'
RADIO_BOUQUETS = '1:7:2:0:0:0:0:0:0:0:FROM BOUQUET "bouquets.radio" ORDER BY bouquet'
# Service types that carry an address of their own (IPTV in a bouquet).
_IPTV_TYPES = {"4097", "5001", "5002", "5003", "8193", "8739"}
_MARKER, _NUMBERED_MARKER, _HIDDEN, _DIRECTORY = 64, 256, 512, 7

# Transcoding on the box (a hardware encoder, its stream port 8002): each
# step's bitrate (bit/s) and picture size, brought down to what the box's
# own transcoding accepts where it says (/transcoding).
TRANSCODE_PORT = 8002
TRANSCODE_STEPS: dict[str, tuple[int, int, int]] = {
    "low": (1_000_000, 640, 360),
    "medium": (2_500_000, 1280, 720),
    "high": (5_000_000, 1280, 720),
    "full": (8_000_000, 1920, 1080),
}
# Images whose stream server reads the options as one query ("?a=1&b=2");
# the others' take each behind its own "?" - as OpenWebif writes them.
_AMP_DISTROS = ("openpli", "satdreamgr", "openvision")
# What a picture from the box is asked as: both (no mode), the picture
# alone, the menus alone.
GRAB_MODES = ("all", "video", "osd")
# The remote's keys (Linux input codes, as OpenWebif's remotes send them).
KEYS: dict[str, int] = {
    "power": 116, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8,
    "8": 9, "9": 10, "0": 11, "up": 103, "down": 108, "left": 105,
    "right": 106, "ok": 352, "exit": 174, "menu": 139, "info": 358,
    "epg": 365, "red": 398, "green": 399, "yellow": 400, "blue": 401,
    "vol_up": 115, "vol_down": 114, "mute": 113, "ch_up": 402,
    "ch_down": 403, "tv": 377, "radio": 385, "text": 388, "audio": 392,
}
# How much of a stream is read to tell whether it is encrypted.
SCRAMBLE_BYTES = 188 * 1400
SCRAMBLE_SECS = 5.0


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
        # A recording's path -> its service reference (what the movie
        # calls name it by), and the folders the box keeps recordings in.
        self._movie_refs: dict[str, str] = {}
        self._movie_dirs: list[str] = []
        # What the box's transcoding accepts (/transcoding), read with the
        # channels while a step is chosen: {} not asked, present False when
        # the box says it has none.
        self.transcoder: dict[str, Any] = {}

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
                "distro": child_text(about, "e2distroversion").lower(),
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
        self.transcoder = self._read_transcoder() if self.transcode_step() else {}
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
        refs: dict[str, str] = {}
        for m in root.iter("e2movie"):
            sref = child_text(m, "e2servicereference")
            path = child_text(m, "e2filename") or sref.split(":", 10)[-1]
            if not path.startswith("/"):
                continue
            refs[path] = sref
            desc = child_text(m, "e2description", True)
            ext = child_text(m, "e2descriptionextended", True)
            plot = f"{desc}\n\n{ext}" if desc and ext and ext != desc else (desc or ext)
            url = self.url(f"/file?action=download&file={quote(path, safe='/')}")
            out.append({"key": path, "title": child_text(m, "e2title") or path.rsplit("/", 1)[-1],
                        "plot": plot, "channel": child_text(m, "e2servicename"),
                        "start": _int(child_text(m, "e2time")), "secs": _length(child_text(m, "e2length")),
                        "ext": path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else "ts",
                        "url": self.login_url(url)})
        self._movie_refs = refs
        self._movie_dirs = [d for d in (clean_text(x.text) for x in root.iter("e2location")) if d]
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

    def add_timer(self, stream_id: int | str, start: float, stop: float,
                  title: str, description: str = "") -> tuple[bool, str]:
        """A recording timer on the box: it records whether this app runs
        or not. (ok, what the box said)."""
        sref = self._sref_of(stream_id)
        if not sref:
            return False, ""
        root = self.get_xml("/web/timeradd", {
            "sRef": sref, "begin": str(int(start)), "end": str(int(stop)),
            "name": title or "dopeIPTV", "description": description or "",
            "justplay": "0", "afterevent": "3", "repeated": "0"})
        return (child_text(root, "e2state").lower() == "true",
                child_text(root, "e2statetext"))

    def _sref_of(self, stream_id: int | str) -> str:
        row = self._channel(stream_id)
        if row is None:
            return ""
        key = row["_key"]
        return next((s for s, k in self._srefs.items() if k == key), "")

    def tuned(self, stream_id: int | str) -> bool:
        """Whether the box tunes this channel itself (a DVB service, not an
        IPTV entry in a bouquet): what its signal and transcoding are for."""
        sref = self._sref_of(stream_id)
        return bool(sref) and not iptv_address(sref)

    # -- the tuner's signal -----------------------------------------------------

    def signal(self) -> dict[str, float | None]:
        """/web/signal: SNR (dB and %), AGC (%) and BER of the tuner behind
        what the box shows itself - None for each it does not say (in
        standby, or no channel of its own)."""
        root = self.get_xml("/web/signal", timeout=(3.0, 5.0))
        st = root if root.tag == "e2frontendstatus" else root.find("e2frontendstatus")
        if st is None:
            raise LocalizedError("err_unexpected_response")
        # "e2acg" is how both web interfaces spell it; "e2agc" in case one
        # ever spells it right.
        return {"snr_db": _value(child_text(st, "e2snrdb")),
                "snr": _value(child_text(st, "e2snr")),
                "agc": _value(child_text(st, "e2acg") or child_text(st, "e2agc")),
                "ber": _value(child_text(st, "e2ber"))}

    # -- a picture of what the TV shows ----------------------------------------

    def screenshot(self, mode: str = "all") -> bytes:
        """/grab: the TV's picture, its menus, or both (*mode* all), as
        JPEG. Raises LocalizedError when the box sends no picture."""
        params = {"format": "jpg"}
        if mode in ("video", "osd"):
            params["mode"] = mode
        data = self.get("/grab", params, timeout=(6.0, 30.0)).content or b""
        if not data.startswith((b"\xff\xd8", b"\x89PNG", b"BM")):
            raise LocalizedError("err_box_no_grab")
        return data

    # -- its remote control ----------------------------------------------------

    def remote(self, key: str | int, long: bool = False) -> bool:
        """/web/remotecontrol: a key of the box's remote (a name in KEYS or
        its code), held with *long*."""
        code = key if isinstance(key, int) else KEYS.get(str(key))
        if code is None:
            raise ValueError(f"no such key: {key!r}")
        params = {"command": str(int(code))}
        if long:
            params["type"] = "long"
        root = self.get_xml("/web/remotecontrol", params, timeout=(4.0, 8.0))
        return child_text(root, "e2result").lower() == "true"

    # -- its recordings --------------------------------------------------------

    def _movie(self, rec_id: int | str) -> tuple[dict | None, str, str]:
        """(row, path on the box, service reference) of a recording."""
        try:
            row = self._rec_by_id.get(int(rec_id))
        except (TypeError, ValueError):
            row = None
        path = str((row or {}).get("_key") or "")
        if row is None or not path.startswith("/"):
            return None, "", ""
        return row, path, self._movie_refs.get(path) or f"1:0:0:0:0:0:0:0:0:0:{path}"

    def is_movie(self, rec_id: int | str) -> bool:
        return self._movie(rec_id)[0] is not None

    def movie_folders(self) -> list[str]:
        """The folders the box keeps recordings in (/web/getlocations and
        the movie list's bookmarks), each ending in /."""
        found: list[str] = []
        try:
            root = self.get_xml("/web/getlocations", timeout=(4.0, 10.0))
            found = [clean_text(x.text) for x in root.iter("e2location")]
        except Exception as e:
            log.debug("enigma2: no getlocations (%s)", e)
        out: list[str] = []
        for d in [*found, *self._movie_dirs]:
            d = (d or "").strip()
            if d.startswith("/"):
                d = d.rstrip("/") + "/"
                if d not in out:
                    out.append(d)
        return out

    def _movie_call(self, path: str, params: dict) -> tuple[bool, str]:
        root = self.get_xml(path, params, timeout=(6.0, 60.0))
        return (child_text(root, "e2state").lower() == "true",
                child_text(root, "e2statetext"))

    def movie_delete(self, rec_id: int | str) -> tuple[bool, str]:
        """/web/moviedelete: the recording off the box's disk (into its
        trash, where the box keeps one). (ok, what the box said)."""
        row, path, sref = self._movie(rec_id)
        if row is None:
            return False, ""
        ok, text = self._movie_call("/web/moviedelete", {"sRef": sref})
        if ok:
            self._recs = [r for r in self._recs if r is not row]
            self._rec_by_id = {k: v for k, v in self._rec_by_id.items() if v is not row}
            self._movie_refs.pop(path, None)
        return ok, text

    def movie_move(self, rec_id: int | str, folder: str) -> tuple[bool, str]:
        """/web/moviemove: the recording (and the box's files beside it)
        into another folder on the box."""
        row, path, sref = self._movie(rec_id)
        folder = (folder or "").strip()
        if row is None or not folder.startswith("/"):
            return False, ""
        ok, text = self._movie_call("/web/moviemove", {"sRef": sref, "dirname": folder})
        if ok:
            self._moved(row, path, folder.rstrip("/") + "/" + path.rsplit("/", 1)[-1], sref)
        return ok, text

    def movie_rename(self, rec_id: int | str, name: str) -> tuple[bool, str]:
        """/web/movierename: the recording's file (and its title) renamed;
        *name* without the extension, which the box keeps."""
        row, path, sref = self._movie(rec_id)
        name = clean_text(name)
        if row is None or not name or "/" in name or name in (".", ".."):
            return False, ""
        ok, text = self._movie_call("/web/movierename", {"sRef": sref, "newname": name})
        if ok:
            folder, base = path.rsplit("/", 1)
            ext = base[base.rfind("."):] if "." in base else ""
            self._moved(row, path, f"{folder}/{name}{ext}", sref)
            row["name"] = name
        return ok, text

    def _moved(self, row: dict, old: str, new: str, sref: str) -> None:
        """A recording at a new path: its play address and reference follow
        (its id stays until the next load)."""
        row["_key"] = new
        row["_url"] = self.login_url(self.url(f"/file?action=download&file={quote(new, safe='/')}"))
        self._movie_refs.pop(old, None)
        self._movie_refs[new] = (sref[: len(sref) - len(old)] + new if sref.endswith(old)
                                 else f"1:0:0:0:0:0:0:0:0:0:{new}")

    # -- transcoding (a hardware encoder, port 8002) ---------------------------

    def transcode_step(self) -> str:
        step = str(self.opt("transcode", "") or "")
        return step if step in TRANSCODE_STEPS else ""

    def _read_transcoder(self) -> dict[str, Any]:
        """What the box's transcoding accepts, from OpenWebif's /transcoding
        - asked with nothing set: with options it would change them."""
        try:
            root = self.get_xml("/transcoding", timeout=(4.0, 10.0))
        except Exception as e:
            log.info("enigma2: the box's transcoding not read (%s) - asked as chosen", e)
            return {}
        if root.tag == "e2simplexmlresult":
            if child_text(root, "e2state").lower() == "false":
                log.info("enigma2: the box says it has no transcoding: %s",
                         child_text(root, "e2statetext"))
                return {"present": False}
            return {}
        if root.tag != "e2configs":
            return {}
        out: dict[str, Any] = {"present": True}
        enc = root.find("encoder")
        for conf in (enc.findall("e2config") if enc is not None else []):
            name = child_text(conf, "e2configname")
            limits = child_text(conf, "e2configlimits")
            choices = [c.strip() for c in child_text(conf, "e2configchoices").split(",") if c.strip()]
            if name == "bitrate":
                m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", limits)
                if m:
                    out["bitrate_limits"] = (int(m.group(1)), int(m.group(2)))
                nums = [_int(c) for c in choices if c.isdigit()]
                if nums:
                    out["bitrate_choices"] = sorted(nums)
            elif name == "resolution" and choices:
                sizes = [sz for sz in (_size(c) for c in choices) if sz]
                if sizes:
                    out["sizes"] = sizes
        for conf in root.findall("e2config"):
            if child_text(conf, "e2configname") == "port":
                port = _int(child_text(conf, "e2configvalue"))
                if 0 < port < 65536:
                    out["port"] = port
        return out

    def transcode_params(self, step: str) -> tuple[int, int, int]:
        """(bitrate, width, height) a step asks the box for: the step's own,
        brought to what the box's transcoding says it accepts."""
        rate, width, height = TRANSCODE_STEPS[step]
        caps = self.transcoder
        choices: list[int] = list(caps.get("bitrate_choices") or [])
        limits: tuple[int, int] | None = caps.get("bitrate_limits")
        known = choices or list(limits or ())
        # A box that counts in kbit/s says so by its numbers.
        if known and max(known) < 100_000:
            rate //= 1000
        if choices:
            rate = max([c for c in choices if c <= rate] or [min(choices)])
        elif limits:
            rate = min(max(rate, limits[0]), limits[1])
        sizes: list[tuple[int, int]] = list(caps.get("sizes") or [])
        if sizes:
            fit = [sz for sz in sizes if sz[1] <= height]
            width, height = (max(fit, key=lambda sz: sz[1]) if fit
                             else min(sizes, key=lambda sz: sz[1]))
        return rate, width, height

    def _host(self) -> str:
        host = urlsplit(self.server).hostname or ""
        return f"[{host}]" if ":" in host else host

    def transcoded_url(self, sref: str, step: str) -> str:
        """The channel from the box's encoder at *step*."""
        rate, width, height = self.transcode_params(step)
        port = self.opt_int("transcode_port", 0) or int(self.transcoder.get("port") or TRANSCODE_PORT)
        ref = sref if sref.endswith(":") else sref + ":"
        sep = "&" if self.box_info.get("distro") in _AMP_DISTROS else "?"
        query = sep.join((f"bitrate={rate}", f"width={width}", f"height={height}", "interlaced=0"))
        url = f"http://{self._host()}:{port}/{quote(ref, safe=':%')}?{query}"
        return self.login_url(url) if self.opt_flag("stream_login", True) else url

    def play_url(self, stream_id: int | str, purpose: str = "play") -> str:
        """The address a channel plays from here: transcoded by the box when
        its source asks for that - for the player, and for casting and
        multiview (*purpose* "cast", "multiview") only when it says so too.
        Anything else (IPTV in a bouquet, a box that says it has no
        transcoding) plays as live_url() says."""
        plain = self.live_url(stream_id)
        step = self.transcode_step()
        if not (plain and step) or self.transcoder.get("present") is False:
            return plain
        if purpose != "play" and not self.opt_flag("transcode_more", False):
            return plain
        sref = self._sref_of(stream_id)
        if not sref or iptv_address(sref):
            return plain
        return self.transcoded_url(sref, step)

    # -- a channel the box cannot decrypt --------------------------------------

    def _own_stream(self, url: str) -> bool:
        """Whether *url* is one of the box's own streams (its stream port or
        its encoder's) - never another source's address."""
        try:
            u = urlsplit(url)
            port = u.port
        except ValueError:
            return False
        ports = {self.opt_int("stream_port", 8001), self.opt_int("transcode_port", 0),
                 int(self.transcoder.get("port") or TRANSCODE_PORT)}
        return (u.scheme == "http" and port in ports and (u.hostname or "").lower()
                == (urlsplit(self.server).hostname or "").lower())

    def _stream_head(self, url: str) -> bytes:
        """The first part of a stream from the box (SCRAMBLE_BYTES, or what
        came in SCRAMBLE_SECS), logged in as the box asks."""
        u = urlsplit(url)
        bare = urlunsplit((u.scheme, u.netloc.rsplit("@", 1)[-1], u.path, u.query, ""))
        auth_mod = requests.auth
        auth = self._auth or (auth_mod.HTTPBasicAuth(self.username, self.password)
                              if self.username else None)
        r = self.session.get(bare, stream=True, auth=auth, timeout=(4.0, SCRAMBLE_SECS))
        if r.status_code == 401 and self.username and "digest" in r.headers.get(
                "WWW-Authenticate", "").lower():
            r.close()
            r = self.session.get(bare, stream=True, timeout=(4.0, SCRAMBLE_SECS),
                                 auth=auth_mod.HTTPDigestAuth(self.username, self.password))
        buf = bytearray()
        try:
            if r.status_code != 200:
                return b""
            until = time.monotonic() + SCRAMBLE_SECS
            for chunk in r.iter_content(32 * 1024):
                buf += chunk
                if len(buf) >= SCRAMBLE_BYTES or time.monotonic() > until:
                    break
        finally:
            r.close()
        return bytes(buf)

    def scrambled(self, url: str) -> bool | None:
        """(off the window's thread) True when the box sends this channel
        still encrypted - it has no card, CI module or rights for it; False
        in the clear; None when it cannot be told (no answer, not a
        transport stream, not one of the box's own streams)."""
        if not self._own_stream(url):
            return None
        try:
            data = self._stream_head(url)
        except Exception as e:
            log.debug("enigma2: stream not read for the encryption check (%s)", e)
            return None
        found = ts_scramble.verdict(data)
        said = {True: "encrypted - the box does not decrypt it", False: "in the clear",
                None: f"not told ({len(data)} bytes)"}
        log.info("enigma2: %s is %s", redact_url(url), said[found])
        return found


def _int(text: str) -> int:
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def _value(text: str) -> float | None:
    """The number in what the box says ("12.34 dB", "85 %", "0"), or None."""
    m = re.search(r"-?\d+(?:[.,]\d+)?", text or "")
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def _size(text: str) -> tuple[int, int] | None:
    """(width, height) of "1280x720", or of "720p" (16:9)."""
    m = re.match(r"^\s*(\d{3,4})\s*[x×]\s*(\d{3,4})\s*$", text or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"^\s*(\d{3,4})[pi]\s*$", text or "")
    if m:
        h = int(m.group(1))
        return (h * 16 // 9 + 1) // 2 * 2, h
    return None


def signal_rows(sig: dict | None) -> list[tuple[str, str]]:
    """The signal as the stream stats show it: SNR, AGC, BER - or one line
    saying the box gives none."""
    sig = sig or {}

    def num(v: Any, fmt: str) -> str:
        return fmt.format(v) if isinstance(v, (int, float)) else ""
    snr = " · ".join(x for x in (num(sig.get("snr_db"), "{:.1f} dB"),
                                 num(sig.get("snr"), "{:.0f} %")) if x)
    rows = [("SNR", snr), ("AGC", num(sig.get("agc"), "{:.0f} %")),
            ("BER", num(sig.get("ber"), "{:.0f}"))]
    if not any(v for _k, v in rows):
        return [(tr("box_signal"), tr("box_signal_none"))]
    return [(k, v or "—") for k, v in rows]


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
