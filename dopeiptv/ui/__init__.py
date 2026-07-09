"""UI layer: the main window and its mixins, plus standalone widgets,
dialogs, the channel list, the theme engine and the TMDB match helper.

``MainWindow`` is exposed lazily (PEP 562): importing a leaf module such as
``dopeiptv.ui.theme`` must not drag in the whole window and everything it
depends on (embedded player, providers, ...). That eager pull is also what
would create an import cycle now that ``media`` imports ``ui.theme``.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .main_window import MainWindow

__all__ = ["MainWindow"]


def __getattr__(name: str):
    if name == "MainWindow":
        from .main_window import MainWindow
        return MainWindow
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
