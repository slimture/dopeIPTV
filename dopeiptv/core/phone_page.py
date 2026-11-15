"""The phone remote's page (core/phone_remote.py serves it): HTML, CSS and
JavaScript in one answer, nothing fetched from anywhere else, so it works
on a home network with no internet.

Mobile first: what plays at the top (the channel, what is on now and
next), the channels and favourites with a search in the middle, and the
controls - channel down and up, play/pause, stop, volume and mute - docked
at the bottom where a thumb reaches. It asks the app what plays every 1.5 s
while it is on screen, and not at all while it is not.

While TV mode is on (the state says so; the button in the header turns it
on and off) the page is a remote instead: a d-pad with OK, Back and
Options, the four colour keys with what each does there, play/pause,
channel and volume rockers, mute and number keys that fold out - each a
TV key (api/tv_key). Or a touchpad (the page remembers the choice): a
swipe is an arrow, a tap OK, a long press Back - the app reads the
gesture (core/phone_remote.gesture), the page only says where the finger
went and for how long.

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

#: The palette keys the page uses, as ui/theme.py names them.
COLOURS = ("bg", "pane", "side", "hover", "sel", "input", "btn", "btn_hover", "border",
           "text", "text2", "muted", "muted2", "ok", "error", "accent", "on_accent",
           "key_red", "key_green", "key_yellow", "key_blue")
#: The words the page shows, as the window passes them (i18n keys' values).
WORDS = ("channels", "favorites", "search", "nothing", "next", "paused", "offline", "expired",
         "none_found", "no_favorites", "play_pause", "stop", "ch_up", "ch_down", "mute", "volume",
         "loading", "tv_mode", "tv_switch", "ok", "up", "down", "left", "right", "back", "options",
         "vol_up", "vol_down", "digits", "touchpad", "touch_hint",
         "key_red", "key_green", "key_yellow", "key_blue")
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
           lang: str = "en", rtl: bool = False) -> tuple[bytes, str]:
    """The page as UTF-8 and its Content-Security-Policy."""
    style = (_CSS.replace("@@DARK@@", css_vars(dark)).replace("@@LIGHT@@", css_vars(light)))
    texts = {k: str(words.get(k) or "") for k in WORDS}
    script = (_JS.replace("@@WORDS@@", _js_literal(texts))
              .replace("@@TOUCH@@", _js_literal({"hold": HOLD_MS, "slop": TAP_SLOP})))
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
    <button class="tvBtn" id="tvBtn" aria-pressed="false">
      <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4.5" width="18" height="12.5" rx="2"/><path d="M8.5 20.5h7M12 17v3.5"/></svg>
      <span id="tvBtnText"></span>
    </button>
  </header>
  <div class="banner" id="banner" role="status" hidden></div>
  <section class="now" id="now" aria-live="polite">
    <div class="logo" id="nowLogo"><span id="nowInitials"></span><img id="nowImg" alt="" hidden></div>
    <div class="nowText">
      <div class="nowTitle" id="nowTitle"></div>
      <div class="nowProg"><span class="chip" id="nowPaused" hidden></span><span id="nowProg"></span></div>
      <div class="bar" id="nowBarWrap" hidden><i id="nowBar"></i></div>
      <div class="nowTimes" id="nowTimes"><span id="nowStart"></span><span id="nowEnd"></span></div>
      <div class="next" id="nowNext"></div>
    </div>
  </section>
  <div id="browse">
  <div class="sticky">
    <div class="tabs" role="tablist">
      <button class="tab" id="tabAll" role="tab" aria-selected="true"></button>
      <button class="tab" id="tabFav" role="tab" aria-selected="false"></button>
    </div>
    <label class="search">
      <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5"/></svg>
      <input id="q" type="search" autocomplete="off" autocapitalize="off" spellcheck="false" enterkeyhint="search">
    </label>
  </div>
  <ul class="list" id="list"></ul>
  <div class="empty" id="empty" hidden></div>
  <div id="more"></div>
  </div>
  <section class="tvr" id="tvr" hidden>
    <div class="pad" id="pad">
      <button class="arr up" id="kUp" data-a="up"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 15l6-6 6 6"/></svg></button>
      <button class="arr left" id="kLeft" data-a="left"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg></button>
      <button class="okk" id="kOk" data-a="ok"></button>
      <button class="arr right" id="kRight" data-a="right"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg></button>
      <button class="arr down" id="kDown" data-a="down"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg></button>
    </div>
    <div class="touch" id="touch" hidden><span id="touchHint"></span></div>
    <div class="pair">
      <button class="pill" id="kBack" data-a="back"><svg class="flip" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/></svg><span id="kBackText"></span></button>
      <button class="pill" id="padMode" aria-pressed="false"><svg viewBox="0 0 24 24" aria-hidden="true"><circle class="fill" cx="12" cy="15" r="2.6"/><path d="M7 15a5 5 0 0 1 10 0M3.8 15a8.2 8.2 0 0 1 16.4 0"/></svg><span id="padModeText"></span></button>
      <button class="pill" id="kMenu" data-a="menu"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg><span id="kMenuText"></span></button>
    </div>
    <div class="colours">
      <button class="ck" id="k_red" data-a="red"><i class="r"></i><span dir="auto"></span></button>
      <button class="ck" id="k_green" data-a="green"><i class="g"></i><span dir="auto"></span></button>
      <button class="ck" id="k_yellow" data-a="yellow"><i class="y"></i><span dir="auto"></span></button>
      <button class="ck" id="k_blue" data-a="blue"><i class="b"></i><span dir="auto"></span></button>
    </div>
    <div class="deck">
      <div class="rock">
        <button id="kVolUp" data-a="vol_up"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 6v12M6 12h12"/></svg></button>
        <span class="mid"><svg viewBox="0 0 24 24" aria-hidden="true"><path class="fill" d="M4 9h4l5-4v14l-5-4H4z"/><path d="M16.5 9.5a3.5 3.5 0 0 1 0 5"/></svg></span>
        <button id="kVolDown" data-a="vol_down"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 12h12"/></svg></button>
      </div>
      <div class="midc">
        <button class="key primary" id="kPlay" data-a="play_pause"><svg viewBox="0 0 24 24" aria-hidden="true"><path class="fill" d="M3.5 5.5v13l8.5-6.5zM14 5.5h2.6v13H14zM18.4 5.5H21v13h-2.6z"/></svg></button>
        <button class="key" id="kMute" data-a="mute"><svg viewBox="0 0 24 24" aria-hidden="true"><path class="fill" d="M4 9h4l5-4v14l-5-4H4z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/></svg></button>
      </div>
      <div class="rock">
        <button id="kChUp" data-a="ch_up"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 15l6-6 6 6"/></svg></button>
        <span class="mid"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4.5" width="18" height="12.5" rx="2"/><path d="M8.5 20.5h7"/></svg></span>
        <button id="kChDown" data-a="ch_down"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg></button>
      </div>
    </div>
    <button class="fold" id="digitsBtn" aria-expanded="false" aria-controls="digits"><svg viewBox="0 0 24 24" aria-hidden="true"><path class="fill" d="M5 4h3v3H5zM10.5 4h3v3h-3zM16 4h3v3h-3zM5 10.5h3v3H5zM10.5 10.5h3v3h-3zM16 10.5h3v3h-3zM5 17h3v3H5zM10.5 17h3v3h-3zM16 17h3v3h-3z"/></svg><span id="digitsText"></span></button>
    <div class="digits" id="digits" hidden>
      <button data-a="digit_1">1</button><button data-a="digit_2">2</button><button data-a="digit_3">3</button>
      <button data-a="digit_4">4</button><button data-a="digit_5">5</button><button data-a="digit_6">6</button>
      <button data-a="digit_7">7</button><button data-a="digit_8">8</button><button data-a="digit_9">9</button>
      <button class="zero" data-a="digit_0">0</button>
    </div>
  </section>
</main>
<footer class="dock" id="dock">
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
</footer>
<script>@@SCRIPT@@</script>
</body>
</html>
"""

_CSS = """
:root{@@DARK@@;color-scheme:dark;--radius:12px;--dock:172px}
@media (prefers-color-scheme: light){:root{@@LIGHT@@;color-scheme:light}}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:var(--bg);color:var(--text);
  font:15px/1.35 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans",Ubuntu,Cantarell,"Helvetica Neue",Arial,sans-serif;
  -webkit-text-size-adjust:100%;text-size-adjust:100%}
body{min-height:100vh;padding-bottom:calc(var(--dock) + env(safe-area-inset-bottom))}
#app{max-width:560px;margin:0 auto;padding:0 16px}
button{font:inherit;color:inherit;border:0;background:none;padding:0;cursor:pointer;touch-action:manipulation}
button:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
svg{width:24px;height:24px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
svg .fill{fill:currentColor;stroke:none}
[hidden]{display:none!important}
[dir=rtl] .flip{transform:scaleX(-1)}
.top{display:flex;align-items:center;gap:10px;padding:calc(12px + env(safe-area-inset-top)) 2px 10px}
.brand{font-weight:700;letter-spacing:.2px}
.source{flex:1;min-width:0;color:var(--muted);font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tvBtn{flex:none;display:flex;align-items:center;gap:6px;height:36px;padding:0 12px;border-radius:999px;
  background:var(--btn);color:var(--text2);font-size:13px;font-weight:600}
.tvBtn svg{width:18px;height:18px}
.tvBtn[aria-pressed=true]{background:var(--accent);color:var(--on-accent)}
.banner{background:var(--pane);color:var(--error);border-radius:var(--radius);padding:12px 14px;margin:0 0 10px;font-size:14px}
.now{display:flex;gap:14px;align-items:flex-start;background:var(--pane);border-radius:var(--radius);padding:14px}
.logo{flex:0 0 56px;height:56px;border-radius:10px;background:var(--btn);display:flex;align-items:center;
  justify-content:center;overflow:hidden;font-weight:700;font-size:18px;color:var(--text2)}
.logo img{width:100%;height:100%;object-fit:contain;padding:6px}
.nowText{min-width:0;flex:1}
.nowTitle{font-size:18px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nowProg{color:var(--text2);margin-top:2px;display:flex;gap:8px;align-items:center;min-width:0}
.nowProg span:last-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chip{flex:none;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.4px;padding:2px 7px;
  border-radius:999px;background:var(--sel);color:var(--text2)}
.bar{height:4px;border-radius:2px;background:var(--btn);margin-top:10px;overflow:hidden}
.bar i{display:block;height:100%;width:0;background:var(--accent);border-radius:2px}
.nowTimes{display:flex;justify-content:space-between;color:var(--muted);font-size:12px;margin-top:4px;
  font-variant-numeric:tabular-nums}
.next{color:var(--muted);font-size:13px;margin-top:6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sticky{position:sticky;top:0;z-index:2;background:var(--bg);padding:12px 0 8px}
.tabs{display:flex;gap:6px;background:var(--pane);border-radius:var(--radius);padding:4px}
.tab{flex:1;min-height:40px;border-radius:9px;color:var(--muted);font-weight:600}
.tab[aria-selected=true]{background:var(--sel);color:var(--text)}
.search{display:flex;align-items:center;gap:8px;margin-top:8px;background:var(--input);border-radius:var(--radius);
  padding:0 12px;min-height:46px;color:var(--muted)}
.search svg{width:20px;height:20px;flex:none}
.search input{flex:1;min-width:0;border:0;background:none;color:var(--text);font:inherit;font-size:16px;height:44px;outline:none}
.search input::placeholder{color:var(--muted)}
.list{list-style:none;margin:0;padding:0;background:var(--pane);border-radius:var(--radius);overflow:hidden}
.list:empty{display:none}
.list li+li .row{box-shadow:inset 0 1px 0 var(--border)}
.row{display:flex;align-items:center;gap:12px;width:100%;min-height:58px;padding:8px 14px;text-align:start;position:relative}
.row:active{background:var(--hover)}
.row.playing{background:var(--sel)}
.row.playing::before{content:"";position:absolute;inset-inline-start:0;top:10px;bottom:10px;width:3px;border-radius:2px;background:var(--accent)}
.row.pending{opacity:.6}
.num{flex:0 0 34px;color:var(--muted);font-size:13px;text-align:end;font-variant-numeric:tabular-nums}
.lg{flex:0 0 40px;height:40px;border-radius:8px;background:var(--btn);display:flex;align-items:center;justify-content:center;
  overflow:hidden;color:var(--text2);font-weight:700;font-size:13px}
.lg img{width:100%;height:100%;object-fit:contain;padding:4px}
.lg.long{font-size:11px;letter-spacing:-.2px}
.logo.long{font-size:15px}
.name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.star{flex:none;color:var(--accent)}
.star svg{width:18px;height:18px}
.empty{color:var(--muted);text-align:center;padding:32px 16px}
#more{height:1px}
.dock{position:fixed;left:0;right:0;bottom:0;z-index:3;background:var(--pane);
  box-shadow:0 -1px 0 var(--border);padding:10px 16px calc(12px + env(safe-area-inset-bottom))}
.dock>*{max-width:528px;margin-left:auto;margin-right:auto}
.mini{color:var(--text2);font-size:13px;text-align:center;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-bottom:6px}
.vol{display:flex;align-items:center;gap:10px}
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
.keys{display:flex;gap:10px;margin-top:8px}
.key{flex:1;height:60px;border-radius:var(--radius);background:var(--btn);display:flex;align-items:center;justify-content:center}
.key:active{background:var(--btn-hover)}
.key svg{width:28px;height:28px}
.key.primary{background:var(--accent);color:var(--on-accent);flex:1.4}
.key.primary:active{filter:brightness(1.1)}
.key[disabled]{opacity:.45}
body.tv{padding-bottom:0}
body.tv .nowTimes,body.tv .next{display:none}
.tvr{padding:14px 0 calc(18px + env(safe-area-inset-bottom))}
.pad,.colours,.deck,.digits{direction:ltr}
.pad{width:min(72vw,272px);aspect-ratio:1;margin:0 auto;border-radius:50%;background:var(--pane);
  box-shadow:inset 0 0 0 1px var(--border);display:grid;grid-template:1fr 1.1fr 1fr/1fr 1.1fr 1fr}
.arr{display:flex;align-items:center;justify-content:center;color:var(--text2);border-radius:50%}
.arr:active{background:var(--hover);color:var(--text)}
.arr svg{width:30px;height:30px}
.up{grid-area:1/2}.left{grid-area:2/1}.right{grid-area:2/3}.down{grid-area:3/2}
.okk{grid-area:2/2;margin:4px;border-radius:50%;background:var(--accent);color:var(--on-accent);font-weight:700;font-size:19px}
.okk:active{filter:brightness(1.1)}
.touch{height:min(72vw,272px);border-radius:24px;background:var(--pane);box-shadow:inset 0 0 0 1px var(--border);
  display:flex;align-items:center;justify-content:center;text-align:center;padding:28px;color:var(--muted);font-size:14px;
  touch-action:none;user-select:none;-webkit-user-select:none;-webkit-touch-callout:none}
.touch.down{background:var(--hover)}
.pair{display:flex;gap:10px;margin-top:16px}
.pill{flex:1;min-width:0;height:48px;border-radius:var(--radius);background:var(--btn);display:flex;align-items:center;
  justify-content:center;gap:7px;font-size:14px;font-weight:600;padding:0 8px}
.pill span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pill svg{width:20px;height:20px;flex:none}
.pill:active{background:var(--btn-hover)}
.pill[aria-pressed=true]{background:var(--sel);color:var(--accent)}
.colours{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:12px}
.ck{min-width:0;height:54px;border-radius:var(--radius);background:var(--btn);display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:7px;padding:0 4px;color:var(--text2);font-size:11.5px;font-weight:600}
.ck:active{background:var(--btn-hover)}
.ck i{display:block;width:30px;height:6px;border-radius:3px}
.ck span{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ck .r{background:var(--key-red)}.ck .g{background:var(--key-green)}.ck .y{background:var(--key-yellow)}.ck .b{background:var(--key-blue)}
.deck{display:grid;grid-template-columns:1fr 1.25fr 1fr;gap:10px;margin-top:12px}
.rock{display:flex;flex-direction:column;border-radius:var(--radius);background:var(--btn);overflow:hidden}
.rock button{height:54px;display:flex;align-items:center;justify-content:center}
.rock button:active{background:var(--btn-hover)}
.rock .mid{height:26px;display:flex;align-items:center;justify-content:center;color:var(--muted)}
.rock .mid svg{width:17px;height:17px}
.midc{display:flex;flex-direction:column;gap:10px}
.midc .key{flex:1;height:auto;margin:0}
.fold{width:100%;height:46px;margin-top:12px;border-radius:var(--radius);background:var(--pane);display:flex;
  align-items:center;justify-content:center;gap:8px;color:var(--text2);font-weight:600;font-size:14px}
.fold svg{width:18px;height:18px}
.fold[aria-expanded=true]{color:var(--text)}
.digits{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:8px}
.digits button{height:54px;border-radius:var(--radius);background:var(--btn);font-size:22px;font-weight:600;
  font-variant-numeric:tabular-nums}
.digits button:active{background:var(--btn-hover)}
.digits .zero{grid-column:2}
"""

_JS = r"""
"use strict";
const W = @@WORDS@@;
const T = @@TOUCH@@;
const $ = (id) => document.getElementById(id);
const LANG = document.documentElement.lang || "en";
// The app's language, in the phone's own variant of it when it has one (en-GB: 24 hours).
const LOCALE = (navigator.languages || []).find((l) => l.toLowerCase().split("-")[0] === LANG.toLowerCase()) || LANG;
const clock = new Intl.DateTimeFormat(LOCALE, {hour: "2-digit", minute: "2-digit"});
let channels = [], byId = new Map(), favs = [], favSet = new Set(), stamp = null;
let tab = "all", query = "", playingId = null, shown = 0, rows = [], st = {}, stAt = 0;
let pollTimer = 0, fails = 0, volUntil = 0, volTimer = 0, gone = false, nowImgId = null, nowSeen = true;
let tvOn = null, touchOn = false;
try { if (localStorage.getItem("tab") === "fav") tab = "fav"; } catch (e) { /* private mode */ }
try { touchOn = localStorage.getItem("touchpad") === "1"; } catch (e) { /* private mode */ }

async function api(path, body) {
  const opt = body === undefined ? {cache: "no-store"}
    : {method: "POST", cache: "no-store", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)};
  const r = await fetch(path, opt);
  if (r.status === 403) { expired(); throw new Error("403"); }
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
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
  if (frac !== null) $("nowBar").style.setProperty("width", (Math.max(0, Math.min(1, frac)) * 100).toFixed(1) + "%");
}

function show(s) {
  st = s; stAt = Date.now();
  setTv(s.tv_mode);
  $("source").textContent = s.source || "";
  const playing = s.state === "playing", stopped = s.state === "stopped";
  hide($("icoPlay"), playing); hide($("icoPause"), !playing);
  const ch = s.channel || null;
  const title = stopped ? W.nothing : (ch ? ch.name : s.title) || "";
  $("nowTitle").textContent = title;
  $("nowPaused").textContent = W.paused; hide($("nowPaused"), s.state !== "paused");
  $("nowProg").textContent = stopped ? "" : (s.now ? s.now.title : (ch ? "" : s.programme || ""));
  $("nowNext").textContent = !stopped && s.next ? W.next + " " + at(s.next.start) + " · " + s.next.title : "";
  const img = $("nowImg"), id = ch && ch.logo ? String(ch.id) : null;
  if (id !== nowImgId) {
    nowImgId = id;
    hide(img, true);
    if (id) img.src = "logo/" + encodeURIComponent(id); else img.removeAttribute("src");
  }
  $("nowInitials").textContent = stopped ? "" : initials(title);
  $("nowLogo").classList.toggle("long", $("nowInitials").textContent.length > 3);
  hide($("nowInitials"), !img.hasAttribute("hidden"));
  tick();
  if (Date.now() > volUntil) setVol(s.volume || 0);
  hide($("icoVol"), s.muted); hide($("icoMute"), !s.muted);
  $("mute").classList.toggle("on", !!s.muted);
  const mini = $("mini");
  mini.textContent = stopped ? "" : title + (s.now ? " · " + s.now.title : "");
  hide(mini, nowSeen || !mini.textContent);
  const pid = ch ? String(ch.id) : null;
  if (pid !== playingId) { playingId = pid; markPlaying(); }
  for (const k of ["chDown", "chUp"]) $(k).disabled = !(s.kind === "live" && !stopped);
}

function tick() {
  const s = st;
  if (!s || s.state === "stopped") { setBar(null); $("nowStart").textContent = $("nowEnd").textContent = ""; return; }
  const now = (s.time || Date.now() / 1000) + (Date.now() - stAt) / 1000;
  if (s.now && s.now.stop > s.now.start) {
    setBar((now - s.now.start) / (s.now.stop - s.now.start));
    $("nowStart").textContent = at(s.now.start); $("nowEnd").textContent = at(s.now.stop);
  } else if (s.duration > 0) {
    const pos = (s.position || 0) + (s.state === "playing" ? (Date.now() - stAt) / 1000 : 0);
    setBar(pos / s.duration);
    $("nowStart").textContent = hms(pos); $("nowEnd").textContent = hms(s.duration);
  } else { setBar(null); $("nowStart").textContent = $("nowEnd").textContent = ""; }
}

function setVol(v) {
  const r = $("vol"); r.value = v;
  r.style.setProperty("--pct", v + "%");
  $("volText").textContent = v + "%";
}

async function poll() {
  clearTimeout(pollTimer);
  if (gone) return;
  try {
    const s = await api("api/state");
    fails = 0; banner(s.note || "");
    show(s);
    if (s.stamp !== stamp) loadChannels(s.stamp);
  } catch (e) {
    if (gone) return;
    fails++; banner(W.offline);
  }
  if (!document.hidden) pollTimer = setTimeout(poll, fails ? Math.min(8000, 1500 * (fails + 1)) : 1500);
}

async function loadChannels(want) {
  stamp = want;
  try {
    const d = await api("api/channels");
    channels = (d.channels || []).map((c) => Object.assign(c, {key: fold(c.name)}));
    byId = new Map(channels.map((c) => [String(c.id), c]));
    favs = (d.favorites || []).map(String).filter((id) => byId.has(id));
    favSet = new Set(favs);
    render();
  } catch (e) { stamp = null; }
}

function filtered() {
  const base = tab === "fav" ? favs.map((id) => byId.get(id)) : channels;
  const q = fold(query.trim());
  if (!q) return base;
  return base.filter((c) => c.key.includes(q) || (c.number != null && String(c.number) === q));
}

function render() {
  rows = filtered(); shown = 0;
  $("list").textContent = "";
  const empty = $("empty");
  empty.textContent = rows.length ? "" : (stamp === null ? W.loading : tab === "fav" && !query ? W.no_favorites : W.none_found);
  hide(empty, rows.length);
  more();
}

const STAR = '<svg viewBox="0 0 24 24" aria-hidden="true"><path class="fill" d="M12 3.6l2.6 5.3 5.8.8-4.2 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.2-4.1 5.8-.8z"/></svg>';

function row(c) {
  const li = document.createElement("li");
  const b = document.createElement("button");
  b.className = "row" + (String(c.id) === playingId ? " playing" : "");
  b.dataset.id = String(c.id);
  const num = document.createElement("span"); num.className = "num"; num.textContent = c.number != null ? c.number : "";
  const lg = document.createElement("span"); lg.textContent = initials(c.name);
  lg.className = lg.textContent.length > 3 ? "lg long" : "lg";
  if (c.logo) {
    const img = new Image(); img.alt = ""; img.loading = "lazy"; img.decoding = "async";
    img.addEventListener("load", () => { lg.textContent = ""; lg.appendChild(img); });
    img.src = "logo/" + encodeURIComponent(String(c.id));
  }
  const name = document.createElement("span"); name.className = "name"; name.textContent = c.name;
  b.append(num, lg, name);
  if (favSet.has(String(c.id))) { const s = document.createElement("span"); s.className = "star"; s.innerHTML = STAR; b.append(s); }
  li.append(b);
  return li;
}

function more() {
  const list = $("list"), end = Math.min(rows.length, shown + 120);
  const frag = document.createDocumentFragment();
  for (; shown < end; shown++) frag.append(row(rows[shown]));
  list.append(frag);
}

function markPlaying() {
  for (const b of document.querySelectorAll(".row")) b.classList.toggle("playing", b.dataset.id === playingId);
}

function soon() { clearTimeout(pollTimer); pollTimer = setTimeout(poll, 350); }

async function send(path, body) {
  if (navigator.vibrate) navigator.vibrate(8);
  try { const r = await api(path, body || {}); soon(); return r; } catch (e) { soon(); return null; }
}

function setTab(t) {
  tab = t;
  $("tabAll").setAttribute("aria-selected", String(t === "all"));
  $("tabFav").setAttribute("aria-selected", String(t === "fav"));
  try { localStorage.setItem("tab", t); } catch (e) { /* private mode */ }
  render();
  window.scrollTo({top: Math.min(window.scrollY, $("list").offsetTop - 120)});
}

function label(id, text) { const b = $(id); b.setAttribute("aria-label", text); b.title = text; }

// TV mode on: the page is a remote; off: the lists and the dock again.
function setTv(on) {
  on = !!on;
  if (on === tvOn) return;
  tvOn = on;
  hide($("browse"), on); hide($("tvr"), !on); hide($("dock"), on);
  document.body.classList.toggle("tv", on);
  $("tvBtn").setAttribute("aria-pressed", String(on));
  if (on) window.scrollTo({top: 0});
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

function initTv() {
  $("tvBtnText").textContent = W.tv_mode; label("tvBtn", W.tv_switch);
  for (const [id, w] of [["kUp", "up"], ["kDown", "down"], ["kLeft", "left"], ["kRight", "right"],
                         ["kVolUp", "vol_up"], ["kVolDown", "vol_down"], ["kPlay", "play_pause"],
                         ["kMute", "mute"], ["kChUp", "ch_up"], ["kChDown", "ch_down"]]) label(id, W[w]);
  $("kOk").textContent = W.ok; $("kBackText").textContent = W.back; $("kMenuText").textContent = W.options;
  $("padModeText").textContent = W.touchpad; $("digitsText").textContent = W.digits;
  $("touchHint").textContent = W.touch_hint;
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

function init() {
  $("tabAll").textContent = W.channels; $("tabFav").textContent = W.favorites;
  $("q").placeholder = W.search; $("q").setAttribute("aria-label", W.search);
  label("toggle", W.play_pause); label("stop", W.stop); label("chUp", W.ch_up); label("chDown", W.ch_down);
  label("mute", W.mute); $("vol").setAttribute("aria-label", W.volume);
  $("nowTitle").textContent = W.loading;
  initTv();
  setTab(tab);
  $("tabAll").addEventListener("click", () => setTab("all"));
  $("tabFav").addEventListener("click", () => setTab("fav"));
  $("q").addEventListener("input", (e) => { query = e.target.value; render(); });
  $("list").addEventListener("click", (e) => {
    const b = e.target.closest(".row");
    if (!b) return;
    b.classList.add("pending");
    send("api/play", {id: b.dataset.id}).then(() => b.classList.remove("pending"));
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
    new IntersectionObserver((es) => { if (es.some((x) => x.isIntersecting) && shown < rows.length) more(); },
                             {rootMargin: "600px"}).observe($("more"));
    new IntersectionObserver((es) => {
      nowSeen = es[0].isIntersecting;
      hide($("mini"), nowSeen || !$("mini").textContent);
    }).observe($("now"));
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); else clearTimeout(pollTimer); });
  setInterval(tick, 1000);
  poll();
}
init();
"""
