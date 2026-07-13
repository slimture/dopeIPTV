"""Extracted from main_window.py (mixin); MainWindow inherits this.

Verbatim move - self.* access and behaviour unchanged.
"""

from __future__ import annotations

import os
import shutil
import time
from ..providers.client import b64, epg_times
from ..i18n import tr
from ..core.recording import format_size, safe_filename
from .theme import P
from ..core.workers import run_async
from PyQt6.QtCore import QDateTime, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QVBoxLayout
from datetime import datetime, timedelta


class _RecordingMixin:
    def _job_item(self, j: dict) -> dict:
        label = {"recording": "● REC", "scheduled": "Scheduled",
                 "done": "Done", "failed": "Failed",
                 "cancelled": "Cancelled"}.get(j["status"], j["status"])
        start = datetime.fromtimestamp(
            j["start"]).strftime("%a %d %b %H:%M")
        stop = ("until stopped" if j.get("stop") is None
                else datetime.fromtimestamp(j["stop"]).strftime("%H:%M"))
        return {"name": f"[{label}] {j['title']}  ({start} – {stop})",
                "_job": j["id"], "_key": f"job:{j['id']}",
                "_kind": "recjob", "_status": j["status"],
                "_error": j.get("error") or "",
                "_path": j.get("path") or ""}

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
            self._set_status(
                f"● Recording {n} stream{'s' if n > 1 else ''}...")

    def _on_recording_stopped(self, title: str, reason: str) -> None:
        abnormal = reason not in ("finished", "stopped")
        self._set_status(
            f"● Recording stopped: {title} ({reason})",
            error=abnormal)
        if self._player_fs and self.player:
            self.player.set_overlay_info(
                f"Recording stopped: {title} ({reason})")

    def _choice_dialog(self, title: str, message: str,
                       options: list[tuple[str, str]]) -> int | None:
        """A compact, themed confirmation dialog. *options* is a list of
        (label, kind) where kind is "primary"/"normal"/"danger"; returns the
        index of the clicked option, or None if dismissed. Replaces the
        oversized multi-button QMessageBox with clean stacked buttons."""
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
        d.exec()
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
                                 time.time(), j.get("stop"))
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
             ("Play anyway (I have multiple streams)", "normal"),
             ("Cancel", "normal")])
        if idx == 0:
            self._watch_recording_file(j)
            return False
        if idx == 1:
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
        val_edit.setPlaceholderText("e.g. 75")
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

    def _record_now(self, it, minutes) -> None:
        if it.get("stream_id") is None:
            return
        if not self._within_storage_cap():
            return
        title = self.channel_display_name(it)
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
                    url=self.client.live_url(it["stream_id"], "ts"))
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
        self.rec.add_job(url, title, now, stop_ts)
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
                url=self.client.live_url(it["stream_id"], "ts"))
            self._set_status(
                f"● Recording {title} {length} - capturing "
                "the stream you're watching (no extra connection)")
        elif self._recorder_ready():
            url = self.client.live_url(it["stream_id"], "ts")
            self.rec.add_job(url, title, time.time(), stop_ts)
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
                         folder_box.currentData())
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
        if QMessageBox.question(
                self, tr("msg_delete_rec_title"),
                tr("msg_delete_rec_body", what=what)) \
                != QMessageBox.StandardButton.Yes:
            return
        for it in items:
            try:
                os.remove(it["_path"])
                self.rec.prune_path(it["_path"])
            except OSError as e:
                self._set_status(
                    f"Could not delete: {e}", error=True)
        cur = self.cat_list.currentItem()
        self._load_items(
            cur.data(Qt.ItemDataRole.UserRole) if cur else None)

    def _rename_recording(self, it) -> None:
        path = it.get("_path")
        if not path:
            return
        name, ok = QInputDialog.getText(
            self, "Rename recording", "New name:",
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
        except OSError as e:
            QMessageBox.warning(self, tr("msg_rename_rec_title"), str(e))
        cur = self.cat_list.currentItem()
        self._load_items(
            cur.data(Qt.ItemDataRole.UserRole) if cur else None)

    def _move_recordings(self, items: list[dict],
                         folder: str) -> None:
        target = os.path.join(self.rec.directory(), folder)
        try:
            os.makedirs(target, exist_ok=True)
            for it in items:
                shutil.move(it["_path"], os.path.join(
                    target, os.path.basename(it["_path"])))
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

    @staticmethod
    def _timeshift_days(it) -> int:
        try:
            if int(it.get("tv_archive") or 0):
                return int(it.get("tv_archive_duration") or 1) or 1
        except (TypeError, ValueError):
            pass
        return 0

    def _play_timeshift(self, it, back_min=None, prog=None) -> None:
        sid = it.get("stream_id")
        days = self._timeshift_days(it)
        if sid is None or not days:
            return
        now = time.time()
        if prog:
            start = prog["start_timestamp"]
            duration_min = max(
                1, int((prog["stop_timestamp"] - start) // 60) + 2)
            what = prog.get("title") or "programme"
        else:
            start = now - (back_min or 30) * 60
            duration_min = max(1, int((now - start) // 60) + 1)
            what = None
        start = max(start, now - days * 86400)
        start += self._replay_delay_minutes() * 60
        url = self.client.timeshift_url(
            sid, datetime.fromtimestamp(start), duration_min)
        name = self.channel_display_name(it)
        title = (f"{what} ({name}, timeshift)" if what
                 else f"{name} (timeshift)")
        # A catch-up/archive URL is a seekable segment. Remember the segment's
        # content start so the live timeline can show how far behind live we
        # are; catchup=True marks it so the DVR-pause/reconnect guards and the
        # seek-mode logic treat it as an archive segment, not the live edge.
        self._ts_segment_start = start
        # A specific programme (picked from the menu/EPG) gets its own seek bar
        # spanning just that programme; a timeline scrub or "go back X" keeps
        # the live timeline so the user can keep scrubbing across the window.
        self._ts_catchup_program = bool(prog)
        self._start_playback(url, title, it.get("stream_icon"),
                             self._item_key(it), "live", record=False,
                             item=it, catchup=True)

    # (minutes back, duration i18n key) - the label is "Go back <duration>".
    TIMESHIFT_STEPS = (
        (30, "dur_30min"), (60, "dur_1h"), (120, "dur_2h"), (360, "dur_6h"),
        (720, "dur_12h"), (1440, "dur_1d"), (2880, "dur_2d"),
        (4320, "dur_3d"), (7200, "dur_5d"), (10080, "dur_7d"),
    )

    def _build_timeshift_menu(self, ts_menu, it) -> None:
        days = self._timeshift_days(it)
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
        for mins, dur_key in self.TIMESHIFT_STEPS:
            if mins > days * 1440:
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
        m.addSeparator()
        m.addAction(tr("ctx_new_folder"), lambda: self._new_rec_folder())
        m.addAction(tr("rec_change_folder"),
                    lambda: self._choose_rec_dir())
        m.exec(self.listw.viewport().mapToGlobal(pos))
