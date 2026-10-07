"""Seiten (SSR, funktionieren ohne JavaScript) – je Sprache mit eigenen Slugs."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlencode, urlsplit

from flask import (Flask, Response, abort, current_app, g, jsonify, make_response, redirect,
                   render_template, request, url_for)
from werkzeug.exceptions import HTTPException

from . import borders as B
from . import today_berlin, utcnow
from .content import DATASETS, is_due
from .db import check_salt_db
from .holidays import QUIET_MAX
from .i18n import LANGS, SLUGS, fmt_date, fmt_pct, fmt_range, negotiate

PAGES = ("home", "holidays", "route", "borders", "customs", "info", "offline")
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; manifest-src 'self'; worker-src 'self'; font-src 'self'; "
       "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


def tv():
    return current_app.extensions["tv"]


def t(key: str, **kw):
    return tv().tr.t(g.lang, key, **kw)


def href(page: str, lang: str | None = None, **params) -> str:
    return url_for(f"{page}_{lang or g.lang}", **params)


def now_ts() -> int:
    return int(utcnow(current_app).timestamp())


def abs_url(path: str) -> str:
    base = current_app.config["TV_BASE_URL"] or request.host_url.rstrip("/")
    return base + path


def ago(ts: int | None, now: int | None = None) -> str:
    if ts is None:
        return ""
    now = now or now_ts()
    diff = max(0, now - int(ts))
    if diff < 60:
        return t("ago_now")
    if diff < 3600:
        return t("ago_min", n=diff // 60)
    if diff < 48 * 3600:
        return t("ago_h", n=diff // 3600)
    return t("ago_d", n=diff // 86400)


def in_days(day, today) -> str:
    n = (day - today).days
    if n <= 0:
        return t("in_days_0")
    if n == 1:
        return t("in_days_1")
    return t("in_days_n", n=n)


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def bucket_label(bucket) -> str:
    return t(f"b_bucket_{bucket}") if bucket is not None else ""


def status_text(st: dict) -> dict:
    """Menschenlesbarer Status für eine Richtung (gleiches Format wie im JS)."""
    if st["state"] == "live":
        main = bucket_label(st["bucket"])
        count = tv().tr.plural(g.lang, "b_reports", st["count"], h=st["window_min"] // 60)
    else:
        main = t("b_no_reports") if st["last_at"] else t("b_no_reports_ever")
        count = ""
    return {"main": main, "count": count, "level_label": t(f"b_level_{st['level']}")}


EXTRA_COUNTRIES = {"DE": {"de": "Deutschland", "tr": "Almanya"}, "GR": {"de": "Griechenland", "tr": "Yunanistan"}}


def cname(code: str) -> str:
    names = tv().content.transit["countries"].get(code, {}).get("name") or EXTRA_COUNTRIES.get(code)
    return names[g.lang] if names else code


def fmt_ranges(ranges, with_weekday: bool = True) -> str:
    """Mehrere Ferienblöcke lesbar: 'Do 25.03.2027 + Di 30.03.2027 – Sa 03.04.2027'."""
    tr = tv().tr
    parts = []
    for r in ranges:
        if r.start == r.end:
            parts.append(fmt_date(r.start, g.lang, tr, with_weekday=with_weekday))
        else:
            parts.append(f"{fmt_date(r.start, g.lang, tr, with_weekday=with_weekday)} – "
                         f"{fmt_date(r.end, g.lang, tr, with_weekday=with_weekday)}")
    return " + ".join(parts)


def whatsapp_url(text: str, url: str) -> str:
    return "https://wa.me/?text=" + quote(f"{text} {url}")


def _client_strings() -> dict:
    keys = ["ago_now", "ago_min", "ago_h", "ago_d", "b_no_reports", "b_no_reports_ever", "b_last_report", "b_reports_1",
            "b_reports_n", "b_report_thanks", "b_report_queued", "b_report_ratelimited", "b_report_busy",
            "b_report_stale", "b_report_error", "b_level_ok", "b_level_mid", "b_level_bad", "b_level_none", "c_no_results"]
    keys += [f"b_bucket_{i}" for i in range(B.BUCKET_COUNT)]
    return {k: t(k) for k in keys}


# ------------------------------------------------------------------- Seiten

def home():
    app = current_app
    today = today_berlin(app)
    radar = tv().radar
    content = tv().content
    next_by_state = {}
    for code in radar.states:
        info = radar.next_for_state(code, today)
        if info:
            info["when"] = in_days(info["ranges"][0].start, today)
            next_by_state[code] = info
    running = radar.running_period(today)
    running_now = len(radar.states_on_holiday(today)) if running else 0
    upcoming = radar.next_start(today)
    main = [c for c in content.crossings["crossings"] if c["main"]]
    statuses = B.statuses(_db(), [c["id"] for c in main], now_ts())
    featured = [i for i in content.customs["items"] if i.get("featured")]
    return render_template("home.html", page="home", today=today, next_by_state=next_by_state,
                           running=running, running_now=running_now, upcoming=upcoming, main_crossings=main, statuses=statuses, featured=featured)


def holidays():
    app = current_app
    today = today_berlin(app)
    radar = tv().radar
    period = radar.by_id.get(request.args.get("zeitraum", "")) or radar.current_or_next_period(today) or radar.periods[-1]
    land = request.args.get("land", "").upper()
    land = land if land in radar.states else ""
    bayrams = radar.bayrams_in(period)
    chart = _holiday_chart(radar, period, today, bayrams)
    personal = {}
    for code in radar.states:
        ranges = period.ranges.get(code)
        if ranges:
            stretches = period.stretches(code)
            personal[code] = {"ranges": ranges, "days": period.holiday_days(code), "free": stretches,
                              "free_differs": [(r.start, r.end) for r in stretches] != [(r.start, r.end) for r in ranges],
                              "quiet": radar.quiet_days(period, code),
                              "bayrams": [b for b, states in bayrams if code in states]}
    share_urls, share_texts = {}, {}
    for code, info in personal.items():
        text = t("hol_share_text", period=f"{period.label[g.lang]} {radar.states[code]['short']}",
                 dates=fmt_ranges(info["ranges"], with_weekday=False))
        share_texts[code] = text
        share_urls[code] = whatsapp_url(text, abs_url(href("holidays", zeitraum=period.id, land=code)))
    return render_template("holidays.html", page="holidays", period=period, periods=radar.periods,
                           land=land, chart=chart, personal=personal, today=today,
                           all16=radar.all_states_windows(period), peak=radar.peak(period),
                           share_urls=share_urls, share_texts=share_texts, radar_meta=tv().content.meta("holidays"),
                           radar_due=tv().content.review_due("holidays", today), quiet_max=QUIET_MAX,
                           wave_note=_wave_note, bayram_source=_bayram_source(),
                           bayram_facts=[(b, ", ".join(radar.states[s]["short"] for s in states)) for b, states in bayrams])


def _wave_note(wave, kind: str) -> str:
    """Einordnung eines Reisetags: in welchen Ländern die Ferien gerade beginnen bzw. gleich enden."""
    prefix = "hol_wave_dep" if kind == "departure" else "hol_wave_ret"
    if not wave.states:
        return t(f"{prefix}_none")
    if len(wave.states) > 3:
        return t(f"{prefix}_many", n=len(wave.states))
    return t(f"{prefix}_states", states=", ".join(tv().radar.states[s]["short"] for s in wave.states))


def _bayram_source() -> dict | None:
    return (tv().content.holidays.get("bayrams") or {}).get("source")


def _holiday_chart(radar, period, today, bayrams=()) -> dict:
    """Geometrie der Zeitleiste. x in Prozent (lesbar auf jeder Breite), y in px.

    Keine Inline-Styles, nur SVG-Attribute – so bleibt die CSP ohne 'unsafe-inline'.
    """
    first = period.start - timedelta(days=3)
    last = period.end + timedelta(days=3)
    ndays = (last - first).days + 1
    row_h = 22

    def pct(day) -> float:
        return round((day - first).days / ndays * 100, 3)

    day_pct = 100 / ndays
    series = radar.series(first, last)
    pts = " L".join(f"{i + 0.5:.1f},{100 - share * 100:.2f}" for i, (_d, share, _n) in enumerate(series))
    area = f"M0,100 L{pts} L{ndays},100 Z"

    order = sorted(radar.states, key=lambda s: (period.span(s).start if period.span(s) else period.end, s))
    rows = []
    for idx, code in enumerate(order):
        bars = [{"x": pct(r.start), "w": round(pct(r.end) - pct(r.start) + day_pct, 3)}
                for r in period.ranges.get(code, [])]
        rows.append({"code": code, "short": radar.states[code]["short"], "name": radar.states[code]["name"],
                     "y": idx * row_h, "bars": bars})

    months, day = [], first
    while day <= last:
        if day.day == 1:
            months.append({"x": pct(day), "label": tv().tr.t(g.lang, "month_short")[day.month - 1]})
        day += timedelta(days=1)
    if not months or months[0]["x"] > 12:
        months.insert(0, {"x": 0, "label": tv().tr.t(g.lang, "month_short")[first.month - 1], "edge": True})

    today_x = pct(today) + day_pct / 2 if first <= today <= last else None

    def band(start, end) -> dict:
        start, end = max(start, first), min(end, last)
        return {"x": pct(start), "w": round(pct(end) - pct(start) + day_pct, 3),
                "i": (start - first).days, "n": (end - start).days + 1}

    all16 = [band(w.start, w.end) for w in radar.all_states_windows(period)]
    # Bayram-Festtage als Band (ohne Arife; den nennt der Text darunter)
    bayram_bands = [band(b.days.start, b.days.end) for b, _states in bayrams
                    if b.days.start <= last and b.days.end >= first]
    return {"area": area, "ndays": ndays, "rows": rows, "row_h": row_h, "height": len(rows) * row_h,
            "months": months, "today_x": today_x, "all16": all16, "bayrams": bayram_bands}


def route():
    content = tv().content
    ids = [c["id"] for c in content.crossings["crossings"]]
    statuses = B.statuses(_db(), ids, now_ts())
    return render_template("route.html", page="route", transit=content.transit, statuses=statuses,
                           crossings=content.crossing_by_id, today=today_berlin(current_app))


def borders():
    content = tv().content
    crossings = content.crossings["crossings"]
    now = now_ts()
    statuses = B.statuses(_db(), [c["id"] for c in crossings], now)
    waves = tv().radar.waves(today_berlin(current_app))[:8]
    return render_template("borders.html", page="borders", crossings=crossings, statuses=statuses,
                           waves=waves, now=now, radar=tv().radar, crossings_meta=content.meta("crossings"))


def crossing(cid: str):
    content = tv().content
    c = content.crossing_by_id.get(cid)
    if c is None:
        abort(404)
    now = now_ts()
    db = _db()
    statuses = B.statuses(db, [cid], now)[cid]
    patterns = {d: B.hourly_pattern(db, cid, d, c["tz"], now) for d in B.DIRECTIONS}
    routes = [r for r in content.transit["routes"] if cid in r["controls"]]
    def short(st):
        if st["state"] == "live":
            return f"{bucket_label(st['bucket'])} ({ago(st['last_at'], now)})"
        return t("b_level_none")
    share_text = t("b_share_both", name=c["short"], to_tr=short(statuses["to_tr"]), to_de=short(statuses["to_de"]))
    share_url = whatsapp_url(share_text, abs_url(href("crossing", cid=cid)))
    flash = None
    if request.args.get("gemeldet"):
        flash = ("ok", t("b_report_thanks"))
    elif request.args.get("fehler") in ("ratelimited", "busy", "stale", "invalid"):
        key = {"ratelimited": "b_report_ratelimited", "busy": "b_report_busy",
               "stale": "b_report_stale"}.get(request.args["fehler"], "b_report_error")
        flash = ("error", t(key))
    return render_template("crossing.html", page="crossing", c=c, statuses=statuses, patterns=patterns,
                           routes=routes, share_text=share_text, share_url=share_url, flash=flash, now=now,
                           sources=content.crossings["sources"], countries=content.transit["countries"])


def same_origin(require: bool) -> bool:
    """Schutz vor fremden Formular-Posts (CSRF): Origin bzw. Referer muss unser Host sein.

    Funktioniert, weil wir Referrer-Policy 'same-origin' senden: eigene POSTs tragen den
    echten Origin, fremde Seiten liefern ihren eigenen Origin oder 'null'.
    """
    allowed = {request.host}
    if current_app.config["TV_BASE_URL"]:
        allowed.add(urlsplit(current_app.config["TV_BASE_URL"]).netloc)
    for header in ("Origin", "Referer"):
        value = request.headers.get(header)
        if value and value != "null":
            return urlsplit(value).netloc in allowed
    return not require


def report_form(cid: str):
    """Fallback ohne JavaScript: klassisches Formular, danach Redirect (PRG)."""
    if cid not in tv().content.crossing_by_id:
        abort(404)
    if not same_origin(require=True):
        abort(403)
    target = href("crossing", cid=cid)
    if request.form.get("website"):  # Honeypot: Bots füllen das versteckte Feld
        return redirect(f"{target}?gemeldet=1#melden", code=303)
    try:
        B.add_report(_db(), cid, request.form.get("direction"), request.form.get("bucket"),
                     request.remote_addr or "0.0.0.0", now_ts())
    except B.ReportError as exc:  # code: invalid, stale, ratelimited oder busy (Obergrenze)
        return redirect(f"{target}?fehler={exc.code}#melden", code=303)
    return redirect(f"{target}?gemeldet=1#melden", code=303)


def customs():
    content = tv().content
    items = content.customs["items"]
    by_dir = {d: [i for i in items if i["direction"] == d] for d in content.customs["directions"]}
    today = today_berlin(current_app)
    return render_template("customs.html", page="customs", by_dir=by_dir, data=content.customs,
                           featured=[i for i in items if i.get("featured")], today=today)


def info():
    content = tv().content
    today = today_berlin(current_app)
    datasets = []
    for name in ("holidays", "customs", "transit", "crossings"):
        meta = content.meta(name)
        datasets.append({"name": name, "meta": meta, "due": is_due(meta.get("review_after"), today)})
    reports_24h = B.count_since(_db(), now_ts() - 86400)
    cfg = current_app.config
    operator = {k: cfg[f"TV_OPERATOR_{k.upper()}"] for k in ("name", "address", "email")}
    return render_template("info.html", page="info", datasets=datasets, reports_24h=reports_24h,
                           operator=operator, content=content)


def offline():
    return render_template("offline.html", page="offline")


def manifest():
    lang = g.lang
    data = {
        "name": "tatilvakti – " + t("tagline"),
        "short_name": "tatilvakti",
        "description": t("meta_description"),
        "lang": "de-DE" if lang == "de" else "tr-TR",
        "dir": "ltr",
        "start_url": f"/{lang}/",
        "scope": "/",
        "id": f"/{lang}/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#f5efe6",
        "theme_color": "#0e6b6b",
        "categories": ["travel", "navigation"],
        "icons": [
            {"src": asset("icons/icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": asset("icons/icon-512.png"), "sizes": "512x512", "type": "image/png"},
            {"src": asset("icons/icon-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": t("b_title"), "url": href("borders")},
            {"name": t("hol_title"), "url": href("holidays")},
            {"name": t("c_title"), "url": href("customs")},
        ],
    }
    resp = make_response(json.dumps(data, ensure_ascii=False))
    resp.mimetype = "application/manifest+json"
    return resp


# ------------------------------------------------------- PWA, SEO, Betrieb

def offline_urls() -> list[str]:
    """Alles, was der Service Worker vorab speichert – inkl. aller Ferien-Zeiträume."""
    content = tv().content
    urls = []
    for lang in LANGS:
        for page in PAGES:
            urls.append(url_for(f"{page}_{lang}"))
        for p in tv().radar.periods:
            urls.append(url_for(f"holidays_{lang}", zeitraum=p.id))
        for c in content.crossings["crossings"]:
            urls.append(url_for(f"crossing_{lang}", cid=c["id"]))
    return urls


def service_worker():
    with open(current_app.static_folder + "/js/sw.js", encoding="utf-8") as fh:
        source = fh.read()
    config = {
        "version": tv().build_id,
        "assets": [asset(p) for p in ("css/app.css", "js/app.js", "icons/favicon.svg",
                                      "icons/icon-192.png", "icons/icon-512.png")],
        "pages": offline_urls(),
        "offline": {lang: url_for(f"offline_{lang}") for lang in LANGS},
        "langs": list(LANGS),
    }
    body = "self.TV_CONFIG = " + json.dumps(config, ensure_ascii=False) + ";\n" + source
    resp = Response(body, mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


def root():
    lang = negotiate(request.headers.get("Accept-Language"))
    resp = redirect(url_for(f"home_{lang}"), code=302)
    resp.headers["Vary"] = "Accept-Language"
    return resp


def robots():
    body = f"User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: {abs_url('/sitemap.xml')}\n"
    return Response(body, mimetype="text/plain")


def sitemap():
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">']
    entries = [(p, {}) for p in PAGES if p != "offline"]
    entries += [("crossing", {"cid": c["id"]}) for c in tv().content.crossings["crossings"]]
    for page, params in entries:
        for lang in LANGS:
            lines.append("<url><loc>" + abs_url(url_for(f"{page}_{lang}", **params)) + "</loc>")
            for alt in LANGS:
                hreflang = "de-DE" if alt == "de" else "tr-TR"
                lines.append(f'<xhtml:link rel="alternate" hreflang="{hreflang}" '
                             f'href="{abs_url(url_for(f"{page}_{alt}", **params))}"/>')
            lines.append("</url>")
    lines.append("</urlset>")
    return Response("\n".join(lines), mimetype="application/xml")


def due_items(content, today: date) -> tuple[list[dict], str | None]:
    """Fällige Prüfdaten: Datensätze, Einzelregeln (review_after) und Preise (prices_valid_until).

    Liefert die überschrittenen Einträge und das nächste noch nicht fällige Datum
    (für eine Kalender-Erinnerung).
    """
    checks = [(name, None, "review_after", content.meta(name).get("review_after")) for name in DATASETS]
    checks += [("customs", i["id"], "review_after", i.get("review_after")) for i in content.customs["items"]]
    for code, country in content.transit["countries"].items():
        checks += [("transit", code, f, country.get(f)) for f in ("prices_valid_until", "review_after")]
    checks += [("crossings", c["id"], "review_after", c.get("review_after")) for c in content.crossings["crossings"]]
    due, upcoming = [], []
    for dataset, item, fieldname, value in checks:
        if not value:
            continue
        if is_due(value, today):
            due.append({"dataset": dataset, "item": item, "field": fieldname, "date": value})
        else:
            upcoming.append(value)
    return due, (min(upcoming) if upcoming else None)


def healthz():
    """Betriebsstatus für das Monitoring. HTTP 503 nur, wenn die DB nicht antwortet.

    status 'attention' (weiter HTTP 200), wenn etwas zu tun ist; die Gründe stehen in 'attention'.
    Eine kaputte Schlüssel-DB ('salt_db') legt alle Meldungen lahm, die Seiten laufen aber weiter –
    deshalb kein 503, sonst nähme ein Health-Check im Proxy die ganze Seite vom Netz.
    """
    content = tv().content
    cfg = current_app.config
    today = today_berlin(current_app)
    now = now_ts()
    try:
        maintenance_at = B.last_maintenance(_db())
        db_ok = True
    except sqlite3.Error:  # pragma: no cover - DB nicht lesbar
        maintenance_at, db_ok = None, False
    salt_error = check_salt_db(cfg["TV_SALT_DB_PATH"])
    runtime = tv().runtime
    if salt_error and not runtime["salt_db_failed"]:  # nur beim Wechsel loggen, nicht jede Minute
        current_app.logger.warning("Schlüssel-DB (TV_SALT_DB_PATH) nicht nutzbar, Meldungen scheitern: %s",
                                   salt_error)
    runtime["salt_db_failed"] = salt_error is not None
    due, next_review = due_items(content, today)
    imprint_ok = all(str(cfg[f"TV_OPERATOR_{k}"]).strip() for k in ("NAME", "ADDRESS", "EMAIL"))
    forwarded_ignored = runtime["forwarded_ignored"]
    # Wartung läuft im before_request alle 10 Min.; deutlich älter heißt: Schreiben schlägt fehl
    maintenance_overdue = maintenance_at is None or now - maintenance_at > 3 * B.MAINTENANCE_EVERY_S
    attention = [reason for reason, active in (("due_items", bool(due)), ("imprint", not imprint_ok),
                                                ("proxy", forwarded_ignored),
                                                ("maintenance", db_ok and maintenance_overdue),
                                                ("salt_db", salt_error is not None)) if active]
    data = {
        "status": "degraded" if not db_ok else ("attention" if attention else "ok"),
        "db": db_ok,
        "salt_db": salt_error is None,
        "build": tv().build_id,
        "datasets": {n: {"as_of": content.meta(n)["as_of"], "review_due": content.review_due(n, today)}
                     for n in DATASETS},
        "due_items": due,
        "next_review": next_review,
        "imprint_ok": imprint_ok,
        "proxy": {"trust_proxy": cfg["TV_TRUST_PROXY"], "forwarded_ignored": forwarded_ignored},
        "maintenance_at": iso(maintenance_at) if maintenance_at else None,
        "attention": attention,
    }
    return data, (200 if db_ok else 503)


# Stabile Fehlercodes der JSON-API (Werkzeug-Namen wären sprachlich und versionsabhängig)
API_ERRORS = {400: "invalid", 405: "method_not_allowed", 413: "too_large", 415: "invalid", 429: "ratelimited"}


def _db():
    from .db import get_db
    return get_db()


def asset(path: str) -> str:
    return url_for("static", filename=path, v=tv().asset_hashes.get(path, "0"))


# ------------------------------------------------------------- Registrierung

def register(app: Flask) -> None:
    views = {"home": home, "holidays": holidays, "route": route, "borders": borders,
             "customs": customs, "info": info, "offline": offline}
    for lang in LANGS:
        for page, view in views.items():
            slug = SLUGS[page][lang]
            app.add_url_rule(f"/{lang}/{slug}", endpoint=f"{page}_{lang}", view_func=view,
                             defaults={"lang": lang})
        base = f"/{lang}/{SLUGS['borders'][lang]}"
        app.add_url_rule(f"{base}/<cid>", endpoint=f"crossing_{lang}", view_func=crossing, defaults={"lang": lang})
        app.add_url_rule(f"{base}/<cid>/report", endpoint=f"report_{lang}", view_func=report_form,
                         methods=["POST"], defaults={"lang": lang})
        app.add_url_rule(f"/{lang}/manifest.webmanifest", endpoint=f"manifest_{lang}", view_func=manifest,
                         defaults={"lang": lang})
    app.add_url_rule("/", endpoint="root", view_func=root)
    app.add_url_rule("/sw.js", endpoint="sw", view_func=service_worker)
    app.add_url_rule("/robots.txt", endpoint="robots", view_func=robots)
    app.add_url_rule("/sitemap.xml", endpoint="sitemap", view_func=sitemap)
    app.add_url_rule("/healthz", endpoint="healthz", view_func=healthz)

    @app.template_filter("todate")
    def todate(value):
        return value if isinstance(value, date) else date.fromisoformat(value)

    @app.url_value_preprocessor
    def pull_lang(_endpoint, values):
        if values and "lang" in values:
            g.lang = values.pop("lang")

    @app.before_request
    def default_lang():
        if "lang" not in g:
            first = request.path.strip("/").split("/", 1)[0]
            g.lang = first if first in LANGS else negotiate(request.headers.get("Accept-Language"))

    @app.before_request
    def housekeeping():
        """Proxy-Check und Datensparsamkeit – auch ohne neue Meldungen.

        Die Wartung stößt jeder Prozess höchstens alle 10 Min. an (ohne DB-Zugriff dazwischen),
        B.maintenance prüft dann über alle Prozesse den gemeinsamen Zeitstempel in kv.
        """
        if request.endpoint == "static":
            return
        runtime = tv().runtime
        if (not current_app.config["TV_TRUST_PROXY"] and not runtime["forwarded_ignored"]
                and ("X-Forwarded-For" in request.headers or "Forwarded" in request.headers)):
            runtime["forwarded_ignored"] = True
            current_app.logger.warning(
                "Anfrage mit X-Forwarded-For, aber TV_TRUST_PROXY=0: Der Spam-Schutz sieht nur die "
                "Adresse des Proxys, alle Nutzer teilen sich ein Limit. Hinter Pangolin TV_TRUST_PROXY=1 setzen.")
        now = now_ts()
        last = runtime["maintenance_checked_at"]
        if last is not None and 0 <= now - last < B.MAINTENANCE_EVERY_S:
            return
        runtime["maintenance_checked_at"] = now
        try:
            B.maintenance(_db(), now)
        except sqlite3.Error as exc:  # Seite trotzdem ausliefern; /healthz meldet 'maintenance'
            current_app.logger.warning("Wartung fehlgeschlagen: %s", exc)

    @app.context_processor
    def inject():
        endpoint = request.endpoint or ""
        page = endpoint.rsplit("_", 1)[0] if endpoint.endswith(tuple(f"_{l}" for l in LANGS)) else None
        other = "tr" if g.lang == "de" else "de"
        alt_urls = {}
        if page and page not in ("report", "manifest"):
            args = dict(request.view_args or {})
            query = {k: v for k, v in request.args.items() if k in ("zeitraum", "land")}
            for lang in LANGS:
                alt_urls[lang] = url_for(f"{page}_{lang}", **args) + (("?" + urlencode(query)) if query else "")
        tr = tv().tr
        return {
            "lang": g.lang, "other_lang": other, "t": t, "href": href, "asset": asset,
            "alt_urls": alt_urls, "abs_url": abs_url, "ago": ago, "iso": iso, "in_days": in_days,
            "fmt_date": lambda d, **kw: fmt_date(d, g.lang, tr, **kw),
            "fmt_range": lambda a, b: fmt_range(a, b, g.lang, tr),
            "fmt_pct": lambda share, digits=1: fmt_pct(share, g.lang, digits),
            "plural": lambda key, n, **kw: tr.plural(g.lang, key, n, **kw),
            "status_text": status_text, "bucket_label": bucket_label, "bucket_count": B.BUCKET_COUNT,
            "client_strings": _client_strings, "build_id": tv().build_id,
            "states": tv().radar.states, "is_due": is_due, "cname": cname, "fmt_ranges": fmt_ranges,
        }

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        # same-origin: externe Seiten erfahren nichts, eigene POSTs tragen einen prüfbaren Origin
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=(), interest-cohort=()"
        resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        resp.headers.pop("Set-Cookie", None)  # Grundsatz: niemals Cookies
        # HSTS nur über HTTPS (request.is_secure gilt nach ProxyFix) oder bei https-Basis-URL.
        # Ohne includeSubDomains: andere Subdomains der Domain sind nicht unsere Sache.
        if request.is_secure or current_app.config["TV_BASE_URL"].startswith("https://"):
            resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.path.startswith("/static/"):
            if request.args.get("v"):
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-cache"
        elif "Cache-Control" not in resp.headers or resp.mimetype == "text/html":
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.errorhandler(403)
    def forbidden(_exc):
        if request.path.startswith("/api/"):
            return {"error": "forbidden"}, 403
        return render_template("error.html", page=None, code=403), 403

    @app.errorhandler(404)
    def not_found(_exc):
        if request.path.startswith("/api/"):
            return {"error": "not_found"}, 404
        return render_template("error.html", page=None, code=404), 404

    @app.errorhandler(500)
    def server_error(_exc):  # pragma: no cover - Notfallpfad
        if request.path.startswith("/api/"):
            return {"error": "server_error", "degraded": True}, 500
        return render_template("error.html", page=None, code=500), 500

    @app.errorhandler(HTTPException)
    def http_error(exc):
        """Alle übrigen HTTP-Fehler (400, 405, 413 …): unter /api/ immer JSON, sonst Standardseite."""
        if not request.path.startswith("/api/"):
            return exc
        resp = jsonify({"error": API_ERRORS.get(exc.code, "http_error")})
        resp.status_code = exc.code or 500
        for name, value in exc.get_headers():
            if name.lower() != "content-type":  # z. B. Allow bei 405
                resp.headers[name] = value
        return resp
