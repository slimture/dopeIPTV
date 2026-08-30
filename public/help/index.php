<?php
/**
 * dopeIPTV help - iptv.dope.rs/help/
 *
 * A DIRECTORY with an index.php, not help.php, so the existing nginx
 * `try_files $uri $uri/` plus `index index.php` serves it with no server
 * config change at all - one less thing a deploy can be missing.
 *
 * Every topic is rendered server-side, so the page works with JavaScript off
 * and a search engine sees all of it. The search box filters what is already
 * on the page; it is not a query against anything.
 *
 * The content lives in lang/<code>.php like the rest of the site, which means
 * t()'s per-key fallback applies: a language that has not translated a topic
 * yet shows that topic in English while the rest of its page stays translated.
 * A partial translation degrades topic by topic instead of breaking.
 */
require __DIR__ . '/../i18n.php';

$SITE = 'https://iptv.dope.rs';
$REPO = 'https://github.com/slimture/dopeIPTV';

$rel = @json_decode(@file_get_contents(__DIR__ . '/../releases.json'), true);
$version = $rel['version'] ?? '';

/**
 * The help tree: section id => [topic ids].
 *
 * Text comes from t("help_<section>_<topic>_q") and ..._a. Ordered the way
 * somebody meets the app - install, watch, then the things you go looking
 * for - rather than the way the code is organised.
 */
const HELP = [
    'start' => ['linux', 'macos', 'windows', 'playlist', 'm3u', 'demo'],
    'watch' => ['play', 'seek', 'fullscreen', 'popout', 'tracks', 'keys', 'resume'],
    'epg'   => ['what', 'guide', 'xmltv', 'refresh', 'times'],
    'ts'    => ['what', 'start', 'browse', 'depth'],
    'rec'   => ['start', 'timed', 'scheduled', 'oneconn', 'cap', 'where', 'while'],
    'mv'    => ['open', 'audio', 'options', 'cost'],
    'local' => ['add', 'views', 'art', 'music', 'missing'],
    'cast'  => ['start', 'convert', 'stop'],
    'trakt' => ['connect', 'scrobble'],
    'set'   => ['language', 'theme', 'playback', 'subs', 'parental', 'hide', 'playlists'],
    'fix'   => ['start', 'picture', 'gatekeeper', 'smartscreen', 'defender',
                'stream', 'limit', 'icon', 'external', 'logs'],
];
?><!DOCTYPE html>
<html lang="<?= h(lang_code()) ?>" dir="<?= h(lang_dir()) ?>">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title><?= h(t('help_meta_title')) ?></title>
<meta name="description" content="<?= h(t('help_meta_desc')) ?>">
<link rel="canonical" href="<?= h($SITE) ?>/help/<?= lang_code() === 'en' ? '' : '?lang=' . h(lang_code()) ?>">
<meta name="theme-color" content="#0f1218">
<meta name="robots" content="index,follow">
<meta property="og:type" content="article">
<meta property="og:site_name" content="dopeIPTV">
<meta property="og:locale" content="<?= h(lang_locale()) ?>">
<meta property="og:title" content="<?= h(t('help_meta_title')) ?>">
<meta property="og:description" content="<?= h(t('help_meta_desc')) ?>">
<meta property="og:url" content="<?= h($SITE) ?>/help/">
<meta property="og:image" content="<?= h($SITE) ?>/og-image.png">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="icon" href="/favicon.png" sizes="any">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="stylesheet" href="/style.css">
</head>
<body>
<header class="bar">
  <div class="wrap">
    <a class="brand" href="/"><span class="glyph">◉</span><b>dopeIPTV</b></a>
    <nav class="links">
      <a class="navlink" href="/#features"><?= h(t('nav_features')) ?></a>
      <a class="navlink" href="/#download"><?= h(t('nav_download')) ?></a>
      <a class="navlink" href="/help/" aria-current="page"><?= h(t('nav_help')) ?></a>
      <a class="navlink" href="<?= h($REPO) ?>"><?= h(t('nav_github')) ?></a>
    </nav>
  </div>
</header>

<main id="top">
  <section class="pt0">
    <div class="wrap">
      <div class="sec-head">
        <span class="eyebrow"><?= h(t('help_eyebrow')) ?></span>
        <h1><?= h(t('help_h1')) ?></h1>
        <p><?= h(t('help_intro')) ?></p>
      </div>

      <div class="help-search">
        <input type="search" id="helpSearch" autocomplete="off"
               placeholder="<?= h(t('help_search_ph')) ?>"
               aria-label="<?= h(t('help_search_ph')) ?>">
        <p class="help-count" id="helpCount" hidden></p>
      </div>

      <div class="help-layout">
        <nav class="help-toc" aria-label="<?= h(t('help_toc')) ?>">
<?php foreach (HELP as $sec => $topics): ?>
          <a href="#<?= h($sec) ?>" data-toc="<?= h($sec) ?>"><?= h(t("help_{$sec}_title")) ?></a>
<?php endforeach; ?>
        </nav>

        <div class="help-body">
<?php foreach (HELP as $sec => $topics): ?>
          <section class="help-sec" id="<?= h($sec) ?>" data-sec="<?= h($sec) ?>">
            <h2><?= h(t("help_{$sec}_title")) ?></h2>
<?php foreach ($topics as $tp):
            $q = t("help_{$sec}_{$tp}_q");
            $a = t("help_{$sec}_{$tp}_a");
            if ($q === "help_{$sec}_{$tp}_q") { continue; }   // not written yet
?>
            <details class="faq-item help-item" id="<?= h("$sec-$tp") ?>">
              <summary><?= h($q) ?></summary>
              <p><?= $a ?></p>
            </details>
<?php endforeach; ?>
          </section>
<?php endforeach; ?>
          <p class="help-none" id="helpNone" hidden><?= h(t('help_no_hits')) ?></p>
        </div>
      </div>

      <p class="autonote"><?= t('help_more') ?></p>
    </div>
  </section>
</main>

<footer>
  <div class="wrap">
    <span class="v">dopeIPTV<?= $version ? ' ' . h($version) : '' ?> · © <?= date('Y') ?></span>
    <nav>
      <a href="/"><?= h(t('nav_download')) ?></a>
      <a href="<?= h($REPO) ?>">GitHub</a>
      <a href="<?= h($REPO) ?>/releases"><?= h(t('footer_releases')) ?></a>
    </nav>
  </div>
</footer>

<script>
// Filters what is already on the page. With this script absent every topic
// is still there, closed, and reachable by its anchor - which is also what a
// search engine and a reader with JavaScript off get.
(function () {
  var box = document.getElementById('helpSearch');
  var count = document.getElementById('helpCount');
  var none = document.getElementById('helpNone');
  if (!box) return;
  var items = [].slice.call(document.querySelectorAll('.help-item'));
  var secs = [].slice.call(document.querySelectorAll('.help-sec'));
  var tocs = [].slice.call(document.querySelectorAll('[data-toc]'));

  items.forEach(function (el) {
    el.dataset.text = (el.textContent || '').toLowerCase();
  });

  function apply(q) {
    q = q.trim().toLowerCase();
    var hits = 0;
    items.forEach(function (el) {
      var on = !q || el.dataset.text.indexOf(q) !== -1;
      el.hidden = !on;
      // Opened while searching so the answer is visible without a second
      // click; closed again when the box is cleared.
      if (q) { el.open = on; } else { el.open = false; }
      if (on) hits++;
    });
    secs.forEach(function (s) {
      var any = s.querySelector('.help-item:not([hidden])');
      s.hidden = !any;
    });
    tocs.forEach(function (a) {
      var s = document.getElementById(a.dataset.toc);
      a.hidden = !!(s && s.hidden);
    });
    if (q) {
      count.hidden = false;
      count.textContent = hits + ' / ' + items.length;
    } else {
      count.hidden = true;
    }
    none.hidden = !(q && hits === 0);
  }

  box.addEventListener('input', function () { apply(box.value); });
  // A deep link opens the topic it names.
  function openHash() {
    if (!location.hash) return;
    var el = document.querySelector(location.hash);
    if (el && el.tagName === 'DETAILS') { el.open = true; }
  }
  window.addEventListener('hashchange', openHash);
  openHash();
})();
</script>
</body>
</html>
