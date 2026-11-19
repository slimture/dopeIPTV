"""Settings' WATCH pages: Playback, Picture and sound, Guide, Multiview,
Recording and Casting (their rows and keys: docs/SETTINGS_MAP.md).

Each ``build_<page>(ctx)`` fills its page the first time it is needed
(SettingsShell.ensure_built), never as the window opens, and leaves what
the page stores and applies as ``ctx.page_commits[<page key>]``:
commit(put, changed, final), run by open_settings' commit. *ctx* is the
Settings window's SettingsContext (ui/mw_settings.py).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6 import sip
from PyQt6.QtCore import QLocale, Qt
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QLabel, QListWidget, QListWidgetItem, QPushButton,
)

from ..core import night_mode, rec_mp4
from ..core.local_buffer import MEMORY_MAX_MINUTES
from ..core.recording import format_size
from ..core.workers import dir_size_bytes, run_async
from ..core.xdg import open_url
from ..i18n import tr
from ..media.dvr_player import DVR_SPACE_GB, dvr_base
from ..media.embedded import SUB_COLORS, SUB_SCALES
from ..media.players import embedded_playback_reason
from .settings_ui import (
    DurationSpin, GridSpin, SizeSpin, Stepper, ValueSlider, after_shown,
    fit_to_rows, hbox, page_icon, size_text,
)
from .theme import P
from .widgets import file_dialog_options

if TYPE_CHECKING:
    from .mw_settings import SettingsContext


def mb_value(mb: int) -> tuple[str, str]:
    """A size in MB as Recording stores it: a number and its unit."""
    for unit, div in (("TB", 1_000_000), ("GB", 1000)):
        if mb >= div:
            return f"{mb / div:g}", unit
    return (f"{mb:g}", "MB") if mb > 0 else ("", "GB")


def build_playback(ctx: SettingsContext) -> None:
    """Playback: how a stream plays and what plays next, the stream and
    its buffer, yt-dlp's sites, and rewinding live TV."""
    win, d, play = ctx.win, ctx.dialog, ctx.pages["playback"]
    on, is_on, flag, nerd_link = ctx.on, ctx.is_on, ctx.flag, ctx.nerd_link
    # How early the "Next episode" card appears. A number the user
    # picks rather than one guessed here: where the credits start
    # varies from show to show.
    # Seconds too: a show's credits can be twenty seconds long.
    nextup_box = win._combo(
        [("0", tr("option_off"))]
        + [(str(s), tr("opt_seconds", n=s)) for s in (10, 20, 30, 45)]
        + [(str(m * 60), tr("opt_minutes", n=m))
           for m in (1, 2, 3, 5, 10)],
        str(win.settings.value("nextup_lead_secs", "180")))
    fmt_box = win._combo(
        [("ts", "ts"), ("m3u8", "m3u8")],
        win.settings.value("stream_format", "ts"))
    buf_box = win._combo(
        [("1", "1 s"), ("3", "3 s"), ("5", "5 s"),
         ("10", "10 s"), ("30", "30 s")],
        str(win.settings.value("cache_secs", "10")))
    rewind_box = win._combo(
        [("0", tr("option_off"))]
        + [(str(m), tr("opt_minutes", n=m)) for m in (1, 2, 5, 10, 15, 30)]
        + [("60", tr("dur_1h")), ("120", tr("dur_2h")),
           ("240", tr("dur_4h")), ("360", tr("dur_6h"))],
        str(win.settings.value("live_rewind_minutes", "2")))
    c = play.card(tr("set_card_behaviour"))
    stream_info_box = c.toggle(
        tr("setting_stream_info"), is_on("show_stream_info", "false"),
        tr("setting_stream_info_hint"))
    autoplay_box = c.toggle(tr("setting_autoplay_preview"),
                            win._autoplay_preview())
    autoplay_next_box = c.toggle(
        tr("setting_autoplay_next"),
        is_on("autoplay_next_episode", "true"))
    c.row(tr("setting_nextup_lead"), nextup_box)
    autorecon_box = c.toggle(tr("setting_auto_reconnect"),
                             is_on("auto_reconnect_live", "true"))
    # Stored inverted (hide_upcoming_prompt): the setting the user
    # sees is "ask"; the "Don't show again" box in the prompt hides
    # it.
    upcoming_box = c.toggle(
        tr("set_upcoming_prompt"),
        win.settings.value("hide_upcoming_prompt", "false") != on)
    # Night mode: the player's menu and its key turn it on too, live.
    # A card of its own held this one row.
    night_box = c.toggle(tr("night_mode"), night_mode.enabled(win.settings),
                         tr("night_mode_hint"))
    c = play.card(tr("set_card_stream_buffer"))
    c.row(tr("setting_stream_format"), fmt_box)
    c.row(tr("setting_network_buffer"), buf_box)
    # Settings apply as they change; these two only reach the player
    # with the next stream, so they say so.
    c.note(tr("set_next_stream_note"))
    nerd_link(c, "set_link_stream",
              [tr("nerd_probe"), tr("nerd_cache"), tr("nerd_card_hls"),
               tr("nerd_card_latency")], "player")
    # YouTube, Twitch and the like: which yt-dlp opens them
    # (mw_webstreams).
    win._build_ytdlp_card(play, d)
    # Rewinding live TV has a card of its own: its five settings sat
    # in "Stream" between the stream format and the network buffer.
    c = rw = play.card(tr("set_card_rewind"))
    c.row(tr("setting_live_rewind"), rewind_box,
          tr("setting_live_rewind_hint"))
    rewind_disk_box = c.toggle(
        tr("setting_live_rewind_disk"),
        is_on("live_rewind_disk", "false"),
        tr("setting_live_rewind_disk_hint"))
    # The app's own buffer (core/dvr_session.py): on disk within a
    # cap, downloading on through a pause. Opt-in while it is new.
    kept_box = c.toggle(
        tr("setting_dvr_kept"), is_on("live_rewind_kept", "false"),
        tr("setting_dvr_kept_hint"))
    space_box = win._combo(
        [(v, tr("opt_gigabytes", n=v)) for v in DVR_SPACE_GB],
        str(win.settings.value("live_rewind_space_gb", "2")))
    space_row = c.row(tr("setting_dvr_space"), space_box,
                      tr("setting_dvr_space_hint"))
    c.shown_if(space_row, kept_box.isChecked)
    # Where it is, what it takes now, and that it is only for now.
    dvr_dir = dvr_base()
    where_btn = QPushButton(tr("btn_open_folder"))
    where_btn.setToolTip(str(dvr_dir))

    def open_dvr_dir() -> None:
        dvr_dir.mkdir(parents=True, exist_ok=True)
        open_url(dvr_dir.as_uri())
    where_btn.clicked.connect(open_dvr_dir)

    def where_text(size: str) -> str:
        return tr("setting_dvr_where") + " · " + tr("dvr_where_now", size=size)
    where_row = c.row(where_text("…"), where_btn,
                      tr("setting_dvr_where_hint") + "\n" + str(dvr_dir))
    c.shown_if(where_row, kept_box.isChecked)

    def show_dvr_size(total: int) -> None:
        if not sip.isdeleted(where_row.label):
            where_row.label.setText(where_text(format_size(total)))

    def count_dvr() -> None:
        run_async(win.pool, dir_size_bytes, show_dvr_size, None, dvr_dir)
    # Counted off the window's thread once it is up: a buffer kept on
    # disk is thousands of segments, one stat each.
    after_shown(d, count_dvr)
    # mpv's disk cache is beside the point with the app's own buffer.
    c.shown_if(rewind_disk_box.parentWidget(),
               lambda: not kept_box.isChecked())

    def rewind_room(on_disk: bool) -> None:
        # Longer than MEMORY_MAX_MINUTES only on disk: in memory an
        # HD channel takes about 60 MB a minute.
        model = rewind_box.model()
        for i in range(rewind_box.count()):
            if int(rewind_box.itemData(i)) > MEMORY_MAX_MINUTES:
                model.item(i).setEnabled(on_disk)
        if not on_disk and int(rewind_box.currentData()) > \
                MEMORY_MAX_MINUTES:
            rewind_box.setCurrentIndex(
                rewind_box.findData(str(MEMORY_MAX_MINUTES)))

    def rewind_room_now(_on: bool = False) -> None:
        # rw, not c: c is the next card by the time a switch is
        # flipped.
        rw.refresh()
        rewind_room(rewind_disk_box.isChecked() or kept_box.isChecked())

    rewind_disk_box.toggled.connect(rewind_room_now)
    kept_box.toggled.connect(rewind_room_now)
    rewind_room_now()
    if not win.player:
        c = play.card()
        c.note(tr("set_embedded_unavailable",
                  reason=embedded_playback_reason(localized=True)
                  or tr("set_reason_unknown")))

    def commit_playback(put, changed: set, final: bool) -> None:
        put("stream_format", fmt_box.currentData())
        # Stored inverted: the switch asks whether to *show* the
        # prompt.
        put("hide_upcoming_prompt",
            "false" if upcoming_box.isChecked() else "true")
        put("show_stream_info", flag(stream_info_box))
        put("autoplay_preview", flag(autoplay_box))
        put("autoplay_next_episode", flag(autoplay_next_box))
        put("nextup_lead_secs", nextup_box.currentData())
        put("auto_reconnect_live", flag(autorecon_box))
        # These two only when switched: off and never set is no
        # setting.
        if night_box.isChecked() != night_mode.enabled(win.settings):
            put(night_mode.KEY, flag(night_box))
        if night_mode.KEY in changed and win.player:
            win.player.set_night_mode(night_box.isChecked())
        put("cache_secs", buf_box.currentData())
        put("live_rewind_minutes", rewind_box.currentData())
        put("live_rewind_disk", flag(rewind_disk_box))
        put("live_rewind_kept", flag(kept_box))
        put("live_rewind_space_gb", space_box.currentData())
    ctx.page_commits["playback"] = commit_playback


def build_audio(ctx: SettingsContext) -> None:
    """Picture and sound on one page (they were two pages of four and five
    rows, both about how a stream plays): the audio and subtitle languages,
    the subtitles' look, the picture and its decoding."""
    win, audio = ctx.win, ctx.pages["audio"]
    is_on, flag, own, nerd_link = ctx.is_on, ctx.flag, ctx.own, ctx.nerd_link
    LANGS = [
        ("", tr("option_lang_auto")), ("swe", tr("lang_swe")),
        ("eng", tr("lang_eng")), ("nor", tr("lang_nor")),
        ("dan", tr("lang_dan")), ("fin", tr("lang_fin")),
        ("ger", tr("lang_ger")), ("fre", tr("lang_fre")),
        ("spa", tr("lang_spa")), ("ita", tr("lang_ita")),
        ("por", tr("lang_por")), ("pol", tr("lang_pol")),
        ("ara", tr("lang_ara")), ("tur", tr("lang_tur")),
    ]
    alang_box = win._combo(LANGS, win.settings.value("audio_lang", ""))
    sub_box = win._combo(
        [("off", tr("option_sub_off")), ("auto", tr("option_sub_auto")),
         ("lang", tr("option_sub_lang")),
         ("forced", tr("option_sub_forced"))],
        win.settings.value("sub_mode", "auto"))
    slang_box = win._combo(LANGS, win.settings.value("sub_lang", ""))
    slang2_box = win._combo(LANGS, win.settings.value("sub_lang2", ""))
    c = subs_card = audio.card(tr("sec_audio_subs"))
    c.row(tr("setting_audio_lang"), alang_box, tr("set_next_stream_note"),
          badge=own())
    c.row(tr("setting_subtitles"), sub_box, badge=own())
    subs_on = lambda: sub_box.currentData() != "off"  # noqa: E731
    c.shown_if(c.row(tr("setting_sub_lang"), slang_box,
                     tr("setting_sub_lang_ttx_hint"), badge=own()), subs_on)
    c.shown_if(c.row(tr("setting_sub_lang_fallback"), slang2_box, badge=own()),
               subs_on)
    sub_live_box = c.toggle(tr("setting_sub_live"), is_on("sub_live", "true"),
                            tr("setting_sub_live_hint"), badge=own())
    c.shown_if(sub_live_box, subs_on)
    sub_box.currentIndexChanged.connect(lambda _i: subs_card.refresh())
    sub_size_box = win._combo(
        [(v, tr("opt_percent", n=v)) for v in SUB_SCALES],
        str(win.settings.value("sub_scale", "100")))
    sub_color_box = win._combo(
        [(key, tr(f"option_sub_color_{key}")) for key in SUB_COLORS],
        win.settings.value("sub_color", "white"))
    c = audio.card(tr("set_card_sub_look"))
    c.row(tr("setting_sub_size"), sub_size_box)
    c.row(tr("setting_sub_color"), sub_color_box)
    sub_box_box = c.toggle(tr("setting_sub_box"),
                           is_on("sub_background", "false"),
                           tr("setting_sub_box_hint"))

    # -- Picture (on the same page) ------------------------------------
    aspect_box = win._combo(
        [("auto", tr("option_aspect_auto")), ("16:9", "16:9"),
         ("4:3", "4:3"), ("2.35:1", "2.35:1"),
         ("stretch", tr("option_aspect_stretch"))],
        win.settings.value("aspect_mode", "auto"))
    try:
        sharpen_now = min(3.0, max(0.0, float(
            win.settings.value("video_sharpen", 0.0) or 0.0)))
    except (TypeError, ValueError):
        sharpen_now = 0.0
    # A slider, not four presets: with settings applied as they
    # change, the picture sharpens under it while it is dragged.
    sharpen_box = ValueSlider(
        0.0, 3.0, sharpen_now, scale=10,
        text=lambda v: tr("option_off") if v == 0
        else QLocale().toString(v, "f", 1))
    sharpen_box.setAccessibleName(tr("setting_sharpen"))
    tonemap_box = win._combo(
        [("auto", tr("option_tonemap_auto")), ("hable", "Hable"),
         ("mobius", "Mobius"), ("reinhard", "Reinhard"),
         ("bt.2390", "BT.2390"), ("clip", tr("option_tonemap_clip"))],
        win.settings.value("video_tonemapping", "auto"))
    hwdec_box = win._combo(
        [("no", tr("option_hwdec_off")),
         ("auto-copy-safe", tr("option_hwdec_safe")),
         ("auto-safe", tr("option_hwdec_direct"))],
        str(win.settings.value("hwdec_mode", "") or "no"))
    c = audio.card(tr("sec_video"))
    c.row(tr("setting_aspect_ratio"), aspect_box)
    deint_box = c.toggle(tr("setting_deinterlace"),
                         is_on("video_deinterlace", "false"))
    c.row(tr("setting_sharpen"), sharpen_box)
    c.row(tr("setting_tonemapping"), tonemap_box)
    c = audio.card(tr("set_card_decoding"))
    c.row(tr("setting_hwdec"), hwdec_box, tr("setting_hwdec_hint"))
    c.note(tr("set_next_stream_note"))
    # How the picture is drawn (media/video_thread.py): this copy's
    # default (off in a release on macOS) until the person chooses;
    # read when the player is made.
    from ..core import video_mode
    chose = video_mode.choice(win.settings)
    video_thread_default = video_mode.default_on_here()
    video_thread_box = c.toggle(
        tr("setting_video_thread"),
        chose == "on" if chose else video_thread_default,
        tr("setting_video_thread_hint"))
    nerd_link(c, "set_link_picture",
              [tr("nerd_spdif"), tr("nerd_sync"), tr("nerd_interpolation"),
               tr("nerd_adelay"), tr("nerd_card_mpv")], "player")

    def commit_audio(put, changed: set, final: bool) -> None:
        put("audio_lang", alang_box.currentData())
        put("sub_mode", sub_box.currentData())
        put("sub_lang", slang_box.currentData())
        put("sub_lang2", slang2_box.currentData())
        put("sub_live", flag(sub_live_box))
        put("sub_scale", sub_size_box.currentData())
        put("sub_color", sub_color_box.currentData())
        put("sub_background", flag(sub_box_box))
        put("aspect_mode", aspect_box.currentData())
        # Stored once it is chosen: OK with nothing touched writes
        # nothing, and the default stays this copy's.
        if (video_thread_box.isChecked() != video_thread_default
                or win.settings.value(video_mode.SETTING) is not None):
            put(video_mode.SETTING, flag(video_thread_box))
        put("hwdec_mode", hwdec_box.currentData())
        if "hwdec_mode" in changed and win.player:
            # Stage it for the next mpv build (a fresh core reads the
            # widget attribute, not live settings). A changed decode
            # mode takes effect on the next opened stream - we never
            # re-assign hwdec on the running stream, which would
            # reinitialise the decoder and glitch the video.
            win.player.video.hwdec_pref = hwdec_box.currentData()
        put("video_deinterlace", flag(deint_box))
        put("video_sharpen", f"{sharpen_box.value():.1f}")
        put("video_tonemapping", tonemap_box.currentData())
    ctx.page_commits["audio"] = commit_audio


def build_guide(ctx: SettingsContext) -> None:
    """The programme guide: how many programmes ahead, the line at the
    current time, its offsets, its data fetched again or cleared."""
    win, guide = ctx.win, ctx.pages["guide"]
    flag = ctx.flag
    epg_count_box = Stepper(lo=win.EPG_UPCOMING_MIN,
                            hi=win.EPG_UPCOMING_MAX,
                            value=win._epg_upcoming_count())

    def delay_box(key: str) -> Stepper:
        try:
            total = int(win.settings.value(key, 0))
        except (TypeError, ValueError):
            total = 0
        spin = DurationSpin(tr("delay_later"), tr("delay_earlier"),
                            tr("delay_none"), tr("opt_hours"),
                            tr("opt_minutes"))
        spin.setValue(total)
        # As wide as its longest text, so stepping never resizes it.
        fm = spin.fontMetrics()
        widest = max(fm.horizontalAdvance(spin.textFromValue(v))
                     for v in (-1425, 1425, -1439, 1439, 0))
        return Stepper(spin, width=widest + 80)

    replay_delay_box = delay_box("replay_delay_min")
    epg_delay_box = delay_box("epg_delay_min")
    c = guide.card()
    c.row(tr("setting_upcoming_count"), epg_count_box)
    now_line_box = c.toggle(
        tr("set_guide_now_line"),
        win.settings.value("guide_now_line", "false") == "true",
        tr("set_guide_now_line_hint"))
    c = guide.card(tr("set_card_offsets"))
    c.row(tr("setting_replay_delay"), replay_delay_box,
          tr("set_replay_delay_hint"))
    c.row(tr("setting_epg_delay"), epg_delay_box,
          tr("set_epg_delay_hint"))
    refresh_epg_btn = QPushButton(tr("btn_refresh_epg"))
    clear_epg_btn = QPushButton(tr("btn_clear_epg"))
    refresh_epg_btn.clicked.connect(win._refresh_epg_now)
    clear_epg_btn.clicked.connect(win._clear_epg_cache)
    c = guide.card(tr("set_card_guide_data"))
    c.layout_row(hbox(refresh_epg_btn, clear_epg_btn),
                 tr("setting_epg_cache"))

    def commit_guide(put, changed: set, final: bool) -> None:
        # Stored once it is chosen: OK with nothing touched writes
        # nothing.
        if (flag(now_line_box) == "true"
                or win.settings.value("guide_now_line") is not None):
            put("guide_now_line", flag(now_line_box))
        if "guide_now_line" in changed:
            import time as _time

            from .epg_grid import EpgGrid
            for grid in win.findChildren(EpgGrid):
                grid._place_now_line(_time.time())
        put("epg_upcoming_count", epg_count_box.value())
        put("replay_delay_min", replay_delay_box.value())
        put("epg_delay_min", epg_delay_box.value())
        if "epg_delay_min" in changed:
            win.xmltv.delay_minutes = win._epg_delay_minutes()
    ctx.page_commits["guide"] = commit_guide


def build_multiview(ctx: SettingsContext) -> None:
    """Multiview: its window, what it does and its controls."""
    win, mv = ctx.win, ctx.pages["multiview"]
    is_on, flag = ctx.is_on, ctx.flag
    c = mv.card(tr("mv_sec_window"))
    mv_titlebar_box = c.toggle(tr("mv_set_titlebar"),
                               not is_on("mv_frameless", "true"))
    mv_ontop_box = c.toggle(tr("mv_set_on_top"),
                            is_on("mv_on_top", "false"))
    mv_geo_box = c.toggle(tr("mv_set_remember_geo"),
                          is_on("mv_remember_geo", "true"))
    mv_cells_box = QComboBox()
    for n, label in ((2, "2  (1 × 2)"), (4, "4  (2 × 2)"),
                     (6, "6  (2 × 3)"), (9, "9  (3 × 3)")):
        mv_cells_box.addItem(label, n)
    try:
        cur_cells = int(win.settings.value("mv_cells", 4))
    except (TypeError, ValueError):
        cur_cells = 4
    mv_cells_box.setCurrentIndex(max(0, mv_cells_box.findData(cur_cells)))
    c.row(tr("mv_set_cells"), mv_cells_box)
    c = mv.card(tr("mv_sec_behavior"))
    mv_stop_box = c.toggle(tr("mv_set_stop_docked"),
                           is_on("mv_stop_docked", "true"))
    mv_unmute_box = c.toggle(tr("mv_set_new_unmuted"),
                             is_on("mv_new_unmuted", "true"))
    mv_conflict_box = QComboBox()
    mv_conflict_box.addItem(tr("mv_conflict_ask"), "ask")
    mv_conflict_box.addItem(tr("mv_close"), "close")
    mv_conflict_box.addItem(tr("mv_keep"), "keep")
    cur_mode = str(win.settings.value("mv_conflict_mode", "ask"))
    mv_conflict_box.setCurrentIndex(
        max(0, mv_conflict_box.findData(cur_mode)))
    c.row(tr("mv_set_conflict"), mv_conflict_box)
    c = mv.card(tr("mv_sec_controls"))

    def whole(key: str, default: int) -> int:
        try:
            return int(float(win.settings.value(key, default)))
        except (TypeError, ValueError):
            return default

    mv_hide_spin = Stepper(lo=1, hi=15, value=whole("mv_autohide_secs", 2))
    c.row(tr("mv_set_autohide"), mv_hide_spin)
    mv_step_spin = Stepper(lo=1, hi=60, value=whole("mv_seek_step_min", 5))
    c.row(tr("mv_set_seek_step"), mv_step_spin)
    mv_info_btn = QPushButton(tr("mv_set_reset_info"))
    mv_info_btn.clicked.connect(
        lambda: win.settings.setValue("mv_info_seen", "false"))
    c.layout_row(hbox(mv_info_btn))

    def commit_multiview(put, changed: set, final: bool) -> None:
        put("mv_frameless",
            "false" if mv_titlebar_box.isChecked() else "true")
        put("mv_on_top", flag(mv_ontop_box))
        put("mv_remember_geo", flag(mv_geo_box))
        put("mv_stop_docked", flag(mv_stop_box))
        put("mv_new_unmuted", flag(mv_unmute_box))
        put("mv_conflict_mode", mv_conflict_box.currentData())
        put("mv_autohide_secs", mv_hide_spin.value())
        put("mv_seek_step_min", mv_step_spin.value())
        put("mv_cells", mv_cells_box.currentData())
    ctx.page_commits["multiview"] = commit_multiview


def build_recording(ctx: SettingsContext) -> None:
    """Recording: the folder and MP4, the size limits, the padding, the
    series recordings (an Enigma2 box's AutoTimer rules beside them) and
    the recorder found."""
    win, d, rec = ctx.win, ctx.dialog, ctx.pages["recording"]
    flag, nerd_link = ctx.flag, ctx.nerd_link
    rec_dir_btn = QPushButton(tr("btn_choose_folder"))
    c = rec.card(tr("set_card_folder"))
    rec_dir_row = c.row(tr("rec_saved_in"), rec_dir_btn,
                        win.rec.directory())

    def pick_rec_dir():
        path = QFileDialog.getExistingDirectory(
            d, tr("rec_choose_folder_title"), win.rec.directory(),
            file_dialog_options(QFileDialog.Option.ShowDirsOnly))
        if path:
            win.rec.set_directory(path)
            rec_dir_row.hint_label.setText(path)
            if win.mode == "rec":
                win._load_categories()

    rec_dir_btn.clicked.connect(pick_rec_dir)

    # A size and its unit in one stepper ("4.5 GB", "No limit"),
    # where a text box took a number and a dropdown beside it took
    # the unit.
    def stored_mb(vkey: str, ukey: str) -> int:
        try:
            val = float(win.settings.value(vkey, 0) or 0)
        except (TypeError, ValueError):
            val = 0.0
        mult = {"MB": 1, "GB": 1000, "TB": 1_000_000}.get(
            str(win.settings.value(ukey, "GB")), 1000)
        return max(0, round(val * mult))

    rec_max_mb = stored_mb("rec_max_value", "rec_max_unit")
    rec_total_mb = stored_mb("rec_total_value", "rec_total_unit")
    rec_max_spin = SizeSpin(
        [0, 500, 1000, 2000, 3000, 4000, 5000, 8000, 10000, 15000, 20000,
         25000, 30000, 40000, 50000, 75000, 100000, 150000, 200000,
         250000, 500000, 1_000_000, 2_000_000], tr("rec_cap_no_limit"),
        rec_max_mb)
    rec_total_spin = SizeSpin(
        [0, 10000, 25000, 50000, 100000, 200000, 250000, 500000,
         750000, 1_000_000, 1_500_000, 2_000_000, 3_000_000, 4_000_000,
         5_000_000, 8_000_000, 10_000_000, 20_000_000],
        tr("rec_cap_no_limit"), rec_total_mb)
    rec_max_spin.setValue(rec_max_mb)
    rec_total_spin.setValue(rec_total_mb)
    rec_max_box = Stepper(rec_max_spin, width=rec_max_spin.widest() + 80)
    rec_total_box = Stepper(rec_total_spin,
                            width=rec_total_spin.widest() + 80)
    # Saved as MP4 once a recording ended well (core/rec_mp4.py).
    mp4_box = c.toggle(tr("rec_mp4_setting"), rec_mp4.enabled(win.settings),
                       tr("rec_mp4_hint"))
    # Teletext and DVB subtitles cannot go in an MP4: such a
    # recording may stay a .ts (only asked while recordings become
    # MP4s).
    keep_subs_box = c.toggle(tr("rec_mp4_keep_subs"), rec_mp4.keep_subs(win.settings),
                             tr("rec_mp4_keep_subs_hint"))
    mp4_card = c
    c.shown_if(c.rows[-1][0], mp4_box.isChecked)
    mp4_box.toggled.connect(lambda _on: mp4_card.refresh())
    nerd_link(c, "set_link_rec",
              [tr("nerd_card_rec_files"), tr("nerd_card_rec_after")],
              "serve")
    c = rec.card(tr("set_card_limits"))
    c.row(tr("rec_max_label"), rec_max_box)
    c.row(tr("rec_total_label"), rec_total_box)
    # Minutes before and after a programme recorded from the guide
    # (and by a series rule); 0 and 0 record its listed times, as
    # before.
    from ..core import series_rules as srules
    pad_before, pad_after = srules.padding(win.settings)

    def pad_box(grid: list[int], value: int, name: str) -> Stepper:
        spin = GridSpin(sorted(set(grid) | {value}),
                        lambda v: tr("opt_minutes", n=v))
        spin.setValue(value)
        box = Stepper(spin, width=spin.widest() + 80)
        box.setAccessibleName(name)
        return box

    pad_before_box = pad_box([0, 1, 2, 3, 5, 10, 15, 20, 30], pad_before,
                             tr("rec_pad_before"))
    pad_after_box = pad_box([0, 1, 2, 3, 5, 10, 15, 20, 30, 45, 60, 90,
                             120], pad_after, tr("rec_pad_after"))
    c = rec.card(tr("set_card_padding"))
    c.row(tr("rec_pad_before"), pad_before_box, tr("rec_pad_before_hint"))
    c.row(tr("rec_pad_after"), pad_after_box, tr("rec_pad_after_hint"))
    # Series recordings: the rules of the source in use, deleted at
    # once (with the recordings they scheduled that have not
    # started).
    series_card = rec.card(tr("set_card_series"))
    rule_list = QListWidget(objectName="SeriesRuleList")
    rec_icon = page_icon("rec", win._action_pixmap, 16)
    # A rule that reminds instead of recording wears the guide's
    # bell.
    from .epg_grid import EpgGrid
    bell_icon = QIcon(EpgGrid._bar_pixmap("bell", QColor(P["text2"])))
    # An Enigma2 box's own AutoTimer rules join the app's, each row
    # saying who records it (ui/autotimer_ui.py).
    from .autotimer_ui import BoxRules, marked
    box_rules = BoxRules(win, rule_list, lambda: refill_rules(), d,
                         lambda: series_card.refresh())
    box_icon = page_icon("tv", win._action_pixmap, 16)

    def fill_rules() -> None:
        rule_list.clear()
        for r in sorted(win.series_rules.rules(),
                        key=lambda r: srules.norm(r.get("text"))):
            label = win._series_rule_label(r)
            item = QListWidgetItem(
                bell_icon if srules.action(r) == srules.REMIND else rec_icon,
                marked(label, False) if box_rules.marks else label)
            item.setData(Qt.ItemDataRole.UserRole, r["id"])
            rule_list.addItem(item)
        for item in box_rules.items(box_icon):
            rule_list.addItem(item)
        rule_list.setCurrentRow(0)

    fill_rules()
    fit_to_rows(rule_list)
    rules_none = QLabel(tr("series_none",
                           menu=tr("series_record_menu").rstrip("….")))
    rules_none.setObjectName("SettingsHint")
    rules_none.setWordWrap(True)
    rule_add = QPushButton(tr("series_add_word"))
    rule_del = QPushButton(tr("ctx_delete"))
    series_card.shown_if(
        series_card.widget(rules_none, tr("set_card_series")),
        lambda: rule_list.count() == 0)
    series_card.shown_if(
        series_card.widget(rule_list, tr("set_card_series")),
        lambda: rule_list.count() > 0)
    series_card.layout_row(hbox(rule_add, rule_del))
    if box_rules.active:
        series_card.shown_if(
            series_card.layout_row(hbox(*box_rules.buttons.values()),
                                   tr("set_card_series")),
            lambda: box_rules.present)
        series_card.shown_if(series_card.widget(box_rules.status, tr("set_card_series")),
                             lambda: bool(box_rules.status.text()))
        series_card.shown_if(series_card.note(tr("series_by_note")).parentWidget(),
                             lambda: box_rules.marks)
    series_card.note(tr("series_note", days=srules.DAYS))
    series_card.note(tr("series_remind_note"))

    def rules_sync() -> None:
        rule_del.setEnabled(rule_list.currentItem() is not None)
        box_rules.sync()
        series_card.refresh()

    def refill_rules() -> None:
        fill_rules()
        rules_sync()

    def rule_delete() -> None:
        item = rule_list.currentItem()
        if item is None:
            return
        if box_rules.current() is not None:
            box_rules.remove(box_rules.current())
            return
        row = rule_list.row(item)
        win._delete_series_rule(item.data(Qt.ItemDataRole.UserRole))
        fill_rules()
        rule_list.setCurrentRow(min(row, rule_list.count() - 1))
        rules_sync()

    def rule_add_word() -> None:
        if win._record_series(None, "", word=True, parent=d):
            fill_rules()
            rules_sync()
        elif box_rules.active:
            box_rules.load()        # it may have gone to the box

    rule_add.clicked.connect(rule_add_word)
    rule_del.clicked.connect(rule_delete)
    rule_list.currentRowChanged.connect(lambda _row: rules_sync())
    rules_sync()
    box_rules.load()
    rk, rexe = win.rec.recorder()
    c = rec.card()
    c.note(tr("rec_recorder_found", kind=rk, path=rexe) if rexe
           else tr("rec_recorder_missing"))
    c.note(tr("rec_howto_hint"))

    def commit_recording(put, changed: set, final: bool) -> None:
        if mp4_box.isChecked() != rec_mp4.enabled(win.settings):
            put(rec_mp4.SETTING, flag(mp4_box))
        if keep_subs_box.isChecked() != rec_mp4.keep_subs(win.settings):
            put(rec_mp4.SUBS_SETTING, flag(keep_subs_box))
        # A size left as it was keeps its stored spelling ("2.5" TB).
        if rec_max_box.value() != rec_max_mb:
            val, unit = mb_value(rec_max_box.value())
            put("rec_max_value", val)
            put("rec_max_unit", unit)
        if rec_total_box.value() != rec_total_mb:
            val, unit = mb_value(rec_total_box.value())
            put("rec_total_value", val)
            put("rec_total_unit", unit)
        # Untouched and never set: not written (0 is the default).
        for skey, box, was in ((srules.PAD_BEFORE_KEY, pad_before_box, pad_before),
                               (srules.PAD_AFTER_KEY, pad_after_box, pad_after)):
            if box.value() != was or win.settings.value(skey) is not None:
                put(skey, box.value())
    ctx.page_commits["recording"] = commit_recording


def build_casting(ctx: SettingsContext) -> None:
    """Casting: its own page, beside Playback (it was a card there) - how
    long a pause on the TV may be, a rewind buffer kept beside a cast, the
    devices added by address."""
    win, casting = ctx.win, ctx.pages["casting"]
    is_on, flag, nerd_link = ctx.is_on, ctx.flag, ctx.nerd_link
    # Pausing a cast channel records it on this computer until you
    # press play, so this is the one setting that decides how long a
    # pause on the TV may be. It sat under Recording, where nobody
    # casting looks.
    try:
        cast_pause_mb = round(float(
            win.settings.value("cast_pause_gb", 4.5) or 4.5) * 1000)
    except (TypeError, ValueError):
        cast_pause_mb = 4500
    cast_grid = [1000, 2000, 3000, 4500, 6000, 8000, 10000, 15000,
                 20000, 30000, 50000]
    cast_spin = GridSpin(cast_grid, lambda v: size_text(v, ""),
                         cast_grid[0], max(cast_grid[-1], cast_pause_mb))
    cast_spin.setValue(max(cast_grid[0], cast_pause_mb))
    cast_pause_box = Stepper(cast_spin, width=cast_spin.widest() + 80)
    c = casting.card(tr("set_card_cast"))
    c.row(tr("cast_pause_label"), cast_pause_box, tr("cast_pause_hint"))
    # Off unless asked for: it is a second connection to the
    # provider, and nearly every account allows one.
    c = casting.card()
    cast_buffer_box = c.toggle(
        tr("set_cast_keep_buffer"), is_on("cast_keep_buffer", "false"),
        tr("set_cast_keep_buffer_hint"))
    win._cast_added_card(casting.card(tr("set_card_cast_added")), nerd_link)

    def commit_casting(put, changed: set, final: bool) -> None:
        if cast_pause_box.value() != cast_pause_mb:
            put("cast_pause_gb", f"{cast_pause_box.value() / 1000:g}")
        put("cast_keep_buffer", flag(cast_buffer_box))
    ctx.page_commits["casting"] = commit_casting
