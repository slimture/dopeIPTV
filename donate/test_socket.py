#!/usr/bin/env python3
"""The service's Unix socket (donate.py open_to_nginx, main): nginx's
workers' and the service's alone - 0660 and nginx's group (SOCKET_GROUP,
www), in a folder the rc file makes iptv:www 0750 - where before anyone in
the jail could connect (0666). Where the group does not exist, or cannot be
given, it fails closed: the service does not start, and says why, naming
SOCKET_GROUP - nothing opens it to every user (a developer's machine
names a group of its own user's). The group's
id and the calls that change a file are made up for the rules, a real
socket and a real start for the rest.

Plain Python: ``python3 donate/test_socket.py`` runs them all (pytest finds
them too). Everything in a temporary directory.
"""

from __future__ import annotations

import grp
import os
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


def _refused(d, files: _Files, groups: dict, group: str = "www") -> str:
    try:
        said = _run(d, files, groups, group)
    except d.SocketNotPrivate as e:
        return str(e)
    raise AssertionError(f"opened: {said}")


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


def test_without_the_group_it_does_not_start():
    d = _donate()
    files = _Files({SOCK: (1001, 1001, stat.S_IFSOCK | 0o660), DIR: (1001, 1001, stat.S_IFDIR | 0o777)})
    said = _refused(d, files, {}, group="www")
    assert "no group 'www' here" in said and "SOCKET_GROUP" in said
    assert d.CONFIG_FILE in said                               # where to put it right
    assert files.calls == []                                   # the socket as made: 0660, never wider
    assert files.files[SOCK][2] == stat.S_IFSOCK | 0o660 and files.files[DIR][2] == stat.S_IFDIR | 0o777


def test_a_group_it_may_not_give_does_not_start_either():
    d = _donate()
    files = _Files({SOCK: (1001, 1001, stat.S_IFSOCK | 0o660), DIR: (1001, 1001, stat.S_IFDIR | 0o700)},
                   chown_fails=True)
    said = _refused(d, files, {"www": 80})
    assert "the group 'www' could not be given" in said and "SOCKET_GROUP" in said
    assert [c for c in files.calls if c[0] == "chmod"] == []
    assert files.files[SOCK][2] == stat.S_IFSOCK | 0o660 and files.files[DIR][2] == stat.S_IFDIR | 0o700
    # No group named at all is no way round it.
    assert "SOCKET_GROUP is empty" in _refused(d, _Files({SOCK: (1001, 1001, stat.S_IFSOCK | 0o660)}), {}, "")


def test_nothing_opens_it_to_every_user():
    """SOCKET_OPEN = yes, which once made it 0666, is no setting now: the
    service still does not start without the group."""
    os.environ["SOCKET_OPEN"] = "yes"
    try:
        d = _donate()
        assert not hasattr(d, "SOCKET_OPEN")
        files = _Files({SOCK: (1001, 1001, stat.S_IFSOCK | 0o660), DIR: (0, 0, stat.S_IFDIR | 0o1777)})
        _refused(d, files, {}, group="www")
        assert files.calls == [] and files.files[DIR][2] == stat.S_IFDIR | 0o1777
    finally:
        del os.environ["SOCKET_OPEN"]


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
        try:
            d.open_to_nginx(path, "no-such-group-here")
            raise AssertionError("opened without the group")
        except d.SocketNotPrivate:
            pass
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o660    # left as it was made
        assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700
    finally:
        s.close()


SCRIPT = os.path.join(HERE, "donate.py")


def _service(conf_lines: str) -> tuple:
    """The service's config in a folder of its own, its socket there: the
    socket's path and the environment to start it with."""
    root = tempfile.mkdtemp(prefix="d")
    stats, run = os.path.join(root, "stats"), os.path.join(root, "run")
    os.makedirs(stats)
    os.makedirs(run, mode=0o750)
    path = os.path.join(run, "donate.sock")
    conf = os.path.join(root, "donate.conf")
    with open(conf, "w") as fh:
        fh.write(f"STATS_DIR={stats}\nLISTEN=unix:{path}\n{conf_lines}")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("STATS_", "LISTEN", "SOCKET_"))}
    env["DONATE_CONFIG"] = conf
    return path, env


def _answers(path: str, env: dict) -> tuple:
    """Start it, ask it through its socket (--check), stop it: the socket as
    it was while it ran, and what the service said."""
    proc = subprocess.Popen([sys.executable, "-I", SCRIPT], env=env, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 30
        while not os.path.exists(path) and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        st = os.stat(path)
        got = subprocess.run([sys.executable, "-I", SCRIPT, "--check"], env=env, capture_output=True,
                             text=True, timeout=30)
        assert got.returncode == 0 and got.stdout.startswith("200 "), got.stdout
    finally:
        proc.terminate()
        _out, said = proc.communicate(timeout=10)
    return st, said


def test_the_service_starts_on_it_and_answers_through_it():
    path, env = _service(f"SOCKET_GROUP={_own_group()}\n")
    st, said = _answers(path, env)
    assert stat.S_ISSOCK(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o660 and st.st_gid == os.getgid()
    assert f"listening on unix:{path} (0660, group {_own_group()})" in said, said


def test_without_the_group_the_service_does_not_start_and_says_why():
    path, env = _service("SOCKET_GROUP=no-such-group-here\n")
    got = subprocess.run([sys.executable, "-I", SCRIPT], env=env, capture_output=True, text=True, timeout=30)
    assert got.returncode == 1, got.stderr
    said = got.stderr
    assert "[donate] not started: " in said and "listening on" not in said, said
    assert "no group 'no-such-group-here' here" in said and "SOCKET_GROUP in " + env["DONATE_CONFIG"] in said
    assert "SOCKET_OPEN" not in said
    assert not os.path.exists(path)                            # nothing left to connect to
    # The old SOCKET_OPEN = yes opens nothing: still not started.
    path, env = _service("SOCKET_GROUP=no-such-group-here\nSOCKET_OPEN = yes\n")
    got = subprocess.run([sys.executable, "-I", SCRIPT], env=env, capture_output=True, text=True, timeout=30)
    assert got.returncode == 1 and "[donate] not started: " in got.stderr, got.stderr
    assert not os.path.exists(path)


def test_the_socket_is_the_services_and_nginxs_alone():
    # The rc file that makes its folder, and nginx's file that names it, are
    # the server's: its own repository checks them against these defaults.
    d = _donate()
    assert d.CONF.get("LISTEN", "unix:/var/run/iptv_donate/donate.sock") == "127.0.0.1:0"
    src = open(os.path.join(HERE, "donate.py"), encoding="utf-8").read()
    assert 'CONF.get("LISTEN", "unix:/var/run/iptv_donate/donate.sock")' in src
    assert 'CONF.get("SOCKET_GROUP", "www")' in src
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
