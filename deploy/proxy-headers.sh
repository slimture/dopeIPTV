#!/bin/sh
# dopeiptv.org's security headers, limits and log lines into the TLS-front
# proxy's own server files - what proxy-dopeiptv.org.conf and
# proxy-iptv.dope.rs.conf in this repo show - keeping each file's own
# addresses (CONTENT_HOST) and certificates as they are. In the webproxy
# jail:
#
#     sh proxy-headers.sh
#
# Every line of those that the live file lacks is put in where it belongs:
# a server's own after its server_name, a location's after its first line,
# a map or a zone before the first server. Nothing already there is changed
# or put in twice - a header or a size the live file has with a value of
# its own is kept, and said - so a file from before any of it gets all of
# it, one from an earlier run what is new, and a second run changes
# nothing. nginx gives a server the http block's add_header lines only
# while the server has none of its own, so every such line found there
# (nginx.conf and what it includes) is named again in the server, the CSP
# from the map with "always". Each file changed is copied to /root first;
# when nginx -t does not pass, they are put back and nothing is reloaded.
set -eu
NGX=${NGINX_DIR:-/usr/local/etc/nginx}
CONF=${1:-$NGX/conf.d/dopeiptv.org.conf}
OLD=${2:-$NGX/conf.d/iptv.dope.rs.conf}
TEST=${NGINX_TEST:-nginx -t}
RELOAD=${NGINX_RELOAD:-service nginx reload}
KEEP=${BACKUP_DIR:-/root}

say() { printf '%s\n' "$*"; }

[ -f "$CONF" ] || { say "not found: $CONF"; exit 1; }
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
        [ -f "$f" ] && [ "$f" != "$CONF" ] && [ "$f" != "$OLD" ] && awk -v want=0 -v incl="" "$TOP" "$f" >> "$TMP/global"
    done
done < "$TMP/includes"

{
    echo '    add_header Content-Security-Policy   $csp_policy always;'
    echo "    add_header Content-Security-Policy   \"frame-ancestors 'none'\" always;"
    echo '    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;'
    echo '    add_header X-Content-Type-Options    "nosniff" always;'
    echo '    add_header X-Frame-Options           "DENY" always;'
    echo '    add_header Referrer-Policy           $dopeiptv_referrer always;'
    echo '    proxy_hide_header Referrer-Policy;'
    echo '    add_header Permissions-Policy        "camera=(), microphone=(), geolocation=(), display-capture=(), usb=(), serial=(), hid=(), bluetooth=(), midi=(), idle-detection=(), xr-spatial-tracking=(), browsing-topics=()" always;'
    echo '    add_header Cross-Origin-Opener-Policy   $dopeiptv_coop always;'
    echo '    add_header Cross-Origin-Resource-Policy $dopeiptv_corp always;'
} > "$TMP/headers"
echo '    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;' > "$TMP/www"
while read -r line; do
    case $line in
        *Content-Security-Policy*'$csp_policy'*) ;;
        *Strict-Transport-Security*)
            # The http block's own is kept: it may promise more (subdomains, preload).
            say "kept from the http block, in place of the site's own: $line"
            grep -v 'Strict-Transport-Security' "$TMP/headers" > "$TMP/h2"
            printf '    %s\n' "$line" >> "$TMP/h2"
            mv "$TMP/h2" "$TMP/headers"
            printf '    %s\n' "$line" > "$TMP/www" ;;
        *X-Content-Type-Options*|*X-Frame-Options*|*Referrer-Policy*|*Permissions-Policy*|*Cross-Origin-*)
            say "replaced by this site's own: $line" ;;
        *) say "kept from the http block: $line"; printf '    %s\n' "$line" >> "$TMP/headers" ;;
    esac
done < "$TMP/global"

# What each block must have: "<server_name> [+ssl][|<location>]", a tab,
# the line. Every server of the site: no access log, the error log only for
# what stops it (the privacy policy, section 9), no version in an answer.
want() { while IFS= read -r l; do printf '%s\t%s\n' "$1" "$l"; done; }
LOGS='access_log off;
error_log /var/log/nginx/error.log crit;
server_tokens off;'
MAIN='dopeiptv.org +ssl'
{
    printf '%s\n' "$LOGS" | want "$MAIN"
    want "$MAIN" < "$TMP/headers"
    printf '%s\n' 'client_max_body_size    256k;' 'client_body_buffer_size 256k;' | want "$MAIN"
    printf '%s\n' 'client_body_buffer_size 3m;' 'limit_conn dopeiptv_sync_put 8;' | want "$MAIN|location ^~ /sync/"
    printf '%s\n' 'client_body_buffer_size 4k;' | want "$MAIN|location ^~ /together/"
    { printf '%s\n' "$LOGS"; cat "$TMP/www"; } | want 'www.dopeiptv.org +ssl'
    printf '%s\n' "$LOGS" | want 'dopeiptv.org www.dopeiptv.org'
} > "$TMP/want.site"
{
    { printf '%s\n' "$LOGS"; cat "$TMP/www"; } | want 'iptv.dope.rs +ssl'
    printf '%s\n' "$LOGS" | want 'iptv.dope.rs'
} > "$TMP/want.old"

# Before the first server: each map and zone the file has no line of yet.
: > "$TMP/top"
top() {  # top MARKER < block
    if grep -v '^[[:space:]]*#' "$CONF" | grep -qF "$1"; then
        cat > /dev/null
    else
        cat >> "$TMP/top"
        echo >> "$TMP/top"
    fi
}
top 'map $uri $dopeiptv_referrer' <<'EOF'
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
top 'map $uri $dopeiptv_coop' <<'EOF'
# The admin pages in a window of their own (Cross-Origin-Opener-Policy).
map $uri $dopeiptv_coop {
    default              "";
    ~^/admin(?:/|$)      "same-origin";
}
EOF
top 'map $uri $dopeiptv_corp' <<'EOF'
# The admin pages and the services' answers never loaded into another
# site's page (Cross-Origin-Resource-Policy).
map $uri $dopeiptv_corp {
    default                                   "";
    ~^/admin(?:/|$)                           "same-origin";
    ~^/(?:sync|together|feedback|donate)/     "same-origin";
    ~^/(?:support/key|hit)$                   "same-origin";
}
EOF
top 'map $request_method $dopeiptv_sync_put' <<'EOF'
# A few sync copies at once from one address, never more (PUTs only).
map $request_method $dopeiptv_sync_put {
    default              "";
    PUT                  $binary_remote_addr;
}
EOF
top 'zone=dopeiptv_sync_put:' <<'EOF'
limit_conn_zone $dopeiptv_sync_put zone=dopeiptv_sync_put:1m;
EOF

# The file read twice: first what each server and location has, then the
# file again with what it lacks put in. A line is the same as one there by
# its directive (a header by its name, the CSP's two by their whole line);
# one there with another value is kept and said in the notes.
FILL='
function norm(s) { sub(/^[ \t]+/, "", s); sub(/[ \t]+#.*$/, "", s); sub(/[ \t]+$/, "", s)
                   gsub(/[ \t]+/, " ", s); return s }
function ident(s,   w) {
    split(s, w, " ")
    if (w[1] == "add_header" && w[2] == "Content-Security-Policy") return s
    if (w[1] == "add_header" || w[1] == "proxy_hide_header" || w[1] == "limit_conn") return w[1] " " w[2]
    return w[1]
}
function opens(s,   t) { t = s; sub(/#.*/, "", t); return gsub(/\{/, "", t) }
function closes(s,   t) { t = s; sub(/#.*/, "", t); return gsub(/\}/, "", t) }
function put(f,   l) { while ((getline l < f) > 0) print l; close(f) }
function key(i) { return name[i] (ssl[i] ? " +ssl" : "") }
function add(k, loc, ind,   i, v, id, got, first) {
    first = 1
    for (i = 1; i <= nw[k]; i++) {
        v = w[k, i]; id = ident(norm(v)); got = ""
        if (loc == "" && ((s, id) in have)) got = have[s, id]
        if (loc != "" && ((s, loc, id) in lhave)) got = lhave[s, loc, id]
        if (got == "") {
            if (first && loc == "") print ""
            first = 0
            print ind v; added++
        } else if (got != norm(v)) {
            print "kept as it is in " k ": " got "  (this repo: " norm(v) ")" >> notef
        }
    }
}
BEGIN {
    while ((getline l < wantf) > 0) {
        i = index(l, "\t"); k = substr(l, 1, i - 1); v = substr(l, i + 1)
        sub(/^[ \t]+/, "", v); nw[k]++; w[k, nw[k]] = v
    }
    close(wantf)
}
FNR == NR {
    t = $0; sub(/#.*/, "", t); n = norm(t)
    if (depth == 0 && t ~ /^[ \t]*server[ \t]*\{/) { s++; inserver = 1 }
    else if (inserver && depth == 1) {
        if (n ~ /^server_name /) { v = n; sub(/^server_name /, "", v); sub(/ ?;$/, "", v); name[s] = v }
        if (n ~ /^listen / && n ~ / ssl( |;|$)/) ssl[s] = 1
        if (n ~ /^location / && opens($0) > closes($0)) { loc = n; sub(/ ?\{.*$/, "", loc); inloc = 1 }
        else if (n != "") have[s, ident(n)] = n
    } else if (inserver && depth == 2 && inloc && n != "") {
        lhave[s, loc, ident(n)] = n
    }
    depth += opens($0) - closes($0)
    if (depth < 2) inloc = 0
    if (depth == 0) inserver = 0
    next
}
FNR == 1 { depth = 0; s = 0; inserver = 0 }
{
    t = $0; sub(/#.*/, "", t); n = norm(t)
    opening = 0
    if (depth == 0 && t ~ /^[ \t]*server[ \t]*\{/) {
        if (!topped) { if (topf != "") put(topf); topped = 1 }
        s++; inserver = 1; opening = 1
    }
    print
    if (inserver && !opening && depth == 1) {
        ind = $0; sub(/[^ \t].*$/, "", ind)
        if (n ~ /^server_name /) { found[key(s)]++; add(key(s), "", ind) }
        else if (n ~ /^location /) {
            loc = n; sub(/ ?\{.*$/, "", loc)
            if ((key(s) "|" loc) in nw) {
                if (opens($0) > closes($0)) add(key(s) "|" loc, loc, ind "    ")
                else print "not put in: " key(s) "|" loc " is on one line" >> notef
            }
        }
    }
    depth += opens($0) - closes($0)
    if (depth == 0) inserver = 0
}
END { print found[main] + 0, added + 0, topped + 0 > countf }'

fill() {  # fill FILE WANT TOP MAIN OUT
    awk -v wantf="$2" -v topf="$3" -v main="$4" -v notef="$TMP/notes" -v countf="$TMP/count" \
        "$FILL" "$1" "$1" > "$5"
    read -r found added topped < "$TMP/count"
}

: > "$TMP/notes"
fill "$CONF" "$TMP/want.site" "$TMP/top" "$MAIN" "$TMP/new"
if [ "$found" != 1 ] || [ "$topped" != 1 ]; then
    say "did not find one \"server_name dopeiptv.org;\" server in $CONF - nothing changed"
    exit 1
fi
site=no old=no
cmp -s "$CONF" "$TMP/new" || site=yes
if [ -f "$OLD" ]; then
    fill "$OLD" "$TMP/want.old" "" 'iptv.dope.rs +ssl' "$TMP/new.old"
    if [ "$found" = 1 ]; then
        cmp -s "$OLD" "$TMP/new.old" || old=yes
    else
        say "no https server \"server_name iptv.dope.rs;\" in $OLD - left as it is"
    fi
fi
while IFS= read -r note; do say "$note"; done < "$TMP/notes"
if [ "$site" = no ] && [ "$old" = no ]; then
    say "everything is in $CONF already - nothing changed"
    exit 0
fi

stamp=$(date +%Y%m%d-%H%M%S)
back() {  # put every file changed back as it was
    [ "$site" = no ] || cat "$KEEP/${CONF##*/}.$stamp" > "$CONF"
    [ "$old" = no ] || cat "$KEEP/${OLD##*/}.$stamp" > "$OLD"
}
[ "$site" = no ] || cp -p "$CONF" "$KEEP/${CONF##*/}.$stamp"
[ "$old" = no ] || cp -p "$OLD" "$KEEP/${OLD##*/}.$stamp"
[ "$site" = no ] || cat "$TMP/new" > "$CONF"
[ "$old" = no ] || cat "$TMP/new.old" > "$OLD"
if $TEST; then
    $RELOAD
    [ "$site" = no ] || say "done: $CONF has what it lacked (the file as it was: $KEEP/${CONF##*/}.$stamp)"
    [ "$old" = no ] || say "done: $OLD has what it lacked (the file as it was: $KEEP/${OLD##*/}.$stamp)"
else
    back
    say "nginx -t did not pass: the files are back as they were, nothing reloaded"
    exit 1
fi
