const CACHE_NAME = "osd-offline-v1"; // bump when offline.html changes
const OFFLINE_URL = "/static/offline.html";

self.addEventListener("install", event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.add(OFFLINE_URL))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener("activate", event => {
    event.waitUntil(
        caches.keys()
            .then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))))
            .then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", event => {
    // Leitplanke: OpenSecDash is a live security dashboard - NEVER cache pages,
    // API responses or /static/ assets. We only handle top-level navigations,
    // and only to fall back to the offline page when the network is gone.
    // Everything else (HTMX fragments, /static/ with its own ?v= busting, the
    // /ws/events WebSocket) is left completely untouched by returning early.
    if (event.request.mode !== "navigate") {
        return;
    }
    event.respondWith(
        fetch(event.request).catch(() => caches.match(OFFLINE_URL))
    );
});
