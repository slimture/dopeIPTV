#!/usr/bin/env python3
"""Fetch a binary the release build needs - only the exact bytes the repo names.

    python tools/build_inputs.py fetch libmpv-windows-x86_64 libmpv/mpv-2.dll
    python tools/build_inputs.py verify-git vulkan-loader vulkan-loader
    python tools/build_inputs.py show

Everything release.yml downloads by URL and then runs or ships - the libmpv
build for Windows, the AppImage runtime and appimagetool, micromamba - is
listed in packaging/build-inputs.json with its SHA-256. ``fetch`` downloads
it and stops unless the bytes are the ones listed, so a changed upstream
file, a taken-over account or a tampered download cannot reach a package.

An input may list several sources. They are tried in order, and the first
that yields the listed bytes is used:

- upstream's own file (a rolling "continuous" tag, or a build its
  publisher keeps for 30 days);
- a release of dopeIPTV's own that shipped the same bytes - those stay for
  good, so a pin does not rot when upstream moves on. A released AppImage
  starts with the runtime (``take: appimage-runtime``; appimagetool writes
  16 bytes of digest into it, zeroed again here), and a released Windows
  zip holds the DLL (``take: zip:<member>``).

``verify-git`` checks that a source tree cloned by tag is the commit the
repo names for that tag (a tag can be moved; a commit cannot).

Standard library only, plus the curl and 7z programs every runner has.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PINS = os.path.join(ROOT, "packaging", "build-inputs.json")
#: The head of an AppImage that surely holds its runtime (under 1 MB today).
RUNTIME_HEAD = 4 << 20
#: What appimagetool fills in of the runtime it prepends.
RUNTIME_FILLED = (".digest_md5",)


class Refused(Exception):
    """A source that did not yield the listed bytes."""


def load(path: str = PINS) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: str, head: int = 0) -> None:
    """*url* into *dest* with curl - https only, also after a redirect;
    retried, since a release build must not fail on one flaky transfer.
    *head*: only that many bytes from the start."""
    if not url.startswith("https://"):
        raise Refused(f"not an https address: {url}")
    cmd = ["curl", "-fsSL", "--proto", "=https", "--proto-redir", "=https",
           "--retry", "8", "--retry-delay", "15", "--retry-all-errors",
           "--connect-timeout", "30", "-o", dest]
    if head:
        cmd += ["-r", f"0-{head - 1}"]
    try:
        proc = subprocess.run([*cmd, url], stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, errors="replace", check=False)
    except OSError as e:
        raise Refused(f"curl could not be started: {e}") from e
    if proc.returncode != 0:
        raise Refused(f"download failed (curl {proc.returncode}): {(proc.stderr or '').strip()[-200:]}")


def runtime_of(head: bytes) -> bytes:
    """The type2 runtime an AppImage starts with - the ELF file up to the
    end of its section table - as upstream published it: with the sections
    appimagetool fills in zeroed again."""
    if head[:4] != b"\x7fELF" or head[4] != 2 or head[5] != 1:
        raise Refused("not a 64-bit little-endian ELF file")
    shoff, = struct.unpack_from("<Q", head, 0x28)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", head, 0x3A)
    end = shoff + shentsize * shnum
    if not (0 < shentsize and shstrndx < shnum and end <= len(head)):
        raise Refused("the ELF section table does not fit the downloaded head")
    rows = [struct.unpack_from("<IIQQQQ", head, shoff + i * shentsize) for i in range(shnum)]
    str_off, str_size = rows[shstrndx][4], rows[shstrndx][5]
    names = head[str_off:str_off + str_size]
    out = bytearray(head[:end])
    for name, _type, _flags, _addr, off, size in rows:
        stop = names.find(b"\0", name)
        if names[name:stop].decode("ascii", "replace") in RUNTIME_FILLED and off + size <= end:
            out[off:off + size] = bytes(size)
    return bytes(out)


def _take(kind: str, got: str, work: str) -> str:
    """The file a source stands for, from what was downloaded to *got*."""
    if not kind:
        return got
    out = os.path.join(work, "taken")
    if kind == "appimage-runtime":
        with open(got, "rb") as fh:
            data = runtime_of(fh.read(RUNTIME_HEAD))
        with open(out, "wb") as fh:
            fh.write(data)
        return out
    how, _, member = kind.partition(":")
    if how == "zip" and member:
        try:
            with zipfile.ZipFile(got) as z, z.open(member) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
        except (KeyError, zipfile.BadZipFile, OSError) as e:
            raise Refused(f"{member} could not be read from the zip: {e}") from e
        return out
    if how == "7z" and member:
        folder = os.path.join(work, "x")
        try:
            proc = subprocess.run(["7z", "x", "-y", f"-o{folder}", got, member],
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                  errors="replace", check=False)
        except OSError as e:
            raise Refused(f"7z could not be started: {e}") from e
        path = os.path.join(folder, *member.split("/"))
        if proc.returncode != 0 or not os.path.isfile(path):
            raise Refused(f"{member} could not be read from the archive (7z {proc.returncode})")
        return path
    raise Refused(f"unknown way to take a file: {kind!r}")


def from_source(source: dict, want: str, work: str) -> str:
    """The path of a file with SHA-256 *want* made from *source*; Refused
    when it yields anything else."""
    url, kind = str(source.get("url") or ""), str(source.get("take") or "")
    got = os.path.join(work, "download")
    download(url, got, head=RUNTIME_HEAD if kind == "appimage-runtime" else 0)
    listed = str(source.get("sha256") or "")
    if listed and sha256_file(got) != listed:
        raise Refused(f"the download's SHA-256 is {sha256_file(got)}, not the listed {listed}")
    path = _take(kind, got, work)
    have = sha256_file(path)
    if have != want:
        raise Refused(f"SHA-256 {have}, not the listed {want}")
    return path


def _place(path: str, dest: str) -> None:
    """*path* (in the temp folder) to *dest* - on another disk too, where
    a rename cannot go."""
    try:
        os.replace(path, dest)
    except OSError:
        shutil.copyfile(path, dest)


def fetch(name: str, dest: str, pins: dict | None = None) -> str:
    """Write the input *name* to *dest*; returns the address it came from.
    Raises Refused when no source yields the listed bytes."""
    pins = load() if pins is None else pins
    entry = (pins.get("inputs") or {}).get(name)
    if not entry:
        raise Refused(f"{name} is not in packaging/build-inputs.json")
    want = str(entry.get("sha256") or "")
    if len(want) != 64:
        raise Refused(f"{name} lists no SHA-256")
    said = []
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    for n, source in enumerate(entry.get("sources") or []):
        work = tempfile.mkdtemp(prefix="build-input-")
        try:
            path = from_source(source, want, work)
            _place(path, dest)
            if n:
                print(f"::notice::{name}: taken from source {n + 1} ({source.get('url')}); "
                      f"the ones before it: {'; '.join(said)}")
            return str(source.get("url"))
        except Refused as e:
            said.append(f"{source.get('url')}: {e}")
        finally:
            shutil.rmtree(work, ignore_errors=True)
    raise Refused(f"{name}: no source gave the listed bytes - " + " | ".join(said or ["no source listed"]))


def verify_git(name: str, folder: str, tag: str = "", pins: dict | None = None,
               unknown_ok: bool = False) -> str:
    """The checked-out commit of *folder*, which must be the one listed for
    *name* (and *tag*, where an entry lists several). An unlisted tag is
    refused, or with *unknown_ok* only said."""
    pins = load() if pins is None else pins
    entry = (pins.get("git") or {}).get(name)
    if not entry:
        raise Refused(f"{name} is not in packaging/build-inputs.json")
    proc = subprocess.run(["git", "-C", folder, "rev-parse", "HEAD"], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, errors="replace", check=False)
    head = (proc.stdout or "").strip()
    if proc.returncode != 0 or len(head) < 40:
        raise Refused(f"{folder} is not a git checkout: {(proc.stderr or '').strip()[-200:]}")
    tags = dict(entry.get("tags") or {})
    if entry.get("tag"):
        tags[str(entry["tag"])] = str(entry.get("commit") or "")
    tag = tag or str(entry.get("tag") or "")
    want = tags.get(tag)
    if not want:
        msg = f"{name} {tag or '(no tag)'} is commit {head}, which packaging/build-inputs.json does not list"
        if unknown_ok:
            print(f"::warning::{msg} - add it there once looked at")
            return head
        raise Refused(msg)
    if head != want:
        raise Refused(f"{name} {tag} is commit {head}, not the listed {want}: the tag was moved")
    return head


def show(pins: dict | None = None) -> str:
    pins = load() if pins is None else pins
    lines = []
    for name, entry in sorted((pins.get("inputs") or {}).items()):
        lines.append(f"{name}  sha256:{entry.get('sha256')}")
        lines += [f"    {s.get('url')}" + (f"  [{s['take']}]" if s.get("take") else "")
                  for s in entry.get("sources") or []]
    for name, entry in sorted((pins.get("git") or {}).items()):
        tags = dict(entry.get("tags") or {})
        if entry.get("tag"):
            tags[str(entry["tag"])] = str(entry.get("commit") or "")
        lines += [f"{name}  {entry.get('url')}"] + [f"    {t}  {c}" for t, c in sorted(tags.items())]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download an input and check it is the listed bytes")
    f.add_argument("name")
    f.add_argument("dest")
    g = sub.add_parser("verify-git", help="check a clone is the commit listed for its tag")
    g.add_argument("name")
    g.add_argument("folder")
    g.add_argument("--tag", default="")
    g.add_argument("--unknown-ok", action="store_true",
                   help="a tag the list does not know is said, not refused")
    sub.add_parser("show", help="what is listed")
    a = p.parse_args(argv)
    try:
        if a.cmd == "fetch":
            where = fetch(a.name, a.dest)
            print(f"{a.name}: {a.dest} is the listed file (sha256 {sha256_file(a.dest)}), from {where}")
        elif a.cmd == "verify-git":
            head = verify_git(a.name, a.folder, a.tag, unknown_ok=a.unknown_ok)
            print(f"{a.name}: {a.folder} is at {head}")
        else:
            print(show())
    except Refused as e:
        print(f"::error::{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
