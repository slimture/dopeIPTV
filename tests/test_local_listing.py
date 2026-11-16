"""One folder of Local files read in slices (core/local_listing.py): the
folder's own order (its folders, then its media files, each by name without
regard to case), the slices, cancel, and the names a file plays on into."""
import os

from dopeiptv.core import local_listing
from dopeiptv.core.local_listing import FolderListing

EXTS = (".mkv", ".mp4", ".mp3", ".flac")


def _folder(tmp_path):
    root = tmp_path / "F"
    for d in ("beta", "Alpha", ".hidden", "zeta"):
        (root / d).mkdir(parents=True)
    for f in ("b.mkv", "A.mp3", "c.txt", ".dot.mkv", "Z.flac", "m.MP4"):
        (root / f).write_bytes(b"x" * 3)
    return root


def _all(listing: FolderListing) -> list:
    got = []
    while not listing.step(0.0):
        got += listing.take()
    return got + listing.take()


def _in_order(items) -> list[str]:
    """The listing's rows as the list shows them: folders, then files."""
    return [os.path.basename(i.path) for i in items if i.is_dir] + \
        [os.path.basename(i.path) for i in items if not i.is_dir]


def test_folders_first_then_the_media_files_by_name(tmp_path):
    items = _all(FolderListing(str(_folder(tmp_path)), EXTS))
    assert _in_order(items) == ["Alpha", "beta", "zeta",
                                "A.mp3", "b.mkv", "m.MP4", "Z.flac"]
    f = next(i for i in items if i.path.endswith("b.mkv"))
    assert f.size == 3 and f.mtime > 0 and not f.is_dir


def test_each_group_only_grows_at_its_end(tmp_path, monkeypatch):
    """However the slices fall, and whether a file comes out as it is told
    (its stat in hand: a gvfs share) or once all are told, what is taken is
    in order within the folders and within the files - the list adds rows,
    it never moves them."""
    root = _folder(tmp_path)
    for asked in (0.0, 10.0):          # every telling "asked", or none
        monkeypatch.setattr(FolderListing, "ASKED", asked)
        listing = FolderListing(str(root), EXTS)
        dirs, files = [], []
        while True:
            finished = listing.step(0.0)        # one entry per slice
            for i in listing.take():
                (dirs if i.is_dir else files).append(os.path.basename(i.path))
            if finished:
                break
        assert dirs == ["Alpha", "beta", "zeta"]
        assert files == ["A.mp3", "b.mkv", "m.MP4", "Z.flac"]


def test_a_told_file_with_its_stat_in_hand_comes_out_at_once(tmp_path,
                                                             monkeypatch):
    """On a share that does not say what an entry is, telling it took a
    stat: the file is shown then, not after every entry is told."""
    root = tmp_path / "G"
    root.mkdir()
    for n in range(5):
        (root / f"clip{n}.mkv").write_bytes(b"x")
    (root / "zz").mkdir()
    monkeypatch.setattr(FolderListing, "ASKED", 0.0)
    listing = FolderListing(str(root), EXTS)
    listing.step(0.0)                   # the folder read
    listing.step(0.0)                   # the first entry told
    assert [os.path.basename(i.path) for i in listing.take()] == ["clip0.mkv"]


def test_a_slice_keeps_to_its_budget(tmp_path, monkeypatch):
    root = tmp_path / "H"
    root.mkdir()
    for n in range(200):
        (root / f"{n:03d}.mkv").write_bytes(b"x")
    import time
    from types import SimpleNamespace
    clock = [0.0]
    monkeypatch.setattr(local_listing, "time", SimpleNamespace(
        monotonic=lambda: clock[0], perf_counter=time.perf_counter))
    real = FolderListing._stat

    def slow(self, e):
        clock[0] += 0.01                # each stat a 10 ms round trip
        real(self, e)
    monkeypatch.setattr(FolderListing, "_stat", slow)
    listing = FolderListing(str(root), EXTS)
    slices = 0
    while not listing.step(0.05):
        slices += 1
        assert len(listing.take()) <= 6     # about 50 ms of stats a slice
    assert slices >= 30


def test_media_names_before_and_after_every_entry_is_told(tmp_path):
    root = _folder(tmp_path)
    listing = FolderListing(str(root), EXTS)
    assert listing.media_names() == []          # nothing read yet
    listing.step(0.0)                           # the folder read
    assert not listing.told
    assert listing.media_names() == ["A.mp3", "b.mkv", "m.MP4", "Z.flac"]
    while not listing.told:
        listing.step(0.0)
    assert listing.media_names() == ["A.mp3", "b.mkv", "m.MP4", "Z.flac"]


def test_cancel_stops_at_the_next_entry(tmp_path):
    listing = FolderListing(str(_folder(tmp_path)), EXTS)
    listing.step(0.0)
    listing.cancel()
    assert listing.cancelled and listing.step(1.0) is True


def test_nothing_is_read_before_the_first_step(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("read the disk before step")
    monkeypatch.setattr(os, "scandir", boom)
    FolderListing(str(tmp_path), EXTS)


def test_a_folder_that_is_gone_or_not_one_lists_nothing(tmp_path):
    (tmp_path / "file.mkv").write_bytes(b"x")
    for p in (tmp_path / "gone", tmp_path / "file.mkv"):
        listing = FolderListing(str(p), EXTS)
        assert listing.step(1.0) is True and listing.take() == []


def test_a_link_to_nothing_is_left_out(tmp_path):
    root = tmp_path / "L"
    root.mkdir()
    (root / "real.mkv").write_bytes(b"x")
    try:
        os.symlink(root / "missing.mkv", root / "broken.mkv")
    except (OSError, NotImplementedError):
        return
    items = _all(FolderListing(str(root), EXTS))
    assert [os.path.basename(i.path) for i in items] == ["real.mkv"]
