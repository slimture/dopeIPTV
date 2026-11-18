# Changelog

All notable changes to dopeIPTV, newest first. This project loosely follows
[Keep a Changelog](https://keepachangelog.com/) and
[Semantic Versioning](https://semver.org/). Each release is also published, with
downloads, on the [GitHub releases page](https://github.com/slimture/dopeIPTV/releases).

## [Unreleased]

### Fixed

- Casting: going back in a channel says so on the card - "Going back…" and
  where to, fetched from the archive - and "Back to live…" for live, until
  the TV plays it; no more Playing for the old stream meanwhile.
- Settings sync: the same playlist added by hand on two computers was sent
  to the site again every other round, for ever, and a new password on one
  never reached the other - they are one playlist now. A history list that
  is full on one computer no longer removes the oldest titles on the
  others, and what a round brought in is kept even when the window closes
  right after it. A second profile's theme that arrives by sync is shown
  at once.
- The decision card and the messages panel take a new theme's colours
  straight away.
- AirPlay: "The Apple TV has not reached this Mac" no longer comes while a
  seek is still being readied - the new stream is the Apple TV's to reach
  only once it has been handed it. A seek also asks the provider's archive
  one time less: the channel already on the TV is known, so the archive is
  no longer probed first.
- TV: the channel names' column is as wide as the category's longest names
  need, up to 40 % of the row - long names are no longer cut at a fixed
  width, and a category of short names leaves the room to what is on. A
  name that is still too long shows whole in its tooltip.
- TV: a channel's name in the list no longer loses its last letter ("BBC
  Eart") on a scaled screen - Windows at 125 or 150 % drew it a pixel or two
  wider than it measured and the rest was cut off (#35).
- AirPlay: a stream the Apple TV is still loading - a cast just begun, a
  seek in a channel - is no longer sent again after ten seconds of
  buffering: that only started it over (it went back a few seconds at the
  start, and a seek never took). A new stream has 45 seconds to start; once
  it has played, ten seconds stuck is a stall as before. A seek reaches the
  Apple TV sooner too: its stream is cut into 2-second segments, so the
  Apple TV has the three it waits for after 6 seconds of it, not 18.
- AirPlay: going back in a channel while it is cast moves the Apple TV
  there again. Since 1.6.4 the moment was handed over at its first segment
  and the Apple TV played on where it was; it waits for three again, as
  before.

## [1.6.4]

### Changed

- Casting has a new look. While a cast runs, a card stands where the video
  was: the channel's logo or the film's poster, what is on or which episode,
  the TV it plays on, and how it goes there - Playing, Paused, Buffering…,
  Reconnecting… or Not playing with Try again. Big round buttons for
  play/pause, 30 seconds back and forward (on a channel where going back
  works) and Stop, the timeline with LIVE, the volume, and the tracks,
  timeshift and cast window as small buttons. It fits a narrow column and
  sits centred in a wide one, in every theme.
- The player has a cast button of its own, and the cast window opens with
  the TV used last on top and picked: one more click, or Enter, casts to it
  ("Cast to Living room TV").
- What plays on the TV is marked "On TV" in every list and place - TV,
  Movies, Series and a series' page, Local files, Recordings, Radio and
  Mine - and the sidebar logo's jump to what plays goes to it while a cast
  runs.
- AirPlay (macOS): the AirPlay window opens with Apple's button right under
  the pointer, ready to click, and lists Apple TVs first.

### Fixed

- Casting: the card says Playing as soon as the TV plays, also while a seek
  is still on its way (it stayed on Starting…); going live during a seek no
  longer says "Cast failed: Casting stopped." - the newer send simply takes
  its place. The tracks button shows only for a Chromecast: on an Apple TV,
  a DLNA TV or a Kodi its menu held only the cast window, which has its own
  button.
- AirPlay: going back in a channel while it is cast starts sooner - the
  moment gone back to is handed to the Apple TV once its first segment is
  ready, not three (there is no live edge to keep back from). "The Apple TV
  has not reached this Mac" no longer comes after every seek: the time the
  Apple TV has to reach a new stream starts with that stream. The AirPlay
  window opens at the top right of the cast card, and floats over the app
  only while it is in front - not over other programs.
- The messages panel was laid out before its new rows were shown: a long
  message was cut off at the top and the foot, and two were squeezed over
  each other. Each row is now shown as it is added and the panel is as tall
  as they are.
- macOS: the pointer hides again over the video after two seconds, in the
  window and in full screen. With the picture drawn on its own thread
  (1.6.3) macOS put the arrow back over it; it is now hidden the way a
  video player on the Mac hides it, until the mouse moves.
- A right click (or the menu key) on a result on the Search page opened no
  menu and sent an error report instead; the menu is back.
- A cast that gets stuck buffering recovers by itself: after ten seconds
  that nobody paused, the TV is sent the stream again from where it was -
  the channel at the live edge, or where the rewind buffer or archive had
  it, a film from its position - at most twice, then the card says so with
  Try again. It goes the way the cast's own controls go, so it never opens
  a second connection to the account, and it does not try by itself while
  something else uses the connection. Every stall and try is in the log.
- The phone remote while casting (Chromecast, a TV, Kodi or AirPlay) shows
  what plays on the TV, says "Casting to …" and drives the TV: play and
  pause, stop, seeking a film, channel up and down, and the TV's volume and
  mute (an Apple TV keeps its volume to its own remote, so the slider is not
  shown). It said "Nothing is playing" before - and Play started the player
  on this computer beside the cast, a second connection to the account. The
  system's media controls and keys, Home Assistant and the remote API follow
  the cast the same way.
- The sidebar logo's jump to what plays lands on it exactly: a recording is
  picked in Recordings (it picked the first one), and a local file deep in a
  long folder is scrolled into sight (it was picked far below the window).
- The rows over the lists - Recently watched, Continue, My stations, what
  records now - play on one click only with autoplay on (Settings ->
  Playback), as the lists do; otherwise a click picks and a double click
  plays. Popular right now in Series still opens a series' page on one.

## [1.6.3]

Room for the list, a guide that follows the category, and every place in the
same new look.

### Added

- Phone remote: a live channel in the rewind buffer can be gone back in
  from Now playing. Its bar runs from as far back as the buffer reaches to
  live; dragged, the place under the finger shows as how far behind live
  ("−4:12") and one seek goes when it is let go; the player's skip steps
  work in the buffer (forward no further than live), and Live - lit at the
  live edge, else showing how far behind - goes back to live. All of it
  through the DVR row's own paths in the player's buffer, never a second
  connection; with the buffer off, on an HLS channel or anything not live,
  nothing new shows. The state carries it small (`dvr`: behind live, how
  far back, at live, still filling, when), reckoned on by the clock on the
  phone and told again only when that would drift, so the poll's 304 holds
  while a channel plays behind live. Mirrored right to left; three new
  words in all 27 languages.
- Record in TV mode. Options has a Record tile: it records what plays
  through the player's record button's own path (from the rewind buffer or
  the player's stream, no connection of its own) - also while TV mode's
  page is in front of the picture - and while that recording runs it says
  "Stop recording · 12 min" and asks before it stops it; it cannot be
  pressed while nothing live plays. A REC mark (a red dot and REC) sits
  over the picture while a recording runs. The app's questions while TV
  mode is on - a recording's, a zap's - are TV mode's own: large, in a
  window over the full screen, answered with the remote's arrows, OK and
  Back (a game controller, LIRC and the phone's remote too). The phone's
  TV-mode remote has a Record pill in its More fold, asked first as Now
  playing's Record is (a long press asks anyway); while a recording runs,
  its red dot and time show it instead.
- Reply to an answer to your feedback. When dopeIPTV asks for more - an
  answer that lets you reply - a box shows under it in Answers to
  feedback; your reply goes with the same random code as the report and
  nothing else about you, cleaned the same way as feedback. One reply per
  answer: then it waits for the next answer, which the app asks for soon
  after, as after new feedback. The card reads as the conversation - what
  you wrote, each answer, your replies - and your own words stay on this
  computer. A conversation that is closed says so.
- Local files notices changes on disk by itself (#34): a file renamed,
  added or removed shows a moment later without Rescan, in place - the
  scroll and the row you were on stay where they were, and a folder that
  went away says so quietly. The folder shown and the library's folders
  are watched (up to 2000); a share over the network, where no word comes
  of what another computer did there, or a bigger library, is read again
  lightly every few minutes while Local files is on screen. Rescan and F5
  stay for "now".

- A recording you watched to its end says so in Recordings (Watched), and
  its menu marks one watched or not watched by hand - each profile its
  own, kept on this computer. A recording made from now on keeps its
  channel and when it began beside it, so Recordings can say them; its
  menu also opens the folder it is in.

- Room for the list. The places in the sidebar fold into a compact block
  of icons at its top - the small arrow under them, or Settings >
  Appearance - so a long list of categories has the room again; the entry
  you are on stays lit, a recording's dot and the other marks stay on
  their icons, and the arrow unfolds them. The icon rail is as it was.
- Guide, Search, Local files, Radio and Recordings can each be taken out
  of the sidebar; their places still open from their shortcuts, the
  command palette and the menus, and the app never opens on one you took
  out.
- A right click on a row's heading - Recently watched, Continue, Popular
  right now, Recording now and next, My stations - hides it at once, and
  a note says where it comes back (Popular right now keeps its one switch
  on Settings > Posters & info).
- Just the list, on Settings > Appearance: no rows over TV, Movies, Series
  and Local files, smaller headers with the count beside the name, Movies
  and Series as rows and TV compact - your own choices are kept, and
  everything is as you had it once it is off. "What the places show" on
  Settings > Appearance holds all of it, each profile its own.

### Changed

- A running recording stays on the activity line at the sidebar's foot
  for as long as it runs, instead of a note for a few seconds. A click on
  it asks "Stop recording ...? What has been recorded so far is kept." -
  Stop recording or Keep recording - and nothing stops without that
  answer; with several, the panel lists each with its own Stop recording.
- The sort's menu says what every category follows - "As in all
  categories (Provider's order)" - and the order picked is checked in it;
  "Default" is called Provider's order, here and in Settings; A→Z is the
  language's own (A→Ö in Swedish).
- Every place's header is one line at one height: the category's name
  small and calm at its start (cut with "…" to its room, never over
  anything), the way back before it where there is one (Local files,
  Recordings, Radio), then the filter, the sort - its mark alone, the
  order in its tooltip - and Grid / List. The big title is gone; the count
  is in the status line.
- The source's name over the categories is gone: the avatar at the
  sidebar's foot opens the same menu, and the status line names the
  source.
- Recordings: grouped by day, the newest first - Today, Yesterday,
  Earlier this week, Last week, then by month - under headings over the
  grid and the list alike. The sort's menu puts them oldest first, by
  name, under their channels, the longest or the largest first, and has
  Unwatched only at its foot; the choice is kept.
- Local files: a sort for every folder - by name A-Z (as before) or Z-A,
  the last changed or the oldest first, the largest first, by type -
  folders always first, the choice kept; in the library the last changed
  first is the newest added. A folder read bit by bit is put in order once
  it is all there, and what changes on disk keeps that order.
- Phone remote: More opens on an even row of three tiles - Subtitles and
  sound, Sleep timer, Record - each its icon over its words, the next
  programme under them; Record turns red with its time while a recording
  runs (a tap asks to stop it) and the sleep timer shows its minutes. TV
  mode's remote has the same three tiles in its fold.
- Movies: a film no longer opens a page of its own in the middle pane,
  which said again what the detail column says. A click picks the film
  and the detail column shows it - with Continue, how far it got and the
  time left for one under way; a double click or Enter plays it, as in the
  lists; a card in Continue goes on from where it stopped. Popular right
  now's cards are picked and played the same way.
- The Phone remote window closes by itself once a phone has connected,
  and a note says so - its job is done; open it again for another phone.
- When the Next episode card comes can be set in seconds too - 10, 20, 30
  or 45 s before the end, besides 1 to 10 minutes (Settings > Playback).

- macOS draws the picture on its own thread by default in a release too,
  as Linux and Windows do (a release there drew on the window's thread
  while the own thread was tried in test builds). Settings > Picture & sound and
  DOPEIPTV_VIDEO_THREAD=0 still turn it off.
- Closing multiview yourself (its window, or the Multiview entry again),
  the cell you were following - the one you hear - goes on in the window:
  a live channel as a zap, a film or an episode where the cell got to,
  every cell's connection let go first. Not when multiview made way for
  something you played in the window.

- The Guide follows the category you pick in the sidebar: with the guide
  open over TV, picking Movies, News or any other category (or arrowing
  through them) shows that category's channels in the guide, which stays
  open - before, it closed and the category's channel list came instead.
  A folder of favourite channels works the same.
- A double click on a TV category (or a folder of favourite channels)
  opens the guide on it, and its menu has Show in the guide: one click for
  the channels and what is on now, two for the whole guide. Double-clicked
  again, the category the guide shows goes back to its channel list.

- Local files is a place of the new look, like Mine, TV, Movies and
  Series: the folder's name ("Local files" at the top) with how many
  folders and files it holds, a way back to the folder above ("Back to
  Films" - and the mouse's back button, Backspace), the filter, Rescan and
  the Folders / Library switch in its header, then "Continue" - the files
  in progress under the folder, from where you stopped, without the resume
  question - and the folder's folders and files as rows with their
  pictures, size and, once played, length. The library shows its series,
  films, folders and music as posters or rows (Grid / List, remembered).
  Everything the list did stays: a file plays on through its folder, the
  menus and the queue, the search through the subfolders, a library's
  series page, the phone remote's Local files. On a narrow pane the
  controls take rows of their own under the title.

- Recordings is a place of the new look too: the folder's name with how
  many recordings and what they take on the disk, the filter, ⋯
  (Scheduled recordings, Series recordings - Settings > Recording -, a new
  folder, the folder itself, another recordings folder, clear the finished
  ones) and Grid / List in its header; "Recording now and next" at the top
  - what records, with its red mark and how long it has run, and what is
  to come, soonest first, each with its stop, edit and cancel in its menu;
  then the recordings as rows or 16:9 cards: a frame of each (found once,
  never of one still recording), else its channel's logo, with its
  channel, day, length and size and how far you watched it. A recording's
  name no longer repeats the date its file was named with. Active &
  scheduled and Upcoming show their recordings with their state (REC with
  its time, Scheduled, Done, Failed and why). Everything the list did
  stays: play, play in mpv or VLC, rename, edit info, move, delete, the
  scheduled recordings' own actions, the detail panel.

- Radio is a place of the new look as well: the list's name - Most voted,
  a country - with how many stations (or countries, languages, genres) it
  holds, a way back to the countries while one is open ("Back to
  Countries" - the mouse's back button, Backspace), the filter (under
  Search the whole directory's search, as before) and Grid / List in its
  header; "My stations" as a row of small cards over every list of
  stations, then the stations as rows or tiles with their logos, where
  each is from and what it plays, a star on your favourites, the one
  playing marked with what it says is on. The countries show their flags
  and how many stations each has. Everything the list did stays - the
  menus, favourites and their folders, the station's website, the detail
  panel - and nothing more is asked of radio-browser.info than before.

- macOS draws the picture on a thread of its own too, as Linux and Windows
  do: Qt cannot hand its OpenGL to another thread there (AppKit stops the
  app), so the render thread draws with an NSOpenGLContext of the app's own
  - attached to the window and kept up to date on the main thread, drawn and
  flushed on the render thread under its lock (media/video_macos.py). Any
  fault falls back to the window's thread as before; the release build's
  self-check draws through it on the macOS runners.

- Settings' group "Accounts and integrations" is "Services" - one word like
  the others, so it fits the sidebar in every language (it was cut off).
- Movies' and Series' cards show a favourite's star and Watch later's clock
  after the title, as the TV place's rows show a favourite channel's star -
  the same marks everywhere; a star given from the menu shows at once.

### Fixed

- The preferred subtitle language was chosen but not shown when the
  stream tagged its track another way than the setting keeps it - Swedish
  is kept as "swe", many streams say "sv" or "sv-SE". The app's own
  subtitle and audio languages now go to mpv every way a stream tags them,
  and a track counts as in the language whichever it uses, for every
  language the app knows (channel_prefs.all_spellings, same_language).
- macOS: the app could crash when a sound device came or went - an AirPlay
  device found nearby, headphones - inside libmpv: its CoreAudio output is
  handed the system's device change after it has gone. The player now asks
  mpv for its AVFoundation output first, which listens to no device change,
  and CoreAudio after it (a libmpv without it, or a Nerd mpv option of the
  person's own, as before).
- Local files with no folder added said another place's category ("1") as
  its folder, and offered no "Add folder": only a folder the sidebar has
  picked is its folder now.
- The phone remote's Radio was empty for anyone who had kept no station:
  it showed My stations alone. Now the most voted stations show under
  them (alone when none is kept), asked once and kept five minutes.
- macOS: the app hung for good going into or out of TV mode (from the
  window or the phone remote), and could going into or out of full screen,
  while something played. AppKit draws the picture's view itself during
  the full screen transition and takes its OpenGL context's lock there and
  then, on the main thread that held Python's lock as it asked; the
  picture's render thread, in a frame, held that lock and waited for
  Python's. Such window changes are now made with every picture's frame
  ended first and the next held back until the change is made
  (media/video_macos's `window_change`, `WindowChanges` on the main window,
  the pop-out and multiview).
- An answer to your feedback not yet read is said again as soon as the app
  starts: the message was kept only while the app ran, and after a restart
  it came back only once the site was asked again - for a report already
  answered, up to two hours later.
- Mine played a title, a channel or a station on one click whatever the
  autoplay setting said: its lists now follow Settings > Playback as the
  others do - with autoplay off one click picks and a double click or
  Enter plays (a double click always does); New episodes still open a
  series' page on one click.

- The jump to what plays (the sidebar's logo, Now playing) found a radio
  station only when it was in the list on screen: it now goes to Radio and
  the list it was played from - a country, language or genre opened again -
  or My stations, and picks the station there.

- Mine > History: several rows can be picked (Ctrl or Shift and a click -
  nothing plays then) and taken out at once with Delete (a Mac's delete
  key) or Remove in the menu of one of them; a click on its own plays as
  before.
- A series' page put Up next on E1 while E2 played, until a minute of it
  had played: what plays now is where you are (a started episode further
  on still comes first).

- macOS: hovering the timeline over the video, the time under the pointer
  went behind the bar while the picture had its own thread (the picture's
  own view is over the window's widgets there); the time is now a small
  window of its own, always on top. Linux and Windows are unchanged.

- An answer to your feedback could take hours to show: the app asked for
  answers every quarter of an hour on the first day and every six hours
  after. It now asks every five minutes on the first day, every two hours
  after, and whenever the app comes to the front - an answer written a
  moment ago shows when you switch back. An answer still unread when the
  app starts says so again in the activity panel (it stays there until
  read), not only by the count in the menu; read however the window was
  opened, the message goes.

- Recordings: the Delete key did nothing in Upcoming; it cancels and
  removes the recordings to come picked there, as in Active & scheduled.

- In a right-to-left language, the muted line under a Local files card
  that began with a Latin name (an artist, an album) was laid out left to
  right, its parts in the wrong order; it reads from the right now, each
  part whole.

- Local files: a folder holding only video files got no posters or frames
  (only one with subfolders did), and a clip shorter than two minutes never
  got its frame; Folders picked while the library was still being read
  could end up showing the library; the status line called a folder's
  files "recordings".

- Enter in a list (TV's channels, Movies' and Series' rows) plays the row
  picked, as a double click does; it did nothing unless a channel number
  was being typed.

- With the player muted, another program's sound (Spotify) crackled at
  every channel change: the player opened a new stream to the system's
  sound server for each channel, muted or not, and the sound server
  renegotiated. While muted it now opens none (mpv's null output keeps the
  time); unmuting brings the system's output back, or the one your mpv
  options name, with the sound track you had picked.
- Nerd > Diagnostics said "Window thread" before anything had played on a
  computer that draws the picture on its own thread: it now says the own
  thread starts with the first video.

### Removed

- The DOPEIPTV_VIDEO_THREAD=force test switch: forcing Qt's own drawing onto
  the render thread crashes on macOS (AppKit allows it only on the main
  thread), so macOS gets a drawing path of its own instead.

## [1.6.2]

A new look: the sidebar is the app's structure - Mine, TV, Movies and Series
as places of their own, each with the same header - and the app opens where
you left off.

### Fixed

- Going from a grid back to rows (Grid on and off, or Movies' posters and
  then TV) left an empty strip to the left of the channels until the app
  was started again.
- A place's header: the filter, the sort and the Grid / List switch are
  one height and in one line (the filter stood taller and higher on
  macOS); a long category name is cut with "…" and shown whole in its
  tooltip; on a narrow pane the sort shows its mark alone, so the posters
  never run past the edge. The TV place's header and Recently watched line
  up with the filter and the channels under them, and a row of small cards
  fades out at an end with more that way.

### Changed

- Phone remote: TV mode's remote fits the phone it is on. The d-pad takes the room left below Now playing - bigger on a tall phone, with the volume and channel rockers under it, beside it on a small one - and on its side the keys sit next to it; from 320 x 568 up nothing scrolls as it opens, the safe areas stay clear and every key is 44 px or more. Play/pause and mute sit with Back and Options, the touchpad switch with the number keys.
- Phone remote: Now playing stays compact. Subtitles and sound, the sleep timer, Record and the next programme fold behind More (TV mode's remote folds its sound and sleep timer the same way); the phone remembers whether it was open. While folded, a recording's red dot and time and the sleep timer's minutes stay on the card - a tap on the dot asks to stop the recording.
- The sidebar is the app's structure: Mine, TV, Movies, Series, Guide,
  Search, then - under a hairline - Local files, Radio and Recordings.
  Home is gone; Mine holds what were Favorites, Watch later and History
  (as tabs in its header for now), and every place has the same header:
  its name and how many it holds, with Mine's tabs beside it.
- The app opens where you left off - each profile its own place, the
  Guide or Search over the list you came from - or always on Mine
  (Settings > General > Start). "Open Home at startup" became "Always on
  Mine"; Home's other settings went with it, a popular shelf switched off
  stays off at the top of Movies or Series.
- Back and Esc from the Guide, Search and a series' page go back to the
  place you came from; a profile switch goes to that person's place.
- Popular right now becomes a row at the top of Movies and of Series,
  switched on or off in Settings > Posters & info. Followed series and
  their news are on Settings > Mine.
- The donate heart asks alone, one week a month as before; Home's card is
  gone with Home.
- Series is a place of the new look: the source's series categories in the
  left column as before, and where the list was the category's name with its
  count and order, a filter, the sort (recently updated first where the
  source says when, else its own order; each category keeps its own) and a
  Grid / List switch that is remembered. Over the series: "Popular right
  now" - this week's TMDB chart matched to the source's series, ranked, a
  category's own in a category; hidden when nothing matches or TMDB is off -
  and "Continue": each series under way with its next episode ("S2 E5 · 18
  min left") and how far it got; Enter or a click continues it from there
  without the resume question, as the series page's Continue does, and the
  place stays. Continue is worked out from what the app keeps (the resume
  points, the series page's episodes it holds) - never a request per series;
  a series whose next episode is not known shows its last watched one, and
  its page works out what is next. The series as posters (a followed series
  with news says "New episode") or as a list; a click opens the series' page,
  and Back comes to the grid exactly as it was. Everything the list did stays:
  its menu - for every card selected with Ctrl or Shift -, favourites and
  folders, Watch later, Follow, hidden and locked categories and the PIN, a
  kids profile, renamed and hidden series, posters from TMDB, the phone
  remote's Series, and the middle pane following what plays. A "Following"
  category lists the series followed, the ones with news first. Fast with
  tens of thousands of series: painted cards, pictures only for what is on
  screen.
- TV is a place of its own in the new look: over the channels a header
  with the category's name, how many channels and in which order ("48
  channels · by number"), the sort and a Compact / Airy switch (kept per
  profile); under it Recently watched, this source's channels played last
  as small cards with what is on now (hidden while there are none). The
  channels are rows: number, logo, name, what is on with the time it began
  and a bar of how far, and "Then 21:30 Debatten" - on the same line when
  compact (in a column of its own on a wide pane), under the bar when airy,
  with a bigger logo. The one playing, here or on a TV, sits on the
  accent's wash with its bar and Playing; a narrow pane puts the name over
  what is on. It is the same list as before: zapping, numbers typed,
  channel up and down, the channel's menu, favourites and their folders,
  stream health, a box's tuners, Nerd's numbers and technical line, the
  filter and multiselect all work on it as they did, and the guide is read
  from memory only. A "channel" that is only a heading in the provider's
  list ("-= Sweden =-") is painted as a heading, as in the guide. A card of
  Recently watched plays the channel - as its row when the list shows it -
  and its menu can take it off; a channel played in private viewing never
  gets there, nor one hidden or of a locked category, nor one for adults in
  a kids profile. A grid of channel tiles keeps working as before.
- Mine is a page of its own: what you were watching (films, episodes and
  local files, any source), your favourite channels live now - in the
  order you usually watch at this hour and weekday, "Usually watched now"
  on the top ones, a heart that takes one out and puts it back where it
  was -, your favourite films and series, Watch later as a queue you
  order yourself (move up, down or drag; how many and how long; a title
  you watched leaves it by itself), new episodes of the series you follow
  and History as a timeline by day with Play, Continue or Play again. One
  search and a row of chips (All, Channels, Movies, Series, Radio, Local
  files) filter all of it; your favourite folders are a tab of their own,
  dragged into order or from one folder to another. Another source's
  titles say so and play as the Search page plays them; a kids profile,
  locked folders and private viewing behave as the lists do.

### Added

- One set of painted cards for every place (`ui/media_cards.py`): posters,
  wide cards and list rows with the same pills (Live, New, Playing,
  Watched), channel rows with what is on and next, a sideways Continue
  row, the shared page header - one view per list, pictures only for what
  is on screen, mirrored right to left, named for screen readers.
- Movies is a place of its own, in the look every place now shares: the
  source's film categories in the left column as before, and beside them
  the category's name and count, a filter, the sort and Grid / List
  (remembered); "Popular right now" - this week's chart matched to the
  source's own films, ranked - and "Continue" with the
  films in progress; then the films as posters or rows with their year,
  length and rating, New for one added lately, Watched and how far it got.
  A category of 20,000 films opens at once: a card works out its picture
  and words only as it comes on screen.
- A film opens its own page, built as a series' page is: the backdrop,
  the poster, year · length · genres · rating, the plot with More, one
  button - Play, or Continue from where it stopped, with how far -
  Favorite, Watch later, the film's menu on ⋯, and the cast when the
  source or TMDB names it (a click finds their other films). Back, Esc or
  the mouse's back button go back to the grid exactly as it was. It plays
  as the list played it - resume points, watched marks, Trakt and Simkl,
  History, subtitles remembered - and opening it never starts anything.
  Everything the film list did stays: the right-click menu, several films
  at once with Ctrl or Shift, favourites and folders, Watch later, hidden
  and locked categories, kids profiles, private viewing, TMDB matches, and
  the middle pane following a film played from elsewhere.

## [1.6.1]

A calmer guide, a night mode that tames loud adverts and bangs without
raising anything, the visualiser for channels that are sound alone, settings brought over to a new computer from the
welcome, the same menu bar on every system, and fixes - above all to
how connections to the account are counted.

### Added

- The welcome brings your settings over from another computer: "Already use dopeIPTV on another computer? Bring your settings" on its first page takes the sync code from there (joined as Settings > Sync joins it, fetched off the window's thread) or restores a backup. Profiles, settings and favorites come along; sources and watch history too where the other computer syncs them, and then this one syncs them as well. With sources the welcome closes and loads them (and asks "Who's watching?" with two profiles or more); without, the connect step says why. A wrong code, nothing under it or no network is said plainly and leaves sync off; a modified build is refused as in Settings. A restored backup is staged for the next start, with Quit to get there.
- An audio-only stream from a source (an iHeart channel in an M3U, a DVB
  radio channel, a film that is only sound) shows the visualiser instead
  of a black picture, decided once mpv has listed its tracks - a stream
  with video is never touched; two more styles, Vectorscope and
  Frequencies, and Waveform no longer draws over a checkerboard.
- Local files can be read again on demand: Rescan, a small button in its
  bar beside the view switch, or F5 while Local files shows (a key of its
  own in Settings > Keyboard shortcuts). Folders view lists the folder
  again, the selected row kept; the library walks its folder again at
  once, the list as it is meanwhile and the progress as on a first visit.
  No watcher: only when asked.

### Changed

- The guide is calmer and denser: rows 44 px high instead of 60 (TV
  mode's guide keeps its large ones), flat cards with a small gap instead
  of borders and bevels, the programme on the air tinted with the accent
  and a thin strip of how far it got instead of a thick accent frame, the
  past dimmed, and one thin accent line at the current time through the
  board and its header. A wide card says when it starts under its title;
  a card too narrow for a readable title shows none ("Ny…" is gone), its
  tooltip has the title and times.
- The guide's channel column is narrower (160 px), its names in a quieter
  normal weight, the catch-up mark small and after the name. Its edge can
  be dragged wider or narrower (a double-click puts it back), remembered
  on this computer; a narrow guide shows the logos alone, the name in the
  tooltip and a short form of it where there is no logo.
- A guide row with no programmes is an empty field, why in its tooltip -
  said once over the board when no channel has a guide, not on every row.
  A "channel" that is only a heading in the provider's list ("-= Sweden
  =-", "#### SE ####") is drawn as a heading over the channels below it,
  with nothing to click.
- The guide's line at the time now runs behind the programmes and shows
  between them; Settings > Guide > "Now line over the programmes" puts
  it in front.
- The menu bar is the same on Windows, Linux and macOS: File, View,
  History and Help, with Settings and Quit in File and About last in
  Help, where people look for them, in place of the one "dopeIPTV" menu
  on Windows and Linux. macOS looks as before: Qt moves About, Settings,
  Quit and the supporter entries into the application menu.
- About and the version can be found in Settings: General ends with an
  "About dopeIPTV" row showing the version, which opens About over
  Settings, and the Settings search finds it by "about", "version" or
  "update" (issue #31 - it was only in the menu bar's first menu).
- Server mode, its port in use: the app says whose it is - dopeIPTV's own
  server (a `dopeiptv --server` started from a terminal or at login, or
  another window) or another program - and offers the next free port
  (past the remote API's and the phone remote's) in its note and on the
  Nerd page's line; one click stores it and starts the server there,
  nothing changes without it. `dopeiptv --server` logs the same, with the
  `--port` to start with. Any other reason a port does not open is said
  as before.
- About says what the app is today - IPTV and the TV at home: Xtream
  Codes, M3U, Stalker portals, Enigma2 and other boxes, Jellyfin, Emby
  and Plex, with a guide, recordings, TV mode and casting - not only
  "Xtream Codes and M3U playlists", in every language.
- The three search boxes say what they do (#32): the one over the list
  "Filter this list…" (in a folder of Local files "Search this folder
  and its subfolders…"), the one by Categories "Find categories by name
  or channel…" (or movie, or series), and the Search page "Search
  everything in all your sources".
- A click on the picture brings the controls up and pauses nothing; the
  centre button, the play/pause button and the keys pause. A click meant
  to bring the controls back or give the window focus stopped the picture.
- Home's back button stays at the top when the page scrolls, so going
  back is always one click away; it scrolled away with the featured strip.

### Fixed

- Local files no longer freezes the window on a big folder or a network
  share. A search in a folder held it while each match's folder was read
  for its album's name (1.5 s at a time on a share with 15 ms a round
  trip); "View: folders" held it while every subfolder was (5 s for 1,350
  folders, 3 ms each); the library held it for seconds at every slice of
  its walk while each music shelf's albums were listed for a cover (21 s
  for a thousand albums). All of that is read on the pool now, nothing on
  the window's thread over a few ms. A folder's rows come in as they are
  read - the first within a tenth of a second instead of after the whole
  folder (34 s for 5,300 entries) - the folders first and the files in
  order as before, and a file played meanwhile still plays on through
  the whole folder. A search's matches are added below the ones found
  (the selection and scroll stay) and no longer wait behind thumbnails
  and art lookups; the library's cache is read from disk once a session,
  written off the window's thread, and a walk that finds nothing new
  leaves the list as it is.
- The app's notices are in one place instead of four. What it is doing
  (a list or the guide loading, a folder read or searched, the network
  scanned, a sync, an update check, a download, a recording saved as MP4)
  was a strip at the top of the list that pushed it down and up, or words
  in the status line that stayed; what it told you was a dark note at the
  window's foot, or the status line in red. Now both live in one corner,
  away from the video: at the foot of the sidebar, just above the source
  avatar, one calm line - an icon for the kind of work, what you started
  first (else the oldest) and "+2" for the rest, an accent bar when the
  progress is known ("Loading programme guide… 45%"), a gentle spinner
  when not; guides loading together are one line ("Loading guide · 3
  sources"). A click opens every job with its progress and a Cancel where
  it can stop. Short work never flickers in (it shows after 0.4 s, then
  for at least 0.6 s), every job ends with its work, a newer one of the
  same kind or its own time limit. On the icon rail it is a ring with a
  count; on Home it sits at the same corner. Next to it, at the bottom of
  the list, small cards for errors, offers and notes: a title, a line, one
  action and a dismiss; three at most, the newest on top and "+N more"
  for the rest; an offer stays above the passing notes until answered, an
  error until dismissed, a passing note goes after 6 s (not while the
  pointer rests on it); the same note twice is one card with a count.
  Nothing is drawn while the video fills the screen, TV mode shows or the
  picture is popped out - cards wait until you are back. The status line
  keeps the list's count and what plays; what is about the picture
- The app's notices are in one calm place instead of four. What it is
  doing (a list or the guide loading, a folder read or searched, the
  network scanned, a sync, an update check, a download, a recording saved
  as MP4) was a strip at the top of the list that pushed it down and up,
  or words in the status line that stayed; what it told you was a dark
  note at the window's foot, or the status line in red. Now all of it
  lives at the foot of the sidebar, just above the source avatar, and
  nothing appears over the list or the video. One quiet line says the
  work - an icon for its kind, what you started first (else the oldest)
  and "+2" for the rest, an accent bar when the progress is known
  ("Loading programme guide… 45%"), a gentle spinner when not; guides
  loading together are one line ("Loading guide · 3 sources"). Short
  work never flickers in (it shows after 0.4 s, then for at least
  0.6 s), and every job ends with its work, a newer one of the same kind
  or its own time limit. Passing words ("EPG cache cleared", "Copied") are
  that line's for a few seconds and gone - no box. An error or a message
  only adds a small count to the line; a click opens "Working on" (every
  job with its progress and a Cancel where it can stop) and "Messages"
  (the newest first, with its action and a dismiss, the read ones faded,
  Clear all). A stream that fails says so on the video and nowhere else.
  Only what needs a decision now - the server's offer of a free port - is
  a small card of the sidebar's width right above the line (its words in
  two lines at most, one action, a dismiss); one at a time, more wait as
  a count. The line and the card are part of the sidebar: the categories
  above simply get shorter while the card shows, stay scrollable and keep
  the chosen one in view, and the avatar stays on screen at any window
  height. On the icon rail the line is a ring with the count and the card
  sits beside the rail; on Home they keep the same corner. Nothing is
  drawn while the video fills the screen, TV mode shows or the picture is
  popped out - messages and decisions wait until you are back. The status
  line keeps the list's count and what plays; what is about the picture
  (loading, reconnecting, a typed channel number) stays over the video,
  and real questions stay dialogs. Readable on every theme, mirrored right
  to left, read out by screen readers.
- "Loading programme guide…" no longer stays at the top of the list after
  the guide is in. The strip was shown by any guide download - also one a
  lookup started on its own (the detail panel, a past programme, TV mode),
  or the previous source's still going after a switch - and nothing of
  those ended it, so it stayed until a category change hid it. Now the
  window owns each guide load under a token of its own: only that load
  shows the strip and its end ends it (done, from the disk cache, failed
  or no guide at all), a late word from an older load changes nothing, a
  list loaded meanwhile leaves it alone, and a load that says nothing for a
  minute is let go with a calm note in the status line. TV mode's now and
  next no longer download and parse the guide on the window's thread.
- Night mode brings loud parts down and nothing up. In 1.6.0 its leveller
  (`dynaudnorm`) did little to a bang and lifted quiet passages, making
  plain speech louder and its level ride with every pause - "louder, and
  it wobbles" - and it held the sound back by 3.5 s at every start. Three
  compressors that only ever turn down now take its place: a slow one
  for a loud advert break, a loud scene or a channel mastered hot, one for
  an explosion or a shout, and a quick one for a shot or a door. Speech at
  broadcast level stays under all three, so the news sounds as without
  it (a channel mastered hotter comes down, steadily); measured, bangs over quiet dialogue end up 8 dB less loud,
  an advert 11 LU louder than the speech around it about 4 dB nearer it,
  and nothing is ever louder than with night mode off. Turning it on or
  off from the player's menu or Settings says so in the picture's corner
  as the N key does (the status line without a picture), once whichever
  way it was switched; a player whose FFmpeg cannot build the filter says
  "Night mode could not be turned on for this stream" instead of claiming
  it is on, and a stream's sound that did not start through it is brought
  back without it rather than left silent.
- TV mode: Stop pressed while a dropped channel waited for the app's
  reconnect - the Stop key over the page or the picture, the phone
  remote's or Home Assistant's - stopped nothing, and the reconnect then
  started the channel again and brought the picture back over the page.
  It now stops it as the Stop button does (1.6.0 fixed this for the
  button); the media keys' Stop, and the phone's and Home Assistant's
  outside TV mode, too.
- Trakt and Simkl: a quick switch to another profile and back while a
  sign-in was being renewed could leave the profile's new client with the
  refresh token Trakt had just retired, and its next renewal then signed
  the profile out of Trakt. A profile's sign-in is now kept once for every
  client made for it, renewed by one of them at a time, and a renewal's
  answer lands only on the sign-in it renewed: never over a newer one,
  and never after Sign out, which a renewal still on its way used to undo
  by storing the tokens again.
- Keyboard shortcuts did not sync between computers: sync still looked
  for the keys the editor kept before 1.5.0. They sync now, and only
  between computers of the same system - macOS with macOS, Linux with
  Linux, Windows with Windows - since ⌘ is not Ctrl and each system keeps
  keys of its own. A computer leaves another system's keys as they are,
  and its first sync after the update takes the keys the other computers
  of its system already share.
- A cast was counted against the source in use, whichever source it came
  from: a cast of what plays from another source (started before a
  switch, by failover or from the Search page) is now asked about and
  counted on that source's account, and server mode sees it there. Cast
  after a source switch, what plays went to the TV as the new source's
  channel of the same number; it now goes by the address it plays from.
- A live channel that dropped with reconnects off stayed counted as a
  connection until Stop, so a test asked for nothing and server mode
  refused devices for it; once the player gives up on it, it no longer
  counts (a stall, and the slow reconnect, still do).
- A rewind buffer kept streaming by a recording or a cast after the
  player moved on counted as a connection on every source; it now counts
  only on the source its channel is from, so server mode no longer
  refuses another source's devices for it.
- A web stream (YouTube, Twitch through yt-dlp) or a radio station
  counted as a connection of the source in use; it is no provider's and
  is no longer counted, so it brings no question and no refusal from
  server mode.
- A recording of what plays that went on over a new connection lost its
  source: it was counted on every source and lost that source's VPN rule
  and own network route. It now keeps the source of the channel it
  records - also when that channel was from failover, the Search page or
  a source switched away from, where it went on with the new source's
  channel of the same number; it now goes on at the address it played.
- The welcome's connect step on a low screen: the first time it showed, wrapped text (the box finder's note above all) was cut off and the card ran past the window. The step now scrolls inside the card, and what Find on my network says sits under the link at the field's width.
- A series' page played an episode on one click, whatever "Auto-play preview on selection" said. It now follows that setting as the lists do: off (the default), one click picks the episode and a double click or Enter plays it.
- The welcome's connect step showed the MAC address, the media server sign-in and Find on my network for Xtream until another kind or a language was picked.
- "Night mode could not be turned on for this stream" - and why a typed
  channel number did not play - wraps in the picture's corner instead of
  running off the edge of a narrow docked player.
- On macOS the command palette names About, Settings, Quit, the
  supporter key and Donate by the application menu they are in
  ("dopeIPTV"), not Help or File.
- After an upgrade from 1.5, a row removed from Recently viewed, a title
  removed from Continue watching or a series no longer followed in the
  first profile is gone from the copy the first start left for a
  downgrade too - before, it stayed there, and in every backup made
  after.
- Night mode (N) and teletext (T), new in 1.6, no longer stay without a
  key for good when an earlier version's shortcuts gave N or T to
  another action: nothing is stored for them, so once the key is free
  again they have it.
- The loading indicator: a playlist address (a .m3u or .pls station, a
  yt-dlp playlist) says "Connecting…" and then "Still loading…" until
  its entry plays, not "Buffering…" after a second; the second line of a
  wrapped note is no longer cut at the bottom (Chinese, Japanese, the
  ellipsis); the pop-out's own play/pause disc no longer sits under it
  taking clicks.

- A cast of what plays from another source than the one in use (started
  before a switch, by failover or from the Search page) checked the
  trusted certificate of the source in use, and a Kodi or an Apple TV got
  that source's user agent, headers and proxy. It now asks about the pin
  of the source the stream comes from, and the receiver gets that
  source's own options.
- A scheduled recording that came due while something played from the
  same account opened a second connection without a word. It is now
  counted as one started by hand: its channel playing, it is recorded from
  the player's rewind buffer (from its start, as far back as the buffer
  holds) or the player's stream, with no connection of its own; with room
  on the account it opens its own; over a full limit, or one the provider
  does not say while the app already holds a connection to it, it waits -
  nothing opened - with a note offering "Record anyway", starts by itself
  once there is room (Stop, or play its channel), and fails at its stop
  time saying no connection was free.
- The same provider account added twice (another name, other categories,
  or as Xtream and as its M3U) counted each source against a limit of its
  own. Sources with the same server and user name now share one count and
  one limit: questions, server mode's room and Nerd > Diagnostics' In use
  now (one line for the account).
- The phone remote follows what plays: when the channel changes - on the computer, from the page's channel up and down or from another remote - its row is marked and scrolled into view, smoothly and centred, once your own scrolling has rested a moment. A channel the list shown leaves out (Favourites, a search) gets a "Show in list" button under Now playing instead of the page switching lists by itself. A channel playing from another source no longer marks a row of the same number in this one's list.


- The phone remote fits small phones: from 320 px wide (iPhone SE 1st generation, small Androids) and on its side, the bar's tabs and search, the dock's volume and keys and the remote's d-pad and keys wrap or sit side by side as there is room, every button is at least 44 px, the remote's labels are never cut mid-word, and the notch, the home bar and the status bar are kept clear.
- Scrolling on the computer no longer stutters while the phone remote is on: a poll never loads the programme guide on the window's thread any more (a guide not yet in memory was downloaded and parsed there, freezing the window for seconds), now and next are looked up off it, the playing channel and the favourites are worked out again only when they change, the same state again costs the phone nothing (304), and the page asks every 3 s instead of 1.5 s once nothing has changed for a while (every second just after a tap).
- The phone remote when an episode plays: Now playing names the series with "S2 E5 · Title" under it, the dock's keys are the previous and next episode (as the media keys and autoplay go, where each got to without the resume question nobody at the computer would answer), and "Episodes" opens the series' seasons - Specials last - and their episodes with length, watched and how far, the playing one marked and scrolled to and followed to the next; a tap plays one as the series page does (resume, marks, Trakt and autoplay's queue). The episodes come from what the series page already holds, else are fetched once off the window's thread, from a cache built only when something changed (and a 304 for the same again). TV mode's remote on the phone is unchanged. The media keys' Previous now goes to the episode before, as Next goes to the one after.
- The phone remote seeks: in a film, an episode, a local file or a recording, drag the bar on Now playing (the time under your finger shows while you drag; it seeks once when you let go) or skip 10 s back and 30 s forward, as the player's own buttons do - through the player's seek, never past the end, and not in a live channel. A tap on a search box brings up the phone's keyboard at once, and TV mode's search on the TV can be typed on the phone's own keyboard instead of walking the one on screen with the arrows.
- The phone remote changes subtitles and sound and sets the sleep timer: "Subtitles and sound" on Now playing (and on TV mode's remote) lists what plays' audio tracks and subtitles as the player's own menu names them - teletext pages, forced and external ones (now named so in the player's menu too) - with the one on marked, and a pick goes through the player's own path, so a title's remembered tracks and a channel's teletext page are kept as on the computer. "Sleep timer" sets the player's own timer to TV mode's steps or turns it off, and Now playing shows the minutes left.
- The phone remote records: "Record" in a row of its own on Now playing, away from the playback keys, records the live channel that plays as the player's record button does - from the stream already playing, no second connection - after "Record …?" (its "Don't show this again" is kept in the app for every phone; a long press asks anyway), and turns into "Stop recording" with its time, which always asks first. The next programme can be recorded (with the recording padding, into the same list of recordings) or reminded, and Recordings shows what records now and what is to come at the top - a recording waiting for a free connection offers Record anyway. Whatever the app would ask the computer in something the phone asked for - a connection one too many, a box's tuners, a recording in the way of a channel - is now asked on the phone in the same words with the same choices, and nothing goes ahead without an answer there; it no longer waits on the computer's screen with "Answer on the computer" on the phone.
- The phone remote plays everything the app does, not only channels: Movies and Series by category, Recent (Continue watching and History), Local files by folder with the folder search, Radio (your stations and a search) and Recordings, in one row of sections that fits a 320 px phone; Back - the page's arrow or the phone's own gesture - goes up a level. Lists come a page at a time from caches built off the window's thread with small posters, show what the computer shows (no hidden or locked category, nothing for adults for a kids profile, a portal's lists only as already loaded) and play through the window's own paths, so resume points, marks, Trakt, the connection count and the one-connection rule hold.
- The middle pane follows what plays: a channel, episode, film, local file, station or recording started from outside it - the phone, the remote API and Home Assistant, the media keys, channel up and down, Home, the command palette - takes it there (TV and the channel's category, the series' page with the episode marked, Movies and the film's category, the file's folder, Radio, Recordings), never while you type in a field, with a dialog open, in TV mode or for a play from the pane itself. Channel up and down with Series or Movies on screen now go through the source's channels instead of doing nothing.

- A series' page scrolls smoothly: a scroll step moves what is drawn and
  paints only the strip that comes into view (it painted every card on
  screen again, each with its picture smooth-scaled anew, and the header's
  backdrop too), a card's picture is scaled to its size once, and a
  picture that comes in paints only its own card. Pictures everywhere -
  lists, Home, posters, Search, the series page - are decoded once, on the
  loaders' threads and without holding up the window, already at their
  size; the window only puts them up (each was decoded a second time there,
  about 5 ms for an episode's still). Nothing looks different.
- Local files: a search in a folder looks through all its subfolders
  too, not only the files in the folder itself (#33). Each match says
  which subfolder it is in, the nearest come first, and a match plays as
  it does from its own folder - an album plays on through that album,
  an episode on into the next. The walk runs in the background and
  starts over as you type or open another folder, so a slow network
  share never holds the window up; it stops at 500 matches or 30,000
  files and folders and says so. Hidden folders and links that loop
  back are left out; an emptied search shows the folder as before.
- The 🔍 by Categories in Local files filters the list shown, as it does
  in Favorites and History - it did nothing there. The box over the list
  no longer keeps saying "Search radio-browser.info…" after leaving
  Radio's Search.
- On a series' page, the poster under the video read "play" while one of
  its episodes played, and a click on it started the episode over
  instead of pausing it. It now pauses and resumes it, as for a channel.
- GNOME (Wayland): the pointer no longer spins for some 25 seconds after
  Settings opens. Each "This profile's" mark, Profiles' "In use" and a
  source's page's tabs were shown for a moment as windows of their own,
  each asking GNOME for an activation token it never used, and GNOME waited
  for them to time out.
- Settings opens about four times as fast (about 85 ms instead of 375 ms
  with Nerd on and five sources; on one of Nerd's pages about 205 ms) and
  the video beside it stands still far less: only the page it opens on is
  built, every other the first time it is shown or a search reaches it.
  What each page stores, and when, is unchanged; a page never opened
  stores nothing.
- Settings > Appearance: switching on "Follow the system" shows the light
  theme row at once; it stayed hidden until Settings was opened again.
- A click on the video's right-click menu where it hangs over a list or
  the guide no longer goes through to what is under it: the item did its
  work, then that row's own menu (Record…) opened on top, mostly on
  Wayland with Qt 6.9 and later. The same for the pop-out's, a multiview
  cell's and the guide's own right-click menus.

## [1.6.0]

Profiles for the household, TV mode with the phone as its remote, a page
for every series, Jellyfin, Emby and Plex, SAT>IP and four more home
backends as sources, the Enigma2 extras and its channel lists, server
mode, casting to Kodi and an Apple TV, teletext, the command palette,
radio and web streams, Watch together, and sync for everyone.

### Added

- Profiles, one per person in the household (Settings > Profiles, up to
  eight): favorites of every kind, history and resume points, Watch
  later, watched marks, followed series, reminders, statistics, Trakt and
  Simkl, Home's sections, theme and accent, audio and subtitle languages
  are a profile's own (declared in `core/profiles.py`); sources,
  recordings and their rules, the guide, devices, Nerd, playback and the
  parental lock are the household's. "Who's watching?" at start (and in
  TV mode), a four-digit PIN hashed as the parental PIN, kids profiles
  that never see a locked or adult category and open Settings only with
  a grown-up's PIN; switching stops what plays and needs no restart. The
  first start copies everything into the first profile and leaves the
  old keys for a downgrade. Sync and the full backup carry every profile
  (sync never a PIN); the phone remote, the remote API and Home Assistant
  show and switch it.
- TV mode (View menu, Ctrl+Shift+T, `--tv`, "Start in TV mode"): a
  10-foot page of its own - Home, Channels with now/next, the guide in
  large, Movies and Series in posters, Search with a keyboard on screen,
  Options (sound, subtitles, sleep timer, leave) - run with the arrows,
  OK, Back, Menu, the colour keys, channel up/down and digits from a
  remote that acts as a keyboard, an HDMI-CEC remote, a game controller
  (SDL2 through the optional pysdl2, else Linux evdev) or LIRC. The
  picture full screen with a big info bar, Now playing in a row above the
  tabs while a stream goes on behind the page, Back to the page and Stop
  to stop.
- Phone remote (View > Phone remote…, off until turned on): one page on
  the home network behind a long random token, its address as a QR code
  (`core/qr.py`) - the source's channels with search, favorites and
  logos, play, channel up/down, play/pause, stop, volume and mute, now
  and next. In TV mode a remote (d-pad or touchpad, colour keys, volume
  and channel rockers, number keys), the Now playing card back to the
  picture. On a phone's home screen as "dopeIPTV Remote" with the app's
  icon (web app manifest, apple-touch-icon, a maskable icon). New address
  shuts out every phone that had the old one; a client that keeps
  guessing is shut out for ten minutes, every client rate-limited. TV
  mode and its keys also from the remote API (`/api/tv`, `/api/tv_key`)
  and Home Assistant.
- Jellyfin and Emby (AuthenticateByName, Quick Connect, Live TV through
  PlaybackInfo, the file or the server's HLS, UDP 7359 discovery) and
  Plex (PIN sign-in in the browser, the best of a server's connections,
  direct play or the transcoder, Live TV from the server's DVR, GDM
  discovery) as sources: a token and never a password, the server's
  artwork, resume points and watched marks on the rows, playback reported
  back (start, every 10 s, pause, stop) and marks sent back, nothing in
  private viewing, the server's partly watched titles in Continue
  watching.
- VDR (restfulapi with streamdev-server, or streamdev's M3U alone),
  DVBViewer Media Server, NextPVR (v5+, PIN) and MythTV (Services API;
  its channels for the guide, no live TV over HTTP and said so) as source
  kinds, with groups, logos, guides, recordings as films and Record on
  the box; Find on my network asks their ports, every host's ports at
  once.
- Enigma2: the tuner signal (SNR, AGC, BER) in Stats for nerds and a box
  info window; a screenshot of the box's TV (`/grab`) with Save; its
  remote control; its recordings renamed, moved and deleted; a channel
  it cannot decrypt said so (diagnosis, stream health, failover,
  multiview, recordings); transcoding on port 8002 as a source's own Nerd
  option.
- Enigma2 tuners in the connections count: the box's tuners, running
  recordings and what its TV shows (`/web/about`, `/web/timerlist`,
  `/web/getcurrent`); a zap from any way in, a multiview cell, a cast or
  a recording over its own connection that would cut one off is asked
  about first (Go ahead anyway); a ring marks those channels; Nerd >
  Diagnostics shows what is in use. A box that does not answer never
  holds playback up.
- Enigma2 AutoTimer: the box's own rules beside the app's under
  Settings > Recording > Series recordings - new, edit, on/off, preview,
  Run now - each row saying who records it; "Record series…" offers a rule on the
  box; what the box records itself the series plan leaves to it.
- Kodi as a cast target: found by mDNS or added with its login, played
  over JSON-RPC with the source's user agent and headers (never a
  loopback address), pause, seek, volume and the strip's state.
- AirPlay to an Apple TV on macOS, the way Apple documents it: an
  AVPlayer with Apple's own AirPlay button in a small window of its own;
  HLS or MP4 the Apple TV decodes as it is, the rest repackaged by ffmpeg
  as fMP4 HLS on a LAN server under a token; the strip drives it.
- Casting: a channel, film or episode picked while a cast runs goes to
  the same receiver through the playback path's checks; the archive and
  the rewind buffer reach the TV as one growing playlist
  (`providers/cast_feed.py`); going back during a live cast uses the
  rewind buffer while it holds the moment, else the archive; "Keep the
  rewind buffer while casting" (Settings > Casting, off - one more
  connection, asked first over a full or unknown limit); "Add a device by
  address…" for a Chromecast, a DLNA TV or a Kodi on another part of the
  network, kept and listed after a restart.
- DVB subtitles and teletext subtitle pages in the subtitle menu by
  language and page, the page kept per channel and picked by the
  preferred language; a teletext viewer (View menu, the player's menu,
  T) decoded in plain Python from dopeIPTV's own rewind buffer, never a
  second connection; "Keep teletext and subtitles" leaves such a
  recording a .ts when saving as MP4.
- Command palette (Ctrl+K / Cmd+K): the menu's and the rebindable
  actions, every Settings row, the source's channels and the guide's
  programmes from one field.
- Internet radio from radio-browser.info (Radio, under Local files):
  directory lists, search, countries, languages, genres, favourite
  stations; a click reported only when turned on.
- YouTube Live, Twitch and similar sites through yt-dlp (Open address…,
  Ctrl+L, `--play`, `dopeiptv://`): mpv's ytdl hook or the address
  resolved by the app; the system's yt-dlp or the official release
  downloaded into the user's data folder, checked against SHA2-256SUMS
  and updated at most once a day while used - never in our packages.
- Watch together: a room's 8-character code, each person on their own
  source (TMDB id, else title and year), kept in step within about a
  second; messages sealed with a key from the code (PBKDF2, HKDF,
  ChaCha20-Poly1305), passed on by dopeiptv.org/together/; never during
  private viewing.
- Night mode for the sound (ffmpeg's dynaudnorm, one labelled filter; N,
  the player's menu, Settings > Playback).
- Recordings as MP4 (Settings > Recording, off by default): remuxed off
  the window's thread, the .ts deleted only once the MP4 is plausibly
  whole; a remux cut short is done at the next start.
- Series rules that remind instead of record, and "Remind me of every
  airing…" in the guide.
- Your statistics (History > Your statistics…): time watched by week,
  month, year, weekday and hour, top channels, movies and series, the
  longest streak; this computer only, never synced.
- Export to calendar… (File, the Reminders window): reminders and
  scheduled recordings as RFC 5545 iCalendar, and a subscription address
  from the remote API or the phone remote.
- Simkl beside Trakt (AUTH V2 device sign-in, the access token renewed,
  sign-out revokes), and Discord status (Settings > Integrations, off by
  default; nothing during private viewing).
- The system's media controls on macOS (Now Playing) and Windows (the
  System Media Transport Controls), as MPRIS on Linux.
- Stream information (Settings > Playback, off by default): resolution,
  frame rate and codec on a channel change and in the channel list, from
  what the player saw (#24, @lmerega).
- Connections to the account (for everyone): "1 of 2 connections in use"
  in the account details; the app's own counted (player, rewind buffers,
  recordings over their own connection, multiview cells, casts), and a
  plain warning with Go ahead anyway before one more would go over.
- Nerd: backup addresses per source (the next tried on no answer, TLS or
  5xx, never on 401/403/404); Test connection (DNS, TCP, TLS, the login,
  a live channel's first byte and speed); own DNS over HTTPS (Cloudflare,
  Quad9, Google or custom); low latency for live; an IP version per
  source; a trusted certificate per source (pinned by fingerprint, a
  question before casting it); stream analysis, a simple TR 101 290 read
  from the rewind buffer; Home Assistant's commands over MQTT (buttons,
  volume, favourites, play by name or number, TV mode); Other networks
  (VLANs) searched by unicast.
- A page for every series: opening a series - from its list, Favorites,
  Watch later, Home's shelves, Continue watching, New episodes, the
  command palette (series are found there now) or an actor's titles -
  shows its own page where the list is, as on Plex or Jellyfin: the
  backdrop (or poster), title, year, rating, genres and plot, one button
  that plays on (Continue S2 E5 with its progress, or Play S1 E1),
  Favorite, Follow and Watch later, the seasons in one row of chips
  (Specials last; the one picked kept per series, a profile's own) and
  the season's episodes as cards with their still, length, plot, watched
  check and progress, the next one marked. Back (the arrow, Esc, the
  mouse's back button) shows the list exactly as it was. Episodes from
  the source's own series info (Xtream, Stalker, Jellyfin/Emby/Plex) or a
  local library, off the window's thread; TMDB fills a missing backdrop
  and stills. TV mode's series screen gets season tabs the same way.
- SAT>IP servers as a source kind (Fritz!Box Cable, Octopus NET,
  Digibit, Kathrein EXIP, minisatip, Tvheadend's SAT>IP server): found by
  SSDP with Find on my network (the other networks by unicast), the
  tuners from `X_SATIPCAP`, the channels from the server's own list, a
  Fritz!Box's lists or an M3U given as a file or an address; stable ids
  from the tuning, RTSP over TCP by default (UDP, or HTTP where the
  server wants it), each stream counted as one of the server's tuners -
  a multiview cell, a recording or a cast that would need one more asks
  first. No guide from SAT>IP: the source's XMLTV address gives one.
- An Enigma2 box's channel lists edited from the app (the box's menu ->
  "Edit the box's channel lists…", through OpenWebif's bouquet editor):
  TV and radio bouquets made, renamed, moved and removed, channels
  dragged into order, renamed and removed, markers and spacers, channels
  added from all, a satellite's or a provider's, with a search. Each
  change goes to the box in the order made, off the window's thread; the
  box takes a backup of its lists first, offered to save, and Undo puts
  it back; the source reads its channels again when the window closes.
  Never for a kids profile, never touching what plays.
- Server mode (Nerd > Remote and automation, off by default, or
  `dopeiptv --server` without a window, for a NAS or a Raspberry Pi):
  dopeIPTV as the home's server - a playlist and the guide for VLC, Kodi
  and a TV's IPTV app, and an HDHomeRun tuner Plex, Jellyfin and Emby
  find by themselves (`discover.json`, `lineup.json`, UDP 65001), the
  guide under the tuner's channel numbers. Each channel is fetched once
  into a rewind buffer session and shared by everyone watching it, the
  app's own player included; never more channels at once than the
  account allows, the app's own viewing and recordings first, and a
  device that would need one more is told all tuners are in use. The
  guides it hands out are kept fresh: each served source's downloaded
  again once it is six hours old, with or without the window. Home
  addresses only, a token on all but the tuner's endpoints; a page in the
  browser shows who watches what and holds its settings (port, tuners,
  which sources and categories - never a hidden, locked or adult one).
- A loading indicator over the picture (issue #27): a stream that takes
  a moment shows a spinner, the channel's logo or the title's poster when
  the app holds it already, its name and "Connecting…" - "Still loading"
  after 8 s, "Reconnecting…" for the app's reconnect - from a third of a
  second after it was asked for until mpv's first picture
  (`playback-restart`), and "Buffering… 45%" when playback waits a second
  or more for its cache, a seek or the rewind buffer. Gone at once on a
  stop, an error and the next stream; docked, full screen, TV mode, the
  pop-out (over its mirror) and, as a small spinner, each multiview cell.
  It follows mpv's events on its own thread and only shows: no command to
  mpv, no connection, no download, clicks go through it
  (`media/loading_overlay.py`).
- Official builds: each package carries a signed manifest of its own
  files (`tools/build_manifest.py`, release.yml); a modified build leaves
  dopeiptv.org's services alone (Watch together, sync, the update check
  and self-update, reports - offered to copy instead) and About says so;
  requests to dopeiptv.org carry `X-dopeIPTV-Build`.
- One search across every source (issue #29): a Search page in the
  sidebar's places, on Ctrl+Shift+F (a key of your own for the old guide
  search carries over), the guide's Search button and the command
  palette's "Search everywhere for …". It finds channels, the guide's
  programmes from now to a week ahead, movies and series in all your
  sources, in four groups with a few rows each and Show all, every row
  naming its source; a programme on now has a Live mark and a progress
  bar. A hit plays in the docked player and the results stay; Back from
  a series' page comes to them as they were, Esc empties the field and
  then goes back. Another source's channel plays without switching to
  it (the status line says from where), its reminders fire whichever
  source is in use and a recording asks it for the address as it
  starts; its movies and series switch to it first. Each source's lists
  are read once a session off the window's thread - a portal's only as
  far as they were opened, said on the page - and its guide only from
  what this computer has; hidden, locked and, for a kids profile, adult
  categories are never searched, and nothing is kept past leaving the
  page while viewing privately. It replaces the Search the guide window.

### Changed

- Settings: the sidebar in groups - Watch, Content, Accounts and
  integrations, System, and Nerd in its colour - every setting in one
  place (`docs/SETTINGS_MAP.md`). Casting, Watch history (Trakt and Simkl
  together), Sync, Integrations, Keyboard shortcuts and Profiles are
  pages of their own; Nerd's pages are five beside Status, with one link
  row on each everyday page they belong beside.
- A page of its own for each source in Settings (Connection, Network,
  Streams, Guide, Advanced), each row marked own or shared and where;
  adding keeps the quick form, then opens the new source's page.
- The active source's name over the categories, with its kind in a chip
  when there are two sources or more, a click opening the source menu;
  the status line says the list's source, and what plays from another
  source says so.
- Settings opens in about half the time, and the video beside it
  stutters less: the app-wide event filters let what they do not handle
  through at once, hidden pages are not laid out, cache sizes are counted
  off the window's thread and Nerd's checks run after the window shows.
- Sync between computers is for everyone, not only supporters: no
  supporter key is needed or sent. The Sync page says in plain words what
  dopeiptv.org gets - bytes encrypted on the computer with a key from the
  sync code, under a random number, nothing that identifies anyone - and
  that a copy unused for 180 days is deleted. dopeiptv.org now limits
  writes per address and the space all copies take (a new copy waits while
  it is full; HTTP 507 reads "no room right now", 401/403 "turned down").
  The supporter's icon and two themes stay theirs.
- The app asks for a donation in a calmer rhythm: the heart on the logo
  shows one week a month, once the app is used for real (five hours watched
  in the last 30 days, two weeks after the first run) - in its colour the
  first day, muted after, never moving - and goes by itself after the week.
  Home's card takes its place on the first day while Home shows, never both.
  "Not right now" (and the right-click's Hide for three months) puts both
  away for three months, the new "I've donated" for a year; a supporter key
  is never asked. Nothing shows in full screen, TV mode, private viewing or
  a kids profile. Settings, General: "Ask for a donation now and then" turns
  it off altogether; Support dopeIPTV stays in the menu and in About.
- Settings' sidebar: "Sync" for Sync between computers, and shorter names
  where a page's name was cut off (Remote and automation in 17 languages,
  Greek's Boxes and lists, Russian's Parental lock).
- Every feature with the others: casts and multiview cells meet the VPN
  guard; a cast counts as one of the account's connections; a multiview
  cell plays with the source's Nerd options; a channel asked for by name
  is only one the lists show; the live preview ends what played before
  as a zap does; a playback that was private stays private to its end
  for every reporter.
- The remote API and the phone remote let in every private range and
  this computer's own address, never a public one.
- Open a video, folders and backups: the system's own file dialog on
  macOS and Windows; Qt's own stays on Linux.
- Web pages through yt-dlp play with mpv's own network defaults, not the
  ones tuned for IPTV.
- macOS: the player's core leaves out mpv's built-in scripts the app
  never uses.
- Windows: every server the app runs binds exclusively and without a
  reverse lookup of the computer's name.
- Requirements: PyObjC (macOS) and PyWinRT (Windows), with platform
  markers.

### Fixed

- "Cast to TV" was listed twice in the video's right-click menu and in
  the pop-out's.
- A channel previewed after music played with the visualiser in place of
  its picture.
- A reminder set from the guide lost its programme's end.
- With mpv 0.42 the highest quality for adaptive streams stayed on the
  smallest variant (variants shown as editions).
- macOS: libmpv could crash in its audio-device watch when a route came
  or went as a player closed.
- Windows: a cast could take seconds to start (the servers' reverse
  lookup); a server could share a port another program held; the rewind
  buffer could not delete a segment still open; `file:///C:/...` and a
  Windows path on `--play`.
- The guide's compact bar never switched with a large font.
- A table header's empty end was a light strip on dark themes.
- A Nerd check line could show as a window of its own for a moment and
  take the focus while Settings was built.
- A source's options all set back to their defaults kept the old ones.
- Text boxes of a source's Nerd options were light text on white.
- Open address with a web page in place of a stream asked for it twice
  a second through the rewind buffer: the buffer takes only a stream
  (a page, a playlist or a message ends at once), a few KB is a failed
  try, and a channel whose provider sent no stream is played without the
  buffer for a while.
- Test connection called a source with no live channel failed, and a
  media server not a server.
- Failover could pick a channel the other source's lists hide or lock.
- A source with a trusted certificate: another host the stream went to
  (a redirect, a segment elsewhere) is checked against the system's
  authorities.
- The stream played after a web page kept that page's options.
- Trakt and Simkl: a sign-in renewed on a worker was written to the
  settings from that thread.
- Sync: Start and Join one over the other cut each other short.

## [1.5.0]

Boxes on the home network as sources, Nerd for those who tinker, series
recording, a full backup, keyboard shortcuts of your own, private viewing,
feedback with answers in the app, and a supporter key.

### Added

- Enigma2 receivers (OpenWebif and the Dreambox WebInterface), Tvheadend
  servers and HDHomeRun tuners (and their emulators) as source types, with
  their bouquets or tags, guides and recordings; "Find on my network"
  (HDHomeRun broadcast and a probe of the local /24).
- Nerd: a Settings page, off until chosen, whose pages fold out under it in
  a colour of their own - Network, Boxes and servers, Channels and guide,
  Player, Recording, Remote and sharing. Each setting is tried for real
  (✓ / ! / ✕, Test, Details): user agent, headers, proxy and IP version
  per source, a VPN guard, mpv options and shaders, the box from the app
  (zap, wake, standby, record on the box), picon folders, udpxy, guides
  from several places and a channel's guide by hand, presets (Fritz!Box,
  Channels DVR, Threadfin, xTeVe, Dispatcharr, ErsatzTV).
- Failover: a live channel that will not play is played from another
  source (by guide id, else name).
- Stream health per channel (starts, failures, time to the first picture,
  drops, the last error), in Settings, the channel's menu and a dot.
- Channel numbers of one's own; digits typed over the picture zap too, a
  number not on screen is found in the whole source, `POST /api/play
  {"number": 12}`.
- A source's own catch-up address: Kodi's placeholders and modes (default,
  append, shift, flussonic), tried before the app's own guesses.
- A remote API and sharing: one small server with a token - play, the
  channel list, what is playing - and every source's channels as one M3U
  and their guides as one XMLTV.
- `--play`, `--channel` and `dopeiptv://` links; a single instance.
- Recording file names from a template (folders, never outside), and a
  command after a recording (no shell).
- Private viewing (nothing kept until the app closes) and Clear recent
  history by time range.
- Send feedback…: topic, where and how often for a problem, attachments
  one by one, only the part of the log that matters, "Show exactly what is
  sent"; an answer in the app through a random code kept on the computer
  (only its hash is sent). Error and crash reports offered, scrubbed of
  addresses, logins, sources, channels and titles. Limits against
  spamming in the app and on the site.
- Supporter keys: signed by dopeiptv.org, checked offline, nothing about
  the person in them; "Supporter key…" in the dopeIPTV menu and Settings,
  `dopeiptv://supporter?key=...` from the site's thank-you. A supporter's
  logo shades into the heart's colour with a heart on its middle bar, the
  app's icon can do the same, two themes are theirs (Aurora, Dawn), and
  their feedback is marked to be read first (a word, never the key).
- Sync between computers, for supporters (Settings, General): favorites
  with their folders, settings, and on request playlists and watch
  history, the same on every computer. End-to-end encrypted with a code
  only the person's computers know (ChaCha20-Poly1305 and HKDF in plain
  Python); dopeiptv.org keeps ciphertext under a slot it cannot tie to
  anyone and only checks that the supporter key is one. Last writer wins
  per entry, a removal travels; a change syncs half a minute later, never
  in a stream's first seconds.
- Series recording: "Record series…" on a programme - the exact title or
  a word, one channel or any - scheduled from the guide for the week
  ahead, nothing twice, a cancelled one not again; padding before and
  after every recording from the guide.
- Back up and restore everything in one file - settings, sources,
  favourites, history, recordings planned, Nerd - shown before it is
  restored; or only its playlists, as before.
- Keyboard shortcuts of your own, in Settings > Advanced: click a key and
  press the new one; a key moved from another action says so, keys the
  app needs are kept.
- Catch-up on Stalker / Ministra portals, from the portal's own archive,
  by programme.
- Nerd: a channel's own settings - audio and subtitle language,
  deinterlacing, aspect ratio, audio delay.
- Nerd: rewrite rules for a source's stream addresses (find -> replace,
  plain or regex, previewed and tested), and a highest quality for
  adaptive streams (1080p, 720p, 480p, lowest, or a bitrate).
- The sidebar in places and MINE: Home, TV, Guide, Movies, Series,
  Recordings and Local files at the top; Favorites, Watch later and
  History (with a Watched filter) below; a quiet status - a red dot while
  recording, new episodes, the watch-later count; the source as an avatar
  beside Settings at the foot. Multiview lives in the View menu and on a
  channel's right-click.
- The guide opens as a page in the middle, beside the player; Pop out
  gives it its own window as before. Its hours fit the room it has, or
  − / + (Ctrl + wheel, Ctrl + = / - / 0) pick how many.
- Answers to feedback in a window of their own (Help > Answers to
  feedback, Settings > General), asked for soon after a report; the menus
  grouped as each system has them (File, View, History, Help on a Mac).
- Nerd: events out to webhooks and MQTT - playing, paused, stopped,
  channel changed, recordings - with Home Assistant discovery; private
  viewing sends what happened, not what.

### Changed

- Stats for nerds are laid out for the room the picture has: larger in
  fullscreen, two columns when one is too tall, seven lines on a click.
- Serbian in Latin script throughout.
- Flatpak: freedesktop runtime 25.08 (24.08 reached end of life).
- Typed channel numbers wait 1.5 s (was 1.2 s); Enter zaps at once.
- An error report never interrupts a video: while one plays, or in
  fullscreen, it waits in a note and in the menu.
- "Supporter since" reads as a month in the app's language.
- `dopeiptv://` links play streams only, never a file on the computer;
  the remote API checks the address too, and lets an idle client go.
- Linux: the single-instance socket lives in the user's own runtime
  folder.
- The donate heart no longer beats, and sits on the logo's corner.

### Fixed

- macOS: a second dopeIPTV menu appeared beside the real one.
- The source dialog with Nerd on was too tall to use and did not scroll;
  Check now moved the focus away.
- The guide could take the app down after it had been closed, with the
  older PyQt the Flatpak uses: its timers kept calling into the closed
  dialog.
- Feedback and error reports never left the Mac app (no certificates for
  the secure connection); album art in Local files had the same problem.
  A long report with a log is no longer too big for the site.
- Stats for nerds disappeared in fullscreen on macOS, in the pop-out, and
  after leaving either.
- Turning Nerd on unfolds its pages and scrolls them into view.
- Failover could take over after the user had chosen another channel or
  pressed Stop.
- The VPN guard now holds auto-preview, external players, multiview and
  recordings too, and never the user's own recordings; on Windows it no
  longer flashes a console window.
- The rewind buffer sends a stream's login up front again (some servers
  refused it otherwise) and follows "Streams through the proxy too".
- Windows: the command after a recording broke on quoted arguments.
- Very long programme titles no longer make a recording's file name too
  long.
- Private viewing keeps titles out of the log too.
- The icon rail lost its logo.

## [1.4.5]

An installer for Windows, and updates that install themselves on Windows
and macOS, fetched from dopeiptv.org.

### Added

- Windows: an installer beside the portable zip - per user, no
  administrator, a Start menu entry and an entry under Settings -> Apps.
  A copy it installed updates itself: About and the update banner offer
  Update now, which downloads the next installer, checks it, closes the
  app, installs silently and opens the new version.
- macOS: the app updates itself. Update now downloads the next .dmg,
  takes the app in it only when its signature holds, it is Solecraft
  AB's and Apple has notarised it, puts it beside this one and swaps the
  two when dopeIPTV restarts (or quits). A copy run from the .dmg, or one
  macOS moved aside because it was opened from Downloads, is sent to the
  download page instead.
- One update window, in the app's own style, from the download to the
  restart: what is happening, a bar with the megabytes, then Restart now
  (Install now on Windows) or Later - or why nothing was installed.
- The welcome screen connects a Stalker / Ministra portal too (the
  portal's address and a MAC address), beside Xtream and M3U.

### Changed

- Updates come from dopeiptv.org: the app asks the site for the newest
  version and fetches it from there, as `dopeIPTV/<version>`, so the site
  can count updates made from the app (how many, by version and system,
  never who). GitHub answers when the site does not. Test builds are
  asked of the site too and fetched through it, though their files stay
  on GitHub.
- macOS: the .dmg files are signed with Solecraft AB's Developer ID and
  notarised by Apple, so the app opens like any other - no more Open
  Anyway under Privacy & Security. 1.4.4's own .dmg files were rebuilt
  this way on 13 November 2026 (the app inside is unchanged); test builds
  are signed but not notarised.

- Updates are signed: release.yml and nightly.yml sign each release's
  SHA256SUMS with dopeIPTV's own Ed25519 key (a GitHub Actions secret), and
  the app installs an update only when the file is on a list signed so -
  sent by the site with the release, else fetched from GitHub. Nothing is
  fetched over plain http, and nothing without a checksum.

### Fixed

- Home without a provider: its top bar was squeezed to a sliver - the
  back button cut off and no way out of Home.

## [1.4.4]

Playback that rides out a dropping connection, and a warning when
another device on the account is taking it.

### Fixed

- Linux (GNOME): "Support dopeIPTV…" chosen from the app menu, or from the
  heart's right-click menu, opens the page in front of the app like every
  other link. Qt asks for the activation token for the focused window, and
  from a menu that was the menu itself or, as it closed, none; the link now
  waits a moment for the window to have the focus back.
- The TV guide in Arabic, Persian, Hebrew and Urdu opened hours away from
  now, on an empty stretch of the board: Qt inverts a view's horizontal
  scrolling in a right-to-left layout, and the guide scrolls in
  left-to-right scene coordinates. The board itself (channels on the left,
  time running right) stays left to right in every language.
- Rewind buffer (dopeIPTV's own): a provider connection that kept dropping
  - typically another device or app on the same account taking the one
  connection the provider allows - made playback stutter, jump back a few
  seconds at every drop, and at last open the same short piece over and
  over. A reconnect now goes on where the stream left off when the provider
  repeats the last seconds (they are skipped, the player never sees the
  drop), reconnects at once, and an ended piece moves playback on to the
  next one instead of back to itself. The provider's small pieces go into
  the buffer in blocks, so the reading keeps up.

### Changed

- The website has its own address, dopeiptv.org; iptv.dope.rs sends every
  request on there. The app's links (About, the update banner, donating),
  the Trakt sign-in's return address and the package metadata (software
  centres, the Flatpak, the .deb, Python's project page) point to it, with
  the help and donating beside the homepage; GitHub stays as the source
  code and issue tracker.

### Added

- When a live channel keeps losing its connection, the app says why: the
  account's figures if the provider gives them ("all 1/1 connections are
  in use - close the stream on your other device or app"), else that
  another device or app on the account is the likely cause. With the
  rewind buffer on or off: without it, three drops within a minute of a
  stream that was playing tell it. Shown once per channel; reconnecting
  works as before.

## [1.4.3]

Fixes after 1.4.2: no spinning pointer on Home, the pop-out's bars above
its controls, the window back in its dock icon after an update, and a
tidier About.

### Fixed

- GNOME (Wayland): the pointer no longer spins for a while each time Home
  opens, or when Home is switched off in Settings. Home's donate heart was
  shown for a moment as a window of its own, which asked GNOME for an
  activation token it never used.
- Pop-out player: the DVR row and the timeshift timeline no longer lie over
  the control bar when it comes back after fading; they move up with the
  video as it shrinks.
- Home: the back and heart buttons at the top are round, filled circles; the
  edge was drawn in four arcs with gaps between them.
- Linux: after an AppImage update restarts the app, its window belongs to
  the dock or dash icon it was started from, not a second icon beside it.
  The window takes the name of the menu entry that launched it (Gear
  Lever's, AppImageLauncher's) and keeps it across the restart; the entries
  dopeIPTV ships also say `StartupWMClass=dopeiptv`.

### Changed

- About: "Made by" and the Solecraft logotype beside **Close**, a quiet
  link to solecraft.se.
- About: the release notes show only what is new to you - this version's
  own notes when you are up to date, the versions since yours when an
  update is waiting - not every version back to 1.2.15.

## [1.4.2]

Settings rebuilt and applied live, the guide with every channel, links in
front on Linux, and donating from the app.

### Added

- Donating from the app, never in the way: a small red heart beside the
  sidebar's logo (under it on the icon rail) and at the top right of Home,
  its words in the tooltip; "Support dopeIPTV…" in the app menu (in the one
  app menu on macOS) and in About; one card on Home after about ten hours
  of watching, with Donate (away for a year), Not now (three months) and
  I've already donated (for good), which also goes by itself after a week.
  The heart's right-click menu hides it for three months or for good, and
  Settings → General → Support dopeIPTV turns it back on. Links carry
  `?ref=app-heart|app-menu|app-home|app-about` for the site's statistics.
- Settings search across every page; a Picture & sound page; Home's
  shelves as chips; steppers for numbers, durations and sizes.

### Changed

- Settings apply as they change (debounced, and once more on Close) and
  write only what differs; one Close button instead of OK / Cancel.
  Sections are borderless surfaces with hairlines between rows, controls
  filled, not outlined.
- The app icon's triangle is centred by its centroid (38-74% of the tile
  instead of 40-76%); the committed Flatpak and Windows icons are drawn
  again from `make_app_icon`.
- Multiview's button toggles its window.
- On a Linux desktop session links go through Qt's opener first, which
  passes the compositor's activation token, so the browser is raised; in a
  frozen bundle the process carries the system's library paths for the
  moment of the launch. The cleaned xdg-open path stays as the fallback.

### Fixed

- The guide showed only the first 300 channels of "All".
- Category flags for countries named in words, not only flag emoji or
  "SE |" prefixes.
- The rewind buffer logged a traceback when mpv hung up before answering.

## [1.4.1]

Subtitles on at once in any language, themes with a colour of their own,
and readable text on light accents.

### Added

- Eight themes that differ by colour rather than by shade: Ocean, Forest,
  Bordeaux, Synthwave, Amber terminal and Espresso, and two light ones,
  Sand and Sky - 25 in all. Each has an accent of its own (Settings →
  Appearance → the theme's own colour) deep enough to carry the white text
  on buttons, and every one passes the readability test. Names in all 27
  languages.

### Fixed

- Turning subtitles on in a film was still slow when the language wanted
  was not the file's first subtitle track - with the default "Automatic",
  the usual case. The track a film opens with, hidden, is now chosen by the
  languages asked for in Settings, then the language turned on last, then
  the app's own language, so it is usually the one that gets turned on:
  0.10 s instead of a frozen picture for 15 s and more against a slow
  server (two-track MKV, English first, app in Swedish). A track the file
  flags default or forced still shows at the start in "Automatic".
- Text on the accent colour (buttons, the checked nav entry, selections,
  ticks) is near-black where white does not read - Nord, Dracula,
  Catppuccin, Rosé Pine, One Dark, Tokyo Night, Gruvbox, Kanagawa,
  Everforest and the light accents.

### Docs

- README, FEATURES, ARCHITECTURE, DEVELOPER_NOTES, SECURITY, CONTRIBUTING,
  the issue and PR templates and the AppStream description match 1.4.0.
- A donate link: `.github/FUNDING.yml` (the repository's Sponsor button)
  and a "Support dopeIPTV" section in the README, which says what a
  donation pays for: development, the website and the downloads.
- `docs/MAINTAINING.md`: the runbook for whoever takes over. `AGENTS.md`
  is for every AI assistant; `CLAUDE.md`, `GEMINI.md` and
  `.github/copilot-instructions.md` point to it.

## [1.4.0]

Rewind with dopeIPTV's own buffer - downloading on through a pause, back to
live without a reconnect - and a programme guide that keeps itself up to
date.

### Fixed

- **The programme guide no longer stays on yesterday.** The guide was
  loaded at start and then only by a playlist refresh: an app left open
  overnight - or a Mac that slept - showed yesterday's programmes, "The
  server's schedule times look wrong", and an empty guide. A guide counted
  as fresh for six hours after its download, too, also one that a
  provider sends a day at a time and that had run out. The guide is now
  looked at every 20 minutes and whenever the app comes back to the front,
  and fetched again when it is older than six hours or runs out within
  three (at most one try an hour, for guides that always end at midnight).
  The detail panel fills in from the new guide at once, and where the
  provider's guide simply ends before now it says so, instead of "The
  server's schedule times look wrong".

- **No "TIMESHIFT" badge for a pause in the rewind buffer.** Pausing a
  catch-up channel that has the DVR row stamped "⧗ TIMESHIFT" on the
  picture, as if the provider's archive were playing; the pause is the
  buffer's. The badge is now kept for the archive, and the timeshift
  timeline takes how far behind live it is from the buffer.

### Added

- **A new Home.** A featured strip at the top - this week's most popular
  titles the provider has, else what it added last - with the artwork
  big over a soft copy of itself, title, year, rating, Play and More info,
  turning every few seconds (held still under the pointer; Settings ->
  Home -> Featured at the top turns it off). Continue watching is a row of
  wide cards with the time left; a favourite channel's tile says LIVE,
  what is on and how far it has got, on a panel in the logo's colour; the
  popular rows carry their chart position big beside the poster; a
  movie's year and rating sit under its title. Rows page with arrows by
  their title instead of a scrollbar, and the rows of a section have "See
  all". The pictures stay one opaque pixmap per card, so scrolling a full
  page stays as smooth as before (ui/home_widgets.py).
- **Rewind with dopeIPTV's own buffer (experimental).** Settings ->
  Playback -> Rewind with dopeIPTV's own buffer: the app reads a live
  channel from the provider itself and keeps it on disk - in segments of a
  couple of seconds, as far back as the rewind setting says, within a space
  cap (1-20 GB, 2 by default) and never below 1 GB free - and mpv plays it
  from a local server (loopback, a token per session, no provider address
  in it). The download goes on while paused, so LIVE is a jump, never a
  reconnect, and nothing kept is lost; seeks inside what mpv holds are
  mpv's own, further back the local stream opens at another segment. A
  dropped provider connection is opened again by itself, and the gap is
  skipped, never played across; when it gives up, the app's reconnect
  takes over as before. One provider connection, as before - switching
  channels closes it before the next opens. Off by default, and with it
  off live channels play exactly as before; ts only, m3u8 plays as
  before. Measured: the window's event loop is no slower with it on.
  Designed with Luca Merega in the issue that followed pull request #22.
- **Each channel keeps its history (own buffer).** A channel you leave
  keeps what was kept of it - frozen, its provider connection closed at
  once - and coming back to it, the DVR row has that history again, with
  a tick where you were away (the pointer on the row shows the time, and
  "nothing kept here" in the gap, which is skipped, never played across).
  Opening the channel again after a reconnect keeps it too. One space cap
  for all of it: history older than the rewind setting goes, and over the
  cap - or with the disk running low - the channels left longest ago give
  way first, then the one watched; at most eight channels are kept. Only
  for this session: all of it is wiped when the app closes. Settings shows
  where it is and how much it takes, with a button to open the folder.
- **Record from the buffer.** With the own buffer, "Record" in the player
  records from it: no connection of its own, whole through a pause or a
  rewind (the buffer goes on downloading), and from what is on screen -
  rewound, that is the past. Changing channel while it records offers to
  let it go on in the background over a second connection, where the
  account allows more than one (an account that says it allows one is
  only offered to stop); coming back to that channel takes no other
  connection. Across a reconnect gap it goes on with what follows. The
  file begins on a key frame of the video, a few seconds before what was
  on screen, with the stream's tables first: one that began between two
  key frames showed nothing - black or grey in many players - until the
  next one. The question on changing channel now says why: the recording
  keeps its channel downloading, and that is the connection.
- **No popular rows without a TMDB key, now in the log.** The popular rows
  and the featured strip come from TMDB's chart of the week, fetched with
  the TMDB key; a build from source has no built-in one, so after a week
  the rows went away without a word. The log now says so once
  (set DOPEIPTV_TMDB_KEY, or a key of your own in Settings).
- **Turning subtitles on no longer freezes a film.** mpv answers a
  subtitle track chosen during playback with a refresh seek: it drops what
  it has buffered and asks the provider for the stream again from a little
  earlier - measured against a throttled server, 5 to 7 s of frozen
  picture each time subtitles went on, and again after each Off. A film or
  an episode now opens with a track chosen even when none is to show
  (the subtitle languages asked for first), hidden; On shows it and Off
  hides it - 0.1 s - and the track is only changed when another language
  is asked for, which still takes the provider's time. Whether a track
  shows at the start is decided as before (flagged default or forced, or
  in a language asked for), once the tracks are known. A Settings save no
  longer chooses a track mid-film either. A live channel cannot be sought,
  so it never froze, and opens as before.
- **A pause that stops a recording says so.** Without the own buffer, a
  recording in the player is mpv's own, and mpv stops reading the channel
  shortly into a pause - so the recording stood still without a word. The
  first pause of such a recording (or a recording started while paused)
  now says it, and names the setting that records through a pause. A
  recording from the buffer says nothing: it goes on.
- **Refresh the guide from the guide.** A ↻ button in the programme guide's
  window, and "Refresh guide now" in its right-click menu - on a channel
  without programmes and on an empty spot too - fetch the guide again and
  redraw the board when it is in, where it was scrolled to. (Refresh
  playlist fetches the guide again as well, as it did.) "Refresh guide
  now" is also in the right-click menus of a channel, a category in TV
  and TV in the sidebar.
- **tools/epg_check.py** tells the provider's guide from the app's copy
  of it: what the provider sends now - channels, programmes, how many
  have something on, where it ends - beside the copy on this computer,
  with a verdict. It uses the account the app is logged in with and
  prints nothing of it.
- **Stalker / Ministra portals.** A playlist can now be a MAC portal:
  Settings -> Playlists -> Add, "Stalker / Ministra portal", with the
  portal's address and the box's MAC address (written as portals expect
  it, 00:1A:79:12:34:56, from however it was typed). providers/stalker.py
  speaks the portal's API behind the Xtream client's interface: the API
  is found beside the address given (portal.php or server/load.php, with
  or without /stalker_portal), every call looks like a MAG250, an expired
  token is renewed once, and a box the portal does not know or has
  blocked gets the wrong-login message. Channels, films and series - in
  all three layouts portals use for series - and the guide's now and next
  from the portal (it has no XMLTV; a playlist's own guide address still
  works). A channel's or a film's address comes from create_link, fetched
  off the window's thread before it plays (the window never waits on the
  portal), within 8 s; a reconnect asks for a new one, and one the portal
  did not give is not asked for again for 5 s. Exported and imported with its
  MAC. Catch-up from a portal is not supported yet.
- **Test builds (nightly).** Settings -> General -> Update channel ->
  Test builds: the app also offers the test builds of the `testing`
  branch, built with every change by `.github/workflows/nightly.yml` - the
  release workflow called with a version of `<release>.<build>`
  (1.4.0.123: newer than 1.4.0, older than 1.4.1) - and published as GitHub
  pre-releases tagged `nightly-1.4.0.123` (the newest seven are kept).
  Pre-releases are never the latest release, so the Releases channel and
  the website never see them. The AppImage updates itself in place on
  either channel; it is checked every six hours on Test builds.
  A package that did not build for a test build (often a download that
  timed out) is left out and named in its notes, and the run stays green:
  no failure mail for a hiccup. A release still fails loud.

- **The Intel (x86_64) macOS `.dmg` is back (experimental).** Gone since
  1.2.11, because Homebrew stopped making packages for Intel Macs. It is
  now built on the Intel runner with ffmpeg and the libraries mpv needs
  from conda-forge, which still builds them; libmpv itself is built from
  mpv's source against those (conda-forge's own libmpv leaves two of
  mpv's macOS helpers to the mpv program and cannot be loaded by an app).
  packaging/macos_bundle_dylibs.py makes the bundle self-contained. The
  release checks it as it does the Apple Silicon one - with the conda
  environment moved out of reach first, so a library the bundle still
  looked for there fails the build and not on a Mac. If the Intel leg
  fails, the rest of the release still goes out.

### Known issues

- **Flicker around the video in fullscreen on Linux with variable refresh
  rate (VRR).** With VRR on, the display follows the video's frame rate
  (25 or 50 a second) and flickers around the picture on some screens -
  the flicker on GNOME with NVIDIA that 1.2.18 looked into. Turning VRR
  off (GNOME: Settings -> Displays -> Variable Refresh Rate) stops it.

### Changed

- **One bar for rewinding on a catch-up channel.** Watched live with the
  rewind buffer, a channel with the provider's archive had two bars over
  the video, one above the other: the DVR row and the timeshift timeline.
  They now take turns in one place, with **DVR | TIMESHIFT** at the left
  of the bar to switch; the choice is kept for the next channel. A seek
  in the buffer (the arrow keys, -10 s) shows the DVR row, a step into the
  archive (Shift+arrow) the timeline. The timeline has the DVR row's
  panel, and no longer a darker box behind its slider. Fullscreen is as
  before: the timeline alone.

## [1.3.0]

Rewind a live channel, a new look - eight more themes, accents, corners,
the desktop's light and dark mode, the size of everything - subtitles your
way, a docked video you can make taller and flags in the category list on
every system; and an app that keeps up with a big provider while its guide
and lists load.

### Added

- **Eight new themes, and a picker that shows them.** Tokyo Night, Rosé
  Pine, One Dark, Everforest, Kanagawa and High contrast, and two more
  light ones, Catppuccin (latte) and Solarized (light) - 17 in all.
  **Settings → Appearance** shows each as a small picture of the app in
  it, and the accent as a row of colours: three new ones (gold, indigo,
  mint), the theme's own (Nord's frost, Dracula's purple, Gruvbox's
  orange...) and any colour of your own. Every theme keeps its text at a
  contrast of 7:1 or more against its background (a test checks).
- **Follow the desktop's light and dark mode.** The theme you picked while
  the desktop is dark, a light one of your choice while it is light, and
  the switch as the desktop switches - where Qt can tell (KDE, GNOME,
  Windows, macOS); otherwise the picked theme stays.
- **Corners and size.** Sharp, rounded or extra rounded corners - on
  buttons, fields, menus and cards, and on what the app draws itself: the
  grid's tiles and channel logos, Home's cards, the guide's programmes (the
  window's own corners are the desktop's) - and the size of text and
  controls from 90 to 150 %: Qt scales the whole window, so everything
  keeps its proportions. The size applies at the next start;
  a QT_SCALE_FACTOR set in the environment wins.
- **How subtitles look.** Size (75-200 %), colour (white, yellow, light
  gray) and a dark box behind them, under **Settings → Audio & subtitles**
  and in the player's menu (right-click the video → Subtitles → How
  subtitles look), applied to what is playing at once; a subtitle track
  picked by hand stays picked. Subtitles with styling of their own
  keep their colours. The box works with mpv before 0.38 too.

The rest of this section is mostly a contribution by Luca Merega (pull
request #22).

- **Rewind a live channel (DVR).** A row over the bottom of the video shows
  how much of the channel is kept - "DVR −0:18 / 2:00" - with a slider to
  go back in it and a LIVE button to return. Arrow keys seek in it too, and
  a pause picks up where it stopped. Rewinding uses only what mpv actually
  holds and never seeks across a gap. The row floats over the video like a
  film's scrubber, so the mini player never changes height for it, and it
  shows only when the pointer moves over the video and fades after a few
  seconds. At live it says so - LIVE lit, and the time the buffer holds:
  mpv plays about 10 s behind the newest data (the network buffer), which
  is live, not rewound; the time back counts from there, and LIVE returns
  there with the buffer kept. **Settings → Playback → Rewind live
  channels** sets how far back: off, 1-10 minutes in memory (2 by
  default, about 60 MB a minute for an HD channel), or up to 6 hours with
  **Keep the rewind buffer on disk** - mpv's file only grows, 1-4 GB an
  hour while a channel plays, and is freed when the channel changes.
  Paused or rewound for longer than the network buffer, mpv stops reading
  and the channel runs on without it; the row then shows the real time
  behind live, and LIVE opens the channel again rather than landing at the
  end of what had arrived - except while recording, where it keeps the
  stream. LIVE says so first: it reads "↻ LIVE", and its tooltip that the
  rewind buffer starts over and that dragging the bar to its end stays in
  it. Catch-up from the provider's archive works as before.
- **Drag the height of the docked video.** A small handle under the video
  sets how tall it is, and the height is remembered. On Windows the
  detached player now resizes from its edges and corners, also with the
  title bar hidden.
- **Country flags in the category list on every system.** Windows shows a
  flag emoji as two letters; a category named with one ("🇸🇪 Sverige"), or
  with a country code before a separator ("SE | Sport", "DE: News"), now
  gets a small bundled flag (flag-icons, MIT). Codes that providers mostly
  use for something else - AR for Arabic, AF, LA, SA, TV - get none.

### Fixed

- **Lists, categories and the detail panel no longer wait for the
  programme guide.** Every channel list loaded while the guide was coming
  in started another guide load, which then sat on a background thread
  waiting for the first one's download and parse - as did the detail
  panel for every channel clicked. A few clicks took every background
  thread, and nothing else loaded until the guide was in: opening Movies
  took 21 s in a measurement, now 50 ms. One guide load runs at a time,
  and the detail panel fills in its programmes when the guide arrives.
- **"All" with tens of thousands of films, the search box and the switch
  between list and grid react at once.** Each of them asked every row
  whether it was hidden or renamed, also when nothing was (most lists):
  search went from 0.8 s to under 0.3 s, list/grid from 0.5 s to 0.2 s.
  A list without section headers is laid out from one row's height
  instead of asking each of its rows (80,000 films: 0.65 s to 0.1 s).
  The sidebar's category search builds its index off the GUI thread.
- **No more stalls every few seconds while browsing films and series.**
  The TMDB caches (posters, ids, cast) were kept in a settings file, and
  each save - two seconds after any batch of lookups - had Qt rewrite all
  of it on the GUI thread: 0.25 s with a full cache, and the playing video
  stuttered with it. They are files of their own now, written in the
  background; the existing cache moves over on the first start.
- **Video and the window keep up while the app loads in the background.**
  Measured against a big provider (20,000 channels, 80,000 films, 20,000
  series), a playlist refresh held the window - and a playing video - up
  for 6.3 s in all, up to 1.9 s at a time; now 0.2 s, never more than
  60 ms. Opening Home: 1.9 s, up to 0.6 s at a time; now 0.5 s. What did
  it:
  - Every call between Qt and Python waited out the 5 ms turn of any
    thread busy in Python; the turn is now 0.2 ms (1 ms on Windows).
    Home's cards took 9 s to build next to two busy threads; now 0.5 s.
  - The lists kept on disk for a provider that is down: every fetch,
    even of one category, wrote all of them again (tens of MB, several
    times at once), and every start and refresh read all of them back
    in, wanted or not. Each list now has a file of its own, the
    provider's reply as it came, read only when it is going to be used.
  - The provider's lists are parsed a row at a time as far as the
    window is concerned, instead of in one go that stopped it.
  - Home's newest films, series and channels, the guide search's and
    the guide's channel lists, and a cast member's other titles are
    picked out of the whole catalogue on a worker, not the GUI thread.
  - The guide download told the window its progress for every 64 KB -
    2,200 times for a big guide; now when the figure changes.
  - A full garbage collection walked every object in the app every five
    minutes, the whole catalogue included (~150 ms with a big one);
    what it leaves is now set aside until the hour comes round.

- **The app no longer crawls while the programme guide loads.** A big
  guide (hundreds of thousands of programmes) is parsed in a process of
  its own now: parsed in a thread, it held Python's interpreter for most
  of its 10-30 seconds, and the window had to share it - in a measurement
  with a 140 MB guide the window stalled for 1.4 s in all, up to half a
  second at a time, and a playing video stuttered with it; now about
  0.1 s in all, never more than 60 ms. That process also packs the new
  guide for the disk, the saved index is read a piece at a time, and a
  start with a guide on disk reads only its index instead of unpacking
  the whole guide too. A guide older than six hours comes from its saved
  index while the new one downloads, instead of being parsed again first
  (the start of every day); a playlist refresh keeps showing the old
  guide until the new one is in, instead of none for half a minute; and a
  guide already loaded answers at once while a refresh runs, instead of
  holding up the programme lists, logos and searches waiting behind it.
  Where the separate process cannot run, the guide is parsed in the app
  as before.
- **About dopeIPTV, with an update waiting:** on Linux (Wayland) the
  release notes lay over "Update now", "Check for updates", the links and
  the TMDB line, and the title read "About dopeIPTV dopeIPTV". The notes
  arrive after the dialog is up, and needed it to grow, which Wayland did
  not do; their room is now there from the start, so the dialog never
  changes size. When the app is up to date, the box shows what the newest
  version brought.
- **A quieter log on Wayland.** Qt's "QWaylandTextInputv3::disableSurface
  ... but 0x0 is focused" came on every focus change, dozens a minute, and
  buried what the log is for; it is left out like the other harmless
  Wayland lines.

## [1.2.18]

What is popular right now, on Home - and fixes for the AppImage and .deb
on a current Linux distro and for subtitles with the full stop in front.

### Added

- **Popular right now, on Home.** Two new shelves, "Popular movies right
  now" and "Popular series right now": this week's most popular titles on
  TMDB that your provider has, in the chart's order. A provider's API has
  no chart of its own, so TMDB's list of the week is fetched once a day,
  through the TMDB key the covers use, and matched against the provider's
  catalogue - by TMDB id where the provider gives one, else by title and
  year, in English or the app's language, with tags such as "EN - " or
  "|SE|" in front of a name ignored. A copy in a hidden or locked
  category gives way to a visible one. Each shelf can be switched off
  under Settings → Home, and a shelf switched on or off there now shows
  so at once, also while Home is open.

### Fixed

- **AppImage and .deb on a current distro (Arch, and others as they
  update):** every start printed a page of "Fontconfig error: ... invalid
  attribute 'xsi:nil'" and "did not find extension DRI_Mesa version 1".
  The package carried the build machine's older fontconfig, which cannot
  read a newer distro's font configuration, and its older libgbm, which
  the system's Mesa loaded and could not use. Both now come from the
  system, with freetype, harfbuzz and expat, as the AppImage project
  recommends; the .deb lists them as dependencies, and the release build
  starts the AppImage on Arch Linux and fails on either error.
- **Subtitles with the full stop in front (".Hej då").** A subtitle
  translated from an Arabic or Hebrew file can keep that language's
  right-to-left marks around every line, and mpv then laid a Swedish or
  English line out right to left. When a subtitle file is loaded - found
  on OpenSubtitles, picked from disk, or remembered for a title - those
  marks are taken out of every line without an Arabic or Hebrew letter;
  the file itself is left as it was.

## [1.2.17]

The series you follow get a place in Settings, and OpenSubtitles' daily
limit is explained.

### Added

- **Settings → Home → Followed series.** A switch for whether new
  episodes of a followed series are announced (the note in the app, and
  on Linux the desktop notification); the New episodes shelf and the
  dots on new episodes show them either way. Below it, the series this
  playlist follows, by name, with Stop following - no need to find a
  series again to stop following it.

### Changed

- **OpenSubtitles' daily limit is explained.** Settings → Audio &
  subtitles says how many subtitles a day OpenSubtitles allows: 5 per
  internet connection without an account, 20 with a free one. When the
  day's downloads are used up, the search says so in the app's language,
  with where a free account goes, instead of the site's English.

### Fixed

- **Settings → Local files:** the folder list stood at Qt's default
  height, so one folder floated above an empty well, under an empty band
  where the hidden "No folders yet" line still took its room. The list is
  now as tall as its folders (up to six, then it scrolls), each with a
  folder icon and its full path as a tooltip, and one is selected so
  Remove says what it would remove.

## [1.2.16]

A fix for subtitles from OpenSubtitles on macOS, Windows and the Flatpak.

### Fixed

- **Subtitles from OpenSubtitles** were downloaded and loaded, then
  reported as "OpenSubtitles did not send a usable subtitle file" on the
  macOS, Windows and Flatpak builds: their newer mpv (0.37 and later) calls
  the forced-subtitles option sub-forced-events-only, and setting the old
  name failed after the subtitle was already in. The same old name kept
  "On - forced subtitles only" from working there, and could leave a
  subtitle remembered for a title hidden. The player now sets whichever
  name its mpv has, and only the loading itself decides success.

## [1.2.15]

Casting to smart TVs, subtitles from OpenSubtitles, the desktop's media
controls, word when a followed series has a new episode - plus a redesigned
Settings window, the whole app in your own language, Trakt sign-in working
again, and an AppImage that updates itself.

### Added

- **Cast to DLNA / UPnP TVs.** The cast panel finds smart TVs (Samsung, LG,
  Sony, Philips, Panasonic), AV receivers and apps such as Kodi beside the
  Chromecasts, listed as "Name (DLNA)". The TV is handed the stream's own
  address and fetches it itself; a local file is served to it from here,
  with byte ranges so it can seek. Pause, seek, volume and mute work from
  the cast strip. Nothing to install - it works without pychromecast.
- **Subtitles from OpenSubtitles.com.** "Find subtitles online…" in the
  player's Subtitles menu searches for the film, episode or local file
  that is playing - by TMDB id, by the series with season and episode, or
  by the hash of a local file, falling back to the name - and loads the
  pick. A subtitle loaded for a title comes back with it next time. The
  API key goes in Settings → Audio & subtitles (release builds can ship one
  from a `DOPEIPTV_OPENSUBTITLES_KEY` secret); an account is optional and
  only raises the daily download limit. "Load subtitle file…" loads one
  from disk.
- **Desktop media controls (MPRIS, Linux).** GNOME's and KDE's media
  controls, the keyboard's media keys, headphone buttons and KDE Connect
  pause, play, skip and seek, and the desktop shows what is playing with
  its logo. The media keys also work inside the window on every system.
  A switch in Settings → General turns it off.
- **Follow a series.** "Follow series" in a series' right-click menu; the
  provider is asked every few hours what it lists, and new episodes land on
  Home's "New episodes" shelf, in a toast and - on Linux, with the window
  in the background - as a desktop notification. Opening the series selects
  the first new episode and marks the new ones with a dot.
- **Update in place (AppImage).** "Update now" in the new-version banner and
  in About downloads the new AppImage beside the running one, checks it
  against GitHub's SHA-256, and runs its `--self-check` before anything on
  disk changes; only then is it moved into place and our menu entry pointed
  at it, and the app offers to restart into it. The new version removes the
  old file once it has started. A file the user renamed is replaced under
  its own name. A failing self-check is accepted only where the running
  version fails it too (no 3D graphics): never worse than now. Other
  formats keep the "Download" button.
- **The menu entry follows a replaced AppImage.** If the file our menu entry
  launches is gone (an AppImage upgraded by hand), the running AppImage
  takes its place, so the dock icon keeps working.
- **Drop a playlist on the window.** An `.m3u`/`.m3u8` file or a provider
  link dropped anywhere on the window is added as a playlist, and the app
  offers to switch to it.
- **Reorder playlists** by dragging them in Settings → Playlists; the
  playlist menu follows the same order.

### Changed

- **A new Settings window.** A sidebar with fifteen pages and a search
  field replaces the row of tabs. Each page groups its settings in titled
  cards, with a short hint under the ones that need one, and every yes/no
  choice is a switch. Playlists show their server and an "In use" badge,
  Refresh appears only for the playlist in use, and "Reset all settings"
  moved to Advanced. Stored settings are unchanged.
- **Everything in your language.** About 200 texts that were still English
  are translated into all 27 languages: the recording dialogs and why a
  recording stopped, timeshift, list counts, empty lists, the detail panel,
  the player's badges, and the errors from the provider, Trakt,
  Chromecast, the updater and the video player. The log keeps them in
  English, so bug reports stay readable.
- Dates and weekdays follow the app's language instead of the system's
  English, and Qt's own buttons and dialogs (OK/Cancel/Yes/No, file
  dialogs) follow it too.
- Trakt signs in through auth.trakt.tv with PKCE, and a client secret is
  optional (Trakt apps created since October 2026 have none, dopeIPTV's
  own included). The browser sign-in of dopeIPTV's own app returns through
  https://iptv.dope.rs/trakt/callback, which hands it on to the app on
  127.0.0.1 - an app every Trakt account can use may only redirect to
  https://. The device-code sign-in opens Trakt's activation page itself,
  with the code filled in where Trakt offers that.
- "Cast to Chromecast…" is now "Cast to TV…", and the cast panel hides the
  audio, subtitle and picture-size choices for a TV (they belong to the
  Chromecast converter) and says so.

### Fixed

- **A crash under load.** Python's garbage collector ran on whichever
  thread happened to allocate - usually one fetching logos or the guide -
  and freeing a cycle that held Qt objects there could clear a timer's
  callback while the timer could still fire: the app segfaulted. The
  collector now runs on the GUI thread only, on a short timer, and the
  logo cache's prune timer no longer forms such a cycle.
- **Trakt sign-in** ended on Trakt's page with "invalid_client: client_id
  is required", and the device sign-in with HTTP 401: Trakt no longer
  knew the app dopeIPTV signed in through, so it has a new one. Trakt
  also moved its sign-in to a new host in 2026. A sign-in Trakt no longer
  accepts is dropped, so Settings offers Connect again instead of a
  "Connected" that silently fails.
- Trakt's device sign-in waits when Trakt asks it to slow down instead of
  giving up, disconnecting forgets the account's favorites list, and a
  failed watched-history sync no longer blocks every later one.
- Self-update: closing the progress window made every failure look like a
  cancel, so it was never shown.
- Favorites: renaming a folder onto another merged duplicate rows (and a
  later reorder crashed on them); a folder's colour now follows a rename
  and goes with a delete.
- The guide: a damaged cache or index is fetched or parsed again instead
  of failing the guide on every start, and both are written atomically.
- M3U playlists: å/ä/ö came out as "Ã¥" when a server named the wrong
  character set, a byte-order mark hid the header, and `file://` paths
  with spaces did not open. Export/import keeps M3U playlists, and
  removing the active playlist switches to the next one.
- Multiview keeps the screen awake, and Space, M and F act on the focused
  window while multiview is in front (Space restarted the channel behind
  it).
- A pop-out player saved on a monitor that is no longer there opens on
  this one.
- Windows: the detail pane's schedule list crashed.
- The Chromecast device picker and its note were in English in 25
  languages, and eight Serbian texts were in Cyrillic.
- Turkish headings in capitals read "DIL" instead of "DİL".
- About no longer says VLC is built in - the built-in player is mpv.
- macOS install steps (README, website): right-click → Open no longer gets
  past Gatekeeper since macOS 15, the oldest version the app runs on. They
  now go through System Settings → Privacy & Security → Open Anyway. The
  README and the website's help also explain how to update on every
  platform.

## [1.2.14]

Favorites in bulk: several at once, moved between folders by menu or by
drag, and no dialog over the window at startup.

### Added

- **Several favorites at once.** Ctrl/Shift-click gathers channels, movies or
  series in TV, Movies, Series and Favorites; "Add to favorites", "Add to
  folder" and "Remove from favorites" then act on all of them. Gathering rows
  never starts a channel's preview.
- **Move between folders.** In Favorites, "Move to" takes the selected rows
  out of the folder on screen and into another one, or out of every folder.
- **Drag and drop in Favorites.** Drag rows onto a folder in the sidebar to
  move them there, or within a folder to reorder it (when the list is in its
  own order, not sorted).

### Fixed

- Right-clicking a row in a multi-selection (Recordings, History) no longer
  drops the selection, so "Move"/"Delete" act on every selected row.
- A poster in the grid view can no longer be dragged loose of the grid.
- **Catalog metadata in the Linux downloads.** The AppImage and .deb now
  carry the AppStream metainfo (description, links, screenshots) the Flatpak
  already had, so AppImageHub and GNOME Software show them. Its release list
  had stopped at 0.7.2 and is current again; a test and `tools/release.sh`
  now refuse a release the metainfo does not name.
- **No dialog over the window at startup.** The once-only "Add to the
  application menu?" offer is now a link in the status row, opened when
  clicked; the startup dialog covered the app, and was all AppImageHub's
  screenshot of it showed.

## [1.2.13]

Linux packaging: the AppImage and .deb start where they could not, and a
release is now proven to start before it ships.

### Fixed

- **Host C++ runtime.** The bundle shipped libstdc++ and libgcc_s from the
  Ubuntu 22.04 build host (GLIBCXX 3.4.30). The GL driver is the host's,
  and newer Mesa's LLVM needs 3.4.32: with the older copy loaded first,
  Mesa failed to load, X11 was left without a GL config and Qt aborted.
  Both now come from the host; the bundle needs 3.4.29 at most.
- **Host Wayland libraries (#20, @lmerega).** The bundled libwayland-*
  (1.20) shadowed the host's, and Mesa built against Wayland 1.23+ needs
  `wl_display_create_queue_with_name`: on Debian 13 the window never
  appeared under Wayland. The whole client/cursor/EGL/server family is
  left on the host, both in the libmpv dependency staging and after
  PyInstaller's Analysis; the .deb depends on all four.
- **Qt's X11 platform plugin libraries.** libxkbcommon-x11, libxcb-icccm,
  libxcb-keysyms and libxcb-xkb were not installed on the build host, so
  PyInstaller left them out and the plugin could not load on a system
  without them. They are installed on the build host now.
- **No OpenGL, no crash.** On X11 without a usable GLX, Qt aborts the
  process when the video surface's window is created - and for a bare
  QOpenGLContext too. The X server is asked directly (GLX plus an RGBA
  window FBConfig) before the window is built; on a definite no the
  embedded player is left out and video opens externally. Multiview says
  so instead of opening.
- **Qt messages reach the log.** PyQt returns None for Qt's default
  handler, so the message filter dropped every Qt message, including the
  text of a fatal error.
- **`tools/release.sh` works from anywhere inside the clone.**

### Added

- Release checks start the app for real: X11 under Xvfb with the
  distro's own Mesa on Ubuntu 22.04/24.04 and Debian 12/13 (x86_64) and
  Ubuntu 24.04/Debian 13 (ARM), plus native Wayland under Weston on
  Debian 13, where a buffer must be committed to the titled main window.
  The bundle is audited for Wayland libraries and for an X11 plugin that
  cannot resolve.

## [1.2.12]

The Windows player, M3U links read as Xtream logins and a crash on
closing the resume prompt - plus everything a full review of the code
turned up: parental-control holes, refreshes that never refreshed, and a
list of smaller faults and dead code.

### Fixed

- **Windows: in-app video without a Vulkan driver (#19).** libmpv imports
  `vulkan-1.dll`, which only graphics drivers install. The release build
  now compiles the Khronos loader into the bundle, a self-check proves it
  is loaded from there, and `tools/win_dll_audit.py` fails the build on
  any DLL neither Windows nor the bundle provides. Settings > Playback
  names the missing file when in-app video is unavailable.
- **Windows: no console windows.** ffmpeg and ffprobe started by
  recording, the cast bridge and the thumbnailer each got a console of
  their own (for a recording, open the whole time - closing it killed
  the recording). They start with `CREATE_NO_WINDOW`.
- **M3U links stay M3U (#17).** The path fallback in `parse_xtream_url`
  required nothing of the last segment, so `/output/m3u` read as user
  `output`, password `m3u`. It now needs a numeric stream id.
- **Closing the resume prompt no longer crashes (macOS).** Home cards
  emitted `clicked` from `mousePressEvent`; the prompt's event loop and
  leaving Home ran under Qt's implicit grab, and a card deleted meanwhile
  crashed `QApplication::notify`. Cards act on release, deferred.
- **Parental control.** Home's shelves, the guide's whole-lineup
  fallback, the guide search and the cast filmography read the whole
  catalogue and showed locked and hidden categories; they go through one
  filter now, and Home re-filters its cached copies at paint time. Locked
  favorite folders are left off Home. Unlocking a category (menu or
  Manage categories), unlocking a favorite folder and removing a locked
  one ask for the PIN.
- **Switching playlists loads that playlist's rules.** The category and
  channel override stores were built once, so the previous playlist's
  hide/rename/lock rules applied and edits were saved under its key. The
  whole-catalogue caches are dropped on a switch and a refresh too.
- **M3U refresh.** `M3UClient` inherited a no-op `clear_list_cache`, so
  Refresh never re-downloaded; a failed first load was never retried.
  Refresh now re-downloads on the next list call (keeping the channels
  until the new copy parses), failures retry after a minute, and URL
  lookups never wait on a download. `#EXTINF` parsing keeps a comma
  inside a quoted attribute.
- **XMLTV times with `+0200` and no space** parsed as nothing, emptying
  such guides; `Z`/`GMT`/`UTC` read as UTC. A forced guide refresh that
  cannot download falls back to the copy on disk of any age.
- **Xtream: `get_short_epg` answering `[]`** no longer raises.
- **Trakt tokens are refreshed.** The refresh token and expiry were
  stored and never read. Calls now renew the token within ten minutes of
  expiry, under a lock, with a five-minute back-off after a refusal.
  Closing a sign-in dialog any way stops the loopback listener (which
  held its port for three minutes) and the device-code polling.
- **Recordings:** ffmpeg sends the player's User-Agent and reconnects on
  live URLs (not archive windows); the mpv fallback sends the User-Agent
  too. Renaming or moving a recording onto an existing name no longer
  overwrites that file. Recording an on-air programme from the guide
  takes the detail panel's path (in-player capture of the channel being
  watched) instead of a second connection.
- **Artwork:** a TMDB error was cached - persistently - as "no match";
  errors are session-only now. A failed filmography or person lookup left
  the cast panel waiting forever. The image loader wrote a non-image 200
  to its disk cache and refetched it on every paint; such a URL is marked
  dead. The shared dead-URL map is trimmed in place instead of rebound.
  The detail panel paints a poster only for the row still selected.
- **ID3 covers after a UTF-16 description** were cut at the wrong byte.
- **Local files:** leaving mid-scan left the scan marked active, so it
  never ran again that session; thumbnails use the bundled ffmpeg; "Add
  to multiview" lists every cell of the grid.
- **Multiview** reads a channel's archive flags defensively.
- **Stores:** `HistoryStore.remove` compares keys as strings like `add`;
  `FavoriteStore.remove` no longer creates a missing folder;
  `WatchedStore.clear` forgets whole-show marks; malformed watched and
  watch-later caches no longer raise at startup.
- **Crash tracebacks go through the logger**, so they reach
  `DOPEIPTV_LOG_FILE` and are masked.
- **Untranslated strings**: the wrong-credentials dialog, the PIN status
  line and the active-playlist mark.

### Removed

- Dead code: `timeshift_url`, the player's `_is_seekable`,
  `AUDIO_URL_EXTS` and archive-segment flag, the cast bridge's
  `_filter_escape`, `_remove_history`, `is_item_watched`, several
  write-only attributes, and 121 translation keys nothing used (from the
  English source and all 26 locales).

## [1.2.11]

Mostly Linux: the desktop icon, the links in the About window, and a set
of faults that all had the same shape - a list row and the player
disagreeing about what had been selected.

### Fixed

- **The app appears in the application menu, with its icon.** Wayland
  ignores what an app sets for its own window icon: GNOME and KDE read it
  from the .desktop entry matching the window, and an AppImage has never
  had one installed - so the taskbar showed a blank placeholder however
  right the icon inside the download was. The app offers once to add an
  entry (Settings > Playback holds the same switch, and unticking it
  removes the file); `Exec` points at `$APPIMAGE`, so it survives the file
  being moved or upgraded in place. A `.deb`, a Flatpak or a source
  checkout with no stable command is left alone.
- **The Linux icon is the size everyone else's is.** Drawn edge to edge
  while every icon beside it carries a margin - GNOME asks for about 80%
  of the canvas, the same proportion Apple asks for, so one constant now
  serves both. Applied to the runtime icon, the `.deb`, the AppImage and
  the Flatpak, whose committed icon was a single full-bleed 256 px file
  that nothing regenerated; a test now fails if those drift from the code
  that draws them.
- **An icon that will not appear has a readable cause.** Where a stale
  `icon-theme.cache` exists, GTK reads it instead of the directory and an
  icon added since is invisible - one four months old was hiding a set of
  perfectly good files. The app drops a cache older than its own icons,
  and logs one line naming which of the four possible causes applies.
  `install_icon` also replaces an icon whose bytes changed rather than
  skipping anything already present.
- **Links in the About window work again.** Website, download and the
  Trakt pages all did nothing from a packaged build: opening a link starts
  the browser as a child of this process, so it inherited the bundle's
  `LD_LIBRARY_PATH`, loaded our libraries instead of its own and died
  before drawing. Exactly the fault external mpv had; the cure it already
  had is now shared, and every link in the app goes through it.
- **A view toggle inside a series keeps the episodes.** It fell back to
  the series list, and a double-click there tried to play the series
  itself - an error, and playback stopped. Three faults of the same shape,
  plus the versions Favorites, Watchlist and Watched had of it.
- **Hiding a live channel no longer hides a film.** Provider ids are only
  unique within a type, so id 123 in live and id 123 in vod were the same
  row as far as the hidden list was concerned.
- **One scrollbar in the right-hand panel, not two nested ones.** The
  wheel, and a trackpad especially, went to whichever region the pointer
  was over, so the panel simply refused to move.
- **The "Next episode" card sits where the picture ends.** Its inset was
  sized for the floating fullscreen control bar and applied everywhere, so
  docked it hovered a bar's height above empty video.

### Security

- **Credentials are kept out of the log.** An Xtream stream URL carries
  the whole account (`/live/USER/PASS/1234.ts`), and these logs exist to
  be shared in bug reports. Two layers: `redact_url` understands the
  shapes a credential arrives in and is called where a URL is logged, and
  the account's own values are masked in every record by a filter, which
  covers the shapes not yet taught and any log line written later. CodeQL
  flagged three sites; four more had the same shape and are fixed too.
- **The cast bridge answers only for its own files.** It took the file
  name straight from the request and joined it onto our folder behind a
  blocklist that rejected `/` and a leading dot - which let a Windows
  backslash walk out of the folder. It is an allowlist now, with the
  joined path checked to be inside our own directory afterwards.

### Added

- **`DOPEIPTV_MAC_RASTER=1`** selects, on macOS, the fullscreen
  presentation path that cured this on Linux and Windows - opt-in, so both
  can be compared from a single build.

### Removed

- **The Intel (x86_64) macOS `.dmg`.** 1.2.10 is the last release that has
  one. Homebrew moved macOS x86_64 to Tier 3 in September 2026 — no CI, no
  new binary packages — so `brew install mpv` on the Intel runner now
  answers "no bottle available", and the same is coming for every
  dependency as its formula next updates. Building that chain from source
  on a Tier 3 machine takes hours per build and breaks constantly, and
  pinning an old mpv only holds until the next dependency loses its own
  package. Apple has said macOS Tahoe 26 is the last macOS for Intel and
  Homebrew stops running on Intel entirely in September 2027, so this was
  ending either way. Apple Silicon is unaffected.

## [1.2.10]

macOS fullscreen and Windows antivirus, plus a few things around watching
a series in a row.

### Fixed

- **Windows Defender no longer deletes dopeiptv.exe before it starts.** A
  false positive with two build-level causes. PyInstaller ships one
  prebuilt launcher used by every app built with it, and several engines
  hold signatures for those exact bytes, so the app was flagged for being
  a PyInstaller build; it is now compiled from source. The .exe also
  carried no VERSIONINFO resource at all, which weighs against a binary
  heuristically - it now has company, product, description and version,
  generated from `__version__` so it cannot drift. (onedir and no-UPX were
  already right.) The remaining SmartScreen warning needs an authenticode
  signature and a paid certificate; the shipped README covers what the
  warning means and how to report the false positive to Microsoft.

- **The picture no longer freezes on entering macOS fullscreen.** Audio
  carried on while the image stood still - a second or two at best, until
  you tabbed out and back at worst. Fullscreen moves the video into a new
  window, and on macOS a GL surface can keep presenting the frame it had
  in the old one while reporting success throughout. The remedy already
  existed for the pop-out path and had never been wired to this one.
- **Space pauses in fullscreen**, and so does every other shortcut. They
  were window-scoped, and in fullscreen the active window is the one
  showing the picture, so none of them fired there.
- **The pointer hides in fullscreen**, having been hidden on the window
  behind the picture rather than the one in front.
- **The Dock icon is the same size as its neighbours.** Apple insets an
  app icon within its canvas (824 of 1024) and ours was full-bleed. Fixed
  in both places it is set - the bundle's .icns and the icon the running
  app installs, which overrides it. It is also rendered above 256 px for
  the first time, so a retina Dock is no longer upscaling.
- **Subtitles carry to the next episode.** A track choice was filed under
  the episode alone, so autoplay lost it immediately. It is remembered for
  the series too.

### Added

- **A "Next episode" card** over the closing minutes of an episode, to
  skip the credits in one click. How early it appears is a setting
  (Settings > Playback: off, or one to ten minutes); it never advances on
  its own, because where the credits start is a property of the show and
  nothing in the file says where they are.

## [1.2.9]

Your own files, played like everything else - and autoplay works again.

### Added

- **A Local files section**, playing what is on disk and on any share the
  system has already mounted (a gvfs SMB mount on Linux, a `/Volumes` share
  on macOS - ordinary paths, so no in-app SMB client to go wrong).
- **Two views of a library**: plain folder browsing, or an Infuse-style view
  that reads file names and groups episodes into series and seasons, with
  films under Movies. The toggle sits in the middle column.
- **Artwork**: TMDB posters for what can be matched, an ffmpeg frame grab for
  what cannot, and embedded or folder cover art for music. A file with no
  year and no release tag in its name is never guessed at - it gets a
  thumbnail instead of some unrelated film's poster.
- **Music as music**: albums shelved together, artist/album/title read from
  the tags (FLAC, MP3, MP4 - dependency-free, header-only), a right-click
  play queue, a visualiser and a ten-band equaliser.
- **Local files behave like the rest of the app**: resume, a History category
  of their own, casting, multiview, and Trakt scrobbling where TMDB can name
  the file. A file on an unmounted share says so and offers to drop the row.
- **A settings section** for the watched folders and for clearing caches.
- **Live scan progress**, with results appearing as they are found rather
  than after the whole tree has been walked.
- **A native Windows ARM64 build.**

### Fixed

- **Autoplay, everywhere.** The music queue, local series and streamed
  episodes all hang off one signal, raised only from a poll of mpv's
  `eof-reached` - which is true only while `keep-open` is in force, and only
  if a poll catches it. End of playback now comes from mpv's `end-file`
  event, delivered exactly once whatever the options say; the poll stays as
  a backup.
- **The next episode no longer arrives paused.** `keep-open` pauses mpv at
  end of file, and that pause is player state, not file state, so it
  survived loading the next file.
- **Local series play through.** A local episode is not a provider episode
  and never reached the autoplay branch. Music always plays on; video only
  when it really is an episode, so one film ending never starts another.
- **An already-watched episode replays from History.** Those rows carry the
  series context but no provider episode id, and the URL was built from the
  missing id - a path that never existed.
- **Artwork is fetched for a whole shelf**, not only its first batch.
- **File work moved off the UI thread** - listing, tag reads and library
  walks are SMB round trips, and on the UI thread they were the macOS
  spinning beachball.
- **A frozen picture in macOS fullscreen heals itself** instead of waiting
  for the user to tab out and back.
- **A plot summary is never rendered as markup**: the music panel switches
  that label to rich text and did not switch it back.
- The Intel macOS build moved to `macos-15-intel`; its retired predecessor
  queued forever.

## [1.2.8]

### Fixed

- **The app no longer dies where the decimal separator is a comma.** On a
  German or French system it closed without a word as the video player
  initialised - on a fresh install, that was the wizard's "Try demo" button.
  libmpv requires the C numeric locale; Qt sets the locale from the system.
  It is now reset right before the player is created. (#18)
- **An M3U link is no longer mistaken for an Xtream URL** when its path has
  two or more segments; those were read as username and password.

### Added

- **An Intel (x86_64) macOS .dmg**, alongside the Apple Silicon one.

## [1.2.7]

### Fixed

- Casting: the stale-session check, a crash in the spool lead, the archive
  rewind, a 4K cap, and clicks on the seek bar landing where they were
  aimed. Chromecast setup now asks which device it is rather than whether
  it is old.

## [1.2.6]

Casting works.

### Added

- **A cast strip above the player**, naming the device and what was sent
  there, with a stop button, for as long as a cast runs. Casting stops local
  playback - the receiver pulls the stream itself - so the player pane went
  black with nothing anywhere saying why.
- **A local converter for channels the receiver cannot decode.** ffmpeg runs
  here and serves the result on the LAN, with the video copied through
  untouched and only the part the receiver could not take re-encoded (almost
  always just the audio; video only for HEVC, which is expensive and says so
  in the log). It is reached only after the device has refused the stream
  itself, so a receiver that decodes the channel never touches it, and what a
  device refuses is remembered for the session so the same twenty seconds of
  refusals are not spent again. ffmpeg is already required for recording, so
  this adds no dependency.
- **The log now says what a cast is doing** at every step: the address and
  MIME type handed over, the receiver's state and the sender's own connection
  for the whole life of the cast, the head of the playlist, the codecs inside
  the stream, and which of the two paths - the provider's own stream or the
  converted one - reached the TV.

### Fixed

- **Casting a live channel produced no picture.** The receiver was handed the
  panel's address and left to resolve the redirect itself. It now gets the
  address the stream really lives at, labelled with the Content-Type the
  server reports rather than a guess from a file extension - a resolved
  address usually has no extension at all, and the guess landed on
  `video/mp4`, so an HLS playlist was announced as an MP4 and refused.
- **Casting from History could never work.** It handed over the address the
  channel was played from - a raw MPEG transport stream, which the Cast
  platform does not support in any container. The rule is about the row, not
  the section it is shown in, so a live channel is cast as HLS from Channels,
  Favorites, History and Home alike.
- **A refused address gets a second attempt** at the panel URL, letting the
  receiver follow the redirect itself.
- **A cast that was only slow to start was killed by the retry meant to save
  it.** Loading media replaces whatever the receiver is doing, and silence was
  being read as refusal. Only an explicit IDLE with reason ERROR or CANCELLED
  starts another attempt now.
- **Casting took two presses.** The device list offers the devices you cast to
  last time before discovery has finished, so the first press could name a
  device this run had not met yet and failed with "not found - rescan". It
  discovers on demand instead, under a lock so a press during the opening scan
  waits for it rather than starting a second one.
- **Rescanning crashed inside pychromecast** with "Zeroconf instance loop must
  be running, was it already stopped?". Every device holds the browser's
  zeroconf instance and its socket thread reaches for it on reconnect, while
  `stop_discovery()` closes that instance - so the devices were left holding a
  closed one. Devices are disconnected first now, browser second, in both
  rescan and shutdown.
- **Playing something in the app now ends the cast**, which is the same
  handover casting performs in reverse. Otherwise the account held two
  connections at once and, on a tight limit, the new stream was refused - which
  looked like the app being unable to play anything after a cast.
- **Closing the app stops the cast** again. On close this runs in a daemon
  thread racing `os._exit`; tearing the bridge down first spent that budget
  waiting on ffmpeg and the HTTP server, and the stop never reached the
  receiver.
- **A stream the receiver has no decoder for is named** instead of failing
  silently: mpv is decoding the same channel and its codec list is used, with
  the transport stream's own PMT read as the fallback. Raw MPEG-TS and
  Matroska are refused up front rather than cast into the void.



The pop-out player works on Windows.

### Fixed

- **Windows pop-out rendered wrong, then not at all.** Upside down, then black
  on the GL mirror; a single frozen frame on the raster mirror. Both mirrors
  exist for defects Windows does not have - macOS leaves a stale layer behind
  after reparenting a QOpenGLWidget, and Qt/Mesa never presents GL in a second
  top-level window on Linux. 1.2.0 put Windows on the macOS mirror anyway, in
  the release that shipped calling Windows pop-out experimental and untested.
  Windows reparents the real player again, as it did before 1.2.0. Guarded by a
  test that fakes the platform, so it runs on any host.
- **Popping out stopped playback on Windows.** Detaching reparents the control
  bar while the originating click is still in flight; Windows delivers the
  release to whatever widget ends up under the pointer, which is the poster's
  play/stop button - stop, on a live channel. The overlay button now ignores a
  click within 400 ms of a pop-out toggle. Armed on win32 only, so macOS and
  Linux never reach the guard.
- **The pointer did not hide over the Windows pop-out video.** The idle-hide
  lived in the mirror path only; the reparent path arms the same timer now,
  driven by the player's own pointer-move signal, and restores the pointer when
  docking back.

### Internal

- The raster mirror logs a heartbeat (ticks per second, new frames, frames
  shown) instead of only logging state changes - a mirror that ticks without
  producing frames used to look identical to one that had stopped.

## [1.2.4]

Live streams stay up again - 1.2.2 broke them, and this reverts the cause.

### Fixed

- **Live streams dropped within seconds, repeatedly.** 1.2.2 began pushing the
  network-buffer setting (cache-secs, and a demuxer byte budget scaled from it)
  onto the stream already playing, so a changed setting applied immediately.
  It was reasoned to be safe - "a readahead target, no decoder reinit" - and it
  was not: cache-secs is a floor rather than a ceiling, nothing bounded the
  readahead, and reworking the demuxer's budget mid-stream is not free. The
  buffer is applied once per stream in play() again, as it was before 1.2.2; a
  change in Settings takes effect on the next stream. Playback stability
  outranks the convenience of a live-applied setting.
- **Auto-reconnect did nothing.** Recovering a dropped live stream clears the
  player's current URL to mark "nothing is playing" - which is also the guard
  that suppresses the failure diagnosis, so the drop being handled raced in and
  set the "already diagnosed" flag. The retry 300 ms later took its early exit
  and returned silently: the log read "live reconnect (eof) try 1/2" and the
  channel stayed dead, while playing it by hand worked instantly. The flag is
  reset when a reconnect is scheduled, and both silent exits now log why.
- **Trakt watched marks were never written to disk.** WatchedStore.replace()
  rebuilds the whole Trakt layer from a sync payload, and its _save() had
  drifted below the return of the method under it, where it could never run. A
  completed sync updated memory only: the marks were gone on the next start,
  and last_sync_at never stuck either, so the app re-synced on every launch.

### Internal

- The paint heartbeat reports paints per second and the demuxer cache time -
  the two numbers that identified the faults above.
- Dead code removed: two superseded dialogs (316 lines) and two unused
  methods, found by a dead-code scan over the package.

## [1.2.3]

Resizable video windows, sections that remember where you were, and a cast
panel that finally lists the right films.

### Video windows

- **The pop-out and the multiview grid can be resized without the title bar.**
  Both are title-bar-less by default, which left them with no resize grips at
  all - the only way to change their size was to turn the title bar back on.
  The video's own edges are the handles now, with the matching resize cursor
  when you hover one. A press on an edge resizes only; in multiview it does
  not move the audio to that cell.
- **The pointer no longer drags across a maximized video.** Moving it did real
  work per mouse report - hundreds a second - redrawing the centre play/pause
  glyph and re-placing the seek bar, timeshift timeline and sleep pill, each
  of which repaints the video underneath. Measured on 300 pointer events: 299
  surface repaints before, 1 after.

### Browsing

- **Each section remembers the sub-category you were in.** Jumping between TV,
  Movies, Series, Favorites and back dropped you at the top of the category
  list every time. Also for Watch Later, Watched, Recordings and History. A
  category that no longer exists falls back to the usual starting row, and
  explicit navigation - jumping to what's playing, drilling into a series -
  still wins.

### Cast

- **Fixed: a cast member was credited with films they are not in.** Titles were
  matched against your playlist with punctuation *and spaces* stripped, so the
  rule allowing a provider title to match when it merely starts with a credit
  had no word boundaries left - a credit like "America" claimed every
  "American ..." title. "The Wall" is not "The Wall Street Documentary" and
  "Frozen" is not "Frozen II"; genuine release junk ("Inception REPACK") still
  matches.
- **Fixed: picking a title there didn't land you on it.** It was navigated by a
  fixed delay that almost always fired before the category list had loaded, so
  it just played the film with nothing selected and an empty detail panel. It
  now starts playing at once and the middle column follows, in the title's own
  category. Shows drill into their episode list.
- **Fixed: missing posters in the panel.** Artwork is resolved through the same
  service and cache the middle list uses, so a title shows the poster it shows
  there - including the ones whose provider artwork is a dead proxy, where the
  list finds a TMDB poster embedded in that very URL.

### Playback

- **Fixed: unmuting a film that started muted stayed silent** until you replayed
  it. Muting moves the volume slider to 0 with its signals blocked, so mpv was
  handed that 0 as the stream's volume - and clearing the mute flag cannot undo
  a zero volume.

## [1.2.2]

The pop-out player works on Linux again, plus programme-guide, recording,
playlist-refresh, buffer and Continue-watching fixes.

### Pop-out on Linux (rewritten)

- **Fixed: the pop-out window was black.** On modern Qt/Mesa nothing OpenGL
  ever reaches the screen in a *second* top-level window - a reparented video
  surface (on X11 *and* Wayland), a second GL surface, and a native GL child
  window all reported successful rendering and showed black. The Linux pop-out
  no longer puts OpenGL in that window at all: each frame is rendered offscreen
  in the docked player's own (working) GL context and drawn into the pop-out as
  an image. The docked player's mpv instance and render context are never
  moved, freed or reparented, so playback cannot be disturbed by popping out,
  and docking back is instant.
- **Fixed: heavy stuttering in the pop-out.** mpv's render call blocks until
  each frame's target display time by default; called from the UI thread that
  stalled the interface for most of a frame interval (worst on 23.976 fps
  film). We present through a readback rather than a display swap, so mpv no
  longer paces the caller. Frames are also read one tick late (no GPU wait),
  rendered only when mpv actually has a new frame, and capped at 1080p.
- **Fixed: the docked mini player looked broken while popped out** - it still
  reserved height for the control bar that had moved into the pop-out, its
  responsive rules measured the wrong window (hiding the volume slider), and a
  frozen video frame could show through above the "playing in pop-out" panel.
- **Fixed: the control bar's menus (subtitles/options, record, timeshift) never
  opened over the pop-out** on Wayland, and docking back could leave the video
  paused.

### Playback

- **Fixed: "Refresh playlist" did nothing within five minutes of the last
  fetch** - it cleared the memory cache but then served the same lists back
  from the disk copy, so changes at the provider never appeared. Refresh now
  always re-fetches; the disk copy stays as the offline fallback.
- **The network-buffer setting applies to the stream you are watching**, not
  just the next one, and a larger buffer now also raises the demuxer's memory
  budget - so picking a bigger value genuinely cushions stuttery live channels.
- The record/reminder prompt for a broadcast that has not started appears
  immediately again on every channel.
- "Watch the recorded channel" starts playing even when picked the instant a
  recording began.

### Continue watching & History

- **Fixed: a film could not be resumed from Continue watching** (it failed with
  a stream error) and **appeared twice in History**, one copy unplayable. A
  title first played from History lost its identity when its resume point was
  saved; it now keeps both its key and its playable URL.
- **Fixed: Continue-watching cards on Home did nothing when clicked** for those
  same titles.
- **Fixed: a crash when clicking a card on Home.**

### Programme guide

- **Fixed: channel logos only appeared the second time the guide was opened.**
  They load in the background, and a logo arriving after the guide had scrolled
  to "now" was placed far to the left of the channel column instead of in its
  row. Only a logo already in the cache - i.e. one from a previous visit -
  landed correctly.
- **Fixed: clicking a programme's title did not select its box.** The title is
  drawn on top of the card so it can stay readable while the card scrolls, but
  it was answering clicks itself, which put the selection outline around the
  text. Only the box is clickable now.
- **Fixed: the buttons along the bottom were different sizes**, with the
  scheduled-recordings frame taller than the rest and its text out of line.
  Their symbols were emoji characters in the button text, rendered by whatever
  font the system substituted; they are drawn icons now, so the row is even on
  every platform.

### Recording

- **Fixed: a recording started behind the picture you were watching.** With a
  larger buffer the file began up to that much earlier than the frame on
  screen, because the whole buffer - including the part already played - was
  written out.

### Interface

- **The pop-out and volume buttons look right on Linux.** The pop-out symbol
  was a Unicode character that came out tiny outside macOS, and the speaker was
  drawn much smaller than the controls around it. Both are drawn to match their
  neighbours now.
- **The collapsed sidebar is remembered between sessions**, and the app mark at
  its top is no longer clipped on the narrow rail.
- **Multiview keeps your place.** Sending a film or an episode from the player
  to a multiview cell continues where you were instead of starting over
  (swapping two cells carries their positions across as well).

## [1.2.1]

Clearer update notices, a readable About dialog, and a calmer default startup.

- **A clear "new version available" banner.** When a newer release is found, a
  dismissible banner now appears across the top of the window - visible on every
  view, including the full-window Home page where the old sidebar badge was easy
  to miss. It shows once per version (and stays gone once dismissed).
- **Download goes to the website.** The banner's and the About dialog's
  **Download** buttons now open the website instead of the raw GitHub release.
- **Fixed: the About dialog's release notes were unreadable** - an unthemed
  white box with faint text. The notes panel now uses the app theme.
- **The app starts on TV by default.** Home is now opt-in: turn on "Open Home on
  startup" in Settings to land there instead. This applies to first run too.

## [1.2.0]

A dedicated place to manage upcoming recordings, a smoother maximize on macOS,
and a batch of pop-out and Home polish.

### Recordings

- **New "Scheduled recordings" panel.** A dedicated window lists every pending
  recording (recording now + scheduled), soonest first, with **Edit start/stop
  time** and **Cancel** right there. Open it from the player's REC menu or a new
  button in the EPG guide, so upcoming recordings are managed in one place
  instead of hopping between the sidebar and the middle column. It refreshes
  live as recordings start, finish or get cancelled.
- **Upcoming recordings surface on the default Recordings view.** The
  "All recordings" view now shows the pending recordings under an "Upcoming"
  header at the top (they were previously only under the "Active & scheduled" /
  "Upcoming" sub-categories). Folder views are unaffected.
- **Fixed: "Watch the recorded channel" did nothing when picked the instant a
  recording started.** ffmpeg creates the output file a beat after the job
  begins, so the option found no file and gave up silently. It now waits
  briefly for the file and starts playback automatically.
- Recording status and category labels are now translated in every language.

### Player & pop-out

- **macOS: maximizing the mini player is now as smooth as the pop-out.** It goes
  fullscreen through a frameless mirror window instead of the decorated main
  window, skipping the slow native fullscreen animation and the pane teardown -
  and you get the full control bar, auto-hiding controls and cursor-hide in
  maximized mode. Every way out (button, Escape, double-click) docks straight
  back to the mini player.
- **The pop-out control bar auto-hides** after a few idle seconds and returns on
  mouse movement, and **the mouse cursor now hides** over the video in the
  pop-out - windowed and fullscreen.
- **Sleep-timer countdown.** A top-right pill shows the time left before
  playback stops. It auto-hides with the other controls but pins on - and turns
  red - for the final 30 seconds, so the imminent stop is unmissable.
- **Fixed: a crash when opening the pop-out.** The seek bar's hover tooltip
  held a reference to a window that had been destroyed by the reparent; it is
  now rebuilt on demand.
- **Windows: the pop-out now uses the same mirror approach as macOS.** This is
  **experimental and not yet sufficiently tested on Windows** - treat Windows
  pop-out as unstable for now. Linux keeps its existing (unchanged) pop-out.

### Home

- **Resuming a movie from Home lands in its own category** with the movie
  selected, instead of leaving you in whatever category was last open.
- TV-channel logos on Home are centred in their tiles (and a touch larger).

### Settings

- **"Reset all settings" now really clears everything.** Watched, Watch Later
  and resume positions live in separate config files and used to survive a
  reset; all three are now cleared alongside the main settings.

## [1.1.0]

Video stability on macOS, smarter series navigation, a leaner Home, and a
faster app all around.

### Video

- **Fixed: pop-out froze or blanked the video (audio kept playing).** Docking
  the player in or out of the pop-out window reparents the video widget. On
  macOS (shared OpenGL contexts) Qt *preserves* the GL context across that
  reparent, so none of the usual rebuild hooks fire - the widget's backing
  framebuffer was still composed against the old window and the screen froze
  on the last frame while mpv kept playing. Only a window resize (e.g.
  fullscreen) recovered it. The player now does the equivalent automatically:
  after every dock/undock the video surface gets a 1-pixel resize nudge that
  rebuilds the backing framebuffer against the new window. Pure widget
  geometry - mpv and the stream are never touched. Where the GL context
  genuinely is recreated (Linux), the render context is rebuilt as before,
  plus a throttled self-heal in the paint path as a safety net.
- **Fixed: entering video fullscreen flashed a half-sized frame.** The window
  went fullscreen *after* painting had resumed, so one frame showed the video
  filling the still-normal-sized window. The whole transition is now painted
  as a single clean cut.

### Series navigation

- **"Now playing" jumps into the right episode.** Clicking the sidebar logo
  while an episode plays now lands inside that series' episode list with the
  playing episode selected - previously an async category reload could bounce
  the view back to "all series" before the drill finished.
- **Home's Continue watching opens the series too.** Clicking a partly-watched
  episode on Home plays it *and* drills the middle column into the series'
  episode list with that episode selected.
- **Back from an episode list lands in the series' own category.** Backing out
  now selects the category the series actually lives in (with the series row
  selected), instead of whatever category happened to be selected before.
- **Stable episode posters on Home.** Continue-watching episode cards resolve
  the series' poster (the same art the episode list shows) instead of
  title-searching the mangled episode name as a movie, which made the poster
  appear and disappear at random.
- **Recently viewed treats episodes as episodes.** History rows for episodes
  now carry their series: replaying one resumes where you left off (it used
  to restart from zero), drills into the series' episode list like Continue
  watching, resolves the series' poster, and reads "Series · S1 E2 - Title"
  instead of a bare "S01 E01". The duplicate rows the old context-less
  fallback created are gone (History also dedupes across int/str id types).

### Home

- **New: a Watch Later shelf.** Movies and shows from your Watch Later list,
  with the same play/drill behaviour as everywhere else; rows the provider
  can't act on are filtered out. Toggleable under Settings > Home.
- **Continue watching and Recently viewed no longer overlap.** A partly-
  watched title lives on Continue watching; Recently viewed shows the rest
  (turn Continue watching off and it shows everything again).
- **The TV guide from Home (or Movies/Series) covers your favorites.** It
  used to open over an arbitrary slice - whatever category the hidden TV
  view had auto-selected, or the first 300 channels of the provider's full
  dump. Now it guides your favorite channels (the lineup you actually
  curated) and falls back to the full lineup when you have none. Opened
  from TV or Favorites it is scoped to the current list, as before.
- **Removed the Featured row.** It was the top 12 of the same recently-added
  movies already shown directly below it - pure duplication, and its oversized
  posters were the slowest part of the page.

### Performance

- **Faster startup**: heavy modules load lazily (~40% off import time), and an
  optional custom allocator (jemalloc/mimalloc) can be enabled with the
  `DOPEIPTV_JEMALLOC` environment variable - off by default, a strict no-op
  unless the library exists.
- **Snappier UI**: fullscreen toggles batch their relayout into one paint,
  the Settings dialog builds ~30% faster, list scrolling and divider drags are
  smoother, and the image caches are bounded (2.5 GB disk budget with
  automatic pruning).
- **Sidebar and layout state survive fullscreen**: the sidebar's expanded/rail
  choice and the middle column's full form are kept across video fullscreen
  round-trips.

### Settings

- **A pasted provider link survives messy input.** Pasting an Xtream/M3U
  link into a server field that already held text (or copying it along with
  a text prefix) used to silently fail the auto-fill; the link is now found
  in the pasted text and the fields fill cleanly.
- **Removed the one-time 0.9.0 settings migration.** Every install that
  needed it has run it; it no longer lurks in the background re-pruning
  settings after a manual reset.
- **Fixed: the language dropdown wouldn't scroll in Settings.** The wheel
  guard that stops the mouse wheel from accidentally changing a setting was
  unknowingly installed on every *scrollbar* too (a scrollbar is a slider to
  Qt), including the one inside the language dropdown's popup - and Qt
  delivers wheel scrolling *through* the scrollbar, so the open list ignored
  the wheel entirely on every platform. Scrollbars are now exempt from the
  guard: the popup (capped at 14 rows, with a visible scrollbar) scrolls
  normally with wheel or trackpad, and every other box still swallows the
  wheel so scrolling the page can't nudge a setting. A restart hint under the
  picker says a language change needs a restart. Release builds also verify
  that all 27 languages load from each packaged artifact (.dmg, Windows zip,
  AppImage/.deb, wheel), so a packaging slip can never silently ship an
  English-only picker again.

## [1.0.1]

Packaging fix for the 1.0 release.

- **Fixed: only English available in the installed apps.** 1.0.0 was the first
  release to ship translations as external `dopeiptv/locale/*.json` files
  rather than inline in the code. In the frozen builds (macOS `.dmg`, Windows
  `.exe`, Linux AppImage/`.deb`) those files weren't located at runtime — on a
  macOS `.app` the code lives in `Contents/Frameworks` while the data lands in
  `Contents/Resources` — so the language picker silently collapsed to English
  only. The locale directory is now found across every packaging layout
  (source, wheel, PyInstaller bundle root, macOS `.app`), and the locale files
  are bundled explicitly in all three PyInstaller specs. The pip/pipx install
  was unaffected. A regression test covers the frozen-bundle lookup.

## [1.0.0]

The 1.0 milestone: dopeIPTV now speaks **27 languages**, sets up from a single
pasted link, scrolls large lineups without lag, and ships a strictly
type-checked logic core with a translator-contribution workflow.

- **27 interface languages** — the whole UI is now translated into 27
  languages (up from 8): added Português, Italiano, Nederlands, Polski,
  Hrvatski, Srpski, Ελληνικά, Türkçe, Українська, Bahasa Indonesia, Tiếng
  Việt, हिन्दी, 日本語, 한국어, Kiswahili, العربية, فارسی, עברית and اردو.
  The four right-to-left languages (Arabic, Persian, Hebrew, Urdu) mirror the
  layout. English is the inline source; every other language is a single
  reviewable `dopeiptv/locale/<code>.json` file, so a native speaker can
  correct one language in isolation.
- **Paste a whole Xtream link** — the onboarding wizard and the Add/Edit
  playlist dialog now accept a full Xtream URL (`get.php`, `player_api.php`,
  or a direct stream URL) pasted into the server field and split it into the
  server / username / password fields for you. Typing the three fields by
  hand still works exactly as before, and the language picker in the welcome
  screen now lists all 27 languages.
- **Smart provider detection** — a pasted link is recognised automatically as
  Xtream or M3U and the wizard/dialog switches mode for you. Xtream is always
  preferred (its API also serves movies, series and EPG), so even a `get.php`
  M3U-export link configures a full Xtream provider.
- **Snappier lists** — channel/movie/series logos and posters are now scaled
  once and cached instead of being re-scaled on every repaint, which removed
  the main source of scroll lag with large lineups. Lists also use smooth
  pixel-granular scrolling and lay huge lineups out in background batches.
  None of this touches the video pipeline.
- **Translator workflow** — a `docs/TRANSLATING.md` guide, a "Translation
  fix" issue template, and a `tools/i18n_status.py` health report (coverage,
  stray keys and placeholder mismatches per language) make it easy for native
  speakers to correct the machine-translated locales.
- **Stricter type gate & docs** (internal) — the mypy type-check now covers
  the whole pure-logic layer (13 modules across `providers/`, `core/` and
  `services/`, up from 3), a few latent type issues were fixed, and every
  module and class now carries a docstring.

## [0.9.0]

**Home** — a full-window start page with a Featured hero row and shelves — plus an
interactive EPG guide, reminders/recording for upcoming programmes, and a big
round of player, artwork and stability work.

- **Home** — a SwipTV-style start page (opens at launch, configurable): an
  oversized **Featured** hero row, and shelves for **Continue watching**,
  **Favourites now** (with the current programme), **favourite movies &
  series**, **Recently viewed** and **Recently added** movies, series *and TV
  channels*. Posters paint instantly on a cold start from a per-playlist disk
  cache and refresh in the background. Text-only rounded quick-nav (TV / EPG
  Guide / Movies / Series / Settings) in the accent colour. Right-click a
  channel tile to set a reminder or record without leaving Home. Clicking a
  channel lands the classic list in that channel's own category with the row
  selected, and the detail panel follows. Every shelf can be toggled in
  Settings.
- **EPG guide, interactive** — channel logos, programme descriptions, an info
  panel, progress on the on-air card, day-jump arrows, full arrow-key
  navigation, sticky programme titles while scrolling, playable rows even for
  channels without guide data, duplicate entries dropped, and the whole guide
  follows the theme's accent colour.
- **Upcoming programmes: reminders & recording** — when a stream hasn't
  started yet (the provider's 407), the app says so at once and offers a
  **reminder when it starts** or a **recording** (until the programme ends, or
  a custom length). The same actions live on right-click — in the channel
  list, in Home and in the detail pane's programme guide. A "don't show
  again" opt-out is resettable in Settings.
- **"Recently added"** — a 🆕 category under TV, Movies and Series (newest
  first, cached briefly so re-opening is instant).
- **Player** — click the video to pause/play with a centred play/pause disc
  (docked, fullscreen and pop-out); the right-click menu is complete
  everywhere (pause/stop, fullscreen, audio/subtitle tracks, delay, aspect,
  filters, sleep timer); hovering the timeshift timeline shows the time you'd
  jump to; the poster overlay tracks pause/play state correctly for
  timeshift and catch-up.
- **Artwork** — episode posters resolve through the show's TMDB artwork
  (providers rarely ship per-episode images), junk cover URLs ("n/A" & co.)
  are skipped, and recordings show their channel's logo.
- **Faster failure answers** — a parallel probe surfaces definitive stream
  errors (forbidden / not found / not started) in about a second instead of
  after the whole retry budget.
- **Onboarding & housekeeping** — clearer first-run flow (playlist name hint,
  Trakt confirmation, icon-only actions), Content Manager lists categories
  alphabetically, community health files (contributing, security policy,
  code of conduct, issue templates).
- **Stability** — many macOS repaint fixes (ghosted shelf titles, menus
  bleeding through the video, double-exposed programme lists), a robustness
  test suite (246 tests), fixed EPG-grid crash on rebuild, and the stuck
  "Loading channels…" indicators are gone for good.
- **Instant startup & outage resilience** — the app opens immediately (no
  more blocking "Connecting to…" splash; credentials verify in the
  background), channel/movie/series lists are cached in memory for 5
  minutes AND mirrored to disk per playlist, so a down or overloaded
  provider shows the last known lineup instead of empty lists. After a
  network failure the client fails fast for 30 s (no more minutes-long
  hangs), and any successful reply lifts the cooldown at once.
- **Timeshift trust** — only a proven provider response ("this is an error
  page, not a stream") may ever hide a channel's catch-up. Player-level
  noise (an mpv error right after the swap, a too-early "not seekable"
  reading) no longer silently strips timeshift off working channels, and
  the archive probe treats a network failure on any URL format as
  inconclusive. The TIMESHIFT badge no longer appears when pausing a
  channel without an archive (that pause rides mpv's local buffer).
- **Fewer wrong prompts** — the "upcoming broadcast" reminder/record dialog
  is no longer shown when an overloaded panel answers 407 for an ordinary
  live channel (the guide-on-air check and the network cooldown gate it).
- **One-time settings reset** — because so much changed, the first launch
  of 0.9.0 resets stored settings to the new defaults. Playlists,
  favorites, history, reminders, recordings, category customisations,
  parental PIN, language and the Trakt account are all kept.

## [0.8.1]

**Fix: working catch-up channels could silently lose their timeshift.**

- A momentary network failure during the archive check (timeout, DNS/TLS error, refused connection) was treated as proof that a channel serves no catch-up, hiding its whole timeshift UI for 14 days with no message. Only a real provider response (an error page instead of a stream) can hide a channel's catch-up now; network failures leave everything untouched and show a clear "couldn't reach the archive" status instead. Channels already hidden on 0.8.0 come back after one playlist refresh (↻).

## [0.8.0]

**Multiview** — watch up to nine live channels at once — plus a sharper,
truly cross-platform interface and a long list of fixes.

- **Multiview** — a separate grid window (2/4/6/9 windows, size under Settings → Multiview) where every window is an independent stream: mix channels from different playlists/accounts, click a window for audio focus (red border marks the audible one), right-click for mute, **audio/subtitle track choice**, **move/swap between windows** or remove. Catch-up channels get a **real timeshift timeline** — programme ticks from the EPG, hover names the programme, drag to any point in the provider archive, a LIVE pill jumps back to the edge. Each window shows its channel, current programme and source playlist; overlays, controls and the mouse cursor auto-hide; double-click maximizes; viewing is recorded in History. Send the currently playing video there via right-click, and starting playback in the main window offers to close multiview (configurable). A Settings → Multiview tab collects everything: grid size, title bar/always-on-top, remembered window geometry, auto-hide delay, seek step, audio behavior and the connection-conflict policy.
- **Identical icons on every OS** — the sidebar and control icons are now a hand-drawn vector set (no more emoji fonts that rendered differently on Linux/macOS/Windows), the playlist switcher is a compact icon chip that reveals the active playlist's name on hover, the logo (= jump to now playing) stays on the collapsed rail, and EPG Guide is reachable from right-click menus (TV entry, live categories, channels).
- **Settings overhaul** — a new Multiview tab; number fields with real stepper arrows and dropdowns with visible ▼ on every OS (drawn assets); checkboxes that are actually visible on the OLED pure-black theme; a custom-painted tab bar with identical packed spacing on every platform; the dialog sizes itself to fit.
- **Favorites everywhere** — add/remove favorites from History, Watch Later and Watched; the grouped "All favorites" view routes movies/series to the right store (remove used to silently miss) and no longer auto-plays a movie row as a live channel.
- **Fixes** — resume prompt now appears for movies/series played from Favorites; History entries mis-filed as TV channels are healed automatically and play again; a pending auto-preview can no longer stomp a just-started playback; switching playlists no longer forces a full EPG re-download; macOS: no duplicate app menu, no clipped sidebar icons, and Settings → Reset all settings no longer crashes on exit.
- **Remembered tracks on resume** — the audio/subtitle track you pick for a movie, episode or recording is remembered per title and re-applied when you replay or resume it (picking a subtitle track now also always makes it visible).
- **Performance** — dragging the column dividers is much smoother (icons are cached instead of re-drawn, redundant list relayouts are skipped); app startup is faster (Chromecast support now loads on first use instead of at launch); multiview uses far less memory when the grid mixes plain live channels with catch-up ones (the deep rewind buffer is only allocated where it can be used).

## [0.7.3]

**A real pop-out player, simpler playback, and stream errors that finally
tell you what's wrong.**

- **Pop-out player** — detach the video into its own window (the ⧉ button in the player bar, or `P`) and keep it on a second screen while you browse and zap in the main window. It's the *same* in-app player moved out, so the full control bar, seeking and channel-zapping all come with it. By default it's a clean, title-bar-less video window you move by dragging the video; right-click it for **Always on top**, **Show title bar** or **Auto-hide controls**, double-click for fullscreen, `Esc` to leave it. This replaces Picture-in-Picture, which it fully supersedes.
- **Simpler playback** — removed the confusing "Playback mode" setting. The embedded player is the player; "Open externally" (mpv/VLC) stays on the right-click menu. The two old modes ("reused mpv window" and "external") were buggy or redundant and are gone.
- **Stream errors in plain language** — when a channel won't play, the app now says *why* instead of "loading failed": expired subscription, all connections in use, provider blocked the stream (incl. the non-standard `458`), server unreachable/timeout, or a format it can't play. No debug mode needed.
- **Troubleshooting** — `DOPEIPTV_LOG=debug` now logs every provider call (auth state, connections, HTTP status) with credentials redacted, and `DOPEIPTV_LOG_FILE=/path` tees it to a file for bug reports.

## [0.7.2]

**Windows joins Linux and macOS** — a portable Windows x64 build (unzip and
run, no installer) — plus timeshift and UI fixes.

- **Windows (portable)** — unzip the folder and run `dopeiptv.exe`, no installer and no admin rights; an optional Start-menu/desktop shortcut under Settings → Interface → Maintenance, a bundled README, and a fix for a crash on exit in the windowed build.
- **Timeshift** — channels that advertise catch-up but don't actually serve it now hide the timeshift affordance (list marker, in-player rewind button and seek-bar overlay) instead of leaving it behind an "archive unavailable" message. Channels with a genuinely shorter archive are unaffected.
- The category-search magnifier now sits centred in its button.
- Website: grouped, plain-language download page (OS + CPU), a subtle per-file download counter, and macOS/Windows first-launch notes.

## [0.7.1]

A **UI/UX polish** release: a redesigned, responsive left panel and a calmer
timeshift player.

- **Redesigned left panel** — icons for every nav item, a Browse/Library split with a collapsible Library group, and Guide/Settings as a compact side-by-side pair. The sidebar auto-collapses to the icon rail when the window gets narrow.
- **Responsive layout** — the middle-column controls go compact on a narrow column, the player's control row never drops the volume, and everything below the video lives in one scroll column so the logo/programme info can't overlap the picture. A minimum window size prevents squeezing panes into overlap.
- **Timeshift timeline auto-hides** after a few idle seconds and reappears on interaction.
- Accent colours follow your chosen theme everywhere (EPG "now" progress bar, playing highlights); the sidebar collapses correctly in the Watched/Trakt lists; About → website link and better release-notes formatting.

## [0.7.0]

**Timeshift &amp; catch-up TV**: scrub back into a channel's archive on a live
timeline, pause live TV, or jump to a specific past programme — plus a
**reminders manager**, **search everywhere**, **customizable keyboard
shortcuts**, and **rock-solid video playback** by default.

<details>
<summary><b>Full notes</b></summary>

### Timeshift, catch-up &amp; DVR
- **Live timeline** on timeshift channels — scrub back into the provider's archive and watch what already aired.
- **DVR-style pause** — pause live TV and resume behind live; the player shows how far behind live you are and drops the LIVE tag the moment you pause.
- **Pick a specific past programme** from the guide and play just that show, with a seek bar clamped to the programme's own length.
- **Archive-depth learning** — the app learns how far back each channel really reaches, adapts the “go back” span to it, and recovers gracefully from over-deep requests instead of dropping the channel.
- **Catch-up from History** — resume timeshift straight from a channel in your History, with a catch-up marker on those rows.
- **Per-channel reset** — right-click a timeshift channel to clear its learned archive state.
- Amber timeshift markers, a **Go-live** button, and arrow-key scrubbing (fine-seek inside a segment, Shift+arrow for a coarse timeline step).

### Reminders &amp; guide
- **Reminders manager** — find, review and multi-delete programme reminders in one place; the store is crash-proof against decorated rows.
- **Programme search across the whole guide** — find a show by name anywhere in the EPG.
- **Configurable “upcoming programmes”** in the detail pane (default 5, adjustable).
- Guide polish: a **Close** button, centring over the main window, and deeper EPG fetches.

### Search everywhere
- **Category search** — match category names *and* the channels inside them, with smart ranking; collapsible on the sidebar.
- **Left-column search** in Favorites, Watch Later, Watched, Recordings and History.
- **Jump-to-now-playing** (sidebar logo) also selects the playing channel's category.

### Keyboard &amp; navigation
- **Customizable keyboard-shortcuts editor** with sensible per-OS defaults.

### Video &amp; playback
- **Software decoding is now the default** — like standalone mpv. Modern CPUs handle even 4K 10-bit HEVC/HDR comfortably, and it's immune to the GPU/driver render hazards that could black out hardware-decoded video with subtitles (e.g. the nvidia-open stack).
- **Hardware decoding is an opt-in** setting (Settings → Playback → Video) for those who want it, with mpv's `hwdec-software-fallback` as a safety net for genuine decoder failures.
- **Video filters** — optional deinterlace, sharpen and HDR tone-mapping, plus a **Video** menu in the in-player options.
- Faster channel switching via a light stream probe for live zapping (full analysis is kept for VOD).

### Recordings
- **Editable recording title &amp; description.**
- Recording **stops cleanly when you switch the channel** it's capturing.

### Settings &amp; interface
- Playback settings are **grouped and scrollable**, and **scrolling the page never changes a control** — you have to click into it.
- **Themed spin boxes** (fixes the white-on-white “upcoming count” box) and a tidy **Maintenance** row.

### Fixes
- **Fixed an exit segfault** on newer Python during teardown.
- **macOS:** arrow-key scrubbing works, and Picture-in-Picture stays floating.
- Audio no longer goes silent after switching a movie to a TV channel.
</details>

## [0.6.4]

Stability &amp; housekeeping: **4K plays smoothly**, the app **no longer bloats or
slows down over time**, and **live TV recovers from drops on its own** — plus
continue watching, EPG reminders, and a discreet update check.

<details>
<summary><b>Full notes</b></summary>

### Smooth 4K &amp; playback stability
- **Fixed the periodic 4K stutter.** The resume‑position save was rewriting the whole multi‑MB settings file every 12 s — it now lives in its own small file, so playback is never hitched by it.
- **Fixed the video stutter when dragging the volume slider** (and other quick settings): the big TMDB/Trakt caches shared that same file, so they've moved to a dedicated `cache.ini` and small writes are instant.
- **Hardware decoding on by default** (`hwdec=auto-copy-safe`) for smooth 4K across GPUs, with `DOPEIPTV_HWDEC` / `DOPEIPTV_DEMUX_MAX` / `DOPEIPTV_VIDEO_SYNC` escape hatches.
- Audio no longer goes silent after switching a movie to a TV channel.

### No bloat or slow‑down over time
- **TMDB caches are now capped** so months of browsing can't grow them without bound.
- **EPG guides are gzip‑compressed on disk** (hundreds of MB → tens) — raw guide and parsed index.
- **Orphaned EPG caches are cleaned up at startup** — guides for playlists you've removed no longer pile up (can reclaim gigabytes).
- New **Settings → Playback → “Refresh guide now”** and **“Clear EPG cache”**.

### Live TV
- **Live streams reconnect on drop** instead of freezing on the last frame.
- **“Auto‑reconnect live streams” toggle** — turn it off on a single‑connection/shared account so the app doesn't grab the connection back from another device.
- **Zap conveniences:** a “last channel” key and type‑a‑number to jump.
- **Double‑click to play a channel** by default (single‑click / arrow‑key zapping is now an opt‑in setting).
- **Account status panel:** expiry, days left, and connections for the selected provider.

### Continue watching &amp; Recently added
- **Continue watching** for partly‑watched movies **and** episodes, with the series' artwork + name on episode rows — under both Movies and Series.
- **Recently added** — newest Movies and Series first.

### EPG &amp; reminders
- **Programme reminders** — get notified when a show starts, then tune straight in.
- Clearer loading: a centred spinner and a label that names what's loading.

### Player extras
- **Next‑episode button** and **auto‑play the next episode** at the end.
- **Sleep timer** — stop playback after a chosen number of minutes (presets or custom).
- Stats‑for‑nerds fixes (no more blank rows).

### Trakt &amp; Settings
- Simpler Trakt connect (connect via browser).
- Watched rows are named from Trakt's own title when TMDB has no match.

### Polish &amp; fixes
- **Discreet update indicator** — a small “Update available” pill in the sidebar when a newer release is out (once‑a‑day background check; opt out in Settings → Interface).
- Sidebar rail collapses/expands smoothly within a single drag and snaps clean on release.
- Version numbers realigned, a latent startup crash fixed, and debug scaffolding removed.
</details>

## [0.6.3]

A stability‑and‑polish release: **keyboard shortcuts** across the player and EPG
guide, a **calmer progress indicator**, and a batch of **playback stability**
fixes — plus the app is now **Flathub‑ready** (fully offline build).

<details>
<summary><b>Full notes</b></summary>

### Keyboard shortcuts
- **Global:** `Ctrl+G` opens the EPG guide, `Ctrl+B` toggles the sidebar rail, `Ctrl+Shift+M` toggles focus mode.
- **While the player is up:** `M` mute, `P` Picture‑in‑Picture, `R` record, `I` stream stats, `←`/`→` zap channels, `↑`/`↓` nudge the volume.
- **In the EPG grid:** `N` jumps to now, `P` to the playing channel, `Enter` plays the selected programme.

### Progress indicator
- The loading strip is now a **steady indeterminate indicator** instead of a percentage bar that could stick at “100%”.
- A **watchdog** clears it automatically if a background job never reports back.

### Playback stability
- **Auto‑reconnect** — a live stream that drops on a network hiccup retries quietly instead of failing.
- **Stall recovery** — a watchdog notices a frozen picture and recovers it.
- **No drop on window move** — moving/reparenting the window no longer tears down and rebuilds the video.

### Packaging
- **Flathub‑ready** — the Flatpak manifest builds **fully offline** from pinned, vendored wheels, plus a submission guide.
- Fresh **store screenshots**.
</details>

## Earlier releases

Notes for **0.6.2** and earlier are on the
[GitHub releases page](https://github.com/slimture/dopeIPTV/releases).

[0.6.4]: https://github.com/slimture/dopeIPTV/compare/v0.6.3...v0.6.4
[0.6.3]: https://github.com/slimture/dopeIPTV/compare/v0.6.2...v0.6.3
