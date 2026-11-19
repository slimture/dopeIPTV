"""Nerd for recordings: a file name template, a command run when a
recording is done, and checks for both and for the folder.

    {title} {channel} {date} {time} {year} {month} {day} {hour} {minute} {weekday}

A "/" in a template makes folders under the recording folder
("{channel}/{title} {date}"); each part is made safe for a file name, and
nothing can lead outside the recording folder (".." and absolute parts are
dropped). The command is split like a shell would split it but never run
through one: placeholders are filled in per argument, so a title with
quotes or ; in it is only ever text.

One program does read its arguments as a shell: Windows' cmd.exe, which
Windows itself puts in front of a batch file (.bat, .cmd). For such a
command (``through_cmd``) a title or a channel's name - a provider's words
- goes without the quote that would end its argument, and without a % that
would put a variable's value in its place; every argument that holds a
character cmd.exe reads (& | < > ^ ( ) % !) is put in quotes, as one with a
space always was; and a recording whose own file name would be expanded is
not handed to the command at all (``post_command``).
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from datetime import datetime

from .log import log
from .privacy import for_log
from .nerd_check import FAIL, INFO, OK, WARN, Check

DEFAULT_TEMPLATE = "{title} {date} {time}"
FIELDS = ("title", "channel", "date", "time", "year", "month", "day", "hour",
          "minute", "weekday", "path", "folder", "file")
_NAME_FIELDS = FIELDS[:10]
# Half a surrogate pair too (JSON can carry one): no file system takes it.
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f\ud800-\udfff]')
# A title or a channel's name is cut at this many characters, as
# safe_filename cuts a title; and no part of the path comes near a file
# system's 255 bytes a name - room is left for the " (12)" and the extension
# a recording's file adds (any part may be the last). Too long, the title
# gives way first, so the template's date and time stay.
NAME_CHARS = 120
PART_BYTES = 255 - 16


#: The names Windows keeps for its devices, whatever comes after a dot.
_WINDOWS_DEVICES = frozenset(
    ["CON", "PRN", "AUX", "NUL"] + [f"{d}{n}" for d in ("COM", "LPT") for n in "123456789"])


def not_a_device(name: str) -> str:
    """*name*, or "_name" when Windows would open one of its devices for it
    (CON, NUL, COM1... - "NUL.ts" too): a recording of a channel called so
    went nowhere, and its folder could not be made."""
    if name.split(".", 1)[0].strip().upper() in _WINDOWS_DEVICES:
        return "_" + name
    return name


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", _UNSAFE.sub("", text)).strip()


def _safe_part(text: str) -> str:
    part = _clean(text)
    raw = part.encode("utf-8", "ignore")
    if len(raw) > PART_BYTES:
        part = raw[:PART_BYTES].decode("utf-8", "ignore")
    part = part.strip().strip(".").strip()
    return not_a_device(part) if part else part


def _pieces(template: str, values: dict[str, str]) -> list[str]:
    text = re.sub(r"\{(\w*)\}", lambda m: values.get(m.group(1), ""), template or "")
    return re.split(r"[\\/]+", text)


def fields(title: str, channel: str = "", when: datetime | None = None) -> dict[str, str]:
    w = when or datetime.now()
    return {"title": title or "", "channel": channel or "", "date": w.strftime("%Y-%m-%d"),
            "time": w.strftime("%H.%M"), "year": w.strftime("%Y"), "month": w.strftime("%m"),
            "day": w.strftime("%d"), "hour": w.strftime("%H"), "minute": w.strftime("%M"),
            "weekday": w.strftime("%A")}


def unknown_fields(template: str) -> list[str]:
    """Placeholders in *template* the app does not know."""
    return sorted({m for m in re.findall(r"\{(\w*)\}", template or "") if m not in _NAME_FIELDS})


def render_name(template: str, title: str, channel: str = "",
                when: datetime | None = None) -> str:
    """A recording's path under the recording folder, without extension:
    the template filled in, part by part made safe and short enough for any
    file system. Empty parts go, and an empty result falls back to the
    default template."""
    # A "/" in a title ("AC/DC Live", "21/11") is not a folder: only the
    # template's own are.
    values = {k: re.sub(r"[\\/]+", "-", v) for k, v in fields(title, channel, when).items()}
    for key in ("title", "channel"):
        values[key] = values[key][:NAME_CHARS]
    while values["title"]:
        over = max(len(_clean(p).encode("utf-8")) for p in _pieces(template, values)) - PART_BYTES
        if over <= 0:
            break
        values["title"] = values["title"][:-(over // 4 + 1)]
    parts = [_safe_part(p) for p in _pieces(template, values)]
    parts = [p for p in parts if p and p not in (".", "..")]
    if not parts:
        if (template or "") != DEFAULT_TEMPLATE:
            return render_name(DEFAULT_TEMPLATE, title, channel, when)
        return _safe_part((title or "")[:NAME_CHARS]) or "recording"
    return os.path.join(*parts)


def _split(command: str, windows: bool) -> list[str]:
    """The command's words. On Windows as its programs read a command
    line: only double quotes group, also inside a word (--ini="C:\\My
    dir\\x.ini"), and they are not part of it - shlex's non-POSIX mode kept
    them, so "C:\\Program Files\\x.exe" was looked for with its quotes and
    never found; backslashes are path separators, not escapes."""
    if not windows:
        return shlex.split(command, posix=True)
    lex = shlex.shlex(command, posix=True)
    lex.whitespace_split = True
    lex.commenters = ""
    lex.quotes = '"'
    lex.escape = ""
    return list(lex)


#: What cmd.exe reads as more than text in an argument that is not in quotes.
_CMD_READS = frozenset("&|<>^()%!")
#: The variables cmd.exe makes up itself, beside the environment's.
_CMD_OWN = frozenset({"CD", "DATE", "TIME", "RANDOM", "ERRORLEVEL", "CMDEXTVERSION",
                      "CMDCMDLINE", "HIGHESTNUMANODENUMBER", "__CD__", "__APPDIR__",
                      "FIRMWARE_TYPE"})


def through_cmd(program: str) -> bool:
    """Whether Windows runs *program* through cmd.exe: a batch file (.bat,
    .cmd - Windows starts cmd.exe for one by itself) or cmd.exe as named."""
    name = re.split(r"[\\/]", (program or "").strip().strip('"'))[-1].lower()
    return name.endswith((".bat", ".cmd")) or name in ("cmd", "cmd.exe")


def _expansions(value: str, env: dict[str, str] | None = None) -> list[str]:
    """The variables cmd.exe would put the value of in place of a part of
    *value*: each %NAME% (or !NAME!) of a variable there is. One there is
    not stays as written on a command line."""
    known = os.environ if env is None else env
    names = {k.upper() for k in known} | _CMD_OWN
    found = []
    for mark in "%!":
        # Between each two marks next to each other, as cmd.exe looks.
        for name in value.split(mark)[1:-1]:
            if name.split(":", 1)[0].upper() in names:
                found.append(mark + name.split(":", 1)[0].upper())
    return found


def _expanded_by_cmd(value: str, env: dict[str, str] | None = None) -> bool:
    return bool(_expansions(value, env))


def _words_for_cmd(text: str, env: dict[str, str] | None = None) -> str:
    """A provider's words as an argument cmd.exe reads: without the quote
    that would end the argument, without control characters (a new line is
    a new command), and - when cmd.exe would expand them - without their
    % and !."""
    out = "".join(c for c in (text or "") if c != '"' and ord(c) >= 32 and ord(c) != 127)
    if _expanded_by_cmd(out, env):
        out = out.replace("%", "").replace("!", "")
    return out


def _quoted(arg: str) -> str:
    """*arg* in quotes, as subprocess.list2cmdline quotes one with a space."""
    out, slashes = ['"'], 0
    for c in arg:
        if c == "\\":
            slashes += 1
            continue
        if c == '"':
            out.append("\\" * (slashes * 2) + '\\"')
        else:
            out.append("\\" * slashes + c)
        slashes = 0
    out.append("\\" * (slashes * 2) + '"')
    return "".join(out)


def cmd_line(argv: list[str]) -> str:
    """*argv* as the one line Windows hands a program, for one cmd.exe
    reads: what subprocess makes of the list, but with every argument that
    holds a character cmd.exe reads in quotes too. Unquoted, "A&B.ts" was
    the argument A and the command B.ts."""
    return " ".join(_quoted(a) if (not a or any(c in _CMD_READS or c in " \t" for c in a))
                    else subprocess.list2cmdline([a]) for a in argv)


def post_argv(command: str, path: str, title: str, channel: str = "",
              windows: bool | None = None, env: dict[str, str] | None = None) -> list[str]:
    """The command as arguments, placeholders filled in each one. For a
    command Windows runs through cmd.exe, the title and the channel's name
    as cmd.exe can only read as text (``_words_for_cmd``)."""
    win = os.name == "nt" if windows is None else windows
    try:
        argv = _split(command or "", win)
    except ValueError as e:
        raise ValueError(f"cannot read the command: {e}") from e
    if win and argv and through_cmd(argv[0]):
        title, channel = _words_for_cmd(title, env), _words_for_cmd(channel, env)
    values = fields(title, channel)
    values.update(path=path, folder=os.path.dirname(path), file=os.path.basename(path))
    return [re.sub(r"\{(\w*)\}", lambda m: values.get(m.group(1), m.group(0)), a) for a in argv]


def post_command(command: str, path: str, title: str, channel: str = "",
                 windows: bool | None = None,
                 env: dict[str, str] | None = None) -> list[str] | str:
    """What is started for a finished recording: the arguments - or, for a
    command Windows runs through cmd.exe, the one line it is given
    (``cmd_line``). ValueError for a command that cannot be read, and for a
    recording whose file name cmd.exe would expand (%NAME% of a variable
    there is): the command is not started for that one."""
    win = os.name == "nt" if windows is None else windows
    argv = post_argv(command, path, title, channel, win, env)
    if not (win and argv and through_cmd(argv[0])):
        return argv
    # What the person wrote may name a variable (%USERPROFILE%); what was
    # filled in - the file's name, a title next to a channel's name - may
    # not add one, nor a quote or a new line.
    for written, arg in zip(_split(command or "", win), argv, strict=True):
        if arg == written:
            continue
        added = list(_expansions(arg, env))
        for name in _expansions(written, env):
            if name in added:
                added.remove(name)
        if added or arg.count('"') != written.count('"') \
                or any(ord(c) < 32 and c not in written for c in arg):
            raise ValueError("the recording's file name holds what cmd.exe would expand "
                             "(%NAME%) - the command is not run for it")
    return cmd_line(argv)


def _env(path: str, title: str, channel: str, command: str = "") -> dict[str, str]:
    from .xdg import system_env
    env = system_env()
    try:
        first = _split(command, True)[:1] if command and os.name == "nt" else []
    except ValueError:
        first = []
    if first and through_cmd(first[0]):
        # A batch file reads these with %NAME%, which cmd.exe then reads on.
        title, channel = _words_for_cmd(title, env), _words_for_cmd(channel, env)
    env.update(DOPEIPTV_REC_PATH=path, DOPEIPTV_REC_TITLE=title or "",
               DOPEIPTV_REC_CHANNEL=channel or "")
    return env


def run_post(command: str, path: str, title: str, channel: str = "") -> subprocess.Popen | None:
    """Start the command for a finished recording, on its own (the app does
    not wait for it). None when there is none or it cannot start."""
    if not (command or "").strip():
        return None
    try:
        argv = post_argv(command, path, title, channel)
        if not argv:
            return None
        proc = subprocess.Popen(post_command(command, path, title, channel),
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                env=_env(path, title, channel, command),
                                start_new_session=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, ValueError) as e:
        log.warning("recording: the command after it did not start: %s", for_log(e))
        return None
    log.info("recording: started %s for %s", os.path.basename(argv[0]),
             for_log(os.path.basename(path)))
    return proc


# -- checks (the Nerd page's lines) -------------------------------------------

def check_folder(folder: str) -> Check:
    """Can recordings be written there, and how much room is left."""
    path = os.path.expanduser(folder or "")
    if not path or not os.path.isdir(path):
        return Check(FAIL, "nerd_ck_folder_missing", {}, f"Not a folder: {path}")
    lines = [f"Folder: {path}"]
    try:
        fd, probe = tempfile.mkstemp(prefix=".dopeiptv-write-test-", dir=path)
        os.close(fd)
        os.remove(probe)
    except OSError as e:
        lines.append(f"Writing a test file: {type(e).__name__}: {e}")
        return Check(FAIL, "nerd_ck_rec_readonly", {}, "\n".join(lines))
    free = shutil.disk_usage(path).free
    gb = free / 1e9
    lines.append(f"Writing a test file: ok\nFree: {gb:.1f} GB")
    if gb < 2:
        return Check(WARN, "nerd_ck_rec_low", {"gb": f"{gb:.1f}"}, "\n".join(lines))
    return Check(OK, "nerd_ck_rec_ok", {"gb": f"{gb:.0f}"}, "\n".join(lines))


def check_template(template: str) -> Check:
    """What a recording would be called, or which placeholder is unknown."""
    if not (template or "").strip():
        return Check(INFO, "nerd_ck_tpl_default",
                     {"example": render_name(DEFAULT_TEMPLATE, "Nyheterna", "SVT1 HD") + ".ts"})
    bad = unknown_fields(template)
    example = render_name(template, "Nyheterna", "SVT1 HD") + ".ts"
    detail = f"Template: {template}\nExample: {example}\nKnown: " + " ".join("{%s}" % f for f in _NAME_FIELDS)
    if bad:
        return Check(FAIL, "nerd_ck_tpl_unknown", {"names": ", ".join("{%s}" % b for b in bad)}, detail)
    return Check(OK, "nerd_ck_tpl_ok", {"example": example}, detail)


def check_post(command: str, timeout: float = 20.0) -> Check:
    """Run the command once on a small test file, as it would run after a
    recording: does it start, and what does it answer."""
    if not (command or "").strip():
        return Check(INFO, "nerd_ck_mpv_empty")
    tmp = tempfile.mkdtemp(prefix="dopeiptv-post-test-")
    sample = os.path.join(tmp, "dopeIPTV test.ts")
    with open(sample, "wb") as fh:
        fh.write(b"\x47" + b"\xff" * 187)
    try:
        argv = post_argv(command, sample, "dopeIPTV test", "Test")
        started = post_command(command, sample, "dopeIPTV test", "Test")
    except ValueError as e:
        return Check(FAIL, "nerd_ck_post_bad", {}, str(e))
    lines = ["Ran: " + " ".join(shlex.quote(a) for a in argv)]
    t0 = time.monotonic()
    try:
        out = subprocess.run(started, capture_output=True, text=True, timeout=timeout,
                             env=_env(sample, "dopeIPTV test", "Test", command),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError as e:
        lines.append(f"Error: {e}")
        return Check(FAIL, "nerd_ck_post_missing", {"name": argv[0] if argv else ""}, "\n".join(lines))
    except subprocess.TimeoutExpired:
        lines.append(f"Still running after {int(timeout)} s - left to finish on its own after a real recording.")
        return Check(WARN, "nerd_ck_post_slow", {"s": int(timeout)}, "\n".join(lines))
    except OSError as e:
        lines.append(f"Error: {type(e).__name__}: {e}")
        return Check(FAIL, "nerd_ck_post_bad", {}, "\n".join(lines))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ms = int((time.monotonic() - t0) * 1000)
    lines.append(f"Exit code: {out.returncode} ({ms} ms)")
    if out.stdout.strip():
        lines.append("Output:\n" + out.stdout.strip()[-2000:])
    if out.stderr.strip():
        lines.append("Errors:\n" + out.stderr.strip()[-2000:])
    if out.returncode != 0:
        return Check(FAIL, "nerd_ck_post_code", {"code": out.returncode}, "\n".join(lines))
    return Check(OK, "nerd_ck_post_ok", {"ms": ms}, "\n".join(lines))
