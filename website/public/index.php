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
        . '& M3U with a full EPG guide, live timeshift, catch-up TV and recording. '
        . 'For Linux and macOS.';

$rel = @json_decode(@file_get_contents(__DIR__ . '/releases.json'), true);
$version = $rel['version'] ?? '0.7.0';
$assets  = $rel['assets'] ?? [];
$relDate = !empty($rel['published_at']) ? date('M j, Y', strtotime($rel['published_at'])) : '';
function h($s) { return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
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
<meta name="keywords" content="IPTV player, Xtream Codes, M3U, EPG, XMLTV, timeshift, catch-up TV, IPTV recording, Linux IPTV, macOS IPTV, dopeIPTV">
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
  "operatingSystem": "Linux, macOS",
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
        <p class="lede">A fast, keyboard-driven IPTV client for Xtream Codes &amp; M3U. Scrub back into a channel's archive, pause live TV, browse the full EPG, and record — with a built-in mpv player. For Linux &amp; macOS.</p>
        <div class="cta-row">
          <a class="btn primary" id="heroDownload" href="#download">Download for your system</a>
          <a class="btn ghost" href="<?= h($REPO) ?>">View source</a>
        </div>
        <p class="platnote"><b id="osLabel">Linux · macOS</b> — free &amp; open source</p>
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
      <span class="chip">Chromecast</span><span class="chip">Trakt</span><span class="chip">8 languages</span>
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
      <div class="dls">
<?php if ($assets): foreach ($assets as $a): ?>
        <a class="dl" href="<?= h($a['url']) ?>" rel="nofollow"<?= !empty($a['sha256']) ? ' title="SHA-256: ' . h($a['sha256']) . '"' : '' ?>>
          <span class="os"><?= h($a['icon'] ?? '📦') ?></span>
          <span class="meta"><span class="name"><?= h($a['label']) ?></span><span class="sub"><?= h($a['sub'] ?? '') ?></span></span>
          <span class="go">Download →</span>
        </a>
<?php endforeach; else: ?>
        <a class="dl" href="<?= h($REPO) ?>/releases/latest" rel="nofollow">
          <span class="os">📦</span>
          <span class="meta"><span class="name">All packages on GitHub</span><span class="sub">latest release</span></span>
          <span class="go">Open →</span>
        </a>
<?php endif; ?>
      </div>
      <p class="autonote">↻ Generated on the server from the <code>slimture/dopeIPTV</code> GitHub releases — new builds appear automatically.</p>
<?php if (is_file(__DIR__ . '/files/SHA256SUMS')): ?>
      <p class="autonote">🔒 Verify your download: <a class="verify-link" href="/files/SHA256SUMS">SHA-256 checksums</a> — <code>sha256sum -c SHA256SUMS</code> (hover a button to see its hash).</p>
<?php endif; ?>
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
