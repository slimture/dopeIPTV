## dopeIPTV 1.2.6

Casting works.

- **Casting a live channel gave no picture at all.** The receiver was handed
  the panel's address and left to sort out the redirect itself; it never did.
  It now gets the address the stream really lives at, labelled with the type
  the server itself reports rather than a guess from a file extension - and if
  that address is refused, the panel URL still gets its own attempt.
- **You can cast from every section.** History handed over the address the
  channel was played from - a raw transport stream, which a Chromecast cannot
  decode at all - so casting worked from the channel list and never from
  History. A live channel goes as HLS wherever you cast it from.
- **Channels the TV has no decoder for now play anyway.** Some are ordinary
  H.264 video with Dolby Digital Plus audio: a Chromecast that is not an Ultra
  or a Google TV has no E-AC-3 decoder and refuses them without ever saying
  why. ffmpeg converts those here - the video is copied through untouched, only
  what the receiver could not take is re-encoded - and the result is served on
  your own network. Native playback stays the rule: converting is reached only
  after the device itself has refused the stream, so a receiver that decodes
  the channel keeps getting the provider's own stream, untouched.
- **You can see that you are casting.** A strip above the player names the
  device and what was sent there, with a way to stop it. Casting stops local
  playback, so until now the player pane simply went black with nothing
  anywhere saying why.
- **Playing something in the app ends the cast** - the same handover in
  reverse, and two provider connections are no longer held at once.
- **Casting no longer takes two presses**, and the device list offers the
  devices you cast to last time immediately instead of an empty box while it
  searches.
- **Rescanning for devices crashed** inside pychromecast: stopping discovery
  closes the zeroconf instance every discovered device still holds.
- **Closing the app stops the cast** again.
- **A refused channel says why** - "this channel is eac3, which this Chromecast
  has no decoder for" - instead of leaving a black TV and no explanation.

Full details in the [changelog](https://github.com/slimture/dopeIPTV/blob/main/CHANGELOG.md).

> Linux is and remains the primary target — Windows and macOS are a bonus.
