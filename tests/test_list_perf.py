"""List/scroll performance guards.

The channel-list delegate used to smooth-scale every visible logo on every
paint - the dominant source of scroll lag with thousands of rows. It now
memoises the scaled pixmap in the global QPixmapCache; these tests lock that
in so a future refactor can't silently reintroduce per-frame scaling.
"""
import pytest


def _app():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_scaled_logo_is_memoised():
    app = _app()  # keep a reference so the QApplication isn't collected
    assert app is not None
    from PyQt6.QtGui import QColor, QPixmap, QPixmapCache
    from dopeiptv.ui.channel_list import ChannelDelegate

    QPixmapCache.clear()
    src = QPixmap(120, 120)
    src.fill(QColor("#123456"))
    url = "http://logos.example/ch1.png"

    first = ChannelDelegate._scaled_logo(src, url, 44)
    assert first.width() == 44 and not first.isNull()
    # The exact-size scaled copy is now in the global cache under its key.
    assert QPixmapCache.find(f"chlogo:44:{url}") is not None
    # A second call returns the cached copy rather than rescaling.
    second = ChannelDelegate._scaled_logo(src, url, 44)
    assert second.cacheKey() == first.cacheKey()
    # A different target size is a distinct cache entry.
    other = ChannelDelegate._scaled_logo(src, url, 64)
    assert other.width() == 64
    assert other.cacheKey() != first.cacheKey()


def test_channel_view_uses_smooth_scroll():
    app = _app()  # keep a reference so the QApplication isn't collected
    assert app is not None
    from PyQt6.QtWidgets import QAbstractItemView, QListView
    from dopeiptv.ui.channel_list import ChannelListView

    v = ChannelListView()
    assert v.verticalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerPixel
    # SinglePass (default), NOT Batched: batched layout blanked/jumped the list
    # while posters streamed in mid-scroll.
    assert v.layoutMode() == QListView.LayoutMode.SinglePass
