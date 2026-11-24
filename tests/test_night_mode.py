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
    """mpv's labelled af list, as far as the commands go - and, with a
    sound track, mpv dropping that track when a filter its ffmpeg cannot
    build (*refused*) is on the chain as the sound starts."""

    def __init__(self, old: bool = False, broken: bool = False, hides: bool = False,
                 sound: bool | None = None, refused: bool = False):
        self.chain: list[tuple[str, str]] = []      # (label, graph)
        self.commands: list[tuple] = []
        self.old, self.broken, self.hides, self.refused = old, broken, hides, refused
        self.track_list = ([] if sound is None
                           else [{"type": "video", "id": 1, "selected": True},
                                 {"type": "audio", "id": 1, "selected": sound}])
        self.aid_set: list = []

    @property
    def aid(self):
        return next((t["id"] for t in self.track_list
                     if t["type"] == "audio" and t["selected"]), False)

    @aid.setter
    def aid(self, value):
        self.aid_set.append(value)
        for t in self.track_list:
            if t["type"] == "audio":
                t["selected"] = not (self.refused and "dopeiptv-night" in self.labels())

    def command(self, name, op, value):
        self.commands.append((name, op, value))
        if self.broken:
            raise RuntimeError("mpv said no")
        assert name == "af"
        if op in ("add", "pre"):
            if self.hides:
                return
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


def test_the_filter_only_turns_down_with_options_every_ffmpeg_knows():
    assert nm.entry() == (
        "@dopeiptv-night:lavfi=["
        "acompressor=threshold=0.133:ratio=4:attack=2000:release=9000:knee=1,"
        "acompressor=threshold=0.25:ratio=8:attack=50:release=1000:knee=1,"
        "acompressor=threshold=0.35:ratio=20:attack=5:release=400:knee=1]")
    stages = []
    for part in nm.GRAPH.split(","):
        name, _, opts = part.partition("=")
        assert name == "acompressor"
        stages.append({k: float(v) for k, v in (kv.split("=") for kv in opts.split(":"))})
        # Only options acompressor has had since ffmpeg 3.0, as plain
        # numbers (no "dB" to read), within their ranges there - and no
        # makeup gain or upward mode: it never turns anything up.
        assert set(stages[-1]) == {"threshold", "ratio", "attack", "release", "knee"}
    for s in stages:
        assert 0.000976563 <= s["threshold"] <= 1 and 1 <= s["ratio"] <= 20
        assert 0.01 <= s["attack"] <= 2000 and 0.01 <= s["release"] <= 9000
        assert s["knee"] == 1                         # below the threshold: nothing
    # Slow level, then the scene, then the bang: each quicker and higher.
    assert [s["threshold"] for s in stages] == sorted(s["threshold"] for s in stages)
    assert [s["attack"] for s in stages] == sorted((s["attack"] for s in stages), reverse=True)


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


def test_it_counts_as_on_only_once_the_core_carries_it():
    # A core that took the command and shows no entry did not take it.
    m = FakeMpv(hides=True)
    assert not nm.apply(m, True) and not nm.has_filter(m)
    assert nm.apply(m, False)


def test_a_sound_that_did_not_start_through_the_filter_comes_back_without_it():
    # A core given the filter before any stream finds out only as the sound
    # starts that its ffmpeg cannot build it: mpv drops the sound track.
    m = FakeMpv(sound=False, refused=True)
    nm.apply(m, True)
    assert nm.sound_plays(m) is False
    assert nm.rescue_sound(m) is True
    assert nm.sound_plays(m) is True and not nm.has_filter(m)
    assert m.aid_set == [1, 1]               # with the filter, then without
    # Not the filter's doing (a stream switch's race): it stays on.
    m = FakeMpv(sound=False)
    nm.apply(m, True)
    assert nm.rescue_sound(m) is False and nm.sound_plays(m) and nm.has_filter(m)
    # A sound that plays through it, a stream without sound, no filter at
    # all: nothing is done.
    playing = FakeMpv(sound=True)
    nm.apply(playing, True)
    for m in (playing, FakeMpv(sound=None), FakeMpv(sound=False, refused=True)):
        assert nm.rescue_sound(m) is False and m.aid_set == []
    # A core that cannot say, or refuses the removal too: never raises.
    assert nm.sound_plays(SimpleNamespace()) is None
    m = FakeMpv(sound=False, refused=True)
    nm.apply(m, True)
    m.broken = True
    assert nm.rescue_sound(m) is False


def _player(core, settings=None, on=False):
    """The player's night mode as its methods see it, without a GL surface."""
    from dopeiptv.media.embedded import EmbeddedPlayer
    told, refused = [], []
    p = SimpleNamespace(video=SimpleNamespace(mpv=core), _settings=settings,
                        _night_on=on, _night_core=None, _night_now=False,
                        _night_refused=False, current_url="http://h/live/1.ts",
                        night_mode_changed=SimpleNamespace(emit=told.append),
                        night_mode_refused=SimpleNamespace(emit=lambda: refused.append(1)),
                        EQ_LABEL=EmbeddedPlayer.EQ_LABEL, EQ_BANDS=EmbeddedPlayer.EQ_BANDS)
    for name in ("set_night_mode", "toggle_night_mode", "_sync_night", "set_equaliser",
                 "night_mode_failed", "_night_on_event"):
        setattr(p, name, getattr(EmbeddedPlayer, name).__get__(p))
    p.refused = refused
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
    # Settings switches it through the same way (it stores the key itself).
    p.set_night_mode(True)
    assert told == [True, False, True] and s[nm.KEY] == "false"


def test_a_core_that_refuses_it_is_said_and_not_asked_by_every_play():
    pytest.importorskip("PyQt6.QtWidgets")
    core = FakeMpv(broken=True)               # a playing core refuses the graph
    p, told = _player(core)
    p.set_night_mode(True)
    assert told == [True] and p.night_mode_failed()
    asked = len(core.commands)
    for _play in range(3):
        p._sync_night()
    assert len(core.commands) == asked        # not again with every stream
    p.set_night_mode(False)
    assert told == [True, False] and not p.night_mode_failed()
    assert len(core.commands) == asked        # nothing to take off
    p.set_night_mode(True)                    # switched again: asked again
    assert len(core.commands) > asked and p.night_mode_failed()
    other = FakeMpv()                         # a new core is asked afresh
    p.video.mpv = other
    p._sync_night()
    assert other.labels() == ["dopeiptv-night"] and not p.night_mode_failed()


def test_a_streams_sound_that_did_not_start_through_it_is_brought_back():
    pytest.importorskip("PyQt6.QtWidgets")
    core = FakeMpv(sound=False, refused=True)  # given before the stream
    p, told = _player(core)
    p.set_night_mode(True)
    assert core.labels() == ["dopeiptv-night"] and not p.night_mode_failed()
    p._night_on_event("start-file")            # only the stream's start counts
    assert core.aid_set == []
    p._night_on_event("playback-restart")
    assert p.refused == [1] and p.night_mode_failed()
    assert nm.sound_plays(core) and core.labels() == []
    p._night_on_event("playback-restart")      # a seek: nothing more
    p._sync_night()                            # the next stream on this core
    assert p.refused == [1] and core.labels() == [] and core.aid_set == [1, 1]
    # A stream whose sound plays through it: not touched.
    core = FakeMpv(sound=True)
    p, _told = _player(core)
    p.set_night_mode(True)
    p._night_on_event("playback-restart")
    assert p.refused == [] and core.aid_set == [] and core.labels() == ["dopeiptv-night"]


def test_the_window_says_it_once_however_it_was_switched():
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.i18n import tr
    from dopeiptv.ui.mw_shortcuts import _ShortcutsMixin
    said, status = [], []
    p, _told = _player(FakeMpv(), _Settings())
    p.isVisible = lambda: True
    p.show_number_entry = lambda text, ms: said.append(text)
    w = SimpleNamespace(player=p, _typing=lambda: False, _flash_status=status.append)
    for name in ("_shortcut_night_mode", "_night_mode_told", "_night_mode_refused", "_night_say"):
        setattr(w, name, getattr(_ShortcutsMixin, name).__get__(w))
    p.night_mode_changed = SimpleNamespace(emit=w._night_mode_told)
    p.night_mode_refused = SimpleNamespace(emit=w._night_mode_refused)
    w._shortcut_night_mode()                  # the key
    assert said == [tr("night_on")]           # once, not twice
    p.toggle_night_mode()                     # the player's menu
    p.set_night_mode(True)                    # Settings
    assert said == [tr("night_on"), tr("night_off"), tr("night_on")] and status == []
    p.current_url = None                      # no picture: the status line
    p.set_night_mode(False)
    assert status == [tr("night_off")]
    p.current_url = "http://h/live/2.ts"      # a core that refuses it
    p.video.mpv = FakeMpv(broken=True)
    p.set_night_mode(True)
    assert said[-1] == tr("night_refused") and tr("night_refused") != tr("night_on")
    w._night_mode_refused()                   # found out as a stream's sound starts
    assert said[-1] == tr("night_refused")
    # The window hears both from the player (its part of the window's
    # build, ui/mw_build_detail.py).
    from dopeiptv.ui.main_window import MainWindow
    src = inspect.getsource(MainWindow._build_player)
    assert "night_mode_changed.connect(self._night_mode_told)" in src
    assert "night_mode_refused.connect(self._night_mode_refused)" in src


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
    assert src.index("self._sync_night()") < src.index("self._play_load(")
    assert "m.play(source or url)" in inspect.getsource(EmbeddedPlayer._play_load)
    # The video side is never part of it.
    night = (inspect.getsource(EmbeddedPlayer._sync_night)
             + inspect.getsource(EmbeddedPlayer._night_on_event) + inspect.getsource(nm))
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


_RATE = 16000


def _speech(rnd, secs: float, f0: float, pauses: list | None = None, at: int = 0) -> list:
    """Speech-like sound: syllables (a voice's harmonics, a quick rise and
    fall) in words of one to three, words in phrases, phrases apart by a
    pause - whose span (from *at*) goes into *pauses*."""
    import math
    out: list[float] = []
    while len(out) < secs * _RATE:
        for _word in range(rnd.randint(2, 4)):
            for _syl in range(rnd.randint(1, 3)):
                n, f = int(rnd.uniform(0.12, 0.26) * _RATE), f0 * rnd.uniform(0.9, 1.15)
                amp = rnd.uniform(0.8, 1.0)
                phase = [rnd.random() * 6.28 for _k in range(8)]
                for i in range(n):
                    t = i / _RATE
                    env = min(1.0, t / 0.02) * min(1.0, (n - i) / _RATE / 0.04)
                    out.append(amp * env * sum(math.sin(2 * math.pi * f * k * t + phase[k - 1]) / k
                                               for k in range(1, 9)) / 2.2)
                out += [0.0] * int(rnd.uniform(0.03, 0.08) * _RATE)
            out += [0.0] * int(rnd.uniform(0.08, 0.15) * _RATE)
        start = len(out)
        out += [0.0] * int(rnd.uniform(0.35, 0.6) * _RATE)
        if pauses is not None and start < secs * _RATE:
            pauses.append((at + start, at + min(len(out), int(secs * _RATE))))
    return out[:int(secs * _RATE)]


def _noise(rnd, n: int, rms_db: float) -> list:
    """A dark, steady background (room tone, a bed under speech)."""
    b, out = 0.0, []
    for _ in range(n):
        b = 0.97 * b + 0.03 * (rnd.random() * 2 - 1)
        out.append(b)
    scale = 10 ** (rms_db / 20) / _rms(out)
    return [x * scale for x in out]


def _rms(x) -> float:
    import math
    return math.sqrt(sum(v * v for v in x) / max(1, len(x))) or 1e-9


def _db(x: float) -> float:
    import math
    return 20 * math.log10(max(x, 1e-9))


def _loudness(x) -> float:
    """Integrated loudness, ITU-R BS.1770 (K-weighting, 400 ms blocks,
    the absolute and the relative gate) - what ffmpeg's ebur128 says."""
    import math

    def biquad(x, b0, b1, b2, a1, a2):
        y, x1, x2, y1, y2 = [], 0.0, 0.0, 0.0, 0.0
        for v in x:
            o = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1, y2, y1 = x1, v, y1, o
            y.append(o)
        return y
    k = math.tan(math.pi * 1681.974450955533 / _RATE)
    q, vh = 0.7071752369554196, 10 ** (3.999843853973347 / 20)
    vb, a0 = vh ** 0.4996667741545416, 1 + k / q + k * k
    y = biquad(x, (vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0,
               (vh - vb * k / q + k * k) / a0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0)
    k, q = math.tan(math.pi * 38.13547087602444 / _RATE), 0.5003270373238773
    a0 = 1 + k / q + k * k
    y = biquad(y, 1.0, -2.0, 1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0)
    acc = [0.0]
    for v in y:
        acc.append(acc[-1] + v * v)
    blk, step = int(0.4 * _RATE), int(0.1 * _RATE)
    z = [(acc[s + blk] - acc[s]) / blk for s in range(0, len(y) - blk + 1, step)]
    z = [m for m in z if m > 0 and -0.691 + 10 * math.log10(m) > -70]
    rel = -0.691 + 10 * math.log10(sum(z) / len(z)) - 10
    z = [m for m in z if -0.691 + 10 * math.log10(m) > rel]
    return -0.691 + 10 * math.log10(sum(z) / len(z))


def _mix(a, b) -> list:
    return [x + y for x, y in zip(a, b, strict=True)]


def _at(x, lufs: float) -> list:
    g = 10 ** ((lufs - _loudness(x)) / 20)
    return [v * g for v in x]


def _sounds() -> dict:
    """The test signals, each with what is measured on it (sample spans)."""
    import random
    rnd, s = random.Random(7), _RATE
    out = {}
    # The news: speaker A at -23 LUFS, B 3 dB quieter, taking turns over
    # room tone - plain, already even speech.
    pauses: list = []
    parts = [_speech(rnd, 3, (120, 200)[i % 2], pauses, i * 3 * s) for i in range(4)]
    a, b = _at(parts[0] + parts[2], -23), _at(parts[1] + parts[3], -26)
    news = a[:3 * s] + b[:3 * s] + a[3 * s:] + b[3 * s:]
    news = _mix(news, _noise(rnd, len(news), -60))
    out["news"] = (news, {"pauses": pauses, "turns": [(i * 3 * s + s // 2, (i + 1) * 3 * s)
                                                      for i in range(4)]})
    # A film: quiet dialogue (-30 LUFS), four bangs over it, an explosion.
    film = _at(_speech(rnd, 12, 130), -30)
    bangs = [(int((4 + 1.2 * i) * s), int((4 + 1.2 * i) * s + 0.04 * s)) for i in range(4)]
    for a0, a1 in bangs:
        for i in range(a0, a1):
            film[i] += 0.9 * (rnd.random() * 2 - 1)
    boom = _noise(rnd, int(1.5 * s), -12)
    for i, v in enumerate(boom):
        film[int(8.6 * s) + i] += v * (1 - 0.5 * i / len(boom))
    film = _mix(film, _noise(rnd, len(film), -55))
    out["film"] = (film, {"quiet": [(s // 2, int(3.8 * s))], "bangs": bangs,
                          "loud": [(int(8.6 * s), int(10.1 * s))]})
    # An advert 11 LU louder than the speech before and after it.
    import math
    ad = [0.5 * (0.75 * sum(math.sin(2 * math.pi * f * i / s) for f in (220, 277, 330, 440, 554))
                 / 5 * (1.0 if (i / s) % 0.5 < 0.1 else 0.7) + 0.25 * (rnd.random() * 2 - 1))
          for i in range(4 * s)]
    advert = (_at(_speech(rnd, 4, 120), -23) + _at(ad, -12) + _at(_speech(rnd, 4, 120), -23))
    advert = _mix(advert, _noise(rnd, len(advert), -60))
    out["advert"] = (advert, {"quiet": [(s // 2, 4 * s), (int(8.5 * s), 12 * s)],
                              "loud": [(int(4.5 * s), 8 * s)]})
    # Speech over a steady background bed.
    bed = _mix(_at(_speech(rnd, 8, 140), -23), _noise(rnd, 8 * s, -38))
    out["bed"] = (bed, {"turns": [(s, 8 * s)]})
    return out


def _through_a_real_core(tmp_path, x, name: str) -> list:
    """*x* played through night mode on a real core, as mpv's pcm output
    wrote it (faster than real time)."""
    import array
    import locale
    import wave
    try:
        import mpv
        locale.setlocale(locale.LC_NUMERIC, "C")
    except Exception as e:            # no libmpv on this machine
        pytest.skip(f"libmpv not here: {e}")
    src, out = tmp_path / f"{name}.wav", tmp_path / f"{name}.raw"
    with wave.open(str(src), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_RATE)
        w.writeframes(array.array("h", (max(-32767, min(32767, int(v * 32767)))
                                        for v in x)).tobytes())
    m = mpv.MPV(vo="null", ao="pcm", ao_pcm_file=str(out), ao_pcm_waveheader="no",
                audio_format="s16", audio_channels="mono", audio_samplerate=_RATE,
                terminal=False, idle=True)
    try:
        assert nm.apply(m, True)
        m.play(str(src))
        m.wait_for_playback(timeout=60)
        assert nm.has_filter(m)                            # never dropped on the way
    finally:
        m.terminate()
    y = [v / 32768 for v in array.array("h", out.read_bytes())]
    assert abs(len(y) - len(x)) < _RATE // 2, (name, len(y), len(x))
    return y[:len(x)] + [0.0] * max(0, len(x) - len(y))


def _span(x, spans) -> list:
    return [v for a, b in spans for v in x[a:b]]


def _drift(x, y, spans) -> float:
    """How far the gain it applies moves (dB), 0.4 s at a time where speech is."""
    n, gains = int(0.4 * _RATE), []
    for a, b in spans:
        for i in range(a, b - n, n):
            if _db(_rms(x[i:i + n])) > -45:
                gains.append(_db(_rms(y[i:i + n])) - _db(_rms(x[i:i + n])))
    return max(gains) - min(gains)


def test_a_real_core_never_makes_it_louder_leaves_speech_and_brings_the_loud_down(tmp_path):
    """Measured on this machine's libmpv: night mode is never louder than
    without it, on any of the signals; the news - plain speech, two
    speakers - is left as it is (no level change, no gain riding between
    words or speakers, the pauses not lifted), as is speech over a steady
    bed; bangs, an explosion and a loud advert come down towards the
    speech around them, which stays where it was."""
    got = {name: (x, _through_a_real_core(tmp_path, x, name), meta)
           for name, (x, meta) in _sounds().items()}
    for name, (x, y, _meta) in got.items():
        assert _loudness(y) <= _loudness(x) + 0.05, (name, _loudness(x), _loudness(y))
    x, y, meta = got["news"]
    assert -1.0 <= _loudness(y) - _loudness(x) <= 0.05
    assert _drift(x, y, meta["turns"]) <= 1.0
    assert _db(_rms(_span(y, meta["pauses"]))) - _db(_rms(_span(x, meta["pauses"]))) <= 0.2
    x, y, meta = got["bed"]
    assert _drift(x, y, meta["turns"]) <= 1.0

    def gap(x, loud, quiet):
        return _db(_rms(_span(x, loud))) - _db(_rms(_span(x, quiet)))
    x, y, meta = got["film"]
    quiet = _db(_rms(_span(y, meta["quiet"]))) - _db(_rms(_span(x, meta["quiet"])))
    assert abs(quiet) <= 1.0                               # the dialogue stays
    assert gap(x, meta["bangs"], meta["quiet"]) - gap(y, meta["bangs"], meta["quiet"]) >= 6
    assert gap(x, meta["loud"], meta["quiet"]) - gap(y, meta["loud"], meta["quiet"]) >= 2.5
    x, y, meta = got["advert"]
    assert gap(x, meta["loud"], meta["quiet"]) - gap(y, meta["loud"], meta["quiet"]) >= 3


def test_a_real_core_that_cannot_build_it_keeps_the_streams_sound(tmp_path):
    """A core given a graph its ffmpeg cannot build before the stream drops
    the stream's sound as it starts: rescue_sound takes the filter off and
    the sound plays. A playing core refuses such a graph outright and keeps
    its sound. (An audio and video stream made with ffmpeg's command.)"""
    import locale
    import shutil
    if shutil.which("ffmpeg") is None:
        pytest.skip("no ffmpeg command here to make a stream")
    try:
        import mpv
        locale.setlocale(locale.LC_NUMERIC, "C")
    except Exception as e:
        pytest.skip(f"libmpv not here: {e}")
    ts = tmp_path / "av.ts"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                    "-t", "8", "-c:v", "mpeg2video", "-c:a", "mp2", str(ts)],
                   check=True, timeout=60)
    bad = f"@{nm.LABEL}:lavfi=[dynaudnorm=f=150:no_such_option=1]"
    m = mpv.MPV(vo="null", ao="null", terminal=False, idle=True, keep_open="yes")
    try:
        m.command("af", "add", bad)                        # an idle core takes it
        assert nm.has_filter(m)
        m.play(str(ts))
        m.wait_for_property("time-pos", lambda v: v is not None and v > 0.3, timeout=20)
        assert nm.sound_plays(m) is False                  # mpv dropped the sound
        assert nm.rescue_sound(m) is True
        assert nm.sound_plays(m) is True and not nm.has_filter(m)
        try:                                               # playing: refused outright
            m.command("af", "add", bad)
            refused = False
        except Exception:                                  # python-mpv's error, by version
            refused = True
        assert refused and nm.sound_plays(m) is True and not nm.has_filter(m)
        assert nm.apply(m, True) and nm.sound_plays(m)     # the real one takes
    finally:
        m.terminate()
