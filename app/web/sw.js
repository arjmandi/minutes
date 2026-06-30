// minutes — service worker (root scope). Precaches the app shell for offline launch + fast loads.
// CRITICAL: never intercept /ingest (the capture WebSocket) or /api/* (REST + live WS) — those must
// always hit the network. Navigations are network-first (fresh app), assets cache-first.
// Bump CACHE on every deploy so clients pick up new assets on next launch.
const CACHE = "minutes-shell-v1";
const SHELL = [
  "/app",
  "/assets/app.js",
  "/assets/fs-styles.css",
  "/assets/app-kit.css",
  "/assets/mobile.css",
  "/assets/dual.css",
  "/assets/recordflow.css",
  "/assets/pcm-worklet.js",
  "/assets/icons/icon-192.png",
  "/assets/icons/icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // Never touch capture/API traffic or non-GET — straight to the network.
  if (req.method !== "GET") return;
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname === "/ingest" || url.pathname.startsWith("/ingest")) return;
  if (url.pathname === "/sw.js") return;

  // Navigations (/, /app, /shared/*): network-first so the app stays fresh; fall back to the
  // cached shell offline.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(() => caches.match("/app").then((r) => r || caches.match(req)))
    );
    return;
  }

  // Static assets: cache-first, then network (and cache the result).
  if (url.pathname.startsWith("/assets/")) {
    event.respondWith(
      caches.match(req).then((hit) =>
        hit || fetch(req).then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
          return res;
        })
      )
    );
  }
});
