"""The phone remote's page (core/phone_remote.py serves it): HTML, CSS and
JavaScript in one answer, nothing fetched from anywhere else, so it works
on a home network with no internet.

Mobile first: what plays at the top (the channel, what is on now and
next), the channels and favourites with a search in the middle, and the
controls - channel down and up, play/pause, stop, volume and mute - docked
at the bottom where a thumb reaches. It asks the app what plays every 1.5 s
while it is on screen, and not at all while it is not.

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

#: The palette keys the page uses, as ui/theme.py names them.
COLOURS = ("bg", "pane", "side", "hover", "sel", "input", "btn", "btn_hover", "border",
           "text", "text2", "muted", "muted2", "ok", "error", "accent", "on_accent")
#: The words the page shows, as the window passes them (i18n keys' values).
WORDS = ("channels", "favorites", "search", "nothing", "next", "paused", "offline", "expired",
         "none_found", "no_favorites", "play_pause", "stop", "ch_up", "ch_down", "mute", "volume",
         "loading")
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
    script = _JS.replace("@@WORDS@@", _js_literal(texts))
    lang = lang if re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?", lang or "") else "en"
    bg_dark = str(dark.get("bg") or "") if _HEX.fullmatch(str(dark.get("bg") or "")) else ""
    bg_light = str(light.get("bg") or "") if _HEX.fullmatch(str(light.get("bg") or "")) else ""
    html = (_HTML.replace("@@LANG@@", lang).replace("@@DIR@@", "rtl" if rtl else "ltr")
            .replace("@@BG_DARK@@", bg_dark).replace("@@BG_LIGHT@@", bg_light)
            .replace("@@STYLE@@", style).replace("@@SCRIPT@@", script))
    csp = ("default-src 'none'; "
           f"script-src {_sha(script)}; style-src {_sha(style)}; "
           "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
           "form-action 'none'; frame-ancestors 'none'")
    return html.encode("utf-8"), csp


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
</main>
<footer class="dock">
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
.top{display:flex;align-items:baseline;gap:10px;padding:calc(14px + env(safe-area-inset-top)) 2px 10px}
.brand{font-weight:700;letter-spacing:.2px}
.source{color:var(--muted);font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
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
"""

_JS = r"""
"use strict";
const W = @@WORDS@@;
const $ = (id) => document.getElementById(id);
const LANG = document.documentElement.lang || "en";
// The app's language, in the phone's own variant of it when it has one (en-GB: 24 hours).
const LOCALE = (navigator.languages || []).find((l) => l.toLowerCase().split("-")[0] === LANG.toLowerCase()) || LANG;
const clock = new Intl.DateTimeFormat(LOCALE, {hour: "2-digit", minute: "2-digit"});
let channels = [], byId = new Map(), favs = [], favSet = new Set(), stamp = null;
let tab = "all", query = "", playingId = null, shown = 0, rows = [], st = {}, stAt = 0;
let pollTimer = 0, fails = 0, volUntil = 0, volTimer = 0, gone = false, nowImgId = null, nowSeen = true;
try { if (localStorage.getItem("tab") === "fav") tab = "fav"; } catch (e) { /* private mode */ }

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
    fails = 0; banner("");
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

function init() {
  $("tabAll").textContent = W.channels; $("tabFav").textContent = W.favorites;
  $("q").placeholder = W.search; $("q").setAttribute("aria-label", W.search);
  label("toggle", W.play_pause); label("stop", W.stop); label("chUp", W.ch_up); label("chDown", W.ch_down);
  label("mute", W.mute); $("vol").setAttribute("aria-label", W.volume);
  $("nowTitle").textContent = W.loading;
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
