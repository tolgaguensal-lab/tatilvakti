/* Kaltstart, Melden und Teilen (prod-1, prod-3, prod-4): Leerzustand statt grauer Chips, Auswahl des
   Übergangs auf der Startseite (zuletzt gemeldeter zuerst), Richtung aus der letzten Meldung,
   Leerzustand verschwindet bei neuer Meldung ohne Neuladen, WhatsApp direkt und Teilen-Menü nur mit
   navigator.share. Die App startet mit leerer DB. */
"use strict";

const L = require("./lib");

const t = L.harness("share_report");
const visible = (page, sel) => page.isVisible(sel);
const pickOrder = (page) => page.$$eval("[data-picker] > li", (items) => items.filter((li) => !li.hidden).map((li) => li.getAttribute("data-pick")));

t.run(async () => {
  const { chromium } = L.loadPlaywright();
  const browser = await chromium.launch();
  try {
    const ctx = await L.newContext(browser, { serviceWorkers: "block" });
    const page = await ctx.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));

    // Kaltstart: eine Zeile mit Aufruf statt acht grauer Chips, vier große Knöpfe in Datenreihenfolge
    await page.goto(L.BASE + "/de/", { waitUntil: "load" });
    t.check("Start: Leerzustand sichtbar", await visible(page, ".station .live__empty"));
    t.check("Start: keine grauen Chips", !(await visible(page, ".station .mini-borders")));
    t.check("Start: Aufruf zum Melden", (await page.textContent(".station .live__empty")).includes("Sei die erste Person"));
    t.check("Start: vier Übergänge zur Wahl", JSON.stringify(await pickOrder(page)) === '["kapikule","gradina","horgos","batrovci"]',
      JSON.stringify(await pickOrder(page)));
    t.check("Start: Hinweis „zuletzt“ verborgen", !(await visible(page, ".picker__last")));
    t.check("Start: Quellen von Behörden verlinkt", await page.$$eval(".srcline a.ext", (as) => as.some((a) => a.href.includes("police.hu"))));
    t.check("Start: kein Teilen-Menü ohne navigator.share", (await page.$$("[data-share]")).length === 0);

    // Röszke wählen: Formular nennt den Übergang, nichts vorbelegt
    await page.click('[data-pick="horgos"] a');
    await page.waitForURL(/\/de\/grenze\/horgos#melden$/);
    const title = (await page.textContent("#report-title")).trim();
    t.check("Formular nennt den Übergang", title === "Wie lange hast du in Röszke gewartet?", JSON.stringify(title));
    const box = await page.$eval("#report-title", (el) => el.getBoundingClientRect().top);
    t.check("Titel im sichtbaren Bereich", box >= 0 && box < 844, String(box));
    t.check("ohne frühere Meldung keine Richtung vorbelegt", await page.$$eval('input[name="direction"]', (els) => els.every((e) => !e.checked)));
    await page.click('label.seg__opt:has(input[value="to_de"])');
    await page.click('label.bucket:has(input[value="2"])');
    await page.click('form[data-report] button[type="submit"]');
    await L.waitFor(page, () => /Danke/.test(document.querySelector("[data-report-msg]").textContent), null, { label: "Danke" });
    const pref = await page.evaluate(() => JSON.parse(localStorage.getItem("tv.report_pref")));
    t.check("letzte Meldung gemerkt (nur Übergang und Richtung)", JSON.stringify(pref) === '{"cid":"horgos","direction":"to_de"}', JSON.stringify(pref));

    // Nächstes Formular: Richtung vorgeschlagen
    await page.goto(L.BASE + "/de/grenze/kapikule", { waitUntil: "load" });
    t.check("Richtung aus letzter Meldung vorbelegt", await page.$eval('input[name="direction"][value="to_de"]', (e) => e.checked));

    // Startseite: Röszke zuerst, Liste mit Status (Röszke live), Kapıkule als eine Zeile
    await page.goto(L.BASE + "/de/", { waitUntil: "load" });
    t.check("zuletzt gemeldeter Übergang zuerst", (await pickOrder(page))[0] === "horgos", JSON.stringify(await pickOrder(page)));
    t.check("Hinweis „zuletzt gemeldet“", await visible(page, '[data-pick="horgos"] .picker__last'));
    t.check("Liste mit Meldung sichtbar", await visible(page, ".station .mini-borders") && !(await visible(page, ".station .live__empty")));
    t.check("Röszke mit Status", await visible(page, '.mini-borders [data-status][data-cid="horgos"][data-dir="to_de"]'));
    const kapikule = await page.$eval('.mini-borders [data-status][data-cid="kapikule"]', (el) => el.closest("li").innerText);
    t.check("Kapıkule ohne Meldung: eine Zeile statt Chips", kapikule.includes("Noch keine Meldung") && !kapikule.includes("keine aktuelle"), JSON.stringify(kapikule));

    // Ein weiterer Übergang als letzter: wird zusätzlich ganz vorne gezeigt
    await page.evaluate(() => localStorage.setItem("tv.report_pref", JSON.stringify({ cid: "derekoy", direction: "to_tr" })));
    await page.reload({ waitUntil: "load" });
    const order = await pickOrder(page);
    t.check("weiterer Übergang zuerst, Hauptübergänge bleiben", order[0] === "derekoy" && order.length === 5, JSON.stringify(order));

    // Kaputter Speicher: kein Fehler, nichts umsortiert
    await page.evaluate(() => localStorage.setItem("tv.report_pref", JSON.stringify({ cid: '"]x', direction: "up" })));
    await page.reload({ waitUntil: "load" });
    t.check("kaputter Speicher: Reihenfolge wie Daten", (await pickOrder(page))[0] === "kapikule");
    await page.goto(L.BASE + "/de/grenze/gradina", { waitUntil: "load" });
    t.check("kaputter Speicher: keine Richtung vorbelegt", await page.$$eval('input[name="direction"]', (els) => els.every((e) => !e.checked)));

    // Übersicht: weitere Übergänge leer, neue Meldung kommt per Aktualisierung ohne Neuladen
    await page.goto(L.BASE + "/de/grenze", { waitUntil: "load" });
    const more = 'section[aria-labelledby="more-title"]';
    t.check("Übersicht: weitere Übergänge im Leerzustand", await page.$eval(more, (s) => s.classList.contains("is-empty")));
    const resp = await page.request.post(L.BASE + "/api/v1/borders/ipsala/reports", {
      data: { direction: "to_tr", bucket: 1 }, headers: { Origin: L.BASE },
    });
    t.check("Meldung per API", resp.status() === 201, String(resp.status()));
    await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
    await L.waitFor(page, (sel) => !document.querySelector(sel).classList.contains("is-empty"), more, { label: "Liste aktualisiert" });
    t.check("Übersicht: Liste zeigt neue Meldung", await visible(page, `${more} [data-status][data-cid="ipsala"][data-dir="to_tr"]`));
    t.check("Übersicht: Übergang ohne Meldung als Zeile", await visible(page, `${more} .crossing:has([data-cid="derekoy"]) .dirs__empty`));

    // Teilen: Leerzustand teilt einen Aufruf, WhatsApp direkt
    await page.goto(L.BASE + "/tr/sinir/pazarkule", { waitUntil: "load" });
    const wa = await page.getAttribute(".share a.btn--wa", "href");
    const text = decodeURIComponent(wa.split("?text=")[1]);
    t.check("WhatsApp direkt per wa.me", wa.startsWith("https://wa.me/?text="), wa);
    t.check("Leerzustand teilt Aufruf (TR)", text.startsWith("Pazarkule'de bekliyor musun?") && !/veri yok/.test(text), JSON.stringify(text));
    t.check("ohne Tracking-Parameter", !/utm_|fbclid|ref=/.test(text) && text.endsWith("/tr/sinir/pazarkule"), JSON.stringify(text));
    t.check("keine JS-Fehler", errors.length === 0, JSON.stringify(errors));
    await ctx.close();

    // Mit navigator.share: eigener Knopf „Teilen“, WhatsApp-Link bleibt ein Link
    const ctx2 = await L.newContext(browser, { serviceWorkers: "block" });
    await ctx2.addInitScript(() => {
      window.__shared = [];
      navigator.share = (data) => { window.__shared.push(data); return Promise.resolve(); };
    });
    const p2 = await ctx2.newPage();
    await p2.goto(L.BASE + "/de/zoll", { waitUntil: "load" });
    t.check("Zoll: Teilen-Knopf je Regel sichtbar", await p2.isVisible('#tr-handy [data-share]'));
    await p2.click('#tr-handy [data-share]');
    const shared = await p2.evaluate(() => window.__shared);
    t.check("Zoll: Regeltext und Sprungmarke geteilt", shared.length === 1 && shared[0].text.startsWith("Handy aus Deutschland (IMEI) – beachten: ")
      && shared[0].url.endsWith("/de/zoll#tr-handy"), JSON.stringify(shared));
    await ctx2.close();
  } finally {
    await browser.close();
  }
});
