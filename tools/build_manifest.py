#!/usr/bin/env python3
"""Put the signed list of a build's own files into the build.

    DOPEIPTV_BUILD_KEY=<base64 secret> python3 tools/build_manifest.py frozen dist/dopeiptv/dopeiptv
    DOPEIPTV_BUILD_KEY=<base64 secret> python3 tools/build_manifest.py tree dopeiptv

The app tells an official build from a modified one by this list
(dopeiptv/core/build_check.py): each file's SHA-256 and the version,
signed with dopeIPTV's release key - the GitHub Actions secret
DOPEIPTV_UPDATE_KEY that signs each release's SHA256SUMS
(tools/sign_sums.py).

frozen APP  A PyInstaller bundle, APP its executable. The bundle is asked
            for its own files (APP --build-files: its data files, and its
            modules as its archive holds them - the .py files are not in
            a bundle), the list is signed and put beside the package inside
            the bundle, and the bundle is asked again: it must now call
            itself official, or this fails.
tree PKG    A package pip installs (the Flatpak): its .py files and the
            data files pyproject.toml ships (--pyproject), listed and signed
            into PKG before the package is built.

Without the key nothing is written: a fork's build calls itself modified,
which it is. --require-key makes that an error (a release). The key's
public half must be one the app carries (dopeiptv/core/update_keys.py),
or nothing is signed. Never in git: .gitignore names both files.

The key is DOPEIPTV_BUILD_KEY - a key that signs file lists and nothing
else, whose public half is in update_keys.BUILD_KEYS - and never the update
key: a build job runs PyInstaller, pip's packages and other people's tools
before it signs, so what it holds must not be able to sign an update. The
key is read once and taken out of the environment (DOPEIPTV_UPDATE_KEY
too, should a job be handed it) before the bundle is started: the bundle
holds every third-party package the app ships, and none of them is handed
a signing key.
"""
from __future__ import annotations

import argparse
import base64
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

#: Where the signing key comes from.
KEY_VAR = "DOPEIPTV_BUILD_KEY"
#: Taken out of the environment however it got there: no program this tool
#: starts inherits a signing key.
KEY_VARS = (KEY_VAR, "DOPEIPTV_UPDATE_KEY")


def _take_secret() -> str:
    """The build key, and every signing key out of the environment for
    good: nothing this process starts after it inherits one."""
    found = os.environ.pop(KEY_VAR, "").strip()
    for var in KEY_VARS:
        os.environ.pop(var, None)
    return found


# Before the package is imported: importing it in a checkout runs git
# (dopeiptv/__init__.py), and git is whatever the PATH names.
_GIVEN = _take_secret()

from dopeiptv.core import build_check, ed25519, update_keys  # noqa: E402

#: The keys a build's file list may be signed with here: build keys alone.
UPDATE_KEYS = update_keys.BUILD_KEYS


def _seed(require: bool, keys: tuple[str, ...]) -> bytes | None:
    secret = _take_secret() or _GIVEN
    if not secret:
        msg = "DOPEIPTV_BUILD_KEY is empty: no build manifest - this build will call itself modified"
        if require:
            raise SystemExit(msg)
        print(f"::warning::{msg}")
        return None
    seed = base64.b64decode(secret, validate=True)
    public = base64.b64encode(ed25519.public_key(seed)).decode()
    if public not in keys:
        raise SystemExit("the secret's public half is not a build key the app carries "
                         "(BUILD_KEYS in dopeiptv/core/update_keys.py): nothing signed")
    return seed


def _home(pkg_dir: str) -> str:
    """Where the list's files go: the package's folder - in a macOS app its
    twin under Contents/Resources, since only code may lie in Frameworks
    (codesign); a link there points at each, as PyInstaller links data."""
    real = os.path.realpath(pkg_dir)
    head, sep, tail = (real + os.sep).partition(os.sep + os.path.join("Contents", "Frameworks") + os.sep)
    if not sep:
        return real
    return os.path.join(head, "Contents", "Resources", tail.rstrip(os.sep))


def _write(pkg_dir: str, data: bytes, sig: str) -> None:
    real, home = os.path.realpath(pkg_dir), _home(pkg_dir)
    os.makedirs(home, exist_ok=True)
    for name, body in ((build_check.MANIFEST, data), (build_check.SIGNATURE, sig.encode("ascii"))):
        with open(os.path.join(home, name), "wb") as fh:
            fh.write(body)
        if home != real:
            link = os.path.join(real, name)
            if os.path.lexists(link):
                os.remove(link)
            os.symlink(os.path.relpath(os.path.join(home, name), real), link)


def _ask(app: str) -> dict:
    """What the bundle says of itself (``--build-files``)."""
    fd, out = tempfile.mkstemp(prefix="dopeiptv-build-files-", suffix=".json")
    os.close(fd)
    try:
        # Absolute, in the system's own form: CreateProcess on Windows.
        # Never with a signing key in its environment (KEY_VARS).
        env = {k: v for k, v in os.environ.items() if k not in KEY_VARS}
        proc = subprocess.run([os.path.abspath(app), "--build-files", out], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, errors="replace", env=env,
                              timeout=300, check=False)
        try:
            with open(out, encoding="utf-8") as fh:
                got = json.load(fh)
        except (OSError, ValueError):
            got = {}
        if proc.returncode != 0 or "files" not in got:
            raise SystemExit(f"{app} --build-files failed (exit {proc.returncode}): "
                             f"{got.get('error', '')} {proc.stdout[-400:]} {proc.stderr[-800:]}")
        return got
    finally:
        try:
            os.remove(out)
        except OSError:
            pass


def frozen(app: str, require: bool, keys: tuple[str, ...] = UPDATE_KEYS) -> int:
    got = _ask(app)
    if not got.get("frozen"):
        raise SystemExit(f"{app} is not a frozen build")
    pkg, files, version = str(got["package"]), dict(got["files"]), str(got["version"])
    print(f"{app}: {len(files)} files of dopeIPTV {version} in {pkg}")
    seed = _seed(require, keys)
    if seed is None:
        return 0
    data = build_check.make(files, version, build_check.FROZEN)
    _write(pkg, data, build_check.sign(seed, data))
    again = _ask(app)
    v = again.get("verdict") or {}
    if v.get("kind") != build_check.OFFICIAL:
        raise SystemExit(f"the bundle does not call itself official with its list: {v.get('summary')}")
    print(f"signed build manifest in {pkg}: the bundle says {v.get('summary')}")
    return 0


def _package_data(pyproject: str) -> list[str]:
    import tomllib
    with open(pyproject, "rb") as fh:
        conf = tomllib.load(fh)
    return list(((conf.get("tool") or {}).get("setuptools") or {}).get("package-data", {}).get("dopeiptv", []))


def tree_files(pkg_dir: str, pyproject: str) -> dict[str, str]:
    """What pip installs of *pkg_dir*: the .py files of its packages (a
    folder with __init__.py, up to the top) and the data files the
    package-data globs name."""
    found = build_check.disk_files(pkg_dir)
    root = os.path.realpath(pkg_dir)
    data: set[str] = set()
    for pattern in _package_data(pyproject):
        for path in glob.glob(os.path.join(root, pattern), recursive=True):
            if os.path.isfile(path):
                data.add("dopeiptv/" + os.path.relpath(path, root).replace(os.sep, "/"))

    def in_package(rel: str) -> bool:
        parts = rel.split("/")[1:-1]
        return all(os.path.isfile(os.path.join(root, *parts[:i], "__init__.py"))
                   for i in range(len(parts) + 1))

    return {k: v for k, v in found.items()
            if (k.endswith(".py") and in_package(k)) or k in data}


def tree(pkg_dir: str, pyproject: str, require: bool, keys: tuple[str, ...] = UPDATE_KEYS) -> int:
    with open(os.path.join(pkg_dir, "__init__.py"), encoding="utf-8") as fh:
        m = re.search(r'^__version__ = "([^"]+)"', fh.read(), re.MULTILINE)
    if m is None:
        raise SystemExit(f"{pkg_dir}/__init__.py names no __version__")
    version = m.group(1)
    files = tree_files(pkg_dir, pyproject)
    print(f"{pkg_dir}: {len(files)} files of dopeIPTV {version}")
    seed = _seed(require, keys)
    if seed is None:
        return 0
    data = build_check.make(files, version, build_check.TREE)
    sig = build_check.sign(seed, data)
    if not build_check.signed_by(data, sig, keys):
        raise SystemExit("the signature does not check: nothing written")
    _write(pkg_dir, data, sig)
    print(f"signed build manifest in {pkg_dir}")
    return 0


def main(argv: list[str] | None = None, keys: tuple[str, ...] = UPDATE_KEYS) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("mode", choices=("frozen", "tree"))
    p.add_argument("path", help="frozen: the bundle's executable; tree: the package folder")
    p.add_argument("--pyproject", default=os.path.join(ROOT, "pyproject.toml"))
    p.add_argument("--require-key", action="store_true",
                   help="fail without DOPEIPTV_BUILD_KEY (a release)")
    a = p.parse_args(argv)
    if a.mode == "frozen":
        return frozen(a.path, a.require_key, keys)
    return tree(a.path, a.pyproject, a.require_key, keys)


if __name__ == "__main__":
    sys.exit(main())
