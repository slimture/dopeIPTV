## dopeIPTV 0.7.2

**Windows joins the party** — a portable Windows build is now available
alongside Linux and macOS — plus timeshift and UI fixes.

**Highlights:** **Windows portable build** (unzip and run, no installer) ·
**dead timeshift channels now hide themselves** · **centred search icon** ·
**Windows shutdown crash fixed**.

<details>
<summary><b>Full release notes</b> — click to expand</summary>

### Windows (portable) — new
- A **portable Windows x64 build**: unzip the folder and run `dopeiptv.exe`, no installer and no admin rights. The bundled README covers the first-launch SmartScreen "unknown publisher" prompt (More info → Run anyway), an optional Start-menu/desktop shortcut (Settings → Interface → Maintenance → Create shortcut), and how to remove every trace of the app.
- Fixed a **crash on exit** in the windowed build (`'NoneType' object has no attribute 'flush'`).

> Linux is and remains the primary target — Windows and macOS are a bonus.

### Timeshift
- **Channels that advertise catch-up but don't actually serve it now hide the timeshift affordance.** Once a channel's archive is found not to work at any depth, its `◀◀` marker in the list, the in-player rewind button, and the seek-bar overlay all disappear together, instead of lingering after an "archive unavailable" message. Channels with a genuinely shorter archive are unaffected — they keep working at the depths they support.

### Fixes & smaller touches
- The **category search magnifier** now sits dead-centre in its button instead of shoved to the top-left.

</details>
