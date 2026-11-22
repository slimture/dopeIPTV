#!/usr/bin/env python3
"""A review kit before a release: every question of the transparency page
(site/transparency/<lang>.html) with the very code its copy button gives -
taken from the app at any commit, not only a released tag - and the
questions about the rest of the app that the page does not show code for
(they name the files, at that commit, for an AI that can open links).

    python3 tools/review_kit.py --app PATH [--ref testing] [--lang en] [--out DIR]

PATH is a checkout of the app at --ref (a tag, a branch or a commit that is
on GitHub, so the links open). DIR gets one file per part (01-reports.txt,
...) and ALL.txt with all of them in one go, to paste into an AI assistant
and bring its answers back. Nothing here is published: tools/ is not part
of the site.
"""
from __future__ import annotations

import argparse
import html
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import transparency  # noqa: E402

#: Names the next release adds to a part's code, shown when the app has them
#: (a block taken by its lines gets them in its first named companion).
#: At the release they move into transparency.CODE, and its tests into
#: transparency.TESTS - 1.6.12's did.
NEXT: dict[tuple[str, str], list[str]] = {}

#: Blocks the next release adds to a part, shown when the app has every file
#: they name - after the part's own blocks, with no marker on the page yet.
#: At the release each moves into transparency.CODE, with its
#: <!--tp:code PART ID--> on the page - 1.6.12's did.
NEW: list[tuple[str, dict]] = []

#: Names the next release takes out of a part's code, left out when the app
#: no longer has them.
GONE: dict[tuple[str, str], list[str]] = {}

#: Excerpts whose markers the next release no longer has: (part, block,
#: file, a marker still there in the released code, the names to show instead).
MOVED: list[tuple[str, str, str, str, list[str]]] = []

#: The rest of the app: what to ask, and the files to read (at the commit).
WHOLE = [
    ("servers", "the servers the app runs on this computer and the home network",
     "Who can reach each one, and is every request checked - the token (and compared in constant "
     "time), the Host header against DNS rebinding, the Origin against a web page's requests, "
     "sizes, connection counts and time limits? Can a page in the browser, another program or "
     "another user on the same computer, or another device on the network use one to read "
     "anything, play or record something, or make the app fetch an address of its choosing?",
     ["dopeiptv/core/remote_api.py", "dopeiptv/core/phone_remote.py", "dopeiptv/core/phone_browse.py",
      "dopeiptv/services/home_server.py", "dopeiptv/services/share.py", "dopeiptv/core/hdhr.py",
      "dopeiptv/core/restream.py", "dopeiptv/core/dvr_server.py", "dopeiptv/core/net_bridge.py",
      "dopeiptv/providers/cast_bridge.py", "dopeiptv/providers/airplay_bridge.py",
      "dopeiptv/providers/oauth_loopback.py", "dopeiptv/core/exclusive_bind.py",
      "dopeiptv/core/home_net.py"]),
    ("processes", "the programs the app starts (ffmpeg, mpv, VLC, yt-dlp, the command after a recording)",
     "Can text from a provider, a guide or a file name - a title, an address - inject options or "
     "commands, make ffmpeg or mpv read local files or other protocols, or write outside the folder "
     "it should? Is anything run through a shell? Look at every place a program is started.",
     ["dopeiptv/core/player_exec.py", "dopeiptv/media/players.py", "dopeiptv/core/recording.py",
      "dopeiptv/core/rec_mp4.py", "dopeiptv/core/rec_extras.py", "dopeiptv/core/rec_view.py",
      "dopeiptv/core/ytdlp.py", "dopeiptv/core/ytdlp_pin.py", "tools/ytdlp_pin.py",
      "dopeiptv/core/xdg.py", "dopeiptv/providers/cast_feed.py"]),
    ("input", "everything it reads from providers, boxes, media servers, guides, pictures and subtitles",
     "Treat every answer as hostile: is each one size-limited, parsed safely (XML entities, deep "
     "nesting, huge numbers, slow regular expressions, pickle), and shown as plain text rather than "
     "HTML? Which addresses from a provider may reach the player, and where can the app's own "
     "requests land - a name that resolves to this computer, a redirect into the home network? "
     "Can a provider or a device on the "
     "network crash the app, fill the disk or memory, make it read a local file or a network share "
     "(a Windows login hash), or make it open an internal address?",
     ["dopeiptv/core/stream_address.py", "dopeiptv/core/dest_guard.py", "dopeiptv/core/picture_files.py",
      "dopeiptv/core/svg_safe.py", "dopeiptv/core/_lazy_requests.py", "dopeiptv/core/net_adapter.py", "dopeiptv/core/workers.py", "dopeiptv/providers/client.py",
      "dopeiptv/providers/stalker.py", "dopeiptv/providers/epg.py", "dopeiptv/providers/boxes.py",
      "dopeiptv/providers/enigma2.py", "dopeiptv/providers/satip.py", "dopeiptv/providers/lan_scan.py",
      "dopeiptv/providers/jellyfin.py", "dopeiptv/providers/plex.py", "dopeiptv/ui/widgets.py"]),
    ("secrets", "the logins, tokens and codes it keeps, its logs, backups and error reports",
     "Where are passwords, tokens, the sync code and room codes kept, who can read them, and do any "
     "of them end up in a log file, an error report, a backup or a crash file? What does restoring "
     "someone else's backup turn on? Is TLS certificate checking on wherever the app talks to a "
     "service?",
     ["dopeiptv/core/settings_files.py", "dopeiptv/core/stores.py", "dopeiptv/core/log.py",
      "dopeiptv/core/backup.py", "dopeiptv/ui/backup_dialogs.py", "dopeiptv/core/report.py",
      "dopeiptv/core/net_adapter.py", "dopeiptv/core/cert_pin.py", "dopeiptv/core/sync.py",
      "dopeiptv/ui/sync_review.py"]),
    ("links", "how other programs reach the app: dopeiptv:// links, a second start, drops, Discord, LIRC and the media controls",
     "Can a web page, another program or another user of the computer make the app start a server, "
     "play an address, load a plugin or run code through a link or a second start? Are the local "
     "sockets the app talks to or listens on this user's own? What goes on the session bus?",
     ["dopeiptv/core/commands.py", "dopeiptv/app.py", "dopeiptv/launcher.py", "dopeiptv/core/instance.py",
      "dopeiptv/ui/mw_commands.py", "dopeiptv/ui/mw_queue.py", "dopeiptv/core/discord_rpc.py",
      "dopeiptv/core/tv_input.py", "dopeiptv/services/mpris.py", "dopeiptv/services/media_session.py"]),
    ("casting", "casting to TVs and finding devices on the network (Chromecast, DLNA, Kodi, AirPlay)",
     "What does each kind of device get from the app (an address, a login, headers)? Can a device on "
     "the network send the app's requests elsewhere, pose as a TV the person used before, or take a "
     "cast meant for another? Are the bridges the app serves to a TV locked to it?",
     ["dopeiptv/providers/casting.py", "dopeiptv/providers/chromecast.py", "dopeiptv/providers/dlna.py",
      "dopeiptv/providers/kodi.py", "dopeiptv/providers/mdns.py", "dopeiptv/providers/cast_devices.py",
      "dopeiptv/ui/cast_dialog.py", "dopeiptv/providers/cast_bridge.py", "dopeiptv/providers/airplay_bridge.py"]),
    ("profiles", "profiles, PINs, the parental lock and kids profiles",
     "How is a PIN kept and how many tries does guessing take? Can a kids profile or someone without "
     "the PIN reach a locked category's or an adult title - through History, Continue watching, "
     "search, the phone remote, a rename, Settings or Nerd? Can a kids profile play a local file, a "
     "recording or an address, leave for a grown-up's profile, or be made without a grown-up's PIN?",
     ["dopeiptv/core/pins.py", "dopeiptv/core/profiles.py", "dopeiptv/core/stores.py",
      "dopeiptv/ui/mw_profiles.py", "dopeiptv/ui/mw_lists.py", "dopeiptv/ui/mw_phone_browse.py",
      "dopeiptv/core/mitt.py", "dopeiptv/ui/mw_search.py", "dopeiptv/ui/profiles_ui.py",
      "dopeiptv/ui/main_window.py"]),
    ("privacyall", "private viewing across the whole app",
     "Private viewing promises that nothing is kept or sent about what plays. Check every place that "
     "keeps or sends something about a playback - history, resume points, statistics, Trakt, Simkl, "
     "media servers, Discord, events, MQTT, the media controls, the remote API, TMDB lookups, the "
     "rewind buffer, a cast's converter (its HLS segments and spool), recordings, subtitles, mpv's own "
     "cache, logs - and whether each asks about that very playback and keeps nothing on a disk "
     "(core/memory_safe.py: what counts as memory, swap included).",
     ["dopeiptv/core/privacy.py", "dopeiptv/ui/mw_privacy.py", "dopeiptv/ui/main_window.py",
      "dopeiptv/ui/mw_trakt.py", "dopeiptv/ui/mw_mpris.py", "dopeiptv/ui/mw_remote.py",
      "dopeiptv/core/events.py", "dopeiptv/core/dvr_pool.py", "dopeiptv/providers/metadata.py",
      "dopeiptv/core/memory_safe.py", "dopeiptv/core/dvr_store.py", "dopeiptv/media/dvr_player.py",
      "dopeiptv/providers/cast_bridge.py", "dopeiptv/providers/airplay_bridge.py",
      "dopeiptv/ui/mw_cast.py", "dopeiptv/ui/mw_recording.py", "dopeiptv/ui/subtitle_search.py"]),
    ("build", "how a release is built, signed and checked",
     "Could someone who takes over a dependency, a GitHub Action, the website or the release key's "
     "storage get a changed build to users? What is pinned, what is checked, and what is only "
     "trusted?",
     [".github/workflows/release.yml", ".github/workflows/ci.yml", ".github/workflows/nightly.yml",
      "packaging/build-inputs.json", "tools/build_inputs.py", "tools/audit_deps.py",
      "requirements.txt", "dopeiptv/core/build_check.py", "dopeiptv/core/selfupdate.py",
      "dopeiptv/core/update_keys.py", "tools/build_manifest.py", "tools/ytdlp_pin.py"]),
]

#: The question over all the parts at once - the page's "copy everything".
ALL_ASK = (
    "Below is dopeIPTV's security and privacy in {n} parts - an open-source TV player, its code at "
    "commit {ref}. Each part has its own question and the code it is about (each block headed with "
    "the address of those lines on GitHub). Answer every part in turn: explain in plain English "
    "what the code does, then review it as an attacker would and say where it falls short. Then, "
    "at the end, give one table of every weakness you found across all parts - the part, the file "
    "and line, who could use it (a provider, a device on the network, a web page, another user of "
    "the computer, someone on the network path, the developer's own server), what they could do, "
    "how serious it is (critical, high, medium, low), and how to fix it - and an overall "
    "assessment. Look for ways the parts could be combined, too. Do not guess: say so where "
    "something depends on code that is not included, and do not take the comments' word for what "
    "the code does. Last, score each part and the whole from 1 to 10 against the project's own "
    "threat model ({threat}): 10 when none of the attackers it names can use anything you found, "
    "1 when one of them can take what matters now - each score with one line on why, in a table "
    "at the very top of your answer."
)

_ASK = re.compile(r'<blockquote class="tp-ask" data-for="([a-z]+)">(.*?)</blockquote>', re.S)
_CODE = re.compile(r"<!--tp:code ([a-z]+) ([a-z]+)-->")
_H2 = re.compile(r'<h2 id="([a-z]+)">(.*?)</h2>', re.S)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def _git(path: str, *args: str) -> str:
    return subprocess.run(["git", "-C", path, *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def _lines(a: int, b: int) -> str:
    return str(a) if a == b else f"{a}-{b}"


def kit(app: str, ref: str, lang: str) -> list[tuple[str, str]]:
    site = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    site_ref = _git(site, "rev-parse", "HEAD")
    with open(os.path.join(app, "dopeiptv/__init__.py"), encoding="utf-8") as fh:
        version = re.search(r'__version__ = "([^"]+)"', fh.read())
    shown = f"{version.group(1) if version else '?'} @ {ref}"
    for (part, bid), names in NEXT.items():
        for block in transparency.CODE.get(part, []):
            into = next((b for b in [block] + block.get("with", []) if "names" in b), None)
            if block["id"] == bid and into is not None:
                src = open(os.path.join(app, into["path"]), encoding="utf-8").read()
                for n in names:
                    leaf = n.split(".")[-1]
                    if n not in into["names"] and re.search(rf"^\s*(def|class)?\s*{re.escape(leaf)}\b", src, re.M):
                        into["names"].append(n)
    for (part, bid), names in GONE.items():
        for block in transparency.CODE.get(part, []):
            if block["id"] == bid:
                src = open(os.path.join(app, block["path"]), encoding="utf-8").read()
                block["names"] = [n for n in block["names"] if n not in names
                                  or re.search(rf"^{re.escape(n)}\b", src, re.M)]
    # Where the app at *ref* has moved on from the excerpt's markers (the next
    # release), the excerpt follows it by name instead.
    for part, bid, path, marker, names in MOVED:
        src = open(os.path.join(app, path), encoding="utf-8").read()
        if re.search(marker, src, re.M):
            continue
        for block in transparency.CODE.get(part, []):
            if block["id"] != bid:
                continue
            for w in block.get("with", []):
                if w.get("path") == path and w.get("to") and not re.search(w["to"], src, re.M):
                    w.pop("from", None)
                    w.pop("to", None)
                    w["names"] = list(names)
    added: set[tuple[str, str]] = set()
    for part, blk in NEW:
        files = [b["path"] for b in [blk] + blk.get("with", []) if b["repo"] == "app"]
        have = {b["id"] for b in transparency.CODE.get(part, [])}
        if blk["id"] not in have and all(os.path.exists(os.path.join(app, f)) for f in files):
            transparency.CODE.setdefault(part, []).append(blk)
            added.add((part, blk["id"]))
    code = transparency.code_of(app, ref, site, site_ref)
    with open(os.path.join(site, "site", "transparency", lang + ".html"), encoding="utf-8") as fh:
        page = fh.read()
    titles = {k: _text(v) for k, v in _H2.findall(page)}
    asks = {p: _text(a) for p, a in _ASK.findall(page) if p != "all"}
    with_code = {p for p, _b in _CODE.findall(page)}
    out = []
    # Every part of the page in its order: its own question, or - for a part
    # that shows code but asks nothing of its own - the plain one.
    for part in [k for k in titles if k in asks or k in with_code]:
        ask = asks.get(part) or (
            f"Below is the part of dopeIPTV's transparency page called \"{titles[part]}\", with the code "
            f"it is about. Explain in plain English what the code does, then review it as an attacker "
            f"would: does the code do what the page says, and where does it fall short?")
        chunks = [ask]
        marked = [(p, bid) for p, bid in _CODE.findall(page) if p == part]
        for p, bid in marked + sorted(k for k in added if k[0] == part):
            for blk in code.get(part, []):
                if blk["id"] != bid:
                    continue
                for c in blk["parts"]:
                    chunks.append(f"--- {c['path']}:{_lines(c['start'], c['end'])} (dopeIPTV {shown}) "
                                  f"{c['url']} ---\n{c['code']}")
        out.append((part, f"## {titles.get(part, part)}\n\n" + "\n\n".join(chunks)))
    base = f"{transparency.REPO}/blob/{ref}/"
    for part, what, ask, files in WHOLE:
        links = "\n".join(base + f for f in files if os.path.exists(os.path.join(app, f)))
        out.append((part, f"## {what[0].upper() + what[1:]}\n\n"
                    f"Below are links to code from dopeIPTV, an open-source TV player - {what} - at "
                    f"commit {ref}. Open and read each file (if you cannot open links, say so, and ask "
                    f"for the files to be pasted). Explain in plain English what the code does. Then "
                    f"review it critically, as an attacker would: {ask} Point out weaknesses with the "
                    f"file and line, how bad each is, and what the code does not protect against. Do "
                    f"not guess: say so if something depends on code that is not included.\n\n{links}"))
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--app", required=True)
    ap.add_argument("--ref", default="testing")
    ap.add_argument("--lang", default="en")
    ap.add_argument("--out", default="review-kit")
    a = ap.parse_args(argv)
    app = os.path.abspath(a.app)
    ref = a.ref
    if not re.fullmatch(r"[0-9a-f]{40}", ref):
        ref = _git(app, "rev-parse", "HEAD")
    parts = kit(app, ref, a.lang)
    os.makedirs(a.out, exist_ok=True)
    head = ALL_ASK.format(n=len(parts), ref=ref,
                          threat=f"{transparency.REPO}/blob/{ref}/docs/SECURITY.md")
    for i, (part, text) in enumerate(parts, 1):
        with open(os.path.join(a.out, f"{i:02d}-{part}.txt"), "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    with open(os.path.join(a.out, "ALL.txt"), "w", encoding="utf-8") as fh:
        fh.write(head + "\n\n" + "\n\n\n".join(t for _p, t in parts) + "\n")
    # For an assistant that cannot open links: each part about the rest of
    # the app with its files' whole source in it, one file per part.
    full = os.path.join(a.out, "full")
    os.makedirs(full, exist_ok=True)
    for part, what, ask, files in WHOLE:
        blocks = []
        for f in files:
            path = os.path.join(app, f)
            if os.path.exists(path):
                with open(path, encoding="utf-8", errors="replace") as fh:
                    blocks.append(f"--- {f} (dopeIPTV @ {ref}) {transparency.REPO}/blob/{ref}/{f} ---\n{fh.read()}")
        with open(os.path.join(full, f"{part}.txt"), "w", encoding="utf-8") as fh:
            fh.write(f"## {what[0].upper() + what[1:]}\n\nBelow is the whole source of the files that "
                     f"matter for this part of dopeIPTV, an open-source TV player - {what} - at commit "
                     f"{ref}. Explain in plain English what the code does. Then review it critically, as "
                     f"an attacker would: {ask} Point out weaknesses with the file and line, how bad each "
                     f"is (critical, high, medium, low), who could use it and how to fix it. Do not guess: "
                     f"say so if something depends on code that is not included, and do not take the "
                     f"comments' word for what the code does.\n\n" + "\n\n".join(blocks) + "\n")
    print(f"{a.out}: {len(parts)} parts, app {ref[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
