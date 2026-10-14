export const meta = {
  name: 'nutzertest',
  description: 'tatilvakti aus Sicht realistischer Personas im Handy-Browser nutzen, Befunde gegenprüfen, ehrliches Gesamturteil schreiben',
  whenToUse: 'Wenn man wissen will, was die App echten Nutzern bringt (vor dem Launch, nach UI-Änderungen). args: {"datum": "JJJJ-MM-TT", "personas": ["ayse", …] (optional)}',
  phases: [
    { title: 'Nutzen', detail: 'je Persona die App bedienen und beurteilen' },
    { title: 'Gegenprüfen', detail: 'Probleme je Persona in frischer Sitzung nachstellen' },
    { title: 'Auswerten', detail: 'Gesamtbericht über alle Personas' },
  ],
}

// Ids wie in .claude/skills/nutzertest/references/personas.md
const PERSONAS = ['ayse', 'mehmet', 'elif', 'can', 'zeynep', 'thomas']
const FUNKTIONEN = ['Startseite und Bundesland-Auswahl', 'Ferien-Radar', 'Ferien-Wellen', 'Routen und Vignetten',
  'Papiere/Checkliste', 'Zoll und Koffer', 'Grenze live (Wartezeiten)', 'Tagesverlauf', 'Wartezeit melden',
  'Teilen', 'Offline', 'Startbildschirm-Hinweis', 'Info, Quellen, Datenschutz', 'Türkische Ansicht']

const DATUM = (args && args.datum) || 'lauf'
const WAHL = (args && Array.isArray(args.personas) && args.personas.length) ? args.personas : PERSONAS
const unbekannt = WAHL.filter(p => !PERSONAS.includes(p))
if (unbekannt.length) throw new Error('Unbekannte Personas: ' + unbekannt.join(', ') + ' (bekannt: ' + PERSONAS.join(', ') + ')')
const OUT = `tmp/nutzertest-${DATUM}`

const GRUND = `
Du arbeitest im Repo tatilvakti (Wurzel: 'git rev-parse --show-toplevel', dorthin wechseln). Lies zuerst
VOLLSTÄNDIG .claude/skills/nutzertest/SKILL.md sowie references/personas.md und references/bewertung.md
in diesem Ordner und folge ihnen. Schreibe nur unter ${OUT}/ (in .gitignore). Ändere keinen Code, keine
Daten, committe nichts. Starte Server nur auf den dir zugeteilten Ports und beende sie am Ende
(kill "$(cat <ordner>/server.pid)"), kein pkill.`

const ERGEBNIS = {
  type: 'object',
  properties: {
    persona: { type: 'string' },
    bericht: { type: 'string', description: 'Pfad des Persona-Berichts' },
    kurzurteil: { type: 'string' },
    aufgaben: { type: 'array', items: { type: 'object', properties: {
      nr: { type: 'integer' }, aufgabe: { type: 'string' },
      ergebnis: { type: 'string', enum: ['geschafft', 'teilweise', 'nicht geschafft', 'fehlt'] },
      tipps: { type: 'integer' }, verstaendlich: { type: 'integer' }, vertrauen: { type: 'integer' } },
      required: ['nr', 'aufgabe', 'ergebnis', 'tipps', 'verstaendlich', 'vertrauen'] } },
    nutzen: { type: 'string' },
    fehlt: { type: 'array', items: { type: 'string' } },
    nervt: { type: 'array', items: { type: 'string' } },
    vergleich: { type: 'string' },
    wiederkommen: { type: 'string', enum: ['ja', 'vielleicht', 'nein'] },
    empfehlen: { type: 'integer' },
    startbildschirm: { type: 'string' },
    funktionen: { type: 'array', items: { type: 'object', properties: {
      funktion: { type: 'string' }, wert: { type: 'string', enum: ['bringt viel', 'bringt etwas', 'bringt nichts', 'nicht genutzt'] },
      warum: { type: 'string' } }, required: ['funktion', 'wert', 'warum'] } },
    probleme: { type: 'array', items: { type: 'object', properties: {
      id: { type: 'string' }, schwere: { type: 'string', enum: ['blocker', 'hoch', 'mittel', 'niedrig'] },
      art: { type: 'string', enum: ['fehler', 'text', 'darstellung', 'fehlt', 'vertrauen'] },
      kurz: { type: 'string' }, beleg: { type: 'string' }, nachstellen: { type: 'string' }, vorschlag: { type: 'string' } },
      required: ['id', 'schwere', 'art', 'kurz', 'beleg', 'nachstellen'] } },
  },
  required: ['persona', 'bericht', 'kurzurteil', 'aufgaben', 'nutzen', 'fehlt', 'nervt', 'vergleich',
    'wiederkommen', 'empfehlen', 'startbildschirm', 'funktionen', 'probleme'],
}

const PRUEFUNG = {
  type: 'object',
  properties: {
    urteile: { type: 'array', items: { type: 'object', properties: {
      id: { type: 'string' },
      urteil: { type: 'string', enum: ['bestätigt', 'nicht nachstellbar', 'falsch verstanden', 'gewollt'] },
      schwere: { type: 'string', enum: ['blocker', 'hoch', 'mittel', 'niedrig'] },
      nachgestellt: { type: 'string', description: 'was du selbst ausgeführt und gesehen hast' },
      ursache: { type: 'string', description: 'optional: Stelle im Code, erst nach dem Nachstellen gesucht' } },
      required: ['id', 'urteil', 'schwere', 'nachgestellt'] } },
  },
  required: ['urteile'],
}

const GESAMT = {
  type: 'object',
  properties: {
    bericht: { type: 'string' },
    kurz: { type: 'string' },
    top_probleme: { type: 'array', items: { type: 'string' } },
  },
  required: ['bericht', 'kurz', 'top_probleme'],
}

const ergebnisse = await pipeline(
  WAHL,
  (p, _, i) => agent(`${GRUND}

Du BIST die Persona "${p}" aus references/personas.md und testest tatilvakti genau so, wie diese Person
es in ihrer Lage tun würde. Arbeitsordner: ${OUT}/${p}/ (Server-Port ${18900 + 20 * i}). Bericht nach der
Vorlage in references/bewertung.md nach ${OUT}/${p}.md.

Vorgehen nach SKILL.md "Ablauf für eine Persona": App mit --now/--seed der Persona starten, mit
scripts/user.js in kleinen Schritten bedienen (Gerät, Sprache, ggf. --slow und offline wie in der
Persona), nach jedem Häppchen lesen und wie die Persona entscheiden. Alle Aufgaben der Persona
versuchen. Bildschirmfotos der wichtigen Momente. Verdächtige Fehler einmal in frischer Session
nachstellen.

Beurteile am Ende jede dieser Funktionen aus Sicht der Persona (Feld funktionen; "nicht genutzt",
wenn die Persona sie in ihrer Lage gar nicht gebraucht oder gefunden hat): ${FUNKTIONEN.join(', ')}.

Gib das Ergebnis strukturiert zurück; bericht = Pfad deines Berichts.`,
    { label: 'nutzen:' + p, phase: 'Nutzen', schema: ERGEBNIS }),
  (erg, p, i) => {
    if (!erg) return null
    const offen = (erg.probleme || []).filter(x => x.schwere !== 'niedrig')
    if (!offen.length) { log(`${p}: keine Probleme ab "mittel" zu prüfen`); return { persona: p, erg, urteile: [] } }
    return agent(`${GRUND}

Du bist GEGENPRÜFER für den Nutzertest der Persona "${p}" (Bericht ${erg.bericht}). Prüfe jedes der
folgenden Probleme unabhängig: Stelle es selbst nach (eigener Ordner ${OUT}/${p}-pruefung/, Port
${18910 + 20 * i}, neue --session, gleiche --now/--seed/Gerät/Sprache wie in der Persona). Versuche es zu
widerlegen. Urteile:
- "bestätigt": du siehst dasselbe; Schwere ggf. korrigieren (Maßstab references/bewertung.md),
- "nicht nachstellbar": trotz zweier Versuche nicht gesehen,
- "falsch verstanden": die App tut es doch, der Tester hat es übersehen (Beleg!),
- "gewollt": bewusstes Verhalten, das für die Persona trotzdem kein Problem ist (Beleg aus dem sichtbaren Text).
Erst NACH dem Nachstellen darfst du im Code nach der Ursache suchen (Feld ursache, Datei:Zeile).

Probleme: ${JSON.stringify(offen, null, 2)}`,
      { label: 'pruefen:' + p, phase: 'Gegenprüfen', schema: PRUEFUNG })
      .then(pr => ({ persona: p, erg, urteile: pr ? pr.urteile : null }))
  },
)

const fertig = ergebnisse.filter(Boolean)
const ausgefallen = WAHL.filter(p => !fertig.some(r => r.persona === p))
if (ausgefallen.length) log('Ohne Ergebnis: ' + ausgefallen.join(', '))
if (!fertig.length) return { fehler: 'keine Persona hat ein Ergebnis geliefert' }

phase('Auswerten')
const gesamt = await agent(`${GRUND}

Schreibe den GESAMTBERICHT des Nutzertests nach der Vorlage "Gesamtbericht" in references/bewertung.md
nach ${OUT}/BERICHT.md. Grundlage: die Persona-Berichte (lies sie: ${fertig.map(r => r.erg.bericht).join(', ')})
und diese strukturierten Ergebnisse samt Gegenprüfung:
${JSON.stringify(fertig, null, 2)}
${ausgefallen.length ? 'Ohne Ergebnis (im Bericht nennen): ' + ausgefallen.join(', ') : ''}

Regeln: Unter "Bestätigte Probleme" nur, was die Gegenprüfung bestätigt hat (Probleme der Schwere
"niedrig" wurden nicht gegengeprüft: gesondert als "ungeprüft, niedrig" aufführen). Gleiche Probleme
mehrerer Personas zusammenfassen und die Zahl der Betroffenen nennen. Ordne nach Wirkung: Schwere ×
Zahl der Betroffenen × wie zentral die Aufgabe für die App ist. Die Tabelle Funktion × Persona aus den
Feldern funktionen. Ehrlich und konkret: Wem bringt die App heute was, wem nicht, und warum. Keine
Werbesprache. Grenzen des Tests nennen (SKILL.md "Grenzen"). Verlinke Persona-Berichte und
Bildschirmfotos relativ.`,
  { label: 'auswerten', phase: 'Auswerten', schema: GESAMT })

return { ordner: OUT, gesamt, personas: fertig.map(r => ({ persona: r.persona, kurzurteil: r.erg.kurzurteil,
  wiederkommen: r.erg.wiederkommen, empfehlen: r.erg.empfehlen,
  bestaetigt: (r.urteile || []).filter(u => u.urteil === 'bestätigt').length })), ausgefallen }
