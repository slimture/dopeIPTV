"""Loading a big guide without the window crawling. The parse runs in a
process of its own (a thread shares the interpreter with the window), the
old guide's index stands in while a new one comes, and a guide in memory
answers while a refresh holds the lock."""
import gzip
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

from dopeiptv.providers import epg
from dopeiptv.providers.epg import (
    XmltvGuide, parse_xmltv, read_index_blob)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _guide(title: str = "Show") -> bytes:
    now = datetime.now(timezone.utc)
    fmt = "%Y%m%d%H%M%S +0000"
    rows = []
    for c in range(3):
        for h in range(-2, 6):
            a = (now + timedelta(hours=h)).strftime(fmt)
            b = (now + timedelta(hours=h + 1)).strftime(fmt)
            rows.append(f'<programme start="{a}" stop="{b}" channel="c{c}">'
                        f'<title>{title} {c}/{h}</title><desc>About {h}.</desc>'
                        '</programme>')
    chans = "".join(f'<channel id="c{c}"><display-name>Chan {c}'
                    '</display-name></channel>' for c in range(3))
    return f"<tv>{chans}{''.join(rows)}</tv>".encode()


def test_the_parse_process_gives_what_a_parse_here_gives():
    data = gzip.compress(_guide())
    r = subprocess.run([sys.executable, "-m", "dopeiptv.providers.epg_index",
                        epg.INDEX_FLAG], input=data, capture_output=True,
                       cwd=ROOT, timeout=120)
    assert r.returncode == 0, r.stderr
    assert read_index_blob(r.stdout) == parse_xmltv(
        data, XmltvGuide.KEEP_PAST_DAYS)


def test_the_frozen_entry_hands_the_flag_to_the_parser():
    # dopeiptv.py is the frozen app's entry: the flag must reach the parser
    # before anything opens a window.
    r = subprocess.run([sys.executable, os.path.join(ROOT, "dopeiptv.py"),
                        epg.INDEX_FLAG], input=_guide(), capture_output=True,
                       cwd=ROOT, timeout=120)
    assert r.returncode == 0, r.stderr
    by_id, by_name = read_index_blob(r.stdout)
    assert sorted(by_id) == ["c0", "c1", "c2"]
    assert by_name["chan1"] == "c1"


def test_a_download_is_parsed_in_a_process_of_its_own(tmp_path, monkeypatch):
    def not_here(self, data):
        raise AssertionError("parsed or packed in the app's own process")
    monkeypatch.setattr(XmltvGuide, "_parse", not_here)
    monkeypatch.setattr(XmltvGuide, "_write_cache", not_here)
    cache = tmp_path / "epg_x.xml"
    data = _guide()
    g = XmltvGuide(client=None, cache_path=str(cache))
    g._download = lambda: data
    assert g.ensure_loaded() is True
    assert g.listings_for({"epg_channel_id": "c1"})
    # The process kept the guide, gzipped, and its index for the next start.
    assert gzip.decompress(cache.read_bytes()) == data
    g2 = XmltvGuide(client=None, cache_path=str(cache))
    assert g2._load_index() is True


def test_a_fresh_guide_on_disk_loads_without_its_xml(tmp_path, monkeypatch):
    cache = tmp_path / "epg_x.xml"
    first = XmltvGuide(client=None, cache_path=str(cache))
    first._download = _guide
    assert first.ensure_loaded() is True

    def not_read(self, max_age=None):
        raise AssertionError("unpacked the XML although the index is good")
    monkeypatch.setattr(XmltvGuide, "_read_cache", not_read)
    g = XmltvGuide(client=None, cache_path=str(cache))
    g._download = lambda: (_ for _ in ()).throw(AssertionError("downloaded"))
    assert g.ensure_loaded() is True
    assert g.listings_for({"epg_channel_id": "c2"})


def test_gentle_gzip_matches_gzip(tmp_path):
    data = os.urandom(300_000) + b"guide " * 200_000
    assert gzip.decompress(epg.gzip_gently(data)) == data
    assert epg.gunzip_gently(gzip.compress(data)) == data
    two = gzip.compress(b"one ") + gzip.compress(b"two")
    assert epg.gunzip_gently(two) == gzip.decompress(two)
    import pytest
    import zlib
    with pytest.raises(zlib.error):
        epg.gunzip_gently(gzip.compress(data)[:-50_000])


def test_a_parse_process_that_fails_leaves_the_parse_here(tmp_path,
                                                          monkeypatch):
    monkeypatch.setattr(epg, "_index_command", lambda: (
        [sys.executable, "-c", "import sys; sys.exit(3)"], dict(os.environ)))
    g = XmltvGuide(client=None, cache_path=str(tmp_path / "epg_x.xml"))
    g._download = _guide
    assert g.ensure_loaded() is True
    assert g.listings_for({"epg_channel_id": "c2"})


def test_a_parse_process_that_cannot_start_leaves_the_parse_here(
        tmp_path, monkeypatch):
    monkeypatch.setattr(epg, "_index_command", lambda: (
        [str(tmp_path / "no-such-program")], dict(os.environ)))
    g = XmltvGuide(client=None, cache_path=str(tmp_path / "epg_x.xml"))
    g._download = _guide
    assert g.ensure_loaded() is True
    assert g.listings_for({"epg_channel_id": "c0"})


def test_a_stale_guide_comes_from_its_index_while_the_new_one_loads(
        tmp_path, monkeypatch):
    cache = tmp_path / "epg_x.xml"
    first = XmltvGuide(client=None, cache_path=str(cache))
    first._download = lambda: _guide("Old")
    first._write_cache(_guide("Old"))
    assert first.ensure_loaded() is True        # writes the index
    old = time.time() - 30 * 86400               # far past the TTL
    os.utime(cache, (old, old))
    os.utime(first._index_path(), (old + 1, old + 1))

    parsed = []
    real = XmltvGuide._parse_and_index

    def counting(self, data, **kw):
        parsed.append(data)
        real(self, data, **kw)
    monkeypatch.setattr(XmltvGuide, "_parse_and_index", counting)
    g = XmltvGuide(client=None, cache_path=str(cache))
    seen_during_download = []

    def download():
        # The old guide is already up while the new one downloads.
        seen_during_download.append(
            g.current_programme({"epg_channel_id": "c1"}))
        return _guide("New")
    g._download = download
    assert g.ensure_loaded() is True
    assert seen_during_download[0]["title"].startswith("Old")
    assert len(parsed) == 1                     # the new guide only
    assert g.current_programme({"epg_channel_id": "c1"})["title"] \
        .startswith("New")


def test_a_refresh_shows_the_guide_on_disk_until_the_new_one_is_in(tmp_path):
    # A playlist refresh (button or auto-refresh) starts a new guide object
    # with force=True: it showed no programmes at all for the half minute
    # the new guide took.
    cache = tmp_path / "epg_x.xml"
    first = XmltvGuide(client=None, cache_path=str(cache))
    first._download = lambda: _guide("Old")
    assert first.ensure_loaded() is True
    g = XmltvGuide(client=None, cache_path=str(cache))
    seen = []

    def download():
        seen.append(g.current_programme({"epg_channel_id": "c0"}))
        return _guide("New")
    g._download = download
    assert g.ensure_loaded(force=True) is True
    assert seen[0]["title"].startswith("Old")
    assert g.current_programme({"epg_channel_id": "c0"})["title"] \
        .startswith("New")


def test_a_loaded_guide_answers_while_a_refresh_holds_the_lock(tmp_path):
    g = XmltvGuide(client=None, cache_path=str(tmp_path / "epg_x.xml"))
    g._download = _guide
    assert g.ensure_loaded() is True
    answers = []
    with g._lock:                     # a refresh downloading and parsing
        t = threading.Thread(target=lambda: answers.append(
            g.listings_for({"epg_channel_id": "c1"})))
        t.start()
        t.join(timeout=5)
        assert not t.is_alive(), "waited for the refresh"
    assert answers and answers[0]


def test_the_frozen_app_starts_itself_as_the_parser(tmp_path, monkeypatch):
    # A bundle is its own interpreter: the parse process is sys.executable
    # with the flag, which dopeiptv.py (the bundle's entry) hands on. The
    # release build's --self-check runs this same probe on every bundle.
    import pytest
    if sys.platform == "win32":
        pytest.skip("a shell script stands in for the frozen executable")
    exe = tmp_path / "dopeiptv"
    exe.write_text(f"#!/bin/sh\nexec '{sys.executable}' "
                   f"'{os.path.join(ROOT, 'dopeiptv.py')}' \"$@\"\n")
    exe.chmod(0o755)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert epg._index_command()[0] == [str(exe), epg.INDEX_FLAG]
    probe = XmltvGuide(None)
    assert probe._parse_elsewhere(_guide()) is True
    assert sorted(probe._by_id) == ["c0", "c1", "c2"]
