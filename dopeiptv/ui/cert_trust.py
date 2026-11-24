"""Trusting a home server's own certificate (Nerd, core/cert_pin.py): the
window that shows it - subject, issuer, expiry, SHA-256 fingerprint - and
asks before it is pinned; and the offer, when a pinned source fails because
its server now shows another certificate, to look at the new one.

The certificate is always read off the window's thread; what is shown is
what the server showed, never anything typed.
"""

from __future__ import annotations

from typing import Any, Callable

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt, QThreadPool
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QLabel, QLayout, QPushButton, QVBoxLayout,
    QWidget,
)

from ..core import cert_pin, net_route
from ..core.log import log
from ..core.workers import run_async
from ..i18n import tr
from . import message_box
from .theme import P


def two_lines(fp: str) -> str:
    """A fingerprint as it is read out: two lines of sixteen bytes."""
    shown = cert_pin.pretty(fp)
    return shown[:47] + "\n" + shown[48:] if len(shown) > 48 else shown


class CertTrustDialog(QDialog):
    """Is this the certificate of your own server? *pinned*: what was
    trusted before (the certificate changed since)."""

    def __init__(self, parent: QWidget | None, host: str, port: int, info: cert_pin.CertInfo,
                 pinned: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("cert_trust_title"))
        self.setModal(True)
        self.setMinimumWidth(560)
        self.info, self.host, self.port = info, host, port
        changed = bool(pinned) and pinned != info.sha256
        col = QVBoxLayout(self)
        # Never squeezed: a fingerprint cut off is no use to compare.
        col.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        col.setContentsMargins(24, 22, 24, 20)
        col.setSpacing(10)
        head = QLabel(tr("cert_changed_head") if changed else tr("cert_trust_head"))
        head.setWordWrap(True)
        head.setStyleSheet(f"font-size:16px; font-weight:700; color:{P['error'] if changed else P['text']};")
        col.addWidget(head)
        about = QLabel(tr("cert_trust_about", server=f"{host}:{port}"))
        about.setWordWrap(True)
        about.setStyleSheet(f"font-size:13px; color:{P['text2']};")
        col.addWidget(about)
        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        when = "%Y-%m-%d %H:%M UTC"

        mono_face = QFont("monospace")
        mono_face.setStyleHint(QFont.StyleHint.TypeWriter)

        def value(text: str, colour: str = "", mono: bool = False) -> QLabel:
            lbl = QLabel(text)
            lbl.setTextFormat(Qt.TextFormat.PlainText)     # what the server's certificate says
            lbl.setWordWrap(not mono)          # a fingerprint keeps its two lines
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            # The face through the style sheet: the app's own names one for
            # every widget, over setFont().
            face = f" font-family: '{mono_face.defaultFamily()}', monospace;" if mono else ""
            lbl.setStyleSheet(f"color:{colour or P['text']};{face}")
            return lbl
        self.subject = value(info.name("subject"))
        issuer = info.name("issuer") + (f"  ({tr('cert_self_signed')})" if info.self_signed else "")
        until = info.not_after.strftime(when) if info.not_after else "?"
        if info.expired():
            until += f"  ({tr('cert_expired')})"
        form.addRow(tr("cert_subject"), self.subject)
        form.addRow(tr("cert_issuer"), value(issuer))
        form.addRow(tr("cert_valid_from"), value(info.not_before.strftime(when) if info.not_before else "?"))
        form.addRow(tr("cert_valid_until"), value(until, P["error"] if info.expired() else ""))
        form.addRow(tr("cert_names"), value(", ".join(info.names) or "-"))
        self.fingerprint = value(two_lines(info.sha256), mono=True)
        form.addRow("SHA-256", self.fingerprint)
        if changed and pinned:
            form.addRow(tr("cert_before"), value(two_lines(pinned), P["muted"], mono=True))
        col.addLayout(form)
        advice = QLabel(tr("cert_trust_advice"))
        advice.setWordWrap(True)
        advice.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        col.addWidget(advice)
        col.addSpacing(6)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(tr("common_cancel"))
        self.trust = QPushButton(tr("cert_trust_btn"), objectName="Primary")
        cancel.clicked.connect(self.reject)
        self.trust.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.trust)
        col.addLayout(buttons)
        cancel.setDefault(True)          # trusting takes a deliberate click

    def pin(self) -> dict[str, str]:
        """The source options that pin this certificate."""
        return cert_pin.pin_options(self.host, self.port, self.info.sha256)


class CertFetch(QObject):
    """Reads a server's certificate off the window's thread and hands it to
    *done(host, port, info)* - or *failed(message)* - while its owner lives."""

    def __init__(self, owner: QObject, done: Callable[[str, int, Any], None],
                 failed: Callable[[str], None]) -> None:
        super().__init__(owner)
        self._done, self._failed = done, failed
        self._gen = 0
        self._want: tuple[str, int, int] | None = None

    def start(self, host: str, port: int, route: net_route.Route | None) -> None:
        self._gen += 1
        gen = self._gen
        self._want = (host, port, gen)

        def work() -> tuple[int, Any, str]:
            # The answer says whose it is: one asked for earlier (another
            # address typed meanwhile) is never shown as the newer one's.
            try:
                return gen, cert_pin.fetch(host, port, 8.0, route), ""
            except Exception as e:
                localized = getattr(e, "localized", None)     # as run_async says it
                return gen, None, localized() if callable(localized) else str(e)

        run_async(QThreadPool.globalInstance(), work, self._came, self._error)

    def _came(self, got: Any) -> None:
        if sip.isdeleted(self) or self._want is None:
            return
        gen, info, error = got
        host, port, want = self._want
        if gen != want:
            return              # an earlier ask's; the newer one is on its way
        self._want = None
        if info is None:
            self._failed(error)
            return
        self._done(host, port, info)

    def _error(self, message: str) -> None:
        if sip.isdeleted(self) or self._want is None:
            return
        self._want = None
        self._failed(message)


# -- the window: a pinned source whose certificate changed ----------------------------

class _Review(QObject):
    """One offer to review a source's new certificate (parented to the
    window; gone when done)."""

    def __init__(self, window: Any, store: Any, playlist: dict, retry: Callable[[str], None] | None) -> None:
        super().__init__(window)
        self.window, self.store, self.pl, self.retry = window, store, playlist, retry
        self.fetch = CertFetch(self, self._show, self._failed)

    def ask(self, host: str, port: int) -> None:
        # Review the default (Enter), Close for Escape and the window's close.
        got = message_box.choose(
            self.window, tr("cert_changed_title"),
            tr("cert_changed_body", name=str(self.pl.get("name") or ""), server=f"{host}:{port}"),
            [(tr("common_close"), message_box.NORMAL), (tr("cert_review_btn"), message_box.PRIMARY)],
            default=1, escape=0, kind=message_box.WARNING)
        if got != 1:
            self.deleteLater()
            return
        src = dict(self.pl.get("options") or {}, _kind=self.pl.get("kind") or "xtream")
        self.fetch.start(host, port, net_route.route_for(src))

    def _show(self, host: str, port: int, info: Any) -> None:
        old = cert_pin.pin_of(self.pl.get("options"))
        dlg = CertTrustDialog(self.window, host, port, info, old[2] if old else None)
        if dlg.exec():
            pid = str(self.pl.get("id") or "")
            options = dict(self.pl.get("options") or {}, **dlg.pin())
            if self.store is not None and pid:
                self.store.update(pid, options=options)
            cert_pin.clear_mismatch(host, port)
            log.info("cert: the new certificate of %s:%s is trusted for its source", host, port)
            if self.retry is not None and pid:
                self.retry(pid)
        self.deleteLater()

    def _failed(self, message: str) -> None:
        message_box.warning(self.window, tr("cert_changed_title"),
                            tr("cert_fetch_failed", error=message))
        self.deleteLater()


def offer_review(window: Any, store: Any, playlist: dict | None,
                 retry: Callable[[str], None] | None = None) -> bool:
    """*playlist* failed: if its pinned server was met showing another
    certificate, say so plainly and offer to look at the new one (and trust
    it, then *retry(pid)*). False - nothing said - otherwise."""
    pin = cert_pin.pin_of((playlist or {}).get("options"))
    if pin is None or playlist is None:
        return False
    host, port, _fp = pin
    if cert_pin.mismatch(host, port) is None:
        return False
    _Review(window, store, playlist, retry).ask(host, port)
    return True
