"""Kuratierte Daten: vollständig, zweisprachig, mit Quellen – sonst startet die App nicht."""
import re

import pytest

from tatilvakti.content import load_content, validate
from tatilvakti.i18n import LANGS, Translator, fold, negotiate


def test_datasets_validate_cleanly():
    assert validate(load_content()) == []


def test_validation_catches_missing_translation():
    content = load_content()
    content.customs["items"][0]["title"]["tr"] = ""
    problems = validate(content)
    assert any("nicht zweisprachig" in p for p in problems)


def test_validation_catches_missing_state_in_period():
    content = load_content()
    del content.holidays["periods"][0]["ranges"]["NW"]
    assert any("Länder fehlen" in p for p in validate(content))


def _period(content, pid):
    return next(p for p in content.holidays["periods"] if p["id"] == pid)


def test_empty_list_means_no_holidays_but_key_stays_mandatory():
    content = load_content()
    assert _period(content, "pfingsten-2027")["ranges"]["HE"] == []  # echtes Beispiel in den Daten
    _period(content, "herbst-2026")["ranges"]["NW"] = []
    assert validate(content) == []
    del _period(content, "pfingsten-2027")["ranges"]["HE"]
    assert any("pfingsten-2027: Länder fehlen" in p for p in validate(content))


def test_validation_rejects_period_without_any_holidays():
    content = load_content()
    period = _period(content, "winter-2027")
    period["ranges"] = {state: [] for state in period["ranges"]}
    assert any("winter-2027: kein Land hat" in p for p in validate(content))


@pytest.mark.parametrize("ranges, message", [
    ([["2027-05-18"]], "genau Start und Ende"),
    ("2027-05-18", "Liste von Zeiträumen"),
    ([["2027-05-18", "2027-02-30"]], "ungültiges Datum"),
    ([["2027-05-18", "2027-05-10"]], "unplausibler Zeitraum"),
    ([["2027-05-18", "2027-05-29"], ["2027-05-25", "2027-05-26"]], "überschneidet sich mit pfingsten-2027"),
    ([["2027-03-31", "2027-04-01"]], "überschneidet sich mit ostern-2027"),  # BW-Osterferien
])
def test_validation_catches_broken_holiday_ranges(ranges, message):
    content = load_content()
    _period(content, "pfingsten-2027")["ranges"]["BW"] = ranges
    assert any(message in p for p in validate(content)), validate(content)


def test_validation_catches_unused_source_and_dead_checklist_link():
    content = load_content()
    content.customs["sources"]["alt"] = {"name": "Alt", "url": "https://example.org"}
    content.transit["documents"][0]["link"] = "customs#gibt-es-nicht"
    content.transit["countries"]["RS"]["notes"][0]["source"] = {"name": "", "url": "https://example.org"}
    problems = validate(content)
    assert any("customs.sources.alt: wird von keiner Regel verwendet" in p for p in problems)
    assert any("zeigt auf keine Zoll-Regel" in p for p in problems)
    assert any("transit.RS.notes.source: Quelle braucht Name und URL" in p for p in problems)


@pytest.mark.parametrize("link, message", [
    (None, "Link muss Text sein"),
    (42, "Link muss Text sein"),
    ("customs#", "zeigt auf keine Zoll-Regel"),
    ("route#tr-vollmacht", "zeigt auf keine Zoll-Regel"),
])
def test_validation_reports_broken_checklist_links_instead_of_crashing(link, message):
    content = load_content()
    content.transit["documents"][0]["link"] = link
    assert any(message in p for p in validate(content))


@pytest.mark.parametrize("state, ranges, days", [
    ("NW", [["2027-05-18", "2027-05-18"]], 4),                              # Di nach Pfingsten
    ("BW", [["2027-05-07", "2027-05-07"], ["2027-05-18", "2027-05-29"]], 4),  # Brückentag + Pfingstferien
    ("HH", [["2027-05-07", "2027-05-11"]], 6),                              # Do Himmelfahrt bis Di
])
def test_validation_rejects_breaks_too_short_for_the_radar(state, ranges, days):
    content = load_content()
    _period(content, "pfingsten-2027")["ranges"][state] = ranges
    problems = validate(content)
    assert any(f"pfingsten-2027.{state}: nur {days} freie Tage am Stück" in p for p in problems), problems


@pytest.mark.parametrize("query, expected", [
    ("kopek", "eu-haustier"),      # „köpek“ ohne Sonderzeichen getippt
    ("Katze", "eu-haustier"),
    ("cocuk", "eu-waren"),         # Freigrenze 175 € für Kinder
    ("175", "eu-gold"),
    ("hediye", "tr-geschenke"),
    ("kanister", "eu-kraftstoff"),
    ("185000", "eu-bargeld"),
    ("mavi kart", "tr-auto-dauer"),
    ("akraba", "tr-auto-fahrer"),
    ("kasko", "tr-gruene-karte"),
])
def test_customs_search_finds_new_rules(query, expected):
    content = load_content()
    hits = [item["id"] for item in content.customs["items"] if fold(query) in item["search"]]
    assert expected in hits


def test_child_allowance_is_stated_wherever_the_adult_one_is():
    """Die Freigrenze für unter 15-Jährige (175 €, § 2 EF-VO) steht überall, wo 300/430 € genannt sind."""
    content = load_content()
    for item in content.customs["items"]:
        if item["direction"] == "to_eu" and "300 €" in item["rule"]["de"]:
            assert "175 €" in item["rule"]["de"] and "175 €" in item["rule"]["tr"], item["id"]


def test_cash_rules_name_the_eu_external_border_not_only_germany():
    """Art. 3 VO (EU) 2018/1672: anmelden im Mitgliedstaat, über den man die EU verlässt bzw. betritt."""
    items = {item["id"]: item for item in load_content().customs["items"]}
    assert "Ungarn, Kroatien oder Bulgarien" in items["tr-bargeld"]["rule"]["de"]
    assert "Bulgarien" in items["eu-bargeld"]["rule"]["de"] and "185.000 TL" in items["eu-bargeld"]["rule"]["de"]
    assert "eu_barmittel" in items["tr-bargeld"]["sources"] and "tr_nakit_cikis" in items["eu-bargeld"]["sources"]


def test_route_checklist_covers_licence_children_and_pets():
    docs = {doc["id"] for doc in load_content().transit["documents"]}
    assert {"fuehrerschein", "einverstaendnis", "haustier", "ausstattung"} <= docs


def test_vignette_prices_name_the_vehicle_class():
    countries = load_content().transit["countries"]
    si = " ".join(p["label"]["de"] for p in countries["SI"]["prices"])
    hu = " ".join(p["label"]["de"] for p in countries["HU"]["prices"])
    assert "2A" in si and "2B" in si
    assert "D1" in hu and "D2" in hu
    for price in countries["RO"]["prices"]:  # Rovinieta-Preise gelten nur für Kategorie A
        assert "Kat. A" in price["label"]["de"] and "A kategorisi" in price["label"]["tr"]


def test_car_driver_rule_requires_the_holder_to_be_in_turkey():
    """Tebliğ Seri No: 9 – Verwandte dürfen ohne den Berechtigten fahren, solange er in der Türkei ist."""
    rule = next(i for i in load_content().customs["items"] if i["id"] == "tr-auto-fahrer")["rule"]
    assert "solange du selbst in der Türkei bist" in rule["de"]
    assert "sen Türkiye'deyken" in rule["tr"]


def test_imei_limit_is_per_passport():
    rule = next(i for i in load_content().customs["items"] if i["id"] == "tr-handy")["rule"]
    assert "Pro Pass" in rule["de"] and "Pasaport başına" in rule["tr"]


@pytest.mark.parametrize("path, country_heading", [("/de/route", "Serbien"), ("/tr/guzergah", "Sırbistan")])
def test_route_page_shows_sources_of_single_notes_and_checklist_items(client, path, country_heading):
    """Hinweise mit eigener Quelle dürfen nicht unter der Länderquelle verschwinden."""
    html = client.get(path).get_data(as_text=True)
    rs = html.split('id="land-RS"', 1)[1].split("</details>", 1)[0]
    assert country_heading in rs
    assert "https://www.adac.de/reise-freizeit/reiseplanung/reiseziele/serbien/fahrzeug/" in rs
    assert "https://digital-strategy.ec.europa.eu/" in rs
    checklist = html.split('id="docs-title"', 1)[1].split("</section>", 1)[0]
    for doc in load_content().transit["documents"]:
        if "source" in doc:
            assert doc["source"]["url"] in checklist, doc["id"]
    assert 'class="src-inline"' in checklist and "style=" not in checklist


def test_info_page_lists_note_and_checklist_sources(client):
    html = client.get("/de/info").get_data(as_text=True)
    assert "https://www.oeamtc.at/thema/reiseplanung/mitfuehrpflichten-fuer-autofahrer-in-europa-16183282" in html
    assert "https://www.vidincalafatbridge.bg/en/charges" in html


def test_strings_have_identical_keys_in_both_languages():
    tr = Translator()
    de_keys, tr_keys = set(tr.strings["de"]), set(tr.strings["tr"])
    assert de_keys == tr_keys, f"nur DE: {de_keys - tr_keys}, nur TR: {tr_keys - de_keys}"


def test_strings_use_same_placeholders_and_are_not_empty():
    tr = Translator()
    for key, de_value in tr.strings["de"].items():
        tr_value = tr.strings["tr"][key]
        assert type(de_value) is type(tr_value), key
        if isinstance(de_value, list):
            assert len(de_value) == len(tr_value), key
            continue
        assert de_value.strip() and tr_value.strip(), key
        assert set(re.findall(r"\{(\w+)\}", de_value)) == set(re.findall(r"\{(\w+)\}", tr_value)), key


def test_every_customs_rule_cites_an_official_https_source():
    content = load_content()
    for item in content.customs["items"]:
        for ref in item["sources"]:
            assert content.customs["sources"][ref]["url"].startswith("https://")


def test_fold_makes_search_independent_of_diacritics():
    assert fold("Kaşar") == "kasar"
    assert fold("İçki") == "icki"
    assert fold("Käse") == "kase"
    assert fold("RAKI") == fold("rakı")


def test_language_negotiation():
    assert negotiate("tr-TR,tr;q=0.9,de;q=0.8") == "tr"
    assert negotiate("de-DE,de;q=0.9,tr;q=0.5") == "de"
    assert negotiate("en-US,en;q=0.9") == "de"
    assert negotiate(None) == "de"
    assert set(LANGS) == {"de", "tr"}
