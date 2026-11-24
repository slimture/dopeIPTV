"""A PIN as it is kept and as it is tried (core/pins.py).

Four digits are 10,000 PINs. Kept as one SHA-256 of salt + PIN (up to
1.6.8), a copy of the settings file gave the PIN away at once; kept by
PBKDF2 now, in a versioned form, an old value still checked the old way
and written anew on the next right entry - nobody's PIN stops working.
And in the window a wrong PIN could be tried as fast as it could be typed:
wrong ones are counted per PIN, each making the next try wait a little
longer, a minute at most - a slow-down, never a lock.
"""
from __future__ import annotations

import hashlib
import json
import time

import pytest

pytest.importorskip("PyQt6.QtCore")
from PyQt6.QtCore import QSettings  # noqa: E402

from dopeiptv.core import pins, profiles  # noqa: E402
from dopeiptv.core.profiles import MAIN_ID, ProfileStore  # noqa: E402
from dopeiptv.core.stores import ParentalControl  # noqa: E402


@pytest.fixture(autouse=True)
def _no_profile():
    profiles.activate("")
    yield
    profiles.activate("")


@pytest.fixture
def quick(monkeypatch):
    """Fewer rounds, for tests that try many PINs."""
    monkeypatch.setattr(pins, "ROUNDS", 1000)


def _ini(tmp_path, name="s.ini"):
    return QSettings(str(tmp_path / name), QSettings.Format.IniFormat)


def _old(salt: str, pin: str) -> str:
    """A PIN as 1.6.8 and before kept it."""
    return hashlib.sha256((salt + pin).encode()).hexdigest()


# -- kept ---------------------------------------------------------------------------

def test_a_pin_is_kept_by_pbkdf2_with_a_salt_of_its_own():
    assert pins.ROUNDS >= 600_000
    salt, stored = pins.make("2468")
    scheme, rounds, key = stored.split("$")
    assert scheme == "pbkdf2-sha256" and int(rounds) == pins.ROUNDS
    assert key == hashlib.pbkdf2_hmac("sha256", b"2468", salt.encode(), pins.ROUNDS).hex()
    assert "2468" not in (salt, stored) and len(salt) == 32
    assert pins.check(stored, salt, "2468") and not pins.check(stored, salt, "2469")
    assert not pins.outdated(stored)
    other_salt, other = pins.make("2468")
    assert other_salt != salt and other != stored           # the same PIN, never the same value


def test_a_pin_kept_the_old_way_is_still_taken(quick):
    stored = _old("abc123", "2468")
    assert pins.check(stored, "abc123", "2468") and not pins.check(stored, "abc123", "2467")
    assert pins.outdated(stored)
    # Fewer rounds than a PIN set now: outdated too.
    assert pins.outdated(pins.hashed("s", "2468", 10)) and pins.check(pins.hashed("s", "2468", 10), "s", "2468")
    assert not pins.outdated("") and not pins.outdated(pins.hashed("s", "2468"))


@pytest.mark.parametrize("stored", [
    "", "pbkdf2-sha256$", "pbkdf2-sha256$x$00", "pbkdf2-sha256$1000$", "pbkdf2-sha256$0$00",
    "pbkdf2-sha256$-5$00", "pbkdf2-sha256$1000$00$more", "pbkdf2-sha256$99999999999$00",
    "not a value", "ünïcödé", None, 5,
])
def test_a_value_that_is_none_of_ours_takes_no_pin(stored):
    started = time.monotonic()
    for pin in ("", "0000", "2468"):
        assert pins.check(stored, "salt", pin) is False
    assert time.monotonic() - started < 2.0                 # no rounds a file asked for


def test_a_profile_s_old_pin_works_and_is_kept_the_new_way_after(tmp_path, quick):
    s = _ini(tmp_path)
    old = _old("0a1b", "2468")
    s.setValue(profiles.K_LIST, json.dumps([
        {"id": MAIN_ID, "name": "Me", "pin_hash": old, "pin_salt": "0a1b"},
        {"id": "kid", "name": "Lisa", "kids": True}]))
    store = ProfileStore(s)
    me = store.get(MAIN_ID)
    assert me.has_pin and me.verify("2468") and not me.verify("1357")
    assert not store.verify(MAIN_ID, "1357")
    assert store.get(MAIN_ID).pin_hash == old               # a wrong PIN changes nothing
    assert store.verify(MAIN_ID, "2468")
    kept = json.loads(s.value(profiles.K_LIST))[0]
    assert kept["pin_hash"].startswith("pbkdf2-sha256$") and kept["pin_salt"] != "0a1b"
    again = ProfileStore(s)                                  # the next start
    assert again.verify(MAIN_ID, "2468") and not again.verify(MAIN_ID, "1357")
    assert again.grown_up_pin("2468") and again.grown_up_match("2468").id == MAIN_ID
    # A PIN set now is the new way from the start.
    again.set_pin("kid", "1111")
    assert again.get("kid").pin_hash.startswith("pbkdf2-sha256$")
    assert again.grown_up_match("1111") is None             # a kids profile's opens no Settings


def test_the_parental_pin_s_old_value_works_and_is_kept_the_new_way_after(tmp_path, quick):
    s = _ini(tmp_path)
    s.setValue("parental_salt", "feed")
    s.setValue("parental_pin_hash", _old("feed", "4321"))
    p = ParentalControl(s)
    p.session_unlocked = True
    assert p.has_pin() and not p.verify("1234")
    assert s.value("parental_pin_hash") == _old("feed", "4321")
    assert p.verify("4321")
    assert str(s.value("parental_pin_hash")).startswith("pbkdf2-sha256$")
    assert s.value("parental_salt") != "feed"
    assert p.session_unlocked is True                       # checking is not locking
    assert ParentalControl(s).verify("4321") and not ParentalControl(s).verify("1234")
    p.set_pin("a longer one")
    assert str(s.value("parental_pin_hash")).startswith("pbkdf2-sha256$")
    assert p.verify("a longer one") and not p.verify("4321") and p.session_unlocked is False
    p.clear_pin()
    assert not p.has_pin() and not p.verify("a longer one") and not p.verify("")


# -- tried ----------------------------------------------------------------------------

class _Clock:
    def __init__(self, at=1_700_000_000.0):
        self.at = at

    def __call__(self):
        return self.at


def test_wrong_pins_make_the_next_try_wait_a_little_longer_each_time():
    clock = _Clock()
    t = pins.Tries(None, clock)
    assert t.wait("parental") == 0
    waits = []
    for _ in range(9):
        waits.append(t.wrong("parental"))
        clock.at += 100                                     # waited it out
        assert t.wait("parental") == 0
    assert waits == [0, 0, 5, 10, 20, 40, 60, 60, 60]       # two slips free; never past a minute
    assert max(pins.Tries.WAITS) <= 60
    # 10,000 PINs at a minute each: about a week of nothing else.
    assert 10_000 * pins.Tries.WAITS[-1] > 6 * 86400


def test_a_wait_counts_down_and_is_over_when_it_says():
    clock = _Clock()
    t = pins.Tries(None, clock)
    for _ in range(3):
        t.wrong("profile/me")
    assert t.wait("profile/me") == 5
    clock.at += 3.5
    assert t.wait("profile/me") == pytest.approx(1.5)
    clock.at += 1.5
    assert t.wait("profile/me") == 0


def test_the_right_pin_forgets_its_own_wrong_tries_and_no_other_s():
    clock = _Clock()
    t = pins.Tries(None, clock)
    # A profile's question takes the parental PIN too: a wrong try is one at each.
    for _ in range(4):
        t.wrong("profile/kid", "parental")
    assert t.wait("profile/kid") == t.wait("parental") == 10
    t.right("profile/kid")                                  # the kid knows the kid's own
    assert t.wait("profile/kid") == 0 and t.count("profile/kid") == 0
    assert t.wait("parental") == 10 and t.count("parental") == 4
    assert t.wait("profile/kid", "parental") == 10          # the question still waits
    t.right("parental")
    assert t.wait("profile/kid", "parental") == 0


def test_closing_the_app_does_not_start_the_count_over(tmp_path):
    s = _ini(tmp_path)
    clock = _Clock()
    t = pins.Tries(s, clock)
    for _ in range(5):
        t.wrong("profile/me")
    assert t.wait("profile/me") == 20
    clock.at += 2
    again = pins.Tries(s, clock)                            # the next start
    assert again.count("profile/me") == 5 and again.wait("profile/me") == 18
    again.right("profile/me")
    assert pins.Tries(s, clock).count("profile/me") == 0 and not s.contains(pins.Tries.KEY)


def test_wrong_tries_are_forgotten_after_a_while_without_one():
    clock = _Clock()
    t = pins.Tries(None, clock)
    for _ in range(7):
        t.wrong("parental")
    clock.at += pins.Tries.FORGET_S - 1
    assert t.count("parental") == 7
    clock.at += 1
    assert t.count("parental") == 0 and t.wait("parental") == 0
    assert t.wrong("parental") == 0                         # a slip again


def test_never_a_lock_whatever_the_clock_or_the_file_says(tmp_path):
    s = _ini(tmp_path)
    clock = _Clock()
    t = pins.Tries(s, clock)
    for _ in range(20):
        t.wrong("parental")
    clock.at -= 10 * 365 * 86400                            # the clock set back ten years
    again = pins.Tries(s, clock)
    assert 0 < again.wait("parental") <= 60
    clock.at += 60
    assert again.wait("parental") == 0
    # A file saying a million wrong tries a year from now: a minute at most.
    s.setValue(pins.Tries.KEY, json.dumps({"parental": [10 ** 6, clock.at + 365 * 86400]}))
    far = pins.Tries(s, clock)
    assert 0 < far.wait("parental") <= 60
    clock.at += 60
    assert far.wait("parental") == 0
    for junk in ("{", "[1, 2]", json.dumps({"parental": "x"}), json.dumps({"parental": [None]})):
        s.setValue(pins.Tries.KEY, junk)
        assert pins.Tries(s, clock).wait("parental") == 0


# -- in the window ----------------------------------------------------------------------

def _window(tmp_path, monkeypatch, answers):
    """A window's PIN questions with *answers* typed in turn; what it said."""
    from dopeiptv.ui import mw_context, mw_profiles, profiles_ui
    s = _ini(tmp_path)
    store = ProfileStore(s)
    store.add("Me", pid=MAIN_ID)
    kid = store.add("Lisa")
    store.set_pin(MAIN_ID, "2468")
    store.set_pin(kid.id, "1111")
    clock = _Clock()
    said: list[str] = []
    asked: list[str] = []
    typed = list(answers)

    def ask(_parent, _title, text, longest=8):
        asked.append(text)
        return typed.pop(0)
    monkeypatch.setattr(profiles_ui, "ask_pin", ask)
    monkeypatch.setattr(mw_context.message_box, "get_text",
                        lambda *a, **k: (asked.append("parental") or typed.pop(0), True))
    for kind in ("information", "warning"):
        monkeypatch.setattr(mw_profiles.message_box, kind, lambda _p, _t, text, *a: said.append(text))

    class W(mw_profiles._ProfilesMixin, mw_context._ContextMenuMixin):
        def __init__(self):
            self.settings = s
            self.profiles = store
            self.parental = ParentalControl(s)
            self._profile_must_pick = False
            self._pin_tries_kept = pins.Tries(s, clock)

        def _profile_kids(self):
            return False
    return W(), store, kid, clock, said, asked


def test_a_wrong_pin_in_the_window_slows_the_next_try(tmp_path, monkeypatch, quick):
    from dopeiptv.i18n import tr
    w, store, kid, clock, said, asked = _window(
        tmp_path, monkeypatch, ["0000", "0001", "0002", "2468", "2468"])
    me = store.get(MAIN_ID)
    assert not w._profile_unlock(me) and not w._profile_unlock(me)
    assert said == [tr("msg_wrong_pin")] * 2                # two slips: nothing more said
    assert not w._profile_unlock(me)
    assert said[-1] == tr("msg_wrong_pin") + "\n\n" + tr("pin_wait", n=5)
    # Within the wait the question is not asked at all - not for the right PIN either.
    assert not w._profile_unlock(me)
    assert len(asked) == 3 and said[-1] == tr("pin_wait", n=5)
    clock.at += 4.2
    assert not w._profile_unlock(me) and said[-1] == tr("pin_wait", n=1)
    clock.at += 1
    assert w._profile_unlock(me) and len(asked) == 4        # waited: the right PIN opens it
    assert w._pin_tries().count(f"profile/{MAIN_ID}") == 0
    assert w._profile_unlock(me)                            # and no wait after it


def test_nothing_typed_is_no_guess(tmp_path, monkeypatch, quick):
    w, store, kid, clock, said, asked = _window(tmp_path, monkeypatch, ["", "", "", "", None])
    me = store.get(MAIN_ID)
    for _ in range(4):
        assert not w._profile_unlock(me)
    assert not w._profile_unlock(me)                        # cancelled
    assert w._pin_tries().count(f"profile/{MAIN_ID}") == 0 and len(asked) == 5


def test_knowing_one_pin_is_no_way_to_more_tries_at_another(tmp_path, monkeypatch, quick):
    """A profile's question takes the parental PIN too. Guessing at it
    there, with the profile's own PIN typed now and then to start over,
    was a way round the count."""
    w, store, kid, clock, said, asked = _window(
        tmp_path, monkeypatch, ["0000", "0001", "0002", "1111", "9999", "1111", "4321"])
    w.parental.set_pin("4321")
    for _ in range(3):
        assert not w._profile_unlock(kid)                   # three guesses at the parental PIN
    clock.at += 5
    assert w._profile_unlock(kid)                           # the kid's own PIN: in
    assert w._pin_tries().count(f"profile/{kid.id}") == 0
    assert w._pin_tries().count("parental") == 3            # still counted
    assert not w._profile_unlock(kid)                       # a fourth guess: 10 s now, not none
    assert w._pin_tries().wait("parental") == 10
    assert not w._profile_unlock(kid) and len(asked) == 5   # not even asked meanwhile
    clock.at += 10
    assert w._profile_unlock(kid)
    # The parental PIN's own question shares that count, and the PIN clears it.
    clock.at += 1
    assert w._request_unlock() and w.parental.session_unlocked
    assert w._pin_tries().count("parental") == 0


def test_the_parental_pin_s_own_question_slows_down_too(tmp_path, monkeypatch, quick):
    from dopeiptv.i18n import tr
    w, store, kid, clock, said, asked = _window(
        tmp_path, monkeypatch, ["1", "2", "3", "4321"])
    w.parental.set_pin("4321")
    for _ in range(3):
        assert not w._request_unlock()
    assert said[-1] == tr("msg_wrong_pin") + "\n\n" + tr("pin_wait", n=5)
    assert not w._request_unlock() and len(asked) == 3 and said[-1] == tr("pin_wait", n=5)
    clock.at += 5
    assert w._request_unlock() and w.parental.session_unlocked


def test_settings_from_a_kids_profile_counts_every_grown_up_s_pin(tmp_path, monkeypatch, quick):
    w, store, kid, clock, said, asked = _window(
        tmp_path, monkeypatch, ["0000", "0001", "0002", "2468"])
    store.update(kid.id, kids=True)
    store.reload()
    w._profile_kids = lambda: True
    for _ in range(3):
        assert not w._kids_settings_unlock()
    assert w._pin_tries().count(f"profile/{MAIN_ID}") == 3
    assert w._pin_tries().count(f"profile/{kid.id}") == 0   # a kids profile's PIN opens nothing here
    assert not w._kids_settings_unlock() and len(asked) == 3
    clock.at += 5
    assert w._kids_settings_unlock()
    assert w._pin_tries().count(f"profile/{MAIN_ID}") == 0
