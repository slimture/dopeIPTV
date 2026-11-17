"""The phone remote's page made for the phone it is on: Now playing kept
compact - what plays' sound and subtitles, the sleep timer, recording and the
next programme folded behind More, the choice kept on the phone, a running
recording's red dot and time left on the folded card - and TV mode's remote
filling the room below it, upright and on its side, every key 44 px or more.

The page's structure is checked here as text; the layout itself in a real
browser (Chromium through Playwright, tests/data/phone_layout.js) at
320x568, 375x667, 390x844 and 428x926 and each on its side, dark and light -
skipped where node or Playwright's Chromium is missing (CI installs
neither). DOPEIPTV_PHONE_SHOTS=<folder> keeps a picture of each step."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from dopeiptv.core import phone_page
from dopeiptv.core.phone_remote import PhoneServer, RateLimiter

TOKEN = "Ab3_-x9Ab3_-x9Ab3_-x9Ab3_-x9Qz12"
SIZES = [(320, 568), (375, 667), (390, 844), (428, 926)]


def _sha(text):
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


def _page():
    words = {k: k.replace("_", " ").title() for k in phone_page.WORDS}
    html, csp = phone_page.render({"bg": "#17171C", "rec": "#E5484D"}, {"bg": "#F5F5F7"}, words, "en")
    html = html.decode()
    script = re.search(r"<script>(.*)</script>", html, re.S).group(1)
    style = re.search(r"<style>(.*)</style>", html, re.S).group(1)
    return html, csp, script, style


def _between(text, start, end):
    i = text.index(start)
    return text[i:text.index(end, i)]


def _fn(script, name):
    return re.search(r"function " + name + r"\(.*?\) \{(.*?)\n\}", script, re.S).group(1)


# -- the page, as text ------------------------------------------------------------------------

def test_now_playing_folds_its_secondary_rows_behind_more():
    html, csp, script, style = _page()
    assert f"script-src {_sha(script)};" in csp and f"style-src {_sha(style)};" in csp
    now = _between(html, '<section class="now"', "</section>")
    # One small button with a chevron, folded until the phone says otherwise.
    more = re.search(r'<button class="more" id="moreBtn"[^>]*>', now).group(0)
    assert 'aria-expanded="false"' in more and 'aria-controls="nowMore"' in more and "hidden" in more
    assert 'class="caret"' in _between(now, 'id="moreBtn"', "</button>")
    # Behind it: subtitles and sound, the sleep timer, the Record row and the next programme.
    body = _between(now, '<div class="nowMore" id="nowMore" hidden>', '<span class="nowPill"')
    for part in ("tracksBtn", "sleepBtn", "nowRec", "recBtn", "nextBtn"):
        assert f'id="{part}"' in body, part
    # The Record row stays a row of its own, apart from the transport keys.
    assert body.index('id="nowSet"') < body.index('id="nowRec"')
    assert "recBtn" not in _between(html, '<footer class="dock"', "</footer>")
    assert "recBtn" not in _between(html, 'id="nowTimes"', "</div>")
    assert "body.tv .nowMore{display:none}" in style
    # Its words are the app's.
    assert {"more", "less"} <= set(phone_page.WORDS)
    fold = _fn(script, "folds")
    assert "open ? W.less : W.more" in fold and 'setAttribute("aria-expanded", String(open))' in fold


def test_the_fold_is_remembered_on_the_phone_and_private_mode_is_no_error():
    _html, _csp, script, _style = _page()
    keys = re.search(r"const FOLDS = \{(.*?)\n\};", script, re.S).group(1)
    assert 'key: "more"' in keys and 'key: "tv_more"' in keys
    read = script.index("localStorage.getItem(FOLDS[k].key)")
    assert "try {" in script[read - 30:read]
    write = script.index("localStorage.setItem(FOLDS[k].key")
    assert "try {" in script[write - 30:write]
    assert 'unfolded[k] = localStorage.getItem(FOLDS[k].key) === "1"' in script     # folded unless kept open
    assert "initFolds();" in _fn(script, "init") and "folds();" in _fn(script, "show")


def test_a_recording_stays_in_sight_on_the_folded_card():
    html, _csp, script, style = _page()
    now = _between(html, '<section class="now"', "</section>")
    acts = _between(now, '<div class="nowActs" id="nowActs">', '<div class="nowMore"')
    # The red dot and its time sit beside More, outside what folds away...
    live = _between(acts, 'id="recLive"', "</button>")
    assert 'class="recIcon"' in live and 'class="fill"' in live
    assert 'id="recLive"' not in _between(now, 'id="nowMore"', '<span class="nowPill"')
    # ...shown whenever a recording runs and the card is folded, its time each second.
    fold = _fn(script, "folds")
    # (TV mode's remote: open or folded, its Record pill giving way to it.)
    assert "recording = !!(st.rec && st.rec.id)" in fold
    assert 'hide($(f.rec), !recording || (open && k === "now"))' in fold
    tick = _fn(script, "recTick")
    assert "FOLDS[k].rec" in tick and "hms(" in tick and 'setAttribute("aria-label", text)' in tick
    assert "recTick();" in _fn(script, "tick")
    # A tap on it asks to stop, as Stop always does; it never starts a recording.
    init = _fn(script, "initFolds")
    assert "if (st.rec && st.rec.id) stopAsk(st.rec.id, st.rec.title)" in init
    assert script.count('send("api/rec"') == 1 and 'function recNow(dont) { send("api/rec"' in script
    assert ".showIn.live.rec{background:var(--sel);color:var(--rec)}" in style
    # The sleep timer's minutes stay too, a way to its sheet.
    assert "FOLDS[k].sleep" in _fn(script, "sleepTick") and "openSleep" in init


def test_tv_modes_remote_folds_the_same_and_shows_a_recording():
    html, _csp, script, _style = _page()
    remote = _between(html, '<section class="tvr" id="tvr" hidden>', "</section>")
    folds = _between(remote, '<div class="folds">', '<div class="pair" id="tvActs"')
    for part in ("padMode", "digitsBtn", "tvRecLive", "tvSleepLive", "tvMoreBtn"):
        assert f'id="{part}"' in folds, part
    assert re.search(r'id="tvMoreBtn" aria-expanded="false" aria-controls="tvActs" hidden', folds)
    assert 'id="tvTracks"' in _between(remote, 'id="tvActs"', "</div>")
    assert "tvActs" not in _fn(script, "actsOffer")           # the fold shows it, nothing else


def test_tv_modes_remote_records_from_its_fold_as_now_playing_does():
    html, csp, script, style = _page()
    # The page's policy still allows exactly its own script and style.
    assert f"script-src {_sha(script)};" in csp and f"style-src {_sha(style)};" in csp
    assert "'unsafe-inline'" not in csp
    # A Record pill beside Subtitles and sound and the Sleep timer, in the fold,
    # hidden until the state says what plays can be recorded.
    acts = _between(html, '<div class="pair" id="tvActs" hidden>', "</div>")
    assert acts.index('id="tvTracks"') < acts.index('id="tvSleep"') < acts.index('id="tvRec"')
    pill = re.search(r'<button class="pill" id="tvRec" hidden>.*?</button>', acts).group(0)
    assert 'class="recIcon"' in pill and 'class="fill"' in pill and 'id="tvRecText"' in pill
    assert "tvRec" not in _between(html, '<footer class="dock"', "</footer>")
    # Shown for a live channel the app can record from what plays, gone while
    # one records: the dot beside More shows it then, open or folded.
    offer = _fn(script, "recOffer")
    assert 'hide($("tvRec"), !(live && s.can_rec) || running);' in offer
    fold = _fn(script, "folds")
    assert 'hide($(f.rec), !recording || (open && k === "now"))' in fold
    # The same press as Now playing's Record: asked first unless the household
    # turned that off, a long press asks anyway; the app's own words.
    init = _fn(script, "initRec")
    assert 'for (const id of ["recBtn", "tvRec"])' in init
    assert "if (!(st.rec && st.rec.id)) recAsk();" in init and "recPressed();" in init
    assert '$("tvRecText").textContent = W.rec;' in init
    assert "if (st.rec_ask) recAsk(); else recNow(false);" in _fn(script, "recPressed")
    # One way to start a recording (api/rec), never without that press.
    assert script.count('send("api/rec"') == 1
    # Still a key a thumb reaches: the fold's pills, 44 px and more.
    assert "min-height:var(--key-h)" in style.split("\n.pill{", 1)[1].split("}", 1)[0]


def test_tv_modes_remote_fills_the_room_left_and_stays_on_the_screen():
    _html, _csp, _script, style = _page()

    def rule(sel):                      # a rule of its own, at the start of a line
        return style.split("\n" + sel + "{", 1)[1].split("}", 1)[0]

    # The small viewport's height (the browser's bars shown), with a fallback.
    assert "--svh:1vh" in style and "@supports (height:1svh){:root{--svh:1svh}}" in style
    assert "height:calc(var(--svh)*100)" in rule("body.tv #app")
    assert "flex:1 0 auto" in rule("body.tv #app>.tvr")
    # The d-pad takes what is left between the rockers and under Now playing...
    assert "container-type:size" in style and "100cqw" in style and "100cqh" in style
    assert "--P:var(--pad)" in style                                   # without container queries
    assert "@container (max-aspect-ratio: 6/5)" in style                # tall: the rockers under it
    assert "width:var(--P);height:var(--P)" in rule(".pad")
    # ...never under 44 px a key, the home bar kept clear, side by side when on its side.
    assert "--pad-min:150px" in style and "min-height:44px" in rule(".rock button")
    assert "env(safe-area-inset-bottom" in style and "var(--safe-b)" in rule(".tvr")
    assert "@media (orientation: landscape) and (min-width: 540px)" in style
    assert '"stage side"' in style
    assert "overflow-x" not in style


def test_the_window_gives_the_page_its_new_words():
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.i18n import tr
    from dopeiptv.ui import mw_phone
    words = mw_phone.page_words()
    assert words["more"] == tr("phone_page_more") and words["less"] == tr("phone_page_less")
    assert words["more"] and words["less"]


# -- the page in a real browser ---------------------------------------------------------------

def _node():
    """(node, its environment) when Playwright and its Chromium are there, else None."""
    node = shutil.which("node")
    if not node:
        return None
    env = dict(os.environ)
    try:
        root = subprocess.run([node, "-p", "require('path').resolve(process.execPath, '../../lib/node_modules')"],
                              capture_output=True, text=True, timeout=20).stdout.strip()
        env["NODE_PATH"] = os.pathsep.join(p for p in (env.get("NODE_PATH", ""), root) if p)
        exe = subprocess.run([node, "-e", "process.stdout.write(require('playwright').chromium.executablePath())"],
                             capture_output=True, text=True, timeout=20, env=env).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return (node, env) if exe and os.path.exists(exe) else None


class _Host:
    """What the window answers, for one live channel: TV mode, a recording and
    the sleep timer as the page asks for them; Stop puts it back as it was."""

    def __init__(self):
        self.lock = threading.Lock()
        self.t0 = time.time() // 60 * 60
        self.reset()

    def reset(self):
        self.tv, self.rec, self.sleep_at = False, None, 0.0

    def state(self):
        out = {"state": "playing", "time": time.time(), "volume": 64, "muted": False, "source": "Pirat",
               "stamp": "s1", "tv_mode": self.tv, "kind": "live", "title": "TV4 HD", "position": 0,
               "duration": 0, "channel": {"id": 1002, "name": "TV4 HD", "number": 3, "logo": False, "here": True},
               "now": {"title": "Rapport", "start": self.t0 - 900, "stop": self.t0 + 900},
               "next": {"title": "Sportnytt", "start": self.t0 + 900, "stop": self.t0 + 1800},
               "can_rec": True, "rec_ask": False, "tracks": "1",
               "sections": ["all", "fav", "vod", "series", "recent", "local", "radio", "rec"]}
        if self.rec:
            out["rec"] = self.rec
        if self.sleep_at > time.time():
            out["sleep_at"] = int(self.sleep_at)
        return out

    def __call__(self, action, params):
        with self.lock:
            if action == "state":
                return self.state()
            if action == "channels":
                names = ["SVT1 HD", "SVT2", "TV4 HD", "Kanal 5", "TV6", "Sjuan", "TV8", "Kanal 9"]
                return {"channels": [{"id": 1000 + i, "name": n, "number": i + 1, "logo": False}
                                     for i, n in enumerate(names)], "favorites": [1000, 1002]}
            if action == "tv":
                self.tv = params.get("mode") == "on"
                return {"ok": True, "tv_mode": self.tv}
            if action == "rec":
                self.rec = {"id": "r1", "title": "TV4 HD", "start": int(time.time()) - 754, "here": True}
            elif action == "rec_stop":
                self.rec = None
            elif action == "sleep":
                self.sleep_at = time.time() + 60 * int(params.get("minutes") or 0)
            elif action == "stop":
                self.reset()
            elif action == "logo":
                return None
            return {"ok": True}


def _serve(lang):
    from dopeiptv import i18n
    from dopeiptv.ui import mw_phone
    i18n.set_language(lang)
    try:
        words = mw_phone.page_words()
    finally:
        i18n.set_language("en")
    dark, light = mw_phone.page_palettes(False)
    page = phone_page.render(dark, light, words, lang, lang in ("ar", "fa", "he", "ur"))
    srv = PhoneServer(0, TOKEN, _Host(), lambda: page, host="127.0.0.1",
                      limiter=RateLimiter(rate=500.0, burst=500.0))
    srv.start()
    return srv


def test_the_page_fits_each_phone_upright_and_on_its_side_in_a_browser():
    pytest.importorskip("PyQt6.QtWidgets")
    found = _node()
    if found is None:
        pytest.skip("node with Playwright's Chromium is not installed")
    node, env = found
    servers = {lang: _serve(lang) for lang in ("en", "ru", "ar")}
    try:
        def url(lang):
            return f"http://127.0.0.1:{servers[lang].port}/{TOKEN}/"
        runs = []
        for w, h in SIZES:
            for size in ((w, h), (h, w)):
                for scheme in ("dark", "light"):
                    runs.append({"url": url("en"), "w": size[0], "h": size[1], "scheme": scheme, "lang": "en",
                                 "full": scheme == "dark"})
        # Long words (Russian) on the smallest phone both ways; right to left (Arabic).
        runs += [{"url": url("ru"), "w": 320, "h": 568, "scheme": "dark", "lang": "ru", "full": True},
                 {"url": url("ru"), "w": 568, "h": 320, "scheme": "dark", "lang": "ru", "full": False},
                 {"url": url("ar"), "w": 375, "h": 667, "scheme": "dark", "lang": "ar", "full": True}]
        args = [node, str(Path(__file__).parent / "data" / "phone_layout.js"), json.dumps(runs)]
        shots = os.environ.get("DOPEIPTV_PHONE_SHOTS", "")
        if shots:
            os.makedirs(shots, exist_ok=True)
            args.append(shots)
        done = subprocess.run(args, capture_output=True, text=True, timeout=600, env=env)
    finally:
        for srv in servers.values():
            srv.stop()
    lines = done.stdout.strip().splitlines()
    assert lines, done.stderr[-2000:]
    out = json.loads(lines[-1])
    assert "fatal" not in out, out.get("fatal")
    checks = out["checks"]
    assert out["problems"] == [], "\n".join(out["problems"])
    assert checks["errors"] == [], "\n".join(checks["errors"])
    # TV mode's remote is on the screen whole as it opens: no scrolling, at every size.
    assert len(checks["fits"]) == len(runs)
    for tag, (height, screen) in checks["fits"].items():
        assert height <= screen + 1, (tag, height, screen)
    # Now playing opens folded and stays compact; the phone remembers it opened.
    for tag, f in checks["folded"].items():
        assert f["expanded"] == "false" and f["body"] is True, tag
        assert f["height"] <= 240, (tag, f["height"])
    assert checks["remembered"] == ["true", "1"]
    # A recording's dot and time on the folded card, saying what a tap does.
    assert checks["recLive"]
    for tag, r in checks["recLive"].items():
        assert re.search(r"\d+:\d\d", r["text"]) and r["recBtnHidden"], (tag, r)
        assert r["label"].endswith(r["text"]), (tag, r)
    # Record from TV mode's remote: its pill gives way to the dot and its
    # time, the fold still open - one control for a recording, not two.
    assert set(checks["tvRec"]) == set(checks["recLive"])
    for tag, r in checks["tvRec"].items():
        assert r["open"] == "true" and re.search(r"\d+:\d\d", r["text"]), (tag, r)
