"""Update-check mixin for MainWindow.

The once-a-day background "is there a newer release?" check and the badge/status
wiring it drives. Split out of main_window.py; every method operates on
MainWindow state (self.settings, self._sidebar_logo, self.update_status_btn,
...) through the mixin, so behaviour is identical.
"""
from __future__ import annotations

import os
import threading
import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMessageBox, QProgressDialog,
    QPushButton,
)

from .. import VERSION
from ..core import macupdate, selfupdate, winupdate
from ..core.log import log
from ..core.updates import GITHUB_REPO, fetch_update, offered
from ..core.xdg import open_url
from ..core.workers import run_async
from ..i18n import tr
from .theme import P

# Where "Download" sends people - the website, not the raw GitHub release.
# ?ref= tells the site's statistics the visit came from the app: a link
# opened from a desktop app carries no referrer, so it counted as "direct".
_WEBSITE = "https://dopeiptv.org/?ref=app-update"


def _updater():
    """What can update this copy in place: an AppImage in a writable folder
    (selfupdate), a copy the Windows installer put there (winupdate), our
    notarised app in a folder the user may write to (macupdate) - or
    nothing, and the download page is offered."""
    for m in (selfupdate, winupdate, macupdate):
        if m.can_self_update():
            return m
    return None


class _UpdatesMixin:
    """MainWindow mixin: the startup update check and its status-row link."""
    def _update_channel(self) -> str:
        """Settings -> General: "stable", or "nightly" for the test builds."""
        ch = self.settings.value("update_channel", "stable")
        return "nightly" if ch == "nightly" else "stable"

    def _maybe_check_updates(self) -> None:
        """Once-a-day background check for a newer release; on a hit, light the
        badge on the Settings button. Cached in QSettings so it doesn't hit
        GitHub every launch and works offline (fails silently). Opt-out via the
        'check for updates' setting."""
        if self.settings.value("check_updates", "true") != "true":
            return
        # Test hook: force the badge on without a network call, so the update
        # indicator can be seen even when you're already on the latest release.
        # Placed after the opt-out check so opting out still suppresses it.
        if os.environ.get("DOPEIPTV_FAKE_UPDATE") == "1":
            self._apply_update_state("v99.0.0")
            return
        try:
            last = float(self.settings.value("update_check_ts", 0) or 0)
        except (TypeError, ValueError):
            last = 0.0
        channel = self._update_channel()
        # Once a day for releases; test builds come with every change.
        every = 6 * 3600 if channel == "nightly" else 86400
        if (time.time() - last < every and
                self.settings.value("update_check_channel", "stable")
                == channel):
            self._apply_update_state(
                self.settings.value("update_latest_tag", "") or "")
            return

        def done(rel):
            rel = rel or {}
            tag = rel.get("tag", "") or ""
            self.settings.setValue("update_check_ts", str(int(time.time())))
            self.settings.setValue("update_check_channel", channel)
            self.settings.setValue("update_latest_tag", tag)
            self.settings.setValue("update_latest_url",
                                   rel.get("url", "") if rel.get("nightly")
                                   else "")
            self._apply_update_state(tag)

        run_async(self.pool, lambda: fetch_update(GITHUB_REPO, channel),
                  done, lambda _e: None)

    def _apply_cached_update(self) -> None:
        """Light the badge from the last cached result at startup so it appears
        right away, without waiting for (or making) a network call."""
        if self.settings.value("check_updates", "true") != "true":
            return
        if os.environ.get("DOPEIPTV_FAKE_UPDATE") == "1":
            self._apply_update_state("v99.0.0")
            return
        tag = self.settings.value("update_latest_tag", "") or ""
        if tag:
            self._apply_update_state(tag)

    def _apply_update_state(self, latest_tag: str) -> None:
        newer = offered(latest_tag, VERSION, self._update_channel())
        logo = self._sidebar_logo
        if not newer:
            logo.set_update(False)
            logo.setToolTip(tr("tooltip_jump_playing"))
            self.update_status_btn.hide()
            return
        logo.setToolTip(tr("about_update_available", version=latest_tag))
        # The attention-grabbing part (status link, red badge, bounce,
        # red->accent settle) runs once per version, so the cached apply at
        # startup and the later network check for the same tag don't double up.
        if getattr(self, "_update_shown_tag", None) == latest_tag:
            return
        self._update_shown_tag = latest_tag
        # A clear, dismissible banner over the whole window (visible on Home
        # too, unlike the sidebar badge) - the main "a new version is out"
        # notice. The status-row link and logo badge stay as quiet reminders.
        self._show_update_banner(latest_tag)
        # Subtle, non-overlay link in the status row - shown for 30 s (as long
        # as the logo badge stays red) then hidden; the badge remains after.
        self.update_status_btn.setText(tr("update_status", version=latest_tag))
        self.update_status_btn.show()
        QTimer.singleShot(30_000, self.update_status_btn.hide)
        logo.set_update(True, "#E5484D")   # red first, to catch the eye
        logo.bounce()
        # After 30 s, settle from the attention-grabbing red to the theme
        # accent - in follow mode, so it keeps matching if the theme changes.
        QTimer.singleShot(30_000, lambda: logo.set_update(
            True, follow_accent=True))

    # -- "new version" banner (visible over any view, Home included) ---------

    def _show_update_banner(self, tag: str) -> None:
        """A dismissible accent banner across the top of the window announcing a
        new release, with a Download button (to the website). Overlays the
        central widget so it shows even on the full-window Home page. Once
        dismissed for a given version it stays gone for that version."""
        if self.settings.value("update_banner_dismissed", "") == tag:
            return
        parent = self.centralWidget()
        if parent is None:
            return
        banner = getattr(self, "_update_banner", None)
        if banner is None:
            banner = self._update_banner = QFrame(parent)
            banner.setObjectName("UpdateBanner")
            row = QHBoxLayout(banner)
            row.setContentsMargins(14, 8, 8, 8)
            row.setSpacing(12)
            self._update_banner_lbl = QLabel("")
            self._update_banner_lbl.setStyleSheet(
                "color:#FFFFFF; font-size:12px; font-weight:600;"
                " background:transparent;")
            dl = self._update_banner_btn = QPushButton(tr("about_download"))
            dl.setCursor(Qt.CursorShape.PointingHandCursor)
            dl.setStyleSheet(
                "QPushButton { background:rgba(255,255,255,0.18);"
                " color:#FFFFFF; border:none; border-radius:6px;"
                " padding:4px 12px; font-size:12px; font-weight:600; }"
                "QPushButton:hover { background:rgba(255,255,255,0.30); }")
            dl.clicked.connect(self._update_action)
            close = QPushButton("✕")   # ✕
            close.setCursor(Qt.CursorShape.PointingHandCursor)
            close.setFixedSize(24, 24)
            close.setStyleSheet(
                "QPushButton { background:transparent; color:#FFFFFF;"
                " border:none; font-size:13px; }"
                "QPushButton:hover { color:#111111; }")
            close.clicked.connect(self._dismiss_update_banner)
            row.addWidget(self._update_banner_lbl, 1)
            row.addWidget(dl)
            row.addWidget(close)
        banner.setStyleSheet(
            f"#UpdateBanner {{ background:{P['accent']}; border-radius:10px; }}")
        self._update_banner_lbl.setText(
            "\U0001F389 " + tr("about_update_available", version=tag))
        self._update_banner_btn.setText(self._update_action_label())
        self._update_banner_tag = tag
        self._reposition_update_banner(force=True)
        banner.show()
        banner.raise_()
        # Fade it away on its own after a while so it doesn't linger forever;
        # the sidebar badge stays as the quiet reminder, and it returns next
        # launch unless the user dismissed it with the ✕.
        QTimer.singleShot(30_000, self._autohide_update_banner)

    def _autohide_update_banner(self) -> None:
        b = getattr(self, "_update_banner", None)
        if b is not None:
            b.hide()

    def _dismiss_update_banner(self) -> None:
        tag = getattr(self, "_update_banner_tag", "")
        if tag:
            self.settings.setValue("update_banner_dismissed", tag)
        b = getattr(self, "_update_banner", None)
        if b is not None:
            b.hide()

    def _reposition_update_banner(self, force: bool = False) -> None:
        """Keep the banner centred along the BOTTOM of the central widget on
        resize - the top would collide with Home's own menu row (back button +
        TV/Movies/Series pills)."""
        b = getattr(self, "_update_banner", None)
        parent = self.centralWidget()
        if b is None or parent is None or (not force and b.isHidden()):
            return
        w = max(300, min(560, parent.width() - 40))
        b.setFixedWidth(w)
        b.adjustSize()
        b.move((parent.width() - w) // 2,
               max(10, parent.height() - b.height() - 16))
        b.raise_()

    # -- updating in place (AppImage, the Windows installer) ------------------

    def _update_action_label(self) -> str:
        return (tr("update_now") if _updater() is not None
                else tr("about_download"))

    def _update_action(self) -> None:
        """The banner's / About's button: update in place where the app can,
        else open the download page."""
        if _updater() is not None:
            self._self_update()
        else:
            # A test build is downloaded from its own release page.
            open_url(self.settings.value("update_latest_url", "")
                     or _WEBSITE)

    def _self_update(self) -> None:
        """Download, verify and test the newest AppImage, then offer to
        restart into it. A worker does the work; a timer carries its
        progress to the dialog, which can cancel it."""
        if getattr(self, "_self_updating", False):
            return
        # None: an AppImage's own update, which says why it cannot run.
        updater = _updater() or selfupdate
        self._self_updating = True
        b = getattr(self, "_update_banner", None)
        if b is not None:
            b.hide()
        cancel = threading.Event()
        state = {"got": 0, "total": 0, "tag": ""}
        dlg = QProgressDialog(tr("update_checking"), tr("common_cancel"),
                              0, 0, self)
        dlg.setWindowTitle(tr("update_now"))
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.canceled.connect(cancel.set)
        dlg.show()

        def progress(got: int, total: int) -> None:
            state["got"], state["total"] = got, total

        def tick() -> None:
            if cancel.is_set() or not state["tag"]:
                return
            got, total = state["got"], state["total"]
            if total and got < total:
                dlg.setLabelText(tr("update_downloading",
                                    version=state["tag"]))
                dlg.setMaximum(100)
                dlg.setValue(int(got * 100 / total))
            elif got and updater.VERIFY_KEY:
                dlg.setLabelText(tr(updater.VERIFY_KEY))
                dlg.setMaximum(0)

        timer = QTimer(self)
        timer.timeout.connect(tick)
        timer.start(150)

        def work():
            channel = self._update_channel()
            rel = fetch_update(GITHUB_REPO, channel)
            if not offered(rel.get("tag", ""), VERSION, channel):
                return None
            state["tag"] = rel["tag"]
            old = selfupdate.appimage_path()
            new = updater.install(rel, VERSION, progress, cancel)
            return rel["tag"], old, new

        def finish() -> None:
            timer.stop()
            timer.deleteLater()
            # hide(), not close(): closing a QProgressDialog emits canceled,
            # which marked every finished update as cancelled - and fail()
            # then swallowed the error it was about to show.
            dlg.hide()
            dlg.deleteLater()
            self._self_updating = False

        def done(result) -> None:
            finish()
            if result is None:
                QMessageBox.information(self, tr("update_now"),
                                        "✓ " + tr("about_up_to_date"))
                return
            tag, old, new = result
            if old is not None and old != new:
                self.settings.setValue("selfupdate_old_path", str(old))
            self.settings.setValue("update_banner_dismissed", tag)
            if QMessageBox.question(
                    self, tr("update_now"),
                    tr(updater.DONE_KEY, version=tag)
                    ) == QMessageBox.StandardButton.Yes:
                updater.restart_into(new)
                self.close()
            elif hasattr(updater, "on_exit"):
                # "Is installed" holds anyway: the Mac's new app goes in
                # place when this one quits.
                updater.on_exit(new)

        def fail(err) -> None:
            finish()
            if cancel.is_set():
                return
            log.warning("update: %s", err)
            QMessageBox.warning(self, tr("update_now"),
                                tr("update_failed", error=str(err)))

        run_async(self.pool, work, done, fail)

    def _come_forward(self) -> None:
        """Opened by the installer after an update: Windows puts a window a
        process in the background opens behind the one in front, so the
        new version asks for the front - and where Windows keeps it back,
        its taskbar button flashes until it is clicked."""
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        QApplication.alert(self, 0)
        log.info("update: opened again after the update")

    def _finish_self_update(self) -> None:
        """At start: the version that replaced an old AppImage removes it,
        and a menu entry left naming a file that is gone (an AppImage
        replaced by hand) is pointed at this one; on Windows, the installer
        an update downloaded goes."""
        if winupdate.installed_dir() is not None:
            winupdate.cleanup()
        if macupdate.app_bundle() is not None:
            macupdate.cleanup()
        running = selfupdate.appimage_path()
        old = self.settings.value("selfupdate_old_path", "") or ""
        if old and running is not None:
            self.settings.remove("selfupdate_old_path")
            selfupdate.take_pending_cleanup(old, running)
        if running is not None:
            try:
                selfupdate.repoint_desktop_entry(running)
            except Exception as e:      # a nicety; never break startup
                log.warning("update: menu entry check failed: %s", e)
