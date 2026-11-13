#!/usr/bin/env python3
"""Sign a release's checksum list with dopeIPTV's release key.

    DOPEIPTV_UPDATE_KEY=<base64 secret> python3 tools/sign_sums.py SHA256SUMS

Writes SHA256SUMS.sig beside it: the Ed25519 signature of the file's bytes,
in base64 on one line. release.yml and nightly.yml run it with the GitHub
Actions secret. It stops unless the key's public half is one the app
carries (dopeiptv/core/update_keys.py) - a release the app would refuse to
install must not go out.
"""
import base64
import importlib.util
import os
import sys


def _load(name: str):
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(here, "..", "dopeiptv", "core", f"{name}.py"))
    if spec is None or spec.loader is None:
        sys.exit(f"dopeiptv/core/{name}.py not found")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: tools/sign_sums.py SHA256SUMS", file=sys.stderr)
        return 2
    secret = os.environ.get("DOPEIPTV_UPDATE_KEY", "").strip()
    if not secret:
        print("DOPEIPTV_UPDATE_KEY is empty: nothing signed", file=sys.stderr)
        return 1
    ed25519, keys = _load("ed25519"), _load("update_keys")
    seed = base64.b64decode(secret, validate=True)
    public = base64.b64encode(ed25519.public_key(seed)).decode()
    if public not in keys.UPDATE_KEYS:
        print("the secret's public half is not one the app carries "
              "(dopeiptv/core/update_keys.py): nothing signed", file=sys.stderr)
        return 1
    path = sys.argv[1]
    with open(path, "rb") as fh:
        data = fh.read()
    sig = ed25519.sign(seed, data)
    assert ed25519.verify(base64.b64decode(public), data, sig)
    with open(path + ".sig", "w") as fh:
        fh.write(base64.b64encode(sig).decode() + "\n")
    print(f"signed {path} ({len(data.splitlines())} files) with key {public[:12]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
