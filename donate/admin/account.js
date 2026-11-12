/* The admin page's Account tab: how you sign in (passkeys, a password, an
 * authenticator app), the other admins (an invite link for a new one,
 * removing one), and signing out here or everywhere. /admin/account and
 * /admin/account/* in donate.py, admin_auth.py behind them. Before the new
 * sign-in has its first admin (still on nginx's password) it offers only
 * that: an invite for yourself. An external file for the CSP.
 */
(function () {
  "use strict";
  const K = window.AdminKit, P = window.Passkey;
  if (!K || !P) { return; }
  const { $, mk } = K;
  const date = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" });
  const when = (t) => (t ? date.format(new Date(t * 1000)) : "never");
  let acct = null;

  function api(action, body) {
    return P.post("/admin/account/" + action, body || {}).then((res) => { if (res && res.me) { acct = res; } return res; });
  }
  function button(text, cls, onClick) {
    const b = mk("button", cls || "btn", text);
    b.type = "button";
    b.addEventListener("click", (e) => {
      b.disabled = true;
      Promise.resolve(onClick(e)).catch((err) => note(err)).then(() => { b.disabled = false; });
    });
    return b;
  }
  function field(label, input) {
    const l = mk("label", "field");
    l.append(mk("span", "", label), input);
    return l;
  }
  function input(type, auto, extra) {
    const i = mk("input");
    i.type = type;
    if (auto) { i.autocomplete = auto; }
    Object.assign(i, extra || {});
    return i;
  }
  let msgBox = null;
  function note(err, ok) {
    if (!msgBox) { return; }
    msgBox.textContent = err ? (err.message ? P.nice(err) : String(err)) : (ok || "");
    msgBox.className = "msg" + (ok && !err ? " ok" : "");
    msgBox.hidden = !msgBox.textContent;
  }
  function card(title, lede) {
    const c = mk("section", "card acct");
    const head = mk("div", "card-head");
    head.append(mk("h2", "", title));
    c.append(head);
    if (lede) { c.append(mk("p", "muted lede", lede)); }
    return c;
  }

  // -- before the first admin: still on nginx's password ---------------------------
  function drawLegacy(res) {
    const c = card("Set up the new sign-in",
      "You are signed in with the old password. Make an invite for yourself, open it on this device and create a passkey " +
      "(or set a password). From then on the new sign-in is used, and the old password can go.");
    const user = input("text", "off", { value: (res.user || "").toLowerCase().replace(/[^a-z0-9._-]/g, ""), required: true });
    const name = input("text", "name", { placeholder: "Your name, as shown" });
    const form = mk("form", "acct-form");
    form.append(field("Sign-in name", user), field("Name", name));
    const go = mk("button", "btn-primary", "Make my invite");
    go.type = "submit";
    form.append(go);
    const out = mk("div");
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      go.disabled = true;
      api("invite", { user: user.value, name: name.value })
        .then((r) => {
          const a = mk("a", "btn-primary", "Open my invite");
          a.href = r.link;
          out.replaceChildren(mk("p", "", "Ready. Open it here and create your passkey:"), a);
        })
        .catch((err) => note(err))
        .then(() => { go.disabled = false; });
    });
    c.append(form, out);
    return [c];
  }

  // -- your own sign-in ----------------------------------------------------------------
  function drawMe(me) {
    const c = card("How you sign in", "Signed in as " + me.name + " (" + me.user + ").");
    // Passkeys
    c.append(mk("h3", "subhead", "Passkeys"));
    const list = mk("ul", "acct-list");
    me.passkeys.forEach((pk) => {
      const li = mk("li");
      const text = mk("div", "a-main");
      text.append(mk("strong", "", pk.name), mk("span", "muted", "Added " + when(pk.added) + " · last used " + when(pk.used)));
      const acts = mk("div", "a-acts");
      acts.append(
        button("Rename", "link", () => {
          const label = window.prompt("A name for this passkey", pk.name);
          return label === null ? null : api("passkey-rename", { id: pk.id, label: label }).then(draw);
        }),
        button("Remove", "link danger", () => {
          if (!window.confirm("Remove the passkey “" + pk.name + "”? It also stays on the device until you delete it there.")) { return null; }
          return api("passkey-remove", { id: pk.id }).then(draw);
        }));
      li.append(text, acts);
      list.append(li);
    });
    if (!me.passkeys.length) { list.append(mk("li", "muted", "None yet.")); }
    c.append(list);
    if (P.supported()) {
      c.append(button("Add a passkey on this device", "btn-primary", () => api("passkey-options")
        .then((opts) => P.create(opts))
        .then((cred) => api("passkey-add", { credential: cred, label: P.label() }))
        .then(() => { draw(); note(null, "Passkey added."); })));
    } else {
      c.append(mk("p", "muted", "This browser cannot make passkeys."));
    }

    // Password
    c.append(mk("h3", "subhead", "Password"));
    c.append(mk("p", "muted", me.password ? "Set. Your password manager can keep it." : "None: you sign in with a passkey."));
    const pwForm = mk("form", "acct-form");
    const user = input("text", "username", { value: me.user, readOnly: true });
    user.hidden = true;                       // for the password manager, which pairs it with the new password
    const cur = input("password", "current-password");
    const nw = input("password", "new-password", { minLength: 10, required: true });
    const nw2 = input("password", "new-password", { minLength: 10, required: true });
    pwForm.append(user);
    if (me.password) { pwForm.append(field("Current password", cur)); }
    pwForm.append(field(me.password ? "New password" : "Password", nw), field("The same again", nw2));
    const pwGo = mk("button", "btn-primary", "Save the password");
    pwGo.type = "submit";
    pwForm.append(pwGo);
    pwForm.hidden = true;
    const pwOpen = button(me.password ? "Change the password" : "Set a password", "btn", () => {
      pwForm.hidden = false;
      pwOpen.hidden = true;
      (me.password ? cur : nw).focus();
    });
    pwForm.addEventListener("submit", (e) => {
      e.preventDefault();
      if (nw.value !== nw2.value) { note("The two passwords are not the same."); return; }
      pwGo.disabled = true;
      api("password", { current: cur.value, password: nw.value })
        .then(() => { draw(); note(null, "Password saved."); })
        .catch((err) => note(err)).then(() => { pwGo.disabled = false; });
    });
    c.append(pwOpen, pwForm);
    if (me.password && me.passkeys.length) {
      c.append(button("Remove the password (passkeys only)", "link danger", () => {
        if (!window.confirm("Sign in with passkeys only? The password and its authenticator codes go.")) { return null; }
        return api("password-remove").then(draw);
      }));
    }

    // Authenticator
    c.append(mk("h3", "subhead", "Authenticator app"));
    if (!me.password) {
      c.append(mk("p", "muted", "Codes go with a password; a passkey needs none."));
    } else if (me.totp) {
      c.append(mk("p", "muted", "On: after the password, a sign-in asks for a code."));
      const code = input("text", "one-time-code", { inputMode: "numeric", maxLength: 7, placeholder: "Code" });
      const row = mk("div", "acct-inline");
      row.append(code, button("Turn off", "btn", () => api("totp-remove", { code: code.value }).then(() => { draw(); note(null, "Codes turned off."); })));
      c.append(row);
    } else {
      c.append(mk("p", "muted", "Off. Turn it on, and a sign-in with the password also asks for a six-digit code from your phone."));
      const box = mk("div", "totp-setup");
      c.append(button("Turn on", "btn", () => api("totp-begin").then((t) => totpSetup(box, t))), box);
    }
    return c;
  }
  function totpSetup(box, t) {
    const qr = mk("div", "qr");
    const doc = new DOMParser().parseFromString(t.svg, "image/svg+xml");
    qr.append(document.importNode(doc.documentElement, true));
    const open = mk("a", "btn", "Open in the authenticator on this device");
    open.href = t.uri;
    const key = mk("code", "secret", t.secret.replace(/(.{4})/g, "$1 ").trim());
    const code = input("text", "one-time-code", { inputMode: "numeric", maxLength: 7, placeholder: "123456" });
    const row = mk("div", "acct-inline");
    row.append(code, button("Confirm", "btn-primary", () => api("totp-confirm", { code: code.value })
      .then(() => { draw(); note(null, "Codes are on. Keep the authenticator: without it, sign in with a passkey."); })));
    const steps = mk("ol", "totp-steps");
    [["Scan the code with your authenticator app, or open it on this device. On an iPhone, the Passwords app can keep it."],
     ["No camera? Type the key instead:"], ["Then type the six digits it shows:"]].forEach(([t2], i) => {
      const li = mk("li", "", t2);
      if (i === 0) { li.append(qr, open); }
      if (i === 1) { li.append(key); }
      if (i === 2) { li.append(row); }
      steps.append(li);
    });
    box.replaceChildren(steps);
    code.focus();
  }

  // -- the other admins ---------------------------------------------------------------------
  function drawAdmins(res) {
    const c = card("Admins", "Everyone here sees the same statistics and donations, and can invite or remove an admin.");
    const list = mk("ul", "acct-list");
    res.admins.forEach((a) => {
      const li = mk("li");
      const text = mk("div", "a-main");
      const ways = [a.passkeys ? a.passkeys + (a.passkeys === 1 ? " passkey" : " passkeys") : "", a.password ? "password" : "",
                    a.totp ? "authenticator" : ""].filter(Boolean).join(", ");
      text.append(mk("strong", "", a.name + (a.me ? " (you)" : "")),
                  mk("span", "muted", a.user + " · " + (ways || "no way in yet") + " · since " + when(a.created)));
      li.append(text);
      if (!a.me) {
        const acts = mk("div", "a-acts");
        acts.append(button("Remove", "link danger", () => {
          if (!window.confirm("Remove " + a.name + " as an admin? They are signed out at once.")) { return null; }
          return api("remove", { user: a.user }).then(draw);
        }));
        li.append(acts);
      }
      list.append(li);
    });
    res.invites.forEach((inv) => {
      const li = mk("li", "pending");
      const text = mk("div", "a-main");
      text.append(mk("strong", "", (inv.name || inv.user) + " · invited"),
                  mk("span", "muted", inv.user + " · the link works until " + when(inv.until)));
      const acts = mk("div", "a-acts");
      acts.append(button("Cancel", "link danger", () => api("invite-cancel", { user: inv.user }).then(draw)));
      li.append(text, acts);
      list.append(li);
    });
    c.append(list);

    c.append(mk("h3", "subhead", "Invite an admin"));
    const form = mk("form", "acct-form two");
    const user = input("text", "off", { required: true, placeholder: "e.g. anna", autocapitalize: "none", spellcheck: false });
    const name = input("text", "off", { placeholder: "Anna Andersson" });
    form.append(field("Sign-in name", user), field("Name, as shown", name));
    const go = mk("button", "btn-primary", "Make an invite link");
    go.type = "submit";
    form.append(go);
    const out = mk("div", "invite-out");
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      go.disabled = true;
      api("invite", { user: user.value, name: name.value })
        .then((r) => {
          const link = input("text", "off", { value: r.link, readOnly: true });
          link.className = "link-box";
          const copy = button("Copy", "btn", () => navigator.clipboard.writeText(r.link).then(() => note(null, "Copied.")));
          const row = mk("div", "acct-inline");
          row.append(link, copy);
          if (navigator.share) {
            row.append(button("Share", "btn", () => navigator.share({ title: "dopeIPTV admin", url: r.link }).catch(() => null)));
          }
          out.replaceChildren(mk("p", "", "Send this link to " + (name.value || user.value) +
            " in a private message. It works once, until " + when(r.until) + "; it is shown only now."), row);
          user.value = ""; name.value = "";
          drawList();
        })
        .catch((err) => note(err))
        .then(() => { go.disabled = false; });
    });
    c.append(form, out);
    return c;
  }

  // -- signing out --------------------------------------------------------------------------
  function drawSessions() {
    const c = card("Signing out", "This browser stays signed in for 400 days from your last visit.");
    const row = mk("div", "acct-inline");
    row.append(
      button("Sign out here", "btn", () => P.post("/admin/auth/signout").then(() => location.replace("/admin/login"))),
      button("Sign out everywhere", "btn danger", () => {
        if (!window.confirm("Sign out on all your devices, this one too?")) { return null; }
        return api("signout-all").then(() => location.replace("/admin/login"));
      }));
    c.append(row);
    return c;
  }

  // -- drawing it all ------------------------------------------------------------------------
  let listOnly = null;
  function drawList() { if (listOnly) { listOnly(); } }
  function render(res) {
    const box = $("account");
    msgBox = mk("p", "msg", "");
    msgBox.setAttribute("role", "status");
    msgBox.hidden = true;
    const parts = res.legacy ? drawLegacy(res) : [drawMe(res.me), drawAdmins(res), drawSessions()];
    box.replaceChildren(msgBox, ...parts);
    listOnly = () => fetch("/admin/account", { credentials: "same-origin", cache: "no-store" })
      .then((r) => r.json()).then((r2) => {
        acct = r2;
        if (!r2.legacy) {
          const fresh = drawAdmins(r2);
          const old = box.querySelectorAll(".card.acct")[1];
          if (old) { fresh.querySelector(".invite-out").replaceWith(old.querySelector(".invite-out")); old.replaceWith(fresh); }
        }
      });
  }
  function draw() {
    if (acct && !acct.legacy) { render(acct); return Promise.resolve(); }
    return load();
  }
  function load() {
    $("account").replaceChildren(mk("p", "muted", "Loading…"));
    return fetch("/admin/account", { credentials: "same-origin", cache: "no-store" })
      .then((r) => { if (!r.ok) { throw new Error("The service answered " + r.status + "."); } return r.json(); })
      .then((res) => { acct = res; render(res); })
      .catch((e) => $("account").replaceChildren(mk("p", "error", "Could not load the account: " + e.message)));
  }
  window.AdminAccount = { show: load };
})();
