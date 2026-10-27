"""The TMDB caches in JSON files of their own, written on a thread of their
own. In the settings file, every save had Qt rewrite all of it (7.7 MB with
a full poster cache, 0.25 s) on the GUI thread while the user browsed."""
import json
import threading

from PyQt6.QtCore import QSettings

from dopeiptv.providers.metadata import CacheFiles

BIG = {f"vod:title {i}": {"poster_url": f"https://x/{i}.jpg", "tmdb_id": i}
       for i in range(500)}


def _settings(tmp_path):
    return QSettings(str(tmp_path / "cache.ini"), QSettings.Format.IniFormat)


def test_a_cache_is_saved_beside_the_settings_not_in_them(tmp_path):
    s = _settings(tmp_path)
    files = CacheFiles(s)
    files.save("tmdb_poster_cache_v3", BIG)
    files.flush()
    assert not s.contains("tmdb_poster_cache_v3")
    f = tmp_path / "cache.d" / "tmdb_poster_cache_v3.json"
    assert json.loads(f.read_text(encoding="utf-8")) == BIG
    assert not list((tmp_path / "cache.d").glob("*.part"))
    assert CacheFiles(_settings(tmp_path)).load("tmdb_poster_cache_v3") == BIG


def test_the_file_is_written_off_the_calling_thread(tmp_path):
    files = CacheFiles(_settings(tmp_path))
    where = []
    real = files._write
    files._write = lambda k, d: (where.append(threading.get_ident()),
                                 real(k, d))
    files.save("tmdb_byid_cache_v1", {"vod:1": {"name": "A"}})
    writer = files._writer
    if writer is not None:
        writer.join(timeout=5)
    assert where and where[0] != threading.get_ident()
    assert files.load("tmdb_byid_cache_v1") == {"vod:1": {"name": "A"}}


def test_a_cache_in_the_settings_moves_out_on_first_read(tmp_path):
    s = _settings(tmp_path)
    s.setValue("tmdb_person_cache", json.dumps({"p:1": ["A", "B"]}))
    files = CacheFiles(s)
    assert files.load("tmdb_person_cache") == {"p:1": ["A", "B"]}
    assert not s.contains("tmdb_person_cache")
    files.flush()
    assert CacheFiles(_settings(tmp_path)).load(
        "tmdb_person_cache") == {"p:1": ["A", "B"]}


def test_a_damaged_file_starts_over(tmp_path):
    d = tmp_path / "cache.d"
    d.mkdir()
    (d / "tmdb_poster_cache_v3.json").write_text('{"vod:x": {"poster', "utf-8")
    assert CacheFiles(_settings(tmp_path)).load("tmdb_poster_cache_v3") is None


def test_settings_that_are_no_file_keep_the_cache(tmp_path):
    s = QSettings("dopeiptv-test", "tmdb-cache-native")
    s.clear()
    try:
        files = CacheFiles(s)
        if files.dir is None:           # the platform's own store
            files.save("tmdb_person_id_cache", {"name:x": 5})
            assert json.loads(s.value("tmdb_person_id_cache")) == {"name:x": 5}
    finally:
        s.clear()


def test_reset_all_takes_the_files_and_saves_nothing_after(tmp_path):
    files = CacheFiles(_settings(tmp_path))
    files.save("tmdb_poster_cache_v3", BIG)
    files.flush()
    files.clear()
    assert not (tmp_path / "cache.d").exists()
    files.save("tmdb_poster_cache_v3", BIG)
    files.flush()
    assert not (tmp_path / "cache.d").exists()


def test_the_resolver_keeps_its_caches_in_files(tmp_path):
    from unittest.mock import MagicMock
    from PyQt6.QtCore import QThreadPool
    from dopeiptv.providers.metadata import PosterResolver
    s = _settings(tmp_path)
    s.setValue(PosterResolver.CACHE_MATCHER_VER_KEY,
               PosterResolver.CACHE_MATCHER_VER)
    s.setValue(PosterResolver.CACHE_KEY, json.dumps(
        {"vod:old": {"poster_url": "https://x/old.jpg"}}))
    r = PosterResolver(QThreadPool(), s, MagicMock())
    assert "vod:old" in r._cache                # moved over
    assert not s.contains(PosterResolver.CACHE_KEY)
    r._cache["vod:new"] = {"poster_url": "https://x/new.jpg"}
    r._save()
    r.flush()
    again = PosterResolver(QThreadPool(), _settings(tmp_path), MagicMock())
    assert set(again._cache) >= {"vod:old", "vod:new"}
