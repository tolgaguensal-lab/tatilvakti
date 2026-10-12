#!/usr/bin/env python3
"""Kennt ein Release die Schema-Version der Haupt-DB? Prüfung vor dem Umschalten.

Die App schreibt ihre Schema-Version (SCHEMA_VERSION in tatilvakti/db.py) beim Start als
PRAGMA user_version in die Haupt-DB, nur aufwärts. Ein Release mit kleinerer Version behandelt
neuere Daten nicht richtig, z. B. löscht seine Wartung Prüfwerte in Spalten nicht, die es nicht
kennt (Audit d2-schema-version-rollback). deploy.sh und rollback.sh rufen dieses Skript deshalb
vor dem Umschalten als Dienstbenutzer auf, und zwar immer in der eigenen Fassung, nicht in der
des Ziel-Releases: Ältere Releases kennen die Prüfung nicht.

    python3 scripts/schema_check.py --db /var/lib/tatilvakti-v2/tatilvakti.db \
        --release /opt/tatilvakti-v2/releases/<release> [--backups /var/lib/tatilvakti-v2/backups]

Erste Zeile: "schema db=<n> release=<m>" (n = 0: noch keine DB bzw. leer). Exit-Code 0, wenn das
Release die DB kennt (m ≥ n), sonst 1. Mit --backups nennt es dann die Sicherungen, die das Release
kennt (neueste zuerst), z. B. die vor dem Deploy. Exit-Code 2 (EXIT_UNREADABLE), wenn sich eine der
beiden Versionen nicht lesen lässt, etwa bei falschen Rechten an -wal/-shm oder einer beschädigten
DB: Dann ist nichts über die Versionen bekannt, und die Ausgabe nennt nur den Grund.

restore.sh prüft so vor dem Zurückspielen eine Kopie der Sicherung gegen das aktive Release
(--label Sicherung: So heißt die DB dann in den Meldungen).

DBs ohne user_version und Releases ohne SCHEMA_VERSION (beide von vor dieser Prüfung) ordnet es an
den Spalten von reports ein (LEGACY_COLUMNS). Nur Standardbibliothek, läuft mit dem python3
des Systems. Die Produktions-DB nur als deren Eigentümer öffnen, wie bei backup.py.
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

SCHEMA_VERSION_RE = re.compile(r"^SCHEMA_VERSION = (\d+)\s*(?:#.*)?$", re.M)
# Spalten im CREATE TABLE von SCHEMA in db.py, eine je Zeile: "    net         TEXT"
COLUMN_RE = re.compile(r"^\s+(\w+)\s+TEXT\b", re.M)
# Stand vor der Schema-Version: höchste Version, deren Spalte vorhanden ist (sonst 1, ohne Tabelle
# reports 0). Die Liste bleibt bei Version 3 stehen, neuere Versionen stehen immer in user_version.
LEGACY_COLUMNS = (("block", 3), ("net", 2))
# Zeitstempel im Namen einer Sicherung: tatilvakti-<UTC>.db, pre-deploy-<UTC>-<commit>.db
BACKUP_GLOBS = ("tatilvakti-*.db", "pre-deploy-*.db")
STAMP_RE = re.compile(r"-(\d{8}T\d{6}Z)[-.]")
SHOW_BACKUPS = 3
EXIT_NEWER = 1       # DB neuer, als das Release kennt
EXIT_UNREADABLE = 2  # Version von DB oder Release nicht lesbar


def legacy_version(columns: set[str]) -> int:
    """Schema-Version nach den Spalten von reports (Stand vor user_version)."""
    if not columns:
        return 0
    for column, version in LEGACY_COLUMNS:
        if column in columns:
            return version
    return 1


def db_version(path: Path) -> int:
    """Schema-Version einer DB-Datei: user_version, sonst an den Spalten abgelesen. 0 ohne DB."""
    if not path.exists():
        return 0
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version:
            return version
        columns = {row[1] for row in conn.execute("PRAGMA table_info(reports)")}
    finally:
        conn.close()
    return legacy_version(columns)


def release_version(release: Path) -> int:
    """Schema-Version, die ein Release kennt: SCHEMA_VERSION aus dessen tatilvakti/db.py, bei
    älteren Releases an den Spalten in dessen SCHEMA abgelesen."""
    text = (release / "tatilvakti" / "db.py").read_text(encoding="utf-8")
    match = SCHEMA_VERSION_RE.search(text)
    if match:
        return int(match.group(1))
    return max(1, legacy_version(set(COLUMN_RE.findall(text))))


def suitable_backups(dest: Path, version: int, limit: int = SHOW_BACKUPS) -> list[tuple[Path, int]]:
    """Sicherungen mit Schema-Version ≤ version, neueste zuerst (nach dem Zeitstempel im Namen)."""
    found = []
    for pattern in BACKUP_GLOBS:
        for path in dest.glob(pattern):
            match = STAMP_RE.search(path.name)
            if not match:
                continue
            try:
                have = db_version(path)
            except sqlite3.Error:
                continue
            if have <= version:
                found.append((match.group(1), path, have))
    found.sort(reverse=True)
    return [(path, have) for _, path, have in found[:limit]]


def check_owner(db: Path) -> None:
    """Die DB nur als ihr Eigentümer öffnen: Als root angelegte -wal/-shm könnte der Dienst
    danach nicht mehr beschreiben (wie in backup.py und preflight.py)."""
    owner = db.stat().st_uid
    if hasattr(os, "geteuid") and os.geteuid() != owner:
        raise PermissionError(f"{db} gehört UID {owner}, Aufruf als UID {os.geteuid()}: "
                              "als Dienstbenutzer ausführen (z. B. sudo -u tatilvakti-v2 …)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--db", type=Path, required=True, help="Haupt-DB (wird nur gelesen)")
    parser.add_argument("--release", type=Path, required=True, help="Release-Verzeichnis (mit tatilvakti/db.py)")
    parser.add_argument("--backups", type=Path, help="Sicherungen, die bei einem Abbruch genannt werden")
    parser.add_argument("--label", default="Datenbank",
                        help="So heißt die DB in den Meldungen (Standard: Datenbank; restore.sh: Sicherung)")
    args = parser.parse_args(argv)
    try:
        if args.db.exists():
            check_owner(args.db)
        have = db_version(args.db)
    except (OSError, sqlite3.Error) as exc:
        print(f"FEHLER: Schema-Version der {args.label} {args.db} nicht lesbar: {exc}")
        return EXIT_UNREADABLE
    try:
        known = release_version(args.release)
    except OSError as exc:
        print(f"FEHLER: Schema-Version von {args.release.name} nicht lesbar: {exc}")
        return EXIT_UNREADABLE
    print(f"schema db={have} release={known}")
    if have <= known:
        return 0
    print(f"FEHLER: Die {args.label} hat Schema-Version {have}, {args.release.name} kennt nur bis {known}.")
    if args.backups:
        try:
            backups = suitable_backups(args.backups, known) if args.backups.is_dir() else []
        except OSError as exc:
            print(f"Sicherungen in {args.backups} nicht lesbar: {exc}")
            return EXIT_NEWER
        if backups:
            print(f"Sicherungen mit Schema-Version bis {known}, neueste zuerst:")
            for path, version in backups:
                print(f"  {path}  (Schema-Version {version})")
        else:
            print(f"Keine Sicherung mit Schema-Version bis {known} in {args.backups}.")
    return EXIT_NEWER


if __name__ == "__main__":
    sys.exit(main())
