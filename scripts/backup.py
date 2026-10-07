#!/usr/bin/env python3
"""Backup der tatilvakti-Datenbank per SQLite-Online-Backup, ohne Prüfwerte und Tagesschlüssel.

Warum kein `cp`: Im WAL-Modus stehen die jüngsten Meldungen oft noch in der -wal-Datei, eine
Dateikopie der .db verliert sie. Die Backup-API liest einen konsistenten Stand, auch während
gunicorn schreibt.

Datensparsamkeit: Die KOPIE behält nur, was ausdrücklich erlaubt ist (KEEP_TABLES, REPORT_COLUMNS):
von jeder Meldung Übergang, Richtung, Wartezeit-Bereich und Zeitpunkte. Alles andere wird geleert,
also die Prüfwerte (reports.client, reports.net), eine evtl. vorhandene Tabelle salts (ältere
Versionen) und Unbekanntes, danach per VACUUM aus den freien Seiten entfernt. Die eigene
Salts-Datei (TV_SALT_DB_PATH) wird nie gelesen. Das Original bleibt unverändert.

    python scripts/backup.py --db /var/lib/tatilvakti-v2/tatilvakti.db \
        --dest /var/lib/tatilvakti-v2/backups --keep-days 14

Ergebnis: <dest>/tatilvakti-<UTC-Zeit>.db (Modus 0600). Ältere Sicherungen als --keep-days werden
erst NACH einer erfolgreichen neuen Sicherung gelöscht, die neueste bleibt also immer erhalten.
Exit-Code ≠ 0 bei jedem Fehler (systemd meldet den Lauf dann als fehlgeschlagen).

Nur als Eigentümer der DB ausführen (in Produktion: tatilvakti-v2-backup.service). Als root
angelegte -wal/-shm-Dateien könnte der Dienst danach nicht mehr beschreiben; das Skript bricht
deshalb ab, wenn der aufrufende Benutzer nicht Eigentümer der DB ist.
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
TMP_RE = re.compile(r"^\.tatilvakti-\d{8}T\d{6}Z\.db\.tmp$")

# Was eine Sicherung behalten darf. Alles andere wird in der Kopie geleert, auch Unbekanntes (etwa
# nach einem Rollback auf ein Release, das neuere Spalten noch nicht kennt); davor warnt main().
# test_backup_knows_the_app_schema hält die Listen mit dem Schema der App (tatilvakti/db.py) gleich.
KEEP_TABLES = ("reports", "kv")
REPORT_COLUMNS = ("id", "crossing", "direction", "bucket", "observed_at", "created_at")
# Bekannt und bewusst geleert: Prüfwerte gegen Spam (HMAC von IP bzw. Anschluss mit dem
# Tagesschlüssel) und die Tagesschlüssel selbst (ältere Versionen hatten sie in der Haupt-DB)
HASH_COLUMNS = ("client", "net")
SECRET_TABLES = ("salts",)


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _q(name: str) -> str:
    """Bezeichner für SQL quoten (Namen stammen aus der Datei selbst, nicht vom Benutzer)."""
    return '"' + name.replace('"', '""') + '"'


def _tables_to_check(conn: sqlite3.Connection) -> list[str]:
    # Interne Tabellen von SQLite (sqlite_sequence, sqlite_stat1 …) enthalten keine Inhalte
    return sorted(t for t in _tables(conn) if not t.startswith("sqlite_"))


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({_q(table)})")}


def _extra_columns(conn: sqlite3.Connection) -> list[str]:
    """Spalten von reports, die eine Sicherung nicht enthalten darf (Prüfwerte, Unbekanntes)."""
    return sorted(_columns(conn, "reports") - set(REPORT_COLUMNS))


def scrub(conn: sqlite3.Connection) -> list[str]:
    """Prüfwerte und Tagesschlüssel aus der Kopie entfernen, auch aus den freien Seiten.

    Leert alles außer KEEP_TABLES und REPORT_COLUMNS. Gibt die dabei geleerten UNBEKANNTEN Tabellen
    und Spalten zurück (weder Inhalt noch bekannter Prüfwert/Schlüssel), damit main() warnt.
    """
    unknown = []
    for table in _tables_to_check(conn):
        if table not in KEEP_TABLES:
            if table not in SECRET_TABLES:
                unknown.append(table)
            conn.execute(f"DELETE FROM {_q(table)}")
        elif table == "reports":
            for col in _extra_columns(conn):
                if col not in HASH_COLUMNS:
                    unknown.append(f"reports.{col}")
                try:
                    conn.execute(f"UPDATE reports SET {_q(col)} = NULL WHERE {_q(col)} IS NOT NULL")
                except sqlite3.IntegrityError as exc:  # NOT NULL: lässt sich nicht leeren
                    raise RuntimeError(f"reports.{col} lässt sich nicht leeren ({exc}). In scripts/backup.py "
                                       "als Inhalt (REPORT_COLUMNS) oder Prüfwert (HASH_COLUMNS) eintragen.") from exc
    conn.commit()
    conn.execute("VACUUM")
    return unknown


def verify(path: Path) -> dict:
    """Prüft eine Sicherung: Integrität, keine Prüfwerte, keine Schlüssel, nichts Unbekanntes.

    Gibt die Zahl der Meldungen zurück. Bricht ab, sobald außerhalb von KEEP_TABLES bzw.
    REPORT_COLUMNS noch ein Wert steht.
    """
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"integrity_check: {result}")
        info, leftover = {"reports": 0}, {}
        for table in _tables_to_check(conn):
            if table not in KEEP_TABLES:
                leftover[table] = conn.execute(f"SELECT COUNT(*) FROM {_q(table)}").fetchone()[0]
            elif table == "reports":
                info["reports"] = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
                for col in _extra_columns(conn):
                    leftover[f"reports.{col}"] = conn.execute(
                        f"SELECT COUNT(*) FROM reports WHERE {_q(col)} IS NOT NULL").fetchone()[0]
        leftover = {name: n for name, n in leftover.items() if n}
        if leftover:
            raise RuntimeError(f"Sicherung enthält noch Prüfwerte/Schlüssel (Anzahl Werte): {leftover}")
        return info
    finally:
        conn.close()


def check_owner(db: Path) -> None:
    """Die DB nur als ihr Eigentümer öffnen (siehe Modul-Docstring: -wal/-shm-Eigentümer)."""
    owner = db.stat().st_uid
    if hasattr(os, "geteuid") and os.geteuid() != owner:
        raise PermissionError(f"{db} gehört UID {owner}, Aufruf als UID {os.geteuid()}: "
                              "als Dienstbenutzer ausführen (systemctl start tatilvakti-v2-backup)")


def backup(db: Path, dest: Path, now: datetime) -> tuple[Path, dict]:
    """Sichert db nach dest, bereinigt und prüft die Kopie.

    Gibt den Pfad zurück und die Zählwerte aus verify(), dazu unter "unknown" die Tabellen und
    Spalten, die scrub() geleert hat, obwohl sie dieses Skript nicht kennt.
    """
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
                unknown = scrub(dst)
            finally:
                dst.close()
        finally:
            src.close()
        os.chmod(tmp, 0o600)
        info = verify(tmp)
        info["unknown"] = unknown
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target, info


def prune(dest: Path, keep_days: int, now: datetime, keep: Path) -> list[Path]:
    """Löscht Sicherungen, die älter als keep_days sind (nach Zeitstempel im Dateinamen),
    und Reste abgebrochener Läufe (.tatilvakti-….db.tmp)."""
    removed = []
    for path in sorted(dest.iterdir()):
        if TMP_RE.match(path.name):
            path.unlink()
            removed.append(path)
            continue
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
    parser.add_argument("--dest", type=Path, help="Zielverzeichnis der Sicherungen")
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
        if args.dest is None:
            parser.error("--dest fehlt")
        if args.keep_days < 1:
            parser.error("--keep-days muss mindestens 1 sein")
        now = datetime.now(timezone.utc)
        started = time.monotonic()
        if args.db.exists():
            check_owner(args.db)
        target, info = backup(args.db, args.dest, now)
        if info["unknown"]:
            print(f"WARNUNG: unbekannte Tabellen/Spalten in der Kopie geleert: {', '.join(info['unknown'])}. "
                  "scripts/backup.py prüfen (KEEP_TABLES, REPORT_COLUMNS, HASH_COLUMNS).", file=sys.stderr)
        removed = prune(args.dest, args.keep_days, now, keep=target)
        print(f"ok {target} reports={info['reports']} bytes={target.stat().st_size} "
              f"removed={len(removed)} dauer={time.monotonic() - started:.1f}s")
        return 0
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
