// FamPilot Service Worker
const VERSION = 'fampilot-v2';

self.addEventListener('install', e => {
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(clients.claim());
});

// Push notification handler
self.addEventListener('push', e => {
  let payload = { title: 'FamPilot', body: 'You have a new update' };
  if (e.data) {
    try {
      payload = e.data.json();
    } catch (err) {
      payload.body = e.data.text();
    }
  }

  const options = {
    body: payload.body || '',
    icon: '/static/icon.svg',
    badge: '/static/icon.svg',
    tag: payload.tag || 'fampilot',
    data: { url: payload.url || '/' },
    requireInteraction: false,
  };

  e.waitUntil(
    self.registration.showNotification(payload.title || 'FamPilot', options)
  );
});

// Click handler
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/';

  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windowClients => {
      // Try to focus an existing window
      for (const client of windowClients) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          client.navigate(url);
          return client.focus();
        }
      }
      // Otherwise open a new one
      if (clients.openWindow) {
        return clients.openWindow(url);
      }
    })
  );
});

// Re-subscribe if subscription expires
self.addEventListener('pushsubscriptionchange', e => {
  // Could re-subscribe here, but most cases need user interaction
  console.log('Push subscription expired');
});
