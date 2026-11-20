// The phone remote's page at phone sizes, in Chromium through Playwright
// (tests/test_phone_layout.py runs it): node phone_layout.js '<runs as JSON>' [shots dir]
// Each run is {url, w, h, scheme, lang, full}. Prints one line of JSON:
// {problems: [...], checks: {...}} - what a person could not reach or read,
// and what the test asserts besides.
"use strict";
const {chromium} = require("playwright");

const runs = JSON.parse(process.argv[2]);
const shots = process.argv[3] || "";

// The page wider than the screen, a visible key under 44 px or past the
// screen's side (the row of sections scrolls sideways on its own), words cut
// in a key.
function audit() {
  const vw = window.innerWidth, out = [];
  const sw = document.scrollingElement.scrollWidth;
  if (sw > vw + 0.5) out.push(`page ${sw}px wide in ${vw}px`);
  for (const el of document.querySelectorAll("button, input, [role=button]")) {
    if (el.closest("[hidden]") || !el.getClientRects().length) continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const name = el.id || el.className || el.tagName;
    const key = !["range", "search", "checkbox"].includes(el.type);
    if (key && (r.width < 43.5 || r.height < 43.5)) out.push(`${name} ${r.width.toFixed(1)}x${r.height.toFixed(1)}`);
    if (!el.closest(".tabs") && (r.left < -0.5 || r.right > vw + 0.5)) {
      out.push(`${name} off screen ${r.left.toFixed(0)}..${r.right.toFixed(0)}`);
    }
  }
  for (const el of document.querySelectorAll(".pill span, .fold span, .ck span, .tvBtn span, .moreIn span, .okk")) {
    if (el.closest("[hidden]") || !el.getClientRects().length) continue;
    if (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1) {
      out.push(`cut: ${el.id || el.parentElement.id || el.className} "${el.textContent}"`);
    }
  }
  return out;
}

const shown = (page, sel) => page.waitForFunction(
  (s) => { const e = document.querySelector(s); return !!e && !e.closest("[hidden]") && e.getClientRects().length > 0; },
  sel, {timeout: 8000});
const gone = (page, sel) => page.waitForFunction(
  (s) => { const e = document.querySelector(s); return !e || !!e.closest("[hidden]") || !e.getClientRects().length; },
  sel, {timeout: 8000});

(async () => {
  const browser = await chromium.launch();
  const problems = [], checks = {fits: {}, folded: {}, recLive: {}, tvRec: {}, tiles: {}, remembered: null, errors: []};
  try {
    for (const run of runs) {
      const tag = `${run.w}x${run.h}-${run.scheme}${run.lang === "en" ? "" : "-" + run.lang}`;
      const ctx = await browser.newContext({viewport: {width: run.w, height: run.h}, deviceScaleFactor: 2,
                                            isMobile: true, hasTouch: true, colorScheme: run.scheme});
      const page = await ctx.newPage();
      page.on("pageerror", (e) => checks.errors.push(`${tag}: ${e}`));
      page.on("console", (m) => { if (m.type() === "error") checks.errors.push(`${tag}: ${m.text()}`); });
      const look = async (what) => {
        for (const p of await page.evaluate(audit)) problems.push(`${what} ${tag}: ${p}`);
        if (shots) await page.screenshot({path: `${shots}/${what}-${tag}.png`, animations: "disabled"});
      };
      await page.goto(run.url);
      await shown(page, "#moreBtn");
      await page.waitForTimeout(150);

      // Now playing folded: More, and nothing of what it holds.
      checks.folded[tag] = await page.evaluate(() => ({
        expanded: document.getElementById("moreBtn").getAttribute("aria-expanded"),
        body: document.getElementById("nowMore").hidden, height: document.getElementById("now").offsetHeight}));
      await look("normal-folded");
      await page.click("#moreBtn");
      await shown(page, "#tracksBtn");
      await shown(page, "#recBtn");
      // Open: Subtitles and sound, the Sleep timer and Record an even row of
      // tiles across the card, each its icon over its words.
      checks.tiles[tag] = await page.evaluate(() => ["tracksBtn", "sleepBtn", "recBtn"].map((id) => {
        const b = document.getElementById(id), r = b.getBoundingClientRect();
        const icon = b.querySelector("svg").getBoundingClientRect(), words = b.querySelector("span:last-child").getBoundingClientRect();
        return {top: Math.round(r.top), w: r.width, h: r.height, iconAbove: icon.bottom <= words.top + 1,
                wide: document.getElementById("nowMore").getBoundingClientRect().width};
      }));
      await look("normal-open");
      if (run.full && checks.remembered === null) {
        await page.reload();
        await shown(page, "#tracksBtn");
        checks.remembered = await page.evaluate(() => [document.getElementById("moreBtn").getAttribute("aria-expanded"),
                                                       localStorage.getItem("more")]);
      }
      await page.click("#moreBtn");
      await gone(page, "#tracksBtn");

      // TV mode's remote: it fits the screen as it opens, then each fold.
      await page.click("#tvBtn");
      await shown(page, "#tvr");
      await page.waitForTimeout(150);
      checks.fits[tag] = await page.evaluate(() => [document.scrollingElement.scrollHeight, window.innerHeight]);
      await look("tv");
      if (run.full) {
        for (const [btn, what] of [["#digitsBtn", "tv-digits"], ["#padMode", "tv-touchpad"], ["#tvMoreBtn", "tv-more"]]) {
          await page.click(btn);
          await page.waitForTimeout(120);
          await look(what);
          await page.click(btn);
        }
        // Record from the remote's fold: the tile turns red with its time while
        // it records; folded, the dot and its time say it.
        await page.click("#tvMoreBtn");
        await shown(page, "#tvRec");
        await page.click("#tvRec");
        await page.waitForFunction(() => document.getElementById("tvRec").classList.contains("on"), null, {timeout: 8000});
        await gone(page, "#tvRecLive");
        checks.tvRec[tag] = await page.evaluate(() => ({
          open: document.getElementById("tvMoreBtn").getAttribute("aria-expanded"),
          text: document.getElementById("tvRecText").textContent,
          red: getComputedStyle(document.getElementById("tvRec")).backgroundColor}));
        await look("tv-more-rec");
        await page.click("#tvMoreBtn");
        await shown(page, "#tvRecLive");
        await page.evaluate(() => signedFetch("api/rec_stop", {method: "POST", headers: {"Content-Type": "application/json"},
                                                         body: "{}"}));
        await gone(page, "#tvRecLive");
      }
      await page.click("#tvBtn");
      await shown(page, "#moreBtn");

      if (run.full) {
        // A recording: started from the open card, then folded - its dot and time stay.
        await page.click("#moreBtn");
        await shown(page, "#recBtn");
        await page.click("#recBtn");
        await page.waitForFunction(() => document.getElementById("recBtn").classList.contains("on"), null, {timeout: 8000});
        await page.click("#moreBtn");
        await shown(page, "#recLive");
        checks.recLive[tag] = await page.evaluate(() => ({
          text: document.getElementById("recLive").textContent, label: document.getElementById("recLive").getAttribute("aria-label"),
          recBtnHidden: !!document.getElementById("recBtn").closest("[hidden]")}));
        await look("normal-folded-rec");
        await page.click("#tvBtn");
        await shown(page, "#tvRecLive");
        await look("tv-rec");
      }
      // The host back to where it started for the next run.
      await page.evaluate(() => signedFetch("api/stop", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"}));
      await ctx.close();
    }
  } finally {
    await browser.close();
  }
  process.stdout.write(JSON.stringify({problems, checks}) + "\n");
})().catch((e) => { process.stdout.write(JSON.stringify({fatal: String(e && e.stack || e)}) + "\n"); process.exit(1); });
