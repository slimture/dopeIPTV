"""An Enigma2 box's own windows, from its channel menu: its info with the
tuner's signal (asked every 2 s while the window shows), a picture of what
its TV shows (the picture, the menus or both) with Save, and its remote
control. Every request to the box runs off the window's thread; an answer
that comes after the window closed is dropped."""

from __future__ import annotations

import time
from typing import Any, Callable

from PyQt6 import sip
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QGridLayout, QHBoxLayout,
    QLabel, QPushButton, QSizePolicy, QVBoxLayout,
)

from ..core.log import log
from ..core.workers import run_async
from ..i18n import tr
from ..providers.enigma2 import signal_rows
from .theme import ACCENTS, P

SIGNAL_MS = 2000


def why(err: Any) -> str:
    """What went wrong, as the user reads it."""
    msg = getattr(err, "localized", None)
    return msg() if callable(msg) else str(err or "")


def transcode_text(client: Any) -> str:
    """The box's transcoding as its source has it: off, absent, or the step
    with what is asked of the box."""
    step = client.transcode_step() if hasattr(client, "transcode_step") else ""
    if not step:
        return tr("nerd_src_no_transcode")
    if (getattr(client, "transcoder", None) or {}).get("present") is False:
        return tr("box_tc_absent")
    rate, width, height = client.transcode_params(step)
    bps = rate * 1000 if rate < 100_000 else rate
    return f"{tr('e2_tc_' + step)} · {bps / 1e6:g} Mbit/s · {width}×{height}"


class _BoxWindow(QDialog):
    """A window of the box's: its client, the pool its requests run on and
    a line that says how the last one went."""

    def __init__(self, parent: Any, client: Any, pool: Any, title: str) -> None:
        super().__init__(parent)
        self.client, self.pool = client, pool
        self.setWindowTitle(title)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color:{P['muted']}; font-size:12px;")

    def _ask(self, fn: Callable[..., Any], done: Callable[[Any], None], *args: Any) -> None:
        run_async(self.pool, fn, done, self._failed, *args)

    def _gone(self) -> bool:
        return sip.isdeleted(self)

    def _failed(self, err: Any = None) -> None:
        if self._gone():
            return
        self._busy = False
        self.status.setText(tr("box_failed", why=why(err)))


class BoxInfoDialog(_BoxWindow):
    """The box: model, image, web interface, tuners, transcoding - and the
    signal of the tuner behind what it shows itself, kept up to date."""

    def __init__(self, parent: Any, client: Any, pool: Any) -> None:
        info = dict(getattr(client, "box_info", None) or {})
        super().__init__(parent, client, pool, tr("box_info_title"))
        self.setMinimumWidth(420)
        self._busy = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        rows = [(tr("box_info_model"), info.get("model") or "—"),
                (tr("box_info_image"), info.get("image") or "—"),
                (tr("box_info_webif"), info.get("webif") or "—"),
                (tr("box_info_tuners"), ", ".join(info.get("tuners") or []) or "—"),
                (tr("box_info_transcoding"), transcode_text(client))]
        for i, (name, value) in enumerate(rows):
            grid.addWidget(self._name(name), i, 0)
            v = QLabel(str(value))
            v.setTextFormat(Qt.TextFormat.PlainText)    # what the box says of itself
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            v.setWordWrap(True)
            grid.addWidget(v, i, 1)
        lay.addLayout(grid)
        head = QLabel(tr("box_signal"))
        head.setStyleSheet("font-weight:700;")
        lay.addWidget(head)
        self.signal = QGridLayout()
        self.signal.setHorizontalSpacing(18)
        self.signal.setVerticalSpacing(4)
        lay.addLayout(self.signal)
        note = QLabel(tr("box_signal_note"))
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{P['muted']}; font-size:12px;")
        lay.addWidget(note)
        lay.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._shown_rows: list[tuple[str, str]] = []
        self._show_signal([(tr("box_signal"), "…")])
        self.timer = QTimer(self)
        self.timer.setInterval(SIGNAL_MS)
        self.timer.timeout.connect(self.poll)

    @staticmethod
    def _name(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color:{P['muted']};")
        return lbl

    def showEvent(self, event: Any) -> None:  # noqa: N802 - Qt's name
        super().showEvent(event)
        self.timer.start()
        self.poll()

    def hideEvent(self, event: Any) -> None:  # noqa: N802 - Qt's name
        self.timer.stop()
        super().hideEvent(event)

    def poll(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._ask(self.client.signal, self._came)

    def _came(self, sig: Any) -> None:
        if self._gone():
            return
        self._busy = False
        self.status.setText("")
        self._show_signal(signal_rows(sig if isinstance(sig, dict) else None))

    def _show_signal(self, rows: list[tuple[str, str]]) -> None:
        if rows == self._shown_rows:
            return
        self._shown_rows = rows
        while self.signal.count():
            item = self.signal.takeAt(0)
            if item is not None and item.widget() is not None:
                item.widget().deleteLater()
        for i, (name, value) in enumerate(rows):
            self.signal.addWidget(self._name(name), i, 0)
            v = QLabel(value)
            v.setTextFormat(Qt.TextFormat.PlainText)
            v.setStyleSheet("font-family:monospace;")
            v.setWordWrap(True)
            self.signal.addWidget(v, i, 1)


class BoxScreenshotDialog(_BoxWindow):
    """What the box's TV shows, as the box grabs it, with Save."""

    MODES = (("all", "box_grab_all"), ("video", "box_grab_video"), ("osd", "box_grab_osd"))

    def __init__(self, parent: Any, client: Any, pool: Any) -> None:
        super().__init__(parent, client, pool, tr("box_screenshot_title"))
        self.resize(720, 500)
        self._busy = False
        self._data = b""
        self._pixmap: QPixmap | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(10)
        bar = QHBoxLayout()
        self.mode = QComboBox()
        for value, key in self.MODES:
            self.mode.addItem(tr(key), value)
        self.again = QPushButton(tr("box_grab_again"))
        self.again.clicked.connect(self.take)
        self.mode.currentIndexChanged.connect(self.take)
        bar.addWidget(self.mode)
        bar.addWidget(self.again)
        bar.addStretch(1)
        lay.addLayout(bar)
        self.picture = QLabel("…")
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.picture.setMinimumSize(320, 180)
        self.picture.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.picture.setStyleSheet(f"background:{P['input']}; border-radius:8px;")
        lay.addWidget(self.picture, 1)
        lay.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Close)
        self.save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_btn.setEnabled(False)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        QTimer.singleShot(0, self.take)

    def take(self, *_a: Any) -> None:
        if self._busy or self._gone():
            return
        self._busy = True
        self.again.setEnabled(False)
        self.status.setText(tr("box_grab_taking"))
        self._ask(self.client.screenshot, self._came, self.mode.currentData())

    def _failed(self, err: Any = None) -> None:
        if self._gone():
            return
        super()._failed(err)
        self.again.setEnabled(True)

    def _came(self, data: Any) -> None:
        if self._gone():
            return
        self._busy = False
        self.again.setEnabled(True)
        pix = QPixmap()
        if not isinstance(data, bytes) or not pix.loadFromData(data):
            self.status.setText(tr("err_box_no_grab"))
            return
        self._data, self._pixmap = data, pix
        self.status.setText(f"{pix.width()}×{pix.height()}")
        self.save_btn.setEnabled(True)
        self._fit()

    def _fit(self) -> None:
        if self._pixmap is None:
            return
        self.picture.setPixmap(self._pixmap.scaled(
            self.picture.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt's name
        super().resizeEvent(event)
        self._fit()

    def save(self) -> None:
        if not self._data:
            return
        ext = "png" if self._data.startswith(b"\x89PNG") else (
            "bmp" if self._data.startswith(b"BM") else "jpg")
        name = time.strftime(f"box-%Y%m%d-%H%M%S.{ext}")
        path, _f = QFileDialog.getSaveFileName(self, tr("box_screenshot_title"), name,
                                               f"{ext.upper()} (*.{ext})")
        if not path:
            return
        try:
            with open(path, "wb") as fh:
                fh.write(self._data)
        except OSError as e:
            log.warning("box screenshot not saved: %s", e)
            self.status.setText(tr("box_failed", why=str(e)))
            return
        self.status.setText(tr("box_grab_saved", path=path))


# The remote's buttons: (key, label, tooltip key or "").
_PAD = [
    [("power", "⏻", "box_key_power"), ("tv", "TV", ""), ("radio", "RADIO", "")],
    [("1", "1", ""), ("2", "2", ""), ("3", "3", "")],
    [("4", "4", ""), ("5", "5", ""), ("6", "6", "")],
    [("7", "7", ""), ("8", "8", ""), ("9", "9", "")],
    [("text", "TEXT", "box_key_text"), ("0", "0", ""), ("audio", "AUDIO", "")],
    [("menu", "MENU", ""), ("up", "▲", ""), ("info", "INFO", "")],
    [("left", "◀", ""), ("ok", "OK", ""), ("right", "▶", "")],
    [("exit", "EXIT", "box_key_exit"), ("down", "▼", ""), ("epg", "EPG", "box_key_epg")],
    [("vol_up", "VOL +", ""), ("mute", "🔇", "box_key_mute"), ("ch_up", "CH +", "")],
    [("vol_down", "VOL −", ""), ("", "", ""), ("ch_down", "CH −", "")],
]
# The colour keys in the theme's named colours (ACCENTS): the box's own
# red, green, yellow and blue whatever theme or accent is chosen.
_COLOURS = (("red", "red"), ("green", "green"), ("yellow", "gold"), ("blue", "blue"))
# Keys typed in the window, as the box's.
_TYPED = {Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down", Qt.Key.Key_Left: "left",
          Qt.Key.Key_Right: "right", Qt.Key.Key_Return: "ok", Qt.Key.Key_Enter: "ok",
          Qt.Key.Key_Backspace: "exit", Qt.Key.Key_PageUp: "ch_up",
          Qt.Key.Key_PageDown: "ch_down", Qt.Key.Key_Plus: "vol_up", Qt.Key.Key_Minus: "vol_down"}


class BoxRemoteDialog(_BoxWindow):
    """The box's remote: numbers, arrows, OK, exit, menu, info, guide, the
    colour keys, volume, channel up and down, power. Presses go to the box
    one after another, in the order made."""

    def __init__(self, parent: Any, client: Any, pool: Any, name: str = "") -> None:
        super().__init__(parent, client, pool, tr("box_remote_title", name=name or "Enigma2"))
        self._queue: list[str] = []
        self._sending = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(8)
        grid = QGridLayout()
        grid.setSpacing(6)
        self.buttons: dict[str, QPushButton] = {}
        for r, row in enumerate(_PAD):
            for c, (key, label, tip) in enumerate(row):
                if not key:
                    continue
                b = self._button(key, label, tip)
                if key == "power":
                    b.setStyleSheet(f"color:{P['rec']}; font-weight:700;")
                grid.addWidget(b, r, c)
        lay.addLayout(grid)
        colours = QHBoxLayout()
        colours.setSpacing(6)
        for key, accent in _COLOURS:
            b = self._button(key, "", "")
            b.setAccessibleName(key)
            b.setFixedHeight(14)
            b.setStyleSheet(f"background:{ACCENTS[accent][1]}; border-radius:7px;")
            colours.addWidget(b)
        lay.addLayout(colours)
        lay.addWidget(self.status)

    def _button(self, key: str, label: str, tip: str) -> QPushButton:
        b = QPushButton(label)
        b.setMinimumSize(56, 32)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if tip:
            b.setToolTip(tr(tip))
        b.clicked.connect(lambda _c=False, k=key: self.press(k))
        self.buttons[key] = b
        return b

    def press(self, key: str) -> None:
        self._queue.append(key)
        self._next()

    def _next(self) -> None:
        if self._sending or not self._queue or self._gone():
            return
        self._sending = True
        self._ask(self.client.remote, self._sent, self._queue.pop(0))

    def _sent(self, ok: Any) -> None:
        if self._gone():
            return
        self._sending = False
        self.status.setText("" if ok else tr("box_failed", why=""))
        self._next()

    def _failed(self, err: Any = None) -> None:
        if self._gone():
            return
        self._sending = False
        self._queue.clear()
        self.status.setText(tr("box_failed", why=why(err)))

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802 - Qt's name
        text = event.text()
        key = text if text.isdigit() and len(text) == 1 else _TYPED.get(event.key())
        if key:
            self.press(key)
            event.accept()
            return
        super().keyPressEvent(event)
