"""The demo's streams in one table, and the tool that asks them before a
release (tools/check_demo.py): every host named in docs/FEATURES.md, the
retired one gone, and the tool's own checks - https alone, the certificate
checked, a playlist and its first segment - against a server of the test's
own."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from dopeiptv.providers import demo, demo_streams

ROOT = Path(__file__).resolve().parent.parent


def _tool():
    spec = importlib.util.spec_from_file_location("check_demo", ROOT / "tools" / "check_demo.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_stream_is_https_and_every_host_is_told():
    """Each stream is https on a host docs/FEATURES.md names (the
    transparency page lists the same); the demo plays only what the table
    holds, and the retired host is nowhere in the app."""
    features = (ROOT / "docs" / "FEATURES.md").read_text(encoding="utf-8")
    assert demo_streams.hosts(), "no hosts"
    for host in demo_streams.hosts():
        assert host in features, host
    assert all(u.startswith("https://") for u in demo_streams.STREAMS.values())
    used = {key for *_r, key, _w in demo.CHANNELS} | {f[5] for f in demo.FILMS}
    assert used == set(demo_streams.STREAMS), used ^ set(demo_streams.STREAMS)
    client = demo.DemoClient()
    played = {client.live_url(c["stream_id"], "ts") for c in client.live_streams(None)}
    played |= {client.vod_url(f["stream_id"], "m3u8") for f in client.vod_streams(None)}
    assert played == set(demo_streams.STREAMS.values())
    app = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                    for p in (ROOT / "dopeiptv").rglob("*.py"))
    assert "bitdash" not in app


def test_the_table_is_read_without_the_app():
    """The tool reads the table by its path: standard library only."""
    src = (ROOT / "dopeiptv" / "providers" / "demo_streams.py").read_text(encoding="utf-8")
    imports = [ln for ln in src.splitlines() if ln.startswith(("import ", "from "))]
    assert imports == ["from __future__ import annotations", "from urllib.parse import urlsplit"]
    assert _tool().table().STREAMS == demo_streams.STREAMS


@pytest.fixture
def tls(tmp_path):
    from tests.test_net_advanced import _make_cert
    return _make_cert(tmp_path, "localhost", "demo")


@pytest.fixture
def tool(monkeypatch, tls):
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    mod = _tool()
    mod.OPENER = mod._opener(cafile=tls[0])       # the test's certificate, still checked
    return mod


def _routes(master_body: str | None = None) -> dict:
    master = master_body if master_body is not None else (
        "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000\nlow/index.m3u8\n")
    media = "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\nseg0.ts\n#EXTINF:4.0,\nseg1.ts\n"
    return {"/master.m3u8": (200, {}, master), "/low/index.m3u8": (200, {}, media),
            "/low/seg0.ts": (200, {}, b"\x47" * 188 * 20)}


def test_a_stream_that_plays_is_ok(tool, tls):
    from tests.test_welcome_paths import Served
    s = Served(_routes(), tls=tls)
    base = f"https://127.0.0.1:{s.server_address[1]}"
    try:
        assert tool.check(base + "/master.m3u8") == ""
        assert tool.check(base + "/low/index.m3u8") == ""          # a media playlist alone
        assert [p for p, _q in s.seen] == ["/master.m3u8", "/low/index.m3u8", "/low/seg0.ts",
                                            "/low/index.m3u8", "/low/seg0.ts"]
    finally:
        s.shutdown()


def test_what_does_not_play_is_named(tool, tls):
    from tests.test_welcome_paths import Served
    routes = _routes()
    routes["/page"] = (200, {"Content-Type": "text/html"}, "<html>Moved</html>")
    routes["/empty-master.m3u8"] = (200, {}, "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n")
    routes["/no-segment.m3u8"] = (200, {}, "#EXTM3U\n#EXT-X-ENDLIST\n")
    routes["/to-http"] = (302, {"Location": "http://127.0.0.1:9/master.m3u8"}, "")
    routes["/huge.m3u8"] = (200, {}, "#EXTM3U\n" + "#" * (tool.PLAYLIST_MAX + 10))
    s = Served(routes, tls=tls)
    base = f"https://127.0.0.1:{s.server_address[1]}"
    try:
        assert tool.check(base + "/gone.m3u8") == "HTTP 404"
        assert tool.check(base + "/page") == "no HLS playlist"
        assert tool.check(base + "/empty-master.m3u8") == "a master playlist with no variant"
        assert tool.check(base + "/no-segment.m3u8") == "a playlist with no segment"
        assert "redirect to http scheme refused" in tool.check(base + "/to-http")
        assert "no playlist" in tool.check(base + "/huge.m3u8")
        assert "not https" in tool.check(f"http://127.0.0.1:{s.server_address[1]}/master.m3u8")
    finally:
        s.shutdown()


def test_a_certificate_that_does_not_check_out_fails(monkeypatch, tls, tmp_path):
    """The system's authorities do not know the test's certificate: a
    failure, never a stream let through unchecked."""
    from tests.test_welcome_paths import Served
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    from tests.test_net_advanced import _make_cert
    other = _make_cert(tmp_path, "localhost", "other")
    mod = _tool()
    mod.OPENER = mod._opener(cafile=other[0])
    s = Served(_routes(), tls=tls)
    try:
        why = mod.check(f"https://127.0.0.1:{s.server_address[1]}/master.m3u8")
        assert "certificate" in why.lower(), why
    finally:
        s.shutdown()


def test_the_tool_says_each_stream_and_fails_on_any(tool, tls, capsys):
    from tests.test_welcome_paths import Served
    s = Served(_routes(), tls=tls)
    base = f"https://127.0.0.1:{s.server_address[1]}"
    try:
        tool.table = lambda: type("T", (), {"STREAMS": {"good": base + "/master.m3u8",
                                                        "bad": base + "/gone.m3u8"}})
        assert tool.main() == 1
        out = capsys.readouterr().out
        assert "ok   good" in out and "FAIL bad" in out and "HTTP 404" in out
        assert "1 of 2 streams answer." in out
        tool.table = lambda: type("T", (), {"STREAMS": {"good": base + "/master.m3u8"}})
        assert tool.main() == 0
    finally:
        s.shutdown()


def test_the_tool_runs_on_its_own():
    """It starts with nothing of the app imported (a syntax or import slip
    would show here, not on the day of a release)."""
    out = subprocess.run([sys.executable, "-I", "-c",
                          "import importlib.util, sys; "
                          f"spec = importlib.util.spec_from_file_location('c', {str(ROOT / 'tools' / 'check_demo.py')!r}); "
                          "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
                          "print(len(m.table().STREAMS), 'dopeiptv' in sys.modules)"],
                         capture_output=True, text=True, timeout=60)
    assert out.stdout.split() == [str(len(demo_streams.STREAMS)), "False"], out.stderr
