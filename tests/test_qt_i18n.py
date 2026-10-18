"""Qt's own texts follow the app's language: the standard dialog buttons
stayed English in every language ("Ta bort?" answered with Yes / No)."""

import os
import sys
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTranslator  # noqa: E402
from PyQt6.QtGui import QImage  # noqa: E402
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


def test_the_catalogue_is_what_qt_reads(tmp_path):
    data = qt_i18n.build_qm({("QPlatformTheme", "OK"): "Sawa",
                             ("QGnomeTheme", "&OK"): "Ναι ✓",
                             ("QPlatformTheme", "Cancel"): "取消"})
    path = tmp_path / "buttons.qm"
    path.write_bytes(data)
    t = QTranslator()
    assert t.load(str(path))
    assert t.translate("QPlatformTheme", "OK") == "Sawa"
    assert t.translate("QGnomeTheme", "&OK") == "Ναι ✓"
    assert t.translate("QPlatformTheme", "Cancel") == "取消"
    # Another context, or a text it does not hold, is left to Qt.
    assert t.translate("QFileDialog", "OK") == ""
    assert t.translate("QPlatformTheme", "Abort") == ""


def test_a_worker_decoding_an_image_never_runs_python():
    """Qt asks the translators from any thread: a worker whose logo fails
    to decode asks for the error text. A translator answering in Python
    made every such worker wait for the GIL inside Qt, and with a big
    playlist starting up the whole app froze."""
    calls = []

    def profile(frame, event, _arg):
        if event == "call" and frame.f_code.co_filename.endswith(
                "qt_i18n.py"):
            calls.append(frame.f_code.co_name)

    def decode_junk():
        sys.setprofile(profile)
        for data in (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,
                     b"GIF89a" + b"\x01" * 40, b"not an image"):
            QImage().loadFromData(data)
        sys.setprofile(None)

    try:
        i18n.set_language("sv")
        qt_i18n.apply_qt_language(app)
        assert qt_i18n._installed
        assert all(type(t) is QTranslator
                   for t in qt_i18n._installed.values())
        worker = threading.Thread(target=decode_junk)
        worker.start()
        worker.join()
    finally:
        i18n.set_language("en")
        qt_i18n.apply_qt_language(app)
    assert calls == []
