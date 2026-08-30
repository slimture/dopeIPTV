<?php
/**
 * dopeIPTV help - iptv.dope.rs/help/
 *
 * A DIRECTORY with an index.php, not help.php, so the existing nginx
 * `try_files $uri $uri/` plus `index index.php` serves it with no server
 * config change at all - one less thing a deploy can be missing.
 *
 * Every topic is rendered server-side with its answer VISIBLE - articles,
 * not an accordion. That is what makes the search useful: a match reads
 * immediately instead of asking for a second click. It also means the page
 * is complete with JavaScript off and a search engine indexes all of it.
 *
 * The search lives in help.js, an external file, because the site's CSP is
 * `script-src 'self'` with no 'unsafe-inline'.
 *
 * Content comes from lang/<code>.php like the rest of the site, so t()'s
 * per-key fallback applies: a language that has not translated a topic yet
 * shows that one topic in English inside an otherwise translated page.
 */
require __DIR__ . '/../i18n.php';

$SITE = 'https://iptv.dope.rs';
$REPO = 'https://github.com/slimture/dopeIPTV';

$rel = @json_decode(@file_get_contents(__DIR__ . '/../releases.json'), true);
$version = $rel['version'] ?? '';

/**
 * section id => [topic ids], in the order somebody meets the app.
 *
 * Seven sections, not eleven: the guide, timeshift and recording are one
 * subject to a reader even though they are three to the code, and a page of
 * many small headings with two sentences under each reads as fragments
 * rather than as documentation.
 */
const HELP = [
    'start' => ['linux', 'macos', 'windows', 'playlist', 'm3u', 'demo'],
    'watch' => ['play', 'seek', 'fullscreen', 'popout', 'tracks', 'keys', 'resume'],
    'tv'    => ['epg', 'guide', 'xmltv', 'times', 'ts', 'tsstart', 'tsbrowse',
                'rec', 'recwhen', 'reconn', 'reccap', 'recwhere'],
    'more'  => ['mv', 'mvaudio', 'mvcost', 'cast', 'castfix', 'trakt'],
    'local' => ['add', 'views', 'art', 'music', 'missing'],
    'set'   => ['language', 'playback', 'parental', 'hide', 'playlists'],
    'fix'   => ['logs', 'start', 'picture', 'gatekeeper', 'smartscreen',
                'defender', 'stream', 'limit', 'icon', 'external'],
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
  <section class="help-top">
    <div class="wrap">
      <h1><?= h(t('help_h1')) ?></h1>
      <p class="help-lede"><?= h(t('help_intro')) ?></p>

      <div class="help-searchbar">
        <span class="help-mag" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"
          stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle
          cx="11" cy="11" r="7"/><path d="M16.5 16.5 21 21"/></svg></span>
        <input type="search" id="helpSearch" autocomplete="off" autofocus
               placeholder="<?= h(t('help_search_ph')) ?>"
               aria-label="<?= h(t('help_search_ph')) ?>">
        <button type="button" id="helpClear" hidden
                aria-label="<?= h(t('help_clear')) ?>">✕</button>
      </div>
      <p class="help-count" id="helpCount" hidden
         data-fmt="<?= h(t('help_hits')) ?>"></p>
    </div>
  </section>

  <section class="pt0">
    <div class="wrap">
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
            <article class="help-item" id="<?= h("$sec-$tp") ?>">
              <h3><a href="#<?= h("$sec-$tp") ?>"><?= h($q) ?></a></h3>
              <div class="help-a"><?= $a ?></div>
            </article>
<?php endforeach; ?>
          </section>
<?php endforeach; ?>
          <p class="help-none" id="helpNone" hidden><?= h(t('help_no_hits')) ?></p>
        </div>
      </div>

      <p class="autonote help-foot"><?= t('help_more') ?></p>
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
<script src="/help.js" defer></script>
</body>
</html>
