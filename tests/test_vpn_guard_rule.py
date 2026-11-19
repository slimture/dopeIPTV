"""Nerd's VPN guard: one rule for the window, recordings and multiview.

A stream on the home network - a box's source whatever its address, or any
source's stream at a home address (a private or link-local IP, a name with
no dot like "vuduo2", .local/.lan/.home/.internal/.home.arpa, fritz.box) -
plays without a VPN unless the guard covers the home network too; the
user's own files always play. The window, a recording and a multiview cell
give the same answer for the same address."""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from dopeiptv.core import nerd  # noqa: E402

LAN = ["http://vuduo2:8001/1:0:19:283D:3FB:1:C00000:0:0:0:",
       "http://tvheadend.lan:9981/stream/channel/abc",
       "http://fritz.box/dvb/m3u/tvsd.m3u",
       "http://FRITZ.BOX./dvb/m3u/tvhd.m3u",
       "http://192.168.1.5:8001/x", "HTTP://10.0.0.2/x", "http://[fd00::1]:80/x",
       "http://[fe80::1%25eth0]/x", "http://127.0.0.1:5004/auto/v1", "http://localhost/x",
       "http://nas.local/x", "http://box.home/x", "http://server.internal/x",
       "http://tv.home.arpa/x", "rtsp://cam/x"]
FILES = ["/home/u/Videos/dopeIPTV/rec.ts", "file:///home/u/rec.ts", "C:\\Users\\u\\rec.ts",
         "rec.ts"]
WAN = ["http://provider.example.com:8080/live/u/p/1.ts", "https://cdn.example.net/x.m3u8",
       "http://8.8.8.8/x", "http://[2001:4860:4860::8888]/x", "udp://@239.1.1.1:1234", ""]


class _S(dict):
    def value(self, key, default=None):
        return self.get(key, default)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def guard(monkeypatch):
    """The guard on, no VPN up; *lan*: it covers the home network too."""
    monkeypatch.setattr(nerd, "active_tunnels", lambda: [])

    def make(lan: bool = False, kind: str | None = None):
        nerd.install(_S(nerd_enabled="true", nerd_vpn_guard="true",
                        nerd_vpn_lan="true" if lan else "false"))
        nerd.set_source({"kind": kind} if kind else None)
    yield make
    nerd.install(None)
    nerd.set_source(None)


def test_home_addresses_and_files_are_local():
    for url in LAN:
        assert nerd.is_lan_url(url), url
        assert not nerd.is_own_file(url), url
    for url in FILES:
        assert nerd.is_own_file(url) and nerd.is_lan_url(url), url
    for url in WAN:
        assert not nerd.is_lan_url(url) and not nerd.is_own_file(url), url
    # A box's source is on the home network whatever its address says.
    assert nerd.is_local_stream(WAN[0], "tvheadend")
    assert not nerd.is_local_stream(WAN[0], "xtream")
    # The events sent to a home system go there directly by the same rule.
    from dopeiptv.core import events
    assert events._no_proxy("vuduo2") and events._no_proxy("fritz.box")
    assert not events._no_proxy("2001:4860:4860::8888") and not events._no_proxy("example.com")


def test_files_always_play_and_home_streams_unless_covered(guard):
    guard(lan=False)
    for url in FILES + LAN:
        assert not nerd.blocks_stream(url), url
    for url in WAN[:-1]:
        assert nerd.blocks_stream(url), url
    assert not nerd.blocks_stream(WAN[0], "enigma2")
    guard(lan=True)
    for url in FILES:
        assert not nerd.blocks_stream(url), url
    for url in LAN + WAN[:-1]:
        assert nerd.blocks_stream(url), url
    assert nerd.blocks_stream(WAN[0], "enigma2")


def test_an_address_is_judged_by_where_it_goes_not_how_it_is_spelt(guard):
    """http://134744072/ reaches 8.8.8.8 - the system's resolver takes an
    integer, octal, hex or short IPv4 - but had no dot, so it counted as a
    box's name on the home network and played past the guard. And a
    Windows network path (//server/share, \\\\server\\share,
    file://server/...) is on another computer, not the person's own file."""
    for host in ("134744072", "0x08080808", "010.010.010.010", "8.8.2056", "0x8.0x8.0x8.0x8",
                 "::ffff:8.8.8.8"):
        assert nerd.ipv4_any(host) is None or str(nerd.ipv4_any(host)) == "8.8.8.8", host
        assert not nerd.is_home_host(host), host
    for host in ("3232235777", "0xc0a80101", "10.1", "127.1", "0177.0.0.1", "::ffff:192.168.1.1"):
        assert nerd.is_home_host(host), host
    for host in ("vuduo2", "4294967296", "1.2.3.4.5.lan"):
        assert nerd.is_home_host(host), host
    for bad in ("1.2.3.256", "256.1.1", "0x1ffffffff", "1..2", "08", "²", "1" * 11):
        assert nerd.ipv4_any(bad) is None, bad
    away = ["http://134744072/live.ts", "http://0x08080808/x", "//8.8.8.8/share/x.ts",
            "\\\\134744072\\share\\x.ts", "file://8.8.8.8/share/x.ts",
            "\\\\?\\UNC\\8.8.8.8\\share\\x.ts"]
    for url in away:
        assert not nerd.is_own_file(url) and not nerd.is_lan_url(url), url
    nas = ["//nas/share/x.ts", "\\\\nas\\share\\x.ts", "file://nas.local/x.ts",
           "file:////192.168.1.2/x.ts"]
    for url in nas:
        assert not nerd.is_own_file(url) and nerd.is_lan_url(url), url
    for url in ("\\\\?\\C:\\Users\\u\\rec.ts", "file://localhost/home/u/rec.ts", "file:///C:/rec.ts"):
        assert nerd.is_own_file(url), url
    guard(lan=False)
    for url in away:
        assert nerd.blocks_stream(url), url
    for url in nas:
        assert not nerd.blocks_stream(url), url
    guard(lan=True)
    for url in nas:
        assert nerd.blocks_stream(url), url


def _window_says(url: str) -> bool:
    from dopeiptv.ui.main_window import MainWindow

    class W:
        toasts: list = []

        def _show_toast(self, text, ms=0):
            self.toasts.append(text)
    w = W()
    blocked = MainWindow._vpn_guard_blocks(w, url)
    assert bool(w.toasts) == blocked
    return blocked


def _recording_says(url: str, kind: str | None, tmp_path) -> bool:
    from PyQt6.QtCore import QSettings

    from dopeiptv.core.recording import RecordingManager
    s = QSettings(str(tmp_path / "r.ini"), QSettings.Format.IniFormat)
    s.setValue("recordings_dir", str(tmp_path / "rec"))
    rec = RecordingManager(s)
    rec._timer.stop()
    rec.recorder = lambda: (None, None)            # no process, whatever the answer
    j = {"url": url, "title": "x", "stop": None, "source_kind": kind}
    rec._spawn(j)
    return j.get("error") == "no VPN up (Nerd's VPN guard)"


def _cell_says(url: str) -> bool:
    from dopeiptv.ui.mw_multiview import _MultiviewCell

    class Video:
        mpv = None
        shown = False

        def show(self):
            self.shown = True

    class Cell:
        title = ""
        video = Video()
    cell = Cell()
    assert _MultiviewCell._mpv_play(cell, url) is False     # no mpv here either way
    return not cell.video.shown


@pytest.mark.parametrize("lan", [False, True])
@pytest.mark.parametrize("kind", [None, "xtream", "tvheadend"])
def test_the_window_recordings_and_multiview_agree(app, guard, tmp_path, lan, kind):
    guard(lan=lan, kind=kind)
    for url in LAN + WAN[:-1] + FILES:
        window = _window_says(url)
        assert window == nerd.blocks_stream(url, kind), url
        assert _recording_says(url, kind, tmp_path) == window, url
        assert _cell_says(url) == window, url
