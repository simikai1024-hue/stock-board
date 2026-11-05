// 달의검 한·미 전광판 통합 PWA Service Worker (실시간 데이터·오디오 스트리밍 무간섭 보장)
const CACHE_NAME = 'moon-sword-board-shell-v2';
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

  // 실시간 JSON, OST 오디오 Range 스트리밍 및 외부 API는 항상 100% 브라우저 네트워크 직통
  if (
    url.pathname.endsWith('.json') ||
    url.pathname.endsWith('.m4a') ||
    url.pathname.endsWith('.mp3') ||
    url.origin !== self.location.origin
  ) {
    return;
  }

  if (req.mode === 'navigate' || url.pathname.endsWith('.html') || url.pathname.endsWith('/')) {
    event.respondWith(
      fetch(req, { cache: 'no-store' }).then((res) => {
        const copy = res.clone();
        caches.open(CACHE_NAME).then((c) => c.put(req, copy)).catch(() => {});
        return res;
      }).catch(() => caches.match(req))
    );
    return;
  }

  event.respondWith(
    fetch(req).catch(() => caches.match(req))
  );
});
