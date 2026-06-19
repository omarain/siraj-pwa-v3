// Siraj PWA v3 — Service Worker
// Version is bumped on every deploy; auto-updates clients
const VERSION = 'v3.2';
const CACHE = 'siraj-workshop-' + VERSION;
const PRE_CACHE = [
  '/',
  '/static/index.html',
  '/static/login.html',
  '/static/app.js',
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/icon-32.png'
];

// ── Install: pre-cache shell ──
self.addEventListener('install', e => {
  console.log('[SW] install', VERSION);
  self.skipWaiting(); // activate immediately, don't wait for old SW to die
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(PRE_CACHE).catch(err =>
      console.warn('[SW] pre-cache partial fail', err)
    ))
  );
});

// ── Activate: clean old caches ──
self.addEventListener('activate', e => {
  console.log('[SW] activate', VERSION);
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// ── Fetch: network-first for nav, stale-while-revalidate for assets ──
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  const isNav = e.request.mode === 'navigate';
  const isStatic = url.pathname.startsWith('/static/');

  // API calls: never cache
  if (url.pathname.startsWith('/api/')) {
    return; // let browser handle normally
  }

  if (isNav) {
    // Navigation (HTML pages): network-first, fallback to cache
    e.respondWith(
      fetch(e.request).then(response => {
        // Cache the fresh copy
        const clone = response.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return response;
      }).catch(() => caches.match(e.request))
    );
  } else if (isStatic) {
    // Static assets: cache-first (they're versioned by cache name)
    e.respondWith(
      caches.match(e.request).then(cached =>
        cached || fetch(e.request).then(response => {
          const clone = response.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
          return response;
        })
      )
    );
  }
  // Everything else: network-only
});

// ── Listen for update messages from client ──
self.addEventListener('message', e => {
  if (e.data === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
