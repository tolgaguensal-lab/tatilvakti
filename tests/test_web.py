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
    assert data["status"] == "ok" and data["db"] is True
    assert data["datasets"]["holidays"]["as_of"] == "2026-10-06"
    assert data["datasets"]["holidays"]["review_due"] is False


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
