# tatilvakti – Sıla yolu

Reisevorbereitung für die Fahrt oder den Flug von Deutschland in die Türkei. Zweisprachig (DE/TR), mobil zuerst, als PWA auch offline nutzbar. Ohne Login, ohne Cookies, ohne Paid-APIs.

Die App ist wie die Reise selbst aufgebaut. Der Startscreen ist eine Routenlinie mit vier Stationen:

| Station | Feature | Was es löst |
|---|---|---|
| 1 · Wann? | **Ferien-Radar** | Alle 16 Bundesländer auf einer Zeitachse, eigenes Land hervorgehoben. „Ferien-Druck“ = Anteil der Bevölkerung, deren Land an diesem Tag Schulferien hat (gewichtet nach Destatis-Einwohnerzahl). Für das eigene Land: Abreise- und Rückreisetage mit der kleinsten Reisewelle (siehe [Reisetage](#reisetage-im-ferien-radar)), dazu Ramazan- und Kurban Bayramı im Diagramm. Jeder Ferienzeitraum ist eine eigene Seite (`/de/ferien/sommer-2027`, `/tr/tatil/yaz-2027`). Teilen per WhatsApp: voller Ländername, Ferientermine und je Richtung der beste Tag mit Reisewelle. |
| 2 · Welche Strecke? | **Route & Vignetten** | 3 Autorouten im Vergleich (über Ungarn/Serbien, Slowenien/Kroatien/Serbien, Schengen-Route über Rumänien). Zahl der Grenzkontrollen mit Live-Status, Mautsystem je Land, **nur offizielle Shops** (Schutz vor Resellern), Checkliste für Papiere und Ausstattung. |
| 3 · Grenze live | **Wartezeiten aus der Community** | Melden in drei Schritten: Richtung, Wartezeit, senden. Die Startseite bietet die 4 Hauptübergänge zur Auswahl, der zuletzt gemeldete steht vorn, die Richtung der letzten Meldung wird vorgeschlagen. Gezeigt werden der Median der letzten 2 Std., die Zahl der Meldungen und das Alter der letzten Meldung. Ohne Meldung in 2 Std. erscheint eine Zeile mit Aufruf zum Melden statt grauer Chips. Dazu der Tagesverlauf aus der Historie, Ferien-Wellen und Links zu Behörden und Automobilclubs, auch auf Startseite und Übersicht. Funktioniert auch an der Grenze ohne Netz: Meldungen landen in einer Warteschlange und werden nachgeliefert. |
| 4 · Was darf mit? | **Zoll & Koffer** | Beide Richtungen (rein in die TR, zurück in die EU). Ampel *erlaubt / mit Grenze / beachten / verboten*, Suche unabhängig von Umlauten und Akzenten („kasar“ findet „Kaşar“). Enthält Handy/IMEI (120 Tage, Gebühr 2026), Bargeld, Gold, Sucuk und Käse, Auto-Regeln. Jede Regel mit Quelle (amtlich, wo möglich; Grüne Karte: GDV, Auto in der Türkei zusätzlich ADAC) und per `#Anker` teilbar. |

### Reisetage im Ferien-Radar

Für das gewählte Bundesland zeigt das Radar je Richtung bis zu 3 Tage mit der kleinsten **Reisewelle**. Das ist eine Schätzung aus Ferienterminen und Einwohnerzahlen, keine Stau-Messung.

- **Freier Block** eines Landes: Ferien plus angrenzende Wochenenden und bundesweite Feiertage. Je Land und Zeitraum gibt es genau einen.
- **Reisewelle** an einem Tag: Bevölkerungsanteil (Destatis-Gewichte) der Länder, deren freier Block an diesem Tag oder in den 2 Tagen davor beginnt (Abreise) bzw. an diesem Tag oder in den 2 Tagen danach endet (Rückreise). Das eigene Land zählt mit.
- **Kandidaten:** je Richtung die Tage vom Blockrand an, die höchstens 1/6 der freien Tage kosten. Vom Urlaub bleiben so mindestens 2/3; bei 6 Wochen geht es bis zu 7 Tage später los bzw. früher zurück. Tage vor heute entfallen. Liegen alle Kandidaten hinter uns, sagt die Karte das.
- **Ruhig** ist ein Tag nur, wenn das eigene Land nicht in der Welle ist und die Welle höchstens 25 % beträgt. Sortiert wird nach Welle, bei Gleichstand gewinnt der Tag näher am Blockrand. Jeder Tag steht mit Prozent und Einordnung da („Ferienstart: BW“, „Ferienende in 8 Ländern“).
- **Kein ruhiger Tag:** Dann steht dort ehrlich „Kein ruhiger Abreisetag …“ bzw. „Kein ruhiger Rückreisetag …“ mit dem am wenigsten vollen Tag. Bei Herbst-, Winter-, Oster- und Pfingstferien ist das der Normalfall.
- Teilen-Text und Link-Vorschau nennen je Richtung den besten Tag mit Prozent.
- Die **Ferien-Wellen** auf der Grenzseite beruhen auf denselben Blöcken. Datum ist der erste bzw. letzte freie Tag, z. B. NRW Sommer 2027 am Sa 17.07., nicht am Mo 19.07.
- Bayram-Reisen rechnet die Reisewelle nicht ein. Fällt ein Bayram in die freien Tage, sagt die Karte das.
- Stellschrauben: `WAVE_DAYS` (3), `SHIFT_DIVISOR` (6) und `QUIET_MAX` (0,25) in `holidays.py`, Mindestlänge eines Blocks `MIN_FREE_DAYS` (8) in `content.py`.

## Ehrlichkeitsregeln (im Code durchgesetzt)

- **Keine erfundenen Zahlen.** Alle kuratierten Werte stehen in `tatilvakti/data/*.json` und tragen Quelle, Prüfdatum (`as_of`) und Ablaufdatum (`review_after`; Preise zusätzlich `prices_valid_until`). Ist ein Datum überschritten, zeigt die App „Prüfung fällig“ bzw. streicht Preise durch, statt sie als aktuell auszugeben. `/healthz` meldet das ebenfalls.
- **Wartezeiten stammen nur von Reisenden.** Die App gibt sie nicht als Behördendaten aus. Gezeigt werden Bereiche statt Scheinpräzision. Ohne Meldung der letzten 2 Std. erscheint „Keine aktuellen Meldungen“ plus Alter der letzten. Im Median zählt je Anschluss nur dessen jüngste Meldung. Bei gerader Anzahl gilt der höhere Median-Wert, also lieber vorsichtig.
- **Ehrliche Reisetage.** Das Radar empfiehlt keine „ruhigsten Tage nach Ferien-Druck“, sondern Tage nach Reisewelle. Ein Tag, an dem das eigene Land selbst los- oder zurückfährt, gilt nie als ruhig. Gibt es keinen ruhigen Tag, sagt die App das.
- **Kein Preisversprechen.** Ferien-Druck und Reisewelle sind nachprüfbare Nachfrage-Indikatoren, keine Flugpreis- oder Stauprognose. Das steht auch in der App.
- **Prozent in Landesschreibweise:** DE „12,6 %“ mit geschütztem Leerzeichen, TR „%12,6“ (`i18n.fmt_pct`).
- **Degraded statt Fehlerseite.** Offline oder bei Serverfehlern liefert der Service Worker den letzten gespeicherten Stand mit sichtbarem Alter. Die API antwortet dann mit `{"error": "offline", "degraded": true}`.
- **Kerninfos ohne JavaScript.** Alle Seiten sind serverseitig gerendert. JS ergänzt nur (Personalisierung, Live-Refresh, Suche, Offline-Queue, Teilen-Menü). Das Melde-Formular funktioniert auch ohne JS (POST mit Redirect).

## Architektur

```
Browser (PWA)                              Server (kleiner VPS)
───────────────                            ─────────────────────────────────────
SSR-HTML  ◀── network-first (4 s) ─────── Flask + Jinja (DE /de/…, TR /tr/…)
app.js    ◀── cache-first (Hash-URLs) ──── static/ (CSS, JS, Icons, Vorschaubilder)
sw.js     ◀── generiert: Version + ─────── /sw.js (Asset-Hashes + Datenstand
              Precache-Liste                → neue Version bei jeder Änderung)
fetch     ◀─▶ /api/v1/borders ──────────── SQLite (WAL): reports, kv;
                                            Tagesschlüssel in eigener Datei
localStorage (tv.*): Bundesland,           data/*.json: Ferien und Bayram-Termine,
  Reiseart, Häkchen, Warteschlange,          Zoll, Transit, Übergänge, alte URLs
  letzte Meldung (Übergang, Richtung),       (validiert beim Start)
  Hinweis geschlossen
```

- **Stack:** Python 3.10+, Flask, gunicorn, SQLite. Sonst keine Abhängigkeiten, kein Build-Step, keine externen Skripte oder Fonts. Node und Playwright braucht es nur für Browser-Tests und die Vorschaubilder.
- **Adressen und Sprachen:** lokalisierte Slugs (`/de/ferien` ↔ `/tr/tatil`), Sprachwechsel als EU-Nummernschild (D | TR), der Wechsel behält `?land=`. Jeder Ferienzeitraum ist eine eigene Seite: `/de/ferien/<slug>` bzw. `/tr/tatil/<slug>`, z. B. `/de/ferien/sommer-2027` und `/tr/tatil/yaz-2027`. `/de/ferien` zeigt weiter den laufenden bzw. nächsten Zeitraum. Alte Links mit `?zeitraum=<id>` und Slugs der anderen Sprache leiten per 301 auf den Pfad weiter, ein unbekannter Slug (z. B. ein entfernter Zeitraum) per 302 auf `/de/ferien` bzw. `/tr/tatil`; `?land=` bleibt jeweils erhalten. Canonical, `hreflang` und `og:url` stehen ohne `?land=`. `hreflang` lautet `de` bzw. `tr` (ohne Land), dazu `x-default` (DE). Die Sitemap enthält alle Seiten beider Sprachen außer der Offline-Seite, auch jeden Zeitraum und jeden Übergang.
- **Texte:** Ein Test erzwingt identische Schlüssel und Platzhalter in `de.json` und `tr.json`. Die Datenvalidierung verlangt jeden kuratierten Text in beiden Sprachen, auch Quellen- und Shop-Namen. Türkisch durchgehend in der sen-Form.
- **Sicherheit:** strikte CSP ohne `unsafe-inline` (auch keine Inline-Styles; die SVG-Diagramme nutzen nur Attribute), `Referrer-Policy: same-origin` (fremde Seiten erhalten keinen Referrer, eigene Formulare und API-Aufrufe tragen Origin bzw. Referer, die der CSRF-Schutz gegen den eigenen Host prüft; mit `no-referrer` schickten Browser `Origin: null` und keinen Referer, das Formular ohne JS würde abgelehnt), keine Cookies (ein Test prüft das auf jeder Seite). app.js übernimmt `?land=` und den gespeicherten Wert `tv.state` nur als zweistelliges Kürzel, Werte in Selektoren laufen über `CSS.escape()`.
- **Spam-Schutz ohne IP-Speicherung:** Aus der IP bildet die App mit einem täglich wechselnden Zufallsschlüssel drei HMAC-Prüfwerte: je Client (`client`: IPv4-Adresse bzw. IPv6-/64), je Anschluss (`net`: IPv4-Adresse bzw. IPv6-/56) und je Netz (`block`: IPv4-Adresse bzw. IPv6-/48). Die Prüfwerte werden nach 48 h gelöscht. Es gibt nur den Schlüssel des aktuellen UTC-Tages: Die erste Meldung nach 00:00 UTC legt den neuen an und löscht ältere. Ohne Meldungen erledigt das der nächste Wartungslauf (Timer alle 5 Min., bei Verkehr zusätzlich die App). Die Schlüssel liegen in einer eigenen Datei (`TV_SALT_DB_PATH`, in Produktion auf tmpfs), Sicherungen enthalten weder Schlüssel noch Prüfwerte. Limits: je Client 1 Meldung pro Übergang und Richtung in 20 Min. und 10 Meldungen pro Stunde, je Anschluss 3 pro Stunde je Übergang und Richtung, je Übergang und Richtung insgesamt 30 in 10 Min. und davon höchstens 10 aus einem IPv6-/48 (darüber jeweils HTTP 429 mit `detail: "crossing_busy"`, eigenem Hinweis, Log-Warnung und einen Tag lang dem Pflegehinweis `crossing_cap` in `/healthz`). So füllt ein einzelnes /48 mit seinen 256 Anschlüssen die Obergrenze nicht allein und sperrt die übrigen Melder nicht aus. Im Median zählt je Übergang und Richtung nur die jüngste Meldung je Anschluss (IPv4-Adresse bzw. IPv6-/56), und aus einem IPv6-/48 höchstens 2 Anschlüsse: die mit den jüngsten Meldungen (`BLOCK_VOTES`). Dazu ein Honeypot-Feld. Grenzen: Wer viele IPv4-Adressen oder mehrere /48 hat, bekommt mehrere Stimmen und kann die Obergrenze füllen. Ein einzelnes /48 kann den Status bestimmen, solange höchstens zwei andere Stimmen da sind. Menschen hinter CGNAT oder im selben /56 teilen sich die Limits und eine Stimme, im selben /48 (z. B. Mobilfunkkunden desselben Anbieters) die 2 Stimmen; ihre Meldungen werden alle gespeichert. Der Tagesverlauf rechnet mit allen gespeicherten Meldungen, Spam darin räumt `purge-reports` auf.

### PWA und Offline

- **Service Worker:** `/sw.js` erzeugt der Server: `self.TV_CONFIG` (Build-ID, Assets mit Hash, Seitenliste, Pflichtseiten) vor `static/js/sw.js`. Die Build-ID ist ein Hash über Assets, Templates, Texte, Daten und Code. Eine Cache-Version zählt niemand von Hand hoch. Ausnahme: `data/redirects.json` zählt nicht zur Build-ID (siehe [Alte URLs](#alte-urls-dataredirectsjson)).
- **Vorab gespeichert** werden je Sprache die 7 festen Seiten (samt Offline-Seite), jeder Ferienzeitraum als eigener Pfad und jeder Grenzübergang, also 2 × (7 + Zeiträume + Übergänge). Beim Datenstand dieses Commits (7 Zeiträume, 9 Übergänge) sind das 46 Seiten.
- **Update ohne Verlust des Offline-Stands:** Der Install ist eine Transaktion. Assets, `/de/`, `/tr/`, `/de/offline` und `/tr/cevrimdisi` sind Pflicht. Fehlt eins, scheitert der Install, und der bisherige Worker bleibt samt Stand aktiv; der Browser versucht es bei der nächsten Update-Prüfung erneut. Optionale Seiten, die nicht laden, übernimmt `activate` aus dem vorigen Seiten-Cache.
- **Caches** heißen alle `tv2-*`. Beim Aktivieren löscht v2 alle anderen Caches des Origins, auch die der Alt-App.
- **Strategien:** Seiten network-first mit 4 s Zeitlimit, bei Netzfehler oder 5xx der gespeicherte Stand. Assets mit Hash cache-first. `/api/` network-first, offline `{"error": "offline", "degraded": true}` (503). Letzter Rückfall ist eine zweisprachige Minimalseite.
- **Warteschlange:** Eine Meldung ohne Netz bleibt auf dem Gerät und geht raus, sobald das Gerät online und die App offen ist, solange sie höchstens 90 Min. alt ist. Bei Netzfehler oder 5xx legt app.js sie zurück, statt sie zu verlieren. Bei 4xx (zu alt, Limit, ungültig) wird sie verworfen.
- **Startbildschirm-Hinweis** („Öffnet an der Grenze auch ohne Netz“): nur auf Startseite, Grenz-Übersicht und Übergangsseiten, erst nach Wahl des Bundeslandes oder einer angenommenen Meldung, nie in der installierten App und nicht mehr, nachdem man ihn geschlossen hat (`tv.a2hs_off`). Chromium: eigener Knopf (`beforeinstallprompt`), iOS: Kurzanleitung über „Teilen“. Auf niedrigen Bildschirmen kompakt, Esc schließt ihn. Einen Besuchszähler gibt es nicht. `tv.visits` und `tv.seen_at` älterer Versionen löscht app.js beim Laden.
- **iOS:** Die Info-Seite (`/de/info#offline`) erklärt die 7-Tage-Regel von Safari: Website-Daten verschwinden nach 7 Tagen Safari-Nutzung ohne Besuch, bei Apps auf dem Home-Bildschirm nicht. In der installierten App bittet app.js den Browser um dauerhaften Speicher (`navigator.storage.persist()`).
- **Gespeichert im Browser** (`localStorage`, Schlüssel `tv.*`): Bundesland (`state`), Reiseart (`mode`), Häkchen der Checkliste (`checks`), Warteschlange (`queue`), Übergang und Richtung der letzten angenommenen Meldung (`report_pref`, erst nach HTTP 201) und „Hinweis geschlossen“ (`a2hs_off`). Dazu die Offline-Kopie im Cache des Service Workers. Der Datenschutztext listet genau das; `tests/test_share.py` gleicht ihn mit app.js ab.
- **Ablösung der Alt-App:** Kill-Switch für alte Service Worker über `TV_LEGACY_SW_PATHS` (siehe [Konfiguration](#konfiguration)), alte URLs über `data/redirects.json`. Ablauf und Entscheidungen: [docs/MIGRATION.md](docs/MIGRATION.md).

### Teilen und Link-Vorschau

- WhatsApp-Knöpfe sind direkte `wa.me`-Links mit Text und URL. Das Teilen-Menü des Geräts ist ein eigener Knopf, sichtbar nur mit `navigator.share`. Keine Tracking-Parameter.
- Teilbar sind das Ferien-Radar (mit Bundesland: voller Ländername, Ferientermine, je Richtung der beste Tag), jeder Übergang (gemeldete Richtungen, ohne Meldung ein Aufruf zum Melden), die Routenseite und jede Zoll-Regel (Link mit `#Anker`).
- Jede Seite trägt Open-Graph- und Twitter-Tags, `og:url` ist kanonisch. Die Ferienseite mit `?land=` nimmt die Zusammenfassung des Landes als Beschreibung. Absolute URLs kommen aus `TV_BASE_URL`, ohne diesen Wert aus dem Host der Anfrage. In Produktion muss `TV_BASE_URL` deshalb stimmen, sonst zeigen Vorschauen auf den internen Host.
- Vorschaubilder: `static/og/og-de.png` und `og-tr.png` (1200 × 630, PNG, unter 300 KB; ein Test prüft das). Vorlage und Skript liegen in `scripts/og/` (`og.html`, `og.json` mit den i18n-Schlüsseln, `render.js`, `rendered-texts.json`). Nach Änderungen an `tagline`, `hol_title`, `r_title`, `b_title`, `nav_customs` oder `og_foot` die Bilder neu rendern:

  ```bash
  NODE_PATH=/pfad/zu/node_modules node scripts/og/render.js     # z. B. /opt/node-tools/node_modules
  ```

  `tests/test_share.py` vergleicht `rendered-texts.json` mit den aktuellen Texten und schlägt fehl, wenn das Neurendern vergessen wurde. Das Rendern braucht Playwright für Node und die Schriften Inter bzw. Inter Display (fehlen sie, nimmt der Browser eine andere). Beides ist keine Laufzeit-Abhängigkeit.

### Browser-Unterstützung (CSS)

- Auswahlzustände, Fokus und alle Farben kommen ohne `:has()`, `color-mix()` und `:focus-visible` aus. Ziel sind auch iOS 12–15 und Android 5/6.
- Gemischte Farben sind feste Tokens, definiert in `:root` und im Dark-Block von `app.css`: `--bg-a88`, `--brand-line` (45 % `--brand` + 55 % `--line`), `--none-a25`, `--ok-a25`, `--mid-a25`, `--bad-a25`, `--accent-a15`, `--accent-a45`. Wer eine Grundfarbe ändert, muss diese Tokens in beiden Blöcken nachziehen, sonst schlägt `tests/test_css.py` fehl.
- Die `fill`-Attribute im Ferien-Diagramm (`holidays.html`, Farben `#a33c16`, `#8a5300`, `#0e6b6b`) sind ein Rückfall ohne CSS und folgen nicht automatisch den Tokens.
- Neuere Selektoren (`:has()`, `:focus-visible`, `:is()`, `:where()`) nie in eine Selektorliste mit anderen schreiben: Ein unbekannter Selektor verwirft die ganze Regel. Der Test prüft das.
- **Zoll-Toolbar:** Ist sie sichtbar, setzt app.js `html.has-toolbar`. Sprungziele danach bekommen 128 px `scroll-margin-top`, den Tastaturfokus schiebt app.js darunter (WCAG 2.4.11). Wird die Toolbar höher (z. B. mehr Filter), den Wert in `app.css` anpassen; `tests/e2e/toolbar_layout.js` meldet das.

### Dateien

```
tatilvakti/
  __init__.py      App-Factory, Datenvalidierung beim Start, Asset-Hashing, Build-ID, Impressum-Prüfung
  views.py         Seiten, PWA (/sw.js, Manifest, Kill-Switch), SEO (Sitemap, robots), alte URLs, /healthz
  api.py           /api/v1/borders, /api/v1/borders/<id>, POST …/reports
  cli.py           Befehle: flask --app tatilvakti maintenance | purge-reports
  borders.py       Meldungen, Median, Rate-Limits, Tagesverlauf, Datensparsamkeit
  holidays.py      Ferien-Druck, Überlappung, Reisewellen (Reisetage, Ferien-Wellen), Bayram-Termine
  content.py       Laden + Validieren der kuratierten Daten und der Weiterleitungen
  db.py            SQLite: Haupt-DB und Schlüssel-DB
  i18n.py          Texte, Sprachwahl, Slugs, Datums- und Prozentformat, Such-Normalisierung
  data/            holidays.json, customs.json, transit.json, crossings.json, redirects.json
  i18n/            de.json, tr.json
  templates/       Jinja-Templates (SSR), legacy_sw.js (Kill-Switch)
  static/          css/app.css, js/app.js, js/sw.js, Icons, og/ (Vorschaubilder)
tests/             pytest (Daten, Logik, HTTP, PWA, Teilen, Texte, CSS, Betriebsskripte)
tests/e2e/         Browser-Tests (Node-Playwright, nur mit TV_E2E=1)
deploy/            systemd-Units (Dienst, Wartung, Backup) und Env-Vorlage
scripts/           deploy.sh, rollback.sh, release-lib.sh, healthcheck.py, preflight.py, backup.py, tv-flask.sh
scripts/og/        Vorlage und Render-Skript der Vorschaubilder
docs/MIGRATION.md  Ablösung der Alt-App: Entscheidungen, Umschalten, Prüfliste, Rollback
```

## Setup (Entwicklung)

```bash
python3 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/flask --app tatilvakti run --debug     # http://127.0.0.1:5000
```

**Abhängigkeiten:** Direkt verwendete Pakete stehen in `requirements.in` (Laufzeit) und `requirements-dev.in` (plus pytest). Die `.txt`-Dateien pinnen alle Pakete samt Abhängigkeiten mit Hashes. Aktualisieren mit [uv](https://docs.astral.sh/uv/), danach Tests laufen lassen:

```bash
uv pip compile requirements.in --universal --python-version 3.10 --generate-hashes -o requirements.txt
uv pip compile requirements-dev.in --universal --python-version 3.10 --generate-hashes -o requirements-dev.txt
```

### Tests

```bash
.venv/bin/python -m pytest                       # alles ohne Browser, die Browser-Tests werden übersprungen
TV_E2E=1 NODE_PATH=/pfad/zu/node_modules .venv/bin/python -m pytest -m e2e   # nur die Browser-Tests (ca. 1 Min.)
```

- **pytest:** `test_data.py` (Datensätze und Validierung), `test_holidays.py` (Ferien-Druck, Reisewelle, Reisetage), `test_borders.py` (Meldungen, Median, Limits, Datensparsamkeit), `test_web.py` (Seiten, API, CSP, keine Cookies), `test_pwa.py` (Service Worker, Kill-Switch, alte URLs, Startbildschirm-Hinweis), `test_share.py` (Startseite, Melden, Teilen, Link-Vorschau, Impressum, Datenschutztext gegen den Code), `test_texts.py`, `test_css.py`, `test_cli.py`, `test_scripts.py` (Units, Backup, Preflight, Env-Vorlage).
- **`test_texts.py`** durchsucht das TR-HTML aller Seiten nach typischen deutschen Wörtern (`GERMAN_WORDS`; Ausnahmen in `PROPER_NAMES`, amtliche deutsche Bezeichnungen wie „Zulassungsbescheinigung Teil I“) und prüft alle TR-Texte auf sen-Form und Schreibweise („resmî“).
- **Browser-Tests:** `tests/test_e2e.py` startet für jedes Skript in `tests/e2e/` eine eigene App (leere Temp-DB, feste Uhr 20.07.2027, Kill-Switch unter `/service-worker.js` und `/app/sw.js`) und führt es mit Node aus. Playwright für Node muss über `NODE_PATH` oder `PLAYWRIGHT_MODULE` (Pfad zum Modul `playwright`) auffindbar sein, die Browser wie bei Playwright üblich (`PLAYWRIGHT_BROWSERS_PATH`). Node und Playwright sind keine Laufzeit-Abhängigkeit. Ohne `TV_E2E=1` werden die Tests übersprungen.

  | Skript | prüft |
  |---|---|
  | `service_worker.js` | erster Besuch und offline, Update bei schlechtem Netz (Proxy simuliert Netzverlust, Deploy, 503) |
  | `report_queue.js` | Meldung ohne Netz: Warteschlange, Nachliefern, sichtbarer Status |
  | `layout_hint.js` | 320 px, Startbildschirm-Hinweis (iOS, Chromium, installiert, ohne JS) |
  | `kill_switch.js` | alte Worker unter `TV_LEGACY_SW_PATHS`: abgemeldet, Caches gelöscht, `tv2-*` bleibt |
  | `old_browsers.js` | Auswahl, Fokus und Diagramm ohne `:has()`, `color-mix()`, `:focus-visible`, ohne CSS-Variablen und ohne CSS |
  | `bad_params.js` | präparierte `?land=`-Links und kaputter Gerätespeicher |
  | `toolbar_layout.js` | Sprungziele und Fokus unter der Zoll-Toolbar; jede Seite der Precache-Liste bei 320, 360 und 390 px ohne seitliches Überlaufen |
  | `share_report.js` | Kaltstart ohne Meldungen, Auswahl und Melden, Teilen |

## Betrieb

| | |
|---|---|
| Dienst | `tatilvakti-v2.service`: gunicorn auf `127.0.0.1:3096`, Benutzer `tatilvakti-v2` |
| Code | `/opt/tatilvakti-v2/releases/<UTC-Zeit>-<commit>`, jedes Release mit eigenem venv. Aktiv ist der Symlink `current`. |
| Daten | `/var/lib/tatilvakti-v2/tatilvakti.db`, Sicherungen in `backups/` |
| Tagesschlüssel | `/run/tatilvakti-v2/salts.db` (tmpfs, wird nie gesichert) |
| Konfiguration | `/etc/tatilvakti-v2.env`, Vorlage `deploy/tatilvakti-v2.env.example` |
| Timer | `tatilvakti-v2-maintenance.timer` (alle 5 Min.), `tatilvakti-v2-backup.timer` (nachts) |

v2 läuft **parallel** zur Alt-App (`tatilvakti.service`, Port 3095) und berührt sie nicht. Die Ablösung, also Umschalten in Pangolin, Prüfliste, Rollback und Entscheidungen zu den Alt-Diensten, beschreibt [docs/MIGRATION.md](docs/MIGRATION.md).

### Einrichten (einmalig)

```bash
sudo apt install python3-venv git                     # Debian/Ubuntu
sudo useradd --system --user-group --no-create-home --home-dir /nonexistent \
     --shell /usr/sbin/nologin tatilvakti-v2
sudo git clone <repo-url> /opt/tatilvakti-v2/src && cd /opt/tatilvakti-v2/src
sudo install -m 0640 -o root -g tatilvakti-v2 deploy/tatilvakti-v2.env.example /etc/tatilvakti-v2.env
sudoedit /etc/tatilvakti-v2.env                       # Impressum eintragen
sudo cp deploy/tatilvakti-v2*.service deploy/tatilvakti-v2*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo scripts/deploy.sh                                # erstes Release, startet noch nichts
sudo systemctl enable --now tatilvakti-v2.service tatilvakti-v2-maintenance.timer tatilvakti-v2-backup.timer
curl -s http://127.0.0.1:3096/healthz
```

Die Units schreiben nur in ihre eigenen Verzeichnisse (`StateDirectory`, `RuntimeDirectory`). Code und Konfiguration sind für den Dienst schreibgeschützt.

### Update und Rollback

```bash
cd /opt/tatilvakti-v2/src && sudo git pull
sudo scripts/deploy.sh                    # Optionen: --rev <tag|commit>, --no-tests, --keep <n>
```

`deploy.sh` baut ein neues Release aus dem eingecheckten Stand (`git archive`), installiert die Pakete nur mit passenden Hashes und startet dann den **Preflight** (`scripts/preflight.py`) als Dienstbenutzer. Der Preflight prüft die Konfiguration, ruft `create_app()` gegen eine Kopie der Produktions-DB auf, lädt alle Seiten und die Offline-Liste des Service Workers und lässt `pytest` laufen. Erst danach schaltet `deploy.sh` den Symlink `current` um und startet den Dienst neu. Meldet `/healthz` danach nicht die neue Build-ID, geht es automatisch auf das vorige Release zurück, und `deploy.sh` startet dieses neu. Das klappt auch, wenn systemd den Dienst nach mehreren Fehlstarts schon aufgegeben hat (`failed`, start-limit-hit): Vor jedem Neustart läuft `systemctl reset-failed`. Ein kaputter Datenstand erreicht so nie die laufende Seite. `deploy.sh` und `rollback.sh` werten die Antwort von `/healthz` auch bei HTTP 503 aus (`scripts/healthcheck.py`): Steht `db` unter `down`, gilt das Release als nicht gesund. Steht dort nur `salt_db`, gibt es eine WARNUNG und keinen Rollback: Die Seiten laufen, Meldungen scheitern, und die Ursache liegt meist außerhalb des Releases (`/run`). Die letzte Antwort steht bei einem Fehlschlag in der Ausgabe.

Geänderte Units in `deploy/` übernimmt `deploy.sh` nicht selbst, es weist nur darauf hin. Dann die Dateien erneut nach `/etc/systemd/system/` kopieren, `sudo systemctl daemon-reload` ausführen und die betroffenen Units neu starten.

Der Neustart unterbricht die Seite für wenige Sekunden. Nach jeder Änderung an Daten, Texten, Templates oder Code lädt jedes Gerät die Offline-Daten neu. Solche Deploys deshalb nicht mitten in einer Ferienwelle einspielen. Ausnahme ist `data/redirects.json`: Ein Deploy, der nur diese Datei ändert, behält die Build-ID. `deploy.sh` prüft dann nur, dass der Dienst wieder antwortet.

```bash
sudo /opt/tatilvakti-v2/current/scripts/rollback.sh            # zurück auf das vorige Release
sudo /opt/tatilvakti-v2/current/scripts/rollback.sh --list     # Releases anzeigen
sudo /opt/tatilvakti-v2/current/scripts/rollback.sh <release>  # ein bestimmtes Release
```

`rollback.sh` startet den Dienst neu, wenn er läuft, gerade startet oder abgestürzt ist (`failed`). Einen bewusst gestoppten Dienst startet es nur mit `--start`. `deploy.sh` und `rollback.sh` laufen nie gleichzeitig (Sperre `/opt/tatilvakti-v2/.lock`).

Zurück zur Alt-App: [docs/MIGRATION.md → Rollback](docs/MIGRATION.md#7-rollback).

### Befehle

`scripts/tv-flask.sh` führt `flask --app tatilvakti <befehl>` als Dienstbenutzer aus, mit derselben Env-Datei und denselben Pfaden wie der Dienst. Lokal ohne Wrapper: `.venv/bin/flask --app tatilvakti <befehl>`.

```bash
# Datensparsamkeit sofort ausführen (läuft sonst alle 5 Min. per Timer und bei Verkehr in der App)
sudo /opt/tatilvakti-v2/current/scripts/tv-flask.sh maintenance

# Meldungen gezielt löschen, z. B. nach Spam. Erst mit --dry-run zählen.
sudo /opt/tatilvakti-v2/current/scripts/tv-flask.sh purge-reports --crossing kapikule \
     [--direction to_tr|to_de] --since 2026-10-06T14:00 [--until 2026-10-06T16:00] [--dry-run]
```

`purge-reports` löscht nach Eingangszeit der Meldung (`since` ≤ t < `until`, ohne `--until` bis jetzt) und gibt die Anzahl aus. Zeiten im Format ISO 8601. Ohne Zeitzone gilt deutsche Zeit.

### Konfiguration

| Variable | Bedeutung |
|---|---|
| `TV_BASE_URL` | Öffentliche URL für Canonical, `hreflang`, Sitemap, Teilen-Links und Link-Vorschau: `https://tatilvakti.guenlab.de`. Muss in Produktion stimmen, sonst zeigen Vorschauen auf den internen Host. Beginnt sie mit `https://`, sendet die App HSTS. |
| `TV_TRUST_PROXY` | Anzahl vertrauenswürdiger Reverse-Proxys, damit der Spam-Schutz die echte Client-IP sieht (hinter Pangolin: `1`). Prüfen: MIGRATION.md → Prüfliste e) |
| `TV_OPERATOR_NAME`, `TV_OPERATOR_ADDRESS`, `TV_OPERATOR_EMAIL` | Impressum (§ 5 DDG). Erscheint nur, wenn alle drei gesetzt sind; die Anschrift braucht mindestens eine Zeile, Zeilen mit `;` trennen. Sonst steht auf der Info-Seite „noch nicht eingerichtet“, ein halbes Impressum gibt es nicht. Dieselbe Prüfung liefert `/healthz` → `imprint_ok`. Ist `TV_BASE_URL` gesetzt und das Impressum unvollständig, warnt die App beim Start im Journal. |
| `TV_DB_PATH` | Haupt-DB (Standard: `instance/tatilvakti.db`). In Produktion setzt die Unit den Wert, nicht die Env-Datei. |
| `TV_SALT_DB_PATH` | Tagesschlüssel (Standard: neben `TV_DB_PATH` als `…-salts.db`). In Produktion setzt die Unit `/run/tatilvakti-v2/salts.db` (tmpfs). |
| `TV_LEGACY_SW_PATHS` | Kommagetrennte Pfade alter Service-Worker-Skripte, z. B. `/service-worker.js`. Unter jedem liefert v2 einen Kill-Switch aus (MIGRATION.md, Abschnitt 3); Antwort 200, `application/javascript`, `Cache-Control: no-store`, `Service-Worker-Allowed: /`. Regeln: absolut, endet auf `.js`, ohne Query oder Fragment, nur `A–Z a–z 0–9 . _ ~ @ + - /`, keine doppelten Pfade, keine Kollision mit eigenen Routen (`/sw.js`, `/static/…`, `/de/grenze/x.js` usw.). Bei einem Fehler startet die App nicht, der Preflight meldet dann „create_app() schlägt fehl: …“. |
| `GUNICORN_CMD_ARGS` | Optional für die Umstiegsphase: Access-Log ohne IP (Beispiel in der Env-Vorlage) |

### Reverse-Proxy (Pangolin/Traefik)

- Ziel `127.0.0.1:3096` auf Hermes. HTTPS ist Pflicht, sonst funktioniert der Service Worker nicht, HTTP leitet auf HTTPS um.
- HSTS (`max-age=31536000`, ohne `includeSubDomains`) setzt die App selbst, sobald `TV_BASE_URL` mit `https://` beginnt. Im Proxy nicht zusätzlich setzen.
- Host weitergeben (`X-Forwarded-Host`): Der CSRF-Schutz vergleicht `Origin` mit dem Host bzw. `TV_BASE_URL`.
- **Kompression einschalten.** Sie spart rund 79 % der Offline-Daten, die jedes Gerät nach einem Deploy lädt (laut Audit-Messung rund 1,2 MB → 0,26 MB). Weder die App noch gunicorn komprimieren, und Pangolin komprimiert ab Werk nicht ([Diskussion #3158](https://github.com/orgs/fosrl/discussions/3158)). Ein Schalter pro Ressource ist bisher nur vorgeschlagen ([PR #3579](https://github.com/fosrl/pangolin/pull/3579): Entwurf, nicht gemergt, Stand Oktober 2026). Deshalb die Traefik-Middleware [`compress`](https://doc.traefik.io/traefik/middlewares/http/compress/) verwenden, z. B. für den ganzen Entrypoint:

  ```yaml
  # traefik_config.yml (statisch): wirkt für alle Ressourcen dieses Entrypoints
  entryPoints:
    websecure:
      http:
        middlewares: [compress@file]
  # dynamic_config.yml
  http:
    middlewares:
      compress:
        compress: {}
  ```

  Prüfen: `curl -s -o /dev/null -D - -H 'Accept-Encoding: gzip' https://tatilvakti.guenlab.de/de/ferien | grep -i content-encoding`
- **Keinen Health-Check in Pangolin/Traefik auf `/healthz` legen.** `/healthz` liefert HTTP 503 schon, wenn nur das Melden ausfällt (z. B. Schlüssel-DB), die Seiten laufen dann weiter. Ein Health-Check nähme die ganze Seite vom Netz. Falls einer nötig ist, auf `/de/` richten (siehe Logs und Monitoring).
- Pangolin protokolliert pro Anfrage IP und URL (Request-Log), also auch `?land=`. Aufbewahrung und Auftragsverarbeitung: MIGRATION.md, Abschnitt 2.6.

### Logs und Monitoring

- `journalctl -u tatilvakti-v2`: App-Meldungen ab INFO (z. B. eingegangene Meldungen ohne IP, Warnungen) und gunicorn-Fehler. gunicorn schreibt kein Access-Log. Wartung und Backup: `journalctl -u tatilvakti-v2-maintenance` bzw. `-u tatilvakti-v2-backup`.
- Warnungen, die man beobachten sollte (alle ohne IP und ohne Prüfwert): „Impressum unvollständig: …“ (beim Start), „Obergrenze erreicht: …“ und „Netz-Anteil an der Obergrenze erreicht: …“ (möglicher Spam aus vielen Adressen bzw. aus einem IPv6-/48, dann `purge-reports`), „Anfrage mit X-Forwarded-For, aber TV_TRUST_PROXY=0 …“, „Haupt-DB (TV_DB_PATH) nimmt keine Meldungen an …“, „Schlüssel-DB (TV_SALT_DB_PATH) nicht nutzbar …“, „Wartung fehlgeschlagen: …“, „Unlesbare Client-Adresse …“.
- Die Aufbewahrung im Journal gilt für den ganzen Host. Empfehlung: begrenzen, z. B. `MaxRetentionSec=14day` in `/etc/systemd/journald.conf.d/retention.conf`.
- `/healthz` trennt Ausfall und Pflege (immer `Cache-Control: no-store`):
  - **HTTP 503**, `status` `down`, sobald Melden nicht geht. Die Gründe stehen in `down`: `db` (die Haupt-DB nimmt keine Meldung an: nicht lesbar, schreibgeschützt, Schreibsperre länger belegt, als eine Meldung wartet, oder Schreiben scheitert, z. B. bei voller Platte) und `salt_db` (Schlüssel-DB nicht nutzbar). Die Seiten laufen dabei meist weiter.
  - **HTTP 200**, `status` `attention`, wenn nur etwas zu pflegen ist (Gründe in `attention`: `due_items` fällige Datenprüfung, `imprint` unvollständiges Impressum, `proxy` Proxy-Fehlkonfiguration, `maintenance` Wartung seit über 30 Min. nicht gelaufen, `crossing_cap` Obergrenze oder Netz-Anteil in den letzten 24 h erreicht: Log-Warnung prüfen, bei Spam `purge-reports`), sonst `status` `ok`.
  - Außerdem `db` und `salt_db` (je `true`/`false`), `build` (Build-ID), `datasets.*.review_due`, `next_review` (nächstes Prüfdatum, gut für eine Kalender-Erinnerung), `maintenance_at` und `crossing_cap_at` (zuletzt eine Obergrenze erreicht).
  - **Details nur auf dem Server:** Alles oben sieht nur ein Abruf auf dem Server selbst, z. B. `curl -s http://127.0.0.1:3096/healthz` (so fragen auch `deploy.sh` und `rollback.sh`). Von außen, erkannt an `X-Forwarded-For` des Proxys bzw. an einer fremden Adresse, kommen nur `status`, `build` und `down` mit demselben Statuscode. Proxy-Einstellung, Zustand des Spam-Schutzes und Pflegehinweise bleiben so intern.
  - Die Prüfung schreibt nicht bei jedem Abruf: Sie liest `reports`, holt die Schreibsperre wie eine Meldung (`BEGIN IMMEDIATE`, ein `INSERT` ohne Zeile, `ROLLBACK`) und stößt die Wartung an, die über alle Prozesse höchstens alle 10 Min. in `kv` schreibt. Hält ein anderer Prozess die Sperre, wartet die Prüfung so lange wie eine Meldung (5 s) und meldet `db` genau dann, wenn auch die Meldung scheitern würde.
  - **Uptime-Monitor:** `https://tatilvakti.guenlab.de/healthz` nur auf den Statuscode prüfen (200 gut, alles andere Alarm). Für die Pflegehinweise eignet sich ein täglicher Blick oder eine zweite, seltenere Stichwortprüfung auf `"status":"ok"` (die Antwort ist kompaktes JSON, `status` steht auch in der Antwort von außen); welcher Hinweis es ist, zeigt der Abruf auf dem Server.
  - Keinen Health-Check des Reverse-Proxys auf `/healthz` legen, der bei Fehlern die ganze Seite vom Netz nimmt: Bei `salt_db` laufen die Seiten noch. Falls einer nötig ist, auf eine Seite wie `/de/` richten.

### Backup und Restore

- `tatilvakti-v2-backup.timer` sichert nachts um 03:40 Ortszeit per SQLite-Online-Backup nach `/var/lib/tatilvakti-v2/backups/tatilvakti-<UTC-Zeit>.db`. Ein `cp` der Datei würde im WAL-Modus die jüngsten Meldungen verlieren. Die Kopie behält von jeder Meldung nur Übergang, Richtung, Wartezeit-Bereich und Zeitpunkte. Alles andere wird geleert: die Prüfwerte (`client`, `net`, `block`) und auch Spalten oder Tabellen, die `backup.py` nicht kennt (dann mit WARNUNG im Journal). Die Tagesschlüssel liegen in einer eigenen Datei und werden nie gesichert. Sofort sichern: `sudo systemctl start tatilvakti-v2-backup.service`.
- **Aufbewahrung:** lokal 14 Tage (`--keep-days` in der Unit). Der Datenschutztext nennt diese Frist, `tests/test_share.py` gleicht sie mit der Unit ab. Zusätzlich empfohlen: eine Kopie außerhalb des Hosts, z. B. 30 Tage. Dafür nur `backups/` kopieren, nie die laufende DB samt `-wal`/`-shm` und nie `/run/tatilvakti-v2`. Wer extern sichert, muss diese Frist im Datenschutztext ergänzen (`i_privacy_reports_keep` in `de.json` und `tr.json`).
- **Restore** (einmal testen):

  ```bash
  B=/var/lib/tatilvakti-v2/backups/tatilvakti-<zeit>.db
  sudo -u tatilvakti-v2 /opt/tatilvakti-v2/current/.venv/bin/python \
       /opt/tatilvakti-v2/current/scripts/backup.py --verify "$B"
  # Dienst und Timer anhalten: Die Wartung (alle 5 Min.) legte sonst in der Lücke eine leere DB an
  sudo systemctl stop tatilvakti-v2-maintenance.timer tatilvakti-v2-backup.timer
  sudo systemctl stop tatilvakti-v2-maintenance.service tatilvakti-v2-backup.service tatilvakti-v2.service
  # bisherige tatilvakti.db samt -wal/-shm beiseitelegen, nach erfolgreichem Restore löschen
  sudo install -m 0600 -o tatilvakti-v2 -g tatilvakti-v2 "$B" /var/lib/tatilvakti-v2/tatilvakti.db
  sudo systemctl start tatilvakti-v2.service tatilvakti-v2-maintenance.timer tatilvakti-v2-backup.timer
  ```

- Neue Tabellen oder Spalten im Schema (`tatilvakti/db.py`) muss `scripts/backup.py` kennen: als Inhalt (`KEEP_TABLES`, `REPORT_COLUMNS`) oder als Prüfwert bzw. Schlüssel (`HASH_COLUMNS`, `SECRET_TABLES`). Sonst schlägt `tests/test_scripts.py` fehl und damit auch der Preflight von `deploy.sh`.
- `backup.py` und `preflight.py` öffnen die Produktions-DB nur als deren Eigentümer. Als root angelegte `-wal`/`-shm`-Dateien könnte der Dienst sonst nicht mehr beschreiben.

## Datenpflege

Nach jeder Änderung `pytest` laufen lassen. Die App startet nicht, wenn ein Datensatz fehlerhaft ist. So geht nie ein kaputter Stand live.

### Allgemein

- Jeder Datensatz hat `meta.as_of` und `meta.review_after`, Zoll-Regeln und Übergänge zusätzlich einzeln `review_after`, Vignettenpreise `prices_valid_until`. `/healthz` → `due_items` und `next_review` (Abruf auf dem Server, siehe Logs und Monitoring) eignen sich für ein Monitoring.
- Jede Quelle und jeder Shop hat `"name": {"de": …, "tr": …}` und eine https-URL: in `holidays.json` (`meta.sources`, `meta.population_source`, `bayrams.source`), `customs.json` (`sources`), `transit.json` (`shop`, `source`, optional `source` an `notes` und `documents`) und `crossings.json` (`sources`). Einsprachige Namen lehnt die Validierung ab.
- Preise (`value`) sind entweder eine reine Zahl mit Währung („9,60 €“, „6.900 Ft“, „30 Lei“, „54.258 TL“) oder `{de, tr}`, sobald Worte dabei sind, z. B. `{"de": "ca. 6.900 Ft", "tr": "yaklaşık 6.900 Ft"}`.

### Ferien (`data/holidays.json`)

- Enthält Herbst 2026, Weihnachten 2026/27, Winter 2027, Ostern 2027, Himmelfahrt/Pfingsten 2027, Sommer 2027 und Sommer 2028, jeweils alle 16 Länder. Einmal im Jahr ergänzen. Ein Land ohne Ferien im Zeitraum bekommt eine leere Liste `[]`, der Schlüssel bleibt Pflicht.
- **Slug:** Jeder Zeitraum braucht `"slug": {"de": "<id>", "tr": "<türkisch>"}`, z. B. `{"de": "sommer-2027", "tr": "yaz-2027"}`. Erlaubt sind a–z, 0–9 und einzelne Bindestriche. Ein Slug gehört über beide Sprachen zu genau einem Zeitraum und darf nicht die `id` eines anderen sein. Den DE-Slug gleich der `id` wählen, damit alte `?zeitraum=`-Links 1:1 passen.
- **Freie Blöcke:** Je Land und Zeitraum ist nur ein freier Block (Ferien plus angrenzende Wochenenden und bundesweite Feiertage) erlaubt, getrennte Blöcke lehnt die Validierung ab. Blöcke unter 8 freien Tagen bleiben draußen (`MIN_FREE_DAYS`); die App sagt das auf der persönlichen Karte („keine längeren Ferien … kurze Ferien von höchstens einer Woche führt das Radar nicht“). Freie Blöcke eines Landes aus zwei verschiedenen Zeiträumen dürfen nicht aneinanderstoßen, dann als einen Zeitraum erfassen. Außerdem prüft die Validierung Überschneidungen je Land und Zeiträume ganz ohne Ferienland.
- **Abgelaufene Zeiträume** dürfen aus `holidays.json` verschwinden. Ihr Pfad `/de/ferien/<slug>` leitet dann per 302 auf `/de/ferien` weiter und behält `?land=`. Für Ferienpfade ist kein Eintrag in `redirects.json` nötig, er wäre auch nicht erlaubt (eigene Route).
- **Bayram-Termine** (optionaler Abschnitt `bayrams`): `{as_of, source: {name: {de, tr}, url}, items: [{id, kind: "ramazan" | "kurban", label: {de, tr}, arife, start, end}]}`, Daten im Format `JJJJ-MM-TT`. Die Validierung verlangt: Arife = Tag vor dem ersten Festtag, Ramazan 3 und Kurban 4 Festtage, https-Quelle, gültiges `as_of`. Eingetragen sind nur Ramazan Bayramı 2027 (Arife 08.03., Festtage 09.–11.03.) und Kurban Bayramı 2027 (Arife 15.05., Festtage 16.–19.05.), Quelle Diyanet (`namazvakitleri.diyanet.gov.tr/en-US/dini-gunler`). Die App zeigt einen Bayram nur, wenn er in freie Blöcke eines Zeitraums fällt.
- **Nachtragen:** Kommen Zeiträume von 2028 hinzu, die Bayram-Termine 2028 laut Diyanet ergänzen. Anhaltspunkte ohne Beleg: Ramazan Ende Februar, Kurban Anfang Mai 2028.

### Grenzübergänge (`data/crossings.json`)

- Je Übergang Pflicht: `name_loc {de, tr}` für Sätze wie „Wie lange hast du in Kapıkule gewartet?“. DE „in <Kurzname>“, TR Kurzname + `'` + da/de/ta/te nach Vokalharmonie, z. B. `Kapıkule'de`, `İpsala'da`. Die Validierung prüft nur die Form, die Endung pflegt man von Hand.
- Je Quelle Pflicht: `label {de, tr}` im Format „Land: Stelle“ (z. B. „Ungarn: Polizei (Határinfo)“). Es steht auf Startseite, Übersicht und Übergangsseite, der volle Name auf der Info-Seite.
- `main: true` markiert die Hauptübergänge (Startseite, Auswahl beim Melden).
- Ohne diese Felder startet die App nicht.

### Zoll, Vignetten

- Pro Eintrag Quelle und `review_after`, Preise mit `prices_valid_until`. `/healthz` → `datasets.*.review_due` (Abruf auf dem Server) eignet sich für ein Monitoring.

### Alte URLs (`data/redirects.json`)

Weiterleitungen für Pfade der Alt-App. Eingabe ist Anhang A in [docs/MIGRATION.md](docs/MIGRATION.md).

```json
{"meta": {"as_of": "2026-10-07"},
 "redirects": [
   {"from": "/grenzen", "to": "/de/grenze", "code": 301, "note": "Search Console"},
   {"from": "/go/vignette-at", "to": null, "code": 410}
 ]}
```

- Greift nur, wo v2 sonst 404 antwortet. Ein abschließender Slash spielt keine Rolle, die Query der Anfrage wird ignoriert. Das Ziel darf Query und Anker haben (`/de/info#impressum`). Pfade aus Search Console und Proxy-Logs dürfen prozentkodiert eingetragen werden (`/%C3%BCber-uns`), kodierte und unkodierte Form gelten als dieselbe URL.
- 410 zeigt die normale Fehlerseite in der Sprache des Pfads, sonst aus `Accept-Language`. Unter `/api/` kommt JSON `{"error": "gone"}`.
- **Validierung beim Start:** Pfadform (beginnt mit `/`, ohne Query, Fragment und Leerzeichen), nur interne Ziele, keine Duplikate, keine Ketten, nichts unter `/api/v1/` oder `/static/`, nicht `/`. Dazu ein Abgleich mit der echten Antwort von v2: Die Quelle muss ohne Tabelle 404 liefern, auch unter `/de/grenze/<alter-übergang>`, das Ziel direkt 200. Die App startet also nicht bei Tippfehler-Zielen, POST-Routen, `/de` (308), Zielen, die selbst weiterleiten, Ferienpfaden und Kill-Switch-Pfaden.
- Die Datei ändert die Build-ID nicht. Geräte laden danach also nicht alle Offline-Seiten neu, `/healthz` zeigt aber dieselbe Build-ID wie vorher. Ob die Einträge greifen, prüft MIGRATION.md, Prüfliste g).

## Vor dem Launch prüfen

Die Entwicklungsumgebung hatte keinen direkten Zugriff auf die offiziellen Seiten. Die Werte sind per Websuche recherchiert und nur übernommen, wo mehrere Quellen übereinstimmen. Gegenzuprüfen sind:

**Daten**

1. **Ferientermine** (je 2 unabhängige Quellen pro Land und Zeitraum): Stichprobe gegen kmk.org.
2. **Bayram-Termine 2027:** Die Diyanet-Seite war gesperrt. Die Termine sind nur über Presseberichte belegt, die sich auf den Diyanet-Kalender berufen. Einmal an `namazvakitleri.diyanet.gov.tr/en-US/dini-gunler` gegenprüfen.
3. **IMEI:** Gebühr 2026 54.258 TL laut Resmî Gazete (Harçlar Kanunu Genel Tebliği Seri No: 98, Sayı 33124 vom 31.12.2025). Der Hinweis, dass Roaming bzw. eine ausländische eSIM die 120-Tage-Regel nicht auslöst, beruht auf übereinstimmenden Berichten, nicht auf einem BTK-Dokument; so steht es auch in der App. Offen: Laut Suche und Presseberichten gilt für die Registrierung eine Frist von 365 Tagen ab Einreise, und die Nutzung lässt sich per e-Devlet auf 180 Tage verlängern. Beides fehlt bewusst in der App, bis es jemand auf `mcks.gov.tr` bestätigt.
4. **Auto in der Türkei:** Die Regel, dass die Familie das Auto nur fahren darf, solange der Halter in der Türkei ist, am Original von Gümrük Genel Tebliği Seri No: 9 gegenlesen (`ticaret.gov.tr`).
5. **Vignetten und Maut:** AT (ASFINAG-Pressemeldung), SI 16 € / 7 Tage, HU 6.900 Ft (D1, 10 Tage, laut NÚSZ), BG in Euro seit 01.01.2026 und Preise laut BG TOLL ab 01.08.2026, RO neue Tarife seit 01.10.2026 und Verkauf über TollRo (`etoll.ro`). Dass in RO ohne Angabe der Euro-Norm der teuerste Tarif gilt, stützt sich auf Zusammenfassungen des Ordin MTI 888/2026, nicht auf den Wortlaut im Monitorul Oficial.
6. **Kroatien:** Maut ohne Mautstellen (CroLibertas) laut Daten ab 01.03.2027. Termin beobachten.

**Links und Texte**

7. **Quellen-Links von Hand durchklicken:** alle Links auf der Info-Seite „Datenstand & Quellen“ (`/de/info`), die Shops auf der Routenseite (u. a. `nemzetiutdij.hu`, `etoll.ro`, `bgtoll.bg`, `crolibertas.hr`) und die Infos zu den Übergängen (Határinfo, `mvr.bg/gdgp`, `trakya.ticaret.gov.tr`, AMSS, HAK). Keiner davon war aus der Entwicklungsumgebung erreichbar. Wo ein Deep-Link nicht prüfbar war, steht eine Einstiegsseite.
8. **Türkisch von Muttersprachlern gegenlesen** lassen: `tr.json` und alle `tr`-Felder in `data/*.json`. Die Tests prüfen nur sen-Form, Schreibweise und bekannte Fehler.
9. **Link-Vorschau** nach dem Umschalten einmal mit WhatsApp und dem Facebook Sharing Debugger testen (Bild, Titel, URL auf `TV_BASE_URL`).

**Betrieb**

10. **Impressum** (alle drei `TV_OPERATOR_*`), `TV_BASE_URL` und `TV_TRUST_PROXY` gesetzt: `/healthz` auf dem Server (`curl -s http://127.0.0.1:3096/healthz`) → `status` `ok`, `imprint_ok` `true`.
11. **Datenschutztext:** Empfänger (Hoster, Pangolin) und die konkrete Log-Frist des Proxys fehlen noch. Der Betreiber muss sie liefern (MIGRATION.md 2.6), danach in `de.json` und `tr.json` nachtragen.
12. **Ablösung der Alt-App:** Bestandsaufnahme und Entscheidungen aus [docs/MIGRATION.md](docs/MIGRATION.md) (Newsletter, Push, alte URLs, Service Worker, Proxy-Logs).

**Offene Rückfragen an den Betreiber**

- Obergrenze 30 Meldungen je Übergang und Richtung in 10 Min., davon höchstens 10 aus einem IPv6-/48: passend für Spitzentage an Kapıkule? Wie viele Reisende sich bei den großen Mobilfunkanbietern ein /48 teilen, ist nicht gemessen; die Log-Warnung „Netz-Anteil an der Obergrenze erreicht“ bzw. `crossing_cap` in `/healthz` an Spitzentagen beobachten.
- Stimmen je IPv6-/48 im Median (`BLOCK_VOTES` in `tatilvakti/borders.py`, jetzt 2, nicht gemessen): Ohne diese Grenze zählte jeder Anschluss (/56) eines /48 einzeln, ein /48 aus einem Tunnel-Angebot hatte also so viele Stimmen, wie es Meldungen durch die Limits brachte. 1 wäre gegen ein solches /48 am strengsten, ließe aber Mobilfunkkunden, die sich ein /48 teilen, nur eine gemeinsame Stimme; mehr ist fairer für sie, gibt aber auch einem Troll mit eigenem /48 mehr Gewicht. Bei 2 bestimmt ein /48 den Status nur, solange höchstens zwei andere Stimmen da sind. Passt das?
- Deploy-Weg: `deploy.sh` baut aus einem Git-Klon auf Hermes und braucht dort Zugriff auf PyPI. Passt das, oder soll von außen deployt werden?
- Welche Pangolin-Version läuft, und ist das Request-Log für die Ressource aktiv (MIGRATION.md 2.6)?
- `scripts/tv-flask.sh` (systemd-run) ist nur syntaktisch geprüft: auf Hermes einmal `sudo /opt/tatilvakti-v2/current/scripts/tv-flask.sh maintenance` ausführen.
- Pflichtausstattung und Lichtpflicht fehlen einzeln für AT, SI, HR, HU, RO und BG. Die App verweist dafür allgemein auf die ÖAMTC-Übersicht.

## Bewusst später (Roadmap)

- **Offizielle Wartezeit-Quellen als Adapter:** z. B. die ungarische Polizei (Határinfo: Röszke, Tompa, Ásotthalom). Nicht in v1, weil die Seiten aus der Entwicklungsumgebung nicht erreichbar waren und ein ungetesteter Scraper „halb fertig“ wäre. Der Datenfluss ist darauf vorbereitet (`source` im API-JSON).
- Push-Alarm „Kapıkule unter 1 Std.“ (Web-Push, VAPID, ohne Konto).
- Automatischer Ferien-Import (z. B. OpenHolidays-API) als Ergänzung der kuratierten Daten.
- Kurze Ferien (unter 8 freien Tagen) im Radar darstellen, statt sie wegzulassen.
- Packliste, eSIM-Vergleich und Affiliate-Chips, jeweils mit klarer Kennzeichnung „Anzeige“ und `rel="sponsored"`.
- Optionale KI-Fragen, nie als Gate vor Kerninfos.
