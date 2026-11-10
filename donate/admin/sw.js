/* The admin page's service worker: shows a push from donate.py (a new
 * donation, or a test) as a notification, and a click on it opens the
 * admin page's donations. Nothing is cached. */
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = { body: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(d.title || "dopeIPTV", {
    body: d.body || "",
    icon: "/apple-touch-icon.png",
    badge: "/favicon.png?v=2",
    tag: d.tag || undefined,
    data: { url: d.url || "/admin/#donations" }
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || "/admin/", self.location.origin).href;
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    const open = list.find((c) => c.url.startsWith(self.location.origin + "/admin/"));
    if (open) { return open.focus().then((c) => (c && c.navigate ? c.navigate(url) : c)); }
    return self.clients.openWindow(url);
  }));
});
