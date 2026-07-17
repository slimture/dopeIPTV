## dopeIPTV 0.8.1

**Fix: working catch-up channels could silently lose their timeshift.**

A momentary network failure during the archive check (a timeout, DNS/TLS
error or refused connection) was treated as proof that the channel serves
no catch-up - and the channel's whole timeshift UI (list marker, rewind
button, archive timeline) was hidden for 14 days, with no message. One
flaky moment was enough to disable channels whose archive works fine.

- **Only a real provider response can hide a channel's catch-up now** (an
  HTML/JSON error page instead of a stream). Network-level failures leave
  everything untouched.
- When the archive can't be reached, the app now **says so** - "Couldn't
  reach the catch-up archive - check the connection and try again" -
  instead of failing silently or blaming the channel.
- If channels already lost their timeshift on 0.8.0: refresh the playlist
  (↻) once and they come back immediately.

Everything else is 0.8.0 - see [its release notes](https://github.com/slimture/dopeIPTV/releases/tag/v0.8.0)
for the multiview grid, the cross-platform interface and the rest.

> Linux is and remains the primary target - Windows and macOS are a bonus.
