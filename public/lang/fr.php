<?php
/**
 * Français (fr) — traduction complète de iptv.dope.rs. Les noms propres et
 * termes techniques (Xtream Codes, M3U, AppImage, mpv, GitHub …) restent en
 * anglais. Les attributs HTML dans les chaînes utilisent des apostrophes
 * simples pour que les valeurs restent des chaînes PHP entre guillemets.
 */
return [
    // <head>
    "meta_title"   => "dopeIPTV — le lecteur IPTV Linux avec EPG & timeshift",
    "meta_desc"    => "Lecteur IPTV rapide et open source pour Xtream Codes & M3U — guide EPG complet, timeshift, catch-up, enregistrement et multiview. Linux, macOS & Windows.",
    "meta_keywords"=> "lecteur IPTV, lecteur IPTV Linux, Xtream Codes, M3U, EPG, XMLTV, timeshift, TV en différé, enregistrement IPTV, multiview IPTV, regarder plusieurs chaînes, IPTV multi-écran, IPTV Linux, IPTV macOS, IPTV Windows, dopeIPTV",

    // header nav
    "nav_features"    => "Fonctionnalités",
    "nav_screenshots" => "Captures",
    "nav_download"    => "Télécharger",
    "nav_github"      => "GitHub",
    "nav_download_btn"=> "Télécharger",

    // hero
    "hero_eyebrow" => "À l'antenne · version",
    "hero_h1"      => "TV en direct, catch-up et enregistrements — dans une seule application <span class='hl'>de bureau native</span>.",
    "hero_lede"    => "Un lecteur IPTV rapide et moderne pour Xtream Codes &amp; M3U — avec guide EPG complet, timeshift et catch-up en direct, enregistrement en un clic et multiview jusqu'à neuf chaînes à la fois. Basé sur mpv, conçu pour Linux — et fonctionne aussi sous macOS &amp; Windows.",
    "hero_cta"     => "Télécharger pour votre système",
    "hero_source"  => "Voir le code source",
    "hero_free"    => "gratuit &amp; open source",

    // feature strip chips
    "chip_timeshift" => "Timeshift / catch-up",
    "chip_languages" => "26 langues",

    // features
    "feat_eyebrow" => "Tout sur un seul écran",
    "feat_h2"      => "Pensé pour la façon dont on regarde vraiment la télé.",
    "feat_intro"   => "Pas une médiathèque détournée — un client TV, avec la timeline, le guide et l'enregistreur là où vous les attendez.",
    "feat_c1_h" => "Timeshift &amp; catch-up",
    "feat_c1_p" => "Revenez en arrière dans l'archive d'une chaîne sur une timeline en direct, ou sautez directement à un programme passé depuis le guide.",
    "feat_mv_h" => "Multiview",
    "feat_mv_p" => "Regardez jusqu'à neuf chaînes en direct à la fois dans une grille — mélangez différentes playlists, cliquez sur l'une pour le son, avec timeshift et sous-titres par fenêtre.",
    "feat_c2_h" => "Mettre le direct en pause",
    "feat_c2_p" => "Pause et reprise façon magnétoscope, derrière le direct — le lecteur indique exactement de combien vous êtes en retard.",
    "feat_c3_h" => "Guide EPG complet",
    "feat_c3_p" => "Une vraie grille de programmes avec recherche, rappels et une liste configurable de ce qui arrive ensuite.",
    "feat_c4_h" => "Enregistrement en un clic",
    "feat_c4_p" => "Enregistrez le flux que vous regardez via une seule connexion, avec minuteries, limites de taille et une bibliothèque d'enregistrements.",
    "feat_c5_h" => "Multi-fournisseur",
    "feat_c5_p" => "Plusieurs playlists Xtream ou M3U côte à côte, chacune avec son propre EPG, son actualisation automatique et son URL de guide personnalisée.",
    "feat_c6_h" => "Lecture fluide",
    "feat_c6_p" => "Un moteur mpv intégré, avec Chromecast, synchronisation Trakt, thèmes et contrôle complet au clavier.",

    // screenshots
    "shots_eyebrow" => "Un aperçu de l'intérieur",
    "shots_h2"      => "Épuré, sombre, et qui ne gêne pas.",
    "shot_ph"       => "capture",
    "shot_main_alt" => "Fenêtre principale de dopeIPTV avec la liste des chaînes, le guide et la vidéo",
    "shot_main_t"   => "Chaînes &amp; lecteur",
    "shot_main_c"   => "la liste, le guide et la vidéo dans une seule interface.",
    "shot_epg_alt"  => "Grille du guide des programmes EPG de dopeIPTV",
    "shot_epg_t"    => "Guide des programmes",
    "shot_epg_c"    => "vue en grille avec marqueurs de catch-up.",
    "shot_ts_alt"   => "Timeline de timeshift de dopeIPTV parcourant l'archive d'une chaîne",
    "shot_ts_t"     => "Timeline de timeshift",
    "shot_ts_c"     => "parcourez l'archive, le bord du direct est marqué.",
    "shot_rec_alt"  => "Bibliothèque d'enregistrements de dopeIPTV avec minuteries",
    "shot_rec_t"    => "Enregistrements",
    "shot_rec_c"    => "minuteries, limites de stockage et lecture.",

    // download
    "dl_eyebrow" => "Obtenir dopeIPTV",
    "dl_h2"      => "Téléchargez la dernière version.",
    "dl_latest"  => "dernière",
    "os_help_linux"   => "Pas sûr ? Prenez l'<b>AppImage</b> — elle fonctionne sur n'importe quelle distribution sans installation. Choisissez <b>.deb</b> sur Debian/Ubuntu. Prenez <b>Intel / AMD</b> sauf si vous avez une machine ARM (Raspberry Pi, serveur ARM).",
    "os_help_macos"   => "Une seule image fonctionne à la fois sur Apple Silicon (série M) et sur les Mac Intel.",
    "os_help_windows" => "Version portable — décompressez et lancez, rien à installer. La plateforme la plus récente, encore en cours de finition.",
    "os_install_linux"   => "🐧 <b>AppImage :</b> rendez-la exécutable et lancez-la — rien à installer : <code>chmod +x dopeIPTV-*.AppImage &amp;&amp; ./dopeIPTV-*.AppImage</code>. <b>.deb</b> (Debian/Ubuntu) : <code>sudo apt install ./dopeIPTV-*.deb</code>. <b>.rpm</b> (Fedora/RHEL) : <code>sudo dnf install ./dopeIPTV-*.rpm</code>.",
    "os_install_macos"   => "🍎 Ouvrez le <code>.dmg</code> et glissez dopeIPTV dans Applications. Comme l'app n'est pas encore notariée par Apple, le premier lancement peut être bloqué — <b>clic droit sur l'app → Ouvrir</b>, puis <b>Ouvrir</b> dans la boîte de dialogue (ou autorisez-la dans <b>Réglages Système → Confidentialité &amp; sécurité → Ouvrir quand même</b>). Si macOS indique plutôt que l'app est <b>« endommagée »</b>, supprimez l'indicateur de téléchargement dans le Terminal : <code>xattr -dr com.apple.quarantine /Applications/dopeIPTV.app</code>. C'est sans risque — l'avertissement signifie seulement que le build n'est pas signé.",
    "os_install_windows" => "🪟 Décompressez le dossier et lancez <code>dopeiptv.exe</code>. Comme l'app n'est pas encore signée, SmartScreen peut afficher <b>« Windows a protégé votre ordinateur »</b> — cliquez sur <b>Informations complémentaires → Exécuter quand même</b>. Ce n'est qu'un avertissement, rien n'est bloqué ni supprimé.",
    "arch_apple"     => "Apple Silicon & Intel",
    "arch_x86"       => "Intel / AMD (64 bits)",
    "arch_arm"       => "ARM (64 bits)",
    "arch_universal" => "Universel",
    "dl_t_dmg"      => "Image disque macOS",
    "dl_f_dmg"      => ".dmg — glissez dans Applications",
    "dl_t_pkg"      => "Installateur macOS",
    "dl_f_pkg"      => ".pkg",
    "dl_t_exe"      => "Installateur Windows",
    "dl_f_exe"      => ".exe",
    "dl_t_winzip"   => "Windows portable",
    "dl_f_winzip"   => ".zip — décompressez &amp; lancez, sans installation",
    "dl_t_appimage" => "AppImage",
    "dl_f_appimage" => "fonctionne sur toute distro — sans installation",
    "dl_t_deb"      => "Paquet .deb",
    "dl_f_deb"      => "pour Debian / Ubuntu",
    "dl_t_rpm"      => "Paquet .rpm",
    "dl_f_rpm"      => "pour Fedora / RHEL",
    "dl_t_flatpak"  => "Flatpak",
    "dl_f_flatpak"  => "toutes distros",
    "dl_recommended"=> "Recommandé",
    "dl_go"         => "Télécharger →",
    "dl_all_name"   => "Tous les paquets sur GitHub",
    "dl_all_sub"    => "dernière version",
    "dl_open"       => "Ouvrir →",
    "note_generated" => "↻ Généré sur le serveur à partir des releases GitHub de <code>slimture/dopeIPTV</code> — les nouvelles versions apparaissent automatiquement.",
    "note_verify"    => "🔒 Vérifiez votre téléchargement — <a class='verify-link' href='/files/SHA256SUMS'>sommes SHA-256</a> · <code>sha256sum -c SHA256SUMS</code>",

    // credits
    "cred_eyebrow" => "Open source",
    "cred_h2"      => "Un logiciel libre, sur les épaules de géants.",
    "cred_intro"   => "dopeIPTV est <b>libre et open source</b> sous licence GPL-3.0 — sans publicité, sans pistage, sans comptes. Il est construit avec, et reconnaissant envers, ces projets et services :",
    "cred_playback"      => "Lecture",
    "cred_interface"     => "Interface",
    "cred_casting"       => "Casting",
    "cred_metadata"      => "Métadonnées &amp; visuels",
    "cred_watched"       => "Synchro des vus",
    "cred_licences"      => "Licences",
    "cred_licences_link" => "GPL-3.0 &amp; tiers",
    "disclaimer" => "Ce produit utilise l'API TMDB mais n'est ni approuvé ni certifié par TMDB. Ce produit utilise l'API Trakt mais n'est ni approuvé ni certifié par Trakt. Toutes les marques sont la propriété de leurs détenteurs respectifs.",

    // footer
    "footer_releases" => "Versions",
    "footer_docs"     => "Docs",
    "lang_label"      => "Langue",
];
