/* Passkeys (WebAuthn) for the sign-in page and the Account tab: the
 * server's options in, the passkey's answer out - JSON both ways, bytes as
 * base64url (donate/admin_auth.py reads them). Plus the one small POST
 * helper both pages use. An external file for the CSP.
 */
(function () {
  "use strict";
  function b64(buf) {
    const bytes = new Uint8Array(buf);
    let s = "";
    for (let i = 0; i < bytes.length; i++) { s += String.fromCharCode(bytes[i]); }
    return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function unb64(s) {
    const bin = atob(String(s).replace(/-/g, "+").replace(/_/g, "/"));
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) { out[i] = bin.charCodeAt(i); }
    return out.buffer;
  }
  const ids = (list) => (list || []).map((c) => ({ type: c.type || "public-key", id: unb64(c.id) }));

  function supported() {
    return !!(window.PublicKeyCredential && navigator.credentials && navigator.credentials.create);
  }
  // Passkeys offered in the name field's autofill (Safari, Chrome): the
  // sign-in page starts one such request and lets the field show them.
  function autofill() {
    try {
      return window.PublicKeyCredential && PublicKeyCredential.isConditionalMediationAvailable
        ? PublicKeyCredential.isConditionalMediationAvailable() : Promise.resolve(false);
    } catch (e) { return Promise.resolve(false); }
  }
  function create(opts) {
    const publicKey = Object.assign({}, opts, {
      challenge: unb64(opts.challenge),
      user: Object.assign({}, opts.user, { id: unb64(opts.user.id) }),
      excludeCredentials: ids(opts.excludeCredentials),
    });
    return navigator.credentials.create({ publicKey: publicKey }).then((cred) => ({
      id: cred.id, rawId: b64(cred.rawId), type: cred.type,
      response: {
        clientDataJSON: b64(cred.response.clientDataJSON),
        attestationObject: b64(cred.response.attestationObject),
        transports: cred.response.getTransports ? cred.response.getTransports() : [],
      },
    }));
  }
  function get(opts, signal, inAutofill) {
    const req = { publicKey: Object.assign({}, opts, { challenge: unb64(opts.challenge), allowCredentials: ids(opts.allowCredentials) }) };
    if (signal) { req.signal = signal; }
    if (inAutofill) { req.mediation = "conditional"; }
    return navigator.credentials.get(req).then((cred) => ({
      id: cred.id, rawId: b64(cred.rawId), type: cred.type,
      response: {
        clientDataJSON: b64(cred.response.clientDataJSON),
        authenticatorData: b64(cred.response.authenticatorData),
        signature: b64(cred.response.signature),
        userHandle: cred.response.userHandle ? b64(cred.response.userHandle) : null,
      },
    }));
  }
  // What to call a passkey made on this device, until renamed.
  function label() {
    const ua = navigator.userAgent;
    const dev = /iPhone/.test(ua) ? "iPhone" : /iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1) ? "iPad"
      : /Macintosh/.test(ua) ? "Mac" : /Android/.test(ua) ? "Android" : /Windows/.test(ua) ? "Windows"
      : /CrOS/.test(ua) ? "Chromebook" : /Linux/.test(ua) ? "Linux" : "This device";
    const br = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome"
      : /Safari\//.test(ua) ? "Safari" : "";
    return br && dev !== "iPhone" && dev !== "iPad" ? dev + " · " + br : dev;
  }
  // A browser's refusals, in words.
  function nice(e) {
    const n = e && e.name;
    if (n === "NotAllowedError") { return "Cancelled, or the device did not allow it."; }
    if (n === "InvalidStateError") { return "This device already has a passkey for this account."; }
    if (n === "SecurityError") { return "Passkeys need the page's own address (https://dopeiptv.org)."; }
    if (n === "NotSupportedError") { return "This device cannot make that kind of passkey."; }
    return (e && e.message) || "Something went wrong.";
  }
  function post(path, body) {
    return fetch(path, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
    }).then((r) => r.json().catch(() => ({})).then((j) => {
      if (!r.ok) { const err = new Error(j.error || "The service answered " + r.status + "."); err.status = r.status; throw err; }
      return j;
    }));
  }
  window.Passkey = { supported: supported, autofill: autofill, create: create, get: get, label: label, nice: nice, post: post };
})();
