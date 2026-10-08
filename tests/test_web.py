"""Seiten, API, PWA und Datenschutz-Garantien über HTTP."""
import html as htmllib
import json
import re
from datetime import datetime, timezone

import pytest

from tatilvakti import borders as B

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
@pytest.mark.parametrize("cap", ["CROSSING_CAP", "BLOCK_CAP"])
def test_full_crossing_gets_its_own_message(client, monkeypatch, lang, path, text, cap):
    """Obergrenze voll: nicht „Du hast hier gerade schon gemeldet“, das stimmt für Erstmelder nicht.
    Ebenso, wenn das eigene Netz (/48) seinen Anteil an der Obergrenze ausgeschöpft hat."""
    monkeypatch.setattr(B, cap, 0)
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
    resp = client.get("/healthz")
    data = resp.get_json()
    assert resp.headers["Cache-Control"] == "no-store"  # nie aus einem Cache (Audit d4-healthz-…)
    assert data["db"] is True and data["salt_db"] is True and data["down"] == []
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


def test_healthz_is_503_without_a_usable_salt_db(operated_app, tmp_path, caplog):
    """Audit d4-healthz-200-bei-ausfall: Ohne Schlüssel-DB scheitert jede Meldung. Ein Monitor, der
    nur den Statuscode prüft, muss das sehen: 503, status 'down', Grund in 'down' (nicht attention)."""
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
            assert resp.status_code == 503 and resp.headers["Cache-Control"] == "no-store"
            assert (data["status"], data["down"], data["attention"]) == ("down", ["salt_db"], [])
            assert data["salt_db"] is False and data["db"] is True
    assert len([r for r in caplog.records if "nicht nutzbar" in r.getMessage()]) == 1  # einmal, nicht je Abfrage
    operated_app.config["TV_SALT_DB_PATH"] = str(tmp_path / "wieder" / "salts.db")  # repariert
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert (resp.get_json()["status"], resp.get_json()["down"]) == ("ok", [])


def test_healthz_keeps_maintenance_hints_at_200(operated_app, clock):
    """Pflegehinweise (fällige Prüfung, Impressum, Proxy, Wartung) sind kein Ausfall: HTTP 200,
    status 'attention', 'down' leer. Ein Statuscode-Monitor bleibt grün."""
    operated_app.config["TV_OPERATOR_EMAIL"] = ""
    client = operated_app.test_client()
    client.get("/de/", headers={"X-Forwarded-For": "203.0.113.9"})  # TV_TRUST_PROXY=0: Proxy-Hinweis
    clock.now = datetime(2100, 1, 1, 9, 0, tzinfo=timezone.utc)  # alle Daten zur Prüfung fällig
    resp = client.get("/healthz")
    data = resp.get_json()
    assert resp.status_code == 200 and data["down"] == []
    assert data["status"] == "attention" and data["attention"] == ["due_items", "imprint", "proxy"]


@pytest.fixture
def quick_lock(monkeypatch):
    """Kurze Wartezeit auf die Schreibsperre, damit Tests mit gehaltener Sperre schnell sind."""
    import tatilvakti.db
    monkeypatch.setattr(tatilvakti.db, "BUSY_TIMEOUT_MS", 50)


def test_healthz_is_503_while_another_process_holds_the_write_lock(operated_app, quick_lock, caplog):
    """Audit-Probe (b): Eine zweite Verbindung hält BEGIN IMMEDIATE. Jede Meldung scheitert dann
    mit „database is locked“; früher blieb /healthz bei 200 und status ok."""
    from tatilvakti.db import connect
    client = operated_app.test_client()
    assert client.get("/healthz").status_code == 200  # Wartung dieses Prozesses ist erledigt
    other = connect(operated_app.config["TV_DB_PATH"])
    try:
        other.execute("BEGIN IMMEDIATE")
        operated_app.config["PROPAGATE_EXCEPTIONS"] = False
        assert client.post("/api/v1/borders/kapikule/reports", json={"direction": "to_tr", "bucket": 1}).status_code == 500
        with caplog.at_level("WARNING"):
            resp = client.get("/healthz")
        data = resp.get_json()
        assert resp.status_code == 503 and (data["status"], data["down"], data["db"]) == ("down", ["db"], False)
        assert any("nimmt keine Meldungen an: database is locked" in r.getMessage() for r in caplog.records)
    finally:
        other.execute("ROLLBACK")
        other.close()
    assert client.get("/healthz").status_code == 200


def test_healthz_is_503_when_the_db_is_read_only(operated_app, monkeypatch):
    """Schreibgeschützte DB (Rechte, -wal/-shm von root, read-only eingehängt): Lesen klappt, die
    Seiten laufen, aber keine Meldung lässt sich speichern. BEGIN IMMEDIATE allein merkt das nicht."""
    import sqlite3
    from tatilvakti import views
    from tatilvakti.db import Connection
    client = operated_app.test_client()
    assert client.get("/healthz").status_code == 200
    path = operated_app.config["TV_DB_PATH"]
    readonly = sqlite3.connect(f"file:{path}?mode=ro", uri=True, isolation_level=None, factory=Connection)
    readonly.row_factory = sqlite3.Row
    readonly.salt_path = operated_app.config["TV_SALT_DB_PATH"]
    monkeypatch.setattr(views, "_db", lambda: readonly)
    try:
        resp = client.get("/healthz")
        assert resp.status_code == 503 and resp.get_json()["down"] == ["db"]
        assert resp.get_json()["maintenance_at"] is not None  # lesen ging
    finally:
        readonly.close()


def test_healthz_is_503_when_writing_fails(operated_app, clock, monkeypatch):
    """Volle Platte oder E/A-Fehler zeigen sich erst beim Schreiben. /healthz stößt dafür die Wartung
    an, die höchstens alle MAINTENANCE_EVERY_S Sekunden schreibt, und versucht es bis zum Erfolg."""
    import sqlite3
    client = operated_app.test_client()
    assert client.get("/healthz").status_code == 200
    real = B.maintenance

    def full(conn, now, force=False):
        raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(B, "maintenance", full)
    clock.advance(seconds=B.MAINTENANCE_EVERY_S)
    for _ in range(2):  # bleibt rot, solange Schreiben scheitert
        resp = client.get("/healthz")
        assert resp.status_code == 503 and resp.get_json()["down"] == ["db"]
    monkeypatch.setattr(B, "maintenance", real)
    assert client.get("/healthz").status_code == 200  # wieder schreibbar: sofort grün


def test_healthz_does_not_write_on_every_call(operated_app, clock):
    """Billig: Zwischen den Wartungsläufen schreibt /healthz nichts (data_version ändert sich nur,
    wenn eine andere Verbindung etwas festschreibt)."""
    from tatilvakti.db import connect
    client = operated_app.test_client()
    client.get("/healthz")
    db_op = connect(operated_app.config["TV_DB_PATH"])
    try:
        before = db_op.execute("PRAGMA data_version").fetchone()[0]
        for _ in range(3):
            clock.advance(seconds=30)
            assert client.get("/healthz").status_code == 200
        assert db_op.execute("PRAGMA data_version").fetchone()[0] == before
        clock.advance(seconds=B.MAINTENANCE_EVERY_S)  # jetzt ist die Wartung fällig und schreibt
        assert client.get("/healthz").status_code == 200
        assert db_op.execute("PRAGMA data_version").fetchone()[0] != before
    finally:
        db_op.close()



def test_healthz_salt_error_during_maintenance_is_not_a_db_failure(operated_app, tmp_path, clock):
    """Läuft die Wartung im /healthz-Abruf selbst und scheitert nur an der Schlüssel-DB, ist die
    Haupt-DB trotzdem in Ordnung: down nennt nur salt_db."""
    client = operated_app.test_client()
    assert client.get("/healthz").status_code == 200
    blocker = tmp_path / "keine-verzeichnis"
    blocker.write_text("")
    operated_app.config["TV_SALT_DB_PATH"] = str(blocker / "salts.db")
    clock.advance(seconds=B.MAINTENANCE_EVERY_S)
    # Die Wartung im before_request dieses Prozesses ist gerade gelaufen: Erst /healthz schreibt
    operated_app.extensions["tv"].runtime["maintenance_checked_at"] = clock.ts
    resp = client.get("/healthz")
    assert resp.status_code == 503 and resp.get_json()["down"] == ["salt_db"]
    assert resp.get_json()["maintenance_at"] == iso_z(clock.ts)


def iso_z(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_healthz_flags_a_full_crossing_cap_for_a_day(operated_app, clock):
    """Audit d3-ratelimit-crossing-cap-48, Erkennung: Ist eine Obergrenze (oder der Anteil eines /48)
    erreicht, zeigt /healthz einen Tag lang den Pflegehinweis 'crossing_cap' mit Zeitpunkt. Kein
    Ausfall: HTTP 200, damit der Uptime-Monitor nicht anschlägt; die tägliche Prüfung auf
    "status":"ok" sieht es."""
    from tatilvakti.db import connect
    client = operated_app.test_client()
    data = client.get("/healthz").get_json()
    assert (data["status"], data["crossing_cap_at"]) == ("ok", None)
    conn = connect(operated_app.config["TV_DB_PATH"])
    try:
        for i in range(B.CROSSING_CAP):
            B.add_report(conn, "kapikule", "to_tr", 1, f"198.51.100.{i + 1}", clock.ts)
        with pytest.raises(B.CrossingBusy):
            B.add_report(conn, "kapikule", "to_tr", 1, "198.51.100.99", clock.ts)
    finally:
        conn.close()
    at = clock.ts
    clock.advance(hours=23)
    resp = client.get("/healthz")
    data = resp.get_json()
    assert resp.status_code == 200 and data["down"] == []
    assert (data["status"], data["attention"], data["crossing_cap_at"]) == ("attention", ["crossing_cap"], iso_z(at))
    # Von außen nur der Status, nicht der Grund
    public = client.get("/healthz", environ_base={"REMOTE_ADDR": "198.51.100.7"}).get_json()
    assert public == {"status": "attention", "build": data["build"], "down": []}
    clock.advance(hours=1)
    data = client.get("/healthz").get_json()
    assert (data["status"], data["attention"], data["crossing_cap_at"]) == ("ok", [], iso_z(at))


@pytest.mark.parametrize("trust_proxy, remote, headers", [
    (1, "127.0.0.1", {"X-Forwarded-For": "203.0.113.9"}),   # Pangolin, richtig eingestellt
    (0, "127.0.0.1", {"X-Forwarded-For": "203.0.113.9"}),   # Pangolin über den Tunnel, TV_TRUST_PROXY fehlt
    (0, "127.0.0.1", {"Forwarded": "for=203.0.113.9"}),
    (0, "127.0.0.1", {"X-Real-IP": "203.0.113.9"}),
    (0, "198.51.100.7", {}),                                  # Port direkt erreichbar
], ids=["proxy", "tunnel-ohne-trust", "forwarded", "x-real-ip", "direkt"])
def test_healthz_shows_details_only_on_the_server(tmp_path, clock, trust_proxy, remote, headers):
    """Audit d2-healthz-internals / d4-healthz-details-oeffentlich: Von außen nur status, build und
    down mit demselben Statuscode. Proxy-Einstellung, Zustand des Spam-Schutzes, Pflegehinweise und
    Prüfdaten sieht nur, wer auf dem Server selbst fragt (deploy.sh, curl auf 127.0.0.1:3096)."""
    from tatilvakti import create_app
    app = create_app({"TESTING": True, "TV_DB_PATH": str(tmp_path / "hz.db"), "TV_CLOCK": clock,
                      "TV_TRUST_PROXY": trust_proxy})
    client = app.test_client()
    full = client.get("/healthz").get_json()  # Testclient: 127.0.0.1 ohne Proxy-Header
    assert {"proxy", "salt_db", "due_items", "attention", "imprint_ok"} <= full.keys()
    resp = client.get("/healthz", headers=headers, environ_base={"REMOTE_ADDR": remote})
    assert resp.status_code == 200 and resp.headers["Cache-Control"] == "no-store"
    assert resp.get_json() == {"status": full["status"], "build": full["build"], "down": []}
    # Ausfall: von außen derselbe Statuscode und der Grund
    blocker = tmp_path / "keine-verzeichnis"
    blocker.write_text("")
    app.config["TV_SALT_DB_PATH"] = str(blocker / "salts.db")
    resp = client.get("/healthz", headers=headers, environ_base={"REMOTE_ADDR": remote})
    assert resp.status_code == 503
    assert resp.get_json() == {"status": "down", "build": full["build"], "down": ["salt_db"]}


@pytest.mark.parametrize("remote", ["127.0.0.1", "::1", "::ffff:127.0.0.1"])
def test_healthz_details_for_every_loopback_address(client, remote):
    data = client.get("/healthz", environ_base={"REMOTE_ADDR": remote}).get_json()
    assert "proxy" in data and "salt_db" in data


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


def test_trusted_proxy_limits_one_48_to_its_share(tmp_path, clock):
    from tatilvakti import create_app
    app = create_app({"TESTING": True, "TV_DB_PATH": str(tmp_path / "px.db"), "TV_CLOCK": clock, "TV_TRUST_PROXY": 1})
    client = app.test_client()
    url = "/api/v1/borders/kapikule/reports"
    body = {"direction": "to_tr", "bucket": 5}
    for i in range(B.BLOCK_CAP):  # je ein anderes /56 im selben /48
        assert client.post(url, json=body, headers={"X-Forwarded-For": f"2001:db8:7:{i:02x}00::1"}).status_code == 201
    over = client.post(url, json=body, headers={"X-Forwarded-For": "2001:db8:7:ff00::1"})
    assert over.status_code == 429 and over.get_json()["detail"] == "crossing_busy"
    assert client.post(url, json=body, headers={"X-Forwarded-For": "2001:db8:8::1"}).status_code == 201


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
