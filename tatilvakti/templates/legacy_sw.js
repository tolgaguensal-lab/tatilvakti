/* tatilvakti: Kill-Switch für einen alten Service Worker (TV_LEGACY_SW_PATHS).
   Ersetzt den alten Worker beim nächsten Update: sofort aktiv, löscht alle Caches, die nicht
   zu tatilvakti v2 gehören, meldet sich ab und lädt offene Fenster neu. Kein fetch-Handler
   (Anfragen gehen direkt ins Netz), kein push-Handler (alte Push-Abos enden mit der Abmeldung). */
(function () {
  "use strict";
  var KEEP = {{ cache_prefix | tojson }};

  // Ein Fehler beim Aufräumen darf die Abmeldung nicht verhindern
  function settle(promise) { return promise.catch(function () {}); }

  self.addEventListener("install", function () {
    self.skipWaiting();
  });

  self.addEventListener("activate", function (event) {
    event.waitUntil(settle(caches.keys().then(function (names) {
      return Promise.all(names.filter(function (n) { return n.indexOf(KEEP) !== 0; })
        .map(function (n) { return caches.delete(n); }));
    }))
      .then(function () { return settle(self.clients.claim()); })
      .then(function () { return self.registration.unregister(); })
      .then(function () { return self.clients.matchAll({ type: "window" }); })
      .then(function (clients) {
        return Promise.all(clients.map(function (client) {
          return client.navigate(client.url).catch(function () { /* Fenster schon geschlossen */ });
        }));
      }));
  });
})();
