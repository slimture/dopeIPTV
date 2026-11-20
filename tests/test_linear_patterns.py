"""The patterns that read a provider's text take linear time on hostile
text of any length - an M3U line, a film's or a channel's name, a portal's
archive answer, a guide or list field - and match exactly what they always
matched: each is compared with the pattern it replaced over real lines and
thousands of made-up ones. Python's re holds the interpreter while it
runs, so one quadratic match stopped the whole window."""
from __future__ import annotations

import random
import re
import time

import pytest

from dopeiptv.providers import epg, satip, stalker, vdr
from dopeiptv.providers.client import M3UClient
from dopeiptv.providers.metadata import PosterResolver
from dopeiptv.services import failover, popular

# -- what they were ------------------------------------------------------------------

OLD_EXTINF = re.compile(r'#EXTINF:\s*-?[\d.]*(?P<attrs>(?:[^,"]|"[^"]*")*),(?P<name>.*)')
OLD_LOOSE = re.compile(r'#EXTINF:-?\d*\s*(?P<attrs>[^,]*),(?P<name>.*)')
OLD_ATTR = re.compile(r'([\w-]+)="([^"]*)"')
OLD_MAG = re.compile(r"(?:\s+[a-z_]+:(?!//)\S*)+\s*$", re.I)
OLD_PREFIX = re.compile(r"^\s*\|?\s*[a-z]{2,3}\s*[:|]\s*")
OLD_BRACKETED = re.compile(r"[\[({][^\])}]*[\])}]")
OLD_YEAR_IN_BRACKETS = re.compile(r"[\[(]\s*((?:19|20)\d{2})\s*[\])]")


def old_split_guide_urls(text):
    t = (text or "").strip()
    if not t:
        return []
    return [u.strip() for u in re.split(r"[\s,;|]+(?=https?://)", t) if u.strip()]


def old_split_lists(text):
    t = (text or "").strip()
    if not t:
        return []
    pat = re.compile(r"[\s,;|]+(?=(?:https?://|file:))", re.I)
    return [p.strip() for line in t.splitlines() for p in pat.split(line) if p.strip()]


def old_clean_title(raw):
    t = raw or ""
    year = None
    year_m = re.search(r"\b(19|20)\d{2}\b", t)
    if year_m:
        year = int(year_m.group(0))
    t = re.sub(r"^\s*[A-Za-z]{2,3}\s*[|\-:]\s*", "", t)
    t = re.sub(r"[\[({][^\])}]*[\])}]", " ", t)
    t = re.sub(r"[\[\](){}]", " ", t)
    t = re.sub(
        r"\b(1080p|720p|2160p|4K|UHD|HDR|WEB[-.]?DL|WEB[-.]?RIP|"
        r"BR[-.]?RIP|BluRay|BDR[iI][pP]|x265|x264|HEVC|H\.?264|"
        r"H\.?265|AAC|DTS|AC3|MULTI|VOSTFR|VOST|VF|SUB|DUAL|"
        r"REMUX|EXTENDED|UNRATED|DIRECTORS?[ .]CUT)\b.*$", "",
        t, flags=re.IGNORECASE)
    t = re.sub(r"\s*\(?\b(19|20)\d{2}\b\)?\s*", " ", t)
    t = t.strip(" -_.|:;/[](){}\t\n")
    t = re.sub(r"\s+", " ", t)
    return t, year


def old_name_key(name):
    n = (name or "").casefold()
    n = OLD_PREFIX.sub("", n)
    n = failover._QUALITY.sub(" ", n)
    return re.sub(r"[^0-9a-zÀ-￿]", "", n)


def _made_up(alphabet: list[str], count: int = 3000, longest: int = 40, seed: int = 7) -> list[str]:
    rnd = random.Random(seed)
    return ["".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, longest))) for _ in range(count)]


def _groups(m):
    return None if m is None else (m.group(0), m.groupdict())


M3U_LINES = [
    '#EXTINF:-1 tvg-id="svt1.se" tvg-name="SVT1" tvg-logo="http://l/svt1.png" group-title="Sweden",SVT 1 HD',
    '#EXTINF:-1 group-title="News, Sports" catchup="shift" catchup-days="7",CNN',
    '#EXTINF:0,Das Erste', "#EXTINF:-1,", '#EXTINF:-1 tvg-name="a,b,No quote end',
    '#EXTINF: -1 tvg-chno="12",  Name, with comma', "#EXTINF:3.5 x=1,y", '#EXTINF:-1 a="1"b="2",N',
    '#EXTM3U url-tvg="http://e/g.xml.gz,http://e/h.xml" x-tvg-url="http://e/x.xml"',
    '#EXTINF:-1 tvg-logo=""  group-title="",', '#EXTINF:abc,def',
]
M3U_ALPHABET = list('#EXTINF:') + ['-', '1', '.', ' ', ',', '"', '=', 'a', 'tvg-id', 'x', '\t', '_']


def test_m3u_lines_match_as_before():
    lines = M3U_LINES + ["#EXTINF:" + s for s in _made_up(M3U_ALPHABET)]
    for pat_new, pat_old in ((M3UClient._EXTINF, OLD_EXTINF), (vdr._EXTINF, OLD_EXTINF),
                             (satip._EXTINF, OLD_EXTINF), (M3UClient._EXTINF_LOOSE, OLD_LOOSE)):
        for line in lines:
            assert _groups(pat_new.match(line)) == _groups(pat_old.match(line)), line
    words = _made_up(['a', 'b', '-', '_', '=', '"', ' ', ',', 'é', '1'], longest=30)
    for text in M3U_LINES + words:
        for pat in (M3UClient._ATTR, vdr._ATTR, satip._ATTR):
            assert pat.findall(text) == OLD_ATTR.findall(text), text


def test_list_and_guide_fields_split_as_before():
    fields = ["http://a/g.xml http://b/h.xml", "http://a/x?a=1,2;http://b/y", "http://a\n,| https://b",
              "file:///x y.m3u, http://h/l.m3u", " , http://a", "nothing here"]
    fields += _made_up(['http://', 'https://', 'file:', 'h', ' ', ',', ';', '|', '\n', 'x'], longest=12)
    for f in fields:
        assert epg.split_guide_urls(f) == old_split_guide_urls(f), f
        assert satip.split_lists(f) == old_split_lists(f), f


TITLES = ["EN| The Matrix (1999) [MULTI] 1080p", "SV - Amélie [AR-ENG] [", "Wonder Woman 1984 (2020)",
          "FR: Le Dîner de cons {x} (1998) x265 HEVC", "Dune 2021 2160p WEB-DL", "  Up  ", "",
          "Blade Runner (Director's Cut) 1982", "4K-Avatar (2009) [", "Spider-Man: No Way Home 2021"]


def test_film_and_channel_names_clean_as_before():
    made = _made_up(['[', ']', '(', ')', '{', '}', ' ', 'a', 'B', '1999', '2020', 'x', '-', '|',
                     ':', 'EN', '1080p', 'SE'], longest=20)
    for t in TITLES + made:
        assert PosterResolver.clean_title(t) == old_clean_title(t), t
        assert failover.name_key(t) == old_name_key(t), t
        old = OLD_BRACKETED.sub(" ", t)
        assert popular.without_brackets(t) == old, t
        a, b = OLD_YEAR_IN_BRACKETS.search(t), popular._YEAR_IN_BRACKETS.search(t)
        assert (a and a.group(0)) == (b and b.group(0)), t
    for t in ("|UK| BBC One", "SE: SVT1", " se | tv4 hd", "SVT-1", "  |  dk:  dr1 fhd"):
        assert failover.name_key(t) == old_name_key(t), t


def test_portal_archive_options_come_off_as_before():
    cmds = ["http://h/a.ts position:0 media_len:3600", "http://h/a.ts", "auto http://h/x",
            "http://h/x position:0  ", " position:0", "position:0", "http://h/x a:1 b c:2",
            "http://h/x http://y", "http://h/x A_B:x:y", "http://h/x\tq:1\n"]
    cmds += _made_up(['a', '_', ':', '/', '//', ' ', '\t', 'http://h', 'x', 'Q'], longest=16)
    for c in cmds:
        assert stalker._MAG_OPTIONS.sub("", c) == OLD_MAG.sub("", c), repr(c)


HOSTILE = 200_000


@pytest.mark.parametrize("what, call", [
    ("an M3U line of digits with no comma",
     lambda: M3UClient("x")._parse("#EXTM3U\n#EXTINF:" + "1" * HOSTILE + "\nhttp://x/1\n")),
    ("an M3U line of spaces", lambda: M3UClient("x")._parse("#EXTINF:" + " " * HOSTILE + "\nhttp://x/1\n")),
    ("an M3U header of one long word", lambda: M3UClient("x")._parse("#EXTM3U " + "a" * HOSTILE + "\n")),
    ("streamdev's list", lambda: vdr.parse_m3u("#EXTINF:" + "1" * HOSTILE + "\nhttp://x/1\n")),
    ("a SAT>IP list", lambda: satip.parse_m3u("#EXTINF:" + "1" * HOSTILE + "\nrtsp://x/1\n")),
    ("a film of opening brackets", lambda: PosterResolver.clean_title("[" * HOSTILE)),
    ("a film of spaces", lambda: PosterResolver.clean_title("a" + " " * HOSTILE + "x")),
    ("a popular title of brackets", lambda: popular.name_keys("(" * HOSTILE)),
    ("a channel of spaces", lambda: failover.name_key(" " * HOSTILE + "x")),
    ("a portal's archive cmd", lambda: stalker.archive_address("auto http://h/x" + " " * HOSTILE + "!")),
    ("a portal's cmd of options", lambda: stalker.archive_address("http://h/x" + " a:1" * HOSTILE + " !")),
    ("a guide field", lambda: epg.split_guide_urls("x" + " ," * HOSTILE + "y")),
    ("a SAT>IP list field", lambda: satip.split_lists("x" + " ;" * HOSTILE + "y")),
])
def test_hostile_text_takes_linear_time(what, call):
    start = time.perf_counter()
    call()
    assert time.perf_counter() - start < 3.0, what
