"""Stats for nerds over the picture (I, or the options menu).

What mpv says of the stream playing - its tracks, decoding, A/V sync,
dropped and mistimed frames, bitrate, cache and network speed - read once
a second while they show; with Nerd on also the picture's colour and scan,
the sound's output, the source, its user agent and proxy (_nerd_stats);
and the lines the window adds (a box's signal, ``stats_extra``).
``_StatsLabel`` draws them: names quiet, values in fixed-width digits, the
largest type that fits the picture in one column or two, a click for the
few lines most looked at or all of them. Stats that are on stay on across
the pop-out and fullscreen.

Mixed into EmbeddedPlayer; it reads the player's attributes.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel


_ASPECTS = ((16 / 9, "16:9"), (4 / 3, "4:3"), (21 / 9, "21:9"), (2.39, "2.39:1"),
            (1.85, "1.85:1"), (5 / 4, "5:4"), (1.0, "1:1"), (3 / 2, "3:2"))


def aspect_name(value) -> str:
    """mpv's aspect as people write it: 1.7777777777777777 is 16:9."""
    try:
        a = float(value)
    except (TypeError, ValueError):
        return str(value)
    for ratio, name in _ASPECTS:
        if abs(a - ratio) < 0.01:
            return name
    return f"{a:.2f}:1"


def on_off(value) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    text = str(value).strip().lower()
    return {"yes": "on", "true": "on", "no": "off", "false": "off"}.get(text, str(value))


class _StatsLabel(QLabel):
    """Stats for nerds over the picture: names quiet, values bright and in
    fixed-width digits (they change every second without the text
    jumping). A click switches between all of it and the few lines most
    looked at."""

    toggled = pyqtSignal()
    #: The lines of the compact view.
    COMPACT = ("Video", "Audio", "HW dec", "Dropped", "Bitrate", "Cache", "Net")
    #: Lines in both views when there are any: a box's signal.
    ALWAYS = ("SNR",)

    def __init__(self, parent) -> None:
        super().__init__("", parent)
        self.compact = False
        self.columns = 1
        self.font_px = 11
        # What the last fit was made for (room, view, lines) and whether
        # anything fitted at all; the type size styled now.
        self._fit_for: tuple | None = None
        self._fitted = False
        self._styled_px: int | None = None
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click: fewer or more lines")

    def mousePressEvent(self, event) -> None:      # noqa: N802 - Qt hook
        self.compact = not self.compact
        self.toggled.emit()
        event.accept()

    def _html(self, groups, columns: int) -> str:
        from html import escape

        def table(rows) -> str:
            return ("<table cellspacing='0' cellpadding='0'>" + "".join(
                f"<tr><td style='color:#9A9AA8; padding:1px 12px 1px 0'>{escape(k)}</td>"
                f"<td style='color:#F2F2F6; font-family:monospace; padding:1px 0'>{escape(str(v))}</td></tr>"
                for k, v in rows) + "</table>")
        shown = [[r for r in g if not self.compact or r[0] in self.COMPACT or r[0] in self.ALWAYS]
                 for g in groups]
        shown = [g for g in shown if g]
        if columns > 1 and len(shown) > 1:
            cells = "<td width='22'></td>".join(f"<td valign='top'>{table(g)}</td>" for g in shown)
            return f"<table cellspacing='0' cellpadding='0'><tr>{cells}</tr></table>"
        return "<div style='margin:0'></div>".join(table(g) + ("<div style='font-size:6px'>&nbsp;</div>"
                                                                if i < len(shown) - 1 else "")
                                                     for i, g in enumerate(shown))

    def _style(self, px: int) -> None:
        # A style sheet re-polishes the label: only when the size changes.
        if px == self._styled_px:
            return
        self._styled_px = px
        self.setStyleSheet(
            "background: rgba(0,0,0,185); border-radius: 8px;"
            f"padding: {max(6, px // 2 + 2)}px {max(8, px - 1)}px; font-size: {px}px;")

    def lay_out(self, groups, max_w: int, max_h: int) -> None:
        """The largest type, one column or two, that fits *max_w* x *max_h*.

        Searched again only when the room, the view or the lines change:
        each second's new values keep the last fit while it still fits
        (or when nothing did) - the search is up to 14 style sheets and
        layouts, every second, when the picture is small."""
        shape = (max_w, max_h, self.compact, tuple(tuple(k for k, _v in g) for g in groups))
        if shape == self._fit_for:
            self._style(self.font_px)
            self.setText(self._html(groups, self.columns))
            self.adjustSize()
            if not self._fitted or (self.height() <= max_h and self.width() <= max_w):
                return
        self._fit_for = shape
        top = max(10, min(15, round(max_h / 62)))
        tries = [(px, cols) for px in range(top, 8, -1) for cols in (1, 2)]
        for px, cols in tries:
            self._style(px)
            self.setText(self._html(groups, cols))
            self.adjustSize()
            if self.height() <= max_h and self.width() <= max_w:
                self.font_px, self.columns, self._fitted = px, cols, True
                return
        self.font_px, self.columns, self._fitted = 9, 2, False


class StatsOverlayMixin:
    def _show_stats(self) -> None:
        if self._stats_overlay.isVisible():
            self._stats_overlay.hide()
            self._stats_timer.stop()
            self.stats_shown.emit(False)
            return
        self._update_stats_text()
        self._stats_overlay.show()
        self._stats_overlay.raise_()
        self._place_stats()
        self._stats_timer.start()
        if self._stats_timer.isActive():
            self.stats_shown.emit(True)

    def _keep_stats_up(self) -> None:
        """Stats that are on (their timer runs) stay on: shown over the
        surface that has the picture now, above what came into its window.

        The pop-out - and fullscreen on macOS, which is the pop-out filling
        the screen - moves the overlays into another window, and a widget
        given a new parent is hidden. Nothing showed the stats again, so
        they went the moment fullscreen began, their timer ticking on."""
        ov = self._stats_overlay
        if not self._stats_timer.isActive() or self.video.mpv is None:
            return
        if ov.isHidden():
            ov.show()
        ov.raise_()
        self._place_stats()

    def _update_stats_text(self) -> None:
        m = self.video.mpv
        if m is None:
            self._stats_overlay.hide()
            if self._stats_timer.isActive():
                self._stats_timer.stop()
                self.stats_shown.emit(False)
            return

        def prop(name, fmt=str):
            # Read via attribute access (m.some_property) - the same form the
            # rest of the player uses successfully. Some libmpv builds don't
            # resolve the subscript getter for these hyphenated names, which
            # made every stat below Video/Audio read as "—".
            try:
                v = getattr(m, name.replace("-", "_"))
                return fmt(v) if v is not None else "—"
            except Exception:
                return "—"

        def track_info(kind):
            try:
                for t in (m.track_list or []):
                    if t.get("type") == kind and t.get("selected"):
                        codec = t.get("codec") or "?"
                        parts = [codec]
                        if kind == "video":
                            w = t.get("demux-w") or t.get("width")
                            h = t.get("demux-h") or t.get("height")
                            if w and h:
                                parts.append(f"{w}×{h}")
                            fps = t.get("demux-fps")
                            if fps:
                                parts.append(f"{fps:.1f} fps")
                        elif kind == "audio":
                            sr = t.get("demux-samplerate")
                            ch = t.get("demux-channel-count") or \
                                t.get("audio-channels")
                            lang = t.get("lang")
                            if sr:
                                parts.append(f"{sr} Hz")
                            if ch:
                                parts.append(f"{ch}ch")
                            if lang:
                                parts.append(lang)
                        return " / ".join(parts)
            except Exception:
                pass
            return "—"

        hwdec = prop("hwdec-current")
        if not hwdec or hwdec == "—":
            hwdec = prop("hwdec")
        if not hwdec or hwdec in ("—", "no"):
            hwdec = "software"
        dropped = prop("frame-drop-count", lambda v: str(int(v)))
        vo_drops = prop("vo-drop-frame-count", lambda v: str(int(v)))
        if dropped != "—" and vo_drops != "—":
            dropped = f"{dropped} / {vo_drops} vo"
        # Display-sync timing faults: with video-sync=display-resample a
        # periodic present-timing correction shows up here (not as a "drop"),
        # so these two pinpoint a render/vsync-side hitch vs a decode-side one.
        mistimed = prop("mistimed-frame-count", lambda v: str(int(v)))
        delayed = prop("vo-delayed-frame-count", lambda v: str(int(v)))

        stream = [
            ("Video", track_info('video')),
            ("Audio", track_info('audio')),
            ("HW dec", hwdec),
            ("A/V sync", prop('avsync', lambda v: f'{v:.3f} s')),
            ("Dropped", dropped),
            ("Timing", f"{mistimed} mistimed / {delayed} delayed"),
            ("FPS", prop('estimated-vf-fps', lambda v: f'{v:.1f}')),
            ("Bitrate", prop('video-bitrate', lambda v: f'{v / 1000:.0f} kbps' if v else '—')),
            ("Cache", prop('demuxer-cache-duration', lambda v: f'{v:.1f} s')),
            ("Net", prop('cache-speed', lambda v: f'{v / 1024:.0f} KB/s' if v else '0 KB/s')),
            ("Format", prop('file-format')),
            ("Protocol", prop('stream-path', lambda v: v.split('://')[0] if '://' in str(v) else str(v))),
        ]
        self._stats_groups = [stream, self._nerd_stats(prop), list(self.stats_extra)]
        self._place_stats()

    def _nerd_stats(self, prop) -> list[str]:
        """More for a nerd, with Nerd on: the picture's colour and scan,
        what the audio goes out as, the source and what the network says
        it is."""
        from ..core import nerd
        n = nerd.current()
        if not (n.on and n.flag("nerd_overlay_more", True)):
            return []

        def vp(key: str) -> str:
            try:
                return str((self.video.mpv.video_params or {}).get(key) or "—")
            except Exception:
                return "—"
        src = nerd.source() or {}
        out = [
            ("Colour", f"{vp('colormatrix')} / {vp('primaries')} / {vp('gamma')}"),
            ("Pixels", f"{vp('pixelformat')} · {vp('w')}×{vp('h')} · {aspect_name(vp('aspect'))}"),
            ("Deinterlace", prop('deinterlace', on_off)),
            ("Audio out", f"{prop('audio-codec-name')} · "
                          f"{prop('audio-out-params', lambda v: (v or {}).get('format', '—'))}"),
            ("Passthrough", n.get('nerd_spdif') or 'off'),
            ("Container", f"{prop('file-format')} · demuxer {prop('current-demuxer')}"),
            ("Source", src.get('_kind') or '—'),
            ("User agent", n.user_agent(src)),
            ("Proxy", n.proxy(src) and 'on' or 'off'),
        ]
        if n.flag("nerd_vpn_guard"):
            up = nerd.vpn_interfaces()
            out.append(("VPN", ', '.join(up) if up else 'none'))
        return out

    def _place_stats(self) -> None:
        """Lay the stats out for the room the picture has: the type grows
        with it (fullscreen reads from the sofa), a second column when one
        is taller than the picture, smaller type when even that is not
        enough. 8 px inside the surface's top-left (the docked video, or the
        pop-out mirror while popped out)."""
        g = self._ov_surface.geometry()
        groups = getattr(self, "_stats_groups", None)
        if groups:
            self._stats_overlay.lay_out(groups, g.width() - 16, g.height() - 16)
        else:
            self._stats_overlay.adjustSize()
        self._stats_overlay.move(g.x() + 8, g.y() + 8)
