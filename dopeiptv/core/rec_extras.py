"""Nerd for recordings: a file name template, a command run when a
recording is done, and checks for both and for the folder.

    {title} {channel} {date} {time} {year} {month} {day} {hour} {minute} {weekday}

A "/" in a template makes folders under the recording folder
("{channel}/{title} {date}"); each part is made safe for a file name, and
nothing can lead outside the recording folder (".." and absolute parts are
dropped). The command is split like a shell would split it but never run
through one: placeholders are filled in per argument, so a title with
quotes or ; in it is only ever text.
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
from typing import Any

from .log import log
from .nerd_check import FAIL, INFO, OK, WARN, Check

DEFAULT_TEMPLATE = "{title} {date} {time}"
FIELDS = ("title", "channel", "date", "time", "year", "month", "day", "hour",
          "minute", "weekday", "path", "folder", "file")
_NAME_FIELDS = FIELDS[:10]
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _safe_part(text: str) -> str:
    part = _UNSAFE.sub("", text).strip().strip(".")
    return re.sub(r"\s+", " ", part)


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
    the template filled in, part by part made safe. Empty parts go, and an
    empty result falls back to the default template."""
    # A "/" in a title ("AC/DC Live", "21/11") is not a folder: only the
    # template's own are.
    values = {k: re.sub(r"[\\/]+", "-", v) for k, v in fields(title, channel, when).items()}
    text = re.sub(r"\{(\w*)\}", lambda m: values.get(m.group(1), ""), template or "")
    parts = [_safe_part(p) for p in re.split(r"[\\/]+", text)]
    parts = [p for p in parts if p and p not in (".", "..")]
    if not parts:
        if (template or "") != DEFAULT_TEMPLATE:
            return render_name(DEFAULT_TEMPLATE, title, channel, when)
        return _safe_part(title) or "recording"
    return os.path.join(*parts)


def post_argv(command: str, path: str, title: str, channel: str = "") -> list[str]:
    """The command as arguments, placeholders filled in each one."""
    values = fields(title, channel)
    values.update(path=path, folder=os.path.dirname(path), file=os.path.basename(path))
    try:
        argv = shlex.split(command or "", posix=os.name != "nt")
    except ValueError as e:
        raise ValueError(f"cannot read the command: {e}") from e
    return [re.sub(r"\{(\w*)\}", lambda m: values.get(m.group(1), m.group(0)), a) for a in argv]


def _env(path: str, title: str, channel: str) -> dict[str, str]:
    from .xdg import system_env
    env = system_env()
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
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, env=_env(path, title, channel),
                                start_new_session=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, ValueError) as e:
        log.warning("recording: the command after it did not start: %s", e)
        return None
    log.info("recording: started %s for %s", os.path.basename(argv[0]), os.path.basename(path))
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
    except ValueError as e:
        return Check(FAIL, "nerd_ck_post_bad", {}, str(e))
    lines = ["Ran: " + " ".join(shlex.quote(a) for a in argv)]
    t0 = time.monotonic()
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                             env=_env(sample, "dopeIPTV test", "Test"),
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


def settings_value(settings: Any, key: str) -> str:
    try:
        return str(settings.value(key, "") or "")
    except Exception:
        return ""
