#!/usr/bin/env python3
"""Fetch the .deb files an ``apt-get install`` would download, several at once.

    python3 -I tools/apt_fetch.py DIR PACKAGE...
    sudo apt-get install -y --no-install-recommends \\
        -o Dir::Cache::Archives=DIR PACKAGE...

Ubuntu's mirror for GitHub's runners serves its package lists fast and the
packages themselves at times at 100 kB/s; apt takes them one after another,
and that was nine minutes of a release build. This asks apt which files the
same install needs (``--print-uris``: apt's own resolution, after
``apt-get update``), takes each file's size and SHA-256 from apt's lists -
the ones ``apt-get update`` checked against the archive's signature - and
downloads up to ``WORKERS`` at once from the mirrors apt itself is set up
with, moving to the next mirror when one fails, or crawls on its first try.

A file is kept only with exactly the size and SHA-256 the lists name, and
written into DIR under the name apt gave it. apt then installs from DIR - it
checks a file there by its size alone, which is why the SHA-256 is checked
here - and downloads whatever is not there itself. So nothing here can make
an install take what apt would not, or fail: at worst apt fetches all of it,
as before. Only http and https, only the hosts apt's sources name.

Standard library only.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

WORKERS = 8
READ_TIMEOUT = 30           # seconds without a byte: that mirror is given up
SLOW_AFTER = 8              # on a file's first try, seconds before judging it
SLOW_RATE = 200_000         # bytes a second under which the first try moves on
DEADLINE = 300              # seconds for all of it; what is left is apt's
CHUNK = 1 << 16
USER_AGENT = "dopeIPTV-build apt_fetch"

_URI = re.compile(r"^'([^']+)' (\S+) (\d+) \S+$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+~%_-]*\.deb$")


@dataclass
class Wanted:
    """One file apt would download: where apt would take it from, the name
    it would keep it under, and what the signed lists say it is."""
    uri: str
    name: str
    size: int
    path: str = ""                       # pool/... inside the archive, as the lists name it
    url_path: str = ""                   # the same as apt's address spells it (%2b for +)
    sha256: str = ""
    bases: list[str] = field(default_factory=list)


def parse_uris(text: str) -> list[Wanted]:
    """``apt-get install --print-uris`` lines: 'URI' NAME SIZE HASH. A line
    that is not one, or names a file that is no plain .deb name, is left out
    (apt downloads that one itself)."""
    out = []
    for line in text.splitlines():
        m = _URI.match(line.strip())
        if not m or not _NAME.match(m.group(2)):
            continue
        out.append(Wanted(m.group(1), m.group(2), int(m.group(3))))
    return out


def pool_path(uri: str) -> str:
    """The file's place inside the archive (``pool/main/...``) as the
    lists' Filename field names it - apt's address spells a + as %2b and a
    ~ as %7e; "" when the address has none."""
    i = uri.find("/pool/")
    return urllib.parse.unquote(uri[i + 1:]) if i >= 0 else ""


def mirror_list(path: str) -> list[str]:
    """The base addresses a ``mirror+file:`` list names, in its order: one
    per line, a tab and its options after it, # a comment. Only http(s)."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    bases = []
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        base = line.split()[0]
        if base.startswith(("http://", "https://")) and base not in bases:
            bases.append(base)
    return bases


def bases_of(uri: str) -> list[str]:
    """Where *uri*'s file can come from: its own archive for an http(s)
    address, every mirror of a ``mirror+file:`` one, nothing for any other
    kind (a file:, cdrom: or mirror+http: source stays apt's)."""
    i = uri.find("/pool/")
    if i < 0:
        return []
    root = uri[:i]
    if root.startswith(("http://", "https://")):
        return [root]
    if root.startswith("mirror+file:"):
        return mirror_list(root[len("mirror+file:"):])
    return []


def records(text: str) -> dict[str, tuple[str, int]]:
    """``apt-cache show`` output as {Filename: (SHA256, Size)}."""
    out: dict[str, tuple[str, int]] = {}
    for para in text.split("\n\n"):
        fields = {}
        for line in para.splitlines():
            key, sep, value = line.partition(": ")
            if sep and not line.startswith(" "):
                fields[key] = value.strip()
        name, sha, size = fields.get("Filename"), fields.get("SHA256"), fields.get("Size")
        if name and sha and re.fullmatch(r"[0-9a-f]{64}", sha) and (size or "").isdigit():
            out[name] = (sha, int(size or 0))
    return out


def plan(wanted: list[Wanted], known: dict[str, tuple[str, int]]) -> list[Wanted]:
    """The files to fetch here: those the lists name with the size apt
    printed, and with somewhere to fetch them from."""
    out = []
    for w in wanted:
        w.path = pool_path(w.uri)
        w.url_path = w.uri[w.uri.find("/pool/") + 1:]
        record = known.get(w.path)
        if record is None or record[1] != w.size:
            continue
        w.sha256 = record[0]
        w.bases = bases_of(w.uri)
        if w.bases:
            out.append(w)
    return out


class _Slow(Exception):
    pass


def _get(url: str, w: Wanted, part: str, judge: bool, deadline: float) -> bool:
    """*url* into *part*: True when it is exactly *w*'s bytes."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    digest, got, start = hashlib.sha256(), 0, time.monotonic()
    with urllib.request.urlopen(req, timeout=READ_TIMEOUT) as resp, open(part, "wb") as f:
        while True:
            chunk = resp.read(CHUNK)
            if not chunk:
                break
            got += len(chunk)
            if got > w.size:
                return False
            if time.monotonic() > deadline:
                raise TimeoutError("out of time")
            digest.update(chunk)
            f.write(chunk)
            took = time.monotonic() - start
            if judge and took > SLOW_AFTER and got / took < SLOW_RATE \
                    and w.size - got > (CHUNK * 8):
                raise _Slow
    return got == w.size and digest.hexdigest() == w.sha256


def fetch_one(w: Wanted, folder: str, deadline: float) -> str:
    """*w* into *folder* from the first of its mirrors that gives its bytes:
    "fetched", or why not. Its first try moves on when it crawls."""
    part = os.path.join(folder, "partial", w.name + ".apt-fetch")
    why = "no mirror"
    for i, base in enumerate(w.bases):
        if time.monotonic() > deadline:
            why = "out of time"
            break
        url = base.rstrip("/") + "/" + w.url_path
        try:
            if _get(url, w, part, judge=(i == 0 and len(w.bases) > 1), deadline=deadline):
                os.replace(part, os.path.join(folder, w.name))
                return "fetched"
            why = "not the bytes the lists name"
        except _Slow:
            why = "slow"
        except (urllib.error.URLError, OSError, ValueError) as e:
            why = type(e).__name__
        try:
            os.remove(part)
        except OSError:
            pass
    return why


def _run(cmd: list[str]) -> str:
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise RuntimeError(f"{cmd[0]} {cmd[1]}: {done.stderr.strip()[:300]}")
    return done.stdout


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) < 2:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    folder, packages = os.path.abspath(args[0]), args[1:]
    if any(p.startswith("-") for p in packages):
        print("apt_fetch: packages only, no options", file=sys.stderr)
        return 2
    os.makedirs(os.path.join(folder, "partial"), exist_ok=True)
    start = time.monotonic()
    try:
        wanted = parse_uris(_run([
            "apt-get", "install", "--print-uris", "-qq", "--no-install-recommends",
            "-o", f"Dir::Cache::Archives={folder}", "--", *packages]))
        names = sorted({w.name.split("_", 1)[0] for w in wanted})
        known = records(_run(["apt-cache", "show", "--", *names])) if names else {}
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        print(f"apt_fetch: apt did not say what it needs ({e}); apt fetches it all")
        return 0
    todo = plan(wanted, known)
    deadline = start + DEADLINE
    results: dict[str, int] = {}
    with concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
        for w, result in zip(todo, pool.map(lambda w: fetch_one(w, folder, deadline), todo),
                             strict=True):
            results[result] = results.get(result, 0) + 1
            if result != "fetched":
                print(f"apt_fetch: {w.name}: {result} - left to apt")
    fetched = results.get("fetched", 0)
    mb = sum(w.size for w in todo) / 1e6
    print(f"apt_fetch: {fetched} of {len(wanted)} files ({mb:.1f} MB) checked and "
          f"in place in {time.monotonic() - start:.0f} s; "
          f"{len(wanted) - fetched} left to apt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
