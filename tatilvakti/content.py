"""Kuratierte Datensätze laden und prüfen.

Jeder Datensatz hat meta.as_of (geprüft am) und meta.review_after. Ist review_after
überschritten, zeigt die App sichtbar „Prüfung fällig“, statt alte Werte als
aktuell auszugeben.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from .holidays import Range, free_stretches
from .i18n import LANGS, fold

DATA_DIR = Path(__file__).parent / "data"
DATASETS = ("holidays", "customs", "transit", "crossings")
CUSTOMS_STATUSES = ("ok", "limit", "declare", "no")
TOLL_SYSTEMS = ("vignette", "evignette", "toll", "hgs")
# Kürzeste freie Zeit am Stück (Ferien plus angrenzende Wochenenden und bundesweite Feiertage),
# die das Ferien-Radar führt. Kurze Ferien (Brückentage, zwei Tage Winterferien) bleiben draußen:
# Für eine Reise in die Türkei reichen sie selten, und als eigene Blöcke erzeugten sie
# Reisewellen, die es Richtung Türkei so nicht gibt (z. B. Freitag nach Himmelfahrt in 8 Ländern).
# Der Hinweis in holidays.json (meta.note) sagt das den Nutzern.
MIN_FREE_DAYS = 8
# Bayram-Termine (Diyanet): Festtage je Art, der Arife-Tag liegt direkt davor
BAYRAM_DAYS = {"ramazan": 3, "kurban": 4}
# Alte URLs der Alt-App (data/redirects.json): dauerhaft weiterleiten oder „gibt es nicht mehr“
REDIRECT_CODES = (301, 410)
REDIRECT_FIELDS = {"from", "to", "code", "note"}
# Namensräume von v2: Ein 404 dort ist eine echte Antwort, keine alte URL
REDIRECT_RESERVED = ("/api/v1/", "/static/")


@dataclass
class Content:
    holidays: dict
    customs: dict
    transit: dict
    crossings: dict
    crossing_by_id: dict = field(default_factory=dict)
    redirects: dict = field(default_factory=lambda: {"meta": {}, "redirects": []})
    redirect_by_path: dict = field(default_factory=dict)  # redirect_key(from) → Eintrag

    def meta(self, name: str) -> dict:
        return getattr(self, name)["meta"]

    def review_due(self, name: str, today: date) -> bool:
        return is_due(self.meta(name).get("review_after"), today)

    def set_redirects(self, data) -> None:
        """Weiterleitungen übernehmen und nach Pfad indizieren (geprüft wird in validate)."""
        self.redirects = data
        entries = data.get("redirects") if isinstance(data, dict) else None
        self.redirect_by_path = {}
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and isinstance(entry.get("from"), str):
                self.redirect_by_path.setdefault(redirect_key(entry["from"]), entry)


def is_due(review_after: str | None, today: date) -> bool:
    return bool(review_after) and today > date.fromisoformat(review_after)


def load_content(data_dir: Path = DATA_DIR) -> Content:
    raw = {}
    for name in DATASETS:
        with open(data_dir / f"{name}.json", encoding="utf-8") as fh:
            raw[name] = json.load(fh)
    content = Content(**raw)
    content.crossing_by_id = {c["id"]: c for c in content.crossings["crossings"]}
    with open(data_dir / "redirects.json", encoding="utf-8") as fh:
        content.set_redirects(json.load(fh))
    # Suchindex für den Zoll-Check (diakritik-unabhängig, beide Sprachen)
    for item in content.customs["items"]:
        parts = [item["title"][l] for l in LANGS] + [item["rule"][l] for l in LANGS] + item.get("keywords", [])
        item["search"] = fold(" ".join(parts))
    return content


# ---------------------------------------------------------------- Validierung

def redirect_key(path: str) -> str:
    """Vergleichsform eines Pfads für die Weiterleitungen: '/alt/' und '/alt' sind dieselbe URL."""
    return path.rstrip("/") or "/"


def _is_local_path(value, allow_query: bool) -> bool:
    """Interner, relativer Pfad: beginnt mit genau einem /, keine Leer- oder Steuerzeichen.

    '//host' wäre protokollrelativ (fremde Seite), '\\' werten manche Browser wie '/'.
    """
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
        return False
    if "\\" in value or not value.isprintable() or any(ch.isspace() for ch in value):
        return False
    return allow_query or ("?" not in value and "#" not in value)


def validate_redirects(data) -> list[str]:
    """Aufbau von data/redirects.json. Die Prüfung gegen die Routen folgt nach deren Anlage
    (redirect_route_problems), weil erst dann feststeht, welche Pfade v2 selbst beantwortet."""
    if not isinstance(data, dict) or not isinstance(data.get("redirects"), list):
        return ["redirects: Objekt mit meta und Liste redirects erwartet"]
    problems: list[str] = []
    _check_date((data.get("meta") or {}).get("as_of"), "redirects.meta.as_of", problems)
    sources: dict[str, str] = {}
    for idx, entry in enumerate(data["redirects"]):
        if not isinstance(entry, dict):
            problems.append(f"redirects[{idx}]: Objekt mit from, to und code erwartet")
            continue
        src, dst, code = entry.get("from"), entry.get("to"), entry.get("code")
        where = f"redirects {src!r}" if isinstance(src, str) else f"redirects[{idx}]"
        unknown = sorted(set(entry) - REDIRECT_FIELDS)
        if unknown:
            problems.append(f"{where}: unbekannte Felder {unknown} (erlaubt: from, to, code, note)")
        if not _is_local_path(src, allow_query=False):
            problems.append(f"{where}: from muss ein Pfad sein, der mit / beginnt (ohne Query, Fragment, Leerzeichen)")
        elif redirect_key(src) == "/":
            problems.append(f"{where}: / ist die Startseite von v2")
        elif (src + "/").startswith(REDIRECT_RESERVED):
            problems.append(f"{where}: {', '.join(REDIRECT_RESERVED)} gehören v2")
        elif redirect_key(src) in sources:
            problems.append(f"{where}: doppelt (auch {sources[redirect_key(src)]!r})")
        else:
            sources[redirect_key(src)] = src
        if type(code) is not int or code not in REDIRECT_CODES:  # type(): True wäre sonst 1
            problems.append(f"{where}: code muss 301 oder 410 sein, ist {code!r}")
        elif code == 410 and dst is not None:
            problems.append(f"{where}: 410 hat kein Ziel, to muss null sein")
        elif code == 301 and not _is_local_path(dst, allow_query=True):
            problems.append(f"{where}: to muss ein interner Pfad sein, z. B. /de/ferien (keine fremde Seite)")
        if "note" in entry and not isinstance(entry["note"], str):
            problems.append(f"{where}: note muss Text sein")
    for entry in data["redirects"]:  # keine Ketten: ein Ziel ist nie selbst eine alte URL
        if isinstance(entry, dict) and entry.get("code") == 301 and _is_local_path(entry.get("to"), True):
            if redirect_key(urlsplit(entry["to"]).path) in sources:
                problems.append(f"redirects {entry.get('from')!r}: Ziel {entry['to']!r} ist selbst eine alte URL (Kette)")
    return problems


def redirect_route_problems(data, is_route: Callable[[str], bool]) -> list[str]:
    """Alte URLs dürfen keine eigene Route treffen (sie griffen nie), Ziele müssen eine sein."""
    problems = []
    for entry in data.get("redirects", []):
        src, dst = entry["from"], entry.get("to")
        if is_route(src) or (redirect_key(src) != src and is_route(redirect_key(src))):
            problems.append(f"redirects {src!r}: ist eine eigene Route von v2, die Weiterleitung griffe nie")
        if dst is not None and not is_route(urlsplit(dst).path):
            problems.append(f"redirects {src!r}: Ziel {dst!r} ist keine Seite von v2")
    return problems


def _is_bilingual(value) -> bool:
    return isinstance(value, dict) and all(isinstance(value.get(l), str) and value[l].strip() for l in LANGS)


def _check_date(value, where, problems):
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        problems.append(f"{where}: ungültiges Datum {value!r}")


def _check_url(value, where, problems):
    if not (isinstance(value, str) and value.startswith("https://")):
        problems.append(f"{where}: Quelle braucht https-URL, ist {value!r}")


def _check_source(src, where, problems):
    if not (isinstance(src, dict) and isinstance(src.get("name"), str) and src["name"].strip()):
        problems.append(f"{where}: Quelle braucht Name und URL")
        return
    _check_url(src.get("url"), where, problems)


def _validate_bayrams(bayrams, problems) -> None:
    """Bayram-Termine: optional, aber wenn vorhanden mit Quelle, Prüfdatum und stimmigen Tagen."""
    if bayrams is None:
        return
    if not isinstance(bayrams, dict):
        problems.append("holidays.bayrams: Objekt mit as_of, source und items erwartet")
        return
    _check_date(bayrams.get("as_of"), "holidays.bayrams.as_of", problems)
    _check_source(bayrams.get("source"), "holidays.bayrams.source", problems)
    seen = set()
    for item in bayrams.get("items", []):
        bid = item.get("id")
        if bid in seen:
            problems.append(f"holidays.bayrams: doppelte id {bid}")
        seen.add(bid)
        kind = item.get("kind")
        if kind not in BAYRAM_DAYS:
            problems.append(f"holidays.bayrams.{bid}: unbekannte Art {kind!r}")
        if not _is_bilingual(item.get("label")):
            problems.append(f"holidays.bayrams.{bid}.label: nicht zweisprachig")
        try:
            arife, start, end = (date.fromisoformat(item[key]) for key in ("arife", "start", "end"))
        except (KeyError, TypeError, ValueError):
            problems.append(f"holidays.bayrams.{bid}: Arife, Beginn und Ende brauchen ein gültiges Datum")
            continue
        if start - arife != timedelta(days=1):
            problems.append(f"holidays.bayrams.{bid}: Arife muss der Tag vor dem ersten Festtag sein")
        if kind in BAYRAM_DAYS and (end - start).days + 1 != BAYRAM_DAYS[kind]:
            problems.append(f"holidays.bayrams.{bid}: {kind} dauert {BAYRAM_DAYS[kind]} Tage, nicht {(end - start).days + 1}")


def validate(content: Content) -> list[str]:
    """Liefert eine Liste von Problemen (leer = alles in Ordnung)."""
    problems: list[str] = []
    for name in DATASETS:
        meta = content.meta(name)
        _check_date(meta.get("as_of"), f"{name}.meta.as_of", problems)
        _check_date(meta.get("review_after"), f"{name}.meta.review_after", problems)

    # Ferien: alle 16 Länder je Zeitraum, Start <= Ende, sinnvolle Länge. Eine leere Liste
    # heißt „dieses Land hat in dem Zeitraum keine Ferien“ (z. B. Pfingsten in Hessen) –
    # der Schlüssel muss trotzdem da sein, damit ein vergessenes Land auffällt.
    states = content.holidays["states"]
    if len(states) != 16:
        problems.append(f"holidays: {len(states)} statt 16 Bundesländer")
    for state, info in states.items():
        if not isinstance(info.get("population"), int) or info["population"] <= 0:
            problems.append(f"holidays.states.{state}: Einwohnerzahl fehlt")
    for src in content.holidays["meta"]["sources"]:
        _check_url(src.get("url"), "holidays.meta.sources", problems)
    seen_ids = set()
    taken: dict[str, list[tuple[date, date, str]]] = {}  # Land → belegte Ferientage (alle Zeiträume)
    blocks: dict[str, list[tuple[Range, str]]] = {}      # Land → freie Blöcke (alle Zeiträume)
    for period in content.holidays["periods"]:
        pid = period["id"]
        if pid in seen_ids:
            problems.append(f"holidays: doppelte Periode {pid}")
        seen_ids.add(pid)
        if not _is_bilingual(period.get("label")):
            problems.append(f"holidays.{pid}: Label nicht zweisprachig")
        if set(period["ranges"]) != set(states):
            problems.append(f"holidays.{pid}: Länder fehlen oder sind unbekannt")
        if not any(period["ranges"].values()):
            problems.append(f"holidays.{pid}: kein Land hat in diesem Zeitraum Ferien")
        for state, ranges in period["ranges"].items():
            if not isinstance(ranges, list):
                problems.append(f"holidays.{pid}.{state}: Liste von Zeiträumen erwartet (leer = keine Ferien)")
                continue
            valid: list[Range] = []
            for pair in ranges:
                if not (isinstance(pair, list) and len(pair) == 2):
                    problems.append(f"holidays.{pid}.{state}: Zeitraum braucht genau Start und Ende")
                    continue
                start, end = pair
                try:
                    s, e = date.fromisoformat(start), date.fromisoformat(end)
                except (TypeError, ValueError):
                    problems.append(f"holidays.{pid}.{state}: ungültiges Datum")
                    continue
                if e < s or (e - s).days > 60:
                    problems.append(f"holidays.{pid}.{state}: unplausibler Zeitraum {start}–{end}")
                    continue
                for s2, e2, other in taken.setdefault(state, []):
                    if s <= e2 and s2 <= e:
                        problems.append(f"holidays.{pid}.{state}: {start}–{end} überschneidet sich mit {other}")
                taken[state].append((s, e, pid))
                valid.append(Range(s, e))
            for stretch in free_stretches(valid):
                blocks.setdefault(state, []).append((stretch, pid))
                if stretch.days < MIN_FREE_DAYS:
                    problems.append(
                        f"holidays.{pid}.{state}: nur {stretch.days} freie Tage am Stück "
                        f"({stretch.start}–{stretch.end}); kurze Ferien unter {MIN_FREE_DAYS} Tagen führt das Radar nicht"
                    )
    # Freie Blöcke eines Landes aus zwei Zeiträumen dürfen sich nicht berühren: Reisewellen und
    # Ferien-Wellen zählten sonst einen Ferienbeginn mitten in den Ferien.
    for state, items in blocks.items():
        items.sort(key=lambda item: item[0].start)
        for (a, pa), (b, pb) in zip(items, items[1:]):
            if pa != pb and b.start <= a.end + timedelta(days=1):
                problems.append(f"holidays.{pb}.{state}: freier Block {b.start}–{b.end} schließt an {pa} an "
                                f"({a.start}–{a.end}); als ein Zeitraum erfassen")
    _validate_bayrams(content.holidays.get("bayrams"), problems)

    # Zoll
    sources = content.customs["sources"]
    for key, src in sources.items():
        _check_url(src.get("url"), f"customs.sources.{key}", problems)
    ids = set()
    for item in content.customs["items"]:
        iid = item.get("id")
        if iid in ids:
            problems.append(f"customs: doppelte id {iid}")
        ids.add(iid)
        if item.get("direction") not in content.customs["directions"]:
            problems.append(f"customs.{iid}: unbekannte Richtung")
        if item.get("status") not in CUSTOMS_STATUSES:
            problems.append(f"customs.{iid}: unbekannter Status")
        for fieldname in ("title", "rule"):
            if not _is_bilingual(item.get(fieldname)):
                problems.append(f"customs.{iid}.{fieldname}: nicht zweisprachig")
        if "detail" in item and not _is_bilingual(item["detail"]):
            problems.append(f"customs.{iid}.detail: nicht zweisprachig")
        if not item.get("sources"):
            problems.append(f"customs.{iid}: keine Quelle")
        for ref in item.get("sources", []):
            if ref not in sources:
                problems.append(f"customs.{iid}: unbekannte Quelle {ref}")
        if "review_after" in item:
            _check_date(item["review_after"], f"customs.{iid}.review_after", problems)
    # Verwaiste Quellen deuten auf eine ersetzte, aber nicht entfernte Angabe hin
    used = {ref for item in content.customs["items"] for ref in item.get("sources", [])}
    for key in sorted(set(sources) - used):
        problems.append(f"customs.sources.{key}: wird von keiner Regel verwendet")

    # Transit
    countries = content.transit["countries"]
    for code, country in countries.items():
        if country.get("system") not in TOLL_SYSTEMS:
            problems.append(f"transit.{code}: unbekanntes Mautsystem")
        if not _is_bilingual(country.get("name")):
            problems.append(f"transit.{code}.name: nicht zweisprachig")
        _check_url(country["shop"].get("url"), f"transit.{code}.shop", problems)
        _check_url(country["source"].get("url"), f"transit.{code}.source", problems)
        if country.get("prices") and "prices_valid_until" not in country:
            problems.append(f"transit.{code}: Preise ohne Gültigkeitsdatum")
        for price in country.get("prices", []):
            if not _is_bilingual(price.get("label")):
                problems.append(f"transit.{code}: Preis-Label nicht zweisprachig")
        for note in country.get("notes", []):
            if not _is_bilingual(note):
                problems.append(f"transit.{code}: Hinweis nicht zweisprachig")
            if "source" in note:  # optional: eigene Quelle, wenn die Länderquelle den Hinweis nicht abdeckt
                _check_source(note["source"], f"transit.{code}.notes.source", problems)
    for route in content.transit["routes"]:
        for code in route["countries"]:
            if code not in countries:
                problems.append(f"transit.route.{route['id']}: unbekanntes Land {code}")
        for cid in route["controls"]:
            if cid not in content.crossing_by_id:
                problems.append(f"transit.route.{route['id']}: unbekannter Übergang {cid}")
        for listname in ("pros", "cons"):
            for entry in route[listname]:
                if not _is_bilingual(entry):
                    problems.append(f"transit.route.{route['id']}.{listname}: nicht zweisprachig")
    for doc in content.transit["documents"]:
        if not _is_bilingual(doc["text"]):
            problems.append(f"transit.documents.{doc['id']}: nicht zweisprachig")
        if "link" in doc:
            if not isinstance(doc["link"], str):
                problems.append(f"transit.documents.{doc['id']}: Link muss Text sein, ist {doc['link']!r}")
            else:
                page, _, anchor = doc["link"].partition("#")
                if page != "customs" or anchor not in ids:  # ids = Zoll-Regeln von oben
                    problems.append(f"transit.documents.{doc['id']}: Link {doc['link']!r} zeigt auf keine Zoll-Regel")
        if "source" in doc:
            _check_source(doc["source"], f"transit.documents.{doc['id']}.source", problems)

    # Grenzübergänge
    csources = content.crossings["sources"]
    for key, src in csources.items():
        _check_url(src.get("url"), f"crossings.sources.{key}", problems)
    for crossing in content.crossings["crossings"]:
        cid = crossing["id"]
        if len(crossing.get("countries", [])) != 2:
            problems.append(f"crossings.{cid}: braucht genau 2 Länder")
        if not _is_bilingual(crossing.get("note")):
            problems.append(f"crossings.{cid}.note: nicht zweisprachig")
        for ref in crossing.get("official", []):
            if ref not in csources:
                problems.append(f"crossings.{cid}: unbekannte Quelle {ref}")
        if not crossing.get("tz"):
            problems.append(f"crossings.{cid}: Zeitzone fehlt")

    problems += validate_redirects(content.redirects)
    return problems
