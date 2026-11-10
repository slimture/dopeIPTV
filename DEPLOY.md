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

## Donations

The donate dialog (`public/nav.js`) takes the payment on the page itself,
as solequiz.com does: Apple Pay or Google Pay where the visitor has it,
otherwise Stripe's card form inside Stripe's own frames. Stripe needs a
server holding the secret key for that, which is `donate/donate.py`: Python's
standard library only, on a Unix socket in the jail
(`/var/run/iptv_donate/donate.sock`), reached through `/donate/` in the content
server's nginx file. It never sees a card number, and with no network port
nothing outside the machine can reach it.

Until it runs, the dialog sends visitors to Stripe's own donate page (the
Payment Link at the top of `site/render.jq`), so the steps below can come in
any order and the site never shows a broken button.

**Stripe Dashboard** (start in test mode, the toggle top right, and repeat
with live keys once a test payment has gone through):

1. Developers → API keys → **Create secret key** → *Customize permissions*
   (no template), named e.g. `dopeIPTV donate`, with **PaymentIntents:
   Write** and nothing else - the card form and the wallets both pay a
   PaymentIntent. Copy the `rk_...` key.
2. The **publishable key** (`pk_...`) is on the same page.
3. Settings → Payments → **Payment method domains** → Add `iptv.dope.rs`.
   Stripe reads `/.well-known/apple-developer-merchantid-domain-association`
   from the site (deployed with `public/`) and Apple Pay turns "Verified".
   Google Pay needs nothing more.
4. What the card form offers is a *payment method configuration*: Stripe no
   longer lets a PaymentIntent list its own methods. The account's default
   one is shared with every site on the account, so make one for dopeIPTV:
   Settings → Payments → Payment methods, the configuration menu at the
   top → create one (e.g. "dopeIPTV"), turn on Cards, Apple Pay, Google
   Pay and whatever else you want, and put its id in the service's
   config as `DONATE_PMC=pmc_...` (`service iptv_donate restart`). Without
   it the form shows the default set. Settings → Customer emails → **Successful
   payments** on, so every donor gets a receipt.

**The service (jail):**

```sh
bastille console pontusweb
pkg install -y python3
python3 --version                    # 3.9 or later
cd ~/dopeiptv && git pull origin website
cp -R donate /usr/local/www/iptv/
install -m 600 -o iptv -g iptv /dev/null /usr/local/etc/iptv-donate.key
cat > /usr/local/etc/iptv-donate.key # paste the rk_... key, Enter, Ctrl-D
cat > /usr/local/etc/iptv-donate.conf <<'CONF'
STRIPE_PUBLISHABLE_KEY=pk_...
DONATE_CURRENCY=eur
DONATE_COUNTRY=SE
CONF
install -m 555 deploy/iptv_donate /usr/local/etc/rc.d/iptv_donate
install -m 640 -o iptv -g iptv /dev/null /var/log/iptv-donate.log
sysrc iptv_donate_enable=YES
service iptv_donate start
tail -n 1 /var/log/iptv-donate.log   # [donate] listening on unix:/var/run/iptv_donate/donate.sock, 19 currencies (eur by default), configured
python3 -I /usr/local/www/iptv/donate/donate.py --check   # 200 {"publishableKey": "pk_...", ...}
```

The key goes in with `cat` rather than `echo` so it never lands in the
shell's history. Every other setting (the receipt's "Support dopeIPTV",
which currencies are offered) is listed at the top of `donate.py`; change
the `.conf` and `service iptv_donate restart`.

Donors pay in their own currency: the dialog picks it from where the device
says it is (kronor in Sweden, pounds in the UK, dollars in the US, euro in
the euro countries...) and shows round amounts in it - 30/50/100/250 kr,
3/5/10/25 euro. The table is `CURRENCIES` in `donate.py`;
`DONATE_CURRENCY` is only the fallback when the page cannot tell. A payment
in another currency than the account's own is converted by Stripe at
payout, for its usual conversion fee.

A Unix socket rather than 127.0.0.1: in a Bastille jail a connection to
127.0.0.1 can hang (the jail has no loopback of its own), and a socket file
needs no network at all. The rc script makes `/var/run/iptv_donate` for it
(and the pidfile); the socket is left writable so nginx's worker can connect.

Log rotation, next to the two lines from step 10 (SIGHUP makes daemon(8)
reopen the log):

```sh
echo '/var/log/iptv-donate.log                     iptv:iptv      640  5     1024 *    JC    /var/run/iptv_donate/iptv_donate.pid' \
    >> /usr/local/etc/newsyslog.conf.d/iptv.dope.rs.conf
newsyslog -nv 2>&1 | grep donate
```

**nginx (host)** - the content server's file as in step 8, which now has the
`/donate/` location and the rate limit in front of it:

```sh
C=/usr/local/bastille/jails/pontusweb/root/usr/local/etc/nginx/conf.d/iptv.dope.rs.conf
cp /root/iptv-static/deploy/content-iptv.dope.rs.conf "$C"
bastille cmd pontusweb nginx -t && bastille cmd pontusweb service nginx reload
```

and the site's row in the TLS-front proxy's CSP map
(`deploy/csp.snippet.txt` has the line and why each Stripe address is
there), then `nginx -t && service nginx reload` on the host.

**Check:** open the site, Donate. The amounts appear; Safari with a card in
Wallet shows "Donate with Apple Pay", Chrome with a saved card "Donate with
Google Pay", everything else goes straight to the card form. In test mode
the card `4242 4242 4242 4242`, any future date and any CVC pays; the
payment shows in the Dashboard under Payments, with "iptv.dope.rs donate
dialog" in its metadata.

## Statistics and the admin page

`https://iptv.dope.rs/admin/` shows visitors, page views, downloads and
donations, per day, week or month and per calendar year. Nothing is added
to the site for it - no script, no cookie: nginx writes one line per page
view to a log of its own, `donate/stats_collect.py` turns the new lines into
counts every quarter hour (a visitor is a hash of address and browser with a
salt that is thrown away after the day, so no address is ever kept), and the
donate service serves the page behind a password. Donations are read from
Stripe with the key the service already has.

**The page's password (jail):**

```sh
bastille console pontusweb
printf 'admin:%s\n' "$(openssl passwd -6)" > /usr/local/etc/nginx/iptv-admin.htpasswd
chown root:www /usr/local/etc/nginx/iptv-admin.htpasswd
chmod 640 /usr/local/etc/nginx/iptv-admin.htpasswd
```

`openssl passwd` asks for the password twice and writes only its hash. More
users: one `name:hash` line each. The service also takes `ADMIN_USERS=admin`
in `/usr/local/etc/iptv-donate.conf` to allow only the names listed.

**The files and the log (jail):**

```sh
cd ~/dopeiptv && git pull origin website
cp -R donate /usr/local/www/iptv/
install -d -o iptv -g iptv -m 750 /usr/local/www/iptv/stats
install -m 640 -o root -g iptv /dev/null /var/log/nginx/iptv.dope.rs-stats.log
install -m 640 -o iptv -g iptv /dev/null /var/log/iptv-stats.log
service iptv_donate restart
echo '5,20,35,50 * * * * iptv /usr/local/bin/python3 -I /usr/local/www/iptv/donate/stats_collect.py >> /var/log/iptv-stats.log 2>&1' >> /etc/crontab
echo '/var/log/nginx/iptv.dope.rs-stats.log        root:iptv      640  7     *    @T00 JP    /var/run/nginx.pid 30' \
    >> /usr/local/etc/newsyslog.conf.d/iptv.dope.rs.conf
newsyslog -nv 2>&1 | grep stats
```

The log is made before nginx opens it, so it belongs to the `iptv` group
(`stats_collect.py` runs as `iptv`); newsyslog turns it over at midnight,
keeps a week, and leaves the newest old one uncompressed so a line written
just before midnight is still read. The counts live in
`/usr/local/www/iptv/stats/` - outside the web root - ten years of days.

**nginx (host)** - the content server's file as in step 8, which now has the
`iptv_stats` log for `/` and `/help/` and the `/admin/` location:

```sh
C=/usr/local/bastille/jails/pontusweb/root/usr/local/etc/nginx/conf.d/iptv.dope.rs.conf
cp /root/iptv-static/deploy/content-iptv.dope.rs.conf "$C"
bastille cmd pontusweb nginx -t && bastille cmd pontusweb service nginx reload
```

**Check:** open the site once, then `tail -n 1
/var/log/nginx/iptv.dope.rs-stats.log` (jail) shows your visit as one JSON
line. After the next quarter-hour `/usr/local/www/iptv/stats/stats.json`
exists and `https://iptv.dope.rs/admin/` asks for the password and shows the
day. The visitors start with the new log. The downloads per day can go
back as far as the server kept its download log, read once on a fresh
start (jail):

```sh
rm -f /usr/local/www/iptv/stats/stats.json /usr/local/www/iptv/stats/state.json
su -m iptv -c '/usr/local/bin/python3 -I /usr/local/www/iptv/donate/stats_collect.py --backfill'
```

The downloads are counted as the site's own counter (downloads.json)
counts them: a click sent on to the file, robots left out.
