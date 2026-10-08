// Setz service worker: installable app, offline shell, push notifications (phone, even when the app is closed).
const SHELL = "setz-shell-v1";
const PAGES = ["/", "/command", "/hologram", "/static/hologram.js", "/static/live.js", "/static/icon-192.png"];

self.addEventListener("install", (e) => { e.waitUntil(caches.open(SHELL).then((c) => c.addAll(PAGES)).catch(() => {})); self.skipWaiting(); });
self.addEventListener("activate", (e) => { e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== SHELL).map((k) => caches.delete(k))))); self.clients.claim(); });

// pages: network first (always live), cached copy only when offline; API calls are never cached
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin || u.pathname.startsWith("/api/")) return;
  e.respondWith(fetch(e.request).then((r) => { if (r.ok && PAGES.includes(u.pathname)) { const c = r.clone(); caches.open(SHELL).then((s) => s.put(e.request, c)); } return r; })
    .catch(() => caches.match(e.request).then((r) => r || caches.match("/"))));
});

self.addEventListener("push", (e) => {
  let d = {}; try { d = e.data.json(); } catch (_) { d = { title: "Setz", body: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(d.title || "Setz", { body: d.body || "", tag: d.tag, renotify: true,
    icon: "/static/icon-192.png", badge: "/static/icon-192.png", data: { url: d.url || "/" } }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((ws) => {
    for (const w of ws) { if ("focus" in w) { w.navigate(url).catch(() => {}); return w.focus(); } }
    return self.clients.openWindow(url);
  }));
});
