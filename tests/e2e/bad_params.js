/* Präparierte Links und kaputter Gerätespeicher (sec-8): ?land= und tv.state landen nie ungeprüft in
   einem Selektor. Ein kaputter Wert darf app.js nicht lahmlegen: Suche, Richtungsschalter und
   Service Worker müssen trotzdem laufen. */
"use strict";

const L = require("./lib");

const t = L.harness("bad_params");
const BAD = ['"]', "x\"] , *", "\\", "BY\"]", "<b>", "%"];

t.run(async () => {
  const { chromium } = L.loadPlaywright();
  const browser = await chromium.launch();
  try {
    // Präparierter Link, wie im Audit: /de/zoll?land="]
    {
      const ctx = await L.newContext(browser);
      const page = await ctx.newPage();
      const errors = [];
      page.on("pageerror", (e) => errors.push(String(e)));
      await page.goto(L.BASE + "/de/zoll?land=%22%5D", { waitUntil: "load" });
      t.check("Zoll ?land=\"]: Suche sichtbar", await page.isVisible("[data-customs-search]"));
      await L.waitFor(page, async () => (await navigator.serviceWorker.getRegistrations()).length > 0, null,
        { label: "Service Worker registriert" });
      t.check("Zoll ?land=\"]: Service Worker registriert", true);
      await page.fill("[data-customs-search]", "gold");
      const shown = await page.$$eval("[data-rule]", (rules) => rules.filter((r) => !r.hidden).length);
      t.check("Zoll ?land=\"]: Suche filtert", shown > 0 && shown < 10, String(shown));
      t.check("Zoll ?land=\"]: keine JS-Fehler", errors.length === 0, JSON.stringify(errors));
      await ctx.close();
    }

    // Weitere Seiten und Werte: Ferien (Länderkarte), Grenzen (Richtungsschalter), Grenzübergang
    {
      const ctx = await L.newContext(browser, { serviceWorkers: "block" });
      const page = await ctx.newPage();
      const errors = [];
      page.on("pageerror", (e) => errors.push(String(e)));
      for (const bad of BAD) {
        const q = "?land=" + encodeURIComponent(bad);
        await page.goto(L.BASE + "/de/ferien" + q, { waitUntil: "load" });
        const visible = await page.$$eval("[data-per-state]", (els) => els.filter((e) => !e.hidden).map((e) => e.getAttribute("data-per-state")));
        t.check(`Ferien ${q}: nur „Bundesland wählen“`, visible.join() === "none", JSON.stringify(visible));
        await page.goto(L.BASE + "/de/grenze" + q, { waitUntil: "load" });
        t.check(`Grenzen ${q}: Richtungsschalter da`, await page.isVisible("[data-dir-switch]"));
        await page.goto(L.BASE + "/de/" + q, { waitUntil: "load" });
        const link = await page.getAttribute("a[data-state-link]", "href");
        t.check(`Start ${q}: Ferien-Link ohne Bundesland`, link === "/de/ferien", link);
      }
      // Meldung über JS (fetch statt Formular-Post mit Weiterleitung), trotz kaputtem Link
      await page.goto(L.BASE + "/tr/sinir/kapikule?land=%22%5D", { waitUntil: "load" });
      await page.click('label.seg__opt:has(input[value="to_tr"])');
      await page.click('label.bucket:has(input[value="2"])');
      await page.click('form[data-report] button[type="submit"]');
      await L.waitFor(page, () => document.querySelector("[data-report-msg]").textContent.trim().length > 0, null,
        { label: "Rückmeldung zur Meldung" });
      t.check("Kapıkule ?land=\"]: Meldung per JS gesendet", !page.url().includes("gemeldet")
        && (await page.getAttribute("[data-report-msg]", "class")).includes("flash--ok"), page.url());
      // Kleinbuchstaben sind kein Angriff: ?land=nw zeigt NRW (wie der Server)
      await page.goto(L.BASE + "/de/ferien?land=nw", { waitUntil: "load" });
      const nw = await page.$$eval("[data-per-state]", (els) => els.filter((e) => !e.hidden).map((e) => e.getAttribute("data-per-state")));
      t.check("Ferien ?land=nw: NRW", nw.join() === "NW", JSON.stringify(nw));
      t.check("keine JS-Fehler bei kaputten Links", errors.length === 0, JSON.stringify(errors));
      await ctx.close();
    }

    // Kaputter Wert im Gerätespeicher (ältere Fassung, Fremdeingriff)
    {
      const ctx = await L.newContext(browser, { serviceWorkers: "block" });
      await ctx.addInitScript(() => { try { localStorage.setItem("tv.state", JSON.stringify('"]')); } catch (e) { /* egal */ } });
      const page = await ctx.newPage();
      const errors = [];
      page.on("pageerror", (e) => errors.push(String(e)));
      await page.goto(L.BASE + "/de/ferien", { waitUntil: "load" });
      const visible = await page.$$eval("[data-per-state]", (els) => els.filter((e) => !e.hidden).map((e) => e.getAttribute("data-per-state")));
      t.check("Speicher \"]: Ferien zeigen „Bundesland wählen“", visible.join() === "none", JSON.stringify(visible));
      await page.goto(L.BASE + "/de/zoll", { waitUntil: "load" });
      t.check("Speicher \"]: Suche sichtbar", await page.isVisible("[data-customs-search]"));
      t.check("Speicher \"]: keine JS-Fehler", errors.length === 0, JSON.stringify(errors));
      await ctx.close();
    }
  } finally {
    await browser.close();
  }
});
