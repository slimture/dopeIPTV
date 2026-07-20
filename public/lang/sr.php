<?php
/**
 * Srpski (sr, latinica) — potpun prevod iptv.dope.rs. Vlastita imena i tehnički
 * pojmovi (Xtream Codes, M3U, AppImage, mpv, GitHub …) ostaju na engleskom.
 * HTML atributi koriste jednostruke navodnike da vrednosti ostanu PHP niske u
 * dvostrukim navodnicima.
 */
return [
    // <head>
    "meta_title"   => "dopeIPTV — IPTV plejer za Linux sa EPG-om, timeshiftom, snimanjem & multiview",
    "meta_desc"    => "dopeIPTV je brz IPTV plejer otvorenog koda za stone računare za Xtream Codes i M3U, sa potpunim EPG vodičem, live timeshiftom, catch-up TV-om, snimanjem i multiview — gledajte do devet kanala istovremeno. Napravljen za Linux — i na macOS-u i Windowsu.",
    "meta_keywords"=> "IPTV plejer, IPTV plejer Linux, Xtream Codes, M3U, EPG, XMLTV, timeshift, catch-up TV, IPTV snimanje, IPTV multiview, gledanje više kanala, višeekranski IPTV, IPTV Linux, IPTV macOS, IPTV Windows, dopeIPTV",

    // header nav
    "nav_features"    => "Funkcije",
    "nav_screenshots" => "Snimci ekrana",
    "nav_download"    => "Preuzmi",
    "nav_github"      => "GitHub",
    "nav_download_btn"=> "Preuzmi",

    // hero
    "hero_eyebrow" => "U programu · verzija",
    "hero_h1"      => "Live TV, catch-up i snimci — u jednoj <span class='hl'>izvornoj desktop</span> aplikaciji.",
    "hero_lede"    => "Brz, moderan IPTV plejer za Xtream Codes &amp; M3U — sa potpunim EPG vodičem, live timeshiftom i catch-upom, snimanjem jednim klikom i multiview za do devet kanala istovremeno. Izgrađen na mpv-u, napravljen za Linux — i radi i na macOS-u &amp; Windowsu.",
    "hero_cta"     => "Preuzmi za svoj sistem",
    "hero_source"  => "Pogledaj izvorni kôd",
    "hero_free"    => "besplatno &amp; otvoreni kod",

    // feature strip chips
    "chip_timeshift" => "Timeshift / catch-up",
    "chip_languages" => "26 jezika",

    // features
    "feat_eyebrow" => "Sve na jednom ekranu",
    "feat_h2"      => "Napravljeno za način na koji ljudi zaista gledaju TV.",
    "feat_intro"   => "Ne prenamenjena medijska biblioteka — već TV klijent, sa vremenskom linijom, vodičem i snimačem tačno tamo gde ih očekujete.",
    "feat_c1_h" => "Timeshift &amp; catch-up",
    "feat_c1_p" => "Premotajte nazad u arhivu kanala na live vremenskoj liniji ili skočite direktno na prošlu emisiju iz vodiča.",
    "feat_mv_h" => "Multiview",
    "feat_mv_p" => "Gledajte do devet live kanala istovremeno u mreži — mešajte različite plejliste, kliknite jedan za zvuk, sa timeshiftom i titlovima po prozoru.",
    "feat_c2_h" => "Pauziraj live TV",
    "feat_c2_p" => "Pauziranje i nastavak u DVR stilu iza live signala — plejer pokazuje tačno koliko kasnite.",
    "feat_c3_h" => "Potpun EPG vodič",
    "feat_c3_p" => "Prava programska mreža sa pretragom, podsetnicima i prilagodljivim spiskom onoga što sledi.",
    "feat_c4_h" => "Snimanje jednim klikom",
    "feat_c4_p" => "Snimite stream koji gledate preko jedne veze, sa tajmerima, ograničenjima veličine i bibliotekom Snimaka.",
    "feat_c5_h" => "Više provajdera",
    "feat_c5_p" => "Više Xtream ili M3U plejlista jedna uz drugu, svaka sa svojim EPG-om, automatskim osvežavanjem i prilagođenim URL-om vodiča.",
    "feat_c6_h" => "Glatka reprodukcija",
    "feat_c6_p" => "Ugrađeni mpv endžin, sa Chromecastom, Trakt sinhronizacijom, temama i potpunom kontrolom tastaturom.",

    // screenshots
    "shots_eyebrow" => "Pogled iznutra",
    "shots_h2"      => "Čisto, tamno i ne smeta.",
    "shot_ph"       => "snimak ekrana",
    "shot_main_alt" => "Glavni prozor dopeIPTV-a sa spiskom kanala, vodičem i videom",
    "shot_main_t"   => "Kanali &amp; plejer",
    "shot_main_c"   => "spisak, vodič i video u jednom rasporedu.",
    "shot_epg_alt"  => "Mreža EPG programskog vodiča dopeIPTV-a",
    "shot_epg_t"    => "Programski vodič",
    "shot_epg_c"    => "prikaz mreže sa catch-up oznakama.",
    "shot_ts_alt"   => "Timeshift vremenska linija dopeIPTV-a pri pregledanju arhive kanala",
    "shot_ts_t"     => "Timeshift vremenska linija",
    "shot_ts_c"     => "pregledajte arhivu, ivica live signala označena.",
    "shot_rec_alt"  => "Biblioteka snimaka dopeIPTV-a sa tajmerima",
    "shot_rec_t"    => "Snimci",
    "shot_rec_c"    => "tajmeri, ograničenja skladišta i reprodukcija.",

    // download
    "dl_eyebrow" => "Nabavi dopeIPTV",
    "dl_h2"      => "Preuzmite najnoviju verziju.",
    "dl_latest"  => "najnovije",
    "os_help_linux"   => "Niste sigurni? Uzmite <b>AppImage</b> — radi na bilo kojoj distribuciji bez instalacije. Izaberite <b>.deb</b> na Debianu/Ubuntuu. Uzmite <b>Intel / AMD</b> osim ako imate ARM mašinu (Raspberry Pi, ARM server).",
    "os_help_macos"   => "Jedna slika radi i na Apple Siliconu (M serija) i na Intel Mac računarima.",
    "os_help_windows" => "Prenosiva verzija — raspakujte i pokrenite, ništa za instaliranje. Najnovija platforma, još se dorađuje.",
    "os_install_linux"   => "🐧 <b>AppImage:</b> učinite ga izvršnim i pokrenite — ništa za instaliranje: <code>chmod +x dopeIPTV-*.AppImage &amp;&amp; ./dopeIPTV-*.AppImage</code>. <b>.deb</b> (Debian/Ubuntu): <code>sudo apt install ./dopeIPTV-*.deb</code>. <b>.rpm</b> (Fedora/RHEL): <code>sudo dnf install ./dopeIPTV-*.rpm</code>.",
    "os_install_macos"   => "🍎 Otvorite <code>.dmg</code> i prevucite dopeIPTV u Aplikacije. Pošto aplikacija još nije overena (notarized) od strane Apple-a, prvo pokretanje može biti blokirano — <b>desni klik na aplikaciju → Open</b>, zatim <b>Open</b> u dijalogu (ili je dozvolite u <b>System Settings → Privacy &amp; Security → Open Anyway</b>). Ako umesto toga macOS kaže da je aplikacija <b>„oštećena” (damaged)</b>, uklonite oznaku preuzimanja u Terminalu: <code>xattr -dr com.apple.quarantine /Applications/dopeIPTV.app</code>. Bezbedno je — upozorenje samo znači da build nije potpisan.",
    "os_install_windows" => "🪟 Raspakujte folder i pokrenite <code>dopeiptv.exe</code>. Pošto aplikacija još nije potpisana, SmartScreen može prikazati <b>„Windows je zaštitio vaš računar”</b> — kliknite <b>Više informacija → Svejedno pokreni</b>. To je samo upozorenje, ništa se ne blokira niti uklanja.",
    "arch_apple"     => "Apple Silicon & Intel",
    "arch_x86"       => "Intel / AMD (64-bitni)",
    "arch_arm"       => "ARM (64-bitni)",
    "arch_universal" => "Univerzalni",
    "dl_t_dmg"      => "macOS slika diska",
    "dl_f_dmg"      => ".dmg — prevucite u Aplikacije",
    "dl_t_pkg"      => "macOS instaler",
    "dl_f_pkg"      => ".pkg",
    "dl_t_exe"      => "Windows instaler",
    "dl_f_exe"      => ".exe",
    "dl_t_winzip"   => "Windows prenosivi",
    "dl_f_winzip"   => ".zip — raspakujte &amp; pokrenite, bez instalacije",
    "dl_t_appimage" => "AppImage",
    "dl_f_appimage" => "radi na bilo kojoj distribuciji — bez instalacije",
    "dl_t_deb"      => ".deb paket",
    "dl_f_deb"      => "za Debian / Ubuntu",
    "dl_t_rpm"      => ".rpm paket",
    "dl_f_rpm"      => "za Fedoru / RHEL",
    "dl_t_flatpak"  => "Flatpak",
    "dl_f_flatpak"  => "sve distribucije",
    "dl_recommended"=> "Preporučeno",
    "dl_go"         => "Preuzmi →",
    "dl_all_name"   => "Svi paketi na GitHubu",
    "dl_all_sub"    => "najnovije izdanje",
    "dl_open"       => "Otvori →",
    "note_generated" => "↻ Generisano na serveru iz GitHub izdanja <code>slimture/dopeIPTV</code> — nove verzije se pojavljuju automatski.",
    "note_verify"    => "🔒 Proverite svoje preuzimanje — <a class='verify-link' href='/files/SHA256SUMS'>SHA-256 kontrolne sume</a> · <code>sha256sum -c SHA256SUMS</code>",

    // credits
    "cred_eyebrow" => "Otvoreni kod",
    "cred_h2"      => "Slobodan softver, na ramenima divova.",
    "cred_intro"   => "dopeIPTV je <b>slobodan i otvorenog koda</b> pod licencom GPL-3.0 — bez reklama, bez praćenja, bez naloga. Izgrađen je uz pomoć ovih projekata i usluga, kojima je zahvalan:",
    "cred_playback"      => "Reprodukcija",
    "cred_interface"     => "Interfejs",
    "cred_casting"       => "Emitovanje",
    "cred_metadata"      => "Metapodaci &amp; slike",
    "cred_watched"       => "Sinhronizacija odgledanog",
    "cred_licences"      => "Licence",
    "cred_licences_link" => "GPL-3.0 &amp; treće strane",
    "disclaimer" => "Ovaj proizvod koristi TMDB API, ali nije odobren niti sertifikovan od strane TMDB-a. Ovaj proizvod koristi Trakt API, ali nije odobren niti sertifikovan od strane Trakta. Svi zaštitni znakovi su vlasništvo svojih vlasnika.",

    // footer
    "footer_releases" => "Izdanja",
    "footer_docs"     => "Dokumentacija",
    "lang_label"      => "Jezik",
];
