/* The admin page: /admin/data drawn as tiles, two charts, top lists and the
 * donations, and the visitor log (/admin/visits, /admin/visitor) one visit at
 * a time. An external file for the site's CSP (script-src 'self'); the
 * charts are SVG made here, sizes set through the DOM, never inline styles.
 */
(function () {
  "use strict";
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

  let days = 30, data = null;
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
  }
  function load() {
    status(data ? "" : "Loading…");
    fetch("/admin/data?days=" + days, { credentials: "same-origin", cache: "no-store" })
      .then((r) => { if (!r.ok) { throw new Error("the service answered " + r.status); } return r.json(); })
      .then((d) => { data = d; status(""); render(); })
      .catch((e) => status("Could not load the numbers: " + e.message + "."));
  }

  // -- tiles -----------------------------------------------------------------------
  function delta(now, before) {
    if (before === undefined) { return ""; }
    if (!before) { return now ? "new" : ""; }
    const p = Math.round((now - before) / before * 100);
    return (p > 0 ? "▲ " : p < 0 ? "▼ " : "") + Math.abs(p) + "%";
  }
  function tile(label, value, note, d, small) {
    const t = mk("div", "kpi");
    t.append(mk("div", "label", label));
    t.append(mk("div", "value" + (small ? " small" : ""), value));
    const n = mk("div", "note");
    if (d) { n.append(mk("span", "delta", d), document.createTextNode(" vs " + periodText())); }
    else if (note) { n.textContent = note; }
    t.append(n);
    return t;
  }
  function tiles() {
    const T = data.totals, B = data.before || {}, D = data.donations;
    // Different people over the whole span where the hashes reach (400
    // days); visits are a person on a day, summed.
    const unique = T.unique !== null && T.unique !== undefined;
    const visitorsNote = num.format(T.visits || T.visitors) + " visits · " + num.format(T.returning || 0) + " returning";
    const avg = T.visits ? T.engaged / T.visits : 0;
    const bounce = T.visits ? Math.round(T.bounces / T.visits * 100) : 0;
    const opened = (data.top.events || []).find((e) => e[0] === "donate");
    $("kpis").replaceChildren(
      tile(unique ? "Unique visitors" : "Visitors", num.format(unique ? T.unique : T.visitors), visitorsNote,
           unique && B.unique !== undefined && B.unique !== null ? delta(T.unique, B.unique) : ""),
      tile("Page views", num.format(T.views), "from " + num.format(T.requests || 0) + " page requests, robots included", ""),
      tile("Time per visit", T.visits ? duration(avg) : "–", T.visits ? bounce + "% left within 10 s" : "", "", true),
      tile("Downloads", num.format(T.downloads), num.format(data.downloads_all_time) + " on the site's counter", ""),
      tile("Donations", num.format(D.count), D.error ? "Stripe did not answer" :
           sums(D.sums) + (opened ? " · dialog opened " + num.format(opened[1]) + "×" : "")),
      tile("Visitors today", num.format(data.today.visitors), num.format(data.today.views) + " page views", "")
    );
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
  function listCard(title, rows, name, more) {
    const c = mk("section", "card");
    const head = mk("div", "card-head");
    head.append(mk("h2", "", title));
    c.append(head);
    c.append(rankList(rows, name));
    if (more) { c.append(mk("h3", "subhead", more[0]), rankList(more[1], more[2])); }
    return c;
  }
  function rankList(rows, name) {
    if (!rows || !rows.length) { return mk("p", "empty", "Nothing yet."); }
    const top = rows[0][1] || 1, ul = mk("ul", "rank");
    rows.forEach(([k, v]) => {
      const li = mk("li"), bar = mk("span", "bar"), fill = mk("i");
      fill.style.width = Math.max(1.5, v / top * 100) + "%";
      bar.append(fill);
      li.append(mk("span", "k", name ? name(k) : k), mk("span", "v", num.format(v)), bar);
      li.title = (name ? name(k) : k) + ": " + num.format(v);
      ul.append(li);
    });
    return ul;
  }
  function lists() {
    const L = data.top;
    const lang = (k) => { try { return langNames ? langNames.of(k) : k; } catch (e) { return k; } };
    const countries = listCard("Countries", L.countries, (k) => flag(k) + countryName(k));
    if (!data.geo) {
      countries.append(mk("p", "muted", "The country database is fetched by the quarter-hour run (DB-IP, once a month)."));
    }
    const sites = listCard("Sites that linked here", L.refs, (k) => k === "other" ? "Other sites" : k,
                           ["Tagged links", L.sources]);
    sites.append(mk("p", "muted", "Tagged links carry ?ref=name, e.g. iptv.dope.rs/?ref=github."));
    $("lists").replaceChildren(
      listCard("How they found the site", L.channels),
      sites,
      countries,
      listCard("Pages", L.pages, (k) => PAGE_NAMES[k] || k),
      listCard("Languages", L.langs, lang),
      listCard("Browsers", L.browsers, null, ["Versions", L.browser_versions]),
      listCard("Systems", L.os_versions),
      listCard("Devices", L.devices, null, ["Screens", L.screens]),
      listCard("Downloads", L.downloads, null, ["Files", L.files, (k) => k === "other" ? "Other files" : k.replace(/^dopeIPTV-/, "")])
    );
  }

  // -- donations -------------------------------------------------------------------
  function donations() {
    const D = data.donations, box = $("donations");
    $("donations-all").textContent = D.error ? "" :
      "All time: " + num.format(D.count_all) + (D.count_all === 1 ? " donation" : " donations") +
      (D.count_all ? " · " + sums(D.sums_all) : "");
    if (D.error) {
      box.replaceChildren(mk("p", "error", "Could not ask Stripe (" + D.error + "). The key in " +
        "/usr/local/etc/iptv-donate.key needs PaymentIntents: Write, which includes reading them."));
      return;
    }
    if (!D.recent.length) { box.replaceChildren(mk("p", "empty", "No donations through the dialog yet.")); return; }
    const t = mk("table"), head = mk("tr");
    [["When", ""], ["Amount", "num"], ["How", "hide-phone"], ["From", ""]].forEach(([h, c]) => head.append(mk("th", c, h)));
    const thead = mk("thead"); thead.append(head);
    const tb = mk("tbody");
    D.recent.forEach((r) => {
      const tr = mk("tr");
      tr.append(mk("td", "", stamp.format(new Date(r.t * 1000))), mk("td", "num", money(r.amount, r.currency)),
                mk("td", "hide-phone", r.how), mk("td", "who", r.who || "–"));
      tb.append(tr);
    });
    t.append(thead, tb);
    const wrap = mk("div", "table-wrap"); wrap.append(t);
    box.replaceChildren(wrap);
  }

  // -- what visitors did, by how they came ------------------------------------------
  function byChannel() {
    const rows = data.by_channel || [];
    if (!rows.length) { $("by-channel").replaceChildren(mk("p", "empty", "No visits in this period yet.")); return; }
    const pct = (n, of) => of ? Math.round(n / of * 100) + "%" : "–";
    const t = mk("table"), head = mk("tr");
    [["Came from", ""], ["Visits", "num"], ["Read more than one page", "num hide-phone"], ["Downloaded", "num"],
     ["Opened donate", "num hide-phone"], ["Time per visit", "num"]].forEach(([h, c]) => head.append(mk("th", c, h)));
    const thead = mk("thead"); thead.append(head);
    const tb = mk("tbody");
    rows.forEach(([name, r]) => {
      const tr = mk("tr");
      tr.append(mk("td", "", name), mk("td", "num", num.format(r.visits)), mk("td", "num hide-phone", pct(r.more, r.visits)),
                mk("td", "num", pct(r.downloaded, r.visits)), mk("td", "num hide-phone", pct(r.donate, r.visits)),
                mk("td", "num", duration(r.visits ? r.time / r.visits : 0)));
      tb.append(tr);
    });
    t.append(thead, tb);
    $("by-channel").replaceChildren(t);
  }

  // -- calendar years ----------------------------------------------------------------
  function years() {
    const Y = data.years || [];
    if (!Y.length) { $("years").replaceChildren(mk("p", "empty", "Nothing yet.")); return; }
    const t = mk("table"), head = mk("tr");
    [["Year", ""], ["Unique visitors", "num"], ["Page views", "num"], ["Downloads", "num"], ["Donations", "num"], ["Donated", "num hide-phone"]]
      .forEach(([h, c]) => head.append(mk("th", c, h)));
    const thead = mk("thead"); thead.append(head);
    const tb = mk("tbody");
    Y.forEach((y) => {
      const tr = mk("tr");
      const u = y.unique !== null && y.unique !== undefined ? num.format(y.unique) : num.format(y.visitors) + " visits";
      tr.append(mk("td", "", y.year), mk("td", "num", u), mk("td", "num", num.format(y.views)),
                mk("td", "num", num.format(y.downloads)), mk("td", "num", num.format(y.donations)),
                mk("td", "num hide-phone", sums(y.sums)));
      tb.append(tr);
    });
    t.append(thead, tb);
    $("years").replaceChildren(t);
  }

  // -- the visitor log -------------------------------------------------------------
  let logDay = "", logDays = [];
  function badge(text, cls) { return mk("span", "badge" + (cls ? " " + cls : ""), text); }
  function visitRow(v, showDay) {
    const row = mk("details", "visit");
    const sum = mk("summary");
    const when = new Date(v.start * 1000);
    const pages = v.pages.length, dls = v.downloads.length;
    sum.append(
      mk("span", "v-time", (showDay ? dateFmt.format(when) + " " : "") + clock.format(when)),
      mk("span", "v-where", flag(v.country) + countryName(v.country)),
      mk("span", "v-what", [v.browser + (v.bv ? " " + v.bv : ""), v.os, v.device].join(" · ")),
      mk("span", "v-from", v.channel ? v.channel + (v.ref ? " · " + v.ref : "") : (dls ? "Straight to a download" : "")),
      mk("span", "v-pages", (pages ? pages + (pages === 1 ? " page" : " pages") : "") + (v.time ? " · " + duration(v.time) : "")));
    const tags = mk("span", "v-tags");
    if (v.returning) { tags.append(badge("Returning · " + v.returning + (v.returning === 1 ? " day" : " days") + " before")); }
    if (dls) { tags.append(badge("Downloaded", "good")); }
    (v.events || []).forEach((e) => tags.append(badge(e[1] === "donated" ? "Donated" : "Opened donate", e[1] === "donated" ? "good" : "")));
    sum.append(tags);
    row.append(sum);

    const body = mk("div", "v-body");
    const steps = [];
    v.pages.forEach((p) => steps.push([p[0], (PAGE_NAMES[p[1]] || p[1]) + (p[2] ? " · looked at " + duration(p[2]) : "")]));
    v.downloads.forEach((d) => steps.push([d[0], "Downloaded " + d[1]]));
    (v.events || []).forEach((e) => steps.push([e[0], e[1] === "donated" ? "Donated" : "Opened the donate dialog"]));
    steps.sort((a, b) => a[0] - b[0]);
    const ol = mk("ol", "steps");
    steps.forEach(([t, text]) => { const li = mk("li"); li.append(mk("span", "t", clockSec.format(new Date(t * 1000))), mk("span", "", text)); ol.append(li); });
    body.append(ol);
    const facts = mk("dl", "facts");
    [["Screen", v.screen], ["Page language", v.lang && (langNames ? langNames.of(v.lang) : v.lang)],
     ["Browser language", v.blang], ["Tagged link", v.source], ["Visitor", v.id]].forEach(([k, val]) => {
      if (!val) { return; }
      facts.append(mk("dt", "", k), mk("dd", "", val));
    });
    body.append(facts);
    if (!showDay) {
      const all = mk("button", "link", "Every visit by this visitor");
      all.type = "button";
      all.addEventListener("click", () => showVisitor(v.id));
      body.append(all);
    }
    row.append(body);
    return row;
  }
  function loadDay(d) {
    $("visit-list").replaceChildren(mk("p", "muted", "Loading…"));
    fetch("/admin/visits?day=" + encodeURIComponent(d || ""), { credentials: "same-origin", cache: "no-store" })
      .then((r) => { if (!r.ok) { throw new Error("the service answered " + r.status); } return r.json(); })
      .then((res) => {
        logDay = res.day; logDays = res.days || [];
        const pick = $("day-pick");
        pick.replaceChildren(...(logDays.indexOf(logDay) < 0 ? [logDay].concat(logDays) : logDays).map((x) => {
          const o = mk("option", "", longDay.format(day(x))); o.value = x; return o;
        }));
        pick.value = logDay;
        const i = logDays.indexOf(logDay);
        $("day-prev").disabled = i < 0 || i >= logDays.length - 1;
        $("day-next").disabled = i <= 0;
        const list = res.visits || [];
        const people = new Set(list.map((v) => v.id)).size;
        $("day-sum").textContent = num.format(list.length) + (list.length === 1 ? " visit" : " visits") +
          " by " + num.format(people) + (people === 1 ? " visitor" : " visitors");
        $("visitor-head").hidden = true;
        $("visit-list").replaceChildren(...(list.length ? list.map((v) => visitRow(v, false))
          : [mk("p", "empty", "No visits this day.")]));
      })
      .catch((e) => $("visit-list").replaceChildren(mk("p", "error", "Could not load the visits: " + e.message + ".")));
  }
  function showVisitor(id) {
    fetch("/admin/visitor?id=" + encodeURIComponent(id), { credentials: "same-origin", cache: "no-store" })
      .then((r) => r.json())
      .then((res) => {
        const list = res.visits || [];
        $("visitor-head").hidden = false;
        $("visitor-title").textContent = "Visitor " + res.id + " · " + num.format(list.length) +
          (list.length === 1 ? " visit" : " visits") + " in the last " + ((data && data.visits_days) || 30) + " days";
        $("visit-list").replaceChildren(...list.map((v) => visitRow(v, true)));
        window.scrollTo({ top: 0, behavior: "smooth" });
      });
  }
  function setTab(name) {
    document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === name ? "true" : "false"));
    $("tab-overview").hidden = name !== "overview";
    $("tab-visitors").hidden = name !== "visitors";
    document.querySelector(".range").hidden = name !== "overview";
    try { localStorage.setItem("admin-tab", name); } catch (e) { /* fine */ }
    if (name === "visitors") { loadDay(logDay); }
  }

  function render() {
    if (!data) { return; }
    $("h-visits").textContent = "Visitors and page views" + (per() === "day" ? "" : " per " + per());
    $("h-downloads").textContent = "Downloads per " + per();
    tiles(); visitsChart(); downloadsChart(); visitsTable(); lists(); byChannel(); years(); donations();
    $("updated").textContent = data.updated ? "Counts updated " + stamp.format(new Date(data.updated)) + "." : "";
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
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
  $("day-pick").addEventListener("change", (e) => loadDay(e.target.value));
  $("day-prev").addEventListener("click", () => loadDay(logDays[logDays.indexOf(logDay) + 1]));
  $("day-next").addEventListener("click", () => loadDay(logDays[logDays.indexOf(logDay) - 1]));
  $("visitor-back").addEventListener("click", () => loadDay(logDay));
  setRange(days);
  let tab = "overview";
  try { tab = localStorage.getItem("admin-tab") === "visitors" ? "visitors" : "overview"; } catch (e) { /* default */ }
  setTab(tab);
})();
