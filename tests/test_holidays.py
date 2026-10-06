"""Ferien-Radar: Zahlen müssen aus den Daten folgen, nicht erfunden sein."""
import json
from datetime import date

import pytest

from tatilvakti.content import DATA_DIR
from tatilvakti.holidays import HolidayRadar, Range, free_span


@pytest.fixture(scope="module")
def radar():
    with open(DATA_DIR / "holidays.json", encoding="utf-8") as fh:
        return HolidayRadar(json.load(fh))


def test_all_16_states_overlap_exactly_2_to_6_august_2027(radar):
    # Unabhängig belegte Aussage der KMK-Termine: nur KW 31 (02.–06.08.2027) alle 16 Länder frei
    windows = radar.all_states_windows(radar.by_id["sommer-2027"])
    assert [(w.start, w.end) for w in windows] == [(date(2027, 8, 2), date(2027, 8, 6))]


def test_pressure_is_population_weighted(radar):
    assert radar.pressure(date(2027, 8, 3)) == pytest.approx(1.0)
    assert radar.weight["NW"] == pytest.approx(0.216, abs=0.001)
    # 18.07.2027: alle außer NRW, BW, BY haben Ferien
    expected = 1 - radar.weight["NW"] - radar.weight["BW"] - radar.weight["BY"]
    assert radar.pressure(date(2027, 7, 18)) == pytest.approx(expected)


def test_no_holiday_means_zero_pressure(radar):
    assert radar.pressure(date(2027, 5, 5)) == 0
    assert radar.states_on_holiday(date(2027, 5, 5)) == []


def test_next_holiday_for_state(radar):
    period, span = radar.next_for_state("NW", date(2026, 10, 6))
    assert period.id == "herbst-2026"
    assert (span.start, span.end) == (date(2026, 10, 17), date(2026, 10, 31))
    period, _ = radar.next_for_state("HE", date(2026, 10, 20))
    assert period.id == "weihnachten-2026"


def test_free_span_includes_adjacent_weekends():
    # NRW Sommer 2027: Mo 19.07.–Di 31.08. → frei ab Sa 17.07.
    assert free_span(Range(date(2027, 7, 19), date(2027, 8, 31))) == Range(date(2027, 7, 17), date(2027, 8, 31))
    # Hessen: bis Fr 06.08. → frei bis So 08.08.
    assert free_span(Range(date(2027, 6, 28), date(2027, 8, 6))).end == date(2027, 8, 8)


def test_quiet_days_prefer_low_pressure(radar):
    quiet = radar.quiet_days(radar.by_id["sommer-2027"], "NW")
    departure_days = [d for d, _ in quiet["departure"]]
    assert date(2027, 7, 17) in departure_days  # Samstag vor Ferienbeginn
    shares = [s for _, s in quiet["departure"]]
    assert max(shares) <= radar.pressure(date(2027, 7, 21))


def test_split_easter_holidays_are_kept(radar):
    bw = radar.by_id["ostern-2027"].ranges["BW"]
    assert len(bw) == 2
    assert radar.by_id["ostern-2027"].on_holiday("BW", date(2027, 3, 25))
    assert not radar.by_id["ostern-2027"].on_holiday("BW", date(2027, 3, 26))


def test_waves_list_big_state_starts(radar):
    waves = radar.waves(date(2026, 10, 6), horizon_days=40)
    nrw_start = [w for w in waves if w["kind"] == "start" and "NW" in w["states"]]
    assert nrw_start and nrw_start[0]["date"] == date(2026, 10, 17)
    assert all(w["share"] >= 0.04 for w in waves)
