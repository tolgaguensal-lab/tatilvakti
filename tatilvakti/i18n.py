"""Zweisprachigkeit (DE/TR): Texte, Sprachwahl, Datumsformate, Text-Normalisierung."""
from __future__ import annotations

import json
import unicodedata
from datetime import date
from pathlib import Path

LANGS = ("de", "tr")
DEFAULT_LANG = "de"

# Seiten-Slugs je Sprache – lokalisierte URLs für SEO (/de/ferien, /tr/tatil …)
SLUGS = {
    "home": {"de": "", "tr": ""},
    "holidays": {"de": "ferien", "tr": "tatil"},
    "route": {"de": "route", "tr": "guzergah"},
    "borders": {"de": "grenze", "tr": "sinir"},
    "customs": {"de": "zoll", "tr": "gumruk"},
    "info": {"de": "info", "tr": "bilgi"},
    "offline": {"de": "offline", "tr": "cevrimdisi"},
}

_DIR = Path(__file__).parent / "i18n"


class Translator:
    def __init__(self) -> None:
        self.strings: dict[str, dict] = {}
        for lang in LANGS:
            with open(_DIR / f"{lang}.json", encoding="utf-8") as fh:
                self.strings[lang] = json.load(fh)

    def t(self, lang: str, key: str, **kw) -> str:
        value = self.strings[lang].get(key)
        if value is None:
            value = self.strings[DEFAULT_LANG].get(key, key)
        if kw and isinstance(value, str):
            return value.format(**kw)
        return value

    def plural(self, lang: str, key: str, n: int, **kw) -> str:
        """Einfache Pluralregel: <key>_1 für genau 1, sonst <key>_n."""
        return self.t(lang, f"{key}_1" if n == 1 else f"{key}_n", n=n, **kw)


def negotiate(accept_language: str | None) -> str:
    """Wählt tr, wenn der Browser Türkisch vor Deutsch bevorzugt, sonst de."""
    if not accept_language:
        return DEFAULT_LANG
    best_lang, best_q = DEFAULT_LANG, -1.0
    for idx, part in enumerate(accept_language.split(",")):
        piece = part.strip().split(";")
        tag = piece[0].strip().lower()
        q = 1.0
        for param in piece[1:]:
            param = param.strip()
            if param.startswith("q="):
                try:
                    q = float(param[2:])
                except ValueError:
                    q = 0.0
        primary = tag.split("-")[0]
        if primary in LANGS:
            # Reihenfolge als Tiebreaker: frühere Einträge gewinnen bei gleichem q
            score = q - idx * 1e-6
            if score > best_q:
                best_lang, best_q = primary, score
    return best_lang


def fmt_date(d: date, lang: str, tr: Translator, with_weekday: bool = False, with_year: bool = True) -> str:
    """Numerisches Datum (in DE und TR üblich): 'Mo 19.07.2027' bzw. 'Pzt 19.07.2027'."""
    core = f"{d.day:02d}.{d.month:02d}." + (str(d.year) if with_year else "")
    if with_weekday:
        return f"{tr.t(lang, 'weekday_short')[d.weekday()]} {core}"
    return core


def fmt_range(start: date, end: date, lang: str, tr: Translator) -> str:
    if start == end:
        return fmt_date(start, lang, tr)
    same_year = start.year == end.year
    return f"{fmt_date(start, lang, tr, with_year=not same_year)}–{fmt_date(end, lang, tr)}"


def fmt_pct(share: float) -> str:
    """Prozent mit einer Nachkommastelle, Komma als Dezimaltrenner (in DE und TR gleich)."""
    value = share * 100
    if value >= 99.95:
        return "100"
    if value < 0.05:
        return "0"
    return f"{value:.1f}".replace(".", ",")


_FOLD = str.maketrans({"ı": "i", "İ": "i", "ß": "ss"})


def fold(text: str) -> str:
    """Suchnormalisierung: 'Kaşar' == 'kasar', 'Käse' == 'kase', 'İçki' == 'icki'."""
    text = text.translate(_FOLD).lower()
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))
