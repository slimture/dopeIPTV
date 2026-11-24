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
    assert requests.RequestException is real.RequestException and requests.auth is real.auth
    # Its calls and sessions are the stand-in's own, on requests': what they
    # read whole has a ceiling (tests/test_response_cap.py).
    assert requests.get is not real.get and issubclass(requests.Session, real.Session)


def test_a_source_s_client_makes_its_session_on_first_use():
    # app.py makes the source's client before the window: its requests
    # session - and requests with it - waits for the first request
    # (docs/PERFORMANCE.md), and is then made as make_client said.
    code = (
        "import os, sys; os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen');"
        "from dopeiptv.providers.client import make_client;"
        "from dopeiptv.core._lazy_requests import LIST_MAX;"
        "c = make_client({'id': 'a', 'kind': 'xtream', 'server': 'http://192.0.2.1',"
        " 'username': 'uu1', 'password': 'pp1'});"
        "before = 'requests' in sys.modules;"
        "s = c.session;"
        "print('BEFORE', before, 'AFTER', 'requests' in sys.modules,"
        " s.headers['User-Agent'], s.max_response == LIST_MAX, c.session is s)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         timeout=120)
    assert "BEFORE False AFTER True dopeIPTV/1.0 True True" in out.stdout, (
        out.stdout, out.stderr[-1500:])


def test_what_is_asked_of_a_session_is_put_on_it_in_order():
    from dopeiptv.providers.client import XtreamClient
    c = XtreamClient("http://192.0.2.1", "uu1", "pp1")
    seen = []
    c.on_session(lambda s: seen.append(("first", s.headers["User-Agent"])))
    c.on_session(lambda s: s.headers.__setitem__("X-Test", "1"))
    assert seen == [] and "_session" not in vars(c)
    s = c.session
    assert seen == [("first", "dopeIPTV/1.0")] and s.headers["X-Test"] == "1"
    c.on_session(lambda s2: seen.append(("later", s2 is s)))      # made: at once
    assert seen[-1] == ("later", True) and c.session is s


def test_a_setup_that_failed_is_tried_again_never_left_out():
    import pytest

    from dopeiptv.providers.client import XtreamClient
    c = XtreamClient("http://192.0.2.1", "uu1", "pp1")
    fail = [True]

    def picky(s):
        if fail[0]:
            raise RuntimeError("not now")
        s.headers["X-Route"] = "on"
    c.on_session(picky)
    with pytest.raises(RuntimeError):
        _ = c.session
    fail[0] = False
    assert c.session.headers["X-Route"] == "on"
    assert c.session.headers["User-Agent"] == "dopeIPTV/1.0"


def test_a_session_given_is_the_session():
    from dopeiptv.providers.client import OfflineClient
    c = OfflineClient()
    given = object()
    c.session = given
    assert c.session is given
