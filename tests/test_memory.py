"""Memory-growth locks (Linux only - reads VmRSS from /proc).

The app routinely churns through big provider lists (an "All" category is
easily 5000+ rows). These tests re-load such a list many times over the same
model/view and assert the process doesn't keep the old generations alive -
the failure mode is a stray reference (delegate cache, lambda closure,
signal connection) that turns every category switch into a permanent +10 MB.
"""
import gc
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="VmRSS sampling needs /proc (Linux)")


def _rss_kb() -> int:
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    raise RuntimeError("VmRSS not found in /proc/self/status")


def _synthetic_items(n: int, generation: int) -> list:
    # Unique, non-interned payloads per generation (~2 KB each), so a leaked
    # generation is heavy enough to show up unmistakably in RSS.
    pad = f"g{generation}-" + "x" * 2000
    return [{"name": f"Channel {generation}-{i}",
             "stream_id": generation * 1_000_000 + i,
             "num": i,
             "stream_icon": f"http://example.invalid/logo/{generation}/{i}.png",
             "epg_channel_id": f"chan{i}.example",
             "_pad": pad + str(i)}
            for i in range(n)]


def test_repeated_channel_list_loads_do_not_accumulate():
    """12 generations of a 5000-row list through the real model + view: RSS
    after the run must sit near the post-warmup baseline. A model/view that
    kept each generation alive would add ~10 MB per pass (~90 MB total)."""
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    from dopeiptv.ui.channel_list import ChannelListModel, ChannelListView

    app = QApplication.instance() or QApplication([])
    view = ChannelListView()
    model = ChannelListModel()
    view.setModel(model)
    view.resize(600, 800)
    view.show()

    baseline = None
    for gen in range(12):
        model.set_items(_synthetic_items(5000, gen), "live")
        app.processEvents()
        model.set_items([], "live")
        app.processEvents()
        gc.collect()
        if gen == 2:            # warmup done: caches/pools are primed
            baseline = _rss_kb()
    # Tear the widgets down for real (deleteLater + event flush) - a shown
    # top-level view left for the GC to reap poisons later Qt tests.
    view.hide()
    view.setModel(None)
    view.deleteLater()
    model.deleteLater()
    app.processEvents()
    app.processEvents()
    gc.collect()

    growth_kb = _rss_kb() - baseline
    # Headroom for allocator noise and Qt's own pools; a real per-generation
    # leak lands far above this.
    assert growth_kb < 60_000, (
        f"RSS grew {growth_kb / 1024:.1f} MB across repeated list loads "
        f"- something is holding old list generations alive")


def test_store_churn_does_not_accumulate():
    """Hammer the history store (bounded by design) with thousands of adds:
    its JSON snapshot and entry list must stay capped, not grow with use."""
    from dopeiptv.core.stores import HistoryStore

    class _S:
        def __init__(self):
            self.data = {}

        def value(self, key, default=None):
            return self.data.get(key, default)

        def setValue(self, key, value):
            self.data[key] = value

        def sync(self):
            pass

    s = _S()
    h = HistoryStore(s, "history")
    gc.collect()
    before = _rss_kb()
    for i in range(5000):
        h.add(f"http://x/live/u/p/{i}.ts", f"Chan {i}", None, i, "live")
    gc.collect()
    growth_kb = _rss_kb() - before
    assert len(h.entries) <= HistoryStore.MAX_ENTRIES, \
        "history store is no longer bounded"
    assert growth_kb < 40_000, (
        f"RSS grew {growth_kb / 1024:.1f} MB from store churn")


def test_dead_url_map_is_bounded():
    """Tombstones for broken image URLs are only removed when the same URL
    is re-requested - a long browse over providers with many dead links
    grew the map without bound. It must sweep and cap itself."""
    from PyQt6.QtCore import QThreadPool
    from dopeiptv.core.workers import LogoLoader

    ld = LogoLoader(QThreadPool(), disk_budget=0)
    # Twice the cap of long-TTL entries: the sweep can't expire them, so
    # the hard cap must kick in.
    for i in range(LogoLoader.DEAD_CAP * 2):
        ld._mark_dead(f"http://img.example/{i}.png", ttl=3600.0)
    assert len(ld.dead) <= LogoLoader.DEAD_CAP
    # Expired entries are dropped in preference to live ones.
    ld.dead.clear()
    for i in range(LogoLoader.DEAD_CAP):
        ld._mark_dead(f"http://old.example/{i}.png", ttl=-1.0)  # expired
    ld._mark_dead("http://fresh.example/x.png", ttl=3600.0)
    assert "http://fresh.example/x.png" in ld.dead
    assert len(ld.dead) == 1


def test_disk_cache_prunes_oldest_back_under_budget(tmp_path):
    """The on-disk image cache had no automatic bound - it must evict the
    oldest files once over budget, keeping the newest."""
    import os
    from PyQt6.QtCore import QThreadPool
    from dopeiptv.core.workers import LogoLoader

    ld = LogoLoader(QThreadPool(), cache_dir=tmp_path, disk_budget=0)
    sub = tmp_path / "ab"
    sub.mkdir()
    for i in range(10):
        f = sub / f"img{i}"
        f.write_bytes(b"x" * 1000)
        os.utime(f, (1000 + i, 1000 + i))   # img0 oldest ... img9 newest

    ld.disk_budget = 5000                    # 10kB on disk, 5kB budget
    freed = ld._prune_disk_cache()
    assert freed >= 5000
    remaining = sorted(p.name for p in sub.iterdir())
    assert "img9" in remaining and "img0" not in remaining
    total = sum(p.stat().st_size for p in sub.iterdir())
    assert total <= 5000
    # Under budget: a second prune is a no-op.
    assert ld._prune_disk_cache() == 0
