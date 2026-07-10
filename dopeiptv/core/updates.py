"""Check GitHub for a newer dopeIPTV release. No Qt - safe on a worker
thread; the UI marshals the result back itself."""

from __future__ import annotations

import requests

GITHUB_REPO = "slimture/dopeIPTV"


def fetch_latest_release(repo: str = GITHUB_REPO) -> dict:
    """Return the newest published release as
    ``{"tag", "name", "body", "url"}``. Raises on network/HTTP error."""
    r = requests.get(
        f"https://api.github.com/repos/{repo}/releases/latest",
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "dopeIPTV"},
        timeout=15)
    r.raise_for_status()
    d = r.json() or {}
    return {
        "tag": d.get("tag_name", "") or "",
        "name": d.get("name", "") or "",
        "body": d.get("body", "") or "",
        "url": d.get("html_url", "") or "",
    }


def _parse(version: str) -> tuple[int, ...]:
    """Turn '0.5.0' / 'v0.5.0' into (0, 5, 0) for comparison; non-numeric
    parts count as 0 so odd tags never crash the compare."""
    out: list[int] = []
    for part in version.lstrip("vV").strip().split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def is_newer(latest_tag: str, current: str) -> bool:
    """True when *latest_tag* is a strictly newer version than *current*."""
    return _parse(latest_tag) > _parse(current)
