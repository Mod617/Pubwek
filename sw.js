// Service Worker minimal pour PubWek
// Objectif : rendre l'app installable (PWA). Pas de cache offline complexe pour l'instant,
// on garde ça simple. On pourra ajouter du cache plus tard si besoin.

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
