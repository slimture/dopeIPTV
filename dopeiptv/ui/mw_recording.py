"""Extracted from main_window.py (mixin); MainWindow inherits this.

Verbatim move - self.* access and behaviour unchanged.
"""

from __future__ import annotations

import os
import shutil
import time
from functools import partial
from ..providers.client import b64, epg_times
from ..i18n import tr
from ..core import connections
from ..core.connections import Use
from ..core.log import log, redact_url
from ..core.privacy import for_log
from ..core.recording import SCRAMBLED, RecordingManager, format_size, free_path, safe_filename
from .notices import hub_of
from .theme import P
from .widgets import confirm, plain_text
from ..core.workers import run_async
from .qt_i18n import format_when
from PyQt6.QtCore import QDateTime, Qt, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QVBoxLayout
from datetime import datetime, timedelta, timezone


# Why a recording stopped or failed, as the recorder says it (English, and
# also what the log shows) -> what the user reads.
# A recording's line stays as long as it runs; the hub wants a bound.
REC_NOTICE_MS = 7 * 24 * 3600 * 1000

_REC_REASONS = {
    "stopped": "rec_reason_stopped",
    "finished": "rec_reason_finished",
    "size limit reached": "rec_reason_size_limit",
    "recorder stopped unexpectedly": "rec_reason_recorder_died",
    "playback stopped": "rec_reason_playback_stopped",
    "channel changed": "rec_reason_channel_changed",
    "stream error": "rec_reason_stream_error",
    "multiview": "rec_reason_multiview",
    "continued over a new connection": "rec_reason_continued",
    "app closed": "rec_reason_app_closed",
    "no data captured": "rec_reason_no_data",
    "neither ffmpeg nor mpv found": "rec_reason_no_recorder",
    "stop time passed before the app could start it": "rec_reason_too_late",
    RecordingManager.NO_ADDRESS: "rec_reason_no_address",
    RecordingManager.NO_ROOM: "rec_reason_no_room",
    SCRAMBLED: "rec_reason_scrambled",
}


def rec_reason(reason: str) -> str:
    """The recorder's reason in the user's language."""
    key = _REC_REASONS.get(reason or "")
    if key:
        return tr(key)
    if (reason or "").startswith("recorder exited early (code "):
        return tr("rec_reason_exit_code",
                  code=reason[len("recorder exited early (code "):-1])
    return reason or ""


def _live_address(client, stream_id) -> str:
    """(the recorder's thread) A channel's address from the source playing."""
    return client.live_url(stream_id, "ts") or ""


def _source_address(playlist: dict, stream_id) -> str:
    """(the recorder's thread) A channel's address from another source."""
    from ..providers.client import make_client
    return make_client(playlist).live_url(stream_id, "ts") or ""


def _window_live(win, stream_id) -> str:
    """What a recording asks the source playing for (the window's plan for
    MainWindow._links_later)."""
    return win.client.live_url(stream_id, "ts") or ""


class _RecordingMixin:
    """MainWindow mixin: recording, scheduling and timeshift/catch-up controls."""
    def _job_item(self, j: dict) -> dict:
        label = {"recording": "● REC",
                 "scheduled": tr("rec_status_scheduled"),
                 "done": tr("rec_status_done"),
                 "failed": tr("rec_status_failed"),
                 "cancelled": tr("rec_status_cancelled")}.get(
                     j["status"], j["status"])
        start = format_when(j["start"])
        stop = (tr("rec_until_stopped") if j.get("stop") is None
                else datetime.fromtimestamp(j["stop"]).strftime("%H:%M"))
        name = f"[{label}] {j['title']}  ({start} – {stop})"
        # Why it failed, where it failed - a bare [Failed] said nothing.
        if j["status"] == "failed" and j.get("error"):
            name += f"  —  {rec_reason(j['error'])}"
        # Due, and waiting for room on the account (_rec_may_open).
        if j["status"] == "scheduled" and j.get("_held"):
            name += f"  —  {tr('rec_waiting_room')}"
        # Saved as MP4 (Settings > Recording): while it is copied, and when
        # the .ts had to be kept.
        if j.get("remux") == "running":
            name += f"  —  {tr('rec_mp4_saving')}"
        elif j.get("remux") == "failed":
            name += f"  —  {tr('rec_mp4_kept_ts')}"
        elif j.get("remux") == "subs":
            # Kept as .ts on purpose: teletext and subtitles stay in it.
            name += f"  —  {tr('rec_mp4_kept_subs')}"
        return {"name": name,
                "_job": j["id"], "_key": f"job:{j['id']}",
                "_kind": "recjob", "_status": j["status"],
                "_held": bool(j.get("_held")),
                "_error": j.get("error") or "",
                "_path": j.get("path") or "",
                # The recorded channel's logo, so the row isn't iconless.
                "stream_icon": j.get("stream_icon") or ""}

    def _recordings_changed(self) -> None:
        tv = getattr(self, "_tv_rec_changed", None)
        if callable(tv):
            tv()                        # TV mode's Record and REC mark (mw_tv)
        n = self.rec.active_count()
        self.rec_indicator.setText(
            f"● REC ({n})" if n > 1 else "● REC")
        self.rec_indicator.setVisible(n > 0)
        if self.player:
            for b in (self.player.rec_btn, self.player.fs_rec_btn):
                b.setToolTip(tr("rec_tooltip_recording", n=n) if n
                             else tr("tooltip_record"))
                b.setStyleSheet(
                    "color:#FF5C5C; font-weight:700;" if n
                    else "color:#FF5C5C;")
        if self.mode == "rec":
            cur = self.cat_list.currentItem()
            self._load_items(
                cur.data(Qt.ItemDataRole.UserRole) if cur else None)
        self._rec_notices()
        self._remux_jobs()

    def _rec_notices(self) -> None:
        """Each running recording is work on the activity line for as long
        as it runs: a click on it - or its Stop recording in the panel, with
        several - asks before it stops (`_rec_ask_stop`); nothing stops
        without that answer."""
        hub = hub_of(self)
        running = {str(j["id"]): j for j in getattr(self.rec, "jobs", [])
                   if j.get("status") == "recording"}
        shown = self.__dict__.setdefault("_rec_shown", set())
        for jid in list(shown):
            if jid not in running:
                shown.discard(jid)
                hub.end_key("rec:" + jid)
        for jid, j in running.items():
            if jid in shown:
                continue
            shown.add(jid)
            ask = partial(self._rec_ask_stop, j["id"])
            hub.start(tr("notice_recording", title=j.get("title") or ""),
                      key="rec:" + jid, kind="record", mine=True,
                      timeout_ms=REC_NOTICE_MS, cancel=ask, act=ask,
                      cancel_text=tr("rec_stop_recording"))

    def _rec_ask_stop(self, jid) -> bool:
        """Stop the recording *jid*? Asked every time; what has been
        recorded so far is kept. True once it is stopped."""
        j = next((k for k in getattr(self.rec, "jobs", [])
                  if k.get("id") == jid and k.get("status") == "recording"), None)
        if j is None:
            return True                     # it ended meanwhile
        idx = self._choice_dialog(
            tr("rec_stop_ask_title"),
            tr("rec_stop_ask", title=j.get("title") or ""),
            [(tr("rec_stop_recording"), "danger"),
             (tr("rec_keep_recording"), "primary")])
        if idx != 0:
            return False
        self.rec.cancel(jid)
        return True

    def _remux_jobs(self) -> None:
        """A finished recording saved as MP4 (core/recording.py's remux, on
        a thread of its own) is work the window does: a job from its start
        to its end, keyed by its file."""
        hub = hub_of(self)
        running = {str(j.get("path") or ""): j for j in getattr(self.rec, "jobs", [])
                   if j.get("remux") == "running" and j.get("path")}
        shown = self.__dict__.setdefault("_remux_shown", set())
        for path in list(shown):
            if path not in running:
                shown.discard(path)
                hub.end_key("remux:" + path)
        for path, j in running.items():
            if path not in shown:
                shown.add(path)
                hub.start(tr("notice_saving_mp4", title=j.get("title") or ""),
                          key="remux:" + path, kind="record", timeout_ms=30 * 60_000)

    def _on_recording_stopped(self, title: str, reason: str) -> None:
        why = rec_reason(reason)
        self._flash_status(
            "● " + tr("rec_stopped_named", title=title, reason=why))
        if self._player_fs and self.player:
            self.player.set_overlay_info(
                tr("rec_stopped_named", title=title, reason=why))

    def _choice_dialog(self, title: str, message: str,
                       options: list[tuple[str, str]],
                       dont_ask_setting: str | None = None) -> int | None:
        """A compact, themed confirmation dialog. *options* is a list of
        (label, kind) where kind is "primary"/"normal"/"danger"; returns the
        index of the clicked option, or None if dismissed. Replaces the
        oversized multi-button QMessageBox with clean stacked buttons. When
        *dont_ask_setting* is given, a "Don't show this again" checkbox is
        added; ticking it and choosing an option stores "true" under that key
        (so a caller can suppress the prompt next time; it's resettable in
        Settings).

        While the phone remote does something (mw_phone), the question is
        the phone's: asked there in the same words with the same choices,
        never left waiting on this screen with nobody at it. In TV mode it
        is TV mode's (mw_tv's _tv_ask): large, answered with the remote."""
        ask = getattr(self, "_phone_ask_hook", None)
        if ask is not None:
            return ask(title, message, options, dont_ask_setting)
        tv_ask = getattr(self, "_tv_ask", None)
        if getattr(self, "_tv_on", False) and callable(tv_ask):
            return tv_ask(title, message, options, dont_ask_setting)
        from PyQt6.QtWidgets import QCheckBox
        d = QDialog(self)
        d.setWindowTitle(title)
        d.setModal(True)
        d.setMinimumWidth(420)
        lay = QVBoxLayout(d)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(16)
        lbl = QLabel(message)
        lbl.setTextFormat(Qt.TextFormat.PlainText)      # it names a title or a channel
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"font-size:13px; color:{P['text2']};")
        lay.addWidget(lbl)
        btns = QVBoxLayout()
        btns.setSpacing(8)
        chosen: dict[str, int | None] = {"idx": None}
        for i, (label, kind) in enumerate(options):
            b = QPushButton(label)
            if kind == "primary":
                b.setObjectName("Primary")
            elif kind == "danger":
                b.setStyleSheet(
                    f"color:{P['rec']}; font-weight:600;")
            b.clicked.connect(
                lambda _c=False, i=i: (chosen.update(idx=i), d.accept()))
            btns.addWidget(b)
        lay.addLayout(btns)
        dont = None
        if dont_ask_setting:
            dont = QCheckBox(tr("dont_show_again"))
            dont.setStyleSheet(f"font-size:12px; color:{P['muted']};")
            lay.addWidget(dont)
        d.exec()
        if dont is not None and dont.isChecked() and chosen["idx"] is not None:
            self.settings.setValue(dont_ask_setting, "true")
        return chosen["idx"]

    def _guard_stream_switch(self, url: str, title: str) -> bool:
        """The docked player about to play *url*, from any way in (a zap,
        the phone remote, Home Assistant...): the recordings' question
        first, then the box's tuners (mw_connections). True to go on."""
        if not self._guard_rec_switch(url, title):
            return False
        tuner_ok = getattr(self, "_tuner_ok", None)
        return tuner_ok is None or tuner_ok(url)

    def _guard_rec_switch(self, url: str, title: str) -> bool:
        # A stream from the network (a SAT>IP server's RTSP too), not a file.
        if not url or not str(url).startswith(("http", "rtsp")):
            return True
        active = [j for j in self.rec.jobs if j["status"] == "recording"]
        # Back to a channel recorded in the background from its rewind
        # buffer: that buffer is still streaming, so watching it again
        # takes no other connection.
        active = [j for j in active
                  if not (j.get("background") and j.get("url") == url)]
        if not active:
            return True
        inplayer = [j for j in active if j.get("inplayer")]
        if not inplayer and getattr(self, "_multi_stream_ok", False):
            return True
        if inplayer:
            if self.player and url == self.player.current_url:
                return True
            j = inplayer[0]
            if self.rec.buffer_recording(j):
                return self._guard_buffer_switch(j, title)
            can_cont = bool(j.get("url"))
            options = [(tr("rec_guard_stop_switch"), "danger")]
            if can_cont:
                options.append((tr("rec_guard_switch_keep"), "normal"))
            options.append((tr("rec_guard_keep_watching"), "primary"))
            # Going on over a new connection: the recording's own and the
            # new channel's, where the player had one.
            over = self._one_more_over() if can_cont else None
            idx = self._choice_dialog(
                tr("rec_guard_title"),
                self._with_conn_warning(
                    tr("rec_guard_inplayer_body", recording=j["title"], target=title), over),
                options)
            if idx == 0:
                self.rec.finish_all_inplayer("stopped")
                return True
            if can_cont and idx == 1:
                self.rec.finish_all_inplayer(
                    "continued over a new connection")
                # Its source as the one in the player kept it: its count,
                # the VPN guard's rule and its route.
                self.rec.add_job(j["url"],
                                 tr("rec_continued_title", title=j["title"]),
                                 time.time(), j.get("stop"),
                                 icon=j.get("stream_icon"),
                                 channel=j.get("channel") or "",
                                 extra=self._rec_source_of(j))
                self._multi_stream_ok = True
                return True
            return False
        j = active[0]
        idx = self._choice_dialog(
            tr("rec_guard_title"),
            self._with_conn_warning(
                tr("rec_guard_separate_body", recording=j["title"], target=title),
                self._one_more_over(free_player=True)),
            [(tr("rec_guard_watch_recorded"), "primary"),
             (tr("rec_guard_stop_switch"), "danger"),
             (tr("rec_guard_play_anyway"), "normal"),
             (tr("common_cancel"), "normal")])
        if idx == 0:
            self._watch_recording_file(j)
            return False
        if idx == 1:
            for k in active:
                self.rec.cancel(k["id"])
            return True
        if idx == 2:
            self._multi_stream_ok = True
            return True
        return False

    def _watch_recording_file(self, j: dict, _tries: int = 0) -> None:
        # A just-started recording's file is created a beat after the job flips
        # to "recording": ffmpeg has to open the output and write the first
        # bytes. Picking "Watch the recorded channel" right then found no file
        # and silently gave up (the "doesn't start playing" report). Poll
        # briefly for the file to gain some data, then start playback - j is the
        # live job dict, so j["path"] fills in as the recorder sets it.
        path = j.get("path")
        if path and os.path.exists(path) and os.path.getsize(path) > 0:
            self._start_playback(
                path, tr("rec_recording_title", title=j["title"]), None,
                path, "recording", record=False)
            return
        if _tries < 20:                       # ~10 s at 500 ms steps
            QTimer.singleShot(
                500, lambda: self._watch_recording_file(j, _tries + 1))
            return
        QMessageBox.information(
            self, tr("rec_status_recording"),
            tr("msg_rec_file_not_ready"))

    def _rec_indicator_menu(self) -> None:
        m = QMenu(self)
        active = [j for j in self.rec.jobs if j["status"] == "recording"]
        for j in active:
            since = datetime.fromtimestamp(j["start"]).strftime("%H:%M")
            m.addAction(
                tr("rec_stop_named_since", title=j['title'], since=since),
                lambda jid=j["id"]: self.rec.cancel(jid))
        if active:
            m.addSeparator()
        m.addAction(tr("rec_open_recordings"),
                    lambda: self.switch_mode("rec"))
        m.exec(self.rec_indicator.mapToGlobal(
            self.rec_indicator.rect().bottomLeft()))

    def _sync_player_buttons(self) -> None:
        if not self.player:
            return
        it = self._playing_item
        live = bool(it) and it.get("stream_id") is not None
        ts = live and self._timeshift_days(it) > 0
        for b in (self.player.ts_btn, self.player.fs_ts_btn):
            b.setVisible(ts)
        for b in (self.player.rec_btn, self.player.fs_rec_btn):
            b.setVisible(live)

    def _player_timeshift_menu(self, anchor) -> None:
        it = self._playing_item
        if not it or not self._timeshift_days(it):
            return
        # Parent to the anchor's window: in the mirror pop-out the control bar
        # lives in another top-level window, and on Wayland a popup can't be
        # placed over a window it doesn't belong to (the menu never showed).
        m = QMenu(anchor.window())
        self._build_timeshift_menu(m, it)
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _player_record_menu(self, anchor) -> None:
        it = self._playing_item
        if not it or it.get("stream_id") is None:
            return
        m = QMenu(anchor.window())   # see _player_timeshift_menu
        self._build_record_menu(m, it)
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _rec_warn(self, title: str, text: str) -> None:
        """A recording that cannot start, said - on the phone remote when it
        asked (mw_phone), never a box left on this screen for nobody; in TV
        mode as TV mode asks (mw_tv's _tv_tell)."""
        ask = getattr(self, "_phone_ask_hook", None)
        if ask is not None:
            ask.note(text)
            return
        tell = getattr(self, "_tv_tell", None)
        if getattr(self, "_tv_on", False) and callable(tell):
            tell(title, text)
            return
        QMessageBox.warning(self, title, plain_text(text))

    def _recorder_ready(self) -> bool:
        if self.rec.recorder()[1]:
            return True
        self._rec_warn(tr("rec_status_recording"), tr("msg_rec_needs_ffmpeg"))
        return False

    @staticmethod
    def _fmt_gb(n: int) -> str:
        return f"{n / 10**9:.1f} GB"

    def _within_storage_cap(self) -> bool:
        """False (and warns) when the recordings folder has hit the user's
        total size limit, so no new recording is started."""
        if not self.rec.total_cap_exceeded():
            return True
        self._rec_warn(tr("rec_cap_title"),
                       tr("rec_cap_reached",
                          used=self._fmt_gb(self.rec.folder_used_bytes()),
                          cap=self._fmt_gb(self.rec.total_cap_bytes())))
        return False

    def _warn_pause_while_recording(self) -> None:
        """Paused while the player itself records (stream-record, without
        dopeIPTV's own buffer): mpv stops reading the channel shortly into a
        pause, and the recording stands still with it. Said once a
        recording, with where to turn on the buffer that records through."""
        p = self.player
        if p is None or not getattr(p, "_paused", False):
            return
        try:
            path = str(p.video.mpv["stream-record"] or "")
        except Exception:
            path = ""
        if not path or path == getattr(self, "_pause_warned_rec", ""):
            return
        self._pause_warned_rec = path
        self._show_toast(tr("rec_pause_not_recorded",
                            setting=tr("setting_dvr_kept"),
                            settings=tr("settings_title"),
                            page=tr("tab_playback")), 10000)

    def _one_more_over(self, free_player: bool = False):
        """core/connections.py's Verdict when one more connection would go
        over the account's limit (*free_player*: the player lets its
        stream go first) - None when not, or not known."""
        check = getattr(self, "_conn_verdict", None)
        uses = getattr(self, "_conn_uses", None)
        if check is None or uses is None:
            return None
        # A cast ends as the docked player starts (mw_cast), and never runs
        # next to a busy player: it is not one of the rest here.
        return check([u for u in uses(free_player=free_player) if u.kind != "cast"])

    def _with_conn_warning(self, body: str, verdict) -> str:
        add = getattr(self, "_conn_with_warning", None)
        return add(body, verdict) if add is not None and verdict is not None else body

    def _guard_buffer_switch(self, j: dict, title: str) -> bool:
        """Another channel, while the one on screen is recorded from its
        rewind buffer: stop, or let the recording go on in the background -
        its buffer keeps its connection, the new channel takes another. With
        an account that allows one connection, only stopping is offered."""
        one = getattr(self.client, "max_connections", None) == 1
        options = [(tr("rec_guard_stop_switch"), "danger")]
        if not one:
            options.append((tr("rec_guard_buffer_keep"), "normal"))
        options.append((tr("rec_guard_keep_watching"), "primary"))
        # The buffer goes on streaming for the recording; the new channel
        # takes one more.
        idx = self._choice_dialog(
            tr("rec_guard_title"),
            self._with_conn_warning(
                tr("rec_guard_buffer_one_body" if one else "rec_guard_buffer_body",
                   recording=j["title"], target=title),
                None if one else self._one_more_over()),
            options)
        if idx == 0:
            self.rec.finish_all_inplayer("stopped")
            return True
        if not one and idx == 1:
            self.rec.keep_in_background(j["id"])
            self._multi_stream_ok = True
            return True
        return False

    def _build_record_menu(self, rec_menu, it) -> None:
        active = [j for j in self.rec.jobs if j["status"] == "recording"]
        for j in active:
            rec_menu.addAction(
                "■ " + tr("rec_stop_recording") + f": {j['title']}",
                lambda jid=j["id"]: self.rec.cancel(jid))
        if active:
            rec_menu.addSeparator()
        rec_menu.addAction(tr("rec_record_now_until_stopped"),
                           lambda: self._record_now(it, None))
        for dur_key, mins in (("dur_30min", 30), ("dur_1h", 60),
                              ("dur_2h", 120), ("dur_4h", 240)):
            rec_menu.addAction(
                tr("rec_record_now_duration", duration=tr(dur_key)),
                lambda mins=mins: self._record_now(it, mins))
        rec_menu.addSeparator()
        rec_menu.addAction(tr("rec_schedule_recording"),
                           lambda: self._schedule_recording(it))
        rec_menu.addAction(tr("rec_manage_scheduled"),
                           lambda: self._show_scheduled_manager())
        cap_menu = rec_menu.addMenu(tr("rec_size_limit_session"))
        current = self.rec.session_cap
        presets = ((tr("rec_cap_from_settings"), None),
                   (tr("rec_cap_no_limit"), 0),
                   ("250 MB", 250 * 10**6),
                   ("500 MB", 500 * 10**6),
                   ("1 GB", 10**9), ("2 GB", 2 * 10**9),
                   ("5 GB", 5 * 10**9),
                   ("10 GB", 10 * 10**9),
                   ("50 GB", 50 * 10**9),
                   ("100 GB", 100 * 10**9))
        for label, cap in presets:
            act = cap_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(cap == current)
            act.triggered.connect(
                lambda _c, cap=cap: setattr(
                    self.rec, "session_cap", cap))
        cap_menu.addSeparator()
        known_caps = {cap for _label, cap in presets}
        custom_label = tr("rec_cap_custom")
        if current and current not in known_caps:
            custom_label = tr("rec_cap_custom_current",
                              size=format_size(current))
        custom_act = cap_menu.addAction(custom_label)
        custom_act.setCheckable(True)
        custom_act.setChecked(bool(current) and current not in known_caps)
        custom_act.triggered.connect(self._set_custom_rec_cap)

    def _set_custom_rec_cap(self) -> None:
        d = QDialog(self)
        d.setWindowTitle(tr("rec_custom_size_title"))
        d.setMinimumWidth(320)
        f = QFormLayout(d)
        f.setSpacing(10)
        row = QHBoxLayout()
        val_edit = QLineEdit()
        val_edit.setPlaceholderText(tr("size_value_placeholder"))
        unit_box = self._combo(
            [("MB", "MB"), ("GB", "GB"), ("TB", "TB")], "GB")
        row.addWidget(val_edit)
        row.addWidget(unit_box)
        f.addRow(tr("rec_stop_recording_at"), row)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        for b in buttons.buttons():
            b.setIcon(QIcon())
        buttons.accepted.connect(d.accept)
        buttons.rejected.connect(d.reject)
        f.addRow(buttons)
        if not d.exec():
            return
        try:
            val = float(val_edit.text().strip().replace(",", "."))
        except ValueError:
            return
        if val <= 0:
            return
        mult = {"MB": 10**6, "GB": 10**9, "TB": 10**12}[unit_box.currentData()]
        self.rec.session_cap = int(val * mult)

    def _rec_source(self) -> dict:
        """What a recording keeps of the source it is from: its id and its
        kind (the VPN guard's rule, and the address asked for at start,
        read them). {} without a source."""
        store = getattr(self, "playlist_store", None)
        pl = store.active() if store is not None else None
        if not pl:
            return {}
        return {"source": str(pl.get("id") or ""),
                "source_kind": str(pl.get("kind") or "xtream")}

    def _rec_player_source(self) -> dict:
        """What a recording of the player's own stream keeps of its source,
        as _rec_source does: the source it plays from (failover's, the
        Search page's, or one switched away from since it started)."""
        mine = getattr(self, "_conn_player_source", None)
        if not callable(mine):
            return self._rec_source()
        pid = str(mine() or "")
        store = getattr(self, "playlist_store", None)
        pl = store.get(pid) if store is not None and pid else None
        if not pl:
            return {}
        return {"source": pid, "source_kind": str(pl.get("kind") or "xtream")}

    @staticmethod
    def _rec_source_of(job: dict) -> dict:
        """The source a recording keeps (_rec_source), copied from *job*."""
        return {k: job[k] for k in ("source", "source_kind") if job.get(k)}

    def _rec_extra(self, ch: dict, later: bool | None = None) -> dict:
        """What a recording of channel *ch* keeps besides its address: its
        source and - from a client whose links are short-lived (a portal),
        *later* - what asking for the address when it starts takes."""
        from .mw_series import links_later
        out: dict = self._rec_source()
        if later is None:
            later = links_later(self.client)
        if later and ch.get("stream_id") is not None:
            out[RecordingManager.LATER] = {"source": out.get("source", ""),
                                           "stream_id": ch["stream_id"]}
        return out

    def _scheduled_address(self, ch: dict) -> tuple[str, dict]:
        """(address, extra) for a recording of *ch* that starts later: no
        address yet from a portal - its link would be long dead by then, and
        asking for it here is a create_link on the window's thread - but
        what asking for it at the start takes."""
        extra = self._rec_extra(ch)
        if RecordingManager.LATER in extra:
            return "", extra
        return self.client.live_url(ch["stream_id"], "ts") or "", extra

    def _rec_address_for(self, job: dict):
        """(window's thread) RecordingManager.address_for: how a recording
        that asks for its address as it starts gets it - a function the
        recorder runs off this thread - or None when its source is gone."""
        later = job.get(RecordingManager.LATER) or {}
        sid = later.get("stream_id")
        if sid is None:
            return None
        pid = str(later.get("source") or "")
        if not pid or pid == self._rec_source().get("source", ""):
            return partial(_live_address, self.client, sid)
        store = getattr(self, "playlist_store", None)
        pl = store.get(pid) if store is not None else None
        if pl is None:
            log.info("record: %s - its source is gone", for_log(job.get("title")))
            return None
        return partial(_source_address, dict(pl), sid)

    # -- a scheduled recording comes due (RecordingManager.may_open) --------------------

    def _rec_may_open(self, j: dict) -> str:
        """A scheduled recording is due and about to open a connection of
        its own: counted as one started by hand is. Its channel playing in
        the player: recorded from it - the rewind buffer, else the player's
        stream - no second connection. Room on the account (a known limit
        with room, or nothing of the app's on it) or the person's go ahead:
        opened. Otherwise held - never a second connection silently: it
        waits for room, and a note says so, with Record anyway."""
        if self._rec_plays(j) and self._rec_ride(j):
            return RecordingManager.RIDE
        if j.get("_go_ahead"):
            return RecordingManager.OPEN
        why = self._rec_held_why(j)
        if why is None:
            return RecordingManager.OPEN
        if not j.get("_held"):
            self._rec_note_held(j)
        return RecordingManager.HOLD

    def _rec_plays(self, j: dict) -> bool:
        """Whether recording *j*'s channel is what the docked player plays
        now: live, from the same source, not given up on - by its stream id
        there, or its address."""
        p = getattr(self, "player", None)
        if (p is None or not getattr(p, "current_url", None)
                or getattr(self, "_playing_group", None) != "live"
                or getattr(self, "_playing_catchup", False)):
            return False                # an archive's stream is not what airs now
        dead = getattr(self, "_conn_player_dead", None)
        if callable(dead) and dead(p):
            return False
        mine = str(self._rec_player_source().get("source") or "")
        theirs = str(j.get("source") or "")
        if mine and theirs and mine != theirs:
            return False                # one stream id names another channel there
        lp = getattr(self, "_last_playback", None) or {}
        url = str(j.get("url") or "")
        if url and url in (str(lp.get("url") or ""), str(p.current_url or "")):
            return True
        sid = j.get("stream_key") or (j.get(RecordingManager.LATER) or {}).get("stream_id")
        row_of = getattr(self, "_failover_row_for", None)
        row = (row_of(getattr(self, "_playing_key", None)) if callable(row_of) else None) \
            or getattr(self, "_playing_item", None) or {}
        playing = row.get("stream_id")
        return sid not in (None, "") and playing is not None and str(playing) == str(sid)

    def _rec_ride(self, j: dict) -> bool:
        """Record *j* from what the player plays (as a recording started by
        hand on the channel watched is): its rewind buffer from the
        recording's start, as far back as the buffer holds - else the
        player's own stream. True when it does."""
        p = self.player
        session = getattr(p, "_dvr", None)
        buffered = session is not None and bool(getattr(session, "streaming", False))
        if not buffered and any(k.get("inplayer") and k.get("status") == "recording"
                                and k.get("recorder") is None for k in self.rec.jobs):
            return False                # the player records one file of its stream at a time
        try:
            path = self.rec.build_path(str(j.get("title") or ""), j.get("folder") or "",
                                       j.get("channel") or "")
        except OSError as e:
            log.info("record: %s not from the player: %s", for_log(j.get("title")), e)
            return False
        recorder = None
        if buffered:
            try:
                span = session.span()
                lead = float(getattr(session, "KEY_LEAD", 0.0))
                since = (max(float(j.get("start") or 0.0), float(span[0]) + lead)
                         if span else None)
                recorder = session.record(path, since)
            except Exception as e:
                log.info("record: not from the rewind buffer (%s)", e)
                recorder = None
        if recorder is None:
            if not p.start_stream_record(path):
                return False
            take = getattr(p, "take_buffer_recorder", None)
            recorder = take() if callable(take) else None
        lp = getattr(self, "_last_playback", None) or {}
        self.rec.ride_player(j, path, recorder, url=str(lp.get("url") or ""))
        stop = j.get("stop")
        length = (tr("rec_until_stopped") if stop is None
                  else tr("rec_for_minutes", n=max(1, int((float(stop) - time.time()) // 60))))
        hub_of(self).post("● " + tr("rec_started_inplayer", title=j.get("title") or "",
                                     length=length), ms=6000)
        self._warn_pause_while_recording()
        return True

    def _rec_held_why(self, j: dict) -> tuple | None:
        """Why recording *j* over a connection of its own would be one too
        many now - (the account's Verdict or None, the box's tuners' Verdict
        or None, whether the account's limit is unknown while the app holds
        a connection to it already) - or None when there is room."""
        uses_of = getattr(self, "_conn_uses", None)
        by_account = getattr(self, "_conn_by_account", None)
        if not callable(uses_of) or not callable(by_account):
            return None
        url = str(j.get("url") or "")
        if url and not connections.is_network(url):
            return None                 # a file of this computer's
        src = str(j.get("source") or "") or self._conn_source()
        try:
            self._conn_seen()
            uses = list(uses_of())
            # Recordings being given their address open theirs a moment later.
            uses += [Use("recording", str(k.get("source") or ""))
                     for k in self.rec.jobs if k is not j
                     and k.get("status") == "scheduled" and k.get("_asking") is not None]
            listed, key = by_account(uses, src)
            own = connections.own_count(listed, key)
            cap = self._conn_cap(key)
            v = self._conn_ledger().check(key, listed, 1, limit=cap) if cap else None
        except Exception as e:          # counting must never stand in the way
            log.debug("record: the connections not counted: %s", e)
            return None
        tuner = getattr(self, "_tuner_verdict", None)
        t = tuner(url, uses) if url and callable(tuner) else None
        unknown = not cap and own > 0
        if v is None and t is None and not unknown:
            return None
        return v, t, unknown

    def _rec_note_held(self, j: dict) -> None:
        """Said once as recording *j* starts to wait: a note that offers to
        record anyway (_rec_go_ahead), the status line, the picture's corner
        in full screen; the Recordings list says it on its row."""
        title = str(j.get("title") or "")
        self._flash_status("● " + title + "  —  " + tr("rec_waiting_room"), 8000)
        toast = getattr(self, "_show_action_toast", None)
        if callable(toast):
            toast(tr("rec_held", title=title) + "\n" + tr("rec_held_click"), 15000,
                  partial(self._rec_go_ahead, str(j.get("id") or "")))
        if getattr(self, "_player_fs", False) and self.player:
            self.player.set_overlay_info(title + "  —  " + tr("rec_waiting_room"))

    def _rec_go_ahead(self, job_id: str) -> None:
        """Record anyway (the note, the row's menu): asked with what is in
        use; a yes opens the recording's own connection now."""
        j = next((k for k in self.rec.jobs if k.get("id") == job_id), None)
        if j is None or j.get("status") != "scheduled" or not j.get("_held"):
            return                      # started, cancelled or over meanwhile
        why = self._rec_held_why(j)
        if why is None:
            self.rec.tick()             # room now: it starts as it is
            return
        v, t, unknown = why
        parts = [tr("rec_held_what", title=str(j.get("title") or ""))]
        if v is not None:
            parts.append(self._conn_warning(v))
        elif unknown:
            parts.append(tr("conn_test_unknown"))
        if t is not None:
            parts.append(self._tuner_warning(t))
        idx = self._choice_dialog(
            tr("conn_title" if v is not None or unknown else "tuner_title"),
            "\n\n".join(parts),
            [(tr("conn_anyway"), "normal"), (tr("rec_held_wait"), "primary")])
        go = idx == 0 and j.get("status") == "scheduled"
        log.info("record: %s - %s", for_log(j.get("title")),
                 "over one more connection: the person said so" if go else "it waits")
        if go:
            j["_go_ahead"] = True
            self.rec.tick()

    def _record_now(self, it, minutes, title_override=None) -> None:
        if it.get("stream_id") is None:
            return
        if not self._within_storage_cap():
            return
        title = title_override or self.channel_display_name(it)
        now = time.time()
        stop_ts = None if minutes is None else now + minutes * 60
        length = (tr("rec_until_stopped") if minutes is None
                  else tr("rec_for_minutes", n=minutes))

        # The docked player shows what it plays - or TV mode's page is in
        # front of it, its stream going on behind (mw_tv): its stream either way.
        shows = self.player is not None and (
            self.player.isVisible() or getattr(self, "_tv_holds_picture", lambda: False)())
        watching_this = (
            shows
            and self.playback_mode() == "embedded"
            and self._playing_group == "live"
            and self._playing_key == self._item_key(it))
        if watching_this:
            try:
                path = self.rec.build_path(title, channel=self.channel_display_name(it))
            except OSError as e:
                self._rec_warn(tr("rec_status_recording"), str(e))
                return
            if self.player.start_stream_record(path):
                self.rec.add_inplayer_job(
                    title, path, stop_ts,
                    url=self._rec_inplayer_url(it),
                    icon=it.get("stream_icon"),
                    recorder=self.player.take_buffer_recorder(),
                    channel=self.channel_display_name(it),
                    extra=self._rec_player_source())
                hub_of(self).post("● " + tr("rec_started_inplayer",
                                             title=title, length=length), ms=6000)
                self._warn_pause_while_recording()
                return

        # Not the channel currently on screen. If the embedded player is
        # busy with something else, recording this one opens a SECOND
        # connection to the provider (many accounts allow only one), so
        # ask - offer to switch to it and record instead, mirroring the
        # switch-while-recording prompt.
        busy = (shows and self.playback_mode() == "embedded"
                and self._playing_key is not None)
        if busy:
            playing = (self._last_playback or {}).get("title") or ""
            # Recording in the background: a connection of its own next to
            # the player's.
            idx = self._choice_dialog(
                tr("rec_switch_title"),
                self._with_conn_warning(
                    tr("rec_switch_body", playing=playing, target=title),
                    self._one_more_over()),
                [(tr("rec_switch_and_record"), "primary"),
                 (tr("rec_record_background"), "normal"),
                 (tr("common_cancel"), "normal")])
            if idx == 0:
                self._switch_and_record(it, stop_ts, length)
                return
            if idx != 1:
                return  # cancel / dismissed

        if not self._recorder_ready():
            return
        # A media server opens the channel to give its address, a portal
        # asks create_link: off the window's thread first, as the player's.
        go = partial(self._record_in_background, it, title, now, stop_ts, length)
        later = getattr(self, "_links_later", None)
        if later is not None and later(partial(_window_live, self, it["stream_id"]), go,
                                       key=f"record-{it['stream_id']}"):
            return
        go()

    def _record_in_background(self, it, title: str, now: float, stop_ts, length: str) -> None:
        """A recording of *it* over a connection of its own, from now."""
        url = self.client.live_url(it["stream_id"], "ts")
        # A connection of its own from a box: a tuner of its own too.
        tuner_ok = getattr(self, "_tuner_ok", None)
        if tuner_ok is not None and not tuner_ok(url, self._conn_uses(), "tuner_what_rec"):
            return
        self.rec.add_job(url, title, now, stop_ts,
                         icon=it.get("stream_icon"),
                         channel=self.channel_display_name(it),
                         extra=self._rec_source())
        hub_of(self).post("● " + tr("rec_started_to", title=title,
                                     length=length,
                                     folder=self.rec.directory()), ms=6000)

    def _rec_inplayer_url(self, it) -> str:
        """The address a recording of the player's own stream keeps: the
        source's - unless that one is slow to get (a media server would
        open the channel once more for it, a portal ask create_link), or
        what plays is from another source than the one in use (whose
        client would give its own channel of that number): then the one
        the player was given."""
        given = str((getattr(self, "_last_playback", None) or {}).get("url") or "")
        mine = self._rec_player_source().get("source")
        if given and mine and mine != self._rec_source().get("source"):
            return given
        waits = getattr(self, "_link_waits", None)
        if waits is not None and waits(partial(_window_live, self, it["stream_id"])) is not None:
            return given
        return self.client.live_url(it["stream_id"], "ts")

    def _switch_and_record(self, it, stop_ts, length: str) -> None:
        """Switch the embedded player to this channel and record the
        stream we're now watching - one connection, no conflict."""
        # The address first, off the window's thread when it is slow to get:
        # then the player starts the channel at once, and it is what records.
        later = getattr(self, "_links_later", None)
        if later is not None and later(partial(_window_live, self, it["stream_id"]),
                                       partial(self._switch_and_record, it, stop_ts, length)):
            return
        title = self.channel_display_name(it)
        try:
            path = self.rec.build_path(title, channel=self.channel_display_name(it))
        except OSError as e:
            QMessageBox.warning(self, tr("rec_status_recording"), plain_text(str(e)))
            return
        self.play_live_channel(it)
        if self.player and self.player.start_stream_record(path):
            self.rec.add_inplayer_job(
                title, path, stop_ts,
                url=self._rec_inplayer_url(it),
                icon=it.get("stream_icon"),
                recorder=self.player.take_buffer_recorder(),
                extra=self._rec_player_source())
            hub_of(self).post("● " + tr("rec_started_inplayer",
                                         title=title, length=length), ms=6000)
            self._warn_pause_while_recording()
        elif self._recorder_ready():
            url = self.client.live_url(it["stream_id"], "ts")
            self.rec.add_job(url, title, time.time(), stop_ts,
                             icon=it.get("stream_icon"),
                             channel=self.channel_display_name(it),
                             extra=self._rec_source())
            hub_of(self).post("● " + tr("rec_started_to", title=title,
                                         length=length,
                                         folder=self.rec.directory()), ms=6000)

    def _schedule_recording(self, it) -> None:
        if not self._recorder_ready() or it.get("stream_id") is None:
            return
        if not self._within_storage_cap():
            return
        d = QDialog(self)
        d.setWindowTitle(tr("rec_schedule_recording").rstrip("."))
        d.setMinimumWidth(380)
        f = QFormLayout(d)
        f.setSpacing(10)
        name_edit = QLineEdit(self.channel_display_name(it))
        start_edit = QDateTimeEdit(QDateTime.currentDateTime())
        start_edit.setCalendarPopup(True)
        start_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        stop_edit = QDateTimeEdit(
            QDateTime.currentDateTime().addSecs(3600))
        stop_edit.setCalendarPopup(True)
        stop_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        folder_box = QComboBox()
        folder_box.addItem(tr("rec_folder_default"), "")
        for rel in self.rec.folders():
            folder_box.addItem(rel, rel)
        f.addRow(tr("playlist_name"), name_edit)
        f.addRow(tr("field_start"), start_edit)
        f.addRow(tr("field_stop"), stop_edit)
        f.addRow(tr("field_save_in"), folder_box)
        hint = QLabel(tr("rec_schedule_hint", folder=self.rec.directory()))
        hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        hint.setWordWrap(True)
        f.addRow(hint)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        f.addRow(bb)
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        start_ts = start_edit.dateTime().toSecsSinceEpoch()
        stop_ts = stop_edit.dateTime().toSecsSinceEpoch()
        if stop_ts <= start_ts or stop_ts <= time.time():
            QMessageBox.warning(
                self, tr("rec_schedule_recording").rstrip("."),
                tr("msg_stop_time_future"))
            return
        url, extra = self._scheduled_address(it)
        # Its channel, as a programme's recording keeps it: what plays is
        # recorded from the player when it is this channel (_rec_plays).
        extra.setdefault("stream_key", str(it["stream_id"]))
        title = (name_edit.text().strip()
                 or self.channel_display_name(it))
        self.rec.add_job(url, title, start_ts, stop_ts,
                         folder_box.currentData(),
                         icon=it.get("stream_icon"),
                         channel=self.channel_display_name(it),
                         extra=extra)
        hub_of(self).post(tr("rec_scheduled_for", title=title,
                              when=format_when(start_ts)), ms=6000)

    def _edit_job_times(self, job_id: str) -> None:
        job = next((j for j in self.rec.jobs if j["id"] == job_id), None)
        if not job or job["status"] != "scheduled":
            return
        d = QDialog(self)
        d.setWindowTitle(tr("msg_edit_time_title"))
        d.setMinimumWidth(380)
        f = QFormLayout(d)
        f.setSpacing(10)
        start_edit = QDateTimeEdit(
            QDateTime.fromSecsSinceEpoch(int(job["start"])))
        start_edit.setCalendarPopup(True)
        start_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        stop_edit = QDateTimeEdit(
            QDateTime.fromSecsSinceEpoch(int(job["stop"] or (
                job["start"] + 3600))))
        stop_edit.setCalendarPopup(True)
        stop_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        title_lbl = QLabel(job["title"])
        title_lbl.setTextFormat(Qt.TextFormat.PlainText)    # the guide's title
        f.addRow(tr("field_title"), title_lbl)
        f.addRow(tr("field_start"), start_edit)
        f.addRow(tr("field_stop"), stop_edit)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        f.addRow(bb)
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        start_ts = start_edit.dateTime().toSecsSinceEpoch()
        stop_ts = stop_edit.dateTime().toSecsSinceEpoch()
        if stop_ts <= start_ts:
            QMessageBox.warning(
                self, tr("msg_edit_time_title"),
                tr("msg_stop_after_start"))
            return
        self.rec.update_job_times(job_id, start_ts, stop_ts)

    def _selected_recordings(self, clicked_item=None) -> list[dict]:
        items = [self.list_model.item_at(ix.row())
                 for ix in self.listw.selectionModel().selectedRows()]
        items = [it for it in items if it and it.get("_path")
                 and it.get("_kind") == "recording"]
        if (not items and clicked_item
                and clicked_item.get("_path")
                and clicked_item.get("_kind") == "recording"):
            items = [clicked_item]
        return items

    def _remove_jobs_selected(self, clicked_item=None) -> None:
        items = [self.list_model.item_at(ix.row())
                 for ix in self.listw.selectionModel().selectedRows()]
        items = [it for it in items if it and it.get("_job")]
        if not items and clicked_item and clicked_item.get("_job"):
            items = [clicked_item]
        for it in items:
            if it.get("_status") == "recording":
                continue
            if it.get("_status") == "scheduled":
                self.rec.cancel(it["_job"])
            self.rec.remove_job(it["_job"])

    def _show_scheduled_manager(self) -> None:
        """A standalone panel listing the pending recordings (recording now +
        scheduled), soonest first, with Edit-times and Cancel right there - so
        upcoming recordings can be managed from the player or the EPG without
        hunting through the Recordings section's columns."""
        d = QDialog(self)
        d.setWindowTitle(tr("rec_scheduled_title"))
        d.setMinimumWidth(480)
        lay = QVBoxLayout(d)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(12)
        lst = QListWidget()
        lst.setStyleSheet("QListWidget { font-size: 13px; }")
        lay.addWidget(lst)
        empty = QLabel(tr("rec_no_upcoming"))
        empty.setStyleSheet(f"color:{P['muted']}; font-size:13px;")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(empty)
        row = QHBoxLayout()
        edit_btn = QPushButton(tr("rec_edit_times"))
        cancel_btn = QPushButton(tr("rec_cancel_scheduled"))
        cancel_btn.setStyleSheet(f"color:{P['rec']}; font-weight:600;")
        close_btn = QPushButton(tr("common_close"))
        row.addWidget(edit_btn)
        row.addWidget(cancel_btn)
        row.addStretch(1)
        row.addWidget(close_btn)
        lay.addLayout(row)

        def selected_job():
            it = lst.currentItem()
            jid = it.data(Qt.ItemDataRole.UserRole) if it else None
            if jid is None:
                return None
            return next((j for j in self.rec.jobs if j["id"] == jid), None)

        def refresh():
            lst.clear()
            pending = sorted(
                (j for j in self.rec.jobs
                 if j["status"] in ("recording", "scheduled")),
                key=lambda j: j.get("start") or 0)
            for j in pending:
                item = self._job_item(j)
                li = QListWidgetItem(item["name"])
                li.setData(Qt.ItemDataRole.UserRole, j["id"])
                lst.addItem(li)
            has = bool(pending)
            lst.setVisible(has)
            empty.setVisible(not has)
            if has:
                lst.setCurrentRow(0)
            _sync_buttons()

        def _sync_buttons():
            job = selected_job()
            # Times are only editable before it starts; cancel works for both a
            # scheduled job and one recording right now.
            edit_btn.setEnabled(bool(job) and job["status"] == "scheduled")
            cancel_btn.setEnabled(bool(job))

        def do_edit():
            job = selected_job()
            if job and job["status"] == "scheduled":
                self._edit_job_times(job["id"])

        def do_cancel():
            job = selected_job()
            if not job:
                return
            if confirm(d, tr("rec_scheduled_title"),
                       tr("rec_cancel_scheduled") + f"\n\n{job['title']}",
                       default_yes=False):
                self.rec.cancel(job["id"])

        lst.currentItemChanged.connect(lambda *_: _sync_buttons())
        lst.itemDoubleClicked.connect(lambda *_: do_edit())
        edit_btn.clicked.connect(do_edit)
        cancel_btn.clicked.connect(do_cancel)
        close_btn.clicked.connect(d.accept)
        # Keep the panel live as jobs start / finish / get cancelled while open.
        self.rec.jobs_changed.connect(refresh)
        d.finished.connect(
            lambda *_: self.rec.jobs_changed.disconnect(refresh))
        refresh()
        d.exec()

    def _delete_recordings_selected(self, clicked_item=None) -> None:
        # The cards just picked on the Recordings place are the list's now.
        mirror = getattr(self, "_rp_mirror", None)
        if mirror is not None and self._place_rec_active():
            mirror.settle()
        cur = self.cat_list.currentItem()
        # Upcoming lists recordings to come as Active & scheduled does: the
        # Delete key cancels and removes them there too (it did nothing).
        if cur and cur.data(Qt.ItemDataRole.UserRole) in ("__jobs__", "__upcoming__"):
            self._remove_jobs_selected(clicked_item)
            return
        items = self._selected_recordings(clicked_item)
        if not items:
            return
        what = (tr("rec_n_recordings", n=len(items)) if len(items) > 1
                else f"'{items[0]['name']}'")
        if not confirm(self, tr("msg_delete_rec_title"),
                       tr("msg_delete_rec_body", what=what)):
            return
        for it in items:
            try:
                os.remove(it["_path"])
                self.rec.drop_info(it["_path"])
                self._rec_watched().unmark(it["_path"])
                self.rec.prune_path(it["_path"])
            except OSError as e:
                self._set_status(tr("rec_delete_failed", error=e),
                                 error=True)
        cur = self.cat_list.currentItem()
        self._load_items(
            cur.data(Qt.ItemDataRole.UserRole) if cur else None)

    def _delete_all_recordings(self) -> None:
        """Delete every recording in the folder currently shown (all of them
        when 'All recordings' is selected). A single confirm, so a user with a
        cluttered Recordings section can clear it out without ticking each row."""
        cur = self.cat_list.currentItem()
        sel = cur.data(Qt.ItemDataRole.UserRole) if cur else None
        folder = "" if (sel in (None, "__all__", "__jobs__")) else sel
        items = [it for it in self.rec.files(
                     None if folder == "" else folder)
                 if it.get("_path")]
        if not items:
            return
        if not confirm(self, tr("msg_delete_rec_title"),
                       tr("msg_delete_rec_body",
                          what=tr("rec_n_recordings", n=len(items)))):
            return
        for it in items:
            try:
                os.remove(it["_path"])
                self.rec.drop_info(it["_path"])
                self._rec_watched().unmark(it["_path"])
                self.rec.prune_path(it["_path"])
            except OSError as e:
                self._set_status(tr("rec_delete_failed", error=e),
                                 error=True)
        self._load_items(sel)

    def _rename_recording(self, it) -> None:
        path = it.get("_path")
        if not path:
            return
        name, ok = QInputDialog.getText(
            self, tr("msg_rename_rec_title"), tr("cm_new_name"),
            text=it.get("name", ""))
        name = (safe_filename(name.strip())
                if ok and name.strip() else "")
        if not name:
            return
        new_path = free_path(os.path.join(
            os.path.dirname(path),
            name + os.path.splitext(path)[1]), current=path)
        try:
            os.rename(path, new_path)
            self.rec.move_info(path, new_path)   # keep the info sidecar attached
            self._rec_watched().moved(path, new_path)
        except OSError as e:
            QMessageBox.warning(self, tr("msg_rename_rec_title"), plain_text(str(e)))
        cur = self.cat_list.currentItem()
        self._load_items(
            cur.data(Qt.ItemDataRole.UserRole) if cur else None)

    def _edit_recording_info(self, it) -> None:
        """Let the user set a display title and description for a recording,
        stored in a sidecar next to the file (the filename stays untouched)."""
        path = it.get("_path")
        if not path:
            return
        info = self.rec.read_info(path)
        d = QDialog(self)
        d.setWindowTitle(tr("rec_info_title"))
        d.setMinimumWidth(420)
        lay = QVBoxLayout(d)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(8)
        lay.addWidget(QLabel(tr("rec_info_name")))
        title_edit = QLineEdit(info.get("title") or it.get("_filename", ""))
        lay.addWidget(title_edit)
        lay.addWidget(QLabel(tr("rec_info_desc")))
        from PyQt6.QtWidgets import QPlainTextEdit
        desc_edit = QPlainTextEdit(info.get("description", ""))
        desc_edit.setMinimumHeight(120)
        lay.addWidget(desc_edit)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(d.accept)
        btns.rejected.connect(d.reject)
        lay.addWidget(btns)
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        self.rec.write_info(path, title_edit.text(),
                            desc_edit.toPlainText())
        cur = self.cat_list.currentItem()
        self._load_items(
            cur.data(Qt.ItemDataRole.UserRole) if cur else None)

    def _move_recordings(self, items: list[dict],
                         folder: str) -> None:
        target = os.path.join(self.rec.directory(), folder)
        try:
            os.makedirs(target, exist_ok=True)
            for it in items:
                dest = free_path(
                    os.path.join(target, os.path.basename(it["_path"])),
                    current=it["_path"])
                shutil.move(it["_path"], dest)
                self.rec.move_info(it["_path"], dest)
                self._rec_watched().moved(it["_path"], dest)
        except OSError as e:
            QMessageBox.warning(self, tr("msg_move_rec_title"), plain_text(str(e)))
        self._load_categories()

    def _new_rec_folder(self, items=None) -> None:
        name, ok = QInputDialog.getText(
            self, tr("msg_new_folder_title"), tr("msg_folder_name"))
        name = (safe_filename(name.strip())
                if ok and name.strip() else "")
        if not name:
            return
        try:
            os.makedirs(
                os.path.join(self.rec.directory(), name),
                exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, tr("msg_new_folder_title"), plain_text(str(e)))
            return
        if items:
            self._move_recordings(items, name)
        else:
            self._load_categories()

    # -- EPG / replay time offsets ---------------------------------------------------

    def _epg_delay_minutes(self) -> int:
        try:
            base = int(self.settings.value("epg_delay_min", 0))
        except (TypeError, ValueError):
            base = 0
        # Nerd: a source's own guide shift on top (a provider whose guide
        # runs an hour off, say).
        from ..core import nerd
        if nerd.current().on:
            try:
                base += int(float((nerd.source() or {}).get("epg_shift") or 0))
            except (TypeError, ValueError):
                pass
        return base

    def _replay_delay_minutes(self) -> int:
        try:
            return int(self.settings.value("replay_delay_min", 0))
        except (TypeError, ValueError):
            return 0

    def _apply_epg_delay(self, dt):
        if dt is None:
            return None
        mins = self._epg_delay_minutes()
        return dt + timedelta(minutes=mins) if mins else dt

    # -- timeshift / catch-up ------------------------------------------------------

    def _timeshift_days(self, it) -> int:
        try:
            # A channel we've learned doesn't actually serve catch-up (the
            # provider set tv_archive but the archive URL returns HTML/live) is
            # no longer treated as timeshift, so its marker + menu disappear.
            if str(self._item_key(it)) in self._ts_broken_set():
                return 0
            if int(it.get("tv_archive") or 0):
                return int(it.get("tv_archive_duration") or 1) or 1
        except (TypeError, ValueError):
            pass
        return 0

    # How long a learned catch-up failure sticks before the channel is
    # re-tested - so a channel that gains a real archive later lights up again
    # on its own, without waiting for a manual refresh.
    TS_BROKEN_TTL = 14 * 86400   # 14 days

    def _ts_broken_map(self) -> dict:
        """{channel_key: failed_at_epoch} for this playlist. Loaded lazily and
        cached; persisted so it survives sessions."""
        pid = ((self.playlist_store.active() or {}).get("id", "")
               if self.playlist_store else "")
        if getattr(self, "_ts_broken_pid", None) != pid:
            raw = str(self.settings.value(f"ts_broken/{pid}", "") or "")
            m: dict = {}
            for tok in raw.split(","):
                if ":" in tok:
                    k, _, ts = tok.rpartition(":")
                    try:
                        m[k] = float(ts)
                    except ValueError:
                        pass
                elif tok:
                    m[tok] = 0.0   # legacy entry (no timestamp) - never expires
            self._ts_broken = m
            self._ts_broken_pid = pid
        return self._ts_broken

    def _ts_broken_set(self) -> set:
        """Channel keys still considered broken (failure within the TTL)."""
        now = time.time()
        return {k for k, ts in self._ts_broken_map().items()
                if ts == 0.0 or now - ts < self.TS_BROKEN_TTL}

    def _save_ts_broken(self) -> None:
        pid = getattr(self, "_ts_broken_pid", "")
        m = self._ts_broken
        self.settings.setValue(
            f"ts_broken/{pid}",
            ",".join(f"{k}:{int(ts)}" for k, ts in sorted(m.items())))

    def _mark_ts_broken(self, it) -> None:
        if it is None:
            return
        key = str(self._item_key(it))
        m = self._ts_broken_map()
        if key:
            m[key] = time.time()
            self._save_ts_broken()
            # Redraw so the amber ◀◀ marker drops immediately.
            self.listw.viewport().update()
            # If this is the channel on screen, drop the in-player timeshift
            # affordance too: _sync_player_buttons re-reads _timeshift_days
            # (now 0 for this key) and hides the control-bar ◀◀ button, so a
            # channel we just learned has no working archive stops advertising
            # it. The live video is untouched - this only toggles button
            # visibility.
            self._sync_player_buttons()

    # How much a "go back to the limit" request is pulled inside the stated
    # depth, how shallow a failing request must be to count as "no archive at
    # all" (learn-and-hide), and how long a learned real-depth cap sticks.
    TS_MARGIN_MIN = 10               # 10 min inside the deepest requestable point
    # Only a failure this shallow (~the smallest 'go back' step) means the
    # channel serves NO catch-up at all. Anything deeper failing just caps the
    # learned depth - it must never unmark a channel that works closer to live.
    TS_SHALLOW_MIN = 45              # <= 45 min back (the 30-min step + slack)
    TS_DEPTH_TTL = 14 * 86400        # a learned short depth re-expands after 14 d

    def _clear_ts_broken(self) -> None:
        """Forget learned catch-up failures (manual refresh / reset button) so
        the provider's tv_archive flags are trusted again."""
        pid = ((self.playlist_store.active() or {}).get("id", "")
               if self.playlist_store else "")
        self.settings.remove(f"ts_broken/{pid}")
        self.settings.remove(f"ts_depth/{pid}")
        self._ts_broken = {}
        self._ts_broken_pid = pid
        self._ts_depth = {}
        self._ts_depth_pid = pid
        if hasattr(self, "listw"):
            self.listw.viewport().update()

    # -- learned real archive depth ------------------------------------------
    # A provider's tv_archive_duration can overstate what it actually serves,
    # so a request near the stated limit fails. We remember the shallowest depth
    # (minutes back) that failed and only offer/allow less than that - without
    # unmarking the channel as timeshift. Stored with a timestamp so a transient
    # failure re-expands after a while.

    def _ts_depth_map(self) -> dict:
        """{channel_key: (fail_minutes, learned_at)} for this playlist."""
        pid = ((self.playlist_store.active() or {}).get("id", "")
               if self.playlist_store else "")
        if getattr(self, "_ts_depth_pid", None) != pid:
            raw = str(self.settings.value(f"ts_depth/{pid}", "") or "")
            m: dict = {}
            for tok in raw.split(","):
                parts = tok.split(":")
                if len(parts) >= 2 and parts[0]:
                    try:
                        mins = int(float(parts[1]))
                        ts = float(parts[2]) if len(parts) >= 3 else 0.0
                    except ValueError:
                        continue
                    m[parts[0]] = (mins, ts)
            self._ts_depth = m
            self._ts_depth_pid = pid
        return self._ts_depth

    def _save_ts_depth(self) -> None:
        pid = getattr(self, "_ts_depth_pid", "")
        self.settings.setValue(
            f"ts_depth/{pid}",
            ",".join(f"{k}:{v[0]}:{int(v[1])}"
                     for k, v in sorted(self._ts_depth.items())))

    def _learn_ts_maxdepth(self, it, minutes: int) -> None:
        """A catch-up request at *minutes* back failed: the real archive is
        shorter. Record it (only ever tightening) so the menu/requests shrink."""
        key = str(self._item_key(it))
        m = self._ts_depth_map()
        cur = m.get(key)
        if cur is None or minutes < cur[0]:
            m[key] = (int(minutes), time.time())
            self._save_ts_depth()
            if hasattr(self, "listw"):
                self.listw.viewport().update()

    def _confirm_ts_depth(self, it, minutes: int) -> None:
        """A catch-up request at *minutes* back worked: if a stale (shorter) cap
        was learned, the archive actually reaches this deep, so drop it."""
        key = str(self._item_key(it))
        m = self._ts_depth_map()
        cur = m.get(key)
        if cur is not None and minutes >= cur[0]:
            del m[key]
            self._save_ts_depth()

    def _effective_ts_minutes(self, it) -> int:
        """The deepest catch-up we'll actually request (minutes back): the
        provider's stated depth, capped by any learned-shorter real depth, and
        always pulled a margin inside so the very oldest edge isn't requested."""
        days = self._timeshift_days(it)
        if not days:
            return 0
        full = days * 1440
        cur = self._ts_depth_map().get(str(self._item_key(it)))
        if cur and (cur[1] == 0.0 or time.time() - cur[1] < self.TS_DEPTH_TTL):
            full = min(full, cur[0])
        return max(self.TS_MARGIN_MIN, full - self.TS_MARGIN_MIN)

    @staticmethod
    def _ts_provider_flagged(it) -> bool:
        """Whether the provider marks this channel as having catch-up
        (tv_archive), independent of any learned-broken state - i.e. a
        'timeshift channel' from the user's point of view."""
        try:
            return bool(it) and int(it.get("tv_archive") or 0) > 0
        except (TypeError, ValueError):
            return False

    def _reset_channel_timeshift(self, it) -> None:
        """Forget a single channel's learned catch-up failure and short-depth
        cap, so its ◀◀ marker, full menu and archive depth come back. Used from
        the live channel's context menu."""
        key = str(self._item_key(it))
        m = self._ts_broken_map()
        if key in m:
            del m[key]
            self._save_ts_broken()
        dm = self._ts_depth_map()
        if key in dm:
            del dm[key]
            self._save_ts_depth()
        if hasattr(self, "listw"):
            self.listw.viewport().update()
        self._flash_status(tr("ts_reset_done_one"))

    def _play_timeshift(self, it, back_min=None, prog=None,
                        prog_origin=None, _depth_retry=False) -> None:
        # prog_origin: the picked programme's *original* start (the seek-bar's
        # 0-point). Set when re-loading the archive at a scrubbed position so
        # the bar keeps spanning the whole programme with the playhead offset,
        # instead of restarting the bar at each seek. Defaults to the segment
        # start for a fresh programme pick.
        sid = it.get("stream_id")
        days = self._timeshift_days(it)
        if sid is None or not days:
            return
        now = time.time()
        if prog:
            start = prog["start_timestamp"]
            what = prog.get("title") or tr("ts_programme")
        else:
            start = now - (back_min or 30) * 60
            what = None
        # Clamp to the effective archive depth: the provider's stated depth,
        # capped by any learned-shorter real depth, and a margin inside the
        # oldest edge (providers drop the last few minutes, so a request at the
        # exact stated limit lands just past the end and fails).
        floor = now - self._effective_ts_minutes(it) * 60
        # A failure on a request *near the limit* only means that depth isn't
        # available - it must NOT unmark a working channel. Only a shallow,
        # un-clamped request failing implies no catch-up at all (learn-and-hide).
        clamped = start < floor - 1
        start = max(start, floor)
        requested_back_min = (now - start) / 60.0
        if prog:
            duration_min = max(
                1, int((prog["stop_timestamp"] - start) // 60) + 2)
        else:
            duration_min = max(1, int((now - start) // 60) + 1)
        # Only a shallow "go back N" that fails means the channel serves no
        # catch-up at all. A programme pick / scrub is never allowed to unmark
        # a channel (a picked-programme URL can fail on a channel whose plain
        # archive works fine), and a retry never unmarks either.
        allow_mark_broken = (not _depth_retry and prog is None
                             and not clamped
                             and requested_back_min <= self.TS_SHALLOW_MIN)
        start += self._replay_delay_minutes() * 60
        if getattr(self.client, "archive_by_programme", False):
            # A portal (StalkerClient) keeps its archive by programme, and
            # asking for a link takes the network (mw_portal_archive).
            self._play_portal_archive(it, start, prog, prog_origin)
            return
        # Candidate archive-URL formats (providers differ). Play the first;
        # _playback_error walks to the next on an early failure so we auto-pick
        # whichever scheme this provider actually serves.
        urls = self.client.timeshift_urls(
            sid, datetime.fromtimestamp(start), duration_min)
        # Nerd: the source's own catch-up address goes first; the app's
        # own still follow it, so a template that does not answer with a
        # stream costs nothing but the try.
        own = self._nerd_catchup_url(it, start, start + duration_min * 60)
        if own:
            urls = [own] + [u for u in (urls or []) if u != own]
        if not urls:
            return
        log.debug("[ts] probe back_min=%s prog=%s days=%s start=%s "
                  "candidates=%s", back_min, bool(prog), days,
                  datetime.fromtimestamp(start), urls)
        name = self.channel_display_name(it)
        title = (tr("ts_title_programme", what=what, name=name) if what
                 else tr("ts_title_channel", name=name))
        key = self._item_key(it)
        # Probe the candidate URLs over HTTP in the background - the live video
        # keeps playing untouched. Only when a candidate is confirmed to serve
        # real video do we switch the player to it (one clean swap); if none do,
        # the channel is marked as having no working catch-up and the user just
        # gets a status message, with no stutter.
        # The check is work the window does: a job of its own (a newer
        # check replaces it), ended with its answer.
        hub_of(self).start(tr("ts_checking"), key="ts_check", kind="guide", mine=True,
                           timeout_ms=60_000)
        probe_token = getattr(self, "_ts_probe_token", 0) + 1
        self._ts_probe_token = probe_token

        def done(res):
            if probe_token != getattr(self, "_ts_probe_token", 0):
                return   # a newer play/probe superseded this one
            hub_of(self).end_key("ts_check")
            url, proven_dead = (res if isinstance(res, tuple)
                                else (res, True))
            if not url:
                if not proven_dead:
                    # Every candidate failed at the NETWORK level (timeout,
                    # DNS, TLS, refused) - the provider never answered, so
                    # this proves nothing about the channel's archive. Say so
                    # and leave the learned marks/depths untouched; the next
                    # attempt starts clean.
                    self._flash_status(tr("ts_check_failed"), ms=6000)
                elif allow_mark_broken:
                    # A shallow request failed: the provider serves no catch-up.
                    self._mark_ts_broken(it)
                else:
                    # A deep request failed: the archive is shorter than stated.
                    # Learn the real limit (keeping the channel as timeshift),
                    # then retry one step shallower so a single click still lands
                    # at the deepest point that works - instead of dumping the
                    # user to live with no seek bar.
                    self._learn_ts_maxdepth(it, int(round(requested_back_min)))
                    if not prog:
                        nxt = self._next_shallower_step(
                            int(round(requested_back_min)))
                        if nxt is not None:
                            self._flash_status(tr("ts_shorter_archive"))
                            self._play_timeshift(it, back_min=nxt,
                                                 _depth_retry=True)
                            return
                        # Walked the retry chain all the way down to the
                        # shallowest step (30 min) and even that serves no
                        # archive: the provider advertises catch-up it doesn't
                        # actually have. Hide it, same as a shallow failure -
                        # 30 min back is well within any real archive, so this
                        # can't wrongly hide a channel that only has a shallow
                        # window (that path returns a URL and never gets here).
                        self._mark_ts_broken(it)
                if proven_dead:
                    self._flash_status(tr("ts_archive_unavailable"), ms=6000)
                # The live video never stopped. Restore the live timeline (not
                # plain live) so the user can still scrub to a shallower point
                # without restarting the channel.
                if self.player:
                    self._playing_catchup = False
                    if self._timeshift_days(it):
                        self._apply_seek_mode(it, "live")
                    else:
                        self.player.set_seek_mode("live")
                        self.player.set_live_badge(None)
                return
            # Archive reaches this depth after all - drop any stale short cap.
            self._confirm_ts_depth(it, int(round(requested_back_min)))
            self._ts_candidates = [url]
            self._ts_candidate_idx = 0
            self._ts_candidate_started = time.monotonic()
            self._ts_segment_start = start
            self._ts_catchup_program = bool(prog)
            self._ts_from_portal = False
            # Remember the programme's span so the seek bar can be clamped to
            # just this programme (the provider's archive URL runs on to the
            # live edge). _ts_program_start is the bar's 0-point; it stays fixed
            # across scrub-reloads (prog_origin) so the playhead moves inside the
            # same bar instead of the bar restarting each seek.
            if prog:
                self._ts_program_start = (
                    prog_origin if prog_origin is not None
                    else prog.get("start_timestamp"))
                self._ts_program_stop = prog.get("stop_timestamp")
                self._ts_program_title = prog.get("title")
            else:
                self._ts_program_start = None
                self._ts_program_stop = None
                self._ts_program_title = None
            self._start_playback(url, title, it.get("stream_icon"), key,
                                 "live", record=False, item=it, catchup=True)

        run_async(self.pool,
                  lambda u=list(urls): self._probe_ts_candidates(u),
                  # A crashed probe proves nothing either - never mark on it.
                  done, lambda _e: done((None, False)))

    def _nerd_catchup_url(self, it, start: float, end: float) -> str | None:
        """Nerd: the active source's own catch-up address (Add/Edit source >
        Nerd options) for a programme from *start* to *end* (epoch seconds) -
        None, and nothing changes, while Nerd is off or the source asks for
        nothing of its own."""
        from ..core import catchup, nerd
        src = nerd.source()
        if not catchup.wanted(src):
            return None
        try:
            fmt = str(self.settings.value("stream_format", "ts") or "ts")
            live = self.client.live_url(it.get("stream_id"), fmt) or ""
            url = catchup.for_programme(
                src, live, datetime.fromtimestamp(start, timezone.utc),
                datetime.fromtimestamp(end, timezone.utc),
                catchup_id=str(it.get("catchup_id") or ""),
                channel_source=str(it.get("catchup_source") or ""))
        except Exception as e:
            log.info("[ts] the source's catch-up address could not be made: %s", e)
            return None
        if url:
            log.debug("[ts] the source's own catch-up address first: %s", redact_url(url))
        return url

    def _probe_ts_candidates(self, urls) -> tuple:
        """(worker thread) Return (url, proven_dead): the first candidate URL
        that actually serves video, or (None, flag). Filters out the HTML
        error pages / redirects a provider hands back for channels it doesn't
        really archive - so we never churn the player through dead URLs.

        proven_dead only goes True when the provider RESPONDED with something
        that is not a stream (HTML/JSON error page, wrong content). A timeout,
        DNS/TLS or connection error says nothing about whether the channel has
        an archive, so those must never count as proof - a flaky link once
        marked working channels as broken for 14 days, silently."""
        sess = getattr(self.client, "session", None)
        proven_dead = False
        network_failed = False
        for u in urls:
            try:
                # Generous read timeout: providers assemble archive segments
                # on demand and the first byte can take a long while cold -
                # the probe runs in the background while live keeps playing,
                # so waiting costs nothing visible.
                r = sess.get(u, stream=True, timeout=(5, 25),
                             headers={"Range": "bytes=0-8191"})
                ctype = (r.headers.get("Content-Type") or "").lower()
                chunk = next(r.iter_content(4096), b"") or b""
                r.close()
            except Exception as e:
                log.debug("[ts] probe network error for %s: %s",
                          redact_url(u), e)
                network_failed = True
                continue
            head = chunk.lstrip()
            if "text/html" in ctype or head[:1] in (b"<", b"{"):
                proven_dead = True   # a real answer: this URL is not a stream
                continue
            if (chunk[:1] == b"\x47"                 # MPEG-TS sync byte
                    or head[:7] == b"#EXTM3U"        # HLS playlist
                    or "mpegurl" in ctype
                    or "video" in ctype
                    or "octet-stream" in ctype):
                return u, False
            proven_dead = True       # responded, but with something unplayable
        # Dead is only proven when EVERY candidate got a real answer. If any
        # format failed at the network level, the working scheme may simply
        # have been the one that timed out - that must not hide the channel.
        return None, proven_dead and not network_failed

    # (minutes back, duration i18n key) - the label is "Go back <duration>".
    TIMESHIFT_STEPS = (
        (30, "dur_30min"), (60, "dur_1h"), (120, "dur_2h"), (360, "dur_6h"),
        (720, "dur_12h"), (1440, "dur_1d"), (2880, "dur_2d"),
        (4320, "dur_3d"), (7200, "dur_5d"), (10080, "dur_7d"),
    )

    def _next_shallower_step(self, minutes: int):
        """The largest 'go back' step strictly shallower than *minutes* (used to
        fall back one level when a deeper request turns out to exceed the real
        archive). None when there's nothing shallower."""
        lower = [s for s, _ in self.TIMESHIFT_STEPS if s < minutes]
        return max(lower) if lower else None

    def _build_timeshift_menu(self, ts_menu, it) -> None:
        days = self._timeshift_days(it)
        eff_min = self._effective_ts_minutes(it)
        ts_menu.addAction(tr("ts_go_live"),
                          lambda: self.play_live_channel(it))
        ts_menu.addSeparator()
        prog = self.xmltv.current_programme(it)
        if prog:
            ts_menu.addAction(
                tr("ts_watch_from_start_named", title=prog['title']),
                lambda: self._play_timeshift(it, prog=prog))
        ts_menu.addAction(tr("ts_browse_past"),
                          lambda: self._open_catchup_dialog(it))
        ts_menu.addSeparator()
        # Only offer depths the archive can actually reach (stated depth, capped
        # by any learned-shorter real depth), so "go back 3 days" never appears
        # for an archive that only really serves ~1 day.
        for mins, dur_key in self.TIMESHIFT_STEPS:
            if mins > eff_min:
                break
            ts_menu.addAction(
                tr("ts_go_back", t=tr(dur_key)),
                lambda mins=mins: self._play_timeshift(it, back_min=mins))
        note = ts_menu.addAction(tr("ts_archive_depth", n=days))
        note.setEnabled(False)

    def _open_catchup_dialog(self, it, on_pick=None) -> None:
        """Pick a past programme. *on_pick* decides what happens to it - the
        player by default, the Chromecast when a cast is what is running."""
        days = self._timeshift_days(it)
        if not days:
            return
        d = QDialog(self)
        d.setWindowTitle(
            tr("ts_catchup_title", name=self.channel_display_name(it)))
        d.setMinimumSize(480, 500)
        lay = QVBoxLayout(d)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)
        info = QLabel(tr("ts_loading_past"))
        info.setWordWrap(True)
        lay.addWidget(info)
        lst = QListWidget()
        lay.addWidget(lst, 1)
        btns = QHBoxLayout()
        watch_btn = QPushButton(tr("common_watch"), objectName="Primary")
        close_btn = QPushButton(tr("common_close"))
        btns.addStretch()
        btns.addWidget(watch_btn)
        btns.addWidget(close_btn)
        lay.addLayout(btns)
        close_btn.clicked.connect(d.reject)

        def watch(_item=None):
            cur = lst.currentItem()
            p = cur.data(Qt.ItemDataRole.UserRole) if cur else None
            if p:
                (on_pick or (lambda prog: self._play_timeshift(it,
                                                               prog=prog)))(p)
                d.accept()

        watch_btn.clicked.connect(watch)
        lst.itemDoubleClicked.connect(watch)

        def fetch():
            progs = self.xmltv.past_programmes(it, days)
            if progs or it.get("stream_id") is None:
                return progs
            now = time.time()
            out = []
            # A portal lists its archive by day (StalkerClient.archive_table);
            # elsewhere the provider's own table is all there is.
            table = getattr(self.client, "archive_table", None)
            rows = (table(it["stream_id"], days) if table is not None
                    else self.client.epg_table(it["stream_id"]))
            for e in rows:
                start, stop = epg_times(e)
                start, stop = (self._apply_epg_delay(start),
                              self._apply_epg_delay(stop))
                if not start or not stop:
                    continue
                start_ts, stop_ts = start.timestamp(), stop.timestamp()
                if stop_ts <= now and start_ts >= now - days * 86400:
                    out.append({
                        "title": b64(e.get("title")) or "?",
                        "start_timestamp": int(start_ts),
                        "stop_timestamp": int(stop_ts),
                    })
            out.sort(key=lambda p: p["start_timestamp"], reverse=True)
            return out

        def done(progs):
            if not progs:
                info.setText(tr("ts_no_past_programmes"))
                return
            info.setText(tr("ts_past_programmes", n=len(progs), days=days))
            last_day = None
            for p in progs:
                start = datetime.fromtimestamp(p["start_timestamp"])
                stop = datetime.fromtimestamp(p["stop_timestamp"])
                day = format_when(start, "dddd d MMMM")
                if day != last_day:
                    last_day = day
                    head = QListWidgetItem(f"—  {day}  —")
                    head.setFlags(Qt.ItemFlag.NoItemFlags)
                    lst.addItem(head)
                row = QListWidgetItem(
                    f"{start.strftime('%H:%M')}–"
                    f"{stop.strftime('%H:%M')}   "
                    f"{p.get('title') or '?'}")
                row.setData(Qt.ItemDataRole.UserRole, p)
                lst.addItem(row)

        run_async(self.pool, fetch, done,
                  lambda e: info.setText(tr("ts_guide_failed", error=e)))
        d.exec()

    # -- recording context menu ----------------------------------------------------

    def _rec_context_menu(self, pos, it, at=None) -> None:
        """A recording's menu, or a recording to come's - at the list's
        *pos*, or at the screen's point *at* (a card of the Recordings
        place)."""
        m = QMenu(self)
        if it.get("_kind") == "recjob":
            status = it.get("_status")
            if it.get("_path"):
                m.addAction(tr("common_watch"), lambda: self.play_item(it))
            if status == "recording":
                m.addAction(tr("rec_stop_recording"),
                            lambda: self.rec.cancel(it["_job"]))
            elif status == "scheduled":
                if it.get("_held"):
                    m.addAction(tr("rec_record_anyway"),
                                partial(self._rec_go_ahead, it["_job"]))
                m.addAction(tr("rec_edit_times"),
                            lambda: self._edit_job_times(it["_job"]))
                m.addAction(tr("rec_cancel_scheduled"),
                            lambda: self.rec.cancel(it["_job"]))
            else:
                m.addAction(tr("rec_remove_from_list"),
                            lambda: self._remove_jobs_selected(it))
            m.addSeparator()
            m.addAction(tr("rec_clear_finished"),
                        lambda: self.rec.clear_finished())
        else:
            items = self._selected_recordings(it)
            many = len(items) > 1
            m.addAction(tr("ctx_play_in_mpv"),
                        lambda: self.play_item(it, "mpv"))
            m.addAction(tr("ctx_play_in_vlc"),
                        lambda: self.play_item(it, "vlc"))
            m.addSeparator()
            # Watched, as its card says (core/rec_view.WatchedMarks).
            m.addAction(tr("rec_mark_unwatched") if self._rec_watched().has(it.get("_path") or "")
                        else tr("rec_mark_watched"),
                        partial(self._rec_toggle_watched, it))
            m.addAction(tr("btn_open_folder"),
                        partial(self._rec_open_folder_of, it))
            m.addSeparator()
            m.addAction(tr("ctx_rename"),
                        lambda: self._rename_recording(it))
            m.addAction(tr("rec_edit_info"),
                        lambda: self._edit_recording_info(it))
            move = m.addMenu(
                tr("ctx_move_to") if not many
                else tr("rec_move_n", n=len(items)))
            move.addAction(
                tr("rec_move_root"),
                lambda: self._move_recordings(items, ""))
            for rel in self.rec.folders():
                move.addAction(
                    rel,
                    lambda rel=rel: self._move_recordings(items, rel))
            move.addSeparator()
            move.addAction(tr("ctx_new_folder"),
                           lambda: self._new_rec_folder(items))
            m.addAction(
                tr("ctx_delete") if not many
                else tr("rec_delete_n", n=len(items)),
                lambda: self._delete_recordings_selected(it))
            m.addAction(tr("rec_delete_all"),
                        lambda: self._delete_all_recordings())
        m.addSeparator()
        m.addAction(tr("ctx_new_folder"), lambda: self._new_rec_folder())
        m.addAction(tr("rec_change_folder"),
                    lambda: self._choose_rec_dir())
        m.exec(at if at is not None else self.listw.viewport().mapToGlobal(pos))

    def _rec_open_folder_of(self, it) -> None:
        """The folder a recording is in, in the system's file manager."""
        from pathlib import Path
        from ..core.xdg import open_url
        folder = os.path.dirname(str(it.get("_path") or "")) or self.rec.directory()
        if os.path.isdir(folder):
            open_url(Path(folder).as_uri())
