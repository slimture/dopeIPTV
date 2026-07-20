<?php
/**
 * Italiano (it) — traduzione completa di iptv.dope.rs. Nomi propri e termini
 * tecnici (Xtream Codes, M3U, AppImage, mpv, GitHub …) restano in inglese.
 * Gli attributi HTML usano apici singoli così i valori restano stringhe PHP
 * tra virgolette doppie.
 */
return [
    // <head>
    "meta_title"   => "dopeIPTV — il lettore IPTV per Linux con EPG, timeshift, registrazione & multiview",
    "meta_desc"    => "dopeIPTV è un lettore IPTV desktop veloce e open source per Xtream Codes e M3U, con guida EPG completa, timeshift in diretta, TV catch-up, registrazione e multiview — guarda fino a nove canali insieme. Pensato per Linux — anche su macOS e Windows.",
    "meta_keywords"=> "lettore IPTV, lettore IPTV Linux, Xtream Codes, M3U, EPG, XMLTV, timeshift, TV catch-up, registrazione IPTV, multiview IPTV, guardare più canali, IPTV multischermo, IPTV Linux, IPTV macOS, IPTV Windows, dopeIPTV",

    // header nav
    "nav_features"    => "Funzioni",
    "nav_screenshots" => "Screenshot",
    "nav_download"    => "Scarica",
    "nav_github"      => "GitHub",
    "nav_download_btn"=> "Scarica",

    // hero
    "hero_eyebrow" => "In onda · versione",
    "hero_h1"      => "TV in diretta, catch-up e registrazioni — in un'unica app <span class='hl'>desktop nativa</span>.",
    "hero_lede"    => "Un lettore IPTV veloce e moderno per Xtream Codes &amp; M3U — con guida EPG completa, timeshift e catch-up in diretta, registrazione con un clic e multiview per un massimo di nove canali insieme. Basato su mpv, pensato per Linux — e funziona anche su macOS &amp; Windows.",
    "hero_cta"     => "Scarica per il tuo sistema",
    "hero_source"  => "Vedi il codice sorgente",
    "hero_free"    => "gratis &amp; open source",

    // feature strip chips
    "chip_timeshift" => "Timeshift / catch-up",
    "chip_languages" => "8 lingue",

    // features
    "feat_eyebrow" => "Tutto su un unico schermo",
    "feat_h2"      => "Costruito per come si guarda davvero la TV.",
    "feat_intro"   => "Non una libreria multimediale riadattata — un client TV, con la timeline, la guida e il registratore dove te li aspetti.",
    "feat_c1_h" => "Timeshift &amp; catch-up",
    "feat_c1_p" => "Torna indietro nell'archivio di un canale su una timeline in diretta, o salta direttamente a un programma passato dalla guida.",
    "feat_mv_h" => "Multiview",
    "feat_mv_p" => "Guarda fino a nove canali in diretta insieme in una griglia — mescola playlist diverse, clicca su uno per l'audio, con timeshift e sottotitoli per finestra.",
    "feat_c2_h" => "Metti in pausa la diretta",
    "feat_c2_p" => "Pausa e ripresa in stile DVR dietro la diretta — il lettore mostra esattamente di quanto sei indietro.",
    "feat_c3_h" => "Guida EPG completa",
    "feat_c3_p" => "Una vera griglia dei programmi con ricerca, promemoria e un elenco configurabile di ciò che verrà dopo.",
    "feat_c4_h" => "Registrazione con un clic",
    "feat_c4_p" => "Registra il flusso che stai guardando su un'unica connessione, con timer, limiti di dimensione e una libreria di Registrazioni.",
    "feat_c5_h" => "Multi-provider",
    "feat_c5_p" => "Più playlist Xtream o M3U affiancate, ciascuna con la propria EPG, aggiornamento automatico e URL della guida personalizzato.",
    "feat_c6_h" => "Riproduzione fluida",
    "feat_c6_p" => "Un motore mpv integrato, con Chromecast, sincronizzazione Trakt, temi e controllo completo da tastiera.",

    // screenshots
    "shots_eyebrow" => "Uno sguardo all'interno",
    "shots_h2"      => "Pulito, scuro e mai d'intralcio.",
    "shot_ph"       => "screenshot",
    "shot_main_alt" => "Finestra principale di dopeIPTV con la lista dei canali, la guida e il video",
    "shot_main_t"   => "Canali &amp; lettore",
    "shot_main_c"   => "la lista, la guida e il video in un unico layout.",
    "shot_epg_alt"  => "Griglia della guida ai programmi EPG di dopeIPTV",
    "shot_epg_t"    => "Guida ai programmi",
    "shot_epg_c"    => "vista a griglia con indicatori di catch-up.",
    "shot_ts_alt"   => "Timeline del timeshift di dopeIPTV che scorre l'archivio di un canale",
    "shot_ts_t"     => "Timeline del timeshift",
    "shot_ts_c"     => "scorri l'archivio, con il bordo della diretta segnato.",
    "shot_rec_alt"  => "Libreria delle registrazioni di dopeIPTV con timer",
    "shot_rec_t"    => "Registrazioni",
    "shot_rec_c"    => "timer, limiti di spazio e riproduzione.",

    // download
    "dl_eyebrow" => "Ottieni dopeIPTV",
    "dl_h2"      => "Scarica l'ultima versione.",
    "dl_latest"  => "ultima",
    "os_help_linux"   => "Non sei sicuro? Prendi l'<b>AppImage</b> — funziona su qualsiasi distribuzione senza installazione. Scegli <b>.deb</b> su Debian/Ubuntu. Prendi <b>Intel / AMD</b> a meno che tu non abbia una macchina ARM (Raspberry Pi, server ARM).",
    "os_help_macos"   => "Un'unica immagine funziona sia su Apple Silicon (serie M) sia sui Mac Intel.",
    "os_help_windows" => "Versione portabile — estrai ed esegui, niente da installare. La piattaforma più recente, ancora in fase di rifinitura.",
    "os_install_linux"   => "🐧 <b>AppImage:</b> rendila eseguibile e avviala — niente da installare: <code>chmod +x dopeIPTV-*.AppImage &amp;&amp; ./dopeIPTV-*.AppImage</code>. <b>.deb</b> (Debian/Ubuntu): <code>sudo apt install ./dopeIPTV-*.deb</code>. <b>.rpm</b> (Fedora/RHEL): <code>sudo dnf install ./dopeIPTV-*.rpm</code>.",
    "os_install_macos"   => "🍎 Apri il <code>.dmg</code> e trascina dopeIPTV in Applicazioni. Poiché l'app non è ancora autenticata (notarized) da Apple, il primo avvio potrebbe essere bloccato — <b>fai clic destro sull'app → Apri</b>, quindi <b>Apri</b> nella finestra di dialogo (oppure consentila in <b>Impostazioni di Sistema → Privacy &amp; Sicurezza → Apri comunque</b>). Se invece macOS dice che l'app è <b>“danneggiata”</b>, rimuovi il contrassegno di download dal Terminale: <code>xattr -dr com.apple.quarantine /Applications/dopeIPTV.app</code>. È sicuro — l'avviso significa solo che la build non è firmata.",
    "os_install_windows" => "🪟 Estrai la cartella ed esegui <code>dopeiptv.exe</code>. Poiché l'app non è ancora firmata, SmartScreen potrebbe mostrare <b>“Windows ha protetto il PC”</b> — fai clic su <b>Ulteriori informazioni → Esegui comunque</b>. È solo un avviso, niente viene bloccato o rimosso.",
    "arch_apple"     => "Apple Silicon & Intel",
    "arch_x86"       => "Intel / AMD (64 bit)",
    "arch_arm"       => "ARM (64 bit)",
    "arch_universal" => "Universale",
    "dl_t_dmg"      => "Immagine disco macOS",
    "dl_f_dmg"      => ".dmg — trascina in Applicazioni",
    "dl_t_pkg"      => "Installer macOS",
    "dl_f_pkg"      => ".pkg",
    "dl_t_exe"      => "Installer Windows",
    "dl_f_exe"      => ".exe",
    "dl_t_winzip"   => "Windows portabile",
    "dl_f_winzip"   => ".zip — estrai &amp; esegui, senza installazione",
    "dl_t_appimage" => "AppImage",
    "dl_f_appimage" => "funziona su qualsiasi distro — senza installazione",
    "dl_t_deb"      => "Pacchetto .deb",
    "dl_f_deb"      => "per Debian / Ubuntu",
    "dl_t_rpm"      => "Pacchetto .rpm",
    "dl_f_rpm"      => "per Fedora / RHEL",
    "dl_t_flatpak"  => "Flatpak",
    "dl_f_flatpak"  => "tutte le distro",
    "dl_recommended"=> "Consigliato",
    "dl_go"         => "Scarica →",
    "dl_all_name"   => "Tutti i pacchetti su GitHub",
    "dl_all_sub"    => "ultima versione",
    "dl_open"       => "Apri →",
    "note_generated" => "↻ Generato sul server dai release GitHub di <code>slimture/dopeIPTV</code> — le nuove build compaiono automaticamente.",
    "note_verify"    => "🔒 Verifica il tuo download — <a class='verify-link' href='/files/SHA256SUMS'>checksum SHA-256</a> · <code>sha256sum -c SHA256SUMS</code>",

    // credits
    "cred_eyebrow" => "Open source",
    "cred_h2"      => "Software libero, sulle spalle dei giganti.",
    "cred_intro"   => "dopeIPTV è <b>libero e open source</b> con licenza GPL-3.0 — senza pubblicità, senza tracciamento, senza account. È costruito con, e riconoscente verso, questi progetti e servizi:",
    "cred_playback"      => "Riproduzione",
    "cred_interface"     => "Interfaccia",
    "cred_casting"       => "Casting",
    "cred_metadata"      => "Metadati &amp; grafiche",
    "cred_watched"       => "Sincronizzazione dei visti",
    "cred_licences"      => "Licenze",
    "cred_licences_link" => "GPL-3.0 &amp; terze parti",
    "disclaimer" => "Questo prodotto usa l'API di TMDB ma non è approvato né certificato da TMDB. Questo prodotto usa l'API di Trakt ma non è approvato né certificato da Trakt. Tutti i marchi sono proprietà dei rispettivi titolari.",

    // footer
    "footer_releases" => "Versioni",
    "footer_docs"     => "Documentazione",
    "lang_label"      => "Lingua",
];
