/* What visitors do on a page, for the site's own statistics: which
 * questions of the help they open, what they search it for, how far down
 * the front page they get, which install steps they open, which language
 * they switch to and which other sites they follow a link to.
 *
 * Only that it happened and what - never who; no cookie, nothing kept in
 * the browser. Each thing is told once a page, through nav.js's iptvHit
 * (which sends nothing in an automated browser), and the server keeps only
 * what donate/stats_events.py lists. An EXTERNAL file for the site's CSP;
 * with it gone the pages work exactly the same.
 */
(function () {
  "use strict";
  if (typeof iptvHit !== "function") { return; }
  var told = {};
  function once(ev, what, n) {
    var key = ev + "|" + what;
    if (!what || told[key]) { return; }
    told[key] = true;
    iptvHit(ev, what, n);
  }

  document.addEventListener("click", function (e) {
    var t = e.target;
    if (!t || !t.closest) { return; }
    // A question opened by hand (the click comes before it opens).
    var sum = t.closest(".help-item > summary");
    if (sum && !sum.parentNode.open) { once("q", sum.parentNode.id); }
    var a = t.closest("a[href]");
    if (!a) { return; }
    if (a.closest(".langpick")) {
      var to = (/[?&]lang=([a-z]{2,3})/.exec(a.getAttribute("href") || "") || [])[1];
      if (to) { once("lang", (document.documentElement.lang || "") + ">" + to); }
      return;
    }
    if (a.matches("a.dlx-how[data-how]")) { once("how", a.getAttribute("data-how")); return; }
    if (/^https?:$/.test(a.protocol) && a.hostname && a.hostname !== location.hostname &&
        !/(^|\.)stripe\.com$/.test(a.hostname)) {   // the donate link opens the dialog
      once("out", a.hostname.replace(/^www\./, ""));
    }
  }, true);

  // The help's search: what was looked for and how much it found, once
  // typing has stopped for a moment.
  var box = document.getElementById("helpSearch");
  if (box) {
    var timer = 0;
    box.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var q = box.value.replace(/\s+/g, " ").trim().toLowerCase();
        if (q.length < 3) { return; }
        once("s", q.slice(0, 40), document.querySelectorAll(".help-item:not([hidden])").length);
      }, 1500);
    });
  }

  // How far down the front page: each part once it is a quarter in view.
  if (window.IntersectionObserver) {
    var parts = ["features", "shots", "download", "donate"].map(function (id) {
      return document.getElementById(id);
    }).filter(Boolean);
    if (parts.length) {
      var seen = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) { once("sec", en.target.id); seen.unobserve(en.target); }
        });
      }, { threshold: 0.25 });
      parts.forEach(function (el) { seen.observe(el); });
    }
  }
})();
