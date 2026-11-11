# Screenshots

Drop PNG screenshots here with these exact names — the README and the Flathub
AppStream metadata (`packaging/io.github.slimture.dopeIPTV.metainfo.xml`) both
point at them:

| File | What to capture |
|---|---|
| `live.png` | **The hero shot.** Live TV: the sidebar, the channel list, and a channel playing with the now/next guide. |
| `epg-grid.png` | The **programme guide's timeline grid**. |
| `multiview.png` | **Multiview** with four live channels. |
| `player.png` | **Timeshift**: a live channel rewound, the timeline over the video. |
| `themes.png` | **Settings → Appearance**: the theme and accent pickers. Taken by hand. |

All but `themes.png` are the website's English front-page pictures, made by
`tools/site_screenshots.py` (see `docs/MAINTAINING.md`): take them with
`xvfb-run -a -s "-screen 0 2560x1600x24" python tools/site_screenshots.py OUT en`,
then save `main`, `guide`, `multiview` and `timeshift` from WebP
as 1600×1000 PNG under the names above. The file names stay as they are:
the metainfo of every release already out points at them on `main`.

## Getting content for free (no personal provider needed)

- Load the public **iptv-org** M3U (add a playlist → type **M3U** →
  `https://iptv-org.github.io/iptv/index.m3u`), or just use the built-in
  **🎬 Demo channels** button.
- The theme picker shot needs no content at all.
- Don't want your provider's category names visible? Use the **◉** solo
  toggle by "Categories" (shows only the active one) or the **☰** icon-rail
  (`Ctrl+B`).

## Tips for good store screenshots

- **1280×800 or 1600×1000** (16:10-ish), the whole window, no desktop clutter.
- Use a dark theme (the default) — it looks best and matches the store cards.
- PNG, keep each under ~1 MB if you can. Flathub wants at least one; **3–4 is
  ideal** and all four here render in the README and the AppStream page.
- Once added and pushed to `main`, the README images render immediately and
  the Flathub `<image>` URLs resolve via `raw.githubusercontent.com`.
