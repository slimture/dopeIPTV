"""A lazy stand-in for the ``requests`` module.

Importing ``requests`` pulls in urllib3 + charset_normalizer + certifi - about
110 ms of the app's cold start - yet every use in this codebase is inside a
function, on the first actual network call (which happens after the window is
already up, often on a worker thread). So instead of ``import requests`` at
module top - which drags all of that onto the startup import chain - modules do
``from ..core._lazy_requests import requests`` and keep their ``requests.get``
/ ``requests.Session`` / ``except requests.RequestException`` call sites exactly
as before. The real module is imported once, on first attribute access.

Type checkers see the real ``requests`` module (the TYPE_CHECKING branch), so
static analysis of the call sites is unchanged.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import requests as requests  # noqa: F401  (re-exported for type checkers)
else:
    class _LazyRequests:
        _mod = None

        def __getattr__(self, name):
            mod = _LazyRequests._mod
            if mod is None:
                import requests as mod
                _LazyRequests._mod = mod
            return getattr(mod, name)

    #: Import-free proxy; the first attribute access loads the real module.
    requests = _LazyRequests()
