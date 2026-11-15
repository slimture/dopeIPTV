from __future__ import annotations

import os

from dopeiptv.providers import airplay_bridge as ab


def test_a_crashed_runs_ffmpeg_is_ended_and_its_files_go(tmp_path, monkeypatch):
    left = tmp_path / "airplay-abc"
    left.mkdir()
    (left / ab.PIDFILE).write_text("4242")
    (left / "s00001.m4s").write_bytes(b"x")
    other = tmp_path / "keep-me"
    other.mkdir()
    ended = []

    class Out:
        def __init__(self, text):
            self.stdout = text

    def run(args, **kw):
        if args[0] in ("ps", "tasklist"):
            return Out("ffmpeg\n")
        ended.append(args)
        return Out("")

    monkeypatch.setattr(ab.os, "kill", lambda pid, sig: ended.append(pid))
    assert ab.sweep_leftovers(str(tmp_path), run=run) == 1
    assert ended and not left.exists() and other.exists()


def test_a_reused_pid_that_is_not_ffmpeg_is_left_alone(tmp_path, monkeypatch):
    left = tmp_path / "airplay-def"
    left.mkdir()
    (left / ab.PIDFILE).write_text(str(os.getpid() + 1))

    class Out:
        stdout = "Safari\n"

    killed = []
    monkeypatch.setattr(ab.os, "kill", lambda pid, sig: killed.append(pid))
    assert ab.sweep_leftovers(str(tmp_path), run=lambda *a, **k: Out()) == 0
    assert not killed and not left.exists()


def test_the_player_core_drops_mpvs_unused_scripts_where_it_can(monkeypatch):
    from dopeiptv.media import video_surface as vs
    tried = []

    class FakeMPV:
        def __init__(self, **opts):
            tried.append(opts)
            if "load_context_menu" in opts:      # an older libmpv
                raise AttributeError("option does not exist")

    monkeypatch.setattr(vs, "_libmpv", type("M", (), {"MPV": FakeMPV}))
    vs._create_core()
    assert "load_context_menu" in tried[0]
    assert "load_select" in tried[-1] and len(tried) == 2
