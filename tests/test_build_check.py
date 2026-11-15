"""Official, modified or from source (core/build_check.py): the signed list
of a build's own files made and checked (with a key of these tests' own),
what a change to a bundle is called, dopeiptv.org's services off in a
modified build and the header every request to the site carries, the
report's Build line, About's line, and the release workflow's step."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import tempfile
from pathlib import Path

import pytest

from dopeiptv.core import build_check, ed25519
from dopeiptv.core.build_check import MODIFIED, OFFICIAL, SOURCE, Verdict

ROOT = Path(__file__).resolve().parent.parent
SEED = bytes(range(32, 64))
PUB = base64.b64encode(ed25519.public_key(SEED)).decode()
KEYS = (PUB,)
HEADER = build_check.HEADER


@pytest.fixture(autouse=True)
def _fresh_verdict():
    build_check.set_verdict(None)
    yield
    build_check.set_verdict(None)


def _modified(*names: str) -> Verdict:
    return Verdict(MODIFIED, reason="files", extra=tuple(f"dopeiptv/{n}" for n in names),
                   count=len(names))


class FakeArchive:
    """PyInstaller's archive as the check reads it: a table of contents
    (name -> typecode, offset, length) and each entry's bytes."""

    def __init__(self, mods: dict[str, tuple[int, bytes]]) -> None:
        self.toc = {n: (t, 0, len(b)) for n, (t, b) in mods.items()}
        self.data = {n: b for n, (t, b) in mods.items()}

    def extract(self, name: str, raw: bool = False):
        assert raw
        return self.data[name]


def _archive(**changes: bytes) -> FakeArchive:
    mods = {"dopeiptv": (1, b"pkg"), "dopeiptv.app": (0, b"app"), "dopeiptv.ui": (1, b"ui"),
            "dopeiptv.ui.theme": (0, b"theme"), "requests": (0, b"not ours")}
    for name, data in changes.items():
        mods[name.replace("__", ".")] = (0, data)
    return FakeArchive(mods)


def _bundle(root: Path) -> Path:
    pkg = root / "dopeiptv"
    (pkg / "locale").mkdir(parents=True)
    (pkg / "assets").mkdir()
    (pkg / "locale" / "de.json").write_text('{"a": "b"}', encoding="utf-8")
    (pkg / "assets" / "logo.svg").write_text("<svg/>", encoding="utf-8")
    return pkg


def _tree(root: Path) -> Path:
    pkg = _bundle(root)
    (pkg / "__init__.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")
    (pkg / "core").mkdir()
    (pkg / "core" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core" / "x.py").write_text("X = 1\n", encoding="utf-8")
    return pkg


def _sign_into(pkg: Path, files: dict, mode: str, version: str = "9.9.9") -> None:
    data = build_check.make(files, version, mode)
    (pkg / build_check.MANIFEST).write_bytes(data)
    (pkg / build_check.SIGNATURE).write_text(build_check.sign(SEED, data), encoding="ascii")


def _frozen_signed(tmp_path: Path, arc: FakeArchive | None = None) -> tuple[Path, FakeArchive]:
    pkg = _bundle(tmp_path)
    arc = arc or _archive()
    found, _ = build_check.collect(str(pkg), True, arc)
    _sign_into(pkg, found, build_check.FROZEN)
    return pkg, arc


def _check_frozen(pkg: Path, arc: FakeArchive, version: str = "9.9.9") -> Verdict:
    return build_check.check(str(pkg), True, version, archive=arc, keys=KEYS)


# -- the list, made and checked ----------------------------------------------------

def test_a_frozen_bundle_signed_is_official_and_lists_its_modules_and_data(tmp_path):
    pkg, arc = _frozen_signed(tmp_path)
    doc = json.loads((pkg / build_check.MANIFEST).read_text())
    assert doc["mode"] == "frozen" and doc["version"] == "9.9.9"
    assert set(doc["files"]) == {"dopeiptv/__init__.py", "dopeiptv/app.py", "dopeiptv/ui/__init__.py",
                                 "dopeiptv/ui/theme.py", "dopeiptv/locale/de.json",
                                 "dopeiptv/assets/logo.svg"}
    assert doc["files"]["dopeiptv/app.py"] == hashlib.sha256(b"app").hexdigest()
    v = _check_frozen(pkg, arc)
    assert v.kind == OFFICIAL and v.line() == "Build: official"


def test_a_changed_module_an_extra_file_and_a_missing_one_are_named(tmp_path):
    pkg, _arc = _frozen_signed(tmp_path)
    v = _check_frozen(pkg, _archive(dopeiptv__ui__theme=b"a fork's theme"))
    assert v.kind == MODIFIED and v.changed == ("dopeiptv/ui/theme.py",)
    assert v.line() == "Build: modified (ui/theme.py)"

    (pkg / "custom_skin.py").write_text("SKIN = 1\n")
    v = _check_frozen(pkg, _archive())
    assert v.kind == MODIFIED and v.extra == ("dopeiptv/custom_skin.py",)
    assert "custom_skin.py" in v.line()
    (pkg / "custom_skin.py").unlink()

    (pkg / "locale" / "de.json").unlink()
    (pkg / "assets" / "logo.svg").write_text("<svg>other</svg>")
    v = _check_frozen(pkg, _archive())
    assert v.kind == MODIFIED and v.missing == ("dopeiptv/locale/de.json",)
    assert v.changed == ("dopeiptv/assets/logo.svg",) and v.count == 2


def test_a_module_put_into_the_archive_is_extra_and_an_unlisted_data_file_no_code(tmp_path):
    pkg, _arc = _frozen_signed(tmp_path)
    v = _check_frozen(pkg, _archive(dopeiptv__custom_skin=b"x"))
    assert v.kind == MODIFIED and v.names == ("custom_skin.py",)
    (pkg / "locale" / "xx.json").write_text("{}")
    assert _check_frozen(pkg, _archive()).kind == OFFICIAL


def test_no_list_a_bad_signature_another_key_or_version_is_modified(tmp_path):
    pkg = _bundle(tmp_path)
    arc = _archive()
    v = _check_frozen(pkg, arc)
    assert v.kind == MODIFIED and v.reason == "unsigned"
    assert v.line() == "Build: modified (no signed file list)"
    pkg, arc = _frozen_signed(tmp_path / "b")
    (pkg / build_check.SIGNATURE).write_text("bm90IGEgc2lnbmF0dXJl\n")
    assert _check_frozen(pkg, arc).reason == "signature"
    # A list signed with a key the app does not carry.
    other = bytes(range(64, 96))
    data = (pkg / build_check.MANIFEST).read_bytes()
    (pkg / build_check.SIGNATURE).write_text(build_check.sign(other, data))
    assert _check_frozen(pkg, arc).reason == "signature"
    # Signed by the right key, but the list was changed after.
    (pkg / build_check.SIGNATURE).write_text(build_check.sign(SEED, data))
    (pkg / build_check.MANIFEST).write_bytes(data.replace(b"9.9.9", b"9.9.8"))
    assert _check_frozen(pkg, arc).reason == "signature"
    (pkg / build_check.MANIFEST).write_bytes(data)
    assert _check_frozen(pkg, arc).kind == OFFICIAL
    assert _check_frozen(pkg, arc, version="9.9.10").reason == "version"


def test_names_are_capped_with_how_many_there_are_in_all():
    v = Verdict(MODIFIED, reason="files", extra=("dopeiptv/custom_skin.py",),
                changed=tuple(f"dopeiptv/m{i}.py" for i in range(6)), count=7)
    assert v.line() == "Build: modified (custom_skin.py, m0.py, m1.py, … 7 files)"


def test_not_frozen_is_from_source_with_the_checkout_commit(tmp_path):
    pkg = _tree(tmp_path)
    assert build_check.check(str(pkg), False, "9.9.9", keys=KEYS) == Verdict(SOURCE)
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/testing\n")
    (git / "refs" / "heads" / "testing").write_text("3078763" + "a" * 33 + "\n")
    v = build_check.check(str(pkg), False, "9.9.9", keys=KEYS)
    assert v.kind == SOURCE and v.git == "3078763"
    assert v.line() == "Build: from source (git 3078763)"
    # A list left in a checkout is not read: it is a checkout.
    _sign_into(pkg, {"dopeiptv/nothing.py": "0" * 64}, build_check.TREE)
    assert build_check.check(str(pkg), False, "9.9.9", keys=KEYS).kind == SOURCE


def test_a_worktree_and_packed_refs_give_the_commit(tmp_path):
    common = tmp_path / "main.git"
    wt = common / "worktrees" / "a"
    wt.mkdir(parents=True)
    (wt / "HEAD").write_text("ref: refs/heads/official-build\n")
    (wt / "commondir").write_text("../..\n")
    (common / "packed-refs").write_text("# pack-refs\n" + "c62072c" + "b" * 33 + " refs/heads/official-build\n")
    root = tmp_path / "checkout"
    root.mkdir()
    (root / ".git").write_text(f"gitdir: {wt}\n")
    assert build_check.git_head(str(root)) == "c62072c"
    assert build_check.git_head(str(tmp_path / "none")) == ""


def test_this_copy_from_a_checkout_is_from_source_and_its_services_on():
    assert not build_check.is_frozen()
    assert build_check.kind() == SOURCE and build_check.allowed()
    assert build_check.headers() == {HEADER: "source"}
    build_check.refuse("sync")


def test_worked_out_once_on_a_thread_of_its_own(monkeypatch):
    calls = []

    def compute():
        calls.append(1)
        return Verdict(OFFICIAL)

    monkeypatch.setattr(build_check, "_compute", compute)
    build_check.start()
    assert build_check.verdict().kind == OFFICIAL
    build_check.start()
    assert build_check.kind() == OFFICIAL and calls == [1]


def test_the_bundle_says_what_it_holds_for_the_build(tmp_path):
    out = tmp_path / "files.json"
    assert build_check.files_main([str(out)]) == 0
    got = json.loads(out.read_text())
    assert got["frozen"] is False and got["mode"] == "tree"
    assert "dopeiptv/core/build_check.py" in got["files"]
    assert got["verdict"]["kind"] == SOURCE
    assert build_check.files_main([]) == 2


# -- tools/build_manifest.py --------------------------------------------------------

def _tool():
    spec = importlib.util.spec_from_file_location("build_manifest", ROOT / "tools" / "build_manifest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_tool_signs_a_package_pip_installs_and_it_checks_out(tmp_path, monkeypatch):
    bm = _tool()
    pkg = _tree(tmp_path / "src")
    (pkg / "_tmdb_key.py.example").write_text("x")          # not installed: not listed
    (pkg / "scripts").mkdir()
    (pkg / "scripts" / "loose.py").write_text("x")           # no package: not installed
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[tool.setuptools.package-data]\ndopeiptv = ["locale/*.json", "assets/*.svg"]\n')
    monkeypatch.setenv("DOPEIPTV_UPDATE_KEY", base64.b64encode(SEED).decode())
    assert bm.main(["tree", str(pkg), "--pyproject", str(pyproject)], keys=KEYS) == 0
    listed = json.loads((pkg / build_check.MANIFEST).read_text())["files"]
    assert set(listed) == {"dopeiptv/__init__.py", "dopeiptv/core/__init__.py", "dopeiptv/core/x.py",
                           "dopeiptv/locale/de.json", "dopeiptv/assets/logo.svg"}
    # Installed as pip would (no loose script, no example, its own __pycache__).
    inst = tmp_path / "site-packages" / "dopeiptv"
    for rel in [*(k.split("/", 1)[1] for k in listed), build_check.MANIFEST, build_check.SIGNATURE]:
        (inst / rel).parent.mkdir(parents=True, exist_ok=True)
        (inst / rel).write_bytes((pkg / rel).read_bytes())
    (inst / "__pycache__").mkdir()
    (inst / "__pycache__" / "x.cpython-313.pyc").write_bytes(b"\0")
    assert build_check.check(str(inst), False, "9.9.9", keys=KEYS).kind == OFFICIAL
    (inst / "custom_skin.py").write_text("x")
    assert build_check.check(str(inst), False, "9.9.9", keys=KEYS).names == ("custom_skin.py",)


def test_the_tool_without_the_key_writes_nothing_and_a_release_stops(tmp_path, monkeypatch):
    bm = _tool()
    pkg = _tree(tmp_path)
    monkeypatch.delenv("DOPEIPTV_UPDATE_KEY", raising=False)
    assert bm.main(["tree", str(pkg)], keys=KEYS) == 0
    assert not (pkg / build_check.MANIFEST).exists()
    with pytest.raises(SystemExit):
        bm.main(["tree", str(pkg), "--require-key"], keys=KEYS)
    # A secret whose public half the app does not carry signs nothing.
    monkeypatch.setenv("DOPEIPTV_UPDATE_KEY", base64.b64encode(bytes(range(64, 96))).decode())
    with pytest.raises(SystemExit):
        bm.main(["tree", str(pkg)], keys=KEYS)
    assert not (pkg / build_check.MANIFEST).exists()


def _asker(pkg: Path, arc: FakeArchive, keys: tuple[str, ...] = KEYS):
    """The bundle's --build-files, answered in this process (*keys*: the
    ones the bundle carries)."""
    def ask(_app: str) -> dict:
        found, _ = build_check.collect(str(pkg), True, arc)
        v = build_check.check(str(pkg), True, "9.9.9", archive=arc, keys=keys)
        return {"frozen": True, "package": str(pkg), "version": "9.9.9", "files": found,
                "verdict": {"kind": v.kind, "summary": v.summary()}}
    return ask


def test_the_tool_signs_a_frozen_bundle_and_asks_it_again(tmp_path, monkeypatch):
    bm = _tool()
    pkg, arc = _bundle(tmp_path), _archive()
    monkeypatch.setattr(bm, "_ask", _asker(pkg, arc))
    monkeypatch.setenv("DOPEIPTV_UPDATE_KEY", base64.b64encode(SEED).decode())
    assert bm.frozen("dopeiptv", require=True, keys=KEYS) == 0
    assert _check_frozen(pkg, arc).kind == OFFICIAL
    # A bundle that would not call itself official (it carries another
    # key) stops the build.
    monkeypatch.setattr(bm, "_ask", _asker(pkg, arc, keys=(base64.b64encode(b"\1" * 32).decode(),)))
    with pytest.raises(SystemExit):
        bm.frozen("dopeiptv", require=True, keys=KEYS)


def test_in_a_mac_app_the_list_lies_in_resources_linked_from_frameworks(tmp_path, monkeypatch):
    bm = _tool()
    contents = tmp_path / "dopeIPTV.app" / "Contents"
    pkg = _bundle(contents / "Frameworks")
    (contents / "Resources").mkdir()
    arc = _archive()
    monkeypatch.setattr(bm, "_ask", _asker(pkg, arc))
    monkeypatch.setenv("DOPEIPTV_UPDATE_KEY", base64.b64encode(SEED).decode())
    assert bm.frozen("dopeiptv", require=False, keys=KEYS) == 0
    kept = contents / "Resources" / "dopeiptv" / build_check.MANIFEST
    assert kept.is_file() and (pkg / build_check.MANIFEST).is_symlink()
    assert _check_frozen(pkg, arc).kind == OFFICIAL


# -- dopeiptv.org's services: the header, and off in a modified build ------------------

class _Resp:
    def __init__(self, status: int = 200, body: dict | None = None, content: bytes = b"") -> None:
        self.status_code, self._body, self.content = status, body or {}, content
        self.headers = {"Content-Length": str(len(content))}

    def json(self):
        return self._body

    def raise_for_status(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, chunk_size: int):
        yield self.content


@pytest.fixture
def net(monkeypatch):
    """Every request through requests, kept: (method, url, headers)."""
    import requests
    calls: list[tuple[str, str, dict]] = []

    def get(url, **kw):
        calls.append(("GET", url, dict(kw.get("headers") or {})))
        if url.endswith("releases.json"):
            return _Resp(body={"version": "1.5.1", "assets": []})
        return _Resp(content=b"abc")

    def post(url, **kw):
        calls.append(("POST", url, dict(kw.get("headers") or {})))
        return _Resp(body={"replies": {}}, content=b'{"replies": {}}')

    def request(method, url, **kw):
        calls.append((method, url, dict(kw.get("headers") or {})))
        return _Resp(404)

    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "request", request)
    return calls


def test_every_request_to_dopeiptv_org_says_the_build(net):
    from dopeiptv.core import report, selfupdate, sync, updates
    updates.fetch_site_release()
    assert report.send("feedback", "Nice.")
    report.fetch_replies(["code"])
    sync.Remote("slot").get()
    for method, url, headers in net:
        assert url.startswith("https://dopeiptv.org/"), url
        assert headers.get(HEADER) == "source", (method, url)
    net.clear()
    asset = {"name": "x.AppImage", "size": 3, "digest": "sha256:" + hashlib.sha256(b"abc").hexdigest()}
    with tempfile.TemporaryDirectory() as d:
        selfupdate.download(dict(asset, url="https://dopeiptv.org/dl.php?f=x.AppImage"), Path(d) / "a")
        selfupdate.download(dict(asset, url="https://github.com/slimture/dopeIPTV/x.AppImage"), Path(d) / "b")
    assert HEADER in net[0][2] and HEADER not in net[1][2]


def test_the_together_relay_says_the_build_and_asks_nothing_when_modified():
    from dopeiptv.core import together
    calls = []

    class Session:
        def request(self, method, url, **kw):
            calls.append(kw["headers"])
            return _Resp(body={"now": 7})

        def close(self):
            pass

    relay = together.Relay("room", "m" * 16)
    relay._session = Session()
    assert relay.post(b"sealed") == 7 and calls[-1][HEADER] == "source"
    build_check.set_verdict(_modified("custom_skin.py"))
    relay = together.Relay("room", "m" * 16)
    relay._session = Session()
    with pytest.raises(together.RelayError) as e:
        relay.post(b"sealed")
    assert e.value.kind == "modified" and len(calls) == 1


def test_a_modified_build_asks_nothing_of_the_site(net):
    from dopeiptv.core import macupdate, report, selfupdate, sync, updates, winupdate
    build_check.set_verdict(_modified("custom_skin.py"))
    assert not build_check.allowed() and build_check.headers() == {HEADER: "modified"}
    with pytest.raises(build_check.NotOfficial):
        updates.fetch_update()
    for mod in (selfupdate, winupdate, macupdate):
        with pytest.raises(build_check.NotOfficial):
            mod.install({"tag": "v9.9.9", "assets": []}, "1.0.0")
    assert not report.send("error", "trace")
    assert report.fetch_replies(["code"]) is None
    with pytest.raises(sync.SyncError) as e:
        sync.Remote("slot").get()
    assert e.value.kind == "modified"
    assert net == []


def test_each_place_says_it_in_one_line():
    from dopeiptv.i18n import tr
    for service in build_check.SERVICES:
        line = build_check.notice(service)
        assert line == tr("build_not_official", service=tr(build_check.SERVICES[service]))
        assert line.startswith("This is not an official version of dopeIPTV.") and "dopeiptv.org" in line
    assert build_check.notice("sync") == ("This is not an official version of dopeIPTV. "
                                          "Settings sync is only in the official app, from dopeiptv.org.")
    assert "Watch together" in build_check.NotOfficial("together").localized()


# -- the report's header -------------------------------------------------------------

def test_the_report_header_says_the_build():
    from dopeiptv.core import report
    assert "Build: from source" in report.build("error", trace="x", with_log=False)
    build_check.set_verdict(Verdict(MODIFIED, reason="files", extra=("dopeiptv/custom_skin.py",),
                                    changed=("dopeiptv/a.py", "dopeiptv/b.py", "dopeiptv/c.py"), count=4))
    text = report.build("error", trace="x", with_log=False)
    assert "Build: modified (custom_skin.py, a.py, b.py, … 4 files)" in text.splitlines()
    build_check.set_verdict(Verdict(OFFICIAL))
    assert "Build: official" in report.system_lines()


# -- the window -----------------------------------------------------------------------

_APP: list = []


def _qapp():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
    app = QApplication.instance() or QApplication([])
    _APP[:] = [app]                       # kept: a QApplication let go takes Qt down
    return app


def _about(monkeypatch) -> tuple[list[str], list]:
    _qapp()
    from PyQt6.QtWidgets import QDialog, QLabel, QMainWindow

    from dopeiptv.ui import mw_settings

    class Host(QMainWindow):
        pool = None
        _format_release_notes = staticmethod(mw_settings._SettingsMixin._format_release_notes)

        def _update_action_label(self):
            return "Update now"

        def _update_channel(self):
            return "stable"

        def _update_action(self):
            pass

        def _remember_update(self, rel):
            pass

    asked: list = []
    monkeypatch.setattr(mw_settings, "run_async", lambda pool, fn, done, fail: asked.append(fn))
    seen: list[str] = []

    def fake_exec(dialog):
        seen.extend(lbl.text() for lbl in dialog.findChildren(QLabel)
                    if lbl.objectName() == "AboutBuild")
        return 0

    monkeypatch.setattr(QDialog, "exec", fake_exec)
    mw_settings._SettingsMixin.show_about(Host())
    return seen, asked


def test_about_says_a_build_is_not_official_and_asks_for_no_update(monkeypatch):
    from dopeiptv.i18n import tr
    build_check.set_verdict(_modified("custom_skin.py"))
    seen, asked = _about(monkeypatch)
    assert seen == [tr("about_build_modified")] and asked == []
    assert seen[0] == ("Not an official build - online services are off. "
                       "Get the official app at dopeiptv.org.")


def test_about_says_running_from_source_and_an_official_build_says_nothing(monkeypatch):
    seen, asked = _about(monkeypatch)
    assert seen == ["Running from source"] and len(asked) == 1
    build_check.set_verdict(Verdict(OFFICIAL))
    seen, asked = _about(monkeypatch)
    assert seen == [] and len(asked) == 1


def test_a_modified_build_offers_the_report_to_copy_instead(monkeypatch):
    app = _qapp()
    from PyQt6.QtCore import QSettings, QThreadPool
    from PyQt6.QtWidgets import QWidget

    from dopeiptv.core import report
    from dopeiptv.i18n import tr
    from dopeiptv.ui import dialogs
    from dopeiptv.ui.mw_report import _ReportMixin

    class Win(_ReportMixin, QWidget):
        def __init__(self):
            super().__init__()
            self.settings = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
            self.pool = QThreadPool()
            self.toasts: list[str] = []

        def _show_toast(self, text, ms=0):
            self.toasts.append(text)

    shown: list = []

    def fake_exec(dlg):
        shown.append((dlg.copy_only, dlg.send_btn.text(),
                      dlg.not_official.text() if dlg.not_official is not None else ""))
        return 1

    monkeypatch.setattr(dialogs.ReportDialog, "exec", fake_exec)
    monkeypatch.setattr(report, "send", lambda *a, **k: pytest.fail("a modified build sent a report"))
    build_check.set_verdict(_modified("custom_skin.py"))
    w = Win()
    w.open_report("error", "Traceback (most recent call last):\nValueError: boom")
    assert shown == [(True, tr("build_copy_report"), tr("build_reports_elsewhere"))]
    assert shown[0][2] == "This is not an official version of dopeIPTV, so reports go to whoever made it."
    text = app.clipboard().text()
    assert "ValueError: boom" in text and "Build: modified (custom_skin.py)" in text
    assert w.toasts == [tr("build_report_copied")]
    fb = dialogs.FeedbackDialog(None, lambda opts: "", copy_only=True)
    assert fb.send_btn.text() == tr("build_copy_report") and not fb.answers.isChecked()
    assert not fb.answers.isVisibleTo(fb) and fb.not_official is not None
    plain = dialogs.ReportDialog(None, "error", "x")
    assert plain.not_official is None and plain.send_btn.text() == tr("report_send")


def test_watch_together_and_sync_say_why_they_are_off(monkeypatch, tmp_path, net):
    _qapp()
    from PyQt6.QtCore import QObject, QSettings
    from PyQt6.QtWidgets import QWidget

    from dopeiptv.core import sync
    from dopeiptv.core.stores import PlaylistStore
    from dopeiptv.ui.mw_sync import _SyncMixin
    from dopeiptv.ui.mw_together import _TogetherMixin
    from dopeiptv.ui.together_ui import TogetherDialog

    build_check.set_verdict(_modified("custom_skin.py"))

    class Room(_TogetherMixin, QObject):
        def __init__(self):
            QObject.__init__(self)
            self._init_together()

    room = Room()
    assert room.together_start("Anna") == build_check.notice("together")
    assert room._together_room is None and room.together_view()["unofficial"]
    dlg = TogetherDialog(room)
    dlg.name.setText("Anna")
    assert dlg.official_lbl.isVisibleTo(dlg) and not dlg.start_btn.isEnabled()
    assert dlg.official_lbl.text() == build_check.notice("together")

    class Win(_SyncMixin, QWidget):
        def __init__(self):
            super().__init__()
            self.settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
            self._resume_settings = QSettings(str(tmp_path / "r.ini"), QSettings.Format.IniFormat)
            self.playlist_store = PlaylistStore(self.settings)
            self._sync_endpoint = "https://dopeiptv.org/sync/"
            self._sync_state_file = tmp_path / "sync.json"
            self._init_sync()

        def _show_toast(self, text, ms=0):
            pass

    w = Win()
    w.settings.setValue(sync.K_CODE, sync.format_code(sync.parse_code(sync.new_code())))
    assert not w.sync_now(manual=True)
    assert w._sync_status[0] == "modified"
    assert w._sync_status_text()[1] == build_check.notice("sync")
    assert net == []


# -- the release workflow --------------------------------------------------------------

def test_every_frozen_build_signs_its_manifest_before_it_is_packed():
    text = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    steps = text.split("      - name: ")

    def step(name_start: str, after: str) -> str:
        i = next(n for n, s in enumerate(steps) if s.startswith(after))
        return next(s for s in steps[i:] if s.startswith(name_start))

    for after, run in (("PyInstaller bundle\n        run: pyinstaller --noconfirm dopeiptv.spec",
                        "python tools/build_manifest.py frozen dist/dopeiptv/dopeiptv "),
                       ("PyInstaller bundle (.app)",
                        "python tools/build_manifest.py frozen dist/dopeIPTV.app/Contents/MacOS/dopeiptv "),
                       ("PyInstaller bundle\n        shell: bash",
                        "python tools/build_manifest.py frozen dist/dopeiptv/dopeiptv.exe ")):
        s = step("Build manifest", after)
        assert "DOPEIPTV_UPDATE_KEY: ${{ secrets.DOPEIPTV_UPDATE_KEY }}" in s and run in s
        assert "--require-key" in s and "startsWith(github.ref, 'refs/tags/')" in s
    order = [text.index(m) for m in ("frozen dist/dopeiptv/dopeiptv ", "- name: Build AppImage",
                                     "- name: Build .deb")]
    assert order == sorted(order)
    order = [text.index(m) for m in ("frozen dist/dopeIPTV.app", "- name: Sign the .app",
                                     "\"$BIN\" --build-files")]
    assert order == sorted(order)
    order = [text.index(m) for m in ("frozen dist/dopeiptv/dopeiptv.exe", "- name: Installer (NSIS)",
                                     "- name: Package portable zip")]
    assert order == sorted(order)
    tree = text.index("python3 tools/build_manifest.py tree dopeiptv")
    assert tree < text.index("uses: flatpak/flatpak-github-actions/flatpak-builder@v6")
    assert (ROOT / "tools" / "build_manifest.py").is_file()
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "dopeiptv/_build_manifest.json\n" in ignored and "dopeiptv/_build_manifest.json.sig\n" in ignored
    assert '"_build_manifest.json", "_build_manifest.json.sig"' in \
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_the_bundle_answers_the_build_before_anything_else_starts():
    script = (ROOT / "dopeiptv.py").read_text(encoding="utf-8")
    assert 'if sys.argv[1:2] == ["--build-files"]:' in script
    assert script.index("--build-files") < script.index("maybe_preload_allocator()")
