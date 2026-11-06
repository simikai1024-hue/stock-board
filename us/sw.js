// 달의검 독립 전광판 PWA Service Worker (실시간 JSON & 올드보이 OST 오디오 직통 · v5)
const CACHE_NAME = 'moon-sword-sub-pwa-v4';

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
  if (
    url.pathname.endsWith('.json') ||
    url.pathname.endsWith('.m4a') ||
    url.pathname.endsWith('.mp3') ||
    url.origin !== self.location.origin
  ) {
    return;
  }
  event.respondWith(
    fetch(req, { cache: 'no-store' }).catch(() => caches.match(req))
  );
});
