/* Privacy, behind the scenes (/transparency/): each part's button copies its
   question, the page's own words for it - its promise, what we can know, the
   hosts it lists and the limits it names, with the rule for judging those -
   and its code together, ready to paste into any AI assistant. The
   code is the page's own (.tp-src, each block headed with the address of its
   lines on GitHub); nothing is fetched. Where the page may not write the
   clipboard, the text is put in a box, selected, to be copied by hand. */
(function () {
  "use strict";
  var buttons = document.querySelectorAll(".tp-copy[data-for]");
  if (!buttons.length) { return; }
  function clean(el) { return el.textContent.replace(/\s+/g, " ").trim(); }
  // The question over all of them names what a part's copy carries besides
  // its code: the page's own promise and the limits it names itself.
  function labels() {
    var all = document.querySelector('.tp-ask[data-for="all"]');
    return {promise: all ? all.getAttribute("data-promise") || "" : "",
            limits: all ? all.getAttribute("data-limits") || "" : ""};
  }
  // A part's own words and code, in the page's order: what stands between
  // its heading and the next - its promise, what we can know, the hosts it
  // lists, the limits it names - and every block of code shown there or
  // named for it.
  // The heading a list or table stands under: the h3 just before it, or the
  // one before its line of introduction.
  function headOf(el) {
    var prev = el.previousElementSibling;
    if (prev && prev.tagName === "P") { prev = prev.previousElementSibling; }
    return prev && prev.tagName === "H3" ? clean(prev) + ":\n" : "";
  }
  function section(part) {
    var h = document.getElementById(part), lab = labels(), words = [], code = [];
    function addCode(src) {
      if (code.indexOf(src) < 0) { code.push(src); }
    }
    // The page's words may sit in a block of their own (Read more, the
    // limits' box): looked for inside each, never inside the proof.
    var WORDS = ".tp-promise, .tp-know, table.tp-hosts, ul.tp-limits, ul.tp-names";
    function take(x) {
      if (x.classList.contains("tp-promise")) {
        words.push((lab.promise ? lab.promise + " " : "") + clean(x));
      } else if (x.classList.contains("tp-know")) {
        words.push(clean(x));
      } else if (x.matches("table.tp-hosts")) {
        words.push(headOf(x) + [].map.call(x.querySelectorAll("tbody tr"), function (tr) {
          return "- " + [].map.call(tr.cells, clean).join(" – ");
        }).join("\n"));
      } else {
        words.push(headOf(x) +
          [].map.call(x.children, function (li) { return "- " + clean(li); }).join("\n"));
      }
    }
    for (var el = h && h.nextElementSibling; el && el.tagName !== "H2"; el = el.nextElementSibling) {
      if (el.matches(WORDS)) { take(el); }
      [].forEach.call(el.querySelectorAll(WORDS), function (x) {
        if (!x.closest(".tp-proof")) { take(x); }
      });
      [].forEach.call(el.querySelectorAll(".tp-src"), addCode);
    }
    [].forEach.call(document.querySelectorAll('.tp-src[data-for="' + part + '"]'), addCode);
    return {words: words, code: code};
  }
  function textFor(part, alone) {
    if (part === "all") { return everything(); }
    var ask = document.querySelector('.tp-ask[data-for="' + part + '"]');
    var out = [ask ? clean(ask) : ""], got = section(part), lab = labels();
    if (alone && lab.limits && got.words.length) { out.push(lab.limits); }
    if (got.words.length) { out.push(got.words.join("\n\n")); }
    got.code.forEach(function (src) {
      var code = src.querySelector("pre code");
      out.push("--- " + src.getAttribute("data-head") + " ---\n" + (code ? code.textContent : ""));
    });
    return out.join("\n\n");
  }
  // "Copy everything": the question over all of them, then every part's
  // heading, question and code, in the page's order.
  function everything() {
    var all = document.querySelector('.tp-ask[data-for="all"]');
    var out = [all ? clean(all) : ""];
    [].forEach.call(document.querySelectorAll(".tp-ask[data-for]"), function (ask) {
      var part = ask.getAttribute("data-for");
      if (part === "all") { return; }
      var h = document.getElementById(part);
      // "1. Can you see…": the number is a span of its own on the page.
      var num = h && h.querySelector(".lnum"), title = h ? clean(h) : part;
      if (num) { title = clean(num) + ". " + title.slice(clean(num).length).trim(); }
      out.push("## " + title + "\n\n" + textFor(part, false));
    });
    return out.join("\n\n\n");
  }
  function byHand(btn, text) {
    var row = btn.parentNode, box = row.querySelector("textarea.tp-copied");
    if (!box) {
      box = document.createElement("textarea");
      box.className = "tp-copied";
      box.readOnly = true;
      box.rows = 8;
      box.setAttribute("aria-label", btn.textContent);
      row.appendChild(box);
    }
    box.value = text;
    box.focus();
    box.select();
  }
  [].forEach.call(buttons, function (btn) {
    var say = btn.textContent, done = btn.getAttribute("data-done") || say, timer = 0;
    btn.hidden = false;
    btn.addEventListener("click", function () {
      var text = textFor(btn.getAttribute("data-for"), true);
      if (!(navigator.clipboard && window.isSecureContext)) { byHand(btn, text); return; }
      navigator.clipboard.writeText(text).then(function () {
        btn.textContent = done;
        btn.classList.add("is-done");
        clearTimeout(timer);
        timer = setTimeout(function () {
          btn.textContent = say;
          btn.classList.remove("is-done");
        }, 2400);
      }, function () { byHand(btn, text); });
    });
  });
})();
