/* The admin page: /admin/data drawn as tiles, two charts, top lists and the
 * donations, and the visitor log (/admin/visits, /admin/visitor) one visit at
 * a time; the Analysis tab is analysis.js, the log's sort, groups and
 * filters logtools.js, both built on window.AdminKit below. The tab and
 * what it shows live in the address (#visitors?span=month...), so a reload
 * stays where it was, while the page opened afresh starts on the overview;
 * each move to another view is a step in the browser's history, so Back
 * (the browser's, the phone's, or the page's own) returns to the last one.
 * An external file for the site's CSP (script-src 'self'); the charts are
 * SVG made here, sizes set through the DOM, never inline styles.
 */
(function () {
  "use strict";
  // Signed out meanwhile (another device signed out everywhere, an admin
  // removed): any of the page's own calls answering 401 goes to the sign-in
  // page, which brings the page back to where it was.
  const pageFetch = window.fetch.bind(window);
  window.fetch = function (url, opts) {
    return pageFetch(url, opts).then((r) => {
      if (r.status === 401 && /^\/admin\//.test(String(url)) && !/^\/admin\/auth\//.test(String(url))) {
        location.replace("/admin/login?next=" + encodeURIComponent(location.pathname + location.hash));
      }
      return r;
    });
  };
  const $ = (id) => document.getElementById(id);
  const SVG = "http://www.w3.org/2000/svg";
  const ZERO_DECIMAL = /^(bif|clp|djf|gnf|jpy|kmf|krw|mga|pyg|rwf|ugx|vnd|vuv|xaf|xof|xpf)$/i;
  const num = new Intl.NumberFormat();
  const dayFmt = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" });
  const longDay = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
  const stamp = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const monthFmt = new Intl.DateTimeFormat(undefined, { month: "short", year: "2-digit" });
  const longMonth = new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" });
  const dateFmt = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" });
  const RANGES = [7, 30, 90, 365, 0];          // 0: everything kept
  const clock = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
  const clockSec = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  let langNames = null, regionNames = null;
  try { langNames = new Intl.DisplayNames(["en"], { type: "language" }); } catch (e) { /* codes then */ }
  try { regionNames = new Intl.DisplayNames(["en"], { type: "region" }); } catch (e) { /* codes then */ }
  function countryName(code) {
    if (!code || code === "unknown") { return "Unknown"; }
    try { return regionNames ? regionNames.of(code) : code; } catch (e) { return code; }
  }
  function flag(code) {
    if (!/^[A-Z]{2}$/.test(code || "")) { return ""; }
    return String.fromCodePoint(...[...code].map((c) => 0x1F1E6 + c.charCodeAt(0) - 65)) + " ";
  }
  function duration(secs) {
    secs = Math.round(secs || 0);
    if (secs < 60) { return secs + " s"; }
    const m = Math.floor(secs / 60), r = secs % 60;
    if (m < 60) { return m + " min" + (r ? " " + r + " s" : ""); }
    return Math.floor(m / 60) + " h " + (m % 60) + " min";
  }
  const PAGE_NAMES = { "/": "Home", "/help/": "Help" };

  let days = 30, data = null, tab = "overview";
  try {
    const saved = localStorage.getItem("admin-days");
    if (saved !== null && RANGES.indexOf(Number(saved)) >= 0) { days = Number(saved); }
  } catch (e) { /* default */ }

  function mk(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text !== undefined && text !== null) { n.textContent = text; }
    return n;
  }
  function svg(tag, attrs, parent) {
    const n = document.createElementNS(SVG, tag);
    for (const k in attrs) { n.setAttribute(k, attrs[k]); }
    if (parent) { parent.appendChild(n); }
    return n;
  }
  function money(minor, cur) {
    const major = ZERO_DECIMAL.test(cur) ? minor : minor / 100;
    try {
      return new Intl.NumberFormat(undefined, { style: "currency", currency: cur,
        maximumFractionDigits: major % 1 ? 2 : 0 }).format(major);
    } catch (e) { return major + " " + cur; }
  }
  function sums(byCur) {
    const parts = Object.keys(byCur || {}).sort().map((c) => money(byCur[c], c));
    return parts.length ? parts.join(" · ") : "–";
  }
  function day(iso) { return new Date(iso + "T12:00:00"); }
  // One point of the series: a day, a week (from its Monday) or a month.
  function per() { return data.step === "week" ? "week" : data.step === "month" ? "month" : "day"; }
  function tickLabel(iso) { return per() === "month" ? monthFmt.format(day(iso)) : dayFmt.format(day(iso)); }
  // A week or month still under way is drawn dashed and pale, and says so.
  function partial(i) { return per() !== "day" && i === data.series.length - 1; }
  function pointLabel(iso, i) {
    const soFar = i !== undefined && partial(i) ? " (so far)" : "";
    if (per() === "month") { return longMonth.format(day(iso)) + soFar; }
    if (per() === "week") { return "Week of " + dateFmt.format(day(iso)) + soFar; }
    return longDay.format(day(iso));
  }
  function periodText() {
    return days === 365 ? "the 12 months before" : "the " + days + " days before";
  }
  function status(text) { $("status").textContent = text || ""; }

  // -- loading -------------------------------------------------------------------
  function setRange(n) {
    days = n;
    try { localStorage.setItem("admin-days", String(n)); } catch (e) { /* fine */ }
    document.querySelectorAll(".range button").forEach((b) => {
      b.setAttribute("aria-checked", Number(b.dataset.days) === n ? "true" : "false");
    });
    load();
    if (tab === "analysis" && window.AdminAnalysis) { window.AdminAnalysis.show(); }
    if (tab === "feedback" && window.AdminFeedback) { window.AdminFeedback.show(); }
  }
  // Donations seen here are not shown again in the note on the site's own
  // pages (public/nav.js, the same browser's storage).
  function markSeen() {
    try {
      const newest = Math.max(0, ...((data.donations || {}).recent || []).map((r) => r.t || 0));
      if (newest > (+localStorage.getItem("dn-admin-seen") || 0)) { localStorage.setItem("dn-admin-seen", String(newest)); }
    } catch (e) { /* no storage: the note shows them once more */ }
  }
  function load() {
    status(data ? "" : "Loading…");
    fetch("/admin/data?days=" + days, { credentials: "same-origin", cache: "no-store" })
      .then((r) => { if (!r.ok) { throw new Error("the service answered " + r.status); } return r.json(); })
      .then((d) => { data = d; status(""); render(); markSeen(); if (d.dl) { dlCounts = d.dl; drawDl(); } })
      .catch((e) => status("Could not load the numbers: " + e.message + "."));
  }

  // -- tiles -----------------------------------------------------------------------
  function delta(now, before) {
    if (before === undefined) { return ""; }
    if (!before) { return now ? "new" : ""; }
    const p = Math.round((now - before) / before * 100);
    return (p > 0 ? "▲ " : p < 0 ? "▼ " : "") + Math.abs(p) + "%";
  }
  // *also*: a line under the note that shows with the comparison too.
  function tile(label, value, note, d, small, also) {
    const t = mk("div", "kpi");
    t.append(mk("div", "label", label));
    t.append(mk("div", "value" + (small ? " small" : ""), value));
    const n = mk("div", "note");
    if (d) { n.append(mk("span", "delta", d), document.createTextNode(" vs " + periodText())); }
    else if (note) { n.textContent = note; }
    t.append(n);
    if (also) { t.append(mk("div", "note also", also)); }
    return t;
  }
  // -- the summary: what the period came to, in one sentence and three numbers --
  function spark(values, cls) {
    const W = 132, H = 36, n = values.length;
    const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "spark " + cls, "aria-hidden": "true" });
    if (n < 2) { return s; }
    const max = Math.max(1, ...values);
    const pts = values.map((v, i) => [i * W / (n - 1), H - 3 - (H - 6) * v / max]);
    const line = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join("");
    svg("path", { d: line + `L${W} ${H}L0 ${H}Z`, class: "area" }, s);
    svg("path", { d: line, class: "stroke" }, s);
    return s;
  }
  function change(now, before) {
    if (before === undefined || before === null) { return null; }
    if (!before) { return now ? { text: "new", cls: "up" } : null; }
    const p = Math.round((now - before) / before * 100);
    return { text: (p > 0 ? "▲ " : p < 0 ? "▼ " : "") + Math.abs(p) + "%", cls: p > 0 ? "up" : p < 0 ? "down" : "" };
  }
  function bigStat(label, value, ch, sparkline, cls, note) {
    const b = mk("div", "big " + (cls || ""));
    b.append(mk("div", "label", label));
    const row = mk("div", "row");
    row.append(mk("div", "value", value));
    if (ch) { row.append(mk("span", "chip " + ch.cls, ch.text)); }
    b.append(row);
    if (note) { b.append(mk("div", "note", note)); }
    if (sparkline) { b.append(sparkline); }
    return b;
  }
  // How many showed they were people, and what was left out as likely
  // robots (stats_collect.py: kept and marked, never deleted).
  function judgedLine() {
    const J = data.judged;
    if (!J || !J.since || !(J.people || J.robots)) { return ""; }
    let text = num.format(J.people) + " of them clearly people";
    if (J.robots) {
      text += " · " + num.format(J.robots) + (J.robots === 1 ? " likely robot" : " likely robots") + " not counted";
      const also = [];
      if (J.views) { also.push(num.format(J.views) + (J.views === 1 ? " page view" : " page views")); }
      if (J.downloads) { also.push(num.format(J.downloads) + (J.downloads === 1 ? " download" : " downloads")); }
      if (also.length) { text += " (" + also.join(", ") + ")"; }
    }
    if (!J.all) { text += " · judged since " + dayFmt.format(day(J.since)); }
    return text;
  }
  function summary() {
    const T = data.totals, B = data.before || {}, D = data.donations, S = data.series;
    const unique = T.unique !== null && T.unique !== undefined;
    const people = unique ? T.unique : T.visitors;
    const peopleBefore = unique ? B.unique : B.visitors;
    const when = days === 0 ? "Since counting began" : days === 365 ? "The last 12 months" : "The last " + days + " days";
    const box = $("summary");
    const head = mk("div", "sum-head");
    const line = mk("p", "sentence");
    const strong = (t) => mk("strong", "", t);
    line.append(strong(num.format(people)), document.createTextNode(people === 1 ? " person found dopeIPTV, " : " people found dopeIPTV, "),
                strong(num.format(T.downloads)), document.createTextNode(" downloaded it and "),
                strong(num.format(D.count || 0)), document.createTextNode((D.count === 1 ? " donated." : " donated.")));
    head.append(mk("div", "when", when), line);
    const judged = judgedLine();
    if (judged) { head.append(mk("div", "judged", judged)); }
    head.append(mk("div", "today", "Today so far: " + num.format(data.today.visitors) + " visitors · " +
                   num.format(data.today.views) + " page views"));
    const vs = days ? "vs " + periodText() : "";
    const stats = mk("div", "bigs");
    stats.append(
      bigStat(unique ? "People" : "Visits", num.format(people), days ? change(people, peopleBefore) : null,
              spark(S.map((d) => d.visitors), "s1"), "", vs),
      bigStat("Downloads", num.format(T.downloads), days ? change(T.downloads, B.downloads) : null,
              spark(S.map((d) => d.downloads), "s3"), "", num.format(data.downloads_all_time) + " all time"),
      bigStat("Donations", D.error ? "–" : num.format(D.count || 0), null, null, "heart",
              D.error ? "Stripe did not answer" : (D.count ? sums(D.sums) : "none yet in this period")));
    box.replaceChildren(head, stats);
  }

  // -- from visit to donation ----------------------------------------------------
  function funnel() {
    const rows = data.by_channel || [];
    const t = { visits: 0, more: 0, downloaded: 0, donate: 0 };
    rows.forEach(([, r]) => { t.visits += r.visits; t.more += r.more; t.downloaded += r.downloaded; t.donate += r.donate; });
    const donated = data.donations && !data.donations.error ? data.donations.count || 0 : null;
    const box = $("funnel");
    if (!t.visits) { box.replaceChildren(mk("p", "empty", "No visits in this period yet.")); $("funnel-note").textContent = ""; return; }
    $("funnel-note").textContent = "share of all visits";
    const steps = [["Visits", t.visits], ["Read more than one page", t.more], ["Downloaded", t.downloaded],
                   ["Opened the donate dialog", t.donate]];
    if (donated !== null) { steps.push(["Donated", donated]); }
    const ul = mk("ul", "funnel");
    steps.forEach(([label, n], i) => {
      const li = mk("li", "step-" + i + (i === steps.length - 1 && donated !== null ? " last" : ""));
      const bar = mk("span", "track"), fill = mk("i");
      fill.style.width = Math.min(100, Math.max(n ? 1.2 : 0, n / t.visits * 100)) + "%";
      bar.append(fill);
      li.append(mk("span", "k", label), bar, mk("span", "v", num.format(n)),
                mk("span", "p", i ? Math.round(n / t.visits * 1000) / 10 + "%" : ""));
      ul.append(li);
    });
    box.replaceChildren(ul);
  }

  function tiles() {
    const T = data.totals, B = data.before || {}, D = data.donations;
    // Different people over the whole span where the hashes reach (400
    // days); visits are a person on a day, summed.
    const unique = T.unique !== null && T.unique !== undefined;
    const visitorsNote = num.format(T.visits || T.visitors) + " visits · " + num.format(T.returning || 0) + " returning";
    // Time and quick leaves only from visits whose time is known: a page
    // closed without a word (phones do) or its message blocked is not a
    // visit of no seconds.
    const timed = T.timed !== undefined ? T.timed : T.visits;
    const avg = timed ? T.engaged / timed : 0;
    const bounce = timed ? Math.round(T.bounces / timed * 100) : 0;
    const untimed = Math.max(0, (T.visits || 0) - timed);
    const opened = (data.top.events || []).find((e) => e[0] === "donate");
    // The secondary numbers, under the summary's three.
    $("kpis").replaceChildren(
      tile("Visits", num.format(T.visits || T.visitors), num.format(T.returning || 0) + " by returning visitors",
           delta(T.visits || T.visitors, B.visits || B.visitors)),
      tile("Page views", num.format(T.views), "from " + num.format(T.requests || 0) + " page requests, robots included",
           delta(T.views, B.views)),
      tile("Time per visit", timed ? duration(avg) : "–",
           timed ? bounce + "% left within 10 s" + (untimed ? " · " + num.format(untimed) + " without a time" : "") : "", "", true),
      tile("Donate dialog", num.format(opened ? opened[1] : 0), "times opened" + (D.error ? "" : " · " + sums(D.sums)), "", true),
      // The app updating itself through dopeiptv.org: people who keep it,
      // never counted among the downloads above.
      tile("Updates from the app", num.format(T.updates || 0), "", delta(T.updates || 0, B.updates), false, updatesNote())
    );
    void visitorsNote;
  }

  // A test build's file has a four-part version (dopeIPTV-1.4.5.78-...).
  const TEST_FILE = /^dopeiptv[-_](\d+(?:\.\d+){3})[-_.]/i;
  function fileVersion(n) { return (/^dopeiptv[-_](\d+(?:\.\d+)+)[-_.]/i.exec(n || "") || [, ""])[1]; }
  // The period's updates by channel: [release, test builds], from the files.
  function updateSplit(files) {
    let rel = 0, test = 0;
    (files || []).forEach(([k, v]) => { if (TEST_FILE.test(k)) { test += v; } else if (k !== "other") { rel += v; } });
    return [rel, test];
  }
  function splitText(rel, test) {
    return num.format(rel) + " to a release · " + num.format(test) + " to a test build";
  }
  function ownText(rows) {
    const own = (rows || []).reduce((a, r) => a + r[1], 0);
    return own ? num.format(own) + " from your own network, not counted" : "";
  }
  // "3 to a release · 1 to a test build": the period's updates by channel.
  function updatesNote() {
    const L = data.top;
    if (!(L.update_files || []).length && !(L.update_own || []).length) { return "the app fetches them from dopeiptv.org"; }
    const [rel, test] = updateSplit(L.update_files);
    return [splitText(rel, test), ownText(L.update_own)].filter(Boolean).join(" · ");
  }

  // -- charts ----------------------------------------------------------------------
  // Whole, round steps (1, 2, 5 x 10^n), at most five of them up the axis.
  function scale(max) {
    let step = 1;
    for (let p = 1; ; p *= 10) {
      const hit = [1, 2, 5].map((m) => m * p).find((st) => Math.ceil(max / st) <= 5);
      if (hit) { step = hit; break; }
    }
    return { step, top: Math.max(1, Math.ceil(max / step)) * step };
  }
  function frame(box, height, maxVal, right) {
    const W = Math.max(280, box.clientWidth), H = height;
    const m = { l: 40, r: right, t: 10, b: 26 };
    const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });
    const { step, top } = scale(maxVal);
    const y = (v) => m.t + (H - m.t - m.b) * (1 - v / top);
    for (let v = 0; v <= top; v += step) {
      const yy = Math.round(y(v)) + .5;
      svg("line", { x1: m.l, x2: W - m.r, y1: yy, y2: yy, class: v ? "grid" : "base" }, s);
      svg("text", { x: m.l - 8, y: yy + 4, "text-anchor": "end", class: "tick" }, s).textContent = num.format(v);
    }
    return { s, W, H, m, y };
  }
  function xTicks(f, n, x) {
    const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor((f.W - f.m.l - f.m.r) / 76))));
    for (let i = 0; i < n; i += every) {
      svg("text", { x: x(i), y: f.H - 6, "text-anchor": "middle", class: "tick" }, f.s).textContent =
        tickLabel(data.series[i].date);
    }
  }
  function showTip(ev, html) {
    const tip = $("tip");
    tip.replaceChildren(...html);
    tip.hidden = false;
    const r = tip.getBoundingClientRect();
    let x = ev.clientX + 14, y = ev.clientY - r.height - 12;
    if (x + r.width > window.innerWidth - 8) { x = ev.clientX - r.width - 14; }
    if (y < 8) { y = ev.clientY + 16; }
    tip.style.left = Math.max(8, x) + "px";
    tip.style.top = y + "px";
  }
  function hideTip() { $("tip").hidden = true; }
  function tipRow(cls, label, value) {
    const row = mk("div", "row"), k = mk("span");
    if (cls) { k.append(mk("i", cls)); }
    k.append(document.createTextNode(label));
    row.append(k, mk("strong", "", value));
    return row;
  }

  function visitsChart() {
    const box = $("chart-visits"), S = data.series;
    $("legend").replaceChildren(...[["s1", "Visitors"], ["s2", "Page views"]].map(([c, t]) => {
      const sp = mk("span"); sp.append(mk("i", c), document.createTextNode(t)); return sp;
    }));
    const max = Math.max(1, ...S.map((d) => Math.max(d.views, d.visitors)));
    const narrow = box.clientWidth < 520;
    const f = frame(box, narrow ? 220 : 260, max, narrow ? 12 : 86);
    const n = S.length;
    const x = (i) => f.m.l + (n === 1 ? 0 : (f.W - f.m.l - f.m.r) * i / (n - 1));
    xTicks(f, n, x);
    const done = partial(n - 1) && n > 1 ? n - 1 : n;
    const path = (key, from, to) => S.slice(from, to).map((d, j) =>
      (j ? "L" : "M") + x(from + j).toFixed(1) + " " + f.y(d[key]).toFixed(1)).join("");
    if (done > 1) {
      svg("path", { d: path("visitors", 0, done) + `L${x(done - 1).toFixed(1)} ${f.y(0)}L${x(0).toFixed(1)} ${f.y(0)}Z`,
                    class: "area a1" }, f.s);
    }
    svg("path", { d: path("views", 0, done), class: "line l2" }, f.s);
    svg("path", { d: path("visitors", 0, done), class: "line l1" }, f.s);
    if (done < n) {
      svg("path", { d: path("views", n - 2, n), class: "line l2 dash" }, f.s);
      svg("path", { d: path("visitors", n - 2, n), class: "line l1 dash" }, f.s);
    }
    if (!narrow) {      // direct labels at the line ends, nudged apart
      let a = f.y(S[n - 1].visitors), b = f.y(S[n - 1].views);
      if (Math.abs(a - b) < 15) { const mid = (a + b) / 2; if (a <= b) { a = mid - 8; b = mid + 8; } else { a = mid + 8; b = mid - 8; } }
      svg("text", { x: x(n - 1) + 8, y: a + 4, class: "end" }, f.s).textContent = "Visitors";
      svg("text", { x: x(n - 1) + 8, y: b + 4, class: "end" }, f.s).textContent = "Page views";
    }
    const cross = svg("line", { y1: f.m.t, y2: f.H - f.m.b, class: "cross", visibility: "hidden" }, f.s);
    const d2 = svg("circle", { r: 4.5, class: "dot2", visibility: "hidden" }, f.s);
    const d1 = svg("circle", { r: 4.5, class: "dot1", visibility: "hidden" }, f.s);
    const hit = svg("rect", { x: f.m.l - 6, y: 0, width: f.W - f.m.l - f.m.r + 12, height: f.H, class: "hit" }, f.s);
    const move = (ev) => {
      const r = f.s.getBoundingClientRect(), px = (ev.clientX - r.left) * f.W / r.width;
      const i = Math.max(0, Math.min(n - 1, Math.round((px - f.m.l) / ((f.W - f.m.l - f.m.r) / Math.max(1, n - 1)))));
      const d = S[i], xx = x(i);
      cross.setAttribute("x1", xx); cross.setAttribute("x2", xx); cross.setAttribute("visibility", "visible");
      d1.setAttribute("cx", xx); d1.setAttribute("cy", f.y(d.visitors)); d1.setAttribute("visibility", "visible");
      d2.setAttribute("cx", xx); d2.setAttribute("cy", f.y(d.views)); d2.setAttribute("visibility", "visible");
      showTip(ev, [mk("b", "", pointLabel(d.date, i)),
                   tipRow("s1", "Visitors", num.format(d.visitors)),
                   tipRow("s2", "Page views", num.format(d.views))]);
    };
    hit.addEventListener("pointermove", move);
    hit.addEventListener("pointerdown", move);
    hit.addEventListener("pointerleave", () => {
      [cross, d1, d2].forEach((e) => e.setAttribute("visibility", "hidden")); hideTip();
    });
    f.s.setAttribute("aria-label", "Visitors and page views per " + per());
    box.replaceChildren(f.s);
  }

  function downloadsChart() {
    const box = $("chart-downloads"), S = data.series;
    const max = Math.max(...S.map((d) => d.downloads));
    if (!max) { box.replaceChildren(mk("p", "empty", "No downloads in this period yet.")); return; }
    const f = frame(box, box.clientWidth < 520 ? 170 : 200, max, 12);
    const n = S.length, step = (f.W - f.m.l - f.m.r) / n;
    const bw = Math.max(2, Math.min(24, step - 2));
    const x = (i) => f.m.l + step * (i + .5);
    xTicks(f, n, x);
    const base = f.y(0);
    const bars = S.map((d, i) => {
      if (!d.downloads) { return null; }
      const top = f.y(d.downloads), h = base - top, r = Math.min(4, bw / 2, h);
      const l = x(i) - bw / 2, rr = l + bw;
      return svg("path", { class: partial(i) ? "bar partial" : "bar", d: `M${l} ${base}V${top + r}Q${l} ${top} ${l + r} ${top}H${rr - r}Q${rr} ${top} ${rr} ${top + r}V${base}Z` }, f.s);
    });
    S.forEach((d, i) => {
      const hit = svg("rect", { x: f.m.l + step * i, y: f.m.t, width: step, height: base - f.m.t, class: "hit" }, f.s);
      const on = (ev) => {
        bars.forEach((b, j) => b && b.classList.toggle("on", j === i));
        showTip(ev, [mk("b", "", pointLabel(d.date, i)), tipRow("", "Downloads", num.format(d.downloads))]);
      };
      hit.addEventListener("pointermove", on);
      hit.addEventListener("pointerdown", on);
      hit.addEventListener("pointerleave", () => { bars.forEach((b) => b && b.classList.remove("on")); hideTip(); });
    });
    f.s.setAttribute("aria-label", "Downloads per " + per());
    box.replaceChildren(f.s);
  }

  function visitsTable() {
    const t = mk("table"), head = mk("tr");
    const first = { day: "Day", week: "Week", month: "Month" }[per()];
    [[first, ""], ["Visitors", "num"], ["Page views", "num"], ["Downloads", "num"]].forEach(([h, c]) => head.append(mk("th", c, h)));
    const thead = mk("thead"); thead.append(head);
    const tb = mk("tbody");
    data.series.map((d, i) => [d, i]).reverse().forEach(([d, i]) => {
      const tr = mk("tr");
      tr.append(mk("td", "", pointLabel(d.date, i)), mk("td", "num", num.format(d.visitors)),
                mk("td", "num", num.format(d.views)), mk("td", "num", num.format(d.downloads)));
      tb.append(tr);
    });
    t.append(thead, tb);
    $("table-visits").replaceChildren(t);
  }

  // -- top lists -------------------------------------------------------------------
  // by: what a row stands for in a visit (admin.py FIND_BY) - a row then
  // opens the visits it counted.
  function listCard(title, rows, name, more, by) {
    const c = mk("section", "card");
    const head = mk("div", "card-head");
    head.append(mk("h2", "", title));
    c.append(head);
    c.append(rankList(rows, name, by, title));
    if (more) { c.append(mk("h3", "subhead", more[0]), rankList(more[1], more[2], more[3], more[0])); }
    return c;
  }
  // The app's own updates: per system, to which file, from which version.
  function updatesCard(L) {
    const c = listCard("Updates from the app", L.updates || [], null,
                       ["Updated to", L.update_files || [], (k) => k === "other" ? "Other files"
                         : k.replace(/^dopeIPTV-/, "") + (TEST_FILE.test(k) ? " (test build)" : "")]);
    const [rel, test] = updateSplit(L.update_files);
    const split = mk("p", "upd-split", splitText(rel, test));
    c.querySelector(".card-head") ? c.querySelector(".card-head").after(split) : c.prepend(split);
    c.append(mk("h3", "subhead", "Updated from"), rankList(L.update_from || [], (k) => k === "other" ? "Other versions" : k));
    const own = ownText(L.update_own);
    c.append(mk("p", "muted", "Fetched by the app itself from dopeiptv.org, once per address and file a day - not among the " +
      "downloads. A test build is one of the app's Test builds channel." + (own ? " Besides these: " + own + "." : "")));
    return c;
  }
  // A page elsewhere, opened in a new tab without telling it where from.
  function outLink(page) {
    const a = mk("a", "out", "\u2197");
    a.href = "https://" + page;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.title = "Open " + page;
    a.setAttribute("aria-label", "Open " + page);
    a.addEventListener("click", (e) => e.stopPropagation());
    a.addEventListener("keydown", (e) => e.stopPropagation());
    return a;
  }
  // A row each, its bar drawn behind it: as long as its share of the top one.
  // The first dozen show; the rest are a click away, never cut off unseen.
  const SHOWN = 12;
  function rankList(rows, name, by, title) {
    if (!rows || !rows.length) { return mk("p", "empty", "Nothing yet."); }
    const top = rows[0][1] || 1, ul = mk("ul", "rank");
    rows.forEach(([k, v], i) => {
      const li = mk("li", i === 0 ? "first" : ""), fill = mk("i", "fill");
      fill.style.width = Math.max(2, v / top * 100) + "%";
      li.append(fill, mk("span", "k", name ? name(k) : k), mk("span", "v", num.format(v)));
      li.title = (name ? name(k) : k) + ": " + num.format(v);
      li.hidden = i >= SHOWN;
      if (by && k !== "other") {
        li.classList.add("pick");
        li.tabIndex = 0;
        li.setAttribute("role", "button");
        li.title += " - show these visits";
        const go = () => { navStep(); showFiltered(by, k, title + " \u00b7 " + (name ? name(k) : k)); };
        li.addEventListener("click", go);
        li.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
      }
      ul.append(li);
    });
    if (rows.length <= SHOWN) { return ul; }
    const box = mk("div"), more = mk("button", "link more");
    more.type = "button";
    let open = false;
    const label = () => { more.textContent = open ? "Show the top " + SHOWN : "Show all " + num.format(rows.length); };
    more.addEventListener("click", () => {
      open = !open;
      ul.querySelectorAll("li").forEach((li, i) => { if (i >= SHOWN) { li.hidden = !open; } });
      label();
    });
    label();
    box.append(ul, more);
    return box;
  }
  function group(title, cards) {
    const g = mk("section", "group");
    g.append(mk("h3", "group-head", title));
    const grid = mk("div", "lists");
    grid.append(...cards);
    g.append(grid);
    return g;
  }
  function lists() {
    const L = data.top;
    const lang = (k) => { try { return langNames ? langNames.of(k) : k; } catch (e) { return k; } };
    const countries = listCard("Countries", L.countries, (k) => flag(k) + countryName(k), null, "country");
    if (!data.geo) {
      countries.append(mk("p", "muted", "The country database is fetched by the quarter-hour run (DB-IP, once a month)."));
    }
    const sites = listCard("Sites that linked here", L.refs, (k) => k === "other" ? "Other sites" : k,
                           ["Tagged links", L.sources, null, "source"], "ref");
    // The exact pages that linked here, before the tagged links: each opens
    // the visits it brought, and its arrow the page itself.
    if ((L.ref_pages || []).length) {
      const pages = rankList(L.ref_pages, (k) => k === "other" ? "Other pages" : k, "ref_page", "Page that linked");
      pages.querySelectorAll("li").forEach((li, i) => {
        const k = L.ref_pages[i][0];
        if (k !== "other") { li.append(outLink(k)); }
      });
      const tagged = sites.querySelector("h3.subhead");
      sites.insertBefore(pages, tagged);
      sites.insertBefore(mk("h3", "subhead", "Exact pages"), pages);
    }
    sites.append(mk("p", "muted", "The exact page shows when the site that linked sends it - many send only their " +
      "address (Google, Facebook and X always do). Tagged links carry ?ref=name, e.g. dopeiptv.org/?ref=github: " +
      "add one to a link you post yourself."));
    // In three groups, by the question each answers.
    $("lists").replaceChildren(
      group("Where they come from", [listCard("How they found the site", L.channels, null, null, "channel"), sites, countries]),
      group("What they read and get", [listCard("Pages", L.pages, (k) => PAGE_NAMES[k] || k, null, "page"),
        listCard("Languages", L.langs, lang, null, "lang"),
        listCard("Downloads", L.downloads, null,
                 ["Files", L.files, (k) => k === "other" ? "Other files" : k.replace(/^dopeIPTV-/, ""), "file"], "download"),
        updatesCard(L)]),
      group("What they use", [listCard("Systems", L.os_versions, null, null, "os_version"),
        listCard("Browsers", L.browsers, null, ["Versions", L.browser_versions, null, "browser_version"], "browser"),
        listCard("Devices", L.devices, null, ["Screens", L.screens, null, "screen"], "device")])
    );
  }

  // -- donations -------------------------------------------------------------------
  // Notifications of new donations, turned on per device: the browser's own
  // push (donate.py signs and sends it, sw.js shows it). On an iPhone only
  // from the Home Screen, which is Apple's rule.
  const PUSH_OK = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  const IOS = /iPhone|iPad|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  let pushSub = null, pushChecked = false;
  function deviceLabel() {
    const ua = navigator.userAgent;
    const br = /Edg\//.test(ua) ? "Edge" : /OPR\//.test(ua) ? "Opera" : /Firefox\//.test(ua) ? "Firefox"
      : /Chrome\//.test(ua) ? (navigator.brave ? "Brave" : "Chrome") : /Safari\//.test(ua) ? "Safari" : "Browser";
    const os = IOS ? (/iPad/.test(ua) || navigator.maxTouchPoints > 1 && !/iPhone/.test(ua) ? "iPad" : "iPhone")
      : /Android/.test(ua) ? "Android" : /Mac OS X/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows"
      : /Linux/.test(ua) ? "Linux" : "";
    return br + (os ? " on " + os : "");
  }
  function b64bytes(s) {
    const raw = atob((s + "=".repeat((4 - s.length % 4) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }
  function post(path, body) {
    return fetch(path, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}) }).then((r) => r.json().then((j) => [r.ok, j]));
  }
  // What this device holds, told to the service again on each visit (it
  // forgets a device whose push service has; this puts it back).
  function checkPush() {
    if (!PUSH_OK || pushChecked) { return Promise.resolve(); }
    pushChecked = true;
    return navigator.serviceWorker.getRegistration("/admin/")
      .then((reg) => (reg ? reg.pushManager.getSubscription() : null))
      .then((sub) => {
        pushSub = sub;
        if (sub) {                  // a browser drops it when the permission goes
          return post("/admin/push-subscribe", { subscription: sub.toJSON(), label: deviceLabel() }).catch(() => {});
        }
        return null;
      })
      .catch(() => { pushSub = null; });
  }
  function turnOn(say) {
    // Asked inside the click: Safari allows the question only there.
    const asked = Notification.requestPermission();
    say("Turning on…");
    Promise.resolve(asked).then((perm) => {
      if (perm !== "granted") { throw new Error(perm === "denied" ? "blocked" : "not allowed"); }
      return navigator.serviceWorker.register("/admin/sw.js", { scope: "/admin/" });
    })
      .then(() => navigator.serviceWorker.ready)
      .then((reg) => reg.pushManager.getSubscription().then((old) => old ||
        reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64bytes(data.push.key) })))
      .then((sub) => post("/admin/push-subscribe", { subscription: sub.toJSON(), label: deviceLabel() })
        .then(([ok, j]) => { if (!ok) { throw new Error(j.error || "the service said no"); } pushSub = sub; return j; }))
      .then((j) => { data.push.devices = data.push.devices.concat([{ label: deviceLabel() }]).slice(-j.devices); notifyRow(); })
      .catch((e) => say(e.message === "blocked"
        ? "Notifications are blocked for this site - allow them in the browser's site settings, then try again."
        : "Not turned on: " + e.message + "."));
  }
  function turnOff() {
    const sub = pushSub;
    pushSub = null;
    if (!sub) { notifyRow(); return; }
    post("/admin/push-unsubscribe", { endpoint: sub.endpoint }).catch(() => {});
    sub.unsubscribe().catch(() => {}).finally(() => {
      data.push.devices = data.push.devices.filter((d) => d.label !== deviceLabel());
      notifyRow();
    });
  }
  function notifyRow() {
    const box = $("notify"), P = data.push || {};
    const said = mk("span", "muted say");
    const say = (t) => { said.textContent = t; };
    const button = (text, cls, fn) => { const b = mk("button", cls, text); b.type = "button"; b.addEventListener("click", fn); return b; };
    const devices = (P.devices || []).length;
    const others = (n) => n ? n + (n === 1 ? " device gets them" : " devices get them") : "";
    const row = mk("div", "notify-row");
    if (!P.ready) {
      row.append(mk("span", "off", "Notifications need Python's cryptography package on the server"),
        mk("span", "muted", " (pkg install py311-cryptography, then service iptv_donate restart)."));
    } else if (!PUSH_OK && IOS) {
      row.append(mk("span", "off", "On iPhone and iPad: "), document.createTextNode("Share \u2192 Add to Home Screen, open dopeIPTV from there and sign in once - then turn notifications on here."));
    } else if (!PUSH_OK) {
      row.append(mk("span", "off", "This browser cannot show notifications from a site."));
    } else if (pushSub) {
      row.append(mk("span", "on", "Notifications are on for this device"), document.createTextNode(" · "),
        button("Send a test", "link", (e) => {
          e.target.disabled = true; say(" Sending…");
          post("/admin/notify-test").then(([ok, j]) => say(ok ? " Sent to " + j.sent + (j.sent === 1 ? " device." : " devices.") : " Not sent: " + (j.error || "no answer") + "."))
            .catch(() => say(" Not sent: the service did not answer.")).finally(() => { e.target.disabled = false; });
        }), document.createTextNode(" · "), button("Turn off", "link", turnOff));
    } else {
      row.append(button("Turn on notifications on this device", "btn-heart", () => turnOn(say)));
      if (devices) { row.append(mk("span", "muted", " " + others(devices))); }
    }
    row.append(said);
    const list = (P.devices || []).map((d) => d.label).filter(Boolean);
    box.replaceChildren(row);
    if (list.length && P.ready) { box.append(mk("div", "muted devices", "Getting them: " + [...new Set(list)].join(", "))); }
  }
  // Supporter keys (donate/supporter.py): one again for a payment (the same
  // key its donor got), or one by hand; and how many have been handed out.
  let skWired = false;
  function supporterKeys() {
    const out = $("sk-out"), count = $("sk-count");
    if (!out) { return; }
    const say = (n) => { count.textContent = n == null ? "" : num.format(n) + " handed out"; };
    const shown = (ok, j) => {
      if (!ok) { out.replaceChildren(mk("p", "error", j.error || "Could not make a key.")); return; }
      say(j.handed_out);
      const code = mk("code", "sk-key", j.key);
      const copy = mk("button", "btn", "Copy");
      copy.type = "button";
      copy.addEventListener("click", () => navigator.clipboard.writeText(j.key)
        .then(() => { copy.textContent = "Copied ✓"; }, () => { copy.textContent = "Select it and copy"; }));
      out.replaceChildren(code, mk("p", "muted", "Supporter since " + j.since + "."), copy);
    };
    if (!skWired) {
      skWired = true;
      $("sk-again").addEventListener("click", () => {
        const pi = $("sk-pi").value.trim();
        if (!/^pi_[A-Za-z0-9]{8,64}$/.test(pi)) { out.replaceChildren(mk("p", "error", "That is not a payment id (pi_…).")); return; }
        out.replaceChildren(mk("p", "muted", "Asking Stripe…"));
        post("/admin/supporter-key", { pi: pi }).then(([ok, j]) => shown(ok, j))
          .catch((e) => out.replaceChildren(mk("p", "error", String(e))));
      });
      $("sk-hand").addEventListener("click", () => {
        post("/admin/supporter-key", {}).then(([ok, j]) => shown(ok, j))
          .catch((e) => out.replaceChildren(mk("p", "error", String(e))));
      });
    }
    post("/admin/supporter-key", { count: true }).then(([ok, j]) => {
      if (ok) { say(j.handed_out); } else { out.replaceChildren(mk("p", "muted", j.error || "")); }
    }).catch(() => {});
  }

  function donations() {
    const D = data.donations, box = $("donations");
    checkPush().then(notifyRow);
    notifyRow();
    $("donations-all").textContent = D.error ? "" :
      "All time: " + num.format(D.count_all) + (D.count_all === 1 ? " donation" : " donations") +
      (D.count_all ? " · " + sums(D.sums_all) : "");
    if (D.error) {
      box.replaceChildren(mk("p", "error", "Could not ask Stripe (" + D.error + "). The key in " +
        "/usr/local/etc/iptv-donate.key needs PaymentIntents: Write, which includes reading them."));
      return;
    }
    if (!D.recent.length) { box.replaceChildren(mk("p", "empty", "No donations through the dialog yet.")); return; }
    let rel = null;
    try { rel = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }); } catch (e) { rel = null; }
    const ago = (t) => {
      const s = Date.now() / 1000 - t;
      if (!rel) { return ""; }
      if (s < 3600) { return rel.format(-Math.max(1, Math.round(s / 60)), "minute"); }
      if (s < 86400) { return rel.format(-Math.round(s / 3600), "hour"); }
      if (s < 86400 * 45) { return rel.format(-Math.round(s / 86400), "day"); }
      return rel.format(-Math.round(s / 86400 / 30), "month");
    };
    const ul = mk("ul", "don-list");
    D.recent.forEach((r) => {
      const li = mk("li");
      const when = mk("span", "don-when");
      when.append(mk("span", "", ago(r.t)), mk("small", "", stamp.format(new Date(r.t * 1000))));
      const who = mk("span", "don-who");
      who.append(mk("span", "don-name", r.who || "Anonymous"), mk("span", "chip " + (/Apple|Google/.test(r.how) ? "wallet" : "card"), r.how));
      li.append(mk("span", "don-heart", "\u2665"), mk("span", "don-amt", money(r.amount, r.currency)), who, when);
      ul.append(li);
    });
    box.replaceChildren(ul);
  }

  // -- what visitors did, by how they came ------------------------------------------
  function byChannel() {
    const rows = data.by_channel || [];
    if (!rows.length) { $("by-channel").replaceChildren(mk("p", "empty", "No visits in this period yet.")); return; }
    const most = Math.max(1, ...rows.map(([, r]) => r.visits));
    const meter = (n, of, cls, label) => {
      const p = of ? Math.round(n / of * 100) : 0, cell = mk("span", "m " + cls), track = mk("span", "track"), fill = mk("i");
      fill.style.width = p + "%";
      track.append(fill);
      cell.append(mk("span", "ml", label), track, mk("span", "mv", of ? p + "%" : "–"));
      return cell;
    };
    const box = mk("div", "chan-rows");
    const head = mk("div", "chan-row head");
    head.append(mk("span", "c-name", "Came from"), mk("span", "c-visits", "Visits"), mk("span", "m", "Read more than one page"),
                mk("span", "m", "Downloaded"), mk("span", "m", "Opened donate"), mk("span", "c-time", "Time per visit"));
    box.append(head);
    rows.forEach(([name, r]) => {
      const row = mk("div", "chan-row"), v = mk("span", "c-visits"), vb = mk("i");
      vb.style.width = Math.max(4, r.visits / most * 100) + "%";
      v.append(mk("b", "", num.format(r.visits)), vb);
      row.append(mk("span", "c-name", name), v, meter(r.more, r.visits, "m1", "Read more"),
                 meter(r.downloaded, r.visits, "m2", "Downloaded"), meter(r.donate, r.visits, "m3", "Opened donate"),
                 mk("span", "c-time", (r.timed !== undefined ? r.timed : r.visits) ? duration(r.time / (r.timed !== undefined ? r.timed : r.visits)) : "–"));
      box.append(row);
    });
    $("by-channel").replaceChildren(box);
  }

  // -- calendar years ----------------------------------------------------------------
  function years() {
    const Y = data.years || [];
    if (!Y.length) { $("years").replaceChildren(); return; }
    $("years").replaceChildren(...Y.map((y) => {
      const c = mk("article", "card year");
      const unique = y.unique !== null && y.unique !== undefined;
      c.append(mk("h3", "", String(y.year)));
      const dl = mk("dl");
      [[unique ? "People" : "Visits", num.format(unique ? y.unique : y.visitors)], ["Page views", num.format(y.views)],
       ["Downloads", num.format(y.downloads)]].forEach(([k, v]) => {
        const d = mk("div"); d.append(mk("dt", "", k), mk("dd", "", v)); dl.append(d);
      });
      c.append(dl, mk("p", "year-heart", y.donations ? num.format(y.donations) + (y.donations === 1 ? " donation · " : " donations · ") + sums(y.sums)
                                                   : "No donations"));
      return c;
    }));
  }

  // -- the visitor log -------------------------------------------------------------
  let logDay = "", logDays = [], logList = [], showRobots = false;
  let logFilter = null;          // {by, value, label}: one row of the overview; {label, keys}: a group of the Analysis tab
  let logVisitor = "";           // the log shows every visit of one visitor
  let logSpan = "day", logEnd = "";  // the log shows a day, week, month or year
  // Whether it shows the period we are in (today, this week...): then it
  // moves on with the clock - past midnight to the new day, and a reload
  // opens the period of the day it is reloaded on. A period chosen in the
  // past stays where it is.
  let logNow = true;
  let dlCounts = null;               // admin.py download_counts, from the newest answer
  try { showRobots = localStorage.getItem("admin-robots") === "1"; } catch (e) { /* hidden then */ }
  function badge(text, cls) { return mk("span", "badge" + (cls ? " " + cls : ""), text); }
  // How long single visits are kept (donate.py: STATS_VISITS_DAYS), in words.
  function keptFor() {
    const n = (data && data.visits_days) || 400;
    return n < 60 ? n + " days" : Math.round(n / 30.44) + " months";
  }
  // From iOS 26 Safari says iOS 18 for every newer system; its own version is
  // the system's. Visits counted before the collector knew it still say 18.
  function osText(v) {
    const m = /^(iOS|iPadOS) 18$/.exec(v.os || "");
    return m && v.browser === "Safari" && Number(v.bv) >= 26 ? m[1] + " " + v.bv : v.os;
  }
  const WHY = {
    acted: "a person: moved, touched, scrolled or typed",
    pages: "a person: read more than one page",
    time: "a person: looked at it 5 s or more",
    download: "a person: downloaded after a page",
    returning: "a person: came back on another day",
    headless: "likely a robot: a browser without a screen",
  };
  function judgedText(v) {
    if (v.why === "old") { return "likely a robot: " + v.browser + " " + v.bv + " is over two years out of date"; }
    return WHY[v.why] || (v.kind ? "not sure: a short visit without a move" : "");
  }
  // The time of a visit; from several days, its date on a line above.
  function timeCell(when, showDay) {
    const cell = mk("span", "v-time");
    if (showDay) { cell.append(mk("span", "d", dayFmt.format(when))); }
    cell.append(document.createTextNode(clock.format(when)));
    return cell;
  }
  // A visit kept only anonymously (after STATS_VISITS_DAYS): its day, no
  // time of day and no visitor - what stats_collect.py's anonymous() keeps.
  function anonRow(a) {
    const row = mk("details", "visit with-day anon" + (a.kind === "robot" ? " robot" : ""));
    const sum = mk("summary");
    const cell = mk("span", "v-time");
    cell.append(mk("span", "d", dayFmt.format(day(a.day))));
    sum.append(cell,
      mk("span", "v-where", flag(a.country) + countryName(a.country)),
      mk("span", "v-what", [a.browser, a.os, a.device].filter(Boolean).join(" · ")),
      mk("span", "v-from", a.channel ? a.channel + (a.site ? " · " + a.site : "") : (a.downloaded ? "Straight to a download" : "")),
      mk("span", "v-pages", (a.pages ? a.pages + (a.pages === 1 ? " page" : " pages") : "") +
         (a.timed ? " · about " + duration(a.time) : a.pages ? " · time unknown" : "")));
    const tags = mk("span", "v-tags");
    if (a.kind === "robot") { tags.append(badge("Likely robot", "robot")); }
    if (a.returning) { tags.append(badge("Returning")); }
    if (a.downloaded) { tags.append(badge("Downloaded", "good")); }
    if (a.donated) { tags.append(badge("\u2665 Donated", "heart")); }
    else if (a.donate) { tags.append(badge("Opened donate", "soft-heart")); }
    sum.append(tags);
    row.append(sum);
    const body = mk("div", "v-body");
    body.append(mk("p", "muted", "Kept without details after " + keptFor() + ": no time of day and no visitor, " +
                   "the time rounded to ten seconds."));
    row.append(body);
    return row;
  }
  function visitRow(v, showDay, fromVisitor) {
    const row = mk("details", "visit" + (v.kind === "robot" ? " robot" : "") + (showDay ? " with-day" : ""));
    // Which visit this is across a redraw: the live loop keeps open ones open.
    row.dataset.id = (v.day || "") + "|" + (v.start || "") + "|" + (v.id || "");
    const sum = mk("summary");
    const when = new Date(v.start * 1000);
    const pages = v.pages.length, dls = v.downloads.length;
    sum.append(
      timeCell(when, showDay),
      mk("span", "v-where", flag(v.country) + countryName(v.country)),
      mk("span", "v-what", [v.browser + (v.bv ? " " + v.bv : ""), osText(v), v.device].join(" · ")),
      mk("span", "v-from", v.channel ? v.channel + (v.ref ? " · " + v.ref : "") : (dls ? "Straight to a download" : "")),
      mk("span", "v-pages", (pages ? pages + (pages === 1 ? " page" : " pages") : "") +
         (v.time ? " · " + duration(v.time) : pages ? " · time unknown" : "")));
    const tags = mk("span", "v-tags");
    if (v.kind === "robot") { tags.append(badge("Likely robot", "robot")); }
    else if (v.kind === "unknown") { tags.append(badge("Not sure")); }
    if (v.returning) { tags.append(badge("Returning · " + v.returning + (v.returning === 1 ? " day" : " days") + " before")); }
    if (dls) { tags.append(badge("Downloaded", "good")); }
    (v.events || []).forEach((e) => tags.append(badge(e[1] === "donated" ? "\u2665 Donated" : "Opened donate", e[1] === "donated" ? "heart" : "soft-heart")));
    sum.append(tags);
    row.append(sum);

    const body = mk("div", "v-body");
    const steps = [];
    v.pages.forEach((p) => steps.push([p[0], (PAGE_NAMES[p[1]] || p[1]) + (p[2] ? " · looked at " + duration(p[2]) : "")]));
    v.downloads.forEach((d) => steps.push([d[0], "Downloaded " + d[1]]));
    (v.events || []).forEach((e) => steps.push([e[0], e[1] === "donated" ? "Donated" : "Opened the donate dialog"]));
    (v.acts || []).forEach((a) => { const t = actText(a); if (t) { steps.push([a[0], t]); } });
    steps.sort((a, b) => a[0] - b[0]);
    const ol = mk("ol", "steps");
    steps.forEach(([t, text]) => { const li = mk("li"); li.append(mk("span", "t", clockSec.format(new Date(t * 1000))), mk("span", "", text)); ol.append(li); });
    body.append(ol);
    const facts = mk("dl", "facts");
    if (v.ref_page) {
      const dd = mk("dd", "", "");
      const a = mk("a", "ext", v.ref_page);
      a.href = "https://" + v.ref_page;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      dd.append(a);
      facts.append(mk("dt", "", "Linked from"), dd);
    }
    [["Screen", v.screen], ["Page language", v.lang && (langNames ? langNames.of(v.lang) : v.lang)],
     ["Browser language", v.blang], ["Tagged link", v.source], ["Judged", judgedText(v)],
     ["Visitor", v.id]].forEach(([k, val]) => {
      if (!val) { return; }
      facts.append(mk("dt", "", k), mk("dd", "", val));
    });
    body.append(facts);
    if (!fromVisitor) {
      const all = mk("button", "link", "Every visit by this visitor");
      all.type = "button";
      all.addEventListener("click", () => { navStep(); showVisitor(v.id); });
      body.append(all);
    }
    row.append(body);
    return row;
  }
  // An update the app fetched for itself (stats_collect.py _app_update,
  // admin.py visits: type "update"). No page was seen, so it is no visit:
  // never among the visits, visitors or downloads counted above it.
  const isUpdate = (v) => v.type === "update";
  function updateRow(u, showDay) {
    const row = mk("details", "visit app-update" + (u.own ? " own" : "") + (showDay ? " with-day" : ""));
    row.dataset.id = (u.day || "") + "|" + (u.start || "") + "|" + (u.file || "");
    const test = TEST_FILE.test(u.file || "");
    const to = fileVersion(u.file) || "?";
    const sum = mk("summary");
    sum.append(
      timeCell(new Date((u.start || 0) * 1000), showDay),
      mk("span", "v-where", flag(u.country) + countryName(u.country)),
      mk("span", "v-what", "dopeIPTV app · " + fileKind(u.file)),
      mk("span", "v-from", "Updated itself"),
      mk("span", "v-pages", (u.from ? u.from + " \u2192 " : "to ") + to));
    const tags = mk("span", "v-tags");
    tags.append(badge(test ? "Test build" : "Release", "app"));
    if (u.own) { tags.append(badge("Your network")); }
    sum.append(tags);
    row.append(sum);
    const body = mk("div", "v-body");
    const facts = mk("dl", "facts");
    [["Updated", (u.from ? "from " + u.from + " " : "") + "to " + to + (test ? ", a test build" : ", a release")],
     ["File", u.file], ["System", [u.os, u.cpu].filter(Boolean).join(" · ")]].forEach(([k, val]) => {
      if (val) { facts.append(mk("dt", "", k), mk("dd", "", val)); }
    });
    body.append(facts, mk("p", "muted", "Fetched by the app itself from dopeiptv.org. No page was seen, so it is not " +
      "a visit and not among the visitors or downloads" + (u.own ? " - and from your own network, so not among the " +
      "update counts either." : "; it is among the updates on the overview.")));
    row.append(body);
    return row;
  }
  // What a visitor did on a page (public/events.js, donate/stats_events.py):
  // [time, event, what, hits].
  const SECTION_NAMES = { features: "Features", shots: "Screenshots", download: "Download", donate: "the donate band" };
  const SYSTEMS = { linux: "Linux", macos: "macOS", windows: "Windows" };
  function helpName(id) { return (data && data.help && data.help[id]) || id; }
  function actText(a) {
    const lang = (k) => { try { return langNames ? langNames.of(k) : k; } catch (e) { return k; } };
    switch (a[1]) {
      case "q": return "Opened the help question \u201c" + helpName(a[2]) + "\u201d";
      case "s": return "Searched the help for \u201c" + a[2] + "\u201d" +
        (a[3] >= 0 ? " \u00b7 " + (a[3] === 0 ? "nothing found" : a[3] + (a[3] === 1 ? " answer" : " answers")) : "");
      case "sec": return "Scrolled down to " + (SECTION_NAMES[a[2]] || a[2]);
      case "how": return "Opened the install steps for " + (SYSTEMS[a[2]] || a[2]);
      case "lang": { const p = String(a[2]).split(">"); return "Switched the language from " + lang(p[0]) + " to " + lang(p[1]); }
      case "out": return "Followed a link to " + a[2];
      default: return "";
    }
  }
  // What a period is called in the picker.
  function periodName(x) {
    if (logSpan === "week") {
      const s = day(x), e = day(x);
      e.setDate(e.getDate() + 6);
      return dayFmt.format(s) + " \u2013 " + dateFmt.format(e);
    }
    if (logSpan === "month") { return longMonth.format(day(x)); }
    if (logSpan === "year") { return x.slice(0, 4); }
    return longDay.format(day(x));
  }
  function loadDay(d) {
    $("visit-list").replaceChildren(mk("p", "muted", "Loading…"));
    fetch("/admin/visits?day=" + encodeURIComponent(d || "") + "&span=" + logSpan,
          { credentials: "same-origin", cache: "no-store" })
      .then((r) => { if (!r.ok) { throw new Error("the service answered " + r.status); } return r.json(); })
      .then((res) => {
        logDay = res.day; logEnd = res.end || res.day; logDays = res.periods || res.days || [];
        logNow = todayIso() >= logDay && todayIso() <= logEnd;
        if (res.dl) { dlCounts = res.dl; }
        const pick = $("day-pick");
        pick.replaceChildren(...(logDays.indexOf(logDay) < 0 ? [logDay].concat(logDays) : logDays).map((x) => {
          const o = mk("option", "", periodName(x)); o.value = x; return o;
        }));
        pick.value = logDay;
        const i = logDays.indexOf(logDay);
        $("day-prev").disabled = i < 0 || i >= logDays.length - 1;
        $("day-next").disabled = i <= 0;
        logList = res.visits || [];
        logFilter = null;
        logVisitor = "";
        $("visitor-head").hidden = true;
        if (window.LogTools) { window.LogTools.reset(); }
        drawLog();
      })
      .catch((e) => $("visit-list").replaceChildren(mk("p", "error", "Could not load the visits: " + e.message + ".")));
  }
  // Above the log: the downloads of today, yesterday, this week, month and
  // year, each a way to that period's visits, and the site's counter.
  const DL_CELLS = [["today", "Today", "day"], ["yesterday", "Yesterday", "day"], ["week", "This week", "week"],
                    ["month", "This month", "month"], ["year", "This year", "year"]];
  let liveNow = null;                // admin.py live: who is here, the newest downloads
  const dlSeen = {};                 // the counts last drawn, to show what came since
  // A release file as people know it: "dopeIPTV-1.4.4-macOS-arm64.dmg" -> "macOS, Apple silicon".
  function fileKind(f) {
    const n = String(f || "");
    if (/\.AppImage$/i.test(n)) { return "Linux AppImage" + (/aarch64|arm64/.test(n) ? " (ARM)" : ""); }
    if (/\.deb$/i.test(n)) { return "Linux .deb" + (/arm64/.test(n) ? " (ARM)" : ""); }
    if (/\.flatpak$/i.test(n)) { return "Flatpak"; }
    if (/macOS/i.test(n)) { return /arm64/.test(n) ? "macOS, Apple silicon" : "macOS, Intel"; }
    if (/windows/i.test(n)) { return "Windows" + (/arm64/.test(n) ? " (ARM)" : ""); }
    return n;
  }
  function ago(sec) {
    return sec < 60 ? "just now" : sec < 3600 ? Math.floor(sec / 60) + " min ago" : Math.floor(sec / 3600) + " h ago";
  }
  function drawDl() {
    const box = $("dl-strip"), c = dlCounts;
    box.hidden = !c;
    if (!c) { return; }
    const plain = !logFilter && !logVisitor;
    const cells = DL_CELLS.map(([key, label, span]) => {
      const p = c[key] || { n: 0, day: "" };
      const b = mk("button", "dl-cell");
      b.type = "button";
      const was = dlSeen[key + p.day];
      dlSeen[key + p.day] = p.n;
      b.append(mk("b", "", num.format(p.n)), mk("span", "", label));
      if (was !== undefined && p.n > was) {
        b.classList.add("up");
        b.append(mk("em", "dl-plus", "+" + num.format(p.n - was)));
      }
      b.setAttribute("aria-current", plain && logSpan === span && logDay === p.day ? "true" : "false");
      b.title = (p.day < c.since ? "Counted from " + longDay.format(day(c.since)) + ". " : "") + "Show these visits";
      b.addEventListener("click", () => {
        navStep();
        logSpan = span;
        loadDay(p.day);
        window.scrollTo({ top: 0 });
      });
      return b;
    });
    const all = mk("div", "dl-cell static");
    all.append(mk("b", "", num.format(c.all || 0)), mk("span", "", "All time"));
    all.title = "The site's own download counter since it began - likely robots included, unlike the rest";
    const title = mk("div", "dl-title");
    title.append(mk("span", "", "Downloads"));
    const L = liveNow;
    if (L) {
      const here = mk("span", "live", num.format(L.here) + " on the site now");
      here.prepend(mk("i", "", ""));
      here.title = "Visitors seen in the last " + Math.round(L.window / 60) + " minutes, likely robots left out. " +
        "Counts here update every 15 seconds while this page is open.";
      title.append(here);
    }
    const parts = [title, ...cells, all];
    if (L && L.updates_recent && L.updates_recent.length) {
      // [time, file, from version, country, own network]
      const upd = (u) => fileKind(u[1]) + ", " +
        (TEST_FILE.test(u[1]) ? "test build " : "release ") + fileVersion(u[1]) + (u[2] ? " (from " + u[2] + ")" : "") +
        (u[3] ? " · " + flag(u[3]) + " " + countryName(u[3]) : "") + (u[4] ? " · your own network" : "") +
        ", " + ago(Math.max(0, L.at - (u[0] || L.at)));
      const line = mk("p", "dl-latest");
      line.append(mk("span", "muted", "Latest update from the app: "), document.createTextNode(upd(L.updates_recent[0])));
      line.title = L.updates_recent.map(upd).join("\n");
      parts.push(line);
    }
    if (L && L.recent && L.recent.length) {
      const r = L.recent[0], line = mk("p", "dl-latest");
      line.append(mk("span", "muted", "Latest download: "), document.createTextNode(fileKind(r[1]) +
        (r[2] ? " from " + flag(r[2]) + " " + countryName(r[2]) : "") + ", " + ago(Math.max(0, L.at - r[0]))));
      line.title = L.recent.map((x) => fileKind(x[1]) + (x[2] ? " · " + countryName(x[2]) : "") + " · " +
        ago(Math.max(0, L.at - x[0]))).join("\n");
      parts.push(line);
    }
    box.replaceChildren(...parts);
  }
  // Live: the counts above the log and today's line on the overview, asked
  // again every 15 seconds while one of them is on screen - the service
  // reads the newest log lines first (stats_collect.collect_if_stale).
  let liveTimer = 0, liveBusy = false, liveRev = null, lastFull = Date.now(), overviewStale = false;
  function liveWanted() {
    return document.visibilityState === "visible" && (tab === "visitors" || tab === "overview" || tab === "analysis");
  }
  function todayIso() {
    const n = new Date();
    return n.getFullYear() + "-" + String(n.getMonth() + 1).padStart(2, "0") + "-" + String(n.getDate()).padStart(2, "0");
  }
  // Someone is choosing something (a list open, a field being typed in) or
  // reading a chart's tooltip: a redraw now would pull it away.
  function busyHere() {
    const ae = document.activeElement;
    return (ae && /^(SELECT|INPUT|TEXTAREA)$/.test(ae.tagName)) || !$("tip").hidden;
  }
  // The overview again, without "Loading…" and without moving the page.
  function quietLoad() {
    fetch("/admin/data?days=" + days, { credentials: "same-origin", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d || d.days !== data.days) { return; }
        data = d; overviewStale = false;
        if (tab === "overview" && !busyHere()) { render(); markSeen(); } else { overviewStale = true; }
        if (d.dl) { dlCounts = d.dl; drawDl(); }
      })
      .catch(() => { overviewStale = true; });
  }
  // The visits of the period shown again: open ones stay open, the one at
  // the top of the window stays where it is, and new ones light up briefly.
  function refreshLog() {
    const want = logDay, span = logSpan;
    fetch("/admin/visits?day=" + encodeURIComponent(want) + "&span=" + span, { credentials: "same-origin", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((res) => {
        if (!res || tab !== "visitors" || logFilter || logVisitor || logDay !== want || logSpan !== span || busyHere()) { return; }
        const box = $("visit-list");
        const known = new Set(logList.map((v) => (v.day || "") + "|" + (v.start || "") + "|" + (v.id || v.file || "")));
        const open = new Set([...box.querySelectorAll("details.visit[open]")].map((d) => d.dataset.id));
        const top = $("dl-strip").getBoundingClientRect().bottom > 0 ? null :
          [...box.querySelectorAll("details.visit")].find((d) => d.getBoundingClientRect().bottom > 80);
        const anchor = top ? { id: top.dataset.id, y: top.getBoundingClientRect().top } : null;
        if (res.dl) { dlCounts = res.dl; }
        logList = res.visits || [];
        drawLog();
        box.querySelectorAll("details.visit").forEach((d) => {
          if (open.has(d.dataset.id)) { d.open = true; }
          if (d.dataset.id && !known.has(d.dataset.id)) { d.classList.add("fresh"); }
        });
        if (anchor) {
          const again = [...box.querySelectorAll("details.visit")].find((d) => d.dataset.id === anchor.id);
          if (again) { window.scrollBy(0, again.getBoundingClientRect().top - anchor.y); }
        }
      })
      .catch(() => { /* the next change tries again */ });
  }
  function refreshView() {
    lastFull = Date.now();
    if (tab === "overview") { quietLoad(); } else { overviewStale = true; }
    if (window.AdminAnalysis) {
      if (tab === "analysis") { window.AdminAnalysis.refresh(); } else { window.AdminAnalysis.stale(); }
    }
    const today = todayIso();
    if (tab === "visitors" && !logFilter && !logVisitor && logDay && today >= logDay && today <= logEnd) { refreshLog(); }
    else { followClock(); }
  }
  // Past midnight (or into a new week, month, year) with the current period
  // shown: on to the new one.
  function followClock() {
    if (tab === "visitors" && !logFilter && !logVisitor && logNow && logEnd && todayIso() > logEnd && !busyHere()) {
      loadDay(todayIso());
    }
  }
  function liveTick() {
    clearTimeout(liveTimer);
    if (!liveWanted() || liveBusy) { return; }
    followClock();
    liveBusy = true;
    fetch("/admin/live", { credentials: "same-origin", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((L) => {
        if (!L) { return; }
        liveNow = L;
        dlCounts = L.dl;
        drawDl();
        const changed = liveRev !== null && L.rev !== liveRev;
        liveRev = L.rev;
        // Donations come from Stripe, cached five minutes: the overview is
        // fetched again that often even when no visit changed.
        if (changed || Date.now() - lastFull > 300000) { refreshView(); }
        const t = document.querySelector(".sum-head .today");
        if (t && data) {
          data.today = L.today;
          t.textContent = "Today so far: " + num.format(L.today.visitors) + " visitors · " + num.format(L.today.views) +
            " page views · " + num.format(L.here) + " on the site now";
        }
      })
      .catch(() => { /* asked again at the next tick */ })
      .then(() => { liveBusy = false; if (liveWanted()) { liveTimer = setTimeout(liveTick, 15000); } });
  }
  document.addEventListener("visibilitychange", () => { if (liveWanted()) { liveTick(); } else { clearTimeout(liveTimer); } });
  // The day's visits - or one row of the overview's, across its days - the
  // likely robots hidden unless asked for; the count says how many there
  // were either way.
  function drawLog() {
    drawDl();
    const f = logFilter;
    ["day-prev", "day-pick", "day-next", "spans"].forEach((id) => { $(id).hidden = !!f; });
    $("filter-clear").hidden = !f;
    $("filter-clear").textContent = f && f.back === "analysis" ? "\u2039 Back to the analysis" : "\u2039 Back";
    $("analyse-this").hidden = !(f && f.by && FIND_DIMS[f.by] && window.AdminAnalysis);
    document.querySelectorAll("#spans button").forEach((b) => b.setAttribute("aria-checked", b.dataset.span === logSpan ? "true" : "false"));
    const ups = logList.filter(isUpdate).length;
    const counted = logList.filter((v) => v.kind !== "robot" && !isUpdate(v));
    const robots = logList.filter((v) => v.kind === "robot").length;
    const anon = counted.filter((v) => v.anon).length;
    const people = new Set(counted.filter((v) => !v.anon).map((v) => v.id)).size;
    let span = "";
    if (f) {
      const kept = (data && data.visits_days) || 400;
      span = !days || days > kept ? " in the last " + keptFor() + " (single visits are kept that long)"
                                  : " in the last " + days + " days";
    }
    const visitsText = counted.length || !ups
      ? num.format(counted.length) + (counted.length === 1 ? " visit" : " visits") +
        (anon ? " \u00b7 " + num.format(anon) + " kept without details"
              : " by " + num.format(people) + (people === 1 ? " visitor" : " visitors"))
      : "No visits";
    $("day-sum").textContent = (f ? f.label + " \u00b7 " : "") + visitsText + span +
      (ups ? " · " + num.format(ups) + (ups === 1 ? " app update" : " app updates") : "") +
      (robots ? " · " + num.format(robots) + (robots === 1 ? " likely robot" : " likely robots") : "");
    // Each its own switch, named for what it does: the updates are the log
    // tools' filter "App update" (so the chip and the address agree).
    const LTools = window.LogTools;
    const upsHidden = !!LTools && LTools.flag("upd") === false;
    const ut = $("updates-toggle");
    ut.hidden = !ups || !LTools;
    ut.textContent = upsHidden ? "Show updates" : "Hide updates";
    ut.setAttribute("aria-pressed", upsHidden ? "false" : "true");
    const tog = $("robots-toggle");
    tog.hidden = !robots;
    tog.textContent = showRobots ? "Hide robots" : "Show robots";
    tog.setAttribute("aria-pressed", showRobots ? "true" : "false");
    const list = showRobots ? logList : logList.filter((v) => v.kind !== "robot");
    const none = f ? "No visits kept for this." : "No visits this " + logSpan + ".";
    const many = !!f || logSpan !== "day";
    const row = (v) => (isUpdate(v) ? updateRow(v, many) : v.anon ? anonRow(v) : visitRow(v, many));
    const LT = window.LogTools, shownFor = f ? "group" : logSpan;
    if (LT) { LT.bar($("log-tools"), list, shownFor, drawLog); }
    $("visit-list").replaceChildren(...(!list.length ? [mk("p", "empty", robots ? "Only likely robots." : none)]
      : LT ? LT.rows(list, shownFor, row, drawLog) : list.map(row)));
    writeHash();
  }
  // The overview's lists (admin.py FIND_BY) as the Analysis tab's data
  // points (analysis.js DIMS): a group of visits analysed from the log.
  const FIND_DIMS = { country: "country", channel: "channel", ref: "site", source: "source", page: "page", lang: "lang",
                      os_version: "osv", browser: "browser", browser_version: "bv", device: "device", screen: "screen",
                      download: "dl", file: "file" };
  // A row of the overview's lists, clicked: the visits it counted.
  function showFiltered(by, value, label) {
    logFilter = { by: by, value: value, label: label };
    logVisitor = "";
    logList = [];
    if (window.LogTools) { window.LogTools.reset(); }
    setTab("visitors", true);
    $("visitor-head").hidden = true;
    $("visit-list").replaceChildren(mk("p", "muted", "Loading…"));
    window.scrollTo({ top: 0 });
    const want = logFilter;
    fetch("/admin/find?by=" + encodeURIComponent(by) + "&value=" + encodeURIComponent(value) + "&days=" + days,
          { credentials: "same-origin", cache: "no-store" })
      .then((r) => { if (!r.ok) { throw new Error("the service answered " + r.status); } return r.json(); })
      .then((res) => { if (logFilter !== want) { return; } logList = res.visits || []; drawLog(); })
      .catch((e) => $("visit-list").replaceChildren(mk("p", "error", "Could not load the visits: " + e.message + ".")));
  }
  // A group of the Analysis tab, one by one: the visits its rows name
  // ([day, start, id]), the newest first; analysis.py caps them.
  function showGroup(label, keys) {
    logFilter = { label: label, back: "analysis", keys: keys };
    logVisitor = "";
    logList = [];
    if (window.LogTools) { window.LogTools.reset(); }
    setTab("visitors", true);
    $("visitor-head").hidden = true;
    $("visit-list").replaceChildren(mk("p", "muted", "Loading…"));
    window.scrollTo({ top: 0 });
    const want = logFilter;
    post("/admin/pick", { keys: keys })
      .then(([ok, res]) => {
        if (!ok) { throw new Error(res.error || "the service said no"); }
        if (logFilter !== want) { return; }
        logList = res.visits || [];
        drawLog();
      })
      .catch((e) => $("visit-list").replaceChildren(mk("p", "error", "Could not load the visits: " + e.message + ".")));
  }
  function showVisitor(id) {
    logVisitor = id;
    setTab("visitors", true);
    $("log-tools").hidden = true;
    fetch("/admin/visitor?id=" + encodeURIComponent(id), { credentials: "same-origin", cache: "no-store" })
      .then((r) => r.json())
      .then((res) => {
        if (logVisitor !== id) { return; }
        const list = res.visits || [];
        $("visitor-head").hidden = false;
        drawDl();
        $("visitor-title").textContent = "Visitor " + res.id + " · " + num.format(list.length) +
          (list.length === 1 ? " visit" : " visits") + " in the last " + keptFor();
        $("visit-list").replaceChildren(...list.map((v) => visitRow(v, true, true)));
        window.scrollTo({ top: 0 });
        writeHash();
      });
  }
  function setTab(name, keepLog) {
    tab = name;
    document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === name ? "true" : "false"));
    $("tab-overview").hidden = name !== "overview";
    $("tab-visitors").hidden = name !== "visitors";
    $("tab-analysis").hidden = name !== "analysis";
    $("tab-account").hidden = name !== "account";
    $("tab-builds").hidden = name !== "builds";
    $("tab-feedback").hidden = name !== "feedback";
    document.querySelector(".range").hidden = name === "visitors" || name === "account" || name === "builds";
    if (name === "feedback") {
      if (window.AdminFeedback) { window.AdminFeedback.show(); } else {
        $("fb-status").textContent = "The Feedback tab's script did not load. The service on the server serves it " +
          "once restarted: service iptv_donate restart - then reload this page.";
      }
    }
    if (name === "account" && window.AdminAccount) { window.AdminAccount.show(); }
    if (name === "builds") {
      if (window.AdminBuilds) { window.AdminBuilds.show(); } else {
        $("builds").replaceChildren(mk("p", "error", "The Builds tab's script did not load. The service on the " +
          "server serves it once restarted: service iptv_donate restart - then reload this page."));
      }
    }
    if (name === "visitors" && !keepLog) { loadDay(logDay); }
    if (name === "overview" && overviewStale && data) { quietLoad(); }
    if (liveWanted()) { liveTick(); } else { clearTimeout(liveTimer); }
    if (name === "analysis") {
      if (window.AdminAnalysis) { window.AdminAnalysis.show(); } else {
        $("an-bar").replaceChildren(mk("p", "error", "The Analysis tab's script did not load. The service on the " +
          "server serves it once restarted: service iptv_donate restart - then reload this page."));
        writeHash();
      }
    }
    // The visitor log writes the address once its visits are in, the
    // Analysis tab once drawn: a step back then scrolls to where it was.
    if (name === "overview" || name === "account" || name === "builds" || name === "feedback") { writeHash(); }
    // Drawn while the tab was hidden (data arriving, a resize), a chart
    // measured its box as 0 wide, fell back to its narrowest size and was
    // then stretched to the full width - tall as a wall. Drawn again now
    // the box has its width.
    if (name === "overview" && data) { visitsChart(); downloadsChart(); }
  }

  // -- the address and the history: where the page is, and the way back ------------
  // navStep() before a move to another view (a tab, a group, a visitor, a
  // drill-down) makes the next address a new entry in the history; anything
  // else (a filter, the sort) only rewrites the one it is on. Each entry
  // keeps how deep it is (the page's own Back shows from the second on), how
  // far down it was scrolled, and for a group of the Analysis tab its visits.
  let started = false;                 // not before readHash: the first setTab would wipe it
  let pending = false, restoreY = null;
  try { history.scrollRestoration = "manual"; } catch (e) { /* the browser's own then */ }
  function navStep() {
    pending = true;
    try { history.replaceState(Object.assign({}, history.state, { scroll: window.scrollY }), ""); } catch (e) { /* fine */ }
  }
  function depth() { return (history.state && history.state.depth) || 0; }
  function writeHash() {
    if (!started) { return; }
    let h = "", extra = {};
    if (tab === "visitors") {
      const p = new URLSearchParams();
      const f = logFilter;
      if (logVisitor) { p.set("visitor", logVisitor); }
      else if (f && f.keys) { p.set("group", "1"); extra = { keys: f.keys, label: f.label }; }
      else if (f && f.by) { p.set("by", f.by); p.set("value", f.value); p.set("label", f.label); }
      else { p.set("span", logSpan); if (logDay && !logNow) { p.set("day", logDay); } }
      const t = !logVisitor && window.LogTools ? window.LogTools.toHash() : "";
      if (t) { p.set("f", t); }
      h = "visitors?" + p.toString();
    } else if (tab === "analysis") {
      h = "analysis" + (window.AdminAnalysis ? window.AdminAnalysis.toHash() : "");
    } else if (tab === "account" || tab === "builds" || tab === "feedback") {
      h = tab;
    }
    const url = h ? "#" + h : location.pathname + location.search;
    const same = h ? location.hash === "#" + h : !location.hash;
    try {
      if (pending && !same) {
        history.pushState(Object.assign({ depth: depth() + 1, scroll: 0 }, extra), "", url);
      } else {
        history.replaceState(Object.assign({}, history.state, { depth: depth() }, extra), "", url);
      }
    } catch (e) { /* the address stays as it was */ }
    pending = false;
    $("nav-back").hidden = depth() < 1;
    if (restoreY !== null) { window.scrollTo(0, restoreY); restoreY = null; }
  }
  // Back from a view the page cannot rebuild by itself (reloaded with no
  // entry behind it): where its own Back button led before.
  function back(fallback) {
    if (depth() > 0) { history.back(); } else { fallback(); }
  }
  // At start: the page opened afresh shows the overview; reloaded, what it
  // showed (the scripts after this one are in by then).
  function readHash() {
    started = true;
    pending = false;
    const h = location.hash.replace(/^#/, ""), name = h.split("?")[0];
    const p = new URLSearchParams(h.indexOf("?") >= 0 ? h.slice(h.indexOf("?") + 1) : "");
    const st = history.state || {};
    if (name === "visitors") {
      if (window.LogTools) { window.LogTools.fromHash(p.get("f") || ""); }
      if (p.get("visitor")) { return showVisitor(p.get("visitor")); }
      if (p.get("group")) {
        if (Array.isArray(st.keys)) { return showGroup(st.label || "Visits", st.keys); }
        if (window.AdminAnalysis) { return setTab("analysis"); }
      }
      if (p.get("by") && p.get("value")) { return showFiltered(p.get("by"), p.get("value"), p.get("label") || p.get("value")); }
      if (["day", "week", "month", "year"].indexOf(p.get("span")) >= 0) { logSpan = p.get("span"); }
      if (/^\d{4}-\d\d-\d\d$/.test(p.get("day") || "")) { logDay = p.get("day"); }
      return setTab("visitors");
    }
    if (name === "feedback") { return setTab("feedback"); }
    if (name === "analysis" && window.AdminAnalysis) {
      window.AdminAnalysis.fromHash(h.slice("analysis".length));
      return setTab("analysis");
    }
    if (name === "account" || name === "builds") { return setTab(name); }
    setTab("overview");
  }

  function render() {
    if (!data) { return; }
    const T = data.totals, unique = T.unique !== null && T.unique !== undefined;
    $("h-visits").textContent = "Visitors and page views" + (per() === "day" ? "" : " per " + per());
    $("h-downloads").textContent = "Downloads per " + per();
    $("n-visits").textContent = num.format(unique ? T.unique : T.visitors) + (unique ? " different visitors" : " visits") +
      " · " + num.format(T.views) + " page views";
    const S = data.series, best = S.reduce((a, d, i) => (d.downloads > (S[a] || {}).downloads ? i : a), 0);
    $("n-downloads").textContent = num.format(T.downloads) + (T.downloads === 1 ? " download" : " downloads") +
      (T.downloads && S[best] ? " · most on " + pointLabel(S[best].date, best) : "");
    summary(); tiles(); funnel(); visitsChart(); downloadsChart(); visitsTable(); lists(); byChannel(); years(); donations(); supporterKeys();
    $("updated").textContent = data.updated ? "Counts updated " + stamp.format(new Date(data.updated)) + "." : "";
    $("kept").textContent = keptFor();
  }

  document.querySelectorAll(".range button").forEach((b) => {
    b.addEventListener("click", () => setRange(Number(b.dataset.days)));
  });
  $("table-toggle").addEventListener("click", (e) => {
    const t = $("table-visits"), open = t.hidden;
    t.hidden = !open;
    e.currentTarget.setAttribute("aria-expanded", open ? "true" : "false");
    e.currentTarget.textContent = open ? "Hide table" : "Show as table";
  });
  let resizeTimer = 0, lastW = window.innerWidth;
  window.addEventListener("resize", () => {
    if (window.innerWidth === lastW) { return; }
    lastW = window.innerWidth;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (data) { visitsChart(); downloadsChart(); } }, 120);
  });
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.tab !== tab) { navStep(); }
    setTab(b.dataset.tab);
  }));
  $("nav-back").addEventListener("click", () => history.back());
  $("day-pick").addEventListener("change", (e) => loadDay(e.target.value));
  $("day-prev").addEventListener("click", () => loadDay(logDays[logDays.indexOf(logDay) + 1]));
  $("day-next").addEventListener("click", () => loadDay(logDays[logDays.indexOf(logDay) - 1]));
  $("visitor-back").addEventListener("click", () => back(() => loadDay(logDay)));
  $("filter-clear").addEventListener("click", () => back(() => {
    if (logFilter && logFilter.back === "analysis") { setTab("analysis"); window.scrollTo({ top: 0 }); return; }
    loadDay(logDay);
  }));
  $("analyse-this").addEventListener("click", () => {
    const f = logFilter;
    if (!f || !window.AdminAnalysis) { return; }
    window.AdminAnalysis.focus(FIND_DIMS[f.by], f.value);
    navStep();
    setTab("analysis");
    window.scrollTo({ top: 0 });
  });
  // A day, a week, a month or a year: the period with today in it when the
  // one shown holds today, else the one that begins where this one does.
  document.querySelectorAll("#spans button").forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.span === logSpan) { return; }
    const now = new Date(), today = now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0") + "-" +
      String(now.getDate()).padStart(2, "0");
    const anchor = logDay && !(today >= logDay && today <= logEnd) ? logDay : today;
    navStep();
    logSpan = b.dataset.span;
    loadDay(anchor);
  }));
  $("updates-toggle").addEventListener("click", () => {
    const LT = window.LogTools;
    if (!LT) { return; }
    LT.setFlag("upd", LT.flag("upd") === false ? undefined : false);
    drawLog();
  });
  $("robots-toggle").addEventListener("click", () => {
    showRobots = !showRobots;
    try { localStorage.setItem("admin-robots", showRobots ? "1" : "0"); } catch (e) { /* this time only */ }
    drawLog();
  });
  // What analysis.js and logtools.js build on.
  window.AdminKit = {
    $: $, mk: mk, svg: svg, num: num, dayFmt: dayFmt, flag: flag, countryName: countryName, duration: duration,
    showTip: showTip, hideTip: hideTip, tipRow: tipRow, PAGE_NAMES: PAGE_NAMES, langNames: langNames,
    helpName: helpName, writeHash: writeHash, step: navStep, listCard: listCard, rankList: rankList,
    showFiltered: (by, value, label) => { navStep(); showFiltered(by, value, label); },
    showGroup: (label, keys) => { navStep(); showGroup(label, keys); },
    days: () => days,
    spanText: () => (days ? (days === 365 ? "the last 12 months" : "the last " + days + " days") : "everything kept"),
  };
  setRange(days);
  // The other scripts (defer) run after this one: the address is read once
  // they are in, so a reload on the Analysis tab finds it.
  setTab("overview");
  window.addEventListener("DOMContentLoaded", readHash);
  // Back and Forward, and an address typed or pasted: the view of the entry,
  // scrolled to where it was left.
  window.addEventListener("popstate", () => {
    restoreY = (history.state && history.state.scroll) || 0;
    readHash();
  });

  // Pull down to reload, in the app on the iPhone's Home Screen. iOS gives
  // a Home Screen web app no pull-to-refresh of its own (Safari's belongs
  // to the browser), and closing the app was the only way to load what was
  // new. At the top of the page, a pull past the mark reloads it.
  (function pullToReload() {
    const standalone = window.navigator.standalone === true ||
      (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches);
    if (!standalone || !("ontouchstart" in window)) { return; }
    const MARK = 70;                   // px of travel, after the drag's damping
    const knob = document.createElement("div");
    knob.className = "ptr";
    knob.setAttribute("aria-hidden", "true");
    document.body.append(knob);
    let from = null, travel = 0;
    const reset = () => {
      knob.classList.remove("ready");
      knob.style.transform = "";
      knob.style.opacity = "";
    };
    document.addEventListener("touchstart", (e) => {
      from = window.scrollY <= 0 && e.touches.length === 1 ? e.touches[0].clientY : null;
      travel = 0;
    }, { passive: true });
    document.addEventListener("touchmove", (e) => {
      if (from === null) { return; }
      if (window.scrollY > 0) { from = null; reset(); return; }
      travel = Math.min(Math.max(0, e.touches[0].clientY - from) * 0.5, MARK + 24);
      knob.style.transform = `translate(-50%, ${travel - 44}px) rotate(${travel * 4}deg)`;
      knob.style.opacity = String(Math.min(1, travel / MARK));
      knob.classList.toggle("ready", travel >= MARK);
    }, { passive: true });
    document.addEventListener("touchend", () => {
      if (from === null) { return; }
      from = null;
      if (travel >= MARK) {
        knob.classList.add("spin");
        window.location.reload();
      } else {
        reset();
      }
    }, { passive: true });
  })();
})();
