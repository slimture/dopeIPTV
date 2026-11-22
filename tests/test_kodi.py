"""Casting to Kodi over its JSON-RPC API: what it is told, how its state is
followed, its login, and the hub that puts it beside the Chromecasts and
TVs. A stand-in Kodi - an HTTP server answering JSON-RPC - plays the part."""
import base64
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dopeiptv.i18n import LocalizedError
from dopeiptv.providers import kodi, mdns


class FakeSettings:
    def __init__(self):
        self.d = {}

    def value(self, key, default=None):
        return self.d.get(key, default)

    def setValue(self, key, value):  # noqa: N802
        self.d[key] = value

    def remove(self, key):
        self.d.pop(key, None)


class FakeKodi:
    """Kodi's web server: remembers every call, and plays along."""

    def __init__(self, login=None, old_seek=False):
        self.calls = []
        #: Every request that reached it, answered or refused.
        self.hits = 0
        self.login = login
        self.old_seek = old_seek
        self.playing = None          # the file being played
        self.speed = 0
        self.pos = 0.0
        self.volume, self.muted = 40, False
        self.start_playing = True
        self.ignore_open = False
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                fake.hits += 1
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if fake.login is not None:
                    want = "Basic " + base64.b64encode(
                        ":".join(fake.login).encode()).decode()
                    if self.headers.get("Authorization") != want:
                        self.send_response(401)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                fake.calls.append((body["method"], body.get("params")))
                reply = {"jsonrpc": "2.0", "id": body.get("id")}
                try:
                    reply["result"] = fake.answer(body["method"], body.get("params") or {})
                except _Err as e:
                    reply["error"] = {"code": e.code, "message": "Invalid params."}
                data = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def answer(self, method, p):
        if method == "JSONRPC.Ping":
            return "pong"
        if method == "XBMC.GetInfoLabels":
            return {"System.FriendlyName": "Living room"}
        if method == "Player.Open":
            if self.ignore_open:
                return "OK"
            self.playing = p["item"]["file"]
            self.speed = 1 if self.start_playing else 0
            self.pos = 0.0
            return "OK"
        if method == "Player.GetActivePlayers":
            return [{"playerid": 1, "type": "video"}] if self.playing else []
        if method == "Player.GetProperties":
            return {"speed": self.speed, "time": kodi.time_obj(self.pos),
                    "totaltime": kodi.time_obj(5400)}
        if method == "Player.GetItem":
            return {"item": {"file": self.playing or "", "label": "x"}}
        if method == "Player.PlayPause":
            self.speed = 1 if p["play"] else 0
            return {"speed": self.speed}
        if method == "Player.Seek":
            value = p["value"]
            if self.old_seek and "time" in value:
                raise _Err(-32602)
            self.pos = kodi.seconds_of(value.get("time", value))
            return {"time": kodi.time_obj(self.pos)}
        if method == "Player.Stop":
            self.playing = None
            return "OK"
        if method == "Application.GetProperties":
            return {"volume": self.volume, "muted": self.muted}
        if method == "Application.SetVolume":
            self.volume = p["volume"]
            return self.volume
        if method == "Application.SetMute":
            self.muted = p["mute"]
            return self.muted
        raise _Err(-32601)

    def methods(self):
        return [m for m, _p in self.calls]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class _Err(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(kodi, "POLL_S", 0.05)
    monkeypatch.setattr(kodi, "START_WAIT_S", 2.0)
    monkeypatch.setattr(mdns, "browse", lambda *a, **k: [])


class Grab(logging.Handler):
    """Every line the app's logger writes, whatever its handlers."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())

    def __enter__(self):
        from dopeiptv.core.log import log
        self._was = log.level
        log.setLevel(logging.DEBUG)
        log.addHandler(self)
        return self

    def __exit__(self, *exc):
        from dopeiptv.core.log import log
        log.removeHandler(self)
        log.setLevel(self._was)

    @property
    def text(self):
        return "\n".join(self.lines)


def _wait(cond, secs=3.0):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def test_what_goes_over_the_wire():
    assert kodi.parse_address("192.168.1.20") == ("192.168.1.20", 8080)
    assert kodi.parse_address(" 192.168.1.20:9090 ") == ("192.168.1.20", 9090)
    assert kodi.parse_address("http://kodi.local:8081/jsonrpc") == ("kodi.local", 8081)
    assert kodi.parse_address("[fe80::1]:8080") == ("fe80::1", 8080)
    for bad in ("", "user:pw@host", "ftp://x", "a b", "host:99999", "http://"):
        assert kodi.parse_address(bad) is None, bad
    assert kodi.time_obj(3723.5) == {"hours": 1, "minutes": 2, "seconds": 3,
                                     "milliseconds": 500}
    assert kodi.seconds_of(kodi.time_obj(5025.25)) == pytest.approx(5025.25)
    assert kodi.seconds_of("nonsense") == 0.0
    assert kodi.rpc_request("Player.Stop", {"playerid": 1}, 7) == {
        "jsonrpc": "2.0", "method": "Player.Stop", "id": 7, "params": {"playerid": 1}}
    # The source's user agent and headers, as Kodi's own options.
    url = kodi.with_options("http://p.example/live/u/p/1.ts", "VLC/3.0 (x)",
                            {"Referer": "http://p.example/a b", "User-Agent": "no"})
    assert url == ("http://p.example/live/u/p/1.ts|User-Agent=VLC%2F3.0%20%28x%29"
                   "&Referer=http%3A%2F%2Fp.example%2Fa%20b")
    assert kodi.with_options("http://x/1.ts") == "http://x/1.ts"
    assert kodi.with_options("/home/me/film.mkv", "UA") == "/home/me/film.mkv"
    assert kodi.with_options("http://x/1.ts|User-Agent=A", "B") == "http://x/1.ts|User-Agent=A"
    # Only this computer can open these: never handed to a Kodi.
    for url in ("http://127.0.0.1:41000/dvr/tok/live.ts", "http://localhost/x",
                "http://[::1]:9/x", "http://a.localhost/x"):
        assert kodi.is_loopback_url(url), url
    assert not kodi.is_loopback_url("http://192.168.1.5:8000/x")


def test_remembered_devices_keep_their_login_and_never_show_it():
    s = FakeSettings()
    dev = kodi.KodiDevice("Bedroom", "10.0.0.9", 8081, "kodi", "s3cret", manual=True)
    kodi.save_devices(s, [dev])
    back = kodi.load_devices(s)
    assert [(d.name, d.host, d.port, d.user, d.password) for d in back] == [
        ("Bedroom", "10.0.0.9", 8081, "kodi", "s3cret")]
    assert "s3cret" not in repr(back[0]) and back[0].login == ("kodi", "s3cret")
    assert kodi.KodiDevice("a", "h", password="pw").login == ("kodi", "pw")
    assert kodi.KodiDevice("a", "h").login is None
    s.setValue(kodi.SETTINGS_KEY, "not json")
    assert kodi.load_devices(s) == []


def test_a_kodi_added_by_hand_is_checked_and_remembered(fast, monkeypatch):
    k = FakeKodi(login=("kodi", "s3cret"))
    try:
        s = FakeSettings()
        m = kodi.KodiManager(s)
        with pytest.raises(LocalizedError) as e:
            m.add("127.0.0.1", k.port)
        assert e.value.key == "kodi_needs_login"
        # Its login over plain http, the person not asked: nothing sent.
        hits = k.hits
        with pytest.raises(LocalizedError) as e:
            m.add("127.0.0.1", k.port, "kodi", "s3cret")
        assert e.value.key == "cast_err_kodi_plain" and k.hits == hits
        assert kodi.load_devices(s) == []
        with Grab() as grab:
            label = m.add("127.0.0.1", k.port, "kodi", "s3cret", plain_ok=True)
            assert label == "Living room (Kodi)"
            other = kodi.KodiManager(s)
            other.cast(label, "http://p.example/1.ts", "x")
            other.stop()
        assert grab.lines and "s3cret" not in grab.text
        assert [d.password for d in kodi.load_devices(s)] == ["s3cret"]
        # Found again by mDNS at the same address: once, with its login.
        found = mdns.Instance(name="Kodi", host="127.0.0.1", port=k.port)
        monkeypatch.setattr(mdns, "browse", lambda *a, **kw: [
            found, mdns.Instance("Other", "10.9.9.9", 8080)])
        names = m.scan()
        assert names == ["Living room (Kodi)", "Other (Kodi)"]
        assert m.devices["Living room (Kodi)"].password == "s3cret"
        m.forget("Living room (Kodi)")
        assert kodi.load_devices(s) == [] and "Living room (Kodi)" not in m.devices
    finally:
        k.close()
    with pytest.raises(LocalizedError) as e:
        kodi.KodiManager(FakeSettings()).add("127.0.0.1", 1)
    assert e.value.key == "kodi_no_answer"


def test_a_cast_tells_kodi_and_follows_it(fast, monkeypatch):
    k = FakeKodi(old_seek=True)
    try:
        s = FakeSettings()
        kodi.save_devices(s, [kodi.KodiDevice("TV", "127.0.0.1", k.port)])
        m = kodi.KodiManager(s)
        monkeypatch.setattr(m, "_stream_options",
                            lambda: ("Agent/1", {"X-Token": "abc"}))
        # The remembered name works before any scan has run.
        assert m.knows("TV (Kodi)")
        name = m.cast("TV (Kodi)", "http://p.example/h.m3u8", "A film",
                      source="http://p.example/movie/u/p/7.mkv", duration=5400,
                      start=600)
        assert name == "TV (Kodi)"
        # The player's own address, with the source's agent and headers.
        assert k.playing == ("http://p.example/movie/u/p/7.mkv"
                             "|User-Agent=Agent%2F1&X-Token=abc")
        assert m.state == "PLAYING/None" and m.duration == 5400
        # Resumed where the film was: the old seek after the new was refused.
        assert m.position() >= 600 and k.pos == 600
        assert _wait(lambda: m.volume() == (0.4, False))
        m.pause()
        assert k.speed == 0 and _wait(lambda: m.state == "PAUSED/None")
        m.resume()
        assert k.speed == 1 and _wait(lambda: m.state == "PLAYING/None")
        m.set_volume(0.7)
        m.set_muted(True)
        assert (k.volume, k.muted) == (70, True) and m.volume() == (0.7, True)
        k.pos = 1234.0
        assert _wait(lambda: 1234 <= m.position() < 1240)
        # Stopped with Kodi's own remote: the cast is over here, and Kodi is
        # not told to stop again.
        k.playing = None
        assert _wait(lambda: m.gone and m.active is None)
        assert m.state == "IDLE/FINISHED"
        n_stops = k.methods().count("Player.Stop")
        m.stop()
        assert k.methods().count("Player.Stop") == n_stops
    finally:
        k.close()


def test_another_item_on_kodi_ends_the_cast_here(fast):
    k = FakeKodi()
    try:
        s = FakeSettings()
        kodi.save_devices(s, [kodi.KodiDevice("TV", "127.0.0.1", k.port)])
        m = kodi.KodiManager(s)
        m.cast("TV (Kodi)", "http://p.example/live/u/p/1.ts", "News")
        assert _wait(lambda: m._file != "")
        k.playing = "smb://nas/holiday.mkv"
        assert _wait(lambda: m.gone)
        assert k.playing == "smb://nas/holiday.mkv"      # left alone
        # The next cast starts afresh.
        m.cast("TV (Kodi)", "http://p.example/live/u/p/2.ts", "Sport")
        assert not m.gone and m.active is not None
        m.stop()
        assert k.playing is None and m.active is None
    finally:
        k.close()


def test_a_stretch_of_the_archive_running_out_is_an_ending_not_a_stop(fast):
    """The archive is cast a stretch at a time: when one runs out the window
    asks for the next (it watches for IDLE/FINISHED), so the cast stays."""
    k = FakeKodi()
    try:
        s = FakeSettings()
        kodi.save_devices(s, [kodi.KodiDevice("TV", "127.0.0.1", k.port)])
        m = kodi.KodiManager(s)
        m.cast("TV (Kodi)", "http://p.example/timeshift/u/p/240/2026-11-15:20-00/1.ts",
               "News", start=12, dvr=True)
        assert m.duration == 0 and "Player.Seek" not in k.methods()
        k.playing = None
        assert _wait(lambda: m.state == "IDLE/FINISHED")
        assert not m.gone and m.active is not None
        m.stop()
    finally:
        k.close()


def test_two_kodis_of_one_name_and_one_that_goes_away(fast, monkeypatch):
    monkeypatch.setattr(kodi, "UNREACHABLE_AFTER", 3)
    k1, k2 = FakeKodi(), FakeKodi()      # both call themselves "Living room"
    try:
        m = kodi.KodiManager(FakeSettings())
        a = m.add("127.0.0.1", k1.port)
        b = m.add("127.0.0.1", k2.port)
        assert (a, b) == ("Living room (Kodi)", "Living room #2 (Kodi)")
        assert m.devices[a].port == k1.port and m.devices[b].port == k2.port
        m.cast(b, "http://p.example/1.ts", "x")
        k2.close()                       # switched off
        assert _wait(lambda: m.gone and m.active is None, 5)
    finally:
        k1.close()


def test_what_kodi_cannot_have_is_refused(fast):
    k = FakeKodi(login=("kodi", "pw"))
    try:
        s = FakeSettings()
        kodi.save_devices(s, [kodi.KodiDevice("TV", "127.0.0.1", k.port)])
        m = kodi.KodiManager(s)
        with pytest.raises(LocalizedError) as e:
            m.cast("TV (Kodi)", "http://127.0.0.1:43111/dvr/tok/live.ts", "x")
        assert e.value.key == "cast_err_kodi_local" and k.calls == []
        with pytest.raises(LocalizedError) as e:
            m.cast("TV (Kodi)", "http://p.example/1.ts", "x")
        assert e.value.key == "cast_err_kodi_login" and m.active is None
        k.login = None
        # A player that came up but is still filling its buffer when the time
        # is up is given the benefit of the doubt; none at all is a refusal.
        k.start_playing = False
        m.cast("TV (Kodi)", "http://p.example/1.ts", "x")
        assert m.active is not None and m.state == "BUFFERING/None"
        m.stop()
        k.ignore_open = True
        with pytest.raises(LocalizedError) as e:
            m.cast("TV (Kodi)", "http://p.example/2.ts", "x")
        assert e.value.key == "cast_err_kodi_no_start" and m.active is None
        with pytest.raises(LocalizedError) as e:
            m.cast("Nowhere (Kodi)", "http://p.example/1.ts", "x")
        assert e.value.key == "cast_err_not_found"
    finally:
        k.close()


def test_a_local_file_is_served_to_kodi(fast, tmp_path):
    k = FakeKodi()
    try:
        s = FakeSettings()
        kodi.save_devices(s, [kodi.KodiDevice("TV", "127.0.0.1", k.port)])
        m = kodi.KodiManager(s)
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"\x00" * 1000)
        m.cast("TV (Kodi)", str(f), "Clip")
        assert k.playing.startswith("http://") and k.playing.endswith("/clip.mp4")
        assert "|" not in k.playing
        m.stop()
        assert m.files._server is None
    finally:
        k.close()


class _FakeMgr:
    def __init__(self, names, suffix=""):
        self.names, self.suffix = names, suffix
        self.active = None
        self.calls = []
        self.gone = False

    def knows(self, name):
        return bool(self.suffix) and name.endswith(self.suffix)

    def scan(self):
        return list(self.names)

    def cast(self, name, *a, **kw):
        self.calls.append(("cast", name))
        self.active = name
        return name

    def stop(self):
        self.calls.append(("stop",))
        self.active = None

    def shutdown(self):
        self.calls.append(("shutdown",))


def test_the_hub_puts_kodi_beside_the_others(monkeypatch):
    pytest.importorskip("PyQt6")
    from dopeiptv.providers import casting
    monkeypatch.setattr(casting.ChromecastManager, "available",
                        staticmethod(lambda: True))
    cc = _FakeMgr(["Kitchen"])
    tv = _FakeMgr(["Hall (DLNA)"], " (DLNA)")
    kd = _FakeMgr(["Den (Kodi)"], " (Kodi)")
    hub = casting.CastHub(cc, tv, kd)
    assert hub.scan() == ["Kitchen", "Hall (DLNA)", "Den (Kodi)"]
    assert hub.is_kodi("Den (Kodi)") and not hub.is_dlna("Den (Kodi)")
    assert hub.plays_as_is("Den (Kodi)") and hub.plays_as_is("Hall (DLNA)")
    assert not hub.plays_as_is("Kitchen")
    hub.cast("Kitchen", "http://a", "A")
    hub.cast("Den (Kodi)", "http://a", "A")
    assert ("stop",) in cc.calls and hub.active == "Den (Kodi)"
    assert not hub.ended_elsewhere()
    kd.gone = True
    assert hub.ended_elsewhere()
    hub.cast("Hall (DLNA)", "http://a", "A")
    assert ("stop",) in kd.calls and not hub.ended_elsewhere()
    order = []
    for name, mgr in (("cc", cc), ("tv", tv), ("kodi", kd)):
        mgr.shutdown = (lambda n=name: order.append(n))
    kd.active = "Den (Kodi)"
    tv.active = None
    hub.shutdown()
    assert order[0] == "kodi" and sorted(order) == ["cc", "kodi", "tv"]


def test_a_kodi_typed_as_https_is_asked_over_https_never_http():
    """An address typed as https:// stays so - its login never goes out in
    the clear over http instead; kept so in the settings too."""
    assert kodi.parse_target("https://kodi.lan:8443/jsonrpc") == ("kodi.lan", 8443, True)
    assert kodi.parse_target("kodi.lan:8080") == ("kodi.lan", 8080, False)
    assert kodi.parse_address("https://kodi.lan:8443") == ("kodi.lan", 8443)
    dev = kodi.KodiDevice("Den", "kodi.lan", 8443, "kodi", "pw", manual=True, secure=True)
    assert dev.endpoint == "https://kodi.lan:8443/jsonrpc"
    assert kodi.KodiDevice("Den", "kodi.lan", 8080).endpoint == "http://kodi.lan:8080/jsonrpc"

    class S(dict):
        def value(self, k, d=None):
            return self.get(k, d)

        def setValue(self, k, v):
            self[k] = v
    s = S()
    kodi.save_devices(s, [dev, kodi.KodiDevice("Plain", "10.0.0.2", 8080)])
    back = kodi.load_devices(s)
    assert [d.secure for d in back] == [True, False]
    assert back[0].endpoint.startswith("https://")


# -- a login over plain http: asked once per Kodi -------------------------------------

def test_a_login_over_plain_http_is_sent_only_once_the_person_said_so(fast):
    """A Kodi's login over http is the password in the clear: nothing goes to
    it - not the cast, not a poll - until Send anyway, kept with that Kodi
    (never asked again, here or in another run). A Kodi without a login, or
    one asked over https, is never asked."""
    k = FakeKodi(login=("kodi", "s3cret"))
    try:
        s = FakeSettings()
        kodi.save_devices(s, [kodi.KodiDevice("Den", "127.0.0.1", k.port, "kodi", "s3cret",
                                              manual=True)])
        m = kodi.KodiManager(s)
        assert m.asks_first("Den (Kodi)")
        with pytest.raises(LocalizedError) as e:
            m.cast("Den (Kodi)", "http://p.example/1.ts", "x")
        assert e.value.key == "cast_err_kodi_plain" and k.hits == 0
        with pytest.raises(kodi.KodiPlainLogin):
            kodi.KodiRpc(m.device("Den (Kodi)")).call("JSONRPC.Ping")
        assert k.hits == 0
        with Grab() as grab:
            m.allow_plain("Den (Kodi)")
        assert "s3cret" not in grab.text
        assert not m.asks_first("Den (Kodi)")
        assert [d.plain_ok for d in kodi.load_devices(s)] == [True]
        assert '"plain_login": true' in s.value(kodi.SETTINGS_KEY)
        # Another run reads it back: not asked again.
        again = kodi.KodiManager(s)
        assert not again.asks_first("Den (Kodi)")
        again.cast("Den (Kodi)", "http://p.example/1.ts", "x")
        assert k.playing.startswith("http://p.example/1.ts") and k.hits > 0
        again.stop()
    finally:
        k.close()
    # No login, or https: nothing to ask.
    assert not kodi.KodiDevice("A", "h").asks_first
    assert not kodi.KodiDevice("A", "h", user="kodi", password="pw", secure=True).asks_first
    assert kodi.KodiDevice("A", "h", password="pw").asks_first


def test_use_https_tries_it_first_and_keeps_it_only_once_it_answers(fast, monkeypatch):
    s = FakeSettings()
    kodi.save_devices(s, [kodi.KodiDevice("Den", "10.0.0.5", 8080, "kodi", "pw", manual=True)])
    m = kodi.KodiManager(s)
    tried = []

    def describe(dev, session=None):
        tried.append(dev.endpoint)
        if fail:
            raise kodi.requests.ConnectionError("no https here")
        return "Den"
    monkeypatch.setattr(kodi, "describe", describe)
    fail = True
    with pytest.raises(LocalizedError) as e:
        m.use_https("Den (Kodi)")
    assert e.value.key == "kodi_https_failed"
    assert tried == ["https://10.0.0.5:8080/jsonrpc"]
    assert [d.secure for d in kodi.load_devices(s)] == [False] and m.asks_first("Den (Kodi)")
    fail = False
    assert m.use_https("Den (Kodi)") == "Den (Kodi)"
    assert [d.secure for d in kodi.load_devices(s)] == [True]
    assert m.device("Den (Kodi)").endpoint.startswith("https://")
    assert not m.asks_first("Den (Kodi)")
