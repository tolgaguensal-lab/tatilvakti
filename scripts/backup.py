#!/usr/bin/env python3
"""Backup der tatilvakti-Datenbank per SQLite-Online-Backup, ohne Prüfwerte und Tagesschlüssel.

Warum kein `cp`: Im WAL-Modus stehen die jüngsten Meldungen oft noch in der -wal-Datei, eine
Dateikopie der .db verliert sie. Die Backup-API liest einen konsistenten Stand, auch während
gunicorn schreibt.

Datensparsamkeit: Die KOPIE behält nur, was ausdrücklich erlaubt ist (KEEP_TABLES, REPORT_COLUMNS):
von jeder Meldung Übergang, Richtung, Wartezeit-Bereich und Zeitpunkte. Alles andere wird geleert,
also die Prüfwerte (reports.client, reports.net, reports.block), eine evtl. vorhandene Tabelle
salts (ältere Versionen) und Unbekanntes, danach per VACUUM aus den freien Seiten entfernt. Die
eigene Salts-Datei (TV_SALT_DB_PATH) wird nie gelesen. Das Original bleibt unverändert.

    python scripts/backup.py --db /var/lib/tatilvakti-v2/tatilvakti.db \
        --dest /var/lib/tatilvakti-v2/backups --keep-days 14

Ergebnis: <dest>/tatilvakti-<UTC-Zeit>.db (Modus 0600). Jeder Lauf löscht danach die Sicherungen,
die --keep-days Tage alt sind: Bei 14 löscht der nächtliche Lauf am 14. Tag die Sicherung von
damals, auch wenn der Timer an diesem Tag etwas früher startet oder die Sommerzeit dazwischen
begann (PRUNE_SLACK_S). Das gilt auch, wenn die neue Sicherung scheitert; dann bleibt die neueste
vorhandene erhalten, es gibt also immer mindestens eine. Exit-Code ≠ 0 bei jedem Fehler (systemd
meldet den Lauf dann als fehlgeschlagen).

Sicherung vor dem Umschalten (scripts/deploy.sh, Audit d4-kein-db-snapshot-vor-umschalten):

    python scripts/backup.py --db … --dest … --pre-deploy <release>

Ergebnis: <dest>/pre-deploy-<release>.db, also pre-deploy-<UTC-Zeit>-<commit>.db, gleich bereinigt
und geprüft. Vorher prüft das Skript den freien Platz (check_space) und bricht ab, statt die Platte
des laufenden Dienstes zu füllen. Danach bleiben die neuesten --keep-snapshots solcher Sicherungen
(Standard 3) und dazu je ältere Schema-Version die neueste (prune_snapshots); die nächtlichen rührt
dieser Aufruf nicht an. Ältere als --keep-days löscht jeder Lauf, auch der nächtliche: Der
Datenschutztext nennt eine Frist für alle Sicherungen.

Nur als Eigentümer der DB ausführen (in Produktion: tatilvakti-v2-backup.service). Als root
angelegte -wal/-shm-Dateien könnte der Dienst danach nicht mehr beschreiben; das Skript bricht
deshalb ab, wenn der aufrufende Benutzer nicht Eigentümer der DB ist.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

NAME_RE = re.compile(r"^tatilvakti-(\d{8}T\d{6}Z)\.db$")
# Sicherung vor einem Deploy: pre-deploy-<Release>.db, der Name des Releases ist <UTC-Zeit>-<Commit>
# wie unter /opt/tatilvakti-v2/releases (deploy.sh). Die Zeit im Namen ist der Beginn des Deploys.
RELEASE_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{4,40}$")
SNAPSHOT_RE = re.compile(r"^pre-deploy-(\d{8}T\d{6}Z)-[0-9a-f]{4,40}\.db$")
# Reste abgebrochener Läufe. Sie können Prüfwerte enthalten (vor scrub), deshalb räumt sie jeder
# nächtliche Lauf weg, auch die eines abgebrochenen Deploys, diese aber erst ab TMP_MIN_AGE_S.
NIGHTLY_TMP_RE = re.compile(r"^\.tatilvakti-\d{8}T\d{6}Z\.db\.tmp$")
SNAPSHOT_TMP_RE = re.compile(r"^\.pre-deploy-\d{8}T\d{6}Z-[0-9a-f]{4,40}\.db\.tmp$")
# Mindestalter (mtime) eines Rests von --pre-deploy, bevor der nächtliche Lauf ihn löscht: Läuft
# gerade ein Deploy, schreibt backup.py vielleicht noch daran (Kopie, dann VACUUM).
# Eigene Reste löscht der nächtliche Lauf sofort, zwei davon gleichzeitig startet systemd nicht.
TMP_MIN_AGE_S = 3600
# Standard für --keep-days; deploy.sh gibt keinen Wert mit. test_backup_default_matches_the_unit
# hält ihn gleich mit der Unit (und damit mit dem Datenschutztext).
KEEP_DAYS = 14
KEEP_SNAPSHOTS = 3
# Platz für eine Sicherung vor dem Deploy: die Kopie selbst und das VACUUM in scrub(), das laut
# SQLite-Doku bis zum Doppelten der DB an freiem Platz braucht (Temp-Kopie und Journal,
# https://sqlite.org/lang_vacuum.html), zusammen also bis zum Dreifachen von DB samt -wal. Dazu
# eine Reserve, damit der laufende Dienst danach weiter schreiben kann.
SPACE_FACTOR = 3
SPACE_RESERVE = 64 * 2**20

# Was eine Sicherung behalten darf. Alles andere wird in der Kopie geleert, auch Unbekanntes (etwa
# nach einem Rollback auf ein Release, das neuere Spalten noch nicht kennt); davor warnt main().
# test_backup_knows_the_app_schema hält die Listen mit dem Schema der App (tatilvakti/db.py) gleich.
KEEP_TABLES = ("reports", "kv")
REPORT_COLUMNS = ("id", "crossing", "direction", "bucket", "observed_at", "created_at")
# Bekannt und bewusst geleert: Prüfwerte gegen Spam (HMAC von IP, Anschluss bzw. Netz mit dem
# Tagesschlüssel) und die Tagesschlüssel selbst (ältere Versionen hatten sie in der Haupt-DB)
HASH_COLUMNS = ("client", "net", "block")
SECRET_TABLES = ("salts",)
# Spielraum beim Löschen alter Sicherungen. Der Abstand zweier nächtlicher Läufe schwankt:
# - Der Timer startet mit Zufallsverzögerung (RandomizedDelaySec, 15 Min.).
# - Sommerzeit: Der Timer läuft in Ortszeit (03:40), die Dateinamen tragen UTC. Liegt die Umstellung
#   im März dazwischen, ist die Sicherung von vor 14 Tagen beim Lauf 1 h jünger als 14 Tage.
# Ohne genug Spielraum bliebe sie dann einen Tag länger liegen. 3 h decken beides ab und bleiben
# weit unter einem Tag, die Sicherung von gestern ist also nie betroffen.
# test_backup_prune_slack_covers_the_timer hält ihn größer als Verzögerung plus Umstellungsstunde.
PRUNE_SLACK_S = 3 * 3600


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


def check_space(db: Path, dest: Path) -> None:
    """Vor einer Sicherung vor dem Deploy: Reicht der Platz, ohne die Platte des Dienstes zu füllen?

    Gezählt wird das Dateisystem von dest. Liegt das Temp-Verzeichnis von SQLite auf einem anderen,
    ist die Rechnung nur vorsichtiger. Eine nächtliche Sicherung prüft das nicht (wie bisher).
    """
    size = sum(path.stat().st_size for path in (db, Path(f"{db}-wal")) if path.exists())
    need = SPACE_FACTOR * size + SPACE_RESERVE
    probe = dest
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if free < need:
        raise RuntimeError(f"zu wenig Platz für die Sicherung in {dest}: frei {_mib(free)}, nötig {_mib(need)} "
                           f"({SPACE_FACTOR} × DB samt -wal und {_mib(SPACE_RESERVE)} Reserve für den Dienst)")


def _mib(n: int) -> str:
    return f"{n / 2**20:.0f} MiB"


def backup(db: Path, dest: Path, now: datetime, release: str | None = None) -> tuple[Path, dict]:
    """Sichert db nach dest, bereinigt und prüft die Kopie.

    Ohne release heißt die Sicherung tatilvakti-<UTC-Zeit>.db, mit release (Sicherung vor dem
    Deploy) pre-deploy-<release>.db. Gibt den Pfad zurück und die Zählwerte aus verify(), dazu
    unter "unknown" die Tabellen und Spalten, die scrub() geleert hat, obwohl sie dieses Skript
    nicht kennt.
    """
    if not db.is_file():
        raise FileNotFoundError(f"Datenbank fehlt: {db}")
    if release is not None and not RELEASE_RE.match(release):
        raise ValueError(f"kein Release-Name (<UTC-Zeit>-<Commit>): {release!r}")
    dest.mkdir(parents=True, exist_ok=True)
    if release is None:
        target = dest / f"tatilvakti-{now.strftime('%Y%m%dT%H%M%SZ')}.db"
    else:
        target = dest / f"pre-deploy-{release}.db"
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


def _taken(path: Path) -> datetime | None:
    """Zeitpunkt einer Sicherung laut Dateiname (nächtlich oder vor einem Deploy), None bei fremden
    Dateien (auch bei einem unmöglichen Datum wie Monat 13: die rührt das Aufräumen nicht an)."""
    match = NAME_RE.match(path.name) or SNAPSHOT_RE.match(path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _expired(taken: datetime, keep_days: int, now: datetime) -> bool:
    return (now - taken).total_seconds() > keep_days * 86400 - PRUNE_SLACK_S


def _stale(path: Path) -> bool:
    """Rest einer Sicherung vor dem Deploy, an dem kein laufender Deploy mehr schreibt?"""
    try:
        return time.time() - path.stat().st_mtime > TMP_MIN_AGE_S
    except FileNotFoundError:
        return False


def prune(dest: Path, keep_days: int, now: datetime, keep: Path | None = None) -> list[Path]:
    """Löscht Sicherungen, die keep_days alt sind (nach Zeitstempel im Dateinamen, mit
    PRUNE_SLACK_S Spielraum), auch die vor einem Deploy (pre-deploy-….db), und Reste abgebrochener
    Läufe beider Arten (….db.tmp; die eines Deploys ab TMP_MIN_AGE_S).

    So löscht der nächtliche Lauf am Tag keep_days die Sicherung von damals, statt sie bis zum
    nächsten Lauf liegen zu lassen. keep wird nie gelöscht (Standard: die neueste vorhandene).
    Fehlt eine Datei beim Löschen schon (gleichzeitiger Deploy), zählt sie trotzdem als gelöscht.
    """
    if keep is None:
        backups = [p for p in dest.iterdir() if _taken(p) is not None]
        keep = max(backups, key=_taken, default=None)
    removed = []
    for path in sorted(dest.iterdir()):
        if NIGHTLY_TMP_RE.match(path.name) or (SNAPSHOT_TMP_RE.match(path.name) and _stale(path)):
            path.unlink(missing_ok=True)
            removed.append(path)
            continue
        taken = _taken(path)
        if taken is None or path == keep:
            continue
        if _expired(taken, keep_days, now):
            path.unlink(missing_ok=True)
            removed.append(path)
    return removed


def _load_schema_check():
    """scripts/schema_check.py laden. Es liegt immer daneben: im Release unter scripts/ und in der
    Kopie, die deploy.sh anlegt (wie in preflight.py)."""
    spec = importlib.util.spec_from_file_location("tv_schema_check", Path(__file__).with_name("schema_check.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _version(version_of, path: Path) -> int | None:
    """Schema-Version einer Sicherung, None, wenn sie nicht lesbar ist (oder gerade gelöscht wurde)."""
    try:
        return version_of(path)
    except (OSError, sqlite3.Error):
        return None


def prune_snapshots(dest: Path, keep_days: int, now: datetime, keep: Path, count: int = KEEP_SNAPSHOTS,
                    version_of=None) -> list[Path]:
    """Nach einer Sicherung vor dem Deploy: die neuesten count Sicherungen dieser Art behalten (keep
    zählt mit und bleibt immer) und dazu je Schema-Version unter der von keep die neueste. Ältere als
    keep_days löscht es trotzdem (außer keep), ebenso Reste abgebrochener Läufe dieser Art.

    Warum je ältere Schema-Version: Nach `deploy.sh --rev <alt> --force-schema` soll Weg 2 aus
    schema_hint (release-lib.sh) die Sicherung zurückspielen, die das ältere Release kennt, und die
    hat schema_check.py eben noch genannt. Liegen seit dem Deploy, der die Version erhöht hat, mehrere
    Deploys, wäre sie unter den neuesten count nicht mehr dabei. version_of liest die Schema-Version
    einer Datei (Standard: db_version aus schema_check.py). Nächtliche Sicherungen und deren Reste
    bleiben unberührt, die verwaltet der nächtliche Lauf mit der Frist aus der Unit."""
    if version_of is None:
        version_of = _load_schema_check().db_version
    snapshots = sorted((p for p in dest.iterdir() if SNAPSHOT_RE.match(p.name) and _taken(p) is not None),
                       key=lambda p: (_taken(p), p.name), reverse=True)
    newest = [keep] + [p for p in snapshots if p != keep][:count - 1]
    top, seen = _version(version_of, keep), set()
    for path in snapshots:  # neueste zuerst: die erste je Version ist deren neueste
        version = _version(version_of, path) if path != keep else None
        # 0 heißt: keine Tabelle reports, also keine Meldungen, die ein Rollback bräuchte
        if version and top is not None and version < top and version not in seen:
            seen.add(version)
            newest.append(path)
    removed = []
    for path in sorted(dest.iterdir()):
        if SNAPSHOT_TMP_RE.match(path.name):
            path.unlink(missing_ok=True)
            removed.append(path)
        elif path in snapshots and path != keep and (path not in newest or _expired(_taken(path), keep_days, now)):
            path.unlink(missing_ok=True)
            removed.append(path)
    return removed


def _prune_after_failure(dest: Path, keep_days: int, now: datetime) -> None:
    """Auch ohne neue Sicherung die Frist einhalten: alte löschen, die neueste vorhandene behalten.
    Ein Fehler dabei (z. B. Zielverzeichnis fehlt) verdeckt nicht den eigentlichen Fehler."""
    try:
        removed = prune(dest, keep_days, now)
    except OSError as exc:
        print(f"Aufräumen alter Sicherungen nicht möglich: {exc}", file=sys.stderr)
        return
    if removed:
        print(f"trotz Fehler aufgeräumt: removed={len(removed)} (die neueste Sicherung bleibt)", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--db", type=Path, default=os.environ.get("TV_DB_PATH"),
                        help="Quelle (Standard: $TV_DB_PATH)")
    parser.add_argument("--dest", type=Path, help="Zielverzeichnis der Sicherungen")
    parser.add_argument("--keep-days", type=int, default=KEEP_DAYS,
                        help=f"Aufbewahrung in Tagen (Standard {KEEP_DAYS})")
    parser.add_argument("--verify", type=Path, metavar="DATEI",
                        help="nur eine vorhandene Sicherung prüfen (z. B. vor einem Restore)")
    parser.add_argument("--pre-deploy", metavar="RELEASE",
                        help="Sicherung vor dem Umschalten (deploy.sh): pre-deploy-RELEASE.db, Platz vorher prüfen")
    parser.add_argument("--keep-snapshots", type=int, default=KEEP_SNAPSHOTS,
                        help=f"mit --pre-deploy: so viele solcher Sicherungen behalten (Standard {KEEP_SNAPSHOTS}), "
                             "dazu je ältere Schema-Version die neueste")
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
        snapshot = args.pre_deploy is not None
        if snapshot and not RELEASE_RE.match(args.pre_deploy):
            parser.error(f"--pre-deploy erwartet den Namen des Releases (<UTC-Zeit>-<Commit>): {args.pre_deploy!r}")
        if args.keep_snapshots < 1:
            parser.error("--keep-snapshots muss mindestens 1 sein")
        # Vor der Sicherung laden: Fehlt schema_check.py, bleibt nichts liegen
        version_of = _load_schema_check().db_version if snapshot else None
        now = datetime.now(timezone.utc)
        started = time.monotonic()
        try:
            if args.db.exists():
                check_owner(args.db)
                if snapshot:
                    check_space(args.db, args.dest)
            target, info = backup(args.db, args.dest, now, release=args.pre_deploy)
        except (OSError, sqlite3.Error, RuntimeError):
            # Vor einem Deploy nicht: deploy.sh bricht ab, die Frist hält der nächtliche Lauf ein
            if not snapshot:
                _prune_after_failure(args.dest, args.keep_days, now)
            raise
        if info["unknown"]:
            print(f"WARNUNG: unbekannte Tabellen/Spalten in der Kopie geleert: {', '.join(info['unknown'])}. "
                  "scripts/backup.py prüfen (KEEP_TABLES, REPORT_COLUMNS, HASH_COLUMNS).", file=sys.stderr)
        if snapshot:
            removed = prune_snapshots(args.dest, args.keep_days, now, keep=target, count=args.keep_snapshots,
                                      version_of=version_of)
        else:
            removed = prune(args.dest, args.keep_days, now, keep=target)
        print(f"ok {target} reports={info['reports']} bytes={target.stat().st_size} "
              f"removed={len(removed)} dauer={time.monotonic() - started:.1f}s")
        return 0
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
