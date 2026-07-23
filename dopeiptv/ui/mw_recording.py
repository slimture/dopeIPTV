"""Extracted from main_window.py (mixin); MainWindow inherits this.

Verbatim move - self.* access and behaviour unchanged.
"""

from __future__ import annotations

import os
import shutil
import time
from ..providers.client import b64, epg_times
from ..i18n import tr
from ..core.log import log
from ..core.recording import format_size, safe_filename
from .theme import P
from .widgets import confirm
from ..core.workers import run_async
from PyQt6.QtCore import QDateTime, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QVBoxLayout
from datetime import datetime, timedelta


class _RecordingMixin:
    """MainWindow mixin: recording, scheduling and timeshift/catch-up controls."""
    def _job_item(self, j: dict) -> dict:
        label = {"recording": "● REC",
                 "scheduled": tr("rec_status_scheduled"),
                 "done": tr("rec_status_done"),
                 "failed": tr("rec_status_failed"),
                 "cancelled": tr("rec_status_cancelled")}.get(
                     j["status"], j["status"])
        start = datetime.fromtimestamp(
            j["start"]).strftime("%a %d %b %H:%M")
        stop = ("until stopped" if j.get("stop") is None
                else datetime.fromtimestamp(j["stop"]).strftime("%H:%M"))
        return {"name": f"[{label}] {j['title']}  ({start} – {stop})",
                "_job": j["id"], "_key": f"job:{j['id']}",
                "_kind": "recjob", "_status": j["status"],
                "_error": j.get("error") or "",
                "_path": j.get("path") or "",
                # The recorded channel's logo, so the row isn't iconless.
                "stream_icon": j.get("stream_icon") or ""}

    def _recordings_changed(self) -> None:
        n = self.rec.active_count()
        self.rec_indicator.setText(
            f"● REC ({n})" if n > 1 else "● REC")
        self.rec_indicator.setVisible(n > 0)
        if self.player:
            for b in (self.player.rec_btn, self.player.fs_rec_btn):
                b.setToolTip(f"Recording ({n})" if n else "Record")
                b.setStyleSheet(
                    "color:#FF5C5C; font-weight:700;" if n
                    else "color:#FF5C5C;")
        if self.mode == "rec":
            cur = self.cat_list.currentItem()
            self._load_items(
                cur.data(Qt.ItemDataRole.UserRole) if cur else None)
        elif n:
            # Transient only - the persistent "● REC" pill is the lasting
            # indicator. A resting _set_status here used to linger as
            # "Recording N streams" long after recording had stopped.
            self._flash_status(
                f"● Recording {n} stream{'s' if n > 1 else ''}…")

    def _on_recording_stopped(self, title: str, reason: str) -> None:
        self._flash_status(f"● Recording stopped: {title} ({reason})")
        if self._player_fs and self.player:
            self.player.set_overlay_info(
                f"Recording stopped: {title} ({reason})")

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
        Settings)."""
        from PyQt6.QtWidgets import QCheckBox
        d = QDialog(self)
        d.setWindowTitle(title)
        d.setModal(True)
        d.setMinimumWidth(420)
        lay = QVBoxLayout(d)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(16)
        lbl = QLabel(message)
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
        if not url or not str(url).startswith("http"):
            return True
        active = [j for j in self.rec.jobs if j["status"] == "recording"]
        if not active:
            return True
        inplayer = [j for j in active if j.get("inplayer")]
        if not inplayer and getattr(self, "_multi_stream_ok", False):
            return True
        if inplayer:
            if self.player and url == self.player.current_url:
                return True
            j = inplayer[0]
            can_cont = bool(j.get("url"))
            options = [("Stop recording and switch", "danger")]
            if can_cont:
                options.append(
                    ("Switch, keep recording on a new connection", "normal"))
            options.append(("Keep watching (recording continues)", "primary"))
            idx = self._choice_dialog(
                "Recording in progress",
                f"“{j['title']}” is being recorded from the stream you're "
                f"watching. Switching to “{title}” will stop that recording, "
                "unless you continue it over a second connection (needs a "
                "multi-stream account).",
                options)
            if idx == 0:
                self.rec.finish_all_inplayer("stopped")
                return True
            if can_cont and idx == 1:
                self.rec.finish_all_inplayer(
                    "continued over a new connection")
                self.rec.add_job(j["url"], f"{j['title']} (cont.)",
                                 time.time(), j.get("stop"),
                                 icon=j.get("stream_icon"))
                self._multi_stream_ok = True
                return True
            return False
        j = active[0]
        idx = self._choice_dialog(
            "Recording in progress",
            f"“{j['title']}” is being recorded over its own connection. If "
            f"your account only allows one stream at a time, starting "
            f"“{title}” can kill that recording.",
            [("Watch the recorded channel", "primary"),
             ("Stop recording and switch", "danger"),
             ("Play anyway (I have multiple streams)", "normal"),
             ("Cancel", "normal")])
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

    def _watch_recording_file(self, j: dict) -> None:
        path = j.get("path")
        if not path or not os.path.exists(path):
            QMessageBox.information(
                self, tr("rec_status_recording"),
                tr("msg_rec_file_not_ready"))
            return
        self._start_playback(path, f"{j['title']} (recording)", None,
                             path, "recording", record=False)

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
        m = QMenu(self)
        self._build_timeshift_menu(m, it)
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _player_record_menu(self, anchor) -> None:
        it = self._playing_item
        if not it or it.get("stream_id") is None:
            return
        m = QMenu(self)
        self._build_record_menu(m, it)
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _recorder_ready(self) -> bool:
        if self.rec.recorder()[1]:
            return True
        QMessageBox.warning(
            self, tr("rec_status_recording"),
            tr("msg_rec_needs_ffmpeg"))
        return False

    @staticmethod
    def _fmt_gb(n: int) -> str:
        return f"{n / 10**9:.1f} GB"

    def _within_storage_cap(self) -> bool:
        """False (and warns) when the recordings folder has hit the user's
        total size limit, so no new recording is started."""
        if not self.rec.total_cap_exceeded():
            return True
        QMessageBox.warning(
            self, tr("rec_cap_title"),
            tr("rec_cap_reached",
               used=self._fmt_gb(self.rec.folder_used_bytes()),
               cap=self._fmt_gb(self.rec.total_cap_bytes())))
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
        presets = (("From Settings", None), ("No limit", 0),
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
        custom_label = "Custom size..."
        if current and current not in known_caps:
            custom_label = f"Custom size... (currently {format_size(current)})"
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

    def _record_now(self, it, minutes, title_override=None) -> None:
        if it.get("stream_id") is None:
            return
        if not self._within_storage_cap():
            return
        title = title_override or self.channel_display_name(it)
        now = time.time()
        stop_ts = None if minutes is None else now + minutes * 60
        length = ("until stopped" if minutes is None
                  else f"for {minutes} min")

        watching_this = (
            self.player is not None
            and self.player.isVisible()
            and self.playback_mode() == "embedded"
            and self._playing_group == "live"
            and self._playing_key == self._item_key(it))
        if watching_this:
            try:
                path = self.rec.build_path(title)
            except OSError as e:
                QMessageBox.warning(self, tr("rec_status_recording"), str(e))
                return
            if self.player.start_stream_record(path):
                self.rec.add_inplayer_job(
                    title, path, stop_ts,
                    url=self.client.live_url(it["stream_id"], "ts"),
                    icon=it.get("stream_icon"))
                self._set_status(
                    f"● Recording {title} {length} - capturing "
                    "the stream you're watching (no extra connection)")
                return

        # Not the channel currently on screen. If the embedded player is
        # busy with something else, recording this one opens a SECOND
        # connection to the provider (many accounts allow only one), so
        # ask - offer to switch to it and record instead, mirroring the
        # switch-while-recording prompt.
        busy = (self.player is not None and self.player.isVisible()
                and self.playback_mode() == "embedded"
                and self._playing_key is not None)
        if busy:
            playing = (self._last_playback or {}).get("title") or ""
            idx = self._choice_dialog(
                tr("rec_switch_title"),
                tr("rec_switch_body", playing=playing, target=title),
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
        url = self.client.live_url(it["stream_id"], "ts")
        self.rec.add_job(url, title, now, stop_ts,
                         icon=it.get("stream_icon"))
        self._set_status(
            f"● Recording {title} {length} → {self.rec.directory()}")

    def _switch_and_record(self, it, stop_ts, length: str) -> None:
        """Switch the embedded player to this channel and record the
        stream we're now watching - one connection, no conflict."""
        title = self.channel_display_name(it)
        try:
            path = self.rec.build_path(title)
        except OSError as e:
            QMessageBox.warning(self, tr("rec_status_recording"), str(e))
            return
        self.play_live_channel(it)
        if self.player and self.player.start_stream_record(path):
            self.rec.add_inplayer_job(
                title, path, stop_ts,
                url=self.client.live_url(it["stream_id"], "ts"),
                icon=it.get("stream_icon"))
            self._set_status(
                f"● Recording {title} {length} - capturing "
                "the stream you're watching (no extra connection)")
        elif self._recorder_ready():
            url = self.client.live_url(it["stream_id"], "ts")
            self.rec.add_job(url, title, time.time(), stop_ts,
                             icon=it.get("stream_icon"))
            self._set_status(
                f"● Recording {title} {length} → {self.rec.directory()}")

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
        folder_box.addItem("(Recordings folder)", "")
        for rel in self.rec.folders():
            folder_box.addItem(rel, rel)
        f.addRow(tr("playlist_name"), name_edit)
        f.addRow(tr("field_start"), start_edit)
        f.addRow(tr("field_stop"), stop_edit)
        f.addRow(tr("field_save_in"), folder_box)
        hint = QLabel(
            f"Saved under {self.rec.directory()} - change the "
            "location in Settings → Recording. The app must be "
            "running when the recording starts.")
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
        url = self.client.live_url(it["stream_id"], "ts")
        title = (name_edit.text().strip()
                 or self.channel_display_name(it))
        self.rec.add_job(url, title, start_ts, stop_ts,
                         folder_box.currentData(),
                         icon=it.get("stream_icon"))
        when = datetime.fromtimestamp(start_ts).strftime(
            "%a %d %b %H:%M")
        self._set_status(
            f"Recording of {title} scheduled for {when}")

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
        f.addRow(tr("field_title"), QLabel(job["title"]))
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
        cur = self.cat_list.currentItem()
        if cur and cur.data(Qt.ItemDataRole.UserRole) == "__jobs__":
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
                self.rec.write_info(it["_path"], "", "", icon="")  # drop sidecar
                self.rec.prune_path(it["_path"])
            except OSError as e:
                self._set_status(
                    f"Could not delete: {e}", error=True)
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
                self.rec.write_info(it["_path"], "", "", icon="")
                self.rec.prune_path(it["_path"])
            except OSError as e:
                self._set_status(f"Could not delete: {e}", error=True)
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
        new_path = os.path.join(
            os.path.dirname(path),
            name + os.path.splitext(path)[1])
        try:
            os.rename(path, new_path)
            self.rec.move_info(path, new_path)   # keep the info sidecar attached
        except OSError as e:
            QMessageBox.warning(self, tr("msg_rename_rec_title"), str(e))
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
                dest = os.path.join(target, os.path.basename(it["_path"]))
                shutil.move(it["_path"], dest)
                self.rec.move_info(it["_path"], dest)
        except OSError as e:
            QMessageBox.warning(self, tr("msg_move_rec_title"), str(e))
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
            QMessageBox.warning(self, "New folder", str(e))
            return
        if items:
            self._move_recordings(items, name)
        else:
            self._load_categories()

    # -- EPG / replay time offsets ---------------------------------------------------

    def _epg_delay_minutes(self) -> int:
        try:
            return int(self.settings.value("epg_delay_min", 0))
        except (TypeError, ValueError):
            return 0

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
            what = prog.get("title") or "programme"
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
        # Carried to _verify_catchup: a *within-depth* (un-clamped) request that
        # plays but turns out to be the live feed (not seekable) is a fake
        # archive at a point a real one would serve, so it may be hidden - only a
        # request right at the depth limit is spared (that depth just isn't kept).
        self._ts_last_clamped = clamped
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
        # Candidate archive-URL formats (providers differ). Play the first;
        # _playback_error walks to the next on an early failure so we auto-pick
        # whichever scheme this provider actually serves.
        urls = self.client.timeshift_urls(
            sid, datetime.fromtimestamp(start), duration_min)
        if not urls:
            return
        log.debug("[ts] probe back_min=%s prog=%s days=%s start=%s "
                  "candidates=%s", back_min, bool(prog), days,
                  datetime.fromtimestamp(start), urls)
        name = self.channel_display_name(it)
        title = (f"{what} ({name}, timeshift)" if what
                 else f"{name} (timeshift)")
        key = self._item_key(it)
        # Probe the candidate URLs over HTTP in the background - the live video
        # keeps playing untouched. Only when a candidate is confirmed to serve
        # real video do we switch the player to it (one clean swap); if none do,
        # the channel is marked as having no working catch-up and the user just
        # gets a status message, with no stutter.
        self._flash_status(tr("ts_checking"))
        probe_token = getattr(self, "_ts_probe_token", 0) + 1
        self._ts_probe_token = probe_token

        def done(res):
            if probe_token != getattr(self, "_ts_probe_token", 0):
                return   # a newer play/probe superseded this one
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
                log.debug("[ts] probe network error for %s: %s", u, e)
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

    def _open_catchup_dialog(self, it) -> None:
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
                self._play_timeshift(it, prog=p)
                d.accept()

        watch_btn.clicked.connect(watch)
        lst.itemDoubleClicked.connect(watch)

        def fetch():
            progs = self.xmltv.past_programmes(it, days)
            if progs or it.get("stream_id") is None:
                return progs
            now = time.time()
            out = []
            for e in self.client.epg_table(it["stream_id"]):
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
                info.setText(
                    "The guide has no past programmes for this "
                    "channel - use 'Go back ...' instead.")
                return
            info.setText(
                f"{len(progs)} programmes - the provider archives "
                f"{days} day{'s' if days != 1 else ''} back. "
                "Double-click to watch.")
            last_day = None
            for p in progs:
                start = datetime.fromtimestamp(p["start_timestamp"])
                stop = datetime.fromtimestamp(p["stop_timestamp"])
                day = start.strftime("%A %d %B")
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
                  lambda e: info.setText(
                      f"Could not load the guide: {e}"))
        d.exec()

    # -- recording context menu ----------------------------------------------------

    def _rec_context_menu(self, pos, it) -> None:
        m = QMenu(self)
        if it.get("_kind") == "recjob":
            status = it.get("_status")
            if it.get("_path"):
                m.addAction(tr("common_watch"), lambda: self.play_item(it))
            if status == "recording":
                m.addAction(tr("rec_stop_recording"),
                            lambda: self.rec.cancel(it["_job"]))
            elif status == "scheduled":
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
        m.exec(self.listw.viewport().mapToGlobal(pos))
