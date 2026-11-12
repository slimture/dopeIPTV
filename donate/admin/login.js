/* The admin page's sign-in (/admin/login): a passkey - from the button or
 * offered in the name field's autofill - or a name and password, then a
 * code when that admin has an authenticator; and taking up an invite
 * (?invite=...) with a new passkey or password. donate/admin_auth.py checks
 * it all. An external file for the CSP.
 */
(function () {
  "use strict";
  const P = window.Passkey;
  const $ = (id) => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  // Back to where the page was, but only somewhere on the admin page.
  function next() {
    const n = params.get("next") || "";
    return /^\/admin\/(?!\/)/.test(n) ? n : "/admin/";
  }
  function say(id, text) { const m = $(id); m.textContent = text || ""; m.hidden = !text; }
  function busy(el, on) { if (el) { el.disabled = on; el.classList.toggle("busy", on); } }
  function show(id) { ["signin", "code-step", "invite"].forEach((s) => { $(s).hidden = s !== id; }); }
  const done = () => location.replace(next());

  // -- a passkey ----------------------------------------------------------------
  let waiting = null;                     // the autofill request, while one waits
  function stopAutofill() { if (waiting) { waiting.abort(); waiting = null; } }
  function withPasskey(inAutofill) {
    const ctl = new AbortController();
    if (inAutofill) { waiting = ctl; }
    return P.post("/admin/auth/options", {})
      .then((opts) => P.get(opts, ctl.signal, inAutofill))
      .then((cred) => P.post("/admin/auth/passkey", { credential: cred }))
      .then(done);
  }
  function startAutofill() {
    if (!P.supported()) { return; }
    P.autofill().then((ok) => {
      if (!ok || !$("invite").hidden) { return; }
      withPasskey(true).catch((e) => {
        if (e.name === "AbortError") { return; }
        if (e.status) { say("msg", e.message); return; }     // the server said no: no asking again and again
        if (e.name !== "NotAllowedError") { say("msg", P.nice(e)); }
        // Dismissed, or none to offer: offered again the next time the name
        // field is chosen - never on a timer, which some browsers turned into
        // a request a second.
        $("pw-user").addEventListener("focus", startAutofill, { once: true });
      });
    });
  }
  $("pk-signin").addEventListener("click", () => {
    say("msg");
    if (!P.supported()) { say("msg", "This browser cannot use passkeys. Sign in with your name and password."); return; }
    stopAutofill();
    busy($("pk-signin"), true);
    withPasskey(false)
      .catch((e) => { if (e.name !== "AbortError") { say("msg", P.nice(e)); } busy($("pk-signin"), false); startAutofill(); });
  });

  // -- a name and password, then perhaps a code ------------------------------------
  let ticket = "";
  $("pw-form").addEventListener("submit", (e) => {
    e.preventDefault();
    say("msg");
    busy($("pw-go"), true);
    P.post("/admin/auth/password", { user: $("pw-user").value, password: $("pw-pass").value })
      .then((res) => {
        if (res.code) {
          ticket = res.ticket;
          stopAutofill();
          show("code-step");
          $("code").value = "";
          $("code").focus();
          return;
        }
        done();
      })
      .catch((err) => say("msg", err.message))
      .then(() => busy($("pw-go"), false));
  });
  $("code-form").addEventListener("submit", (e) => {
    e.preventDefault();
    say("code-msg");
    const btn = e.currentTarget.querySelector("button");
    busy(btn, true);
    P.post("/admin/auth/code", { ticket: ticket, code: $("code").value })
      .then(done)
      .catch((err) => { say("code-msg", err.message); busy(btn, false); });
  });
  $("code").addEventListener("input", (e) => {
    const digits = e.target.value.replace(/\D/g, "");
    if (digits.length === 6 && !e.target.dataset.sent) {
      e.target.dataset.sent = "1";
      $("code-form").requestSubmit();
      setTimeout(() => { delete e.target.dataset.sent; }, 1500);
    }
  });
  $("code-back").addEventListener("click", () => { show("signin"); say("code-msg"); startAutofill(); });

  // -- an invite ----------------------------------------------------------------------
  const token = params.get("invite");
  function invite() {
    show("invite");
    P.post("/admin/auth/invite", { token: token })
      .then((inv) => {
        $("inv-title").textContent = "Welcome" + (inv.name ? ", " + inv.name : "");
        $("inv-user").value = inv.user;
        if (!P.supported()) {
          $("inv-pk").hidden = true;
          say("inv-msg", "This browser cannot make passkeys; set a password, and add a passkey later from another device.");
        }
      })
      .catch((err) => {
        say("inv-msg", err.message);
        ["inv-pk", "inv-form"].forEach((id) => { $(id).hidden = true; });
      });
    $("inv-pk").addEventListener("click", () => {
      say("inv-msg");
      busy($("inv-pk"), true);
      P.post("/admin/auth/invite-options", { token: token })
        .then((opts) => P.create(opts))
        .then((cred) => P.post("/admin/auth/invite-passkey", { token: token, credential: cred, label: P.label() }))
        .then(() => location.replace("/admin/#account"))
        .catch((e) => { say("inv-msg", P.nice(e)); busy($("inv-pk"), false); });
    });
    $("inv-form").addEventListener("submit", (e) => {
      e.preventDefault();
      say("inv-msg");
      if ($("inv-pass").value !== $("inv-pass2").value) { say("inv-msg", "The two passwords are not the same."); return; }
      const btn = e.currentTarget.querySelector("button");
      busy(btn, true);
      P.post("/admin/auth/invite-password", { token: token, password: $("inv-pass").value })
        .then(() => location.replace("/admin/#account"))
        .catch((err) => { say("inv-msg", err.message); busy(btn, false); });
    });
  }

  if (token) { invite(); } else { startAutofill(); }
})();
