#!/bin/sh
# What the live site really sends, checked from outside (a laptop, not the
# server): the security headers on a page, on a 404 and on a service, and
# that the content server's own port does not answer from the internet.
# The repository holds the configuration; only this shows it is the one in
# force.   sh deploy/check-live.sh [host] [content server address]
# Exit 0 when all is as it should be; each miss is said.
set -u
HOST=${1:-dopeiptv.org}
CONTENT=${2:-}
fail=0
miss() { echo "MISSING: $*"; fail=1; }
check() {  # check PATH: the headers every answer carries
    h=$(curl -sS -o /dev/null -D - --max-time 15 "https://$HOST$1" | tr -d '\r') || { miss "$1 did not answer"; return; }
    echo "$h" | grep -qi '^content-security-policy:.*default-src' || miss "$1: a Content-Security-Policy with default-src"
    echo "$h" | grep -qi "^content-security-policy:.*frame-ancestors 'none'" || miss "$1: frame-ancestors 'none'"
    echo "$h" | grep -qi '^strict-transport-security: max-age=[1-9]' || miss "$1: Strict-Transport-Security"
    echo "$h" | grep -qi '^x-content-type-options: nosniff' || miss "$1: X-Content-Type-Options"
    echo "$h" | grep -qi '^x-frame-options: deny' || miss "$1: X-Frame-Options"
    echo "$h" | grep -qi '^referrer-policy:' || miss "$1: Referrer-Policy"
    case $1 in /admin/*)    # the admin pages alone in a window of their own
        echo "$h" | grep -qi '^cross-origin-opener-policy: *same-origin' || miss "$1: Cross-Origin-Opener-Policy same-origin" ;;
    esac
    echo "$h" | grep -qi '^server:.*[0-9]' && miss "$1: the server names its version"
    echo "checked $1"
}
check /
check /transparency/
check /no-such-page-$$
check /feedback/replies
check /admin/
if [ -n "$CONTENT" ]; then
    # The content server listens for the front proxy alone: from here, nothing.
    if curl -sS -o /dev/null --max-time 5 "http://$CONTENT:8081/" 2>/dev/null; then
        miss "http://$CONTENT:8081/ answers from outside - the proxy-only rule is not in force"
    else
        echo "checked $CONTENT:8081 (no answer from outside)"
    fi
fi
[ "$fail" = 0 ] && echo "all as it should be"
exit "$fail"
