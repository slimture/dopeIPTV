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


def test_the_filter_is_a_clear_leveller_every_ffmpeg_knows():
    assert nm.entry() == ("@dopeiptv-night:lavfi=[acompressor=threshold=0.063:ratio=3:"
                          "attack=5:release=200,dynaudnorm=f=150:g=5:p=0.7:m=10]")
    (comp, comp_opts), (norm, norm_opts) = (f.partition("=")[::2] for f in nm.GRAPH.split(","))
    assert (comp, norm) == ("acompressor", "dynaudnorm")
    c = dict(kv.split("=") for kv in comp_opts.split(":"))
    n = dict(kv.split("=") for kv in norm_opts.split(":"))
    # Only options both have had since ffmpeg 3.0, values as plain numbers
    # (no "dB" for an ffmpeg to read), none that raises the level.
    assert set(c) == {"threshold", "ratio", "attack", "release"}
    assert set(n) <= {"f", "g", "p", "m", "r", "n", "c", "b", "s"}
    assert all(v.replace(".", "", 1).isdigit() for v in (*c.values(), *n.values()))
    # Gentle above -24 dBFS, quick to catch a bang, let go before the next words.
    assert float(c["threshold"]) == 0.063 and float(c["ratio"]) == 3
    assert float(c["attack"]) == 5 and float(c["release"]) == 200
    # 150 ms slices over five of them, peaks to -3 dBFS, at most +20 dB.
    assert int(n["f"]) == 150 and int(n["g"]) == 5 and int(n["g"]) % 2 == 1
    assert float(n["p"]) == 0.7 and float(n["m"]) == 10


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
    # The window hears both from the player.
    from dopeiptv.ui.main_window import MainWindow
    src = inspect.getsource(MainWindow._build_ui)
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
    assert src.index("self._sync_night()") < src.index("m.play(source or url)")
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


_RATE = 24000


def _speech_then_a_loud_scene(path) -> None:
    """Quiet speech (peaks near -30 dBFS, syllables three times a second),
    a loud scene (-3 dBFS), quiet speech again - 2.5 s, 1.5 s, 2.5 s."""
    import array
    import math
    import wave
    a = array.array("h")
    for amp, secs in ((0.03, 2.5), (0.7, 1.5), (0.03, 2.5)):
        for i in range(int(secs * _RATE)):
            t = i / _RATE
            env = 0.5 + 0.5 * math.sin(2 * math.pi * 3 * t)
            a.append(int(amp * env * math.sin(2 * math.pi * 440 * t) * 32767))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_RATE)
        w.writeframes(a.tobytes())


def _level(samples, start: float, end: float) -> tuple[float, float]:
    """(RMS in dBFS, peak 0..1) of mono s16 *samples* from *start* to *end* s."""
    import math
    part = samples[int(start * _RATE):int(end * _RATE)]
    rms = math.sqrt(sum(x * x for x in part) / len(part)) / 32768
    return 20 * math.log10(rms + 1e-9), max(abs(x) for x in part) / 32768


def test_a_real_core_plays_a_stream_through_it_and_it_is_clearly_heard(tmp_path):
    """The graph as this machine's libmpv builds it, a stream played through
    it to a file (mpv's own pcm output, faster than real time): quiet speech
    comes up by well over 10 dB, the loud scene does not clip."""
    import array
    import locale
    import wave
    try:
        import mpv
        locale.setlocale(locale.LC_NUMERIC, "C")
    except Exception as e:            # no libmpv on this machine
        pytest.skip(f"libmpv not here: {e}")
    src, out = tmp_path / "in.wav", tmp_path / "out.raw"
    _speech_then_a_loud_scene(src)
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
    played = array.array("h", out.read_bytes())
    with wave.open(str(src), "rb") as w:
        given = array.array("h", w.readframes(w.getnframes()))
    assert abs(len(played) - len(given)) < _RATE // 2, (len(played), len(given))
    for start, end in ((1.4, 2.0), (5.2, 6.4)):        # quiet speech, settled
        before, _ = _level(given, start, end)
        after, _ = _level(played, start, end)
        assert after - before > 10, (start, before, after)
    _, loud_peak = _level(played, 2.8, 3.8)
    assert loud_peak < 0.8                                 # -3 dBFS, never clipped


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
