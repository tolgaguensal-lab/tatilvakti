# Bewertung und Berichtsvorlagen

## Je Aufgabe

| Feld | Werte |
|---|---|
| Ergebnis | `geschafft` · `teilweise` · `nicht geschafft` · `fehlt` (die App bietet es nicht) |
| Weg | Tipps bis zur Antwort, Seiten in der Reihenfolge |
| Stolpersteine | wörtliches Zitat des Textes, der verwirrt hat, mit Bildschirmfoto |
| Verständlich (1–5) | 5 = sofort klar, 1 = nicht verstanden |
| Vertrauen (1–5) | 5 = würde danach handeln, 1 = glaubt es nicht |

## Je Persona: Was bringt es mir?

- **Nutzen in eigenen Worten:** ein bis drei Sätze aus Sicht der Persona, konkret („Ich weiß jetzt,
  dass wir am Montag statt am Samstag fahren“), keine Werbesprache.
- **Was fehlt mir:** konkrete Fragen, auf die sie keine Antwort fand.
- **Was nervt:** konkrete Stellen.
- **Vergleich:** besser, gleich oder schlechter als das, was sie sonst nutzt (siehe Persona), und warum.
- **Wiederkommen:** `ja` · `vielleicht` · `nein`, mit Grund.
- **Weiterempfehlen (0–10):** mit Grund.
- **Startbildschirm:** würde sie die App dort ablegen? Warum (nicht)?

## Probleme

Jedes Problem ist ein eigener Eintrag:

| Feld | Inhalt |
|---|---|
| id | `<persona>-<laufende Nr.>` |
| Schwere | `blocker` (Aufgabe unmöglich oder Information, die zu einer falschen Entscheidung führt) · `hoch` (Persona springt ab oder handelt falsch) · `mittel` (Umweg, Zweifel) · `niedrig` (Schönheitsfehler) |
| Art | `fehler` (tut nicht, was es zeigt) · `text` (missverständlich, falsche Sprache) · `darstellung` (Layout, Lesbarkeit) · `fehlt` (Funktion/Information fehlt) · `vertrauen` (Quelle, Frische, Seriosität unklar) |
| Was passiert | aus Sicht der Persona |
| Beleg | URL, Schritt im Protokoll, Bildschirmfoto, wörtliches Zitat |
| Nachstellen | Startbefehl (`--now`, `--seed`, Gerät, Sprache) und die Schritte |
| Vorschlag | was der Persona geholfen hätte (optional, ohne Code) |

Kein Problem ohne Beleg. Was nicht nachstellbar ist, kommt unter „Eindrücke“, nicht unter Probleme.

## Vorlage: Bericht je Persona (`<persona>.md`)

```markdown
# Nutzertest: <Name> (<id>)

Lage: <Satz aus der Persona> · App-Uhr <--now> · Seed <leer|saison> · <Gerät>, <Sprache>
Kurzurteil: <ein Satz, wie ihn die Persona einer Freundin sagen würde>

## Aufgaben
| # | Aufgabe | Ergebnis | Tipps | Verständlich | Vertrauen |
|---|---|---|---|---|---|

### 1. <Aufgabe>
<Was sie getan hat, was sie gesehen hat (Zitate), wo sie gezögert hat. Bildschirmfotos verlinken.>

## Was bringt es mir?
- Nutzen: …
- Was fehlt mir: …
- Was nervt: …
- Vergleich mit <Alternative>: …
- Wiederkommen: … · Weiterempfehlen: n/10 · Startbildschirm: …

## Probleme
| id | Schwere | Art | Kurz |
|---|---|---|---|
<je Problem danach ein Abschnitt mit Beleg und Nachstellen>

## Eindrücke
<Gefühle, Vermutungen, Dinge ohne harten Beleg>
```

## Vorlage: Gesamtbericht (`BERICHT.md`)

```markdown
# Nutzertest tatilvakti – <Datum>

## Kurz
<drei bis fünf Sätze: Wem bringt die App heute was, wem (noch) nicht, und das wichtigste Problem.>

## Nutzen je Persona
| Persona | Lage | Nutzen (1 Satz) | Wiederkommen | Empfehlen |
|---|---|---|---|---|

## Funktion × Persona
| Funktion | ayse | mehmet | … |
|---|---|---|---|
<je Zelle: bringt viel · bringt etwas · bringt nichts · nicht genutzt>

## Bestätigte Probleme, nach Wirkung
<nur gegengeprüfte; Schwere, wie viele Personas betroffen, Beleg, Vorschlag>

## Nicht bestätigt
<was die Gegenprüfung nicht nachstellen konnte, mit Grund>

## Was bleiben sollte
## Was fehlt (Wünsche der Personas)
## Grenzen dieses Tests
<simulierte Menschen, ausgedachte Meldungen, kein echtes iOS-Safari, keine echten Netze …>
```
