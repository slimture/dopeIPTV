"""dopeIPTV for iPhone, iPad and Apple TV on the site (render.jq apple_*,
site/apple.json, templates/apple.html, style.css .apb / .ap-*): an app of
its own beside the computer app. Until its App Store address is set the
front page's band, its page /apple/ and the menu's row say it is coming;
with the address the same places point to the App Store - and only an
address on Apple's own store is taken. Every language has its words."""
import json
import os
import re
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SITE = os.path.join(ROOT, "site")

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="no jq")

KEYS = ["apple_soon", "apple_h", "apple_short", "apple_band_p", "apple_more",
        "apple_meta_title", "apple_desc", "apple_lede", "apple_iphone_p", "apple_ipad_p",
        "apple_tv_p", "apple_until_h", "apple_until_p", "apple_until_btn", "apple_store_note"]


def _f(*p):
    return os.path.join(SITE, *p)


@pytest.fixture(scope="module")
def release(tmp_path_factory):
    path = tmp_path_factory.mktemp("rel") / "releases.json"
    path.write_text(json.dumps({"version": "1.7.0", "published_at": "2026-11-24T22:34:54Z",
                                "notes": "", "html_url": "https://github.com/slimture/dopeIPTV",
                                "assets": []}), encoding="utf-8")
    return str(path)


def _render(release, page, lang="en", apple=None, tmp=None):
    tpl = {"index": "index.html", "apple": "apple.html", "sitemap": None}[page]
    args = ["jq", "-n", "-j", "-f", _f("render.jq"),
            "--arg", "page", page, "--arg", "lang", lang, "--arg", "year", "2026",
            "--arg", "sums", "", "--arg", "pics_v", "x", "--arg", "tips", "",
            "--arg", "tip_url", "", "--arg", "tip_hint", "",
            "--argjson", "avail", '["en","sv","ar"]', "--argjson", "asset_urls", "{}",
            "--argjson", "screenshots", "[]", "--argjson", "help_mtime", "0",
            "--slurpfile", "site", _f("i18n.json"), "--slurpfile", "helpfile", _f("help.json"),
            "--slurpfile", "featfile", _f("features.json"), "--slurpfile", "fmt", _f("formats.json"),
            "--slurpfile", "legal", _f("legal.json"), "--slurpfile", "en", _f("lang", "en.json"),
            "--slurpfile", "tr", _f("lang", f"{lang}.json"), "--slurpfile", "rel", release,
            "--slurpfile", "dl", os.devnull, "--slurpfile", "spots", _f("spots.json"),
            "--rawfile", "tpl", _f("templates", tpl) if tpl else os.devnull,
            "--rawfile", "navtpl", _f("templates", "nav.html"),
            "--rawfile", "body", os.devnull]
    if apple is not None:
        path = os.path.join(tmp, "apple.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(apple, fh)
        args += ["--slurpfile", "apple", path]
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def test_every_language_has_the_apple_words():
    codes = sorted(f[:-5] for f in os.listdir(_f("lang")) if f.endswith(".json"))
    assert len(codes) == 27
    for code in codes:
        words = json.load(open(_f("lang", f"{code}.json"), encoding="utf-8"))
        missing = [k for k in KEYS if not str(words.get(k) or "").strip()]
        assert not missing, (code, missing)


def test_the_settings_file_says_coming_soon():
    conf = json.load(open(_f("apple.json"), encoding="utf-8"))
    assert conf["app_store"] == ""


def test_coming_soon_on_the_front_page_its_page_and_the_menu(release, tmp_path):
    en = json.load(open(_f("lang", "en.json"), encoding="utf-8"))
    for apple in (None, {"app_store": ""}):
        front = _render(release, "index", apple=apple, tmp=str(tmp_path))
        band = re.search(r'<aside class="apb" id="apple".*?</aside>', front, re.S).group(0)
        assert en["apple_soon"] in band and 'href="/apple/"' in band
        assert "apps.apple.com" not in band
        # The band sits in the download section, after the downloads.
        assert front.index('id="download"') < front.index('class="apb"') < front.index("</section>", front.index('id="download"'))
        assert 'class="navmenu-item" href="/apple/"' in front
        page = _render(release, "apple", apple=apple, tmp=str(tmp_path))
        assert f"<h1>{en['apple_h']}</h1>" in page
        assert 'class="ap-soon"' in page and 'class="ap-note"' in page
        assert '<link rel="canonical" href="https://dopeiptv.org/apple/">' in page
        assert 'aria-current="page"' in re.search(r'href="/apple/"[^>]*>', page).group(0)


def test_with_its_store_address_the_same_places_point_there(release, tmp_path):
    store = "https://apps.apple.com/app/dopeiptv/id1234567890"
    front = _render(release, "index", apple={"app_store": store}, tmp=str(tmp_path))
    band = re.search(r'<aside class="apb".*?</aside>', front, re.S).group(0)
    assert f'href="{store}"' in band and "ap-soon" not in band
    page = _render(release, "apple", apple={"app_store": store}, tmp=str(tmp_path))
    assert f'href="{store}"' in page and "ap-soon" not in page and "ap-note" not in page


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "http://apps.apple.com/app/x",
                                 "https://apps.apple.com.evil.example/app",
                                 'https://apps.apple.com/app/"><script>', 42])
def test_only_an_address_on_apples_store_is_taken(release, tmp_path, bad):
    page = _render(release, "apple", apple={"app_store": bad}, tmp=str(tmp_path))
    assert 'class="ap-soon"' in page
    assert "javascript:" not in page and "<script>" not in page and "evil" not in page


def test_its_page_in_every_language_of_the_site_and_in_the_sitemap(release, tmp_path):
    for code in ("sv", "ar"):
        page = _render(release, "apple", lang=code, tmp=str(tmp_path))
        words = json.load(open(_f("lang", f"{code}.json"), encoding="utf-8"))
        assert words["apple_lede"] in page
    assert 'dir="rtl"' in _render(release, "apple", lang="ar", tmp=str(tmp_path))
    sitemap = _render(release, "sitemap", tmp=str(tmp_path))
    for code in ("en", "sv", "ar"):
        url = "https://dopeiptv.org/apple/" + ("" if code == "en" else f"?lang={code}")
        assert f"<loc>{url}</loc>" in sitemap


def test_the_sync_renders_it_and_hands_it_its_settings():
    sync = open(os.path.join(ROOT, "sync-releases.sh"), encoding="utf-8").read()
    assert 'render apple "$c" "$SITE/templates/apple.html" "$OUT/i18n/$c/apple/index.html"' in sync
    assert '--slurpfile apple "$SITE/apple.json"' in sync
