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

// Only these end-user installer types are listed/mirrored; dev artifacts like
// the .whl and the source .tar.gz are skipped.
const USER_EXTS = ['.dmg', '.appimage', '.deb', '.rpm', '.flatpak', '.pkg', '.exe', '.msi', '.zip'];

function auth_headers(string $accept): array {
    $h = ["User-Agent: " . UA, "Accept: $accept"];
    $t = getenv('GITHUB_TOKEN');
    if ($t) { $h[] = "Authorization: Bearer $t"; }
    return $h;
}

/** GET a (small) text/JSON body with a User-Agent. Returns body or null. */
function http_get(string $url): ?string {
    $headers = auth_headers('application/vnd.github+json');
    if (function_exists('curl_init')) {
        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER => true, CURLOPT_FOLLOWLOCATION => true,
            CURLOPT_HTTPHEADER => $headers, CURLOPT_TIMEOUT => 30,
            CURLOPT_FAILONERROR => true,
        ]);
        $body = curl_exec($ch);
        $ok = ($body !== false && curl_getinfo($ch, CURLINFO_HTTP_CODE) < 400);
        curl_close($ch);
        return $ok ? $body : null;
    }
    $ctx = stream_context_create(['http' => [
        'method' => 'GET', 'header' => implode("\r\n", $headers), 'timeout' => 30]]);
    $body = @file_get_contents($url, false, $ctx);
    return $body === false ? null : $body;
}

/** Stream a (possibly large) asset straight to disk - never into memory, so a
 *  160 MB AppImage doesn't blow PHP's memory_limit. Returns true on success. */
function download_to(string $url, string $dest): bool {
    $headers = auth_headers('application/octet-stream');
    $fp = fopen($dest, 'wb');
    if (!$fp) { return false; }
    if (function_exists('curl_init')) {
        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_FILE => $fp, CURLOPT_FOLLOWLOCATION => true,
            CURLOPT_HTTPHEADER => $headers, CURLOPT_TIMEOUT => 1800,
            CURLOPT_FAILONERROR => true,
        ]);
        $ok = (curl_exec($ch) !== false && curl_getinfo($ch, CURLINFO_HTTP_CODE) < 400);
        curl_close($ch);
        fclose($fp);
        return $ok;
    }
    fclose($fp);
    $ctx = stream_context_create(['http' => [
        'header' => implode("\r\n", $headers), 'timeout' => 1800]]);
    $in = @fopen($url, 'rb', false, $ctx);
    if (!$in) { return false; }
    $out = fopen($dest, 'wb');
    $ok = (stream_copy_to_stream($in, $out) !== false);
    fclose($in); fclose($out);
    return $ok;
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
    if (str_ends_with($n, '.pkg'))       return ['macOS · .pkg', 'installer', '🍎'];
    if (str_ends_with($n, '.exe') || str_ends_with($n, '.msi')) return ['Windows', 'installer', '🪟'];
    if (str_ends_with($n, '.zip')) {
        return (strpos($n, 'win') !== false)
            ? ['Windows · portable', 'x64 · unzip &amp; run', '🪟']
            : ['Archive', 'zip', '📦'];
    }
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

// --- Completeness gate ----------------------------------------------------
// A finished release always carries all three desktop platforms (Linux is the
// primary target; macOS and Windows are a bonus). GitHub attaches assets one
// CI job at a time, so a cron tick that lands mid-build sees only the fast
// jobs (macOS ~2 min, Windows ~2 min) and NOT the slower Linux ones (~5 min).
// Publishing that partial set would replace a complete mirror with a
// macOS+Windows-only list and prune the Linux downloads. Guard against it:
// only assets GitHub reports as fully 'uploaded' count, and if any platform
// group is still missing we keep the existing releases.json untouched and
// retry on the next tick. (If this project ever intentionally drops a
// platform, relax the required set below.)
$hasLinux = $hasMac = $hasWin = false;
foreach ($rel['assets'] ?? [] as $a) {
    if (($a['state'] ?? 'uploaded') !== 'uploaded') { continue; }
    $n = strtolower(basename($a['name'] ?? ''));
    if (str_ends_with($n, '.appimage') || str_ends_with($n, '.deb')
        || str_ends_with($n, '.rpm') || str_ends_with($n, '.flatpak')) { $hasLinux = true; }
    if (str_ends_with($n, '.dmg') || str_ends_with($n, '.pkg')) { $hasMac = true; }
    if (str_ends_with($n, '.exe') || str_ends_with($n, '.msi')
        || (str_ends_with($n, '.zip') && strpos($n, 'win') !== false)) { $hasWin = true; }
}
if (!$hasLinux || !$hasMac || !$hasWin) {
    $miss = [];
    if (!$hasLinux) { $miss[] = 'Linux'; }
    if (!$hasMac)   { $miss[] = 'macOS'; }
    if (!$hasWin)   { $miss[] = 'Windows'; }
    fwrite(STDERR, "[sync] v$version incomplete (missing " . implode('+', $miss)
        . "); still building - keeping existing releases.json, retry next run\n");
    exit(0);
}

$assets  = [];
$keep    = [];   // filenames we want to retain in files/

foreach (($rel['assets'] ?? []) as $a) {
    // Never mirror an asset GitHub is still uploading.
    if (($a['state'] ?? 'uploaded') !== 'uploaded') { continue; }
    $name = basename($a['name']);
    // Skip dev/source artifacts (.whl, .tar.gz, checksums, …) - end users only
    // want installers.
    $ln = strtolower($name);
    $isUser = false;
    foreach (USER_EXTS as $ext) { if (str_ends_with($ln, $ext)) { $isUser = true; break; } }
    if (!$isUser) { continue; }

    $keep[$name] = true;
    $dest = FILES_DIR . '/' . $name;
    // Download only when missing or size changed (GitHub assets are immutable per release).
    if (!is_file($dest) || filesize($dest) !== (int)($a['size'] ?? -1)) {
        fwrite(STDERR, "[sync] fetching $name (" . human_size((int)$a['size']) . ") ... ");
        if (!download_to($a['browser_download_url'], $dest . '.part')) {
            @unlink($dest . '.part');
            fwrite(STDERR, "FAILED\n");
            unset($keep[$name]);
            continue;
        }
        rename($dest . '.part', $dest);   // atomic swap
        fwrite(STDERR, "done\n");
    } else {
        fwrite(STDERR, "[sync] $name already current\n");
    }
    // SHA-256 for download verification: prefer GitHub's own digest (free, no
    // re-hashing), fall back to hashing the mirrored file (streamed, not loaded
    // into memory) for older releases that don't carry a digest.
    $sha = '';
    if (!empty($a['digest']) && strncmp($a['digest'], 'sha256:', 7) === 0) {
        $sha = substr($a['digest'], 7);
    } elseif (is_file($dest)) {
        $sha = hash_file('sha256', $dest) ?: '';
    }

    [$label, $sub, $icon] = classify($name);
    $assets[] = [
        'label'  => $label,
        'sub'    => $sub ? ($sub . ' · ' . human_size((int)$a['size'])) : human_size((int)$a['size']),
        'icon'   => $icon,
        'url'    => '/files/' . rawurlencode($name),
        'name'   => $name,
        'sha256' => $sha,
    ];
}

// A standard SHA256SUMS file so users can run `sha256sum -c SHA256SUMS`.
$sumsName = 'SHA256SUMS';
$sums = '';
foreach ($assets as $x) { if ($x['sha256']) { $sums .= $x['sha256'] . '  ' . $x['name'] . "\n"; } }
if ($sums !== '') {
    file_put_contents(FILES_DIR . '/' . $sumsName . '.part', $sums);
    rename(FILES_DIR . '/' . $sumsName . '.part', FILES_DIR . '/' . $sumsName);
    $keep[$sumsName] = true;
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
