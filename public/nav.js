/* Header disclosures: close them when you click away or press Escape.
 *
 * Everything the header does works without this file - <details> is a native
 * disclosure and the items are plain links. This only adds the dismissal a
 * menu is expected to have, so losing it costs politeness, not navigation.
 *
 * An EXTERNAL file, like help.js: the site's CSP is `script-src 'self'` with
 * no 'unsafe-inline' (deploy/csp.snippet.txt), and an inline block would be
 * blocked without a word.
 */
(function () {
  var menus = [].slice.call(document.querySelectorAll("header.bar details"));
  if (!menus.length) { return; }

  function shut(except) {
    menus.forEach(function (d) {
      if (d !== except && d.open) { d.open = false; }
    });
  }

  document.addEventListener("click", function (e) {
    var inside = null;
    menus.forEach(function (d) { if (d.contains(e.target)) { inside = d; } });
    // Older browsers ignore the `name` attribute that makes the disclosures
    // an exclusive pair, so close the others here too.
    shut(inside);
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { shut(null); }
  });
})();

/* The donate dialog: Stripe's card form (the Payment Element) in a dialog on
 * this page, as on solequiz.com - nothing leaves the site but the payment
 * itself, which goes from Stripe's iframes straight to Stripe.
 *
 * Every donate link (the card, the header, the footer) points at Stripe's own
 * page, so with this file gone or no <dialog>, a click simply goes there.
 * Once the dialog is open it never leaves the page: a failure is a sentence
 * in the dialog and the reason in the console. The page holds the dialog's
 * words in #donate-i18n; amounts and the publishable key come from
 * /donate/config, the PaymentIntent from /donate/session.
 *
 * Apple Pay and Google Pay go as on solequiz.com: where the device has one,
 * the main button opens the wallet's own sheet (Stripe's Payment Request)
 * and /donate/wallet confirms the payment with what the wallet hands back;
 * "Pay with card" under it opens the card form.
 */
(function () {
  "use strict";
  var words = document.getElementById("donate-i18n");
  if (!words || !window.fetch || !window.Promise ||
      typeof HTMLDialogElement !== "function") { return; }
  var T = words.dataset;
  var LANG = T.lang || document.documentElement.lang || "en";
  // The Stripe.js release solequiz.com runs on against the same account.
  var STRIPE_JS = "https://js.stripe.com/endive/stripe.js";
  var ZERO_DECIMAL = /^(bif|clp|djf|gnf|jpy|kmf|krw|mga|pyg|rwf|ugx|vnd|vuv|xaf|xof|xpf)$/;
  var cfg = null, stripeLoading = null, dlg = null, el = {}, chosen = 0, live = null, busy = false;
  var stripe = null, pr = null, wallet = null, walletAmount = 0;
  var APPLE = /iPhone|iPad|iPod|Macintosh/.test(navigator.userAgent);

  function isDonate(a) {
    return !!a && /^https:\/\/(donate|buy)\.stripe\.com\//.test(a.href || "");
  }
  function mk(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text) { n.textContent = text; }
    return n;
  }
  function fmt(minor) {
    var cur = (cfg && cfg.currency || "eur").toUpperCase();
    var major = ZERO_DECIMAL.test(cur.toLowerCase()) ? minor : minor / 100;
    try {
      return new Intl.NumberFormat(LANG, { style: "currency", currency: cur,
        minimumFractionDigits: major % 1 ? 2 : 0 }).format(major);
    } catch (e) { return major + " " + cur; }
  }
  function toMinor(text) {
    var v = parseFloat(String(text).replace(/\s/g, "").replace(",", "."));
    if (!isFinite(v)) { return 0; }
    var zero = ZERO_DECIMAL.test((cfg && cfg.currency) || "eur");
    return Math.round(zero ? v : v * 100);
  }
  function loadStripe() {
    if (window.Stripe) { return Promise.resolve(window.Stripe); }
    if (!stripeLoading) {
      stripeLoading = new Promise(function (ok, fail) {
        var s = document.createElement("script");
        s.src = STRIPE_JS;
        s.onload = function () { ok(window.Stripe); };
        s.onerror = function () { stripeLoading = null; s.remove(); fail(new Error("stripe.js")); };
        document.head.appendChild(s);
      });
    }
    return stripeLoading;
  }
  function css(name, dflt) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || dflt;
  }
  function appearance() {
    var dark = css("color-scheme", "dark").indexOf("light") < 0;
    return {
      theme: dark ? "night" : "stripe",
      variables: {
        colorPrimary: css("--accent", "#2997ff"), colorText: css("--text", "#f5f5f7"),
        colorTextSecondary: css("--muted", "#aeaeb4"), colorBackground: css("--surface", "#202023"),
        colorDanger: "#ff6b6b", borderRadius: "12px", spacingUnit: "4px", fontSizeBase: "15px",
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
      },
      rules: {
        ".Input": { backgroundColor: css("--ink", "#0e0e10"), border: "1px solid " + css("--line-2", "#45454b"), boxShadow: "none" },
        ".Input:focus": { border: "1px solid " + css("--accent", "#2997ff"), boxShadow: "0 0 0 1px " + css("--accent", "#2997ff") },
        ".Label": { fontWeight: "600" }
      }
    };
  }
  // What went wrong goes to the console, for whoever looks after the site;
  // the visitor gets a plain sentence and stays on the page.
  function warn(what, err) { try { console.warn("[donate] " + what, err || ""); } catch (e) { /* none */ } }

  function build() {
    dlg = mk("dialog", "dn");
    dlg.setAttribute("aria-labelledby", "dn-title");
    var head = mk("div", "dn-head"), ht = mk("div");
    var h = mk("h2", "", T.title); h.id = "dn-title";
    ht.append(h, mk("p", "", T.sub));
    var x = mk("button", "dn-x", "×"); x.type = "button"; x.setAttribute("aria-label", T.close);
    x.addEventListener("click", close);
    head.append(ht, x);

    el.amounts = mk("div", "dn-step");
    el.chips = mk("div", "dn-chips");
    el.chips.setAttribute("role", "radiogroup");
    var other = mk("label", "dn-other", T.other);
    el.custom = mk("input", "dn-input");
    Object.assign(el.custom, { type: "text", inputMode: "decimal", autocomplete: "off" });
    el.custom.addEventListener("input", function () {
      chosen = toMinor(el.custom.value); mark(); el.err1.hidden = true;
    });
    other.append(el.custom);
    el.err1 = mk("p", "dn-error"); el.err1.hidden = true; el.err1.setAttribute("role", "alert");
    el.go = mk("button", "btn primary dn-go", T.cont); el.go.type = "button";
    el.go.addEventListener("click", function () {
      if (wallet) { payWallet(); } else { toPay(); }
    });
    el.card = mk("button", "dn-card", T.card); el.card.type = "button"; el.card.hidden = true;
    el.card.addEventListener("click", toPay);
    el.amounts.append(el.chips, other, el.err1, el.go, el.card);

    el.pay = mk("div", "dn-step"); el.pay.hidden = true;
    var top = mk("div", "dn-paytop");
    el.back = mk("button", "dn-back", (document.documentElement.dir === "rtl" ? "→ " : "← ") + T.back); el.back.type = "button";
    el.back.addEventListener("click", toAmounts);
    el.sum = mk("strong", "dn-sum");
    top.append(el.back, el.sum);
    var em = mk("label", "dn-email", T.email);
    el.email = mk("input", "dn-input");
    Object.assign(el.email, { type: "email", name: "email", autocomplete: "email", required: true, spellcheck: false });
    em.append(el.email);
    el.fields = mk("div", "dn-fields");
    el.err2 = mk("p", "dn-error"); el.err2.hidden = true; el.err2.setAttribute("role", "alert");
    el.payBtn = mk("button", "btn primary dn-paybtn"); el.payBtn.type = "button"; el.payBtn.disabled = true;
    el.payBtn.addEventListener("click", pay);
    el.pay.append(top, em, el.fields, el.err2, el.payBtn, mk("p", "dn-lock", T.lock));

    el.loading = mk("p", "dn-loading", T.loading); el.loading.hidden = true;
    el.done = mk("div", "dn-step dn-done"); el.done.hidden = true;
    el.done.append(mk("p", "dn-heart", "♥"), mk("h3", "", T.thanks), mk("p", "", T.receipt));

    var body = mk("div", "dn-body");
    body.append(el.loading, el.amounts, el.pay, el.done);
    dlg.append(head, body);
    dlg.addEventListener("cancel", function (e) { e.preventDefault(); close(); });
    dlg.addEventListener("click", function (e) { if (e.target === dlg) { close(); } });
    document.body.appendChild(dlg);
  }
  function show(step) {
    [el.loading, el.amounts, el.pay, el.done].forEach(function (s) { s.hidden = s !== step; });
  }
  function mark() {
    [].forEach.call(el.chips.children, function (c) {
      var on = Number(c.dataset.amount) === chosen;
      c.classList.toggle("is-on", on);
      c.setAttribute("aria-checked", on ? "true" : "false");
    });
  }
  function chips() {
    el.chips.replaceChildren();
    (cfg.presets || []).forEach(function (minor) {
      var b = mk("button", "dn-chip", fmt(minor)); b.type = "button";
      b.setAttribute("role", "radio"); b.dataset.amount = String(minor);
      b.addEventListener("click", function () { chosen = minor; el.custom.value = ""; el.err1.hidden = true; mark(); });
      el.chips.appendChild(b);
    });
    if (!chosen && cfg.presets && cfg.presets.length) { chosen = cfg.presets[Math.min(1, cfg.presets.length - 1)]; }
    mark();
  }
  function teardown() {
    if (live) { try { live.destroy(); } catch (e) { /* gone already */ } }
    live = null; el.fields.replaceChildren();
  }
  function close() {
    teardown(); busy = false;
    if (dlg && dlg.open) { dlg.close(); }
  }
  function toAmounts() { teardown(); show(el.amounts); }

  function open(href) {
    if (!dlg) { build(); }
    el.loading.textContent = T.loading;
    show(el.loading);
    dlg.showModal();
    (cfg ? Promise.resolve(cfg) : fetch("/donate/config", { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) { throw new Error("/donate/config answered " + r.status); } return r.json(); }))
      .then(function (c) { cfg = c; chips(); show(el.amounts); prepareWallet(); })
      .catch(function (err) {
        warn("the donate service did not answer (donate.py, nginx /donate/):", err);
        el.loading.textContent = T.failed;
      });
  }

  function amountOk() {
    if (!chosen || chosen < cfg.min || chosen > cfg.max) {
      el.err1.textContent = (T.range || "").replace("{min}", fmt(cfg.min)).replace("{max}", fmt(cfg.max));
      el.err1.hidden = false;
      return false;
    }
    return true;
  }
  function stripeFor(Stripe, key) {
    if (!stripe) { stripe = Stripe(key, { locale: LANG }); }
    return stripe;
  }
  // The wallet button, where there is a wallet: made ready when the dialog
  // opens, so a click can open the sheet at once (it must, inside the click).
  function prepareWallet() {
    if (pr || !cfg.publishableKey || !(window.ApplePaySession || window.PaymentRequest)) { return; }
    loadStripe().then(function (Stripe) {
      var st = stripeFor(Stripe, cfg.publishableKey);
      pr = st.paymentRequest({
        country: cfg.country || "SE", currency: (cfg.currency || "eur").toLowerCase(),
        total: { label: cfg.name || T.title, amount: chosen || cfg.min },
        requestPayerEmail: true, requestPayerName: true,
        disableWallets: ["link", "browserCard"]
      });
      pr.on("paymentmethod", walletPaid);
      return pr.canMakePayment().then(function (can) {
        wallet = can && ((APPLE && can.applePay && "apple") || (can.googlePay && "google")) || null;
        if (!wallet) { return; }
        el.go.textContent = wallet === "apple" ? T.apple.replace("Apple Pay", "\uF8FF\u00A0Pay") : T.google;
        el.go.classList.add("dn-wallet", "dn-wallet--" + wallet);
        el.card.hidden = false;
      });
    }).catch(function () { pr = null; wallet = null; });
  }
  function payWallet() {
    if (!amountOk() || !pr) { return; }
    walletAmount = chosen;
    pr.update({ total: { label: cfg.name || T.title, amount: walletAmount } });
    pr.show();
  }
  function walletDone() { show(el.done); }
  function walletFailed(msg) {
    show(el.amounts);
    el.err1.textContent = msg || T.failed; el.err1.hidden = false;
  }
  function walletPaid(ev) {
    fetch("/donate/wallet", { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount: walletAmount, paymentMethod: ev.paymentMethod.id,
        email: ev.payerEmail || "", name: ev.payerName || "" }) })
      .then(function (r) { return r.json().catch(function () { return {}; }); })
      .then(function (res) {
        if (res.ok) { ev.complete("success"); walletDone(); return; }
        if (res.action) {
          ev.complete("success");
          show(el.loading);
          var next = stripe.handleNextAction ? stripe.handleNextAction({ clientSecret: res.action })
                                             : stripe.confirmCardPayment(res.action);
          return next.then(function (r) {
            if (r.error) { walletFailed(r.error.message); } else { walletDone(); }
          });
        }
        ev.complete("fail");
        walletFailed(res.error && res.error !== "stripe" ? res.error : "");
      }, function () { ev.complete("fail"); walletFailed(); });
  }

  function toPay() {
    if (!amountOk()) { return; }
    var amount = chosen;
    el.loading.textContent = T.loading;
    show(el.loading);
    el.sum.textContent = fmt(amount);
    el.payBtn.textContent = (T.pay || "").replace("{amount}", fmt(amount));
    el.payBtn.disabled = true; el.err2.hidden = true;
    Promise.all([
      fetch("/donate/session", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ amount: amount }) })
        .then(function (r) {
          if (!r.ok) { throw new Error("/donate/session answered " + r.status + " (the reason is in /var/log/iptv-donate.log)"); }
          return r.json();
        }),
      loadStripe().catch(function () {
        throw new Error("Stripe.js did not load - is https://js.stripe.com in the site's Content-Security-Policy?");
      })
    ]).then(function (got) {
      var s = got[0], st = stripeFor(got[1], s.publishableKey);
      var elements = st.elements({ clientSecret: s.clientSecret, appearance: appearance() });
      // The wallets have their own button above, as on solequiz.com.
      var pe = elements.create("payment", { layout: { type: "accordion", defaultCollapsed: false },
        wallets: { applePay: "never", googlePay: "never" } });
      teardown();
      live = { elements: elements, destroy: function () { pe.destroy(); } };
      show(el.pay);
      pe.mount(el.fields);
      pe.on("change", function (ev) { if (!busy) { el.payBtn.disabled = !ev.complete; } });
      pe.on("loaderror", function (ev) {
        warn("the card form did not load:", ev && ev.error);
        fail();
      });
    }).catch(function (err) {
      warn("could not open the card form:", err);
      show(el.amounts);
      el.err1.textContent = T.failed; el.err1.hidden = false;
    });
  }

  function fail(msg) {
    el.err2.textContent = msg || T.failed; el.err2.hidden = false;
  }
  function payAgain(msg) {
    busy = false; el.payBtn.disabled = false;
    el.payBtn.textContent = (T.pay || "").replace("{amount}", el.sum.textContent);
    fail(msg);
  }
  function pay() {
    if (!live || !live.elements || busy) { return; }
    if (!el.email.checkValidity() || !el.email.value.trim()) {
      fail(T.emailmissing); el.email.focus(); return;
    }
    busy = true; el.err2.hidden = true;
    el.payBtn.disabled = true; el.payBtn.textContent = T.busy;
    stripe.confirmPayment({
      elements: live.elements,
      redirect: "if_required",          // only a method that must leave the page (Klarna and the like)
      confirmParams: {
        return_url: window.location.origin + "/?donated=1#donate",
        receipt_email: el.email.value.trim()
      }
    }).then(function (r) {
      if (r && r.error) { payAgain(r.error.message); return; }
      var state = r && r.paymentIntent && r.paymentIntent.status;
      if (state === "succeeded" || state === "processing") { teardown(); busy = false; show(el.done); return; }
      warn("payment ended as", state);
      payAgain();
    }, function (err) { warn("confirmPayment failed:", err); payAgain(); });
  }

  document.addEventListener("click", function (e) {
    var a = e.target.closest ? e.target.closest("a[href]") : null;
    if (!isDonate(a) || e.defaultPrevented || e.button !== 0 ||
        e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) { return; }
    e.preventDefault();
    var menu = a.closest("details");
    if (menu) { menu.open = false; }
    open(a.href);
  });

  // Back from a payment method that left the page (Klarna and the like).
  if (/[?&]donated=1(&|$)/.test(window.location.search)) {
    try {
      history.replaceState(null, "", window.location.pathname +
        window.location.search.replace(/([?&])donated=1(&|$)/, "$1").replace(/[?&]$/, "") +
        window.location.hash);
    } catch (e) { /* the address stays; harmless */ }
    build(); show(el.done); dlg.showModal();
  }
})();
