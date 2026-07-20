<?php
/**
 * Русский (ru) — полный перевод iptv.dope.rs. Имена собственные и технические
 * термины (Xtream Codes, M3U, AppImage, mpv, GitHub …) остаются на английском.
 * HTML-атрибуты в строках используют одинарные кавычки, чтобы значения
 * оставались обычными строками PHP в двойных кавычках.
 */
return [
    // <head>
    "meta_title"   => "dopeIPTV — IPTV-плеер для Linux с EPG, timeshift, записью & multiview",
    "meta_desc"    => "dopeIPTV — быстрый IPTV-плеер с открытым исходным кодом для Xtream Codes и M3U, с полным телегидом EPG, живым timeshift, catch-up-ТВ, записью и multiview — смотрите до девяти каналов одновременно. Создан для Linux — также на macOS и Windows.",
    "meta_keywords"=> "IPTV-плеер, IPTV-плеер Linux, Xtream Codes, M3U, EPG, XMLTV, timeshift, catch-up ТВ, запись IPTV, multiview IPTV, просмотр нескольких каналов, многоэкранное IPTV, IPTV Linux, IPTV macOS, IPTV Windows, dopeIPTV",

    // header nav
    "nav_features"    => "Возможности",
    "nav_screenshots" => "Скриншоты",
    "nav_download"    => "Скачать",
    "nav_github"      => "GitHub",
    "nav_download_btn"=> "Скачать",

    // hero
    "hero_eyebrow" => "В эфире · версия",
    "hero_h1"      => "Прямой эфир, catch-up и записи — в одном <span class='hl'>нативном настольном</span> приложении.",
    "hero_lede"    => "Быстрый современный IPTV-плеер для Xtream Codes &amp; M3U — с полным телегидом EPG, живым timeshift и catch-up, записью в один клик и multiview для до девяти каналов одновременно. Построен на mpv, создан для Linux — и работает также на macOS &amp; Windows.",
    "hero_cta"     => "Скачать для вашей системы",
    "hero_source"  => "Посмотреть исходный код",
    "hero_free"    => "бесплатно &amp; открытый код",

    // feature strip chips
    "chip_timeshift" => "Timeshift / catch-up",
    "chip_languages" => "8 языков",

    // features
    "feat_eyebrow" => "Всё на одном экране",
    "feat_h2"      => "Создан под то, как люди действительно смотрят ТВ.",
    "feat_intro"   => "Не переделанная медиатека — ТВ-клиент, где шкала времени, телегид и рекордер находятся именно там, где вы их ожидаете.",
    "feat_c1_h" => "Timeshift &amp; catch-up",
    "feat_c1_p" => "Отматывайте назад в архив канала на живой шкале времени или переходите прямо к прошедшей передаче из телегида.",
    "feat_mv_h" => "Multiview",
    "feat_mv_p" => "Смотрите до девяти прямых каналов одновременно в сетке — смешивайте разные плейлисты, кликните по одному для звука, с timeshift и субтитрами для каждого окна.",
    "feat_c2_h" => "Пауза прямого эфира",
    "feat_c2_p" => "Пауза и возобновление в стиле DVR позади прямого эфира — плеер показывает, насколько именно вы отстаёте.",
    "feat_c3_h" => "Полный телегид EPG",
    "feat_c3_p" => "Настоящая сетка передач с поиском, напоминаниями и настраиваемым списком того, что будет дальше.",
    "feat_c4_h" => "Запись в один клик",
    "feat_c4_p" => "Записывайте поток, который смотрите, по одному соединению — с таймерами, ограничениями размера и библиотекой Записей.",
    "feat_c5_h" => "Несколько провайдеров",
    "feat_c5_p" => "Несколько плейлистов Xtream или M3U рядом, у каждого свой EPG, автообновление и собственный URL телегида.",
    "feat_c6_h" => "Плавное воспроизведение",
    "feat_c6_p" => "Встроенный движок mpv, с Chromecast, синхронизацией Trakt, темами и полным управлением с клавиатуры.",

    // screenshots
    "shots_eyebrow" => "Взгляд изнутри",
    "shots_h2"      => "Чисто, темно и не мешает.",
    "shot_ph"       => "скриншот",
    "shot_main_alt" => "Главное окно dopeIPTV со списком каналов, телегидом и видео",
    "shot_main_t"   => "Каналы &amp; плеер",
    "shot_main_c"   => "список, телегид и видео в одном макете.",
    "shot_epg_alt"  => "Сетка телегида EPG dopeIPTV",
    "shot_epg_t"    => "Телегид",
    "shot_epg_c"    => "вид сеткой с метками catch-up.",
    "shot_ts_alt"   => "Шкала времени timeshift dopeIPTV, прокручивающая архив канала",
    "shot_ts_t"     => "Шкала времени timeshift",
    "shot_ts_c"     => "прокручивайте архив, край прямого эфира отмечен.",
    "shot_rec_alt"  => "Библиотека записей dopeIPTV с таймерами",
    "shot_rec_t"    => "Записи",
    "shot_rec_c"    => "таймеры, ограничения хранилища и воспроизведение.",

    // download
    "dl_eyebrow" => "Получить dopeIPTV",
    "dl_h2"      => "Скачайте последнюю версию.",
    "dl_latest"  => "последняя",
    "os_help_linux"   => "Не уверены? Возьмите <b>AppImage</b> — он работает на любом дистрибутиве без установки. Выберите <b>.deb</b> на Debian/Ubuntu. Возьмите <b>Intel / AMD</b>, если у вас не машина ARM (Raspberry Pi, ARM-сервер).",
    "os_help_macos"   => "Один образ работает и на Apple Silicon (серия M), и на Mac с Intel.",
    "os_help_windows" => "Портативная сборка — распакуйте и запустите, ничего устанавливать не нужно. Самая новая платформа, ещё дорабатывается.",
    "os_install_linux"   => "🐧 <b>AppImage:</b> сделайте файл исполняемым и запустите — ничего устанавливать не нужно: <code>chmod +x dopeIPTV-*.AppImage &amp;&amp; ./dopeIPTV-*.AppImage</code>. <b>.deb</b> (Debian/Ubuntu): <code>sudo apt install ./dopeIPTV-*.deb</code>. <b>.rpm</b> (Fedora/RHEL): <code>sudo dnf install ./dopeIPTV-*.rpm</code>.",
    "os_install_macos"   => "🍎 Откройте <code>.dmg</code> и перетащите dopeIPTV в Applications. Поскольку приложение ещё не заверено (notarized) Apple, первый запуск может быть заблокирован — <b>щёлкните приложение правой кнопкой → Открыть</b>, затем <b>Открыть</b> в диалоге (или разрешите в <b>Системные настройки → Конфиденциальность &amp; безопасность → Всё равно открыть</b>). Если же macOS сообщает, что приложение <b>«повреждено» (damaged)</b>, снимите метку загрузки в Терминале: <code>xattr -dr com.apple.quarantine /Applications/dopeIPTV.app</code>. Это безопасно — предупреждение лишь означает, что сборка не подписана.",
    "os_install_windows" => "🪟 Распакуйте папку и запустите <code>dopeiptv.exe</code>. Поскольку приложение ещё не подписано, SmartScreen может показать <b>«Система Windows защитила ваш компьютер»</b> — нажмите <b>Подробнее → Выполнить в любом случае</b>. Это лишь предупреждение, ничего не блокируется и не удаляется.",
    "arch_apple"     => "Apple Silicon & Intel",
    "arch_x86"       => "Intel / AMD (64-бит)",
    "arch_arm"       => "ARM (64-бит)",
    "arch_universal" => "Универсальный",
    "dl_t_dmg"      => "Образ диска macOS",
    "dl_f_dmg"      => ".dmg — перетащите в Applications",
    "dl_t_pkg"      => "Установщик macOS",
    "dl_f_pkg"      => ".pkg",
    "dl_t_exe"      => "Установщик Windows",
    "dl_f_exe"      => ".exe",
    "dl_t_winzip"   => "Windows портативная",
    "dl_f_winzip"   => ".zip — распакуйте &amp; запустите, без установки",
    "dl_t_appimage" => "AppImage",
    "dl_f_appimage" => "работает на любом дистрибутиве — без установки",
    "dl_t_deb"      => "Пакет .deb",
    "dl_f_deb"      => "для Debian / Ubuntu",
    "dl_t_rpm"      => "Пакет .rpm",
    "dl_f_rpm"      => "для Fedora / RHEL",
    "dl_t_flatpak"  => "Flatpak",
    "dl_f_flatpak"  => "все дистрибутивы",
    "dl_recommended"=> "Рекомендуется",
    "dl_go"         => "Скачать →",
    "dl_all_name"   => "Все пакеты на GitHub",
    "dl_all_sub"    => "последний релиз",
    "dl_open"       => "Открыть →",
    "note_generated" => "↻ Сгенерировано на сервере из релизов GitHub <code>slimture/dopeIPTV</code> — новые сборки появляются автоматически.",
    "note_verify"    => "🔒 Проверьте загрузку — <a class='verify-link' href='/files/SHA256SUMS'>контрольные суммы SHA-256</a> · <code>sha256sum -c SHA256SUMS</code>",

    // credits
    "cred_eyebrow" => "Открытый код",
    "cred_h2"      => "Свободное ПО, на плечах гигантов.",
    "cred_intro"   => "dopeIPTV — <b>свободное ПО с открытым кодом</b> под лицензией GPL-3.0 — без рекламы, без слежки, без аккаунтов. Оно создано с помощью этих проектов и сервисов, которым мы благодарны:",
    "cred_playback"      => "Воспроизведение",
    "cred_interface"     => "Интерфейс",
    "cred_casting"       => "Трансляция",
    "cred_metadata"      => "Метаданные &amp; обложки",
    "cred_watched"       => "Синхронизация просмотренного",
    "cred_licences"      => "Лицензии",
    "cred_licences_link" => "GPL-3.0 &amp; сторонние",
    "disclaimer" => "Этот продукт использует API TMDB, но не одобрен и не сертифицирован TMDB. Этот продукт использует API Trakt, но не одобрен и не сертифицирован Trakt. Все товарные знаки являются собственностью их владельцев.",

    // footer
    "footer_releases" => "Релизы",
    "footer_docs"     => "Документация",
    "lang_label"      => "Язык",
];
