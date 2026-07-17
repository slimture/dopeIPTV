"""Internationalization module for dopeIPTV.

Provides a ``tr(key, **kwargs)`` function that returns the translated string
for the currently active language, with optional format substitutions.

Usage::

    from .i18n import tr, set_language, LANGUAGES

    set_language("sv")
    print(tr("status_loading_channels"))   # "Laddar kanaler…"
    print(tr("status_playing", title="CNN"))  # "Spelar: CNN"

No external dependencies beyond the Python standard library.

--------------------------------------------------------------------------
Contributor guide (how translation works in this codebase)
--------------------------------------------------------------------------
* Every user-visible string in the UI goes through ``tr("some_key")``. So to
  find everything that is translated, grep the code for ``tr(``. Any bare
  string literal passed to a Qt setter (setText / setWindowTitle / addAction /
  setPlaceholderText / setToolTip / addTab / QLabel(...) / QPushButton(...))
  is a bug unless it is a brand/technical token (e.g. "mpv", "VLC", "PiP",
  "dopeIPTV", "http://server:port").
* To ADD A STRING: add a key to ``_STRINGS`` below with one line per language,
  then call ``tr("your_key")`` (with ``{placeholders}`` filled via keyword
  args, e.g. ``tr("status_playing", title=name)``).
* To ADD A LANGUAGE: add its code + display name to ``LANGUAGES`` and add that
  code to every entry in ``_STRINGS``. Run ``tests`` (or the snippet in
  ``tests/``) - the suite checks that every key covers every language and that
  every ``tr("…")`` used in the code has a matching key.
* Dialogs/menus that are rebuilt each time they open pick up the current
  language automatically; persistent chrome is refreshed live by
  ``MainWindow.retranslate_ui`` / ``EmbeddedPlayer.retranslate_ui``.
"""

from __future__ import annotations

_current_language: str = "en"

LANGUAGES: dict[str, str] = {
    "en": "English",
    "sv": "Svenska",
    "es": "Español",
    "de": "Deutsch",
    "fr": "Français",
    "zh": "中文",
    "ru": "Русский",
    "th": "ไทย",
}

# ---------------------------------------------------------------------------
# Every UI string keyed by a descriptive snake_case identifier.
# Each value is a dict mapping language code -> translated text.
# Keys that contain ``{…}`` placeholders use Python str.format_map().
# ---------------------------------------------------------------------------

_STRINGS: dict[str, dict[str, str]] = {

    # ── Navigation / sidebar ──────────────────────────────────────────────

    "nav_tv": {
        "en": "TV",
        "sv": "TV",
        "es": "TV",
        "de": "TV",
        "fr": "TV",
        "zh": "电视",
        "ru": "ТВ",
        "th": "ทีวี",
    },
    "nav_movies": {
        "en": "Movies",
        "sv": "Filmer",
        "es": "Películas",
        "de": "Filme",
        "fr": "Films",
        "zh": "电影",
        "ru": "Фильмы",
        "th": "ภาพยนตร์",
    },
    "nav_series": {
        "en": "Series",
        "sv": "Serier",
        "es": "Series",
        "de": "Serien",
        "fr": "Séries",
        "zh": "剧集",
        "ru": "Сериалы",
        "th": "ซีรีส์",
    },
    "nav_favorites": {
        "en": "Favorites",
        "sv": "Favoriter",
        "es": "Favoritos",
        "de": "Favoriten",
        "fr": "Favoris",
        "zh": "收藏",
        "ru": "Избранное",
        "th": "รายการโปรด",
    },
    "nav_watchlist": {
        "en": "Watch Later",
        "sv": "Titta senare",
        "es": "Ver más tarde",
        "de": "Später ansehen",
        "fr": "Regarder plus tard",
        "zh": "稍后观看",
        "ru": "Посмотреть позже",
        "th": "ดูภายหลัง",
    },
    "nav_watched": {
        "en": "Watched",
        "sv": "Sedda",
        "es": "Vistos",
        "de": "Gesehen",
        "fr": "Vus",
        "zh": "已观看",
        "ru": "Просмотрено",
        "th": "ดูแล้ว",
    },
    "watched_local": {
        "en": "Local",
        "sv": "Lokalt",
        "es": "Local",
        "de": "Lokal",
        "fr": "Local",
        "zh": "本地",
        "ru": "Локально",
        "th": "ในเครื่อง",
    },
    "watched_trakt": {
        "en": "Trakt",
        "sv": "Trakt",
        "es": "Trakt",
        "de": "Trakt",
        "fr": "Trakt",
        "zh": "Trakt",
        "ru": "Trakt",
        "th": "Trakt",
    },
    "watched_trakt_only_note": {
        "en": "Watched on another device - not available from this provider.",
        "sv": "Sedd på en annan enhet - finns inte hos den här leverantören.",
        "es": "Visto en otro dispositivo: no disponible en este proveedor.",
        "de": "Auf einem anderen Gerät gesehen - bei diesem Anbieter nicht verfügbar.",
        "fr": "Vu sur un autre appareil - indisponible chez ce fournisseur.",
        "zh": "在其他设备上观看 - 此提供商不提供。",
        "ru": "Просмотрено на другом устройстве - недоступно у этого провайдера.",
        "th": "ดูบนอุปกรณ์อื่น - ไม่มีให้บริการจากผู้ให้บริการนี้",
    },
    "fav_channels": {
        "en": "Channels",
        "sv": "Kanaler",
        "es": "Canales",
        "de": "Kanäle",
        "fr": "Chaînes",
        "zh": "频道",
        "ru": "Каналы",
        "th": "ช่อง",
    },
    "fav_movies": {
        "en": "Movies",
        "sv": "Filmer",
        "es": "Películas",
        "de": "Filme",
        "fr": "Films",
        "zh": "电影",
        "ru": "Фильмы",
        "th": "ภาพยนตร์",
    },
    "fav_series": {
        "en": "Series",
        "sv": "Serier",
        "es": "Series",
        "de": "Serien",
        "fr": "Séries",
        "zh": "剧集",
        "ru": "Сериалы",
        "th": "ซีรีส์",
    },
    "nav_recordings": {
        "en": "Recordings",
        "sv": "Inspelningar",
        "es": "Grabaciones",
        "de": "Aufnahmen",
        "fr": "Enregistrements",
        "zh": "录制",
        "ru": "Записи",
        "th": "บันทึก",
    },
    "nav_history": {
        "en": "History",
        "sv": "Historik",
        "es": "Historial",
        "de": "Verlauf",
        "fr": "Historique",
        "zh": "历史",
        "ru": "История",
        "th": "ประวัติ",
    },
    "sidebar_categories": {
        "en": "CATEGORIES",
        "sv": "KATEGORIER",
        "es": "CATEGORÍAS",
        "de": "KATEGORIEN",
        "fr": "CATÉGORIES",
        "zh": "分类",
        "ru": "КАТЕГОРИИ",
        "th": "หมวดหมู่",
    },

    "sidebar_library": {
        "en": "LIBRARY",
        "sv": "BIBLIOTEK",
        "es": "BIBLIOTECA",
        "de": "BIBLIOTHEK",
        "fr": "BIBLIOTHÈQUE",
        "zh": "媒体库",
        "ru": "БИБЛИОТЕКА",
        "th": "คลัง",
    },

    # ── Settings dialog tabs ──────────────────────────────────────────────

    "settings_title": {
        "en": "Settings",
        "sv": "Inställningar",
        "es": "Ajustes",
        "de": "Einstellungen",
        "fr": "Paramètres",
        "zh": "设置",
        "ru": "Настройки",
        "th": "การตั้งค่า",
    },
    "tab_playback": {
        "en": "Playback",
        "sv": "Uppspelning",
        "es": "Reproducción",
        "de": "Wiedergabe",
        "fr": "Lecture",
        "zh": "播放",
        "ru": "Воспроизведение",
        "th": "เล่น",
    },
    "tab_interface": {
        "en": "Interface",
        "sv": "Gränssnitt",
        "es": "Interfaz",
        "de": "Oberfläche",
        "fr": "Interface",
        "zh": "界面",
        "ru": "Интерфейс",
        "th": "อินเทอร์เฟซ",
    },
    "tab_playlists": {
        "en": "Playlists",
        "sv": "Spellistor",
        "es": "Listas de reproducción",
        "de": "Wiedergabelisten",
        "fr": "Listes de lecture",
        "zh": "播放列表",
        "ru": "Плейлисты",
        "th": "เพลย์ลิสต์",
    },
    "account_loading": {
        "en": "Loading account…", "sv": "Laddar konto…",
        "es": "Cargando cuenta…", "de": "Konto wird geladen…",
        "fr": "Chargement du compte…", "zh": "正在加载账户……",
        "ru": "Загрузка аккаунта…", "th": "กำลังโหลดบัญชี…",
    },
    "account_unavailable": {
        "en": "Account info not available for this playlist.",
        "sv": "Kontoinfo ej tillgänglig för den här spellistan.",
        "es": "Información de cuenta no disponible para esta lista.",
        "de": "Kontoinformationen für diese Wiedergabeliste nicht verfügbar.",
        "fr": "Infos de compte indisponibles pour cette liste.",
        "zh": "此播放列表无账户信息。",
        "ru": "Данные аккаунта недоступны для этого плейлиста.",
        "th": "ไม่มีข้อมูลบัญชีสำหรับเพลย์ลิสต์นี้",
    },
    "account_status": {
        "en": "Status", "sv": "Status", "es": "Estado", "de": "Status",
        "fr": "Statut", "zh": "状态", "ru": "Статус", "th": "สถานะ",
    },
    "account_expiry": {
        "en": "Expires", "sv": "Går ut", "es": "Caduca", "de": "Läuft ab",
        "fr": "Expire", "zh": "到期", "ru": "Истекает", "th": "หมดอายุ",
    },
    "account_connections": {
        "en": "Connections", "sv": "Anslutningar", "es": "Conexiones",
        "de": "Verbindungen", "fr": "Connexions", "zh": "连接数",
        "ru": "Подключения", "th": "การเชื่อมต่อ",
    },
    "account_days_left": {
        "en": "{days} days left", "sv": "{days} dagar kvar",
        "es": "{days} días restantes", "de": "noch {days} Tage",
        "fr": "{days} jours restants", "zh": "剩余 {days} 天",
        "ru": "осталось {days} дн.", "th": "เหลือ {days} วัน",
    },
    "account_expired": {
        "en": "expired", "sv": "utgången", "es": "caducada",
        "de": "abgelaufen", "fr": "expiré", "zh": "已过期",
        "ru": "истёк", "th": "หมดอายุแล้ว",
    },
    "account_unlimited": {
        "en": "Unlimited", "sv": "Obegränsat", "es": "Ilimitado",
        "de": "Unbegrenzt", "fr": "Illimité", "zh": "无限期",
        "ru": "Бессрочно", "th": "ไม่จำกัด",
    },
    "account_trial": {
        "en": "Trial", "sv": "Prova-på", "es": "Prueba", "de": "Test",
        "fr": "Essai", "zh": "试用", "ru": "Пробный", "th": "ทดลอง",
    },
    "tab_parental": {
        "en": "Parental",
        "sv": "Föräldrakontroll",
        "es": "Control parental",
        "de": "Jugendschutz",
        "fr": "Contrôle parental",
        "zh": "家长控制",
        "ru": "Родительский контроль",
        "th": "การควบคุมโดยผู้ปกครอง",
    },
    "tab_metadata": {
        "en": "Metadata",
        "sv": "Metadata",
        "es": "Metadatos",
        "de": "Metadaten",
        "fr": "Métadonnées",
        "zh": "元数据",
        "ru": "Метаданные",
        "th": "ข้อมูลเมตา",
    },
    "tab_recording": {
        "en": "Recording",
        "sv": "Inspelning",
        "es": "Grabación",
        "de": "Aufnahme",
        "fr": "Enregistrement",
        "zh": "录制",
        "ru": "Запись",
        "th": "การบันทึก",
    },
    "tab_trakt": {
        "en": "Trakt",
        "sv": "Trakt",
        "es": "Trakt",
        "de": "Trakt",
        "fr": "Trakt",
        "zh": "Trakt",
        "ru": "Trakt",
        "th": "Trakt",
    },

    # ── Settings labels (Playback) ────────────────────────────────────────

    "setting_autoplay_preview": {
        "en": "Auto-play preview on selection",
        "sv": "Spela förhandsvisning automatiskt vid val",
        "es": "Reproducir vista previa automáticamente al seleccionar",
        "de": "Vorschau bei Auswahl automatisch abspielen",
        "fr": "Lecture automatique de l'aperçu à la sélection",
        "zh": "选择时自动预览播放",
        "ru": "Автопроигрывание превью при выборе",
        "th": "เล่นตัวอย่างอัตโนมัติเมื่อเลือก",
    },
    "setting_autoplay_next": {
        "en": "Auto-play next episode",
        "sv": "Spela nästa avsnitt automatiskt",
        "es": "Reproducir el siguiente episodio automáticamente",
        "de": "Nächste Folge automatisch abspielen",
        "fr": "Lire automatiquement l'épisode suivant",
        "zh": "自动播放下一集",
        "ru": "Автовоспроизведение следующего эпизода",
        "th": "เล่นตอนถัดไปอัตโนมัติ",
    },
    "setting_auto_reconnect": {
        "en": "Auto-reconnect live streams",
        "sv": "Återanslut live-streamar automatiskt",
        "es": "Reconectar transmisiones en directo automáticamente",
        "de": "Live-Streams automatisch neu verbinden",
        "fr": "Reconnexion automatique des flux en direct",
        "zh": "自动重新连接直播流",
        "ru": "Автоматически переподключать прямые трансляции",
        "th": "เชื่อมต่อสตรีมสดใหม่อัตโนมัติ",
    },
    "setting_stream_format": {
        "en": "Live stream format",
        "sv": "Format för liveström",
        "es": "Formato de transmisión en vivo",
        "de": "Livestream-Format",
        "fr": "Format du flux en direct",
        "zh": "直播流格式",
        "ru": "Формат прямой трансляции",
        "th": "รูปแบบสตรีมสด",
    },
    "setting_audio_lang": {
        "en": "Preferred audio language",
        "sv": "Föredraget ljudspråk",
        "es": "Idioma de audio preferido",
        "de": "Bevorzugte Audiosprache",
        "fr": "Langue audio préférée",
        "zh": "首选音频语言",
        "ru": "Предпочтительный язык аудио",
        "th": "ภาษาเสียงที่ต้องการ",
    },
    "setting_subtitles": {
        "en": "Subtitles",
        "sv": "Undertexter",
        "es": "Subtítulos",
        "de": "Untertitel",
        "fr": "Sous-titres",
        "zh": "字幕",
        "ru": "Субтитры",
        "th": "คำบรรยาย",
    },
    "setting_sub_lang": {
        "en": "Preferred subtitle language",
        "sv": "Föredraget undertextspråk",
        "es": "Idioma de subtítulos preferido",
        "de": "Bevorzugte Untertitelsprache",
        "fr": "Langue de sous-titres préférée",
        "zh": "首选字幕语言",
        "ru": "Предпочтительный язык субтитров",
        "th": "ภาษาคำบรรยายที่ต้องการ",
    },
    "setting_sub_lang_fallback": {
        "en": "Fallback subtitle language",
        "sv": "Reservundertextspråk",
        "es": "Idioma de subtítulos alternativo",
        "de": "Ersatz-Untertitelsprache",
        "fr": "Langue de sous-titres de secours",
        "zh": "备用字幕语言",
        "ru": "Запасной язык субтитров",
        "th": "ภาษาคำบรรยายสำรอง",
    },
    "setting_aspect_ratio": {
        "en": "Aspect ratio",
        "sv": "Bildförhållande",
        "es": "Relación de aspecto",
        "de": "Seitenverhältnis",
        "fr": "Format d'image",
        "zh": "宽高比",
        "ru": "Соотношение сторон",
        "th": "อัตราส่วนภาพ",
    },
    "setting_network_buffer": {
        "en": "Network buffer",
        "sv": "Nätverksbuffert",
        "es": "Búfer de red",
        "de": "Netzwerkpuffer",
        "fr": "Tampon réseau",
        "zh": "网络缓冲",
        "ru": "Сетевой буфер",
        "th": "บัฟเฟอร์เครือข่าย",
    },
    "setting_replay_delay": {
        "en": "Replay delay",
        "sv": "Reprisefördröjning",
        "es": "Retardo de repetición",
        "de": "Wiedergabeverzögerung",
        "fr": "Délai de relecture",
        "zh": "回放延迟",
        "ru": "Задержка повтора",
        "th": "ดีเลย์การเล่นซ้ำ",
    },
    "setting_epg_delay": {
        "en": "EPG delay",
        "sv": "EPG-fördröjning",
        "es": "Retardo de la guía EPG",
        "de": "EPG-Verzögerung",
        "fr": "Délai EPG",
        "zh": "EPG 延迟",
        "ru": "Задержка EPG",
        "th": "ดีเลย์ EPG",
    },
    "setting_epg_cache": {
        "en": "EPG cache", "sv": "EPG-cache",
        "es": "Caché de EPG", "de": "EPG-Cache",
        "fr": "Cache EPG", "zh": "EPG 缓存",
        "ru": "Кэш EPG", "th": "แคช EPG",
    },
    "btn_refresh_epg": {
        "en": "Refresh guide now", "sv": "Uppdatera guide nu",
        "es": "Actualizar guía", "de": "Guide jetzt aktualisieren",
        "fr": "Actualiser le guide", "zh": "立即刷新指南",
        "ru": "Обновить программу", "th": "รีเฟรชผังรายการ",
    },
    "btn_clear_epg": {
        "en": "Clear EPG cache", "sv": "Rensa EPG-cache",
        "es": "Borrar caché EPG", "de": "EPG-Cache leeren",
        "fr": "Vider le cache EPG", "zh": "清除 EPG 缓存",
        "ru": "Очистить кэш EPG", "th": "ล้างแคช EPG",
    },
    "epg_cache_cleared": {
        "en": "EPG cache cleared — reloading…",
        "sv": "EPG-cache rensad — laddar om…",
        "es": "Caché EPG borrada — recargando…",
        "de": "EPG-Cache geleert — wird neu geladen…",
        "fr": "Cache EPG vidé — rechargement…",
        "zh": "EPG 缓存已清除 — 正在重新加载…",
        "ru": "Кэш EPG очищен — перезагрузка…",
        "th": "ล้างแคช EPG แล้ว — กำลังโหลดใหม่…",
    },

    # ── Settings labels (Interface) ───────────────────────────────────────

    "setting_list_size": {
        "en": "List size",
        "sv": "Liststorlek",
        "es": "Tamaño de lista",
        "de": "Listengröße",
        "fr": "Taille de la liste",
        "zh": "列表大小",
        "ru": "Размер списка",
        "th": "ขนาดรายการ",
    },
    "setting_upcoming_count": {
        "en": "Upcoming programmes shown",
        "sv": "Antal kommande program",
        "es": "Programas próximos mostrados",
        "de": "Angezeigte kommende Sendungen",
        "fr": "Programmes à venir affichés",
        "zh": "显示的即将播出节目数",
        "ru": "Показывать предстоящих программ",
        "th": "จำนวนรายการที่กำลังจะมาถึงที่แสดง",
    },
    "setting_sort_by": {
        "en": "Sort lists by",
        "sv": "Sortera listor efter",
        "es": "Ordenar listas por",
        "de": "Listen sortieren nach",
        "fr": "Trier les listes par",
        "zh": "列表排序方式",
        "ru": "Сортировка списков по",
        "th": "เรียงรายการตาม",
    },
    "setting_theme": {
        "en": "Theme",
        "sv": "Tema",
        "es": "Tema",
        "de": "Thema",
        "fr": "Thème",
        "zh": "主题",
        "ru": "Тема",
        "th": "ธีม",
    },
    "setting_accent_color": {
        "en": "Accent color",
        "sv": "Accentfärg",
        "es": "Color de acento",
        "de": "Akzentfarbe",
        "fr": "Couleur d'accent",
        "zh": "强调色",
        "ru": "Цвет акцента",
        "th": "สีเน้น",
    },
    "setting_language": {
        "en": "Language",
        "sv": "Språk",
        "es": "Idioma",
        "de": "Sprache",
        "fr": "Langue",
        "zh": "语言",
        "ru": "Язык",
        "th": "ภาษา",
    },

    # ── Combo box option labels ───────────────────────────────────────────

    "option_compact": {
        "en": "Compact",
        "sv": "Kompakt",
        "es": "Compacto",
        "de": "Kompakt",
        "fr": "Compact",
        "zh": "紧凑",
        "ru": "Компактный",
        "th": "กะทัดรัด",
    },
    "option_medium": {
        "en": "Medium",
        "sv": "Mellan",
        "es": "Mediano",
        "de": "Mittel",
        "fr": "Moyen",
        "zh": "中等",
        "ru": "Средний",
        "th": "กลาง",
    },
    "option_large": {
        "en": "Large",
        "sv": "Stor",
        "es": "Grande",
        "de": "Groß",
        "fr": "Grand",
        "zh": "大",
        "ru": "Крупный",
        "th": "ใหญ่",
    },
    "option_xlarge": {
        "en": "Extra large (TV)",
        "sv": "Extra stor (TV)",
        "es": "Extra grande (TV)",
        "de": "Extra groß (TV)",
        "fr": "Très grand (TV)",
        "zh": "特大（电视）",
        "ru": "Очень крупный (ТВ)",
        "th": "ใหญ่พิเศษ (ทีวี)",
    },
    "option_sort_default": {
        "en": "Default (provider order)",
        "sv": "Standard (leverantörens ordning)",
        "es": "Predeterminado (orden del proveedor)",
        "de": "Standard (Anbieterreihenfolge)",
        "fr": "Par défaut (ordre du fournisseur)",
        "zh": "默认（提供商排序）",
        "ru": "По умолчанию (порядок провайдера)",
        "th": "ค่าเริ่มต้น (ลำดับผู้ให้บริการ)",
    },
    "option_sort_az": {
        "en": "Name A -> Z",
        "sv": "Namn A -> Ö",
        "es": "Nombre A -> Z",
        "de": "Name A -> Z",
        "fr": "Nom A -> Z",
        "zh": "名称 A -> Z",
        "ru": "Имя А -> Я",
        "th": "ชื่อ A -> Z",
    },
    "option_sort_za": {
        "en": "Name Z -> A",
        "sv": "Namn Ö -> A",
        "es": "Nombre Z -> A",
        "de": "Name Z -> A",
        "fr": "Nom Z -> A",
        "zh": "名称 Z -> A",
        "ru": "Имя Я -> А",
        "th": "ชื่อ Z -> A",
    },
    "option_sort_recent": {
        "en": "Recently added",
        "sv": "Senast tillagda",
        "es": "Añadidos recientemente",
        "de": "Zuletzt hinzugefügt",
        "fr": "Récemment ajoutés",
        "zh": "最近添加",
        "ru": "Недавно добавленные",
        "th": "เพิ่มล่าสุด",
    },
    "option_sub_off": {
        "en": "Off",
        "sv": "Av",
        "es": "Desactivado",
        "de": "Aus",
        "fr": "Désactivé",
        "zh": "关闭",
        "ru": "Выкл.",
        "th": "ปิด",
    },
    "option_sub_auto": {
        "en": "On (player default)",
        "sv": "På (spelarens standard)",
        "es": "Activado (predeterminado del reproductor)",
        "de": "An (Player-Standard)",
        "fr": "Activé (par défaut du lecteur)",
        "zh": "开启（播放器默认）",
        "ru": "Вкл. (по умолчанию плеера)",
        "th": "เปิด (ค่าเริ่มต้นของเครื่องเล่น)",
    },
    "option_sub_lang": {
        "en": "On - preferred language",
        "sv": "På - föredraget språk",
        "es": "Activado - idioma preferido",
        "de": "An - bevorzugte Sprache",
        "fr": "Activé - langue préférée",
        "zh": "开启 - 首选语言",
        "ru": "Вкл. - предпочтительный язык",
        "th": "เปิด - ภาษาที่ต้องการ",
    },
    "option_sub_forced": {
        "en": "On - forced subtitles only",
        "sv": "På - enbart tvingade undertexter",
        "es": "Activado - solo subtítulos forzados",
        "de": "An - nur erzwungene Untertitel",
        "fr": "Activé - sous-titres forcés uniquement",
        "zh": "开启 - 仅强制字幕",
        "ru": "Вкл. - только принудительные субтитры",
        "th": "เปิด - คำบรรยายบังคับเท่านั้น",
    },
    "option_aspect_auto": {
        "en": "Auto",
        "sv": "Auto",
        "es": "Automático",
        "de": "Auto",
        "fr": "Auto",
        "zh": "自动",
        "ru": "Авто",
        "th": "อัตโนมัติ",
    },
    "option_aspect_stretch": {
        "en": "Stretch to window",
        "sv": "Sträck till fönstret",
        "es": "Estirar a la ventana",
        "de": "Auf Fenster strecken",
        "fr": "Étirer à la fenêtre",
        "zh": "拉伸到窗口",
        "ru": "Растянуть на окно",
        "th": "ยืดให้เต็มหน้าต่าง",
    },
    "option_yes": {
        "en": "Yes",
        "sv": "Ja",
        "es": "Sí",
        "de": "Ja",
        "fr": "Oui",
        "zh": "是",
        "ru": "Да",
        "th": "ใช่",
    },
    "option_no": {
        "en": "No",
        "sv": "Nej",
        "es": "No",
        "de": "Nein",
        "fr": "Non",
        "zh": "否",
        "ru": "Нет",
        "th": "ไม่",
    },
    "option_lang_auto": {
        "en": "Auto / provider default",
        "sv": "Auto / leverantörens standard",
        "es": "Automático / predeterminado del proveedor",
        "de": "Auto / Standard des Anbieters",
        "fr": "Auto / par défaut du fournisseur",
        "zh": "自动 / 提供商默认",
        "ru": "Авто / по умолчанию провайдера",
        "th": "อัตโนมัติ / ค่าเริ่มต้นของผู้ให้บริการ",
    },

    # ── Buttons / actions ─────────────────────────────────────────────────

    "btn_play": {
        "en": "Play",
        "sv": "Spela",
        "es": "Reproducir",
        "de": "Abspielen",
        "fr": "Lire",
        "zh": "播放",
        "ru": "Воспроизвести",
        "th": "เล่น",
    },
    "btn_stop": {
        "en": "Stop",
        "sv": "Stoppa",
        "es": "Detener",
        "de": "Stopp",
        "fr": "Arrêter",
        "zh": "停止",
        "ru": "Стоп",
        "th": "หยุด",
    },
    "btn_pause": {
        "en": "Pause",
        "sv": "Pausa",
        "es": "Pausa",
        "de": "Pause",
        "fr": "Pause",
        "zh": "暂停",
        "ru": "Пауза",
        "th": "หยุดชั่วคราว",
    },
    "btn_settings": {
        "en": "Settings",
        "sv": "Inställningar",
        "es": "Ajustes",
        "de": "Einstellungen",
        "fr": "Paramètres",
        "zh": "设置",
        "ru": "Настройки",
        "th": "การตั้งค่า",
    },
    "btn_refresh": {
        "en": "Refresh",
        "sv": "Uppdatera",
        "es": "Actualizar",
        "de": "Aktualisieren",
        "fr": "Actualiser",
        "zh": "刷新",
        "ru": "Обновить",
        "th": "รีเฟรช",
    },
    "btn_epg_guide": {
        "en": "EPG Guide",
        "sv": "EPG-guide",
        "es": "Guía EPG",
        "de": "EPG-Guide",
        "fr": "Guide EPG",
        "zh": "节目指南",
        "ru": "Программа передач",
        "th": "คู่มือ EPG",
    },
    "epg_filter_channels": {
        "en": "Filter channels…", "sv": "Filtrera kanaler…",
        "es": "Filtrar canales…", "de": "Kanäle filtern…",
        "fr": "Filtrer les chaînes…", "zh": "筛选频道…",
        "ru": "Фильтр каналов…", "th": "กรองช่อง…",
    },
    "epg_select_channel": {
        "en": "Select a channel", "sv": "Välj en kanal",
        "es": "Selecciona un canal", "de": "Kanal auswählen",
        "fr": "Sélectionnez une chaîne", "zh": "选择一个频道",
        "ru": "Выберите канал", "th": "เลือกช่อง",
    },
    "epg_jump_now": {
        "en": "Now", "sv": "Nu", "es": "Ahora", "de": "Jetzt",
        "fr": "Maintenant", "zh": "现在", "ru": "Сейчас", "th": "ตอนนี้",
    },
    "epg_jump_playing": {
        "en": "Playing", "sv": "Spelas nu", "es": "En reproducción",
        "de": "Läuft", "fr": "En cours", "zh": "正在播放",
        "ru": "Сейчас идёт", "th": "กำลังเล่น",
    },
    "epg_play_channel": {
        "en": "Play channel", "sv": "Spela kanal",
        "es": "Reproducir canal", "de": "Kanal abspielen",
        "fr": "Lire la chaîne", "zh": "播放频道",
        "ru": "Воспроизвести канал", "th": "เล่นช่อง",
    },
    "epg_no_programme": {
        "en": "No current programme data", "sv": "Ingen aktuell programdata",
        "es": "Sin datos de programa actual",
        "de": "Keine aktuellen Programmdaten",
        "fr": "Aucune donnée de programme actuelle", "zh": "无当前节目数据",
        "ru": "Нет данных о текущей программе", "th": "ไม่มีข้อมูลรายการปัจจุบัน",
    },
    "epg_now_prefix": {
        "en": "Now", "sv": "Nu", "es": "Ahora", "de": "Jetzt",
        "fr": "En cours", "zh": "正在播放", "ru": "Сейчас", "th": "ตอนนี้",
    },
    "epg_record_hint": {
        "en": "Right-click an upcoming or currently-airing programme to "
              "record it (multi-select works too).",
        "sv": "Högerklicka på ett kommande eller pågående program för att "
              "spela in det (flerval fungerar också).",
        "es": "Haz clic derecho en un programa próximo o en emisión para "
              "grabarlo (también funciona la selección múltiple).",
        "de": "Rechtsklick auf eine kommende oder laufende Sendung, um sie "
              "aufzunehmen (Mehrfachauswahl möglich).",
        "fr": "Clic droit sur un programme à venir ou en cours pour "
              "l'enregistrer (sélection multiple possible).",
        "zh": "右键点击即将播出或正在播出的节目即可录制（也支持多选）。",
        "ru": "Щёлкните правой кнопкой по будущей или текущей передаче, чтобы "
              "записать её (работает и множественный выбор).",
        "th": "คลิกขวาที่รายการที่กำลังจะออกอากาศหรือกำลังออกอากาศเพื่อบันทึก "
              "(เลือกหลายรายการได้)",
    },
    "epg_channels_count": {
        "en": "{n} channels", "sv": "{n} kanaler", "es": "{n} canales",
        "de": "{n} Kanäle", "fr": "{n} chaînes", "zh": "{n} 个频道",
        "ru": "{n} каналов", "th": "{n} ช่อง",
    },
    "epg_channels_first": {
        "en": "(first {n})", "sv": "(första {n})", "es": "(primeros {n})",
        "de": "(erste {n})", "fr": "({n} premières)", "zh": "（前 {n} 个）",
        "ru": "(первые {n})", "th": "({n} แรก)",
    },
    "btn_add": {
        "en": "Add…",
        "sv": "Lägg till…",
        "es": "Añadir…",
        "de": "Hinzufügen…",
        "fr": "Ajouter…",
        "zh": "添加…",
        "ru": "Добавить…",
        "th": "เพิ่ม…",
    },
    "btn_edit": {
        "en": "Edit…",
        "sv": "Redigera…",
        "es": "Editar…",
        "de": "Bearbeiten…",
        "fr": "Modifier…",
        "zh": "编辑…",
        "ru": "Изменить…",
        "th": "แก้ไข…",
    },
    "btn_remove": {
        "en": "Remove",
        "sv": "Ta bort",
        "es": "Eliminar",
        "de": "Entfernen",
        "fr": "Supprimer",
        "zh": "移除",
        "ru": "Удалить",
        "th": "ลบ",
    },
    "btn_export": {
        "en": "Export…",
        "sv": "Exportera…",
        "es": "Exportar…",
        "de": "Exportieren…",
        "fr": "Exporter…",
        "zh": "导出…",
        "ru": "Экспорт…",
        "th": "ส่งออก…",
    },
    "btn_import": {
        "en": "Import…",
        "sv": "Importera…",
        "es": "Importar…",
        "de": "Importieren…",
        "fr": "Importer…",
        "zh": "导入…",
        "ru": "Импорт…",
        "th": "นำเข้า…",
    },
    "btn_close": {
        "en": "Close",
        "sv": "Stäng",
        "es": "Cerrar",
        "de": "Schließen",
        "fr": "Fermer",
        "zh": "关闭",
        "ru": "Закрыть",
        "th": "ปิด",
    },
    "btn_ok": {
        "en": "Ok",
        "sv": "Ok",
        "es": "Aceptar",
        "de": "Ok",
        "fr": "Ok",
        "zh": "确定",
        "ru": "Ок",
        "th": "ตกลง",
    },
    "btn_cancel": {
        "en": "Cancel",
        "sv": "Avbryt",
        "es": "Cancelar",
        "de": "Abbrechen",
        "fr": "Annuler",
        "zh": "取消",
        "ru": "Отмена",
        "th": "ยกเลิก",
    },
    "btn_save": {
        "en": "Save",
        "sv": "Spara",
        "es": "Guardar",
        "de": "Speichern",
        "fr": "Enregistrer",
        "zh": "保存",
        "ru": "Сохранить",
        "th": "บันทึก",
    },
    "btn_search": {
        "en": "Search",
        "sv": "Sök",
        "es": "Buscar",
        "de": "Suchen",
        "fr": "Rechercher",
        "zh": "搜索",
        "ru": "Поиск",
        "th": "ค้นหา",
    },
    "btn_connect": {
        "en": "Connect",
        "sv": "Anslut",
        "es": "Conectar",
        "de": "Verbinden",
        "fr": "Connecter",
        "zh": "连接",
        "ru": "Подключить",
        "th": "เชื่อมต่อ",
    },
    "welcome_title": {
        "en": "Welcome to dopeIPTV",
        "sv": "Välkommen till dopeIPTV",
        "es": "Bienvenido a dopeIPTV",
        "de": "Willkommen bei dopeIPTV",
        "fr": "Bienvenue dans dopeIPTV",
        "zh": "欢迎使用 dopeIPTV",
        "ru": "Добро пожаловать в dopeIPTV",
        "th": "ยินดีต้อนรับสู่ dopeIPTV",
    },
    "welcome_subtitle": {
        "en": "Connect your IPTV provider to load your channels, movies and "
              "series — or just look around first.",
        "sv": "Anslut din IPTV-leverantör för att ladda dina kanaler, filmer "
              "och serier – eller kika runt först.",
        "es": "Conecta tu proveedor de IPTV para cargar tus canales, películas "
              "y series, o simplemente echa un vistazo primero.",
        "de": "Verbinde deinen IPTV-Anbieter, um deine Sender, Filme und "
              "Serien zu laden – oder sieh dich erst einmal um.",
        "fr": "Connectez votre fournisseur IPTV pour charger vos chaînes, "
              "films et séries — ou jetez d'abord un coup d'œil.",
        "zh": "连接您的 IPTV 提供商以加载频道、电影和剧集，或先四处看看。",
        "ru": "Подключите IPTV-провайдера, чтобы загрузить каналы, фильмы и "
              "сериалы — или просто осмотритесь.",
        "th": "เชื่อมต่อผู้ให้บริการ IPTV เพื่อโหลดช่อง ภาพยนตร์ และซีรีส์ "
              "— หรือลองดูรอบ ๆ ก่อนก็ได้",
    },
    "welcome_connect": {
        "en": "Connect your provider",
        "sv": "Anslut din leverantör",
        "es": "Conecta tu proveedor",
        "de": "Anbieter verbinden",
        "fr": "Connecter votre fournisseur",
        "zh": "连接您的提供商",
        "ru": "Подключить провайдера",
        "th": "เชื่อมต่อผู้ให้บริการ",
    },
    "welcome_explore": {
        "en": "Continue without account",
        "sv": "Fortsätt utan konto",
        "es": "Continuar sin cuenta",
        "de": "Ohne Konto fortfahren",
        "fr": "Continuer sans compte",
        "zh": "无账户继续",
        "ru": "Продолжить без аккаунта",
        "th": "ดำเนินการต่อโดยไม่มีบัญชี",
    },
    "onb_try_demo": {
        "en": "🎬 Try demo channels",
        "sv": "🎬 Testa demo-kanaler",
        "es": "🎬 Probar canales de demostración",
        "de": "🎬 Demo-Kanäle testen",
        "fr": "🎬 Essayer les chaînes de démo",
        "zh": "🎬 试用演示频道",
        "ru": "🎬 Попробовать демоканалы",
        "th": "🎬 ลองช่องเดโม",
    },
    "demo_notice": {
        "en": "Demo mode: a few free public test streams so you can try the "
              "app. They're third-party services, so playback isn't "
              "guaranteed. Add your own provider any time for the full "
              "experience.",
        "sv": "Demoläge: några gratis publika teststreams så du kan testa "
              "appen. De är tredjepartstjänster, så uppspelning kan inte "
              "garanteras. Lägg till din egen leverantör när du vill för hela "
              "upplevelsen.",
        "es": "Modo demo: algunas transmisiones de prueba públicas y gratuitas "
              "para probar la app. Son servicios de terceros, así que la "
              "reproducción no está garantizada. Añade tu proveedor cuando "
              "quieras.",
        "de": "Demo-Modus: ein paar kostenlose öffentliche Teststreams zum "
              "Ausprobieren. Es sind Drittanbieter-Dienste, die Wiedergabe "
              "ist daher nicht garantiert. Füge jederzeit deinen eigenen "
              "Anbieter hinzu.",
        "fr": "Mode démo : quelques flux de test publics et gratuits pour "
              "essayer l'appli. Ce sont des services tiers, la lecture n'est "
              "donc pas garantie. Ajoutez votre fournisseur quand vous voulez.",
        "zh": "演示模式：一些免费的公共测试流，供你试用本应用。它们是第三方服务，"
              "因此无法保证播放。随时可以添加你自己的服务商。",
        "ru": "Демо-режим: несколько бесплатных публичных тестовых потоков, "
              "чтобы попробовать приложение. Это сторонние сервисы, поэтому "
              "воспроизведение не гарантируется. В любой момент добавьте "
              "своего провайдера.",
        "th": "โหมดเดโม: สตรีมทดสอบสาธารณะฟรีสองสามรายการเพื่อให้ลองใช้แอป "
              "เป็นบริการของบุคคลที่สาม จึงไม่รับประกันการเล่น "
              "เพิ่มผู้ให้บริการของคุณเองได้ทุกเมื่อ",
    },
    "trakt_wizard_intro": {
        "en": "One-time setup: create a free Trakt API app, set its Redirect "
              "URI to exactly {url}, and paste its Client ID and Secret below. "
              "After that, Connect just opens your browser and you click Yes.",
        "sv": "Engångsinställning: skapa en gratis Trakt-API-app, sätt dess "
              "Redirect URI till exakt {url} och klistra in dess Client ID och "
              "Secret nedan. Sedan öppnar Connect bara webbläsaren och du "
              "klickar Yes.",
        "es": "Configuración única: crea una app API gratuita de Trakt, pon su "
              "Redirect URI exactamente en {url} y pega su Client ID y Secret "
              "abajo. Después, Conectar solo abre el navegador y pulsas Sí.",
        "de": "Einmalige Einrichtung: erstelle eine kostenlose Trakt-API-App, "
              "setze ihre Redirect-URI exakt auf {url} und füge Client ID und "
              "Secret unten ein. Danach öffnet Connect nur den Browser und du "
              "klickst Ja.",
        "fr": "Configuration unique : créez une app API Trakt gratuite, "
              "définissez son Redirect URI exactement sur {url}, puis collez "
              "son Client ID et Secret ci-dessous. Ensuite, Connecter ouvre "
              "le navigateur et vous cliquez sur Oui.",
        "zh": "一次性设置：创建一个免费的 Trakt API 应用，将其 Redirect URI "
              "设置为 {url}，然后在下面粘贴 Client ID 和 Secret。之后，"
              "连接只会打开浏览器，你点击 Yes 即可。",
        "ru": "Разовая настройка: создайте бесплатное Trakt API-приложение, "
              "укажите его Redirect URI точно как {url} и вставьте Client ID и "
              "Secret ниже. После этого «Подключить» просто откроет браузер, и "
              "вы нажмёте Yes.",
        "th": "ตั้งค่าครั้งเดียว: สร้างแอป Trakt API ฟรี ตั้ง Redirect URI ให้เป็น {url} "
              "พอดี แล้ววาง Client ID และ Secret ด้านล่าง จากนั้น Connect "
              "จะเปิดเบราว์เซอร์ให้คุณคลิก Yes",
    },
    "demo_title": {
        "en": "dopeIPTV — Demo channels",
        "sv": "dopeIPTV — Demo-kanaler",
        "es": "dopeIPTV — Canales de demostración",
        "de": "dopeIPTV — Demo-Kanäle",
        "fr": "dopeIPTV — Chaînes de démo",
        "zh": "dopeIPTV — 演示频道",
        "ru": "dopeIPTV — Демоканалы",
        "th": "dopeIPTV — ช่องเดโม",
    },
    "onb_choose_language": {
        "en": "Choose your language",
        "sv": "Välj språk",
        "es": "Elige tu idioma",
        "de": "Sprache wählen",
        "fr": "Choisissez votre langue",
        "zh": "选择语言",
        "ru": "Выберите язык",
        "th": "เลือกภาษาของคุณ",
    },
    "onb_next": {
        "en": "Next", "sv": "Nästa", "es": "Siguiente", "de": "Weiter",
        "fr": "Suivant", "zh": "下一步", "ru": "Далее", "th": "ถัดไป",
    },
    "onb_back": {
        "en": "Back", "sv": "Tillbaka", "es": "Atrás", "de": "Zurück",
        "fr": "Retour", "zh": "上一步", "ru": "Назад", "th": "ย้อนกลับ",
    },
    "onb_skip": {
        "en": "Skip for now", "sv": "Hoppa över", "es": "Omitir por ahora",
        "de": "Vorerst überspringen", "fr": "Ignorer pour l'instant",
        "zh": "暂时跳过", "ru": "Пропустить", "th": "ข้ามไปก่อน",
    },
    "onb_finish": {
        "en": "Finish", "sv": "Klar", "es": "Finalizar", "de": "Fertig",
        "fr": "Terminer", "zh": "完成", "ru": "Готово", "th": "เสร็จสิ้น",
    },
    "onb_features_title": {
        "en": "What's inside dopeIPTV",
        "sv": "Det här finns i dopeIPTV",
        "es": "Qué incluye dopeIPTV",
        "de": "Das steckt in dopeIPTV",
        "fr": "Ce que contient dopeIPTV",
        "zh": "dopeIPTV 有什么",
        "ru": "Что внутри dopeIPTV",
        "th": "สิ่งที่มีใน dopeIPTV",
    },
    "onb_feat_1": {
        "en": "Live TV, Movies & Series — fast and searchable",
        "sv": "Live-TV, filmer och serier – snabbt och sökbart",
        "es": "TV en directo, películas y series — rápido y con búsqueda",
        "de": "Live-TV, Filme & Serien — schnell und durchsuchbar",
        "fr": "TV en direct, films et séries — rapide et cherchable",
        "zh": "直播电视、电影和剧集 — 快速且可搜索",
        "ru": "Live-ТВ, фильмы и сериалы — быстро и с поиском",
        "th": "ทีวีสด ภาพยนตร์ และซีรีส์ — รวดเร็วและค้นหาได้",
    },
    "onb_feat_2": {
        "en": "Full EPG guide with now/next and catch-up",
        "sv": "Full EPG-guide med nu/nästa och catch-up",
        "es": "Guía EPG completa con ahora/después y catch-up",
        "de": "Voller EPG-Guide mit Jetzt/Nächstes und Catch-up",
        "fr": "Guide EPG complet avec en cours/à suivre et rattrapage",
        "zh": "完整的 EPG 节目指南，含正在播出/稍后及回看",
        "ru": "Полный EPG с «сейчас/далее» и архивом",
        "th": "คู่มือ EPG เต็มรูปแบบพร้อมกำลังฉาย/ถัดไป และดูย้อนหลัง",
    },
    "onb_feat_3": {
        "en": "Built-in video player — no external app needed",
        "sv": "Inbyggd videospelare – ingen extern app behövs",
        "es": "Reproductor de vídeo integrado — sin apps externas",
        "de": "Eingebauter Player — keine externe App nötig",
        "fr": "Lecteur vidéo intégré — aucune app externe requise",
        "zh": "内置视频播放器 — 无需外部应用",
        "ru": "Встроенный плеер — без сторонних приложений",
        "th": "เครื่องเล่นวิดีโอในตัว — ไม่ต้องใช้แอปภายนอก",
    },
    "onb_feat_4": {
        "en": "Recording, Chromecast, Trakt sync, themes & more",
        "sv": "Inspelning, Chromecast, Trakt-synk, teman m.m.",
        "es": "Grabación, Chromecast, sync con Trakt, temas y más",
        "de": "Aufnahme, Chromecast, Trakt-Sync, Themes & mehr",
        "fr": "Enregistrement, Chromecast, sync Trakt, thèmes et plus",
        "zh": "录制、Chromecast、Trakt 同步、主题等",
        "ru": "Запись, Chromecast, синхронизация Trakt, темы и не только",
        "th": "การบันทึก, Chromecast, ซิงก์ Trakt, ธีม และอื่น ๆ",
    },
    "onb_trakt_title": {
        "en": "Connect Trakt (optional)",
        "sv": "Anslut Trakt (valfritt)",
        "es": "Conectar Trakt (opcional)",
        "de": "Trakt verbinden (optional)",
        "fr": "Connecter Trakt (facultatif)",
        "zh": "连接 Trakt（可选）",
        "ru": "Подключить Trakt (необязательно)",
        "th": "เชื่อมต่อ Trakt (ไม่บังคับ)",
    },
    "onb_trakt_desc": {
        "en": "Sync your watched history and watchlist with Trakt.tv. This is "
              "completely optional — you can also do it later in Settings.",
        "sv": "Synka din sedda-historik och bevakningslista med Trakt.tv. Helt "
              "valfritt – du kan även göra det senare i Inställningar.",
        "es": "Sincroniza tu historial y tu lista con Trakt.tv. Es totalmente "
              "opcional; también puedes hacerlo luego en Ajustes.",
        "de": "Synchronisiere deinen Verlauf und deine Merkliste mit Trakt.tv. "
              "Völlig optional — geht auch später in den Einstellungen.",
        "fr": "Synchronisez votre historique et votre liste avec Trakt.tv. "
              "C'est facultatif — vous pourrez le faire plus tard dans les "
              "Réglages.",
        "zh": "将观看记录和待看列表与 Trakt.tv 同步。完全可选 — 也可稍后在设置中完成。",
        "ru": "Синхронизируйте историю просмотров и список с Trakt.tv. Это "
              "необязательно — можно сделать позже в настройках.",
        "th": "ซิงก์ประวัติการดูและรายการที่อยากดูกับ Trakt.tv ทั้งหมดนี้ไม่บังคับ "
              "— ทำภายหลังในการตั้งค่าก็ได้",
    },
    "onb_trakt_connect": {
        "en": "Sign in with Trakt", "sv": "Logga in med Trakt",
        "es": "Iniciar sesión con Trakt", "de": "Mit Trakt anmelden",
        "fr": "Se connecter avec Trakt", "zh": "使用 Trakt 登录",
        "ru": "Войти через Trakt", "th": "เข้าสู่ระบบด้วย Trakt",
    },
    "onb_limited_notice": {
        "en": "You're exploring without a provider, so the app is limited. "
              "Add one any time with the “+ Add provider” button in the "
              "middle.",
        "sv": "Du utforskar utan en leverantör, så appen är begränsad. Lägg "
              "till en när som helst med “+ Lägg till leverantör”-knappen i "
              "mitten.",
        "es": "Estás explorando sin proveedor, así que la app está limitada. "
              "Añade uno cuando quieras con el botón “+ Añadir proveedor” del "
              "centro.",
        "de": "Du erkundest ohne Anbieter, daher ist die App eingeschränkt. "
              "Füge jederzeit einen über die Schaltfläche „+ Anbieter "
              "hinzufügen“ in der Mitte hinzu.",
        "fr": "Vous explorez sans fournisseur, l'app est donc limitée. "
              "Ajoutez-en un à tout moment avec le bouton « + Ajouter un "
              "fournisseur » au centre.",
        "zh": "你正在没有提供商的情况下浏览，因此应用功能受限。可随时用中间的"
              "“+ 添加提供商”按钮添加。",
        "ru": "Вы просматриваете без провайдера, поэтому приложение "
              "ограничено. Добавьте его в любой момент кнопкой «+ Добавить "
              "провайдера» в центре.",
        "th": "คุณกำลังสำรวจโดยไม่มีผู้ให้บริการ แอปจึงถูกจำกัด "
              "เพิ่มได้ทุกเมื่อด้วยปุ่ม “+ เพิ่มผู้ให้บริการ” ตรงกลาง",
    },
    "sort_global": {
        "en": "Global default", "sv": "Global standard",
        "es": "Predet. global", "de": "Globaler Standard",
        "fr": "Défaut global", "zh": "全局默认",
        "ru": "Общий по умолч.", "th": "ค่าเริ่มต้นรวม",
    },
    "sort_scope_hint": {
        "en": "Sort order for this category. Pick “Global default” to follow "
              "the app-wide setting.",
        "sv": "Sortering för den här kategorin. Välj ”Global standard” för att "
              "följa appens globala inställning.",
        "es": "Orden para esta categoría. Elige “Predet. global” para seguir "
              "el ajuste general.",
        "de": "Sortierung für diese Kategorie. „Globaler Standard“ folgt der "
              "app-weiten Einstellung.",
        "fr": "Tri pour cette catégorie. Choisissez « Défaut global » pour "
              "suivre le réglage général.",
        "zh": "此分类的排序。选择“全局默认”以跟随全局设置。",
        "ru": "Сортировка для этой категории. Выберите «Общий по умолч.», "
              "чтобы следовать общей настройке.",
        "th": "การเรียงสำหรับหมวดนี้ เลือก “ค่าเริ่มต้นรวม” เพื่อใช้ค่าตั้งรวมของแอป",
    },
    "tooltip_toggle_sidebar": {
        "en": "Collapse the sidebar to icons (Ctrl+B)",
        "sv": "Fäll ihop sidopanelen till ikoner (Ctrl+B)",
        "es": "Contraer la barra lateral a iconos (Ctrl+B)",
        "de": "Seitenleiste auf Symbole verkleinern (Strg+B)",
        "fr": "Réduire la barre latérale en icônes (Ctrl+B)",
        "zh": "将侧边栏折叠为图标 (Ctrl+B)",
        "ru": "Свернуть боковую панель в значки (Ctrl+B)",
        "th": "ย่อแถบด้านข้างเป็นไอคอน (Ctrl+B)",
    },
    "nav_set_color": {
        "en": "Set color…", "sv": "Välj färg…", "es": "Elegir color…",
        "de": "Farbe wählen…", "fr": "Choisir la couleur…",
        "zh": "设置颜色…", "ru": "Выбрать цвет…", "th": "ตั้งสี…",
    },
    "nav_set_text_color": {
        "en": "Set text color…", "sv": "Välj textfärg…",
        "es": "Color del texto…", "de": "Textfarbe wählen…",
        "fr": "Couleur du texte…", "zh": "设置文字颜色…",
        "ru": "Цвет текста…", "th": "ตั้งสีข้อความ…",
    },
    "nav_set_bg_color": {
        "en": "Set background color…", "sv": "Välj bakgrundsfärg…",
        "es": "Color de fondo…", "de": "Hintergrundfarbe wählen…",
        "fr": "Couleur de fond…", "zh": "设置背景颜色…",
        "ru": "Цвет фона…", "th": "ตั้งสีพื้นหลัง…",
    },
    "nav_reset_color": {
        "en": "Reset color", "sv": "Återställ färg", "es": "Restablecer color",
        "de": "Farbe zurücksetzen", "fr": "Réinitialiser la couleur",
        "zh": "重置颜色", "ru": "Сбросить цвет", "th": "รีเซ็ตสี",
    },
    "tooltip_jump_playing": {
        "en": "Go to what's playing now",
        "sv": "Gå till det som spelas nu",
        "es": "Ir a lo que se está reproduciendo",
        "de": "Zum aktuell Laufenden springen",
        "fr": "Aller à la lecture en cours",
        "zh": "跳转到正在播放的内容",
        "ru": "Перейти к тому, что играет сейчас",
        "th": "ไปที่รายการที่กำลังเล่น",
    },
    "toast_nothing_playing": {
        "en": "Nothing is playing right now",
        "sv": "Inget spelas just nu",
        "es": "No se está reproduciendo nada ahora",
        "de": "Es läuft gerade nichts",
        "fr": "Rien n'est en cours de lecture",
        "zh": "当前没有正在播放的内容",
        "ru": "Сейчас ничего не воспроизводится",
        "th": "ยังไม่มีรายการที่กำลังเล่น",
    },
    "tooltip_hide_list": {
        "en": "Hide the list — focus the player (Ctrl+Shift+M)",
        "sv": "Dölj listan — fokusera spelaren (Ctrl+Shift+M)",
        "es": "Ocultar la lista — enfocar el reproductor (Ctrl+Shift+M)",
        "de": "Liste ausblenden — auf den Player fokussieren (Strg+Umschalt+M)",
        "fr": "Masquer la liste — se concentrer sur le lecteur (Ctrl+Maj+M)",
        "zh": "隐藏列表 — 专注播放器 (Ctrl+Shift+M)",
        "ru": "Скрыть список — фокус на плеере (Ctrl+Shift+M)",
        "th": "ซ่อนรายการ — โฟกัสที่เครื่องเล่น (Ctrl+Shift+M)",
    },
    "tooltip_show_list": {
        "en": "Show the list again",
        "sv": "Visa listan igen",
        "es": "Mostrar la lista de nuevo",
        "de": "Liste wieder anzeigen",
        "fr": "Réafficher la liste",
        "zh": "重新显示列表",
        "ru": "Показать список снова",
        "th": "แสดงรายการอีกครั้ง",
    },
    "tooltip_solo_category": {
        "en": "Collapse categories — show only the current one",
        "sv": "Fäll ihop kategorier — visa bara den aktuella",
        "es": "Contraer categorías — mostrar solo la actual",
        "de": "Kategorien einklappen — nur die aktuelle zeigen",
        "fr": "Réduire les catégories — n'afficher que l'actuelle",
        "zh": "折叠分类 — 仅显示当前分类",
        "ru": "Свернуть категории — показать только текущую",
        "th": "ย่อหมวดหมู่ — แสดงเฉพาะหมวดปัจจุบัน",
    },
    "tooltip_toggle_library": {
        "en": "Show or hide your library",
        "sv": "Visa eller dölj biblioteket",
        "es": "Mostrar u ocultar tu biblioteca",
        "de": "Bibliothek ein- oder ausblenden",
        "fr": "Afficher ou masquer votre bibliothèque",
        "zh": "显示或隐藏媒体库",
        "ru": "Показать или скрыть библиотеку",
        "th": "แสดงหรือซ่อนคลังของคุณ",
    },
    "rec_today": {
        "en": "Today", "sv": "Idag", "es": "Hoy", "de": "Heute",
        "fr": "Aujourd'hui", "zh": "今天", "ru": "Сегодня", "th": "วันนี้",
    },
    "rec_yesterday": {
        "en": "Yesterday", "sv": "Igår", "es": "Ayer", "de": "Gestern",
        "fr": "Hier", "zh": "昨天", "ru": "Вчера", "th": "เมื่อวาน",
    },
    "rec_this_week": {
        "en": "This week", "sv": "Denna vecka", "es": "Esta semana",
        "de": "Diese Woche", "fr": "Cette semaine", "zh": "本周",
        "ru": "На этой неделе", "th": "สัปดาห์นี้",
    },
    "rec_earlier": {
        "en": "Earlier", "sv": "Tidigare", "es": "Anteriores", "de": "Früher",
        "fr": "Plus tôt", "zh": "更早", "ru": "Ранее", "th": "ก่อนหน้านี้",
    },
    "fav_empty_all": {
        "en": "No favorites yet — right-click any channel, movie or series "
              "to add one.",
        "sv": "Inga favoriter än – högerklicka en kanal, film eller serie för "
              "att lägga till.",
        "es": "Aún no hay favoritos: haz clic derecho en un canal, película o "
              "serie para añadir uno.",
        "de": "Noch keine Favoriten — Rechtsklick auf einen Sender, Film oder "
              "eine Serie zum Hinzufügen.",
        "fr": "Aucun favori pour l'instant — clic droit sur une chaîne, un "
              "film ou une série pour en ajouter.",
        "zh": "还没有收藏 — 右键点击任意频道、电影或剧集即可添加。",
        "ru": "Пока нет избранного — щёлкните правой кнопкой по каналу, фильму "
              "или сериалу, чтобы добавить.",
        "th": "ยังไม่มีรายการโปรด — คลิกขวาที่ช่อง ภาพยนตร์ หรือซีรีส์ "
              "เพื่อเพิ่ม",
    },
    "onb_fill_all": {
        "en": "Please fill in server, username and password.",
        "sv": "Fyll i server, användarnamn och lösenord.",
        "es": "Completa el servidor, el usuario y la contraseña.",
        "de": "Bitte Server, Benutzername und Passwort ausfüllen.",
        "fr": "Veuillez remplir le serveur, l'identifiant et le mot de passe.",
        "zh": "请填写服务器、用户名和密码。",
        "ru": "Заполните сервер, имя пользователя и пароль.",
        "th": "กรุณากรอกเซิร์ฟเวอร์ ชื่อผู้ใช้ และรหัสผ่าน",
    },
    "onb_add_provider": {
        "en": "+ Add provider",
        "sv": "+ Lägg till leverantör",
        "es": "+ Añadir proveedor",
        "de": "+ Anbieter hinzufügen",
        "fr": "+ Ajouter un fournisseur",
        "zh": "+ 添加提供商",
        "ru": "+ Добавить провайдера",
        "th": "+ เพิ่มผู้ให้บริการ",
    },
    "welcome_add_hint": {
        "en": "No provider yet — add one any time in Settings.",
        "sv": "Ingen leverantör än – lägg till en när som helst i Inställningar.",
        "es": "Aún no hay proveedor: añade uno cuando quieras en Ajustes.",
        "de": "Noch kein Anbieter – füge jederzeit einen in den Einstellungen "
              "hinzu.",
        "fr": "Aucun fournisseur pour l'instant — ajoutez-en un à tout moment "
              "dans les Réglages.",
        "zh": "尚无提供商 — 可随时在设置中添加。",
        "ru": "Провайдер ещё не добавлен — добавьте его в любой момент в "
              "настройках.",
        "th": "ยังไม่มีผู้ให้บริการ — เพิ่มได้ทุกเมื่อในการตั้งค่า",
    },
    "btn_use": {
        "en": "Use",
        "sv": "Använd",
        "es": "Usar",
        "de": "Verwenden",
        "fr": "Utiliser",
        "zh": "使用",
        "ru": "Использовать",
        "th": "ใช้",
    },
    "btn_watch": {
        "en": "Watch",
        "sv": "Titta",
        "es": "Ver",
        "de": "Ansehen",
        "fr": "Regarder",
        "zh": "观看",
        "ru": "Смотреть",
        "th": "ดู",
    },
    "btn_play_channel": {
        "en": "Play channel",
        "sv": "Spela kanal",
        "es": "Reproducir canal",
        "de": "Kanal abspielen",
        "fr": "Lire la chaîne",
        "zh": "播放频道",
        "ru": "Воспроизвести канал",
        "th": "เล่นช่อง",
    },
    "btn_back_to_series": {
        "en": "Back to series",
        "sv": "Tillbaka till serier",
        "es": "Volver a la serie",
        "de": "Zurück zur Serie",
        "fr": "Retour à la série",
        "zh": "返回剧集",
        "ru": "Назад к сериалу",
        "th": "กลับไปยังซีรีส์",
    },
    "btn_clear_history": {
        "en": "Clear history",
        "sv": "Rensa historik",
        "es": "Borrar historial",
        "de": "Verlauf löschen",
        "fr": "Effacer l'historique",
        "zh": "清除历史",
        "ru": "Очистить историю",
        "th": "ล้างประวัติ",
    },
    "btn_grid": {
        "en": "Grid",
        "sv": "Rutnät",
        "es": "Cuadrícula",
        "de": "Raster",
        "fr": "Grille",
        "zh": "网格",
        "ru": "Сетка",
        "th": "ตาราง",
    },

    # ── Tooltips ───────────────────────────────────────────────────────────

    "tooltip_previous_channel": {
        "en": "Previous channel",
        "sv": "Föregående kanal",
        "es": "Canal anterior",
        "de": "Vorheriger Kanal",
        "fr": "Chaîne précédente",
        "zh": "上一个频道",
        "ru": "Предыдущий канал",
        "th": "ช่องก่อนหน้า",
    },
    "tooltip_next_channel": {
        "en": "Next channel",
        "sv": "Nästa kanal",
        "es": "Canal siguiente",
        "de": "Nächster Kanal",
        "fr": "Chaîne suivante",
        "zh": "下一个频道",
        "ru": "Следующий канал",
        "th": "ช่องถัดไป",
    },
    "tooltip_next_episode": {
        "en": "Next episode",
        "sv": "Nästa avsnitt",
        "es": "Episodio siguiente",
        "de": "Nächste Folge",
        "fr": "Épisode suivant",
        "zh": "下一集",
        "ru": "Следующий эпизод",
        "th": "ตอนถัดไป",
    },
    "tooltip_fullscreen": {
        "en": "Fullscreen",
        "sv": "Helskärm",
        "es": "Pantalla completa",
        "de": "Vollbild",
        "fr": "Plein écran",
        "zh": "全屏",
        "ru": "Полный экран",
        "th": "เต็มหน้าจอ",
    },
    "tooltip_exit_fullscreen": {
        "en": "Exit fullscreen",
        "sv": "Avsluta helskärm",
        "es": "Salir de pantalla completa",
        "de": "Vollbild beenden",
        "fr": "Quitter le plein écran",
        "zh": "退出全屏",
        "ru": "Выйти из полноэкранного режима",
        "th": "ออกจากโหมดเต็มหน้าจอ",
    },
    "tooltip_mute_unmute": {
        "en": "Mute / unmute",
        "sv": "Ljud av / på",
        "es": "Silenciar / activar sonido",
        "de": "Stumm / Ton an",
        "fr": "Couper / rétablir le son",
        "zh": "静音 / 取消静音",
        "ru": "Без звука / со звуком",
        "th": "ปิด / เปิดเสียง",
    },
    "tooltip_popout": {
        "en": "Pop out to a separate window",
        "sv": "Öppna i separat fönster",
        "es": "Abrir en una ventana separada",
        "de": "In separatem Fenster öffnen",
        "fr": "Ouvrir dans une fenêtre séparée",
        "zh": "弹出到独立窗口",
        "ru": "Открыть в отдельном окне",
        "th": "เปิดในหน้าต่างแยก",
    },
    "tooltip_popout_exit": {
        "en": "Return the player to the main window",
        "sv": "Återför spelaren till huvudfönstret",
        "es": "Devolver el reproductor a la ventana principal",
        "de": "Player zum Hauptfenster zurückholen",
        "fr": "Ramener le lecteur dans la fenêtre principale",
        "zh": "将播放器返回主窗口",
        "ru": "Вернуть плеер в главное окно",
        "th": "นำเครื่องเล่นกลับไปยังหน้าต่างหลัก",
    },
    "popout_title": {
        "en": "dopeIPTV — Player",
        "sv": "dopeIPTV — Spelare",
        "es": "dopeIPTV — Reproductor",
        "de": "dopeIPTV — Player",
        "fr": "dopeIPTV — Lecteur",
        "zh": "dopeIPTV — 播放器",
        "ru": "dopeIPTV — Плеер",
        "th": "dopeIPTV — เครื่องเล่น",
    },
    "popout_placeholder": {
        "en": "▶  Playing in a separate window\nClick to bring it back",
        "sv": "▶  Spelas i separat fönster\nKlicka för att ta tillbaka",
        "es": "▶  Reproduciendo en una ventana separada\nHaz clic para recuperarlo",
        "de": "▶  Wiedergabe in separatem Fenster\nKlicken zum Zurückholen",
        "fr": "▶  Lecture dans une fenêtre séparée\nCliquez pour la ramener",
        "zh": "▶  正在独立窗口中播放\n点击以取回",
        "ru": "▶  Воспроизведение в отдельном окне\nНажмите, чтобы вернуть",
        "th": "▶  กำลังเล่นในหน้าต่างแยก\nคลิกเพื่อนำกลับ",
    },
    "tooltip_stop_playback": {
        "en": "Stop playback",
        "sv": "Stoppa uppspelning",
        "es": "Detener reproducción",
        "de": "Wiedergabe stoppen",
        "fr": "Arrêter la lecture",
        "zh": "停止播放",
        "ru": "Остановить воспроизведение",
        "th": "หยุดเล่น",
    },
    "tooltip_pause_resume": {
        "en": "Pause / resume",
        "sv": "Pausa / återuppta",
        "es": "Pausa / reanudar",
        "de": "Pause / fortsetzen",
        "fr": "Pause / reprendre",
        "zh": "暂停 / 继续",
        "ru": "Пауза / продолжить",
        "th": "หยุดชั่วคราว / เล่นต่อ",
    },
    "tooltip_volume": {
        "en": "Volume",
        "sv": "Volym",
        "es": "Volumen",
        "de": "Lautstärke",
        "fr": "Volume",
        "zh": "音量",
        "ru": "Громкость",
        "th": "ระดับเสียง",
    },
    "tooltip_record": {
        "en": "Record",
        "sv": "Spela in",
        "es": "Grabar",
        "de": "Aufnehmen",
        "fr": "Enregistrer",
        "zh": "录制",
        "ru": "Запись",
        "th": "บันทึก",
    },
    "tooltip_timeshift": {
        "en": "Timeshift / catch-up",
        "sv": "Tidsförskjutning / catch-up",
        "es": "Timeshift / ponerse al día",
        "de": "Zeitversetzt / Nachholen",
        "fr": "Différé / rattrapage",
        "zh": "时移 / 回看",
        "ru": "Сдвиг времени / догоняющий просмотр",
        "th": "ย้อนเวลา / ดูย้อนหลัง",
    },
    "tooltip_audio_subs_aspect": {
        "en": "Audio / subtitles / aspect / buffer",
        "sv": "Ljud / undertexter / bildförhållande / buffert",
        "es": "Audio / subtítulos / aspecto / búfer",
        "de": "Audio / Untertitel / Seitenverhältnis / Puffer",
        "fr": "Audio / sous-titres / format / tampon",
        "zh": "音频 / 字幕 / 宽高比 / 缓冲",
        "ru": "Аудио / субтитры / соотношение / буфер",
        "th": "เสียง / คำบรรยาย / อัตราส่วน / บัฟเฟอร์",
    },
    "tooltip_reload_channels_epg": {
        "en": "Reload channels and EPG from server",
        "sv": "Ladda om kanaler och EPG från servern",
        "es": "Recargar canales y EPG del servidor",
        "de": "Kanäle und EPG vom Server neu laden",
        "fr": "Recharger les chaînes et l'EPG depuis le serveur",
        "zh": "从服务器重新加载频道和节目指南",
        "ru": "Перезагрузить каналы и EPG с сервера",
        "th": "โหลดช่องและ EPG จากเซิร์ฟเวอร์ใหม่",
    },
    "tooltip_play_in_mpv": {
        "en": "Play in mpv",
        "sv": "Spela i mpv",
        "es": "Reproducir en mpv",
        "de": "In mpv abspielen",
        "fr": "Lire dans mpv",
        "zh": "在 mpv 中播放",
        "ru": "Воспроизвести в mpv",
        "th": "เล่นใน mpv",
    },
    "tooltip_back_10s": {
        "en": "Back 10 seconds",
        "sv": "Tillbaka 10 sekunder",
        "es": "Retroceder 10 segundos",
        "de": "10 Sekunden zurück",
        "fr": "Reculer de 10 secondes",
        "zh": "后退10秒",
        "ru": "Назад 10 секунд",
        "th": "ย้อนกลับ 10 วินาที",
    },
    "tooltip_forward_30s": {
        "en": "Forward 30 seconds",
        "sv": "Framåt 30 sekunder",
        "es": "Avanzar 30 segundos",
        "de": "30 Sekunden vorwärts",
        "fr": "Avancer de 30 secondes",
        "zh": "前进30秒",
        "ru": "Вперёд 30 секунд",
        "th": "เดินหน้า 30 วินาที",
    },

    # ── Status messages ───────────────────────────────────────────────────

    "status_loading_channels": {
        "en": "Loading channels…",
        "sv": "Laddar kanaler…",
        "es": "Cargando canales…",
        "de": "Kanäle werden geladen…",
        "fr": "Chargement des chaînes…",
        "zh": "正在加载频道…",
        "ru": "Загрузка каналов…",
        "th": "กำลังโหลดช่อง…",
    },
    "status_loading_categories": {
        "en": "Loading categories…",
        "sv": "Laddar kategorier…",
        "es": "Cargando categorías…",
        "de": "Kategorien werden geladen…",
        "fr": "Chargement des catégories…",
        "zh": "正在加载分类…",
        "ru": "Загрузка категорий…",
        "th": "กำลังโหลดหมวดหมู่…",
    },
    "status_loading_content": {
        "en": "Loading content…",
        "sv": "Laddar innehåll…",
        "es": "Cargando contenido…",
        "de": "Inhalte werden geladen…",
        "fr": "Chargement du contenu…",
        "zh": "正在加载内容…",
        "ru": "Загрузка контента…",
        "th": "กำลังโหลดเนื้อหา…",
    },
    "status_loading_movies": {
        "en": "Loading movies…", "sv": "Laddar filmer…",
        "es": "Cargando películas…", "de": "Filme werden geladen…",
        "fr": "Chargement des films…", "zh": "正在加载电影…",
        "ru": "Загрузка фильмов…", "th": "กำลังโหลดหนัง…",
    },
    "status_loading_series": {
        "en": "Loading series…", "sv": "Laddar serier…",
        "es": "Cargando series…", "de": "Serien werden geladen…",
        "fr": "Chargement des séries…", "zh": "正在加载剧集…",
        "ru": "Загрузка сериалов…", "th": "กำลังโหลดซีรีส์…",
    },
    "status_loading_recent": {
        "en": "Loading recently added…", "sv": "Laddar senast tillagt…",
        "es": "Cargando añadidos recientes…",
        "de": "Kürzlich hinzugefügte werden geladen…",
        "fr": "Chargement des ajouts récents…", "zh": "正在加载最近添加…",
        "ru": "Загрузка недавних…", "th": "กำลังโหลดที่เพิ่มล่าสุด…",
    },
    "status_loading_episodes": {
        "en": "Loading episodes…",
        "sv": "Laddar avsnitt…",
        "es": "Cargando episodios…",
        "de": "Episoden werden geladen…",
        "fr": "Chargement des épisodes…",
        "zh": "正在加载剧集…",
        "ru": "Загрузка эпизодов…",
        "th": "กำลังโหลดตอน…",
    },
    "status_refreshing_playlist": {
        "en": "Refreshing playlist…",
        "sv": "Uppdaterar spellista…",
        "es": "Actualizando lista de reproducción…",
        "de": "Wiedergabeliste wird aktualisiert…",
        "fr": "Actualisation de la liste de lecture…",
        "zh": "正在刷新播放列表…",
        "ru": "Обновление плейлиста…",
        "th": "กำลังรีเฟรชเพลย์ลิสต์…",
    },
    "status_connecting": {
        "en": "Connecting to {name}…",
        "sv": "Ansluter till {name}…",
        "es": "Conectando a {name}…",
        "de": "Verbindung mit {name}…",
        "fr": "Connexion à {name}…",
        "zh": "正在连接 {name}…",
        "ru": "Подключение к {name}…",
        "th": "กำลังเชื่อมต่อกับ {name}…",
    },
    "status_loading_programme_guide": {
        "en": "Loading programme guide…",
        "sv": "Laddar programguide…",
        "es": "Cargando guía de programación…",
        "de": "Programmführer wird geladen…",
        "fr": "Chargement du guide des programmes…",
        "zh": "正在加载节目指南…",
        "ru": "Загрузка телепрограммы…",
        "th": "กำลังโหลดผังรายการ…",
    },
    "status_loading_programme_guide_pct": {
        "en": "Loading programme guide... {pct}%",
        "sv": "Laddar programguide... {pct}%",
        "es": "Cargando guía de programación... {pct}%",
        "de": "Programmführer wird geladen... {pct}%",
        "fr": "Chargement du guide des programmes... {pct}%",
        "zh": "正在加载节目指南... {pct}%",
        "ru": "Загрузка телепрограммы... {pct}%",
        "th": "กำลังโหลดผังรายการ... {pct}%",
    },
    "status_playing": {
        "en": "Playing: {title}",
        "sv": "Spelar: {title}",
        "es": "Reproduciendo: {title}",
        "de": "Wiedergabe: {title}",
        "fr": "Lecture : {title}",
        "zh": "正在播放：{title}",
        "ru": "Воспроизведение: {title}",
        "th": "กำลังเล่น: {title}",
    },
    "chan_entry": {
        "en": "Channel: {num}", "sv": "Kanal: {num}", "es": "Canal: {num}",
        "de": "Kanal: {num}", "fr": "Chaîne : {num}", "zh": "频道：{num}",
        "ru": "Канал: {num}", "th": "ช่อง: {num}",
    },
    "chan_not_found": {
        "en": "No channel #{num}", "sv": "Ingen kanal #{num}",
        "es": "Sin canal n.º {num}", "de": "Kein Kanal #{num}",
        "fr": "Aucune chaîne n° {num}", "zh": "无 {num} 号频道",
        "ru": "Нет канала №{num}", "th": "ไม่มีช่อง #{num}",
    },
    "status_no_favorites": {
        "en": "No favorites yet - right-click a channel in TV to add one.",
        "sv": "Inga favoriter ännu - högerklicka en kanal i TV för att lägga till.",
        "es": "Sin favoritos aún - haz clic derecho en un canal de TV para añadir.",
        "de": "Noch keine Favoriten - Rechtsklick auf einen TV-Kanal zum Hinzufügen.",
        "fr": "Pas encore de favoris - clic droit sur une chaîne TV pour en ajouter.",
        "zh": "暂无收藏 - 右键点击电视频道添加。",
        "ru": "Пока нет избранного - щёлкните правой кнопкой по каналу в ТВ.",
        "th": "ยังไม่มีรายการโปรด - คลิกขวาที่ช่องทีวีเพื่อเพิ่ม",
    },
    "status_no_history": {
        "en": "No watch history yet.",
        "sv": "Ingen visningshistorik ännu.",
        "es": "No hay historial de reproducción aún.",
        "de": "Noch kein Wiedergabeverlauf.",
        "fr": "Pas encore d'historique de visionnage.",
        "zh": "暂无观看历史。",
        "ru": "История просмотров пуста.",
        "th": "ยังไม่มีประวัติการรับชม",
    },
    "status_reconnecting": {
        "en": "Reconnecting…", "sv": "Återansluter…",
        "es": "Reconectando…", "de": "Neu verbinden…",
        "fr": "Reconnexion…", "zh": "正在重新连接…",
        "ru": "Переподключение…", "th": "กำลังเชื่อมต่อใหม่…",
    },
    "status_stream_dropped": {
        "en": "Live stream dropped — double-click to reconnect",
        "sv": "Live-streamen tappades — dubbelklicka för att återansluta",
        "es": "Transmisión en directo interrumpida — doble clic para reconectar",
        "de": "Live-Stream abgebrochen — Doppelklick zum Neuverbinden",
        "fr": "Flux en direct interrompu — double-cliquez pour reconnecter",
        "zh": "直播流已中断 — 双击重新连接",
        "ru": "Прямая трансляция прервана — двойной щелчок для переподключения",
        "th": "สตรีมสดหลุด — ดับเบิลคลิกเพื่อเชื่อมต่อใหม่",
    },
    "update_status": {
        "en": "Update available ({version})",
        "sv": "Uppdatering tillgänglig ({version})",
        "es": "Actualización disponible ({version})",
        "de": "Update verfügbar ({version})",
        "fr": "Mise à jour disponible ({version})",
        "zh": "有可用更新 ({version})",
        "ru": "Доступно обновление ({version})",
        "th": "มีอัปเดต ({version})",
    },
    "setting_deinterlace": {
        "en": "Deinterlace", "sv": "Avfläta",
        "es": "Desentrelazar", "de": "Deinterlacing",
        "fr": "Désentrelacement", "zh": "去隔行",
        "ru": "Деинтерлейсинг", "th": "ลบเส้นอินเทอร์เลซ",
    },
    "setting_sharpen": {
        "en": "Sharpen", "sv": "Skärpa",
        "es": "Nitidez", "de": "Schärfen",
        "fr": "Netteté", "zh": "锐化",
        "ru": "Резкость", "th": "เพิ่มความคมชัด",
    },
    "setting_tonemapping": {
        "en": "HDR tone-mapping", "sv": "HDR-tonemappning",
        "es": "Mapeo de tonos HDR", "de": "HDR-Tonemapping",
        "fr": "Tone-mapping HDR", "zh": "HDR 色调映射",
        "ru": "HDR-тонирование", "th": "การแมปโทน HDR",
    },
    "setting_hwdec": {
        "en": "Hardware decoding", "sv": "Hårdvaruavkodning",
        "es": "Decodificación por hardware", "de": "Hardware-Dekodierung",
        "fr": "Décodage matériel", "zh": "硬件解码",
        "ru": "Аппаратное декодирование", "th": "การถอดรหัสด้วยฮาร์ดแวร์",
    },
    "setting_hwdec_hint": {
        "en": "Software (CPU) decoding is the default and handles even 4K fine. "
              "Turn on hardware decoding to offload the GPU; if video then goes "
              "black or glitches (some drivers, e.g. nvidia-open, with "
              "subtitles), switch it back off.",
        "sv": "Mjukvaruavkodning (CPU) är standard och klarar även 4K bra. Slå "
              "på hårdvaruavkodning för att avlasta med GPU:n; om bilden då blir "
              "svart eller hackar (vissa drivrutiner, t.ex. nvidia-open, med "
              "subtitles), stäng av den igen.",
        "es": "La decodificación por software (CPU) es la predeterminada y "
              "maneja incluso 4K sin problemas. Activa la decodificación por "
              "hardware para descargar la GPU; si el vídeo se pone negro o falla "
              "(algunos controladores, p. ej. nvidia-open, con subtítulos), "
              "desactívala.",
        "de": "Software-Dekodierung (CPU) ist Standard und schafft auch 4K "
              "problemlos. Hardware-Dekodierung einschalten, um die GPU zu "
              "nutzen; wird das Bild dann schwarz oder fehlerhaft (manche "
              "Treiber, z. B. nvidia-open, mit Untertiteln), wieder ausschalten.",
        "fr": "Le décodage logiciel (CPU) est la valeur par défaut et gère même "
              "la 4K sans souci. Activez le décodage matériel pour soulager le "
              "GPU ; si la vidéo devient noire ou instable (certains pilotes, "
              "p. ex. nvidia-open, avec sous-titres), désactivez-le.",
        "zh": "软件（CPU）解码为默认，连 4K 也能流畅处理。开启硬件解码可减轻 "
              "GPU 负担；若视频随后变黑或出现故障（某些驱动，如 nvidia-open，"
              "在有字幕时），请将其关闭。",
        "ru": "Программное декодирование (CPU) используется по умолчанию и "
              "справляется даже с 4K. Включите аппаратное декодирование, чтобы "
              "разгрузить GPU; если видео чернеет или сбоит (некоторые драйверы, "
              "например nvidia-open, с субтитрами), выключите его обратно.",
        "th": "การถอดรหัสด้วยซอฟต์แวร์ (CPU) เป็นค่าเริ่มต้นและรองรับแม้กระทั่ง 4K "
              "ได้ดี เปิดการถอดรหัสด้วยฮาร์ดแวร์เพื่อลดภาระ GPU หากวิดีโอกลายเป็น "
              "สีดำหรือมีปัญหา (ไดรเวอร์บางตัว เช่น nvidia-open เมื่อมีซับไตเติล) "
              "ให้ปิดกลับ",
    },
    "option_hwdec_safe": {
        "en": "On - safe", "sv": "På - säker",
        "es": "Activada - segura",
        "de": "An - sicher",
        "fr": "Activé - sûr", "zh": "开启 - 安全",
        "ru": "Вкл. - безопасно",
        "th": "เปิด - ปลอดภัย",
    },
    "option_hwdec_direct": {
        "en": "On - direct (zero-copy)", "sv": "På - direkt (zero-copy)",
        "es": "Activada - directa (zero-copy)",
        "de": "An - direkt (Zero-Copy)",
        "fr": "Activé - direct (zero-copy)", "zh": "开启 - 直通（零拷贝）",
        "ru": "Вкл. - прямое (zero-copy)", "th": "เปิด - โดยตรง (zero-copy)",
    },
    "option_hwdec_off": {
        "en": "Off - software (CPU, recommended)",
        "sv": "Av - mjukvara (CPU, rekommenderas)",
        "es": "Desactivada - software (CPU, recomendado)",
        "de": "Aus - Software (CPU, empfohlen)",
        "fr": "Désactivé - logiciel (CPU, recommandé)",
        "zh": "关闭 - 软件解码（CPU，推荐）",
        "ru": "Выкл. - программно (CPU, рекомендуется)",
        "th": "ปิด - ซอฟต์แวร์ (CPU, แนะนำ)",
    },
    "option_off": {
        "en": "Off", "sv": "Av", "es": "Desactivado", "de": "Aus",
        "fr": "Désactivé", "zh": "关闭", "ru": "Выкл.", "th": "ปิด",
    },
    "option_low": {
        "en": "Low", "sv": "Låg", "es": "Baja", "de": "Niedrig",
        "fr": "Faible", "zh": "低", "ru": "Низкая", "th": "ต่ำ",
    },
    "option_high": {
        "en": "High", "sv": "Hög", "es": "Alta", "de": "Hoch",
        "fr": "Élevée", "zh": "高", "ru": "Высокая", "th": "สูง",
    },
    "option_tonemap_auto": {
        "en": "Auto", "sv": "Auto", "es": "Automático", "de": "Automatisch",
        "fr": "Auto", "zh": "自动", "ru": "Авто", "th": "อัตโนมัติ",
    },
    "option_tonemap_clip": {
        "en": "Clip", "sv": "Klipp", "es": "Recorte", "de": "Clipping",
        "fr": "Écrêtage", "zh": "裁剪", "ru": "Обрезка", "th": "ตัด",
    },
    "option_on": {
        "en": "On", "sv": "På", "es": "Activado", "de": "Ein",
        "fr": "Activé", "zh": "开启", "ru": "Вкл.", "th": "เปิด",
    },
    "opt_video": {
        "en": "Video", "sv": "Video", "es": "Vídeo", "de": "Video",
        "fr": "Vidéo", "zh": "视频", "ru": "Видео", "th": "วิดีโอ",
    },
    "sec_playback": {
        "en": "Playback", "sv": "Uppspelning", "es": "Reproducción",
        "de": "Wiedergabe", "fr": "Lecture", "zh": "播放",
        "ru": "Воспроизведение", "th": "การเล่น",
    },
    "sec_audio_subs": {
        "en": "Audio & subtitles", "sv": "Ljud & undertexter",
        "es": "Audio y subtítulos", "de": "Audio & Untertitel",
        "fr": "Audio et sous-titres", "zh": "音频与字幕",
        "ru": "Звук и субтитры", "th": "เสียงและคำบรรยาย",
    },
    "sec_video": {
        "en": "Video", "sv": "Video", "es": "Vídeo", "de": "Video",
        "fr": "Vidéo", "zh": "视频", "ru": "Видео", "th": "วิดีโอ",
    },
    "sec_network": {
        "en": "Network & timing", "sv": "Nätverk & timing",
        "es": "Red y sincronización", "de": "Netzwerk & Timing",
        "fr": "Réseau et synchronisation", "zh": "网络与时序",
        "ru": "Сеть и тайминг", "th": "เครือข่ายและการจับเวลา",
    },
    "sec_guide": {
        "en": "Guide", "sv": "Guide", "es": "Guía", "de": "Programm",
        "fr": "Guide", "zh": "节目指南", "ru": "Программа", "th": "ผังรายการ",
    },
    "epg_search_title": {
        "en": "Search the guide", "sv": "Sök i guiden",
        "es": "Buscar en la guía", "de": "Programm durchsuchen",
        "fr": "Rechercher dans le guide", "zh": "搜索节目指南",
        "ru": "Поиск в программе", "th": "ค้นหาในผังรายการ",
    },
    "epg_search_btn": {
        "en": "Search", "sv": "Sök", "es": "Buscar", "de": "Suchen",
        "fr": "Rechercher", "zh": "搜索", "ru": "Поиск", "th": "ค้นหา",
    },
    "epg_search_placeholder": {
        "en": "Search programmes (e.g. Formula 1)",
        "sv": "Sök program (t.ex. Formel 1)",
        "es": "Buscar programas (p. ej. Fórmula 1)",
        "de": "Sendungen suchen (z. B. Formel 1)",
        "fr": "Rechercher des programmes (p. ex. Formule 1)",
        "zh": "搜索节目（例如 F1）",
        "ru": "Поиск передач (напр. Формула 1)",
        "th": "ค้นหารายการ (เช่น ฟอร์มูล่าวัน)",
    },
    "epg_search_hint": {
        "en": "Type at least 2 characters to search this week's guide.",
        "sv": "Skriv minst 2 tecken för att söka i veckans guide.",
        "es": "Escribe al menos 2 caracteres para buscar en la guía de esta semana.",
        "de": "Gib mindestens 2 Zeichen ein, um im Programm dieser Woche zu suchen.",
        "fr": "Saisissez au moins 2 caractères pour rechercher dans le guide de la semaine.",
        "zh": "输入至少 2 个字符以搜索本周节目。",
        "ru": "Введите не менее 2 символов для поиска в программе на неделю.",
        "th": "พิมพ์อย่างน้อย 2 ตัวอักษรเพื่อค้นหาผังรายการสัปดาห์นี้",
    },
    "epg_searching": {
        "en": "Searching…", "sv": "Söker…", "es": "Buscando…",
        "de": "Suche läuft…", "fr": "Recherche…", "zh": "正在搜索…",
        "ru": "Поиск…", "th": "กำลังค้นหา…",
    },
    "epg_search_count": {
        "en": "{n} matches", "sv": "{n} träffar", "es": "{n} resultados",
        "de": "{n} Treffer", "fr": "{n} résultats", "zh": "{n} 个结果",
        "ru": "Найдено: {n}", "th": "{n} รายการ",
    },
    "epg_search_none": {
        "en": "No matches this week.", "sv": "Inga träffar den här veckan.",
        "es": "Sin resultados esta semana.",
        "de": "Keine Treffer diese Woche.",
        "fr": "Aucun résultat cette semaine.", "zh": "本周无匹配结果。",
        "ru": "На этой неделе совпадений нет.",
        "th": "ไม่พบรายการในสัปดาห์นี้",
    },
    "status_stream_error": {
        "en": "Stream error: {msg}",
        "sv": "Strömfel: {msg}",
        "es": "Error de transmisión: {msg}",
        "de": "Stream-Fehler: {msg}",
        "fr": "Erreur de flux : {msg}",
        "zh": "流错误：{msg}",
        "ru": "Ошибка потока: {msg}",
        "th": "ข้อผิดพลาดสตรีม: {msg}",
    },
    "status_checking_stream": {
        "en": "Stream failed — checking why…",
        "sv": "Strömmen misslyckades — kollar varför…",
        "es": "Fallo de transmisión — comprobando por qué…",
        "de": "Stream fehlgeschlagen — Ursache wird geprüft…",
        "fr": "Échec du flux — vérification de la cause…",
        "zh": "流播放失败 — 正在检查原因…",
        "ru": "Поток не работает — выясняем причину…",
        "th": "สตรีมล้มเหลว — กำลังตรวจสอบสาเหตุ…",
    },
    "diag_account_status": {
        "en": "Your account is {status} — contact your provider",
        "sv": "Ditt konto är {status} — kontakta din leverantör",
        "es": "Tu cuenta está {status} — contacta con tu proveedor",
        "de": "Dein Konto ist {status} — wende dich an deinen Anbieter",
        "fr": "Votre compte est {status} — contactez votre fournisseur",
        "zh": "您的账户状态为 {status} — 请联系您的提供商",
        "ru": "Ваш аккаунт: {status} — обратитесь к провайдеру",
        "th": "บัญชีของคุณ {status} — ติดต่อผู้ให้บริการ",
    },
    "diag_expired": {
        "en": "Your subscription has expired — renew it with your provider",
        "sv": "Ditt abonnemang har gått ut — förnya hos din leverantör",
        "es": "Tu suscripción ha caducado — renuévala con tu proveedor",
        "de": "Dein Abo ist abgelaufen — verlängere es bei deinem Anbieter",
        "fr": "Votre abonnement a expiré — renouvelez-le auprès du fournisseur",
        "zh": "您的订阅已过期 — 请向提供商续订",
        "ru": "Ваша подписка истекла — продлите её у провайдера",
        "th": "การสมัครสมาชิกหมดอายุ — ต่ออายุกับผู้ให้บริการ",
    },
    "diag_conn_limit": {
        "en": "All {active}/{maxc} connections are in use — close the stream "
              "on your other device or app",
        "sv": "Alla {active}/{maxc} anslutningar används — stäng strömmen på "
              "din andra enhet eller app",
        "es": "Todas las conexiones {active}/{maxc} están en uso — cierra la "
              "transmisión en tu otro dispositivo o app",
        "de": "Alle {active}/{maxc} Verbindungen sind belegt — beende den "
              "Stream auf deinem anderen Gerät oder in der anderen App",
        "fr": "Toutes les connexions {active}/{maxc} sont utilisées — fermez le "
              "flux sur votre autre appareil ou application",
        "zh": "{active}/{maxc} 个连接已全部占用 — 请在您的其他设备或应用上关闭该流",
        "ru": "Заняты все подключения {active}/{maxc} — закройте поток на "
              "другом устройстве или в другом приложении",
        "th": "ใช้การเชื่อมต่อครบ {active}/{maxc} แล้ว — ปิดสตรีมบนอุปกรณ์หรือแอปอื่น",
    },
    "diag_timeout": {
        "en": "The provider didn't respond (timeout) — its server is likely "
              "down or overloaded",
        "sv": "Leverantören svarade inte (timeout) — servern är troligen nere "
              "eller överbelastad",
        "es": "El proveedor no respondió (timeout) — su servidor está caído o "
              "sobrecargado",
        "de": "Der Anbieter hat nicht geantwortet (Timeout) — sein Server ist "
              "vermutlich ausgefallen oder überlastet",
        "fr": "Le fournisseur n'a pas répondu (timeout) — son serveur est "
              "probablement hors service ou surchargé",
        "zh": "提供商未响应（超时）— 其服务器可能已宕机或过载",
        "ru": "Провайдер не ответил (тайм-аут) — его сервер, вероятно, недоступен "
              "или перегружен",
        "th": "ผู้ให้บริการไม่ตอบสนอง (หมดเวลา) — เซิร์ฟเวอร์อาจล่มหรือโหลดเกิน",
    },
    "diag_unreachable": {
        "en": "Can't reach the provider — its server is down or there's a "
              "network problem",
        "sv": "Når inte leverantören — servern är nere eller så är det ett "
              "nätverksproblem",
        "es": "No se puede conectar con el proveedor — su servidor está caído o "
              "hay un problema de red",
        "de": "Anbieter nicht erreichbar — sein Server ist ausgefallen oder es "
              "gibt ein Netzwerkproblem",
        "fr": "Impossible de joindre le fournisseur — son serveur est hors "
              "service ou il y a un problème réseau",
        "zh": "无法连接到提供商 — 其服务器已宕机或存在网络问题",
        "ru": "Не удаётся связаться с провайдером — его сервер недоступен или "
              "есть проблема с сетью",
        "th": "เชื่อมต่อผู้ให้บริการไม่ได้ — เซิร์ฟเวอร์ล่มหรือมีปัญหาเครือข่าย",
    },
    "diag_forbidden": {
        "en": "The provider refused the stream (HTTP {code}) — account blocked, "
              "or too many connections",
        "sv": "Leverantören nekade strömmen (HTTP {code}) — kontot spärrat "
              "eller för många anslutningar",
        "es": "El proveedor rechazó la transmisión (HTTP {code}) — cuenta "
              "bloqueada o demasiadas conexiones",
        "de": "Der Anbieter hat den Stream abgelehnt (HTTP {code}) — Konto "
              "gesperrt oder zu viele Verbindungen",
        "fr": "Le fournisseur a refusé le flux (HTTP {code}) — compte bloqué "
              "ou trop de connexions",
        "zh": "提供商拒绝了该流（HTTP {code}）— 账户被封或连接过多",
        "ru": "Провайдер отклонил поток (HTTP {code}) — аккаунт заблокирован "
              "или слишком много подключений",
        "th": "ผู้ให้บริการปฏิเสธสตรีม (HTTP {code}) — บัญชีถูกบล็อกหรือเชื่อมต่อมากเกินไป",
    },
    "diag_not_found": {
        "en": "The provider doesn't have this stream (HTTP 404) — try "
              "refreshing the playlist",
        "sv": "Leverantören har inte den här strömmen (HTTP 404) — prova att "
              "uppdatera spellistan",
        "es": "El proveedor no tiene esta transmisión (HTTP 404) — prueba a "
              "actualizar la lista",
        "de": "Der Anbieter hat diesen Stream nicht (HTTP 404) — aktualisiere "
              "die Playlist",
        "fr": "Le fournisseur n'a pas ce flux (HTTP 404) — essayez d'actualiser "
              "la liste",
        "zh": "提供商没有此流（HTTP 404）— 请尝试刷新播放列表",
        "ru": "У провайдера нет этого потока (HTTP 404) — попробуйте обновить "
              "плейлист",
        "th": "ผู้ให้บริการไม่มีสตรีมนี้ (HTTP 404) — ลองรีเฟรชเพลย์ลิสต์",
    },
    "diag_blocked": {
        "en": "The provider blocked this stream (HTTP {code}) — usually "
              "anti-VPN/re-streaming, a connection limit, or a region/device "
              "block. Turn off any VPN and check with your provider.",
        "sv": "Leverantören blockerade strömmen (HTTP {code}) — oftast "
              "anti-VPN/restream, en anslutningsgräns eller ett region-/"
              "enhetsblock. Stäng av ev. VPN och kolla med din leverantör.",
        "es": "El proveedor bloqueó esta transmisión (HTTP {code}) — "
              "normalmente anti-VPN/re-streaming, un límite de conexiones o un "
              "bloqueo de región/dispositivo. Desactiva la VPN y consulta con "
              "tu proveedor.",
        "de": "Der Anbieter hat diesen Stream blockiert (HTTP {code}) — meist "
              "Anti-VPN/Re-Streaming, ein Verbindungslimit oder eine Region-/"
              "Gerätesperre. Schalte ein VPN aus und frage deinen Anbieter.",
        "fr": "Le fournisseur a bloqué ce flux (HTTP {code}) — souvent "
              "anti-VPN/re-streaming, une limite de connexions ou un blocage "
              "régional/d'appareil. Désactivez tout VPN et contactez le "
              "fournisseur.",
        "zh": "提供商屏蔽了此流（HTTP {code}）— 通常是反 VPN/转播、连接数限制或"
              "地区/设备封锁。请关闭 VPN 并咨询您的提供商。",
        "ru": "Провайдер заблокировал этот поток (HTTP {code}) — обычно "
              "анти-VPN/ре-стриминг, лимит подключений или блокировка по "
              "региону/устройству. Отключите VPN и уточните у провайдера.",
        "th": "ผู้ให้บริการบล็อกสตรีมนี้ (HTTP {code}) — มักเป็นการป้องกัน VPN/รีสตรีม "
              "การจำกัดการเชื่อมต่อ หรือการบล็อกตามภูมิภาค/อุปกรณ์ ปิด VPN แล้วสอบถามผู้ให้บริการ",
    },
    "diag_http_error": {
        "en": "The provider returned an error (HTTP {code}) — its server is "
              "having trouble",
        "sv": "Leverantören svarade med ett fel (HTTP {code}) — servern har "
              "problem",
        "es": "El proveedor devolvió un error (HTTP {code}) — su servidor tiene "
              "problemas",
        "de": "Der Anbieter hat einen Fehler zurückgegeben (HTTP {code}) — sein "
              "Server hat Probleme",
        "fr": "Le fournisseur a renvoyé une erreur (HTTP {code}) — son serveur "
              "rencontre des problèmes",
        "zh": "提供商返回错误（HTTP {code}）— 其服务器出现故障",
        "ru": "Провайдер вернул ошибку (HTTP {code}) — у его сервера проблемы",
        "th": "ผู้ให้บริการส่งข้อผิดพลาด (HTTP {code}) — เซิร์ฟเวอร์มีปัญหา",
    },
    "diag_http_ok_no_play": {
        "en": "The provider is serving the stream but it wouldn't play — likely "
              "an unsupported format for this channel",
        "sv": "Leverantören skickar strömmen men den gick inte att spela — "
              "troligen ett format som inte stöds för den här kanalen",
        "es": "El proveedor envía la transmisión pero no se reprodujo — "
              "probablemente un formato no compatible para este canal",
        "de": "Der Anbieter liefert den Stream, aber er ließ sich nicht "
              "abspielen — wahrscheinlich ein nicht unterstütztes Format",
        "fr": "Le fournisseur diffuse le flux mais il ne se lit pas — "
              "probablement un format non pris en charge pour cette chaîne",
        "zh": "提供商正在提供该流但无法播放 — 该频道可能是不受支持的格式",
        "ru": "Провайдер отдаёт поток, но он не воспроизводится — вероятно, "
              "неподдерживаемый формат для этого канала",
        "th": "ผู้ให้บริการส่งสตรีมแต่เล่นไม่ได้ — อาจเป็นรูปแบบที่ไม่รองรับสำหรับช่องนี้",
    },
    "diag_generic": {
        "en": "The stream couldn't be reached — the provider may be down",
        "sv": "Strömmen kunde inte nås — leverantören kan vara nere",
        "es": "No se pudo acceder a la transmisión — el proveedor puede estar "
              "caído",
        "de": "Der Stream war nicht erreichbar — der Anbieter ist evtl. offline",
        "fr": "Le flux est inaccessible — le fournisseur est peut-être hors "
              "service",
        "zh": "无法访问该流 — 提供商可能已宕机",
        "ru": "Не удалось получить поток — возможно, провайдер недоступен",
        "th": "เข้าถึงสตรีมไม่ได้ — ผู้ให้บริการอาจล่ม",
    },
    "status_player_not_found": {
        "en": "Player not found",
        "sv": "Spelaren hittades inte",
        "es": "Reproductor no encontrado",
        "de": "Player nicht gefunden",
        "fr": "Lecteur introuvable",
        "zh": "未找到播放器",
        "ru": "Плеер не найден",
        "th": "ไม่พบเครื่องเล่น",
    },
    "status_player_not_found_msg": {
        "en": "{name} was not found. Install it and try again.",
        "sv": "{name} hittades inte. Installera och försök igen.",
        "es": "No se encontró {name}. Instálalo e inténtalo de nuevo.",
        "de": "{name} wurde nicht gefunden. Bitte installieren und erneut versuchen.",
        "fr": "{name} n'a pas été trouvé. Installez-le et réessayez.",
        "zh": "未找到 {name}。请安装后重试。",
        "ru": "{name} не найден. Установите и попробуйте снова.",
        "th": "ไม่พบ {name} กรุณาติดตั้งแล้วลองอีกครั้ง",
    },
    "embedded_gl_failed": {
        "en": "In-app video isn't available on this system's graphics "
              "(often a virtual machine without GPU acceleration). "
              "You can still open channels in an external player.",
        "sv": "Inbäddad video stöds inte av den här datorns grafik "
              "(ofta en virtuell maskin utan GPU-acceleration). "
              "Du kan fortfarande öppna kanaler i en extern spelare.",
        "es": "El vídeo integrado no está disponible con los gráficos de "
              "este sistema (a menudo una máquina virtual sin aceleración "
              "por GPU). Aún puedes abrir canales en un reproductor externo.",
        "de": "In-App-Video wird von der Grafik dieses Systems nicht "
              "unterstützt (oft eine virtuelle Maschine ohne "
              "GPU-Beschleunigung). Du kannst Kanäle weiterhin in einem "
              "externen Player öffnen.",
        "fr": "La vidéo intégrée n'est pas disponible avec les graphismes de "
              "ce système (souvent une machine virtuelle sans accélération "
              "GPU). Vous pouvez toujours ouvrir les chaînes dans un lecteur "
              "externe.",
        "zh": "此系统的显卡不支持应用内视频（通常是没有 GPU 加速的虚拟机）。"
              "你仍可以在外部播放器中打开频道。",
        "ru": "Встроенное видео недоступно на графике этой системы "
              "(часто это виртуальная машина без ускорения GPU). "
              "Вы всё ещё можете открывать каналы во внешнем плеере.",
        "th": "วิดีโอในแอปใช้งานไม่ได้กับกราฟิกของระบบนี้ "
              "(มักเป็นเครื่องเสมือนที่ไม่มีการเร่ง GPU) "
              "คุณยังเปิดช่องในเครื่องเล่นภายนอกได้",
    },

    # ── Search ────────────────────────────────────────────────────────────

    "search_placeholder": {
        "en": "Search channels, movies or series…",
        "sv": "Sök kanaler, filmer eller serier…",
        "es": "Buscar canales, películas o series…",
        "de": "Kanäle, Filme oder Serien suchen…",
        "fr": "Rechercher chaînes, films ou séries…",
        "zh": "搜索频道、电影或剧集…",
        "ru": "Поиск каналов, фильмов или сериалов…",
        "th": "ค้นหาช่อง, ภาพยนตร์หรือซีรีส์…",
    },
    "search_filter_channels": {
        "en": "Filter channels…",
        "sv": "Filtrera kanaler…",
        "es": "Filtrar canales…",
        "de": "Kanäle filtern…",
        "fr": "Filtrer les chaînes…",
        "zh": "筛选频道…",
        "ru": "Фильтр каналов…",
        "th": "กรองช่อง…",
    },

    # ── Detail panel / metadata labels ────────────────────────────────────

    "detail_genre": {
        "en": "Genre",
        "sv": "Genre",
        "es": "Género",
        "de": "Genre",
        "fr": "Genre",
        "zh": "类型",
        "ru": "Жанр",
        "th": "ประเภท",
    },
    "detail_director": {
        "en": "Director",
        "sv": "Regissör",
        "es": "Director",
        "de": "Regisseur",
        "fr": "Réalisateur",
        "zh": "导演",
        "ru": "Режиссёр",
        "th": "ผู้กำกับ",
    },
    "detail_released": {
        "en": "Released",
        "sv": "Utgiven",
        "es": "Lanzamiento",
        "de": "Veröffentlicht",
        "fr": "Sorti",
        "zh": "上映",
        "ru": "Дата выхода",
        "th": "วันที่ออกฉาย",
    },
    "detail_duration": {
        "en": "Duration",
        "sv": "Längd",
        "es": "Duración",
        "de": "Dauer",
        "fr": "Durée",
        "zh": "时长",
        "ru": "Длительность",
        "th": "ระยะเวลา",
    },
    "detail_rating": {
        "en": "Rating",
        "sv": "Betyg",
        "es": "Calificación",
        "de": "Bewertung",
        "fr": "Note",
        "zh": "评分",
        "ru": "Рейтинг",
        "th": "คะแนน",
    },
    "detail_cast": {
        "en": "Cast",
        "sv": "Skådespelare",
        "es": "Reparto",
        "de": "Besetzung",
        "fr": "Distribution",
        "zh": "演员",
        "ru": "Актёры",
        "th": "นักแสดง",
    },
    "detail_no_info": {
        "en": "No further information available.",
        "sv": "Ingen ytterligare information tillgänglig.",
        "es": "No hay más información disponible.",
        "de": "Keine weiteren Informationen verfügbar.",
        "fr": "Aucune information supplémentaire disponible.",
        "zh": "暂无更多信息。",
        "ru": "Дополнительная информация недоступна.",
        "th": "ไม่มีข้อมูลเพิ่มเติม",
    },
    "detail_loading_info": {
        "en": "Loading information…",
        "sv": "Laddar information…",
        "es": "Cargando información…",
        "de": "Informationen werden geladen…",
        "fr": "Chargement des informations…",
        "zh": "正在加载信息…",
        "ru": "Загрузка информации…",
        "th": "กำลังโหลดข้อมูล…",
    },
    "detail_select_something": {
        "en": "Select something from the list",
        "sv": "Välj något från listan",
        "es": "Selecciona algo de la lista",
        "de": "Wähle etwas aus der Liste",
        "fr": "Sélectionnez un élément dans la liste",
        "zh": "请从列表中选择",
        "ru": "Выберите что-нибудь из списка",
        "th": "เลือกบางอย่างจากรายการ",
    },
    "detail_select_channel": {
        "en": "Select a channel",
        "sv": "Välj en kanal",
        "es": "Selecciona un canal",
        "de": "Wähle einen Kanal",
        "fr": "Sélectionnez une chaîne",
        "zh": "请选择一个频道",
        "ru": "Выберите канал",
        "th": "เลือกช่อง",
    },

    # ── Recording ─────────────────────────────────────────────────────────

    "setting_check_updates": {
        "en": "Check for updates on startup",
        "sv": "Sök efter uppdateringar vid start",
        "es": "Buscar actualizaciones al iniciar",
        "de": "Beim Start nach Updates suchen",
        "fr": "Rechercher les mises à jour au démarrage",
        "zh": "启动时检查更新",
        "ru": "Проверять обновления при запуске",
        "th": "ตรวจหาการอัปเดตเมื่อเริ่มต้น",
    },
    "setting_force_x11": {
        "en": "Run via X11 backend (needs restart)",
        "sv": "Kör via X11-backend (kräver omstart)",
        "es": "Ejecutar con backend X11 (requiere reinicio)",
        "de": "Über X11-Backend ausführen (Neustart nötig)",
        "fr": "Utiliser le backend X11 (redémarrage requis)",
        "zh": "通过 X11 后端运行（需重启）",
        "ru": "Запускать через X11 (нужен перезапуск)",
        "th": "รันผ่านแบ็กเอนด์ X11 (ต้องรีสตาร์ต)",
    },
    "setting_force_x11_hint": {
        "en": "Wayland only: run under XWayland so the pop-out player window "
              "can stay always-on-top. May look slightly softer on fractional "
              "HiDPI scaling.",
        "sv": "Endast Wayland: kör under XWayland så att det utpoppade "
              "spelarfönstret kan ligga alltid överst. Kan se något mjukare ut "
              "vid fraktionell HiDPI-skalning.",
        "es": "Solo Wayland: ejecuta bajo XWayland para que la ventana flotante "
              "del reproductor pueda estar siempre visible. Puede verse algo "
              "borroso con escala HiDPI fraccionada.",
        "de": "Nur Wayland: läuft unter XWayland, damit das ausgeklappte "
              "Player-Fenster immer im Vordergrund bleiben kann. Kann bei "
              "fraktionaler HiDPI-Skalierung etwas weicher wirken.",
        "fr": "Wayland uniquement : exécute sous XWayland pour que la fenêtre "
              "détachée du lecteur puisse rester toujours au premier plan. Peut "
              "paraître légèrement flou en mise à l'échelle HiDPI fractionnaire.",
        "zh": "仅 Wayland：在 XWayland 下运行，使弹出的播放器窗口可以始终置顶。"
              "在分数 HiDPI 缩放下可能略微模糊。",
        "ru": "Только Wayland: запуск под XWayland, чтобы отделённое окно плеера "
              "могло оставаться поверх всех окон. При дробном HiDPI-масштабе "
              "может выглядеть чуть мягче.",
        "th": "เฉพาะ Wayland: รันภายใต้ XWayland เพื่อให้หน้าต่างเครื่องเล่นที่แยกออกมา "
              "อยู่บนสุดเสมอได้ อาจดูนุ่มขึ้นเล็กน้อยเมื่อสเกล HiDPI แบบเศษส่วน",
    },
    "ext_play_title": {
        "en": "Open external player?",
        "sv": "Öppna extern spelare?",
        "es": "¿Abrir reproductor externo?",
        "de": "Externen Player öffnen?",
        "fr": "Ouvrir le lecteur externe ?",
        "zh": "打开外部播放器？",
        "ru": "Открыть внешний плеер?",
        "th": "เปิดโปรแกรมเล่นภายนอกไหม?",
    },
    "ext_play_body": {
        "en": "Something is playing in the mini player. Opening an external "
              "player pulls a second stream from the provider, which many "
              "accounts don't allow. Stop the mini player first?",
        "sv": "Något spelas i minispelaren. Att öppna en extern spelare hämtar "
              "en andra stream från leverantören, vilket många konton inte "
              "tillåter. Stoppa minispelaren först?",
        "es": "Algo se reproduce en el mini reproductor. Abrir uno externo "
              "abre una segunda conexión con el proveedor, que muchas cuentas "
              "no permiten. ¿Detener el mini reproductor primero?",
        "de": "Im Mini-Player läuft etwas. Ein externer Player öffnet eine "
              "zweite Verbindung zum Anbieter, was viele Konten nicht "
              "erlauben. Mini-Player zuerst stoppen?",
        "fr": "Quelque chose est en lecture dans le mini-lecteur. Ouvrir un "
              "lecteur externe crée une seconde connexion au fournisseur, que "
              "beaucoup de comptes interdisent. Arrêter le mini-lecteur ?",
        "zh": "迷你播放器正在播放。打开外部播放器会向服务商建立第二条连接，"
              "许多账户不允许这样。要先停止迷你播放器吗？",
        "ru": "В мини-плеере что-то воспроизводится. Внешний плеер откроет "
              "второе соединение с провайдером, что многие аккаунты запрещают. "
              "Остановить мини-плеер сначала?",
        "th": "มีบางอย่างกำลังเล่นในมินิเพลเยอร์ การเปิดโปรแกรมเล่นภายนอกจะดึงสตรีมที่สอง "
              "จากผู้ให้บริการ ซึ่งหลายบัญชีไม่อนุญาต หยุดมินิเพลเยอร์ก่อนไหม?",
    },
    "ext_play_stop_open": {
        "en": "Stop and open externally",
        "sv": "Stoppa och öppna externt",
        "es": "Detener y abrir externo",
        "de": "Stoppen und extern öffnen",
        "fr": "Arrêter et ouvrir",
        "zh": "停止并外部打开",
        "ru": "Остановить и открыть",
        "th": "หยุดแล้วเปิดภายนอก",
    },
    "ext_play_keep_open": {
        "en": "Open anyway (2nd connection)",
        "sv": "Öppna ändå (2:a anslutning)",
        "es": "Abrir igualmente (2.ª conexión)",
        "de": "Trotzdem öffnen (2. Verbindung)",
        "fr": "Ouvrir quand même (2e connexion)",
        "zh": "仍然打开（第二条连接）",
        "ru": "Всё равно открыть (2-е соединение)",
        "th": "เปิดต่อไป (การเชื่อมต่อที่ 2)",
    },
    "rec_record_programme": {
        "en": "Record this programme",
        "sv": "Spela in det här programmet",
        "es": "Grabar este programa",
        "de": "Diese Sendung aufnehmen",
        "fr": "Enregistrer cette émission",
        "zh": "录制此节目",
        "ru": "Записать эту передачу",
        "th": "บันทึกรายการนี้",
    },
    "rec_record": {
        "en": "Record",
        "sv": "Spela in",
        "es": "Grabar",
        "de": "Aufnehmen",
        "fr": "Enregistrer",
        "zh": "录制",
        "ru": "Записать",
        "th": "บันทึก",
    },
    "rec_stop_recording": {
        "en": "Stop recording",
        "sv": "Stoppa inspelning",
        "es": "Detener grabación",
        "de": "Aufnahme stoppen",
        "fr": "Arrêter l'enregistrement",
        "zh": "停止录制",
        "ru": "Остановить запись",
        "th": "หยุดบันทึก",
    },
    "rec_all_recordings": {
        "en": "All recordings",
        "sv": "Alla inspelningar",
        "es": "Todas las grabaciones",
        "de": "Alle Aufnahmen",
        "fr": "Tous les enregistrements",
        "zh": "所有录制",
        "ru": "Все записи",
        "th": "การบันทึกทั้งหมด",
    },
    "rec_active_scheduled": {
        "en": "Active & scheduled",
        "sv": "Aktiva och schemalagda",
        "es": "Activas y programadas",
        "de": "Aktive und geplante",
        "fr": "Actifs et programmés",
        "zh": "进行中和已计划",
        "ru": "Активные и запланированные",
        "th": "กำลังดำเนินการและตามกำหนด",
    },
    "rec_upcoming": {
        "en": "Upcoming",
        "sv": "Kommande",
        "es": "Próximamente",
        "de": "Geplant",
        "fr": "À venir",
        "zh": "即将开始",
        "ru": "Предстоящие",
        "th": "กำลังจะมาถึง",
    },
    "rec_status_recording": {
        "en": "Recording",
        "sv": "Inspelning",
        "es": "Grabando",
        "de": "Aufnahme",
        "fr": "Enregistrement",
        "zh": "录制中",
        "ru": "Запись",
        "th": "กำลังบันทึก",
    },
    "rec_status_scheduled": {
        "en": "Scheduled",
        "sv": "Schemalagd",
        "es": "Programada",
        "de": "Geplant",
        "fr": "Programmé",
        "zh": "已计划",
        "ru": "Запланировано",
        "th": "ตามกำหนด",
    },
    "rec_status_done": {
        "en": "Done",
        "sv": "Klar",
        "es": "Completada",
        "de": "Fertig",
        "fr": "Terminé",
        "zh": "完成",
        "ru": "Готово",
        "th": "เสร็จสิ้น",
    },
    "rec_status_failed": {
        "en": "Failed",
        "sv": "Misslyckad",
        "es": "Fallida",
        "de": "Fehlgeschlagen",
        "fr": "Échoué",
        "zh": "失败",
        "ru": "Ошибка",
        "th": "ล้มเหลว",
    },
    "rec_status_cancelled": {
        "en": "Cancelled",
        "sv": "Avbruten",
        "es": "Cancelada",
        "de": "Abgebrochen",
        "fr": "Annulé",
        "zh": "已取消",
        "ru": "Отменено",
        "th": "ยกเลิกแล้ว",
    },
    "rec_record_now_until_stopped": {
        "en": "Record now - until stopped",
        "sv": "Spela in nu - tills stoppad",
        "es": "Grabar ahora - hasta detener",
        "de": "Jetzt aufnehmen - bis gestoppt",
        "fr": "Enregistrer maintenant - jusqu'à l'arrêt",
        "zh": "立即录制 - 直到手动停止",
        "ru": "Записать сейчас - до остановки",
        "th": "บันทึกตอนนี้ - จนกว่าจะหยุด",
    },
    "rec_record_now_duration": {
        "en": "Record now - {duration}",
        "sv": "Spela in nu - {duration}",
        "es": "Grabar ahora - {duration}",
        "de": "Jetzt aufnehmen - {duration}",
        "fr": "Enregistrer maintenant - {duration}",
        "zh": "立即录制 - {duration}",
        "ru": "Записать сейчас - {duration}",
        "th": "บันทึกตอนนี้ - {duration}",
    },
    "rec_schedule_recording": {
        "en": "Schedule recording…",
        "sv": "Schemalägg inspelning…",
        "es": "Programar grabación…",
        "de": "Aufnahme planen…",
        "fr": "Programmer un enregistrement…",
        "zh": "计划录制…",
        "ru": "Запланировать запись…",
        "th": "กำหนดเวลาบันทึก…",
    },
    "rec_open_recordings": {
        "en": "Open Recordings",
        "sv": "Öppna inspelningar",
        "es": "Abrir grabaciones",
        "de": "Aufnahmen öffnen",
        "fr": "Ouvrir les enregistrements",
        "zh": "打开录制",
        "ru": "Открыть записи",
        "th": "เปิดการบันทึก",
    },
    "rec_recording_n_streams": {
        "en": "Recording {n} stream(s)…",
        "sv": "Spelar in {n} ström(mar)…",
        "es": "Grabando {n} flujo(s)…",
        "de": "{n} Stream(s) werden aufgenommen…",
        "fr": "Enregistrement de {n} flux…",
        "zh": "正在录制 {n} 个流…",
        "ru": "Запись {n} поток(ов)…",
        "th": "กำลังบันทึก {n} สตรีม…",
    },

    # ── Cast popup ────────────────────────────────────────────────────────

    "cast_other_titles": {
        "en": "other titles in your playlist",
        "sv": "andra titlar i din spellista",
        "es": "otros títulos en tu lista",
        "de": "andere Titel in deiner Wiedergabeliste",
        "fr": "autres titres dans votre liste de lecture",
        "zh": "你的播放列表中的其他影片",
        "ru": "другие фильмы в вашем плейлисте",
        "th": "ชื่อเรื่องอื่นในเพลย์ลิสต์ของคุณ",
    },
    "cast_looking_up": {
        "en": "Looking up filmography…",
        "sv": "Söker filmografi…",
        "es": "Buscando filmografía…",
        "de": "Filmografie wird gesucht…",
        "fr": "Recherche de la filmographie…",
        "zh": "正在查找影视作品…",
        "ru": "Поиск фильмографии…",
        "th": "กำลังค้นหาผลงาน…",
    },
    "cast_searching_playlist": {
        "en": "Searching your playlist…",
        "sv": "Söker i din spellista…",
        "es": "Buscando en tu lista…",
        "de": "Durchsuche deine Wiedergabeliste…",
        "fr": "Recherche dans votre liste de lecture…",
        "zh": "正在搜索你的播放列表…",
        "ru": "Поиск в вашем плейлисте…",
        "th": "กำลังค้นหาเพลย์ลิสต์ของคุณ…",
    },
    "cast_no_matches": {
        "en": "No other titles from this playlist matched.",
        "sv": "Inga andra titlar i spellistan matchade.",
        "es": "No se encontraron otros títulos en esta lista.",
        "de": "Keine anderen Titel in dieser Wiedergabeliste gefunden.",
        "fr": "Aucun autre titre de cette liste n'a correspondu.",
        "zh": "播放列表中没有匹配的其他影片。",
        "ru": "Совпадений с другими фильмами в плейлисте не найдено.",
        "th": "ไม่พบชื่อเรื่องอื่นในเพลย์ลิสต์นี้",
    },
    "cast_titles_found": {
        "en": "{count} title(s) found in your playlist",
        "sv": "{count} titel/titlar hittades i din spellista",
        "es": "{count} título(s) encontrado(s) en tu lista",
        "de": "{count} Titel in deiner Wiedergabeliste gefunden",
        "fr": "{count} titre(s) trouvé(s) dans votre liste de lecture",
        "zh": "在你的播放列表中找到 {count} 个影片",
        "ru": "Найдено {count} совпадений в вашем плейлисте",
        "th": "พบ {count} ชื่อเรื่องในเพลย์ลิสต์ของคุณ",
    },
    "cast_double_click": {
        "en": "double-click to open",
        "sv": "dubbelklicka för att öppna",
        "es": "doble clic para abrir",
        "de": "Doppelklick zum Öffnen",
        "fr": "double-cliquez pour ouvrir",
        "zh": "双击打开",
        "ru": "дважды щёлкните для открытия",
        "th": "ดับเบิลคลิกเพื่อเปิด",
    },
    "cast_find_other_titles": {
        "en": "Find other titles with {name} in your playlist",
        "sv": "Hitta andra titlar med {name} i din spellista",
        "es": "Buscar otros títulos con {name} en tu lista",
        "de": "Andere Titel mit {name} in deiner Liste finden",
        "fr": "Trouver d'autres titres avec {name} dans votre liste",
        "zh": "在播放列表中查找 {name} 的其他作品",
        "ru": "Найти другие фильмы с {name} в вашем плейлисте",
        "th": "ค้นหาชื่อเรื่องอื่นที่มี {name} ในเพลย์ลิสต์ของคุณ",
    },
    "cast_looking_up_member": {
        "en": "Looking up cast member…",
        "sv": "Söker efter skådespelare…",
        "es": "Buscando miembro del reparto…",
        "de": "Darsteller wird gesucht…",
        "fr": "Recherche de l'acteur…",
        "zh": "正在查找演员…",
        "ru": "Поиск актёра…",
        "th": "กำลังค้นหานักแสดง…",
    },

    # ── About / menu ──────────────────────────────────────────────────────

    "about_desc": {
        "en": "An elegant IPTV client for Xtream Codes and M3U playlists - "
              "with EPG, embedded mpv/VLC playback, favorites, recordings and "
              "Trakt sync.",
        "sv": "En elegant IPTV-klient för Xtream Codes och M3U-spellistor - "
              "med EPG, inbäddad mpv/VLC-uppspelning, favoriter, inspelningar "
              "och Trakt-synk.",
        "es": "Un elegante cliente IPTV para Xtream Codes y listas M3U, con "
              "EPG, reproducción integrada mpv/VLC, favoritos, grabaciones y "
              "sincronización con Trakt.",
        "de": "Ein eleganter IPTV-Client für Xtream Codes und M3U-Playlists - "
              "mit EPG, eingebetteter mpv/VLC-Wiedergabe, Favoriten, "
              "Aufnahmen und Trakt-Sync.",
        "fr": "Un client IPTV élégant pour Xtream Codes et playlists M3U - "
              "avec EPG, lecture intégrée mpv/VLC, favoris, enregistrements "
              "et synchronisation Trakt.",
        "zh": "一款优雅的 IPTV 客户端，支持 Xtream Codes 和 M3U 播放列表——"
              "含 EPG、内嵌 mpv/VLC 播放、收藏、录制和 Trakt 同步。",
        "ru": "Элегантный IPTV-клиент для Xtream Codes и M3U-плейлистов - с "
              "EPG, встроенным воспроизведением mpv/VLC, избранным, записью и "
              "синхронизацией с Trakt.",
        "th": "แอป IPTV ที่สวยงามสำหรับ Xtream Codes และเพลย์ลิสต์ M3U - พร้อม "
              "EPG, การเล่นในตัว mpv/VLC, รายการโปรด, การบันทึก และซิงก์ Trakt",
    },
    "about_website": {
        "en": "Website", "sv": "Webbplats", "es": "Sitio web", "de": "Website",
        "fr": "Site web", "zh": "网站", "ru": "Веб-сайт", "th": "เว็บไซต์",
    },
    "about_github": {
        "en": "GitHub", "sv": "GitHub", "es": "GitHub", "de": "GitHub",
        "fr": "GitHub", "zh": "GitHub", "ru": "GitHub", "th": "GitHub",
    },
    "about_all_releases": {
        "en": "All releases", "sv": "Alla versioner", "es": "Versiones",
        "de": "Alle Versionen", "fr": "Versions", "zh": "所有版本",
        "ru": "Все выпуски", "th": "ทุกเวอร์ชัน",
    },
    "about_checking": {
        "en": "Checking for updates…", "sv": "Söker efter uppdateringar…",
        "es": "Buscando actualizaciones…", "de": "Suche nach Updates…",
        "fr": "Recherche de mises à jour…", "zh": "正在检查更新……",
        "ru": "Проверка обновлений…", "th": "กำลังตรวจหาการอัปเดต…",
    },
    "about_up_to_date": {
        "en": "You're on the latest version.",
        "sv": "Du har den senaste versionen.",
        "es": "Tienes la última versión.",
        "de": "Du hast die neueste Version.",
        "fr": "Vous avez la dernière version.",
        "zh": "你已是最新版本。",
        "ru": "У вас последняя версия.",
        "th": "คุณใช้เวอร์ชันล่าสุดแล้ว",
    },
    "about_update_available": {
        "en": "A new version is available: {version}",
        "sv": "En ny version finns: {version}",
        "es": "Hay una nueva versión disponible: {version}",
        "de": "Eine neue Version ist verfügbar: {version}",
        "fr": "Une nouvelle version est disponible : {version}",
        "zh": "有新版本可用：{version}",
        "ru": "Доступна новая версия: {version}",
        "th": "มีเวอร์ชันใหม่: {version}",
    },
    "about_check_failed": {
        "en": "Couldn't check for updates right now.",
        "sv": "Kunde inte söka efter uppdateringar just nu.",
        "es": "No se pudo buscar actualizaciones ahora.",
        "de": "Konnte gerade nicht nach Updates suchen.",
        "fr": "Impossible de vérifier les mises à jour pour l'instant.",
        "zh": "暂时无法检查更新。",
        "ru": "Сейчас не удалось проверить обновления.",
        "th": "ตรวจหาการอัปเดตไม่ได้ในขณะนี้",
    },
    "about_download": {
        "en": "Download the update", "sv": "Ladda ner uppdateringen",
        "es": "Descargar la actualización", "de": "Update herunterladen",
        "fr": "Télécharger la mise à jour", "zh": "下载更新",
        "ru": "Скачать обновление", "th": "ดาวน์โหลดการอัปเดต",
    },
    "about_check_again": {
        "en": "Check again", "sv": "Sök igen", "es": "Buscar de nuevo",
        "de": "Erneut suchen", "fr": "Revérifier", "zh": "重新检查",
        "ru": "Проверить снова", "th": "ตรวจอีกครั้ง",
    },
    "about_check_updates": {
        "en": "Check for updates", "sv": "Sök efter uppdateringar",
        "es": "Buscar actualizaciones", "de": "Nach Updates suchen",
        "fr": "Rechercher des mises à jour", "zh": "检查更新",
        "ru": "Проверить обновления", "th": "ตรวจหาการอัปเดต",
    },
    "about_tmdb_credit": {
        "en": "Movie and TV metadata and artwork provided by TMDB. This "
              "product uses the TMDB API but is not endorsed or certified by "
              "TMDB.",
        "sv": "Metadata och bilder för filmer och serier tillhandahålls av "
              "TMDB. Denna produkt använder TMDB:s API men är inte godkänd "
              "eller certifierad av TMDB.",
        "es": "Metadatos e imágenes de películas y series proporcionados por "
              "TMDB. Este producto usa la API de TMDB pero no está avalado ni "
              "certificado por TMDB.",
        "de": "Film- und Serien-Metadaten und -Bilder von TMDB. Dieses "
              "Produkt nutzt die TMDB-API, ist aber nicht von TMDB unterstützt "
              "oder zertifiziert.",
        "fr": "Métadonnées et visuels de films et séries fournis par TMDB. Ce "
              "produit utilise l'API de TMDB mais n'est ni approuvé ni "
              "certifié par TMDB.",
        "zh": "影视元数据和图片由 TMDB 提供。本产品使用 TMDB API，但未获得 "
              "TMDB 的认可或认证。",
        "ru": "Метаданные и изображения фильмов и сериалов предоставлены TMDB. "
              "Этот продукт использует API TMDB, но не одобрен и не "
              "сертифицирован TMDB.",
        "th": "ข้อมูลและภาพของหนังและซีรีส์จัดหาโดย TMDB "
              "ผลิตภัณฑ์นี้ใช้ TMDB API แต่ไม่ได้รับการรับรองจาก TMDB",
    },
    "menu_about": {
        "en": "About dopeIPTV",
        "sv": "Om dopeIPTV",
        "es": "Acerca de dopeIPTV",
        "de": "Über dopeIPTV",
        "fr": "À propos de dopeIPTV",
        "zh": "关于 dopeIPTV",
        "ru": "О dopeIPTV",
        "th": "เกี่ยวกับ dopeIPTV",
    },
    "menu_quit": {
        "en": "Quit",
        "sv": "Avsluta",
        "es": "Salir",
        "de": "Beenden",
        "fr": "Quitter",
        "zh": "退出",
        "ru": "Выход",
        "th": "ออก",
    },
    "menu_refresh_playlist": {
        "en": "Refresh playlist",
        "sv": "Uppdatera spellista",
        "es": "Actualizar lista de reproducción",
        "de": "Wiedergabeliste aktualisieren",
        "fr": "Actualiser la liste de lecture",
        "zh": "刷新播放列表",
        "ru": "Обновить плейлист",
        "th": "รีเฟรชเพลย์ลิสต์",
    },

    # ── Labels for item counts ────────────────────────────────────────────

    "label_channels": {
        "en": "channels",
        "sv": "kanaler",
        "es": "canales",
        "de": "Kanäle",
        "fr": "chaînes",
        "zh": "频道",
        "ru": "каналов",
        "th": "ช่อง",
    },
    "label_movies": {
        "en": "movies",
        "sv": "filmer",
        "es": "películas",
        "de": "Filme",
        "fr": "films",
        "zh": "电影",
        "ru": "фильмов",
        "th": "ภาพยนตร์",
    },
    "label_series": {
        "en": "series",
        "sv": "serier",
        "es": "series",
        "de": "Serien",
        "fr": "séries",
        "zh": "剧集",
        "ru": "сериалов",
        "th": "ซีรีส์",
    },
    "label_episodes": {
        "en": "episodes",
        "sv": "avsnitt",
        "es": "episodios",
        "de": "Episoden",
        "fr": "épisodes",
        "zh": "集",
        "ru": "эпизодов",
        "th": "ตอน",
    },
    "label_favorites": {
        "en": "favorites",
        "sv": "favoriter",
        "es": "favoritos",
        "de": "Favoriten",
        "fr": "favoris",
        "zh": "收藏",
        "ru": "избранных",
        "th": "รายการโปรด",
    },
    "label_history_items": {
        "en": "history items",
        "sv": "historikobjekt",
        "es": "elementos del historial",
        "de": "Verlaufseinträge",
        "fr": "éléments d'historique",
        "zh": "历史记录",
        "ru": "записей истории",
        "th": "รายการประวัติ",
    },
    "label_recordings": {
        "en": "recordings",
        "sv": "inspelningar",
        "es": "grabaciones",
        "de": "Aufnahmen",
        "fr": "enregistrements",
        "zh": "录制",
        "ru": "записей",
        "th": "การบันทึก",
    },
    "label_all": {
        "en": "All",
        "sv": "Alla",
        "es": "Todos",
        "de": "Alle",
        "fr": "Tout",
        "zh": "全部",
        "ru": "Все",
        "th": "ทั้งหมด",
    },
    "label_size": {
        "en": "Size",
        "sv": "Storlek",
        "es": "Tamaño",
        "de": "Größe",
        "fr": "Taille",
        "zh": "大小",
        "ru": "Размер",
        "th": "ขนาด",
    },
    "label_sort": {
        "en": "Sort",
        "sv": "Sortera",
        "es": "Ordenar",
        "de": "Sortieren",
        "fr": "Trier",
        "zh": "排序",
        "ru": "Сортировка",
        "th": "เรียง",
    },
    "label_default": {
        "en": "Default",
        "sv": "Standard",
        "es": "Predeterminado",
        "de": "Standard",
        "fr": "Par défaut",
        "zh": "默认",
        "ru": "По умолчанию",
        "th": "ค่าเริ่มต้น",
    },
    "label_recent": {
        "en": "Recent",
        "sv": "Senaste",
        "es": "Reciente",
        "de": "Neueste",
        "fr": "Récent",
        "zh": "最近",
        "ru": "Недавние",
        "th": "ล่าสุด",
    },

    # ── Login dialog ──────────────────────────────────────────────────────

    "login_title": {
        "en": "Connect to an Xtream server",
        "sv": "Anslut till en Xtream-server",
        "es": "Conectar a un servidor Xtream",
        "de": "Mit einem Xtream-Server verbinden",
        "fr": "Connexion à un serveur Xtream",
        "zh": "连接到 Xtream 服务器",
        "ru": "Подключение к серверу Xtream",
        "th": "เชื่อมต่อกับเซิร์ฟเวอร์ Xtream",
    },
    "login_subtitle": {
        "en": "Sign in with your Xtream Codes credentials.",
        "sv": "Logga in med dina Xtream Codes-uppgifter.",
        "es": "Inicia sesión con tus credenciales de Xtream Codes.",
        "de": "Melden Sie sich mit Ihren Xtream-Codes-Zugangsdaten an.",
        "fr": "Connectez-vous avec vos identifiants Xtream Codes.",
        "zh": "使用您的 Xtream Codes 凭据登录。",
        "ru": "Войдите с вашими учётными данными Xtream Codes.",
        "th": "ลงชื่อเข้าใช้ด้วยข้อมูล Xtream Codes ของคุณ",
    },
    "login_server": {
        "en": "Server",
        "sv": "Server",
        "es": "Servidor",
        "de": "Server",
        "fr": "Serveur",
        "zh": "服务器",
        "ru": "Сервер",
        "th": "เซิร์ฟเวอร์",
    },
    "login_username": {
        "en": "Username",
        "sv": "Användarnamn",
        "es": "Usuario",
        "de": "Benutzername",
        "fr": "Nom d'utilisateur",
        "zh": "用户名",
        "ru": "Имя пользователя",
        "th": "ชื่อผู้ใช้",
    },
    "login_password": {
        "en": "Password",
        "sv": "Lösenord",
        "es": "Contraseña",
        "de": "Passwort",
        "fr": "Mot de passe",
        "zh": "密码",
        "ru": "Пароль",
        "th": "รหัสผ่าน",
    },

    # ── Playlist dialog ───────────────────────────────────────────────────

    "playlist_add_title": {
        "en": "Add playlist",
        "sv": "Lägg till spellista",
        "es": "Añadir lista de reproducción",
        "de": "Wiedergabeliste hinzufügen",
        "fr": "Ajouter une liste de lecture",
        "zh": "添加播放列表",
        "ru": "Добавить плейлист",
        "th": "เพิ่มเพลย์ลิสต์",
    },
    "playlist_edit_title": {
        "en": "Edit playlist",
        "sv": "Redigera spellista",
        "es": "Editar lista de reproducción",
        "de": "Wiedergabeliste bearbeiten",
        "fr": "Modifier la liste de lecture",
        "zh": "编辑播放列表",
        "ru": "Изменить плейлист",
        "th": "แก้ไขเพลย์ลิสต์",
    },
    "playlist_kind": {
        "en": "Type", "sv": "Typ", "es": "Tipo", "de": "Typ",
        "fr": "Type", "zh": "类型", "ru": "Тип", "th": "ประเภท",
    },
    "playlist_kind_xtream": {
        "en": "Xtream Codes (server + login)",
        "sv": "Xtream Codes (server + inloggning)",
        "es": "Xtream Codes (servidor + acceso)",
        "de": "Xtream Codes (Server + Login)",
        "fr": "Xtream Codes (serveur + identifiants)",
        "zh": "Xtream Codes（服务器 + 登录）",
        "ru": "Xtream Codes (сервер + вход)",
        "th": "Xtream Codes (เซิร์ฟเวอร์ + ล็อกอิน)",
    },
    "playlist_kind_m3u": {
        "en": "M3U playlist (URL)",
        "sv": "M3U-spellista (URL)",
        "es": "Lista M3U (URL)",
        "de": "M3U-Playlist (URL)",
        "fr": "Playlist M3U (URL)",
        "zh": "M3U 播放列表（URL）",
        "ru": "M3U-плейлист (URL)",
        "th": "เพลย์ลิสต์ M3U (URL)",
    },
    "playlist_m3u_url": {
        "en": "M3U URL", "sv": "M3U-URL", "es": "URL M3U", "de": "M3U-URL",
        "fr": "URL M3U", "zh": "M3U 网址", "ru": "URL M3U", "th": "URL M3U",
    },
    "playlist_name": {
        "en": "Name",
        "sv": "Namn",
        "es": "Nombre",
        "de": "Name",
        "fr": "Nom",
        "zh": "名称",
        "ru": "Название",
        "th": "ชื่อ",
    },
    "playlist_custom_epg_url": {
        "en": "Custom TV guide URL",
        "sv": "Anpassad TV-guide-URL",
        "es": "URL de guía de TV personalizada",
        "de": "Benutzerdefinierte TV-Guide-URL",
        "fr": "URL de guide TV personnalisée",
        "zh": "自定义电视指南 URL",
        "ru": "Пользовательский URL телепрограммы",
        "th": "URL คู่มือทีวีกำหนดเอง",
    },
    "playlist_auto_refresh": {
        "en": "Auto-refresh",
        "sv": "Automatisk uppdatering",
        "es": "Actualización automática",
        "de": "Automatische Aktualisierung",
        "fr": "Actualisation automatique",
        "zh": "自动刷新",
        "ru": "Автообновление",
        "th": "รีเฟรชอัตโนมัติ",
    },
    "playlist_required_fields": {
        "en": "Server, username and password are required.",
        "sv": "Server, användarnamn och lösenord krävs.",
        "es": "Servidor, usuario y contraseña son obligatorios.",
        "de": "Server, Benutzername und Passwort sind erforderlich.",
        "fr": "Le serveur, le nom d'utilisateur et le mot de passe sont obligatoires.",
        "zh": "服务器、用户名和密码为必填项。",
        "ru": "Сервер, имя пользователя и пароль обязательны.",
        "th": "ต้องระบุเซิร์ฟเวอร์ ชื่อผู้ใช้ และรหัสผ่าน",
    },

    # ── EPG guide ─────────────────────────────────────────────────────────

    "epg_now": {
        "en": "Now",
        "sv": "Nu",
        "es": "Ahora",
        "de": "Jetzt",
        "fr": "Maintenant",
        "zh": "当前",
        "ru": "Сейчас",
        "th": "ตอนนี้",
    },
    "epg_upcoming": {
        "en": "Upcoming",
        "sv": "Kommande",
        "es": "Próximamente",
        "de": "Demnächst",
        "fr": "À venir",
        "zh": "即将播出",
        "ru": "Далее",
        "th": "กำลังจะมาถึง",
    },
    "epg_earlier_today": {
        "en": "Earlier today",
        "sv": "Tidigare idag",
        "es": "Hoy más temprano",
        "de": "Heute früher",
        "fr": "Plus tôt aujourd'hui",
        "zh": "今日早些时候",
        "ru": "Ранее сегодня",
        "th": "ก่อนหน้านี้วันนี้",
    },
    "epg_no_current_data": {
        "en": "No current programme data",
        "sv": "Inga aktuella programdata",
        "es": "Sin datos de programa actual",
        "de": "Keine aktuellen Programmdaten",
        "fr": "Pas de données de programme en cours",
        "zh": "暂无当前节目数据",
        "ru": "Нет данных о текущей программе",
        "th": "ไม่มีข้อมูลรายการปัจจุบัน",
    },
    "epg_no_guide_available": {
        "en": "No programme guide available for this channel.",
        "sv": "Ingen programguide tillgänglig för denna kanal.",
        "es": "No hay guía de programación disponible para este canal.",
        "de": "Kein Programmführer für diesen Kanal verfügbar.",
        "fr": "Pas de guide des programmes disponible pour cette chaîne.",
        "zh": "此频道暂无节目指南。",
        "ru": "Программа передач для этого канала недоступна.",
        "th": "ไม่มีผังรายการสำหรับช่องนี้",
    },
    "epg_could_not_load": {
        "en": "Could not load the programme guide.",
        "sv": "Kunde inte ladda programguiden.",
        "es": "No se pudo cargar la guía de programación.",
        "de": "Programmführer konnte nicht geladen werden.",
        "fr": "Impossible de charger le guide des programmes.",
        "zh": "无法加载节目指南。",
        "ru": "Не удалось загрузить телепрограмму.",
        "th": "ไม่สามารถโหลดผังรายการได้",
    },

    # ── Context menu ──────────────────────────────────────────────────────

    "ctx_play_in_mpv": {
        "en": "Play in mpv",
        "sv": "Spela i mpv",
        "es": "Reproducir en mpv",
        "de": "In mpv abspielen",
        "fr": "Lire dans mpv",
        "zh": "在 mpv 中播放",
        "ru": "Воспроизвести в mpv",
        "th": "เล่นใน mpv",
    },
    "mv_add": {
        "en": "Add to multiview", "sv": "Lägg till i multiview",
        "es": "Añadir a multivista", "de": "Zu Multiview hinzufügen",
        "fr": "Ajouter au multivue", "zh": "添加到多画面",
        "ru": "Добавить в мультиэкран", "th": "เพิ่มไปยังมัลติวิว",
    },
    "mv_title": {
        "en": "dopeIPTV — Multiview", "sv": "dopeIPTV — Multiview",
        "es": "dopeIPTV — Multivista", "de": "dopeIPTV — Multiview",
        "fr": "dopeIPTV — Multivue", "zh": "dopeIPTV — 多画面",
        "ru": "dopeIPTV — Мультиэкран", "th": "dopeIPTV — มัลติวิว",
    },
    "mv_empty_cell": {
        "en": "Right-click a channel → Add to multiview",
        "sv": "Högerklicka en kanal → Lägg till i multiview",
        "es": "Clic derecho en un canal → Añadir a multivista",
        "de": "Kanal rechtsklicken → Zu Multiview hinzufügen",
        "fr": "Clic droit sur une chaîne → Ajouter au multivue",
        "zh": "右键点击频道 → 添加到多画面",
        "ru": "ПКМ по каналу → Добавить в мультиэкран",
        "th": "คลิกขวาที่ช่อง → เพิ่มไปยังมัลติวิว",
    },
    "mv_cell_error": {
        "en": "{title} — stream failed",
        "sv": "{title} — strömmen misslyckades",
        "es": "{title} — fallo de transmisión",
        "de": "{title} — Stream fehlgeschlagen",
        "fr": "{title} — échec du flux",
        "zh": "{title} — 流播放失败",
        "ru": "{title} — поток не работает",
        "th": "{title} — สตรีมล้มเหลว",
    },
    "ctx_open_externally": {
        "en": "Open externally",
        "sv": "Öppna externt",
        "es": "Abrir externamente",
        "de": "Extern öffnen",
        "fr": "Ouvrir en externe",
        "zh": "外部打开",
        "ru": "Открыть во внешнем плеере",
        "th": "เปิดภายนอก",
    },
    "ctx_cast_to_chromecast": {
        "en": "Cast to Chromecast…",
        "sv": "Casta till Chromecast…",
        "es": "Enviar a Chromecast…",
        "de": "An Chromecast senden…",
        "fr": "Diffuser sur Chromecast…",
        "zh": "投射到 Chromecast…",
        "ru": "Транслировать на Chromecast…",
        "th": "ส่งไปยัง Chromecast…",
    },
    "ctx_add_to_favorites": {
        "en": "Add to favorites",
        "sv": "Lägg till i favoriter",
        "es": "Añadir a favoritos",
        "de": "Zu Favoriten hinzufügen",
        "fr": "Ajouter aux favoris",
        "zh": "添加到收藏",
        "ru": "Добавить в избранное",
        "th": "เพิ่มในรายการโปรด",
    },
    "ctx_add_to_folder": {
        "en": "Add to folder",
        "sv": "Lägg till i mapp",
        "es": "Añadir a carpeta",
        "de": "Zu Ordner hinzufügen",
        "fr": "Ajouter au dossier",
        "zh": "添加到文件夹",
        "ru": "Добавить в папку",
        "th": "เพิ่มในโฟลเดอร์",
    },
    "ctx_rename_folder": {
        "en": "Rename folder…",
        "sv": "Byt namn på mapp…",
        "es": "Renombrar carpeta…",
        "de": "Ordner umbenennen…",
        "fr": "Renommer le dossier…",
        "zh": "重命名文件夹…",
        "ru": "Переименовать папку…",
        "th": "เปลี่ยนชื่อโฟลเดอร์…",
    },
    "ctx_remove_folder": {
        "en": "Remove folder “{group}”",
        "sv": "Ta bort mappen ”{group}”",
        "es": "Eliminar la carpeta «{group}»",
        "de": "Ordner „{group}“ entfernen",
        "fr": "Supprimer le dossier « {group} »",
        "zh": "删除文件夹“{group}”",
        "ru": "Удалить папку «{group}»",
        "th": "ลบโฟลเดอร์ “{group}”",
    },
    "prompt_folder_name": {
        "en": "Folder name:",
        "sv": "Mappnamn:",
        "es": "Nombre de la carpeta:",
        "de": "Ordnername:",
        "fr": "Nom du dossier :",
        "zh": "文件夹名称：",
        "ru": "Имя папки:",
        "th": "ชื่อโฟลเดอร์:",
    },
    "ctx_new_group": {
        "en": "New group…",
        "sv": "Ny grupp…",
        "es": "Nuevo grupo…",
        "de": "Neue Gruppe…",
        "fr": "Nouveau groupe…",
        "zh": "新建组…",
        "ru": "Новая группа…",
        "th": "กลุ่มใหม่…",
    },
    "ctx_remove_from_favorites": {
        "en": "Remove from favorites",
        "sv": "Ta bort från favoriter",
        "es": "Eliminar de favoritos",
        "de": "Aus Favoriten entfernen",
        "fr": "Supprimer des favoris",
        "zh": "从收藏中移除",
        "ru": "Удалить из избранного",
        "th": "ลบออกจากรายการโปรด",
    },
    "ctx_rename_channel": {
        "en": "Rename channel…",
        "sv": "Byt namn på kanal…",
        "es": "Renombrar canal…",
        "de": "Kanal umbenennen…",
        "fr": "Renommer la chaîne…",
        "zh": "重命名频道…",
        "ru": "Переименовать канал…",
        "th": "เปลี่ยนชื่อช่อง…",
    },
    "ctx_rename": {
        "en": "Rename…",
        "sv": "Byt namn…",
        "es": "Renombrar…",
        "de": "Umbenennen…",
        "fr": "Renommer…",
        "zh": "重命名…",
        "ru": "Переименовать…",
        "th": "เปลี่ยนชื่อ…",
    },
    "ctx_hide_channel": {
        "en": "Hide channel",
        "sv": "Dölj kanal",
        "es": "Ocultar canal",
        "de": "Kanal ausblenden",
        "fr": "Masquer la chaîne",
        "zh": "隐藏频道",
        "ru": "Скрыть канал",
        "th": "ซ่อนช่อง",
    },
    "ctx_hide": {
        "en": "Hide",
        "sv": "Dölj",
        "es": "Ocultar",
        "de": "Ausblenden",
        "fr": "Masquer",
        "zh": "隐藏",
        "ru": "Скрыть",
        "th": "ซ่อน",
    },
    "ctx_copy_stream_url": {
        "en": "Copy stream URL",
        "sv": "Kopiera ström-URL",
        "es": "Copiar URL del flujo",
        "de": "Stream-URL kopieren",
        "fr": "Copier l'URL du flux",
        "zh": "复制流地址",
        "ru": "Скопировать URL потока",
        "th": "คัดลอก URL สตรีม",
    },
    "ctx_remove_from_history": {
        "en": "Remove selected from history",
        "sv": "Ta bort markerade från historik",
        "es": "Eliminar seleccionado del historial",
        "de": "Ausgewählte aus Verlauf entfernen",
        "fr": "Supprimer la sélection de l'historique",
        "zh": "从历史中移除所选",
        "ru": "Удалить выбранное из истории",
        "th": "ลบที่เลือกออกจากประวัติ",
    },
    "ctx_manage_categories": {
        "en": "Manage categories…",
        "sv": "Hantera kategorier…",
        "es": "Gestionar categorías…",
        "de": "Kategorien verwalten…",
        "fr": "Gérer les catégories…",
        "zh": "管理分类…",
        "ru": "Управление категориями…",
        "th": "จัดการหมวดหมู่…",
    },
    "ctx_delete": {
        "en": "Delete",
        "sv": "Radera",
        "es": "Eliminar",
        "de": "Löschen",
        "fr": "Supprimer",
        "zh": "删除",
        "ru": "Удалить",
        "th": "ลบ",
    },
    "ctx_new_folder": {
        "en": "New folder…",
        "sv": "Ny mapp…",
        "es": "Nueva carpeta…",
        "de": "Neuer Ordner…",
        "fr": "Nouveau dossier…",
        "zh": "新建文件夹…",
        "ru": "Новая папка…",
        "th": "โฟลเดอร์ใหม่…",
    },
    "ctx_move_to": {
        "en": "Move to",
        "sv": "Flytta till",
        "es": "Mover a",
        "de": "Verschieben nach",
        "fr": "Déplacer vers",
        "zh": "移动到",
        "ru": "Переместить в",
        "th": "ย้ายไปยัง",
    },

    # ── Category management dialog ────────────────────────────────────────

    "cat_manage_title": {
        "en": "Manage categories",
        "sv": "Hantera kategorier",
        "es": "Gestionar categorías",
        "de": "Kategorien verwalten",
        "fr": "Gérer les catégories",
        "zh": "管理分类",
        "ru": "Управление категориями",
        "th": "จัดการหมวดหมู่",
    },
    "cat_rename": {
        "en": "Rename…",
        "sv": "Byt namn…",
        "es": "Renombrar…",
        "de": "Umbenennen…",
        "fr": "Renommer…",
        "zh": "重命名…",
        "ru": "Переименовать…",
        "th": "เปลี่ยนชื่อ…",
    },
    "cat_hide": {
        "en": "Hide",
        "sv": "Dölj",
        "es": "Ocultar",
        "de": "Ausblenden",
        "fr": "Masquer",
        "zh": "隐藏",
        "ru": "Скрыть",
        "th": "ซ่อน",
    },
    "cat_unhide": {
        "en": "Unhide",
        "sv": "Visa",
        "es": "Mostrar",
        "de": "Einblenden",
        "fr": "Afficher",
        "zh": "取消隐藏",
        "ru": "Показать",
        "th": "ยกเลิกการซ่อน",
    },
    "cat_lock": {
        "en": "Lock",
        "sv": "Lås",
        "es": "Bloquear",
        "de": "Sperren",
        "fr": "Verrouiller",
        "zh": "锁定",
        "ru": "Заблокировать",
        "th": "ล็อค",
    },
    "cat_unlock": {
        "en": "Unlock",
        "sv": "Lås upp",
        "es": "Desbloquear",
        "de": "Entsperren",
        "fr": "Déverrouiller",
        "zh": "解锁",
        "ru": "Разблокировать",
        "th": "ปลดล็อค",
    },

    # ── Parental control ──────────────────────────────────────────────────

    "parental_enter_pin": {
        "en": "Enter PIN:",
        "sv": "Ange PIN-kod:",
        "es": "Ingresa el PIN:",
        "de": "PIN eingeben:",
        "fr": "Entrez le code PIN :",
        "zh": "请输入 PIN 码：",
        "ru": "Введите PIN-код:",
        "th": "ป้อน PIN:",
    },
    "parental_wrong_pin": {
        "en": "Wrong PIN.",
        "sv": "Fel PIN-kod.",
        "es": "PIN incorrecto.",
        "de": "Falscher PIN.",
        "fr": "Code PIN incorrect.",
        "zh": "PIN 码错误。",
        "ru": "Неверный PIN-код.",
        "th": "PIN ไม่ถูกต้อง",
    },
    "parental_no_pin_set": {
        "en": "No PIN set.",
        "sv": "Ingen PIN-kod angiven.",
        "es": "Sin PIN establecido.",
        "de": "Kein PIN festgelegt.",
        "fr": "Aucun code PIN défini.",
        "zh": "未设置 PIN 码。",
        "ru": "PIN-код не задан.",
        "th": "ยังไม่ได้ตั้ง PIN",
    },
    "parental_set_change_pin": {
        "en": "Set / change PIN…",
        "sv": "Ange / ändra PIN-kod…",
        "es": "Establecer / cambiar PIN…",
        "de": "PIN festlegen / ändern…",
        "fr": "Définir / changer le code PIN…",
        "zh": "设置 / 更改 PIN 码…",
        "ru": "Установить / изменить PIN-код…",
        "th": "ตั้ง / เปลี่ยน PIN…",
    },
    "parental_remove_pin": {
        "en": "Remove PIN",
        "sv": "Ta bort PIN-kod",
        "es": "Eliminar PIN",
        "de": "PIN entfernen",
        "fr": "Supprimer le code PIN",
        "zh": "移除 PIN 码",
        "ru": "Удалить PIN-код",
        "th": "ลบ PIN",
    },
    "parental_lock_now": {
        "en": "Lock now",
        "sv": "Lås nu",
        "es": "Bloquear ahora",
        "de": "Jetzt sperren",
        "fr": "Verrouiller maintenant",
        "zh": "立即锁定",
        "ru": "Заблокировать сейчас",
        "th": "ล็อคตอนนี้",
    },
    "parental_control": {
        "en": "Parental control",
        "sv": "Föräldrakontroll",
        "es": "Control parental",
        "de": "Jugendschutz",
        "fr": "Contrôle parental",
        "zh": "家长控制",
        "ru": "Родительский контроль",
        "th": "การควบคุมโดยผู้ปกครอง",
    },

    # ── Confirmation / message dialogs ────────────────────────────────────

    "confirm_clear_history": {
        "en": "Remove all watch history?",
        "sv": "Ta bort all visningshistorik?",
        "es": "¿Eliminar todo el historial de reproducción?",
        "de": "Gesamten Wiedergabeverlauf löschen?",
        "fr": "Supprimer tout l'historique de visionnage ?",
        "zh": "清除所有观看历史？",
        "ru": "Удалить всю историю просмотров?",
        "th": "ลบประวัติการรับชมทั้งหมด?",
    },
    "confirm_delete_recording": {
        "en": "Delete {what} from disk?",
        "sv": "Ta bort {what} från disk?",
        "es": "¿Eliminar {what} del disco?",
        "de": "{what} von der Festplatte löschen?",
        "fr": "Supprimer {what} du disque ?",
        "zh": "从磁盘删除 {what}？",
        "ru": "Удалить {what} с диска?",
        "th": "ลบ {what} จากดิสก์?",
    },
    "confirm_remove_playlist": {
        "en": "Remove this playlist? Its favorites and history are kept "
              "until you re-add and clear them.",
        "sv": "Ta bort denna spellista? Favoriter och historik behålls "
              "tills du lägger till den igen och rensar dem.",
        "es": "¿Eliminar esta lista? Sus favoritos e historial se "
              "conservan hasta que la vuelvas a añadir y los borres.",
        "de": "Diese Wiedergabeliste entfernen? Favoriten und Verlauf "
              "werden beibehalten, bis Sie sie erneut hinzufügen und löschen.",
        "fr": "Supprimer cette liste ? Ses favoris et son historique "
              "sont conservés jusqu'à ce que vous les ajoutiez à nouveau et les effaciez.",
        "zh": "移除此播放列表？收藏和历史将保留，"
              "直到您重新添加并清除它们。",
        "ru": "Удалить этот плейлист? Избранное и история будут "
              "сохранены, пока вы не добавите его снова и не очистите их.",
        "th": "ลบเพลย์ลิสต์นี้? รายการโปรดและประวัติจะถูกเก็บไว้"
              "จนกว่าคุณจะเพิ่มใหม่และล้างข้อมูล",
    },
    "confirm_restore_channels": {
        "en": "Undo all channel renames and hides for this section "
              "and go back to the provider's original list?",
        "sv": "Ångra alla kanalnamnbyten och döljningar i denna "
              "sektion och gå tillbaka till leverantörens ursprungslista?",
        "es": "¿Deshacer todos los cambios de nombre y ocultaciones de "
              "canales en esta sección y volver a la lista original del proveedor?",
        "de": "Alle Kanalumbenennungen und -ausblendungen in diesem "
              "Bereich rückgängig machen und zur Originalliste des Anbieters zurückkehren?",
        "fr": "Annuler tous les renommages et masquages de chaînes dans "
              "cette section et revenir à la liste originale du fournisseur ?",
        "zh": "撤销此部分中所有频道的重命名和隐藏，"
              "恢复到提供商的原始列表？",
        "ru": "Отменить все переименования и скрытия каналов в этом разделе "
              "и вернуться к исходному списку провайдера?",
        "th": "ยกเลิกการเปลี่ยนชื่อและซ่อนช่องทั้งหมดในส่วนนี้ "
              "แล้วกลับไปยังรายการเดิมของผู้ให้บริการ?",
    },

    # ── Options menu (embedded player) ────────────────────────────────────

    "opt_audio_track": {
        "en": "Audio track",
        "sv": "Ljudspår",
        "es": "Pista de audio",
        "de": "Audiospur",
        "fr": "Piste audio",
        "zh": "音轨",
        "ru": "Аудиодорожка",
        "th": "แทร็กเสียง",
    },
    "opt_subtitles": {
        "en": "Subtitles",
        "sv": "Undertexter",
        "es": "Subtítulos",
        "de": "Untertitel",
        "fr": "Sous-titres",
        "zh": "字幕",
        "ru": "Субтитры",
        "th": "คำบรรยาย",
    },
    "opt_audio_delay": {
        "en": "Audio delay",
        "sv": "Ljudfördröjning",
        "es": "Retardo de audio",
        "de": "Audio-Verzögerung",
        "fr": "Délai audio",
        "zh": "音频延迟",
        "ru": "Задержка аудио",
        "th": "ดีเลย์เสียง",
    },
    "opt_aspect_ratio": {
        "en": "Aspect ratio",
        "sv": "Bildförhållande",
        "es": "Relación de aspecto",
        "de": "Seitenverhältnis",
        "fr": "Format d'image",
        "zh": "宽高比",
        "ru": "Соотношение сторон",
        "th": "อัตราส่วนภาพ",
    },
    "opt_network_buffer": {
        "en": "Network buffer",
        "sv": "Nätverksbuffert",
        "es": "Búfer de red",
        "de": "Netzwerkpuffer",
        "fr": "Tampon réseau",
        "zh": "网络缓冲",
        "ru": "Сетевой буфер",
        "th": "บัฟเฟอร์เครือข่าย",
    },
    "opt_sleep_timer": {
        "en": "Sleep timer", "sv": "Insomningstimer",
        "es": "Temporizador de apagado", "de": "Sleep-Timer",
        "fr": "Minuterie de veille", "zh": "睡眠定时器",
        "ru": "Таймер сна", "th": "ตั้งเวลาปิด",
    },
    "opt_minutes": {
        "en": "{n} min", "sv": "{n} min", "es": "{n} min", "de": "{n} Min",
        "fr": "{n} min", "zh": "{n} 分钟", "ru": "{n} мин", "th": "{n} นาที",
    },
    "opt_sleep_custom": {
        "en": "Custom…", "sv": "Egen tid…", "es": "Personalizado…",
        "de": "Eigene Zeit…", "fr": "Personnalisé…", "zh": "自定义……",
        "ru": "Свой…", "th": "กำหนดเอง…",
    },
    "sleep_prompt": {
        "en": "Stop playback after (minutes):",
        "sv": "Stäng av uppspelning efter (minuter):",
        "es": "Detener la reproducción tras (minutos):",
        "de": "Wiedergabe stoppen nach (Minuten):",
        "fr": "Arrêter la lecture après (minutes) :",
        "zh": "在多少分钟后停止播放：",
        "ru": "Остановить воспроизведение через (минут):",
        "th": "หยุดเล่นหลังจาก (นาที):",
    },
    "sleep_set": {
        "en": "Sleep timer: stopping in {n} min",
        "sv": "Insomningstimer: stänger av om {n} min",
        "es": "Temporizador: se apagará en {n} min",
        "de": "Sleep-Timer: stoppt in {n} Min",
        "fr": "Minuterie : arrêt dans {n} min",
        "zh": "睡眠定时器：{n} 分钟后停止",
        "ru": "Таймер сна: остановка через {n} мин",
        "th": "ตั้งเวลาปิด: จะหยุดใน {n} นาที",
    },
    "sleep_cancelled": {
        "en": "Sleep timer cancelled", "sv": "Insomningstimer avbruten",
        "es": "Temporizador cancelado", "de": "Sleep-Timer abgebrochen",
        "fr": "Minuterie annulée", "zh": "睡眠定时器已取消",
        "ru": "Таймер сна отменён", "th": "ยกเลิกตั้งเวลาปิดแล้ว",
    },
    "sleep_stopping": {
        "en": "Sleep timer: stopping playback", "sv": "Insomningstimer: stänger av",
        "es": "Temporizador: deteniendo", "de": "Sleep-Timer: stoppt",
        "fr": "Minuterie : arrêt", "zh": "睡眠定时器：正在停止",
        "ru": "Таймер сна: остановка", "th": "ตั้งเวลาปิด: กำลังหยุด",
    },
    "opt_stats_for_nerds": {
        "en": "Stats for nerds",
        "sv": "Statistik för nördar",
        "es": "Estadísticas para curiosos",
        "de": "Statistiken für Nerds",
        "fr": "Statistiques pour les curieux",
        "zh": "详细统计信息",
        "ru": "Статистика для гиков",
        "th": "สถิติสำหรับผู้ที่สนใจ",
    },

    # ── Timeshift / catch-up ──────────────────────────────────────────────

    "epg_play_this_programme": {
        "en": "Play this programme (catch-up)",
        "sv": "Spela det här programmet (catch-up)",
        "es": "Reproducir este programa (recuperar)",
        "de": "Diese Sendung ansehen (Catch-up)",
        "fr": "Lire cette émission (rattrapage)",
        "zh": "播放此节目（回看）",
        "ru": "Смотреть эту передачу (архив)",
        "th": "เล่นรายการนี้ (ดูย้อนหลัง)",
    },
    "ts_play_from_start": {
        "en": "Play from start (catch-up)",
        "sv": "Spela från början (catch-up)",
        "es": "Reproducir desde el inicio (recuperar)",
        "de": "Von Anfang an ansehen (Catch-up)",
        "fr": "Lire depuis le début (rattrapage)",
        "zh": "从头播放（回看）",
        "ru": "Смотреть сначала (архив)",
        "th": "เล่นตั้งแต่ต้น (ดูย้อนหลัง)",
    },
    "ts_go_live": {
        "en": "Go Live",
        "sv": "Gå live",
        "es": "Ir en vivo",
        "de": "Live gehen",
        "fr": "Aller en direct",
        "zh": "回到直播",
        "ru": "Прямой эфир",
        "th": "ไปที่สด",
    },
    "ts_watch_from_start": {
        "en": "Watch '{title}' from the start",
        "sv": "Titta på '{title}' från början",
        "es": "Ver '{title}' desde el inicio",
        "de": "'{title}' von Anfang an ansehen",
        "fr": "Regarder '{title}' depuis le début",
        "zh": "从头观看 '{title}'",
        "ru": "Смотреть '{title}' сначала",
        "th": "ดู '{title}' ตั้งแต่ต้น",
    },
    "ts_browse_past": {
        "en": "Browse past programmes (EPG)…",
        "sv": "Bläddra bland tidigare program (EPG)…",
        "es": "Explorar programas anteriores (EPG)…",
        "de": "Vergangene Sendungen durchsuchen (EPG)…",
        "fr": "Parcourir les programmes passés (EPG)…",
        "zh": "浏览过往节目 (EPG)…",
        "ru": "Просмотр прошедших программ (EPG)…",
        "th": "ดูรายการย้อนหลัง (EPG)…",
    },
    "ts_go_back_30m": {
        "en": "Go back 30 minutes",
        "sv": "Gå tillbaka 30 minuter",
        "es": "Retroceder 30 minutos",
        "de": "30 Minuten zurück",
        "fr": "Revenir de 30 minutes",
        "zh": "回退30分钟",
        "ru": "Назад на 30 минут",
        "th": "ย้อนกลับ 30 นาที",
    },
    "ts_go_back_1h": {
        "en": "Go back 1 hour",
        "sv": "Gå tillbaka 1 timme",
        "es": "Retroceder 1 hora",
        "de": "1 Stunde zurück",
        "fr": "Revenir de 1 heure",
        "zh": "回退1小时",
        "ru": "Назад на 1 час",
        "th": "ย้อนกลับ 1 ชั่วโมง",
    },
    "ts_go_back_2h": {
        "en": "Go back 2 hours",
        "sv": "Gå tillbaka 2 timmar",
        "es": "Retroceder 2 horas",
        "de": "2 Stunden zurück",
        "fr": "Revenir de 2 heures",
        "zh": "回退2小时",
        "ru": "Назад на 2 часа",
        "th": "ย้อนกลับ 2 ชั่วโมง",
    },
    "ts_go_back_6h": {
        "en": "Go back 6 hours",
        "sv": "Gå tillbaka 6 timmar",
        "es": "Retroceder 6 horas",
        "de": "6 Stunden zurück",
        "fr": "Revenir de 6 heures",
        "zh": "回退6小时",
        "ru": "Назад на 6 часов",
        "th": "ย้อนกลับ 6 ชั่วโมง",
    },
    "ts_go_back_12h": {
        "en": "Go back 12 hours",
        "sv": "Gå tillbaka 12 timmar",
        "es": "Retroceder 12 horas",
        "de": "12 Stunden zurück",
        "fr": "Revenir de 12 heures",
        "zh": "回退12小时",
        "ru": "Назад на 12 часов",
        "th": "ย้อนกลับ 12 ชั่วโมง",
    },
    "ts_go_back_1d": {
        "en": "Go back 1 day",
        "sv": "Gå tillbaka 1 dag",
        "es": "Retroceder 1 día",
        "de": "1 Tag zurück",
        "fr": "Revenir de 1 jour",
        "zh": "回退1天",
        "ru": "Назад на 1 день",
        "th": "ย้อนกลับ 1 วัน",
    },
    "ts_go_back_2d": {
        "en": "Go back 2 days",
        "sv": "Gå tillbaka 2 dagar",
        "es": "Retroceder 2 días",
        "de": "2 Tage zurück",
        "fr": "Revenir de 2 jours",
        "zh": "回退2天",
        "ru": "Назад на 2 дня",
        "th": "ย้อนกลับ 2 วัน",
    },
    "ts_go_back_3d": {
        "en": "Go back 3 days",
        "sv": "Gå tillbaka 3 dagar",
        "es": "Retroceder 3 días",
        "de": "3 Tage zurück",
        "fr": "Revenir de 3 jours",
        "zh": "回退3天",
        "ru": "Назад на 3 дня",
        "th": "ย้อนกลับ 3 วัน",
    },
    "ts_go_back_5d": {
        "en": "Go back 5 days",
        "sv": "Gå tillbaka 5 dagar",
        "es": "Retroceder 5 días",
        "de": "5 Tage zurück",
        "fr": "Revenir de 5 jours",
        "zh": "回退5天",
        "ru": "Назад на 5 дней",
        "th": "ย้อนกลับ 5 วัน",
    },
    "ts_go_back_7d": {
        "en": "Go back 7 days",
        "sv": "Gå tillbaka 7 dagar",
        "es": "Retroceder 7 días",
        "de": "7 Tage zurück",
        "fr": "Revenir de 7 jours",
        "zh": "回退7天",
        "ru": "Назад на 7 дней",
        "th": "ย้อนกลับ 7 วัน",
    },

    # ── Metadata tab ──────────────────────────────────────────────────────

    "meta_artwork_source": {
        "en": "Artwork source",
        "sv": "Bildkälla",
        "es": "Fuente de imágenes",
        "de": "Bildquelle",
        "fr": "Source des images",
        "zh": "封面来源",
        "ru": "Источник обложек",
        "th": "แหล่งภาพ",
    },
    "meta_playlist_artwork": {
        "en": "Playlist (provider artwork)",
        "sv": "Spellista (leverantörens bilder)",
        "es": "Lista (imágenes del proveedor)",
        "de": "Wiedergabeliste (Anbieter-Artwork)",
        "fr": "Liste (images du fournisseur)",
        "zh": "播放列表（提供商图片）",
        "ru": "Плейлист (обложки провайдера)",
        "th": "เพลย์ลิสต์ (ภาพจากผู้ให้บริการ)",
    },
    "meta_tmdb_artwork": {
        "en": "TMDB (fetch posters by title)",
        "sv": "TMDB (hämta affischer efter titel)",
        "es": "TMDB (obtener pósteres por título)",
        "de": "TMDB (Poster nach Titel abrufen)",
        "fr": "TMDB (récupérer les affiches par titre)",
        "zh": "TMDB（按标题获取海报）",
        "ru": "TMDB (поиск постеров по названию)",
        "th": "TMDB (ดึงโปสเตอร์ตามชื่อ)",
    },
    "meta_tmdb_api_key": {
        "en": "TMDB API key",
        "sv": "TMDB API-nyckel",
        "es": "Clave de API de TMDB",
        "de": "TMDB-API-Schlüssel",
        "fr": "Clé API TMDB",
        "zh": "TMDB API 密钥",
        "ru": "API-ключ TMDB",
        "th": "คีย์ API TMDB",
    },

    # ── About dialog ──────────────────────────────────────────────────────

    "about_description": {
        "en": "An elegant IPTV client for Xtream Codes with EPG, "
              "embedded playback, favorites and history.",
        "sv": "En elegant IPTV-klient för Xtream Codes med EPG, "
              "inbäddad uppspelning, favoriter och historik.",
        "es": "Un elegante cliente IPTV para Xtream Codes con EPG, "
              "reproducción integrada, favoritos e historial.",
        "de": "Ein eleganter IPTV-Client für Xtream Codes mit EPG, "
              "integrierter Wiedergabe, Favoriten und Verlauf.",
        "fr": "Un client IPTV élégant pour Xtream Codes avec EPG, "
              "lecture intégrée, favoris et historique.",
        "zh": "优雅的 Xtream Codes IPTV 客户端，"
              "支持 EPG、内嵌播放、收藏和历史记录。",
        "ru": "Элегантный IPTV-клиент для Xtream Codes с EPG, "
              "встроенным воспроизведением, избранным и историей.",
        "th": "แอป IPTV สำหรับ Xtream Codes ที่ใช้งานง่าย "
              "พร้อม EPG, การเล่นในแอป, รายการโปรดและประวัติ",
    },
    "about_playback_via": {
        "en": "Playback via mpv (embedded/window) or VLC.",
        "sv": "Uppspelning via mpv (inbäddad/fönster) eller VLC.",
        "es": "Reproducción mediante mpv (integrado/ventana) o VLC.",
        "de": "Wiedergabe über mpv (eingebettet/Fenster) oder VLC.",
        "fr": "Lecture via mpv (intégré/fenêtre) ou VLC.",
        "zh": "通过 mpv（内嵌/窗口）或 VLC 播放。",
        "ru": "Воспроизведение через mpv (встроенный/окно) или VLC.",
        "th": "เล่นผ่าน mpv (ฝัง/หน้าต่าง) หรือ VLC",
    },

    # ── Refresh options ───────────────────────────────────────────────────

    "refresh_never": {
        "en": "Never",
        "sv": "Aldrig",
        "es": "Nunca",
        "de": "Nie",
        "fr": "Jamais",
        "zh": "从不",
        "ru": "Никогда",
        "th": "ไม่เลย",
    },
    "refresh_at_startup": {
        "en": "At startup",
        "sv": "Vid start",
        "es": "Al iniciar",
        "de": "Beim Start",
        "fr": "Au démarrage",
        "zh": "启动时",
        "ru": "При запуске",
        "th": "เมื่อเริ่มต้น",
    },
    "refresh_every_2h": {
        "en": "Every 2 hours",
        "sv": "Var 2:a timme",
        "es": "Cada 2 horas",
        "de": "Alle 2 Stunden",
        "fr": "Toutes les 2 heures",
        "zh": "每2小时",
        "ru": "Каждые 2 часа",
        "th": "ทุก 2 ชั่วโมง",
    },
    "refresh_every_6h": {
        "en": "Every 6 hours",
        "sv": "Var 6:e timme",
        "es": "Cada 6 horas",
        "de": "Alle 6 Stunden",
        "fr": "Toutes les 6 heures",
        "zh": "每6小时",
        "ru": "Каждые 6 часов",
        "th": "ทุก 6 ชั่วโมง",
    },
    "refresh_every_12h": {
        "en": "Every 12 hours",
        "sv": "Var 12:e timme",
        "es": "Cada 12 horas",
        "de": "Alle 12 Stunden",
        "fr": "Toutes les 12 heures",
        "zh": "每12小时",
        "ru": "Каждые 12 часов",
        "th": "ทุก 12 ชั่วโมง",
    },
    "refresh_daily": {
        "en": "Daily",
        "sv": "Dagligen",
        "es": "Diariamente",
        "de": "Täglich",
        "fr": "Quotidiennement",
        "zh": "每天",
        "ru": "Ежедневно",
        "th": "ทุกวัน",
    },
    "refresh_weekly": {
        "en": "Weekly",
        "sv": "Varje vecka",
        "es": "Semanalmente",
        "de": "Wöchentlich",
        "fr": "Hebdomadaire",
        "zh": "每周",
        "ru": "Еженедельно",
        "th": "ทุกสัปดาห์",
    },

    # ── Misc / various ────────────────────────────────────────────────────

    "misc_movie": {
        "en": "Movie",
        "sv": "Film",
        "es": "Película",
        "de": "Film",
        "fr": "Film",
        "zh": "电影",
        "ru": "Фильм",
        "th": "ภาพยนตร์",
    },
    "misc_series_singular": {
        "en": "Series",
        "sv": "Serie",
        "es": "Serie",
        "de": "Serie",
        "fr": "Série",
        "zh": "剧集",
        "ru": "Сериал",
        "th": "ซีรีส์",
    },
    "misc_episode": {
        "en": "Episode",
        "sv": "Avsnitt",
        "es": "Episodio",
        "de": "Episode",
        "fr": "Épisode",
        "zh": "剧集",
        "ru": "Эпизод",
        "th": "ตอน",
    },
    "misc_recordings_saved_in": {
        "en": "Recordings are saved in:",
        "sv": "Inspelningar sparas i:",
        "es": "Las grabaciones se guardan en:",
        "de": "Aufnahmen werden gespeichert in:",
        "fr": "Les enregistrements sont sauvegardés dans :",
        "zh": "录制文件保存在：",
        "ru": "Записи сохраняются в:",
        "th": "การบันทึกถูกบันทึกใน:",
    },
    "misc_choose_folder": {
        "en": "Choose folder…",
        "sv": "Välj mapp…",
        "es": "Elegir carpeta…",
        "de": "Ordner auswählen…",
        "fr": "Choisir un dossier…",
        "zh": "选择文件夹…",
        "ru": "Выбрать папку…",
        "th": "เลือกโฟลเดอร์…",
    },
    "misc_no_audio_tracks": {
        "en": "(no audio tracks)",
        "sv": "(inga ljudspår)",
        "es": "(sin pistas de audio)",
        "de": "(keine Audiospuren)",
        "fr": "(pas de pistes audio)",
        "zh": "（无音轨）",
        "ru": "(нет аудиодорожек)",
        "th": "(ไม่มีแทร็กเสียง)",
    },
    "misc_error": {
        "en": "Error: {msg}",
        "sv": "Fel: {msg}",
        "es": "Error: {msg}",
        "de": "Fehler: {msg}",
        "fr": "Erreur : {msg}",
        "zh": "错误：{msg}",
        "ru": "Ошибка: {msg}",
        "th": "ข้อผิดพลาด: {msg}",
    },
    "misc_view_on_imdb": {
        "en": "View on IMDb",
        "sv": "Visa på IMDb",
        "es": "Ver en IMDb",
        "de": "Auf IMDb ansehen",
        "fr": "Voir sur IMDb",
        "zh": "在 IMDb 上查看",
        "ru": "Смотреть на IMDb",
        "th": "ดูบน IMDb",
    },
    "misc_loading": {
        "en": "Loading…",
        "sv": "Laddar…",
        "es": "Cargando…",
        "de": "Laden…",
        "fr": "Chargement…",
        "zh": "加载中…",
        "ru": "Загрузка…",
        "th": "กำลังโหลด…",
    },
    "misc_connected_to_trakt": {
        "en": "Connected to Trakt.",
        "sv": "Ansluten till Trakt.",
        "es": "Conectado a Trakt.",
        "de": "Mit Trakt verbunden.",
        "fr": "Connecté à Trakt.",
        "zh": "已连接到 Trakt。",
        "ru": "Подключено к Trakt.",
        "th": "เชื่อมต่อกับ Trakt แล้ว",
    },
    "misc_not_connected": {
        "en": "Not connected.",
        "sv": "Ej ansluten.",
        "es": "No conectado.",
        "de": "Nicht verbunden.",
        "fr": "Non connecté.",
        "zh": "未连接。",
        "ru": "Не подключено.",
        "th": "ไม่ได้เชื่อมต่อ",
    },
    "misc_connect_to_trakt": {
        "en": "Connect to Trakt…",
        "sv": "Anslut till Trakt…",
        "es": "Conectar a Trakt…",
        "de": "Mit Trakt verbinden…",
        "fr": "Se connecter à Trakt…",
        "zh": "连接到 Trakt…",
        "ru": "Подключить Trakt…",
        "th": "เชื่อมต่อกับ Trakt…",
    },
    "misc_disconnect": {
        "en": "Disconnect",
        "sv": "Koppla från",
        "es": "Desconectar",
        "de": "Trennen",
        "fr": "Déconnecter",
        "zh": "断开连接",
        "ru": "Отключить",
        "th": "ตัดการเชื่อมต่อ",
    },
    "misc_watchlist_history": {
        "en": "Watchlist / History…",
        "sv": "Bevakningslista / Historik…",
        "es": "Lista de seguimiento / Historial…",
        "de": "Merkliste / Verlauf…",
        "fr": "Liste de suivi / Historique…",
        "zh": "观看列表 / 历史…",
        "ru": "Список просмотра / История…",
        "th": "รายการติดตาม / ประวัติ…",
    },
    "misc_connect_first": {
        "en": "Connect to Trakt first.",
        "sv": "Anslut till Trakt först.",
        "es": "Conéctate a Trakt primero.",
        "de": "Verbinde dich zuerst mit Trakt.",
        "fr": "Connectez-vous d'abord à Trakt.",
        "zh": "请先连接到 Trakt。",
        "ru": "Сначала подключитесь к Trakt.",
        "th": "กรุณาเชื่อมต่อกับ Trakt ก่อน",
    },
    "misc_stop_recording_at": {
        "en": "Stop a recording when the file reaches",
        "sv": "Stoppa inspelning när filen når",
        "es": "Detener grabación cuando el archivo llegue a",
        "de": "Aufnahme stoppen wenn die Datei erreicht",
        "fr": "Arrêter l'enregistrement lorsque le fichier atteint",
        "zh": "当文件达到以下大小时停止录制",
        "ru": "Остановить запись при достижении файлом",
        "th": "หยุดบันทึกเมื่อไฟล์ถึง",
    },
    "misc_theme_applies_immediately": {
        "en": "Theme and accent apply immediately.",
        "sv": "Tema och accentfärg tillämpas omedelbart.",
        "es": "El tema y el color de acento se aplican inmediatamente.",
        "de": "Thema und Akzentfarbe werden sofort angewendet.",
        "fr": "Le thème et la couleur d'accent s'appliquent immédiatement.",
        "zh": "主题和强调色立即应用。",
        "ru": "Тема и цвет акцента применяются сразу.",
        "th": "ธีมและสีเน้นจะมีผลทันที",
    },
    "misc_language_restart": {
        "en": "The menus update now. Restart dopeIPTV to translate every part of the app.",
        "sv": "Menyerna uppdateras nu. Starta om dopeIPTV för att översätta hela appen.",
        "es": "Los menús se actualizan ahora. Reinicia dopeIPTV para traducir toda la aplicación.",
        "de": "Die Menüs werden jetzt aktualisiert. Starte dopeIPTV neu, um die gesamte App zu übersetzen.",
        "fr": "Les menus se mettent à jour maintenant. Redémarrez dopeIPTV pour traduire toute l'application.",
        "zh": "菜单已更新。重启 dopeIPTV 以翻译应用的所有部分。",
        "ru": "Меню обновлены. Перезапустите dopeIPTV, чтобы перевести всё приложение.",
        "th": "เมนูอัปเดตแล้ว รีสตาร์ท dopeIPTV เพื่อแปลทุกส่วนของแอป",
    },
    "popout_always_on_top": {
        "en": "Always on top", "sv": "Alltid överst", "es": "Siempre visible",
        "de": "Immer im Vordergrund", "fr": "Toujours au premier plan",
        "zh": "置顶", "ru": "Поверх всех окон", "th": "ปักหมุดบนสุด",
    },
    "popout_autohide_controls": {
        "en": "Auto-hide controls", "sv": "Dölj kontroller automatiskt",
        "es": "Ocultar controles automáticamente",
        "de": "Steuerung automatisch ausblenden",
        "fr": "Masquer les commandes automatiquement", "zh": "自动隐藏控制栏",
        "ru": "Автоскрытие панели управления", "th": "ซ่อนแถบควบคุมอัตโนมัติ",
    },
    "popout_hide_titlebar": {
        "en": "Hide title bar", "sv": "Dölj titelrad",
        "es": "Ocultar barra de título", "de": "Titelleiste ausblenden",
        "fr": "Masquer la barre de titre", "zh": "隐藏标题栏",
        "ru": "Скрыть заголовок окна", "th": "ซ่อนแถบชื่อ",
    },
    "popout_show_titlebar": {
        "en": "Show title bar", "sv": "Visa titelrad",
        "es": "Mostrar barra de título", "de": "Titelleiste anzeigen",
        "fr": "Afficher la barre de titre", "zh": "显示标题栏",
        "ru": "Показать заголовок окна", "th": "แสดงแถบชื่อ",
    },
    "popout_wayland_hint": {
        "en": "Always on top: right-click the title bar (Wayland)",
        "sv": "Alltid överst: högerklicka på titelraden (Wayland)",
        "es": "Siempre visible: clic derecho en la barra de título (Wayland)",
        "de": "Immer im Vordergrund: Titelleiste rechtsklicken (Wayland)",
        "fr": "Toujours au premier plan : clic droit sur la barre de titre "
              "(Wayland)",
        "zh": "始终置顶：右键点击标题栏（Wayland）",
        "ru": "Поверх всех окон: ПКМ по заголовку окна (Wayland)",
        "th": "อยู่บนสุดเสมอ: คลิกขวาที่แถบชื่อหน้าต่าง (Wayland)",
    },
    "resume_title": {
        "en": "Resume playback", "sv": "Återuppta uppspelning",
        "es": "Reanudar reproducción", "de": "Wiedergabe fortsetzen",
        "fr": "Reprendre la lecture", "zh": "继续播放",
        "ru": "Продолжить воспроизведение", "th": "เล่นต่อ",
    },
    "resume_prompt": {
        "en": "You stopped watching at {time}.",
        "sv": "Du slutade titta vid {time}.",
        "es": "Dejaste de ver en {time}.",
        "de": "Du hast bei {time} aufgehört.",
        "fr": "Vous vous êtes arrêté à {time}.",
        "zh": "你上次看到 {time}。",
        "ru": "Вы остановились на {time}.",
        "th": "คุณหยุดดูไว้ที่ {time}",
    },
    "resume_continue": {
        "en": "Resume from {time}", "sv": "Fortsätt från {time}",
        "es": "Continuar desde {time}", "de": "Ab {time} fortsetzen",
        "fr": "Reprendre à {time}", "zh": "从 {time} 继续",
        "ru": "Продолжить с {time}", "th": "เล่นต่อจาก {time}",
    },
    "resume_restart": {
        "en": "Start from the beginning", "sv": "Börja från början",
        "es": "Empezar desde el principio", "de": "Von vorne beginnen",
        "fr": "Recommencer depuis le début", "zh": "从头开始",
        "ru": "Начать сначала", "th": "เริ่มจากต้น",
    },
    "theme_graphite": {
        "en": "Graphite (default)", "sv": "Grafit (standard)",
        "es": "Grafito (predeterminado)", "de": "Graphit (Standard)",
        "fr": "Graphite (défaut)", "zh": "石墨（默认）",
        "ru": "Графит (по умолч.)", "th": "กราไฟต์ (เริ่มต้น)",
    },
    "theme_midnight": {
        "en": "Midnight (blue)", "sv": "Midnatt (blå)",
        "es": "Medianoche (azul)", "de": "Mitternacht (blau)",
        "fr": "Minuit (bleu)", "zh": "午夜（蓝）",
        "ru": "Полночь (синий)", "th": "เที่ยงคืน (น้ำเงิน)",
    },
    "theme_oled": {
        "en": "OLED (pure black)", "sv": "OLED (helsvart)",
        "es": "OLED (negro puro)", "de": "OLED (reines Schwarz)",
        "fr": "OLED (noir pur)", "zh": "OLED（纯黑）",
        "ru": "OLED (чёрный)", "th": "OLED (ดำสนิท)",
    },
    "theme_nord": {
        "en": "Nord", "sv": "Nord", "es": "Nord", "de": "Nord",
        "fr": "Nord", "zh": "Nord", "ru": "Nord", "th": "Nord",
    },
    "theme_dracula": {
        "en": "Dracula", "sv": "Dracula", "es": "Dracula", "de": "Dracula",
        "fr": "Dracula", "zh": "Dracula", "ru": "Dracula", "th": "Dracula",
    },
    "theme_gruvbox": {
        "en": "Gruvbox (dark)", "sv": "Gruvbox (mörk)",
        "es": "Gruvbox (oscuro)", "de": "Gruvbox (dunkel)",
        "fr": "Gruvbox (sombre)", "zh": "Gruvbox（深色）",
        "ru": "Gruvbox (тёмная)", "th": "Gruvbox (มืด)",
    },
    "theme_solarized": {
        "en": "Solarized (dark)", "sv": "Solarized (mörk)",
        "es": "Solarized (oscuro)", "de": "Solarized (dunkel)",
        "fr": "Solarized (sombre)", "zh": "Solarized（深色）",
        "ru": "Solarized (тёмная)", "th": "Solarized (มืด)",
    },
    "theme_catppuccin": {
        "en": "Catppuccin (mocha)", "sv": "Catppuccin (mocha)",
        "es": "Catppuccin (mocha)", "de": "Catppuccin (mocha)",
        "fr": "Catppuccin (mocha)", "zh": "Catppuccin（mocha）",
        "ru": "Catppuccin (mocha)", "th": "Catppuccin (mocha)",
    },
    "theme_light": {
        "en": "Light", "sv": "Ljust", "es": "Claro", "de": "Hell",
        "fr": "Clair", "zh": "浅色", "ru": "Светлая", "th": "สว่าง",
    },
    "accent_blue": {
        "en": "Blue", "sv": "Blå", "es": "Azul", "de": "Blau",
        "fr": "Bleu", "zh": "蓝色", "ru": "Синий", "th": "น้ำเงิน",
    },
    "accent_purple": {
        "en": "Purple", "sv": "Lila", "es": "Morado", "de": "Lila",
        "fr": "Violet", "zh": "紫色", "ru": "Фиолетовый", "th": "ม่วง",
    },
    "accent_teal": {
        "en": "Teal", "sv": "Turkos", "es": "Verde azulado", "de": "Türkis",
        "fr": "Turquoise", "zh": "青色", "ru": "Бирюзовый", "th": "ครามเขียว",
    },
    "accent_green": {
        "en": "Green", "sv": "Grön", "es": "Verde", "de": "Grün",
        "fr": "Vert", "zh": "绿色", "ru": "Зелёный", "th": "เขียว",
    },
    "accent_orange": {
        "en": "Orange", "sv": "Orange", "es": "Naranja", "de": "Orange",
        "fr": "Orange", "zh": "橙色", "ru": "Оранжевый", "th": "ส้ม",
    },
    "accent_pink": {
        "en": "Pink", "sv": "Rosa", "es": "Rosa", "de": "Rosa",
        "fr": "Rose", "zh": "粉色", "ru": "Розовый", "th": "ชมพู",
    },
    "accent_red": {
        "en": "Red", "sv": "Röd", "es": "Rojo", "de": "Rot",
        "fr": "Rouge", "zh": "红色", "ru": "Красный", "th": "แดง",
    },
    "lang_swe": {
        "en": "Swedish", "sv": "Svenska", "es": "Sueco", "de": "Schwedisch",
        "fr": "Suédois", "zh": "瑞典语", "ru": "Шведский", "th": "สวีเดน",
    },
    "lang_eng": {
        "en": "English", "sv": "Engelska", "es": "Inglés", "de": "Englisch",
        "fr": "Anglais", "zh": "英语", "ru": "Английский", "th": "อังกฤษ",
    },
    "lang_nor": {
        "en": "Norwegian", "sv": "Norska", "es": "Noruego", "de": "Norwegisch",
        "fr": "Norvégien", "zh": "挪威语", "ru": "Норвежский", "th": "นอร์เวย์",
    },
    "lang_dan": {
        "en": "Danish", "sv": "Danska", "es": "Danés", "de": "Dänisch",
        "fr": "Danois", "zh": "丹麦语", "ru": "Датский", "th": "เดนมาร์ก",
    },
    "lang_fin": {
        "en": "Finnish", "sv": "Finska", "es": "Finés", "de": "Finnisch",
        "fr": "Finnois", "zh": "芬兰语", "ru": "Финский", "th": "ฟินแลนด์",
    },
    "lang_ger": {
        "en": "German", "sv": "Tyska", "es": "Alemán", "de": "Deutsch",
        "fr": "Allemand", "zh": "德语", "ru": "Немецкий", "th": "เยอรมัน",
    },
    "lang_fre": {
        "en": "French", "sv": "Franska", "es": "Francés", "de": "Französisch",
        "fr": "Français", "zh": "法语", "ru": "Французский", "th": "ฝรั่งเศส",
    },
    "lang_spa": {
        "en": "Spanish", "sv": "Spanska", "es": "Español", "de": "Spanisch",
        "fr": "Espagnol", "zh": "西班牙语", "ru": "Испанский", "th": "สเปน",
    },
    "lang_ita": {
        "en": "Italian", "sv": "Italienska", "es": "Italiano",
        "de": "Italienisch", "fr": "Italien", "zh": "意大利语",
        "ru": "Итальянский", "th": "อิตาลี",
    },
    "lang_por": {
        "en": "Portuguese", "sv": "Portugisiska", "es": "Portugués",
        "de": "Portugiesisch", "fr": "Portugais", "zh": "葡萄牙语",
        "ru": "Португальский", "th": "โปรตุเกส",
    },
    "lang_pol": {
        "en": "Polish", "sv": "Polska", "es": "Polaco", "de": "Polnisch",
        "fr": "Polonais", "zh": "波兰语", "ru": "Польский", "th": "โปแลนด์",
    },
    "lang_ara": {
        "en": "Arabic", "sv": "Arabiska", "es": "Árabe", "de": "Arabisch",
        "fr": "Arabe", "zh": "阿拉伯语", "ru": "Арабский", "th": "อาหรับ",
    },
    "lang_tur": {
        "en": "Turkish", "sv": "Turkiska", "es": "Turco", "de": "Türkisch",
        "fr": "Turc", "zh": "土耳其语", "ru": "Турецкий", "th": "ตุรกี",
    },
    "misc_no_playlists_export": {
        "en": "No playlists to export.",
        "sv": "Inga spellistor att exportera.",
        "es": "No hay listas para exportar.",
        "de": "Keine Wiedergabelisten zum Exportieren.",
        "fr": "Aucune liste de lecture à exporter.",
        "zh": "没有可导出的播放列表。",
        "ru": "Нет плейлистов для экспорта.",
        "th": "ไม่มีเพลย์ลิสต์ให้ส่งออก",
    },
    "misc_exported_n_playlists": {
        "en": "Exported {count} playlist(s) to:\n{path}",
        "sv": "Exporterade {count} spellista/-or till:\n{path}",
        "es": "Se exportaron {count} lista(s) a:\n{path}",
        "de": "{count} Wiedergabeliste(n) exportiert nach:\n{path}",
        "fr": "{count} liste(s) exportée(s) vers :\n{path}",
        "zh": "已导出 {count} 个播放列表到：\n{path}",
        "ru": "Экспортировано {count} плейлист(ов) в:\n{path}",
        "th": "ส่งออก {count} เพลย์ลิสต์ไปยัง:\n{path}",
    },
    "misc_imported_n_playlists": {
        "en": "Imported {count} playlist(s).",
        "sv": "Importerade {count} spellista/-or.",
        "es": "Se importaron {count} lista(s).",
        "de": "{count} Wiedergabeliste(n) importiert.",
        "fr": "{count} liste(s) importée(s).",
        "zh": "已导入 {count} 个播放列表。",
        "ru": "Импортировано {count} плейлист(ов).",
        "th": "นำเข้า {count} เพลย์ลิสต์แล้ว",
    },
    "misc_recording_stopped": {
        "en": "Recording stopped: {title} ({reason})",
        "sv": "Inspelning stoppad: {title} ({reason})",
        "es": "Grabación detenida: {title} ({reason})",
        "de": "Aufnahme gestoppt: {title} ({reason})",
        "fr": "Enregistrement arrêté : {title} ({reason})",
        "zh": "录制已停止：{title}（{reason}）",
        "ru": "Запись остановлена: {title} ({reason})",
        "th": "หยุดบันทึก: {title} ({reason})",
    },
    "misc_for_linux": {
        "en": "for Linux",
        "sv": "för Linux",
        "es": "para Linux",
        "de": "für Linux",
        "fr": "pour Linux",
        "zh": "Linux 版",
        "ru": "для Linux",
        "th": "สำหรับ Linux",
    },

    # ══════════════════════════════════════════════════════════════════════
    # To add a new UI string: add a key below with a line per language, then
    # call tr("your_key") in the code. To add a whole new *language*: add its
    # code to LANGUAGES above and a matching entry to every key here.
    # ══════════════════════════════════════════════════════════════════════

    # ── Player options menu (the ⚙ button) ───────────────────────────────
    "opt_off": {
        "en": "Off", "sv": "Av", "es": "Desactivado", "de": "Aus",
        "fr": "Désactivé", "zh": "关闭", "ru": "Выкл.", "th": "ปิด",
    },
    "opt_no_audio_tracks": {
        "en": "(no audio tracks)", "sv": "(inga ljudspår)",
        "es": "(sin pistas de audio)", "de": "(keine Audiospuren)",
        "fr": "(aucune piste audio)", "zh": "（无音轨）",
        "ru": "(нет аудиодорожек)", "th": "(ไม่มีแทร็กเสียง)",
    },
    "opt_delay_default": {
        "en": "0 s (default)", "sv": "0 s (standard)",
        "es": "0 s (predeterminado)", "de": "0 s (Standard)",
        "fr": "0 s (défaut)", "zh": "0 秒（默认）",
        "ru": "0 с (по умолч.)", "th": "0 วินาที (ค่าเริ่มต้น)",
    },
    "opt_aspect_auto": {
        "en": "Auto", "sv": "Auto", "es": "Auto", "de": "Auto",
        "fr": "Auto", "zh": "自动", "ru": "Авто", "th": "อัตโนมัติ",
    },
    "opt_aspect_stretch": {
        "en": "Stretch to window", "sv": "Sträck till fönster",
        "es": "Estirar a la ventana", "de": "An Fenster anpassen",
        "fr": "Étirer à la fenêtre", "zh": "拉伸至窗口",
        "ru": "Растянуть по окну", "th": "ยืดเต็มหน้าต่าง",
    },

    # ── Common buttons reused in several dialogs ──────────────────────────
    "common_close": {
        "en": "Close", "sv": "Stäng", "es": "Cerrar", "de": "Schließen",
        "fr": "Fermer", "zh": "关闭", "ru": "Закрыть", "th": "ปิด",
    },
    "common_cancel": {
        "en": "Cancel", "sv": "Avbryt", "es": "Cancelar", "de": "Abbrechen",
        "fr": "Annuler", "zh": "取消", "ru": "Отмена", "th": "ยกเลิก",
    },
    "common_dismiss": {
        "en": "Dismiss", "sv": "Stäng", "es": "Descartar", "de": "Schließen",
        "fr": "Ignorer", "zh": "关闭", "ru": "Закрыть", "th": "ปิด",
    },
    "reminder_add": {
        "en": "Remind me when it starts", "sv": "Påminn mig när det börjar",
        "es": "Recordarme cuando empiece", "de": "Erinnern, wenn es beginnt",
        "fr": "Me rappeler au début", "zh": "开始时提醒我",
        "ru": "Напомнить о начале", "th": "เตือนฉันเมื่อเริ่ม",
    },
    "reminder_remove": {
        "en": "Remove reminder", "sv": "Ta bort påminnelse",
        "es": "Quitar recordatorio", "de": "Erinnerung entfernen",
        "fr": "Supprimer le rappel", "zh": "移除提醒",
        "ru": "Убрать напоминание", "th": "ลบการเตือน",
    },
    "reminder_set": {
        "en": "Reminder set for {title}", "sv": "Påminnelse satt för {title}",
        "es": "Recordatorio para {title}", "de": "Erinnerung für {title} gesetzt",
        "fr": "Rappel défini pour {title}", "zh": "已为 {title} 设置提醒",
        "ru": "Напоминание для {title}", "th": "ตั้งการเตือนสำหรับ {title} แล้ว",
    },
    "reminder_now_title": {
        "en": "Programme starting", "sv": "Program börjar",
        "es": "El programa comienza", "de": "Sendung beginnt",
        "fr": "Le programme commence", "zh": "节目即将开始",
        "ru": "Передача начинается", "th": "รายการกำลังเริ่ม",
    },
    "reminder_now_body": {
        "en": "{title} is starting on {channel}.",
        "sv": "{title} börjar på {channel}.",
        "es": "{title} está empezando en {channel}.",
        "de": "{title} beginnt auf {channel}.",
        "fr": "{title} commence sur {channel}.",
        "zh": "{title} 正在 {channel} 开始。",
        "ru": "{title} начинается на {channel}.",
        "th": "{title} กำลังเริ่มทาง {channel}",
    },
    "reminder_watch_now": {
        "en": "Watch now", "sv": "Titta nu", "es": "Ver ahora",
        "de": "Jetzt ansehen", "fr": "Regarder", "zh": "立即观看",
        "ru": "Смотреть", "th": "ดูเลย",
    },
    "common_loading": {
        "en": "Loading…", "sv": "Laddar…", "es": "Cargando…",
        "de": "Wird geladen…", "fr": "Chargement…", "zh": "加载中……",
        "ru": "Загрузка…", "th": "กำลังโหลด…",
    },
    "rec_switch_title": {
        "en": "Record another channel?",
        "sv": "Spela in en annan kanal?",
        "es": "¿Grabar otro canal?",
        "de": "Anderen Kanal aufnehmen?",
        "fr": "Enregistrer une autre chaîne ?",
        "zh": "录制另一个频道？",
        "ru": "Записать другой канал?",
        "th": "บันทึกอีกช่องหรือไม่?",
    },
    "rec_switch_body": {
        "en": "“{playing}” is playing. Recording “{target}” instead needs a "
              "second connection to your provider - many accounts allow only "
              "one at a time.",
        "sv": "“{playing}” spelas. Att spela in “{target}” kräver en andra "
              "anslutning till din leverantör - många konton tillåter bara "
              "en åt gången.",
        "es": "“{playing}” se está reproduciendo. Grabar “{target}” necesita "
              "una segunda conexión con tu proveedor; muchas cuentas solo "
              "permiten una a la vez.",
        "de": "“{playing}” läuft. Für die Aufnahme von “{target}” ist eine "
              "zweite Verbindung zum Anbieter nötig - viele Konten erlauben "
              "nur eine gleichzeitig.",
        "fr": "“{playing}” est en lecture. Enregistrer “{target}” nécessite "
              "une deuxième connexion à votre fournisseur - beaucoup de "
              "comptes n'en autorisent qu'une à la fois.",
        "zh": "正在播放“{playing}”。录制“{target}”需要与提供商建立第二个连接——"
              "许多账户一次只允许一个。",
        "ru": "Воспроизводится «{playing}». Для записи «{target}» нужно второе "
              "подключение к провайдеру - многие аккаунты допускают только "
              "одно одновременно.",
        "th": "กำลังเล่น “{playing}” การบันทึก “{target}” ต้องใช้การเชื่อมต่อที่สองกับ"
              "ผู้ให้บริการ - หลายบัญชีอนุญาตเพียงหนึ่งเดียวในแต่ละครั้ง",
    },
    "rec_switch_and_record": {
        "en": "Switch to it and record",
        "sv": "Byt till den och spela in",
        "es": "Cambiar a él y grabar",
        "de": "Dorthin wechseln und aufnehmen",
        "fr": "Passer dessus et enregistrer",
        "zh": "切换到它并录制",
        "ru": "Переключиться и записать",
        "th": "สลับไปและบันทึก",
    },
    "rec_record_background": {
        "en": "Record in the background (needs a second connection)",
        "sv": "Spela in i bakgrunden (kräver en andra anslutning)",
        "es": "Grabar en segundo plano (necesita una segunda conexión)",
        "de": "Im Hintergrund aufnehmen (braucht eine zweite Verbindung)",
        "fr": "Enregistrer en arrière-plan (nécessite une 2e connexion)",
        "zh": "在后台录制（需要第二个连接）",
        "ru": "Записать в фоне (нужно второе подключение)",
        "th": "บันทึกในพื้นหลัง (ต้องใช้การเชื่อมต่อที่สอง)",
    },

    # ── Chromecast dialog ─────────────────────────────────────────────────
    "cast_title": {
        "en": "Cast to Chromecast", "sv": "Casta till Chromecast",
        "es": "Enviar a Chromecast", "de": "An Chromecast streamen",
        "fr": "Diffuser vers Chromecast", "zh": "投放到 Chromecast",
        "ru": "Транслировать на Chromecast", "th": "แคสต์ไปยัง Chromecast",
    },
    "cast_scanning": {
        "en": "Scanning for Chromecast devices…",
        "sv": "Söker efter Chromecast-enheter…",
        "es": "Buscando dispositivos Chromecast…",
        "de": "Suche nach Chromecast-Geräten…",
        "fr": "Recherche d'appareils Chromecast…",
        "zh": "正在扫描 Chromecast 设备……",
        "ru": "Поиск устройств Chromecast…",
        "th": "กำลังค้นหาอุปกรณ์ Chromecast…",
    },
    "cast_rescan": {
        "en": "Rescan", "sv": "Sök igen", "es": "Reescanear",
        "de": "Erneut suchen", "fr": "Rechercher à nouveau",
        "zh": "重新扫描", "ru": "Повторить поиск", "th": "สแกนอีกครั้ง",
    },
    "cast_cast": {
        "en": "Cast", "sv": "Casta", "es": "Enviar", "de": "Streamen",
        "fr": "Diffuser", "zh": "投放", "ru": "Транслировать", "th": "แคสต์",
    },
    "cast_stop": {
        "en": "Stop casting", "sv": "Sluta casta", "es": "Detener envío",
        "de": "Streaming stoppen", "fr": "Arrêter la diffusion",
        "zh": "停止投放", "ru": "Остановить трансляцию", "th": "หยุดแคสต์",
    },
    "cast_devices_found": {
        "en": "{n} device(s) found.", "sv": "{n} enhet(er) hittades.",
        "es": "{n} dispositivo(s) encontrado(s).",
        "de": "{n} Gerät(e) gefunden.", "fr": "{n} appareil(s) trouvé(s).",
        "zh": "找到 {n} 个设备。", "ru": "Найдено устройств: {n}.",
        "th": "พบ {n} อุปกรณ์",
    },
    "cast_none_found": {
        "en": "No Chromecast devices found on this network.",
        "sv": "Inga Chromecast-enheter hittades i nätverket.",
        "es": "No se encontraron dispositivos Chromecast en esta red.",
        "de": "Keine Chromecast-Geräte in diesem Netzwerk gefunden.",
        "fr": "Aucun appareil Chromecast trouvé sur ce réseau.",
        "zh": "在此网络上未找到 Chromecast 设备。",
        "ru": "В этой сети не найдено устройств Chromecast.",
        "th": "ไม่พบอุปกรณ์ Chromecast ในเครือข่ายนี้",
    },
    "cast_scan_failed": {
        "en": "Scan failed: {msg}", "sv": "Sökning misslyckades: {msg}",
        "es": "Error de búsqueda: {msg}", "de": "Suche fehlgeschlagen: {msg}",
        "fr": "Échec de la recherche : {msg}", "zh": "扫描失败：{msg}",
        "ru": "Ошибка поиска: {msg}", "th": "สแกนล้มเหลว: {msg}",
    },
    "cast_starting": {
        "en": "Starting cast to {name}…",
        "sv": "Startar casting till {name}…",
        "es": "Iniciando envío a {name}…",
        "de": "Streaming zu {name} wird gestartet…",
        "fr": "Démarrage de la diffusion vers {name}…",
        "zh": "正在开始投放到 {name}……",
        "ru": "Запуск трансляции на {name}…",
        "th": "กำลังเริ่มแคสต์ไปยัง {name}…",
    },
    "cast_casting_to": {
        "en": "Casting to {name}.", "sv": "Castar till {name}.",
        "es": "Enviando a {name}.", "de": "Streaming zu {name}.",
        "fr": "Diffusion vers {name}.", "zh": "正在投放到 {name}。",
        "ru": "Трансляция на {name}.", "th": "กำลังแคสต์ไปยัง {name}",
    },
    "cast_failed": {
        "en": "Cast failed: {msg}", "sv": "Casting misslyckades: {msg}",
        "es": "Error al enviar: {msg}", "de": "Streaming fehlgeschlagen: {msg}",
        "fr": "Échec de la diffusion : {msg}", "zh": "投放失败：{msg}",
        "ru": "Ошибка трансляции: {msg}", "th": "แคสต์ล้มเหลว: {msg}",
    },
    "cast_stopped": {
        "en": "Casting stopped.", "sv": "Casting stoppad.",
        "es": "Envío detenido.", "de": "Streaming gestoppt.",
        "fr": "Diffusion arrêtée.", "zh": "投放已停止。",
        "ru": "Трансляция остановлена.", "th": "หยุดแคสต์แล้ว",
    },
    "cast_stop_failed": {
        "en": "Stop failed: {msg}", "sv": "Kunde inte stoppa: {msg}",
        "es": "Error al detener: {msg}", "de": "Stoppen fehlgeschlagen: {msg}",
        "fr": "Échec de l'arrêt : {msg}", "zh": "停止失败：{msg}",
        "ru": "Не удалось остановить: {msg}", "th": "หยุดล้มเหลว: {msg}",
    },

    # ── Playlist dialog extras ────────────────────────────────────────────
    "playlist_msg_title": {
        "en": "Playlist", "sv": "Spellista", "es": "Lista", "de": "Playlist",
        "fr": "Liste", "zh": "播放列表", "ru": "Плейлист", "th": "เพลย์ลิสต์",
    },
    "playlist_name_placeholder": {
        "en": "e.g. My provider", "sv": "t.ex. Min leverantör",
        "es": "p. ej. Mi proveedor", "de": "z. B. Mein Anbieter",
        "fr": "p. ex. Mon fournisseur", "zh": "例如：我的服务商",
        "ru": "напр. Мой провайдер", "th": "เช่น ผู้ให้บริการของฉัน",
    },
    "playlist_epg_placeholder": {
        "en": "optional - overrides the provider's xmltv.php",
        "sv": "valfritt - ersätter leverantörens xmltv.php",
        "es": "opcional: reemplaza el xmltv.php del proveedor",
        "de": "optional - ersetzt die xmltv.php des Anbieters",
        "fr": "facultatif - remplace le xmltv.php du fournisseur",
        "zh": "可选——覆盖服务商的 xmltv.php",
        "ru": "необязательно - заменяет xmltv.php провайдера",
        "th": "ไม่บังคับ - แทนที่ xmltv.php ของผู้ให้บริการ",
    },

    # ── Recording scheduling messages (EPG guide) ─────────────────────────
    "rec_msg_title": {
        "en": "Record", "sv": "Spela in", "es": "Grabar", "de": "Aufnehmen",
        "fr": "Enregistrer", "zh": "录制", "ru": "Запись", "th": "บันทึก",
    },
    "rec_scheduled_status": {
        "en": "Scheduled {n} recording(s) - see Recordings → Upcoming",
        "sv": "Schemalade {n} inspelning(ar) - se Inspelningar → Kommande",
        "es": "Programadas {n} grabación(es) - ve a Grabaciones → Próximas",
        "de": "{n} Aufnahme(n) geplant - siehe Aufnahmen → Anstehend",
        "fr": "{n} enregistrement(s) programmé(s) - voir Enregistrements → À venir",
        "zh": "已安排 {n} 个录制 - 见 录制 → 即将进行",
        "ru": "Запланировано записей: {n} - см. Записи → Предстоящие",
        "th": "กำหนดบันทึก {n} รายการ - ดูที่ การบันทึก → ที่กำลังจะมาถึง",
    },
    "rec_skipped_warning": {
        "en": "{n} programme(s) could not be scheduled: missing channel "
              "stream id.",
        "sv": "{n} program kunde inte schemaläggas: kanalens stream-id "
              "saknas.",
        "es": "No se pudieron programar {n} programa(s): falta el id de "
              "transmisión del canal.",
        "de": "{n} Sendung(en) konnten nicht geplant werden: fehlende "
              "Kanal-Stream-ID.",
        "fr": "{n} programme(s) n'ont pas pu être programmés : identifiant "
              "de flux de chaîne manquant.",
        "zh": "{n} 个节目无法安排：缺少频道流 ID。",
        "ru": "Не удалось запланировать программ ({n}): отсутствует stream id "
              "канала.",
        "th": "ไม่สามารถกำหนด {n} รายการได้: ไม่มี stream id ของช่อง",
    },

    # ── Content manager dialog ────────────────────────────────────────────
    "cm_title": {
        "en": "Manage categories", "sv": "Hantera kategorier",
        "es": "Gestionar categorías", "de": "Kategorien verwalten",
        "fr": "Gérer les catégories", "zh": "管理分类",
        "ru": "Управление категориями", "th": "จัดการหมวดหมู่",
    },
    "cm_hint": {
        "en": "Hidden categories disappear from the sidebar and their "
              "channels are left out of 'All'. Locked categories need the "
              "parental PIN to open.",
        "sv": "Dolda kategorier försvinner från sidofältet och deras kanaler "
              "utelämnas ur 'Alla'. Låsta kategorier kräver föräldra-PIN för "
              "att öppnas.",
        "es": "Las categorías ocultas desaparecen de la barra lateral y sus "
              "canales quedan fuera de 'Todo'. Las categorías bloqueadas "
              "necesitan el PIN parental para abrirse.",
        "de": "Ausgeblendete Kategorien verschwinden aus der Seitenleiste und "
              "ihre Kanäle fehlen in 'Alle'. Gesperrte Kategorien benötigen "
              "die Kindersicherungs-PIN.",
        "fr": "Les catégories masquées disparaissent de la barre latérale et "
              "leurs chaînes sont exclues de « Tout ». Les catégories "
              "verrouillées nécessitent le code parental.",
        "zh": "隐藏的分类会从侧边栏消失，其频道也会被排除在“全部”之外。"
              "锁定的分类需要家长 PIN 才能打开。",
        "ru": "Скрытые категории исчезают из боковой панели, а их каналы не "
              "входят в «Все». Заблокированные категории открываются по "
              "родительскому PIN-коду.",
        "th": "หมวดหมู่ที่ซ่อนจะหายไปจากแถบข้างและช่องจะไม่รวมอยู่ใน 'ทั้งหมด' "
              "หมวดหมู่ที่ล็อกต้องใช้ PIN ผู้ปกครองเพื่อเปิด",
    },
    "cm_rename": {
        "en": "Rename…", "sv": "Byt namn…", "es": "Renombrar…",
        "de": "Umbenennen…", "fr": "Renommer…", "zh": "重命名……",
        "ru": "Переименовать…", "th": "เปลี่ยนชื่อ…",
    },
    "cm_hide": {
        "en": "Hide", "sv": "Dölj", "es": "Ocultar", "de": "Ausblenden",
        "fr": "Masquer", "zh": "隐藏", "ru": "Скрыть", "th": "ซ่อน",
    },
    "cm_unhide": {
        "en": "Unhide", "sv": "Visa", "es": "Mostrar", "de": "Einblenden",
        "fr": "Afficher", "zh": "取消隐藏", "ru": "Показать", "th": "เลิกซ่อน",
    },
    "cm_lock": {
        "en": "Lock", "sv": "Lås", "es": "Bloquear", "de": "Sperren",
        "fr": "Verrouiller", "zh": "锁定", "ru": "Заблокировать", "th": "ล็อก",
    },
    "cm_unlock": {
        "en": "Unlock", "sv": "Lås upp", "es": "Desbloquear",
        "de": "Entsperren", "fr": "Déverrouiller", "zh": "解锁",
        "ru": "Разблокировать", "th": "ปลดล็อก",
    },
    "cm_flag_hidden": {
        "en": "hidden", "sv": "dold", "es": "oculta", "de": "ausgeblendet",
        "fr": "masquée", "zh": "已隐藏", "ru": "скрыта", "th": "ซ่อนอยู่",
    },
    "cm_flag_locked": {
        "en": "locked", "sv": "låst", "es": "bloqueada", "de": "gesperrt",
        "fr": "verrouillée", "zh": "已锁定", "ru": "заблокирована",
        "th": "ล็อกอยู่",
    },
    "cm_rename_title": {
        "en": "Rename category", "sv": "Byt namn på kategori",
        "es": "Renombrar categoría", "de": "Kategorie umbenennen",
        "fr": "Renommer la catégorie", "zh": "重命名分类",
        "ru": "Переименовать категорию", "th": "เปลี่ยนชื่อหมวดหมู่",
    },
    "cm_new_name": {
        "en": "New name:", "sv": "Nytt namn:", "es": "Nuevo nombre:",
        "de": "Neuer Name:", "fr": "Nouveau nom :", "zh": "新名称：",
        "ru": "Новое имя:", "th": "ชื่อใหม่:",
    },
    "pin_new_prompt": {
        "en": "New PIN:", "sv": "Ny PIN:", "es": "Nuevo PIN:",
        "de": "Neue PIN:", "fr": "Nouveau code PIN :", "zh": "新 PIN：",
        "ru": "Новый PIN:", "th": "PIN ใหม่:",
    },
    "pin_choose_prompt": {
        "en": "No PIN is set yet. Choose a PIN to protect locked content:",
        "sv": "Ingen PIN är angiven ännu. Välj en PIN för att skydda låst "
              "innehåll:",
        "es": "Aún no hay PIN. Elige un PIN para proteger el contenido "
              "bloqueado:",
        "de": "Es ist noch keine PIN gesetzt. Wähle eine PIN, um gesperrte "
              "Inhalte zu schützen:",
        "fr": "Aucun code PIN défini. Choisissez un code PIN pour protéger le "
              "contenu verrouillé :",
        "zh": "尚未设置 PIN。请选择一个 PIN 以保护锁定的内容：",
        "ru": "PIN ещё не задан. Выберите PIN для защиты заблокированного "
              "содержимого:",
        "th": "ยังไม่ได้ตั้ง PIN เลือก PIN เพื่อปกป้องเนื้อหาที่ถูกล็อก:",
    },
    "set_cat_icon_title": {
        "en": "Set category icon", "sv": "Ange kategoriikon",
        "es": "Establecer icono de categoría", "de": "Kategoriesymbol festlegen",
        "fr": "Définir l'icône de catégorie", "zh": "设置分类图标",
        "ru": "Задать значок категории", "th": "ตั้งไอคอนหมวดหมู่",
    },
    "set_cat_icon_prompt": {
        "en": "Enter an emoji or short text (leave blank to remove):",
        "sv": "Ange en emoji eller kort text (lämna tomt för att ta bort):",
        "es": "Introduce un emoji o texto corto (deja vacío para quitar):",
        "de": "Emoji oder kurzen Text eingeben (leer lassen zum Entfernen):",
        "fr": "Saisissez un emoji ou un texte court (laisser vide pour "
              "supprimer) :",
        "zh": "输入表情或简短文字（留空则移除）：",
        "ru": "Введите эмодзи или короткий текст (пусто — убрать):",
        "th": "ป้อนอิโมจิหรือข้อความสั้น (เว้นว่างเพื่อลบ):",
    },
    "size_value_placeholder": {
        "en": "e.g. 75", "sv": "t.ex. 75", "es": "p. ej. 75", "de": "z. B. 75",
        "fr": "p. ex. 75", "zh": "例如 75", "ru": "напр. 75", "th": "เช่น 75",
    },

    # ── Channel / category context menus (extras) ────────────────────────
    "ctx_reset_color": {
        "en": "Reset color", "sv": "Återställ färg",
        "es": "Restablecer color", "de": "Farbe zurücksetzen",
        "fr": "Réinitialiser la couleur", "zh": "重置颜色",
        "ru": "Сбросить цвет", "th": "รีเซ็ตสี",
    },
    "ctx_reset_channel": {
        "en": "Reset this channel's customizations",
        "sv": "Återställ kanalens anpassningar",
        "es": "Restablecer las personalizaciones de este canal",
        "de": "Anpassungen dieses Kanals zurücksetzen",
        "fr": "Réinitialiser les personnalisations de cette chaîne",
        "zh": "重置此频道的自定义设置", "ru": "Сбросить настройки этого канала",
        "th": "รีเซ็ตการปรับแต่งของช่องนี้",
    },
    "ctx_restore_defaults": {
        "en": "Restore default channels…",
        "sv": "Återställ standardkanaler…",
        "es": "Restaurar canales predeterminados…",
        "de": "Standardkanäle wiederherstellen…",
        "fr": "Restaurer les chaînes par défaut…",
        "zh": "恢复默认频道…", "ru": "Восстановить каналы по умолчанию…",
        "th": "คืนค่าช่องเริ่มต้น…",
    },
    "settings_image_cache_label": {
        "en": "Image cache on disk: {size}",
        "sv": "Bildcache på disk: {size}",
        "es": "Caché de imágenes en disco: {size}",
        "de": "Bildcache auf Datenträger: {size}",
        "fr": "Cache d'images sur disque : {size}",
        "zh": "磁盘上的图像缓存: {size}",
        "ru": "Кэш изображений на диске: {size}",
        "th": "แคชรูปภาพบนดิสก์: {size}",
    },
    "settings_image_cache_clear": {
        "en": "Clear image cache",
        "sv": "Rensa bildcache",
        "es": "Vaciar caché de imágenes",
        "de": "Bildcache leeren",
        "fr": "Vider le cache d'images",
        "zh": "清除图像缓存",
        "ru": "Очистить кэш изображений",
        "th": "ล้างแคชรูปภาพ",
    },
    "settings_image_cache_hint": {
        "en": "Cached posters and channel logos on disk. Cleared covers "
              "reload from the network on next scroll.",
        "sv": "Cachade affischer och kanallogotyper på disk. Rensade "
              "covers laddas om från nätverket vid nästa scroll.",
        "es": "Pósteres y logotipos de canales en caché en disco. Los "
              "elementos borrados se recargan desde la red al desplazarse.",
        "de": "Zwischengespeicherte Poster und Kanallogos auf dem "
              "Datenträger. Gelöschte Cover werden beim nächsten Scrollen "
              "aus dem Netzwerk nachgeladen.",
        "fr": "Affiches et logos de chaînes en cache sur disque. Les "
              "éléments effacés se rechargent depuis le réseau au "
              "prochain défilement.",
        "zh": "磁盘上缓存的海报和频道徽标。清除后下次滚动时将从网络重新加载。",
        "ru": "Кэшированные постеры и логотипы каналов на диске. "
              "Очищенные заново загрузятся из сети при следующей прокрутке.",
        "th": "โปสเตอร์และโลโก้ช่องที่แคชไว้บนดิสก์ "
              "รายการที่ถูกล้างจะโหลดใหม่จากเครือข่ายเมื่อเลื่อนครั้งถัดไป",
    },
    "ctx_mark_watched": {
        "en": "Mark as watched (local)",
        "sv": "Markera som sedd (lokalt)",
        "es": "Marcar como visto (local)",
        "de": "Als gesehen markieren (lokal)",
        "fr": "Marquer comme vu (local)",
        "zh": "标记为已观看（本地）",
        "ru": "Отметить как просмотрено (локально)",
        "th": "ทำเครื่องหมายว่าดูแล้ว (ในเครื่อง)",
    },
    "ctx_mark_watched_trakt": {
        "en": "Mark as watched + Trakt",
        "sv": "Markera som sedd + Trakt",
        "es": "Marcar como visto + Trakt",
        "de": "Als gesehen markieren + Trakt",
        "fr": "Marquer comme vu + Trakt",
        "zh": "标记为已观看 + Trakt",
        "ru": "Отметить как просмотрено + Trakt",
        "th": "ทำเครื่องหมายว่าดูแล้ว + Trakt",
    },
    "ctx_unmark_watched": {
        "en": "Unmark as watched (local)",
        "sv": "Ta bort som sedd (lokalt)",
        "es": "Desmarcar como visto (local)",
        "de": "Als ungesehen markieren (lokal)",
        "fr": "Retirer comme vu (local)",
        "zh": "取消已观看标记（本地）",
        "ru": "Снять отметку о просмотре (локально)",
        "th": "ยกเลิกการทำเครื่องหมายว่าดูแล้ว (ในเครื่อง)",
    },
    "ctx_unmark_watched_trakt": {
        "en": "Unmark as watched + Trakt",
        "sv": "Ta bort som sedd + Trakt",
        "es": "Desmarcar como visto + Trakt",
        "de": "Als ungesehen markieren + Trakt",
        "fr": "Retirer comme vu + Trakt",
        "zh": "取消已观看标记 + Trakt",
        "ru": "Снять отметку о просмотре + Trakt",
        "th": "ยกเลิกการทำเครื่องหมายว่าดูแล้ว + Trakt",
    },
    "ctx_watchlist_add": {
        "en": "Add to Watch Later (local)",
        "sv": "Lägg till i Titta senare (lokalt)",
        "es": "Añadir a Ver más tarde (local)",
        "de": "Zu 'Später ansehen' hinzufügen (lokal)",
        "fr": "Ajouter à Regarder plus tard (local)",
        "zh": "添加到稍后观看（本地）",
        "ru": "Добавить в «Посмотреть позже» (локально)",
        "th": "เพิ่มไปที่ดูภายหลัง (ในเครื่อง)",
    },
    "ctx_watchlist_add_trakt": {
        "en": "Add to Watch Later + Trakt",
        "sv": "Lägg till i Titta senare + Trakt",
        "es": "Añadir a Ver más tarde + Trakt",
        "de": "Zu 'Später ansehen' hinzufügen + Trakt",
        "fr": "Ajouter à Regarder plus tard + Trakt",
        "zh": "添加到稍后观看 + Trakt",
        "ru": "Добавить в «Посмотреть позже» + Trakt",
        "th": "เพิ่มไปที่ดูภายหลัง + Trakt",
    },
    "ctx_watchlist_remove": {
        "en": "Remove from Watch Later (local)",
        "sv": "Ta bort från Titta senare (lokalt)",
        "es": "Quitar de Ver más tarde (local)",
        "de": "Aus 'Später ansehen' entfernen (lokal)",
        "fr": "Retirer de Regarder plus tard (local)",
        "zh": "从稍后观看移除（本地）",
        "ru": "Убрать из «Посмотреть позже» (локально)",
        "th": "ลบจากดูภายหลัง (ในเครื่อง)",
    },
    "ctx_watchlist_remove_trakt": {
        "en": "Remove from Watch Later + Trakt",
        "sv": "Ta bort från Titta senare + Trakt",
        "es": "Quitar de Ver más tarde + Trakt",
        "de": "Aus 'Später ansehen' entfernen + Trakt",
        "fr": "Retirer de Regarder plus tard + Trakt",
        "zh": "从稍后观看移除 + Trakt",
        "ru": "Убрать из «Посмотреть позже» + Trakt",
        "th": "ลบจากดูภายหลัง + Trakt",
    },
    "ctx_match_tmdb": {
        "en": "Match on TMDB…",
        "sv": "Matcha mot TMDB…",
        "es": "Emparejar con TMDB…",
        "de": "Mit TMDB abgleichen…",
        "fr": "Associer à TMDB…",
        "zh": "在 TMDB 上匹配…",
        "ru": "Сопоставить с TMDB…",
        "th": "จับคู่กับ TMDB…",
    },
    "tmdb_match_title": {
        "en": "Find on TMDB",
        "sv": "Hitta på TMDB",
        "es": "Buscar en TMDB",
        "de": "Auf TMDB suchen",
        "fr": "Rechercher sur TMDB",
        "zh": "在 TMDB 上查找",
        "ru": "Найти на TMDB",
        "th": "ค้นหาใน TMDB",
    },
    "tmdb_match_hint": {
        "en": "Search TMDB and pick the right poster/metadata. Your choice "
              "is remembered and overrides the automatic match.",
        "sv": "Sök på TMDB och välj rätt affisch/metadata. Ditt val sparas "
              "och åsidosätter den automatiska matchningen.",
        "es": "Busca en TMDB y elige el póster/metadatos correctos. Tu "
              "elección se recuerda y anula la coincidencia automática.",
        "de": "TMDB durchsuchen und das richtige Poster/Metadaten wählen. "
              "Deine Auswahl wird gespeichert und überschreibt die "
              "automatische Zuordnung.",
        "fr": "Recherchez sur TMDB et choisissez la bonne "
              "affiche/métadonnées. Votre choix est mémorisé et remplace la "
              "correspondance automatique.",
        "zh": "搜索 TMDB 并选择正确的海报/元数据。您的选择将被记住并覆盖自动匹配。",
        "ru": "Найдите на TMDB и выберите правильный постер/метаданные. Ваш "
              "выбор запоминается и заменяет автоматическое совпадение.",
        "th": "ค้นหา TMDB และเลือกโปสเตอร์/ข้อมูลที่ถูกต้อง "
              "การเลือกของคุณจะถูกจดจำและแทนที่การจับคู่อัตโนมัติ",
    },
    "tmdb_search_placeholder": {
        "en": "Title",
        "sv": "Titel",
        "es": "Título",
        "de": "Titel",
        "fr": "Titre",
        "zh": "标题",
        "ru": "Название",
        "th": "ชื่อเรื่อง",
    },
    "tmdb_year_placeholder": {
        "en": "Year",
        "sv": "År",
        "es": "Año",
        "de": "Jahr",
        "fr": "Année",
        "zh": "年份",
        "ru": "Год",
        "th": "ปี",
    },
    "tmdb_search_btn": {
        "en": "Search",
        "sv": "Sök",
        "es": "Buscar",
        "de": "Suchen",
        "fr": "Rechercher",
        "zh": "搜索",
        "ru": "Поиск",
        "th": "ค้นหา",
    },
    "tmdb_enter_title": {
        "en": "Enter a title to search.",
        "sv": "Ange en titel att söka efter.",
        "es": "Introduce un título para buscar.",
        "de": "Titel eingeben, um zu suchen.",
        "fr": "Saisissez un titre à rechercher.",
        "zh": "输入要搜索的标题。",
        "ru": "Введите название для поиска.",
        "th": "ป้อนชื่อเรื่องเพื่อค้นหา",
    },
    "tmdb_searching": {
        "en": "Searching…",
        "sv": "Söker…",
        "es": "Buscando…",
        "de": "Suche läuft…",
        "fr": "Recherche…",
        "zh": "正在搜索…",
        "ru": "Поиск…",
        "th": "กำลังค้นหา…",
    },
    "tmdb_search_failed": {
        "en": "Search failed: {msg}",
        "sv": "Sökningen misslyckades: {msg}",
        "es": "Búsqueda fallida: {msg}",
        "de": "Suche fehlgeschlagen: {msg}",
        "fr": "Échec de la recherche : {msg}",
        "zh": "搜索失败: {msg}",
        "ru": "Не удалось выполнить поиск: {msg}",
        "th": "การค้นหาล้มเหลว: {msg}",
    },
    "tmdb_no_results": {
        "en": "No matches found.",
        "sv": "Inga träffar.",
        "es": "No se encontraron coincidencias.",
        "de": "Keine Treffer gefunden.",
        "fr": "Aucun résultat trouvé.",
        "zh": "未找到匹配项。",
        "ru": "Совпадений не найдено.",
        "th": "ไม่พบรายการที่ตรงกัน",
    },
    "tmdb_n_matches": {
        "en": "{n} matches",
        "sv": "{n} träffar",
        "es": "{n} coincidencias",
        "de": "{n} Treffer",
        "fr": "{n} résultats",
        "zh": "{n} 个匹配",
        "ru": "{n} совпадений",
        "th": "{n} รายการ",
    },
    "tmdb_use_this": {
        "en": "Use this",
        "sv": "Använd denna",
        "es": "Usar este",
        "de": "Diesen verwenden",
        "fr": "Utiliser celui-ci",
        "zh": "使用此项",
        "ru": "Использовать это",
        "th": "ใช้รายการนี้",
    },
    "tmdb_clear_override": {
        "en": "Clear override",
        "sv": "Rensa åsidosättning",
        "es": "Borrar anulación",
        "de": "Überschreibung entfernen",
        "fr": "Effacer la substitution",
        "zh": "清除覆盖",
        "ru": "Сбросить переопределение",
        "th": "ล้างการแทนที่",
    },
    "ctx_remove_group": {
        "en": 'Remove group "{group}"', "sv": 'Ta bort grupp "{group}"',
        "es": 'Eliminar grupo "{group}"', "de": 'Gruppe "{group}" entfernen',
        "fr": 'Supprimer le groupe « {group} »', "zh": '移除组“{group}”',
        "ru": 'Удалить группу «{group}»', "th": 'ลบกลุ่ม "{group}"',
    },
    "ctx_unlock_group": {
        "en": "Unlock group (remove protection)",
        "sv": "Lås upp grupp (ta bort skydd)",
        "es": "Desbloquear grupo (quitar protección)",
        "de": "Gruppe entsperren (Schutz entfernen)",
        "fr": "Déverrouiller le groupe (retirer la protection)",
        "zh": "解锁组（移除保护）",
        "ru": "Разблокировать группу (снять защиту)",
        "th": "ปลดล็อกกลุ่ม (ลบการป้องกัน)",
    },
    "ctx_lock_group": {
        "en": "Lock group (parental control)",
        "sv": "Lås grupp (föräldrakontroll)",
        "es": "Bloquear grupo (control parental)",
        "de": "Gruppe sperren (Kindersicherung)",
        "fr": "Verrouiller le groupe (contrôle parental)",
        "zh": "锁定组（家长控制）",
        "ru": "Заблокировать группу (родит. контроль)",
        "th": "ล็อกกลุ่ม (การควบคุมโดยผู้ปกครอง)",
    },
    "ctx_rename_category": {
        "en": "Rename category…", "sv": "Byt namn på kategori…",
        "es": "Renombrar categoría…", "de": "Kategorie umbenennen…",
        "fr": "Renommer la catégorie…", "zh": "重命名分类…",
        "ru": "Переименовать категорию…", "th": "เปลี่ยนชื่อหมวดหมู่…",
    },
    "ctx_set_icon": {
        "en": "Set icon…", "sv": "Ange ikon…", "es": "Establecer icono…",
        "de": "Symbol festlegen…", "fr": "Définir l'icône…",
        "zh": "设置图标…", "ru": "Задать значок…",
        "th": "ตั้งไอคอน…",
    },
    "ctx_set_color": {
        "en": "Set color", "sv": "Ange färg", "es": "Establecer color",
        "de": "Farbe festlegen", "fr": "Définir la couleur", "zh": "设置颜色",
        "ru": "Задать цвет", "th": "ตั้งสี",
    },
    "ctx_set_bg_color": {
        "en": "Set background", "sv": "Ange bakgrund",
        "es": "Establecer fondo", "de": "Hintergrund festlegen",
        "fr": "Définir le fond", "zh": "设置背景", "ru": "Задать фон",
        "th": "ตั้งพื้นหลัง",
    },
    "color_default": {
        "en": "Default", "sv": "Standard", "es": "Predeterminado",
        "de": "Standard", "fr": "Défaut", "zh": "默认", "ru": "По умолчанию",
        "th": "ค่าเริ่มต้น",
    },
    "ctx_hide_category": {
        "en": "Hide category", "sv": "Dölj kategori", "es": "Ocultar categoría",
        "de": "Kategorie ausblenden", "fr": "Masquer la catégorie",
        "zh": "隐藏分类", "ru": "Скрыть категорию", "th": "ซ่อนหมวดหมู่",
    },
    "ctx_unlock_category": {
        "en": "Unlock category (remove protection)",
        "sv": "Lås upp kategori (ta bort skydd)",
        "es": "Desbloquear categoría (quitar protección)",
        "de": "Kategorie entsperren (Schutz entfernen)",
        "fr": "Déverrouiller la catégorie (retirer la protection)",
        "zh": "解锁分类（移除保护）",
        "ru": "Разблокировать категорию (снять защиту)",
        "th": "ปลดล็อกหมวดหมู่ (ลบการป้องกัน)",
    },
    "ctx_lock_category": {
        "en": "Lock category (parental control)",
        "sv": "Lås kategori (föräldrakontroll)",
        "es": "Bloquear categoría (control parental)",
        "de": "Kategorie sperren (Kindersicherung)",
        "fr": "Verrouiller la catégorie (contrôle parental)",
        "zh": "锁定分类（家长控制）",
        "ru": "Заблокировать категорию (родит. контроль)",
        "th": "ล็อกหมวดหมู่ (การควบคุมโดยผู้ปกครอง)",
    },
    "ctx_play_in_vlc": {
        "en": "Play in VLC", "sv": "Spela i VLC", "es": "Reproducir en VLC",
        "de": "In VLC abspielen", "fr": "Lire dans VLC", "zh": "在 VLC 中播放",
        "ru": "Воспроизвести в VLC", "th": "เล่นใน VLC",
    },
    "cat_all": {
        "en": "All", "sv": "Alla", "es": "Todo", "de": "Alle", "fr": "Tout",
        "zh": "全部", "ru": "Все", "th": "ทั้งหมด",
    },
    "cat_continue": {
        "en": "Continue watching", "sv": "Fortsätt titta",
        "es": "Continuar viendo", "de": "Weiterschauen",
        "fr": "Reprendre", "zh": "继续观看",
        "ru": "Продолжить просмотр", "th": "ดูต่อ",
    },
    "cat_recent": {
        "en": "Recently added", "sv": "Senast tillagt",
        "es": "Añadido recientemente", "de": "Kürzlich hinzugefügt",
        "fr": "Ajouts récents", "zh": "最近添加",
        "ru": "Недавно добавленные", "th": "เพิ่มล่าสุด",
    },
    "ctx_continue_remove": {
        "en": "Remove from Continue watching",
        "sv": "Ta bort från Fortsätt titta",
        "es": "Quitar de Continuar viendo",
        "de": "Aus Weiterschauen entfernen",
        "fr": "Retirer de Reprendre",
        "zh": "从继续观看中移除",
        "ru": "Убрать из «Продолжить просмотр»",
        "th": "ลบออกจากดูต่อ",
    },

    # ── Durations (reused by record + timeshift menus) ────────────────────
    "dur_30min": {"en": "30 min", "sv": "30 min", "es": "30 min",
                  "de": "30 Min", "fr": "30 min", "zh": "30 分钟",
                  "ru": "30 мин", "th": "30 นาที"},
    "dur_1h": {"en": "1 hour", "sv": "1 timme", "es": "1 hora",
               "de": "1 Stunde", "fr": "1 heure", "zh": "1 小时",
               "ru": "1 час", "th": "1 ชั่วโมง"},
    "dur_2h": {"en": "2 hours", "sv": "2 timmar", "es": "2 horas",
               "de": "2 Stunden", "fr": "2 heures", "zh": "2 小时",
               "ru": "2 часа", "th": "2 ชั่วโมง"},
    "dur_4h": {"en": "4 hours", "sv": "4 timmar", "es": "4 horas",
               "de": "4 Stunden", "fr": "4 heures", "zh": "4 小时",
               "ru": "4 часа", "th": "4 ชั่วโมง"},
    "dur_6h": {"en": "6 hours", "sv": "6 timmar", "es": "6 horas",
               "de": "6 Stunden", "fr": "6 heures", "zh": "6 小时",
               "ru": "6 часов", "th": "6 ชั่วโมง"},
    "dur_12h": {"en": "12 hours", "sv": "12 timmar", "es": "12 horas",
                "de": "12 Stunden", "fr": "12 heures", "zh": "12 小时",
                "ru": "12 часов", "th": "12 ชั่วโมง"},
    "dur_1d": {"en": "1 day", "sv": "1 dag", "es": "1 día", "de": "1 Tag",
               "fr": "1 jour", "zh": "1 天", "ru": "1 день", "th": "1 วัน"},
    "dur_2d": {"en": "2 days", "sv": "2 dagar", "es": "2 días",
               "de": "2 Tage", "fr": "2 jours", "zh": "2 天",
               "ru": "2 дня", "th": "2 วัน"},
    "dur_3d": {"en": "3 days", "sv": "3 dagar", "es": "3 días",
               "de": "3 Tage", "fr": "3 jours", "zh": "3 天",
               "ru": "3 дня", "th": "3 วัน"},
    "dur_5d": {"en": "5 days", "sv": "5 dagar", "es": "5 días",
               "de": "5 Tage", "fr": "5 jours", "zh": "5 天",
               "ru": "5 дней", "th": "5 วัน"},
    "dur_7d": {"en": "7 days", "sv": "7 dagar", "es": "7 días",
               "de": "7 Tage", "fr": "7 jours", "zh": "7 天",
               "ru": "7 дней", "th": "7 วัน"},

    # ── Recording menu / recordings context menu (extras) ─────────────────
    "rec_size_limit_session": {
        "en": "Size limit (this session)",
        "sv": "Storleksgräns (denna session)",
        "es": "Límite de tamaño (esta sesión)",
        "de": "Größenlimit (diese Sitzung)",
        "fr": "Limite de taille (cette session)",
        "zh": "大小限制（本次会话）", "ru": "Лимит размера (эта сессия)",
        "th": "จำกัดขนาด (เซสชันนี้)",
    },
    "rec_stop_named_since": {
        "en": "Stop recording: {title} (since {since})",
        "sv": "Stoppa inspelning: {title} (sedan {since})",
        "es": "Detener grabación: {title} (desde {since})",
        "de": "Aufnahme stoppen: {title} (seit {since})",
        "fr": "Arrêter l'enregistrement : {title} (depuis {since})",
        "zh": "停止录制：{title}（自 {since}）",
        "ru": "Остановить запись: {title} (с {since})",
        "th": "หยุดบันทึก: {title} (ตั้งแต่ {since})",
    },
    "rec_edit_times": {
        "en": "Edit start/stop time…",
        "sv": "Redigera start-/stopptid…",
        "es": "Editar hora de inicio/fin…",
        "de": "Start-/Stoppzeit bearbeiten…",
        "fr": "Modifier l'heure de début/fin…",
        "zh": "编辑开始/停止时间…", "ru": "Изменить время начала/окончания…",
        "th": "แก้ไขเวลาเริ่ม/หยุด…",
    },
    "rec_cancel_scheduled": {
        "en": "Cancel scheduled recording",
        "sv": "Avbryt schemalagd inspelning",
        "es": "Cancelar grabación programada",
        "de": "Geplante Aufnahme abbrechen",
        "fr": "Annuler l'enregistrement programmé",
        "zh": "取消计划录制", "ru": "Отменить запланированную запись",
        "th": "ยกเลิกการบันทึกตามกำหนด",
    },
    "rec_remove_from_list": {
        "en": "Remove selected from list",
        "sv": "Ta bort markerade från listan",
        "es": "Eliminar seleccionado de la lista",
        "de": "Ausgewählte aus Liste entfernen",
        "fr": "Retirer la sélection de la liste",
        "zh": "从列表中移除所选", "ru": "Удалить выбранное из списка",
        "th": "ลบที่เลือกออกจากรายการ",
    },
    "rec_clear_finished": {
        "en": "Clear all finished from list",
        "sv": "Rensa alla färdiga från listan",
        "es": "Borrar todas las finalizadas de la lista",
        "de": "Alle abgeschlossenen aus Liste entfernen",
        "fr": "Effacer tous les terminés de la liste",
        "zh": "从列表中清除所有已完成", "ru": "Очистить все завершённые из списка",
        "th": "ล้างรายการที่เสร็จแล้วทั้งหมด",
    },
    "rec_move_n": {
        "en": "Move {n} recordings to", "sv": "Flytta {n} inspelningar till",
        "es": "Mover {n} grabaciones a", "de": "{n} Aufnahmen verschieben nach",
        "fr": "Déplacer {n} enregistrements vers", "zh": "将 {n} 个录制移动到",
        "ru": "Переместить {n} записей в", "th": "ย้าย {n} การบันทึกไปยัง",
    },
    "rec_move_root": {
        "en": "(Recordings folder)", "sv": "(Inspelningsmapp)",
        "es": "(Carpeta de grabaciones)", "de": "(Aufnahmeordner)",
        "fr": "(Dossier des enregistrements)", "zh": "（录制文件夹）",
        "ru": "(Папка записей)", "th": "(โฟลเดอร์การบันทึก)",
    },
    "rec_delete_n": {
        "en": "Delete {n} recordings", "sv": "Radera {n} inspelningar",
        "es": "Eliminar {n} grabaciones", "de": "{n} Aufnahmen löschen",
        "fr": "Supprimer {n} enregistrements", "zh": "删除 {n} 个录制",
        "ru": "Удалить {n} записей", "th": "ลบ {n} การบันทึก",
    },
    "rec_n_recordings": {
        "en": "{n} recordings", "sv": "{n} inspelningar",
        "es": "{n} grabaciones", "de": "{n} Aufnahmen",
        "fr": "{n} enregistrements", "zh": "{n} 个录制",
        "ru": "{n} записей", "th": "{n} การบันทึก",
    },
    "rec_change_folder": {
        "en": "Change recordings folder…",
        "sv": "Byt inspelningsmapp…",
        "es": "Cambiar carpeta de grabaciones…",
        "de": "Aufnahmeordner ändern…",
        "fr": "Changer le dossier des enregistrements…",
        "zh": "更改录制文件夹…", "ru": "Изменить папку записей…",
        "th": "เปลี่ยนโฟลเดอร์การบันทึก…",
    },
    "rec_saved_in": {
        "en": "Recordings are saved in:", "sv": "Inspelningar sparas i:",
        "es": "Las grabaciones se guardan en:",
        "de": "Aufnahmen werden gespeichert in:",
        "fr": "Les enregistrements sont enregistrés dans :",
        "zh": "录制保存在：", "ru": "Записи сохраняются в:",
        "th": "การบันทึกถูกบันทึกไว้ใน:",
    },
    "rec_custom_size_title": {
        "en": "Custom size limit", "sv": "Anpassad storleksgräns",
        "es": "Límite de tamaño personalizado",
        "de": "Benutzerdefiniertes Größenlimit",
        "fr": "Limite de taille personnalisée", "zh": "自定义大小限制",
        "ru": "Свой лимит размера", "th": "จำกัดขนาดกำหนดเอง",
    },
    "rec_stop_recording_at": {
        "en": "Stop recording at", "sv": "Stoppa inspelning kl.",
        "es": "Detener grabación a las", "de": "Aufnahme stoppen um",
        "fr": "Arrêter l'enregistrement à", "zh": "停止录制时间",
        "ru": "Остановить запись в", "th": "หยุดบันทึกเมื่อ",
    },

    # ── Timeshift menu (extras) ───────────────────────────────────────────
    "ts_go_back": {
        "en": "Go back {t}", "sv": "Gå tillbaka {t}", "es": "Retroceder {t}",
        "de": "{t} zurück", "fr": "Reculer de {t}", "zh": "回退 {t}",
        "ru": "Назад на {t}", "th": "ย้อนกลับ {t}",
    },
    "ts_watch_from_start_named": {
        "en": "Watch '{title}' from the start",
        "sv": "Se '{title}' från början",
        "es": "Ver '{title}' desde el principio",
        "de": "'{title}' von Anfang an ansehen",
        "fr": "Regarder « {title} » depuis le début",
        "zh": "从头观看“{title}”", "ru": "Смотреть «{title}» с начала",
        "th": "ดู '{title}' ตั้งแต่ต้น",
    },
    "ts_archive_depth": {
        "en": "Archive depth: {n} day(s)", "sv": "Arkivdjup: {n} dag(ar)",
        "es": "Profundidad de archivo: {n} día(s)",
        "de": "Archivtiefe: {n} Tag(e)", "fr": "Profondeur d'archive : {n} jour(s)",
        "zh": "存档深度：{n} 天", "ru": "Глубина архива: {n} дн.",
        "th": "ความลึกของคลัง: {n} วัน",
    },
    "ts_catchup_title": {
        "en": "Catch-up - {name}", "sv": "Catch-up - {name}",
        "es": "Recuperar - {name}", "de": "Nachholen - {name}",
        "fr": "Rattrapage - {name}", "zh": "回看 - {name}",
        "ru": "Архив - {name}", "th": "ดูย้อนหลัง - {name}",
    },
    "ts_loading_past": {
        "en": "Loading past programmes from the guide…",
        "sv": "Laddar tidigare program från guiden…",
        "es": "Cargando programas anteriores de la guía…",
        "de": "Vergangene Sendungen aus dem Guide werden geladen…",
        "fr": "Chargement des programmes passés depuis le guide…",
        "zh": "正在从指南加载过去的节目…",
        "ru": "Загрузка прошлых передач из гида…",
        "th": "กำลังโหลดรายการที่ผ่านมาจากคู่มือ…",
    },

    # ── Common ────────────────────────────────────────────────────────────
    "common_watch": {
        "en": "Watch", "sv": "Se", "es": "Ver", "de": "Ansehen",
        "fr": "Regarder", "zh": "观看", "ru": "Смотреть", "th": "ดู",
    },
    "btn_test": {
        "en": "Test", "sv": "Testa", "es": "Probar", "de": "Testen",
        "fr": "Tester", "zh": "测试", "ru": "Проверить", "th": "ทดสอบ",
    },
    "btn_choose_folder": {
        "en": "Choose folder…", "sv": "Välj mapp…",
        "es": "Elegir carpeta…", "de": "Ordner wählen…",
        "fr": "Choisir un dossier…", "zh": "选择文件夹…",
        "ru": "Выбрать папку…", "th": "เลือกโฟลเดอร์…",
    },
    "misc_series": {
        "en": "Series", "sv": "Serie", "es": "Serie", "de": "Serie",
        "fr": "Série", "zh": "剧集", "ru": "Сериал", "th": "ซีรีส์",
    },

    # ── Form field labels ─────────────────────────────────────────────────
    "field_start": {
        "en": "Start", "sv": "Start", "es": "Inicio", "de": "Start",
        "fr": "Début", "zh": "开始", "ru": "Начало", "th": "เริ่ม",
    },
    "field_stop": {
        "en": "Stop", "sv": "Stopp", "es": "Fin", "de": "Stopp",
        "fr": "Fin", "zh": "停止", "ru": "Стоп", "th": "หยุด",
    },
    "field_save_in": {
        "en": "Save in", "sv": "Spara i", "es": "Guardar en",
        "de": "Speichern in", "fr": "Enregistrer dans", "zh": "保存到",
        "ru": "Сохранить в", "th": "บันทึกใน",
    },
    "field_title": {
        "en": "Title", "sv": "Titel", "es": "Título", "de": "Titel",
        "fr": "Titre", "zh": "标题", "ru": "Название", "th": "ชื่อเรื่อง",
    },
    "field_client_id": {
        "en": "Client ID", "sv": "Klient-ID", "es": "ID de cliente",
        "de": "Client-ID", "fr": "ID client", "zh": "客户端 ID",
        "ru": "ID клиента", "th": "Client ID",
    },
    "field_client_secret": {
        "en": "Client Secret", "sv": "Klienthemlighet",
        "es": "Secreto de cliente", "de": "Client-Secret",
        "fr": "Secret client", "zh": "客户端密钥", "ru": "Секрет клиента",
        "th": "Client Secret",
    },

    # ── Settings extras ───────────────────────────────────────────────────
    "settings_export_tip": {
        "en": "Export all playlists to a JSON file",
        "sv": "Exportera alla spellistor till en JSON-fil",
        "es": "Exportar todas las listas a un archivo JSON",
        "de": "Alle Playlists in eine JSON-Datei exportieren",
        "fr": "Exporter toutes les listes vers un fichier JSON",
        "zh": "将所有播放列表导出为 JSON 文件",
        "ru": "Экспортировать все плейлисты в файл JSON",
        "th": "ส่งออกเพลย์ลิสต์ทั้งหมดเป็นไฟล์ JSON",
    },
    "settings_import_tip": {
        "en": "Import playlists from a JSON file",
        "sv": "Importera spellistor från en JSON-fil",
        "es": "Importar listas desde un archivo JSON",
        "de": "Playlists aus einer JSON-Datei importieren",
        "fr": "Importer des listes depuis un fichier JSON",
        "zh": "从 JSON 文件导入播放列表",
        "ru": "Импортировать плейлисты из файла JSON",
        "th": "นำเข้าเพลย์ลิสต์จากไฟล์ JSON",
    },
    "setting_artwork_source": {
        "en": "Artwork source", "sv": "Bildkälla", "es": "Fuente de imágenes",
        "de": "Bildquelle", "fr": "Source des visuels", "zh": "封面来源",
        "ru": "Источник обложек", "th": "แหล่งภาพ",
    },
    "setting_tmdb_key": {
        "en": "TMDB API key", "sv": "TMDB API-nyckel", "es": "Clave de API de TMDB",
        "de": "TMDB-API-Schlüssel", "fr": "Clé API TMDB", "zh": "TMDB API 密钥",
        "ru": "Ключ API TMDB", "th": "คีย์ API ของ TMDB",
    },
    "tmdb_key_placeholder": {
        "en": "TMDB API key (v3 auth)", "sv": "TMDB API-nyckel (v3-auth)",
        "es": "Clave de API de TMDB (auth v3)",
        "de": "TMDB-API-Schlüssel (v3-Auth)", "fr": "Clé API TMDB (auth v3)",
        "zh": "TMDB API 密钥（v3 认证）", "ru": "Ключ API TMDB (v3 auth)",
        "th": "คีย์ API ของ TMDB (v3 auth)",
    },
    "meta_src_builtin": {
        "en": "TMDB (built-in, recommended)",
        "sv": "TMDB (inbyggd, rekommenderas)",
        "es": "TMDB (integrado, recomendado)",
        "de": "TMDB (integriert, empfohlen)",
        "fr": "TMDB (intégré, recommandé)",
        "zh": "TMDB（内置，推荐）",
        "ru": "TMDB (встроенный, рекомендуется)",
        "th": "TMDB (ในตัว, แนะนำ)",
    },
    "meta_src_own": {
        "en": "TMDB (my own key)",
        "sv": "TMDB (egen nyckel)",
        "es": "TMDB (mi propia clave)",
        "de": "TMDB (eigener Schlüssel)",
        "fr": "TMDB (ma propre clé)",
        "zh": "TMDB（自己的密钥）",
        "ru": "TMDB (свой ключ)",
        "th": "TMDB (คีย์ของฉันเอง)",
    },
    "meta_src_provider": {
        "en": "Provider artwork",
        "sv": "Leverantörens bilder",
        "es": "Imágenes del proveedor",
        "de": "Anbieter-Bilder",
        "fr": "Images du fournisseur",
        "zh": "供应商图片",
        "ru": "Обложки провайдера",
        "th": "ภาพจากผู้ให้บริการ",
    },
    "tmdb_enter_key": {
        "en": "Enter an API key first.", "sv": "Ange en API-nyckel först.",
        "es": "Introduce una clave de API primero.",
        "de": "Zuerst einen API-Schlüssel eingeben.",
        "fr": "Saisissez d'abord une clé API.", "zh": "请先输入 API 密钥。",
        "ru": "Сначала введите ключ API.", "th": "กรุณาใส่คีย์ API ก่อน",
    },
    "tmdb_checking": {
        "en": "Checking…", "sv": "Kontrollerar…", "es": "Comprobando…",
        "de": "Wird geprüft…", "fr": "Vérification…", "zh": "正在检查……",
        "ru": "Проверка…", "th": "กำลังตรวจสอบ…",
    },
    "tmdb_key_works": {
        "en": "Key works.", "sv": "Nyckeln fungerar.", "es": "La clave funciona.",
        "de": "Schlüssel funktioniert.", "fr": "La clé fonctionne.",
        "zh": "密钥有效。", "ru": "Ключ работает.", "th": "คีย์ใช้งานได้",
    },
    "tmdb_key_failed": {
        "en": "Key check failed: {msg}",
        "sv": "Nyckelkontroll misslyckades: {msg}",
        "es": "Error al comprobar la clave: {msg}",
        "de": "Schlüsselprüfung fehlgeschlagen: {msg}",
        "fr": "Échec de la vérification de la clé : {msg}",
        "zh": "密钥检查失败：{msg}", "ru": "Ошибка проверки ключа: {msg}",
        "th": "ตรวจสอบคีย์ล้มเหลว: {msg}",
    },
    "pin_set_change": {
        "en": "Set / change PIN…", "sv": "Ange / byt PIN…",
        "es": "Establecer / cambiar PIN…", "de": "PIN festlegen / ändern…",
        "fr": "Définir / changer le PIN…", "zh": "设置/更改 PIN…",
        "ru": "Задать / изменить PIN…", "th": "ตั้ง / เปลี่ยน PIN…",
    },
    "pin_remove": {
        "en": "Remove PIN", "sv": "Ta bort PIN", "es": "Quitar PIN",
        "de": "PIN entfernen", "fr": "Supprimer le PIN", "zh": "移除 PIN",
        "ru": "Удалить PIN", "th": "ลบ PIN",
    },
    "pin_lock_now": {
        "en": "Lock now", "sv": "Lås nu", "es": "Bloquear ahora",
        "de": "Jetzt sperren", "fr": "Verrouiller maintenant", "zh": "立即锁定",
        "ru": "Заблокировать сейчас", "th": "ล็อกตอนนี้",
    },
    "pin_none_set": {
        "en": "No PIN set.", "sv": "Ingen PIN angiven.", "es": "Sin PIN.",
        "de": "Keine PIN festgelegt.", "fr": "Aucun PIN défini.",
        "zh": "未设置 PIN。", "ru": "PIN не задан.", "th": "ยังไม่ได้ตั้ง PIN",
    },
    "pl_mgmt_unavailable": {
        "en": "Playlist management unavailable",
        "sv": "Spellistehantering ej tillgänglig",
        "es": "Gestión de listas no disponible",
        "de": "Playlist-Verwaltung nicht verfügbar",
        "fr": "Gestion des listes indisponible",
        "zh": "播放列表管理不可用", "ru": "Управление плейлистами недоступно",
        "th": "ไม่สามารถจัดการเพลย์ลิสต์ได้",
    },

    # ── Message boxes ─────────────────────────────────────────────────────
    "msg_could_not_connect": {
        "en": "Could not connect to {name}: {msg}",
        "sv": "Kunde inte ansluta till {name}: {msg}",
        "es": "No se pudo conectar a {name}: {msg}",
        "de": "Verbindung zu {name} fehlgeschlagen: {msg}",
        "fr": "Impossible de se connecter à {name} : {msg}",
        "zh": "无法连接到 {name}：{msg}",
        "ru": "Не удалось подключиться к {name}: {msg}",
        "th": "ไม่สามารถเชื่อมต่อกับ {name}: {msg}",
    },
    "msg_connect_trakt_first": {
        "en": "Connect to Trakt first.", "sv": "Anslut till Trakt först.",
        "es": "Conéctate a Trakt primero.", "de": "Zuerst mit Trakt verbinden.",
        "fr": "Connectez-vous d'abord à Trakt.", "zh": "请先连接到 Trakt。",
        "ru": "Сначала подключитесь к Trakt.", "th": "กรุณาเชื่อมต่อ Trakt ก่อน",
    },
    "msg_cast_needs_package": {
        "en": "Casting needs the pychromecast package:\n\n"
              "  pip install pychromecast",
        "sv": "Casting kräver paketet pychromecast:\n\n"
              "  pip install pychromecast",
        "es": "El envío necesita el paquete pychromecast:\n\n"
              "  pip install pychromecast",
        "de": "Streaming benötigt das Paket pychromecast:\n\n"
              "  pip install pychromecast",
        "fr": "La diffusion nécessite le paquet pychromecast :\n\n"
              "  pip install pychromecast",
        "zh": "投放需要 pychromecast 包：\n\n  pip install pychromecast",
        "ru": "Для трансляции нужен пакет pychromecast:\n\n"
              "  pip install pychromecast",
        "th": "การแคสต์ต้องใช้แพ็กเกจ pychromecast:\n\n"
              "  pip install pychromecast",
    },
    "msg_restore_defaults_body": {
        "en": "Undo all channel renames and hides for this section and go "
              "back to the provider's original list?",
        "sv": "Ångra alla namnbyten och döljningar för den här sektionen och "
              "gå tillbaka till leverantörens ursprungslista?",
        "es": "¿Deshacer todos los renombrados y ocultaciones de esta sección "
              "y volver a la lista original del proveedor?",
        "de": "Alle Umbenennungen und Ausblendungen dieses Bereichs rückgängig "
              "machen und zur Originalliste des Anbieters zurückkehren?",
        "fr": "Annuler tous les renommages et masquages de cette section et "
              "revenir à la liste d'origine du fournisseur ?",
        "zh": "撤销此部分的所有频道重命名和隐藏，并恢复到服务商的原始列表？",
        "ru": "Отменить все переименования и скрытия каналов в этом разделе и "
              "вернуться к исходному списку провайдера?",
        "th": "เลิกทำการเปลี่ยนชื่อและซ่อนช่องทั้งหมดในส่วนนี้ "
              "และกลับไปยังรายการเดิมของผู้ให้บริการหรือไม่?",
    },
    "msg_parental_title": {
        "en": "Parental control", "sv": "Föräldrakontroll",
        "es": "Control parental", "de": "Kindersicherung",
        "fr": "Contrôle parental", "zh": "家长控制",
        "ru": "Родительский контроль", "th": "การควบคุมโดยผู้ปกครอง",
    },
    "msg_wrong_pin": {
        "en": "Wrong PIN.", "sv": "Fel PIN.", "es": "PIN incorrecto.",
        "de": "Falsche PIN.", "fr": "PIN incorrect.", "zh": "PIN 错误。",
        "ru": "Неверный PIN.", "th": "PIN ไม่ถูกต้อง",
    },
    "msg_rec_file_not_ready": {
        "en": "The recording file hasn't been created yet - try again in a "
              "few seconds.",
        "sv": "Inspelningsfilen har inte skapats ännu - försök igen om några "
              "sekunder.",
        "es": "El archivo de grabación aún no se ha creado; inténtalo de nuevo "
              "en unos segundos.",
        "de": "Die Aufnahmedatei wurde noch nicht erstellt - versuche es in "
              "ein paar Sekunden erneut.",
        "fr": "Le fichier d'enregistrement n'a pas encore été créé - réessayez "
              "dans quelques secondes.",
        "zh": "录制文件尚未创建——请几秒后重试。",
        "ru": "Файл записи ещё не создан - повторите через несколько секунд.",
        "th": "ยังไม่ได้สร้างไฟล์บันทึก - ลองอีกครั้งในอีกไม่กี่วินาที",
    },
    "msg_rec_needs_ffmpeg": {
        "en": "Recording needs ffmpeg (recommended) or mpv on the PATH.\n\n"
              "Install ffmpeg, e.g.:  sudo apt install ffmpeg",
        "sv": "Inspelning kräver ffmpeg (rekommenderas) eller mpv i PATH.\n\n"
              "Installera ffmpeg, t.ex.:  sudo apt install ffmpeg",
        "es": "La grabación necesita ffmpeg (recomendado) o mpv en el PATH.\n\n"
              "Instala ffmpeg, p. ej.:  sudo apt install ffmpeg",
        "de": "Aufnahme benötigt ffmpeg (empfohlen) oder mpv im PATH.\n\n"
              "Installiere ffmpeg, z. B.:  sudo apt install ffmpeg",
        "fr": "L'enregistrement nécessite ffmpeg (recommandé) ou mpv dans le "
              "PATH.\n\nInstallez ffmpeg, p. ex. :  sudo apt install ffmpeg",
        "zh": "录制需要 PATH 中有 ffmpeg（推荐）或 mpv。\n\n"
              "安装 ffmpeg，例如：sudo apt install ffmpeg",
        "ru": "Для записи нужен ffmpeg (рекомендуется) или mpv в PATH.\n\n"
              "Установите ffmpeg, напр.:  sudo apt install ffmpeg",
        "th": "การบันทึกต้องมี ffmpeg (แนะนำ) หรือ mpv ใน PATH\n\n"
              "ติดตั้ง ffmpeg เช่น:  sudo apt install ffmpeg",
    },
    "msg_stop_time_future": {
        "en": "The stop time must be in the future and after the start time.",
        "sv": "Stopptiden måste vara i framtiden och efter starttiden.",
        "es": "La hora de fin debe ser futura y posterior a la de inicio.",
        "de": "Die Stoppzeit muss in der Zukunft und nach der Startzeit liegen.",
        "fr": "L'heure de fin doit être future et postérieure au début.",
        "zh": "停止时间必须在将来且晚于开始时间。",
        "ru": "Время окончания должно быть в будущем и после времени начала.",
        "th": "เวลาหยุดต้องอยู่ในอนาคตและหลังเวลาเริ่ม",
    },
    "msg_edit_time_title": {
        "en": "Edit recording time", "sv": "Redigera inspelningstid",
        "es": "Editar hora de grabación", "de": "Aufnahmezeit bearbeiten",
        "fr": "Modifier l'heure d'enregistrement", "zh": "编辑录制时间",
        "ru": "Изменить время записи", "th": "แก้ไขเวลาบันทึก",
    },
    "msg_stop_after_start": {
        "en": "The stop time must be after the start time.",
        "sv": "Stopptiden måste vara efter starttiden.",
        "es": "La hora de fin debe ser posterior a la de inicio.",
        "de": "Die Stoppzeit muss nach der Startzeit liegen.",
        "fr": "L'heure de fin doit être postérieure à l'heure de début.",
        "zh": "停止时间必须晚于开始时间。",
        "ru": "Время окончания должно быть после времени начала.",
        "th": "เวลาหยุดต้องอยู่หลังเวลาเริ่ม",
    },
    "msg_delete_rec_title": {
        "en": "Delete recording", "sv": "Radera inspelning",
        "es": "Eliminar grabación", "de": "Aufnahme löschen",
        "fr": "Supprimer l'enregistrement", "zh": "删除录制",
        "ru": "Удалить запись", "th": "ลบการบันทึก",
    },
    "msg_delete_rec_body": {
        "en": "Delete {what} from disk?", "sv": "Radera {what} från disken?",
        "es": "¿Eliminar {what} del disco?", "de": "{what} von der Festplatte löschen?",
        "fr": "Supprimer {what} du disque ?", "zh": "从磁盘删除 {what}？",
        "ru": "Удалить {what} с диска?", "th": "ลบ {what} ออกจากดิสก์หรือไม่?",
    },
    "msg_clear_history_title": {
        "en": "Clear history", "sv": "Rensa historik", "es": "Borrar historial",
        "de": "Verlauf löschen", "fr": "Effacer l'historique", "zh": "清除历史",
        "ru": "Очистить историю", "th": "ล้างประวัติ",
    },
    "msg_clear_history_body": {
        "en": "Remove all watch history?", "sv": "Ta bort all visningshistorik?",
        "es": "¿Eliminar todo el historial de reproducción?",
        "de": "Gesamten Wiedergabeverlauf entfernen?",
        "fr": "Supprimer tout l'historique de lecture ?",
        "zh": "移除所有观看历史？", "ru": "Удалить всю историю просмотра?",
        "th": "ลบประวัติการดูทั้งหมดหรือไม่?",
    },
    "msg_rename_rec_title": {
        "en": "Rename recording", "sv": "Byt namn på inspelning",
        "es": "Renombrar grabación", "de": "Aufnahme umbenennen",
        "fr": "Renommer l'enregistrement", "zh": "重命名录制",
        "ru": "Переименовать запись", "th": "เปลี่ยนชื่อการบันทึก",
    },
    "msg_move_rec_title": {
        "en": "Move recording", "sv": "Flytta inspelning",
        "es": "Mover grabación", "de": "Aufnahme verschieben",
        "fr": "Déplacer l'enregistrement", "zh": "移动录制",
        "ru": "Переместить запись", "th": "ย้ายการบันทึก",
    },
    "msg_new_folder_title": {
        "en": "New folder", "sv": "Ny mapp", "es": "Nueva carpeta",
        "de": "Neuer Ordner", "fr": "Nouveau dossier", "zh": "新建文件夹",
        "ru": "Новая папка", "th": "โฟลเดอร์ใหม่",
    },
    "msg_folder_name": {
        "en": "Folder name:", "sv": "Mappnamn:", "es": "Nombre de carpeta:",
        "de": "Ordnername:", "fr": "Nom du dossier :", "zh": "文件夹名称：",
        "ru": "Имя папки:", "th": "ชื่อโฟลเดอร์:",
    },
    "msg_trakt_enter_creds": {
        "en": "Enter a Client ID and Client Secret first.",
        "sv": "Ange klient-ID och klienthemlighet först.",
        "es": "Introduce primero un ID de cliente y un secreto de cliente.",
        "de": "Zuerst Client-ID und Client-Secret eingeben.",
        "fr": "Saisissez d'abord un ID client et un secret client.",
        "zh": "请先输入客户端 ID 和客户端密钥。",
        "ru": "Сначала введите ID клиента и секрет клиента.",
        "th": "กรุณาใส่ Client ID และ Client Secret ก่อน",
    },
    "msg_remove_playlist_title": {
        "en": "Remove playlist", "sv": "Ta bort spellista",
        "es": "Eliminar lista", "de": "Playlist entfernen",
        "fr": "Supprimer la liste", "zh": "移除播放列表",
        "ru": "Удалить плейлист", "th": "ลบเพลย์ลิสต์",
    },
    "msg_remove_playlist_body": {
        "en": "Remove this playlist? Its favorites and history are kept until "
              "you re-add and clear them.",
        "sv": "Ta bort den här spellistan? Dess favoriter och historik "
              "behålls tills du lägger till den igen och rensar dem.",
        "es": "¿Eliminar esta lista? Sus favoritos e historial se conservan "
              "hasta que la vuelvas a añadir y los borres.",
        "de": "Diese Playlist entfernen? Ihre Favoriten und der Verlauf "
              "bleiben erhalten, bis du sie erneut hinzufügst und löschst.",
        "fr": "Supprimer cette liste ? Ses favoris et son historique sont "
              "conservés jusqu'à ce que vous la rajoutiez et les effaciez.",
        "zh": "移除此播放列表？其收藏和历史将保留，直到你重新添加并清除它们。",
        "ru": "Удалить этот плейлист? Его избранное и история сохраняются, "
              "пока вы не добавите его снова и не очистите их.",
        "th": "ลบเพลย์ลิสต์นี้หรือไม่? รายการโปรดและประวัติจะถูกเก็บไว้ "
              "จนกว่าคุณจะเพิ่มใหม่และล้างออก",
    },

    # ── Trakt dialogs ─────────────────────────────────────────────────────
    "trakt_connect_title": {
        "en": "Connect to Trakt", "sv": "Anslut till Trakt",
        "es": "Conectar a Trakt", "de": "Mit Trakt verbinden",
        "fr": "Se connecter à Trakt", "zh": "连接到 Trakt",
        "ru": "Подключиться к Trakt", "th": "เชื่อมต่อ Trakt",
    },
    "trakt_browser_opening": {
        "en": "Opening your browser… approve dopeIPTV on the Trakt page that "
              "appears, then come back here. Already signed in to Trakt on "
              "the web? Just click 'Yes'.",
        "sv": "Öppnar webbläsaren… godkänn dopeIPTV på Trakt-sidan som visas "
              "och kom sedan tillbaka hit. Redan inloggad på Trakt på webben? "
              "Klicka bara 'Yes'.",
        "es": "Abriendo tu navegador… aprueba dopeIPTV en la página de Trakt "
              "y vuelve aquí. ¿Ya tienes sesión en Trakt? Solo pulsa 'Yes'.",
        "de": "Browser wird geöffnet… bestätige dopeIPTV auf der Trakt-Seite "
              "und komm dann zurück. Schon bei Trakt angemeldet? Einfach "
              "'Yes' klicken.",
        "fr": "Ouverture du navigateur… autorisez dopeIPTV sur la page Trakt, "
              "puis revenez ici. Déjà connecté à Trakt ? Cliquez sur 'Yes'.",
        "zh": "正在打开浏览器……在出现的 Trakt 页面上批准 dopeIPTV，然后返回这里。"
              "已在网页登录 Trakt？点击 'Yes' 即可。",
        "ru": "Открываем браузер… подтвердите dopeIPTV на странице Trakt и "
              "вернитесь сюда. Уже вошли в Trakt? Просто нажмите 'Yes'.",
        "th": "กำลังเปิดเบราว์เซอร์… อนุมัติ dopeIPTV บนหน้า Trakt แล้วกลับมาที่นี่ "
              "หากล็อกอิน Trakt บนเว็บอยู่แล้ว แค่คลิก 'Yes'",
    },
    "trakt_finishing": {
        "en": "Approved - finishing sign-in…",
        "sv": "Godkänt - slutför inloggningen…",
        "es": "Aprobado: finalizando el inicio de sesión…",
        "de": "Bestätigt - Anmeldung wird abgeschlossen…",
        "fr": "Autorisé - finalisation de la connexion…",
        "zh": "已批准——正在完成登录……",
        "ru": "Подтверждено - завершаем вход…",
        "th": "อนุมัติแล้ว - กำลังเข้าสู่ระบบให้เสร็จ…",
    },
    "trakt_timed_out": {
        "en": "Timed out waiting for approval. Close and try again.",
        "sv": "Tidsgränsen gick ut i väntan på godkännande. Stäng och försök "
              "igen.",
        "es": "Se agotó el tiempo de espera. Cierra e inténtalo de nuevo.",
        "de": "Zeitüberschreitung beim Warten auf die Bestätigung. Schließen "
              "und erneut versuchen.",
        "fr": "Délai dépassé en attendant l'autorisation. Fermez et réessayez.",
        "zh": "等待批准超时。请关闭后重试。",
        "ru": "Истекло время ожидания подтверждения. Закройте и повторите.",
        "th": "หมดเวลารอการอนุมัติ ปิดแล้วลองใหม่",
    },
    "trakt_denied": {
        "en": "Sign-in was declined in the browser.",
        "sv": "Inloggningen nekades i webbläsaren.",
        "es": "El inicio de sesión se rechazó en el navegador.",
        "de": "Die Anmeldung wurde im Browser abgelehnt.",
        "fr": "La connexion a été refusée dans le navigateur.",
        "zh": "在浏览器中拒绝了登录。",
        "ru": "Вход был отклонён в браузере.",
        "th": "การเข้าสู่ระบบถูกปฏิเสธในเบราว์เซอร์",
    },
    "trakt_port_busy": {
        "en": "Couldn't open the local sign-in port ({port}). Close whatever "
              "is using it and try again.",
        "sv": "Kunde inte öppna den lokala inloggningsporten ({port}). Stäng "
              "det som använder den och försök igen.",
        "es": "No se pudo abrir el puerto local de inicio de sesión ({port}). "
              "Cierra lo que lo use e inténtalo de nuevo.",
        "de": "Der lokale Anmelde-Port ({port}) konnte nicht geöffnet werden. "
              "Schließe, was ihn belegt, und versuch es erneut.",
        "fr": "Impossible d'ouvrir le port de connexion local ({port}). "
              "Fermez ce qui l'utilise et réessayez.",
        "zh": "无法打开本地登录端口（{port}）。请关闭占用它的程序后重试。",
        "ru": "Не удалось открыть локальный порт для входа ({port}). Закройте "
              "занимающую его программу и повторите.",
        "th": "เปิดพอร์ตล็อกอินในเครื่องไม่ได้ ({port}) ปิดสิ่งที่ใช้งานอยู่แล้วลองใหม่",
    },
    "trakt_use_code_instead": {
        "en": "Use a code instead", "sv": "Använd en kod istället",
        "es": "Usar un código", "de": "Stattdessen Code verwenden",
        "fr": "Utiliser un code", "zh": "改用代码",
        "ru": "Ввести код вместо этого", "th": "ใช้รหัสแทน",
    },
    "trakt_requesting_code": {
        "en": "Requesting a device code…",
        "sv": "Begär en enhetskod…", "es": "Solicitando un código…",
        "de": "Gerätecode wird angefordert…",
        "fr": "Demande d'un code d'appareil…", "zh": "正在请求设备代码……",
        "ru": "Запрос кода устройства…", "th": "กำลังขอรหัสอุปกรณ์…",
    },
    "trakt_code_expired": {
        "en": "Code expired - try again.", "sv": "Koden gick ut - försök igen.",
        "es": "El código expiró; inténtalo de nuevo.",
        "de": "Code abgelaufen - erneut versuchen.",
        "fr": "Code expiré - réessayez.", "zh": "代码已过期——请重试。",
        "ru": "Код истёк - попробуйте снова.", "th": "รหัสหมดอายุ - ลองอีกครั้ง",
    },
    "trakt_connected_excl": {
        "en": "Connected to Trakt!", "sv": "Ansluten till Trakt!",
        "es": "¡Conectado a Trakt!", "de": "Mit Trakt verbunden!",
        "fr": "Connecté à Trakt !", "zh": "已连接到 Trakt！",
        "ru": "Подключено к Trakt!", "th": "เชื่อมต่อ Trakt แล้ว!",
    },
    "trakt_login_failed": {
        "en": "Trakt login failed: {msg}",
        "sv": "Trakt-inloggning misslyckades: {msg}",
        "es": "Error de inicio de sesión en Trakt: {msg}",
        "de": "Trakt-Anmeldung fehlgeschlagen: {msg}",
        "fr": "Échec de la connexion à Trakt : {msg}",
        "zh": "Trakt 登录失败：{msg}", "ru": "Ошибка входа в Trakt: {msg}",
        "th": "เข้าสู่ระบบ Trakt ล้มเหลว: {msg}",
    },
    "trakt_enter_code": {
        "en": "Go to <b>{url}</b> and enter this code:<br><br>"
              "<span style='font-size:20px; font-weight:700;'>{code}</span>",
        "sv": "Gå till <b>{url}</b> och ange den här koden:<br><br>"
              "<span style='font-size:20px; font-weight:700;'>{code}</span>",
        "es": "Ve a <b>{url}</b> e introduce este código:<br><br>"
              "<span style='font-size:20px; font-weight:700;'>{code}</span>",
        "de": "Gehe zu <b>{url}</b> und gib diesen Code ein:<br><br>"
              "<span style='font-size:20px; font-weight:700;'>{code}</span>",
        "fr": "Rendez-vous sur <b>{url}</b> et saisissez ce code :<br><br>"
              "<span style='font-size:20px; font-weight:700;'>{code}</span>",
        "zh": "前往 <b>{url}</b> 并输入此代码：<br><br>"
              "<span style='font-size:20px; font-weight:700;'>{code}</span>",
        "ru": "Перейдите на <b>{url}</b> и введите этот код:<br><br>"
              "<span style='font-size:20px; font-weight:700;'>{code}</span>",
        "th": "ไปที่ <b>{url}</b> แล้วป้อนรหัสนี้:<br><br>"
              "<span style='font-size:20px; font-weight:700;'>{code}</span>",
    },
    "trakt_could_not_start": {
        "en": "Could not start Trakt login: {msg}",
        "sv": "Kunde inte starta Trakt-inloggning: {msg}",
        "es": "No se pudo iniciar el acceso a Trakt: {msg}",
        "de": "Trakt-Anmeldung konnte nicht gestartet werden: {msg}",
        "fr": "Impossible de démarrer la connexion à Trakt : {msg}",
        "zh": "无法开始 Trakt 登录：{msg}",
        "ru": "Не удалось начать вход в Trakt: {msg}",
        "th": "ไม่สามารถเริ่มเข้าสู่ระบบ Trakt: {msg}",
    },
    "trakt_watchlist_title": {
        "en": "Trakt Watchlist & History",
        "sv": "Trakt bevakningslista och historik",
        "es": "Lista y historial de Trakt",
        "de": "Trakt-Merkliste & Verlauf",
        "fr": "Liste de suivi et historique Trakt",
        "zh": "Trakt 待看列表和历史",
        "ru": "Список и история Trakt",
        "th": "รายการที่ติดตามและประวัติ Trakt",
    },
    "trakt_tab_watchlist": {
        "en": "Watchlist", "sv": "Bevakningslista", "es": "Lista de seguimiento",
        "de": "Merkliste", "fr": "Liste de suivi", "zh": "待看列表",
        "ru": "Список к просмотру", "th": "รายการที่ติดตาม",
    },
    "trakt_load_failed": {
        "en": "Could not load Trakt data: {msg}",
        "sv": "Kunde inte ladda Trakt-data: {msg}",
        "es": "No se pudieron cargar los datos de Trakt: {msg}",
        "de": "Trakt-Daten konnten nicht geladen werden: {msg}",
        "fr": "Impossible de charger les données Trakt : {msg}",
        "zh": "无法加载 Trakt 数据：{msg}",
        "ru": "Не удалось загрузить данные Trakt: {msg}",
        "th": "ไม่สามารถโหลดข้อมูล Trakt: {msg}",
    },
    "trakt_create_app": {
        "en": "Create a free Trakt app…",
        "sv": "Skapa en gratis Trakt-app…",
        "es": "Crear una app gratuita de Trakt…",
        "de": "Kostenlose Trakt-App erstellen…",
        "fr": "Créer une application Trakt gratuite…",
        "zh": "创建免费的 Trakt 应用……",
        "ru": "Создать бесплатное приложение Trakt…",
        "th": "สร้างแอป Trakt ฟรี…",
    },
    "trakt_client_id_ph": {
        "en": "Client ID (from the app you created)",
        "sv": "Klient-ID (från appen du skapade)",
        "es": "ID de cliente (de la app que creaste)",
        "de": "Client-ID (aus der erstellten App)",
        "fr": "ID client (de l'application créée)",
        "zh": "客户端 ID（来自你创建的应用）",
        "ru": "ID клиента (из созданного приложения)",
        "th": "Client ID (จากแอปที่คุณสร้าง)",
    },
    "trakt_client_secret_ph": {
        "en": "Client Secret", "sv": "Klienthemlighet",
        "es": "Secreto de cliente", "de": "Client-Secret",
        "fr": "Secret client", "zh": "客户端密钥", "ru": "Секрет клиента",
        "th": "Client Secret",
    },
    "trakt_connect_btn": {
        "en": "Connect to Trakt…", "sv": "Anslut till Trakt…",
        "es": "Conectar a Trakt…", "de": "Mit Trakt verbinden…",
        "fr": "Se connecter à Trakt…", "zh": "连接到 Trakt……",
        "ru": "Подключиться к Trakt…", "th": "เชื่อมต่อ Trakt…",
    },
    "trakt_connect_browser": {
        "en": "Connect via browser", "sv": "Anslut via webbläsare",
        "es": "Conectar con el navegador", "de": "Über Browser verbinden",
        "fr": "Se connecter via le navigateur", "zh": "通过浏览器连接",
        "ru": "Подключиться через браузер", "th": "เชื่อมต่อผ่านเบราว์เซอร์",
    },
    "trakt_save_creds": {
        "en": "Save Client ID & Secret",
        "sv": "Spara Client ID & Secret",
        "es": "Guardar Client ID y Secret",
        "de": "Client-ID & Secret speichern",
        "fr": "Enregistrer Client ID et Secret",
        "zh": "保存 Client ID 和 Secret",
        "ru": "Сохранить Client ID и Secret",
        "th": "บันทึก Client ID และ Secret",
    },
    "trakt_creds_saved": {
        "en": "Saved. Now click 'Connect via browser' above to sign in.",
        "sv": "Sparat. Klicka nu på 'Anslut via webbläsare' ovan för att logga in.",
        "es": "Guardado. Ahora pulsa 'Conectar con el navegador' arriba para "
              "iniciar sesión.",
        "de": "Gespeichert. Klicke jetzt oben auf 'Über Browser verbinden', "
              "um dich anzumelden.",
        "fr": "Enregistré. Cliquez maintenant sur 'Se connecter via le "
              "navigateur' ci-dessus pour vous connecter.",
        "zh": "已保存。现在点击上方的“通过浏览器连接”以登录。",
        "ru": "Сохранено. Теперь нажмите «Подключиться через браузер» выше, "
              "чтобы войти.",
        "th": "บันทึกแล้ว คลิก 'เชื่อมต่อผ่านเบราว์เซอร์' ด้านบนเพื่อเข้าสู่ระบบ",
    },
    "trakt_connect_browser_hint": {
        "en": "The easy way: uses dopeIPTV's built-in Trakt app. Your browser "
              "opens Trakt, you click 'Yes', and you're signed in - no codes.",
        "sv": "Det enkla sättet: använder dopeIPTV:s inbyggda Trakt-app. Din "
              "webbläsare öppnar Trakt, du klickar 'Ja', och du är inloggad - "
              "inga koder.",
        "es": "La forma fácil: usa la app de Trakt integrada en dopeIPTV. Tu "
              "navegador abre Trakt, pulsas 'Sí' y ya estás dentro - sin códigos.",
        "de": "Der einfache Weg: nutzt die eingebaute Trakt-App von dopeIPTV. "
              "Dein Browser öffnet Trakt, du klickst 'Ja' und bist angemeldet - "
              "ohne Codes.",
        "fr": "La méthode simple : utilise l'app Trakt intégrée de dopeIPTV. "
              "Votre navigateur ouvre Trakt, vous cliquez sur 'Oui' et vous "
              "êtes connecté - sans codes.",
        "zh": "简单方式：使用 dopeIPTV 内置的 Trakt 应用。浏览器打开 Trakt，"
              "点击“是”即可登录——无需代码。",
        "ru": "Простой способ: использует встроенное приложение Trakt в "
              "dopeIPTV. Браузер откроет Trakt, вы нажмёте «Да» и войдёте - "
              "без кодов.",
        "th": "วิธีง่าย ๆ: ใช้แอป Trakt ในตัวของ dopeIPTV เบราว์เซอร์จะเปิด "
              "Trakt คุณคลิก 'ใช่' แล้วเข้าสู่ระบบ - ไม่ต้องใช้รหัส",
    },
    "trakt_creds_hint": {
        "en": "Advanced: use your own Trakt API app. Paste its Client ID and "
              "Secret, Save them, then use 'Connect via browser' above (Trakt "
              "always confirms sign-in in the browser).",
        "sv": "Avancerat: använd din egen Trakt-API-app. Klistra in dess "
              "Client ID och Secret, Spara, och använd sedan 'Anslut via "
              "webbläsare' ovan (Trakt bekräftar alltid inloggning i webbläsaren).",
        "es": "Avanzado: usa tu propia app de la API de Trakt. Pega su Client "
              "ID y Secret, guárdalos y usa 'Conectar con el navegador' arriba "
              "(Trakt siempre confirma el inicio de sesión en el navegador).",
        "de": "Erweitert: nutze deine eigene Trakt-API-App. Füge ihre Client-ID "
              "und Secret ein, speichere sie und nutze dann oben 'Über Browser "
              "verbinden' (Trakt bestätigt die Anmeldung immer im Browser).",
        "fr": "Avancé : utilisez votre propre app API Trakt. Collez son Client "
              "ID et Secret, enregistrez-les, puis utilisez 'Se connecter via le "
              "navigateur' ci-dessus (Trakt confirme toujours la connexion dans "
              "le navigateur).",
        "zh": "高级：使用你自己的 Trakt API 应用。粘贴其 Client ID 和 Secret，"
              "保存后使用上方的“通过浏览器连接”（Trakt 始终在浏览器中确认登录）。",
        "ru": "Дополнительно: используйте своё приложение Trakt API. Вставьте "
              "его Client ID и Secret, сохраните, затем нажмите «Подключиться "
              "через браузер» выше (Trakt всегда подтверждает вход в браузере).",
        "th": "ขั้นสูง: ใช้แอป Trakt API ของคุณเอง วาง Client ID และ Secret "
              "บันทึก แล้วใช้ 'เชื่อมต่อผ่านเบราว์เซอร์' ด้านบน (Trakt จะยืนยัน"
              "การเข้าสู่ระบบในเบราว์เซอร์เสมอ)",
    },
    "trakt_disconnect": {
        "en": "Disconnect", "sv": "Koppla från", "es": "Desconectar",
        "de": "Trennen", "fr": "Déconnecter", "zh": "断开连接",
        "ru": "Отключить", "th": "ตัดการเชื่อมต่อ",
    },
    "trakt_watchlist_btn": {
        "en": "Watchlist / History…", "sv": "Bevakningslista / Historik…",
        "es": "Lista / Historial…", "de": "Merkliste / Verlauf…",
        "fr": "Liste de suivi / Historique…", "zh": "待看列表 / 历史……",
        "ru": "Список / История…", "th": "รายการที่ติดตาม / ประวัติ…",
    },
    "mark_needs_tmdb": {
        "en": "TMDB metadata hasn't resolved for this title yet — try "
              "again in a few seconds, or use 'Match on TMDB…' to pick "
              "one manually.",
        "sv": "TMDB har inte matchat den här titeln än — försök igen "
              "om några sekunder, eller använd 'Matcha mot TMDB…' för "
              "att välja manuellt.",
        "es": "Los metadatos de TMDB aún no se han resuelto para este "
              "título — inténtalo de nuevo en unos segundos o usa "
              "'Emparejar con TMDB…' para elegir manualmente.",
        "de": "TMDB-Metadaten für diesen Titel wurden noch nicht "
              "aufgelöst — versuche es in ein paar Sekunden erneut oder "
              "nutze 'Mit TMDB abgleichen…'.",
        "fr": "Les métadonnées TMDB n'ont pas encore été résolues pour "
              "ce titre — réessaye dans quelques secondes ou utilise "
              "'Associer à TMDB…' pour choisir manuellement.",
        "zh": "TMDB 元数据尚未解析此标题——请几秒后再试，或使用"
              "'在 TMDB 上匹配…'手动选择。",
        "ru": "Метаданные TMDB для этого названия ещё не получены — "
              "попробуйте через несколько секунд или используйте "
              "«Сопоставить с TMDB…».",
        "th": "TMDB ยังไม่ได้จับคู่ชื่อเรื่องนี้ — "
              "ลองใหม่ในอีกไม่กี่วินาที หรือใช้ 'จับคู่กับ TMDB…' "
              "เพื่อเลือกด้วยตนเอง",
    },
    "trakt_sync_now": {
        "en": "Sync now", "sv": "Synka nu", "es": "Sincronizar ahora",
        "de": "Jetzt synchronisieren", "fr": "Synchroniser",
        "zh": "立即同步", "ru": "Синхронизировать", "th": "ซิงค์ตอนนี้",
    },
    "trakt_syncing": {
        "en": "Syncing watched history…",
        "sv": "Synkar tittarhistorik…",
        "es": "Sincronizando historial…",
        "de": "Verlauf wird synchronisiert…",
        "fr": "Synchronisation de l'historique…",
        "zh": "正在同步观看历史…",
        "ru": "Синхронизация истории просмотров…",
        "th": "กำลังซิงค์ประวัติการดู…",
    },
    "btn_sync_now": {
        "en": "Sync now (Trakt)",
        "sv": "Synka nu (Trakt)",
        "es": "Sincronizar ahora (Trakt)",
        "de": "Jetzt synchronisieren (Trakt)",
        "fr": "Synchroniser (Trakt)",
        "zh": "立即同步 (Trakt)",
        "ru": "Синхронизировать (Trakt)",
        "th": "ซิงค์เดี๋ยวนี้ (Trakt)",
    },
    "trakt_sync_never": {
        "en": "Watched history: not synced yet.",
        "sv": "Tittarhistorik: inte synkad än.",
        "es": "Historial de visualización: aún no sincronizado.",
        "de": "Verlauf: noch nicht synchronisiert.",
        "fr": "Historique de visionnage : pas encore synchronisé.",
        "zh": "观看历史：尚未同步。",
        "ru": "История просмотров: ещё не синхронизирована.",
        "th": "ประวัติการดู: ยังไม่ได้ซิงค์",
    },
    "trakt_sync_status": {
        "en": "Synced {when} — {movies} movies, {episodes} episodes.",
        "sv": "Synkad {when} — {movies} filmer, {episodes} avsnitt.",
        "es": "Sincronizado {when} — {movies} películas, "
              "{episodes} episodios.",
        "de": "Synchronisiert {when} — {movies} Filme, "
              "{episodes} Episoden.",
        "fr": "Synchronisé {when} — {movies} films, "
              "{episodes} épisodes.",
        "zh": "已同步于 {when} — {movies} 部电影, {episodes} 集。",
        "ru": "Синхронизировано {when} — фильмов: {movies}, "
              "эпизодов: {episodes}.",
        "th": "ซิงค์เมื่อ {when} — {movies} หนัง, {episodes} ตอน",
    },
    "trakt_sync_hint": {
        "en": "Marks movies and episodes you've watched on any device "
              "(mobile, browser, other players) with a check-badge in "
              "the list. Auto-syncs on startup at most once an hour.",
        "sv": "Markerar filmer och avsnitt du sett på andra enheter "
              "(mobil, webb, andra spelare) med en bock-badge i listan. "
              "Autosynkar vid uppstart högst en gång per timme.",
        "es": "Marca las películas y episodios que has visto en "
              "cualquier dispositivo (móvil, navegador, otros "
              "reproductores) con una marca en la lista. Se sincroniza "
              "automáticamente al iniciar, como máximo una vez por hora.",
        "de": "Markiert Filme und Episoden, die du auf irgendeinem "
              "Gerät angesehen hast (Handy, Browser, andere Player), "
              "mit einer Häkchen-Plakette in der Liste. Autosync beim "
              "Start höchstens einmal pro Stunde.",
        "fr": "Marque les films et épisodes que tu as regardés sur "
              "n'importe quel appareil (mobile, navigateur, autres "
              "lecteurs) avec un badge dans la liste. Synchronisation "
              "automatique au démarrage, une fois par heure au maximum.",
        "zh": "在列表中标记您在任何设备（移动设备、浏览器、其他播放器）上"
              "观看过的电影和剧集。启动时最多每小时自动同步一次。",
        "ru": "Помечает фильмы и эпизоды, которые вы посмотрели на "
              "любом устройстве (телефон, браузер, другие плееры), "
              "значком в списке. Автосинхронизация при запуске не чаще "
              "раза в час.",
        "th": "ทำเครื่องหมายภาพยนตร์และตอนที่คุณดูบนอุปกรณ์ใดก็ได้ "
              "(มือถือ เบราว์เซอร์ โปรแกรมเล่นอื่น) "
              "ด้วยเครื่องหมายถูกในรายการ "
              "ซิงค์อัตโนมัติเมื่อเปิดโปรแกรมไม่เกินหนึ่งครั้งต่อชั่วโมง",
    },
    "trakt_connected": {
        "en": "Connected to Trakt.", "sv": "Ansluten till Trakt.",
        "es": "Conectado a Trakt.", "de": "Mit Trakt verbunden.",
        "fr": "Connecté à Trakt.", "zh": "已连接到 Trakt。",
        "ru": "Подключено к Trakt.", "th": "เชื่อมต่อ Trakt แล้ว",
    },
    "trakt_not_connected": {
        "en": "Not connected.", "sv": "Inte ansluten.", "es": "No conectado.",
        "de": "Nicht verbunden.", "fr": "Non connecté.", "zh": "未连接。",
        "ru": "Не подключено.", "th": "ยังไม่ได้เชื่อมต่อ",
    },

    # ── Cast/actor filmography panel ──────────────────────────────────────
    "actor_other_titles": {
        "en": "{name} — other titles in your playlist",
        "sv": "{name} — andra titlar i din spellista",
        "es": "{name} — otros títulos en tu lista",
        "de": "{name} — weitere Titel in deiner Playlist",
        "fr": "{name} — autres titres dans votre liste",
        "zh": "{name} — 你播放列表中的其他作品",
        "ru": "{name} — другие тайтлы в вашем плейлисте",
        "th": "{name} — เรื่องอื่นในเพลย์ลิสต์ของคุณ",
    },
    "actor_lookup_filmography": {
        "en": "Looking up filmography…", "sv": "Slår upp filmografi…",
        "es": "Buscando filmografía…", "de": "Filmografie wird gesucht…",
        "fr": "Recherche de la filmographie…", "zh": "正在查询影视作品……",
        "ru": "Поиск фильмографии…", "th": "กำลังค้นหาผลงาน…",
    },
    "actor_searching_playlist": {
        "en": "Searching your playlist…", "sv": "Söker i din spellista…",
        "es": "Buscando en tu lista…", "de": "Deine Playlist wird durchsucht…",
        "fr": "Recherche dans votre liste…", "zh": "正在搜索你的播放列表……",
        "ru": "Поиск в вашем плейлисте…", "th": "กำลังค้นหาในเพลย์ลิสต์…",
    },
    "actor_no_matches": {
        "en": "No other titles from this playlist matched.",
        "sv": "Inga andra titlar från denna spellista matchade.",
        "es": "No coincidió ningún otro título de esta lista.",
        "de": "Keine weiteren Titel aus dieser Playlist gefunden.",
        "fr": "Aucun autre titre de cette liste ne correspond.",
        "zh": "此播放列表中没有其他匹配的作品。",
        "ru": "Других совпадений в этом плейлисте нет.",
        "th": "ไม่มีเรื่องอื่นในเพลย์ลิสต์นี้ที่ตรงกัน",
    },
    "actor_matches_found": {
        "en": "{n} title(s) found in your playlist (double-click to open):",
        "sv": "{n} titel/titlar hittades i din spellista (dubbelklicka "
              "för att öppna):",
        "es": "{n} título(s) encontrado(s) en tu lista (doble clic para abrir):",
        "de": "{n} Titel in deiner Playlist gefunden (Doppelklick zum Öffnen):",
        "fr": "{n} titre(s) trouvé(s) dans votre liste (double-clic pour "
              "ouvrir) :",
        "zh": "在你的播放列表中找到 {n} 个作品（双击打开）：",
        "ru": "Найдено тайтлов в плейлисте: {n} (двойной клик - открыть):",
        "th": "พบ {n} เรื่องในเพลย์ลิสต์ของคุณ (ดับเบิลคลิกเพื่อเปิด):",
    },
    "actor_not_found_tmdb": {
        "en": "Couldn't find {name} on TMDB.",
        "sv": "Kunde inte hitta {name} på TMDB.",
        "es": "No se encontró a {name} en TMDB.",
        "de": "{name} konnte auf TMDB nicht gefunden werden.",
        "fr": "Impossible de trouver {name} sur TMDB.",
        "zh": "在 TMDB 上找不到 {name}。",
        "ru": "Не удалось найти {name} на TMDB.",
        "th": "ไม่พบ {name} บน TMDB",
    },
    "actor_lookup_member": {
        "en": "Looking up cast member…", "sv": "Slår upp skådespelare…",
        "es": "Buscando miembro del reparto…",
        "de": "Darsteller wird gesucht…",
        "fr": "Recherche du membre de la distribution…",
        "zh": "正在查询演员……", "ru": "Поиск актёра…",
        "th": "กำลังค้นหานักแสดง…",
    },
    "ph_no_limit": {
        "en": "no limit", "sv": "ingen gräns", "es": "sin límite",
        "de": "kein Limit", "fr": "aucune limite", "zh": "无限制",
        "ru": "без лимита", "th": "ไม่จำกัด",
    },
    "rec_total_label": {
        "en": "Total recordings folder limit",
        "sv": "Total gräns för inspelningsmappen",
        "es": "Límite total de la carpeta de grabaciones",
        "de": "Gesamtlimit des Aufnahmeordners",
        "fr": "Limite totale du dossier d'enregistrements",
        "zh": "录制文件夹总容量上限",
        "ru": "Общий лимит папки записей",
        "th": "ขีดจำกัดรวมของโฟลเดอร์การบันทึก",
    },
    "rec_cap_title": {
        "en": "Storage limit reached",
        "sv": "Lagringsgräns nådd",
        "es": "Límite de almacenamiento alcanzado",
        "de": "Speicherlimit erreicht",
        "fr": "Limite de stockage atteinte",
        "zh": "已达存储上限",
        "ru": "Достигнут лимит хранилища",
        "th": "ถึงขีดจำกัดพื้นที่จัดเก็บแล้ว",
    },
    "rec_cap_reached": {
        "en": "Can't start a new recording: the recordings folder is at your "
              "limit ({used} of {cap}). Delete some recordings or raise the "
              "limit in Settings.",
        "sv": "Kan inte starta en ny inspelning: inspelningsmappen har nått "
              "din gräns ({used} av {cap}). Radera några inspelningar eller "
              "höj gränsen i Inställningar.",
        "es": "No se puede iniciar una grabación: la carpeta está en tu "
              "límite ({used} de {cap}). Borra grabaciones o sube el límite "
              "en Ajustes.",
        "de": "Neue Aufnahme nicht möglich: der Ordner hat dein Limit erreicht "
              "({used} von {cap}). Lösche Aufnahmen oder erhöhe das Limit in "
              "den Einstellungen.",
        "fr": "Impossible de démarrer un enregistrement : le dossier a atteint "
              "votre limite ({used} sur {cap}). Supprimez des enregistrements "
              "ou augmentez la limite dans les Réglages.",
        "zh": "无法开始新录制：录制文件夹已达上限（{used} / {cap}）。"
              "请删除部分录制或在设置中提高上限。",
        "ru": "Нельзя начать запись: папка достигла лимита ({used} из {cap}). "
              "Удалите записи или увеличьте лимит в настройках.",
        "th": "เริ่มบันทึกใหม่ไม่ได้: โฟลเดอร์ถึงขีดจำกัดแล้ว ({used} จาก {cap}) "
              "ลบการบันทึกบางส่วนหรือเพิ่มขีดจำกัดในการตั้งค่า",
    },

    # ── Settings: reset-to-defaults ───────────────────────────────────────
    "settings_reset_all": {
        "en": "Reset all settings…",
        "sv": "Återställ alla inställningar…",
        "es": "Restablecer todos los ajustes…",
        "de": "Alle Einstellungen zurücksetzen…",
        "fr": "Réinitialiser tous les paramètres…",
        "zh": "重置所有设置……",
        "ru": "Сбросить все настройки…",
        "th": "รีเซ็ตการตั้งค่าทั้งหมด…",
    },
    "settings_reset_confirm_1": {
        "en": "This will erase every dopeIPTV preference on this computer: "
              "your playlists, favorites, history, theme, resume positions, "
              "PIN, Trakt/TMDB keys and the panel layout.\n\nContinue?",
        "sv": "Detta raderar alla dopeIPTV-inställningar på den här datorn: "
              "dina spellistor, favoriter, historik, tema, återupptagnings-"
              "positioner, PIN, Trakt/TMDB-nycklar och panellayouten.\n\n"
              "Fortsätt?",
        "es": "Esto borrará todas las preferencias de dopeIPTV en este "
              "equipo: tus listas, favoritos, historial, tema, posiciones "
              "de reanudación, PIN y claves de Trakt/TMDB, y el diseño de "
              "paneles.\n\n¿Continuar?",
        "de": "Dies löscht alle dopeIPTV-Einstellungen auf diesem Rechner: "
              "Playlists, Favoriten, Verlauf, Design, Fortsetzungspositionen, "
              "PIN, Trakt-/TMDB-Schlüssel und das Panel-Layout.\n\nFortfahren?",
        "fr": "Cela effacera toutes les préférences dopeIPTV sur cet "
              "ordinateur : listes, favoris, historique, thème, positions "
              "de reprise, PIN, clés Trakt/TMDB et disposition des panneaux."
              "\n\nContinuer ?",
        "zh": "这将删除这台计算机上的所有 dopeIPTV 设置："
              "播放列表、收藏、历史、主题、续播位置、PIN、Trakt/TMDB 密钥和面板布局。"
              "\n\n继续吗？",
        "ru": "Это удалит все настройки dopeIPTV на этом компьютере: "
              "плейлисты, избранное, историю, тему, позиции возобновления, "
              "PIN, ключи Trakt/TMDB и раскладку панелей.\n\nПродолжить?",
        "th": "การทำเช่นนี้จะลบการตั้งค่า dopeIPTV ทั้งหมดในคอมพิวเตอร์เครื่องนี้: "
              "เพลย์ลิสต์ รายการโปรด ประวัติ ธีม ตำแหน่งเล่นต่อ "
              "PIN คีย์ Trakt/TMDB และเลย์เอาต์แผง\n\nดำเนินการต่อหรือไม่?",
    },
    "settings_reset_confirm_2": {
        "en": "Are you really sure? This can't be undone.",
        "sv": "Är du verkligen säker? Detta kan inte ångras.",
        "es": "¿Estás realmente seguro? Esto no se puede deshacer.",
        "de": "Bist du wirklich sicher? Das lässt sich nicht rückgängig machen.",
        "fr": "Êtes-vous vraiment sûr ? Cette action est irréversible.",
        "zh": "你真的确定吗？此操作无法撤销。",
        "ru": "Вы точно уверены? Отменить это будет невозможно.",
        "th": "คุณแน่ใจจริง ๆ หรือ? การกระทำนี้ไม่สามารถย้อนกลับได้",
    },
    "settings_reset_done": {
        "en": "All settings have been reset. dopeIPTV will now close - start "
              "it again to set up your first playlist.",
        "sv": "Alla inställningar är återställda. dopeIPTV stängs nu - "
              "starta om appen för att lägga upp din första spellista.",
        "es": "Se han restablecido todos los ajustes. dopeIPTV se cerrará; "
              "vuelve a abrirlo para configurar tu primera lista.",
        "de": "Alle Einstellungen wurden zurückgesetzt. dopeIPTV wird jetzt "
              "beendet - starte es erneut, um deine erste Playlist einzurichten.",
        "fr": "Tous les paramètres ont été réinitialisés. dopeIPTV va se "
              "fermer - relancez-le pour configurer votre première liste.",
        "zh": "所有设置已重置。dopeIPTV 现在将关闭 — 请重新启动以设置你的第一个播放列表。",
        "ru": "Все настройки сброшены. dopeIPTV сейчас закроется - "
              "запустите его снова, чтобы настроить первый плейлист.",
        "th": "รีเซ็ตการตั้งค่าทั้งหมดแล้ว dopeIPTV จะปิดตัวลง - "
              "เปิดใหม่เพื่อตั้งค่าเพลย์ลิสต์แรกของคุณ",
    },

    "cat_search_placeholder": {
        "en": "Search categories & channels…",
        "sv": "Sök kategorier & kanaler…",
        "es": "Buscar categorías y canales…",
        "de": "Kategorien & Sender suchen…",
        "fr": "Rechercher catégories et chaînes…",
        "zh": "搜索分类和频道…",
        "ru": "Поиск категорий и каналов…",
        "th": "ค้นหาหมวดหมู่และช่อง…",
    },
    "cat_search_items": {
        "en": "Search this list…", "sv": "Sök i listan…",
        "es": "Buscar en la lista…", "de": "Liste durchsuchen…",
        "fr": "Rechercher dans la liste…", "zh": "搜索此列表…",
        "ru": "Поиск в списке…", "th": "ค้นหาในรายการนี้…",
    },
    "cat_search_none": {
        "en": "No matching categories", "sv": "Inga matchande kategorier",
        "es": "No hay categorías coincidentes",
        "de": "Keine passenden Kategorien",
        "fr": "Aucune catégorie correspondante", "zh": "没有匹配的分类",
        "ru": "Нет подходящих категорий", "th": "ไม่พบหมวดหมู่ที่ตรงกัน",
    },

    "sec_timeshift": {
        "en": "Timeshift", "sv": "Timeshift", "es": "Timeshift",
        "de": "Timeshift", "fr": "Timeshift", "zh": "时移",
        "ru": "Таймшифт", "th": "ไทม์ชิฟต์",
    },
    "reminders_menu": {
        "en": "Reminders…", "sv": "Påminnelser…", "es": "Recordatorios…",
        "de": "Erinnerungen…", "fr": "Rappels…", "zh": "提醒…",
        "ru": "Напоминания…", "th": "การเตือน…",
    },
    "reminders_title": {
        "en": "Reminders", "sv": "Påminnelser", "es": "Recordatorios",
        "de": "Erinnerungen", "fr": "Rappels", "zh": "提醒",
        "ru": "Напоминания", "th": "การเตือน",
    },
    "reminders_empty": {
        "en": "No reminders set", "sv": "Inga påminnelser",
        "es": "Sin recordatorios", "de": "Keine Erinnerungen",
        "fr": "Aucun rappel", "zh": "没有提醒",
        "ru": "Нет напоминаний", "th": "ไม่มีการเตือน",
    },
    "reminders_remove": {
        "en": "Remove", "sv": "Ta bort", "es": "Quitar", "de": "Entfernen",
        "fr": "Retirer", "zh": "移除", "ru": "Удалить", "th": "ลบ",
    },
    "reminders_remove_n": {
        "en": "Remove {n}", "sv": "Ta bort {n}", "es": "Quitar {n}",
        "de": "{n} entfernen", "fr": "Retirer {n}", "zh": "移除 {n} 个",
        "ru": "Удалить: {n}", "th": "ลบ {n} รายการ",
    },
    "reminder_starts_in": {
        "en": "starts in {t}", "sv": "börjar om {t}", "es": "empieza en {t}",
        "de": "beginnt in {t}", "fr": "commence dans {t}", "zh": "{t}后开始",
        "ru": "начнётся через {t}", "th": "เริ่มในอีก {t}",
    },
    "reminder_starting": {
        "en": "starting now", "sv": "börjar nu", "es": "empieza ahora",
        "de": "beginnt jetzt", "fr": "commence maintenant", "zh": "即将开始",
        "ru": "начинается", "th": "กำลังเริ่ม",
    },
    "reminder_watch_named": {
        "en": "Watch {title}", "sv": "Titta på {title}", "es": "Ver {title}",
        "de": "{title} ansehen", "fr": "Regarder {title}", "zh": "观看 {title}",
        "ru": "Смотреть {title}", "th": "ดู {title}",
    },
    "reminder_multi_body": {
        "en": "{n} programmes are starting now", "sv": "{n} program börjar nu",
        "es": "{n} programas están empezando",
        "de": "{n} Sendungen beginnen jetzt",
        "fr": "{n} programmes commencent", "zh": "{n} 个节目即将开始",
        "ru": "{n} передач начинается", "th": "{n} รายการกำลังเริ่ม",
    },
    "rec_edit_info": {
        "en": "Edit info…", "sv": "Redigera info…", "es": "Editar info…",
        "de": "Info bearbeiten…", "fr": "Modifier les infos…",
        "zh": "编辑信息…", "ru": "Изменить сведения…", "th": "แก้ไขข้อมูล…",
    },
    "rec_info_title": {
        "en": "Recording info", "sv": "Inspelningsinfo",
        "es": "Información de la grabación", "de": "Aufnahme-Info",
        "fr": "Infos de l'enregistrement", "zh": "录制信息",
        "ru": "Сведения о записи", "th": "ข้อมูลการบันทึก",
    },
    "rec_info_name": {
        "en": "Title", "sv": "Titel", "es": "Título", "de": "Titel",
        "fr": "Titre", "zh": "标题", "ru": "Название", "th": "ชื่อเรื่อง",
    },
    "rec_info_desc": {
        "en": "Description", "sv": "Beskrivning", "es": "Descripción",
        "de": "Beschreibung", "fr": "Description", "zh": "描述",
        "ru": "Описание", "th": "คำอธิบาย",
    },
    "sec_maintenance": {
        "en": "Maintenance", "sv": "Underhåll", "es": "Mantenimiento",
        "de": "Wartung", "fr": "Maintenance", "zh": "维护",
        "ru": "Обслуживание", "th": "การบำรุงรักษา",
    },
    "win_shortcut_btn": {
        "en": "Create shortcut", "sv": "Skapa genväg", "es": "Crear acceso directo",
        "de": "Verknüpfung erstellen", "fr": "Créer un raccourci", "zh": "创建快捷方式",
        "ru": "Создать ярлык", "th": "สร้างทางลัด",
    },
    "win_shortcut_hint": {
        "en": "Add dopeIPTV to the Start menu and desktop",
        "sv": "Lägg till dopeIPTV på Start-menyn och skrivbordet",
        "es": "Añadir dopeIPTV al menú Inicio y al escritorio",
        "de": "dopeIPTV zum Startmenü und Desktop hinzufügen",
        "fr": "Ajouter dopeIPTV au menu Démarrer et au bureau",
        "zh": "将 dopeIPTV 添加到开始菜单和桌面",
        "ru": "Добавить dopeIPTV в меню «Пуск» и на рабочий стол",
        "th": "เพิ่ม dopeIPTV ไปยังเมนู Start และเดสก์ท็อป",
    },
    "win_shortcut_done": {
        "en": "Shortcut created", "sv": "Genväg skapad", "es": "Acceso directo creado",
        "de": "Verknüpfung erstellt", "fr": "Raccourci créé", "zh": "已创建快捷方式",
        "ru": "Ярлык создан", "th": "สร้างทางลัดแล้ว",
    },
    "win_shortcut_fail": {
        "en": "Couldn't create shortcut", "sv": "Kunde inte skapa genväg",
        "es": "No se pudo crear el acceso directo", "de": "Verknüpfung fehlgeschlagen",
        "fr": "Échec de la création du raccourci", "zh": "无法创建快捷方式",
        "ru": "Не удалось создать ярлык", "th": "สร้างทางลัดไม่สำเร็จ",
    },
    "ts_reset_channel": {
        "en": "Reset timeshift for this channel",
        "sv": "Återställ timeshift för den här kanalen",
        "es": "Restablecer timeshift para este canal",
        "de": "Timeshift für diesen Sender zurücksetzen",
        "fr": "Réinitialiser le timeshift pour cette chaîne",
        "zh": "重置此频道的时移",
        "ru": "Сбросить таймшифт для этого канала",
        "th": "รีเซ็ตไทม์ชิฟต์สำหรับช่องนี้",
    },
    "ts_reset_done_one": {
        "en": "Timeshift reset for this channel",
        "sv": "Timeshift återställd för kanalen",
        "es": "Timeshift restablecido para este canal",
        "de": "Timeshift für den Sender zurückgesetzt",
        "fr": "Timeshift réinitialisé pour cette chaîne",
        "zh": "已重置此频道的时移",
        "ru": "Таймшифт для канала сброшен",
        "th": "รีเซ็ตไทม์ชิฟต์สำหรับช่องนี้แล้ว",
    },
    "ts_reset_broken": {
        "en": "Reset timeshift channels", "sv": "Återställ timeshift-kanaler",
        "es": "Restablecer canales timeshift",
        "de": "Timeshift-Sender zurücksetzen",
        "fr": "Réinitialiser les chaînes timeshift", "zh": "重置时移频道",
        "ru": "Сбросить каналы таймшифта", "th": "รีเซ็ตช่องไทม์ชิฟต์",
    },
    "ts_reset_done": {
        "en": "Timeshift channels reset", "sv": "Timeshift-kanaler återställda",
        "es": "Canales timeshift restablecidos",
        "de": "Timeshift-Sender zurückgesetzt",
        "fr": "Chaînes timeshift réinitialisées", "zh": "时移频道已重置",
        "ru": "Каналы таймшифта сброшены", "th": "รีเซ็ตช่องไทม์ชิฟต์แล้ว",
    },
    "ts_reset_hint": {
        "en": "Show catch-up again on channels the app learned don't serve it. "
              "They're re-tested automatically after a while, too.",
        "sv": "Visa catch-up igen på kanaler appen lärt sig saknar det. "
              "De testas också automatiskt om efter ett tag.",
        "es": "Vuelve a mostrar la repetición en canales que el programa "
              "aprendió que no la ofrecen. También se reintentan tras un tiempo.",
        "de": "Catch-up wieder auf Sendern anzeigen, bei denen die App gelernt "
              "hat, dass es fehlt. Sie werden nach einer Weile neu geprüft.",
        "fr": "Réafficher le replay sur les chaînes que l'appli a apprises "
              "sans replay. Elles sont aussi retestées après un moment.",
        "zh": "在应用判定不支持回看的频道上重新显示回看。过一段时间也会自动重试。",
        "ru": "Снова показывать архив на каналах, где приложение сочло его "
              "недоступным. Со временем они также проверяются заново.",
        "th": "แสดงการดูย้อนหลังอีกครั้งบนช่องที่แอปเรียนรู้ว่าไม่มี "
              "และจะทดสอบใหม่อัตโนมัติหลังผ่านไปสักพัก",
    },

    "ts_archive_unavailable": {
        "en": "Catch-up isn't available for this channel - check with your "
              "provider",
        "sv": "Catch-up saknas för den här kanalen - kolla med din leverantör",
        "es": "La repetición no está disponible para este canal - consulta a "
              "tu proveedor",
        "de": "Catch-up ist für diesen Sender nicht verfügbar - frag deinen "
              "Anbieter",
        "fr": "Le replay n'est pas disponible pour cette chaîne - contactez "
              "votre fournisseur",
        "zh": "此频道不支持回看 - 请咨询你的服务商",
        "ru": "Архив недоступен для этого канала - уточните у провайдера",
        "th": "ช่องนี้ไม่รองรับการดูย้อนหลัง - โปรดสอบถามผู้ให้บริการ",
    },
    "ts_shorter_archive": {
        "en": "Archive is shorter than listed - trying the deepest available…",
        "sv": "Arkivet är kortare än angivet - provar det djupaste som finns…",
        "es": "El archivo es más corto de lo indicado - probando lo más "
              "profundo disponible…",
        "de": "Archiv ist kürzer als angegeben - versuche das tiefste "
              "Verfügbare…",
        "fr": "L'archive est plus courte qu'indiqué - essai du plus loin "
              "disponible…",
        "zh": "存档比标示的短 - 正在尝试可用的最早时间…",
        "ru": "Архив короче заявленного - пробуем самую раннюю доступную точку…",
        "th": "คลังย้อนหลังสั้นกว่าที่ระบุ - กำลังลองจุดที่ลึกที่สุดที่มี…",
    },
    "ts_checking": {
        "en": "Checking catch-up…", "sv": "Kollar catch-up…",
        "es": "Comprobando repetición…", "de": "Catch-up wird geprüft…",
        "fr": "Vérification du replay…", "zh": "正在检查回看…",
        "ru": "Проверка архива…", "th": "กำลังตรวจสอบการดูย้อนหลัง…",
    },

    # ── Keyboard shortcuts editor ─────────────────────────────────────────
    "sc_title": {
        "en": "Keyboard shortcuts", "sv": "Kortkommandon",
        "es": "Atajos de teclado", "de": "Tastenkürzel",
        "fr": "Raccourcis clavier", "zh": "键盘快捷键",
        "ru": "Горячие клавиши", "th": "แป้นพิมพ์ลัด",
    },
    "sc_open": {
        "en": "Keyboard shortcuts…", "sv": "Kortkommandon…",
        "es": "Atajos de teclado…", "de": "Tastenkürzel…",
        "fr": "Raccourcis clavier…", "zh": "键盘快捷键…",
        "ru": "Горячие клавиши…", "th": "แป้นพิมพ์ลัด…",
    },
    "sc_hint": {
        "en": "Click a field and press the new key combination. "
              "Escape and Delete stay reserved.",
        "sv": "Klicka i ett fält och tryck den nya tangentkombinationen. "
              "Escape och Delete är reserverade.",
        "es": "Haz clic en un campo y pulsa la nueva combinación. "
              "Escape y Suprimir están reservadas.",
        "de": "Feld anklicken und neue Tastenkombination drücken. "
              "Escape und Entf bleiben reserviert.",
        "fr": "Cliquez dans un champ et appuyez sur la nouvelle combinaison. "
              "Échap et Suppr restent réservées.",
        "zh": "点击输入框并按下新的组合键。Escape 和 Delete 为保留键。",
        "ru": "Щёлкните поле и нажмите новую комбинацию клавиш. "
              "Escape и Delete зарезервированы.",
        "th": "คลิกช่องแล้วกดคีย์ผสมใหม่ ปุ่ม Escape และ Delete ถูกสงวนไว้",
    },
    "sc_reset": {
        "en": "Reset to defaults", "sv": "Återställ standard",
        "es": "Restablecer valores", "de": "Standard wiederherstellen",
        "fr": "Réinitialiser", "zh": "恢复默认",
        "ru": "Сбросить по умолчанию", "th": "รีเซ็ตเป็นค่าเริ่มต้น",
    },
    "sc_save": {
        "en": "Save", "sv": "Spara", "es": "Guardar", "de": "Speichern",
        "fr": "Enregistrer", "zh": "保存", "ru": "Сохранить", "th": "บันทึก",
    },
    "sc_next_channel": {
        "en": "Next channel", "sv": "Nästa kanal", "es": "Canal siguiente",
        "de": "Nächster Sender", "fr": "Chaîne suivante", "zh": "下一个频道",
        "ru": "Следующий канал", "th": "ช่องถัดไป",
    },
    "sc_prev_channel": {
        "en": "Previous channel", "sv": "Föregående kanal",
        "es": "Canal anterior", "de": "Vorheriger Sender",
        "fr": "Chaîne précédente", "zh": "上一个频道",
        "ru": "Предыдущий канал", "th": "ช่องก่อนหน้า",
    },
    "sc_last_channel": {
        "en": "Last channel", "sv": "Senaste kanalen", "es": "Último canal",
        "de": "Letzter Sender", "fr": "Dernière chaîne", "zh": "上一次频道",
        "ru": "Последний канал", "th": "ช่องล่าสุด",
    },
    "sc_play_pause": {
        "en": "Play / Pause", "sv": "Spela / Pausa",
        "es": "Reproducir / Pausar", "de": "Wiedergabe / Pause",
        "fr": "Lecture / Pause", "zh": "播放 / 暂停",
        "ru": "Воспроизведение / Пауза", "th": "เล่น / หยุดชั่วคราว",
    },
    "sc_fullscreen": {
        "en": "Fullscreen", "sv": "Helskärm", "es": "Pantalla completa",
        "de": "Vollbild", "fr": "Plein écran", "zh": "全屏",
        "ru": "Полный экран", "th": "เต็มจอ",
    },
    "sc_mute": {
        "en": "Mute", "sv": "Ljud av", "es": "Silenciar", "de": "Stumm",
        "fr": "Muet", "zh": "静音", "ru": "Без звука", "th": "ปิดเสียง",
    },
    "sc_popout": {
        "en": "Pop out player", "sv": "Poppa ut spelaren",
        "es": "Ventana flotante", "de": "Player ausklappen",
        "fr": "Détacher le lecteur", "zh": "弹出播放器",
        "ru": "Отделить плеер", "th": "แยกหน้าต่างเครื่องเล่น",
    },
    "sc_record": {
        "en": "Record", "sv": "Spela in", "es": "Grabar", "de": "Aufnehmen",
        "fr": "Enregistrer", "zh": "录制", "ru": "Запись", "th": "บันทึก",
    },
    "sc_stats": {
        "en": "Playback stats", "sv": "Uppspelningsstatistik",
        "es": "Estadísticas de reproducción", "de": "Wiedergabestatistik",
        "fr": "Statistiques de lecture", "zh": "播放统计",
        "ru": "Статистика воспроизведения", "th": "สถิติการเล่น",
    },
    "sc_epg_guide": {
        "en": "TV guide", "sv": "TV-tablå", "es": "Guía de TV",
        "de": "TV-Programm", "fr": "Guide TV", "zh": "电视指南",
        "ru": "Телегид", "th": "ผังรายการทีวี",
    },
    "sc_epg_search": {
        "en": "Search guide", "sv": "Sök i tablån",
        "es": "Buscar en la guía", "de": "Programm durchsuchen",
        "fr": "Rechercher dans le guide", "zh": "搜索指南",
        "ru": "Поиск в телегиде", "th": "ค้นหาผังรายการ",
    },
    "sc_reminders": {
        "en": "Reminders", "sv": "Påminnelser", "es": "Recordatorios",
        "de": "Erinnerungen", "fr": "Rappels", "zh": "提醒",
        "ru": "Напоминания", "th": "การเตือน",
    },
    "sc_sidebar": {
        "en": "Toggle sidebar", "sv": "Visa/dölj sidopanel",
        "es": "Mostrar/ocultar barra lateral", "de": "Seitenleiste umschalten",
        "fr": "Afficher/masquer la barre latérale", "zh": "切换侧边栏",
        "ru": "Боковая панель", "th": "สลับแถบข้าง",
    },
    "sc_focus_mode": {
        "en": "Focus mode", "sv": "Fokusläge", "es": "Modo enfoque",
        "de": "Fokusmodus", "fr": "Mode concentration", "zh": "专注模式",
        "ru": "Режим фокуса", "th": "โหมดโฟกัส",
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def set_language(code: str) -> None:
    """Set the active language.  Falls back to ``"en"`` for unknown codes."""
    global _current_language
    _current_language = code if code in LANGUAGES else "en"


def current_language() -> str:
    """Return the current language code (e.g. ``"en"``, ``"sv"``)."""
    return _current_language


def tr(key: str, **kwargs: object) -> str:
    """Return the translated string for *key* in the current language.

    Any ``{name}`` placeholders in the string are substituted with the
    corresponding keyword arguments via ``str.format_map()``.

    If the key is missing entirely, the key itself is returned (prefixed
    with ``?``) so missing translations are easy to spot during development.
    If the key exists but has no translation for the active language, the
    English string is used as a fallback.
    """
    entry = _STRINGS.get(key)
    if entry is None:
        return f"?{key}"
    text = entry.get(_current_language) or entry.get("en", f"?{key}")
    if kwargs:
        try:
            return text.format_map(kwargs)
        except (KeyError, IndexError):
            return text
    return text
