<?php
/**
 * The page for an address that does not exist. nginx's own 404 is a bare
 * "404 Not Found" with no way back; this one has the site's header, says
 * what happened in the visitor's language, and offers the two pages there
 * are. It still answers 404, so nothing indexes it as a page.
 *
 * Needs `error_page 404 /404.php;` in the content server's nginx config
 * (deploy/content-iptv.dope.rs.conf); without that line nginx keeps its own.
 */
require __DIR__ . '/i18n.php';
http_response_code(404);
?><!DOCTYPE html>
<html lang="<?= h(lang_code()) ?>" dir="<?= h(lang_dir()) ?>">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>404 · dopeIPTV</title>
<meta name="robots" content="noindex">
<meta name="theme-color" content="#0f1218">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="icon" href="/favicon.ico" sizes="32x32">
<link rel="stylesheet" href="<?= h(asset('/style.css')) ?>">
</head>
<body>
<?php $nav_current = ''; require __DIR__ . '/nav.php'; ?>
<main id="top">
  <section class="hero">
    <div class="wrap">
      <span class="eyebrow"><span class="dot"></span> 404</span>
      <h1><span class="h1-brand">dopeIPTV</span> <?= h(t('nf_h1')) ?></h1>
      <p class="lede"><?= h(t('nf_lede')) ?></p>
      <div class="cta-row">
        <a class="btn primary" href="/"><?= h(t('nf_home')) ?></a>
        <a class="btn ghost" href="/help/"><?= h(t('nav_help')) ?></a>
      </div>
    </div>
  </section>
</main>
<script src="<?= h(asset('/nav.js')) ?>" defer></script>
</body>
</html>
