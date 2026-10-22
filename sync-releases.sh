#!/bin/sh
# sync-releases.sh - mirror the latest GitHub release of slimture/dopeIPTV
# into public/files/ and render the site's pages from it.
#
# The site is static: there is no PHP anywhere in it. What index.php,
# help/index.php, 404.php and sitemap.php did on every request is done here,
# once per run, for every language (site/render.jq); nginx picks the
# language per request (deploy/content-iptv.dope.rs.conf). What dl.php
# counted per request is read back from nginx's log of /dl.php here.
#
# From /etc/crontab, as the site's own user, every 15 minutes (DEPLOY.md):
#   0,15,30,45 * * * * iptv /bin/sh /usr/local/www/iptv/sync-releases.sh >> /var/log/iptv-sync.log 2>&1
#
# Needs FreeBSD's fetch(1), sha256(1) and md5(1), and jq. GitHub's API is
# asked once per run, unauthenticated: 4 calls an hour of the 60 allowed.
#
# Exit status: 0 - the pages are current (also while a release is still
# being built: they keep the release before it). 1 - GitHub or a download
# failed, or a page did not render: every page is left exactly as it was.

set -eu
umask 022
# /etc/crontab's PATH leaves out /usr/local/bin, where jq is.
PATH=$PATH:/usr/local/bin
export PATH

BASE=${IPTV_BASE:-$(cd "$(dirname "$0")" && pwd)}
PUB=$BASE/public
SITE=$BASE/site
FILES=$PUB/files
REL=$PUB/releases.json
COUNTS=$BASE/downloads.json
DL_LOG=${IPTV_DOWNLOAD_LOG:-/var/log/nginx/iptv.dope.rs-downloads.log}
LOG_STATE=$BASE/.downloads-log.state
REPO=slimture/dopeIPTV
UA=dopeiptv-site-sync
TAB=$(printf '\t')
US=$(printf '\037')   # field separator that is not whitespace: empty fields stay fields

say() { printf '[sync] %s\n' "$*" >&2; }

# ---- One sync at a time ------------------------------------------------------
# The cron fires every 15 minutes, and a release lands in the minutes someone
# is most likely to run this by hand. Two runs downloading into the same
# files is how an installer once came out interleaved. A second run leaves
# quietly: the work is idempotent and the one already running is doing it.
LOCK=$BASE/.sync.lock
running() {
    pid=$(cat "$LOCK/pid" 2>/dev/null || true)
    [ -n "$pid" ] && ps -p "$pid" -o command= 2>/dev/null | grep -q sync-releases
}
if ! mkdir "$LOCK" 2>/dev/null; then
    if running; then
        say "another sync is already running - nothing to do"
        exit 0
    fi
    # Left behind by a run that was killed - or by the PHP sync, whose lock
    # was a plain file of this name.
    rm -rf "$LOCK"
    if ! mkdir "$LOCK" 2>/dev/null; then
        say "another sync is already running - nothing to do"
        exit 0
    fi
fi
echo $$ > "$LOCK/pid"
rm -rf "$BASE"/.sync.??????    # a killed run's scratch directory
TMP=$(mktemp -d "$BASE/.sync.XXXXXX")
trap 'rm -rf "$TMP" "$LOCK"' EXIT
trap 'exit 1' HUP INT TERM

mkdir -p "$FILES"

# ---- Download counts -------------------------------------------------------------
# dl.php counted a download per request; nginx now answers /dl.php itself,
# in a log of its own, and the count is taken from that log with dl.php's
# rules: a plain GET that was sent on to the file (302), from something
# that names itself and does not say it is a bot. The log is read from where
# the last run stopped, the end of the file it was rotated into included.
# Counted before GitHub is asked, so a run GitHub fails still counts.
inode() { ls -i "$1" | awk '{ print $1 }'; }
count_downloads() {
    if [ ! -e "$DL_LOG" ]; then
        say "no download log at $DL_LOG yet - counts unchanged"
        return 0
    fi
    if [ ! -r "$DL_LOG" ]; then
        say "cannot read $DL_LOG - counts unchanged"
        return 0
    fi
    old_ino="" old_off=0
    if [ -r "$LOG_STATE" ]; then
        read -r old_ino old_off < "$LOG_STATE" || true
    fi
    case $old_off in ''|*[!0-9]*) old_off=0 ;; esac
    ino=$(inode "$DL_LOG")
    : > "$TMP/log.new"
    if [ -n "$old_ino" ] && [ "$ino" != "$old_ino" ]; then
        # Rotated since the last run: first what was left of the old file,
        # which newsyslog keeps uncompressed as .0 (the p flag, DEPLOY.md).
        if [ -f "$DL_LOG.0" ] && [ "$(inode "$DL_LOG.0")" = "$old_ino" ]; then
            tail -c +$((old_off + 1)) "$DL_LOG.0" >> "$TMP/log.new"
        fi
        old_off=0
    fi
    size=$(wc -c < "$DL_LOG" | tr -d ' ')
    [ "$size" -ge "$old_off" ] || old_off=0
    tail -c +$((old_off + 1)) "$DL_LOG" | head -c $((size - old_off)) > "$TMP/log.cur"
    # A line nginx is still writing waits for the next run.
    if [ -s "$TMP/log.cur" ] && [ "$(tail -c 1 "$TMP/log.cur" | od -An -c | tr -d ' ')" != '\n' ]; then
        sed '$d' "$TMP/log.cur" > "$TMP/log.cur2"
        mv -f "$TMP/log.cur2" "$TMP/log.cur"
    fi
    off=$((old_off + $(wc -c < "$TMP/log.cur" | tr -d ' ')))
    cat "$TMP/log.cur" >> "$TMP/log.new"

    # Combined format: "$request" is field 2 and "$http_user_agent" field 6
    # between double quotes (nginx writes a quote inside a value as \x22).
    awk -F '"' '
        $2 ~ /^GET \/dl\.php\?/ && $3 ~ /^ 302 / {
            ua = tolower($6)
            if (ua == "" || ua == "-") next
            if (ua ~ /bot|crawl|spider|slurp|preview|facebookexternalhit|whatsapp|telegram|discord|skype|embed|monitor|scan/) next
            split($2, req, " ")
            q = req[2]; sub(/^[^?]*\?/, "", q)
            n = split(q, args, "&"); name = ""
            for (i = 1; i <= n; i++) if (args[i] ~ /^f=/) { name = substr(args[i], 3); break }
            sub(/.*\//, "", name)
            if (name != "") count[name]++
        }
        END { for (k in count) printf "%s\t%d\n", k, count[k] }' "$TMP/log.new" > "$TMP/counted.tsv"

    if [ -s "$TMP/counted.tsv" ]; then
        old=$COUNTS
        if [ ! -s "$old" ]; then
            old=/dev/null
        elif ! jq -e 'type == "object"' "$old" >/dev/null 2>&1; then
            # Never start the counts over: they go back years.
            say "$COUNTS is not a JSON object - counts left alone until it is fixed"
            return 0
        fi
        jq -n --indent 4 --slurpfile old "$old" --rawfile new "$TMP/counted.tsv" '
            reduce ($new | split("\n")[] | select(. != "") | split("\t")) as [$k, $n]
                ($old[0] // {}; .[$k] = ((.[$k] // 0) + ($n | tonumber)))' > "$TMP/downloads.json"
        cp "$TMP/downloads.json" "$COUNTS.part"
        mv -f "$COUNTS.part" "$COUNTS"
        say "counted $(awk -F "$TAB" '{ s += $2 } END { print s }' "$TMP/counted.tsv") download(s)"
    fi
    echo "$ino $off" > "$LOG_STATE.part"
    mv -f "$LOG_STATE.part" "$LOG_STATE"
}
count_downloads

# ---- The latest release --------------------------------------------------------
API=$TMP/latest.json
if ! fetch -q -T 30 --user-agent="$UA" -o "$API" \
        "https://api.github.com/repos/$REPO/releases/latest"; then
    say "could not get the latest release from GitHub - keeping the site as it is"
    exit 1
fi
if ! jq -e '.tag_name | type == "string" and length > 0' "$API" >/dev/null 2>&1; then
    say "unexpected API payload; keeping the site as it is"
    exit 1
fi
version=$(jq -r '.tag_name | sub("^v+"; "")' "$API")

# ---- Completeness gate -------------------------------------------------------------
# A finished release carries all three desktop platforms. GitHub attaches the
# assets one CI job at a time, and a run that lands mid-build would replace a
# complete mirror with the fast jobs' files and prune the rest. Only assets
# GitHub calls uploaded count; while a platform is missing, the release
# before it stays on the site.
missing=$(jq -r '
    [.assets[]? | select((.state // "uploaded") == "uploaded")
     | .name // "" | split("/") | last | ascii_downcase] as $n
    | [if any($n[]; endswith(".appimage") or endswith(".deb")
              or endswith(".rpm") or endswith(".flatpak")) then empty else "Linux" end,
       if any($n[]; endswith(".dmg") or endswith(".pkg")) then empty else "macOS" end,
       if any($n[]; endswith(".exe") or endswith(".msi")
              or (endswith(".zip") and contains("win"))) then empty else "Windows" end]
    | join("+")' "$API")

if [ -n "$missing" ]; then
    say "v$version incomplete (missing $missing); still building - keeping the current release, retry next run"
else
    # ---- Mirror the installers ---------------------------------------------------
    # Only what end users install: the wheel and the source tarball are left
    # on GitHub. Fetched only when missing or a different size - a release's
    # assets never change. (The parentheses around `... as $l | ...` matter:
    # jq 1.8 would bind the whole `and` to $l - see site/render.jq.)
    jq -r '
        .assets[]? | select((.state // "uploaded") == "uploaded")
        | (.name // "" | split("/") | last) as $name
        | select($name != "" and (($name | ascii_downcase) as $l
                 | any(".dmg", ".appimage", ".deb", ".rpm", ".flatpak", ".pkg",
                       ".exe", ".msi", ".zip"; . as $e | $l | endswith($e))))
        | [$name, (.size // -1 | tostring), (.browser_download_url // ""),
           (.digest // "")] | join("\u001f")' "$API" > "$TMP/assets.tsv"

    failed=""
    : > "$TMP/mirrored.tsv"
    while IFS=$US read -r name size url digest; do
        dest=$FILES/$name
        have=-1
        [ -f "$dest" ] && have=$(wc -c < "$dest" | tr -d ' ')
        want=""
        case $digest in sha256:*) want=${digest#sha256:} ;; esac
        if [ "$have" != "$size" ]; then
            printf '[sync] fetching %s (%s bytes) ... ' "$name" "$size" >&2
            part=$dest.part.$$
            if ! fetch -q -T 1800 --user-agent="$UA" -o "$part" "$url"; then
                rm -f "$part"
                echo "FAILED" >&2
                failed="$failed $name"
                continue
            fi
            # Against GitHub's own digest, while the bytes are in hand: a
            # truncated download must never go out advertised with the
            # right checksum.
            if [ -n "$want" ] && [ "$(sha256 -q "$part")" != "$want" ]; then
                rm -f "$part"
                echo "CORRUPT (sha256 mismatch)" >&2
                failed="$failed $name"
                continue
            fi
            if ! mv -f "$part" "$dest"; then
                rm -f "$part"
                echo "FAILED (could not put it in place)" >&2
                failed="$failed $name"
                continue
            fi
            echo "done" >&2
        else
            say "$name already current"
        fi
        sha=$want
        if [ -z "$sha" ] && [ -f "$dest" ]; then
            sha=$(sha256 -q "$dest")
        fi
        printf '%s\t%s\n' "$name" "$sha" >> "$TMP/mirrored.tsv"
    done < "$TMP/assets.tsv"

    # A download that failed is a half-built release too: keep what is
    # published and try again next run, rather than list one installer short.
    if [ -n "$failed" ]; then
        say "v$version: asset(s) failed to mirror (${failed# }) - keeping the site as it is, retry next run"
        exit 1
    fi

    # A standard SHA256SUMS, so `sha256sum -c SHA256SUMS` works.
    awk -F "$TAB" '$2 != "" { print $2 "  " $1 }' "$TMP/mirrored.tsv" > "$TMP/SHA256SUMS"
    if [ -s "$TMP/SHA256SUMS" ]; then
        cp "$TMP/SHA256SUMS" "$FILES/SHA256SUMS.part"
        mv -f "$FILES/SHA256SUMS.part" "$FILES/SHA256SUMS"
        printf 'SHA256SUMS\t\n' >> "$TMP/mirrored.tsv"
    fi

    # Whatever the latest release does not name goes.
    for f in "$FILES"/*; do
        [ -f "$f" ] || [ -L "$f" ] || continue
        b=${f##*/}
        if ! cut -f1 "$TMP/mirrored.tsv" | grep -qxF -- "$b"; then
            rm -f "$f"
            say "pruned stale $b"
        fi
    done

    # releases.json, the release the pages are rendered from, in the shape
    # and layout the PHP sync wrote it.
    synced=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)
    jq -j --indent 4 -a --arg synced "$synced" --rawfile mirrored "$TMP/mirrored.tsv" '
        def human: . as $b | ["B", "KB", "MB", "GB"] as $u
            | {v: $b, i: 0} | until(.v < 1024 or .i >= 3; .v /= 1024 | .i += 1)
            | "\((.v * 10 | round) / 10) \($u[.i])";
        def classify($name):
            ($name | ascii_downcase) as $n
            | ($n | contains("arm") or contains("aarch64")) as $arm
            | if $n | endswith(".dmg") then
                ["macOS · .dmg", (if $arm then "Apple Silicon (M-series)" else "Intel" end), "🍎"]
              elif $n | endswith(".appimage") then
                ["Linux · AppImage", (if $arm then "ARM64 · portable" else "x86_64 · portable" end), "🐧"]
              elif $n | endswith(".deb") then
                ["Linux · .deb", "Debian / Ubuntu" + (if $arm then " (ARM64)" else "" end), "🐧"]
              elif $n | endswith(".rpm") then ["Linux · .rpm", "Fedora / RHEL", "🐧"]
              elif $n | endswith(".flatpak") then ["Flatpak", "Flathub · all distros", "📦"]
              elif $n | endswith(".pkg") then ["macOS · .pkg", "installer", "🍎"]
              elif ($n | endswith(".exe")) or ($n | endswith(".msi")) then ["Windows", "installer", "🪟"]
              elif $n | endswith(".zip") then
                (if $n | contains("win")
                 then ["Windows · portable", (if $arm then "ARM64" else "x64" end) + " · unzip &amp; run", "🪟"]
                 else ["Archive", "zip", "📦"] end)
              else [$name, "", "📦"] end;
        # Capped: a landing page section, not an archive.
        def cap($max):
            if utf8bytelength <= $max then .
            else reduce explode[] as $c ({out: [], n: 0};
                    (if $c < 128 then 1 elif $c < 2048 then 2
                     elif $c < 65536 then 3 else 4 end) as $w
                    | if .n + $w > $max then . else .out += [$c] | .n += $w end)
                 | (.out | implode) + "\n\n…" end;
        ($mirrored | split("\n") | map(select(. != "") | split("\t")
                                      | {key: .[0], value: .[1]}) | from_entries) as $sha
        | {version: (.tag_name | sub("^v+"; "")),
           published_at: (.published_at // null),
           notes: (.body // "" | tostring | cap(20000)),
           html_url: (.html_url // "https://github.com/slimture/dopeIPTV/releases"),
           assets: [.assets[]? | select((.state // "uploaded") == "uploaded")
                    | (.name // "" | split("/") | last) as $name
                    | select($sha | has($name)) | classify($name) as [$label, $sub, $icon]
                    | (.size // 0 | human) as $size
                    | {label: $label,
                       sub: (if $sub != "" then $sub + " · " + $size else $size end),
                       icon: $icon, url: ("/files/" + ($name | @uri)),
                       name: $name, sha256: $sha[$name]}],
           synced_at: $synced}' "$API" > "$TMP/releases.json"
    cp "$TMP/releases.json" "$REL.part"
    mv -f "$REL.part" "$REL"
    say "wrote releases.json for v$version with $(jq '.assets | length' "$REL") asset(s)"
fi

# ---- The pages -----------------------------------------------------------------------
# Every page in every language, into a staging directory first; only once
# all of them rendered do they replace the published ones, each by a rename
# in its own directory, so a visitor never sees a page half written. A page
# that did not change is left alone (its date and ETag with it).
OUT=$TMP/out
mkdir -p "$OUT"

# English and every language with a translation file, in the switcher's order.
codes=en
for c in $(jq -r '.names | keys_unsorted[] | select(. != "en")' "$SITE/i18n.json"); do
    if [ -f "$SITE/lang/$c.json" ]; then codes="$codes $c"; fi
done
avail=$(for c in $codes; do echo "$c"; done | jq -R . | jq -cs .)

asset_urls=$(for p in /style.css /nav.js /app.js /help.js; do
    printf '%s\t%s?v=%s\n' "$p" "$p" "$(md5 -q "$PUB$p" | cut -c1-10)"
done | jq -R 'split("\t") | {key: .[0], value: .[1]}' | jq -cs from_entries)
screenshots=$(for f in main.png epg.png timeshift.png recordings.png; do
    [ -f "$PUB/screenshots/$f" ] && echo "$f"
done | jq -R . | jq -cs .)
sums=""
[ -f "$FILES/SHA256SUMS" ] && sums=1
year=$(date +%Y)
newest=$(ls -t "$SITE/templates/help.html" "$SITE"/lang/*.json | head -1)
help_mtime=$(date -r "$newest" +%s)
rel=$REL
[ -s "$rel" ] && jq -e 'type == "object"' "$rel" >/dev/null 2>&1 || rel=/dev/null
counts=$COUNTS
[ -s "$counts" ] && jq -e 'type == "object"' "$counts" >/dev/null 2>&1 || counts=/dev/null

render() {  # render PAGE LANG TEMPLATE OUTFILE
    mkdir -p "${4%/*}"
    tr=$SITE/lang/$2.json
    jq -n -j -f "$SITE/render.jq" \
        --arg page "$1" --arg lang "$2" --arg year "$year" --arg sums "$sums" \
        --argjson avail "$avail" --argjson asset_urls "$asset_urls" \
        --argjson screenshots "$screenshots" --argjson help_mtime "$help_mtime" \
        --slurpfile site "$SITE/i18n.json" --slurpfile helpfile "$SITE/help.json" \
        --slurpfile fmt "$SITE/formats.json" \
        --slurpfile en "$SITE/lang/en.json" --slurpfile tr "$tr" \
        --slurpfile rel "$rel" --slurpfile dl "$counts" \
        --rawfile tpl "$3" --rawfile navtpl "$SITE/templates/nav.html" > "$4"
}
for c in $codes; do
    render index "$c" "$SITE/templates/index.html" "$OUT/i18n/$c/index.html"
    render help "$c" "$SITE/templates/help.html" "$OUT/i18n/$c/help/index.html"
    render 404 "$c" "$SITE/templates/404.html" "$OUT/i18n/$c/404.html"
done
render sitemap en /dev/null "$OUT/i18n/sitemap.xml"

changed=0
for f in $(cd "$OUT" && find . -type f | sort); do
    f=${f#./}
    dest=$PUB/$f
    if [ -f "$dest" ] && cmp -s "$OUT/$f" "$dest"; then
        continue
    fi
    mkdir -p "${dest%/*}"
    cp "$OUT/$f" "${dest%/*}/.${dest##*/}.$$"
    mv -f "${dest%/*}/.${dest##*/}.$$" "$dest"
    changed=$((changed + 1))
done
say "pages: $changed of $(cd "$OUT" && find . -type f | wc -l | tr -d ' ') updated"
