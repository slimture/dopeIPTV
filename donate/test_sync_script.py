"""sync-releases.sh run for real in a copy of the site, with stand-ins for
FreeBSD's fetch, md5 and sha256 (fetch always fails: GitHub does not
answer). The pages must be rendered all the same, from the release already
here, and link CSS and JS by copies named for their content - so a deploy
that copies a new style.css in never dresses the pages still out in it."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="no jq")

STUB_HASH = """#!/bin/sh
[ "${1:-}" = -q ] && shift
exec python3 -c 'import hashlib, sys
f = open(sys.argv[1], "rb") if len(sys.argv) > 1 else sys.stdin.buffer
print(hashlib.%s(f.read()).hexdigest())' "$@"
"""


@pytest.fixture()
def base(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "md5").write_text(STUB_HASH % "md5")
    (bin_ / "sha256").write_text(STUB_HASH % "sha256")
    (bin_ / "fetch").write_text("#!/bin/sh\nexit 1\n")
    for f in bin_.iterdir():
        f.chmod(0o755)
    b = tmp_path / "site"
    shutil.copytree(os.path.join(ROOT, "site"), b / "site",
                    ignore=lambda d, names: [n for n in names
                                             if d.endswith("lang") and n not in ("en.json", "sv.json")])
    pub = b / "public"
    pub.mkdir()
    for name in ("style.css", "nav.js", "app.js", "help.js", "features.js", "events.js"):
        shutil.copy(os.path.join(ROOT, "public", name), pub / name)
    (pub / "screenshots").mkdir()
    (pub / "help-shots").mkdir()
    (pub / "releases.json").write_text(json.dumps({"version": "1.6.6", "assets": []}))
    shutil.copy(os.path.join(ROOT, "sync-releases.sh"), b / "sync-releases.sh")
    env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", IPTV_BASE=str(b),
               IPTV_DOWNLOAD_LOG=str(tmp_path / "no.log"))
    return b, env


def _run(base, env, **kw):
    return subprocess.run(["sh", str(base / "sync-releases.sh")], env=env, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, timeout=120, **kw)


def _short(path) -> str:
    return hashlib.md5(open(path, "rb").read()).hexdigest()[:10]


def test_the_pages_are_rendered_when_github_does_not_answer_and_link_copies(base):
    b, env = base
    run = _run(b, env)
    assert run.returncode == 0, run.stderr
    assert "could not get the latest release" in run.stderr
    assert "the pages are rendered from the release already here" in run.stderr
    assert re.search(r"pages: \d+ of \d+ updated", run.stderr)
    page = (b / "public" / "i18n" / "sv" / "index.html").read_text(encoding="utf-8")
    css = f"/assets/style.{_short(b / 'public' / 'style.css')}.css"
    assert f'href="{css}"' in page
    assert (b / "public" / css.lstrip("/")).read_bytes() == (b / "public" / "style.css").read_bytes()
    assert re.search(r'src="/assets/nav\.[0-9a-f]{10}\.js"', page)
    assert not (b / ".sync.lock").exists()


def test_a_new_stylesheet_reaches_the_pages_only_with_them(base):
    b, env = base
    _run(b, env)
    first = f"style.{_short(b / 'public' / 'style.css')}.css"
    with open(b / "public" / "style.css", "a") as f:
        f.write("\n/* a deploy */\n")
    # Copied in: the pages out still link the copy they were made with.
    page = b / "public" / "i18n" / "en" / "index.html"
    assert f"/assets/{first}" in page.read_text(encoding="utf-8")
    assert "a deploy" not in (b / "public" / "assets" / first).read_text(encoding="utf-8")
    _run(b, env)
    second = f"style.{_short(b / 'public' / 'style.css')}.css"
    assert second != first and f"/assets/{second}" in page.read_text(encoding="utf-8")
    # The old copy stays for pages still out a while, then goes; the copy
    # linked now stays however old it is.
    old = time.time() - 20 * 86400
    os.utime(b / "public" / "assets" / first, (old, old))
    os.utime(b / "public" / "assets" / second, (old, old))
    run = _run(b, env)
    assert f"pruned old {first}" in run.stderr
    assert not (b / "public" / "assets" / first).exists()
    assert (b / "public" / "assets" / second).exists()


def test_cron_leaves_while_a_sync_runs_and_a_run_by_hand_waits():
    sh = open(os.path.join(ROOT, "sync-releases.sh"), encoding="utf-8").read()
    subprocess.run(["sh", "-n", os.path.join(ROOT, "sync-releases.sh")], check=True)
    assert re.search(r'if running && \[ -t 2 \]; then\n.*waiting for it', sh)
    # The release part in a subshell of its own, whose end clears no lock.
    assert re.search(r"set \+e\n\(\nset -e\ntrap - EXIT HUP INT TERM", sh)
    assert sh.index("release_rc=$?") < sh.index("# ---- The pages")
