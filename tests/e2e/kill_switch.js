/* Kill-Switch (e): alte Service Worker unter TV_LEGACY_SW_PATHS werden abgemeldet, ihre Caches
   gelöscht, Caches von v2 (tv2-*) bleiben. Die App läuft mit TV_LEGACY_SW_PATHS aus TV_E2E_LEGACY_SW. */
"use strict";

const L = require("./lib");

const t = L.harness("kill_switch");
const [ROOT_SW, APP_SW] = (process.env.TV_E2E_LEGACY_SW || "").split(",");
const OLD_PAGE = "<!doctype html><title>OLD APP</title><h1>OLD APP</h1>";

/* Worker der Alt-App: cache-first für Navigationen, eigene Caches, push-Handler */
function oldWorker(cacheNames) {
  return `
const PAGE = ${JSON.stringify(OLD_PAGE)};
self.addEventListener("install", (e) => e.waitUntil(Promise.all(${JSON.stringify(cacheNames)}.map((n) =>
  caches.open(n).then((c) => c.put("/__old-shell", new Response(PAGE, { headers: { "Content-Type": "text/html" } })))))
  .then(() => self.skipWaiting())));
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("push", () => {});
self.addEventListener("fetch", (e) => {
  if (e.request.mode === "navigate") e.respondWith(caches.match("/__old-shell").then((r) => r || fetch(e.request)));
});`;
}

function registerPage(script) {
  return OLD_PAGE + `<script>navigator.serviceWorker.register(${JSON.stringify(script)});</script>`;
}

async function registrations(page) {
  return page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).map((r) => {
    const worker = r.active || r.waiting || r.installing;
    return (worker ? new URL(worker.scriptURL).pathname : "?") + " @ " + new URL(r.scope).pathname;
  }));
}

async function cacheNames(page) {
  return page.evaluate(async () => (await caches.keys()).sort());
}

/* Update-Prüfung wie der Browser sie regelmäßig macht */
async function checkForUpdate(page, scope) {
  return page.evaluate(async (s) => {
    const reg = await navigator.serviceWorker.getRegistration(s);
    try { await reg.update(); return "ok"; } catch (err) { return "Fehler: " + err.message; }
  }, scope);
}

t.run(async () => {
  if (!ROOT_SW || !APP_SW) throw new Error("TV_E2E_LEGACY_SW braucht zwei Pfade");
  const { chromium } = L.loadPlaywright();
  const proxy = await L.startProxy(L.BASE);
  const browser = await chromium.launch();
  try {
    // ---------------------------------------------------------------- (e1) alter Worker mit Scope /, cache-first
    {
      const ctx = await L.newContext(browser);
      const page = await ctx.newPage();
      proxy.state.serve = {
        [ROOT_SW]: { type: "application/javascript", body: oldWorker(["oldapp-shell-v7", "tv-pages-legacy"]) },
        "/__legacy": { type: "text/html", body: registerPage(ROOT_SW) },
      };
      await page.goto(proxy.url + "/__legacy", { waitUntil: "load" });
      await L.waitFor(page, () => !!navigator.serviceWorker.controller, null, { label: "alter Worker steuert" });
      await page.evaluate(async () => (await caches.open("tv2-keep-e2e")).put("/x", new Response("v2")));
      await page.goto(proxy.url + "/de/", { waitUntil: "load" });
      t.check("(e1) vorher: alter Worker liefert die Alt-App aus", (await page.title()) === "OLD APP");

      proxy.reset();  // Umschalten: ab jetzt antwortet v2, unter dem alten Pfad der Kill-Switch
      const killSwitch = await (await page.request.get(proxy.url + ROOT_SW)).text();
      t.check("(e1) v2 liefert unter dem alten Pfad den Kill-Switch", killSwitch.includes("registration.unregister()"));
      const navigated = page.waitForNavigation({ timeout: 20000 });
      t.check("(e1) Update-Prüfung", (await checkForUpdate(page, "/")) === "ok");
      await navigated;
      await L.waitFor(page, () => document.title !== "OLD APP" && !!document.querySelector("h1"), null, { label: "neu geladen" });
      t.check("(e1) Fenster neu geladen, jetzt v2", (await L.heading(page)).includes("Sıla yolu"), await page.title());
      const names = await cacheNames(page);
      t.check("(e1) Caches der Alt-App gelöscht", !names.includes("oldapp-shell-v7") && !names.includes("tv-pages-legacy"), JSON.stringify(names));
      t.check("(e1) tv2-Caches bleiben", names.includes("tv2-keep-e2e"), JSON.stringify(names));
      await L.waitFor(page, async () => {
        const regs = await navigator.serviceWorker.getRegistrations();
        return regs.length === 1 && regs[0].active && regs[0].active.scriptURL.endsWith("/sw.js");
      }, null, { label: "nur noch /sw.js registriert", timeout: 30000 });
      t.check("(e1) nur noch der Worker von v2 registriert", true, JSON.stringify(await registrations(page)));
      await ctx.close();
    }

    // ---------------------------------------------------------------- (e2) v2 installiert, alter Worker mit engerem Scope
    {
      proxy.reset();
      const ctx = await L.newContext(browser);
      const page = await ctx.newPage();
      const config = await L.installApp(page, proxy.url);
      const v2Caches = await L.cacheCounts(page);
      const scope = APP_SW.slice(0, APP_SW.lastIndexOf("/") + 1);
      proxy.state.serve = {
        [APP_SW]: { type: "application/javascript", body: oldWorker(["oldapp-scoped"]) },
        [scope + "__legacy"]: { type: "text/html", body: registerPage(APP_SW) },
      };
      await page.goto(proxy.url + scope + "__legacy", { waitUntil: "load" });
      await L.waitFor(page, async (s) => (await navigator.serviceWorker.getRegistrations())
        .some((r) => new URL(r.scope).pathname === s && r.active && r.active.state === "activated"), scope,
      { label: "alter Worker aktiv" });
      const before = await registrations(page);
      t.check("(e2) vorher zwei Registrierungen", before.length === 2, JSON.stringify(before));

      proxy.reset();
      t.check("(e2) Update-Prüfung", (await checkForUpdate(page, scope)) === "ok");
      await L.waitFor(page, async () => (await navigator.serviceWorker.getRegistrations()).length === 1, null,
        { label: "alter Worker abgemeldet" });
      const regs = await registrations(page);
      t.check("(e2) nur noch /sw.js @ /", JSON.stringify(regs) === JSON.stringify(["/sw.js @ /"]), JSON.stringify(regs));
      const after = await L.cacheCounts(page);
      t.check("(e2) Cache der Alt-App gelöscht", !("oldapp-scoped" in after), JSON.stringify(after));
      const complete = await page.evaluate(async (cfg) => {
        const pages = await caches.open(cfg.cachePrefix + "pages-" + cfg.version);
        const keys = new Set((await pages.keys()).map((r) => new URL(r.url).pathname + new URL(r.url).search));
        return cfg.pages.every((p) => keys.has(p));
      }, config);
      t.check("(e2) Offline-Stand von v2 unberührt", complete
        && after["tv2-static-" + config.version] === v2Caches["tv2-static-" + config.version], JSON.stringify(after));
      await ctx.close();
    }
  } finally {
    await browser.close();
    await proxy.close();
  }
});
