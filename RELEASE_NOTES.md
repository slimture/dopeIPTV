## dopeIPTV 1.6.5

**A crash and two mix-ups fixed, safer settings sync, and supporting dopeIPTV is now a tip.**

### Fixed

- **A provider that refuses the login** no longer crashes the app as it opens. The lists from before stay; without them it says the user name or password is wrong.
- **A source's type:** changing it in Settings no longer takes another type's address and login along (an Xtream login turned up under Jellyfin or Tvheadend). Each type keeps its own, and a source is stored as the new type only once its own fields are filled in.
- **M3U channels keep who they are** when the provider adds, removes or reorders channels: favourites, hidden and renamed channels, your own numbers and reminders no longer move to the channel next door. The first start after the update reads the list as before; the start after that moves what is kept, in every profile.
- **Settings sync:** restoring an older backup no longer takes anything from your other computers; the same playlist added on two computers is one; a full history list no longer removes titles on the others; and what a round brought in is kept even when the window closes right after.
- **Server mode:** the tuner can no longer be reached from a web page (DNS rebinding). Plex, Jellyfin and Channels are not affected.
- **Casting:** going back in a channel says so on the card - "Going back…", "Back to live…" - until the TV plays it.
- **AirPlay (macOS):** going back in a channel moves the Apple TV there again; a stream it is still loading is no longer started over after ten seconds; a seek reaches it sooner.
- **TV:** a channel's name is no longer cut short on a scaled Windows screen, and the name column is as wide as the longest names need (#35).
- **Mac:** the app's text is in the system's own font, and the app starts a little faster.
- **Send feedback** fits the screen and scrolls; **messages** take a new theme's colours.

### Changed

- **Supporting dopeIPTV is a tip or monthly support** - never a donation - in all 27 languages, and the heart opens the new support page on dopeiptv.org.

## dopeIPTV 1.6.4

**Casting with a card of its own that gets a stuck TV going again, and a round of fixes.**

### New and better

- **Casting has a new look.** While something plays on a TV, a card stands where the video was: the channel's logo or the film's poster, what is on, the TV it plays on and how it goes there - Playing, Paused, Buffering…, Reconnecting… - with big buttons for play and pause, 30 seconds back and forward, and Stop.
- **A stuck cast gets going by itself.** A TV that buffers for ten seconds without anyone pausing is sent the stream again from where it was - twice at most, and never with a second connection to your provider. If that does not help, the card says so with **Try again**.
- **On TV**: what plays on the TV is marked in every list and place, and the Now playing button goes to it.
- **A cast button** in the player, and the cast window has the TV you used last on top, ready - Enter casts.
- **AirPlay (macOS):** the AirPlay window opens by the card and stays with the app instead of over other programs; going back in a channel starts much sooner.
- **The phone remote and the media keys** show and drive what plays on the TV - play and pause, stop, seek, channel up and down, the TV's volume.

### Fixed

- **Search:** a right-click on a result opened no menu and sent an error report instead.
- **macOS:** the pointer hides again over the video, in the window and in full screen.
- **Now playing** goes exactly to what plays - a recording or a local file too.
- **Recently watched, Continue** and the other rows over the lists no longer start playing on a single click unless autoplay is on.
- **Messages** at the foot of the sidebar: a long one is no longer cut off, and two no longer end up on top of each other.
- **AirPlay** no longer says the Apple TV has not reached the Mac after every seek, and the card says Playing as soon as the TV does.

## dopeIPTV 1.6.3

**Room for the list, a guide that follows the category, and every place in the same new look - with the picture on its own thread on a Mac too.**

### New and better

- **Movies**: a click shows the film in the right-hand column - with **Continue** for one you have started - and a double click plays it. No more page of its own in the middle saying the same thing again.
- **Room for your categories.** The places at the top of the sidebar fold into a row of icons with the little arrow under them, so the categories get the whole height. Places you never use - Guide, Search, Local files, Radio, Recordings - can be taken out of the sidebar.
- **Just the list.** One switch in **Settings → Appearance → What the places show**: no rows over the lists, a lower header, Movies and Series as rows. Rows you do not want - Recently watched, Continue, Popular right now - can also be hidden one by one: right-click their heading.
- **The guide follows the category.** With the guide open, pick Movies, News or Sport in the sidebar and the guide shows that category. A double click on a category opens its guide, and a double click again goes back to the channels; **Show in the guide** is in the category's menu too.
- **Local files, Recordings and Radio in the new look.** Local files shows a renamed, added or removed file by itself, without Rescan. Recordings shows what records now and next at the top, a picture from each recording and what you have watched. Radio has My stations on top and every station with its logo.
- **The picture on its own thread on a Mac** as on Linux and Windows: the window stays smooth while video plays, the controls come up when you move the mouse over the picture, and a right-click never leaves it black.
- **Closing multiview**, the window you were listening to goes on in the main window - a film where it was.
- **Answers to your feedback** show within minutes, and when you come back to the app, with a message that stays until you read it - after a restart too. When an answer asks for more, you can reply right under it - still no account, no e-mail.
- **Phone remote:** the window with the code closes by itself once your phone has connected, and a note says so.
- **Record in TV mode.** Press **Options**, then **Record**: what you are watching is recorded, without a second connection to your provider. While it records, a small **REC** sits in the corner of the picture and the tile says **Stop recording · 12 min** - it asks before it stops. Any question the app has is shown big on the TV and answered with your remote. On the phone remote's TV mode remote, **Record** is under **More**.
- **Rewind live TV from your phone.** While a live channel plays, Now playing on the phone remote shows how far back the rewind buffer reaches: drag the bar to go back (it shows how far behind live you will be), skip back or forward, and tap **Live** to catch up - it lights up when you are watching live and otherwise shows how far behind you are.
- **A recording stays in sight** at the foot of the sidebar for as long as it runs. Click it to stop - the app asks first, and what has been recorded so far is kept. With two at once you pick which one.
- **A calmer header** in every place: the category's name small at the left, then the filter, the sort and Grid / List on the same line - no big title, nothing on top of anything. The count is in the status line.
- **One way to your sources**: the source picker over the categories is gone - the button at the foot of the sidebar opens the same menu.
- **Recordings in order**: grouped by day - Today, Yesterday, Earlier this week, Last week, then by month - and the sort button puts them oldest first, by name, by channel, longest or largest first. **Unwatched only** hides what you have seen.
- **Local files can be sorted**: by name A–Z or Z–A, last changed, oldest, largest or type - folders always first, and the choice holds for every folder.
- **Phone remote:** More opens on three even tiles - Subtitles and sound, Sleep timer, Record. Record turns red with the time while it records, in TV mode's remote too.

### Fixed

- The **preferred subtitle language** shows by itself again - for every language, also when a stream writes it "sv" or "en-GB" instead of "swe" or "eng". The preferred audio language too.
- **macOS**: no crash any more when a sound device comes or goes - an AirPlay speaker nearby, headphones.
- **Local files** with no folder yet offers **Add folder** again, and no stray "1" in its header.
- The phone remote's **Radio** shows the most voted stations, under your own - it was empty when you had kept none.
- **macOS**: the app no longer hangs for good going into or out of TV mode - or full screen - while something plays.
- Muted, the player no longer makes another program's sound (Spotify) crackle at every channel change.
- Enter plays the row picked in TV, Movies and Series.
- Movies' and Series' cards show a favourite's star and Watch later's clock.
- **Mine → History**: pick several rows with Ctrl or Shift and take them out at once with `Delete`.
- **Mine** follows the autoplay setting: with it off, one click picks and a double click plays, as in every other list.
- A series' page no longer says the first episode is up next while the second one plays.
- Jumping to what plays finds a radio station too, in the list or country you played it from.
- The **Next episode** card can come 10, 20, 30 or 45 seconds before the end, not only whole minutes (**Settings → Playback**).

On the AppImage from 1.2.15 or later, or the Windows or Mac app from 1.4.5? Click **Update now** in the banner - or download 1.6.3 from [dopeiptv.org](https://dopeiptv.org).

## dopeIPTV 1.6.2

**A new look that hangs together: the sidebar is the app - Mine, TV, Movies and Series are places of their own with the same calm header - and dopeIPTV opens where you left off.**

### New and better

- **The sidebar is the app.** Mine, TV, Movies, Series, Guide and Search, then Local files, Radio and Recordings. Home is gone: what it held now lives where you would look for it, and Back, `Esc` or the mouse's back button take you to the place you came from, exactly as you left it.
- **Opens where you left off** - each profile its own place - or always on Mine: **Settings → General → Start**.
- **Mine - everything that is yours on one page.** What you were watching, your favourite channels live now in the order you usually watch at this hour ("Usually watched now"), your favourite films and series, Watch later as a queue you put in order yourself, new episodes of the series you follow, and History as a timeline by day with Play, Continue or Play again. One search and a row of chips filter all of it; your favourite folders are a tab of their own, dragged into order.
- **TV in the new look.** The category's name, how many channels and in which order, Recently watched as small cards, and the channels as rows with what is on, how far it has got and what comes next - **Compact** or **Airy**. Zapping, numbers, favourites, stream health and everything else works as before.
- **Movies and Series as posters or a list**, with **Popular right now** - this week's chart from TMDB, matched to what your own source has - and **Continue**, which for a series is the next episode ("S2 E5 · 18 min left"). A big category opens at once.
- **Every film has its own page**, like a series: the backdrop, the plot, the cast, and one button - Play, or Continue from where you stopped. Opening it never starts anything.
- **Popular right now** can be switched off for Movies or Series in **Settings → Posters & info**.
- **The phone remote's TV mode remote fits your phone**: the d-pad fills the room below Now playing, nothing scrolls, and every key is big enough for a thumb. Subtitles and sound, the sleep timer and recording fold away behind **More**, and the phone remembers how you left it.

### Fixed

- Switching from Grid back to the list left an empty strip to the left of the channels until the app was restarted.
- The header over Movies and Series: the filter, the sort and Grid / List stand in one line at one height (on a Mac the filter sat higher), a long category name ends with "…", and on a narrow window the posters no longer run past the edge.

On the AppImage from 1.2.15 or later, or the Windows or Mac app from 1.4.5? Click **Update now** in the banner - or download 1.6.2 from [dopeiptv.org](https://dopeiptv.org).

## dopeIPTV 1.6.1

**Smooth scrolling while video plays, a calmer guide, a night mode that tames loud adverts and bangs, the visualizer for music channels, and your settings on a new computer in one step.**

### New and better

- **Smooth while something plays.** The picture is now drawn on its own, apart from the rest of the window: scrolling a list, the guide or a series' page stays smooth while a channel, a film or the visualizer plays, and nothing in the window can make the video stutter. If a computer cannot draw it that way, dopeIPTV uses the old way by itself - **Settings → Nerd → Diagnostics** says which.
- **A calmer, denser guide.** Lower rows, so more channels fit; a narrower channel column that shows only the logos when the guide is narrow (drag its edge to set the width); flat cards with a thin progress bar on what is on now; separators like "-= Sweden =-" as headings; and rows without guide data left empty instead of repeating the same sentence. Want a line at the time now? **Settings → Guide** turns it on.
- **Night mode that works.** Loud adverts, explosions and bangs come down while ordinary speech stays exactly as it is - it never makes anything louder - press `N`. It says so however you switch it, and tells you when it could not be turned on for a stream.
- **The visualizer for music and radio channels.** A channel in your playlist with sound but no picture now shows the visualizer instead of a black screen. Two new styles, Vectorscope and Frequencies, make five - in the player's options menu.
- **New computer? Bring your settings from the welcome.** Type the sync code from your other computer, or restore a backup: your profiles and settings come over, and your sources too when the other computer syncs them. Encrypted on your computer before it is sent - dopeiptv.org keeps only bytes it cannot read.
- **The same menu bar everywhere.** File, View, History and Help on Windows, Linux and macOS - About is under Help (on a Mac in the dopeIPTV menu, as before). **Settings → General** now ends with About and the version too.
- **Server mode tells you who has its port** - dopeIPTV's own server started earlier, or another program - and offers the next free one with one click.
- **Local files never freeze the window.** Searching, switching to folders and opening a big folder on a network drive read the disk in the background - the window stays free and the rows fill in as they come. **Rescan** (the button beside the view switch, or `F5`) reads a folder again.
- **One quiet place for what the app is doing.** Loading the guide, scanning a folder, syncing, checking for updates: a short line at the foot of the sidebar says it, with a count when more runs at once - click it for the details and Cancel. Messages only add a small badge; nothing pops up over your lists, and a small card comes up only when something needs your decision. The "Loading programme guide" bar that could stay stuck at the top is gone.
- **Settings open at once** - about four times faster, and on GNOME without the spinning pointer that hung around for half a minute.
- **Pictures load without slowing anything down.** Posters, logos and episode pictures are decoded once, away from the window, so lists, Home and above all a series' page scroll smoothly while they load.
- **The phone remote plays everything the app plays.** Movies and series by category, Continue watching and History, Local files with their search, radio and recordings - and while an episode plays, its season's episodes with the one playing marked, previous and next episode on the buttons. Seek in a film or an episode, pick subtitles and sound, set the sleep timer, and record what plays or the next programme - it asks first, and any question the app would ask comes to the phone. Searching opens the phone's own keyboard, in TV mode too.
- **The phone remote fits small phones** - down to the smallest iPhone SE, upright or on its side - and **follows what plays**: change channel anywhere and the page marks it and scrolls to it.
- **The middle of the window follows what plays.** Start a channel from the phone or the media keys while a series' page shows, and the channel list comes up with it marked - the same for a film, an episode, a file, a station or a recording. Not while you type, in a dialog or in TV mode.

### Fixed

- Keyboard shortcuts sync between your computers again - between computers of the same system, since `⌘` is not `Ctrl`.
- A series' page played an episode on one click. Now one click picks it and a double click or `Enter` plays it, as in the lists - unless **Auto-play preview on selection** is on.
- Connections to your account are counted right: a cast on the source it comes from, a stream the player gave up on no longer, a rewind buffer kept by a recording or a cast only on its own source, and YouTube, Twitch and radio not at all. A cast or a recording after you switched source no longer picks the new source's channel with the same number.
- Searching in a Local files folder finds files in its subfolders too.
- The search boxes say what they search: the list, the categories, or everything.
- TV mode: Back on its Home, twice, leaves it; leaving full screen any way really leaves TV mode, so the next channel no longer brings it back; it opens complete instead of folding in; View > TV mode is checked while it is on; and the window and the docked player come back exactly as they were.
- Leaving full screen put the window back a little lower and smaller each time on Linux.
- A choice in the player's or the guide's right-click menu no longer opens the menu of the row under it.
- A click on the picture shows the controls instead of pausing; the pause button and `Space` pause.
- On a series' page the "Playing" label had its text touching its edges.
- The round pause button over the picture had a square box around it, and the rewind bar came up squeezed for a moment before its normal size.
- On a series' page, the poster's play and pause button under the video now controls the episode that plays.
- Home's back button stays at the top while the page scrolls.
- TV mode: Stop while a dropped channel waited to reconnect now stops it.
- A scheduled recording that comes due while you watch no longer opens a second connection by itself: it records from what plays when it is the same channel, and otherwise waits for a free connection or your OK.
- The same provider account added twice is counted as one account.
- A cast checks the trusted certificate of the source the stream comes from.
- With the phone remote on, a single request could freeze the window for seconds while the guide loaded; it no longer does.
- Smaller things: night mode's notice fits a narrow player, the command palette names the macOS menus right, `N` and `T` get their keys back, and the loading indicator no longer says "Buffering" too early for a playlist address.
- Trakt and Simkl: switching profile quickly while a sign-in was renewed could sign you out.
- The welcome on small screens: the text was cut off and the card ran past the window; the Xtream form also showed fields that belong to other kinds.

On the AppImage from 1.2.15 or later, or the Windows or Mac app from 1.4.5? Click **Update now** in the banner - or download 1.6.1 from [dopeiptv.org](https://dopeiptv.org).
