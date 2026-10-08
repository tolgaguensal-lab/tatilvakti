"""Seiten (SSR, funktionieren ohne JavaScript) – je Sprache mit eigenen Slugs."""
from __future__ import annotations

import ipaddress
import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlencode, urlsplit

from flask import (Flask, Response, abort, current_app, g, jsonify, make_response, redirect,
                   render_template, request, url_for)
from markupsafe import Markup, escape
from werkzeug.exceptions import HTTPException, MethodNotAllowed, NotFound
from werkzeug.routing import RequestRedirect

from . import borders as B
from . import operator_imprint, today_berlin, utcnow
from .content import DATASETS, is_due, redirect_key
from .db import SaltDbError, check_salt_db
from .holidays import QUIET_MAX
from .i18n import LANGS, SLUGS, fmt_date, fmt_pct, fmt_range, negotiate

PAGES = ("home", "holidays", "route", "borders", "customs", "info", "offline")
# Präfix aller Caches von v2. Der Service Worker löscht beim Aktivieren alle anderen Caches des
# Origins (auch die der Alt-App), der Kill-Switch für alte Worker löscht alle außer diesen.
CACHE_PREFIX = "tv2-"
# Pfade alter Service-Worker-Skripte (TV_LEGACY_SW_PATHS): absolut, Segmente ohne Sonderzeichen, .js
LEGACY_SW_RE = re.compile(r"^(?:/[A-Za-z0-9._~@+-]+)+$")
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
    """Direkt zu WhatsApp (wa.me), Text und Link in einer Nachricht; ohne Tracking-Parameter."""
    return "https://wa.me/?text=" + quote(f"{text} {url}")


def is_live(st: dict, dirs=B.DIRECTIONS) -> bool:
    """Hat der Übergang in einer der Richtungen eine Meldung aus dem aktuellen Fenster?"""
    return any(st[d]["state"] == "live" for d in dirs)


def any_live(statuses: dict, cids, dirs=B.DIRECTIONS) -> bool:
    return any(is_live(statuses[cid], dirs) for cid in cids)


def official_sources(crossings) -> list[dict]:
    """Infos von Behörden und Automobilclubs für diese Übergänge, ohne Doppelte.

    Reihenfolge wie in crossings.json (sources), damit die Datenpflege sie bestimmt.
    """
    used = {ref for c in crossings for ref in c["official"]}
    return [src for key, src in tv().content.crossings["sources"].items() if key in used]


def _client_strings() -> dict:
    keys = ["ago_now", "ago_min", "ago_h", "ago_d", "b_no_reports", "b_no_reports_ever", "b_last_report", "b_reports_1",
            "b_reports_n", "b_report_thanks", "b_report_sending", "b_report_queued", "b_report_slow", "b_report_retry",
            "b_report_delivered", "b_report_dropped_1", "b_report_dropped_n", "b_report_partial_1", "b_report_partial_n",
            "b_report_ratelimited", "b_report_busy", "b_report_stale", "b_report_error", "b_level_ok", "b_level_mid", "b_level_bad", "b_level_none",
            "c_no_results", "c_results_1", "c_results_n"]
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
    crossings = content.crossings["crossings"]
    main = [c for c in crossings if c["main"]]
    statuses = B.statuses(_db(), [c["id"] for c in main], now_ts())
    featured = [i for i in content.customs["items"] if i.get("featured")]
    return render_template("home.html", page="home", today=today, next_by_state=next_by_state,
                           running=running, running_now=running_now, upcoming=upcoming, main_crossings=main,
                           statuses=statuses, featured=featured, crossings=crossings,
                           official=official_sources(main))


def holidays(slug: str | None = None):
    """Ferien-Radar. /de/ferien zeigt den laufenden oder nächsten Zeitraum, /de/ferien/<slug>
    (TR: /tr/tatil/<slug>) einen bestimmten – mit eigenem Titel und h1, kanonisch ohne ?land=.

    Alte Links mit ?zeitraum=<id> und Slugs der anderen Sprache leiten dauerhaft (301) auf den
    Pfad weiter; ?land= bleibt dabei erhalten. Ein unbekannter Slug – meist ein abgelaufener
    Zeitraum aus einem geteilten Link – führt auf die Übersicht (302, mit ?land=): nur vorläufig,
    damit ein Browser die Umleitung nicht dauerhaft speichert, falls es den Zeitraum später gibt.
    """
    app = current_app
    today = today_berlin(app)
    radar = tv().radar
    land = request.args.get("land", "").upper()
    land = land if land in radar.states else ""
    keep = {"land": land} if land else {}
    if slug is None:
        target = radar.find_period(request.args.get("zeitraum", ""))
        if target is not None:
            return redirect(href("holidays", slug=target.slug[g.lang], **keep), code=301)
        period = radar.current_or_next_period(today) or radar.periods[-1]
    else:
        period = radar.by_slug[g.lang].get(slug)
        if period is None:
            target = radar.find_period(slug)
            if target is None:
                return redirect(href("holidays", **keep), code=302)
            return redirect(href("holidays", slug=target.slug[g.lang], **keep), code=301)
        # Sprachwechsel, hreflang und Canonical: derselbe Zeitraum mit dem Slug der anderen Sprache
        g.alt_params = {lang: {"slug": period.slug[lang]} for lang in LANGS}
    bayrams = radar.bayrams_in(period)
    chart = _holiday_chart(radar, period, today, bayrams)
    personal = {}
    for code in radar.states:
        ranges = period.ranges.get(code)
        if ranges:
            stretches = period.stretches(code)
            personal[code] = {"ranges": ranges, "days": period.holiday_days(code), "free": stretches,
                              "free_differs": [(r.start, r.end) for r in stretches] != [(r.start, r.end) for r in ranges],
                              "quiet": radar.quiet_days(period, code, today),
                              "bayrams": [b for b, states in bayrams if code in states]}
    share_texts = {code: _holiday_share_text(radar, period, code, info) for code, info in personal.items()}
    # Link-Vorschau (z. B. WhatsApp): mit Bundesland dessen Ferien und ruhige Tage, sonst die Einleitung
    description = share_texts.get(land) or t("hol_lead")
    return render_template("holidays.html", page="holidays", period=period, periods=radar.periods,
                           period_page=slug is not None, land=land, chart=chart, personal=personal, today=today,
                           all16=radar.all_states_windows(period), peak=radar.peak(period),
                           share_texts=share_texts, description=description, radar_meta=tv().content.meta("holidays"),
                           radar_due=tv().content.review_due("holidays", today), quiet_max=QUIET_MAX,
                           wave_note=_wave_note, bayram_source=_bayram_source(),
                           bayram_facts=[(b, ", ".join(radar.states[s]["short"] for s in states)) for b, states in bayrams])


def _holiday_share_text(radar, period, code: str, info: dict) -> str:
    """„Nordrhein-Westfalen, Sommerferien 2027: 19.07.–31.08.2027 · Tage mit der kleinsten
    Reisewelle – Abreise: Di 20.07. (0 %); Rückreise: Sa 28.08. (0 %)“.

    Voller Ländername, Schulferien wie amtlich festgelegt, je Richtung der beste Tag wie auf der
    Seite (Rang 1) mit Reisewelle – bzw. ehrlich „kein ruhiger Tag“. Richtungen, deren Tage schon
    vorbei sind, fehlen. Zugleich die Beschreibung der Link-Vorschau.
    """
    tr = tv().tr

    def best(kind: str) -> str | None:
        pick = info["quiet"][kind]
        if not pick.days:
            return None
        w = pick.days[0]
        day = f"{fmt_date(w.day, g.lang, tr, with_weekday=True, with_year=False)} ({fmt_pct(w.share, g.lang)})"
        return day if pick.calm else t("hol_share_none", day=day)

    dates = " + ".join(fmt_range(r.start, r.end, g.lang, tr) for r in info["ranges"])
    text = t("hol_share_text", state=radar.states[code]["name"], period=period.label[g.lang], dates=dates)
    parts = [t(key, day=day) for key, day in (("hol_share_dep", best("departure")), ("hol_share_ret", best("return")))
             if day]
    if parts:
        text += " · " + t("hol_share_quiet", parts="; ".join(parts))
    return text


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
            # Beginnt der Monat kurz vor dem rechten Rand, passt sein Name nicht mehr (320 px: „Haz“
            # ragte aus der Zeitleiste) – dann nur die Gitterlinie
            months.append({"x": pct(day), "label": tv().tr.t(g.lang, "month_short")[day.month - 1],
                           "tight": pct(day) > 88})
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


def season(as_of: str) -> str:
    """Reisesaison eines Datenstands für den Seitentitel: ab September die Saison bis zum
    nächsten Sommer („2026/27“), davor das laufende Jahr („2027“)."""
    day = date.fromisoformat(as_of)
    return f"{day.year}/{(day.year + 1) % 100:02d}" if day.month >= 9 else str(day.year)


def route():
    content = tv().content
    ids = [c["id"] for c in content.crossings["crossings"]]
    statuses = B.statuses(_db(), ids, now_ts())
    return render_template("route.html", page="route", transit=content.transit, statuses=statuses,
                           crossings=content.crossing_by_id, today=today_berlin(current_app),
                           season=season(content.meta("transit")["as_of"]),
                           share_text=t("r_share_text", n=len(content.transit["routes"])))


def borders():
    content = tv().content
    crossings = content.crossings["crossings"]
    now = now_ts()
    statuses = B.statuses(_db(), [c["id"] for c in crossings], now)
    waves = tv().radar.waves(today_berlin(current_app))[:8]
    return render_template("borders.html", page="borders", crossings=crossings, statuses=statuses,
                           waves=waves, now=now, radar=tv().radar, crossings_meta=content.meta("crossings"),
                           official=official_sources(crossings),
                           main_names=", ".join(c["short"] for c in crossings if c["main"]))


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
    # Nur Richtungen mit aktueller Meldung; ohne jede Meldung ein Aufruf statt „keine Daten“
    parts = [f"{t('b_dir_' + d)}: {bucket_label(statuses[d]['bucket'])} ({ago(statuses[d]['last_at'], now)})"
             for d in B.DIRECTIONS if statuses[d]["state"] == "live"]
    if parts:
        share_text = t("b_share_live", name=c["short"], parts=", ".join(parts))
    else:
        share_text = t("b_share_call", at=c["name_loc"][g.lang])
    flash = None
    if request.args.get("gemeldet"):
        flash = ("ok", t("b_report_thanks"))
    elif request.args.get("fehler") in ("ratelimited", "busy", "stale", "invalid"):
        key = {"ratelimited": "b_report_ratelimited", "busy": "b_report_busy",
               "stale": "b_report_stale"}.get(request.args["fehler"], "b_report_error")
        flash = ("error", t(key))
    return render_template("crossing.html", page="crossing", c=c, statuses=statuses, patterns=patterns,
                           routes=routes, share_text=share_text, flash=flash, now=now,
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
    # Fristen im Datenschutztext kommen aus dem Code, damit Text und Löschung nie auseinanderlaufen
    return render_template("info.html", page="info", datasets=datasets, reports_24h=reports_24h,
                           operator=operator_imprint(current_app.config), content=content,
                           hash_ttl_h=B.CLIENT_HASH_TTL_H, retention_days=B.RETENTION_DAYS)


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
    """Alles, was der Service Worker vorab speichert – inkl. aller Ferien-Zeiträume (als Pfad)."""
    content = tv().content
    urls = []
    for lang in LANGS:
        for page in PAGES:
            urls.append(url_for(f"{page}_{lang}"))
        for p in tv().radar.periods:
            urls.append(url_for(f"holidays_{lang}", slug=p.slug[lang]))
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
        # Pflichtseiten: ohne sie scheitert der Install, der bisherige Worker bleibt aktiv
        "home": {lang: url_for(f"home_{lang}") for lang in LANGS},
        "offline": {lang: url_for(f"offline_{lang}") for lang in LANGS},
        "langs": list(LANGS),
        "cachePrefix": CACHE_PREFIX,
    }
    body = "self.TV_CONFIG = " + json.dumps(config, ensure_ascii=False) + ";\n" + source
    resp = Response(body, mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


def legacy_service_worker():
    """Kill-Switch unter dem Pfad eines alten Service Workers (TV_LEGACY_SW_PATHS).

    Der Browser prüft registrierte Worker regelmäßig auf Updates und lädt dabei dieses Skript:
    Es aktiviert sich sofort, löscht alle Caches außer denen von v2, meldet sich ab und lädt
    offene Fenster neu. Ohne fetch-Handler gehen Anfragen bis dahin direkt ins Netz.
    """
    resp = Response(render_template("legacy_sw.js", cache_prefix=CACHE_PREFIX), mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-store"
    # Hatte der alte Worker per Header einen weiteren Scope als sein Verzeichnis, prüft der
    # Browser das beim Update erneut – ohne diesen Header schlüge das Update fehl.
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


def parse_legacy_sw_paths(value) -> list[str]:
    """TV_LEGACY_SW_PATHS: kommagetrennt (Env) oder Liste (Tests); leere Einträge entfallen."""
    items = value.split(",") if isinstance(value, str) else list(value or [])
    return [str(p).strip() for p in items if str(p).strip()]


def own_status(app: Flask, path: str) -> int:
    """HTTP-Status, den v2 selbst für GET path liefert – ohne Weiterleitungstabelle.

    Für die Prüfung von data/redirects.json beim Start (content.redirect_route_problems): Nur so
    zählen auch Routen mit Platzhalter richtig (/de/grenze/<unbekannt> → 404, die Weiterleitung
    greift) und Ziele, die selbst 404, 405 oder eine Weiterleitung liefern.
    """
    content = app.extensions["tv"].content
    table, content.redirect_by_path = content.redirect_by_path, {}
    try:
        return app.test_client().get(path).status_code
    finally:
        content.redirect_by_path = table


def is_route(app: Flask, path: str) -> bool:
    """Gehört der Pfad zu einer eigenen Route (auch nur für POST oder per Slash-Weiterleitung)?"""
    adapter = app.url_map.bind("localhost")
    try:
        adapter.match(path, method="GET")
    except (RequestRedirect, MethodNotAllowed):
        return True
    except NotFound:
        return False
    return True


def legacy_sw_problems(app: Flask, paths: list[str]) -> list[str]:
    problems, seen = [], set()
    for path in paths:
        where = f"TV_LEGACY_SW_PATHS: {path!r}"
        if not path.startswith("/"):
            problems.append(f"{where} muss mit / beginnen")
        elif "?" in path or "#" in path:
            problems.append(f"{where} ohne Query oder Fragment angeben, nur den Pfad")
        elif not path.endswith(".js"):
            problems.append(f"{where} muss auf .js enden")
        elif not LEGACY_SW_RE.match(path) or any(seg in (".", "..") for seg in path.split("/")):
            problems.append(f"{where} enthält unzulässige Zeichen (erlaubt: A–Z a–z 0–9 . _ ~ @ + - /)")
        elif path in seen:
            problems.append(f"{where} ist doppelt")
        elif is_route(app, path):
            problems.append(f"{where} kollidiert mit einer eigenen Route (z. B. /sw.js, /static/…)")
        seen.add(path)
    return problems


def register_legacy_sw(app: Flask, paths: list[str]) -> None:
    """Kill-Switch-Routen anlegen; erst nach allen eigenen Routen aufrufen (Kollisionsprüfung)."""
    problems = legacy_sw_problems(app, paths)
    if problems:
        raise RuntimeError("Konfiguration fehlerhaft:\n" + "\n".join(problems))
    for idx, path in enumerate(paths):
        app.add_url_rule(path, endpoint=f"legacy_sw_{idx}", view_func=legacy_service_worker)


def root():
    lang = negotiate(request.headers.get("Accept-Language"))
    resp = redirect(url_for(f"home_{lang}"), code=302)
    resp.headers["Vary"] = "Accept-Language"
    return resp


def robots():
    body = f"User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: {abs_url('/sitemap.xml')}\n"
    return Response(body, mimetype="text/plain")


def sitemap():
    """Alle indexierbaren Seiten beider Sprachen, je mit hreflang-Alternativen und x-default.

    Ein Eintrag ist eine Seite in allen Sprachen: {Sprache: Pfad}. Ferienzeiträume haben je
    Sprache einen eigenen Slug, deshalb die Pfade je Sprache statt gemeinsamer Parameter.
    """
    content = tv().content
    entries = [{lang: url_for(f"{p}_{lang}") for lang in LANGS} for p in PAGES if p != "offline"]
    entries += [{lang: url_for(f"holidays_{lang}", slug=p.slug[lang]) for lang in LANGS} for p in tv().radar.periods]
    entries += [{lang: url_for(f"crossing_{lang}", cid=c["id"]) for lang in LANGS}
                for c in content.crossings["crossings"]]

    def loc(path: str) -> str:  # str(): Markup würde beim Verketten den Rest escapen
        return str(escape(abs_url(path)))

    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">']
    for paths in entries:
        alternates = [(lang, paths[lang]) for lang in LANGS] + [("x-default", paths[LANGS[0]])]
        for lang in LANGS:
            lines.append(f"<url><loc>{loc(paths[lang])}</loc>")
            lines += [f'<xhtml:link rel="alternate" hreflang="{code}" href="{loc(path)}"/>' for code, path in alternates]
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


# Prüft das Schreibrecht auf reports, ohne etwas zu schreiben: fügt keine Zeile ein
_NOOP_REPORT_INSERT = ("INSERT INTO reports (crossing, direction, bucket, observed_at, created_at) "
                       "SELECT '', 'to_tr', 0, 0, 0 WHERE 0")


def _report_db_problem(now: int) -> str | None:
    """Kann die Haupt-DB jetzt eine Meldung speichern? None, sonst die Fehlermeldung (nur fürs Log).

    Prüft, was B.add_report braucht, ohne bei jedem Abruf zu schreiben:
    1. Lesen: reports über den Index auf created_at (wie die Obergrenze).
    2. Schreibsperre und Schreibrecht: BEGIN IMMEDIATE wie add_report, dann ein INSERT in reports,
       das keine Zeile einfügt, und ROLLBACK. Erst dieses INSERT scheitert an einer schreib-
       geschützten DB (Dateirechte, -wal/-shm eines anderen Benutzers, read-only eingehängt);
       BEGIN IMMEDIATE allein gelingt dann noch. Die Dateien bleiben unverändert. Hält ein anderer
       Prozess die Sperre, wartet die Prüfung so lange wie eine Meldung (db.BUSY_TIMEOUT_MS) und
       scheitert genau dann, wenn auch die Meldung scheitern würde.
    3. Echtes Schreiben: Eine volle Platte oder einen E/A-Fehler zeigt erst ein Schreibvorgang.
       Dafür stößt /healthz die Wartung an. Sie schreibt über alle Prozesse höchstens alle
       MAINTENANCE_EVERY_S Sekunden in kv (derselbe Schreibvorgang, den sonst der nächste
       Seitenaufruf oder der Timer auslöst), dazwischen liest sie nur. Scheitert das Schreiben,
       versucht es der nächste Abruf erneut, bis es wieder klappt. Fehler der Schlüssel-DB
       (SaltDbError) zählen hier nicht, die prüft check_salt_db.
    """
    try:
        conn = _db()
        conn.execute("SELECT MAX(created_at) FROM reports").fetchone()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(_NOOP_REPORT_INSERT)
        finally:
            if conn.in_transaction:  # manche Fehler (z. B. volle Platte) rollen selbst zurück
                conn.execute("ROLLBACK")
        try:
            B.maintenance(conn, now)
        except SaltDbError:
            pass
    except sqlite3.Error as exc:
        return str(exc) or type(exc).__name__
    return None


# Setzt jeder Reverse-Proxy (Traefik/Pangolin): Die Anfrage kam von außen, auch wenn sie über einen
# Tunnel von 127.0.0.1 eintrifft
_PROXY_HEADERS = ("X-Forwarded-For", "Forwarded", "X-Real-IP")


def _local_call() -> bool:
    """Kommt die Anfrage direkt vom Server selbst (deploy.sh, curl auf 127.0.0.1:3096)?

    Über Pangolin kommt sie ebenfalls von 127.0.0.1, trägt aber X-Forwarded-For. Mit
    TV_TRUST_PROXY setzt ProxyFix außerdem die Adresse des Clients ein. Beides zählt als außen,
    auch bei falsch gesetztem TV_TRUST_PROXY.
    """
    try:
        addr = ipaddress.ip_address(request.remote_addr or "")
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_loopback and not any(name in request.headers for name in _PROXY_HEADERS)


def healthz():
    """Betriebsstatus für Monitoring und deploy.sh. Nie gecacht (Cache-Control: no-store).

    HTTP 503 mit status 'down', sobald Melden nicht geht; die Gründe stehen in 'down': 'db' (die
    Haupt-DB nimmt keine Meldung an, siehe _report_db_problem) und 'salt_db' (Schlüssel-DB nicht
    nutzbar). Die Seiten laufen dann meist weiter. Deshalb darf kein Health-Check des Proxys, der
    bei Fehlern die ganze Seite vom Netz nimmt, auf /healthz zeigen.

    Sonst HTTP 200: status 'attention', wenn etwas zu pflegen ist (Gründe in 'attention':
    fällige Datenprüfung, Impressum, Proxy-Einstellung, überfällige Wartung, Obergrenze für
    Meldungen in den letzten 24 h erreicht), sonst 'ok'. Ein Uptime-Monitor prüft nur den
    Statuscode, die Pflegehinweise sind für einen täglichen Blick.

    Alle Details nur für Aufrufe auf dem Server selbst (_local_call). Von außen nur status, build
    und down mit demselben Statuscode: Proxy-Einstellung, Zustand des Spam-Schutzes und Pflege-
    hinweise gehen niemanden sonst etwas an.
    """
    content = tv().content
    cfg = current_app.config
    today = today_berlin(current_app)
    now = now_ts()
    runtime = tv().runtime
    db_error = _report_db_problem(now)
    if db_error and not runtime.get("db_failed"):  # nur beim Wechsel loggen, nicht jede Minute
        current_app.logger.warning("Haupt-DB (TV_DB_PATH) nimmt keine Meldungen an: %s", db_error)
    runtime["db_failed"] = db_error is not None
    try:
        maintenance_at = B.last_maintenance(_db())
        maintenance_overdue = maintenance_at is None or now - maintenance_at > 3 * B.MAINTENANCE_EVERY_S
        cap_at = B.last_crossing_cap(_db())
    except sqlite3.Error:  # DB nicht lesbar: steht schon unter 'down'
        maintenance_at, maintenance_overdue, cap_at = None, False, None
    # Obergrenze oder Netz-Anteil erreicht: möglicher Spam (purge-reports) oder zu knapp bemessen
    cap_recent = cap_at is not None and 0 <= now - cap_at < B.CAP_ATTENTION_S
    salt_error = check_salt_db(cfg["TV_SALT_DB_PATH"])
    if salt_error and not runtime["salt_db_failed"]:
        current_app.logger.warning("Schlüssel-DB (TV_SALT_DB_PATH) nicht nutzbar, Meldungen scheitern: %s",
                                   salt_error)
    runtime["salt_db_failed"] = salt_error is not None
    due, next_review = due_items(content, today)
    imprint_ok = operator_imprint(cfg) is not None  # dieselbe Prüfung wie Info-Seite und Start-Warnung
    forwarded_ignored = runtime["forwarded_ignored"]
    down = [reason for reason, failed in (("db", db_error is not None), ("salt_db", salt_error is not None))
            if failed]
    # Die Wartung schreibt spätestens alle 10 Min. (Timer, Seitenaufrufe, dieser Abruf); viel älter
    # heißt: Schreiben scheitert schon länger. Der Grund steht dann auch unter 'down'.
    attention = [reason for reason, active in (("due_items", bool(due)), ("imprint", not imprint_ok),
                                                ("proxy", forwarded_ignored),
                                                ("maintenance", maintenance_overdue),
                                                ("crossing_cap", cap_recent)) if active]
    data = {
        "status": "down" if down else ("attention" if attention else "ok"),
        "down": down,
        "db": db_error is None,
        "salt_db": salt_error is None,
        "build": tv().build_id,
        "datasets": {n: {"as_of": content.meta(n)["as_of"], "review_due": content.review_due(n, today)}
                     for n in DATASETS},
        "due_items": due,
        "next_review": next_review,
        "imprint_ok": imprint_ok,
        "proxy": {"trust_proxy": cfg["TV_TRUST_PROXY"], "forwarded_ignored": forwarded_ignored},
        "maintenance_at": iso(maintenance_at) if maintenance_at else None,
        "crossing_cap_at": iso(cap_at) if cap_at else None,
        "attention": attention,
    }
    code, headers = (503 if down else 200), {"Cache-Control": "no-store"}
    if not _local_call():
        return {"status": data["status"], "build": data["build"], "down": down}, code, headers
    return data, code, headers


# Stabile Fehlercodes der JSON-API (Werkzeug-Namen wären sprachlich und versionsabhängig)
API_ERRORS = {400: "invalid", 405: "method_not_allowed", 413: "too_large", 415: "invalid", 429: "ratelimited"}


def _db():
    from .db import get_db
    return get_db()


def asset(path: str) -> str:
    return url_for("static", filename=path, v=tv().asset_hashes.get(path, "0"))


def error_page(code: int) -> str:
    """Fehlerseite ohne Canonical, hreflang, og:url und Sprachwechsel: Unter einem bekannten
    Endpunkt (z. B. /de/grenze/<unbekannt>) zeigten sie sonst auf eine URL, die es nicht gibt.
    Explizit übergebene Werte haben Vorrang vor dem Context-Processor."""
    return render_template("error.html", page=None, code=code, alt_urls={}, switch_urls={})


def error_response(code: int) -> Response:
    """Eigene Fehlerseite mit Statuscode. Sprache aus dem Pfad, sonst aus Accept-Language
    (default_lang) – dann dürfen Caches DE und TR nicht mischen."""
    resp = make_response(error_page(code), code)
    if g.get("lang_negotiated"):
        resp.headers["Vary"] = "Accept-Language"
    return resp


def _legacy_url(entry: dict):
    """Alte URL der Alt-App: dauerhaft weiterleiten (301) oder „gibt es nicht mehr“ (410)."""
    if entry["code"] == 301:
        return redirect(entry["to"], code=301)
    if request.path.startswith("/api/"):
        return {"error": "gone"}, 410
    return error_response(410)


# ------------------------------------------------------------- Registrierung

def register(app: Flask) -> None:
    views = {"home": home, "holidays": holidays, "route": route, "borders": borders,
             "customs": customs, "info": info, "offline": offline}
    for lang in LANGS:
        for page, view in views.items():
            slug = SLUGS[page][lang]
            app.add_url_rule(f"/{lang}/{slug}", endpoint=f"{page}_{lang}", view_func=view,
                             defaults={"lang": lang})
        # Ein Ferienzeitraum als eigene Seite, gleicher Endpunkt: href("holidays", slug=…) baut den Pfad
        app.add_url_rule(f"/{lang}/{SLUGS['holidays'][lang]}/<slug>", endpoint=f"holidays_{lang}",
                         view_func=holidays, defaults={"lang": lang})
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

    @app.template_filter("wbr")
    def wbr(value):
        """Umbruchstelle nach jedem „/“: „Himmelfahrt-/Pfingstferien“ passt sonst als Überschrift
        nicht auf 320 px. Der Text wird zuerst escaped, nur <wbr> ist Markup."""
        return Markup("/<wbr>").join(escape(part) for part in str(value).split("/"))

    @app.url_value_preprocessor
    def pull_lang(_endpoint, values):
        if values and "lang" in values:
            g.lang = values.pop("lang")

    @app.before_request
    def default_lang():
        g.lang_negotiated = False
        if "lang" not in g:
            first = request.path.strip("/").split("/", 1)[0]
            g.lang_negotiated = first not in LANGS
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
        except sqlite3.Error as exc:  # Seite trotzdem ausliefern; /healthz prüft selbst (down: db)
            current_app.logger.warning("Wartung fehlgeschlagen: %s", exc)

    @app.context_processor
    def inject():
        endpoint = request.endpoint or ""
        page = endpoint.rsplit("_", 1)[0] if endpoint.endswith(tuple(f"_{l}" for l in LANGS)) else None
        other = "tr" if g.lang == "de" else "de"
        # alt_urls: dieselbe Seite je Sprache, ohne Query – für Canonical, hreflang und og:url
        # (?land= ergibt keine eigene Seite). switch_urls: Sprachwechsel, behält ?land= bei.
        alt_urls, switch_urls = {}, {}
        if page and page not in ("report", "manifest"):
            args = dict(request.view_args or {})
            params = g.get("alt_params") or {}  # z. B. Ferienzeitraum: Slug je Sprache
            land = request.args.get("land", "").upper()
            land = land if land in tv().radar.states else ""
            for lang in LANGS:
                alt_urls[lang] = url_for(f"{page}_{lang}", **params.get(lang, args))
                switch_urls[lang] = alt_urls[lang] + ("?" + urlencode({"land": land}) if land else "")
        tr = tv().tr
        return {
            "lang": g.lang, "other_lang": other, "t": t, "href": href, "asset": asset,
            "alt_urls": alt_urls, "switch_urls": switch_urls, "abs_url": abs_url, "ago": ago, "iso": iso, "in_days": in_days,
            "fmt_date": lambda d, **kw: fmt_date(d, g.lang, tr, **kw),
            "fmt_range": lambda a, b: fmt_range(a, b, g.lang, tr),
            "fmt_pct": lambda share, digits=1: fmt_pct(share, g.lang, digits),
            "plural": lambda key, n, **kw: tr.plural(g.lang, key, n, **kw),
            "status_text": status_text, "bucket_label": bucket_label, "bucket_count": B.BUCKET_COUNT,
            "client_strings": _client_strings, "build_id": tv().build_id,
            "states": tv().radar.states, "is_due": is_due, "cname": cname, "fmt_ranges": fmt_ranges,
            "wa_url": whatsapp_url, "is_live": is_live, "any_live": any_live, "window_h": B.WINDOW_MIN // 60,
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
        return error_response(403)

    @app.errorhandler(404)
    def not_found(_exc):
        # Alte URLs (data/redirects.json) greifen nur dort, wo sonst 404 käme
        entry = tv().content.redirect_by_path.get(redirect_key(request.path))
        if entry is not None:
            return _legacy_url(entry)
        if request.path.startswith("/api/"):
            return {"error": "not_found"}, 404
        return error_response(404)

    @app.errorhandler(500)
    def server_error(_exc):  # pragma: no cover - Notfallpfad
        if request.path.startswith("/api/"):
            return {"error": "server_error", "degraded": True}, 500
        return error_response(500)

    @app.errorhandler(HTTPException)
    def http_error(exc):
        """Alle übrigen HTTP-Fehler (400, 405, 413 …): unter /api/ immer JSON, sonst die eigene
        zweisprachige Fehlerseite statt der englischen Standardseite von Werkzeug."""
        code = exc.code or 500
        if request.path.startswith("/api/"):
            resp = jsonify({"error": API_ERRORS.get(code, "http_error")})
            resp.status_code = code
        else:
            resp = error_response(code)
        for name, value in exc.get_headers():
            if name.lower() != "content-type":  # z. B. Allow bei 405
                resp.headers[name] = value
        return resp
