/* Sticky Zoll-Toolbar (a11y-1): Sprungziele und Tastaturfokus landen unter ihr, nicht dahinter
   (WCAG 2.4.11), und die Seite springt nicht, wenn man in Suche oder Filter tippt.
   Layout (layout-1): keine Seite aus der Precache-Liste läuft bei 320, 360 oder 390 px seitlich über. */
"use strict";

const L = require("./lib");

const t = L.harness("toolbar_layout");
const ZOLL = { de: "/de/zoll", tr: "/tr/gumruk" };

/* Regeltitel bzw. fokussiertes Element im Verhältnis zur Toolbar (läuft im Browser, siehe addInitScript) */
function underToolbar(el) {
  const tools = document.querySelector("[data-customs-tools]").getBoundingClientRect();
  const r = el.getBoundingClientRect();
  return { top: Math.round(r.top), bottom: Math.round(r.bottom), toolbarBottom: Math.round(tools.bottom), covered: r.top < tools.bottom - 0.5 };
}

t.run(async () => {
  const { chromium } = L.loadPlaywright();
  const browser = await chromium.launch();
  try {
    for (const width of [320, 390]) {
      const ctx = await L.newContext(browser, { viewport: { width, height: 700 }, isMobile: true, hasTouch: true, serviceWorkers: "block" });
      await ctx.addInitScript({ content: "window.underToolbar = " + underToolbar.toString() + ";" });
      const page = await ctx.newPage();
      const errors = [];
      page.on("pageerror", (e) => errors.push(String(e)));
      for (const lang of ["de", "tr"]) {
        // Tipp auf eine Karte „teuerste Fehler“ (Sprung innerhalb der Seite)
        await page.goto(L.BASE + ZOLL[lang], { waitUntil: "load" });
        const ids = await page.$$eval(".featured a", (as) => as.map((a) => a.getAttribute("href")));
        const bad = [];
        for (const id of ids) {
          await page.evaluate(() => window.scrollTo(0, 0));
          await page.click(`.featured a[href="${id}"]`);
          await L.sleep(50);
          const pos = await page.evaluate((sel) => underToolbar(document.querySelector(sel + " .rule__title")), id);
          if (pos.covered) bad.push(id + " " + JSON.stringify(pos));
        }
        t.check(`${width}px ${lang}: ${ids.length} Sprungziele unter der Toolbar sichtbar`, ids.length >= 4 && !bad.length, bad.join(" "));

        // Von der Startseite aus (Toolbar erscheint erst mit app.js)
        await page.goto(L.BASE + "/" + lang + "/", { waitUntil: "load" });
        const href = await page.getAttribute(".mini-rules a", "href");
        await page.goto(L.BASE + href, { waitUntil: "load" });
        await L.sleep(100);
        const pos = await page.evaluate(() => underToolbar(document.querySelector(location.hash + " .rule__title")));
        t.check(`${width}px ${lang}: Sprung von der Startseite (${href}) sichtbar`, !pos.covered, JSON.stringify(pos));

        // Tastatur rückwärts durch die Liste: kein Fokus unter der Toolbar
        await page.goto(L.BASE + ZOLL[lang], { waitUntil: "load" });
        await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
        await page.focus(".footer a");
        const hidden = [];
        let steps = 0, inToolbar = false;
        while (steps++ < 120 && !inToolbar) {
          await page.keyboard.press("Shift+Tab");
          await L.sleep(30);
          const r = await page.evaluate(() => {
            const el = document.activeElement;
            if (!el || el === document.body || el.closest(".tabbar, .footer")) return null;
            if (el.closest("[data-customs-tools]")) return { inToolbar: true };
            return Object.assign({ what: el.tagName + " " + el.textContent.trim().slice(0, 20) }, underToolbar(el));
          });
          if (!r) continue;
          inToolbar = !!r.inToolbar;
          if (!inToolbar && r.covered) hidden.push(JSON.stringify(r));
        }
        t.check(`${width}px ${lang}: Fokus nie unter der Toolbar (bis zur Toolbar zurück)`, inToolbar && !hidden.length,
          hidden.slice(0, 3).join(" "));

        // Mitten in der Liste in Suche und Filter tippen: die Seite bleibt stehen
        await page.evaluate(() => window.scrollTo(0, 3000));
        for (const sel of ["[data-customs-search]", '[data-customs-dir="all"]']) {
          const y0 = await page.evaluate(() => window.scrollY);
          await page.tap(sel);
          await L.sleep(100);
          const dy = (await page.evaluate(() => window.scrollY)) - y0;
          t.check(`${width}px ${lang}: Tippen in ${sel} springt nicht`, Math.abs(dy) <= 8, "Δy=" + dy);
        }
      }
      t.check(`${width}px: keine JS-Fehler`, errors.length === 0, JSON.stringify(errors));
      await ctx.close();
    }

    // Browser ohne scroll-margin (Safari < 14.1): app.js schiebt Sprungziele nach
    {
      const ctx = await L.newContext(browser, { viewport: { width: 390, height: 700 }, isMobile: true, hasTouch: true, serviceWorkers: "block" });
      await ctx.route(/\/static\/css\/[^?]*\.css(\?.*)?$/, async (route) => {
        const res = await route.fetch();
        await route.fulfill({ response: res, body: (await res.text()).split("scroll-margin-top").join("scrollx-margin-top") });
      });
      await ctx.addInitScript({ content: "window.underToolbar = " + underToolbar.toString() + ";" });
      const page = await ctx.newPage();
      await page.goto(L.BASE + "/de/zoll", { waitUntil: "load" });
      const ids = await page.$$eval(".featured a", (as) => as.map((a) => a.getAttribute("href")));
      const bad = [];
      for (const id of ids) {
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.click(`.featured a[href="${id}"]`);
        await L.sleep(100);
        const pos = await page.evaluate((sel) => underToolbar(document.querySelector(sel + " .rule__title")), id);
        if (pos.covered) bad.push(id + " " + JSON.stringify(pos));
      }
      t.check(`ohne scroll-margin: ${ids.length} Sprungziele sichtbar`, !bad.length, bad.join(" "));
      await page.goto(L.BASE + "/de/", { waitUntil: "load" });
      const href = await page.getAttribute(".mini-rules a", "href");
      await page.goto(L.BASE + href, { waitUntil: "load" });
      await L.sleep(150);
      const pos = await page.evaluate(() => underToolbar(document.querySelector(location.hash + " .rule__title")));
      t.check(`ohne scroll-margin: Sprung von der Startseite sichtbar`, !pos.covered, JSON.stringify(pos));
      await ctx.close();
    }

    // Ohne JavaScript: keine Toolbar, kein zusätzlicher Abstand
    {
      const ctx = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 390, height: 700 } });
      const page = await ctx.newPage();
      await page.goto(L.BASE + "/de/zoll", { waitUntil: "load" });
      const r = await page.evaluate(() => ({
        cls: document.documentElement.className, toolbar: !document.querySelector("[data-customs-tools]").hidden,
        margin: getComputedStyle(document.querySelector(".rule")).scrollMarginTop,
      }));
      t.check("ohne JS: keine Toolbar, scroll-margin 0", !r.cls.includes("has-toolbar") && !r.toolbar && r.margin === "0px", JSON.stringify(r));
      await ctx.close();
    }

    // layout-1: alle Seiten der Precache-Liste bei 320, 360 und 390 px, DE und TR
    {
      const ctx0 = await L.newContext(browser, { serviceWorkers: "block" });
      const p0 = await ctx0.newPage();
      await p0.goto(L.BASE + "/de/", { waitUntil: "load" });
      const config = await L.swConfig(p0);
      await ctx0.close();
      const pages = config.pages.filter((p) => !p.endsWith(".webmanifest"))
        .concat(["/de/ferien?land=NW", "/tr/tatil?land=BY", "/de/route?land=HE"]);
      t.check("Precache-Liste mit DE- und TR-Seiten", pages.some((p) => p.startsWith("/de/")) && pages.some((p) => p.startsWith("/tr/")) && pages.length > 30,
        String(pages.length));
      for (const width of [320, 360, 390]) {
        const ctx = await L.newContext(browser, { viewport: { width, height: 640 }, isMobile: true, hasTouch: true, serviceWorkers: "block" });
        await ctx.addInitScript(() => { try { localStorage.setItem("tv.state", '"NW"'); } catch (e) { /* egal */ } });
        const page = await ctx.newPage();
        const over = [];
        for (const path of pages) {
          await page.goto(L.BASE + path, { waitUntil: "load" });
          // Aufklappbares (Länder der Route, Ferientabelle) mitprüfen
          await page.evaluate(() => document.querySelectorAll("details").forEach((d) => { d.open = true; }));
          const r = await page.evaluate(() => ({ inner: window.innerWidth, scroll: document.documentElement.scrollWidth }));
          if (r.inner !== width || r.scroll > r.inner) over.push(path + " " + JSON.stringify(r));
        }
        t.check(`${width}px: ${pages.length} Seiten ohne seitliches Überlaufen`, !over.length, over.slice(0, 4).join(" "));
        await ctx.close();
      }
    }
  } finally {
    await browser.close();
  }
});
