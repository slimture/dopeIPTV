<?php
/**
 * English — the source strings for iptv.dope.rs. Every other lang/<code>.php
 * mirrors these keys; a missing key falls back here (see i18n.php). HTML
 * attributes inside strings use single quotes so the values stay plain
 * double-quoted PHP.
 */
return [
    // <head>
    "meta_title"   => "dopeIPTV — the Linux IPTV player with EPG, timeshift, recording & multiview",
    "meta_desc"    => "dopeIPTV is a fast, open-source desktop IPTV player for Xtream Codes & M3U with a full EPG guide, live timeshift, catch-up TV, recording and multiview — watch up to nine channels at once. Built for Linux — also on macOS and Windows.",
    "meta_keywords"=> "IPTV player, Linux IPTV player, Xtream Codes, M3U, EPG, XMLTV, timeshift, catch-up TV, IPTV recording, IPTV multiview, watch multiple channels, multi-screen IPTV, IPTV player Linux, macOS IPTV, Windows IPTV, dopeIPTV",

    // header nav
    "nav_features"    => "Features",
    "nav_screenshots" => "Screenshots",
    "nav_download"    => "Download",
    "nav_github"      => "GitHub",
    "nav_download_btn"=> "Download",

    // hero
    "hero_eyebrow" => "On air · version",
    "hero_h1"      => "Live TV, catch-up and recordings — in one <span class='hl'>native desktop</span> app.",
    "hero_lede"    => "A fast, keyboard-driven IPTV player for Xtream Codes &amp; M3U. Scrub back into a channel's archive, pause live TV, browse the full EPG, record, and watch up to nine channels at once in multiview — with a built-in mpv player. Built for Linux; runs on macOS &amp; Windows too.",
    "hero_cta"     => "Download for your system",
    "hero_source"  => "View source",
    "hero_free"    => "free &amp; open source",

    // feature strip chips (proper nouns stay untranslated in the template)
    "chip_timeshift" => "Timeshift / catch-up",
    "chip_languages" => "8 languages",

    // features
    "feat_eyebrow" => "Everything on one screen",
    "feat_h2"      => "Built for how people actually watch TV.",
    "feat_intro"   => "Not a repurposed media library — a TV client, with the timeline, the guide and the recorder where you expect them.",
    "feat_c1_h" => "Timeshift &amp; catch-up",
    "feat_c1_p" => "Scrub back into a channel's archive on a live timeline, or jump straight to a past programme from the guide.",
    "feat_mv_h" => "Multiview",
    "feat_mv_p" => "Watch up to nine live channels at once in a grid — mix different playlists, click one for audio, with per-window timeshift and subtitles.",
    "feat_c2_h" => "Pause live TV",
    "feat_c2_p" => "DVR-style pause and resume behind live — the player shows exactly how far behind you are.",
    "feat_c3_h" => "Full EPG guide",
    "feat_c3_p" => "A real programme grid with search, reminders and a configurable list of what's coming up next.",
    "feat_c4_h" => "One-click recording",
    "feat_c4_p" => "Record the stream you're watching over a single connection, with timers, size caps and a Recordings library.",
    "feat_c5_h" => "Multi-provider",
    "feat_c5_p" => "Several Xtream or M3U playlists side by side, each with its own EPG, auto-refresh and custom guide URL.",
    "feat_c6_h" => "Buttery playback",
    "feat_c6_p" => "A built-in mpv engine, with Chromecast, Trakt sync, themes and full keyboard control.",

    // screenshots
    "shots_eyebrow" => "A look inside",
    "shots_h2"      => "Clean, dark, and out of your way.",
    "shot_ph"       => "screenshot",
    "shot_main_alt" => "dopeIPTV main window with the channel list, guide and video",
    "shot_main_t"   => "Channels &amp; player",
    "shot_main_c"   => "the list, the guide and the video in one layout.",
    "shot_epg_alt"  => "dopeIPTV EPG programme guide grid",
    "shot_epg_t"    => "Programme guide",
    "shot_epg_c"    => "grid view with catch-up markers.",
    "shot_ts_alt"   => "dopeIPTV timeshift timeline scrubbing a channel archive",
    "shot_ts_t"     => "Timeshift timeline",
    "shot_ts_c"     => "scrub the archive, live edge marked.",
    "shot_rec_alt"  => "dopeIPTV recordings library with timers",
    "shot_rec_t"    => "Recordings",
    "shot_rec_c"    => "timers, storage caps and playback.",

    // download
    "dl_eyebrow" => "Get dopeIPTV",
    "dl_h2"      => "Download the latest release.",
    "dl_latest"  => "latest",
    "os_help_linux"   => "Not sure? Get the <b>AppImage</b> — it runs on any distribution with no install. Choose <b>.deb</b> on Debian/Ubuntu. Pick <b>Intel / AMD</b> unless you're on an ARM machine (Raspberry Pi, ARM server).",
    "os_help_macos"   => "One image works on both Apple Silicon (M-series) and Intel Macs.",
    "os_help_windows" => "Portable build — unzip and run, nothing to install. Newest platform, still being polished.",
    "os_install_linux"   => "🐧 <b>AppImage:</b> make it executable and run it — nothing to install: <code>chmod +x dopeIPTV-*.AppImage &amp;&amp; ./dopeIPTV-*.AppImage</code>. <b>.deb</b> (Debian/Ubuntu): <code>sudo apt install ./dopeIPTV-*.deb</code>. <b>.rpm</b> (Fedora/RHEL): <code>sudo dnf install ./dopeIPTV-*.rpm</code>.",
    "os_install_macos"   => "🍎 Open the <code>.dmg</code> and drag dopeIPTV to Applications. Because the app isn't notarized by Apple yet, the first launch may be blocked — <b>right-click the app → Open</b>, then <b>Open</b> in the dialog (or allow it under <b>System Settings → Privacy &amp; Security → Open Anyway</b>). If macOS instead says the app is <b>“damaged”</b>, clear the download flag in Terminal: <code>xattr -dr com.apple.quarantine /Applications/dopeIPTV.app</code>. It's safe — the warning only means the build isn't code-signed.",
    "os_install_windows" => "🪟 Unzip the folder and run <code>dopeiptv.exe</code>. Because the app isn't code-signed yet, SmartScreen may show <b>“Windows protected your PC”</b> — click <b>More info → Run anyway</b>. It's only a warning, nothing is blocked or removed.",
    "arch_apple"     => "Apple Silicon & Intel",
    "arch_x86"       => "Intel / AMD (64-bit)",
    "arch_arm"       => "ARM (64-bit)",
    "arch_universal" => "Universal",
    "dl_t_dmg"      => "macOS disk image",
    "dl_f_dmg"      => ".dmg — drag to Applications",
    "dl_t_pkg"      => "macOS installer",
    "dl_f_pkg"      => ".pkg",
    "dl_t_exe"      => "Windows installer",
    "dl_f_exe"      => ".exe",
    "dl_t_winzip"   => "Windows portable",
    "dl_f_winzip"   => ".zip — unzip &amp; run, no install",
    "dl_t_appimage" => "AppImage",
    "dl_f_appimage" => "runs on any distro — no install",
    "dl_t_deb"      => ".deb package",
    "dl_f_deb"      => "for Debian / Ubuntu",
    "dl_t_rpm"      => ".rpm package",
    "dl_f_rpm"      => "for Fedora / RHEL",
    "dl_t_flatpak"  => "Flatpak",
    "dl_f_flatpak"  => "all distros",
    "dl_recommended"=> "Recommended",
    "dl_go"         => "Download →",
    "dl_all_name"   => "All packages on GitHub",
    "dl_all_sub"    => "latest release",
    "dl_open"       => "Open →",
    "note_generated" => "↻ Generated on the server from the <code>slimture/dopeIPTV</code> GitHub releases — new builds appear automatically.",
    "note_verify"    => "🔒 Verify your download — <a class='verify-link' href='/files/SHA256SUMS'>SHA-256 checksums</a> · <code>sha256sum -c SHA256SUMS</code>",

    // credits
    "cred_eyebrow" => "Open source",
    "cred_h2"      => "Free software, standing on giants.",
    "cred_intro"   => "dopeIPTV is <b>free and open source</b> under the GPL-3.0 licence — no ads, no tracking, no accounts. It's built with, and grateful to, these projects and services:",
    "cred_playback"      => "Playback",
    "cred_interface"     => "Interface",
    "cred_casting"       => "Casting",
    "cred_metadata"      => "Metadata &amp; artwork",
    "cred_watched"       => "Watched sync",
    "cred_licences"      => "Licences",
    "cred_licences_link" => "GPL-3.0 &amp; third-party",
    "disclaimer" => "This product uses the TMDB API but is not endorsed or certified by TMDB. This product uses the Trakt API but is not endorsed or certified by Trakt. All trademarks are the property of their respective owners.",

    // footer
    "footer_releases" => "Releases",
    "footer_docs"     => "Docs",
    "lang_label"      => "Language",
];
