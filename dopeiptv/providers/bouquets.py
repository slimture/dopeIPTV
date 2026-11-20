"""An Enigma2 box's channel lists (its bouquets) edited from the app,
through OpenWebif's bouquet editor: the bouquets (TV and radio) and the
channels in each, reordered, renamed, added and removed, markers and
spacers, new bouquets, and the editor's backup of every list as a tar on
the box - downloaded here, and put back.

Checked against OpenWebif's source (E2OpenPlugins/e2openplugin-OpenWebif,
master, read November 2026), not against a box:

- controllers/root.py serves ``/bouqueteditor`` (BQEController), and
  controllers/BQE.py what is under it: ``web/<call>`` answers an
  e2simplexmlresult, ``api/<call>`` the same as JSON - ``{"Result": [ok,
  text]}``; ``tmp/`` serves the box's /tmp; ``uploadrestore`` takes a POSTed
  tar (the form field ``rfile``) and writes it to /tmp/bouquets_backup.tar
  (opened without truncating: a longer file left there keeps its tail,
  after the tar's end, which tar does not read). Each change imports
  controllers/BouquetEditor.py and says "BouquetEditor plugin not found"
  where it cannot; a call that raises answers ``{"result": false,
  "exception": ...}``.
- controllers/BouquetEditor.py, what each call does (MODE_TV 0, MODE_RADIO
  1): ``addbouquet`` (name, mode) adds " (TV)" or " (Radio)" to the name,
  as the box's own menu does, appends the bouquet to the list and answers
  no reference; ``removebouquet`` (sBouquetRef, mode) also deletes its file;
  ``movebouquet`` (sBouquetRef, mode, position); ``addservicetobouquet``
  (sBouquetRef, sRef, sRefBefore: before that entry, at the end without;
  one already in the list is refused); ``removeservice`` (sBouquetRef,
  sRef); ``moveservice`` (sBouquetRef, sRef, mode, position);
  ``renameservice`` (a bouquet: sRef, mode, newName - its list's name; an
  entry: sBouquetRef, sRef, newName, sRefBefore - removed and added again
  with the name, so sRefBefore is the entry after it);
  ``addmarkertobouquet`` (sBouquetRef, Name, sRefBefore; a spacer with SP
  and no Name); ``backup`` (Filename: letters, digits, "_", "." and spaces
  kept) tars the bouquets, lamedb and the tuning XML into
  /tmp/<name>.tar with /tmp/.webouquetedit inside and answers the tar's
  name; ``restore`` (Filename, a path on the box) takes only such a tar,
  replaces the lists and deletes the file.
- Positions count as enigma2's eBouquet::moveService does: the entry ends
  at that index of the list. The editor's own page (public/js/bqe.min.js)
  sends the index in the list ``getservices`` gives - which leaves out
  entries hidden with flag 512 (some images' hidden services), so in a
  bouquet with such entries a move can land a place off; the list read
  again afterwards shows where it went.
- ``/bouqueteditor/api/getservices`` (sRef): a bouquet's entries with
  ``servicereference``, ``servicename`` (< and > escaped), ``ismarker``
  (0, 1 a marker, 2 a spacer), ``isgroup`` (alternatives), ``isprotected``;
  the bouquets of ``bouquets.tv`` / ``bouquets.radio`` the same way. What
  to add from is OpenWebif's own API, as the editor's page asks it:
  ``/api/getservices`` (every channel ``ORDER BY name``, the providers
  ``FROM PROVIDERS``, a satellite's or a provider's channels by its
  reference) and ``/api/getsatellites`` (stype tv/radio: ``service``,
  ``name``).
- An entry's reference carries its name once it has one of its own
  (``...:0::Name``); enigma2 compares references without it, so the same
  entry is still found by its old one (``same_ref``).

Not checked: a real box; an OpenWebif old enough to have no
``/bouqueteditor`` (it answers 404, said as no editor); the Dreambox
WebInterface's own WebBouquetEditor plugin, which is not used (said as no
editor); how long a backup or a restore takes on a box with a large lamedb.

All of it is network: called off the window's thread.
"""

from __future__ import annotations

import io
import re
import tarfile
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from ..core.log import log
from ..i18n import LocalizedError
from .boxes import clean_text

if TYPE_CHECKING:
    from .enigma2 import Enigma2Client

MODE_TV, MODE_RADIO = 0, 1
_TV_TYPES = ("1:7:1:0:0:0:0:0:0:0:(type == 1) || (type == 17) || (type == 195) || "
             "(type == 25) || (type == 22) || (type == 31) || (type == 211) ")
_RADIO_TYPES = "1:7:2:0:0:0:0:0:0:0:(type == 2) || (type == 10) "
SERVICE, MARKER, SPACER, BOUQUET, GROUP = "service", "marker", "spacer", "bouquet", "group"
#: What the editor answers where it cannot load itself.
_NOT_FOUND = "bouqueteditor plugin not found"
#: The file every backup of the editor's has inside.
CHECK_FILE = "tmp/.webouquetedit"
_BACKUP_NAME = re.compile(r"[^A-Za-z0-9_. ]+|^\.|\.$|^ | $|^$")
_TIMEOUT = (6.0, 30.0)
_SLOW = (6.0, 120.0)
#: The most a backup downloaded from the box may be.
MAX_BACKUP = 64 * 1024 * 1024
#: How far a tar is read for the editor's mark: an editor's backup is a
#: few hundred lists and a few MB unpacked; a compressed bomb is not
#: unpacked past this, nor its members counted past that.
_UNPACKED_MAX = 1 << 30
_MEMBERS_MAX = 20_000


def _types(radio: bool) -> str:
    return _RADIO_TYPES if radio else _TV_TYPES


def root_ref(radio: bool = False) -> str:
    """The list of bouquets itself (``bouquets.tv`` / ``bouquets.radio``)."""
    return _types(radio) + f'FROM BOUQUET "bouquets.{"radio" if radio else "tv"}" ORDER BY bouquet'


def all_ref(radio: bool = False) -> str:
    """Every channel the box knows, by name."""
    return _types(radio) + "ORDER BY name"


def providers_ref(radio: bool = False) -> str:
    return _types(radio) + "FROM PROVIDERS ORDER BY name"


def mode(radio: bool) -> str:
    return str(MODE_RADIO if radio else MODE_TV)


def ref_id(ref: str) -> str:
    """What names an entry: type, flags, ids and path - a name of its own
    (the 12th field) left out, as enigma2 compares references."""
    f = (ref or "").split(":")
    if len(f) < 10:
        return ref or ""
    return ":".join(x.upper() for x in f[:10]) + ":" + (f[10] if len(f) > 10 else "")


def same_ref(a: str, b: str) -> bool:
    """Whether two references name one entry."""
    return bool(a) and bool(b) and ref_id(a) == ref_id(b)


def find(entries: list[Entry], ref: str) -> int:
    """The index of the entry *ref* names, or -1."""
    return next((i for i, e in enumerate(entries) if e.ref and same_ref(e.ref, ref)), -1)


@dataclass(frozen=True)
class Entry:
    """One line of a list: a channel, a marker (a heading), a spacer, a
    bouquet (in the list of bouquets, or one inside a bouquet), or a group
    of alternatives. *ref* is "" for one the box is still making."""
    ref: str
    name: str = ""
    kind: str = SERVICE
    locked: bool = False

    @property
    def pending(self) -> bool:
        return not self.ref


@dataclass
class Lists:
    """What the editor said of a list of bouquets: whether the box has the
    editor, the bouquets, and what the box said when it has none."""
    present: bool
    bouquets: list[Entry] = field(default_factory=list)
    said: str = ""


class NoEditor(LocalizedError):
    """The box has no bouquet editor (an older OpenWebif, or the Dreambox
    WebInterface): said in a line, never as a failure of the box."""

    def __init__(self, said: str = "") -> None:
        super().__init__("bq_none")
        self.said = said


def _is_bouquet(ref: str) -> bool:
    from .enigma2 import is_bouquet
    return is_bouquet(ref)


def _name(text: Any) -> str:
    return clean_text(str(text or "").replace("&lt;", "<").replace("&gt;", ">"))


def _flag(value: Any) -> str:
    return str(value if value is not None else "0").strip()


def parse_entries(data: Any) -> list[Entry]:
    """``getservices``' entries (the editor's or OpenWebif's own API)."""
    items = data.get("services") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise LocalizedError("err_unexpected_response")
    out = []
    for s in items:
        if not isinstance(s, dict):
            continue
        ref = str(s.get("servicereference") or "").strip()
        if not ref:
            continue
        marker = _flag(s.get("ismarker"))
        if marker == "2" or ref.startswith("1:832:"):
            kind = SPACER
        elif marker == "1":
            kind = MARKER
        elif _flag(s.get("isgroup")) == "1":
            kind = GROUP
        elif _is_bouquet(ref):
            kind = BOUQUET
        else:
            kind = SERVICE
        out.append(Entry(ref, "" if kind == SPACER else _name(s.get("servicename")), kind,
                         _flag(s.get("isprotected")) not in ("", "0")))
    return out


def parse_satellites(data: Any) -> list[Entry]:
    """``/api/getsatellites``: each satellite's (and cable's, terrestrial's)
    list, "Astra 19.2E - Services", "... - New"."""
    items = data.get("satellites") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise LocalizedError("err_unexpected_response")
    return [Entry(str(s.get("service") or ""), _name(s.get("name")), BOUQUET)
            for s in items if isinstance(s, dict) and s.get("service")]


def parse_result(data: Any) -> tuple[bool, str]:
    """A change's answer: ``{"Result": [ok, text]}`` - or a call that raised
    on the box (``{"result": false, "exception": ...}``)."""
    if isinstance(data, dict) and "Result" in data:
        res = data["Result"]
        if res is None:
            return False, ""
        if isinstance(res, (list, tuple)) and len(res) >= 2:
            ok = res[0] is True or str(res[0]).lower() == "true"
            return ok, clean_text(res[1])
    if isinstance(data, dict) and data.get("result") is False:
        return False, clean_text(data.get("message") or data.get("exception") or "")
    raise LocalizedError("err_unexpected_response")


def backup_name(now: float | None = None) -> str:
    """What a backup is called on the box (only what its editor keeps)."""
    return time.strftime("dopeiptv_bouquets_%Y%m%d_%H%M%S", time.localtime(now))


def is_backup(data: bytes) -> bool:
    """Whether *data* is a tar the editor made (the only kind it restores):
    read a member at a time to its mark, and no further than any backup of
    the editor's goes (_UNPACKED_MAX, _MEMBERS_MAX)."""
    try:
        with tarfile.open(fileobj=io.BytesIO(data or b""), mode="r:*") as tf:
            for count, m in enumerate(iter(tf.next, None)):
                if m.name.lstrip("./").rstrip("/") == CHECK_FILE:
                    return True
                if count >= _MEMBERS_MAX or m.offset_data + m.size > _UNPACKED_MAX:
                    return False
            return False
    except Exception:           # damaged, cut short, a compression error: no backup
        return False


def _status(err: BaseException) -> int:
    return int(getattr(getattr(err, "response", None), "status_code", 0) or 0)


class BouquetEditor:
    """The bouquet editor of one box, through its client's requests (its
    login, basic or digest). *present*: None until asked, then whether the
    box has the editor."""

    def __init__(self, client: Enigma2Client) -> None:
        self.client = client
        self.present: bool | None = None
        self.said = ""

    # -- requests -------------------------------------------------------------

    def _json(self, path: str, params: dict | None = None,
              timeout: tuple[float, float] = _TIMEOUT) -> Any:
        r = self.client.get(path, params, timeout)
        try:
            return r.json()
        except ValueError:
            raise LocalizedError("err_unexpected_response") from None

    def _absent(self, said: str) -> NoEditor:
        self.present, self.said = False, said
        log.info("enigma2: no bouquet editor on the box (%s)", said or "-")
        return NoEditor(said)

    def _change(self, call: str, params: dict[str, str],
                timeout: tuple[float, float] = _TIMEOUT) -> tuple[bool, str]:
        """/bouqueteditor/api/<call>: (ok, what the box said). NoEditor
        when the box has none."""
        try:
            got = parse_result(self._json(f"/bouqueteditor/api/{call}", params, timeout))
        except LocalizedError:
            raise
        except Exception as e:
            if _status(e) == 404:
                raise self._absent("404") from None
            raise
        if not got[0] and got[1].lower() == _NOT_FOUND:
            raise self._absent(got[1])
        self.present = True
        log.info("enigma2: bouquet editor %s - %s", call, "done" if got[0] else f"refused: {got[1]}")
        return got

    # -- reading ----------------------------------------------------------------

    def bouquets(self, radio: bool = False) -> Lists:
        """The box's bouquets (TV or radio), or Lists(False) when it has no
        editor - said plainly, never an error."""
        try:
            data = self._json("/bouqueteditor/api/getservices", {"sRef": root_ref(radio)})
        except LocalizedError as e:
            if e.key != "err_unexpected_response":
                raise
            self._absent("not JSON")
            return Lists(False, said="not JSON")
        except Exception as e:
            if _status(e) != 404:
                raise
            self._absent("404")
            return Lists(False, said="404")
        if isinstance(data, dict) and "services" not in data and data.get("result") is False:
            # The call raised on the box: its words, not "no editor".
            raise LocalizedError(clean_text(data.get("exception") or data.get("message"))
                                 or "err_unexpected_response")
        try:
            found = parse_entries(data)
        except LocalizedError:
            self._absent("no list")
            return Lists(False, said="no list")
        self.present, self.said = True, ""
        return Lists(True, found)

    def entries(self, bref: str) -> list[Entry]:
        """A bouquet's entries as the box has them, in order."""
        try:
            return parse_entries(self._json("/bouqueteditor/api/getservices", {"sRef": bref}))
        except Exception as e:
            if _status(e) == 404:
                raise self._absent("404") from None
            raise

    def satellites(self, radio: bool = False) -> list[Entry]:
        try:
            return parse_satellites(self._json("/api/getsatellites",
                                               {"stype": "radio" if radio else "tv"}))
        except Exception as e:
            log.info("enigma2: no satellites list (%s)", e)
            return []

    def providers(self, radio: bool = False) -> list[Entry]:
        try:
            data = self._json("/api/getservices", {"sRef": providers_ref(radio)}, _SLOW)
            return [Entry(e.ref, e.name, BOUQUET) for e in parse_entries(data)]
        except Exception as e:
            log.info("enigma2: no providers list (%s)", e)
            return []

    def channels_in(self, ref: str) -> list[Entry]:
        """The channels of a satellite's, a provider's or the whole list -
        what is added from."""
        data = self._json("/api/getservices", {"sRef": ref}, _SLOW)
        return [e for e in parse_entries(data) if e.kind == SERVICE]

    # -- the bouquets -------------------------------------------------------------

    def add_bouquet(self, name: str, radio: bool = False) -> tuple[bool, str]:
        """A new bouquet at the end of the list; the box names it
        "<name> (TV)" or "<name> (Radio)"."""
        return self._change("addbouquet", {"name": name, "mode": mode(radio)})

    def rename_bouquet(self, bref: str, name: str, radio: bool = False) -> tuple[bool, str]:
        return self._change("renameservice", {"sRef": bref, "mode": mode(radio), "newName": name})

    def remove_bouquet(self, bref: str, radio: bool = False) -> tuple[bool, str]:
        """The bouquet off the list, and its file off the box."""
        return self._change("removebouquet", {"sBouquetRef": bref, "mode": mode(radio)})

    def move_bouquet(self, bref: str, position: int, radio: bool = False) -> tuple[bool, str]:
        return self._change("movebouquet", {"sBouquetRef": bref, "mode": mode(radio),
                                            "position": str(int(position))})

    # -- a bouquet's entries --------------------------------------------------------

    def add_service(self, bref: str, sref: str, before: str = "") -> tuple[bool, str]:
        """*sref* into the bouquet before the entry *before* (at the end
        without one)."""
        return self._change("addservicetobouquet", {"sBouquetRef": bref, "sRef": sref,
                                                    "sRefBefore": before or ""})

    def remove_service(self, bref: str, sref: str) -> tuple[bool, str]:
        return self._change("removeservice", {"sBouquetRef": bref, "sRef": sref})

    def move_service(self, bref: str, sref: str, position: int,
                     radio: bool = False) -> tuple[bool, str]:
        return self._change("moveservice", {"sBouquetRef": bref, "sRef": sref,
                                            "mode": mode(radio), "position": str(int(position))})

    def rename_service(self, bref: str, sref: str, name: str, before: str = "") -> tuple[bool, str]:
        """An entry renamed in this bouquet; *before* is the entry after it
        (the box takes it out and puts it back there with the name)."""
        return self._change("renameservice", {"sBouquetRef": bref, "sRef": sref,
                                              "newName": name, "sRefBefore": before or ""})

    def add_marker(self, bref: str, name: str, before: str = "") -> tuple[bool, str]:
        return self._change("addmarkertobouquet", {"sBouquetRef": bref, "Name": name,
                                                   "sRefBefore": before or ""})

    def add_spacer(self, bref: str, before: str = "") -> tuple[bool, str]:
        return self._change("addmarkertobouquet", {"sBouquetRef": bref, "SP": "1",
                                                   "sRefBefore": before or ""})

    # -- backup and restore ---------------------------------------------------------

    def backup(self, name: str = "") -> str:
        """The editor's backup of every list, as a tar in the box's /tmp:
        its file name there. Raises LocalizedError with what the box said
        when it could not."""
        name = _BACKUP_NAME.sub("_", name or backup_name())
        ok, text = self._change("backup", {"Filename": name}, _SLOW)
        if not ok or not text or "/" in text:
            raise LocalizedError("bq_backup_failed", why=text or "")
        return text

    def download(self, tar: str) -> bytes:
        """A backup from the box's /tmp, checked to be the editor's."""
        r = self.client.get("/bouqueteditor/tmp/" + quote(tar), None, _SLOW)
        data = bytes(r.content or b"")
        if len(data) > MAX_BACKUP or not is_backup(data):
            raise LocalizedError("bq_not_backup")
        return data

    def take_backup(self, name: str = "") -> tuple[str, bytes]:
        """A backup on the box and its copy here: (its name, its bytes - b""
        when the box made it but it could not be fetched)."""
        tar = self.backup(name)
        try:
            data = self.download(tar)
        except Exception as e:
            log.warning("enigma2: the box's backup %s not downloaded: %s", tar, e)
            data = b""
        log.info("enigma2: backup of the bouquets on the box: /tmp/%s (%d bytes here)",
                 tar, len(data))
        return tar, data

    def restore(self, data: bytes) -> tuple[bool, str]:
        """A backup put back: sent to the box (``uploadrestore``), then
        restored there - every list replaced. Only a tar the editor made."""
        if not is_backup(data):
            raise LocalizedError("bq_not_backup")
        try:
            r = self.client.post("/bouqueteditor/uploadrestore", {}, _SLOW,
                                 files={"rfile": ("bouquets_backup.tar", data, "application/x-tar")})
        except Exception as e:
            if _status(e) == 404:
                raise self._absent("404") from None
            raise
        try:
            ok, path = parse_result(r.json())
        except ValueError:
            raise LocalizedError("err_unexpected_response") from None
        if not ok or not path.startswith("/"):
            return False, path
        return self._change("restore", {"Filename": path}, _SLOW)


# -- planning a move -----------------------------------------------------------------

def plan_move(n: int, rows: list[int], to: int) -> tuple[list[int], list[tuple[int, int]]]:
    """Rows *rows* of a list of *n*, moved together to before row *to* (*n*:
    the end): (the new order as the old indices, the moves that make it -
    (an old index, the position it goes to), one after another, as the
    box moves an entry). At most one move per row."""
    picked = sorted({r for r in rows if 0 <= r < n})
    to = max(0, min(n, to))
    taken = set(picked)
    rest = [i for i in range(n) if i not in taken]
    k = to - sum(1 for r in picked if r < to)
    new = rest[:k] + picked + rest[k:]
    final = {old: pos for pos, old in enumerate(new)}
    # Those going down from the last, then those going up from the first:
    # each move then leaves the ones already placed where they are.
    order = (sorted((r for r in picked if r < to), key=lambda r: final[r], reverse=True)
             + sorted((r for r in picked if r >= to), key=lambda r: final[r]))
    cur = list(range(n))
    moves = []
    for r in order:
        p, q = final[r], cur.index(r)
        if p != q:
            cur.insert(p, cur.pop(q))
            moves.append((r, p))
    return new, moves
