"""Is it the provider's guide, or the app's copy of it?

Downloads the active playlist's programme guide exactly as the app does,
and sets it beside the copy the app keeps on this computer: how big, how
many channels, whether there is anything on now, and where it ends.

    python tools/epg_check.py            # the summary
    python tools/epg_check.py svt1       # plus now/next for channels named so

Run from the repository (with the venv active). Uses the provider account
the app is logged in with; nothing of it is printed.
"""
from __future__ import annotations

import os
import statistics
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import QCoreApplication, QSettings  # noqa: E402

from dopeiptv import APP_NAME, ORG  # noqa: E402
from dopeiptv.core.stores import PlaylistStore  # noqa: E402
from dopeiptv.providers.client import make_client  # noqa: E402
from dopeiptv.providers.epg import (  # noqa: E402
    XmltvGuide, epg_cache_path, normalize_name, parse_xmltv,
)


def when(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%a %d/%m %H:%M")


def hours(seconds: float) -> str:
    h = seconds / 3600
    return f"in {h:.0f} h" if h >= 0 else f"{-h:.0f} h ago"


def summary(by_id: dict, now: float, label: str) -> float | None:
    """Print what a guide holds; return where most channels' guides end."""
    with_progs = {cid: s for cid, s in by_id.items() if len(s[0])}
    total = sum(len(s[0]) for s in with_progs.values())
    print(f"{label}: {len(with_progs):,} channels, {total:,} programmes")
    if not with_progs:
        return None
    on_now = sum(1 for s in with_progs.values()
                 if any(a <= now < b for a, b in zip(s[0], s[1], strict=True)))
    ends = [s[1][-1] for s in with_progs.values()]
    median_end = statistics.median(ends)
    print(f"  something on now:  {on_now:,} channels "
          f"({on_now * 100 // len(with_progs)} %)")
    print(f"  ends (most channels): {when(median_end)}  "
          f"({hours(median_end - now)})")
    print(f"  ends (latest channel): {when(max(ends))}")
    days = Counter(datetime.fromtimestamp(a).strftime("%d/%m")
                   for s in with_progs.values() for a in s[0])
    print("  programmes per day:  " + ", ".join(
        f"{d}: {n:,}" for d, n in sorted(
            days.items(), key=lambda kv: kv[0][3:] + kv[0][:2])[-6:]))
    return median_end


def main() -> int:
    app = QCoreApplication(sys.argv[:1])
    app.setOrganizationName(ORG)
    app.setApplicationName(APP_NAME)
    settings = QSettings(ORG, ORG)
    playlist = PlaylistStore(settings).active()
    if not playlist:
        print("No playlist is set up in the app.")
        return 1
    client = make_client(playlist)
    custom = playlist.get("epg_url") or None
    print(f"Playlist: {playlist.get('name') or '?'}  "
          f"(guide from {'its own guide URL' if custom else 'the provider'})")
    now = time.time()
    print(f"Now: {when(now)}\n")

    saved = XmltvGuide(client, custom, cache_path=epg_cache_path(
        playlist.get("id")))
    age = saved._cache_age()
    saved_end = None
    if age is None:
        print("Saved guide on this computer: none")
    elif saved._load_from_disk():
        print(f"Saved guide on this computer: downloaded {hours(-age)}")
        saved_end = summary(saved._by_id, now, "  saved")
    else:
        print("Saved guide on this computer: there, but it does not load")
    print()

    fresh = XmltvGuide(client, custom)
    t0 = time.monotonic()
    try:
        data = fresh._download()
    except Exception as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        print(f"Download from the provider FAILED: {type(e).__name__}"
              + (f" (HTTP {status})" if status else ""))
        print("\nVerdict: the provider's guide cannot be fetched right now.")
        return 2
    took = time.monotonic() - t0
    print(f"Download from the provider: {len(data) / 2**20:.1f} MB "
          f"in {took:.1f} s")
    if not data.strip():
        print("\nVerdict: the provider sends an empty guide.")
        return 2
    try:
        by_id, by_name = parse_xmltv(data, XmltvGuide.KEEP_PAST_DAYS)
    except Exception as e:
        print(f"It does not read as XMLTV: {type(e).__name__}: {e}"[:300])
        print("\nVerdict: the provider's guide is damaged or not a guide.")
        return 2
    fresh_end = summary(by_id, now, "  provider")

    print()
    if fresh_end is None:
        print("Verdict: the provider's guide has no programmes at all.")
    elif fresh_end < now:
        print("Verdict: the PROVIDER's guide is out of date - it ends before "
              "now. Nothing in the app can fix that; ask the provider.")
    elif fresh_end - now < XmltvGuide.RUNS_OUT_S:
        print("Verdict: the provider's guide is current but short - it runs "
              "out within a few hours. The app fetches it again as it does.")
    elif saved_end is not None and saved_end < now:
        print("Verdict: the provider's guide is fine; the copy on this "
              "computer is old. Settings -> Guide -> Refresh guide now (or "
              "this version, which refreshes by itself) fixes it.")
    else:
        print("Verdict: the provider's guide is fine.")

    wanted = normalize_name(" ".join(sys.argv[1:]))
    if wanted:
        print(f"\nChannels named like '{' '.join(sys.argv[1:])}':")
        names = sorted(n for n in by_name if wanted in n)[:12]
        if not names:
            print("  none in the provider's guide")
        for n in names:
            s = by_id.get(by_name[n])
            if not s or not len(s[0]):
                print(f"  {n}: no programmes")
                continue
            later = [i for i in range(len(s[0])) if s[1][i] > now][:3]
            shown = ", ".join(
                f"{datetime.fromtimestamp(s[0][i]):%d/%m %H:%M} {s[2][i]}"
                for i in later) or f"nothing after {when(s[1][-1])}"
            print(f"  {n} [{by_name[n]}]: {shown}")
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
