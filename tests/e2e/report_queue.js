/* Meldung ohne Netz (c): Warteschlange, Nachliefern bei 'online', sichtbarer Status. */
"use strict";

const L = require("./lib");

const t = L.harness("report_queue");

t.run(async () => {
  const { chromium } = L.loadPlaywright();
  const proxy = await L.startProxy(L.BASE);
  const browser = await chromium.launch();
  try {
    const ctx = await L.newContext(browser);
    const page = await ctx.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    await L.installApp(page, proxy.url, "/de/grenze/kapikule");
    const box = '[data-status][data-cid="kapikule"][data-dir="to_tr"] .status__main';
    const label = (await page.textContent('label.bucket:has(input[value="2"]) span')).trim();
    t.check("Status vorher ohne Meldung", (await page.textContent(box)).trim() !== label);

    await L.setOffline(ctx, proxy, true);
    await page.reload({ waitUntil: "domcontentloaded" });  // aus dem Cache des Service Workers
    t.check("Offline-Banner sichtbar", await page.isVisible("[data-offline-banner]"));
    await page.click('label.seg__opt:has(input[value="to_tr"])');
    await page.click('label.bucket:has(input[value="2"])');
    await page.click('form[data-report] button[type="submit"]');
    const queuedMsg = (await page.textContent("[data-report-msg]")).trim();
    t.check("Meldung landet in der Warteschlange", queuedMsg.startsWith("Kein Netz"), JSON.stringify(queuedMsg));
    const queue = await page.evaluate(() => JSON.parse(localStorage.getItem("tv.queue") || "[]"));
    t.check("Warteschlange hat 1 Eintrag", queue.length === 1 && queue[0].cid === "kapikule", JSON.stringify(queue));

    await L.setOffline(ctx, proxy, false);  // löst 'online' im Fenster aus
    await L.waitFor(page, () => /angekommen/.test(document.querySelector("[data-report-msg]").textContent),
      null, { label: "Hinweis „angekommen“" });
    t.check("Hinweis: nachgeliefert", true);
    await L.waitFor(page, (sel) => document.querySelector(sel).textContent.trim().length > 0, box);
    t.check("Status zeigt die Meldung", (await page.textContent(box)).trim() === label, await page.textContent(box));
    const left = await page.evaluate(() => JSON.parse(localStorage.getItem("tv.queue") || "[]").length);
    t.check("Warteschlange leer", left === 0, String(left));
    const api = await (await page.request.get(proxy.url + "/api/v1/borders/kapikule")).json();
    t.check("Meldung ist auf dem Server", api.directions.to_tr.state === "live" && api.directions.to_tr.bucket === 2,
      JSON.stringify(api.directions.to_tr));

    // Server-Störung beim Nachliefern (5xx): Meldung bleibt in der Warteschlange statt verloren zu gehen
    await L.setOffline(ctx, proxy, true);
    await page.click('label.seg__opt:has(input[value="to_de"])');
    await page.click('label.bucket:has(input[value="1"])');
    await page.click('form[data-report] button[type="submit"]');
    proxy.state.fail = (path) => path.startsWith("/api/");
    await L.setOffline(ctx, proxy, false);
    await L.waitFor(page, () => navigator.onLine, null, { label: "wieder online" });
    await L.sleep(1500);
    const kept = await page.evaluate(() => JSON.parse(localStorage.getItem("tv.queue") || "[]"));
    t.check("5xx: Meldung bleibt in der Warteschlange", kept.length === 1 && kept[0].direction === "to_de", JSON.stringify(kept));
    proxy.state.fail = null;
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    // Die Warteschlange ist sofort leer, die Antwort kommt später: auf den Server warten
    let back;
    for (let i = 0; i < 50; i++) {
      back = (await (await page.request.get(proxy.url + "/api/v1/borders/kapikule")).json()).directions.to_de;
      if (back.state === "live") break;
      await L.sleep(200);
    }
    t.check("nach der Störung nachgeliefert", back.state === "live" && back.bucket === 1, JSON.stringify(back));
    const rest = await page.evaluate(() => JSON.parse(localStorage.getItem("tv.queue") || "[]").length);
    t.check("Warteschlange danach leer", rest === 0, String(rest));
    t.check("keine JS-Fehler", errors.length === 0, JSON.stringify(errors));
    await ctx.close();
  } finally {
    await browser.close();
    await proxy.close();
  }
});
