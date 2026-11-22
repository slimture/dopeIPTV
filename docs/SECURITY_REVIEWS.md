# Security and privacy reviews

Every review round of dopeIPTV's security and privacy: who looked, at what,
what they found and what was done about it. Nothing is left out because it
was awkward. Before every release a round is made with the review kit
(AGENTS.md, rule 13: `tools/review_kit.py` on the `website` branch gives
every question of the transparency page with its code, and the questions
about the rest of the app); its findings are added here.

Severity: **critical** - anyone could take something that matters now;
**high** - a real way in or out under conditions an attacker can make;
**medium** - a real weakness that needs something unusual to use;
**low** - defence in depth; **info** - wording, documentation or a limit
written down.

## November 2026 - independent AI reviews of 1.6.7 and 1.6.8

The owner gave the transparency page's code and questions to an independent
AI assistant, area by area, for 1.6.7 and then for 1.6.8, and an overall
assessment of each. The findings, and what came of them:

| Area | Finding | Severity | Done |
|------|---------|----------|------|
| Error reports | The last scrub before sending did not know the person's own values (source names, logins, addresses) - only the patterns | medium | 1.6.9: `report.send`/`send_status` take `personal` as a required argument; the last pass scrubs with them |
| Error reports | `build_check.secure` checked the scheme, not that the host is dopeiptv.org | low | 1.6.9: https to dopeiptv.org itself only (loopback for tests) |
| Error reports | The pattern scrub missed quoted and JSON values, Authorization and Cookie lines, bearer tokens, addresses without a scheme | medium | 1.6.8 |
| Settings sync | The slot alone let anyone who learnt it read, replace or delete a household's copy | high | 1.6.9 + website: a credential from the code (`auth_of`, `X-Sync-Auth`) the server keeps only a hash of; a bound slot refuses requests without it |
| Settings sync | A slot only an older app has written stays unbound, so its slot alone still opens it | medium | 1.6.9: the first round of a computer with this version writes the slot with its credential even when nothing changed, binding it at once |
| Settings sync | Whoever held the sync code could change a playlist's proxy, headers, rewrite, DNS or trusted certificate in the copy, and it was taken without a word (red team) | high | 1.6.9: those settings are held back, said on a card and taken only on Review's Apply |
| Settings sync | The server could hand back an earlier copy unnoticed | medium | 1.6.8: a counter sealed in every copy; an earlier copy is told and this computer's put back |
| Settings sync | The counter is no freshness guarantee for a computer that has never synced, and the server can withhold a copy | info | Written down: inherent to a server that stores; the transparency page says so |
| Settings sync | Requests could follow a redirect or go over plain http | low | 1.6.8: https only, no redirect followed |
| Watch together | Room codes of 8 characters (about 40 bits) and 120 000 rounds were guessable offline from the room id | high | 1.6.8: 10 characters (about 50 bits), 600 000 rounds; old codes still join |
| Watch together | Replayed messages were acted on | medium | 1.6.8: a sequence number per sender; 1.6.9: the relay's backlog at joining not read, messages out of their time ignored |
| Watch together | Every member holds the room key, so one member can write as another | medium | 1.6.9: member ids the hash of an Ed25519 key of their own, every message signed (1.6.8 members protected once they update) |
| Watch together | Padding not checked strictly on opening | low | 1.6.9: one of the padded sizes, zeros after the text |
| Watch together | The relay sees who is in a room with whom, when and from which address | info | Written down: a relay has to; it keeps it in memory only and logs nothing |
| Private viewing | A resume point, a log line or Trakt/Simkl could still name a private playback after the switch was turned off | medium | 1.6.8: kept per playback (`playback_private`, `private_key`, `for_log`) |
| Private viewing | Other reporters, metadata lookups and logs outside `for_log` were not shown to be covered | medium | 1.6.9: every reporter, store and lookup audited against the playback - the automatic watched mark, Trakt's start after its lookup, a title cast again, the radio click, TMDB, stream health and details, track, subtitle, delay and teletext choices, a series' season, the folder opened, the wake lock's reason and log lines now ask the playback; a private channel's rewind buffer deleted when left, at private viewing's end and before the app closes. The system's media controls show "dopeIPTV" and the remote API's status `private: true` for a private playback |
| Updates | A build without the release key trusted the site's checksum | high | 1.6.8: no key, no update |
| Updates | A download could follow a redirect to plain http, and was not size-limited while read | low | 1.6.9: redirects by hand, each https; stops past the file's size |
| Updates | An older signed release could be offered | info | Checked: only a newer version is offered, the asset name and the signed list must match, and the new copy's self-check confirms its version |
| Website statistics | One secret kept 25 months made visitor ids recomputable; detailed visits kept 400 days; raw logs a week; the front server's log 30 days | high | 1.6.8 + website: a key per month, deleted after the month after; visits 30 days; raw logs two days; the front server logs nothing |
| Website statistics | Visitor ids kept 25 months; rare combinations in anonymous visits | medium | Website: a month's ids go with its key (only counts kept); finished months made k-anonymous (k = 5) |
| Website statistics | Referrer and URL queries in the raw log; the content server's default log | low | Website: queries and fragments never logged; no other log |
| Website statistics | A completed payment was noted on the visit | medium | 1.6.8 + website: counted per day only |
| Transparency page | "Every destination", "built from exactly this code" and the site's own checksum check were claimed more strongly than shown | info | Website: what a search found, not a promise; builds not yet reproducible, said so; the site's check shown in code |
| Players | A playlist line such as `--log-file=` or `--include=` was read as an option by an external mpv/VLC or the mpv recorder (live attack, sweeps; proven) | high | 1.6.9: the address after `--`, and nothing but a stream's address or a file's path handed over |
| Players | ffmpeg read `concat:`, `subfile:`, `unix:` and local files behind an address | medium | 1.6.9: a stream's protocols only, everywhere ffmpeg is started |
| Local servers | The loopback proxy took anyone's requests; one address could open unlimited connections; a forged Host reached the token-bearing addresses | medium | 1.6.9: a per-run key, connection caps, time limits, `host_for_addresses` |
| Recordings | A batch file after a recording ran a title's `&` / `%VAR%`; a NUL or `..` in a name or folder | high (Windows) | 1.6.9: text only; names and folders made safe |
| Backups | A restore turned on server mode or joined a sync unnamed | medium | 1.6.9: named before it is put back |
| Backups | A full backup held every login as plain text, with no way to lock the file | medium | 1.6.9: an optional password - ChaCha20-Poly1305 under PBKDF2-HMAC-SHA256 (600 000 rounds), the header covered by the tag, test vectors; a weak password against an offline guesser written down (docs/SECURITY.md, "A backup's password") |
| Build chain | Downloads by "latest", actions by tag, unhashed pip installs, the update key in every build job | high | 1.6.9: SHA-256 pins, a hashed lock, actions by commit, read-only workflows, the key in one signing step |
| Updates | A taken-over site could offer an older signed release; a file lying in the download folder | medium | 1.6.9: never older than the copy running; a file of the updater's own |
| Player text | A station's or programme's title drawn as rich text over the picture | low | 1.6.9: plain text |
| Stream addresses | An `av://lavfi` channel address made the player write a file of the provider's choosing; `gopher://`, `edl://`, `memory://` reached past streams (input review; proven) | critical | 1.6.9: `core/stream_address.py`, checked where every address is handed out and in both players |
| Guide cache | The guide's index was unpickled with no restriction | high (needs local write) | 1.6.9: a plain format of its own, no pickle in the app |
| Pictures | A logo naming `/dev/zero` or `\\host\share` was read on sight; an SVG made Qt open files it named; no size cap | high | 1.6.9: `core/picture_files.py`, `core/svg_safe.py`, a 64 MB download cap and allocation limits |
| Answers | An endless M3U, Xtream or API answer filled memory; deep JSON raised past the handlers | high | 1.6.9: a ceiling on every answer read whole |
| Links | A quote in a `dopeiptv://` link could add `--server`, `--play` or Qt's `-platformpluginpath` (code run as the person); any web page could make the app open any address | high | 1.6.9: a link read alone, Qt given the program's name only, a play link asked about first |
| PINs | One SHA-256 per PIN (all 10 000 tried at once from the settings file); unlimited tries | medium | 1.6.9: PBKDF2 600 000 rounds; waits after two wrong tries |
| Local IPC | Discord's socket in /tmp and lircd's socket taken from anyone; a media server's token in the artwork address on the session bus | medium | 1.6.9: this user's own only; no login address on the bus |
| Casting | An SSDP or DLNA answer could send the app's requests (and the stream's address) anywhere; a device taking a known TV's name got the next cast | medium | 1.6.9: same host, no redirect, capped; a TV known by its own id |
| Parental lock | A locked category's title played without the PIN from History, Continue, Recently watched and Mine; a kids profile could rename an adult category past its filter; the sidebar search named locked channels (ui sweep; proven) | medium | 1.6.9: one lock filter for every reader; the provider's own name counts; names only |
| Settings from outside | A nav colour from sync or a backup was pasted into a style sheet, and a bad one stopped the app starting | medium | 1.6.9: `#rrggbb` only |
| Links | A sign-in page or a download from a service's answer was opened whatever its scheme | low | 1.6.9: https only (and this computer's http) |
| Devices | A SAT>IP server's description could name a local file or a share on another host as its channel list (providers sweep; proven) | high | 1.6.9: http(s) on the server's own host only |
| XML | The entity defences were bypassed by a `]` in a declaration, UTF-16 or UTF-7 | medium | 1.6.9: one shared check, expat reads the prolog (`core/safe_xml.py`) |
| Patterns | Quadratic regular expressions froze the app on hostile playlist text (proven) | medium | 1.6.9: linear patterns, compared with the old on real and made-up input |
| Cache | A source id from a backup or sync could name a file outside the cache folder | medium | 1.6.9: `core/source_files.file_part` |
| Discovery | Redirects followed and whole answers read; a crafted HDHomeRun reply ended the search | low | 1.6.9: no redirect, short reads, deadlines |
| Markup | Words from outside reached labels, tooltips and message boxes as rich text (ui sweep; proven on five sinks) | medium | 1.6.9: plain text everywhere, enforced by a whole-code-base test |
| Boxes | An Enigma2 box's screenshot was read and decoded whole on the window's thread | low | 1.6.9: 64 MB, three formats, size from the header, off the window's thread |
| Stream addresses | A provider's `ytdl://` address ran yt-dlp | low | 1.6.9: refused from a source |
| Threat model | The rules had no written threat model behind them | info | 1.6.9: docs/SECURITY.md |
| Logs | Logins outside an address (a bare query, a header, `key=value`, a MAC) reached the log; control characters were written as they came; log files had the default mode | medium | 1.6.9: masked, escaped, 0600 in 0700 |
| Updates | The update check followed dopeiptv.org's and GitHub's redirects anywhere, the yt-dlp download too | low | 1.6.9: the site's none, GitHub's https only |
| VPN guard | An address in another IPv4 spelling or an IPv4-mapped IPv6, and a network path, slipped past the guard | low | 1.6.9: `nerd.ipv4_any`, paths by their server |
| Numbers | "1e999", "Infinity", NaN or thousands of digits from a provider raised or overflowed | low | 1.6.9: `core/num.py` |
| Sign-in | Trakt's loopback sign-in took any request with a code and waited on a silent connection | low | 1.6.9: state checked, 5 s |
| Sharing | A logo, a source's name or the guide's icons carried a login to another device | medium | 1.6.9: left out |
| MQTT | A broker could hold the events thread and flood the app with commands | low | 1.6.9: bounded and paced |
| Local IPC | The command socket's last place was the shared temporary folder | low | 1.6.9: a folder of the user's own |
| Windows | System tools by bare name, ffmpeg and mpv from the current folder or a batch file | low | 1.6.9: full paths, programs only |
| Internet sources | A provider on the internet could send the player to a file share, a raw socket, WebDAV or a service on this computer (a printer's page, a cloud's metadata), its pictures and guide too | medium | 1.6.9: media protocols only, never this computer or link-local (AceStream's engine the one exception) |
| Settings files | A settings file on a drive without permissions stayed readable by others without a word | low | 1.6.9: said once, by name |
| Overall | The parts reviewed are not the whole attack surface: parsers of provider data, logins at rest, the local servers, the programs the app starts, the build chain | info | 1.6.9 (in progress): five more reviews - local servers and processes, untrusted input and secrets, supply chain and platforms, the website's services - with their findings to be added here |

## November 2026 - independent AI review of 1.6.9 (testing)

The owner gave the review kit's `ALL.txt` from `testing` to an independent
AI assistant, which scored each of its 18 parts and the whole against
[docs/SECURITY.md](SECURITY.md): **6/10 overall**. The findings, and what
came of them:

| Area | Finding | Severity | Done |
|------|---------|----------|------|
| The app's own requests | A public name could resolve to this computer or a link-local host (DNS rebinding), and requests followed a provider's redirect into this computer, the home network or a cloud's metadata service - `stream_address` judged only the address as written | high | 1.6.9: `core/dest_guard.py` - every request through the shared requests session and the route adapter checked on its socket after it connects, before a byte is sent; every redirect requests follows, and each one the cast follows by hand, judged by its target as written and as it resolves |
| yt-dlp | The app trusted the `SHA2-256SUMS` downloaded beside the binary: whoever could replace one could replace both | high | 1.6.9: the build checks yt-dlp's PGP signature (`tools/ytdlp_pin.py`, `gpgv`, the key's fingerprint in the app) and writes the SHA-256s into the signed release (`core/ytdlp_pin.py`); the app installs only those bytes and runs only a copy it marked itself |
| Build chain | Without a build key the build jobs - which run PyInstaller and other people's packages - were handed the update key | high | 1.6.9: never: the manifest is signed with `DOPEIPTV_BUILD_KEY` alone; without it a build calls itself modified and a tag's build stops. The owner makes the key (docs/MAINTAINING.md, "The release key") |
| Profiles | A kids profile could play local files, recordings and addresses opened by hand, a row of no category or of one the source does not list, and from the phone; it was made without a grown-up's PIN and left for a grown-up's profile freely | high | 1.6.9: one door in `_start_playback` (`_kids_may_play`), no local files or recordings on the phone, a category must be one the source lists; made only once a grown-up's PIN exists, left for a grown-up's profile and Settings opened only with it (a kids profile from before, in a household with no such PIN, is never locked in) |
| Server mode | The playlist's token, which every TV holds, also opened the server's page and settings | medium | 1.6.9: a token of its own for the page and `api/` (`nerd_server_admin_token`), never in a backup |
| Private viewing | The remote API's status and the events still carried the profile, cast device, position and length of a private playback; a private stream's address in the log kept its server's name | medium | 1.6.9: nothing of them; `url_for_log` says `<private>` |
| Error reports | An address that began as the version did (1.6.8.10) was left in a report as if it were the build's number | low | 1.6.9: only exactly this build's own four-part version |
| Pictures | A link inside a trusted picture folder (a shared picon folder, a share) could point at any file; a pipe or device there made the read wait | medium | 1.6.9: links followed only while they stay inside, or are the person's own file; looked at before opening, opened without following a link swapped in since |
| Backups | A backup without a password, holding every login, was made without a word; any password was taken | medium | 1.6.9: a warning first, Cancel the default; a password of at least 10 characters |
| Watch together | 1.6.7's 8-character codes still joined, and 1.6.8's unsigned members were taken for a release of overlap | medium | 1.6.9: both refused; an old code is said to be one |
| Website | `SOCKET_OPEN = yes` could open the service's socket to every user on the machine | low | website: gone; nothing opens it |
| Server mode | The HDHomeRun endpoints take no token | medium | Answered: Plex and other HDHomeRun clients send none; this computer's own networks only, never a web page or a name not a home's, paced (docs/SECURITY.md) |
| Links | `dopeiptv://channel/` plays without a question | low | Answered: only a channel the lists show, through the zap's own questions; a link with an address is asked about (docs/SECURITY.md) |
| Feedback | Replies to an answer are tied together by the code's hash | info | Answered: that is what lets a reply reach its thread; the hash says nothing of who, and the person's code stays on their computer |
| Player | mpv and FFmpeg resolve names and follow redirects inside themselves | medium | Written down: the app cannot see their sockets; FFmpeg is kept to a stream's protocols (docs/SECURITY.md, "The player and the recorder look names up themselves") |
| Limits | The ceilings let a hostile source make the app read and unpack up to 256 MiB, 1 GiB and 2 GiB | low | Lowered in the next round (32 MiB, 512 MiB); written down (docs/SECURITY.md, "A hostile source can make the app work up to its ceilings") |
| Home network | Plain http on the home network, cast addresses with a login, a relay that sees who is in a room, a sync server that can hold a copy back, files without owners on some drives | info | Written down, each with its reason (docs/SECURITY.md, Honest limits) |

## November 2026 - second independent AI review of 1.6.9 (testing)

The kit made again from `testing` (64956d7) and the website (737166a), given
to an independent AI assistant: **7/10 overall** against
[docs/SECURITY.md](SECURITY.md). What came of its findings:

| Area | Finding | Severity | Done |
|------|---------|----------|------|
| Casting | A Kodi typed as `https://` was asked over http, its login in the clear | high | 1.6.9: the scheme is kept (`KodiDevice.secure`, `parse_target`), stored with the device; never asked over http instead |
| Server mode | The HDHomeRun endpoints let any device on this computer's networks watch without a token | high | 1.6.9: the token is needed, in the tuner's own address (`/t/<token>/`); devices are let in without it only as the person says - the addresses listed, or every device on this computer's networks (what a server set up before 1.6.9 keeps); the UDP search answers only those let in |
| Remote API | No Host or Origin check on requests with the token | medium | 1.6.9: a Host that is not a home name, a page of another site (Origin, Sec-Fetch-Site) refused whatever the token |
| Network bridge | The loopback proxy started a thread for every connection before the key, without a limit | medium | 1.6.9: `MAX_SERVING` (64) at once, closed before any work beyond it; a request's head within `HEAD_TIMEOUT` in all, a trickle let go |
| Recordings | The folder check was by name: a link inside the recordings folder could send a recording elsewhere | high (conditional) | 1.6.9: no link on the way down, where it resolves still inside (`_stays_inside`), a link where the file goes a name taken; a folder others may write in logged |
| Settings files | A drive that keeps no permissions left the logins readable, with only a calm note | high (conditional) | 1.6.9: the window asks first - close the default, or go on with that file; server mode without a window does not start until told |
| Providers | A source on the internet could send the app to any device of the home network | medium | 1.6.9: never, unless the person lets that source in (`home_addresses`); a multicast relay at home (udpxy) still taken |
| Private viewing | Turned on mid-play, events still waiting named what plays | medium | 1.6.9: taken back unsent (`withdraw_titled`); what was sent already is said to be beyond reach |
| Error reports | Logins told in words, tokens without a name, other accounts' home folders and phone numbers survived the cleaning | medium | 1.6.9: each removed (`_SAID`, `_JWT`, `_PREFIXED`, `_LONG`, `_HOME_DIR`, `_PHONE`), with tests; the page's promise says the cleaning removes what it recognizes, which the person reads first |
| Sync | One code is the whole key, with no way to replace it | high (conditional) | 1.6.9: a new code from Settings > Sync; the old one's copy deleted |
| Release chain | The signing job runs on what the workflow says, with no person in the way | high (conditional) | 1.6.9: release.yml's `checksums` in the environment `release-signing`, nightly's `publish` in `nightly-signing`; the owner sets a required reviewer and moves the key there (docs/MAINTAINING.md, "The release key") |
| Website statistics | k=5 counted visits, not people; the numbers were kept long and could be tied back | medium | website: no address is written anywhere - the front server hands each request to the service's memory over a local socket, and the download counter has a log of its own without one; the month's key lives in memory only; a visit is kept with a visitor id of that day alone, its times in 10-minute steps, and sealed so only that day's key can tell two visits of one person apart; how many different people is a sketch per day (bits, no list, no ids); a day's lists keep no name fewer than five people saw once it is a month old, then the month's the same, so what is kept for years names nobody |
| Feedback | A thread's code hash linked its reports for 12 months | low | website: gone after 120 quiet days (`forget_codes`) |
| Website deployment | The CSP and who may reach the content server could not be seen in the repository | high (conditional) | a check of the live site's answers, run from outside after every deploy (the server's own repository) |
| Backups | A backup without a password, or a weak one | high (conditional) | Answered: a password of 10 characters or more, and without one a warning with Cancel the default (1.6.9); the hint now recommends one |
| Phone remote, casts | Tokens in plain http on the home network | high (on-path) | 1.6.9: the phone remote's key never crosses the network - after the `#` of the code's address, kept on the phone - and every request is signed with it (HMAC-SHA256 over method, address, time, nonce and body; a minute's window, each nonce once), a picture's address signed for a quarter of an hour, an address of before a new key at once, the calendar a read-only key of its own; a converted cast serves only its receiver's address. What plays is still readable on the way: written down (docs/SECURITY.md, "The home network's servers speak plain http") |
| Watch together, sync | The relay and the sync server see timing and sizes; they can drop or hold back | medium | Written down (docs/SECURITY.md, Honest limits); a rejoin in the same run still knows every message number taken (`together.remembered`), and a member of an older app is said, not silently left out |
| PINs, kids profiles | Four digits; a provider's categories decide what is for adults | medium | 1.6.9: a profile's PIN 4 to 8 digits, the parental PIN at least 4 characters (a longer one typed where a grown-up's is asked); written down (docs/SECURITY.md, "A PIN can be short", "A kids profile holds only with a grown-up's PIN") |
| Media libraries | mpv, FFmpeg and Qt parse hostile media natively | medium | Written down ("Bugs in the media libraries are theirs") |
| Private viewing | A private channel's rewind buffer was on disk until it was left | medium | 1.6.9: a channel private from its start keeps it in memory (Linux's `$XDG_RUNTIME_DIR`, this user's own tmpfs, a budget of its own: `dvr_store.memory_base`, `memory_budget`); elsewhere on disk, marked and deleted as before - written down |
| Limits | 256 MiB for an answer and 1 GiB for a source's lists let a few requests at once take the memory | low | 1.6.9: 32 MiB and 512 MiB (`_lazy_requests.RESPONSE_MAX`, `LIST_MAX`) |
| Media session | Any program on the session bus can drive the app | low | Answered: Settings > General turns the system's media controls off (docs/SECURITY.md, "Any program on the session bus can drive the app") |

## November 2026 - third independent AI review of 1.6.9 (testing)

The kit from `testing` (a47e613) and the website (147f133), given to an
independent AI assistant with the rule to review those commits alone:
**7/10, preliminary**. What came of its findings so far:

| Area | Finding | Severity | Done |
|------|---------|----------|------|
| Website statistics | One person whose address changed counted as several people, so one phone could pass k=5 | medium | website: each visit also carries its device's sealed mark (browser, language, exact screen - no address); the smaller of the two counts is taken; both deleted when the month is made anonymous |
| Website statistics | The page that linked (its path) was counted per day; a path can name a person | medium | website: kept nowhere; days and months kept before lose theirs at the next run; the privacy policy says so |
| Watch together | The note for an older, unsigned member never reached the person: such a frame was dropped before the room saw it | low | 1.6.9: the link says it once (`older_frame`, `Link`'s *on_older*), still taking nothing of it |
| Casting | Without the receiver's address the converter fell back to its token alone | low | 1.6.9: the first address that asks for the run's own stream becomes the only one (`CastBridge.admits`) |
| Phone remote | An attacker who can change the home network's traffic can change the page and read the key | medium (on-path, active) | Written down (docs/SECURITY.md, "The home network's servers speak plain http"): https needs a public name and a certificate service per computer; the remote reaches no login or file |

## November 2026 - fourth independent AI review of 1.6.9 (testing)

The kit from `testing` (d1e359c) and the website (b2b02c3): **7/10**; the
website's statistics 5/10. It found no way for someone far away to steal
a provider's login or slip in an update that is not signed. What it found,
and what was done:

| Area | Finding | Severity | Done |
|------|---------|----------|------|
| Website statistics | The days' sketches use the same two bits for a visitor all month: two quiet days side by side could show that someone came on both | medium | website: the sketches are kept only where the month's key is - in the service's memory or beside the key on a tmpfs - never on a disk or in a backup (`stats_collect.sketches_file`); the copy on the disk read into memory once and deleted |
| Website statistics | Five visitor or device marks are not five people: the five-people promise of the anonymous months could not be proven | medium | website: no row of a visit is kept past its days any more - neither in detail nor as anonymous rows; after 14 days only the day's counts stay (per way in too, `funnel_of`). The anonymous rows of before were made those counts and deleted; the policy no longer promises a number of people |
| Website statistics | A month summed the days' rarer names: one person on five days kept a rare site named | medium | website: a name is kept only from a day when five visitors brought it; nothing adds days up again under a name, and the months' lists of them are dropped (`fold_day`, `_old_rows_off`) |
| Website statistics | Detailed visits and their timing for 30 days help a guess at who it was; the policy said it could not be done | medium | website: 14 days (was 30), the start to the half hour (was ten minutes), no device mark, keys kept two months (was three); the policy and the transparency page say plainly that it is much harder, not impossible |
| Website server | The server's runbook still described a statistics log with addresses and a key file on disk | medium | the runbook: those parts rewritten, the old sections marked superseded with a warning never to make that log again; a test fails if an `install` or `echo` step names it |
| Website server | Whether only the front proxy reaches the content server cannot be seen in the repository | conditional | the check from outside asks the content server's port after every deploy; it answered nothing (run after the second round) |
| Private viewing | The runtime folder was checked to be on tmpfs, the folder written into was not: a link or a disk mounted under it would put the buffer on disk | medium | 1.6.9: the buffer's own folder checked before a byte is written - no link, this user's own, 0700, in memory where it really is (`dvr_store.memory_folder`); else on disk, marked and deleted, said in the log, no second connection |
| Phone remote | A web page that pointed its own name at this computer could make failed tries that shut the phone out for a while | low | 1.6.9: a Host that is no name of the home is refused (421) before anything is counted (`home_net.home_host`) |
| Phone remote | An attacker who controls the home network's traffic can change the page | documented | Accepted as written down (docs/SECURITY.md, "The home network's servers speak plain http"); the remote is off until turned on |

## November 2026 - fifth independent AI review of 1.6.9 (testing)

The kit from `testing` (8a1ee68) and the website (ee704ee): **8/10**;
private viewing 6/10, the website's statistics 7/10. Again no way for
someone far away to steal a provider's login or slip in an unsigned update.
What it found, and what was done:

| Area | Finding | Severity | Done |
|------|---------|----------|------|
| Private viewing | A cast's converter kept its work - HLS segments, the spool a pause is kept in - in the disk cache, private or not; private viewing turned on midway did not touch it | medium | 1.6.9: what plays privately is converted only in memory out of a disk's reach (`cast_bridge.private_cast_dir`, the Apple TV's bridge too), else the cast is refused with the reason; private viewing turned on sends a disk-converted cast again under that rule (`_cast_private_on`); a crash's leftovers in memory swept at the next start |
| Private viewing | tmpfs may swap: "in memory" was not "never on a disk" | medium | 1.6.9: a folder counts as memory only when swap cannot carry it to a disk - no swap, zram, dm-crypt (under LVM too), a swap file on such a device, or a tmpfs mounted `noswap` (`core/memory_safe.py`) |
| Private viewing | Too little memory or a failed check put a private buffer on the disk | low | 1.6.9: no buffer at all then (`dvr_pool.NoPrivateBuffer`, no connection opened for the try); a disk buffer of the channel from before goes as it is watched privately; mpv's own disk cache never for a private playback; private viewing turned on opens a disk-buffered channel again |
| Private viewing | Recordings and other outputs were not asked | low | 1.6.9: a recording while viewing privately is asked first, Cancel the default (`_rec_private_ok`, the phone and TV mode too); a fetched subtitle in memory or as bytes in mpv's address, never a file; no copy of a subtitle in the cache |
| Website statistics | `STATS_VISITS_DAYS` could keep visits past 14 days | low | website: never more than 14 (`VISITS_DAYS_MAX`), said at the start |
| Website statistics | A failed deletion of what should be gone was silent | medium | website: every one through `_gone` - said once, tried again every run; after each run `left_behind` lists what should no longer exist and `sweep_left` deletes it; what stays is said at the top of the admin page |
| Website statistics | The keys and sketches on a tmpfs could reach a disk through swap | medium | website: the server's swap encrypted with a one-time key (the server's runbook); `swap_state` asks the system, swap that keeps what it is given is said in the start line and on the admin page |
| Website statistics | The privacy policy said the key was never in a file, that a restart of the service forgets it, and named two statistics logs of before | medium | website: corrected in English and Swedish, the transparency page too; `test_policy_text.py` fails on the old sentences |
| Website server | The error logs kept a line with an address until removed by hand | low | website: both servers' error logs turned over every night, six days kept (the server's runbook) |
| Supply chain | The dependency audit passed when it could not ask | medium | 1.6.9: an audit that did not finish fails (exit 2: no answer after three tries, a package left unchecked); the release runs it too and is not signed until it passed (`release.yml`, `needs.audit.result`) |
| Media libraries, credentials on command lines, plain http at home, sync's first join, direct casting, parental denylist | Known limits | documented | As written down in docs/SECURITY.md; unchanged this round |

## November 2026 - sixth independent AI review of 1.6.9 (testing)

The kit from `testing` (0a9da33) and the website (771a074): **8/10**;
private viewing 6/10, casting 6/10, the website's statistics 7/10, every
other part 8 or 9. Again no way for someone far away to steal a
provider's login or slip in an unsigned update. What it found, and what
was done:

| Area | Finding | Severity | Done |
|------|---------|----------|------|
| Private viewing, casting | Private viewing turned on while a cast was being sent skipped the check, so a converter on the disk could go on | medium | 1.6.9: each converter run is watched by a thread of its own and stops by itself the moment private viewing begins, its files gone (`cast_bridge._watch`, `airplay_bridge._watch`, `private_stopped`) - a cast, a seek, a recast, a reconnect alike; a send on its way is looked at again once it is done (`_cast_send`'s `_private_check`); tests turn private viewing on at each of them |
| Private viewing, casting | A private cast's HLS in memory had no cap while it ran - a long film or a high bitrate could fill the memory | medium | 1.6.9: its folder measured once a second, the converter stopped past its room (`folder_bytes`, `private_cap`) |
| Casting | The Apple TV's bridge served anyone on the network who had its address | medium | 1.6.9: this computer and the first other address that asks for the run's stream alone, 403 for any other, said once a run (`airplay_bridge.admits`, `refused_peer`); the address's token as before |
| Credentials | A login of three characters or fewer was not masked by value in the log | low | 1.6.9: masked as a word of its own (`core/log.py`, `_short`) |
| Website statistics | A day's visits could stay almost 15 days | low | website: the 14th day back is gone at the first run that day - 14 dates, never more (`_forget_old`, `left_behind`: `<=`) |
| Website statistics | zram with a backing device counted as safe swap | medium, conditional | website: zram counts only without a disk behind it; dm-crypt followed through LVM and other mappings (`_block_encrypted`) |
| Website statistics | Swap that could not be asked gave no warning | low | website: said on the admin page as well as plain swap (`trouble`: "could not be asked") |
| Website server | The deployment's state cannot be read from the repository | documented | the runbook's checks after a deployment (the web server's configuration, the logs and their turnover, the service and swap) run on the server; the start line says the swap and how long visits are kept |
| Local servers | Server mode's tuner open to the whole home network | answered | Already off by default since 1.6.9 (`home_server.OPEN_OFF`): the tuner takes its token in its own address unless the person lets named devices or the home in |
| Profiles | A four-digit PIN is weak | answered | 4 to 8 digits; PBKDF2 with 600 000 rounds and growing waits after two wrong tries (`core/pins.py`); a longer one is the person's choice |
| Commands from outside | A link or the media keys change channel without asking | answered | A link that plays an address is asked first, the whole address shown; a channel change by `--channel` or the media keys comes from this user's own session (its socket and session bus), as a key pressed does |
| Viewing metadata | Titles go to TMDB even with the provider's artwork (Trakt's ids, the watched marks) | low | 1.6.9: **Provider artwork, TMDB off** asks TMDB nothing at all - no resolver, no chart, no picture moved onto image.tmdb.org (`services/coverart.OFF`); a picture the provider itself keeps on TMDB's server still loads from there |
| Player TLS, credentials on command lines, the OS keyring, default-encrypted backups | Known limits | kept | The maintainer's decision, each for what it would cost: checking certificates in the player stops a provider with a broken one from playing; a proxy in front of every recording and external player is one more thing to fail; the keyring differs on every desktop and is missing on some; a password chosen by default is a backup lost when it is forgotten. Each stays written down in docs/SECURITY.md as an honest limit |

## November 2026 - seventh independent AI review of 1.6.9 (testing)

The kit from `testing` (1ea3e89) and the website (4df5f39): **8/10**; 9 for
updates, connections, sync and Watch together, 7 or 8 elsewhere. No
critical weakness, no way found to run code or take a login from afar. It
confirmed the sixth round's fixes - the cast watches and their lock, the
in-flight check, the Apple TV bound to its first receiver, the short
logins, the 14 dates - and what it found:

| Area | Finding | Severity | Done |
|------|---------|----------|------|
| Private viewing, website statistics | zram whose `backing_dev` cannot be read counted as safe swap | medium | 1.6.9 and the website: missing (no writeback in the kernel) is safe, there but unreadable is not (`memory_safe.encrypted_block`, `stats_collect._block_encrypted`) |
| Local servers | A sign-in's error callback without the state could end a Trakt sign-in | low | 1.6.9: an error too must carry the state asked for (`oauth_loopback._taken`) |
| Viewing metadata | No switch that turns TMDB off | medium | Already in `testing` after the reviewed commit: **Provider artwork, TMDB off** (`services/coverart.OFF`) |
| Casting, private viewing | The watch looks once a second; the private cap is a budget stopped when passed, not a hard ceiling | medium | Answered: a run on the disk is stopped within the second, a private run's work within its room's second; written down so in docs/SECURITY.md |
| Server mode | A server set up before 1.6.9 keeps the tuner open to the home | medium | Answered: what it had, so Plex and the TVs it serves go on playing; Settings shows the choice |
| Credentials, player TLS, keyring, backups, LAN casting, direct cast addresses, a home source's protocols, player redirects, the website's visit detail, reproducible builds | Known limits | kept | The maintainer's decision: each change would cost playback, recordings or a restore, or add a question; all written down in docs/SECURITY.md |

## The end of the rounds for 1.6.9

Seven rounds, from 7/10 to 8/10 overall with every confirmed defect fixed
in code. What stays below 9 is what the project chose to keep - each
written down in docs/SECURITY.md with its reason. 1.6.9 is released from
here; the next round comes before a release that changes what the app
sends, keeps or serves.

## November 2026 - the website's own copy, read by an up-to-date AI (after 1.6.9)

The maintainer pasted what the Behind the scenes page's Copy everything
button gives (its questions and code, from `v1.6.9`) into an up-to-date AI
assistant. With only that, it saw neither the page's promises nor the
limits each part names, nor parts 4 and 5, so much of what it raised the
page already said (plain HTTP between the site's servers on one machine,
the error logs, the honest limits). Checked against the code, what holds:

| Area | Finding | Severity | Done |
|------|---------|----------|------|
| Logs | A line whose masking failed went out as it was | medium | After 1.6.9: withheld instead, a traceback too (`core/log.py`, `WITHHELD`) |
| Private viewing | A channel recorded or cast while watched privately kept writing its buffer to the disk after the recording or cast ended, until it was left | medium | After 1.6.9: opened again as the last of them lets go, as when private viewing is turned on (`dvr_pool.on_private_disk`) |
| The page | Its copy carried no promises or limits, parts 4 and 5 had no question, and several answers lacked their code | low | The website: each part's copy carries its promise, what we can know, its hosts and its limits with the rule for judging them; parts 4 and 5 have their questions; excerpts of `refuse_older` and `_get_https`, the settings file's question, the report sent on Send only, the relay's backlog left unread, private viewing turned on mid-play |
| Feedback, Watch together | The page did not say that feedback carries `"supporter": "yes"`, nor that the relay cannot check signatures | low | The website: both said, in English and Swedish |
| Updates, TMDB, the relay's clock, sync, the admin cookie, swap | Raised as unknown or open | answered | Already in the code: `selfupdate.refuse_older`, **Provider artwork, TMDB off**, the first answer's backlog left unread and the clock taken from it, the sync slot bound on its first write, the 600 a minute limit by address, the server's encrypted swap watched by `swap_state` |

## November 2026 - the website's parts read one at a time, and a CodeQL scan (after 1.6.9)

The maintainer gave each part of the Behind the scenes page, with its code
from `v1.6.9`, to an up-to-date AI assistant on its own, and ran GitHub's
CodeQL on `main`. Checked against the code, what holds:

| Area | Finding | Severity | Done |
|------|---------|----------|------|
| Updates | The signature covers the file, not the tag: a site or mirror taken over could offer an older release's signed file under a newer tag - a step back to a version with a known fault | medium | After 1.6.9: the signed file must name the release offered (`selfupdate.names_version`); a size beyond any release file is not fetched |
| Error reports | An IPv6 address ending in "::" (`2001:db8::`, `fe80::`) went through the cleaning, and `::ffff:192.0.2.7` left the IPv4's last three numbers | low | After 1.6.9: both go, in the app and in the site's second cleaning |
| Error reports | The window said "exactly what is sent" while the version, the system, a supporter's mark and the answer code's hash went beside the text unseen | low | After 1.6.9: the window says each (`report.sent_beside`); the page says the version and system |
| Error reports | Several reports' hashes are asked for in one request, and how long the limits hold an address was not said | info | The website: both said, the limits' windows and handlers in the excerpt |
| Private viewing | "Nothing written to a disk" while encrypted swap counts as memory; a cast or server mode holding the buffer keeps it on the disk; it does not work backwards | info | The website: "nothing unencrypted", each written down as a limit |
| Statistics | The page said nothing kept ties a visitor's days together, while a kept visit carries the visitor's mark sealed - opened by the service itself while the month's key is in memory | low | The website: who can link two kept visits (the running service, within the month) and who cannot (a copy of the disks) said; what a visit holds for its 14 days listed whole; the swap check warns but does not stop counting |
| Sync | The cipher's excerpt was said to be whole without the stream cipher | info | The website: `chacha20`, `block` and their helpers in it, with the RFC 8439 / 5869 vector tests; the code's place on Windows and a slot only an older app wrote as limits |
| Watch together | A restarted app does not know old running numbers and takes the relay's clock from the relay, so a lying relay could hand an old genuine message again; a ten-character code is for an evening's room | low | The website: both written down as limits; the padding and encryption (`_seal_text`, `_open_text`) and the tests in the excerpt |
| CodeQL | 4 TLS contexts without an explicit least version (Python's default is TLS 1.2 already) | info | After 1.6.9: TLS 1.2 said where each is made (`cert_pin.at_least_tls12`) |
| CodeQL | yt-dlp's own copy and wrapper runnable by other users; SHA-1 for ids kept in memory | low | After 1.6.9: this user's alone (0700); SHA-256 |
| CodeQL | 40 log lines "logging a password", a cache file "storing a secret", SHA-1 for the picture cache's file names, MD5 in NextPVR's login, three sockets on every interface | answered | False positives or by design: the stream address (which holds an Xtream login) taints every row that carries one, while each line logs `url_for_log`, `redact_url`, `address_scheme` or `for_log` of it; the cache's names are no secret; NextPVR's protocol is MD5; the HDHomeRun and mDNS answers and server mode must listen on the network |
| CodeQL | 25 in the tests (fake servers, test regexes) | answered | The tests' own, not the app's |

## November 2026 - an independent AI review of 1.6.10 (testing at ec467dd)

The maintainer gave the review kit (`tools/review_kit.py`, 19 parts, the
code at `ec467dd`) to ChatGPT, which read the excerpts and the linked
files. It scored the whole 6/10 against docs/SECURITY.md, found no
critical or unauthenticated takeover, and listed 46 weaknesses. Checked
against the code and the threat model, none is a defect the code did not
already mean: almost every one is a limit docs/SECURITY.md writes down
with its reason, and three that it did not say were written down.

| Area | Finding | Severity (theirs) | Done |
|------|---------|-------------------|------|
| Network boundary | A public name that resolves to a home address is not refused for the app's own requests (pictures) nor for the player; "close this first" | medium | Written down: **A list chooses where its pictures come from**, and its reason (a media server's artwork and Plex's `plex.direct` names resolve to home addresses). Put to the maintainer as a choice for a later version: refuse it for an internet source's own requests, the source's own server and Plex's names left out |
| Logins | Kept in the settings files, not a keyring; http providers send them in the clear; the player does not check certificates on old libraries | high | Written down: **Logins are in the settings files, not a keyring**, **A provider on plain http**, **The player checks certificates only as its libraries do** (the maintainer's decision: playback first) |
| LAN servers | Plain http: a reader takes the tokens, a changer can change the phone remote's page | high | Written down: **The home network's servers speak plain http**. Added: the remote API's shared playlist redirects to the source's own address, an Xtream login in it, so its token reaches those logins (server mode restreams instead) |
| Casting | A receiver gets the stream address, a device can take a name, the Apple TV bridge serves the first asker | high/medium | Written down: **A cast hands the TV the stream address**, the device table's "what is left", the bridge's `admits` |
| Tuner | Endpoints without a token for the devices let in | medium | Written down: **The tuner without its token**; off by default since 1.6.9 |
| Updates | The site can hold an update back or offer an older signed release still newer than the one running; the release key and the build are trusted | medium/high | Written down: **What dopeiptv.org keeps is its word**, **Builds are not yet reproducible**, **The build key signs in jobs that run other people's code**; the release key signs only after the maintainer approves (`release-signing`) |
| Updates | The AppImage checked by its path, then moved by its path - a race in a folder another account can write to | medium, conditional | Answered: whoever can write that folder can replace the AppImage itself at any time; nothing new is opened |
| yt-dlp | The app's copy known by its size and time after install, not hashed before each run | medium, conditional | Answered and written down under **yt-dlp is trusted by its own signature**: the folder is the user's own (0700); whoever can write there can change the app too |
| Private viewing | Turned on midway: a converter's second, a request already sent; providers and TVs see what they serve | medium | Written down: **Private viewing writes nothing to a disk** (the second each converter is watched in, what it does not reach) |
| Private viewing | A subtitle search sends the title | low | Written down: the search asks by the words in its box as the person opens it - what is asked right then goes (rule 3) |
| Reports | Pattern cleaning cannot catch every name; the site sees the address and the time | medium/low | Written down: the report is shown whole before Send, and **What dopeiptv.org keeps is its word** |
| Sync | The code is the whole key; the server can hold a copy back from a new computer; a slot only an older app wrote is unbound | high/medium | Written down: **The sync code is the whole key**, **The sync server can hold a copy back** (the older slot there too) |
| Watch together | The relay sees who is in a room and when; a replay across a restart | medium | Written down: **The Watch together relay sees who is in a room**; the restart's limit in the round above |
| Statistics | Visits kept 14 days could single someone out; swap is warned about, not enforced | medium | Written down on the transparency page (part 10) and in the round above |
| Backups | An unencrypted export, a weak password, a restore staged decrypted, a restore that turns things on | medium | Written down: **A backup's password is as strong as the password**; the staged file is 0600 beside the settings that then hold the same logins; what a restore turns on is named before it is put back (`backup.RISKY_SOURCE`, `SIGN_INS`) |
| Profiles | A short PIN offline; kids filtering by the provider's categories | medium | Written down: **A PIN can be short**, **A kids profile holds only with a grown-up's PIN** |
| Links | A web page can play a named channel | low | Written down: **A link to a channel plays without a question** |
| Third parties | TMDB sees the titles looked up; the built-in key can be read from the app | medium/low | Written down: **TMDB learns what is looked up**; Provider artwork, TMDB off asks it nothing; the key is public by nature |
| Media libraries | mpv's and FFmpeg's parsers and their own requests | medium | Written down: **Bugs in the media libraries are theirs**, **The player and the recorder look names up themselves**, **A hostile source can make the app work up to its ceilings** |

## November 2026 - an independent AI review of 1.6.11 (testing at cb79269)

The maintainer gave the review kit (20 parts, the code at `cb79269`, part
20 the changes since 1.6.10) to ChatGPT. It scored the whole 6/10, found
no critical weakness and no way past the signed updates, and ranked what
to close first: logins out of process arguments and receivers' addresses,
the phone remote's first page and the home network's plain http,
certificates checked by default, the bounded name registry, backups and
report cleaning. This round closes in code what can be closed without
risking playback; what stays is written down with its reason.

| Area | Finding | Severity (theirs) | Done |
|------|---------|-------------------|------|
| Logins | An Xtream address - its login - in the recorder's and an external player's command line, readable by every account on Linux | high | Fixed: a recording over its own connection is read by the app (`dvr_ingest.Ingest`, its route, reconnects and landing check) and handed to ffmpeg/mpv on their input (`recording.Feed`, `piped`; `-protocol_whitelist pipe`); an external player gets an http(s) address in a 0600 playlist file (`players.handoff_file`). What stays - an HLS recording, a cast's converter, server mode's HLS copy - is written down under **Programs started by the app show their arguments** |
| Logins | Nerd's bridge key in the recorder's arguments | high | Fixed: in its environment only (`net_bridge.recorder_env`) |
| Certificates | Strict TLS only opt-in; an https stream's certificate unchecked on older libraries | high | Fixed: every internet https stream's certificate checked in the player and the recorder, Nerd or not (`nerd.checks_certificate`, `_apply_tls`); one that does not check out is said (`diag_certificate`) and asked about once per source (`MainWindow._tls_offer`), the answer the source's own `tls_unchecked`, taken back on its page, a risky setting for sync and restore (`backup.RISKY_SOURCE`). Written down: **Every internet stream's certificate is checked** |
| Network boundary | The 8 192-name registry lets an early name go | medium | Fixed: it fails closed - past the bound every public name counts as an outsider's but the person's own and Plex's (`dest_guard._FULL`) |
| Network boundary | A proxied connection is the proxy's; mpv and FFmpeg resolve and redirect themselves | medium | Answered, written down: **The player and the recorder look names up themselves**; a recording over its own connection now goes through `dest_guard` (above) |
| Reports | A login of one or two characters stayed in free text | medium | Fixed: a login however short hidden as a word of its own (`report.SHORT_SECRET`, as `core/log.py` does) |
| Private viewing | A TMDB lookup queued before its title turned private still went | low | Fixed: asked again on the worker just before the request, nothing kept (`metadata._PRIVATE`) |
| Private viewing | A cast's converter on the disk stopped only at the next second's look | medium | Fixed: turning private viewing on wakes both bridges' watches at once (`privacy.wait_change`) |
| LAN servers | The remote API had no per-client budget or lock-out | medium | Fixed: a request budget per address and ten wrong tokens a minute shut it out for ten minutes, never this computer (`RemoteServer.limits`) |
| LAN servers | Plain http: a reader takes tokens, a changer the phone remote's page; the shared playlist redirects to Xtream addresses | high | Written down: **The home network's servers speak plain http** - a page on the home network cannot be authenticated without a certificate the phone trusts; server mode restreams instead of redirecting |
| LAN servers | The tuner's token-free endpoints; optional browser headers | medium | Answered: off by default since 1.6.9 (`home_server.OPEN_OFF`), the devices let in named by the person |
| LAN servers | Any private address counts as home | medium | Answered, written down: **Home means any private address** - the token, the signatures and the Host check are the lock; server mode keeps to this computer's own networks |
| Casting | A device can take a name; a direct cast hands the receiver the address | high/medium | Answered, written down: **A cast hands the TV the stream address**; a device known by more than its name is not preselected (`ui/cast_dialog._is_stranger`) |
| Logins | Plain text in the settings files, not a keyring | high | Written down: **Logins are in the settings files, not a keyring** |
| Backups | An unencrypted export | high | Answered: a backup without a password is made only after a warning whose default is Cancel (`MainWindow._backup_without_password_ok`); written down: **A backup's password is as strong as the password** |
| Profiles | A four-digit PIN can be guessed offline; kids filtering trusts the provider's categories | medium | Written down: **A PIN can be short**, **A kids profile holds only with a grown-up's PIN** |
| Updates | An older but newer signed release; a compromised signing key or builder | medium/high | Written down: **What dopeiptv.org keeps is its word**, **Builds are not yet reproducible**, **The build key signs in jobs that run other people's code** |
| Sync, Watch together | A new computer cannot tell an older copy; the relay sees who is in a room and can hold messages back | medium | Written down: **The sync server can hold a copy back**, **The Watch together relay sees who is in a room** |
| Statistics | A day's visits linkable; keys in memory | low/medium | Written down on the transparency page (part 10); the server's swap is encrypted and watched (`swap_state`) |
| Guides | 2 GiB of guide is still work | medium | Written down: **A hostile source can make the app work up to its ceilings** |
| yt-dlp | Checked by its hash, not at the moment it runs | low | Answered: whoever can change it runs as the person already (outside the model) |
| Poster cache | A picture let go is asked for again | low | Answered: the same host, the same picture; **Provider artwork, TMDB off** asks TMDB nothing |
| Third parties, update checks, links | TMDB sees titles; an update check shows the system; a web page can offer a stream | low | Written down: **TMDB learns what is looked up**, what an update check sends, the question before an address from a link (`mw_commands._ask_link_play`) |

## November 2026 - a second independent AI review of 1.6.11 (testing at cb28ead)

The maintainer gave the 21-part kit (the code at `cb28ead`, part 21 the
fixes after the first review) to ChatGPT. It scored the whole 7/10 (from
6/10), found no unauthenticated critical weakness, no way past the signed
updates and no way for the server to read sync, and named what stood
between 7 and 8: logins in the arguments of the programs the app starts in
every mode, the media engines' own requests (an HLS playlist's segments,
keys and redirects) outside the app's destination guard, a certificate
check that did not fail closed, and Kodi's and the home network's plain
http. This round answers the whole table, most of it in code; four
agents and a second pass worked on it, each change with real tests (real
ffmpeg, real mpv, `/proc/<pid>/cmdline` read while they run).

| Area | Finding | Severity (theirs) | Done |
|------|---------|-------------------|------|
| Logins | The recorder still got the address for an HLS playlist, the certificate fallback and RTSP; an external player fell back to the address | high | Fixed: no program the app starts is handed a provider's address. The recorder, a cast's converter and probe, the cast's archive and buffer readers, AirPlay's converter and server mode's HLS copy read an alias (`net_bridge.Bridge.alias`: a `.stream.invalid` host under a 192-bit token, the path's shape alone, mapped back segment by segment so relative HLS addresses work) through the bridge, its key in their environment alone (`child_env`); an rtsp/srt/rtmp address with a login goes in a 0600 file (ffmpeg's concat list, mpv's playlist); an external player gets its 0600 playlist or is not started; a token header never in argv (`tests/test_security_rules.py` fails on `-http_proxy` or an unfiltered `-headers` anywhere) |
| Network boundary | FFmpeg/mpv fetch nested HLS resources, keys and redirects outside the app's guard (SSRF) | medium | Fixed: the bridge judges every connection it opens - by the host as written, then on the connected socket before a byte is sent (DNS rebinding) - by where the stream's source is (`Policy`): a source on the internet's never this computer, link-local, a metadata service or the home network, but the stream's own server, the person's own names and `.plex.direct`. The in-app player (docked, multiview, radio) reads every stream of an internet source through it, Nerd on or off (`nerd.player_proxy`); every program above too. A home source is left as it was (written down) |
| Certificates | A refused `tls-verify` was logged and playback went on | high (conditional) | Fixed: the stream is not opened and the video says why (`nerd.CertificateUnchecked`); never handed to another player instead. An https alias's certificate is checked by the bridge as the player checks it |
| Network boundary | A proxied request is the proxy's; the source's own names and udpxy are exceptions | medium | Fixed in part: a request through a proxy has its name looked up here first (`dest_guard.proxied_refusal`); udpxy only in its own shape; an address with no host counts as this computer. Answered: `note_own` stays (a server at home by its public name), its reason in `dest_guard` |
| LAN servers | The remote API plays any network address for a token holder; a global budget | medium/low | Fixed: no address that leads to this computer or a link-local host (`remote_api.play_refusal`); a budget for every device together beside each address's (`RateLimiter`) |
| LAN servers | The tuner open to the home network, carried over from before 1.6.9 | medium | Fixed: said once on a card with "Close it" (only the devices that used it), `--server` logs it at every start, Settings says what the tuner is open to |
| Casting | Kodi's login over plain http | high | Fixed: asked once per Kodi before a login goes over http - "Send anyway", "Use HTTPS" (tried first), Cancel the default (`kodi.KodiPlainLogin`) |
| Links | A `dopeiptv://` link changes the channel unasked | low | Fixed: asked first, the channel's name in plain text |
| Backups | Risky settings restored when the restore is approved | medium | Fixed: every risky thing a box of its own, none ticked; what is not ticked stays as it is here |
| Profiles | Kids filtering trusts the provider's categories; short PINs | medium | Fixed: "Only what a grown-up allows" (per source, off by default); PINs of 4 to 12 digits with a hint |
| Reports and logs | An encoded login escapes redaction; the outgoing report not editable | medium | Fixed: percent-encoded, JSON-escaped and Basic base64 forms masked; the report sends exactly what its window shows |
| Sync | A new computer can be handed an older copy | medium | Fixed in part: a copy last saved more than 30 days ago is asked about before a joining computer takes it (`sync.saved_at`, `STALE_DAYS`); a server that withholds stays written down |
| Watch together | The relay cannot tell members from anyone who knows a room id; replay after a restart | medium | Fixed (app and website): a room credential the relay keeps only a hash of binds the room; limits per member, address and day; a member since 1.6.11 taken only from its first message naming this stay's own id |
| Updates | Nightly-to-stable at the same three numbers offered automatically | low | Fixed: only for the build the person left Test builds on; else strictly newer |
| yt-dlp | Checked by size and time between hashes | low | Fixed: hashed before every run; mpv's hook through a wrapper that hashes it first |
| Instance socket | The folder's error path went on | low | Fixed: fails closed (`instance._folder_refusal`) |
| Recordings | A stream with no end fills the disk | medium | Fixed: a recording stops while a gibibyte is still free (`recording.DISK_FLOOR`) |
| Website statistics | Visit-level retention; raw server logs | medium/low | Fixed: single visits 7 days (was 14); the service warns on the admin page while an old log with addresses is on the disk; the servers' configuration is checked by hand (written down) |
| Release signing | A compromised signing environment | high | Answered: the update key lives only in GitHub environments of its own (`release-signing`: a required reviewer, `main` and `v*` tags only, administrators may not bypass; `nightly-signing`: `testing` only) - nothing else reads it |
| Logins at rest | The settings file, not a keyring | medium | Answered: a keyring is planned as a round of its own after 1.6.11 (it touches every login); written down |
| Third parties, metadata | TMDB sees titles; the update check shows the build | low | Answered: Posters & info can turn TMDB off; written down |
| Crypto | An own ChaCha20-Poly1305 | low (unproven) | Answered: RFC 8439 test vectors in the tests; written down |
| Casting | A device can claim another's identity | medium | Answered: discovery is unauthenticated by design of those protocols; a device known by more than its name is not preselected |

## November 2026 - a third independent AI review of 1.6.11 (testing at 3886b6f)

The maintainer gave the new 21-part kit (the code at `3886b6f`, part 21
the fixes after the second review) to ChatGPT. It scored the whole 8/10
(from 7/10), found no unauthenticated critical weakness, and named what
was left: arguments that still name something, the person's proxy
connecting where the bridge cannot see, a backup without a password
holding every login, and the home network's plain http and wide reach.
This round closes what can be closed without losing a feature or risking
playback, and writes down why the rest stays.

| Area | Finding | Severity (theirs) | Done |
|------|---------|-------------------|------|
| Logins | A non-http address goes to a program as an argument unless the app sees a login in it - a token in an rtsp or rtmp path, or under a query word of its own, escapes | medium | Fixed: every network address but a multicast group's goes in a 0600 file (`player_exec.handed_privately`) - the recorder's list, the external player's playlist, and now a cast's converter, ffprobe and the Apple TV's converter too (`player_exec.private_list`, through `net_bridge.hand_over`), which before got an rtsp address with a login as it was. A SAT>IP tuning (the tuning alone), a multicast group, and for the recorder an address an ffmpeg older than 5 would read without its options, stay an argument - written down |
| Privacy | The external player's title in its arguments | low | Fixed: with a handoff file the title is that file's `#EXTINF` line alone, which mpv and VLC show; a local file keeps its title beside its path, which is there anyway |
| Network boundary | Through Nerd's proxy the bridge cannot see where the proxy connects | medium | Fixed in part: a name is looked up here before the proxy is asked, and each of its addresses judged as a connection that landed there (`Bridge._proxied`, best effort as `dest_guard.proxied_refusal`); the proxy's own lookup stays the proxy's - written down |
| Backups | A backup without a password holds every login in plain text | medium | Fixed: without a password the file holds no login (`backup.without_logins`: Trakt, Simkl, OpenSubtitles, a TMDB key of one's own, the sync code, MQTT, the webhooks' header, a proxy or header with a login, a Kodi's password, a playlist's password, token and box MAC, a login in its addresses); a restore keeps the logins of the computer it is put back on (`Backup.keep`, `_logins_from_here`); a login written into an address's path stays - written down |
| Casting | A direct cast hands the TV the stream's address, an Xtream login in it | medium | Answered: a DLNA TV, a Kodi and an Apple TV are made to fetch a stream themselves; every cast through the computer would keep it awake and in the path and make casting less dependable. To a Chromecast or an Apple TV one cast can go through the app (a quality, track or subtitle chosen). Written down in docs/SECURITY.md |
| LAN servers | The remote API's token travels over plain http | medium | Answered: a certificate for a home address would be one every client warns about, and Home Assistant's REST calls, VLC and Kodi cannot sign; the token is 192 bits, with a lock-out and New address. Written down |
| LAN servers | "Home" is every private range | medium | Answered: narrowing the phone remote and the remote API to this computer's own networks would shut out phones and Home Assistant on another part of a network split in VLANs; every request needs the token or the phone's signature, a lock-out holds, server mode's token-free tuner already uses the narrow rule (`home_net.LocalNetworks`). Written down |
| Casting | Kodi over http allowed after the person says so | low | Answered: the person's choice per Kodi, "Use HTTPS" tried first, Cancel the default |
| Network boundary | `note_own` lets a source's own name lead home | low | Answered: a server at home under its public name needs it; its reason in `dest_guard` |
| Sync | A computer new to the slot can be handed an older copy | low | Answered: the 30-day question holds; a server that withholds stays written down |
| Media engines | FFmpeg and mpv decode outside input in the app's own process | medium | Answered: a sandbox for the decoders is the step towards 9/10, a project of its own after 1.6.11 (the Flatpak runs in Flatpak's sandbox already) |
| Release signing | The signing infrastructure | medium | Answered: the update key lives only in GitHub environments of its own with a required reviewer, administrators may not bypass; the next kit's part 19 says so |
| Logins at rest | The settings file, not a keyring | medium | Answered: planned as a round of its own after 1.6.11 |
