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
