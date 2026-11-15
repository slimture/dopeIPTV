"""The phone remote on a phone's home screen: "dopeIPTV Remote" with the
app's icon, in a window of its own - the manifest and icons served under
the token like everything else, the page's head pointing at them."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dopeiptv.core import phone_page  # noqa: E402
from dopeiptv.core.phone_remote import ASSETS, PhoneServer  # noqa: E402

TOKEN = "t" * 40
_APP = None


def test_the_page_points_at_its_manifest_and_icons():
    html, csp = phone_page.render({"bg": "#101010"}, {"bg": "#fafafa"}, {})
    head = html.decode("utf-8").split("</head>", 1)[0]
    for tag in ('<link rel="manifest" href="manifest.webmanifest">',
                '<link rel="apple-touch-icon" href="icon-180.png">',
                '<meta name="apple-mobile-web-app-title" content="dopeIPTV Remote">',
                '<meta name="apple-mobile-web-app-capable" content="yes">'):
        assert tag in head, tag
    assert "manifest-src 'self'" in csp and "img-src 'self'" in csp


def test_the_manifest_stays_under_the_token_and_names_every_icon():
    m = json.loads(phone_page.manifest("#101010"))
    assert m["name"] == "dopeIPTV Remote" and m["display"] == "standalone"
    assert m["start_url"] == "./" and m["scope"] == "./"      # relative: the token stays
    assert m["background_color"] == "#101010"
    assert {i["src"] for i in m["icons"]} <= set(phone_page.ICONS)
    assert any(i["purpose"] == "maskable" for i in m["icons"])
    assert json.loads(phone_page.manifest("javascript:x"))["theme_color"] == "#111111"
    assert set(phone_page.ICONS) | {"manifest.webmanifest"} == ASSETS


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.headers.get("Content-Type"), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type"), e.read()


@pytest.fixture
def server():
    assets = {"manifest.webmanifest": (phone_page.manifest("#101010"),
                                       "application/manifest+json"),
              "icon-180.png": (b"\x89PNG\r\n\x1a\nfake", "image/png")}
    srv = PhoneServer(0, TOKEN, lambda a, p: {}, lambda: (b"<html></html>", "default-src 'none'"),
                      host="127.0.0.1", asset=assets.get)
    srv.start()
    yield f"http://127.0.0.1:{srv.port}"
    srv.stop()


def test_the_server_hands_out_the_manifest_and_icons_under_the_token(server):
    code, ctype, body = _get(f"{server}/{TOKEN}/manifest.webmanifest")
    assert code == 200 and ctype == "application/manifest+json"
    assert json.loads(body)["name"] == "dopeIPTV Remote"
    code, ctype, body = _get(f"{server}/{TOKEN}/icon-180.png")
    assert code == 200 and ctype == "image/png" and body.startswith(b"\x89PNG")
    assert _get(f"{server}/{TOKEN}/icon-512.png")[0] == 404        # not drawn: none
    assert _get(f"{server}/{'x' * 40}/manifest.webmanifest")[0] == 403   # never without it


def test_the_window_draws_square_edge_to_edge_icons():
    from PyQt6.QtGui import QImage
    from PyQt6.QtWidgets import QApplication
    global _APP
    _APP = QApplication.instance() or QApplication([])   # kept alive

    from dopeiptv.ui.mw_phone import _home_screen_assets
    assets = _home_screen_assets()
    assert set(assets) == ASSETS
    for name, size in phone_page.ICONS.items():
        data, ctype = assets[name]
        img = QImage.fromData(data, "PNG")
        assert ctype == "image/png" and img.width() == img.height() == size, name
        # Full bleed: no transparent corner for iOS to fill with black.
        assert img.pixelColor(0, 0).alpha() == 255, name
