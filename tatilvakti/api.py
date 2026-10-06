"""JSON-API für Live-Daten. Fehler sind nie Abstürze: immer JSON mit Code."""
from __future__ import annotations

from flask import Flask, current_app, request

from . import borders as B
from .db import get_db
from .views import iso, now_ts, same_origin, tv


def _crossing_json(c: dict, st: dict) -> dict:
    return {
        "id": c["id"], "name": c["name"], "short": c["short"], "main": c["main"],
        "countries": c["countries"],
        "directions": {d: {**s, "last_at_iso": iso(s["last_at"]) if s["last_at"] else None} for d, s in st.items()},
    }


def borders_index():
    crossings = tv().content.crossings["crossings"]
    now = now_ts()
    statuses = B.statuses(get_db(), [c["id"] for c in crossings], now)
    return {
        "generated_at": now,
        "generated_at_iso": iso(now),
        "window_min": B.WINDOW_MIN,
        "source": "crowd",
        "crossings": [_crossing_json(c, statuses[c["id"]]) for c in crossings],
    }


def border_detail(cid: str):
    c = tv().content.crossing_by_id.get(cid)
    if c is None:
        return {"error": "not_found"}, 404
    now = now_ts()
    db = get_db()
    data = _crossing_json(c, B.statuses(db, [cid], now)[cid])
    data["pattern"] = {d: B.hourly_pattern(db, cid, d, c["tz"], now) for d in B.DIRECTIONS}
    data["generated_at"] = now
    return data


def border_report(cid: str):
    if cid not in tv().content.crossing_by_id:
        return {"error": "not_found"}, 404
    if not same_origin(require=False):  # JSON braucht ohnehin CORS-Preflight; hier zusätzlich
        return {"error": "forbidden"}, 403
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return {"error": "invalid", "detail": "json_expected"}, 400
    if payload.get("website"):  # Honeypot
        return {"ok": True}, 201
    try:
        B.add_report(get_db(), cid, payload.get("direction"), payload.get("bucket"),
                     request.remote_addr or "0.0.0.0", now_ts(), payload.get("observed_at"))
    except B.InvalidReport as exc:
        return {"error": "invalid", "detail": str(exc)}, 400
    except B.StaleReport:
        return {"error": "stale"}, 422
    except B.RateLimited as exc:
        return {"error": "ratelimited", "detail": str(exc)}, 429
    now = now_ts()
    st = B.statuses(get_db(), [cid], now)[cid]
    current_app.logger.info("report crossing=%s direction=%s", cid, payload.get("direction"))
    return {"ok": True, "crossing": _crossing_json(tv().content.crossing_by_id[cid], st)}, 201


def register(app: Flask) -> None:
    app.add_url_rule("/api/v1/borders", endpoint="api_borders", view_func=borders_index)
    app.add_url_rule("/api/v1/borders/<cid>", endpoint="api_border", view_func=border_detail)
    app.add_url_rule("/api/v1/borders/<cid>/reports", endpoint="api_report", view_func=border_report,
                     methods=["POST"])
