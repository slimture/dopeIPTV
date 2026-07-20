<?php
/**
 * Svenska (sv) — fullständig översättning av iptv.dope.rs. Egennamn och
 * tekniska termer (Xtream Codes, M3U, AppImage, mpv, GitHub …) står kvar på
 * engelska. HTML-attribut i strängarna använder enkla citattecken så att
 * värdena förblir vanliga dubbelciterade PHP-strängar.
 */
return [
    // <head>
    "meta_title"   => "dopeIPTV — IPTV-spelaren för Linux med EPG, timeshift, inspelning & multiview",
    "meta_desc"    => "dopeIPTV är en snabb IPTV-spelare med öppen källkod för Xtream Codes och M3U, med komplett EPG-guide, timeshift i realtid, catch-up-TV, inspelning och multiview — se upp till nio kanaler samtidigt. Byggd för Linux — även på macOS och Windows.",
    "meta_keywords"=> "IPTV-spelare, IPTV-spelare Linux, Xtream Codes, M3U, EPG, XMLTV, timeshift, catch-up-TV, IPTV-inspelning, IPTV-multiview, se flera kanaler, multiskärm-IPTV, IPTV Linux, IPTV macOS, IPTV Windows, dopeIPTV",

    // header nav
    "nav_features"    => "Funktioner",
    "nav_screenshots" => "Skärmbilder",
    "nav_download"    => "Ladda ner",
    "nav_github"      => "GitHub",
    "nav_download_btn"=> "Ladda ner",

    // hero
    "hero_eyebrow" => "Sänds nu · version",
    "hero_h1"      => "Live-TV, catch-up och inspelningar — i en <span class='hl'>inbyggd skrivbordsapp</span>.",
    "hero_lede"    => "En snabb, modern IPTV-spelare för Xtream Codes &amp; M3U — med komplett EPG-guide, timeshift och catch-up i realtid, inspelning med ett klick och multiview för upp till nio kanaler samtidigt. Byggd på mpv, gjord för Linux — och funkar även på macOS &amp; Windows.",
    "hero_cta"     => "Ladda ner för ditt system",
    "hero_source"  => "Visa källkoden",
    "hero_free"    => "gratis &amp; öppen källkod",

    // feature strip chips
    "chip_timeshift" => "Timeshift / catch-up",
    "chip_languages" => "26 språk",

    // features
    "feat_eyebrow" => "Allt på en och samma skärm",
    "feat_h2"      => "Byggd för hur man faktiskt tittar på TV.",
    "feat_intro"   => "Inget ombyggt mediebibliotek — en TV-klient, med tidslinjen, guiden och inspelaren där du förväntar dig dem.",
    "feat_c1_h" => "Timeshift &amp; catch-up",
    "feat_c1_p" => "Spola tillbaka in i en kanals arkiv på en live-tidslinje, eller hoppa direkt till ett tidigare program från guiden.",
    "feat_mv_h" => "Multiview",
    "feat_mv_p" => "Se upp till nio livekanaler samtidigt i ett rutnät — blanda olika spellistor, klicka på en för ljudet, med timeshift och undertexter per fönster.",
    "feat_c2_h" => "Pausa live-TV",
    "feat_c2_p" => "Pausa och återuppta i DVR-stil bakom sändningen — spelaren visar exakt hur långt bak du ligger.",
    "feat_c3_h" => "Komplett EPG-guide",
    "feat_c3_p" => "Ett riktigt programrutnät med sökning, påminnelser och en konfigurerbar lista över vad som kommer härnäst.",
    "feat_c4_h" => "Inspelning med ett klick",
    "feat_c4_p" => "Spela in strömmen du tittar på över en enda anslutning, med timers, storleksgränser och ett inspelningsbibliotek.",
    "feat_c5_h" => "Flera leverantörer",
    "feat_c5_p" => "Flera Xtream- eller M3U-spellistor sida vid sida, var och en med egen EPG, automatisk uppdatering och egen guide-URL.",
    "feat_c6_h" => "Mjuk uppspelning",
    "feat_c6_p" => "En inbyggd mpv-motor, med Chromecast, Trakt-synk, teman och full tangentbordsstyrning.",

    // screenshots
    "shots_eyebrow" => "En titt inuti",
    "shots_h2"      => "Ren, mörk och ur vägen.",
    "shot_ph"       => "skärmbild",
    "shot_main_alt" => "dopeIPTV huvudfönster med kanallistan, guiden och videon",
    "shot_main_t"   => "Kanaler &amp; spelare",
    "shot_main_c"   => "listan, guiden och videon i en och samma layout.",
    "shot_epg_alt"  => "dopeIPTV EPG-programguide-rutnät",
    "shot_epg_t"    => "Programguide",
    "shot_epg_c"    => "rutnätsvy med catch-up-markörer.",
    "shot_ts_alt"   => "dopeIPTV timeshift-tidslinje som spolar i ett kanalarkiv",
    "shot_ts_t"     => "Timeshift-tidslinje",
    "shot_ts_c"     => "spola i arkivet, med live-läget markerat.",
    "shot_rec_alt"  => "dopeIPTV inspelningsbibliotek med timers",
    "shot_rec_t"    => "Inspelningar",
    "shot_rec_c"    => "timers, lagringsgränser och uppspelning.",

    // download
    "dl_eyebrow" => "Skaffa dopeIPTV",
    "dl_h2"      => "Ladda ner den senaste versionen.",
    "dl_latest"  => "senaste",
    "os_help_linux"   => "Osäker? Ta <b>AppImage</b> — den körs på vilken distribution som helst utan installation. Välj <b>.deb</b> på Debian/Ubuntu. Ta <b>Intel / AMD</b> om du inte har en ARM-maskin (Raspberry Pi, ARM-server).",
    "os_help_macos"   => "En och samma image funkar på både Apple Silicon (M-serien) och Intel-Mac.",
    "os_help_windows" => "Portabelt bygge — packa upp och kör, inget att installera. Den nyaste plattformen, fortfarande under finslipning.",
    "os_install_linux"   => "🐧 <b>AppImage:</b> gör den körbar och starta den — inget att installera: <code>chmod +x dopeIPTV-*.AppImage &amp;&amp; ./dopeIPTV-*.AppImage</code>. <b>.deb</b> (Debian/Ubuntu): <code>sudo apt install ./dopeIPTV-*.deb</code>. <b>.rpm</b> (Fedora/RHEL): <code>sudo dnf install ./dopeIPTV-*.rpm</code>.",
    "os_install_macos"   => "🍎 Öppna <code>.dmg</code>-filen och dra dopeIPTV till Program. Eftersom appen ännu inte är notariserad av Apple kan första starten blockeras — <b>högerklicka på appen → Öppna</b>, och sedan <b>Öppna</b> i dialogrutan (eller tillåt den under <b>Systeminställningar → Integritet &amp; säkerhet → Öppna ändå</b>). Om macOS istället säger att appen är <b>”skadad”</b>, ta bort nedladdningsflaggan i Terminal: <code>xattr -dr com.apple.quarantine /Applications/dopeIPTV.app</code>. Det är säkert — varningen betyder bara att bygget inte är signerat.",
    "os_install_windows" => "🪟 Packa upp mappen och kör <code>dopeiptv.exe</code>. Eftersom appen ännu inte är signerad kan SmartScreen visa <b>”Windows skyddade din dator”</b> — klicka på <b>Mer info → Kör ändå</b>. Det är bara en varning, inget blockeras eller tas bort.",
    "arch_apple"     => "Apple Silicon & Intel",
    "arch_x86"       => "Intel / AMD (64-bit)",
    "arch_arm"       => "ARM (64-bit)",
    "arch_universal" => "Universal",
    "dl_t_dmg"      => "macOS-diskavbildning",
    "dl_f_dmg"      => ".dmg — dra till Program",
    "dl_t_pkg"      => "macOS-installerare",
    "dl_f_pkg"      => ".pkg",
    "dl_t_exe"      => "Windows-installerare",
    "dl_f_exe"      => ".exe",
    "dl_t_winzip"   => "Windows portabel",
    "dl_f_winzip"   => ".zip — packa upp &amp; kör, ingen installation",
    "dl_t_appimage" => "AppImage",
    "dl_f_appimage" => "körs på alla distributioner — ingen installation",
    "dl_t_deb"      => ".deb-paket",
    "dl_f_deb"      => "för Debian / Ubuntu",
    "dl_t_rpm"      => ".rpm-paket",
    "dl_f_rpm"      => "för Fedora / RHEL",
    "dl_t_flatpak"  => "Flatpak",
    "dl_f_flatpak"  => "alla distributioner",
    "dl_recommended"=> "Rekommenderas",
    "dl_go"         => "Ladda ner →",
    "dl_all_name"   => "Alla paket på GitHub",
    "dl_all_sub"    => "senaste versionen",
    "dl_open"       => "Öppna →",
    "note_generated" => "↻ Genereras på servern från GitHub-releaserna för <code>slimture/dopeIPTV</code> — nya byggen dyker upp automatiskt.",
    "note_verify"    => "🔒 Verifiera din nedladdning — <a class='verify-link' href='/files/SHA256SUMS'>SHA-256-kontrollsummor</a> · <code>sha256sum -c SHA256SUMS</code>",

    // credits
    "cred_eyebrow" => "Öppen källkod",
    "cred_h2"      => "Fri programvara, på jättars axlar.",
    "cred_intro"   => "dopeIPTV är <b>fritt och har öppen källkod</b> under GPL-3.0-licensen — inga annonser, ingen spårning, inga konton. Det är byggt med, och tacksamt mot, dessa projekt och tjänster:",
    "cred_playback"      => "Uppspelning",
    "cred_interface"     => "Gränssnitt",
    "cred_casting"       => "Casting",
    "cred_metadata"      => "Metadata &amp; omslag",
    "cred_watched"       => "Synk av sedda",
    "cred_licences"      => "Licenser",
    "cred_licences_link" => "GPL-3.0 &amp; tredje part",
    "disclaimer" => "Denna produkt använder TMDB:s API men stöds eller certifieras inte av TMDB. Denna produkt använder Trakts API men stöds eller certifieras inte av Trakt. Alla varumärken tillhör sina respektive ägare.",

    // footer
    "footer_releases" => "Utgåvor",
    "footer_docs"     => "Dokumentation",
    "lang_label"      => "Språk",
];
