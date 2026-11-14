"""Nerd in the interface: a page that is not there until chosen, controls
that store what they say, a source's own options stored only where they
differ, the welcome wizard's quiet offer, diagnostics without secrets."""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtGui import QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog  # noqa: E402

from dopeiptv.core import nerd  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    yield s
    nerd.install(None)


def _shell():
    from dopeiptv.ui.settings_ui import SettingsPage, SettingsShell, page_icon
    shell = SettingsShell("Search", "none")
    blank = lambda k, sz, c: QPixmap(sz, sz)  # noqa: E731
    general = shell.add_page(SettingsPage("general", "General", ""), page_icon("gear", blank))
    general.card("x").row("Language")
    page = shell.add_page(SettingsPage("nerd", "Nerd", ""), page_icon("nerd", blank))
    return shell, page


def _shown(shell, key):
    return not shell._items[key].isHidden()


def test_nerd_is_a_category_whose_switch_shows_the_rest(app, settings):
    from dopeiptv.ui import nerd_settings
    shell, page = _shell()
    nerd_settings.build(page, settings, QDialog())
    shell.arrange([("System", ["general", "nerd"])])
    shell.show_page("nerd")
    assert _shown(shell, "nerd")                      # always in the sidebar
    switch_card, network = page.cards[0], page.cards[2]
    assert not switch_card.isHidden() and network.isHidden()
    # Off: a search does not reach what the switch keeps...
    shell.search.setText("referer")
    assert not _shown(shell, "nerd")
    # ...but one for what it holds finds the switch, which says so.
    shell.search.setText("proxy")
    assert _shown(shell, "nerd") and not switch_card.isHidden() and network.isHidden()
    shell.search.setText("")
    from dopeiptv.ui.settings_ui import ToggleSwitch
    sw = switch_card.findChildren(ToggleSwitch)[0]
    sw.setChecked(True)
    assert not network.isHidden()
    shell.search.setText("proxy")
    assert _shown(shell, "nerd") and not _shown(shell, "general")


def test_the_page_stores_what_its_controls_say(app, settings):
    from dopeiptv.ui import nerd_settings
    from dopeiptv.ui.settings_ui import Chip
    shell, page = _shell()
    d = QDialog()
    commit = nerd_settings.build(page, settings, d)
    from PyQt6.QtWidgets import QComboBox, QLineEdit, QPlainTextEdit
    combos = page.findChildren(QComboBox)
    ua = next(c for c in combos if c.findData("tivimate") >= 0)
    ua.setCurrentIndex(ua.findData("tivimate"))
    for chip in page.findChildren(Chip):
        chip.setChecked(chip.text() in ("AC3", "TrueHD"))
    proxy = next(e for e in page.findChildren(QLineEdit) if e.placeholderText().startswith("http://192.168.1.1"))
    proxy.setText("http://10.0.0.1:3128")
    raw = next(t for t in page.findChildren(QPlainTextEdit) if "deband" in t.placeholderText())
    raw.setPlainText("deband=yes")
    stored = {}
    commit(lambda k, v: stored.__setitem__(k, v))
    assert stored["nerd_user_agent"] == "tivimate"
    assert stored["nerd_spdif"] == "ac3,truehd"
    assert stored["nerd_proxy"] == "http://10.0.0.1:3128"
    assert stored["nerd_mpv_options"] == "deband=yes"
    # What was left as shown is not written (an untouched page writes nothing).
    assert "nerd_net_timeout" not in stored and "nerd_ip_family" not in stored


def test_a_custom_user_agent_is_kept_as_typed(app, settings):
    from dopeiptv.ui import nerd_settings
    settings.setValue("nerd_user_agent", "MyBox/2.0")
    shell, page = _shell()
    commit = nerd_settings.build(page, settings, QDialog())
    stored = {}
    commit(lambda k, v: stored.__setitem__(k, v))
    assert stored["nerd_user_agent"] == "MyBox/2.0"


def test_an_untouched_page_writes_nothing(app, settings):
    from dopeiptv.ui import nerd_settings
    shell, page = _shell()
    commit = nerd_settings.build(page, settings, QDialog())
    stored = {}
    commit(lambda k, v: stored.__setitem__(k, v))
    assert stored == {}


def test_a_sources_own_options_only_where_they_differ(app):
    from dopeiptv.ui.source_nerd import SourceNerdPanel
    p = SourceNerdPanel("enigma2", {"stream_port": "17999", "keep_me": "x"})
    assert p.port.value() == 17999
    assert p.values() == {"stream_port": "17999", "keep_me": "x"}
    p.switches["picons"].setChecked(False)
    p.ua.setCurrentIndex(p.ua.findData("kodi"))
    p.proxy.setCurrentIndex(p.proxy.findData("direct"))
    assert p.values() == {"stream_port": "17999", "keep_me": "x", "picons": False,
                          "user_agent": "kodi", "proxy": "direct"}
    p.port.setValue(8001)
    assert "stream_port" not in p.values()
    # Another kind: its own rows, the box's left behind.
    p.set_kind("hdhomerun")
    p.transcode.setCurrentIndex(p.transcode.findData("mobile"))
    v = p.values()
    assert v["transcode"] == "mobile" and "picons" not in v and "stream_port" not in v
    p.set_kind("tvheadend")
    p.profile.setEditText("my-profile")
    assert p.values()["profile"] == "my-profile"


def test_the_source_dialog_has_nerd_options_only_with_nerd_on(app, settings):
    from dopeiptv.ui.dialogs import PlaylistDialog
    nerd.install(settings)
    assert PlaylistDialog().nerd_panel is None
    settings.setValue("nerd_enabled", "true")
    nerd.install(settings)
    d = PlaylistDialog(playlist={"name": "Vu", "kind": "enigma2", "server": "http://10.0.0.2",
                                 "options": {"stream_port": "17999"}})
    assert d.nerd_panel is not None and d.nerd_panel.port.value() == 17999
    assert d.values()["options"] == {"stream_port": "17999"}
    d.nerd_panel.port.setValue(8001)
    assert "options" not in d.values()


def _welcome(settings):
    from PyQt6.QtWidgets import QMainWindow

    from dopeiptv.ui.welcome import WelcomeOverlay
    got = {}
    win = QMainWindow()
    got["_win"] = win          # kept: Qt deletes the overlay with its window
    ov = WelcomeOverlay(win, settings=settings,
                        on_connect=lambda *a: got.update(args=a), on_explore=lambda: None,
                        on_connect_trakt=lambda: None, on_language_change=lambda c: None,
                        on_demo=lambda: None)
    return ov, got


def _kinds(ov):
    return [ov._conn_kind.itemData(i) for i in range(ov._conn_kind.count())]


def test_welcome_offers_nerd_quietly_and_boxes_only_then(app, settings):
    ov, got = _welcome(settings)
    assert "enigma2" not in _kinds(ov)
    assert ov._w_nerd.text() == "🤓 Nerd? Show advanced options"
    ov._toggle_nerd()
    assert settings.value("nerd_enabled") == "true" and nerd.current().on
    assert {"enigma2", "tvheadend", "hdhomerun"} <= set(_kinds(ov))
    assert ov._w_nerd.text().startswith("🤓 Nerd on")
    ov._stack.setCurrentIndex(1)
    ov._conn_kind.setCurrentIndex(ov._conn_kind.findData("enigma2"))
    assert not ov._finder.row.isHidden()
    ov._server.setText("http://192.168.1.10")
    ov._do_connect()
    assert got["args"][:4] == ("http://192.168.1.10", "", "", "enigma2")
    # Off again: the box kinds go, and the form is back on a normal kind.
    ov._toggle_nerd()
    assert "enigma2" not in _kinds(ov) and ov._conn_kind.currentData() == "xtream"
    assert settings.value("nerd_enabled") == "false"


def test_welcome_is_no_taller_with_nerd_on(app, settings):
    """The welcome card with Nerd on holds the box kinds and Find on my
    network, nothing more: never taller than the plain Xtream form (the
    source's own Nerd options live in the source dialog only)."""
    def height(kind):
        ov, got = _welcome(settings)
        got["_win"].resize(1100, 640)
        got["_win"].show()
        ov._stack.setCurrentIndex(1)
        i = ov._conn_kind.findData(kind)
        assert i >= 0, kind
        ov._conn_kind.setCurrentIndex(i)
        ov._fit_card(1)
        h = ov._card.sizeHint().height()
        got["_win"].close()
        return h
    # The tallest the plain form measures (a first window can measure a few
    # pixels apart from a later one - CI saw 504 then 486).
    plain = max(height("xtream"), height("xtream"))
    settings.setValue("nerd_enabled", "true")
    nerd.install(settings)
    for kind in ("xtream", "enigma2", "tvheadend", "hdhomerun"):
        assert height(kind) <= plain, kind


def test_diagnostics_never_carry_a_secret(app, settings):
    from dopeiptv.ui.nerd_settings import diagnostics
    settings.setValue("nerd_enabled", "true")
    settings.setValue("nerd_proxy", "http://user:hunter2@10.0.0.1:3128")
    settings.setValue("nerd_headers", "X-Token: s3cr3t-token")
    settings.setValue("nerd_spdif", "ac3")
    text = diagnostics(settings)
    assert "hunter2" not in text and "s3cr3t" not in text and "10.0.0.1" not in text
    assert "nerd_proxy = (set)" in text and "nerd_spdif = ac3" in text and "Nerd: on" in text


def test_the_source_dialog_scrolls_rather_than_outgrow_the_screen(app, settings):
    """With Nerd on and a source's own options open, the form is taller
    than a laptop's screen: it scrolls, OK and Cancel stay in view, and it
    shrinks again when they fold shut."""
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QDialogButtonBox

    from dopeiptv.ui.dialogs import PlaylistDialog
    settings.setValue("nerd_enabled", "true")
    nerd.install(settings)
    d = PlaylistDialog(playlist={"name": "Vu", "kind": "enigma2", "server": "http://10.0.0.2"})
    d.show()
    QTest.qWait(30)
    shut = d.height()
    d.nerd_panel.toggle.setChecked(True)
    QTest.qWait(30)
    screen = d.screen().availableGeometry().height()
    assert d.frameGeometry().height() <= screen
    content = d._scroll.widget().sizeHint().height()
    assert content > d._scroll.viewport().height()          # more than shown...
    assert d._scroll.verticalScrollBar().maximum() > 0      # ...so it scrolls
    buttons = d.findChild(QDialogButtonBox)
    assert buttons.parent() is d and buttons.isVisible()    # never scrolled away
    d.nerd_panel.toggle.setChecked(False)
    QTest.qWait(30)
    assert d.height() == shut
    d.close()


def test_check_now_keeps_the_focus(app, settings):
    """Pressing Check now (VPN) used to disable it while it looked - and a
    focused widget that is disabled hands the focus to the next field, the
    picon folder below, which lit up as if clicked."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QPushButton

    from dopeiptv.i18n import tr
    from dopeiptv.ui import nerd_settings
    settings.setValue("nerd_enabled", "true")
    nerd.install(settings)
    shell, page = _shell()
    nerd_settings.build(page, settings, QDialog())
    shell.arrange([("System", ["general", "nerd"])])
    shell.show_page("nerd")
    shell.resize(900, 700)
    shell.show()
    QTest.qWait(400)                      # the first check, at opening
    btn = next(b for b in shell.findChildren(QPushButton) if b.text() == tr("nerd_vpn_check"))
    btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    btn.setFocus()
    QTest.mouseClick(btn, Qt.MouseButton.LeftButton)
    QTest.qWait(30)
    assert QApplication.focusWidget() is btn
    QTest.qWait(400)
    assert QApplication.focusWidget() is btn and btn.isEnabled()
    shell.close()


def test_a_check_that_ends_after_settings_closed_does_nothing(app):
    """Test pressed, Settings closed before the answer: the line is gone
    when the check ends. Touching it then ended the whole app (an exception
    in a Qt slot aborts); now the late answer is dropped."""
    import time

    from PyQt6 import sip
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QWidget

    from dopeiptv.core.nerd_check import OK, Check
    from dopeiptv.ui.nerd_check_line import CheckLine

    def slow() -> Check:
        time.sleep(0.4)
        return Check(OK, "nerd_ck_udpxy_ok", {"ms": 1})
    holder = QWidget()
    line = CheckLine(lambda: slow, "udpxy", test=True, parent=holder)
    line.run(explicit=True)
    holder.deleteLater()
    QTest.qWait(50)
    assert sip.isdeleted(line)
    QTest.qWait(700)                      # the answer arrives: nothing happens


def test_a_check_line_says_what_it_found(app):
    """A check's answer, in words and with its details a click away."""
    from PyQt6.QtTest import QTest

    from dopeiptv.core.nerd_check import FAIL, Check
    from dopeiptv.ui.nerd_check_line import CheckLine
    line = CheckLine(lambda: (lambda: Check(FAIL, "nerd_ck_denied", {"code": 403}, "GET http://h/\\nResult: HTTP 403")),
                     "Your source", test=True)
    assert line.text.text() == "Not tested yet." and not line.isHidden()
    line.run()
    for _ in range(50):
        QTest.qWait(20)
        if line.result is not None:
            break
    assert line.result.state == FAIL and "HTTP 403" in line.text.text()
    assert line.glyph.text() == "✗" and not line.details.isHidden()


def test_the_problem_window_can_be_selected_and_copied(app):
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QGuiApplication

    from dopeiptv.ui.dialogs import ProblemDialog
    d = ProblemDialog(None, "The proxy does not work", "The proxy does not answer.",
                      "GET http://h/\\nError: ProxyError: Cannot connect to proxy")
    flags = d.details.textInteractionFlags()
    assert flags & Qt.TextInteractionFlag.TextSelectableByMouse
    assert d.details.isReadOnly() and "ProxyError" in d.details.toPlainText()
    d.copy_btn.click()
    copied = QGuiApplication.clipboard().text()
    assert "The proxy does not work" in copied and "ProxyError" in copied
    assert d.copied.text().endswith("Copied")


def test_the_nerd_page_lists_its_parts_and_what_is_changed(app, settings):
    """Under the switch, Nerd's pages as a list: each opens its page, says
    what is changed on it, and has the way back above its title."""
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtWidgets import QLineEdit

    from dopeiptv.i18n import tr
    from dopeiptv.ui import nerd_settings
    from dopeiptv.ui.settings_ui import SettingsPage, ToggleSwitch
    settings.setValue("nerd_enabled", "true")
    settings.setValue("nerd_rec_template", "{channel}/{title}")
    nerd.install(settings)
    page = SettingsPage("nerd", "Nerd", "")
    blank = lambda k, sz, c: QPixmap(sz, sz)  # noqa: E731
    pages = {}
    for key, kind in (("net", "network"), ("boxes", "box"), ("lists", "channels"),
                      ("player", "knobs"), ("rec", "rec"), ("serve", "remote")):
        sp = SettingsPage(f"nerd_{key}", key.title(), f"About {key}")
        sp.icon_kind, sp.icon_base = kind, blank
        pages[key] = sp
    opened = []
    nerd_settings.build(page, settings, QDialog(), boxes=[{"id": "b", "name": "Vu+", "kind": "enigma2"}],
                        pages=pages, open_page=opened.append)
    rows = page.findChildren(nerd_settings._PartRow)
    assert [r.accessibleName() for r in rows] == ["Net", "Boxes", "Lists", "Player", "Rec", "Serve"]
    summary = {r.accessibleName(): r.summary.text() for r in rows}
    assert summary["Rec"] == tr("nerd_part_changed", n=1)           # the template, stored
    assert summary["Net"] == tr("nerd_part_untouched")
    assert summary["Boxes"] == tr("nerd_part_boxes", n=1)
    # A change on a page shows the next time the list is looked at.
    proxy = next(e for e in pages["net"].findChildren(QLineEdit)
                 if e.placeholderText().startswith("http://192.168.1.1"))
    proxy.setText("http://10.0.0.1:3128")
    page.show()
    app.processEvents()
    assert next(r for r in rows if r.accessibleName() == "Net").summary.text() == tr("nerd_part_changed", n=1)
    remote = next(sw for sw in pages["serve"].findChildren(ToggleSwitch) if sw.accessibleName() == tr("nerd_remote"))
    remote.setChecked(True)
    page.hide()
    page.show()
    app.processEvents()
    assert next(r for r in rows if r.accessibleName() == "Serve").summary.text() == tr("nerd_part_on", port=3673)
    # A click, or Enter, opens the page; its crumb leads back.
    row = rows[3]
    pos = QPointF(row.rect().center())
    for kind in (QMouseEvent.Type.MouseButtonPress, QMouseEvent.Type.MouseButtonRelease):
        row.event(QMouseEvent(kind, pos, pos, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                              Qt.KeyboardModifier.NoModifier))
    assert opened == ["nerd_player"]
    from PyQt6.QtTest import QTest
    QTest.keyClick(rows[4], Qt.Key.Key_Return)
    assert opened == ["nerd_player", "nerd_rec"]
    pages["player"].crumb.click()
    assert opened[-1] == "nerd"
    # Off: the list goes with the rest of what Nerd holds.
    switch = next(sw for sw in page.findChildren(ToggleSwitch) if sw.accessibleName() == tr("nerd_switch"))
    switch.setChecked(False)
    assert all(not r.isVisibleTo(page) for r in rows)
    page.hide()
