"""yt-dlp for YouTube, Twitch and the like (core/ytdlp.py): which addresses
go through it, which yt-dlp is used (the app's own copy, else the system's),
the managed copy downloaded from a fake GitHub release - only the release
and the SHA-256 this build names (core/ytdlp_pin.py), tried before it
replaces the old one, and a copy the app did not put there never run -
the stream asked of a fake yt-dlp where the libmpv has no hook, and the
wrapper a frozen build runs it through."""
from __future__ import annotations

import hashlib
import os
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dopeiptv import VERSION
from dopeiptv.core import ytdlp
from dopeiptv.core.ytdlp import Found, YtdlpError

posix = pytest.mark.skipif(sys.platform == "win32", reason="fake yt-dlp is a shell script")

ASSET = "yt-dlp_linux"


def _script(body: str) -> bytes:
    return ("#!/bin/sh\n" + body + "\n").encode()


GOOD = _script('case "$1" in --version) echo 2099.01.02; exit 0;; esac\nexit 2')
BROKEN = _script("exit 3")


@pytest.fixture(autouse=True)
def _local_is_direct(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost,::1")


class Releases:
    """github.com/yt-dlp/yt-dlp/releases as the app uses it: /latest leads
    to the tag, /download/<tag>/<file> the release's files."""

    def __init__(self, tag="2099.01.02", body=GOOD, sums_for=None):
        self.tag, self.body = tag, body
        self.sums_for = body if sums_for is None else sums_for
        self.hits: list[tuple[str, str]] = []
        #: Where the release's file moves on to (GitHub's storage), if anywhere.
        self.moved_to: str | None = None
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                srv.hits.append((self.path, self.headers.get("User-Agent", "")))
                base = "/yt-dlp/yt-dlp/releases"
                if self.path == base + "/latest":
                    self.send_response(302)
                    self.send_header("Location", f"https://github.com{base}/tag/{srv.tag}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path == f"{base}/download/{srv.tag}/SHA2-256SUMS":
                    digest = hashlib.sha256(srv.sums_for).hexdigest()
                    return self._send(f"{'0' * 64}  yt-dlp.exe\n{digest}  {ASSET}\n".encode())
                if self.path == f"{base}/download/{srv.tag}/{ASSET}":
                    if srv.moved_to:
                        self.send_response(302)
                        self.send_header("Location", srv.moved_to)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    return self._send(srv.body)
                if self.path == "/storage/" + ASSET:
                    return self._send(srv.body)
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _send(self, data: bytes):
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/yt-dlp/yt-dlp/releases"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def releases():
    srv = Releases()
    yield srv
    srv.close()


def pin_for(body: bytes = GOOD, tag: str = "2099.01.02") -> dict:
    """What tools/ytdlp_pin.py writes, for a release whose file is *body*."""
    return {"tag": tag, "sums": {ASSET: hashlib.sha256(body).hexdigest(), "yt-dlp.exe": "0" * 64}}


@pytest.fixture(autouse=True)
def _pinned(monkeypatch):
    monkeypatch.setattr(ytdlp.ytdlp_pin, "PIN", pin_for())


# -- the addresses -------------------------------------------------------------------

def test_which_addresses_go_through_yt_dlp():
    for url in ("https://www.youtube.com/watch?v=abc", "https://youtu.be/abc",
                "https://m.twitch.tv/somechannel", "http://twitch.tv/x",
                "https://kick.com/someone", "https://music.youtube.com/live/abc"):
        assert ytdlp.is_web_stream(url), url
    for url in ("https://provider.example/live/u/p/1.ts", "https://notyoutube.com/x",
                "https://youtube.com.evil.example/x", "rtsp://youtube.com/x", "", "ytdl"):
        assert not ytdlp.is_web_stream(url), url


def test_what_the_address_says_about_being_live():
    assert ytdlp.looks_live("https://www.twitch.tv/somechannel")
    assert not ytdlp.looks_live("https://www.twitch.tv/videos/123")
    assert not ytdlp.looks_live("https://www.twitch.tv/somechannel/clip/x")
    assert ytdlp.looks_live("https://www.youtube.com/@lofigirl/live")
    assert ytdlp.looks_live("https://www.youtube.com/live/abcdef")
    assert ytdlp.looks_live("https://kick.com/someone")
    assert not ytdlp.looks_live("https://www.youtube.com/watch?v=abc")
    assert ytdlp.display_name("https://www.twitch.tv/somechannel/") == "twitch.tv/somechannel"
    assert ytdlp.display_name("https://www.youtube.com/watch?v=abc") == "youtube.com/watch"
    assert ytdlp.display_name("https://youtu.be/?v=abc") == "youtu.be?v=abc"
    assert ytdlp.mpv_address("https://youtu.be/x") == "ytdl://https://youtu.be/x"
    assert ytdlp.mpv_address("ytdl://https://youtu.be/x") == "ytdl://https://youtu.be/x"


def test_the_release_file_for_each_computer():
    assert ytdlp.asset_name("linux", "x86_64", musl=False) == "yt-dlp_linux"
    assert ytdlp.asset_name("linux", "aarch64", musl=False) == "yt-dlp_linux_aarch64"
    assert ytdlp.asset_name("linux", "armv7l", musl=False) == "yt-dlp_linux_armv7l"
    assert ytdlp.asset_name("linux", "x86_64", musl=True) == "yt-dlp_musllinux"
    assert ytdlp.asset_name("linux", "riscv64", musl=False) is None
    assert ytdlp.asset_name("darwin", "arm64") == "yt-dlp_macos"
    assert ytdlp.asset_name("win32", "AMD64") == "yt-dlp.exe"
    assert ytdlp.asset_name("win32", "ARM64") == "yt-dlp_arm64.exe"
    assert ytdlp.asset_name("freebsd14", "amd64") is None


# -- which yt-dlp ----------------------------------------------------------------------

@posix
def test_the_apps_copy_first_while_it_is_on_else_the_systems(tmp_path):
    def which(name):
        assert name == "yt-dlp"
        return "/usr/bin/yt-dlp"

    def none(_name):
        return None

    assert ytdlp.find(True, tmp_path, which) == Found("/usr/bin/yt-dlp", False)
    assert ytdlp.find(True, tmp_path, none) is None
    own = ytdlp.managed_path(tmp_path)
    own.write_bytes(GOOD)
    assert ytdlp.find(True, tmp_path, which) == Found("/usr/bin/yt-dlp", False)  # not runnable
    own.chmod(0o755)
    assert ytdlp.find(True, tmp_path, which) == Found("/usr/bin/yt-dlp", False)  # not the app's own
    ytdlp._mark(tmp_path, "2099.01.02", "0" * 64)
    assert ytdlp.find(True, tmp_path, which) == Found(str(own), True)
    own.write_bytes(GOOD + b"# changed since\n")
    assert ytdlp.find(True, tmp_path, which) == Found("/usr/bin/yt-dlp", False)
    ytdlp._mark(tmp_path, "2099.01.02", "0" * 64)
    # Off: the system's only, the app's copy is not used.
    assert ytdlp.find(False, tmp_path, which) == Found("/usr/bin/yt-dlp", False)
    assert ytdlp.find(False, tmp_path, none) is None


def test_the_data_folder_is_the_users(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert ytdlp.managed_path() == tmp_path / "dopeiptv" / "yt-dlp" / "yt-dlp"


def test_the_choice_and_the_last_check_are_this_computers():
    from dopeiptv.core import sync
    assert not sync.syncable(ytdlp.SETTING_MANAGED)
    assert not sync.syncable(ytdlp.SETTING_CHECKED)


def test_at_most_once_a_day():
    day = ytdlp.CHECK_EVERY
    assert ytdlp.update_due(0, 1000.0)
    assert ytdlp.update_due(None, 1000.0)
    assert ytdlp.update_due("junk", 1000.0)
    assert not ytdlp.update_due(1000, 1000.0 + day - 1)
    assert ytdlp.update_due(1000, 1000.0 + day)
    assert ytdlp.update_due(5000, 1000.0)          # the clock went back


def test_the_sums_file():
    a, b = "a" * 64, "B" * 64
    text = f"{a}  yt-dlp\n{b} *yt-dlp.exe\nnot a line\n{'c' * 10}  short\n"
    assert ytdlp.parse_sums(text) == {"yt-dlp": a, "yt-dlp.exe": b.lower()}


# -- the managed copy -----------------------------------------------------------------

@posix
def test_downloaded_checked_tried_and_put_in_place(tmp_path, releases):
    seen = []
    tag = ytdlp.install(tmp_path, releases=releases.url, asset=ASSET,
                        progress=lambda got, total: seen.append((got, total)))
    assert tag == "2099.01.02"
    own = ytdlp.managed_path(tmp_path)
    assert own.read_bytes() == GOOD and os.stat(own).st_mode & stat.S_IXUSR
    assert ytdlp.installed_tag(tmp_path) == "2099.01.02"
    assert seen and seen[-1] == (len(GOOD), len(GOOD))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["pinned.txt", "release.txt", "yt-dlp"]
    assert ytdlp.version_of(str(own)) == "2099.01.02"
    # Only the app's name and version go with each request.
    assert {ua for _p, ua in releases.hits} == {f"dopeIPTV/{VERSION}"}
    assert ytdlp.own_copy(tmp_path) and ytdlp.find(True, tmp_path, which=lambda _n: None).managed
    # Only the file: no checksum list is asked of the release.
    assert [p for p, _ua in releases.hits] == [f"/yt-dlp/yt-dlp/releases/download/2099.01.02/{ASSET}"]
    # Already the release this build names: nothing asked of the network.
    before = len(releases.hits)
    assert ytdlp.update(tmp_path, releases=releases.url) is None
    assert releases.hits[before:] == []


@posix
def test_a_newer_release_named_by_a_newer_app_replaces_the_copy(tmp_path, releases, monkeypatch):
    monkeypatch.setattr(ytdlp, "asset_name", lambda *a, **k: ASSET)
    ytdlp.install(tmp_path, releases=releases.url)
    releases.tag = "2099.02.01"
    releases.body = releases.sums_for = GOOD.replace(b"2099.01.02", b"2099.02.01")
    # The release has moved on, but this build names the old one: kept.
    assert ytdlp.update(tmp_path, releases=releases.url) is None
    monkeypatch.setattr(ytdlp.ytdlp_pin, "PIN", pin_for(releases.body, "2099.02.01"))
    assert ytdlp.update(tmp_path, releases=releases.url) == "2099.02.01"
    assert ytdlp.version_of(str(ytdlp.managed_path(tmp_path))) == "2099.02.01"


@posix
def test_a_release_whose_file_and_sums_were_both_replaced_is_never_used(tmp_path):
    """Whoever controls the release - its file and the SHA2-256SUMS beside
    it - still does not get a file run: the SHA-256 is the build's own."""
    evil = _script('case "$1" in --version) echo 2099.01.02; exit 0;; esac\necho pwned')
    srv = Releases(body=evil, sums_for=evil)
    try:
        with pytest.raises(YtdlpError) as e:
            ytdlp.install(tmp_path, releases=srv.url, asset=ASSET)
        assert e.value.reason == "checksum"
        assert not ytdlp.managed_path(tmp_path).exists()
    finally:
        srv.close()


def test_a_build_that_names_no_release_downloads_none(tmp_path, monkeypatch):
    monkeypatch.setattr(ytdlp.ytdlp_pin, "PIN", None)
    with pytest.raises(YtdlpError) as e:
        ytdlp.install(tmp_path, releases="http://127.0.0.1:9/never", asset=ASSET)
    assert e.value.reason == "pin"
    assert ytdlp.update(tmp_path, releases="http://127.0.0.1:9/never") is None
    for bad in ({"tag": "x y", "sums": {ASSET: "0" * 64}}, {"tag": "1", "sums": {ASSET: "zz"}},
                {"tag": "1"}, "2099.01.02", ["2099"]):
        assert ytdlp.pinned(bad) is None


@posix
def test_a_copy_the_app_did_not_put_there_is_not_run(tmp_path):
    """An older app's copy, unpinned, or one changed since: the system's is
    used, and the person is offered the build's own."""
    own = ytdlp.managed_path(tmp_path)
    own.write_bytes(GOOD)
    own.chmod(0o755)
    (tmp_path / "release.txt").write_text("2099.01.02\n")
    assert not ytdlp.own_copy(tmp_path)
    assert ytdlp.find(True, tmp_path, which=lambda _n: "/usr/bin/yt-dlp") == Found("/usr/bin/yt-dlp", False)
    assert ytdlp.find(True, tmp_path, which=lambda _n: None) is None


@posix
def test_a_download_that_does_not_match_its_checksum_is_never_used(tmp_path):
    srv = Releases(body=GOOD + b"# tampered\n", sums_for=GOOD)
    try:
        own = ytdlp.managed_path(tmp_path)
        own.write_bytes(b"#!/bin/sh\necho old\n")
        with pytest.raises(YtdlpError) as e:
            ytdlp.install(tmp_path, releases=srv.url, asset=ASSET)
        assert e.value.reason == "checksum"
        assert own.read_bytes() == b"#!/bin/sh\necho old\n"           # untouched
        assert sorted(p.name for p in tmp_path.iterdir()) == ["yt-dlp"]  # no temp left
    finally:
        srv.close()


@posix
def test_a_download_that_does_not_start_is_never_used(tmp_path, monkeypatch):
    srv = Releases(body=BROKEN)
    monkeypatch.setattr(ytdlp.ytdlp_pin, "PIN", pin_for(BROKEN))
    try:
        with pytest.raises(YtdlpError) as e:
            ytdlp.install(tmp_path, releases=srv.url, asset=ASSET)
        assert e.value.reason == "runs"
        assert list(tmp_path.iterdir()) == []
    finally:
        srv.close()


@posix
def test_the_download_follows_its_redirects_only_over_https(tmp_path, releases):
    """A release's file moves on to GitHub's storage. Followed by hand,
    each hop https (rule 10): a hop to plain http is refused before it is
    asked - only a test's own server on this computer may be http."""
    port = releases.httpd.server_address[1]
    releases.moved_to = f"http://127.0.0.1:{port}/storage/{ASSET}"
    assert ytdlp.install(tmp_path, releases=releases.url, asset=ASSET) == "2099.01.02"
    assert ("/storage/" + ASSET, f"dopeIPTV/{VERSION}") in releases.hits
    releases.moved_to = f"http://downloads.example/storage/{ASSET}"
    with pytest.raises(YtdlpError) as e:
        ytdlp.install(tmp_path / "again", releases=releases.url, asset=ASSET)
    assert e.value.reason == "download"
    assert not (tmp_path / "again").exists() or not ytdlp.managed_path(tmp_path / "again").exists()


def test_no_release_binary_for_this_computer(tmp_path, monkeypatch):
    monkeypatch.setattr(ytdlp, "asset_name", lambda *a, **k: None)
    with pytest.raises(YtdlpError) as e:
        ytdlp.install(tmp_path, releases="http://127.0.0.1:9/never")
    assert e.value.reason == "platform"


def test_a_release_without_the_file_named(tmp_path, releases):
    with pytest.raises(YtdlpError) as e:
        ytdlp.install(tmp_path, releases=releases.url + "/nothing", asset=ASSET)
    assert e.value.reason == "download"


# -- running it ---------------------------------------------------------------------------

@posix
def test_the_stream_asked_of_yt_dlp_itself(tmp_path):
    fake = tmp_path / "yt-dlp"
    log = tmp_path / "args"
    fake.write_bytes(_script(
        f'echo "$@" > {log}\n'
        "echo '{\"title\": \"Night stream\", \"uploader\": \"somechannel\", \"is_live\": true,"
        " \"url\": \"https://video.example/live.m3u8\", \"http_headers\": {\"User-Agent\": \"X\"}}'"))
    fake.chmod(0o755)
    info = ytdlp.resolve(str(fake), "https://www.twitch.tv/somechannel")
    assert info == {"url": "https://video.example/live.m3u8",
                    "title": "somechannel - Night stream", "live": True,
                    "headers": {"User-Agent": "X"}}
    args = log.read_text().split()
    assert args[-2:] == ["--", "https://www.twitch.tv/somechannel"] and "-J" in args

    fake.write_bytes(_script("echo 'ERROR: [twitch] somechannel: The channel is not live' >&2; exit 1"))
    with pytest.raises(YtdlpError) as e:
        ytdlp.resolve(str(fake), "https://www.twitch.tv/somechannel")
    assert e.value.reason == "failed" and "not live" in e.value.detail

    fake.write_bytes(_script("echo '{\"title\": \"two parts\", \"requested_formats\": []}'"))
    with pytest.raises(YtdlpError):
        ytdlp.resolve(str(fake), "https://www.youtube.com/watch?v=x")
    with pytest.raises(YtdlpError) as e:
        ytdlp.resolve(str(tmp_path / "missing"), "https://youtu.be/x")
    assert e.value.reason == "missing"


def test_the_wrapper_clears_the_bundles_library_paths():
    text = ytdlp.wrapper_text("/home/me/.local/share/dopeiptv/yt-dlp/yt-dlp",
                              {"LD_LIBRARY_PATH": "/usr/lib/it's", "PATH": "/usr/bin"})
    assert text.startswith("#!/bin/sh\n")
    assert "LD_LIBRARY_PATH='/usr/lib/it'\"'\"'s'; export LD_LIBRARY_PATH" in text
    assert "unset PYTHONHOME" in text and "unset LD_PRELOAD" in text
    assert "PATH=" not in text.replace("LD_LIBRARY_PATH=", "")
    assert text.rstrip().endswith('exec /home/me/.local/share/dopeiptv/yt-dlp/yt-dlp "$@"')


@posix
def test_a_frozen_build_hands_mpv_the_wrapper(tmp_path):
    found = Found("/usr/bin/yt-dlp", False)
    assert ytdlp.hook_path(found, tmp_path, frozen=False) == "/usr/bin/yt-dlp"
    path = ytdlp.hook_path(found, tmp_path, frozen=True)
    assert path == str(tmp_path / "yt-dlp-run")
    assert os.access(path, os.X_OK)
    assert "exec /usr/bin/yt-dlp" in open(path).read()
    assert ytdlp.hook_path(found, tmp_path, frozen=True) == path     # written once
