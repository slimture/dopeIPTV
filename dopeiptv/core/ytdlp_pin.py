"""The yt-dlp release the app's own copy may be (core/ytdlp.py): its tag and
each release file's SHA-256, written by tools/ytdlp_pin.py once yt-dlp's
own PGP signature over the release's SHA2-256SUMS checked out against
yt-dlp's signing key (packaging/yt-dlp-signing-key.asc, fingerprint
``KEY``). Being a module of the app's, it is covered by the app's signed
release and build manifest: the app trusts no checksum list it downloads.

``PIN`` None: this build names no release, and the app downloads none -
the system's yt-dlp is used where there is one.
"""
from __future__ import annotations

from typing import Any

KEY = "AC0CBBE6848D6A873464AF4E57CF65933B5A7581"

PIN: dict[str, Any] | None = None
