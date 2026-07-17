"""Plain-language diagnosis of why a stream won't play.

mpv only ever reports a generic "loading failed", which tells an end user
nothing. When playback gives up, probe the account (Xtream) and the stream URL
and turn the result into one human sentence the UI can show - no debug mode
required. Runs in a worker thread (it does blocking HTTP), so keep it
self-contained and never touch Qt here.
"""
from __future__ import annotations

import time

import requests

from ..core.log import log
from ..i18n import tr


def diagnose_stream(url: str, client=None) -> str:
    """Return one human sentence explaining why *url* wouldn't play. Checks the
    provider account first (expiry / connection limit / status), then probes
    the stream URL itself for an HTTP status."""
    reason = _account_reason(client)
    if reason:
        return reason
    return _probe_url(url)


def _account_reason(client) -> str | None:
    try:
        if client is None or not hasattr(client, "account_info"):
            return None
        info = (client.account_info() or {}).get("user_info") or {}
    except Exception:
        return None
    if not info:
        return None
    status = str(info.get("status") or "").strip().lower()
    if status and status != "active":
        return tr("diag_account_status", status=status.capitalize())
    exp = info.get("exp_date")
    try:
        if exp not in (None, "", "0", 0) and int(exp) < time.time():
            return tr("diag_expired")
    except (TypeError, ValueError):
        pass
    try:
        active = int(info.get("active_cons"))
        maxc = int(info.get("max_connections"))
        if maxc > 0 and active >= maxc:
            return tr("diag_conn_limit", active=active, maxc=maxc)
    except (TypeError, ValueError):
        pass
    return None


def _probe_url(url: str) -> str:
    try:
        r = requests.get(url, stream=True, timeout=(8, 8),
                         headers={"User-Agent": "dopeIPTV/1.0"})
        code = r.status_code
        try:
            r.close()
        except Exception:
            pass
    except requests.Timeout:
        return tr("diag_timeout")
    except requests.ConnectionError:
        return tr("diag_unreachable")
    except Exception as e:
        log.debug("stream probe failed: %s: %s", type(e).__name__, e)
        return tr("diag_generic")
    log.info("stream probe: HTTP %s", code)
    if code == 200:
        return tr("diag_http_ok_no_play")
    if code in (401, 403):
        return tr("diag_forbidden", code=code)
    if code == 404:
        return tr("diag_not_found")
    # 458 (and friends) are non-standard codes Xtream panels return to say the
    # stream is *blocked* - anti-VPN/re-stream, a connection limit, a region or
    # device block, or a player User-Agent that isn't on their allow-list.
    if code == 458:
        return tr("diag_blocked", code=code)
    return tr("diag_http_error", code=code)
