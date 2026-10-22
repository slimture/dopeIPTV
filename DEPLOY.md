# Deploy: iptv.dope.rs without PHP

From the PHP site to the static one, on `slimfree`. Afterwards nginx in
`pontusweb` serves plain files, `sync-releases.sh` (sh, `fetch`, `jq`) does
what `sync-releases.php` did and renders every page, and nothing for
iptv.dope.rs talks to php-fpm. Nextcloud's nginx and php-fpm configs are not
touched.

Nothing is switched before step 8, and step 7 proves first that every page
comes out byte for byte the same as the PHP one. Until step 11 going back is
two commands (Rollback, at the end).

Steps marked **host** run as root on slimfree. Steps marked **jail** run in
a shell inside the jail:

```sh
bastille console pontusweb
sh                      # the commands below are sh; harmless if it already is
```

## 1. jq (host)

```sh
bastille cmd pontusweb pkg install -y jq
bastille cmd pontusweb jq --version            # jq-1.8.2 or later
```

`fetch`, `sha256` and `md5` are part of FreeBSD.

## 2. The files (host)

```sh
rm -rf /root/iptv-static && mkdir /root/iptv-static
fetch -qo - https://github.com/slimture/dopeIPTV/archive/refs/heads/website.tar.gz \
    | tar -xf - -C /root/iptv-static --strip-components 1
ls /root/iptv-static        # DEPLOY.md README.md deploy public site sync-releases.sh
```

## 3. Into the jail (host)

```sh
W=/usr/local/bastille/jails/pontusweb/root/usr/local/www/iptv
cp /root/iptv-static/sync-releases.sh "$W/"
cp -R /root/iptv-static/site "$W/"
cp -R /root/iptv-static/public/. "$W/public/"
```

Plain `cp -R`: never `cp -p`, never `rsync --delete` (README, "Updating the
site"). This only adds files; the live site is still the PHP one.

## 4. The PHP sync off (jail)

```sh
sed -i '' '/sync-releases\.php/s/^/#/' /etc/crontab
grep -n sync-releases /etc/crontab      # the php line now starts with #
pgrep -lf sync-releases.php             # nothing - no PHP sync still running
```

## 5. A user of its own (jail)

```sh
pw useradd iptv -c "iptv.dope.rs sync" -d /nonexistent -s /usr/sbin/nologin
chown -R iptv:iptv /usr/local/www/iptv
chown www /usr/local/www/iptv/downloads.json      # dl.php counts into it until step 8
touch /var/log/iptv-sync.log && chown iptv:iptv /var/log/iptv-sync.log
```

php-fpm runs as `www` for Nextcloud, and the site was `www`'s only because
`dl.php` wrote the counts. With no PHP left, nothing running as `www` can
write to the site: a php-fpm taken over cannot replace an installer or a
page. nginx only reads (files 644, directories 755).

## 6. The sync, once by hand (jail)

```sh
cd /usr/local/www/iptv
cp public/releases.json /tmp/releases.json.php
su -m iptv -c 'sh sync-releases.sh'; echo "exit $?"
```

Expect:

```
[sync] no download log at /var/log/nginx/iptv.dope.rs-downloads.log yet - counts unchanged
[sync] dopeIPTV-1.2.17-aarch64.AppImage already current
...                                    (one line per installer)
[sync] wrote releases.json for v1.2.17 with 8 asset(s)
[sync] pages: 82 of 82 updated
exit 0
```

```sh
diff /tmp/releases.json.php public/releases.json   # only the "synced_at" line
ls public/i18n                                     # 27 languages and sitemap.xml
```

## 7. Same pages as PHP (jail)

The PHP site is still live, so every page can be compared with it:

```sh
n=0 bad=0
for l in $(ls public/i18n | grep -v sitemap); do
  for p in '' help/; do
    n=$((n + 1))
    fetch -qo /tmp/php.html "http://10.0.1.2:8081/${p}?lang=$l" \
      && cmp -s /tmp/php.html "public/i18n/$l/${p}index.html" \
      || { bad=$((bad + 1)); echo "different: /${p}?lang=$l"; }
  done
done; echo "$n pages compared, $bad different"
```

Expect `54 pages compared, 0 different`. If only front pages (`/?lang=..`)
differ, someone downloaded in between and the PHP page counted it: run the
`su -m iptv ...` line from step 6 again, then this. Anything else: stop here -
nothing is switched yet - and send the output.

## 8. Switch nginx (host)

```sh
C=/usr/local/bastille/jails/pontusweb/root/usr/local/etc/nginx/conf.d/iptv.dope.rs.conf
grep -l 'server_name iptv.dope.rs' "${C%/*}"/*.conf   # must print $C
cp "$C" /root/iptv.dope.rs.conf.php
cp /root/iptv-static/deploy/content-iptv.dope.rs.conf "$C"
bastille cmd pontusweb nginx -t
bastille cmd pontusweb service nginx reload
```

If `nginx -t` complains, `cp /root/iptv.dope.rs.conf.php "$C"` puts the old
file back - nginx was never reloaded with the new one.

## 9. The cron line (jail)

Every 15 minutes, as before, as `iptv`:

```sh
echo '0,15,30,45 * * * * iptv /bin/sh /usr/local/www/iptv/sync-releases.sh >> /var/log/iptv-sync.log 2>&1' >> /etc/crontab
grep -n sync-releases /etc/crontab
```

## 10. Log rotation (jail)

nginx writes `/dl.php`, and only that, to a log of its own; the sync counts
downloads in it. `P` keeps the newest rotation (`.0`) uncompressed, so the
sync can finish reading the file it was rotated into. 30 is SIGUSR1: nginx
reopens its logs.

```sh
mkdir -p /usr/local/etc/newsyslog.conf.d
cat > /usr/local/etc/newsyslog.conf.d/iptv.dope.rs.conf <<'EOF'
# logfilename                                [owner:group]  mode count size when flags [/pid_file] [sig]
/var/log/nginx/iptv.dope.rs-downloads.log                   644  10    1024 *    JP    /var/run/nginx.pid 30
/var/log/iptv-sync.log                       iptv:iptv      644  5     1024 *    JN
EOF
newsyslog -nv 2>&1 | grep iptv          # a dry run: both files listed, nothing rotated
```

## 11. The PHP files out (jail)

```sh
cd /usr/local/www/iptv
find public -name '*.php'               # look first
find public -name '*.php' -delete
rmdir public/lang public/help public/trakt/callback public/trakt
rm -f sync-releases.php .sync.lock
```

## 12. Check

From anywhere:

```sh
curl -sSI https://iptv.dope.rs/ | head -1              # HTTP/2 200
curl -sSI https://iptv.dope.rs/finns-inte | head -1    # HTTP/2 404
```

And with php-fpm stopped - Nextcloud is down for that minute (host):

```sh
bastille cmd pontusweb service php_fpm stop
curl -sSI https://iptv.dope.rs/ | head -1              # still HTTP/2 200
curl -sSI https://iptv.dope.rs/help/ | head -1         # HTTP/2 200
curl -sSI https://iptv.dope.rs/finns-inte | head -1    # still HTTP/2 404
bastille cmd pontusweb service php_fpm start
```

The rest of what PHP did:

```sh
curl -sSI 'https://iptv.dope.rs/?lang=sv' | grep -i -E '^(set-cookie|content-security-policy):'
#   set-cookie: lang=sv; Max-Age=31536000; path=/
#   content-security-policy: default-src 'self'; ...      (the TLS proxy's, unchanged)
curl -s -H 'Accept-Language: de-DE,de;q=0.9' https://iptv.dope.rs/ | grep -o '<html lang="[a-z]*"'
#   <html lang="de"
curl -sSI 'https://iptv.dope.rs/trakt/callback/?code=x&state=y' | grep -i '^location'
#   location: http://127.0.0.1:41794/callback?code=x&state=y
curl -sSI 'https://iptv.dope.rs/dl.php?f=SHA256SUMS' | grep -i '^location'
#   location: /files/SHA256SUMS                           (HEAD: not counted)
```

After the next quarter-hour (jail): `tail -n 3 /var/log/iptv-sync.log` ends
with `[sync] pages: N of 82 updated`, and once someone has downloaded,
`counted N download(s)`.

## What is different

Nothing a visitor sees. On the wire:

- Pages - and the download counts on them - change once per sync, every 15
  minutes, not per request.
- Redirects say `Content-Type: text/html` without `; charset=utf-8`.
- Trakt's `error_description` goes on to the app as Trakt sent it (`+` for a
  space rather than `%20`); the app reads both the same.
- `/i18n.php`, `/nav.php`, `/lang/xx.php` and `/sync-releases.php` answer
  404 (before: an empty page, an error, 403).
- The sitemap dates the help page by the deploy day: its files are new.
- A release-notes paragraph with certain letters in it (Å, ą, х, …) was left
  out by the PHP; it is shown now.

## Rollback

Before step 11 (host, then jail):

```sh
C=/usr/local/bastille/jails/pontusweb/root/usr/local/etc/nginx/conf.d/iptv.dope.rs.conf
cp /root/iptv.dope.rs.conf.php "$C"
bastille cmd pontusweb nginx -t && bastille cmd pontusweb service nginx reload
```

```sh
chown -R www:www /usr/local/www/iptv
sed -i '' -e '/sync-releases\.sh/d' -e '/sync-releases\.php/s/^#*//' /etc/crontab
```

After step 11 the PHP files are needed as well - they are in the last PHP
commit (host), then the two blocks above:

```sh
mkdir /root/iptv-php
fetch -qo - https://github.com/slimture/dopeIPTV/archive/dd1658ebfa2d369f8d897307699114cf398452bd.tar.gz \
    | tar -xf - -C /root/iptv-php --strip-components 1
W=/usr/local/bastille/jails/pontusweb/root/usr/local/www/iptv
cp -R /root/iptv-php/public/. "$W/public/"
cp /root/iptv-php/sync-releases.php "$W/"
```
