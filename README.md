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
fetch     ◀─▶ /api/v1/borders ──────────── SQLite (WAL): reports, kv;
                                            Tagesschlüssel in eigener Datei
localStorage: Bundesland, Reiseart,         data/*.json: Ferien, Zoll, Transit,
  Checkliste, Offline-Meldungen               Übergänge (validiert beim Start)
```

- **Stack:** Python 3.10+, Flask, gunicorn, SQLite. Sonst keine Abhängigkeiten, kein Build-Step, keine externen Skripte oder Fonts.
- **i18n:** lokalisierte Slugs (`/de/ferien` ↔ `/tr/tatil`), `hreflang`, Sprachwechsel als EU-Nummernschild (D | TR). Ein Test erzwingt identische Schlüssel und Platzhalter in `de.json` und `tr.json`; die Datenvalidierung verlangt jeden kuratierten Text in beiden Sprachen.
- **Service Worker:** Der Server generiert ihn mit Build-ID (Hash über Assets und Datenstand). Man muss nie manuell eine Cache-Version hochzählen. Vorab gespeichert werden alle Seiten in beiden Sprachen, alle Ferienzeiträume und alle Grenzübergänge (42 Seiten).
- **Sicherheit:** strikte CSP ohne `unsafe-inline` (auch keine Inline-Styles; die SVG-Diagramme nutzen nur Attribute), `Referrer-Policy: same-origin` (fremde Seiten erhalten keinen Referrer, eigene Formulare und API-Aufrufe tragen Origin bzw. Referer, die der CSRF-Schutz gegen den eigenen Host prüft; mit `no-referrer` schickten Browser `Origin: null` und keinen Referer, das Formular ohne JS würde abgelehnt), keine Cookies (ein Test prüft das auf jeder Seite).
- **Spam-Schutz ohne IP-Speicherung:** HMAC(IP) mit täglich wechselndem Zufallsschlüssel. Der Prüfwert wird nach 48 h gelöscht, der Schlüssel nach 2 Tagen. Die Schlüssel liegen in einer eigenen Datei (`TV_SALT_DB_PATH`, in Produktion auf tmpfs), Sicherungen enthalten weder Schlüssel noch Prüfwerte. Limits: 1 Meldung pro Übergang und Richtung in 20 Min., 10 Meldungen pro Stunde. Dazu ein Honeypot-Feld.

```
tatilvakti/
  __init__.py      App-Factory, Datenvalidierung beim Start, Asset-Hashing
  views.py         Seiten, PWA (/sw.js, Manifest), SEO (sitemap, robots), /healthz
  api.py           /api/v1/borders, /api/v1/borders/<id>, POST …/reports
  cli.py           Befehle: flask --app tatilvakti maintenance | purge-reports
  borders.py       Meldungen, Median, Rate-Limits, Tagesverlauf, Datensparsamkeit
  holidays.py      Ferien-Druck, Überlappung, ruhigste Tage, Ferien-Wellen
  content.py       Laden + Validieren der kuratierten Daten
  i18n.py          Texte, Sprachwahl, Datumsformat, Such-Normalisierung
  data/            holidays.json, customs.json, transit.json, crossings.json
  i18n/            de.json, tr.json
  templates/       Jinja-Templates (SSR)
  static/          app.css, app.js, sw.js, Icons
tests/             pytest (Daten, Logik, HTTP, Datenschutz, CSP, Betriebsskripte)
deploy/            systemd-Units (Dienst, Wartung, Backup) und Env-Vorlage
scripts/           deploy.sh, rollback.sh, preflight.py, backup.py, tv-flask.sh
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

`deploy.sh` baut ein neues Release aus dem eingecheckten Stand (`git archive`), installiert die Pakete nur mit passenden Hashes und startet dann den **Preflight** (`scripts/preflight.py`) als Dienstbenutzer. Der Preflight prüft die Konfiguration, ruft `create_app()` gegen eine Kopie der Produktions-DB auf, lädt alle Seiten und die Offline-Liste des Service Workers und lässt `pytest` laufen. Erst danach schaltet `deploy.sh` den Symlink `current` um und startet den Dienst neu. Meldet `/healthz` danach nicht die neue Build-ID, geht es automatisch auf das vorige Release zurück, und `deploy.sh` startet dieses neu. Das klappt auch, wenn systemd den Dienst nach mehreren Fehlstarts schon aufgegeben hat (`failed`, start-limit-hit): Vor jedem Neustart läuft `systemctl reset-failed`. Ein kaputter Datenstand erreicht so nie die laufende Seite. Meldet `/healthz` `salt_db: false`, gibt `deploy.sh` eine WARNUNG aus: Die Seiten laufen, aber Meldungen scheitern.

Geänderte Units in `deploy/` übernimmt `deploy.sh` nicht selbst, es weist nur darauf hin. Dann die Dateien erneut nach `/etc/systemd/system/` kopieren, `sudo systemctl daemon-reload` ausführen und die betroffenen Units neu starten.

Der Neustart unterbricht die Seite für wenige Sekunden. Nach jeder Änderung an Daten, Texten oder Templates lädt jedes Gerät die Offline-Daten neu. Solche Deploys deshalb nicht mitten in einer Ferienwelle einspielen.

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
| `TV_BASE_URL` | Öffentliche URL für Canonical, `hreflang`, Sitemap und Teilen-Links: `https://tatilvakti.guenlab.de` |
| `TV_TRUST_PROXY` | Anzahl vertrauenswürdiger Reverse-Proxys, damit der Spam-Schutz die echte Client-IP sieht (hinter Pangolin: `1`). Prüfen: MIGRATION.md → Prüfliste e) |
| `TV_OPERATOR_NAME`, `TV_OPERATOR_ADDRESS`, `TV_OPERATOR_EMAIL` | Impressum (§ 5 DDG). Adresszeilen mit `;` trennen. Solange leer, zeigt die Info-Seite einen sichtbaren Hinweis. |
| `TV_DB_PATH` | Haupt-DB (Standard: `instance/tatilvakti.db`). In Produktion setzt die Unit den Wert, nicht die Env-Datei. |
| `TV_SALT_DB_PATH` | Tagesschlüssel (Standard: neben `TV_DB_PATH` als `…-salts.db`). In Produktion setzt die Unit `/run/tatilvakti-v2/salts.db` (tmpfs). |
| `TV_LEGACY_SW_PATHS` | Kommagetrennte Pfade alter Service-Worker-Skripte, unter denen v2 einen Kill-Switch ausliefert. Erst setzen, wenn ein Release das unterstützt (MIGRATION.md, Abschnitt 3). |
| `GUNICORN_CMD_ARGS` | Optional für die Umstiegsphase: Access-Log ohne IP (Beispiel in der Env-Vorlage) |

### Reverse-Proxy (Pangolin/Traefik)

- Ziel `127.0.0.1:3096` auf Hermes. HTTPS ist Pflicht, sonst funktioniert der Service Worker nicht, HTTP leitet auf HTTPS um.
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
- Pangolin protokolliert pro Anfrage IP und URL (Request-Log). Aufbewahrung und Auftragsverarbeitung: MIGRATION.md, Abschnitt 2.6.

### Logs und Monitoring

- `journalctl -u tatilvakti-v2`: App-Meldungen ab INFO (z. B. eingegangene Meldungen ohne IP, Warnungen) und gunicorn-Fehler. gunicorn schreibt kein Access-Log. Wartung und Backup: `journalctl -u tatilvakti-v2-maintenance` bzw. `-u tatilvakti-v2-backup`.
- Die Aufbewahrung im Journal gilt für den ganzen Host. Empfehlung: begrenzen, z. B. `MaxRetentionSec=14day` in `/etc/systemd/journald.conf.d/retention.conf`.
- `/healthz` liefert HTTP 200 mit `status` `ok` oder `attention` (Gründe in `attention`, z. B. fällige Datenprüfung `due_items`, fehlendes Impressum `imprint_ok: false`, Proxy-Fehlkonfiguration) und HTTP 503, wenn die Datenbank nicht antwortet. Außerdem `build` (Build-ID) und `datasets.*.review_due`. Geeignet für einen Uptime-Monitor mit Stichwortprüfung auf `"status":"ok"` (die Antwort ist kompaktes JSON).

### Backup und Restore

- `tatilvakti-v2-backup.timer` sichert nachts um 03:40 Ortszeit per SQLite-Online-Backup nach `/var/lib/tatilvakti-v2/backups/tatilvakti-<UTC-Zeit>.db`. Ein `cp` der Datei würde im WAL-Modus die jüngsten Meldungen verlieren. Die Kopie behält von jeder Meldung nur Übergang, Richtung, Wartezeit-Bereich und Zeitpunkte. Alles andere wird geleert: die Prüfwerte (`client`, `net`) und auch Spalten oder Tabellen, die `backup.py` nicht kennt (dann mit WARNUNG im Journal). Die Tagesschlüssel liegen in einer eigenen Datei und werden nie gesichert. Sofort sichern: `sudo systemctl start tatilvakti-v2-backup.service`.
- **Aufbewahrung:** lokal 14 Tage (`--keep-days` in der Unit). Zusätzlich eine Kopie außerhalb des Hosts, z. B. 30 Tage. Dafür nur `backups/` kopieren, nie die laufende DB samt `-wal`/`-shm` und nie `/run/tatilvakti-v2`.
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
6. **Ablösung der Alt-App:** Bestandsaufnahme und Entscheidungen aus [docs/MIGRATION.md](docs/MIGRATION.md) (Newsletter, Push, alte URLs, Service Worker, Proxy-Logs).

## Bewusst später (Roadmap)

- **Offizielle Wartezeit-Quellen als Adapter:** z. B. die ungarische Polizei (Határinfo: Röszke, Tompa, Ásotthalom). Nicht in v1, weil die Seiten aus der Entwicklungsumgebung nicht erreichbar waren und ein ungetesteter Scraper „halb fertig“ wäre. Der Datenfluss ist darauf vorbereitet (`source` im API-JSON).
- Push-Alarm „Kapıkule unter 1 Std.“ (Web-Push, VAPID, ohne Konto).
- Automatischer Ferien-Import (z. B. OpenHolidays-API) als Ergänzung der kuratierten Daten.
- Packliste, eSIM-Vergleich und Affiliate-Chips, jeweils mit klarer Kennzeichnung „Anzeige“ und `rel="sponsored"`.
- Optionale KI-Fragen, nie als Gate vor Kerninfos.
