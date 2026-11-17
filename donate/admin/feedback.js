/* The admin page's Feedback part: error reports and feedback sent from the
 * app (Send feedback…, and the offer after an error or a crash), laid out
 * like the statistics - the numbers for the period, a row a day, the
 * problems grouped by what went wrong, what people wrote. Each report can
 * be read in full, marked seen or fixed, archived (out of the lists and the
 * unread count, under Archived) or deleted for good, and copied - as plain
 * text from its row (Copy; the service writes the text, feedback.py plain(),
 * the conversation after it included), the whole list as shown (Copy all
 * shown), or as a block to paste into a conversation with whoever fixes it.
 * Which build sent a report: "source" is marked, and the lists filter by
 * build.
 *
 * A conversation, still anonymous: an answer may let the person reply once
 * ("Let them reply", on by default for "Need more details"); their reply
 * shows in the report's thread - report, answer, reply, answer… - and
 * makes the report unread again.
 *
 * New reports and replies come in by themselves: while the page is in
 * front the service is asked every EVERY ms with the revision the page has
 * (nothing read when nothing changed), and what is new is drawn in place -
 * the report open stays open with what is typed in it, the filters and the
 * scroll stay, the tab's count follows.
 *
 * /admin/feedback, /admin/feedback-item, /admin/feedback-copy,
 * /admin/feedback-mark, /admin/feedback-reply, /admin/feedback-archive,
 * /admin/feedback-delete in donate.py (feedback.py). The app scrubs a
 * report before the person sends it; the service scrubs it again. An
 * external file for the CSP.
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
  // Which build sent it (X-dopeIPTV-Build; reports from before it are unknown).
  const BUILD = { official: "Official", source: "From source", unknown: "Unknown" };
  // The feedback list shows the archive instead of the rest while on.
  let topic = "", build = "", archived = false;
  // Answers the app shows in its own language (feedback.py REPLY_CODES);
  // words of one's own go with any of them, or alone as a note.
  const REPLY = [["thanks", "Thanks, noted"], ["known", "Known problem, being worked on"],
                 ["fixed", "Fixed in…"], ["needinfo", "Need more details"], ["note", "Own words only"]];
  const SAID = { thanks: "Thanks - read and noted.", known: "A known problem, being worked on.",
                 fixed: "Fixed in {v}.", needinfo: "More details would help.", note: "" };
  let res = null, loading = false, again = false, refill = false;
  // The report or problem open below its row ("i:" + id, "g:" + key): kept
  // open, with what is typed in it, when the lists are drawn again.
  let open = null, openKey = "";
  // Rows that came or changed since the last look: lit up for a moment.
  let fresh = new Set();
  // How the problems are ordered and which show: most people hit first,
  // fixed ones out of the way, one version at a time if wanted.
  const SORTS = [["count", "Most reported"], ["recent", "Most recent"], ["versions", "Most versions"],
                 ["new", "Newest problem"]];
  let sort = "count", only = "open", version = "";

  const LOGIN = "/admin/login?next=" + encodeURIComponent("/admin/#feedback");
  function get(url) {
    return fetch(url, { credentials: "same-origin", cache: "no-store" }).then((r) => {
      if (r.status === 401) { location.replace(LOGIN); }
      if (!r.ok) { throw new Error("HTTP " + r.status); }
      return r.json();
    });
  }
  function post(url, body) {
    return fetch(url, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(body) }).then((r) => r.ok);
  }
  const plural = (n, one, more) => num.format(n) + " " + (n === 1 ? one : more);
  const saidText = (a) => (SAID[a.code] || "").replace("{v}", a.version || "the next version") + (a.text ? " " + a.text : "");

  // -- copying, phones included -----------------------------------------------
  // The clipboard takes text only during a tap: the Clipboard API where the
  // page may use it, else a selected textarea and execCommand. Text still to
  // be fetched goes in as a promise (ClipboardItem - Safari keeps the tap for
  // it); where that is not taken, the button asks for a second tap, with the
  // text at hand by then.
  function copyByHand(text) {
    const was = document.activeElement;
    const ta = mk("textarea", "copy-buffer");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.setAttribute("aria-hidden", "true");
    document.body.append(ta);
    ta.focus();
    ta.select();
    ta.setSelectionRange(0, text.length);        // iOS selects nothing without it
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    ta.remove();
    if (was && was.focus) { was.focus(); }
    return ok;
  }
  function copyNow(text) {
    if (navigator.clipboard && navigator.clipboard.writeText && window.isSecureContext) {
      return navigator.clipboard.writeText(text).then(() => true, () => copyByHand(text));
    }
    return Promise.resolve(copyByHand(text));
  }
  function copyLater(promise) {
    let tried = Promise.resolve(false);
    if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write && window.isSecureContext) {
      try {
        const item = new ClipboardItem({ "text/plain": promise.then((t) => new Blob([t], { type: "text/plain" })) });
        tried = navigator.clipboard.write([item]).then(() => true, () => false);
      } catch (e) { tried = Promise.resolve(false); }
    }
    return tried.then((ok) => ok || promise.then(copyNow));
  }
  // A button that copies what *text()* gives - a string, or a promise of
  // one - and says so for a moment.
  function copyButton(label, cls, text) {
    const b = mk("button", cls, label);
    b.type = "button";
    let ready = null, timer = 0;
    const say = (words, cls2, stay) => {
      clearTimeout(timer);
      b.textContent = words;
      b.classList.remove("done", "again");
      if (cls2) { b.classList.add(cls2); }
      if (!stay) { timer = setTimeout(() => { b.textContent = label; b.classList.remove("done", "again"); }, 2200); }
    };
    const said = (ok) => say(ok ? "Copied \u2713" : "Could not copy - select the text and copy it", ok ? "done" : "");
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      if (ready === null) {
        const got = text();
        if (typeof got === "string") { ready = got; }
        else {
          const p = Promise.resolve(got);
          p.then((t) => { ready = t; }, () => {});
          copyLater(p).then((ok) => (ok ? said(true) : say("Tap again to copy", "again", true)),
                            (err) => say("Could not load it" + (err && err.message ? ": " + err.message : "")));
          return;
        }
      }
      copyNow(ready).then(said);
    });
    return b;
  }
  // The plain text of reports (feedback.py plain(), with their conversation),
  // fetched once each - again once the report has something new.
  const copies = new Map();
  function fetchCopies(ids) {
    const want = ids.filter((id) => id && !copies.has(id));
    if (!want.length) { return Promise.resolve(copies); }
    return get("/admin/feedback-copy?ids=" + want.map(encodeURIComponent).join(",")).then((r) => {
      (r.items || []).forEach((i) => copies.set(i.id, i.copy));
      return copies;
    });
  }
  const utc = (t) => new Date(t * 1000).toISOString().slice(0, 16).replace("T", " ") + " UTC";
  const buildsText = (b) => Object.keys(b || {}).filter((k) => b[k]).map((k) => k + " " + b[k]).join(", ") || "unknown";
  // A problem's line above its latest report.
  function groupHead(g) {
    return "Problem: " + (g.sig || "(no message)") + "\n" +
      "Reported " + g.count + (g.count === 1 ? " time" : " times") + " in " + K.spanText() + " - versions " +
      g.versions.join(", ") + " - " + g.systems.join(", ") + " - builds " + buildsText(g.builds) +
      " - first " + utc(g.first) + ", last " + utc(g.last) + " - " + g.state + "\n\nThe latest report:\n";
  }
  function groupCopy(g) {
    return () => fetchCopies([g.latest]).then((c) => groupHead(g) + (c.get(g.latest) || "(not found)\n"));
  }
  function itemCopy(it) {
    return () => copies.get(it.id) || fetchCopies([it.id]).then((c) => c.get(it.id) || "(not found)\n");
  }
  const RULE = "\n" + "-".repeat(60) + "\n\n";
  // Everything the list shows, in its order, as one text.
  function copyAll(what, shown, ids, one) {
    return copyButton("Copy all shown", "btn btn-small quiet fb-copy-all", () => fetchCopies(ids).then((c) =>
      "dopeIPTV " + what + " - " + shown.length + " shown, " + K.spanText() + (build ? ", build " + BUILD[build].toLowerCase() : "") +
      RULE + shown.map((x) => one(x, c)).join(RULE)));
  }
  function buildSelect() {
    const sel = mk("select", "fb-select");
    sel.setAttribute("aria-label", "Build");
    [["", "Every build"], ["official", "Official"], ["source", "From source"], ["unknown", "Unknown build"]]
      .forEach(([v, t]) => { const o = mk("option", "", t); o.value = v; o.selected = build === v; sel.append(o); });
    sel.addEventListener("change", () => { build = sel.value; problems(); words(); });
    return sel;
  }
  function sourceChip(n, total) {
    const c = mk("span", "chip b-source", "Source");
    c.title = n === undefined ? "Sent from an app run from source (a checkout)"
      : num.format(n) + " of " + num.format(total) + " from an app run from source (a checkout)";
    return c;
  }
  function metaLine(text, button) {
    const meta = mk("div", "fb-meta");
    meta.append(mk("span", "fb-meta-text", text), button);
    return meta;
  }

  function show() {
    if (loading) { again = true; return; }
    loading = true;
    later();
    $("fb-status").textContent = res ? "" : "Loading…";
    get("/admin/feedback?days=" + K.days()).then((r) => { res = r; waiting = null; draw(); })
      .catch((e) => { $("fb-status").textContent = "Could not load the reports: " + e.message; })
      .finally(() => { loading = false; if (again) { again = false; show(); } });
    K.writeHash();
  }
  // After a change made here (a mark, an answer, the archive): the lists
  // again, and the report open below its row drawn again with them.
  function reload() { refill = true; show(); }

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
                                     K.listCard("By system", res.by_os || [], (v) => v),
                                     K.listCard("By build", res.by_build || [], (v) => BUILD[v] || v));
    problems();
    words();
    if (refill) {
      refill = false;
      const a = open && open.fbArgs;
      if (a) {
        const g = a.group ? (res.groups || []).find((x) => x.key === a.key) : null;
        if (!a.group || g) { load(open, a.id, a.key, g, false); }
      }
    }
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
  function chip(cls, text, title) {
    const c = mk("span", "chip " + cls, text);
    if (title) { c.title = title; }
    return c;
  }

  // A row of a list: its key on it (the scroll is kept by it), lit up when
  // it is new since the last look.
  function row(rk, state) {
    const li = mk("li", "fb-row st-" + state + (fresh.has(rk) ? " fresh" : ""));
    li.dataset.key = rk;
    return li;
  }
  // The body below a row: the one open stays open, with what is typed in
  // it; *list* says it found its row again.
  function bodyFor(rk, list) {
    if (open && openKey === rk) { list.kept = true; return open; }
    const body = mk("div", "fb-body");
    body.hidden = true;
    return body;
  }
  function letGo(prefix, list) {
    if (openKey.charAt(0) === prefix && !list.kept) { open = null; openKey = ""; }
  }

  function controls(shown) {
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
    [["open", "Not fixed"], ["all", "All"], ["fixed", "Fixed"], ["archived", "Archived"]].forEach(([v, t]) => {
      const o = mk("option", "", t); o.value = v; o.selected = only === v; which.append(o);
    });
    which.addEventListener("change", () => { only = which.value; problems(); });
    const ver = mk("select", "fb-select");
    const all = mk("option", "", "Every version"); all.value = ""; ver.append(all);
    (res.by_version || []).forEach(([v]) => { const o = mk("option", "", v); o.value = v; o.selected = version === v; ver.append(o); });
    ver.addEventListener("change", () => { version = ver.value; problems(); });
    bar.append(chips, which, ver, buildSelect());
    if (shown.length) {
      bar.append(copyAll("problems", shown, shown.map((g) => g.latest),
                         (g, c) => groupHead(g) + (c.get(g.latest) || "(not found)\n")));
    }
    return bar;
  }

  // The problems: one row per kind of error (the same error from many
  // people is one row), ordered as chosen - most reported first to begin
  // with, so what hits the most people is at the top. Archived ones only
  // under Archived.
  function problems() {
    const box = $("fb-problems");
    let groups = (res.groups || []).filter((g) => (only === "archived") === !!g.archived);
    if (only === "open") { groups = groups.filter((g) => g.state !== "fixed"); }
    if (only === "fixed") { groups = groups.filter((g) => g.state === "fixed"); }
    if (version) { groups = groups.filter((g) => g.versions.indexOf(version) >= 0); }
    if (build) { groups = groups.filter((g) => (g.builds || {})[build]); }
    const by = {
      count: (a, b) => b.count - a.count || b.last - a.last,
      recent: (a, b) => b.last - a.last,
      versions: (a, b) => b.versions.length - a.versions.length || b.count - a.count,
      new: (a, b) => b.first - a.first,
    }[sort];
    groups.sort(by);
    const list = { kept: false };
    if (!groups.length) {
      letGo("g", list);
      box.replaceChildren(controls([]), mk("p", "empty", only === "archived" ? "Nothing archived in this period."
        : (res.groups || []).length ? "Nothing matches - try All" + (build ? " and Every build." : ".")
        : "No errors or crashes reported in this period."));
      return;
    }
    const ul = mk("ul", "fb-list");
    groups.forEach((g) => {
      const rk = "g:" + g.key;
      const li = row(rk, g.state);
      const btn = mk("button", "fb-head");
      btn.type = "button";
      btn.append(mk("span", "chip kind-" + g.kind, KIND[g.kind]), mk("span", "sig", g.sig || "(no message)"),
                 mk("span", "count", "×" + num.format(g.count)), stateChip(g.state));
      if (g.replies) { btn.append(mk("span", "chip answered", "Answered")); }
      if (g.archived) { btn.append(chip("archived", "Archived")); }
      if ((g.builds || {}).source) { btn.insertBefore(sourceChip(g.builds.source, g.count), btn.children[1]); }
      const meta = metaLine("Versions " + g.versions.join(", ") + " · " + g.systems.join(", ") +
                            " · last " + when.format(new Date(g.last * 1000)),
                            copyButton("Copy", "fb-copy", groupCopy(g)));
      const body = bodyFor(rk, list);
      btn.addEventListener("click", () => toggle(body, rk, g.latest, g.key, g));
      li.append(btn, meta, body);
      ul.append(li);
    });
    letGo("g", list);
    box.replaceChildren(controls(groups), ul);
  }

  // What people wrote, the latest first (a reply to an answer brings its
  // report up) - all of it, one topic, or the archive.
  function topicChips(live, shown) {
    const t = res.topics || {};
    const bar = mk("div", "fb-controls");
    const chips = mk("div", "fb-chips");
    [["", "All", live.length]].concat(Object.keys(TOPIC).map((k) => [k, TOPIC[k], t[k] || 0]))
      .forEach(([key, label, n]) => {
        if (key && !n) { return; }
        const on = !archived && topic === key;
        const b = mk("button", "chip-f" + (on ? " on" : ""), label + " " + num.format(n));
        b.type = "button";
        b.setAttribute("aria-pressed", on ? "true" : "false");
        b.addEventListener("click", () => { topic = key; archived = false; words(); });
        chips.append(b);
      });
    if (res.archived || archived) {
      const b = mk("button", "chip-f" + (archived ? " on" : ""), "Archived " + num.format(res.archived || 0));
      b.type = "button";
      b.title = "What was archived - out of the list and the unread count until it is unarchived or they reply";
      b.setAttribute("aria-pressed", archived ? "true" : "false");
      b.addEventListener("click", () => { archived = !archived; topic = ""; words(); });
      chips.append(b);
    }
    bar.append(chips, buildSelect());
    if (shown.length) { bar.append(copyAll("feedback", shown, shown.map((i) => i.id), (i, c) => c.get(i.id) || "(not found)\n")); }
    return bar;
  }

  function words() {
    const box = $("fb-words");
    const every = (res.items || []).filter((i) => i.kind === "feedback");
    const live = every.filter((i) => !i.archived);
    const list = { kept: false };
    if (!every.length) { letGo("i", list); box.replaceChildren(mk("p", "empty", "No feedback in this period.")); return; }
    // A supporter's feedback first while it is new; the rest the latest first.
    const first = (i) => (i.sup && i.state === "new" ? 1 : 0);
    const items = (archived ? every.filter((i) => i.archived) : live)
      .filter((i) => (!topic || i.topic === topic) && (!build || i.build === build))
      .sort((a, b) => first(b) - first(a));
    const ul = mk("ul", "fb-list");
    items.forEach((it) => {
      const rk = "i:" + it.id;
      const li = row(rk, it.state);
      const btn = mk("button", "fb-head");
      btn.type = "button";
      if (it.topic) { btn.append(mk("span", "chip t-" + it.topic, TOPIC[it.topic] || it.topic)); }
      if (it.from === "site") { btn.append(mk("span", "chip from-site", "Website")); }
      if (it.build === "source") { btn.append(sourceChip()); }
      if (it.sup) {
        const sup = mk("span", "chip sup", "\u2665 Supporter");
        sup.title = "Sent from an app with a supporter key - read first";
        btn.append(sup);
      }
      btn.append(mk("span", "sig words", it.preview || "(empty)"), stateChip(it.state));
      if (it.waiting) { btn.append(chip("reply-waiting", "Replied", "They replied to your answer - your turn")); }
      else if (it.open) { btn.append(chip("can", "Can reply", "Your answer lets them reply - they have not yet")); }
      else if (it.replies) { btn.append(mk("span", "chip answered", "Answered")); }
      if (it.archived) { btn.append(chip("archived", "Archived")); }
      const where = [it.area && AREA[it.area], it.often && OFTEN[it.often]].filter(Boolean).join(", ");
      const origin = it.from === "site" ? "from the website" : it.version + " · " + it.os;
      const talk = it.thread ? " · " + plural(it.thread, "reply", "replies") + ", the last " +
        when.format(new Date(it.last * 1000)) : "";
      const meta = metaLine(when.format(new Date(it.t * 1000)) + " · " + origin + (where ? " · " + where : "") + talk,
                            copyButton("Copy", "fb-copy", itemCopy(it)));
      const body = bodyFor(rk, list);
      btn.addEventListener("click", () => toggle(body, rk, it.id, it.id));
      li.append(btn, meta, body);
      ul.append(li);
    });
    letGo("i", list);
    box.replaceChildren(topicChips(live, items), items.length ? ul
      : mk("p", "empty", archived ? "Nothing archived " + (topic || build ? "like this " : "") + "in this period."
        : build ? "Nothing from this build on this topic in this period." : "Nothing on this topic in this period."));
  }

  // Answering: one report, or a group (everyone who reported that problem,
  // and whoever reports it later). Only reports whose sender asked for
  // answers in the app can be told; the rest are counted. One report's
  // answer may let them reply, once ("Let them reply"; never a group's).
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
    if (group) {
      (r.replies || []).forEach((a) => {
        box.append(mk("p", "fb-said", when.format(new Date(a.t * 1000)) + " · " + saidText(a)));
      });
    }
    if (!can && !group) { return box; }
    // Nothing picked at first: often the answer is words of one's own. A
    // picked one picked again goes back to none; none with words sends them
    // as they are ("note").
    let code = "", touched = false;
    const chips = mk("div", "fb-chips");
    const version = mk("input", "fb-version");
    Object.assign(version, { placeholder: "1.5.1 (empty: the next version)", autocomplete: "off", spellcheck: false });
    version.hidden = true;
    // Let them reply: on for "Need more details", off for the rest, until
    // flipped by hand - then as flipped.
    const letReply = mk("label", "fb-open");
    const tick = mk("input");
    tick.type = "checkbox";
    tick.addEventListener("change", () => { touched = true; });
    letReply.append(tick, mk("span", "", "Let them reply"),
                    mk("span", "muted", "once, in the app - then it waits for your next answer"));
    letReply.hidden = !!group;
    const byCode = () => { if (!touched) { tick.checked = code === "needinfo"; } };
    REPLY.filter(([k]) => k !== "note").forEach(([k, label]) => {
      const b = mk("button", "chip-f", label);
      b.type = "button";
      b.setAttribute("aria-pressed", "false");
      b.addEventListener("click", () => {
        code = code === k ? "" : k;
        [].forEach.call(chips.children, (c) => {
          const on = c === b && code === k;
          c.classList.toggle("on", on);
          c.setAttribute("aria-pressed", on ? "true" : "false");
        });
        version.hidden = code !== "fixed";
        byCode();
      });
      chips.append(b);
    });
    byCode();
    const words = mk("textarea", "fb-words-in");
    Object.assign(words, { rows: 3, maxLength: 1500,
                           placeholder: "Your own words - shown as written (after the answer picked above, if any)" });
    const send = mk("button", "btn", group ? "Send to all of them" : "Send answer");
    send.type = "button";
    const said = mk("span", "muted");
    send.addEventListener("click", () => {
      if (!code && !words.value.trim()) { said.textContent = "Pick an answer or write something first."; return; }
      send.disabled = true;
      const body = { target: group ? "group" : "report", key: key, code: code || "note", text: words.value,
                     version: version.value.trim() };
      if (!group) { body.open = tick.checked; }
      post("/admin/feedback-reply", body).then((ok) => {
        if (!ok) { said.textContent = "Could not save it."; send.disabled = false; return; }
        if (open) { open.fbForm = null; }        // a fresh box for the next answer
        copies.delete(r.id);
        reload();
      });
    });
    const line = mk("div", "fb-actions");
    line.append(send, said);
    box.append(chips, version, letReply, words, line);
    return box;
  }

  // A report's conversation, in order: the report, each answer, each reply
  // to one - with their times; a reply has its own Copy.
  function thread(r) {
    const box = mk("div", "fb-thread");
    const entry = (cls, label, ...extra) => {
      const el = mk("div", "fb-entry " + cls);
      const top = mk("div", "fb-entry-head");
      top.append(mk("span", "fb-entry-who", label), ...extra.filter(Boolean));
      el.append(top);
      box.append(el);
      return el;
    };
    entry("report", (KIND[r.kind] || "Report") + " · " + when.format(new Date(r.t * 1000)))
      .append(mk("pre", "fb-text", r.text));
    (r.conversation || []).forEach((e) => {
      const at = when.format(new Date(e.t * 1000));
      if (e.who === "reply") {
        entry("reply", "They replied · " + at + " · " + e.version + " · " + e.os,
              e.state === "new" ? stateChip("new") : null, copyButton("Copy", "fb-copy", () => e.copy))
          .append(mk("p", "fb-reply-text", e.text));
        return;
      }
      entry("answer", (e.group ? "You answered everyone with this problem · " : "You answered · ") + at,
            e.can ? chip("can", "Waiting for their reply", "They may reply to this answer, once") : null)
        .append(mk("p", "fb-said", saidText(e) || "(no words)"));
    });
    return box;
  }

  // What goes when a report or a problem is deleted, said before it goes.
  function deleteWords(r, group) {
    if (group) {
      const n = group.all || group.count;
      const outside = n - group.count;
      return "Delete this problem for good: " + (n === 1 ? "its one report" : "all " + plural(n, "report", "reports")) +
        (outside > 0 ? " (" + num.format(outside) + " of them outside the period shown)" : "") +
        ", the answers to it and every reply to those? Whoever asked for answers in the app finds nothing any more." +
        " This cannot be undone.";
    }
    const talk = r.conversation || [];
    const answers = talk.filter((e) => e.who === "answer" && !e.group).length;
    const replies = talk.filter((e) => e.who === "reply").length;
    const parts = ["the report"];
    if (answers) { parts.push(plural(answers, "answer", "answers")); }
    if (replies) { parts.push(plural(replies, "reply from them", "replies from them")); }
    const list = parts.length > 1 ? parts.slice(0, -1).join(", ") + " and " + parts[parts.length - 1] : parts[0];
    return "Delete this " + (r.kind === "feedback" ? "feedback" : (KIND[r.kind] || "report").toLowerCase() + " report") +
      " for good: " + list + (r.answerable ? " - and its code, so their app finds nothing for it any more" : "") +
      "? This cannot be undone.";
  }

  // The buttons over an open report or problem.
  function actions(r, key, group) {
    const bar = mk("div", "fb-actions");
    // The same plain text, fenced, to paste into a conversation about a fix.
    bar.append(copyButton("Copy for a bug fix", "btn", () => "```\n" + (r.copy || r.text || "").replace(/\n+$/, "") + "\n```\n"));
    [["seen", "Mark seen"], ["fixed", "Mark fixed"], ["new", "Mark new"]].forEach(([state, label]) => {
      const b = mk("button", "btn quiet", label);
      b.type = "button";
      b.addEventListener("click", () => post("/admin/feedback-mark", { key: key, state: state }).then((ok) => {
        if (ok) { reload(); }
      }));
      bar.append(b);
    });
    const isArchived = group ? !!group.archived : !!r.archived;
    const arch = mk("button", "btn quiet", isArchived ? "Unarchive" : "Archive");
    arch.type = "button";
    arch.title = isArchived ? "Back into the lists" : "Out of the lists and the unread count - under Archived";
    arch.addEventListener("click", () => post("/admin/feedback-archive", { key: key, archived: !isArchived })
      .then((ok) => { if (ok) { reload(); } }));
    const del = mk("button", "btn quiet danger", "Delete…");
    del.type = "button";
    del.addEventListener("click", () => {
      if (!window.confirm(deleteWords(r, group))) { return; }
      del.disabled = true;
      post("/admin/feedback-delete", { key: key, group: !!group }).then((ok) => {
        if (!ok) { del.disabled = false; del.textContent = "Could not delete it"; return; }
        if (open) { open.hidden = true; }
        open = null; openKey = "";
        copies.delete(r.id);
        show();
      });
    });
    bar.append(arch, del);
    return bar;
  }

  // A report in full: its conversation, a copy to paste where it gets
  // fixed, the marks, the archive, and the answer box; a problem: its
  // latest report under the answer to all of them.
  function fill(body, r, key, group, first) {
    if (r.copy) { copies.set(r.id, r.copy); }
    const bar = actions(r, key, group);
    if (group) {
      body.replaceChildren(bar, answerBox(r, key, group), mk("pre", "fb-text", r.text));
    } else {
      // The answer being written stays as it is when the conversation is
      // drawn again (a reply came, a mark changed).
      const form = (!first && body.fbForm) || answerBox(r, key, null);
      body.fbForm = form;
      body.replaceChildren(bar, thread(r), form);
    }
    if (r.state === "new" && key === r.id) { post("/admin/feedback-mark", { key: r.id, state: "seen" }); }
    // Its replies read now too.
    if ((r.unread || []).length) { post("/admin/feedback-mark", { keys: r.unread, state: "seen" }); }
  }
  function load(body, id, key, group, first) {
    return get("/admin/feedback-item?id=" + encodeURIComponent(id)).then((r) => {
      if (open !== body) { return; }              // closed meanwhile
      fill(body, r, key, group, first);
    }).catch((e) => {
      if (first && open === body) { body.replaceChildren(mk("p", "error", "Could not load it: " + e.message)); }
    });
  }
  function toggle(body, rk, id, key, group) {
    if (!body.hidden) { body.hidden = true; open = null; openKey = ""; return; }
    if (open && open !== body) { open.hidden = true; }
    open = body;
    openKey = rk;
    body.fbArgs = { id: id, key: key, group: !!group };
    body.fbForm = null;
    body.hidden = false;
    body.replaceChildren(mk("p", "muted", "Loading…"));
    load(body, id, key, group, true);
  }

  // -- what comes in by itself ---------------------------------------------------
  // While the page is in front: asked every EVERY ms with the revision the
  // page has - the tab's own period while it shows, else everything for the
  // tab's count - and told "same" when nothing changed. Signed out: no more
  // asking (and on the tab, the sign-in page, as everywhere).
  const EVERY = 45000;
  let timer = 0, asking = false, stopped = false, badgeRev = "", waiting = null;
  function onTab() { const t = $("tab-feedback"); return !!t && !t.hidden; }
  // An answer being typed or a list being chosen from on the tab: a redraw
  // now would take it away - it waits until the field is left.
  function busy() {
    const ae = document.activeElement;
    const tab = $("tab-feedback");
    return !!ae && !!tab && tab.contains(ae) && /^(SELECT|INPUT|TEXTAREA)$/.test(ae.tagName);
  }
  function later() {
    clearTimeout(timer);
    if (!stopped && document.visibilityState === "visible") { timer = setTimeout(tick, EVERY); }
  }
  function tick() {
    clearTimeout(timer);
    if (stopped || document.visibilityState !== "visible") { return; }
    if (asking || loading) { later(); return; }
    const here = onTab() && !!res;
    const days = here ? K.days() : 0;
    asking = true;
    fetch("/admin/feedback?days=" + days + "&rev=" + encodeURIComponent((here ? res.rev : badgeRev) || ""),
          { credentials: "same-origin", cache: "no-store" })
      .then((r) => {
        if (r.status === 401) {
          stopped = true;
          if (onTab()) { location.replace(LOGIN); }
          return null;
        }
        return r.ok ? r.json() : null;
      })
      .then((r) => {
        if (!r || r.same) { return; }
        if (!here) { badgeRev = r.rev; count(r.new); return; }
        if (onTab() && res && !loading && K.days() === days) { update(r); }
      })
      .catch(() => { /* asked again at the next tick */ })
      .then(() => { asking = false; later(); });
  }
  // The row at the top of the window, and where it is: the same row at the
  // same place once the lists are drawn again.
  function topRow() {
    const rows = [].slice.call(document.querySelectorAll("#tab-feedback .fb-row"));
    const top = rows.find((li) => li.getBoundingClientRect().bottom > 0);
    return top ? { key: top.dataset.key, y: top.getBoundingClientRect().top } : null;
  }
  function keepPlace(a) {
    if (!a) { return; }
    const li = [].slice.call(document.querySelectorAll("#tab-feedback .fb-row")).find((x) => x.dataset.key === a.key);
    if (li) { window.scrollBy(0, li.getBoundingClientRect().top - a.y); }
  }
  // The newer summary drawn in place: what is new lights up, the report
  // open stays open (its conversation again when a reply came, the answer
  // being typed kept), the filters and the scroll stay.
  function update(r) {
    count(r.new);
    if (busy()) { waiting = r; return; }
    waiting = null;
    const items = new Map((res.items || []).map((i) => [i.id, i]));
    const counts = new Map((res.groups || []).map((g) => [g.key, g.count]));
    fresh = new Set();
    (r.items || []).forEach((i) => {
      const was = items.get(i.id);
      if (!was || was.last < i.last) { fresh.add("i:" + i.id); }
    });
    (r.groups || []).forEach((g) => { if (!counts.has(g.key) || counts.get(g.key) < g.count) { fresh.add("g:" + g.key); } });
    const wasOpen = openKey.charAt(0) === "i" ? items.get(openKey.slice(2)) : null;
    (r.items || []).forEach((i) => { const was = items.get(i.id); if (was && was.last < i.last) { copies.delete(i.id); } });
    const anchor = topRow();
    res = r;
    draw();
    fresh = new Set();
    keepPlace(anchor);
    if (open && wasOpen && open.fbArgs) {
      const now = (res.items || []).find((i) => i.id === wasOpen.id);
      if (now && (now.last !== wasOpen.last || now.replies !== wasOpen.replies || now.open !== wasOpen.open)) {
        load(open, open.fbArgs.id, open.fbArgs.key, null, false);
      }
    }
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") { tick(); } else { clearTimeout(timer); }
  });
  // Left the field it waited for: drawn now.
  document.addEventListener("focusout", () => setTimeout(() => {
    if (waiting && !busy() && onTab()) { update(waiting); }
  }, 0));

  // The tab says how many are not read yet, from the start.
  function count(n) {
    const c = $("fb-count");
    if (!c) { return; }
    c.hidden = !n;
    c.textContent = n > 99 ? "99+" : String(n || "");
  }
  const draw0 = draw;
  draw = function () { draw0(); count(res.new); };
  setTimeout(tick, 1200);

  window.AdminFeedback = { show: show, badge: () => (res ? res.new : 0) };
}());
