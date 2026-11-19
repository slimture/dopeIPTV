# dopeiptv.org — the dopeIPTV website

The site's address since November 2026; the old one, iptv.dope.rs, sends
every request on to the same path here (DEPLOY.md, "Moving to dopeiptv.org").

A single-page, responsive landing site (dark "broadcast console" theme) with a
download list that auto-mirrors the latest GitHub release. Static files only:
no PHP, no framework, no build step. A POSIX `sh` script with `fetch(1)` and
`jq` mirrors the release and renders every page in every language; nginx
serves them. Designed to sit inside a strict same-origin CSP.

```
website/
├── public/                 → the content server's web root
│   ├── style.css           the look: black/white, greys, the app's blue; light + dark
│   ├── app.js              OS-detect for the hero button (progressive enhancement)
│   ├── nav.js, help.js     the narrow-window menu and the help search
│   ├── features.js         /features/: the groups filter the list, the box searches it
│   ├── events.js           what visitors do on a page, for the statistics (what, never who)
│   ├── favicon.svg         the app's icon (blue tile, white play)
│   ├── fonts/              Inter (OFL), self-hosted; Apple devices use their own SF
│   ├── favicon.ico         16/32/48 px, for clients that ask for it blind
│   ├── site.webmanifest    PWA/icon metadata
│   ├── robots.txt          allows all + sitemap
│   ├── sitemap.xml         index → /sitemap.php (nginx serves the rendered one)
│   ├── i18n/               every page in every language, rendered by the sync (git-ignored)
│   ├── releases.json       written by the sync (never committed)
│   ├── files/              mirrored release assets land here (git-ignored)
│   └── screenshots/        the app's pictures, <lang>/<name>.webp (from the app's tools/site_screenshots.py):
│                           hero-1..5 (the first picture's slideshow), feat-<key> (the feature explorer)
├── site/                   what the pages are made from - not web-served
│   ├── templates/          index.html, help.html, features.html, legal.html, 404.html and nav.html
│   ├── lang/*.json         the texts, one file per language (en.json is the source)
│   ├── i18n.json           language names, which are right-to-left, ICU locales
│   ├── help.json           the help page's sections and topics
│   ├── features.json       every feature of the app (/features/, linked under the feature explorer):
│                           its groups and their features in order, each with the help answer it links to
│   ├── legal.json          the legal pages: their titles, the date, the languages
│   ├── legal/{sv,en}/      their texts - Swedish governs, English is a translation
│   ├── formats.json        dates and numbers per language, as ICU 76.1 writes them
│   ├── spots.json          where each feature picture shows its feature, per language (the same tool)
│   └── render.jq           the renderer
├── sync-releases.sh        cron: latest release → files/ + releases.json + i18n/
├── DEPLOY.md               moving the live site from the PHP version, step by step
└── deploy/                 nginx + CSP config templates (two roles)
```

## One look everywhere

The site is one design. The header, the narrow-window menu, the footer, the
pages and the dialogs are made of the same pieces, and a change to one of
them is carried to the others in the same commit - a header that looks
different from the menu it opens, or a footer from the page above it, is a
bug.

- **Colours** only from `style.css`'s tokens (`--text`, `--muted`, `--faint`,
  `--surface`, `--line`, `--accent`, `--heart`, `--brand`), in both themes.
- **One set of icons**: 24-unit line icons, a 1.8 stroke, round caps (the
  menu's in `render.jq`, `nav`: features, download, send, help; the heart
  filled). The same thing has the same icon wherever it shows.
- **One idea of a control**: the header's square buttons (44 x 40, a 12px
  radius); panels that open from the header (the menu, the language picker)
  16px round with rows of a finger's height, an icon in a tinted square and
  a chevron; hover is the accent's light tint everywhere.
- **The page you are on** is marked the same way everywhere: the accent, on
  its light wash.
- **The same words for the same thing**: a link reuses the key the other
  places use (`nav_help`, `support_nav`, `foot_payments`...) rather than a
  phrase of its own.
- **Every reader**: mirrored right to left, calm for reduced motion, a
  phone's tap target 44px or more.
- **Before it goes out**: look at it on a phone and a computer, in light and
  dark, and in one right-to-left language.

## Architecture

Two nginx roles (adapt to your own setup):

- a **TLS-front proxy** that terminates HTTPS for `dopeiptv.org` and reverse-
  proxies to
- a **content server** that serves the static site on an internal port
  (`:8081` in the template) and runs the release-sync cron.

What the PHP version worked out on every request is split in two:

- **Once per sync**, every 15 minutes: `sync-releases.sh` mirrors the release
  and renders every page for every language into `public/i18n/<lang>/`, plus
  the sitemap. Download counts come from nginx's log of `/dl.php`.
- **Per request**, in `deploy/content-iptv.dope.rs.conf`: nginx picks the
  language - `?lang=` (remembered in a cookie for a year), else the cookie,
  else the first language in the browser's `Accept-Language` that the site
  has, else English - and answers `/dl.php` (on to the file) and
  `/trakt/callback/` (on to the app) itself.

Nothing for this site talks to php-fpm. A tip is given in the tip dialog on
the page (`public/nav.js`: Stripe's card form in Stripe's frames, Apple Pay,
Google Pay, on dopeIPTV's own Stripe account); monthly support is Stripe's
own page (a Payment Link), linked from the support page, `/support/`, with
GitHub Sponsors beside them. What is on is the server's
(`/usr/local/etc/iptv-site.conf`, read by `sync-releases.sh`), and until one
is set the site invites to nothing. The one thing that is not static is a
small service, `donate/donate.py` (standard-library Python on a Unix socket):
the dialog's `/donate/` makes the PaymentIntent, and back from paying,
`/donate/key` and `/support/key` ask Stripe whether the payment went through
and give the supporter key for it. Setting it up: `DEPLOY.md`, "Support".

The same service serves the admin page, `/admin/` for its admins only
(`donate/admin.py`, `donate/admin/`; signing in with a passkey, or a password
and an authenticator code, and invites for a new admin:
`donate/admin_auth.py`): unique visitors, how they found the
site, countries, browsers and devices, downloads and tips and monthly support by day,
week, month and year, and every visit of the last 13 months one by one -
sorted, grouped and filtered as wanted (`donate/admin/logtools.js`) - and an
Analysis tab (`donate/admin/analysis.js`, `donate/analysis.py`) that breaks
those visits down by any data point, split by a second, with patterns,
charts and what visitors do on the pages (`public/events.js`,
`donate/stats_events.py`). A
page view counts when the page has opened in a real browser and told
`/hit` so (`public/nav.js`, no cookie, no id); `donate/stats_collect.py`
reads nginx's log of it - no address kept, robots left out, and the ones
that do run pages (a browser without a screen, or one years out of date,
unless a pointer, touch or key said a person was there) kept and marked
but not counted. Signed in once,
a browser stays signed in (a cookie signed by the service), and a new
tip comes as a notification on the devices that turned them on (the
browser's own Web Push) and as a note on the site's own pages there. Setting it up: `DEPLOY.md`, "Statistics
and the admin page" and "Signed in for good, and told of support".

Config templates live in `deploy/` with role-based names —
`proxy-iptv.dope.rs.conf` and `content-iptv.dope.rs.conf`. Replace the
`CONTENT_HOST` placeholder and the web-root paths with your own; nothing in the
repo hardcodes a real host or address.

## Deploy

Moving the live site from the PHP version: follow [DEPLOY.md](DEPLOY.md). A
new install:

1. **DNS** — point `dopeiptv.org` at the same public IP as your other sites.

2. **Content server** — `pkg install jq`. Copy `public/` to your web root
   (e.g. `/usr/local/www/iptv/public`), and `site/` and `sync-releases.sh` one
   level above it (so they are *not* web-served). Give the tree to a user of
   its own - not `www`, which php-fpm runs as for other sites - and install
   `deploy/content-iptv.dope.rs.conf` into your nginx `conf.d/`:

   ```sh
   pw useradd iptv -c "dopeiptv.org sync" -d /nonexistent -s /usr/sbin/nologin
   chown -R iptv:iptv /usr/local/www/iptv
   ```

3. **TLS-front proxy** — install `deploy/proxy-iptv.dope.rs.conf`, set
   `CONTENT_HOST` to the content server's internal address:port, add the CSP
   row from `deploy/csp.snippet.txt` to your `map $server_name $csp_policy`
   block, issue a cert and reload:

   ```sh
   certbot certonly --webroot -w /usr/local/www/letsencrypt -d dopeiptv.org
   ```

4. **Cron the release sync** on the content server (every 15 min, in
   `/etc/crontab`):

   ```
   0,15,30,45 * * * * iptv /bin/sh /usr/local/www/iptv/sync-releases.sh >> /var/log/iptv-sync.log 2>&1
   ```

   Run it once by hand before nginx serves the site - there are no pages
   until it has run - and **as the user the cron line names**, or the files
   land owned by root and the next scheduled run cannot write over them:

   ```sh
   cd /usr/local/www/iptv && su -m iptv -c 'sh sync-releases.sh'
   ```

   It only downloads assets that are missing or changed, checks each against
   GitHub's SHA-256, does atomic swaps, prunes files no longer in the latest
   release, and leaves every page exactly as it was (exit status 1) when
   GitHub is unreachable or a download fails halfway - a half-built release
   is never published with an installer silently missing.

5. **Rotate the download log** with newsyslog, keeping `.0` uncompressed so
   the sync can finish reading it - the line is in DEPLOY.md, step 10.

## Where this actually runs

Written down because it has cost time twice, and neither time was the
setup's fault - it was not knowing the setup.

**The site lives inside a Bastille jail called `pontusweb`, not on the
host.** Every path below is the jail's own view of the filesystem:

| | |
|---|---|
| host | `slimfree` |
| jail | `pontusweb` |
| jail root, from the host | `/usr/local/bastille/jails/pontusweb/root` (ZFS `iris-data/...`) |
| site, inside the jail | `/usr/local/www/iptv/` |
| checkout, inside the jail | `~/dopeiptv` — lower case; the host's is `~/dopeIPTV` |

So `/usr/local/www/iptv/` typed on the HOST is not the live site. Get into
the jail first, and note that `bastille console` opens an interactive
shell - anything pasted after it on the same line is swallowed by the
host's shell and never runs:

```sh
bastille console pontusweb     # opens a shell; type the rest INSIDE it
```

**The sync's cron line is in `/etc/crontab`, with a user field**, not in
anybody's personal crontab:

```
0,15,30,45 * * * * iptv /bin/sh /usr/local/www/iptv/sync-releases.sh >> /var/log/iptv-sync.log 2>&1
```

`crontab -l` therefore prints nothing for root AND nothing for iptv, which
looks exactly like "there is no cron job" and is not. The sync runs as
`iptv`, and nothing in `crontab -l` says so.

## Updating the site

Copy the code, never the live data, and render at once. Inside the jail:

```sh
bastille console pontusweb
cd ~/dopeiptv
git checkout website          # the site is NOT on main or testing
git pull origin website
cp -R public/. /usr/local/www/iptv/public/
cp -R site donate sync-releases.sh /usr/local/www/iptv/
cd /usr/local/www/iptv && su -m iptv -c 'sh sync-releases.sh'
```

When `donate/` changed, `service iptv_donate restart` as well.

The last line puts a change on the pages now rather than at the next
quarter-hour - and a changed `style.css` or JS file is only linked under its
new address (below) once the pages are rendered again.

`public/.` with the trailing dot copies the CONTENTS. `cp -R public/ dest/`
on FreeBSD puts the directory inside the target instead, giving you
`public/public/`.

Or from the host, without entering the jail - same commands, one prefix:

```sh
J=$(jls -j pontusweb path)
cp -R public/. "$J/usr/local/www/iptv/public/"
```

(Prefer doing it inside the jail. `chown iptv:iptv` from the host resolves
`iptv` out of the HOST's passwd, which does not have it; inside the jail it
cannot be wrong.)

These are live data that the repo does not carry, and a deploy must leave
them alone:

| | where | why |
|---|---|---|
| `downloads.json` | one level **above** the web root | the download counts, kept by the sync from nginx's `/dl.php` log |
| `.downloads-log.state` | next to it | how far the sync has read that log |
| `public/releases.json` | written by the sync | git-ignored; a stale stub once replaced it and the page showed v0.7.0 |
| `public/i18n/` | written by the sync | the pages themselves |
| `public/files/` | the mirrored installers | the repo has only a `.gitignore` |

**Do not use `cp -p`.** It preserves ownership and mode *from the checkout*,
where everything belongs to root - which once silently took `public/files/`
away from the user the cron sync runs as, and every asset then failed to
mirror with "Permission denied". Plain `cp -R` overwrites file contents and
leaves existing directories' ownership alone. If it has already happened:

```sh
chown -R iptv:iptv /usr/local/www/iptv
```

`--delete` (rsync) is likewise never right here: the live data is absent from
the source tree, so it would delete all of it.

**Before a change to the pages or `style.css` goes out**, `python3 -m pytest
donate` checks the stylesheet against the pages (`donate/test_site_layout.py`):
every `#id` a rule names must be one a page or script makes - a renamed
section once left three rules styling nothing - and "Built on" (`#built`)
may be pulled up only right under the support band (`#support + #built`).
The band is there only once a way to pay is set up; without it the pull put
the row halfway over the downloads' edge. A part whose place depends on what
is before it gets its rule from that neighbour (`A + B`), never on its own.

## Trakt sign-in comes back through here

`/trakt/callback/` is part of the app, not of the site: it is the redirect
URI registered with dopeIPTV's Trakt app. A Trakt app that every account can
sign in to may only redirect to an `https://` address, so Trakt sends the
browser here, and nginx hands the answer straight on to the app on the
user's own machine (`http://127.0.0.1:41794/callback`). It passes on `code`,
`state`, `error` and `error_description` only, to that one fixed address -
no open redirect - and the code is useless without the app's PKCE verifier.

If that location goes missing from the nginx config, signing in to Trakt
through the browser fails for every user (the code sign-in still works).
Keep it, and change its address only together with the app
(`trakt.WEB_REDIRECT_URI`) and the Trakt app's registered redirect URI.
nginx answers `/trakt/callback` with a 301 to `/trakt/callback/`, keeping the
query. A quick check after a deploy:

```sh
curl -sI 'https://dopeiptv.org/trakt/callback/?code=x&state=y' | grep -i location
# location: http://127.0.0.1:41794/callback?code=x&state=y
```

## The legal pages

`/privacy/`, `/terms/`, `/support/` and `/about/` - the privacy policy,
the terms of use, support (a tip, monthly support, the supporter key,
refunds and disputes), and the company details the Swedish E-Commerce Act
asks for. Linked from a row at the foot of every page (`/donations/` sends
visitors to `/support/`). Every footer names Solecraft AB with its
organisation number, address and e-mail, and says in the reader's language
that dopeIPTV does not contain, sell or link to any channels or other
content (`foot_made_by`, `foot_nocontent`).

- The texts are HTML fragments in `site/legal/sv/` and `site/legal/en/`.
  **The Swedish text governs**; the English is its translation, and the two
  change together. Legal text is never machine-translated: the other 25
  languages render the English text inside their own header and footer,
  with a line in their language saying so (`legal_only_en`), and their
  canonical points at the English page.
- Titles, descriptions and the "last updated" date are in
  `site/legal.json`; change the date (`updated` and both `text.*.updated`)
  whenever the content changes.
- What the privacy policy says is what the code does: the statistics
  (`donate/stats_collect.py`), the reports (`donate/feedback.py`), sync,
  the logs in `deploy/`, and in the app `core/report.py`, `core/updates.py`
  and the services it calls. Change one, change the other.
- Yellow `<mark class="todo">` boxes are details still to be filled in
  (the address, the payment provider, where the server is, log retention,
  the VAT number) - none should be left when the pages go live.
- They need the content server's two `legal` locations
  (`deploy/content-iptv.dope.rs.conf`): copy it and reload nginx once.
- The support page's ways to pay come from `site/render.jq` (where the
  text says `<!--SUPPORT-OPTIONS-->`), with the links from the server.

## Still to add (drop-in, no code changes)

- **`og-image.png`** (1200×630) and **`favicon.png` / `apple-touch-icon.png`**
  in `public/` — referenced by the meta tags and manifest. The SVG favicon works
  today; the PNGs are for older clients and social cards.

## SEO built in

Unique `<title>` + meta description per page and language, a canonical URL
and a full hreflang set on every page (home and help, 27 languages), Open
Graph + Twitter cards, `SoftwareApplication` JSON-LD, `robots.txt`, a
generated sitemap (`sitemap.xml` is an index pointing at `/sitemap.php`),
semantic headings, `<html lang>` per language, and fast static delivery
(gzip + cache headers). The root URL picks a language from the visitor's
cookie or browser and says so with `Vary`; crawlers get English there and
find every translation at its own `?lang=` URL.

Keep real broadcasters out of the page - text, mock-ups and screenshots. A
channel brand next to "IPTV" is what pirate services advertise with and what
rights holders' automated takedown requests look for; the demo channels
(Big Buck Bunny, Sintel, Tears of Steel) are free to show.

The name is the first word of each page's `<h1>` (it is what people type
into a search box, and Google splits the bare word "dopeiptv" into "dope
iptv" unless the page says otherwise), and the JSON-LD gives the lowercase
spelling as an `alternateName`. `/index.php` and `/help/index.php` redirect
to `/` and `/help/`, so each page has one address.

After a deploy, resubmit `https://dopeiptv.org/sitemap.xml` in Google Search
Console and Bing Webmaster Tools.

CSS and JS are cached for a year: the sync links them from every page with
the file's content hash in the URL (`?v=`), so a changed file always gets a
new address. Anything linked without it would be stuck for a year.

Server-side lines that belong with this, all in the templates in `deploy/`:

- content server: `error_page 404 /404.html;` and the two cache rules;
- TLS front: `http2 on;`, and the port-80 block that sends `http://` to
  `https://` (skip that one if `nginx.conf` already redirects all port-80
  traffic).

## Local preview

The pages are plain files once a sync has rendered them, and their links
are absolute, so any static file server rooted at `public/` shows them:

```sh
cd public && python3 -m http.server 8000
# open http://localhost:8000/i18n/en/index.html (or i18n/sv/, i18n/ar/, ...)
```

The sync itself needs FreeBSD's `fetch`, `sha256` and `md5`; elsewhere, run
it on the server and copy `public/i18n/` back.
