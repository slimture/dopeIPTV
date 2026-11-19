"""The player's audio and subtitle tracks, and a channel's own settings.

Which subtitle track a stream opens with, and whether it shows: a film or
an episode opens with a track chosen and hidden, so turning subtitles on
is only showing it - mpv answers a track chosen during playback with a
refresh seek, seconds of frozen picture from a slow panel (_sub_defaults,
then _apply_sub_rule once the tracks are known). The one-shot choices the
window arms for the next play() (a title's remembered tracks, its subtitle
files, which channel it is), a subtitle file loaded into what plays (a
copy without stray right-to-left marks), the options menu's track entries
(``fill_audio_tracks``, ``fill_sub_tracks``, ``track_label``: the phone
remote's sheet is made of them) and a pick from them. Nerd: a channel's
own settings (core/channel_prefs.py), and the audio delay chosen kept for
the channel or the title playing.

Mixed into EmbeddedPlayer; it reads the player's attributes.
"""
from __future__ import annotations

import hashlib
import os

from PyQt6.QtCore import QStandardPaths

from ..core import channel_prefs
from ..core.log import log
from ..core.privacy import for_log
from ..i18n import current_language, tr
from .subtitle_text import fixed_bytes


# The tags a subtitle track's language comes as, per app language: ISO
# 639-2 (both forms where they differ) and 639-1.
_SUB_LANG_TAGS = {
    "en": "eng,en", "sv": "swe,sv", "es": "spa,es", "de": "ger,deu,de",
    "fr": "fre,fra,fr", "zh": "chi,zho,zh", "ru": "rus,ru", "th": "tha,th",
    "pt": "por,pt", "it": "ita,it", "nl": "dut,nld,nl", "pl": "pol,pl",
    "hr": "hrv,hr", "sr": "srp,sr", "el": "gre,ell,el", "tr": "tur,tr",
    "uk": "ukr,uk", "id": "ind,id", "vi": "vie,vi", "hi": "hin,hi",
    "ja": "jpn,ja", "ko": "kor,ko", "sw": "swa,sw", "ar": "ara,ar",
    "fa": "per,fas,fa", "he": "heb,he", "ur": "urd,ur",
}


def _set_forced_only(m, on: bool) -> None:
    """Show only the forced subtitle events, or everything. mpv 0.37 renamed
    sub-forced-only to sub-forced-events-only, and the libmpv in use may be
    either side of that (Homebrew's and the macOS, Windows and Flatpak
    builds are newer, the AppImage's older): the new name, else the old.
    Setting the old name on a new mpv raised - and a subtitle that had
    loaded fine was then reported as unusable."""
    for name in ("sub-forced-events-only", "sub-forced-only"):
        try:
            m[name] = on
            return
        except Exception:
            continue


_SUB_FIX_MAX_BYTES = 20 * 1024 * 1024


def _subtitle_to_load(path: str) -> str:
    """*path*, or a copy of it without the right-to-left marks a file
    translated from Arabic or Hebrew can keep around every line
    (subtitle_text.py). The copy goes to the cache under the same file
    name, so mpv still reads a language from it."""
    try:
        if os.path.getsize(path) > _SUB_FIX_MAX_BYTES:
            return path
        with open(path, "rb") as fh:
            fixed = fixed_bytes(fh.read())
    except OSError:
        return path
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.CacheLocation)
    if fixed is None or not base:
        return path
    key = hashlib.sha1(os.path.abspath(path).encode(
        "utf-8", "surrogateescape")).hexdigest()[:12]
    folder = os.path.join(base, "subtitles", key)
    copy = os.path.join(folder, os.path.basename(path))
    try:
        os.makedirs(folder, exist_ok=True)
        with open(copy, "wb") as fh:
            fh.write(fixed)
    except OSError as e:
        log.info("subtitle: no fixed copy of %s: %s", for_log(path), e)
        return path
    log.info("subtitle: right-to-left marks taken out of %s",
             for_log(os.path.basename(path)))
    return copy


def _delay_key(channel: str) -> str:
    """Where the audio delay of a stream that is no channel (a film, a
    file) is kept (Nerd) - and where a channel's was, before it moved into
    its own settings (core/channel_prefs.py)."""
    return channel_prefs.legacy_key(channel)


class TracksMixin:
    def _sub_defaults(self, mode: str, live: bool, set_opt) -> None:
        """The subtitle track a stream opens with, and whether it shows.

        mpv answers a subtitle track chosen during playback with a refresh
        seek: it drops what it has buffered and asks for the stream again
        from a little earlier - on a film from a slow panel, five seconds or
        more of frozen picture each time subtitles were turned on. So a film
        or an episode opens with a track chosen even when none is to show,
        hidden: it is read along with the rest, and turning subtitles on is
        only showing it. Whether the setting shows it is decided once the
        tracks are known (_apply_sub_rule). A live channel cannot be sought
        - choosing a track later costs nothing there - and opens as before.
        """
        m = self.video.mpv
        s = self._settings
        langs = [x for x in (s.value("sub_lang", "") or "",
                             s.value("sub_lang2", "") or "") if x]
        self._sub_rule = None
        if live or mode == "forced":
            set_opt("subs-fallback", "default")      # mpv's own default
            set_opt("sub-visibility", True)
            if mode == "off":
                set_opt("sid", "no")
                _set_forced_only(m, False)
                return
            set_opt("sid", "auto")
            # A primary + optional fallback language: mpv's slang takes a
            # comma-separated priority list, so "swe,eng" means prefer
            # Swedish subs but fall back to English when absent - each
            # language every way a stream tags it ("swe", "sv").
            set_opt("slang", ",".join(channel_prefs.all_spellings(langs))
                    if mode == "lang" else "")
            _set_forced_only(m, mode == "forced")
            return
        set_opt("sid", "auto")
        # The track picked is the one most likely to be turned on: the
        # languages asked for, then the one turned on last, then the app's
        # own. Picking another later is the slow part. In "auto" a track
        # the file flags still shows (_apply_sub_rule).
        set_opt("slang", ",".join(self._preferred_sub_langs(langs)))
        set_opt("subs-fallback", "yes")              # some track, always
        _set_forced_only(m, False)
        set_opt("sub-visibility", False)
        if mode != "off":
            self._sub_rule = (mode, channel_prefs.all_spellings(langs))

    def _own_subtitles(self, own: dict, live: bool, set_opt) -> None:
        """Nerd: a channel's own subtitle choice, over what _sub_defaults
        just chose - "off", or a language first in line (the app's own
        after it). Its catch-up opens like a film: a track chosen, hidden,
        shown once the tracks say it is in that language (_apply_sub_rule)."""
        s = self._settings
        langs = [x for x in (s.value("sub_lang", "") or "",
                             s.value("sub_lang2", "") or "") if x]
        if live:
            also = langs if s.value("sub_mode", "auto") == "lang" else []
        else:
            also = self._preferred_sub_langs(langs)
        for prop, value in channel_prefs.subtitle_props(own, live, also):
            set_opt(prop, value)
        _set_forced_only(self.video.mpv, False)
        self._sub_rule = channel_prefs.subtitle_rule(own, live)

    def _preferred_sub_langs(self, langs: list[str]) -> list[str]:
        """The languages a film's hidden track is chosen by: those asked
        for in Settings, the one turned on last, the app's language."""
        s = self._settings
        last = (s.value("sub_last_lang", "") or "") if s else ""
        out: list[str] = []
        for value in (*langs, last,
                      _SUB_LANG_TAGS.get(current_language(), "")):
            for tag in str(value).split(","):
                for spelled in channel_prefs.spellings(tag) or [tag.strip().lower()]:
                    if spelled and spelled not in out:
                        out.append(spelled)
        return out

    def _apply_sub_rule(self, m) -> None:
        """The stream's tracks are known: show the track chosen hidden at
        the start if the setting would have chosen it itself - one the file
        flags default or forced, or (by language) one in a language asked
        for. The same tracks mpv showed before tracks were chosen hidden.

        In "auto" the hidden track is picked by language, so a track the
        file flags may be another one: that one is taken, once, at the very
        start - what mpv itself picked before."""
        try:
            tracks = m.track_list or []
        except Exception:
            return
        if not tracks:
            return                        # not loaded yet: the next poll
        mode, langs = self._sub_rule
        self._sub_rule = None
        subs = [t for t in tracks if t.get("type") == "sub"]
        sel = next((t for t in subs if t.get("selected")), None)
        if mode == "auto":
            flagged = (next((t for t in subs if t.get("default")), None)
                       or next((t for t in subs if t.get("forced")), None))
            if flagged is None:
                return
            try:
                if sel is None or sel.get("id") != flagged.get("id"):
                    m["sid"] = flagged.get("id")
                m["sub-visibility"] = True
            except Exception:
                pass
            return
        if sel is None:
            return
        if (sel.get("default") or sel.get("forced")
                or (mode == "lang" and channel_prefs.same_language(sel.get("lang"), langs))):
            try:
                m["sub-visibility"] = True
            except Exception:
                pass

    def set_track_prefs(self, aid=None, sid=None) -> None:
        """Arm one-shot audio/subtitle track ids for the next play() - the
        window passes a title's remembered choices here right before
        replaying/resuming it. Cleared by play() whether applied or not."""
        self._pref_aid = aid
        self._pref_sid = sid

    def set_channel(self, ckey: str | None) -> None:
        """Arm which channel the next play() is - its source's id and its
        stream's id (core/channel_prefs.channel_key); None for anything else.
        Nerd plays it with the settings of its own and keeps its audio delay
        there. Consumed by play(), whether it is a channel or not."""
        self._chan_armed = ckey or None

    def _prefs_store(self):
        if self._chan_store is None:
            self._chan_store = channel_prefs.ChannelPrefs(self._settings)
        return self._chan_store

    def _channel_own(self, chan: str | None, stream: str | None = None) -> dict:
        """The settings of its own channel *chan* plays with: {} for none,
        for anything that is no channel, and while Nerd is off (kept, not
        applied). A delay kept the old way for *stream* moves in. Never
        raises: a bad stored value must not stop a channel from playing."""
        if not chan or self._settings is None:
            return {}
        try:
            if not channel_prefs.enabled():
                return {}
            return self._prefs_store().get(
                chan, legacy=_delay_key(stream) if stream else None)
        except Exception as e:
            log.debug("channel settings of %s not read: %s", chan, e)
            return {}

    def channel_prefs_changed(self, chan: str | None) -> None:
        """The channel playing now got new settings of its own (its dialog):
        put in force as a Settings change is - the picture and the delay at
        once; the tracks it plays may wait for its next start."""
        m = self.video.mpv
        if (not chan or chan != self._chan_key or m is None
                or self.current_url is None):
            return
        self._chan_own = self._channel_own(chan)
        self.apply_default_options()
        if self._delay_chan == chan:
            try:
                value = channel_prefs.delay(self._chan_own.get("adelay"))
                m["audio-delay"] = value
                self._delay_set = bool(value)
            except Exception as e:
                log.debug("audio delay of %s not set: %s", chan, e)

    def set_sub_files(self, paths: list[str]) -> None:
        """Arm subtitle files for the next play() - one fetched or picked
        for this title before comes back with it. One-shot, like the track
        choices."""
        self._pref_sub_files = [str(p) for p in paths if p]

    def add_subtitle(self, path: str, title: str = "", lang: str = "") -> bool:
        """Load a subtitle file into what is playing and show it."""
        m = self.video.mpv
        if m is None or not self.current_url:
            return False
        try:
            m.command("sub-add", _subtitle_to_load(str(path)), "select",
                      title or "", lang or "")
        except Exception as e:
            log.warning("sub-add %s failed: %s", for_log(path), e)
            return False
        # Loaded: from here on it is only about showing it, which must not
        # turn a loaded subtitle into a failure.
        _set_forced_only(m, False)
        try:
            m["sub-visibility"] = True
        except Exception as e:
            log.debug("sub-visibility after sub-add failed: %s", e)
        self.subtitle_added.emit(str(path))
        return True

    def _load_subtitle_file(self) -> None:
        from PyQt6.QtWidgets import QFileDialog
        path, _flt = QFileDialog.getOpenFileName(
            self.window(), tr("subs_pick_title"), "",
            tr("opt_subtitles") + " (*.srt *.ass *.ssa *.vtt *.sub)")
        if path:
            self.add_subtitle(path)

    def _remember_delay(self, value: float) -> None:
        """Nerd: the audio delay chosen, kept for the channel playing - not
        for a private playback, which keeps nothing of what is played (a
        channel's own delay says it was watched), though private viewing
        ended before it did: then it holds for this stream only, and the
        next start sets the delay back."""
        from ..core.privacy import playing_private
        if playing_private():
            self._delay_set = abs(value) >= 0.001
            return
        chan = getattr(self, "_delay_chan", None)
        if chan and self._settings is not None:
            try:
                self._chan_own = self._prefs_store().set_delay(chan, value)
                self._delay_set = bool(channel_prefs.delay(value))
            except Exception as e:
                log.debug("audio delay of %s not kept: %s", chan, e)
            return
        key = getattr(self, "_delay_key", None)
        if key and self._settings is not None:
            self._delay_set = abs(value) >= 0.001
            if abs(value) < 0.001:
                self._settings.remove(key)
            else:
                self._settings.setValue(key, f"{value:g}")

    def _tracks_of(self, kind: str) -> list[dict]:
        m = self.video.mpv
        if m is None:
            return []
        try:
            return [t for t in (m.track_list or []) if t.get("type") == kind]
        except Exception:
            return []

    @staticmethod
    def track_label(t: dict) -> str:
        """A track as the menus name it: its language and title, and whether
        it is forced or a file of its own."""
        parts = [t.get("lang") or "", t.get("title") or ""]
        label = " ".join(p for p in parts if p).strip() or tr("player_track_n", n=t.get("id"))
        marks = [tr(key) for flag, key in (("forced", "player_track_forced"),
                                          ("external", "player_track_external")) if t.get(flag)]
        return f"{label} ({', '.join(marks)})" if marks else label

    def fill_audio_tracks(self, menu) -> None:
        """The audio tracks as the options menu lists them, the one playing
        checked; each picked through _pick_track (the phone remote's sheet
        is made of the same entries)."""
        for t in self._tracks_of("audio"):
            act = menu.addAction(self.track_label(t))
            act.setCheckable(True)
            act.setChecked(bool(t.get("selected")))
            act.triggered.connect(
                lambda _c=False, tid=t.get("id"): self._pick_track("aid", tid))

    def fill_sub_tracks(self, menu) -> list[dict]:
        """Off and the subtitle tracks as the options menu lists them -
        teletext and DVB subtitles by language and page - the one shown
        checked; returns the tracks."""
        m = self.video.mpv
        off = menu.addAction(tr("opt_off"))
        off.setCheckable(True)
        sub_tracks = self._tracks_of("sub")
        # A track chosen hidden (_sub_defaults) is off, as far as anyone
        # watching can tell.
        try:
            shown = bool(m["sub-visibility"]) if m else False
        except Exception:
            shown = True
        off.setChecked(not (shown and any(t.get("selected") for t in sub_tracks)))
        off.triggered.connect(lambda _c=False: self._pick_track("sid", "no"))
        for t in sub_tracks:
            if self._ttx_is_track(t):
                # Teletext and DVB subtitles: language and page.
                self._ttx_add_entries(menu, t, shown)
                continue
            act = menu.addAction(self.track_label(t))
            act.setCheckable(True)
            act.setChecked(bool(t.get("selected")) and shown)
            act.triggered.connect(
                lambda _c=False, tid=t.get("id"): self._pick_track("sid", tid))
        return sub_tracks

    def _pick_track(self, prop: str, tid) -> None:
        """User picked an audio/subtitle track from the options menu: apply
        it and announce the choice so the window can remember it per title.

        Subtitles go off by hiding the track, not by letting it go, and on by
        showing it: choosing a track is what makes mpv drop its buffer and
        ask the provider again (_sub_defaults), so it happens only when the
        track asked for is another one."""
        if prop == "sid":
            self._ttx_forget()
        if prop != "sid":
            self._set_mpv(prop, tid)
        elif tid == "no":
            self._set_mpv("sub-visibility", False)
        else:
            m = self.video.mpv
            try:
                current = m.sid if m is not None else None
            except Exception:
                current = None
            if current != tid:
                self._set_mpv("sid", tid)
            self._remember_sub_lang(m, tid)
            # Picked on purpose: all of it, whatever "forced only" says.
            if m is not None:
                _set_forced_only(m, False)
            # A stream opened with subtitles off leaves them hidden.
            self._set_mpv("sub-visibility", True)
        self.track_selected.emit(prop, tid)

    def _remember_sub_lang(self, m, tid) -> None:
        """The language of a subtitle turned on by hand: the next film opens
        with a track in it chosen, hidden, so turning it on is instant. Not
        from a private playback: it would say what language was watched."""
        from ..core.privacy import playing_private
        if m is None or not self._settings or playing_private():
            return
        try:
            track = next((t for t in (m.track_list or [])
                          if t.get("type") == "sub" and t.get("id") == tid),
                         None)
        except Exception:
            return
        lang = str((track or {}).get("lang") or "").strip().lower()
        if lang and lang not in ("und", "unknown"):
            self._settings.setValue("sub_last_lang", lang)
