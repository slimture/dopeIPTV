"""Monthly support's price on the support page in the visitor's currency:
render.jq reads SUPPORT_MONTHLY_PRICES ("SEK 29, EUR 2.50, GBP 2,50") into
the page as data-prices for nav.js, with SUPPORT_MONTHLY_PRICE - else the
first - as the words without JavaScript. Rendered with jq, as
sync-releases.sh does."""
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

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="no jq")


def _support(prices: str, price: str = "", lang: str = "en") -> str:
    def f(*p):
        return os.path.join(SITE, *p)
    written = lang if os.path.exists(f("legal", lang, "support.html")) else "en"
    args = ["jq", "-n", "-j", "-f", f("render.jq"),
            "--arg", "page", "support", "--arg", "lang", lang, "--arg", "year", "2026",
            "--arg", "sums", "", "--arg", "pics_v", "x", "--arg", "tips", "on",
            "--arg", "tip_url", "", "--arg", "monthly_url", "https://buy.stripe.com/x",
            "--arg", "sponsors_url", "", "--arg", "tip_hint", "50 kr",
            "--arg", "monthly_price", price, "--arg", "monthly_prices", prices,
            "--argjson", "avail", '["en","sv"]', "--argjson", "asset_urls", "{}",
            "--argjson", "screenshots", "[]", "--argjson", "help_mtime", "0",
            "--slurpfile", "site", f("i18n.json"), "--slurpfile", "helpfile", f("help.json"),
            "--slurpfile", "featfile", f("features.json"), "--slurpfile", "fmt", f("formats.json"),
            "--slurpfile", "legal", f("legal.json"), "--slurpfile", "en", f("lang", "en.json"),
            "--slurpfile", "tr", f("lang", f"{lang}.json"), "--slurpfile", "rel", os.devnull,
            "--slurpfile", "dl", os.devnull, "--slurpfile", "spots", f("spots.json"),
            "--rawfile", "tpl", f("templates", "legal.html"),
            "--rawfile", "navtpl", f("templates", "nav.html"),
            "--rawfile", "body", f("legal", written, "support.html")]
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def _price(page: str):
    m = re.search(r'<bdi class="sway-price"([^>]*)>([^<]*)</bdi>', page)
    assert m, "no monthly price on the support page"
    attrs = dict(re.findall(r'(data-[a-z]+)="([^"]*)"', m.group(1)))
    prices = json.loads(html.unescape(attrs["data-prices"])) if "data-prices" in attrs else None
    return prices, attrs.get("data-base"), html.unescape(m.group(2))


def test_each_currency_and_the_first_without_javascript():
    prices, base, words = _price(_support("SEK 29, EUR 2.50, GBP 2,50; usd 3"))
    assert prices == {"sek": "29", "eur": "2.50", "gbp": "2.50", "usd": "3"}
    assert base == "sek" and words == "29 SEK"


def test_the_price_as_written_wins_without_javascript():
    _prices, base, words = _price(_support("SEK 29, EUR 2.50", "29 kr", "sv"))
    assert base == "sek" and words == "29 kr"


def test_a_line_that_is_no_price_is_left_out():
    prices, _base, _words = _price(_support("SEK 29, nonsense, EUR two, USD 3.999, NOK 29"))
    assert prices == {"sek": "29", "nok": "29"}


def test_without_the_list_the_page_says_the_price_as_before():
    prices, base, words = _price(_support("", "30 kr"))
    assert prices is None and base is None and words == "30 kr"


def test_the_tip_hint_is_marked_for_the_dialog():
    assert '<bdi class="sway-hint">50 kr</bdi>' in _support("")
