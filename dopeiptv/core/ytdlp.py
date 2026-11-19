"""yt-dlp: what lets the player open YouTube Live, Twitch and sites like
them. mpv's ytdl hook runs it for such an address; where the libmpv has no
hook (the Flatpak's is built without Lua) the app runs it itself and hands
the player the stream it names (``resolve``).

Where it comes from:

- the system's, on the PATH (a distribution's package, pip, Homebrew);
- or a copy the app keeps in the user's data folder: the official release
  binary for this platform from github.com/yt-dlp/yt-dlp, checked against
  the release's SHA2-256SUMS before it is used, and checked for a newer
  release at most once a day while it is used - YouTube changes often, and
  an old yt-dlp is the usual reason a stream stops opening.

It is never in the app's own packages. The managed copy can be turned off
(``ytdlp_managed``): then only the system's is used and nothing is
downloaded. Requests name the app (``dopeIPTV/<version>``), nothing else.

No Qt: the window runs these on a worker thread.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .. import VERSION
from ._lazy_requests import requests
from .log import log, redact_url
from .xdg import BUNDLE_VARS, system_env

RELEASES = "https://github.com/yt-dlp/yt-dlp/releases"
SUMS = "SHA2-256SUMS"
#: Settings: the app-managed copy on (the default), and when it was last
#: compared with the newest release (seconds since the epoch).
SETTING_MANAGED = "ytdlp_managed"
SETTING_CHECKED = "ytdlp_checked"
CHECK_EVERY = 24 * 3600
#: A release binary is 15-40 MB; past this it is not one.
MAX_BYTES = 200 * 1024 * 1024
_TAG_FILE = "release.txt"
_WRAPPER = "yt-dlp-run"
#: The format the app asks for when it resolves an address itself: picture
#: and sound in one stream (a live stream's HLS always is).
FORMAT = ("best[vcodec!=none][acodec!=none][height<=1080]"
          "/best[vcodec!=none][acodec!=none]/best")

#: Sites whose addresses go through yt-dlp (the host or a subdomain of it).
WEB_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com", "twitch.tv",
             "kick.com", "dailymotion.com", "dai.ly", "vimeo.com", "rumble.com",
             "bilibili.com", "trovo.live")
_TWITCH_PAGES = frozenset({"videos", "directory", "downloads", "jobs", "p",
                           "settings", "subscriptions", "inventory", "wallet",
                           "drops", "search", "turbo", "friends", "messages"})


class YtdlpError(Exception):
    """*reason*: platform (no release binary for this computer), release
    (the newest release could not be found), download, checksum, runs (the
    downloaded binary does not start), busy (the copy is in use), missing,
    failed (yt-dlp could not open the address; *detail* says why)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


# -- the addresses ----------------------------------------------------------------

def _host(url: str) -> str:
    try:
        u = urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    if u.scheme.lower() not in ("http", "https"):
        return ""
    return (u.hostname or "").lower().rstrip(".")


def is_web_stream(url: str) -> bool:
    """An address of YouTube, Twitch or a site like them (yt-dlp's)."""
    host = _host(url)
    return bool(host) and any(host == h or host.endswith("." + h) for h in WEB_HOSTS)


def looks_live(url: str) -> bool:
    """Whether the address itself says it is a live stream: a Twitch or
    Kick channel, a YouTube channel's /live or a /live/<id> link."""
    host = _host(url)
    if not host:
        return False
    parts = [p for p in urlsplit(url).path.split("/") if p]
    if host == "twitch.tv" or host.endswith(".twitch.tv"):
        return len(parts) == 1 and parts[0].lower() not in _TWITCH_PAGES
    if host == "kick.com" or host.endswith(".kick.com"):
        return len(parts) == 1
    if host == "youtube.com" or host.endswith(".youtube.com"):
        if len(parts) >= 2 and parts[0] == "live":
            return True
        return bool(parts) and parts[-1] == "live" and len(parts) in (2, 3)
    return False


def display_name(url: str) -> str:
    """A name for an address until the stream says its own:
    ``twitch.tv/somechannel``."""
    host = _host(url)
    if not host:
        return str(url or "")
    if host.startswith(("www.", "m.")):
        host = host.split(".", 1)[1]
    u = urlsplit(url)
    path = u.path.rstrip("/")
    if not path and u.query:
        path = "?" + u.query
    text = host + path
    return text if len(text) <= 80 else text[:79] + "…"


def mpv_address(url: str) -> str:
    """The address as mpv's ytdl hook takes it straight away (instead of
    trying the web page as a stream first)."""
    return url if url.startswith("ytdl://") else "ytdl://" + url


# -- where it is ---------------------------------------------------------------------

def data_dir() -> Path:
    """The app's own data folder: ~/.local/share/dopeiptv (XDG_DATA_HOME),
    ~/Library/Application Support/dopeIPTV, %LOCALAPPDATA%\\dopeiptv."""
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")) / "dopeiptv"
    if sys.platform == "darwin":
        return Path(os.path.expanduser("~/Library/Application Support/dopeIPTV"))
    return Path(os.environ.get("XDG_DATA_HOME")
                or os.path.expanduser("~/.local/share")) / "dopeiptv"


def managed_dir(base: str | Path | None = None) -> Path:
    return Path(base) if base else data_dir() / "yt-dlp"


def managed_path(base: str | Path | None = None, system: str | None = None) -> Path:
    exe = "yt-dlp.exe" if (system or sys.platform) == "win32" else "yt-dlp"
    return managed_dir(base) / exe


def _is_musl() -> bool:
    try:
        return platform.libc_ver()[0] != "glibc" and any(
            Path("/lib").glob("ld-musl-*.so.1"))
    except OSError:
        return False


def asset_name(system: str | None = None, machine: str | None = None,
               musl: bool | None = None) -> str | None:
    """The release file for this computer, or None where yt-dlp ships no
    binary for it."""
    system = system or sys.platform
    m = (platform.machine() if machine is None else machine).lower()
    if system.startswith("linux"):
        if musl is None:
            musl = _is_musl()
        base = "yt-dlp_musllinux" if musl else "yt-dlp_linux"
        if m in ("x86_64", "amd64"):
            return base
        if m in ("aarch64", "arm64"):
            return base + "_aarch64"
        if m.startswith("armv7") and not musl:
            return "yt-dlp_linux_armv7l"
        return None
    if system == "darwin":
        return "yt-dlp_macos"
    if system == "win32":
        if m in ("amd64", "x86_64"):
            return "yt-dlp.exe"
        if m in ("arm64", "aarch64"):
            return "yt-dlp_arm64.exe"
        if m in ("x86", "i386", "i686"):
            return "yt-dlp_x86.exe"
    return None


@dataclass(frozen=True)
class Found:
    path: str
    managed: bool


def _runnable(p: Path) -> bool:
    try:
        return p.is_file() and (sys.platform == "win32" or os.access(p, os.X_OK))
    except OSError:
        return False


def find(managed_on: bool = True, base: str | Path | None = None,
         which: Callable[[str], str | None] = shutil.which) -> Found | None:
    """The yt-dlp to use: the app's copy (kept up to date) while the
    managed copy is on and there is one, else the system's on the PATH."""
    if managed_on:
        p = managed_path(base)
        if _runnable(p):
            return Found(str(p), True)
    system = which("yt-dlp")
    return Found(system, False) if system else None


def installed_tag(base: str | Path | None = None) -> str:
    try:
        return (managed_dir(base) / _TAG_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def update_due(last_checked: Any, now: float | None = None) -> bool:
    """At most once a day (and again when the clock went back)."""
    try:
        last = float(last_checked or 0)
    except (TypeError, ValueError):
        last = 0.0
    now = time.time() if now is None else now
    return last <= 0 or now - last >= CHECK_EVERY or now < last


# -- running it ----------------------------------------------------------------------

def _run_kwargs() -> dict[str, Any]:
    kw: dict[str, Any] = {"env": system_env(), "stdin": subprocess.DEVNULL,
                          "capture_output": True, "text": True}
    if sys.platform == "win32":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kw


def version_of(path: str, run: Callable[..., Any] = subprocess.run,
               timeout: float = 60.0) -> str:
    """``yt-dlp --version`` - its release ("2025.10.22"), or "" when it
    does not run."""
    try:
        done = run([path, "--version"], timeout=timeout, **_run_kwargs())
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        log.info("yt-dlp at %s does not run: %s", path, type(e).__name__)
        return ""
    if getattr(done, "returncode", 1) != 0:
        return ""
    out = str(getattr(done, "stdout", "") or "").strip().splitlines()
    return out[0].strip()[:40] if out else ""


def _last_error(text: str) -> str:
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    for ln in reversed(lines):
        if ln.startswith("ERROR:"):
            return ln[6:].strip()[:300]
    return lines[-1][:300] if lines else ""


def resolve(path: str, url: str, run: Callable[..., Any] = subprocess.run,
            timeout: float = 60.0) -> dict[str, Any]:
    """Ask yt-dlp for the stream behind *url* (one stream with picture and
    sound): {"url", "title", "live", "headers"}. For a libmpv without the
    ytdl hook."""
    cmd = [path, "-J", "--no-playlist", "--no-warnings", "-f", FORMAT, "--", url]
    try:
        done = run(cmd, timeout=timeout, **_run_kwargs())
    except subprocess.TimeoutExpired as e:
        raise YtdlpError("failed", "timeout") from e
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        raise YtdlpError("missing", type(e).__name__) from e
    if getattr(done, "returncode", 1) != 0:
        raise YtdlpError("failed", _last_error(getattr(done, "stderr", "")))
    try:
        info = json.loads(getattr(done, "stdout", "") or "")
    except ValueError as e:
        raise YtdlpError("failed", "no answer") from e
    if not isinstance(info, dict):
        raise YtdlpError("failed", "no answer")
    stream = str(info.get("url") or "")
    if not stream.lower().startswith(("http://", "https://")):
        raise YtdlpError("failed", "no single stream")
    headers = info.get("http_headers")
    title = " ".join(str(info.get("title") or "").split())
    who = " ".join(str(info.get("uploader") or info.get("channel") or "").split())
    if who and title and who.casefold() not in title.casefold():
        title = f"{who} - {title}"
    return {"url": stream, "title": (title or who)[:200],
            "live": bool(info.get("is_live")),
            "headers": {str(k): str(v) for k, v in headers.items()}
            if isinstance(headers, dict) else {}}


def wrapper_text(real: str, env: dict[str, str]) -> str:
    """A shell script that runs *real* with the system's library paths, not
    the app bundle's (a frozen build's LD_LIBRARY_PATH and the like, which
    mpv's ytdl hook would otherwise pass on): *env* is what to give them,
    a variable missing from it is unset."""
    lines = ["#!/bin/sh",
             "# Written by dopeIPTV: yt-dlp without the app bundle's library paths."]
    for var in BUNDLE_VARS:
        if var in env:
            lines.append(f"{var}={shlex.quote(env[var])}; export {var}")
        else:
            lines.append(f"unset {var}")
    lines.append(f'exec {shlex.quote(real)} "$@"')
    return "\n".join(lines) + "\n"


def hook_path(found: Found, base: str | Path | None = None,
              frozen: bool | None = None) -> str:
    """What mpv's ytdl hook should run: yt-dlp itself, or - in a frozen
    build on Linux and macOS - a small script that clears the bundle's
    library paths first."""
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if not frozen or sys.platform == "win32":
        return found.path
    folder = managed_dir(base)
    target = folder / _WRAPPER
    text = wrapper_text(found.path, system_env())
    try:
        if not (target.is_file() and target.read_text(encoding="utf-8") == text):
            folder.mkdir(parents=True, exist_ok=True)
            tmp = folder / (_WRAPPER + ".new")
            tmp.write_text(text, encoding="utf-8")
            os.chmod(tmp, 0o755)
            os.replace(tmp, target)
    except OSError as e:
        log.info("yt-dlp: no wrapper script (%s) - using it directly", e)
        return found.path
    return str(target)


# -- the managed copy ----------------------------------------------------------------

def _headers() -> dict[str, str]:
    return {"User-Agent": f"dopeIPTV/{VERSION}"}


def _get(url: str, **kw: Any) -> Any:
    """GET *url* with its redirects followed by hand, each https - a
    release's file moves on to GitHub's storage, never to plain http
    (selfupdate._get_https); only the app's name and version with each."""
    from .selfupdate import UpdateError, _get_https
    try:
        return _get_https(url, headers=lambda _url: _headers(), get=requests.get, **kw)
    except UpdateError as e:
        raise YtdlpError("download", str(e)) from e


def parse_sums(text: str) -> dict[str, str]:
    """SHA2-256SUMS: file name -> its SHA-256 (lower-case hex)."""
    out: dict[str, str] = {}
    for line in str(text or "").splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            out[parts[-1].lstrip("*")] = parts[0].lower()
    return out


def latest_tag(releases: str = RELEASES, get: Callable[..., Any] | None = None) -> str:
    """The newest release's tag, from where GitHub's "latest" link leads
    (no API call, so no API rate limit)."""
    get = get or requests.get
    try:
        r = get(f"{releases}/latest", allow_redirects=False, timeout=20,
                headers=_headers())
    except requests.RequestException as e:
        raise YtdlpError("release", type(e).__name__) from e
    m = re.search(r"/releases/tag/([A-Za-z0-9._-]+)", str(r.headers.get("Location") or ""))
    if not m:
        raise YtdlpError("release", f"HTTP {r.status_code}")
    return m.group(1)


def install(base: str | Path | None = None, *, releases: str = RELEASES,
            tag: str | None = None, asset: str | None = None,
            run: Callable[..., Any] = subprocess.run,
            progress: Callable[[int, int], None] | None = None,
            cancelled: Callable[[], bool] | None = None) -> str:
    """Download the release binary into the managed folder: checked
    against the release's SHA2-256SUMS, then tried (``--version``), then
    put in place of the old copy in one step. Returns the release's tag."""
    asset = asset or asset_name()
    if not asset:
        raise YtdlpError("platform", f"{sys.platform} {platform.machine()}")
    tag = tag or latest_tag(releases)
    base_url = f"{releases}/download/{tag}"
    try:
        r = _get(f"{base_url}/{SUMS}", timeout=20)
    except requests.RequestException as e:
        raise YtdlpError("download", type(e).__name__) from e
    if r.status_code != 200:
        raise YtdlpError("download", f"{SUMS}: HTTP {r.status_code}")
    want = parse_sums(r.text).get(asset)
    if not want:
        raise YtdlpError("checksum", f"{asset} is not in {SUMS}")
    folder = managed_dir(base)
    folder.mkdir(parents=True, exist_ok=True)
    final = managed_path(base)
    tmp = folder / f"{final.stem}.download{final.suffix}"
    try:
        digest = hashlib.sha256()
        size = 0
        try:
            with _get(f"{base_url}/{asset}", stream=True, timeout=30) as resp:
                if resp.status_code != 200:
                    raise YtdlpError("download", f"HTTP {resp.status_code}")
                total = int(resp.headers.get("Content-Length") or 0)
                with open(tmp, "wb") as fh:
                    for chunk in resp.iter_content(256 * 1024):
                        if cancelled is not None and cancelled():
                            raise YtdlpError("cancelled")
                        if not chunk:
                            continue
                        fh.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise YtdlpError("download", "too large")
                        if progress is not None:
                            progress(size, total)
        except requests.RequestException as e:
            raise YtdlpError("download", type(e).__name__) from e
        if digest.hexdigest() != want:
            raise YtdlpError("checksum", asset)
        os.chmod(tmp, os.stat(tmp).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        if not version_of(str(tmp), run):
            raise YtdlpError("runs", asset)
        try:
            os.replace(tmp, final)
        except PermissionError as e:
            raise YtdlpError("busy", str(final)) from e
        (folder / _TAG_FILE).write_text(tag + "\n", encoding="utf-8")
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    log.info("yt-dlp %s installed at %s (%d bytes, checksum checked)", tag, final, size)
    return tag


def update(base: str | Path | None = None, *, releases: str = RELEASES,
           run: Callable[..., Any] = subprocess.run,
           progress: Callable[[int, int], None] | None = None) -> str | None:
    """The managed copy brought to the newest release. The new tag when it
    was updated, None when it was already the newest."""
    tag = latest_tag(releases)
    if tag == installed_tag(base) and _runnable(managed_path(base)):
        return None
    return install(base, releases=releases, tag=tag, run=run, progress=progress)


def describe(found: Found | None, version: str = "") -> str:
    """For the log: which yt-dlp, never more."""
    if found is None:
        return "none"
    return f"{'managed' if found.managed else 'system'} {version or '?'} ({redact_url(found.path)})"
