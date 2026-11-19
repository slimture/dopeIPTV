"""Is a release's checksum list signed with dopeIPTV's release key?

    python3 -I donate/release_sums.py SHA256SUMS SHA256SUMS.sig

Exit 0 when SHA256SUMS.sig (base64, one line) is the Ed25519 signature of
SHA256SUMS's bytes by one of KEYS and every line of the list is a checksum
and a file name, as `sha256sum` writes them; 1 otherwise, saying why on
stderr. sync-releases.sh mirrors a release's installers only then, and only
files on that list with its checksum - so someone who can publish a release
on GitHub but does not hold the key cannot change what dopeiptv.org hands
out, as the app already refuses such an update (its core/selfupdate.py,
signed_asset).

KEYS is the app's dopeiptv/core/update_keys.py, UPDATE_KEYS: the public
halves only. A change of key goes in both, the new key added beside the old
until every release is signed with it.
"""
from __future__ import annotations

import base64
import binascii
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ed25519  # noqa: E402

KEYS: tuple[str, ...] = (
    "IP6gbDXErt8o1kctkDQsL2gRnXSs7RU92F3yjmIuEdg=",
)
_LINE = re.compile(r"[0-9a-f]{64} [ *][^\s/][^/\n]*")


def why_not(sums: bytes, sig: bytes, keys: tuple[str, ...] = KEYS) -> str:
    """"" when *sums* is signed by one of *keys* and well formed, else why not."""
    if not sums.strip():
        return "the release has no SHA256SUMS"
    try:
        raw = base64.b64decode(sig.strip(), validate=True)
    except (binascii.Error, ValueError):
        return "SHA256SUMS.sig is not base64"
    if not any(ed25519.verify(base64.b64decode(k), sums, raw) for k in keys):
        return "SHA256SUMS is not signed with dopeIPTV's release key"
    try:
        lines = sums.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return "SHA256SUMS is not text"
    bad = [ln for ln in lines if ln and not _LINE.fullmatch(ln)]
    if bad:
        return f"SHA256SUMS has a line that is not a checksum and a file name: {bad[0][:80]!r}"
    return ""


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: release_sums.py SHA256SUMS SHA256SUMS.sig", file=sys.stderr)
        return 2
    try:
        with open(argv[1], "rb") as fh:
            sums = fh.read()
        with open(argv[2], "rb") as fh:
            sig = fh.read()
    except OSError as e:
        print(f"cannot read {e.filename}", file=sys.stderr)
        return 1
    why = why_not(sums, sig)
    if why:
        print(why, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
