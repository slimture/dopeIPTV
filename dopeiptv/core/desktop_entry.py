"""Install a desktop entry for this app in the user's own data directory.

Why this exists at all: **Wayland ignores setWindowIcon.** GNOME and KDE
take a window's icon from the .desktop file whose name matches the
window's app_id (Qt sets that from ``setDesktopFileName``, so ours is
``dopeiptv.desktop``). Run from a .deb the file is there and the icon is
right; run from an AppImage nothing has ever installed one, so the
taskbar shows the generic placeholder no matter how good the icon inside
the AppImage is.

The AppImage project's own position is that integration belongs to the
user's tooling - appimaged, AppImageLauncher - not to the application,
and that is a fair position: the whole point of the format is one file
that changes nothing outside itself. So this never runs on its own. The
window asks once, remembers the answer, and the same switch in Settings
turns it back off - which removes the file rather than leaving it behind.

``Exec`` points at ``$APPIMAGE``, the path the AppImage runtime exports,
so the entry keeps working when the file is moved or replaced by a newer
version at the same path - and never at the temporary mount, which is
gone the moment the app exits.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .. import APP_NAME
from .log import log

ENTRY_NAME = "dopeiptv.desktop"
# The window's app_id (app.setDesktopFileName). A compositor looks for a
# file of exactly this name, so it is not ours to choose freely.
ICON_NAME = "dopeiptv"


# The menu entry says it opens these links (Exec ... %u).
LINK_MIME = "x-scheme-handler/dopeiptv"

#: The AppImage an update's restart came from (core/selfupdate.restart_into):
#: a tool's entry still naming that file is the one the dock has pinned.
RESTARTED_FROM = "DOPEIPTV_RESTARTED_FROM"
#: A look for this file's own entry reads no more entries than this, and no
#: more of each than ENTRY_MAX bytes.
ENTRIES_MAX = 2000
ENTRY_MAX = 64 * 1024


def _exec_program(text: str) -> str | None:
    """The program an entry's Exec= line starts (past an ``env VAR=x``
    prefix, as some AppImage tools write), or None."""
    group = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            group = line
            continue
        if group != "[Desktop Entry]" or not line.startswith("Exec="):
            continue
        try:
            argv = shlex.split(line[len("Exec="):])
        except ValueError:
            return None
        while argv and (argv[0] == "env" or "=" in argv[0]):
            argv.pop(0)
        return argv[0] if argv else None
    return None


def launched_app_id(env: dict[str, str] | None = None) -> str | None:
    """The desktop id of the entry that started this run, when that entry
    is ours under another name - or None to keep ``dopeiptv``.

    A dock or dash pins the entry it launched from, and that is often not
    ours: Gear Lever, AppImageLauncher and the like each write their own
    copy, named their own way. Started from the icon, GNOME ties the window
    to it by the process it launched. Restarted after an update, by us, it
    has only the window's app_id to go on - "dopeiptv" matched no entry,
    and the new window came up as a stranger beside the pinned icon. GLib
    names the launching entry in GIO_LAUNCHED_DESKTOP_FILE, and the restart
    inherits it, so the window can take that entry's name both times.

    Only an entry whose Exec starts dopeIPTV: the variable is inherited by
    everything below the launch - from a terminal, it names the terminal.
    Without it (KDE, a start from the file manager, the entry it named gone
    with the file it started), the entry that starts this very AppImage -
    or the one an update's restart came from - does (``entry_for_files``)."""
    return launch_choice(env)[0]


def launch_choice(env: dict[str, str] | None = None) -> tuple[str | None, str]:
    """``launched_app_id`` and how it was chosen, for the log: "launcher"
    (the entry that started this run), "file" (an entry that starts this
    AppImage) or "default"."""
    if not sys.platform.startswith("linux") or _in_flatpak():
        return None, "default"
    env = os.environ if env is None else env
    raw = env.get("GIO_LAUNCHED_DESKTOP_FILE") or ""
    path = Path(raw)
    if raw and path.suffix == ".desktop" and path.parent.name == "applications":
        if path.name == ENTRY_NAME:
            return None, "launcher"
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        program = _exec_program(text)
        if program and Path(program).name.lower().startswith("dopeiptv"):
            return path.stem, "launcher"
    found = entry_for_files((env.get("APPIMAGE"), env.get(RESTARTED_FROM)), env)
    return (found, "file") if found else (None, "default")


def _app_dirs(env: dict[str, str]) -> list[Path]:
    home = env.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    dirs = (env.get("XDG_DATA_DIRS")
            or os.pathsep.join(("/usr/local/share", "/usr/share"))).split(os.pathsep)
    return [Path(d) / "applications" for d in [home, *dirs] if d]


def _same_file(a: str, b: Path) -> bool:
    try:
        return Path(a).resolve() == b
    except (OSError, RuntimeError, ValueError):
        return False


def entry_for_files(files, env: dict[str, str] | None = None) -> str | None:
    """The desktop id of an entry, not ours, whose Exec starts one of
    *files* (absolute paths; None and empty skipped): the user's own
    applications folder first, then the system's, each in name order. Ours
    (dopeiptv.desktop) found first, or none at all: None - the window keeps
    "dopeiptv". Bounded: ENTRIES_MAX entries, ENTRY_MAX bytes of each."""
    env = os.environ if env is None else env
    wanted = []
    for f in files:
        if f and os.path.isabs(f):
            try:
                wanted.append(Path(f).resolve())
            except (OSError, RuntimeError, ValueError):
                continue
    if not wanted:
        return None
    read = 0
    for folder in _app_dirs(env):
        try:
            names = sorted(e for e in os.listdir(folder) if e.endswith(".desktop"))
        except OSError:
            continue
        for name in names:
            read += 1
            if read > ENTRIES_MAX:
                return None
            try:
                with open(folder / name, "rb") as fh:
                    text = fh.read(ENTRY_MAX).decode("utf-8", "replace")
            except OSError:
                continue
            program = _exec_program(text)
            if not program or not os.path.isabs(program):
                continue
            if any(_same_file(program, w) for w in wanted):
                return None if name == ENTRY_NAME else name[:-len(".desktop")]
    return None


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME")
                or Path.home() / ".local" / "share")


def entry_path() -> Path:
    return _data_home() / "applications" / ENTRY_NAME


def _in_flatpak() -> bool:
    # A Flatpak ships its own entry and cannot see the host's applications
    # directory anyway, so offering to write one would be a lie.
    return Path("/.flatpak-info").exists()


def system_entry_exists() -> bool:
    """Whether a package (.deb, .rpm) already installed an entry
    system-wide. Then there is nothing missing and nothing to offer."""
    dirs = (os.environ.get("XDG_DATA_DIRS")
            or os.pathsep.join(("/usr/local/share", "/usr/share"))).split(os.pathsep)
    return any((Path(d) / "applications" / ENTRY_NAME).exists()
               for d in dirs if d)


def exec_command() -> str | None:
    """The command the entry should launch, or None if there isn't one
    worth writing down.

    $APPIMAGE is the AppImage's own path, exported by its runtime; it
    survives the file being moved or upgraded in place. Otherwise an
    installed console script (pipx, pip --user) is a real, stable path.
    A source checkout run as ``python -m`` is neither, and gets no
    offer at all rather than an entry that breaks on the next rename."""
    appimage = os.environ.get("APPIMAGE")
    if appimage and Path(appimage).exists():
        return appimage
    exe = shutil.which("dopeiptv")
    if exe:
        return exe
    return None


def can_offer() -> bool:
    """Whether installing an entry would actually fix anything here."""
    return (sys.platform.startswith("linux")
            and not _in_flatpak()
            and not system_entry_exists()
            and not entry_path().exists()
            and exec_command() is not None)


def is_installed() -> bool:
    return entry_path().exists()


def describe() -> str:
    """One line saying exactly where this stands, logged at every start.

    "No icon" has four different causes that look identical from the
    outside: nothing ever offered, something already provides an entry,
    the entry exists but the session has not picked it up, or the entry is
    fine and the ICON file is missing. Guessing between them costs a round
    trip each time. This says which, in one line of the log."""
    icons = _data_home() / "icons" / "hicolor"
    sizes = sorted(p.parent.parent.name
                   for p in icons.glob("*/apps/dopeiptv.png"))
    return ("desktop entry: installed=%s can_offer=%s system_entry=%s "
            "flatpak=%s exec=%s icons=%s path=%s"
            % (is_installed(), can_offer(), system_entry_exists(),
               _in_flatpak(), exec_command(),
               ",".join(sizes) or "NONE", entry_path()))


def quote_exec(cmd: str) -> str:
    # Exec is not a shell command line. A path needing quotes gets them,
    # and inside quotes the spec asks for a backslash before ", `, $ and
    # \\ itself - the last three because a quoted Exec is still expanded
    # for those. The backslash must be doubled FIRST or it escapes the
    # escapes that follow.
    if not any(c in cmd for c in ' \t"\\`$'):
        return cmd
    inner = cmd.replace("\\", "\\\\")
    for ch in ('"', "`", "$"):
        inner = inner.replace(ch, "\\" + ch)
    return f'"{inner}"'


def install() -> bool:
    """Write the entry. Returns whether it is there afterwards."""
    cmd = exec_command()
    if cmd is None:
        return False
    path = entry_path()
    body = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        "Comment=IPTV client with Xtream Codes and EPG\n"
        # %u and the scheme: a dopeiptv:// link opens here (core/commands.py),
        # as with the entry the packages ship - two entries for the same app
        # that disagree about what it accepts is how they start drifting.
        f"Exec={quote_exec(cmd)} %u\n"
        f"Icon={ICON_NAME}\n"
        "Terminal=false\n"
        "Categories=AudioVideo;Video;TV;\n"
        "Keywords=iptv;tv;xtream;epg;\n"
        "StartupWMClass=dopeiptv\n"
        f"MimeType={LINK_MIME};\n"
        # Written by us, for us: the switch in Settings reads this back so
        # it never offers to remove an entry a package manager owns.
        "X-dopeIPTV-Generated=true\n"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        path.chmod(0o644)
    except OSError as e:
        log.warning("desktop entry: could not write %s: %s", path, e)
        return False
    _refresh_database()
    log.info("desktop entry: installed %s -> %s", path, cmd)
    return True


def take_links() -> bool:
    """An entry we wrote before dopeiptv:// links existed gets them: %u on
    its Exec line and the scheme. Ours only; True when it changed."""
    path = entry_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if "X-dopeIPTV-Generated=true" not in text or LINK_MIME in text:
        return False
    lines = []
    for line in text.splitlines():
        if line.startswith("Exec=") and "%u" not in line.split():
            line += " %u"
        lines.append(line)
        if line.startswith("StartupWMClass="):
            lines.append(f"MimeType={LINK_MIME};")
    if not any(ln.startswith("MimeType=") for ln in lines):
        lines.append(f"MimeType={LINK_MIME};")
    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as e:
        log.warning("desktop entry: could not update %s: %s", path, e)
        return False
    _refresh_database()
    log.info("desktop entry: now opens dopeiptv:// links")
    return True


def remove() -> bool:
    """Take the entry away again. Only ever removes our own."""
    path = entry_path()
    try:
        if not path.exists():
            return True
        if "X-dopeIPTV-Generated=true" not in path.read_text(
                encoding="utf-8", errors="replace"):
            log.info("desktop entry: %s was not written by us - left alone",
                     path)
            return False
        path.unlink()
    except OSError as e:
        log.warning("desktop entry: could not remove %s: %s", path, e)
        return False
    _refresh_database()
    log.info("desktop entry: removed %s", path)
    return True


def _refresh_database() -> None:
    """Nudge the menu to notice. Absent on many systems and not required -
    the file itself is what the compositor reads for the icon."""
    exe = shutil.which("update-desktop-database")
    if not exe:
        return
    try:
        subprocess.run([exe, str(entry_path().parent)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        pass
