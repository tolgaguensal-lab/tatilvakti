/* tatilvakti service worker.
   self.TV_CONFIG is prepended by the server (/sw.js): build version, hashed assets,
   all content pages in both languages and the offline pages. No manual cache bumping:
   every asset or data change produces a new version automatically. */
(function () {
  "use strict";

  var C = self.TV_CONFIG;
  var STATIC = "tv-static-" + C.version;
  var PAGES = "tv-pages-" + C.version;
  var LIVE = "tv-live";
  var TIMEOUT_MS = 4000;
  // Query params that do not change the HTML (personal state is applied client-side)
  var STRIP = ["land", "gemeldet", "fehler"];

  function cacheKey(input) {
    var url = new URL(typeof input === "string" ? input : input.url, self.location.origin);
    STRIP.forEach(function (p) { url.searchParams.delete(p); });
    url.hash = "";
    return url.toString();
  }

  self.addEventListener("install", function (event) {
    event.waitUntil(Promise.all([
      caches.open(STATIC).then(function (cache) { return cache.addAll(C.assets); }),
      caches.open(PAGES).then(function (cache) {
        return Promise.all(C.pages.map(function (url) {
          return fetch(url, { credentials: "omit" }).then(function (resp) {
            if (resp.ok) return cache.put(cacheKey(url), resp);
          }).catch(function () { /* one missing page must not block the install */ });
        }));
      })
    ]).then(function () { return self.skipWaiting(); }));
  });

  self.addEventListener("activate", function (event) {
    var keep = [STATIC, PAGES, LIVE];
    event.waitUntil(caches.keys().then(function (names) {
      return Promise.all(names.filter(function (n) { return n.indexOf("tv-") === 0 && keep.indexOf(n) === -1; })
        .map(function (n) { return caches.delete(n); }));
    }).then(function () { return self.clients.claim(); }));
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

  function pageFallback(request) {
    var url = new URL(request.url);
    return caches.open(PAGES).then(function (cache) {
      return cache.match(cacheKey(request))
        .then(function (hit) { return hit || cache.match(cacheKey(request), { ignoreSearch: true }); })
        .then(function (hit) {
          if (hit) return hit;
          if (url.pathname === "/") return cache.match(new URL("/" + langOf(url) + "/", self.location.origin).toString());
          return null;
        })
        .then(function (hit) { return hit || cache.match(new URL(C.offline[langOf(url)], self.location.origin).toString()); })
        .then(function (hit) {
          return hit || new Response("Offline", { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } });
        });
    });
  }

  self.addEventListener("fetch", function (event) {
    var request = event.request;
    if (request.method !== "GET") return;
    var url = new URL(request.url);
    if (url.origin !== self.location.origin || url.pathname === "/sw.js") return;

    if (url.pathname.indexOf("/static/") === 0) {
      event.respondWith(caches.match(request).then(function (hit) {
        return hit || fetch(request).then(function (resp) {
          if (resp.ok) { var copy = resp.clone(); caches.open(STATIC).then(function (c) { c.put(request, copy); }); }
          return resp;
        });
      }));
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
        return resp && (resp.ok || resp.type === "opaqueredirect") ? resp : pageFallback(request).then(function (fb) { return resp.status >= 500 ? fb : resp; });
      }).catch(function () { return pageFallback(request); }));
      return;
    }

    event.respondWith(networkFirst(request, PAGES, cacheKey(request)).catch(function () { return caches.match(cacheKey(request)); }));
  });
})();
