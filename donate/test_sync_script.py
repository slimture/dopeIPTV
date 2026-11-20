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


def test_a_release_whose_tag_is_not_a_version_is_never_used(base, tmp_path):
    # GitHub answers, but the tag is more than a version number: nothing of
    # that release reaches the site, and the pages are made from the one here.
    b, env = base
    latest = tmp_path / "latest.json"
    latest.write_text(json.dumps({"tag_name": 'v1</script><script src="/files/a.zip">', "assets": []}))
    (tmp_path / "bin" / "fetch").write_text(
        "#!/bin/sh\nout=\nfor a in \"$@\"; do\n  [ \"$prev\" = -o ] && out=$a\n  prev=$a\ndone\n"
        "case \"$a\" in\n  */releases/latest) cp %s \"$out\" ;;\n  *) exit 1 ;;\nesac\n" % latest)
    run = _run(b, env)
    assert run.returncode == 0, run.stderr
    assert "the latest release's tag is not a version number" in run.stderr
    assert json.loads((b / "public" / "releases.json").read_text())["version"] == "1.6.6"
    assert "</script><script" not in (b / "public" / "i18n" / "en" / "index.html").read_text(encoding="utf-8")


def test_a_download_is_a_key_of_its_own_only_when_the_site_knows_its_name(base, tmp_path):
    # nginx sends any name shaped like a test build's on to GitHub (302), so
    # a made-up one would be a key in downloads.json for good: it is summed
    # under "other", and the total is what was downloaded.
    b, env = base
    (b / "public" / "files").mkdir()
    (b / "public" / "files" / "dopeIPTV-1.6.6-x86_64.AppImage").write_bytes(b"x")
    (b / "public" / "nightly.json").write_text(json.dumps(
        {"version": "1.6.8.90", "assets": [{"name": "dopeIPTV-1.6.8.90-x86_64.AppImage"}]}))
    (b / "downloads.json").write_text(json.dumps({"dopeIPTV-1.6.5-x86_64.AppImage": 40, "other": 2}))
    ua = "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0"

    def line(name: str, status: int = 302, agent: str = ua) -> str:
        return (f'10.0.1.1 - - [19/Nov/2026:12:00:00 +0100] "GET /dl.php?f={name} HTTP/1.1" {status} 0 '
                f'"" "{agent}" "203.0.113.5"\n')
    log = tmp_path / "downloads.log"
    log.write_text(line("dopeIPTV-1.6.6-x86_64.AppImage") + line("dopeIPTV-1.6.5-x86_64.AppImage")
                   + line("dopeIPTV-1.6.8.90-x86_64.AppImage")
                   + line("dopeIPTV-9.9.9.1-made-up") + line("dopeIPTV-9.9.9.2-made-up")
                   + line("nothing-here", 404) + line("dopeIPTV-1.6.6-x86_64.AppImage", agent="dopeIPTV/1.6.5 (Linux; x86_64)"))
    run = _run(b, dict(env, IPTV_DOWNLOAD_LOG=str(log)))
    assert run.returncode == 0, run.stderr
    assert "counted 5 download(s)" in run.stderr
    assert json.loads((b / "downloads.json").read_text()) == {
        "dopeIPTV-1.6.5-x86_64.AppImage": 41, "other": 4,
        "dopeIPTV-1.6.6-x86_64.AppImage": 1, "dopeIPTV-1.6.8.90-x86_64.AppImage": 1}


def test_the_counters_own_log_has_no_address_and_counts_alike(base, tmp_path):
    """nginx's iptv_dl_count: the request line, the answer and what asked -
    nothing else - counted as the log of before was."""
    b, env = base
    (b / "public" / "files").mkdir()
    (b / "public" / "files" / "dopeIPTV-1.6.6-x86_64.AppImage").write_bytes(b"x")
    (b / "downloads.json").write_text(json.dumps({"dopeIPTV-1.6.6-x86_64.AppImage": 3}))

    def line(name: str, status: int = 302, kind: str = "browser") -> str:
        return f'"GET /dl.php?f={name} HTTP/1.1" {status} {kind}\n'
    log = tmp_path / "dl-counts.log"
    log.write_text(line("dopeIPTV-1.6.6-x86_64.AppImage") + line("dopeIPTV-1.6.6-x86_64.AppImage")
                   + line("dopeIPTV-1.6.6-x86_64.AppImage", kind="app")
                   + line("dopeIPTV-1.6.6-x86_64.AppImage", kind="robot")
                   + line("dopeIPTV-1.6.6-x86_64.AppImage", kind="none")
                   + line("dopeIPTV-1.6.6-x86_64.AppImage", 404))
    assert "203.0.113" not in log.read_text()
    run = _run(b, dict(env, IPTV_DOWNLOAD_LOG=str(log)))
    assert run.returncode == 0, run.stderr
    assert "counted 2 download(s)" in run.stderr
    assert json.loads((b / "downloads.json").read_text()) == {"dopeIPTV-1.6.6-x86_64.AppImage": 5}
    src = open(os.path.join(ROOT, "sync-releases.sh"), encoding="utf-8").read()
    assert "IPTV_DOWNLOAD_LOG:-/var/log/nginx/iptv.dope.rs-dl-counts.log}" in src


# -- a release mirrored only as its signed checksum list says ------------------------

def _github(tmp_path, b, env, seed: bytes, change=None, extra=None):
    """A stand-in GitHub serving a release of three installers and its
    SHA256SUMS signed with *seed*; the site's check carries the public half
    of a test key. *change*: a file whose bytes GitHub serves (with its own
    digest) differing from the list; *extra*: a file left off the list."""
    import base64
    import sys
    sys.path.insert(0, HERE)
    import ed25519
    test_seed = b"\x07" * 32
    (b / "donate").mkdir()
    shutil.copy(os.path.join(HERE, "ed25519.py"), b / "donate" / "ed25519.py")
    src = open(os.path.join(HERE, "release_sums.py"), encoding="utf-8").read()
    real = re.search(r'KEYS: tuple\[str, \.\.\.\] = \(\n    "([^"]+)",', src).group(1)
    (b / "donate" / "release_sums.py").write_text(
        src.replace(real, base64.b64encode(ed25519.public_key(test_seed)).decode()))
    srv = tmp_path / "srv"
    srv.mkdir()
    names = ["dopeIPTV-1.6.7-x86_64.AppImage", "dopeIPTV-1.6.7-macOS-arm64.dmg",
             "dopeIPTV-1.6.7-windows-x64-setup.exe"]
    if extra:
        names.append(extra)
    sums = ""
    for n in names:
        data = f"the bytes of {n}\n".encode()
        (srv / n).write_bytes(data)
        if n != extra:
            sums += f"{hashlib.sha256(data).hexdigest()}  {n}\n"
    (srv / "SHA256SUMS").write_text(sums)
    (srv / "SHA256SUMS.sig").write_text(base64.b64encode(ed25519.sign(seed, sums.encode())).decode() + "\n")
    if change:
        (srv / change).write_bytes(b"something else entirely\n")
    assets = [{"name": n, "size": (srv / n).stat().st_size, "state": "uploaded",
               "browser_download_url": f"https://gh.test/{n}",
               "digest": "sha256:" + hashlib.sha256((srv / n).read_bytes()).hexdigest()}
              for n in [*names, "SHA256SUMS", "SHA256SUMS.sig"]]
    (srv / "latest.json").write_text(json.dumps({"tag_name": "v1.6.7", "published_at": "2026-11-20T10:00:00Z",
                                                 "body": "Notes.", "html_url": "https://github.com/x",
                                                 "assets": assets}))
    (tmp_path / "bin" / "fetch").write_text(
        "#!/bin/sh\nout=\nprev=\nfor a in \"$@\"; do\n  [ \"$prev\" = -o ] && out=$a\n  prev=$a\ndone\n"
        f"case \"$a\" in\n  */releases/latest) cp {srv}/latest.json \"$out\" ;;\n"
        f"  https://gh.test/*) cp \"{srv}/${{a##*/}}\" \"$out\" ;;\n  *) exit 1 ;;\nesac\n")
    (b / "public" / "files").mkdir(exist_ok=True)
    return test_seed, _run(b, env)


def _version(b) -> str:
    return json.loads((b / "public" / "releases.json").read_text())["version"]


def test_a_release_signed_with_the_key_is_mirrored_with_its_signed_checksums(base, tmp_path):
    b, env = base
    _seed, run = _github(tmp_path, b, env, b"\x07" * 32)
    assert run.returncode == 0, run.stderr
    assert _version(b) == "1.6.7", run.stderr
    files = sorted(p.name for p in (b / "public" / "files").iterdir())
    assert files == ["SHA256SUMS", "dopeIPTV-1.6.7-macOS-arm64.dmg", "dopeIPTV-1.6.7-windows-x64-setup.exe",
                     "dopeIPTV-1.6.7-x86_64.AppImage"]
    rel = json.loads((b / "public" / "releases.json").read_text())
    assert rel["sha256sums"] == (tmp_path / "srv" / "SHA256SUMS").read_text()
    assert rel["sha256sums_sig"].strip()


def test_a_release_not_signed_with_the_key_never_reaches_the_site(base, tmp_path):
    b, env = base
    _seed, run = _github(tmp_path, b, env, b"\x09" * 32)
    assert "not signed with dopeIPTV's release key - keeping the site as it is" in run.stderr
    assert _version(b) == "1.6.6" and not list((b / "public" / "files").iterdir())


def test_a_file_unlike_the_signed_list_or_not_on_it_keeps_the_release_out(base, tmp_path):
    b, env = base
    _seed, run = _github(tmp_path, b, env, b"\x07" * 32, change="dopeIPTV-1.6.7-macOS-arm64.dmg")
    assert "dopeIPTV-1.6.7-macOS-arm64.dmg: GitHub's checksum is not the signed one" in run.stderr
    assert _version(b) == "1.6.6"
    assert not (b / "public" / "files" / "dopeIPTV-1.6.7-macOS-arm64.dmg").exists()


def test_a_file_left_off_the_signed_list_keeps_the_release_out(base, tmp_path):
    b, env = base
    _seed, run = _github(tmp_path, b, env, b"\x07" * 32, extra="dopeIPTV-1.6.7-windows-x64.zip")
    assert "dopeIPTV-1.6.7-windows-x64.zip is not on the signed checksum list" in run.stderr
    assert _version(b) == "1.6.6"


def test_the_check_says_why_a_list_is_refused():
    import base64
    import sys
    sys.path.insert(0, HERE)
    import ed25519
    import release_sums
    seed = b"\x05" * 32
    key = (base64.b64encode(ed25519.public_key(seed)).decode(),)
    good = b"%s  dopeIPTV-1.6.7.flatpak\n" % (b"a" * 64)
    sig = base64.b64encode(ed25519.sign(seed, good))
    assert release_sums.why_not(good, sig, key) == ""
    assert "no SHA256SUMS" in release_sums.why_not(b"", sig, key)
    assert "not base64" in release_sums.why_not(good, b"%%%", key)
    assert "not signed" in release_sums.why_not(good + b"\n", sig, key)
    assert "not signed" in release_sums.why_not(good, sig)          # the real key: not this one
    odd = b"%s  ../../etc/passwd\n" % (b"a" * 64)
    assert "not a checksum and a file name" in release_sums.why_not(
        odd, base64.b64encode(ed25519.sign(seed, odd)), key)
