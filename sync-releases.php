<?php
/**
 * sync-releases.php - mirror the latest GitHub release of slimture/dopeIPTV
 * into public/files/ and write public/releases.json for index.php to render.
 *
 * Run from cron on the content server, e.g. every 15 minutes:
 *   0,15,30,45 * * * * /usr/local/bin/php /usr/local/www/iptv/sync-releases.php >> /var/log/iptv-sync.log 2>&1
 *
 * No client-side GitHub calls: the page only ever reads the local JSON and the
 * mirrored files, so it stays inside a strict same-origin CSP and keeps working
 * even if GitHub is unreachable.
 *
 * Optional: set GITHUB_TOKEN in the environment to raise the API rate limit.
 */

const REPO      = 'slimture/dopeIPTV';
const PUBLIC_DIR = __DIR__ . '/public';
const FILES_DIR  = PUBLIC_DIR . '/files';
const JSON_PATH  = PUBLIC_DIR . '/releases.json';
const UA         = 'dopeiptv-site-sync';

@mkdir(FILES_DIR, 0755, true);

/** Minimal HTTP GET with a User-Agent (GitHub requires one). Returns body or null. */
function http_get(string $url, bool $binary = false): ?string {
    $token = getenv('GITHUB_TOKEN');
    $headers = ["User-Agent: " . UA, "Accept: application/vnd.github+json"];
    if ($token) { $headers[] = "Authorization: Bearer $token"; }
    if (function_exists('curl_init')) {
        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_FOLLOWLOCATION => true,
            CURLOPT_HTTPHEADER     => $headers,
            CURLOPT_TIMEOUT        => $binary ? 600 : 30,
            CURLOPT_FAILONERROR    => true,
        ]);
        $body = curl_exec($ch);
        $ok = ($body !== false && curl_getinfo($ch, CURLINFO_HTTP_CODE) < 400);
        curl_close($ch);
        return $ok ? $body : null;
    }
    $ctx = stream_context_create(['http' => [
        'method' => 'GET', 'header' => implode("\r\n", $headers), 'timeout' => $binary ? 600 : 30,
    ]]);
    $body = @file_get_contents($url, false, $ctx);
    return $body === false ? null : $body;
}

/** Classify an asset filename into a friendly label, sub-line and icon. */
function classify(string $name): array {
    $n = strtolower($name);
    $arm = (strpos($n, 'arm') !== false || strpos($n, 'aarch64') !== false);
    if (str_ends_with($n, '.dmg'))       return ['macOS · .dmg', 'Apple Silicon & Intel', '🍎'];
    if (str_ends_with($n, '.appimage'))  return ['Linux · AppImage', $arm ? 'ARM64 · portable' : 'x86_64 · portable', '🐧'];
    if (str_ends_with($n, '.deb'))       return ['Linux · .deb', 'Debian / Ubuntu' . ($arm ? ' (ARM64)' : ''), '🐧'];
    if (str_ends_with($n, '.rpm'))       return ['Linux · .rpm', 'Fedora / RHEL', '🐧'];
    if (str_ends_with($n, '.flatpak'))   return ['Flatpak', 'Flathub · all distros', '📦'];
    if (str_ends_with($n, '.tar.gz') || str_ends_with($n, '.tar.xz')) return ['Linux · archive', $arm ? 'ARM64' : 'x86_64', '🐧'];
    if (str_ends_with($n, '.exe') || str_ends_with($n, '.msi')) return ['Windows', 'installer', '🪟'];
    if (str_ends_with($n, '.zip'))       return ['Archive', 'zip', '📦'];
    return [$name, '', '📦'];
}

function human_size(int $bytes): string {
    $u = ['B','KB','MB','GB']; $i = 0;
    while ($bytes >= 1024 && $i < count($u) - 1) { $bytes /= 1024; $i++; }
    return round($bytes, 1) . ' ' . $u[$i];
}

$raw = http_get("https://api.github.com/repos/" . REPO . "/releases/latest");
if ($raw === null) { fwrite(STDERR, "[sync] GitHub API unreachable; keeping existing releases.json\n"); exit(1); }
$rel = json_decode($raw, true);
if (!is_array($rel) || empty($rel['tag_name'])) { fwrite(STDERR, "[sync] unexpected API payload\n"); exit(1); }

$version = ltrim($rel['tag_name'], 'v');
$assets  = [];
$keep    = [];   // filenames we want to retain in files/

foreach (($rel['assets'] ?? []) as $a) {
    $name = basename($a['name']);
    $keep[$name] = true;
    $dest = FILES_DIR . '/' . $name;
    // Download only when missing or size changed (GitHub assets are immutable per release).
    if (!is_file($dest) || filesize($dest) !== (int)($a['size'] ?? -1)) {
        $bin = http_get($a['browser_download_url'], true);
        if ($bin === null) { fwrite(STDERR, "[sync] failed to fetch $name; skipping\n"); continue; }
        file_put_contents($dest . '.part', $bin);
        rename($dest . '.part', $dest);   // atomic swap
        fwrite(STDERR, "[sync] mirrored $name (" . human_size(strlen($bin)) . ")\n");
    }
    [$label, $sub, $icon] = classify($name);
    $assets[] = [
        'label' => $label,
        'sub'   => $sub ? ($sub . ' · ' . human_size((int)$a['size'])) : human_size((int)$a['size']),
        'icon'  => $icon,
        'url'   => '/files/' . rawurlencode($name),
        'name'  => $name,
    ];
}

// Prune files that are no longer part of the latest release.
foreach (glob(FILES_DIR . '/*') as $f) {
    $b = basename($f);
    if ($b !== '.gitkeep' && empty($keep[$b])) { @unlink($f); fwrite(STDERR, "[sync] pruned stale $b\n"); }
}

$out = [
    'version'      => $version,
    'published_at' => $rel['published_at'] ?? null,
    'html_url'     => $rel['html_url'] ?? ('https://github.com/' . REPO . '/releases'),
    'assets'       => $assets,
    'synced_at'    => gmdate('c'),
];
file_put_contents(JSON_PATH . '.part', json_encode($out, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES));
rename(JSON_PATH . '.part', JSON_PATH);
fwrite(STDERR, "[sync] wrote releases.json for v$version with " . count($assets) . " asset(s)\n");
