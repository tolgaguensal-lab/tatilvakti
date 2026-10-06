"""Seiten, API, PWA und Datenschutz-Garantien über HTTP."""
import json
import re
from datetime import datetime, timezone

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
    html = client.get("/de/ferien?zeitraum=sommer-2027&land=NW").get_data(as_text=True)
    assert 'hreflang="tr-TR" href="https://tatilvakti.example/tr/tatil?zeitraum=sommer-2027&amp;land=NW"' in html
    assert 'href="/tr/tatil?zeitraum=sommer-2027&amp;land=NW"' in html  # Nummernschild-Umschalter


def test_holiday_page_personalises_server_side(client):
    html = client.get("/de/ferien?zeitraum=sommer-2027&land=NW").get_data(as_text=True)
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
    html = client.get("/de/ferien?zeitraum=ostern-2027&land=BW").get_data(as_text=True)
    assert "Do 25.03.2027 + Di 30.03.2027 – Sa 03.04.2027" in html
    assert "6 Ferientage" in html
    assert "Frei inkl. Wochenenden und bundesweiter Feiertage: Do 25.03.2027 – So 04.04.2027" in html


def test_unknown_pages_are_404(client):
    assert client.get("/de/grenze/atlantis").status_code == 404
    assert client.get("/api/v1/borders/atlantis").get_json() == {"error": "not_found"}


def test_service_worker_precaches_all_pages(client):
    resp = client.get("/sw.js")
    assert resp.status_code == 200 and resp.mimetype == "application/javascript"
    assert resp.headers["Cache-Control"] == "no-cache"
    body = resp.get_data(as_text=True)
    config = json.loads(body.split("self.TV_CONFIG = ", 1)[1].split(";\n", 1)[0])
    for path in ALL_PAGES:
        assert path in config["pages"]
    assert "/de/ferien?zeitraum=sommer-2027" in config["pages"]
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
    assert data["db"] is True
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


def test_sitemap_lists_both_languages(client):
    xml = client.get("/sitemap.xml").get_data(as_text=True)
    assert "https://tatilvakti.example/tr/sinir/kapikule" in xml
    assert 'hreflang="de-DE"' in xml
