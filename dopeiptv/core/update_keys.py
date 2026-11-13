"""The public halves of dopeIPTV's release signing keys (base64, 32 bytes).

The app installs an update only when the release's checksum list
(SHA256SUMS) carries a signature by one of these (core/selfupdate.py,
signed_asset). The private half lives only in the GitHub Actions secret
DOPEIPTV_UPDATE_KEY, where release.yml and nightly.yml sign each release
with it (tools/sign_sums.py) - so a site or mirror taken over cannot serve
an update the app accepts. Made once with tools/make_update_key.py.

A second key can be added here ahead of a change of key; the old one goes
once every copy out there carries the new one. No imports: tools/ loads
this file on its own.
"""
from __future__ import annotations

UPDATE_KEYS: tuple[str, ...] = (
    "IP6gbDXErt8o1kctkDQsL2gRnXSs7RU92F3yjmIuEdg=",
)
