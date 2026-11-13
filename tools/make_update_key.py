#!/usr/bin/env python3
"""Make the key pair that signs dopeIPTV's releases. Run it once, on your
own computer - no other software needed:

    python3 tools/make_update_key.py

It prints two lines:

  PRIVATE - goes into GitHub as the Actions secret DOPEIPTV_UPDATE_KEY
            (Settings -> Secrets and variables -> Actions -> New repository
            secret), and into your password manager as the only backup.
            Never anywhere else: whoever has it can sign an update.
  PUBLIC  - goes into the app (dopeiptv/core/update_keys.py, UPDATE_KEYS);
            it is no secret.

Lose the private key, and the apps out there accept no further update from
inside the app until a new version is installed by hand - so keep the backup.
"""
import base64
import importlib.util
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "ed25519", os.path.join(_here, "..", "dopeiptv", "core", "ed25519.py"))
if _spec is None or _spec.loader is None:
    sys.exit("dopeiptv/core/ed25519.py not found - run this from the dopeIPTV clone")
ed25519 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ed25519)

seed = os.urandom(32)
public = ed25519.public_key(seed)
assert ed25519.verify(public, b"dopeIPTV", ed25519.sign(seed, b"dopeIPTV"))

print()
print("PRIVATE (GitHub secret DOPEIPTV_UPDATE_KEY + your password manager, nowhere else):")
print("  " + base64.b64encode(seed).decode())
print()
print("PUBLIC (for the app - safe to share):")
print("  " + base64.b64encode(public).decode())
print()
