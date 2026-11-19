#!/bin/sh
# dopeiptv.org's security headers into the TLS-front proxy's own server
# file - the lines proxy-dopeiptv.org.conf in this repo shows - keeping the
# file's own addresses (CONTENT_HOST) as they are. In the webproxy jail:
#
#     sh proxy-headers.sh
#
# The file is copied to /root first. nginx gives a server the http block's
# add_header lines only while the server has none of its own, so every
# such line found there (nginx.conf and what it includes) is named again in
# the server, the CSP from the map with "always". When nginx -t does not
# pass, the copy is put back and nothing is reloaded. A second run changes
# nothing.
set -eu
NGX=${NGINX_DIR:-/usr/local/etc/nginx}
CONF=${1:-$NGX/conf.d/dopeiptv.org.conf}
TEST=${NGINX_TEST:-nginx -t}
RELOAD=${NGINX_RELOAD:-service nginx reload}
KEEP=${BACKUP_DIR:-/root}

say() { printf '%s\n' "$*"; }

[ -f "$CONF" ] || { say "not found: $CONF"; exit 1; }
if grep -q 'dopeiptv_referrer' "$CONF"; then
    say "the headers are already in $CONF - nothing changed"
    exit 0
fi
TMP=$(mktemp -d /tmp/proxyhdr.XXXXXX)
trap 'rm -rf "$TMP"' EXIT

# add_header lines at the level of the http block: nginx.conf's own, and
# the top level of every file it includes there.
TOP='{ line = $0; sub(/#.*/, "", line)
       if (depth == want && line ~ /^[ \t]*add_header[ \t]/) { sub(/^[ \t]+/, "", line); print line }
       if (depth == want && incl && line ~ /^[ \t]*include[ \t]/) {
           sub(/^[ \t]*include[ \t]+/, "", line); sub(/[ \t]*;.*/, "", line); print line > incl }
       depth += gsub(/\{/, "{", line) - gsub(/\}/, "}", line) }'
: > "$TMP/includes"
awk -v want=1 -v incl="$TMP/includes" "$TOP" "$NGX/nginx.conf" > "$TMP/global"
while read -r pattern; do
    case $pattern in /*) ;; *) pattern=$NGX/$pattern ;; esac
    for f in $pattern; do
        [ -f "$f" ] && [ "$f" != "$CONF" ] && awk -v want=0 -v incl="" "$TOP" "$f" >> "$TMP/global"
    done
done < "$TMP/includes"

{
    echo '    add_header Content-Security-Policy   $csp_policy always;'
    echo "    add_header Content-Security-Policy   \"frame-ancestors 'none'\" always;"
    echo '    add_header Strict-Transport-Security "max-age=31536000" always;'
    echo '    add_header X-Content-Type-Options    "nosniff" always;'
    echo '    add_header X-Frame-Options           "DENY" always;'
    echo '    add_header Referrer-Policy           $dopeiptv_referrer always;'
    echo '    proxy_hide_header Referrer-Policy;'
} > "$TMP/headers"
while read -r line; do
    case $line in
        *Content-Security-Policy*'$csp_policy'*) ;;
        *Strict-Transport-Security*|*X-Content-Type-Options*|*X-Frame-Options*|*Referrer-Policy*)
            say "replaced by this site's own: $line" ;;
        *) say "kept from the http block: $line"; printf '    %s\n' "$line" >> "$TMP/headers" ;;
    esac
done < "$TMP/global"
cat > "$TMP/map" <<'EOF'
# Where the address may not leave the site: the admin pages, the support
# page (Stripe sends people back to it with their checkout's id) and the
# Trakt sign-in's way back to the app.
map $uri $dopeiptv_referrer {
    default              "strict-origin-when-cross-origin";
    ~^/admin(?:/|$)      "no-referrer";
    ~^/support(?:/|$)    "no-referrer";
    ~^/trakt/            "no-referrer";
}
EOF
echo '    add_header Strict-Transport-Security "max-age=31536000" always;' > "$TMP/www"

awk -v mapf="$TMP/map" -v hdrf="$TMP/headers" -v wwwf="$TMP/www" -v countf="$TMP/count" '
    function put(f,   l) { while ((getline l < f) > 0) print l; close(f) }
    mapped == 0 && /^[ \t]*server[ \t]*\{/ { put(mapf); print ""; mapped = 1 }
    { print }
    /^[ \t]*server_name[ \t]+dopeiptv\.org[ \t]*;/ { print ""; put(hdrf); main++ }
    /^[ \t]*server_name[ \t]+www\.dopeiptv\.org[ \t]*;/ { print ""; put(wwwf); www++ }
    END { print mapped + 0, main + 0, www + 0 > countf }' "$CONF" > "$TMP/new"
read -r mapped main www < "$TMP/count"
if [ "$mapped" != 1 ] || [ "$main" != 1 ]; then
    say "did not find one \"server_name dopeiptv.org;\" server in $CONF - nothing changed"
    exit 1
fi

copy=$KEEP/dopeiptv.org.conf.$(date +%Y%m%d-%H%M%S)
cp -p "$CONF" "$copy"
cat "$TMP/new" > "$CONF"
if $TEST; then
    $RELOAD
    say "done: the headers are in $CONF (the file as it was: $copy)"
else
    cat "$copy" > "$CONF"
    say "nginx -t did not pass: $CONF is back as it was, nothing reloaded"
    exit 1
fi
