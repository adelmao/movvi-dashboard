/* ============================================================
   TVDE Fleet — Service Worker
   Adelmo Top Unipessoal Lda
   Coloca em: C:\TVDE\dashboard\static\sw.js
   ============================================================ */

const CACHE_NAME = 'adelmo-fleet-v3';
const OFFLINE_URL = '/offline';

// Ficheiros a fazer cache imediatamente na instalação
const PRE_CACHE = [
  '/',
  '/dashboard',
  '/static/style.css',
  '/manifest.json',
];

// ── Install ────────────────────────────────────────────────
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(PRE_CACHE).catch(() => {
        // Se algum ficheiro falhar, continua na mesma
        console.log('[SW] Alguns ficheiros não foram cacheados, a continuar...');
      });
    })
  );
  self.skipWaiting();
});

// ── Activate ───────────────────────────────────────────────
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

// ── Fetch ──────────────────────────────────────────────────
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // API calls — Network First (dados sempre frescos)
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          // Cacheia a resposta da API
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
          return response;
        })
        .catch(() => {
          // Offline: devolve cache se existir
          return caches.match(request).then(
            (cached) =>
              cached ||
              new Response(
                JSON.stringify({ error: 'offline', cached: false }),
                { headers: { 'Content-Type': 'application/json' } }
              )
          );
        })
    );
    return;
  }

  // Navegação (pages) — Network First com fallback
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() => caches.match('/dashboard') || caches.match('/'))
    );
    return;
  }

  // Assets estáticos — Cache First
  event.respondWith(
    caches.match(request).then(
      (cached) => cached || fetch(request).then((response) => {
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
        }
        return response;
      })
    )
  );
});

// ── Push Notifications (futuro) ────────────────────────────
self.addEventListener('push', (event) => {
  if (!event.data) return;
  const data = event.data.json();
  self.registration.showNotification(data.title || 'Adelmo Fleet', {
    body: data.body || '',
    icon: '/static/icon-192.png',
    badge: '/static/icon-192.png',
    tag: data.tag || 'fleet-alert',
    data: { url: data.url || '/dashboard' },
  });
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(
    clients.openWindow(event.notification.data.url || '/dashboard')
  );
});
