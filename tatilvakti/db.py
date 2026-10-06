"""SQLite-Zugriff. Eine Datei, WAL-Modus, Verbindung pro Request."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import current_app, g

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
CREATE INDEX IF NOT EXISTS idx_reports_client ON reports (client, created_at);

CREATE TABLE IF NOT EXISTS salts (
    day  TEXT PRIMARY KEY,          -- UTC-Datum
    salt BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["TV_DB_PATH"])
    return g.db


def close_db(_exc=None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()
