"""Ferien-Radar: Zahlen müssen aus den Daten folgen, nicht erfunden sein."""
import json
from datetime import date

import pytest

from tatilvakti.content import DATA_DIR, MIN_FREE_DAYS
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


# ------------------------------------------- Zeiträume, in denen nicht alle Länder Ferien haben

def test_pfingsten_2027_covers_bw_and_by_and_leaves_others_empty(radar):
    pfingsten = radar.by_id["pfingsten-2027"]
    assert [(r.start, r.end) for r in pfingsten.ranges["BW"]] == [(date(2027, 5, 18), date(2027, 5, 29))]
    assert [(r.start, r.end) for r in pfingsten.ranges["BY"]] == [(date(2027, 5, 18), date(2027, 5, 28))]
    for state in ("HE", "RP", "SL"):  # laut Ferienordnung keine Pfingstferien
        assert pfingsten.ranges[state] == []
        assert pfingsten.span(state) is None and pfingsten.stretches(state) == []
        assert pfingsten.holiday_days(state) == 0 and not pfingsten.on_holiday(state, date(2027, 5, 20))
    # Mitte der Pfingstferien: BW, BY und Sachsen-Anhalt frei – vorher meldete das Radar hier 0 %
    assert radar.states_on_holiday(date(2027, 5, 20)) == ["BW", "BY", "ST"]
    expected = radar.weight["BW"] + radar.weight["BY"] + radar.weight["ST"]
    assert radar.pressure(date(2027, 5, 20)) == pytest.approx(expected)


def test_next_holiday_skips_periods_without_holidays_for_the_state(radar):
    assert radar.next_for_state("BW", date(2027, 4, 15))["period"].id == "pfingsten-2027"
    assert radar.next_for_state("HE", date(2027, 4, 15))["period"].id == "sommer-2027"
    assert radar.next_for_state("SN", date(2027, 1, 10))["period"].id == "winter-2027"
    assert radar.next_for_state("NW", date(2027, 1, 10))["period"].id == "ostern-2027"


def test_quiet_days_peak_and_windows_cope_with_empty_states(radar):
    pfingsten = radar.by_id["pfingsten-2027"]
    assert radar.quiet_days(pfingsten, "HE") == {"departure": [], "return": []}
    quiet = radar.quiet_days(pfingsten, "BW")
    assert quiet["departure"] and quiet["return"]
    assert radar.all_states_windows(pfingsten) == []
    day, share, count = radar.peak(pfingsten)
    assert pfingsten.start <= day <= pfingsten.end and 0 < share < 1 and count < 16


def test_waves_ignore_states_without_holidays(radar):
    waves = radar.waves(date(2027, 4, 15), horizon_days=40)  # bis 25.05.: nur Himmelfahrt/Pfingsten
    assert waves and not any(s in w["states"] for w in waves for s in ("HE", "RP", "SL"))
    bw_start = [w for w in waves if w["kind"] == "start" and "BW" in w["states"]]
    assert bw_start and bw_start[0]["date"] == date(2027, 5, 18)


def test_radar_works_with_minimal_data_and_empty_lists():
    data = {
        "states": {"AA": {"population": 3}, "BB": {"population": 1}},
        "periods": [{"id": "p", "kind": "x", "label": {"de": "P", "tr": "P"},
                     "ranges": {"AA": [["2027-05-18", "2027-05-21"]], "BB": []}}],
    }
    radar = HolidayRadar(data)
    period = radar.by_id["p"]
    assert (period.start, period.end) == (date(2027, 5, 18), date(2027, 5, 21))
    assert radar.pressure(date(2027, 5, 19)) == pytest.approx(0.75)
    assert radar.next_for_state("BB", date(2027, 5, 1)) is None
    assert radar.quiet_days(period, "BB") == {"departure": [], "return": []}
    assert radar.all_states_windows(period) == []
    assert [w["states"] for w in radar.waves(date(2027, 5, 1), 60)] == [["AA"], ["AA"]]


@pytest.mark.parametrize("path, text", [
    ("/de/ferien?zeitraum=winter-2027&land=NW", "Nordrhein-Westfalen hat in diesem Zeitraum keine Ferien."),
    ("/de/ferien?zeitraum=pfingsten-2027&land=HE", "Hessen hat in diesem Zeitraum keine Ferien."),
    ("/tr/tatil?zeitraum=pfingsten-2027&land=HE", "Hessen bu dönemde tatilde değil."),
    ("/de/ferien?zeitraum=pfingsten-2027&land=BW", "Deine Ferien in Baden-Württemberg"),
    # NRW hat nur den 18.05. frei (mit Pfingstwochenende 4 Tage) – zu kurz fürs Radar
    ("/de/ferien?zeitraum=pfingsten-2027&land=NW", "Nordrhein-Westfalen hat in diesem Zeitraum keine Ferien."),
    ("/de/ferien?zeitraum=winter-2027&land=BY", "Deine Ferien in Bayern"),
])
def test_holiday_page_renders_states_with_and_without_holidays(client, path, text):
    resp = client.get(path)
    assert resp.status_code == 200
    assert text in resp.get_data(as_text=True)


def test_home_names_pfingsten_as_next_holiday_in_bw(client, clock):
    clock.now = clock.now.replace(year=2027, month=4, day=15)
    html = client.get("/de/").get_data(as_text=True)
    bw = html.split('data-per-state="BW" hidden>', 1)[1].split("</div>", 1)[0]
    assert "Himmelfahrt-/Pfingstferien 2027" in bw


# ------------------------------------------------ Kurze Ferien führen nicht in die Irre

def test_every_listed_break_is_long_enough_for_quiet_days(radar):
    """Ab MIN_FREE_DAYS unterscheiden sich die 7-Tage-Fenster für Abreise und Rückreise."""
    for period in radar.periods:
        for state in radar.states:
            assert all(s.days >= MIN_FREE_DAYS for s in period.stretches(state)), (period.id, state)
            quiet = radar.quiet_days(period, state)
            if quiet["departure"]:
                assert quiet["departure"] != quiet["return"], (period.id, state)


def test_waves_never_start_and_end_on_the_same_day(radar):
    waves = radar.waves(date(2026, 10, 6), horizon_days=700)
    starts = {(s, w["date"]) for w in waves if w["kind"] == "start" for s in w["states"]}
    ends = {(s, w["date"]) for w in waves if w["kind"] == "end" for s in w["states"]}
    assert starts and not starts & ends


def test_no_bridge_day_wave_around_ascension_2027(radar):
    """Vorher meldete die Grenzseite am Fr 07.05.2027 „Ferienbeginn“ für 8 Länder."""
    waves = radar.waves(date(2027, 4, 20), horizon_days=50)
    assert date(2027, 5, 7) not in {w["date"] for w in waves}
    nrw_events = [w for w in waves if "NW" in w["states"]]
    assert nrw_events == []  # NRW-Pfingstferientag 18.05. ist nicht im Radar
    assert radar.next_for_state("BE", date(2027, 4, 20))["period"].id == "sommer-2027"
