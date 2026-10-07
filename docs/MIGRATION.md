# Ablösung der Alt-App durch tatilvakti v2

Unter `https://tatilvakti.guenlab.de` läuft heute eine ältere App. Laut Auftrag hat sie Web-Push (VAPID), einen Listmonk-Newsletter, einen KI-Gateway, Affiliate-Links und einen Grenz-Scraper. Aus der Entwicklungsumgebung war sie nicht erreichbar, alles zur Alt-App hier ist deshalb eine Annahme, die der Betreiber in Schritt 1 bestätigt.

v2 läuft **parallel** daneben. Live geht v2 erst, wenn in Pangolin das Ziel der Domain von Port 3095 auf 3096 wechselt. Die Alt-App wird dabei nur gestoppt, nicht gelöscht, solange ein Rollback möglich sein soll.

| | Alt-App | v2 |
|---|---|---|
| systemd-Unit | `tatilvakti.service` | `tatilvakti-v2.service` (+ Timer für Wartung und Backup) |
| Benutzer | (alt) | `tatilvakti-v2` |
| Port | `127.0.0.1:3095` | `127.0.0.1:3096` |
| Code | (alt) | `/opt/tatilvakti-v2/releases/…`, aktiv ist `current` |
| Daten | (alt) | `/var/lib/tatilvakti-v2`, Tagesschlüssel in `/run/tatilvakti-v2` (tmpfs) |
| Konfiguration | (alt) | `/etc/tatilvakti-v2.env` |

## Ablauf

1. **Bestandsaufnahme:** Infos aus der Alt-App sammeln (Abschnitt 1) und an die Entwicklung geben (Anhang B).
2. **Entscheidungen** zu Newsletter, Push, KI, Affiliate, Scraper, Logs (Abschnitt 2). *Gate: ohne diese Entscheidungen nicht umschalten.*
3. **v2 installieren** (README → Betrieb → Einrichten) und auf einer **Test-Subdomain** prüfen, inklusive Generalprobe für Service Worker und Rollback (Abschnitt 4).
4. **Umschalten** in Pangolin (Abschnitt 5), direkt danach die **Prüfliste** (Abschnitt 6).
5. Bei Problemen **Rollback** (Abschnitt 7).
6. **Nachlauf:** 4 Wochen beobachten, dann die Alt-App stilllegen und ihre Daten löschen (Abschnitt 8).

---

## 1. Bestandsaufnahme: Was der Betreiber aus der Alt-App liefert

Jeder Punkt beantwortet eine Frage, die sich aus dem Code von v2 allein nicht klären lässt. Ergebnis in Anhang B eintragen.

### 1.1 Service Worker

- [ ] **Skript-URL und Scope.** In Chrome mit geöffneter Alt-App: DevTools (F12) → *Application* → *Service workers* zeigt *Source* (z. B. `/service-worker.js`) und *Scope* (z. B. `/`). Alternativ `chrome://serviceworker-internals`, oder im Alt-Code nach `serviceWorker.register(` suchen.
- [ ] **Cache-Namen.** DevTools → *Application* → *Cache storage*.
- [ ] **Strategie für Seiten:** Liefert der alte Worker Seiten aus dem Cache, bevor er das Netz fragt (cache-first)? Im Alt-Code den `fetch`-Handler ansehen.
- [ ] **Push:** Hat der alte Worker einen `push`-Handler?

*Warum:* v2 registriert nur `/sw.js` und räumt nur eigene Caches (`tv-*`) ab. Liegt der alte Worker unter einem anderen Pfad, bleibt er aktiv (siehe Abschnitt 3).

### 1.2 Web-App-Manifest

- [ ] URL des Manifests (`<link rel="manifest">`) sowie `id`, `start_url`, `scope`. DevTools → *Application* → *Manifest*.

*Warum:* v2 hat `/de/manifest.webmanifest` und `/tr/manifest.webmanifest` mit `id` `/de/` bzw. `/tr/`, `start_url` `/de/` bzw. `/tr/`, `scope` `/`. Weicht die alte `id` ab, behandeln Browser v2 als andere App ([MDN: id](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Manifest/Reference/id)). Bereits installierte Icons öffnen weiter die alte `start_url`.

### 1.3 Alte URLs und Deep-Links

- [ ] Alle öffentlichen Pfade der Alt-App, gesammelt aus:
  - Routen im Alt-Code,
  - Google Search Console → *Indexierung* → *Seiten* (Export) und der alten Sitemap,
  - Pangolin- bzw. Traefik-Logs der letzten Wochen, nur Pfad und Anzahl,
  - Links, die selbst geteilt wurden (WhatsApp, Social Media, Newsletter), Affiliate-Links, alte Impressums- und Datenschutz-URLs,
  - API-Pfaden, die eine installierte Alt-PWA aufruft.
- [ ] Zu jedem Pfad das neue Ziel (Anhang A).

*Warum:* v2 beantwortet unbekannte Pfade mit 404 (Seite mit Link zur Startseite), nur `/` leitet auf `/de/` bzw. `/tr/` weiter. Eine Weiterleitungstabelle in der App ist vorgesehen. Grundlage dafür ist diese Liste.

### 1.4 Alt-Dienste und ihre Daten

- [ ] Unit und Konfiguration: `systemctl cat tatilvakti.service`, Env-Datei, Pfade der Datenbank(en).
- [ ] Zeitgesteuerte Jobs: `systemctl list-timers --all`, `crontab -l` der beteiligten Benutzer (Push-Versand, Scraper, Newsletter-Sync).
- [ ] **Newsletter (Listmonk):** Wo läuft die Instanz? Welche Listen, wie viele Abonnenten, wie ist das Double-Opt-in nachgewiesen, über welchen Mail-Dienst wird versendet?
- [ ] **Push:** Wo liegen die Abos (Tabelle/Datei), wie viele sind es, wo liegt das VAPID-Schlüsselpaar?
- [ ] **KI-Gateway:** Anbieter, was wird wo protokolliert (Prompts?), wie lange, welche API-Schlüssel?
- [ ] **Affiliate:** Partnerprogramme, Linkformat (z. B. `/go/<partner>`), Vertragsbedingungen zu Link-Änderungen.
- [ ] **Grenz-Scraper:** Quellen, Intervall, Speicherort der Historie, Nutzungsbedingungen der Quellen.

### 1.5 Infrastruktur

- [ ] **Pangolin:** Wo läuft es (eigene Hardware, VPS-Anbieter oder Pangolin Cloud) und in welcher Version? Welche Aufbewahrung ist in den Organisationseinstellungen für die HTTPS-Request-Logs eingestellt? Ist im Traefik von Pangolin ein `accessLog` aktiv?
- [ ] **VM „Hermes“:** Bei welchem Anbieter läuft sie?
- [ ] **Auftragsverarbeitung:** Gibt es einen AVV (Art. 28 DSGVO) mit diesen Anbietern?

---

## 2. Entscheidungen vor dem Umschalten (Gate)

v2 enthält **keinen** Newsletter, kein Push, keinen KI-Gateway und keine Affiliate-Links. Seine Datenschutzhinweise erwähnen nichts davon. Für jeden Alt-Dienst gilt deshalb: entweder **einstellen und Daten löschen** oder **weiterbetreiben und in den Datenschutzhinweisen von v2 abdecken**. Ein Dienst, der ohne passende Hinweise weiterläuft, verletzt die Informationspflicht (Art. 13 DSGVO). Daten ohne Zweck verletzen die Speicherbegrenzung (Art. 5 Abs. 1 lit. e DSGVO).

Pro Punkt eintragen: Entscheidung, Datum, erledigt.

### 2.1 Newsletter (Listmonk): größtes Risiko, echte E-Mail-Adressen

- **Einstellen:** Eine letzte Ausgabe an die bestehende Liste schicken (Einstellung, Hinweis auf die neue App). Danach Liste, Abonnenten, Bounce- und Versandlogs löschen, auch beim Mail-Dienstleister, und die Backups der Listmonk-Instanz im normalen Turnus auslaufen lassen. Löschung protokollieren (was, wann, wie viele, durch wen).
- **Weiterbetreiben:** Die Einwilligung gilt für diesen Newsletter, also weiterführen, ohne die Liste für anderes zu nutzen. Vor dem Umschalten in v2 ergänzen: Datenschutzhinweis (Zweck, Rechtsgrundlage Einwilligung Art. 6 Abs. 1 lit. a, Double-Opt-in, Mail-Dienstleister als Empfänger, Speicherdauer, Widerruf), Link zur Anmeldung bzw. Abmeldung. Funktionierenden Abmeldelink prüfen und den AVV mit dem Mail-Dienstleister prüfen.
- [ ] Entscheidung: ______  Datum: ______  erledigt: ☐

### 2.2 Web-Push

- **Einstellen (Empfehlung, solange v2 kein Push kann):** Den Push-Versand der Alt-App **vor** dem Umschalten stoppen, optional vorher eine letzte Nachricht senden. Danach die Abo-Datenbank löschen. Der Kill-Switch (Abschnitt 3) meldet die alten Worker ab, damit enden die Abos auch im Browser.
- **Für später aufheben:** Nur wenn Push in v2 konkret geplant ist (Roadmap „Kapıkule unter 1 Std.“). Dann das VAPID-Schlüsselpaar sicher aufbewahren, denn Abos sind an diesen Schlüssel gebunden. Zweck und Frist der Aufbewahrung dokumentieren. Bis dahin **nichts senden**: v2 hat keinen `push`-Handler, Browser zeigen dann eine Standardbenachrichtigung.
- [ ] Entscheidung: ______  Datum: ______  erledigt: ☐

### 2.3 KI-Gateway

- Endpunkte abschalten, alte Pfade in Anhang A mit 410 oder Weiterleitung eintragen.
- Protokolle löschen (Prompts können personenbezogen sein), auch beim Anbieter, soweit möglich. API-Schlüssel widerrufen.
- [ ] Entscheidung: ______  Datum: ______  erledigt: ☐

### 2.4 Affiliate

- Alte Affiliate-Links (z. B. `/go/…`) in Anhang A: auf eine passende Seite von v2 weiterleiten oder mit 410 beantworten. Partnerprogramme über die Änderung informieren, falls der Vertrag das verlangt.
- Affiliate in v2 erst mit Kennzeichnung „Anzeige“, `rel="sponsored"` und passendem Datenschutzhinweis (README → Roadmap).
- [ ] Entscheidung: ______  Datum: ______  erledigt: ☐

### 2.5 Grenz-Scraper und Historie

- Scraper-Job abschalten.
- Historie exportieren (CSV/SQLite) und archivieren, aber **nicht** in die Tabelle `reports` von v2 importieren. Dort stünde sie als Meldung von Reisenden (`source: crowd`) und würde Median und Tagesverlauf verfälschen. Später ggf. als eigene, gekennzeichnete Quelle einbinden.
- Nutzungsbedingungen der gescrapten Quellen prüfen, bevor die Daten weiterverwendet werden.
- [ ] Entscheidung: ______  Datum: ______  erledigt: ☐

### 2.6 Pangolin-Logs und Auftragsverarbeitung

Das HTTPS-Request-Log von Pangolin speichert laut [Pangolin-Doku](https://docs.pangolin.net/manage/analytics/request) pro Anfrage Zeit, Client-IP, Standort, User-Agent und URL, ab Werk 7 Tage. Die Aufbewahrung ist dort eine Einstellung der Organisation, nicht der einzelnen Ressource. Eine Meldung geht an `/api/v1/borders/<übergang>/reports`. Das Log verknüpft also IP, Grenzübergang und Uhrzeit.

- [ ] In den Organisationseinstellungen von Pangolin die Aufbewahrung der HTTPS-Request-Logs abschalten oder kürzen (z. B. 1–7 Tage). Den Wert notieren.
- [ ] Prüfen, ob die Einstellung wirkt: Bei abgeschalteter Aufbewahrung darf nach einer Testanfrage kein neuer Eintrag im Request-Log erscheinen, sonst keiner, der älter als die Frist ist. Pangolin hatte hier Fehler: In Version 1.13.0 erschienen Request-Logs trotz abgeschalteter Aufbewahrung ([Issue #2061](https://github.com/fosrl/pangolin/issues/2061), geschlossen). Version 1.22.0 behebt laut [Release Notes](https://github.com/fosrl/pangolin/releases/tag/1.22.0) einen Fehler, durch den die Aufbewahrung der Access-Logs ein falsches Feld der Organisationseinstellungen las. Deshalb eine aktuelle Version einsetzen.
- [ ] Traefik-`accessLog` in der Pangolin-Konfiguration prüfen: abschalten oder die Client-IP weglassen. Dazu unter `fields.names` sowohl `ClientHost` als auch `ClientAddr` auf `drop` setzen und Header nicht loggen (`fields.headers.defaultMode: drop`, der Traefik-Standard; [Traefik-Doku](https://doc.traefik.io/traefik/v3.1/observability/access-logs/)).
- [ ] AVV mit dem VPS-Anbieter bzw. Pangolin Cloud abschließen oder abrufen. Bei Anbietern außerhalb der EU die Drittlandübermittlung prüfen.
- [ ] Ergebnis (Empfänger, Log-Frist) in die Datenschutzhinweise von v2 übernehmen. Bleibt ein IP-Log bestehen, muss der Satz „Die IP-Adresse selbst speichern wir nicht“ in v2 auf die App-Datenbank eingeschränkt werden.

### 2.7 Manifest-id und alte Start-URL

- Option A: v2 behält `id` `/de/` bzw. `/tr/`. Installierte Alt-Icons öffnen weiter die alte `start_url`. Die muss per Weiterleitung (Anhang A) funktionieren.
- Option B: v2 übernimmt die alte `id`. Dann aktualisiert Chrome installierte Apps, statt sie verwaisen zu lassen ([web.dev: Manifest-Updates](https://web.dev/articles/manifest-updates)). Dafür ist eine Code-Änderung nötig.
- [ ] Entscheidung: ______

### 2.8 Impressum und Datenschutzhinweise

- [ ] `TV_OPERATOR_NAME`, `TV_OPERATOR_ADDRESS`, `TV_OPERATOR_EMAIL` in `/etc/tatilvakti-v2.env` gesetzt (`/healthz` → `imprint_ok: true`).
- [ ] Alle Dienste, die nach 2.1–2.6 weiterlaufen, sind in den Hinweisen von v2 abgedeckt.
- [ ] Alte Impressums- und Datenschutz-URLs stehen in Anhang A (Ziel `/de/info#impressum` bzw. `/de/info#datenschutz`).

---

## 3. Alter Service Worker und Kill-Switch (`TV_LEGACY_SW_PATHS`)

Je nach Ergebnis von 1.1:

| Alter Worker | Folge nach dem Umschalten | Maßnahme |
|---|---|---|
| `/sw.js`, Scope `/` | Der Browser lädt bei der nächsten Update-Prüfung das neue `/sw.js` und ersetzt den alten Worker (v2 aktiviert sofort: `skipWaiting`, `clients.claim`). Alte Caches mit anderen Namen als `tv-*` bleiben liegen. | Cache-Namen an die Entwicklung, damit v2 sie beim Aktivieren löscht. |
| anderer Pfad, z. B. `/service-worker.js` | Der alte Worker bleibt registriert. Chromium entfernt ihn auch dann nicht, wenn sein Skript 404 liefert (in einer Simulation für das Launch-Audit beobachtet). Arbeitet er cache-first, sehen Bestandsnutzer dauerhaft die Alt-App. | Kill-Switch unter diesem Pfad (siehe unten). |
| Scope enger als `/`, z. B. `/app/` | Für Seiten unter `/app/` gilt weiter der alte Worker, der neue mit Scope `/` übernimmt sie nicht. | Kill-Switch unter dem alten Skript-Pfad. |

**Kill-Switch:** v2 liefert in einer späteren Ausbaustufe unter den Pfaden aus `TV_LEGACY_SW_PATHS` (kommagetrennt, z. B. `/service-worker.js`) einen Worker aus. Er aktiviert sich sofort, löscht alle Caches, meldet sich ab und lädt offene Fenster neu. Damit enden auch alte Push-Abos.

- Erst in `/etc/tatilvakti-v2.env` setzen, wenn ein Release das unterstützt. Der Preflight prüft, dass jeder Pfad JavaScript mit Status 200 liefert, und bricht sonst ab.
- Mindestens 12 Monate aktiv lassen (Empfehlung: Wer die App nur zur Sommerreise öffnet, kommt erst in der nächsten Saison wieder).

---

## 4. v2 installieren und auf einer Test-Subdomain prüfen

1. v2 einrichten wie in README → Betrieb → Einrichten beschrieben. `/etc/tatilvakti-v2.env` enthält von Anfang an `TV_BASE_URL=https://tatilvakti.guenlab.de`: Canonical-Links zeigen damit schon im Test auf die Hauptdomain, es entsteht kein doppelter Suchindex.
2. In Pangolin eine **neue Ressource** anlegen, z. B. `tatilvakti-test.guenlab.de`, Ziel: Hermes, Port **3096**. Kompression einschalten (README → Reverse-Proxy). Empfohlen: Pangolin-Authentifizierung für diese Ressource, damit sie nicht öffentlich ist.
3. Funktionstest auf der Test-Subdomain:
   - [ ] alle Seiten in DE und TR, Sprachwechsel,
   - [ ] offline: DevTools → *Network* → *Offline*, neu laden, gespeicherter Stand mit Alter erscheint,
   - [ ] Meldung absetzen, mit und ohne JavaScript,
   - [ ] als App installieren (Android/Chrome, iOS/Safari),
   - [ ] `/healthz` (siehe 6a).
4. **Generalprobe Service Worker und Rollback.** Die Test-Subdomain ist ein eigener Origin. Deshalb lässt sich dort der Wechsel von der Alt-App auf v2 gefahrlos durchspielen:
   1. Ziel der Test-Ressource auf **3095** (Alt-App) stellen, auf einem Testgerät öffnen und installieren, damit sich der alte Worker registriert.
   2. Ziel auf **3096** umstellen. Mit der installierten App prüfen, ob nach höchstens einem Neuladen v2 erscheint. DevTools → *Application*: Welche Worker sind registriert, welche Caches liegen noch da?
   3. Ziel zurück auf **3095** (Rollback) und beobachten, was das Gerät zeigt.
   4. Ergebnis an die Entwicklung (Anhang B). Davon hängen Kill-Switch und Cache-Bereinigung ab.
5. Testmeldungen löschen (6e, letzter Punkt). Sie landen in der echten Datenbank von v2.

## 5. Umschalten

**Voraussetzungen:**

- [ ] Abschnitt 2 entschieden, alles, was vorher passieren muss, ist erledigt (Push-Versand und Scraper gestoppt, Hinweise für weiterlaufende Dienste in v2).
- [ ] `/healthz` auf der Test-Subdomain: `status` `ok`, `imprint_ok` `true`.
- [ ] Zeitpunkt außerhalb der Reisespitzen (nicht am Wochenende vor Ferienbeginn). Jedes Gerät lädt nach dem Wechsel die Offline-Daten neu.

**Schritte:**

1. Alt-App sichern (Ablageort nur für root lesbar, enthält personenbezogene Daten und wird in Abschnitt 8 gelöscht):

   ```bash
   sudo install -d -m 0700 /root/tatilvakti-alt
   sudo cp "$(systemctl show -P FragmentPath tatilvakti.service)" /root/tatilvakti-alt/
   # dazu Env-Datei und Datenbank der Alt-App kopieren (Pfade aus 1.4)
   ```

2. Alt-Jobs nach Abschnitt 2 stoppen (Timer/Cron für Push, Scraper, Newsletter).
3. **Pangolin:** In der Ressource `tatilvakti.guenlab.de` das Ziel von Port **3095** auf **3096** ändern und speichern.
4. Sofort die Prüfliste (Abschnitt 6) abarbeiten.
5. Wenn alles passt: Alt-App stoppen, die Unit-Datei bleibt für den Rollback:

   ```bash
   sudo systemctl disable --now tatilvakti.service
   ```

6. In der Search Console die Sitemap `https://tatilvakti.guenlab.de/sitemap.xml` einreichen.

## 6. Prüfliste nach Deploy und Umschalten

Mit `D=https://tatilvakti.guenlab.de` (auf der Test-Subdomain entsprechend).

**a) Status**

```bash
curl -s "$D/healthz" | python3 -m json.tool
```

Erwartet: `status` `ok`. Bei `attention` stehen die Gründe in `attention`. Außerdem `db` `true`, `build` wie in der Ausgabe von `deploy.sh`, `due_items` leer (sonst sind Daten zu prüfen, README → Datenpflege), `imprint_ok` `true`, `proxy.trust_proxy` `1`, `proxy.forwarded_ignored` `false`.

**b) Host-Weitergabe und Canonical**

```bash
curl -s "$D/de/" | grep -o '<link rel="canonical"[^>]*>'   # href="https://tatilvakti.guenlab.de/de/"
```

Ein Formular- oder API-POST mit 403 deutet darauf hin, dass Pangolin den Host nicht weitergibt (`X-Forwarded-Host`). Dann prüfen, ob `TV_BASE_URL` gesetzt ist.

**c) HTTPS und HSTS**

```bash
curl -sI "http://tatilvakti.guenlab.de/de/" | head -3                    # 301/308 auf https://…
curl -sI "$D/de/" | grep -i strict-transport-security                    # max-age=31536000
```

Fehlt der HSTS-Header, in Pangolin/Traefik ergänzen. Fehlt die Umleitung auf HTTPS, ebenfalls.

**d) Kompression**

```bash
curl -s -o /dev/null -D - -H 'Accept-Encoding: gzip, br' "$D/de/ferien" | grep -i content-encoding
```

Erwartet `gzip` oder `br`. Ohne Kompression lädt jedes Gerät nach jedem Deploy rund 1,2 MB statt rund 0,26 MB (Messung aus dem Launch-Audit).

**e) Proxy-Kette (`TV_TRUST_PROXY`)**

Der Spam-Schutz braucht die echte Client-IP. Ist `TV_TRUST_PROXY` zu klein (0), teilen sich alle Nutzer ein Limit. Ist der Wert zu groß oder reicht der Proxy den Header des Clients nur durch, lässt sich das Limit per Header umgehen.

1. Startzeit auf dem Server merken: `START=$(date -u +%Y-%m-%dT%H:%M:%SZ)`
2. **Zwei Netze:** Mit dem Handy über Mobilfunk (WLAN aus) und mit dem Laptop im WLAN je eine Meldung für denselben Übergang und dieselbe Richtung absetzen, z. B. Kapıkule → Türkei. Beide müssen angenommen werden. Kommt beim zweiten Gerät „zu viele Meldungen“, ist `TV_TRUST_PROXY` zu klein oder ein weiterer Proxy (z. B. CDN) sitzt davor.
3. **Header-Fälschung:** Vom Laptop zweimal mit unterschiedlicher, gefälschter Adresse, in der **anderen** Richtung als in Schritt 2:

   ```bash
   for n in 1 2; do
     curl -s -o /dev/null -w '%{http_code}\n' -X POST "$D/api/v1/borders/kapikule/reports" \
       -H 'Content-Type: application/json' -H "Origin: $D" \
       -H "X-Forwarded-For: 203.0.113.$n" -d '{"direction":"to_de","bucket":1}'
   done
   ```

   Erwartet `201`, dann `429`. Zweimal `201` heißt: Die App glaubt dem Header des Clients. Dann vor dem Launch `TV_TRUST_PROXY` bzw. die Proxy-Kette korrigieren.
4. Keine Warnung im Journal: `journalctl -u tatilvakti-v2 --since -15min | grep -i forwarded` bleibt leer.
5. Testmeldungen löschen. Erst zählen, dann löschen. Meldungen echter Nutzer im selben Zeitraum wären mit betroffen, deshalb einen ruhigen Übergang oder Zeitpunkt wählen:

   ```bash
   sudo /opt/tatilvakti-v2/current/scripts/tv-flask.sh purge-reports --crossing kapikule --since "$START" --dry-run
   sudo /opt/tatilvakti-v2/current/scripts/tv-flask.sh purge-reports --crossing kapikule --since "$START"
   ```

**f) Alte Installation:** Auf einem Gerät mit installierter Alt-App öffnen. Nach höchstens einem Neuladen erscheint v2. In DevTools ist nur `/sw.js` registriert, es gibt nur Caches `tv-*` (Abschnitt 3).

**g) Alte URLs:** Für jede Zeile aus Anhang A:

```bash
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' "$D/alter/pfad"
```

**h) Timer, Wartung, Backup**

```bash
systemctl list-timers 'tatilvakti-v2*'
sudo /opt/tatilvakti-v2/current/scripts/tv-flask.sh maintenance
sudo systemctl start tatilvakti-v2-backup.service && journalctl -u tatilvakti-v2-backup -n 3 -o cat
```

**i) Logs ohne IP:** `journalctl -u tatilvakti-v2 -n 100 -o cat` enthält App- und gunicorn-Meldungen, aber keine Client-IPs.

**j) Pangolin:** Request-Log und Aufbewahrung wie in 2.6 entschieden.

## 7. Rollback

### 7.1 Neues v2-Release ist fehlerhaft, v2 bleibt

```bash
sudo /opt/tatilvakti-v2/current/scripts/rollback.sh            # voriges Release
sudo /opt/tatilvakti-v2/current/scripts/rollback.sh --list     # verfügbare Releases
```

`deploy.sh` rollt selbst zurück, wenn ein neues Release nach dem Neustart `/healthz` nicht mit der erwarteten Build-ID beantwortet, und startet das alte Release wieder. `rollback.sh` startet den Dienst auch dann neu, wenn systemd ihn nach mehreren Fehlstarts aufgegeben hat (`failed`, start-limit-hit). Einen bewusst gestoppten Dienst startet es nur mit `--start`.

### 7.2 Zurück zur Alt-App

1. Alt-App starten und lokal prüfen:

   ```bash
   sudo systemctl start tatilvakti.service
   curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3095/
   ```

   Fehlt die Unit-Datei, aus `/root/tatilvakti-alt/` zurückkopieren und `sudo systemctl daemon-reload`.
2. **Pangolin:** Ziel der Ressource `tatilvakti.guenlab.de` zurück auf **3095**.
3. v2 darf weiterlaufen (Port 3096 ist von außen nicht erreichbar) oder `sudo systemctl stop tatilvakti-v2`.
4. Alt-Jobs (Push, Scraper, Newsletter) nur wieder starten, wenn ihre Daten noch da sind und die Entscheidungen aus Abschnitt 2 das zulassen.
5. **Service Worker:** Browser, die v2 zwischendurch geladen haben, haben jetzt `/sw.js` von v2 registriert. Der lädt Seiten zuerst aus dem Netz, online erscheint also wieder die Alt-App. Offline kann noch der gespeicherte Stand von v2 erscheinen, bis der Worker ersetzt ist. Das passiert automatisch, wenn die Alt-App selbst ein `/sw.js` hat. Wie es sich konkret verhält, zeigt die Generalprobe (4.4).
6. Meldungen, die v2 inzwischen gesammelt hat, bleiben in `/var/lib/tatilvakti-v2`. Es gibt keinen Rückabgleich.

## 8. Nach dem Umschalten

- **4 Wochen beobachten:** Search Console → *Seiten* (404). Optional das Access-Log ohne IP einschalten (`GUNICORN_CMD_ARGS` in `/etc/tatilvakti-v2.env`, siehe Kommentar dort), danach die häufigsten 404-Pfade auswerten und Anhang A ergänzen:

  ```bash
  journalctl -u tatilvakti-v2 -o cat --since -7d | awk '$3 == 404 {print $2}' | sort | uniq -c | sort -rn | head -30
  ```

  Danach das Access-Log wieder ausschalten.
- **Alt-App stilllegen**, wenn kein Rollback mehr nötig ist:
  - `sudo systemctl disable --now tatilvakti.service` (falls noch nicht geschehen).
  - Daten nach den Entscheidungen aus Abschnitt 2 löschen: Alt-Datenbank, Push-Abos, Listmonk-Liste, KI-Logs, Scraper-Rohdaten, Backups der Alt-App, `/root/tatilvakti-alt`.
  - Schlüssel widerrufen bzw. löschen (KI-API, VAPID, falls Push nicht geplant ist). Die Env-Datei der Alt-App entfernen.
  - **Löschprotokoll** führen: was, wann, wie viele Datensätze, durch wen.
- **Kill-Switch** (Abschnitt 3) mindestens 12 Monate aktiv lassen.

---

## Anhang A: Weiterleitungstabelle (Vorlage)

Neue Ziele in v2: `/de/` · `/de/ferien` · `/de/route` · `/de/grenze` · `/de/grenze/<übergang>` · `/de/zoll` · `/de/info` (`#datenschutz`, `#impressum`). Türkisch: `/tr/` · `/tr/tatil` · `/tr/guzergah` · `/tr/sinir` · `/tr/sinir/<übergang>` · `/tr/gumruk` · `/tr/bilgi`. Die IDs der Übergänge stehen in `tatilvakti/data/crossings.json`.

| Alte URL | Neues Ziel | Code (301 / 410) | Quelle (Code, Search Console, Log, geteilt) |
|---|---|---|---|
| `/…` | `/de/…` | 301 | |

## Anhang B: Übergabe an die Entwicklung

| Punkt | Wert |
|---|---|
| SW-Skript-URL(s) und Scope | |
| SW-Strategie für Seiten (cache-first / network-first) | |
| Cache-Namen der Alt-App | |
| Manifest-URL, `id`, `start_url`, `scope` | |
| Entscheidung Manifest-id (2.7) | |
| Weiterleitungstabelle (Anhang A) | |
| Ergebnis der Generalprobe (4.4) | |
| Weiterlaufende Dienste für die Datenschutzhinweise (2.1–2.6) | |
| Pangolin: Version, Aufbewahrung der Request-Logs (Organisation), Hoster, AVV | |
