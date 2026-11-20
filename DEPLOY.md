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
install -m 600 -o iptv -g iptv /dev/null /var/log/iptv-sync.log
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
downloads in it. It holds IP addresses, so it is turned over at midnight and
only the day before is kept - two days at the most, as the statistics' log
(the privacy policy says so): the sync reads it every 15 minutes and keeps
the counts itself, so nothing older is needed. `P` keeps the newest rotation (`.0`) uncompressed, so the sync can
finish reading the file it was rotated into. 30 is SIGUSR1: nginx reopens
its logs.

```sh
mkdir -p /usr/local/etc/newsyslog.conf.d
cat > /usr/local/etc/newsyslog.conf.d/iptv.dope.rs.conf <<'EOF'
# logfilename                                [owner:group]  mode count size when flags [/pid_file] [sig]
/var/log/nginx/iptv.dope.rs-downloads.log    root:iptv      640  1     *    @T00 JP    /var/run/nginx.pid 30
/var/log/iptv-sync.log                       iptv:iptv      600  5     1024 *    JN
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
#   set-cookie: lang=sv; Max-Age=31536000; path=/; Secure; SameSite=Lax
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

## Support (a tip, monthly support)

Everything is on dopeIPTV's own Stripe account (Solecraft AB), and always
called a **tip** or **monthly support** - never a donation: Solecraft AB is a
company, not a charity, and Stripe reviews the words.

- **A tip** (any amount, once) is given in the **tip dialog** on the page
  (`public/nav.js`): Apple Pay or Google Pay where the visitor has it,
  otherwise Stripe's card form inside Stripe's own frames. The header's link
  and heart, the front page's band and the support page's tip button open
  it. Stripe needs a server holding the secret key for that, which is
  `donate/donate.py`: Python's standard library only, on a Unix socket in
  the jail (`/var/run/iptv_donate/donate.sock`), reached through `/donate/`
  in the content server's nginx file. It never sees a card number, and with
  no network port nothing outside the machine can reach it.
- **Monthly support** (a fixed price) is a **Stripe Payment Link**: Stripe's
  own payment page, reached by a plain link on the support page
  (`/support/`, `site/legal/<lang>/support.html` with the ways to pay from
  `site/render.jq`); the dialog points there too. GitHub Sponsors sits
  beside them.

The site's side is this server's, never git's: `sync-releases.sh` reads
`/usr/local/etc/iptv-site.conf` and renders it into the pages. Until one of
the ways is on, the site invites to nothing - no link or heart in the
header, no band on the front page, no link in the footer - and the support
page says "Coming soon" where the buttons will be. The day they are added,
the next sync (within 15 minutes) shows them everywhere.

```sh
install -m 644 /dev/null /usr/local/etc/iptv-site.conf
cat > /usr/local/etc/iptv-site.conf <<'CONF'
SUPPORT_TIPS=on
SUPPORT_MONTHLY_URL=https://buy.stripe.com/...
SUPPORT_SPONSORS_URL=https://github.com/sponsors/slimture
SUPPORT_TIP_HINT=50 kr
SUPPORT_MONTHLY_PRICE=29 kr
SUPPORT_MONTHLY_PRICES=SEK 29, EUR 2.50, GBP 2.50, USD 3
SUPPORT_PORTAL_URL=https://billing.stripe.com/p/login/...
CONF
su -m iptv -c 'sh /usr/local/www/iptv/sync-releases.sh'
```

`SUPPORT_TIPS=on` turns the tip dialog on - only once the service below
answers, or a click on a tip shows "Something went wrong". Instead of the
dialog a tip can be a Payment Link of its own (`SUPPORT_TIP_URL=https://
buy.stripe.com/...`, used while `SUPPORT_TIPS` is not on). Only `https://`
addresses are taken; a way left out (or empty) is "coming soon". The hint
and the price are only words on the page - the dialog's amounts come from
`donate.py`, the monthly price is set in its Payment Link.

`SUPPORT_PORTAL_URL` is Stripe's customer portal (Settings → Billing →
Customer portal → its login link): "Manage or cancel your monthly support"
under monthly support's button, and in the rules' "Cancelling monthly
support". Without it that link goes to the monthly card.

**The page itself** is `site/templates/support.html`: the two ways at the
top, what a supporter gets (the app's supporter icon, `public/supporter-icon.png`,
drawn by the app's `ui/app_icon.py`), where the money goes, and the rules -
`site/legal/<sv|en>/support.html`, each section a folded question
(`<details class="sp-q">`), who you pay open below them.

**In the visitor's currency.** Monthly support's Stripe price can have an
amount of its own per currency (the price → "Add a price by currency"), and
Stripe's page then charges the one of where the visitor is - the price's
own currency where it has none. `SUPPORT_MONTHLY_PRICES` lists the same
amounts, the price's own currency first (`SEK 29, EUR 2.50, GBP 2.50,
USD 3`; a comma or a point for the decimals); `nav.js` shows the visitor's
by the same rule - the country's currency where the list has it, else the
first - and `SUPPORT_MONTHLY_PRICE` (else the first, "29 SEK") is what the
page says without JavaScript. Keep the two in step: a currency added in
Stripe is added here too. The tip's hint ("50 kr is a good start") follows
the tip dialog: its second amount in the currency it opens in.

**Stripe Dashboard** (start in a sandbox - test mode - and repeat with live
keys once a test payment has gone through):

1. Developers → API keys → **Create restricted key**, named e.g.
   `dopeIPTV`, with **PaymentIntents: Write** (the dialog's card form and
   wallets pay a PaymentIntent; Write reads as well) and **Checkout
   Sessions: Read** (monthly support's key, back from its Payment Link) -
   nothing else. Copy the `rk_...` key.
2. The **publishable key** (`pk_...`) is on the same page.
3. Settings → Payments → **Payment method domains** → Add `dopeiptv.org`.
   Stripe reads `/.well-known/apple-developer-merchantid-domain-association`
   from the site (deployed with `public/`; Stripe's file, the same for every
   account - if the Dashboard offers another, put that one there) and Apple
   Pay turns "Verified". Google Pay needs nothing more.
4. Optional: what the card form offers is a *payment method
   configuration*. The account's default one is fine on an account of
   dopeIPTV's own; for another set make one (Settings → Payments → Payment
   methods, the configuration menu at the top, Cards, Apple Pay, Google
   Pay...) and put its id in the service's config as `DONATE_PMC=pmc_...`.
5. Settings → Business → **Public details**: the statement descriptor
   `DOPEIPTV` (what the bank statement says; the service adds nothing to
   it unless `DONATE_SUFFIX` is set), the support e-mail and the website
   `https://dopeiptv.org`. Settings → **Customer emails** → Successful
   payments on, so every tip gets a receipt; the receipt says "Tip for
   dopeIPTV" (`DONATE_NAME`) and carries the link that shows the supporter
   key again.
6. **Monthly support**: Product catalogue → a product "Monthly support for
   dopeIPTV" with a recurring monthly price, then Payment Links → New, that
   price → After payment → "Don't show confirmation page" →
   `https://dopeiptv.org/support/?paid={CHECKOUT_SESSION_ID}`. Its
   `https://buy.stripe.com/...` address goes in `iptv-site.conf` as
   `SUPPORT_MONTHLY_URL`. Settings → Billing → **Customer portal** on, so a
   supporter can cancel from the link in their e-mails.

**The service (jail)** - the key goes in with `cat` rather than `echo` so it
never lands in the shell's history:

```sh
bastille console pontusweb
cd ~/dopeiptv && git pull origin website
cp -R donate /usr/local/www/iptv/
install -m 600 -o iptv -g iptv /dev/null /usr/local/etc/iptv-donate.key
cat > /usr/local/etc/iptv-donate.key   # paste the rk_... key, Enter, Ctrl-D
cat > /usr/local/etc/iptv-donate.conf <<'CONF'
STRIPE_PUBLISHABLE_KEY=pk_...
DONATE_CURRENCY=sek
DONATE_COUNTRY=SE
CONF
service iptv_donate restart
tail -n 1 /var/log/iptv-donate.log     # ... Stripe set up, tip dialog on (41 currencies, sek by default), supporter keys on ...
python3 -I /usr/local/www/iptv/donate/donate.py --check   # 200 {"stripe": true, "dialog": true, ...}
```

Every other setting (the receipt's name, the return address, which
currencies are offered) is listed at the top of `donate.py`; change the
`.conf` and `service iptv_donate restart`. The visitor pays in their own
currency - 41 of them; the dialog picks it from where the device says it is
and shows round amounts in it (30/50/100/250 kr, 3/5/10/25 euro), and a menu
in its head changes it. The table is `CURRENCIES` in `donate.py`;
`DONATE_CURRENCY` is only the fallback when the page cannot tell. A payment
in another currency than the account's own is converted by Stripe, for its
conversion fee.

**Back from monthly support's Payment Link** the support page asks
`/support/key`, which `donate/donate.py` answers: it asks Stripe for that
Checkout Session and, once it is paid, gives the supporter key made from it
(`donate/supporter.py`) - the same key for the same payment, every time. A
tip's key comes the same way from its PaymentIntent, in the dialog and
through its receipt's link (`/support/#key=...`). Without the Stripe key
the support page still works; the dialog then cannot open, the thank-you
says the key could not be made, and the admin page's Support list stays
empty.

**nginx (host)** - the content server's file as in step 8: `/support/` is a
legal page (`/donations/` sends visitors there), `/donate/` and
`/support/key` reach the service with the rate limit in front, and
`/.well-known/apple-developer-merchantid-domain-association` is served for
Apple Pay:

```sh
C=/usr/local/bastille/jails/pontusweb/root/usr/local/etc/nginx/conf.d/iptv.dope.rs.conf
cp /root/iptv-static/deploy/content-iptv.dope.rs.conf "$C"
bastille cmd pontusweb nginx -t && bastille cmd pontusweb service nginx reload
```

and the site's row in the TLS-front proxy's CSP map
(`deploy/csp.snippet.txt` has the line and why each Stripe address is
there: Stripe.js, its frames, Google Pay's), then `nginx -t && service
nginx reload` on the host.

**Check:** in the sandbox, with its keys (`pk_test_...`, `rk_test_...`) and
a test Payment Link: open the site, the heart. The amounts appear; Safari
with a card in Wallet shows "Tip with Apple Pay", Chrome with a saved card
"Pay with Google Pay", everything else goes to the card form. The card
`4242 4242 4242 4242`, any future date and any CVC pays; the thank-you
shows a supporter key, the payment shows in the Dashboard with
"dopeiptv.org tip dialog" in its metadata, and the admin page lists it.
Then monthly support from the support page, the same way. Then the same
with the live keys and link.

## Statistics and the admin page

`https://iptv.dope.rs/admin/` shows visitors (unique over any span),
page views, time per visit, how visitors found the site, countries,
languages, browsers and versions, systems, devices and screens, downloads
and tips and monthly support - per day, week, month and calendar year - and, under
*Visitors*, every visit of the last 13 months one by one: when, from where,
which pages and how long, downloads, a support link clicked, and every other
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
- A visitor is a keyed hash (HMAC-SHA256) of address and browser; no
  address is written down. The key is the month's own
  (`STATS_DIR/visitor-keys.json`, 0600): a new one each month, and a
  month's key deleted once the month after it has ended - from then on
  nobody can tell an address from its hashes, this server's administrators
  included. A visitor is the same visitor within a month and is told
  returning from the month before at the most; unique visitors over a
  longer span are the months' summed. (Before November 2026 one secret
  was kept for 25 months; the first run of this version deletes it.) The
  hashes go with their month's key: the month is counted once more into
  `uniques-months.json` (different visitors in the month and each week,
  people and robots) and its hashes deleted. Single visits are
  kept 30 days (`STATS_VISITS_DAYS`: a visit in detail - its time to the
  second, versions, screen, languages, the page that linked - can point to
  one person; it was 400 days in November 2026, a `STATS_VISITS_DAYS`
  line in the config file overrides this); then they are made anonymous -
  no id, no time of day, no versions or screen, only the day, country,
  browser and system, device, how the visitor came, pages, time and whether
  there was a download or the donate dialog - and kept for good, as are the
  counts per day; once a month is whole they are made k-anonymous (each
  like at least four others of the month, a rarer one told less field by
  field or left out: `k_anonymous`) - each combination kept is shared by at
  least five visits, which need not be five people. Countries come from DB-IP's free database
  (CC BY 4.0), fetched once a month by the quarter-hour run and looked up
  on the server - nothing is sent anywhere.
- What visitors do on a page (`public/events.js`, since November 2026):
  which help questions they open, what they search the help for and how
  many answers it found, how far down the front page they get, which
  install steps they open, a language switched, a link to another site
  followed (its host only). Each once a page, through the same `/hit`, sent
  by nothing in an automated browser; the service keeps only what
  `donate/stats_events.py` lists and drops anything else. They live on the
  visit (`acts`) and go with it after 30 days - the anonymous visit keeps
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
  the last 30 the collector keeps in `state.json` (the hour, file, version
  from, country; no address). Each is kept at its hour, never the second,
  and for 30 days (`UPDATES_DAYS`); the counts per day stay.
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
  person; never for a search engine. Since the third 1.6.9 review it is
  kept nowhere: its path can name a person (a profile, a shared link), and
  days kept before lose theirs at the next run - only the site counts.
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

`ADMIN_USERS` in `/usr/local/etc/iptv-donate.conf` counts only while
`admin-users.json` has no admin at all, and only for a name it lists:
empty or left out - the default - it is nobody (since November 2026; an
empty or lost admin file used to make an admin of whatever named one).
The first admin then comes from `admin_users.py invite` ("Locked out"
below), never from a header.

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
install -d -o iptv -g iptv -m 700 /usr/local/www/iptv/stats
install -m 640 -o root -g iptv /dev/null /var/log/nginx/iptv.dope.rs-stats.log
install -m 600 -o iptv -g iptv /dev/null /var/log/iptv-stats.log
service iptv_donate restart
echo '5,20,35,50 * * * * iptv /usr/local/bin/python3 -I /usr/local/www/iptv/donate/stats_collect.py >> /var/log/iptv-stats.log 2>&1' >> /etc/crontab
echo '/var/log/nginx/iptv.dope.rs-stats.log        root:iptv      640  1     *    @T00 JP    /var/run/nginx.pid 30' \
    >> /usr/local/etc/newsyslog.conf.d/iptv.dope.rs.conf
newsyslog -nv 2>&1 | grep stats
```

The log is made before nginx opens it, so it belongs to the `iptv` group
(`stats_collect.py` runs as `iptv`); newsyslog turns it over at midnight,
keeps only the day before (two days of addresses at the most), and leaves
that one uncompressed so a line written
just before midnight is still read. The counts live in
`/usr/local/www/iptv/stats/` - outside the web root - and are the `iptv`
user's alone: every file 0600, every folder 0700. The service starts with
umask 077 and narrows anything left wider at each start (reports,
supporters, the counts were 0644 before November 2026, the folders 0750),
and `stats_collect.py` writes its files the same way, so the `iptv` group
reads none of it. Something else that reads them - a backup - runs as
root or as `iptv`.

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

## Signed in for good, and told of support

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

**Notifications on your devices.** A new tip or monthly support comes as a
notification - "New tip: 50.00 SEK" or "New monthly: 30.00 SEK", and from
whom; a click opens the admin page's Support. It is the browser's own push (Apple's,
Google's, Mozilla's or Microsoft's push service carries it, encrypted for
that device alone): no app, no third party. The service looks at Stripe
every minute and a half; its first look only notes where it is, so a
restart never repeats old payments. It signs the pushes with a key of its
own, made on first use (`stats/push-vapid.pem`), and needs Python's
cryptography package (jail):

```sh
python3 -c 'import cryptography; print("ok")' || pkg install -y "$(python3 -c 'import sys; print(f"py{sys.version_info[0]}{sys.version_info[1]}-cryptography")')"
service iptv_donate restart
```

The jail must reach the push services over HTTPS (web.push.apple.com,
fcm.googleapis.com, updates.push.services.mozilla.com,
*.notify.windows.com), as it reaches api.stripe.com.

Then on each device: open `/admin/`, and in the Support card press
*Turn on notifications on this device* and allow them. On a Mac in Safari,
Chrome or Firefox; on an iPhone or iPad Apple allows it only from the Home
Screen: Safari, Share, *Add to Home Screen*, open dopeIPTV from there, sign
in once, then turn them on. The card lists the devices that get them and
has *Send a test*; *Turn off* takes this device off.

**A note on the site.** In a browser that has opened `/admin/`, the
site's own pages show new tips and monthly support in a small note at the foot of the
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
devices, like a tip.

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

A tip or monthly support comes with a supporter key for the app: a tip's
in the tip dialog's thank-you and through the link in its receipt, monthly
support's on the support page when the visitor comes back from Stripe's
payment page (the Checkout Session's id in the address, "Support" above).
The same payment always gives the same key; a lost one is made again on the
admin page from the payment's id. The app checks a key offline against the public
half of a signing key made here. Without the signing key nothing changes:
the thank-you shows no key.

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
well) and "Checkout Sessions: Read"; a tip's receipt link finds its payment
with Stripe's search.

The admin page makes a key again for a payment (a tip's pi_... id or
monthly support's cs_live_... Checkout Session from Stripe's Dashboard -
the same key the supporter got) or by hand for someone who gave another
way; it also counts the keys handed out. Nothing about the supporter is
kept: supporters.jsonl beside the statistics has an id and a time.

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

### A reply to an answer, archive and delete, new feedback by itself

**A reply, still anonymous.** Each answer to one report has a switch, *Let
them reply* - on by default for "Need more details", off for the others,
flipped by hand per answer; never on a group's answer. While the latest
answer to a report has it on, the app (from the version after 1.6.3) shows
a reply box under that answer; the reply goes to the same
`/feedback/report` with `"thread"` = the report's code hash, and is taken
only then - one reply, then closed until the next answer with the switch on
(otherwise 409 `{"error": "closed"}`, nothing kept). The same limits and
scrubbing as feedback, at most 2000 characters, and the same push ("A
reply to your answer"). It is a row of its own in feedback.jsonl, tied to
the report by its id (`thread_of`), never by the hash; the app is told only
whether the latest answer lets them reply (`"open"` in
`/feedback/replies`), never its own words back. On the Feedback tab the
report opens as its conversation - report, answer, reply, answer…, each
with its time and each reply with its own Copy; a reply makes the report
unread again ("Replied" on its row) and brings it to the top. Copy and
Copy all shown give the whole conversation as plain text.

**Archive** (a report, or a problem): out of the lists and the unread
count, under *Archived* (the feedback list's chip, the problems' list) and
back with *Unarchive*; a reply takes an archived report out, unread. Kept
beside the marks in feedback-state.json. **Delete…** asks first and names
what goes: one report with its answers, replies and code hash (the app
then finds nothing for it), or every report of a problem and its answers.
The file is rewritten whole and swapped in, as the daily cleanup's prune
does - and prune now takes a report's replies with it (a thread never
outlives its report's 12 months), and the answers and marks of what it
removes. Both are POSTs for the signed-in admin from the page
itself (`/admin/feedback-archive`, `/admin/feedback-delete`), under
`location ^~ /admin/` like the other admin routes.

**New feedback by itself.** While the admin page is in front it asks every
45 s - `/admin/feedback?rev=…` answers `{"same": true}` without reading a
report when nothing changed (the files' times and sizes) - and draws what
is new in place: the report open stays open with what is typed in it, the
filters and the scroll stay, a new row lights up, the tab's count follows.
It waits while an answer is being typed. Paused while the tab is hidden.

Deploy (jail) - only `donate/` changed, both nginx files stay as they are:

    cd ~/dopeiptv; git checkout website; git pull origin website
    cp -R donate /usr/local/www/iptv/
    service iptv_donate restart

Check: `python3 -m pytest donate` - all pass. Older apps are served as
before (an answer's `"open"` is a field they do not read).

The privacy policy's item on error reports and feedback says the app asks
for answers "for 30 days": the app now counts those 30 days from the
person's latest reply, and a reply carries the same code hash as its
report. Worth a sentence in `site/legal/sv/` and `site/legal/en/privacy.html`
(the Swedish governs) with this deploy.

## Settings sync

Anyone's app can keep favorites, settings, playlists and watch history the
same on several computers (the app's Settings, Sync). The app encrypts all
of it with a key made from a random code that only the person's computers
know; the service (donate/sync.py, routes `/sync/<slot>` in donate.py)
keeps that ciphertext under a slot - another hash of the code - and cannot
read it. A request carries nothing about the person: no account and no key
(an older app still sends its supporter key; it is not read). Nothing is
tied to a slot or logged, and nginx does not log /sync/.
Kept: `stats/sync/<slot>.json` (mode 600) - the ciphertext, a version and
when it was written; at most 2 MB a slot. A slot nobody has read or written
for 180 days (the privacy policy) is removed by the service once a day
(`python3 donate/sync.py --cleanup` does the same by hand, and says how
much the rest take).

Since anyone may store, the service limits it (donate.py, in memory only):
120 requests, 30 writes (PUT, DELETE) and 20 new slots a day per address
(429 past them), and all slots together `sync.MAX_TOTAL` (5 GB) - past it a
new slot gets 507 while the slots there can still be written. Past 80 % the
admin page says so at the top of Overview (Services, Settings sync shows
the numbers) and the devices with notifications on are told, at most once
a day. To make room: raise `MAX_TOTAL` in donate/sync.py and restart the
service.

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
answers `404` (nothing under that slot, and no key asked for) - not 401
(the service from before sync was for everyone) or 502.

## Watch together

The app's "Watch together…" keeps friends' players in step, each playing
from their own source: one starts a room and gets a short code
(`K7PQM-2XDH9`: ten characters, stretched by 600 000 rounds of PBKDF2;
one of eight from 1.6.7 still joins its room), the others join with it. The app makes from the code a room
id and a key; every message - who is in the room, what plays, play, pause
and seek - is encrypted on the sender's computer, and the service
(donate/together.py, routes `/together/<room>` in donate.py) only passes
the blobs on: POST one, GET the ones after a number (waiting up to 25 s
for the next). It cannot read them and does not know whose they are.
In memory only - nothing on disk, nothing logged but a daily count of
rooms; at most 8 in a room, its last 300 messages (128 KB), a room
forgotten after an hour without a request. A restart of the service
forgets every room; the apps start over by themselves. nginx does not
log /together/.

1. The files and the service (jail):

       cd ~/dopeiptv; git checkout website; git pull origin website
       cp -R donate /usr/local/www/iptv/
       service iptv_donate restart

2. The content server (jail): the new `iptv_together` zone and the
   `location ^~ /together/` block are in deploy/content-iptv.dope.rs.conf
   (a read timeout longer than the 25 s a request waits, no buffering):

       cp ~/dopeiptv/deploy/content-iptv.dope.rs.conf /usr/local/etc/nginx/conf.d/iptv.dope.rs.conf
       nginx -t && service nginx reload

3. The TLS-front proxy (`bastille console webproxy`): its own
   `location ^~ /together/` block, now in deploy/proxy-dopeiptv.org.conf
   (not logged, a 50 s read timeout, no buffering). Copy the block into
   /usr/local/etc/nginx/conf.d/dopeiptv.org.conf (CONTENT_HOST as in the
   file's other blocks), then `nginx -t && service nginx reload`.

**Check** (jail): `python3 -m pytest donate/test_together.py` or
`python3 donate/test_together.py` - all pass; then
`curl -s -H 'X-Together-Member: 0123456789abcdef' "https://dopeiptv.org/together/$(printf 'ab%.0s' $(seq 32))?after=0&wait=0"`
answers `{"seq": 0, "now": ..., "n": 1, "msgs": []}`, not 404 or 502.

## Feedback from the site's footer

The footer's "Feedback" (nav.js, words in site/lang `fbs_*`, reusing the
app's) sends to the same /feedback/report as the app, marked
`"from": "site"`: the admin page's Feedback shows it as from the website.
Only from the site's own pages (Origin), a field people never see must be
empty (a robot filling it is thanked and nothing kept), no sending in the
first three seconds, a minute between two from one browser - and the
service's own limits on top. Deploy: the site files and `donate/` as usual,
`service iptv_donate restart`.

## Official builds only, and Copy on the Feedback tab

The app tells the site which build it is in a header: `X-dopeIPTV-Build:
official` (a release or test build from the project's workflows), `source`
(run from a checkout) or `modified` (built from changed code). Watch
together (`/together/`), settings sync (`/sync/`) and feedback / error
reports (`/feedback/report`) answer `modified` with 403 and
`{"error": "unofficial_build", "message": "This is not an official version
of dopeIPTV. This service is only in the official app, from dopeiptv.org."}`,
which the app shows (donate/official.py). No header - every app from before
it - is served as before, and so is a word the service does not know. The
header is the app's own word, not proof.

Only how many were refused is kept, per day and service, in
`stats/refused-builds.json`: no address, room, slot or anything else about
a request. The service's log says the first refusal of a day and the day's
sum. The admin page shows them on Overview under Services, **Refused
(modified builds)**, for the period chosen.

A report kept from now on records its build: official or source, unknown
without the header (older apps, the site's footer; older reports too). The
Feedback tab marks "Source", filters both lists by build and lists "By
build" under "Where it comes from". Every problem and feedback row has
**Copy**: the whole report as plain text - what it is, when, version,
system, build, what the person picked, then the text as sent (the error and
its traceback, the recent log, their own words). Each list has **Copy all
shown** for what its filters show (`/admin/feedback-copy`). On a phone it
copies on the first tap where the browser allows that, else the button
says "Tap again to copy".

The privacy policy lists what a report carries (section on error reports
and feedback): the build kind is new there - add it to `site/legal/sv/` and
`site/legal/en/privacy.html` (the Swedish governs) with this deploy.

Deploy (jail) - only `donate/` changed; both nginx layers pass the header on
as it is, so neither nginx file changes:

    cd ~/dopeiptv; git checkout website; git pull origin website
    cp -R donate /usr/local/www/iptv/
    service iptv_donate restart

**Check** (jail): `python3 -m pytest donate` or
`python3 donate/test_official.py` - all pass; then

    curl -s -H 'X-dopeIPTV-Build: modified' -H 'X-Together-Member: 0123456789abcdef' \
        "https://dopeiptv.org/together/$(printf 'ab%.0s' $(seq 32))?after=0&wait=0"

answers the `unofficial_build` JSON, and the same without the
`X-dopeIPTV-Build` line answers `{"seq": 0, ...}` as before. Overview's
Refused (modified builds) then shows the one refusal the check made.

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
the page with a `canonical` of https://dopeiptv.org/.

## Hardening, November 2026

What a security review of the site found, and what changed (each commit
says it in full). Nothing changes for a visitor, for the app - 1.6.7, 1.6.8
and newer sync, send feedback, watch together, update and tip as before -
or for the admin page.

- **The content server's error log** holds only what stops it (`error_log
  ... crit`), as the proxy's dopeiptv servers already did. Before, nginx
  wrote every request a limit refused there with its whole request line -
  sync slots, watch-together rooms, admin invites, checkout ids - which
  the privacy policy says are never logged anywhere. What is there from
  before is cleaned out once, below.
- **Limits that apply.** nginx runs a location's `return` before its
  `limit_req`, so a limit beside a return was never asked - /hit's never
  was. /dl.php now has one of its own (`iptv_dl`), and both are answered
  from a named location after the limit. A file's name is at most 120
  characters; a name the site does not know (made up in the shape of a
  test build's, which nginx sends on to GitHub) is counted under `other`
  in downloads.json, so the total stays. The admin pages are counted by
  address too (`iptv_admin_addr`), whatever cookie comes along.
- **The logs** hold 1024 characters of a browser's name at the most, 2048
  of an address or the page that linked, and no user name in the download
  log.
- **A request's body** is held in memory, never in nginx's temp folder on
  disk, in both nginx layers; the proxy lets at most 8 sync copies come in
  at once from one address (`dopeiptv_sync_put`).
- **Nothing with a dot first** is served (/.gitignore and /files/.gitignore
  were) but /.well-known/, where `security.txt` now says how to report a
  security problem. Its `Expires` is a year ahead: renew it each November
  (`donate/test_deploy_content.py` fails once it has passed).
- **The lang cookie** is `Secure; SameSite=Lax`.
- **The proxy** sends no version (`server_tokens off`), a
  Permissions-Policy, Cross-Origin-Opener-Policy on the admin pages,
  Cross-Origin-Resource-Policy on the admin pages and the services, and
  HSTS on iptv.dope.rs's move. `deploy/proxy-headers.sh` now puts into the
  live files whatever of the repo's they lack (it stopped once the first
  headers were in).
- **Feedback**: a report is cut before it is cleaned, a crash's first
  frame is found in one look (a made-up one held the service for
  seconds), and a home folder's name becomes `<user>`.
- **The service's log** is turned over by newsyslog; its daemon reopens it
  on a SIGHUP (`-H` in `deploy/iptv_donate`).

**pontusweb (jail)** - `bastille console pontusweb`, then `sh`. The files,
the tests, the service with its new rc file:

```sh
cd ~/dopeiptv
git checkout website
git pull origin website
python3 -m pytest -q -p no:cacheprovider donate/
cp -R donate /usr/local/www/iptv/
cp sync-releases.sh /usr/local/www/iptv/
cp -R public/. /usr/local/www/iptv/public/
install -m 555 deploy/iptv_donate /usr/local/etc/rc.d/iptv_donate
service iptv_donate restart
tail -n 2 /var/log/iptv-donate.log
```

The service's log turned over - only once the rc file above runs it, whose
daemon reopens the log on newsyslog's SIGHUP (without `-H` daemon does not
catch one). Five copies of up to 1 MB, the service's alone to read:

```sh
grep -q iptv-donate.log /usr/local/etc/newsyslog.conf.d/iptv.dope.rs.conf || echo '/var/log/iptv-donate.log                     iptv:iptv      600  5     1024 *    J     /var/run/iptv_donate/iptv_donate.pid' >> /usr/local/etc/newsyslog.conf.d/iptv.dope.rs.conf
newsyslog -nv 2>&1 | grep iptv-donate
```

The content server's file - new zones only, no zone's key changed, so a
reload takes it; the workers' start time and the log say so:

```sh
diff /usr/local/etc/nginx/conf.d/iptv.dope.rs.conf deploy/content-iptv.dope.rs.conf
cp /usr/local/etc/nginx/conf.d/iptv.dope.rs.conf /root/iptv.dope.rs.conf.before-hardening
cp deploy/content-iptv.dope.rs.conf /usr/local/etc/nginx/conf.d/iptv.dope.rs.conf
nginx -t && service nginx reload
ps -axo pid,lstart,command | grep "[n]ginx: worker"
grep -n emerg /var/log/nginx/error.log | tail -3
```

If `nginx -t` complains, `cp /root/iptv.dope.rs.conf.before-hardening
/usr/local/etc/nginx/conf.d/iptv.dope.rs.conf` puts the old file back.

Then what the content server wrote into the jail's error log before: every
line that names its server (`server: dopeiptv.org`, `server: iptv.dope.rs`,
or its port, `:8081`) goes; Nextcloud's lines stay. The file is written over
in place, so nginx goes on writing to it. Older copies of the log, if
anything turns it over (`error.log.0`, `error.log.0.bz2`...), hold the same
lines and go whole:

```sh
grep -c -E 'server: (dopeiptv\.org|iptv\.dope\.rs|[0-9.*]*:8081)' /var/log/nginx/error.log
grep -v -E 'server: (dopeiptv\.org|iptv\.dope\.rs|[0-9.*]*:8081)' /var/log/nginx/error.log > /root/error.log.kept
cat /root/error.log.kept > /var/log/nginx/error.log
rm /root/error.log.kept
ls -l /var/log/nginx/
rm -f /var/log/nginx/error.log.[0-9]*
```

The first line counts what goes; run again afterwards it says 0, and
stays so: a refused request writes nothing there now.

**webproxy (jail)** - `bastille console webproxy`, then `sh`. The script
brings both of the proxy's files for the site up to the repo's, keeping
their addresses and certificates, tests them with `nginx -t` and reloads
(or puts both back):

```sh
fetch -qo /root/proxy-headers.sh https://raw.githubusercontent.com/slimture/dopeIPTV/website/deploy/proxy-headers.sh
sh /root/proxy-headers.sh
ps -axo pid,lstart,command | grep "[n]ginx: worker"
grep -n emerg /var/log/nginx/error.log | tail -3
```

It says which of the http block's headers it named again, any line the
live file has with a value of its own (kept), and "done" for each file it
changed, with its copy in /root; run again, it says "nothing changed".
(Without GitHub from the jail: on the host, `cp
/root/iptv-static/deploy/proxy-headers.sh
/usr/local/bastille/jails/webproxy/root/root/` after step 2's fetch.)

The proxy's dopeiptv servers have written only what stops them since the
"no front server log" round; lines from before that name visitors'
addresses. The same cleaning, by those servers' names:

```sh
grep -c -E 'server: (www\.)?(dopeiptv\.org|iptv\.dope\.rs)' /var/log/nginx/error.log
grep -v -E 'server: (www\.)?(dopeiptv\.org|iptv\.dope\.rs)' /var/log/nginx/error.log > /root/error.log.kept
cat /root/error.log.kept > /var/log/nginx/error.log
rm /root/error.log.kept
ls -l /var/log/nginx/
rm -f /var/log/nginx/error.log.[0-9]*
```

**Port 8081: only the proxy.** The content server answers whoever reaches
10.0.1.2:8081 and believes the X-Real-IP it is told (the statistics, every
limit per visitor), and the address that may is this server's own, never
the repo's. From the host and from a jail other than webproxy:

```sh
fetch -T 5 -qo /dev/null http://10.0.1.2:8081/ && echo "8081 answers here"
```

Only the proxy should get an answer. Where another does, the content
server's file includes `/usr/local/etc/nginx/iptv-only-from-proxy.conf`
(a pattern this round: nothing until the file is there, and a new copy of
the repo's file keeps it - since "Every log, and port 8081 closed" a
plain name, without which nginx does not start). With the proxy's address
as pontusweb sees it - `jls -j webproxy ip4.addr` on the host, or
`ifconfig` in webproxy for a VNET jail - put in that file, in pontusweb:

```nginx
allow <the proxy's address>;
deny all;
```

then `nginx -t && service nginx reload`, and at once `curl -sI
https://dopeiptv.org/ | head -1`: still 200 - a 403 means the proxy comes
from another address; `rm /usr/local/etc/nginx/iptv-only-from-proxy.conf`
and reload, then look again. The check above then answers 403 everywhere
but on the proxy.

**Check** (from anywhere):

```sh
curl -sI https://dopeiptv.org/.gitignore | head -1
curl -s https://dopeiptv.org/.well-known/security.txt
curl -sI 'https://dopeiptv.org/?lang=sv' | grep -i '^set-cookie'
curl -sI https://dopeiptv.org/admin/login | grep -i -E '^(server|permissions-policy|cross-origin-[a-z]+-policy):'
curl -sI https://dopeiptv.org/ | grep -i -E '^(server|permissions-policy|cross-origin-[a-z]+-policy):'
curl -sI https://iptv.dope.rs/ | grep -i '^strict-transport-security'
curl -sI 'https://dopeiptv.org/dl.php?f=SHA256SUMS' | grep -i '^location'
curl -s -o /dev/null -w '%{http_code}\n' "https://dopeiptv.org/dl.php?f=dopeIPTV-1.6.8.90-$(printf 'x%.0s' $(seq 110))"
curl -s -o /dev/null -w '%{http_code}\n' https://dopeiptv.org/sync/$(printf 'ab%.0s' $(seq 32))
```

Answers: `HTTP/2 404`; the five lines of security.txt; `set-cookie:
lang=sv; Max-Age=31536000; path=/; Secure; SameSite=Lax`; on /admin/login
`server: nginx` (no version), the permissions policy and both
cross-origin policies, `same-origin`; on / the same but no cross-origin
line; the HSTS line; `location: /files/SHA256SUMS`; `404` for a name of
128 characters (a test build's shape: before, a 302 to GitHub); `404` for
an empty sync slot, as before. Then in the app: Sync now, Send feedback, a
watch-together room - as before; the admin page signs in and fills.

**Privacy, behind the scenes** showed the code of site commit 12d6507; it
is made again from this round's code with the part below.

## The servers on the transparency page

*Security and privacy, behind the scenes* has a part of its own for the
servers (part 7, "The servers behind dopeiptv.org"): the way a request
takes, what each server writes down and for how long, the limits, the
headers on every answer, and what it does not protect against - in the
front proxy's and the content server's own lines, `donate.py`'s socket and
the log rotation's lines in this file. `tools/transparency.py` reads
nginx's files as nginx does and takes a block by its first words
(`"nginx": ["server > location = /hit", ...]`, `nginx_find`), with the
comments above it; the headers the page shows as an example are worked out
from the proxy's file and `csp.snippet.txt`. Nothing it shows is a secret:
the proxy's address, the live allow-list and nginx.conf stay on the
server.

The page quotes this file in two places - the site's whole newsyslog file
and the logs narrowed by hand, both in "This round's deploy" under "Every
log, and port 8081 closed" (until that round the three lines in "10. Log
rotation", "Statistics and the admin page" and "Hardening, November
2026") - found by their words: change those, and change `CODE` in
`tools/transparency.py` with them. `python3 -m pytest donate/test_transparency.py` fails once a
block the page shows is gone from its file. After a change to a file the
page shows, once it is on GitHub:

```sh
python3 tools/transparency.py --app <a checkout of the app at v1.6.8> --ref v1.6.8
```

and commit `site/transparency.json`.

Deploy (jail) - of what the server runs only `site/` changed (beside it a
test in `donate/` and `tools/`); neither nginx file nor the service:

```sh
cd ~/dopeiptv && git checkout website && git pull origin website
cp -R site donate sync-releases.sh /usr/local/www/iptv/
cd /usr/local/www/iptv && su -m iptv -c 'sh sync-releases.sh'
```

**Check:** `https://dopeiptv.org/transparency/` has nine parts, the
seventh "The servers behind dopeiptv.org" (`?lang=sv`: "Servrarna bakom
dopeiptv.org"), and its *Copy the code and the question* gives the question
with the proxy's `location /` first.

## The dopeiptv jail

Since 19 November 2026 the site's content server, its service and its
cron jobs run in a Bastille thin jail of their own, `dopeiptv`, which
holds nothing else: nginx, the Python service (`iptv_donate`, as `iptv`)
and `/etc/crontab`'s jobs (`sync-releases.sh` and `stats_collect.py`, as
`iptv`). `pontusweb`, where they ran beside unrelated PHP, MySQL and Redis
sites, no longer serves the site and is cleaned up later; the sections
above that say pontusweb are how it got here.

- **What may reach it, and what it may reach.** The host's pf lets into
  the jail only the TLS-front proxy's jail, on port 8081, and out of it
  only DNS and https - which the service and the scripts use for Stripe,
  GitHub, the browsers' push services and DB-IP's download (and `git
  pull`). No ssh, no port 80, nothing else. The rules hold the server's
  addresses and are not in the repository. nginx's own allow list
  (`/usr/local/etc/nginx/iptv-only-from-proxy.conf`: `allow <the proxy>;
  deny all;`, never in git) is a second lock behind the firewall.
- **Its nginx.conf** is the jail's own, and small:
  `deploy/nginx-dopeiptv-jail.conf`, at `/usr/local/etc/nginx/nginx.conf`
  - no access log, an error log for what stops nginx alone (`crit`), no
  version in an answer, and `conf.d/*.conf` - the content server's file -
  for the rest. nginx's workers run as `www`, nginx's own default.
- **Everything is run from the host**: `jexec`, `service -j`, `sysrc -j`
  and `pkg -j`. The checkout is `/root/dopeiptv` inside the jail, and the
  jail's root, from the host, `/usr/local/bastille/jails/dopeiptv/root`.
- **The logs the service and the cron jobs write** are made before they
  first run, `iptv`'s and 600: `daemon -o` and cron's `>>` run as `iptv`,
  who cannot make a file in `/var/log`. `install` over a log that is there
  would empty it, hence the `test -e`:

```sh
jexec dopeiptv sh -c 'test -e /var/log/iptv-donate.log || install -o iptv -g iptv -m 600 /dev/null /var/log/iptv-donate.log'
jexec dopeiptv sh -c 'test -e /var/log/iptv-stats.log || install -o iptv -g iptv -m 600 /dev/null /var/log/iptv-stats.log'
jexec dopeiptv sh -c 'test -e /var/log/iptv-sync.log || install -o iptv -g iptv -m 600 /dev/null /var/log/iptv-sync.log'
```

**The standard deploy** (host) - the code, the service, the pages
rendered now as `iptv` rather than at the next quarter-hour:

```sh
jexec dopeiptv sh -c 'cd /root/dopeiptv && git pull origin website && cp -R public/. /usr/local/www/iptv/public/ && cp -R site donate sync-releases.sh /usr/local/www/iptv/'
service -j dopeiptv iptv_donate restart
jexec -U iptv dopeiptv /bin/sh /usr/local/www/iptv/sync-releases.sh
```

A change to the content server's nginx file (host) - a new zone key needs
a new zone name, or the reload keeps the old configuration ("A reload that
did nothing"):

```sh
cp /usr/local/bastille/jails/dopeiptv/root/root/dopeiptv/deploy/content-iptv.dope.rs.conf /usr/local/bastille/jails/dopeiptv/root/usr/local/etc/nginx/conf.d/iptv.dope.rs.conf
jexec dopeiptv nginx -t && service -j dopeiptv nginx reload
```

The jail's nginx.conf the same way, from `deploy/nginx-dopeiptv-jail.conf`
to `/usr/local/bastille/jails/dopeiptv/root/usr/local/etc/nginx/nginx.conf`.

## Every log, and port 8081 closed

What a second review of the site found, and what changed (each commit
says it in full). Nothing changes for a visitor, for the app - 1.6.7, 1.6.8
and newer sync, send feedback, watch together, update and tip as before -
or for the admin page. Written for the dopeiptv jail (above).

- **Port 8081 closed to all but the proxy.** The content server's file
  includes `iptv-only-from-proxy.conf` by its plain name now: without the
  file `nginx -t` fails and nginx does not start. Before, a pattern
  (`.con[f]`) matched nothing while the file was missing, and the port
  answered whoever reached it - and believed the X-Real-IP they said. The
  file lets in the proxy alone: nothing in the jail itself asks for 8081
  (`sync-releases.sh` fetches only from GitHub; the service and
  `stats_collect.py` only from Stripe, the push services, GitHub and
  DB-IP), so it has no `allow 127.0.0.1;`. A location that only answers
  with a redirect or a "not found" answers before nginx asks the list, and
  gives nothing but that. The file is in the dopeiptv jail already, so for
  the running site nothing changes; the difference is the day it is gone.
- **The service's socket is nginx's and the service's alone.** It was 0666
  in a folder anyone could pass through: any program in the jail could
  connect, and say any X-Real-IP or X-Admin-User header (the admin pages
  check the session themselves). Now the socket is 0660 and of the group
  nginx's workers run as, `www` (`SOCKET_GROUP` in `iptv-donate.conf`, `www`
  if not set), made so from the start (umask 117 while it is made), in
  `/var/run/iptv_donate`, which the rc file makes `iptv:www` 0750 at every
  start (`iptv_donate_socket_group` in rc.conf, `www` - the same group as
  `SOCKET_GROUP`); it was `iptv` 0755. On FreeBSD a new file takes its
  folder's group, so the socket is `www`'s without the service being in
  the group. Where the group does not exist (another system, a typo), the
  socket is 0666 as before - nginx still reaches it whoever it runs as -
  in a folder of the service's own that others can only pass through
  (0711), and the start line says so (since "Fail closed, answers in
  memory, no links followed" the service does not start then). In the dopeiptv jail only nginx
  (`www`) and the service (`iptv`) run, so the socket is theirs alone.
- **The logs keep less of the address asked for.** A page view (`/`,
  `/help/`) keeps its path, never its query - `?fbclid=`, `?utm_...`,
  whatever a link carried; a `?lang=` the line's `l` says already, and
  `stats_collect.py` reads only the path. `/hit` keeps the twelve fields
  the page's signal sends (`nav.js`) and `stats_collect.py` reads - each as
  sent, the page that linked last, so a long one is cut before the rest -
  and nothing else of its query. A download's request line keeps its
  method, the file's name (`$iptv_dl_file`, the name `/dl.php` answers
  for) and the protocol. `stats_collect.py` and `sync-releases.sh` read the
  lines of before and of now alike while the old ones age out, two days
  (`donate/test_log_fields.py` works the lines out from the file as nginx
  writes them and counts both).
- **Every log, honestly.** The transparency page (part 7) and the privacy
  policy list every log on the server with what it holds, how long and
  who may read it - not "two days" where a log keeps more. The site's
  newsyslog file is written whole (below): `iptv-sync.log` 600 (it was
  644), `iptv-stats.log` turned over at all (it never was) and 600. nginx's
  error log in both jails is narrowed to 600 (nginx makes it 644). The two
  access logs are 640 `root:iptv`: nginx makes a log its workers' user's
  (`www`) when it reopens it, and `stats_collect.py` and `sync-releases.sh`
  read them as `iptv` - through the group; 600 would lock them out. In the
  dopeiptv jail `www` is nginx alone.

### Every log on the server

| Log | Jail | What it holds | Kept | Mode |
|---|---|---|---|---|
| `/var/log/nginx/iptv.dope.rs-stats.log` | dopeiptv | Page views (`/`, `/help/`) and `/hit`: the visitor's address, time, method, the page's path - for `/hit` its twelve fields -, status, the page that linked without its query, the browser's name (1024 characters), the page's language | turned over at midnight, the day before kept: two days at the most (`1 * @T00`) | 640 `www:iptv` (newsyslog makes it `root:iptv`, nginx's reopen gives it to its workers' user) |
| `/var/log/nginx/iptv.dope.rs-downloads.log` | dopeiptv | `/dl.php`: the proxy's address, time, method, the file's name, protocol, status, size, the page that linked without its query, the browser's name, the visitor's address | the same | the same |
| `/var/log/nginx/error.log` | dopeiptv | the jail's nginx, the site's alone; `crit` only, in its nginx.conf and in the server: what stops nginx - a file it cannot open, the service's socket gone or refused. Such a line names the request (its line - a sync slot, say) and the proxy's address, not the visitor's | not turned over by any line of the site's: until removed by hand | 600 from this round (nginx makes it 644) |
| `/var/log/nginx/error.log` | webproxy | the proxy's, shared with the other sites behind it; the site's servers write `crit` only - a TLS handshake nginx cannot make sense of, a worker out of files. There the address on a line is the visitor's | the same | 600 from this round |
| `/var/log/iptv-donate.log` | dopeiptv | the service's (daemon `-o`): its start line and set-up, files it narrowed, a fault by its kind and file:line (never its words), Stripe's refusals in Stripe's fixed codes (a tip that could not even start: Stripe's sentence about the site's own settings), a push that failed and the push service's host, an admin sign-in refused and why (an admin's name, never what was typed or from where), once a day how many sync copies, rooms, reports and modified builds' requests came and went, sync's space; the statistics' own lines when the admin page brings them up to date. Never an address, a request or its path, nor anything a visitor sent | five old copies of up to 1 MB (`5 1024 *`): until 5 MB more are written - at its pace months, perhaps years | 600 `iptv` |
| `/var/log/iptv-stats.log` | dopeiptv | `stats_collect.py`'s from cron: the country database fetched (its address) or why not, how many log lines could not be read (a count, never a line), a day that held as many visits as a day may, a `STATS_IGNORE` name not found (yours), Python's error should a run break. Nothing of a visit | five old copies of up to 1 MB from this round (before: never turned over) - years at its pace | 600 `iptv` |
| `/var/log/iptv-sync.log` | dopeiptv | `sync-releases.sh`'s - the release mirroring and page rendering, nothing to do with settings sync: GitHub's release, each file fetched or already current, how many downloads counted (a number), the pages rendered, nightly.json | five old copies of up to 1 MB: a few lines every five minutes, so months | 600 `iptv` (it was 644) |

Beside them only FreeBSD's own (cron, messages, auth.log...), which hold
nothing of a request. Nothing else writes a log: the content server's
other requests none (`access_log off`, in the server and in the jail's
nginx.conf), the proxy's servers for the site none, the service none of a
request (`log_message` says nothing).

### This round's deploy (host)

The allow list - it should be there; two lines, `allow` with the proxy's
address and `deny all;`:

```sh
jexec dopeiptv cat /usr/local/etc/nginx/iptv-only-from-proxy.conf
```

Were it missing, it is made before anything else, or nginx stops at the
new file below. `PROXY_ADDRESS` is the proxy's address as the jail sees
it: put it in before running the line (left as it is, `nginx -t` refuses
it with `invalid parameter`). Nothing of it goes to git:

```sh
printf 'allow %s;\ndeny all;\n' PROXY_ADDRESS > /usr/local/bastille/jails/dopeiptv/root/usr/local/etc/nginx/iptv-only-from-proxy.conf
```

The files, and the service with its new rc file - its socket's folder is
made again at the start:

```sh
jexec dopeiptv sh -c 'cd /root/dopeiptv && git pull origin website && cp -R public/. /usr/local/www/iptv/public/ && cp -R site donate sync-releases.sh /usr/local/www/iptv/'
jexec dopeiptv install -m 555 /root/dopeiptv/deploy/iptv_donate /usr/local/etc/rc.d/iptv_donate
service -j dopeiptv iptv_donate restart
jexec dopeiptv ls -ld /var/run/iptv_donate
jexec dopeiptv ls -l /var/run/iptv_donate/
jexec dopeiptv tail -n 1 /var/log/iptv-donate.log
jexec dopeiptv python3 -I /usr/local/www/iptv/donate/donate.py --check
```

`drwxr-x---  2 iptv  www` for the folder; in it `srw-rw----  1 iptv  www
... donate.sock` and `-rw-------  1 iptv  www ... iptv_donate.pid`; the
log's last line `[donate] listening on
unix:/var/run/iptv_donate/donate.sock (0660, group www), Stripe set up,
...`; `--check` (as root) answers `200 {"stripe": true, ...}`. A line with
`no group 'www' here` (`0666: ...` until the next round, `[donate] not
started: ...` since) means the group is named otherwise in this
jail: `jexec dopeiptv pw groupshow www`, then `SOCKET_GROUP=` in the
jail's `/usr/local/etc/iptv-donate.conf` and `sysrc -j dopeiptv
iptv_donate_socket_group=`, both the group nginx's workers run as.

The content server's file - no zone's key changed, so a reload takes it -
and a look at the jail's nginx.conf against the repo's copy (the same
directives; the layout may differ):

```sh
diff /usr/local/bastille/jails/dopeiptv/root/usr/local/etc/nginx/conf.d/iptv.dope.rs.conf /usr/local/bastille/jails/dopeiptv/root/root/dopeiptv/deploy/content-iptv.dope.rs.conf
cp /usr/local/bastille/jails/dopeiptv/root/usr/local/etc/nginx/conf.d/iptv.dope.rs.conf /root/iptv.dope.rs.conf.before-logs
cp /usr/local/bastille/jails/dopeiptv/root/root/dopeiptv/deploy/content-iptv.dope.rs.conf /usr/local/bastille/jails/dopeiptv/root/usr/local/etc/nginx/conf.d/iptv.dope.rs.conf
jexec dopeiptv nginx -t && service -j dopeiptv nginx reload
jexec dopeiptv ps -axo pid,lstart,command | grep "[n]ginx: worker"
jexec dopeiptv grep -n emerg /var/log/nginx/error.log | tail -3
jexec dopeiptv sh -c 'nginx -T 2>/dev/null | grep -E "^(allow|deny) "'
diff /usr/local/bastille/jails/dopeiptv/root/usr/local/etc/nginx/nginx.conf /usr/local/bastille/jails/dopeiptv/root/root/dopeiptv/deploy/nginx-dopeiptv-jail.conf
```

Workers started just now, no `emerg`, and the allow list's two lines as
nginx has loaded them. If `nginx -t` complains, `cp
/root/iptv.dope.rs.conf.before-logs
/usr/local/bastille/jails/dopeiptv/root/usr/local/etc/nginx/conf.d/iptv.dope.rs.conf`
puts the old file back - nginx was never reloaded with the new one.

The pages - the transparency page and the privacy policy say all of it:

```sh
jexec -U iptv dopeiptv /bin/sh /usr/local/www/iptv/sync-releases.sh
```

The site's newsyslog file, whole (`iptv-stats.log` new, `iptv-sync.log`
600; the downloads' and statistics' logs turned over at midnight with the
day before kept uncompressed, `P`, for the readers to finish; 30 is
SIGUSR1, nginx's reopen; the service's line sends daemon(8) its SIGHUP,
which its `-H` takes) - the `cp` says there is no such file if the jail
had none yet, and then there is nothing to keep:

```sh
mkdir -p /usr/local/bastille/jails/dopeiptv/root/usr/local/etc/newsyslog.conf.d
cp /usr/local/bastille/jails/dopeiptv/root/usr/local/etc/newsyslog.conf.d/iptv.dope.rs.conf /root/newsyslog-iptv.dope.rs.conf.before-logs
cat > /usr/local/bastille/jails/dopeiptv/root/usr/local/etc/newsyslog.conf.d/iptv.dope.rs.conf <<'LOGS'
/var/log/nginx/iptv.dope.rs-downloads.log    root:iptv      640  1     *    @T00 JP    /var/run/nginx.pid 30
/var/log/nginx/iptv.dope.rs-stats.log        root:iptv      640  1     *    @T00 JP    /var/run/nginx.pid 30
/var/log/iptv-sync.log                       iptv:iptv      600  5     1024 *    JN
/var/log/iptv-stats.log                      iptv:iptv      600  5     1024 *    JN
/var/log/iptv-donate.log                     iptv:iptv      600  5     1024 *    J     /var/run/iptv_donate/iptv_donate.pid
LOGS
jexec dopeiptv sh -c 'newsyslog -nv 2>&1 | grep iptv'
```

`newsyslog -nv` lists the five, rotating none (the jail's own cron runs
newsyslog every hour). newsyslog sets the mode of a log it makes anew; the
ones there now are narrowed by hand - the service's and the scripts' to
`iptv` alone, nginx's error log to nginx, its two access logs to nginx and
the `iptv` group:

```sh
jexec dopeiptv sh -c 'chmod 600 /var/log/iptv-sync.log* /var/log/iptv-stats.log* /var/log/iptv-donate.log* /var/log/nginx/error.log'
jexec dopeiptv sh -c 'chgrp iptv /var/log/nginx/iptv.dope.rs-*.log && chmod 640 /var/log/nginx/iptv.dope.rs-*.log'
jexec dopeiptv sh -c 'ls -l /var/log/iptv-* /var/log/nginx/'
```

The webproxy jail - only its error log's mode; neither of the proxy's
files changes:

```sh
jexec webproxy ls -l /var/log/nginx/error.log
jexec webproxy chmod 600 /var/log/nginx/error.log
jexec webproxy ls -l /var/log/nginx/error.log
```

**Check.** From anywhere, the site, Sync now, Send feedback and a
watch-together room in the app, and the admin page work as before. Then
the logs: open `https://dopeiptv.org/?utm_source=check&fbclid=x` in a
private window (no admin cookie there), and from anywhere

```sh
curl -sI 'https://dopeiptv.org/dl.php?f=SHA256SUMS&utm_source=check' | head -1
```

and on the host:

```sh
jexec dopeiptv tail -n 4 /var/log/nginx/iptv.dope.rs-stats.log
jexec dopeiptv tail -n 1 /var/log/nginx/iptv.dope.rs-downloads.log
```

Among the last lines, the page's says `"u":"/"` and its `/hit`'s
`"u":"/hit?p=%2F&ev=view&t=&x=&n=&s=check&l=en&...&r="` - no `fbclid`
anywhere; the download's request is `"HEAD /dl.php?f=SHA256SUMS
HTTP/1.0" 302` (a HEAD is not counted).

## Fail closed, answers in memory, no links followed

What a third review of the site found, and what changed (each commit says
it in full). Nothing changes for a visitor, for the app - sync, feedback,
watch together, updates and tips as before - or for the admin page.
Written for the dopeiptv jail.

- **The service does not start on a socket others could use.** Where the
  group `SOCKET_GROUP` names does not exist, or the service may not give
  it (another system, a typo), it stops at once and says why in its log -
  `[donate] not started: the socket ... cannot be nginx's and this
  service's alone: no group 'www' here. SOCKET_GROUP in
  /usr/local/etc/iptv-donate.conf names the group nginx's workers run
  as, ...` - and leaves no socket behind; before, it went on with the
  socket 0666, open to every user in the jail. daemon(8) starts it again
  every 5 seconds, so the line comes back until the config is right.
  Nothing opens it to every user: there is no setting for that (a
  developer's machine names a group of its own user's in `SOCKET_GROUP`;
  a `SOCKET_OPEN = yes` left in a config does nothing). In the
  dopeiptv jail `www` is there and the socket is 0660 `iptv:www` already:
  nothing changes but the day the group is gone.
- **No answer in a temporary file.** A request's body has been held in
  memory since "Hardening, November 2026"; an answer larger than nginx's
  buffers was still written to a file in `/var/tmp/nginx` and sent from
  there - a sync copy can be 2 MB. `proxy_max_temp_file_size 0;` in the
  content server's 8081 server and in the proxy's dopeiptv.org server:
  such an answer is passed on as it comes, the service and the proxy
  waiting for the reader (the service gives a write 600 s). www, plain
  http and iptv.dope.rs proxy nothing. The proxy's live file differs from
  the repo's - its own addresses, an older layout - so the one line goes
  in by itself (below); `deploy/proxy-headers.sh` would put it in too.
- **No symbolic link followed.** `disable_symlinks on
  from=$document_root;` in the content server: a link under `public/` - a
  copy, a hand, a mistake - to a file outside it is answered as if nothing
  were there. Nothing the site serves is one (git holds none in `public/`
  or `site/`, `sync-releases.sh` makes none); the deploy looks on the
  server before the file goes in. nginx then opens each folder below the
  root on its own, on FreeBSD only to pass through it (`O_SEARCH`, or
  `O_EXEC` where there is no `O_SEARCH`), so no folder needs more than it
  has now.

### This round's deploy (host)

**dopeiptv** - the files, and a look that nothing under the site's root is
a link (no output: from the new file on, one would be answered 404):

```sh
jexec dopeiptv sh -c 'cd /root/dopeiptv && git pull origin website && cp -R public/. /usr/local/www/iptv/public/ && cp -R site donate sync-releases.sh /usr/local/www/iptv/'
jexec dopeiptv find /usr/local/www/iptv/public -type l
```

The content server's file - no zone changed, so a reload takes it:

```sh
diff /usr/local/bastille/jails/dopeiptv/root/usr/local/etc/nginx/conf.d/iptv.dope.rs.conf /usr/local/bastille/jails/dopeiptv/root/root/dopeiptv/deploy/content-iptv.dope.rs.conf
cp /usr/local/bastille/jails/dopeiptv/root/usr/local/etc/nginx/conf.d/iptv.dope.rs.conf /root/iptv.dope.rs.conf.before-tempfile
cp /usr/local/bastille/jails/dopeiptv/root/root/dopeiptv/deploy/content-iptv.dope.rs.conf /usr/local/bastille/jails/dopeiptv/root/usr/local/etc/nginx/conf.d/iptv.dope.rs.conf
jexec dopeiptv nginx -t && service -j dopeiptv nginx reload
jexec dopeiptv ps -axo pid,lstart,command | grep "[n]ginx: worker"
jexec dopeiptv sh -c 'nginx -T 2>/dev/null | grep -E "^ *(proxy_max_temp_file_size|disable_symlinks) "'
```

The diff shows only the two directives and their comments; workers started
just now; nginx has loaded `disable_symlinks on from=$document_root;` and
`proxy_max_temp_file_size 0;`. If `nginx -t` complains, nothing was
reloaded, and `cp /root/iptv.dope.rs.conf.before-tempfile
/usr/local/bastille/jails/dopeiptv/root/usr/local/etc/nginx/conf.d/iptv.dope.rs.conf`
puts the old file back.

The service (its rc file is the same) and the pages - the transparency
page and the privacy policy:

```sh
service -j dopeiptv iptv_donate restart
jexec dopeiptv tail -n 1 /var/log/iptv-donate.log
jexec dopeiptv python3 -I /usr/local/www/iptv/donate/donate.py --check
jexec -U iptv dopeiptv /bin/sh /usr/local/www/iptv/sync-releases.sh
```

The log's last line `[donate] listening on
unix:/var/run/iptv_donate/donate.sock (0660, group www), Stripe set up,
...`, and `--check` `200 {"stripe": true, ...}`. A line `[donate] not
started: ...` means the group it names is not there: it says what to put
right, and the service tries again every 5 seconds.

**webproxy** - one line into the live `dopeiptv.org.conf`, which is never
written over with the repo's: a copy kept, the line put into a new file
right after `server_name dopeiptv.org;` (awk says `not put in` where the
file has no such line or has the directive already - then stop there), the
difference shown - one added line, `proxy_max_temp_file_size 0;` -, the
live configuration tested as it is, then the new file moved in, tested,
and nginx reloaded:

```sh
cp -p /usr/local/bastille/jails/webproxy/root/usr/local/etc/nginx/conf.d/dopeiptv.org.conf /root/dopeiptv.org.conf.before-tempfile
awk '/^[ \t]*proxy_max_temp_file_size[ \t]/ { had++ } { print } /^[ \t]*server_name[ \t]+dopeiptv\.org[ \t]*;/ { print "    proxy_max_temp_file_size 0;"; n++ } END { exit (n == 1 && had == 0 ? 0 : 1) }' /root/dopeiptv.org.conf.before-tempfile > /root/dopeiptv.org.conf.new && echo "one line put in" || echo "not put in"
diff /root/dopeiptv.org.conf.before-tempfile /root/dopeiptv.org.conf.new
jexec webproxy nginx -t
mv -f /root/dopeiptv.org.conf.new /usr/local/bastille/jails/webproxy/root/usr/local/etc/nginx/conf.d/dopeiptv.org.conf
jexec webproxy nginx -t && service -j webproxy nginx reload
jexec webproxy ps -axo pid,lstart,command | grep "[n]ginx: worker"
jexec webproxy sh -c 'nginx -T 2>/dev/null | grep -n proxy_max_temp_file_size'
```

Workers started just now, and nginx has the one line. Should the second
`nginx -t` complain, nothing was reloaded; this puts the old file back:

```sh
cp -p /root/dopeiptv.org.conf.before-tempfile /usr/local/bastille/jails/webproxy/root/usr/local/etc/nginx/conf.d/dopeiptv.org.conf && jexec webproxy nginx -t && service -j webproxy nginx reload
```

These commands hold no `#` and no `!`, and remove nothing: they run as
they are in the host's zsh.

**Check.** From anywhere, the site, Sync now, Send feedback and a
watch-together room in the app, an update check, a tip dialog that opens,
and the admin page work as before. Then a link under the site's root, to
a file outside it that is public anyway, answered 404 (a 200 would mean it
was followed) - and taken away again:

```sh
jexec dopeiptv ln -s /usr/local/www/iptv/sync-releases.sh /usr/local/www/iptv/public/link-check.txt
curl -s -o /dev/null -w '%{http_code}\n' https://dopeiptv.org/link-check.txt
jexec dopeiptv rm /usr/local/www/iptv/public/link-check.txt
```

## Checked from outside, people not visits, codes forgotten (1.6.9 review)

- `deploy/check-live.sh` checks what the live site really sends, from any
  computer that is not the server: the security headers on a page, on a
  404 and on a service (CSP with `default-src` and `frame-ancestors 'none'`,
  HSTS, nosniff, DENY, Referrer-Policy, COOP on the admin pages - only they
  get it -, no version in `Server`) - and,
  given the content jail's address, that its port 8081 does not answer from
  outside. Run it after every deploy:

  ```sh
  sh deploy/check-live.sh dopeiptv.org <the content jail's public-facing address>
  ```

- The statistics' months are made k-anonymous by **people**: a combination
  kept is shared by at least five different visitors, told apart by a short
  hash of the month's visitor id (`who`) that goes in the same step - one
  person who came five times no longer counts as five (`stats_collect.py`,
  `k_anonymous`). Months already made so stay as they are.
- A feedback report's code hash (`reply`) goes once its thread has been quiet
  for `CODE_DAYS` (120): nothing asks after it then, and nothing links the
  report to the copy of the app that sent it (`feedback.forget_codes`, run
  with the daily prune).

Deploy as the rounds before (copy `site`, `public`, `donate` and restart
`iptv_donate`), then run the check above.

## No address written down, keys in memory (1.6.9 review, second round)

The statistics are rebuilt so that nothing on the server's disks can lead
back to a visitor - not for a minute, not in a backup. It supersedes what
"Statistics and the admin page" and "Every log, and port 8081 closed" say
about the statistics' logs. Nothing changes for a visitor or for the app;
the admin page shows the same numbers (unique visitors are counted from
sketches now, within a few in a thousand).

- **No log with an address.** nginx hands each statistics line (a page
  view, `/hit`, a click on a download) to the donate service over a socket
  of its own (`/var/run/iptv_donate/stats.sock`, `syslog:` in the content
  server's file) instead of writing `iptv.dope.rs-stats.log` and
  `iptv.dope.rs-downloads.log`. The lines wait in the service's memory and
  its own thread counts them every minute (`donate/stats_inbox.py`,
  `donate.start_statistics`) - the cron job for `stats_collect.py` goes. The
  public download counter (`sync-releases.sh`) gets a log of its own that
  holds the request line, the answer and what asked (a browser, the app, a
  robot) - no address, no time, no browser's name
  (`iptv.dope.rs-dl-counts.log`, nginx's `iptv_dl_count`).
- **Keys in memory alone.** The month keys the visitor marks are made with
  are never in a file on a disk (`visitor-keys.json` is deleted at the first
  run). A restart starts new ones - a visitor before and after it counts
  twice that month - unless `STATS_KEYS_DIR` names a folder on a file system
  kept in memory (tmpfs, below), which a restart keeps and a reboot empties.
- **Nothing kept ties two days of a visitor together.** A visit in the
  visitor log names its visitor for its day alone; its mark is sealed with
  the month's key (random without it). Its start is kept to the ten minutes
  and what happened in it to ten seconds from there, a screen by its kind,
  the browser's language without its country, the site that linked by the
  name its owner registered, a link's tag only when it is one of ours or a
  site everyone knows. The lists of visitors' marks (`uniques.json`,
  `kinds.json`) become each day's sketches (`sketches.json`: two bits a
  visitor, no mark) and are deleted.
- **Kept for good, and anonymous for good.** The months' anonymous visits are
  k-anonymous by different people, told apart by their sealed marks (a key
  gone: one person at the most). A day's lists of who came (countries,
  languages, versions, sites, tags) keep no name counted fewer than five
  times once the day is a month old - those are the month's, where too a
  name fewer than five times is "other" once the month is whole.

### This round's deploy (host)

The files and the service - at its start it opens the statistics' socket:

```sh
jexec dopeiptv sh -c 'cd /root/dopeiptv && git pull origin website && cp -R public/. /usr/local/www/iptv/public/ && cp -R site donate sync-releases.sh /usr/local/www/iptv/'
service -j dopeiptv iptv_donate restart
jexec dopeiptv ls -l /var/run/iptv_donate/
jexec dopeiptv grep "statistics:" /var/log/iptv-donate.log | tail -1
```

`srw-rw----  1 iptv  www ... stats.sock` beside `donate.sock`, and the
line `[donate] statistics: lines from nginx at
/var/run/iptv_donate/stats.sock (0660, group www), counted every 60 s, keys
in memory alone`. Until nginx is switched the service still reads what is
left of the old logs.

The counter's log, then the content server's file:

```sh
jexec dopeiptv install -m 640 -o root -g iptv /dev/null /var/log/nginx/iptv.dope.rs-dl-counts.log
cp /usr/local/bastille/jails/dopeiptv/root/usr/local/etc/nginx/conf.d/iptv.dope.rs.conf /root/iptv.dope.rs.conf.before-memory
cp /usr/local/bastille/jails/dopeiptv/root/root/dopeiptv/deploy/content-iptv.dope.rs.conf /usr/local/bastille/jails/dopeiptv/root/usr/local/etc/nginx/conf.d/iptv.dope.rs.conf
jexec dopeiptv nginx -t && service -j dopeiptv nginx reload
jexec dopeiptv grep -n emerg /var/log/nginx/error.log | tail -3
```

Open the site in a private window, wait a minute and look: the admin page's
Visitors tab shows the visit, and nothing new is written to the old logs
(`jexec dopeiptv ls -l /var/log/nginx/`: their size stays). Then the old
download log's last lines into the counter, once, and the old logs away -
with every copy newsyslog kept of them, and `iptv-stats.log`, whose cron
job goes:

```sh
jexec -U iptv dopeiptv env IPTV_DOWNLOAD_LOG=/var/log/nginx/iptv.dope.rs-downloads.log /bin/sh /usr/local/www/iptv/sync-releases.sh
jexec dopeiptv sed -i '' '/stats_collect\.py/d' /etc/crontab
jexec dopeiptv grep -c stats_collect /etc/crontab
jexec dopeiptv sh -c 'rm -f /var/log/nginx/iptv.dope.rs-stats.log* /var/log/nginx/iptv.dope.rs-downloads.log* /var/log/iptv-stats.log*'
jexec dopeiptv ls -l /var/log/nginx/ /var/log/
```

`0` for the cron line. The site's newsyslog file, whole - the counter's
log turned over at midnight with the day before kept uncompressed for
`sync-releases.sh` (`P`), the scripts' and the service's as before:

```sh
cat > /usr/local/bastille/jails/dopeiptv/root/usr/local/etc/newsyslog.conf.d/iptv.dope.rs.conf <<'LOGS'
/var/log/nginx/iptv.dope.rs-dl-counts.log    root:iptv      640  1     *    @T00 JP    /var/run/nginx.pid 30
/var/log/iptv-sync.log                       iptv:iptv      600  5     1024 *    JN
/var/log/iptv-donate.log                     iptv:iptv      600  5     1024 *    J     /var/run/iptv_donate/iptv_donate.pid
LOGS
jexec dopeiptv sh -c 'newsyslog -nv 2>&1 | grep iptv'
```

A backup of the jail taken before this round holds those logs as they were:
delete their copies there too (`var/log/nginx/iptv.dope.rs-stats.log*`,
`iptv.dope.rs-downloads.log*`) - from now on no backup has them.

**The keys through a restart (optional).** A small tmpfs in the jail, its
owner the service's user, and the service told about it - `df -T` there says
`tmpfs`, and the start line `keys in memory and /var/db/iptv-stats-keys`.
`IPTV_UID` and `IPTV_GID` are `jexec dopeiptv id -u iptv` and `id -g iptv`:

```sh
mkdir -p /usr/local/bastille/jails/dopeiptv/root/var/db/iptv-stats-keys
echo "tmpfs /usr/local/bastille/jails/dopeiptv/root/var/db/iptv-stats-keys tmpfs rw,size=1m,mode=0700,uid=IPTV_UID,gid=IPTV_GID 0 0" >> /usr/local/bastille/jails/dopeiptv/fstab
mount -t tmpfs -o size=1m,mode=0700,uid=IPTV_UID,gid=IPTV_GID tmpfs /usr/local/bastille/jails/dopeiptv/root/var/db/iptv-stats-keys
jexec dopeiptv df -T /var/db/iptv-stats-keys
echo 'STATS_KEYS_DIR=/var/db/iptv-stats-keys' >> /usr/local/bastille/jails/dopeiptv/root/usr/local/etc/iptv-donate.conf
service -j dopeiptv iptv_donate restart
jexec dopeiptv grep "statistics:" /var/log/iptv-donate.log | tail -1
```

Then from any other computer, as after every round:

```sh
sh deploy/check-live.sh dopeiptv.org <the content jail's public-facing address>
```

### Every log on the server

| Log | Jail | What it holds | Kept | Mode |
|---|---|---|---|---|
| `/var/log/nginx/iptv.dope.rs-dl-counts.log` | dopeiptv | `/dl.php`: the method, the file's name and the protocol, the answer, and whether a browser, the app, a robot or nothing asked - no address, no time, no browser's name | turned over at midnight, the day before kept: two days at the most (`1 * @T00`) | 640 `www:iptv` (newsyslog makes it `root:iptv`, nginx's reopen gives it to its workers' user) |
| `/var/log/nginx/error.log` | dopeiptv | the jail's nginx, the site's alone; `crit` only: what stops nginx - a file it cannot open, the service's socket gone or refused. Such a line names the request and the proxy's address, not the visitor's | not turned over by any line of the site's: until removed by hand | 600 |
| `/var/log/nginx/error.log` | webproxy | the proxy's, shared with the other sites behind it; the site's servers write `crit` only - a TLS handshake nginx cannot make sense of, a worker out of files. There the address on a line is the visitor's | the same | 600 |
| `/var/log/iptv-donate.log` | dopeiptv | the service's (daemon `-o`): its start line and set-up, files it narrowed, a fault by its kind and file:line (never its words), Stripe's refusals in Stripe's fixed codes, a push that failed and the push service's host, an admin sign-in refused and why (an admin's name, never what was typed or from where), once a day how many sync copies, rooms, reports and modified builds' requests came and went, sync's space; the statistics' own lines - the country database fetched, how many lines could not be read or were dropped (a count, never a line), a day that held as many visits as a day may. Never an address, a request or its path, nor anything a visitor sent | five old copies of up to 1 MB (`5 1024 *`): months, perhaps years | 600 `iptv` |
| `/var/log/iptv-sync.log` | dopeiptv | `sync-releases.sh`'s - the release mirroring and page rendering: GitHub's release, each file fetched or already current, how many downloads counted (a number), the pages rendered, nightly.json | five old copies of up to 1 MB: months | 600 `iptv` |

Beside them only FreeBSD's own (cron, messages, auth.log...), which hold
nothing of a request. The statistics' lines are in the service's memory for
a minute and then only as what `stats_collect.py` keeps; the content
server's other requests none (`access_log off`), the proxy's servers for
the site none, the service none of a request.
