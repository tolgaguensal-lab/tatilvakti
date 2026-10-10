/* Meldung ohne Netz (c) und bei hängendem Netz: Warteschlange, Zeitlimit, Nachliefern bei 'online',
   sichtbarer Status, ehrlicher Hinweis bei verworfenen Meldungen. */
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

    // Netz hängt (Grenze mit schwachem Empfang): Der Browser meldet online, der POST bekommt nie eine
    // Antwort. Sofort sichtbar „wird gesendet“, nach dem Zeitlimit (8 s) in die Warteschlange statt verloren.
    const queued = () => page.evaluate(() => JSON.parse(localStorage.getItem("tv.queue") || "[]"));
    const msg = () => page.$eval("[data-report-msg]", (el) => ({ text: el.textContent.trim(), cls: el.className }));
    proxy.state.hang = (path, method) => method === "POST" && path.startsWith("/api/");
    await page.goto(proxy.url + "/de/grenze/gradina", { waitUntil: "load" });
    await page.click('label.seg__opt:has(input[value="to_tr"])');
    await page.click('label.bucket:has(input[value="3"])');
    const sentAt = Date.now();
    await page.click('form[data-report] button[type="submit"]');
    const sending = await msg();
    t.check("hängt: sofort „Wird gesendet …“", sending.text === "Wird gesendet …" && sending.cls.includes("flash--sending"),
      JSON.stringify(sending));
    const btn = await page.$eval('form[data-report] button[type="submit"]', (b) => [b.getAttribute("aria-busy"), b.getAttribute("aria-disabled"),
      getComputedStyle(b).opacity, document.activeElement === b]);
    t.check("hängt: Knopf busy, gesperrt, sichtbar blass, behält den Fokus", btn[0] === "true" && btn[1] === "true" && btn[2] === "0.6" && btn[3],
      JSON.stringify(btn));
    await L.waitFor(page, () => document.querySelector("[data-report-msg]").classList.contains("flash--queued"), null,
      { label: "Warteschlangen-Hinweis nach dem Zeitlimit", timeout: 20000 });
    const waited = Date.now() - sentAt;
    t.check("hängt: Zeitlimit greift nach etwa 8 s", waited >= 7500 && waited < 15000, String(waited));
    const slow = await msg();
    t.check("hängt: Hinweis „zu langsam, gespeichert“", slow.text.startsWith("Das Netz ist gerade zu langsam"), JSON.stringify(slow));
    const freed = await page.$eval('form[data-report] button[type="submit"]', (b) => [b.getAttribute("aria-busy"), b.getAttribute("aria-disabled")]);
    t.check("hängt: Knopf wieder frei", freed[0] === null && freed[1] === null, JSON.stringify(freed));
    let stored = await queued();
    t.check("hängt: Meldung in der Warteschlange, als versucht markiert",
      stored.length === 1 && stored[0].cid === "gradina" && stored[0].bucket === 3 && stored[0].tried === true, JSON.stringify(stored));
    // Nachliefern mit demselben Zeitlimit. Die Meldung bleibt gespeichert, bis ihr Ergebnis feststeht:
    // App mitten im Nachliefern geschlossen (hier: neu geladen) – nichts geht verloren
    const posts = () => proxy.state.log.filter((line) => line.startsWith("POST /api/")).length;
    const before = posts();
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    await L.sleep(1000);
    t.check("hängt beim Nachliefern: Versuch läuft", posts() === before + 1, String(posts() - before));
    t.check("hängt beim Nachliefern: Meldung bleibt währenddessen gespeichert", (await queued()).length === 1);
    await page.reload({ waitUntil: "load" });  // startet beim Laden einen neuen Versuch, der wieder hängt
    stored = await queued();
    t.check("App mitten im Nachliefern geschlossen: Meldung noch da", stored.length === 1 && stored[0].cid === "gradina",
      JSON.stringify(stored));
    await L.sleep(9000);  // Zeitlimit des Versuchs beim Laden abwarten: Er blockiert danach nichts mehr
    t.check("hängt beim Nachliefern: nach dem Zeitlimit weiter gespeichert", (await queued()).length === 1);
    proxy.state.hang = null;
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    await L.waitFor(page, () => /angekommen/.test(document.querySelector("[data-report-msg]").textContent), null,
      { label: "Hinweis „angekommen“ nach dem Hängen" });
    const gradina = (await (await page.request.get(proxy.url + "/api/v1/borders/gradina")).json()).directions.to_tr;
    t.check("hängt: danach auf dem Server", gradina.state === "live" && gradina.bucket === 3, JSON.stringify(gradina));
    t.check("hängt: Warteschlange danach leer", (await queued()).length === 0);

    // Verworfen beim Nachliefern (zu alt bzw. 4xx): sichtbar und ehrlich, nicht still
    const now = await page.evaluate(() => Math.floor(Date.now() / 1000));
    await page.evaluate((items) => localStorage.setItem("tv.queue", JSON.stringify(items)), [
      { cid: "gradina", direction: "to_de", bucket: 1, observed_at: now - 2 * 3600 },  // älter als 90 Min.
      { cid: "kapikule", direction: "to_tr", bucket: 4, observed_at: now },             // 429: hier schon gemeldet
    ]);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    await L.waitFor(page, () => /zählen leider nicht/.test(document.querySelector("[data-report-msg]").textContent), null,
      { label: "Hinweis „verworfen“" });
    const lost = await msg();
    t.check("verworfen: Hinweis mit Anzahl", lost.text.startsWith("2 gespeicherte Meldungen zählen leider nicht") && lost.cls.includes("flash--error"),
      JSON.stringify(lost));
    t.check("verworfen: Warteschlange leer", (await queued()).length === 0);
    // Gemischt (offline gesammelt, eine zu alt, eine frisch): ein eigener Text, kein Widerspruch
    // „Deine Meldung ist angekommen … Deine Meldung zählt nicht“
    await page.evaluate((items) => localStorage.setItem("tv.queue", JSON.stringify(items)), [
      { cid: "gradina", direction: "to_de", bucket: 1, observed_at: now - 2 * 3600 },  // älter als 90 Min.
      { cid: "horgos", direction: "to_tr", bucket: 2, observed_at: now },               // kommt an (201)
    ]);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    await L.waitFor(page, () => /Ein Teil/.test(document.querySelector("[data-report-msg]").textContent), null,
      { label: "Hinweis „teils angekommen“" });
    const mixed = await msg();
    t.check("gemischt: ein Text mit Anzahl, gelb statt rot",
      mixed.text.startsWith("Ein Teil deiner gespeicherten Meldungen ist angekommen, danke! Eine davon zählt leider nicht")
        && !mixed.text.includes("Deine Meldung") && mixed.cls.includes("flash--queued"), JSON.stringify(mixed));
    const horgos = (await (await page.request.get(proxy.url + "/api/v1/borders/horgos")).json()).directions.to_tr;
    t.check("gemischt: frische Meldung auf dem Server", horgos.state === "live" && horgos.bucket === 2, JSON.stringify(horgos));
    t.check("gemischt: Warteschlange leer", (await queued()).length === 0);
    // War die Meldung schon unterwegs (Antwort verloren) und meldet der Server „hier schon gemeldet“,
    // ist sie angekommen – kein falscher Hinweis „zählt nicht“
    await page.evaluate((item) => localStorage.setItem("tv.queue", JSON.stringify([item])),
      { cid: "kapikule", direction: "to_tr", bucket: 2, observed_at: now, tried: true });
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    // Auf den eigenen Text warten: Der gemischte Hinweis davor enthält auch „angekommen“
    await L.waitFor(page, () => /^Deine Meldung ohne Netz ist jetzt angekommen/.test(document.querySelector("[data-report-msg]").textContent.trim()),
      null, { label: "versucht + schon gemeldet = angekommen" });
    t.check("versucht + 429 same_spot: gilt als angekommen", (await msg()).cls.includes("flash--ok"));
    // Seite ohne Meldeformular (z. B. Start der installierten App): Hinweis oben im Inhalt
    await page.evaluate((item) => localStorage.setItem("tv.queue", JSON.stringify([item])),
      { cid: "gradina", direction: "to_de", bucket: 1, observed_at: now - 2 * 3600 });
    await page.goto(proxy.url + "/tr/", { waitUntil: "load" });
    await L.waitFor(page, () => document.querySelector("[data-notice]").textContent.length > 0, null, { label: "Hinweis auf der Startseite" });
    const home = await page.$eval("[data-notice] .flash", (el) => ({ text: el.textContent, cls: el.className, visible: el.getBoundingClientRect().height > 0 }));
    t.check("Startseite: Hinweis „zählt nicht“ auf Türkisch sichtbar",
      home.text.startsWith("Cihazında bekleyen bildirimin maalesef sayılmadı") && home.cls.includes("flash--error") && home.visible, JSON.stringify(home));
    t.check("keine JS-Fehler", errors.length === 0, JSON.stringify(errors));
    await ctx.close();
  } finally {
    await browser.close();
    await proxy.close();
  }
});
