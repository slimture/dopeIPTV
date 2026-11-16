"""Server mode's page in a browser (services/home_server.py serves it at
``/?token=…``): what it is doing - the tuners in use, who watches what from
which address, the provider connection of each channel - the addresses to
give Plex, Jellyfin, VLC or Kodi, and its few settings: port, channels at
once, the sources and categories it serves; and how to start it with the
computer.

HTML, CSS and JavaScript in one answer, nothing from anywhere else (a home
network may have no internet), styled as the app is - flat surfaces,
hairlines, filled controls - in its dark palette, or its light one when the
browser asks for light; the colours as #rrggbb only, the words in the
app's language (i18n.py). The page sends the token from its own address
as a header with each request; the Content-Security-Policy allows exactly
its own script and style (by their hashes) and requests to itself, like
the phone remote's (core/phone_page.py).
"""

from __future__ import annotations

import re
from typing import Any

from .phone_page import _js_literal, _sha, css_vars

#: The words the page shows (i18n keys, as the server passes them).
WORDS = ("srv_page_title", "srv_in_use", "srv_watching", "srv_nobody", "srv_shared",
         "srv_state_streaming", "srv_state_connecting", "srv_state_reconnecting",
         "srv_addresses", "srv_addr_tuner", "srv_addr_tuner_hint", "srv_addr_guide",
         "srv_addr_playlist", "problem_copy", "problem_copied", "settings_title",
         "nerd_remote_port", "srv_port_hint", "srv_tuners", "srv_tuners_hint", "srv_sources",
         "srv_sources_hint", "srv_all_categories", "common_save", "srv_saved", "srv_start",
         "srv_start_hint", "srv_offline", "srv_no_sources", "common_loading", "srv_minutes",
         "private_menu")
_HEX = re.compile(r"#[0-9A-Fa-f]{6}")


def render(dark: dict[str, Any], light: dict[str, Any], words: dict[str, str],
           lang: str = "en", rtl: bool = False) -> tuple[bytes, str]:
    """The page as UTF-8 and its Content-Security-Policy."""
    style = _CSS.replace("@@DARK@@", css_vars(dark)).replace("@@LIGHT@@", css_vars(light))
    script = _JS.replace("@@WORDS@@", _js_literal({k: str(words.get(k) or "") for k in WORDS}))
    lang = lang if re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?", lang or "") else "en"
    bg = str(dark.get("bg") or "")
    html = (_HTML.replace("@@LANG@@", lang).replace("@@DIR@@", "rtl" if rtl else "ltr")
            .replace("@@BG@@", bg if _HEX.fullmatch(bg) else "")
            .replace("@@STYLE@@", style).replace("@@SCRIPT@@", script))
    return html.encode("utf-8"), csp(script, style)


def csp(script: str, style: str) -> str:
    return ("default-src 'none'; "
            f"script-src {_sha(script)}; style-src {_sha(style)}; "
            "img-src data:; connect-src 'self'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'")


_HTML = """<!doctype html>
<html lang="@@LANG@@" dir="@@DIR@@">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark light">
<meta name="theme-color" content="@@BG@@">
<meta name="referrer" content="no-referrer">
<link rel="icon" href="data:,">
<title>dopeIPTV</title>
<style>@@STYLE@@</style>
</head>
<body>
<main>
  <header><h1 id="title"></h1><span class="pill" id="usage"></span></header>
  <div class="banner" id="banner" role="status" hidden></div>
  <section><h2 id="hWatch"></h2><div class="card" id="watch"></div></section>
  <section><h2 id="hAddr"></h2>
    <div class="card">
      <div class="row"><div class="what"><b id="lTuner"></b><small id="lTunerHint"></small></div>
        <code id="aTuner" dir="ltr"></code><button class="btn" data-copy="aTuner"></button></div>
      <div class="row"><div class="what"><b id="lPlaylist"></b></div>
        <code id="aPlaylist" dir="ltr"></code><button class="btn" data-copy="aPlaylist"></button></div>
      <div class="row"><div class="what"><b id="lGuide"></b></div>
        <code id="aGuide" dir="ltr"></code><button class="btn" data-copy="aGuide"></button></div>
    </div>
  </section>
  <section><h2 id="hSet"></h2>
    <div class="card">
      <div class="row"><div class="what"><b id="lPort"></b><small id="lPortHint"></small></div>
        <input type="number" id="port" min="1024" max="65535" inputmode="numeric"></div>
      <div class="row"><div class="what"><b id="lTuners"></b><small id="lTunersHint"></small></div>
        <input type="number" id="tuners" min="0" max="64" inputmode="numeric"></div>
      <div class="row col"><div class="what"><b id="lSources"></b><small id="lSourcesHint"></small></div>
        <div id="sources"></div></div>
      <div class="row end"><span class="note" id="savedNote" hidden></span><button class="btn primary" id="save"></button></div>
    </div>
  </section>
  <section><h2 id="hStart"></h2>
    <div class="card"><div class="row col"><small id="lStartHint"></small><code class="block" id="cmd" dir="ltr"></code></div></div>
  </section>
</main>
<script>@@SCRIPT@@</script>
</body>
</html>
"""

_CSS = """
:root{@@DARK@@;color-scheme:dark;--r:12px}
@media (prefers-color-scheme: light){:root{@@LIGHT@@;color-scheme:light}}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--text);
  font:15px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans",Ubuntu,Cantarell,Arial,sans-serif}
main{max-width:760px;margin:0 auto;padding:20px 16px 48px}
header{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:8px}
h1{font-size:22px;margin:0;flex:1;min-width:0}
h2{font-size:12px;font-weight:700;letter-spacing:.6px;text-transform:uppercase;color:var(--muted);margin:26px 4px 8px}
[hidden]{display:none!important}
.pill{background:var(--btn);color:var(--text2);border-radius:999px;padding:5px 12px;font-size:13px;font-weight:600}
.pill.busy{background:var(--accent);color:var(--on-accent)}
.banner{background:var(--pane);color:var(--error);border-radius:var(--r);padding:12px 14px;margin:12px 0}
.card{background:var(--pane);border-radius:var(--r);overflow:hidden}
.row{display:flex;align-items:center;gap:12px;padding:12px 14px;flex-wrap:wrap}
.row+.row{box-shadow:inset 0 1px 0 var(--border)}
.row.col{flex-direction:column;align-items:stretch}
.row.end{justify-content:flex-end}
.what{flex:1 1 220px;min-width:0;display:flex;flex-direction:column;gap:2px}
small{color:var(--muted);font-size:13px}
code{font:13px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--input);color:var(--text2);
  border-radius:8px;padding:7px 10px;word-break:break-all;flex:1 1 260px;min-width:0;user-select:all}
code.block{white-space:pre-wrap;flex:none}
.btn{font:inherit;font-size:14px;font-weight:600;border:0;border-radius:9px;padding:8px 14px;cursor:pointer;
  background:var(--btn);color:var(--text)}
.btn:hover{background:var(--btn-hover)}
.btn.primary{background:var(--accent);color:var(--on-accent)}
.btn:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
input[type=number]{font:inherit;width:120px;border:0;border-radius:9px;padding:8px 10px;background:var(--input);color:var(--text)}
label.check{display:flex;align-items:center;gap:8px;padding:4px 0;cursor:pointer}
label.check input{accent-color:var(--accent);width:18px;height:18px}
.src{padding:6px 0}
.src+.src{box-shadow:inset 0 1px 0 var(--border)}
.cats{display:flex;flex-wrap:wrap;gap:2px 16px;padding:2px 0 6px 26px}
.chan{padding:12px 14px}
.chan+.chan{box-shadow:inset 0 1px 0 var(--border)}
.chan b{display:block}
.chan .meta{color:var(--muted);font-size:13px}
.viewer{font-size:13px;color:var(--text2);padding-top:4px;font-variant-numeric:tabular-nums}
.empty{padding:14px;color:var(--muted)}
.note{color:var(--ok);font-size:13px}
"""

_JS = r"""
"use strict";
const W = @@WORDS@@;
const $ = (id) => document.getElementById(id);
const token = new URLSearchParams(location.search).get("token") || "";
let settings = null, cats = {};

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
function fill(s, vals) { return s.replace(/\{(\w+)\}/g, (m, k) => (k in vals ? String(vals[k]) : m)); }
async function api(path, body) {
  const opts = {headers: {"X-Dopeiptv-Token": token}, cache: "no-store"};
  if (body !== undefined) {
    opts.method = "POST"; opts.body = JSON.stringify(body);
    opts.headers["Content-Type"] = "application/json";
  }
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}
function banner(text) { $("banner").textContent = text || ""; $("banner").hidden = !text; }
function mins(s) { return fill(W.srv_minutes, {n: Math.max(1, Math.round(s / 60))}); }

function showStatus(st) {
  const busy = st.in_use > 0;
  $("usage").textContent = fill(W.srv_in_use, {n: st.in_use, total: st.tuners});
  $("usage").classList.toggle("busy", busy);
  const box = $("watch");
  box.textContent = "";
  if (!st.channels.length) { box.appendChild(el("div", "empty", W.srv_nobody)); }
  for (const ch of st.channels) {
    const d = el("div", "chan");
    d.appendChild(el("b", "", ch.private ? W.private_menu : (ch.name || ch.stream)));
    const state = W["srv_state_" + ch.state] || ch.state;
    const meta = [ch.source_name || ch.source, state, mins(ch.since)];
    if (ch.shared) meta.push(W.srv_shared);
    d.appendChild(el("div", "meta", meta.join(" · ")));
    for (const v of ch.viewers) d.appendChild(el("div", "viewer", v.client + " · " + mins(v.since) + " · " + v.mb + " MB"));
    box.appendChild(d);
  }
  $("aTuner").textContent = st.addresses.tuner;
  $("aPlaylist").textContent = st.addresses.playlist;
  $("aGuide").textContent = st.addresses.guide;
}

function catList(src, holder) {
  holder.textContent = "";
  const all = !src.categories;
  const allBox = el("label", "check");
  const a = el("input"); a.type = "checkbox"; a.checked = all;
  allBox.append(a, el("span", "", W.srv_all_categories));
  holder.appendChild(allBox);
  const wrap = el("div", "cats"); wrap.hidden = all;
  holder.appendChild(wrap);
  a.addEventListener("change", async () => {
    wrap.hidden = a.checked;
    src.categories = a.checked ? null : (src.categories || []);
    if (!a.checked && !cats[src.id]) {
      try { cats[src.id] = (await api("api/categories?source=" + encodeURIComponent(src.id))).categories; }
      catch (e) { cats[src.id] = []; }
      drawCats(src, wrap);
    }
  });
  if (!all) {
    api("api/categories?source=" + encodeURIComponent(src.id))
      .then((r) => { cats[src.id] = r.categories; drawCats(src, wrap); }).catch(() => {});
  }
}
function drawCats(src, wrap) {
  wrap.textContent = "";
  for (const c of cats[src.id] || []) {
    const l = el("label", "check");
    const b = el("input"); b.type = "checkbox";
    b.checked = (src.categories || []).includes(c.id);
    b.addEventListener("change", () => {
      const set = new Set(src.categories || []);
      if (b.checked) set.add(c.id); else set.delete(c.id);
      src.categories = [...set];
    });
    l.append(b, el("span", "", c.name));
    wrap.appendChild(l);
  }
}

function showSettings(s) {
  settings = s;
  $("port").value = s.port; $("tuners").value = s.tuners;
  $("lTunersHint").textContent = W.srv_tuners_hint;
  $("cmd").textContent = s.command;
  const box = $("sources");
  box.textContent = "";
  if (!s.sources.length) { box.appendChild(el("div", "empty", W.srv_no_sources)); return; }
  for (const src of s.sources) {
    const d = el("div", "src");
    const l = el("label", "check");
    const b = el("input"); b.type = "checkbox"; b.checked = src.served;
    l.append(b, el("b", "", src.name));
    d.appendChild(l);
    const inner = el("div"); inner.hidden = !src.served;
    d.appendChild(inner);
    b.addEventListener("change", () => { src.served = b.checked; inner.hidden = !b.checked; });
    catList(src, inner);
    box.appendChild(d);
  }
}

async function save() {
  const sources = {};
  for (const src of settings.sources) if (src.served) sources[src.id] = src.categories || [];
  const body = {port: Number($("port").value), tuners: Number($("tuners").value), sources: sources};
  try {
    const r = await api("api/settings", body);
    $("savedNote").textContent = W.srv_saved; $("savedNote").hidden = false;
    setTimeout(() => { $("savedNote").hidden = true; }, 2500);
    if (r.port && r.port !== Number(location.port || 80)) {
      setTimeout(() => { location.port = String(r.port); }, 800);
    }
  } catch (e) { banner(W.srv_offline); }
}

async function poll() {
  try { showStatus(await api("api/status")); banner(""); }
  catch (e) { banner(W.srv_offline); }
  if (!document.hidden) setTimeout(poll, 3000);
}

function init() {
  $("title").textContent = W.srv_page_title; document.title = W.srv_page_title;
  $("hWatch").textContent = W.srv_watching; $("hAddr").textContent = W.srv_addresses;
  $("hSet").textContent = W.settings_title; $("hStart").textContent = W.srv_start;
  $("lTuner").textContent = W.srv_addr_tuner; $("lTunerHint").textContent = W.srv_addr_tuner_hint;
  $("lPlaylist").textContent = W.srv_addr_playlist; $("lGuide").textContent = W.srv_addr_guide;
  $("lPort").textContent = W.nerd_remote_port; $("lPortHint").textContent = W.srv_port_hint;
  $("lTuners").textContent = W.srv_tuners; $("lSources").textContent = W.srv_sources;
  $("lSourcesHint").textContent = W.srv_sources_hint; $("lStartHint").textContent = W.srv_start_hint;
  $("save").textContent = W.common_save; $("usage").textContent = W.common_loading;
  for (const b of document.querySelectorAll("[data-copy]")) {
    b.textContent = W.problem_copy;
    b.addEventListener("click", async () => {
      const text = $(b.dataset.copy).textContent;
      try { await navigator.clipboard.writeText(text); }
      catch (e) { const r = document.createRange(); r.selectNodeContents($(b.dataset.copy));
                  const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); return; }
      b.textContent = W.problem_copied; setTimeout(() => { b.textContent = W.problem_copy; }, 1500);
    });
  }
  $("save").addEventListener("click", save);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  api("api/settings").then(showSettings).catch(() => banner(W.srv_offline));
  poll();
}
init();
"""
