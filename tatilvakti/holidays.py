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


def free_span(span: Range) -> Range:
    """Ferienzeitraum inklusive direkt angrenzender Wochenenden."""
    start, end = span.start, span.end
    while (start - timedelta(days=1)).weekday() >= 5:
        start -= timedelta(days=1)
    while (end + timedelta(days=1)).weekday() >= 5:
        end += timedelta(days=1)
    return Range(start, end)


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

    def upcoming_periods(self, today: date) -> list[Period]:
        return [p for p in self.periods if p.end >= today]

    def next_for_state(self, state: str, today: date) -> tuple[Period, Range] | None:
        for period in self.periods:
            for r in period.ranges.get(state, []):
                if r.end >= today:
                    return period, period.span(state)
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

        Angrenzende Wochenenden zählen mit (Ferien ab Montag → Abreise schon am Samstag).
        Abreise: die ersten 7 freien Tage. Rückreise: die letzten 7 freien Tage.
        Sortiert nach Ferien-Druck, bei Gleichstand der frühere (Abreise) bzw.
        spätere Tag (Rückreise) – das lässt mehr Urlaub übrig.
        """
        span = period.span(state)
        if span is None:
            return {"departure": [], "return": []}
        free = free_span(span)
        dep_window = self.series(free.start, min(free.start + timedelta(days=6), free.end))
        ret_window = self.series(max(free.end - timedelta(days=6), free.start), free.end)
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
