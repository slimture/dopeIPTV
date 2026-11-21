"""/pay/wallet charges only an Apple Pay or Google Pay card, as Stripe
says the payment method is (donate.py, wallet_pay), and says a refusal only
as one - never Stripe's reason - so it is no way to test stolen cards.
Stripe is a stand-in here: nothing is sent anywhere."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _donate():
    root = tempfile.mkdtemp()
    conf = os.path.join(root, "donate.conf")
    with open(conf, "w") as fh:
        fh.write(f"STATS_DIR={root}\nLISTEN=127.0.0.1:0\n")
    os.environ["DONATE_CONFIG"] = conf
    sys.modules.pop("donate", None)
    import donate
    donate._secret = lambda: "sk_test_stand_in"
    return donate


PM = "pm_1QabcdEFGHijklMN"


def _stripe(donate, pm_status=200, pm=None, intent=(200, {"status": "succeeded"})):
    calls = []

    def post(url, form, key):
        calls.append(url)
        assert url == donate.STRIPE_PM + PM and form is None
        return pm_status, pm or {}

    def intent_(form, key):
        calls.append("intent")
        assert form["payment_method"] == PM and form["confirm"] == "true"
        return intent
    donate._post, donate._intent = post, intent_
    return calls


def test_a_wallet_card_is_charged():
    donate = _donate()
    for wallet in ("apple_pay", "google_pay"):
        calls = _stripe(donate, pm={"type": "card", "card": {"wallet": {"type": wallet}}})
        assert donate.wallet_pay(500, "eur", PM, "", "") == (200, {"ok": True})
        assert calls[-1] == "intent"


def test_a_card_typed_in_elsewhere_is_never_charged():
    donate = _donate()
    for pm in ({"type": "card", "card": {"wallet": None}},
               {"type": "card", "card": {"wallet": {"type": "link"}}},
               {"type": "card", "card": {}},
               {"type": "sepa_debit"}):
        calls = _stripe(donate, pm=pm)
        assert donate.wallet_pay(200, "eur", PM, "", "") == (402, {"error": "declined"})
        assert "intent" not in calls
    # One made with another account's key is unknown here: the same answer.
    calls = _stripe(donate, pm_status=404, pm={"error": {"message": "No such PaymentMethod"}})
    assert donate.wallet_pay(200, "eur", PM, "", "") == (402, {"error": "declined"})
    # Stripe not answering is not a decline.
    _stripe(donate, pm_status=0, pm={"error": {"message": "timed out"}})
    assert donate.wallet_pay(200, "eur", PM, "", "") == (502, {"error": "stripe"})
    assert donate.wallet_pay(200, "eur", "pm_x;", "", "")[0] == 400


def test_a_decline_never_says_why():
    donate = _donate()
    _stripe(donate, pm={"type": "card", "card": {"wallet": {"type": "apple_pay"}}},
            intent=(402, {"error": {"type": "card_error", "code": "incorrect_cvc",
                                    "message": "Your card's security code is incorrect."}}))
    status, body = donate.wallet_pay(200, "eur", PM, "", "")
    assert (status, body) == (402, {"error": "declined"})


def test_the_page_says_its_own_words_for_any_refusal():
    js = open(os.path.join(os.path.dirname(HERE), "public", "nav.js"), encoding="utf-8").read()
    assert 'ev.complete("fail");\n        walletFailed();' in js
    assert "res.error !== \"stripe\" ? res.error" not in js


def test_a_tip_is_for_the_app_and_gives_nothing():
    # Neither way to pay carries anything beside the tip.
    donate = _donate()
    forms = []
    donate.PUBLISHABLE = "pk_test_stand_in"

    def intent_(form, key):
        forms.append(form)
        return 200, {"status": "succeeded", "id": "pi_1QabcdEFGHijklMN", "created": 1795305660,
                     "client_secret": "pi_1QabcdEFGHijklMN_secret_x"}
    donate._post = lambda url, form, key: (200, {"type": "card", "card": {"wallet": {"type": "apple_pay"}}})
    donate._intent = intent_
    assert donate.wallet_pay(500, "eur", PM, "", "") == (200, {"ok": True})
    assert donate.create_session(500, "eur")[0] == 200
    for form in forms:
        assert form["description"] == donate.NAME and not [k for k in form if "supporter" in k]
    assert not hasattr(donate, "key_for") and not hasattr(donate, "find_key")
