// 달의검 독립 폴더 전용 PWA Service Worker (US v25 — 무캐시 최신 직통)
const CACHE_NAME = 'moon-sword-us-pwa-v25';

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.pathname.endsWith('.json') || url.pathname.endsWith('.m4a') || url.pathname.endsWith('.mp3') || url.origin !== self.location.origin) {
    return;
  }
  if (req.mode === 'navigate' || url.pathname.endsWith('.html') || url.pathname.endsWith('/')) {
    event.respondWith(
      fetch(req.url, { cache: 'no-store' }).then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(CACHE_NAME).then((c) => c.put(req.url, copy)).catch(() => {});
        }
        return res;
      }).catch(() => caches.match(req.url))
    );
    return;
  }
  event.respondWith(fetch(req).catch(() => caches.match(req)));
});
