/* The admin page: /admin/data drawn as tiles, two charts, top lists and the
 * donations. An external file for the site's CSP (script-src 'self'); the
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
  let langNames = null;
  try { langNames = new Intl.DisplayNames(["en"], { type: "language" }); } catch (e) { /* codes then */ }

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
    const since = data.before ? "" : "since " + dateFmt.format(day(data.first));
    $("kpis").replaceChildren(
      tile("Visitors", num.format(T.visitors), since, delta(T.visitors, B.visitors)),
      tile("Page views", num.format(T.views), since, delta(T.views, B.views)),
      tile("Downloads", num.format(T.downloads), num.format(data.downloads_all_time) + " on the site's counter",
           ""),
      tile("Donations", num.format(D.count), D.error ? "Stripe did not answer" : sums(D.sums)),
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
  const PAGE_NAMES = { "/": "Home", "/help/": "Help" };
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
    $("lists").replaceChildren(
      listCard("Pages", L.pages, (k) => PAGE_NAMES[k] || k),
      listCard("Came from", L.refs, (k) => k === "other" ? "Other sites" : k),
      listCard("Languages", L.langs, lang),
      listCard("Systems", L.os),
      listCard("Devices", L.devices),
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

  // -- calendar years ----------------------------------------------------------------
  function years() {
    const Y = data.years || [];
    if (!Y.length) { $("years").replaceChildren(mk("p", "empty", "Nothing yet.")); return; }
    const t = mk("table"), head = mk("tr");
    [["Year", ""], ["Visitors", "num"], ["Page views", "num"], ["Downloads", "num"], ["Donations", "num"], ["Donated", "num hide-phone"]]
      .forEach(([h, c]) => head.append(mk("th", c, h)));
    const thead = mk("thead"); thead.append(head);
    const tb = mk("tbody");
    Y.forEach((y) => {
      const tr = mk("tr");
      tr.append(mk("td", "", y.year), mk("td", "num", num.format(y.visitors)), mk("td", "num", num.format(y.views)),
                mk("td", "num", num.format(y.downloads)), mk("td", "num", num.format(y.donations)),
                mk("td", "num hide-phone", sums(y.sums)));
      tb.append(tr);
    });
    t.append(thead, tb);
    $("years").replaceChildren(t);
  }

  function render() {
    if (!data) { return; }
    $("h-visits").textContent = "Visitors and page views" + (per() === "day" ? "" : " per " + per());
    $("h-downloads").textContent = "Downloads per " + per();
    tiles(); visitsChart(); downloadsChart(); visitsTable(); lists(); years(); donations();
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
  setRange(days);
})();
