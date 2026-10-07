#!/usr/bin/env python3
"""Backup der tatilvakti-Datenbank per SQLite-Online-Backup, ohne Prüfwerte und Tagesschlüssel.

Warum kein `cp`: Im WAL-Modus stehen die jüngsten Meldungen oft noch in der -wal-Datei, eine
Dateikopie der .db verliert sie. Die Backup-API liest einen konsistenten Stand, auch während
gunicorn schreibt.

Datensparsamkeit: In der KOPIE werden die Prüfwerte (reports.client) geleert und eine evtl.
vorhandene Tabelle salts (ältere Versionen) geleert, danach per VACUUM aus den freien Seiten
entfernt. Die eigene Salts-Datei (TV_SALT_DB_PATH) wird nie gelesen. Das Original bleibt unverändert.

    python scripts/backup.py --db /var/lib/tatilvakti-v2/tatilvakti.db \
        --dest /var/lib/tatilvakti-v2/backups --keep-days 14

Ergebnis: <dest>/tatilvakti-<UTC-Zeit>.db (Modus 0600). Ältere Sicherungen als --keep-days werden
erst NACH einer erfolgreichen neuen Sicherung gelöscht, die neueste bleibt also immer erhalten.
Exit-Code ≠ 0 bei jedem Fehler (systemd meldet den Lauf dann als fehlgeschlagen).
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

NAME_RE = re.compile(r"^tatilvakti-(\d{8}T\d{6}Z)\.db$")


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def scrub(conn: sqlite3.Connection) -> None:
    """Prüfwerte und Tagesschlüssel aus der Kopie entfernen, auch aus den freien Seiten."""
    tables = _tables(conn)
    if "reports" in tables and "client" in _columns(conn, "reports"):
        conn.execute("UPDATE reports SET client = NULL WHERE client IS NOT NULL")
    if "salts" in tables:
        conn.execute("DELETE FROM salts")
    conn.commit()
    conn.execute("VACUUM")


def verify(path: Path) -> dict:
    """Prüft eine Sicherung: Integrität, keine Prüfwerte, keine Schlüssel. Gibt Zählwerte zurück."""
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"integrity_check: {result}")
        tables = _tables(conn)
        info = {"reports": 0, "with_client": 0, "salts": 0}
        if "reports" in tables:
            info["reports"] = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
            if "client" in _columns(conn, "reports"):
                info["with_client"] = conn.execute(
                    "SELECT COUNT(*) FROM reports WHERE client IS NOT NULL").fetchone()[0]
        if "salts" in tables:
            info["salts"] = conn.execute("SELECT COUNT(*) FROM salts").fetchone()[0]
        if info["with_client"] or info["salts"]:
            raise RuntimeError(f"Sicherung enthält noch Prüfwerte/Schlüssel: {info}")
        return info
    finally:
        conn.close()


def backup(db: Path, dest: Path, now: datetime) -> Path:
    if not db.is_file():
        raise FileNotFoundError(f"Datenbank fehlt: {db}")
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"tatilvakti-{now.strftime('%Y%m%dT%H%M%SZ')}.db"
    tmp = dest / f".{target.name}.tmp"
    tmp.unlink(missing_ok=True)
    try:
        src = sqlite3.connect(str(db), timeout=30)
        try:
            dst = sqlite3.connect(str(tmp))
            try:
                src.backup(dst)
                # Eigenständige Datei ohne -wal/-shm: ein Restore besteht aus genau einer Datei
                dst.execute("PRAGMA journal_mode=DELETE")
                scrub(dst)
            finally:
                dst.close()
        finally:
            src.close()
        os.chmod(tmp, 0o600)
        verify(tmp)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target


def prune(dest: Path, keep_days: int, now: datetime, keep: Path) -> list[Path]:
    """Löscht Sicherungen, die älter als keep_days sind (nach Zeitstempel im Dateinamen)."""
    removed = []
    for path in sorted(dest.iterdir()):
        match = NAME_RE.match(path.name)
        if not match or path == keep:
            continue
        taken = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        if (now - taken).total_seconds() > keep_days * 86400:
            path.unlink()
            removed.append(path)
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--db", type=Path, default=os.environ.get("TV_DB_PATH"),
                        help="Quelle (Standard: $TV_DB_PATH)")
    parser.add_argument("--dest", type=Path, required=True, help="Zielverzeichnis der Sicherungen")
    parser.add_argument("--keep-days", type=int, default=14, help="Aufbewahrung in Tagen (Standard 14)")
    parser.add_argument("--verify", type=Path, metavar="DATEI",
                        help="nur eine vorhandene Sicherung prüfen (z. B. vor einem Restore)")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.verify:
            info = verify(args.verify)
            print(f"ok {args.verify} reports={info['reports']}")
            return 0
        if args.db is None:
            parser.error("--db fehlt (oder TV_DB_PATH setzen)")
        if args.keep_days < 1:
            parser.error("--keep-days muss mindestens 1 sein")
        now = datetime.now(timezone.utc)
        started = time.monotonic()
        target = backup(args.db, args.dest, now)
        info = verify(target)
        removed = prune(args.dest, args.keep_days, now, keep=target)
        print(f"ok {target} reports={info['reports']} bytes={target.stat().st_size} "
              f"removed={len(removed)} dauer={time.monotonic() - started:.1f}s")
        return 0
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
