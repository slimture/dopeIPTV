"""The AppStream metainfo must pass the oldest appstreamcli a release
builds with. The x86_64 AppImage is made on Ubuntu 22.04 (AppStream 0.15),
whose validator fails the build on any warning - and an URL type it does
not know is one: 1.4.4's first build stopped at `vcs-browser`."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

METAINFO = Path(__file__).resolve().parent.parent / "packaging" / \
    "io.github.slimture.dopeIPTV.metainfo.xml"

# The URL types AppStream 0.15 knows; vcs-browser and contribute came later.
OLD_URL_TYPES = {"homepage", "bugtracker", "faq", "help", "donation",
                 "translate", "contact"}


def test_url_types_known_to_old_appstream():
    root = ET.parse(METAINFO).getroot()
    types = [u.get("type") for u in root.iter("url")]
    assert types, "the metainfo names no URLs"
    unknown = sorted({t for t in types if t not in OLD_URL_TYPES})
    assert not unknown, f"URL types Ubuntu 22.04's appstreamcli rejects: {unknown}"


def test_homepage_is_the_website():
    root = ET.parse(METAINFO).getroot()
    homes = [u.text for u in root.iter("url") if u.get("type") == "homepage"]
    assert homes == ["https://dopeiptv.org/"]
