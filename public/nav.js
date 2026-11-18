/* Counting visits, for the site's own statistics (donate/stats_collect.py):
 * the page tells /hit once it is shown in a browser, how long it was looked
 * at when it is left, and when the donate dialog opens or a donation goes
 * through. No cookie, no id, nothing kept in the browser; the server counts.
 * Robots that fetch pages without running them never send it, which is
 * what makes the numbers honest. Nothing here can get in the page's way.
 *
 * Two yes-or-no facts help tell people from the robots that do run pages:
 * whether the visitor moved the pointer, touched, scrolled or typed (once a
 * page, only that it happened - never where, what or how), and whether the
 * browser says it runs without a screen (headless Chrome names itself in
 * its brands). stats_collect.py judges the visit from them.
 */
/* Chromium (Chrome, Edge, Opera, Brave) redoes the blur behind the sticky
 * header on every frame of a scroll, and the sections rising into view and
 * moving layers clipped to rounded corners cost it too; Safari and Firefox
 * do all of it cheaply. On Chromium the header is near-opaque instead, the
 * sections are simply there and the donate band is not clipped (style.css,
 * app.js). userAgentData and window.chrome are Chromium's alone; Brave can
 * hide the first, so both are asked. */
var IPTV_CR = !!(navigator.userAgentData || window.chrome || navigator.brave);
if (IPTV_CR) { document.documentElement.classList.add("cr"); }

var iptvHit = (function () {
  "use strict";
  var since = 0, started = false, acted = false;
  var INPUT = ["pointermove", "pointerdown", "touchstart", "wheel", "keydown"];
  function send(q) {
    try {
      var url = "/hit?p=" + encodeURIComponent(location.pathname) + "&" + q;
      if (navigator.sendBeacon && navigator.sendBeacon(url)) { return; }
      if (window.fetch) { fetch(url, { method: "POST", keepalive: true, credentials: "same-origin" }); }
    } catch (e) { /* counting never gets in the way */ }
  }
  function shown() {
    started = true;
    since = Date.now();
    var src = (/[?&](?:ref|utm_source|source)=([^&#]*)/.exec(location.search) || [])[1] || "";
    send("ev=view&r=" + encodeURIComponent(document.referrer || "") +
         "&s=" + encodeURIComponent(decodeURIComponent(src).slice(0, 40)) +
         "&l=" + encodeURIComponent(document.documentElement.lang || "") +
         "&bl=" + encodeURIComponent(navigator.language || "") +
         "&w=" + (screen.width || 0) + "&h=" + (screen.height || 0) +
         (headless() ? "&hl=1" : ""));
    // Counted: ?ref= leaves the address, so a link shared or bookmarked from
    // here does not count as one from the app or GitHub again.
    if (src && history.replaceState) {
      try {
        var q = location.search.replace(/([?&])(?:ref|utm_source|source)=[^&#]*&?/g, "$1").replace(/[?&]$/, "");
        history.replaceState(history.state, "", location.pathname + q + location.hash);
      } catch (e) { /* the address keeps it; harmless */ }
    }
  }
  function headless() {
    try {
      var b = navigator.userAgentData && navigator.userAgentData.brands;
      return !!b && b.some(function (x) { return /headless/i.test(x.brand || ""); });
    } catch (e) { return false; }
  }
  // Scroll is not among them: a link to #download scrolls the page by itself.
  // isTrusted: an event a script made up is no sign of anyone. A pointer
  // move only counts once the pointer has really gone somewhere - Chromium
  // sends one at a standstill when the page lays out, robots without a
  // mouse included (theirs at 0,0).
  var px = null, py = null;
  function act(e) {
    if (acted || !started || (e && e.isTrusted === false)) { return; }
    if (e && e.type === "pointermove" && (px === null || (e.clientX === px && e.clientY === py))) {
      px = e.clientX; py = e.clientY;
      return;
    }
    acted = true;
    INPUT.forEach(function (t) { document.removeEventListener(t, act, true); });
    send("ev=act");
  }
  function left() {
    if (!since) { return; }
    var secs = Math.round((Date.now() - since) / 1000);
    since = 0;
    if (secs > 0) { send("ev=leave&t=" + secs); }
  }
  try {
    if (navigator.webdriver) { return function () {}; }    // automated browsers say so
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") { left(); }
      else if (started && !since) { since = Date.now(); }
    });
    window.addEventListener("pagehide", left);
    INPUT.forEach(function (t) { document.addEventListener(t, act, { capture: true, passive: true }); });
    if (document.prerendering) {
      document.addEventListener("prerenderingchange", shown, { once: true });
    } else {
      shown();
    }
  } catch (e) { /* as above */ }
  // iptvHit(event[, what[, number]]): events.js tells what visitors do on
  // the page with it (donate/stats_events.py lists what is kept).
  return function (ev, x, n) {
    send("ev=" + encodeURIComponent(ev) +
         (x ? "&x=" + encodeURIComponent(String(x).slice(0, 80)) : "") +
         (n !== undefined ? "&n=" + encodeURIComponent(n) : ""));
  };
})();

/* Header disclosures: close them when you click away or press Escape.
 *
 * Everything the header does works without this file - <details> is a native
 * disclosure and the items are plain links. This only adds the dismissal a
 * menu is expected to have, so losing it costs politeness, not navigation.
 *
 * An EXTERNAL file, like help.js: the site's CSP is `script-src 'self'` with
 * no 'unsafe-inline' (deploy/csp.snippet.txt), and an inline block would be
 * blocked without a word.
 */
(function () {
  var menus = [].slice.call(document.querySelectorAll("header.bar details"));
  if (!menus.length) { return; }

  function shut(except) {
    menus.forEach(function (d) {
      if (d !== except && d.open) { d.open = false; }
    });
  }

  document.addEventListener("click", function (e) {
    var inside = null;
    menus.forEach(function (d) { if (d.contains(e.target)) { inside = d; } });
    // Older browsers ignore the `name` attribute that makes the disclosures
    // an exclusive pair, so close the others here too.
    shut(inside);
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { shut(null); }
  });
})();

/* Support: nothing is paid on this site. A tip and monthly support are
 * Stripe's own Payment Links, on the support page (/support/, render.jq's
 * support options). What is here:
 * - old links that opened the donate dialog (dopeiptv.org/#donate: GitHub's
 *   Sponsor button, older apps, release notes) land on /support/; the app's
 *   heart (#support) shows the band on the front page, an invitation;
 * - back from paying (/support/?paid=<Checkout Session id>) the thank-you
 *   shows the supporter key - /support/key (donate/donate.py) asks Stripe
 *   whether that session is paid - with a copy button and a dopeiptv://
 *   link, kept in this browser so the page shows it again;
 * - the owner's note: new tips and monthly support at the foot of the
 *   site's pages, in the browser that has opened /admin/.
 */
(function () {
  "use strict";
  var CALM = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  var HEART = "M12 21s-7.5-4.6-9.6-9.2C.9 8.4 3 4.5 6.9 4.5c2.1 0 3.6 1.1 5.1 3 1.5-1.9 3-3 5.1-3 3.9 0 6 3.9 4.5 7.3C19.5 16.4 12 21 12 21z";
  function mk(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text) { n.textContent = text; }
    return n;
  }
  function heart(cls) {
    var NS = "http://www.w3.org/2000/svg", svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", cls);
    svg.setAttribute("aria-hidden", "true");
    var path = document.createElementNS(NS, "path");
    path.setAttribute("d", HEART);
    svg.append(path);
    return svg;
  }
  function thump(node) {
    if (!node || !node.animate) { return; }
    if (CALM) {                     // less motion: it glows instead of beating
      node.animate([{ opacity: 1 }, { opacity: 0.45 }, { opacity: 1 }], { duration: 620, easing: "ease-in-out" });
      return;
    }
    node.animate([1, 1.7, 0.85, 1.25, 1].map(function (v) { return { transform: "scale(" + v + ")" }; }),
      { duration: 620, easing: "ease-out" });
  }

  // -- where an old link lands -------------------------------------------------
  var path = window.location.pathname;
  if (window.location.hash === "#donate" || /^#key=/.test(window.location.hash)) {
    // The donate dialog is gone: the support page has the ways to give. A
    // receipt's old #key link finds its way there too (its key: write to us).
    window.location.replace("/support/" + window.location.search);
    return;
  }
  if (window.location.hash === "#support") {
    // The app's heart: the band, not a payment. The visitor reads why first.
    // A front page without it (no way to pay is set up yet) leaves that to
    // the support page.
    var band0 = document.getElementById("support");
    if (!band0 && !/^\/support\//.test(path)) {
      window.location.replace("/support/" + window.location.search);
      return;
    }
    if (band0) {
      requestAnimationFrame(function () {
        band0.scrollIntoView({ behavior: CALM ? "auto" : "smooth", block: "center" });
        var btn = band0.querySelector(".donate-btn");
        if (btn && !CALM) {
          setTimeout(function () { btn.classList.add("invite"); }, 500);
          setTimeout(function () { btn.classList.remove("invite"); }, 3100);
        }
      });
    }
  }

  // The band on the front page beats and drifts only while it is in view.
  var band = document.querySelector(".donate");
  if (band && "IntersectionObserver" in window) {
    new IntersectionObserver(function (seen) {
      seen.forEach(function (s) { band.classList.toggle("is-live", s.isIntersecting); });
    }, { threshold: 0.2 }).observe(band);
  }
  // A heart pressed beats once on its way to the support page.
  document.addEventListener("click", function (e) {
    var a = e.target.closest ? e.target.closest("a.navdonate, a.navheart, a.donate-btn") : null;
    if (a) { thump(a.querySelector("svg") || a); }
  });

  // -- the thank-you after paying ----------------------------------------------
  var box = document.getElementById("support-thanks");
  if (box && window.fetch) {
    var T = box.dataset, KEPT = "dn-supporter-key";
    var paid = /[?&]paid=(cs_(?:live|test)_[A-Za-z0-9]{8,200})(?:&|$)/.exec(window.location.search);
    var head = mk("p", "dn-keyhead"), code = mk("code", "dn-keytext"), state = mk("p", "dn-keystate");
    code.setAttribute("translate", "no");
    var row = mk("div", "dn-keyrow");
    var copy = mk("button", "btn primary dn-keycopy", T.keycopy); copy.type = "button";
    var openIt = mk("a", "btn dn-keyopen", T.keyopen);
    row.append(copy, openIt);
    var how = mk("p", "dn-keynote", T.keyhow);
    var keyPart = mk("div", "dn-key"); keyPart.hidden = true;
    keyPart.append(head, code, row, how);
    box.append(mk("h2", "support-thanks-h", T.thanks), keyPart, state);
    head.textContent = T.key;
    state.hidden = true;
    var pick = function () {        // no clipboard: the key selected, for the visitor's own copy
      var r = document.createRange(); r.selectNodeContents(code);
      var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
    };
    copy.addEventListener("click", function () {
      var said = function () {
        copy.textContent = T.keycopied;
        setTimeout(function () { copy.textContent = T.keycopy; }, 2500);
      };
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(code.textContent).then(said, pick);
      } else { pick(); }
    });
    var say = function (text) { state.textContent = text || ""; state.hidden = !text; };
    var showKey = function (key) {
      if (!/^DS1-[A-Za-z0-9_-]{20,}$/.test(key || "")) { return false; }
      code.textContent = key;
      openIt.href = "dopeiptv://supporter?key=" + encodeURIComponent(key);
      keyPart.hidden = false; say("");
      try { localStorage.setItem(KEPT, key); } catch (e) { /* this browser keeps nothing */ }
      return true;
    };
    var tries = 0;
    var ask = function (cs) {
      fetch("/support/key", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ s: cs }) })
        .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return [r.status, j]; }); })
        .then(function (got) {
          if (got[0] === 200 && showKey(got[1].key)) {
            if (typeof iptvHit === "function") { iptvHit("donated"); }
            return;
          }
          if (got[0] === 202 && ++tries < 6) { say(T.keywait); setTimeout(function () { ask(cs); }, 5000); return; }
          say(got[0] === 202 ? T.keywait : T.keynone);
        })
        .catch(function () { say(T.keywait); });
    };
    var kept = "";
    try { kept = localStorage.getItem(KEPT) || ""; } catch (e) { kept = ""; }
    if (paid) {
      box.hidden = false;
      say(T.loading);
      try {     // the session's id is not for the history or a shared link
        history.replaceState(null, "", path + window.location.search.replace(/([?&])paid=[^&]*/, "$1")
          .replace(/[?&]+$/, "").replace(/\?&+/, "?") + window.location.hash);
      } catch (e) { /* the address stays; harmless */ }
      requestAnimationFrame(function () { box.scrollIntoView({ block: "start" }); });
      ask(paid[1]);
    } else if (kept && showKey(kept)) {
      box.hidden = false;
      box.querySelector(".support-thanks-h").textContent = T.again;
    }
  }

  // -- the owner's note --------------------------------------------------------
  // In a browser that has opened /admin/ (which leaves iptv_admin_hint, no
  // secret), new tips and monthly support show at the foot of the site's
  // pages: a heart, how much, a link to the statistics. Dismissed - or the
  // admin page opened - they are seen and do not come back. /admin/news
  // answers only the signed-in admin; every other visitor's page asks
  // nothing. In English, as the admin page is.
  (function ownerNote() {
    if (!window.fetch || !/(?:^|;\s*)iptv_admin_hint=1(?:;|$)/.test(document.cookie)) { return; }
    var KEY = "dn-admin-seen", note = null, timer = 0;
    function seen() { try { return +localStorage.getItem(KEY) || 0; } catch (e) { return 0; } }
    function mark(t) { try { localStorage.setItem(KEY, String(t)); } catch (e) { /* none */ } }
    function money(minor, c) {
      try {
        var f = new Intl.NumberFormat(undefined, { style: "currency", currency: c });
        return f.format(minor / Math.pow(10, f.resolvedOptions().maximumFractionDigits));
      } catch (e) { return (minor / 100) + " " + c; }
    }
    function ago(t) {
      var s = Math.max(0, Date.now() / 1000 - t), rt;
      try { rt = new Intl.RelativeTimeFormat("en", { numeric: "auto" }); } catch (e) { return ""; }
      if (s < 3600) { return rt.format(-Math.max(1, Math.round(s / 60)), "minute"); }
      if (s < 86400) { return rt.format(-Math.round(s / 3600), "hour"); }
      return rt.format(-Math.round(s / 86400), "day");
    }
    function away(rows) {
      mark(Math.max.apply(null, rows.map(function (r) { return r.t; })));
      if (!note) { return; }
      var n = note; note = null;
      n.classList.add("is-leaving");
      setTimeout(function () { n.remove(); }, 260);
    }
    function render(rows) {
      if (note) { note.remove(); }
      note = mk("aside", "owner-note");
      note.setAttribute("role", "status");
      var mark1 = heart("owner-heart"), body = mk("div", "owner-body");
      body.append(mk("strong", "", rows.length === 1 ? "New support" : rows.length + " new tips and payments"));
      var ul = mk("ul", "owner-list");
      rows.slice(0, 3).forEach(function (r) {
        var li = mk("li");
        li.append(mk("b", "", money(r.amount, r.currency)),
                  document.createTextNode([r.how, ago(r.t), r.who].filter(Boolean).map(function (x) { return " · " + x; }).join("")));
        ul.append(li);
      });
      if (rows.length > 3) { ul.append(mk("li", "owner-more", "and " + (rows.length - 3) + " more")); }
      var link = mk("a", "owner-link", "Open statistics");
      link.href = "/admin/#donations";
      link.addEventListener("click", function () { away(rows); });
      body.append(ul, link);
      var shut = mk("button", "owner-close", "×");
      shut.type = "button";
      shut.setAttribute("aria-label", "Dismiss");
      shut.addEventListener("click", function () { away(rows); });
      note.append(mark1, body, shut);
      document.body.append(note);
      setTimeout(function () { thump(mark1); }, 380);
    }
    function check() {
      if (document.hidden) { return; }
      fetch("/admin/news", { credentials: "same-origin", cache: "no-store" })
        .then(function (r) {
          if (r.status === 204) { clearInterval(timer); return null; }     // signed out: stop asking
          return r.ok ? r.json() : null;
        })
        .then(function (j) {
          if (!j || !j.list) { return; }
          var since = seen();
          if (!since) { since = Date.now() / 1000 - 3 * 86400; mark(since); }    // a first look: the last three days
          var fresh = j.list.filter(function (r) { return r.t > since; });
          if (fresh.length) { render(fresh); } else if (note) { note.remove(); note = null; }
        })
        .catch(function () { /* the note is not worth a word */ });
    }
    check();
    timer = setInterval(check, 120000);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) { check(); } });
  })();
})();

/* A copy button on each command in the help - on the help page and in the
   install dialog on the front page (app.js calls this for what it opens). A
   command is there to be run, and selecting a line of it by hand on a
   trackpad is fiddly. An icon, so it is one size in every language; its
   name (the body's data-copy) is the label and the tooltip. Only where the
   page may write the clipboard; elsewhere the commands stay as they were. */
window.iptvCopyButtons = function (root) {
  "use strict";
  var body = document.body, say = body.getAttribute("data-copy"),
      done = body.getAttribute("data-copied") || say;
  if (!say || !(navigator.clipboard && window.isSecureContext)) { return; }
  var NS = "http://www.w3.org/2000/svg";
  function svg(paths) {
    var s = document.createElementNS(NS, "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("aria-hidden", "true");
    paths.forEach(function (d) {
      var p = document.createElementNS(NS, d.charAt(0) === "r" ? "rect" : "path");
      if (d.charAt(0) === "r") {
        var v = d.slice(1).split(",");
        ["x", "y", "width", "height", "rx"].forEach(function (k, i) { p.setAttribute(k, v[i]); });
      } else { p.setAttribute("d", d); }
      s.appendChild(p);
    });
    return s;
  }
  [].forEach.call(root.querySelectorAll(".help-a pre"), function (pre) {
    if (pre.parentNode.classList.contains("codebox")) { return; }
    var code = pre.querySelector("code") || pre;
    var box = document.createElement("div");
    box.className = "codebox";
    pre.parentNode.insertBefore(box, pre);
    box.appendChild(pre);
    var btn = document.createElement("button");
    btn.type = "button"; btn.className = "copy";
    btn.title = say; btn.setAttribute("aria-label", say);
    btn.append(svg(["r9,9,11,11,2.5", "M5 15V6.5A2.5 2.5 0 0 1 7.5 4H15"]),
               svg(["M5 12.5l4.5 4.5L19 7.5"]));
    var timer = 0;
    btn.addEventListener("click", function () {
      navigator.clipboard.writeText(code.textContent).then(function () {
        btn.classList.add("is-done");
        btn.title = done; btn.setAttribute("aria-label", done);
        clearTimeout(timer);
        timer = setTimeout(function () {
          btn.classList.remove("is-done");
          btn.title = say; btn.setAttribute("aria-label", say);
        }, 1800);
      }, function () { /* refused: the command is still there to select */ });
    });
    box.appendChild(btn);
  });
};
window.iptvCopyButtons(document);

/* What dopeIPTV is built on, under the donate band: opened, the card
 * grows down, a small TV drops in and switches on, and the projects fly out
 * of it as its channels (style.css .built-more); closed, it folds back up. The page
 * follows the card down. Without motion, it simply opens and closes. */
(function () {
  "use strict";
  var box = document.querySelector(".built-more");
  if (!box || !box.animate) { return; }
  var sum = box.querySelector("summary"), body = box.querySelector(".built-body");
  var still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var running = null;
  // The channels fly out of the TV: each project starts small at the TV's
  // screen and springs to its place, one after another (transform and
  // opacity only). Measured once, while the card is laid out at full size.
  function fly() {
    var tv = box.querySelector(".built-tv svg");
    if (!tv) { return; }
    var t = tv.getBoundingClientRect(), cx = t.left + t.width / 2, cy = t.top + t.height * .55;
    [].forEach.call(box.querySelectorAll(".credit"), function (c, i) {
      var r = c.getBoundingClientRect();
      var dx = cx - (r.left + r.width / 2), dy = cy - (r.top + r.height / 2);
      c.animate([{ transform: "translate(" + dx + "px, " + dy + "px) scale(.12)", opacity: 0 },
                 { opacity: 1, offset: .3 },
                 { transform: "none", opacity: 1 }],
                { duration: 680, delay: 420 + i * 70, easing: "cubic-bezier(.34, 1.36, .64, 1)", fill: "backwards" });
    });
  }
  sum.addEventListener("click", function (e) {
    if (still || !body) { return; }
    e.preventDefault();
    if (running) { running.cancel(); running = null; }
    if (box.open) {
      running = body.animate([{ height: body.offsetHeight + "px", opacity: 1 }, { height: "0px", opacity: 0 }],
                             { duration: 240, easing: "cubic-bezier(.4, 0, .2, 1)" });
      running.onfinish = function () { box.open = false; running = null; };
      return;
    }
    // One smooth movement: the card grows down to its height, and the page
    // follows its foot frame by frame - never so far that the heading
    // leaves the top, under the sticky header.
    box.open = true;
    var h = body.offsetHeight;
    fly();
    running = body.animate([{ height: "0px", opacity: .4 }, { height: h + "px", opacity: 1 }],
                           { duration: 420, easing: "cubic-bezier(.2, .8, .2, 1)" });
    var follow = function () {
      var over = body.getBoundingClientRect().bottom + 24 - window.innerHeight;
      var room = sum.getBoundingClientRect().top - 76;
      if (over > 0 && room > 0) { window.scrollBy(0, Math.min(over, room)); }
    };
    var step = function () { if (running) { follow(); requestAnimationFrame(step); } };
    running.onfinish = function () { running = null; follow(); };
    requestAnimationFrame(step);
  });
})();

/* Feedback from the footer's link: what it is about, the app or this
 * website, a few words - to the admin page's Feedback part, as the app's
 * Send feedback does (POST /feedback/report, donate/feedback.py), marked as
 * from the website. Nothing about the visitor goes with it; the service
 * scrubs the words of addresses and e-mail addresses. Not a door for
 * spam: a field only a robot fills in, no sending in the first seconds, a
 * minute between two from one browser - and the service's own limits.
 * The words are in #feedback-i18n (render.jq).
 */
(function () {
  "use strict";
  var words = document.getElementById("feedback-i18n");
  if (!words || !window.fetch || typeof HTMLDialogElement !== "function") { return; }
  var T = words.dataset, dlg = null, el = {}, opened = 0, topic = "", where = "app";
  var TOPICS = ["bug", "idea", "like", "question"];
  function mk(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text) { n.textContent = text; }
    return n;
  }
  function chipRow(keys, pick, label) {
    var row = mk("div", "fbs-chips");
    row.setAttribute("role", "radiogroup");
    row.setAttribute("aria-label", label);
    keys.forEach(function (k) {
      var b = mk("button", "dn-chip fbs-chip", T[k]); b.type = "button";
      b.setAttribute("role", "radio"); b.dataset.key = k;
      b.addEventListener("click", function () { pick(k); mark(); });
      row.append(b);
    });
    return row;
  }
  function mark() {
    [].forEach.call(dlg.querySelectorAll(".fbs-chip"), function (b) {
      var on = b.dataset.key === topic || b.dataset.key === where;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-checked", on ? "true" : "false");
    });
    el.send.disabled = !topic || el.text.value.trim().length < 3;
  }
  function build() {
    dlg = mk("dialog", "dn fbs");
    dlg.setAttribute("aria-labelledby", "fbs-title");
    var head = mk("div", "dn-head"), ht = mk("div");
    var h = mk("h2", "", T.title); h.id = "fbs-title";
    ht.append(h, mk("p", "", T.intro));
    var x = mk("button", "dn-x", "×"); x.type = "button"; x.setAttribute("aria-label", T.close);
    x.addEventListener("click", close);
    head.append(ht, x);
    el.form = mk("div", "dn-step");
    var t1 = mk("p", "fbs-label", T.topic), t2 = mk("p", "fbs-label", T.where);
    el.text = mk("textarea", "dn-input fbs-text");
    Object.assign(el.text, { rows: 5, maxLength: 4000, placeholder: T.ph });
    el.text.setAttribute("aria-label", T.ph);
    el.text.addEventListener("input", mark);
    // Left empty by people (it is out of sight and out of the tab order);
    // a robot filling in every field gives itself away.
    var trap = mk("label", "fbs-trap"); trap.setAttribute("aria-hidden", "true");
    el.trap = mk("input"); Object.assign(el.trap, { type: "text", name: "website", tabIndex: -1, autocomplete: "off" });
    trap.append(el.trap);
    el.err = mk("p", "dn-error"); el.err.hidden = true; el.err.setAttribute("role", "alert");
    el.send = mk("button", "btn primary fbs-send", T.send); el.send.type = "button";
    el.send.addEventListener("click", send);
    el.form.append(t1, chipRow(TOPICS, function (k) { topic = k; }, T.topic),
                   t2, chipRow(["app", "site"], function (k) { where = k; }, T.where),
                   el.text, trap, el.err, el.send, mk("p", "dn-lock", T.privacy));
    el.done = mk("div", "dn-step fbs-done"); el.done.hidden = true;
    el.done.append(mk("p", "fbs-thanks", T.sent));
    var body = mk("div", "dn-body");
    body.append(el.form, el.done);
    dlg.append(head, body);
    dlg.addEventListener("cancel", function (e) { e.preventDefault(); close(); });
    dlg.addEventListener("click", function (e) { if (e.target === dlg) { close(); } });
    document.body.appendChild(dlg);
  }
  function open() {
    if (!dlg) { build(); }
    el.form.hidden = false; el.done.hidden = true; el.err.hidden = true;
    opened = Date.now();
    mark();
    dlg.showModal();
  }
  function close() { if (dlg && dlg.open) { dlg.close(); } }
  function fail(text) { el.err.textContent = text; el.err.hidden = false; el.send.disabled = false; }
  function last() { try { return Number(localStorage.getItem("fbs-last") || 0); } catch (e) { return 0; } }
  function thanked() {
    el.form.hidden = true; el.done.hidden = false;
    el.text.value = ""; topic = ""; mark();
  }
  function send() {
    var text = el.text.value.trim();
    if (!topic || text.length < 3) { return; }
    // A robot: thanked, and nothing sent.
    if (el.trap.value || Date.now() - opened < 3000) { thanked(); return; }
    if (Date.now() - last() < 60000) { fail(T.wait); return; }
    el.send.disabled = true; el.err.hidden = true;
    fetch("/feedback/report", { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Dopeiptv-Report": "1" },
      body: JSON.stringify({ kind: "feedback", from: "site", version: "site", os: "Web", text: text,
                             topic: topic, area: where === "site" ? "website" : "", hp: el.trap.value }) })
      .then(function (r) {
        if (r.ok) {
          try { localStorage.setItem("fbs-last", String(Date.now())); } catch (e) { /* none kept */ }
          thanked();
          return;
        }
        fail(r.status === 429 ? T.wait : T.failed);
      })
      .catch(function () { fail(T.failed); });
  }
  document.addEventListener("click", function (e) {
    var a = e.target.closest ? e.target.closest("a[data-feedback]") : null;
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) { return; }
    e.preventDefault();
    open();
  });
  if (window.location.hash === "#feedback") { open(); }
})();
