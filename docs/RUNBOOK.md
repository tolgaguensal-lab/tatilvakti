# Störung und Wiederaufbau (tatilvakti v2)

Ablauf für den Fall, dass die Seite nicht läuft oder falsche Daten zeigt, und für den Neuaufbau auf einem anderen Host. Gilt für v2 auf Hermes (`tatilvakti-v2.service`, Port 3096). Pfade, Units und Skripte wie in [README → Betrieb](../README.md#betrieb). Befehle mit `sudo` laufen auf Hermes, „von außen“ heißt: von einem anderen Rechner, z. B. dem Laptop.

Reihenfolge: erst einordnen (1), dann prüfen (2), dann handeln (3). Nichts löschen, bevor die Ursache klar ist, vor allem keine `tatilvakti.db-wal` oder `-shm`: Darin können die jüngsten Meldungen stehen.

## 1. Einordnen

| Symptom | Häufige Ursache | Prüfen | Maßnahme |
|---|---|---|---|
| Seite lädt nicht, Pangolin meldet 502 oder Zeitüberschreitung | Dienst gestoppt oder abgestürzt (nach 5 Fehlstarts in 2 Min. gibt systemd auf), Proxy-Weg (Pangolin, Verbindung zu Hermes) | 2a, 2b, 2c | 3a Neustart, 3b Rollback |
| `/healthz` HTTP 503 mit `down: ["db"]`, Melden scheitert, Seiten laufen meist weiter | Haupt-DB nimmt nichts an: Platte voll, falsche Rechte an `-wal`/`-shm`, Sperre, beschädigte DB | 2c, 2d, 2e | Platz schaffen, Rechte richten, 3c Restore |
| `/healthz` HTTP 503 mit `down: ["salt_db"]` | Schlüssel-DB unter `/run/tatilvakti-v2` nicht nutzbar | 2c, 2f | 3a Neustart |
| Fehler seit einem Deploy (Seiten mit 500, falsche Inhalte) | das neue Release | 2b | 3b Rollback |
| Meldungen fehlen oder stimmen nicht, „database disk image is malformed“ im Journal | beschädigte oder falsche Datenbank | 2e | 3c Restore |
| Viele Meldungen auf einmal, Warnung „Obergrenze erreicht“ im Journal | Spam | 2b | 3d Meldungen löschen |
| Geräte zeigen weiter die Alt-App | alter Service Worker der Alt-App | MIGRATION.md 6f | 3e Kill-Switch |
| Hermes ist weg oder nicht mehr vertrauenswürdig | – | – | 4 Wiederaufbau |

## 2. Prüfschritte

**a) Von außen**

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://tatilvakti.guenlab.de/healthz
curl -s https://tatilvakti.guenlab.de/healthz          # status, build, down
```

200: Dienst und Proxy laufen, ein Problem liegt woanders (Pflegehinweise zeigt 2b). 503: Die App antwortet, Melden geht nicht, der Grund steht in `down`. 502, 504 oder keine Antwort: weiter mit 2b.

**b) Dienst auf Hermes**

```bash
curl -s http://127.0.0.1:3096/healthz | python3 -m json.tool    # alle Details, nur lokal
systemctl status tatilvakti-v2 --no-pager
journalctl -u tatilvakti-v2 -n 100 --no-pager -o cat
systemctl list-timers 'tatilvakti-v2*' --no-pager
journalctl -u tatilvakti-v2-maintenance -u tatilvakti-v2-backup -n 10 --no-pager -o cat
```

- Lokal HTTP 200, von außen nicht: Die Ursache liegt im Proxy-Weg: Pangolin-Ressource (Ziel Port 3096), die Verbindung von Pangolin zu Hermes (hängt vom Aufbau ab, siehe 4), Zertifikat, DNS. Siehe README → Reverse-Proxy.
- `Active: failed` (auch `start-limit-hit`): Die Ursache steht im Journal, bei Bedarf mit mehr Zeilen (`-n 300`). Begann es mit einem Deploy, gleich 3b.
- Die Bedeutung von `status`, `down` und `attention` erklärt README → Logs und Monitoring.

**c) Speicherplatz**

```bash
df -h /var/lib/tatilvakti-v2 /run/tatilvakti-v2 /opt/tatilvakti-v2 /tmp
sudo du -sh /var/lib/tatilvakti-v2 /var/lib/tatilvakti-v2/backups /opt/tatilvakti-v2/releases
journalctl --disk-usage
```

Platz schaffen, ohne Daten zu verlieren: geprüfte `pre-restore-<zeit>/`-Verzeichnisse unter `/var/lib/tatilvakti-v2` löschen (README → Backup und Restore), das Journal kürzen (`sudo journalctl --vacuum-time=14d`, passend zur Empfehlung in README → Logs und Monitoring). Alte Releases räumt `deploy.sh` selbst auf (`--keep`). Sicherungen unter `backups/` nur löschen, wenn eine neuere geprüft ist. `deploy.sh` bricht ab, wenn für die Sicherung vor dem Umschalten der Platz fehlt (README → Update und Rollback). Muss ein Hotfix trotzdem sofort raus: `cd /opt/tatilvakti-v2/src && sudo scripts/deploy.sh --no-snapshot "<Grund>"`. Dann ist die neueste nächtliche Sicherung der jüngste Datenstand zum Zurückspielen, `deploy.sh` nennt sie samt Alter. Nicht zusammen mit `--force-schema` (lehnt `deploy.sh` ab).

**d) Rechte**

```bash
sudo ls -la /var/lib/tatilvakti-v2 /run/tatilvakti-v2
```

Alles muss `tatilvakti-v2` gehören (Verzeichnisse `0700`, Dateien `0600`). Typischer Fehler: `tatilvakti.db-wal` oder `-shm` gehören `root`, weil jemand die DB als root geöffnet hat (z. B. mit `sqlite3`). Dann kann der Dienst nicht mehr schreiben (`down: ["db"]`). Beheben:

```bash
sudo find /var/lib/tatilvakti-v2 -maxdepth 1 -name 'tatilvakti.db*' -exec chown tatilvakti-v2:tatilvakti-v2 {} +
sudo systemctl restart tatilvakti-v2
```

Die DB immer nur als Dienstbenutzer öffnen, wie in 2e.

**e) Datenbank prüfen (nur lesend, als Dienstbenutzer)**

```bash
cd / && sudo -u tatilvakti-v2 python3 -c 'import sqlite3,sys; c=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro", uri=True, timeout=30); print(c.execute("PRAGMA integrity_check").fetchone()[0], c.execute("SELECT COUNT(*) FROM reports").fetchone()[0])' /var/lib/tatilvakti-v2/tatilvakti.db
cd / && sudo -u tatilvakti-v2 python3 /opt/tatilvakti-v2/current/scripts/schema_check.py --db /var/lib/tatilvakti-v2/tatilvakti.db --release /opt/tatilvakti-v2/current
```

Erwartet `ok <Zahl der Meldungen>` und `schema db=N release=M` mit N ≤ M. Alles andere bei `integrity_check` heißt: Restore (3c). N > M heißt: Das aktive Release ist älter als die Datenbank (nach `rollback.sh --force-schema`), siehe README → Update und Rollback → Schema-Version. `unable to open database file` (bei `schema_check.py` mit „nicht lesbar“ und Exit-Code 2) heißt meist: `-wal` oder `-shm` gehören nicht dem Dienstbenutzer, also erst 2d.

**f) Schlüssel-DB (tmpfs)**

```bash
sudo ls -la /run/tatilvakti-v2
df -h /run
```

Die Tagesschlüssel liegen in `/run/tatilvakti-v2/salts.db` auf tmpfs und werden nie gesichert. Fehlt das Verzeichnis (etwa von Hand gelöscht), legt systemd es beim Start des Dienstes wieder an, die App legt die Datei neu an. Ein verlorener Tagesschlüssel ist harmlos: Die Melde-Limits des Tages beginnen neu. Ist `/run` voll, Platz dort schaffen; die Ursache liegt dann außerhalb von tatilvakti.

## 3. Maßnahmen

**a) Neustart**

```bash
sudo systemctl restart tatilvakti-v2
# nach zu vielen Fehlstarts (failed, start-limit-hit) erst:
sudo systemctl reset-failed tatilvakti-v2 && sudo systemctl start tatilvakti-v2
curl -s http://127.0.0.1:3096/healthz | python3 -m json.tool
```

**b) Rollback auf ein früheres Release**

```bash
sudo /opt/tatilvakti-v2/current/scripts/rollback.sh --list
sudo /opt/tatilvakti-v2/current/scripts/rollback.sh             # voriges Release
sudo /opt/tatilvakti-v2/current/scripts/rollback.sh <release>   # bestimmtes Release
```

Die Datenbank bleibt dabei, wie sie ist. Bricht `rollback.sh` wegen der Schema-Version ab, nennt es die Wege und passende Sicherungen; Ablauf und Reihenfolge stehen in README → Update und Rollback → Schema-Version. Scheitert nur der Preflight des Ziels und ist Eile geboten: `--no-preflight`. Die Prüfung der Schema-Version läuft auch dann. Meldet sie „Schema-Version nicht lesbar“, liegt es an der Datenbank (2d, 2e); muss trotzdem sofort zurückgerollt werden: `--force-schema "<Grund>"`. Zurück zur Alt-App: MIGRATION.md → Rollback.

**c) Restore einer Sicherung**

```bash
sudo ls -l /var/lib/tatilvakti-v2/backups/
sudo /opt/tatilvakti-v2/current/scripts/restore.sh --check /var/lib/tatilvakti-v2/backups/<datei>
sudo /opt/tatilvakti-v2/current/scripts/restore.sh /var/lib/tatilvakti-v2/backups/<datei>
```

`tatilvakti-<zeit>.db` sind die nächtlichen Sicherungen, `pre-deploy-<release>.db` der Stand direkt vor dem Deploy von `<release>`. Meldungen seit der Sicherung gehen verloren. Den bisherigen Stand legt `restore.sh` nach `pre-restore-<zeit>/` und nennt bei jedem Fehler den Weg zurück (README → Backup und Restore). Hat ein Deploy die Daten beschädigt und die Schema-Version erhöht, zuerst zurückrollen, dann die Sicherung vor dem Deploy zurückspielen (README → Update und Rollback → Schema-Version). Ist die Sicherung neuer, als das aktive Release kennt, spielt `restore.sh` sie nicht zurück und nennt die Wege (README → Backup und Restore → Schema-Version); `--check` meldet das nur als WARNUNG. Notfall: `--force-schema "<Grund>"`.

**d) Spam: Meldungen gezielt löschen**

```bash
sudo /opt/tatilvakti-v2/current/scripts/tv-flask.sh purge-reports --crossing <übergang> --since <zeit> --dry-run
sudo /opt/tatilvakti-v2/current/scripts/tv-flask.sh purge-reports --crossing <übergang> --since <zeit>
```

Optionen und Zeitformat: README → Befehle.

**e) Kill-Switch für den alten Service Worker**

Zeigen Geräte weiter die Alt-App, fehlt deren Worker-Pfad in `TV_LEGACY_SW_PATHS`: in `/etc/tatilvakti-v2.env` eintragen (`sudoedit /etc/tatilvakti-v2.env`), dann `sudo systemctl restart tatilvakti-v2`. Ablauf und Regeln: MIGRATION.md, Abschnitt 3; Prüfen: MIGRATION.md 6f. Für den eigenen Worker `/sw.js` gibt es keinen Kill-Switch: Ein neues Release mit neuer Build-ID ersetzt ihn, und Seiten kommen online ohnehin zuerst aus dem Netz (README → PWA und Offline).

**Danach:** `/healthz` von außen und auf Hermes prüfen (2a, 2b), eine Seite laden, bei Bedarf eine Testmeldung absetzen und wieder löschen (MIGRATION.md 6e, Schritt 5). Kurz notieren, was passiert ist: Beginn, Symptom, Ursache, Maßnahme, Ende.

## 4. Wiederaufbau auf einem neuen Host

Ziel: unter einer Stunde von leerem Host bis `/healthz` mit `status` `ok` über die echte Domain. Gemessen ist das noch nicht. Einmal auf einer frischen VM mit Stoppuhr durchspielen und die Zeit unten eintragen.

**Vorher klären: Wie hängt Hermes an Pangolin?** Das ist noch offen (MIGRATION.md 1.5, Ergebnis in Anhang B), und davon hängen die Schritte 1 und 5 ab. Pangolin bindet Hosts als Sites an, u. a. per Newt (Tunnel aus einem anderen Netz) oder als Local (Ressourcen auf dem Host, auf dem Pangolin selbst läuft, ohne Tunnel; [Pangolin-Doku: Sites](https://docs.pangolin.net/manage/sites/understanding-sites)). Auf Hermes nachsehen:

```bash
sudo docker ps --format '{{.Names}}'     # laufen Pangolin, Traefik, Gerbil oder Newt hier als Container?
systemctl list-units --type=service --no-pager | grep -i -E 'pangolin|traefik|gerbil|newt'
```

- **Fall A: Pangolin läuft auf einem anderen Rechner** (eigener Server oder Pangolin Cloud), Hermes ist dort eine Site, z. B. mit Newt auf Hermes. Fällt Hermes aus, bleiben Pangolin, Zertifikat und DNS unberührt.
- **Fall B: Pangolin und Traefik laufen auf Hermes selbst.** Dann ist mit Hermes auch der Proxy weg, samt allen Ressourcen, die er bedient, und muss mit aufgebaut werden.

Ergebnis hier und in MIGRATION.md, Anhang B eintragen. Fall: ______

**Muss außerhalb von Hermes liegen** (beim monatlichen Restore-Drill mitprüfen):

- [ ] **Code:** das Git-Repository (GitHub). Alles außer den Meldungen kommt daraus, auch Ferien-, Zoll- und Routendaten.
- [ ] **Konfiguration:** die Werte aus `/etc/tatilvakti-v2.env` im Passwortmanager: `TV_BASE_URL`, `TV_TRUST_PROXY`, `TV_OPERATOR_NAME`, `TV_OPERATOR_ADDRESS`, `TV_OPERATOR_EMAIL`, `TV_LEGACY_SW_PATHS`, falls gesetzt auch `GUNICORN_CMD_ARGS`.
- [ ] **Daten:** eine aktuelle Sicherung außerhalb von Hermes (Kopie aus `backups/`, README → Backup und Restore). Ohne sie startet der neue Host ohne Meldungen.
- [ ] **Zugänge:** Pangolin (Admin), SSH-Schlüssel für den neuen Host, DNS von `guenlab.de`.
- [ ] **Nur in Fall B, der Proxy:** die Einstellungen von Pangolin und Traefik, so festgehalten, dass sie sich auf einem neuen Host wieder eintragen lassen: die Ressource `tatilvakti.guenlab.de` (Ziel, Host-Weitergabe, kein Health-Check auf `/healthz`), die Traefik-Dateikonfiguration mit der Middleware `compress` (`compress@file`) aus README → Reverse-Proxy, `accessLog` und Aufbewahrung der Request-Logs wie in MIGRATION.md 2.6, die übrigen Ressourcen dieser Pangolin-Instanz und die Angaben für die Zertifikate. Wer statt der Einstellungen die Daten von Pangolin kopiert: Enthalten sie das Request-Log (IP-Adressen, MIGRATION.md 2.6), gilt dessen Frist auch für die Kopie.

**Schritte:**

1. Neuen Host bereitstellen (Debian oder Ubuntu, `python3` ab 3.10, Zugang zu GitHub und PyPI für `git clone` und `pip`). Dann der Proxy:
   - Fall A: In Pangolin eine Site für den neuen Host anlegen und deren Verbindung (z. B. Newt) dort so einrichten, wie Pangolin es beim Anlegen anzeigt.
   - Fall B: Pangolin samt Traefik auf dem neuen Host nach der Pangolin-Doku installieren und die festgehaltenen Einstellungen eintragen (Liste oben).
2. README → Betrieb → Einrichten (einmalig) Zeile für Zeile, mit einer Abweichung: In `/etc/tatilvakti-v2.env` die Werte aus dem Passwortmanager eintragen statt der Vorlage. Am Ende laufen Dienst und Timer mit einer leeren Datenbank; `deploy.sh` meldet dabei „Keine Datenbank …, also keine Sicherung vor dem Umschalten“.
3. Sicherung auf den Host kopieren (z. B. per `scp` nach `/root/`) und zurückspielen:

   ```bash
   sudo /opt/tatilvakti-v2/current/scripts/restore.sh --check /root/tatilvakti-<zeit>.db
   sudo /opt/tatilvakti-v2/current/scripts/restore.sh /root/tatilvakti-<zeit>.db
   ```

   Die leere Datenbank landet dabei in `/var/lib/tatilvakti-v2/pre-restore-<zeit>/`; danach löschen, ebenso die kopierte Datei unter `/root/`.
4. Lokal prüfen: `curl -s http://127.0.0.1:3096/healthz | python3 -m json.tool` zeigt `db` und `salt_db` `true`, `imprint_ok` `true`.
5. **Pangolin:** Die Ressource `tatilvakti.guenlab.de` auf den neuen Host richten. Fall A: im Ziel die neue Site eintragen, die Adresse wie bisher auf Hermes (README → Reverse-Proxy). Fall B: die Ressource mit Ziel Port 3096 so wie auf Hermes anlegen bzw. prüfen. In beiden Fällen Kompression und Host-Weitergabe wie in README → Reverse-Proxy, kein Health-Check auf `/healthz`. **DNS:** nur ändern, wenn `getent hosts tatilvakti.guenlab.de` die Adresse von Hermes selbst zeigt (Fall B) und nicht die eines anderen Pangolin-Servers.
6. Prüfliste MIGRATION.md, Abschnitt 6, mindestens a) Status, b) Canonical, c) HTTPS, e) Proxy-Kette und h) Timer und Backup.
7. Den alten Host, falls er wieder auftaucht, stoppen (`sudo systemctl disable --now tatilvakti-v2.service tatilvakti-v2-maintenance.timer tatilvakti-v2-backup.timer`), in Fall A auch seine Site in Pangolin entfernen: Zwei laufende Instanzen sammelten getrennt Meldungen. Seine Datenbank enthält Prüfwerte, die dort keine Wartung mehr löscht: spätestens nach 48 h löschen, wie ein `pre-restore-<zeit>/`.

**Durchläufe:**

| Datum | Host | Sicherung | Zeit bis `status` `ok` über die Domain | Was fehlte |
|---|---|---|---|---|
