"""Qt's own texts follow the app's language: the standard dialog buttons
stayed English in every language ("Ta bort?" answered with Yes / No)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import (  # noqa: E402
    QApplication, QDialogButtonBox, QMessageBox,
)

from dopeiptv import i18n  # noqa: E402
from dopeiptv.ui import qt_i18n  # noqa: E402

app = QApplication.instance() or QApplication([])


def _texts(box) -> list[str]:
    return sorted(b.text().replace("&", "") for b in box.buttons())


def _buttons():
    return QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                            | QDialogButtonBox.StandardButton.Cancel)


def test_standard_buttons_follow_the_language():
    try:
        i18n.set_language("sv")
        qt_i18n.apply_qt_language(app)
        assert "Avbryt" in _texts(_buttons())
        box = QMessageBox()
        box.setStandardButtons(QMessageBox.StandardButton.Yes
                               | QMessageBox.StandardButton.No)
        assert _texts(box) == sorted([i18n.tr("option_yes"),
                                      i18n.tr("option_no")])

        # Greek has no Qt catalogue at all: the buttons still translate.
        i18n.set_language("el")
        qt_i18n.apply_qt_language(app)
        assert i18n.tr("common_cancel") in _texts(_buttons())
    finally:
        i18n.set_language("en")
        qt_i18n.apply_qt_language(app)
    assert _texts(_buttons()) == ["Cancel", "OK"]


def test_every_button_string_exists():
    for key in qt_i18n.BUTTON_KEYS.values():
        assert not i18n.tr(key).startswith("?"), key
