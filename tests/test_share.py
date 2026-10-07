"""Startseite, Grenz-Leerzustand, Melden, Teilen und Rechtstexte (Audit prod-1, prod-3, prod-4, legal-1, legal-6)."""
import html as htmllib
import json
import re
import struct
from pathlib import Path

import pytest

from tatilvakti import borders as B
from tatilvakti import create_app, operator_imprint
from tatilvakti.content import load_content, validate

ROOT = Path(__file__).parent.parent
OPERATOR = {"TV_OPERATOR_NAME": "Erika Muster", "TV_OPERATOR_ADDRESS": "Musterweg 1;12345 Musterstadt",
            "TV_OPERATOR_EMAIL": "kontakt@example.org"}


def page(client, path):
    """HTML ohne den JSON-Block für app.js (dort stehen alle Texte, auch unbenutzte)."""
    return client.get(path).get_data(as_text=True).split('<script type="application/json" id="tv-strings">')[0]


def text(html):
    return htmllib.unescape(re.sub(r"<[^>]+>", " ", html))


def meta(html, prop):
    m = re.search(r'<meta (?:property|name)="' + re.escape(prop) + r'" content="([^"]*)">', html)
    return htmllib.unescape(m.group(1)) if m else None


def report(client, cid, direction="to_tr", bucket=2, ip="198.51.100.7"):
    resp = client.post(f"/api/v1/borders/{cid}/reports", json={"direction": direction, "bucket": bucket},
                       environ_base={"REMOTE_ADDR": ip})
    assert resp.status_code == 201, resp.get_json()


# ------------------------------------------------------------------ Daten: Ortsangabe, Quellen

def test_every_crossing_has_a_location_form_for_the_report_title():
    content = load_content()
    for c in content.crossings["crossings"]:
        assert c["name_loc"]["de"] == "in " + c["short"]
        assert re.fullmatch(re.escape(c["short"]) + "'(da|de|ta|te)", c["name_loc"]["tr"]), c["id"]
    by_id = content.crossing_by_id
    # Vokalharmonie: hinten (a, ı, o, u) → -da, vorn (e, i, ö, ü) → -de
    assert (by_id["kapikule"]["name_loc"]["tr"], by_id["ipsala"]["name_loc"]["tr"],
            by_id["asotthalom"]["name_loc"]["tr"], by_id["batrovci"]["name_loc"]["tr"]) == (
        "Kapıkule'de", "İpsala'da", "Ásotthalom'da", "Batrovci'de")


@pytest.mark.parametrize("loc, needle", [
    (None, "name_loc: nicht zweisprachig"),
    ({"de": "in Kapıkule", "tr": "Kapıkulede"}, "Kapıkule'da/'de/'ta/'te erwartet"),
    ({"de": "in Kapıkule", "tr": "Kapıkule'den"}, "Kapıkule'da/'de/'ta/'te erwartet"),
    ({"de": "in Edirne", "tr": "Kapıkule'de"}, "name_loc.de: muss den Kurznamen"),
])
def test_validation_rejects_broken_location_forms(loc, needle):
    content = load_content()
    content.crossing_by_id["kapikule"]["name_loc"] = loc
    assert any(needle in p for p in validate(content)), validate(content)


def test_validation_requires_bilingual_source_labels():
    content = load_content()
    content.crossings["sources"]["hu_police"]["label"] = {"de": "Ungarn: Polizei", "tr": ""}
    assert any("crossings.sources.hu_police.label" in p for p in validate(content))


# ------------------------------------------------------------------ prod-1: Kaltstart

@pytest.mark.parametrize("path, line, call", [
    ("/de/", "Noch keine Meldung in den letzten 2 Std.", "Sei die erste Person, die meldet"),
    ("/tr/", "Son 2 saatte henüz bildirim yok.", "İlk bildiren sen ol"),
])
def test_home_cold_start_shows_one_line_instead_of_grey_chips(client, path, line, call):
    html = page(client, path)
    station = html.split('id="pick-title"')[0].rsplit('<li class="station', 1)[1]
    assert '<div class="live is-empty" data-live>' in station  # CSS blendet die Chips aus
    empty = re.search(r'<p class="live__empty">(.*?)</p>', station, re.S).group(1)
    assert line in text(empty) and call in text(empty)


def test_report_switches_the_empty_state_off_per_crossing(client):
    report(client, "horgos")
    html = page(client, "/de/")
    assert '<div class="live" data-live>' in html
    rows = dict(re.findall(r'href="/de/grenze/(\w+)">[^<]*</a>\s*<div class="(dirs[^"]*)" data-dirs>', html))
    assert rows == {"kapikule": "dirs is-empty", "gradina": "dirs is-empty", "horgos": "dirs", "batrovci": "dirs is-empty"}
    assert 'data-cid="horgos" data-dir="to_tr" data-live="1"' in html
    assert 'data-cid="horgos" data-dir="to_de" data-live="0"' in html


@pytest.mark.parametrize("path", ["/de/grenze", "/tr/sinir", "/de/route", "/tr/guzergah"])
def test_lists_without_reports_are_collapsed(client, path):
    html = page(client, path)
    lists = re.findall(r'class="live( is-empty)?"[^>]*data-live>', html)
    assert lists and all(lists), lists  # jede Liste im Leerzustand
    report(client, "kapikule")
    lists = re.findall(r'class="live( is-empty)?"[^>]*data-live>', page(client, path))
    assert "" in lists  # die Liste mit Kapıkule (Richtung Türkei) zeigt jetzt Status


@pytest.mark.parametrize("path, heading", [
    ("/de/", "Infos von Behörden und Automobilclubs"),
    ("/tr/", "Resmî kurumlardan ve otomobil kulüplerinden bilgiler"),
    ("/de/grenze", "Infos von Behörden und Automobilclubs"),
    ("/tr/sinir", "Resmî kurumlardan ve otomobil kulüplerinden bilgiler"),
])
def test_official_sources_are_linked_on_home_and_overview(client, path, heading):
    html = page(client, path)
    assert heading in text(html)
    sources = load_content().crossings["sources"]
    lang = path.split("/")[1]
    for key in ("hu_police", "bg_police", "tr_trakya"):
        assert f'href="{sources[key]["url"]}"' in html
        assert htmllib.escape(sources[key]["label"][lang]) in html


def test_crossing_page_lists_sources_with_bilingual_labels(client):
    html = page(client, "/tr/sinir/kapikule")
    assert "Bulgaristan: Sınır Polisi" in html and "Türkiye: Trakya Gümrük" in html
    assert "Bulgarische Grenzpolizei" not in html


# ------------------------------------------------------------------ prod-3: Melden

@pytest.mark.parametrize("lang", ["de", "tr"])
def test_home_offers_a_choice_of_crossings_instead_of_kapikule_only(client, lang):
    html = page(client, f"/{lang}/")
    picks = re.findall(r'<li data-pick="(\w+)"( hidden)?>\s*<a class="picker__btn" href="([^"]+)"', html)
    crossings = load_content().crossings["crossings"]
    assert [p[0] for p in picks] == [c["id"] for c in crossings]  # alle, für „zuletzt gemeldet“
    visible = [p for p in picks if not p[1]]
    assert [p[0] for p in visible] == [c["id"] for c in crossings if c["main"]]
    assert len(visible) == 4
    slug = "grenze" if lang == "de" else "sinir"
    assert all(p[2] == f"/{lang}/{slug}/{p[0]}#melden" for p in picks)
    assert "cta--accent" not in html  # der alte, fest verdrahtete Knopf


@pytest.mark.parametrize("path, title", [
    ("/de/grenze/kapikule", "Wie lange hast du in Kapıkule gewartet?"),
    ("/de/grenze/horgos", "Wie lange hast du in Röszke gewartet?"),
    ("/tr/sinir/kapikule", "Kapıkule'de ne kadar bekledin?"),
    ("/tr/sinir/ipsala", "İpsala'da ne kadar bekledin?"),
    ("/tr/sinir/asotthalom", "Ásotthalom'da ne kadar bekledin?"),
])
def test_report_form_names_the_crossing(client, path, title):
    html = page(client, path)
    heading = re.search(r'<h2 id="report-title"[^>]*>(.*?)</h2>', html, re.S).group(1)
    assert text(heading).strip() == title


def test_no_direction_is_preselected_on_the_server(client):
    assert not re.search(r'name="direction"[^>]*checked', page(client, "/de/grenze/kapikule"))


@pytest.mark.parametrize("lang", ["de", "tr"])
def test_texts_promise_the_real_number_of_steps(client, lang):
    strings = json.loads((ROOT / "tatilvakti" / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
    assert "einem Tipp" not in strings["b_lead"] and "tek dokunuş" not in strings["b_lead"]
    assert ("drei Schritten" if lang == "de" else "üç adımda") in strings["b_lead"]


# ------------------------------------------------------------------ prod-4: Link-Vorschau

PREVIEW_PAGES = ["/de/", "/tr/", "/de/ferien", "/tr/tatil", "/de/route", "/tr/guzergah", "/de/grenze", "/tr/sinir",
                 "/de/grenze/kapikule", "/tr/sinir/kapikule", "/de/zoll", "/tr/gumruk", "/de/info", "/tr/bilgi"]


@pytest.mark.parametrize("path", PREVIEW_PAGES)
def test_every_page_has_open_graph_and_twitter_tags(client, path):
    html = page(client, path)
    lang = path.split("/")[1]
    title = htmllib.unescape(re.search(r"<title>(.*?)</title>", html).group(1))
    description = meta(html, "description")
    assert meta(html, "og:title") == title and meta(html, "og:description") == description and description
    assert meta(html, "og:site_name") == "tatilvakti" and meta(html, "og:type") == "website"
    assert meta(html, "og:url") == "https://tatilvakti.example" + path
    assert meta(html, "og:locale") == ("de_DE" if lang == "de" else "tr_TR")
    assert meta(html, "og:locale:alternate") == ("tr_TR" if lang == "de" else "de_DE")
    image = meta(html, "og:image")
    assert re.fullmatch(rf"https://tatilvakti\.example/static/og/og-{lang}\.png\?v=[0-9a-f]{{10}}", image)
    assert (meta(html, "og:image:width"), meta(html, "og:image:height")) == ("1200", "630")
    assert meta(html, "og:image:alt") and meta(html, "twitter:card") == "summary_large_image"
    resp = client.get(image.replace("https://tatilvakti.example", ""))
    assert resp.status_code == 200 and resp.mimetype == "image/png"


@pytest.mark.parametrize("lang", ["de", "tr"])
def test_preview_images_are_small_pngs_in_the_right_size(lang):
    data = (ROOT / "tatilvakti" / "static" / "og" / f"og-{lang}.png").read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) < 300 * 1024
    assert struct.unpack(">II", data[16:24]) == (1200, 630)


def test_preview_urls_fall_back_to_the_request_host(tmp_path, clock):
    app = create_app({"TESTING": True, "TV_DB_PATH": str(tmp_path / "h.db"), "TV_CLOCK": clock})
    html = app.test_client().get("/tr/sinir").get_data(as_text=True)
    assert meta(html, "og:url") == "http://localhost/tr/sinir"
    assert meta(html, "og:image").startswith("http://localhost/static/og/og-tr.png?v=")


@pytest.mark.parametrize("lang, expected", [
    ("de", "Nordrhein-Westfalen, Sommerferien 2027: 19.07.–31.08.2027 · Tage mit der kleinsten Reisewelle – "
           "Abreise: Di 20.07., Mi 21.07., Do 22.07.; Rückreise: "),
    ("tr", "Nordrhein-Westfalen, 2027 yaz tatili: 19.07.–31.08.2027 · Tatil dalgasının en küçük olduğu günler – "
           "gidiş: Sal 20.07., Çar 21.07., Per 22.07.; dönüş: "),
])
def test_holiday_share_text_names_the_state_and_the_quiet_days(client, lang, expected):
    path = "/de/ferien/sommer-2027" if lang == "de" else "/tr/tatil/yaz-2027"
    html = page(client, f"{path}?land=NW")
    article = re.search(r'<article class="card card--brand" data-per-state="NW">(.*?)</article>', html, re.S).group(1)
    shares = re.findall(r'data-share-text="([^"]*)"', article)
    assert len(shares) == 1  # ein Teilen-Block je Kontext
    share = htmllib.unescape(shares[0])
    assert share.startswith(expected), share
    assert "Ferien Sommerferien" not in share and "tatilimiz" not in share and " NRW" not in share
    # Link-Vorschau des geteilten Links: dieselbe Zusammenfassung
    assert meta(html, "og:description") == share
    wa = htmllib.unescape(re.search(r'href="(https://wa\.me/[^"]+)"', article).group(1))
    # Geteilt wird der Pfad des Zeitraums (eigene Seite), das Bundesland bleibt als ?land= dran
    assert wa.endswith(f"%20https%3A//tatilvakti.example{path}%3Fland%3DNW")


def test_holiday_page_without_state_keeps_the_lead_as_preview(client):
    html = page(client, "/de/ferien/sommer-2027")
    assert meta(html, "og:description").startswith("Alle 16 Bundesländer auf einen Blick.")


@pytest.mark.parametrize("path, expected", [
    ("/de/grenze/kapikule", "Wartest du gerade in Kapıkule? Melde die Wartezeit auf tatilvakti"),
    ("/tr/sinir/kapikule", "Kapıkule'de bekliyor musun? Bekleme süresini tatilvakti'de bildir"),
    ("/tr/sinir/ipsala", "İpsala'da bekliyor musun?"),
])
def test_empty_crossing_shares_a_call_to_report(client, path, expected):
    html = page(client, path)
    share = htmllib.unescape(re.search(r'data-share-text="([^"]*)"', html).group(1))
    assert share.startswith(expected) and "keine" not in share and "veri yok" not in share


def test_live_crossing_shares_only_directions_with_reports(client, clock):
    report(client, "kapikule", "to_de", 3)
    clock.advance(minutes=10)
    html = page(client, "/de/grenze/kapikule")
    share = htmllib.unescape(re.search(r'data-share-text="([^"]*)"', html).group(1))
    assert share == "Kapıkule – Richtung Deutschland: 1–2 Std. (vor 10 Min.). Gemeldet von Reisenden auf tatilvakti."
    wa = re.search(r'href="(https://wa\.me/\?text=[^"]+)"', html).group(1)
    assert "utm_" not in wa and wa.endswith("%20https%3A//tatilvakti.example/de/grenze/kapikule")


def test_whatsapp_link_and_system_share_are_separate(client):
    html = page(client, "/de/grenze/kapikule")
    block = re.search(r'<div class="share">(.*?)</div>', html, re.S).group(1)
    assert re.search(r'<a class="btn btn--wa" href="https://wa\.me/\?text=[^"]+" target="_blank"', block)
    assert "data-share" not in block.split("</a>")[0]  # der WhatsApp-Link öffnet kein System-Menü
    assert re.search(r'<button class="btn btn--ghost" type="button" data-share [^>]*hidden>', block)


def test_every_customs_rule_can_be_shared_with_its_anchor(client):
    html = page(client, "/de/zoll")
    items = load_content().customs["items"]
    for item in items:
        article = re.search(rf'<article class="card rule[^"]*" id="{item["id"]}".*?</article>', html, re.S).group(0)
        assert article.count('<div class="share share--inline">') == 1
        url = re.search(r'data-share-url="([^"]*)"', article).group(1)
        assert url == f"https://tatilvakti.example/de/zoll#{item['id']}"
        share = htmllib.unescape(re.search(r'data-share-text="([^"]*)"', article).group(1))
        assert share.startswith(item["title"]["de"] + " – ") and share.endswith(item["rule"]["de"])


def test_route_page_can_be_shared(client):
    html = page(client, "/tr/guzergah")
    assert html.count('data-share-text="') == 1
    share = htmllib.unescape(re.search(r'data-share-text="([^"]*)"', html).group(1))
    assert share.startswith("Arabayla Türkiye'ye: 3 güzergâh karşılaştırması")


# ------------------------------------------------------------------ legal-1: Impressum

@pytest.mark.parametrize("missing", ["TV_OPERATOR_NAME", "TV_OPERATOR_ADDRESS", "TV_OPERATOR_EMAIL"])
def test_imprint_is_never_shown_half(tmp_path, clock, missing):
    config = {**OPERATOR, missing: "  "}
    app = create_app({"TESTING": True, "TV_DB_PATH": str(tmp_path / "i.db"), "TV_CLOCK": clock, **config})
    client = app.test_client()
    section = re.search(r'<section class="card prose" id="impressum".*?</section>', page(client, "/de/info"), re.S).group(0)
    assert "<address>" not in section and "Erika Muster" not in section and "kontakt@example.org" not in section
    assert "Das Impressum ist noch nicht eingerichtet." in section
    assert "TV_OPERATOR" not in section  # keine Variablennamen für Besucher
    assert client.get("/healthz").get_json()["imprint_ok"] is False


def test_complete_imprint_shows_name_address_and_email(tmp_path, clock):
    app = create_app({"TESTING": True, "TV_DB_PATH": str(tmp_path / "i.db"), "TV_CLOCK": clock, **OPERATOR})
    client = app.test_client()
    section = re.search(r'<section class="card prose" id="impressum".*?</section>', page(client, "/tr/bilgi"), re.S).group(0)
    assert "<address>Erika Muster<br>Musterweg 1<br>12345 Musterstadt</address>" in section
    assert 'href="mailto:kontakt@example.org"' in section
    assert client.get("/healthz").get_json()["imprint_ok"] is True


@pytest.mark.parametrize("address, ok", [("Musterweg 1;12345 Musterstadt", True), (" ; ", False), ("", False)])
def test_operator_imprint_needs_a_real_address_line(address, ok):
    assert (operator_imprint({**OPERATOR, "TV_OPERATOR_ADDRESS": address}) is not None) is ok


@pytest.mark.parametrize("base_url, operator, warned", [
    ("https://tatilvakti.example", {}, True),
    ("https://tatilvakti.example", {**OPERATOR, "TV_OPERATOR_EMAIL": ""}, True),
    ("https://tatilvakti.example", OPERATOR, False),
    ("", {}, False),  # Entwicklung ohne öffentliche Adresse
])
def test_start_warns_in_production_without_complete_imprint(tmp_path, clock, caplog, base_url, operator, warned):
    with caplog.at_level("WARNING"):
        create_app({"TESTING": True, "TV_DB_PATH": str(tmp_path / "w.db"), "TV_CLOCK": clock,
                    "TV_BASE_URL": base_url, **operator})
    messages = [r.getMessage() for r in caplog.records if "Impressum unvollständig" in r.getMessage()]
    assert bool(messages) is warned
    if warned:
        assert "TV_OPERATOR_EMAIL" in messages[0] and "kontakt@" not in messages[0]


# ------------------------------------------------------------------ legal-6: Datenschutz

# Was app.js im localStorage ablegt (Schlüssel tv.<name>) – der Datenschutztext nennt jeden davon
DOCUMENTED_KEYS = {"state": "prefs", "mode": "prefs", "checks": "checks", "queue": "queue", "report_pref": "last",
                   "visits": "visits", "seen_at": "visits", "a2hs_off": "visits"}


def test_privacy_text_covers_every_storage_key_in_app_js():
    js = (ROOT / "tatilvakti" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    keys = set(re.findall(r'store\.(?:get|set)\("([\w-]+)"', js))
    assert keys == set(DOCUMENTED_KEYS), "neuer Schlüssel in app.js: Datenschutztext (i_privacy_dev_*) ergänzen"
    assert "sessionStorage" not in js and "indexedDB" not in js and "document.cookie" not in js
    template = (ROOT / "tatilvakti" / "templates" / "info.html").read_text(encoding="utf-8")
    listed = re.search(r'for key in \(([^)]*)\)', template).group(1)
    for item in set(DOCUMENTED_KEYS.values()) | {"cache"}:
        assert f'"{item}"' in listed


@pytest.mark.parametrize("path, words", [
    ("/de/info", ["Auf deinem Gerät", "Bundesland und die Reiseart", "Häkchen bei den Papieren",
                  "ohne Netz noch nicht gesendet", "Übergang und Richtung deiner letzten Meldung",
                  "Hinweis zum Startbildschirm", "Offline-Kopie der Seiten", "Cache des Service Workers",
                  "§ 25 Abs. 2 Nr. 2 TDDDG", "kein Cookie-Banner", "Website-Daten", "?land=",
                  "Zugriffsprotokoll", "eigenen Datei", f"spätestens {B.CLIENT_HASH_TTL_H} Stunden",
                  f"{B.RETENTION_DAYS} Tage", "Sicherungskopien", "weder Prüfwerte noch Tagesschlüssel",
                  "Die IP-Adresse selbst speichert die App nicht"]),
    ("/tr/bilgi", ["Cihazında", "eyaletin ve yolculuk şeklin", "araç belgelerinde", "henüz gönderilemeyen",
                   "son bildiriminin sınır kapısı ve yönü", "ana ekran ipucu", "internetsiz kullanım için kaydedilmiş kopyası",
                   "service worker önbelleği", "TDDDG md. 25 f. 2 b. 2", "çerez uyarısı da yok", "site verilerini",
                   "?land=", "erişim kaydında", "kendi dosyasında", f"en geç {B.CLIENT_HASH_TTL_H} saat",
                   f"{B.RETENTION_DAYS} gün", "yedeklerinde", "IP adresinin kendisini uygulama kaydetmez"]),
])
def test_privacy_text_matches_the_code(client, path, words):
    section = re.search(r'<section class="card prose" id="datenschutz".*?</section>', page(client, path), re.S).group(0)
    plain = " ".join(text(section).split())
    for word in words:
        assert word in plain, word
    # Die alte Pauschalaussage stimmte wegen ?land= nicht
    assert "verlassen dein Gerät nicht" not in plain and "Cihazından çıkmaz" not in plain
