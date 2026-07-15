<?php
/**
 * dl.php - download redirector + per-file counter.
 *
 * Every download link on the page points here (/dl.php?f=NAME) instead of
 * straight at /files/NAME, so we can tally how many times each release file is
 * fetched. We bump a small JSON counter and then 302-redirect to the real
 * /files/NAME, which nginx serves efficiently with the right attachment
 * headers. The tally is a vanity metric, not access control - a crafted direct
 * /files/ URL still works and just isn't counted.
 *
 * The counts live in downloads.json ONE LEVEL ABOVE the web root (next to
 * sync-releases.php), so it is never web-served and never pruned by the release
 * sync. PHP-FPM's user must be able to write it, e.g.:
 *     touch /usr/local/www/iptv/downloads.json
 *     chown www /usr/local/www/iptv/downloads.json
 */
declare(strict_types=1);

// basename() strips any path, defeating ../ traversal and absolute paths.
$name = basename((string)($_GET['f'] ?? ''));
$path = __DIR__ . '/files/' . $name;

// Whitelist by existence: only redirect to a real file actually in /files.
if ($name === '' || $name[0] === '.' || !is_file($path)) {
    http_response_code(404);
    header('Content-Type: text/plain; charset=utf-8');
    echo 'Not found';
    exit;
}

// Bump the counter under an exclusive lock so concurrent downloads don't lose
// increments. Any failure here is non-fatal - the download must still proceed.
$countsFile = dirname(__DIR__) . '/downloads.json';
$fp = @fopen($countsFile, 'c+');
if ($fp !== false) {
    if (flock($fp, LOCK_EX)) {
        $raw  = stream_get_contents($fp);
        $data = json_decode($raw !== false && $raw !== '' ? $raw : '{}', true);
        if (!is_array($data)) { $data = []; }
        $data[$name] = (int)($data[$name] ?? 0) + 1;
        rewind($fp);
        ftruncate($fp, 0);
        fwrite($fp, json_encode(
            $data, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES));
        fflush($fp);
        flock($fp, LOCK_UN);
    }
    fclose($fp);
}

// Hand the actual transfer to nginx (/files/ sets octet-stream + attachment).
header('Location: /files/' . rawurlencode($name), true, 302);
