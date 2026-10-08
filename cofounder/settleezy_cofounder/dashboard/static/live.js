// Setz live layer, shared by the dashboard, command center and hologram/widget:
//  - live notifications (server-sent events) -> in-page card + system pop-up when the page is in the background
//  - phone push (installed app) via the service worker
//  - "refresh" events so numbers update the moment a job finishes
//  - install-as-app button
(function () {
  const S = (window.SetzLive = { handlers: [], refreshers: [], unread: 0, installPrompt: null });
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ICON = { meeting: "📅", calendar: "📅", lead: "✨", alert: "▲", partner: "🤝", voice: "🎙", job: "⚙", success: "✓", info: "•" };

  // in-page notification stack (top-right), styled to work on light and dark pages
  const css = document.createElement("style");
  css.textContent = `.sz-stack{position:fixed;top:14px;right:14px;z-index:60;display:grid;gap:8px;width:min(340px,calc(100vw - 28px));pointer-events:none}
  .sz-note{pointer-events:auto;display:grid;grid-template-columns:28px 1fr auto;gap:8px;align-items:start;padding:11px 12px;border-radius:12px;
  background:rgba(8,24,42,.96);color:#e2f6ff;border:1px solid rgba(94,232,255,.45);box-shadow:0 10px 30px rgba(0,0,0,.35);
  font:13px/1.4 Inter,system-ui,sans-serif;animation:szIn .28s ease-out;cursor:pointer}
  .sz-note b{display:block;font-size:13.5px}.sz-note span.i{font-size:16px;text-align:center;line-height:1.3}
  .sz-note button{all:unset;cursor:pointer;color:#9cc3d2;padding:0 2px}.sz-note.alert{border-color:#ff6b6b}.sz-note.lead,.sz-note.success{border-color:#4cd97b}
  .sz-note.meeting,.sz-note.calendar{border-color:#f0b43c}@keyframes szIn{from{opacity:0;transform:translateY(-8px)}to{opacity:1}}
  @media (prefers-reduced-motion:reduce){.sz-note{animation:none}}`;
  document.head.appendChild(css);
  let stack;
  function card(n) {
    stack = stack || document.body.appendChild(Object.assign(document.createElement("div"), { className: "sz-stack", role: "status" }));
    const el = document.createElement("div");
    el.className = "sz-note " + (n.kind || "info");
    el.innerHTML = `<span class="i" aria-hidden="true">${ICON[n.kind] || "•"}</span><div><b>${esc(n.title)}</b>${esc(n.body || "")}</div><button aria-label="Dismiss">✕</button>`;
    el.onclick = (e) => { if (e.target.tagName !== "BUTTON" && n.url) location.href = n.url; el.remove(); };
    stack.prepend(el);
    setTimeout(() => el.remove(), n.kind === "alert" || n.kind === "meeting" ? 20000 : 9000);
  }

  async function systemPopup(n) {
    if (!("Notification" in window) || Notification.permission !== "granted" || n.quiet) return;
    const opts = { body: n.body || "", tag: "setz-" + (n.id || Date.now()), icon: "/static/icon-192.png", data: { url: n.url || "/" } };
    try {
      const reg = await navigator.serviceWorker?.getRegistration();
      if (reg) return reg.showNotification(n.title, opts);
    } catch (_) {}
    const x = new Notification(n.title, opts); x.onclick = () => { window.focus(); if (n.url) location.href = n.url; };
  }

  function connect() {
    const es = new EventSource("/api/notify/stream");
    es.onmessage = (m) => {
      const ev = JSON.parse(m.data);
      if (ev.type === "notification") {
        S.unread++; S.handlers.forEach((h) => h(ev));
        if (document.hidden) systemPopup(ev); else card(ev);   // visible page: in-page card; elsewhere: system pop-up
      } else if (ev.type === "refresh") {
        S.refreshers.forEach((f) => f(ev.what));
      }
    };
    es.onerror = () => { es.close(); setTimeout(connect, 5000); };
  }

  S.onNotification = (h) => S.handlers.push(h);
  S.onRefresh = (f) => S.refreshers.push(f);

  // pop-ups on this device (+ push to this phone when the app is installed and the server supports it)
  S.enable = async function () {
    if (!("Notification" in window)) throw new Error("This browser can't show notifications");
    const p = await Notification.requestPermission();
    if (p !== "granted") throw new Error("Notifications were blocked: allow them in the browser's site settings");
    let push = false;
    try {
      const reg = await navigator.serviceWorker.ready;
      const { publicKey } = await (await fetch("/api/push/key")).json();
      if (publicKey && reg.pushManager) {
        const key = Uint8Array.from(atob(publicKey.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((publicKey.length + 3) % 4)), (c) => c.charCodeAt(0));
        const sub = (await reg.pushManager.getSubscription()) || (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key }));
        await fetch("/api/push/subscribe", { method: "POST", headers: { "X-SZ": "1", "Content-Type": "application/json" }, body: JSON.stringify(sub) });
        push = true;
      }
    } catch (_) {}
    await fetch("/api/notifications/test", { method: "POST", headers: { "X-SZ": "1" } });
    return { push };
  };
  S.state = () => ("Notification" in window ? Notification.permission : "unsupported");

  window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); S.installPrompt = e; document.dispatchEvent(new Event("setz-installable")); });
  S.install = async () => { if (!S.installPrompt) return false; S.installPrompt.prompt(); await S.installPrompt.userChoice; S.installPrompt = null; return true; };

  if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "127.0.0.1" || location.hostname === "localhost")) {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", connect); else connect();
})();
