/* tatilvakti Service Worker.
   self.TV_CONFIG stellt der Server voran (/sw.js): Build-Version, Cache-Präfix, Assets mit Hash,
   alle Seiten in beiden Sprachen, Start- und Offline-Seiten. Keine Cache-Version von Hand:
   jede Änderung an Assets oder Daten ergibt automatisch eine neue Version.

   Grundsatz: Ein Update darf den Offline-Stand nie verlieren.
   - Der Install ist eine Transaktion: Assets, Startseiten und Offline-Seiten müssen alle im
     Cache landen, sonst scheitert er, und der bisherige Worker bleibt samt Caches aktiv.
     Der Browser versucht es bei der nächsten Update-Prüfung erneut.
   - Optionale Seiten, die nicht laden, übernimmt activate aus dem vorigen Seiten-Cache,
     bevor alte Caches gelöscht werden.
   - Die Offline-Seiten liegen zusätzlich im STATIC-Cache, der letzte Fallback ist nie leer. */
(function () {
  "use strict";

  var C = self.TV_CONFIG;
  // Eigenes Präfix: kollidiert nie mit Caches der Alt-App auf demselben Origin (z. B. „tv-…“)
  var PREFIX = C.cachePrefix || "tv2-";
  var STATIC = PREFIX + "static-" + C.version;
  var PAGES = PREFIX + "pages-" + C.version;
  var LIVE = PREFIX + "live";
  var TIMEOUT_MS = 4000;
  // Query-Parameter, die das HTML nicht ändern (Persönliches setzt app.js im Browser)
  var STRIP = ["land", "gemeldet", "fehler"];

  function values(obj) { return Object.keys(obj || {}).map(function (k) { return obj[k]; }); }

  function cacheKey(input) {
    var url = new URL(typeof input === "string" ? input : input.url, self.location.origin);
    STRIP.forEach(function (p) { url.searchParams.delete(p); });
    url.hash = "";
    return url.toString();
  }

  // Antwort verwerfen, ohne sie zu lesen: Ein ungelesener Body hielte die Verbindung belegt,
  // nach wenigen Fehlerantworten (z. B. 503 bei einem Neustart) stünden alle weiteren Abrufe.
  function discard(resp) {
    if (resp && resp.body && !resp.bodyUsed) resp.body.cancel().catch(function () {});
  }

  function fetchPage(url) {
    return fetch(url, { credentials: "omit" }).then(function (resp) {
      if (resp.ok) return resp;
      discard(resp);
      throw new Error("precache " + url + ": " + resp.status);
    });
  }

  self.addEventListener("install", function (event) {
    var offline = values(C.offline);
    var required = values(C.home).concat(offline);
    var optional = C.pages.filter(function (url) { return required.indexOf(url) === -1; });
    event.waitUntil(Promise.all([caches.open(STATIC), caches.open(PAGES)]).then(function (opened) {
      var staticCache = opened[0], pageCache = opened[1];
      return Promise.all([
        staticCache.addAll(C.assets),
        // Pflichtseiten: Fehlt eine, scheitert der Install (alter Worker und Caches bleiben)
        Promise.all(required.map(function (url) {
          return fetchPage(url).then(function (resp) {
            var jobs = [pageCache.put(cacheKey(url), resp.clone())];
            if (offline.indexOf(url) !== -1) jobs.push(staticCache.put(cacheKey(url), resp.clone()));
            return Promise.all(jobs);
          });
        })),
        // Optionale Seiten: Fehlt eine, übernimmt activate sie aus dem vorigen Cache
        Promise.all(optional.map(function (url) {
          return fetchPage(url).then(function (resp) { return pageCache.put(cacheKey(url), resp); })
            .catch(function () { /* siehe adoptMissingPages */ });
        }))
      ]);
    }).then(function () { return self.skipWaiting(); }));
  });

  // Neuester älterer Seiten-Cache zuerst (caches.keys() liefert in Anlege-Reihenfolge)
  function olderPageCaches() {
    return caches.keys().then(function (names) {
      return names.filter(function (n) { return n.indexOf(PREFIX + "pages-") === 0 && n !== PAGES; }).reverse();
    });
  }

  function firstHit(names, key) {
    if (!names.length) return Promise.resolve(null);
    return caches.open(names[0]).then(function (cache) { return cache.match(key); }).then(function (hit) {
      return hit || firstHit(names.slice(1), key);
    });
  }

  function adoptMissingPages() {
    return caches.open(PAGES).then(function (cache) {
      return cache.keys().then(function (requests) {
        var have = {};
        requests.forEach(function (r) { have[r.url] = true; });
        var missing = C.pages.map(cacheKey).filter(function (key) { return !have[key]; });
        if (!missing.length) return;
        return olderPageCaches().then(function (older) {
          return Promise.all(missing.map(function (key) {
            return firstHit(older, key).then(function (hit) { if (hit) return cache.put(key, hit); });
          }));
        });
      });
    });
  }

  self.addEventListener("activate", function (event) {
    var keep = [STATIC, PAGES, LIVE];
    event.waitUntil(adoptMissingPages().then(function () { return true; }, function () { return false; })
      .then(function (adopted) {
        return caches.keys().then(function (names) {
          // Alles, was nicht zu dieser Version gehört, wird gelöscht, auch Caches der Alt-App.
          // Nur wenn die Übernahme scheiterte (z. B. Speicher voll), bleiben ältere Seiten-Caches.
          return Promise.all(names.filter(function (n) {
            if (keep.indexOf(n) !== -1) return false;
            return adopted || n.indexOf(PREFIX + "pages-") !== 0;
          }).map(function (n) { return caches.delete(n); }));
        });
      })
      .then(function () { return self.clients.claim(); }));
  });

  function networkFirst(request, cacheName, key) {
    return caches.open(cacheName).then(function (cache) {
      var network = fetch(request).then(function (resp) {
        if (resp && resp.ok && resp.type === "basic") cache.put(key, resp.clone());
        return resp;
      });
      var fromCache = function () {
        return cache.match(key).then(function (hit) { return hit || Promise.reject(new Error("miss")); });
      };
      var slow = new Promise(function (resolve) {
        setTimeout(function () { cache.match(key).then(function (hit) { if (hit) resolve(hit); }); }, TIMEOUT_MS);
      });
      return Promise.race([network.catch(fromCache), slow]);
    });
  }

  function langOf(url) {
    var first = url.pathname.split("/")[1];
    if (C.langs.indexOf(first) !== -1) return first;
    var nav = (self.navigator && self.navigator.language || "de").slice(0, 2);
    return C.langs.indexOf(nav) !== -1 ? nav : "de";
  }

  // Nie leer: zweisprachige Minimalseite, falls selbst die Offline-Seiten fehlen
  function lastResort() {
    var links = C.langs.map(function (l) { return '<a href="' + C.home[l] + '">' + l.toUpperCase() + "</a>"; }).join(" · ");
    var body = '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">' +
      "<title>Offline</title><h1>Offline · Çevrimdışı</h1><p>" + links + "</p>";
    return new Response(body, { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } });
  }

  function pageFallback(request) {
    var url = new URL(request.url);
    var lang = langOf(url);
    var key = cacheKey(request);
    var offlineKey = new URL(C.offline[lang], self.location.origin).toString();
    return caches.open(PAGES).then(function (cache) {
      return cache.match(key)
        // auch ältere Seiten-Caches, falls activate sie behalten musste (Übernahme gescheitert)
        .then(function (hit) { return hit || caches.match(key); })
        .then(function (hit) { return hit || cache.match(key, { ignoreSearch: true }); })
        .then(function (hit) {
          if (hit) return hit;
          if (url.pathname === "/") return cache.match(new URL(C.home[lang], self.location.origin).toString());
          return null;
        })
        .then(function (hit) { return hit || caches.match(offlineKey, { cacheName: STATIC }); })
        .then(function (hit) { return hit || caches.match(offlineKey); })
        .then(function (hit) { return hit || lastResort(); });
    });
  }

  // Assets mit Hash: zuerst aus dem Cache. Offline kann eine übernommene ältere Seite einen
  // älteren Hash anfordern – dann ist die aktuelle Fassung derselben Datei das Beste, was da ist.
  function staticAsset(request) {
    return caches.match(request).then(function (hit) {
      return hit || fetch(request).then(function (resp) {
        if (resp.ok) { var copy = resp.clone(); caches.open(STATIC).then(function (c) { c.put(request, copy); }); }
        return resp;
      }).catch(function (err) {
        return caches.match(request, { ignoreSearch: true, cacheName: STATIC }).then(function (any) {
          if (any) return any;
          throw err;
        });
      });
    });
  }

  self.addEventListener("fetch", function (event) {
    var request = event.request;
    if (request.method !== "GET") return;
    var url = new URL(request.url);
    if (url.origin !== self.location.origin || url.pathname === "/sw.js") return;

    if (url.pathname.indexOf("/static/") === 0) {
      event.respondWith(staticAsset(request));
      return;
    }

    if (url.pathname.indexOf("/api/") === 0) {
      event.respondWith(networkFirst(request, LIVE, cacheKey(request)).catch(function () {
        return new Response(JSON.stringify({ error: "offline", degraded: true }), {
          status: 503, headers: { "Content-Type": "application/json" }
        });
      }));
      return;
    }

    var wantsHtml = request.mode === "navigate" || (request.headers.get("Accept") || "").indexOf("text/html") !== -1;
    if (wantsHtml) {
      event.respondWith(networkFirst(request, PAGES, cacheKey(request)).then(function (resp) {
        if (resp.ok || resp.type === "opaqueredirect" || resp.status < 500) return resp;
        discard(resp);  // Serverfehler: lieber der gespeicherte Stand
        return pageFallback(request);
      }).catch(function () { return pageFallback(request); }));
      return;
    }

    event.respondWith(networkFirst(request, PAGES, cacheKey(request)).catch(function () { return caches.match(cacheKey(request)); }));
  });
})();
