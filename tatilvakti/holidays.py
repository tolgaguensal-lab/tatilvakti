"""Ferien-Radar: Wer hat wann Ferien, wie groß ist der Ferien-Druck?

Ferien-Druck = Anteil der Bevölkerung Deutschlands, deren Bundesland an einem Tag
Schulferien hat (gewichtet mit Destatis-Einwohnerzahlen). Das ist ein ehrlicher,
nachprüfbarer Nachfrage-Indikator – keine Preisprognose.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from functools import cached_property


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


class Period:
    def __init__(self, raw: dict) -> None:
        self.id: str = raw["id"]
        self.kind: str = raw["kind"]
        self.label: dict = raw["label"]
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
        total = sum(s["population"] for s in self.states.values())
        self.weight = {code: s["population"] / total for code, s in self.states.items()}

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

    def quiet_days(self, period: Period, state: str, count: int = 3) -> dict[str, list[tuple[date, float]]]:
        """Ruhigste Abreise- und Rückreisetage innerhalb der Ferien eines Landes.

        Angrenzende Wochenenden und bundesweite Feiertage zählen mit (Ferien ab Montag →
        Abreise schon am Samstag). Abreise: die ersten 7 freien Tage, Rückreise: die letzten 7.
        Sortiert nach Ferien-Druck, bei Gleichstand der frühere (Abreise) bzw.
        spätere Tag (Rückreise) – das lässt mehr Urlaub übrig.
        """
        stretches = period.stretches(state)
        if not stretches:
            return {"departure": [], "return": []}
        first, last = stretches[0], stretches[-1]
        dep_window = self.series(first.start, min(first.start + timedelta(days=6), first.end))
        ret_window = self.series(max(last.end - timedelta(days=6), last.start), last.end)
        departure = sorted(dep_window, key=lambda r: (round(r[1], 4), r[0]))[:count]
        ret = sorted(ret_window, key=lambda r: (round(r[1], 4), -r[0].toordinal()))[:count]
        return {
            "departure": sorted(((d, s) for d, s, _ in departure), key=lambda x: x[0]),
            "return": sorted(((d, s) for d, s, _ in ret), key=lambda x: x[0]),
        }

    def waves(self, today: date, horizon_days: int = 150, min_share: float = 0.04) -> list[dict]:
        """Ferienbeginn/-ende größerer Länder in den nächsten Monaten (für die Grenzseite)."""
        events: dict[tuple[str, date], list[str]] = {}
        limit = today + timedelta(days=horizon_days)
        for period in self.periods:
            for state in self.states:
                span = period.span(state)
                if span is None:
                    continue
                if today <= span.start <= limit:
                    events.setdefault(("start", span.start), []).append(state)
                if today <= span.end <= limit:
                    events.setdefault(("end", span.end), []).append(state)
        out = []
        for (kind, day), states in events.items():
            share = sum(self.weight[s] for s in states)
            if share >= min_share:
                out.append({"kind": kind, "date": day, "states": sorted(states, key=lambda s: -self.weight[s]), "share": share})
        return sorted(out, key=lambda e: e["date"])
