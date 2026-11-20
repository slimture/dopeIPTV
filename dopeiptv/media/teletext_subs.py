"""Teletext and DVB subtitles in the player (core/teletext_subs.py).

Their entries in the subtitle menu ("Swedish - teletext 199", "Finnish -
DVB subtitles"), the teletext page a track plays (mpv's ``teletext-page``,
changed while it plays), the page chosen per channel and the preferred
subtitle language picking a teletext page. The pages come from the PMT:
read from the app's own rewind buffer, or from the head of a local .ts
file - never from a connection of its own. Where the libmpv's FFmpeg has
no libzvbi, a teletext entry is shown as unavailable and why, and a track
that does not start says so once - never an error.

Mixed into EmbeddedPlayer; it reads the player's attributes.
"""
from __future__ import annotations

import os
import threading
import weakref
from typing import Any

from PyQt6.QtCore import QLocale, QTimer
from PyQt6.QtWidgets import QInputDialog

from ..core import channel_prefs, privacy
from ..core import teletext as ttx
from ..core import teletext_subs as tsubs
from ..core.log import log
from ..i18n import tr

#: Set once a teletext track was chosen and mpv let go of it: its FFmpeg
#: cannot decode teletext, whatever the library check said.
_broken: list[bool] = [False]
#: The languages with a word of their own in the app (Settings' list).
_WORDS = ("swe", "eng", "nor", "dan", "fin", "ger", "fre", "spa", "ita",
          "por", "pol", "ara", "tur")
_LOCAL_HEAD = 4 << 20


def language_label(code: str) -> str:
    """A track's language as the menu names it: the app's own word where it
    has one, else the language in its own words, else the code."""
    code = channel_prefs.lang(code)
    if not code:
        return tr("sub_lang_unknown")
    for main in _WORDS:
        if code in channel_prefs.spellings(main):
            return tr(f"lang_{main}")
    two = next((s for s in channel_prefs.spellings(code) if len(s) == 2), "")
    if two:
        loc = QLocale(two)
        name = loc.nativeLanguageName() if loc.language() != QLocale.Language.C else ""
        if name:
            return name[:1].upper() + name[1:]
    return code.upper()


def choice_label(c: tsubs.Choice) -> str:
    """"Swedish - teletext 199", "Swedish - teletext", "Finnish - DVB
    subtitles", and "(hard of hearing)" after one for them."""
    lang = language_label(c.lang)
    if c.kind == tsubs.DVBSUB:
        text = tr("sub_dvb", lang=lang)
    elif c.page is not None:
        text = tr("sub_ttx_page", lang=lang, page=ttx.page_text(c.page))
    else:
        text = tr("sub_ttx", lang=lang)
    return f"{text} ({tr('sub_hoh')})" if c.hoh else text


def unavailable_reason() -> str:
    """Why this libmpv cannot show teletext subtitles; "" when it can (or
    cannot tell yet)."""
    if _broken[0]:
        return tr("sub_ttx_no_libzvbi")
    try:
        from .players import teletext_decoder
        if teletext_decoder() is False:
            return tr("sub_ttx_no_libzvbi")
    except Exception as e:
        log.debug("teletext: decoder check failed: %s", e)
    return ""


class TeletextSubsMixin:
    _ttx_token = 0
    _ttx_pending = False
    _ttx_live = False
    _ttx_tracks: list[dict] = []
    _ttx_streams: dict | None = None
    _ttx_auto: tsubs.Choice | None = None
    _ttx_current: tsubs.Choice | None = None
    _ttx_check_tid: Any = None
    _ttx_version: tuple[int, int] | None = None
    _ttx_store: tsubs.PageStore | None = None
    # The window's teletext viewer (None: not offered) and its key's text.
    open_teletext = None
    teletext_key = None

    # -- each stream ----------------------------------------------------------

    def _ttx_reset(self, live: bool = False) -> None:
        """A stream starts (or stops): what was known of the last one goes."""
        self._ttx_token += 1
        self._ttx_pending = True
        self._ttx_live = live
        self._ttx_tracks = []
        self._ttx_streams = None
        self._ttx_auto = None
        self._ttx_current = None
        self._ttx_check_tid = None

    def _ttx_mpv_version(self) -> tuple[int, int]:
        if self._ttx_version is None:
            m = self.video.mpv
            try:
                self._ttx_version = tsubs.mpv_version(m.mpv_version if m else "")
            except Exception:
                self._ttx_version = (0, 0)
        return self._ttx_version

    def _ttx_langs(self) -> list[str]:
        """The subtitle languages asked for, the channel's own first."""
        s = self._settings
        out = []
        own = (getattr(self, "_chan_own", None) or {}).get("slang")
        if own and own != channel_prefs.SUB_OFF:
            out.append(str(own))
        elif self._ttx_live and s is not None and str(s.value("sub_live", "true")) == "false":
            return out              # live channels open without subtitles
        if s is not None and (own or s.value("sub_mode", "auto") == "lang"):
            out += [x for x in (s.value("sub_lang", "") or "",
                                s.value("sub_lang2", "") or "") if x]
        return out

    def _ttx_before_load(self, set_opt) -> None:
        """mpv's teletext page before a stream opens: never mpv 0.37's
        default 100 - the index page drawn over the picture - should mpv
        pick a teletext track itself."""
        set_opt("teletext-page", tsubs.default_page(self._ttx_langs(),
                                                    self._ttx_mpv_version()))

    def _ttx_page_store(self) -> tsubs.PageStore:
        if self._ttx_store is None:
            self._ttx_store = tsubs.PageStore(self._settings)
        return self._ttx_store

    # -- the tracks known -----------------------------------------------------

    def _ttx_poll(self, m) -> None:
        """(the position poll) Once the stream's tracks are known: its
        teletext and DVB subtitle tracks, the PMT's pages looked up, and the
        teletext page this channel or language plays."""
        try:
            tracks = m.track_list or []
        except Exception:
            return
        if not tracks:
            return
        self._ttx_pending = False
        self._ttx_tracks = [t for t in tracks
                            if t.get("type") == "sub" and tsubs.track_kind(t)]
        if not self._ttx_tracks:
            return
        self._ttx_lookup_pages()
        self._ttx_apply()

    def _ttx_lookup_pages(self) -> None:
        """The PMT's pages, off the window's thread: from the rewind buffer
        when it has the stream, else the head of a local file."""
        store = self._dvr.store if getattr(self, "_dvr", None) is not None else None
        path = ""
        url = self.current_url or ""
        if store is None and url and "://" not in url and os.path.isfile(url):
            path = url
        if store is None and not path:
            return
        me, token = weakref.ref(self), self._ttx_token

        def work() -> None:
            streams = None
            try:
                if store is not None:
                    streams = ttx.streams_from_store(store)
                else:
                    with open(path, "rb") as fh:
                        streams = ttx.read_streams(fh.read(_LOCAL_HEAD))
            except Exception as e:
                log.debug("teletext: no PMT read: %s", e)
            player = me()
            if player is not None and streams:
                try:
                    player._ttx_streams_ready.emit(token, streams)
                except RuntimeError:
                    pass

        threading.Thread(target=work, name="ttx-pmt", daemon=True).start()

    def _ttx_streams_got(self, token: int, streams: object) -> None:
        if token != self._ttx_token or not isinstance(streams, dict):
            return
        self._ttx_streams = streams
        m = self.video.mpv
        auto = self._ttx_auto
        if auto is not None and m is not None:
            # Chosen with no page known: now its page for that language.
            try:
                if m.sid == auto.track:
                    self._ttx_apply(again=True)
                return
            except Exception:
                return
        if self._ttx_current is None:
            self._ttx_apply(again=True)

    def _ttx_choices(self) -> list[tsubs.Choice]:
        return tsubs.all_choices(self._ttx_tracks, self._ttx_streams)

    def _ttx_apply(self, again: bool = False) -> None:
        """The channel's own teletext page, or one in the language asked for
        (live, where mpv itself picked no subtitle in it), shown; a teletext
        track mpv chose itself given its page."""
        m = self.video.mpv
        options = self._ttx_choices()
        if m is None or not options:
            return
        choice = self._ttx_page_store().find(self._chan_key, options)
        langs = self._ttx_langs()
        if choice is None and self._ttx_live and langs \
                and not self._ttx_other_sub_in(m, langs):
            choice = tsubs.pick(options, langs)
        if choice is None:
            self._ttx_fix_page(m, options, langs)
            return
        if choice.kind == tsubs.TELETEXT and unavailable_reason():
            return
        self._ttx_play(choice, remember=False, auto=True)

    def _ttx_other_sub_in(self, m, langs: list[str]) -> bool:
        """mpv picked a subtitle track of another kind in one of *langs*."""
        try:
            tracks = m.track_list or []
        except Exception:
            return False
        for t in tracks:
            if t.get("type") != "sub" or not t.get("selected") or tsubs.track_kind(t) \
                    == tsubs.TELETEXT:
                continue
            tl = channel_prefs.lang(t.get("lang"))
            if any(tl and tl in channel_prefs.spellings(x) for x in langs):
                return True
        return False

    def _ttx_fix_page(self, m, options: list[tsubs.Choice], langs: list[str]) -> None:
        """A teletext track selected by mpv itself plays the page of its
        language, never the one mpv defaults to."""
        try:
            sid = m.sid
        except Exception:
            return
        mine = [c for c in options if c.kind == tsubs.TELETEXT and c.track == sid]
        if not mine:
            return
        choice = tsubs.pick(mine, langs) or mine[0]
        self._ttx_set_page(choice)
        self._ttx_current = choice

    def _ttx_set_page(self, choice: tsubs.Choice) -> None:
        if choice.kind != tsubs.TELETEXT:
            return
        page = tsubs.mpv_page(choice, self._ttx_mpv_version())
        self._set_mpv("teletext-page", page)

    def _ttx_play(self, choice: tsubs.Choice, remember: bool, auto: bool = False) -> None:
        """Show *choice*: its page set first (it changes while the track
        plays - no refresh), the track selected only when it is another."""
        m = self.video.mpv
        if m is None:
            return
        self._ttx_set_page(choice)
        try:
            current = m.sid
        except Exception:
            current = None
        if current != choice.track:
            self._set_mpv("sid", choice.track)
        try:
            for name in ("sub-forced-events-only", "sub-forced-only"):
                try:
                    m[name] = False
                    break
                except Exception:
                    continue
        except Exception:
            pass
        self._set_mpv("sub-visibility", True)
        self._ttx_current = choice
        self._ttx_auto = choice if auto and choice.page is None else None
        if remember and self._chan_key and choice.kind == tsubs.TELETEXT \
                and not privacy.playing_private():
            # A channel's page says it was watched: not kept for a private
            # playback (it shows for this one all the same).
            self._ttx_page_store().put(self._chan_key, choice)
        if choice.kind == tsubs.TELETEXT:
            self._ttx_check_tid = choice.track
            QTimer.singleShot(2500, self._ttx_check)

    def _ttx_check(self) -> None:
        """A teletext track chosen a moment ago: mpv let go of it when its
        FFmpeg cannot decode teletext. Said once, the entry then unavailable."""
        tid, self._ttx_check_tid = self._ttx_check_tid, None
        m = self.video.mpv
        if tid is None or m is None or self._ttx_current is None \
                or self._ttx_current.track != tid:
            return
        try:
            sid = m.sid
            present = any(t.get("id") == tid and t.get("type") == "sub"
                          for t in (m.track_list or []))
        except Exception:
            return
        if sid == tid or not present:
            return
        if not _broken[0]:
            _broken[0] = True
            log.warning("teletext: mpv could not start the teletext track - "
                        "its FFmpeg has no libzvbi")
            self.set_overlay_info(tr("sub_ttx_no_libzvbi_short"))
        self._ttx_current = None

    def _ttx_forget(self) -> None:
        """Subtitles off or another track picked by hand: the channel's
        teletext page goes."""
        if self._ttx_current is not None and self._chan_key \
                and not privacy.playing_private():
            self._ttx_page_store().forget(self._chan_key)
        self._ttx_current = None
        self._ttx_auto = None

    # -- the subtitle menu ----------------------------------------------------

    def _ttx_is_track(self, t: dict) -> bool:
        return bool(tsubs.track_kind(t))

    def _ttx_add_entries(self, menu, track: dict, shown: bool) -> None:
        """*track*'s entries: a teletext track's pages (or languages), a DVB
        subtitle track as itself; teletext unavailable said so."""
        m = self.video.mpv
        why = unavailable_reason()
        try:
            page_now = int(m["teletext-page"]) if m is not None else None
        except Exception:
            page_now = None
        selected = bool(track.get("selected")) and shown
        streams = self._ttx_streams
        for c in tsubs.choices(track, streams):
            label = choice_label(c)
            if c.kind == tsubs.TELETEXT and why:
                act = menu.addAction(f"{label} - {tr('sub_ttx_unavailable')}")
                act.setEnabled(False)
                act.setToolTip(why)
                continue
            act = menu.addAction(label)
            act.setCheckable(True)
            on = selected
            if on and c.kind == tsubs.TELETEXT:
                cur = self._ttx_current
                on = (cur is not None and cur.same_page(c)) or (
                    page_now is not None
                    and page_now == tsubs.mpv_page(c, self._ttx_mpv_version())
                    and (cur is None or cur.track == c.track))
            act.setChecked(on)
            act.triggered.connect(lambda _c=False, ch=c: self._ttx_pick(ch))

    def _ttx_menu_tail(self, menu) -> None:
        """After the tracks: a teletext page typed by hand, and why teletext
        is unavailable when it is."""
        if not any(tsubs.track_kind(t) == tsubs.TELETEXT for t in self._ttx_tracks):
            return
        why = unavailable_reason()
        if why:
            note = menu.addAction(tr("sub_ttx_no_libzvbi_short"))
            note.setEnabled(False)
            note.setToolTip(why)
            menu.setToolTipsVisible(True)
            return
        menu.addAction(tr("sub_ttx_page_pick"), self._ttx_ask_page)

    def _ttx_pick(self, choice: tsubs.Choice) -> None:
        """Picked in the menu: shown, and its page kept for the channel."""
        self._ttx_play(choice, remember=True)
        self.track_selected.emit("sid", choice.track)

    def _ttx_ask_page(self) -> None:
        """"Teletext page…": any page 100-899 as subtitles, kept for the
        channel."""
        options = [c for c in self._ttx_choices() if c.kind == tsubs.TELETEXT]
        if not options:
            return
        cur = self._ttx_current
        start = int(ttx.page_text(cur.page)) if cur is not None and cur.page else (
            tsubs.mpv_page(options[0], self._ttx_mpv_version()) or 199)
        value, ok = QInputDialog.getInt(self, tr("sub_ttx_page_pick").rstrip("…"),
                                        tr("sub_ttx_page_ask"), max(100, min(899, start)),
                                        100, 899)
        page = ttx.page_from_text(str(value)) if ok else None
        if page is None:
            return
        base = options[0] if cur is None else next(
            (c for c in options if c.track == cur.track), options[0])
        match = next((c for c in options if c.page == page and c.track == base.track), None)
        self._ttx_pick(match or tsubs.Choice(base.track, tsubs.TELETEXT, base.lang,
                                             page, False, base.pid))
