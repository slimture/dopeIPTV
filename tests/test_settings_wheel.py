"""The Settings wheel guard must never freeze scrolling.

The guard swallows the wheel on value controls (combos/spinboxes/sliders) so
scrolling the page can't change a setting. But QScrollBar IS-A QAbstractSlider,
so a naive findChildren sweep also installed the guard on every scrollbar -
and QAbstractScrollArea delivers wheel events BY forwarding them to its own
scrollbar, so every scrollable child (most visibly the 27-entry language list)
ignored the wheel entirely. These tests exercise the real install helper and
lock in both sides: lists scroll, value controls stay guarded.
"""
import pytest


def _app():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _wheel(app, target, dy=-240):
    from PyQt6.QtCore import QPoint, QPointF, Qt
    from PyQt6.QtGui import QWheelEvent
    ev = QWheelEvent(
        QPointF(50, 50), QPointF(50, 50), QPoint(0, 0), QPoint(0, dy),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False)
    app.sendEvent(target, ev)


def test_wheel_guard_leaves_list_scrollbars_alone():
    app = _app()
    assert app is not None
    from PyQt6.QtWidgets import (
        QAbstractItemView, QDialog, QListWidget, QVBoxLayout,
    )
    from dopeiptv.ui.mw_settings import _SettingsMixin

    d = QDialog()
    lay = QVBoxLayout(d)
    lst = QListWidget()
    for i in range(27):
        lst.addItem(f"Language {i}")
    lst.setFixedHeight(200)
    lst.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    lay.addWidget(lst)
    d.show()

    _SettingsMixin._install_wheel_guard(d)
    _wheel(app, lst.viewport())
    assert lst.verticalScrollBar().value() > 0, (
        "wheel on the language list must scroll it - the guard has landed on "
        "the list's own scrollbar again (QScrollBar IS-A QAbstractSlider)")
    d.close()


def test_language_dropdown_popup_scrolls_with_guard_installed():
    """The original bug, end to end: the combo popup's scrollbar is created as
    a CHILD of the combo, so the guard sweep used to land on it and swallow
    every wheel event - the open language dropdown couldn't scroll on any
    platform. With the QScrollBar skip the popup must scroll."""
    app = _app()
    assert app is not None
    from PyQt6.QtWidgets import QDialog, QVBoxLayout
    from dopeiptv.ui.mw_settings import _SettingsMixin
    from dopeiptv.ui.theme import build_style

    # The theme's `combobox-popup: 0` is what caps the popup at
    # maxVisibleItems rows (bare Fusion shows all 27 full-height with nothing
    # to scroll), so apply it exactly like the app does - and restore after.
    app.setStyleSheet(build_style())
    try:
        d = QDialog()
        lay = QVBoxLayout(d)
        box = _SettingsMixin._combo(
            [(str(i), f"Language {i}") for i in range(27)], "0")
        lay.addWidget(box)
        d.show()
        _SettingsMixin._install_wheel_guard(d)

        box.showPopup()
        view = box.view()
        assert view.verticalScrollBar().maximum() > 0, (
            "popup is not capped - combobox-popup:0 from the theme is not "
            "in effect, so the 27-language dropdown would overflow the screen")
        _wheel(app, view.viewport())
        assert view.verticalScrollBar().value() > 0, (
            "wheel inside the open language dropdown must scroll it - the "
            "guard has landed on the popup's scrollbar again")
        box.hidePopup()
        d.close()
    finally:
        app.setStyleSheet("")


def test_wheel_guard_still_protects_value_controls():
    app = _app()
    assert app is not None
    from PyQt6.QtWidgets import QComboBox, QDialog, QSpinBox, QVBoxLayout
    from dopeiptv.ui.mw_settings import _SettingsMixin

    d = QDialog()
    lay = QVBoxLayout(d)
    combo = QComboBox()
    combo.addItems([f"opt {i}" for i in range(5)])
    combo.setCurrentIndex(2)
    spin = QSpinBox()
    spin.setRange(0, 10)
    spin.setValue(5)
    lay.addWidget(combo)
    lay.addWidget(spin)
    d.show()

    _SettingsMixin._install_wheel_guard(d)
    _wheel(app, combo)
    _wheel(app, spin)
    assert combo.currentIndex() == 2, "wheel must not change a combo's value"
    assert spin.value() == 5, "wheel must not change a spinbox's value"
    d.close()
