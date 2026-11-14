/* The admin page's Feedback part: error reports and feedback sent from the
 * app (Send feedback…, and the offer after an error or a crash), laid out
 * like the statistics - the numbers for the period, a row a day, the
 * problems grouped by what went wrong, what people wrote. Each report can
 * be read in full, marked seen or fixed, and copied as a block to paste
 * into a conversation with whoever fixes it.
 * /admin/feedback, /admin/feedback-item, /admin/feedback-mark in donate.py
 * (feedback.py). The app scrubs a report before the person sends it; the
 * service scrubs it again. An external file for the CSP.
 */
(function () {
  "use strict";
  const K = window.AdminKit;
  if (!K) { return; }
  const { $, mk, num } = K;
  const when = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const KIND = { error: "Error", crash: "Crash", feedback: "Feedback" };
  // What the person picked in the app: what it is about, where, how often.
  const TOPIC = { bug: "Not working", idea: "Idea", like: "Likes", question: "Question" };
  const AREA = { playback: "Playback", guide: "Guide", recording: "Recording", lists: "Channels and lists",
                 settings: "Settings", cast: "Casting", updates: "Updates", other: "Something else",
                 website: "The website" };
  const OFTEN = { always: "every time", sometimes: "sometimes", once: "once" };
  let topic = "";
  // Answers the app shows in its own language (feedback.py REPLY_CODES);
  // words of one's own go with any of them, or alone as a note.
  const REPLY = [["thanks", "Thanks, noted"], ["known", "Known problem, being worked on"],
                 ["fixed", "Fixed in…"], ["needinfo", "Need more details"], ["note", "Own words only"]];
  const SAID = { thanks: "Thanks - read and noted.", known: "A known problem, being worked on.",
                 fixed: "Fixed in {v}.", needinfo: "More details would help.", note: "" };
  let res = null, loading = false, open = null;
  // How the problems are ordered and which show: most people hit first,
  // fixed ones out of the way, one version at a time if wanted.
  const SORTS = [["count", "Most reported"], ["recent", "Most recent"], ["versions", "Most versions"],
                 ["new", "Newest problem"]];
  let sort = "count", only = "open", version = "";

  function get(url) {
    return fetch(url, { credentials: "same-origin", cache: "no-store" }).then((r) => {
      if (r.status === 401) { location.replace("/admin/login?next=" + encodeURIComponent("/admin/#feedback")); }
      if (!r.ok) { throw new Error("HTTP " + r.status); }
      return r.json();
    });
  }
  function post(url, body) {
    return fetch(url, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(body) }).then((r) => r.ok);
  }

  function show() {
    if (loading) { return; }
    loading = true;
    $("fb-status").textContent = res ? "" : "Loading…";
    get("/admin/feedback?days=" + K.days()).then((r) => { res = r; draw(); })
      .catch((e) => { $("fb-status").textContent = "Could not load the reports: " + e.message; })
      .finally(() => { loading = false; });
    K.writeHash();
  }

  function big(label, value, note, cls) {
    const b = mk("div", "big" + (cls ? " " + cls : ""));
    const row = mk("div", "row");
    row.append(mk("span", "value", value));
    b.append(mk("span", "label", label), row, mk("span", "note", note));
    return b;
  }

  function draw() {
    $("fb-status").textContent = "";
    const k = res.kinds || {};
    const head = mk("div", "sum-head");
    const line = mk("p", "sentence");
    const strong = (t) => mk("strong", "", t);
    line.append(strong(num.format(res.total)), document.createTextNode(res.total === 1 ? " report, " : " reports, "),
                strong(num.format(res.open_groups)), document.createTextNode(res.open_groups === 1 ? " problem not yet fixed." : " problems not yet fixed."));
    head.append(mk("div", "when", K.spanText().replace(/^./, (c) => c.toUpperCase())), line,
                mk("div", "today", num.format(res.new) + " not read yet"));
    const bigs = mk("div", "bigs");
    bigs.append(big("Errors", num.format(k.error || 0), "caught by the app"),
                big("Crashes", num.format(k.crash || 0), "the app went down", "crash"),
                big("Feedback", num.format(k.feedback || 0), "in people's own words", "fb"));
    $("fb-summary").replaceChildren(head, bigs);
    chart();
    $("fb-versions").replaceChildren(K.listCard("By version", res.by_version || [], (v) => v),
                                     K.listCard("By system", res.by_os || [], (v) => v));
    problems();
    words();
  }

  // A bar a day, errors and crashes on feedback.
  function chart() {
    const days = res.per_day || [];
    const box = $("fb-chart");
    if (!days.length) { box.replaceChildren(mk("p", "empty", "No reports in this period.")); return; }
    const W = 760, H = 140, pad = 18, n = days.length, bw = Math.max(3, Math.min(26, (W - 2 * pad) / n - 3));
    const top = Math.max(1, ...days.map((d) => d[1] + d[2] + d[3]));
    const svg = K.svg("svg", { viewBox: "0 0 " + W + " " + (H + 22), class: "fb-bars", role: "img",
                               "aria-label": "Reports per day" });
    days.forEach((d, i) => {
      const x = pad + i * ((W - 2 * pad) / n);
      let y = H;
      [[d[3], "fb"], [d[1], "err"], [d[2], "crash"]].forEach(([v, cls]) => {
        if (!v) { return; }
        const h = Math.max(2, v / top * (H - 10));
        y -= h;
        const r = K.svg("rect", { x: x, y: y, width: bw, height: h, rx: 2, class: cls });
        r.addEventListener("mouseenter", (e) => K.showTip(e, d[0] + ": " + d[1] + " errors, " + d[2] + " crashes, " +
                                                            d[3] + " feedback"));
        r.addEventListener("mouseleave", K.hideTip);
        svg.append(r);
      });
    });
    const first = K.svg("text", { x: pad, y: H + 16, class: "axis" }); first.textContent = days[0][0];
    const last = K.svg("text", { x: W - pad, y: H + 16, class: "axis", "text-anchor": "end" }); last.textContent = days[n - 1][0];
    svg.append(first, last);
    const legend = mk("div", "legend");
    [["err", "Errors"], ["crash", "Crashes"], ["fb", "Feedback"]].forEach(([c, t]) => {
      const s = mk("span", "key " + c); s.append(mk("i"), document.createTextNode(t)); legend.append(s);
    });
    box.replaceChildren(legend, svg);
  }

  function stateChip(state) {
    return mk("span", "chip st-" + state, state === "fixed" ? "Fixed" : state === "seen" ? "Seen" : "New");
  }

  function controls() {
    const bar = mk("div", "fb-controls");
    const chips = mk("div", "fb-chips");
    SORTS.forEach(([key, label]) => {
      const b = mk("button", "chip-f" + (sort === key ? " on" : ""), label);
      b.type = "button";
      b.setAttribute("aria-pressed", sort === key ? "true" : "false");
      b.addEventListener("click", () => { sort = key; problems(); });
      chips.append(b);
    });
    const which = mk("select", "fb-select");
    [["open", "Not fixed"], ["all", "All"], ["fixed", "Fixed"]].forEach(([v, t]) => {
      const o = mk("option", "", t); o.value = v; o.selected = only === v; which.append(o);
    });
    which.addEventListener("change", () => { only = which.value; problems(); });
    const ver = mk("select", "fb-select");
    const all = mk("option", "", "Every version"); all.value = ""; ver.append(all);
    (res.by_version || []).forEach(([v]) => { const o = mk("option", "", v); o.value = v; o.selected = version === v; ver.append(o); });
    ver.addEventListener("change", () => { version = ver.value; problems(); });
    bar.append(chips, which, ver);
    return bar;
  }

  // The problems: one row per kind of error (the same error from many
  // people is one row), ordered as chosen - most reported first to begin
  // with, so what hits the most people is at the top.
  function problems() {
    const box = $("fb-problems");
    let groups = (res.groups || []).slice();
    if (only === "open") { groups = groups.filter((g) => g.state !== "fixed"); }
    if (only === "fixed") { groups = groups.filter((g) => g.state === "fixed"); }
    if (version) { groups = groups.filter((g) => g.versions.indexOf(version) >= 0); }
    const by = {
      count: (a, b) => b.count - a.count || b.last - a.last,
      recent: (a, b) => b.last - a.last,
      versions: (a, b) => b.versions.length - a.versions.length || b.count - a.count,
      new: (a, b) => b.first - a.first,
    }[sort];
    groups.sort(by);
    if (!groups.length) {
      box.replaceChildren(controls(), mk("p", "empty", (res.groups || []).length ? "Nothing matches - try All."
        : "No errors or crashes reported in this period."));
      return;
    }
    const ul = mk("ul", "fb-list");
    groups.forEach((g) => {
      const li = mk("li", "fb-row st-" + g.state);
      const btn = mk("button", "fb-head");
      btn.type = "button";
      btn.append(mk("span", "chip kind-" + g.kind, KIND[g.kind]), mk("span", "sig", g.sig || "(no message)"),
                 mk("span", "count", "×" + num.format(g.count)), stateChip(g.state));
      if (g.replies) { btn.append(mk("span", "chip answered", "Answered")); }
      const meta = mk("div", "fb-meta", "Versions " + g.versions.join(", ") + " · " + g.systems.join(", ") +
                      " · last " + when.format(new Date(g.last * 1000)));
      const body = mk("div", "fb-body");
      body.hidden = true;
      btn.addEventListener("click", () => toggle(body, g.latest, g.key, g));
      li.append(btn, meta, body);
      ul.append(li);
    });
    box.replaceChildren(controls(), ul);
  }

  // What people wrote, newest first - all of it, or one topic.
  function topicChips(all) {
    const t = res.topics || {};
    const bar = mk("div", "fb-controls");
    const chips = mk("div", "fb-chips");
    [["", "All", all.length]].concat(Object.keys(TOPIC).map((k) => [k, TOPIC[k], t[k] || 0]))
      .forEach(([key, label, n]) => {
        if (key && !n) { return; }
        const b = mk("button", "chip-f" + (topic === key ? " on" : ""), label + " " + num.format(n));
        b.type = "button";
        b.setAttribute("aria-pressed", topic === key ? "true" : "false");
        b.addEventListener("click", () => { topic = key; words(); });
        chips.append(b);
      });
    bar.append(chips);
    return bar;
  }

  function words() {
    const box = $("fb-words");
    const all = (res.items || []).filter((i) => i.kind === "feedback");
    if (!all.length) { box.replaceChildren(mk("p", "empty", "No feedback in this period.")); return; }
    // A supporter's feedback first while it is new; the rest newest first.
    const first = (i) => (i.sup && i.state === "new" ? 1 : 0);
    const items = (topic ? all.filter((i) => i.topic === topic) : all.slice())
      .sort((a, b) => first(b) - first(a));
    const ul = mk("ul", "fb-list");
    items.forEach((it) => {
      const li = mk("li", "fb-row st-" + it.state);
      const btn = mk("button", "fb-head");
      btn.type = "button";
      if (it.topic) { btn.append(mk("span", "chip t-" + it.topic, TOPIC[it.topic] || it.topic)); }
      if (it.from === "site") { btn.append(mk("span", "chip from-site", "Website")); }
      if (it.sup) {
        const sup = mk("span", "chip sup", "\u2665 Supporter");
        sup.title = "Sent from an app with a supporter key - read first";
        btn.append(sup);
      }
      btn.append(mk("span", "sig words", it.preview || "(empty)"), stateChip(it.state));
      if (it.replies) { btn.append(mk("span", "chip answered", "Answered")); }
      const where = [it.area && AREA[it.area], it.often && OFTEN[it.often]].filter(Boolean).join(", ");
      const origin = it.from === "site" ? "from the website" : it.version + " · " + it.os;
      const meta = mk("div", "fb-meta", when.format(new Date(it.t * 1000)) + " · " + origin +
                      (where ? " · " + where : ""));
      const body = mk("div", "fb-body");
      body.hidden = true;
      btn.addEventListener("click", () => toggle(body, it.id, it.id));
      li.append(btn, meta, body);
      ul.append(li);
    });
    box.replaceChildren(topicChips(all), items.length ? ul : mk("p", "empty", "Nothing on this topic in this period."));
  }

  // A report in full: its text, a copy to paste where it gets fixed, and
  // the marks.
  // Answering: one report, or a group (everyone who reported that problem,
  // and whoever reports it later). Only reports whose sender asked for
  // answers in the app can be told; the rest are counted.
  function answerBox(r, key, group) {
    const box = mk("div", "fb-answer");
    const can = group ? group.answerable : (r.answerable ? 1 : 0);
    const head = mk("p", "fb-answer-head", group
      ? (can ? "Answer everyone with this problem - " + num.format(can) + " of " + num.format(group.count) +
               " asked for answers in the app, and anyone who reports it later is told too."
             : "Nobody who reported this asked for answers in the app - an answer still reaches whoever reports it later.")
      : (can ? "Answer - shown in their app, in their language (your own words as written)."
             : "This person did not ask for answers in the app."));
    box.append(head);
    (r.replies || []).forEach((a) => {
      const line = mk("p", "fb-said", when.format(new Date(a.t * 1000)) + " · " +
        (SAID[a.code] || "").replace("{v}", a.version || "the next version") + (a.text ? " " + a.text : ""));
      box.append(line);
    });
    if (!can && !group) { return box; }
    let code = "thanks";
    const chips = mk("div", "fb-chips");
    const version = mk("input", "fb-version");
    Object.assign(version, { placeholder: "1.5.1 (empty: the next version)", autocomplete: "off", spellcheck: false });
    version.hidden = true;
    REPLY.forEach(([k, label]) => {
      const b = mk("button", "chip-f" + (k === code ? " on" : ""), label);
      b.type = "button";
      b.addEventListener("click", () => {
        code = k;
        [].forEach.call(chips.children, (c) => c.classList.toggle("on", c === b));
        version.hidden = code !== "fixed";
      });
      chips.append(b);
    });
    const words = mk("textarea", "fb-words-in");
    Object.assign(words, { rows: 3, maxLength: 1500, placeholder: "Words of your own (optional) - shown as written" });
    const send = mk("button", "btn", group ? "Send to all of them" : "Send answer");
    send.type = "button";
    const said = mk("span", "muted");
    send.addEventListener("click", () => {
      if (code === "note" && !words.value.trim()) { said.textContent = "Write something first."; return; }
      send.disabled = true;
      post("/admin/feedback-reply", { target: group ? "group" : "report", key: key, code: code,
                                      text: words.value, version: version.value.trim() })
        .then((ok) => { if (ok) { res = null; show(); } else { said.textContent = "Could not save it."; send.disabled = false; } });
    });
    const row = mk("div", "fb-actions");
    row.append(send, said);
    box.append(chips, version, words, row);
    return box;
  }

  function toggle(body, id, key, group) {
    if (!body.hidden) { body.hidden = true; open = null; return; }
    if (open && open !== body) { open.hidden = true; }
    open = body;
    body.hidden = false;
    body.replaceChildren(mk("p", "muted", "Loading…"));
    get("/admin/feedback-item?id=" + encodeURIComponent(id)).then((r) => {
      const pre = mk("pre", "fb-text", r.text);
      const bar = mk("div", "fb-actions");
      const copy = mk("button", "btn", "Copy for a bug fix");
      copy.type = "button";
      copy.addEventListener("click", () => {
        const block = "dopeIPTV " + KIND[r.kind].toLowerCase() + " report " + r.id + " (version " + r.version +
          ", " + r.os + ", " + new Date(r.t * 1000).toISOString().slice(0, 16).replace("T", " ") + ")\n```\n" + r.text + "\n```\n";
        navigator.clipboard.writeText(block).then(() => { copy.textContent = "Copied ✓"; },
                                                  () => { copy.textContent = "Select the text and copy it"; });
      });
      bar.append(copy);
      [["seen", "Mark seen"], ["fixed", "Mark fixed"], ["new", "Mark new"]].forEach(([state, label]) => {
        const b = mk("button", "btn quiet", label);
        b.type = "button";
        b.addEventListener("click", () => post("/admin/feedback-mark", { key: key, state: state }).then((ok) => {
          if (ok) { res = null; show(); }
        }));
        bar.append(b);
      });
      body.replaceChildren(bar, answerBox(r, key, group), pre);
      if (r.state === "new" && key === id) { post("/admin/feedback-mark", { key: id, state: "seen" }); }
    }).catch((e) => body.replaceChildren(mk("p", "error", "Could not load it: " + e.message)));
  }

  // The tab says how many are not read yet, from the start.
  function count(n) {
    const c = $("fb-count");
    if (!c) { return; }
    c.hidden = !n;
    c.textContent = n > 99 ? "99+" : String(n || "");
  }
  const draw0 = draw;
  draw = function () { draw0(); count(res.new); };
  setTimeout(() => { get("/admin/feedback?days=0").then((r) => count(r.new)).catch(() => {}); }, 1200);

  window.AdminFeedback = { show: show, badge: () => (res ? res.new : 0) };
}());
