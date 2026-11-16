"""Local files' search through a folder and its subfolders (core/local_search.py,
issue #33): what it finds and where, its bounds, cancel, and what it never
walks into - hidden folders, a NAS's housekeeping, a link that loops."""
from __future__ import annotations

import os

import pytest

from dopeiptv.core.local_search import TreeSearch

EXTS = (".mkv", ".mp4", ".mp3", ".flac")


def _touch(path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return str(path)


def _run(search: TreeSearch) -> TreeSearch:
    while not search.step(5.0):
        pass
    return search


def _names(search: TreeSearch) -> list[str]:
    return [os.path.basename(h.path) for h in search.hits]


def test_a_file_in_a_subfolder_is_found_and_says_where(tmp_path):
    root = tmp_path / "MusicVideos"
    _touch(root / "Top Song.mkv")
    _touch(root / "reggae" / "Bob.Marley-One.Love.mkv")
    _touch(root / "reggae" / "deep" / "er" / "Bob Dylan - Hurricane.mp4")
    _touch(root / "reggae" / "notes.txt")

    s = _run(TreeSearch(str(root), "bob marley", EXTS))
    assert _names(s) == ["Bob.Marley-One.Love.mkv"]
    assert s.hits[0].folder == "reggae" and not s.hits[0].is_dir
    assert s.hits[0].size == 1 and s.hits[0].mtime > 0
    assert s.done and not s.capped and not s.partial

    # The nearest first: the subfolder's match before the deeper one's.
    s = _run(TreeSearch(str(root), "bob", EXTS))
    assert _names(s) == ["Bob.Marley-One.Love.mkv",
                         "Bob Dylan - Hurricane.mp4"]
    assert s.hits[1].folder == os.path.join("reggae", "deep", "er")

    # A match in the folder searched has no subfolder to name.
    s = _run(TreeSearch(str(root), "top", EXTS))
    assert _names(s) == ["Top Song.mkv"] and s.hits[0].folder == ""


def test_words_in_any_order_case_accents_and_punctuation_left_out(tmp_path):
    root = tmp_path / "M"
    _touch(root / "a" / "Beyoncé - Halo.flac")
    _touch(root / "b" / "One.Love_Bob-Marley.mkv")
    assert _names(_run(TreeSearch(str(root), "beyonce", EXTS))) == \
        ["Beyoncé - Halo.flac"]
    assert _names(_run(TreeSearch(str(root), "LOVE bob", EXTS))) == \
        ["One.Love_Bob-Marley.mkv"]
    # The extension is not part of the name matched, nor is anything else.
    assert _run(TreeSearch(str(root), "flac", EXTS)).hits == []
    assert _run(TreeSearch(str(root), "", EXTS)).hits == []


def test_a_matching_folder_comes_back_as_one_to_open(tmp_path):
    root = tmp_path / "M"
    _touch(root / "Reggae Classics" / "track01.mp3")
    s = _run(TreeSearch(str(root), "reggae", EXTS))
    assert [(os.path.basename(h.path), h.is_dir, h.folder)
            for h in s.hits] == [("Reggae Classics", True, "")]


def test_only_media_files_and_their_folder_kept_for_the_queue(tmp_path):
    root = tmp_path / "M"
    album = root / "Artist" / "Album"
    for n in ("02 Second.mp3", "01 First.mp3", "cover.jpg", "03 Third.mp3"):
        _touch(album / n)
    s = _run(TreeSearch(str(root), "second", EXTS))
    assert _names(s) == ["02 Second.mp3"]
    # Its folder's tracks in the order its listing shows them.
    assert s.siblings == {str(album): ["01 First.mp3", "02 Second.mp3",
                                       "03 Third.mp3"]}


def test_hidden_folders_and_files_and_nas_junk_are_left_out(tmp_path):
    root = tmp_path / "M"
    _touch(root / ".hidden" / "Match one.mkv")
    _touch(root / ".Match two.mkv")
    _touch(root / "@eaDir" / "Match three.mkv")
    _touch(root / "ok" / "Match four.mkv")
    s = _run(TreeSearch(str(root), "match", EXTS, skip={"@eadir"}))
    assert _names(s) == ["Match four.mkv"]


def test_the_number_of_matches_is_capped_and_says_so(tmp_path):
    root = tmp_path / "M"
    for i in range(30):
        _touch(root / f"d{i:02d}" / f"Clip {i:02d}.mkv")
    s = _run(TreeSearch(str(root), "clip", EXTS, max_hits=10))
    assert len(s.hits) == 10 and s.capped and s.done
    assert _names(s)[0] == "Clip 00.mkv"


def test_the_walk_stops_at_its_entry_cap_and_depth_and_says_so(tmp_path):
    root = tmp_path / "M"
    for i in range(20):
        _touch(root / f"Clip {i:02d}.mkv")
    s = _run(TreeSearch(str(root), "clip", EXTS, max_entries=5))
    assert len(s.hits) == 5 and s.partial and not s.capped

    deep = tmp_path / "D"
    _touch(deep / "one" / "Clip near.mkv")
    _touch(deep / "one" / "two" / "three" / "Clip far.mkv")
    s = _run(TreeSearch(str(deep), "clip", EXTS, max_depth=2))
    assert _names(s) == ["Clip near.mkv"] and s.partial
    s = _run(TreeSearch(str(deep), "clip", EXTS, max_depth=3))
    assert _names(s) == ["Clip near.mkv", "Clip far.mkv"] and not s.partial


def test_cancel_stops_it_at_once_and_from_any_point(tmp_path):
    root = tmp_path / "M"
    for i in range(5):
        _touch(root / f"d{i}" / f"Clip {i}.mkv")
    s = TreeSearch(str(root), "clip", EXTS)
    s.cancel()
    assert s.step() is True and s.hits == [] and s.cancelled

    s = TreeSearch(str(root), "clip", EXTS)
    assert s.step(0.0) is False         # one folder per slice: the top one
    assert s.step(0.0) is False and len(s.hits) == 1
    s.cancel()
    assert s.step(5.0) is True and len(s.hits) == 1


def test_nothing_is_read_before_the_first_step(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("the disk was touched on the window's thread")

    monkeypatch.setattr(os, "scandir", boom)
    monkeypatch.setattr(os.path, "realpath", boom)
    TreeSearch(str(tmp_path), "x", EXTS)


def test_an_unreadable_folder_is_an_empty_one(tmp_path):
    s = _run(TreeSearch(str(tmp_path / "gone"), "x", EXTS))
    assert s.done and s.hits == []


def _symlink(target, link) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")


def test_a_link_that_loops_is_not_walked_into(tmp_path):
    root = tmp_path / "M"
    _touch(root / "sub" / "Clip a.mkv")
    _symlink(root, root / "sub" / "back-to-top")         # an ancestor
    _symlink(root / "sub", root / "sub" / "itself")      # its own folder
    _symlink(root / "sub", root / "again")               # inside the walk
    s = _run(TreeSearch(str(root), "clip", EXTS))
    assert _names(s) == ["Clip a.mkv"]
    assert s.done and not s.partial


def test_a_link_out_of_the_folder_is_followed_once(tmp_path):
    outside = tmp_path / "Elsewhere"
    _touch(outside / "Clip b.mkv")
    _symlink(outside / ".." / "Elsewhere", outside / "loop")
    root = tmp_path / "M"
    root.mkdir()
    _symlink(outside, root / "linked")
    _symlink(outside, root / "linked twice")
    s = _run(TreeSearch(str(root), "clip", EXTS))
    assert _names(s) == ["Clip b.mkv"]
    assert s.hits[0].folder == "linked"
