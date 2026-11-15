"""Night mode for the sound: one labelled audio filter on the player's core.

The filter itself (well-behaved options an old ffmpeg knows too), that it
is put on and taken off by its own label - the music equaliser's entry and
anything else on the audio chain stay - that the player writes to mpv only
when the state changes (an ordinary play with night mode off touches
nothing), that a core made after the choice gets it at its first play, the
options menu's switch, the key, and the setting. With libmpv here, the same
on a real core.
"""
from __future__ import annotations

import inspect
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from dopeiptv.core import night_mode as nm
from dopeiptv.core import shortcut_keys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value


class FakeMpv:
    """mpv's labelled af list, as far as the commands go."""

    def __init__(self, old: bool = False, broken: bool = False):
        self.chain: list[tuple[str, str]] = []      # (label, graph)
        self.commands: list[tuple] = []
        self.old, self.broken = old, broken

    def command(self, name, op, value):
        self.commands.append((name, op, value))
        if self.broken:
            raise RuntimeError("mpv said no")
        assert name == "af"
        if op in ("add", "pre"):
            label, _, rest = value[1:].partition(":")
            for i, (lab, _g) in enumerate(self.chain):
                if lab == label:
                    self.chain[i] = (label, rest)
                    return
            if op == "add":
                self.chain.append((label, rest))
            else:
                self.chain.insert(0, (label, rest))
        elif op == "remove" and not self.old or op == "del" and self.old:
            self.chain = [(lab, g) for lab, g in self.chain if "@" + lab != value]
        else:
            raise RuntimeError(f"unknown af operation {op}")

    def __getitem__(self, key):
        assert key == "af"
        return [{"name": "lavfi", "label": lab, "params": {"graph": g}} for lab, g in self.chain]

    def labels(self):
        return [lab for lab, _g in self.chain]


def test_the_filter_is_a_gentle_leveller_every_ffmpeg_knows():
    assert nm.entry() == "@dopeiptv-night:lavfi=[dynaudnorm=f=250:g=15:p=0.5:m=8]"
    name, _, opts = nm.GRAPH.partition("=")
    assert name == "dynaudnorm"
    got = dict(kv.split("=") for kv in opts.split(":"))
    # Only options ffmpeg 4 had already; a quarter-second frame, a few
    # seconds of smoothing, peaks to -6 dBFS, at most +18 dB.
    assert set(got) <= {"f", "g", "p", "m", "r", "n", "c", "b", "s"}
    assert int(got["f"]) == 250 and int(got["g"]) % 2 == 1
    assert float(got["p"]) == 0.5 and float(got["m"]) == 8


def test_the_setting_is_off_unless_turned_on():
    s = _Settings()
    assert not nm.enabled(s) and not nm.enabled(None)
    nm.store(s, True)
    assert s[nm.KEY] == "true" and nm.enabled(s)
    nm.store(s, False)
    assert not nm.enabled(s)


def test_on_and_off_by_its_own_label_leaving_the_rest():
    m = FakeMpv()
    m.chain = [("", "volume=1"), ("dopeiptv-eq", "equalizer=f=31:g=3")]
    assert nm.apply(m, True) and m.labels() == ["", "dopeiptv-eq", "dopeiptv-night"]
    assert nm.has_filter(m)
    assert nm.apply(m, True) and m.labels().count("dopeiptv-night") == 1
    assert nm.apply(m, False) and m.labels() == ["", "dopeiptv-eq"]
    assert not nm.has_filter(m)
    # Taking off what is not there is no error.
    assert nm.apply(m, False) and m.labels() == ["", "dopeiptv-eq"]


def test_an_old_mpv_is_asked_with_del_and_a_refusal_is_said():
    m = FakeMpv(old=True)
    nm.apply(m, True)
    assert nm.apply(m, False) and m.labels() == []
    assert [c[1] for c in m.commands] == ["add", "remove", "del"]
    assert not nm.apply(FakeMpv(broken=True), True)
    assert not nm.apply(FakeMpv(broken=True), False)


def _player(core, settings=None, on=False):
    """The player's night mode as its methods see it, without a GL surface."""
    from dopeiptv.media.embedded import EmbeddedPlayer
    told = []
    p = SimpleNamespace(video=SimpleNamespace(mpv=core), _settings=settings,
                        _night_on=on, _night_core=None, _night_now=False,
                        night_mode_changed=SimpleNamespace(emit=told.append),
                        EQ_LABEL=EmbeddedPlayer.EQ_LABEL, EQ_BANDS=EmbeddedPlayer.EQ_BANDS)
    for name in ("set_night_mode", "toggle_night_mode", "_sync_night", "set_equaliser"):
        setattr(p, name, getattr(EmbeddedPlayer, name).__get__(p))
    return p, told


def test_the_player_writes_to_mpv_only_when_night_mode_changes():
    pytest.importorskip("PyQt6.QtWidgets")
    core = FakeMpv()
    p, _told = _player(core)
    p._sync_night()                       # every play(): off, never on
    assert core.commands == []            # the core is not touched at all
    p.set_night_mode(True)
    assert core.labels() == ["dopeiptv-night"] and len(core.commands) == 1
    for _play in range(3):                # channel changes, the rewind buffer
        p._sync_night()
    assert len(core.commands) == 1        # mpv keeps it from stream to stream
    p.set_night_mode(False)
    assert core.labels() == [] and len(core.commands) == 2
    p._sync_night()
    assert len(core.commands) == 2


def test_a_core_made_after_the_choice_gets_it_at_its_first_play():
    pytest.importorskip("PyQt6.QtWidgets")
    p, _told = _player(None, on=True)
    p.set_night_mode(True)                # no core yet (its GL surface is lazy)
    core = FakeMpv()
    p.video.mpv = core
    p._sync_night()
    assert core.labels() == ["dopeiptv-night"]
    other = FakeMpv()                     # a core of its own, as after a rebuild
    p.video.mpv = other
    p._sync_night()
    assert other.labels() == ["dopeiptv-night"]


def test_the_menu_and_key_toggle_remember_and_tell():
    pytest.importorskip("PyQt6.QtWidgets")
    s = _Settings()
    core = FakeMpv()
    p, told = _player(core, s)
    assert p.toggle_night_mode() is True
    assert s[nm.KEY] == "true" and core.labels() == ["dopeiptv-night"] and told == [True]
    assert p.toggle_night_mode() is False
    assert s[nm.KEY] == "false" and core.labels() == [] and told == [True, False]


def test_the_equaliser_and_night_mode_keep_out_of_each_others_way():
    pytest.importorskip("PyQt6.QtWidgets")
    core = FakeMpv()
    p, _told = _player(core)
    p.set_night_mode(True)
    p.set_equaliser([6, 5, 4, 2, 0, 0, 0, 0, 0, 0], True)
    assert core.labels() == ["dopeiptv-eq", "dopeiptv-night"]      # equaliser first
    p.set_equaliser([3, 5, 4, 2, 0, 0, 0, 0, 0, 0], True)
    assert core.labels() == ["dopeiptv-eq", "dopeiptv-night"]
    p.set_equaliser([0] * 10, False)                                 # its off: only its own
    assert core.labels() == ["dopeiptv-night"]
    p.set_night_mode(False)
    assert core.labels() == []


def test_every_play_puts_it_on_its_core_before_the_stream_opens():
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.media.embedded import EmbeddedPlayer
    src = inspect.getsource(EmbeddedPlayer.play)
    assert src.index("self._sync_night()") < src.index("m.play(source or url)")
    # The video side is never part of it.
    night = inspect.getsource(EmbeddedPlayer._sync_night) + inspect.getsource(nm)
    for prop in ("lavfi-complex", '"vf"', '"vid"', '"hwdec"', '"video'):
        assert prop not in night, prop


def test_its_key_is_n_and_rebindable():
    assert shortcut_keys.DEFAULTS["night_mode"] == "N"
    from dopeiptv.i18n import _STRINGS
    assert _STRINGS[shortcut_keys.LABELS["night_mode"]]["en"]
    over, lost = shortcut_keys.assign({}, "night_mode", "Ctrl+Alt+N")
    assert shortcut_keys.effective(over)["night_mode"] == "Ctrl+Alt+N" and not lost


_CHILD = r"""
import os, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QMenu
app = QApplication.instance() or QApplication([])
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
from dopeiptv.core import night_mode as nm
from dopeiptv.i18n import tr
from dopeiptv.media.embedded import EmbeddedPlayer
p = EmbeddedPlayer(settings=s)
p.night_key = lambda: "N"
menu = QMenu()
p.populate_options_menu(menu)
acts = [a for a in menu.actions() if a.text().split("\t")[0] == tr("night_mode")]
assert len(acts) == 1, [a.text() for a in menu.actions()]
act = acts[0]
assert act.isCheckable() and not act.isChecked() and act.text().endswith("\tN")
told = []
p.night_mode_changed.connect(told.append)
act.trigger()
assert s.value(nm.KEY) == "true" and p.night_mode_on() and told == [True]
menu2 = QMenu()
p.populate_options_menu(menu2)
assert [a for a in menu2.actions() if a.text().startswith(tr("night_mode"))][0].isChecked()
# A player made with it on starts with it on.
assert EmbeddedPlayer(settings=s).night_mode_on()
print("NIGHT_OK", flush=True)
"""


def test_the_options_menu_has_the_switch():
    pytest.importorskip("PyQt6.QtWidgets")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    assert "NIGHT_OK" in out.stdout, out.stdout + out.stderr[-3000:]


def _real_mpv():
    try:
        import locale

        import mpv
        locale.setlocale(locale.LC_NUMERIC, "C")
        return mpv.MPV(vo="null", ao="null", terminal=False, idle=True)
    except Exception as e:            # no libmpv on this machine
        pytest.skip(f"libmpv not here: {e}")


def test_a_real_core_takes_and_drops_it_by_its_label():
    m = _real_mpv()
    try:
        m.command("af", "pre", "@dopeiptv-eq:lavfi=[equalizer=f=31:width_type=o:width=1:g=3.0]")
        assert nm.apply(m, True) and nm.has_filter(m)
        assert [f.get("label") for f in m["af"]] == ["dopeiptv-eq", "dopeiptv-night"]
        assert m["af"][1]["params"]["graph"] == nm.GRAPH
        assert nm.apply(m, False) and not nm.has_filter(m)
        assert [f.get("label") for f in m["af"]] == ["dopeiptv-eq"]
    finally:
        m.terminate()
