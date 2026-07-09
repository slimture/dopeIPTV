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
