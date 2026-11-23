"""tools/apt_fetch.py: the .deb files an install needs, several at once, each
kept only with the size and SHA-256 apt's signed lists name for it - from
the mirrors apt is set up with, the next one when one fails or crawls."""
from __future__ import annotations

import hashlib
import http.server
import importlib.util
import os
import shutil
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


def _tool():
    spec = importlib.util.spec_from_file_location("apt_fetch", _REPO / "tools" / "apt_fetch.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["apt_fetch"] = mod          # its dataclass looks itself up there
    spec.loader.exec_module(mod)
    return mod


apt_fetch = _tool()


# -- what apt says, read ------------------------------------------------------------

def test_print_uris_lines_are_read_and_odd_names_left_to_apt():
    text = (
        "'http://m/ubuntu/pool/main/a/a/a_1.0_amd64.deb' a_1.0_amd64.deb 10 MD5Sum:00\n"
        "'mirror+file:/etc/apt/apt-mirrors.txt/pool/main/b/b/b_2%3a1~x_all.deb' "
        "b_2%3a1~x_all.deb 20 SHA256:ff\n"
        "'http://m/pool/x/../../etc/passwd' ../passwd 5 MD5Sum:00\n"
        "Reading package lists...\n")
    got = apt_fetch.parse_uris(text)
    assert [(w.name, w.size) for w in got] == [("a_1.0_amd64.deb", 10), ("b_2%3a1~x_all.deb", 20)]


def test_a_files_place_is_the_lists_own_spelling():
    uri = "http://m/ubuntu/pool/main/libx/libxml2/libxml2-dev_2.9.14%2bdfsg-1_amd64.deb"
    assert apt_fetch.pool_path(uri) == "pool/main/libx/libxml2/libxml2-dev_2.9.14+dfsg-1_amd64.deb"
    assert apt_fetch.pool_path("http://m/dists/x") == ""


def test_the_mirrors_are_the_ones_apt_names_and_only_http(tmp_path):
    lst = tmp_path / "mirrors.txt"
    lst.write_text("http://a.example/ubuntu/\tpriority:1\n# a comment\n\n"
                   "https://b.example/ubuntu/\tpriority:2\nfile:/srv/mirror\n"
                   "http://a.example/ubuntu/\n", encoding="utf-8")
    assert apt_fetch.mirror_list(str(lst)) == ["http://a.example/ubuntu/", "https://b.example/ubuntu/"]
    assert apt_fetch.bases_of(f"mirror+file:{lst}/pool/main/x.deb") == \
        ["http://a.example/ubuntu/", "https://b.example/ubuntu/"]
    assert apt_fetch.bases_of("http://c.example/ubuntu/pool/main/x.deb") == ["http://c.example/ubuntu"]
    for other in ("file:/srv/pool/x.deb", "mirror+http://m/list/pool/x.deb", "cdrom:/pool/x.deb"):
        assert apt_fetch.bases_of(other) == []
    assert apt_fetch.mirror_list(str(tmp_path / "missing")) == []


def test_the_lists_records_give_each_files_hash_and_size():
    sha = "a" * 64
    text = (f"Package: a\nFilename: pool/main/a/a_1_amd64.deb\nSize: 10\nSHA256: {sha}\n"
            "Description: two\n lines\n\n"
            "Package: b\nFilename: pool/main/b/b_1_amd64.deb\nSize: 5\nSHA256: not-a-hash\n")
    assert apt_fetch.records(text) == {"pool/main/a/a_1_amd64.deb": (sha, 10)}


def test_a_file_the_lists_do_not_name_so_is_left_to_apt():
    w = apt_fetch.parse_uris(
        "'http://m/pool/a_1_amd64.deb' a_1_amd64.deb 10 MD5Sum:00\n"
        "'http://m/pool/b_1_amd64.deb' b_1_amd64.deb 10 MD5Sum:00\n"
        "'http://m/pool/c_1_amd64.deb' c_1_amd64.deb 10 MD5Sum:00\n")
    known = {"pool/a_1_amd64.deb": ("1" * 64, 10), "pool/b_1_amd64.deb": ("2" * 64, 11)}
    assert [x.name for x in apt_fetch.plan(w, known)] == ["a_1_amd64.deb"]


# -- downloads from two mirrors -------------------------------------------------------

def _deb(name: str, n: int = 4096) -> bytes:
    return hashlib.sha256(name.encode()).digest() * (n // 32)


class _Mirrors(http.server.BaseHTTPRequestHandler):
    # path -> what the mirror does with it: ("ok"|"wrong"|"404"|"more"|"trickle", bytes)
    plan: dict[str, tuple[str, bytes]] = {}
    asked: list[str] = []

    def log_message(self, *_a):
        pass

    def do_GET(self):
        type(self).asked.append(self.path)
        mode, body = type(self).plan.get(self.path, ("404", b""))
        if mode == "404":
            self.send_error(404)
            return
        if mode == "wrong":
            body = bytes(len(body))
        elif mode == "more":
            body = body + b"x" * 64
        self.send_response(200)
        self.send_header("Content-Length", str(len(body) if mode != "endless" else 1 << 38))
        self.end_headers()
        if mode == "endless":                      # far more than the lists name
            try:
                for _ in range(1 << 22):          # 256 GiB: only the cap ends it in time
                    self.wfile.write(b"x" * 65536)
            except OSError:
                pass
            return
        if mode == "trickle":
            for i in range(0, len(body), 1024):
                try:
                    self.wfile.write(body[i:i + 1024])
                    self.wfile.flush()
                except OSError:
                    return
                time.sleep(0.05)
        else:
            self.wfile.write(body)


@pytest.fixture
def mirrors(monkeypatch):
    monkeypatch.setenv("no_proxy", "*")
    for var in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        monkeypatch.delenv(var, raising=False)
    _Mirrors.plan, _Mirrors.asked = {}, []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Mirrors)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _wanted(base: str, name: str, body: bytes, bases: list[str]):
    w = apt_fetch.Wanted(f"{base}/pool/main/{name}", name, len(body))
    w.path = w.url_path = f"pool/main/{name}"
    w.sha256 = hashlib.sha256(body).hexdigest()
    w.bases = bases
    return w


def test_a_file_is_kept_only_with_the_bytes_the_lists_name(mirrors, tmp_path):
    (tmp_path / "partial").mkdir()
    one, two = f"{mirrors}/one", f"{mirrors}/two"
    files = {name: _deb(name) for name in ("good_1_amd64.deb", "fixed_1_amd64.deb",
                                            "moved_1_amd64.deb", "bad_1_amd64.deb",
                                            "long_1_amd64.deb")}
    plan = {
        "good_1_amd64.deb": [("ok", "ok")],
        "fixed_1_amd64.deb": [("wrong", "ok")],    # the first mirror's bytes are not it
        "moved_1_amd64.deb": [("404", "ok")],      # the first mirror has none
        "bad_1_amd64.deb": [("wrong", "wrong")],   # neither has it: apt's own
        "long_1_amd64.deb": [("more", "more")],    # more bytes than the lists name
    }
    for name, [(first, second)] in plan.items():
        _Mirrors.plan[f"/one/pool/main/{name}"] = (first, files[name])
        _Mirrors.plan[f"/two/pool/main/{name}"] = (second, files[name])
    deadline = time.monotonic() + 60
    results = {name: apt_fetch.fetch_one(_wanted(mirrors, name, body, [one, two]),
                                         str(tmp_path), deadline)
               for name, body in files.items()}
    assert results["good_1_amd64.deb"] == "fetched"
    assert results["fixed_1_amd64.deb"] == "fetched"
    assert results["moved_1_amd64.deb"] == "fetched"
    assert results["bad_1_amd64.deb"] == "not the bytes the lists name"
    assert results["long_1_amd64.deb"] == "not the bytes the lists name"
    kept = sorted(p.name for p in tmp_path.iterdir() if p.is_file())
    assert kept == ["fixed_1_amd64.deb", "good_1_amd64.deb", "moved_1_amd64.deb"]
    for name in kept:
        assert (tmp_path / name).read_bytes() == files[name]
    assert list((tmp_path / "partial").iterdir()) == []      # nothing half-written left


def test_a_first_try_that_crawls_moves_to_the_next_mirror(mirrors, tmp_path, monkeypatch):
    (tmp_path / "partial").mkdir()
    monkeypatch.setattr(apt_fetch, "SLOW_AFTER", 0.2)
    monkeypatch.setattr(apt_fetch, "SLOW_RATE", 10 ** 9)     # anything is slow
    monkeypatch.setattr(apt_fetch, "CHUNK", 1024)
    one, two = f"{mirrors}/one", f"{mirrors}/two"
    body = _deb("slow_1_amd64.deb", 64 * 1024)
    _Mirrors.plan["/one/pool/main/slow_1_amd64.deb"] = ("trickle", body)
    _Mirrors.plan["/two/pool/main/slow_1_amd64.deb"] = ("ok", body)
    start = time.monotonic()
    got = apt_fetch.fetch_one(_wanted(mirrors, "slow_1_amd64.deb", body, [one, two]),
                              str(tmp_path), time.monotonic() + 60)
    assert got == "fetched" and time.monotonic() - start < 2.5
    assert (tmp_path / "slow_1_amd64.deb").read_bytes() == body
    # The last mirror is never left for being slow: with one mirror it waits.
    (tmp_path / "slow_1_amd64.deb").unlink()
    _Mirrors.plan["/one/pool/main/slow_1_amd64.deb"] = ("trickle", body[:16 * 1024])
    small = body[:16 * 1024]
    assert apt_fetch.fetch_one(_wanted(mirrors, "slow_1_amd64.deb", small, [one]),
                               str(tmp_path), time.monotonic() + 60) == "fetched"


def _stub(folder: Path, name: str, out: Path, status: int = 0) -> None:
    script = folder / name
    script.write_text(f"#!/bin/sh\ncat '{out}'\nexit {status}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs sh")
def test_main_asks_apt_and_fetches_from_the_mirror_list(mirrors, tmp_path, monkeypatch, capsys):
    lst = tmp_path / "mirrors.txt"
    lst.write_text(f"{mirrors}/one/\tpriority:1\n{mirrors}/two/\tpriority:2\n", encoding="utf-8")
    a, b = _deb("liba_1_amd64.deb"), _deb("libb_1+x_amd64.deb")
    _Mirrors.plan.update({
        "/one/pool/main/liba_1_amd64.deb": ("ok", a),
        "/one/pool/main/libb_1%2bx_amd64.deb": ("404", b),
        "/two/pool/main/libb_1%2bx_amd64.deb": ("ok", b),
    })
    uris = tmp_path / "uris"
    uris.write_text(
        f"'mirror+file:{lst}/pool/main/liba_1_amd64.deb' liba_1_amd64.deb {len(a)} MD5Sum:0\n"
        f"'mirror+file:{lst}/pool/main/libb_1%2bx_amd64.deb' libb_1+x_amd64.deb {len(b)} MD5Sum:0\n",
        encoding="utf-8")
    show = tmp_path / "show"
    show.write_text(
        f"Package: liba\nFilename: pool/main/liba_1_amd64.deb\nSize: {len(a)}\n"
        f"SHA256: {hashlib.sha256(a).hexdigest()}\n\n"
        f"Package: libb\nFilename: pool/main/libb_1+x_amd64.deb\nSize: {len(b)}\n"
        f"SHA256: {hashlib.sha256(b).hexdigest()}\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir, "apt-get", uris)
    _stub(bin_dir, "apt-cache", show)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    out = tmp_path / "debs"
    assert apt_fetch.main([str(out), "liba", "libb"]) == 0
    assert (out / "liba_1_amd64.deb").read_bytes() == a
    assert (out / "libb_1+x_amd64.deb").read_bytes() == b
    assert "2 of 2 files" in capsys.readouterr().out
    # apt not answering is no failure: apt fetches it all itself.
    _stub(bin_dir, "apt-get", uris, status=100)
    assert apt_fetch.main([str(out), "liba"]) == 0
    assert "apt fetches it all" in capsys.readouterr().out
    # Nothing but package names reaches apt.
    assert apt_fetch.main([str(out), "-o", "Dir::Etc=/tmp"]) == 2


def test_a_mirror_that_sends_too_much_is_cut_off_at_the_size_the_lists_name(mirrors, tmp_path):
    (tmp_path / "partial").mkdir()
    one = f"{mirrors}/one"
    body = _deb("huge_1_amd64.deb")
    _Mirrors.plan["/one/pool/main/huge_1_amd64.deb"] = ("endless", body)
    start = time.monotonic()
    got = apt_fetch.fetch_one(_wanted(mirrors, "huge_1_amd64.deb", body, [one]),
                              str(tmp_path), time.monotonic() + 30)
    # Stopped as soon as it passed the size - not read on to the deadline.
    assert got == "not the bytes the lists name" and time.monotonic() - start < 3
    assert list(tmp_path.glob("*.deb")) == [] and list((tmp_path / "partial").iterdir()) == []
