"""The front page's downloads (render.jq, downloads; style.css .dlx; app.js):
a card per system, all built alike - name, how it installs, the processor,
one button with the file's size, its format, the other formats folded, how
to install - and on a phone one card that sends the link to the computer.
Rendered with jq from a release in the shape sync-releases.sh writes."""
import html
import json
import os
import re
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SITE = os.path.join(ROOT, "site")
PUBLIC = os.path.join(ROOT, "public")

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="no jq")

# name, size in bytes, the size as sync-releases.sh writes it in "sub"
FILES = [
    ("dopeIPTV-1.6.6-x86_64.AppImage", 164981240, "157.3 MB"),
    ("dopeIPTV-1.6.6-aarch64.AppImage", 169765384, "161.9 MB"),
    ("dopeiptv_1.6.6_amd64.deb", 163882922, "156.3 MB"),
    ("dopeiptv_1.6.6_arm64.deb", 167702664, "159.9 MB"),
    ("dopeIPTV-1.6.6.flatpak", 73806800, "70.4 MB"),
    ("dopeIPTV-1.6.6-macOS-arm64.dmg", 67262434, "64.1 MB"),
    ("dopeIPTV-1.6.6-macOS-x86_64.dmg", 102174459, "97.4 MB"),
    ("dopeIPTV-1.6.6-windows-x64-setup.exe", 78775428, "75.1 MB"),
    ("dopeIPTV-1.6.6-windows-arm64-setup.exe", 59639587, "56.9 MB"),
    ("dopeIPTV-1.6.6-windows-x64.zip", 105260615, "100.4 MB"),
    ("dopeIPTV-1.6.6-windows-arm64.zip", 80811119, "77.1 MB"),
]


@pytest.fixture(scope="module")
def release(tmp_path_factory):
    path = tmp_path_factory.mktemp("rel") / "releases.json"
    assets = [{"label": "x", "sub": "x · " + human, "icon": "", "url": "/files/" + name,
               "name": name, "sha256": "0" * 64, "size": size} for name, size, human in FILES]
    path.write_text(json.dumps({"version": "1.6.6", "published_at": "2026-11-18T23:47:42Z",
                                "notes": "", "html_url": "https://github.com/slimture/dopeIPTV",
                                "assets": assets}), encoding="utf-8")
    return str(path)


def _index(release: str, lang: str = "en") -> str:
    def f(*p):
        return os.path.join(SITE, *p)
    args = ["jq", "-n", "-j", "-f", f("render.jq"),
            "--arg", "page", "index", "--arg", "lang", lang, "--arg", "year", "2026",
            "--arg", "sums", "", "--arg", "pics_v", "x", "--arg", "tips", "on",
            "--arg", "tip_url", "", "--arg", "monthly_url", "https://buy.stripe.com/x",
            "--arg", "sponsors_url", "", "--arg", "portal_url", "", "--arg", "tip_hint", "50 kr",
            "--arg", "monthly_price", "", "--arg", "monthly_prices", "",
            "--argjson", "avail", '["en","sv"]', "--argjson", "asset_urls", "{}",
            "--argjson", "screenshots", "[]", "--argjson", "help_mtime", "0",
            "--slurpfile", "site", f("i18n.json"), "--slurpfile", "helpfile", f("help.json"),
            "--slurpfile", "featfile", f("features.json"), "--slurpfile", "fmt", f("formats.json"),
            "--slurpfile", "legal", f("legal.json"), "--slurpfile", "en", f("lang", "en.json"),
            "--slurpfile", "tr", f("lang", f"{lang}.json"), "--slurpfile", "rel", release,
            "--slurpfile", "dl", os.devnull, "--slurpfile", "spots", f("spots.json"),
            "--rawfile", "tpl", f("templates", "index.html"),
            "--rawfile", "navtpl", f("templates", "nav.html"),
            "--rawfile", "body", os.devnull]
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def _cards(page: str) -> dict:
    return dict(re.findall(r'<article class="dlx-card" data-os="([a-z]+)">(.*?)</article>', page, re.S))


def _lang(code: str) -> dict:
    return json.load(open(os.path.join(SITE, "lang", f"{code}.json"), encoding="utf-8"))


def test_every_card_is_built_alike(release):
    en = _lang("en")
    cards = _cards(_index(release))
    assert list(cards) == ["linux", "macos", "windows"]
    for os_id, card in cards.items():
        parts = re.findall(r'^ {12}<(h3|p class="dlx-sub"|div class="dlx-[a-z]+")', card, re.M)
        assert parts == ["h3", 'p class="dlx-sub"', 'div class="dlx-pick"', 'div class="dlx-btns"',
                         'div class="dlx-meta"', 'div class="dlx-foot"'], os_id
        assert f'<p class="dlx-sub">{html.escape(en["dl_s_" + os_id])}</p>' in card
        radios = re.findall(r'<input type="radio" name="arch-%s" id="arch-%s-(\d)"( checked)?>' % (os_id, os_id), card)
        assert radios == [("0", " checked"), ("1", "")], os_id
        assert re.findall(r'class="dlx-big" data-k="(\d)"', card) == ["0", "1"]
        assert re.findall(r'<p data-k="(\d)">', card) == ["0", "1"]
        assert 'class="dlx-how"' in card
    assert re.findall(r'<label for="arch-macos-\d">([^<]+)</label>', cards["macos"]) == ["Apple Silicon", "Intel"]
    assert re.findall(r'<label for="arch-linux-\d">([^<]+)</label>', cards["linux"]) == ["Intel / AMD", "ARM"]
    # The usual file is the one chosen: x86-64 on Linux and Windows, Apple Silicon on a Mac.
    assert 'data-k="0" href="/dl.php?f=dopeIPTV-1.6.6-x86_64.AppImage"' in cards["linux"]
    assert 'data-k="0" href="/dl.php?f=dopeIPTV-1.6.6-windows-x64-setup.exe"' in cards["windows"]
    assert 'data-k="0" href="/dl.php?f=dopeIPTV-1.6.6-macOS-arm64.dmg"' in cards["macos"]
    # The rest folded under Other formats, counted; a Mac's foot says which Mac instead.
    assert re.search(r'<summary>Other formats <span class="n">3</span></summary>', cards["linux"])
    assert re.search(r'<summary>Other formats <span class="n">2</span></summary>', cards["windows"])
    assert "dlx-more" not in cards["macos"]
    assert f'<p class="dlx-hint">{html.escape(en["dl_which_mac"])}</p>' in cards["macos"]


def test_sizes_are_written_as_the_reader_writes_numbers(release):
    en = _cards(_index(release, "en"))["linux"]
    sv = _cards(_index(release, "sv"))["linux"]
    fa = _cards(_index(release, "fa"))["linux"]
    assert "<small>157.3 MB</small>" in en
    assert "<small>157,3 MB</small>" in sv and "157.3" not in sv
    assert "<small>۱۵۷٫۳ MB</small>" in fa
    # the tooltip too
    assert re.search(r'title="AppImage[^"]*157,3 MB', sv)


def test_other_formats_say_who_each_is_for_without_the_file_ending(release):
    for lang in ("en", "sv", "ar", "zh"):
        cards = _cards(_index(release, lang))
        rows = re.findall(r"<li><a [^>]*><b>[^<]*</b><span>([^<]*)</span><em>([^<]*)</em></a></li>",
                          cards["windows"] + cards["linux"])
        assert len(rows) == 5, lang
        for what, size in rows:
            assert not re.match("^‎?\\.[a-z]+", what), (lang, what)
            assert size.endswith("MB"), (lang, size)


def test_the_phone_card_uses_the_menus_words(release):
    page = _index(release, "sv")
    sv = _lang("sv")
    phone = re.search(r'<div class="dl-phone">(.*?)</div>\n      </div>', page, re.S).group(1)
    assert f"<h3>{html.escape(sv['dl_phone_h'])}</h3>" in phone
    assert re.search(r'id="dlShare"[^>]*>%s</button>' % re.escape(sv["menu_send_link"]), phone)
    assert "dl-phone-ico" in phone


def test_every_language_has_the_downloads_words():
    import glob
    keys = ("dl_s_linux", "dl_s_macos", "dl_s_windows", "dl_pick", "dl_phone_h", "dl_phone_p")
    for f in glob.glob(os.path.join(SITE, "lang", "*.json")):
        have = json.load(open(f, encoding="utf-8"))
        for k in keys:
            assert have.get(k), (f, k)
        assert "dl_phone_share" not in have and "dl_phone_p2" not in have, f


def test_the_processor_choice_needs_no_script_and_the_rows_line_up():
    css = open(os.path.join(PUBLIC, "style.css"), encoding="utf-8").read()
    assert '.dlx-card:has(.dlx-pick input:nth-of-type(2):checked) [data-k="0"]' in css
    assert '.dlx-card:not(:has(.dlx-pick input:nth-of-type(2):checked)) [data-k="1"]' in css
    assert re.search(r"\.dlx-card \{[^}]*grid-template-rows: subgrid", css)
    # A card's other formats close on a click elsewhere or Escape, side by side.
    js = open(os.path.join(PUBLIC, "app.js"), encoding="utf-8").read()
    assert ".dlx-more" in js and '"Escape"' in js
