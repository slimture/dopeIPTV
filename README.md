# iptv.dope.rs — the dopeIPTV website

A single-page, responsive landing site (dark "broadcast console" theme) with a
download list that auto-mirrors the latest GitHub release. No build step, no
framework — plain PHP + CSS + a few lines of JS, designed to sit inside a strict
same-origin CSP.

```
website/
├── public/                 → deploy to your content server's web root
│   ├── index.php           landing page (server-renders releases.json)
│   ├── style.css           broadcast-console theme, light + dark
│   ├── app.js              OS-detect for the hero button (progressive enhancement)
│   ├── favicon.svg         accent-amber TV glyph
│   ├── site.webmanifest    PWA/icon metadata
│   ├── robots.txt          allows all + sitemap
│   ├── sitemap.xml         single URL
│   ├── releases.json       written by the sync script on the server (never committed)
│   ├── files/              mirrored release assets land here (git-ignored)
│   └── screenshots/        drop real PNGs here (see below)
├── sync-releases.php       cron: pull latest release → files/ + releases.json
└── deploy/                 nginx + CSP config templates (two roles)
```

## Architecture

Two nginx roles (adapt to your own setup):

- a **TLS-front proxy** that terminates HTTPS for `iptv.dope.rs` and reverse-
  proxies to
- a **content server** that serves the PHP site on an internal port (`:8081`
  in the template) and runs the release-sync cron.

Config templates live in `deploy/` with role-based names —
`proxy-iptv.dope.rs.conf` and `content-iptv.dope.rs.conf`. Replace the
`CONTENT_HOST` placeholder and the web-root paths with your own; nothing in the
repo hardcodes a real host or address.

## Deploy

1. **DNS** — point `iptv.dope.rs` at the same public IP as your other sites.

2. **Content server** — copy `public/` to your web root (e.g.
   `/usr/local/www/iptv/public`) and `sync-releases.php` one level above it
   (so it is *not* web-served). Install `deploy/content-iptv.dope.rs.conf`
   into your nginx `conf.d/`, then reload nginx.

3. **TLS-front proxy** — install `deploy/proxy-iptv.dope.rs.conf`, set
   `CONTENT_HOST` to the content server's internal address:port, add the CSP
   row from `deploy/csp.snippet.txt` to your `map $server_name $csp_policy`
   block, issue a cert and reload:

   ```sh
   certbot certonly --webroot -w /usr/local/www/letsencrypt -d iptv.dope.rs
   ```

4. **Cron the release sync** on the content server (every 15 min):

   ```
   0,15,30,45 * * * * /usr/local/bin/php /path/to/sync-releases.php >> /var/log/iptv-sync.log 2>&1
   ```

   Run it once by hand first to populate `files/` and `releases.json` - **as
   the same user the cron line names**, or the files land owned by root and
   the next scheduled run cannot write over them:

   ```sh
   su -m www -c '/usr/local/bin/php /usr/local/www/iptv/sync-releases.php'
   ```

   It only downloads assets that are missing or changed, does atomic swaps,
   prunes files no longer in the latest release, and keeps the page working
   (from the last JSON) even if GitHub is unreachable - including when a
   download fails halfway, which it treats as a half-built release rather than
   publishing a list with an installer silently missing. Set `GITHUB_TOKEN` in
   the environment to raise the API rate limit if needed.

## Updating the site

Copy the code, never the live data:

```sh
cd ~/dopeiptv
git checkout website          # the site is NOT on main or testing
git pull origin website
cp -R public/. /usr/local/www/iptv/public/
cp sync-releases.php /usr/local/www/iptv/
```

Three things on the server are live data that the repo does not carry, and a
deploy must leave them alone:

| | where | why |
|---|---|---|
| `downloads.json` | one level **above** the web root | the download counter, written by `dl.php` |
| `public/releases.json` | written by the sync | git-ignored; a stale stub once replaced it and the page showed v0.7.0 |
| `public/files/` | the mirrored installers | the repo has only a `.gitkeep` |

**Do not use `cp -p`.** It preserves ownership and mode *from the checkout*,
where everything belongs to root - which silently took `public/files/` away
from the `www` user the cron sync runs as, and every asset then failed to
mirror with "Permission denied". Plain `cp -R` overwrites file contents and
leaves existing directories' ownership alone. If it has already happened:

```sh
chown -R www:www /usr/local/www/iptv/public/files/
chown www:www /usr/local/www/iptv/public
```

`--delete` (rsync) is likewise never right here: the live data is absent from
the source tree, so it would delete all of it.

## Still to add (drop-in, no code changes)

- **Screenshots** — put real PNGs in `public/screenshots/` and swap the four
  `<div class="ph">…</div>` placeholders in `index.php` for
  `<img src="/screenshots/main.png" alt="dopeIPTV main window" width="1280" height="800">`.
- **`og-image.png`** (1200×630) and **`favicon.png` / `apple-touch-icon.png`**
  in `public/` — referenced by the meta tags and manifest. The SVG favicon works
  today; the PNGs are for older clients and social cards.

## SEO built in

Unique `<title>` + meta description, canonical URL, Open Graph + Twitter cards,
`SoftwareApplication` JSON-LD, `robots.txt`, `sitemap.xml`, semantic headings,
`lang="en"`, responsive viewport, and fast static delivery (gzip + cache
headers). After launch, submit the site in Google Search Console and Bing
Webmaster Tools and request indexing of `https://iptv.dope.rs/`.

## Local preview

```sh
cd public && php -S localhost:8000
# open http://localhost:8000
```
