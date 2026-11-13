/* The admin page's Analysis tab: every visit kept in detail of the span
 * (/admin/rows, donate/analysis.py) as a row, and all the sums done here,
 * so any breakdown, split, measure and filter is a click away.
 *
 *   filters  apply to the whole tab: a row clicked anywhere narrows
 *            everything to that group, and they combine
 *   patterns plain sentences for what stands out, from groups of 5 visits
 *            or more only
 *   explore  break down by a data point, optionally split by a second (a
 *            pivot), with a measure
 *   charts   over time, when they come, how long and how much they read,
 *            system against the file downloaded, browser language against
 *            the page's, and what they do on the pages (events.js)
 *
 * Each part can be hidden (the chips on top, remembered in this browser);
 * the filters and the explorer's choices live in the address, so a reload
 * keeps them. Any group opens one visit at a time in the visitor log
 * (POST /admin/pick).
 *
 * Shared pieces come from admin.js (window.AdminKit); colours from
 * admin.css (--s1 for one series, its tints for amounts). An external file
 * for the CSP; sizes set through the DOM, never inline styles.
 */
(function () {
  "use strict";
  const K = window.AdminKit;
  if (!K) { return; }
  const { $, mk, num } = K;
  const MIN = 5;                           // a group under this says nothing
  const WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  // The site's languages (site/lang/*.json): a browser language outside
  // them sees the site in another.
  const SITE_LANGS = "ar de el en es fa fr he hi hr id it ja ko nl pl pt ru sr sv sw th tr uk ur vi zh".split(" ");
  const SECTIONS = [["features", "Features"], ["shots", "Screenshots"], ["download", "Download"], ["donate", "Donate band"]];
  const TIMES = [["unknown", "Time unknown"], ["<10 s", "Under 10 s"], ["10-30 s", "10-30 s"], ["30-60 s", "30 s - 1 min"],
                 ["1-3 min", "1-3 min"], ["3-10 min", "3-10 min"], ["10+ min", "10 min or more"]];

  const pct = (x) => (x === null || x === undefined || isNaN(x) ? "–" : Math.round(x * 100) + "%");
  const lang = (k) => { try { return K.langNames ? K.langNames.of(k) : k; } catch (e) { return k; } };
  function timeBucket(r) {
    const t = r.time;
    if (!t) { return "unknown"; }
    return t < 10 ? "<10 s" : t < 30 ? "10-30 s" : t < 60 ? "30-60 s" : t < 180 ? "1-3 min" : t < 600 ? "3-10 min" : "10+ min";
  }

  // -- data points and measures ----------------------------------------------------
  // get: a row's value, or a list of them (a visit can read several pages);
  // order: "num" keeps the natural order instead of the largest first;
  // find: the visitor log's filter for it (admin.py FIND_BY), if there is one.
  const DIMS = {
    country: { label: "Country", get: (r) => r.country, name: (v) => K.flag(v) + K.countryName(v), find: "country" },
    channel: { label: "How they came", get: (r) => r.channel, find: "channel" },
    site: { label: "Site that linked", get: (r) => r.site || "(no site)", find: "ref" },
    source: { label: "Tagged link", get: (r) => r.source || "(none)", find: "source" },
    os: { label: "System", get: (r) => r.os },
    osv: { label: "System version", get: (r) => r.osv, find: "os_version" },
    browser: { label: "Browser", get: (r) => r.browser, find: "browser" },
    bv: { label: "Browser version", get: (r) => r.bv, find: "browser_version" },
    device: { label: "Device", get: (r) => r.device || "Unknown", find: "device" },
    screen: { label: "Screen", get: (r) => r.screen || "Unknown", find: "screen" },
    lang: { label: "Page language", get: (r) => r.lang, name: lang, find: "lang" },
    blang: { label: "Browser language", get: (r) => r.blang || "?", name: (v) => (v === "?" ? "Unknown" : lang(v)) },
    page: { label: "Page read", get: (r) => (r.paths.length ? r.paths : ["(none)"]), name: (v) => (K.PAGE_NAMES[v] || v), find: "page" },
    pages: { label: "Pages read", get: (r) => (r.pages >= 5 ? "5+" : String(r.pages)), order: "num" },
    timeb: { label: "Time on site", get: timeBucket, name: (v) => (TIMES.find((t) => t[0] === v) || [v, v])[1], order: "list",
             list: TIMES.map((t) => t[0]) },
    dl: { label: "Downloaded for", get: (r) => (r.dl.length ? r.dl : ["Nothing"]), find: "download" },
    file: { label: "File downloaded", get: (r) => (r.files.length ? r.files : ["Nothing"]),
            name: (v) => v.replace(/^dopeIPTV-/, ""), find: "file" },
    ret: { label: "First or returning", get: (r) => (r.ret ? "Returning" : "First visit") },
    kind: { label: "Judged", get: (r) => r.kind, name: (v) => ({ person: "Person", unknown: "Not sure", robot: "Likely robot" }[v] || v) },
    wd: { label: "Weekday", get: (r) => r.wd, name: (v) => WD[v], order: "num" },
    hour: { label: "Hour", get: (r) => r.hour, name: (v) => String(v).padStart(2, "0") + ":00", order: "num" },
    day: { label: "Day", get: (r) => r.day, name: (v) => K.dayFmt.format(new Date(v + "T12:00:00")), order: "num" },
  };
  const avg = (g, f) => (g.length ? g.reduce((s, r) => s + f(r), 0) / g.length : null);
  const share = (g, f) => (g.length ? g.filter(f).length / g.length : null);
  const read = (g) => g.filter((r) => r.pages > 0);
  const MEASURES = {
    visits: { label: "Visits", calc: (g) => g.length, fmt: (x) => num.format(x) },
    visitors: { label: "Visitors", calc: (g) => new Set(g.map((r) => r.id)).size, fmt: (x) => num.format(x) },
    dlrate: { label: "Share that downloaded", calc: (g) => share(g, (r) => r.dl.length), fmt: pct, rate: true },
    downloads: { label: "Downloads", calc: (g) => g.reduce((s, r) => s + r.dl.length, 0), fmt: (x) => num.format(x) },
    pagesavg: { label: "Pages per visit", calc: (g) => avg(read(g), (r) => r.pages), fmt: (x) => (x === null ? "–" : x.toFixed(1)), rate: true },
    timeavg: { label: "Time per visit", calc: (g) => avg(g.filter((r) => r.time > 0), (r) => r.time),
               fmt: (x) => (x === null ? "–" : K.duration(x)), rate: true },
    morerate: { label: "Share that read more than one page", calc: (g) => share(read(g), (r) => r.pages > 1), fmt: pct, rate: true },
    retrate: { label: "Share that came back", calc: (g) => share(g, (r) => r.ret > 0), fmt: pct, rate: true },
    donaterate: { label: "Share that opened donate", calc: (g) => share(g, (r) => r.donate), fmt: pct, rate: true },
  };

  // -- state -------------------------------------------------------------------------
  let all = [], loadedFor = null, robots = false, span = { first: "", last: "" };
  let filters = [];                        // [{dim, value}]
  const ex = { by: "country", split: "", measure: "visits" };

  function rowsOf(res) {
    const f = res.fields;
    return res.rows.map((a) => { const o = {}; f.forEach((k, i) => { o[k] = a[i]; }); return o; });
  }
  function values(dim, r) { const v = DIMS[dim].get(r); return Array.isArray(v) ? v : [v]; }
  function matches(r) { return filters.every((f) => values(f.dim, r).some((v) => String(v) === String(f.value))); }
  function current() { return all.filter((r) => (robots || r.kind !== "robot") && matches(r)); }
  function base() { return all.filter((r) => robots || r.kind !== "robot"); }
  function nameOf(dim, v) { const d = DIMS[dim]; return d.name ? d.name(v) : String(v); }
  function groupBy(rows, dim) {
    const g = new Map();
    rows.forEach((r) => values(dim, r).forEach((v) => { if (!g.has(v)) { g.set(v, []); } g.get(v).push(r); }));
    return g;
  }
  function ordered(dim, g, measure) {
    const d = DIMS[dim], m = MEASURES[measure];
    const list = Array.from(g.entries());
    if (d.order === "num") { return list.sort((a, b) => (a[0] > b[0] ? 1 : a[0] < b[0] ? -1 : 0)); }
    if (d.order === "list") { return list.sort((a, b) => d.list.indexOf(a[0]) - d.list.indexOf(b[0])); }
    // Rates order by size of group first: a 100% of two visits is not news.
    return list.sort((a, b) => (m.rate ? b[1].length - a[1].length : (m.calc(b[1]) || 0) - (m.calc(a[1]) || 0)));
  }
  function addFilter(dim, value) {
    K.step();                              // a drill-down: Back undoes it
    if (!filters.some((f) => f.dim === dim && String(f.value) === String(value))) { filters.push({ dim: dim, value: value }); }
    draw();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // -- building blocks ---------------------------------------------------------------
  function card(id, title, note) {
    const box = $(id);
    const head = mk("div", "card-head");
    head.append(mk("h2", "", title));
    if (note) { head.append(mk("span", "muted", note)); }
    box.replaceChildren(head);
    return box;
  }
  function pickable(el, dim, value, label) {
    el.classList.add("pick");
    el.tabIndex = 0;
    el.setAttribute("role", "button");
    el.title = "Narrow everything to " + label;
    const go = () => addFilter(dim, value);
    el.addEventListener("click", go);
    el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
  }
  function tip(el, lines) {
    el.addEventListener("mousemove", (e) => K.showTip(e, lines.map((l, i) => (i ? mk("div", "", l) : mk("b", "", l)))));
    el.addEventListener("mouseleave", K.hideTip);
  }
  // A bar for a share of the largest, behind the row's text (admin.css .rank).
  function barRow(label, value, frac, extra) {
    const li = mk("li"), fill = mk("i", "fill");
    fill.style.width = Math.max(2, Math.min(1, frac) * 100) + "%";
    li.append(fill, mk("span", "k", label), mk("span", "v", value));
    if (extra) { li.classList.add("with-x"); li.append(mk("span", "x", extra)); }
    return li;
  }
  function select(id, label, options, value, onChange) {
    const wrap = mk("label", "an-ctl");
    wrap.append(mk("span", "", label));
    const s = mk("select");
    s.id = id;
    options.forEach(([v, t]) => { const o = mk("option", "", t); o.value = v; s.append(o); });
    s.value = value;
    s.addEventListener("change", () => onChange(s.value));
    wrap.append(s);
    return wrap;
  }

  // -- the parts of the tab, each shown or not ----------------------------------------
  const PARTS = [["an-patterns", "Patterns"], ["an-explore", "Explore"], ["an-trend", "Over time"],
                 ["an-when", "When"], ["an-engage", "How much"], ["an-match", "Systems and files"],
                 ["an-langs", "Languages"], ["an-acts", "On the pages"]];
  let hidden = {};
  try { hidden = JSON.parse(localStorage.getItem("admin-an-hidden") || "{}") || {}; } catch (e) { hidden = {}; }
  function drawParts() {
    const box = $("an-parts");
    box.replaceChildren(mk("span", "muted", "Show"), ...PARTS.map(([id, label]) => {
      const c = mk("button", "lt-chip", label);
      c.type = "button";
      c.setAttribute("aria-pressed", hidden[id] ? "false" : "true");
      c.addEventListener("click", () => {
        if (hidden[id]) { delete hidden[id]; } else { hidden[id] = 1; }
        try { localStorage.setItem("admin-an-hidden", JSON.stringify(hidden)); } catch (e) { /* this time */ }
        draw();
      });
      return c;
    }));
    PARTS.forEach(([id]) => { $(id).hidden = !!hidden[id]; });
  }
  // The visits of a group, in full, in the visitor log: the newest
  // thousand at most (analysis.py PICK_MAX).
  function openVisits(rows, label) {
    const keys = rows.slice().sort((a, b) => b.start - a.start).slice(0, 1000).map((r) => [r.day, r.start, r.id]);
    K.showGroup(label + (rows.length > 1000 ? " \u00b7 the newest 1,000" : ""), keys);
  }
  function filterLabel() {
    return filters.length ? filters.map((f) => DIMS[f.dim].label + ": " + nameOf(f.dim, f.value)).join(" \u00b7 ") : "All visits analysed";
  }

  // -- the bar on top: what is shown, the filters ------------------------------------
  function drawBar(cur) {
    const box = $("an-bar");
    const people = new Set(cur.map((r) => r.id)).size;
    const text = mk("div", "an-sum");
    text.append(mk("strong", "", num.format(cur.length) + (cur.length === 1 ? " visit" : " visits")),
                document.createTextNode(" by " + num.format(people) + (people === 1 ? " visitor" : " visitors") +
                                        " · " + K.spanText() + " · the visits kept in detail"));
    const chips = mk("div", "an-chips");
    filters.forEach((f, i) => {
      const c = mk("button", "chip-f");
      c.type = "button";
      c.append(mk("span", "", DIMS[f.dim].label + ": " + nameOf(f.dim, f.value)), mk("span", "x", "×"));
      c.title = "Remove this filter";
      c.addEventListener("click", () => { filters.splice(i, 1); draw(); });
      chips.append(c);
    });
    if (filters.length > 1) {
      const clear = mk("button", "link", "Clear all");
      clear.type = "button";
      clear.addEventListener("click", () => { filters = []; draw(); });
      chips.append(clear);
    }
    if (!filters.length) { chips.append(mk("span", "muted", "Click any row below to narrow everything to that group.")); }
    const tog = mk("label", "an-tog");
    const cb = mk("input");
    cb.type = "checkbox";
    cb.checked = robots;
    cb.addEventListener("change", () => { robots = cb.checked; draw(); });
    tog.append(cb, document.createTextNode(" Include likely robots"));
    const open = mk("button", "lt-toggle", "Show these " + num.format(cur.length) + " visits");
    open.type = "button";
    open.hidden = !cur.length;
    open.addEventListener("click", () => openVisits(cur, filterLabel()));
    const row = mk("div", "an-acts-row");
    row.append(tog, open);
    box.replaceChildren(text, chips, row);
  }

  // -- patterns: what stands out -----------------------------------------------------
  function drawPatterns(cur) {
    const box = card("an-patterns", "Patterns", "groups of " + MIN + " visits or more");
    const ul = mk("ul", "an-findings");
    const say = (parts, dim, value) => {
      const li = mk("li");
      parts.forEach((p) => li.append(typeof p === "string" ? document.createTextNode(p) : p));
      if (dim) { pickable(li, dim, value, nameOf(dim, value)); }
      ul.append(li);
    };
    const b = (t) => mk("strong", "", t);
    if (!cur.length) { box.append(mk("p", "empty", "No visits kept in detail for this yet.")); return; }
    const all0 = cur, dl0 = MEASURES.dlrate.calc(all0);
    const countries = ordered("country", groupBy(cur, "country"), "visits");
    const top3 = countries.slice(0, 3), top3n = top3.reduce((s, c) => s + c[1].length, 0);
    say([b(num.format(cur.length)), " visits from ", b(num.format(countries.length)),
         countries.length === 1 ? " country" : " countries",
         countries.length > 1 ? ". " + top3.map((c) => K.countryName(c[0])).join(", ") + " bring " + pct(top3n / cur.length) + " of them." : "."]);
    // The largest countries, each in a line: how they came, what they use.
    countries.filter((c) => c[1].length >= MIN && c[0] !== "unknown").slice(0, 5).forEach(([c, g]) => {
      const lead = (dim) => { const o = ordered(dim, groupBy(g, dim), "visits")[0]; return o ? [nameOf(dim, o[0]), o[1].length / g.length] : null; };
      const ch = lead("channel"), os = lead("os");
      // The site that linked, from the visits of that way in only.
      const si = ordered("site", groupBy(g.filter((r) => r.site && nameOf("channel", r.channel) === ch[0]), "site"), "visits")[0];
      say([b(K.flag(c) + K.countryName(c)), " · " + num.format(g.length) + " visits: " + pct(ch[1]) + " came by " +
           ch[0].toLowerCase() + (si ? " (most from " + si[0] + ")" : "") + ", " + pct(os[1]) + " use " + os[0] +
           ", " + pct(MEASURES.dlrate.calc(g)) + " downloaded"], "country", c);
    });
    // Which way in, which system, gives downloads.
    const best = (dim, label) => {
      const gs = ordered(dim, groupBy(cur, dim), "visits").filter((x) => x[1].length >= MIN);
      if (gs.length < 2) { return; }
      gs.sort((a, b2) => MEASURES.dlrate.calc(b2[1]) - MEASURES.dlrate.calc(a[1]));
      const top = gs[0], low = gs[gs.length - 1];
      say([label(top[0]), " download most often: ", b(pct(MEASURES.dlrate.calc(top[1]))), " (all: " + pct(dl0) + "); least: " +
           nameOf(dim, low[0]) + " " + pct(MEASURES.dlrate.calc(low[1])) + "."], dim, top[0]);
    };
    best("channel", (v) => "Visitors who came by " + nameOf("channel", v).toLowerCase());
    best("os", (v) => nameOf("os", v) + " visitors");
    const phones = cur.filter((r) => r.device === "Mobile" || r.device === "Tablet");
    if (phones.length >= MIN) {
      say(["Phones and tablets: ", b(pct(phones.length / cur.length)), " of visits; " + pct(MEASURES.dlrate.calc(phones)) +
           " of them downloaded (they cannot run the app there)."], "device", "Mobile");
    }
    const back = cur.filter((r) => r.ret > 0), first = cur.filter((r) => !r.ret);
    if (back.length >= MIN && first.length >= MIN) {
      say(["Returning visitors: ", b(pct(back.length / cur.length)), " of visits; " + pct(MEASURES.dlrate.calc(back)) +
           " downloaded, against " + pct(MEASURES.dlrate.calc(first)) + " of first visits."], "ret", "Returning");
    }
    const home = cur.filter((r) => r.paths.indexOf("/") >= 0);
    const reached = (id) => home.filter((r) => r.acts.some((a) => a[0] === "sec" && a[1] === id)).length;
    if (home.length >= MIN && home.some((r) => r.acts.some((a) => a[0] === "sec"))) {
      say(["Of front-page visits, ", b(pct(reached("download") / home.length)), " got down to the download section and " +
           pct(reached("donate") / home.length) + " to the donate band."]);
    }
    const empty = new Map();
    cur.forEach((r) => r.acts.forEach((a) => { if (a[0] === "s" && a[2] === 0) { empty.set(a[1], (empty.get(a[1]) || 0) + 1); } }));
    if (empty.size) {
      say(["Searched the help and found nothing: ",
           b(Array.from(empty.entries()).sort((a, c) => c[1] - a[1]).slice(0, 5).map((e) => "“" + e[0] + "”").join(", ")), "."]);
    }
    const foreign = ordered("blang", groupBy(cur.filter((r) => r.blang && SITE_LANGS.indexOf(r.blang) < 0), "blang"), "visits")[0];
    if (foreign && foreign[1].length >= MIN) {
      say(["Most common browser language the site is not translated into: ", b(lang(foreign[0])),
           " (" + num.format(foreign[1].length) + " visits)."], "blang", foreign[0]);
    }
    box.append(ul);
  }

  // -- explore: breakdown, split, measure --------------------------------------------
  function drawExplore(cur) {
    const box = card("an-explore", "Explore", "click a row to narrow everything to it");
    const dims = Object.keys(DIMS).map((k) => [k, DIMS[k].label]);
    const ctl = mk("div", "an-ctls");
    ctl.append(select("an-by", "Break down by", dims, ex.by, (v) => { ex.by = v; drawExplore(current()); }),
               select("an-split", "Split by", [["", "Nothing"]].concat(dims.filter((d) => d[0] !== ex.by)), ex.split,
                      (v) => { ex.split = v; drawExplore(current()); }),
               select("an-measure", "Measure", Object.keys(MEASURES).map((k) => [k, MEASURES[k].label]), ex.measure,
                      (v) => { ex.measure = v; drawExplore(current()); }));
    box.append(ctl);
    if (!cur.length) { box.append(mk("p", "empty", "No visits to break down.")); return; }
    const m = MEASURES[ex.measure], baseVal = m.calc(cur);
    const groups = ordered(ex.by, groupBy(cur, ex.by), ex.measure);
    if (!ex.split || ex.split === ex.by) {
      const shown = groups.slice(0, 40);
      const max = Math.max(...shown.map((g) => m.calc(g[1]) || 0), 0) || 1;
      const ul = mk("ul", "rank an-rank");
      shown.forEach(([v, g]) => {
        const val = m.calc(g);
        const small = m.rate && g.length < MIN;
        const li = barRow(nameOf(ex.by, v), m.fmt(val), (val || 0) / max,
                          (m.rate ? num.format(g.length) + " visits" : pct(g.length / cur.length) + " of visits"));
        if (small) { li.classList.add("small"); li.title = "Fewer than " + MIN + " visits: too few to say much"; }
        pickable(li, ex.by, v, nameOf(ex.by, v));
        const go = mk("button", "link go", "Visits");
        go.type = "button";
        go.title = "Show these visits one by one";
        go.addEventListener("click", (e) => {
          e.stopPropagation();
          openVisits(g, (filters.length ? filterLabel() + " \u00b7 " : "") + DIMS[ex.by].label + ": " + nameOf(ex.by, v));
        });
        li.append(go);
        ul.append(li);
      });
      box.append(mk("p", "an-base muted", m.label + " for all shown: " + m.fmt(baseVal) +
                    (groups.length > shown.length ? " · the largest " + shown.length + " of " + groups.length : "")), ul);
      return;
    }
    // A pivot: rows by the first, columns by the second, the measure in the
    // cells, shaded by size.
    const cols = ordered(ex.split, groupBy(cur, ex.split), "visits").slice(0, 7).map((c) => c[0]);
    const rowsShown = groups.slice(0, 15);
    const cells = rowsShown.map(([v, g]) => {
      const sub = groupBy(g, ex.split);
      return cols.map((c) => { const gg = sub.get(c) || []; return { v: gg.length ? m.calc(gg) : null, n: gg.length }; });
    });
    const max = Math.max(...cells.flat().map((c) => c.v || 0), 0) || 1;
    const table = mk("table", "an-pivot");
    const hr = mk("tr");
    hr.append(mk("th", "", DIMS[ex.by].label + " \\ " + DIMS[ex.split].label));
    cols.forEach((c) => hr.append(mk("th", "num", nameOf(ex.split, c))));
    hr.append(mk("th", "num", "All"));
    table.append(hr);
    rowsShown.forEach(([v, g], i) => {
      const tr = mk("tr");
      const th = mk("th", "", nameOf(ex.by, v));
      pickable(th, ex.by, v, nameOf(ex.by, v));
      tr.append(th);
      cells[i].forEach((c, j) => {
        const td = mk("td", "num cell", c.n ? m.fmt(c.v) : "");
        if (c.n) {
          td.style.setProperty("--a", String(Math.round(Math.min(1, (c.v || 0) / max) * 100) / 100));
          if (m.rate && c.n < MIN) { td.classList.add("small"); }
          tip(td, [nameOf(ex.by, v) + " · " + nameOf(ex.split, cols[j]), m.label + ": " + m.fmt(c.v), num.format(c.n) + " visits"]);
          td.addEventListener("click", () => { K.step(); filters.push({ dim: ex.by, value: v }, { dim: ex.split, value: cols[j] }); draw(); });
        }
        tr.append(td);
      });
      tr.append(mk("td", "num all", m.fmt(m.calc(g))));
      table.append(tr);
    });
    const wrap = mk("div", "table-wrap");
    wrap.append(table);
    box.append(mk("p", "an-base muted", m.label + "; cells under " + MIN + " visits are faint. Click a cell for both filters."), wrap);
  }

  // -- over time ---------------------------------------------------------------------
  function drawTrend(cur) {
    const box = card("an-trend", "Over time", MEASURES[ex.measure].label.toLowerCase());
    if (!cur.length) { box.append(mk("p", "empty", "Nothing yet.")); return; }
    const days = [];
    const first = span.first || all.reduce((m, r) => (r.day < m ? r.day : m), span.last);
    for (let d = new Date(first + "T12:00:00"); ; d.setDate(d.getDate() + 1)) {
      const x = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
      if (x > span.last || days.length > 800) { break; }
      days.push(x);
    }
    const len = days.length;
    const key = (d) => {
      if (len <= 120) { return d; }
      const dt = new Date(d + "T12:00:00");
      if (len <= 550) {
        dt.setDate(dt.getDate() - ((dt.getDay() + 6) % 7));
        return dt.getFullYear() + "-" + String(dt.getMonth() + 1).padStart(2, "0") + "-" + String(dt.getDate()).padStart(2, "0");
      }
      return d.slice(0, 7) + "-01";
    };
    const m = MEASURES[ex.measure];
    const buckets = Array.from(new Set(days.map(key))).sort();
    const g = groupBy(cur, "day");
    const pts = buckets.map((b) => {
      const rs = [];
      g.forEach((v, d) => { if (key(d) === b) { rs.push(...v); } });
      return { b: b, v: rs.length ? m.calc(rs) : (m.rate ? null : 0), n: rs.length };
    });
    const W = Math.max(300, box.clientWidth - 32), H = 200, ml = 44, mr = 12, mt = 10, mb = 26;
    const max = Math.max(...pts.map((p) => p.v || 0), m.rate && ex.measure.endsWith("rate") ? 0.05 : 1);
    const top = ex.measure.endsWith("rate") ? Math.min(1, Math.ceil(max * 10) / 10) : niceTop(max);
    const x = (i) => ml + (pts.length < 2 ? (W - ml - mr) / 2 : i * (W - ml - mr) / (pts.length - 1));
    const y = (v) => mt + (H - mt - mb) * (1 - v / top);
    const s = K.svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", class: "an-line" });
    [0, top / 2, top].forEach((v) => {
      const yy = Math.round(y(v)) + 0.5;
      K.svg("line", { x1: ml, x2: W - mr, y1: yy, y2: yy, class: v ? "grid" : "base" }, s);
      K.svg("text", { x: ml - 8, y: yy + 4, "text-anchor": "end", class: "tick" }, s).textContent = m.fmt(v);
    });
    let d = "";
    pts.forEach((p, i) => { if (p.v !== null) { d += (d ? "L" : "M") + x(i).toFixed(1) + " " + y(p.v).toFixed(1); } });
    K.svg("path", { d: d, class: "line l1" }, s);
    const every = Math.max(1, Math.ceil(pts.length / 6));
    pts.forEach((p, i) => {
      if (i % every === 0) {
        K.svg("text", { x: x(i), y: H - 6, "text-anchor": "middle", class: "tick" }, s).textContent =
          K.dayFmt.format(new Date(p.b + "T12:00:00"));
      }
      if (p.v === null) { return; }
      const dot = K.svg("circle", { cx: x(i), cy: y(p.v), r: pts.length > 60 ? 2 : 3.5, class: "dot1" }, s);
      const hit = K.svg("rect", { x: x(i) - 8, y: mt, width: 16, height: H - mt - mb, class: "hit" }, s);
      tip(hit, [K.dayFmt.format(new Date(p.b + "T12:00:00")) + (len > 120 ? " (" + (len <= 550 ? "week" : "month") + ")" : ""),
                m.label + ": " + m.fmt(p.v), num.format(p.n) + " visits"]);
      void dot;
    });
    box.append(s);
  }
  function niceTop(max) {
    for (let p = 1; ; p *= 10) {
      const hit = [1, 2, 5].map((k) => k * p).find((st) => Math.ceil(max / st) <= 4);
      if (hit) { return Math.max(1, Math.ceil(max / hit)) * hit; }
    }
  }

  // -- when they come ----------------------------------------------------------------
  function drawWhen(cur) {
    const box = card("an-when", "When they come", "visits by weekday and hour, server time");
    const grid = Array.from({ length: 7 }, () => new Array(24).fill(0));
    cur.forEach((r) => { grid[r.wd][r.hour] += 1; });
    const max = Math.max(1, ...grid.flat());
    const t = mk("div", "an-heat");
    t.append(mk("span", "h0"));
    for (let h = 0; h < 24; h++) { t.append(mk("span", "hh", h % 3 === 0 ? String(h).padStart(2, "0") : "")); }
    grid.forEach((row, w) => {
      const lab = mk("span", "wd", WD[w]);
      pickable(lab, "wd", w, WD[w]);
      t.append(lab);
      row.forEach((n, h) => {
        const c = mk("span", "hc" + (n ? "" : " zero"));
        c.style.setProperty("--a", String(Math.round(n / max * 100) / 100));
        tip(c, [WD[w] + " " + String(h).padStart(2, "0") + ":00–" + String(h + 1).padStart(2, "0") + ":00", num.format(n) + " visits"]);
        if (n) { c.addEventListener("click", () => { K.step(); filters.push({ dim: "wd", value: w }, { dim: "hour", value: h }); draw(); }); }
        t.append(c);
      });
    });
    box.append(t);
  }

  // -- how long and how much ---------------------------------------------------------
  function drawEngage(cur) {
    const box = card("an-engage", "How much they read", "share of visits");
    const two = mk("div", "an-two");
    const list = (title, dim, keys) => {
      const sec = mk("div");
      sec.append(mk("h3", "subhead", title));
      const g = groupBy(cur, dim), n = cur.length || 1;
      const max = Math.max(1, ...keys.map((k) => (g.get(k) || []).length));
      const ul = mk("ul", "rank");
      keys.forEach((k) => {
        const c = (g.get(k) || []).length;
        const li = barRow(nameOf(dim, k), pct(c / n), c / max, num.format(c));
        if (c) { pickable(li, dim, k, nameOf(dim, k)); }
        ul.append(li);
      });
      sec.append(ul);
      return sec;
    };
    two.append(list("Pages read", "pages", ["1", "2", "3", "4", "5+"]), list("Time on site", "timeb", TIMES.map((x) => x[0])));
    box.append(two);
  }

  // -- system against the file downloaded --------------------------------------------
  function drawMatch(cur) {
    const box = card("an-match", "Systems and the files they download", "visits that downloaded, by the file's platform");
    const plats = ["Linux", "macOS", "Windows", "Python", "Other"].filter((p) => cur.some((r) => r.dl.indexOf(p) >= 0));
    if (!plats.length) { box.append(mk("p", "empty", "No downloads in these visits.")); return; }
    const g = ordered("os", groupBy(cur, "os"), "visits");
    const table = mk("table", "an-pivot");
    const hr = mk("tr");
    hr.append(mk("th", "", "System"), mk("th", "num", "Visits"), mk("th", "num", "Downloaded"));
    plats.forEach((p) => hr.append(mk("th", "num", p)));
    table.append(hr);
    let max = 1;
    const rowsData = g.map(([os, rs]) => {
      const counts = plats.map((p) => rs.filter((r) => r.dl.indexOf(p) >= 0).length);
      max = Math.max(max, ...counts);
      return [os, rs, counts];
    });
    rowsData.forEach(([os, rs, counts]) => {
      const tr = mk("tr");
      const th = mk("th", "", os);
      pickable(th, "os", os, os);
      tr.append(th, mk("td", "num", num.format(rs.length)), mk("td", "num", pct(MEASURES.dlrate.calc(rs))));
      counts.forEach((c, j) => {
        const td = mk("td", "num cell", c ? num.format(c) : "");
        if (c) {
          td.style.setProperty("--a", String(Math.round(c / max * 100) / 100));
          tip(td, [os + " visitors who downloaded for " + plats[j], num.format(c) + " visits"]);
        }
        tr.append(td);
      });
      table.append(tr);
    });
    const wrap = mk("div", "table-wrap");
    wrap.append(table);
    box.append(wrap);
  }

  // -- languages ---------------------------------------------------------------------
  function drawLangs(cur) {
    const box = card("an-langs", "Languages", "the browser's against the page's");
    const g = ordered("blang", groupBy(cur.filter((r) => r.blang), "blang"), "visits").slice(0, 15);
    if (!g.length) { box.append(mk("p", "empty", "No browser languages yet.")); return; }
    const table = mk("table", "an-pivot");
    const hr = mk("tr");
    hr.append(mk("th", "", "Browser language"), mk("th", "num", "Visits"), mk("th", "num", "Shown in it"), mk("th", "", "Most often shown in"));
    table.append(hr);
    g.forEach(([bl, rs]) => {
      const tr = mk("tr");
      const th = mk("th", "", lang(bl) + (SITE_LANGS.indexOf(bl) < 0 ? " · not on the site" : ""));
      pickable(th, "blang", bl, lang(bl));
      const top = ordered("lang", groupBy(rs, "lang"), "visits")[0];
      tr.append(th, mk("td", "num", num.format(rs.length)), mk("td", "num", pct(share(rs, (r) => r.lang === bl))),
                mk("td", "", top ? lang(top[0]) : ""));
      table.append(tr);
    });
    const wrap = mk("div", "table-wrap");
    wrap.append(table);
    box.append(wrap);
  }

  // -- what they do on the pages -----------------------------------------------------
  function drawActs(cur) {
    const box = card("an-acts", "What they do on the pages", "events.js; from November 2026");
    const count = (ev, f) => {
      const m = new Map();
      cur.forEach((r) => r.acts.forEach((a) => { if (a[0] === ev && (!f || f(a))) { m.set(a[1], (m.get(a[1]) || 0) + 1); } }));
      return Array.from(m.entries()).sort((a, b) => b[1] - a[1]);
    };
    if (!cur.some((r) => r.acts.length)) {
      box.append(mk("p", "empty", "Nothing told yet: these come from pages loaded after this was put on the site."));
      return;
    }
    const lists = mk("div", "an-three");
    const listOf = (title, entries, name, unit) => {
      const sec = mk("div");
      sec.append(mk("h3", "subhead", title));
      if (!entries.length) { sec.append(mk("p", "empty", "None yet.")); return sec; }
      const max = entries[0][1] || 1;
      const ul = mk("ul", "rank");
      entries.slice(0, 12).forEach(([k, n]) => ul.append(barRow(name ? name(k) : k, num.format(n) + (unit || ""), n / max)));
      sec.append(ul);
      return sec;
    };
    const home = cur.filter((r) => r.paths.indexOf("/") >= 0);
    const reach = mk("div");
    reach.append(mk("h3", "subhead", "How far down the front page"));
    const ul = mk("ul", "rank");
    SECTIONS.forEach(([id, label]) => {
      const n = home.filter((r) => r.acts.some((a) => a[0] === "sec" && a[1] === id)).length;
      ul.append(barRow(label, pct(home.length ? n / home.length : null), home.length ? n / home.length : 0, num.format(n)));
    });
    reach.append(ul);
    const helpVisits = cur.filter((r) => r.paths.indexOf("/help/") >= 0).length;
    lists.append(reach,
      listOf("Help questions opened" + (helpVisits ? " (" + num.format(helpVisits) + " help visits)" : ""), count("q"), K.helpName),
      listOf("Searched the help and found nothing", count("s", (a) => a[2] === 0), (q) => "“" + q + "”"),
      listOf("Searched the help", count("s"), (q) => "“" + q + "”"),
      listOf("Install steps opened", count("how"), (k) => ({ linux: "Linux", macos: "macOS", windows: "Windows" }[k] || k)),
      listOf("Language switched", count("lang"), (k) => k.split(">").map(lang).join(" → ")),
      listOf("Links followed to other sites", count("out")));
    box.append(lists);
  }

  // -- drawing it all ------------------------------------------------------------------
  function draw() {
    const cur = current();
    drawParts();
    drawBar(cur);
    drawPatterns(cur);
    drawExplore(cur);
    drawTrend(cur);
    drawWhen(cur);
    drawEngage(cur);
    drawMatch(cur);
    drawLangs(cur);
    drawActs(cur);
    void base;
    K.writeHash();
  }
  function show() {
    const days = K.days();
    if (loadedFor === days) { draw(); return; }
    $("an-bar").replaceChildren(mk("p", "muted", "Loading…"));
    fetch("/admin/rows?days=" + days, { credentials: "same-origin", cache: "no-store" })
      .then((r) => { if (!r.ok) { throw new Error("the service answered " + r.status); } return r.json(); })
      .then((res) => { all = rowsOf(res); loadedFor = days; span = { first: res.first, last: res.last }; draw(); })
      .catch((e) => $("an-bar").replaceChildren(mk("p", "error", "Could not load the visits: " + e.message + ".")));
  }
  // The address: "?by=os&split=&m=dlrate&f=country=SE,dl=Linux&robots=1".
  function toHash() {
    const p = new URLSearchParams();
    if (ex.by !== "country") { p.set("by", ex.by); }
    if (ex.split) { p.set("split", ex.split); }
    if (ex.measure !== "visits") { p.set("m", ex.measure); }
    if (filters.length) { p.set("f", filters.map((f) => f.dim + "=" + encodeURIComponent(f.value)).join(",")); }
    if (robots) { p.set("robots", "1"); }
    const s = p.toString();
    return s ? "?" + s : "";
  }
  function fromHash(h) {
    const p = new URLSearchParams((h || "").replace(/^\?/, ""));
    if (DIMS[p.get("by")]) { ex.by = p.get("by"); }
    ex.split = DIMS[p.get("split")] ? p.get("split") : "";
    if (MEASURES[p.get("m")]) { ex.measure = p.get("m"); }
    robots = p.get("robots") === "1";
    filters = [];
    (p.get("f") || "").split(",").forEach((x) => {
      const i = x.indexOf("=");
      if (i < 1 || !DIMS[x.slice(0, i)]) { return; }
      const dim = x.slice(0, i), raw = decodeURIComponent(x.slice(i + 1));
      // Numbers stay numbers (hour, weekday), as the rows hold them.
      filters.push({ dim: dim, value: (dim === "hour" || dim === "wd") ? Number(raw) : raw });
    });
  }
  // New visits came in (admin.js, the live loop): the rows again, quietly,
  // drawn with every choice kept; or, while another tab shows, next time.
  function refresh() {
    const days = K.days();
    fetch("/admin/rows?days=" + days, { credentials: "same-origin", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((res) => {
        if (!res || K.days() !== days) { return; }
        all = rowsOf(res); loadedFor = days; span = { first: res.first, last: res.last };
        if (!$("tab-analysis").hidden) { draw(); }
      })
      .catch(() => { /* the next change tries again */ });
  }
  window.AdminAnalysis = {
    show: show,
    refresh: refresh,
    stale: () => { loadedFor = null; },
    // From the visitor log: start from one group.
    focus: (dim, value) => { if (DIMS[dim]) { filters = [{ dim: dim, value: value }]; } },
    toHash: toHash,
    fromHash: fromHash,
    DIMS: DIMS,
  };
})();
