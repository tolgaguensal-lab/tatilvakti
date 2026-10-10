#!/usr/bin/env python3
"""Ferien-Dataset gegen den KMK-Ferienkalender abgleichen.

Die Ferientermine in tatilvakti/data/holidays.json werden einmal im Jahr ergänzt.
Dieses Skript liest die KMK-PDF (FER2026_27.pdf und Nachfolger, Link auf
https://www.kmk.org/service/ferienregelung/ferienkalender.html), zerlegt die
Tabelle und vergleicht sie mit dem Dataset. Die kursive Kennzeichnung der PDF
(schul- und unterrichtsfreie Tage) wird über den Font erkannt und kommt eigens
zu Wort – sie ist leicht zu überlesen und hat schon zu einer falschen Prüfmeldung
geführt (unterstellt: HB/HH/NI-Winter und BY-Winter wären kursiv; tatsächlich
sind nur fünf Zellen kursiv, u. a. Bayerns Herbstwoche um Allerheiligen und der
Oster-Brückentag in Baden-Württemberg).

Verglichen wird je Bundesland über die FREIEN STRECKEN des Schuljahres (Ferien-
blöcke plus angrenzende Wochenende und bundesweite Feiertage, wie die App sie
rechnet), nicht Spalte für Spalte: Das Dataset darf Blöcke anders einsortieren
als die KMK-Spalten – Bayerns Frühjahrsferien (Februar) stehen im Dataset unter
Winter –, aber jeder freie Block ab 8 Tagen muss auf beiden Seiten gleich sein.

Bewertung:
- Strecke ab 8 Tagen fehlt im Dataset oder ist dort anders → Fehler, Exit 1.
- Dataset enthält Strecken ab 8 Tagen, die die KMK-Tabelle nicht kennt → Fehler.
- Kursive Tage fehlen im Dataset: Ergibt ihr Block mindestens 8 freie Tage →
  Fehler (das Radar führt Blöcke ab 8 Tagen, siehe meta.note). Sonst Hinweis
  „bewusst nicht enthalten“: Kurze Blöcke führt das Radar nicht, die meta.note
  deckt das ab.
- Zeiträume der KMK-Tabelle, die das Dataset nicht pflegt → Hinweis (das Dataset
  pflegt nur die Zeiträume bis zum nächsten review_after).

    pip install pymupdf                                  # nur zum PDF-Lesen
    python3 scripts/kmk_diff.py ~/Downloads/FER2026_27.pdf

Braucht sonst nichts: Der Tabellen-Parser und der Abgleich arbeiten mit
nachgebauten Spans und sind ohne PDF testbar (tests/test_kmk_diff.py).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tatilvakti.holidays import HolidayRadar, Range, free_stretches  # noqa: E402

# KMK-Landname → Code im Dataset
STATE_CODE = {
    "Baden-Württemberg": "BW", "Bayern": "BY", "Berlin": "BE", "Brandenburg": "BB",
    "Bremen": "HB", "Hamburg": "HH", "Hessen": "HE", "Mecklenburg-Vorpommern": "MV",
    "Niedersachsen": "NI", "Nordrhein-Westfalen": "NW", "Rheinland-Pfalz": "RP",
    "Saarland": "SL", "Sachsen": "SN", "Sachsen-Anhalt": "ST", "Schleswig-Holstein": "SH",
    "Thüringen": "TH",
}
# Spaltenkopf der KMK-Tabelle → Zeitraum-Präfix im Dataset
COLUMN_PERIOD = {
    "Herbst": "herbst", "Weihnachten": "weihnachten", "Winter": "winter",
    "Ostern/Frühjahr": "ostern", "Himmelfahrt/Pfingsten": "pfingsten", "Sommer": "sommer",
}
MIN_FREE_DAYS = 8  # wie in content.py: darunter führt das Radar keine Blöcke

DATEISH_RE = re.compile(r"^\d{2}\.\d{2}\.?$")
RANGE_RE = re.compile(r"(\d{2}\.\d{2}\.)\s*-\s*(\d{2}\.\d{2}\.)")
SINGLE_RE = re.compile(r"\d{2}\.\d{2}\.")
YEAR_RE = re.compile(r"^\d{4}(/\d{4})?$")
LABEL_RE = re.compile(r"^([A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]+)(?: \(\d+\))?$")


def extract_spans(path: str) -> list[dict]:
    """Text-Spans der ersten PDF-Seite mit Position und Font. Braucht pymupdf."""
    import pymupdf  # erst hier: der Abgleich (und die Tests) kommen ohne PDF aus

    page = pymupdf.open(path)[0]
    spans: list[dict] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for s in line["spans"]:
                text = s["text"].strip()
                if text:
                    spans.append({"x": s["bbox"][0], "y0": s["bbox"][1], "y1": s["bbox"][3],
                                  "t": text, "font": s["font"]})
    return spans


def parse_table(spans: list[dict]) -> tuple[dict[tuple[str, str], tuple[str, bool]], dict[str, int]]:
    """Tabelle → ({(Land, Spalte): (Zelltext, kursiv)}, {Spalte: Jahr}).

    Erkennt: Spaltenköpfe samt Jahreszeile, Länderzeilen über die Labels am linken
    Rand, Zellinhalt über die nächstgelegene Spalten- und Zeilenposition, und die
    kursiven Spans über ihren Font (alles Datumsartige, das nicht im Hauptfont steht).
    """
    heads = sorted((s["x"], s["t"]) for s in spans if s["t"] in COLUMN_PERIOD)
    if not heads:
        raise SystemExit("KMK-Tabelle nicht erkannt: keine Spaltenköpfe (Herbst, Winter, …)")
    column_year: dict[str, int] = {}
    for x, name in heads:
        under = [s["t"] for s in spans
                 if YEAR_RE.match(s["t"]) and s["y0"] < 110 and abs(s["x"] - x) < 45]
        m = re.match(r"(\d{4})", under[0]) if under else None
        column_year[name] = int(m.group(1)) if m else 0

    dateish = [s for s in spans if DATEISH_RE.match(s["t"]) or RANGE_RE.search(s["t"])]
    common = Counter(s["font"] for s in dateish).most_common()
    body_font = common[0][0] if common else Counter(s["font"] for s in spans).most_common(1)[0][0]
    italic_fonts = {s["font"] for s in dateish if s["font"] != body_font}

    labels = sorted((s for s in spans if s["x"] < 100 and LABEL_RE.match(s["t"])),
                    key=lambda s: s["y0"])
    rows: list[tuple[float, str]] = []
    for s in labels:
        name = LABEL_RE.match(s["t"]).group(1)
        if name in STATE_CODE and all(n != name for _, n in rows):
            rows.append(((s["y0"] + s["y1"]) / 2, name))

    def column_of(x: float) -> str | None:
        best = min(heads, key=lambda h: abs(h[0] - x))
        return best[1]

    cells: dict[tuple[str, str], tuple[str, bool]] = {}
    for s in spans:
        if s["x"] < 180 or not (DATEISH_RE.match(s["t"]) or s["t"] in ("--", "-")
                                or " und " in s["t"] or RANGE_RE.search(s["t"])):
            continue
        col = column_of(s["x"])
        row = min(rows, key=lambda r: abs(r[0] - (s["y0"] + s["y1"]) / 2), default=None)
        if col is None or row is None or abs(row[0] - (s["y0"] + s["y1"]) / 2) > 10:
            continue
        key = (row[1], col)
        text, kursiv = cells.get(key, ("", False))
        cells[key] = ((text + " " + s["t"]).strip(), kursiv or s["font"] in italic_fonts)
    return cells, column_year


def cell_ranges(text: str, column: str, year: int) -> list[tuple[date, date]]:
    """Zelltext → Zeiträume. '--' heißt keins; '07.05. und 18.05.' zwei Einzeltage.

    Jahr je Spalte: Weihnachten mit Monatswechsel (ab Juli = Vorjahr), sonst das
    Jahr der Spaltenüberschrift.
    """
    text = text.replace("²", "").replace("¹", "").strip()
    if text in ("", "--", "-"):
        return []

    def year_of(month: int) -> int:
        if column == "Weihnachten":
            return year if month >= 7 else year + 1
        return year

    out: list[tuple[date, date]] = []
    used: set[str] = set()
    for a, b in RANGE_RE.findall(text):
        am, bm = int(a[3:5]), int(b[3:5])
        out.append((date(year_of(am), am, int(a[:2])), date(year_of(bm), bm, int(b[:2]))))
        used.update((a, b))
    for d in SINGLE_RE.findall(text):
        if d in used:
            continue
        m = int(d[3:5])
        out.append((date(year_of(m), m, int(d[:2])), date(year_of(m), m, int(d[:2]))))
    return out


def compare(cells, column_year, radar) -> tuple[list[str], list[str]]:
    """→ (Fehler, Hinweise). Vergleicht je Land die freien Strecken der gepflegten Zeiträume."""
    columns = [c for c in column_year if column_year[c]]
    app_columns = [c for c in columns if f"{COLUMN_PERIOD[c]}-{column_year[c]}" in radar.by_id]
    hints = [f"KMK-Zeitraum {COLUMN_PERIOD[c]}-{column_year[c]} ist im Dataset nicht gepflegt."
             for c in columns if c not in app_columns]

    def kmk_stretches(state: str, cols) -> list[Range]:
        dates: list[Range] = []
        for col in cols:
            text, _ = cells.get((state, col), ("", False))
            for s, e in cell_ranges(text, col, column_year[col]):
                dates.append(Range(s, e))
        return free_stretches(dates)

    def dataset_stretches(state: str) -> list[Range]:
        dates: list[Range] = []
        for col in app_columns:
            period = radar.by_id[f"{COLUMN_PERIOD[col]}-{column_year[col]}"]
            dates.extend(period.ranges.get(state, []))
        return free_stretches(dates)

    def covered_by(state: str, day: date) -> bool:
        return any(r.start <= day <= r.end for r in dataset_stretches(state))

    errors: list[str] = []
    for land, code in STATE_CODE.items():
        kmk_big = [r for r in kmk_stretches(land, app_columns) if r.days >= MIN_FREE_DAYS]
        app_big = [r for r in dataset_stretches(code) if r.days >= MIN_FREE_DAYS]
        if kmk_big != app_big:
            errors.append(
                f"{code}: freie Blöcke ab {MIN_FREE_DAYS} Tagen weichen ab – "
                f"KMK {[(str(r.start), str(r.end)) for r in kmk_big]} "
                f"vs. Dataset {[(str(r.start), str(r.end)) for r in app_big]}")
        for col in app_columns:
            text, kursiv = cells.get((land, col), ("", False))
            if not kursiv:
                continue
            ranges = cell_ranges(text, col, column_year[col])
            uncovered = sorted({d for s, e in ranges for d in (s, e) if not covered_by(code, d)})
            if not uncovered:
                hints.append(f"{code} {col}: kursiv ({text}) – im Block enthalten, korrekt.")
                continue
            own = [r for r in free_stretches([Range(s, e) for s, e in ranges])
                   if r.days >= MIN_FREE_DAYS]
            if own:
                errors.append(
                    f"{code} {col}: kursiv ({text}) fehlt im Dataset, ergibt aber Blöcke ab "
                    f"{MIN_FREE_DAYS} Tagen ({[(str(r.start), str(r.end)) for r in own]}) – "
                    f"das Radar müsste sie führen.")
            else:
                hints.append(f"{code} {col}: kursiv ({text}) – Block unter {MIN_FREE_DAYS} "
                             f"Tagen, bewusst nicht enthalten (meta.note).")
    return errors, hints


def main() -> int:
    ap = argparse.ArgumentParser(description="Ferien-Dataset gegen die KMK-PDF abgleichen.")
    ap.add_argument("pdf", help="Pfad zur KMK-Ferienkalender-PDF, z. B. FER2026_27.pdf")
    ap.add_argument("--data", default="tatilvakti/data",
                    help="Datenordner mit holidays.json (default: tatilvakti/data)")
    args = ap.parse_args()

    from tatilvakti.content import load_content  # erst hier: --help geht auch ohne Flask

    with open(Path(args.data) / "holidays.json", encoding="utf-8") as fh:
        radar = HolidayRadar(json.load(fh))
    cells, column_year = parse_table(extract_spans(args.pdf))
    errors, hints = compare(cells, column_year, radar)
    for h in hints:
        print(f"→ {h}")
    for e in errors:
        print(f"✗ {e}", file=sys.stderr)
    print(f"{len(hints)} Hinweise, {len(errors)} Fehler")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
