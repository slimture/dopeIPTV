"""Connections to the account (core/connections.py): the numbers an Xtream
account gives, which streams count, the app's own against the limit, and
what the provider counted besides them - only while its answer is fresh."""
from __future__ import annotations

from dopeiptv.core import connections as cn
from dopeiptv.core.connections import Ledger, Use


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_numbers_from_user_info():
    assert cn.numbers({"max_connections": "2", "active_cons": "1"}) == (2, 1)
    assert cn.numbers({"max_connections": 0, "active_cons": "0"}) == (None, 0)
    assert cn.numbers({"max_connections": "x"}) == (None, None)
    assert cn.numbers(None) == (None, None)
    assert cn.numbers({"max_connections": "3.0", "active_cons": -1}) == (3, None)


def test_what_counts_as_a_connection():
    assert cn.is_network("http://h.example.com/live/u/p/1.ts")
    assert cn.is_network("https://user:pw@h.example.com:8080/x")
    assert cn.is_network("rtmp://h.example.com/live")
    for local in ("", None, "/home/me/film.mkv", "file:///home/me/film.mkv",
                  "http://127.0.0.1:41234/live?t=abc", "http://localhost:8000/x",
                  "http://[::1]:9000/x", "C:\\Videos\\a.mkv"):
        assert not cn.is_network(local), local


def test_own_count_per_source():
    uses = [Use("player", "a"), Use("recording", "b"), Use("multiview", ""), Use("buffer", "a")]
    assert cn.own_count(uses, "a") == 3          # an unknown source counts
    assert cn.own_count(uses, "b") == 2
    assert cn.kinds_of(uses, "a") == ("player", "buffer", "multiview")
    assert cn.counted(["multiview", "player", "multiview"]) == [("player", 1), ("multiview", 2)]


def test_unknown_limit_says_nothing():
    led = Ledger()
    assert led.check("a", [Use("player", "a")] * 5) is None
    led.report("a", None, 3, own=0)
    assert led.check("a", [Use("player", "a")] * 5) is None


def test_over_the_limit_with_the_apps_own():
    led = Ledger()
    uses = [Use("player", "a")]
    assert led.check("a", uses, limit=2) is None
    v = led.check("a", uses, limit=1)
    assert v is not None and (v.limit, v.in_use, v.after, v.own, v.others) == (1, 1, 2, 1, 0)
    assert v.kinds == ("player",)
    # Another source's connections are not this account's.
    assert led.check("a", [Use("recording", "b")], limit=1) is None
    assert led.check("a", uses, adding=0, limit=1) is None


def test_another_device_while_the_answer_is_fresh():
    clock = Clock()
    led = Ledger(clock)
    # The provider counted 2 while the app held 1: one is another device's.
    led.report("a", 2, 2, own=1)
    assert led.limit("a") == 2 and led.others("a") == 1
    v = led.check("a", [Use("player", "a")])
    assert v is not None and v.others == 1 and v.after == 3
    clock.t += cn.FRESH_SECS + 1
    assert led.others("a") == 0
    assert led.check("a", [Use("player", "a")]) is None
    # The limit stands without a fresh count.
    assert led.check("a", [Use("player", "a")] * 2) is not None


def test_older_answers_do_not_replace_newer():
    clock = Clock()
    led = Ledger(clock)
    led.report("a", 2, 2, own=0, at=1000.0)
    led.report("a", 4, 0, own=0, at=990.0)
    assert led.limit("a") == 2 and led.others("a") == 2
    led.report("a", None, 0, own=0, at=1001.0)
    assert led.limit("a") == 2 and led.others("a") == 0     # a later answer without a limit
    led.forget("a")
    assert led.limit("a") is None


def test_one_provider_account_however_it_was_added():
    """The same server and user name is one account: other names, another
    category set, the scheme's own port written or not, the host's case, an
    M3U of the same panel; a portal by its MAC address."""
    a = {"id": "a", "server": "http://Panel.example.com", "username": "u"}
    assert cn.account_of(a) == "http://panel.example.com:80|u"
    for same in ({"server": "panel.example.com:80", "username": "u"},
                 {"server": "http://panel.example.com:80/", "username": " u "},
                 {"server": "http://panel.example.com/get.php?username=u&password=p&type=m3u",
                  "kind": "m3u"}):
        assert cn.account_of(same) == cn.account_of(a), same
    for other in ({"server": "http://panel.example.com", "username": "v"},
                  {"server": "https://panel.example.com", "username": "u"},
                  {"server": "http://panel.example.com:8080", "username": "u"},
                  {"server": "http://other.example.com", "username": "u"}):
        assert cn.account_of(other) != cn.account_of(a), other
    # Nothing to tell: no server, no user name (a box, a plain M3U).
    assert cn.account_of({"server": "http://box.local"}) == ""
    assert cn.account_of({"username": "u"}) == "" and cn.account_of(None) == ""
    assert cn.account_of({"server": "http://[fd00::5]:8080", "username": "u"}) == \
        "http://[fd00::5]:8080|u"
    assert cn.account_of({"server": "http://portal.example/c/", "mac": "00:1A:79:AA:BB:CC"}) == \
        "http://portal.example:80|mac=00:1a:79:aa:bb:cc"


def test_two_sources_of_one_account_share_its_limit():
    pls = [{"id": "a", "server": "http://p.example.com", "username": "u"},
           {"id": "box", "server": "http://box.local"},
           {"id": "b", "server": "http://P.example.com:80", "username": "u"},
           {"id": "c", "server": "http://p.example.com", "username": "w"}]
    acc = cn.accounts(pls)
    assert acc == {"a": "a", "box": "box", "b": "a", "c": "c"}
    uses = cn.on_accounts([Use("player", "a"), Use("recording", "b"), Use("cast", "c"),
                           Use("multiview", "")], acc)
    assert [(u.kind, u.source) for u in uses] == [
        ("player", "a"), ("recording", "a"), ("cast", "c"), ("multiview", "")]
    led = Ledger(Clock())
    led.report(acc["b"], 2, None, own=0)          # b's provider said two: a's too
    v = led.check(acc["b"], uses)
    assert v is not None and v.own == 3 and v.limit == 2   # a's player, b's recording, the cell
    assert v.kinds == ("player", "recording", "multiview")
    assert led.check(acc["c"], uses) is None       # another account: its own (unknown) limit
