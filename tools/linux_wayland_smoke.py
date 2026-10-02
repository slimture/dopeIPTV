"""Check that the Linux bundle submits its main window to host Wayland/Mesa."""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def mapped_main_window(log: str) -> bool:
    xdg_surfaces: dict[str, str] = {}
    toplevels: dict[str, str] = {}
    titled: set[str] = set()
    attached: set[str] = set()
    for line in log.splitlines():
        match = re.search(
            r"(wl_surface|xdg_surface|xdg_toplevel)[@#](\d+)\.destroy\(", line)
        if match:
            interface, object_id = match.groups()
            surface = object_id if interface == "wl_surface" else (
                xdg_surfaces.get(object_id) if interface == "xdg_surface"
                else toplevels.get(object_id))
            if surface is not None:
                titled.discard(surface)
                attached.discard(surface)
                xdg_surfaces = {key: value for key, value in xdg_surfaces.items()
                                if value != surface}
                toplevels = {key: value for key, value in toplevels.items()
                             if value != surface}
        match = re.search(
            r"get_xdg_surface\(new id xdg_surface[@#](\d+), wl_surface[@#](\d+)",
            line)
        if match:
            xdg, surface = match.groups()
            xdg_surfaces[xdg] = surface
            titled.discard(surface)
            attached.discard(surface)
        match = re.search(
            r"xdg_surface[@#](\d+)\.get_toplevel\(new id xdg_toplevel[@#](\d+)",
            line)
        if match and match[1] in xdg_surfaces:
            toplevels[match[2]] = xdg_surfaces[match[1]]
        match = re.search(r'xdg_toplevel[@#](\d+)\.set_title\("([^"]*)"', line)
        if match and match[1] in toplevels:
            surface = toplevels[match[1]]
            if "dopeiptv" in match[2].lower():
                titled.add(surface)
            else:
                titled.discard(surface)
                attached.discard(surface)
        match = re.search(r"wl_surface[@#](\d+)\.attach\(([^,)]*)", line)
        if match:
            surface, buffer = match.groups()
            if surface in titled and re.match(r"wl_buffer[@#]\d+", buffer):
                attached.add(surface)
            else:
                attached.discard(surface)
        match = re.search(r"wl_surface[@#](\d+)\.commit\(", line)
        if match and match[1] in titled and match[1] in attached:
            return True
    return False


def _stop(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except ProcessLookupError:
        pass
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: linux_wayland_smoke.py /path/to/AppRun", file=sys.stderr)
        return 2
    executable = str(Path(args[0]).resolve())
    with tempfile.TemporaryDirectory(prefix="dopeiptv-wayland-") as temp:
        root = Path(temp)
        runtime = root / "runtime"
        runtime.mkdir(mode=0o700)
        home = root / "home"
        home.mkdir()
        env = os.environ.copy()
        for key in ("LD_PRELOAD", "LD_LIBRARY_PATH", "DISPLAY", "QT_PLUGIN_PATH",
                    "QT_QPA_PLATFORM_PLUGIN_PATH", "XDG_CONFIG_HOME",
                    "XDG_CACHE_HOME", "XDG_DATA_HOME"):
            env.pop(key, None)
        env.update(HOME=str(home), XDG_RUNTIME_DIR=str(runtime),
                   WAYLAND_DISPLAY="dopeiptv-test", LIBGL_ALWAYS_SOFTWARE="1")
        compositor = app = None
        weston_log = root / "weston.log"
        app_log = root / "app.log"
        try:
            with weston_log.open("w") as out:
                compositor = subprocess.Popen([
                    "weston", "--backend=headless", "--renderer=pixman",
                    "--shell=kiosk", "--no-config", "--idle-time=0",
                    "--socket=dopeiptv-test",
                ], env=env, stdout=out, stderr=subprocess.STDOUT,
                    start_new_session=True)
            deadline = time.monotonic() + 10
            while not (runtime / "dopeiptv-test").exists():
                if compositor.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Weston did not create its Wayland socket")
                time.sleep(0.1)
            env.update(QT_QPA_PLATFORM="wayland", WAYLAND_DEBUG="client",
                       QT_WAYLAND_CLIENT_BUFFER_INTEGRATION="wayland-egl",
                       QT_WAYLAND_DISABLE_WINDOWDECORATION="1")
            with app_log.open("w") as out:
                app = subprocess.Popen([executable], env=env, stdout=out,
                                       stderr=subprocess.STDOUT,
                                       start_new_session=True)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                log = app_log.read_text(errors="replace")
                if app.poll() is not None:
                    raise RuntimeError("The app exited before mapping its main window")
                if mapped_main_window(log) and "Embedded playback: enabled" in log:
                    print("Wayland: main window submitted a buffer; embedded player enabled")
                    return 0
                time.sleep(0.2)
            raise RuntimeError("The app did not map its main window with embedded playback")
        except (OSError, RuntimeError) as exc:
            print(f"::error::{exc}", file=sys.stderr)
            for path in (weston_log, app_log):
                if path.exists():
                    print(f"--- {path.name} ---\n{path.read_text(errors='replace')}",
                          file=sys.stderr)
            return 1
        finally:
            _stop(app)
            _stop(compositor)


if __name__ == "__main__":
    raise SystemExit(main())
