"""Unit tests for dopeiptv.core.recording utilities."""

from dopeiptv.core.recording import format_size, safe_filename


def test_safe_filename_strips_unsafe():
    assert "/" not in safe_filename("a/b\\c:d")
    assert "\\" not in safe_filename("a/b\\c:d")


def test_safe_filename_empty():
    assert safe_filename("") == "recording"
    assert safe_filename(None) == "recording"


def test_safe_filename_truncates():
    long = "x" * 200
    assert len(safe_filename(long)) <= 120


def test_format_size_bytes():
    assert "0 B" == format_size(0)
    assert "512 B" == format_size(512)


def test_format_size_kb():
    assert "1 KB" == format_size(1024)


def test_format_size_mb():
    result = format_size(1024 * 1024)
    assert "MB" in result


def test_format_size_gb():
    result = format_size(1024 ** 3)
    assert "GB" in result


def test_total_recordings_cap(tmp_path):
    """The folder-wide size cap counts only recordings and trips at the
    limit so a new recording can be refused."""
    from PyQt6.QtCore import QSettings
    from dopeiptv.core.recording import RecordingManager

    for name, size in (("a.mp4", 1_000_000_000),
                       ("b.mp4", 2_000_000_000),
                       ("note.txt", 5_000_000_000)):
        p = tmp_path / name
        with open(p, "wb") as f:
            f.seek(size - 1)
            f.write(b"\0")

    s = QSettings("dopeIPTV-test", "reccap")
    s.clear()
    s.setValue("recordings_dir", str(tmp_path))
    rm = RecordingManager(s)

    # Only the two .mp4 files count (~3 GB), not the .txt.
    assert 2.9e9 < rm.folder_used_bytes() < 3.1e9
    assert rm.total_cap_bytes() == 0 and not rm.total_cap_exceeded()

    s.setValue("rec_total_value", "5")
    s.setValue("rec_total_unit", "GB")
    assert not rm.total_cap_exceeded()          # 3 GB < 5 GB

    s.setValue("rec_total_value", "2")
    assert rm.total_cap_exceeded()              # 3 GB >= 2 GB

    s.setValue("rec_total_value", "1")
    s.setValue("rec_total_unit", "TB")
    assert rm.total_cap_bytes() == 10**12 and not rm.total_cap_exceeded()
