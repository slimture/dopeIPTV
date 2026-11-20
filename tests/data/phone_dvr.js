// The phone remote's rewind buffer bar in Chromium through Playwright
// (tests/test_phone_dvr.py runs it): node phone_dvr.js '<runs as JSON>'
// Each run is {url, w, h, lang}; the host plays a live channel 4:12 behind
// live in a buffer reaching back 32:10. Prints one line of JSON:
// {problems, errors, runs: {tag: {...}}, steady: [statuses]}.
"use strict";
const {chromium} = require("playwright");

const runs = JSON.parse(process.argv[2]);

// The page wider than the screen, a visible key under 44 px or past the
// screen's side, the buffer's parts outside Now playing's card.
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
  const card = document.getElementById("now").getBoundingClientRect();
  for (const id of ["liveBtn", "skipBack", "skipFwd", "nowStart", "nowBarWrap"]) {
    const r = document.getElementById(id).getBoundingClientRect();
    if (r.left < card.left - 0.5 || r.right > card.right + 0.5) out.push(`${id} outside Now playing`);
  }
  return out;
}

function read() {
  const $ = (id) => document.getElementById(id);
  return {start: $("nowStart").textContent, live: $("liveBtn").lastElementChild.textContent,
          on: $("liveBtn").classList.contains("on"), endHidden: $("nowEnd").hidden,
          bar: $("nowBar").style.width, can: $("nowBarWrap").classList.contains("can")};
}

(async () => {
  const browser = await chromium.launch();
  const out = {problems: [], errors: [], runs: {}, steady: null};
  try {
    for (const run of runs) {
      const tag = `${run.w}x${run.h}-${run.lang}`;
      const ctx = await browser.newContext({viewport: {width: run.w, height: run.h}, deviceScaleFactor: 2,
                                            isMobile: true, hasTouch: true});
      const page = await ctx.newPage();
      page.on("pageerror", (e) => out.errors.push(`${tag}: ${e}`));
      page.on("console", (m) => { if (m.type() === "error") out.errors.push(`${tag}: ${m.text()}`); });
      await page.goto(run.url);
      await page.waitForFunction(() => !document.getElementById("liveBtn").hidden, null, {timeout: 8000});
      await page.waitForTimeout(150);
      const r = {first: await page.evaluate(read), words: await page.evaluate(() => ({live: W.live}))};
      for (const p of await page.evaluate(audit)) out.problems.push(`${tag}: ${p}`);
      // A drag from a fifth of the way to the middle, then let go.
      const bar = await page.evaluate(() => {
        const b = document.getElementById("nowBarWrap").getBoundingClientRect();
        return {left: b.left, w: b.width, y: b.top + b.height / 2};
      });
      const at = (f) => (run.lang === "ar" ? bar.left + bar.w * (1 - f) : bar.left + bar.w * f);
      await page.mouse.move(at(0.2), bar.y);
      await page.mouse.down();
      await page.mouse.move(at(0.5), bar.y, {steps: 5});
      r.tip = await page.evaluate(() => document.getElementById("seekTip").textContent);
      const changed = (was) => page.waitForFunction((w) => document.getElementById("liveBtn").textContent !== w,
                                                    was, {timeout: 8000});
      await page.mouse.up();
      await changed(r.first.live);
      await page.waitForTimeout(1200);
      r.afterDrag = await page.evaluate(read);
      await page.click("#skipBack");
      await changed(r.afterDrag.live);
      await page.waitForTimeout(1200);
      r.afterBack = await page.evaluate(read);
      await page.click("#liveBtn");
      await page.waitForFunction(() => document.getElementById("liveBtn").classList.contains("on"), null,
                                 {timeout: 8000});
      await page.waitForTimeout(1200);
      r.afterLive = await page.evaluate(read);
      for (const p of await page.evaluate(audit)) out.problems.push(`${tag} at live: ${p}`);
      await page.click("#skipFwd");                    // at live: nothing to send
      await page.waitForTimeout(500);
      if (out.steady === null) {
        out.steady = await page.evaluate(async () => {
          const a = await signedFetch("api/state", {});
          const etag = a.headers.get("ETag"), got = [];
          for (let i = 0; i < 2; i++) {
            await new Promise((ok) => setTimeout(ok, 1500));
            got.push((await signedFetch("api/state", {headers: {"If-None-Match": etag}})).status);
          }
          return got;
        });
      }
      out.runs[tag] = r;
      // The host back to where it started for the next run.
      await page.evaluate(() => signedFetch("api/stop", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"}));
      await ctx.close();
    }
  } finally {
    await browser.close();
  }
  process.stdout.write(JSON.stringify(out) + "\n");
})().catch((e) => { process.stdout.write(JSON.stringify({fatal: String(e && e.stack || e)}) + "\n"); process.exit(1); });
