---
name: nutzertest
description: Nutzertest für tatilvakti aus Sicht echter Menschen. Realistische Personas (Familie mit Auto, Rentner auf Türkisch, Studentin mit Flug, Power-User an der Grenze ohne Netz, Planerin mit Schwester, skeptischer Erstbesucher) bedienen die laufende App im Handy-Browser (Playwright, DE/TR, langsames Netz, offline) und beurteilen ehrlich, was sie ihnen bringt und was nicht, mit Belegen und Bildschirmfotos. Immer verwenden, wenn jemand wissen will, wie Nutzer die App erleben, ob sie etwas bringt, ob sie verständlich oder vertrauenswürdig ist, vor einem Launch oder nach UI-Änderungen, oder Begriffe wie Nutzertest, Usability, Persona, UX-Test, „wie ein echter User“, Nutzerperspektive, user test, user journey fallen – auch wenn nicht ausdrücklich „Skill“ gesagt wird. Für alle Personas auf einmal den Workflow `nutzertest` starten.
---

# Nutzertest: tatilvakti durch die Augen echter Menschen

Ziel ist ein ehrliches Urteil aus Nutzersicht: Was bringt die App dieser Person in ihrer Lage, wo
scheitert sie, wem würde sie sie empfehlen? Technische Korrektheit prüfen die Tests im Repo; hier
zählt, was ein Mensch am Handy erlebt. Deshalb bedienst du die echte App im Browser und urteilst nur
nach dem, was auf dem Bildschirm steht.

## Bausteine

- `references/personas.md`: sechs Personas mit Lage, Uhrzeit, Datenstand, Gerät, Sprache, Zielen.
- `references/bewertung.md`: Bewertungsraster, Schweregrade, Vorlagen für die Berichte.
- `scripts/serve.py`: startet die App mit eigener Temp-DB und eigener Uhr, optional mit Meldungen.
- `scripts/user.js`: bedient die App wie ein Mensch (Schritte als JSON) und protokolliert, was zu sehen ist.
- Workflow `.claude/workflows/nutzertest.js`: alle Personas parallel, Gegenprüfung, Gesamtbericht.

## Voraussetzungen

- Python-venv des Repos (`.venv/bin/python`, sonst `python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`).
- Node mit Playwright: `NODE_PATH=/opt/node-tools/node_modules` (oder `PLAYWRIGHT_MODULE`), Chromium
  über `PLAYWRIGHT_BROWSERS_PATH`. Kein `playwright install` nötig, wo Chromium schon liegt.
- Arbeitsordner `tmp/nutzertest-<JJJJ-MM-TT>/` im Repo: `tmp/` ist in `.gitignore`, Berichte und
  Bildschirmfotos landen also nie im Repo, sind aber für den Betreiber lesbar.

## Ablauf für eine Persona

1. **Persona lesen** (Abschnitt in `references/personas.md`) und dich hineinversetzen: Was weiß sie,
   was will sie, wie ungeduldig ist sie, womit vergleicht sie?

2. **App starten** im Hintergrund, mit eigener Uhr und Datenstand der Persona, auf einem freien Port:
   ```bash
   D=tmp/nutzertest-<datum>/<persona>
   .venv/bin/python .claude/skills/nutzertest/scripts/serve.py --now <--now> --seed <leer|saison> --port <port> --dir $D &
   sleep 3; cat $D/server.url
   ```

3. **Bedienen wie ein Mensch**, in kleinen Häppchen (3 bis 8 Schritte je Aufruf): erst `{"see": true}`,
   lesen, dann entscheiden, was die Persona als Nächstes antippen würde. Nicht den ganzen Weg vorab
   aus Wissen über den Code planen; Menschen suchen, zögern, gehen zurück.
   ```bash
   NODE_PATH=/opt/node-tools/node_modules node .claude/skills/nutzertest/scripts/user.js \
     --base "$(cat $D/server.url)" --device <android|iphone|klein|desktop> --lang <de|tr> \
     --out $D --session <persona> --steps $D/schritte-01.json [--slow]
   ```
   - Immer dieselbe `--session`: So bleiben Service Worker, gespeichertes Bundesland und
     Warteschlange erhalten, wie auf einem echten Handy.
   - Navigiere über sichtbare Texte (`tap`, `choose`). Adressen tippen nur, wo die Persona eine kennt
     (Einstieg aus der Persona, ein geteilter Link).
   - Bildschirmfotos an den entscheidenden Stellen (`shot`, ohne `full`: so viel, wie ein Handy zeigt).
   - Offline: zuerst mit Netz öffnen und ein paar Sekunden warten (`{"wait": 3000}`), damit der
     Offline-Speicher steht, dann `{"offline": true}`.
   - `protokoll.jsonl` im Ordner hält jeden Schritt fest, auch JS-Fehler der Seite.

4. **Notieren während des Nutzens**: wo gezögert, was missverstanden, wie viele Tipps bis zur
   Antwort. Zitate wörtlich aus dem Protokoll übernehmen.

5. **Verdacht auf einen Fehler** einmal in einer frischen Session (anderer `--session`-Name)
   nachstellen. Erst danach darfst du in den Code schauen, um die Ursache zu benennen. Das Urteil
   darüber, was die Persona erlebt hat, ändert der Code nicht.

6. **Bericht schreiben** nach `references/bewertung.md` in `$D/../<persona>.md`.

7. **Aufräumen**: `kill "$(cat $D/server.pid)"`. Profile (`profil-*`) dürfen liegen bleiben.

Für alle Personas auf einmal: Workflow `nutzertest` starten, mit
`args: {"datum": "<JJJJ-MM-TT>"}` und optional `"personas": ["ayse", "can"]`.

## Worauf es ankommt

- **Nur der Bildschirm zählt.** Die Persona kennt weder Code noch README. Was nicht sichtbar oder nicht
  verständlich ist, gibt es für sie nicht, auch wenn es technisch existiert.
- **Ehrlich statt höflich.** „Bringt mir nichts“ und „Funktion fehlt“ sind gültige Ergebnisse. Genauso
  wichtig: festhalten, was gut funktioniert und warum.
- **Kein Urteil ohne Beleg.** Jedes Problem braucht Zitat, Schritt im Protokoll oder Bildschirmfoto
  und lässt sich nachstellen. Bauchgefühle gehören unter „Eindrücke“.
- **Seed `saison` ist erfunden.** Die Meldungen sind ausgedacht. Beurteile, wie die App sie zeigt
  (verständlich, frisch genug, ehrlich über Unsicherheit), nie, ob die Werte stimmen.
- **Fakten nur mit Quelle anzweifeln.** Zollfreimengen, Ferientermine oder Vignettenpreise darf eine
  Persona glauben. Fällt dir als Tester etwas auf, prüfe es per Websuche und vermerke es als
  „Faktencheck“ mit Quelle. Daten der App änderst du in einem Nutzertest nicht.
- **Türkisch**: Beurteile Verständlichkeit und offensichtliche Fehler (deutsche Wörter in der
  türkischen Ansicht, abgebrochene Sätze). Die Feinprüfung durch Muttersprachler ersetzt das nicht.
- **Platzhalter sind kein Befund**: Impressum „Nutzertest, Teststraße 1“ und Hoster „Testhoster GmbH“
  setzt `serve.py`; im Betrieb stehen dort echte Angaben.
- **Externe Links** (Behörden, Automobilclubs) lassen sich im Container nicht öffnen. Bewerte, ob es
  sie gibt, wo sie stehen und ob ihr Text sagt, wohin sie führen.
- **Nichts am Code ändern.** Ein Nutzertest liefert Befunde; Korrekturen sind ein eigener Schritt.

## Grenzen

Chromium mit Handy-Größe ist kein echtes iPhone: Safari-eigene Abläufe (Teilen-Menü, „Zum
Home-Bildschirm“) lassen sich nur anhand der Anleitung in der App beurteilen. Langsames Netz und
Offline sind simuliert. Die Personas sind gespielt; sie ersetzen keine Gespräche mit echten
Reisenden, helfen aber, die richtigen Fragen für diese Gespräche zu finden. Schreib diese Grenzen in
jeden Gesamtbericht.
