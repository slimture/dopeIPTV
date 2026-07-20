"""Keep heavy third-party imports off the startup path.

`requests` (with urllib3 + charset_normalizer + certifi) is ~110-150 ms to
import, yet every use is inside a function on the first network call - which
happens after the window is up, often on a worker thread. It must NOT be
pulled onto the import chain of the main window (that cost belongs after
startup, not before the first frame). This test fails loudly if any module
reintroduces a top-level `import requests` on that chain.
"""
import subprocess
import sys


def test_requests_is_not_imported_at_startup():
    # Fresh interpreter: import the whole main-window chain, then check that
    # `requests` is absent from sys.modules. A child process keeps it isolated
    # from whatever the test session already imported.
    code = (
        "import os; os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen');"
        "import sys, dopeiptv.ui.main_window;"
        "print('LOADED' if 'requests' in sys.modules else 'DEFERRED')"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert "DEFERRED" in out.stdout, (
        "requests got imported at startup again - a module on the main-window "
        f"chain has a top-level `import requests`.\nstdout={out.stdout!r}\n"
        f"stderr={out.stderr[-500:]!r}")


def test_lazy_requests_proxy_resolves_the_real_module():
    from dopeiptv.core._lazy_requests import requests
    # Attribute access transparently forwards to the real requests module.
    assert requests.Session is not None
    assert issubclass(requests.RequestException, Exception)
    import requests as real
    assert requests.get is real.get
