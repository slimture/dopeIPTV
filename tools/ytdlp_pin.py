#!/usr/bin/env python3
"""Pin the yt-dlp release the app's own copy may be: dopeiptv/core/ytdlp_pin.py.

    python3 tools/ytdlp_pin.py [--tag TAG] [--out PATH] [--or-none]

The newest release's tag (or TAG), its SHA2-256SUMS and SHA2-256SUMS.sig are
fetched from github.com/yt-dlp/yt-dlp over https; the signature is checked
with gpgv against yt-dlp's signing key in packaging/yt-dlp-signing-key.asc,
which must be the key whose fingerprint core/ytdlp_pin.py names - nothing
else is trusted. Only then are the SHA-256s of the files the app downloads
(core/ytdlp.asset_name, every platform) written into the module, which is
part of the app's signed release: the app never trusts a checksum list it
downloads itself.

release.yml runs this in a job of its own and hands the module to every
build. With --or-none a failure writes PIN = None (the build then downloads
no yt-dlp; the system's is used) and says why, instead of stopping the build.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dopeiptv.core import ytdlp, ytdlp_pin  # noqa: E402

KEY_FILE = ROOT / "packaging" / "yt-dlp-signing-key.asc"
OUT = ROOT / "dopeiptv" / "core" / "ytdlp_pin.py"
LATEST = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
DOWNLOAD = "https://github.com/yt-dlp/yt-dlp/releases/download/{tag}/{name}"
#: Every release file the app may download (core/ytdlp.asset_name).
ASSETS = ("yt-dlp_linux", "yt-dlp_linux_aarch64", "yt-dlp_linux_armv7l",
          "yt-dlp_musllinux", "yt-dlp_musllinux_aarch64", "yt-dlp_macos",
          "yt-dlp.exe", "yt-dlp_arm64.exe", "yt-dlp_x86.exe")
#: Without these the pin is no use on the platforms the app ships for.
NEEDED = ("yt-dlp_linux", "yt-dlp_linux_aarch64", "yt-dlp_macos", "yt-dlp.exe")
_MAX = 1 << 20


class PinError(Exception):
    pass


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any,
                         newurl: str) -> Any:
        if not newurl.lower().startswith("https://"):
            raise PinError(f"a redirect off https: {newurl.split('?')[0]}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url: str, limit: int = _MAX) -> bytes:
    if not url.startswith("https://"):
        raise PinError(f"not https: {url}")
    opener = urllib.request.build_opener(_HttpsOnly)
    req = urllib.request.Request(url, headers={"User-Agent": "dopeIPTV-build"})
    with opener.open(req, timeout=60) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise PinError(f"larger than {limit} bytes: {url}")
    return data


def latest_tag() -> str:
    tag = str(json.loads(fetch(LATEST)).get("tag_name") or "")
    if not ytdlp._TAG_RE.fullmatch(tag):
        raise PinError(f"no release tag in {LATEST}")
    return tag


def signed_by(status: str, fingerprint: str) -> bool:
    """Whether gpgv's --status-fd output says a good signature by the key
    *fingerprint* (its primary key, or the signing subkey's own line)."""
    want = fingerprint.upper()
    for line in status.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "[GNUPG:]" and parts[1] == "VALIDSIG":
            if parts[2].upper() == want or parts[-1].upper() == want:
                return True
    return False


def verify(sums: bytes, sig: bytes, key_asc: bytes, fingerprint: str) -> None:
    """*sig* is yt-dlp's good signature over *sums* by *fingerprint* - else
    PinError. gpgv with a keyring holding that key alone."""
    with tempfile.TemporaryDirectory() as d:
        home = Path(d) / "home"
        home.mkdir(mode=0o700)
        ring = Path(d) / "yt-dlp.gpg"
        env = dict(os.environ, GNUPGHOME=str(home))
        made = subprocess.run(["gpg", "--batch", "--yes", "--dearmor", "-o", str(ring)],
                              input=key_asc, capture_output=True, env=env, timeout=60)
        if made.returncode != 0 or not ring.exists():
            raise PinError("the signing key could not be read")
        listed = subprocess.run(["gpg", "--batch", "--with-colons", "--show-keys", str(ring)],
                                capture_output=True, text=True, env=env, timeout=60)
        fprs = [ln.split(":")[9] for ln in listed.stdout.splitlines() if ln.startswith("fpr:")]
        if not fprs or fprs[0].upper() != fingerprint.upper():
            raise PinError("packaging/yt-dlp-signing-key.asc is not the key core/ytdlp_pin.py names")
        (Path(d) / "SUMS").write_bytes(sums)
        (Path(d) / "SUMS.sig").write_bytes(sig)
        out = subprocess.run(["gpgv", "--status-fd", "1", "--keyring", str(ring),
                              str(Path(d) / "SUMS.sig"), str(Path(d) / "SUMS")],
                             capture_output=True, text=True, env=env, timeout=60)
        if out.returncode != 0 or not signed_by(out.stdout, fingerprint):
            raise PinError("SHA2-256SUMS is not signed by yt-dlp's key")


def module_text(old: str, pin: dict[str, Any] | None) -> str:
    """The module's source with ``PIN`` set to *pin*; the rest as it was."""
    head, sep, _rest = old.partition("PIN: dict[str, Any] | None = ")
    if not sep:
        raise PinError("core/ytdlp_pin.py has no PIN to set")
    if pin is None:
        return head + sep + "None\n"
    sums = "".join(f"        {json.dumps(k)}: {json.dumps(v)},\n" for k, v in sorted(pin["sums"].items()))
    return (head + sep + "{\n"
            f"    \"tag\": {json.dumps(pin['tag'])},\n"
            "    \"sums\": {\n" + sums + "    },\n"
            "}\n")


def make_pin(tag: str, sums_text: str) -> dict[str, Any]:
    sums = ytdlp.parse_sums(sums_text)
    chosen = {a: sums[a] for a in ASSETS if a in sums}
    missing = [a for a in NEEDED if a not in chosen]
    if missing:
        raise PinError(f"{tag} has no {', '.join(missing)}")
    return {"tag": tag, "sums": chosen}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tag")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--or-none", action="store_true")
    a = ap.parse_args(argv)
    out = Path(a.out)
    old = OUT.read_text(encoding="utf-8")
    try:
        tag = a.tag or latest_tag()
        if not ytdlp._TAG_RE.fullmatch(tag):
            raise PinError(f"not a tag: {tag!r}")
        sums = fetch(DOWNLOAD.format(tag=tag, name=ytdlp.SUMS))
        sig = fetch(DOWNLOAD.format(tag=tag, name=ytdlp.SUMS + ".sig"))
        verify(sums, sig, KEY_FILE.read_bytes(), ytdlp_pin.KEY)
        pin = make_pin(tag, sums.decode("utf-8", "replace"))
    except Exception as e:      # noqa: BLE001 - every failure is said the same way
        if not a.or_none:
            print(f"yt-dlp pin: {e}", file=sys.stderr)
            return 1
        print(f"::warning::yt-dlp is not pinned in this build ({e}); it downloads no yt-dlp")
        out.write_text(module_text(old, None), encoding="utf-8")
        return 0
    out.write_text(module_text(old, pin), encoding="utf-8")
    print(f"yt-dlp {pin['tag']} pinned: {len(pin['sums'])} files, signature by {ytdlp_pin.KEY}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
