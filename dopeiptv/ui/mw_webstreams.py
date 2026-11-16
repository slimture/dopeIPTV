"""YouTube Live, Twitch and sites like them in the window, through yt-dlp
(core/ytdlp.py).

Such an address opens from Open address… (the app menu), --play, a
dopeiptv://play link or the remote API. It plays in the embedded player:
mpv's ytdl hook runs yt-dlp where the libmpv has one, else the window asks
yt-dlp for the stream and plays that. With no yt-dlp found the app offers
to download the official release binary for this computer (checksum
checked) into the user's data folder - never bundled - and keeps that copy
up to date, at most once a day while it is used. Settings > Playback turns
the managed copy off, and then only the system's is used.

Saved addresses are favourites of their own kind (``web_favorites``, by
address, no source's), with a LIVE badge when the stream is live; they show
under Favourites, and sync and back up with the rest.
"""

from __future__ import annotations

import time
from functools import partial

from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QInputDialog, QLabel, QLineEdit,
    QPushButton, QVBoxLayout,
)

from ..core import ytdlp
from ..core.commands import stream_url_ok
from ..core.log import log, redact_url
from ..core.stores import FAV_DEFAULT_GROUP
from ..core.workers import run_async
from ..i18n import tr
from .notices import hub_of
from .theme import P

#: Error reasons (core/ytdlp.YtdlpError) -> what the person is told.
_ERR_TEXT = {"platform": "ytdlp_err_platform", "release": "ytdlp_err_download",
             "download": "ytdlp_err_download", "checksum": "ytdlp_err_checksum",
             "runs": "ytdlp_err_runs", "busy": "ytdlp_err_busy",
             "missing": "ytdlp_err_runs", "failed": "web_play_failed"}


def _error_text(err) -> str:
    reason = getattr(err, "reason", "")
    text = tr(_ERR_TEXT.get(reason, "ytdlp_err_download"))
    detail = getattr(err, "detail", "")
    return f"{text} ({detail})" if detail and reason == "failed" else text


class _WebStreamsMixin:
    """MainWindow mixin: web pages played through yt-dlp, their favourites,
    the managed yt-dlp."""

    _ytdlp_job: dict | None = None
    _web_gen = 0

    # -- which yt-dlp -------------------------------------------------------------------

    def _ytdlp_managed_on(self) -> bool:
        return self.settings.value(ytdlp.SETTING_MANAGED, "true") == "true"

    def _ytdlp_found(self) -> ytdlp.Found | None:
        return ytdlp.find(self._ytdlp_managed_on())

    # -- Open address… -------------------------------------------------------------------

    def open_address(self) -> None:
        """A stream's address or a YouTube/Twitch page, typed or pasted."""
        d = QDialog(self)
        d.setWindowTitle(tr("open_address_title"))
        d.setMinimumWidth(520)
        lay = QVBoxLayout(d)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)
        hint = QLabel(tr("open_address_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        lay.addWidget(hint)
        edit = QLineEdit()
        edit.setPlaceholderText("https://www.twitch.tv/…")
        clip = (QGuiApplication.clipboard().text() or "").strip()
        if stream_url_ok(clip) and len(clip) < 2000 and " " not in clip:
            edit.setText(clip)
            edit.selectAll()
        lay.addWidget(edit)
        save = QCheckBox(tr("open_address_save"))
        lay.addWidget(save)
        err = QLabel()
        err.setStyleSheet(f"font-size:12px; color:{P['error']};")
        err.hide()
        lay.addWidget(err)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        go = bb.addButton(tr("open_address_play"), QDialogButtonBox.ButtonRole.AcceptRole)
        go.setObjectName("Primary")
        go.setDefault(True)
        bb.rejected.connect(d.reject)

        def accept() -> None:
            url = edit.text().strip()
            if not stream_url_ok(url):
                err.setText(tr("open_address_bad"))
                err.show()
                return
            d.accept()
        bb.accepted.connect(accept)
        lay.addWidget(bb)
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        url = edit.text().strip()
        if save.isChecked():
            self._web_fav_add(url)
        self._play_address(url)

    # -- playing -------------------------------------------------------------------------

    def _web_fav_rows(self, group: str | None = None) -> list[dict]:
        return [{**x, "_kind": "web", "_key": x["url"],
                 "_sub": ytdlp.display_name(x["url"])}
                for x in self.web_favs.items(group) if x.get("url")]

    def _web_activate(self, it: dict, player=None, external: bool = False) -> None:
        """A saved address chosen (Favourites): a page through yt-dlp, any
        other address as Open address plays it."""
        url = str(it.get("url") or it.get("_url") or "")
        if not url:
            return
        if ytdlp.is_web_stream(url):
            self._play_web(url, it.get("name"),
                           {**it, "_kind": "web", "_key": url, "_named": True},
                           player, external)
        elif external or player == "vlc":
            from ..media.players import launch_player
            launch_player(player or "mpv", url, it.get("name") or url, self)
        else:
            self._play_address(url, it.get("name"))

    def _web_item(self, url: str, title: str | None = None) -> dict:
        fav = next((x for x in self.web_favs.items() if x.get("url") == url), None)
        if fav:
            return {**fav, "_kind": "web", "_key": url, "_named": True}
        return {"url": url, "name": title or ytdlp.display_name(url),
                "_live": ytdlp.looks_live(url), "_kind": "web", "_key": url,
                "_named": bool(title)}

    def _play_web(self, url: str, title: str | None = None, item: dict | None = None,
                  player=None, external: bool = False) -> None:
        """A web page (YouTube, Twitch, ...) played through yt-dlp."""
        from ..media.players import launch_player
        item = item or self._web_item(url, title)
        name = item.get("name") or title or ytdlp.display_name(url)
        found = self._ytdlp_found()
        if found is None:
            # After this returns: a command from outside (the remote API, a
            # second start) is answered at once, not when the window closes.
            QTimer.singleShot(0, partial(self._ytdlp_offer, partial(
                self._play_web, url, title, item, player, external)))
            return
        log.info("web stream: %s with %s", redact_url(url), ytdlp.describe(found))
        pl = self.player
        if external or player == "vlc" or pl is None:
            # An external mpv runs its own ytdl hook (VLC its own scripts).
            launch_player(player or "mpv", url, name, self)
            return
        self._web_gen += 1
        if pl.has_ytdl_hook():
            pl.set_ytdl_path(ytdlp.hook_path(found))
            self._start_playback(ytdlp.mpv_address(url), name, item.get("stream_icon"),
                                 url, "web", record=False, item=item)
            self._web_started(found, url)
            return
        # No hook in this libmpv (built without Lua): yt-dlp is asked here.
        self._show_busy(tr("web_opening"))
        run_async(self.pool, partial(ytdlp.resolve, found.path, url),
                  partial(self._web_resolved, self._web_gen, url, name, item, found),
                  partial(self._web_resolve_failed, self._web_gen))

    def _web_resolved(self, gen: int, url: str, name: str, item: dict,
                      found: ytdlp.Found, info) -> None:
        if gen != self._web_gen:
            return
        self._hide_busy()
        # A saved address keeps the name it was given; else the page's own.
        title = name if item.get("_named") else (info.get("title") or name)
        item = {**item, "name": title,
                "_live": bool(item.get("_live") or info.get("live"))}
        self._start_playback(info["url"], title, item.get("stream_icon"), url, "web",
                             record=False, item=item)
        self._web_started(found, url)

    def _web_resolve_failed(self, gen: int, msg) -> None:
        if gen != self._web_gen:
            return
        self._hide_busy()
        log.info("web stream: yt-dlp could not open it: %s", msg)
        detail = str(msg or "").partition(": ")[2]
        text = tr("web_play_failed")
        self._show_stream_error(f"{text} ({detail})" if detail else text)

    def _web_started(self, found: ytdlp.Found, url: str) -> None:
        """After a page started: the managed copy's daily look for a newer
        release, and the offer to keep the address."""
        if found.managed and ytdlp.update_due(
                self.settings.value(ytdlp.SETTING_CHECKED, 0)):
            self.settings.setValue(ytdlp.SETTING_CHECKED, int(time.time()))
            run_async(self.pool, ytdlp.update, self._ytdlp_updated,
                      self._ytdlp_update_failed)
        if not self.web_favs.is_favorite(url):
            self._show_action_toast(tr("web_save_offer"), 6000,
                                    partial(self._web_fav_add, url))

    def _ytdlp_updated(self, tag) -> None:
        if tag:
            log.info("yt-dlp updated to %s", tag)
            self._show_toast(tr("ytdlp_updated", version=tag), 4000)

    def _ytdlp_update_failed(self, msg) -> None:
        log.info("yt-dlp update check failed: %s", msg)

    # -- the managed copy ----------------------------------------------------------------

    def _ytdlp_offer(self, then=None) -> None:
        """No yt-dlp: download the app's own copy (asked), or say where to
        get one."""
        if not self._ytdlp_managed_on():
            self._choice_dialog(tr("ytdlp_title"), tr("ytdlp_missing_system"),
                                [(tr("common_ok"), "primary")])
            return
        if ytdlp.asset_name() is None:
            self._choice_dialog(tr("ytdlp_title"), tr("ytdlp_err_platform"),
                                [(tr("common_ok"), "primary")])
            return
        idx = self._choice_dialog(tr("ytdlp_title"), tr("ytdlp_offer"),
                                  [(tr("ytdlp_download"), "primary"),
                                   (tr("common_cancel"), "normal")])
        if idx == 0:
            self._ytdlp_download(then)

    def _ytdlp_download(self, then=None, done=None, update: bool = False) -> None:
        """Download the managed copy - or, with *update*, bring it to the
        newest release (nothing downloaded when it is that already) - off
        the window's thread; *then* runs once it is in place, *done(ok,
        text)* is told either way."""
        if self._ytdlp_job is not None:
            return
        job: dict = {"got": 0, "total": 0, "then": then, "done": done}
        self._ytdlp_job = job

        def progress(got: int, total: int) -> None:   # the worker's thread
            job["got"], job["total"] = got, total

        timer = QTimer(self)
        timer.setInterval(400)
        timer.timeout.connect(self._ytdlp_progress)
        job["timer"] = timer
        timer.start()
        # A download is work the window does: a job of its own, its words
        # following the bytes, ended by the answer (or let go if none comes).
        job["notice"] = hub_of(self).start(tr("ytdlp_downloading"), key="ytdlp",
                                           timeout_ms=120_000)
        self._ytdlp_progress()
        run_async(self.pool, partial(ytdlp.update if update else ytdlp.install,
                                     progress=progress),
                  self._ytdlp_installed, self._ytdlp_install_failed)

    def _ytdlp_progress(self) -> None:
        job = self._ytdlp_job
        if job is None:
            return
        mb = job["got"] / 1e6
        total = job["total"] / 1e6
        if job["got"] != job.get("said"):
            job["said"] = job["got"]
            hub_of(self).update(job.get("notice", 0), tr(
                "ytdlp_downloading_n", got=f"{mb:.0f}",
                total=f"{total:.0f}" if total else "?"))

    def _ytdlp_finish(self) -> dict:
        job, self._ytdlp_job = self._ytdlp_job or {}, None
        timer = job.get("timer")
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        hub_of(self).finish(job.get("notice", 0))
        return job

    def _ytdlp_installed(self, tag) -> None:
        job = self._ytdlp_finish()
        self.settings.setValue(ytdlp.SETTING_CHECKED, int(time.time()))
        # No tag from an update: the copy there is the newest already.
        text = tr("ytdlp_installed", version=tag or ytdlp.installed_tag() or "?")
        self._show_toast(text, 4000)
        if job.get("done") is not None:
            job["done"](True, text)
        if job.get("then") is not None:
            job["then"]()

    def _ytdlp_install_failed(self, msg) -> None:
        job = self._ytdlp_finish()
        reason = str(msg or "").partition(":")[0].strip()
        text = tr(_ERR_TEXT.get(reason, "ytdlp_err_download"))
        log.warning("yt-dlp download failed: %s", msg)
        if job.get("done") is not None:
            self._set_status(text, error=True)
            job["done"](False, text)
        else:
            self._choice_dialog(tr("ytdlp_title"), text, [(tr("common_ok"), "primary")])

    # -- favourites ------------------------------------------------------------------------

    def _web_fav_add(self, url: str, name: str | None = None) -> None:
        if not url or self.web_favs.is_favorite(url):
            return
        lp = getattr(self, "_last_playback", None) or {}
        live = ytdlp.looks_live(url)
        if not name and lp.get("key") == url:
            # The page's own title, as the hook gave it to mpv, or as yt-dlp
            # said it when the app asked.
            shown = str(lp.get("title") or "")
            name = (self._web_playing_title(lp)
                    or ("" if shown == ytdlp.display_name(url) else shown))
            live = live or bool((lp.get("item") or {}).get("_live")) \
                or self._web_playing_live()
        self.web_favs.add(FAV_DEFAULT_GROUP, {
            "url": url, "name": name or ytdlp.display_name(url), "_live": live})
        self._show_toast(tr("web_saved"), 3000)
        if self.mode == "fav":
            self._load_categories()
        else:
            self.list_model.refresh_all()

    def _web_playing_title(self, lp: dict) -> str:
        """The page's own title, as the hook gave it to mpv."""
        try:
            title = str(self.player.video.mpv.media_title or "")
        except Exception:      # no player, no core, nothing playing
            return ""
        title = " ".join(title.split())
        return "" if not title or "://" in title or title == lp.get("title") else title[:200]

    def _web_playing_live(self) -> bool:
        """Whether what plays has no end (a live stream's duration is
        unknown)."""
        if not getattr(self.player, "current_url", None):
            return False
        try:
            duration = self.player.video.mpv.duration
        except Exception:      # no core
            return False
        return not duration

    def _web_toggle_fav(self, it: dict) -> None:
        url = it.get("url")
        if not url:
            return
        if self.web_favs.is_favorite(url):
            self.web_favs.remove(url)
            if self.mode == "fav":
                self._load_categories()
            else:
                self.list_model.refresh_all()
        else:
            self._web_fav_add(url, it.get("name"))

    def _web_rename(self, it: dict) -> None:
        url = it.get("url")
        if not url or not self.web_favs.is_favorite(url):
            return
        name, ok = QInputDialog.getText(self, tr("web_rename"), tr("web_rename_prompt"),
                                        text=str(it.get("name") or ""))
        name = " ".join((name or "").split())
        if not ok or not name:
            return
        for items in self.web_favs.groups.values():
            for row in items:
                if row.get("url") == url:
                    row["name"] = name[:200]
        self.web_favs._save()
        if self.mode == "fav":
            self._load_categories()

    def _web_copy(self, it: dict) -> None:
        url = it.get("url")
        if url:
            QGuiApplication.clipboard().setText(url)
            self._show_toast(tr("web_copied"), 2500)

    # -- Settings > Playback -------------------------------------------------------------

    def _build_ytdlp_card(self, page, dlg) -> None:
        c = page.card(tr("ytdlp_card"))
        box = c.toggle(tr("ytdlp_managed"), self._ytdlp_managed_on(),
                       tr("ytdlp_managed_hint"))
        state = QLabel(tr("ytdlp_looking"))
        state.setWordWrap(True)
        state.setObjectName("SettingsHint")
        action = QPushButton(tr("ytdlp_update_now"))
        c.row(tr("ytdlp_title"), action, search="yt-dlp youtube twitch")
        c.widget(state, tr("ytdlp_title"))
        card = _YtdlpCard(self, dlg, box, state, action)
        box.toggled.connect(card.switched)
        action.clicked.connect(card.act)
        card.refresh()


class _YtdlpCard(QObject):
    """The yt-dlp card's state: which one is used, and its button (download,
    update now). Lives as long as the dialog; applied as it is changed."""

    def __init__(self, win, dlg, box, state: QLabel, action: QPushButton) -> None:
        super().__init__(dlg)
        self.win, self.box = win, box
        self.state, self.action = state, action

    def switched(self, on: bool) -> None:
        self.win.settings.setValue(ytdlp.SETTING_MANAGED, "true" if on else "false")
        self.refresh()

    def refresh(self) -> None:
        found = ytdlp.find(self.box.isChecked())
        managed_on = self.box.isChecked()
        self.action.setText(tr("ytdlp_update_now") if found and found.managed
                            else tr("ytdlp_download"))
        self.action.setEnabled(managed_on and ytdlp.asset_name() is not None
                               and self.win._ytdlp_job is None)
        if found is None:
            self.state.setText(tr("ytdlp_none") if managed_on else tr("ytdlp_none_system"))
            return
        self.state.setText(tr("ytdlp_looking"))
        run_async(self.win.pool, partial(ytdlp.version_of, found.path),
                  partial(self.show, found), partial(self.unknown, found))

    def show(self, found: ytdlp.Found, version) -> None:
        if sip.isdeleted(self.state):
            return
        key = "ytdlp_is_managed" if found.managed else "ytdlp_is_system"
        self.state.setText(tr(key, version=str(version or "?"), path=found.path))

    def unknown(self, found: ytdlp.Found, _msg=None) -> None:
        self.show(found, "")

    def act(self) -> None:
        if not self.box.isChecked():
            return
        found = ytdlp.find(True)
        self.action.setEnabled(False)
        self.state.setText(tr("ytdlp_downloading"))
        self.win._ytdlp_download(done=self.done,
                                 update=bool(found and found.managed))

    def done(self, _ok: bool, text: str) -> None:
        if sip.isdeleted(self.state):
            return
        self.refresh()
        if not _ok:
            self.state.setText(text)
