<?php
/**
 * dopeIPTV landing page - iptv.dope.rs
 * Server-renders the download list from releases.json (written by
 * sync-releases.php via cron). Zero client-side GitHub calls, so it stays
 * comfortably inside a strict same-origin CSP.
 */
$SITE   = 'https://iptv.dope.rs';
$REPO   = 'https://github.com/slimture/dopeIPTV';
$DESC   = 'dopeIPTV is a fast, open-source desktop IPTV player for Xtream Codes '
        . '& M3U with a full EPG guide, live timeshift, catch-up TV, recording '
        . 'and multiview — watch up to nine channels at once. '
        . 'For Linux, macOS and Windows.';

$rel = @json_decode(@file_get_contents(__DIR__ . '/releases.json'), true);
$version = $rel['version'] ?? '0.7.0';
$assets  = $rel['assets'] ?? [];
$relDate = !empty($rel['published_at']) ? date('M j, Y', strtotime($rel['published_at'])) : '';
function h($s) { return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }

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
 * Plain-language download metadata derived from the asset filename, so the
 * page can group by OS and say clearly what each file is and which CPU it's
 * for (the raw "x86_64" / two-.deb listing confused people). Returns:
 *   os    - group key: 'Windows' | 'macOS' | 'Linux' | 'Other'
 *   icon  - group emoji
 *   title - what the file is ("AppImage", ".deb package", ...)
 *   fmt   - one-line "what/when" hint
 *   arch  - human CPU label ("Intel / AMD (64-bit)" vs "ARM64")
 *   rank  - sort order within the OS group (recommended first)
 *   rec   - true for the option most people should pick
 */
function dl_meta(string $name): array {
    $n = strtolower($name);
    $arm = (strpos($n, 'arm') !== false || strpos($n, 'aarch64') !== false);
    $cpu = $arm ? 'ARM (64-bit)' : 'Intel / AMD (64-bit)';
    if (str_ends_with($n, '.dmg'))
        return ['os'=>'macOS','icon'=>'🍎','title'=>'macOS disk image','fmt'=>'.dmg — drag to Applications','arch'=>'Apple Silicon & Intel','rank'=>10,'rec'=>true];
    if (str_ends_with($n, '.pkg'))
        return ['os'=>'macOS','icon'=>'🍎','title'=>'macOS installer','fmt'=>'.pkg','arch'=>'Apple Silicon & Intel','rank'=>20,'rec'=>false];
    if (str_ends_with($n, '.exe') || str_ends_with($n, '.msi'))
        return ['os'=>'Windows','icon'=>'🪟','title'=>'Windows installer','fmt'=>'.exe','arch'=>$cpu,'rank'=>10,'rec'=>true];
    if (str_ends_with($n, '.zip') && strpos($n, 'win') !== false)
        return ['os'=>'Windows','icon'=>'🪟','title'=>'Windows portable','fmt'=>'.zip — unzip &amp; run, no install','arch'=>$cpu,'rank'=>10,'rec'=>true];
    if (str_ends_with($n, '.appimage'))
        return ['os'=>'Linux','icon'=>'🐧','title'=>'AppImage','fmt'=>'runs on any distro — no install','arch'=>$cpu,'rank'=>($arm?12:10),'rec'=>!$arm];
    if (str_ends_with($n, '.deb'))
        return ['os'=>'Linux','icon'=>'🐧','title'=>'.deb package','fmt'=>'for Debian / Ubuntu','arch'=>$cpu,'rank'=>($arm?22:20),'rec'=>false];
    if (str_ends_with($n, '.rpm'))
        return ['os'=>'Linux','icon'=>'🐧','title'=>'.rpm package','fmt'=>'for Fedora / RHEL','arch'=>$cpu,'rank'=>30,'rec'=>false];
    if (str_ends_with($n, '.flatpak'))
        return ['os'=>'Linux','icon'=>'📦','title'=>'Flatpak','fmt'=>'all distros','arch'=>'Universal','rank'=>40,'rec'=>false];
    return ['os'=>'Other','icon'=>'📦','title'=>$name,'fmt'=>'','arch'=>'','rank'=>99,'rec'=>false];
}

// Group the release assets by OS so the list reads as "pick your system,
// then your CPU" instead of a flat jumble of two .debs and two AppImages.
// Linux first - it's the focus of this release; Windows last (newest, still
// finding its feet).
$osOrder = ['Linux', 'macOS', 'Windows', 'Other'];
$osHelp  = [
    'Linux'   => 'Not sure? Get the <b>AppImage</b> — it runs on any distribution with no install. Choose <b>.deb</b> on Debian/Ubuntu. Pick <b>Intel / AMD</b> unless you\'re on an ARM machine (Raspberry Pi, ARM server).',
    'macOS'   => 'One image works on both Apple Silicon (M-series) and Intel Macs.',
    'Windows' => 'Portable build — unzip and run, nothing to install. Newest platform, still being polished.',
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
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>dopeIPTV — Desktop IPTV player with EPG, timeshift &amp; recording</title>
<meta name="description" content="<?= h($DESC) ?>">
<link rel="canonical" href="<?= h($SITE) ?>/">
<meta name="theme-color" content="#0f1218">
<meta name="robots" content="index,follow">
<meta name="keywords" content="IPTV player, Xtream Codes, M3U, EPG, XMLTV, timeshift, catch-up TV, IPTV recording, IPTV multiview, watch multiple channels, multi-screen IPTV, Linux IPTV, macOS IPTV, Windows IPTV, dopeIPTV">
<!-- Open Graph -->
<meta property="og:type" content="website">
<meta property="og:site_name" content="dopeIPTV">
<meta property="og:title" content="dopeIPTV — Desktop IPTV player with EPG, timeshift &amp; recording">
<meta property="og:description" content="<?= h($DESC) ?>">
<meta property="og:url" content="<?= h($SITE) ?>/">
<meta property="og:image" content="<?= h($SITE) ?>/og-image.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<!-- Twitter -->
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="dopeIPTV — Desktop IPTV player with EPG, timeshift &amp; recording">
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
      <a class="navlink" href="#features">Features</a>
      <a class="navlink" href="#shots">Screenshots</a>
      <a class="navlink" href="#download">Download</a>
      <a class="navlink" href="<?= h($REPO) ?>">GitHub</a>
      <a class="btn primary" href="#download">Download</a>
    </nav>
  </div>
</header>

<main id="top">
  <section class="hero">
    <div class="wrap hero-grid">
      <div>
        <span class="eyebrow"><span class="dot"></span> On air · version <?= h($version) ?></span>
        <h1>Live TV, catch-up and recordings — in one <span class="hl">native desktop</span> app.</h1>
        <p class="lede">A fast, keyboard-driven IPTV client for Xtream Codes &amp; M3U. Scrub back into a channel's archive, pause live TV, browse the full EPG, record, and watch up to nine channels at once in multiview — with a built-in mpv player. For Linux, macOS &amp; Windows.</p>
        <div class="cta-row">
          <a class="btn primary" id="heroDownload" href="#download">Download for your system</a>
          <a class="btn ghost" href="<?= h($REPO) ?>">View source</a>
        </div>
        <p class="platnote"><b id="osLabel">Linux · macOS · Windows</b> — free &amp; open source</p>
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
              <div class="tl-meta"><span>19:24 · Evening News</span><span class="behind">− 12 min behind live</span></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>

  <div class="strip">
    <div class="wrap">
      <span class="chip">Xtream Codes</span><span class="chip">M3U / M3U8</span>
      <span class="chip">XMLTV EPG</span><span class="chip">Timeshift / catch-up</span>
      <span class="chip">Multiview</span><span class="chip">Chromecast</span><span class="chip">Trakt</span><span class="chip">8 languages</span>
    </div>
  </div>

  <section id="features">
    <div class="wrap">
      <div class="sec-head">
        <span class="eyebrow">Everything on one screen</span>
        <h2>Built for how people actually watch TV.</h2>
        <p>Not a repurposed media library — a TV client, with the timeline, the guide and the recorder where you expect them.</p>
      </div>
      <div class="grid">
        <div class="card"><div class="ic">⏱</div><h3>Timeshift &amp; catch-up</h3><p>Scrub back into a channel's archive on a live timeline, or jump straight to a past programme from the guide.</p></div>
        <div class="card"><div class="ic">⊞</div><h3>Multiview</h3><p>Watch up to nine live channels at once in a grid — mix different playlists, click one for audio, with per-window timeshift and subtitles.</p></div>
        <div class="card"><div class="ic">⏸</div><h3>Pause live TV</h3><p>DVR-style pause and resume behind live — the player shows exactly how far behind you are.</p></div>
        <div class="card"><div class="ic">▦</div><h3>Full EPG guide</h3><p>A real programme grid with search, reminders and a configurable list of what's coming up next.</p></div>
        <div class="card"><div class="ic">⏺</div><h3>One-click recording</h3><p>Record the stream you're watching over a single connection, with timers, size caps and a Recordings library.</p></div>
        <div class="card"><div class="ic">◉</div><h3>Multi-provider</h3><p>Several Xtream or M3U playlists side by side, each with its own EPG, auto-refresh and custom guide URL.</p></div>
        <div class="card"><div class="ic">▶</div><h3>Buttery playback</h3><p>A built-in mpv engine, with Chromecast, Trakt sync, themes and full keyboard control.</p></div>
      </div>
    </div>
  </section>

  <section id="shots" style="padding-top:0;">
    <div class="wrap">
      <div class="sec-head">
        <span class="eyebrow">A look inside</span>
        <h2>Clean, dark, and out of your way.</h2>
      </div>
      <div class="shots">
<?php
// Each row lights up automatically the moment the PNG exists in screenshots/ -
// drop in main.png / epg.png / timeshift.png / recordings.png and they appear,
// no code change. Until then the placeholder is shown.
$shots = [
    ['main.png',       'dopeIPTV main window with the channel list, guide and video', 'Channels &amp; player', 'the list, the guide and the video in one layout.'],
    ['epg.png',        'dopeIPTV EPG programme guide grid',                            'Programme guide',       'grid view with catch-up markers.'],
    ['timeshift.png',  'dopeIPTV timeshift timeline scrubbing a channel archive',      'Timeshift timeline',    'scrub the archive, live edge marked.'],
    ['recordings.png', 'dopeIPTV recordings library with timers',                      'Recordings',            'timers, storage caps and playback.'],
];
foreach ($shots as [$file, $alt, $title, $cap]):
    $exists = is_file(__DIR__ . '/screenshots/' . $file);
?>
        <div class="shot">
<?php if ($exists): ?>
          <img src="/screenshots/<?= h($file) ?>" alt="<?= h($alt) ?>" width="1280" height="800" loading="lazy">
<?php else: ?>
          <div class="ph">screenshot · <?= $title ?></div>
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
          <span class="eyebrow">Get dopeIPTV</span>
          <h2>Download the latest release.</h2>
        </div>
        <span class="release-tag"><b>v<?= h($version) ?></b><?= $relDate ? ' · ' . h($relDate) : ' · latest' ?></span>
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
              <span class="name"><?= h($m['title']) ?><?php if ($m['rec']): ?> <span class="badge">Recommended</span><?php endif; ?></span>
              <span class="sub"><span class="arch"><?= h($m['arch']) ?></span><?= $m['fmt'] ? ' · ' . $m['fmt'] : '' ?><?= $size ? ' · ' . h($size) : '' ?><?php if ($dlN > 0): ?> · <span class="dl-count" title="downloads">↓ <?= number_format($dlN) ?></span><?php endif; ?></span>
            </span>
            <span class="go">Download →</span>
          </a>
<?php endforeach; ?>
        </div>
      </div>
<?php endforeach; else: ?>
      <div class="dls">
        <a class="dl" href="<?= h($REPO) ?>/releases/latest" rel="nofollow">
          <span class="meta"><span class="name">All packages on GitHub</span><span class="sub">latest release</span></span>
          <span class="go">Open →</span>
        </a>
      </div>
<?php endif; ?>
<?php
      $hasMac = false; $hasWindows = false;
      foreach ($assets as $a) {
          $nm = strtolower($a['name'] ?? '');
          if (($a['icon'] ?? '') === '🍎' || str_ends_with($nm, '.dmg') || str_ends_with($nm, '.pkg') || str_contains($nm, 'macos')) { $hasMac = true; }
          if (($a['icon'] ?? '') === '🪟' || str_contains($nm, 'win')) { $hasWindows = true; }
      }
      if ($hasMac): ?>
      <p class="autonote">🍎 On macOS, open the <code>.dmg</code> and drag dopeIPTV to Applications. Because the app isn't notarized by Apple yet, the first launch may be blocked — <b>right-click the app → Open</b>, then <b>Open</b> in the dialog (or allow it under <b>System Settings → Privacy &amp; Security → Open Anyway</b>). If macOS instead says the app is <b>“damaged”</b>, clear the download flag in Terminal: <code>xattr -dr com.apple.quarantine /Applications/dopeIPTV.app</code>. It's safe — the warning only means the build isn't code-signed.</p>
<?php endif; ?>
<?php if ($hasWindows): ?>
      <p class="autonote">🪟 On Windows, unzip the folder and run <code>dopeiptv.exe</code>. Because the app isn't code-signed yet, SmartScreen may show <b>“Windows protected your PC”</b> — click <b>More info → Run anyway</b>. It's only a warning, nothing is blocked or removed.</p>
<?php endif; ?>
      <p class="autonote">↻ Generated on the server from the <code>slimture/dopeIPTV</code> GitHub releases — new builds appear automatically.</p>
<?php if (is_file(__DIR__ . '/files/SHA256SUMS')): ?>
      <p class="autonote">🔒 Verify your download — <a class="verify-link" href="/files/SHA256SUMS">SHA-256 checksums</a> · <code>sha256sum -c SHA256SUMS</code></p>
<?php endif; ?>
    </div>
  </section>

  <section id="credits" style="padding-top:0;">
    <div class="wrap">
      <div class="sec-head">
        <span class="eyebrow">Open source</span>
        <h2>Free software, standing on giants.</h2>
        <p>dopeIPTV is <b>free and open source</b> under the GPL-3.0 licence — no ads, no tracking, no accounts. It's built with, and grateful to, these projects and services:</p>
      </div>
      <div class="credits-grid">
        <div class="credit"><h3>Playback</h3><p><a href="https://mpv.io" rel="noopener">mpv</a> &amp; <a href="https://ffmpeg.org" rel="noopener">FFmpeg</a>, via python-mpv</p></div>
        <div class="credit"><h3>Interface</h3><p>Qt &amp; <a href="https://www.riverbankcomputing.com/software/pyqt/" rel="noopener">PyQt6</a></p></div>
        <div class="credit"><h3>Casting</h3><p><a href="https://github.com/home-assistant-libs/pychromecast" rel="noopener">PyChromecast</a> — Google Cast</p></div>
        <div class="credit"><h3>Metadata &amp; artwork</h3><p><a href="https://www.themoviedb.org" rel="noopener">The Movie Database (TMDB)</a></p></div>
        <div class="credit"><h3>Watched sync</h3><p><a href="https://trakt.tv" rel="noopener">Trakt</a></p></div>
        <div class="credit"><h3>Licences</h3><p><a href="<?= h($REPO) ?>/blob/main/docs/THIRD-PARTY-LICENSES.md" rel="noopener">GPL-3.0 &amp; third-party</a></p></div>
      </div>
      <p class="disclaimer">This product uses the TMDB API but is not endorsed or certified by TMDB. This product uses the Trakt API but is not endorsed or certified by Trakt. All trademarks are the property of their respective owners.</p>
    </div>
  </section>
</main>

<footer>
  <div class="wrap">
    <span class="v">dopeIPTV <?= h($version) ?> · © <?= date('Y') ?></span>
    <nav>
      <a href="<?= h($REPO) ?>">GitHub</a>
      <a href="<?= h($REPO) ?>/releases">Releases</a>
      <a href="<?= h($REPO) ?>#readme">Docs</a>
    </nav>
  </div>
</footer>
<script src="/app.js" defer></script>
</body>
</html>
