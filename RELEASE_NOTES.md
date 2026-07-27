## dopeIPTV 1.2.5

The pop-out player works on Windows.

- **Windows pop-out is fixed.** It drew the picture upside down, then black,
  then - after a first attempt at a fix - a single frozen frame. All of it came
  from the same mistake: 1.2.0 put Windows on the mirror rendering that macOS
  and Linux need for their own platform defects, in a release that shipped
  saying Windows pop-out was experimental and not sufficiently tested. It was
  not. Windows has neither defect, and now simply moves the player into the
  pop-out window - the ordinary way, and the only way it worked before 1.2.0.
- **Popping out no longer stops the stream** on Windows. Detaching the player
  reparents its control bar while the click that started it is still in flight,
  and Windows then delivered the release to whatever ended up under the pointer
  - the poster's play/stop button, which on a live channel means stop. The
  stream died the moment you popped out.
- **The pointer hides over the pop-out video** on Windows, after a couple of
  idle seconds, like it already did elsewhere.

macOS and Linux are untouched by all of this - they keep the rendering path
each of them needs.

Full details in the [changelog](https://github.com/slimture/dopeIPTV/blob/main/CHANGELOG.md).

> Linux is and remains the primary target — Windows and macOS are a bonus.
