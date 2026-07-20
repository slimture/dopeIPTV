<?php
/**
 * Lightweight i18n for the landing page. Zero dependencies, CSP-safe:
 * translations are local PHP arrays (lang/<code>.php), the language is chosen
 * from ?lang=, a cookie, or the browser's Accept-Language, and any key a
 * locale omits falls back to English. A language only advertises itself
 * (hreflang, switcher) once its lang file exists - so we never point a search
 * engine at a page that is really still English.
 */

const I18N_DEFAULT = 'en';

// Native names, shown in the switcher. Display order: English first, then the
// rest alphabetically by their English language name (matches the desktop app).
const I18N_NAMES = [
    'en' => 'English',
    'ar' => 'العربية',           // Arabic
    'zh' => '中文',               // Chinese
    'hr' => 'Hrvatski',          // Croatian
    'nl' => 'Nederlands',        // Dutch
    'fr' => 'Français',          // French
    'de' => 'Deutsch',           // German
    'el' => 'Ελληνικά',          // Greek
    'he' => 'עברית',             // Hebrew
    'hi' => 'हिन्दी',              // Hindi
    'id' => 'Bahasa Indonesia',  // Indonesian
    'it' => 'Italiano',          // Italian
    'ja' => '日本語',             // Japanese
    'ko' => '한국어',             // Korean
    'fa' => 'فارسی',             // Persian
    'pl' => 'Polski',            // Polish
    'pt' => 'Português (BR)',    // Portuguese
    'ru' => 'Русский',           // Russian
    'sr' => 'Srpski',            // Serbian
    'es' => 'Español',           // Spanish
    'sw' => 'Kiswahili',         // Swahili
    'sv' => 'Svenska',           // Swedish
    'th' => 'ไทย',                // Thai
    'tr' => 'Türkçe',            // Turkish
    'uk' => 'Українська',        // Ukrainian
    'ur' => 'اردو',              // Urdu
    'vi' => 'Tiếng Việt',        // Vietnamese
];

// Right-to-left languages mirror the whole page.
const I18N_RTL = ['ar', 'fa', 'he', 'ur'];

// og:locale / <html lang> region hints.
const I18N_LOCALE = [
    'en' => 'en_US', 'sv' => 'sv_SE', 'es' => 'es_ES', 'de' => 'de_DE',
    'nl' => 'nl_NL', 'fr' => 'fr_FR', 'it' => 'it_IT', 'pt' => 'pt_BR',
    'pl' => 'pl_PL', 'hr' => 'hr_HR', 'sr' => 'sr_RS', 'el' => 'el_GR',
    'tr' => 'tr_TR', 'uk' => 'uk_UA', 'ru' => 'ru_RU', 'hi' => 'hi_IN',
    'zh' => 'zh_CN', 'ja' => 'ja_JP', 'ko' => 'ko_KR', 'vi' => 'vi_VN',
    'id' => 'id_ID', 'th' => 'th_TH', 'sw' => 'sw_KE', 'he' => 'he_IL',
    'ar' => 'ar_AR', 'fa' => 'fa_IR', 'ur' => 'ur_PK',
];

if (!function_exists('h')) {
    function h($s) { return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
}

/** Language codes that actually have a translation file (English always in). */
function i18n_available(): array {
    static $avail = null;
    if ($avail !== null) { return $avail; }
    $avail = ['en'];
    foreach (array_keys(I18N_NAMES) as $code) {
        if ($code !== 'en' && is_file(__DIR__ . "/lang/$code.php")) {
            $avail[] = $code;
        }
    }
    return $avail;
}

/** Choose the language: ?lang= (sticks via cookie) -> cookie -> browser. */
function i18n_pick(): string {
    $avail = i18n_available();
    $q = $_GET['lang'] ?? '';
    if (is_string($q) && in_array($q, $avail, true)) {
        @setcookie('lang', $q, time() + 31536000, '/');
        return $q;
    }
    $c = $_COOKIE['lang'] ?? '';
    if (is_string($c) && in_array($c, $avail, true)) { return $c; }
    $al = $_SERVER['HTTP_ACCEPT_LANGUAGE'] ?? '';
    foreach (explode(',', (string)$al) as $part) {
        $code = strtolower(trim(explode(';', $part)[0]));
        $code = explode('-', $code)[0];
        if (in_array($code, $avail, true)) { return $code; }
    }
    return I18N_DEFAULT;
}

$GLOBALS['I18N_LANG'] = i18n_pick();
$GLOBALS['I18N_EN'] = require __DIR__ . '/lang/en.php';
if ($GLOBALS['I18N_LANG'] === 'en') {
    $GLOBALS['I18N_TR'] = $GLOBALS['I18N_EN'];
} else {
    $loaded = require __DIR__ . '/lang/' . $GLOBALS['I18N_LANG'] . '.php';
    // Union: a translated key wins, a missing one falls back to English.
    $GLOBALS['I18N_TR'] = (is_array($loaded) ? $loaded : []) + $GLOBALS['I18N_EN'];
}

/** Translated string for *key* (English fallback, then the key itself). */
function t(string $key): string {
    return $GLOBALS['I18N_TR'][$key] ?? $GLOBALS['I18N_EN'][$key] ?? $key;
}
function lang_code(): string { return $GLOBALS['I18N_LANG']; }
function lang_is_rtl(): bool { return in_array($GLOBALS['I18N_LANG'], I18N_RTL, true); }
function lang_dir(): string { return lang_is_rtl() ? 'rtl' : 'ltr'; }
function lang_locale(): string { return I18N_LOCALE[$GLOBALS['I18N_LANG']] ?? 'en_US'; }

/** <link rel="alternate" hreflang> block for every available language. */
function i18n_hreflang(string $site): string {
    $out = '';
    foreach (i18n_available() as $code) {
        $href = $code === 'en' ? "$site/" : "$site/?lang=$code";
        $out .= '<link rel="alternate" hreflang="' . h($code)
              . '" href="' . h($href) . "\">\n";
    }
    $out .= '<link rel="alternate" hreflang="x-default" href="'
          . h("$site/") . "\">\n";
    return $out;
}

/** Canonical URL for the active language. */
function i18n_canonical(string $site): string {
    return lang_code() === 'en' ? "$site/" : "$site/?lang=" . lang_code();
}
