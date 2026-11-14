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

Every 15 minutes, as before, as `iptv` - and in between only the newest
test build (`nightly`: one call to GitHub, writes `nightly.json`), so the
app's Test builds channel has a new one within five minutes:

```sh
echo '0,15,30,45 * * * * iptv /bin/sh /usr/local/www/iptv/sync-releases.sh >> /var/log/iptv-sync.log 2>&1' >> /etc/crontab
echo '5,10,20,25,35,40,50,55 * * * * iptv /bin/sh /usr/local/www/iptv/sync-releases.sh nightly >> /var/log/iptv-sync.log 2>&1' >> /etc/crontab
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
tail -n 1 /var/log/iptv-donate.log   # [donate] listening on unix:/var/run/iptv_donate/donate.sock, 41 currencies (eur by default), configured
python3 -I /usr/local/www/iptv/donate/donate.py --check   # 200 {"publishableKey": "pk_...", ...}
```

The key goes in with `cat` rather than `echo` so it never lands in the
shell's history. Every other setting (the receipt's "Support dopeIPTV",
which currencies are offered) is listed at the top of `donate.py`; change
the `.conf` and `service iptv_donate restart`.

Donors pay in their own currency - 41 of them: those of the site's
languages and the world's largest. The dialog picks it from where the
device says it is (kronor in Sweden, rupees in India, yuan in China, euro
in the euro countries...) and shows round amounts in it - 30/50/100/250 kr,
3/5/10/25 euro; a menu in the dialog's head changes it on either step. The
table is `CURRENCIES` in `donate.py` - look at the rows of currencies that
lose value fast (the lira) now and then; `DONATE_CURRENCY` is only the
fallback when the page cannot tell. Not the rouble or the rial: Stripe
does not take payments from Russia or Iran. A payment in another currency
than the account's own is converted by Stripe, for its conversion fee.

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

`https://iptv.dope.rs/admin/` shows visitors (unique over any span),
page views, time per visit, how visitors found the site, countries,
languages, browsers and versions, systems, devices and screens, downloads
and donations - per day, week, month and calendar year - and, under
*Visitors*, every visit of the last 13 months one by one: when, from where,
which pages and how long, downloads, the donate dialog, and every other
visit by the same visitor. The log sorts (newest, oldest, most pages,
longest), groups (a year by month, a month by day, or by country, system
and the rest, each group folded with its own sums) and filters. *Analysis*
takes the same visits: any breakdown, split by a second data point, with a
measure (visits, visitors, share that downloaded, pages and time per visit,
came back, opened donate); filters that narrow every part of the tab;
patterns in plain sentences; visits over time, by weekday and hour, how much
they read, systems against the files downloaded, browser language against
the page's, and what visitors do on the pages. Any group there opens one
visit at a time in the log. The tab and what it shows are kept in the
address, so a reload stays where it was.

How it counts, so the numbers are honest:

- A page view counts when the page has opened in a real browser: the page
  tells `/hit` itself (`public/nav.js`), with no cookie and no id, and
  again with how long it was looked at. Robots that fetch pages without
  running them, link previews and prerendered pages never do. Every page
  request is counted too, robots and all, so the page shows how much was
  left out.
- A visitor is a hash of address and browser with a secret salt kept on the
  server; no address is written down. The hashes are kept 25 months (unique
  and returning visitors, a year against the year before). Single visits
  are kept 400 days, thirteen months (`STATS_VISITS_DAYS`; until
  November 2026 it was 30); then they are made anonymous -
  no id, no time of day, no versions or screen, only the day, country,
  browser and system, device, how the visitor came, pages, time and whether
  there was a download or the donate dialog - and kept for good, as are the
  counts per day. Countries come from DB-IP's free database
  (CC BY 4.0), fetched once a month by the quarter-hour run and looked up
  on the server - nothing is sent anywhere.
- What visitors do on a page (`public/events.js`, since November 2026):
  which help questions they open, what they search the help for and how
  many answers it found, how far down the front page they get, which
  install steps they open, a language switched, a link to another site
  followed (its host only). Each once a page, through the same `/hit`, sent
  by nothing in an automated browser; the service keeps only what
  `donate/stats_events.py` lists and drops anything else. They live on the
  visit (`acts`) and go with it after 13 months - the anonymous visit keeps
  none of them.
- Updates the app fetches for itself (from 1.4.5): the app asks
  `releases.json` for the newest version and fetches the file through
  `/dl.php` as `dopeIPTV/<version> (<system>; <cpu>)`. `stats_collect.py`
  counts those as updates, apart from downloads - per system, per file
  and per version updated from, once per address and file a day - and
  `sync-releases.sh` leaves them out of the public download counter.
  `releases.json` lists each file's size and SHA-256, which the app checks.
  The admin page splits them into release and test build (a file with a
  four-part version), keeps one from your own network apart (shown, not
  counted), and the Visitors tab shows the newest few as they come - from
  the last 30 the collector keeps in `state.json` (time, file, version
  from, country; no address).
  Test builds the same way: `sync-releases.sh` writes the newest one to
  `nightly.json` (linked from nowhere), and `/dl.php` counts a test
  build's file and sends it on to its pre-release on GitHub - nothing of
  it is mirrored (the `$iptv_dl_nightly` map in the content server's
  file).
- The admin page's Builds tab lists the release the site offers and the
  ten newest test builds (donate.py asks GitHub's releases API, at most
  every two minutes), each file with a download and a link to copy for a
  tester - through `/dl.php`, so a tester's download counts. A new file
  of the page (`builds.js`): `service iptv_donate restart` after deploying.
- Signed releases (from 1.4.5): `releases.json` and `nightly.json` carry the
  release's `SHA256SUMS` and its signature by dopeIPTV's key
  (`sha256sums`, `sha256sums_sig`), fetched from GitHub, and the app
  installs an update only with that signature. The site moves to a new
  release only once it is signed (the completeness gate says "signed
  checksums" until then).
- Sites that linked here, and the exact page when the linking site sends
  it (many send only their address - Google, Facebook and X always): host
  and path, never the query or fragment, which can carry what points to a
  person; never for a search engine. Kept per visit (`ref_page`) and per
  day (`ref_pages`, the top 200 like the sites).
- Your own visits are left out: a browser that has opened `/admin/` gets a
  cookie nginx does not log (pages, downloads, `/hit`), addresses that are
  not public (your own network) never count, and `STATS_IGNORE` in
  `/usr/local/etc/iptv-donate.conf` lists more - addresses, networks or a
  dynamic-DNS name: `STATS_IGNORE=203.0.113.5, home.example.net`.
- The numbers are brought up to date whenever the admin page is opened,
  by cron every quarter hour, and - while its Overview or Visitors tab is
  on screen - every 15 seconds: the downloads by period, today's visitors,
  who was on the site in the last five minutes and the newest download
  (`/admin/live`). When today's visits change, the tab on screen fetches
  its view again by itself - the overview, the visitor log of a period
  with today in it (open visits stay open, new ones light up), the
  Analysis tab - so the page never needs reloading.

The visits of the last 13 months are personal data under the GDPR even
without an address (the id can be worked out again from an address); the
anonymous ones and the counts are not. The site should say what is counted
and why (a short privacy note).

**Signing in.** `/admin/` is for its admins only. Each signs in with a
passkey (Face ID, Touch ID, a phone or a security key; kept in iCloud
Keychain or a password manager and ready on the admin's other devices), or
with a name and password - an ordinary form a password manager fills - and,
if they turn it on, a six-digit code from an authenticator app. A new admin
comes in through a one-time invite link (valid 7 days) another admin makes
on the page's *Account* tab, where each admin also adds or removes passkeys,
sets a password, turns codes on or off, removes another admin, and signs out
here or on every device. The admins live in
`/usr/local/www/iptv/stats/admin-users.json` (mode 600): password hashes
(scrypt), passkeys' public keys, authenticator secrets, invites as hashes.
Ten failed tries in 15 minutes hold back that address or name for a
quarter of an hour; nginx counts sign-in requests too. Each refused try is
a line in `/var/log/iptv-donate.log` saying why (no admin by that name, a
wrong password, another origin), never what was typed. The name field
takes the sign-in name or the name shown, so "admin" and "Pontus" are the
same admin. Any `/admin/` address opened without a session goes to
`/admin/login`.

**From the old password to the new sign-in** (once): put the files in
place and restart the service (below), open `https://dopeiptv.org/admin/`
with the old password, and on the *Account* tab make an invite for
yourself; open it on the device that should hold the passkey and create
one. Then the new content file, which drops nginx's password prompt (jail):

```sh
C=/usr/local/etc/nginx/conf.d/iptv.dope.rs.conf
cp "$C" "$C.before-signin"
cp ~/dopeiptv/deploy/content-iptv.dope.rs.conf "$C"
nginx -t && service nginx reload
rm -f /usr/local/etc/nginx/iptv-admin.htpasswd
```

**A reload that did nothing.** `nginx -t` can pass and the reload still
fail: the running nginx then keeps its old configuration, says nothing on
the console, and writes the reason only to its error log. A `limit_req_zone`
whose key changes does this - nginx refuses to give a zone it holds a new
key on a reload - and it kept this file's changes from November 10 to 12
out of the running server (the admin page still asked for the deleted
password file). After a reload, check that the workers are new and the log
is quiet:

```sh
ps -axo pid,lstart,command | grep "[n]ginx: worker"
grep -n "emerg" /var/log/nginx/error.log | tail -3
```

Workers older than the reload mean it failed; `service nginx restart`
starts afresh (every site in the jail is gone for a second, and downloads
in progress break). A zone's key is better changed under a new zone name,
which a reload takes.

**Locked out** (every passkey gone, a password forgotten), from the jail's
shell: `su -m iptv -c 'python3 -I /usr/local/www/iptv/donate/admin_users.py
invite <name>'` prints a new invite link for a new name; `... list` shows
the admins, `... remove <name>` takes one away, `... signout <name>` ends
their sessions.

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
`/usr/local/www/iptv/stats/` - outside the web root.

**nginx (host)** - the content server's file as in step 8, which now has the
`iptv_stats` log for `/` and `/help/` and the `/admin/` location:

```sh
C=/usr/local/bastille/jails/pontusweb/root/usr/local/etc/nginx/conf.d/iptv.dope.rs.conf
cp /root/iptv-static/deploy/content-iptv.dope.rs.conf "$C"
bastille cmd pontusweb nginx -t && bastille cmd pontusweb service nginx reload
```

**Check:** open the site in a private window (no admin cookie there), then
`tail -n 2 /var/log/nginx/iptv.dope.rs-stats.log` (jail) shows the page and
its `/hit` as JSON lines. `https://iptv.dope.rs/admin/` asks for the
password and shows the visit under *Visitors*. `/var/log/iptv-stats.log`
says when the country database was fetched, or why not. The visitors start with the new log. The downloads per day can go
back as far as the server kept its download log, read once on a fresh
start (jail):

```sh
rm -f /usr/local/www/iptv/stats/stats.json /usr/local/www/iptv/stats/state.json
su -m iptv -c '/usr/local/bin/python3 -I /usr/local/www/iptv/donate/stats_collect.py --backfill'
```

A download is a click sent on to the file, from a real browser (robots,
scripts and download checkers are left out), counted once per visitor and
file a day - stricter than the site's public counter (downloads.json),
which counts every click. Once per visitor needs the visitor's address in
/dl.php's log, the `iptv_dl` format in the content server's file; lines
written before it count each click.

## Signed in for good, and told of donations

**Signed in once, signed in for good.** Signed in, a browser holds a
cookie signed by the donate service (`iptv_admin`, 400 days, renewed by
each visit, sent only to `/admin/`, SameSite=Lax so a Home Screen app or a
notification opening the page keeps it). nginx asks the service whether
the cookie is good (`auth_request`, the hidden `/_admin_session`) and lets
the browser in on that alone; anyone else goes to `/admin/login`. Each
device signs in once and keeps its own cookie. The key lives in `/usr/local/www/iptv/stats/admin-session.key`
(made on first use, mode 600); delete it and `service iptv_donate restart`
to sign every browser out.

nginx needs its `auth_request` module (jail):

```sh
nginx -V 2>&1 | tr ' ' '\n' | grep auth_request
```

No line: install a build that has it (`pkg install nginx-full`) before the
new content file, or `nginx -t` refuses it.

**Notifications on your devices.** A new donation comes as a notification
- "New donation: 12.50 EUR", how it was paid and from whom; a click opens
the admin page's donations. It is the browser's own push (Apple's,
Google's, Mozilla's or Microsoft's push service carries it, encrypted for
that device alone): no app, no third party. The service looks at Stripe
every minute and a half; its first look only notes where it is, so a
restart never repeats old donations. It signs the pushes with a key of its
own, made on first use (`stats/push-vapid.pem`), and needs Python's
cryptography package (jail):

```sh
python3 -c 'import cryptography; print("ok")' || pkg install -y "$(python3 -c 'import sys; print(f"py{sys.version_info[0]}{sys.version_info[1]}-cryptography")')"
service iptv_donate restart
```

The jail must reach the push services over HTTPS (web.push.apple.com,
fcm.googleapis.com, updates.push.services.mozilla.com,
*.notify.windows.com), as it reaches api.stripe.com.

Then on each device: open `/admin/`, and in the Donations card press
*Turn on notifications on this device* and allow them. On a Mac in Safari,
Chrome or Firefox; on an iPhone or iPad Apple allows it only from the Home
Screen: Safari, Share, *Add to Home Screen*, open dopeIPTV from there, sign
in once, then turn them on. The card lists the devices that get them and
has *Send a test*; *Turn off* takes this device off.

**A note on the site.** In a browser that has opened `/admin/`, the
site's own pages show new donations in a small note at the foot of the
page; dismissed, or seen on the admin page, they do not come back. The
page knows to ask by a cookie without a secret (`iptv_admin_hint`); the
answer, `/admin/news`, is the signed session's to give, and that one path
has no password in nginx (a page asking for it would meet the prompt).
Every other visitor's page asks nothing.

**Deploy:** the files as usual (`cp -R public/. ...`, `cp -R site donate ...`,
the sync), `service iptv_donate restart`, and the content server's file as
in step 8:

```sh
C=/usr/local/bastille/jails/pontusweb/root/usr/local/etc/nginx/conf.d/iptv.dope.rs.conf
cp /root/iptv-static/deploy/content-iptv.dope.rs.conf "$C"
bastille cmd pontusweb nginx -t && bastille cmd pontusweb service nginx reload
```

## Feedback and error reports from the app

The app's *Send feedback…* and the offer after an error or a crash post to
`/feedback/report` (donate/feedback.py; nothing about the sender is kept).
The admin page shows them under **Feedback**: the numbers for the period,
a row a day, the problems grouped by what went wrong (sortable: most
reported, most recent, most versions, newest; not fixed / all / fixed;
one version), what people wrote, each report in full with *Copy for a bug
fix*, and seen / fixed marks. A new report sends a push to the admins'
devices, like a donation.

1. Copy the files and restart (jail):

       cd ~/dopeiptv; git checkout website; git pull origin website
       cp -R donate /usr/local/www/iptv/
       service iptv_donate restart

2. nginx (host): the new `/feedback/report` and `/feedback/export` blocks
   and the `iptv_feedback` zone are in deploy/content-iptv.dope.rs.conf -
   copy it as before, then `nginx -t && service nginx reload`.

3. Optional - reading the reports from a fixing session: a read-only token
   of its own, not an admin login. In the jail, add a line to
   /usr/local/etc/iptv-donate.conf

       FEEDBACK_READ_TOKEN=<openssl rand -hex 24>

   and restart the service. `GET /feedback/export?days=30` with
   `Authorization: Bearer <token>` then answers the same summary with each
   report's text; without the line it answers 404. Give the token only to
   a place that keeps secrets (a cloud session's environment secret), never
   in a chat or a file in the repository.

## Supporter keys

A donation comes with a supporter key for the app: shown in the donate
dialog when the payment goes through, and behind a link in Stripe's receipt
(the payment's description), so it can be fetched again later. The app
checks a key offline against the public half of a signing key made here.
Without the signing key nothing changes: no key, no link in the receipt.

Make the signing key once, in the jail (the service runs as iptv):

```sh
cd ~/dopeiptv && python3 donate/supporter.py --new-key
chown iptv:iptv /usr/local/etc/iptv-donate-supporter.key
```

It prints one line of 64 hex characters: the PUBLIC half, safe to share -
it goes into the app (dopeiptv/core/supporter.py, PUBLIC_KEY_HEX). The file
itself is the secret: it stays in the jail, mode 600, and is never sent
anywhere. `--new-key` never overwrites an existing file; `python3
donate/supporter.py --public` prints the public half again. Lose the file
and every key handed out stops being reissuable (the ones people have keep
working in the app); make a new one only together with a new app release.

Stripe: the restricted key needs "PaymentIntents: Write" (which reads as
well); the receipt link finds its payment with Stripe's search.

The admin page makes a key again for a payment (its pi_... id from
Stripe's Dashboard - the same key the donor got) or by hand for someone who
gave another way; it also counts the keys handed out. Nothing about the
donor is kept: supporters.jsonl beside the statistics has an id and a time.

## Answers to feedback

The app may keep a random code for a report and send only its SHA-256; an
admin answers on the Feedback tab - one report, or a whole problem (all
who reported it, and anyone who does later; "Fixed in 1.5.1" also marks it
fixed) - with fixed answers the app shows in its own language and words of
one's own. The app asks POST /feedback/replies with its hashes now and
then for 30 days. Nothing names anyone; feedback-replies.json beside the
statistics. Deploy: `donate/` and `service iptv_donate restart`, and the
content server's file for the new `/feedback/replies` block (jail):

```sh
cp ~/dopeiptv/deploy/content-iptv.dope.rs.conf /usr/local/etc/nginx/conf.d/iptv.dope.rs.conf
nginx -t && service nginx reload
```

## Settings sync for supporters

A supporter's app can keep favorites, settings, playlists and watch history
the same on several computers (the app's Settings, General, "Sync between
computers"). The app encrypts all of it with a key made from a random code
that only the person's computers know; the service (donate/sync.py, routes
`/sync/<slot>` in donate.py) keeps that ciphertext under a slot - another
hash of the code - and cannot read it. Every request carries the supporter
key, which the service checks against the signing key (the same one as
"Supporter keys" above; without it /sync/ answers 503) and forgets: it is
never stored, logged or tied to a slot, and nginx does not log /sync/.
Kept: `stats/sync/<slot>.json` (mode 600) - the ciphertext, a version and
when it was written; at most 2 MB a slot. A slot nobody has read or written
for 400 days is removed by the service once a day
(`python3 donate/sync.py --cleanup` does the same by hand).

1. The files and the service (jail):

       cd ~/dopeiptv; git checkout website; git pull origin website
       cp -R donate /usr/local/www/iptv/
       service iptv_donate restart

2. The content server (jail): the new `iptv_sync` zone and the
   `location ^~ /sync/` block are in deploy/content-iptv.dope.rs.conf:

       cp ~/dopeiptv/deploy/content-iptv.dope.rs.conf /usr/local/etc/nginx/conf.d/iptv.dope.rs.conf
       nginx -t && service nginx reload

3. The TLS-front proxy (`bastille console webproxy`): its own
   `location ^~ /sync/` block, now in deploy/proxy-dopeiptv.org.conf -
   without it nginx's 1 MB default refuses a large sync. Copy the block
   into /usr/local/etc/nginx/conf.d/dopeiptv.org.conf (CONTENT_HOST as in
   the file's other blocks), then `nginx -t && service nginx reload`.

**Check** (jail): `python3 -m pytest donate/test_sync.py` or
`python3 donate/test_sync.py` - all pass; then
`curl -s -o /dev/null -w '%{http_code}\n' https://dopeiptv.org/sync/$(printf 'ab%.0s' $(seq 32))`
answers `401` (no supporter key), not 404 or 502.

## Feedback from the site's footer

The footer's "Feedback" (nav.js, words in site/lang `fbs_*`, reusing the
app's) sends to the same /feedback/report as the app, marked
`"from": "site"`: the admin page's Feedback shows it as from the website.
Only from the site's own pages (Origin), a field people never see must be
empty (a robot filling it is thanked and nothing kept), no sending in the
first three seconds, a minute between two from one browser - and the
service's own limits on top. Deploy: the site files and `donate/` as usual,
`service iptv_donate restart`.

## Moving to dopeiptv.org

The site's address is dopeiptv.org; iptv.dope.rs sends every request on to
the same path there, permanently. Keep the old name and its certificate
for good: older versions of the app, links out on the web and search
engines still use it. The content server's file keeps its name
(`content-iptv.dope.rs.conf`) and so do the logs.

**1. DNS (registrar).** `dopeiptv.org` and `www.dopeiptv.org`: the same A
(and AAAA, if it has one) as `iptv.dope.rs`.

The TLS-front proxy is a jail of its own, `webproxy` (`bastille console
webproxy`); its files below are in that jail's /usr/local/etc/nginx/.

**2. A certificate (the TLS-front proxy).** Before the site moves,
plain http:// on the new names only has to answer certbot. Put this in
`/usr/local/etc/nginx/conf.d/dopeiptv.org.conf`:

```nginx
server {
    listen 0.0.0.0:80;
    server_name dopeiptv.org www.dopeiptv.org;
    location /.well-known/acme-challenge/ { root /usr/local/www/letsencrypt; }
    location / { return 302 https://iptv.dope.rs$request_uri; }
}
```

```sh
nginx -t && service nginx reload
certbot certonly --webroot -w /usr/local/www/letsencrypt -d dopeiptv.org -d www.dopeiptv.org
```

**3. The site (jail).** The usual deploy - the pages now name dopeiptv.org
as their address - then the content server's file (`server_name` gains
dopeiptv.org):

```sh
diff /usr/local/etc/nginx/conf.d/iptv.dope.rs.conf ~/dopeiptv/deploy/content-iptv.dope.rs.conf
cp /usr/local/etc/nginx/conf.d/iptv.dope.rs.conf /root/iptv.dope.rs.conf.bak
cp ~/dopeiptv/deploy/content-iptv.dope.rs.conf /usr/local/etc/nginx/conf.d/iptv.dope.rs.conf
nginx -t && service nginx reload
```

In `/usr/local/etc/iptv-donate.conf`, `DONATE_ORIGIN`, `DONATE_RETURN_URL`
and `SITE_HOST` - if they are set there at all - must name dopeiptv.org
(or go: the defaults are the new ones), then `service iptv_donate restart`.

**4. The switch (the TLS-front proxy).** `deploy/proxy-dopeiptv.org.conf`
replaces the file from step 2: the proxy's own iptv.dope.rs file with the
new name and certificate, plus www and plain http:// sent on. The
`"dopeiptv.org"` row in `/usr/local/etc/nginx/csp-map.conf` is a copy of
the `"iptv.dope.rs"` one. Then the old file's server sends everything on
(`deploy/proxy-iptv.dope.rs.conf`; its certificate lines stay). Reload
after each with `nginx -t && service nginx reload`.

**5. Outside the server.**
- Stripe, Settings -> Payment method domains: add dopeiptv.org (Apple Pay
  is shown only on a registered domain).
- Trakt, the API app's settings: add `https://dopeiptv.org/trakt/callback`
  as a redirect URI and keep the old one - released apps still send it.
- The admin page: sign in again at dopeiptv.org/admin/ and turn push
  notifications on again; both belong to the old address.
- Search Console: add dopeiptv.org (a Domain property, verified by a DNS
  TXT record), submit `https://dopeiptv.org/sitemap.xml`, then in the
  iptv.dope.rs property Settings -> Change of address -> dopeiptv.org.
  Bing Webmaster Tools has the same (Site Move).
- GitHub's About, Wikidata, AlternativeTo, awesome-iptv, solecraft.se.

**Check:** `curl -sI https://iptv.dope.rs/help/?lang=sv` answers `301` with
`location: https://dopeiptv.org/help/?lang=sv`; the same for http://, for
`/dl.php?f=...` and for `/trakt/callback?code=x`; https://dopeiptv.org/ shows
the page with a `canonical` of https://dopeiptv.org/; a donation goes
through, with Apple Pay on an iPhone.
