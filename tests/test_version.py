"""Guard against the recurring release bug: the app version in
dopeiptv/__init__.py and the packaging version in pyproject.toml MUST match,
or the built sdist/wheel gets named after the wrong version (e.g. a 0.7.1
release shipping a dopeiptv-0.7.0.tar.gz). This test fails the moment they
drift, so it's caught before tagging a release, not after.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import dopeiptv

_ROOT = Path(__file__).resolve().parent.parent


def test_pyproject_version_matches_package() -> None:
    with (_ROOT / "pyproject.toml").open("rb") as fh:
        pyproject_version = tomllib.load(fh)["project"]["version"]
    assert pyproject_version == dopeiptv.__version__, (
        f"Version drift: pyproject.toml says {pyproject_version!r} but "
        f"dopeiptv.__version__ is {dopeiptv.__version__!r}. Bump BOTH."
    )


def test_appstream_metainfo_names_this_release_first() -> None:
    """Software catalogs (AppImageHub, GNOME Software, Flathub) read the
    version from the AppStream file's newest <release>. It went unmaintained
    from 0.7.2 on, so 1.2.x would have been listed as 0.7.2."""
    import xml.etree.ElementTree as ET

    root = ET.parse(
        _ROOT / "packaging" / "io.github.slimture.dopeIPTV.metainfo.xml"
    ).getroot()
    releases = root.findall("./releases/release")
    assert releases, "metainfo has no <release> entries"
    newest = releases[0].get("version")
    assert newest == dopeiptv.__version__, (
        f"packaging/io.github.slimture.dopeIPTV.metainfo.xml lists "
        f"{newest!r} first, but this is {dopeiptv.__version__!r}. Add a "
        f"<release> for it at the top of <releases>."
    )
    dates = [r.get("date") or "" for r in releases]
    assert dates == sorted(dates, reverse=True), "releases must be newest first"
