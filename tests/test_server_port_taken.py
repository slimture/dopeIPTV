"""Server mode's port in use: whose it is - dopeIPTV's own server (the
window's, a ``--server``'s, an older copy's) or another program - asked on
loopback, the next free port found past the taken ones and the app's other
servers' ports, offered and taken only on a click; in the window (its note
and the Nerd page's line) and in the headless server's log. Any other reason
a port does not open is said as before."""
from __future__ import annotations

import errno
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QCoreApplication, QObject, QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from dopeiptv import i18n  # noqa: E402
from dopeiptv.core import hdhr  # noqa: E402
from dopeiptv.core.dvr_pool import DvrPool  # noqa: E402
from dopeiptv.core.nerd_check import FAIL, OK, WARN  # noqa: E402
from dopeiptv.services import home_server as hs  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield


def _pump_until(cond, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return bool(cond())


class Blocker:
    """Another program on a port: a plain socket that takes each connection
    and closes it, saying nothing."""

    def __init__(self, host: str = "0.0.0.0", port: int = 0) -> None:
        self.sock = socket.socket()
        self.sock.bind((host, port))
        self.sock.listen(8)
        self.sock.settimeout(0.1)
        self.port = self.sock.getsockname()[1]
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _addr = self.sock.accept()
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                return
            conn.close()

    def close(self) -> None:
        self._stop.set()
        self.sock.close()


class Host:
    """A server's host with nothing to serve."""

    def __init__(self, kind: str | None = None) -> None:
        if kind is not None:
            self.kind = kind

    def sources(self):
        return []

    def filters(self, pid):
        return set(), set(), {}

    def app_use(self, pid):
        return 0, []

    def app_session(self, pid, sid):
        return None

    def pool(self):
        return None


def _home_server(host, bind: str = "127.0.0.1") -> hs.HomeServer:
    cfg = hs.Config(port=0, token="t" * 24, device_id=hdhr.new_device_id())
    srv = hs.HomeServer(host, cfg, bind=bind, discovery_port=0)
    srv.start()
    return srv


def _free_run(n: int, host: str = "127.0.0.1") -> list[socket.socket]:
    """*n* consecutive ports bound now (each socket holds its port)."""
    for _ in range(200):
        probe = socket.socket()
        probe.bind((host, 0))
        base = probe.getsockname()[1]
        probe.close()
        if base + n > 65535:
            continue
        held: list[socket.socket] = []
        try:
            for p in range(base, base + n):
                s = socket.socket()
                held.append(s)
                s.bind((host, p))
        except OSError:
            for s in held:
                s.close()
            continue
        return held
    raise RuntimeError("no run of free ports")


# -- what a port error is, who holds a port, which is free -------------------------------------

def test_only_a_port_in_use_counts_as_taken():
    assert hs.port_taken(OSError(errno.EADDRINUSE, "Address already in use"))
    assert hs.port_taken(OSError(10048, "Only one usage of each socket address"))

    class WinError(OSError):
        winerror = 10048
    assert hs.port_taken(WinError(0, "WSAEADDRINUSE"))
    assert not hs.port_taken(PermissionError(errno.EACCES, "Permission denied"))
    assert not hs.port_taken(OSError(errno.EADDRNOTAVAIL, "Cannot assign requested address"))
    assert not hs.port_taken(ValueError("port"))


def test_a_plain_socket_on_the_port_is_another_program():
    b = Blocker("127.0.0.1")
    try:
        taken = hs.look_at_port(b.port, bind="127.0.0.1")
        assert taken.holder == hs.HOLDER_OTHER and not taken.ours
        assert taken.key == "nerd_ck_server_taken"
        assert "another program" in i18n.tr(taken.key, port=b.port)
        assert taken.free is not None and taken.free > b.port
        assert hs.bindable(taken.free, "127.0.0.1")
    finally:
        b.close()
    # One that takes the connection and never answers: another program too,
    # once its time is up.
    mute = socket.socket()
    mute.bind(("127.0.0.1", 0))
    mute.listen(1)
    try:
        started = time.monotonic()
        assert hs.port_holder(mute.getsockname()[1], timeout=0.3) == hs.HOLDER_OTHER
        assert time.monotonic() - started < 3
    finally:
        mute.close()


def _headless_host(tmp_path):
    from dopeiptv.headless import HeadlessHost
    return HeadlessHost(QSettings(str(tmp_path / "headless.ini"), QSettings.Format.IniFormat))


def test_dopeiptv_s_own_server_is_known_by_its_mark(tmp_path):
    for host, holder, key in ((_headless_host(tmp_path), hs.HOLDER_HEADLESS, "nerd_ck_server_ours"),
                              (Host("window"), hs.HOLDER_WINDOW, "nerd_ck_server_ours_app"),
                              (Host(), hs.HOLDER_OURS, "nerd_ck_server_ours")):
        srv = _home_server(host)
        try:
            taken = hs.look_at_port(srv.port, bind="127.0.0.1")
            assert taken.holder == holder and taken.ours and taken.key == key, taken
            assert taken.free is not None and taken.free != srv.port
        finally:
            srv.stop()
    assert "dopeIPTV's server is already running" in i18n.tr("nerd_ck_server_ours", port=1)


class _OldCopy(BaseHTTPRequestHandler):
    """A dopeIPTV server from before the mark (1.6.0): its name only - and a
    tuner emulator that is not dopeIPTV."""
    server_version = "dopeIPTV"
    sys_version = ""
    manufacturer = "dopeIPTV"

    def log_message(self, *a) -> None:
        pass

    def do_GET(self) -> None:
        body = json.dumps(hdhr.discover("1234567F", 1, "http://x")
                          | {"Manufacturer": self.manufacturer}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _Emulator(_OldCopy):
    server_version = "Threadfin"
    manufacturer = "Silicondust"


def test_a_copy_before_the_mark_is_known_by_its_name_and_others_are_not():
    for handler, holder in ((_OldCopy, hs.HOLDER_OURS), (_Emulator, hs.HOLDER_OTHER)):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            assert hs.port_holder(httpd.server_address[1]) == holder
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_the_next_free_port_skips_taken_ports_and_the_app_s_own(tmp_path):
    held = _free_run(5)
    base = held[0].getsockname()[1]
    try:
        for s in held[:3]:
            s.listen(1)                 # base and the next two: taken
        for s in held[3:]:
            s.close()
        assert hs.next_free_port(base, bind="127.0.0.1") == base + 3
        assert hs.next_free_port(base, avoid={base + 3}, bind="127.0.0.1") == base + 4
        assert hs.next_free_port(base, avoid={base + 3, base + 4}, bind="127.0.0.1",
                                 tries=4) is None
    finally:
        for s in held:
            s.close()
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    assert hs.avoided_ports(s) == {3673, 3674}
    s.setValue("nerd_remote_port", "4100")
    s.setValue("phone_remote_port", 3677)
    assert hs.avoided_ports(s) == {3673, 3674, 4100, 3677}


def test_the_line_says_whose_port_it_is_and_offers_the_next():
    t = hs.Taken(3675, hs.HOLDER_HEADLESS, 3676)
    ck = hs.check_server(True, False, 3675, "OSError: in use", None, None, t)
    assert ck.state == FAIL and ck.key == "nerd_ck_server_ours"
    assert ck.params == {"port": 3675, "free": 3676} and ck.detail == "OSError: in use"
    # A move to a port in use: it runs on the one before, and says so as a warning.
    ck = hs.check_server(True, True, 3675, "", {"in_use": 0, "tuners": 1}, {},
                         hs.Taken(3680, hs.HOLDER_OTHER, 3681))
    assert ck.state == WARN and ck.key == "nerd_ck_server_taken" and ck.params["port"] == 3680
    ck = hs.check_server(True, True, 3675, "", {"in_use": 0, "tuners": 1}, {}, None)
    assert ck.state == OK
    assert hs.check_server(True, False, 3675, "PermissionError", None, None).key == "nerd_ck_server_down"


# -- in the window -----------------------------------------------------------------------------

def _window(tmp_path, port: int):
    from dopeiptv.ui import mw_server
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    for k, v in (("nerd_enabled", "true"), ("nerd_server", "true"), ("nerd_server_port", str(port))):
        settings.setValue(k, v)
    pool = DvrPool(tmp_path / "dvr", 60.0, 10**9, min_free=0)

    class Player:
        live_hook = None

        def dvr_pool_for_server(self, _keep):
            return pool

    class Window(mw_server._ServerMixin, QObject):
        def __init__(self) -> None:
            super().__init__()
            self.settings, self.player = settings, Player()
            self.told: list[str] = []
            self.acts: list = []

        def _show_toast(self, text, _ms) -> None:
            self.told.append(text)

        def _show_action_toast(self, text, _ms, act) -> None:
            self.told.append(text)
            self.acts.append(act)
    return Window()


def test_the_window_says_another_program_and_takes_the_next_port_on_a_click(app, tmp_path):
    b = Blocker()
    w = _window(tmp_path, b.port)
    try:
        w._server_start()
        assert w._home is None and w._home_taken == b.port and not w.told
        assert _pump_until(lambda: w.told), "the note came"
        assert "another program" in w.told[0]
        free = w._home_offer
        assert free > b.port and f"use port {free} instead" in w.told[0]
        assert w.settings.value("nerd_server_port") == str(b.port)     # nothing without the click
        st = w._server_state()
        assert not st["running"] and st["taken"] == b.port and 3673 in st["avoid"]
        w.acts[0]()                                                     # the click
        assert w.settings.value("nerd_server_port") == str(free)
        assert w._home is not None and w._home.port == free and w._server_state()["running"]
        assert w._home_taken == 0
    finally:
        w._server_stop(wait=True)
        b.close()


def test_the_window_says_dopeiptv_s_own_server_has_it(app, tmp_path):
    holder = _home_server(_headless_host(tmp_path), bind="0.0.0.0")
    w = _window(tmp_path, holder.port)
    try:
        w._server_start()
        assert _pump_until(lambda: w.told), "the note came"
        assert "dopeIPTV's server is already running on this computer" in w.told[0]
        assert f"port {holder.port}" in w.told[0] and w._home_offer > holder.port
        assert w._home is None
    finally:
        w._server_stop(wait=True)
        holder.stop()


def test_a_move_to_a_port_in_use_keeps_the_server_and_offers_the_next(app, tmp_path):
    probe = socket.socket()
    probe.bind(("0.0.0.0", 0))
    first = probe.getsockname()[1]
    probe.close()
    w = _window(tmp_path, first)
    b = Blocker()
    try:
        assert w._server_apply()["running"]
        w.settings.setValue("nerd_server_port", str(b.port))
        st = w._server_apply()
        assert st["running"] and st["port"] == first and st["taken"] == b.port
        assert _pump_until(lambda: w.told), "the note came"
        assert "another program" in w.told[0]
        free = w._home_offer
        w.acts[0]()                                                     # the click
        assert w.settings.value("nerd_server_port") == str(free)
        assert w._home.port == free and w._server_state()["taken"] == 0
    finally:
        w._server_stop(wait=True)
        b.close()


def test_any_other_reason_is_said_as_before(app, tmp_path, monkeypatch):
    def refused(self) -> None:
        raise PermissionError(errno.EACCES, "Permission denied")
    monkeypatch.setattr(hs.HomeServer, "start", refused)
    w = _window(tmp_path, 3675)
    w._server_start()
    assert w.told == [i18n.tr("nerd_ck_server_down", port=3675)] and not w.acts
    st = w._server_state()
    assert st["taken"] == 0 and "PermissionError" in st["error"]
    ck = hs.check_server(st["on"], st["running"], st["port"], st["error"], st["status"],
                         st["addresses"])
    assert ck.key == "nerd_ck_server_down"


def test_the_nerd_line_offers_the_next_port_and_the_page_stores_it(app, tmp_path):
    from PyQt6.QtGui import QPixmap
    from PyQt6.QtWidgets import QDialog

    from dopeiptv.core import nerd
    from dopeiptv.ui import nerd_settings
    from dopeiptv.ui.nerd_check_line import CheckLine
    from dopeiptv.ui.settings_ui import SettingsPage, SettingsShell, Stepper, page_icon
    b = Blocker()
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    for k, v in (("nerd_enabled", "true"), ("nerd_server", "true"), ("nerd_server_port", str(b.port))):
        settings.setValue(k, v)
    shell = SettingsShell("Search", "none")
    page = shell.add_page(SettingsPage("nerd", "Nerd", ""),
                          page_icon("nerd", lambda k, sz, c: QPixmap(sz, sz)))

    def state() -> dict:
        return {"on": True, "running": False, "port": b.port, "error": "OSError: in use",
                "status": None, "addresses": None, "taken": b.port, "avoid": [3673, 3674]}
    d = QDialog()
    try:
        commit = nerd_settings.build(page, settings, d, server=state)
        line = next(x for x in page.findChildren(CheckLine)
                    if x._title == i18n.tr("nerd_card_server"))
        port = next(s for s in page.findChildren(Stepper) if s.value() == b.port)
        line.run()
        assert _pump_until(lambda: line.result is not None)
        assert line.result.key == "nerd_ck_server_taken"
        free = line.result.params["free"]
        assert free > b.port and line.action_btn is not None and not line.action_btn.isHidden()
        assert line.action_btn.text() == f"Use port {free}"
        assert port.value() == b.port                                   # nothing without the click
        line.action_btn.click()
        assert port.value() == free and line.action_btn.isHidden()
        stored: dict = {}
        commit(lambda k, v: stored.__setitem__(k, v))
        assert stored["nerd_server_port"] == str(free)
    finally:
        b.close()
        nerd.install(None)


# -- the headless server's log -----------------------------------------------------------------

def test_the_headless_log_names_the_holder_and_the_next_port():
    from dopeiptv.headless import taken_note
    note = taken_note(hs.Taken(3675, hs.HOLDER_OTHER, 3676))
    assert "another program" in note and "--port 3676" in note
    note = taken_note(hs.Taken(3675, hs.HOLDER_HEADLESS, None))
    assert "another `dopeiptv --server`" in note and "--port" not in note


def _env(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ, HOME=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path / "config"),
               XDG_CACHE_HOME=str(tmp_path / "cache"), XDG_DATA_HOME=str(tmp_path / "data"),
               DOPEIPTV_LOG="info", QT_QPA_PLATFORM="offscreen")
    env.pop("DOPEIPTV_LOG_FILE", None)
    return env


def test_headless_on_the_window_s_port_says_so_and_changes_nothing(tmp_path):
    holder = _home_server(Host("window"), bind="0.0.0.0")
    try:
        r = subprocess.run([sys.executable, str(ROOT / "dopeiptv.py"), "--server", "--port",
                            str(holder.port), "--no-discovery"], cwd=str(tmp_path),
                           env=_env(tmp_path), capture_output=True, text=True, timeout=90)
    finally:
        holder.stop()
    assert r.returncode == 2, r.stderr[-3000:]
    assert f"cannot listen on port {holder.port}" in r.stderr
    assert "dopeIPTV's own server has it - the app's window" in r.stderr, r.stderr[-3000:]
    m = re.search(r"port (\d+) is free: start with --port (\d+)", r.stderr)
    assert m and m.group(1) == m.group(2) and int(m.group(1)) > holder.port, r.stderr[-3000:]
