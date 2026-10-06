"""Kuratierte Daten: vollständig, zweisprachig, mit Quellen – sonst startet die App nicht."""
import re

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
