/* QMS service worker.
 *
 * Strategy: NETWORK FIRST for the app shell, cache only as an offline fallback.
 *
 * This app runs on the store's own LAN, so a request costs a millisecond —
 * there is nothing to gain by serving stale JavaScript and a lot to lose: a
 * phone that cached an older build keeps showing the old UI after a fix, which
 * looks exactly like "the fix didn't work".
 *
 * Bump CACHE on every deploy. app.js carries a matching APP_VER, and the UI
 * compares it against the server version and offers a reload when they differ.
 */
const CACHE = "qms-v11";
const SHELL = [
  "/",
  "/index.html",
  "/css/app.css",
  "/js/app.js",
  "/manifest.json",
  "/vendor/zxing.min.js",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // 1. API + generated images: never cached.
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/setup/")) {
    e.respondWith(
      fetch(req).catch(
        () =>
          new Response(JSON.stringify({ detail: "offline" }), {
            status: 503,
            headers: { "Content-Type": "application/json" },
          })
      )
    );
    return;
  }

  // 2. Icons essentially never change — cache first is fine and instant.
  if (url.pathname.startsWith("/icons/")) {
    e.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
    return;
  }

  // 3. Everything else (html, js, css, manifest): network first, fall back to
  //    the cache so the installed app still opens with the box switched off.
  e.respondWith(
    fetch(req)
      .then((res) => {
        if (res && res.ok && res.type === "basic") {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        }
        return res;
      })
      .catch(() =>
        caches.match(req).then((hit) => hit || (req.mode === "navigate" ? caches.match("/") : undefined))
      )
  );
});
