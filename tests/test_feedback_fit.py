"""The feedback window on a small screen: never taller than the room the
screen has for it, the rest scrolling, Cancel and Send always in sight - and
no taller than it needs when there is little in it."""
from __future__ import annotations

import tempfile

import pytest

_APP: list = []


def _qapp():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
    app = QApplication.instance() or QApplication([])
    _APP[:] = [app]                       # kept: a QApplication let go takes Qt down
    return app


def _settle(app) -> None:
    for _ in range(3):
        app.processEvents()


def _send_in_sight(fb) -> bool:
    from PyQt6.QtCore import QPoint
    top = fb.send_btn.mapTo(fb, QPoint(0, 0)).y()
    return fb.send_btn.isVisible() and 0 <= top and top + fb.send_btn.height() <= fb.height()


def test_a_full_feedback_window_fits_the_screen_and_scrolls():
    app = _qapp()
    from dopeiptv.ui import dialogs
    tickets = [{"c": f"c{n}", "t": 1763400000 + n, "w": f"Report {n}",
                "r": [{"code": "thanks", "t": 1763400000 + n}]} for n in range(8)]
    fb = dialogs.FeedbackDialog(None, lambda opts: "line\n" * 60, answers=tickets)
    fb.show()
    _settle(app)
    fb.topic_chips["bug"].setChecked(True)
    fb._toggle_preview()
    _settle(app)
    room = fb._room()
    assert room is not None
    assert fb.height() <= int(room.height() * fb.SCREEN_SHARE)
    assert fb._scroll.verticalScrollBar().maximum() > 0, "what does not fit scrolls"
    assert _send_in_sight(fb)
    # It can be made shorter still; it never insists on all of its content.
    assert fb.minimumHeight() <= fb.SHORTEST
    fb.resize(fb.width(), fb.SHORTEST)
    _settle(app)
    assert _send_in_sight(fb)
    fb.close()


def test_a_short_feedback_window_is_as_tall_as_it_needs():
    app = _qapp()
    from dopeiptv.ui import dialogs
    fb = dialogs.FeedbackDialog(None, lambda opts: "")
    fb.show()
    _settle(app)
    room = fb._room()
    assert room is not None
    if fb.height() < int(room.height() * fb.SCREEN_SHARE):
        assert fb._scroll.verticalScrollBar().maximum() == 0, "no scrolling while it fits"
    assert _send_in_sight(fb)
    fb.close()


def test_it_opens_whole_on_the_screen_over_a_window_low_on_it():
    # Qt centres a dialog over its parent alone: a tall one over a window
    # near the foot of the screen opened with Send under the Dock (macOS).
    app = _qapp()
    from PyQt6.QtWidgets import QStyle, QWidget
    from dopeiptv.ui import dialogs
    parent = QWidget()
    parent.resize(700, 420)
    parent.show()
    _settle(app)
    room = dialogs.FeedbackDialog(parent, lambda opts: "")._room()
    parent.move(room.x() + 40, room.bottom() - 420 - 10)
    _settle(app)
    tickets = [{"c": f"c{n}", "t": 1763400000 + n, "w": f"Report {n}",
                "r": [{"code": "thanks", "t": 1763400000 + n}]} for n in range(8)]
    fb = dialogs.FeedbackDialog(parent, lambda opts: "line\n" * 60, answers=tickets)
    fb.show()
    _settle(app)
    title = fb.style().pixelMetric(QStyle.PixelMetric.PM_TitleBarHeight) or 28
    top = fb.frameGeometry().y()
    assert top >= room.top()
    assert top + fb.height() + title <= room.bottom() + 1, "all of it above the Dock"
    # Over its window as far as the screen allows: centred across it.
    centre = fb.frameGeometry().center().x()
    assert abs(centre - parent.frameGeometry().center().x()) <= 2 or fb.frameGeometry().left() == room.left()
    fb.close()
    parent.close()


def test_feedback_and_a_report_link_to_the_privacy_policy(monkeypatch):
    """A small link under the fine print of both windows opens the privacy
    policy's part on the app in the browser - also where the report can only
    be copied (a modified build)."""
    _qapp()
    from PyQt6.QtWidgets import QPushButton
    from dopeiptv.core import xdg
    from dopeiptv.i18n import tr
    from dopeiptv.ui import dialogs
    opened: list[str] = []
    monkeypatch.setattr(xdg, "open_url", lambda url: opened.append(url) or True)
    windows = [dialogs.FeedbackDialog(None, lambda opts: ""),
               dialogs.FeedbackDialog(None, lambda opts: "", copy_only=True),
               dialogs.ReportDialog(None, "error", "trace"),
               dialogs.ReportDialog(None, "feedback", "words", copy_only=True)]
    for w in windows:
        links = [b for b in w.findChildren(QPushButton) if b.text() == tr("fb_privacy_link")]
        assert len(links) == 1, type(w).__name__
        links[0].click()
    assert opened == [dialogs.PRIVACY_URL] * len(windows)
    assert dialogs.PRIVACY_URL.startswith("https://dopeiptv.org/privacy/")


def test_the_windows_say_what_goes_beside_the_text(monkeypatch):
    # An outside review: the window said "exactly what is sent" while the
    # version, the system and the answer code's hash
    # went beside the text unseen. Each is said now, as send_status writes it.
    app = _qapp()
    import json
    import platform

    from dopeiptv import __version__
    from dopeiptv.core import build_check, report
    from dopeiptv.ui import dialogs
    fb = dialogs.FeedbackDialog(None, lambda opts: "")
    _settle(app)
    line = fb.beside.text()
    assert f"dopeIPTV {__version__}" in line and platform.system() in line
    for key in ("report_item_picked", "report_item_code"):
        assert dialogs.tr(key) in line, key
    fb.answers.setChecked(False)
    assert dialogs.tr("report_item_code") not in fb.beside.text()
    rd = dialogs.ReportDialog(None, "error", "trace")
    assert rd.beside is not None and f"dopeIPTV {__version__}" in rd.beside.text()
    assert dialogs.ReportDialog(None, "error", "trace", copy_only=True).beside is None
    # What send_status sends beside the text is exactly what the line names.
    sent = {}
    monkeypatch.setattr(build_check, "allowed", lambda: True)
    monkeypatch.setattr(report, "_post", lambda url, body, timeout: (sent.update(json.loads(body)), (200, b""))[1])
    report.send_status("error", "x", personal=())
    assert set(sent) == {"kind", "version", "os", "text"}
    assert [f"dopeIPTV {sent['version']}", sent["os"]] == report.sent_beside()


def test_what_is_sent_is_exactly_what_the_window_shows(monkeypatch):
    """The boxes are the person's to edit, and on Send they are scrubbed
    once more with the person's own values: where that hides more - an
    e-mail or a password typed into them - the boxes show it, a note says
    so and nothing goes until Send again; what goes then is exactly what
    is shown, and the last scrub on the way out changes nothing."""
    app = _qapp()
    import json

    from dopeiptv.core import build_check, report
    from dopeiptv.ui import dialogs
    personal = report.personal_values([{"name": "Box", "username": "kalle", "password": "hunter2pw"}])

    def scrub(text):
        return report.scrub(text, personal)
    sent = {}
    monkeypatch.setattr(build_check, "allowed", lambda: True)
    monkeypatch.setattr(report, "_post", lambda url, body, timeout: (sent.update(json.loads(body)), (200, b""))[1])

    rd = dialogs.ReportDialog(None, "feedback", report.build("feedback", personal=personal),
                              scrub=scrub)
    assert rd.text.isReadOnly() is False and rd.message is not None and not rd.message.isReadOnly()
    rd.message.setPlainText("It stopped. Mail me: anna.s@example.se, my password is hunter2pw")
    rd.include.setChecked(True)
    rd.show()
    _settle(app)
    rd.accept()
    _settle(app)
    assert rd.result() == 0 and rd.isVisible(), "not sent: shown first"
    assert rd.hidden_note.isVisible()
    shown = rd.message.toPlainText()
    assert "anna.s@example.se" not in shown and "hunter2pw" not in shown and "<email>" in shown
    rd.accept()
    assert rd.result() == 1
    payload = rd.payload()
    assert scrub(payload) == payload, "nothing left for the last scrub"
    report.send_status("feedback", payload, personal=personal)
    assert sent["text"] == payload, "what goes is what was shown"

    fb = dialogs.FeedbackDialog(None, lambda opts: "dopeIPTV 1.6.10\nSystem: Linux", scrub=scrub)
    fb.topic_chips["bug"].setChecked(True)
    fb.message.setPlainText("Box kalle froze")
    fb.preview.setPlainText("dopeIPTV 1.6.10\nlogin kalle / hunter2pw")
    assert not fb.preview.isVisible()
    fb.show()
    _settle(app)
    fb.accept()
    _settle(app)
    assert fb.result() == 0 and fb.preview.isVisible() and fb.hidden_note.isVisible()
    for box in (fb.message, fb.preview):
        assert "kalle" not in box.toPlainText() and "hunter2pw" not in box.toPlainText()
    assert fb.send_btn.isEnabled()
    fb.accept()
    assert fb.result() == 1 and scrub(fb.payload()) == fb.payload()
    # Nothing to hide: sent at once, as before.
    plain = dialogs.ReportDialog(None, "error", "Traceback: boom", scrub=scrub)
    plain.accept()
    assert plain.result() == 1 and not plain.hidden_note.isVisible()
    for w in (rd, fb, plain):
        w.close()
