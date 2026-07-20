"""First-run onboarding + offline provider-hint mixin for MainWindow.

The welcome overlay and its wizard steps, plus the "+ Add provider" hint shown
when the app is running against the empty offline stand-in. Pure UI moved out of
main_window.py; behaviour is identical.
"""
from __future__ import annotations

from PyQt6.QtCore import QPropertyAnimation, Qt
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QPushButton

from ..i18n import tr
from ..providers.client import DemoClient, OfflineClient
from .welcome import WelcomeOverlay


class _OnboardingMixin:
    """MainWindow mixin: the first-run welcome wizard and the '+ Add provider' hint."""
    def show_welcome(self) -> None:
        """Show the first-run onboarding wizard (no provider configured)."""
        if self._welcome is None:
            self._welcome = WelcomeOverlay(
                self, settings=self.settings,
                on_connect=self._wizard_connect,
                on_explore=self._wizard_explore,
                on_connect_trakt=self._wizard_connect_trakt,
                on_language_change=self._wizard_language,
                on_demo=self.start_demo)
        else:
            self._welcome.reset()
        # If Trakt was linked in an earlier visit, show it as connected.
        self._welcome.set_trakt_connected(self.trakt.is_connected())
        self._welcome.cover()
        self._update_provider_hint()

    def _wizard_connect_trakt(self) -> None:
        """Run the Trakt sign-in flow, then reflect the result in the wizard so
        the user gets a clear 'connected' confirmation on the same screen."""
        self._trakt_connect_flow(self)
        if self._welcome is not None:
            self._welcome.set_trakt_connected(self.trakt.is_connected())

    def _wizard_language(self, code: str) -> None:
        from ..i18n import set_language
        set_language(code)
        self.settings.setValue("language", code)
        self.retranslate_ui()

    def _wizard_connect(self, server: str, user: str, pw: str,
                        kind: str = "xtream", name: str = "") -> None:
        # Use the name the user typed; fall back to the server host so an
        # unnamed playlist still gets a sensible label.
        name = name.strip() or server.split("//")[-1].split("/")[0] \
            or "My playlist"
        pl = self.playlist_store.add(
            {"name": name, "kind": kind, "server": server, "username": user,
             "password": pw, "epg_url": "", "refresh": "never"})
        self.playlist_store.set_active(pl["id"])
        self.switch_playlist(pl["id"])
        # The wizard stays open for the optional Trakt step and hides itself
        # on Finish; the provider hint should not appear now.
        self._update_provider_hint()

    def _wizard_explore(self) -> None:
        self._set_status(tr("welcome_add_hint"))
        self._update_provider_hint()

    # -- "no provider yet" affordance ----------------------------------------

    def _update_provider_hint(self) -> None:
        """Show a big pulsing '+ Add provider' button in the middle pane
        whenever the app is running without a real provider and the wizard is
        closed. It brings the wizard back and disappears the moment a provider
        is added."""
        # Show the hint until a REAL provider is added: explore mode
        # (OfflineClient) and the demo (DemoClient) both still want it, so the
        # user can graduate from trying channels to entering their own. A real
        # Xtream/M3U client removes it for good. M3UClient also subclasses
        # OfflineClient, so key on the exact types. Never float it over a
        # maximized/fullscreen video.
        no_provider = type(self.client) in (OfflineClient, DemoClient)
        overlay_up = self._welcome is not None and self._welcome.isVisible()
        if no_provider and not overlay_up and not self._player_fs:
            if self._add_provider_btn is None:
                self._build_provider_hint()
            self._add_provider_btn.setText(tr("onb_add_provider"))
            self._add_provider_btn.show()
            self._add_provider_btn.raise_()
            self._position_provider_hint()
            self._add_provider_anim.start()
        elif self._add_provider_btn is not None:
            self._add_provider_anim.stop()
            self._add_provider_btn.hide()

    def _build_provider_hint(self) -> None:
        btn = QPushButton(tr("onb_add_provider"), self)
        btn.setMinimumHeight(46)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # Always red, independent of the app theme, so it clearly reads as
        # a call to action.
        btn.setStyleSheet(
            "QPushButton { background:#e5354b; color:#ffffff; font-weight:700;"
            " font-size:14px; border:none; border-radius:10px; padding:0 22px; }"
            "QPushButton:hover { background:#c8283b; }")
        btn.clicked.connect(self.show_welcome)
        # Slow opacity pulse to draw the eye without being noisy.
        eff = QGraphicsOpacityEffect(btn)
        btn.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", self)
        anim.setDuration(1500)
        anim.setStartValue(1.0)
        anim.setKeyValueAt(0.5, 0.45)
        anim.setEndValue(1.0)
        anim.setLoopCount(-1)
        self._add_provider_btn = btn
        self._add_provider_anim = anim

    def _position_provider_hint(self) -> None:
        btn = self._add_provider_btn
        if btn is None or not btn.isVisible():
            return
        # Centre it at the bottom of the *visible* central area. Anchoring to
        # the middle list pane broke in demo/explore mode: the Home page sits
        # on top of the central stack there, so the classic list is hidden and
        # its geometry is stale - centring on it landed the button off to one
        # side. The central stack is always laid out and always visible, so it
        # keeps the button centred whichever page (Home or classic) is showing.
        # A fixed margin above the bottom edge (not a percentage) keeps it
        # planted as the window is resized or the columns are dragged.
        anchor = getattr(self, "_center_stack", None) or self.listw
        btn.adjustSize()
        w = max(240, btn.width() + 40)
        h = 46
        margin = 22
        tl = anchor.mapTo(self, anchor.rect().topLeft())
        x = tl.x() + (anchor.width() - w) // 2
        y = tl.y() + anchor.height() - h - margin
        btn.setGeometry(x, y, w, h)
