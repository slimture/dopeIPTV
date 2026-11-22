"""The cast window (``CastDialog``): the Chromecasts, DLNA TVs, Kodis and
Apple TVs the search finds, the ones added by their address, the tracks
and the quality to cast with, and the TV cast to last on top and picked
(``last_device``, ``remember_device``, ``last_first``,
``cast_button_text``); what it says when the search found nothing
(``none_found_text``). The casting itself is providers/chromecast.py's,
providers/casting.py's and the window's (ui/mw_cast.py).
"""

from __future__ import annotations

import json

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget,
    QPushButton, QVBoxLayout, QWidget,
)

from ..core.workers import run_async
from ..i18n import tr
from ..providers.cast_bridge import normalise_quality, probe_tracks
from ..providers.chromecast import ChromecastManager, cast_import_error


#: The TV cast to last, for a channel and for a title (a film, an episode, a
#: recording...): the household's, a device's name and nothing more.
LAST_DEVICE_KEY = "cast_last_device"


def _last_devices(settings) -> dict:
    try:
        got = json.loads(str(settings.value(LAST_DEVICE_KEY, "") or "{}"))
    except (TypeError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def last_device(settings, live: bool) -> str:
    """The TV cast to last for a channel (*live*) or a title - else the one
    cast to last at all; "" for none."""
    got = _last_devices(settings)
    return str(got.get("live" if live else "title") or got.get("any") or "")


#: How many devices are known by what they were when last cast to.
KNOWN_MAX = 32


def remember_device(settings, live: bool, name: str, ident: str = "") -> None:
    """*name* is the TV cast to last (for this kind of thing, and at all) -
    and *ident* which device it was (providers/casting.CastHub.ident: its
    kind, its own id, its address), so that another one answering to that
    name later is not taken for it."""
    if not name:
        return
    got = _last_devices(settings)
    got["live" if live else "title"] = name
    got["any"] = name
    if ident:
        ids = got.get("ids")
        ids = dict(ids) if isinstance(ids, dict) else {}
        ids.pop(name, None)
        ids[name] = ident                       # the newest last
        got["ids"] = dict(list(ids.items())[-KNOWN_MAX:])
    settings.setValue(LAST_DEVICE_KEY, json.dumps(got))


def known_ident(settings, name: str) -> str:
    """Which device *name* was when it was last cast to; "" for one never
    cast to (or before the app kept this)."""
    ids = _last_devices(settings).get("ids")
    return str(ids.get(name) or "") if isinstance(ids, dict) else ""


def same_device(known: str, now: str) -> bool:
    """Whether two of CastHub.ident's "kind:id@address" are one device: by
    its own id where it has one - a TV the router gave another address is
    still that TV - else by its address."""
    def parts(ident: str) -> tuple[str, str, str]:
        head, _, address = ident.rpartition("@")
        kind, _, own = head.partition(":")
        return kind, own, address
    k_kind, k_own, k_addr = parts(known)
    n_kind, n_own, n_addr = parts(now)
    if k_kind != n_kind:
        return False
    if k_own or n_own:
        return k_own == n_own
    return k_addr == n_addr


def last_first(names: list[str], last: str) -> list[str]:
    """*names* with *last* at the top - when it is there: a TV that is gone
    is just not offered first."""
    if last and last in names:
        return [last] + [n for n in names if n != last]
    return list(names)


def cast_button_text(name: str | None) -> str:
    """The cast window's button for the device picked: "Cast to Living room
    TV" (a long name cut), or plain "Cast"."""
    if not name:
        return tr("cast_cast")
    short = name if len(name) <= 30 else name[:29] + "…"
    return tr("cast_cast_to", name=short)


def none_found_text() -> str:
    """What the cast window says when the search found nothing: and why a
    home network split in parts (VLANs) may hide a device, what fixes that
    on the router, and adding it by its address."""
    return "\n\n".join((tr("cast_none_found"), tr("vlan_nothing_found"),
                         tr("cast_none_found_add")))


class CastDialog(QDialog):
    """Scan for Chromecasts, DLNA TVs and Kodis and cast a stream to one."""

    def __init__(self, window: object, url: str, title: str,
                 codecs: list[str] | None = None,
                 audio_index: int = 0, start: float = 0.0,
                 tracks: dict | None = None, probe: bool = True,
                 source: str | None = None, managing: bool = False,
                 chosen: tuple | None = None, live: bool = False) -> None:
        super().__init__(window)
        self.window = window
        self.url = url
        self.stream_title = title
        # What mpv says the channel is, when it happens to be playing here.
        # Only ever used to explain a refusal - never to refuse in advance:
        # AC-3 plays fine on an Ultra or a Google TV.
        self.codecs = codecs or []
        # The audio track the app is playing. If you switched language here,
        # that is the one you meant to send - so the dialog opens on it. Zero
        # is the stream's own default, and leaving it there is what keeps the
        # cast native.
        self.audio_index = audio_index
        # Where to start. The app already asked whether to resume, so this is
        # a decision already made - the TV just has to honour it.
        self.start = start
        self.duration = 0.0
        # A channel, as against a thing with an end. Said outright by whoever
        # opened the dialog, because it cannot be read off the stream: mpv
        # answers with the seekable window for a live playlist, and ffprobe
        # measures an archive .ts down to the second. Either number turned a
        # broadcast into a "title" on the television - a name and a bar drawn
        # over the picture, and the default receiver leaves those up for as
        # long as it believes it is playing something with a length.
        self.live = live
        # What the player already knows. Free, and it saves opening the
        # stream a second time on an account that has one connection.
        self.tracks = tracks or {}
        # Whether asking the provider directly is worth anything right now.
        self.probe = probe
        # What the converter should read. The same stream in the format the
        # player uses, which is not always the one the receiver is offered.
        self.source = source or url
        self.audio_list: list[dict] = []
        self.subs_list: list[dict] = []
        # Opened on a cast that is already running: it is here to change
        # something about it, not to start one. Nothing may disturb what is
        # playing - not a rescan, and not a question about resuming, which
        # was answered when the cast began.
        self.managing = managing
        self.chosen = chosen
        self.height, self.fps = 0, 0.0
        # Devices that answer to a name cast to before but are not the
        # device it was then (another id, another address): never picked
        # for the person, and said (_note_stranger).
        self._strangers: set[str] = set()
        self.setWindowTitle(tr("cast_title"))
        self.setMinimumWidth(400)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)

        self.status = QLabel(tr("cast_scanning"))
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self._cast())
        lay.addWidget(self.list, 1)
        # A Kodi that mDNS does not find (another subnet, announcing off), or
        # one whose web server wants a login: added by hand. The window owns
        # that form (ui/mw_cast.py), so the button is there only with it.
        self.kodi_btn = QPushButton(tr("cast_add_kodi"))
        self.kodi_btn.clicked.connect(self._add_kodi)
        self.kodi_btn.setVisible(callable(getattr(window, "add_kodi", None)))
        # A Chromecast or a TV the search does not reach (another part of a
        # network split in VLANs): by its address, kept for next time.
        # Opened on one added that way, it offers to forget it.
        self.addr_btn = QPushButton(tr("cast_add_by_address"))
        self.addr_btn.clicked.connect(self._add_by_address)
        self.addr_btn.setVisible(callable(getattr(window, "add_cast_device", None)))
        kodi_row = QHBoxLayout()
        kodi_row.addWidget(self.kodi_btn)
        kodi_row.addWidget(self.addr_btn)
        kodi_row.addStretch(1)
        lay.addLayout(kodi_row)

        # Audio and subtitle choice. Both are honoured by converting the
        # stream here, because the receiver plays whatever the stream hands it
        # and renders no subtitle that is carried inside one - so leaving both
        # on their default is what keeps a cast native.
        self.audio_box = QComboBox()
        self.subs_box = QComboBox()
        # Named after the device it belongs to. The dialog is opened once per
        # thing you cast, so a bare "Older Chromecast" sitting in it reads as
        # a question about this broadcast - something to answer every time -
        # when it is a standing property of the receiver.
        # Named once a device is picked, and not shown before then: it is a
        # question about a particular receiver, and there is no answering it
        # while none is selected.
        # Three tiers, named after what the device IS. Nobody knows their
        # receiver's maximum profile level; everybody knows which one they
        # bought. A checkbox could only say "old or not", which was too
        # blunt in both directions: it sent 720p to a first-generation
        # dongle that decodes 1080p perfectly, and offered an Ultra a
        # ceiling it does not need.
        self.kind_label = QLabel("")
        self.kind_box = QComboBox()
        for key in ("original", "hd", "oldest"):
            self.kind_box.addItem(tr(f"cast_kind_{key}"), key)
        self.kind_label.hide()
        self.kind_box.hide()
        # Each row in a widget of its own, so a DLNA TV - which plays the
        # stream as it is, with no converter to choose tracks - can hide it.
        self.track_rows: list[QWidget] = []
        for box, label in ((self.audio_box, tr("cast_audio")),
                           (self.subs_box, tr("cast_subtitles"))):
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            cap = QLabel(label)
            cap.setMinimumWidth(80)
            row.addWidget(cap)
            box.addItem(tr("cast_reading_tracks"), None)
            box.setEnabled(False)
            row.addWidget(box, 1)
            lay.addWidget(holder)
            self.track_rows.append(holder)
        self.track_note = QLabel(tr("cast_track_note"))
        self.track_note.setWordWrap(True)
        # Dimmed by the theme's own rule; QSS has no "opacity", so the
        # inline style this had was silently ignored.
        self.track_note.setObjectName("DetailMeta")
        self.track_note.hide()
        lay.addWidget(self.track_note)
        # One question, in the words of the problem: nobody thinks "my
        # receiver tops out below fifty frames a second", they think "it
        # stutters on the TV". Remembered per device, and it is a ceiling -
        # an SD or HD channel is already below it and goes over untouched.
        lay.addWidget(self.kind_label)
        lay.addWidget(self.kind_box)
        self.quality_note = QLabel(tr("cast_quality_note"))
        self.quality_note.setWordWrap(True)
        self.quality_note.setObjectName("DetailMeta")
        # Shown with the question, not after it is answered: which receivers
        # this is for is what you need in order to answer it, so hiding it
        # until the box is ticked shows it to the only person who no longer
        # needs it.
        self.quality_note.hide()
        lay.addWidget(self.quality_note)
        self.dlna_note = QLabel(tr("cast_dlna_note"))
        self.dlna_note.setWordWrap(True)
        self.dlna_note.setObjectName("DetailMeta")
        self.dlna_note.hide()
        lay.addWidget(self.dlna_note)
        # AirPlay (macOS): what to do next, built by the receiver itself
        # (services/airplay_macos.py). Apple's AirPlay button is in a window
        # of its own, which nothing here makes or refreshes.
        ap = self._airplay()
        self.airplay_row = ap.dialog_row(self) if ap is not None else None
        if self.airplay_row is not None:
            self.airplay_row.hide()
            lay.addWidget(self.airplay_row)
            found = getattr(ap, "routes_changed", None)
            if found is not None:
                found.connect(self._airplay_found)
        self.kind_box.currentIndexChanged.connect(self._quality_changed)
        self.list.currentItemChanged.connect(
            lambda *_a: self._show_device_quality())
        self.audio_box.currentIndexChanged.connect(self._track_changed)
        self.subs_box.currentIndexChanged.connect(self._track_changed)

        btns = QHBoxLayout()
        self.rescan_btn = QPushButton(tr("cast_rescan"))
        self.cast_btn = QPushButton(tr("cast_cast"), objectName="Primary")
        self.stop_btn = QPushButton(tr("cast_stop"))
        close_btn = QPushButton(tr("common_close"))
        for b in (self.rescan_btn, self.cast_btn, self.stop_btn, close_btn):
            btns.addWidget(b)
        lay.addLayout(btns)

        # The TV picked is named on the button, and Enter casts to it: with
        # the one used last on top and picked, one press.
        self.cast_btn.setDefault(True)
        self.list.currentItemChanged.connect(
            lambda item, *_a: self.cast_btn.setText(
                cast_button_text(item.text() if item is not None else None)))
        self.list.currentItemChanged.connect(lambda *_a: self._note_stranger())
        self.rescan_btn.clicked.connect(self._scan)
        self.cast_btn.clicked.connect(self._cast)
        self.stop_btn.clicked.connect(self._stop)
        close_btn.clicked.connect(self.accept)
        if managing:
            # No discovery: it disconnects every device to start again, and
            # the one in the list is the one currently playing.
            self._show_remembered()
            self._set_status(tr("cast_casting_to",
                                name=getattr(window, "_cast_device", "")))
        else:
            self._scan()
        self._load_tracks()

    # -- audio / subtitle tracks -------------------------------------------

    def _load_tracks(self) -> None:
        """What is in the stream - from the player if it is playing it, and
        only otherwise from ffprobe, which costs a connection."""
        if self.tracks or not self.probe:
            self._fill_tracks(self.tracks)
            return
        def done(tracks):
            try:
                self._fill_tracks(tracks)
            except RuntimeError:
                pass                       # dialog closed while probing

        run_async(self.window.pool, lambda: probe_tracks(self.url), done,
                  lambda _msg: done({}))

    @staticmethod
    def _track_label(t: dict) -> str:
        bits = [b for b in (t.get("lang"), t.get("title")) if b]
        bits.append(t.get("codec") or "?")
        return " · ".join(bits)

    def _fill_tracks(self, tracks: dict) -> None:
        self.duration = (0.0 if self.live
                         else float((tracks or {}).get("duration") or 0.0))
        self.height = int((tracks or {}).get("height") or 0)
        self.fps = float((tracks or {}).get("fps") or 0.0)
        audio = (tracks or {}).get("audio") or []
        subs = (tracks or {}).get("subtitle") or []
        # Every subtitle can be offered now. A text one is handed to the
        # receiver as a WebVTT rendition beside the picture, which needs no
        # libass and nothing drawn into the frames; a picture-based one is
        # still drawn in with overlay, which every ffmpeg has.
        self.audio_list, self.subs_list = audio, subs
        self.audio_box.blockSignals(True)
        self.subs_box.blockSignals(True)
        self.audio_box.clear()
        self.subs_box.clear()
        self.audio_box.addItem(tr("cast_track_default"), None)
        self.subs_box.addItem(tr("cast_subs_off"), None)
        for t in audio:
            self.audio_box.addItem(self._track_label(t), t)
        for t in subs:
            self.subs_box.addItem(self._track_label(t), t)
        # Open on the track the app is playing - but only when that is not
        # the one the stream plays by default anyway. Choosing a track means
        # converting, and a film with seven audio tracks where mpv landed on
        # the default one would otherwise be converted for no reason at all,
        # by a choice nobody made.
        chosen = (audio[self.audio_index]
                  if 0 <= self.audio_index < len(audio) else None)
        if chosen and not chosen.get("default") and self.audio_index > 0:
            self.audio_box.setCurrentIndex(self.audio_index + 1)
        # A running cast opens on what it is actually playing.
        if self.chosen:
            cur_a, cur_s = self.chosen
            if cur_a is not None:
                self.audio_box.setCurrentIndex(cur_a.get("index", 0) + 1)
            if cur_s is not None:
                self.subs_box.setCurrentIndex(cur_s.get("index", 0) + 1)
        self.audio_box.blockSignals(False)
        self.subs_box.blockSignals(False)
        self._track_changed()
        # A single audio track is no choice at all, and no subtitles means
        # nothing to pick from - leave those boxes out of the way.
        self.audio_box.setEnabled(len(audio) > 1)
        self.subs_box.setEnabled(bool(subs))

    def _quality_key(self, device: str) -> str:
        return f"cast_quality_{device}"

    def _is_dlna(self, name: str | None) -> bool:
        """A device that plays the stream as it is: a DLNA TV or a Kodi."""
        hub = self.window.cast
        as_is = getattr(hub, "plays_as_is", None) or getattr(hub, "is_dlna", None)
        return bool(callable(as_is) and as_is(name))

    def _is_kodi(self, name: str | None) -> bool:
        is_kodi = getattr(self.window.cast, "is_kodi", None)
        return bool(callable(is_kodi) and is_kodi(name))

    def _is_airplay(self, name: str | None) -> bool:
        is_airplay = getattr(self.window.cast, "is_airplay", None)
        return bool(callable(is_airplay) and is_airplay(name))

    def _airplay(self):
        for mgr in getattr(self.window.cast, "added", None) or []:
            if getattr(mgr, "KIND", "") == "airplay":
                return mgr
        return None

    def _airplay_found(self, found: bool) -> None:
        """AVRouteDetector found AirPlay routes after the list was filled:
        the entry is added, never taken away while the dialog is open."""
        ap = self._airplay()
        if not found or ap is None or self.managing:
            return
        try:
            if not self.isVisible():
                return                 # a dialog closed earlier
            names = [self.list.item(i).text() for i in range(self.list.count())]
            for name in ap.entries():
                if name not in names:
                    self.list.insertItem(0, name)
        except RuntimeError:
            pass                       # the dialog is going

    def _show_device_quality(self) -> None:
        """Show what this device is remembered as being."""
        item = self.list.currentItem()
        dlna = item is not None and self._is_dlna(item.text())
        # AirPlay: the Apple TV is handed what it plays, and Apple's menu
        # picks it - none of the Chromecast's choices apply.
        airplay = item is not None and self._is_airplay(item.text())
        # A TV fetches the stream itself and plays it as it is: the track
        # and picture choices are the converter's, which only a Chromecast
        # is fed through.
        for row in self.track_rows:
            row.setVisible(not (dlna or airplay))
        if dlna:
            self.dlna_note.setText(tr("cast_kodi_note")
                                   if self._is_kodi(item.text())
                                   else tr("cast_dlna_note"))
        self.dlna_note.setVisible(dlna)
        if self.airplay_row is not None:
            self.airplay_row.setVisible(airplay)
        if dlna or airplay:
            self.track_note.hide()
        else:
            self._track_changed()
        self.kind_label.setVisible(item is not None and not (dlna or airplay))
        self.kind_box.setVisible(item is not None and not (dlna or airplay))
        self.quality_note.setVisible(item is not None and not (dlna or airplay))
        if not item or dlna or airplay:
            return
        want = normalise_quality(str(self.window.settings.value(
            self._quality_key(item.text()), "original") or "original"))
        self.kind_label.setText(tr("cast_device_kind", name=item.text()))
        self.kind_box.blockSignals(True)
        at = self.kind_box.findData(want)
        self.kind_box.setCurrentIndex(at if at >= 0 else 0)
        self.kind_box.blockSignals(False)

    def _quality_changed(self) -> None:
        item = self.list.currentItem()
        if not item:
            return
        want = self.kind_box.currentData() or "original"
        self.window.settings.setValue(self._quality_key(item.text()), want)

    def quality(self) -> str:
        item = self.list.currentItem()
        if not item:
            return "original"
        return normalise_quality(str(self.window.settings.value(
            self._quality_key(item.text()), "original") or "original"))

    def _track_changed(self) -> None:
        item = self.list.currentItem()
        dlna = item is not None and (self._is_dlna(item.text())
                                     or self._is_airplay(item.text()))
        self.track_note.setVisible(self._chosen() != (None, None)
                                   and not dlna)

    def _chosen(self) -> tuple[dict | None, dict | None]:
        return self.audio_box.currentData(), self.subs_box.currentData()

    def _set_status(self, text: str) -> None:
        try:
            self.status.setText(text)
        except RuntimeError:
            pass

    # -- which device a name stands for ----------------------------------------

    def _hub_says(self, what: str, name: str | None) -> str:
        fn = getattr(self.window.cast, what, None)
        try:
            return str(fn(name) or "") if callable(fn) and name else ""
        except Exception:
            return ""

    def _is_stranger(self, name: str) -> bool:
        """Whether the device answering to *name* now is another than the
        one cast to under that name before. Anything on the network can
        announce itself as "Living room TV"."""
        known = known_ident(self.window.settings, name)
        now = self._hub_says("ident", name)
        return bool(known and now and not same_device(known, now))

    def _add_device(self, name: str) -> None:
        """*name* in the list, its address in its tooltip: which device the
        name stands for."""
        self.list.addItem(name)
        where = self._hub_says("where", name)
        if where:
            self.list.item(self.list.count() - 1).setToolTip(where)

    def _note_stranger(self) -> None:
        """The device picked is not the one its name was the last time:
        said, with its address, before anything is sent to it."""
        item = self.list.currentItem()
        if item is None or item.text() not in self._strangers:
            return
        self._set_status(tr("cast_other_device", name=item.text(),
                            address=self._hub_says("where", item.text())))

    def _show_remembered(self) -> None:
        """The devices from last time, without asking the network."""
        if self.list.count():
            return
        remembered = self.window.settings.value("cast_devices", "") or ""
        last = last_device(self.window.settings, self.live)
        for name in last_first([n for n in remembered.split("\n") if n], last):
            self._add_device(name)
        # Land on the one that is playing, when there is one.
        active = getattr(self.window, "_cast_device", None)
        for i in range(self.list.count()):
            if self.list.item(i).text() == active:
                self.list.setCurrentRow(i)
                return
        if self.list.count():
            self.list.setCurrentRow(0)

    def _scan(self) -> None:
        # Fill the list from last time's result immediately. Discovery takes
        # several seconds, and staring at an empty box while the device you
        # cast to yesterday is right there is just waiting for nothing. The
        # scan still runs and replaces this the moment it lands.
        self._show_remembered()
        self._set_status(tr("cast_scanning"))
        self.rescan_btn.setEnabled(False)

        def done(names):
            try:
                self.rescan_btn.setEnabled(True)
                self.window.settings.setValue(
                    "cast_devices", "\n".join(names or []))
                self.list.clear()
                names = list(names or [])
                # A device that answers to a name cast to before but is not
                # the device it was: listed, never picked for the person.
                self._strangers = {n for n in names if self._is_stranger(n)}
                last = last_device(self.window.settings, self.live)
                # The TV used last on top, picked: one press casts to it.
                for name in last_first(names, "" if last in self._strangers else last):
                    self._add_device(name)
                status = (tr("cast_devices_found", n=len(names)) if names
                          else none_found_text())
                if not names and not ChromecastManager.available():
                    # Nothing found, and Chromecasts could not even be looked
                    # for: say why, or the empty list reads as "you have none".
                    why = cast_import_error()
                    status += "\n\n" + (
                        tr("msg_cast_package_broken", why=why) if why
                        else tr("msg_cast_needs_package"))
                if last in self._strangers:
                    status += "\n\n" + tr("cast_other_device", name=last,
                                          address=self._hub_says("where", last))
                self._set_status(status)
                first = next((i for i in range(self.list.count())
                              if self.list.item(i).text() not in self._strangers), -1)
                if first >= 0 and last not in self._strangers:
                    self.list.setCurrentRow(first)
            except RuntimeError:
                pass

        def fail(msg):
            try:
                self.rescan_btn.setEnabled(True)
            except RuntimeError:
                return
            self._set_status(tr("cast_scan_failed", msg=msg))

        run_async(self.window.pool, self.window.cast.scan, done, fail)

    def _cast(self) -> None:
        item = self.list.currentItem()
        if not item:
            return
        name = item.text()
        # A Kodi whose login would go over plain http: asked first, before
        # the player here lets go of anything (mw_cast._kodi_plain_ask).
        ask = getattr(self.window, "_kodi_plain_ask", None)
        kodi = ask(name) if self._is_kodi(name) and callable(ask) else "send"
        if not kodi:
            self._set_status(tr("cast_err_kodi_plain", name=name))
            return
        self._set_status(tr("cast_starting", name=name))

        # Free the provider connection BEFORE the receiver goes for the
        # stream, not after it succeeds. On a single-connection account the
        # old order could not work: the local player still held the one
        # connection, the provider refused the Chromecast's request, and the
        # receiver reported IDLE/ERROR - after which we dutifully stopped
        # local playback, having already lost the cast. Stopping first costs
        # nothing on an account with room to spare.
        stop = getattr(self.window, "stop_local_playback_for_cast", None)
        settle = bool(callable(stop) and stop())

        # The cast strip in the detail pane is the only thing that says a cast
        # is running once this dialog is closed - local playback has stopped,
        # so the player pane is just black.
        def done(n):
            self._strangers.discard(n)
            remember_device(self.window.settings, self.live, n,
                            self._hub_says("ident", n))
            self._set_status(tr("airplay_pick_window") if self._is_airplay(n)
                             else tr("cast_casting_to", name=n))
            self._banner(n, self.stream_title)
            if self._is_airplay(n):
                # This dialog is modal: while it is open, macOS hands no
                # click to Apple's AirPlay menu and shows that menu behind
                # it. The AirPlay window carries on alone; the cast strip
                # controls the cast from here.
                QTimer.singleShot(0, self.accept)

        def failed(msg):
            self._set_status(tr("cast_failed", msg=msg))
            self._banner(None, "")

        audio, subs = self._chosen()
        if self._is_dlna(name):
            audio = subs = None         # a TV plays the stream as it is
        elif self._is_airplay(name):
            subs = None                 # the sound track the app plays goes
        # A channel with catch-up goes through the converter on purpose, so
        # that pausing it means something. The converter writes the stream to
        # a spool on this machine as it goes; the receiver reads that spool at
        # its own pace, so a pause is simply the television stopping reading
        # while the recording carries on - and play continues on the very next
        # frame. Nothing is asked of the provider at all, which is what every
        # other way of pausing a broadcast foundered on.
        dvr = bool((getattr(self.window, "_cast_ctx", None) or {})
                   .get("archive"))
        # Leave everything the strip needs to offer another track later
        # without asking the provider again.
        ctx = getattr(self.window, "_cast_ctx", None)
        # A channel watched from the rewind buffer: its pieces, for a
        # receiver fed through a converter here (never its loopback address).
        feed = ctx.get("feed") if isinstance(ctx, dict) else None
        if self._is_airplay(name):
            # An Apple TV plays a channel live, never from the buffer
            # (mw_cast._cast_can_go_back); the window lets the pieces go.
            feed = None
        more = {"feed": feed} if feed is not None else {}
        # The device this name was the last time it was cast to: nothing is
        # sent to another one answering to it now (CastHub.cast) - unless it
        # is picked here as what it is, its address said (_note_stranger).
        expect = "" if name in self._strangers else known_ident(self.window.settings, name)
        if expect:
            more["expect"] = expect
        if isinstance(ctx, dict):
            ctx.update(url=self.url, source=self.source, codecs=self.codecs,
                       height=self.height, fps=self.fps,
                       audio=audio, subs=subs, duration=self.duration,
                       tracks={"audio": self.audio_list,
                               "subtitle": self.subs_list})
        quality = ("original" if self._is_dlna(name) or self._is_airplay(name)
                   else self.quality())

        def work():
            if kodi == "https":
                # "Use HTTPS": that Kodi tried over https first - kept so once
                # it answers, else nothing is sent to it.
                self.window.cast.kodi.use_https(name)
            return self.window.cast.cast(name, self.url, self.stream_title,
                                         self.codecs, audio, subs,
                                         self.start, self.duration,
                                         settle, self.source, quality,
                                         self.height, self.fps, dvr, **more)

        run_async(self.window.pool, work, done, failed)

    def _add_by_address(self) -> None:
        """Add a Chromecast, a TV or a Kodi by its address - or forget the
        one added that way that is chosen."""
        item = self.list.currentItem()
        is_added = getattr(self.window.cast, "is_added", None)
        current = (item.text() if item is not None and callable(is_added)
                   and is_added(item.text()) else None)
        label = self.window.add_cast_device(self, current)
        if label is None:
            return
        # Forgotten: out of the list. Another one added: beside it.
        self._took(label, current, None if label == "" else {label, current})

    def _add_kodi(self) -> None:
        """Add a Kodi by its address, or give a listed one its login."""
        item = self.list.currentItem()
        current = item.text() if item is not None and self._is_kodi(item.text()) else None
        label = self.window.add_kodi(self, current)
        if label is None:
            return
        self._took(label, current,
                   getattr(getattr(self.window.cast, "kodi", None), "devices", None))

    def _took(self, label: str, current: str | None, known) -> None:
        """A device added (or changed, or forgotten: *label* "") from the
        dialog it was opened on (*current*): the list follows, and is kept
        for the next panel. *known*: the names still there, None when not
        known."""
        # The one it was opened on goes when it is forgotten, or now has
        # another name at the same address; a device at a new address is one
        # more, beside it.
        if current and label != current and (known is None or current not in known):
            for i in range(self.list.count()):
                if self.list.item(i).text() == current:
                    self.list.takeItem(i)
                    break
        if label:
            if not any(self.list.item(i).text() == label
                       for i in range(self.list.count())):
                self.list.addItem(label)
            for i in range(self.list.count()):
                if self.list.item(i).text() == label:
                    self.list.setCurrentRow(i)
                    break
        names = [self.list.item(i).text() for i in range(self.list.count())]
        self.window.settings.setValue("cast_devices", "\n".join(names))

    def _banner(self, device: str | None, title: str) -> None:
        show = getattr(self.window, "show_cast_strip", None)
        if callable(show):
            show(device, title)

    def _stop(self) -> None:
        self._banner(None, "")
        run_async(self.window.pool, self.window.cast.stop,
                  lambda _: self._set_status(tr("cast_stopped")),
                  lambda msg: self._set_status(tr("cast_stop_failed", msg=msg)))
