"""The channel list itself (ui/channel_list.py): the model's rows put in and
taken out without a reset - the row picked, the selection and the scroll
stay where they are, in the window's own list -, section headings that are
not content, the view's keys (a typed number zaps, Enter plays the row
picked, the arrows go to the player when one is up), a right click that
never moves the selection, and Favorites' rows dragged into their order:
where a drop lands in rows and in a grid, and only Favorites' own drag,
only where the window lets them be reordered."""
from __future__ import annotations

import pytest
from PyQt6.QtCore import QEvent, QItemSelectionModel, QMimeData, QPointF, QSize, Qt
from PyQt6.QtGui import QDragMoveEvent, QDropEvent, QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QAbstractItemView, QApplication, QListView, QVBoxLayout, QWidget

from dopeiptv.ui.channel_list import (
    CHANNEL_NUMBERS_KEY,
    FAV_DRAG_MIME,
    ChannelListModel,
    ChannelListView,
    channel_numbers_shown,
)

UR = Qt.ItemDataRole.UserRole


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _rows(n: int, prefix: str = "C") -> list[dict]:
    return [{"name": f"{prefix}{i}", "stream_id": f"{prefix}{i}"} for i in range(n)]


class Host(QWidget):
    """What the view asks of the window: the digits typed, the jump, the
    player's arrows."""

    def __init__(self, arrows: bool = False) -> None:
        super().__init__()
        self.digits: list[str] = []
        self.jumps = 0
        self._chan_buffer = ""
        self.arrow_calls: list[tuple] = []
        self._take_arrows = arrows
        self.view = ChannelListView(self)
        self.model = ChannelListModel(self.view)
        self.view.setModel(self.model)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        lay = QVBoxLayout(self)
        lay.addWidget(self.view)
        self.resize(320, 240)

    def _channel_digit(self, d: str) -> None:
        self.digits.append(d)
        self._chan_buffer += d

    def _channel_jump(self) -> None:
        self.jumps += 1
        self._chan_buffer = ""

    def _handle_player_arrow(self, key, step: bool = False) -> bool:
        self.arrow_calls.append((key, step))
        return self._take_arrows


def _key(view, key, text: str = "", mods=Qt.KeyboardModifier.NoModifier) -> None:
    view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, mods, text))


def _names(model) -> list[str]:
    return [model.data(model.index(r)) for r in range(model.rowCount())]


def test_rows_go_in_and_out_without_a_reset_and_the_pick_stays(app):
    host = Host()
    m, v = host.model, host.view
    items = _rows(50)
    m.set_items(items, "live")
    resets: list[int] = []
    m.modelReset.connect(lambda: resets.append(1))
    v.setCurrentIndex(m.index(30))
    host.show()
    QApplication.processEvents()
    v.scrollTo(m.index(30), QAbstractItemView.ScrollHint.PositionAtTop)
    scroll = v.verticalScrollBar().value()
    assert scroll > 0

    m.insert_items(40, _rows(3, "N"))                       # below what is shown
    assert m.items() is items, "the window's own list, not a copy"
    assert _names(m)[40:43] == ["N0", "N1", "N2"] and m.rowCount() == 53
    assert v.currentIndex().data(UR)["name"] == "C30"
    assert v.verticalScrollBar().value() == scroll

    m.insert_items(0, _rows(2, "T"))                         # above: the pick moves with its row
    assert v.currentIndex().data(UR)["name"] == "C30" and v.currentIndex().row() == 32

    m.append_items(_rows(1, "E"))
    assert _names(m)[-1] == "E0"
    m.insert_items(999, [])                                  # nothing to add, nothing done
    assert m.rowCount() == 56

    m.remove_rows([0, 1, 41, 42, 43, 999, -1])                # two runs, and rows that are not there
    assert m.rowCount() == 51 and "N0" not in _names(m) and "T0" not in _names(m)
    assert v.currentIndex().data(UR)["name"] == "C30" and v.currentIndex().row() == 30
    assert resets == [], "the list was never laid out anew"
    host.close()


def test_a_section_heading_is_no_row_to_pick_and_lays_the_list_out_anew(app):
    m = ChannelListModel()
    m.set_items(_rows(3), "vod")
    assert m.uniform is True
    resets: list[int] = []
    m.modelReset.connect(lambda: resets.append(1))
    m.insert_items(1, [{"_header": "Today"}])
    assert resets == [1] and m.uniform is False, "a heading's height differs: laid out row by row"
    assert m.flags(m.index(1)) == Qt.ItemFlag.NoItemFlags
    assert m.flags(m.index(0)) & Qt.ItemFlag.ItemIsSelectable
    assert m.data(m.index(1)) == "?" and m.item_at(1) == {"_header": "Today"}
    assert m.item_at(-1) is None and m.item_at(99) is None
    assert m.data(m.index(99)) is None


def test_a_rows_tip_is_plain_text_and_names_fall_back(app):
    m = ChannelListModel()
    m.set_items([{"title": "A film"}, {"name": "<b>x</b>", "_tip": "<img src=x> & more"}, {}], "vod")
    assert m.data(m.index(0)) == "A film" and m.data(m.index(2)) == "?"
    tip = m.data(m.index(1), Qt.ItemDataRole.ToolTipRole)
    assert "<img" not in tip and "&lt;img" in tip, "a file's name is never read as markup"
    assert m.data(m.index(0), Qt.ItemDataRole.ToolTipRole) is None
    changed: list[tuple] = []
    m.dataChanged.connect(lambda a, b: changed.append((a.row(), b.row())))
    m.refresh_all()
    assert changed == [(0, 2)]
    ChannelListModel().refresh_all()                          # empty: nothing to say


def test_a_typed_number_zaps_and_enter_plays_the_row_picked(app):
    host = Host()
    v, m = host.view, host.model
    m.set_items(_rows(5), "live")
    v.setCurrentIndex(m.index(2))
    played: list[int] = []
    v.doubleClicked.connect(lambda ix: played.append(ix.row()))

    _key(v, Qt.Key.Key_1, "1")
    _key(v, Qt.Key.Key_2, "2")
    assert host.digits == ["1", "2"] and played == []
    _key(v, Qt.Key.Key_Return, "\r")                          # a number typed: Enter jumps to it
    assert host.jumps == 1 and played == []

    _key(v, Qt.Key.Key_Return, "\r")                          # nothing typed: Enter plays the row
    assert played == [2]
    _key(v, Qt.Key.Key_Enter, "\r", Qt.KeyboardModifier.ControlModifier)
    assert played == [2], "Ctrl+Enter is not a plain Enter"
    v.setCurrentIndex(m.index(-1))
    _key(v, Qt.Key.Key_Return, "\r")
    assert played == [2], "no row picked, nothing played"


def test_the_arrows_go_to_the_player_when_it_takes_them(app):
    taking = Host(arrows=True)
    taking.model.set_items(_rows(5), "live")
    taking.view.setCurrentIndex(taking.model.index(1))
    _key(taking.view, Qt.Key.Key_Right)
    _key(taking.view, Qt.Key.Key_Left, mods=Qt.KeyboardModifier.ShiftModifier)
    assert taking.arrow_calls == [(Qt.Key.Key_Right, False), (Qt.Key.Key_Left, True)]
    assert taking.view.currentIndex().row() == 1, "the list did not move"
    _key(taking.view, Qt.Key.Key_Right, mods=Qt.KeyboardModifier.ControlModifier)
    assert len(taking.arrow_calls) == 2, "Ctrl+arrow is the list's"

    idle = Host(arrows=False)
    idle.model.set_items(_rows(5), "live")
    idle.view.setCurrentIndex(idle.model.index(1))
    _key(idle.view, Qt.Key.Key_Down)
    assert idle.arrow_calls == [] and idle.view.currentIndex().row() == 2
    _key(idle.view, Qt.Key.Key_Right)
    assert idle.arrow_calls == [(Qt.Key.Key_Right, False)], "asked, and the player said no"


def test_a_right_click_never_moves_the_selection_and_zapping_never_gathers(app):
    host = Host()
    v, m = host.view, host.model
    m.set_items(_rows(6), "live")
    host.show()
    QApplication.processEvents()
    v.setCurrentIndex(m.index(0))
    rect = v.visualRect(m.index(3))
    pos = QPointF(rect.center())
    right = QMouseEvent(QEvent.Type.MouseButtonPress, pos, v.viewport().mapToGlobal(pos),
                        Qt.MouseButton.RightButton, Qt.MouseButton.RightButton,
                        Qt.KeyboardModifier.NoModifier)
    v.mousePressEvent(right)
    assert v.currentIndex().row() == 0 and right.isAccepted()

    # A setCurrentIndex from code (zapping) clears and picks one row, even
    # while Ctrl is held for Ctrl+Right - it never toggles rows into a set.
    flags = v.selectionCommand(m.index(4), None)
    assert flags == (QItemSelectionModel.SelectionFlag.ClearAndSelect
                     | QItemSelectionModel.SelectionFlag.Rows)
    host.close()


def test_channel_numbers_are_off_unless_turned_on(app):
    class S(dict):
        def value(self, k, d=None):
            return self.get(k, d)
    assert channel_numbers_shown(None) is False
    assert channel_numbers_shown(S()) is False
    assert channel_numbers_shown(S({CHANNEL_NUMBERS_KEY: "true"})) is True
    assert channel_numbers_shown(S({CHANNEL_NUMBERS_KEY: True})) is True
    assert channel_numbers_shown(S({CHANNEL_NUMBERS_KEY: "false"})) is False


class FavHost(Host):
    def __init__(self, can: bool = True) -> None:
        super().__init__()
        self.can = can
        self.dropped: list[int] = []

    def _fav_can_reorder(self) -> bool:
        return self.can

    def _fav_reorder_drop(self, row: int) -> None:
        self.dropped.append(row)


def _drag(view, ev_type, pos, fav: bool = True):
    mime = QMimeData()
    mime.setData(FAV_DRAG_MIME if fav else "text/plain", b"1")
    # Qt takes a QPoint for a drag move and a QPointF for a drop; an older
    # PyQt6 (CI's) does not turn one into the other.
    drop = ev_type == QEvent.Type.Drop
    cls = QDropEvent if drop else QDragMoveEvent
    e = cls(pos if drop else pos.toPoint(), Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier, ev_type)
    (view.dropEvent if ev_type == QEvent.Type.Drop else view.dragMoveEvent)(e)
    return e


def test_a_favourite_dropped_lands_before_or_after_the_row_under_it(app):
    host = FavHost()
    v, m = host.view, host.model
    m.set_items(_rows(5), "live")
    host.show()
    QApplication.processEvents()
    r1, r4 = v.visualRect(m.index(1)), v.visualRect(m.index(4))
    top = QPointF(r1.center().x(), r1.top() + 2).toPoint()
    low = QPointF(r1.center().x(), r1.bottom() - 2).toPoint()
    assert v._drop_target(top)[0] == 1, "the upper half: before it"
    assert v._drop_target(low)[0] == 2, "the lower half: after it"
    row, line = v._drop_target(QPointF(r4.center().x(), r4.bottom() + 40).toPoint())
    assert row == 5 and line[1] == line[3] == r4.bottom(), "past the last row: at the end"

    v.setViewMode(QListView.ViewMode.IconMode)
    v.setGridSize(QSize(80, 60))
    QApplication.processEvents()
    g = v.visualRect(m.index(2))
    left = QPointF(g.left() + 2, g.center().y()).toPoint()
    right = QPointF(g.right() - 2, g.center().y()).toPoint()
    assert v._drop_target(left)[0] == 2 and v._drop_target(right)[0] == 3
    x = v._drop_target(right)[1]
    assert x[0] == x[2] == g.right(), "a grid's marker stands upright"
    empty = FavHost()
    assert empty.view._drop_target(top) is None
    host.close()


def test_only_favorites_own_drag_reorders_and_only_where_it_may(app):
    host = FavHost()
    v, m = host.view, host.model
    m.set_items(_rows(4), "live")
    host.show()
    QApplication.processEvents()
    at = v.visualRect(m.index(2)).center()
    at.setY(v.visualRect(m.index(2)).bottom() - 2)

    assert not _drag(v, QEvent.Type.DragMove, QPointF(at)).isAccepted(), "not in Favorites"
    v.set_fav_drag(True)
    assert v.dragEnabled() and v.viewport().acceptDrops()
    move = _drag(v, QEvent.Type.DragMove, QPointF(at))
    assert move.isAccepted() and v._drop_line is not None, "the line shows where it lands"
    assert not _drag(v, QEvent.Type.DragMove, QPointF(at), fav=False).isAccepted(), \
        "something else dragged in"
    _drag(v, QEvent.Type.Drop, QPointF(at))
    assert host.dropped == [3] and v._drop_line is None

    host.can = False                                       # e.g. a sorted or filtered view
    assert not _drag(v, QEvent.Type.DragMove, QPointF(at)).isAccepted()
    _drag(v, QEvent.Type.Drop, QPointF(at))
    assert host.dropped == [3], "nothing reordered"
    v.setViewMode(QListView.ViewMode.IconMode)
    assert v.dragEnabled(), "a grid does not turn it off"
    v.set_fav_drag(False)
    assert not v.dragEnabled()
    host.close()
