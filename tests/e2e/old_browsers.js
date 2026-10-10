/* Ältere Browser (css-1, css-2, prod-12): Auswahl, Fokus und Ferien-Diagramm ohne :has(), color-mix()
   und :focus-visible (Safari < 15.4, Chrome < 105), ohne CSS-Variablen und ganz ohne CSS.

   Simuliert durch Umschreiben des ausgelieferten CSS: Ein unbekannter Selektor verwirft die ganze Regel,
   eine unbekannte Funktion die Deklaration, genau wie im alten Browser. Verglichen wird mit demselben
   Chromium ohne Umschreiben. */
"use strict";

const L = require("./lib");

const t = L.harness("old_browsers");

const OLD = (css) => css.split(":has(").join(":hasx(").split("color-mix(").join("colorx-mix(")
  .split(":focus-visible").join(":focusx-visible");
const NOVAR = (css) => OLD(css).split("var(").join("varx(");
const NOCSS = () => "";
const BLACK = "rgb(0, 0, 0)";

async function context(browser, rewrite, scheme) {
  const ctx = await L.newContext(browser, { colorScheme: scheme || "light", serviceWorkers: "block" });
  if (rewrite) {
    await ctx.route(/\/static\/css\/[^?]*\.css(\?.*)?$/, async (route) => {
      const res = await route.fetch();
      await route.fulfill({ response: res, body: rewrite(await res.text()) });
    });
  }
  return ctx;
}

/* Auswahlfeld: Zustand der Fläche hinter Symbol und Text (::after des Text-span) */
function optState(sel) {
  const label = document.querySelector(sel);
  const after = getComputedStyle(label.querySelector("span"), "::after");
  return {
    checked: label.querySelector("input").checked,
    shown: after.content !== "none" && after.position === "absolute",
    border: after.borderTopColor, bg: after.backgroundColor, shadow: after.boxShadow, outline: after.outlineStyle,
    labelBg: getComputedStyle(label).backgroundColor,
  };
}

/* Farben, die früher an color-mix() hingen */
function paints() {
  const st = (sel, prop) => { const el = document.querySelector(sel); return el ? getComputedStyle(el)[prop] : null; };
  return {
    fill: st(".tl__fill", "fill"), fillOpacity: st(".tl__fill", "fillOpacity"),
    all16: st(".tl__area .tl__all16", "fill"), all16Opacity: st(".tl__area .tl__all16", "fillOpacity"),
    bar: st(".tl__bar", "fill"), rowbg: st(".tl__rowbg", "fill"),
    swArea: st(".sw--area", "backgroundColor"), swAll16: st(".sw--all16", "backgroundColor"),
    topbar: st(".topbar", "backgroundColor"), cardBrand: st(".card--brand:not([hidden])", "borderTopColor"),
  };
}

/* Ein Durchlauf mit (old) oder ohne (modern) Umschreiben */
async function probe(browser, rewrite, scheme) {
  const ctx = await context(browser, rewrite, scheme);
  const page = await ctx.newPage();
  const out = {};
  await page.goto(L.BASE + "/de/grenze/kapikule", { waitUntil: "load" });
  out.dotRing = await page.evaluate(() => getComputedStyle(document.querySelector(".status__dot")).boxShadow);
  await page.click('label.seg__opt:has(input[value="to_tr"])');
  await page.click('label.bucket:has(input[value="2"])');
  out.mouseFocus = await page.evaluate(optState, 'label.bucket:has(input[value="2"])');
  await page.keyboard.press("ArrowRight");  // Tastatur: nächste Wartezeit, Fokus muss sichtbar sein
  out.keyFocus = await page.evaluate(optState, 'label.bucket:has(input[value="3"])');
  await page.click('label.bucket:has(input[value="2"])');
  await page.evaluate(() => document.activeElement.blur());
  out.segOn = await page.evaluate(optState, 'label.seg__opt:has(input[value="to_tr"])');
  out.segOff = await page.evaluate(optState, 'label.seg__opt:has(input[value="to_de"])');
  out.bucketOn = await page.evaluate(optState, 'label.bucket:has(input[value="2"])');
  out.bucketOff = await page.evaluate(optState, 'label.bucket:has(input[value="1"])');

  await page.goto(L.BASE + "/de/zoll", { waitUntil: "load" });
  await page.click('[data-customs-dir="to_tr"]');
  out.filter = await page.evaluate(() => {
    const look = (el) => [getComputedStyle(el).borderTopColor, getComputedStyle(el).backgroundColor];
    return { on: look(document.querySelector('[data-customs-dir="to_tr"]')), off: look(document.querySelector('[data-customs-dir="all"]')) };
  });

  await page.goto(L.BASE + "/de/", { waitUntil: "load" });
  out.mode = await page.evaluate(optState, 'label.seg__opt:has(input[value="car"])');

  await page.goto(L.BASE + "/de/ferien?land=NW", { waitUntil: "load" });
  out.paints = await page.evaluate(paints);
  await ctx.close();
  return out;
}

t.run(async () => {
  const { chromium } = L.loadPlaywright();
  const browser = await chromium.launch();
  try {
    for (const scheme of ["light", "dark"]) {
      const modern = await probe(browser, null, scheme);
      const old = await probe(browser, OLD, scheme);
      const s = scheme + ": ";

      // css-1: Auswahl und Fokus sichtbar, im alten Browser genauso wie im neuen
      for (const key of ["segOn", "bucketOn", "mode"]) {
        const o = old[key], m = modern[key];
        t.check(s + key + " ausgewählt und sichtbar (alt)", o.checked && o.shown && o.bg !== o.labelBg, JSON.stringify(o));
        t.check(s + key + " alt = neu", o.border === m.border && o.bg === m.bg && o.shadow === m.shadow,
          JSON.stringify([o, m]));
      }
      for (const key of ["segOff", "bucketOff"]) {
        t.check(s + key + " nicht ausgewählt, keine Fläche", !old[key].checked && !old[key].shown && !modern[key].shown);
      }
      t.check(s + "Tastaturfokus sichtbar (neu)", modern.keyFocus.checked && modern.keyFocus.outline === "solid",
        JSON.stringify(modern.keyFocus));
      t.check(s + "Tastaturfokus sichtbar (alt, ohne :focus-visible)", old.keyFocus.checked && old.keyFocus.outline === "solid",
        JSON.stringify(old.keyFocus));
      t.check(s + "Mausklick ohne Fokusrahmen (neu)", modern.mouseFocus.outline === "none", JSON.stringify(modern.mouseFocus));
      t.check(s + "Filter-Button aktiv sichtbar (alt)", old.filter.on.join() !== old.filter.off.join(), JSON.stringify(old.filter));
      t.check(s + "Filter-Button alt = neu", JSON.stringify(old.filter) === JSON.stringify(modern.filter),
        JSON.stringify([old.filter, modern.filter]));

      // css-2/prod-12: dieselben Farben ohne color-mix(), nichts schwarz oder durchsichtig
      t.check(s + "Ferien-Diagramm, Legende, Topbar alt = neu", JSON.stringify(old.paints) === JSON.stringify(modern.paints),
        JSON.stringify([old.paints, modern.paints]));
      t.check(s + "Diagramm nicht schwarz", old.paints.fill !== BLACK && old.paints.all16 !== BLACK && old.paints.fillOpacity === "0.3",
        JSON.stringify(old.paints));
      t.check(s + "Legende und Topbar nicht durchsichtig",
        [old.paints.swArea, old.paints.swAll16, old.paints.topbar].every((c) => c && c !== "rgba(0, 0, 0, 0)"), JSON.stringify(old.paints));
      t.check(s + "Status-Ring alt = neu", old.dotRing === modern.dotRing && old.dotRing !== "none", old.dotRing);
    }

    // Ohne CSS-Variablen bzw. ganz ohne CSS: die fill-Attribute im Template greifen
    for (const [label, rewrite] of [["ohne var()", NOVAR], ["ohne CSS", NOCSS]]) {
      const ctx = await context(browser, rewrite);
      const page = await ctx.newPage();
      await page.goto(L.BASE + "/de/ferien?land=NW", { waitUntil: "load" });
      const p = await page.evaluate(paints);
      t.check(label + ": Ferien-Druck terrakotta, 30 %", p.fill === "rgb(163, 60, 22)" && p.fillOpacity === "0.3", JSON.stringify(p));
      t.check(label + ": alle 16 Länder terrakotta, 12 %", p.all16 === "rgb(163, 60, 22)" && p.all16Opacity === "0.12", JSON.stringify(p));
      t.check(label + ": Balken petrol, Zeilen ohne Fläche", p.bar === "rgb(14, 107, 107)" && p.rowbg !== BLACK, JSON.stringify(p));
      await ctx.close();
    }
  } finally {
    await browser.close();
  }
});
