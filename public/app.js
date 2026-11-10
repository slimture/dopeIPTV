/* dopeIPTV site - detect the visitor's OS and tailor the hero download button.
   Pure progressive enhancement: the page is fully usable with JS disabled. */
(function () {
  "use strict";
  var ua = navigator.userAgent || "";
  var os = "";
  // A phone or tablet: the app is not for it (an iPhone says "like Mac OS X",
  // Android says "Linux", an iPad says Macintosh outright). No system is
  // claimed, the downloads fold away, and the visitor can send the link on.
  var phone = /iPhone|iPad|iPod|Android|Mobile/i.test(ua) ||
              (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
  if (phone) { phonePage(); return; }
  if (/Mac/i.test(ua)) { os = "macOS"; }
  else if (/Windows/i.test(ua)) { os = "Windows"; }
  else if (/Linux|X11|CrOS/i.test(ua)) { os = "Linux"; }
  if (!os) { return; }   // unknown system: the server-rendered text stands
  // All three systems stay named - a Mac visitor should see at a glance that
  // it is theirs too, not only that a Linux app exists - with theirs marked.
  var mine = document.querySelector('#osLabel [data-os="' + os.toLowerCase() + '"]');
  if (mine) { mine.classList.add("is-mine"); }
  // The headline names the visitor's own system: "for Linux" alone read as
  // "not for me" on a Mac. Phones, unknown systems and search engines keep
  // the page's own, which names all three - and only beside a headline that
  // names one does the line under the buttons list the three (.os-known).
  var h1 = document.getElementById("heroH1");
  var own = h1 && h1.getAttribute("data-" + os.toLowerCase());
  if (own) {
    h1.innerHTML = own;
    document.documentElement.classList.add("os-known");
  }
  // The label comes translated from the page ("Download for %s"); this
  // file used to carry its own English and overwrite every language with it.
  var btn = document.getElementById("heroDownload");
  var fmt = btn && btn.getAttribute("data-os-label");
  if (fmt) { btn.textContent = fmt.replace("%s", os); }
  // This system's download card stands out (without script: three equal).
  var card = document.querySelector('.dlx-card[data-os="' + os.toLowerCase() + '"]');
  var box = card && card.closest(".dlx");
  if (box) {
    card.classList.add("is-mine");
    box.classList.add("has-mine");
  }

  function phonePage() {
    document.documentElement.classList.add("is-phone");
    var share = document.getElementById("dlShare");
    var show = document.getElementById("dlShow");
    var sec = document.getElementById("download");
    if (show && sec) {
      show.hidden = false;
      show.addEventListener("click", function () {
        var open = sec.classList.toggle("dl-open");
        show.setAttribute("aria-expanded", open ? "true" : "false");
      });
    }
    if (!share) { return; }
    var url = window.location.origin + "/";
    var label = share.textContent;
    var send = null;
    if (navigator.share) {
      send = function (b) {
        navigator.share({ title: "dopeIPTV", url: url }).catch(function () { /* closed */ });
      };
    } else if (navigator.clipboard && window.isSecureContext) {
      send = function (b) {
        navigator.clipboard.writeText(url).then(function () {
          var was = b.textContent;
          b.textContent = share.getAttribute("data-copied") || label;
          setTimeout(function () { b.textContent = was; }, 2200);
        }, function () { /* no permission: nothing to say */ });
      };
    }
    if (!send) { return; }
    share.hidden = false;
    share.addEventListener("click", function () { send(share); });
  }
})();

/* Screenshot lightbox: click a screenshot to open it full-size. Built with DOM
   APIs (no inline handlers) so it stays inside a strict same-origin CSP. */
(function () {
  "use strict";
  var shots = document.querySelectorAll(".shot img");
  if (!shots.length) { return; }

  var overlay = document.createElement("div");
  overlay.className = "lb-overlay";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-hidden", "true");

  var big = document.createElement("img");
  big.alt = "";
  var close = document.createElement("button");
  close.className = "lb-close";
  close.type = "button";
  var box = document.querySelector(".shots");
  close.setAttribute("aria-label",
                     (box && box.getAttribute("data-close")) || "Close");
  close.textContent = "×";              // ×
  overlay.appendChild(big);
  overlay.appendChild(close);
  document.body.appendChild(overlay);

  function open(src, alt) {
    big.src = src;
    big.alt = alt || "";
    overlay.setAttribute("aria-hidden", "false");
    overlay.classList.add("open");
  }
  function hide() {
    overlay.classList.remove("open");
    overlay.setAttribute("aria-hidden", "true");
  }

  shots.forEach(function (im) {
    im.addEventListener("click", function () {
      open(im.currentSrc || im.src, im.alt);
    });
  });
  overlay.addEventListener("click", hide);   // backdrop, image, or the × button
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && overlay.classList.contains("open")) { hide(); }
  });
})();

/* The header's disclosures (language picker, narrow-window menu) close
   themselves from nav.js, which every page loads - this one does not. */

/* The feature explorer: the plain list becomes tabs, a small animated
   screen per feature and one line of text at a time. It steps on by itself
   while it is on screen, until the visitor picks a tab (more slowly, for
   reduced motion: the scenes only fade). Without script the list stays as
   it is. */
(function () {
  "use strict";
  var sec = document.getElementById("features");
  var list = sec && sec.querySelector(".feats");
  var fxp = sec && sec.querySelector(".fxp");
  if (!list || !fxp) { return; }
  var feats = [].slice.call(list.querySelectorAll(".feat[data-k]"));
  var tabsBox = fxp.querySelector(".fxp-tabs"), panel = fxp.querySelector(".fxp-main");
  var cap = fxp.querySelector(".fxp-cap"), scenes = fxp.querySelectorAll(".sc");
  if (!feats.length || !tabsBox || !cap) { return; }
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var DUR = reduce ? 8000 : 6000, cur = -1, timer = null, auto = true, inView = false, held = false;
  // It steps on by itself on a computer only: on a phone that was too much
  // movement, and the visitor taps or swipes anyway.
  var narrow = window.matchMedia ? window.matchMedia("(max-width: 860px)") : { matches: false };
  var tabs = feats.map(function (f, i) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "fxp-tab";
    b.id = "fxt-" + i;
    b.setAttribute("role", "tab");
    var ic = f.querySelector(".ic");
    if (ic) { b.appendChild(ic.cloneNode(true)); }
    var s = document.createElement("span");
    s.textContent = f.querySelector("h3").textContent;
    b.appendChild(s);
    var bar = document.createElement("i");
    bar.className = "bar";
    b.appendChild(bar);
    b.addEventListener("click", function () { auto = false; show(i, true); });
    b.addEventListener("keydown", function (e) {
      var k = e.key, rtl = document.documentElement.dir === "rtl", n = null;
      if (k === "ArrowDown" || k === (rtl ? "ArrowLeft" : "ArrowRight")) { n = (i + 1) % feats.length; }
      if (k === "ArrowUp" || k === (rtl ? "ArrowRight" : "ArrowLeft")) { n = (i - 1 + feats.length) % feats.length; }
      if (k === "Home") { n = 0; }
      if (k === "End") { n = feats.length - 1; }
      if (n === null) { return; }
      e.preventDefault();
      auto = false;
      show(n, true);
      tabs[n].focus();
    });
    tabsBox.appendChild(b);
    return b;
  });

  function intoView(b) {
    if (tabsBox.scrollWidth <= tabsBox.clientWidth + 2) { return; }
    var left = b.offsetLeft - (tabsBox.clientWidth - b.offsetWidth) / 2;
    tabsBox.scrollTo({ left: left, behavior: reduce ? "auto" : "smooth" });
  }
  function schedule() {
    clearTimeout(timer);
    tabs.forEach(function (t) { t.classList.remove("playing"); });
    if (!auto || !inView || held || cur < 0 || narrow.matches) { return; }
    var t = tabs[cur];
    void t.offsetWidth;                     // restart the bar's fill
    t.classList.add("playing");
    timer = setTimeout(function () { show((cur + 1) % feats.length, false); }, DUR);
  }
  function show(i, user) {
    if (i === cur && !user) { return; }
    cur = i;
    var k = feats[i].getAttribute("data-k");
    tabs.forEach(function (t, n) {
      var on = n === i;
      t.classList.toggle("is-on", on);
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
    });
    [].forEach.call(scenes, function (sc) { sc.classList.toggle("on", sc.getAttribute("data-k") === k); });
    panel.setAttribute("aria-labelledby", tabs[i].id);
    cap.replaceChildren();
    var h = document.createElement("h3");
    h.textContent = feats[i].querySelector("h3").textContent;
    cap.appendChild(h);
    var p = feats[i].querySelector("p");
    if (p) { cap.appendChild(p.cloneNode(true)); }
    cap.classList.remove("swap");
    void cap.offsetWidth;
    cap.classList.add("swap");
    intoView(tabs[i]);
    schedule();
  }

  if ("IntersectionObserver" in window) {
    new IntersectionObserver(function (en) {
      inView = en[0].isIntersecting;
      fxp.classList.toggle("in-view", inView);     // its scenes move only on screen
      schedule();
    }, { threshold: 0.35 }).observe(fxp);
  } else {
    fxp.classList.add("in-view");
  }
  // A pointer resting on it holds the slideshow, as Apple's do.
  fxp.addEventListener("mouseenter", function () { held = true; schedule(); });
  fxp.addEventListener("mouseleave", function () { held = false; schedule(); });
  document.addEventListener("visibilitychange", function () { held = document.hidden; schedule(); });

  // A swipe across the screen goes to the next or the previous feature.
  var stage = fxp.querySelector(".fxp-stage"), sx = null, sy = 0;
  if (stage) {
    stage.addEventListener("touchstart", function (e) {
      sx = e.touches[0].clientX; sy = e.touches[0].clientY;
    }, { passive: true });
    stage.addEventListener("touchend", function (e) {
      if (sx === null) { return; }
      var dx = e.changedTouches[0].clientX - sx, dy = e.changedTouches[0].clientY - sy;
      sx = null;
      if (Math.abs(dx) < 40 || Math.abs(dx) < Math.abs(dy) * 1.5) { return; }
      var step = dx < 0 ? 1 : -1;
      if (document.documentElement.dir === "rtl") { step = -step; }
      auto = false;
      show((cur + step + feats.length) % feats.length, true);
    }, { passive: true });
  }

  list.hidden = true;
  fxp.hidden = false;
  show(0, false);
})();

/* Motion, Apple-style: rise into view, a living mock-up in the hero. For
   reduced motion the same, calm: sections fade in without rising, the
   picture stays put (style.css). */
(function () {
  "use strict";
  if (!window.matchMedia) { return; }

  // On a phone the page is simply there: nothing rises into view.
  var phone = window.matchMedia("(max-width: 720px)").matches;
  // Nor on Chromium: the rising cost it frames on a scroll (nav.js, .cr).
  var chromium = document.documentElement.classList.contains("cr");
  if ("IntersectionObserver" in window && !phone && !chromium) {
    var sel = ".sec-head, .fxp, .shot, .dlx-card, .dl-phone, .donate, .notes-wrap, .faq-item";
    var fold = window.innerHeight * 0.92;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) { return; }
        var el = en.target;
        io.unobserve(el);
        el.classList.add("in");
        // Done rising: hand the element back to its own transitions.
        setTimeout(function () { el.classList.remove("rv", "in"); el.style.removeProperty("--d"); }, 1400);
      });
    }, { rootMargin: "0px 0px -8% 0px" });
    var seen = new Map();
    [].forEach.call(document.querySelectorAll(sel), function (el) {
      if (el.getBoundingClientRect().top < fold) { return; }
      // Siblings rise one after another, a beat apart.
      var n = seen.get(el.parentNode) || 0;
      seen.set(el.parentNode, n + 1);
      el.style.setProperty("--d", Math.min(n % 6, 5) * 0.07 + "s");
      el.classList.add("rv");
      io.observe(el);
    });
  }

  var mock = document.querySelector(".hero .mock");
  if (!mock || !mock.offsetParent) { return; }
  mock.classList.add("alive");
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(function (en) {
      mock.classList.toggle("still", !en[0].isIntersecting);
    }).observe(mock);
  }
  var chans = mock.querySelectorAll(".chan"), screen = mock.querySelector(".screen");
  var tints = ["#4c8dff", "#ff6b5e", "#34c759", "#ff9f0a", "#5ac8fa", "#bf5af2"];
  var i = 0;
  setInterval(function () {
    if (document.hidden || !chans.length || mock.classList.contains("still")) { return; }
    chans[i].classList.remove("active");
    i = (i + 1) % chans.length;
    chans[i].classList.add("active");
    if (screen) { screen.style.setProperty("--tint", tints[i % tints.length]); }
  }, 3200);
})();

/* The FAQ shows its first five questions; the rest are a click away. */
(function () {
  "use strict";
  var faq = document.querySelector(".faq[data-more]");
  var items = faq ? faq.querySelectorAll(".faq-item") : [];
  if (items.length <= 6) { return; }
  [].forEach.call(items, function (it, n) { if (n >= 5) { it.classList.add("is-extra"); } });
  var b = document.createElement("button");
  b.type = "button";
  b.className = "faq-all";
  b.textContent = faq.getAttribute("data-more");
  b.addEventListener("click", function () {
    [].forEach.call(faq.querySelectorAll(".is-extra"), function (it) { it.classList.remove("is-extra"); });
    b.remove();
    items[5].querySelector("summary").focus();
  });
  faq.appendChild(b);
})();

/* Living details: the download count counts up - for reduced motion too,
   nothing moves. (The format names used to drift by; too much sideways
   motion beside the other moving parts.) */
(function () {
  "use strict";
  var count = document.querySelector(".release-tag .dl-count");
  var m = count && count.textContent.match(/^(\D*)([\d\s.,  ]*\d)(\D*)$/);
  if (!m || !("IntersectionObserver" in window)) { return; }
  var total = parseInt(m[2].replace(/\D/g, ""), 10);
  if (!(total > 0)) { return; }
  var fmt;
  try { fmt = new Intl.NumberFormat(document.documentElement.lang || undefined); } catch (e) { fmt = null; }
  var shown = m[2];
  function put(n) { count.textContent = m[1] + (fmt ? fmt.format(n) : String(n)) + m[3]; }
  var io = new IntersectionObserver(function (en) {
    if (!en[0].isIntersecting) { return; }
    io.disconnect();
    var t0 = null, dur = 1400;
    function step(t) {
      if (t0 === null) { t0 = t; }
      var k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      put(Math.round(total * e));
      if (k < 1) { window.requestAnimationFrame(step); } else { count.textContent = m[1] + shown + m[3]; }
    }
    put(0);
    window.requestAnimationFrame(step);
  }, { threshold: 1 });
  io.observe(count);
})();

/* "How to install" on a download card: the help page's answer for that
   system in a dialog over the downloads - whoever clicks it is in the middle
   of a download, and a page of its own broke that off. The text is the help
   page's (rendered into a <template> beside the cards), so it is kept in one
   place, and "More help" at the foot goes on to the help page. Without
   script or <dialog>, or with a modifier key, the link is the help page. */
(function () {
  "use strict";
  var box = document.querySelector(".dlx");
  if (!box || typeof HTMLDialogElement !== "function") { return; }
  var dlg = null, title = null, body = null, more = null;

  function build() {
    dlg = document.createElement("dialog");
    dlg.className = "howdlg";
    dlg.setAttribute("aria-labelledby", "how-title");
    var head = document.createElement("div");
    head.className = "howdlg-head";
    title = document.createElement("h2");
    title.id = "how-title";
    var x = document.createElement("button");
    x.type = "button"; x.className = "dn-x"; x.textContent = "×";
    x.setAttribute("aria-label", box.getAttribute("data-close") || "Close");
    x.addEventListener("click", function () { dlg.close(); });
    head.append(title, x);
    body = document.createElement("div");
    body.className = "howdlg-body help-a";
    var foot = document.createElement("p");
    foot.className = "howdlg-foot";
    more = document.createElement("a");
    more.textContent = box.getAttribute("data-how-more") || "";
    foot.append(more);
    dlg.append(head, body, foot);
    // A click beside it (on the backdrop) closes it, as Esc does.
    dlg.addEventListener("click", function (e) { if (e.target === dlg) { dlg.close(); } });
    document.body.append(dlg);
  }

  box.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("a.dlx-how[data-how]");
    if (!a || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) { return; }
    var tpl = document.getElementById("how-" + a.getAttribute("data-how"));
    if (!tpl || !tpl.content) { return; }
    e.preventDefault();
    if (!dlg) { build(); }
    title.textContent = tpl.getAttribute("data-title") || a.textContent;
    body.replaceChildren(tpl.content.cloneNode(true));
    if (typeof window.iptvCopyButtons === "function") { window.iptvCopyButtons(body); }
    more.href = a.href;
    dlg.showModal();
    body.scrollTop = 0;
  });
})();
