"""The support page (templates/support.html). Monthly support's price in
the visitor's currency:
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


def _support(prices: str, price: str = "", lang: str = "en", portal: str = "") -> str:
    def f(*p):
        return os.path.join(SITE, *p)
    written = lang if os.path.exists(f("legal", lang, "support.html")) else "en"
    args = ["jq", "-n", "-j", "-f", f("render.jq"),
            "--arg", "page", "support", "--arg", "lang", lang, "--arg", "year", "2026",
            "--arg", "sums", "", "--arg", "pics_v", "x", "--arg", "tips", "on",
            "--arg", "tip_url", "", "--arg", "monthly_url", "https://buy.stripe.com/x",
            "--arg", "sponsors_url", "", "--arg", "portal_url", portal, "--arg", "tip_hint", "50 kr",
            "--arg", "monthly_price", price, "--arg", "monthly_prices", prices,
            "--argjson", "avail", '["en","sv"]', "--argjson", "asset_urls", "{}",
            "--argjson", "screenshots", "[]", "--argjson", "help_mtime", "0",
            "--slurpfile", "site", f("i18n.json"), "--slurpfile", "helpfile", f("help.json"),
            "--slurpfile", "featfile", f("features.json"), "--slurpfile", "fmt", f("formats.json"),
            "--slurpfile", "legal", f("legal.json"), "--slurpfile", "en", f("lang", "en.json"),
            "--slurpfile", "tr", f("lang", f"{lang}.json"), "--slurpfile", "rel", os.devnull,
            "--slurpfile", "dl", os.devnull, "--slurpfile", "spots", f("spots.json"),
            "--rawfile", "tpl", f("templates", "support.html"),
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


def test_the_portal_under_monthly_support_and_in_the_rules():
    page = _support("", "29 kr", "sv", "https://billing.stripe.com/p/login/x")
    assert '<a class="sway-manage" href="https://billing.stripe.com/p/login/x"' in page
    assert 'href="https://billing.stripe.com/p/login/x">Hantera eller avsluta ditt månadsstöd</a> under Månadsstöd' in page
    plain = _support("", "29 kr", "sv")
    assert "sway-manage" not in plain and 'href="#monthly">Hantera' in plain
    assert "%PORTAL%" not in plain


def test_the_rules_fold_up_and_who_you_pay_stays_open():
    page = _support("", "29 kr", "sv")
    assert page.count('<details class="sp-q"') == 9
    assert '<div class="sp-company" id="foretaget">' in page and "559601-3176" in page
    assert "<!--SUPPORT-OPTIONS-->" not in page and "legal-tabs" not in page
    assert page.count('class="perk ') == 4 and page.count("<li><svg") >= 3


def test_every_language_has_the_page_s_words():
    import glob
    keys = [k for k in json.load(open(os.path.join(SITE, "lang", "en.json"), encoding="utf-8"))
            if k.startswith(("support_trust_", "support_perk", "support_money_", "support_faq_", "support_manage"))]
    assert len(keys) == 15
    for f in glob.glob(os.path.join(SITE, "lang", "*.json")):
        have = json.load(open(f, encoding="utf-8"))
        assert not [k for k in keys if not have.get(k)], f
