## dopeIPTV 0.7.3

**A real pop-out player, simpler playback, and stream errors that finally
tell you what's wrong.**

**Highlights:** **pop-out the player into its own window** (second screen,
always-on-top, drag the video to move) · **Playback mode setting removed** —
embedded just works · **plain-language stream errors** instead of "loading
failed" · **provider troubleshooting logs**.

<details>
<summary><b>Full release notes</b> — click to expand</summary>

### Pop-out player — new
- **Detach the video into its own window** with the **⧉** button in the player bar (or press `P`) and keep it on a second screen while you keep browsing and zapping channels in the main window.
- It's the **same in-app player moved out**, not a second one — so the full control bar, seeking, timeshift and channel-zap all come with it, and it renders through the same cross-platform path (works the same on Linux, macOS and Windows).
- By default it's a **clean, title-bar-less video window** — drag the video itself to move it. Right-click it for **Always on top**, **Show title bar**, or **Auto-hide controls** (on by default: the bar fades when idle and returns on mouse movement). **Double-click** for fullscreen, **`Esc`** to leave it; close the window or click **⧉** again to dock it back.
- This **replaces Picture-in-Picture**, which it fully supersedes (PiP's "always on top" lives on as the right-click toggle).

### Simpler playback
- **Removed the "Playback mode" setting.** The embedded player is the player; **Open externally** (mpv / VLC) stays on the right-click menu for a fresh external window. The two old modes — "reused mpv window" and "external" — were buggy or redundant and are gone. Where the embedded player can't run (no libmpv), channels open in an external mpv window automatically.

### Stream errors in plain language
- When a channel won't play, the app now tells you **why** instead of the opaque "loading failed": an **expired subscription**, **all connections in use** (someone/something else is on your account), the **provider blocking the stream** (including the non-standard `458` code — usually anti-VPN/re-streaming or a connection/region block), an **unreachable or timing-out server**, or a **format it can't play**. It checks your account and probes the stream in the background — no debug mode required.

### Troubleshooting
- `DOPEIPTV_LOG=debug` now logs every provider call — authentication state (status, expiry, active/max connections), HTTP status and timing — with your **username and password redacted**. `DOPEIPTV_LOG_FILE=/path` also tees it to a rotating file, so a log is safe to attach to a bug report.

> Linux is and remains the primary target — Windows and macOS are a bonus.

</details>
