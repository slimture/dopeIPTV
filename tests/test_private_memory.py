"""Private viewing keeps nothing on a disk (core/memory_safe): a folder
counts as memory only when it is on tmpfs or ramfs and swap cannot carry
it to a disk - else a private channel has no rewind buffer, a private cast
is not converted, a private subtitle is no file - and every media output
the app makes for a private playback asks it."""
from __future__ import annotations

import inspect
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from dopeiptv.core import memory_safe, privacy

NO_SWAP = "Filename\tType\tSize\tUsed\tPriority\n"


@pytest.fixture(autouse=True)
def _clean():
    privacy.set_private(False)
    privacy.playback_stopped()
    memory_safe._SAID.clear()
    yield
    privacy.set_private(False)
    privacy.playback_stopped()


def _fake_sys(root: Path, blocks: dict) -> str:
    """A /sys with block devices: {name: {"uuid": ..., "slaves": [...], "backing": ...}}."""
    for name, info in blocks.items():
        d = root / "class" / "block" / name
        d.mkdir(parents=True)
        if "uuid" in info:
            (d / "dm").mkdir()
            (d / "dm" / "uuid").write_text(info["uuid"] + "\n")
        if "slaves" in info:
            (d / "slaves").mkdir()
            for s in info["slaves"]:
                (d / "slaves" / s).mkdir()
        if "backing" in info:
            (d / "backing_dev").write_text(info["backing"] + "\n")
    return str(root)


def test_a_block_device_is_encrypted_only_as_its_parts_are(tmp_path):
    sys_root = _fake_sys(tmp_path, {
        "dm-0": {"uuid": "CRYPT-LUKS2-abc-luks"},
        "dm-1": {"uuid": "LVM-xyz", "slaves": ["dm-0"]},          # LVM on LUKS
        "dm-2": {"uuid": "LVM-plain", "slaves": ["sda3"]},        # LVM on a partition
        "sda3": {},
        "zram0": {"backing": "none"},
        "zram1": {"backing": "/dev/sdb1"},                         # writes back to a disk
        "md0": {"slaves": ["dm-0", "sda3"]},                       # half of it plain
    })
    assert memory_safe.encrypted_block("dm-0", sys_root)
    assert memory_safe.encrypted_block("dm-1", sys_root)
    assert not memory_safe.encrypted_block("dm-2", sys_root)
    assert not memory_safe.encrypted_block("sda3", sys_root)
    assert memory_safe.encrypted_block("zram0", sys_root)
    assert not memory_safe.encrypted_block("zram1", sys_root)
    assert not memory_safe.encrypted_block("md0", sys_root)
    assert not memory_safe.encrypted_block("../etc", sys_root)
    assert not memory_safe.encrypted_block("", sys_root)


def test_zram_whose_backing_cannot_be_read_is_not_safe(tmp_path, monkeypatch):
    # A kernel without zram writeback has no backing_dev: nothing can reach a
    # disk. One that is there but cannot be read says nothing - not safe.
    root = _fake_sys(tmp_path, {"zram0": {}, "zram1": {"backing": "none"},
                                "zram2": {"backing": "/dev/sdb2"}, "zram3": {"backing": "none"}})
    assert memory_safe.encrypted_block("zram0", root), "no writeback in the kernel"
    assert memory_safe.encrypted_block("zram1", root)
    assert not memory_safe.encrypted_block("zram2", root)
    real = memory_safe._read
    monkeypatch.setattr(memory_safe, "_read",
                        lambda path, limit=0: None if "zram3" in path else real(path, limit))
    assert not memory_safe.encrypted_block("zram3", root), "unreadable: not counted as safe"


def test_swap_is_safe_only_when_every_area_is(tmp_path, monkeypatch):
    sys_root = _fake_sys(tmp_path, {"dm-0": {"uuid": "CRYPT-plain-x"}, "sda2": {}, "zram0": {}})
    assert memory_safe.swap_list(NO_SWAP + "/dev/x\\040y partition 1 0 -2\n") == [("/dev/x y", "partition")]
    assert memory_safe.swap_safe(NO_SWAP, sys_root) == (True, "no swap")
    where = {"/dev/dm-0": "dm-0", "/dev/sda2": "sda2", "/dev/zram0": "zram0"}
    monkeypatch.setattr(memory_safe, "_block_of", lambda p, kind, root: where.get(p))
    ok, why = memory_safe.swap_safe(NO_SWAP + "/dev/dm-0 partition 1 0 -2\n/dev/zram0 partition 1 0 100\n",
                                    sys_root)
    assert ok and why == "swap encrypted"
    ok, why = memory_safe.swap_safe(NO_SWAP + "/dev/dm-0 partition 1 0 -2\n/dev/sda2 partition 1 0 -3\n",
                                    sys_root)
    assert not ok and "sda2" in why
    ok, _why = memory_safe.swap_safe(NO_SWAP + "/swapfile file 1 0 -2\n", sys_root)
    assert not ok, "a swap file whose disk is not known"
    monkeypatch.setattr(memory_safe, "_read", lambda path, limit=0: None)
    assert memory_safe.swap_safe(None, sys_root)[0] is False, "not asked: not safe"


def test_a_folder_stays_in_memory_only_out_of_swaps_reach(tmp_path):
    plain = NO_SWAP + "/nowhere-swap file 1 0 -2\n"
    mem = f"tmpfs {tmp_path} tmpfs rw,nosuid 0 0\n"
    assert memory_safe.stays_in_memory(tmp_path / "x", mem, NO_SWAP)[0]
    assert not memory_safe.stays_in_memory(tmp_path / "x", mem, plain)[0]
    assert memory_safe.stays_in_memory(tmp_path / "x", f"tmpfs {tmp_path} tmpfs rw,noswap 0 0\n", plain)[0]
    assert memory_safe.stays_in_memory(tmp_path / "x", f"none {tmp_path} ramfs rw 0 0\n", plain)[0]
    assert not memory_safe.stays_in_memory(tmp_path / "x", f"/dev/sda1 {tmp_path} ext4 rw 0 0\n",
                                           NO_SWAP)[0]
    # The longest mount point wins, the newest of equals.
    over = mem + f"/dev/sdb1 {tmp_path} ext4 rw 0 0\n"
    assert not memory_safe.stays_in_memory(tmp_path / "x", over, NO_SWAP)[0]


def test_the_private_folder_is_this_users_own_and_said_once_when_not(tmp_path, monkeypatch):
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    env = {"XDG_RUNTIME_DIR": str(run)}
    mem = f"tmpfs {run} tmpfs rw 0 0\n"
    assert memory_safe.private_base("cast", env, mem, "linux") == run / "dopeiptv-cast"
    assert memory_safe.private_base("cast", env, mem, "darwin") is None
    assert memory_safe.private_base("cast", env, mem, "win32") is None
    base = memory_safe.private_folder("cast", mem, NO_SWAP, env, "linux")
    assert base == run / "dopeiptv-cast" and (os.stat(base).st_mode & 0o777) == 0o700
    said = []
    monkeypatch.setattr(memory_safe.log, "info", lambda *a: said.append(a))
    plain = NO_SWAP + "/nowhere-swap file 1 0 -2\n"
    assert memory_safe.private_folder("cast", mem, plain, env, "linux") is None
    assert memory_safe.private_folder("cast", mem, plain, env, "linux") is None
    assert len(said) == 1, "said once"
    assert memory_safe.private_folder("subs", mem, NO_SWAP, env, "darwin") is None


def test_a_crashs_leftovers_in_memory_are_swept_never_a_running_ones(tmp_path):
    (tmp_path / f"{os.getpid()}-cast-a").mkdir()
    gone_pid = 2 ** 22 + 12345
    (tmp_path / f"{gone_pid}-cast-b").mkdir()
    (tmp_path / "notapid").mkdir()
    assert memory_safe.sweep(tmp_path) == 1
    assert (tmp_path / f"{os.getpid()}-cast-a").exists() and (tmp_path / "notapid").exists()
    assert not (tmp_path / f"{gone_pid}-cast-b").exists()


class _Ingest:
    def __init__(self, url, store):
        self.state = "streaming"
        self.connections = 1

    def start(self):
        pass

    def stop(self, wait: float = 0.0):
        self.state = "stopped"


def test_a_buffer_on_the_disk_is_let_go_as_its_channel_is_watched_privately(tmp_path, monkeypatch):
    from dopeiptv.core import dvr_pool as dp
    from dopeiptv.core import dvr_store
    disk, memory = tmp_path / "disk", tmp_path / "memory"
    monkeypatch.setattr(dp, "memory_budget", lambda base, cap: 300 << 20)
    monkeypatch.setattr(dvr_store, "memory_folder",
                        lambda p, mounts=None, swaps=None: str(p).startswith(str(memory)))
    pool = dp.DvrPool(disk, 600.0, 10**9, ingest_factory=_Ingest, min_free=0, memory=memory)
    try:
        before = pool.open("http://h/live/1.ts", "1")
        assert before.store.root.parent == disk
        assert not pool.on_disk_private()
        before.mark_private()                    # private viewing turned on while it plays
        assert pool.on_disk_private(), "the player is told to open it again"
        again = pool.open("http://h/live/1.ts", "1", private=True)
        assert again is not before and before.closed
        assert again.store.root.parent == memory and not pool.on_disk_private()
        # A recording reads a disk buffer: shared (one connection), it is
        # written for the recording anyway.
        rec = pool.open("http://h/live/2.ts", "2")
        monkeypatch.setattr(type(rec), "recording", property(lambda self: True))
        assert pool.open("http://h/live/2.ts", "2", private=True) is rec
    finally:
        pool.close_all(wait=5.0)


def test_a_private_channel_s_disk_buffer_is_opened_again_once_its_recording_or_cast_is_done(
        tmp_path, monkeypatch):
    """A recording or a cast keeps a channel's buffer on the disk while it
    reads it - written there for it anyway. Watched privately meanwhile,
    the player is told once nothing else reads it, so what follows is not
    written there for the private playback alone."""
    from dopeiptv.core import dvr_pool as dp
    disk = tmp_path / "disk"
    pool = dp.DvrPool(disk, 600.0, 10**9, ingest_factory=_Ingest, min_free=0, memory=None)
    told = []
    pool.on_private_disk = told.append
    try:
        # A recording of the channel, then private viewing turned on.
        rec = pool.open("http://h/live/1.ts", "1")
        recording = [True]
        monkeypatch.setattr(type(rec), "recording", property(lambda self: recording[0]))
        rec.mark_private()
        assert not pool.on_disk_private(), "kept while the recording reads it"
        pool._recording_done(rec)
        assert told == [], "nothing said while the recording still reads it"
        recording[0] = False
        pool._recording_done(rec)                 # the recording ended
        assert told == [rec] and pool.on_disk_private()
        pool._recording_done(rec)
        assert told == [rec], "told once"
        monkeypatch.undo()
        # A cast reads another channel; the pool's let_go and the session's
        # own end both come to the same place - told once.
        cast = pool.open("http://h/live/2.ts", "2")
        cast.hold("cast")
        cast.mark_private()
        assert not pool.on_disk_private()
        pool.let_go(cast, "cast")
        assert told == [rec, cast]
        # A channel not on screen, or not private: nothing.
        other = pool.open("http://h/live/3.ts", "3")
        other.hold("cast")
        pool.let_go(other, "cast")
        assert told == [rec, cast]
    finally:
        pool.close_all(wait=5.0)


def test_the_window_opens_a_private_disk_buffered_channel_again_when_told():
    from dopeiptv.media import dvr_player, embedded
    from dopeiptv.ui import mw_build_detail, mw_privacy
    assert "private_on_disk" in vars(embedded.EmbeddedPlayer)
    src = inspect.getsource(dvr_player.DvrPlayerMixin._dvr_new_pool)
    assert "pool.on_private_disk" in src and "signal.emit()" in src
    assert "self._dvr_new_pool(" in inspect.getsource(dvr_player.DvrPlayerMixin._dvr_start)
    assert "self._dvr_new_pool(" in inspect.getsource(dvr_player.DvrPlayerMixin.dvr_pool_for_server)
    src = inspect.getsource(mw_build_detail)
    assert "private_on_disk.connect(self._private_buffer_on_disk)" in src
    src = inspect.getsource(mw_privacy._PrivacyMixin._private_reopen_if_on_disk)
    assert "private_reopen_needed" in src and "self._retry_last_stream" in src
    assert "_private_reopen_if_on_disk" in inspect.getsource(mw_privacy._PrivacyMixin._private_buffer_on_disk)
    # A player that let go of the channel before the signal came: nothing
    # is started again.
    asked = []
    window = SimpleNamespace(player=SimpleNamespace(_dvr=None, private_reopen_needed=lambda: True),
                             _last_playback={"kind": "live"},
                             _private_reopen_if_on_disk=asked.append)
    mw_privacy._PrivacyMixin._private_buffer_on_disk(window)
    assert asked == []
    window.player._dvr = object()
    mw_privacy._PrivacyMixin._private_buffer_on_disk(window)
    assert len(asked) == 1


def test_the_player_plays_a_private_channel_without_a_buffer_and_never_mpvs_on_disk():
    from dopeiptv.media import dvr_player, embedded
    src = inspect.getsource(dvr_player.DvrPlayerMixin._dvr_start)
    assert "NoPrivateBuffer" in src and "return None" in src
    # play()'s step that sets mpv's cache and rewind for each stream.
    src = inspect.getsource(embedded.EmbeddedPlayer._play_base_options)
    assert "self._play_base_options(" in inspect.getsource(embedded.EmbeddedPlayer.play)
    assert "not self._dvr_playing_private()" in src
    assert 'm["cache-on-disk"] = "yes" if on_disk else "no"' in src
    assert "self._mpv_cache_on_disk = on_disk" in src
    p = SimpleNamespace(_dvr=None, _dvr_pool=None, _mpv_cache_on_disk=True)
    assert dvr_player.DvrPlayerMixin.private_reopen_needed(p)
    p._mpv_cache_on_disk = False
    assert not dvr_player.DvrPlayerMixin.private_reopen_needed(p)


def test_private_viewing_turned_on_opens_a_disk_buffered_channel_again_and_resends_a_cast():
    from dopeiptv.ui import mw_privacy
    src = inspect.getsource(mw_privacy._PrivacyMixin.set_private_viewing)
    assert "_private_reopen_if_on_disk" in src
    assert "self._cast_private_on()" in src
    from dopeiptv.ui import mw_cast
    src = inspect.getsource(mw_cast._CastMixin._cast_private_on)
    assert "on_disk" in src and "self._cast_restart(ctx)" in src and 'ctx.pop("_recovering"' in src
    assert "memory_safe.sweep" in inspect.getsource(mw_privacy)


def _private_cast(monkeypatch, tmp_path, folder):
    from dopeiptv.providers import cast_bridge as cb
    privacy.set_private(True)
    monkeypatch.setattr(cb, "private_cast_dir", lambda: folder)
    monkeypatch.setattr(cb.shutil, "disk_usage",
                        lambda p: SimpleNamespace(total=8 << 30, used=0, free=2 << 30))
    disk = tmp_path / "cache"
    disk.mkdir(exist_ok=True)
    monkeypatch.setattr(cb, "cast_cache_dir", lambda: str(disk))
    return cb, disk


def test_a_private_cast_is_not_converted_without_memory_out_of_a_disks_reach(tmp_path, monkeypatch):
    from dopeiptv.i18n import LocalizedError
    cb, disk = _private_cast(monkeypatch, tmp_path, None)
    bridge = cb.CastBridge()
    closed = []
    feed = SimpleNamespace(close=lambda: closed.append(1), detach=lambda: None)
    with pytest.raises(LocalizedError) as e:
        bridge.start("http://h/live/1.ts", ["eac3"], feed=feed)
    assert e.value.key == "cast_err_private_memory"
    assert closed == [1], "the feed it was handed is let go"
    assert not any(disk.iterdir()), "nothing on the disk"
    assert bridge._server is None


def test_a_private_casts_work_is_in_memory_with_a_cap_of_its_own(tmp_path, monkeypatch):
    memory = tmp_path / "memory"
    memory.mkdir()
    cb, disk = _private_cast(monkeypatch, tmp_path, str(memory))
    bridge = cb.CastBridge()
    try:
        bridge.start("http://h/live/1.ts", ["eac3"], subs=2, sub_codec="subrip")   # HLS: files at once
        assert bridge.private and bridge.hls
        assert str(bridge.hls_dir).startswith(str(memory))
        assert os.path.basename(os.path.dirname(bridge.hls_dir)).startswith(f"{os.getpid()}-cast-")
        assert not any(disk.iterdir()), "nothing on the disk"
        assert bridge._private_cap == min(bridge.cap, cb.PRIVATE_CAP, ((2 << 30) - cb.PRIVATE_LEAST) // 2)
        assert not bridge.on_disk()
        local = tmp_path / "film.mkv"
        local.write_bytes(b"x")
        assert bridge._safe_source(str(local)).startswith(str(memory)), "the link in memory too"
    finally:
        bridge.stop()
    assert not any(memory.iterdir())
    privacy.set_private(False)
    plain = cb.CastBridge()
    try:
        plain.start("http://h/live/1.ts", ["eac3"], subs=2, sub_codec="subrip")
        assert not plain.private and plain.on_disk() and str(plain.hls_dir).startswith(str(disk))
    finally:
        plain.stop()
    # The spool a pause is kept in: no more than the memory's own cap.
    assert "min(self.cap, self._private_cap) if self.private else self.cap" in inspect.getsource(cb)


def _until(cond, seconds=5.0):
    import time
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def test_private_viewing_begun_mid_cast_stops_a_run_on_the_disk_by_itself(tmp_path, monkeypatch):
    # The window may be busy sending the cast when private viewing begins:
    # the converter's own watch ends a run whose work is on the disk.
    cb, disk = _private_cast(monkeypatch, tmp_path, str(tmp_path / "memory"))
    monkeypatch.setattr(cb, "WATCH_EVERY", 0.02)
    privacy.set_private(False)
    bridge = cb.CastBridge()
    try:
        bridge.start("http://h/live/1.ts", ["eac3"], subs=2, sub_codec="subrip")
        assert bridge.on_disk() and str(bridge.hls_dir).startswith(str(disk))
        privacy.set_private(True)
        # Stopped and tidied: _stop() clears the server first, then removes
        # the run's folder (_tmp None after it).
        assert _until(lambda: bridge._server is None and bridge._tmp is None), "stopped within its tick"
        assert bridge.private_stopped and bridge.fatal == "private viewing began"
        assert not any(disk.iterdir()), "its work on the disk gone"
    finally:
        bridge.stop()


def test_a_casts_work_on_the_disk_stops_the_moment_private_viewing_begins(tmp_path, monkeypatch):
    """Not at the watch's next look a second later: turning private viewing
    on wakes it (privacy.wait_change), so nothing more is written."""
    import time
    cb, disk = _private_cast(monkeypatch, tmp_path, str(tmp_path / "memory"))
    monkeypatch.setattr(cb, "WATCH_EVERY", 30.0)
    privacy.set_private(False)
    bridge = cb.CastBridge()
    try:
        bridge.start("http://h/live/1.ts", ["eac3"], subs=2, sub_codec="subrip")
        assert bridge.on_disk()
        time.sleep(0.1)                         # its watch is waiting now
        began = time.monotonic()
        privacy.set_private(True)
        assert _until(lambda: bridge._server is None and bridge._tmp is None, 2.0)
        assert time.monotonic() - began < 2.0 and bridge.private_stopped
    finally:
        bridge.stop()


@pytest.mark.parametrize("how", ["start", "seek", "recast", "reconnect"])
def test_every_way_a_cast_is_sent_is_stopped_or_kept_in_memory_as_privacy_turns(
        tmp_path, monkeypatch, how):
    # A seek, a recast and the watchdog's reconnect are each a new start():
    # each run has its own watch, whatever the window does meanwhile.
    memory = tmp_path / "memory"
    memory.mkdir()
    cb, disk = _private_cast(monkeypatch, tmp_path, str(memory))
    monkeypatch.setattr(cb, "WATCH_EVERY", 0.02)
    privacy.set_private(False)
    bridge = cb.CastBridge()
    args = {"start": {}, "seek": {"start_at": 600.0}, "recast": {"audio": 1},
            "reconnect": {}}[how]
    try:
        bridge.start("http://h/live/1.ts", ["eac3"], subs=2, sub_codec="subrip")
        if how != "start":
            bridge.start("http://h/live/1.ts", ["eac3"], subs=2, sub_codec="subrip", **args)
        assert bridge.on_disk()
        privacy.set_private(True)
        assert _until(lambda: bridge._server is None and bridge._tmp is None) and bridge.private_stopped
        assert not any(disk.iterdir())
        bridge.start("http://h/live/1.ts", ["eac3"], subs=2, sub_codec="subrip", **args)
        assert bridge.private and not bridge.private_stopped
        assert str(bridge.hls_dir).startswith(str(memory))
        privacy.set_private(False)
        assert not _until(lambda: bridge._server is None, 0.2), "a private run goes on in memory"
        assert not any(disk.iterdir())
    finally:
        bridge.stop()


def test_a_private_casts_work_past_its_room_stops_the_converter(tmp_path, monkeypatch):
    memory = tmp_path / "memory"
    memory.mkdir()
    cb, disk = _private_cast(monkeypatch, tmp_path, str(memory))
    monkeypatch.setattr(cb, "WATCH_EVERY", 0.02)
    bridge = cb.CastBridge()
    killed = []
    try:
        bridge.start("http://h/live/1.ts", ["eac3"], subs=2, sub_codec="subrip")
        assert bridge.private and not bridge.on_disk()
        monkeypatch.setattr(bridge, "kill", lambda proc: killed.append(proc))
        with bridge._lock:
            bridge._procs = ["ffmpeg"]
        assert not _until(lambda: killed, 0.2), "within its room nothing is stopped"
        (Path(bridge._tmp) / "piece.ts").write_bytes(b"x" * 4096)
        bridge._private_cap = 1024
        assert _until(lambda: killed == ["ffmpeg"]), "past it the converter stops"
        assert bridge.fatal == "no more room in memory for a private cast"
        assert cb.folder_bytes(bridge._tmp) >= 4096 and cb.folder_bytes(None) == 0
    finally:
        bridge.stop()
    assert not any(memory.iterdir()) and not any(disk.iterdir())


def test_a_cast_on_its_way_as_private_viewing_begins_is_looked_at_once_it_is_sent():
    from dopeiptv.ui import mw_cast
    on = inspect.getsource(mw_cast._CastMixin._cast_private_on)
    assert 'ctx["_private_check"] = True' in on and on.index("_cast_busy()") < on.index("on_disk")
    assert 'getattr(bridge, "private_stopped", False)' in on
    send = inspect.getsource(mw_cast._CastMixin._cast_send)
    assert 'ctx.pop("_private_check", False)' in send and "_cast_private_on" in send
    calls = []
    ctx = {}
    bridge = SimpleNamespace(on_disk=lambda: False, private_stopped=False)
    win = SimpleNamespace(_cast_device="tv", _cast_ctx=ctx, _cast_busy=lambda: True,
                          cast=SimpleNamespace(bridge=bridge),
                          _cast_restart=lambda c: calls.append("restart"))
    mw_cast._CastMixin._cast_private_on(win)
    assert ctx == {"_private_check": True} and calls == [], "busy: looked at later"
    win._cast_busy = lambda: False
    mw_cast._CastMixin._cast_private_on(win)
    assert calls == [], "in memory already: nothing to send"
    bridge.private_stopped = True
    ctx["_recovering"] = 1
    mw_cast._CastMixin._cast_private_on(win)
    assert calls == ["restart"] and "_recovering" not in ctx, "stopped by its watch: sent again"


def test_a_private_airplay_run_is_watched_as_the_cast_bridges(tmp_path, monkeypatch):
    from dopeiptv.providers import airplay_bridge as ab
    src = inspect.getsource(ab.AirPlayBridge._watch)
    assert "privacy.playing_private()" in src and "self.private_stopped = True" in src
    assert "folder_bytes(self.folder) <= self._private_cap" in src and "with self._run_lock:" in src
    start = inspect.getsource(ab.AirPlayBridge.start)
    assert "with self._run_lock:" in start and "target=self._watch" in start


def test_a_private_airplay_cast_is_converted_only_in_memory():
    from dopeiptv.providers import airplay_bridge
    src = inspect.getsource(airplay_bridge.AirPlayBridge._start)
    assert "privacy.playing_private()" in src and "cast_err_private_memory" in src
    assert "private_cast_dir()" in src and 'prefix=f"{os.getpid()}-airplay-"' in src
    assert src.index("self._stop(keep=feed)") < src.index("cast_err_private_memory"), \
        "the run before goes first"


def test_a_recording_while_watching_privately_is_asked_first():
    from dopeiptv.ui import mw_detail, mw_recording
    ask = inspect.getsource(mw_recording._RecordingMixin._rec_private_ok)
    assert "playing_private()" in ask and '(tr("common_cancel"), "primary")' in ask
    assert "self._choice_dialog(" in ask, "the phone and TV mode ask it as their own"
    for fn in (mw_recording._RecordingMixin._record_now, mw_recording._RecordingMixin._schedule_recording,
               mw_detail._DetailMixin._record_programme):
        assert "self._rec_private_ok()" in inspect.getsource(fn), fn.__name__

    class W:
        _choice_dialog = None
    w = W()
    w._choice_dialog = lambda *a, **k: None
    assert mw_recording._RecordingMixin._rec_private_ok(w), "not private: no question"
    privacy.set_private(True)
    assert not mw_recording._RecordingMixin._rec_private_ok(w), "closed: not recorded"
    w._choice_dialog = lambda *a, **k: 0
    assert mw_recording._RecordingMixin._rec_private_ok(w)


def test_a_private_subtitle_is_never_a_file_on_the_disk(tmp_path, monkeypatch):
    from dopeiptv.ui import subtitle_search
    disk = tmp_path / "cache"
    monkeypatch.setattr(subtitle_search, "subtitle_dir", lambda: disk)
    row = {"release": "Show S01E01", "language": "sv", "file_id": 7}
    monkeypatch.setattr(memory_safe, "private_folder", lambda name: None)
    got = subtitle_search.save_subtitle(row, "x.srt", b"1\n00:00:01,000 --> 00:00:02,000\nHej\n", private=True)
    assert isinstance(got, str) and got.startswith("hex://")
    assert bytes.fromhex(got[6:]).endswith(b"Hej\n") and not disk.exists()
    memory = tmp_path / "memory"
    memory.mkdir()
    monkeypatch.setattr(memory_safe, "private_folder", lambda name: memory)
    got = subtitle_search.save_subtitle(row, "x.srt", b"x", private=True)
    assert str(got).startswith(str(memory / f"{os.getpid()}-subs")) and not disk.exists()
    got = subtitle_search.save_subtitle(row, "x.srt", b"x")
    assert str(got).startswith(str(disk)), "not private: the cache, as before"
    from dopeiptv.media import tracks
    src = inspect.getsource(tracks._subtitle_to_load)
    assert "playing_private()" in src
