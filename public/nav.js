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

/* The donate dialog: Stripe's card form (the Payment Element) in a dialog on
 * this page, as on solequiz.com - nothing leaves the site but the payment
 * itself, which goes from Stripe's iframes straight to Stripe.
 *
 * Every donate link (the card, the header, the footer) points at Stripe's own
 * page, so with this file gone or no <dialog>, a click simply goes there.
 * Once the dialog is open it never leaves the page: a failure is a sentence
 * in the dialog and the reason in the console. The page holds the dialog's
 * words in #donate-i18n; amounts and the publishable key come from
 * /donate/config, the PaymentIntent from /donate/session.
 *
 * Apple Pay and Google Pay go as on solequiz.com: where the device has one,
 * the main button opens the wallet's own sheet (Stripe's Payment Request)
 * and /donate/wallet confirms the payment with what the wallet hands back;
 * "Pay with card" under it opens the card form.
 *
 * The wallet button has to be there when the dialog opens. Asked only on
 * opening, the config, Stripe.js and Stripe's wallet check ran one after
 * another, and the button turned from "Continue" into Apple Pay a second or
 * two later. So they start when a donate link is pointed at, focused or
 * touched - the visitor is about to open it - and run side by side; and the
 * button is the wallet's from the start, live as soon as Stripe is ready,
 * wherever the wallet is as good as certain: Apple Pay where Safari itself
 * says the device has it, Google Pay in Chrome (computer and Android alike),
 * and whatever Stripe answered last time on this device - which also keeps
 * "Continue" steady for a browser Stripe said has no wallet.
 *
 * The donor pays in their own currency: guessCurrency() picks it from where
 * the device says it is, out of the ones /donate/config offers, and a small
 * menu in the dialog's head changes it on either step - on the card form's
 * step the form follows at once (remembered on this device).
 */
(function () {
  "use strict";
  var words = document.getElementById("donate-i18n");
  if (!words || !window.fetch || !window.Promise ||
      typeof HTMLDialogElement !== "function") { return; }
  var T = words.dataset;
  var LANG = T.lang || document.documentElement.lang || "en";
  // The Stripe.js release solequiz.com runs on against the same account.
  var STRIPE_JS = "https://js.stripe.com/endive/stripe.js";
  var ZERO_DECIMAL = /^(bif|clp|djf|gnf|jpy|kmf|krw|mga|pyg|rwf|ugx|vnd|vuv|xaf|xof|xpf)$/;
  var cfg = null, cfgLoading = null, stripeLoading = null, dlg = null, el = {}, chosen = 0, live = null, busy = false, cur = "";
  var stripe = null, pr = null, wallet = null, walletAmount = 0, prepping = false;
  var APPLE = /iPhone|iPad|iPod|Macintosh/.test(navigator.userAgent);
  // Asked once: whoever turned motion down gets the dialog without the show.
  var CALM = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  var HEART = "M12 21s-7.5-4.6-9.6-9.2C.9 8.4 3 4.5 6.9 4.5c2.1 0 3.6 1.1 5.1 3 1.5-1.9 3-3 5.1-3 3.9 0 6 3.9 4.5 7.3C19.5 16.4 12 21 12 21z";

  function isDonate(a) {
    return !!a && /^https:\/\/(donate|buy)\.stripe\.com\//.test(a.href || "");
  }
  function mk(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text) { n.textContent = text; }
    return n;
  }
  // Small line icons, drawn in the text's colour.
  var ICONS = {
    card: ["M3.5 6.5a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z",
           "M3.5 9.5h17", "M7 15h4"],
    company: ["M4.5 20.5v-15a1 1 0 0 1 1-1h8a1 1 0 0 1 1 1v15", "M14.5 9.5h4a1 1 0 0 1 1 1v10",
              "M3 20.5h18", "M8 8h3", "M8 11.5h3", "M8 15h3", "M9.5 20.5v-2.5"],
    pencil: ["M4 20l1-4.2L15.6 5.2a2 2 0 0 1 2.8 0l.4.4a2 2 0 0 1 0 2.8L8.2 19 4 20z", "M13.8 7l3.2 3.2"]
  };
  function icon(name) {
    var NS = "http://www.w3.org/2000/svg", svg = document.createElementNS(NS, "svg");
    [["viewBox", "0 0 24 24"], ["class", "dn-ico"], ["aria-hidden", "true"], ["fill", "none"],
     ["stroke", "currentColor"], ["stroke-width", "1.8"], ["stroke-linecap", "round"],
     ["stroke-linejoin", "round"]].forEach(function (a) { svg.setAttribute(a[0], a[1]); });
    ICONS[name].forEach(function (d) {
      var path = document.createElementNS(NS, "path");
      path.setAttribute("d", d);
      svg.append(path);
    });
    return svg;
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

  // -- the heart's show --------------------------------------------------------
  // A heart pressed thumps, a ring goes out from it and small hearts spray
  // from it; the dialog grows out of it. On a thank-you, a bigger burst. The
  // sparks are drawn in a popover - the top layer, over the open dialog and
  // its dimmed backdrop - where the browser has one, else over the page.
  var SPARKS = ["#ff375f", "#ff6b8b", "#ff2d55", "#ff9eb5", "#ff453a"];
  function centre(node) {
    var r = node.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }
  function thump(node, big) {
    if (!node || !node.animate) { return; }
    if (CALM) {                     // less motion: it glows instead of beating
      node.animate([{ opacity: 1 }, { opacity: 0.45 }, { opacity: 1 }], { duration: big ? 900 : 620, easing: "ease-in-out" });
      return;
    }
    var k = big ? [1, 1.45, 0.92, 1.18, 1] : [1, 1.7, 0.85, 1.25, 1];
    node.animate(k.map(function (v) { return { transform: "scale(" + v + ")" }; }),
      { duration: big ? 900 : 620, easing: "ease-out" });
  }
  // A layer for the show, over everything: a popover is in the top layer,
  // above an open dialog and its backdrop. Without popovers, over the page
  // (or null, for what only makes sense on top).
  function showLayer(onTopOnly) {
    var layer = mk("div", "dn-burst");
    var top = typeof layer.showPopover === "function";
    if (!top && onTopOnly) { return null; }
    if (top) { layer.setAttribute("popover", "manual"); }
    document.body.append(layer);
    if (top) {
      try { layer.showPopover(); } catch (e) { if (onTopOnly) { layer.remove(); return null; } }
    }
    layer.done = function (ms) {
      setTimeout(function () {
        try { if (top) { layer.hidePopover(); } } catch (e) { /* gone already */ }
        layer.remove();
      }, ms);
    };
    return layer;
  }
  function burst(at, big, into, wait) {
    if (CALM || !at || !document.body.animate) { return; }
    wait = wait || 0;
    var layer = into || showLayer(false);
    var put = function (n) { n.style.left = at.x + "px"; n.style.top = at.y + "px"; layer.append(n); return n; };
    [0, 1].forEach(function (i) {
      put(mk("i", "dn-ring")).animate([
        { transform: "translate(-50%, -50%) scale(.2)", opacity: 0.85 },
        { transform: "translate(-50%, -50%) scale(" + (big ? 5 : 3) + ")", opacity: 0 }
      ], { duration: big ? 1000 : 700, delay: wait + i * (big ? 160 : 110), easing: "cubic-bezier(.2, .7, .2, 1)", fill: "forwards" });
    });
    var n = big ? 26 : 14, longest = 0;
    for (var i = 0; i < n; i++) {
      var h = put(heart("dn-spark"));
      h.style.color = SPARKS[i % SPARKS.length];
      var a = (i / n) * Math.PI * 2 + (Math.random() - 0.5) * 0.5;
      var d = (big ? 110 : 46) + Math.random() * (big ? 150 : 80);
      var dx = Math.cos(a) * d, dy = Math.sin(a) * d - (big ? 40 : 18);
      var size = (big ? 0.7 : 0.55) + Math.random() * (big ? 0.9 : 0.6);
      var rot = (Math.random() - 0.5) * 70, ms = (big ? 900 : 650) + Math.random() * 400;
      longest = Math.max(longest, ms);
      h.animate([
        { transform: "translate(-50%, -50%) scale(.1) rotate(0deg)", opacity: 1 },
        { opacity: 1, offset: 0.65 },
        { transform: "translate(calc(-50% + " + dx.toFixed(1) + "px), calc(-50% + " + dy.toFixed(1) + "px)) scale(" + size.toFixed(2) + ") rotate(" + rot.toFixed(0) + "deg)", opacity: 0 }
      ], { duration: ms, delay: wait, easing: "cubic-bezier(.12, .8, .3, 1)", fill: "forwards" });
    }
    if (!into) { layer.done(wait + longest + 250); }
    return longest;
  }

  // Pressing Donate: a big heart flies out of the one pressed to the middle
  // of the screen, beats twice - a ring going out at each beat - and opens
  // into the dialog, with a few sparks. About a second, once a page view:
  // the next time the dialog only grows out of the heart.
  var heroShown = false;
  function heroHeart(from) {
    if (heroShown || !from || !dlg.animate) { return false; }
    var layer = showLayer(true);
    if (!layer) { return false; }
    heroShown = true;
    var NS = "http://www.w3.org/2000/svg", big = heart("dn-hero");
    var defs = document.createElementNS(NS, "defs"), grad = document.createElementNS(NS, "linearGradient");
    [["id", "dn-hg"], ["x1", "0"], ["y1", "0"], ["x2", "1"], ["y2", "1"]].forEach(function (a) { grad.setAttribute(a[0], a[1]); });
    [["0", "#ff8aa5"], [".5", "#ff375f"], ["1", "#d70f45"]].forEach(function (st) {
      var stop = document.createElementNS(NS, "stop");
      stop.setAttribute("offset", st[0]); stop.setAttribute("stop-color", st[1]);
      grad.append(stop);
    });
    // Its glow is drawn in the heart itself, painted once: as a CSS filter
    // Chrome worked it out again on every frame of the show.
    var glow = document.createElementNS(NS, "filter"), drop = document.createElementNS(NS, "feDropShadow");
    [["id", "dn-hs"], ["x", "-60%"], ["y", "-60%"], ["width", "220%"], ["height", "220%"]].forEach(function (a) { glow.setAttribute(a[0], a[1]); });
    [["dx", "0"], ["dy", "2"], ["stdDeviation", "2.3"], ["flood-color", "#ff2d55"], ["flood-opacity", ".55"]].forEach(function (a) { drop.setAttribute(a[0], a[1]); });
    glow.append(drop); defs.append(grad, glow); big.prepend(defs);
    big.querySelector("path").setAttribute("fill", "url(#dn-hg)");
    big.querySelector("path").setAttribute("filter", "url(#dn-hs)");
    var size = Math.round(Math.min(240, window.innerWidth * 0.5, window.innerHeight * 0.4));
    var mid = { x: window.innerWidth / 2, y: window.innerHeight / 2 };
    big.style.width = big.style.height = size + "px";
    big.style.left = mid.x + "px"; big.style.top = mid.y + "px";
    layer.append(big);
    if (CALM) {
      // Less motion: the heart only lights up in the middle and fades into
      // the dialog. Nothing flies, grows or bursts.
      big.style.transform = "translate(-50%, -50%)";
      big.animate([{ opacity: 0 }, { opacity: 1, offset: 0.3 }, { opacity: 1, offset: 0.55 }, { opacity: 0 }],
        { duration: 1000, easing: "ease-in-out", fill: "both" });
      dlg.animate([{ opacity: 0 }, { opacity: 0, offset: 0.5 }, { opacity: 1 }], { duration: 1000 });
      try {
        dlg.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 380, pseudoElement: "::backdrop" });
      } catch (e) { /* the backdrop just appears */ }
      layer.done(1200);
      return true;
    }
    var T0 = 1150, at = function (f) { return Math.round(T0 * f); };
    var dx = (from.x - mid.x).toFixed(0), dy = (from.y - mid.y).toFixed(0);
    var c = "translate(-50%, -50%) ";
    big.animate([
      { transform: "translate(calc(-50% + " + dx + "px), calc(-50% + " + dy + "px)) scale(.05)", opacity: 0.7,
        easing: "cubic-bezier(.2, .9, .3, 1.25)" },
      { transform: c + "scale(1)", opacity: 1, offset: 0.34, easing: "ease-out" },
      { transform: c + "scale(1.15)", offset: 0.45, easing: "ease-in-out" },
      { transform: c + "scale(.96)", offset: 0.55, easing: "ease-in-out" },
      { transform: c + "scale(1.1)", offset: 0.65, easing: "ease-in-out" },
      { transform: c + "scale(1)", opacity: 1, offset: 0.74, easing: "cubic-bezier(.5, 0, .8, .4)" },
      { transform: c + "scale(1.9)", opacity: 0 }
    ], { duration: T0, fill: "both" });
    // a ring at each beat
    [0.45, 0.65].forEach(function (f) {
      var ring = mk("i", "dn-ring dn-ring--hero");
      ring.style.left = mid.x + "px"; ring.style.top = mid.y + "px";
      ring.style.width = ring.style.height = Math.round(size * 0.8) + "px";
      layer.prepend(ring);          // behind the heart: only what passes its edge shows
      ring.animate([
        { transform: c + "scale(.9)", opacity: 0.85 },
        { transform: c + "scale(2.3)", opacity: 0 }
      ], { duration: 700, delay: at(f) - 40, easing: "cubic-bezier(.2, .7, .2, 1)", fill: "forwards" });
    });
    // it opens: sparks, and the dialog where the heart was
    burst(mid, false, layer, at(0.76));
    var sheet = window.matchMedia && window.matchMedia("(max-width: 560px)").matches;
    // Hidden until the heart opens, then in. The curve goes on that last
    // step alone: on the whole animation it brought the dialog in half way,
    // behind the heart still beating.
    var hide = { opacity: 0, transform: sheet ? "translateY(100%)" : "scale(.82)" };
    dlg.animate([
      hide,
      Object.assign({ offset: at(0.72) / (at(0.72) + 420), easing: "cubic-bezier(.18, .9, .22, 1.08)" }, hide),
      { opacity: 1, transform: "none" }
    ], { duration: at(0.72) + 420 });
    try {
      dlg.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 380, pseudoElement: "::backdrop" });
    } catch (e) { /* the backdrop just appears */ }
    layer.done(T0 + 1400);
    return true;
  }
  // The dialog grows out of the heart (a phone's sheet rises from the bottom).
  function grow(from) {
    if (!dlg.animate) { return; }
    if (CALM) {                     // less motion: it fades in where it opens
      dlg.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 240, easing: "ease-out" });
      return;
    }
    var r = dlg.getBoundingClientRect();
    var sheet = r.bottom >= window.innerHeight - 2 && r.width >= window.innerWidth - 2;
    var start;
    if (sheet) {
      start = { transform: "translateY(100%)" };
    } else if (from) {
      start = { transform: "translate(" + (from.x - (r.left + r.width / 2)).toFixed(0) + "px, " +
                (from.y - (r.top + r.height / 2)).toFixed(0) + "px) scale(.08)", opacity: 0 };
    } else {
      start = { transform: "scale(.92)", opacity: 0 };
    }
    dlg.animate([start, { transform: "none", opacity: 1 }],
      { duration: sheet ? 420 : 560, easing: "cubic-bezier(.18, .9, .22, 1.08)" });
    try {
      dlg.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 320, pseudoElement: "::backdrop" });
    } catch (e) { /* the backdrop just appears */ }
  }
  function celebrate() {
    var big = el.done.querySelector(".dn-bigheart");
    if (!big) { return; }
    thump(big, true);
    burst(centre(big), true);
  }

  function code() { return cur || (cfg && cfg.currency) || "eur"; }
  // The amounts of the chosen currency: its buttons, smallest and largest.
  function C() {
    var row = cfg.currencies && cfg.currencies[code()];
    return row || { presets: cfg.presets || [], min: cfg.min, max: cfg.max };
  }
  function fmt(minor) {
    var c = code().toUpperCase();
    var major = ZERO_DECIMAL.test(c.toLowerCase()) ? minor : minor / 100;
    try {
      return new Intl.NumberFormat(LANG, { style: "currency", currency: c,
        minimumFractionDigits: major % 1 ? 2 : 0, maximumFractionDigits: major % 1 ? 2 : 0 }).format(major);
    } catch (e) { return major + " " + c; }
  }
  function toMinor(text) {
    var v = parseFloat(String(text).replace(/\s/g, "").replace(",", "."));
    if (!isFinite(v)) { return 0; }
    if (ZERO_DECIMAL.test(code())) { return Math.round(v); }
    if (/^(huf|twd|idr)$/.test(code())) { return Math.round(v) * 100; }   // whole units only
    return Math.round(v * 100);
  }

  // Where the device says it is: its time zone first (a place, where the
  // language is only a preference), then the regions of its languages,
  // then the part of the world its time zone is in, then the page's language.
  var EURO = " AT BE BG CY DE EE ES FI FR GR HR IE IT LT LU LV MT NL PT SI SK AD MC SM VA ME XK AX EU ";
  var BY_COUNTRY = { SE: "sek", NO: "nok", SJ: "nok", DK: "dkk", FO: "dkk", GL: "dkk",
    GB: "gbp", IM: "gbp", JE: "gbp", GG: "gbp", CH: "chf", LI: "chf", PL: "pln", CZ: "czk",
    HU: "huf", RO: "ron", US: "usd", PR: "usd", AU: "aud", CA: "cad", NZ: "nzd", JP: "jpy",
    KR: "krw", BR: "brl", MX: "mxn", IL: "ils", CN: "cny", HK: "hkd", MO: "hkd", TW: "twd",
    SG: "sgd", IN: "inr", ID: "idr", TH: "thb", VN: "vnd", PH: "php", MY: "myr", PK: "pkr",
    AE: "aed", SA: "sar", EG: "egp", TR: "try", UA: "uah", RS: "rsd", ZA: "zar", KE: "kes",
    TZ: "tzs", CO: "cop", CL: "clp" };
  var BY_ZONE = { "Europe/Stockholm": "SE", "Europe/Oslo": "NO", "Arctic/Longyearbyen": "NO",
    "Europe/Copenhagen": "DK", "Atlantic/Faroe": "FO", "America/Nuuk": "GL", "America/Godthab": "GL",
    "Europe/London": "GB", "Europe/Belfast": "GB", "Europe/Isle_of_Man": "GB", "Europe/Jersey": "GB",
    "Europe/Guernsey": "GB", "Europe/Zurich": "CH", "Europe/Vaduz": "CH", "Europe/Warsaw": "PL",
    "Europe/Prague": "CZ", "Europe/Budapest": "HU", "Europe/Bucharest": "RO", "Asia/Tokyo": "JP",
    "Asia/Seoul": "KR", "Pacific/Auckland": "NZ", "Asia/Jerusalem": "IL", "Asia/Tel_Aviv": "IL",
    "Pacific/Honolulu": "US", "America/Puerto_Rico": "PR",
    "Asia/Shanghai": "CN", "Asia/Chongqing": "CN", "Asia/Harbin": "CN", "Asia/Urumqi": "CN",
    "Asia/Hong_Kong": "HK", "Asia/Macau": "MO", "Asia/Taipei": "TW", "Asia/Singapore": "SG",
    "Asia/Kolkata": "IN", "Asia/Calcutta": "IN", "Asia/Jakarta": "ID", "Asia/Makassar": "ID",
    "Asia/Jayapura": "ID", "Asia/Pontianak": "ID", "Asia/Bangkok": "TH", "Asia/Ho_Chi_Minh": "VN",
    "Asia/Saigon": "VN", "Asia/Manila": "PH", "Asia/Kuala_Lumpur": "MY", "Asia/Kuching": "MY",
    "Asia/Karachi": "PK", "Asia/Dubai": "AE", "Asia/Riyadh": "SA", "Africa/Cairo": "EG",
    "Europe/Istanbul": "TR", "Asia/Istanbul": "TR", "Europe/Kyiv": "UA", "Europe/Kiev": "UA",
    "Europe/Uzhgorod": "UA", "Europe/Zaporozhye": "UA", "Europe/Belgrade": "RS",
    "Africa/Johannesburg": "ZA", "Africa/Nairobi": "KE", "Africa/Dar_es_Salaam": "TZ",
    "America/Bogota": "CO", "America/Santiago": "CL", "America/Punta_Arenas": "CL", "Pacific/Easter": "CL" };
  var ZONE_RX = [
    [/^Europe\/(Berlin|Paris|Madrid|Rome|Amsterdam|Brussels|Vienna|Dublin|Lisbon|Athens|Luxembourg|Helsinki|Tallinn|Riga|Vilnius|Bratislava|Ljubljana|Zagreb|Malta|Monaco|San_Marino|Vatican|Andorra|Sofia|Mariehamn|Busingen|Podgorica|Nicosia)$|^Asia\/(Nicosia|Famagusta)$|^Atlantic\/(Canary|Madeira|Azores)$|^Africa\/Ceuta$/, "EU"],
    [/^Australia\//, "AU"],
    [/^America\/(Toronto|Vancouver|Edmonton|Winnipeg|Halifax|St_Johns|Regina|Montreal|Moncton|Whitehorse|Yellowknife|Iqaluit|Glace_Bay|Goose_Bay|Swift_Current|Dawson_Creek|Fort_Nelson|Creston|Inuvik|Thunder_Bay|Rankin_Inlet|Cambridge_Bay|Atikokan)$/, "CA"],
    [/^America\/(Sao_Paulo|Bahia|Fortaleza|Recife|Belem|Manaus|Cuiaba|Campo_Grande|Porto_Velho|Boa_Vista|Rio_Branco|Maceio|Araguaina|Santarem|Noronha|Eirunepe)$/, "BR"],
    [/^America\/(Mexico_City|Monterrey|Merida|Cancun|Chihuahua|Hermosillo|Mazatlan|Tijuana|Matamoros|Ojinaga|Bahia_Banderas|Ciudad_Juarez)$/, "MX"],
    [/^America\/(New_York|Chicago|Denver|Los_Angeles|Phoenix|Anchorage|Detroit|Boise|Juneau|Sitka|Nome|Adak|Metlakatla|Yakutat|Menominee|Indiana\/.+|Kentucky\/.+|North_Dakota\/.+)$|^US\//, "US"]];
  var BY_LANG = { sv: "SE", ja: "JP", ko: "KR", pl: "PL", he: "IL", zh: "CN", hi: "IN", ur: "PK",
    id: "ID", th: "TH", vi: "VN", tr: "TR", uk: "UA", sr: "RS", sw: "KE" };
  function offered(c) { return !!(c && cfg.currencies && cfg.currencies[c]); }
  function currencyOf(country) {
    country = (country || "").toUpperCase();
    var c = BY_COUNTRY[country] || (country && EURO.indexOf(" " + country + " ") >= 0 ? "eur" : "");
    return offered(c) ? c : "";
  }
  function guessCurrency() {
    var saved = "";
    try { saved = localStorage.getItem("dn-currency") || ""; } catch (e) { /* none */ }
    if (offered(saved)) { return saved; }
    var tz = "";
    try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) { /* none */ }
    var country = BY_ZONE[tz] || "";
    ZONE_RX.forEach(function (z) { if (!country && z[0].test(tz)) { country = z[1]; } });
    var got = currencyOf(country);
    (navigator.languages || [navigator.language || ""]).forEach(function (l) {
      var m = /^[a-z]{2,3}[-_]([A-Za-z]{2})\b/.exec(l || "");
      if (!got && m) { got = currencyOf(m[1]); }
    });
    if (!got && /^(Europe|Africa|Atlantic)\//.test(tz) && offered("eur")) { got = "eur"; }
    if (!got && /^(America|Asia|Pacific|Indian)\//.test(tz) && offered("usd")) { got = "usd"; }
    return got || currencyOf(BY_LANG[LANG]) || cfg.currency || "eur";
  }
  function loadStripe() {
    if (window.Stripe) { return Promise.resolve(window.Stripe); }
    if (!stripeLoading) {
      stripeLoading = new Promise(function (ok, fail) {
        var s = document.createElement("script");
        s.src = STRIPE_JS;
        s.onload = function () { ok(window.Stripe); };
        s.onerror = function () { stripeLoading = null; s.remove(); fail(new Error("stripe.js")); };
        document.head.appendChild(s);
      });
    }
    return stripeLoading;
  }
  // /donate/config once, shared by the warm-up and the dialog.
  function getConfig() {
    if (cfg) { return Promise.resolve(cfg); }
    if (!cfgLoading) {
      cfgLoading = fetch("/donate/config", { credentials: "same-origin" })
        .then(function (r) { if (!r.ok) { throw new Error("/donate/config answered " + r.status); } return r.json(); })
        .then(function (c) { cfg = c; if (!offered(cur)) { cur = guessCurrency(); } return c; },
              function (err) { cfgLoading = null; throw err; });
    }
    return cfgLoading;
  }
  // Safari's own answer, at once and without Stripe: whether this device can
  // pay with Apple Pay at all. Stripe's check (a card in the Wallet, the
  // domain registered) comes after and has the last word.
  function applePayLikely() {
    try { return APPLE && !!window.ApplePaySession && ApplePaySession.canMakePayments(); }
    catch (e) { return false; }
  }
  // The wallet to show before Stripe has answered, or null. Chrome is not
  // asked itself: a canMakePayment() of our own for Google Pay, with other
  // data than Stripe's, can use up Chrome's quota and make Stripe's own
  // check fail - so Chrome is taken at its word until Stripe says otherwise.
  function likelyWallet() {
    var said = "";
    try { said = localStorage.getItem("dn-wallet") || ""; } catch (e) { /* none */ }
    if (said === "apple" || said === "google") { return said; }
    if (said === "none") { return null; }
    if (applePayLikely()) { return "apple"; }
    var ua = navigator.userAgent;
    if (window.PaymentRequest && /Chrome\//.test(ua) &&
        !/Edg\/|EdgA\/|OPR\/|SamsungBrowser|YaBrowser|Firefox/.test(ua)) { return "google"; }
    return null;
  }
  // The main button as the wallet's (kind "apple"/"google"; waiting: shown,
  // not yet live) or, with no wallet, as the card button it then is - it
  // opens the card form, which "Continue" did not say.
  function walletLook(kind, waiting) {
    el.go.classList.remove("dn-wallet", "dn-wallet--apple", "dn-wallet--google", "dn-wallet--wait");
    el.go.classList.add("dn-wallet");
    if (kind) {
      el.go.textContent = kind === "apple" ? T.apple.replace("Apple Pay", "\uF8FF\u00A0Pay") : T.google;
      el.go.classList.add("dn-wallet--" + kind);
      if (waiting) { el.go.classList.add("dn-wallet--wait"); }
    } else {
      el.go.replaceChildren(icon("card"), document.createTextNode(T.card));
    }
    el.go.disabled = !!waiting;
    el.card.hidden = !kind;
  }
  // Pointed at, focused or touched: get everything ready before the click.
  function warm() {
    if (!dlg) { build(); }
    loadStripe().catch(function () { /* said where it matters */ });
    getConfig().then(prepareWallet).catch(function () { /* open() says so */ });
  }
  function css(name, dflt) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || dflt;
  }
  function appearance() {
    var dark = css("color-scheme", "dark").indexOf("light") < 0;
    return {
      theme: dark ? "night" : "stripe",
      variables: {
        colorPrimary: css("--accent", "#2997ff"), colorText: css("--text", "#f5f5f7"),
        colorTextSecondary: css("--muted", "#aeaeb4"), colorBackground: css("--surface", "#202023"),
        colorDanger: "#ff6b6b", borderRadius: "12px", spacingUnit: "4px", fontSizeBase: "15px",
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
      },
      rules: {
        ".Input": { backgroundColor: css("--ink", "#0e0e10"), border: "1px solid " + css("--line-2", "#45454b"), boxShadow: "none" },
        ".Input:focus": { border: "1px solid " + css("--accent", "#2997ff"), boxShadow: "0 0 0 1px " + css("--accent", "#2997ff") },
        ".Label": { fontWeight: "600" }
      }
    };
  }
  // What went wrong goes to the console, for whoever looks after the site;
  // the visitor gets a plain sentence and stays on the page.
  function warn(what, err) { try { console.warn("[donate] " + what, err || ""); } catch (e) { /* none */ } }

  function build() {
    dlg = mk("dialog", "dn");
    dlg.setAttribute("aria-labelledby", "dn-title");
    var head = mk("div", "dn-head"), ht = mk("div");
    var h = mk("h2", "", T.title); h.id = "dn-title";
    h.prepend(heart("dn-titleheart"));
    ht.append(h, mk("p", "", T.sub));
    var x = mk("button", "dn-x", "×"); x.type = "button"; x.setAttribute("aria-label", T.close);
    x.addEventListener("click", close);
    el.curwrap = mk("span", "dn-curwrap");
    el.cur = mk("select", "dn-cur");
    el.cur.setAttribute("aria-label", T.currency || "Currency");
    el.cur.addEventListener("change", function () { setCurrency(el.cur.value, true); });
    el.curwrap.append(el.cur);
    head.append(ht, el.curwrap, x);

    el.amounts = mk("div", "dn-step");
    el.chips = mk("div", "dn-chips");
    el.chips.setAttribute("role", "radiogroup");
    var other = mk("label", "dn-other", T.other);
    el.custom = mk("input", "dn-input");
    Object.assign(el.custom, { type: "text", inputMode: "decimal", autocomplete: "off" });
    el.custom.addEventListener("input", function () { setAmount(toMinor(el.custom.value), true); });
    other.append(el.custom);
    el.err1 = mk("p", "dn-error"); el.err1.hidden = true; el.err1.setAttribute("role", "alert");
    el.go = mk("button", "btn primary dn-go"); el.go.type = "button";
    el.go.addEventListener("click", function () {
      if (wallet) { payWallet(); } else { toPay(); }
    });
    // A button of its own with a card on it: as a small link under a wallet
    // button it was easy to miss.
    el.card = mk("button", "btn dn-card"); el.card.type = "button"; el.card.hidden = true;
    el.card.append(icon("card"), document.createTextNode(T.card));
    walletLook(null, false);        // the card button until a wallet turns up
    el.card.addEventListener("click", toPay);
    // A larger gift than the dialog takes: a line to write instead (a bank
    // transfer to the company, no card fees), under the range error - shown
    // only once someone types more than the cap. Up from the start it read
    // as "is this enough?" before anyone had chosen. The cap stays - it
    // keeps stolen cards from trying big amounts.
    el.more1 = moreLine();
    el.amounts.append(el.chips, other, el.err1);
    if (el.more1) { el.amounts.append(el.more1); }
    el.amounts.append(el.go, el.card);

    el.pay = mk("div", "dn-step"); el.pay.hidden = true;
    // The amount stays open here: the same choices, smaller, and an own
    // amount - the card form and the button follow at once.
    var top = mk("div", "dn-paytop");
    el.chips2 = mk("div", "dn-chips dn-chips--sm");
    el.chips2.setAttribute("role", "radiogroup");
    el.custom2 = mk("input", "dn-input dn-input--sm");
    Object.assign(el.custom2, { type: "text", inputMode: "decimal", autocomplete: "off" });
    el.custom2.setAttribute("aria-label", T.other);
    el.custom2.placeholder = T.othershort || T.other;
    el.custom2.addEventListener("input", function () { setAmount(toMinor(el.custom2.value), true); });
    // A box to write in, not a fifth amount: a dashed edge and a pencil
    // while it is empty. "SEK …" there read as one more amount, cut short.
    var own = mk("label", "dn-own");
    own.append(el.custom2, icon("pencil"));
    top.append(el.chips2, own);
    var em = mk("label", "dn-email", T.email);
    el.email = mk("input", "dn-input");
    Object.assign(el.email, { type: "email", name: "email", autocomplete: "email", required: true, spellcheck: false });
    em.append(el.email);
    el.fields = mk("div", "dn-fields");
    el.err2 = mk("p", "dn-error"); el.err2.hidden = true; el.err2.setAttribute("role", "alert");
    el.payBtn = mk("button", "btn primary dn-paybtn"); el.payBtn.type = "button"; el.payBtn.disabled = true;
    el.payBtn.addEventListener("click", pay);
    el.more2 = moreLine();
    el.pay.append(top, em, el.fields, el.err2);
    if (el.more2) { el.pay.append(el.more2); }
    el.pay.append(el.payBtn, mk("p", "dn-lock", T.lock));

    el.loading = mk("p", "dn-loading", T.loading); el.loading.hidden = true;
    el.done = mk("div", "dn-step dn-done"); el.done.hidden = true;
    var bigHeart = mk("p", "dn-heart"); bigHeart.append(heart("dn-bigheart"));
    el.done.append(bigHeart, mk("h3", "", T.thanks), mk("p", "", T.receipt));

    var body = mk("div", "dn-body");
    body.append(el.loading, el.amounts, el.pay, el.done);
    // Who is paid: the company behind dopeIPTV, the name on the statement.
    // With its logotype that is the name, as in the footer and the app's
    // About: "A project by Solecraft" - an icon beside the words said less.
    if (T.payee && T.payeenote) {
      el.payee = mk("p", "dn-payee");
      var mark = icon("company"), name = document.createTextNode(T.payee);
      if (T.payeelogo) {
        name = mk("span", "dn-sole-logo", T.payee);    // the text is for screen readers
        name.style.setProperty("--logo", "url(" + T.payeelogo + ")");
        mark = null;
      }
      // The company's mark and name lead to its site, in a new tab, so the
      // dialog - and a payment begun in it - stays where it is.
      var toPayee = function (child, label) {
        if (!T.payeeurl) { return child; }
        var a = mk("a", "dn-payee-link" + (T.payeelogo && child === name ? " dn-sole" : ""));
        Object.assign(a, { href: T.payeeurl, target: "_blank", rel: "noopener" });
        if (label) { a.setAttribute("aria-label", label); }
        a.append(child);
        return a;
      };
      var parts = T.payeenote.split("{payee}"), words = mk("span");
      if (parts.length > 1) {
        words.append(parts[0], toPayee(name, T.payee), parts.slice(1).join(T.payee));
      } else {
        words.textContent = T.payeenote;
        if (mark === null) { words.append(" ", toPayee(name, T.payee)); }
      }
      if (mark) { el.payee.append(toPayee(mark, T.payee)); }
      el.payee.append(words);
      body.append(el.payee);
    }
    dlg.append(head, body);
    dlg.addEventListener("cancel", function (e) { e.preventDefault(); close(); });
    dlg.addEventListener("click", function (e) { if (e.target === dlg) { close(); } });
    document.body.appendChild(dlg);
  }
  function show(step) {
    var thanks = step === el.done && el.done.hidden;
    [el.loading, el.amounts, el.pay, el.done].forEach(function (s) { s.hidden = s !== step; });
    if (thanks) { requestAnimationFrame(function () { requestAnimationFrame(celebrate); }); }
    el.curwrap.hidden = !el.manyCur || step === el.loading || step === el.done;
    if (el.payee) { el.payee.hidden = step === el.done; }
  }
  function mark() {
    [el.chips, el.chips2].forEach(function (box) {
      [].forEach.call(box.children, function (c) {
        var on = Number(c.dataset.amount) === chosen;
        c.classList.toggle("is-on", on);
        c.setAttribute("aria-checked", on ? "true" : "false");
      });
    });
  }
  function chips() {
    [el.chips, el.chips2].forEach(function (box) {
      box.replaceChildren();
      (C().presets || []).forEach(function (minor) {
        var b = mk("button", "dn-chip", fmt(minor)); b.type = "button";
        b.setAttribute("role", "radio"); b.dataset.amount = String(minor);
        b.addEventListener("click", function () { setAmount(minor, false); });
        box.appendChild(b);
      });
    });
    var p = C().presets || [];
    if (!chosen && p.length) { chosen = p[Math.min(1, p.length - 1)]; }
    mark();
  }
  function currencies() {
    var list = Object.keys(cfg.currencies || {}).sort();
    el.cur.replaceChildren();
    list.forEach(function (c) {
      var o = mk("option", "", c.toUpperCase()); o.value = c;
      el.cur.appendChild(o);
    });
    el.cur.value = code();
    el.manyCur = list.length > 1;
  }
  // A new currency: its own round amounts, the second one chosen.
  function setCurrency(c, mine) {
    if (!offered(c)) { return; }
    cur = c;
    if (mine) { try { localStorage.setItem("dn-currency", c); } catch (e) { /* none */ } }
    chosen = 0; el.custom.value = ""; el.custom2.value = "";
    el.err1.hidden = true; el.err2.hidden = true;
    if (el.cur.value !== c) { el.cur.value = c; }
    chips();
    // On the card form's step the form follows: a deferred intent takes a
    // new currency as it takes a new amount.
    if (live && live.elements) {
      try { live.elements.update({ currency: c, amount: chosen }); }
      catch (e) { warn("the card form did not take the currency:", e); }
    }
    if (pr) {
      try { pr.update({ currency: c, total: { label: cfg.name || T.title, amount: chosen || C().min } }); }
      catch (e) { warn("the wallet is made again for the currency:", e); pr = null; wallet = null; prepareWallet(); }
    }
    payLabel();
  }
  function inRange(minor) { return !!minor && minor >= C().min && minor <= C().max; }
  function moreLine() {
    if (!(T.more && T.moremail && T.more.indexOf("{email}") >= 0)) { return null; }
    var more = mk("p", "dn-more"), at = T.more.split("{email}");
    var mail = mk("a", "", T.moremail);
    mail.href = "mailto:" + T.moremail + "?subject=" + encodeURIComponent("dopeIPTV");
    more.append(at[0], mail, at.slice(1).join(""));
    more.hidden = true;
    return more;
  }
  // The "give more" line: only for an amount above the cap.
  function showMore() {
    var over = !!chosen && !!cfg && chosen > C().max;
    if (el.more1) { el.more1.hidden = !over; }
    if (el.more2) { el.more2.hidden = !over; }
  }
  function rangeText() {
    return (T.range || "").replace("{min}", fmt(C().min)).replace("{max}", fmt(C().max));
  }
  // Any change of amount, on either step: the chips, the button, and the
  // card form (a deferred intent - the PaymentIntent is made on "Donate").
  function setAmount(minor, typed) {
    chosen = minor;
    if (!typed) { el.custom.value = ""; el.custom2.value = ""; }
    el.err1.hidden = true; el.err2.hidden = true;
    mark();
    if (live && live.elements && inRange(minor)) { live.elements.update({ amount: minor }); }
    payLabel();
    showMore();
  }
  function payLabel() {
    if (!cfg) { return; }
    el.payBtn.textContent = (T.pay || "").replace("{amount}", fmt(chosen || 0));
    var ok = inRange(chosen);
    if (!ok && el.pay && !el.pay.hidden && chosen) { el.err2.textContent = rangeText(); el.err2.hidden = false; }
    el.payBtn.disabled = busy || !ok || !live || !live.complete;
  }
  function teardown() {
    if (live) { try { live.destroy(); } catch (e) { /* gone already */ } }
    live = null; el.fields.replaceChildren();
  }
  function close() {
    teardown(); busy = false; el.cur.disabled = false;
    if (dlg && dlg.open) { dlg.close(); }
  }

  function open(href, from) {
    if (!dlg) { build(); }
    if (typeof iptvHit === "function") { iptvHit("donate"); }
    el.loading.textContent = T.loading;
    show(el.loading);
    dlg.showModal();
    var hero = heroHeart(from);
    if (!hero) { grow(from); }
    open.hero = hero;
    // Stripe.js loads beside the config, not after it.
    if (window.ApplePaySession || window.PaymentRequest) { loadStripe().catch(function () { /* below */ }); }
    getConfig()
      .then(function () {
        currencies(); chips(); prepareWallet(); show(el.amounts);
      })
      .catch(function (err) {
        warn("the donate service did not answer (donate.py, nginx /donate/):", err);
        el.loading.textContent = T.failed;
      });
  }

  function amountOk() {
    if (!inRange(chosen)) {
      el.err1.textContent = rangeText();
      el.err1.hidden = false;
      return false;
    }
    return true;
  }
  function stripeFor(Stripe, key) {
    if (!stripe) { stripe = Stripe(key, { locale: LANG }); }
    return stripe;
  }
  // The wallet button, where there is a wallet: made ready when the dialog
  // opens, so a click can open the sheet at once (it must, inside the click).
  function prepareWallet() {
    if (wallet || !cfg.publishableKey || !(window.ApplePaySession || window.PaymentRequest)) { return; }
    var guess = likelyWallet();
    if (guess) { walletLook(guess, !pr); }
    if (pr || prepping) { return; }        // made already, or on its way
    prepping = true;
    loadStripe().then(function (Stripe) {
      var st = stripeFor(Stripe, cfg.publishableKey);
      pr = st.paymentRequest({
        country: cfg.country || "SE", currency: code(),
        total: { label: cfg.name || T.title, amount: chosen || C().min },
        requestPayerEmail: true, requestPayerName: true,
        disableWallets: ["link", "browserCard"]
      });
      pr.on("paymentmethod", walletPaid);
      return pr.canMakePayment().then(function (can) {
        prepping = false;
        wallet = can && ((APPLE && can.applePay && "apple") || (can.googlePay && "google")) || null;
        walletLook(wallet, false);
        try { localStorage.setItem("dn-wallet", wallet || "none"); } catch (e) { /* none */ }
      });
    }).catch(function () { prepping = false; pr = null; wallet = null; walletLook(null, false); });
  }
  function payWallet() {
    if (!amountOk() || !pr) { return; }
    walletAmount = chosen;
    pr.update({ currency: code(), total: { label: cfg.name || T.title, amount: walletAmount } });
    pr.show();
  }
  function donated() {
    if (typeof iptvHit === "function") { iptvHit("donated"); }
  }
  function walletDone() { donated(); show(el.done); }
  function walletFailed(msg) {
    show(el.amounts);
    el.err1.textContent = msg || T.failed; el.err1.hidden = false;
  }
  function walletPaid(ev) {
    fetch("/donate/wallet", { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount: walletAmount, currency: code(), paymentMethod: ev.paymentMethod.id,
        email: ev.payerEmail || "", name: ev.payerName || "" }) })
      .then(function (r) { return r.json().catch(function () { return {}; }); })
      .then(function (res) {
        if (res.ok) { ev.complete("success"); walletDone(); return; }
        if (res.action) {
          ev.complete("success");
          show(el.loading);
          var next = stripe.handleNextAction ? stripe.handleNextAction({ clientSecret: res.action })
                                             : stripe.confirmCardPayment(res.action);
          return next.then(function (r) {
            if (r.error) { walletFailed(r.error.message); } else { walletDone(); }
          });
        }
        ev.complete("fail");
        walletFailed(res.error && res.error !== "stripe" ? res.error : "");
      }, function () { ev.complete("fail"); walletFailed(); });
  }

  function toPay() {
    if (!amountOk()) { return; }
    el.loading.textContent = T.loading;
    show(el.loading);
    el.err2.hidden = true;
    el.custom2.value = el.custom.value;
    loadStripe().catch(function () {
      throw new Error("Stripe.js did not load - is https://js.stripe.com in the site's Content-Security-Policy?");
    }).then(function (Stripe) {
      var st = stripeFor(Stripe, cfg.publishableKey);
      // A deferred intent: the form is made for an amount that can still
      // change; the PaymentIntent is made with the final one on "Donate".
      var opts = { mode: "payment", amount: chosen, currency: code(),
                   appearance: appearance() };
      if (cfg.pmc) { opts.paymentMethodConfiguration = cfg.pmc; }
      var elements = st.elements(opts);
      // The wallets have their own button above, as on solequiz.com.
      var pe = elements.create("payment", { layout: { type: "accordion", defaultCollapsed: false },
        // Link (Stripe's saved cards, any browser - Linux and Firefox too)
        // shows once it is on in the dopeIPTV payment method configuration.
        paymentMethodOrder: ["card", "link", "paypal", "revolut_pay", "amazon_pay", "klarna"],
        wallets: { applePay: "never", googlePay: "never" } });
      teardown();
      live = { elements: elements, complete: false, destroy: function () { pe.destroy(); } };
      show(el.pay);
      pe.mount(el.fields);
      pe.on("change", function (ev) { live.complete = !!ev.complete; payLabel(); });
      pe.on("loaderror", function (ev) {
        warn("the card form did not load:", ev && ev.error);
        fail();
      });
      mark();
      payLabel();
    }).catch(function (err) {
      warn("could not open the card form:", err);
      show(el.amounts);
      el.err1.textContent = T.failed; el.err1.hidden = false;
    });
  }

  function fail(msg) {
    el.err2.textContent = msg || T.failed; el.err2.hidden = false;
  }
  function payAgain(msg) {
    busy = false; el.cur.disabled = false;
    payLabel();
    fail(msg);
  }
  function pay() {
    if (!live || !live.elements || busy) { return; }
    if (!inRange(chosen)) { payLabel(); return; }
    if (!el.email.checkValidity() || !el.email.value.trim()) {
      fail(T.emailmissing); el.email.focus(); return;
    }
    busy = true; el.err2.hidden = true; el.cur.disabled = true;
    el.payBtn.disabled = true; el.payBtn.textContent = T.busy;
    var elements = live.elements, amount = chosen, currency = code();
    elements.submit().then(function (r) {
      if (r && r.error) { throw { shown: r.error.message }; }
      return fetch("/donate/session", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ amount: amount, currency: currency }) })
        .then(function (res) {
          if (!res.ok) { throw new Error("/donate/session answered " + res.status + " (the reason is in /var/log/iptv-donate.log)"); }
          return res.json();
        });
    }).then(function (sess) {
      return stripe.confirmPayment({
        elements: elements,
        clientSecret: sess.clientSecret,
        redirect: "if_required",          // only a method that must leave the page (Klarna and the like)
        confirmParams: {
          return_url: window.location.origin + "/?donated=1#donate",
          receipt_email: el.email.value.trim()
        }
      });
    }).then(function (r) {
      if (r && r.error) { payAgain(r.error.message); return; }
      var state = r && r.paymentIntent && r.paymentIntent.status;
      if (state === "succeeded" || state === "processing") { teardown(); busy = false; donated(); show(el.done); return; }
      warn("payment ended as", state);
      payAgain();
    }).catch(function (err) {
      if (err && err.shown) { payAgain(err.shown); return; }
      warn("the payment did not go through:", err);
      payAgain();
    });
  }

  function intent(e) {
    var a = e.target && e.target.closest ? e.target.closest("a[href]") : null;
    if (isDonate(a)) { warm(); }
  }
  document.addEventListener("pointerover", intent, { passive: true });
  document.addEventListener("focusin", intent);
  document.addEventListener("touchstart", intent, { passive: true });

  document.addEventListener("click", function (e) {
    var a = e.target.closest ? e.target.closest("a[href]") : null;
    if (!isDonate(a) || e.defaultPrevented || e.button !== 0 ||
        e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) { return; }
    e.preventDefault();
    var menu = a.closest("details");
    var mark = a.querySelector("svg") || a;
    var from = centre(mark);
    if (menu) { menu.open = false; }
    thump(mark, false);
    open(a.href, from);
    if (!open.hero) { burst(from, false); }
  });

  // The donate band on the page beats and drifts only while it is in view.
  var band = document.querySelector(".donate");
  if (band && "IntersectionObserver" in window) {
    new IntersectionObserver(function (seen) {
      seen.forEach(function (s) { band.classList.toggle("is-live", s.isIntersecting); });
    }, { threshold: 0.2 }).observe(band);
  }

  // -- the owner's note --------------------------------------------------------
  // In a browser that has opened /admin/ (which leaves iptv_admin_hint, no
  // secret), new donations show at the foot of the site's pages too: a
  // heart, how much, from whom, a link to the statistics. Dismissed - or the
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
      body.append(mk("strong", "", rows.length === 1 ? "New donation" : rows.length + " new donations"));
      var ul = mk("ul", "owner-list");
      rows.slice(0, 3).forEach(function (r) {
        var li = mk("li");
        li.append(mk("b", "", money(r.amount, r.currency)),
                  document.createTextNode([ago(r.t), r.who].filter(Boolean).map(function (x) { return " · " + x; }).join("")));
        ul.append(li);
      });
      if (rows.length > 3) { ul.append(mk("li", "owner-more", "and " + (rows.length - 3) + " more")); }
      var link = mk("a", "owner-link", "Open statistics");
      link.href = "/admin/#donations";
      link.addEventListener("click", function () { away(rows); });
      body.append(ul, link);
      var shut = mk("button", "owner-close", "\u00d7");
      shut.type = "button";
      shut.setAttribute("aria-label", "Dismiss");
      shut.addEventListener("click", function () { away(rows); });
      note.append(mark1, body, shut);
      document.body.append(note);
      setTimeout(function () { thump(mark1, false); }, 380);
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

  // Back from a payment method that left the page (Klarna and the like).
  if (/[?&]donated=1(&|$)/.test(window.location.search)) {
    try {
      history.replaceState(null, "", window.location.pathname +
        window.location.search.replace(/([?&])donated=1(&|$)/, "$1").replace(/[?&]$/, "") +
        window.location.hash);
    } catch (e) { /* the address stays; harmless */ }
    build(); show(el.done); dlg.showModal();
  } else if (window.location.hash === "#donate") {
    // A link from elsewhere to the donate section - GitHub's Sponsor button,
    // the README, the release notes: dopeiptv.org/#donate - opens the dialog.
    open();
  }
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
 * slides down and a small TV in it switches on, the projects coming in as
 * its channels (style.css .built-more); closed, it slides back up. The page
 * follows the card down. Without motion, it simply opens and closes. */
(function () {
  "use strict";
  var box = document.querySelector(".built-more");
  if (!box || !box.animate) { return; }
  var sum = box.querySelector("summary"), body = box.querySelector(".built-body");
  var still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var running = null;
  sum.addEventListener("click", function (e) {
    if (still || !body) { return; }
    e.preventDefault();
    if (running) { running.cancel(); running = null; }
    if (box.open) {
      running = body.animate([{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateY(-10px)" }],
                             { duration: 220, easing: "cubic-bezier(.4, 0, 1, 1)" });
      running.onfinish = function () { box.open = false; running = null; };
      return;
    }
    box.open = true;
    sum.classList.remove("cheer");
    void sum.offsetWidth;
    sum.classList.add("cheer");
    setTimeout(function () { sum.classList.remove("cheer"); }, 1400);
    // The page goes down to the card, never so far that the heading leaves
    // the top (measured by layout: the card is still sliding in).
    var s = sum.getBoundingClientRect();
    var bottom = s.bottom + (parseFloat(getComputedStyle(body).marginTop) || 0) + body.offsetHeight;
    var over = bottom + 24 - window.innerHeight, room = s.top - 76;
    if (over > 0 && room > 0) { window.scrollBy({ top: Math.min(over, room), behavior: "smooth" }); }
  });
})();

