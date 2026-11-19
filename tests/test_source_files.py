"""A source's id names files in the cache folder and nowhere else
(core/source_files.py): an id from a backup, sync or a playlist file such
as "a/../../elsewhere" stays a file name in the cache, while every id the
app makes names the very file it always named."""
from __future__ import annotations

import os
import secrets
import uuid

from dopeiptv.core import m3u_ids
from dopeiptv.core.source_files import file_part
from dopeiptv.providers import epg
from dopeiptv.providers.client import XtreamClient, _list_cache_path

HOSTILE = ("a/../../outside/escaped", "..", "../x", "..\\..\\x", "C:\\Windows\\x", "/etc/x",
           "\\\\host\\share\\x", "a\x00b", "x" * 65, "ä", "a b", "CON:", "")


def test_the_app_s_own_ids_name_the_files_they_always_named(monkeypatch, tmp_path):
    monkeypatch.setattr(epg, "_epg_cache_dir", lambda: tmp_path)
    for pid in ("default", uuid.uuid4().hex[:8], secrets.token_hex(4), "5"):
        assert file_part(pid) == pid
        assert epg.epg_cache_path(pid) == str(tmp_path / f"epg_{pid}.xml")
        assert _list_cache_path({"id": pid}) == str(tmp_path / f"lists_{pid}.json")
        assert m3u_ids.map_path(str(tmp_path), pid) == os.path.join(str(tmp_path), f"m3u_ids_{pid}.json")
    assert epg.epg_cache_path(None) == str(tmp_path / "epg_default.xml")
    assert file_part(5) == "5"


def test_an_id_from_outside_stays_a_name_in_the_cache_folder(monkeypatch, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (tmp_path / "outside").mkdir()
    monkeypatch.setattr(epg, "_epg_cache_dir", lambda: cache)
    names = set()
    for pid in HOSTILE:
        part = file_part(pid)
        assert part.startswith("~") and len(part) == 25 and part == file_part(pid), pid
        names.add(part)
        for path in (epg.epg_cache_path(pid) if pid else epg.epg_cache_path("~"),
                     _list_cache_path({"id": pid}) or str(cache / "x"),
                     m3u_ids.map_path(str(cache), pid)):
            assert os.path.dirname(os.path.normpath(path)) == str(cache), (pid, path)
    assert len(names) == len(HOSTILE)
    # The provider's bytes, written where the list cache keeps a list.
    client = XtreamClient("http://127.0.0.1:0", "u", "p",
                          cache_path=_list_cache_path({"id": "a/../../outside/escaped"}))
    client._save_list(("get_live_streams", None), 0.0, b"PROVIDER-BYTES")
    assert not any((tmp_path / "outside").rglob("*"))
    assert any(cache.rglob("*.json"))


def test_a_kept_source_s_guide_is_not_pruned_whatever_its_id(monkeypatch, tmp_path):
    monkeypatch.setattr(epg, "_epg_cache_dir", lambda: tmp_path)
    odd, gone = "my/list", "ab12cd34"
    for pid in (odd, gone, "default"):
        open(epg.epg_cache_path(pid), "wb").close()
    assert epg.prune_epg_caches([odd]) == 1
    assert os.path.exists(epg.epg_cache_path(odd)) and not os.path.exists(epg.epg_cache_path(gone))
