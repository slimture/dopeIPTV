<?php
/**
 * The sitemap, generated: every page in every language, each listing its
 * translations (hreflang) so a search engine files them as one page in 27
 * languages rather than 27 near-copies.
 *
 * /sitemap.xml, the address robots.txt and Search Console know, is a sitemap
 * index pointing here - the static file it used to be named one URL with a
 * July date, and nothing reminded anyone to update it.
 *
 * lastmod is only stated when it is true: the home page changes with each
 * release (the version, the downloads, the notes), the help page with its
 * text.
 */
require __DIR__ . '/i18n.php';

$SITE = 'https://iptv.dope.rs';

$rel = @json_decode(@file_get_contents(__DIR__ . '/releases.json'), true);
$home = !empty($rel['published_at']) ? strtotime($rel['published_at']) : false;
$helpFiles = array_merge([__DIR__ . '/help/index.php'],
                         glob(__DIR__ . '/lang/*.php') ?: []);
$help = max(array_map(static fn($f) => (int)@filemtime($f), $helpFiles));

$pages = ['/' => $home, '/help/' => $help ?: false];
$langs = i18n_available();

header('Content-Type: application/xml; charset=utf-8');
echo '<?xml version="1.0" encoding="UTF-8"?>', "\n";
echo '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"',
     ' xmlns:xhtml="http://www.w3.org/1999/xhtml">', "\n";
foreach ($pages as $path => $ts) {
    foreach ($langs as $code) {
        echo "  <url>\n";
        echo '    <loc>', h(i18n_url($SITE, $path, $code)), "</loc>\n";
        if ($ts) {
            echo '    <lastmod>', gmdate('Y-m-d', $ts), "</lastmod>\n";
        }
        foreach ($langs as $alt) {
            echo '    <xhtml:link rel="alternate" hreflang="', h($alt),
                 '" href="', h(i18n_url($SITE, $path, $alt)), "\"/>\n";
        }
        echo '    <xhtml:link rel="alternate" hreflang="x-default" href="',
             h($SITE . $path), "\"/>\n";
        echo "  </url>\n";
    }
}
echo "</urlset>\n";
