"""The support page (templates/support.html): a tip for the app, the one
way, and nothing given for it - no monthly support, no other way to give
(November 2026). Rendered with jq, as sync-releases.sh
does."""
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


def test_one_tip_and_nothing_given_for_it():
    page = _support("")
    assert page.count('class="sway ') == 1 and 'class="sway sway-tip"' in page
    assert '<bdi class="sway-hint">50 kr</bdi>' in page and "data-tip-dialog" in page
    for gone in ("sway-monthly", "sway-manage", "sway-price", "sp-gh", "sp-perks", "sp-money", "class=\"perk",
                 "data-monthly", "Sponsor", "monthly", "Monthly"):
        assert gone not in page, gone
    # The dialog's words: a tip, no key.
    words = re.search(r'<div id="tip-i18n" hidden([^>]*)>', page).group(1)
    assert 'data-title="Leave a tip"' in words and "data-key" not in words
    # The thank-you after a tip's Payment Link: its one word, no key.
    assert '<div id="support-thanks" class="support-thanks" hidden data-thanks=' in page
    assert "data-keycopy=" not in page and "supporter" not in page.lower()


def test_the_tip_hint_is_marked_for_the_dialog():
    assert '<bdi class="sway-hint">50 kr</bdi>' in _support("")


def test_four_short_questions_and_who_you_pay_in_the_visitor_s_language():
    # The whole of the rules is the payment terms (/payments/); the support
    # page asks four short questions in the visitor's own language.
    page = _support("", "29 kr", "sv")
    assert re.findall(r'<details class="sp-q" id="([a-z]+)"', page) == ["what", "refund"]
    assert '<div class="sp-company" id="company">' in page and "559601-3176" in page
    assert "SE559601317601" in page and 'href="/about/#foretaget"' in page
    assert "Mogatan 21, 702 13 Örebro, Sverige" in page
    assert '<p class="sp-more"><a href="/payments/">Läs hela betalvillkoren</a></p>' in page
    de = _support("", "", "de")
    assert "Bekomme ich mein Geld zurück?" in de and 'href="/about/#company"' in de
    assert "Changed your mind" not in de and "legal-meta" not in de and '<div class="sp-legal">' in de
    assert "Vad är dricksen för?" in page and "Tappat bort nyckeln?" not in page
    assert "<!--SUPPORT-OPTIONS-->" not in page and "legal-tabs" not in page
    assert 'class="perk' not in page and page.count("<li><svg") >= 2


def test_every_language_has_the_page_s_words():
    import glob
    en = json.load(open(os.path.join(SITE, "lang", "en.json"), encoding="utf-8"))
    keys = [k for k in en if k.startswith(("support_trust_", "support_faq_", "support_fq_"))]
    keys += ["support_who_h", "support_company_all", "foot_vat"]
    assert sorted(keys[:-3]) == ["support_faq_h", "support_fq_more", "support_fq_refund_a", "support_fq_refund_q",
                                 "support_fq_what_a", "support_fq_what_q", "support_trust_refund",
                                 "support_trust_stripe"]
    # Nothing left of monthly support, the perks or where the money goes.
    gone = ("support_monthly_", "support_gh_", "support_perk", "support_money_", "support_manage",
            "support_trust_cancel", "support_fq_cancel_", "support_fq_key_", "support_fq_lost_", "tip_key_wait")
    for f in glob.glob(os.path.join(SITE, "lang", "*.json")):
        have = json.load(open(f, encoding="utf-8"))
        assert not [k for k in keys if not have.get(k)], f
        assert not [k for k in have if k.startswith(gone)], f


def test_the_payment_terms_are_a_page_of_the_legal_set():
    # The support page's rules, one text, as a page with the legal tabs: each
    # folded question a numbered section, who you pay the last.
    for lang, n, last in (("sv", 7, 'id="foretaget"'), ("en", 7, 'id="company"')):
        page = _support("", "29 kr", lang, "https://billing.stripe.com/p/login/x", page="payments")
        tabs = re.search(r'<nav class="legal-tabs"[^>]*>(.*?)</nav>', page, re.S).group(1)
        assert re.findall(r'href="/([a-z]+)/"', tabs) == ["privacy", "transparency", "terms", "payments", "about"]
        assert 'href="/payments/" aria-current="page"' in tabs and "/support/" not in tabs
        assert page.count('<span class="lnum">') == n and f'<h2 {last}>' in page
        assert "sp-q" not in page and "sp-company" not in page and "%PORTAL%" not in page
        assert 'class="legal-tocbox"' in page
        assert "billing.stripe.com" not in page and "#monthly" not in page
    plain = _support("", "", "de", page="payments")
    assert 'lang="en"' in plain and "What a tip is" in plain


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
    assert ">Leave a tip</a>" in en and "· Örebro, Sweden ·" in en and "<bdi>559601-3176</bdi>" in en
    assert "· Örebro, Sverige ·" in _support("", "", "sv", page="terms")


def test_every_page_has_the_one_footer():
    # One footer for every page (render.jq, site_footer): the three groups -
    # Features first among the app's, as in the header and the menu -, the
    # payment terms among the legal links, under the hairline the company
    # with the copyright and, once, that dopeIPTV holds no channels; one
    # logo, dopeIPTV's (Solecraft's under it made two marks fight).
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
        # Each legal page its own link under the words its tab has: one
        # "Privacy & terms" that opened only the privacy page hid the terms.
        for path, word in (("/privacy/", "Privacy"), ("/terms/", "Terms"), ("/payments/", "Payment terms")):
            assert f'<a href="{path}">{word}</a>' in foot, path
            assert f'<a href="{path}">{word}</a>' in en.split("<footer>", 1)[0] or path == "/payments/", path
        assert "Privacy &amp; terms" not in en
        assert foot.count("No channels or content included.") == 1
        assert '<p class="foot-note">No channels or content included.</p>' in foot
        assert "© " in foot and "Solecraft AB</a>" in foot
        assert foot.index('href="/#features"') < foot.index('href="/#download"')
        assert foot.count('class="logo"') == 1 and "sole-logo" not in foot
        assert "foot-nocontent" not in en


def test_the_menu_has_a_computers_row_and_a_phones():
    # The narrow-window menu (render.jq, nav): rows with icons; Download for
    # a computer, "Send the link to your computer" for a phone (nav.js
    # shares or copies it) - the row a phone app of dopeIPTV's takes over
    # once there is one; the Apple app's own page (test_apple.py); feedback
    # and the rules small under them.
    import glob
    for f in glob.glob(os.path.join(SITE, "lang", "*.json")):
        assert json.load(open(f, encoding="utf-8")).get("menu_send_link"), f
    en = _support("", "", "en", page="terms")
    menu = en.split('class="navmenu-panel"', 1)[1].split("</details>", 1)[0]
    assert menu.count('class="mi-ico"') == 6
    assert 'class="navmenu-item" href="/apple/"' in menu
    assert 'class="navmenu-item menu-desk" href="/#download"' in menu
    assert 'class="navmenu-item menu-phone" href="/#download" data-send-link' in menu
    assert ">Send the link to your computer<" in menu and 'data-copied="Link copied"' in menu
    assert 'class="navmenu-foot"' in menu and "data-feedback" in menu

