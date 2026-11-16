"""``dopeiptv --server``: server mode without a window (services/home_server.py)
- for a NAS, a Raspberry Pi, a container or a systemd service.

No widget is imported, nor anything that draws (QtGui, mpv): only QtCore
for the settings, plain threads for the rest. The same settings, sources
and guides as the app - each served source's guide downloaded again when it
is six hours old (services/guide_keeper.py) - the household's, never a profile's own
(the settings are read as they are, not through core/profiles.py) - and the
usual log (stderr, DOPEIPTV_LOG_FILE, Nerd's log file). Ctrl+C or SIGTERM
stops it: every viewer is let go, every provider connection closed, the
buffers wiped.

``--port N`` listens on N this time (the setting stays); ``--no-discovery``
leaves UDP 65001 alone (another tuner emulator on the computer has it).
"""
from __future__ import annotations

import os
import signal
import sys
import threading
from pathlib import Path
from typing import Any

from . import APP_NAME, BUILD_VERSION, ORG
from .core.log import configure_logging, log, register_secrets


class HeadlessHost:
    """What the server asks of the app, without a window: the settings
    file, read and written under a lock (the server's threads share it)."""

    POOL_KEEP_S = 120.0
    POOL_BYTES = 2 << 30

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._pool: Any = None

    def sources(self) -> list[dict]:
        from .core.stores import PlaylistStore
        with self._lock:
            store = PlaylistStore(self.settings)
            items = store.playlists()
            active = store.active_id
        return sorted(items, key=lambda p: p.get("id") != active)

    def filters(self, pid: str) -> tuple[set[str], set[str], dict[str, int]]:
        from .core import channel_numbers
        from .core.stores import CategoryOverrides, ChannelOverrides
        with self._lock:
            cats = CategoryOverrides(self.settings, f"category_overrides_{pid}")
            chans = ChannelOverrides(self.settings, f"channel_overrides_{pid}")
            numbers = dict(channel_numbers.active(pid))
        hidden = {str(k) for k, e in (chans.data.get("live") or {}).items()
                  if isinstance(e, dict) and e.get("hidden")}
        return cats.excluded_ids("live", include_locked=True), hidden, numbers

    def app_use(self, pid: str) -> tuple[int, list[str]]:
        return 0, []

    def app_session(self, pid: str, sid: str) -> Any:
        return None

    def pool(self) -> Any:
        with self._lock:
            if self._pool is None:
                from .core.dvr_pool import DvrPool
                from .core.dvr_store import sweep_abandoned
                base = dvr_base()
                sweep_abandoned(base)
                self._pool = DvrPool(base, self.POOL_KEEP_S, self.POOL_BYTES)
            return self._pool

    def save(self, changes: dict[str, str]) -> Any:
        from .services.home_server import read_config
        with self._lock:
            for key, value in changes.items():
                if value == "":
                    self.settings.remove(key)
                else:
                    self.settings.setValue(key, value)
            self.settings.sync()
            return read_config(self.settings)

    def page(self) -> tuple[bytes, str]:
        from .core import server_page
        from .i18n import current_language, is_rtl, tr
        dark, light = palettes()
        return server_page.render(dark, light, {k: tr(k) for k in server_page.WORDS},
                                  current_language(), is_rtl())

    def close(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.close_all(wait=3.0)


def dvr_base() -> Path:
    """Where the buffers keep their segments: the app's own folder for them."""
    from PyQt6.QtCore import QStandardPaths
    loc = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
    return Path(loc or Path.home() / ".cache" / "dopeiptv") / "dvr"


def palettes() -> tuple[dict[str, str], dict[str, str]]:
    """The app's default dark and light colours for the browser page (no
    profile's look: the server is the household's)."""
    from .ui.theme import THEMES, on_accent, resolve_accent
    out = []
    themes: Any = THEMES
    for key in ("graphite", "light"):
        pal = {k: v for k, v in themes[key].items() if isinstance(v, str)}
        pal["accent"] = resolve_accent(key, "blue", None)[0]
        pal["on_accent"] = on_accent(pal["accent"])
        out.append(pal)
    return out[0], out[1]


def _follow_settings(server: Any, host: HeadlessHost, port_given: bool) -> None:
    """What the app's window changed in the server's settings meanwhile (the
    same file), taken in: the sources, the tuners - and the port, unless
    ``--port`` chose it for this run."""
    from .services.home_server import read_config
    try:
        with host._lock:
            host.settings.sync()
            cfg = read_config(host.settings)
        if port_given:
            cfg.port = server.config.port
        if cfg != server.config:
            server.refresh(cfg)
            if not port_given and cfg.port != server.port:
                server.move(cfg.port)
    except Exception as e:
        log.debug("server: settings not read again: %s", e)


def _arg(argv: list[str], name: str) -> str | None:
    if name in argv:
        i = argv.index(name)
        return argv[i + 1] if i + 1 < len(argv) else None
    return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    configure_logging()
    log.info("%s starting as a server, without a window", BUILD_VERSION)
    from PyQt6.QtCore import QCoreApplication, QSettings
    # The names the app's folders are made from (its cache: the guides, the
    # buffers), as the window has them.
    QCoreApplication.setOrganizationName(ORG)
    QCoreApplication.setApplicationName(APP_NAME)
    settings = QSettings(ORG, ORG)
    from .core import nerd
    from .i18n import set_language
    set_language(str(settings.value("language", "en") or "en"))
    nerd.install(settings)
    from .services.home_server import HomeServer, read_config
    host = HeadlessHost(settings)
    cfg = read_config(settings)
    register_secrets(cfg.token)
    port = _arg(argv, "--port")
    if port is not None:
        try:
            cfg.port = int(port)
        except ValueError:
            log.error("server: --port takes a number")
            return 2
    server = HomeServer(host, cfg, discovery_port=0 if "--no-discovery" in argv else 65001)
    try:
        server.start()
    except OSError as e:
        log.error("server: cannot listen on port %d (%s) - is the app's own server, or "
                  "another program, on it?", cfg.port, e)
        host.close()
        return 2
    page = server.addresses(server.base())["page"]
    log.info("server: settings in a browser at %s", page)
    if sys.stdout.isatty():
        sys.stdout.write(f"dopeIPTV server: {page}\n")
        sys.stdout.flush()
    stop = threading.Event()

    def ask_stop(signum: int, _frame: Any) -> None:
        log.info("server: stopping (signal %d)", signum)
        stop.set()
    signal.signal(signal.SIGINT, ask_stop)
    signal.signal(signal.SIGTERM, ask_stop)
    try:
        ticks = 0
        while not stop.wait(1.0):
            ticks += 1
            if ticks % 30 == 0:
                _follow_settings(server, host, port is not None)
    finally:
        server.stop()
        host.close()
        log.info("server: done")
    return 0
