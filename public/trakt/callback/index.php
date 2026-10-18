<?php
/**
 * trakt/callback - where Trakt's browser sign-in comes back for dopeIPTV.
 *
 * A Trakt app that every Trakt account can sign in to ("Production") may
 * only redirect to an https:// address, so dopeIPTV's app is registered with
 * https://iptv.dope.rs/trakt/callback. This page hands the answer straight on
 * to the app itself, which listens for exactly this on the user's own
 * machine (http://127.0.0.1:41794/callback, see the app's
 * providers/oauth_loopback.py) for as long as its sign-in dialog is open.
 *
 * Only the OAuth answer is passed on, and only to that one fixed address:
 * this is no open redirect. The code is worthless to anyone else - the app
 * proves it is the one that asked with PKCE - so it may pass through here
 * (and through the access log) without harm.
 *
 * Changing this address or the port means changing the app too
 * (trakt.WEB_REDIRECT_URI, OAUTH_PORT) and the redirect URI registered with
 * the Trakt app.
 */
declare(strict_types=1);

const APP_CALLBACK = 'http://127.0.0.1:41794/callback';

$answer = [];
foreach (['code', 'state', 'error', 'error_description'] as $key) {
    $value = $_GET[$key] ?? null;
    if (is_string($value) && $value !== '' && strlen($value) <= 2048) {
        $answer[$key] = $value;
    }
}
$target = APP_CALLBACK;
if ($answer) {
    $target .= '?' . http_build_query($answer, '', '&', PHP_QUERY_RFC3986);
}

header('Cache-Control: no-store');
header('Referrer-Policy: no-referrer');
header('X-Robots-Tag: noindex, nofollow');
header('Location: ' . $target, true, 302);
header('Content-Type: text/html; charset=utf-8');
$href = htmlspecialchars($target, ENT_QUOTES, 'UTF-8');
?>
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="robots" content="noindex, nofollow">
<title>dopeIPTV</title>
</head>
<body>
<p>Returning to dopeIPTV&hellip; <a href="<?= $href ?>">Continue</a></p>
</body>
</html>
