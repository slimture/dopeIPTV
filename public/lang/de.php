<?php
/**
 * Deutsch (de) — vollständige Übersetzung von iptv.dope.rs. Eigennamen und
 * technische Begriffe (Xtream Codes, M3U, AppImage, mpv, GitHub …) bleiben
 * unübersetzt. HTML-Attribute in den Strings verwenden einfache Anführungs-
 * zeichen, damit die Werte reine doppelt gequotete PHP-Strings bleiben.
 */
return [
    // <head>
    "meta_title"   => "dopeIPTV — der Linux-IPTV-Player mit EPG, Timeshift, Aufnahme & Multiview",
    "meta_desc"    => "dopeIPTV ist ein schneller, quelloffener Desktop-IPTV-Player für Xtream Codes und M3U mit vollständigem EPG, Live-Timeshift, Catch-up-TV, Aufnahme und Multiview — bis zu neun Kanäle gleichzeitig. Für Linux entwickelt — auch für macOS und Windows.",
    "meta_keywords"=> "IPTV-Player, Linux-IPTV-Player, Xtream Codes, M3U, EPG, XMLTV, Timeshift, Catch-up-TV, IPTV-Aufnahme, IPTV-Multiview, mehrere Kanäle ansehen, Multiscreen-IPTV, IPTV-Player Linux, macOS IPTV, Windows IPTV, dopeIPTV",

    // header nav
    "nav_features"    => "Funktionen",
    "nav_screenshots" => "Screenshots",
    "nav_download"    => "Download",
    "nav_github"      => "GitHub",
    "nav_download_btn"=> "Download",

    // hero
    "hero_eyebrow" => "Auf Sendung · Version",
    "hero_h1"      => "Live-TV, Catch-up und Aufnahmen — in einer <span class='hl'>nativen Desktop-App</span>.",
    "hero_lede"    => "Ein schneller, moderner IPTV-Player für Xtream Codes &amp; M3U — mit vollständigem EPG, Live-Timeshift und Catch-up, Aufnahme per Klick und Multiview für bis zu neun Kanäle gleichzeitig. Basiert auf mpv, gemacht für Linux — und läuft auch unter macOS &amp; Windows.",
    "hero_cta"     => "Für dein System herunterladen",
    "hero_source"  => "Quellcode ansehen",
    "hero_free"    => "kostenlos &amp; quelloffen",

    // feature strip chips
    "chip_timeshift" => "Timeshift / Catch-up",
    "chip_languages" => "8 Sprachen",

    // features
    "feat_eyebrow" => "Alles auf einem Bildschirm",
    "feat_h2"      => "Gebaut für die Art, wie man wirklich fernsieht.",
    "feat_intro"   => "Keine zweckentfremdete Medienbibliothek — ein TV-Client, mit Zeitleiste, Programmführer und Rekorder genau dort, wo du sie erwartest.",
    "feat_c1_h" => "Timeshift &amp; Catch-up",
    "feat_c1_p" => "Spule auf einer Live-Zeitleiste in das Archiv eines Kanals zurück oder springe direkt aus dem Programmführer zu einer vergangenen Sendung.",
    "feat_mv_h" => "Multiview",
    "feat_mv_p" => "Sieh dir bis zu neun Live-Kanäle gleichzeitig in einem Raster an — mische verschiedene Playlists, klicke einen für den Ton an, mit Timeshift und Untertiteln pro Fenster.",
    "feat_c2_h" => "Live-TV pausieren",
    "feat_c2_p" => "Pausieren und Fortsetzen im DVR-Stil hinter dem Live-Signal — der Player zeigt genau, wie weit du zurückliegst.",
    "feat_c3_h" => "Vollständiger EPG-Programmführer",
    "feat_c3_p" => "Ein echtes Programmraster mit Suche, Erinnerungen und einer konfigurierbaren Liste dessen, was als Nächstes kommt.",
    "feat_c4_h" => "Aufnahme per Klick",
    "feat_c4_p" => "Nimm den Stream, den du gerade siehst, über eine einzige Verbindung auf — mit Timern, Größenlimits und einer Aufnahmen-Bibliothek.",
    "feat_c5_h" => "Mehrere Anbieter",
    "feat_c5_p" => "Mehrere Xtream- oder M3U-Playlists nebeneinander, jede mit eigenem EPG, automatischer Aktualisierung und eigener Programmführer-URL.",
    "feat_c6_h" => "Flüssige Wiedergabe",
    "feat_c6_p" => "Eine integrierte mpv-Engine, mit Chromecast, Trakt-Sync, Themes und vollständiger Tastatursteuerung.",

    // screenshots
    "shots_eyebrow" => "Ein Blick ins Innere",
    "shots_h2"      => "Klar, dunkel und nicht im Weg.",
    "shot_ph"       => "Screenshot",
    "shot_main_alt" => "dopeIPTV-Hauptfenster mit Kanalliste, Programmführer und Video",
    "shot_main_t"   => "Kanäle &amp; Player",
    "shot_main_c"   => "die Liste, der Programmführer und das Video in einem Layout.",
    "shot_epg_alt"  => "dopeIPTV EPG-Programmführer-Raster",
    "shot_epg_t"    => "Programmführer",
    "shot_epg_c"    => "Rasteransicht mit Catch-up-Markierungen.",
    "shot_ts_alt"   => "dopeIPTV-Timeshift-Zeitleiste beim Durchsuchen eines Kanalarchivs",
    "shot_ts_t"     => "Timeshift-Zeitleiste",
    "shot_ts_c"     => "durchsuche das Archiv, der Live-Rand ist markiert.",
    "shot_rec_alt"  => "dopeIPTV-Aufnahmenbibliothek mit Timern",
    "shot_rec_t"    => "Aufnahmen",
    "shot_rec_c"    => "Timer, Speicherlimits und Wiedergabe.",

    // download
    "dl_eyebrow" => "dopeIPTV holen",
    "dl_h2"      => "Lade die neueste Version herunter.",
    "dl_latest"  => "neueste",
    "os_help_linux"   => "Nicht sicher? Nimm das <b>AppImage</b> — es läuft auf jeder Distribution ohne Installation. Wähle <b>.deb</b> unter Debian/Ubuntu. Nimm <b>Intel / AMD</b>, außer du hast eine ARM-Maschine (Raspberry Pi, ARM-Server).",
    "os_help_macos"   => "Ein Image funktioniert sowohl auf Apple Silicon (M-Serie) als auch auf Intel-Macs.",
    "os_help_windows" => "Portable Version — entpacken und starten, nichts zu installieren. Die neueste Plattform, noch im Feinschliff.",
    "os_install_linux"   => "🐧 <b>AppImage:</b> ausführbar machen und starten — nichts zu installieren: <code>chmod +x dopeIPTV-*.AppImage &amp;&amp; ./dopeIPTV-*.AppImage</code>. <b>.deb</b> (Debian/Ubuntu): <code>sudo apt install ./dopeIPTV-*.deb</code>. <b>.rpm</b> (Fedora/RHEL): <code>sudo dnf install ./dopeIPTV-*.rpm</code>.",
    "os_install_macos"   => "🍎 Öffne das <code>.dmg</code> und ziehe dopeIPTV in den Programme-Ordner. Da die App von Apple noch nicht notarisiert ist, kann der erste Start blockiert werden — <b>Rechtsklick auf die App → Öffnen</b>, dann im Dialog auf <b>Öffnen</b> (oder erlaube sie unter <b>Systemeinstellungen → Datenschutz &amp; Sicherheit → Dennoch öffnen</b>). Falls macOS stattdessen meldet, die App sei <b>„beschädigt“</b>, entferne die Download-Markierung im Terminal: <code>xattr -dr com.apple.quarantine /Applications/dopeIPTV.app</code>. Das ist sicher — die Warnung bedeutet nur, dass der Build nicht signiert ist.",
    "os_install_windows" => "🪟 Entpacke den Ordner und starte <code>dopeiptv.exe</code>. Da die App noch nicht signiert ist, zeigt SmartScreen eventuell <b>„Der Computer wurde durch Windows geschützt“</b> — klicke auf <b>Weitere Informationen → Trotzdem ausführen</b>. Es ist nur eine Warnung, nichts wird blockiert oder entfernt.",
    "arch_apple"     => "Apple Silicon & Intel",
    "arch_x86"       => "Intel / AMD (64-Bit)",
    "arch_arm"       => "ARM (64-Bit)",
    "arch_universal" => "Universal",
    "dl_t_dmg"      => "macOS-Disk-Image",
    "dl_f_dmg"      => ".dmg — in den Programme-Ordner ziehen",
    "dl_t_pkg"      => "macOS-Installer",
    "dl_f_pkg"      => ".pkg",
    "dl_t_exe"      => "Windows-Installer",
    "dl_f_exe"      => ".exe",
    "dl_t_winzip"   => "Windows portabel",
    "dl_f_winzip"   => ".zip — entpacken &amp; starten, keine Installation",
    "dl_t_appimage" => "AppImage",
    "dl_f_appimage" => "läuft auf jeder Distro — keine Installation",
    "dl_t_deb"      => ".deb-Paket",
    "dl_f_deb"      => "für Debian / Ubuntu",
    "dl_t_rpm"      => ".rpm-Paket",
    "dl_f_rpm"      => "für Fedora / RHEL",
    "dl_t_flatpak"  => "Flatpak",
    "dl_f_flatpak"  => "alle Distros",
    "dl_recommended"=> "Empfohlen",
    "dl_go"         => "Herunterladen →",
    "dl_all_name"   => "Alle Pakete auf GitHub",
    "dl_all_sub"    => "neueste Version",
    "dl_open"       => "Öffnen →",
    "note_generated" => "↻ Auf dem Server aus den GitHub-Releases von <code>slimture/dopeIPTV</code> generiert — neue Builds erscheinen automatisch.",
    "note_verify"    => "🔒 Überprüfe deinen Download — <a class='verify-link' href='/files/SHA256SUMS'>SHA-256-Prüfsummen</a> · <code>sha256sum -c SHA256SUMS</code>",

    // credits
    "cred_eyebrow" => "Open Source",
    "cred_h2"      => "Freie Software, auf den Schultern von Giganten.",
    "cred_intro"   => "dopeIPTV ist <b>frei und quelloffen</b> unter der GPL-3.0-Lizenz — keine Werbung, kein Tracking, keine Konten. Es baut auf diesen Projekten und Diensten auf, denen wir dankbar sind:",
    "cred_playback"      => "Wiedergabe",
    "cred_interface"     => "Oberfläche",
    "cred_casting"       => "Casting",
    "cred_metadata"      => "Metadaten &amp; Grafiken",
    "cred_watched"       => "Gesehen-Sync",
    "cred_licences"      => "Lizenzen",
    "cred_licences_link" => "GPL-3.0 &amp; Dritte",
    "disclaimer" => "Dieses Produkt verwendet die TMDB-API, wird aber von TMDB nicht unterstützt oder zertifiziert. Dieses Produkt verwendet die Trakt-API, wird aber von Trakt nicht unterstützt oder zertifiziert. Alle Marken sind Eigentum ihrer jeweiligen Inhaber.",

    // footer
    "footer_releases" => "Versionen",
    "footer_docs"     => "Doku",
    "lang_label"      => "Sprache",
];
