"""Seiten, API, PWA und Datenschutz-Garantien über HTTP."""
import html as htmllib
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

PAGES = {
    "de": ["/de/", "/de/ferien", "/de/route", "/de/grenze", "/de/grenze/kapikule", "/de/zoll", "/de/info", "/de/offline"],
    "tr": ["/tr/", "/tr/tatil", "/tr/guzergah", "/tr/sinir", "/tr/sinir/kapikule", "/tr/gumruk", "/tr/bilgi", "/tr/cevrimdisi"],
}
ALL_PAGES = PAGES["de"] + PAGES["tr"]


@pytest.mark.parametrize("path", ALL_PAGES)
def test_pages_render_in_both_languages(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    lang = path.split("/")[1]
    assert f'<html lang="{lang}"' in html
    assert "{{" not in html and "{%" not in html


@pytest.mark.parametrize("path", ALL_PAGES)
def test_no_cookies_and_strict_csp(client, path):
    resp = client.get(path)
    assert "Set-Cookie" not in resp.headers
    csp = resp.headers["Content-Security-Policy"]
    assert "unsafe-inline" not in csp and "script-src 'self'" in csp
    html = resp.get_data(as_text=True)
    # CSP-Kompatibilität: keine Inline-Styles, keine ausführbaren Inline-Skripte, keine Inline-Handler
    assert not re.search(r'\sstyle="', html)
    assert not re.search(r"\son[a-z]+=\"", html)
    for match in re.finditer(r"<script([^>]*)>(.*?)</script>", html, re.S):
        attrs, body = match.groups()
        assert "src=" in attrs or 'type="application/json"' in attrs, attrs


def test_root_redirects_by_browser_language(client):
    resp = client.get("/", headers={"Accept-Language": "tr-TR,tr;q=0.9"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/tr/")
    assert client.get("/").headers["Location"].endswith("/de/")


def test_hreflang_and_language_switch_keep_the_page(client):
    html = client.get("/de/ferien/sommer-2027?land=NW").get_data(as_text=True)
    # Canonical, hreflang und og:url ohne ?land= (keine 16 fast gleichen Seiten je Zeitraum)
    assert '<link rel="canonical" href="https://tatilvakti.example/de/ferien/sommer-2027">' in html
    assert '<link rel="alternate" hreflang="de" href="https://tatilvakti.example/de/ferien/sommer-2027">' in html
    assert '<link rel="alternate" hreflang="tr" href="https://tatilvakti.example/tr/tatil/yaz-2027">' in html
    assert '<link rel="alternate" hreflang="x-default" href="https://tatilvakti.example/de/ferien/sommer-2027">' in html
    assert '<meta property="og:url" content="https://tatilvakti.example/de/ferien/sommer-2027">' in html
    assert "de-DE" not in html.split("</head>")[0].replace('content="de_DE"', "") and "tr-TR" not in html
    # Der Nummernschild-Umschalter behält das Bundesland
    assert 'class="plate" href="/tr/tatil/yaz-2027?land=NW"' in html
    tr = client.get("/tr/tatil/yaz-2027?land=nw").get_data(as_text=True)
    assert '<link rel="canonical" href="https://tatilvakti.example/tr/tatil/yaz-2027">' in tr
    assert 'class="plate" href="/de/ferien/sommer-2027?land=NW"' in tr
    # Ungültiges Bundesland fällt weg, statt in Links weitergereicht zu werden
    bad = client.get('/de/ferien/sommer-2027?land="]').get_data(as_text=True)
    assert 'class="plate" href="/tr/tatil/yaz-2027"' in bad


@pytest.mark.parametrize("path, other, label, name", [
    ("/de/zoll", "tr", "Sprache wechseln:", "Türkçe"),
    ("/tr/gumruk", "de", "Dili değiştir:", "Deutsch"),
])
def test_language_switch_marks_only_the_language_name_with_its_lang(client, path, other, label, name):
    """Audit d1-lang-switch-lang-attr: „Sprache wechseln“ liest der Screenreader in der Seitensprache,
    nur den Namen der Zielsprache in deren Sprache (WCAG 3.1.2). Kein aria-label: Der Name kommt aus
    dem Inhalt und enthält so den sichtbaren Text (axe label-content-name-mismatch, WCAG 2.5.3)."""
    html = client.get(path).get_data(as_text=True)
    tag, inner = re.search(r'(<a class="plate"[^>]*>)(.*?)</a>', html, re.S).groups()
    assert f'hreflang="{other}"' in tag and " lang=" not in tag and "aria-label" not in tag
    assert f'<span class="sr-only">{label} </span>' in inner
    assert f'<span class="plate__txt" lang="{other}">{name}</span>' in inner
    assert re.search(r'<span class="plate__eu" aria-hidden="true">', inner)


def _periods(app):
    return app.extensions["tv"].radar.periods


@pytest.mark.parametrize("lang, base", [("de", "/de/ferien"), ("tr", "/tr/tatil")])
def test_every_period_has_its_own_page_with_title_and_h1(app, client, lang, base):
    titles, h1s = set(), set()
    for period in _periods(app):
        resp = client.get(f"{base}/{period.slug[lang]}")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        title = re.search(r"<title>(.*?)</title>", html).group(1)
        h1 = re.sub(r"<[^>]+>", "", re.search(r"<h1>(.*?)</h1>", html, re.S).group(1)).strip()
        assert period.label[lang] in title and period.label[lang] in h1, (title, h1)
        assert f'aria-current="page">{period.label[lang]}</a>' in html  # Zeitraum-Chip aktiv
        titles.add(title)
        h1s.add(h1)
    assert len(titles) == len(h1s) == len(_periods(app))


def test_period_titles_follow_search_terms(client):
    html = htmllib.unescape(client.get("/tr/tatil/yaz-2027").get_data(as_text=True))
    assert "<title>Almanya okul tatilleri: 2027 yaz tatili, tüm eyaletler – tatilvakti</title>" in html
    assert "Almanya'da 2027 yaz tatili – tüm eyaletler</h1>" in html
    html = client.get("/de/ferien/sommer-2027").get_data(as_text=True)
    assert "<title>Sommerferien 2027: Termine aller 16 Bundesländer – tatilvakti</title>" in html
    assert "Sommerferien 2027 – alle Bundesländer</h1>" in html
    kapikule = client.get("/tr/sinir/kapikule").get_data(as_text=True)
    assert "<title>Kapıkule bekleme süresi – yolculardan canlı bildirim – tatilvakti</title>" in kapikule
    # Die allgemeine Seite behält ihren Titel und zeigt den laufenden oder nächsten Zeitraum
    plain = client.get("/de/ferien").get_data(as_text=True)
    assert "<title>Ferien-Radar: Schulferien aller 16 Bundesländer – tatilvakti</title>" in plain
    assert '<link rel="canonical" href="https://tatilvakti.example/de/ferien">' in plain


@pytest.mark.parametrize("path, location", [
    ("/de/ferien?zeitraum=sommer-2027", "/de/ferien/sommer-2027"),
    ("/de/ferien?zeitraum=sommer-2027&land=NW", "/de/ferien/sommer-2027?land=NW"),
    ("/de/ferien?land=nw&zeitraum=pfingsten-2027", "/de/ferien/pfingsten-2027?land=NW"),
    ("/tr/tatil?zeitraum=sommer-2027&land=BY", "/tr/tatil/yaz-2027?land=BY"),
    ("/tr/tatil?zeitraum=weihnachten-2026", "/tr/tatil/yilbasi-2026-27"),
    ("/de/ferien?zeitraum=sommer-2027&land=XX", "/de/ferien/sommer-2027"),  # ungültiges Land fällt weg
    # Slug der anderen Sprache oder alte id im Pfad: auf den Slug dieser Sprache
    ("/tr/tatil/sommer-2027?land=NW", "/tr/tatil/yaz-2027?land=NW"),
    ("/de/ferien/yaz-2027", "/de/ferien/sommer-2027"),
])
def test_old_period_links_redirect_permanently(client, path, location):
    resp = client.get(path)
    assert resp.status_code == 301
    assert resp.headers["Location"] == location


@pytest.mark.parametrize("path, location", [
    # abgelaufener Zeitraum aus einem geteilten Link: zur Übersicht, das Bundesland bleibt
    ("/de/ferien/sommer-2026?land=NW", "/de/ferien?land=NW"),
    ("/tr/tatil/yok", "/tr/tatil"),
    ("/de/ferien/sommer-1999?land=XX", "/de/ferien"),
])
def test_unknown_period_leads_to_the_radar(client, path, location):
    """Review: Zeitraumseiten stehen in der Sitemap und in WhatsApp-Links – nach der Datenpflege
    endeten sie dauerhaft in 404. 302 statt 301: Gibt es den Slug später, darf kein Browser die
    Umleitung gespeichert haben."""
    resp = client.get(path)
    assert resp.status_code == 302 and resp.headers["Location"] == location
    assert client.get(location).status_code == 200


def test_unknown_query_shows_the_radar(client):
    resp = client.get("/de/ferien?zeitraum=sommer-1999")
    assert resp.status_code == 200 and "Ferien-Radar</h1>" in resp.get_data(as_text=True)


def test_internal_period_links_use_paths(client):
    for path in ("/de/ferien", "/de/ferien/sommer-2027?land=NW", "/tr/tatil/yaz-2027?land=NW"):
        html = client.get(path).get_data(as_text=True)
        assert "zeitraum" not in html, path
    html = client.get("/de/ferien/sommer-2027").get_data(as_text=True)
    assert 'action="/de/ferien/sommer-2027"' in html  # Bundesland-Auswahl ohne JS bleibt im Zeitraum


def test_holiday_page_personalises_server_side(client):
    html = client.get("/de/ferien/sommer-2027?land=NW").get_data(as_text=True)
    assert "Deine Ferien in Nordrhein-Westfalen" in html
    assert "02.08.–06.08.2027" in html  # alle 16 Länder gleichzeitig
    assert re.search(r'data-per-state="NW">', html)  # sichtbar (ohne hidden)
    assert re.search(r'data-per-state="BY" hidden>', html)
    assert re.search(r'data-per-state="none" hidden>', html)


def test_report_api_updates_live_status(client):
    resp = client.post("/api/v1/borders/kapikule/reports", json={"direction": "to_tr", "bucket": 3})
    assert resp.status_code == 201
    status = resp.get_json()["crossing"]["directions"]["to_tr"]
    assert status["state"] == "live" and status["bucket"] == 3 and status["count"] == 1
    again = client.post("/api/v1/borders/kapikule/reports", json={"direction": "to_tr", "bucket": 3})
    assert again.status_code == 429 and again.get_json()["error"] == "ratelimited"
    data = client.get("/api/v1/borders").get_json()
    kapikule = next(c for c in data["crossings"] if c["id"] == "kapikule")
    assert kapikule["directions"]["to_tr"]["state"] == "live"
    assert data["source"] == "crowd"


def test_report_api_rejects_bad_input(client):
    assert client.post("/api/v1/borders/kapikule/reports", data="x").status_code == 400
    assert client.post("/api/v1/borders/kapikule/reports", json={"direction": "up", "bucket": 1}).status_code == 400
    assert client.post("/api/v1/borders/nowhere/reports", json={"direction": "to_tr", "bucket": 1}).status_code == 404
    stale = client.post("/api/v1/borders/kapikule/reports", json={"direction": "to_tr", "bucket": 1, "observed_at": 1})
    assert stale.status_code == 422


def test_honeypot_is_accepted_but_not_stored(client):
    resp = client.post("/api/v1/borders/kapikule/reports", json={"direction": "to_tr", "bucket": 1, "website": "spam"})
    assert resp.status_code == 201
    st = client.get("/api/v1/borders/kapikule").get_json()["directions"]["to_tr"]
    assert st["state"] == "none"


SAME_ORIGIN = {"Origin": "http://localhost"}


def test_report_form_works_without_javascript(client):
    resp = client.post("/de/grenze/horgos/report", data={"direction": "to_de", "bucket": "4"}, headers=SAME_ORIGIN)
    assert resp.status_code == 303 and resp.headers["Location"].endswith("/de/grenze/horgos?gemeldet=1#melden")
    page = client.get("/de/grenze/horgos?gemeldet=1").get_data(as_text=True)
    assert "Danke!" in page and "2–4 Std." in page
    limited = client.post("/de/grenze/horgos/report", data={"direction": "to_de", "bucket": "4"}, headers=SAME_ORIGIN)
    assert "fehler=ratelimited" in limited.headers["Location"]


@pytest.mark.parametrize("lang,path,text", [
    ("de", "/de/grenze/kapikule", "kommen gerade sehr viele Meldungen an"),
    ("tr", "/tr/sinir/kapikule", "çok fazla bildirim geliyor"),
])
def test_full_crossing_gets_its_own_message(client, monkeypatch, lang, path, text):
    """Obergrenze voll: nicht „Du hast hier gerade schon gemeldet“, das stimmt für Erstmelder nicht."""
    from tatilvakti import borders as B
    monkeypatch.setattr(B, "CROSSING_CAP", 0)
    api = client.post("/api/v1/borders/kapikule/reports", json={"direction": "to_tr", "bucket": 1})
    assert api.status_code == 429 and api.get_json() == {"error": "ratelimited", "detail": "crossing_busy"}
    form = client.post(f"{path}/report", data={"direction": "to_tr", "bucket": "1"}, headers=SAME_ORIGIN)
    assert form.headers["Location"].endswith(f"{path}?fehler=busy#melden")
    page = client.get(f"{path}?fehler=busy").get_data(as_text=True)
    assert text in page.split('id="tv-strings"')[0]  # Hinweis im Formular ohne JavaScript
    strings = json.loads(re.search(r'id="tv-strings">(.*?)</script>', page).group(1))
    assert text in strings["b_report_busy"]  # und für app.js


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Origin": "null"},
    {"Referer": "https://evil.example/page"},
    {},
])
def test_cross_site_form_posts_are_rejected(client, headers):
    resp = client.post("/de/grenze/horgos/report", data={"direction": "to_de", "bucket": "4"}, headers=headers)
    assert resp.status_code == 403
    assert client.get("/api/v1/borders/horgos").get_json()["directions"]["to_de"]["state"] == "none"


def test_form_accepts_configured_public_host_and_referer(client):
    ok = client.post("/de/grenze/gradina/report", data={"direction": "to_tr", "bucket": "1"},
                     headers={"Origin": "https://tatilvakti.example"})
    assert ok.status_code == 303
    ok = client.post("/de/grenze/gradina/report", data={"direction": "to_de", "bucket": "1"},
                     headers={"Referer": "http://localhost/de/grenze/gradina"})
    assert ok.status_code == 303


def test_api_rejects_foreign_origin(client):
    resp = client.post("/api/v1/borders/kapikule/reports", json={"direction": "to_tr", "bucket": 1},
                       headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403 and resp.get_json() == {"error": "forbidden"}


def test_referrer_policy_lets_own_posts_carry_origin(client):
    assert client.get("/de/").headers["Referrer-Policy"] == "same-origin"


def test_report_age_is_shown_and_expires(client, clock):
    client.post("/api/v1/borders/gradina/reports", json={"direction": "to_tr", "bucket": 0})
    clock.advance(minutes=30)
    html = client.get("/de/grenze/gradina").get_data(as_text=True)
    assert "vor 30 Min." in html and "unter 15 Min." in html
    clock.advance(hours=3)
    html = client.get("/de/grenze/gradina").get_data(as_text=True)
    assert "Keine aktuellen Meldungen" in html and "vor 3 Std." in html


def test_home_shows_next_real_holiday_start_during_gap(client, clock):
    clock.now = datetime(2027, 3, 15, 9, 0, tzinfo=timezone.utc)
    html = client.get("/de/").get_data(as_text=True)
    assert "Als Nächstes: Oster-/Frühjahrsferien 2027, Beginn Mo 22.03.2027" in html
    assert "Beginn Mo 01.03.2027" not in html


def test_home_says_honestly_when_no_further_holidays_are_listed(app, client, clock):
    """Audit d1-home-stale-pick-state: Ist für das gewählte Land nichts Künftiges mehr eingetragen,
    steht dort ein ehrlicher Hinweis statt „Wähle dein Bundesland“.

    Unabhängig vom Datenstand (ein neues Schuljahr in holidays.json darf den Test nicht brechen):
    Stichtag ist der Tag nach dem letzten freien Zeitraum des Landes, dessen Daten zuerst enden."""
    states = app.extensions["tv"].radar.states
    last = {code: max(s.end for p in _periods(app) for s in p.stretches(code)) for code in states}
    code = min(last, key=lambda c: (last[c], c))
    name = states[code]["name"]

    def blocks_on(day, lang="de"):
        clock.now = datetime(day.year, day.month, day.day, 9, 0, tzinfo=timezone.utc)
        html = client.get(f"/{lang}/").get_data(as_text=True)
        return dict(re.findall(r'<div data-per-state="(\w+)"(?: hidden)?>(.*?)</div>', html, re.S))

    # Gegenprobe am letzten eingetragenen Tag: Die Ferien laufen noch
    blocks = blocks_on(last[code])
    assert f"Nächste Ferien in {name}" in blocks[code] and "noch keine weiteren" not in blocks[code]
    day = last[code] + timedelta(days=1)
    blocks = blocks_on(day)
    assert len(blocks) == 17  # 16 Länder und „none“
    assert f"Für {name} sind noch keine weiteren Ferien eingetragen." in blocks[code]
    assert "Wähle dein Bundesland" not in blocks[code] and "Nächste Ferien" not in blocks[code]
    assert "Wähle dein Bundesland" in blocks["none"]
    assert f"{name} için sıradaki tatil tarihleri henüz eklenmedi." in blocks_on(day, "tr")[code]


def test_customs_search_has_a_permanent_live_region(client):
    """Audit d1-customs-empty-not-announced: Trefferzahl und Leerzustand gehen über eine Live-Region,
    die von Anfang an im DOM steht (WCAG 4.1.3); die Texte bekommt app.js aus tv-strings."""
    for path, results in (("/de/zoll", "{n} Treffer"), ("/tr/gumruk", "{n} sonuç")):
        html = client.get(path).get_data(as_text=True)
        tools = re.search(r'<div class="toolbar" data-customs-tools hidden>.*?<div class="seg"', html, re.S).group(0)
        assert '<p class="sr-only" role="status" aria-live="polite" data-customs-live></p>' in tools
        strings = json.loads(re.search(r'id="tv-strings">(.*?)</script>', html).group(1))
        assert strings["c_results_n"] == results and strings["c_results_1"] and strings["c_no_results"]


def test_split_holidays_are_shown_as_separate_blocks(client):
    html = client.get("/de/ferien/ostern-2027?land=BW").get_data(as_text=True)
    assert "Do 25.03.2027 + Di 30.03.2027 – Sa 03.04.2027" in html
    assert "6 Ferientage" in html
    assert "Frei inkl. Wochenenden und bundesweiter Feiertage: Do 25.03.2027 – So 04.04.2027" in html


def test_unknown_pages_are_404(client):
    assert client.get("/de/grenze/atlantis").status_code == 404
    assert client.get("/api/v1/borders/atlantis").get_json() == {"error": "not_found"}


@pytest.mark.parametrize("path, code", [("/de/grenze/atlantis", 404), ("/tr/sinir/atlantis", 404), ("/gibts-nicht", 404)])
def test_error_pages_have_no_canonical_or_language_switch(client, path, code):
    """Review: Unter einem bekannten Endpunkt zeigten Canonical, og:url, hreflang und der
    Sprachwechsel auf eine URL, die es nicht gibt (/tr/sinir/atlantis → wieder 404)."""
    resp = client.get(path)
    html = resp.get_data(as_text=True)
    assert resp.status_code == code
    assert 'rel="canonical"' not in html and "hreflang" not in html and 'property="og:url"' not in html
    assert 'class="plate"' not in html


def test_service_worker_precaches_all_pages(client):
    resp = client.get("/sw.js")
    assert resp.status_code == 200 and resp.mimetype == "application/javascript"
    assert resp.headers["Cache-Control"] == "no-cache"
    body = resp.get_data(as_text=True)
    config = json.loads(body.split("self.TV_CONFIG = ", 1)[1].split(";\n", 1)[0])
    for path in ALL_PAGES:
        assert path in config["pages"]
    assert "/de/ferien/sommer-2027" in config["pages"]
    assert all("?v=" in a for a in config["assets"])
    for asset in config["assets"]:
        assert client.get(asset).status_code == 200


def test_build_id_changes_when_templates_change(tmp_path, monkeypatch):
    import tatilvakti
    (tmp_path / "templates").mkdir()
    page = tmp_path / "templates" / "page.html"
    page.write_text("<p>alt</p>")
    monkeypatch.setattr(tatilvakti, "PACKAGE_DIR", tmp_path)
    before = tatilvakti._content_fingerprint()
    page.write_text("<p>neu</p>")
    assert tatilvakti._content_fingerprint() != before


def test_static_assets_are_immutable(client):
    html = client.get("/de/").get_data(as_text=True)
    css = re.search(r'href="(/static/css/app\.css\?v=[0-9a-f]+)"', html).group(1)
    assert "immutable" in client.get(css).headers["Cache-Control"]


@pytest.mark.parametrize("lang", ["de", "tr"])
def test_manifest(client, lang):
    resp = client.get(f"/{lang}/manifest.webmanifest")
    assert resp.mimetype == "application/manifest+json"
    data = resp.get_json(force=True)
    assert data["start_url"] == f"/{lang}/" and data["display"] == "standalone"
    for icon in data["icons"]:
        assert client.get(icon["src"]).status_code == 200


def test_healthz_reports_data_freshness(client):
    data = client.get("/healthz").get_json()
    assert data["db"] is True and data["salt_db"] is True
    assert data["datasets"]["holidays"]["as_of"] == "2026-10-06"
    assert data["datasets"]["holidays"]["review_due"] is False
    assert data["due_items"] == [] and data["next_review"] > "2026-10-06"
    assert data["proxy"] == {"trust_proxy": 0, "forwarded_ignored": False}
    assert data["maintenance_at"] == "2026-10-06T10:00:00Z"
    # Test-App ohne Impressum: sichtbar als 'attention', HTTP bleibt 200
    assert data["imprint_ok"] is False
    assert (data["status"], data["attention"]) == ("attention", ["imprint"])


@pytest.fixture
def operated_app(tmp_path, clock):
    from tatilvakti import create_app
    return create_app({"TESTING": True, "TV_DB_PATH": str(tmp_path / "op.db"), "TV_CLOCK": clock,
                       "TV_OPERATOR_NAME": "Erika Muster", "TV_OPERATOR_ADDRESS": "Musterweg 1;12345 Musterstadt",
                       "TV_OPERATOR_EMAIL": "kontakt@example.org"})


def test_healthz_is_ok_with_imprint_and_fresh_data(operated_app):
    resp = operated_app.test_client().get("/healthz")
    data = resp.get_json()
    assert resp.status_code == 200
    assert (data["status"], data["imprint_ok"], data["attention"]) == ("ok", True, [])


def test_healthz_lists_due_rules_and_prices(operated_app):
    """Audit ops-7: Einzelregel und Preise fällig, Datensätze noch nicht – trotzdem 'attention'.

    Die Daten werden hier gezielt gesetzt, damit der Test nicht an den echten Prüfdaten hängt.
    """
    content = operated_app.extensions["tv"].content
    item = content.customs["items"][0]
    item["review_after"] = "2026-10-05"
    code = next(iter(content.transit["countries"]))
    content.transit["countries"][code]["prices_valid_until"] = "2026-10-05"
    resp = operated_app.test_client().get("/healthz")
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["status"] == "attention" and data["attention"] == ["due_items"]
    assert data["due_items"] == [
        {"dataset": "customs", "item": item["id"], "field": "review_after", "date": "2026-10-05"},
        {"dataset": "transit", "item": code, "field": "prices_valid_until", "date": "2026-10-05"},
    ]
    assert not any(v["review_due"] for v in data["datasets"].values())


def test_healthz_flags_whole_datasets_after_review_date(operated_app, clock):
    clock.now = datetime(2100, 1, 1, 9, 0, tzinfo=timezone.utc)
    data = operated_app.test_client().get("/healthz").get_json()
    assert data["status"] == "attention" and data["next_review"] is None
    whole = {d["dataset"] for d in data["due_items"] if d["item"] is None}
    assert whole == {"holidays", "customs", "transit", "crossings"}


def test_healthz_flags_an_unusable_salt_db(operated_app, tmp_path, caplog):
    """Review: Ohne Schlüssel-DB scheitert jede Meldung – /healthz muss das zeigen (HTTP bleibt 200)."""
    client = operated_app.test_client()
    blocker = tmp_path / "keine-verzeichnis"
    blocker.write_text("")  # wie /run/tatilvakti-v2 weg und ohne Recht, es neu anzulegen
    operated_app.config["TV_SALT_DB_PATH"] = str(blocker / "salts.db")
    operated_app.config["PROPAGATE_EXCEPTIONS"] = False  # wie im Betrieb: 500 statt Exception im Test
    with caplog.at_level("WARNING"):
        assert client.post("/api/v1/borders/kapikule/reports", json={"direction": "to_tr", "bucket": 1}).status_code == 500
        for _ in range(2):
            resp = client.get("/healthz")
            data = resp.get_json()
            assert resp.status_code == 200 and data["salt_db"] is False
            assert (data["status"], data["attention"]) == ("attention", ["salt_db"])
    assert len([r for r in caplog.records if "nicht nutzbar" in r.getMessage()]) == 1  # einmal, nicht je Abfrage
    operated_app.config["TV_SALT_DB_PATH"] = str(tmp_path / "wieder" / "salts.db")  # repariert
    data = client.get("/healthz").get_json()
    assert (data["status"], data["salt_db"]) == ("ok", True)


def test_forwarded_header_without_trusted_proxy_warns_once(client, caplog):
    with caplog.at_level("WARNING"):
        client.get("/de/", headers={"X-Forwarded-For": "203.0.113.9"})
        client.get("/de/", headers={"X-Forwarded-For": "203.0.113.10"})
    warnings = [r for r in caplog.records if "TV_TRUST_PROXY=0" in r.getMessage()]
    assert len(warnings) == 1 and "203.0.113" not in warnings[0].getMessage()  # keine IP im Log
    data = client.get("/healthz").get_json()
    assert data["proxy"]["forwarded_ignored"] is True and "proxy" in data["attention"]


def test_trusted_proxy_groups_ipv6_by_64(tmp_path, clock):
    from tatilvakti import create_app
    app = create_app({"TESTING": True, "TV_DB_PATH": str(tmp_path / "px.db"), "TV_CLOCK": clock, "TV_TRUST_PROXY": 1})
    client = app.test_client()
    url = "/api/v1/borders/kapikule/reports"
    body = {"direction": "to_tr", "bucket": 5}
    assert client.post(url, json=body, headers={"X-Forwarded-For": "2001:db8:1:2::1"}).status_code == 201
    assert client.post(url, json=body, headers={"X-Forwarded-For": "2001:db8:1:2::77"}).status_code == 429
    assert client.post(url, json=body, headers={"X-Forwarded-For": "2001:db8:1:3::1"}).status_code == 201
    assert client.get("/healthz").get_json()["proxy"] == {"trust_proxy": 1, "forwarded_ignored": False}
    # Derselbe Anschluss (/56) aus weiteren /64-Netzen: nach 3 Meldungen pro Stunde ist Schluss
    assert client.post(url, json=body, headers={"X-Forwarded-For": "2001:db8:1:4::1"}).status_code == 201
    over = client.post(url, json=body, headers={"X-Forwarded-For": "2001:db8:1:5::1"})
    assert over.status_code == 429 and over.get_json()["detail"] == "net_per_hour"
    assert client.post(url, json=body, headers={"X-Forwarded-For": "198.51.100.7"}).status_code == 201


def test_maintenance_runs_without_new_reports(client, db, clock):
    """Audit sec-3/legal-3/ops-6: nach 49 h ohne neue Meldung sind Prüfwert und alter Schlüssel weg."""
    from tatilvakti.db import salt_db
    assert client.post("/api/v1/borders/kapikule/reports", json={"direction": "to_tr", "bucket": 2}).status_code == 201
    assert db.execute("SELECT COUNT(*) FROM reports WHERE client IS NOT NULL").fetchone()[0] == 1
    clock.advance(hours=49)
    assert client.get("/de/").status_code == 200  # nur ein Seitenaufruf
    assert db.execute("SELECT COUNT(*) FROM reports WHERE client IS NOT NULL").fetchone()[0] == 0
    with salt_db(db) as sconn:
        assert [r["day"] for r in sconn.execute("SELECT day FROM salts")] == []
    assert db.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 1  # die Meldung selbst bleibt


def test_maintenance_in_requests_is_throttled(client, db, clock):
    client.get("/de/")
    first = db.execute("SELECT value FROM kv WHERE key = 'maintenance_at'").fetchone()[0]
    clock.advance(minutes=9)
    client.get("/de/")
    assert db.execute("SELECT value FROM kv WHERE key = 'maintenance_at'").fetchone()[0] == first
    clock.advance(minutes=1)
    client.get("/static/css/app.css")  # statische Dateien lösen nichts aus
    assert db.execute("SELECT value FROM kv WHERE key = 'maintenance_at'").fetchone()[0] == first
    client.get("/de/")
    assert db.execute("SELECT value FROM kv WHERE key = 'maintenance_at'").fetchone()[0] == str(clock.ts)


def test_hsts_only_for_https(client, tmp_path, clock):
    from tatilvakti import create_app
    assert client.get("/de/").headers["Strict-Transport-Security"] == "max-age=31536000"
    plain = create_app({"TESTING": True, "TV_DB_PATH": str(tmp_path / "http.db"), "TV_CLOCK": clock}).test_client()
    assert "Strict-Transport-Security" not in plain.get("/de/").headers
    secure = plain.get("/de/", base_url="https://localhost")
    assert secure.headers["Strict-Transport-Security"] == "max-age=31536000"


@pytest.mark.parametrize("body", [
    '{"direction": "to_tr", "bucket": 1e400}',
    '{"direction": "to_tr", "bucket": Infinity}',
    '{"direction": "to_tr", "bucket": 2.0}',
    '{"direction": "to_tr", "bucket": true}',
    '{"direction": "to_tr", "bucket": 1, "observed_at": 1e400}',
    '{"direction": "to_tr", "bucket": 1, "observed_at": Infinity}',
    '{"direction": "to_tr", "bucket": 1, "observed_at": 1791194400.5}',
])
def test_report_api_rejects_non_integers_without_crashing(client, body):
    resp = client.post("/api/v1/borders/kapikule/reports", data=body, content_type="application/json")
    assert resp.status_code == 400 and resp.get_json()["error"] == "invalid"


def test_api_errors_are_always_json(client):
    resp = client.get("/api/v1/borders/kapikule/reports")
    assert resp.status_code == 405 and resp.get_json() == {"error": "method_not_allowed"}
    assert "POST" in resp.headers["Allow"]
    resp = client.post("/api/v1/borders/kapikule/reports", data="x" * 20000, content_type="application/json")
    assert resp.status_code == 413 and resp.get_json() == {"error": "too_large"}
    resp = client.delete("/api/v1/borders")
    assert resp.status_code == 405 and resp.mimetype == "application/json"
    # Seiten außerhalb der API behalten die normale Fehlerseite
    assert client.post("/de/").mimetype == "text/html"


def _title(html):
    return htmllib.unescape(re.search(r"<title>(.*?)</title>", html).group(1))


@pytest.mark.parametrize("lang, path, title", [
    ("de", "/de/grenze/kapikule/report", "Anfrage nicht möglich – tatilvakti"),
    ("tr", "/tr/sinir/kapikule/report", "İstek işlenemedi – tatilvakti"),
])
def test_method_not_allowed_on_pages_shows_own_error_page(client, lang, path, title):
    """405 auf Seitenpfaden (Audit d1-http-error-default-page): eigene Fehlerseite in der Sprache des
    Pfads, mit Navigation und Weg zur Startseite, statt der englischen Standardseite von Werkzeug."""
    resp = client.get(path)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 405 and resp.mimetype == "text/html"
    assert "POST" in resp.headers["Allow"]  # bleibt erhalten
    assert f'<html lang="{lang}"' in html and _title(html) == title
    assert 'class="tabbar"' in html and f'class="btn btn--primary" href="/{lang}/"' in html
    assert "Method Not Allowed" not in html
    assert "unsafe-inline" not in resp.headers["Content-Security-Policy"] and "Set-Cookie" not in resp.headers
    assert "Vary" not in resp.headers  # Sprache aus dem Pfad, nicht aus Accept-Language


def test_too_large_form_post_shows_own_error_page(client):
    resp = client.post("/de/grenze/kapikule/report", headers=SAME_ORIGIN,
                       data={"direction": "to_tr", "bucket": "1", "website": "x" * 20000})
    html = resp.get_data(as_text=True)
    assert resp.status_code == 413 and resp.mimetype == "text/html"
    assert '<html lang="de"' in html and _title(html) == "Anfrage nicht möglich – tatilvakti"
    assert "Request Entity Too Large" not in html


@pytest.mark.parametrize("path, title, text", [
    ("/de/info", "Anfrage nicht möglich – tatilvakti", "Diese Anfrage konnten wir nicht verarbeiten."),
    ("/tr/bilgi", "İstek işlenemedi – tatilvakti", "Bu isteği işleyemedik."),
])
def test_bad_request_on_pages_shows_own_error_page_and_keeps_headers(app, client, path, title, text):
    from werkzeug.exceptions import TooManyRequests, abort
    endpoint = "info_" + path.split("/")[1]
    app.view_functions[endpoint] = lambda **_: abort(400)
    resp = client.get(path)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 400 and _title(html) == title and text in html

    def limited(**_):
        raise TooManyRequests(retry_after=60)
    app.view_functions[endpoint] = limited
    resp = client.get(path)
    assert resp.status_code == 429 and _title(resp.get_data(as_text=True)) == title
    assert resp.headers["Retry-After"] == "60"  # Kopfzeilen der Ausnahme bleiben, nicht nur Allow


def test_error_page_outside_language_paths_follows_accept_language(client):
    resp = client.post("/healthz", headers={"Accept-Language": "tr-TR,tr;q=0.9,de;q=0.5"})
    html = resp.get_data(as_text=True)
    assert resp.status_code == 405 and "GET" in resp.headers["Allow"]
    assert '<html lang="tr"' in html and _title(html) == "İstek işlenemedi – tatilvakti"
    assert resp.headers["Vary"] == "Accept-Language"  # Caches dürfen DE und TR nicht mischen
    assert '<html lang="de"' in client.post("/healthz").get_data(as_text=True)
    # 404 ebenso
    missing = client.get("/gibts-nicht", headers={"Accept-Language": "tr"})
    assert '<html lang="tr"' in missing.get_data(as_text=True) and missing.headers["Vary"] == "Accept-Language"


def test_outdated_data_is_flagged_not_hidden(client, clock):
    clock.now = datetime(2028, 1, 15, 12, 0, tzinfo=timezone.utc)
    assert client.get("/healthz").get_json()["datasets"]["customs"]["review_due"] is True
    html = client.get("/de/zoll").get_data(as_text=True)
    assert "Prüfung fällig" in html
    route = client.get("/de/route").get_data(as_text=True)
    assert "vermutlich veraltet" in route


def test_sitemap_lists_both_languages(app, client):
    xml = client.get("/sitemap.xml").get_data(as_text=True)
    assert "https://tatilvakti.example/tr/sinir/kapikule" in xml
    assert "de-DE" not in xml and "tr-TR" not in xml
    urls = re.findall(r"<url>(.*?)</url>", xml.replace("\n", ""))
    locs = [re.search(r"<loc>(.*?)</loc>", u).group(1) for u in urls]
    assert len(locs) == len(set(locs))
    # Alle Ferienzeiträume beider Sprachen, je mit de, tr und x-default
    for period in _periods(app):
        de = f"https://tatilvakti.example/de/ferien/{period.slug['de']}"
        tr = f"https://tatilvakti.example/tr/tatil/{period.slug['tr']}"
        for loc in (de, tr):
            entry = urls[locs.index(loc)]
            assert f'hreflang="de" href="{de}"' in entry and f'hreflang="tr" href="{tr}"' in entry
            assert f'hreflang="x-default" href="{de}"' in entry
    assert not any("offline" in loc or "cevrimdisi" in loc or "?" in loc for loc in locs)
    # Jede Seite der Sitemap gibt es wirklich, und ihr Canonical ist genau diese URL
    for loc in locs:
        resp = client.get(loc.replace("https://tatilvakti.example", ""))
        assert resp.status_code == 200, loc
        assert f'<link rel="canonical" href="{loc}">' in resp.get_data(as_text=True), loc
