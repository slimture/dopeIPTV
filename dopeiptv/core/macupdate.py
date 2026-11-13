"""Update the notarised dopeIPTV.app on macOS, in place.

The .dmg on a release is signed with Solecraft AB's Developer ID and
notarised by Apple, so the app can take the next one itself - checked at
every step, and nothing in Applications changes until the end:

1. download the release's .dmg for this build's CPU, checking its size and
   SHA-256 (``selfupdate.download``);
2. mount it (no Finder window), and accept the app in it only when its
   signature holds, it is Solecraft AB's (Team ID ``6QR76TBT4J``, the same
   as this copy's) and Gatekeeper takes it as notarised;
3. copy it beside this one as a hidden ``.dopeIPTV-update.app`` and check
   the copy's signature again;
4. once this process has exited, swap the two and open the new one - or,
   when the user restarts later, swap them as the app quits.

Only a copy this can safely replace updates itself: one of our signed
builds, in a folder the user may write to, not run from the .dmg itself
or from where macOS put a copy it moved aside ("App Translocation"). Any
other is offered the download page. No Qt here; the UI drives it from a
worker.
"""
from __future__ import annotations

import functools
import os
import plistlib
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from .log import log
from .selfupdate import UpdateError, download
from .xdg import system_env

TEAM_ID = "6QR76TBT4J"                  # Solecraft AB's Developer ID
APP_NAME = "dopeIPTV.app"
STAGED = ".dopeIPTV-update.app"
ASIDE = ".dopeIPTV-previous.app"
# The question asked once the new version is ready (i18n key), and what the
# progress dialog says while it is checked after the download.
DONE_KEY = "update_done_restart"
VERIFY_KEY: str | None = "update_verifying_signature"
_TIMEOUT = 300


def app_bundle() -> Path | None:
    """The running dopeIPTV.app, or None (not macOS, not a built app)."""
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    for p in exe.parents:
        if p.suffix == ".app":
            return p
    return None


def arch() -> str | None:
    """This build's CPU as the .dmg's name spells it (the bundled Python's)."""
    plat = sysconfig.get_platform().lower()
    if plat.endswith("arm64"):
        return "arm64"
    if plat.endswith("x86_64") or plat.endswith("intel"):
        return "x86_64"
    return None


@functools.lru_cache(maxsize=8)
def _team_id(app: Path) -> str:
    """The Team ID an app is signed with ("" when unsigned or ad hoc)."""
    try:
        out = subprocess.run(["codesign", "-dv", "--verbose=2", str(app)], capture_output=True,
                             text=True, timeout=60, check=False, env=system_env())
    except (OSError, subprocess.TimeoutExpired):
        return ""
    m = re.search(r"^TeamIdentifier=(\S+)", out.stderr or "", re.M)
    return m.group(1) if m and m.group(1) != "not" else ""


def can_self_update() -> bool:
    app = app_bundle()
    if app is None or arch() is None:
        return False
    # Moved aside by macOS (opened from Downloads unmoved), or run from the
    # read-only .dmg: nothing here to replace.
    if "/AppTranslocation/" in str(app):
        return False
    if not (os.access(app.parent, os.W_OK) and os.access(app, os.W_OK)):
        return False
    return _team_id(app) == TEAM_ID


def _bare(version: str) -> str:
    return version.strip().lstrip("vV")


def pick_asset(assets: list[dict], version: str, cpu: str) -> dict | None:
    """The release asset holding the .dmg for *cpu*."""
    want = f"dopeIPTV-{_bare(version)}-macOS-{cpu}.dmg"
    for a in assets:
        if a.get("name") == want and a.get("url"):
            return a
    return None


def _run(argv: list[str], what: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT,
                              check=False, env=system_env())
    except (OSError, subprocess.TimeoutExpired) as e:
        raise UpdateError("upd_err_mac_check", step=what) from e


def verify_app(app: Path) -> None:
    """Raise unless *app* is intact, Solecraft AB's, and notarised."""
    r = _run(["codesign", "--verify", "--deep", "--strict", str(app)], "codesign")
    if r.returncode != 0:
        log.error("update: signature of %s does not hold: %s", app, (r.stderr or "").strip()[-300:])
        raise UpdateError("upd_err_mac_check", step="codesign")
    team = _team_id(app)
    if team != TEAM_ID:
        log.error("update: %s is signed by team %r, not %s", app, team, TEAM_ID)
        raise UpdateError("upd_err_mac_check", step="Team ID")
    r = _run(["spctl", "--assess", "--type", "execute", "--verbose=2", str(app)], "spctl")
    said = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0 or "Notarized Developer ID" not in said:
        log.error("update: Gatekeeper on %s: %s", app, said.strip()[-300:])
        raise UpdateError("upd_err_mac_check", step="Gatekeeper")


def _mount(dmg: Path) -> Path:
    r = _run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-plist", str(dmg)], "hdiutil")
    if r.returncode != 0:
        log.error("update: hdiutil attach %s: %s", dmg, (r.stderr or "").strip()[-300:])
        raise UpdateError("upd_err_mac_check", step="hdiutil")
    try:
        entities = plistlib.loads(r.stdout.encode()).get("system-entities", [])
    except Exception as e:
        raise UpdateError("upd_err_mac_check", step="hdiutil") from e
    for ent in entities:
        if ent.get("mount-point"):
            return Path(ent["mount-point"])
    raise UpdateError("upd_err_mac_check", step="hdiutil")


def _detach(mount: Path) -> None:
    try:
        subprocess.run(["hdiutil", "detach", "-quiet", str(mount)], capture_output=True,
                       timeout=60, check=False, env=system_env())
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("update: could not detach %s: %s", mount, e)


def install(release: dict, current_version: str,
            progress: Callable[[int, int], None] | None = None,
            cancel: threading.Event | None = None) -> Path:
    """Download and check the release's .dmg, and put the app from it
    beside this one, checked again. Returns the staged app; the swap waits
    for :func:`restart_into` or :func:`on_exit`. Raises UpdateError, with
    nothing in place changed, on any failure."""
    app, cpu = app_bundle(), arch()
    if app is None or cpu is None or not can_self_update():
        raise UpdateError("upd_err_not_installed")
    tag = release.get("tag") or ""
    asset = pick_asset(release.get("assets") or [], tag, cpu)
    if asset is None:
        raise UpdateError("upd_err_no_asset", cpu=cpu)
    work = Path(tempfile.mkdtemp(prefix="dopeIPTV-update-"))
    staged = app.parent / STAGED
    try:
        dmg = work / asset["name"]
        download(asset, dmg, progress, cancel)
        mount = _mount(dmg)
        try:
            new = mount / APP_NAME
            if not new.is_dir():
                raise UpdateError("upd_err_mac_check", step=APP_NAME)
            verify_app(new)
            if cancel is not None and cancel.is_set():
                raise UpdateError("cancelled")
            shutil.rmtree(staged, ignore_errors=True)
            r = _run(["ditto", str(new), str(staged)], "ditto")
            if r.returncode != 0:
                raise UpdateError("upd_err_mac_check", step="ditto")
        finally:
            _detach(mount)
        r = _run(["codesign", "--verify", "--deep", "--strict", str(staged)], "codesign")
        if r.returncode != 0:
            raise UpdateError("upd_err_mac_check", step="codesign")
    except Exception:
        shutil.rmtree(staged, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    log.info("update: %s ready beside %s", tag, app)
    return staged


# Waits for the app to exit, then swaps the staged app in - putting the old
# one back if that fails - and opens whichever is in place when asked to.
_SWAP = ('n=0; while kill -0 "$0" 2>/dev/null; do '
         'n=$((n+1)); [ "$n" -ge 1500 ] && exit 0; sleep 0.2; done; '
         'app="$1"; new="$2"; aside="$3"; reopen="$4"; '
         '[ -d "$new" ] || exit 1; rm -rf "$aside"; '
         'if mv "$app" "$aside"; then '
         '  if mv "$new" "$app"; then rm -rf "$aside"; else mv "$aside" "$app"; fi; '
         'fi; '
         '[ "$reopen" = 1 ] && open "$app"; exit 0')


def _swap_after_exit(staged: Path, reopen: bool, pid: int | None = None) -> None:
    app = app_bundle()
    if app is None:
        return
    subprocess.Popen(
        ["/bin/sh", "-c", _SWAP, str(pid or os.getpid()), str(app), str(staged),
         str(app.parent / ASIDE), "1" if reopen else "0"],
        env=system_env(), start_new_session=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def restart_into(staged: Path, pid: int | None = None) -> None:
    """Swap in *staged* once this process has exited, and open it."""
    _swap_after_exit(staged, True, pid)
    log.info("update: the new version replaces this one when it has quit")


def on_exit(staged: Path, pid: int | None = None) -> None:
    """Restart later: swap in *staged* when the app quits, without opening
    it - "is installed" holds from the next start."""
    _swap_after_exit(staged, False, pid)


def cleanup() -> None:
    """At start: a staged app no swap took (the app was killed, say) goes;
    the next update stages its own."""
    app = app_bundle()
    if app is None:
        return
    for name in (STAGED, ASIDE):
        shutil.rmtree(app.parent / name, ignore_errors=True)
