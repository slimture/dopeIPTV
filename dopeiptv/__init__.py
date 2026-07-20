"""dopeIPTV - an elegant IPTV client for Xtream Codes with EPG."""

__version__ = "1.0.0"
# Keep the two in lock-step - VERSION drives the About dialog and the
# AppImage/.deb/.dmg artifact names, __version__ drives the wheel/sdist.
# VERSION stays a clean semver (the update-check parses it); BUILD_VERSION
# adds a git identifier for builds run from a checkout, so every commit on a
# dev branch is tellable apart in the log and About without touching semver.
VERSION = __version__
APP_NAME = "dopeIPTV"
ORG = "dopeiptv"


def _build_id() -> str:
    import os
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not os.path.isdir(os.path.join(root, ".git")):
        return ""   # packaged build: no git, so just the clean version
    try:
        out = subprocess.run(
            ["git", "-C", root, "describe", "--tags", "--always", "--dirty"],
            capture_output=True, text=True, timeout=2)
        return out.stdout.strip()
    except Exception:
        return ""


_bid = _build_id()
# e.g. "0.6.5 (v0.6.4-73-gab12cd)" from a checkout, or "0.6.5" when packaged.
BUILD_VERSION = f"{VERSION} ({_bid})" if _bid else VERSION
