"""Sprache: keine deutschen Reste in der türkischen Ansicht (prod-6), türkische Texte in
durchgehender sen-Form und ohne die im Audit gefundenen Fehler (prod-5)."""
import html as htmllib
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from tatilvakti.content import load_content

ROOT = Path(__file__).resolve().parents[1] / "tatilvakti"

# Typische deutsche Wörter, als ganzes Wort (Groß/klein egal). Nicht direkt nach einem Apostroph:
# türkische Endungen wie AB'den, Kapıkule'de sind keine deutschen Wörter.
GERMAN_WORDS = (
    "und", "der", "die", "das", "den", "dem", "des", "mit", "für", "von", "vom", "zum", "zur", "oder",
    "nicht", "nur", "bei", "ist", "sind", "auf", "aus", "über", "ca.", "live", "Quelle", "Quellen", "Preise",
    "Wartezeit", "Wartezeiten", "Seite", "Grenze", "Zoll", "Ferien", "Gebühr", "Gebühren", "Maut", "Handy",
    "Gold", "Polizei", "Regierung", "mehrere", "gerundet", "staatlicher", "Straßenagentur", "Stand",
    "geprüft", "Hinweis", "Tage", "Klasse", "Pkw", "Vignette", "Fahrzeug", "Reisefreimengen", "Himmelfahrt",
)
GERMAN_RE = re.compile(r"(?<![\w'’])(?:" + "|".join(re.escape(w) for w in GERMAN_WORDS) + r")(?![\w'’])",
                       re.IGNORECASE)
# Eigennamen und amtliche Bezeichnungen, die auch im Türkischen so stehen (mit Erklärung daneben)
PROPER_NAMES = (
    "Zoll-Portal",                      # Online-Portal des deutschen Zolls
    "Nämlichkeitsnachweis",             # Bescheinigung des deutschen Zolls, im Text erklärt
    "Zulassungsbescheinigung Teil I",   # amtlicher Name des Fahrzeugscheins
)
# Attribute mit sichtbarem bzw. vorgelesenem Text (data-search ist nur Suchindex, beide Sprachen)
TEXT_ATTRS = {"title", "aria-label", "alt", "placeholder", "data-share-text"}
TEXT_META = {"description", "og:title", "og:description", "og:image:alt", "og:site_name", "twitter:image:alt"}


class TextCollector(HTMLParser):
    """Sichtbarer und vorgelesener Text einer Seite, dazu die Texte für das JavaScript (tv-strings)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self._skip, self._json = [], 0, False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style"):
            self._skip += 1
            self._json = attrs.get("id") == "tv-strings"
        if tag == "meta" and (attrs.get("name") in TEXT_META or attrs.get("property") in TEXT_META):
            self.parts.append(attrs.get("content") or "")
        self.parts += [value for name, value in attrs.items() if name in TEXT_ATTRS and value]

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip -= 1
            self._json = False

    def handle_data(self, data):
        if self._json:
            self.parts += [v for v in json.loads(data).values() if isinstance(v, str)]
        elif not self._skip:
            self.parts.append(data)


def visible_text(html: str) -> str:
    collector = TextCollector()
    collector.feed(html)
    text = " ".join(" ".join(collector.parts).split())
    for name in PROPER_NAMES:
        text = text.replace(name, " ")
    return text


def tr_pages(app) -> list[str]:
    radar = app.extensions["tv"].radar
    content = app.extensions["tv"].content
    pages = ["/tr/", "/tr/tatil", "/tr/guzergah", "/tr/sinir", "/tr/gumruk", "/tr/bilgi", "/tr/cevrimdisi",
             "/tr/tatil?land=NW", "/tr/sayfa-yok"]
    pages += [f"/tr/tatil/{p.slug['tr']}" for p in radar.periods]
    pages += [f"/tr/sinir/{c['id']}" for c in content.crossings["crossings"]]
    pages += [f"/tr/sinir/kapikule?fehler={code}" for code in ("ratelimited", "busy", "stale", "invalid")]
    pages += ["/tr/sinir/kapikule?gemeldet=1"]
    return pages


def test_turkish_pages_contain_no_german_words(app, client):
    # Mit Meldungen: auch die Live-Texte (Bereiche, Anzahl, Alter) kommen vor
    for cid, bucket in (("kapikule", 3), ("horgos", 1)):
        assert client.post(f"/api/v1/borders/{cid}/reports", json={"direction": "to_tr", "bucket": bucket}).status_code == 201
    found = {}
    for path in tr_pages(app):
        resp = client.get(path)
        assert resp.status_code in (200, 404), path
        hits = sorted({m.group(0) for m in GERMAN_RE.finditer(visible_text(resp.get_data(as_text=True)))})
        if hits:
            found[path] = hits
    assert found == {}


def test_german_word_check_finds_real_leftovers():
    """Gegenprobe: Die Prüfung schlägt bei den Resten aus dem Audit an, nicht bei türkischen Endungen."""
    html = ('<title>Gümrük ve bavul: Sucuk, Gold, Handy, Rakı</title><p>Kapıkule: Bekleme live</p>'
            '<a title="Ungarische Polizei – Határinfo (Wartezeiten)">x</a><td>ca. 6.900 Ft</td>'
            '<meta property="og:description" content="Preise 2026, mehrere Quellen, gerundet">'
            '<script type="application/json" id="tv-strings">{"x": "Quelle und Stand"}</script>')
    hits = {m.group(0).lower() for m in GERMAN_RE.finditer(visible_text(html))}
    assert hits >= {"gold", "handy", "live", "polizei", "wartezeiten", "ca.", "preise", "mehrere", "quelle",
                    "und", "stand", "gerundet"}
    turkish = ("<p>AB'den çıkarken, Kapıkule'de, 01.10.2026'dan beri; HGS'siz mi geçtin? Alman gümrüğünün "
               "Zoll-Portal sitesi; Araç ruhsatı (Zulassungsbescheinigung Teil I)</p>")
    assert GERMAN_RE.findall(visible_text(turkish)) == []


def test_price_with_words_is_shown_per_language(app, client):
    """„ca.“ steht nur in der deutschen Ansicht, die türkische bekommt ihre eigene Angabe."""
    country = app.extensions["tv"].content.transit["countries"]["HU"]
    country["prices"][0]["value"] = {"de": "ca. 6.900 Ft", "tr": "yaklaşık 6.900 Ft"}
    tr = client.get("/tr/guzergah").get_data(as_text=True)
    de = client.get("/de/route").get_data(as_text=True)
    assert "<td>yaklaşık 6.900 Ft</td>" in tr and "ca. 6.900" not in tr
    assert "<td>ca. 6.900 Ft</td>" in de


@pytest.mark.parametrize("path, title", [
    ("/tr/gumruk", "Gümrük ve bavul: sucuk, altın, telefon, rakı – gidiş ve dönüş – tatilvakti"),
    ("/tr/sinir/kapikule", "Kapıkule bekleme süresi – yolculardan canlı bildirim – tatilvakti"),
    ("/tr/sinir", "Sınır kapılarında bekleme süreleri: Kapıkule, Gradina, Röszke, Batrovci – tatilvakti"),
    ("/tr/guzergah", "Arabayla Türkiye'ye: güzergâhlar, vinyetler ve otoyol ücretleri 2026/27 – tatilvakti"),
    ("/de/zoll", "Zoll & Koffer: Sucuk, Gold, Handy, Rakı – Türkei und zurück – tatilvakti"),
    ("/de/grenze/horgos", "Röszke: Wartezeit live – gemeldet von Reisenden – tatilvakti"),
    ("/de/route", "Mit dem Auto in die Türkei: Routen, Vignetten und Maut 2026/27 – tatilvakti"),
])
def test_page_titles_come_from_the_texts_of_each_language(client, path, title):
    html = client.get(path).get_data(as_text=True)
    assert htmllib.unescape(re.search(r"<title>(.*?)</title>", html).group(1)) == title


def test_no_page_title_is_hardcoded_in_a_template():
    """Seitentitel nur aus i18n (t(...)) und Daten – keine festen Wörter im title-Block (prod-6)."""
    for path in sorted((ROOT / "templates").glob("*.html")):
        block = re.search(r"{% block title %}(.*?){% endblock %}", path.read_text(encoding="utf-8"), re.S)
        if block:
            rest = re.sub(r"{{.*?}}", "", block.group(1))
            assert rest.strip(" –") == "", (path.name, rest)


def test_timezone_of_every_crossing_has_a_name_in_both_languages():
    tr = json.loads((ROOT / "i18n" / "tr.json").read_text(encoding="utf-8"))
    for crossing in load_content().crossings["crossings"]:
        assert "tz_" + crossing["tz"].split("/")[1].lower() in tr, crossing["id"]


# ------------------------------------------------------------------ prod-5

def _turkish_texts():
    """Alle türkischen Texte: tr.json und jedes tr-Feld der kuratierten Daten."""
    texts = []

    def walk(obj, where):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key == "tr" and isinstance(value, str):
                    texts.append((f"{where}.tr", value))
                else:
                    walk(value, f"{where}.{key}")
        elif isinstance(obj, list):
            for idx, value in enumerate(obj):
                walk(value, f"{where}[{idx}]")

    content = load_content()
    for name in ("holidays", "customs", "transit", "crossings"):
        walk(getattr(content, name), name)
    for key, value in json.loads((ROOT / "i18n" / "tr.json").read_text(encoding="utf-8")).items():
        texts += [(f"tr.json:{key}", v) for v in (value if isinstance(value, list) else [value])]
    return texts


# Höfliche Anrede (siz) bzw. Mehrzahl-Imperativ – die App duzt durchgehend (sen-Form)
SIZ_RE = re.compile(r"(?<![\w'’])(?:siz|sizin|size|sizi|sizde|sizden)(?![\w'’])|\w*(?:iniz|ınız|unuz|ünüz)\w*"
                    r"|(?<![\w'’])(?:edin|seçin|bakın|yapın|deneyin|girin|tıklayın|dokunun|ekleyin|bildirin|gönderin"
                    r"|kullanın|kaydedin|sorun)(?![\w'’])", re.IGNORECASE)


def test_turkish_texts_use_the_informal_sen_form_throughout():
    texts = _turkish_texts()
    assert len(texts) > 400
    assert [(where, m.group(0)) for where, text in texts for m in SIZ_RE.finditer(text)] == []


@pytest.mark.parametrize("wrong, why", [
    ("Sınır canlı", "Wort-für-Wort-Übersetzung von „Grenze live“"),
    ("serbest:", "„serbest“ heißt erlaubt, nicht schulfrei"),
    ("Şüphede", "kein natürliches Türkisch"),
    ("şüphede", "kein natürliches Türkisch"),
    ("cihaz kapatılır", "klingt nach „wird ausgeschaltet“"),
    ("paketler de el konur", "Dativ fehlt: „…ürünlere de el konur“"),
    ("Ne girebilir", "holprig"),
    ("Bir örüntü", "akademisch"),
    ("gün içi seyir", "akademisch"),
    ("bildirimin yaşı", "missverständlich"),
    ("yok (artık)", "holprig"),
    ("eyaletiniz", "Sie-Form"),
    ("Tek tük serbest", "missverständlich"),
    ("GVKY", "unübliche Abkürzung"),
    ("hindistan cevizi", "Hindistan groß"),
    ("en pahalı tarif ", "„tarife“ (Tarif), „tarif“ heißt Beschreibung/Rezept"),
    ("Sonbahar tatili", "Zeitraum klein"),
    ("Yaz tatili", "Zeitraum klein"),
    ("evrimdış", "Fachwort; TDK schreibt ohnehin getrennt – „internetsiz“, „İnternet yok“"),
    ("çevrim içi", "Fachwort – „internetten“, „internet gelince“"),
    ("„", "deutsches Anführungszeichen – im Türkischen “…”"),
])
def test_audit_wording_is_gone_from_turkish_texts(wrong, why):
    assert [where for where, text in _turkish_texts() if wrong in text] == [], why


def test_resmi_is_always_spelled_with_circumflex():
    """„resmî“ (amtlich) – ohne Zirkumflex hieße es „sein Bild“; einheitlich wie die TDK."""
    assert [where for where, text in _turkish_texts() if re.search(r"(?<!\w)resmi(?!\w)", text, re.I)] == []


@pytest.mark.parametrize("key, expected", [
    ("station_border", "Sınırda son durum"),
    ("hol_free", "Hafta sonu ve resmî tatillerle birlikte toplam tatil: {dates}"),
    ("c_no_results", "Sonuç yok. Emin değilsen gümrükte beyan et veya resmî kaynağa bak."),
    ("c_lead", "Neleri götürebilirsin, neler yasak? Gidiş ve dönüş için; her kural resmî kaynağıyla."),
    ("b_pattern", "Saatlere göre bekleme"),
    ("err_404_text", "Bu sayfa bulunamadı ya da artık yok."),
    ("hol_your", "Okul tatilin – {state}"),  # „{state} okul tatilin“ hatte keinen Anschluss
    ("hol_pressure_expl", "O gün okul tatilinde olan eyaletlerde yaşayanların Almanya nüfusundaki oranı "
                          "(kısa tatiller hariç, aşağıdaki nota bak)."),
])
def test_audit_replacements_are_in_place(key, expected):
    tr = json.loads((ROOT / "i18n" / "tr.json").read_text(encoding="utf-8"))
    assert tr[key] == expected


def test_audit_replacements_in_the_data():
    content = load_content()
    items = {i["id"]: i for i in content.customs["items"]}
    assert "Türk hatlarıyla çalışmaz (şebekeye kapatılır)" in items["tr-handy"]["rule"]["tr"]
    assert items["eu-fleisch"]["rule"]["tr"].endswith("Az miktardaki ve vakumlu paketlenmiş ürünlere de el konur.")
    assert "Hindistan cevizi" in items["eu-obst"]["detail"]["tr"]
    assert items["tr-schmuck"]["title"]["de"] == "Eigenes Gold und eigener Schmuck"
    note = content.holidays["meta"]["note"]["tr"]
    assert "eyaletinin" in note and "Okulların kendi belirlediği ek tatil günleri" in note
    assert "Göğe Yükseliş" in note  # vorher stand dort das deutsche Wort „Himmelfahrt“ ohne Erklärung
    assert [p["label"]["tr"] for p in content.holidays["periods"]][-1] == "2028 yaz tatili"
