<?php
/**
 * Türkçe (tr) — iptv.dope.rs için tam çeviri. Özel adlar ve teknik terimler
 * (Xtream Codes, M3U, AppImage, mpv, GitHub …) İngilizce kalır. HTML
 * öznitelikleri tek tırnak kullanır, böylece değerler çift tırnaklı PHP
 * dizeleri olarak kalır.
 */
return [
    // <head>
    "meta_title"   => "dopeIPTV — Linux için EPG & timeshift'li IPTV oynatıcı",
    "meta_desc"    => "Xtream Codes & M3U için hızlı, açık kaynaklı IPTV oynatıcı — tam EPG rehberi, timeshift, catch-up, kayıt ve multiview. Linux, macOS & Windows.",
    "meta_keywords"=> "IPTV oynatıcı, Linux IPTV oynatıcı, Xtream Codes, M3U, EPG, XMLTV, timeshift, catch-up TV, IPTV kayıt, IPTV multiview, birden fazla kanal izleme, çoklu ekran IPTV, IPTV Linux, IPTV macOS, IPTV Windows, dopeIPTV",

    // header nav
    "nav_features"    => "Özellikler",
    "nav_screenshots" => "Ekran görüntüleri",
    "nav_download"    => "İndir",
    "nav_github"      => "GitHub",
    "nav_download_btn"=> "İndir",

    // hero
    "hero_eyebrow" => "Yayında · sürüm",
    "hero_h1"      => "Canlı TV, catch-up ve kayıtlar — tek bir <span class='hl'>yerel masaüstü</span> uygulamasında.",
    "hero_lede"    => "Xtream Codes &amp; M3U için hızlı ve modern bir IPTV oynatıcı — tam EPG rehberi, canlı timeshift ve catch-up, tek tıkla kayıt ve aynı anda dokuz kanala kadar multiview ile. mpv üzerine kurulu, Linux için yapıldı — ve macOS &amp; Windows'ta da çalışır.",
    "hero_cta"     => "Sisteminiz için indirin",
    "hero_source"  => "Kaynağı görüntüle",
    "hero_free"    => "ücretsiz &amp; açık kaynak",

    // feature strip chips
    "chip_timeshift" => "Timeshift / catch-up",
    "chip_languages" => "26 dil",

    // features
    "feat_eyebrow" => "Her şey tek ekranda",
    "feat_h2"      => "İnsanların TV'yi gerçekte nasıl izlediğine göre tasarlandı.",
    "feat_intro"   => "Yeniden düzenlenmiş bir medya kütüphanesi değil — zaman çizelgesi, rehber ve kaydedicinin tam beklediğiniz yerde olduğu bir TV istemcisi.",
    "feat_c1_h" => "Timeshift &amp; catch-up",
    "feat_c1_p" => "Canlı bir zaman çizelgesinde bir kanalın arşivine geri sarın veya rehberden doğrudan geçmiş bir programa atlayın.",
    "feat_mv_h" => "Multiview",
    "feat_mv_p" => "Bir ızgarada aynı anda dokuz canlı kanala kadar izleyin — farklı oynatma listelerini karıştırın, ses için birine tıklayın, pencere başına timeshift ve altyazı ile.",
    "feat_c2_h" => "Canlı TV'yi duraklat",
    "feat_c2_p" => "Canlının gerisinde DVR tarzı duraklatma ve sürdürme — oynatıcı tam olarak ne kadar geride olduğunuzu gösterir.",
    "feat_c3_h" => "Tam EPG rehberi",
    "feat_c3_p" => "Arama, hatırlatıcılar ve sıradakilerin yapılandırılabilir bir listesiyle gerçek bir program ızgarası.",
    "feat_c4_h" => "Tek tıkla kayıt",
    "feat_c4_p" => "İzlediğiniz yayını tek bir bağlantı üzerinden kaydedin; zamanlayıcılar, boyut sınırları ve bir Kayıtlar kütüphanesi ile.",
    "feat_c5_h" => "Çoklu sağlayıcı",
    "feat_c5_p" => "Yan yana birden fazla Xtream veya M3U oynatma listesi; her biri kendi EPG'si, otomatik yenilemesi ve özel rehber URL'si ile.",
    "feat_c6_h" => "Akıcı oynatma",
    "feat_c6_p" => "Chromecast, Trakt senkronizasyonu, temalar ve tam klavye kontrolüyle yerleşik bir mpv motoru.",

    // screenshots
    "shots_eyebrow" => "İçeriden bir bakış",
    "shots_h2"      => "Temiz, koyu ve yolunuzdan çekilmiş.",
    "shot_ph"       => "ekran görüntüsü",
    "shot_main_alt" => "Kanal listesi, rehber ve video ile dopeIPTV ana penceresi",
    "shot_main_t"   => "Kanallar &amp; oynatıcı",
    "shot_main_c"   => "liste, rehber ve video tek bir düzende.",
    "shot_epg_alt"  => "dopeIPTV EPG program rehberi ızgarası",
    "shot_epg_t"    => "Program rehberi",
    "shot_epg_c"    => "catch-up işaretleriyle ızgara görünümü.",
    "shot_ts_alt"   => "Bir kanal arşivinde gezinen dopeIPTV timeshift zaman çizelgesi",
    "shot_ts_t"     => "Timeshift zaman çizelgesi",
    "shot_ts_c"     => "arşivde gezinin, canlı kenar işaretli.",
    "shot_rec_alt"  => "Zamanlayıcılarla dopeIPTV kayıt kütüphanesi",
    "shot_rec_t"    => "Kayıtlar",
    "shot_rec_c"    => "zamanlayıcılar, depolama sınırları ve oynatma.",

    // download
    "dl_eyebrow" => "dopeIPTV'yi edinin",
    "dl_h2"      => "En son sürümü indirin.",
    "dl_latest"  => "en son",
    "os_help_linux"   => "Emin değil misiniz? <b>AppImage</b>'i alın — herhangi bir dağıtımda kurulum gerektirmeden çalışır. Debian/Ubuntu'da <b>.deb</b> seçin. ARM makineniz (Raspberry Pi, ARM sunucu) yoksa <b>Intel / AMD</b> alın.",
    "os_help_macos"   => "Tek bir görüntü hem Apple Silicon (M serisi) hem de Intel Mac'lerde çalışır.",
    "os_help_windows" => "Taşınabilir sürüm — çıkarın ve çalıştırın, kurulacak bir şey yok. En yeni platform, hâlâ üzerinde çalışılıyor.",
    "os_install_linux"   => "🐧 <b>AppImage:</b> çalıştırılabilir yapın ve başlatın — kurulacak bir şey yok: <code>chmod +x dopeIPTV-*.AppImage &amp;&amp; ./dopeIPTV-*.AppImage</code>. <b>.deb</b> (Debian/Ubuntu): <code>sudo apt install ./dopeIPTV-*.deb</code>. <b>.rpm</b> (Fedora/RHEL): <code>sudo dnf install ./dopeIPTV-*.rpm</code>.",
    "os_install_macos"   => "🍎 <code>.dmg</code>'yi açın ve dopeIPTV'yi Uygulamalar'a sürükleyin. Uygulama henüz Apple tarafından noterlenmediği (notarized) için ilk açılış engellenebilir — <b>uygulamaya sağ tıklayın → Aç</b>, ardından iletişim kutusunda <b>Aç</b> (veya <b>Sistem Ayarları → Gizlilik &amp; Güvenlik → Yine de Aç</b> altından izin verin). Bunun yerine macOS uygulamanın <b>“hasarlı”</b> olduğunu söylerse, indirme işaretini Terminal'de kaldırın: <code>xattr -dr com.apple.quarantine /Applications/dopeIPTV.app</code>. Güvenlidir — uyarı yalnızca yapının imzalanmadığı anlamına gelir.",
    "os_install_windows" => "🪟 Klasörü çıkarın ve <code>dopeiptv.exe</code>'yi çalıştırın. Uygulama henüz imzalanmadığı için SmartScreen <b>“Windows bilgisayarınızı korudu”</b> gösterebilir — <b>Ek bilgi → Yine de çalıştır</b>'a tıklayın. Bu yalnızca bir uyarıdır, hiçbir şey engellenmez veya kaldırılmaz.",
    "arch_apple"     => "Apple Silicon & Intel",
    "arch_x86"       => "Intel / AMD (64 bit)",
    "arch_arm"       => "ARM (64 bit)",
    "arch_universal" => "Evrensel",
    "dl_t_dmg"      => "macOS disk görüntüsü",
    "dl_f_dmg"      => ".dmg — Uygulamalar'a sürükleyin",
    "dl_t_pkg"      => "macOS yükleyici",
    "dl_f_pkg"      => ".pkg",
    "dl_t_exe"      => "Windows yükleyici",
    "dl_f_exe"      => ".exe",
    "dl_t_winzip"   => "Windows taşınabilir",
    "dl_f_winzip"   => ".zip — çıkarın &amp; çalıştırın, kurulum yok",
    "dl_t_appimage" => "AppImage",
    "dl_f_appimage" => "her dağıtımda çalışır — kurulum yok",
    "dl_t_deb"      => ".deb paketi",
    "dl_f_deb"      => "Debian / Ubuntu için",
    "dl_t_rpm"      => ".rpm paketi",
    "dl_f_rpm"      => "Fedora / RHEL için",
    "dl_t_flatpak"  => "Flatpak",
    "dl_f_flatpak"  => "tüm dağıtımlar",
    "dl_recommended"=> "Önerilen",
    "dl_go"         => "İndir →",
    "dl_all_name"   => "GitHub'daki tüm paketler",
    "dl_all_sub"    => "en son sürüm",
    "dl_open"       => "Aç →",
    "note_generated" => "↻ Sunucuda <code>slimture/dopeIPTV</code> GitHub sürümlerinden oluşturuldu — yeni yapılar otomatik olarak görünür.",
    "note_verify"    => "🔒 İndirmenizi doğrulayın — <a class='verify-link' href='/files/SHA256SUMS'>SHA-256 sağlama toplamları</a> · <code>sha256sum -c SHA256SUMS</code>",

    // credits
    "cred_eyebrow" => "Açık kaynak",
    "cred_h2"      => "Özgür yazılım, devlerin omuzlarında.",
    "cred_intro"   => "dopeIPTV, GPL-3.0 lisansı altında <b>özgür ve açık kaynaklıdır</b> — reklam yok, takip yok, hesap yok. Şu proje ve hizmetlerle oluşturulmuştur ve onlara minnettardır:",
    "cred_playback"      => "Oynatma",
    "cred_interface"     => "Arayüz",
    "cred_casting"       => "Yayınlama",
    "cred_metadata"      => "Meta veriler &amp; görseller",
    "cred_watched"       => "İzlenenler senkronizasyonu",
    "cred_licences"      => "Lisanslar",
    "cred_licences_link" => "GPL-3.0 &amp; üçüncü taraf",
    "disclaimer" => "Bu ürün TMDB API'sini kullanır ancak TMDB tarafından onaylanmamış veya sertifikalandırılmamıştır. Bu ürün Trakt API'sini kullanır ancak Trakt tarafından onaylanmamış veya sertifikalandırılmamıştır. Tüm ticari markalar ilgili sahiplerinin mülkiyetindedir.",

    // footer
    "footer_releases" => "Sürümler",
    "footer_docs"     => "Belgeler",
    "lang_label"      => "Dil",
];
