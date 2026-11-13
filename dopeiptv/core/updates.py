"""Check for a newer dopeIPTV release. No Qt - safe on a worker thread;
the UI marshals the result back itself.

A release is asked of dopeiptv.org first (``releases.json``, written once
every file of the release is mirrored there), and its files are fetched
through the site's download link, under a user agent naming the app and
its version: the site counts updates made from the app apart from
downloads in a browser. GitHub answers when the site does not.

Two channels. Stable follows the releases. Nightly also takes the test
builds made from every change on the testing branch - on GitHub only:
pre-releases tagged ``nightly-<version>``, where the version is the
release's with the build's number after it (1.4.0.123) - newer than 1.4.0,
older than 1.4.1 - so the comparison, the AppImage's name and its
self-check work as for a release.
"""

from __future__ import annotations

import platform
import re
import sys
from urllib.parse import quote

from .. import VERSION
from ._lazy_requests import requests
from .log import log

GITHUB_REPO = "slimture/dopeIPTV"
SITE = "https://dopeiptv.org"
CHANNELS = ("stable", "nightly")
NIGHTLY_PREFIX = "nightly-"


def user_agent() -> str:
    """``dopeIPTV/1.4.4 (Windows; AMD64)``: what the app is, which version
    it runs and on what - all the site learns of an update check or an
    update, and how it tells them from a browser."""
    return f"dopeIPTV/{VERSION} ({platform.system() or sys.platform}; {platform.machine() or '?'})"


def _headers() -> dict:
    return {"Accept": "application/vnd.github+json", "User-Agent": user_agent()}


def _release(d: dict, nightly: bool = False) -> dict:
    tag = d.get("tag_name", "") or ""
    if nightly:
        tag = "v" + tag[len(NIGHTLY_PREFIX):]
    return {
        "tag": tag,
        "name": d.get("name", "") or "",
        "body": d.get("body", "") or "",
        "url": d.get("html_url", "") or "",
        "nightly": nightly,
        "assets": [
            {"name": a.get("name", "") or "",
             "url": a.get("browser_download_url", "") or "",
             "size": int(a.get("size") or 0),
             "digest": a.get("digest", "") or ""}
            for a in (d.get("assets") or []) if isinstance(a, dict)],
    }


def _from_site(d: dict) -> dict:
    """dopeiptv.org's ``releases.json`` in the shape of :func:`_release`;
    each file fetched through ``/dl.php``, which counts it."""
    version = str(d.get("version") or "").strip().lstrip("vV")
    if not re.fullmatch(r"\d+(\.\d+)+", version):
        raise ValueError(f"releases.json names no version ({version!r})")
    assets = []
    for a in d.get("assets") or []:
        if not isinstance(a, dict) or not a.get("name"):
            continue
        name = str(a["name"])
        sha = str(a.get("sha256") or "")
        assets.append({
            "name": name,
            "url": f"{SITE}/dl.php?f={quote(name)}",
            "size": int(a.get("size") or 0),
            "digest": f"sha256:{sha.lower()}" if re.fullmatch(r"[0-9a-fA-F]{64}", sha) else ""})
    return {"tag": "v" + version, "name": f"dopeIPTV {version}",
            "body": str(d.get("notes") or ""), "url": str(d.get("html_url") or ""),
            "nightly": False, "assets": assets}


def fetch_site_release() -> dict:
    """The release dopeiptv.org offers. Raises on network/HTTP error."""
    r = requests.get(f"{SITE}/releases.json", headers={"User-Agent": user_agent()}, timeout=15)
    r.raise_for_status()
    return _from_site(r.json() or {})


def fetch_github_release(repo: str = GITHUB_REPO) -> dict:
    """The newest published release on GitHub. Raises on network/HTTP
    error."""
    r = requests.get(
        f"https://api.github.com/repos/{repo}/releases/latest",
        headers=_headers(), timeout=15)
    r.raise_for_status()
    return _release(r.json() or {})


def fetch_latest_release(repo: str = GITHUB_REPO) -> dict:
    """Return the newest published release as
    ``{"tag", "name", "body", "url", "nightly", "assets"}``, each asset as
    ``{"name", "url", "size", "digest"}`` (digest ``sha256:<hex>``, absent
    on old releases): dopeiptv.org's, or GitHub's when the site cannot be
    asked. Pre-releases - the nightly builds - never come back from this.
    Raises on network/HTTP error."""
    try:
        return fetch_site_release()
    except Exception as e:
        log.info("update: dopeiptv.org did not answer (%s); asking GitHub", e)
    return fetch_github_release(repo)


def fetch_latest_nightly(repo: str = GITHUB_REPO) -> dict | None:
    """The newest nightly build, with its tag as a version tag ("v1.4.0.123");
    None when there is none. Raises on network/HTTP error."""
    r = requests.get(
        f"https://api.github.com/repos/{repo}/releases",
        params={"per_page": 30}, headers=_headers(), timeout=15)
    r.raise_for_status()
    best: dict | None = None
    for d in r.json() or []:
        if not isinstance(d, dict) or d.get("draft") \
                or not d.get("prerelease"):
            continue
        if not str(d.get("tag_name") or "").startswith(NIGHTLY_PREFIX):
            continue
        rel = _release(d, nightly=True)
        if best is None or is_newer(rel["tag"], best["tag"]):
            best = rel
    return best


def fetch_update(repo: str = GITHUB_REPO, channel: str = "stable") -> dict:
    """What *channel* offers: the newest release - or, on nightly, the
    newest nightly build when that is newer still."""
    stable = fetch_latest_release(repo)
    if channel != "nightly":
        return stable
    try:
        nightly = fetch_latest_nightly(repo)
    except Exception:
        nightly = None
    if nightly is not None and is_newer(nightly["tag"], stable.get("tag", "")):
        return nightly
    return stable


def _parse(version: str) -> tuple[int, ...]:
    """Turn '0.5.0' / 'v0.5.0' into (0, 5, 0) for comparison; non-numeric
    parts count as 0 so odd tags never crash the compare. A nightly's fourth
    part sorts it after its release and before the next."""
    out: list[int] = []
    for part in version.lstrip("vV").strip().split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def is_newer(latest_tag: str, current: str) -> bool:
    """True when *latest_tag* is a strictly newer version than *current*."""
    return _parse(latest_tag) > _parse(current)


def same_release(a: str, b: str) -> bool:
    """Whether *a* and *b* are the same release (a test build counts as the
    release it carries the number of: 1.4.2.7 and v1.4.2)."""
    return _parse(a)[:3] == _parse(b)[:3]


def is_nightly(version: str) -> bool:
    """A test build's version has a fourth part: 1.4.0.123."""
    return len(_parse(version)) > 3


def offered(latest_tag: str, current: str, channel: str = "stable") -> bool:
    """Whether *latest_tag* is an update for *current* on *channel*.

    Back on Releases from a test build, the release that build carries the
    number of is offered too - 1.4.0 to 1.4.0.12, which sorts above it - so
    that the channel means releases again from the next one out."""
    if not latest_tag:
        return False
    if channel != "nightly" and is_nightly(current) \
            and not is_nightly(latest_tag):
        return _parse(latest_tag)[:3] >= _parse(current)[:3]
    return is_newer(latest_tag, current)
