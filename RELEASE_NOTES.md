## dopeIPTV 1.2.4

Live streams stay up again. 1.2.2 broke them, and this puts it back.

- **Live channels no longer die after a few seconds.** 1.2.2 started applying
  the network-buffer setting to the stream *already playing*, so a change in
  Settings took effect at once. That turned out to reach into mpv's demuxer
  while it was running, and on some accounts the stream dropped within
  seconds, over and over. The buffer is applied once per stream again, the way
  it was for months — so a change in Settings now takes effect on the next
  channel you open, not the current one. That trade is deliberate: playback
  staying up matters more.
- **Auto-reconnect actually reconnects.** When a live stream did drop, the log
  said it was retrying and then nothing happened — the retry hit an internal
  guard and gave up silently, while clicking the same channel by hand started
  it instantly. Both silent exits now say why they bailed.
- **Trakt watched marks survive a restart.** A completed sync only ever
  updated memory - the call that writes it to disk sat in unreachable code -
  so everything Trakt knew you had watched was gone on the next start, and the
  app re-synced from scratch every launch.

If you are on 1.2.2 or 1.2.3 and your live channels have been dropping, this
is why, and this fixes it.

Full details in the [changelog](https://github.com/slimture/dopeIPTV/blob/main/CHANGELOG.md).

> Linux is and remains the primary target — Windows and macOS are a bonus.
