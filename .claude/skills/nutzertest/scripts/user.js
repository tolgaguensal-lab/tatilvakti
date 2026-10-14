/* Bedient tatilvakti wie ein Mensch am Handy: Schritte aus einer JSON-Datei, nach jedem Schritt
   steht im Protokoll, was auf dem Bildschirm zu lesen ist.

     NODE_PATH=/opt/node-tools/node_modules node .claude/skills/nutzertest/scripts/user.js \
       --base http://127.0.0.1:18900 --device android --lang de \
       --out tmp/nutzertest-x/ayse --session ayse --steps schritte.json

   Schritte (Liste, eins pro Objekt):
     {"go": "/"}                         Adresse öffnen (relativ zu --base oder absolut)
     {"tap": "Ferien-Radar"}             Link/Knopf mit diesem sichtbaren Text antippen (erster Treffer;
                                         {"tap": "OK", "nth": 1} für den zweiten)
     {"choose": "Dein Bundesland", "option": "Nordrhein-Westfalen"}   Auswahlfeld per Beschriftung
     {"fill": "Wartezeit", "value": "…"} Textfeld per Beschriftung
     {"see": true}                       Bildschirm lesen: Überschriften, Text, Links, Knöpfe
     {"shot": "01-start"}                Bildschirmfoto (nur sichtbarer Teil, wie am Handy) nach --out
     {"shot": "01-start", "full": true}  ganze Seite
     {"scroll": "bottom"} / {"scroll": 600}
     {"offline": true} / {"offline": false}   Netz weg bzw. wieder da
     {"back": true} / {"reload": true} / {"wait": 1500}
     {"expect": "Kapıkule"}              steht der Text sichtbar da? (ja/nein ins Protokoll)
     {"note": "…"}                       eigener Vermerk im Protokoll

   --session hält Service Worker, Speicher und Einstellungen zwischen zwei Aufrufen (Profil unter
   <out>/profil-<session>), so wie ein echtes Handy die App beim nächsten Öffnen noch kennt.
   --slow simuliert ein langsames Mobilnetz (ca. 400 kbit/s, 400 ms Latenz). */
"use strict";

const fs = require("fs");
const path = require("path");

const DEVICES = {
  // Mittelklasse-Android und iPhone; isMobile/hasTouch wie im Gerät, Schrift der Seite unverändert
  android: { viewport: { width: 360, height: 740 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true,
             userAgent: "Mozilla/5.0 (Linux; Android 14; SM-A546B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Mobile Safari/537.36" },
  iphone: { viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true,
            userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1" },
  klein: { viewport: { width: 320, height: 640 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
           userAgent: "Mozilla/5.0 (Linux; Android 10; SM-A105F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36" },
  desktop: { viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1, isMobile: false, hasTouch: false },
};

function args(argv) {
  const out = {};
  for (let i = 2; i < argv.length; i++) {
    const key = argv[i].replace(/^--/, "");
    const next = argv[i + 1];
    if (next === undefined || next.startsWith("--")) { out[key] = true; } else { out[key] = next; i++; }
  }
  return out;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const clip = (text, n) => (text.length > n ? text.slice(0, n) + " …[gekürzt]" : text);

async function screen(page) {
  // Was ein Mensch auf der Seite lesen kann: sichtbarer Text von <main> (sonst body), Überschriften,
  // antippbare Links/Knöpfe und Formularfelder mit Beschriftung
  return page.evaluate(() => {
    const visible = (el) => {
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
    };
    const root = document.querySelector("main") || document.body;
    const text = (root.innerText || "").replace(/\n{3,}/g, "\n\n").trim();
    const heads = [...document.querySelectorAll("h1, h2, h3")].filter(visible).map((h) => h.tagName + " " + h.innerText.trim());
    const taps = [...document.querySelectorAll("a[href], button, summary, [role=button]")].filter(visible)
      .map((el) => (el.innerText || el.getAttribute("aria-label") || "").trim().replace(/\s+/g, " "))
      .filter(Boolean);
    const fields = [...document.querySelectorAll("select, input:not([type=hidden]), textarea")].filter(visible)
      .map((el) => {
        // Beschriftung ohne das Feld selbst (bei <label><select>…</select></label> sonst alle Optionen)
        let label = el.getAttribute("aria-label") || el.name || "";
        if (el.labels && el.labels[0]) {
          const copy = el.labels[0].cloneNode(true);
          copy.querySelectorAll("select, input, textarea").forEach((n) => n.remove());
          label = copy.textContent.trim() || label;
        }
        let value = el.tagName === "SELECT" ? (el.selectedOptions[0] || {}).text : el.value;
        if (el.type === "radio" || el.type === "checkbox") value = el.checked ? "gewählt" : "nicht gewählt";
        return label.replace(/\s+/g, " ") + " = " + (value || "(leer)");
      });
    return { title: document.title, lang: document.documentElement.lang, heads, text, taps: [...new Set(taps)], fields };
  });
}

async function byText(page, text, nth) {
  // Wie ein Mensch: zuerst Links/Knöpfe mit diesem Namen, dann irgendein sichtbarer Text
  for (const role of ["link", "button"]) {
    const loc = page.getByRole(role, { name: text });
    if (await loc.count()) return loc.nth(nth || 0);
  }
  const loc = page.getByText(text, { exact: false });
  if (await loc.count()) return loc.nth(nth || 0);
  throw new Error("Nichts mit dem Text „" + text + "“ gefunden");
}

async function settle(page) {
  try { await page.waitForLoadState("load", { timeout: 15000 }); } catch (e) { /* weiter, Protokoll zeigt den Stand */ }
  await sleep(400);
}

async function main() {
  const a = args(process.argv);
  if (!a.base || !a.out || !a.steps) {
    console.error("Aufruf: user.js --base URL --out ORDNER --steps schritte.json [--device android|iphone|klein|desktop] [--lang de|tr] [--session NAME] [--slow]");
    process.exit(2);
  }
  const steps = JSON.parse(fs.readFileSync(a.steps, "utf8"));
  const device = DEVICES[a.device || "android"];
  if (!device) throw new Error("Unbekanntes Gerät: " + a.device);
  fs.mkdirSync(a.out, { recursive: true });
  const { chromium } = require(require.resolve(process.env.PLAYWRIGHT_MODULE || "playwright"));
  const options = Object.assign({}, device, {
    locale: a.lang === "tr" ? "tr-TR" : "de-DE",
    timezoneId: "Europe/Berlin",
    serviceWorkers: "allow",
  });
  let context;
  let browser = null;
  if (a.session) {
    context = await chromium.launchPersistentContext(path.join(a.out, "profil-" + a.session), options);
  } else {
    browser = await chromium.launch();
    context = await browser.newContext(options);
  }
  const page = context.pages()[0] || (await context.newPage());
  const problems = [];
  page.on("pageerror", (err) => problems.push("JS-Fehler: " + err.message));
  page.on("console", (msg) => { if (msg.type() === "error") problems.push("Konsole: " + msg.text()); });
  if (a.slow) {
    const cdp = await context.newCDPSession(page);
    await cdp.send("Network.emulateNetworkConditions",
      { offline: false, latency: 400, downloadThroughput: 50000, uploadThroughput: 25000 });
  }
  const logFile = path.join(a.out, "protokoll.jsonl");
  const log = (entry) => {
    const line = JSON.stringify(Object.assign({ zeit: new Date().toISOString() }, entry));
    fs.appendFileSync(logFile, line + "\n");
    console.log(line);
  };

  for (const [i, step] of steps.entries()) {
    const t0 = Date.now();
    const entry = { schritt: i + 1, tun: step };
    try {
      if (step.go) {
        const url = new URL(step.go, a.base).toString();
        const res = await page.goto(url, { waitUntil: "load", timeout: 30000 }).catch((e) => { throw e; });
        entry.status = res ? res.status() : "aus dem Speicher";
      } else if (step.tap) {
        await (await byText(page, step.tap, step.nth)).click({ timeout: 10000 });
        await settle(page);
      } else if (step.choose) {
        const field = page.getByLabel(step.choose).first();
        try {
          await field.selectOption({ label: step.option }, { timeout: 5000 });
        } catch (e) {
          // Wie ein Mensch, der die Liste aufklappt: zeigen, was zur Wahl steht
          const options = await field.evaluate((el) => [...(el.options || [])].map((o) => o.text)).catch(() => null);
          throw new Error(options ? "„" + step.option + "“ steht nicht zur Wahl. Optionen: " + options.join(" | ")
                                  : "Kein Auswahlfeld mit der Beschriftung „" + step.choose + "“");
        }
        await settle(page);
      } else if (step.fill) {
        await page.getByLabel(step.fill).first().fill(String(step.value), { timeout: 10000 });
      } else if (step.see) {
        const s = await screen(page);
        entry.bildschirm = Object.assign({}, s, { text: clip(s.text, Number(a.max || 4000)) });
      } else if (step.shot) {
        const file = path.join(a.out, step.shot.replace(/[^A-Za-z0-9._-]/g, "_") + ".png");
        await page.screenshot({ path: file, fullPage: Boolean(step.full) });
        entry.datei = file;
      } else if (step.scroll !== undefined) {
        await page.evaluate((v) => window.scrollTo(0, v === "bottom" ? document.body.scrollHeight : Number(v)), step.scroll);
        await sleep(300);
      } else if (step.offline !== undefined) {
        await context.setOffline(Boolean(step.offline));
      } else if (step.back) {
        await page.goBack({ waitUntil: "load" });
      } else if (step.reload) {
        await page.reload({ waitUntil: "load" });
      } else if (step.wait) {
        await sleep(Number(step.wait));
      } else if (step.expect) {
        entry.sichtbar = await page.getByText(step.expect, { exact: false }).first().isVisible().catch(() => false);
      } else if (step.note) {
        entry.vermerk = step.note;
      } else {
        throw new Error("Unbekannter Schritt");
      }
      entry.ok = true;
    } catch (e) {
      entry.ok = false;
      entry.fehler = String(e.message || e).split("\n")[0];
    }
    entry.url = page.url();
    entry.ms = Date.now() - t0;
    if (problems.length) entry.probleme = problems.splice(0);
    log(entry);
  }
  await context.close();
  if (browser) await browser.close();
}

main().catch((e) => { console.error(e); process.exit(1); });
