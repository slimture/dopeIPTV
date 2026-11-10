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
  var osEl = document.getElementById("osLabel");
  if (osEl) { osEl.textContent = os; }
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
    // The hero's "Download" is no use on a phone: it sends the link instead.
    var hero = document.getElementById("heroDownload");
    if (hero) {
      hero.textContent = label;
      hero.addEventListener("click", function (e) { e.preventDefault(); send(hero); });
    }
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

/* Features: on a phone the first six, the rest a click away. */
(function () {
  "use strict";
  var list = document.querySelector(".feats[data-more]");
  if (!list || list.children.length <= 7) { return; }
  list.classList.add("folded");
  var b = document.createElement("button");
  b.type = "button";
  b.className = "feat-all";
  b.textContent = list.getAttribute("data-more");
  b.addEventListener("click", function () { list.classList.remove("folded"); b.remove(); });
  list.parentNode.insertBefore(b, list.nextSibling);
})();

/* Motion, Apple-style: rise into view, the hero window straightening as the
   page scrolls, a living mock-up. Nothing for reduced motion. */
(function () {
  "use strict";
  if (!window.matchMedia || window.matchMedia("(prefers-reduced-motion: reduce)").matches) { return; }

  if ("IntersectionObserver" in window) {
    var sel = ".sec-head, .feat, .shot, .dlx-card, .dl-phone, .donate, .notes-wrap, .faq-item";
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
  var ticking = false;
  function tilt() {
    ticking = false;
    var top = mock.getBoundingClientRect().top, h = window.innerHeight;
    var t = (top - h * 0.2) / (h * 0.6);
    mock.style.setProperty("--tilt", String(Math.max(0, Math.min(1, t)).toFixed(3)));
  }
  window.addEventListener("scroll", function () {
    if (!ticking) { ticking = true; window.requestAnimationFrame(tilt); }
  }, { passive: true });
  window.addEventListener("resize", tilt);
  tilt();

  mock.classList.add("alive");
  var chans = mock.querySelectorAll(".chan"), screen = mock.querySelector(".screen");
  var tints = ["#4c8dff", "#ff6b5e", "#34c759", "#ff9f0a", "#5ac8fa", "#bf5af2"];
  var i = 0;
  setInterval(function () {
    if (document.hidden || !chans.length) { return; }
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
