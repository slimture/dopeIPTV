/* The admin page's Builds tab: the release the site offers and the newest
 * test builds, each file with its download and a link to copy for a tester.
 * /admin/builds in donate.py (admin.builds). Every link goes through
 * /dl.php like the site's own: a test build's file is sent on to GitHub
 * there. An external file for the CSP.
 */
(function () {
  "use strict";
  const K = window.AdminKit;
  if (!K) { return; }
  const { $, mk } = K;
  const when = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const SHOWN = 3;                  // test builds listed at first; the rest behind "Older"
  let res = null, showAll = false, loading = null;

  // What a file is, in the order people look for them.
  const KINDS = [
    [/-setup\.exe$/i, "Windows", "installer"],
    [/windows.*\.zip$/i, "Windows", "portable zip"],
    [/\.dmg$/i, "macOS", ""],
    [/\.AppImage$/i, "Linux", "AppImage"],
    [/\.deb$/i, "Linux", ".deb"],
    [/\.rpm$/i, "Linux", ".rpm"],
    [/\.flatpak$/i, "Linux", "Flatpak"],
    [/\.(whl|tar\.gz)$/i, "Python", ""],
  ];
  function kind(name) {
    for (let i = 0; i < KINDS.length; i++) {
      const [re, os, what] = KINDS[i];
      if (re.test(name)) {
        let cpu = "";
        if (os === "macOS") { cpu = /arm64/.test(name) ? "Apple silicon" : "Intel"; }
        else if (os === "Windows") { cpu = /arm64/.test(name) ? "ARM" : "x64"; }
        else if (os === "Linux" && what !== "Flatpak") { cpu = /aarch64|arm64/.test(name) ? "ARM" : "x86_64"; }
        else if (os === "Python") { cpu = /\.whl$/i.test(name) ? "wheel" : "source"; }
        const arm = /arm64|aarch64/.test(name);
        return { order: i * 2 + (os === "macOS" ? (arm ? 0 : 1) : (arm ? 1 : 0)), os: os,
                 label: [what, cpu].filter(Boolean).join(" · ") };
      }
    }
    return { order: KINDS.length * 2, os: "Other", label: name };
  }
  function size(b) {
    if (!b) { return ""; }
    const mb = b / 1048576;
    return mb >= 1 ? (mb >= 100 ? Math.round(mb) : mb.toFixed(1)) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB";
  }
  function fullUrl(path) { return location.origin + path; }

  function fileRow(a) {
    const k = kind(a.name);
    const row = mk("div", "build-file");
    const what = mk("div", "build-what");
    what.append(mk("span", "build-os", k.os), mk("span", "build-kind", k.label));
    const name = mk("div", "build-name muted", a.name + (a.size ? " · " + size(a.size) : ""));
    if (a.sha256) { name.title = "SHA-256 " + a.sha256; }
    what.append(name);
    const dl = mk("a", "btn btn-small", "Download");
    dl.href = a.url;
    dl.setAttribute("download", "");
    const copy = mk("button", "btn btn-small", "Copy link");
    copy.type = "button";
    copy.addEventListener("click", () => {
      const done = (text) => { copy.textContent = text; setTimeout(() => { copy.textContent = "Copy link"; }, 1600); };
      if (!navigator.clipboard) { window.prompt("The link:", fullUrl(a.url)); return; }
      navigator.clipboard.writeText(fullUrl(a.url)).then(() => done("Copied"), () => window.prompt("The link:", fullUrl(a.url)));
    });
    const acts = mk("div", "build-acts");
    acts.append(dl, copy);
    row.append(what, acts);
    return row;
  }
  function fileList(assets) {
    const list = mk("div", "build-files");
    assets.slice().sort((x, y) => kind(x.name).order - kind(y.name).order || x.name.localeCompare(y.name))
      .forEach((a) => list.append(fileRow(a)));
    if (!assets.length) { list.append(mk("p", "muted", "No files yet.")); }
    return list;
  }
  function meta(b) {
    const bits = [];
    if (b.published_at) { bits.push(when.format(new Date(b.published_at))); }
    if (b.commit) { bits.push("testing @ " + b.commit.slice(0, 7)); }
    return bits.join(" · ");
  }
  function ghLink(b) {
    if (!b.html_url) { return null; }
    const a = mk("a", "link", "On GitHub");
    a.href = b.html_url;
    a.target = "_blank";
    a.rel = "noopener";
    return a;
  }

  // One build: a line that opens to its files - the release first, closed;
  // the newest test build open.
  function buildCard(b, open, release) {
    const c = mk("details", "card build" + (release ? " release" : ""));
    c.open = open;
    const sum = mk("summary", "build-sum");
    sum.append(mk("span", "build-ver", b.version));
    if (release) { sum.append(mk("span", "pill rel", "Release, on the site")); }
    if (!release && b.version === res.offered) { sum.append(mk("span", "pill", "Offered on Test builds")); }
    sum.append(mk("span", "muted build-when", meta(b)));
    c.append(sum);
    if (b.changes && b.changes.length) {
      const ch = mk("details", "build-changes");
      ch.append(mk("summary", "link", "Changes since the release (" + b.changes.length + ")"));
      const ul = mk("ul", "");
      b.changes.forEach((t) => ul.append(mk("li", "", t)));
      ch.append(ul);
      c.append(ch);
    }
    c.append(fileList(b.assets || []));
    const gh = ghLink(b);
    if (gh) { const p = mk("p", "build-foot"); p.append(gh); c.append(p); }
    return c;
  }

  function render() {
    const box = $("builds");
    const tests = res.tests || [];
    const parts = [mk("p", "muted build-intro", "The release the site offers and the newest test builds, built from " +
      "testing after each change. Every link goes through dopeiptv.org/dl.php and works for anyone; the site " +
      "itself lists no test build.")];
    if (res.error) {
      parts.push(mk("p", "error", tests.length
        ? "The newest test builds could not be fetched just now (" + res.error + "); these are from " +
          when.format(new Date(res.checked * 1000)) + "."
        : "Could not fetch the test builds: " + res.error + "."));
    }
    if (res.release) { parts.push(buildCard(res.release, false, true)); }
    tests.slice(0, showAll ? tests.length : SHOWN).forEach((b, i) => parts.push(buildCard(b, i === 0, false)));
    if (tests.length > SHOWN) {
      const more = mk("button", "link build-more", showAll ? "Fewer" : "Older test builds (" + (tests.length - SHOWN) + ")");
      more.type = "button";
      more.addEventListener("click", () => { showAll = !showAll; render(); });
      parts.push(more);
    } else if (!tests.length && !res.error) {
      parts.push(mk("p", "muted", "No test builds on GitHub."));
    }
    box.replaceChildren(...parts);
  }
  function load() {
    if (loading) { return loading; }
    if (!res) { $("builds").replaceChildren(mk("p", "muted", "Loading…")); }
    loading = fetch("/admin/builds", { credentials: "same-origin", cache: "no-store" })
      .then((r) => {
        if (!r.ok) { throw new Error("The service answered " + r.status + (r.status === 404 ? " - restart it: service iptv_donate restart" : "") + "."); }
        return r.json();
      })
      .then((r) => { res = r; render(); })
      .catch((e) => { if (!res) { $("builds").replaceChildren(mk("p", "error", "Could not load the builds: " + e.message)); } })
      .then(() => { loading = null; });
    return loading;
  }
  window.AdminBuilds = { show: load };
})();
