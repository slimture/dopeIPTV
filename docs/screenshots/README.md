# Screenshots

Drop PNG screenshots here with these exact names — the README and the Flathub
AppStream metadata (`packaging/io.github.slimture.dopeIPTV.metainfo.xml`) both
point at them:

| File | Show |
|---|---|
| `live.png` | Live TV: the channel list + a channel playing in the embedded player, EPG "now/next" visible |
| `epg-grid.png` | The EPG timeline grid (open the guide from the sidebar) |
| `themes.png` | Settings → Appearance with the theme/accent picker (works with any content) |
| `player.png` | Favorites (or a nice grid view) with the mini player showing |

All four can be captured for free — no personal provider needed: load the free
public **iptv-org** M3U (`https://iptv-org.github.io/iptv/index.m3u`) for the
live/EPG/player shots, and the theme picker works with anything (even the
built-in demo channels).

Tips for good store screenshots:

- **1280×800 or 1600×1000** (16:10-ish), the whole window, no desktop clutter.
- Use the app maximized or a clean window; a real (or demo/iptv-org) source so
  the lists aren't empty.
- PNG, keep each under ~1 MB if you can. Flathub wants at least one; 3–4 is
  ideal.
- Once added and pushed to `main`, the README images render immediately and
  the Flathub `<image>` URLs resolve via `raw.githubusercontent.com`.
