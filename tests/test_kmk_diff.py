"""Skript scripts/kmk_diff.py: Tabellenzerlegung und Abgleich, mit nachgebauten KMK-Spans.

Kein PDF nötig: parse_table() und compare() arbeiten mit Spans wie sie extract_spans()
aus der KMK-PDF liest; die kursiven Markierungen stecken im Font-Feld.
"""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load_module():
    spec = importlib.util.spec_from_file_location("tv_kmk_diff", ROOT / "scripts" / "kmk_diff.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def kmk():
    return load_module()


def span(x: float, y0: float, text: str, font: str = "F2") -> dict:
    return {"x": x, "y0": y0, "y1": y0 + 8.6, "t": text, "font": font}


def mini_table() -> list[dict]:
    """Zwei Länderzeilen, zwei Spalten: kursiv, regulär und '--' gemischt."""
    return [
        span(207, 72, "Herbst"), span(386, 72, "Winter"),  # Spaltenköpfe
        span(207, 80, "2026"), span(386, 80, "2027"),      # Jahreszeile
        span(62, 96, "Bayern"),
        span(193, 96, "02.11. - 06.11.", "F3"),            # kursiv (Allerheiligen-Woche)
        span(373, 96, "08.02. - 12.02."),                  # BY Frühjahrsferien (Februar)
        span(62, 116, "Hessen"),
        span(193, 116, "05.10. - 17.10."),
        span(373, 116, "--"),
    ]


def radar_with(periods: list[dict]):
    from tatilvakti.holidays import HolidayRadar
    return HolidayRadar({"states": {"BW": {"population": 10}, "BY": {"population": 5},
                                    "HE": {"population": 1}, "HH": {"population": 1}},
                         "periods": periods})


def period(pid: str, ranges: dict[str, str]) -> dict:
    return {"id": pid, "kind": "x", "label": {"de": "P", "tr": "P"},
            "slug": {"de": pid, "tr": pid},
            "ranges": {code: [pair.split(" - ") for pair in pairs.split(",")] if pairs else []
                       for code, pairs in ranges.items()}}


def test_parse_table_finds_cells_years_and_italics(kmk):
    cells, column_year = kmk.parse_table(mini_table())
    assert column_year == {"Herbst": 2026, "Winter": 2027}
    assert cells[("Bayern", "Herbst")] == ("02.11. - 06.11.", True)
    assert cells[("Bayern", "Winter")] == ("08.02. - 12.02.", False)
    assert cells[("Hessen", "Herbst")] == ("05.10. - 17.10.", False)
    assert cells[("Hessen", "Winter")] == ("--", False)


def test_cell_ranges_parse_all_shapes(kmk):
    assert kmk.cell_ranges("12.10. - 24.10.", "Herbst", 2026) == [(date(2026, 10, 12), date(2026, 10, 24))]
    # Weihnachten: Monat ab Juli = Vorjahr (24.12.2026 – 08.01.2027)
    assert kmk.cell_ranges("24.12. - 08.01.", "Weihnachten", 2026) == [(date(2026, 12, 24), date(2027, 1, 8))]
    # Einzel- und 'und'-Tage
    assert kmk.cell_ranges("07.05. und 18.05.", "Himmelfahrt/Pfingsten", 2027) == [
        (date(2027, 5, 7), date(2027, 5, 7)), (date(2027, 5, 18), date(2027, 5, 18))]
    assert kmk.cell_ranges("--", "Winter", 2027) == []
    assert kmk.cell_ranges("29.01.", "Winter", 2027) == [(date(2027, 1, 29), date(2027, 1, 29))]


def test_compare_flags_missing_long_block(kmk):
    """KMK kennt für Hessen eine ≥8-Tage-Strecke, das Dataset nicht → Fehler; umgekehrt für BW."""
    cells, column_year = kmk.parse_table(mini_table())
    errors, hints = kmk.compare(cells, column_year, radar_with([
        period("herbst-2026", {"BW": "2026-10-26 - 2026-10-30", "BY": "2026-11-02 - 2026-11-06"}),
        period("winter-2027", {"BY": "2027-02-08 - 2027-02-12"}),
    ]))
    assert any(e.startswith("HE:") for e in errors), errors
    # Gegenrichtung: BW hat im Dataset eine ≥8-Tage-Strecke, die die (Mini-)KMK-Tabelle nicht kennt
    assert any(e.startswith("BW:") and "weichen ab" in e for e in errors), errors
    # Bayern (regulär und kursiv, beide ≥ 8 Tage) stimmt auf beiden Seiten überein
    assert not any(e.startswith("BY:") for e in errors), errors
    assert any("BY Herbst: kursiv" in h and "im Block enthalten" in h for h in hints), hints


def test_compare_flags_missing_kursiv_block(kmk):
    """Kursive Herbstwoche in Bayern (9 Tage frei mit Wochenende) fehlt im Dataset → Fehler."""
    cells = {("Bayern", "Herbst"): ("02.11. - 06.11.", True)}
    errors, _ = kmk.compare(cells, {"Herbst": 2026}, radar_with([
        period("herbst-2026", {"BW": "2026-10-26 - 2026-10-30"}),
    ]))
    assert any("BY" in e and "kursiv" in e for e in errors), errors


def test_compare_hints_short_kursiv_block(kmk):
    """HH: kursiver 1-Tage-Winterblock (3 Tage frei mit Wochenende) – bewusst raus, nur Hinweis."""
    cells = {("Hamburg", "Winter"): ("29.01.", True),
             ("Bayern", "Winter"): ("08.02. - 12.02.", False)}
    errors, hints = kmk.compare(cells, {"Winter": 2027}, radar_with([
        period("winter-2027", {"BY": "2027-02-08 - 2027-02-12"}),
    ]))
    assert not errors, errors
    assert any("bewusst nicht enthalten" in h for h in hints), hints


def test_compare_hints_unmaintained_periods(kmk):
    """Spalten der KMK-Tabelle ohne gepflegten Zeitraum: Hinweis statt Fehler."""
    cells = {("Bayern", "Herbst"): ("02.11. - 06.11.", False)}
    errors, hints = kmk.compare(cells, {"Herbst": 2026}, radar_with([
        period("winter-2027", {"BY": "2027-02-08 - 2027-02-12"}),
    ]))
    assert not errors, errors
    assert any("herbst-2026" in h and "nicht gepflegt" in h for h in hints), hints
