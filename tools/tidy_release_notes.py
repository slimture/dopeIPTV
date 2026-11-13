#!/usr/bin/env python3
"""Keep the GitHub releases page short, so the downloads are near the top.

A release's body comes from RELEASE_NOTES.md, which carries every version
before it - by 1.4.5 a release was 360 lines before its files. This leaves
each release with its own notes only:

  the latest release   its own section, as it is (the site's What's new and
                       the app's update dialog read it)
  an older release     its headline, its notes folded, and a link to
                       CHANGELOG.md, which has them all
  a test build         its list of changes folded

Run by nightly.yml (every push to testing) and release.yml (after a
release), with the workflow's token; it changes only a release that is not
so yet, so a second run does nothing. The original text of every release
is in git: `git show v1.4.4:RELEASE_NOTES.md`.

    GH_TOKEN=... python3 tools/tidy_release_notes.py [--dry-run]
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

REPO = os.environ.get("GITHUB_REPOSITORY") or "slimture/dopeIPTV"
API = f"https://api.github.com/repos/{REPO}"
FOOTER = f"Every version is in [CHANGELOG.md](https://github.com/{REPO}/blob/main/CHANGELOG.md)."
HEAD = re.compile(r"(?im)^[ \t]*##[ \t]+dopeIPTV[ \t]+(\S+)[^\n]*$")
CHANGES = re.compile(r"(?m)^### Changes since[^\n]*\n")
SHORT = 8                   # lines: an older release this short is not folded


def sections(body: str) -> list[tuple[str, str]]:
    """The body's '## dopeIPTV x.y.z' sections: (version, text with its heading)."""
    marks = list(HEAD.finditer(body))
    return [(m.group(1), body[m.start():marks[i + 1].start() if i + 1 < len(marks) else len(body)].strip())
            for i, m in enumerate(marks)]


def _unwrap(text: str) -> str:
    """Without <details>/<summary>: an older release folded its own long
    part already, and one fold is enough."""
    text = re.sub(r"(?is)<summary>.*?</summary>", "", text)
    text = re.sub(r"(?i)</?details>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def tidy(rel: dict, latest: str) -> str | None:
    """The body *rel* (a release from GitHub's API) should have, or None
    when it has it already. *latest* is the latest release's tag."""
    tag = str(rel.get("tag_name") or "")
    body = str(rel.get("body") or "").replace("\r\n", "\n").strip()
    if not body or rel.get("draft"):
        return None
    if tag.startswith("nightly-"):
        m = CHANGES.search(body)
        if not m or "<details>" in body[m.end():]:
            return None
        rest = body[m.end():].strip()
        n = sum(1 for line in rest.splitlines() if line.startswith("- "))
        if not n:
            return None
        return (body[:m.end()] + "\n<details>\n<summary>" + str(n) + (" change" if n == 1 else " changes") +
                "</summary>\n\n" + rest + "\n\n</details>\n")
    if rel.get("prerelease"):
        return None
    version = tag.lstrip("vV")
    mine = next((text for v, text in sections(body) if v == version), None)
    if tag == latest:
        if mine is None or len(sections(body)) <= 1:
            return None
        return mine + "\n"
    if FOOTER in body:
        return None
    text = _unwrap(HEAD.sub("", mine if mine is not None else body, count=1))
    if len(text.splitlines()) <= SHORT:
        # Short already: only the versions before it go, if there are any.
        return text + "\n\n" + FOOTER + "\n" if len(sections(body)) > 1 else None
    # Its opening lines stay in sight: the one-line summary most have.
    lead, _, rest = text.partition("\n\n")
    if len(lead.splitlines()) <= 3 and not re.match(r"[-*#<|>]\s|<|\|", lead) and rest.strip():
        text = rest.strip()
    else:
        lead = ""
    return ((lead + "\n\n" if lead else "") + "<details>\n<summary>What's new in " + version + "</summary>\n\n" +
            text + "\n\n</details>\n\n" + FOOTER + "\n")


def _call(method: str, url: str, token: str, data: dict | None = None):
    req = urllib.request.Request(url, method=method, data=json.dumps(data).encode() if data else None, headers={
        "Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "dopeIPTV-tidy-release-notes"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read() or b"null")


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        print("GH_TOKEN is not set", file=sys.stderr)
        return 1
    latest = str(_call("GET", f"{API}/releases/latest", token).get("tag_name") or "")
    releases: list = []
    for page in range(1, 20):
        got = _call("GET", f"{API}/releases?per_page=100&page={page}", token)
        releases += got or []
        if not got or len(got) < 100:
            break
    changed = 0
    for rel in releases:
        new = tidy(rel, latest)
        if new is None or not new.strip():
            continue
        old_lines, new_lines = len(str(rel.get("body") or "").splitlines()), len(new.splitlines())
        print(f"{rel['tag_name']}: {old_lines} -> {new_lines} lines" + (" (dry run)" if dry else ""))
        if not dry:
            _call("PATCH", f"{API}/releases/{rel['id']}", token, {"body": new})
        changed += 1
    print(f"{changed} of {len(releases)} releases tidied; latest is {latest}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
