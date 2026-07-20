<?php
/**
 * dopeIPTV landing page - iptv.dope.rs
 * Server-renders the download list from releases.json (written by
 * sync-releases.php via cron). Zero client-side GitHub calls, so it stays
 * comfortably inside a strict same-origin CSP.
 *
 * Multilingual: the interface strings live in lang/<code>.php and the language
 * is chosen from ?lang=, a cookie or the browser (see i18n.php). Only languages
 * with a translation file advertise themselves via hreflang / the switcher.
 */
require __DIR__ . '/i18n.php';   // defines h(), t(), lang_*(), i18n_*()

$SITE   = 'https://iptv.dope.rs';
$REPO   = 'https://github.com/slimture/dopeIPTV';
$DESC   = t('meta_desc');

$rel = @json_decode(@file_get_contents(__DIR__ . '/releases.json'), true);
$version = $rel['version'] ?? '0.7.0';
$assets  = $rel['assets'] ?? [];
$relDate = !empty($rel['published_at']) ? date('M j, Y', strtotime($rel['published_at'])) : '';

// Per-file download tallies, written by dl.php (the /dl.php?f=NAME redirector
// every download link goes through). Kept one level above the web root so it's
// neither web-served nor pruned by the release sync. Missing/unreadable -> no
// counts shown and the page still works.
$dlCounts = @json_decode(@file_get_contents(dirname(__DIR__) . '/downloads.json'), true);
if (!is_array($dlCounts)) { $dlCounts = []; }
function dl_count(array $counts, string $name): int {
    return (int)($counts[$name] ?? 0);
}

/**
 * Plain-language download metadata derived from the asset filename, grouped by
 * OS. The human labels are localized via t(); the OS group key stays a fixed
 * ASCII token ('Linux'/'macOS'/'Windows'/'Other') used for grouping + icons.
 */
function dl_meta(string $name): array {
    $n = strtolower($name);
    $arm = (strpos($n, 'arm') !== false || strpos($n, 'aarch64') !== false);
    $cpu = $arm ? t('arch_arm') : t('arch_x86');
    if (str_ends_with($n, '.dmg'))
        return ['os'=>'macOS','icon'=>'🍎','title'=>t('dl_t_dmg'),'fmt'=>t('dl_f_dmg'),'arch'=>t('arch_apple'),'rank'=>10,'rec'=>true];
    if (str_ends_with($n, '.pkg'))
        return ['os'=>'macOS','icon'=>'🍎','title'=>t('dl_t_pkg'),'fmt'=>t('dl_f_pkg'),'arch'=>t('arch_apple'),'rank'=>20,'rec'=>false];
    if (str_ends_with($n, '.exe') || str_ends_with($n, '.msi'))
        return ['os'=>'Windows','icon'=>'🪟','title'=>t('dl_t_exe'),'fmt'=>t('dl_f_exe'),'arch'=>$cpu,'rank'=>10,'rec'=>true];
    if (str_ends_with($n, '.zip') && strpos($n, 'win') !== false)
        return ['os'=>'Windows','icon'=>'🪟','title'=>t('dl_t_winzip'),'fmt'=>t('dl_f_winzip'),'arch'=>$cpu,'rank'=>10,'rec'=>true];
    if (str_ends_with($n, '.appimage'))
        return ['os'=>'Linux','icon'=>'🐧','title'=>t('dl_t_appimage'),'fmt'=>t('dl_f_appimage'),'arch'=>$cpu,'rank'=>($arm?12:10),'rec'=>!$arm];
    if (str_ends_with($n, '.deb'))
        return ['os'=>'Linux','icon'=>'🐧','title'=>t('dl_t_deb'),'fmt'=>t('dl_f_deb'),'arch'=>$cpu,'rank'=>($arm?22:20),'rec'=>false];
    if (str_ends_with($n, '.rpm'))
        return ['os'=>'Linux','icon'=>'🐧','title'=>t('dl_t_rpm'),'fmt'=>t('dl_f_rpm'),'arch'=>$cpu,'rank'=>30,'rec'=>false];
    if (str_ends_with($n, '.flatpak'))
        return ['os'=>'Linux','icon'=>'📦','title'=>t('dl_t_flatpak'),'fmt'=>t('dl_f_flatpak'),'arch'=>t('arch_universal'),'rank'=>40,'rec'=>false];
    return ['os'=>'Other','icon'=>'📦','title'=>$name,'fmt'=>'','arch'=>'','rank'=>99,'rec'=>false];
}

// Group the release assets by OS. Linux first - it's the focus.
$osOrder = ['Linux', 'macOS', 'Windows', 'Other'];
$osHelp  = [
    'Linux'   => t('os_help_linux'),
    'macOS'   => t('os_help_macos'),
    'Windows' => t('os_help_windows'),
];
// Per-OS "how to install" note, shown right under each OS's downloads so the
// steps sit with the files they apply to.
$osInstall = [
    'Linux'   => t('os_install_linux'),
    'macOS'   => t('os_install_macos'),
    'Windows' => t('os_install_windows'),
];
$groups = [];
foreach ($assets as $a) {
    $m = dl_meta($a['name'] ?? '');
    $groups[$m['os']][] = $a + ['_m' => $m];
}
foreach ($groups as &$g) {
    usort($g, fn($x, $y) => ($x['_m']['rank'] <=> $y['_m']['rank']));
}
unset($g);
?><!DOCTYPE html>
<html lang="<?= h(lang_code()) ?>" dir="<?= h(lang_dir()) ?>">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title><?= h(t('meta_title')) ?></title>
<meta name="description" content="<?= h($DESC) ?>">
<link rel="canonical" href="<?= h(i18n_canonical($SITE)) ?>">
<?= i18n_hreflang($SITE) ?>
<meta name="theme-color" content="#0f1218">
<meta name="robots" content="index,follow">
<meta name="keywords" content="<?= h(t('meta_keywords')) ?>">
<!-- Open Graph -->
<meta property="og:type" content="website">
<meta property="og:site_name" content="dopeIPTV">
<meta property="og:locale" content="<?= h(lang_locale()) ?>">
<meta property="og:title" content="<?= h(t('meta_title')) ?>">
<meta property="og:description" content="<?= h($DESC) ?>">
<meta property="og:url" content="<?= h(i18n_canonical($SITE)) ?>">
<meta property="og:image" content="<?= h($SITE) ?>/og-image.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<!-- Twitter -->
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="<?= h(t('meta_title')) ?>">
<meta name="twitter:description" content="<?= h($DESC) ?>">
<meta name="twitter:image" content="<?= h($SITE) ?>/og-image.png">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="icon" href="/favicon.png" sizes="any">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="manifest" href="/site.webmanifest">
<link rel="stylesheet" href="/style.css">
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  "name": "dopeIPTV",
  "operatingSystem": "Linux, macOS, Windows",
  "applicationCategory": "MultimediaApplication",
  "description": <?= json_encode($DESC) ?>,
  "softwareVersion": "<?= h($version) ?>",
  "url": "<?= h($SITE) ?>/",
  "downloadUrl": "<?= h($SITE) ?>/#download",
  "license": "<?= h($REPO) ?>/blob/main/LICENSE",
  "offers": { "@type": "Offer", "price": "0", "priceCurrency": "USD" }
}
</script>
</head>
<body>
<header class="bar">
  <div class="wrap">
    <a class="brand" href="#top"><span class="glyph">◉</span><b>dopeIPTV</b></a>
    <nav class="links">
      <a class="navlink" href="#features"><?= h(t('nav_features')) ?></a>
      <a class="navlink" href="#shots"><?= h(t('nav_screenshots')) ?></a>
      <a class="navlink" href="#download"><?= h(t('nav_download')) ?></a>
      <a class="navlink" href="<?= h($REPO) ?>"><?= h(t('nav_github')) ?></a>
<?php $avail = i18n_available(); if (count($avail) > 1): ?>
      <details class="langpick">
        <summary title="<?= h(t('lang_label')) ?>">🌐 <?= h(I18N_NAMES[lang_code()] ?? lang_code()) ?></summary>
        <div class="langmenu">
<?php foreach ($avail as $code): ?>
          <a href="/?lang=<?= h($code) ?>"<?= $code === lang_code() ? ' class="on"' : '' ?>><?= h(I18N_NAMES[$code] ?? $code) ?></a>
<?php endforeach; ?>
        </div>
      </details>
<?php endif; ?>
      <a class="btn primary" href="#download"><?= h(t('nav_download_btn')) ?></a>
    </nav>
  </div>
</header>

<main id="top">
  <section class="hero">
    <div class="wrap hero-grid">
      <div>
        <span class="eyebrow"><span class="dot"></span> <?= h(t('hero_eyebrow')) ?> <?= h($version) ?></span>
        <h1><?= t('hero_h1') ?></h1>
        <p class="lede"><?= t('hero_lede') ?></p>
        <div class="cta-row">
          <a class="btn primary" id="heroDownload" href="#download"><?= h(t('hero_cta')) ?></a>
          <a class="btn ghost" href="<?= h($REPO) ?>"><?= h(t('hero_source')) ?></a>
        </div>
        <p class="platnote"><b id="osLabel">Linux · macOS · Windows</b> — <?= t('hero_free') ?></p>
      </div>
      <div class="mock" aria-hidden="true">
        <div class="titlebar"><span class="tl-dot"></span><span class="tl-dot"></span><span class="tl-dot"></span></div>
        <div class="body">
          <div class="chans">
            <div class="chan active"><span class="n"></span> SVT1 HD <span class="live-tag">LIVE</span></div>
            <div class="chan"><span class="n"></span> BBC One</div>
            <div class="chan"><span class="n"></span> Sky Sports</div>
            <div class="chan"><span class="n"></span> Discovery</div>
            <div class="chan"><span class="n"></span> Arte</div>
            <div class="chan"><span class="n"></span> National Geo</div>
          </div>
          <div class="stage">
            <div class="screen"><span class="scan"></span></div>
            <div class="timeline">
              <div class="tl-track"><span class="tl-fill"></span><span class="tl-head"></span></div>
              <div class="tl-meta"><span>19:24 · Evening News</span><span class="behind">− 12 min</span></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>

  <div class="strip">
    <div class="wrap">
      <span class="chip">Xtream Codes</span><span class="chip">M3U / M3U8</span>
      <span class="chip">XMLTV EPG</span><span class="chip"><?= h(t('chip_timeshift')) ?></span>
      <span class="chip">Multiview</span><span class="chip">Chromecast</span><span class="chip">Trakt</span><span class="chip"><?= h(t('chip_languages')) ?></span>
    </div>
  </div>

  <section id="features">
    <div class="wrap">
      <div class="sec-head">
        <span class="eyebrow"><?= h(t('feat_eyebrow')) ?></span>
        <h2><?= h(t('feat_h2')) ?></h2>
        <p><?= t('feat_intro') ?></p>
      </div>
      <div class="grid">
        <div class="card"><div class="ic">⏱</div><h3><?= t('feat_c1_h') ?></h3><p><?= t('feat_c1_p') ?></p></div>
        <div class="card"><div class="ic">⊞</div><h3><?= t('feat_mv_h') ?></h3><p><?= t('feat_mv_p') ?></p></div>
        <div class="card"><div class="ic">⏸</div><h3><?= t('feat_c2_h') ?></h3><p><?= t('feat_c2_p') ?></p></div>
        <div class="card"><div class="ic">▦</div><h3><?= t('feat_c3_h') ?></h3><p><?= t('feat_c3_p') ?></p></div>
        <div class="card"><div class="ic">⏺</div><h3><?= t('feat_c4_h') ?></h3><p><?= t('feat_c4_p') ?></p></div>
        <div class="card"><div class="ic">◉</div><h3><?= t('feat_c5_h') ?></h3><p><?= t('feat_c5_p') ?></p></div>
        <div class="card"><div class="ic">▶</div><h3><?= t('feat_c6_h') ?></h3><p><?= t('feat_c6_p') ?></p></div>
      </div>
    </div>
  </section>

  <section id="shots" style="padding-top:0;">
    <div class="wrap">
      <div class="sec-head">
        <span class="eyebrow"><?= h(t('shots_eyebrow')) ?></span>
        <h2><?= h(t('shots_h2')) ?></h2>
      </div>
      <div class="shots">
<?php
$shots = [
    ['main.png',       t('shot_main_alt'), t('shot_main_t'), t('shot_main_c')],
    ['epg.png',        t('shot_epg_alt'),  t('shot_epg_t'),  t('shot_epg_c')],
    ['timeshift.png',  t('shot_ts_alt'),   t('shot_ts_t'),   t('shot_ts_c')],
    ['recordings.png', t('shot_rec_alt'),  t('shot_rec_t'),  t('shot_rec_c')],
];
foreach ($shots as [$file, $alt, $title, $cap]):
    $exists = is_file(__DIR__ . '/screenshots/' . $file);
?>
        <div class="shot">
<?php if ($exists): ?>
          <img src="/screenshots/<?= h($file) ?>" alt="<?= h($alt) ?>" width="1280" height="800" loading="lazy">
<?php else: ?>
          <div class="ph"><?= h(t('shot_ph')) ?> · <?= $title ?></div>
<?php endif; ?>
          <div class="cap"><b><?= $title ?></b> — <?= $cap ?></div>
        </div>
<?php endforeach; ?>
      </div>
    </div>
  </section>

  <section id="download" style="padding-top:0;">
    <div class="wrap">
      <div class="dl-head">
        <div class="sec-head" style="margin-bottom:0;">
          <span class="eyebrow"><?= h(t('dl_eyebrow')) ?></span>
          <h2><?= h(t('dl_h2')) ?></h2>
        </div>
        <span class="release-tag"><b>v<?= h($version) ?></b><?= $relDate ? ' · ' . h($relDate) : ' · ' . h(t('dl_latest')) ?></span>
      </div>
<?php if ($assets): foreach ($osOrder as $os): if (empty($groups[$os])) continue; ?>
      <div class="dl-group">
        <div class="dl-group-head">
          <span class="dl-os-ico"><?= h($groups[$os][0]['_m']['icon']) ?></span>
          <h3><?= h($os) ?></h3>
        </div>
<?php if (!empty($osHelp[$os])): ?>
        <p class="dl-guide"><?= $osHelp[$os] ?></p>
<?php endif; ?>
        <div class="dls">
<?php foreach ($groups[$os] as $a):
          $m = $a['_m'];
          $size = '';
          if (preg_match('/([\d.]+\s?(?:KB|MB|GB|B))\s*$/i', $a['sub'] ?? '', $mm)) { $size = $mm[1]; }
          $dlName = (string)($a['name'] ?? '');
          $dlHref = $dlName !== '' ? '/dl.php?f=' . rawurlencode($dlName) : ($a['url'] ?? '#');
          $dlN = dl_count($dlCounts, $dlName);
?>
          <a class="dl<?= $m['rec'] ? ' rec' : '' ?>" href="<?= h($dlHref) ?>" rel="nofollow"<?= !empty($a['sha256']) ? ' title="SHA-256: ' . h($a['sha256']) . '"' : '' ?>>
            <span class="meta">
              <span class="name"><?= h($m['title']) ?><?php if ($m['rec']): ?> <span class="badge"><?= h(t('dl_recommended')) ?></span><?php endif; ?></span>
              <span class="sub"><span class="arch"><?= h($m['arch']) ?></span><?= $m['fmt'] ? ' · ' . $m['fmt'] : '' ?><?= $size ? ' · ' . h($size) : '' ?><?php if ($dlN > 0): ?> · <span class="dl-count" title="downloads">↓ <?= number_format($dlN) ?></span><?php endif; ?></span>
            </span>
            <span class="go"><?= h(t('dl_go')) ?></span>
          </a>
<?php endforeach; ?>
        </div>
<?php if (!empty($osInstall[$os])): ?>
        <p class="autonote"><?= $osInstall[$os] ?></p>
<?php endif; ?>
      </div>
<?php endforeach; else: ?>
      <div class="dls">
        <a class="dl" href="<?= h($REPO) ?>/releases/latest" rel="nofollow">
          <span class="meta"><span class="name"><?= h(t('dl_all_name')) ?></span><span class="sub"><?= h(t('dl_all_sub')) ?></span></span>
          <span class="go"><?= h(t('dl_open')) ?></span>
        </a>
      </div>
<?php endif; ?>
      <p class="autonote"><?= t('note_generated') ?></p>
<?php if (is_file(__DIR__ . '/files/SHA256SUMS')): ?>
      <p class="autonote"><?= t('note_verify') ?></p>
<?php endif; ?>
    </div>
  </section>

  <section id="credits" style="padding-top:0;">
    <div class="wrap">
      <div class="sec-head">
        <span class="eyebrow"><?= h(t('cred_eyebrow')) ?></span>
        <h2><?= h(t('cred_h2')) ?></h2>
        <p><?= t('cred_intro') ?></p>
      </div>
      <div class="credits-grid">
        <div class="credit"><h3><?= t('cred_playback') ?></h3><p><a href="https://mpv.io" rel="noopener">mpv</a> &amp; <a href="https://ffmpeg.org" rel="noopener">FFmpeg</a>, via python-mpv</p></div>
        <div class="credit"><h3><?= t('cred_interface') ?></h3><p>Qt &amp; <a href="https://www.riverbankcomputing.com/software/pyqt/" rel="noopener">PyQt6</a></p></div>
        <div class="credit"><h3><?= t('cred_casting') ?></h3><p><a href="https://github.com/home-assistant-libs/pychromecast" rel="noopener">PyChromecast</a> — Google Cast</p></div>
        <div class="credit"><h3><?= t('cred_metadata') ?></h3><p><a href="https://www.themoviedb.org" rel="noopener">The Movie Database (TMDB)</a></p></div>
        <div class="credit"><h3><?= t('cred_watched') ?></h3><p><a href="https://trakt.tv" rel="noopener">Trakt</a></p></div>
        <div class="credit"><h3><?= t('cred_licences') ?></h3><p><a href="<?= h($REPO) ?>/blob/main/docs/THIRD-PARTY-LICENSES.md" rel="noopener"><?= t('cred_licences_link') ?></a></p></div>
      </div>
      <p class="disclaimer"><?= h(t('disclaimer')) ?></p>
    </div>
  </section>
</main>

<footer>
  <div class="wrap">
    <span class="v">dopeIPTV <?= h($version) ?> · © <?= date('Y') ?></span>
    <nav>
      <a href="<?= h($REPO) ?>">GitHub</a>
      <a href="<?= h($REPO) ?>/releases"><?= h(t('footer_releases')) ?></a>
      <a href="<?= h($REPO) ?>#readme"><?= h(t('footer_docs')) ?></a>
    </nav>
  </div>
</footer>
<script src="/app.js" defer></script>
</body>
</html>
