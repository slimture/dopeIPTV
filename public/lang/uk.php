<?php
/**
 * Українська (uk) — повний переклад iptv.dope.rs. Власні назви та технічні
 * терміни (Xtream Codes, M3U, AppImage, mpv, GitHub …) лишаються англійською.
 * HTML-атрибути в рядках використовують одинарні лапки, щоб значення
 * лишалися звичайними рядками PHP у подвійних лапках.
 */
return [
    // <head>
    "meta_title"   => "dopeIPTV — IPTV-плеєр для Linux з EPG і timeshift",
    "meta_desc"    => "Швидкий IPTV-плеєр з відкритим кодом для Xtream Codes & M3U — повний телегід EPG, timeshift, catch-up, запис і multiview. Linux, macOS & Windows.",
    "meta_keywords"=> "IPTV-плеєр, IPTV-плеєр Linux, Xtream Codes, M3U, EPG, XMLTV, timeshift, catch-up ТБ, запис IPTV, multiview IPTV, перегляд кількох каналів, багатоекранне IPTV, IPTV Linux, IPTV macOS, IPTV Windows, dopeIPTV",

    // header nav
    "nav_features"    => "Можливості",
    "nav_screenshots" => "Знімки екрана",
    "nav_download"    => "Завантажити",
    "nav_github"      => "GitHub",
    "nav_download_btn"=> "Завантажити",

    // hero
    "hero_eyebrow" => "В ефірі · версія",
    "hero_h1"      => "Пряме ТБ, catch-up і записи — в одному <span class='hl'>нативному настільному</span> застосунку.",
    "hero_lede"    => "Швидкий сучасний IPTV-плеєр для Xtream Codes &amp; M3U — із повним телегідом EPG, живим timeshift і catch-up, записом одним кліком і multiview для до дев'яти каналів одночасно. Побудований на mpv, створений для Linux — і працює також на macOS &amp; Windows.",
    "hero_cta"     => "Завантажити для вашої системи",
    "hero_source"  => "Переглянути код",
    "hero_free"    => "безкоштовно &amp; відкритий код",

    // feature strip chips
    "chip_timeshift" => "Timeshift / catch-up",
    "chip_languages" => "26 мов",

    // features
    "feat_eyebrow" => "Усе на одному екрані",
    "feat_h2"      => "Створений під те, як люди справді дивляться ТБ.",
    "feat_intro"   => "Не перероблена медіатека — ТБ-клієнт, де шкала часу, телегід і рекордер саме там, де ви їх очікуєте.",
    "feat_c1_h" => "Timeshift &amp; catch-up",
    "feat_c1_p" => "Відмотуйте назад в архів каналу на живій шкалі часу або переходьте прямо до минулої передачі з телегіда.",
    "feat_mv_h" => "Multiview",
    "feat_mv_p" => "Дивіться до дев'яти прямих каналів одночасно в сітці — змішуйте різні плейлисти, клацніть по одному для звуку, з timeshift і субтитрами для кожного вікна.",
    "feat_c2_h" => "Пауза прямого ефіру",
    "feat_c2_p" => "Пауза й відновлення у стилі DVR позаду прямого ефіру — плеєр показує, наскільки саме ви відстаєте.",
    "feat_c3_h" => "Повний телегід EPG",
    "feat_c3_p" => "Справжня сітка передач із пошуком, нагадуваннями та настроюваним списком того, що буде далі.",
    "feat_c4_h" => "Запис одним кліком",
    "feat_c4_p" => "Записуйте потік, який дивитеся, через одне з'єднання — з таймерами, обмеженнями розміру та бібліотекою Записів.",
    "feat_c5_h" => "Кілька провайдерів",
    "feat_c5_p" => "Кілька плейлистів Xtream або M3U поряд, кожен зі своїм EPG, автооновленням і власним URL телегіда.",
    "feat_c6_h" => "Плавне відтворення",
    "feat_c6_p" => "Вбудований рушій mpv, із Chromecast, синхронізацією Trakt, темами й повним керуванням з клавіатури.",

    // screenshots
    "shots_eyebrow" => "Погляд усередину",
    "shots_h2"      => "Чисто, темно й не заважає.",
    "shot_ph"       => "знімок екрана",
    "shot_main_alt" => "Головне вікно dopeIPTV зі списком каналів, телегідом і відео",
    "shot_main_t"   => "Канали &amp; плеєр",
    "shot_main_c"   => "список, телегід і відео в одному макеті.",
    "shot_epg_alt"  => "Сітка телегіда EPG dopeIPTV",
    "shot_epg_t"    => "Телегід",
    "shot_epg_c"    => "вигляд сіткою з позначками catch-up.",
    "shot_ts_alt"   => "Шкала часу timeshift dopeIPTV, що гортає архів каналу",
    "shot_ts_t"     => "Шкала часу timeshift",
    "shot_ts_c"     => "гортайте архів, край прямого ефіру позначено.",
    "shot_rec_alt"  => "Бібліотека записів dopeIPTV з таймерами",
    "shot_rec_t"    => "Записи",
    "shot_rec_c"    => "таймери, обмеження сховища й відтворення.",

    // download
    "dl_eyebrow" => "Отримати dopeIPTV",
    "dl_h2"      => "Завантажте останню версію.",
    "dl_latest"  => "остання",
    "os_help_linux"   => "Не впевнені? Візьміть <b>AppImage</b> — він працює на будь-якому дистрибутиві без встановлення. Оберіть <b>.deb</b> на Debian/Ubuntu. Візьміть <b>Intel / AMD</b>, якщо у вас не машина ARM (Raspberry Pi, ARM-сервер).",
    "os_help_macos"   => "Один образ працює і на Apple Silicon (серія M), і на Mac з Intel.",
    "os_help_windows" => "Портативна збірка — розпакуйте й запустіть, нічого встановлювати не потрібно. Найновіша платформа, ще допрацьовується.",
    "os_install_linux"   => "🐧 <b>AppImage:</b> зробіть файл виконуваним і запустіть — нічого встановлювати не потрібно: <code>chmod +x dopeIPTV-*.AppImage &amp;&amp; ./dopeIPTV-*.AppImage</code>. <b>.deb</b> (Debian/Ubuntu): <code>sudo apt install ./dopeIPTV-*.deb</code>. <b>.rpm</b> (Fedora/RHEL): <code>sudo dnf install ./dopeIPTV-*.rpm</code>.",
    "os_install_macos"   => "🍎 Відкрийте <code>.dmg</code> і перетягніть dopeIPTV до Applications. Оскільки застосунок ще не завірений (notarized) Apple, перший запуск може бути заблокований — <b>клацніть застосунок правою кнопкою → Open</b>, потім <b>Open</b> у діалозі (або дозвольте в <b>System Settings → Privacy &amp; Security → Open Anyway</b>). Якщо ж macOS каже, що застосунок <b>«пошкоджений» (damaged)</b>, зніміть позначку завантаження в Terminal: <code>xattr -dr com.apple.quarantine /Applications/dopeIPTV.app</code>. Це безпечно — попередження лише означає, що збірка не підписана.",
    "os_install_windows" => "🪟 Розпакуйте теку й запустіть <code>dopeiptv.exe</code>. Оскільки застосунок ще не підписаний, SmartScreen може показати <b>«Система Windows захистила ваш ПК»</b> — натисніть <b>Докладніше → Виконати попри це</b>. Це лише попередження, нічого не блокується й не видаляється.",
    "arch_apple"     => "Apple Silicon & Intel",
    "arch_x86"       => "Intel / AMD (64-біт)",
    "arch_arm"       => "ARM (64-біт)",
    "arch_universal" => "Універсальний",
    "dl_t_dmg"      => "Образ диска macOS",
    "dl_f_dmg"      => ".dmg — перетягніть до Applications",
    "dl_t_pkg"      => "Інсталятор macOS",
    "dl_f_pkg"      => ".pkg",
    "dl_t_exe"      => "Інсталятор Windows",
    "dl_f_exe"      => ".exe",
    "dl_t_winzip"   => "Windows портативна",
    "dl_f_winzip"   => ".zip — розпакуйте &amp; запустіть, без встановлення",
    "dl_t_appimage" => "AppImage",
    "dl_f_appimage" => "працює на будь-якому дистрибутиві — без встановлення",
    "dl_t_deb"      => "Пакет .deb",
    "dl_f_deb"      => "для Debian / Ubuntu",
    "dl_t_rpm"      => "Пакет .rpm",
    "dl_f_rpm"      => "для Fedora / RHEL",
    "dl_t_flatpak"  => "Flatpak",
    "dl_f_flatpak"  => "усі дистрибутиви",
    "dl_recommended"=> "Рекомендовано",
    "dl_go"         => "Завантажити →",
    "dl_all_name"   => "Усі пакети на GitHub",
    "dl_all_sub"    => "останній випуск",
    "dl_open"       => "Відкрити →",
    "note_generated" => "↻ Генерується на сервері з релізів GitHub <code>slimture/dopeIPTV</code> — нові збірки з'являються автоматично.",
    "note_verify"    => "🔒 Перевірте завантаження — <a class='verify-link' href='/files/SHA256SUMS'>контрольні суми SHA-256</a> · <code>sha256sum -c SHA256SUMS</code>",

    // credits
    "cred_eyebrow" => "Відкритий код",
    "cred_h2"      => "Вільне ПЗ, на плечах гігантів.",
    "cred_intro"   => "dopeIPTV — <b>вільне ПЗ з відкритим кодом</b> за ліцензією GPL-3.0 — без реклами, без стеження, без облікових записів. Його створено за допомогою цих проєктів і сервісів, яким ми вдячні:",
    "cred_playback"      => "Відтворення",
    "cred_interface"     => "Інтерфейс",
    "cred_casting"       => "Трансляція",
    "cred_metadata"      => "Метадані &amp; обкладинки",
    "cred_watched"       => "Синхронізація переглянутого",
    "cred_licences"      => "Ліцензії",
    "cred_licences_link" => "GPL-3.0 &amp; сторонні",
    "disclaimer" => "Цей продукт використовує API TMDB, але не схвалений і не сертифікований TMDB. Цей продукт використовує API Trakt, але не схвалений і не сертифікований Trakt. Усі торгові марки є власністю їхніх власників.",

    // footer
    "footer_releases" => "Випуски",
    "footer_docs"     => "Документація",
    "lang_label"      => "Мова",
];
