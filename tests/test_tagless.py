"""HTML a provider copied into its guide shown as the words it says."""

from __future__ import annotations

import base64
import time

import pytest

from dopeiptv.core.tagless import plain
from dopeiptv.providers.client import b64
from dopeiptv.providers.epg import parse_xmltv

#: A real one (Premier League, November 2026): a web page's time widget.
WIDGET = ('Matcher från engelska Premier League. Matchen börjar: '
          '<span class="times-rw nd" data-start="1795356000">15:00.')


@pytest.mark.parametrize("raw, shown", [
    (WIDGET, "Matcher från engelska Premier League. Matchen börjar: 15:00."),
    ('Start: <span class="x">15:00</span>.', "Start: 15:00."),
    ("<SPAN>Big</SPAN> <b>bold</b> <i>it</i>", "Big bold it"),
    ("One<br>Two<br/>Three", "One\nTwo\nThree"),
    ("<p>First</p><p>Second</p>", "First\n\nSecond"),
    ("<!-- note -->Text", "Text"),
    ('<a href="https://example.com">Link</a>', "Link"),
    ("&lt;b&gt;bold&lt;/b&gt; text", "bold text"),
    ("AT&amp;T &#8211; news &#x2014; more", "AT&T – news — more"),
])
def test_html_is_left_out(raw, shown):
    assert plain(raw) == shown


@pytest.mark.parametrize("raw", [
    "Tom & Jerry", "Barnes&noble", "R&D", "<Untitled>", "a < b > c",
    "<bold>no tag</bold>", "5 < 7 and 9 > 3", "Plain words", "",
])
def test_what_is_no_html_stays_as_written(raw):
    assert plain(raw) == raw


def test_text_without_markup_is_the_same_object():
    s = "Nothing here"
    assert plain(s) is s


def test_an_entity_never_becomes_a_control_character():
    assert plain("a&#27;[31mb&#7;c&#127;d") == "a[31mbcd"


def test_entities_alone_are_decoded_but_not_reflowed():
    assert plain("5 &lt; 7  and   more") == "5 < 7  and   more"


@pytest.mark.parametrize("hostile", [
    "<a" * 200_000, "<span " + "x" * 400_000, "&" * 400_000,
    "<" * 400_000, "< " * 200_000 + ">", "</" * 200_000,
    "<!--" * 100_000, "&#" * 200_000,
])
def test_no_text_makes_it_run_away(hostile):
    t = time.perf_counter()
    plain(hostile)
    assert time.perf_counter() - t < 2.0


def test_xtream_epg_text_is_plain():
    assert b64(base64.b64encode(WIDGET.encode()).decode()).endswith("börjar: 15:00.")
    assert b64(base64.b64encode(b"Tom &amp; Jerry").decode()) == "Tom & Jerry"


def test_an_xmltv_guide_keeps_its_titles_and_descriptions_plain():
    desc = WIDGET.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    data = (
        '<?xml version="1.0" encoding="UTF-8"?><tv>'
        '<channel id="c1"><display-name>C1</display-name></channel>'
        '<programme channel="c1" start="20991231200000 +0000" stop="20991231220000 +0000">'
        f'<title>&lt;b&gt;Football&lt;/b&gt;</title><desc>{desc}</desc></programme>'
        '<programme channel="c1" start="20991231220000 +0000" stop="20991231230000 +0000">'
        f'<title>Rerun</title><desc>{desc}</desc></programme>'
        '</tv>').encode()
    by_id, _ = parse_xmltv(data, 1)
    _starts, _stops, titles, descs = by_id["c1"]
    assert titles == ("Football", "Rerun")
    assert descs[0] == "Matcher från engelska Premier League. Matchen börjar: 15:00."
    assert descs[0] is descs[1]         # one plain text kept for every rerun
