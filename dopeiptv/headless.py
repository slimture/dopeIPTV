"""``dopeiptv --server``: server mode without a window (services/home_server.py)
- for a NAS, a Raspberry Pi, a container or a systemd service.

No widget is imported, nor anything that draws (QtGui, mpv): only QtCore
for the settings and its event loop, plain threads for the rest. The same
settings, sources and guides as the app - each served source's guide
downloaded again when it is six hours old (services/guide_keeper.py) - the
household's, never a profile's own (the settings are read as they are, not
through core/profiles.py) - and the usual log (stderr, DOPEIPTV_LOG_FILE,
Nerd's log file).

It records what is scheduled while no window runs (services/server_recorder.py):
the household's scheduled recordings and series rules, each job claimed by
one recorder alone (core/rec_claims.py), counted with the tuner's channels
against the account's limit (``HeadlessHost.app_use``).

Ctrl+C or SIGTERM stops it: every recording ends with what it holds, every
viewer is let go, every provider connection closed, the buffers wiped.

``--port N`` listens on N this time (the setting stays); ``--no-discovery``
leaves UDP 65001 alone (another tuner emulator on the computer has it). A
tuner open to every device of this computer's networks without its token
is said in the log at every start (``open_note``).
A settings file that could not be made readable by this user alone stops
it, until ``--settings-not-private`` says once to go on with that file.
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
    #: What the server's answers say it is (home_server.MARK).
    kind = "headless"

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._pool: Any = None
        #: What records the scheduled recordings (services/server_recorder.py);
        #: None until it is made, or when it cannot be.
        self.recorder: Any = None

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
        """The connections this computer's recordings hold to *pid*: the
        restream counts them before it opens one for a device."""
        rec = self.recorder
        return rec.connections(pid) if rec is not None else (0, [])

    def app_session(self, pid: str, sid: str) -> Any:
        """A channel a recording keeps streaming: a device tuning to it
        shares its connection."""
        rec = self.recorder
        return rec.session_for(pid, sid) if rec is not None else None

    def recordings(self) -> dict | None:
        """What the server records, waits for and records next (its page)."""
        rec = self.recorder
        return rec.status() if rec is not None else None

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


def taken_note(taken: Any) -> str:
    """The log's words for a port found in use (home_server.Taken): whose it
    is, and the port free next - nothing is changed."""
    from .services import home_server as hs
    who = {hs.HOLDER_WINDOW: "dopeIPTV's own server has it - the app's window, with server "
                             "mode on (Nerd > Remote and automation)",
           hs.HOLDER_HEADLESS: "dopeIPTV's own server has it - another `dopeiptv --server`",
           hs.HOLDER_OURS: "dopeIPTV's own server has it - the app's window or another "
                           "`dopeiptv --server`"}.get(taken.holder, "another program uses it")
    nxt = (f"port {taken.free} is free: start with --port {taken.free}" if taken.free
           else "none of the next ports is free")
    return f"port {taken.port} is taken: {who}; {nxt}"


def open_note(cfg: Any) -> str:
    """What the tuner is open to without its token, logged at every start
    while it is every device of this computer's networks (what a server set
    up before 1.6.9 keeps) - nothing is changed here. Returns the line ("")
    when there is nothing to say."""
    from .services import home_server as hs
    if cfg.open != hs.OPEN_HOME:
        return ""
    nets = ", ".join(str(n) for n in hs.home_networks()) or "this computer's own networks"
    line = (f"server: every device on {nets} can use the tuner and watch without its token - "
            "what server mode did before 1.6.9. To let in only the devices that need it, in the "
            "app: Settings > Nerd > Remote and automation > Server mode > Devices without the "
            "token")
    log.warning("%s", line)
    return line


def _arg(argv: list[str], name: str) -> str | None:
    if name in argv:
        i = argv.index(name)
        return argv[i + 1] if i + 1 < len(argv) else None
    return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # importlib whole on this thread before any other starts: a first import
    # of it on a worker thread, beside another's, could leave the import
    # machinery half made on Python 3.12.3 (NameError: name 'sys').
    import importlib.util  # noqa: F401
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    configure_logging()
    log.info("%s starting as a server, without a window", BUILD_VERSION)
    from PyQt6.QtCore import QCoreApplication, QSettings, QTimer
    # The names the app's folders are made from (its cache: the guides, the
    # buffers), as the window has them.
    QCoreApplication.setOrganizationName(ORG)
    QCoreApplication.setApplicationName(APP_NAME)
    # QtCore's event loop, for the recorder's timers (core/recording.py) -
    # no window, nothing drawn.
    app = QCoreApplication.instance() or QCoreApplication([APP_NAME])
    from .core import settings_files
    settings_files.protect()            # the logins and tokens: this user's alone
    settings = QSettings(ORG, ORG)
    if settings_files.not_private() and "--settings-not-private" in argv:
        value = settings.value(settings_files.ACCEPTED_KEY, "")
        for path in settings_files.not_private():
            value = settings_files.with_accepted(value, path)
        settings.setValue(settings_files.ACCEPTED_KEY, value)
    exposed = settings_files.waiting(settings.value(settings_files.ACCEPTED_KEY, ""))
    if exposed:
        # Every login and token is in them: no server on them until the
        # person says so - once, for those files; every one named.
        for path in exposed:
            log.error("server: %s could not be made readable by this user alone - it holds "
                      "logins, and other accounts on this computer may read it. Move the "
                      "settings to a drive that keeps permissions, or start once with "
                      "--settings-not-private to go on with it as it is", path)
        return 2
    from .core import nerd
    from .i18n import set_language
    set_language(str(settings.value("language", "en") or "en"))
    nerd.install(settings)
    try:
        # M3U sources onto stable channel ids, as the window's start does.
        from .core import m3u_ids
        from .core.stores import PlaylistStore
        from .providers.epg import _epg_cache_dir
        m3u_ids.start(settings, PlaylistStore(settings).playlists(), str(_epg_cache_dir()))
    except Exception as e:
        log.warning("m3u ids: not moved: %s", e)
    from .services.home_server import (
        HomeServer, avoided_ports, look_at_port, port_taken, read_config,
    )
    host = HeadlessHost(settings)
    from .services.home_server import K_DEVICE, open_mode
    # A server run before 1.6.9 without a window keeps its tuner as it was.
    open_mode(settings, ran_before=bool(str(settings.value(K_DEVICE, "") or "").strip()))
    cfg = read_config(settings)
    register_secrets(cfg.token)
    open_note(cfg)
    port = _arg(argv, "--port")
    if port is not None:
        try:
            cfg.port = int(port)
        except ValueError:
            cfg.port = -1
        if not 0 <= cfg.port < 65536:
            # Past 65535 the bind raised OverflowError, not the OSError a
            # port in use is: a traceback instead of a line. (0: any free one.)
            log.error("server: --port takes a number from 0 to 65535")
            return 2
    server = HomeServer(host, cfg, discovery_port=0 if "--no-discovery" in argv else 65001)
    stop = threading.Event()

    def ask_stop(signum: int, _frame: Any) -> None:
        log.info("server: stopping (signal %d)", signum)
        stop.set()
    # Before it listens: a stop asked while it starts is a stop.
    signal.signal(signal.SIGINT, ask_stop)
    signal.signal(signal.SIGTERM, ask_stop)
    try:
        server.start()
    except OSError as e:
        server.stop()                   # its restream's thread, started with it
        if port_taken(e):
            taken = look_at_port(cfg.port, avoided_ports(settings), server.bind)
            log.error("server: cannot listen on port %d (%s) - %s", cfg.port, e, taken_note(taken))
        else:
            log.error("server: cannot listen on port %d (%s)", cfg.port, e)
        host.close()
        return 2
    page = server.addresses(server.base())["page"]
    log.info("server: settings in a browser at %s", page)
    if sys.stdout.isatty():
        sys.stdout.write(f"dopeIPTV server: {page}\n")
        sys.stdout.flush()
    host.recorder = start_recorder(server, host)
    ticks = [0]

    def look() -> None:
        # Python's signal handlers run between these: Ctrl+C and SIGTERM
        # end the loop within a quarter of a second.
        if stop.is_set():
            app.quit()
            return
        ticks[0] += 1
        if ticks[0] % 120 == 0:
            _follow_settings(server, host, port is not None)
    timer = QTimer()
    timer.setInterval(250)
    timer.timeout.connect(look)
    timer.start()
    try:
        if not stop.is_set():
            app.exec()
    finally:
        timer.stop()
        recorder, host.recorder = host.recorder, None
        if recorder is not None:
            recorder.close()            # every recording ends, what it holds kept
        server.stop()
        host.close()
        if recorder is not None:
            recorder.after_close()      # the command after each, the buffers let go
        log.info("server: done")
    return 0


def start_recorder(server: Any, host: HeadlessHost) -> Any:
    """The server's recorder of what is scheduled (services/server_recorder.py),
    on a settings object of its own (it runs on this thread, the server's
    requests on theirs). None when it cannot keep its claims."""
    from PyQt6.QtCore import QSettings

    from .services import server_recorder
    try:
        recorder = server_recorder.start(QSettings(ORG, ORG), server, host.pool)
    except Exception as e:              # never in the way of the tuner
        log.warning("server: records nothing - %s", e)
        return None
    if recorder is not None:
        server.also_guides = recorder.guide_sources
    return recorder
