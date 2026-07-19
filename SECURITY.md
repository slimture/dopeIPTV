# Security Policy

## Supported versions

Only the **latest release** receives security fixes — the app has a built-in
update check, and upgrading is a download away.

| Version | Supported |
| ------- | --------- |
| latest release | ✅ |
| older releases | ❌ |

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report privately via GitHub's built-in flow: the repository's
**Security tab → "Report a vulnerability"**. You'll get an acknowledgement
as soon as the report is seen (normally within a few days), and credit in
the release notes of the fix unless you prefer otherwise.

If the Security tab is unavailable to you, contact the maintainer
[@slimture](https://github.com/slimture) through GitHub.

## Scope notes

Useful context for researchers:

- dopeIPTV talks only to servers **the user configures** (their IPTV
  provider, optional XMLTV URL) plus a small set of fixed services the user
  opts into: GitHub (update check), TMDB (metadata), Trakt (sync).
- Provider credentials are stored **locally** in Qt's QSettings store,
  unencrypted — the standard threat model of a desktop app. There is no
  server-side component and no telemetry.
- Playback is delegated to **libmpv/FFmpeg**; vulnerabilities in stream
  demuxing/decoding should be reported upstream, though we will gladly ship
  bundled-library updates that pick up their fixes.
