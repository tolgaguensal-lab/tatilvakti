"""Grenze live: Wartezeit-Meldungen von Reisenden, Aggregation, Spam-Schutz.

Ehrlichkeitsregeln:
- Wir speichern und zeigen Bereiche (z. B. „30–60 Min.“), keine Scheingenauigkeit.
- Ein Status gilt nur mit Meldungen aus den letzten WINDOW_MIN Minuten, sonst „keine
  aktuellen Meldungen“ plus Alter der letzten Meldung.
- Median statt Mittelwert: einzelne Ausreißer/Trolle verschieben den Status kaum. Bei
  gerader Anzahl zählt der höhere Wert (lieber vorsichtig als zu optimistisch).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

DIRECTIONS = ("to_tr", "to_de")
BUCKET_COUNT = 6
# Repräsentative Minuten je Bereich (nur für die Höhe der Balken im Tagesverlauf)
BUCKET_MINUTES = (8, 22, 45, 90, 180, 300)
WINDOW_MIN = 120
MAX_REPORT_AGE_MIN = 90
SAME_SPOT_COOLDOWN_MIN = 20
MAX_REPORTS_PER_HOUR = 10
CLIENT_HASH_TTL_H = 48
RETENTION_DAYS = 400


class ReportError(Exception):
    code = "error"


class InvalidReport(ReportError):
    code = "invalid"


class RateLimited(ReportError):
    code = "ratelimited"


class StaleReport(ReportError):
    code = "stale"


def level_for_bucket(bucket: int | None) -> str:
    if bucket is None:
        return "none"
    if bucket <= 1:
        return "ok"
    if bucket <= 3:
        return "mid"
    return "bad"


def _utc_day(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()


def client_key(conn: sqlite3.Connection, ip: str, now: int) -> str:
    """HMAC der IP mit einem täglich wechselnden Zufallsschlüssel. Die IP wird nie gespeichert."""
    day = _utc_day(now)
    row = conn.execute("SELECT salt FROM salts WHERE day = ?", (day,)).fetchone()
    if row is None:
        salt = os.urandom(32)
        conn.execute("INSERT OR IGNORE INTO salts (day, salt) VALUES (?, ?)", (day, salt))
        row = conn.execute("SELECT salt FROM salts WHERE day = ?", (day,)).fetchone()
    return hmac.new(row["salt"], ip.encode(), hashlib.sha256).hexdigest()[:32]


def add_report(conn: sqlite3.Connection, crossing: str, direction: str, bucket, ip: str,
               now: int, observed_at=None) -> int:
    if direction not in DIRECTIONS:
        raise InvalidReport("direction")
    try:
        bucket = int(bucket)
    except (TypeError, ValueError):
        raise InvalidReport("bucket") from None
    if not 0 <= bucket < BUCKET_COUNT:
        raise InvalidReport("bucket")

    if observed_at in (None, ""):
        observed = now
    else:
        try:
            observed = int(observed_at)
        except (TypeError, ValueError):
            raise InvalidReport("observed_at") from None
        if observed > now:
            observed = now  # Uhr des Handys geht vor – auf jetzt begrenzen
        if observed < now - MAX_REPORT_AGE_MIN * 60:
            raise StaleReport("observed_at")

    client = client_key(conn, ip, now)
    recent_same = conn.execute(
        "SELECT 1 FROM reports WHERE client = ? AND crossing = ? AND direction = ? AND created_at > ? LIMIT 1",
        (client, crossing, direction, now - SAME_SPOT_COOLDOWN_MIN * 60),
    ).fetchone()
    if recent_same:
        raise RateLimited("same_spot")
    per_hour = conn.execute(
        "SELECT COUNT(*) FROM reports WHERE client = ? AND created_at > ?", (client, now - 3600)
    ).fetchone()[0]
    if per_hour >= MAX_REPORTS_PER_HOUR:
        raise RateLimited("per_hour")

    cur = conn.execute(
        "INSERT INTO reports (crossing, direction, bucket, observed_at, created_at, client) VALUES (?, ?, ?, ?, ?, ?)",
        (crossing, direction, bucket, observed, now, client),
    )
    maintenance(conn, now)
    return cur.lastrowid


def maintenance(conn: sqlite3.Connection, now: int, force: bool = False) -> bool:
    """Datensparsamkeit: Prüfwerte nach 48 h löschen, alte Meldungen und Schlüssel entfernen."""
    row = conn.execute("SELECT value FROM kv WHERE key = 'maintenance_at'").fetchone()
    if not force and row and int(row["value"]) > now - 600:
        return False
    conn.execute("UPDATE reports SET client = NULL WHERE client IS NOT NULL AND created_at < ?",
                 (now - CLIENT_HASH_TTL_H * 3600,))
    conn.execute("DELETE FROM reports WHERE observed_at < ?", (now - RETENTION_DAYS * 86400,))
    yesterday = (datetime.fromtimestamp(now, tz=timezone.utc).date() - timedelta(days=1)).isoformat()
    conn.execute("DELETE FROM salts WHERE day < ?", (yesterday,))
    conn.execute("INSERT INTO kv (key, value) VALUES ('maintenance_at', ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(now),))
    return True


def _summarize(buckets: list[int], last_at: int | None) -> dict:
    if buckets:
        median = statistics.median_high(buckets)
        return {"state": "live", "bucket": median, "level": level_for_bucket(median),
                "count": len(buckets), "last_at": last_at, "window_min": WINDOW_MIN}
    return {"state": "none", "bucket": None, "level": "none", "count": 0,
            "last_at": last_at, "window_min": WINDOW_MIN}


def statuses(conn: sqlite3.Connection, crossing_ids, now: int) -> dict[str, dict[str, dict]]:
    """Aktueller Status für alle Übergänge und beide Richtungen (2 Queries)."""
    since = now - WINDOW_MIN * 60
    fresh: dict[tuple[str, str], list[int]] = {}
    for row in conn.execute(
        "SELECT crossing, direction, bucket FROM reports WHERE observed_at >= ? ORDER BY observed_at",
        (since,),
    ):
        fresh.setdefault((row["crossing"], row["direction"]), []).append(row["bucket"])
    last = {
        (row["crossing"], row["direction"]): row["last_at"]
        for row in conn.execute(
            "SELECT crossing, direction, MAX(observed_at) AS last_at FROM reports "
            "WHERE observed_at <= ? GROUP BY crossing, direction", (now,))
    }
    return {
        cid: {d: _summarize(fresh.get((cid, d), []), last.get((cid, d))) for d in DIRECTIONS}
        for cid in crossing_ids
    }


def hourly_pattern(conn: sqlite3.Connection, crossing: str, direction: str, tz_name: str, now: int,
                   weeks: int = 8, min_reports: int = 3) -> dict:
    """Median-Wartezeit je Ortsstunde aus den letzten Wochen. Stunden mit zu wenig Daten bleiben leer."""
    tz = ZoneInfo(tz_name) if ZoneInfo else timezone.utc
    hours: list[list[int]] = [[] for _ in range(24)]
    for row in conn.execute(
        "SELECT bucket, observed_at FROM reports WHERE crossing = ? AND direction = ? AND observed_at >= ?",
        (crossing, direction, now - weeks * 7 * 86400),
    ):
        hour = datetime.fromtimestamp(row["observed_at"], tz=tz).hour
        hours[hour].append(row["bucket"])
    slots = []
    for hour, buckets in enumerate(hours):
        if len(buckets) >= min_reports:
            median = statistics.median_high(buckets)
            slots.append({"hour": hour, "count": len(buckets), "bucket": median,
                          "minutes": BUCKET_MINUTES[median], "level": level_for_bucket(median)})
        else:
            slots.append({"hour": hour, "count": len(buckets), "bucket": None, "minutes": None, "level": "none"})
    usable = sum(1 for s in slots if s["bucket"] is not None)
    return {"weeks": weeks, "slots": slots, "usable_hours": usable, "enough": usable >= 4}


def count_since(conn: sqlite3.Connection, since: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM reports WHERE created_at >= ?", (since,)).fetchone()[0]
