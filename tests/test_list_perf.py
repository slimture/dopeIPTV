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


def test_grid_reflow_is_debounced_during_resize_storms():
    """A splitter drag resizes the view per mouse move; the full grid
    relayout must coalesce (leading + trailing) instead of running per
    pixel - that storm starved the video render loop while dragging."""
    app = _app()
    assert app is not None
    from PyQt6.QtCore import QSize
    from dopeiptv.ui.channel_list import ChannelListView

    v = ChannelListView()
    calls = []
    v._justify_grid = lambda: calls.append(1)  # count reflows

    from PyQt6.QtGui import QResizeEvent
    def storm(n, w0):
        for i in range(n):
            v.resize(w0 + i, 400)
            v.resizeEvent(QResizeEvent(QSize(w0 + i, 400),
                                       QSize(w0 + i - 1, 400)))

    storm(30, 500)
    # Leading edge only - the 29 followers are pending, not executed.
    assert len(calls) == 1
    # After the window elapses the trailing pass lands exactly once.
    import time
    deadline = time.monotonic() + 1.0
    while len(calls) < 2 and time.monotonic() < deadline:
        app.processEvents()
    assert len(calls) == 2


def test_a_list_without_headers_is_laid_out_as_uniform_rows():
    """80,000 films: one sizeHint call instead of one per row (0.65 s ->
    0.1 s on the GUI thread). Section headers keep their own height."""
    app = _app()
    from PyQt6.QtCore import QSize, Qt
    from PyQt6.QtWidgets import QStyledItemDelegate
    from dopeiptv.ui.channel_list import ChannelListModel, ChannelListView

    class Delegate(QStyledItemDelegate):
        calls = 0

        def sizeHint(self, option, index):
            Delegate.calls += 1
            it = index.data(Qt.ItemDataRole.UserRole)
            return QSize(0, 30 if it and it.get("_header") else 56)

    v = ChannelListView()
    m = ChannelListModel()
    v.setModel(m)
    v.setItemDelegate(Delegate())
    v.resize(400, 600)
    rows = [{"stream_id": i, "name": f"Film {i}"} for i in range(5000)]
    m.set_items(list(rows), "vod")
    v.doItemsLayout()
    app.processEvents()
    assert v.uniformItemSizes()
    assert Delegate.calls < 50
    m.set_items([{"_header": "Movies"}] + rows[:20], "vod")
    v.doItemsLayout()
    app.processEvents()
    assert not v.uniformItemSizes()
    assert v.visualRect(m.index(0)).height() == 30
    assert v.visualRect(m.index(1)).height() == 56
