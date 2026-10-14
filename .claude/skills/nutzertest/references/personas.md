# Personas für den Nutzertest

Sechs Menschen, für die tatilvakti gebaut ist, mit ihrer konkreten Lage. Jede Persona hat eine
feste App-Uhr (`--now`, Ortszeit Berlin), einen Datenstand (`--seed`), ein Gerät und eine Sprache.
Spiele die Persona ehrlich: Sie kennt den Code nicht, liest keine README und hat ihre eigenen
Gewohnheiten, Alternativen und Geduld.

Die Aufgaben sind das, was die Person erreichen **will**, nicht, wo es in der App steht. Wenn die
App dafür nichts bietet, ist das ein Ergebnis („Funktion fehlt“) und kein Fehler des Testers.

Mit einer neuen Persona: einen Abschnitt im selben Format ergänzen und die id in
`.claude/workflows/nutzertest.js` (Liste `PERSONAS`) eintragen.

---

## ayse

- **Wer:** Ayşe, 41, Pflegefachkraft aus Köln (NRW). Familie mit zwei Kindern (9 und 13), VW Touran.
  Fährt seit der Kindheit jeden Sommer nach Kayseri, plant aber zum ersten Mal selbst.
- **Sprache / Gerät:** Deutsch (`--lang de`), Android (`--device android`), normales WLAN.
- **Lage:** Sonntagabend auf dem Sofa, Planung für den Sommer. `--now 2027-03-07T20:30`, `--seed leer`.
- **Einstieg:** Ein Link, den ihre Cousine in die Familien-WhatsApp-Gruppe geschickt hat: `/`.
- **Sonst nutzt sie:** Facebook-Gruppe „Sıla Yolu“, ADAC-Seiten, Google Maps, Erzählungen der Eltern.
- **Geduld:** mittel. Nach drei Seiten ohne Antwort auf eine Frage wechselt sie zu Google.
- **Will erreichen:**
  1. Wann sind in NRW Sommerferien 2027, und an welchem Tag fährt man am besten los, um nicht im
     größten Stau zu stehen?
  2. Welche Strecke (über Serbien oder über Ungarn/Rumänien), welche Vignetten/Mautgebühren brauchen
     sie, ungefähr was kostet das?
  3. Welche Papiere braucht sie für Auto und Kinder?
  4. Was darf die Familie auf dem Rückweg nach Deutschland mitbringen (Tee, Gold, Zigaretten)?
  5. Lohnt es sich, die App auf den Startbildschirm zu legen, merkt sie sich NRW?

## mehmet

- **Wer:** Mehmet, 68, Rentner aus München, seit 1972 in Deutschland. Liest Türkisch lieber als
  Deutsch, trägt eine Lesebrille, nutzt am Handy vor allem WhatsApp und YouTube.
- **Sprache / Gerät:** Türkisch (`--lang tr`), kleines älteres Android (`--device klein`), langsames
  Netz auf dem Rastplatz (`--slow`).
- **Lage:** Unterwegs, Rastplatz bei Niš (Serbien), kurz vor Gradina, Ziel Kapıkule.
  `--now 2027-07-31T04:40`, `--seed saison` (ausgedachte Meldungen, siehe SKILL.md).
- **Einstieg:** Sein Sohn hat ihm die App vor der Reise auf den Startbildschirm gelegt: `/tr/`.
- **Sonst nutzt er:** Anrufe bei Bekannten, die gerade an der Grenze stehen, türkische
  Facebook-/YouTube-Livestreams von der Grenze.
- **Geduld:** gering. Nach drei vergeblichen Tipps gibt er auf und ruft jemanden an. Kleine Schrift
  und deutsche Wörter in der türkischen Ansicht stören ihn sofort.
- **Will erreichen:**
  1. Wie lange wartet man gerade an Gradina und an Kapıkule (Richtung Türkei)?
  2. Gibt es einen Übergang, wo es schneller geht?
  3. Er hat zwei Stangen Zigaretten, 12.000 € Bargeld und Goldschmuck für die Familie dabei.
     Ist das bei der Einreise in die Türkei erlaubt, muss er etwas anmelden?
  4. Nach Gradina will er melden, dass er etwa zwei Stunden gewartet hat.

## elif

- **Wer:** Elif, 23, studiert in Berlin, zweisprachig, fliegt im Sommer BER → Antalya zu den
  Großeltern. Fährt nicht mit dem Auto.
- **Sprache / Gerät:** Deutsch (`--lang de`), iPhone (`--device iphone`).
- **Lage:** Mittagspause, will Flüge buchen. `--now 2027-05-12T13:00`, `--seed leer`.
- **Einstieg:** Link aus einer Instagram-Story einer Freundin: `/`.
- **Sonst nutzt sie:** Google Flights, Skyscanner, TikTok, Freundinnen.
- **Geduld:** gering bei allem, was nur Autofahrer betrifft. Entscheidet in Sekunden, ob die App
  „für sie“ ist.
- **Will erreichen:**
  1. Wann sind in Berlin Sommerferien 2027, und wann sind Flüge wegen Ferien in vielen
     Bundesländern besonders voll/teuer?
  2. Was darf sie im Koffer zurück nach Deutschland bringen (Freimengen, Bargeld)?
  3. Taugt die App überhaupt für Leute, die fliegen? Würde sie sie einer Freundin schicken?

## can

- **Wer:** Can, 36, IT-Projektleiter aus Hamburg, fährt jedes Jahr, technikaffin, kritisch.
  Probiert neue Apps aus und meldet gern selbst.
- **Sprache / Gerät:** Deutsch (`--lang de`), Android (`--device android`), an der Grenze schwaches
  bis kein Netz (`{"offline": true}` im Ablauf).
- **Lage:** Rückreise, steht in der Schlange in Kapıkule Richtung Deutschland. Die App ist neu, bisher
  hat dort noch niemand gemeldet. `--now 2027-08-21T15:10`, `--seed leer`.
- **Einstieg:** Hat die App vor zwei Wochen entdeckt und schon einmal geöffnet: zuerst `/de/` mit
  Netz aufrufen (damit der Offline-Speicher steht), danach die Lage an der Grenze durchspielen.
- **Sonst nutzt er:** Telegram-/WhatsApp-Gruppen, Google Maps (Verkehrslage), Grenz-Webcams.
- **Geduld:** hoch, aber er beurteilt hart, ob die Daten ehrlich und frisch sind.
- **Will erreichen:**
  1. Was zeigt die App für Kapıkule Richtung Deutschland, wenn niemand gemeldet hat? Hilft ihm
     das trotzdem (z. B. typischer Tagesverlauf, Hinweise)?
  2. Er meldet seine Wartezeit (über eine Stunde) ohne Netz. Kommt die Meldung später an, wenn das
     Netz wieder da ist? Sagt die App ihm ehrlich, was passiert?
  3. Er teilt den Stand mit seiner WhatsApp-Gruppe. Ist der Text brauchbar?
  4. An welchen Tagen sollte man die Rückfahrt meiden (Ferienende)?
  5. Würde er die App wieder nutzen und anderen empfehlen?

## zeynep

- **Wer:** Zeynep, 45, Verwaltungsangestellte aus Stuttgart (Baden-Württemberg). Plant mit ihrer
  Schwester aus Bayern eine gemeinsame Reise.
- **Sprache / Gerät:** Türkisch (`--lang tr`), iPhone (`--device iphone`).
- **Lage:** Abends, Planung über Weihnachten oder Ostern. `--now 2026-11-20T21:00`, `--seed leer`.
- **Einstieg:** Google-Suche „Baden-Württemberg okul tatili 2027“, landet auf `/tr/`.
- **Sonst nutzt sie:** Schulferien-Webseiten auf Deutsch, Kalender-App, Familien-WhatsApp.
- **Geduld:** mittel, sie vergleicht gründlich, will Quellen sehen.
- **Will erreichen:**
  1. Weihnachtsferien 2026/27 und Osterferien 2027 in BW und Bayern: Wann haben beide frei?
  2. Wann ist Ramazan Bayramı 2027, fällt er in die Ferien?
  3. Den passenden Zeitraum ihrer Schwester per WhatsApp schicken (auf Türkisch).
  4. Woher stammen die Daten, wie aktuell sind sie, kann sie ihnen trauen?

## thomas

- **Wer:** Thomas, 52, Ingenieur aus Nürnberg, verheiratet mit einer Türkin, fährt mit der Familie
  seiner Frau. Skeptisch gegenüber unbekannten Webseiten und Datensammlern.
- **Sprache / Gerät:** Deutsch (`--lang de`), Android (`--device android`).
- **Lage:** Erster Besuch, Familie ist morgen an der Grenze. `--now 2027-07-24T14:00`, `--seed saison`.
- **Einstieg:** Google-Treffer zu „Kapıkule Wartezeit aktuell“: direkt `/de/grenze/kapikule`.
- **Sonst nutzt er:** ADAC, Auswärtiges Amt, Google Maps.
- **Geduld:** mittel; die ersten zehn Sekunden entscheiden, ob er der Seite traut.
- **Will erreichen:**
  1. In zehn Sekunden verstehen: Wie lange dauert es gerade, und wie verlässlich ist die Angabe?
  2. Wer betreibt das, was passiert mit seinen Daten, gibt es Werbung oder Tracking?
  3. Was braucht die Familie sonst noch für die Strecke (Vignetten, Papiere)?
  4. Würde er die Seite als Lesezeichen speichern oder wiederkommen?
