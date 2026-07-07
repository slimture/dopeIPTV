"""Unit tests for dopeiptv.epg utilities."""

from dopeiptv.epg import normalize_name, parse_xmltv_time


def test_normalize_name():
    assert normalize_name("  CNN HD  ") == "cnnhd"
    assert normalize_name("BBC One") == "bbcone"


def test_normalize_name_empty():
    assert normalize_name("") == ""
    assert normalize_name(None) == ""


def test_parse_xmltv_time_with_offset():
    dt = parse_xmltv_time("20250101120000 +0000")
    assert dt is not None
    assert dt.hour == 12 or dt.utcoffset() is not None


def test_parse_xmltv_time_no_offset():
    dt = parse_xmltv_time("20250615180000")
    assert dt is not None


def test_parse_xmltv_time_invalid():
    assert parse_xmltv_time("not-a-date") is None
    assert parse_xmltv_time("") is None
    assert parse_xmltv_time(None) is None
