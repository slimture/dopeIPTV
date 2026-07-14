/* dopeIPTV site - detect the visitor's OS and tailor the hero download button.
   Pure progressive enhancement: the page is fully usable with JS disabled. */
(function () {
  "use strict";
  var ua = navigator.userAgent || "";
  var os = "Linux · macOS", label = "Download for your system";
  if (/Mac/i.test(ua)) { os = "macOS"; label = "Download for macOS"; }
  else if (/Windows/i.test(ua)) { os = "Windows (build from source)"; label = "See downloads"; }
  else if (/Linux|X11|CrOS/i.test(ua)) { os = "Linux"; label = "Download for Linux"; }
  var osEl = document.getElementById("osLabel");
  if (osEl) { osEl.textContent = os; }
  var btn = document.getElementById("heroDownload");
  if (btn) { btn.textContent = label; }
})();
