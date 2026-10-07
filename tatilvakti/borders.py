"""Grenze live: Wartezeit-Meldungen von Reisenden, Aggregation, Spam-Schutz.

Ehrlichkeitsregeln:
- Wir speichern und zeigen Bereiche (z. B. „30–60 Min.“), keine Scheingenauigkeit.
- Ein Status gilt nur mit Meldungen aus den letzten WINDOW_MIN Minuten, sonst „keine
  aktuellen Meldungen“ plus Alter der letzten Meldung.
- Eine Stimme pro Anschluss: Im Median zählt je Übergang und Richtung nur die jüngste Meldung
  eines Anschlusses im Fenster. Anschluss heißt: dieselbe IPv4-Adresse bzw. dasselbe IPv6-/56
  (ein Heimanschluss bekommt oft ein /56 mit 256 /64-Netzen), erkannt am Tages-Prüfwert `net`.
  Wer alle 20 Minuten neu meldet, ersetzt nur seine eigene Stimme.
- Median statt Mittelwert: einzelne Ausreißer und Trolle verschieben den Status kaum. Bei
  gerader Anzahl zählt der höhere Wert (lieber vorsichtig als zu optimistisch).

Limits (Prüfwerte statt IP-Adressen, siehe client_keys):
- je Client (IPv4-Adresse bzw. IPv6-/64): SAME_SPOT_COOLDOWN_MIN Minuten Sperre je Übergang
  und Richtung, höchstens MAX_REPORTS_PER_HOUR Meldungen pro Stunde insgesamt;
- je Anschluss (IPv4-Adresse bzw. IPv6-/56): höchstens NET_REPORTS_PER_HOUR Meldungen pro
  Stunde je Übergang und Richtung – so viele, wie eine IPv4-Adresse wegen der Sperre ohnehin
  schafft. Damit füllt ein einzelner Anschluss die Obergrenze nicht allein;
- je Übergang und Richtung über alle: CROSSING_CAP Meldungen in CROSSING_CAP_MIN Minuten,
  darüber 429 „crossing_busy“ und eine Log-Warnung.

Grenzen des Schutzes (bewusst offen benannt): Wer viele IPv4-Adressen oder viele /56-Netze hat
(z. B. ein /48 aus einem Tunnel-Angebot), bekommt mehrere Stimmen und kann die Obergrenze
füllen; dann bekommen auch ehrliche Melder kurz 429. Dagegen helfen die Log-Warnung und das
Aufräumen per `flask --app tatilvakti purge-reports`. Umgekehrt teilen sich Menschen hinter
einer gemeinsamen IPv4-Adresse (CGNAT) oder im selben /56 eine Stimme und die Limits. Beim
Schlüsselwechsel um 00:00 UTC bekommt ein Client einen neuen Prüfwert und kann kurz doppelt zählen.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import os
import re
import sqlite3
import statistics
import threading
from datetime import datetime, timezone

from .db import salt_db

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

log = logging.getLogger(__name__)

DIRECTIONS = ("to_tr", "to_de")
BUCKET_COUNT = 6
# Repräsentative Minuten je Bereich (nur für die Höhe der Balken im Tagesverlauf)
BUCKET_MINUTES = (8, 22, 45, 90, 180, 300)
WINDOW_MIN = 120
MAX_REPORT_AGE_MIN = 90
SAME_SPOT_COOLDOWN_MIN = 20
MAX_REPORTS_PER_HOUR = 10
# Je Anschluss (IPv4-Adresse bzw. IPv6-/56) und Übergang und Richtung, pro Stunde
NET_REPORTS_PER_HOUR = 3
# Obergrenze je Übergang und Richtung über alle Clients: bremst Fluten aus vielen Adressen
CROSSING_CAP = 30
CROSSING_CAP_MIN = 10
CLIENT_HASH_TTL_H = 48
RETENTION_DAYS = 400
MAINTENANCE_EVERY_S = 600
STATUS_CACHE_S = 15
PATTERN_CACHE_S = 300
IPV6_CLIENT_PREFIX = 64
IPV6_NET_PREFIX = 56
_MAX_ABS_INT = 10 ** 12
_INT_TEXT = re.compile(r"-?[0-9]{1,12}")


class ReportError(Exception):
    code = "error"


class InvalidReport(ReportError):
    code = "invalid"


class RateLimited(ReportError):
    code = "ratelimited"


class CrossingBusy(RateLimited):
    """Obergrenze des Übergangs erreicht – betrifft alle, nicht nur diesen Client."""
    code = "busy"


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


_unparsable_warned = False


def _parse_ip(value):
    """IP-Adresse aus REMOTE_ADDR lesen, auch mit Klammern oder Port ('[2001:db8::1]:443',
    '203.0.113.7:4444'). IPv4 in IPv6 (::ffff:a.b.c.d) gilt als IPv4. Unlesbar: None."""
    global _unparsable_warned
    text = str(value).strip()
    if text.startswith("["):
        end = text.find("]")
        if end != -1:
            text = text[1:end]
    elif text.count(":") == 1:  # IPv4 mit Port; IPv6 hat immer mindestens zwei Doppelpunkte
        text = text.split(":", 1)[0]
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        if not _unparsable_warned:
            _unparsable_warned = True
            log.warning("Unlesbare Client-Adresse: Der Spam-Schutz zählt sie als eigenen Client. "
                        "Header-Kette des Reverse-Proxys prüfen (X-Forwarded-For, TV_TRUST_PROXY).")
        return None
    if addr.version == 6 and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def _ipv6_network(addr, prefix: int) -> str:
    host_bits = 128 - prefix
    return str(ipaddress.IPv6Network((int(addr) >> host_bits << host_bits, prefix)))


def normalize_ip(ip: str) -> str:
    """Die Adresse, die für den Spam-Schutz als ein Client gilt.

    Ein IPv6-Anschluss bekommt meist ein ganzes /64-Netz, und jedes Gerät darin kann
    beliebig viele Adressen nutzen. Deshalb zählt das /64 als ein Client. IPv4 bleibt
    unverändert, IPv4 in IPv6 (::ffff:a.b.c.d) gilt als IPv4. Unlesbares bleibt, wie es ist.
    """
    addr = _parse_ip(ip)
    if addr is None:
        return str(ip)
    return _ipv6_network(addr, IPV6_CLIENT_PREFIX) if addr.version == 6 else str(addr)


def normalize_net(ip: str) -> str:
    """Der Anschluss hinter einer Adresse: bei IPv6 das /56, bei IPv4 die Adresse selbst.

    Heimanschlüsse bekommen oft ein /56 (256 /64-Netze), daraus ließen sich sonst 256 Clients
    machen. Gröber (/48) wäre unfair: Dann teilten sich viele fremde Mobilfunkkunden eine Stimme.
    """
    addr = _parse_ip(ip)
    if addr is None:
        return str(ip)
    return _ipv6_network(addr, IPV6_NET_PREFIX) if addr.version == 6 else str(addr)


def _day_salt(conn: sqlite3.Connection, now: int) -> bytes:
    day = _utc_day(now)
    with salt_db(conn) as sconn:
        row = sconn.execute("SELECT salt FROM salts WHERE day = ?", (day,)).fetchone()
        if row is None:
            sconn.execute("INSERT OR IGNORE INTO salts (day, salt) VALUES (?, ?)", (day, os.urandom(32)))
            row = sconn.execute("SELECT salt FROM salts WHERE day = ?", (day,)).fetchone()
    return row["salt"]


def _digest(salt: bytes, text: str) -> str:
    return hmac.new(salt, text.encode(), hashlib.sha256).hexdigest()[:32]


def client_keys(conn: sqlite3.Connection, ip: str, now: int) -> tuple[str, str]:
    """Prüfwerte (client, net): HMAC von Client und Anschluss mit dem täglich wechselnden Schlüssel.

    Die IP wird nie gespeichert. Der Schlüssel liegt in der eigenen Schlüssel-DB
    (TV_SALT_DB_PATH), nie in der gesicherten Haupt-DB. Bei IPv4 sind beide Werte gleich.
    """
    salt = _day_salt(conn, now)
    return _digest(salt, normalize_ip(ip)), _digest(salt, normalize_net(ip))


def client_key(conn: sqlite3.Connection, ip: str, now: int) -> str:
    """Nur der Client-Prüfwert (IPv4-Adresse bzw. IPv6-/64), siehe client_keys."""
    return client_keys(conn, ip, now)[0]


def _strict_int(value, field: str) -> int:
    """Nur echte Ganzzahlen: int (kein bool) oder Ziffern-Text aus Formularen.

    float, Infinity, NaN, 1e400 und Riesenzahlen sind ungültig. So erreicht kein
    OverflowError mehr die Datenbank und kein Traceback das Log.
    """
    if isinstance(value, bool):
        raise InvalidReport(field)
    if isinstance(value, int) and abs(value) <= _MAX_ABS_INT:
        return value
    if isinstance(value, str) and _INT_TEXT.fullmatch(value):
        return int(value)
    raise InvalidReport(field)


def _bump_revision(conn: sqlite3.Connection, deleted: bool = False) -> None:
    """Meldungsstand hochzählen: macht den Status-Cache in allen Prozessen ungültig.

    deleted=True (Löschen per purge-reports oder Wartung) zählt zusätzlich purge_rev hoch,
    daran hängt der Cache des Tagesverlaufs.
    """
    for key in ("reports_rev", "purge_rev") if deleted else ("reports_rev",):
        conn.execute("INSERT INTO kv (key, value) VALUES (?, '1') "
                     "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1", (key,))


def _revision(conn: sqlite3.Connection, key: str = "reports_rev") -> str | None:
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


_cap_warned: dict[tuple[str, str, str], int] = {}


def _warn_crossing_cap(conn, crossing: str, direction: str, now: int) -> None:
    """Log-Warnung bei voller Obergrenze, je Übergang und Richtung höchstens alle CROSSING_CAP_MIN Minuten."""
    key = (getattr(conn, "path", ""), crossing, direction)
    last = _cap_warned.get(key)
    if last is not None and 0 <= now - last < CROSSING_CAP_MIN * 60:
        return
    _cap_warned[key] = now
    log.warning("Obergrenze erreicht: %s Meldungen in %s Min. für crossing=%s direction=%s. "
                "Möglicher Spam, ggf. mit 'flask --app tatilvakti purge-reports' aufräumen.",
                CROSSING_CAP, CROSSING_CAP_MIN, crossing, direction)


def add_report(conn: sqlite3.Connection, crossing: str, direction: str, bucket, ip: str,
               now: int, observed_at=None) -> int:
    if direction not in DIRECTIONS:
        raise InvalidReport("direction")
    bucket = _strict_int(bucket, "bucket")
    if not 0 <= bucket < BUCKET_COUNT:
        raise InvalidReport("bucket")

    if observed_at is None or observed_at == "":
        observed = now
    else:
        observed = _strict_int(observed_at, "observed_at")
        if observed > now:
            observed = now  # Uhr des Handys geht vor – auf jetzt begrenzen
        if observed < now - MAX_REPORT_AGE_MIN * 60:
            raise StaleReport("observed_at")

    client, net = client_keys(conn, ip, now)
    # Prüfen und Speichern in EINER Schreibtransaktion: BEGIN IMMEDIATE sperrt sofort für
    # andere Schreiber, parallele Requests desselben Clients sehen also die erste Meldung.
    conn.execute("BEGIN IMMEDIATE")
    try:
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
        # Vor der Obergrenze: Ein einzelner Anschluss (z. B. ein /56 mit 256 /64-Netzen) soll
        # sie nicht allein füllen und damit ehrliche Melder aussperren können
        per_net = conn.execute(
            "SELECT COUNT(*) FROM reports WHERE net = ? AND crossing = ? AND direction = ? AND created_at > ?",
            (net, crossing, direction, now - 3600),
        ).fetchone()[0]
        if per_net >= NET_REPORTS_PER_HOUR:
            raise RateLimited("net_per_hour")
        # INDEXED BY: nur die Meldungen der letzten Minuten lesen, nie den ganzen Übergang
        busy = conn.execute(
            "SELECT COUNT(*) FROM reports INDEXED BY idx_reports_created "
            "WHERE created_at > ? AND crossing = ? AND direction = ?",
            (now - CROSSING_CAP_MIN * 60, crossing, direction),
        ).fetchone()[0]
        if busy >= CROSSING_CAP:
            _warn_crossing_cap(conn, crossing, direction, now)
            raise CrossingBusy("crossing_busy")
        cur = conn.execute(
            "INSERT INTO reports (crossing, direction, bucket, observed_at, created_at, client, net) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (crossing, direction, bucket, observed, now, client, net),
        )
        _bump_revision(conn)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    try:
        maintenance(conn, now)
    except sqlite3.Error as exc:  # Die Meldung ist gespeichert; Wartung holt der nächste Lauf nach
        log.warning("Wartung nach Meldung fehlgeschlagen: %s", exc)
    return cur.lastrowid


def maintenance(conn: sqlite3.Connection, now: int, force: bool = False) -> dict | None:
    """Datensparsamkeit: Prüfwerte nach 48 h, Meldungen nach RETENTION_DAYS Tagen und
    Tagesschlüssel nach dem Tageswechsel (UTC) löschen.

    Läuft über alle Prozesse höchstens alle MAINTENANCE_EVERY_S Sekunden (Zeitstempel in kv),
    mit force=True sofort. Liefert die Zahl der gelöschten Einträge, None wenn übersprungen.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT value FROM kv WHERE key = 'maintenance_at'").fetchone()
        if not force and row and 0 <= now - int(row["value"]) < MAINTENANCE_EVERY_S:
            conn.execute("COMMIT")
            return None
        # Ein Intervall Vorlauf: Die Wartung läuft nur alle MAINTENANCE_EVERY_S Sekunden, so
        # wird trotzdem kein Prüfwert älter als die zugesagten 48 h. Die Limits brauchen nur 1 h.
        # net wird nur zusammen mit client gesetzt, also auch zusammen gelöscht.
        cleared = conn.execute(
            "UPDATE reports SET client = NULL, net = NULL WHERE client IS NOT NULL AND created_at <= ?",
            (now - CLIENT_HASH_TTL_H * 3600 + MAINTENANCE_EVERY_S,)).rowcount
        deleted = conn.execute("DELETE FROM reports WHERE observed_at < ?",
                               (now - RETENTION_DAYS * 86400,)).rowcount
        if deleted:
            _bump_revision(conn, deleted=True)
        conn.execute("INSERT INTO kv (key, value) VALUES ('maintenance_at', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(now),))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    # Erst nach dem COMMIT: nie die Schlüssel-DB sperren, während die Haupt-DB gesperrt ist.
    # Prüfwerte entstehen nur mit dem heutigen Schlüssel; ältere werden nicht mehr gebraucht.
    with salt_db(conn) as sconn:
        salts = sconn.execute("DELETE FROM salts WHERE day < ?", (_utc_day(now),)).rowcount
    return {"clients_cleared": cleared, "reports_deleted": deleted, "salts_deleted": salts}


def last_maintenance(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT value FROM kv WHERE key = 'maintenance_at'").fetchone()
    return int(row["value"]) if row else None


def purge_reports(conn: sqlite3.Connection, crossing: str, since: int, until: int,
                  direction: str | None = None, dry_run: bool = False) -> int:
    """Meldungen eines Übergangs nach Eingangszeit (created_at, since ≤ t < until) löschen.

    Zum gezielten Aufräumen nach Spam. Liefert die Anzahl (bei dry_run: die gelöscht würde).
    """
    where = "crossing = ? AND created_at >= ? AND created_at < ?"
    params: list = [crossing, since, until]
    if direction:
        where += " AND direction = ?"
        params.append(direction)
    if dry_run:
        return conn.execute(f"SELECT COUNT(*) FROM reports WHERE {where}", params).fetchone()[0]
    conn.execute("BEGIN IMMEDIATE")
    try:
        count = conn.execute(f"DELETE FROM reports WHERE {where}", params).rowcount
        if count:
            _bump_revision(conn, deleted=True)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return count


def _summarize(buckets: list[int], last_at: int | None) -> dict:
    if buckets:
        median = statistics.median_high(buckets)
        return {"state": "live", "bucket": median, "level": level_for_bucket(median),
                "count": len(buckets), "last_at": last_at, "window_min": WINDOW_MIN}
    return {"state": "none", "bucket": None, "level": "none", "count": 0,
            "last_at": last_at, "window_min": WINDOW_MIN}


class _TimedCache:
    """Kurzzeit-Cache im Prozess, Schlüssel beginnt mit der DB-Datei.

    Ein Eintrag gilt, solange die Uhr – auch die Test-Uhr TV_CLOCK – weniger als ttl()
    Sekunden weiter und nicht zurückgelaufen ist und der mitgegebene Stand (rev) gleich ist.
    """

    MAX_ENTRIES = 128

    def __init__(self, ttl) -> None:
        self._ttl = ttl  # Callable: liest die Modul-Konstante bei jedem Zugriff
        self._lock = threading.Lock()
        self._entries: dict[tuple, tuple[str | None, int, dict]] = {}

    def enabled(self, path: str) -> bool:
        return bool(path) and path != ":memory:" and self._ttl() > 0

    def get(self, key: tuple, rev: str | None, now: int) -> dict | None:
        with self._lock:
            entry = self._entries.get(key)
        if entry is None:
            return None
        cached_rev, cached_now, value = entry
        if cached_rev != rev or not 0 <= now - cached_now < self._ttl():
            return None
        return value

    def put(self, key: tuple, rev: str | None, now: int, value: dict) -> None:
        with self._lock:
            if len(self._entries) >= self.MAX_ENTRIES:
                self._entries.clear()
            self._entries[key] = (rev, now, value)


_status_cache = _TimedCache(lambda: STATUS_CACHE_S)
_pattern_cache = _TimedCache(lambda: PATTERN_CACHE_S)


def statuses(conn: sqlite3.Connection, crossing_ids, now: int) -> dict[str, dict[str, dict]]:
    """Aktueller Status für die Übergänge und beide Richtungen, bis STATUS_CACHE_S Sekunden gecacht.

    Der Cache hängt am Meldungsstand kv.reports_rev und gilt damit über Prozessgrenzen: Eine
    Meldung über einen anderen gunicorn-Worker oder ein purge-reports im Terminal macht ihn
    sofort ungültig. Nur wer an der App vorbei in die DB schreibt, sieht bis zu 15 s alte Werte.
    """
    ids = tuple(crossing_ids)
    path = getattr(conn, "path", "")
    cacheable = _status_cache.enabled(path)
    rev = _revision(conn)
    result = _status_cache.get((path, ids), rev, now) if cacheable else None
    if result is None:
        result = _compute_statuses(conn, ids, now)
        if cacheable:
            _status_cache.put((path, ids), rev, now, result)
    # Kopie: Aufrufer dürfen die Dicts verändern, ohne den Cache zu verfälschen
    return {cid: {d: dict(st) for d, st in dirs.items()} for cid, dirs in result.items()}


def _compute_statuses(conn: sqlite3.Connection, ids: tuple[str, ...], now: int) -> dict[str, dict[str, dict]]:
    if not ids:
        return {}
    since = now - WINDOW_MIN * 60
    marks = ",".join("?" * len(ids))
    # Eine Stimme pro Anschluss: spätere Meldungen desselben Prüfwerts überschreiben frühere.
    # Altbestand ohne net zählt je Client, Meldungen ganz ohne Prüfwert (nach 48 h) einzeln.
    # direction IN (…) gehört dazu: Erst damit wird jedes Paar ein Index-Seek auf das Zeitfenster.
    votes: dict[tuple, int] = {}
    for row in conn.execute(
        f"SELECT id, crossing, direction, bucket, client, net FROM reports "
        f"WHERE crossing IN ({marks}) AND direction IN ({','.join('?' * len(DIRECTIONS))}) "
        f"AND observed_at >= ? AND observed_at <= ? ORDER BY observed_at, id",
        (*ids, *DIRECTIONS, since, now),
    ):
        if row["net"] is not None:
            voter = ("net", row["net"])
        elif row["client"] is not None:
            voter = ("client", row["client"])
        else:
            voter = ("report", row["id"])
        votes[(row["crossing"], row["direction"], voter)] = row["bucket"]
    fresh: dict[tuple[str, str], list[int]] = {}
    for (cid, direction, _voter), bucket in votes.items():
        fresh.setdefault((cid, direction), []).append(bucket)

    # Letzte Meldung je Übergang und Richtung: pro Paar ein Index-Seek statt eines Scans
    pairs = [(cid, d) for cid in ids for d in DIRECTIONS]
    values = ",".join("(?, ?)" for _ in pairs)
    last = {
        (row["crossing"], row["direction"]): row["last_at"]
        for row in conn.execute(
            f"WITH k(crossing, direction) AS (VALUES {values}) "
            "SELECT k.crossing, k.direction, (SELECT MAX(r.observed_at) FROM reports r "
            "WHERE r.crossing = k.crossing AND r.direction = k.direction AND r.observed_at <= ?) AS last_at "
            "FROM k",
            (*[v for pair in pairs for v in pair], now),
        )
    }
    return {
        cid: {d: _summarize(fresh.get((cid, d), []), last.get((cid, d))) for d in DIRECTIONS}
        for cid in ids
    }


def hourly_pattern(conn: sqlite3.Connection, crossing: str, direction: str, tz_name: str, now: int,
                   weeks: int = 8, min_reports: int = 3) -> dict:
    """Median-Wartezeit je Ortsstunde aus den letzten Wochen. Stunden mit zu wenig Daten bleiben leer.

    Reine Historie über Wochen, deshalb bis PATTERN_CACHE_S Sekunden gecacht – auch über neue
    Meldungen hinweg. Sonst würde eine Meldungsflut die teure Rechnung bei jedem Aufruf auslösen.
    Gelöschte Meldungen (purge-reports, Wartung) machen den Cache über purge_rev sofort ungültig.
    """
    path = getattr(conn, "path", "")
    key = (path, crossing, direction, tz_name, weeks, min_reports)
    cacheable = _pattern_cache.enabled(path)
    rev = _revision(conn, "purge_rev") if cacheable else None
    result = _pattern_cache.get(key, rev, now) if cacheable else None
    if result is None:
        result = _compute_pattern(conn, crossing, direction, tz_name, now, weeks, min_reports)
        if cacheable:
            _pattern_cache.put(key, rev, now, result)
    return {**result, "slots": [dict(slot) for slot in result["slots"]]}


def _compute_pattern(conn: sqlite3.Connection, crossing: str, direction: str, tz_name: str, now: int,
                     weeks: int, min_reports: int) -> dict:
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
    """Meldungen seit since (Eingangszeit), über den Index auf created_at."""
    return conn.execute("SELECT COUNT(*) FROM reports WHERE created_at >= ?", (since,)).fetchone()[0]
