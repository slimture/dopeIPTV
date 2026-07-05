#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dopeIPTV — en elegant IPTV-klient med Xtream Codes API och EPG.
Uppspelning via mpv eller VLC. Kräver: python3, PyQt6, requests.

    pip install PyQt6 requests
    sudo apt install mpv vlc

Starta med:  python3 dopeiptv.py
"""

import base64
import html
import shutil
import subprocess
import sys
from datetime import datetime, timezone

import requests
from PyQt6.QtCore import (
    QObject, QRunnable, QSettings, QSize, Qt, QThreadPool, QTimer,
    pyqtSignal, pyqtSlot,
)
from PyQt6.QtGui import (
    QAction, QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QSplitter, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

APP_NAME = "dopeIPTV"
ORG = "dopeiptv"

# ----------------------------------------------------------------------------
#  Xtream Codes API-klient
# ----------------------------------------------------------------------------

class XtreamClient:
    def __init__(self, server: str, username: str, password: str):
        self.server = server.rstrip("/")
        if not self.server.startswith(("http://", "https://")):
            self.server = "http://" + self.server
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "dopeIPTV/1.0"

    # -- interna hjälpare ----------------------------------------------------
    def _api(self, **params):
        url = f"{self.server}/player_api.php"
        base = {"username": self.username, "password": self.password}
        base.update(params)
        r = self.session.get(url, params=base, timeout=20)
        r.raise_for_status()
        return r.json()

    # -- publika anrop -------------------------------------------------------
    def authenticate(self):
        data = self._api()
        if not isinstance(data, dict) or "user_info" not in data:
            raise RuntimeError("Oväntat svar från servern.")
        if str(data["user_info"].get("auth", 0)) != "1":
            raise RuntimeError("Fel användarnamn eller lösenord.")
        return data

    def live_categories(self):
        return self._api(action="get_live_categories") or []

    def live_streams(self, category_id=None):
        p = {"action": "get_live_streams"}
        if category_id:
            p["category_id"] = category_id
        return self._api(**p) or []

    def vod_categories(self):
        return self._api(action="get_vod_categories") or []

    def vod_streams(self, category_id=None):
        p = {"action": "get_vod_streams"}
        if category_id:
            p["category_id"] = category_id
        return self._api(**p) or []

    def series_categories(self):
        return self._api(action="get_series_categories") or []

    def series_list(self, category_id=None):
        p = {"action": "get_series"}
        if category_id:
            p["category_id"] = category_id
        return self._api(**p) or []

    def series_info(self, series_id):
        return self._api(action="get_series_info", series_id=series_id) or {}

    def vod_info(self, vod_id):
        return self._api(action="get_vod_info", vod_id=vod_id) or {}

    def short_epg(self, stream_id, limit=8):
        data = self._api(action="get_short_epg", stream_id=stream_id, limit=limit)
        return (data or {}).get("epg_listings", [])

    def epg_table(self, stream_id):
        """Full tablå — reservväg när get_short_epg svarar tomt."""
        data = self._api(action="get_simple_data_table", stream_id=stream_id)
        return (data or {}).get("epg_listings", [])

    # -- ström-URL:er ---------------------------------------------------------
    def live_url(self, stream_id, fmt="ts"):
        ext = "m3u8" if fmt == "m3u8" else "ts"
        return f"{self.server}/live/{self.username}/{self.password}/{stream_id}.{ext}"

    def vod_url(self, stream_id, ext):
        ext = ext or "mp4"
        return f"{self.server}/movie/{self.username}/{self.password}/{stream_id}.{ext}"

    def episode_url(self, episode_id, ext):
        ext = ext or "mp4"
        return f"{self.server}/series/{self.username}/{self.password}/{episode_id}.{ext}"


def b64(text):
    """Xtream skickar EPG-texter som base64."""
    if not text:
        return ""
    try:
        return html.unescape(base64.b64decode(text).decode("utf-8", "replace")).strip()
    except Exception:
        return str(text)


def epg_times(entry):
    """Returnerar (start, stop) som lokala datetime eller (None, None)."""
    def parse(ts_key, str_key):
        v = entry.get(ts_key)
        if v:
            try:
                return datetime.fromtimestamp(int(v), tz=timezone.utc).astimezone()
            except Exception:
                pass
        v = entry.get(str_key)
        if v:
            for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    return datetime.strptime(v, f).astimezone()
                except Exception:
                    continue
        return None
    return parse("start_timestamp", "start"), parse("stop_timestamp", "end")

# ----------------------------------------------------------------------------
#  Trådpool-arbetare
# ----------------------------------------------------------------------------

class WorkerSignals(QObject):
    done = pyqtSignal(object)
    fail = pyqtSignal(str)
    finished = pyqtSignal()


class Worker(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        # QThreadPool får inte radera oss på pool-tråden: WorkerSignals bor i
        # huvudtråden och skulle annars förstöras därifrån mitt under
        # signalleverans (segfault). Livslängden styrs av _ACTIVE_WORKERS.
        self.setAutoDelete(False)
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = WorkerSignals()

    @pyqtSlot()
    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as e:
            self.signals.fail.emit(str(e))
        else:
            self.signals.done.emit(result)
        finally:
            self.signals.finished.emit()


_ACTIVE_WORKERS = set()


def run_async(pool, fn, on_done, on_fail=None, *args, **kwargs):
    w = Worker(fn, *args, **kwargs)
    w.signals.done.connect(on_done)
    if on_fail:
        w.signals.fail.connect(on_fail)
    # Håll en referens tills alla köade signaler levererats i huvudtråden,
    # så att arbetaren (och dess signals-objekt) frigörs där och inte i poolen.
    _ACTIVE_WORKERS.add(w)
    w.signals.finished.connect(lambda: _ACTIVE_WORKERS.discard(w))
    pool.start(w)
    return w

# ----------------------------------------------------------------------------
#  Logotyp-cache (asynkron nedladdning)
# ----------------------------------------------------------------------------

class LogoLoader(QObject):
    def __init__(self, pool):
        super().__init__()
        self.pool = pool
        self.cache = {}
        self.waiting = {}          # url -> [callbacks]

    def get(self, url, callback):
        if not url:
            return
        if url in self.cache:
            callback(self.cache[url])
            return
        if url in self.waiting:
            self.waiting[url].append(callback)
            return
        self.waiting[url] = [callback]

        def fetch(u=url):
            r = requests.get(u, timeout=10)
            r.raise_for_status()
            return u, r.content

        def done(result):
            u, data = result
            callbacks = self.waiting.pop(u, [])
            pm = QPixmap()
            if pm.loadFromData(data):
                pm = pm.scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
                self.cache[u] = pm
                for cb in callbacks:
                    try:
                        cb(pm)
                    except RuntimeError:
                        pass   # widgeten hann tas bort (listan rensades)

        run_async(self.pool, fetch, done, lambda _: self.waiting.pop(url, None))

# ----------------------------------------------------------------------------
#  Extern uppspelning: mpv / VLC
# ----------------------------------------------------------------------------

def launch_player(player, url, title, parent=None):
    title = title or "dopeIPTV"
    if player == "mpv":
        exe = shutil.which("mpv")
        cmd = [exe, "--force-media-title=" + title,
               "--user-agent=dopeIPTV/1.0", url] if exe else None
        namn = "mpv"
    else:
        exe = shutil.which("vlc") or shutil.which("cvlc")
        cmd = [exe, "--meta-title", title, "--http-user-agent=dopeIPTV/1.0",
               url] if exe else None
        namn = "VLC"
    if not cmd:
        QMessageBox.warning(parent, "Spelare saknas",
                            f"{namn} hittades inte. Installera med t.ex.\n\n"
                            f"  sudo apt install {namn.lower()}")
        return
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)

# ----------------------------------------------------------------------------
#  Stil — mörkt, macOS-inspirerat (dopeIPTV-känsla)
# ----------------------------------------------------------------------------

ACCENT = "#4C8DFF"

STYLE = f"""
* {{
    font-family: "SF Pro Text", "Inter", "Cantarell", "Noto Sans", sans-serif;
    color: #ECECF1;
}}
QMainWindow, QDialog {{ background: #17171C; }}

/* Sidopanel */
#Sidebar {{
    background: #101014;
    border-right: 1px solid #232329;
}}
#AppTitle {{ font-size: 15px; font-weight: 700; letter-spacing: 0.5px; }}
#AppSub   {{ color: #7A7A85; font-size: 11px; }}

QToolButton#NavBtn {{
    background: transparent; border: none; border-radius: 8px;
    padding: 8px 12px; text-align: left; font-size: 13px; color: #C9C9D2;
}}
QToolButton#NavBtn:hover  {{ background: #1D1D24; }}
QToolButton#NavBtn:checked {{ background: {ACCENT}; color: white; font-weight: 600; }}

#SectionLabel {{
    color: #6E6E79; font-size: 10px; font-weight: 700;
    letter-spacing: 1.2px; padding: 10px 14px 4px 14px;
}}

QListWidget {{
    background: transparent; border: none; outline: none; font-size: 13px;
}}
QListWidget::item {{ border-radius: 8px; padding: 7px 10px; margin: 1px 6px; color: #C9C9D2; }}
QListWidget::item:hover    {{ background: #1D1D24; }}
QListWidget::item:selected {{ background: #26262E; color: white; }}

/* Mittenkolumn */
#MiddlePane {{ background: #17171C; }}
QLineEdit#Search {{
    background: #222229; border: 1px solid #2C2C34; border-radius: 9px;
    padding: 8px 12px; font-size: 13px;
}}
QLineEdit#Search:focus {{ border: 1px solid {ACCENT}; }}

QListWidget#Channels::item {{ padding: 0px; margin: 2px 8px; }}
QListWidget#Channels::item:selected {{ background: #26262E; }}

#ChName  {{ font-size: 13px; font-weight: 600; }}
#ChEpg   {{ font-size: 11px; color: #8B8B96; }}
#ChNum   {{ font-size: 11px; color: #5A5A64; }}

QProgressBar#EpgBar {{
    background: #2A2A32; border: none; border-radius: 2px; max-height: 4px;
}}
QProgressBar#EpgBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}

/* Detaljpanel */
#DetailPane {{ background: #1B1B21; border-left: 1px solid #232329; }}
#DetailTitle {{ font-size: 20px; font-weight: 700; }}
#DetailMeta  {{ color: #8B8B96; font-size: 12px; }}
#NowTitle    {{ font-size: 14px; font-weight: 600; }}
#NowTime     {{ color: {ACCENT}; font-size: 11px; font-weight: 600; }}
#NowDesc     {{ color: #A7A7B1; font-size: 12px; }}

QFrame#Card {{
    background: #222229; border: 1px solid #2C2C34; border-radius: 12px;
}}
QLabel#EpgRowTime  {{ color: {ACCENT}; font-size: 11px; font-weight: 600; }}
QLabel#EpgRowTitle {{ font-size: 12px; }}

QPushButton {{
    background: #2A2A32; border: 1px solid #34343E; border-radius: 9px;
    padding: 9px 16px; font-size: 13px; font-weight: 600;
}}
QPushButton:hover  {{ background: #34343E; }}
QPushButton#Primary {{ background: {ACCENT}; border: none; color: white; }}
QPushButton#Primary:hover {{ background: #5E99FF; }}

QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #33333C; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #45454F; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}

QComboBox {{
    background: #222229; border: 1px solid #2C2C34; border-radius: 8px;
    padding: 6px 10px;
}}
QComboBox QAbstractItemView {{ background: #222229; selection-background-color: {ACCENT}; }}
QLineEdit {{
    background: #222229; border: 1px solid #2C2C34; border-radius: 8px;
    padding: 8px 10px;
}}
QLineEdit:focus {{ border: 1px solid {ACCENT}; }}
"""

# ----------------------------------------------------------------------------
#  Inloggningsdialog
# ----------------------------------------------------------------------------

class LoginDialog(QDialog):
    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Anslut till Xtream-server")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(14)

        rubrik = QLabel("dopeIPTV")
        rubrik.setStyleSheet("font-size:20px; font-weight:700;")
        under = QLabel("Logga in med dina Xtream Codes-uppgifter.")
        under.setStyleSheet("color:#8B8B96;")
        lay.addWidget(rubrik)
        lay.addWidget(under)

        form = QFormLayout()
        form.setSpacing(10)
        self.server = QLineEdit(settings.value("server", ""))
        self.server.setPlaceholderText("http://server:port")
        self.user = QLineEdit(settings.value("username", ""))
        self.user.setPlaceholderText("användarnamn")
        self.pw = QLineEdit(settings.value("password", ""))
        self.pw.setPlaceholderText("lösenord")
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Server", self.server)
        form.addRow("Användare", self.user)
        form.addRow("Lösenord", self.pw)
        lay.addLayout(form)

        self.status = QLabel("")
        self.status.setStyleSheet("color:#FF6B6B; font-size:12px;")
        lay.addWidget(self.status)

        knappar = QDialogButtonBox()
        self.ok = knappar.addButton("Anslut", QDialogButtonBox.ButtonRole.AcceptRole)
        self.ok.setObjectName("Primary")
        knappar.addButton("Avbryt", QDialogButtonBox.ButtonRole.RejectRole)
        knappar.accepted.connect(self.accept)
        knappar.rejected.connect(self.reject)
        lay.addWidget(knappar)

    def values(self):
        return self.server.text().strip(), self.user.text().strip(), self.pw.text().strip()

# ----------------------------------------------------------------------------
#  Kanalrad (widget i listan)
# ----------------------------------------------------------------------------

class ChannelRow(QWidget):
    def __init__(self, item: dict, kind: str):
        super().__init__()
        self.data = item
        self.kind = kind
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 12, 8)
        lay.setSpacing(12)

        self.logo = QLabel()
        self.logo.setFixedSize(44, 44)
        self.logo.setStyleSheet(
            "background:#26262E; border-radius:10px; font-size:16px; font-weight:700;")
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        namn = item.get("name") or "?"
        self.logo.setText(namn.strip()[:1].upper())
        lay.addWidget(self.logo)

        col = QVBoxLayout()
        col.setSpacing(3)
        self.name = QLabel(namn)
        self.name.setObjectName("ChName")
        col.addWidget(self.name)

        self.epg = QLabel(" ")
        self.epg.setObjectName("ChEpg")
        col.addWidget(self.epg)

        self.bar = QProgressBar()
        self.bar.setObjectName("EpgBar")
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.hide()
        col.addWidget(self.bar)
        lay.addLayout(col, 1)

        if kind == "live" and item.get("num"):
            n = QLabel(str(item["num"]))
            n.setObjectName("ChNum")
            lay.addWidget(n)

    def set_logo(self, pm: QPixmap):
        rounded = QPixmap(44, 44)
        rounded.fill(Qt.GlobalColor.transparent)
        p = QPainter(rounded)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, 44, 44, 10, 10)
        p.setClipPath(path)
        scaled = pm.scaled(44, 44, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        x = (44 - scaled.width()) // 2
        y = (44 - scaled.height()) // 2
        p.drawPixmap(x, y, scaled)
        p.end()
        self.logo.setText("")
        self.logo.setPixmap(rounded)

    def set_now(self, title, pct):
        self.epg.setText(title)
        if pct is not None:
            self.bar.setValue(max(0, min(100, int(pct))))
            self.bar.show()

# ----------------------------------------------------------------------------
#  Huvudfönster
# ----------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, client: XtreamClient, settings: QSettings):
        super().__init__()
        self.client = client
        self.settings = settings
        self.pool = QThreadPool.globalInstance()
        self.logos = LogoLoader(self.pool)
        self.mode = "live"                 # live | vod | series
        self.all_items = []                # aktuell (ofiltrerad) lista
        self.series_ctx = None             # vald serie vid avsnittsläge
        self._info_cache = {}              # (kind, id) -> info-dict

        self.setWindowTitle(APP_NAME)
        self.resize(1240, 780)
        self._build_ui()
        self._load_categories()

    # -- UI-bygge -------------------------------------------------------------
    def _build_ui(self):
        root = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(root)

        # ---------- Sidopanel ----------
        side = QWidget(objectName="Sidebar")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 16, 12, 12)
        sl.setSpacing(4)

        titel = QLabel("dopeIPTV", objectName="AppTitle")
        sub = QLabel("för Linux", objectName="AppSub")
        sl.addWidget(titel)
        sl.addWidget(sub)
        sl.addSpacing(14)

        self.nav_btns = {}
        for key, text in (("live", "📺  Live-TV"),
                          ("vod", "🎬  Filmer"),
                          ("series", "🍿  Serier")):
            b = QToolButton(objectName="NavBtn")
            b.setText(text)
            b.setCheckable(True)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda _, k=key: self.switch_mode(k))
            sl.addWidget(b)
            self.nav_btns[key] = b
        self.nav_btns["live"].setChecked(True)

        sl.addWidget(QLabel("KATEGORIER", objectName="SectionLabel"))
        self.cat_list = QListWidget()
        self.cat_list.currentItemChanged.connect(self._category_changed)
        sl.addWidget(self.cat_list, 1)

        inst = QPushButton("⚙  Inställningar")
        inst.clicked.connect(self.open_settings)
        sl.addWidget(inst)

        # ---------- Mittenkolumn ----------
        mid = QWidget(objectName="MiddlePane")
        ml = QVBoxLayout(mid)
        ml.setContentsMargins(14, 14, 14, 10)
        ml.setSpacing(10)

        self.search = QLineEdit(objectName="Search")
        self.search.setPlaceholderText("Sök kanal, film eller serie …")
        self.search.textChanged.connect(self._apply_filter)
        ml.addWidget(self.search)

        self.back_btn = QPushButton("←  Tillbaka till serier")
        self.back_btn.hide()
        self.back_btn.clicked.connect(self._leave_series)
        ml.addWidget(self.back_btn)

        self.listw = QListWidget(objectName="Channels")
        self.listw.setUniformItemSizes(False)
        self.listw.currentItemChanged.connect(self._item_selected)
        self.listw.itemDoubleClicked.connect(lambda _: self.play())
        self.listw.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.listw.customContextMenuRequested.connect(self._context_menu)
        ml.addWidget(self.listw, 1)

        self.count_lbl = QLabel("")
        self.count_lbl.setStyleSheet("color:#5A5A64; font-size:11px;")
        ml.addWidget(self.count_lbl)

        # ---------- Detaljpanel ----------
        det = QWidget(objectName="DetailPane")
        dl = QVBoxLayout(det)
        dl.setContentsMargins(20, 22, 20, 18)
        dl.setSpacing(12)

        self.d_logo = QLabel()
        self.d_logo.setFixedSize(84, 84)
        self.d_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.d_logo.setStyleSheet(
            "background:#26262E; border-radius:18px; font-size:30px; font-weight:700;")
        dl.addWidget(self.d_logo)

        self.d_title = QLabel("Välj något i listan", objectName="DetailTitle")
        self.d_title.setWordWrap(True)
        dl.addWidget(self.d_title)

        self.d_meta = QLabel("", objectName="DetailMeta")
        self.d_meta.setWordWrap(True)
        dl.addWidget(self.d_meta)

        # "Nu"-kort
        self.now_card = QFrame(objectName="Card")
        nc = QVBoxLayout(self.now_card)
        nc.setContentsMargins(14, 12, 14, 12)
        nc.setSpacing(6)
        self.now_time = QLabel("", objectName="NowTime")
        self.now_title = QLabel("", objectName="NowTitle")
        self.now_title.setWordWrap(True)
        self.now_bar = QProgressBar(objectName="EpgBar")
        self.now_bar.setTextVisible(False)
        self.now_bar.setRange(0, 100)
        self.now_desc = QLabel("", objectName="NowDesc")
        self.now_desc.setWordWrap(True)
        for w in (self.now_time, self.now_title, self.now_bar, self.now_desc):
            nc.addWidget(w)
        self.now_card.hide()
        dl.addWidget(self.now_card)

        # Kommande program
        self.epg_refresh = QPushButton("↻  Uppdatera EPG")
        self.epg_refresh.clicked.connect(self._request_epg)
        self.epg_refresh.hide()
        dl.addWidget(self.epg_refresh)

        self.epg_scroll = QScrollArea()
        self.epg_scroll.setWidgetResizable(True)
        self.epg_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.epg_holder = QWidget()
        self.epg_lay = QVBoxLayout(self.epg_holder)
        self.epg_lay.setContentsMargins(0, 0, 0, 0)
        self.epg_lay.setSpacing(8)
        self.epg_lay.addStretch()
        self.epg_scroll.setWidget(self.epg_holder)
        dl.addWidget(self.epg_scroll, 1)

        # Spelknappar
        rad = QHBoxLayout()
        rad.setSpacing(8)
        self.play_mpv = QPushButton("▶  Spela i mpv", objectName="Primary")
        self.play_mpv.clicked.connect(lambda: self.play("mpv"))
        self.play_vlc = QPushButton("▶  Spela i VLC")
        self.play_vlc.clicked.connect(lambda: self.play("vlc"))
        rad.addWidget(self.play_mpv)
        rad.addWidget(self.play_vlc)
        dl.addLayout(rad)

        root.addWidget(side)
        root.addWidget(mid)
        root.addWidget(det)
        root.setSizes([220, 560, 380])
        root.setCollapsible(0, False)
        root.setCollapsible(2, False)

        # Uppdatera "nu spelas"-klockan varje minut
        self.tick = QTimer(self)
        self.tick.timeout.connect(self._refresh_progress)
        self.tick.start(60_000)
        self._current_epg = None

    # -- lägen och kategorier --------------------------------------------------
    def switch_mode(self, mode):
        for k, b in self.nav_btns.items():
            b.setChecked(k == mode)
        self.mode = mode
        self.series_ctx = None
        self.back_btn.hide()
        self.search.clear()
        self._load_categories()

    def _load_categories(self):
        self.cat_list.clear()
        self.listw.clear()
        self.count_lbl.setText("Hämtar kategorier …")
        fn = {"live": self.client.live_categories,
              "vod": self.client.vod_categories,
              "series": self.client.series_categories}[self.mode]

        def done(cats):
            self.cat_list.blockSignals(True)
            alla = QListWidgetItem("Alla")
            alla.setData(Qt.ItemDataRole.UserRole, None)
            self.cat_list.addItem(alla)
            for c in cats:
                it = QListWidgetItem(c.get("category_name", "?"))
                it.setData(Qt.ItemDataRole.UserRole, c.get("category_id"))
                self.cat_list.addItem(it)
            self.cat_list.blockSignals(False)
            self.cat_list.setCurrentRow(0)

        run_async(self.pool, fn, done, self._error)

    def _category_changed(self, cur, _prev=None):
        if not cur:
            return
        cat = cur.data(Qt.ItemDataRole.UserRole)
        self.series_ctx = None
        self.back_btn.hide()
        self._load_items(cat)

    def _load_items(self, category_id):
        self.listw.clear()
        self.count_lbl.setText("Hämtar innehåll …")
        fn = {"live": self.client.live_streams,
              "vod": self.client.vod_streams,
              "series": self.client.series_list}[self.mode]

        def done(items):
            self.all_items = items or []
            self._apply_filter()

        run_async(self.pool, lambda: fn(category_id), done, self._error)

    # -- lista och filter --------------------------------------------------------
    def _apply_filter(self):
        text = self.search.text().lower().strip()
        self.listw.clear()
        kind = "episode" if self.series_ctx else self.mode
        visade = 0
        for it in self.all_items:
            namn = (it.get("name") or it.get("title") or "").lower()
            if text and text not in namn:
                continue
            row = ChannelRow(self._normalize(it), kind)
            lw = QListWidgetItem()
            lw.setSizeHint(QSize(0, 66))
            lw.setData(Qt.ItemDataRole.UserRole, it)
            self.listw.addItem(lw)
            self.listw.setItemWidget(lw, row)
            url = it.get("stream_icon") or it.get("cover")
            if url:
                self.logos.get(url, row.set_logo)
            visade += 1
            if visade >= 800 and not text:   # skydda UI:t mot enorma listor
                break
        etikett = {"live": "kanaler", "vod": "filmer",
                   "series": "serier", "episode": "avsnitt"}[kind]
        self.count_lbl.setText(f"{visade} {etikett}")

    @staticmethod
    def _normalize(it):
        return {"name": it.get("name") or it.get("title") or "?",
                "num": it.get("num")}

    # -- val, EPG och detaljpanel -------------------------------------------------
    def _item_selected(self, cur, _prev=None):
        self.now_card.hide()
        self._clear_epg_rows()
        self._current_epg = None
        self.epg_refresh.hide()
        if not cur:
            return
        it = cur.data(Qt.ItemDataRole.UserRole)
        namn = it.get("name") or it.get("title") or "?"
        self.d_title.setText(namn)
        self.d_logo.setPixmap(QPixmap())
        self.d_logo.setText(namn.strip()[:1].upper())
        url = it.get("stream_icon") or it.get("cover")
        if url:
            self.logos.get(url, self._set_detail_logo)

        if self.series_ctx:
            info = it.get("info") if isinstance(it.get("info"), dict) else {}
            meta = " · ".join(x for x in (
                f"Säsong {it.get('season')}" if it.get("season") else "",
                info.get("duration", ""),) if x)
            self.d_meta.setText(meta)
            self._show_media_info(info, cur)
        elif self.mode == "live":
            self.d_meta.setText("Direktsänd kanal")
            if it.get("stream_id"):
                self.epg_refresh.show()
                self._request_epg()
        elif self.mode == "vod":
            meta = " · ".join(x for x in (
                str(it.get("year") or ""),
                f"⭐ {it['rating']}" if it.get("rating") else "",) if x)
            self.d_meta.setText(meta or "Film")
            if it.get("stream_id"):
                self._request_media_info("vod", it["stream_id"], cur)
        else:
            self.d_meta.setText("Serie — dubbelklicka för avsnitt")
            if it.get("series_id"):
                self._request_media_info("series", it["series_id"], cur)

    def _set_detail_logo(self, pm):
        rounded = QPixmap(84, 84)
        rounded.fill(Qt.GlobalColor.transparent)
        p = QPainter(rounded)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, 84, 84, 18, 18)
        p.setClipPath(path)
        s = pm.scaled(84, 84, Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap((84 - s.width()) // 2, (84 - s.height()) // 2, s)
        p.end()
        self.d_logo.setText("")
        self.d_logo.setPixmap(rounded)

    def _clear_epg_rows(self):
        while self.epg_lay.count() > 1:
            w = self.epg_lay.takeAt(0).widget()
            if w:
                w.deleteLater()

    def _epg_note(self, text):
        """Liten grå informationsrad i EPG-/informationsytan."""
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#6E6E79; font-size:12px;")
        lbl.setWordWrap(True)
        self.epg_lay.insertWidget(self.epg_lay.count() - 1, lbl)

    def _request_epg(self):
        """Hämtar (om) EPG för den valda live-kanalen."""
        cur = self.listw.currentItem()
        if not cur or self.mode != "live" or self.series_ctx:
            return
        sid = cur.data(Qt.ItemDataRole.UserRole).get("stream_id")
        if not sid:
            return
        self._clear_epg_rows()
        self._epg_note("Hämtar programguide …")

        def fetch():
            listings = self.client.short_epg(sid, 8)
            if not listings:                     # prova reservvägen
                listings = self.client.epg_table(sid)
            return listings

        run_async(self.pool, fetch,
                  lambda e: self._show_epg(e, cur),
                  lambda _: self._epg_error(cur))

    def _epg_error(self, list_item):
        if list_item is not self.listw.currentItem():
            return
        self.now_card.hide()
        self._clear_epg_rows()
        self._epg_note("Kunde inte hämta programguiden.")

    # -- film-/serieinformation -------------------------------------------------
    def _request_media_info(self, kind, mid, list_item):
        cached = self._info_cache.get((kind, mid))
        if cached is not None:
            self._show_media_info(cached, list_item)
            return
        self._epg_note("Hämtar information …")
        if kind == "vod":
            fetch = lambda: (self.client.vod_info(mid) or {}).get("info") or {}
        else:
            fetch = lambda: (self.client.series_info(mid) or {}).get("info") or {}

        def done(info):
            if not isinstance(info, dict):
                info = {}
            self._info_cache[(kind, mid)] = info
            self._show_media_info(info, list_item)

        run_async(self.pool, fetch, done, lambda _: None)

    def _show_media_info(self, info, list_item):
        if list_item is not self.listw.currentItem():
            return
        self._clear_epg_rows()
        plot = str(info.get("plot") or info.get("description") or "").strip()
        if plot:
            kort = QFrame(objectName="Card")
            kl = QVBoxLayout(kort)
            kl.setContentsMargins(14, 12, 14, 12)
            handling = QLabel(plot, objectName="NowDesc")
            handling.setWordWrap(True)
            kl.addWidget(handling)
            self.epg_lay.insertWidget(self.epg_lay.count() - 1, kort)
        rader = (("Genre", info.get("genre")),
                 ("Skådespelare", info.get("cast") or info.get("actors")),
                 ("Regi", info.get("director")),
                 ("Premiär", info.get("releasedate") or info.get("releaseDate")),
                 ("Längd", info.get("duration")),
                 ("Betyg", info.get("rating")))
        nagot = bool(plot)
        for rubrik, varde in rader:
            varde = str(varde or "").strip()
            if varde:
                self._epg_note(f"{rubrik}: {varde}")
                nagot = True
        if not nagot:
            self._epg_note("Ingen ytterligare information tillgänglig.")

    def _show_epg(self, listings, list_item):
        if list_item is not self.listw.currentItem():
            return                      # användaren hann byta kanal
        self.now_card.hide()
        self._clear_epg_rows()
        self._current_epg = None
        now = datetime.now().astimezone()
        alla, kommande = [], []
        aktuellt = None
        for e in listings or []:
            start, stop = epg_times(e)
            post = {"title": b64(e.get("title")), "desc": b64(e.get("description")),
                    "start": start, "stop": stop}
            alla.append(post)
            if start and stop and start <= now < stop and not aktuellt:
                aktuellt = post
            elif start and start > now:
                kommande.append(post)
        kommande.sort(key=lambda p: p["start"])

        if aktuellt:
            self._current_epg = aktuellt
            self.now_time.setText(
                f"NU · {aktuellt['start']:%H:%M}–{aktuellt['stop']:%H:%M}")
            self.now_title.setText(aktuellt["title"] or "Okänt program")
            self.now_desc.setText(aktuellt["desc"][:400])
            self._refresh_progress()
            self.now_card.show()
            # uppdatera även raden i listan
            w = self.listw.itemWidget(list_item)
            if isinstance(w, ChannelRow):
                total = (aktuellt["stop"] - aktuellt["start"]).total_seconds()
                pct = (now - aktuellt["start"]).total_seconds() / total * 100 if total else 0
                w.set_now("Nu: " + (aktuellt["title"] or ""), pct)

        for post in kommande[:6]:
            self._epg_card(post)

        if not aktuellt and not kommande:
            daterade = sorted((p for p in alla if p["start"]),
                              key=lambda p: p["start"])
            if daterade:
                # data finns men allt ligger i det förflutna — troligen skickar
                # servern tider i fel tidszon; visa ändå de senaste posterna
                self._epg_note("Serverns tablåtider verkar felaktiga — "
                               "visar de senaste posterna.")
                for post in daterade[-6:]:
                    self._epg_card(post, med_datum=True)
            else:
                self._epg_note("Ingen programguide tillgänglig "
                               "för den här kanalen.")

    def _epg_card(self, post, med_datum=False):
        kort = QFrame(objectName="Card")
        kl = QVBoxLayout(kort)
        kl.setContentsMargins(12, 9, 12, 9)
        kl.setSpacing(2)
        fmt = "%-d/%-m %H:%M" if med_datum else "%H:%M"
        t = QLabel(post["start"].strftime(fmt), objectName="EpgRowTime")
        ti = QLabel(post["title"] or "Okänt", objectName="EpgRowTitle")
        ti.setWordWrap(True)
        kl.addWidget(t)
        kl.addWidget(ti)
        self.epg_lay.insertWidget(self.epg_lay.count() - 1, kort)

    def _refresh_progress(self):
        e = self._current_epg
        if not e:
            return
        now = datetime.now().astimezone()
        if now >= e["stop"]:            # programmet är slut — hämta ny EPG
            self._current_epg = None
            self._request_epg()
            return
        total = (e["stop"] - e["start"]).total_seconds()
        if total > 0:
            pct = (now - e["start"]).total_seconds() / total * 100
            self.now_bar.setValue(max(0, min(100, int(pct))))

    # -- serier → avsnitt ---------------------------------------------------------
    def _enter_series(self, serie):
        sid = serie.get("series_id")
        if not sid:
            return
        self.count_lbl.setText("Hämtar avsnitt …")

        def done(info):
            episodes = []
            for season, eps in (info.get("episodes") or {}).items():
                for ep in eps:
                    ep["season"] = season
                    ep["name"] = f"S{season} · E{ep.get('episode_num', '?')} — " \
                                 f"{ep.get('title') or 'Avsnitt'}"
                    episodes.append(ep)
            self.series_ctx = serie
            self.all_items = episodes
            self.back_btn.show()
            self.search.clear()
            self._apply_filter()

        run_async(self.pool, lambda: self.client.series_info(sid), done, self._error)

    def _leave_series(self):
        self.series_ctx = None
        self.back_btn.hide()
        cur = self.cat_list.currentItem()
        self._load_items(cur.data(Qt.ItemDataRole.UserRole) if cur else None)

    # -- uppspelning ----------------------------------------------------------------
    def _stream_for(self, it):
        titel = it.get("name") or it.get("title") or "dopeIPTV"
        if self.series_ctx:
            return self.client.episode_url(
                it.get("id"), it.get("container_extension")), titel
        if self.mode == "live":
            fmt = self.settings.value("stream_format", "ts")
            return self.client.live_url(it.get("stream_id"), fmt), titel
        if self.mode == "vod":
            return self.client.vod_url(
                it.get("stream_id"), it.get("container_extension")), titel
        return None, titel

    def play(self, player=None):
        cur = self.listw.currentItem()
        if not cur:
            return
        it = cur.data(Qt.ItemDataRole.UserRole)
        if self.mode == "series" and not self.series_ctx:
            self._enter_series(it)
            return
        url, titel = self._stream_for(it)
        if not url:
            return
        player = player or self.settings.value("player", "mpv")
        launch_player(player, url, titel, self)

    def _context_menu(self, pos):
        cur = self.listw.itemAt(pos)
        if not cur:
            return
        self.listw.setCurrentItem(cur)
        m = QMenu(self)
        m.addAction("Spela i mpv", lambda: self.play("mpv"))
        m.addAction("Spela i VLC", lambda: self.play("vlc"))
        it = cur.data(Qt.ItemDataRole.UserRole)
        if not (self.mode == "series" and not self.series_ctx):
            url, _ = self._stream_for(it)
            if url:
                m.addSeparator()
                m.addAction("Kopiera ström-URL",
                            lambda: QApplication.clipboard().setText(url))
        m.exec(self.listw.mapToGlobal(pos))

    # -- inställningar och fel -------------------------------------------------------
    def open_settings(self):
        d = QDialog(self)
        d.setWindowTitle("Inställningar")
        d.setMinimumWidth(380)
        lay = QVBoxLayout(d)
        lay.setContentsMargins(22, 22, 22, 22)
        form = QFormLayout()
        spelare = QComboBox()
        spelare.addItems(["mpv", "vlc"])
        spelare.setCurrentText(self.settings.value("player", "mpv"))
        fmt = QComboBox()
        fmt.addItems(["ts", "m3u8"])
        fmt.setCurrentText(self.settings.value("stream_format", "ts"))
        form.addRow("Standardspelare", spelare)
        form.addRow("Live-format", fmt)
        lay.addLayout(form)

        byt = QPushButton("Byt konto / server …")
        lay.addWidget(byt)

        knappar = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        knappar.accepted.connect(d.accept)
        knappar.rejected.connect(d.reject)
        lay.addWidget(knappar)

        def logga_ut():
            self.settings.remove("password")
            d.reject()
            QMessageBox.information(self, APP_NAME,
                                    "Starta om appen för att logga in på nytt.")
        byt.clicked.connect(logga_ut)

        if d.exec():
            self.settings.setValue("player", spelare.currentText())
            self.settings.setValue("stream_format", fmt.currentText())

    def _error(self, msg):
        self.count_lbl.setText("Fel: " + msg)

# ----------------------------------------------------------------------------
#  Programikon
# ----------------------------------------------------------------------------

def make_app_icon():
    """Ritar appikonen (blå rundad platta med play-symbol) i olika storlekar."""
    icon = QIcon()
    for s in (256, 128, 64, 48, 32):
        pm = QPixmap(s, s)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        platta = QPainterPath()
        platta.addRoundedRect(0, 0, s, s, s * 0.22, s * 0.22)
        p.fillPath(platta, QColor(ACCENT))
        tri = QPainterPath()
        tri.moveTo(s * 0.40, s * 0.28)
        tri.lineTo(s * 0.40, s * 0.72)
        tri.lineTo(s * 0.76, s * 0.50)
        tri.closeSubpath()
        p.fillPath(tri, QColor("white"))
        p.end()
        icon.addPixmap(pm)
    return icon


def install_icon(icon):
    """Sparar ikonen som 'dopeiptv' i användarens ikontema så att
    skrivbordsfilen (Icon=dopeiptv) hittar den i programmenyn."""
    import os
    from pathlib import Path
    base = Path(os.environ.get("XDG_DATA_HOME",
                               Path.home() / ".local" / "share"))
    mal = base / "icons" / "hicolor" / "256x256" / "apps" / "dopeiptv.png"
    if mal.exists():
        return
    try:
        mal.parent.mkdir(parents=True, exist_ok=True)
        icon.pixmap(256, 256).save(str(mal), "PNG")
    except OSError:
        pass

# ----------------------------------------------------------------------------
#  Start
# ----------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG)
    app.setApplicationDisplayName(APP_NAME)
    # Wayland visar app_id i aktivitetsfältet; utan detta blir det "python3".
    # Namnet måste matcha skrivbordsfilen (dopeiptv.desktop).
    app.setDesktopFileName("dopeiptv")
    ikon = make_app_icon()
    app.setWindowIcon(ikon)
    install_icon(ikon)
    app.setStyleSheet(STYLE)
    settings = QSettings(ORG, ORG)

    client = None
    while client is None:
        # Hoppa över dialogen om vi redan har fungerande uppgifter
        server, user, pw = (settings.value("server", ""),
                            settings.value("username", ""),
                            settings.value("password", ""))
        forsta = not (server and user and pw)
        if forsta:
            dlg = LoginDialog(settings)
            if not dlg.exec():
                return 0
            server, user, pw = dlg.values()

        kandidat = XtreamClient(server, user, pw)
        try:
            kandidat.authenticate()
            client = kandidat
            settings.setValue("server", server)
            settings.setValue("username", user)
            settings.setValue("password", pw)
        except Exception as e:
            settings.remove("password")
            QMessageBox.critical(None, "Anslutning misslyckades", str(e))

    w = MainWindow(client, settings)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
