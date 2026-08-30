<?php
/**
 * The site header, shared by every page.
 *
 * It lives here because it had already drifted: the home page carried a
 * language picker and the help page did not, and BOTH hid every link below
 * 720px with nothing put in their place - so on a phone the header was the
 * logo and nothing else, and /help/ could only be reached by typing it.
 *
 * The link list is written once and rendered twice: inline for a wide
 * window, and inside a disclosure for a narrow one. Two copies of the
 * markup, one copy of the truth.
 *
 * No JavaScript is required. <details> is a native disclosure, the items are
 * plain links, and nav.js only adds close-on-outside-click - so the menu
 * still opens with scripting blocked, which matters on a site whose CSP has
 * already silently eaten one inline <script>.
 *
 * The including page sets $nav_current to 'home' or 'help' beforehand.
 */
$nav_current = $nav_current ?? '';
$nav_repo = 'https://github.com/slimture/dopeIPTV';
// On the home page the section links stay in-page anchors; anywhere else
// they have to travel there first.
$nav_at_home = $nav_current === 'home';
$nav_base = $nav_at_home ? '' : '/';
// Where "switch language" should land: the page you are on, not always the
// front page. Derived from what the page told us, never from REQUEST_URI,
// so there is no user input anywhere near a URL we emit.
$nav_self = $nav_current === 'help' ? '/help/' : '/';

$nav_links = [
    [$nav_base . '#features', t('nav_features'),    ''],
    [$nav_base . '#shots',    t('nav_screenshots'), ''],
    [$nav_base . '#download', t('nav_download'),    ''],
    ['/help/',                t('nav_help'),        'help'],
    [$nav_repo,               t('nav_github'),      ''],
];
$nav_avail = i18n_available();
?>
<header class="bar">
  <div class="wrap">
    <a class="brand" href="<?= $nav_at_home ? '#top' : '/' ?>"><span class="glyph">◉</span><b>dopeIPTV</b></a>
    <nav class="links">
<?php foreach ($nav_links as [$href, $label, $key]): ?>
      <a class="navlink" href="<?= h($href) ?>"<?= $key !== '' && $key === $nav_current ? ' aria-current="page"' : '' ?>><?= h($label) ?></a>
<?php endforeach; ?>
<?php if (count($nav_avail) > 1): ?>
      <details class="langpick" id="langPick" name="hdrmenu">
        <summary aria-label="<?= h(t('lang_label')) ?>">
          <span class="langpick-cur"><span class="langpick-globe">🌐</span><span class="langpick-name"><?= h(I18N_NAMES[lang_code()] ?? lang_code()) ?></span></span>
          <span class="langpick-caret" aria-hidden="true">▾</span>
        </summary>
        <div class="langpick-menu" role="menu">
<?php foreach ($nav_avail as $code): /* always ?lang= — even English, so it overrides a cookie set to another language (otherwise you can't switch back) */
          $href = $nav_self . '?lang=' . rawurlencode($code); ?>
          <a role="menuitem" rel="nofollow" class="langpick-item<?= $code === lang_code() ? ' is-current' : '' ?>" href="<?= h($href) ?>"<?= $code === lang_code() ? ' aria-current="true"' : '' ?>><?= h(I18N_NAMES[$code] ?? $code) ?></a>
<?php endforeach; ?>
        </div>
      </details>
<?php endif; ?>
      <!-- The narrow-window menu. `name` makes the two disclosures an
           exclusive pair in browsers that support it, so opening one shuts
           the other with no script at all. -->
      <details class="navmenu" id="navMenu" name="hdrmenu">
        <summary aria-label="<?= h(t('nav_menu')) ?>">
          <span class="navmenu-bars" aria-hidden="true"><i></i><i></i><i></i></span>
        </summary>
        <div class="navmenu-panel" role="menu">
<?php foreach ($nav_links as [$href, $label, $key]): ?>
          <a role="menuitem" class="navmenu-item<?= $key !== '' && $key === $nav_current ? ' is-current' : '' ?>" href="<?= h($href) ?>"<?= $key !== '' && $key === $nav_current ? ' aria-current="page"' : '' ?>><?= h($label) ?></a>
<?php endforeach; ?>
        </div>
      </details>
      <a class="btn primary" href="<?= $nav_base ?>#download"><?= h(t('nav_download_btn')) ?></a>
    </nav>
  </div>
</header>
