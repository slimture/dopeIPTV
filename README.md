# iptv.dope.rs — the dopeIPTV website

A single-page, responsive landing site (dark "broadcast console" theme) with a
download list that auto-mirrors the latest GitHub release. Static files only:
no PHP, no framework, no build step. A POSIX `sh` script with `fetch(1)` and
`jq` mirrors the release and renders every page in every language; nginx
serves them. Designed to sit inside a strict same-origin CSP.

```
website/
├── public/                 → the content server's web root
│   ├── style.css           broadcast-console theme, light + dark
│   ├── app.js              OS-detect for the hero button (progressive enhancement)
│   ├── nav.js, help.js     the narrow-window menu and the help search
│   ├── favicon.svg         accent-amber TV glyph
│   ├── favicon.ico         16/32/48 px, for clients that ask for it blind
│   ├── site.webmanifest    PWA/icon metadata
│   ├── robots.txt          allows all + sitemap
│   ├── sitemap.xml         index → /sitemap.php (nginx serves the rendered one)
│   ├── i18n/               every page in every language, rendered by the sync (git-ignored)
│   ├── releases.json       written by the sync (never committed)
│   ├── files/              mirrored release assets land here (git-ignored)
│   └── screenshots/        drop real PNGs here (see below)
├── site/                   what the pages are made from - not web-served
│   ├── templates/          index.html, help.html, 404.html and nav.html
│   ├── lang/*.json         the texts, one file per language (en.json is the source)
│   ├── i18n.json           language names, which are right-to-left, ICU locales
│   ├── help.json           the help page's sections and topics
│   ├── formats.json        dates and numbers per language, as ICU 76.1 writes them
│   └── render.jq           the renderer
├── sync-releases.sh        cron: latest release → files/ + releases.json + i18n/
├── DEPLOY.md               moving the live site from the PHP version, step by step
└── deploy/                 nginx + CSP config templates (two roles)
```

## Architecture

Two nginx roles (adapt to your own setup):

- a **TLS-front proxy** that terminates HTTPS for `iptv.dope.rs` and reverse-
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

Nothing for this site talks to php-fpm.

Config templates live in `deploy/` with role-based names —
`proxy-iptv.dope.rs.conf` and `content-iptv.dope.rs.conf`. Replace the
`CONTENT_HOST` placeholder and the web-root paths with your own; nothing in the
repo hardcodes a real host or address.

## Deploy

Moving the live site from the PHP version: follow [DEPLOY.md](DEPLOY.md). A
new install:

1. **DNS** — point `iptv.dope.rs` at the same public IP as your other sites.

2. **Content server** — `pkg install jq`. Copy `public/` to your web root
   (e.g. `/usr/local/www/iptv/public`), and `site/` and `sync-releases.sh` one
   level above it (so they are *not* web-served). Give the tree to a user of
   its own - not `www`, which php-fpm runs as for other sites - and install
   `deploy/content-iptv.dope.rs.conf` into your nginx `conf.d/`:

   ```sh
   pw useradd iptv -c "iptv.dope.rs sync" -d /nonexistent -s /usr/sbin/nologin
   chown -R iptv:iptv /usr/local/www/iptv
   ```

3. **TLS-front proxy** — install `deploy/proxy-iptv.dope.rs.conf`, set
   `CONTENT_HOST` to the content server's internal address:port, add the CSP
   row from `deploy/csp.snippet.txt` to your `map $server_name $csp_policy`
   block, issue a cert and reload:

   ```sh
   certbot certonly --webroot -w /usr/local/www/letsencrypt -d iptv.dope.rs
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
cp -R site sync-releases.sh /usr/local/www/iptv/
cd /usr/local/www/iptv && su -m iptv -c 'sh sync-releases.sh'
```

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
curl -sI 'https://iptv.dope.rs/trakt/callback/?code=x&state=y' | grep -i location
# location: http://127.0.0.1:41794/callback?code=x&state=y
```

## Still to add (drop-in, no code changes)

- **Screenshots** — put real PNGs named `main.png`, `epg.png`,
  `timeshift.png` and `recordings.png` (1280×800) in `public/screenshots/`;
  the next sync shows each one in place of its placeholder.
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

After a deploy, resubmit `https://iptv.dope.rs/sitemap.xml` in Google Search
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
