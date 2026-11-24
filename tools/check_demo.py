"""Do the demo's streams still play? Asks every address of
dopeiptv/providers/demo_streams.py before a release (docs/MAINTAINING.md).

    python3 tools/check_demo.py

For each stream: its playlist (an HLS master or media playlist), the first
variant a master names, and the first bytes of that variant's first
segment - each over https with the certificate checked, redirects followed
only to https, every answer read no further than a playlist or a piece of
a segment needs. A stream that does not answer as an HLS stream is named
with why; the exit status is 1 then, 0 when every one answered.

Standard library only: it reads the table by its path, without the app or
its dependencies, so it runs on any computer with Python 3.11.
"""
from __future__ import annotations

import importlib.util
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlsplit

TABLE = Path(__file__).resolve().parent.parent / "dopeiptv" / "providers" / "demo_streams.py"
PLAYLIST_MAX = 2 * 1024 * 1024
SEGMENT_PEEK = 64 * 1024
TIMEOUT = 20
AGENT = "dopeIPTV-check-demo"


def table():
    spec = importlib.util.spec_from_file_location("demo_streams", TABLE)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot read {TABLE}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    """A redirect followed only to https."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise urllib.error.URLError(f"redirect to {urlsplit(newurl).scheme or 'no'} scheme refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener(cafile: str | None = None) -> urllib.request.OpenerDirector:
    # The system's authorities (or *cafile*'s: the tests'), the certificate
    # and its name checked.
    context = ssl.create_default_context(cafile=cafile)
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), _HttpsOnly())


OPENER = _opener()


def fetch(url: str, most: int, peek: bool = False) -> tuple[str, bytes]:
    """(final address, body) - at most *most* bytes; past that an error
    unless *peek* (a segment's first bytes are enough)."""
    if urlsplit(url).scheme != "https":
        raise ValueError(f"not https: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    if peek:
        req.add_header("Range", f"bytes=0-{most - 1}")
    with OPENER.open(req, timeout=TIMEOUT) as r:
        body = r.read(most + 1)
        if len(body) > most and not peek:
            raise ValueError(f"more than {most // 1024} KiB: no playlist")
        return r.geturl(), body[:most]


def _lines(body: bytes) -> list[str]:
    return [ln.strip() for ln in body.decode("utf-8", "replace").lstrip("﻿").splitlines()]


def _first_uri(lines: list[str], after: str) -> str | None:
    """The first address that follows a line starting with *after*."""
    seen = False
    for ln in lines:
        if ln.startswith(after):
            seen = True
        elif seen and ln and not ln.startswith("#"):
            return ln
    return None


def check(url: str) -> str:
    """'' when the stream answers as an HLS stream, else why not."""
    try:
        where, body = fetch(url, PLAYLIST_MAX)
        lines = _lines(body)
        if not lines or lines[0] != "#EXTM3U":
            return "no HLS playlist"
        if any(ln.startswith("#EXT-X-STREAM-INF") for ln in lines):
            variant = _first_uri(lines, "#EXT-X-STREAM-INF")
            if variant is None:
                return "a master playlist with no variant"
            where, body = fetch(urljoin(where, variant), PLAYLIST_MAX)
            lines = _lines(body)
            if not lines or lines[0] != "#EXTM3U":
                return "its first variant is no playlist"
        segment = _first_uri(lines, "#EXTINF")
        if segment is None:
            return "a playlist with no segment"
        _where, piece = fetch(urljoin(where, segment), SEGMENT_PEEK, peek=True)
        if not piece:
            return "its first segment is empty"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"
    except ssl.SSLError as e:
        return f"certificate: {e}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return str(getattr(e, "reason", None) or e)
    return ""


def main() -> int:
    streams = table().STREAMS
    bad = 0
    for key, url in streams.items():
        why = check(url)
        host = urlsplit(url).hostname
        print(f"{'ok  ' if not why else 'FAIL'} {key:<8} {host}" + (f" - {why}" if why else ""))
        bad += bool(why)
    print(f"{len(streams) - bad} of {len(streams)} streams answer.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
