"""Update a running AppImage to the newest GitHub release, in place.

The AppImage is one file the user put somewhere and pinned to their dock,
so "download the update" left them with two files and a dock icon that
kept starting the old one (the menu entry names the exact path). This
replaces the file the way the user would by hand, but checked at every
step - and nothing on disk changes until the new file has proven it runs:

1. download the release asset next to the running file, as a hidden
   ``.part`` file, checking its size and GitHub's SHA-256 digest;
2. run it with ``--self-check`` (no window: it loads the bundled player
   and the languages, then exits), which proves it mounts and starts here;
3. only then move it into place and point our menu entry at it.

The old file is removed by the NEW version once it has started (see
``take_pending_cleanup``), never before - a failed start leaves the old
one exactly where it was. No Qt here; the UI drives it from a worker.
"""
from __future__ import annotations

import base64
import hashlib
import os
import platform
import re
import shlex
import stat
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import VERSION
from . import build_check, desktop_entry, ed25519
from ._lazy_requests import requests
from .update_keys import UPDATE_KEYS
from .updates import GITHUB_REPO, is_nightly, offered, site_headers
from ..i18n import LocalizedError
from .log import log
from .xdg import system_env

# What the update dialog says once the new version is in place (i18n key:
# restart to use it), and while it is checked after the download.
DONE_KEY = "update_ready_restart"
VERIFY_KEY: str | None = "update_verifying"

# Set by the AppImage runtime for the process it starts. A child AppImage
# gets its own; ours must not leak into the self-check of the new file.
_RUNTIME_VARS = ("APPIMAGE", "APPDIR", "ARGV0", "OWD")
SELF_CHECK_TIMEOUT = 120


class UpdateError(LocalizedError):
    """The update stopped; the message says why, and nothing was changed.
    Raised with a string key, so the log says it in English and the user in
    their own language."""


def appimage_path() -> Path | None:
    """The running AppImage file, or None when this is not one."""
    if not sys.platform.startswith("linux"):
        return None
    p = os.environ.get("APPIMAGE")
    if not p:
        return None
    path = Path(p)
    return path if path.is_file() else None


def can_self_update() -> bool:
    """Running from an AppImage in a folder we may write to."""
    path = appimage_path()
    return (path is not None and arch() is not None
            and os.access(path.parent, os.W_OK)
            and os.access(path, os.W_OK))


def arch() -> str | None:
    """The architecture as the release asset names spell it."""
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "x86_64"
    if m in ("aarch64", "arm64"):
        return "aarch64"
    return None


def _bare(version: str) -> str:
    return version.strip().lstrip("vV")


def pick_asset(assets: list[dict], version: str, cpu: str) -> dict | None:
    """The release asset holding the AppImage for *cpu*."""
    want = f"dopeIPTV-{_bare(version)}-{cpu}.AppImage"
    for a in assets:
        if a.get("name") == want and a.get("url"):
            return a
    return None


def target_path(current: Path, old_version: str, new_version: str) -> Path:
    """Where the new version goes. A file still named after its version
    gets the new version's name beside it; one the user renamed (say
    ``dopeIPTV.AppImage``) is replaced under that same name, so whatever
    they launch it from keeps working."""
    old, new = _bare(old_version), _bare(new_version)
    if old and old in current.name:
        return current.with_name(current.name.replace(old, new))
    return current


def refuse_older(tag: str) -> None:
    """Stop unless the release *tag* is an update for the version running.

    Every installer (this one, the Windows and the Mac one) asks here
    before it fetches anything, whatever offered the release: a release
    signed long ago stays signed, so a site taken over could hand out an
    old one, with the faults fixed since. Strictly newer than this copy it
    must be - the one exception the release a test build carries the
    number of (1.4.0 over 1.4.0.12), and only while the person has left
    Test builds for Releases on this very build (updates.offered,
    left_nightly)."""
    if offered(tag, VERSION, "stable"):
        return
    log.error("update: %s is not newer than the running %s - not installed", tag or "(no version)", VERSION)
    raise UpdateError("upd_err_mac_check", step="version")


_SUMS_LINE = re.compile(r"([0-9a-fA-F]{64}) [ *](\S.*)")


_HOPS = 5
DOWNLOAD_MAX = 2 << 30          # no release file comes near 2 GiB


def _loopback(url: str) -> bool:
    from urllib.parse import urlsplit
    try:
        return (urlsplit(url).hostname or "") in ("127.0.0.1", "localhost", "::1")
    except ValueError:
        return False


def _get_https(url: str, *, headers: Callable[[str], dict] = site_headers,
               get: Callable[..., Any] | None = None, **kw):
    """GET *url*, following its redirects by hand: each address https (a
    release's file on GitHub moves on to its storage, never to plain http),
    and each hop's headers its own (*headers* of its address) - the build's
    kind only to dopeiptv.org. A first address on this computer (a test's
    own server) may be http, and lead on to this computer alone. *get*:
    the requests.get to use (the caller's own)."""
    local = url.startswith("http://") and _loopback(url)
    for _ in range(_HOPS + 1):
        if not (url.startswith("https://") or (local and url.startswith("http://") and _loopback(url))):
            raise UpdateError("upd_err_not_https")
        r = (get or requests.get)(url, allow_redirects=False, headers=headers(url), **kw)
        if not getattr(r, "is_redirect", False):
            return r
        from urllib.parse import urljoin
        nxt = urljoin(url, r.headers.get("Location") or "")
        r.close()
        url = nxt
    raise UpdateError("upd_err_download", error="too many redirects")


def _github_file(tag: str, name: str) -> bytes:
    """A small file of the release *tag* (v1.4.6, or a test build's
    v1.4.5.90), straight from GitHub - read no further than 1 MiB."""
    ver = tag.strip().lstrip("vV")
    gh_tag = f"nightly-{ver}" if is_nightly(tag) else f"v{ver}"
    url = f"https://github.com/{GITHUB_REPO}/releases/download/{gh_tag}/{name}"
    with _get_https(url, timeout=20, stream=True) as r:
        r.raise_for_status()
        out = bytearray()
        for chunk in r.iter_content(chunk_size=1 << 14):
            out += chunk
            if len(out) > 1 << 20:
                raise ValueError(f"{name} is too large")
    return bytes(out)


def names_version(name: str, tag: str) -> bool:
    """Whether the file *name* is one of the release *tag*'s: its version
    in the name as the build writes it (dopeIPTV-1.6.9-x86_64.AppImage,
    dopeiptv_1.6.9_amd64.deb, dopeIPTV-1.6.9.flatpak; a test build's
    1.6.9.282), whole - 1.6.9 is not 1.6.9.5 nor 11.6.9. The name is on the
    signed list, so this binds the version a site says to the bytes: an
    older signed file offered as a newer release is not installed."""
    ver = re.sub(r"^(?:nightly-|[vV])", "", str(tag or "").strip())
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}", ver):
        return False
    return re.search(r"(?<![\d.])" + re.escape(ver) + r"(?![\d]|\.\d)", str(name or "")) is not None


def signed_asset(release: dict, asset: dict) -> dict:
    """*asset* with the SHA-256 the release's signed checksum list gives
    it - the list signed with dopeIPTV's own key (core/update_keys.py).
    The site sends the list and its signature with the release
    (releases.json, nightly.json); otherwise they come from GitHub. With
    no signature, or one that is not by that key, or the file not on the
    list, the update stops: a site or mirror taken over cannot serve
    anything this accepts."""
    keys = []
    for k in UPDATE_KEYS:
        try:
            keys.append(base64.b64decode(k, validate=True))
        except ValueError:
            continue
    if not keys:
        # A build without the release key (core/update_keys.py) has nothing
        # to check a signature with: it takes no update at all rather than
        # trust a checksum the same site sent.
        log.error("update: this build carries no release key - no update is taken")
        raise UpdateError("upd_err_unsigned")
    tag = str(release.get("tag") or "")
    name = asset.get("name")
    if not names_version(str(name or ""), tag):
        # The signed list names files, not versions: a file of an older
        # release, still on its own signed list, offered under a newer tag
        # would pass refuse_older and the checksum both. Its name says its
        # version, and that name is what the list signs.
        log.error("update: %s is not a file of %s - not installed", name, tag or "(no version)")
        raise UpdateError("upd_err_unsigned")
    # The site's copy first; GitHub's when that one is missing, stale or
    # does not check out - the signature, not where it came from, decides.
    for where in ("site", "GitHub"):
        try:
            if where == "site":
                sums, sig = release.get("sums"), release.get("sums_sig")
                if not (sums and sig):
                    continue
            else:
                sums, sig = _github_file(tag, "SHA256SUMS"), _github_file(tag, "SHA256SUMS.sig")
            data = sums.encode() if isinstance(sums, str) else bytes(sums)
            raw_sig = base64.b64decode(sig.strip() if isinstance(sig, (str, bytes)) else b"",
                                       validate=True)
        except Exception as e:
            log.error("update: no signed checksum list for %s from %s: %s", tag, where, e)
            continue
        if not any(ed25519.verify(k, data, raw_sig) for k in keys):
            log.error("update: the checksum list of %s from %s is not signed with "
                      "dopeIPTV's key", tag, where)
            continue
        for line in data.decode("utf-8", "replace").splitlines():
            m = _SUMS_LINE.fullmatch(line.strip())
            if m and m.group(2) == name:
                return dict(asset, digest="sha256:" + m.group(1).lower())
        log.error("update: %s is not on the signed checksum list of %s from %s",
                  name, tag, where)
    raise UpdateError("upd_err_unsigned")


def _expected_sha256(asset: dict) -> str | None:
    digest = str(asset.get("digest") or "")
    m = re.fullmatch(r"sha256:([0-9a-fA-F]{64})", digest)
    return m.group(1).lower() if m else None


#: What each file download() checked hashes to, by its path: checked again
#: right before it runs or is put in place (verify_again).
_VERIFIED: dict[str, str] = {}


def verify_again(path: Path) -> None:
    """*path* still the very bytes download() checked - right before it is
    run or put in place, so nothing changed in between goes on; otherwise
    UpdateError, the file removed."""
    want = _VERIFIED.get(str(path))
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError as e:
        raise UpdateError("upd_err_download", error=e) from e
    if not want or h.hexdigest() != want:
        log.error("update: %s is not the file that was checked - not used", path.name)
        path.unlink(missing_ok=True)
        _VERIFIED.pop(str(path), None)
        raise UpdateError("upd_err_checksum")


def _others_in_group(gid: int) -> bool:
    """Whether an account other than this one is in group *gid* - a member
    of it, or an account whose own group it is. A group that cannot be
    looked up counts as shared (fails closed)."""
    try:
        import grp
        import pwd
        me = pwd.getpwuid(os.geteuid()).pw_name
        members = set(grp.getgrgid(gid).gr_mem)
        if members - {me}:
            return True
        return any(p.pw_gid == gid and p.pw_name != me for p in pwd.getpwall())
    except (ImportError, KeyError, OSError):
        return True


def shared_folder(folder: Path) -> bool:
    """Whether another account could put a file of its own in *folder*'s
    place for one of ours - rename ours away, write one under its name:
    a folder not ours or root's, or one others may write to without the
    sticky bit (which keeps what is ours ours) - its group too, unless no
    other account is in it (a user's own group, as Debian and Fedora make
    one per account). POSIX only."""
    if os.name != "posix":
        return False
    try:
        st = os.stat(folder)
    except OSError:
        return True
    if st.st_uid not in (os.geteuid(), 0):
        return True
    if st.st_mode & stat.S_ISVTX:
        return False
    if st.st_mode & 0o002:
        return True
    if not st.st_mode & 0o020:
        return False
    return st.st_gid != os.getegid() or _others_in_group(st.st_gid)


def download(asset: dict, dest: Path,
             progress: Callable[[int, int], None] | None = None,
             cancel: threading.Event | None = None) -> None:
    """Fetch *asset* to *dest*, verifying size and digest. Removes a partial
    or mismatching file before raising."""
    size = int(asset.get("size") or 0)
    sha = _expected_sha256(asset)
    # Nothing is fetched that cannot be checked, or from an address that
    # is not https - nor from one a redirect leads to (_get_https).
    if not str(asset.get("url") or "").startswith("https://"):
        raise UpdateError("upd_err_not_https")
    if not sha:
        raise UpdateError("upd_err_no_checksum")
    h = hashlib.sha256()
    got = 0
    # A server that sends more than the file is stops being read there,
    # not once the disk is full: it cannot match the signed checksum. The
    # size a site says is never more than DOWNLOAD_MAX either.
    if size > DOWNLOAD_MAX:
        log.error("update: %s says it is %d bytes - more than any release file", asset.get("name"), size)
        raise UpdateError("upd_err_checksum")
    limit = size or DOWNLOAD_MAX
    try:
        with _get_https(asset["url"], stream=True, timeout=30) as r:
            r.raise_for_status()
            total = size or int(r.headers.get("Content-Length") or 0)
            # A file of our own making, never one that was lying there: in a
            # folder others may write to, a file (or a link) put under this
            # name beforehand would be theirs to change after the checksum.
            dest.unlink(missing_ok=True)
            with open(dest, "xb") as fh:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    if cancel is not None and cancel.is_set():
                        raise UpdateError("cancelled")
                    if not chunk:
                        continue
                    got += len(chunk)
                    if got > limit:
                        log.error("update: %s sent more than its %d bytes - stopped",
                                  asset.get("name"), limit)
                        raise UpdateError("upd_err_checksum")
                    fh.write(chunk)
                    h.update(chunk)
                    if progress is not None:
                        progress(got, total)
        if size and got != size:
            raise UpdateError("upd_err_incomplete", got=got, size=size)
        if h.hexdigest() != sha:
            raise UpdateError("upd_err_checksum")
        _VERIFIED[str(dest)] = sha
    except UpdateError:
        dest.unlink(missing_ok=True)
        raise
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise UpdateError("upd_err_download", error=e) from e


def _run_self_check(path: Path, timeout: int) -> tuple[int, str]:
    env = system_env()
    for var in _RUNTIME_VARS:
        env.pop(var, None)
    try:
        proc = subprocess.run(
            [str(path), "--self-check"], env=env, timeout=timeout,
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            errors="replace", check=False)
    except subprocess.TimeoutExpired as e:
        raise UpdateError("upd_err_check_timeout") from e
    except OSError as e:
        raise UpdateError("upd_err_start", error=e) from e
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def self_check(path: Path, version: str, current: Path | None = None,
               timeout: int = SELF_CHECK_TIMEOUT) -> None:
    """Run the new AppImage's own ``--self-check``; raise unless it passes.

    The check also fails where the built-in player cannot load at all - a
    machine without 3D graphics, say - and there the running version fails
    it just the same, while the app itself works through an external
    player. So a failure is accepted when the new version did start (it
    logged its own "starting" line) and the running one fails the check
    too: never worse than now, rather than never updatable."""
    rc, out = _run_self_check(path, timeout)
    if rc == 0:
        return
    tail = " | ".join(out.strip().splitlines()[-3:])
    log.error("update: self-check of %s failed (rc=%s): %s", path, rc, tail)
    started = f"{_bare(version)} starting" in out
    if started and current is not None:
        cur_rc, _ = _run_self_check(current, timeout)
        if cur_rc != 0:
            log.warning("update: the running version fails its self-check "
                        "here as well; accepting the new one, which starts")
            return
    raise UpdateError("upd_err_check_failed")


def install(release: dict, current_version: str,
            progress: Callable[[int, int], None] | None = None,
            cancel: threading.Event | None = None) -> Path:
    """Download, verify, test and put the release's AppImage in place.
    Returns the path of the new file. Raises UpdateError, with nothing
    changed, on any failure."""
    build_check.refuse("updates")
    current = appimage_path()
    cpu = arch()
    if current is None or cpu is None:
        raise UpdateError("upd_err_not_appimage")
    tag = release.get("tag") or ""
    refuse_older(tag)
    asset = pick_asset(release.get("assets") or [], tag, cpu)
    if asset is None:
        raise UpdateError("upd_err_no_asset", cpu=cpu)
    asset = signed_asset(release, asset)
    target = target_path(current, current_version, tag)
    if shared_folder(target.parent):
        # Another account could swap the checked file for one of its own
        # before it runs (the start-up check) or is put in place.
        raise UpdateError("upd_err_folder_shared", folder=str(target.parent))
    part = target.with_name(f".{target.name}.part")
    download(asset, part, progress, cancel)
    try:
        part.chmod(0o755)
        verify_again(part)
        self_check(part, tag, current)
        if cancel is not None and cancel.is_set():
            raise UpdateError("cancelled")
        verify_again(part)
        os.replace(part, target)
    except Exception:
        part.unlink(missing_ok=True)
        raise
    repoint_desktop_entry(target, current)
    log.info("update: installed %s as %s", tag, target)
    return target


def repoint_desktop_entry(new: Path, old: Path | None = None) -> bool:
    """Point our own menu entry at *new* when it names *old* or a file that
    is gone - the AppImage it was written for, deleted after a manual
    upgrade, would leave a dock icon that starts nothing. An entry naming
    another file that still exists is the user's choice and stays, and an
    entry anyone else wrote is never touched."""
    path = desktop_entry.entry_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if "X-dopeIPTV-Generated=true" not in text:
        return False
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("Exec="):
            continue
        try:
            argv = shlex.split(line[len("Exec="):])
        except ValueError:
            return False
        if not argv:
            return False
        named = Path(argv[0])
        if named == new:
            return False
        if named != old and named.exists():
            return False
        # Its arguments stay (%u: the links it opens).
        rest = "".join(f" {a}" for a in argv[1:] if a.startswith("%"))
        lines[i] = "Exec=" + desktop_entry.quote_exec(str(new)) + rest
        try:
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as e:
            log.warning("update: could not repoint %s: %s", path, e)
            return False
        log.info("desktop entry: now launches %s", new)
        return True
    return False


def restart_into(new: Path, pid: int | None = None) -> None:
    """Start *new* once this process (*pid*) has exited, so the old
    instance finishes writing its settings before the new one reads them.
    Gives up after five minutes: an instance that never exits must not get
    a second one started beside it, at some random later moment."""
    pid = pid or os.getpid()
    env = system_env()
    # The file it came from: a tool's menu entry naming that one is the
    # dock's pinned icon, and the new window takes its name (desktop_entry).
    came_from = os.environ.get("APPIMAGE")
    for var in _RUNTIME_VARS:
        env.pop(var, None)
    if came_from:
        env[desktop_entry.RESTARTED_FROM] = came_from
    script = ('n=0; while kill -0 "$0" 2>/dev/null; do '
              'n=$((n+1)); [ "$n" -ge 1500 ] && exit 0; sleep 0.2; done; '
              'exec "$1"')
    subprocess.Popen(
        ["/bin/sh", "-c", script, str(pid), str(new)], env=env,
        start_new_session=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def take_pending_cleanup(old: str, running: Path | None) -> bool:
    """Called by the new version at start with the path the updater left
    behind: remove the old AppImage now that this one has started. Only a
    file that is not the one running and still looks like ours."""
    if not old or running is None:
        return False
    p = Path(old)
    if p == running or not p.is_file():
        return False
    if not (p.name.startswith("dopeIPTV") and p.suffix == ".AppImage"):
        return False
    try:
        p.unlink()
    except OSError as e:
        log.warning("update: could not remove the old %s: %s", p, e)
        return False
    log.info("update: removed the old %s", p)
    return True
