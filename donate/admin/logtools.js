/* The visitor log's own tools: sort, group and filter the visits loaded
 * (a day, a week, a month, a year, or one group from the overview or the
 * Analysis tab), all in the browser.
 *
 *   sort    newest, oldest, most pages, longest, most done on the pages
 *   group   by day, week or month, or by a data point; each group a
 *           folded row with its own sums, opened when wanted
 *   filter  country, system, browser, device, how they came, page language,
 *           and on/off: downloaded, returning, opened donate, ...
 *
 * A long list shows a part at a time. admin.js draws the rows; this only
 * decides which, in what order and under which heading. An external file for
 * the CSP.
 */
(function () {
  "use strict";
  const K = window.AdminKit;
  if (!K) { return; }
  const { mk, num } = K;
  const PAGE = 50;                       // rows drawn at once, then "Show more"
  const FOLD_OVER = 60;                  // more visits than this: groups start folded
  const monthName = new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" });
  const longDay = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });

  function iso(d) {
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  // One visit in the terms the tools use - a visit kept in detail or one
  // kept without (anon: a day, no time of day, no visitor).
  function facts(v) {
    if (v.f) { return v.f; }
    const anon = !!v.anon;
    const day = v.day || iso(new Date(v.start * 1000));
    const pages = anon ? v.pages : v.pages.length;
    const dls = anon ? (v.downloaded ? 1 : 0) : v.downloads.length;
    const evs = anon ? [] : (v.events || []).map((e) => e[1]);
    const os = anon ? (v.os || "Other") : ((v.os || "").split(" (")[0].split(" ")[0] || "Other");
    v.f = {
      day: day, start: anon ? new Date(day + "T12:00:00").getTime() / 1000 : v.start,
      pages: pages, time: v.time || 0, dl: dls, acts: anon ? 0 : (v.acts || []).length,
      donate: anon ? !!(v.donate || v.donated) : evs.length > 0, ret: !!v.returning,
      country: v.country || "unknown", os: os, browser: v.browser || "Other", device: v.device || "Unknown",
      channel: v.channel || (dls && !pages ? "Straight to a download" : "Direct or unknown"),
      site: (anon ? v.site : v.ref) || "", lang: v.lang || "", kind: v.kind || "unknown", id: anon ? "" : v.id,
    };
    return v.f;
  }
  const lang = (k) => { try { return K.langNames ? K.langNames.of(k) : k; } catch (e) { return k; } };
  const KINDS = { person: "Person", unknown: "Not sure", robot: "Likely robot" };

  const SORTS = [
    ["new", "Newest first", (a, b) => b.start - a.start],
    ["old", "Oldest first", (a, b) => a.start - b.start],
    ["pages", "Most pages", (a, b) => b.pages - a.pages || b.time - a.time],
    ["time", "Longest", (a, b) => b.time - a.time || b.pages - a.pages],
    ["acts", "Did the most", (a, b) => (b.acts + b.pages + 3 * b.dl) - (a.acts + a.pages + 3 * a.dl) || b.start - a.start],
  ];
  // [key, label, a visit's value, how the value reads]
  const SELECTS = [
    ["country", "Country", (f) => f.country, (v) => K.flag(v) + K.countryName(v)],
    ["os", "System", (f) => f.os],
    ["browser", "Browser", (f) => f.browser],
    ["device", "Device", (f) => f.device],
    ["channel", "How they came", (f) => f.channel],
    ["site", "Site that linked", (f) => f.site || "(none)"],
    ["lang", "Page language", (f) => f.lang || "?", (v) => (v === "?" ? "Unknown" : lang(v))],
    ["kind", "Judged", (f) => f.kind, (v) => KINDS[v] || v],
  ];
  const FLAGS = [
    ["dl", "Downloaded", (f) => f.dl > 0],
    ["ret", "Returning", (f) => f.ret],
    ["donate", "Opened donate", (f) => f.donate],
    ["more", "More than one page", (f) => f.pages > 1],
    ["long", "A minute or more", (f) => f.time >= 60],
    ["acts", "Used the page", (f) => f.acts > 0],
    ["first", "First visit", (f) => !f.ret],
  ];
  const weekStart = (day) => { const d = new Date(day + "T12:00:00"); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return iso(d); };
  const GROUPS = [
    ["", "Nothing"],
    ["day", "Day", (f) => f.day, (v) => longDay.format(new Date(v + "T12:00:00")), "time"],
    ["week", "Week", (f) => weekStart(f.day), (v) => {
      const s = new Date(v + "T12:00:00"), e = new Date(v + "T12:00:00");
      e.setDate(e.getDate() + 6);
      return "Week of " + K.dayFmt.format(s) + " – " + K.dayFmt.format(e);
    }, "time"],
    ["month", "Month", (f) => f.day.slice(0, 7), (v) => monthName.format(new Date(v + "-15T12:00:00")), "time"],
    ["weekday", "Weekday", (f) => (new Date(f.day + "T12:00:00").getDay() + 6) % 7,
     (v) => ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][v], "num"],
  ].concat(SELECTS.map((s) => [s[0], s[1], s[2], s[3]]));

  // -- state: kept while days and spans change; the sort and grouping also
  // across reloads (this browser), the filters only in the address (#...).
  const st = { sort: "new", group: null, sel: {}, flags: {}, open: false, shown: PAGE, folded: {} };
  try {
    const saved = JSON.parse(localStorage.getItem("admin-logtools") || "{}");
    if (SORTS.some((s) => s[0] === saved.sort)) { st.sort = saved.sort; }
    if (saved.group === null || GROUPS.some((g) => g[0] === saved.group)) { st.group = saved.group === undefined ? null : saved.group; }
  } catch (e) { /* defaults */ }
  function remember() {
    try { localStorage.setItem("admin-logtools", JSON.stringify({ sort: st.sort, group: st.group })); } catch (e) { /* this time */ }
  }
  // A year shows its months, a month or week its days, unless chosen.
  function groupFor(span) {
    if (st.group !== null) { return st.group; }
    return span === "year" ? "month" : span === "month" || span === "week" ? "day" : "";
  }
  function active() { return Object.keys(st.sel).length + Object.keys(st.flags).length; }
  function keep(f) {
    for (const k in st.sel) {
      const s = SELECTS.find((x) => x[0] === k);
      if (s && String(s[2](f)) !== st.sel[k]) { return false; }
    }
    for (const k in st.flags) {
      const fl = FLAGS.find((x) => x[0] === k);
      if (fl && !!fl[2](f) !== st.flags[k]) { return false; }
    }
    return true;
  }

  // -- the bar: sort, group, and the filters behind a button ----------------------------
  function selectEl(label, options, value, onChange, cls) {
    const wrap = mk("label", "lt-ctl" + (cls ? " " + cls : ""));
    wrap.append(mk("span", "", label));
    const s = mk("select");
    options.forEach(([v, t]) => { const o = mk("option", "", t); o.value = v; s.append(o); });
    s.value = value;
    s.addEventListener("change", () => onChange(s.value));
    wrap.append(s);
    return wrap;
  }
  function bar(box, list, span, redraw) {
    const fs = list.map(facts);
    const kept = fs.filter(keep).length;
    const top = mk("div", "lt-top");
    top.append(
      selectEl("Sort", SORTS.map((s) => [s[0], s[1]]), st.sort, (v) => { st.sort = v; st.shown = PAGE; remember(); redraw(); }),
      selectEl("Group by", GROUPS.map((g) => [g[0], g[1]]), groupFor(span), (v) => {
        st.group = v; st.folded = {}; st.shown = PAGE; remember(); redraw();
      }));
    const tog = mk("button", "lt-toggle");
    tog.type = "button";
    tog.setAttribute("aria-expanded", st.open ? "true" : "false");
    tog.textContent = "Filter" + (active() ? " · " + active() : "");
    tog.classList.toggle("on", active() > 0);
    tog.addEventListener("click", () => { st.open = !st.open; redraw(); });
    top.append(tog);
    if (active()) {
      top.append(mk("span", "muted lt-count", num.format(kept) + " of " + num.format(list.length) + " shown"));
      const clear = mk("button", "link", "Clear filters");
      clear.type = "button";
      clear.addEventListener("click", () => { st.sel = {}; st.flags = {}; st.shown = PAGE; redraw(); });
      top.append(clear);
    }
    const parts = [top];
    if (st.open) {
      const panel = mk("div", "lt-panel");
      const sels = mk("div", "lt-sels");
      SELECTS.forEach(([k, label, get, name]) => {
        const counts = new Map();
        fs.forEach((f) => { const v = String(get(f)); counts.set(v, (counts.get(v) || 0) + 1); });
        if (st.sel[k] !== undefined && !counts.has(st.sel[k])) { counts.set(st.sel[k], 0); }
        if (counts.size < 2 && st.sel[k] === undefined) { return; }       // nothing to choose
        const opts = [["", "All"]].concat(Array.from(counts.entries()).sort((a, b) => b[1] - a[1])
          .map(([v, n]) => [v, (name ? name(v) : v) + " (" + num.format(n) + ")"]));
        sels.append(selectEl(label, opts, st.sel[k] || "", (v) => {
          if (v) { st.sel[k] = v; } else { delete st.sel[k]; }
          st.shown = PAGE; redraw();
        }, st.sel[k] !== undefined ? "on" : ""));
      });
      const chips = mk("div", "lt-chips");
      FLAGS.forEach(([k, label, test]) => {
        const n = fs.filter(test).length;
        const c = mk("button", "lt-chip");
        c.type = "button";
        const state = st.flags[k];
        c.setAttribute("aria-pressed", state === true ? "true" : "false");
        c.classList.toggle("not", state === false);
        c.textContent = (state === false ? "Not: " : "") + label + " · " + num.format(state === false ? fs.length - n : n);
        c.title = "Click: only these · again: all but these · again: either";
        c.addEventListener("click", () => {
          if (state === undefined) { st.flags[k] = true; } else if (state) { st.flags[k] = false; } else { delete st.flags[k]; }
          st.shown = PAGE; redraw();
        });
        chips.append(c);
      });
      panel.append(sels, chips);
      parts.push(panel);
    }
    box.replaceChildren(...parts);
    box.hidden = !list.length;
  }

  // -- the rows: filtered, sorted, grouped, a part at a time ------------------------------
  function sums(fs) {
    const people = new Set(fs.filter((f) => f.id).map((f) => f.id)).size;
    const dl = fs.filter((f) => f.dl).length;
    const timed = fs.filter((f) => f.time > 0);
    const t = timed.length ? timed.reduce((s, f) => s + f.time, 0) / timed.length : 0;
    return [num.format(fs.length) + (fs.length === 1 ? " visit" : " visits"),
            people ? num.format(people) + (people === 1 ? " visitor" : " visitors") : "",
            dl ? num.format(dl) + " downloaded" : "",
            t ? "about " + K.duration(t) + " each" : ""].filter(Boolean).join(" · ");
  }
  // Rows for admin.js to draw: visits, a group's head ({head}) or the
  // "Show more" ({more}). row(v) draws one visit.
  function rows(list, span, row, redraw) {
    const sort = SORTS.find((s) => s[0] === st.sort) || SORTS[0];
    const shown = list.filter((v) => keep(facts(v))).sort((a, b) => sort[2](facts(a), facts(b)));
    if (!shown.length) {
      const p = mk("p", "empty", "No visits match these filters. ");
      const clear = mk("button", "link", "Clear filters");
      clear.type = "button";
      clear.addEventListener("click", () => { st.sel = {}; st.flags = {}; redraw(); });
      p.append(clear);
      return [p];
    }
    const more = (n, left) => {
      const b = mk("button", "lt-more", "Show " + num.format(Math.min(PAGE, left)) + " more · " + num.format(left) + " left");
      b.type = "button";
      b.addEventListener("click", () => { st.shown = n + PAGE; redraw(); });
      return b;
    };
    const gk = groupFor(span), g = GROUPS.find((x) => x[0] === gk);
    if (!g || !g[2]) {
      const out = shown.slice(0, st.shown).map(row);
      if (shown.length > st.shown) { out.push(more(st.shown, shown.length - st.shown)); }
      return out;
    }
    const groups = new Map();
    shown.forEach((v) => { const k = g[2](facts(v)); if (!groups.has(k)) { groups.set(k, []); } groups.get(k).push(v); });
    let order = Array.from(groups.entries());
    if (g[4] === "time") {                       // in the sort's direction of time
      const asc = st.sort === "old";
      order.sort((a, b) => (a[0] < b[0] ? (asc ? -1 : 1) : a[0] > b[0] ? (asc ? 1 : -1) : 0));
    } else if (g[4] === "num") {
      order.sort((a, b) => a[0] - b[0]);
    } else {
      order.sort((a, b) => b[1].length - a[1].length);
    }
    const foldAll = shown.length > FOLD_OVER && order.length > 1;
    const out = [];
    const tools = mk("div", "lt-fold");
    [["Open all", false], ["Fold all", true]].forEach(([t, fold]) => {
      const b = mk("button", "link", t);
      b.type = "button";
      b.addEventListener("click", () => { order.forEach(([k]) => { st.folded[gk + ":" + k] = fold; }); redraw(); });
      tools.append(b);
    });
    out.push(tools);
    order.forEach(([k, vs]) => {
      const key = gk + ":" + k;
      const folded = st.folded[key] !== undefined ? st.folded[key] : foldAll;
      const head = mk("button", "lt-group" + (folded ? "" : " open"));
      head.type = "button";
      head.setAttribute("aria-expanded", folded ? "false" : "true");
      head.append(mk("span", "g-name", g[3] ? g[3](k) : String(k)), mk("span", "g-sums", sums(vs.map(facts))));
      head.addEventListener("click", () => { st.folded[key] = !folded; redraw(); });
      out.push(head);
      if (!folded) {
        const lim = st.folded[key + ":n"] || PAGE;
        out.push(...vs.slice(0, lim).map(row));
        if (vs.length > lim) {
          const b = mk("button", "lt-more", "Show " + num.format(Math.min(PAGE, vs.length - lim)) + " more · " +
                       num.format(vs.length - lim) + " left");
          b.type = "button";
          b.addEventListener("click", () => { st.folded[key + ":n"] = lim + PAGE; redraw(); });
          out.push(b);
        }
      }
    });
    return out;
  }

  // The filters in the address, so a reload keeps them: "country=SE,dl=1".
  function toHash() {
    const parts = [];
    Object.keys(st.sel).forEach((k) => parts.push(k + "=" + encodeURIComponent(st.sel[k])));
    Object.keys(st.flags).forEach((k) => parts.push(k + "=" + (st.flags[k] ? "1" : "0")));
    return parts.join(",");
  }
  function fromHash(s) {
    st.sel = {}; st.flags = {};
    (s || "").split(",").forEach((p) => {
      const i = p.indexOf("=");
      if (i < 1) { return; }
      const k = p.slice(0, i), v = decodeURIComponent(p.slice(i + 1));
      if (SELECTS.some((x) => x[0] === k)) { st.sel[k] = v; }
      if (FLAGS.some((x) => x[0] === k) && (v === "1" || v === "0")) { st.flags[k] = v === "1"; }
    });
    if (active()) { st.open = true; }
  }

  window.LogTools = {
    bar: bar,
    rows: rows,
    reset: () => { st.shown = PAGE; st.folded = {}; },
    toHash: toHash,
    fromHash: fromHash,
  };
})();
