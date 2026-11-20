# dopeiptv.org — the dopeIPTV website

The site's address since November 2026; the old one, iptv.dope.rs, sends
every request on to the same path here.

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
  radius); panels that open over the page (the menu, the language picker, a
  download card's other formats) 16px round with rows of a finger's height
  and an icon in a tinted square, no chevron (in a menu it says a submenu
  opens; every row goes straight to its page); hover is the accent's light
  tint everywhere.
- **The downloads**: a card per system, all built alike so their rows line
  up - name, how it installs, the processor (chosen without script), one
  button with the file's size in the reader's own numbers, its format, the
  other formats in that panel, how to install. On a phone one card instead:
  where to download, and "Send the link to your computer" first, the menu's
  own words (`menu_send_link`); the downloads fold out per system behind it.
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

nginx serves the files; how the servers are set up, and how the site goes
out onto them, is not in this repository - it is kept privately with the
maintainers. Ask if you need to know.

What the PHP version worked out on every request is split in two:

- **Once per sync**, every 15 minutes: `sync-releases.sh` mirrors the release
  and renders every page for every language into `public/i18n/<lang>/`, plus
  the sitemap. Download counts come from nginx's log of `/dl.php`. A release
  is mirrored only when its tag is a version number and its `SHA256SUMS` is
  signed with dopeIPTV's release key (`donate/release_sums.py`, the public
  half as in the app's `dopeiptv/core/update_keys.py` - a new key goes in
  both), and only files on that list with its checksum; otherwise the
  release already here stays.
- **Per request**, nginx picks the language - `?lang=` (remembered in a
  cookie for a year), else the cookie, else the first language in the
  browser's `Accept-Language` that the site has, else English - and answers
  `/dl.php` (on to the file) and `/trakt/callback/` (on to the app) itself.

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
and give the supporter key for it.

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
counts nginx's line for it in memory - no address kept, robots left out, and the ones
that do run pages (a browser without a screen, or one years out of date,
unless a pointer, touch or key said a person was there) kept and marked
but not counted. Signed in once,
a browser stays signed in (a cookie signed by the service), and a new
tip comes as a notification on the devices that turned them on (the
browser's own Web Push) and as a note on the site's own pages there.

## Before a change goes out

`python3 -m pytest donate` checks, among the rest, the stylesheet against
the pages (`donate/test_site_layout.py`): every `#id` a rule names must be one a page or script makes - a renamed
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
content (`foot_nocontent`).

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
  the server's logs, and in the app `core/report.py`, `core/updates.py`
  and the services it calls. Change one, change the other.
- Yellow `<mark class="todo">` boxes are details still to be filled in
  (the address, the payment provider, where the server is, log retention,
  the VAT number) - none should be left when the pages go live.
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

CSS and JS are cached for a year: the sync copies each into
`public/assets/` under its content hash (`style.<hash>.css`) and links the
copy from every page, so a changed file always gets a new address - and an
address always the same bytes (a `?v=` on the live file could be cached with
the next deploy's content). Copies no page has linked for two weeks are
pruned. Anything linked without a hash would be stuck for a year.

## Local preview

The pages are plain files once a sync has rendered them, and their links
are absolute, so any static file server rooted at `public/` shows them:

```sh
cd public && python3 -m http.server 8000
# open http://localhost:8000/i18n/en/index.html (or i18n/sv/, i18n/ar/, ...)
```

The sync itself needs FreeBSD's `fetch`, `sha256` and `md5`; elsewhere, run
it on the server and copy `public/i18n/` back.
