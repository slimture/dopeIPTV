"""A home network split in parts (VLANs, subnets): the other networks Nerd
searches (core/home_net.py) - what a line may say, why one is refused, the
caps that keep a search short - and who counts as being at home for the
remote API and the phone remote: every private range, not only the part
this computer is on, never an address on the internet."""
from __future__ import annotations

import http.client
import json

import pytest

from dopeiptv.core import home_net, nerd
from dopeiptv.core.nerd_check import FAIL, INFO, OK
from dopeiptv.core.phone_remote import PhoneServer
from dopeiptv.core.remote_api import RemoteServer
from tests.phone_sign import signed_request

TOKEN = "Ab3_-x9Ab3_-x9Ab3_-x9Ab3_-x9Qz12"


class FakeSettings:
    def __init__(self, **kw):
        self.d = dict(kw)

    def value(self, key, default=None):
        return self.d.get(key, default)

    def setValue(self, key, value):  # noqa: N802
        self.d[key] = value


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)


# -- the list -------------------------------------------------------------------------

def test_each_form_of_an_entry():
    assert home_net.read_entry("192.168.20.0/24") == (int(0xC0A81400), int(0xC0A814FF))
    # Host bits set: the network it is in.
    assert home_net.read_entry("192.168.20.77/24") == home_net.read_entry("192.168.20.0/24")
    assert home_net.read_entry("10.0.40.5") == (0x0A002805, 0x0A002805)
    assert home_net.read_entry("192.168.30.10-60") == (0xC0A81E0A, 0xC0A81E3C)
    assert home_net.read_entry("192.168.30.10-192.168.31.4") == (0xC0A81E0A, 0xC0A81F04)
    assert home_net.read_entry("172.20.0.0/22")[1] - home_net.read_entry("172.20.0.0/22")[0] == 1023
    assert home_net.read_entry("100.100.1.5") == (0x64640105, 0x64640105)


@pytest.mark.parametrize("text, why", [
    ("8.8.8.8", "public"), ("192.0.2.0/24", "public"), ("127.0.0.1", "public"),
    ("172.32.0.1", "public"), ("192.168.255.250-192.169.0.4", "public"),
    ("192.168.0.0/16", "big"), ("10.0.0.0/21", "big"), ("10.0.0.0-10.0.8.0", "big"),
    ("fd00::/64", "ipv6"), ("fd00::5", "ipv6"),
    ("192.168.1.50-10", "order"), ("tv.lan", "form"), ("192.168.1", "form"),
    ("192.168.1.0/x", "form"), ("192.168.1.0/33", "form"), ("300.1.1.1", "form"),
    ("192.168.1.1-300", "form"),
])
def test_a_line_that_cannot_be_searched_says_why(text, why):
    assert home_net.read_entry(text) == why
    entries, problems = home_net.read(f"10.0.0.5\n{text}")
    assert [e.text for e in entries] == ["10.0.0.5"]
    assert [(p.line, p.text, p.reason) for p in problems] == [(2, text, why)]
    check = home_net.check_list(f"10.0.0.5\n{text}")
    assert check.state == FAIL and check.key == f"nerd_ck_ln_{why}" and check.params["n"] == 2
    assert why in home_net.REASONS


def test_several_on_a_line_and_comments():
    entries, problems = home_net.read("# the IoT VLAN\n192.168.20.0/24, 192.168.30.5 ; 10.0.0.9-12  # TVs\n\n")
    assert [(e.line, e.text) for e in entries] == [(2, "192.168.20.0/24"), (2, "192.168.30.5"),
                                                   (2, "10.0.0.9-12")]
    assert problems == []


def test_the_total_is_capped_and_an_address_counts_once():
    two = "192.168.20.0/22\n192.168.24.0/22"
    entries, problems = home_net.read(two)
    assert len(entries) == 2 and not problems                 # 2048 exactly
    _e, problems = home_net.read(two + "\n192.168.40.1")
    assert [(p.line, p.reason) for p in problems] == [(3, "total")]
    assert home_net.check_list(two + "\n192.168.40.1").params["max"] == home_net.MAX_TOTAL == 2048
    # Named again, or inside one already there: nothing more to count.
    entries, problems = home_net.read(two + "\n192.168.20.5\n192.168.21.0/24")
    assert len(entries) == 4 and not problems


def test_the_addresses_searched():
    got = home_net.addresses("192.168.20.0/30\n192.168.20.2\n10.0.0.7\n10.0.0.1-3")
    # A network's first and last are no device's; each address once.
    assert got == ["192.168.20.1", "192.168.20.2", "10.0.0.7", "10.0.0.1", "10.0.0.2", "10.0.0.3"]
    assert home_net.addresses("192.168.20.0/31") == ["192.168.20.0", "192.168.20.1"]
    assert len(home_net.addresses("192.168.20.0/24")) == 254
    assert home_net.addresses("192.168.20.0/30", skip=["192.168.20.1"]) == ["192.168.20.2"]
    assert len(home_net.addresses("192.168.20.0/22\n192.168.24.0/22")) == 2044
    assert home_net.addresses("8.8.8.8\nnonsense") == []


def test_the_line_under_the_box():
    assert home_net.check_list("").state == INFO
    assert home_net.check_list("   \n# nothing yet").key == "nerd_ck_ln_none"
    ok = home_net.check_list("192.168.20.0/24\n192.168.30.5")
    assert ok.state == OK and ok.params == {"nets": 2, "n": 255}


def test_only_while_nerd_is_on():
    on = FakeSettings(nerd_enabled="true", nerd_other_networks="192.168.77.0/30")
    assert home_net.configured(on) == ["192.168.77.1", "192.168.77.2"]
    assert home_net.configured(nerd.Nerd(on)) == ["192.168.77.1", "192.168.77.2"]
    off = FakeSettings(nerd_enabled="false", nerd_other_networks="192.168.77.0/30")
    assert home_net.configured(off) == []
    assert home_net.configured(FakeSettings(nerd_enabled="true")) == []


# -- who is at home -------------------------------------------------------------------------

@pytest.mark.parametrize("addr", [
    "10.1.2.3", "10.255.0.9", "172.16.0.1", "172.20.5.6", "172.31.255.254", "192.168.1.20",
    "192.168.30.7", "100.101.102.103", "127.0.0.1", "169.254.10.1", "fd00::1", "fd12:3456::9",
    "fc00::5", "fe80::1", "fe80::1%eth0", "[fd00::2]", "::1", "::ffff:192.168.20.5",
])
def test_every_private_range_is_at_home(addr):
    assert home_net.is_home_client(addr)


@pytest.mark.parametrize("addr", [
    "8.8.8.8", "1.1.1.1", "172.32.0.1", "192.169.0.1", "203.0.113.9", "192.0.2.1",
    "2001:db8::1", "2a00:1450:4001::200e", "::ffff:8.8.8.8", "", "not-an-address", "fritz.box",
])
def test_an_address_on_the_internet_is_not(addr):
    assert not home_net.is_home_client(addr)


def test_this_computer_at_any_address_of_its_own_is_at_home():
    """A client at the very address it reached us at is this computer - one
    with an address outside the private ranges too."""
    assert home_net.is_home_client("192.0.2.2", own="192.0.2.2")
    assert home_net.is_home_client("::ffff:203.0.113.5", own="203.0.113.5")
    assert not home_net.is_home_client("192.0.2.3", own="192.0.2.2")
    assert not home_net.is_home_client("8.8.8.8", own="")


def _ask(port: int, path: str, headers: dict | None = None) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    path, _data, h = signed_request(TOKEN, "GET", path, None, headers)
    conn.request("GET", path, headers=h)
    r = conn.getresponse()
    body = r.read()
    conn.close()
    return r.status, body


def test_the_remote_api_lets_the_home_network_in_and_the_internet_not(monkeypatch):
    seen: list[str] = []
    verdict = {"ok": True}

    def judge(address: str, own: str = "") -> bool:
        seen.append(address)
        return verdict["ok"]
    srv = RemoteServer("127.0.0.1", 0, TOKEN, lambda action, params: {"ok": True})
    monkeypatch.setattr(srv, "client_ok", judge)
    srv.start()
    try:
        auth = {"Authorization": f"Bearer {TOKEN}"}
        assert _ask(srv.port, "/", auth)[0] == 200
        assert seen == ["127.0.0.1"]
        verdict["ok"] = False                  # as if it came from the internet
        code, body = _ask(srv.port, "/", auth)
        assert code == 403 and b"home network" in body
    finally:
        srv.stop()
    assert RemoteServer.client_ok("172.20.1.4") and RemoteServer.client_ok("fd00::7")
    assert not RemoteServer.client_ok("8.8.8.8")


def test_the_phone_remote_lets_the_home_network_in_and_the_internet_not(monkeypatch):
    from dopeiptv.core import phone_page
    verdict = {"ok": True}

    def page():
        return phone_page.render({"bg": "#17171C", "text": "#ECECF1", "accent": "#4C8DFF"},
                                 {"bg": "#F5F5F7", "text": "#1B1B1F", "accent": "#4C8DFF"},
                                 {"channels": "Channels"})
    srv = PhoneServer(0, TOKEN, lambda action, params: {"ok": True}, page, host="127.0.0.1")
    monkeypatch.setattr(srv, "client_ok", lambda address, own="": verdict["ok"])
    srv.start()
    try:
        assert _ask(srv.port, f"/{TOKEN}/")[0] == 200
        verdict["ok"] = False
        code, body = _ask(srv.port, f"/{TOKEN}/")
        assert code == 403 and body == b"forbidden"
        # A refused address is not counted as guessing the token.
        assert not srv.limiter.blocked("127.0.0.1")
    finally:
        srv.stop()
    for addr in ("10.0.0.8", "172.20.0.3", "192.168.50.2", "fd00::3"):
        assert PhoneServer.client_ok(addr), addr
    assert not PhoneServer.client_ok("93.184.216.34")


def test_the_check_line_key_of_each_reason_is_a_string():
    from dopeiptv.i18n import _STRINGS
    for why in home_net.REASONS:
        assert f"nerd_ck_ln_{why}" in _STRINGS
    for key in ("nerd_ck_ln_none", "nerd_ck_ln_list", "nerd_ck_ln_found", "nerd_ck_ln_nothing"):
        assert key in _STRINGS
    json.dumps(home_net.check_list("10.0.0.0/8").params)     # plain values for the line
