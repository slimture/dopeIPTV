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
| Build chain | Downloads by "latest", actions by tag, unhashed pip installs, the update key in every build job | high | 1.6.9: SHA-256 pins, a hashed lock, actions by commit, read-only workflows, the key in one signing step |
| Updates | A taken-over site could offer an older signed release; a file lying in the download folder | medium | 1.6.9: never older than the copy running; a file of the updater's own |
| Player text | A station's or programme's title drawn as rich text over the picture | low | 1.6.9: plain text |
| Stream addresses | An `av://lavfi` channel address made the player write a file of the provider's choosing; `gopher://`, `edl://`, `memory://` reached past streams (input review; proven) | critical | 1.6.9: `core/stream_address.py`, checked where every address is handed out and in both players |
| Guide cache | The guide's index was unpickled with no restriction | high (needs local write) | 1.6.9: a restricted unpickler |
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
| Overall | The parts reviewed are not the whole attack surface: parsers of provider data, logins at rest, the local servers, the programs the app starts, the build chain | info | 1.6.9 (in progress): five more reviews - local servers and processes, untrusted input and secrets, supply chain and platforms, the website's services - with their findings to be added here |

## The next round

Before 1.6.9 is released: the review kit's `ALL.txt` from `testing`, given to
at least one independent AI assistant; the findings and what was done, here.
