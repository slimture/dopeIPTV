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

import hashlib
import os
import platform
import re
import shlex
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from . import desktop_entry
from ._lazy_requests import requests
from .log import log
from .xdg import system_env

# Set by the AppImage runtime for the process it starts. A child AppImage
# gets its own; ours must not leak into the self-check of the new file.
_RUNTIME_VARS = ("APPIMAGE", "APPDIR", "ARGV0", "OWD")
SELF_CHECK_TIMEOUT = 120


class UpdateError(Exception):
    """The update stopped; the message says why, and nothing was changed."""


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


def _expected_sha256(asset: dict) -> str | None:
    digest = str(asset.get("digest") or "")
    m = re.fullmatch(r"sha256:([0-9a-fA-F]{64})", digest)
    return m.group(1).lower() if m else None


def download(asset: dict, dest: Path,
             progress: Callable[[int, int], None] | None = None,
             cancel: threading.Event | None = None) -> None:
    """Fetch *asset* to *dest*, verifying size and digest. Removes a partial
    or mismatching file before raising."""
    size = int(asset.get("size") or 0)
    sha = _expected_sha256(asset)
    h = hashlib.sha256()
    got = 0
    try:
        with requests.get(asset["url"], stream=True, timeout=30,
                          headers={"User-Agent": "dopeIPTV"}) as r:
            r.raise_for_status()
            total = size or int(r.headers.get("Content-Length") or 0)
            with open(dest, "wb") as fh:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    if cancel is not None and cancel.is_set():
                        raise UpdateError("cancelled")
                    if not chunk:
                        continue
                    fh.write(chunk)
                    h.update(chunk)
                    got += len(chunk)
                    if progress is not None:
                        progress(got, total)
        if size and got != size:
            raise UpdateError(f"download incomplete ({got} of {size} bytes)")
        if sha and h.hexdigest() != sha:
            raise UpdateError("download corrupted (checksum mismatch)")
        if not sha:
            log.warning("update: release lists no SHA-256 for %s; size "
                        "checked only", asset.get("name"))
    except UpdateError:
        dest.unlink(missing_ok=True)
        raise
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise UpdateError(f"download failed: {e}") from e


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
        raise UpdateError("the new version did not finish its start-up "
                          "check") from e
    except OSError as e:
        raise UpdateError(f"the new version could not be started: {e}") from e
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
    raise UpdateError("the new version failed its start-up check")


def install(release: dict, current_version: str,
            progress: Callable[[int, int], None] | None = None,
            cancel: threading.Event | None = None) -> Path:
    """Download, verify, test and put the release's AppImage in place.
    Returns the path of the new file. Raises UpdateError, with nothing
    changed, on any failure."""
    current = appimage_path()
    cpu = arch()
    if current is None or cpu is None:
        raise UpdateError("not running from an AppImage")
    tag = release.get("tag") or ""
    asset = pick_asset(release.get("assets") or [], tag, cpu)
    if asset is None:
        raise UpdateError(f"the release has no AppImage for {cpu}")
    target = target_path(current, current_version, tag)
    part = target.with_name(f".{target.name}.part")
    download(asset, part, progress, cancel)
    try:
        part.chmod(0o755)
        self_check(part, tag, current)
        if cancel is not None and cancel.is_set():
            raise UpdateError("cancelled")
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
        lines[i] = "Exec=" + desktop_entry.quote_exec(str(new))
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
    instance finishes writing its settings before the new one reads them."""
    pid = pid or os.getpid()
    env = system_env()
    for var in _RUNTIME_VARS:
        env.pop(var, None)
    script = ('while kill -0 "$0" 2>/dev/null; do sleep 0.2; done; '
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
