"""Version comparison for the About dialog's update check."""

from dopeiptv.core.updates import is_newer, _parse


def test_parse_handles_v_prefix_and_junk():
    assert _parse("0.5.0") == (0, 5, 0)
    assert _parse("v0.6.1") == (0, 6, 1)
    assert _parse("V1.2") == (1, 2)
    assert _parse("0.5.0-beta") == (0, 5, 0)


def test_is_newer():
    assert is_newer("0.6.0", "0.5.0")
    assert is_newer("v0.5.1", "0.5.0")
    assert is_newer("1.0.0", "0.9.9")
    assert not is_newer("0.5.0", "0.5.0")
    assert not is_newer("0.4.4", "0.5.0")
    assert not is_newer("v0.5.0", "0.5.0")
