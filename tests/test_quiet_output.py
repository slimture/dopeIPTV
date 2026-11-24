"""A muted player opens no sound output of its own (media/embedded.py
_quiet_output): mpv's null output while muted - a stream to the system's
sound server for every channel, even muted, made it renegotiate and
another program's sound crackled at each change - and the system's own
output again, or the one the person's mpv options named, on unmute."""
from __future__ import annotations

from types import SimpleNamespace

from dopeiptv.media import embedded
from dopeiptv.media.embedded import QUIET_AO, EmbeddedPlayer, ao_text


class FakeMpv:
    def __init__(self, ao=None, path="http://x/1.ts", aid=1):
        self.opts = {"ao": [] if ao is None else ao}
        self.path = path
        self.aid = aid
        self.commands: list[tuple] = []
        self.drop_track = False

    def __getitem__(self, key):
        return self.opts[key]

    def __setitem__(self, key, value):
        self.opts[key] = value

    def command(self, *args):
        self.commands.append(args)
        if args[0] == "ao-reload" and self.drop_track:
            self.aid = False


def quiet(player, m, reload=True):
    EmbeddedPlayer._quiet_output(player, m, reload)


def test_ao_text_names_the_outputs_and_never_the_quiet_one():
    assert ao_text([]) == ""
    assert ao_text([{"name": "pipewire"}, {"name": "pulse"}]) == "pipewire,pulse"
    assert ao_text("coreaudio") == "coreaudio"
    assert ao_text(QUIET_AO) == "" and ao_text([{"name": "null"}]) == ""
    assert ao_text(None) == "" and ao_text(object()) == ""


def test_muted_before_a_load_no_output_and_no_reload():
    player = SimpleNamespace(_muted=True)
    m = FakeMpv(path=None)
    quiet(player, m, reload=False)
    assert m.opts["ao"] == QUIET_AO and m.commands == []
    quiet(player, m, reload=False)          # nothing to do the second time
    assert m.commands == []


def test_muting_what_plays_only_mutes_it():
    """Muting a video swapped its sound output and reloaded it; the sound
    restarted and the picture, kept in time by it, stalled for a moment.
    Muting what plays leaves the output it has; unmuting it then has
    nothing to undo."""
    player = SimpleNamespace(_muted=True)
    m = FakeMpv()
    quiet(player, m)
    assert m.opts["ao"] == [] and m.commands == []            # no swap, no reload
    player._muted = False
    quiet(player, m)
    assert m.opts["ao"] == [] and m.commands == []
    # Still muted at the next stream: it starts with no output of its own.
    player._muted = True
    quiet(player, m, reload=False)
    assert m.opts["ao"] == QUIET_AO and m.commands == []


def test_unmuting_a_stream_that_started_muted_reloads_its_output():
    player = SimpleNamespace(_muted=True)
    m = FakeMpv(path=None)
    quiet(player, m, reload=False)                             # before its load
    m.path = "http://x/1.ts"
    player._muted = False
    quiet(player, m)
    assert m.opts["ao"] == "" and m.commands == [("ao-reload",)]


def test_the_persons_own_output_comes_back():
    player = SimpleNamespace(_muted=True)
    m = FakeMpv(ao=[{"name": "pulse"}], path=None)
    quiet(player, m, reload=False)
    m.path = "http://x/1.ts"
    player._muted = False
    quiet(player, m)
    assert m.opts["ao"] == "pulse"


def test_a_device_that_dropped_the_track_gets_the_picked_one_back():
    player = SimpleNamespace(_muted=True)
    m = FakeMpv(aid=2, path=None)
    quiet(player, m, reload=False)
    m.path = "http://x/1.ts"
    m.drop_track = True
    player._muted = False
    quiet(player, m)
    assert m.aid == 2


def test_a_broken_core_is_only_logged(monkeypatch):
    class Broken(FakeMpv):
        def __setitem__(self, key, value):
            raise RuntimeError("gone")
    said = []
    monkeypatch.setattr(embedded.log, "debug", lambda *a, **k: said.append(a))
    quiet(SimpleNamespace(_muted=True), Broken(path=None), reload=False)
    assert said
