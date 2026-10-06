# tatilvakti – Sıla yolu

Reisevorbereitung für die Fahrt oder den Flug von Deutschland in die Türkei. Zweisprachig (DE/TR), mobil zuerst, als PWA auch offline nutzbar. Ohne Login, ohne Cookies, ohne Paid-APIs.

Die App ist wie die Reise selbst aufgebaut. Der Startscreen ist eine Routenlinie mit vier Stationen:

| Station | Feature | Was es löst |
|---|---|---|
| 1 · Wann? | **Ferien-Radar** | Alle 16 Bundesländer auf einer Zeitachse, eigenes Land hervorgehoben. „Ferien-Druck“ = Anteil der Bevölkerung, deren Land an diesem Tag Ferien hat (gewichtet nach Destatis-Einwohnerzahl). Ruhigste Abreise- und Rückreisetage innerhalb der eigenen Ferien, angrenzende Wochenenden eingerechnet. Teilen per WhatsApp. |
| 2 · Welche Strecke? | **Route & Vignetten** | 3 Autorouten im Vergleich (über Ungarn/Serbien, Slowenien/Kroatien/Serbien, Schengen-Route über Rumänien). Zahl der Grenzkontrollen mit Live-Status, Mautsystem je Land, **nur offizielle Shops** (Schutz vor Resellern), Papiere-Checkliste. |
| 3 · Grenze live | **Wartezeiten aus der Community** | Ein Tipp zum Melden („30–60 Min.“). Gezeigt werden der Median der letzten 2 Std., die Zahl der Meldungen und das Alter der letzten Meldung. Dazu der Tagesverlauf aus der Historie, Ferien-Wellen und Links zu offiziellen Live-Quellen. Funktioniert auch an der Grenze ohne Netz: Meldungen landen in einer Warteschlange und werden nachgeliefert. |
| 4 · Was darf mit? | **Zoll & Koffer** | Beide Richtungen (rein in die TR, zurück in die EU). Ampel *erlaubt / mit Grenze / beachten / verboten*, Suche unabhängig von Umlauten und Akzenten („kasar“ findet „Kaşar“). Enthält Handy/IMEI (120 Tage, Gebühr 2026), Bargeld, Gold, Sucuk und Käse, Auto-Regeln. Jede Regel mit offizieller Quelle. |

## Ehrlichkeitsregeln (im Code durchgesetzt)

- **Keine erfundenen Zahlen.** Alle kuratierten Werte stehen in `tatilvakti/data/*.json` und tragen Quelle, Prüfdatum (`as_of`) und Ablaufdatum (`review_after`; Preise zusätzlich `prices_valid_until`). Ist ein Datum überschritten, zeigt die App „Prüfung fällig“ bzw. streicht Preise durch, statt sie als aktuell auszugeben. `/healthz` meldet das ebenfalls.
- **Wartezeiten stammen nur von Reisenden.** Die App gibt sie nicht als Behördendaten aus. Gezeigt werden Bereiche statt Scheinpräzision. Ohne Meldung der letzten 2 Std. erscheint „Keine aktuellen Meldungen“ plus Alter der letzten. Bei gerader Anzahl gilt der höhere Median-Wert, also lieber vorsichtig.
- **Kein Preisversprechen.** Der Ferien-Druck ist ein nachprüfbarer Nachfrage-Indikator, keine Flugpreisprognose. Das steht auch in der App.
- **Degraded statt Fehlerseite.** Offline oder bei Serverfehlern liefert der Service Worker den letzten gespeicherten Stand mit sichtbarem Alter. Die API antwortet dann mit `{"error": "offline", "degraded": true}`.
- **Kerninfos ohne JavaScript.** Alle Seiten sind serverseitig gerendert. JS ergänzt nur (Personalisierung, Live-Refresh, Suche, Offline-Queue). Das Melde-Formular funktioniert auch ohne JS (POST mit Redirect).

## Architektur

```
Browser (PWA)                              Server (kleiner VPS)
───────────────                            ─────────────────────────────────────
SSR-HTML  ◀── network-first (4 s) ─────── Flask + Jinja (DE /de/…, TR /tr/…)
app.js    ◀── cache-first (Hash-URLs) ──── static/ (CSS, JS, Icons, ohne Build)
sw.js     ◀── generiert: Version + ─────── /sw.js (Asset-Hashes + Datenstand
              Precache-Liste                → neue Version bei jeder Änderung)
fetch     ◀─▶ /api/v1/borders ──────────── SQLite (WAL): reports, salts, kv
localStorage: Bundesland, Reiseart,         data/*.json: Ferien, Zoll, Transit,
  Checkliste, Offline-Meldungen               Übergänge (validiert beim Start)
```

- **Stack:** Python 3.10+, Flask, gunicorn, SQLite. Sonst keine Abhängigkeiten, kein Build-Step, keine externen Skripte oder Fonts.
- **i18n:** lokalisierte Slugs (`/de/ferien` ↔ `/tr/tatil`), `hreflang`, Sprachwechsel als EU-Nummernschild (D | TR). Ein Test erzwingt identische Schlüssel und Platzhalter in `de.json` und `tr.json`; die Datenvalidierung verlangt jeden kuratierten Text in beiden Sprachen.
- **Service Worker:** Der Server generiert ihn mit Build-ID (Hash über Assets und Datenstand). Man muss nie manuell eine Cache-Version hochzählen. Vorab gespeichert werden alle Seiten in beiden Sprachen, alle Ferienzeiträume und alle Grenzübergänge (42 Seiten).
- **Sicherheit:** strikte CSP ohne `unsafe-inline` (auch keine Inline-Styles; die SVG-Diagramme nutzen nur Attribute), `Referrer-Policy: no-referrer`, keine Cookies (ein Test prüft das auf jeder Seite).
- **Spam-Schutz ohne IP-Speicherung:** HMAC(IP) mit täglich wechselndem Zufallsschlüssel. Der Prüfwert wird nach 48 h gelöscht, der Schlüssel nach 2 Tagen. Limits: 1 Meldung pro Übergang und Richtung in 20 Min., 10 Meldungen pro Stunde. Dazu ein Honeypot-Feld.

```
tatilvakti/
  __init__.py      App-Factory, Datenvalidierung beim Start, Asset-Hashing
  views.py         Seiten, PWA (/sw.js, Manifest), SEO (sitemap, robots), /healthz
  api.py           /api/v1/borders, /api/v1/borders/<id>, POST …/reports
  borders.py       Meldungen, Median, Rate-Limits, Tagesverlauf, Datensparsamkeit
  holidays.py      Ferien-Druck, Überlappung, ruhigste Tage, Ferien-Wellen
  content.py       Laden + Validieren der kuratierten Daten
  i18n.py          Texte, Sprachwahl, Datumsformat, Such-Normalisierung
  data/            holidays.json, customs.json, transit.json, crossings.json
  i18n/            de.json, tr.json
  templates/       Jinja-Templates (SSR)
  static/          app.css, app.js, sw.js, Icons
tests/             pytest (Daten, Logik, HTTP, Datenschutz, CSP)
deploy/            systemd-Beispiel
```

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest              # 80 Tests
.venv/bin/flask --app tatilvakti run --debug     # http://127.0.0.1:5000
```

Produktion (Beispiel, auch in `deploy/tatilvakti.service`):

```bash
.venv/bin/pip install -r requirements.txt
TV_DB_PATH=/var/lib/tatilvakti/tatilvakti.db TV_TRUST_PROXY=1 \
  .venv/bin/gunicorn --workers 2 --threads 4 --bind 127.0.0.1:3095 wsgi:app
curl -s http://127.0.0.1:3095/healthz
```

| Variable | Bedeutung |
|---|---|
| `TV_DB_PATH` | SQLite-Datei (Standard: `instance/tatilvakti.db`) |
| `TV_TRUST_PROXY` | Anzahl vertrauenswürdiger Reverse-Proxys, damit der Spam-Schutz die echte Client-IP sieht (hinter Pangolin/Traefik/Caddy: `1`) |
| `TV_BASE_URL` | Öffentliche URL für Canonical, `hreflang`, Sitemap und Share-Links, z. B. `https://tatilvakti.guenlab.de` |
| `TV_OPERATOR_NAME`, `TV_OPERATOR_ADDRESS`, `TV_OPERATOR_EMAIL` | Impressum (§ 5 DDG). Adresszeilen mit `;` trennen. Solange leer, zeigt die Info-Seite einen sichtbaren Hinweis. |

**Betrieb:** Die SQLite-Datei sichern, das genügt. Den Access-Log des Reverse-Proxys möglichst ohne IP oder gekürzt führen; die App selbst loggt keine IPs. HTTPS ist Pflicht, sonst funktioniert der Service Worker nicht.

## Datenpflege

- **Ferien:** `data/holidays.json` enthält Herbst 2026, Weihnachten 2026/27, Ostern 2027, Sommer 2027 und Sommer 2028, jeweils alle 16 Länder. Einmal im Jahr ergänzen; nach jeder Änderung `pytest` laufen lassen (die Validierung prüft Vollständigkeit und Plausibilität).
- **Zoll, Vignetten, Übergänge:** pro Eintrag Quelle und `review_after`. `/healthz` → `datasets.*.review_due` eignet sich für ein Monitoring.
- Die App startet nicht, wenn ein Datensatz fehlerhaft ist. So geht nie ein kaputter Stand live.

## Vor dem Launch bitte prüfen

Die Entwicklungsumgebung hatte keinen direkten Zugriff auf die offiziellen Seiten. Die Werte sind per Websuche recherchiert und nur übernommen, wo mehrere Quellen übereinstimmen. Gegenzulesen sind:

1. **Ferientermine** (je 2 unabhängige Quellen pro Land und Zeitraum): Stichprobe gegen kmk.org.
2. **IMEI-Gebühr 2026: 54.258 TL** (mehrere türkische Quellen, u. a. Vodafone TR). Der Hinweis, dass Roaming bzw. eine ausländische eSIM die 120-Tage-Regel nicht auslöst, beruht auf übereinstimmenden Berichten, nicht auf einem BTK-Dokument. In der App ist das so formuliert.
3. **Vignettenpreise 2026:** AT (ASFINAG-Pressemeldung), SI 16 € / 7 Tage, HU „ca. 6.900 Ft“ (Quellen nannten 6.900 bzw. 6.910 Ft), BG in Euro seit 01.01.2026, RO neue Tarife seit 01.10.2026.
4. **Kroatien:** Umstellung auf schrankenlose E-Maut läuft, laut Presse sollen die Mautstellen 2027 wegfallen. Termin beobachten.
5. **Shop-URLs** von HU (`nemzetiutdij.hu`) und RO (`cnadnr.ro`) einmal im Browser öffnen.

## Bewusst später (Roadmap)

- **Offizielle Wartezeit-Quellen als Adapter:** z. B. die ungarische Polizei (Határinfo: Röszke, Tompa, Ásotthalom). Nicht in v1, weil die Seiten aus der Entwicklungsumgebung nicht erreichbar waren und ein ungetesteter Scraper „halb fertig“ wäre. Der Datenfluss ist darauf vorbereitet (`source` im API-JSON).
- Push-Alarm „Kapıkule unter 1 Std.“ (Web-Push, VAPID, ohne Konto).
- Automatischer Ferien-Import (z. B. OpenHolidays-API) als Ergänzung der kuratierten Daten.
- Packliste, eSIM-Vergleich und Affiliate-Chips, jeweils mit klarer Kennzeichnung „Anzeige“ und `rel="sponsored"`.
- Optionale KI-Fragen, nie als Gate vor Kerninfos.
