// 달의검 독립 폴더 전용 PWA Service Worker (US v27 — 무캐시 최신 직통)
const CACHE_NAME = 'moon-sword-us-pwa-v27';
self.addEventListener('install', (e) => { self.skipWaiting(); });
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.pathname.endsWith('.json') || url.pathname.endsWith('.m4a') || url.pathname.endsWith('.mp3') || url.origin !== self.location.origin) return;
  if (req.mode === 'navigate' || url.pathname.endsWith('.html') || url.pathname.endsWith('/')) {
    e.respondWith(fetch(req.url, { cache: 'no-store' }).then((res) => {
      if (res && res.ok) { const c = res.clone(); caches.open(CACHE_NAME).then((ca) => ca.put(req.url, c)).catch(() => {}); }
      return res;
    }).catch(() => caches.match(req.url)));
    return;
  }
  e.respondWith(fetch(req).catch(() => caches.match(req)));
});
