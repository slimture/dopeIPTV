"""Fuzzing the small readers of what comes from outside (tests/fuzz_util.py):
numbers, dopeiptv:// links and command lines, stream addresses, the other
networks' and backup addresses' lines, rewrite rules, catch-up and file
name templates, a backup's file, a synced copy and a watch-together
message once opened, HDHomeRun's and MQTT's packets, the phone remote's
queries and lists. Each holds to what it says it raises - most, nothing."""
from __future__ import annotations

import dataclasses
import json
import math
import random
import socket
import struct
import zlib
from datetime import datetime, timezone

import pytest

from dopeiptv.core import (backup, catchup, commands, hdhr, home_net, mirrors, mqtt, nerd, num,
                           phone_browse, phone_remote, rec_extras, stream_address, sync, together,
                           url_rewrite)
from dopeiptv.providers import hdhomerun, mdns
from tests import fuzz_util as fz

ADDRESSES = ("http://example.com/live/u/p/1.ts", "https://cdn.example/a.m3u8?token=x", "rtsp://10.0.0.5:554/x",
             "udp://@239.1.1.1:1234", "http://192.168.1.10:4022/udp/239.0.0.1:1234", "file:///tmp/a.ts",
             "C:\\Videos\\a.mkv", "/home/u/a.ts", "http://127.0.0.1:6878/ace/getstream?id=x", "ytdl://abc",
             "http://[::ffff:127.0.0.1]/", "http://0x7f000001/", "lavfi:anullsrc", "-i x", "edl://a;b",
             "http://u:p@host.example:8080/x?a=1#f", "srt://host:9000?passphrase=x", "smb://nas/share/a.mkv")
TEXT_TOKENS = ("://", "/", "?", "&", "=", "#", "@", ":", "[", "]", "%", "%00", "\\", "..", "127.0.0.1", "0x",
               "localhost", "169.254.169.254", "::1", "file:", "-", ";", " ", "\n")


def text_from(rng: random.Random, seeds, tokens=TEXT_TOKENS) -> str:
    return fz.mutate_text(rng, rng.choice(seeds), tokens=tokens)


# -- numbers -------------------------------------------------------------------------

def test_fuzz_numbers():
    def make(rng: random.Random) -> object:
        return fz.json_value(rng, rng.choice(([1], ["2.5"], [None]))) if rng.random() < 0.6 else \
            rng.choice(fz.ODD_TEXT + (b"12", 1j, float("nan"), 10 ** 400, -10 ** 300, object()))

    def target(v: object) -> tuple:
        a = num.number(v)
        b = num.number(v, 0.0, lo=-5.0, hi=5.0)
        c = num.whole(v)
        d = num.whole(v, 0, lo=0, hi=100)
        e = num.digits(v)
        assert a is None or math.isfinite(a)
        assert -5.0 <= b <= 5.0 and 0 <= d <= 100
        assert c is None or isinstance(c, int)
        assert e is None or (isinstance(e, int) and e >= 0)
        return a, b, c, d, e
    fz.fuzz("num", target, make)


# -- links and command lines ---------------------------------------------------------------

LINKS = ("dopeiptv://play?url=http%3A%2F%2Fx%2Fa.ts", "dopeiptv://channel?name=SVT1", "dopeiptv://tv",
         "dopeiptv:play?url=file:///etc/passwd", "dopeiptv://play/?url=" + "a" * 3000, "dopeiptv://",
         "DOPEIPTV://CHANNEL?NAME=x&name=y")


def test_fuzz_links_and_commands():
    def link(text: str) -> object:
        got = commands.parse_link(text)
        assert got is None or isinstance(got, dict)
        commands.checked(got)
        commands.is_link(text)
        commands.shown(text)
        commands.link_address_ok(text)
        commands.file_url_path(text)
        commands.file_url_path(text, windows=True)
        commands.host_of(text)
        return got
    fz.fuzz("dopeiptv-link", link, lambda rng: text_from(rng, LINKS + ADDRESSES), seeds=LINKS)

    def argv(args: list) -> object:
        got = commands.parse_command(args)
        commands.own_arguments(["dopeiptv", *args])
        assert got is None or isinstance(got, dict)
        return commands.checked(got)
    fz.fuzz("command-line", argv, lambda rng: [fz.decoded(text_from(rng, ("--play", "--channel", "--tv", "SVT1",
                                                                          *LINKS, *ADDRESSES)))
                                               for _ in range(rng.randint(0, 4))])

    def cmd(value: object) -> object:
        got = commands.checked(value)
        assert got is None or isinstance(got, dict)
        return got
    fz.fuzz("checked-command", cmd, lambda rng: fz.json_value(rng, rng.choice((
        {"play": "http://x/a.ts", "title": "x"}, {"channel": "SVT1", "link": True}, {"tv": True}))))


# -- addresses ------------------------------------------------------------------------

def test_fuzz_stream_addresses():
    def target(arg: tuple) -> object:
        address, remote, internet, home = arg
        got = stream_address.refusal(address, remote, internet, home)
        assert isinstance(got, str)
        stream_address.played(address, remote, "fuzz", internet, home)
        stream_address.host_refusal(address, home)
        stream_address.picture_refusal(address, home)
        stream_address.on_internet(address)
        stream_address.udpxy(address)
        stream_address.acestream(address)
        stream_address.empty_host(address)
        stream_address.scheme_of(address)
        return got
    fz.fuzz("stream-address", target, lambda rng: (fz.decoded(text_from(rng, ADDRESSES)), rng.random() < 0.7,
                                                   rng.random() < 0.5, rng.random() < 0.3), seeds=[
        (a, True, True, False) for a in ADDRESSES])
    for odd in (None, 5, b"http://x", ["a"], {"a": 1}):
        assert stream_address.played(odd) == ""


NETWORKS = ("192.168.2.0/24\n10.0.5.10-10.0.5.40", "172.16.0.0/22, 192.168.3.7", "fd00::/120",
            "192.168.1.0/16", "8.8.8.8", "not a network", "10.0.0.1-10.0.0.0")


def test_fuzz_home_networks():
    def target(value: object) -> object:
        home_net.read(value)
        home_net.check_list(value)
        got = home_net.addresses(value)
        assert len(got) == len(set(got)) <= home_net.MAX_TOTAL
        return got
    fz.fuzz("other-networks", target, lambda rng: text_from(rng, NETWORKS, tokens=(
        "/", "-", ",", "\n", ".", ":", "0", "255", "/0", "/32", "::", "192.168.", "10."))
        if rng.random() < 0.9 else fz.json_value(rng, [NETWORKS[0]]), seeds=NETWORKS)

    nets = home_net.LocalNetworks(networks=lambda: [__import__("ipaddress").ip_network("192.168.1.0/24")])

    def client(address: str) -> bool:
        home_net.is_home_client(address)
        home_net.home_host(address)
        home_net.host_name(address)
        home_net.host_for_addresses(address)
        mdns.at_home(address)
        nerd.is_home_host(address)
        nerd.ipv4_any(address)
        return nets.allows(address)
    fz.fuzz("home-client", client, lambda rng: fz.decoded(text_from(rng, (
        "192.168.1.5", "10.0.0.1", "fe80::1%eth0", "::ffff:192.168.1.5", "8.8.8.8", "box.local", "[::1]:80",
        "tv.lan.", "a" * 300 + ".local"), tokens=(".", ":", "%", "[", "]", "0", "255", "local", "lan"))))


MIRRORS = ("http://a.example:8080\nhttps://b.example/\n# a comment\nc.example:80", "http://x.example/path?q=1",
           "ftp://x", "http://", "http://[::1]:99999")


def test_fuzz_backup_addresses():
    def target(arg: tuple) -> object:
        value, kind = arg
        got = mirrors.parse(value, kind)
        mirrors.problems(value, kind)
        mirrors.check_list(value, kind, "http://a.example:8080")
        mirrors.addresses("http://a.example:8080", {"backup_servers": value}, kind)
        for a in got[:5]:
            mirrors.shown(a)
            mirrors.tail(a + "/live/1.ts", a)
            mirrors.same(a, "http://a.example:8080")
        if got:
            p = mirrors.Picker(got, got[-1])
            p.order()
            p.worked(got[0])
        return got
    fz.fuzz("mirrors", target, lambda rng: (text_from(rng, MIRRORS) if rng.random() < 0.9
                                            else fz.json_value(rng, [MIRRORS[0]]),
                                            rng.choice(("xtream", "m3u", "stalker", "x"))),
            seeds=[(m, "xtream") for m in MIRRORS])


# -- rules and templates --------------------------------------------------------------

RULES = [{"find": "http://old.example", "replace": "https://new.example"},
         {"find": r"/live/(\w+)/(\w+)/", "replace": r"/live/\2/\1/", "regex": True},
         {"find": "(a+)+$", "replace": "x", "regex": True}, {"find": "(?i)TOKEN=[^&]*", "replace": "", "regex": True}]
PIECES = ("(a+)+", "(a|aa)*", ".*.*.*", "(?:x*)*y", r"(\w+\s?)*$", "[^/]*", r"(.)\1", "(?=a)", "a{1,1000}",
          "(?<!x)", ".+?", "^", "$", "|", "\\", "(", ")", "[", "]", "{", "}", "*", "+", "?", "(?P<n>a)", "(?P=n)")


def test_fuzz_rewrite_rules():
    """Rules of any shape - a backup's, another computer's - and addresses
    of any length: a rule is refused, or runs quickly; never a hang."""
    def make(rng: random.Random) -> tuple:
        rules = fz.json_value(rng, RULES)
        if rng.random() < 0.5 and isinstance(rules, list):
            find = "".join(rng.choice(PIECES) for _ in range(rng.randint(1, 6)))
            rules.append({"find": find, "replace": rng.choice(("", "x", r"\1", r"\g<0>", "\\")), "regex": True})
        raw = fz.to_json(rules) if rng.random() < 0.7 else rules
        url = rng.choice(ADDRESSES) + rng.choice(("", "a" * rng.choice((10, 1000, 20000)), "/" * 5000,
                                                  "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaab"))
        return raw, url

    def target(arg: tuple) -> str:
        raw, url = arg
        rules = url_rewrite.parse(raw)
        url_rewrite.problems(rules)
        out = url_rewrite.apply(url, rules)
        assert isinstance(out, str) and len(out) <= max(len(url), url_rewrite.MAX_ADDRESS)
        url_rewrite.rewrite(url, {"options": {"url_rewrite": raw}})
        url_rewrite.shown(out, ["secret"])
        return out
    fz.fuzz("rewrite-rules", target, make, seeds=[(json.dumps(RULES), ADDRESSES[0])], hang=5.0)


TEMPLATES = ("${start}-${end}", "{utc}/{duration}/{Y}{m}{d}{H}{M}{S}", "?utc={utc}&lutc={lutc}",
             "{catchup-id}", "${timestamp:%Y}", "{utc:YmdHMS}", "{offset:60}", "{bogus}", "{", "}", "${")
STARTED = datetime(2026, 5, 28, 10, 0, tzinfo=timezone.utc)


def test_fuzz_catchup_templates():
    def target(arg: tuple) -> object:
        template, live, mode = arg
        catchup.unknown_placeholders(template)
        out = catchup.render(template, STARTED, STARTED.replace(hour=11), STARTED.replace(hour=12), "id7")
        assert isinstance(out, str)
        catchup.template_for(live, template, mode)
        catchup.archive_url(live, STARTED, STARTED.replace(hour=11), template, mode, STARTED, "id7")
        catchup.whole_address(template, mode)
        catchup.shift_template(live)
        catchup.flussonic_template(live)
        catchup.check_template(template, mode, live, "SVT1", now=STARTED)
        return out
    fz.fuzz("catchup", target, lambda rng: (fz.decoded(text_from(rng, TEMPLATES, tokens=(
        "{", "}", "$", ":", "%", "utc", "start", "Y", "offset", "duration"))), fz.decoded(text_from(rng, ADDRESSES)),
        rng.choice(("auto", "default", "append", "shift", "flussonic", "x", ""))))

    def stream(arg: tuple) -> object:
        return catchup.is_stream(*arg)
    fz.fuzz("catchup-stream", stream, lambda rng: (rng.choice(fz.ODD_TEXT + ("video/mp2t", "application/x-mpegurl")),
                                                   fz.mutate_bytes(rng, b"G\x40\x00\x10#EXTM3U\n<html>")))


NAMES = ("{title}", "{channel}/{date} {time} - {title}", "{year}/{month}/{day}/{title}", "../../{title}",
         "{bogus}", "CON", "a/b/c/d/e/f/g/{title}", "{title}" * 50)


def test_fuzz_recording_names():
    def target(arg: tuple) -> str:
        template, title, channel = arg
        out = rec_extras.render_name(template, title, channel, STARTED)
        parts = out.replace("\\", "/").split("/")
        assert out and not out.startswith(("/", "\\")) and ".." not in parts
        assert all(len(p.encode("utf-8")) <= rec_extras.PART_BYTES + 1 for p in parts)
        rec_extras.unknown_fields(template)
        rec_extras.check_template(template)
        rec_extras.post_argv("echo {path} {title} {channel}", "/rec/" + out + ".ts", title, channel, windows=False)
        rec_extras.post_argv("C:\\x.bat {title}", "C:\\rec\\a.ts", title, channel, windows=True)
        return out
    fz.fuzz("recording-name", target, lambda rng: (
        fz.decoded(text_from(rng, NAMES, tokens=("{", "}", "/", "\\", "..", ":", "*", "?", '"', "<", "|", "\x00"))),
        fz.decoded(text_from(rng, ("Rapport", "a/b", "..", "CON", "nul.txt", " . "))),
        fz.decoded(text_from(rng, ("SVT1", "", "x" * 300)))))


CARRIED = {"id": "j1", "start": 1.7e9, "stop": 1.7e9 + 3600, "status": "recording",
           "carry": {"at": 1.7e9 + 60, "part": "News 2026-11-24 21.00.ts"},
           "history": [{"_kind": "live", "stream_id": 2, "_key": 2, "name": "Two"},
                       {"_kind": "movie", "_key": 9}]}


def test_fuzz_carried_recordings_and_the_last_channel():
    """What the jobs' list says of a recording carried on from a window
    (another process wrote it), and what History names last as the app
    starts (core/start_channel.py)."""
    from dopeiptv.core import start_channel
    from dopeiptv.core.recording import RecordingManager

    def target(v: object) -> tuple:
        j = v if isinstance(v, dict) else {"carry": v}
        at = RecordingManager.carried_from(j)
        assert at is None or (isinstance(at, float) and math.isfinite(at))
        rows = j.get("history")
        rows = rows if isinstance(rows, list) else None
        e = start_channel.last_live(rows)
        sid = start_channel.stream_of(e)
        assert isinstance(sid, str) and len(sid) <= 200
        found = start_channel.find(rows, e)
        assert found is None or isinstance(found, dict)
        return at, sid
    fz.fuzz("carried", target, lambda rng: fz.json_value(rng, CARRIED), seeds=(CARRIED, [], None))


# -- what is opened: a backup, a synced copy, a room's message ---------------------------

def test_fuzz_backup_files(tmp_path):
    path = str(tmp_path / "backup.json")
    doc = {backup.MAGIC: 1, "app_version": "1.6.12", "created": "2026-05-28",
           "stores": {"main": {"playlists": json.dumps([{"id": "a1b2c3d4", "name": "P", "kind": "m3u",
                                                          "server": "http://x/a.m3u"}]), "theme": "dark"}},
           "keep": ["theme"]}
    sealed = backup.lock(doc, "pw", rounds=backup.MIN_ROUNDS)

    def make(rng: random.Random) -> bytes:
        base = rng.choice((doc, sealed, [doc["stores"]]))
        if rng.random() < 0.8:
            return fz.json_body(rng, base)
        return fz.mutate_bytes(rng, json.dumps(base).encode())

    def target(data: bytes) -> object:
        with open(path, "wb") as f:
            f.write(data)
        got = backup.read_any(path)
        if isinstance(got, backup.Backup):
            backup.summary(got)
            backup.kept(got)
            backup.replaceable(got)
            backup.missing_paths(got, exists=lambda p: False)
        return got
    fz.fuzz("backup-file", target, make, allowed=(backup.BackupError,),
            seeds=[json.dumps(doc).encode(), json.dumps(sealed).encode()])


def _sync_blob(key: bytes, text: bytes, fmt: int | None = None) -> bytes:
    """What seal() makes, of any text: a synced copy another computer with
    the code could write."""
    import secrets as _s

    from dopeiptv.core import chacha20poly1305 as aead
    head = bytes([sync.FORMAT if fmt is None else fmt])
    nonce = _s.token_bytes(12)
    return head + nonce + aead.encrypt(key, nonce, zlib.compress(text), sync._AAD + head)


def test_fuzz_synced_copy():
    """A synced copy sealed with the right key, its text anything: a
    SyncError or a document - never another exception."""
    _slot, key = sync.derive(b"\x01" * 16)
    good = sync._canon(sync.empty_doc()).encode()

    def make(rng: random.Random) -> bytes:
        k = rng.random()
        if k < 0.6:
            return _sync_blob(key, fz.json_body(rng, json.loads(good)))
        if k < 0.8:
            return _sync_blob(key, fz.mutate_bytes(rng, good))
        return fz.mutate_bytes(rng, _sync_blob(key, good))

    def target(blob: bytes) -> object:
        doc = sync.unseal(key, blob)
        assert isinstance(doc, dict)
        sync.counter(doc)
        sync.saved_at(doc)
        return doc
    fz.fuzz("sync-unseal", target, make, allowed=(sync.SyncError,), seeds=[_sync_blob(key, good)])


def test_fuzz_together_messages():
    """A watch-together message as a room's members can send it - signed by
    its sender, every field anything: parse() says what it is or None, a
    room takes it or leaves it, never an exception."""
    code = together.new_code()
    room_id, key = together.derive(code)
    them, me = together.new_identity(), together.new_identity()
    t = [1000.0]
    clock = together.Clock(mono=lambda: t[0], wall=lambda: 1_780_000_000.0)
    sender = together.Room(code, them.id, "Anna", clock)
    media = together.Media("vod", "Dune", "dune", 2021, 438631)
    sent = [sender.hello(), sender.publish(media, True, 12.5), sender.keepalive(),
            sender.hello(answer=True, to=me.id), sender.bye()]

    def make(rng: random.Random) -> object:
        return [fz.json_value(rng, rng.choice(sent)) for _ in range(rng.randint(1, 4))]

    def target(msgs: object) -> list:
        room = together.Room(code, me.id, "Bo", clock)
        out = []
        for d in msgs if isinstance(msgs, list) else [msgs]:
            got = together.parse(d)
            if isinstance(d, dict):
                together.Media.from_wire(d.get("m"))
                together.PlayState.from_wire(d.get("st"))
                if isinstance(d.get("f"), str) and d["f"] == them.id:
                    try:
                        blob = together.seal(key, room_id, d, them)
                    except ValueError:
                        blob = b""          # longer than any frame
                    if blob:
                        assert together.open_frame(key, room_id, blob) is not None
                        together.older_frame(key, room_id, blob)
                        got = together.open_message(key, room_id, blob) or got
            if got is not None:
                t[0] += 1.0
                out += room.receive(dataclasses.replace(got, signed=True), t[0])
            room.expire(t[0] + 500)
            room.names()
        return out
    fz.fuzz("together-message", target, make, seeds=[sent])
    blob = together.seal(key, room_id, sent[1], them)
    fz.fuzz("together-frame", lambda b: together.open_message(key, room_id, b),
            lambda rng: fz.mutate_bytes(rng, blob), seeds=[blob])


# -- packets ------------------------------------------------------------------------

def _crc(body: bytes) -> bytes:
    return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)


def test_fuzz_hdhomerun_packets():
    asked = hdhomerun.discover_packet()
    answer = hdhr.reply("1053ABCD", 2, "http://192.168.1.5:5004")

    def make(rng: random.Random) -> bytes:
        body = fz.mutate_bytes(rng, rng.choice((asked, answer))[:-4])
        if rng.random() < 0.5 and 4 <= len(body) < 65536:
            body = body[:2] + struct.pack(">H", len(body) - 4) + body[4:]
        return _crc(body) if rng.random() < 0.9 else body

    def target(data: bytes) -> object:
        got = hdhr.tags(data)
        hdhr.wants(data, "1053ABCD")
        reply = hdhomerun.parse_reply(data, "192.168.1.5")
        assert reply is None or reply["server"].lower().startswith("http")
        return got, reply
    fz.fuzz("hdhr-packets", target, make, seeds=[asked, answer])


def test_fuzz_mdns_packets():
    q = mdns.query([(mdns.labels("_xbmc-jsonrpc-h._tcp.local"), mdns.T_PTR)])
    answer = bytes.fromhex("000084000000000300000000") + mdns.encode_name(mdns.labels("_x._tcp.local")) + \
        struct.pack("!HHIH", mdns.T_PTR, 1, 120, 2) + b"\xc0\x0c" + b"\xc0\x0c" + \
        struct.pack("!HHIH", mdns.T_TXT, 1, 120, 4) + b"\x03a=b" + b"\xc0\x0c" + \
        struct.pack("!HHIH", mdns.T_A, 1, 120, 4) + bytes([192, 168, 1, 9])

    def target(data: bytes) -> object:
        try:
            got = mdns.parse(data)
        except ValueError:
            got = []
        mdns.instances([(data, "192.168.1.9")], "_x._tcp.local")
        return got
    fz.fuzz("mdns", target, lambda rng: fz.mutate_bytes(rng, rng.choice((q, answer)), others=[q, answer]),
            seeds=[q, answer])


def test_fuzz_discovery_answers():
    """What answers a search on the network: Jellyfin's and Emby's JSON,
    Plex's GDM, an SSDP answer's locations, plex.tv's list of servers, a
    tune answer - each a device's or a server's word, never an exception."""
    from dopeiptv.providers import dlna, jellyfin, plex
    jf = json.dumps({"Address": "http://172.17.0.2:8096", "Id": "srv1", "Name": "Den"}).encode()
    gdm = b"HTTP/1.0 200 OK\r\nContent-Type: plex/media-server\r\nName: Den\r\nPort: 32400\r\nResource-Identifier: m1\r\n"
    ssdp = (b"HTTP/1.1 200 OK\r\nLOCATION: http://192.168.1.9:1400/desc.xml\r\nST: urn:schemas-upnp-org:device:"
            b"MediaRenderer:1\r\n\r\n")
    res = [{"name": "Den", "provides": "server", "clientIdentifier": "m1", "accessToken": "t", "owned": True,
            "connections": [{"uri": "http://192.168.1.5:32400", "local": True}, {"uri": "https://x.plex.direct",
                                                                                 "relay": True}]}]
    tune = {"MediaContainer": {"MediaSubscription": [{"MediaGrabOperation": [{"Metadata": {"key": "/livetv/sessions/a"}}]}]}}

    def make(rng: random.Random) -> tuple:
        k = rng.randrange(5)
        if k == 0:
            return k, fz.json_body(rng, json.loads(jf)) if rng.random() < 0.7 else fz.mutate_bytes(rng, jf)
        if k == 1:
            return k, fz.mutate_bytes(rng, gdm, tokens=("\r\n", ":", "Port: ", "Content-Type: plex/media-server"))
        if k == 2:
            return k, fz.mutate_bytes(rng, ssdp, tokens=("\r\n", "LOCATION: ", "http://", ":"))
        if k == 3:
            return k, fz.json_value(rng, res)
        return k, fz.json_value(rng, tune)

    def target(arg: tuple) -> object:
        k, data = arg
        if k == 0:
            return jellyfin.parse_discovery(data, "192.168.1.7", "jellyfin")
        if k == 1:
            return plex.parse_gdm(data, "192.168.1.8")
        if k == 2:
            return dlna._locations(data)
        if k == 3:
            for r in data if isinstance(data, list) else []:
                if isinstance(r, dict):
                    plex._connection_order(r)
            return data
        got = plex._find_session(data)
        assert isinstance(got, str)
        return got
    fz.fuzz("discovery", target, make)


class _Wire:
    """A broker's end of a connection: what it sent, read back in pieces."""

    def __init__(self, data: bytes, piece: int) -> None:
        self.data, self.piece, self.at = data, piece, 0

    def settimeout(self, _t: float) -> None:
        pass

    def recv(self, n: int) -> bytes:
        got = self.data[self.at:self.at + min(n, self.piece)]
        self.at += len(got)
        return got

    def close(self) -> None:
        pass

    def fileno(self) -> int:
        return -1


def _mqtt_reader(data: bytes, piece: int = 1 << 20) -> mqtt.Client:
    c = mqtt.Client("broker.lan", timeout=2.0)
    c._sock = _Wire(data, piece)  # type: ignore[assignment]
    return c


def test_fuzz_mqtt_packets():
    good = (mqtt.publish_packet("dopeiptv/command/volume", b"40") + bytes([0xD0, 0])
            + bytes([0x90, 3, 0, 1, 0]) + mqtt.publish_packet("x", b"y" * 300, retain=True))

    def target(arg: tuple) -> list:
        data, piece = arg
        c = _mqtt_reader(data, piece)
        out = []
        for _ in range(50):
            try:
                ptype, flags, body = c._read_one(float("inf"))
            except (mqtt.MqttError, ConnectionError, TimeoutError):
                break
            assert len(body) <= mqtt.MAX_PACKET
            if ptype == mqtt.PUBLISH:
                try:
                    out.append(mqtt.parse_publish(flags, body))
                except mqtt.MqttError:
                    pass
        return out
    fz.fuzz("mqtt-packets", target, lambda rng: (fz.mutate_bytes(rng, good), rng.choice((1, 3, 65536))),
            seeds=[(good, 65536)])


def test_mqtt_answers_take_linear_time():
    """A broker that floods small packets before the one asked for: each
    read in time that grows with what came, not with its square."""
    def read(data: bytes) -> object:
        c = _mqtt_reader(data)
        return c._read(float("inf"), mqtt.CONNACK)
    # All of it in one read: what a fast network hands over at once.
    fz.linear(read, lambda n: bytes([0xD0, 0]) * n + bytes([0x20, 2, 0, 0]), 4000)

    def slowly(data: bytes) -> object:
        c = _mqtt_reader(data, piece=1)
        return c._read(float("inf"), mqtt.PUBLISH)
    # One large packet a byte at a time: what a broker that dawdles sends.
    fz.linear(slowly, lambda n: mqtt.publish_packet("t", b"x" * n), 4000)


# -- the phone remote -----------------------------------------------------------------

def test_fuzz_phone_queries():
    def query(text: str) -> object:
        got = phone_remote.query_params(text)
        assert len(got) <= 16 and all(len(v) <= phone_remote.QUERY_MAX for v in got.values())
        phone_remote.picture_query(text)
        for v in list(got.values())[:4]:
            phone_browse.clean_path(v), phone_browse.query_of(v), phone_browse.offset_of(v), phone_browse.ref_of(v)
        return got
    fz.fuzz("phone-query", query, lambda rng: fz.decoded(text_from(rng, (
        "q=svt&offset=40&ref=r1&path=/media/a", "x=1780000000000&s=" + "a" * 64, "a=" + "&a=" * 100, "%zz=%",
        "offset=-1&offset=99999999999999999999"), tokens=("&", "=", "%", "+", ";", "offset=", "ref=", "path="))))

    server = phone_remote.PhoneServer(0, "t" * 43, lambda name, args: None, lambda: (b"", ""))

    def signed(arg: tuple) -> str:
        headers, target, body = arg
        got = server.signed("POST", target, headers, body, now_ms=1_780_000_000_000)
        assert got in ("", "missing", "stale", "bad", "again")
        server.picture_ok(target, phone_remote.picture_query(target.partition("?")[2]), 1_780_000_000_000)
        return got
    fz.fuzz("phone-signed", signed, lambda rng: (
        {k: fz.decoded(rng.choice(fz.ODD_TEXT + ("1780000000000", "a" * 32, "0" * 64)))
         for k in rng.sample(("X-Remote-Time", "X-Remote-Nonce", "X-Remote-Sign", "x"), rng.randint(0, 4))},
        fz.decoded(text_from(rng, ("/api/state", "/thumb/a?x=1&s=2"))), fz.mutate_bytes(rng, b"{}")))

    def browse(arg: tuple) -> object:
        rows, query, offset = arg
        rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        got = phone_browse.paged(phone_browse.matching(rows, query), phone_browse.offset_of(offset))
        assert len(got.get("rows", got.get("items", []))) <= phone_browse.PAGE
        for r in rows[:5]:
            phone_browse.percent(r.get("pos"), r.get("dur"))
        return got
    fz.fuzz("phone-browse", browse, lambda rng: (
        fz.json_value(rng, [{"name": "SVT1", "pos": 10, "dur": 100}, {"name": "Dune"}]),
        fz.decoded(rng.choice(fz.ODD_TEXT)), rng.choice(fz.ODD_TEXT + (5, -1, 10 ** 30, None))))


def test_fuzz_drawn_pictures():
    """A picture the app draws (core/drawn_art.py) is asked by its address -
    the demo's, but a provider's logo field could name one too: what it asks
    is words alone, bounded, and drawn at a fixed size whatever it says."""
    pytest.importorskip("PyQt6.QtGui")
    from PyQt6.QtGui import QGuiApplication

    from dopeiptv.core import drawn_art
    app = QGuiApplication.instance() or QGuiApplication([])
    drawn_art.use_palette({"blue": "#3b82f6", "green": "#22c55e"}, lambda _c: "#ffffff")
    seeds = (drawn_art.address("logo", "SV", "blue"),
             drawn_art.address("poster", "Big Buck Bunny", "green", "2008 · Blender Foundation"))

    def target(value):
        got = drawn_art.parse(value)
        if got is not None:
            assert got.kind in drawn_art.KINDS and got.text and all(c.isprintable() for c in got.text)
            assert len(got.text) <= drawn_art.TEXT_MAX and len(got.line) <= drawn_art.LINE_MAX
        img = drawn_art.render(value, 64)
        assert (img is None) == (got is None), value
        if img is not None:
            assert max(img.width(), img.height()) <= 512
        return got

    fz.fuzz("drawn-picture", target, lambda rng: fz.decoded(text_from(rng, seeds, tokens=(
        "drawn:", "logo", "poster", "?", "&", "t=", "c=", "l=", "%0A", "%00", "‮", "%",
        "blue", "x" * 600))), seeds=seeds)
    assert app is not None


@pytest.mark.parametrize("module", ["backup", "sync"])
def test_nesting_deeper_than_python_reads_is_refused(module, tmp_path):
    """JSON nested past Python's limit inside what is opened is no backup,
    no synced copy - the error each says, never a RecursionError."""
    deep = "[" * 100_000 + "]" * 100_000
    if module == "backup":
        path = tmp_path / "b.json"
        path.write_text(json.dumps({backup.MAGIC: 1, "stores": {"main": {"playlists": deep}}}))
        with pytest.raises(backup.BackupError):
            backup.read_any(str(path))
    else:
        _slot, key = sync.derive(b"\x02" * 16)
        with pytest.raises(sync.SyncError):
            sync.unseal(key, _sync_blob(key, ('{"c": ' + deep + "}").encode()))


def test_sockets_unused():
    # The MQTT reader above reads a stand-in, never a socket of its own.
    assert not isinstance(_mqtt_reader(b"")._sock, socket.socket)
