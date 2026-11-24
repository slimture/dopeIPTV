# Performance over time

What the app costs as it starts, while it waits, after hours of use and
with a big provider - measured, and held to budgets in CI so that a
regression fails a test instead of waiting for someone to notice. Four
test files, each running a window of its own in a child process,
offscreen, on the demo source (`providers.client.DemoClient`), with a stub
in place of the player's `play` where something is played:

| What | Test | CI's run |
|------|------|----------|
| The start: the import chain to a shown window | `tests/test_perf_start.py` | about 1 s |
| Idle: the window shown, nothing playing | `tests/test_perf_idle.py` | about 14 s |
| Long use: the same things hundreds of times | `tests/test_perf_long_use.py` | about 14 s |
| Big data: a big guide, list and catalogue | `tests/test_perf_big_data.py` | about 6 s |

What they share is `tests/perf.py`: the child's start (`PRELUDE`), which
never lets anything leave the computer - a name that is not this
computer's is not looked up, the radio directory answers nothing, and the
child's settings (`settings()`) have the update check and TMDB off - and
`DOPEIPTV_PERF_LONG`, the longer local run.

## Running them

```bash
QT_QPA_PLATFORM=offscreen python3 -m pytest -q tests/test_perf_*.py
# longer: 1 000 zaps, a week of the guide, 10 s of idle...
DOPEIPTV_PERF_LONG=1 QT_QPA_PLATFORM=offscreen python3 -m pytest -q tests/test_perf_*.py
# what the start imports, and what each module costs
QT_QPA_PLATFORM=offscreen python3 -X importtime -c "import dopeiptv.ui.main_window" 2> imports.txt
```

A failed budget says what it measured: the number, what each round did,
and what grew (types of objects) or fired (timers by receiver).

## The budgets

Each was set at about three times what this computer measured when it was
set (4 cores, other work running beside it), since CI's runners are slower
and shared. Where a count could be held instead of a time, it is: a timer
that fires once a second fires once a second on any machine, an object
left behind is left behind on any machine. Times are the best of a few
runs (or rounds): a busy moment of the machine's is not the app's.

### The start (`test_perf_start.py`)

From the start of the process: Qt, the import chain, an Xtream, an M3U and
a portal client made as `app.py` makes them before the window, the window
built and shown, its first frame. The better of two starts (the first may
compile every module).

| | Measured | Budget |
|--|---------|--------|
| The import chain (`dopeiptv.ui.main_window` and what it needs) | 0.31-0.39 s | 1.2 s |
| The window built (`MainWindow(...)`) | 0.07-0.10 s | 0.3 s |
| From `show()` to its first frame | 0.013-0.02 s | 0.25 s (a collection or a slow paint on a shared runner is a few hundredths) |
| From the start of the process to the first frame | 0.44-0.60 s | 1.8 s |
| Modules imported by the first frame | 491, 279 of them the app's | 600 and 340 |

Never imported by the first frame: requests, urllib3, charset_normalizer,
chardet, certifi, idna, PIL, numpy, pychromecast, zeroconf, ifaddr,
protobuf, casttube, yt_dlp, cryptography, OpenSSL (`HEAVY`). mpv is on the
chain on purpose - the docked player is made with the window - and so is
QtDBus on Linux (the system's media controls, MPRIS).

### Idle (`test_perf_idle.py`)

The window shown, every place visited once, then each measured on screen
as a person comes back to it; then back on TV, nothing playing.

| | Measured | Budget |
|--|---------|--------|
| Timer events a second on the window's thread, every place left | 0.4 (1.4 before) | 1.2 |
| Wakes of the app's threads a second (voluntary context switches, Linux) | 0.8 | 2.5 |
| CPU time, as a share of the idle stretch | 0.02 % | 2 % (loose on purpose: a log line is no regression, a loop that polls is) |
| Timer events a second while a place is on screen | at most 6.7 (Movies, the start's logos still settling) | 15 - a ticker of 100 ms on any place breaks it |

What is left at idle: the recordings' look at what is due (every 5 s) and
the keep of where a film got to (every 12 s). While a place is on screen:
the text cursor of a focused field blinks (twice a second: Mine, Movies,
Series, Search), Mine's page looks at what plays once a second, the guide
moves its clock line every 1.5 s.

### Long use (`test_perf_long_use.py`)

Each thing after a warm-up (its first time fills its caches), in rounds;
after each round what is alive is counted. CI's run / the long run:
160 / 1 000 zaps, 54 / 450 place switches, 4 / 40 Settings windows with
every page built, 24 / 200 cast windows, 18 / 150 series pages.

| What may grow, from the warm-up to the last round | Measured | Budget |
|--|---------|--------|
| QObjects under the window | 0 | 3 |
| Widgets | 0 | 3 |
| Top-level windows | 0 | 0 |
| Objects Python tracks | at most 65 (the long run: 98 after 1 000 zaps, flat from the second round) | 200 |
| What Python has allocated (tracemalloc) | at most 28 KB (54 KB after 1 000 zaps: the last 300 log lines an error report shows, `core/log.py`) | 128 KB |

| Milliseconds each, the quickest round | Measured | Budget |
|--|---------|--------|
| A zap (the playback path with a stub player) | 25 | 75 |
| A place switch | 30 | 90 |
| Settings opened with every page built, and closed | 555 | 1 700 |
| The cast window opened and closed | 25 | 75 |
| A series' page opened and closed | 47 | 150 |

### Big data (`test_perf_big_data.py`)

A generated XMLTV of 2 000 channels with a programme an hour and a
description each: a day in CI (48 000 programmes, 8.4 MB), the week in the
long run (336 000, 58 MB). 50 000 channels, 50 000 films and 10 000 series.

| | A day | The week | Budget (a day; the week's seven times) |
|--|------|----------|--------|
| `epg.parse_xmltv` | 0.48 s | 3.5 s (10.8 s before) | 1.5 s |
| The index written / read / the guide loaded by it | 0.045 / 0.016 / 0.016 s | 0.19 / 0.054 / 0.055 s | 0.15 / 0.05 / 0.05 s |
| The guide in memory, loaded | 2.9 MB | 12 MB | 9 MB (36 the week) |
| Python's peak while 200 channels' day is parsed | 2.8 MB | | 8.5 MB |
| `search_all.Catalogue` of the 110 000 rows | 0.2 s | | 0.6 s |
| `search_all.GuideTable` of the guide | 0.13 s | 1.1 s (250 000 airings, its cap) | 0.4 s |
| `search_all.search`, three queries | at most 0.033 s | 0.039 s | 0.1 s |
| `palette_match.Index` of 100 000 names / a query | 0.17 / 0.018 s | | 0.5 / 0.06 s |
| The window's list of 50 000 rows shown / filtered / sorted | 0.062 / 0.014 / 0.066 s | | 0.2 / 0.05 / 0.2 s |

## What the measurements found

Fixed, each with a test that fails without it:

- **requests on the start's chain.** `core/_lazy_requests.py` keeps it off
  the import chain, but every client made its requests session in its
  `__init__`, and `app.py` makes the source's client before the window - so
  requests, urllib3 and certifi were imported before the first frame at
  every start (and `nerd.install()` imported urllib3 to put back an IP
  version nothing had changed). A client's session is now made on its
  first use (`Mirrored.session`, `on_session`). 620 modules by the first
  frame, now 491; three clients made in 36-54 ms, now 5-15 ms.
  (`tests/test_lazy_imports.py`, `tests/test_perf_start.py`)
- **A guide's times through strptime.** Three quarters of the parse: two
  calls per programme. `epg.xmltv_moment` reads the form nearly every guide
  writes with `int()` and gives exactly what `parse_xmltv_time` gave; the
  rest is left to it. A week of 2 000 channels: 10.8 s, now 3.5 s.
  (`tests/test_epg.py`)
- **The timeshift timeline's clock**, a 1 s timer from the window's start to
  its end, doing nothing unless the timeline was on screen: the largest
  share of the idle window's wakes. It runs only while the timeline is
  shown now (`ui/widgets.WhileShown`). (`tests/test_perf_idle.py`)
- **The list's spinner behind a page.** Leaving TV for Mine, Local files or
  Recordings while the start's channel list was on its way left its
  spinner turning nine times a second behind the page until the list's job
  gave up (25 s). It turns only while it can be seen. (`tests/test_perf_idle.py`)
- **The Reminders window counted down for ever.** Its 1 s countdown
  started with the window and was never stopped, and the window was kept:
  opened once, it ticked in a closed window for the rest of the session -
  one ticker more each time it was opened. It ticks while it is open, and
  goes once closed. (`tests/test_dialogs_freed.py`)
- **TV mode's clock** (15 s) ran from TV mode's first use to the end of the
  session, the page kept out of sight. It runs while TV mode shows.
  (`tests/test_tv_mode.py`)
- **Every cast window stayed.** `CastDialog` was exec()'d and never deleted:
  84 objects, 53 widgets and a top-level window each time, restyled at
  every theme change. It goes once it is closed and no answer of its own is
  still on its way to it. (`tests/test_cast_dialog.py`, `tests/test_perf_long_use.py`)
- **Every question stayed.** `confirm` (now `ui/message_box.py`'s), the window's
  `_choice_dialog` and `profiles_ui.ask_pin` kept their dialog for the
  session - the PIN's with the PIN still in its field. Each lets it go once
  answered. (`tests/test_dialogs_freed.py`)
- **The image cache walked twice.** The window's logos and posters share one
  disk cache and each loader walked all of it five seconds after the start.
  Once per folder now. (`tests/test_memory.py`)

Found, and left - with why:

- **Other dialogs are kept too.** About 50 more `exec()`'d dialogs are never
  deleted (open address, a channel's settings, stream health, your
  statistics, the queue, the equaliser, the catch-up list, the scheduled
  recordings, Trakt's and Simkl's, profiles'...): each stays a child of the
  window once opened. None of them keeps a timer running (each dialog with
  a timer was looked at: the connection test and the stream analysis stop
  theirs as they close, the box's windows on hide, teletext's goes), but
  each needs the cast window's look - whether an answer of its own can
  arrive after it is closed (a worker's callback on a deleted widget
  raises) - before it is let go; the ones opened most were done first. A
  list of them: every function that calls a dialog's `exec()` and never
  `deleteLater`, `WA_DeleteOnClose` or `setParent(None)`.
- **The recordings' 5 s look** runs with no recording at all (0.2 wakes a
  second). Stopping it would need every way a job is added to start it
  again, and a missed recording costs more than the wake.
- **The 12 s keep of where a film got to** runs while nothing plays (0.08
  wakes a second): starting and stopping it is the playback path's.
- **One dead weak reference per Settings window opened** (80 bytes) stays,
  held by C code - most likely a signal connection's proxy in PyQt - and
  no Python container; not found.
- **A place switch keeps about 40 bytes** (17 KB after 450 switches, the
  count of objects flat): something growing in place; not found.
- **The start's "Loading channels…"** stays at the sidebar's foot for up to
  25 s when the start place is left for Mine while the first list loads:
  the list's answer is dropped for the new mode without ending its job
  (`mw_lists._load_items`). Only its spinner was fixed.
- **A guide's parse is still ElementTree's**: 3.5 s for a week of 2 000
  channels, in the guide's own process (`--epg-index`), never the window's.

## Looking into one

The tools the numbers above came from:

- **Timers.** An event filter on the application counts `QEvent.Timer` by
  receiver (a `QTimer`'s interval and parent say whose); see
  `test_perf_idle.py`'s `Timers`. A `QTimer.singleShot` shows as a
  `QObject` of the event dispatcher.
- **What stays.** `len(window.findChildren(QObject))`,
  `QApplication.allWidgets()`, `topLevelWidgets()` and a count of
  `gc.get_objects()` by type, after `sendPostedEvents(DeferredDelete)` and
  `gc.collect()`; `tracemalloc.take_snapshot().compare_to(..., "traceback")`
  says where what stays was allocated. Count without touching the objects:
  reading an attribute of a PyQt class can make its enums and methods
  there and then.
- **What the start imports.** `python -X importtime`, and a
  `sys.meta_path` finder that prints the stack the first time a module is
  imported.
- **The network.** `socket.getaddrinfo` replaced by one that refuses and
  notes the name, as `tests/perf.py` does.
