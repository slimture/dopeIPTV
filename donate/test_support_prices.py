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


def _support(prices: str, price: str = "", lang: str = "en", portal: str = "",
             page: str = "support") -> str:
    def f(*p):
        return os.path.join(SITE, *p)
    src = json.load(open(f("legal.json"), encoding="utf-8")).get("source", {}).get(page, page)
    written = lang if os.path.exists(f("legal", lang, f"{src}.html")) else "en"
    tpl = f("templates", f"{page}.html")
    if not os.path.exists(tpl):
        tpl = f("templates", "legal.html")
    args = ["jq", "-n", "-j", "-f", f("render.jq"),
            "--arg", "page", page, "--arg", "lang", lang, "--arg", "year", "2026",
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
            "--rawfile", "tpl", tpl,
            "--rawfile", "navtpl", f("templates", "nav.html"),
            "--rawfile", "body", f("legal", written, f"{src}.html")]
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
    assert 'tryck på <a href="https://billing.stripe.com/p/login/x">Hantera eller avsluta ditt månadsstöd</a> ovan' in page
    plain = _support("", "29 kr", "sv")
    assert "sway-manage" not in plain and 'href="#monthly">Hantera' in plain
    assert "%PORTAL%" not in plain and "%MANAGE%" not in plain and "%EMAIL%" not in plain


def test_four_short_questions_and_who_you_pay_in_the_visitor_s_language():
    # The whole of the rules is the payment terms (/payments/); the support
    # page asks four short questions in the visitor's own language.
    page = _support("", "29 kr", "sv")
    assert re.findall(r'<details class="sp-q" id="([a-z]+)"', page) == ["cancel", "refund", "key", "again"]
    assert '<div class="sp-company" id="company">' in page and "559601-3176" in page
    assert "SE559601317601" in page and 'href="/about/#foretaget"' in page
    assert "Mogatan 21, 702 13 Örebro, Sverige" in page
    assert '<p class="sp-more"><a href="/payments/">Läs hela betalvillkoren</a></p>' in page
    de = _support("", "", "de")
    assert "Bekomme ich mein Geld zurück?" in de and 'href="/about/#company"' in de
    assert "Changed your mind" not in de and "legal-meta" not in de and '<div class="sp-legal">' in de
    assert "Tappat bort nyckeln?" in page
    assert "<!--SUPPORT-OPTIONS-->" not in page and "legal-tabs" not in page
    assert page.count('class="perk ') == 4 and page.count("<li><svg") >= 3


def test_every_language_has_the_page_s_words():
    import glob
    keys = [k for k in json.load(open(os.path.join(SITE, "lang", "en.json"), encoding="utf-8"))
            if k.startswith(("support_trust_", "support_perk", "support_money_", "support_faq_", "support_manage"))]
    keys += [k for k in json.load(open(os.path.join(SITE, "lang", "en.json"), encoding="utf-8"))
             if k.startswith("support_fq_")] + ["support_who_h", "support_company_all", "foot_vat"]
    assert len(keys) == 15 + 12
    for f in glob.glob(os.path.join(SITE, "lang", "*.json")):
        have = json.load(open(f, encoding="utf-8"))
        assert not [k for k in keys if not have.get(k)], f


def test_the_payment_terms_are_a_page_of_the_legal_set():
    # The support page's rules, one text, as a page with the legal tabs: each
    # folded question a numbered section, who you pay the last.
    for lang, n, last in (("sv", 10, 'id="foretaget"'), ("en", 10, 'id="company"')):
        page = _support("", "29 kr", lang, "https://billing.stripe.com/p/login/x", page="payments")
        tabs = re.search(r'<nav class="legal-tabs"[^>]*>(.*?)</nav>', page, re.S).group(1)
        assert re.findall(r'href="/([a-z]+)/"', tabs) == ["privacy", "terms", "payments", "about"]
        assert 'href="/payments/" aria-current="page"' in tabs and "/support/" not in tabs
        assert page.count('<span class="lnum">') == n and f'<h2 {last}>' in page
        assert "sp-q" not in page and "sp-company" not in page and "%PORTAL%" not in page
        assert 'class="legal-tocbox"' in page
        assert 'href="https://billing.stripe.com/p/login/x"' in page
    plain = _support("", "", "de", page="payments")
    assert 'lang="en"' in plain and 'href="/support/#monthly"' in plain


def test_the_support_page_links_its_rules_and_keeps_no_tabs():
    page = _support("", "29 kr", "sv")
    assert '<li class="sp-trust-terms">' in page and '<a href="/payments/">Betalvillkor</a>' in page
    for p in ("privacy", "terms", "about"):
        assert "legal-tabs" in _support("", "", "en", page=p)


def test_the_heart_says_support_us_and_the_footer_the_country():
    import glob
    for f in glob.glob(os.path.join(SITE, "lang", "*.json")):
        have = json.load(open(f, encoding="utf-8"))
        assert have.get("support_nav") and have.get("foot_payments"), f
        assert "%TOWN%" in have.get("foot_place", ""), f
        assert "foot_support" not in have, f
    en = _support("", "", "en", page="terms")
    assert ">Support us</a>" in en and "· Örebro, Sweden ·" in en and "<bdi>559601-3176</bdi>" in en
    assert "· Örebro, Sverige ·" in _support("", "", "sv", page="terms")


def test_every_page_has_the_one_footer():
    # One footer for every page (render.jq, site_footer): the three groups,
    # the payment terms among the legal links, and on the company line that
    # dopeIPTV holds no channels - once, not a sentence of its own.
    import glob
    for f in glob.glob(os.path.join(SITE, "lang", "*.json")):
        have = json.load(open(f, encoding="utf-8"))
        for k in ("foot_tagline", "foot_h_app", "foot_h_contact", "foot_h_legal", "foot_nocontent"):
            assert have.get(k), (f, k)
    for t in glob.glob(os.path.join(SITE, "templates", "*.html")):
        text = open(t, encoding="utf-8").read()
        if "</body>" in text:
            assert "{{v:footer}}" in text and "<footer>" not in text, t
    for page in ("terms", "support"):
        en = _support("", "", "en", page=page)
        foot = en.split("<footer>", 1)[1]
        assert foot.count('class="foot-col"') == 3
        assert 'href="/payments/"' in foot and 'href="/about/"' in foot and "data-feedback" in foot
        assert "· No channels or content included.</p>" in foot
        assert "foot-nocontent" not in en


def test_nginx_serves_every_legal_page():
    # A page sync-releases.sh renders but nginx does not route is a 404 on
    # the live site - which /payments/ was, its first hour.
    pages = set(json.load(open(os.path.join(SITE, "legal.json"), encoding="utf-8"))["pages"])
    conf = open(os.path.join(ROOT, "deploy", "content-iptv.dope.rs.conf"), encoding="utf-8").read()
    routes = re.findall(r"location ~ \^/\(\?<iptv_legal(?:_bare)?>([a-z|]+)\)", conf)
    assert len(routes) == 2
    for r in routes:
        assert set(r.split("|")) == pages


def test_the_menu_has_a_computers_row_and_a_phones():
    # The narrow-window menu (render.jq, nav): rows with icons; Download for
    # a computer, "Send the link to your computer" for a phone (nav.js
    # shares or copies it) - the row a phone app of dopeIPTV's takes over
    # once there is one; feedback and the rules small under them.
    import glob
    for f in glob.glob(os.path.join(SITE, "lang", "*.json")):
        assert json.load(open(f, encoding="utf-8")).get("menu_send_link"), f
    en = _support("", "", "en", page="terms")
    menu = en.split('class="navmenu-panel"', 1)[1].split("</details>", 1)[0]
    assert menu.count('class="mi-ico"') == 5
    assert 'class="navmenu-item menu-desk" href="/#download"' in menu
    assert 'class="navmenu-item menu-phone" href="/#download" data-send-link' in menu
    assert ">Send the link to your computer<" in menu and 'data-copied="Link copied"' in menu
    assert 'class="navmenu-foot"' in menu and "data-feedback" in menu

