"""The guide's rows read from channel names (core/guide_rows.py): a
"channel" that is only a heading in the provider's list, never a real one;
and a name's short form for a cell with room for a logo alone."""
from __future__ import annotations

import pytest

from dopeiptv.core.guide_rows import section_heading, short_name


@pytest.mark.parametrize("name, word", [
    ("-= Sweden =-", "Sweden"),
    ("#### SE ####", "SE"),
    ("--- Sports ---", "Sports"),
    ("===== NEWS =====", "NEWS"),
    ("***** SWEDEN *****", "SWEDEN"),
    ("|| UK SPORTS ||", "UK SPORTS"),
    ("<<< Kids >>>", "Kids"),
    ("»» Filmer ««", "Filmer"),
    ("★ Sweden ★", "Sweden"),
    ("• N1 •", "N1"),
    ("◆◆ Kids & Family ◆◆", "Kids & Family"),
    ("==  VIP  |  SE  ==", "VIP | SE"),
    ("SE ━━━━━━━━", "SE"),
    ("##### SPORT", "SPORT"),
    ("== 🇸🇪 Sverige ==", "🇸🇪 Sverige"),
    ("== قنوات عربية ==", "قنوات عربية"),
    ("--------", ""),
    ("##########", ""),
])
def test_a_heading_is_its_word(name, word):
    assert section_heading(name) == word


@pytest.mark.parametrize("name", [
    # Real channels, with every kind of prefix, suffix and mark lists use.
    "SVT1 HD SE", "SVT1 SE", "TV4 Fakta SE", "N1 Info & Updates SE",
    "FLYING OVER SWEDEN ULTRA HD", "SVT Barn/24 SE", "V Sport Premium SE",
    "SE| SVT1", "|SE| Kanal 5", "SE: TV4", "[SE] SVT2", "(SE) Kanal 9",
    "UK: BBC One", "VIP | SE | SVT1", "#1 Hits", "ESPN+", "Disney+",
    "TV4 - Sport", "- TV4 -", "[ SWEDEN ]", "Sky Sports...", "Sky Sports....",
    "C More First", "## SE ## TV4 HD", "### SE ### SVT1", "VIP ---",
    "Kanal 5 +1", "24/7 Friends", "MTV 80s", "4K| Nature", "DAZN 1 (backup)",
    "== Sweden Sports Movies Kids Docs ==", "== SE ** Sports ==", "",
    None, "--", "AB",
])
def test_a_real_channel_is_never_a_heading(name):
    assert section_heading(name) is None


@pytest.mark.parametrize("name, short", [
    ("SVT1 HD SE", "SVT1"),
    ("TV4 Fakta SE", "TV4"),
    ("Kanal 5 SE", "K5"),
    ("TV 4", "TV4"),
    ("Eurosport 1 SE", "E1"),
    ("Viaplay Sports 1", "VS1"),
    ("N1 Info & Updates SE", "N1"),
    ("FLYING OVER SWEDEN ULTRA HD", "FOS"),
    ("V Sport Premium SE", "VSP"),
    ("C More First", "CMF"),
    ("Discovery Channel", "DC"),
    ("Nickelodeon", "Nick"),
    ("SE| SVT1", "SVT1"),
    ("|SE| Kanal 5", "K5"),
    ("UK: BBC One", "BBC"),
    ("4K| Nature HD", "Natu"),
    ("CNN", "CNN"),
])
def test_short_names(name, short):
    assert short_name(name) == short


def test_a_short_name_is_short_and_never_empty():
    for name in ("HD", "SE", "   ", "", None, "A very long channel name indeed",
                 "ÅÄÖ-kanalen", "日本テレビ", "قناة الجزيرة الإخبارية"):
        s = short_name(name)
        assert len(s) <= 5
        if (name or "").strip():
            assert s.strip()
