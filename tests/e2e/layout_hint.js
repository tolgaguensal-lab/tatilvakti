/* Layout bei 320 px (d) und Hinweis „auf den Startbildschirm“ (iOS, Chromium, installiert, ohne JS):
   nur auf Start-, Grenz- und Übergangsseiten, erst nach Wahl des Bundeslandes oder einer Meldung,
   ohne Besuchszähler, auf niedrigen Bildschirmen kompakt, Fokus nie unter der Karte. */
"use strict";

const L = require("./lib");

const t = L.harness("layout_hint");
const PAGES = [
  "/de/", "/de/ferien?land=NW", "/de/route", "/de/grenze", "/de/grenze/kapikule", "/de/zoll", "/de/info", "/de/offline",
  "/tr/", "/tr/tatil?land=BY", "/tr/guzergah", "/tr/sinir", "/tr/sinir/kapikule", "/tr/gumruk", "/tr/bilgi", "/tr/cevrimdisi",
  // Zeiträume als eigene Seiten, mit den längsten Überschriften
  "/de/ferien/pfingsten-2027?land=BW", "/tr/tatil/mayis-2027?land=BW", "/tr/tatil/yilbasi-2026-27",
];
const IPHONE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
  + "Version/18.6 Mobile/15E148 Safari/604.1";
const ANDROID_UA = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
  + "Chrome/141.0.0.0 Mobile Safari/537.36";

/* Elemente, die rechts oder links über den Viewport hinausragen (body hat overflow-x: hidden,
   scrollWidth allein verriete das nicht). Bewusst scrollbare Leisten (.chips) zählen nicht. */
function overflowing() {
  const vw = document.documentElement.clientWidth;
  const out = [];
  document.querySelectorAll("body *").forEach((el) => {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height || getComputedStyle(el).visibility === "hidden") return;
    if (el.closest(".skip, .hp, .sr-only, .sprite")) return;
    for (let a = el.parentElement; a; a = a.parentElement) {
      const ox = getComputedStyle(a).overflowX;
      if (ox === "auto" || ox === "scroll") return;
    }
    if (r.right > vw + 0.5 || r.left < -0.5) {
      out.push(el.tagName.toLowerCase() + "." + String(el.className.baseVal ?? el.className).trim().replace(/\s+/g, ".")
        + " [" + Math.round(r.left) + "," + Math.round(r.right) + "]");
    }
  });
  return { vw, n: out.length, first: out.slice(0, 5) };
}

const hintVisible = (page) => page.isVisible("[data-a2hs]");
const HINT_PAGES = /^\/(de|tr)\/(grenze|sinir)?(\/[a-z-]+)?$/; // Start, Grenz-Übersicht, Übergang
const hintPage = (path) => HINT_PAGES.test(path.split("?")[0]);
const store = (page, key) => page.evaluate((k) => JSON.parse(localStorage.getItem("tv." + k) || "null"), key);

t.run(async () => {
  const { chromium } = L.loadPlaywright();
  const browser = await chromium.launch();
  const base = L.BASE;
  try {
    // ---------------------------------------------------------------- (d) 320 px, DE und TR, mit sichtbarem Hinweis
    for (const width of [320, 390]) {
      const ctx = await L.newContext(browser, {
        viewport: { width, height: 640 }, isMobile: true, hasTouch: true, userAgent: IPHONE_UA, serviceWorkers: "block",
      });
      await ctx.addInitScript(() => { try { localStorage.setItem("tv.state", '"NW"'); } catch (e) { /* egal */ } });
      const page = await ctx.newPage();
      for (const path of PAGES) {
        await page.goto(base + path, { waitUntil: "load" });
        const res = await page.evaluate(overflowing);
        t.check(`(d) ${width}px ${path}: kein horizontales Überlaufen`, res.n === 0, JSON.stringify(res.first));
        if (hintPage(path)) {
          t.check(`(d) ${width}px ${path}: Hinweis sichtbar`, await hintVisible(page));
        } else {
          t.check(`(d) ${width}px ${path}: kein Hinweis`, (await page.$$("[data-a2hs]")).length === 0);
        }
      }
      if (width === 320) {
        await page.goto(base + "/tr/", { waitUntil: "load" });
        const box = await page.locator("[data-a2hs]").boundingBox();
        const tabbar = await page.locator(".tabbar").boundingBox();
        t.check("(d) Hinweis liegt über der Tabbar", box.y + box.height <= tabbar.y + 0.5, JSON.stringify([box, tabbar]));
        const close = await page.locator("[data-a2hs-close]").boundingBox();
        t.check("(d) Schließen-Fläche ≥ 44 px", close.width >= 44 && close.height >= 44, JSON.stringify(close));
      }
      await ctx.close();
    }

    // ---------------------------------------------------------------- iOS Safari
    {
      const ctx = await L.newContext(browser, { isMobile: true, hasTouch: true, userAgent: IPHONE_UA, serviceWorkers: "block" });
      const page = await ctx.newPage();
      await page.goto(base + "/de/", { waitUntil: "load" });
      t.check("iOS: erster Besuch ohne Bundesland → kein Hinweis", !(await hintVisible(page)));
      await page.selectOption('select[data-pref="state"]', "BY");
      t.check("iOS: nach Wahl des Bundeslandes sichtbar", await hintVisible(page));
      t.check("iOS: Kurzanleitung statt Button", await page.isVisible("[data-a2hs-ios]") && !(await page.isVisible("[data-a2hs-install]")));
      t.check("iOS: Teilen-Symbol", await page.isVisible('[data-a2hs-ios] use[href="#i-ios-share"]'));
      await page.click("[data-a2hs-close]");
      t.check("iOS: Schließen blendet aus", !(await hintVisible(page)));
      t.check("iOS: Schließen gemerkt", (await store(page, "a2hs_off")) === true);
      await page.reload({ waitUntil: "load" });
      t.check("iOS: bleibt geschlossen", !(await hintVisible(page)));
      await ctx.close();
    }
    {
      // Ohne Bundesland: kein Besuchszähler mehr, erst eine angenommene Meldung zeigt den Hinweis.
      // Werte älterer Versionen (tv.visits, tv.seen_at) verschwinden beim Laden.
      const ctx = await L.newContext(browser, { isMobile: true, hasTouch: true, userAgent: IPHONE_UA, serviceWorkers: "block" });
      await ctx.addInitScript(() => {
        if (sessionStorage.getItem("seeded")) return;
        sessionStorage.setItem("seeded", "1");
        localStorage.setItem("tv.visits", "2");
        localStorage.setItem("tv.seen_at", "1791424414465");
      });
      const page = await ctx.newPage();
      await page.goto(base + "/tr/", { waitUntil: "load" });
      t.check("iOS: alte Zählerwerte entfernt", (await page.evaluate(() => [localStorage.getItem("tv.visits"), localStorage.getItem("tv.seen_at")]))
        .every((v) => v === null));
      t.check("iOS: ohne Bundesland und Meldung kein Hinweis (auch nicht beim „2. Besuch“)", !(await hintVisible(page)));
      const keys = await page.evaluate(() => Object.keys(localStorage).sort());
      t.check("iOS: Aufruf allein speichert nichts", keys.length === 0, JSON.stringify(keys));
      await page.goto(base + "/tr/sinir/kapikule", { waitUntil: "load" });
      // abgelehnte Meldung (Limit) zählt nicht
      await page.route("**/api/v1/borders/*/reports", (route) => route.fulfill({
        status: 429, contentType: "application/json", body: '{"error":"ratelimited"}' }));
      await page.click('label.seg__opt:has(input[value="to_tr"])');
      await page.click('label.bucket:has(input[value="1"])');
      await page.click('form[data-report] button[type="submit"]');
      await L.waitFor(page, () => document.querySelector("[data-report-msg]").textContent.length > 0, null, { label: "Antwort" });
      t.check("iOS: abgelehnte Meldung → kein Hinweis, nichts gemerkt", !(await hintVisible(page)) && (await store(page, "report_pref")) === null);
      await page.unroute("**/api/v1/borders/*/reports");
      await page.click('label.bucket:has(input[value="1"])');
      await page.click('form[data-report] button[type="submit"]');
      await L.waitFor(page, () => !document.querySelector("[data-a2hs]").hidden, null, { label: "Hinweis nach Meldung" });
      t.check("iOS: nach erfolgreicher Meldung Hinweis", await hintVisible(page));
      t.check("iOS: Text auf Türkisch", (await page.textContent("[data-a2hs]")).includes("Ana Ekrana Ekle"));
      await page.goto(base + "/tr/sinir", { waitUntil: "load" });
      t.check("iOS: auch auf der Grenz-Übersicht", await hintVisible(page));
      await page.goto(base + "/tr/gumruk", { waitUntil: "load" });
      t.check("iOS: nicht auf der Zollseite", (await page.$$("[data-a2hs]")).length === 0);
      await ctx.close();
    }
    {
      // Fokus nie unter der Karte (WCAG 2.4.11), Esc schließt. Niedriger Bildschirm (320 × 568):
      // kompakte Karte, die iOS-Schritte erst auf Knopfdruck. 360 × 740: volle Karte.
      const cases = [[320, 568, "/de/"], [320, 568, "/tr/"], [320, 568, "/de/grenze/kapikule"], [360, 740, "/de/"], [360, 740, "/tr/sinir"]];
      for (const [width, height, path] of cases) {
        const label = `${width}×${height} ${path}`;
        const compact = height <= 640;
        const ctx = await L.newContext(browser, {
          viewport: { width, height }, isMobile: true, hasTouch: true, userAgent: IPHONE_UA, serviceWorkers: "block",
        });
        await ctx.addInitScript(() => { try { localStorage.setItem("tv.state", '"NW"'); } catch (e) { /* egal */ } });
        const page = await ctx.newPage();
        await page.goto(base + path, { waitUntil: "load" });
        const card = await page.locator("[data-a2hs]").boundingBox();
        if (compact) {
          t.check(`${label}: kompakte Karte höchstens 120 px hoch`, card && card.height <= 120, JSON.stringify(card));
          t.check(`${label}: Schritte zu, Knopf da`, !(await page.isVisible(".a2hs__steps")) && await page.isVisible("[data-a2hs-how]"));
          await page.click("[data-a2hs-how]");
          t.check(`${label}: Schritte nach Klick`, await page.isVisible(".a2hs__steps")
            && (await page.getAttribute("[data-a2hs-how]", "aria-expanded")) === "true");
          await page.click("[data-a2hs-how]");
        } else {
          t.check(`${label}: volle Karte mit Schritten, ohne Knopf`, await page.isVisible(".a2hs__steps") && !(await page.isVisible("[data-a2hs-how]")));
        }
        const covered = [];
        let stops = 0;
        await page.evaluate(() => { if (document.activeElement) document.activeElement.blur(); window.scrollTo(0, 0); });
        for (let i = 0; i < 90; i++) {
          await page.keyboard.press("Tab");
          await page.waitForTimeout(20);
          const f = await page.evaluate(() => {
            const el = document.activeElement;
            if (!el || el === document.body) return null;
            const hint = document.querySelector("[data-a2hs]");
            if (hint.contains(el) || el.closest(".tabbar, .topbar, .skip")) return { skip: true };
            const r = el.getBoundingClientRect();
            const top = hint.hidden ? Infinity : hint.getBoundingClientRect().top;
            return { covered: r.bottom > top + 0.5, label: el.tagName + " " + (el.textContent || "").trim().slice(0, 30), bottom: Math.round(r.bottom), card: Math.round(top) };
          });
          if (!f || f.skip) continue;
          stops++;
          if (f.covered) covered.push(f);
        }
        t.check(`${label}: kein Fokus (auch nicht teilweise) unter der Karte, ${stops} Stopps`, stops > 10 && covered.length === 0,
          JSON.stringify(covered.slice(0, 3)));
        await page.keyboard.press("Escape");
        t.check(`${label}: Esc schließt und merkt`, !(await hintVisible(page)) && (await store(page, "a2hs_off")) === true);
        await ctx.close();
      }
    }
    {
      // Als App vom Home-Bildschirm gestartet: nie ein Hinweis, Speicher wird dauerhaft angefragt
      const ctx = await L.newContext(browser, { isMobile: true, hasTouch: true, userAgent: IPHONE_UA, serviceWorkers: "block" });
      await ctx.addInitScript(() => {
        Object.defineProperty(navigator, "standalone", { value: true });
        window.__persist = 0;
        if (navigator.storage) {
          navigator.storage.persisted = () => Promise.resolve(false);
          navigator.storage.persist = () => { window.__persist++; return Promise.resolve(true); };
        }
        try { localStorage.setItem("tv.state", '"HE"'); } catch (e) { /* egal */ }
      });
      const page = await ctx.newPage();
      await page.goto(base + "/de/", { waitUntil: "load" });
      t.check("standalone: kein Hinweis", !(await hintVisible(page)));
      t.check("standalone: persist() beim Laden", (await page.evaluate(() => window.__persist)) === 1);
      await ctx.close();
    }

    // ---------------------------------------------------------------- Chromium (beforeinstallprompt)
    {
      const ctx = await L.newContext(browser, { isMobile: true, hasTouch: true, userAgent: ANDROID_UA, serviceWorkers: "block" });
      await ctx.addInitScript(() => {
        window.__persist = 0;
        if (navigator.storage) navigator.storage.persist = () => { window.__persist++; return Promise.resolve(true); };
        window.__fakePrompt = () => {
          const ev = new Event("beforeinstallprompt", { cancelable: true });
          ev.prompt = () => { window.__prompted = true; return Promise.resolve(); };
          ev.userChoice = Promise.resolve({ outcome: "accepted" });
          window.dispatchEvent(ev);
          return ev.defaultPrevented;
        };
      });
      const page = await ctx.newPage();
      await page.goto(base + "/de/", { waitUntil: "load" });
      const prevented = await page.evaluate(() => window.__fakePrompt());
      t.check("Chromium: erster Besuch → Infoleiste des Browsers bleibt", !prevented && !(await hintVisible(page)));
      t.check("Chromium: ohne Ereignis keine iOS-Anleitung", !(await page.isVisible("[data-a2hs-ios]")));
      await page.selectOption('select[data-pref="state"]', "NW");
      t.check("Chromium: nach Wahl des Bundeslandes Button", await hintVisible(page) && await page.isVisible("[data-a2hs-install]"));
      await page.reload({ waitUntil: "load" });
      t.check("Chromium: vor dem Ereignis nichts sichtbar", !(await hintVisible(page)));
      t.check("Chromium: Ereignis abgefangen, wenn der Hinweis kommt", await page.evaluate(() => window.__fakePrompt()));
      await page.click("[data-a2hs-install]");
      await L.waitFor(page, () => window.__prompted === true, null, { label: "prompt()" });
      await L.waitFor(page, () => document.querySelector("[data-a2hs]").hidden, null, { label: "Hinweis weg" });
      t.check("Chromium: Installationsdialog geöffnet, danach ausgeblendet und gemerkt", (await store(page, "a2hs_off")) === true);
      t.check("Chromium: im Browser kein persist()", (await page.evaluate(() => window.__persist)) === 0);
      await page.evaluate(() => window.dispatchEvent(new Event("appinstalled")));
      await L.waitFor(page, () => window.__persist === 1, null, { label: "persist() nach appinstalled" });
      t.check("Chromium: nach appinstalled persist()", true);
      await ctx.close();
    }

    // ---------------------------------------------------------------- ohne JavaScript
    {
      const ctx = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 320, height: 640 }, userAgent: IPHONE_UA });
      const page = await ctx.newPage();
      await page.goto(base + "/de/", { waitUntil: "load" });
      t.check("ohne JS: Hinweis unsichtbar", !(await hintVisible(page)));
      await ctx.close();
    }
  } finally {
    await browser.close();
  }
});
