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
| Settings sync | The server could hand back an earlier copy unnoticed | medium | 1.6.8: a counter sealed in every copy; an earlier copy is told and this computer's put back |
| Settings sync | The counter is no freshness guarantee for a computer that has never synced, and the server can withhold a copy | info | Written down: inherent to a server that stores; the transparency page says so |
| Settings sync | Requests could follow a redirect or go over plain http | low | 1.6.8: https only, no redirect followed |
| Watch together | Room codes of 8 characters (about 40 bits) and 120 000 rounds were guessable offline from the room id | high | 1.6.8: 10 characters (about 50 bits), 600 000 rounds; old codes still join |
| Watch together | Replayed messages were acted on | medium | 1.6.8: a sequence number per sender; 1.6.9: the relay's backlog at joining not read, messages out of their time ignored |
| Watch together | Every member holds the room key, so one member can write as another | medium | 1.6.9: member ids the hash of an Ed25519 key of their own, every message signed (1.6.8 members protected once they update) |
| Watch together | Padding not checked strictly on opening | low | 1.6.9: one of the padded sizes, zeros after the text |
| Watch together | The relay sees who is in a room with whom, when and from which address | info | Written down: a relay has to; it keeps it in memory only and logs nothing |
| Private viewing | A resume point, a log line or Trakt/Simkl could still name a private playback after the switch was turned off | medium | 1.6.8: kept per playback (`playback_private`, `private_key`, `for_log`) |
| Private viewing | Other reporters, metadata lookups and logs outside `for_log` were not shown to be covered | medium | 1.6.9: every reporter, store and lookup audited against the playback - the automatic watched mark, Trakt's start after its lookup, a title cast again, the radio click, TMDB, stream health and details, track, subtitle, delay and teletext choices, a series' season, the folder opened, the wake lock's reason and log lines now ask the playback; a private channel's rewind buffer deleted when left, at private viewing's end and before the app closes. Still open, proposed: the system's media controls and the remote API's status name what plays privately (both local) |
| Updates | A build without the release key trusted the site's checksum | high | 1.6.8: no key, no update |
| Updates | A download could follow a redirect to plain http, and was not size-limited while read | low | 1.6.9: redirects by hand, each https; stops past the file's size |
| Updates | An older signed release could be offered | info | Checked: only a newer version is offered, the asset name and the signed list must match, and the new copy's self-check confirms its version |
| Website statistics | One secret kept 25 months made visitor ids recomputable; detailed visits kept 400 days; raw logs a week; the front server's log 30 days | high | 1.6.8 + website: a key per month, deleted after the month after; visits 30 days; raw logs two days; the front server logs nothing |
| Website statistics | Visitor ids kept 25 months; rare combinations in anonymous visits | medium | Website: a month's ids go with its key (only counts kept); finished months made k-anonymous (k = 5) |
| Website statistics | Referrer and URL queries in the raw log; the content server's default log | low | Website: queries and fragments never logged; no other log |
| Website statistics | A completed payment was noted on the visit | medium | 1.6.8 + website: counted per day only |
| Transparency page | "Every destination", "built from exactly this code" and the site's own checksum check were claimed more strongly than shown | info | Website: what a search found, not a promise; builds not yet reproducible, said so; the site's check shown in code |
| Overall | The parts reviewed are not the whole attack surface: parsers of provider data, logins at rest, the local servers, the programs the app starts, the build chain | info | 1.6.9 (in progress): five more reviews - local servers and processes, untrusted input and secrets, supply chain and platforms, the website's services - with their findings to be added here |

## The next round

Before 1.6.9 is released: the review kit's `ALL.txt` from `testing`, given to
at least one independent AI assistant; the findings and what was done, here.
