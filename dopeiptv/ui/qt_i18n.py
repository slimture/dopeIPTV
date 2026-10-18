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
"""

from __future__ import annotations

from PyQt6.QtCore import QLibraryInfo, QTranslator

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


class _ButtonTranslator(QTranslator):
    """Answers only for the standard buttons; everything else falls through
    (None) to Qt's catalogue, or to the English source."""

    def translate(self, context, source, disambiguation=None, n=-1):
        # The texts live in the platform theme: QPlatformTheme by default,
        # QGnomeTheme (with "&OK", "&Cancel") under GNOME and GTK.
        if not context or not context.endswith("Theme"):
            return None
        key = BUTTON_KEYS.get((source or "").replace("&", ""))
        if key is None or current_language() == "en":
            return None
        return tr(key)


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
    if code != "en":
        qt = QTranslator(app)
        path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        if qt.load(f"qtbase_{_QT_CODES.get(code, code)}", path):
            app.installTranslator(qt)
            _installed["qtbase"] = qt
        else:
            log.debug("i18n: no Qt catalogue for %s in %s", code, path)
    buttons = _ButtonTranslator(app)
    app.installTranslator(buttons)
    _installed["buttons"] = buttons
