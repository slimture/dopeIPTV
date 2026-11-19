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

#: Names the next release adds to a part's code, shown when the app has them.
NEXT = {
    ("sync", "send"): ["auth_of", "AUTH_HEADER", "Remote.of"],
    ("updates", "signed"): ["_HOPS", "DOWNLOAD_MAX", "_get_https"],
}

#: The rest of the app: what to ask, and the files to read (at the commit).
WHOLE = [
    ("servers", "the servers the app runs on this computer and the home network",
     "Who can reach each one, and is every request checked - the token (and compared in constant "
     "time), the Host header against DNS rebinding, the Origin against a web page's requests, "
     "sizes and time limits? Can a page in the browser, another program or another user on the "
     "same computer, or another device on the network use one to read anything, play or record "
     "something, or make the app fetch an address of its choosing?",
     ["dopeiptv/core/remote_api.py", "dopeiptv/core/phone_remote.py", "dopeiptv/core/phone_browse.py",
      "dopeiptv/services/home_server.py", "dopeiptv/core/hdhr.py", "dopeiptv/core/restream.py",
      "dopeiptv/core/dvr_server.py", "dopeiptv/core/net_bridge.py", "dopeiptv/providers/cast_bridge.py",
      "dopeiptv/providers/airplay_bridge.py", "dopeiptv/providers/oauth_loopback.py",
      "dopeiptv/core/instance.py", "dopeiptv/core/exclusive_bind.py", "dopeiptv/core/home_net.py"]),
    ("processes", "the programs the app starts (ffmpeg, mpv, yt-dlp, the command after a recording)",
     "Can text from a provider, a guide or a file name - a title, an address - inject options or "
     "commands, make ffmpeg read local files or other protocols, or write outside the folder it "
     "should? Is anything run through a shell?",
     ["dopeiptv/core/recording.py", "dopeiptv/core/rec_mp4.py", "dopeiptv/core/rec_extras.py",
      "dopeiptv/core/ytdlp.py", "dopeiptv/media/players.py", "dopeiptv/core/player_exec.py",
      "dopeiptv/core/xdg.py"]),
    ("input", "everything it reads from providers, boxes, media servers, guides and subtitles",
     "Treat every answer as hostile: is each one size-limited, parsed safely (XML entities, deep "
     "nesting, huge numbers, slow regular expressions), and shown as plain text rather than HTML? "
     "Can a provider or a device on the network crash the app, fill the disk or memory, or make "
     "it open a file or an internal address?",
     ["dopeiptv/providers/client.py", "dopeiptv/providers/stalker.py", "dopeiptv/providers/epg.py",
      "dopeiptv/providers/enigma2.py", "dopeiptv/providers/satip.py", "dopeiptv/providers/dlna.py",
      "dopeiptv/providers/lan_scan.py", "dopeiptv/providers/mdns.py", "dopeiptv/providers/jellyfin.py",
      "dopeiptv/providers/plex.py", "dopeiptv/core/workers.py", "dopeiptv/ui/widgets.py"]),
    ("secrets", "the logins, tokens and codes it keeps, and its logs",
     "Where are passwords, tokens, the sync code and room codes kept, who can read them, and do "
     "any of them end up in a log file, an error report, a backup or a crash file? Is TLS "
     "certificate checking on wherever the app talks to a service?",
     ["dopeiptv/core/settings_files.py", "dopeiptv/core/stores.py", "dopeiptv/core/log.py",
      "dopeiptv/core/backup.py", "dopeiptv/core/report.py", "dopeiptv/core/net_adapter.py",
      "dopeiptv/core/cert_pin.py"]),
    ("build", "how a release is built, signed and checked",
     "Could someone who takes over a dependency, a GitHub Action, the website or the release "
     "key's storage get a changed build to users? What is pinned, what is checked, and what is "
     "only trusted?",
     [".github/workflows/release.yml", ".github/workflows/ci.yml", "requirements.txt",
      "dopeiptv/core/build_check.py", "dopeiptv/core/selfupdate.py", "dopeiptv/core/update_keys.py"]),
]

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
            if block["id"] == bid:
                src = open(os.path.join(app, block["path"]), encoding="utf-8").read()
                for n in names:
                    leaf = n.split(".")[-1]
                    if n not in block["names"] and re.search(rf"^\s*(def|class)?\s*{re.escape(leaf)}\b", src, re.M):
                        block["names"].append(n)
    code = transparency.code_of(app, ref, site, site_ref)
    with open(os.path.join(site, "site", "transparency", lang + ".html"), encoding="utf-8") as fh:
        page = fh.read()
    titles = {k: _text(v) for k, v in _H2.findall(page)}
    out = []
    for part, ask in _ASK.findall(page):
        chunks = [_text(ask)]
        for p, bid in _CODE.findall(page):
            if p != part:
                continue
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
    head = (f"dopeIPTV review kit - app {ref[:12]}, {len(parts)} parts. Each part is a question and "
            f"the code it is about; paste one part at a time, or all of them, into an AI assistant.")
    for i, (part, text) in enumerate(parts, 1):
        with open(os.path.join(a.out, f"{i:02d}-{part}.txt"), "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    with open(os.path.join(a.out, "ALL.txt"), "w", encoding="utf-8") as fh:
        fh.write(head + "\n\n" + "\n\n\n".join(t for _p, t in parts) + "\n")
    print(f"{a.out}: {len(parts)} parts, app {ref[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
