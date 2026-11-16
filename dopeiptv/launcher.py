"""The ``dopeiptv`` command an installed package runs (pyproject's script):
the window, or - with ``--server`` - server mode without one, which must
not import a widget (dopeiptv/headless.py). dopeiptv.py does the same for a
checkout and the frozen builds."""
from __future__ import annotations

import sys


def main() -> int:
    if "--server" in sys.argv[1:]:
        from .headless import main as server_main
        return server_main([a for a in sys.argv[1:] if a != "--server"])
    from .app import main as app_main
    return app_main()
