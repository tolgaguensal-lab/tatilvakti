"""SQLite-Zugriff. Zwei Dateien im WAL-Modus, Verbindung pro Request.

- Haupt-DB (TV_DB_PATH): Meldungen und kv. Diese Datei wird gesichert.
- Schlüssel-DB (TV_SALT_DB_PATH): nur die Tagesschlüssel für den Spam-Prüfwert. Sie wird
  nie gesichert und darf auf tmpfs liegen. So enthält keine Sicherung Schlüssel und Prüfwert
  zusammen, der Prüfwert lässt sich aus einem Backup also nicht auf die IP zurückrechnen.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from flask import current_app, g

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id          INTEGER PRIMARY KEY,
    crossing    TEXT    NOT NULL,
    direction   TEXT    NOT NULL CHECK (direction IN ('to_tr', 'to_de')),
    bucket      INTEGER NOT NULL CHECK (bucket BETWEEN 0 AND 5),
    observed_at INTEGER NOT NULL,   -- Unix-Sekunden (UTC): wann die Wartezeit erlebt wurde
    created_at  INTEGER NOT NULL,   -- Unix-Sekunden (UTC): wann die Meldung ankam
    client      TEXT                -- Tages-Prüfwert gegen Spam, wird nach 48 h gelöscht
);
CREATE INDEX IF NOT EXISTS idx_reports_lookup ON reports (crossing, direction, observed_at);
-- Nur Meldungen mit Prüfwert (letzte 48 h): Spam-Limits und Wartung lesen nie den Altbestand
DROP INDEX IF EXISTS idx_reports_client;
CREATE INDEX IF NOT EXISTS idx_reports_client_hash ON reports (client, created_at) WHERE client IS NOT NULL;
-- Aufbewahrungsfrist (observed_at) bzw. Obergrenze und Zähler nach Eingang (created_at)
CREATE INDEX IF NOT EXISTS idx_reports_observed ON reports (observed_at);
CREATE INDEX IF NOT EXISTS idx_reports_created ON reports (created_at);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

SALT_SCHEMA = """
CREATE TABLE IF NOT EXISTS salts (
    day  TEXT PRIMARY KEY,          -- UTC-Datum
    salt BLOB NOT NULL
);
"""


class Connection(sqlite3.Connection):
    """sqlite3-Verbindung, die ihre Datei und die zugehörige Schlüssel-DB kennt."""

    path: str = ""
    salt_path: str = ""


def default_salt_path(db_path: str) -> str:
    """Schlüssel-DB neben der Haupt-DB: instance/tatilvakti.db → instance/tatilvakti-salts.db."""
    path = Path(db_path)
    return str(path.with_name(path.stem + "-salts.db"))


def connect(path: str, salt_path: str | None = None) -> Connection:
    conn = sqlite3.connect(path, timeout=5, isolation_level=None, factory=Connection)
    conn.path = path
    conn.salt_path = salt_path or default_salt_path(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    # Gelöschte Prüfwerte und Schlüssel mit Nullen überschreiben, statt sie als freien
    # Speicher in der Datei liegen zu lassen
    conn.execute("PRAGMA secure_delete=ON")
    return conn


@contextmanager
def salt_db(conn: Connection):
    """Eigene, kurze Verbindung zur Schlüssel-DB der übergebenen Haupt-DB-Verbindung."""
    sconn = connect(conn.salt_path)
    try:
        yield sconn
    finally:
        sconn.close()


def init_db(path: str, salt_path: str | None = None) -> None:
    salt_path = salt_path or default_salt_path(path)
    for file in (path, salt_path):
        Path(file).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path, salt_path)
    try:
        conn.executescript(SCHEMA)
        _drop_legacy_salts(conn)
    finally:
        conn.close()
    sconn = connect(salt_path)
    try:
        sconn.executescript(SALT_SCHEMA)
    finally:
        sconn.close()


def _drop_legacy_salts(conn: Connection) -> None:
    """Früher lagen die Tagesschlüssel in der Haupt-DB. Beim Start entfernen.

    VACUUM schreibt die Datei neu, damit auch früher gelöschte Schlüssel und Prüfwerte nicht
    mehr als Reste im freien Speicher stehen. Läuft nur einmal (solange die Tabelle existiert).
    """
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'salts'").fetchone() is None:
        return
    conn.execute("DROP TABLE IF EXISTS salts")
    try:
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.OperationalError as exc:  # pragma: no cover - parallel startender Worker
        log.warning("Alte Schlüssel-Tabelle entfernt, VACUUM übersprungen: %s", exc)


def get_db() -> Connection:
    if "db" not in g:
        g.db = connect(current_app.config["TV_DB_PATH"], current_app.config["TV_SALT_DB_PATH"])
    return g.db


def close_db(_exc=None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()
