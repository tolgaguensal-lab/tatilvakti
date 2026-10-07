"""Ferien-Radar: Wer hat wann Ferien, wie groß ist der Ferien-Druck, wann rollt eine Reisewelle?

Ferien-Druck = Anteil der Bevölkerung Deutschlands, deren Bundesland an einem Tag
Schulferien hat (gewichtet mit Destatis-Einwohnerzahlen). Das ist ein ehrlicher,
nachprüfbarer Nachfrage-Indikator – keine Preisprognose.

Reisewelle = Anteil der Bevölkerung, deren freier Block (Ferien plus angrenzende Wochenenden
und bundesweite Feiertage) gerade begonnen hat (Abreise) bzw. gleich endet (Rückreise). Wer in
die Türkei fährt, bricht meist in den ersten Tagen auf und kommt in den letzten zurück. Daraus
folgen die Tage mit der kleinsten Reisewelle und die Ferien-Wellen der Grenzseite – beide
aus denselben Blöcken, damit sich die Seiten nicht widersprechen. Eine Schätzung aus
Ferienterminen und Einwohnerzahlen, keine Stau-Messung.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from functools import cached_property

WAVE_DAYS = 3        # Abfahrt in den ersten 3 Tagen eines freien Blocks, Rückfahrt in den letzten 3
CANDIDATE_DAYS = 14  # Abreise-Kandidaten: die ersten 2 Wochen des eigenen Blocks, Rückreise: die letzten 2
QUIET_MAX = 0.25     # Reisewelle bis 25 % gilt als ruhig; liegen alle Kandidaten darüber: „kein ruhiger Tag“


@dataclass(frozen=True)
class Range:
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end


def easter_sunday(year: int) -> date:
    """Ostersonntag (gregorianisch, anonymer Algorithmus nach Meeus/Jones/Butcher)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def national_holidays(year: int) -> set[date]:
    """Bundesweite gesetzliche Feiertage (landesspezifische bewusst nicht)."""
    easter = easter_sunday(year)
    return {
        date(year, 1, 1), easter - timedelta(days=2), easter + timedelta(days=1), date(year, 5, 1),
        easter + timedelta(days=39), easter + timedelta(days=50), date(year, 10, 3),
        date(year, 12, 25), date(year, 12, 26),
    }


def is_day_off(day: date) -> bool:
    return day.weekday() >= 5 or day in national_holidays(day.year)


def free_stretches(ranges: list[Range]) -> list[Range]:
    """Zusammenhängende freie Zeiträume eines Landes.

    Ferienblöcke plus direkt angrenzende Wochenenden und bundesweite Feiertage. Eine Lücke
    zwischen zwei Blöcken wird nur überbrückt, wenn jeder Lückentag ohnehin frei ist
    (z. B. BW Ostern 2027: 25.03. + Karfreitag, Wochenende, Ostermontag + 30.03.–03.04.).
    """
    stretches: list[Range] = []
    for r in sorted(ranges, key=lambda x: x.start):
        start, end = r.start, r.end
        while is_day_off(start - timedelta(days=1)):
            start -= timedelta(days=1)
        while is_day_off(end + timedelta(days=1)):
            end += timedelta(days=1)
        if stretches and start <= stretches[-1].end + timedelta(days=1):
            stretches[-1] = Range(stretches[-1].start, max(end, stretches[-1].end))
        else:
            stretches.append(Range(start, end))
    return stretches


@dataclass(frozen=True)
class Block:
    """Freier Block eines Landes: Ferien plus angrenzende Wochenenden und bundesweite Feiertage."""
    state: str
    period: str
    free: Range


@dataclass(frozen=True)
class WaveDay:
    """Reisewelle an einem Tag: Bevölkerungsanteil und Länder (größtes zuerst)."""
    day: date
    share: float
    states: tuple[str, ...]


@dataclass(frozen=True)
class QuietPick:
    """Empfohlene Reisetage, bester zuerst. calm=False: kein Kandidat bis QUIET_MAX – days enthält
    dann nur den am wenigsten schlechten Tag, damit die Seite das ehrlich sagen kann."""
    days: tuple[WaveDay, ...]
    calm: bool


@dataclass(frozen=True)
class Bayram:
    """Ramazan- oder Kurban Bayramı laut Diyanet; der Arife-Tag (Vortag, halber Feiertag) separat."""
    id: str
    kind: str
    label: dict
    arife: date
    days: Range

    @property
    def span(self) -> Range:
        """Arife bis letzter Festtag – dieser Zeitraum wird mit den freien Blöcken verglichen."""
        return Range(self.arife, self.days.end)


class Period:
    def __init__(self, raw: dict) -> None:
        self.id: str = raw["id"]
        self.kind: str = raw["kind"]
        self.label: dict = raw["label"]
        self.slug: dict = raw["slug"]  # Pfad je Sprache: /de/ferien/sommer-2027, /tr/tatil/yaz-2027
        self.ranges: dict[str, list[Range]] = {
            state: [Range(date.fromisoformat(s), date.fromisoformat(e)) for s, e in pairs]
            for state, pairs in raw["ranges"].items()
        }

    @cached_property
    def start(self) -> date:
        return min(r.start for rs in self.ranges.values() for r in rs)

    @cached_property
    def end(self) -> date:
        return max(r.end for rs in self.ranges.values() for r in rs)

    def span(self, state: str) -> Range | None:
        """Gesamtspanne eines Landes (erster bis letzter Ferientag)."""
        rs = self.ranges.get(state) or []
        if not rs:
            return None
        return Range(min(r.start for r in rs), max(r.end for r in rs))

    def on_holiday(self, state: str, day: date) -> bool:
        return any(r.contains(day) for r in self.ranges.get(state, []))

    def holiday_days(self, state: str) -> int:
        """Offizielle Ferientage (Summe der Blöcke, ohne Lücken)."""
        return sum(r.days for r in self.ranges.get(state, []))

    def stretches(self, state: str) -> list[Range]:
        return free_stretches(self.ranges.get(state, []))


class HolidayRadar:
    def __init__(self, data: dict) -> None:
        self.states: dict = data["states"]
        self.periods: list[Period] = sorted((Period(p) for p in data["periods"]), key=lambda p: p.start)
        self.by_id = {p.id: p for p in self.periods}
        self.by_slug: dict[str, dict[str, Period]] = {}  # Sprache → Slug → Zeitraum
        for period in self.periods:
            for lang, slug in period.slug.items():
                self.by_slug.setdefault(lang, {})[slug] = period
        total = sum(s["population"] for s in self.states.values())
        self.weight = {code: s["population"] / total for code, s in self.states.items()}
        self.bayrams: list[Bayram] = sorted(
            (Bayram(b["id"], b["kind"], b["label"], date.fromisoformat(b["arife"]),
                    Range(date.fromisoformat(b["start"]), date.fromisoformat(b["end"])))
             for b in data.get("bayrams", {}).get("items", [])),
            key=lambda b: b.arife)

    @cached_property
    def blocks(self) -> list[Block]:
        """Alle freien Blöcke aller Länder und Zeiträume – Grundlage für Reisewellen und Ferien-Wellen."""
        return [Block(state, period.id, stretch)
                for period in self.periods for state in self.states for stretch in period.stretches(state)]

    def _by_weight(self, states) -> tuple[str, ...]:
        return tuple(sorted(set(states), key=lambda s: (-self.weight[s], s)))

    def find_period(self, key: str) -> Period | None:
        """Zeitraum zu einer id (alte Links mit ?zeitraum=) oder einem Slug irgendeiner Sprache."""
        if key in self.by_id:
            return self.by_id[key]
        return next((slugs[key] for slugs in self.by_slug.values() if key in slugs), None)

    # -------------------------------------------------------------- Abfragen

    def states_on_holiday(self, day: date) -> list[str]:
        return [s for s in self.states if any(p.on_holiday(s, day) for p in self.periods)]

    def pressure(self, day: date) -> float:
        return sum(self.weight[s] for s in self.states_on_holiday(day))

    def series(self, start: date, end: date) -> list[tuple[date, float, int]]:
        out = []
        day = start
        while day <= end:
            on = self.states_on_holiday(day)
            out.append((day, sum(self.weight[s] for s in on), len(on)))
            day += timedelta(days=1)
        return out

    def current_or_next_period(self, today: date) -> Period | None:
        for period in self.periods:
            if period.end >= today:
                return period
        return None

    def running_period(self, today: date) -> Period | None:
        """Zeitraum, in dem heute mindestens ein Land tatsächlich Ferien hat."""
        for period in self.periods:
            if any(period.on_holiday(state, today) for state in self.states):
                return period
        return None

    def next_start(self, today: date) -> tuple[Period, date] | None:
        """Nächster echter Ferienbeginn nach heute (nicht das Startdatum eines laufenden Zeitraums)."""
        best = None
        for period in self.periods:
            for ranges in period.ranges.values():
                for r in ranges:
                    if r.start > today and (best is None or r.start < best[1]):
                        best = (period, r.start)
        return best

    def next_for_state(self, state: str, today: date) -> dict | None:
        """Nächste bzw. laufende Ferien eines Landes: offizielle Blöcke + freier Zeitraum."""
        for period in self.periods:
            for stretch in period.stretches(state):
                if stretch.end >= today:
                    ranges = [r for r in period.ranges[state] if stretch.start <= r.start <= stretch.end]
                    return {"period": period, "ranges": ranges, "free": stretch,
                            "running": stretch.start <= today}
        return None

    def all_states_windows(self, period: Period) -> list[Range]:
        """Zeiträume, in denen alle 16 Länder gleichzeitig Ferien haben."""
        windows, run_start, prev = [], None, None
        for day, _share, count in self.series(period.start, period.end):
            if count == len(self.states):
                if run_start is None:
                    run_start = day
                prev = day
            elif run_start is not None:
                windows.append(Range(run_start, prev))
                run_start = None
        if run_start is not None:
            windows.append(Range(run_start, prev))
        return windows

    def peak(self, period: Period) -> tuple[date, float, int]:
        return max(self.series(period.start, period.end), key=lambda row: (row[1], -row[0].toordinal()))

    def wave(self, day: date, kind: str) -> WaveDay:
        """Reisewelle an einem Tag.

        kind="departure": Länder, deren freier Block an diesem Tag oder in den 2 Tagen davor
        begonnen hat. kind="return": Länder, deren freier Block an diesem Tag oder in den 2 Tagen
        danach endet. Das eigene Land zählt mit – es fährt ja auch.
        """
        reach = timedelta(days=WAVE_DAYS - 1)
        if kind == "departure":
            states = [b.state for b in self.blocks if day - reach <= b.free.start <= day]
        else:
            states = [b.state for b in self.blocks if day <= b.free.end <= day + reach]
        ordered = self._by_weight(states)
        return WaveDay(day, sum(self.weight[s] for s in ordered), ordered)

    def quiet_days(self, period: Period, state: str, count: int = 3) -> dict[str, QuietPick]:
        """Abreise- und Rückreisetage mit der kleinsten Reisewelle innerhalb der freien Zeit eines Landes.

        Kandidaten: die ersten bzw. letzten CANDIDATE_DAYS Tage des eigenen freien Blocks. Hat ein
        Land nur einen Block unter 2 × CANDIDATE_DAYS, bekommt jede Richtung die Hälfte – sonst
        könnte die empfohlene Abreise nach der Rückreise liegen. Rangfolge nach Reisewelle, bei
        Gleichstand der Tag näher am Blockrand (mehr Urlaub). Gezeigt werden nur Tage bis
        QUIET_MAX; gibt es keinen, nur der am wenigsten schlechte (calm=False).
        """
        stretches = period.stretches(state)
        if not stretches:
            return {"departure": QuietPick((), False), "return": QuietPick((), False)}
        first, last = stretches[0], stretches[-1]
        if first == last:
            n_dep = n_ret = max(1, min(CANDIDATE_DAYS, first.days // 2))
        else:
            n_dep, n_ret = min(CANDIDATE_DAYS, first.days), min(CANDIDATE_DAYS, last.days)
        dep = [self.wave(first.start + timedelta(days=i), "departure") for i in range(n_dep)]
        ret = [self.wave(last.end - timedelta(days=i), "return") for i in range(n_ret)]
        # Kandidaten liegen nach Abstand zum Blockrand sortiert vor; sorted() ist stabil
        return {"departure": self._pick(dep, count), "return": self._pick(ret, count)}

    @staticmethod
    def _pick(candidates: list[WaveDay], count: int) -> QuietPick:
        """Beste Tage zuerst (die Liste ist eine Rangfolge, keine Chronik)."""
        ranked = sorted(candidates, key=lambda w: round(w.share, 9))
        calm = [w for w in ranked if w.share <= QUIET_MAX + 1e-9]
        return QuietPick(tuple(calm[:count] if calm else ranked[:1]), bool(calm))

    def bayrams_in(self, period: Period) -> list[tuple[Bayram, tuple[str, ...]]]:
        """Bayram-Termine, die in freie Blöcke dieses Zeitraums fallen, mit den betroffenen Ländern."""
        out = []
        for bayram in self.bayrams:
            states = [b.state for b in self.blocks if b.period == period.id
                      and b.free.start <= bayram.span.end and bayram.span.start <= b.free.end]
            if states:
                out.append((bayram, self._by_weight(states)))
        return out

    def waves(self, today: date, horizon_days: int = 150, min_share: float = 0.04) -> list[dict]:
        """Beginn und Ende freier Blöcke größerer Länder in den nächsten Monaten (für die Grenzseite).

        Dieselben Blöcke wie bei den Reisetagen im Ferien-Radar: Datum ist der erste bzw. letzte
        freie Tag, also inklusive angrenzender Wochenenden und bundesweiter Feiertage.
        """
        events: dict[tuple[str, date], list[str]] = {}
        limit = today + timedelta(days=horizon_days)
        for block in self.blocks:
            if today <= block.free.start <= limit:
                events.setdefault(("start", block.free.start), []).append(block.state)
            if today <= block.free.end <= limit:
                events.setdefault(("end", block.free.end), []).append(block.state)
        out = []
        for (kind, day), states in events.items():
            ordered = self._by_weight(states)
            share = sum(self.weight[s] for s in ordered)
            if share >= min_share:
                out.append({"kind": kind, "date": day, "states": list(ordered), "share": share})
        return sorted(out, key=lambda e: (e["date"], e["kind"]))
