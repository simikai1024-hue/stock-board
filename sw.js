// 달의검 한·미 전광판 통합 PWA Service Worker v22 (HTML 항시 최신 네트워크 직통 보장)
const CACHE_NAME = 'stock-board-skyblue-v18';
const SHELL_ASSETS = [
  './icons/kr-192.png',
  './icons/kr-512.png',
  './icons/us-192.png',
  './icons/us-512.png'
];

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS)).catch(() => {})
  );
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

  // 중요: navigate 요청에 fetch(req, init)를 넘기면 크롬/안드로이드 PWA에서 TypeError가 나며 구캐시로 빠짐 -> 반드시 req.url 문자열로 fetch!
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

  event.respondWith(
    fetch(req).catch(() => caches.match(req))
  );
});
