"""Unit tests for dopeiptv.providers.client utilities."""

from dopeiptv.providers.client import b64


def test_b64_decodes_valid():
    assert b64("aGVsbG8=") == "hello"


def test_b64_empty():
    assert b64("") == ""
    assert b64(None) == ""


def test_b64_invalid_returns_empty():
    assert b64("!!!") == ""
