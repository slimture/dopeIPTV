"""An Enigma2 box's channel lists through OpenWebif's bouquet editor, against
a fake OpenWebif on 127.0.0.1 that answers as controllers/BQE.py and
controllers/BouquetEditor.py do ({"Result": [ok, text]}, eBouquet's
positions, " (TV)" added to a new bouquet's name, a renamed entry's name
in its reference, the backup tar with tmp/.webouquetedit): the lists read,
bouquets and entries added, moved, renamed and removed, a box without the
editor said plainly, an error answer, the backup taken first and put back;
the window's session sending in the order made and reading back, and the
source's channels read again after the window is gone."""
from __future__ import annotations

import io
import itertools
import json
import tarfile
import threading
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from dopeiptv.i18n import LocalizedError, tr
from dopeiptv.providers import bouquets as bq
from dopeiptv.providers.enigma2 import Enigma2Client

ABOUT = """<?xml version="1.0" encoding="UTF-8"?>
<e2abouts><e2about><e2enigmaversion>2024-01-02</e2enigmaversion>
<e2distroversion>openatv</e2distroversion><e2imageversion>7.3</e2imageversion>
<e2webifversion>OWIF 2.2.1</e2webifversion><e2model>Vu+ Duo 4K</e2model>
<e2tunerinfo><e2nim><name>Tuner A</name><type>DVB-S2X</type></e2nim></e2tunerinfo>
</e2about></e2abouts>"""

SVT1 = "1:0:19:283D:3FB:1:C00000:0:0:0:"
SVT2 = "1:0:19:283E:3FB:1:C00000:0:0:0:"
EURO = "1:0:19:1B1F:802:2:11A0000:0:0:0:"
TV4 = "1:0:19:2EE3:441:1:C00000:0:0:0:"
KANAL5 = "1:0:19:2EE4:441:1:C00000:0:0:0:"
P1 = "1:0:2:3:3FB:1:C00000:0:0:0:"
MARKER = "1:64:0:0:0:0:0:0:0:0::--- Sport ---"
SPACER = "1:832:D:0:0:0:0:0:0:0:"
LAMEDB = {SVT1: "SVT1 HD", SVT2: "SVT2 HD", EURO: "Eurosport", TV4: "TV4 <HD>", KANAL5: "Kanal 5",
          P1: "P1"}
SAT = '1:7:1:0:0:0:0:0:0:0:(satellitePosition == 50) && (type == 1) ORDER BY name'
PROV = '1:7:1:0:0:0:0:0:0:0:(provider == "Viasat") && (type == 1) ORDER BY name'
CHECK = "tmp/.webouquetedit"


def bref(file: str) -> str:
    kind = "2" if file.endswith(".radio") else "1"
    return f'1:7:{kind}:0:0:0:0:0:0:0:FROM BOUQUET "{file}" ORDER BY bouquet'


FAV, SPORT, RADIO = (bref("userbouquet.favourites.tv"), bref("userbouquet.sport.tv"),
                     bref("userbouquet.radio.radio"))


def _key(ref: str) -> str:
    f = ref.split(":")
    return ":".join(x.upper() for x in f[:10]) + ":" + (f[10] if len(f) > 10 else "")


def _file(ref: str) -> str:
    return ref.split('FROM BOUQUET "', 1)[1].split('"', 1)[0] if "FROM BOUQUET" in ref else ""


class FakeOwif:
    """OpenWebif with its bouquet editor, as its source has it. *editor*
    False: no /bouqueteditor at all (404); *plugin* False: the editor's
    calls say "BouquetEditor plugin not found"."""

    def __init__(self, editor: bool = True, plugin: bool = True) -> None:
        self.editor, self.plugin = editor, plugin
        self.files: dict[str, list[str]] = {
            "bouquets.tv": [FAV, SPORT], "bouquets.radio": [RADIO],
            "userbouquet.favourites.tv": [SVT1, MARKER, EURO, SVT2, SPACER],
            "userbouquet.sport.tv": [EURO],
            "userbouquet.radio.radio": [P1]}
        self.names = {"userbouquet.favourites.tv": "Favourites (TV)",
                      "userbouquet.sport.tv": "Sport <live>",
                      "userbouquet.radio.radio": "Radio (Radio)"}
        self.tmp: dict[str, bytes] = {}
        self.calls: list[tuple[str, dict]] = []
        self.refuse: dict[str, str] = {}
        self.raises: set[str] = set()
        self.lock = threading.Lock()
        box = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, body, ctype="application/json"):
                data = body if isinstance(body, bytes) else (
                    json.dumps(body) if not isinstance(body, str) else body).encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # noqa: N802
                u = urlsplit(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
                with box.lock:
                    box.calls.append((u.path, q))
                    self._send(*box.get(u.path, q))

            def do_POST(self):  # noqa: N802
                u = urlsplit(self.path)
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                with box.lock:
                    box.calls.append((u.path, {}))
                    if u.path != "/bouqueteditor/uploadrestore" or not box.editor:
                        self._send(404, "<html>Error 404</html>", "text/html")
                        return
                    msg = BytesParser(policy=email_policy).parsebytes(
                        b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body)
                    content = b""
                    for part in msg.iter_parts():
                        if part.get_param("name", header="content-disposition") == "rfile":
                            content = part.get_payload(decode=True)
                    box.tmp["bouquets_backup.tar"] = content
                    self._send(200, {"Result": [True, "/tmp/bouquets_backup.tar"]}, "text/plain")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def paths(self, path: str) -> list[dict]:
        return [q for p, q in self.calls if p == path]

    def changes(self) -> list[str]:
        """The editor's calls that change something, in the order made."""
        reads = {"getservices"}
        return [p.rsplit("/", 1)[-1] for p, _q in self.calls
                if p.startswith("/bouqueteditor/api/") and p.rsplit("/", 1)[-1] not in reads]

    # -- what the box has ---------------------------------------------------------

    def name_of(self, ref: str) -> str:
        f = ref.split(":")
        if len(f) > 11 and f[11]:
            return unquote(f[11])
        if "FROM BOUQUET" in ref:
            return self.names.get(_file(ref), "")
        return LAMEDB.get(":".join(f[:10]) + ":", "")

    def entry(self, i: int, ref: str) -> dict:
        flags = int(ref.split(":")[1])
        spacer = ref.startswith("1:832:D")
        return {"servicereference": ref, "pos": i + 1, "isgroup": "0", "isprotected": "1" if ref == EURO else "0",
                "ismarker": "2" if spacer else "1" if flags & 64 else "0",
                "servicename": "" if spacer else self.name_of(ref).replace("<", "&lt;").replace(">", "&gt;")}

    def root(self, s_ref: str) -> str:
        return "bouquets.radio" if '"bouquets.radio"' in s_ref else "bouquets.tv" if (
            '"bouquets.tv"' in s_ref) else ""

    def get(self, path: str, q: dict):
        if path == "/web/about":
            return 200, ABOUT, "text/xml"
        if path == "/web/getallservices":
            return 200, "<e2servicelistrecursive></e2servicelistrecursive>", "text/xml"
        if path == "/web/getservices":
            return 200, "<e2servicelist></e2servicelist>", "text/xml"
        if path == "/api/getsatellites":
            return 200, {"satellites": [{"service": SAT, "name": "Thor 5/6/7 (0.8W) - Services"}]}
        if path == "/api/getservices":
            ref = q.get("sRef", "")
            if "FROM PROVIDERS" in ref:
                items = [(PROV, "Viasat")]
            elif ref == SAT:
                items = [(SVT1, LAMEDB[SVT1]), (SVT2, LAMEDB[SVT2]), (TV4, LAMEDB[TV4])]
            elif ref == PROV:
                items = [(TV4, LAMEDB[TV4]), (KANAL5, LAMEDB[KANAL5])]
            elif ref.endswith("ORDER BY name"):
                radio = ref.startswith("1:7:2:")
                items = sorted(((r, n) for r, n in LAMEDB.items() if (r.split(":")[2] == "2") == radio),
                               key=lambda x: x[1])
            else:
                items = []
            return 200, {"result": True, "services": [
                {"servicereference": r, "servicename": n, "pos": i + 1} for i, (r, n) in enumerate(items)]}
        if not path.startswith("/bouqueteditor/") or not self.editor:
            return 404, "<html><body><h1>Error 404: Not found</h1></body></html>", "text/html"
        if path.startswith("/bouqueteditor/tmp/"):
            name = unquote(path.rsplit("/", 1)[-1])
            return (200, self.tmp[name], "application/x-tar") if name in self.tmp else (404, "no", "text/html")
        call = path.rsplit("/", 1)[-1]
        if call == "getservices":
            ref = q.get("sRef", "")
            root = self.root(ref)
            if root:
                return 200, {"services": [dict(self.entry(i, b), startpos=0)
                                          for i, b in enumerate(self.files[root])]}
            return 200, {"services": [self.entry(i, r) for i, r in enumerate(self.files.get(_file(ref), []))]}
        if call in self.raises:
            return 200, {"result": False, "request": path, "exception": "KeyError('sRef')"}
        if not self.plugin:
            return 200, {"Result": [False, "BouquetEditor plugin not found"]}
        if call in self.refuse:
            return 200, {"Result": [False, self.refuse[call]]}
        fn = getattr(self, "c_" + call, None)
        if fn is None:
            return 404, "<html>Error 404</html>", "text/html"
        return 200, {"Result": list(fn(q))}

    # -- the editor's calls, as BouquetEditor.py makes them ---------------------------

    def _list(self, s_ref: str) -> list[str]:
        return self.files.setdefault(_file(s_ref), [])

    @staticmethod
    def _find(items: list[str], ref: str) -> int:
        return next((i for i, r in enumerate(items) if _key(r) == _key(ref)), -1)

    @staticmethod
    def _move(items: list[str], i: int, pos: int) -> None:
        if 0 <= pos < len(items) and i >= 0:
            items.insert(pos, items.pop(i))

    def _root_of(self, mode: str) -> list[str]:
        return self.files["bouquets.radio" if mode == "1" else "bouquets.tv"]

    def c_addbouquet(self, q):
        radio = q.get("mode") == "1"
        ident = "".join(c if c.isalnum() and c.isascii() else "_" for c in q["name"].lower())
        file = f"userbouquet.{ident}.{'radio' if radio else 'tv'}"
        n = 1
        while file in self.files:
            file = f"userbouquet.{ident}_{n}.{'radio' if radio else 'tv'}"
            n += 1
        name = q["name"] + (" (Radio)" if radio else " (TV)")
        self._root_of(q.get("mode", "0")).append(bref(file))
        self.files[file], self.names[file] = [], name
        return True, f"Bouquet {name} created."

    def c_removebouquet(self, q):
        items = self._root_of(q.get("mode", "0"))
        i = self._find(items, q["sBouquetRef"])
        if i < 0:
            return False, "Bouquet removed failed."
        items.pop(i)
        self.files.pop(_file(q["sBouquetRef"]), None)
        return True, "Bouquet deleted."

    def c_movebouquet(self, q):
        items = self._root_of(q.get("mode", "0"))
        self._move(items, self._find(items, q["sBouquetRef"]), int(q["position"]))
        return True, "Bouquet moved."

    def c_addservicetobouquet(self, q):
        items = self._list(q["sBouquetRef"])
        ref = q["sRef"]
        if q.get("Name"):
            ref = ":".join(ref.split(":")[:11]) + ":" + q["Name"]
        if self._find(items, ref) >= 0:
            return False, f"Service {self.name_of(ref)} already exists in bouquet."
        at = self._find(items, q["sRefBefore"]) if q.get("sRefBefore") else -1
        items.insert(at if at >= 0 else len(items), ref)
        return True, f"Service {self.name_of(ref)} added."

    def c_removeservice(self, q):
        items = self._list(q["sBouquetRef"])
        i = self._find(items, q["sRef"])
        if i < 0:
            return False, "Service can not be removed."
        items.pop(i)
        return True, "Service removed from bouquet."

    def c_moveservice(self, q):
        items = self._list(q["sBouquetRef"])
        self._move(items, self._find(items, q["sRef"]), int(q["position"]))
        return True, "Service moved."

    def c_renameservice(self, q):
        if int(q["sRef"].split(":")[1]) & 2:
            self.names[_file(q["sRef"])] = q["newName"]
            return True, "Bouquet renamed successfully."
        ok, _t = self.c_removeservice(q)
        if ok:
            ok, _t = self.c_addservicetobouquet(dict(q, Name=q["newName"]))
        return (True, "Service renamed successfully.") if ok else (False, "Service can not be renamed.")

    def c_addmarkertobouquet(self, q):
        items = self._list(q["sBouquetRef"])
        for cnt in itertools.count():
            ref = (f"1:64:{cnt}:0:0:0:0:0:0:0::{q['Name']}" if q.get("Name")
                   else f"1:832:D:{cnt}:0:0:0:0:0:0:")
            if self._find(items, ref) < 0:
                break
        at = self._find(items, q["sRefBefore"]) if q.get("sRefBefore") else -1
        items.insert(at if at >= 0 else len(items), ref)
        return True, "Marker added."

    def c_backup(self, q):
        name = q["Filename"] + ".tar"
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            for member, data in ((CHECK, b"created with WebBouquetEditor"),
                                 ("etc/enigma2/state.json",
                                  json.dumps({"files": self.files, "names": self.names}).encode())):
                info = tarfile.TarInfo(member)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
        self.tmp[name] = buf.getvalue()
        return True, name

    def c_restore(self, q):
        name = q["Filename"].rsplit("/", 1)[-1]
        data = self.tmp.pop(name, None)
        if data is None:
            return False, f"Error, {q['Filename']} does not exists, restore is not possible..."
        with tarfile.open(fileobj=io.BytesIO(data)) as tf:
            if CHECK not in tf.getnames():
                return False, "Error, not created with WebBouquetEditor..."
            state = json.loads(tf.extractfile("etc/enigma2/state.json").read())
        self.files, self.names = state["files"], state["names"]
        return True, "Bouquet-settings were restored successfully"


@pytest.fixture
def fake():
    made: list[FakeOwif] = []

    def make(**kw) -> FakeOwif:
        b = FakeOwif(**kw)
        made.append(b)
        return b
    yield make
    for b in made:
        b.close()


def _editor(box: FakeOwif) -> bq.BouquetEditor:
    c = Enigma2Client(box.base)
    c.box_info = {"openwebif": True}
    return c.bouquet_editor()


# -- reading ----------------------------------------------------------------------

def test_the_bouquets_and_a_bouquets_entries_as_the_editor_lists_them(fake):
    box = fake()
    ed = _editor(box)
    got = ed.bouquets()
    assert got.present and ed.present is True
    assert [(b.ref, b.name, b.kind) for b in got.bouquets] == [
        (FAV, "Favourites (TV)", bq.BOUQUET), (SPORT, "Sport <live>", bq.BOUQUET)]
    assert box.paths("/bouqueteditor/api/getservices")[0]["sRef"] == bq.root_ref(False)
    assert [b.name for b in ed.bouquets(radio=True).bouquets] == ["Radio (Radio)"]
    assert 'FROM BOUQUET "bouquets.radio"' in box.paths("/bouqueteditor/api/getservices")[-1]["sRef"]
    entries = ed.entries(FAV)
    assert [(e.name, e.kind) for e in entries] == [
        ("SVT1 HD", bq.SERVICE), ("--- Sport ---", bq.MARKER), ("Eurosport", bq.SERVICE),
        ("SVT2 HD", bq.SERVICE), ("", bq.SPACER)]
    assert entries[2].locked and not entries[0].locked
    # What to add from: OpenWebif's own API, as the editor's page asks it.
    assert [e.name for e in ed.satellites()] == ["Thor 5/6/7 (0.8W) - Services"]
    assert box.paths("/api/getsatellites")[-1] == {"stype": "tv"}
    assert [(e.ref, e.name) for e in ed.providers()] == [(PROV, "Viasat")]
    assert [e.name for e in ed.channels_in(PROV)] == ["TV4 <HD>", "Kanal 5"]
    assert [e.name for e in ed.channels_in(bq.all_ref())] == [
        "Eurosport", "Kanal 5", "SVT1 HD", "SVT2 HD", "TV4 <HD>"]
    assert [e.name for e in ed.channels_in(bq.all_ref(True))] == ["P1"]


def test_a_renamed_entry_is_still_the_same_entry():
    assert bq.same_ref(SVT1, SVT1 + ":SVT 1") and bq.same_ref(SVT1.lower(), SVT1)
    assert not bq.same_ref(SVT1, SVT2) and not bq.same_ref("", "")
    assert bq.ref_id("1:64:0:0:0:0:0:0:0:0::Sport") == bq.ref_id("1:64:0:0:0:0:0:0:0:0::Nyheter")
    assert not bq.same_ref("1:64:0:0:0:0:0:0:0:0::A", "1:64:1:0:0:0:0:0:0:0::A")


# -- changing ---------------------------------------------------------------------

def test_bouquets_made_renamed_moved_and_deleted(fake):
    box = fake()
    ed = _editor(box)
    ok, said = ed.add_bouquet("Nyheter")
    assert ok and "Nyheter (TV)" in said
    assert box.paths("/bouqueteditor/api/addbouquet")[-1] == {"name": "Nyheter", "mode": "0"}
    lists = ed.bouquets()
    new = lists.bouquets[-1]
    assert new.name == "Nyheter (TV)" and "userbouquet.nyheter.tv" in new.ref
    assert ed.rename_bouquet(new.ref, "Nyheter")[0]
    assert box.paths("/bouqueteditor/api/renameservice")[-1] == {
        "sRef": new.ref, "mode": "0", "newName": "Nyheter"}
    assert ed.move_bouquet(new.ref, 0)[0]
    assert box.paths("/bouqueteditor/api/movebouquet")[-1]["position"] == "0"
    assert [b.name for b in ed.bouquets().bouquets] == ["Nyheter", "Favourites (TV)", "Sport <live>"]
    assert ed.remove_bouquet(SPORT)[0]
    assert [b.ref for b in ed.bouquets().bouquets] == [new.ref, FAV]
    assert "userbouquet.sport.tv" not in box.files
    assert ed.add_bouquet("P3 Musik", radio=True)[0]
    assert box.paths("/bouqueteditor/api/addbouquet")[-1]["mode"] == "1"
    assert ed.bouquets(radio=True).bouquets[-1].name == "P3 Musik (Radio)"


def test_entries_added_moved_renamed_removed_markers_and_spacers(fake):
    box = fake()
    ed = _editor(box)
    # Before an entry, and at the end; one the list has already is refused.
    assert ed.add_service(FAV, TV4, before=EURO)[0]
    assert ed.add_service(FAV, KANAL5)[0]
    q = box.paths("/bouqueteditor/api/addservicetobouquet")
    assert q[0] == {"sBouquetRef": FAV, "sRef": TV4, "sRefBefore": EURO}
    assert q[1]["sRefBefore"] == ""
    ok, said = ed.add_service(FAV, SVT1)
    assert not ok and "already exists" in said
    assert [e.name for e in ed.entries(FAV)] == [
        "SVT1 HD", "--- Sport ---", "TV4 <HD>", "Eurosport", "SVT2 HD", "", "Kanal 5"]
    # A move puts the entry at that index.
    assert ed.move_service(FAV, KANAL5, 0)[0]
    assert box.paths("/bouqueteditor/api/moveservice")[-1] == {
        "sBouquetRef": FAV, "sRef": KANAL5, "mode": "0", "position": "0"}
    # A rename: the box takes it out and puts it back before the next one,
    # with the name in its reference - still the same entry.
    assert ed.rename_service(FAV, SVT1, "SVT 1", before=MARKER)[0]
    entries = ed.entries(FAV)
    assert entries[1].name == "SVT 1" and entries[1].ref.endswith("::SVT 1")
    assert bq.same_ref(entries[1].ref, SVT1) and entries[2].kind == bq.MARKER
    # Its old reference still finds it.
    assert ed.remove_service(FAV, SVT1)[0]
    assert all(not bq.same_ref(e.ref, SVT1) for e in ed.entries(FAV))
    assert ed.add_marker(FAV, "Nyheter", before=SVT2)[0]
    assert ed.add_spacer(FAV)[0]
    assert box.paths("/bouqueteditor/api/addmarkertobouquet")[-1] == {
        "sBouquetRef": FAV, "SP": "1", "sRefBefore": ""}
    entries = ed.entries(FAV)
    kinds = [(e.kind, e.name) for e in entries]
    assert kinds[kinds.index((bq.SERVICE, "SVT2 HD")) - 1] == (bq.MARKER, "Nyheter")
    assert [k for k, _n in kinds].count(bq.SPACER) == 2 and kinds[-1][0] == bq.SPACER


def test_a_box_without_the_editor_is_said_plainly(fake):
    # An older OpenWebif or the Dreambox WebInterface: no /bouqueteditor.
    box = fake(editor=False)
    ed = _editor(box)
    got = ed.bouquets()
    assert not got.present and got.said == "404" and ed.present is False
    with pytest.raises(bq.NoEditor) as err:
        ed.add_bouquet("X")
    assert err.value.localized() == tr("bq_none")
    # OpenWebif's editor that cannot load itself.
    box2 = fake(plugin=False)
    ed2 = _editor(box2)
    assert ed2.bouquets().present
    with pytest.raises(bq.NoEditor):
        ed2.move_service(FAV, SVT1, 2)
    assert ed2.present is False and "not found" in ed2.said


def test_an_error_answer_and_a_refusal(fake):
    box = fake()
    ed = _editor(box)
    box.refuse["addbouquet"] = "Multi-Bouquet is not enabled!"
    assert ed.add_bouquet("X") == (False, "Multi-Bouquet is not enabled!")
    # A call that raised on the box.
    box.raises.add("moveservice")
    assert ed.move_service(FAV, SVT1, 1) == (False, "KeyError('sRef')")
    assert bq.parse_result({"Result": None}) == (False, "")
    with pytest.raises(LocalizedError):
        bq.parse_result({"something": "else"})


def test_the_backup_downloaded_and_put_back(fake):
    box = fake()
    ed = _editor(box)
    tar, data = ed.take_backup("dopeiptv bouquets/../x")
    # Only what the editor keeps in a name: no path out of /tmp.
    assert "/" not in tar and tar in box.tmp and bq.is_backup(data)
    assert box.paths("/bouqueteditor/api/backup")[-1]["Filename"] == "dopeiptv bouquets_.._x"
    ed.remove_bouquet(SPORT)
    ed.add_service(FAV, KANAL5)
    assert "userbouquet.sport.tv" not in box.files
    ok, said = ed.restore(data)
    assert ok and "restored" in said
    assert box.files["bouquets.tv"] == [FAV, SPORT] and KANAL5 not in box.files["userbouquet.favourites.tv"]
    assert box.paths("/bouqueteditor/api/restore")[-1] == {"Filename": "/tmp/bouquets_backup.tar"}
    # Anything else is never sent.
    sent = len(box.calls)
    with pytest.raises(LocalizedError) as err:
        ed.restore(b"not a tar at all")
    assert err.value.key == "bq_not_backup" and len(box.calls) == sent
    # A backup the box made but that does not come here: still the box's.
    box.refuse.clear()
    box.tmp.clear()
    tar2, data2 = ed.take_backup()
    assert tar2 and data2
    real = box.c_backup
    box.c_backup = lambda q: (real(q)[0], "elsewhere.tar")
    assert ed.take_backup() == ("elsewhere.tar", b"")


def test_a_block_of_rows_moved_one_entry_at_a_time():
    def apply(n, moves):
        cur = list(range(n))
        for old, pos in moves:
            cur.insert(pos, cur.pop(cur.index(old)))
        return cur
    for n in range(7):
        for size in range(n + 1):
            for rows in itertools.combinations(range(n), size):
                for to in range(n + 1):
                    new, moves = bq.plan_move(n, list(rows), to)
                    assert apply(n, moves) == new
                    assert len(moves) <= len(rows)
                    assert sorted(new) == list(range(n))
    assert bq.plan_move(5, [4], 0) == ([4, 0, 1, 2, 3], [(4, 0)])
    assert bq.plan_move(5, [0], 5) == ([1, 2, 3, 4, 0], [(0, 4)])
    assert bq.plan_move(5, [2], 2)[1] == [] and bq.plan_move(5, [2], 3)[1] == []


# -- the window's session -----------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class Runner:
    """run_async held back: each call waits until run, one at a time."""

    def __init__(self) -> None:
        self.pending: list = []

    def __call__(self, pool, fn, done, fail=None, *args):
        self.pending.append((fn, done, fail, args))

    def step(self) -> str:
        fn, done, fail, args = self.pending.pop(0)
        try:
            result = fn(*args)
        except Exception as e:
            if fail is not None:
                fail(e)
            return getattr(fn, "__name__", "")
        done(result)
        return getattr(fn, "__name__", "")

    def drain(self, limit: int = 200) -> list[str]:
        ran = []
        while self.pending and limit:
            ran.append(self.step())
            limit -= 1
        return ran


class View:
    """What the session tells the window, written down."""

    def __init__(self, backup_answer: bool = True) -> None:
        self.said: list[str] = []
        self.shown: list[str] = []
        self.none = ""
        self.backup_answer = backup_answer
        self.asked_backup = 0
        self.idles = 0

    def show_bouquets(self):
        self.shown.append("bouquets")

    def show_entries(self):
        self.shown.append("entries")

    def say(self, text):
        self.said.append(text)

    def no_editor(self, said=""):
        self.none = said or "?"

    def backup_taken(self):
        self.shown.append("backup")

    def go_on_without_backup(self, text):
        self.asked_backup += 1
        return self.backup_answer

    def idle(self):
        self.idles += 1


def _session(app, monkeypatch, box, view=None):
    from dopeiptv.ui import bouquet_editor as be
    run = Runner()
    monkeypatch.setattr(be, "run_async", run)
    s = be.Session(view or View(), _editor(box), None)
    s.load()
    run.drain()
    return s, run


def test_the_session_sends_in_the_order_made_after_a_backup_and_reads_back(app, monkeypatch, fake):
    box = fake()
    s, run = _session(app, monkeypatch, box)
    assert s.present and [b.name for b in s.bouquets] == ["Favourites (TV)", "Sport <live>"]
    assert s.current == FAV and [e.name for e in s.entries][:2] == ["SVT1 HD", "--- Sport ---"]
    # Three changes made at once: shown at once, sent one at a time.
    s.move_entries([3], 0)                        # SVT2 to the top
    s.rename_entry(1, "SVT 1")                    # SVT1, now second
    s.add_channels([bq.Entry(TV4, "TV4 <HD>")], below=0)
    assert [e.name for e in s.entries][:3] == ["SVT2 HD", "TV4 <HD>", "SVT 1"]
    assert not s.idle and len(run.pending) == 1
    # The backup comes first, before anything changes on the box.
    assert run.step() == "take_backup"
    assert s.backup == "taken" and bq.is_backup(s.backup_data) and box.changes() == ["backup"]
    while run.pending and run.pending[0][0].__name__ != "_get_bouquets":
        assert len(run.pending) == 1
        run.step()
    assert box.changes() == ["backup", "moveservice", "renameservice", "addservicetobouquet"]
    run.drain()
    # What the box lists is what is shown.
    assert [e.name for e in s.entries] == [e.name for e in _editor(box).entries(FAV)]
    assert [e.name for e in s.entries][:4] == ["SVT2 HD", "TV4 <HD>", "SVT 1", "--- Sport ---"]
    assert s.idle and s.changed and s.view.said[-1] == tr("bq_saved")
    # One backup a window.
    s.remove_entries([1])
    run.drain()
    assert box.changes().count("backup") == 1 and box.changes()[-1] == "removeservice"


def test_a_refused_change_stops_the_ones_after_it(app, monkeypatch, fake):
    box = fake()
    s, run = _session(app, monkeypatch, box)
    box.refuse["moveservice"] = "Service can not be moved."
    s.move_entries([0], 3)
    s.remove_entries([0])
    run.drain()
    assert box.changes() == ["backup", "moveservice"]
    assert s.view.said[-1] == (tr("box_failed", why="Service can not be moved.") + " "
                               + tr("bq_dropped", n=1))
    # The lists read again: what the box has, nothing moved, nothing removed.
    assert [e.ref for e in s.entries] == box.files["userbouquet.favourites.tv"]
    assert not s.changed


def test_a_new_bouquet_is_shown_once_the_box_has_made_it(app, monkeypatch, fake):
    box = fake()
    s, run = _session(app, monkeypatch, box)
    s.add_bouquet("Nyheter")
    assert s.bouquets[-1].pending and s.bouquets[-1].name == "Nyheter (TV)"
    run.drain()
    assert s.current == bref("userbouquet.nyheter.tv") and not s.bouquets[-1].pending
    assert s.entries == [] and s.can_edit()
    # A marker waits for the box: the list holds until it is made.
    s.add_channels([bq.Entry(SVT1, "SVT1 HD"), bq.Entry(EURO, "Eurosport")])
    s.add_marker("Sport", below=0)
    assert s.holding and not s.can_edit()
    s.rename_entry(0, "ignored while it holds")
    run.drain()
    assert not s.holding
    assert [(e.kind, e.name) for e in s.entries] == [
        (bq.SERVICE, "SVT1 HD"), (bq.MARKER, "Sport"), (bq.SERVICE, "Eurosport")]
    assert "renameservice" not in box.changes()


def test_a_backup_that_fails_is_asked_about_first(app, monkeypatch, fake):
    box = fake()
    view = View(backup_answer=False)
    s, run = _session(app, monkeypatch, box, view)
    box.refuse["backup"] = "Error while preparing backup file, /etc/enigma2/lamedb does not exists."
    s.remove_entries([0])
    run.drain()
    assert view.asked_backup == 1 and box.changes() == ["backup"]
    assert view.said[-1] == tr("bq_nothing_changed") and s.entries[0].ref == SVT1
    # Told to go on: the change is made without one.
    view.backup_answer = True
    s.remove_entries([0])
    run.drain()
    assert box.changes() == ["backup", "backup", "removeservice"] and s.backup == "skipped"


def test_a_restore_from_a_file_keeps_the_lists_it_replaces_first(app, monkeypatch, fake):
    box = fake()
    old = _editor(box).take_backup()[1]           # a file kept from an earlier day
    _editor(box).remove_bouquet(SPORT)
    box.calls.clear()
    s, run = _session(app, monkeypatch, box)
    s.restore(old)
    run.drain()
    # The lists it replaced were backed up first, so Undo can put them back.
    assert box.changes() == ["backup", "restore"]
    assert s.backup == "taken" and bq.is_backup(s.backup_data)
    assert box.files["bouquets.tv"] == [FAV, SPORT] and s.view.said[-1] == tr("bq_restored")
    s.restore(s.backup_data)                      # Undo: no second backup
    run.drain()
    assert box.changes() == ["backup", "restore", "restore"]
    assert box.files["bouquets.tv"] == [FAV]
    # No backup to be had: asked first; told no, nothing is replaced.
    view = View(backup_answer=False)
    s, run = _session(app, monkeypatch, box, view)
    box.refuse["backup"] = "Error while preparing backup file"
    box.calls.clear()
    s.restore(old)
    run.drain()
    assert view.asked_backup == 1 and box.changes() == ["backup"]
    assert box.files["bouquets.tv"] == [FAV] and not s.changed


def test_a_change_made_while_a_backup_is_put_back_goes_after_it(app, monkeypatch, fake):
    box = fake()
    s, run = _session(app, monkeypatch, box)
    s.take_backup_now()
    run.drain()
    s.restore(s.backup_data)
    s.remove_entries([0])                         # made while it is on its way
    run.drain()
    assert box.changes() == ["backup", "restore", "removeservice"]
    assert s.idle and SVT1 not in box.files["userbouquet.favourites.tv"]
    assert [e.ref for e in s.entries] == box.files["userbouquet.favourites.tv"]
    # Not put back: what was made meanwhile is not sent.
    box.refuse["restore"] = "Error, not created with WebBouquetEditor..."
    s.restore(s.backup_data)
    s.remove_entries([0])
    run.drain()
    assert box.changes()[-1] == "restore" and s.idle
    assert s.view.said[-1].startswith(tr("box_failed", why="Error, not created with WebBouquetEditor..."))


def test_a_box_gone_after_the_last_change_still_ends_the_round(app, monkeypatch, fake):
    box = fake()
    s, run = _session(app, monkeypatch, box)
    s.remove_entries([0])
    run.step()                                     # the backup
    run.step()                                     # the change
    assert run.pending and run.pending[0][0].__name__ == "_get_entries"
    idles = s.view.idles
    box.close()
    box.close = lambda: None
    run.drain()
    # The read back failed: said, and the round is over (a window closed
    # meanwhile can go).
    assert s.idle and s.changed and s.view.idles == idles + 1
    assert s.view.said[-1].startswith(tr("box_failed", why="").strip())


def test_a_box_without_the_editor_in_the_session(app, monkeypatch, fake):
    s, run = _session(app, monkeypatch, fake(editor=False))
    assert s.present is False and s.view.none == "404" and not s.can_edit()
    s.add_bouquet("X")
    assert not run.pending


# -- the window -------------------------------------------------------------------------

def _settle(app) -> None:
    """The window's queued start and its deferred deletion run inside the
    test, while run_async is still the test's - never in a later test."""
    from PyQt6.QtCore import QCoreApplication, QEvent
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    app.processEvents()

def test_the_window_moves_adds_and_reloads_the_source_once_gone(app, monkeypatch, fake):
    from dopeiptv.ui import bouquet_editor as be
    box = fake()
    run = Runner()
    monkeypatch.setattr(be, "run_async", run)
    client = Enigma2Client(box.base)
    client.box_info = {"openwebif": True}
    w = be.BouquetEditorDialog(None, client, None, "Vu+")
    edited: list = []
    w.edited.connect(edited.append)
    _settle(app)                                  # its start, queued at 0 ms
    run.drain()
    assert w.bq_list.count() == 2 and w.entry_list.count() == 5
    assert w.entry_list.item(1).font().bold()             # the marker
    assert w.source.itemText(0) == tr("bq_all") and w.source.count() >= 4
    assert w.avail_list.count() == 5
    # Search, then add the one found below the selected row.
    w.search.setText("tv4")
    shown = [w.avail_list.item(i).text() for i in range(w.avail_list.count())
             if not w.avail_list.item(i).isHidden()]
    assert shown == ["TV4 <HD>"]
    w._search_enter()
    w.avail_list.currentItem().setSelected(True)
    w.entry_list.item(0).setSelected(True)
    w.add_selected()
    assert w.entry_list.item(1).text() == "TV4 <HD>"
    # A drop: Qt moves nothing, the session does.
    w._entries_dropped([3], 0)
    assert w.entry_list.item(0).text() == "Eurosport"
    # Closed while the box still has them to make: the window stays.
    w.reject()
    assert not edited and not getattr(w, "_finished", False)
    run.drain()
    assert box.changes() == ["backup", "addservicetobouquet", "moveservice"]
    assert edited == [client] and w._finished
    assert box.files["userbouquet.favourites.tv"][:3] == [EURO, SVT1, TV4]
    # Done with: nothing more is asked of the box, by the window or its session.
    w._start()
    w.session.load()
    w._source_picked()
    assert not run.pending
    _settle(app)
    assert not run.pending


def test_the_window_closed_with_nothing_changed_reloads_nothing(app, monkeypatch, fake):
    from dopeiptv.ui import bouquet_editor as be
    run = Runner()
    monkeypatch.setattr(be, "run_async", run)
    client = Enigma2Client(fake().base)
    client.box_info = {"openwebif": True}
    w = be.BouquetEditorDialog(None, client, None)
    edited: list = []
    w.edited.connect(edited.append)
    w._start()
    w._start()                                    # once only
    assert len(run.pending) == 1
    run.drain()
    w.reject()
    assert w._finished and edited == []
    # Its start, still queued from when it was made, finds it done.
    _settle(app)
    assert not run.pending


def test_the_sources_channels_are_read_again_after_the_window(app):
    from PyQt6.QtCore import QObject

    from dopeiptv.ui.mw_box import _BoxMixin

    class Client:
        def __init__(self):
            self.cleared = 0

        def clear_list_cache(self):
            self.cleared += 1

    class Host(QObject, _BoxMixin):
        def __init__(self):
            super().__init__()
            self.client, self.refreshed, self.toasts = Client(), [], []

        def refresh_playlist(self, force=True):
            self.refreshed.append(force)

        def _show_toast(self, text, ms):
            self.toasts.append(text)
    h = Host()
    h._box_bouquets_edited(h.client)
    assert h.client.cleared == 1 and h.refreshed == [False] and h.toasts == [tr("bq_reloading")]
    # Another source by now: nothing.
    h._box_bouquets_edited(Client())
    assert h.refreshed == [False]


def test_the_boxs_menu_offers_the_editor_but_not_to_a_kids_profile(app):
    from PyQt6.QtCore import QObject
    from PyQt6.QtWidgets import QMenu

    from dopeiptv.ui.mw_box import _BoxMixin

    class Host(QObject, _BoxMixin):
        kids = False

        def _profile_kids(self):
            return self.kids

    class Box:
        def remote(self, key):
            return True

        def bouquet_editor(self):
            return None
    h = Host()
    menu = QMenu()
    h._box_windows_menu(menu, Box())
    assert tr("box_bouquets") in [a.text() for a in menu.actions()]
    h.kids = True
    menu2 = QMenu()
    h._box_windows_menu(menu2, Box())
    assert tr("box_bouquets") not in [a.text() for a in menu2.actions()]
