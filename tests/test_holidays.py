"""Ferien-Radar: Zahlen müssen aus den Daten folgen, nicht erfunden sein."""
import json
from datetime import date, timedelta

import pytest

from tatilvakti.content import DATA_DIR, MIN_FREE_DAYS
from tatilvakti.holidays import (CANDIDATE_DAYS, QUIET_MAX, HolidayRadar, Range, easter_sunday, free_stretches,
                                 national_holidays)
from tatilvakti.i18n import fmt_pct

NBSP = chr(0xA0)  # geschütztes Leerzeichen vor dem Prozentzeichen (DE)


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


# ------------------------------------------------ Reisewelle statt Ferien-Druck (Audit prod-2)

def test_departure_wave_counts_own_state_in_the_first_three_days(radar):
    """NRW Sommer 2027: frei ab Sa 17.07. – Abfahrtswelle Sa bis Mo, danach keine."""
    for day in (date(2027, 7, 17), date(2027, 7, 18), date(2027, 7, 19)):
        wave = radar.wave(day, "departure")
        assert wave.states == ("NW",) and wave.share == pytest.approx(radar.weight["NW"])
    assert radar.wave(date(2027, 7, 20), "departure").states == ()
    # Rückreise: Hessen, RP und Saarland frei bis So 08.08. → Welle Fr 06.08. bis So 08.08.
    assert radar.wave(date(2027, 8, 6), "return").states == ("HE", "RP", "SL")
    assert radar.wave(date(2027, 8, 5), "return").states == ()


def test_nrw_summer_2027_departure_avoids_own_start_weekend(radar):
    """Vorher empfahl die App Sa 17.07./So 18.07. – genau die Tage, an denen NRW losfährt."""
    pick = radar.quiet_days(radar.by_id["sommer-2027"], "NW")["departure"]
    days = [w.day for w in pick.days]
    assert pick.calm and date(2027, 7, 17) not in days and date(2027, 7, 18) not in days
    assert days == [date(2027, 7, 20), date(2027, 7, 21), date(2027, 7, 22)]
    assert all(w.share < radar.wave(date(2027, 7, 17), "departure").share for w in pick.days)


@pytest.mark.parametrize("state, own_last_days", [
    ("HE", [date(2027, 8, 6), date(2027, 8, 7), date(2027, 8, 8)]),
    ("BY", [date(2027, 9, 11), date(2027, 9, 12), date(2027, 9, 13)]),
])
def test_return_days_are_not_the_days_everybody_drives_home(radar, state, own_last_days):
    """Vorher: Hessen Fr 06.08. und Bayern Mo 02.08. mit „100 % in Ferien“ als ruhigste Tage."""
    pick = radar.quiet_days(radar.by_id["sommer-2027"], state)["return"]
    assert pick.calm and len(pick.days) == 3
    assert not {w.day for w in pick.days} & set(own_last_days)
    assert all(w.share == 0 for w in pick.days)
    assert date(2027, 8, 2) not in {w.day for w in pick.days}


def test_quiet_days_are_the_best_candidates_everywhere(radar):
    """Für jedes Land und jeden Zeitraum: keine empfohlene Welle größer als ein nicht gewählter Kandidat."""
    for period in radar.periods:
        for state in radar.states:
            stretches = period.stretches(state)
            if not stretches:
                continue
            first, last = stretches[0], stretches[-1]
            n_dep = min(CANDIDATE_DAYS, first.days // 2) if first == last else min(CANDIDATE_DAYS, first.days)
            n_ret = min(CANDIDATE_DAYS, last.days // 2) if first == last else min(CANDIDATE_DAYS, last.days)
            quiet = radar.quiet_days(period, state)
            for kind, window in (("departure", [first.start + timedelta(days=i) for i in range(n_dep)]),
                                 ("return", [last.end - timedelta(days=i) for i in range(n_ret)])):
                pick = quiet[kind]
                chosen = {w.day for w in pick.days}
                assert chosen <= set(window), (period.id, state, kind)
                rest = [radar.wave(d, kind).share for d in window if d not in chosen]
                assert all(w.share <= r + 1e-9 for w in pick.days for r in rest), (period.id, state, kind)
                assert [w.share for w in pick.days] == sorted(w.share for w in pick.days)  # bester zuerst
                assert pick.calm == all(w.share <= QUIET_MAX for w in pick.days)


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
    empty = radar.quiet_days(pfingsten, "HE")
    assert empty["departure"].days == () and empty["return"].days == ()
    quiet = radar.quiet_days(pfingsten, "BW")
    assert quiet["departure"].days and quiet["return"].days
    assert radar.all_states_windows(pfingsten) == []
    day, share, count = radar.peak(pfingsten)
    assert pfingsten.start <= day <= pfingsten.end and 0 < share < 1 and count < 16


def test_waves_ignore_states_without_holidays(radar):
    waves = radar.waves(date(2027, 4, 15), horizon_days=40)  # bis 25.05.: nur Himmelfahrt/Pfingsten
    assert waves and not any(s in w["states"] for w in waves for s in ("HE", "RP", "SL"))
    bw_start = [w for w in waves if w["kind"] == "start" and "BW" in w["states"]]
    # Pfingstferien ab Di 18.05., frei aber schon ab Sa 15.05. (Pfingstwochenende + Pfingstmontag)
    assert bw_start and bw_start[0]["date"] == date(2027, 5, 15)
    assert bw_start[0]["states"] == ["BY", "BW", "ST"]


def test_waves_and_quiet_days_use_the_same_blocks(radar):
    """Grenzseite und Ferien-Radar dürfen sich nicht widersprechen (Audit prod-2)."""
    waves = radar.waves(date(2026, 10, 6), horizon_days=700)
    for event in waves:
        kind = "departure" if event["kind"] == "start" else "return"
        assert set(event["states"]) <= set(radar.wave(event["date"], kind).states), event
    nrw = [w for w in waves if w["kind"] == "start" and "NW" in w["states"] and w["date"].year == 2027
           and w["date"].month == 7]
    assert [w["date"] for w in nrw] == [date(2027, 7, 17)]  # nicht Mo 19.07. (Span), sondern der erste freie Tag
    assert radar.by_id["sommer-2027"].stretches("NW")[0].start == date(2027, 7, 17)


def test_radar_works_with_minimal_data_and_empty_lists():
    data = {
        "states": {"AA": {"population": 3}, "BB": {"population": 1}},
        "periods": [{"id": "p", "kind": "x", "label": {"de": "P", "tr": "P"}, "slug": {"de": "p", "tr": "p"},
                     "ranges": {"AA": [["2027-05-18", "2027-05-21"]], "BB": []}}],
    }
    radar = HolidayRadar(data)
    period = radar.by_id["p"]
    assert (period.start, period.end) == (date(2027, 5, 18), date(2027, 5, 21))
    assert radar.pressure(date(2027, 5, 19)) == pytest.approx(0.75)
    assert radar.next_for_state("BB", date(2027, 5, 1)) is None
    assert radar.quiet_days(period, "BB")["departure"].days == ()
    assert radar.all_states_windows(period) == []
    assert [w["states"] for w in radar.waves(date(2027, 5, 1), 60)] == [["AA"], ["AA"]]


@pytest.mark.parametrize("path, text", [
    ("/de/ferien/winter-2027?land=NW", "Nordrhein-Westfalen hat in diesem Zeitraum keine längeren Ferien."),
    ("/de/ferien/pfingsten-2027?land=HE", "Hessen hat in diesem Zeitraum keine längeren Ferien."),
    ("/tr/tatil/mayis-2027?land=HE", "Hessen bu dönemde uzun bir tatilde değil."),
    ("/de/ferien/pfingsten-2027?land=BW", "Deine Ferien in Baden-Württemberg"),
    # NRW hat nur den 18.05. frei (mit Pfingstwochenende 4 Tage) – zu kurz fürs Radar, und das steht da
    ("/de/ferien/pfingsten-2027?land=NW", "Kurze Ferien von höchstens einer Woche (mit Wochenende) führt das Radar nicht."),
    ("/tr/tatil/mayis-2027?land=NW", "en fazla bir hafta süren kısa tatiller radarda yer almıyor"),
    ("/de/ferien/winter-2027?land=BY", "Deine Ferien in Bayern"),
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

def test_every_listed_break_is_long_enough_and_departure_comes_before_return(radar):
    for period in radar.periods:
        for state in radar.states:
            assert all(s.days >= MIN_FREE_DAYS for s in period.stretches(state)), (period.id, state)
            quiet = radar.quiet_days(period, state)
            if quiet["departure"].days:
                latest_departure = max(w.day for w in quiet["departure"].days)
                assert latest_departure < min(w.day for w in quiet["return"].days), (period.id, state)


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


# ------------------------------------------------ Kein ruhiger Tag: ehrlich sagen

def _item(pct: str, why: str) -> str:
    """Wellenwert und Einordnung eines empfohlenen Tags, wie holidays.html sie ausgibt."""
    return f'<span class="quiet__pct">{pct}</span><span class="quiet__why">{why}</span>'


def _staggered_radar():
    """Drei gleich große Länder starten nacheinander – jeder Abreisetag liegt in einer Welle.

    Echte Länderkürzel, damit die Seite sie beschriften kann; Daten und Einwohner sind erfunden."""
    return HolidayRadar({
        "states": {"NW": {"population": 1}, "BW": {"population": 1}, "BY": {"population": 1}},
        "periods": [{"id": "p", "kind": "x", "label": {"de": "P", "tr": "P"}, "slug": {"de": "p", "tr": "p"}, "ranges": {
            "NW": [["2027-06-05", "2027-06-20"]],   # Sa–So: frei 05.06.–20.06., Kandidaten je 8 Tage
            "BW": [["2027-06-08", "2027-07-02"]],   # Di: Welle 08.–10.06.
            "BY": [["2027-06-11", "2027-07-02"]],   # Fr: Welle 11.–13.06.
        }}],
    })


def test_no_quiet_day_shows_only_the_least_bad_day():
    radar = _staggered_radar()
    quiet = radar.quiet_days(radar.by_id["p"], "NW")
    dep = quiet["departure"]
    assert dep.calm is False and len(dep.days) == 1
    # alle Kandidaten 05.–12.06. bei 1/3; bei Gleichstand der Tag am Blockrand
    assert dep.days[0].day == date(2027, 6, 5) and dep.days[0].share == pytest.approx(1 / 3)
    assert dep.days[0].states == ("NW",)
    # Rückreise: nur NRW endet im Fenster (18.–20.06.) – davor ist es ruhig
    ret = quiet["return"]
    assert ret.calm is True
    assert [w.day for w in ret.days] == [date(2027, 6, 17), date(2027, 6, 16), date(2027, 6, 15)]


def test_quiet_threshold_is_inclusive_and_documented():
    assert QUIET_MAX == 0.25
    radar = HolidayRadar({
        "states": {"AA": {"population": 1}, "BB": {"population": 3}},
        "periods": [{"id": "p", "kind": "x", "label": {"de": "P", "tr": "P"}, "slug": {"de": "p", "tr": "p"},
                     "ranges": {"AA": [["2027-06-05", "2027-06-13"]], "BB": []}}],
    })
    dep = radar.quiet_days(radar.by_id["p"], "AA")["departure"]  # 9 Tage → 4 Kandidaten
    assert dep.calm and [w.day for w in dep.days][0] == date(2027, 6, 8)  # 0 % schlägt die eigene Welle
    assert [round(w.share, 2) for w in dep.days] == [0, 0.25, 0.25]  # genau 25 % zählt noch als ruhig


def test_no_quiet_day_is_rendered_in_both_languages(app, client, monkeypatch):
    radar = app.extensions["tv"].radar
    synthetic = _staggered_radar()
    monkeypatch.setattr(radar, "quiet_days",
                        lambda period, state, count=3: synthetic.quiet_days(synthetic.by_id["p"], "NW", count))
    de = client.get("/de/ferien/sommer-2027?land=NW").get_data(as_text=True)
    nw = de.split('data-per-state="NW">', 1)[1].split("</article>", 1)[0]
    assert f"Kein ruhiger Tag in diesem Zeitraum: Die Reisewelle liegt an jedem Tag über 25{NBSP}%." in nw
    assert "quiet__list--none" in nw and "Sa 05.06." in nw and _item(f"33,3{NBSP}%", "Ferienstart: NRW") in nw
    tr = client.get("/tr/tatil/yaz-2027?land=NW").get_data(as_text=True)
    nw = tr.split('data-per-state="NW">', 1)[1].split("</article>", 1)[0]
    assert "Bu dönemde sakin gün yok: Tatil dalgası her gün %25 üzerinde." in nw
    assert _item("%33,3", "Tatil başlıyor: NRW") in nw


# ------------------------------------------------ Darstellung im Ferien-Radar

def test_holiday_page_shows_wave_days_with_context(client):
    html = client.get("/de/ferien/sommer-2027?land=NW").get_data(as_text=True)
    nw = html.split('data-per-state="NW">', 1)[1].split("</article>", 1)[0]
    assert "Abreisetage mit der kleinsten Reisewelle" in nw and "Ruhigste" not in html
    dep = nw.split("<ol", 1)[1].split("</ol>", 1)[0]
    assert dep.index("Di 20.07.") < dep.index("Mi 21.07.") < dep.index("Do 22.07.")
    assert _item(f"0{NBSP}%", "kein Ferienstart") in dep and "Sa 17.07." not in dep
    assert "keine Stau-Messung" in nw
    tr = client.get("/tr/tatil/yaz-2027?land=NW").get_data(as_text=True)
    nw = tr.split('data-per-state="NW">', 1)[1].split("</article>", 1)[0]
    assert "Tatil dalgasının en küçük olduğu gidiş günleri" in nw
    assert _item("%0", "tatili başlayan eyalet yok") in nw and "trafik ölçümü değildir" in nw


def test_wave_context_names_states_or_counts_them(client):
    html = client.get("/de/ferien/weihnachten-2026?land=BW").get_data(as_text=True)
    bw = html.split('data-per-state="BW">', 1)[1].split("</article>", 1)[0]
    assert _item(f"22,7{NBSP}%", "Ferienende in 8 Ländern") in bw  # Sa 02.01.: acht Länder fahren heim
    html = client.get("/de/ferien/herbst-2026?land=BW").get_data(as_text=True)
    bw = html.split('data-per-state="BW">', 1)[1].split("</article>", 1)[0]
    assert _item(f"13,5{NBSP}%", "Ferienstart: BW") in bw


def test_percentages_follow_the_language(client):
    de = client.get("/de/ferien/sommer-2027").get_data(as_text=True)
    tr = client.get("/tr/tatil/yaz-2027").get_data(as_text=True)
    assert f"<dd>100{NBSP}% · Mo 02.08.2027" in de and "<dd>%100 · Pzt 02.08.2027" in tr
    assert f"<span>100{NBSP}%</span>" in de and "<span>%0</span>" in tr  # Achse
    assert "100 %" not in tr and " %<" not in tr
    tr_borders = client.get("/tr/sinir").get_data(as_text=True)
    share = tr_borders.split('class="waves__share">', 1)[1].split("<", 1)[0]
    assert share.startswith("%") and not share.endswith("%")


@pytest.mark.parametrize("share, de, tr", [
    (0.126, f"12,6{NBSP}%", "%12,6"), (1.0, f"100{NBSP}%", "%100"), (0.0001, f"0{NBSP}%", "%0"), (0.78, f"78,0{NBSP}%", "%78,0"),
])
def test_fmt_pct_uses_language_convention(share, de, tr):
    assert fmt_pct(share, "de") == de and fmt_pct(share, "tr") == tr


def test_fmt_pct_without_decimals():
    assert fmt_pct(0.25, "de", 0) == f"25{NBSP}%" and fmt_pct(0.25, "tr", 0) == "%25"
    assert fmt_pct(0.004, "tr", 0) == "%0" and fmt_pct(0.996, "de", 0) == f"100{NBSP}%"
    assert fmt_pct(0.9996, "tr") == "%100"


def test_table_marks_states_without_holidays(client):
    de = client.get("/de/ferien/pfingsten-2027").get_data(as_text=True)
    row = de.split('<tr data-tl-state="HE"', 1)[1].split("</tr>", 1)[0]
    assert '<span aria-hidden="true">–</span><span class="sr-only">keine Ferien</span>' in row
    tr = client.get("/tr/tatil/mayis-2027").get_data(as_text=True)
    row = tr.split('<tr data-tl-state="HE"', 1)[1].split("</tr>", 1)[0]
    assert '<span class="sr-only">tatil yok</span>' in row
    row = de.split('<tr data-tl-state="BW"', 1)[1].split("</tr>", 1)[0]
    assert "18.05.–29.05.2027" in row and "sr-only" not in row


# ------------------------------------------------ Bayram-Marker (Diyanet)

def test_bayrams_are_linked_to_the_periods_they_fall_into(radar):
    found = {period.id: [(b.id, states) for b, states in radar.bayrams_in(period)] for period in radar.periods}
    assert found["pfingsten-2027"] == [("kurban-2027", ("BY", "BW", "ST", "HH"))]
    assert found["ostern-2027"] == [("ramazan-2027", ("HH",))]  # Hamburgs Frühjahrsferien 01.–12.03.
    assert found["sommer-2027"] == [] and found["herbst-2026"] == []
    kurban = radar.bayrams[1]
    assert (kurban.arife, kurban.days.start, kurban.days.end) == (date(2027, 5, 15), date(2027, 5, 16), date(2027, 5, 19))


def test_holiday_page_marks_kurban_bayrami_in_pentecost_2027(client):
    html = client.get("/de/ferien/pfingsten-2027?land=BW").get_data(as_text=True)
    bw = html.split('data-per-state="BW">', 1)[1].split("</article>", 1)[0]
    assert ("Kurban Bayramı (Opferfest) fällt in deine freien Tage: Arife Sa 15.05., "
            "Bayram So 16.05.2027 – Mi 19.05.2027.") in bw
    he = html.split('data-per-state="HE" hidden>', 1)[1].split("</article>", 1)[0]
    assert "Bayram" not in he  # Hessen hat keine Pfingstferien
    assert 'class="tl__bayram"' in html and "sw--bayram" in html
    assert "Fällt in die freien Tage von: BY, BW, ST, HH" in html
    assert "https://namazvakitleri.diyanet.gov.tr/en-US/dini-gunler" in html
    tr = client.get("/tr/tatil/mayis-2027?land=BW").get_data(as_text=True)
    assert "Kurban Bayramı tatiline denk geliyor: arife Cmt 15.05., bayram Paz 16.05.2027 – Çar 19.05.2027." in tr
    assert "Tatiline denk geldiği eyaletler: BY, BW, ST, HH" in tr
    summer = client.get("/de/ferien/sommer-2027").get_data(as_text=True)
    assert "tl__bayram" not in summer and "Bayram" not in summer


def test_info_page_lists_bayram_source(client):
    html = client.get("/tr/bilgi").get_data(as_text=True)
    assert "https://namazvakitleri.diyanet.gov.tr/en-US/dini-gunler" in html
