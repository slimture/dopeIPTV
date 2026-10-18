"""Qt's own strings in the app's language.

Qt draws some text itself - the standard dialog buttons above all (OK,
Cancel, Yes, No, Close) - and with no translator installed those stayed
English in every language: a Swedish "Ta bort?" answered with "Yes" / "No".

Two layers, so every language is covered:

* Qt's own catalogue (``qtbase_<lang>.qm``, shipped with PyQt6) where one
  exists. It also translates the file and colour dialogs.
* The standard buttons from the app's own strings, for every language -
  eight of ours (Greek, Hindi, Indonesian, Serbian, Swahili, Thai, Urdu,
  Vietnamese) have no Qt catalogue at all. Installed last, so it is asked
  first and the buttons read the same as the rest of the app.

Both are plain catalogues that Qt reads by itself. The buttons were once a
QTranslator subclass answering in Python, and Qt asks every translator from
whichever thread wants a string - a worker decoding a logo asks too, from
inside Qt - so each of those questions had to wait for the GIL. With a big
playlist starting up, that froze the whole app (macOS, 1.2.15 testing).
"""

from __future__ import annotations

import os
import struct
import tempfile

from PyQt6.QtCore import (
    QDateTime, QLibraryInfo, QLocale, QStandardPaths, QTranslator,
)

from ..core.log import log
from ..i18n import current_language, tr

# Qt's source text for a standard button (its "&" accelerator removed) ->
# the app's string for it.
BUTTON_KEYS = {
    "OK": "common_ok", "Cancel": "common_cancel", "Yes": "option_yes",
    "No": "option_no", "Close": "common_close", "Apply": "common_apply",
    "Reset": "common_reset", "Save": "common_save", "Open": "common_open",
    "Discard": "common_discard",
    "Restore Defaults": "common_restore_defaults", "Help": "common_help",
}

# Our language code -> the name of Qt's catalogue, where the two differ.
_QT_CODES = {"pt": "pt_BR", "zh": "zh_CN"}
# ... and the locale its dates are written in. Serbian is the Latin script
# here, which plain "sr" (Cyrillic) is not.
_LOCALES = {"pt": "pt_BR", "zh": "zh_CN", "sr": "sr_Latn_RS"}


def app_locale() -> QLocale:
    """The locale of the app's language - not the system's."""
    code = current_language()
    return QLocale(_LOCALES.get(code, code))


def format_when(when, fmt: str = "ddd d MMM HH:mm") -> str:
    """*when* (a datetime or a Unix timestamp) in the app's language, with a
    Qt date format. strftime's %a and %b are the C locale's - English names
    in every language."""
    ts = when if isinstance(when, (int, float)) else when.timestamp()
    return app_locale().toString(QDateTime.fromSecsSinceEpoch(int(ts)), fmt)


# Where Qt looks the buttons up: QPlatformTheme by default, QGnomeTheme
# (with "&OK", "&Cancel") under GNOME and GTK.
_THEMES = ("QPlatformTheme", "QGnomeTheme")

_QM_MAGIC = bytes((0x3C, 0xB8, 0x64, 0x18, 0xCA, 0xEF, 0x9C, 0x95,
                   0xCD, 0x21, 0x1C, 0xBF, 0x60, 0xA1, 0xBD, 0xDD))


def _elf_hash(text: bytes) -> int:
    """The hash a .qm file files its messages under (Qt's elfHash)."""
    h = 0
    for byte in text:
        h = ((h << 4) + byte) & 0xFFFFFFFF
        g = h & 0xF0000000
        if g:
            h ^= g >> 24
        h &= ~g & 0xFFFFFFFF
    return h or 1


def build_qm(messages: dict[tuple[str, str], str]) -> bytes:
    """A Qt translation catalogue (.qm) of *messages*: (context, source
    text) -> translation. Only the hash table and the messages - all
    QTranslator needs to answer."""
    body = bytearray()
    index = []
    for (context, source), text in messages.items():
        src = source.encode("utf-8")
        ctx = context.encode("utf-8")
        translation = text.encode("utf-16-be")
        index.append((_elf_hash(src), len(body)))
        body += b"\x03" + struct.pack(">I", len(translation)) + translation
        body += b"\x06" + struct.pack(">I", len(src)) + src
        body += b"\x07" + struct.pack(">I", len(ctx)) + ctx
        body += b"\x01"
    hashes = b"".join(struct.pack(">II", h, at) for h, at in sorted(index))
    return (_QM_MAGIC
            + b"\x42" + struct.pack(">I", len(hashes)) + hashes
            + b"\x69" + struct.pack(">I", len(body)) + bytes(body))


def button_messages() -> dict[tuple[str, str], str]:
    """Every standard button in the app's language, under each theme's
    context, with and without the "&" accelerator."""
    out = {}
    for source, key in BUTTON_KEYS.items():
        text = tr(key)
        for context in _THEMES:
            out[(context, source)] = text
            out[(context, "&" + source)] = text
    return out


def _button_translator(app) -> QTranslator | None:
    """The standard buttons in the app's own words, as a .qm file in the
    cache (PyQt6 loads catalogues from files only), rewritten only when the
    words change."""
    base = (QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.CacheLocation)
        or tempfile.gettempdir())
    path = os.path.join(base, f"qt_buttons_{current_language()}.qm")
    data = build_qm(button_messages())
    try:
        os.makedirs(base, exist_ok=True)
        try:
            with open(path, "rb") as fh:
                current = fh.read()
        except OSError:
            current = b""
        if current != data:
            tmp = f"{path}.{os.getpid()}.part"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
    except OSError as e:
        log.debug("i18n: could not write %s: %s", path, e)
        return None
    translator = QTranslator(app)
    if not translator.load(path):
        log.debug("i18n: could not load %s", path)
        return None
    return translator


_installed: dict[str, QTranslator] = {}


def apply_qt_language(app) -> None:
    """Install (or swap) Qt's translators for the current app language.
    Called at start and again on every language switch."""
    if app is None:
        return
    for name in ("qtbase", "buttons"):
        old = _installed.pop(name, None)
        if old is not None:
            app.removeTranslator(old)
    code = current_language()
    # Widgets made from now on - calendar popups above all - speak it too.
    QLocale.setDefault(app_locale())
    if code == "en":
        return
    qt = QTranslator(app)
    path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if qt.load(f"qtbase_{_QT_CODES.get(code, code)}", path):
        app.installTranslator(qt)
        _installed["qtbase"] = qt
    else:
        log.debug("i18n: no Qt catalogue for %s in %s", code, path)
    buttons = _button_translator(app)
    if buttons is not None:
        app.installTranslator(buttons)
        _installed["buttons"] = buttons
