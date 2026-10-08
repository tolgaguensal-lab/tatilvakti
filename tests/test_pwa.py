"""PWA und Ablösung der Alt-App: Service-Worker-Konfiguration, Kill-Switch, alte URLs, Startbildschirm-Hinweis."""
import json
import re

import pytest

import tatilvakti
from tatilvakti import create_app
from tatilvakti.content import load_content, redirect_route_problems, validate, validate_redirects

SW_JS = tatilvakti.STATIC_DIR / "js" / "sw.js"


def sw_config(client) -> dict:
    body = client.get("/sw.js").get_data(as_text=True)
    return json.loads(body.split("self.TV_CONFIG = ", 1)[1].split(";\n", 1)[0])


def make_app(tmp_path, clock, **config):
    return create_app({"TESTING": True, "TV_DB_PATH": str(tmp_path / "pwa.db"), "TV_CLOCK": clock, **config})


# ------------------------------------------------------------------ Service Worker

def test_sw_config_names_required_pages_and_own_cache_prefix(client):
    config = sw_config(client)
    assert config["cachePrefix"] == "tv2-"
    assert config["home"] == {"de": "/de/", "tr": "/tr/"}
    assert config["offline"] == {"de": "/de/offline", "tr": "/tr/cevrimdisi"}
    for url in [*config["home"].values(), *config["offline"].values()]:
        assert url in config["pages"]
        assert client.get(url).status_code == 200


def test_sw_install_is_a_transaction_and_never_ends_empty():
    """Die Regeln aus sw-1 im Quelltext festhalten; das Verhalten prüft tests/e2e/pwa.js im Browser."""
    source = SW_JS.read_text(encoding="utf-8")
    assert '"tv-' not in source  # keine Cache-Namen der Alt-App-Generation mehr
    assert "staticCache.addAll(C.assets)" in source
    assert "values(C.home).concat(offline)" in source  # Pflichtseiten
    assert "adoptMissingPages()" in source  # Übernahme aus dem alten Seiten-Cache vor dem Löschen
    assert "staticCache.put(cacheKey(url)" in source  # Offline-Seiten auch im STATIC-Cache
    assert 'new Response("Offline"' not in source  # letzter Fallback ist eine Seite, kein nackter Text
    # …zweisprachig mit Sprachangabe und dem Wort, das die TR-Ansicht überall nutzt (prod-5)
    assert '<html lang="de">' in source and '<span lang="tr">İnternet yok</span>' in source
    assert "evrimdış" not in source


# ------------------------------------------------------------------ Kill-Switch

def test_legacy_sw_paths_are_off_by_default(client):
    assert client.get("/service-worker.js").status_code == 404


@pytest.mark.parametrize("value", ["/service-worker.js, /app/sw.js", ["/service-worker.js", "/app/sw.js"]])
def test_kill_switch_is_served_under_every_legacy_path(tmp_path, clock, value):
    client = make_app(tmp_path, clock, TV_LEGACY_SW_PATHS=value).test_client()
    for path in ("/service-worker.js", "/app/sw.js"):
        for resp in (client.get(path), client.head(path)):
            assert resp.status_code == 200
            assert resp.mimetype == "application/javascript"
            assert resp.headers["Cache-Control"] == "no-store"
            assert resp.headers["X-Content-Type-Options"] == "nosniff"
            assert resp.headers["Service-Worker-Allowed"] == "/"
            assert "Set-Cookie" not in resp.headers
    assert client.get("/sw.js").get_data(as_text=True).startswith("self.TV_CONFIG")  # eigener SW unverändert


def test_kill_switch_cleans_up_unregisters_and_reloads(tmp_path, clock):
    client = make_app(tmp_path, clock, TV_LEGACY_SW_PATHS="/service-worker.js").test_client()
    js = client.get("/service-worker.js").get_data(as_text=True)
    assert "{{" not in js and "{%" not in js
    assert 'var KEEP = "tv2-";' in js
    assert "self.skipWaiting()" in js
    assert "n.indexOf(KEEP) !== 0" in js and "caches.delete(n)" in js  # alles außer tv2-* löschen
    assert "self.registration.unregister()" in js
    assert 'self.clients.matchAll({ type: "window" })' in js and "client.navigate(client.url)" in js
    # Kein push- und kein fetch-Handler: alte Abos enden, Anfragen gehen direkt ins Netz
    handlers = re.findall(r'addEventListener\("(\w+)"', js)
    assert sorted(handlers) == ["activate", "install"]
    # Reihenfolge: aufräumen, übernehmen, abmelden, neu laden
    order = [js.index(s) for s in ("caches.keys()", "clients.claim()", "registration.unregister()", "client.navigate(")]
    assert order == sorted(order)


def test_kill_switch_reads_the_environment(tmp_path, clock, monkeypatch):
    monkeypatch.setenv("TV_LEGACY_SW_PATHS", " /service-worker.js ,, /js/old-sw.js ")
    app = create_app({"TESTING": True, "TV_DB_PATH": str(tmp_path / "env.db"), "TV_CLOCK": clock})
    client = app.test_client()
    assert client.get("/service-worker.js").status_code == 200
    assert client.get("/js/old-sw.js").status_code == 200


@pytest.mark.parametrize("value, needle", [
    ("service-worker.js", "muss mit / beginnen"),
    ("/service-worker", "muss auf .js enden"),
    ("/sw.js?v=2", "ohne Query"),
    ("/sw.js#x", "ohne Query"),
    ("/sw.js", "kollidiert"),
    ("/static/js/sw.js", "kollidiert"),
    ("/de/grenze/sw.js", "kollidiert"),
    ("/api/v1/borders/sw.js", "kollidiert"),
    ("/a.js,/a.js", "doppelt"),
    ("/old sw.js", "unzulässige Zeichen"),
    ("/<path:x>.js", "unzulässige Zeichen"),
    ("/../sw-old.js", "unzulässige Zeichen"),
    ("//evil.example/sw.js", "unzulässige Zeichen"),
])
def test_invalid_legacy_sw_paths_stop_the_app(tmp_path, clock, value, needle):
    with pytest.raises(RuntimeError) as err:
        make_app(tmp_path, clock, TV_LEGACY_SW_PATHS=value)
    assert "TV_LEGACY_SW_PATHS" in str(err.value) and needle in str(err.value)


# ------------------------------------------------------------------ alte URLs (data/redirects.json)

def with_redirects(monkeypatch, entries):
    """App-Fabrik mit eigener Weiterleitungstabelle statt data/redirects.json."""
    def fake_load():
        content = load_content()
        content.set_redirects({"meta": {"as_of": "2026-10-07"}, "redirects": entries})
        return content
    monkeypatch.setattr(tatilvakti, "load_content", fake_load)


OLD_URLS = [
    {"from": "/grenzen", "to": "/de/grenze", "code": 301, "note": "Search Console"},
    {"from": "/datenschutz/", "to": "/de/info#datenschutz", "code": 301},
    {"from": "/tr/eski-sayfa", "to": "/tr/sinir?x=1", "code": 301},
    {"from": "/go/vignette-at", "to": None, "code": 410},
    {"from": "/api/ki", "to": None, "code": 410},
    {"from": "/api/borders", "to": "/api/v1/borders", "code": 301},
]


@pytest.fixture
def legacy_client(tmp_path, clock, monkeypatch):
    with_redirects(monkeypatch, OLD_URLS)
    return make_app(tmp_path, clock).test_client()


def test_shipped_redirect_table_is_valid_and_empty():
    content = load_content()
    assert validate_redirects(content.redirects) == []
    assert content.redirects["redirects"] == [] and content.redirects["meta"]["as_of"]


def test_old_urls_redirect_permanently(legacy_client):
    for path, target in [("/grenzen", "/de/grenze"), ("/grenzen/", "/de/grenze"),
                         ("/datenschutz", "/de/info#datenschutz"), ("/datenschutz/", "/de/info#datenschutz"),
                         ("/tr/eski-sayfa", "/tr/sinir?x=1"), ("/api/borders", "/api/v1/borders")]:
        resp = legacy_client.get(path)
        assert resp.status_code == 301, path
        assert resp.headers["Location"] == target
        assert legacy_client.head(path).status_code == 301
    assert legacy_client.get("/grenzen", follow_redirects=True).status_code == 200


@pytest.mark.parametrize("accept, lang, title", [
    (None, "de", "Diese Seite gibt es nicht mehr"),
    ("tr-TR,tr;q=0.9,de;q=0.5", "tr", "Bu sayfa artık yok"),
])
def test_removed_urls_answer_410_with_the_normal_error_page(legacy_client, accept, lang, title):
    resp = legacy_client.get("/go/vignette-at", headers={"Accept-Language": accept} if accept else {})
    assert resp.status_code == 410
    html = resp.get_data(as_text=True)
    assert f'<html lang="{lang}"' in html and title in html
    assert f'href="/{lang}/"' in html  # Weg zur Startseite
    assert "Accept-Language" in resp.headers["Vary"]
    assert "Set-Cookie" not in resp.headers


def test_removed_api_urls_answer_410_as_json(legacy_client):
    resp = legacy_client.get("/api/ki")
    assert resp.status_code == 410 and resp.get_json() == {"error": "gone"}


def test_redirects_only_apply_where_v2_would_answer_404(legacy_client):
    assert legacy_client.get("/de/grenze").status_code == 200
    assert legacy_client.get("/unbekannt").status_code == 404
    assert legacy_client.get("/grenzen/kapikule").status_code == 404  # kein Präfix-Treffer


def test_redirect_table_does_not_change_the_build_id(tmp_path, clock, monkeypatch):
    before = make_app(tmp_path, clock).extensions["tv"].build_id
    with_redirects(monkeypatch, OLD_URLS)
    assert make_app(tmp_path, clock).extensions["tv"].build_id == before
    assert "data/redirects.json" in tatilvakti.FINGERPRINT_SKIP


@pytest.mark.parametrize("entry, needle", [
    ({"from": "grenzen", "to": "/de/grenze", "code": 301}, "from muss ein Pfad sein"),
    ({"from": "//evil.example", "to": "/de/", "code": 301}, "from muss ein Pfad sein"),
    ({"from": "/a?b=1", "to": "/de/", "code": 301}, "from muss ein Pfad sein"),
    ({"from": "/alt seite", "to": "/de/", "code": 301}, "from muss ein Pfad sein"),
    ({"from": "/", "to": "/de/", "code": 301}, "Startseite"),
    ({"from": "/api/v1/alt", "to": "/de/", "code": 301}, "gehören v2"),
    ({"from": "/static/alt.css", "to": None, "code": 410}, "gehören v2"),
    ({"from": "/alt", "to": "https://evil.example/", "code": 301}, "interner Pfad"),
    ({"from": "/alt", "to": "//evil.example/", "code": 301}, "interner Pfad"),
    ({"from": "/alt", "to": "/\\evil.example", "code": 301}, "interner Pfad"),
    ({"from": "/alt", "to": None, "code": 301}, "interner Pfad"),
    ({"from": "/alt", "to": "/de/", "code": 302}, "301 oder 410"),
    ({"from": "/alt", "to": "/de/", "code": "301"}, "301 oder 410"),
    ({"from": "/alt", "to": "/de/", "code": True}, "301 oder 410"),
    ({"from": "/alt", "to": "/de/", "code": 410}, "kein Ziel"),
    ({"from": "/alt", "to": "/de/", "code": 301, "form": "/x"}, "unbekannte Felder"),
    ({"from": "/alt", "to": "/de/", "code": 301, "note": 5}, "note muss Text sein"),
])
def test_redirect_validation_rejects(entry, needle):
    problems = validate_redirects({"meta": {"as_of": "2026-10-07"}, "redirects": [entry]})
    assert any(needle in p for p in problems), problems


def test_redirect_validation_rejects_duplicates_chains_and_missing_meta():
    entries = [{"from": "/a", "to": "/b", "code": 301}, {"from": "/a/", "to": None, "code": 410},
               {"from": "/b", "to": "/de/", "code": 301}]
    problems = validate_redirects({"meta": {}, "redirects": entries})
    assert any("doppelt" in p for p in problems)
    assert any("Kette" in p for p in problems)
    assert any("redirects.meta.as_of" in p for p in problems)
    assert validate_redirects({"redirects": {}}) == ["redirects: Objekt mit meta und Liste redirects erwartet"]


def test_data_validation_includes_the_redirect_table():
    content = load_content()
    content.set_redirects({"meta": {"as_of": "2026-10-07"}, "redirects": [{"from": "x", "to": None, "code": 410}]})
    assert any(p.startswith("redirects") for p in validate(content))


@pytest.mark.parametrize("entry, needle", [
    ({"from": "/de/ferien", "to": "/de/", "code": 301}, "eigene Route"),
    ({"from": "/de/ferien/", "to": "/de/", "code": 301}, "eigene Route"),
    ({"from": "/sw.js", "to": None, "code": 410}, "eigene Route"),
    ({"from": "/de/grenze/kapikule", "to": "/de/grenze", "code": 301}, "eigene Route"),
    ({"from": "/de/grenze/kapikule/report", "to": None, "code": 410}, "liefert 405 statt 404"),  # nur POST
    ({"from": "/de/ferien/sommer-2026", "to": "/de/ferien", "code": 301}, "liefert 302 statt 404"),  # macht v2 selbst
    ({"from": "/alt", "to": "/de/gibts-nicht", "code": 301}, "keine Seite von v2"),
    # Review: Ziele mit Platzhalter wurden nur am Muster geprüft
    ({"from": "/alt", "to": "/de/grenze/kapikul", "code": 301}, "liefert 404 statt 200"),
    ({"from": "/alt", "to": "/de/grenze/kapikule/report", "code": 301}, "liefert 405 statt 200"),
    ({"from": "/alt", "to": "/de", "code": 301}, "liefert 308 statt 200"),
    ({"from": "/alt", "to": "/de/ferien?zeitraum=sommer-2027", "code": 301}, "liefert 301 statt 200"),
])
def test_redirects_must_not_shadow_own_routes(tmp_path, clock, monkeypatch, entry, needle):
    with_redirects(monkeypatch, [entry])
    with pytest.raises(RuntimeError) as err:
        make_app(tmp_path, clock)
    assert "redirects.json" in str(err.value) and needle in str(err.value)


def test_old_urls_under_routes_with_placeholders_and_encoded_paths_work(tmp_path, clock, monkeypatch):
    """Review: /de/grenze/<alter-übergang> galt als eigene Route, obwohl v2 dort 404 liefert;
    prozentkodierte Pfade aus Search Console oder Proxy-Log griffen nie."""
    with_redirects(monkeypatch, [{"from": "/de/grenze/kapitan-andreevo", "to": "/de/grenze", "code": 301},
                                 {"from": "/%C3%BCber-uns", "to": "/de/info#impressum", "code": 301},
                                 {"from": "/tr/g%C3%BCmr%C3%BCk", "to": "/tr/gumruk", "code": 301}])
    client = make_app(tmp_path, clock).test_client()
    for path, target in [("/de/grenze/kapitan-andreevo", "/de/grenze"), ("/über-uns", "/de/info#impressum"),
                         ("/%C3%BCber-uns", "/de/info#impressum"), ("/%c3%bcber-uns/", "/de/info#impressum"),
                         ("/tr/gümrük", "/tr/gumruk")]:
        resp = client.get(path)
        assert resp.status_code == 301 and resp.headers["Location"] == target, path
    assert client.get("/de/grenze/kapikule").status_code == 200  # echte Übergänge bleiben unberührt


def test_encoded_and_plain_spelling_of_one_path_are_duplicates():
    entries = [{"from": "/über-uns", "to": "/de/info", "code": 301}, {"from": "/%C3%BCber-uns", "to": None, "code": 410}]
    assert any("doppelt" in p for p in validate_redirects({"meta": {"as_of": "2026-10-07"}, "redirects": entries}))


def test_redirect_must_not_shadow_a_kill_switch(tmp_path, clock, monkeypatch):
    with_redirects(monkeypatch, [{"from": "/service-worker.js", "to": None, "code": 410}])
    make_app(tmp_path, clock)  # ohne Kill-Switch ist der Pfad frei
    with pytest.raises(RuntimeError, match="eigene Route"):
        make_app(tmp_path, clock, TV_LEGACY_SW_PATHS="/service-worker.js")


def test_route_check_accepts_targets_with_query_and_fragment():
    def status(path):
        return 200 if path in ("/de/info", "/de/grenze?land=NW") else 404
    data = {"redirects": [{"from": "/x", "to": "/de/info#impressum", "code": 301},
                          {"from": "/y", "to": "/de/grenze?land=NW", "code": 301},
                          {"from": "/z", "to": None, "code": 410}]}
    assert redirect_route_problems(data, status) == []


# ------------------------------------------------------------------ Startbildschirm-Hinweis, Info-Seite

@pytest.mark.parametrize("path, title, ios", [
    ("/de/", "Öffnet an der Grenze auch ohne Netz", "„Zum Home-Bildschirm“"),
    ("/tr/sinir/kapikule", "Sınırda internetsiz de açılır", "“Ana Ekrana Ekle”yi seç"),
])
def test_home_screen_hint_is_rendered_hidden(client, path, title, ios):
    html = client.get(path).get_data(as_text=True)
    hint = re.search(r'<aside class="a2hs" data-a2hs hidden[^>]*>(.*?)</aside>', html, re.S)
    assert hint, "Hinweis fehlt oder ist ohne JS sichtbar"
    body = hint.group(1)
    assert title in body and ios in body
    assert re.search(r"data-a2hs-install hidden", body) and re.search(r"data-a2hs-ios hidden", body)
    assert 'href="#i-ios-share"' in body and 'href="#i-close"' in body
    assert 'id="i-ios-share"' in html and 'id="i-add-square"' in html and 'id="i-close"' in html


@pytest.mark.parametrize("path", ["/de/offline", "/tr/cevrimdisi", "/de/ferien", "/tr/tatil/yaz-2027", "/de/route",
                                  "/tr/gumruk", "/de/zoll", "/de/info"])
def test_home_screen_hint_only_where_people_look_on_the_road(client, path):
    """Nur Startseite, Grenz-Übersicht und Übergangsseiten – auf der Zollseite verdeckte die Karte
    zusammen mit der Sticky-Toolbar fast alles (Review)."""
    assert "data-a2hs" not in client.get(path).get_data(as_text=True)


@pytest.mark.parametrize("path", ["/de/", "/tr/", "/de/grenze", "/tr/sinir", "/de/grenze/kapikule", "/tr/sinir/ipsala"])
def test_home_screen_hint_on_home_and_border_pages(client, path):
    html = client.get(path).get_data(as_text=True)
    assert '<aside class="a2hs" data-a2hs hidden' in html
    # Kompakte Variante: Knopf für die iOS-Schritte, an die Liste gekoppelt
    assert 'data-a2hs-how aria-expanded="false" aria-controls="a2hs-steps"' in html and 'id="a2hs-steps"' in html


def test_no_home_screen_hint_on_error_pages(client):
    assert "data-a2hs" not in client.get("/gibts-nicht").get_data(as_text=True)


@pytest.mark.parametrize("path, words", [
    ("/de/info", ["Offline nutzen", "7 Tagen Safari-Nutzung", "Home-Bildschirm"]),
    ("/tr/bilgi", ["İnternetsiz kullanım", "7 Safari kullanım gününden", "Ana Ekrana Ekle"]),
])
def test_info_page_explains_offline_behaviour_honestly(client, path, words):
    html = client.get(path).get_data(as_text=True)
    section = re.search(r'<section class="card prose" id="offline".*?</section>', html, re.S).group(0)
    for word in words:
        assert word in section
    assert 'href="https://webkit.org/tracking-prevention/"' in section
    assert "90" in section  # Warteschlange: so lange werden Meldungen nachgeliefert


def test_client_strings_include_delivery_notice(client):
    html = client.get("/de/grenze/kapikule").get_data(as_text=True)
    strings = json.loads(re.search(r'<script type="application/json" id="tv-strings">(.*?)</script>', html, re.S).group(1))
    assert strings["b_report_delivered"].startswith("Deine Meldung ohne Netz")
