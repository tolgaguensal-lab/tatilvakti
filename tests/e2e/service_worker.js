/* Service Worker im Browser: erster Besuch und offline (a), Update bei schlechtem Netz (b). */
"use strict";

const L = require("./lib");

const t = L.harness("service_worker");
const OFFLINE_TITLE = { de: "Du bist offline", tr: "Çevrimdışısın" };
// Stichproben aus der Precache-Liste: Start, Ferien (anderer Zeitraum), Grenze, Zoll, TR-Seiten
const SAMPLES = [
  ["/de/", "Sıla yolu"],
  ["/de/ferien?zeitraum=sommer-2028", "Ferien-Radar"],
  ["/de/grenze/kapikule", "Kapıkule"],
  ["/de/zoll", "Zoll"],
  ["/tr/sinir/kapikule", "Kapıkule"],
  ["/tr/tatil", "Tatil radarı"],
];
const isHtml = (path) => /^\/(de|tr)\//.test(path) && !path.endsWith(".webmanifest");

async function expectOfflinePages(page, base, label) {
  for (const [path, title] of SAMPLES) {
    await page.goto(base + path, { waitUntil: "domcontentloaded" });
    const h1 = await L.heading(page);
    t.check(`${label}: offline ${path}`, h1.includes(title), JSON.stringify(h1));
  }
}

t.run(async () => {
  const { chromium } = L.loadPlaywright();
  const proxy = await L.startProxy(L.BASE);
  const browser = await chromium.launch();
  try {
    // ---------------------------------------------------------------- (a) erster Besuch, offline
    {
      const ctx = await L.newContext(browser);
      const page = await ctx.newPage();
      const errors = [];
      page.on("pageerror", (e) => errors.push(String(e)));
      const config = await L.installApp(page, proxy.url);
      const counts = await L.cacheCounts(page);
      const pagesCache = "tv2-pages-" + config.version, staticCache = "tv2-static-" + config.version;
      t.check("(a) Caches tragen das Präfix tv2-", Object.keys(counts).every((n) => n.startsWith("tv2-")), JSON.stringify(counts));
      t.check("(a) alle Seiten gespeichert", counts[pagesCache] === config.pages.length, `${counts[pagesCache]}/${config.pages.length}`);
      t.check("(a) Assets und Offline-Seiten im STATIC-Cache", counts[staticCache] === config.assets.length + 2, String(counts[staticCache]));

      await L.setOffline(ctx, proxy, true);
      await expectOfflinePages(page, proxy.url, "(a)");
      t.check("(a) Offline-Banner sichtbar", await page.isVisible("[data-offline-banner]"));
      const styled = await page.evaluate(() => getComputedStyle(document.querySelector(".tabbar")).position === "fixed");
      t.check("(a) CSS kommt offline aus dem Cache", styled);
      await page.goto(proxy.url + "/", { waitUntil: "domcontentloaded" });
      t.check("(a) offline / → Startseite", (await L.heading(page)).includes("Sıla yolu"));
      for (const [path, lang] of [["/de/grenze/nicht-gespeichert", "de"], ["/tr/sinir/kaydedilmedi", "tr"]]) {
        await page.goto(proxy.url + path, { waitUntil: "domcontentloaded" });
        const h1 = await L.heading(page);
        t.check(`(a) nicht gespeichert ${path} → Offline-Seite`, h1.includes(OFFLINE_TITLE[lang]), JSON.stringify(h1));
      }
      t.check("(a) keine JS-Fehler", errors.length === 0, JSON.stringify(errors));
      await L.setOffline(ctx, proxy, false);
      await ctx.close();
    }

    // ---------------------------------------------------------------- (b1) Update, alle Seitenabrufe scheitern
    {
      proxy.reset();
      const ctx = await L.newContext(browser);
      const page = await ctx.newPage();
      const config = await L.installApp(page, proxy.url);
      const before = await L.cacheCounts(page);

      proxy.state.swVersion = "e2edeploy0001";
      proxy.state.fail = isHtml;
      const outcome = await updateWorker(page);
      t.check("(b1) Install scheitert ohne Pflichtseiten", outcome === "redundant", outcome);
      const after = await L.cacheCounts(page);
      const oldPages = "tv2-pages-" + config.version;
      t.check("(b1) alter Seiten-Cache vollständig", after[oldPages] === before[oldPages] && after[oldPages] === config.pages.length,
        JSON.stringify(after));
      const active = await page.evaluate(async () => (await navigator.serviceWorker.getRegistration()).active.state);
      t.check("(b1) alter Worker bleibt aktiv", active === "activated", active);

      await L.setOffline(ctx, proxy, true);
      await expectOfflinePages(page, proxy.url, "(b1)");
      await L.setOffline(ctx, proxy, false);

      // Nächster Versuch mit gutem Netz: das Update kommt nach
      proxy.state.fail = null;
      const retry = await updateWorker(page);
      t.check("(b1) nächstes Update mit Netz gelingt", retry === "activated", retry);
      const fresh = await L.cacheCounts(page);
      t.check("(b1) danach nur noch die neuen Caches", fresh["tv2-pages-e2edeploy0001"] === config.pages.length
        && !(oldPages in fresh), JSON.stringify(fresh));
      await ctx.close();
    }

    // ---------------------------------------------------------------- (b2) Update, nur optionale Seiten scheitern
    {
      proxy.reset();
      const ctx = await L.newContext(browser);
      const page = await ctx.newPage();
      const config = await L.installApp(page, proxy.url);
      const required = [...Object.values(config.home), ...Object.values(config.offline)];
      // Caches der Alt-App auf demselben Origin (Namen unbekannt, evtl. „tv-…“)
      await page.evaluate(async () => {
        for (const name of ["oldapp-shell-v7", "tv-pages-abc123"]) {
          await (await caches.open(name)).put("/alt", new Response("alt"));
        }
      });

      proxy.state.swVersion = "e2edeploy0002";
      proxy.state.fail = (path) => isHtml(path) && !required.includes(path);
      const outcome = await updateWorker(page);
      t.check("(b2) Install gelingt mit Pflichtseiten", outcome === "activated", outcome);
      const after = await L.cacheCounts(page);
      t.check("(b2) fehlende Seiten aus dem alten Cache übernommen", after["tv2-pages-e2edeploy0002"] === config.pages.length,
        JSON.stringify(after));
      t.check("(b2) Offline-Seiten im neuen STATIC-Cache", after["tv2-static-e2edeploy0002"] === config.assets.length + 2,
        JSON.stringify(after));
      const allowed = ["tv2-live", "tv2-pages-e2edeploy0002", "tv2-static-e2edeploy0002"];
      const names = Object.keys(after);
      t.check("(b2) alte und fremde Caches gelöscht", names.every((n) => allowed.includes(n)), JSON.stringify(names));
      t.check("(b2) keine Caches der Alt-App mehr", !("oldapp-shell-v7" in after) && !("tv-pages-abc123" in after));

      await L.setOffline(ctx, proxy, true);
      await expectOfflinePages(page, proxy.url, "(b2)");
      await page.goto(proxy.url + "/de/grenze/gibts-nicht", { waitUntil: "domcontentloaded" });
      t.check("(b2) Offline-Seite weiter da", (await L.heading(page)).includes(OFFLINE_TITLE.de));
      await ctx.close();
    }
  } finally {
    await browser.close();
    await proxy.close();
  }
});

/* Update-Prüfung auslösen und warten, bis der neue Worker aktiv oder verworfen ist */
async function updateWorker(page) {
  return page.evaluate(async () => {
    const reg = await navigator.serviceWorker.getRegistration();
    try { await reg.update(); } catch (err) { return "update-error: " + err.message; }
    const worker = reg.installing || reg.waiting;
    if (!worker) return "kein Update";
    return new Promise((resolve) => {
      const done = () => { if (worker.state === "redundant" || worker.state === "activated") resolve(worker.state); };
      worker.addEventListener("statechange", done);
      done();
      setTimeout(() => resolve("Zeitlimit: " + worker.state), 30000);
    });
  });
}
