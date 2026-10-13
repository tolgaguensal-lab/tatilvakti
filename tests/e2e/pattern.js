/* Tagesverlauf einer Grenzseite (crossing.html): Schrift lesbar bei jeder Breite (a), Beschriftungen
   ohne Überdeckung (a), Jetzt-Marker folgt der Uhr des Browsers – auch für Seiten aus dem
   Offline-Speicher (b) und in einem lange offenen Tab (c). test_e2e.py legt dafür für Kapıkule an
   drei Vortagen Meldungen zu jeder Ortsstunde an (21–23 Uhr: über 4 Std., die höchsten Balken). */
"use strict";

const L = require("./lib");

const t = L.harness("pattern");
const NOW = Date.parse(L.NOW);
const ISTANBUL = 3;  // UTC+3, ganzjährig
const PAGES = [["/de/grenze/kapikule", "Jetzt"], ["/tr/sinir/kapikule", "Şimdi"]];
// Browser-Uhr so stellen, dass es in Istanbul hour:30 ist (Server rendert mit seiner Uhr: 11 Uhr)
const at = (hour) => new Date(Date.UTC(2027, 6, 20, (hour - ISTANBUL + 24) % 24, 30));
const xFor = (hour) => ((hour + 0.5) / 24 * 100).toFixed(3) + "%";

function measure() {
  const box = (el) => { const r = el.getBoundingClientRect(); return { l: r.left, r: r.right, t: r.top, b: r.bottom, h: r.height }; };
  const hit = (a, b) => a.l < b.r - 0.5 && b.l < a.r - 0.5 && a.t < b.b - 0.5 && b.t < a.b - 0.5;
  return Array.from(document.querySelectorAll("svg.pattern")).map((svg) => {
    const card = svg.closest(".card");
    const cs = getComputedStyle(card);
    const inner = { l: card.getBoundingClientRect().left + parseFloat(cs.borderLeftWidth),
                    r: card.getBoundingClientRect().right - parseFloat(cs.borderRightWidth) };
    const s = box(svg);
    const mark = svg.querySelector(".pattern__nowmark");
    const now = box(svg.querySelector(".pattern__nowlabel"));
    const lvls = Array.from(svg.querySelectorAll(".pattern__lvl")).map(box);
    const bars = Array.from(svg.querySelectorAll(".pattern__bar")).map(box);
    const ticks = Array.from(svg.querySelectorAll(".pattern__tick")).map(box);
    return {
      hour: mark.getAttribute("data-hour"),
      x1: svg.querySelector(".pattern__now").getAttribute("x1"),
      label: svg.querySelector(".pattern__nowlabel").textContent,
      tickMin: Math.min(...ticks.map((b) => b.h)), tickMax: Math.max(...ticks.map((b) => b.h)),
      lvlMin: Math.min(...lvls.map((b) => b.h)),
      lvlInCard: lvls.every((b) => b.l >= s.r && b.r <= inner.r),
      lvlOnBar: lvls.some((l) => bars.some((b) => hit(l, b))),
      lvlOnNow: lvls.some((l) => hit(l, now)),
      nowInSvg: now.l >= s.l - 0.5 && now.r <= s.r + 0.5,
      inView: [s, now, ...lvls, ...ticks].every((b) => b.l >= -0.5 && b.r <= document.documentElement.clientWidth + 0.5),
    };
  });
}

t.run(async () => {
  const { chromium } = L.loadPlaywright();
  const browser = await chromium.launch();
  const proxy = await L.startProxy(L.BASE);
  try {
    // ---------------------------------------------------------------- (a) Breiten, Stunden, Sprachen
    for (const width of [320, 360, 412, 1280]) {
      const ctx = await L.newContext(browser, { viewport: { width, height: 800 }, serviceWorkers: "block" });
      const page = await ctx.newPage();
      const errors = [];
      page.on("pageerror", (e) => errors.push(String(e)));
      for (const hour of [0, 1, 11, 21, 22, 23]) {
        await ctx.clock.setFixedTime(at(hour));
        for (const [path, label] of PAGES) {
          await page.goto(L.BASE + path, { waitUntil: "load" });
          const charts = await page.evaluate(measure);
          const tag = `(a) ${width}px ${path} ${hour} Uhr`;
          t.check(`${tag}: zwei Diagramme`, charts.length === 2, String(charts.length));
          for (const c of charts) {
            t.check(`${tag}: Marker auf der Browser-Stunde`, c.hour === String(hour) && c.x1 === xFor(hour) && c.label === label,
              JSON.stringify([c.hour, c.x1, c.label]));
            // 11 px Schrift (Box ~13 px) bei jeder Breite: weder winzig am Handy noch riesig am Desktop
            t.check(`${tag}: Uhrzeiten lesbar`, c.tickMin >= 11 && c.tickMax <= 16, `${c.tickMin}–${c.tickMax}`);
            t.check(`${tag}: Niveau-Beschriftung lesbar`, c.lvlMin >= 10, String(c.lvlMin));
            t.check(`${tag}: Niveau-Beschriftung neben der Fläche, in der Karte`, c.lvlInCard);
            t.check(`${tag}: Niveau-Beschriftung frei (Balken, Jetzt)`, !c.lvlOnBar && !c.lvlOnNow);
            t.check(`${tag}: Jetzt-Beschriftung in der Fläche`, c.nowInSvg);
            t.check(`${tag}: nichts ragt aus dem Fenster`, c.inView);
          }
        }
      }
      t.check(`(a) ${width}px: keine JS-Fehler`, errors.length === 0, JSON.stringify(errors));
      await ctx.close();
    }

    // ---------------------------------------------------------------- (b) aus dem Offline-Speicher
    {
      const ctx = await L.newContext(browser);
      const page = await ctx.newPage();
      await L.installApp(page, proxy.url, "/de/grenze/kapikule");
      const cached = await page.evaluate(async () => {
        const hit = await caches.match(location.origin + "/de/grenze/kapikule");
        return hit ? (await hit.text()).match(/data-hour="(\d+)"/g) : null;
      });
      t.check("(b) gespeicherte Seite mit Stunde vom Server (11)", JSON.stringify(cached) === JSON.stringify(['data-hour="11"', 'data-hour="11"']),
        JSON.stringify(cached));
      await L.setOffline(ctx, proxy, true);
      await ctx.clock.setFixedTime(new Date(NOW + 5 * 3600 * 1000));  // 16:00 in Istanbul
      await page.goto(proxy.url + "/de/grenze/kapikule", { waitUntil: "load" });
      const hours = await page.$$eval(".pattern__nowmark", (els) => els.map((el) => el.getAttribute("data-hour")));
      t.check("(b) offline: Marker auf der aktuellen Stunde (16)", JSON.stringify(hours) === '["16","16"]', JSON.stringify(hours));
      await L.setOffline(ctx, proxy, false);
      await ctx.close();
    }

    // ---------------------------------------------------------------- (c) lange offener Tab
    {
      const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: "block" });
      await ctx.clock.install({ time: new Date(NOW) });
      const page = await ctx.newPage();
      await page.goto(L.BASE + "/tr/sinir/kapikule", { waitUntil: "load" });
      const before = await page.$eval(".pattern__nowmark", (el) => el.getAttribute("data-hour"));
      await page.clock.fastForward("02:00:00");
      await L.waitFor(page, () => document.querySelector(".pattern__nowmark").getAttribute("data-hour") === "13", null,
        { timeout: 5000, label: "Marker wandert mit" });
      const x1 = await page.$eval(".pattern__nowmark .pattern__now", (el) => el.getAttribute("x1"));
      t.check("(c) offener Tab: Marker wandert mit der Uhr", before === "11" && x1 === xFor(13), JSON.stringify([before, x1]));
      await ctx.close();
    }
  } finally {
    await browser.close();
    await proxy.close();
  }
});
