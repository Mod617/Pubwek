// Service Worker pour PubWek
// Objectif : rendre l'app installable (PWA) + afficher les notifications
// push reçues même quand l'app n'est pas ouverte.
const CACHE_NAME = "pubwek-v1";

self.addEventListener("install", (event) => {
  // Active immédiatement la nouvelle version sans attendre la fermeture des anciens onglets
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

// Handler minimal requis par les navigateurs pour considérer l'app comme installable.
// Ici on laisse simplement passer toutes les requêtes vers le réseau normalement.
self.addEventListener("fetch", (event) => {
  event.respondWith(fetch(event.request));
});

// =========================================================================
// 🔔 RÉCEPTION D'UNE NOTIFICATION PUSH
// Déclenché même si aucun onglet PubWek n'est ouvert : c'est le cœur du
// fonctionnement "façon WhatsApp/TikTok" demandé.
// =========================================================================
self.addEventListener("push", (event) => {
  let data = { title: "Pubwek", body: "Vous avez une nouvelle notification.", url: "/" };

  if (event.data) {
    try {
      data = event.data.json();
    } catch (e) {
      data.body = event.data.text();
    }
  }

  const options = {
    body: data.body,
    icon: "/static/icons/icon-192x192.png",
    badge: "/static/icons/icon-192x192.png",
    data: { url: data.url || "/" },
  };

  event.waitUntil(self.registration.showNotification(data.title || "Pubwek", options));
});

// =========================================================================
// 🔔 CLIC SUR LA NOTIFICATION
// Ramène l'utilisateur vers le lien pertinent (ex: mes-campagnes, dashboard
// partageur...), en réutilisant un onglet déjà ouvert si possible.
// =========================================================================
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientsList) => {
      for (const client of clientsList) {
        if (client.url.includes(url) && "focus" in client) {
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(url);
      }
    })
  );
});
