/* Gemeinsame Helfer der Browser-Tests (Node, CommonJS).

   Gestartet von tests/test_e2e.py: TV_E2E_BASE ist die laufende App, TV_E2E_NOW ihre feste Uhrzeit.
   Playwright kommt über require.resolve (PLAYWRIGHT_MODULE, Standard "playwright", per NODE_PATH).

   Zwischen Browser und App sitzt ein kleiner Proxy. Er simuliert, was sich mit der App allein
   nicht nachstellen lässt: Netz weg (Verbindung abbrechen), hängendes Netz (nie antworten),
   ein Deploy mit neuer Build-ID (Version in /sw.js umschreiben), fehlschlagende Seitenabrufe
   (503) und alte Service Worker der Alt-App unter eigenen Pfaden. Der Proxy reicht den
   Host-Header durch, damit der CSRF-Schutz der App den Origin des Browsers als eigenen erkennt. */
"use strict";

const http = require("http");

function loadPlaywright() {
  const name = process.env.PLAYWRIGHT_MODULE || "playwright";
  return require(require.resolve(name));
}

const BASE = process.env.TV_E2E_BASE;
const NOW = process.env.TV_E2E_NOW;
if (!BASE || !NOW) {
  console.error("TV_E2E_BASE und TV_E2E_NOW fehlen – über tests/test_e2e.py starten");
  process.exit(2);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function startProxy(upstream) {
  const target = new URL(upstream);
  const state = {
    down: false,          // true: jede Verbindung bricht ab (kein Netz)
    hang: null,           // (pfad, methode) → true: nie antworten (Netz hängt, der Browser bleibt online)
    swVersion: null,      // neue Build-ID in /sw.js (simulierter Deploy)
    fail: null,           // (pfad) → true: 503 statt Weiterleitung an die App
    serve: {},            // pfad → {type, body}: vom Proxy selbst ausgeliefert (Alt-App)
    log: [],
  };
  const server = http.createServer((req, res) => {
    const path = req.url.split("?")[0];
    state.log.push(req.method + " " + req.url);
    if (state.down) { req.socket.destroy(); return; }
    if (state.hang && state.hang(path, req.method)) { req.resume(); return; }
    const own = state.serve[path];
    if (own) {
      res.writeHead(200, { "Content-Type": own.type, "Cache-Control": "no-store" });
      res.end(own.body);
      return;
    }
    if (state.fail && state.fail(path)) {
      res.writeHead(503, { "Content-Type": "text/plain", "Cache-Control": "no-store" });
      res.end("Service Unavailable");
      return;
    }
    const upstreamReq = http.request({
      host: target.hostname, port: target.port, method: req.method, path: req.url, headers: req.headers,
    }, (upRes) => {
      if (path === "/sw.js" && state.swVersion) {
        const chunks = [];
        upRes.on("data", (c) => chunks.push(c));
        upRes.on("end", () => {
          const body = Buffer.concat(chunks).toString("utf8")
            .replace(/"version": "[^"]+"/, '"version": "' + state.swVersion + '"');
          const headers = Object.assign({}, upRes.headers);
          delete headers["content-length"];
          res.writeHead(upRes.statusCode, headers);
          res.end(body);
        });
        return;
      }
      res.writeHead(upRes.statusCode, upRes.headers);
      upRes.pipe(res);
    });
    upstreamReq.on("error", () => { res.writeHead(502); res.end(); });
    req.pipe(upstreamReq);
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      resolve({
        url: "http://127.0.0.1:" + server.address().port,
        state,
        reset() { Object.assign(state, { down: false, hang: null, swVersion: null, fail: null, serve: {} }); },
        close() { return new Promise((r) => { server.closeAllConnections(); server.close(r); }); },
      });
    });
  });
}

/* Minimaler Testrahmen: jede Prüfung eine Zeile, Exit-Code 1 bei einem Fehler. */
function harness(name) {
  let failed = 0;
  return {
    check(label, ok, extra) {
      if (!ok) failed++;
      console.log((ok ? "PASS " : "FAIL ") + label + (extra !== undefined ? " " + extra : ""));
      return ok;
    },
    async run(main) {
      const timer = setTimeout(() => { console.log("FAIL Zeitlimit"); process.exit(1); }, 240000);
      try {
        await main();
      } catch (err) {
        failed++;
        console.log("FAIL " + name + ": " + (err && err.stack || err));
      }
      clearTimeout(timer);
      console.log(failed ? `${name}: ${failed} Fehler` : `${name}: ok`);
      process.exit(failed ? 1 : 0);
    },
  };
}

async function newContext(browser, options) {
  const ctx = await browser.newContext(Object.assign({ viewport: { width: 390, height: 844 }, serviceWorkers: "allow" }, options || {}));
  // Gleiche Uhrzeit wie die App (TV_CLOCK), sonst wären Meldungen „zu alt“
  await ctx.clock.setFixedTime(new Date(NOW));
  return ctx;
}

async function waitFor(page, fn, arg, { timeout = 20000, label = "Bedingung" } = {}) {
  const until = Date.now() + timeout;
  let last;
  while (Date.now() < until) {
    try { last = await page.evaluate(fn, arg); } catch (err) { last = undefined; }
    if (last) return last;
    await sleep(200);
  }
  throw new Error("Zeitlimit: " + label + " (zuletzt " + JSON.stringify(last) + ")");
}

/* Konfiguration aus /sw.js (wie scripts/preflight.py) */
async function swConfig(page) {
  return page.evaluate(async () => {
    const text = await (await fetch("/sw.js", { cache: "no-store" })).text();
    return JSON.parse(text.split("self.TV_CONFIG = ")[1].split(";\n")[0]);
  });
}

/* Cache-Name → Anzahl der Einträge */
async function cacheCounts(page) {
  return page.evaluate(async () => {
    const out = {};
    for (const name of await caches.keys()) out[name] = (await (await caches.open(name)).keys()).length;
    return out;
  });
}

/* Erster Besuch: Seite laden, warten, bis der Service Worker steuert und alles gespeichert ist */
async function installApp(page, base, path) {
  await page.goto(base + (path || "/de/"), { waitUntil: "load" });
  await waitFor(page, () => !!navigator.serviceWorker.controller, null, { label: "SW steuert die Seite" });
  const config = await swConfig(page);
  await waitFor(page, async (cfg) => {
    const pages = await caches.open(cfg.cachePrefix + "pages-" + cfg.version);
    return (await pages.keys()).length >= cfg.pages.length;
  }, config, { timeout: 60000, label: "alle Seiten gespeichert" });
  return config;
}

/* Kein Netz: Proxy bricht Verbindungen ab, der Browser meldet offline */
async function setOffline(ctx, proxy, offline) {
  proxy.state.down = offline;
  await ctx.setOffline(offline);
}

async function heading(page) {
  return (await page.textContent("h1") || "").trim();
}

module.exports = {
  BASE, NOW, loadPlaywright, startProxy, harness, newContext, waitFor, swConfig, cacheCounts,
  installApp, setOffline, heading, sleep,
};
