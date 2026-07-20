<?php
/**
 * Español (es) — traducción completa de iptv.dope.rs. Los nombres propios y
 * términos técnicos (Xtream Codes, M3U, AppImage, mpv, GitHub…) se mantienen
 * sin traducir. Los atributos HTML usan comillas simples.
 */
return [
    // <head>
    "meta_title"   => "dopeIPTV — el reproductor de IPTV para Linux con EPG, timeshift, grabación y multiview",
    "meta_desc"    => "dopeIPTV es un reproductor de IPTV de escritorio rápido y de código abierto para Xtream Codes y M3U, con guía EPG completa, timeshift en directo, TV en diferido (catch-up), grabación y multiview — mira hasta nueve canales a la vez. Pensado para Linux — también en macOS y Windows.",
    "meta_keywords"=> "reproductor IPTV, reproductor IPTV Linux, Xtream Codes, M3U, EPG, XMLTV, timeshift, TV en diferido, grabación IPTV, multiview IPTV, ver varios canales, IPTV multipantalla, IPTV Linux, IPTV macOS, IPTV Windows, dopeIPTV",

    // header nav
    "nav_features"    => "Funciones",
    "nav_screenshots" => "Capturas",
    "nav_download"    => "Descargar",
    "nav_github"      => "GitHub",
    "nav_download_btn"=> "Descargar",

    // hero
    "hero_eyebrow" => "En directo · versión",
    "hero_h1"      => "TV en directo, catch-up y grabaciones — en una sola app <span class='hl'>nativa de escritorio</span>.",
    "hero_lede"    => "Un reproductor de IPTV rápido y moderno para Xtream Codes &amp; M3U — con guía EPG completa, timeshift y catch-up en directo, grabación con un clic y multiview para hasta nueve canales a la vez. Basado en mpv, hecho para Linux — y también funciona en macOS &amp; Windows.",
    "hero_cta"     => "Descargar para tu sistema",
    "hero_source"  => "Ver el código",
    "hero_free"    => "gratis &amp; de código abierto",

    // chips
    "chip_timeshift" => "Timeshift / catch-up",
    "chip_languages" => "26 idiomas",

    // features
    "feat_eyebrow" => "Todo en una sola pantalla",
    "feat_h2"      => "Hecho para cómo se ve la tele de verdad.",
    "feat_intro"   => "No es una biblioteca multimedia reconvertida — es un cliente de TV, con la línea de tiempo, la guía y el grabador donde esperas encontrarlos.",
    "feat_c1_h" => "Timeshift &amp; catch-up",
    "feat_c1_p" => "Retrocede en el archivo de un canal sobre una línea de tiempo en directo, o salta directamente a un programa anterior desde la guía.",
    "feat_mv_h" => "Multiview",
    "feat_mv_p" => "Mira hasta nueve canales en directo a la vez en una cuadrícula — mezcla distintas listas, haz clic en cualquiera para el audio, con timeshift y subtítulos por ventana.",
    "feat_c2_h" => "Pausa la TV en directo",
    "feat_c2_p" => "Pausa y reanuda al estilo DVR por detrás del directo — el reproductor muestra exactamente cuánto vas por detrás.",
    "feat_c3_h" => "Guía EPG completa",
    "feat_c3_p" => "Una parrilla de programas real con búsqueda, recordatorios y una lista configurable de lo que viene a continuación.",
    "feat_c4_h" => "Grabación con un clic",
    "feat_c4_p" => "Graba el canal que estás viendo por una sola conexión, con temporizadores, límites de tamaño y una biblioteca de Grabaciones.",
    "feat_c5_h" => "Multi-proveedor",
    "feat_c5_p" => "Varias listas Xtream o M3U una al lado de la otra, cada una con su propia EPG, actualización automática y URL de guía personalizada.",
    "feat_c6_h" => "Reproducción fluida",
    "feat_c6_p" => "Un motor mpv integrado, con Chromecast, sincronización con Trakt, temas y control total por teclado.",

    // screenshots
    "shots_eyebrow" => "Un vistazo por dentro",
    "shots_h2"      => "Limpio, oscuro y sin estorbar.",
    "shot_ph"       => "captura",
    "shot_main_alt" => "Ventana principal de dopeIPTV con la lista de canales, la guía y el vídeo",
    "shot_main_t"   => "Canales &amp; reproductor",
    "shot_main_c"   => "la lista, la guía y el vídeo en un solo diseño.",
    "shot_epg_alt"  => "Parrilla de la guía de programas EPG de dopeIPTV",
    "shot_epg_t"    => "Guía de programas",
    "shot_epg_c"    => "vista de parrilla con marcas de catch-up.",
    "shot_ts_alt"   => "Línea de tiempo de timeshift de dopeIPTV recorriendo el archivo de un canal",
    "shot_ts_t"     => "Línea de tiempo de timeshift",
    "shot_ts_c"     => "recorre el archivo, con el borde del directo marcado.",
    "shot_rec_alt"  => "Biblioteca de grabaciones de dopeIPTV con temporizadores",
    "shot_rec_t"    => "Grabaciones",
    "shot_rec_c"    => "temporizadores, límites de almacenamiento y reproducción.",

    // download
    "dl_eyebrow" => "Consigue dopeIPTV",
    "dl_h2"      => "Descarga la última versión.",
    "dl_latest"  => "última",
    "os_help_linux"   => "¿No estás seguro? Elige el <b>AppImage</b> — funciona en cualquier distribución sin instalar nada. Usa <b>.deb</b> en Debian/Ubuntu. Elige <b>Intel / AMD</b> salvo que tengas una máquina ARM (Raspberry Pi, servidor ARM).",
    "os_help_macos"   => "Una sola imagen funciona tanto en Apple Silicon (serie M) como en Macs Intel.",
    "os_help_windows" => "Versión portable — descomprime y ejecuta, no hay nada que instalar. La plataforma más nueva, aún puliéndose.",
    "os_install_linux"   => "🐧 <b>AppImage:</b> hazlo ejecutable y ábrelo — no hay nada que instalar: <code>chmod +x dopeIPTV-*.AppImage &amp;&amp; ./dopeIPTV-*.AppImage</code>. <b>.deb</b> (Debian/Ubuntu): <code>sudo apt install ./dopeIPTV-*.deb</code>. <b>.rpm</b> (Fedora/RHEL): <code>sudo dnf install ./dopeIPTV-*.rpm</code>.",
    "os_install_macos"   => "🍎 Abre el <code>.dmg</code> y arrastra dopeIPTV a Aplicaciones. Como la app aún no está notarizada por Apple, puede que el primer arranque se bloquee — <b>haz clic derecho en la app → Abrir</b>, y luego <b>Abrir</b> en el diálogo (o permítela en <b>Ajustes del Sistema → Privacidad y seguridad → Abrir igualmente</b>). Si en cambio macOS dice que la app está <b>“dañada”</b>, quita la marca de descarga en el Terminal: <code>xattr -dr com.apple.quarantine /Applications/dopeIPTV.app</code>. Es seguro — el aviso solo significa que la compilación no está firmada.",
    "os_install_windows" => "🪟 Descomprime la carpeta y ejecuta <code>dopeiptv.exe</code>. Como la app aún no está firmada, SmartScreen puede mostrar <b>“Windows protegió tu PC”</b> — haz clic en <b>Más información → Ejecutar de todas formas</b>. Es solo un aviso, no se bloquea ni se elimina nada.",
    "arch_apple"     => "Apple Silicon e Intel",
    "arch_x86"       => "Intel / AMD (64 bits)",
    "arch_arm"       => "ARM (64 bits)",
    "arch_universal" => "Universal",
    "dl_t_dmg"      => "Imagen de disco de macOS",
    "dl_f_dmg"      => ".dmg — arrastra a Aplicaciones",
    "dl_t_pkg"      => "Instalador de macOS",
    "dl_f_pkg"      => ".pkg",
    "dl_t_exe"      => "Instalador de Windows",
    "dl_f_exe"      => ".exe",
    "dl_t_winzip"   => "Windows portable",
    "dl_f_winzip"   => ".zip — descomprime &amp; ejecuta, sin instalar",
    "dl_t_appimage" => "AppImage",
    "dl_f_appimage" => "funciona en cualquier distro — sin instalar",
    "dl_t_deb"      => "Paquete .deb",
    "dl_f_deb"      => "para Debian / Ubuntu",
    "dl_t_rpm"      => "Paquete .rpm",
    "dl_f_rpm"      => "para Fedora / RHEL",
    "dl_t_flatpak"  => "Flatpak",
    "dl_f_flatpak"  => "todas las distros",
    "dl_recommended"=> "Recomendado",
    "dl_go"         => "Descargar →",
    "dl_all_name"   => "Todos los paquetes en GitHub",
    "dl_all_sub"    => "última versión",
    "dl_open"       => "Abrir →",
    "note_generated" => "↻ Generado en el servidor a partir de las releases de GitHub de <code>slimture/dopeIPTV</code> — las nuevas versiones aparecen automáticamente.",
    "note_verify"    => "🔒 Verifica tu descarga — <a class='verify-link' href='/files/SHA256SUMS'>sumas SHA-256</a> · <code>sha256sum -c SHA256SUMS</code>",

    // credits
    "cred_eyebrow" => "Código abierto",
    "cred_h2"      => "Software libre, a hombros de gigantes.",
    "cred_intro"   => "dopeIPTV es <b>libre y de código abierto</b> bajo la licencia GPL-3.0 — sin anuncios, sin rastreo, sin cuentas. Está construido con, y agradecido a, estos proyectos y servicios:",
    "cred_playback"      => "Reproducción",
    "cred_interface"     => "Interfaz",
    "cred_casting"       => "Casting",
    "cred_metadata"      => "Metadatos &amp; carátulas",
    "cred_watched"       => "Sincronización de vistos",
    "cred_licences"      => "Licencias",
    "cred_licences_link" => "GPL-3.0 &amp; terceros",
    "disclaimer" => "Este producto usa la API de TMDB pero no está avalado ni certificado por TMDB. Este producto usa la API de Trakt pero no está avalado ni certificado por Trakt. Todas las marcas son propiedad de sus respectivos dueños.",

    // footer
    "footer_releases" => "Versiones",
    "footer_docs"     => "Documentación",
    "lang_label"      => "Idioma",
];
