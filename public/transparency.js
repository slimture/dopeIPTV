/* Privacy, behind the scenes (/transparency/): each part's button copies its
   question and its code together, ready to paste into any AI assistant. The
   code is the page's own (.tp-src, each block headed with the address of its
   lines on GitHub); nothing is fetched. Where the page may not write the
   clipboard, the text is put in a box, selected, to be copied by hand. */
(function () {
  "use strict";
  var buttons = document.querySelectorAll(".tp-copy[data-for]");
  if (!buttons.length) { return; }
  function textFor(part) {
    if (part === "all") { return everything(); }
    var ask = document.querySelector('.tp-ask[data-for="' + part + '"]');
    var out = [ask ? ask.textContent.replace(/\s+/g, " ").trim() : ""];
    [].forEach.call(document.querySelectorAll('.tp-src[data-for="' + part + '"]'), function (src) {
      var code = src.querySelector("pre code");
      out.push("--- " + src.getAttribute("data-head") + " ---\n" + (code ? code.textContent : ""));
    });
    return out.join("\n\n");
  }
  // "Copy everything": the question over all of them, then every part's
  // heading, question and code, in the page's order.
  function everything() {
    var all = document.querySelector('.tp-ask[data-for="all"]');
    var out = [all ? all.textContent.replace(/\s+/g, " ").trim() : ""];
    [].forEach.call(document.querySelectorAll(".tp-ask[data-for]"), function (ask) {
      var part = ask.getAttribute("data-for");
      if (part === "all") { return; }
      var h = document.getElementById(part);
      out.push("## " + (h ? h.textContent.replace(/\s+/g, " ").trim() : part) + "\n\n" + textFor(part));
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
      var text = textFor(btn.getAttribute("data-for"));
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
