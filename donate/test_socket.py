#!/usr/bin/env python3
"""The service's Unix socket (donate.py open_to_nginx, main): nginx's
workers' and the service's alone - 0660 and nginx's group (SOCKET_GROUP,
www), in a folder the rc file makes iptv:www 0750 - where before anyone in
the jail could connect (0666). Where the group does not exist, or cannot be
given, it is 0666 as before, so nginx still reaches it, in a folder nobody
else can put anything in. The group's id and the calls that change a file
are made up for the rules, a real socket for the rest.

Plain Python: ``python3 donate/test_socket.py`` runs them all (pytest finds
them too). Everything in a temporary directory.
"""

from __future__ import annotations

import grp
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)


def _donate():
    root = tempfile.mkdtemp()
    stats = os.path.join(root, "stats")
    os.makedirs(stats)
    conf = os.path.join(root, "donate.conf")
    with open(conf, "w") as fh:
        fh.write(f"STATS_DIR={stats}\nLISTEN=127.0.0.1:0\n")
    os.environ["DONATE_CONFIG"] = conf
    os.environ["STATS_DIR"] = stats
    sys.modules.pop("donate", None)
    import donate
    return donate


class _Files:
    """os.stat, os.chown and os.chmod for made-up files: each path's
    (uid, gid, mode), and every change asked for."""

    def __init__(self, files: dict, chown_fails: bool = False):
        self.files, self.calls, self.chown_fails = files, [], chown_fails

    def stat(self, path, *a, **k):
        uid, gid, mode = self.files[path]
        return os.stat_result((mode, 0, 0, 1, uid, gid, 0, 0, 0, 0))

    def chown(self, path, uid, gid):
        self.calls.append(("chown", path, uid, gid))
        if self.chown_fails:
            raise PermissionError(1, "Operation not permitted")
        u, g, m = self.files[path]
        self.files[path] = (u if uid == -1 else uid, g if gid == -1 else gid, m)

    def chmod(self, path, mode):
        self.calls.append(("chmod", path, oct(mode)))
        u, g, m = self.files[path]
        self.files[path] = (u, g, stat.S_IFMT(m) | mode)

    def patch(self, d, groups: dict):
        def getgrnam(name):
            if name not in groups:
                raise KeyError(name)
            return mock.Mock(gr_gid=groups[name])
        return [mock.patch.object(d.os, "stat", self.stat), mock.patch.object(d.os, "chown", self.chown),
                mock.patch.object(d.os, "chmod", self.chmod), mock.patch.object(d.os, "geteuid", lambda: 1001),
                mock.patch("grp.getgrnam", getgrnam)]


SOCK, DIR = "/var/run/iptv_donate/donate.sock", "/var/run/iptv_donate"


def _run(d, files: _Files, groups: dict, group: str = "www") -> str:
    patches = files.patch(d, groups)
    for p in patches:
        p.start()
    try:
        return d.open_to_nginx(SOCK, group)
    finally:
        for p in reversed(patches):
            p.stop()


def test_in_the_rc_files_folder_the_socket_is_nginxs_group_and_0660():
    d = _donate()
    # FreeBSD gives a new file its folder's group: the socket already is www's.
    files = _Files({SOCK: (1001, 80, stat.S_IFSOCK | 0o660), DIR: (1001, 80, stat.S_IFDIR | 0o750)})
    said = _run(d, files, {"www": 80})
    assert said == "0660, group www"
    assert files.calls == [("chmod", SOCK, "0o660")]           # no chown needed, the folder untouched
    assert files.files[SOCK][1:] == (80, stat.S_IFSOCK | 0o660)


def test_elsewhere_the_socket_is_given_the_group():
    d = _donate()
    files = _Files({SOCK: (1001, 1001, stat.S_IFSOCK | 0o660), DIR: (1001, 1001, stat.S_IFDIR | 0o755)})
    assert _run(d, files, {"www": 80}) == "0660, group www"
    assert files.calls == [("chown", SOCK, -1, 80), ("chmod", SOCK, "0o660")]
    assert files.files[SOCK] == (1001, 80, stat.S_IFSOCK | 0o660)


def test_without_the_group_it_is_0666_as_before_in_a_folder_of_its_own():
    d = _donate()
    files = _Files({SOCK: (1001, 1001, stat.S_IFSOCK | 0o660), DIR: (1001, 1001, stat.S_IFDIR | 0o777)})
    said = _run(d, files, {}, group="www")
    assert said.startswith("0666: no group 'www' here")
    assert files.files[SOCK][2] == stat.S_IFSOCK | 0o666       # nginx reaches it, whoever it runs as
    assert files.files[DIR][2] == stat.S_IFDIR | 0o711         # passed through, nothing put in or listed
    assert not [c for c in files.calls if c[0] == "chown"]


def test_a_group_it_may_not_give_is_0666_too():
    d = _donate()
    files = _Files({SOCK: (1001, 1001, stat.S_IFSOCK | 0o660), DIR: (1001, 1001, stat.S_IFDIR | 0o700)},
                   chown_fails=True)
    said = _run(d, files, {"www": 80})
    assert said.startswith("0666: the group 'www' could not be given")
    assert files.files[SOCK][2] == stat.S_IFSOCK | 0o666
    assert files.files[DIR][2] == stat.S_IFDIR | 0o711         # 0700 kept nginx out: now it can pass


def test_a_folder_not_its_own_is_left_alone():
    d = _donate()
    files = _Files({SOCK: (1001, 1001, stat.S_IFSOCK | 0o660), DIR: (0, 0, stat.S_IFDIR | 0o1777)})
    _run(d, files, {})
    assert files.files[DIR][2] == stat.S_IFDIR | 0o1777        # /tmp and the like: someone else's
    assert ("chmod", DIR, "0o711") not in files.calls


def _own_group() -> str:
    return grp.getgrgid(os.getgid()).gr_name


def test_a_real_socket_comes_out_so():
    d = _donate()
    folder = tempfile.mkdtemp(prefix="s")
    path = os.path.join(folder, "d.sock")
    old = os.umask(0o117)
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(path)
    finally:
        os.umask(old)
    try:
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o660    # never wider, from the start
        assert d.open_to_nginx(path, _own_group()) == f"0660, group {_own_group()}"
        st = os.stat(path)
        assert stat.S_IMODE(st.st_mode) == 0o660 and st.st_gid == os.getgid()
        assert d.open_to_nginx(path, "no-such-group-here").startswith("0666")
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o666
        assert stat.S_IMODE(os.stat(folder).st_mode) == 0o711
    finally:
        s.close()


def test_the_service_starts_on_it_and_answers_through_it():
    root = tempfile.mkdtemp(prefix="d")
    stats, run = os.path.join(root, "stats"), os.path.join(root, "run")
    os.makedirs(stats)
    os.makedirs(run, mode=0o750)
    path = os.path.join(run, "donate.sock")
    conf = os.path.join(root, "donate.conf")
    with open(conf, "w") as fh:
        fh.write(f"STATS_DIR={stats}\nLISTEN=unix:{path}\nSOCKET_GROUP={_own_group()}\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("STATS_", "LISTEN", "SOCKET_"))}
    env["DONATE_CONFIG"] = conf
    script = os.path.join(HERE, "donate.py")
    proc = subprocess.Popen([sys.executable, "-I", script], env=env, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 30
        while not os.path.exists(path) and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        st = os.stat(path)
        assert stat.S_ISSOCK(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o660 and st.st_gid == os.getgid()
        got = subprocess.run([sys.executable, "-I", script, "--check"], env=env, capture_output=True,
                             text=True, timeout=30)
        assert got.returncode == 0 and got.stdout.startswith("200 "), got.stdout
    finally:
        proc.terminate()
        _out, said = proc.communicate(timeout=10)
    assert f"listening on unix:{path} (0660, group {_own_group()})" in said, said


def test_the_rc_file_makes_the_folder_the_services_and_nginxs_alone():
    rc = open(os.path.join(ROOT, "deploy", "iptv_donate"), encoding="utf-8").read()
    assert re.search(r"^: \$\{iptv_donate_socket_group:=www\}$", rc, re.M)
    body = re.search(r"iptv_donate_precmd\(\)\n\{\n(.*?)\n\}", rc, re.S).group(1).strip()
    assert body == 'install -d -o "${iptv_donate_user}" -g "${iptv_donate_socket_group}" -m 750 "${piddir}"'
    # The socket is in that folder, by default and in nginx's file.
    d = _donate()
    assert d.CONF.get("LISTEN", "unix:/var/run/iptv_donate/donate.sock") == "127.0.0.1:0"
    src = open(os.path.join(HERE, "donate.py"), encoding="utf-8").read()
    assert 'CONF.get("LISTEN", "unix:/var/run/iptv_donate/donate.sock")' in src
    assert 'CONF.get("SOCKET_GROUP", "www")' in src
    nginx = open(os.path.join(ROOT, "deploy", "content-iptv.dope.rs.conf"), encoding="utf-8").read()
    socks = set(re.findall(r"proxy_pass http://unix:([^:;\s]+)", nginx))
    assert socks == {"/var/run/iptv_donate/donate.sock"}
    # main makes the socket 0660 from the start and leaves its mode to
    # open_to_nginx: never a chmod of its own.
    import ast
    main = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "main")
    code = ast.get_source_segment(src, main)
    assert "open_to_nginx(path)" in code and "os.umask(0o117)" in code
    assert "chmod" not in code and "0o666" not in code


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
