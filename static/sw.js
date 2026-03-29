// FamPilot Service Worker
// Minimal SW — enables PWA installability and share target
const VERSION = 'fampilot-v1';

self.addEventListener('install', e => {
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(clients.claim());
});

// Pass all requests through — no offline caching needed yet
self.addEventListener('fetch', e => {
  e.respondWith(fetch(e.request));
});
