"""The ``dopeiptv`` command an installed package runs (pyproject's script):
the window, or - with ``--server`` - server mode without one, which must
not import a widget (dopeiptv/headless.py). dopeiptv.py does the same for a
checkout and the frozen builds. A start by a dopeiptv:// link reads the
link and nothing after it (core/commands.own_arguments): no link starts
server mode."""
from __future__ import annotations

import sys


def main() -> int:
    from .core.commands import own_arguments
    sys.argv[1:] = own_arguments(sys.argv[1:])
    if "--server" in sys.argv[1:]:
        from .headless import main as server_main
        return server_main([a for a in sys.argv[1:] if a != "--server"])
    from .app import main as app_main
    return app_main()
