"""Home's back chip stays at the top when the page scrolls (it scrolled
away with the featured strip)."""
import pytest

pytest.importorskip("PyQt6")

_APP = None


@pytest.fixture(scope="module")
def app():
    """One QApplication for the module, kept alive past it (Qt aborts when
    widgets outlive a deleted application)."""
    global _APP
    from PyQt6.QtWidgets import QApplication
    _APP = QApplication.instance() or QApplication([])
    return _APP


def test_the_back_chip_follows_its_slot_and_then_sticks(app):
    from PyQt6.QtWidgets import QWidget

    from dopeiptv.ui.mw_home import HomePage

    page = HomePage.__new__(HomePage)
    QWidget.__init__(page)
    page.resize(800, 600)
    page._back = QWidget(page)
    page._back.setFixedSize(34, 34)
    holder = QWidget(page)
    holder.resize(800, 200)
    slot = QWidget(holder)
    slot.setFixedSize(34, 34)
    page._back_slot = slot
    page.isVisible = lambda: True            # no real show: Home's showEvent needs a built page
    holder.move(0, 40)
    slot.move(16, 20)
    page._place_back()
    assert page._back.pos().y() == 60        # over its slot
    holder.move(0, -400)                       # scrolled far past it
    page._place_back()
    assert page._back.pos().y() == HomePage.BACK_TOP and page._back.pos().x() == 16
