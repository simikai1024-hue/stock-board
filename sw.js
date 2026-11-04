// 달의검 한·미 전광판 통합 PWA Service Worker (실시간 데이터 오염 0% 보장)
const CACHE_NAME = 'moon-sword-board-shell-v1';
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

  // 실시간 상태 JSON 및 외부 API는 항상 100% 네트워크 직통 (캐시 절대 금지)
  if (url.pathname.endsWith('.json') || url.origin !== self.location.origin) {
    return;
  }

  // HTML 전광판 본문도 항상 최신 서버 버전 우선(Network-First), 오프라인 순간에만 캐시 폴백
  if (req.mode === 'navigate' || url.pathname.endsWith('.html') || url.pathname.endsWith('/')) {
    event.respondWith(
      fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(CACHE_NAME).then((c) => c.put(req, copy)).catch(() => {});
        return res;
      }).catch(() => caches.match(req))
    );
    return;
  }

  // 아이콘/매니페스트 등 정적 에셋은 네트워크 우선 + 캐시 백업
  event.respondWith(
    fetch(req).catch(() => caches.match(req))
  );
});
