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


def test_info_sidecar_keeps_icon(tmp_path):
    """The channel logo written into a recording's sidecar survives and comes
    back through files(), so the Recordings list shows the same badge as live
    TV. Editing the title must not wipe the icon; clearing it removes it."""
    from PyQt6.QtCore import QSettings
    from dopeiptv.core.recording import RecordingManager

    vid = tmp_path / "Show.mp4"
    with open(vid, "wb") as f:
        f.write(b"\0" * 16)

    s = QSettings("dopeIPTV-test", "recicon")
    s.clear()
    s.setValue("recordings_dir", str(tmp_path))
    rm = RecordingManager(s)

    logo = "http://logos/chan.png"
    rm.write_info(str(vid), "", "", icon=logo)
    row = next(r for r in rm.files(None) if r["_path"] == str(vid))
    assert row["stream_icon"] == logo

    # Editing the title (icon=None) keeps the logo.
    rm.write_info(str(vid), "My Title", "", icon=None)
    row = next(r for r in rm.files(None) if r["_path"] == str(vid))
    assert row["name"] == "My Title" and row["stream_icon"] == logo

    # Explicitly clearing everything drops the sidecar.
    rm.write_info(str(vid), "", "", icon="")
    assert not (tmp_path / "Show.mp4.info.json").exists()


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
