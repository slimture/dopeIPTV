"""Check GitHub for a newer dopeIPTV release. No Qt - safe on a worker
thread; the UI marshals the result back itself.

Two channels. Stable follows the releases. Nightly also takes the test
builds made from every change on the testing branch: pre-releases tagged
``nightly-<version>``, where the version is the release's with the build's
number after it (1.4.0.123) - newer than 1.4.0, older than 1.4.1 - so the
comparison, the AppImage's name and its self-check work as for a release.
"""

from __future__ import annotations

from ._lazy_requests import requests

GITHUB_REPO = "slimture/dopeIPTV"
CHANNELS = ("stable", "nightly")
NIGHTLY_PREFIX = "nightly-"
_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "dopeIPTV"}


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


def fetch_latest_release(repo: str = GITHUB_REPO) -> dict:
    """Return the newest published release as
    ``{"tag", "name", "body", "url", "nightly", "assets"}``, each asset as
    ``{"name", "url", "size", "digest"}`` (digest is GitHub's
    ``sha256:<hex>``, absent on old releases). Pre-releases - the nightly
    builds - never come back from this. Raises on network/HTTP error."""
    r = requests.get(
        f"https://api.github.com/repos/{repo}/releases/latest",
        headers=_HEADERS, timeout=15)
    r.raise_for_status()
    return _release(r.json() or {})


def fetch_latest_nightly(repo: str = GITHUB_REPO) -> dict | None:
    """The newest nightly build, with its tag as a version tag ("v1.4.0.123");
    None when there is none. Raises on network/HTTP error."""
    r = requests.get(
        f"https://api.github.com/repos/{repo}/releases",
        params={"per_page": 30}, headers=_HEADERS, timeout=15)
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
