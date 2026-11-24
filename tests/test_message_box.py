"""ui/message_box.py: every message and question in the app's own look.

Each kind's buttons and answers - OK, Yes / No, a few choices, a line of
text, an item, a number - with Enter (the default, focused as it opens),
Escape and the window's close (the cancel answer) pressed for real on the
modal window; its words plain text; a long text scrolling within the
screen; mirrored right to left; every theme readable; a screen reader's
names for everything it holds; nothing left behind once it is answered."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, Qt, QTimer  # noqa: E402
from PyQt6.QtGui import QCloseEvent  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QComboBox, QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox,
    QWidget,
)

from dopeiptv import i18n  # noqa: E402
from dopeiptv.ui import a11y, message_box, theme  # noqa: E402
from dopeiptv.ui.message_box import MessageDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _english(app):
    i18n.set_language("en")
    app.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
    theme.apply_theme(theme="graphite")
    yield
    i18n.set_language("en")
    app.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
    theme.apply_theme(theme="graphite")


def _answer(*steps, seen=None):
    """Once the modal window is up: *steps* done on it - a key (pressed on
    what has the focus), a string (typed there), "close" (its close
    button), or a callable given the window. A window never answered is
    turned down after 5 s, so a test cannot hang."""
    state = {"tries": 0}

    def go() -> None:
        w = QApplication.activeModalWidget()
        if w is None:
            state["tries"] += 1
            if state["tries"] < 500:
                QTimer.singleShot(10, go)
            return
        if seen is not None:
            seen.append(w)
        for step in steps:
            target = w.focusWidget() or w
            if callable(step):
                step(w)
            elif step == "close":
                w.close()
            elif isinstance(step, str):
                QTest.keyClicks(target, step)
            else:
                QTest.keyClick(target, step)

    def give_up() -> None:
        w = QApplication.activeModalWidget()
        if w is not None:
            w.reject()
    QTimer.singleShot(0, go)
    QTimer.singleShot(5000, give_up)


def _flush() -> None:
    from PyQt6.QtCore import QCoreApplication
    for _ in range(3):
        QApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


ENTER, ESC = Qt.Key.Key_Return, Qt.Key.Key_Escape


# -- the messages ------------------------------------------------------------------------

@pytest.mark.parametrize("call", ["information", "warning", "error"])
def test_a_message_has_ok_alone_and_any_key_way_out_closes_it(app, call):
    for how in (ENTER, ESC, "close"):
        seen: list = []
        _answer(how, seen=seen)
        assert getattr(message_box, call)(None, "Title", "Words") is None
        box = seen[0]
        assert [b.text() for b in box.buttons()] == [i18n.tr("common_ok")]
        assert box.buttons()[0].objectName() == "Primary"
        assert box.windowTitle() == "Title" and box.heading.text() == "Title"


def test_each_message_has_the_mark_of_its_kind(app):
    for call, kind in (("information", message_box.INFO), ("warning", message_box.WARNING),
                       ("error", message_box.ERROR)):
        seen: list = []
        _answer(ENTER, seen=seen)
        getattr(message_box, call)(None, "T", "W")
        assert seen[0]._mark is not None and seen[0]._mark.kind == kind


# -- Yes / No ----------------------------------------------------------------------------

@pytest.mark.parametrize("default_yes", [True, False])
@pytest.mark.parametrize("danger", [False, True])
def test_yes_or_no_answers_as_asked(app, default_yes, danger):
    yes, no = i18n.tr("option_yes"), i18n.tr("option_no")
    seen: list = []
    opened: list = []
    _answer(lambda w: opened.append((w.focusWidget(), w.buttons()[1].isDefault())), ENTER,
            seen=seen)
    assert message_box.confirm(None, "T", "Sure?", default_yes=default_yes,
                               danger=danger) is default_yes
    box = seen[0]
    texts = [b.text() for b in box.buttons()]
    # What Enter answers is at the end, focused as it opens.
    assert texts == ([no, yes] if default_yes else [yes, no])
    assert box.default_index() == 1 and opened == [(box.buttons()[1], True)]
    roles = [b.role for b in box.buttons()]
    if danger:
        assert message_box.DANGER in roles and box._mark.kind == message_box.WARNING
        # The destructive one red, filled only where Enter does it.
        danger_at = roles.index(message_box.DANGER)
        assert box.buttons()[danger_at].objectName() == "MessageDanger"
    else:
        assert message_box.DANGER not in roles and box._mark.kind == message_box.QUESTION
    for how in (ESC, "close"):
        _answer(how)
        assert message_box.confirm(None, "T", "Sure?", default_yes=default_yes,
                                   danger=danger) is False


def test_yes_or_no_in_the_callers_own_words(app):
    seen: list = []
    _answer(ENTER, seen=seen)
    assert message_box.confirm(None, "T", "W", yes="Delete", no="Keep") is True
    assert [b.text() for b in seen[0].buttons()] == ["Keep", "Delete"]


def test_the_other_button_by_the_keyboard(app):
    """Tab (or a click) on the other button: its answer."""
    _answer(Qt.Key.Key_Backtab, Qt.Key.Key_Space)
    assert message_box.confirm(None, "T", "W", default_yes=True) is False
    _answer(lambda w: w.buttons()[0].click())
    assert message_box.confirm(None, "T", "W", default_yes=False) is True


# -- a few choices -----------------------------------------------------------------------

OPTIONS = [("Watch it", "primary"), ("Stop and switch", "danger"), ("Play anyway", "normal"),
           ("Cancel", "normal")]


def test_a_choice_answers_the_index_or_none(app):
    _answer(ENTER)
    assert message_box.choose(None, "T", "W", OPTIONS) == 0
    _answer(ENTER)
    assert message_box.choose(None, "T", "W", OPTIONS, default=2) == 2
    _answer(ESC)
    assert message_box.choose(None, "T", "W", OPTIONS) is None
    _answer("close")
    assert message_box.choose(None, "T", "W", OPTIONS, escape=3) == 3
    _answer(lambda w: w.buttons()[1].click())
    assert message_box.choose(None, "T", "W", OPTIONS) == 1


def test_buttons_side_by_side_when_they_fit_else_one_under_another(app):
    two = MessageDialog(None, "T", "W", options=[("No", "normal"), ("Yes", "primary")])
    many = MessageDialog(None, "T", "W", options=OPTIONS)
    wide = MessageDialog(None, "T", "W", options=[("A very long choice of words " * 2, "normal"),
                                                  ("And another as long as it " * 2, "primary")])
    assert not two.stacked() and many.stacked() and wide.stacked()
    for d in (two, many, wide):
        d.deleteLater()


def test_enter_never_answers_what_stops_or_deletes(app):
    """Stop the recording? Delete the download? A quick Enter keeps it."""
    pick = message_box.enter_index
    assert pick([("Stop recording", "danger"), ("Keep recording", "primary")]) == 1
    assert pick([("Stop and switch", "danger"), ("Go on in the background", "normal"),
                 ("Keep watching", "primary")]) == 2
    assert pick([("Delete", "danger"), ("Delete all", "danger"), ("Cancel", "normal")]) == 2
    # The first stays what Enter answers whenever it does nothing for good.
    assert pick(OPTIONS) == 0
    assert pick([("Cancel", "normal"), ("Play", "primary")]) == 0
    assert pick([("Keep dopeIPTV open", "primary"), ("Close anyway", "danger")]) == 0
    assert pick([("Delete", "danger")]) == 0 and pick([]) == 0


def test_enter_on_the_recordings_questions_keeps_the_recording(app):
    """The window's own questions, pressed for real: "Stop the recording?"
    and another channel while the one on screen records from its buffer -
    Enter keeps recording; only the Stop button stops."""
    from types import SimpleNamespace

    from dopeiptv.ui.mw_recording import _RecordingMixin

    class Host(QWidget):
        _choice_dialog = _RecordingMixin._choice_dialog
        _rec_ask_stop = _RecordingMixin._rec_ask_stop
        _guard_buffer_switch = _RecordingMixin._guard_buffer_switch
        _with_conn_warning = _RecordingMixin._with_conn_warning

    done: list = []
    host = Host()
    host.rec = SimpleNamespace(
        jobs=[{"id": "a", "status": "recording", "title": "News"}],
        cancel=lambda jid: done.append(("cancel", jid)),
        finish_all_inplayer=lambda why: done.append(("finish", why)),
        keep_in_background=lambda jid: done.append(("background", jid)))
    host.client = SimpleNamespace(max_connections=1)
    _answer(ENTER)
    assert host._rec_ask_stop("a") is False and done == []
    _answer(ENTER)
    assert host._guard_buffer_switch(host.rec.jobs[0], "SVT2") is False and done == []
    _answer(lambda w: w.buttons()[0].click())
    assert host._rec_ask_stop("a") is True and done == [("cancel", "a")]
    host.deleteLater()


def test_a_danger_button_is_filled_only_with_no_primary_beside_it(app):
    alone = MessageDialog(None, "T", "W", options=[("No", "normal"), ("Delete", "danger")])
    beside = MessageDialog(None, "T", "W", options=[("Delete", "danger"), ("Keep", "primary")])
    c = message_box.colours()
    assert f"background: {c['danger']}" in alone.styleSheet()
    assert f"background: {c['danger']}" not in beside.styleSheet()
    assert c["danger_text"] in beside.styleSheet()
    alone.deleteLater()
    beside.deleteLater()


def test_the_window_s_choice_keeps_its_hooks_and_its_check_box(app):
    """mw_recording's _choice_dialog is drawn here: Enter answers the first
    option unless it stops or deletes something - then the safer choice -,
    "Don't show this again" kept with the answer; the phone's and TV mode's
    hooks still ask instead."""
    from PyQt6.QtCore import QSettings

    from dopeiptv.ui.mw_recording import _RecordingMixin
    host = QWidget()
    host.settings = QSettings("dopeiptv-test", "message-box")
    host.settings.clear()
    seen: list = []
    opened: list = []
    _answer(lambda w: opened.append(w.focusWidget().text()),
            lambda w: w.findChild(QCheckBox).setChecked(True), ENTER, seen=seen)
    got = _RecordingMixin._choice_dialog(host, "Recording", "Stop it?",
                                         [("Stop", "danger"), ("Keep", "primary")],
                                         dont_ask_setting="mb_test_dont_ask")
    assert got == 1 and opened == ["Keep"]
    assert host.settings.value("mb_test_dont_ask") == "true"
    assert isinstance(seen[0], MessageDialog) and seen[0]._mark.kind == message_box.WARNING
    host.settings.clear()
    _answer(ENTER)
    assert _RecordingMixin._choice_dialog(host, "T", "W", OPTIONS) == 0
    _answer(ESC)
    assert _RecordingMixin._choice_dialog(host, "T", "W", [("A", "normal")]) is None
    host._tv_on = True
    host._tv_ask = lambda *a: 7
    assert _RecordingMixin._choice_dialog(host, "T", "W", [("A", "normal")]) == 7
    host._phone_ask_hook = lambda *a: 9
    assert _RecordingMixin._choice_dialog(host, "T", "W", [("A", "normal")]) == 9
    host.deleteLater()


# -- values asked for --------------------------------------------------------------------

def test_a_line_of_text(app):
    _answer(Qt.Key.Key_End, " room", ENTER)
    assert message_box.get_text(None, "Rename", "Name:", text="Living") == ("Living room", True)
    _answer("typed", ESC)
    assert message_box.get_text(None, "Rename", "Name:", text="Living") == ("", False)
    _answer("close")
    assert message_box.get_text(None, "Rename", "Name:") == ("", False)
    # Cancel pressed is no OK.
    _answer(lambda w: w.buttons()[0].click())
    assert message_box.get_text(None, "Rename", "Name:", text="x") == ("", False)


def test_a_password_is_hidden_and_not_kept_in_its_field(app):
    fields: list = []

    def look(w) -> None:
        f = w.findChild(QLineEdit)
        fields.append(f)
        assert f.echoMode() == QLineEdit.EchoMode.Password
        assert f.maxLength() == 6 and f.placeholderText() == "••••"
        assert f.accessibleName() == "PIN"
    _answer(look, "4711", ENTER)
    got = message_box.get_text(None, "Lock", "Your PIN", password=True, placeholder="••••",
                               max_length=6, name="PIN")
    assert got == ("4711", True)
    assert fields[0].text() == ""


def test_an_item_from_a_list(app):
    items = ["/media/hdd/movie/", "/media/hdd/movie/Sport/", "/media/usb/"]
    _answer(Qt.Key.Key_Down, ENTER)
    assert message_box.get_item(None, "Move", "Where to:", items, 1) == (items[2], True)
    _answer(ESC)
    assert message_box.get_item(None, "Move", "Where to:", items, 1) == (items[1], False)
    seen: list = []
    _answer(lambda w: w.findChild(QComboBox).setEditText("/media/new/"), ENTER, seen=seen)
    assert message_box.get_item(None, "Move", "Where to:", items, 0, True) == ("/media/new/", True)
    assert seen[0].findChild(QComboBox).isEditable()
    _answer(ENTER)
    assert message_box.get_item(None, "Move", "Where to:", [], 0) == ("", True)


def test_a_whole_number_within_its_bounds(app):
    _answer(Qt.Key.Key_Up, ENTER)
    assert message_box.get_int(None, "Sleep", "Minutes:", 60, 1, 1440, 5) == (65, True)
    _answer(ESC)
    assert message_box.get_int(None, "Sleep", "Minutes:", 60, 1, 1440, 5) == (60, False)
    seen: list = []
    _answer(lambda w: w.findChild(QSpinBox).setValue(5000), ENTER, seen=seen)
    assert message_box.get_int(None, "Page", "Page:", 199, 100, 899) == (899, True)


def test_ok_and_cancel_with_ok_the_default(app):
    seen: list = []
    opened: list = []
    _answer(lambda w: opened.append((w.focusWidget(), w.buttons()[1].isDefault())), ESC,
            seen=seen)
    message_box.get_text(None, "T", "L")
    box = seen[0]
    assert [b.text() for b in box.buttons()] == [i18n.tr("common_cancel"), i18n.tr("common_ok")]
    assert box.buttons()[1].objectName() == "Primary"
    assert opened == [(box.findChild(QLineEdit), True)]     # typed in, OK on Enter
    assert box._mark is None and not box.stacked()


# -- what it shows -----------------------------------------------------------------------

HOSTILE = 'News <img src="file:///etc/passwd"><b>bold</b>'


def test_its_words_are_plain_text_as_written(app):
    d = MessageDialog(None, HOSTILE, HOSTILE)
    for lbl in (d.heading, d.body):
        assert lbl.textFormat() == Qt.TextFormat.PlainText
        assert lbl.accessibleName() == HOSTILE
    assert d.windowTitle() == HOSTILE and d.accessibleDescription() == HOSTILE
    assert d.buttons()[0].accessibleDescription() == HOSTILE     # read out with the focus
    d.deleteLater()


def test_a_long_run_with_no_space_gets_places_to_break(app):
    path = "/home/someone/Documents/" + "a-folder-name-without-any-spaces" * 3 + "/backup.json"
    d = MessageDialog(None, "T", f"Could not save {path}")
    assert "​" in d.body.text() and d.body.accessibleName() == f"Could not save {path}"
    d.show()
    QApplication.processEvents()
    assert d.width() == message_box.WIDTH
    assert d.body.geometry().right() < d.width()
    d.close()
    d.deleteLater()


def test_a_long_text_scrolls_inside_a_window_that_fits_the_screen(app):
    words = " ".join(["A sentence that goes on for a while."] * 300)
    d = MessageDialog(None, "Long", words)
    d.show()
    QApplication.processEvents()
    area = d.findChild(QScrollArea)
    assert area is not None and area.widget() is d.body
    screen = d.screen().availableGeometry().height()
    assert d.height() < screen
    assert area.height() <= message_box.TEXT_MOST
    assert area.verticalScrollBar().maximum() > 0
    assert area.accessibleName() == "Long"          # a stop of its own, named
    d.close()
    d.deleteLater()
    short = MessageDialog(None, "Short", "A few words.")
    assert short.findChild(QScrollArea) is None
    short.deleteLater()


def test_the_window_grows_with_its_words_and_keeps_its_width(app):
    small = MessageDialog(None, "T", "One line.")
    big = MessageDialog(None, "T", "A longer text. " * 30)
    for d in (small, big):
        d.show()
    QApplication.processEvents()
    assert small.width() == big.width() == message_box.WIDTH
    assert big.height() > small.height()
    for d in (small, big):
        assert d.body.height() >= d.body.heightForWidth(d.body.width())   # nothing cut
        d.close()
        d.deleteLater()


def test_mirrored_right_to_left(app):
    i18n.set_language("ar")
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    d = MessageDialog(None, "T", "W", kind=message_box.QUESTION,
                      options=[("No", "normal"), ("Yes", "primary")], default=1)
    d.show()
    QApplication.processEvents()
    assert d.layoutDirection() == Qt.LayoutDirection.RightToLeft
    # The mark at the right, the words to its left; the default at the far left.
    assert d._mark.geometry().left() > d.heading.geometry().left()
    no, yes = d.buttons()
    assert yes.mapTo(d, QPoint(0, 0)).x() < no.mapTo(d, QPoint(0, 0)).x()
    d.close()
    d.deleteLater()


@pytest.mark.parametrize("key", sorted(theme.THEMES))
def test_every_theme_reads(key):
    theme.apply_theme(theme=key, accent="theme")
    c = message_box.colours()
    p = theme.P
    assert theme.contrast(c["heading"], c["bg"]) >= 4.5, key
    assert theme.contrast(c["body"], c["bg"]) >= 4.5, key
    assert theme.contrast(c["muted"], c["bg"]) >= 4.5, key
    assert theme.contrast(c["ring"], c["bg"]) >= 3, key
    assert theme.contrast(c["danger_ink"], c["danger"]) >= 4.5, key
    assert theme.contrast(c["danger_text"], p["btn"]) >= 4.5, key
    assert theme.contrast(p["on_accent"], p["accent"]) >= 3, key       # the primary
    for kind in message_box.KINDS:
        assert theme.contrast(c[f"{kind}_glyph"], c[f"{kind}_wash"]) >= 3, (key, kind)
    assert c["bg"] == p["bg"]                       # the theme's own window colour


def test_its_colours_come_from_the_theme(app):
    theme.apply_theme(theme="light")
    d = MessageDialog(None, "T", "W", kind=message_box.WARNING)
    assert theme.P["bg"] in d.styleSheet()
    theme.apply_theme(theme="graphite")
    assert theme.P["bg"] not in d.styleSheet()      # read as it was made: the light one's
    d.deleteLater()


def test_the_marks_are_drawn_in_every_kind_and_direction(app):
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QColor, QImage, QPainter
    for kind in message_box.KINDS:
        for mirrored in (False, True):
            img = QImage(40, 40, QImage.Format.Format_ARGB32)
            img.fill(QColor(0, 0, 0, 0))
            pr = QPainter(img)
            message_box.paint_mark(pr, kind, QRectF(0, 0, 40, 40), mirrored)
            pr.end()
            glyph = QColor(message_box.colours()[f"{kind}_glyph"])
            centre = [img.pixelColor(x, y) for x in range(14, 26) for y in range(8, 32)]
            assert any(abs(c.red() - glyph.red()) + abs(c.green() - glyph.green())
                       + abs(c.blue() - glyph.blue()) < 40 for c in centre), kind


# -- a screen reader and the keyboard ----------------------------------------------------

def test_every_control_has_words_for_a_screen_reader(app):
    boxes = [MessageDialog(None, "T", "W", options=OPTIONS),
             MessageDialog(None, "T", " ".join(["Long words."] * 400))]
    boxes[0].add_check(i18n.tr("dont_show_again"))
    for field in (QLineEdit(), QComboBox(), QSpinBox()):
        d = message_box._value_dialog(None, "Title", "A label:")
        d.add_field(field, "A label:")
        boxes.append(d)
    for d in boxes:
        assert a11y.unnamed(d) == [], [a11y.describe(w) for w in a11y.unnamed(d)]
        d.deleteLater()


def test_tab_goes_as_the_window_reads(app):
    d = message_box._value_dialog(None, "T", "L")
    field = d.add_field(QLineEdit(), "L")
    check = d.add_check("Remember")
    cancel, ok = d.buttons()
    stops, w = [], field
    for _ in range(50):
        if w.focusPolicy() & Qt.FocusPolicy.TabFocus and w is not d:
            stops.append(w)
        w = w.nextInFocusChain()
        if w is field:
            break
    assert stops == [field, check, cancel, ok]
    d.deleteLater()


def test_the_focus_starts_on_the_default_and_its_ring_shows(app):
    d = MessageDialog(None, "T", "W", options=[("No", "normal"), ("Yes", "primary")], default=0)
    d.show()
    QApplication.processEvents()
    no, yes = d.buttons()
    d.activateWindow()
    no.setFocus()
    QApplication.processEvents()
    assert d.focusWidget() is no and no.isDefault()

    def ring_at_edge(button) -> bool:
        from PyQt6.QtGui import QColor
        image = button.grab().toImage()
        ring = QColor(message_box.colours()["ring"])
        return any(sum(abs(a - b) for a, b in zip(
            image.pixelColor(x, image.height() // 2).getRgb()[:3], ring.getRgb()[:3],
            strict=True)) < 60 for x in range(0, 3))
    assert no.hasFocus() and ring_at_edge(no)
    assert not ring_at_edge(yes)                # only where the keyboard is
    d.close()
    d.deleteLater()


def test_a_close_event_answers_as_escape(app):
    d = MessageDialog(None, "T", "W", options=[("No", "normal"), ("Yes", "primary")],
                      default=1, escape=0)
    d.show()
    QApplication.processEvents()
    QApplication.sendEvent(d, QCloseEvent())
    assert d.chosen() == 0 and d.answer(0) == 0
    d.deleteLater()


def test_nothing_is_left_behind_once_answered(app):
    parent = QWidget()
    for _ in range(3):
        _answer(ENTER)
        message_box.information(parent, "T", "W")
        _answer(ESC)
        message_box.get_text(parent, "T", "L")
        d = MessageDialog(parent, "T", "W")
        d.add_check("x")
        _answer(ENTER)
        message_box.run(d)
    _flush()
    assert parent.findChildren(MessageDialog) == []
    assert parent.findChildren(QLabel) == [] and parent.findChildren(QPushButton) == []
    parent.deleteLater()


def test_a_test_drives_it_through_exec(app, monkeypatch):
    """What the app's tests lean on: exec patched, an answer by press() or
    by what exec returns - accepted the default, rejected the escape."""
    monkeypatch.setattr(MessageDialog, "exec", lambda self: self.press(0))
    assert message_box.confirm(None, "T", "W", default_yes=True) is False   # No is at 0
    monkeypatch.setattr(MessageDialog, "exec", lambda self: 1)
    assert message_box.confirm(None, "T", "W", default_yes=False) is False   # the default: No
    assert message_box.get_text(None, "T", "L", text="kept") == ("kept", True)
    monkeypatch.setattr(MessageDialog, "exec", lambda self: 0)
    assert message_box.choose(None, "T", "W", OPTIONS, escape=3) == 3
    assert message_box.get_int(None, "T", "L", 7) == (7, False)
