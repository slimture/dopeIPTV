"""A kids profile is a boundary, on the real window in a child process
(offscreen): whatever asks, it plays nothing its lists would not show -
not a file on this computer, a recording or a web address, nor a row the
app cannot place in a category it knows; it is made only with a PIN a
grown-up keeps, left for a grown-up's profile and opens Settings only
with that PIN; the phone remote lists neither local files nor recordings
for it. A kids profile made before a grown-up's PIN was needed is never
locked in."""
from __future__ import annotations

from tests.test_profiles_reach import _run

_BOUNDARY = r"""
from dopeiptv.ui import profiles_ui
from dopeiptv.ui.profiles_ui import ProfileDialog

asked = []
profiles_ui.ask_pin = lambda parent, title, text, longest=8: asked.append(text) or answer[0]
answer = [None]
started = []


def tried(kind, item, url="http://example.com/x.ts"):
    before = len(started)
    w._start_playback(url, "T", None, "k-%d" % len(started), kind, item=item)
    return len(started) > before


# What would start a stream is noted, never run; what is said, kept.
w._guard_stream_switch = lambda url, title: started.append(url) and False
toasts = []
w._show_toast = lambda text, *a, **k: toasts.append(text)
w._pin_must_wait = lambda *a, **k: False

# A kids profile made before 1.6.9, with no grown-up's PIN anywhere.
assert not w.profiles.grown_up_pins() and not w.parental.has_pin()
assert w.switch_profile(kid.id)
w.switch_mode("live")
assert until(lambda: w.cat_list.count() >= 1)
pump(20)
w._kids_note_cats("live", Client().live_categories())
# One door for every playback: none of these, however asked.
for kind in ("local", "recording", "web"):
    assert not tried(kind, {"name": "x", "path": "/home/me/x.mkv"}), kind
assert not tried("live", {"name": "News Late", "stream_id": 2, "category_id": "1", "is_adult": "1"})
assert not tried("live", {"name": "Blue Room", "stream_id": 3, "category_id": "3"})
assert not tried("live", {"name": "Somewhere", "stream_id": 9}), "no category: not played"
assert not tried("live", {"name": "Elsewhere", "stream_id": 9, "category_id": "77"}), "unknown"
assert not tried("live", None)
assert tried("live", {"name": "News One", "stream_id": 1, "category_id": "1"})
assert toasts.count(tr("profile_kids_not_here")) == 8, toasts
# The phone remote lists no files and no recordings for it.
sections = w._phone_sections()
assert "local" not in sections and "rec" not in sections, sections
assert "all" in sections
# Left freely while no grown-up's PIN exists anywhere: never locked in.
me = [p for p in w.profiles.profiles() if not p.kids][0]
assert w._kids_leave_unlock(me) and asked == []
# Settings stay closed, and say how a grown-up sets one.
assert not w._kids_settings_unlock()

# -- a grown-up's PIN set: the boundary holds --------------------------------------
w.parental.set_pin("4321")
answer[0] = None
assert not w._kids_leave_unlock(me) and asked[-1] == tr("profile_kids_leave")
answer[0] = "1111"
assert not w._kids_leave_unlock(me), "a wrong PIN leaves nothing"
answer[0] = "4321"
assert w._kids_leave_unlock(me), "the grown-up's PIN lets it go"
assert w._kids_settings_unlock()
# Another kids profile is no grown-up's: left for it without a PIN.
other = w.profiles.add("Max", colour="blue", kids=True)
n = len(asked)
assert w._kids_leave_unlock(other) and len(asked) == n

# -- made only with a grown-up's PIN ------------------------------------------------
w.parental.clear_pin()
assert not w.parental.has_pin()
dlg = ProfileDialog(w, w.profiles, parental=w.parental)
dlg.name.setText("Nora")
dlg.kids.setChecked(True)
dlg._save()
assert dlg.error.text() == tr("profile_err_kids_pin")
assert not any(p.name == "Nora" for p in w.profiles.profiles())
w.profiles.set_pin(me.id, "2468")
dlg = ProfileDialog(w, w.profiles, parental=w.parental)
dlg.name.setText("Nora")
dlg.kids.setChecked(True)
dlg._save()
assert any(p.name == "Nora" and p.kids for p in w.profiles.profiles()), dlg.error.text()
print("BOUNDARY_OK")
"""


def test_a_kids_profile_is_a_boundary():
    out = _run(_BOUNDARY)
    assert "BOUNDARY_OK" in out, out[-5000:]
