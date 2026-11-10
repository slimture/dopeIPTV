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
