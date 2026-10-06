"""Ferien-Radar: Zahlen müssen aus den Daten folgen, nicht erfunden sein."""
import json
from datetime import date

import pytest

from tatilvakti.content import DATA_DIR
from tatilvakti.holidays import HolidayRadar, Range, easter_sunday, free_stretches, national_holidays


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
    info = radar.next_for_state("NW", date(2026, 10, 6))
    assert info["period"].id == "herbst-2026" and info["running"] is False
    assert [(r.start, r.end) for r in info["ranges"]] == [(date(2026, 10, 17), date(2026, 10, 31))]
    assert radar.next_for_state("HE", date(2026, 10, 20))["period"].id == "weihnachten-2026"


def test_easter_and_national_holidays():
    assert easter_sunday(2026) == date(2026, 4, 5)
    assert easter_sunday(2027) == date(2027, 3, 28)
    assert easter_sunday(2028) == date(2028, 4, 16)
    holidays_2027 = national_holidays(2027)
    assert date(2027, 3, 26) in holidays_2027 and date(2027, 3, 29) in holidays_2027  # Karfreitag, Ostermontag
    assert date(2027, 10, 3) in holidays_2027


def test_free_stretches_include_weekends_and_public_holidays():
    # NRW Sommer 2027: Mo 19.07.–Di 31.08. → frei ab Sa 17.07.
    assert free_stretches([Range(date(2027, 7, 19), date(2027, 8, 31))]) == [Range(date(2027, 7, 17), date(2027, 8, 31))]
    # Hessen: bis Fr 06.08. → frei bis So 08.08.
    assert free_stretches([Range(date(2027, 6, 28), date(2027, 8, 6))])[0].end == date(2027, 8, 8)
    # Saarland Weihnachten bis Do 31.12.2026 → Neujahr + Wochenende → frei bis So 03.01.2027
    assert free_stretches([Range(date(2026, 12, 21), date(2026, 12, 31))])[0].end == date(2027, 1, 3)


def test_gap_is_bridged_only_when_every_gap_day_is_off():
    # BW Ostern 2027: 25.03. + 30.03.–03.04.; dazwischen Karfreitag, Wochenende, Ostermontag
    bw = free_stretches([Range(date(2027, 3, 25), date(2027, 3, 25)), Range(date(2027, 3, 30), date(2027, 4, 3))])
    assert bw == [Range(date(2027, 3, 25), date(2027, 4, 4))]
    # Erfundener Fall mit echten Schultagen in der Lücke: bleibt getrennt
    split = free_stretches([Range(date(2027, 6, 1), date(2027, 6, 2)), Range(date(2027, 6, 9), date(2027, 6, 10))])
    assert len(split) == 2


def test_official_holiday_days_ignore_gaps(radar):
    assert radar.by_id["ostern-2027"].holiday_days("BW") == 6
    assert radar.by_id["sommer-2027"].holiday_days("NW") == 44


def test_next_start_skips_past_period_starts(radar):
    # 15.03.2027: Hamburgs Frühjahrsferien vorbei, die anderen Osterferien beginnen erst am 22.03.
    assert radar.running_period(date(2027, 3, 15)) is None
    period, start = radar.next_start(date(2027, 3, 15))
    assert (period.id, start) == ("ostern-2027", date(2027, 3, 22))


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
