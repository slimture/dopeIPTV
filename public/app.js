/* dopeIPTV site - detect the visitor's OS and tailor the hero download button.
   Pure progressive enhancement: the page is fully usable with JS disabled. */
(function () {
  "use strict";
  var ua = navigator.userAgent || "";
  var os = "Linux · macOS · Windows", label = "Download for your system";
  if (/Mac/i.test(ua)) { os = "macOS"; label = "Download for macOS"; }
  else if (/Windows/i.test(ua)) { os = "Windows"; label = "Download for Windows"; }
  else if (/Linux|X11|CrOS/i.test(ua)) { os = "Linux"; label = "Download for Linux"; }
  var osEl = document.getElementById("osLabel");
  if (osEl) { osEl.textContent = os; }
  var btn = document.getElementById("heroDownload");
  if (btn) { btn.textContent = label; }
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
  close.setAttribute("aria-label", "Close");
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
