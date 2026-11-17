"""The phone remote's page (core/phone_remote.py serves it): HTML, CSS and
JavaScript in one answer, nothing fetched from anywhere else, so it works
on a home network with no internet.

Mobile first: who is watching (a profile's chip, with two profiles or
more: a tap lists the others), what plays at the top (the channel, what is
on now and next), the channels and favourites with a search in the middle, and the
controls - channel down and up, play/pause, stop, volume and mute - docked
at the bottom where a thumb reaches. It asks the app what plays every 1.5 s
while it is on screen (every second for a while after a tap, every 3 s once
nothing has changed for a while), and not at all while it is not; it asks
with the tag of the state it shows, and the same state again is a 304.

It follows what plays: the playing channel's row is marked, and when the
channel changes - on the computer, from the page or from another remote - it
is brought into view, centred between the bar on top and the dock, once the
person's own scrolling has rested a moment (the page opens at its top). A channel the list shown leaves out
(Favourites, a search) is not switched to on its own - the tab and the
search are the person's - but offered under Now playing: "Show in list".

Everything else the app plays is a section of its own in the same row as
Channels and Favourites (a row that scrolls sideways on its own on a small
phone): Movies and Series by category, Recent (Continue watching and
History), Local files by folder with the folder search, Radio (the
favourite stations, a search of the directory) and Recordings - lists the
app serves a page at a time (api/browse), asked with their tags (the same
again is a 304), the next page asked for as the list nears its end, small
pictures the app made (thumb/). A category or folder opened is a place
with a way back; a series opens its Episodes view (api/episodes): the
season chips, Specials last, the season's episodes as compact lines -
number, title, length, watched, how far - the playing one marked and
brought into view, following it to the next. Back - the page's arrow or
the phone's own gesture (the history's state) - goes up one place.

While an episode plays, Now playing names its series with "S2 E5 · Title"
under it and offers its Episodes view, and the dock's channel keys are the
episode before and after it (api/previous, api/next). A title with a
length - a film, an episode, a file, a recording - is sought there: the bar
dragged (the time under the finger shown, one api/seek when it is let go)
and the player's own skip steps, mirrored right to left. A tap on a search
box focuses its field in the tap's own handler, so the phone's keyboard
comes up at once; while TV mode shows its search, the remote has a field
that types into it with the phone's own keyboard (api/tv_text).

Now playing stays compact: what plays' sound and subtitles, the sleep
timer, recording and the next programme are folded behind More (a small
button with a chevron), open or folded as this phone left it. Folded,
nothing that runs is out of sight - a recording's red dot and its time,
the sleep timer's minutes - and each is a way to it (the dot asks to stop,
as Stop always does).

It fits a phone from 320 px wide up, upright or on its side: sizes are
fluid (clamp, min, flex and grid that wrap - the bar's tabs and search, the
dock's volume and keys), every key at least 44 px, the safe areas kept
clear.

While TV mode is on (the state says so; the button in the header turns it
on and off) the page is a remote instead: a d-pad with OK, Back and
Options, the four colour keys with what each does there, play/pause,
channel and volume rockers, mute and number keys that fold out - each a
TV key (api/tv_key). Or a touchpad (the page remembers the choice): a
swipe is an arrow, a tap OK, a long press Back - the app reads the
gesture (core/phone_remote.gesture), the page only says where the finger
went and for how long. The remote fills the screen below Now playing (the
small viewport's height, so the browser's bars never cover a key): the
d-pad takes the room left, sized by container query units - the rockers
beside it, or under it when that room is tall - and on its side the keys
sit beside it; nothing scrolls as it opens, on a phone from 320 x 568 up.
Its sound, sleep timer and Record fold behind a More of their own - Record
asks first as Now playing's does (a long press asks anyway) and gives way,
while a recording runs, to its red dot and time, open or folded. While
something plays there, the whole Now playing card is a button - a pill at its
trailing edge says what it does - that brings the picture back on the TV
(api/tv_key "picture").

Styled as the app is - flat surfaces, hairlines, the accent colour - in the
app's dark palette, or its light one when the phone asks for light. The
colours come from the window (ui/theme.py), as #rrggbb only; the words from
the app's language (i18n.py). The Content-Security-Policy allows exactly
the page's own script and style (by their hashes) and requests to itself.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any

from .phone_remote import HOLD_MS, TAP_SLOP
from .tv_input import TEXT_MAX

#: The palette keys the page uses, as ui/theme.py names them.
COLOURS = ("bg", "pane", "side", "hover", "sel", "input", "btn", "btn_hover", "border",
           "text", "text2", "muted", "muted2", "ok", "error", "rec", "accent", "on_accent",
           "key_red", "key_green", "key_yellow", "key_blue")
#: The words the page shows, as the window passes them (i18n keys' values).
WORDS = ("channels", "favorites", "search", "nothing", "next", "paused", "offline", "expired",
         "none_found", "no_favorites", "play_pause", "stop", "ch_up", "ch_down", "mute", "volume",
         "loading", "tv_mode", "tv_switch", "ok", "up", "down", "left", "right", "back", "options",
         "vol_up", "vol_down", "digits", "touchpad", "touch_hint",
         "key_red", "key_green", "key_yellow", "key_blue",
         "profile", "profile_pin", "tv_picture", "tv_now_playing", "show_in_list",
         "ep_prev", "ep_next", "episodes", "movies", "series", "recent", "local", "radio",
         "recordings", "back_to", "min", "watched", "playing", "search_radio", "search_here",
         "nothing_here", "find", "skip_back", "skip_fwd", "seek", "tv_type",
         "tracks", "audio", "subtitles", "no_audio", "sleep", "off", "minutes", "sleep_left",
         "close", "rec", "rec_stop", "rec_this", "remind", "dont_ask", "cancel", "rec_anyway",
         "rec_cancel", "rec_ask", "rec_stop_ask", "more", "less")
_HEX = re.compile(r"#[0-9A-Fa-f]{6}")


def css_vars(palette: dict[str, Any]) -> str:
    """``--bg:#17171c;...`` for the colours in *palette* that are #rrggbb
    (anything else is left out: nothing but a colour reaches the CSS)."""
    out = []
    for key in COLOURS:
        value = str(palette.get(key) or "")
        if _HEX.fullmatch(value):
            out.append(f"--{key.replace('_', '-')}:{value.lower()}")
    return ";".join(out)


def _js_literal(obj: Any) -> str:
    """JSON that is safe inside a <script>: no < > & at all (no "</script>",
    no "<!--"), no line separators."""
    text = json.dumps(obj, ensure_ascii=False)
    for c, esc in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
                   (" ", "\\u2028"), (" ", "\\u2029")):
        text = text.replace(c, esc)
    return text


def _sha(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode() + "'"


def render(dark: dict[str, Any], light: dict[str, Any], words: dict[str, str],
           lang: str = "en", rtl: bool = False,
           skips: tuple[int, int] = (10, 30),
           sleep_steps: tuple[int, ...] = (15, 30, 45, 60, 90, 120)) -> tuple[bytes, str]:
    """The page as UTF-8 and its Content-Security-Policy. *skips*: the
    player's own skip back and forward, in seconds."""
    style = (_CSS.replace("@@DARK@@", css_vars(dark)).replace("@@LIGHT@@", css_vars(light)))
    texts = {k: str(words.get(k) or "") for k in WORDS}
    script = (_JS.replace("@@WORDS@@", _js_literal(texts))
              .replace("@@TOUCH@@", _js_literal({"hold": HOLD_MS, "slop": TAP_SLOP}))
              .replace("@@SKIP@@", _js_literal({"back": abs(int(skips[0])), "fwd": abs(int(skips[1])),
                                               "text_max": TEXT_MAX,
                                               "sleep": [int(m) for m in sleep_steps if int(m) > 0]})))
    lang = lang if re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?", lang or "") else "en"
    bg_dark = str(dark.get("bg") or "") if _HEX.fullmatch(str(dark.get("bg") or "")) else ""
    bg_light = str(light.get("bg") or "") if _HEX.fullmatch(str(light.get("bg") or "")) else ""
    html = (_HTML.replace("@@LANG@@", lang).replace("@@DIR@@", "rtl" if rtl else "ltr")
            .replace("@@BG_DARK@@", bg_dark).replace("@@BG_LIGHT@@", bg_light)
            .replace("@@APP_NAME@@", APP_NAME)
            .replace("@@STYLE@@", style).replace("@@SCRIPT@@", script))
    csp = ("default-src 'none'; "
           f"script-src {_sha(script)}; style-src {_sha(style)}; "
           "img-src 'self' data:; connect-src 'self'; manifest-src 'self'; base-uri 'none'; "
           "form-action 'none'; frame-ancestors 'none'")
    return html.encode("utf-8"), csp


#: The name under the icon on a phone's home screen, and its window's.
APP_NAME = "dopeIPTV Remote"
#: The home-screen icons the server hands out (see manifest), name -> size.
ICONS = {"icon-180.png": 180, "icon-192.png": 192, "icon-512.png": 512,
         "icon-maskable-512.png": 512}


def manifest(bg: str = "") -> bytes:
    """The web app manifest: "Add to Home screen" on Android (and Chrome
    elsewhere) gets the name, the icons and a window of its own - no
    address bar. Every address is relative, so it stays under the token."""
    colour = bg if _HEX.fullmatch(bg or "") else "#111111"
    data = {
        "name": APP_NAME, "short_name": "dopeIPTV", "start_url": "./", "scope": "./",
        "display": "standalone", "orientation": "portrait",
        "background_color": colour, "theme_color": colour,
        "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png",
                   "purpose": "any"},
                  {"src": "icon-512.png", "sizes": "512x512", "type": "image/png",
                   "purpose": "any"},
                  {"src": "icon-maskable-512.png", "sizes": "512x512",
                   "type": "image/png", "purpose": "maskable"}],
    }
    return json.dumps(data, separators=(",", ":")).encode("utf-8")


_HTML = """<!doctype html>
<html lang="@@LANG@@" dir="@@DIR@@">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark light">
<meta name="theme-color" content="@@BG_DARK@@" media="(prefers-color-scheme: dark)">
<meta name="theme-color" content="@@BG_LIGHT@@" media="(prefers-color-scheme: light)">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="@@APP_NAME@@">
<meta name="application-name" content="@@APP_NAME@@">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="manifest" href="manifest.webmanifest">
<link rel="apple-touch-icon" href="icon-180.png">
<link rel="icon" type="image/png" sizes="192x192" href="icon-192.png">
<meta name="referrer" content="no-referrer">
<link rel="icon" href="data:,">
<title>dopeIPTV</title>
<style>@@STYLE@@</style>
</head>
<body>
<main id="app">
  <header class="top">
    <span class="brand">dopeIPTV</span>
    <span class="source" id="source"></span>
    <button class="pchip" id="pchip" aria-haspopup="menu" aria-expanded="false" hidden><span class="pav" id="pav"></span><span class="pname" id="pname"></span></button>
    <button class="tvBtn" id="tvBtn" aria-pressed="false">
      <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4.5" width="18" height="12.5" rx="2"/><path d="M8.5 20.5h7M12 17v3.5"/></svg>
      <span id="tvBtnText"></span>
    </button>
  </header>
  <div class="pmenu" id="pmenu" role="menu" hidden></div>
  <div class="banner" id="banner" role="status" hidden></div>
  <section class="now" id="now" aria-live="polite">
    <div class="logo" id="nowLogo"><span id="nowInitials"></span><img id="nowImg" alt="" hidden></div>
    <div class="nowText">
      <div class="nowLabel" id="nowLabel" hidden></div>
      <div class="nowTitle" id="nowTitle"></div>
      <div class="nowProg"><span class="chip" id="nowPaused" hidden></span><span id="nowProg"></span></div>
      <div class="seekbar" id="nowBarWrap" hidden><div class="bar"><i id="nowBar"></i></div><span class="knob" id="nowKnob"></span><span class="seekTip" id="seekTip" hidden></span></div>
      <div class="nowTimes" id="nowTimes"><span id="nowStart"></span><span class="skips" id="skips" hidden><button class="skip" id="skipBack"><svg class="flip" viewBox="0 0 24 24" aria-hidden="true"><path d="M5.6 8.6A7.6 7.6 0 1 1 4.4 12.5"/><path d="M5.3 3.9v4.9h4.9"/></svg><b></b></button><button class="skip" id="skipFwd"><svg class="flip" viewBox="0 0 24 24" aria-hidden="true"><path d="M18.4 8.6A7.6 7.6 0 1 0 19.6 12.5"/><path d="M18.7 3.9v4.9h-4.9"/></svg><b></b></button></span><span id="nowEnd"></span></div>
      <div class="next" id="nowNext"></div>
      <div class="nowActs" id="nowActs">
      <button class="showIn" id="showIn" hidden><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h10M4 12h7M4 18h10"/><circle cx="17.5" cy="12" r="3"/></svg><span id="showInText"></span></button>
      <button class="showIn" id="epsBtn" hidden><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M7.5 9h9M7.5 12.5h9M7.5 16h5"/></svg><span id="epsBtnText"></span></button>
      <button class="showIn live rec" id="recLive" hidden><span class="recIcon"><svg viewBox="0 0 24 24" aria-hidden="true"><circle class="fill" cx="12" cy="12" r="6"/></svg></span><span></span></button>
      <button class="showIn live" id="sleepLive" hidden><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19.5 14.5A7.5 7.5 0 0 1 9.5 4.5a7.5 7.5 0 1 0 10 10z"/></svg><span></span></button>
      <button class="more" id="moreBtn" aria-expanded="false" aria-controls="nowMore" hidden><span class="moreIn"><span id="moreText"></span><svg class="caret" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg></span></button>
      </div>
      <div class="nowMore" id="nowMore" hidden>
      <div class="nowSet" id="nowSet">
      <button class="showIn" id="tracksBtn" hidden><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5.5" width="18" height="13" rx="2.5"/><path d="M10.5 10.3a2.2 2.2 0 1 0 0 3.4M17 10.3a2.2 2.2 0 1 0 0 3.4"/></svg><span id="tracksText"></span></button>
      <button class="showIn" id="sleepBtn" hidden><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19.5 14.5A7.5 7.5 0 0 1 9.5 4.5a7.5 7.5 0 1 0 10 10z"/></svg><span id="sleepText"></span></button>
      </div>
      <div class="nowRec" id="nowRec" hidden>
      <button class="showIn rec" id="recBtn" hidden><span class="recIcon" id="recIcon"></span><span id="recText"></span></button>
      <button class="showIn" id="nextBtn" hidden><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 2"/></svg><span id="nextText"></span></button>
      </div>
      </div>
    </div>
    <span class="nowPill" id="nowPill" aria-hidden="true" hidden><svg viewBox="0 0 24 24"><rect x="3" y="4.5" width="18" height="12.5" rx="2"/><path d="M8.5 20.5h7"/><path class="fill" d="M10.2 8.2v4.6l4-2.3z"/></svg><span id="nowPillText"></span></span>
  </section>
  <div id="browse">
  <div class="sticky" id="bar">
    <div class="tabs" id="tabs" role="tablist">
      <button class="tab" id="tabAll" data-s="all" role="tab" aria-selected="true"></button>
      <button class="tab" id="tabFav" data-s="fav" role="tab" aria-selected="false"></button>
      <button class="tab" id="tabVod" data-s="vod" role="tab" aria-selected="false"></button>
      <button class="tab" id="tabSeries" data-s="series" role="tab" aria-selected="false"></button>
      <button class="tab" id="tabRecent" data-s="recent" role="tab" aria-selected="false"></button>
      <button class="tab" id="tabLocal" data-s="local" role="tab" aria-selected="false"></button>
      <button class="tab" id="tabRadio" data-s="radio" role="tab" aria-selected="false"></button>
      <button class="tab" id="tabRec" data-s="rec" role="tab" aria-selected="false"></button>
    </div>
    <div class="crumb" id="crumb" hidden>
      <button class="back" id="crumbBack"><svg class="flip" viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg></button>
      <span id="crumbTitle" dir="auto"></span>
    </div>
    <label class="search" id="searchBox">
      <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5"/></svg>
      <input id="q" type="search" inputmode="search" enterkeyhint="search" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false">
    </label>
  </div>
  <ul class="list" id="list"></ul>
  <ul class="list" id="lib" hidden></ul>
  <div class="empty" id="empty" hidden></div>
  <div class="empty" id="libEmpty" hidden></div>
  <div id="more"></div>
  </div>
  <section class="eps" id="eps" hidden>
    <div class="sticky" id="epsHead">
      <div class="crumb">
        <button class="back" id="epsBack"><svg class="flip" viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg></button>
        <span id="epsTitle" dir="auto"></span>
      </div>
      <div class="tabs" id="seasons" role="tablist" hidden></div>
    </div>
    <ul class="list" id="epList"></ul>
    <div class="empty" id="epsEmpty" hidden></div>
  </section>
  <section class="tvr" id="tvr" hidden>
    <label class="search tvText" id="tvTextBox" hidden>
      <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M9.5 10h.01M13 10h.01M16.5 10h.01M8 14h8"/></svg>
      <input id="tvText" type="search" inputmode="search" enterkeyhint="search" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false">
    </label>
    <div class="stage" id="stage">
      <div class="rock">
        <button id="kVolUp" data-a="vol_up"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 6v12M6 12h12"/></svg></button>
        <span class="mid"><svg viewBox="0 0 24 24" aria-hidden="true"><path class="fill" d="M4 9h4l5-4v14l-5-4H4z"/><path d="M16.5 9.5a3.5 3.5 0 0 1 0 5"/></svg></span>
        <button id="kVolDown" data-a="vol_down"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 12h12"/></svg></button>
      </div>
      <div class="pad" id="pad">
        <button class="arr up" id="kUp" data-a="up"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 15l6-6 6 6"/></svg></button>
        <button class="arr left" id="kLeft" data-a="left"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg></button>
        <button class="okk" id="kOk" data-a="ok"></button>
        <button class="arr right" id="kRight" data-a="right"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg></button>
        <button class="arr down" id="kDown" data-a="down"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg></button>
      </div>
      <div class="touch" id="touch" hidden><span id="touchHint"></span></div>
      <div class="rock">
        <button id="kChUp" data-a="ch_up"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 15l6-6 6 6"/></svg></button>
        <span class="mid"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4.5" width="18" height="12.5" rx="2"/><path d="M8.5 20.5h7"/></svg></span>
        <button id="kChDown" data-a="ch_down"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg></button>
      </div>
    </div>
    <div class="side">
    <div class="tvKeys">
      <button class="pill" id="kBack" data-a="back"><svg class="flip" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/></svg><span id="kBackText"></span></button>
      <button class="pill primary" id="kPlay" data-a="play_pause"><svg viewBox="0 0 24 24" aria-hidden="true"><path class="fill" d="M3.5 5.5v13l8.5-6.5zM14 5.5h2.6v13H14zM18.4 5.5H21v13h-2.6z"/></svg></button>
      <button class="pill" id="kMute" data-a="mute"><svg viewBox="0 0 24 24" aria-hidden="true"><path class="fill" d="M4 9h4l5-4v14l-5-4H4z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/></svg></button>
      <button class="pill" id="kMenu" data-a="menu"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg><span id="kMenuText"></span></button>
    </div>
    <div class="colours">
      <button class="ck" id="k_red" data-a="red"><i class="r"></i><span dir="auto"></span></button>
      <button class="ck" id="k_green" data-a="green"><i class="g"></i><span dir="auto"></span></button>
      <button class="ck" id="k_yellow" data-a="yellow"><i class="y"></i><span dir="auto"></span></button>
      <button class="ck" id="k_blue" data-a="blue"><i class="b"></i><span dir="auto"></span></button>
    </div>
    <div class="folds">
      <button class="fold" id="padMode" aria-pressed="false"><svg viewBox="0 0 24 24" aria-hidden="true"><circle class="fill" cx="12" cy="15" r="2.6"/><path d="M7 15a5 5 0 0 1 10 0M3.8 15a8.2 8.2 0 0 1 16.4 0"/></svg><span id="padModeText"></span></button>
      <button class="fold" id="digitsBtn" aria-expanded="false" aria-controls="digits"><svg viewBox="0 0 24 24" aria-hidden="true"><path class="fill" d="M5 4h3v3H5zM10.5 4h3v3h-3zM16 4h3v3h-3zM5 10.5h3v3H5zM10.5 10.5h3v3h-3zM16 10.5h3v3h-3zM5 17h3v3H5zM10.5 17h3v3h-3zM16 17h3v3h-3z"/></svg><span id="digitsText"></span></button>
      <button class="fold live rec" id="tvRecLive" hidden><span class="recIcon"><svg viewBox="0 0 24 24" aria-hidden="true"><circle class="fill" cx="12" cy="12" r="6"/></svg></span><span></span></button>
      <button class="fold live" id="tvSleepLive" hidden><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19.5 14.5A7.5 7.5 0 0 1 9.5 4.5a7.5 7.5 0 1 0 10 10z"/></svg><span></span></button>
      <button class="fold" id="tvMoreBtn" aria-expanded="false" aria-controls="tvActs" hidden><svg class="caret" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg><span id="tvMoreText"></span></button>
    </div>
    <div class="pair" id="tvActs" hidden>
      <button class="pill" id="tvTracks"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5.5" width="18" height="13" rx="2.5"/><path d="M10.5 10.3a2.2 2.2 0 1 0 0 3.4M17 10.3a2.2 2.2 0 1 0 0 3.4"/></svg><span id="tvTracksText"></span></button>
      <button class="pill" id="tvSleep"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19.5 14.5A7.5 7.5 0 0 1 9.5 4.5a7.5 7.5 0 1 0 10 10z"/></svg><span id="tvSleepText"></span></button>
      <button class="pill" id="tvRec" hidden><span class="recIcon"><svg viewBox="0 0 24 24" aria-hidden="true"><circle class="fill" cx="12" cy="12" r="6"/></svg></span><span id="tvRecText"></span></button>
    </div>
    <div class="digits" id="digits" hidden>
      <button data-a="digit_1">1</button><button data-a="digit_2">2</button><button data-a="digit_3">3</button>
      <button data-a="digit_4">4</button><button data-a="digit_5">5</button><button data-a="digit_6">6</button>
      <button data-a="digit_7">7</button><button data-a="digit_8">8</button><button data-a="digit_9">9</button>
      <button class="zero" data-a="digit_0">0</button>
    </div>
    </div>
  </section>
</main>
<div class="toast" id="toast" role="status" hidden></div>
<div class="scrim" id="scrim" hidden></div>
<div class="sheet" id="sheet" role="dialog" aria-modal="true" aria-labelledby="sheetTitle" hidden>
  <div class="sheetIn">
    <div class="sheetHead"><span id="sheetTitle" dir="auto"></span><button class="back" id="sheetClose"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>
    <div id="sheetBody"></div>
  </div>
</div>
<footer class="dock" id="dock">
  <div class="dockIn">
  <div class="mini" id="mini" hidden></div>
  <div class="vol">
    <button class="icon" id="mute">
      <svg viewBox="0 0 24 24" aria-hidden="true" id="icoVol"><path class="fill" d="M4 9h4l5-4v14l-5-4H4z"/><path d="M16.5 9.5a3.5 3.5 0 0 1 0 5M19 7a7 7 0 0 1 0 10"/></svg>
      <svg viewBox="0 0 24 24" aria-hidden="true" id="icoMute" hidden><path class="fill" d="M4 9h4l5-4v14l-5-4H4z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/></svg>
    </button>
    <input type="range" id="vol" min="0" max="100" step="1" value="0">
    <span class="volText" id="volText"></span>
  </div>
  <div class="keys">
    <button class="key" id="chDown"><svg class="flip" viewBox="0 0 24 24" aria-hidden="true"><path class="fill" d="M6 5h2.4v14H6zM19 5v14l-9.5-7z"/></svg></button>
    <button class="key primary" id="toggle">
      <svg viewBox="0 0 24 24" aria-hidden="true" id="icoPlay"><path class="fill" d="M8 5.5v13l10.5-6.5z"/></svg>
      <svg viewBox="0 0 24 24" aria-hidden="true" id="icoPause" hidden><path class="fill" d="M7 5h3.6v14H7zM13.4 5H17v14h-3.6z"/></svg>
    </button>
    <button class="key" id="stop"><svg viewBox="0 0 24 24" aria-hidden="true"><rect class="fill" x="6.5" y="6.5" width="11" height="11" rx="1.5"/></svg></button>
    <button class="key" id="chUp"><svg class="flip" viewBox="0 0 24 24" aria-hidden="true"><path class="fill" d="M15.6 5H18v14h-2.4zM5 5v14l9.5-7z"/></svg></button>
  </div>
  </div>
</footer>
<script>@@SCRIPT@@</script>
</body>
</html>
"""

_CSS = """
:root{@@DARK@@;color-scheme:dark;--on-error:var(--bg);--radius:12px;--dock:172px;--vh:1vh;--svh:1vh;
  --safe-t:env(safe-area-inset-top,0px);--safe-b:env(safe-area-inset-bottom,0px);
  --gut-l:max(clamp(12px,4vw,16px),env(safe-area-inset-left,0px));
  --gut-r:max(clamp(12px,4vw,16px),env(safe-area-inset-right,0px));
  --key-h:clamp(46px,calc(var(--svh)*6.6),62px);--tg:clamp(6px,calc(var(--svh)*1.5),14px);--kg:clamp(6px,2vw,10px);
  --rw:clamp(46px,13vmin,68px);--rg:clamp(8px,3vmin,16px);--pad-min:150px;
  --pad:clamp(150px,min(52vw,calc(var(--svh)*30)),272px)}
@supports (height:1dvh){:root{--vh:1dvh}}
@supports (height:1svh){:root{--svh:1svh}}
@media (prefers-color-scheme: light){:root{@@LIGHT@@;color-scheme:light;--on-error:var(--on-accent)}}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:var(--bg);color:var(--text);
  font:15px/1.35 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans",Ubuntu,Cantarell,"Helvetica Neue",Arial,sans-serif;
  -webkit-text-size-adjust:100%;text-size-adjust:100%}
body{min-height:100vh;padding-bottom:var(--dock)}
body::before{content:"";position:fixed;left:0;right:0;top:0;height:var(--safe-t);background:var(--bg);z-index:4}
#app{max-width:560px;margin:0 auto;padding:0 var(--gut-r) 0 var(--gut-l)}
button{font:inherit;color:inherit;border:0;background:none;padding:0;cursor:pointer;touch-action:manipulation}
button:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
svg{width:24px;height:24px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
svg .fill{fill:currentColor;stroke:none}
[hidden]{display:none!important}
[dir=rtl] .flip{transform:scaleX(-1)}
.top{display:flex;align-items:center;gap:clamp(6px,2.5vw,10px);min-width:0;
  padding:calc(clamp(6px,calc(var(--vh)*1.6),12px) + var(--safe-t)) 2px clamp(6px,calc(var(--vh)*1.4),10px)}
.brand{flex:none;font-weight:700;letter-spacing:.2px}
.source{flex:1 1 0;min-width:0;color:var(--muted);font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tvBtn{flex:none;display:flex;align-items:center;justify-content:center;gap:6px;min-width:44px;height:44px;padding:0 12px;
  border-radius:999px;background:var(--btn);color:var(--text2);font-size:13px;font-weight:600;white-space:nowrap}
.tvBtn svg{width:18px;height:18px;flex:none}
.tvBtn[aria-pressed=true]{background:var(--accent);color:var(--on-accent)}
.pchip{display:flex;align-items:center;gap:6px;flex:0 1 auto;min-width:44px;min-height:44px;padding:3px 10px 3px 3px;
  border-radius:999px;background:var(--pane);color:var(--text2);font-size:13px;font-weight:600;max-width:40%}
.pchip .pname{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pav{flex:none;width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;
  background:var(--pf,var(--accent));color:var(--on-accent);font-size:12px;font-weight:700}
.pchip .pav{width:38px;height:38px;font-size:13px}
@media (max-width:359.98px){.tvBtn{padding:0}.tvBtn span{display:none}.pchip{padding-inline-end:3px}.pchip .pname{display:none}}
.pmenu{background:var(--pane);border-radius:var(--radius);padding:4px;margin:0 0 10px;display:flex;flex-direction:column}
.pmenu button{display:flex;align-items:center;gap:10px;min-height:48px;padding:6px 10px;border-radius:9px;text-align:start}
.pmenu button[aria-checked=true]{background:var(--sel);color:var(--text)}
.pmenu button:active{background:var(--hover)}
.pmenu .lock{margin-inline-start:auto;color:var(--muted);font-size:12px}
.banner{background:var(--pane);color:var(--error);border-radius:var(--radius);padding:12px 14px;margin:0 0 10px;font-size:14px;
  overflow-wrap:anywhere}
.now{display:flex;gap:clamp(10px,3.5vw,14px);align-items:flex-start;background:var(--pane);border-radius:var(--radius);
  padding:clamp(10px,3.5vw,14px)}
.logo{flex:0 0 clamp(44px,14vw,56px);height:clamp(44px,14vw,56px);border-radius:10px;background:var(--btn);display:flex;
  align-items:center;justify-content:center;overflow:hidden;font-weight:700;font-size:clamp(15px,4.6vw,18px);color:var(--text2)}
.logo img{width:100%;height:100%;object-fit:contain;padding:6px}
.nowText{min-width:0;flex:1}
.nowTitle{font-size:clamp(16px,4.8vw,18px);font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nowProg{color:var(--text2);margin-top:2px;display:flex;gap:8px;align-items:center;min-width:0}
.nowProg span:last-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.now.ep .nowProg span:last-child{white-space:normal;display:-webkit-box;-webkit-box-orient:vertical;
  -webkit-line-clamp:2;overflow-wrap:anywhere}
.chip{flex:none;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.4px;padding:2px 7px;
  border-radius:999px;background:var(--sel);color:var(--text2)}
.seekbar{position:relative;margin-top:10px}
.bar{height:4px;border-radius:2px;background:var(--btn);overflow:hidden}
.bar i{display:block;height:100%;width:0;background:var(--accent);border-radius:2px}
.seekbar.can{margin-top:0;height:44px;display:flex;align-items:center;cursor:pointer;touch-action:none;
  user-select:none;-webkit-user-select:none}
.seekbar.can .bar{flex:1;height:6px;border-radius:3px}
.knob{display:none;position:absolute;top:50%;inset-inline-start:0;width:18px;height:18px;margin-top:-9px;
  margin-inline-start:-9px;border-radius:50%;background:var(--text);pointer-events:none}
.seekbar.can .knob{display:block}
.seekbar.drag .knob{width:24px;height:24px;margin-top:-12px;margin-inline-start:-12px}
.seekbar:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:6px}
.seekTip{position:absolute;bottom:calc(100% - 2px);inset-inline-start:0;transform:translateX(-50%);padding:4px 9px;
  border-radius:8px;background:var(--accent);color:var(--on-accent);font-size:13px;font-weight:700;
  font-variant-numeric:tabular-nums;white-space:nowrap;pointer-events:none;z-index:1}
[dir=rtl] .seekTip{transform:translateX(50%)}
.nowTimes{display:flex;justify-content:space-between;align-items:center;gap:8px;color:var(--muted);font-size:12px;
  margin-top:4px;font-variant-numeric:tabular-nums}
.skips{display:flex;gap:clamp(6px,2.5vw,12px)}
.skip{position:relative;flex:none;width:44px;height:44px;border-radius:50%;background:var(--btn);color:var(--text);
  display:flex;align-items:center;justify-content:center}
.skip:active{background:var(--btn-hover)}
.skip svg{width:30px;height:30px;stroke-width:1.8}
.skip b{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding-top:2px;
  font-size:10px;font-weight:700;letter-spacing:-.2px}
.next{color:var(--muted);font-size:13px;margin-top:6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.showIn{display:inline-flex;align-items:center;gap:7px;max-width:100%;min-height:44px;margin-top:10px;padding:0 14px 0 11px;
  border-radius:999px;background:var(--btn);color:var(--text);font-size:14px;font-weight:600}
.showIn span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.showIn svg{width:18px;height:18px;flex:none;color:var(--accent)}
.nowActs{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:10px}
.nowActs .showIn,.nowSet .showIn{margin-top:0}
.showIn.on{background:var(--sel)}
.showIn.live{font-variant-numeric:tabular-nums}
.showIn.live.rec{background:var(--sel);color:var(--rec)}
.more{flex:none;margin-inline-start:auto;min-width:44px;min-height:44px;display:flex;align-items:center;
  justify-content:center;color:var(--text2);font-size:13px;font-weight:600}
.moreIn{display:flex;align-items:center;gap:4px;height:32px;padding:0 9px 0 12px;border-radius:999px;background:var(--btn)}
.more svg{width:16px;height:16px;flex:none}
.more:active .moreIn{background:var(--btn-hover)}
.more[aria-expanded=true]{color:var(--text)}
.caret{transition:transform .2s}
[aria-expanded=true] .caret{transform:rotate(180deg)}
@media (prefers-reduced-motion: reduce){.caret{transition:none}}
.nowMore{margin-top:10px}
.nowSet{display:flex;flex-wrap:wrap;gap:8px}
.nowRec{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px;padding-top:12px;box-shadow:inset 0 1px 0 var(--border)}
.nowSet[hidden]+.nowRec{margin-top:0;padding-top:0;box-shadow:none}
body.tv .nowMore{display:none}
.recIcon{display:flex;color:var(--rec)}
.recIcon svg,.showIn .recIcon svg{width:18px;height:18px;color:var(--rec)}
.showIn.rec.on{background:var(--sel);color:var(--rec)}
.toast{position:fixed;left:50%;transform:translateX(-50%);bottom:calc(var(--dock) + 8px);z-index:4;max-width:min(92vw,520px);
  padding:10px 16px;border-radius:12px;background:var(--text);color:var(--bg);font-size:14px;font-weight:600;
  text-align:center;overflow-wrap:anywhere}
body.tv .toast{bottom:calc(16px + var(--safe-b))}
.pill.on{background:var(--sel);color:var(--accent)}
body.tv .nowActs{display:none}
.scrim{position:fixed;inset:0;z-index:5;background:var(--bg);opacity:.62}
.sheet{position:fixed;left:0;right:0;bottom:0;z-index:6;max-height:calc(var(--vh)*82);overflow:auto;
  overscroll-behavior:contain;background:var(--pane);border-radius:16px 16px 0 0;box-shadow:0 -1px 0 var(--border);
  padding:6px var(--gut-r) calc(14px + var(--safe-b)) var(--gut-l)}
.sheetIn{max-width:528px;margin:0 auto}
.sheetHead{display:flex;align-items:center;gap:8px;min-height:52px;position:sticky;top:-6px;background:var(--pane);z-index:1}
.sheetHead span{flex:1;min-width:0;font-weight:700;font-size:17px;overflow-wrap:anywhere}
.sheet .grp{color:var(--muted);font-size:12px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;padding:12px 4px 6px}
.opt{display:flex;align-items:center;gap:10px;width:100%;min-height:48px;padding:6px 12px;border-radius:10px;text-align:start}
.opt span{min-width:0;overflow-wrap:anywhere}
.opt:active{background:var(--hover)}
.opt[aria-checked=true]{background:var(--sel);font-weight:600}
.opt .tick{margin-inline-start:auto;flex:none;color:var(--accent)}
.opt .tick svg{width:20px;height:20px}
.opt[disabled]{opacity:.5}
.sheetText{color:var(--text2);padding:2px 4px 10px;overflow-wrap:anywhere;white-space:pre-line}
.chk{display:flex;align-items:center;gap:10px;min-height:44px;padding:2px 4px;color:var(--text2)}
.chk input{width:22px;height:22px;margin:0;accent-color:var(--accent);flex:none}
.sheetBtns{display:flex;flex-wrap:wrap;gap:10px;margin-top:12px}
.sheetBtns button{flex:1 1 120px;min-height:48px;padding:0 14px;border-radius:var(--radius);background:var(--btn);font-weight:700}
.sheetBtns .primary{background:var(--accent);color:var(--on-accent)}
.sheetBtns .danger{background:var(--error);color:var(--on-error)}
.showIn:active{background:var(--btn-hover)}
.now.act{flex-wrap:wrap;row-gap:12px;min-height:48px;cursor:pointer;user-select:none;-webkit-user-select:none;
  touch-action:manipulation}
.now.act .nowText{flex:1 1 180px}
.now.act:active{background:var(--hover)}
.now.act:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.nowLabel{color:var(--text2);font-size:12px;font-weight:700;letter-spacing:.2px;margin-bottom:1px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nowPill{flex:none;max-width:100%;margin-inline-start:auto;align-self:center;display:flex;align-items:center;gap:7px;
  height:44px;padding:0 16px 0 13px;border-radius:999px;background:var(--accent);color:var(--on-accent);
  font-size:14px;font-weight:700;white-space:nowrap}
.nowPill span{overflow:hidden;text-overflow:ellipsis}
.nowPill svg{width:20px;height:20px;flex:none}
.now.act:active .nowPill{filter:brightness(1.1)}
.sticky{position:sticky;top:var(--safe-t);z-index:2;background:var(--bg);display:flex;flex-wrap:wrap;gap:8px;
  padding:clamp(6px,calc(var(--vh)*1.8),12px) 0 8px}
.tabs{flex:1 1 100%;min-width:0;display:flex;gap:4px;background:var(--pane);border-radius:var(--radius);padding:3px;
  overflow:auto hidden;scrollbar-width:none;overscroll-behavior:contain;scroll-padding-inline:3px}
.tabs::-webkit-scrollbar{display:none}
.tabs.fadeEnd{--fade:linear-gradient(to right,black calc(100% - 40px),transparent)}
.tabs.fadeStart{--fade:linear-gradient(to left,black calc(100% - 40px),transparent)}
.tabs.fadeStart.fadeEnd{--fade:linear-gradient(to right,transparent,black 40px,black calc(100% - 40px),transparent)}
[dir=rtl] .tabs.fadeEnd{--fade:linear-gradient(to left,black calc(100% - 40px),transparent)}
[dir=rtl] .tabs.fadeStart{--fade:linear-gradient(to right,black calc(100% - 40px),transparent)}
[dir=rtl] .tabs.fadeStart.fadeEnd{--fade:linear-gradient(to right,transparent,black 40px,black calc(100% - 40px),transparent)}
.tabs.fadeStart,.tabs.fadeEnd{-webkit-mask-image:var(--fade);mask-image:var(--fade)}
.tab{flex:1 0 auto;min-width:44px;min-height:44px;padding:0 12px;border-radius:9px;color:var(--muted);font-weight:600;
  white-space:nowrap}
.tab[aria-selected=true]{background:var(--sel);color:var(--text)}
.crumb{flex:1 1 100%;min-width:0;display:flex;align-items:center;gap:4px;min-height:44px}
.crumb span{flex:1;min-width:0;font-weight:700;font-size:16px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.back{flex:none;width:44px;height:44px;border-radius:50%;display:flex;align-items:center;justify-content:center;
  color:var(--text2);background:var(--pane)}
.back:active{background:var(--btn)}
.search{flex:1 1 100%;min-width:0;display:flex;align-items:center;gap:8px;background:var(--input);border-radius:var(--radius);
  padding:0 12px;min-height:50px;color:var(--muted)}
.search svg{width:20px;height:20px;flex:none}
.search input{flex:1;min-width:0;border:0;background:none;color:var(--text);font:inherit;font-size:16px;height:44px;outline:none}
.search input::placeholder{color:var(--muted)}
.list{list-style:none;margin:0;padding:0;background:var(--pane);border-radius:var(--radius);overflow:hidden}
.list:empty{display:none}
.list li{content-visibility:auto;contain-intrinsic-size:auto 58px}
.list li+li .row{box-shadow:inset 0 1px 0 var(--border)}
.row{display:flex;align-items:center;gap:clamp(8px,3vw,12px);width:100%;min-height:58px;padding:8px clamp(10px,3.5vw,14px);
  text-align:start;position:relative}
.row:active{background:var(--hover)}
.row.playing{background:var(--sel)}
.row.playing::before{content:"";position:absolute;inset-inline-start:0;top:10px;bottom:10px;width:3px;border-radius:2px;background:var(--accent)}
.row.playing .name{font-weight:600}
.row.pending{opacity:.6}
.num{flex:0 0 clamp(26px,8vw,34px);color:var(--muted);font-size:13px;text-align:end;font-variant-numeric:tabular-nums;
  overflow:hidden;white-space:nowrap}
.lg{flex:0 0 40px;height:40px;border-radius:8px;background:var(--btn);display:flex;align-items:center;justify-content:center;
  overflow:hidden;color:var(--text2);font-weight:700;font-size:13px;position:relative}
.lg img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;padding:4px;opacity:0}
.lg.pic img{opacity:1}
.lg.pic span{visibility:hidden}
.lg.long{font-size:11px;letter-spacing:-.2px}
.logo.long{font-size:15px}
.name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.star{flex:none;color:var(--accent)}
.star svg{width:18px;height:18px}
.empty{color:var(--muted);text-align:center;padding:32px 16px}
.list li.head{padding:14px clamp(10px,3.5vw,14px) 6px;color:var(--muted);font-size:12px;font-weight:700;
  letter-spacing:.4px;text-transform:uppercase;contain-intrinsic-size:auto 38px}
.lg.poster{height:56px;border-radius:6px}
.lg.poster img{object-fit:cover;padding:0}
.lg.dir{color:var(--accent)}
.lg.dir svg{width:22px;height:22px}
.txt{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px}
.txt .name{flex:none}
.sub{color:var(--muted);font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pbar{height:3px;border-radius:2px;background:var(--btn);overflow:hidden;margin-top:4px}
.pbar i{display:block;height:100%;width:0;background:var(--accent);border-radius:2px}
.ok{flex:none;color:var(--ok)}
.ok svg,.chev svg{width:20px;height:20px}
.chev{flex:none;color:var(--muted)}
.ep .num{flex-basis:clamp(30px,9vw,40px);font-weight:600}
.row.playing .num{color:var(--accent)}
.eps{padding-bottom:8px}
#more{height:1px}
.dock{position:fixed;left:0;right:0;bottom:0;z-index:3;background:var(--pane);box-shadow:0 -1px 0 var(--border);
  padding:clamp(6px,calc(var(--vh)*1.6),10px) var(--gut-r) calc(clamp(8px,calc(var(--vh)*2),12px) + var(--safe-b)) var(--gut-l)}
.dockIn{max-width:528px;margin:0 auto;display:flex;flex-wrap:wrap;align-items:center;column-gap:16px;
  row-gap:clamp(4px,calc(var(--vh)*1.2),8px)}
.mini{flex:1 0 100%;min-width:0;color:var(--text2);font-size:13px;text-align:center;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.vol{flex:1 1 200px;min-width:0;display:flex;align-items:center;gap:clamp(6px,2.5vw,10px)}
.icon{width:44px;height:44px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:var(--text2);flex:none}
.icon:active{background:var(--btn)}
.icon.on{color:var(--error)}
.volText{flex:0 0 42px;text-align:end;color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums}
input[type=range]{flex:1;min-width:0;height:44px;background:none;-webkit-appearance:none;appearance:none;margin:0;
  --pct:0%}
input[type=range]::-webkit-slider-runnable-track{height:6px;border-radius:3px;
  background:linear-gradient(to right,var(--accent) var(--pct),var(--btn) var(--pct))}
[dir=rtl] input[type=range]::-webkit-slider-runnable-track{
  background:linear-gradient(to left,var(--accent) var(--pct),var(--btn) var(--pct))}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:24px;height:24px;border-radius:50%;margin-top:-9px;
  background:var(--text);border:0}
input[type=range]::-moz-range-track{height:6px;border-radius:3px;background:var(--btn)}
input[type=range]::-moz-range-progress{height:6px;border-radius:3px;background:var(--accent)}
input[type=range]::-moz-range-thumb{width:24px;height:24px;border-radius:50%;background:var(--text);border:0}
.keys{flex:1 1 260px;min-width:0;display:flex;gap:clamp(6px,2.5vw,10px)}
.key{flex:1;min-width:44px;height:clamp(48px,calc(var(--vh)*9),60px);border-radius:var(--radius);background:var(--btn);
  display:flex;align-items:center;justify-content:center}
.key:active{background:var(--btn-hover)}
.key svg{width:28px;height:28px}
.key.primary{background:var(--accent);color:var(--on-accent);flex:1.4}
.key.primary:active{filter:brightness(1.1)}
.key[disabled]{opacity:.45}
body.tv{padding-bottom:0;min-height:0}
body.tv .nowTimes,body.tv .next{display:none}
body.tv #app{display:flex;flex-direction:column;height:calc(var(--svh)*100)}
body.tv #app>*{flex:none}
body.tv #app>.tvr{flex:1 0 auto}
.tvr{display:grid;grid-template:"text" auto "stage" minmax(var(--pad-min),1fr) "side" auto/minmax(0,1fr);
  padding:var(--tg) 0 calc(var(--tg) + var(--safe-b))}
.tvText{grid-area:text;margin-bottom:var(--tg)}
.stage,.pad,.colours,.digits{direction:ltr}
.stage{grid-area:stage;min-width:0;display:flex;flex-wrap:wrap;align-items:center;align-content:center;
  justify-content:center;gap:var(--rg)}
.stage>*{--P:var(--pad)}
@supports (container-type:size){
  .stage{container-type:size}
  .stage>*{--P:clamp(var(--pad-min),min(calc(100cqw - 2*var(--rw) - 2*var(--rg) - 1px),100cqh),340px)}
}
.rock{flex:none;width:var(--rw);height:var(--P);display:flex;flex-direction:column;border-radius:var(--radius);
  background:var(--btn);overflow:hidden}
.rock button{flex:1 1 0;min-width:44px;min-height:44px;display:flex;align-items:center;justify-content:center}
.rock button:active{background:var(--btn-hover)}
.rock .mid{flex:none;height:clamp(18px,calc(var(--P)*.12),32px);display:flex;align-items:center;justify-content:center;
  color:var(--muted)}
.rock .mid svg{width:17px;height:17px}
.pad{flex:none;width:var(--P);height:var(--P);border-radius:50%;background:var(--pane);
  box-shadow:inset 0 0 0 1px var(--border);display:grid;grid-template:1fr 1.1fr 1fr/1fr 1.1fr 1fr}
.arr{display:flex;align-items:center;justify-content:center;color:var(--text2);border-radius:50%}
.arr:active{background:var(--hover);color:var(--text)}
.arr svg{width:clamp(24px,calc(var(--P)*.11),34px);height:clamp(24px,calc(var(--P)*.11),34px)}
.up{grid-area:1/2}.left{grid-area:2/1}.right{grid-area:2/3}.down{grid-area:3/2}
.okk{grid-area:2/2;margin:4px;border-radius:50%;background:var(--accent);color:var(--on-accent);font-weight:700;
  font-size:clamp(16px,calc(var(--P)*.07),21px);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.okk:active{filter:brightness(1.1)}
.touch{flex:1 1 0;min-width:var(--pad-min);max-width:calc(var(--P)*1.6);height:var(--P);border-radius:24px;
  background:var(--pane);box-shadow:inset 0 0 0 1px var(--border);display:flex;align-items:center;justify-content:center;
  text-align:center;padding:clamp(14px,5vw,28px);color:var(--muted);font-size:14px;
  touch-action:none;user-select:none;-webkit-user-select:none;-webkit-touch-callout:none}
.touch.down{background:var(--hover)}
@container (max-aspect-ratio: 6/5){
  .stage>*{--P:clamp(var(--pad-min),min(calc(100cqw - 1px),calc(100cqh - var(--key-h) - var(--rg))),340px)}
  .pad{order:-1;margin-inline:calc((100cqw - var(--P))/2)}
  .touch{order:-1;flex:0 0 100%;max-width:none}
  .rock{width:calc((100cqw - var(--rg))/2 - 1px);height:var(--key-h);flex-direction:row-reverse}
  .rock .mid{height:auto;width:clamp(18px,8cqw,32px)}
}
.side{grid-area:side;min-width:0;display:flex;flex-direction:column;gap:var(--tg);margin-top:var(--tg)}
.tvKeys{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(44px,1fr) minmax(44px,.8fr) minmax(0,1.25fr);
  gap:var(--kg)}
.pair{display:flex;gap:var(--kg)}
.pill{flex:1 1 0;min-width:0;min-height:var(--key-h);padding:6px 3px;border-radius:var(--radius);background:var(--btn);
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;font-size:clamp(11.5px,3.5vw,12.5px);
  font-weight:600}
.pill span,.fold span{max-width:100%;line-height:1.15;text-align:center;overflow-wrap:break-word;
  -webkit-hyphens:auto;hyphens:auto}
.pill svg{width:20px;height:20px;flex:none}
.pill:active{background:var(--btn-hover)}
.pill.primary{background:var(--accent);color:var(--on-accent)}
.pill.primary svg{width:28px;height:28px}
.pill.primary:active{filter:brightness(1.1)}
.colours{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:var(--kg)}
.ck{min-width:0;min-height:var(--key-h);border-radius:var(--radius);background:var(--btn);display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:6px;padding:6px 3px;color:var(--text2);font-size:clamp(10.5px,3.3vw,11.5px);font-weight:600}
.ck:active{background:var(--btn-hover)}
.ck i{display:block;flex:none;width:min(30px,70%);height:6px;border-radius:3px}
.ck span{max-width:100%;line-height:1.1;text-align:center;overflow:hidden;overflow-wrap:anywhere;
  -webkit-hyphens:auto;hyphens:auto;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2}
.ck .r{background:var(--key-red)}.ck .g{background:var(--key-green)}.ck .y{background:var(--key-yellow)}.ck .b{background:var(--key-blue)}
.folds{display:flex;flex-wrap:wrap;gap:var(--kg)}
.fold{flex:1 1 64px;min-width:0;min-height:46px;padding:5px 4px;border-radius:var(--radius);background:var(--pane);
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;color:var(--text2);
  font-size:clamp(11.5px,3.5vw,12.5px);font-weight:600}
.fold svg{width:18px;height:18px;flex:none}
.fold:active{background:var(--hover)}
.fold[aria-expanded=true]{color:var(--text)}
.fold[aria-pressed=true]{background:var(--sel);color:var(--accent)}
.fold.live{flex:0 1 auto;min-width:64px;padding-inline:12px;font-variant-numeric:tabular-nums}
.fold.rec{color:var(--rec)}
.digits{display:grid;grid-template-columns:repeat(3,1fr);gap:var(--kg)}
.digits button{height:var(--key-h);border-radius:var(--radius);background:var(--btn);font-size:22px;font-weight:600;
  font-variant-numeric:tabular-nums}
.digits button:active{background:var(--btn-hover)}
.digits .zero{grid-column:2}
@media (orientation: landscape) and (min-width: 540px){
  body.tv #app{max-width:920px}
  .tvr{grid-template:"text text" auto "stage side" minmax(var(--pad-min),1fr)/
    minmax(calc(2*var(--rw) + 2*var(--rg) + var(--pad-min) + 2px),1fr) minmax(0,clamp(260px,40%,380px));
    column-gap:var(--rg)}
  .side{margin-top:0;align-self:center}
}
@media (max-height: 440px){body.tv .now{padding-block:10px}body.tv .now.act .nowLabel{display:none}}
@media (max-height: 360px){body.tv .now.act .seekbar{display:none}body.tv .now .logo{flex-basis:44px;height:44px}}
"""

_JS = r"""
"use strict";
const W = @@WORDS@@;
const T = @@TOUCH@@;
const SKIP = @@SKIP@@;
const $ = (id) => document.getElementById(id);
const LANG = document.documentElement.lang || "en";
// The app's language, in the phone's own variant of it when it has one (en-GB: 24 hours).
const LOCALE = (navigator.languages || []).find((l) => l.toLowerCase().split("-")[0] === LANG.toLowerCase()) || LANG;
const clock = new Intl.DateTimeFormat(LOCALE, {hour: "2-digit", minute: "2-digit"});
let channels = [], byId = new Map(), favs = [], favSet = new Set(), stamp = null, loading = false;
let tab = "all", query = "", playingId = null, shown = 0, rows = [], st = {}, stAt = 0;
let pollTimer = 0, fails = 0, volUntil = 0, volTimer = 0, gone = false, nowImgId = null, nowSeen = true;
let tvOn = null, touchOn = false, etag = "", same = 0, quickUntil = 0;
// The sections: the channels (all, favourites) on this page, the rest served a page at a time.
const CHANNELS = ["all", "fav"];
const SECTIONS = ["all", "fav", "vod", "series", "recent", "local", "radio", "rec"];
try { const t = localStorage.getItem("tab"); if (SECTIONS.includes(t)) tab = t; } catch (e) { /* private mode */ }
try { touchOn = localStorage.getItem("touchpad") === "1"; } catch (e) { /* private mode */ }

async function api(path, body) {
  const opt = body === undefined ? {cache: "no-store"}
    : {method: "POST", cache: "no-store", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)};
  const r = await fetch(path, opt);
  if (r.status === 403) { expired(); throw new Error("403"); }
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}

// What plays, asked with the tag of the state shown: null when it is the
// same (a 304 - nothing to read or draw again).
async function getState() {
  const r = await fetch("api/state", {cache: "no-store", headers: etag ? {"If-None-Match": etag} : {}});
  if (r.status === 304) return null;
  if (r.status === 403) { expired(); throw new Error("403"); }
  if (!r.ok) throw new Error(String(r.status));
  const s = await r.json();
  etag = r.headers.get("ETag") || "";
  return s;
}

// A list, asked with the tag of the copy the page has: the same again is a
// 304, and that copy.
const kept = new Map();
async function getList(path) {
  const k = kept.get(path);
  const r = await fetch(path, {cache: "no-store", headers: k ? {"If-None-Match": k.tag} : {}});
  if (r.status === 304 && k) return k.data;
  if (r.status === 403) { expired(); throw new Error("403"); }
  if (!r.ok) throw new Error(String(r.status));
  const data = await r.json();
  const tag = r.headers.get("ETag");
  if (tag) {
    kept.delete(path); kept.set(path, {tag, data});
    if (kept.size > 40) kept.delete(kept.keys().next().value);
  }
  return data;
}

// The hidden attribute itself: an SVG element has no .hidden property.
function hide(el, on) { el.toggleAttribute("hidden", !!on); }
function banner(text) { const b = $("banner"); b.textContent = text || ""; hide(b, !text); }
function expired() { gone = true; clearTimeout(pollTimer); banner(W.expired); }

function initials(name) {
  // A short first word is the channel's own short name (TV4, SVT1, BBC); else two letters.
  const words = String(name || "").replace(/[^\p{L}\p{N} ]/gu, " ").trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "";
  if (words[0].length <= 4 && /\p{N}|^\p{Lu}+$/u.test(words[0])) return words[0].toUpperCase();
  return (words.length > 1 ? words[0][0] + words[1][0] : words[0].slice(0, 2)).toUpperCase();
}

function fold(s) { return String(s || "").toLocaleLowerCase(LANG).normalize("NFD").replace(/\p{M}/gu, ""); }

function hms(secs) {
  secs = Math.max(0, Math.floor(secs));
  const h = Math.floor(secs / 3600), m = Math.floor(secs / 60) % 60, s = secs % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : String(m)) + ":" + String(s).padStart(2, "0");
}

function at(ts) { return clock.format(new Date(ts * 1000)); }

function setBar(frac) {
  hide($("nowBarWrap"), frac === null);
  if (frac === null) return;
  const pct = (Math.max(0, Math.min(1, frac)) * 100).toFixed(1) + "%";
  $("nowBar").style.setProperty("width", pct);
  $("nowKnob").style.setProperty("inset-inline-start", pct);
}

// -- Seeking in a film, an episode, a file or a recording -----------------------------
// The bar dragged (the time under the finger shown, one seek sent when it is
// let go) and the player's own skip steps; through the app's seek, which
// clamps to the title's length. The bar stays where it was sent until the
// app's position catches up.
let drag = null, hold = null;

function canSeek() { return !!st.seek && st.duration > 0 && st.state !== "stopped" && !tvOn; }

function playPos() {
  return (st.position || 0) + (st.state === "playing" ? (Date.now() - stAt) / 1000 : 0);
}

function seekable() {
  const on = canSeek(), w = $("nowBarWrap");
  w.classList.toggle("can", on);
  hide($("skips"), !on);
  if (on) {
    w.setAttribute("role", "slider"); w.setAttribute("tabindex", "0");
    w.setAttribute("aria-label", W.seek); w.setAttribute("aria-valuemin", "0");
  } else {
    for (const a of ["role", "tabindex", "aria-label", "aria-valuemin", "aria-valuemax", "aria-valuenow",
                     "aria-valuetext"]) w.removeAttribute(a);
    if (drag) seekEnd(null);
  }
}

function barFrac(e) {
  const r = $("nowBarWrap").getBoundingClientRect();
  const x = document.documentElement.dir === "rtl" ? r.right - e.clientX : e.clientX - r.left;
  return r.width > 0 ? Math.max(0, Math.min(1, x / r.width)) : 0;
}

function dragShow(frac) {
  const t = frac * st.duration, tip = $("seekTip");
  setBar(frac);
  $("nowStart").textContent = hms(t);
  tip.textContent = hms(t);
  tip.style.setProperty("inset-inline-start", (frac * 100).toFixed(2) + "%");
  hide(tip, false);
}

function seekEnd(frac) {
  const d = drag;
  drag = null;
  $("nowBarWrap").classList.remove("drag");
  hide($("seekTip"), true);
  if (frac === null || !d || !canSeek()) { tick(); return; }
  const t = frac * st.duration;
  seekTo(t, {to: Math.round(t * 10) / 10});
}

function seekTo(t, body) {
  t = Math.max(0, Math.min(st.duration, t));
  hold = {to: t, at: Date.now(), until: Date.now() + 2500};
  st.position = t; stAt = Date.now();
  tick();
  send("api/seek", body);
}

function skip(by) { if (canSeek()) seekTo(playPos() + by, {by}); }

function seekBar() {
  const w = $("nowBarWrap");
  w.addEventListener("pointerdown", (e) => {
    if (!canSeek() || (e.pointerType === "mouse" && e.button !== 0)) return;
    e.preventDefault();
    drag = {id: e.pointerId};
    try { w.setPointerCapture(e.pointerId); } catch (x) { /* already up */ }
    w.classList.add("drag");
    dragShow(barFrac(e));
  });
  w.addEventListener("pointermove", (e) => { if (drag && e.pointerId === drag.id) dragShow(barFrac(e)); });
  w.addEventListener("pointerup", (e) => { if (drag && e.pointerId === drag.id) seekEnd(barFrac(e)); });
  w.addEventListener("pointercancel", () => { if (drag) seekEnd(null); });
  w.addEventListener("click", (e) => { if (canSeek()) e.stopPropagation(); });
  w.addEventListener("keydown", (e) => {
    if (!canSeek()) return;
    const rtl = document.documentElement.dir === "rtl";
    const by = {ArrowLeft: rtl ? SKIP.fwd : -SKIP.back, ArrowRight: rtl ? -SKIP.back : SKIP.fwd,
                ArrowDown: -SKIP.back, ArrowUp: SKIP.fwd}[e.key];
    if (by) { e.preventDefault(); skip(by); }
  });
  for (const [id, by, w2] of [["skipBack", -SKIP.back, "skip_back"], ["skipFwd", SKIP.fwd, "skip_fwd"]]) {
    $(id).lastElementChild.textContent = String(Math.abs(by));
    label(id, W[w2]);
    $(id).addEventListener("click", () => skip(by));
  }
}

const HEX = /^#[0-9a-fA-F]{6}$/;
let profiles = [], profileId = null;

function avatar(el, p) {
  el.textContent = p.emoji || p.initials || "";
  if (HEX.test(p.fill || "")) el.style.setProperty("--pf", p.fill); else el.style.removeProperty("--pf");
}

function showProfile(s) {
  const chip = $("pchip");
  const p = s.profile || null;
  hide(chip, !p);
  if (!p) { hide($("pmenu"), true); return; }
  profiles = s.profiles || [];
  if (p.id !== profileId) { profileId = p.id; hide($("pmenu"), true); chip.setAttribute("aria-expanded", "false"); }
  avatar($("pav"), p);
  $("pname").textContent = p.name || "";
  chip.setAttribute("aria-label", W.profile + ": " + (p.name || ""));
}

function profileMenu() {
  const m = $("pmenu"), chip = $("pchip");
  const open = m.hasAttribute("hidden");
  m.textContent = "";
  if (open) {
    for (const p of profiles) {
      const b = document.createElement("button");
      b.setAttribute("role", "menuitemradio");
      b.setAttribute("aria-checked", String(p.id === profileId));
      b.dataset.id = p.id;
      const av = document.createElement("span"); av.className = "pav"; avatar(av, p);
      const name = document.createElement("span"); name.textContent = p.name || "";
      b.append(av, name);
      if (p.pin && p.id !== profileId) { const l = document.createElement("span"); l.className = "lock"; l.textContent = "PIN"; b.append(l); }
      m.append(b);
    }
  }
  hide(m, !open);
  chip.setAttribute("aria-expanded", String(open));
}

function show(s) {
  if (hold) {
    // Sent a moment ago: the bar stays where it was sent until the app is there too.
    const want = hold.to + (s.state === "playing" ? (Date.now() - hold.at) / 1000 : 0);
    if (Date.now() < hold.until && s.duration > 0 && Math.abs((s.position || 0) - want) > 2) s.position = want;
    else hold = null;
  }
  st = s; stAt = Date.now();
  setTv(s.tv_mode);
  sections(s.sections);
  seekable();
  tvEntry(s);
  $("source").textContent = s.source || "";
  showProfile(s);
  const playing = s.state === "playing", stopped = s.state === "stopped";
  hide($("icoPlay"), playing); hide($("icoPause"), !playing);
  const ch = s.channel || null, ep = !stopped && s.episode || null;
  // An episode: its series, and "S2 E5 · Title" under it.
  const title = stopped ? W.nothing : (ch ? ch.name : ep && ep.series ? ep.series : s.title) || "";
  $("nowTitle").textContent = title;
  $("nowPaused").textContent = W.paused; hide($("nowPaused"), s.state !== "paused");
  $("nowProg").textContent = stopped ? "" : (s.now ? s.now.title : ep ? ep.label : (ch ? "" : s.programme || ""));
  $("now").classList.toggle("ep", !!ep);
  $("nowNext").textContent = !stopped && s.next ? W.next + " " + at(s.next.start) + " · " + s.next.title : "";
  const img = $("nowImg");
  const id = ch && ch.logo ? "logo/" + encodeURIComponent(String(ch.id)) : ep && ep.img ? "thumb/" + ep.img : null;
  if (id !== nowImgId) {
    nowImgId = id;
    hide(img, true);
    if (id) img.src = id; else img.removeAttribute("src");
  }
  $("nowInitials").textContent = stopped ? "" : initials(title);
  $("nowLogo").classList.toggle("long", $("nowInitials").textContent.length > 3);
  hide($("nowInitials"), !img.hasAttribute("hidden"));
  nowCard();
  tick();
  if (Date.now() > volUntil) setVol(s.volume || 0);
  hide($("icoVol"), s.muted); hide($("icoMute"), !s.muted);
  $("mute").classList.toggle("on", !!s.muted);
  const mini = $("mini");
  mini.textContent = stopped ? "" : title + (s.now ? " · " + s.now.title : ep ? " · " + ep.label : "");
  hide(mini, nowSeen || !mini.textContent);
  // A channel of another source (failover, the search) is no row of these
  // lists. The page opens at its top with the row marked; a change follows.
  const pid = ch && ch.here !== false ? String(ch.id) : null;
  if (pid !== playingId) {
    playingId = pid; markPlaying();
    follow = pid && opened ? Math.max(follow, 1) : 0;
    followSoon(0);
  }
  opened = true;
  // Channel down and up; while an episode plays, the episode before and after it.
  const live = s.kind === "live" && !stopped;
  $("chDown").disabled = !(live || (ep && ep.prev));
  $("chUp").disabled = !(live || (ep && ep.next));
  label("chDown", ep ? W.ep_prev : W.ch_down); label("chUp", ep ? W.ep_next : W.ch_up);
  epsOffer(ep);
  actsOffer(s);
  recOffer(s);
  folds();
}

function tick() {
  const s = st;
  if (!s || s.state === "stopped") {
    setBar(null); $("nowStart").textContent = $("nowEnd").textContent = ""; recTick(); return;
  }
  if (drag) { epsTick(); return; }                  // the finger's place, not the clock's
  const now = (s.time || Date.now() / 1000) + (Date.now() - stAt) / 1000;
  if (s.now && s.now.stop > s.now.start && !(s.seek && s.duration > 0)) {
    setBar((now - s.now.start) / (s.now.stop - s.now.start));
    $("nowStart").textContent = at(s.now.start); $("nowEnd").textContent = at(s.now.stop);
  } else if (s.duration > 0) {
    const pos = Math.min(s.duration, playPos());
    setBar(pos / s.duration);
    $("nowStart").textContent = hms(pos); $("nowEnd").textContent = hms(s.duration);
    if (canSeek()) {
      const w = $("nowBarWrap");
      w.setAttribute("aria-valuemax", String(Math.round(s.duration)));
      w.setAttribute("aria-valuenow", String(Math.round(pos)));
      w.setAttribute("aria-valuetext", hms(pos) + " / " + hms(s.duration));
    }
  } else { setBar(null); $("nowStart").textContent = $("nowEnd").textContent = ""; }
  epsTick();
  sleepTick();
  recTick();
}

function setVol(v) {
  const r = $("vol"); r.value = v;
  r.style.setProperty("--pct", v + "%");
  $("volText").textContent = v + "%";
}

// Every 1.5 s; every second for a while after a tap; every 3 s once nothing
// has changed for a while; backing off while the app cannot be reached.
function pollDelay() {
  if (fails) return Math.min(8000, 1500 * (fails + 1));
  if (Date.now() < quickUntil) return 1000;
  return same >= 8 ? 3000 : 1500;
}

async function poll() {
  clearTimeout(pollTimer);
  if (gone) return;
  try {
    const s = await getState();
    fails = 0;
    if (s) {
      same = 0; banner(s.note || "");
      show(s);
      if (s.stamp !== stamp) loadChannels(s.stamp);
    } else { same++; banner(st.note || ""); }
  } catch (e) {
    if (gone) return;
    fails++; banner(W.offline);
  }
  if (!document.hidden) pollTimer = setTimeout(poll, pollDelay());
}

async function loadChannels(want) {
  stamp = want; loading = true;
  try {
    const d = await api("api/channels");
    channels = (d.channels || []).map((c) => Object.assign(c, {key: fold(c.name)}));
    byId = new Map(channels.map((c) => [String(c.id), c]));
    favs = (d.favorites || []).map(String).filter((id) => byId.has(id));
    favSet = new Set(favs);
    loading = false;
    render();
  } catch (e) { stamp = null; loading = false; }
}

function filtered() {
  const base = tab === "fav" ? favs.map((id) => byId.get(id)) : channels;
  const q = fold(query.trim());
  if (!q) return base;
  return base.filter((c) => c.key.includes(q) || (c.number != null && String(c.number) === q));
}

function render() {
  if (isLib()) { rows = []; shown = 0; drawn++; return; }        // a section of its own (libLoad)
  rows = filtered(); shown = 0; drawn++;
  $("list").textContent = "";
  const empty = $("empty");
  empty.textContent = rows.length ? "" : (stamp === null ? W.loading : tab === "fav" && !query ? W.no_favorites : W.none_found);
  hide(empty, rows.length);
  more();
  offer();
  if (follow) followSoon(0);
}

const STAR = '<svg viewBox="0 0 24 24" aria-hidden="true"><path class="fill" d="M12 3.6l2.6 5.3 5.8.8-4.2 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.2-4.1 5.8-.8z"/></svg>';

function row(c) {
  const li = document.createElement("li");
  const b = document.createElement("button");
  b.className = "row" + (String(c.id) === playingId ? " playing" : "");
  b.dataset.id = String(c.id);
  const num = document.createElement("span"); num.className = "num"; num.textContent = c.number != null ? c.number : "";
  const lg = document.createElement("span"), ini = document.createElement("span");
  ini.textContent = initials(c.name);
  lg.className = ini.textContent.length > 3 ? "lg long" : "lg";
  lg.append(ini);
  if (c.logo) {
    // In the row from the start (unseen until it loads), so the browser asks
    // for it only as the row nears the screen; none to be had: the initials.
    const img = new Image(); img.alt = ""; img.loading = "lazy"; img.decoding = "async";
    img.addEventListener("load", () => lg.classList.add("pic"));
    img.addEventListener("error", () => img.remove());
    img.src = "logo/" + encodeURIComponent(String(c.id));
    lg.append(img);
  }
  const name = document.createElement("span"); name.className = "name"; name.textContent = c.name;
  b.append(num, lg, name);
  if (favSet.has(String(c.id))) { const s = document.createElement("span"); s.className = "star"; s.innerHTML = STAR; b.append(s); }
  li.append(b);
  return li;
}

function more() { addRows(shown + 120); }

function addRows(upTo) {
  const list = $("list"), end = Math.min(rows.length, upTo);
  const frag = document.createDocumentFragment();
  for (; shown < end; shown++) frag.append(row(rows[shown]));
  list.append(frag);
}

function markPlaying() {
  for (const b of document.querySelectorAll(".row")) b.classList.toggle("playing", b.dataset.id === playingId);
  offer();
}

// Follow what plays: its row marked, and brought into view - centred between
// the bar on top and the dock, smoothly - once the person's own scrolling
// has rested a moment (follow 1), or at once when asked (follow 2). A channel
// the list shown leaves out (Favourites, a search) is offered instead:
// "Show in list" under Now playing switches to it. Nothing here asks the app
// for more than the state it reads anyway.
const REST_MS = 1200;
let follow = 0, followTimer = 0, opened = false, drawn = 0;
let touching = false, touchedAt = 0, autoUntil = 0;

function followSoon(ms) { clearTimeout(followTimer); followTimer = setTimeout(followNow, ms); }

function playingRow() { return playingId && byId.has(playingId) ? byId.get(playingId) : null; }

// "Show in list": the playing channel is in this source's list, not in the one shown.
function offer() {
  const c = playingRow();
  hide($("showIn"), tvOn || isLib() || !c || rows.includes(c));
  nowRow();
}

function followNow() {
  if (!follow || tvOn || document.hidden || loading || stamp === null) return;   // again when that changes
  const c = playingRow(), i = c ? rows.indexOf(c) : -1;
  offer();
  if (i < 0) { follow = 0; return; }
  if (follow === 1) {
    const wait = touching ? REST_MS : touchedAt + REST_MS - Date.now();
    if (wait > 0) { followSoon(wait); return; }
  }
  if (i >= shown) { fillTo(i + 40); return; }
  const li = $("list").children[i];
  follow = 0;
  if (li) bringIntoView(li);
}

// Rows up to *n* drawn a few hundred a frame, then the follow goes on.
function fillTo(n) {
  const gen = drawn;
  const step = () => {
    if (gen !== drawn) return;
    addRows(shown + 600);
    if (shown < Math.min(n, rows.length)) requestAnimationFrame(step); else followSoon(0);
  };
  requestAnimationFrame(step);
}

function bringIntoView(el, head) {
  const bar = head || $("bar"), dock = $("dock");
  const r = el.getBoundingClientRect();
  const top = Math.max(0, bar.getBoundingClientRect().bottom);
  const bottom = dock.hasAttribute("hidden") ? window.innerHeight : dock.getBoundingClientRect().top;
  if (r.top >= top - 1 && r.bottom <= bottom + 1) return;          // in view: the mark is enough
  const stuck = parseFloat(getComputedStyle(bar).top) || 0;
  const mid = (stuck + bar.offsetHeight + bottom) / 2;
  const y = Math.max(0, window.scrollY + r.top + r.height / 2 - mid);
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  autoUntil = Date.now() + (still ? 200 : 1200);
  window.scrollTo({top: y, behavior: still ? "auto" : "smooth"});
}

function showInList() {
  const c = playingRow();
  if (!c) return;
  if (query) { query = ""; $("q").value = ""; }
  if (isLib() || (tab === "fav" && !favSet.has(playingId))) setTab("all"); else render();
  follow = 2; followNow();
}

// The person's own touch, wheel and scrolling (not the follow's own scroll).
function watchHands() {
  const touched = () => { touchedAt = Date.now(); };
  const opt = {passive: true};
  window.addEventListener("touchstart", () => { touching = true; touched(); }, opt);
  for (const t of ["touchend", "touchcancel"]) window.addEventListener(t, () => { touching = false; touched(); }, opt);
  for (const t of ["wheel", "pointerdown", "keydown"]) window.addEventListener(t, touched, opt);
  window.addEventListener("scroll", () => { if (Date.now() > autoUntil) touched(); }, opt);
}

// The list ends above the dock, however tall the dock is on this phone.
function dockRoom() {
  const d = $("dock");
  document.documentElement.style.setProperty("--dock", (d.hasAttribute("hidden") ? 0 : d.offsetHeight + 12) + "px");
}

// -- Movies, Series, Recent, Local files, Radio, Recordings ---------------------------------
// Lists the app serves a page at a time (api/browse, with their tags), a
// stack of places per section - a category, a folder - and Back through
// them, the phone's own back gesture too (the history's state).
const lib = {stack: {}, items: [], next: null, gen: 0, loading: false};
let qTimer = 0;
const FOLDER = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/></svg>';
const CHEV = '<svg class="flip" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>';
const CHECK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';

function isLib() { return !CHANNELS.includes(tab); }
function libAt() { const s = lib.stack[tab]; return s && s.length ? s[s.length - 1] : {p: tab, title: ""}; }
function libDepth() { return (lib.stack[tab] || []).length; }
// Local files' first page lists the folders: there is nothing to search in yet.
function libSearchable() { return !(tab === "local" && libDepth() <= 1); }

let shownSections = "";
function sections(list) {
  const key = (list || []).join(",");
  if (key === shownSections) return;
  shownSections = key;
  for (const b of $("tabs").children) hide(b, !!list && !list.includes(b.dataset.s));
  if (list && !list.includes(tab)) setTab("all");
  edges($("tabs"));
}

// A row of chips that goes on past the screen fades at that edge: there is more.
function edges(el) {
  const max = el.scrollWidth - el.clientWidth, x = Math.abs(el.scrollLeft);
  el.classList.toggle("fadeStart", x > 2);
  el.classList.toggle("fadeEnd", x < max - 2);
}

function crumb() {
  const deep = isLib() && libDepth() > 1;
  hide($("crumb"), !deep);
  $("crumbTitle").textContent = deep ? libAt().title || "" : "";
  hide($("searchBox"), isLib() && !libSearchable());
  // Short enough for the smallest phone; what it searches said in full to a screen reader.
  const full = !isLib() ? W.search : tab === "radio" ? W.search_radio : tab === "local" ? W.search_here : W.find;
  $("q").placeholder = isLib() ? W.find : W.search; $("q").setAttribute("aria-label", full);
}

function libEmpty(text) { const e = $("libEmpty"); e.textContent = text || ""; hide(e, !text); }

async function libLoad(more) {
  if (!isLib()) return;
  if (more && (lib.loading || lib.next === null)) return;
  const at = libAt(), gen = more ? lib.gen : ++lib.gen, o = more ? lib.next : 0;
  const q = libSearchable() ? query.trim() : "";
  lib.loading = true;
  if (!more) { lib.items = []; lib.next = null; $("lib").textContent = ""; libEmpty(W.loading); }
  try {
    const d = await getList("api/browse?p=" + encodeURIComponent(at.p) + (q ? "&q=" + encodeURIComponent(q) : "")
                            + (o ? "&o=" + o : ""));
    if (gen !== lib.gen) return;
    const items = d.items || [];
    lib.items = more ? lib.items.concat(items) : items;
    lib.next = d.next === undefined ? null : d.next;
    if (!at.title && d.title) { at.title = d.title; crumb(); }
    const frag = document.createDocumentFragment();
    for (const it of items) frag.append(libRow(it));
    $("lib").append(frag);
    libEmpty(lib.items.length ? "" : d.locked ? st.note || W.nothing_here : q ? W.none_found : W.nothing_here);
  } catch (e) {
    if (gen === lib.gen && !more) libEmpty(gone ? W.expired : W.offline);
  } finally {
    if (gen === lib.gen) lib.loading = false;
  }
}

function bar(pct) {
  const b = document.createElement("span"); b.className = "pbar";
  const i = document.createElement("i"); i.style.setProperty("width", Math.max(0, Math.min(100, pct)) + "%");
  b.append(i);
  return b;
}

function textBlock(name, sub, pct) {
  const txt = document.createElement("span"); txt.className = "txt";
  const n = document.createElement("span"); n.className = "name"; n.dir = "auto"; n.textContent = name;
  txt.append(n);
  if (sub) { const u = document.createElement("span"); u.className = "sub"; u.dir = "auto"; u.textContent = sub; txt.append(u); }
  if (pct) txt.append(bar(pct));
  return txt;
}

function mark(html, cls) { const m = document.createElement("span"); m.className = cls; m.innerHTML = html; return m; }

function libRow(it) {
  const li = document.createElement("li");
  if (it.k === "head") { li.className = "head"; li.textContent = it.name || ""; return li; }
  const b = document.createElement("button");
  b.className = "row";
  b.dataset.k = it.k || "";
  b.dataset.name = it.name || "";
  for (const f of ["p", "ref", "sid", "id", "state"]) if (it[f]) b.dataset[f] = it[f];
  const lg = document.createElement("span");
  if (it.k === "dir") { lg.className = "lg dir"; lg.innerHTML = FOLDER; }
  else if (it.k === "job") { lg.className = "lg dir" + (it.state === "rec" ? " recIcon" : ""); lg.innerHTML = it.state === "rec" ? RECDOT : CLOCK; }
  else {
    const ini = document.createElement("span"); ini.textContent = initials(it.name);
    lg.className = "lg" + (it.img && tab !== "radio" ? " poster" : "") + (ini.textContent.length > 3 ? " long" : "");
    lg.append(ini);
    if (it.img && it.ref) {
      // Asked for only as the row nears the screen: a small copy the app made.
      const img = new Image(); img.alt = ""; img.loading = "lazy"; img.decoding = "async";
      img.addEventListener("load", () => lg.classList.add("pic"));
      img.addEventListener("error", () => img.remove());
      img.src = "thumb/" + it.ref;
      lg.append(img);
    }
  }
  b.append(lg, textBlock(it.name || "", it.sub || "", it.pct || 0));
  if (it.w) b.append(mark(CHECK, "ok"));
  else if (it.k === "dir" || it.k === "series") b.append(mark(CHEV, "chev"));
  li.append(b);
  return li;
}

function libPush(p, title) {
  (lib.stack[tab] = lib.stack[tab] || [{p: tab, title: ""}]).push({p, title});
  query = ""; $("q").value = "";
  try { history.pushState({at: tab}, ""); } catch (e) { /* no history */ }
  crumb(); libLoad(false);
  window.scrollTo({top: Math.min(window.scrollY, Math.max(0, $("lib").offsetTop - 120))});
}

function libPop() {
  const s = lib.stack[tab];
  if (!isLib() || !s || s.length <= 1) return false;
  s.pop(); query = ""; $("q").value = "";
  crumb(); libLoad(false);
  return true;
}

function libPressed(e) {
  const b = e.target.closest(".row");
  if (!b) return;
  const k = b.dataset.k;
  if (k === "dir") libPush(b.dataset.p, b.dataset.name);
  else if (k === "series") openEps(b.dataset.sid, b.dataset.ref || "", b.dataset.name);
  else if (k === "job") jobSheet(b.dataset.id, b.dataset.state, b.dataset.name);
  else if (k === "play" && b.dataset.ref) {
    b.classList.add("pending");
    send("api/play_ref", {ref: b.dataset.ref}).then(() => b.classList.remove("pending"));
  }
}

// -- Episodes: a series' seasons and episodes, the playing one marked ---------------------
const eps = {open: false, sid: "", ref: "", v: "", playing: "", data: null, season: 0, gen: 0};

function epsEmpty(text) { const e = $("epsEmpty"); e.textContent = text || ""; hide(e, !text); }

// Now playing offers the view while an episode plays; an open one follows it.
function epsOffer(ep) {
  hide($("epsBtn"), !!tvOn || !ep || !ep.sid);
  nowRow();
  if (!eps.open || !ep || ep.sid !== eps.sid) return;
  if (ep.v !== eps.v || ep.key !== eps.playing) loadEps(false);
}

function openEps(sid, ref, name) {
  if (!sid) return;
  Object.assign(eps, {open: true, sid: String(sid), ref: ref || "", v: "", playing: "", data: null, season: -1});
  $("epsTitle").textContent = name || "";
  $("epList").textContent = ""; $("seasons").textContent = ""; hide($("seasons"), true);
  epsEmpty(W.loading);
  hide($("browse"), true); hide($("eps"), !!tvOn);
  try { history.pushState({eps: 1}, ""); } catch (e) { /* no history */ }
  window.scrollTo({top: 0});
  loadEps(true);
}

function closeEps() {
  if (!eps.open) return;
  eps.open = false; eps.gen++;
  hide($("eps"), true); hide($("browse"), !!tvOn);
  dockRoom();
  if (follow) followSoon(0);
}

function epsPlaying() {
  const ep = st.state !== "stopped" && st.episode;
  return ep && ep.sid === eps.sid ? ep.key : "";
}

async function loadEps(first) {
  const gen = ++eps.gen;
  const ep = st.state !== "stopped" && st.episode && st.episode.sid === eps.sid ? st.episode : null;
  const v = ep ? ep.v : "", was = eps.playing;
  // Asked once per version: while the view loads, a poll asks nothing again.
  eps.v = v; eps.playing = ep ? ep.key : was;
  try {
    const d = await getList("api/episodes?sid=" + encodeURIComponent(eps.sid) + (eps.ref ? "&ref=" + eps.ref : "")
                            + (v ? "&v=" + v : ""));
    if (gen !== eps.gen || !eps.open) return;
    eps.data = d;
    eps.playing = epsPlaying() || d.playing || "";
    if (d.series) $("epsTitle").textContent = d.series;
    const seasons = d.seasons || [];
    // Opened on the playing episode's season (else the next one's); a change
    // of episode takes the view along to its season.
    const playingAt = seasons.findIndex((s) => (s.episodes || []).some((e) => e.key === eps.playing));
    if (first || eps.season < 0 || eps.season >= seasons.length) eps.season = d.open || 0;
    else if (eps.playing !== was && playingAt >= 0) eps.season = playingAt;
    drawSeasons();
    drawEps(first || eps.playing !== was);
  } catch (e) {
    if (gen === eps.gen) { eps.v = ""; if (first) epsEmpty(gone ? W.expired : W.offline); }
  }
}

function drawSeasons() {
  const box = $("seasons"), seasons = (eps.data && eps.data.seasons) || [];
  box.textContent = "";
  hide(box, seasons.length < 2);
  seasons.forEach((s, i) => {
    const b = document.createElement("button");
    b.className = "tab"; b.setAttribute("role", "tab"); b.dataset.i = String(i);
    b.setAttribute("aria-selected", String(i === eps.season));
    b.textContent = s.label || "";
    box.append(b);
  });
  const on = box.querySelector('[aria-selected="true"]');
  if (on && on.scrollIntoView) on.scrollIntoView({block: "nearest", inline: "nearest"});
  edges(box);
}

function epRow(e) {
  const li = document.createElement("li");
  const b = document.createElement("button");
  const now = e.key === eps.playing;
  b.className = "row ep" + (now ? " playing" : "");
  b.dataset.ref = e.ref || ""; b.dataset.key = e.key || "";
  const num = document.createElement("span"); num.className = "num"; num.textContent = e.n || "";
  const sub = [e.min ? W.min.replace("{n}", e.min) : "", now ? W.playing : e.watched ? W.watched : ""]
    .filter(Boolean).join(" · ");
  const txt = textBlock(e.title || e.n || "", sub, now ? 0 : e.pct || 0);
  if (now) txt.append(bar(0));
  b.append(num, txt);
  if (e.watched && !now) b.append(mark(CHECK, "ok"));
  li.append(b);
  return li;
}

function drawEps(bring) {
  const list = $("epList"), seasons = (eps.data && eps.data.seasons) || [];
  const season = seasons[eps.season] || {episodes: []};
  list.textContent = "";
  const frag = document.createDocumentFragment();
  for (const e of season.episodes || []) frag.append(epRow(e));
  list.append(frag);
  epsEmpty(seasons.length ? "" : eps.data && eps.data.locked ? st.note || W.nothing_here : W.nothing_here);
  epsTick();
  if (bring) {
    const at = list.querySelector(".row.playing");
    if (at) requestAnimationFrame(() => bringIntoView(at, $("epsHead")));
  }
}

// The playing episode's bar, from what the state says of where it is.
function epsTick() {
  if (!eps.open || !st.duration) return;
  const i = $("epList").querySelector(".row.playing .pbar i");
  if (!i) return;
  const pos = (st.position || 0) + (st.state === "playing" ? (Date.now() - stAt) / 1000 : 0);
  i.style.setProperty("width", Math.max(0, Math.min(100, 100 * pos / st.duration)).toFixed(1) + "%");
}

function epsPressed(e) {
  const b = e.target.closest(".row");
  if (!b || !b.dataset.ref) return;
  b.classList.add("pending");
  send("api/play_ref", {ref: b.dataset.ref}).then(() => b.classList.remove("pending"));
}

// -- Sheets over the page: subtitles and sound, the sleep timer -----------------------------
// A sheet is a list to pick from or a question; Back (its ✕, the phone's own
// gesture) closes it first.
let sheet = null, skipPop = false;

function openSheet(kind, title) {
  if (!sheet) { try { history.pushState({sheet: 1}, ""); } catch (e) { /* no history */ } }
  sheet = {kind};
  hide($("toast"), true);
  $("sheetTitle").textContent = title;
  $("sheetBody").textContent = "";
  hide($("sheet"), false); hide($("scrim"), false);
  return $("sheetBody");
}

function closeSheet(fromHistory) {
  if (!sheet) return;
  const was = sheet;
  sheet = null;
  hide($("sheet"), true); hide($("scrim"), true);
  if (was.onClose) was.onClose();
  if (!fromHistory) { skipPop = true; try { history.back(); } catch (e) { skipPop = false; } }
}

function sheetOptions(body, groups, pick) {
  groups.forEach((g, gi) => {
    if (g.head) { const h = document.createElement("div"); h.className = "grp"; h.textContent = g.head; body.append(h); }
    g.items.forEach((it, ii) => {
      const b = document.createElement("button");
      b.className = "opt"; b.setAttribute("role", "menuitemradio");
      b.setAttribute("aria-checked", String(!!it.on));
      b.disabled = it.ok === false;
      const t = document.createElement("span"); t.dir = "auto"; t.textContent = it.label;
      b.append(t);
      if (it.on) b.append(mark(CHECK, "tick"));
      b.addEventListener("click", () => pick(gi, ii, it));
      body.append(b);
    });
  });
}

// What plays' audio tracks and subtitles, as the player's own menu lists
// them - read when the sheet opens, and again when the state says they changed.
async function loadTracks() {
  let d;
  try { d = await api("api/tracks"); } catch (e) { return; }
  if (!sheet || sheet.kind !== "tracks") return;
  sheet.stamp = d.stamp; sheet.seen = st.tracks;
  const body = $("sheetBody");
  body.textContent = "";
  const audio = (d.audio || []).length ? d.audio : [{label: W.no_audio, ok: false}];
  sheetOptions(body, [{head: W.audio, items: audio}, {head: W.subtitles, items: d.subs || []}], async (gi, ii) => {
    const stamp = sheet && sheet.stamp;
    await send("api/track", {stamp, kind: gi ? "subs" : "audio", i: ii});
    if (sheet && sheet.kind === "tracks") loadTracks();
  });
}

function openTracks() {
  if (!st.state || st.state === "stopped") return;
  openSheet("tracks", W.tracks).textContent = W.loading;
  loadTracks();
}

// The player's own sleep timer, as TV mode's Options sets it.
function sleepLeft() {
  if (!st.sleep_at || st.state === "stopped") return 0;
  const now = (st.time || Date.now() / 1000) + (Date.now() - stAt) / 1000;
  return Math.max(0, st.sleep_at - now);
}

function sleepText() {
  const left = sleepLeft();
  return left > 0 ? W.sleep_left.replace("{n}", String(Math.max(1, Math.ceil(left / 60)))) : W.sleep;
}

function openSleep() {
  if (!st.state || st.state === "stopped") return;
  const on = sleepLeft() > 0;
  const body = openSheet("sleep", on ? W.sleep + " · " + sleepText() : W.sleep);
  const items = [{label: W.off, on: !on, m: 0}].concat(SKIP.sleep.map((m) => ({label: W.minutes.replace("{n}", m), m})));
  sheetOptions(body, [{items}], (gi, ii, it) => {
    send("api/sleep", {minutes: it.m});
    closeSheet();
  });
}

function sleepTick() {
  const text = sleepText(), left = sleepLeft(), on = left > 0;
  for (const id of ["sleepText", "tvSleepText"]) if ($(id).textContent !== text) $(id).textContent = text;
  for (const id of ["sleepBtn", "tvSleep"]) $(id).classList.toggle("on", on);
  // Folded away, its minutes stay on the card.
  const mins = on ? W.minutes.replace("{n}", String(Math.max(1, Math.ceil(left / 60)))) : "";
  for (const k of ["now", "tv"]) {
    const b = $(FOLDS[k].sleep), was = !b.hasAttribute("hidden");
    hide(b, !on || foldOpen(k));
    if (b.lastElementChild.textContent !== mins) { b.lastElementChild.textContent = mins; b.setAttribute("aria-label", text); }
    if (k === "now" && was === b.hasAttribute("hidden")) nowRow();
  }
}

// Now playing's secondary row (and TV mode's remote): what plays' tracks and
// the sleep timer while something plays.
function actsOffer(s) {
  const playing = !!s.state && s.state !== "stopped";
  hide($("tracksBtn"), !playing); hide($("sleepBtn"), !playing);
  if (sheet && sheet.kind === "tracks" && s.tracks !== sheet.seen) loadTracks();
  if (sheet && !playing && (sheet.kind === "tracks" || sheet.kind === "sleep")) closeSheet();
  sleepTick();
}

// -- More: the card kept compact ------------------------------------------------------------
// What plays' sound and subtitles, the sleep timer, recording and the next
// programme folded behind one button under Now playing (in TV mode's remote:
// the sound, the sleep timer and Record), open or folded as this phone left it.
// Folded, nothing that runs is out of sight: a recording's red dot and time and
// the sleep timer's minutes stay on the card, each a way to it. TV mode's fold
// has no Stop of its own: its dot shows a recording, open or folded.
const FOLDS = {
  now: {btn: "moreBtn", body: "nowMore", text: "moreText", key: "more", rec: "recLive", sleep: "sleepLive"},
  tv: {btn: "tvMoreBtn", body: "tvActs", text: "tvMoreText", key: "tv_more", rec: "tvRecLive", sleep: "tvSleepLive"},
};
const unfolded = {now: false, tv: false};
for (const k of ["now", "tv"]) {
  try { unfolded[k] = localStorage.getItem(FOLDS[k].key) === "1"; } catch (e) { /* private mode */ }
}

function isShown(id) { return !$(id).hasAttribute("hidden"); }
function foldOpen(k) { return isShown(FOLDS[k].body); }

// Now playing's row of buttons only while one of them shows.
function nowRow() {
  const row = $("nowActs");
  hide(row, ![...row.children].some((b) => !b.hasAttribute("hidden")));
}

function folds() {
  const playing = !!st.state && st.state !== "stopped", recording = !!(st.rec && st.rec.id);
  const has = {now: ["tracksBtn", "sleepBtn", "recBtn", "nextBtn"].some(isShown), tv: playing};
  for (const k of ["now", "tv"]) {
    const f = FOLDS[k], open = has[k] && unfolded[k];
    hide($(f.btn), !has[k]);
    hide($(f.body), !open);
    $(f.btn).setAttribute("aria-expanded", String(open));
    $(f.text).textContent = open ? W.less : W.more;
    hide($(f.rec), !recording || (open && k === "now"));
  }
  hide($("nowSet"), !isShown("tracksBtn") && !isShown("sleepBtn"));
  sleepTick(); recTick();
  nowRow();
}

function foldToggle(k) {
  unfolded[k] = !foldOpen(k);
  try { localStorage.setItem(FOLDS[k].key, unfolded[k] ? "1" : "0"); } catch (e) { /* private mode */ }
  folds();
}

function initFolds() {
  for (const k of ["now", "tv"]) {
    const f = FOLDS[k];
    $(f.btn).addEventListener("click", () => foldToggle(k));
    // The dot stops the recording, asked as always - it never starts one.
    $(f.rec).addEventListener("click", () => { if (st.rec && st.rec.id) stopAsk(st.rec.id, st.rec.title); });
    $(f.sleep).addEventListener("click", openSleep);
  }
  folds();
}

function initSheets() {
  $("tracksText").textContent = W.tracks; $("tvTracksText").textContent = W.tracks;
  label("sheetClose", W.close);
  $("sheetClose").addEventListener("click", () => closeSheet());
  $("scrim").addEventListener("click", () => closeSheet());
  for (const id of ["tracksBtn", "tvTracks"]) $(id).addEventListener("click", openTracks);
  for (const id of ["sleepBtn", "tvSleep"]) $(id).addEventListener("click", openSleep);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && sheet) closeSheet(); });
}

// -- Recording ----------------------------------------------------------------------------------
// Record what plays (from the stream that plays - no connection of its own),
// asked first unless that was turned off for every phone (state.rec_ask); a
// long press asks again whatever was chosen. Stopping always asks. The
// channel's next programme recorded or reminded. A question the app asks in
// any of it - one connection too many, a box's tuners - comes here and is
// answered here; nothing it asks about starts without that answer.
const RECDOT = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle class="fill" cx="12" cy="12" r="6"/></svg>';
const STOPSQ = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect class="fill" x="6.5" y="6.5" width="11" height="11" rx="1.5"/></svg>';
const CLOCK = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 2"/></svg>';
let lastAsking = null, askShown = "", toastTimer = 0;

function toast(text) {
  const t = $("toast");
  t.textContent = text || "";
  hide(t, !text);
  clearTimeout(toastTimer);
  if (text) toastTimer = setTimeout(() => hide(t, true), 6000);
}

function sheetText(body, text) {
  const p = document.createElement("div"); p.className = "sheetText"; p.dir = "auto"; p.textContent = text;
  body.append(p);
}

function sheetButtons(body, buttons) {
  const row = document.createElement("div"); row.className = "sheetBtns";
  for (const [label, kind, act] of buttons) {
    const b = document.createElement("button");
    b.className = kind || ""; b.textContent = label;
    b.addEventListener("click", act);
    row.append(b);
  }
  body.append(row);
}

function sheetCheck(body, label, on) {
  const l = document.createElement("label"); l.className = "chk";
  const c = document.createElement("input"); c.type = "checkbox"; c.checked = !!on;
  const t = document.createElement("span"); t.textContent = label;
  l.append(c, t); body.append(l);
  return c;
}

// The app's own question, in its words with its choices; a choice sends the
// request again with the answer - only then does it go ahead.
function askSheet(ask, path, body) {
  if (!ask || !ask.q) return;
  askShown = ask.q;
  const b = openSheet("ask", ask.title || "");
  sheetText(b, ask.text || "");
  const dont = ask.dont ? sheetCheck(b, W.dont_ask, false) : null;
  sheetButtons(b, (ask.choices || []).map((c, i) => [c.label, c.kind === "primary" ? "primary" : c.kind === "danger" ? "danger" : "", () => {
    closeSheet();
    const answers = (ask.answers || []).concat([{q: ask.q, i, dont: !!(dont && dont.checked)}]);
    send(path, Object.assign({}, body || {}, {answers}));
  }]));
}

function playingName() { return (st.channel && st.channel.name) || st.title || ""; }

function recNow(dont) { send("api/rec", dont ? {dont: true} : {}); }

function recAsk() {
  const b = openSheet("rec", W.rec_ask.replace("{title}", playingName()));
  const dont = sheetCheck(b, W.dont_ask, !st.rec_ask);
  sheetButtons(b, [[W.rec, "primary", () => {
    closeSheet();
    if (!dont.checked && !st.rec_ask) send("api/rec_ask", {ask: true});
    recNow(dont.checked);
  }], [W.cancel, "", () => {
    closeSheet();
    if (!dont.checked && !st.rec_ask) send("api/rec_ask", {ask: true});      // asked again from now on
  }]]);
}

function stopAsk(id, title) {
  const b = openSheet("rec", W.rec_stop_ask.replace("{title}", title || ""));
  sheetButtons(b, [[W.rec_stop, "danger", () => { closeSheet(); send("api/rec_stop", {id}); }],
                   [W.cancel, "", () => closeSheet()]]);
}

function recPressed() {
  if (st.rec && st.rec.id) { stopAsk(st.rec.id, st.rec.title); return; }
  if (st.rec_ask) recAsk(); else recNow(false);
}

function nextSheet() {
  const n = st.next;
  if (!n) return;
  const b = openSheet("next", n.title || W.next);
  sheetText(b, at(n.start) + "–" + at(n.stop) + (playingName() ? " · " + playingName() : ""));
  sheetButtons(b, [[W.rec_this, "primary", () => { closeSheet(); send("api/rec_next", {start: n.start, what: "record"}); }],
                   [W.remind, "", () => { closeSheet(); send("api/rec_next", {start: n.start, what: "remind"}); }]]);
}

function jobSheet(id, state, title) {
  if (state === "rec") { stopAsk(id, title); return; }
  const b = openSheet("job", title || "");
  const buttons = [];
  if (state === "held") buttons.push([W.rec_anyway, "primary", () => { closeSheet(); send("api/rec_go", {id}); }]);
  buttons.push([W.rec_cancel, "danger", () => { closeSheet(); send("api/rec_stop", {id}); }]);
  buttons.push([W.close, "", () => closeSheet()]);
  sheetButtons(b, buttons);
}

// Now playing's own row for it, apart from the others: Record or Stop with
// its time, and the next programme. TV mode's remote: Record in its fold,
// gone while a recording runs (its dot says it, and stops it).
function recOffer(s) {
  const live = s.kind === "live" && s.state !== "stopped" && !!s.channel;
  const running = !!(s.rec && s.rec.id);
  hide($("recBtn"), !(live && s.can_rec) && !running);
  hide($("tvRec"), !(live && s.can_rec) || running);
  hide($("nextBtn"), !live || !s.next);
  $("recBtn").classList.toggle("on", running);
  $("recIcon").innerHTML = running ? STOPSQ : RECDOT;
  hide($("nowRec"), $("recBtn").hasAttribute("hidden") && $("nextBtn").hasAttribute("hidden"));
  $("nextText").textContent = s.next ? W.next + " · " + (s.next.title || "") : "";
  recTick();
  if (s.ask && s.ask.q !== askShown && lastAsking && Date.now() - lastAsking.at < 60000) askSheet(s.ask, lastAsking.path, lastAsking.body);
}

function recTick() {
  const r = st.rec;
  let text = W.rec, time = "";
  if (r && r.id) {
    const now = (st.time || Date.now() / 1000) + (Date.now() - stAt) / 1000;
    time = hms(Math.max(0, now - (r.start || now)));
    text = W.rec_stop + " · " + time;
  }
  if ($("recText").textContent !== text) $("recText").textContent = text;
  // Folded away, the red dot and the time stay on the card.
  for (const k of ["now", "tv"]) {
    const b = $(FOLDS[k].rec);
    if (b.lastElementChild.textContent !== time) { b.lastElementChild.textContent = time; b.setAttribute("aria-label", text); }
  }
}

function initRec() {
  $("tvRecText").textContent = W.rec;
  // Now playing's Record and TV mode's remote's: one press, asked as set; a long press asks anyway.
  for (const id of ["recBtn", "tvRec"]) {
    const b = $(id);
    let held = 0, long = false;
    b.addEventListener("pointerdown", () => {
      long = false; clearTimeout(held);
      held = setTimeout(() => { long = true; if (!(st.rec && st.rec.id)) recAsk(); }, T.hold);
    });
    for (const t of ["pointerup", "pointercancel", "pointerleave"]) b.addEventListener(t, () => clearTimeout(held));
    b.addEventListener("click", () => { if (long) { long = false; return; } recPressed(); });
    b.addEventListener("contextmenu", (e) => e.preventDefault());
  }
  $("nextBtn").addEventListener("click", nextSheet);
}

// Back - the page's arrows and the phone's own gesture: a sheet first, then
// the Episodes view, then a section's place before.
function goneBack() {
  if (skipPop) { skipPop = false; return; }
  if (sheet) { closeSheet(true); return; }
  if (eps.open) { closeEps(); return; }
  libPop();
}

function soon() { clearTimeout(pollTimer); pollTimer = setTimeout(poll, 350); }

// What may start a stream or a recording: the app's question in it comes back here.
const ASKING = ["api/play", "api/play_ref", "api/next", "api/previous", "api/toggle", "api/rec", "api/rec_next",
                "api/rec_go"];

async function send(path, body) {
  if (navigator.vibrate) navigator.vibrate(8);
  quickUntil = Date.now() + 10000;
  if (ASKING.includes(path)) lastAsking = {path, body: body || {}, at: Date.now()};
  try {
    const r = await api(path, body || {});
    soon();
    if (r && r.ask) askSheet(r.ask, path, body);
    if (r && r.note) toast(r.note);
    return r;
  } catch (e) { soon(); return null; }
}

function setTab(t) {
  if (!SECTIONS.includes(t)) t = "all";
  const was = tab;
  tab = t;
  for (const b of $("tabs").children) b.setAttribute("aria-selected", String(b.dataset.s === t));
  try { localStorage.setItem("tab", t); } catch (e) { /* private mode */ }
  if (was !== t && (isLib() || !CHANNELS.includes(was))) { query = ""; $("q").value = ""; }
  const on = $("tabs").querySelector('[aria-selected="true"]');
  if (on && on.scrollIntoView) on.scrollIntoView({block: "nearest", inline: "nearest"});
  hide($("list"), isLib()); hide($("lib"), !isLib()); hide($("empty"), isLib() || !$("empty").textContent);
  if (isLib()) {
    if (!lib.stack[t]) lib.stack[t] = [{p: t, title: ""}];
    crumb(); libLoad(false);
  } else { hide($("libEmpty"), true); crumb(); render(); }
  const list = isLib() ? $("lib") : $("list");
  window.scrollTo({top: Math.min(window.scrollY, Math.max(0, list.offsetTop - 120))});
}

function label(id, text) { const b = $(id); b.setAttribute("aria-label", text); b.title = text; }

// TV mode on and something playing: the whole Now playing card is a button
// that brings the picture back on the TV. Else the card as it always is.
function nowCard() {
  const n = $("now"), on = !!tvOn && !!st.state && st.state !== "stopped";
  n.classList.toggle("act", on);
  hide($("nowLabel"), !on); hide($("nowPill"), !on);
  if (!on) { for (const a of ["role", "tabindex", "aria-label"]) n.removeAttribute(a); return; }
  const what = [$("nowTitle").textContent, $("nowProg").textContent].filter(Boolean).join(" · ");
  n.setAttribute("role", "button"); n.setAttribute("tabindex", "0");
  n.setAttribute("aria-label", W.tv_picture + (what ? ": " + what : ""));
}

function nowPressed() { if ($("now").classList.contains("act")) send("api/tv_key", {action: "picture"}); }

// TV mode on: the page is a remote; off: the lists and the dock again.
function setTv(on) {
  on = !!on;
  if (on === tvOn) return;
  tvOn = on;
  hide($("browse"), on || eps.open); hide($("eps"), on || !eps.open); hide($("tvr"), !on); hide($("dock"), on);
  document.body.classList.toggle("tv", on);
  $("tvBtn").setAttribute("aria-pressed", String(on));
  nowCard(); dockRoom(); offer(); epsOffer(st.state !== "stopped" && st.episode || null); seekable();
  if (on) window.scrollTo({top: 0});
  else if (follow) followSoon(0);
}

function setTouch(on) {
  touchOn = on;
  hide($("pad"), on); hide($("touch"), !on);
  $("padMode").setAttribute("aria-pressed", String(on));
  try { localStorage.setItem("touchpad", on ? "1" : "0"); } catch (e) { /* private mode */ }
}

// One touch is one key. The app reads it (a swipe an arrow, a tap OK) from
// where the finger went and how long it stayed; held still, Back at once.
function touchpad() {
  const t = $("touch");
  let p = null, held = 0;
  const end = () => { clearTimeout(held); t.classList.remove("down"); };
  const touch = (dx, dy, ms) => send("api/touch", {dx: Math.round(dx), dy: Math.round(dy), ms: Math.round(ms)});
  t.addEventListener("pointerdown", (e) => {
    p = {x: e.clientX, y: e.clientY, t: Date.now()};
    try { t.setPointerCapture(e.pointerId); } catch (x) { /* already up */ }
    t.classList.add("down");
    clearTimeout(held);
    held = setTimeout(() => { if (p) { p = null; end(); touch(0, 0, T.hold); } }, T.hold);
  });
  t.addEventListener("pointermove", (e) => {
    if (p && Math.max(Math.abs(e.clientX - p.x), Math.abs(e.clientY - p.y)) > T.slop) clearTimeout(held);
  });
  t.addEventListener("pointerup", (e) => {
    end();
    if (p) { const q = p; p = null; touch(e.clientX - q.x, e.clientY - q.y, Date.now() - q.t); }
  });
  t.addEventListener("pointercancel", () => { p = null; end(); });
  t.addEventListener("contextmenu", (e) => e.preventDefault());
}

// TV mode's search on the TV: typed on the phone's own keyboard instead of
// walking the one on screen with the arrows. What is typed goes to the TV a
// moment after the last key (api/tv_text); what the TV shows comes back with
// the state, and never over what is being typed here.
let tvTextTimer = 0, tvTextSent = null;

function tvEntry(s) {
  const box = $("tvTextBox"), input = $("tvText");
  const on = !!s.tv_mode && typeof s.tv_entry === "string";
  hide(box, !on);
  if (!on) { tvTextSent = null; return; }
  if (document.activeElement !== input && s.tv_entry !== input.value
      && (tvTextSent === null || s.tv_entry === tvTextSent)) input.value = s.tv_entry;
}

function tvTextSend() {
  clearTimeout(tvTextTimer);
  const text = $("tvText").value.slice(0, SKIP.text_max);
  if (text === tvTextSent) return;
  tvTextSent = text;
  send("api/tv_text", {text});
}

function initTv() {
  const tvText = $("tvText");
  tvText.maxLength = SKIP.text_max;
  tvText.placeholder = W.tv_type; tvText.setAttribute("aria-label", W.tv_type);
  tvText.addEventListener("input", () => { clearTimeout(tvTextTimer); tvTextTimer = setTimeout(tvTextSend, 250); });
  tvText.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); tvTextSend(); tvText.blur(); } });
  tvText.addEventListener("blur", tvTextSend);
  focusOnTap($("tvTextBox"), tvText);
  $("tvBtnText").textContent = W.tv_mode; label("tvBtn", W.tv_switch);
  for (const [id, w] of [["kUp", "up"], ["kDown", "down"], ["kLeft", "left"], ["kRight", "right"],
                         ["kVolUp", "vol_up"], ["kVolDown", "vol_down"], ["kPlay", "play_pause"],
                         ["kMute", "mute"], ["kChUp", "ch_up"], ["kChDown", "ch_down"]]) label(id, W[w]);
  $("kOk").textContent = W.ok; $("kBackText").textContent = W.back; $("kMenuText").textContent = W.options;
  $("padModeText").textContent = W.touchpad; $("digitsText").textContent = W.digits;
  $("touchHint").textContent = W.touch_hint;
  $("nowLabel").textContent = W.tv_now_playing; $("nowPillText").textContent = W.tv_picture;
  $("now").addEventListener("click", nowPressed);
  $("now").addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && $("now").classList.contains("act")) { e.preventDefault(); nowPressed(); }
  });
  for (const c of ["red", "green", "yellow", "blue"]) $("k_" + c).lastElementChild.textContent = W["key_" + c];
  $("tvr").addEventListener("click", (e) => {
    const b = e.target.closest("[data-a]");
    if (b) send("api/tv_key", {action: b.dataset.a});
  });
  $("tvBtn").addEventListener("click", async () => {
    const r = await send("api/tv", {mode: tvOn ? "off" : "on"});
    if (r && "tv_mode" in r) setTv(r.tv_mode);
  });
  $("padMode").addEventListener("click", () => setTouch(!touchOn));
  $("digitsBtn").addEventListener("click", () => {
    const open = $("digits").hasAttribute("hidden");
    hide($("digits"), !open); $("digitsBtn").setAttribute("aria-expanded", String(open));
  });
  touchpad(); setTouch(touchOn); setTv(false);
}

// A tap anywhere on a search box focuses its field in the tap's own handler:
// iOS opens the keyboard only for a focus made there, never after a wait.
function focusOnTap(box, input) {
  box.addEventListener("click", (e) => { if (e.target !== input) input.focus(); });
}

function init() {
  for (const [id, w] of [["tabAll", "channels"], ["tabFav", "favorites"], ["tabVod", "movies"],
                         ["tabSeries", "series"], ["tabRecent", "recent"], ["tabLocal", "local"],
                         ["tabRadio", "radio"], ["tabRec", "recordings"]]) $(id).textContent = W[w];
  $("q").placeholder = W.search; $("q").setAttribute("aria-label", W.search);
  focusOnTap($("searchBox"), $("q"));
  $("q").addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    if (isLib()) { clearTimeout(qTimer); libLoad(false); }
    $("q").blur();                                    // the keyboard goes; the results stay
  });
  seekBar();
  label("crumbBack", W.back_to); label("epsBack", W.back_to);
  $("epsBtnText").textContent = W.episodes;
  initSheets();
  initRec();
  initFolds();
  label("toggle", W.play_pause); label("stop", W.stop); label("chUp", W.ch_up); label("chDown", W.ch_down);
  label("mute", W.mute); $("vol").setAttribute("aria-label", W.volume);
  $("nowTitle").textContent = W.loading;
  $("showInText").textContent = W.show_in_list;
  initTv();
  setTab(tab);
  $("tabs").addEventListener("click", (e) => {
    const b = e.target.closest(".tab");
    if (b && b.dataset.s) setTab(b.dataset.s);
  });
  $("q").addEventListener("input", (e) => {
    query = e.target.value;
    if (!isLib()) { render(); return; }
    clearTimeout(qTimer);
    qTimer = setTimeout(() => libLoad(false), tab === "radio" || tab === "local" ? 600 : 250);
  });
  $("lib").addEventListener("click", libPressed);
  $("epList").addEventListener("click", epsPressed);
  $("seasons").addEventListener("click", (e) => {
    const b = e.target.closest(".tab");
    if (!b) return;
    eps.season = Number(b.dataset.i) || 0;
    drawSeasons(); drawEps(false);
    window.scrollTo({top: Math.min(window.scrollY, Math.max(0, $("epList").offsetTop - 160))});
  });
  $("epsBtn").addEventListener("click", () => {
    const ep = st.episode;
    if (ep && ep.sid) openEps(ep.sid, "", ep.series);
  });
  for (const id of ["crumbBack", "epsBack"]) $(id).addEventListener("click", () => history.back());
  try { history.replaceState({top: 1}, ""); } catch (e) { /* no history */ }
  window.addEventListener("popstate", goneBack);
  $("showIn").addEventListener("click", showInList);
  $("list").addEventListener("click", (e) => {
    const b = e.target.closest(".row");
    if (!b) return;
    b.classList.add("pending");
    send("api/play", {id: b.dataset.id}).then(() => b.classList.remove("pending"));
  });
  $("pchip").addEventListener("click", profileMenu);
  $("pmenu").addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    const p = profiles.find((x) => x.id === b.dataset.id);
    hide($("pmenu"), true); $("pchip").setAttribute("aria-expanded", "false");
    if (!p || p.id === profileId) return;
    if (p.pin) { banner(W.profile_pin); return; }
    const r = await send("api/profile", {id: p.id});
    if (!r) banner(W.profile_pin);
  });
  $("toggle").addEventListener("click", () => send("api/toggle"));
  $("stop").addEventListener("click", () => send("api/stop"));
  $("chUp").addEventListener("click", () => send("api/next"));
  $("chDown").addEventListener("click", () => send("api/previous"));
  $("mute").addEventListener("click", () => send("api/mute"));
  $("vol").addEventListener("input", (e) => {
    const v = Number(e.target.value);
    setVol(v); volUntil = Date.now() + 2000;
    clearTimeout(volTimer);
    volTimer = setTimeout(() => send("api/volume", {value: v}), 120);
  });
  $("nowImg").addEventListener("load", () => { hide($("nowImg"), false); hide($("nowInitials"), true); });
  $("nowImg").addEventListener("error", () => { hide($("nowImg"), true); hide($("nowInitials"), false); });
  if ("IntersectionObserver" in window) {
    new IntersectionObserver((es) => {
      if (!es.some((x) => x.isIntersecting)) return;
      if (isLib()) libLoad(true); else if (shown < rows.length) more();
    }, {rootMargin: "600px"}).observe($("more"));
    new IntersectionObserver((es) => {
      nowSeen = es[0].isIntersecting;
      hide($("mini"), nowSeen || !$("mini").textContent);
    }).observe($("now"));
  }
  if ("ResizeObserver" in window) new ResizeObserver(dockRoom).observe($("dock"));
  window.addEventListener("resize", dockRoom);
  for (const id of ["tabs", "seasons"]) {
    const row = $(id);
    row.addEventListener("scroll", () => edges(row), {passive: true});
    window.addEventListener("resize", () => edges(row));
  }
  watchHands(); dockRoom(); edges($("tabs"));
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) { poll(); if (follow) followSoon(0); } else clearTimeout(pollTimer);
  });
  setInterval(tick, 1000);
  poll();
}
init();
"""
