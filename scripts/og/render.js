/* Link-Vorschaubilder (1200 × 630) aus og.html rendern, Texte aus tatilvakti/i18n/<lang>.json.

   Nach jeder Änderung an den Texten in og.json (tagline, Stationstitel, og_foot):
     NODE_PATH=/pfad/zu/node_modules node scripts/og/render.js
   Schreibt tatilvakti/static/og/og-de.png und og-tr.png sowie rendered-texts.json (die verwendeten
   Texte). tests/test_share.py vergleicht diese Datei mit den aktuellen i18n-Texten – so fällt ein
   vergessenes Neurendern auf. Braucht Playwright für Node (nur Entwicklung, keine Laufzeit-Abhängigkeit). */
"use strict";

const fs = require("fs");
const path = require("path");
const { chromium } = require(require.resolve(process.env.PLAYWRIGHT_MODULE || "playwright"));

const ROOT = path.resolve(__dirname, "..", "..");
const LANGS = ["de", "tr"];

function texts(lang, keys) {
  const strings = JSON.parse(fs.readFileSync(path.join(ROOT, "tatilvakti", "i18n", lang + ".json"), "utf8"));
  const get = (key) => {
    if (typeof strings[key] !== "string") throw new Error(`${lang}.json: Schlüssel ${key} fehlt`);
    return strings[key];
  };
  return { claim: get(keys.claim), feats: keys.feats.map(get), foot: get(keys.foot) };
}

(async () => {
  const keys = JSON.parse(fs.readFileSync(path.join(__dirname, "og.json"), "utf8"));
  const out = path.join(ROOT, "tatilvakti", "static", "og");
  const used = {};
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
    for (const lang of LANGS) {
      const t = texts(lang, keys);
      used[lang] = t;
      await page.goto("file://" + path.join(__dirname, "og.html"));
      await page.evaluate(({ lang, t }) => {
        document.documentElement.lang = lang;
        document.getElementById("claim").textContent = t.claim;
        document.getElementById("foot").textContent = t.foot;
        t.feats.forEach((label, i) => {
          const el = document.createElement("span");
          el.className = "feat";
          const num = document.createElement("b");
          num.textContent = String(i + 1);
          el.append(num, label);
          document.getElementById("feats").appendChild(el);
        });
      }, { lang, t });
      await page.evaluate(() => document.fonts.ready);
      // Mindestens 40 px Rand: WhatsApp & Co. beschneiden die Vorschau je nach Gerät leicht
      const over = await page.evaluate(() => Array.from(document.querySelectorAll("body *")).filter((el) => {
        const r = el.getBoundingClientRect();
        return r.width && (r.left < 40 || r.right > 1160 || r.top < 0 || r.bottom > 630);
      }).map((el) => el.className || el.tagName));
      if (over.length) throw new Error(`${lang}: zu nah am Rand: ${over.join(", ")}`);
      await page.screenshot({ path: path.join(out, `og-${lang}.png`), type: "png" });
      console.log(`og-${lang}.png`);
    }
  } finally {
    await browser.close();
  }
  fs.writeFileSync(path.join(__dirname, "rendered-texts.json"), JSON.stringify(used, null, 2) + "\n");
})().catch((err) => { console.error(err.message || err); process.exit(1); });
