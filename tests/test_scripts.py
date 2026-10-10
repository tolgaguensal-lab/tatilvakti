"""Betriebsskripte (scripts/) und systemd-Units (deploy/): Syntax, Schnittstellen, Backup, Preflight."""
from __future__ import annotations

import importlib.util
import os
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from time import monotonic, sleep
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
DEPLOY = ROOT / "deploy"


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(f"tv_script_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unit(name: str) -> dict[str, list[str]]:
    """Liest eine systemd-Unit: Schlüssel → alle Werte (Fortsetzungszeilen zusammengefügt)."""
    values: dict[str, list[str]] = {}
    text = (DEPLOY / name).read_text(encoding="utf-8").replace("\\\n", " ")
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in "#;[" or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values.setdefault(key.strip(), []).append(" ".join(value.split()))
    return values


@pytest.mark.parametrize("path", sorted(SCRIPTS.glob("*.sh")), ids=lambda p: p.name)
def test_shell_scripts_parse(path):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash fehlt")
    subprocess.run([bash, "-n", str(path)], check=True)
    # release-lib.sh wird nur geladen ("source"), alles andere muss ausführbar sein
    assert bool(path.stat().st_mode & stat.S_IXUSR) == (path.name != "release-lib.sh")


@pytest.mark.parametrize("path", sorted(SCRIPTS.glob("*.py")), ids=lambda p: p.name)
def test_python_scripts_compile(path):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
    assert path.stat().st_mode & stat.S_IXUSR


def test_units_follow_the_interface():
    svc = unit("tatilvakti-v2.service")
    start = svc["ExecStart"][0]
    assert "--bind 127.0.0.1:3096" in start and "--no-control-socket" in start
    assert "--access-logfile" not in start  # keine IP-Adressen im Journal
    assert svc["User"] == ["tatilvakti-v2"] and svc["WorkingDirectory"] == ["/opt/tatilvakti-v2/current"]
    assert svc["EnvironmentFile"] == ["/etc/tatilvakti-v2.env"]
    assert "TV_SALT_DB_PATH=/run/tatilvakti-v2/salts.db" in svc["Environment"]
    assert svc["StateDirectory"] == ["tatilvakti-v2"] and svc["RuntimeDirectory"] == ["tatilvakti-v2"]
    assert "ReadWritePaths" not in svc

    maint = unit("tatilvakti-v2-maintenance.service")
    assert maint["ExecStart"][0].endswith("/flask --app tatilvakti maintenance")
    assert maint["Environment"] == svc["Environment"]
    assert maint["RuntimeDirectoryPreserve"] == ["yes"]  # räumt /run unter dem Dienst nicht weg

    backup = unit("tatilvakti-v2-backup.service")
    assert "InaccessiblePaths" in backup and "/run/tatilvakti-v2" in backup["InaccessiblePaths"][0]
    assert not any("TV_SALT_DB_PATH" in e for e in backup.get("Environment", []))


def test_env_example_passes_config_check(capsys):
    pre = load_script("preflight")
    env = pre.parse_env_file((DEPLOY / "tatilvakti-v2.env.example").read_text(encoding="utf-8"))
    assert env["TV_BASE_URL"] == "https://tatilvakti.guenlab.de"
    assert env["TV_TRUST_PROXY"] == "1"
    rep = pre.Report()
    pre.check_config(env, rep)
    assert rep.errors == []


@pytest.mark.parametrize("env, needle", [
    ({"TV_BASE_URL": "https://tatilvakti.example"}, "Platzhalter"),
    ({"TV_BASE_URL": "http://tatilvakti.guenlab.de"}, "https://"),
    ({"TV_TRUST_PROXY": "ja"}, "Zahl"),
    ({"TV_SALT_DB_PATH": "/var/lib/tatilvakti-v2/salts.db"}, "TV_SALT_DB_PATH"),
    ({"TV_LEGACY_SW_PATHS": "service-worker.js"}, "TV_LEGACY_SW_PATHS"),
])
def test_config_check_rejects(env, needle, capsys):
    pre = load_script("preflight")
    rep = pre.Report()
    pre.check_config(env, rep)
    assert any(needle in e for e in rep.errors)


def test_config_check_treats_separator_only_address_as_missing(capsys):
    """Wie operator_imprint() in der App: Eine Anschrift nur aus „;“ ist keine Anschrift."""
    pre = load_script("preflight")
    env = {"TV_OPERATOR_NAME": "Max Muster", "TV_OPERATOR_ADDRESS": " ; ;", "TV_OPERATOR_EMAIL": "a@b.de"}
    pre.check_config(env, pre.Report())
    out = capsys.readouterr().out
    assert "Impressum unvollständig" in out and "TV_OPERATOR_ADDRESS" in out
    env["TV_OPERATOR_ADDRESS"] = "Musterstr. 1; 12345 Musterstadt"
    pre.check_config(env, pre.Report())
    assert "Impressum-Angaben gesetzt" in capsys.readouterr().out


def test_parse_env_file_like_systemd():
    pre = load_script("preflight")
    env = pre.parse_env_file('# Kommentar\n; auch\nA=1\nB="zwei Wörter"\nC=x y;z\n'
                             "GUNICORN_CMD_ARGS=\"--access-logformat '%(m)s %(U)s'\"\n")
    assert env == {"A": "1", "B": "zwei Wörter", "C": "x y;z",
                   "GUNICORN_CMD_ARGS": "--access-logformat '%(m)s %(U)s'"}


def test_preflight_checks_all_pages(tmp_path, monkeypatch, capsys):
    pre = load_script("preflight")
    env = {"TV_BASE_URL": "https://tatilvakti.guenlab.de", "TV_TRUST_PROXY": "1"}
    for key in (*env, "TV_DB_PATH", "TV_SALT_DB_PATH"):  # check_app setzt sie, monkeypatch räumt auf
        monkeypatch.setenv(key, "")
    rep = pre.Report()
    build = pre.check_app(env, None, tmp_path, rep)
    out = capsys.readouterr().out
    assert rep.errors == [], out
    assert build
    assert "aus der Precache-Liste" in out
    assert pre.precache_urls("kein Service Worker") is None


def test_healthcheck_reads_the_body_of_a_503(monkeypatch, capsys):
    """deploy.sh fragt /healthz über HTTP ab: Bei 503 wirft urllib einen HTTPError, dessen Body
    trotzdem zählt (salt_db allein: gesund mit WARNUNG; db: nicht gesund)."""
    import io
    import json
    import urllib.error
    hc = load_script("healthcheck")

    def answer(code, body):
        def urlopen(url, timeout):
            raw = io.BytesIO(json.dumps(body).encode())
            if code == 200:
                return raw
            raise urllib.error.HTTPError(url, code, "Service Unavailable", {}, raw)
        monkeypatch.setattr(hc.urllib.request, "urlopen", urlopen)

    answer(503, {"status": "down", "down": ["salt_db"], "db": True, "salt_db": False, "build": "b1"})
    assert hc.main(["http://127.0.0.1:3096/healthz", "b1"]) == 0
    out, err = capsys.readouterr()
    assert "status=down build=b1 down=['salt_db']" in out and "WARNUNG: Schlüssel-DB" in err

    answer(503, {"status": "down", "down": ["db", "salt_db"], "db": False, "salt_db": False, "build": "b1"})
    assert hc.main(["http://127.0.0.1:3096/healthz", "b1"]) == 1
    out, err = capsys.readouterr()
    assert "Ausfall: db" in out and err == ""

    answer(200, {"status": "ok", "down": [], "db": True, "salt_db": True, "build": "b0"})
    assert hc.main(["http://127.0.0.1:3096/healthz", "b1"]) == 1
    assert "erwartet build=b1" in capsys.readouterr().out
    assert hc.main(["http://127.0.0.1:3096/healthz"]) == 0  # ohne erwartete Build-ID

    # 503 ohne JSON (z. B. Proxy-Fehlerseite) oder gar keine Antwort: nicht gesund
    def broken(url, timeout):
        raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, io.BytesIO(b"<html>"))
    monkeypatch.setattr(hc.urllib.request, "urlopen", broken)
    assert hc.main(["http://127.0.0.1:3096/healthz"]) == 1
    assert "keine auswertbare Antwort" in capsys.readouterr().out


@pytest.mark.parametrize("body, healthy", [
    ({"status": "ok", "db": True, "salt_db": True}, True),                       # ältere Releases
    ({"status": "attention", "db": True, "salt_db": False}, True),               # dort war das 200
    ({"status": "degraded", "db": False, "salt_db": True}, False),
    ({"status": "down", "down": [], "db": False}, False),                         # db zählt immer
    ({"status": "attention", "down": [], "db": True, "attention": ["imprint"]}, True),
    ({"status": "attention", "build": "b", "down": []}, True),                    # knapp, von außen
    ({"status": "down", "build": "b", "down": ["salt_db"]}, True),
    ({"status": "down", "build": "b", "down": ["db"]}, False),
    ({"status": "ok", "build": "b"}, False),                                      # weder down noch db
])
def test_healthcheck_understands_old_and_new_answers(body, healthy):
    hc = load_script("healthcheck")
    assert hc.evaluate(body)[0] is healthy


def test_healthcheck_timeout_covers_the_lock_wait():
    """Ein Abruf darf so lange dauern, wie /healthz auf die Schreibsperre wartet, aber nicht viel
    länger: wait_healthy wiederholt ihn bis zu HEALTH_TRIES-mal, ein hängender Dienst verzögert den
    Rollback sonst unnötig (Kommentar zu HEALTH_TRIES in release-lib.sh)."""
    from tatilvakti.db import BUSY_TIMEOUT_MS
    hc = load_script("healthcheck")
    assert BUSY_TIMEOUT_MS / 1000 < hc.TIMEOUT_S <= BUSY_TIMEOUT_MS / 1000 + 2
    assert f"healthcheck.py ({hc.TIMEOUT_S} s)" in (SCRIPTS / "release-lib.sh").read_text(encoding="utf-8")


def test_healthz_from_the_app_passes_the_health_check(app, client):
    """Schnittstelle zwischen App und deploy.sh: Die echte Antwort von /healthz gilt als gesund,
    auch die knappe für Aufrufe von außen."""
    hc = load_script("healthcheck")
    data = client.get("/healthz").get_json()
    healthy, line, warnings = hc.evaluate(data, data["build"])
    assert healthy and warnings == [] and "down=[]" in line
    public = client.get("/healthz", headers={"X-Forwarded-For": "203.0.113.9"}).get_json()
    assert "db" not in public and hc.evaluate(public, data["build"])[0] is True


def make_db(path: Path) -> None:
    """Haupt-DB im Schema der App, im WAL-Modus, mit Prüfwerten (und alten Schlüsseln, falls vorhanden).

    Fehlen die Spalten net und block (Prüfwerte für Anschluss und Netz) im Schema dieses Stands,
    kommen sie dazu: Das Backup muss sie in jedem Fall leeren.
    """
    from tatilvakti.db import connect, init_db
    init_db(str(path))
    conn = connect(str(path))
    try:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(reports)")}
        for column in ("net", "block"):
            if column not in columns:
                conn.execute(f"ALTER TABLE reports ADD COLUMN {column} TEXT")
        now = int(datetime(2026, 10, 6, 10, tzinfo=timezone.utc).timestamp())
        conn.executemany(
            "INSERT INTO reports (crossing, direction, bucket, observed_at, created_at, client, net, block) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [("kapikule", "to_tr", 1, now - i, now - i, f"pruefwert-geheim-{i:04d}", f"netz-geheim-{i:04d}",
              f"block-geheim-{i:04d}") for i in range(20)])
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'salts'").fetchone():
            conn.execute("INSERT INTO salts (day, salt) VALUES ('2026-10-06', ?)", (b"schluessel-geheim",))
    finally:
        conn.close()


def test_backup_is_complete_and_scrubbed(tmp_path):
    bk = load_script("backup")
    db = tmp_path / "tatilvakti.db"
    make_db(db)
    # Offene Verbindung mit ungecheckpointeten Schreibvorgängen: diese Zeilen stehen nur im -wal
    live = sqlite3.connect(str(db))
    live.execute("PRAGMA wal_autocheckpoint=0")
    live.executemany("INSERT INTO reports (crossing, direction, bucket, observed_at, created_at, client, net, block) "
                     "VALUES ('kapikule', 'to_de', 2, 1, 1, ?, ?, ?)",
                     [(f"pruefwert-wal-{i}", f"netz-wal-{i}", f"block-wal-{i}") for i in range(30)])
    live.commit()
    try:
        now = datetime(2026, 10, 7, 3, 40, tzinfo=timezone.utc)
        target, info = bk.backup(db, tmp_path / "backups", now)
    finally:
        live.close()

    assert target.name == "tatilvakti-20261007T034000Z.db"
    assert info == {"reports": 50, "unknown": []}
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not Path(str(target) + "-wal").exists()
    raw = target.read_bytes()
    # Weder Prüfwerte (IP, Anschluss, Netz) noch Schlüssel, auch nicht im freien Speicher
    assert b"pruefwert" not in raw and b"netz-" not in raw and b"block-" not in raw
    assert b"schluessel-geheim" not in raw
    copy = sqlite3.connect(str(target))
    try:
        assert copy.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert copy.execute("SELECT COUNT(*), COUNT(client), COUNT(net), COUNT(block), COUNT(observed_at) "
                            "FROM reports").fetchone() == (50, 0, 0, 0, 50)
    finally:
        copy.close()
    # Das Original bleibt unverändert
    orig = sqlite3.connect(str(db))
    try:
        assert orig.execute("SELECT COUNT(client), COUNT(net), COUNT(block) FROM reports").fetchone() == (50, 50, 50)
    finally:
        orig.close()
    assert bk.main(["--verify", str(target)]) == 0


def test_backup_knows_the_app_schema(tmp_path):
    """Jede Tabelle und jede Spalte von reports im Schema der App ist in backup.py eingeordnet:
    als Inhalt der Sicherung oder als bewusst geleerter Prüfwert/Schlüssel. Schlägt dieser Test
    fehl, hat sich das Schema geändert: KEEP_TABLES/REPORT_COLUMNS bzw. HASH_COLUMNS/SECRET_TABLES
    in scripts/backup.py ergänzen."""
    bk = load_script("backup")
    from tatilvakti.db import init_db
    db = tmp_path / "schema.db"
    init_db(str(db))
    conn = sqlite3.connect(str(db))
    try:
        tables = set(bk._tables_to_check(conn))
        columns = bk._columns(conn, "reports")
    finally:
        conn.close()
    assert tables <= set(bk.KEEP_TABLES) | set(bk.SECRET_TABLES)
    assert columns <= set(bk.REPORT_COLUMNS) | set(bk.HASH_COLUMNS)
    assert set(bk.REPORT_COLUMNS) <= columns


def test_backup_clears_unknown_and_verify_rejects_leftovers(tmp_path, capsys):
    bk = load_script("backup")
    db = tmp_path / "tatilvakti.db"
    make_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("ALTER TABLE reports ADD COLUMN neu TEXT")
    conn.execute("UPDATE reports SET neu = 'unbekannt-geheim'")
    conn.execute("CREATE TABLE extra (x TEXT)")
    conn.execute("INSERT INTO extra VALUES ('tabelle-geheim')")
    conn.commit()
    conn.close()

    assert bk.main(["--db", str(db), "--dest", str(tmp_path / "backups")]) == 0
    assert "WARNUNG: unbekannte Tabellen/Spalten in der Kopie geleert: extra, reports.neu" in capsys.readouterr().err
    target = next((tmp_path / "backups").glob("tatilvakti-*.db"))
    raw = target.read_bytes()
    assert b"unbekannt-geheim" not in raw and b"tabelle-geheim" not in raw

    # Eine Sicherung mit Restwerten (z. B. von einem älteren backup.py) fällt bei --verify durch
    old = tmp_path / "alt.db"
    src, dst = sqlite3.connect(str(db)), sqlite3.connect(str(old))
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    assert bk.main(["--verify", str(old)]) == 1
    err = capsys.readouterr().err
    assert "reports.net" in err and "reports.client" in err and "reports.block" in err and "extra" in err


def backup_name(when: datetime) -> str:
    return f"tatilvakti-{when.strftime('%Y%m%dT%H%M%SZ')}.db"


def test_backup_prune_keeps_newest(tmp_path):
    bk = load_script("backup")
    now = datetime(2026, 10, 30, 3, 40, tzinfo=timezone.utc)
    names = [backup_name(now - timedelta(days=d)) for d in (0, 13, 15, 40)]
    odd = "tatilvakti-20261399T034000Z.db"  # passt zum Muster, ist aber kein Datum: bleibt unberührt
    for name in [*names, ".tatilvakti-20261001T034000Z.db.tmp", "notiz.txt", odd]:
        (tmp_path / name).write_text("x")
    removed = bk.prune(tmp_path, 14, now, keep=tmp_path / names[0])
    assert sorted(p.name for p in removed) == sorted([names[2], names[3], ".tatilvakti-20261001T034000Z.db.tmp"])
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([names[0], names[1], "notiz.txt", odd])
    (tmp_path / odd).unlink()
    # Die neueste Sicherung bleibt auch bei Aufbewahrung von einem Tag
    assert bk.prune(tmp_path, 1, now + timedelta(days=30), keep=tmp_path / names[0]) == [tmp_path / names[1]]
    # Ohne keep: die neueste vorhandene
    assert bk.prune(tmp_path, 1, now + timedelta(days=60)) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([names[0], "notiz.txt"])


def test_backup_prune_deletes_on_day_14_despite_timer_delay(tmp_path):
    """Audit d2-backup-retention-text: Der Datenschutztext sagt 14 Tage. Der Timer startet mit
    Zufallsverzögerung; heute früher als vor 14 Tagen heißt: Die alte Sicherung ist ein paar
    Minuten jünger als 14 Tage. Sie muss trotzdem heute weg, nicht erst morgen (Tag 15)."""
    bk = load_script("backup")
    now = datetime(2026, 10, 30, 3, 41, tzinfo=timezone.utc)  # heute: Start nach 1 Min. Verzögerung
    old = backup_name(now - timedelta(days=13, hours=23, minutes=50))  # vor 14 Tagen nach 11 Min.
    young = backup_name(now - timedelta(days=13, hours=20))  # z. B. von Hand gesichert
    newest = backup_name(now)
    for name in (old, young, newest):
        (tmp_path / name).write_text("x")
    assert [p.name for p in bk.prune(tmp_path, 14, now, keep=tmp_path / newest)] == [old]
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([young, newest])


def test_backup_prune_slack_covers_the_timer():
    """Der Spielraum beim Löschen muss die Zufallsverzögerung des Timers und die Stunde der
    Sommerzeit-Umstellung abdecken (Timer in Ortszeit, Dateinamen in UTC), sonst bleibt die
    Sicherung von vor 14 Tagen wieder bis zum 15. Tag liegen."""
    bk = load_script("backup")
    timer = unit("tatilvakti-v2-backup.timer")
    assert timer["OnCalendar"] == ["*-*-* 03:40:00"]  # einmal am Tag, in Ortszeit
    delay = timer["RandomizedDelaySec"][0]
    assert delay.endswith("min")
    assert int(delay[:-3]) * 60 + 3600 < bk.PRUNE_SLACK_S < 86400 / 2


def test_backup_prune_keeps_14_days_all_year(tmp_path):
    """Review zu d2-backup-retention-text: Der Timer läuft um 03:40 Ortszeit (Europe/Berlin), die
    Dateinamen tragen UTC. Für jeden Tag des Jahres, auch über beide Zeitumstellungen, im
    ungünstigsten Fall der Zufallsverzögerung: Der Lauf am 14. Tag löscht die Sicherung von damals
    und behält die vom Tag danach. Mit einer Stunde Spielraum blieb im März die Sicherung vom
    18.03. (02:55 UTC) beim Lauf am 01.04. (01:40 UTC) liegen."""
    bk = load_script("backup")
    berlin = ZoneInfo("Europe/Berlin")
    delay = timedelta(minutes=int(unit("tatilvakti-v2-backup.timer")["RandomizedDelaySec"][0][:-3]))

    def run(day: date, after: timedelta = timedelta(0)) -> datetime:
        return datetime.combine(day, time(3, 40), tzinfo=berlin).astimezone(timezone.utc) + after

    for offset in range(366):
        day = date(2026, 1, 1) + timedelta(days=offset)
        dest = tmp_path / day.isoformat()
        dest.mkdir()
        old, nxt = backup_name(run(day, delay)), backup_name(run(day + timedelta(days=1)))
        for name in (old, nxt):
            (dest / name).write_text("x")
        today = day + timedelta(days=14)
        keep = dest / backup_name(run(today))  # die neue Sicherung dieses Laufs
        assert [p.name for p in bk.prune(dest, 14, run(today), keep=keep)] == [old], day
        assert bk.prune(dest, 14, run(today, delay), keep=keep) == [], day


def test_backup_failure_still_prunes_and_keeps_the_newest(tmp_path, capsys):
    """Scheitert die Sicherung (hier: DB fehlt), löscht der Lauf trotzdem, was über der Frist liegt.
    Die neueste vorhandene Sicherung bleibt, auch wenn sie selbst schon alt ist."""
    bk = load_script("backup")
    dest = tmp_path / "backups"
    dest.mkdir()
    now = datetime.now(timezone.utc)
    names = [backup_name(now - timedelta(days=d)) for d in (20, 30)]
    for name in names:
        (dest / name).write_text("x")
    assert bk.main(["--db", str(tmp_path / "fehlt.db"), "--dest", str(dest)]) == 1
    err = capsys.readouterr().err
    assert "FEHLER: Datenbank fehlt" in err and "removed=1" in err
    assert [p.name for p in dest.iterdir()] == [names[0]]
    # Fehlt auch das Zielverzeichnis, bleibt es beim eigentlichen Fehler
    assert bk.main(["--db", str(tmp_path / "fehlt.db"), "--dest", str(tmp_path / "nirgends")]) == 1
    assert "FEHLER: Datenbank fehlt" in capsys.readouterr().err


def test_owner_check_refuses_foreign_db(tmp_path, monkeypatch):
    bk = load_script("backup")
    db = tmp_path / "x.db"
    db.write_bytes(b"")
    monkeypatch.setattr(os, "geteuid", lambda: db.stat().st_uid + 1)
    with pytest.raises(PermissionError):
        bk.check_owner(db)


# ------------------------------------------------- deploy.sh/rollback.sh mit systemctl-Attrappe

SYSTEMCTL_STUB = r"""#!/usr/bin/env bash
# systemctl-Attrappe: Zustand in $STUB/state, Start-Sperre (start-limit-hit) in $STUB/limit,
# /healthz als Datei $STUB/healthz.json (urllib liest file://-URLs), jeder Aufruf in $STUB/calls.
# Ein Release mit Datei HEALTHZ antwortet mit deren Inhalt (z. B. status down), sonst status ok.
echo "$*" >>"$STUB/calls"
state=$(cat "$STUB/state" 2>/dev/null || echo inactive)
case $1 in
    is-active)
        [ "$2" = --quiet ] || echo "$state"
        [ "$state" = active ] ;;
    reset-failed)
        rm -f "$STUB/limit"
        [ "$state" != failed ] || echo inactive >"$STUB/state" ;;
    restart)
        if [ -e "$STUB/limit" ]; then echo "Start request repeated too quickly." >&2; exit 1; fi
        rm -f "$STUB/healthz.json"
        rel=$(readlink -f "$TV_RELEASE_BASE/current")
        if [ -e "$rel/BROKEN" ]; then
            # Wie gunicorn mit Type=notify: Der Master meldet READY, die Worker scheitern beim Booten,
            # systemd startet bis StartLimitBurst neu und gibt dann auf (failed, start-limit-hit)
            echo failed >"$STUB/state"
            touch "$STUB/limit"
        else
            echo active >"$STUB/state"
            if [ -e "$rel/HEALTHZ" ]; then
                cp "$rel/HEALTHZ" "$STUB/healthz.json"
            else
                printf '{"status": "ok", "db": true, "build": "%s"}' "$(cat "$rel/BUILD")" >"$STUB/healthz.json"
            fi
        fi ;;
    *) echo "unerwartet: $*" >&2; exit 1 ;;
esac
"""


class Releases:
    """Testaufbau: /opt/tatilvakti-v2 mit den Releases r1 und r2 unter tmp_path, systemctl-Attrappe."""

    def __init__(self, tmp_path: Path):
        self.base = tmp_path / "opt"
        self.stub = tmp_path / "stub"
        for name in ("r1", "r2"):
            rel = self.base / "releases" / name
            (rel / ".venv" / "bin").mkdir(parents=True)
            gunicorn = rel / ".venv" / "bin" / "gunicorn"
            gunicorn.write_text("#!/bin/sh\n")
            gunicorn.chmod(0o755)
            (rel / "BUILD").write_text(f"build-{name}")
        (self.stub / "bin").mkdir(parents=True)
        systemctl = self.stub / "bin" / "systemctl"
        systemctl.write_text(SYSTEMCTL_STUB)
        systemctl.chmod(0o755)
        self.env = {**os.environ, "PATH": f"{self.stub / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}",
                    "STUB": str(self.stub), "TV_RELEASE_BASE": str(self.base),
                    "TV_RELEASE_HEALTH_URL": (self.stub / "healthz.json").as_uri(),
                    "TV_RELEASE_HEALTH_TRIES": "1"}

    def setup(self, current: str, previous: str | None, state: str, limit: bool = False) -> None:
        os.symlink(f"releases/{current}", self.base / "current")
        if previous:
            os.symlink(f"releases/{previous}", self.base / "previous")
        (self.stub / "state").write_text(state)
        if limit:
            (self.stub / "limit").touch()

    def link(self, name: str) -> str | None:
        path = self.base / name
        return os.readlink(path) if path.is_symlink() else None

    def state(self) -> str:
        return (self.stub / "state").read_text().strip()

    def calls(self) -> list[str]:
        path = self.stub / "calls"
        return [c.split()[0] for c in path.read_text().splitlines()] if path.exists() else []

    def build(self) -> str | None:
        import json
        path = self.stub / "healthz.json"
        return json.loads(path.read_text())["build"] if path.exists() else None

    def bash(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", *args], env=self.env, capture_output=True, text=True, timeout=60)

    def activate(self, release: str, build: str) -> subprocess.CompletedProcess:
        """Schritt 4/5 von deploy.sh (activate_release aus release-lib.sh)."""
        return self.bash("-c", '. "$1" && activate_release "$2" "$3"', "_",
                         str(SCRIPTS / "release-lib.sh"), f"releases/{release}", build)

    def rollback(self, *args: str) -> subprocess.CompletedProcess:
        return self.bash(str(SCRIPTS / "rollback.sh"), *args)

    def healthz(self, release: str, **data) -> None:
        """Antwort von /healthz für ein Release festlegen (Build-ID passend zum Release)."""
        import json
        body = {"build": f"build-{release}", "attention": [], **data}
        (self.base / "releases" / release / "HEALTHZ").write_text(json.dumps(body))


@pytest.fixture
def releases(tmp_path):
    if not (shutil.which("bash") and shutil.which("flock")):
        pytest.skip("bash oder flock fehlt")
    return Releases(tmp_path)


def test_deploy_rolls_back_and_restarts_after_start_limit(releases):
    """Bootet das neue Release nicht, steht die Unit nach den Neustartversuchen auf 'failed'
    (start-limit-hit). Der automatische Rollback muss das alte Release trotzdem wieder starten."""
    releases.setup("r1", None, "active")
    (releases.base / "releases" / "r2" / "BROKEN").touch()
    res = releases.activate("r2", "build-r2")
    assert res.returncode == 1 and "Deploy zurückgerollt" in res.stderr, res.stdout + res.stderr
    assert releases.link("current") == "releases/r1" and releases.link("previous") is None
    assert releases.state() == "active" and releases.build() == "build-r1"
    assert releases.calls() == ["is-active", "reset-failed", "restart", "reset-failed", "restart"]


def test_deploy_activates_and_checks_build(releases):
    releases.setup("r1", None, "active")
    res = releases.activate("r2", "build-r2")
    assert res.returncode == 0, res.stdout + res.stderr
    assert releases.link("current") == "releases/r2" and releases.link("previous") == "releases/r1"
    assert releases.build() == "build-r2" and "/healthz: status=ok build=build-r2" in res.stdout


def test_deploy_keeps_release_with_only_the_salt_db_down(releases):
    """503 nur wegen der Schlüssel-DB: Die Ursache liegt meist außerhalb des Releases (/run), ein
    Rollback hilft nicht. deploy.sh bleibt beim neuen Release und WARNT."""
    releases.setup("r1", None, "active")
    releases.healthz("r2", status="down", down=["salt_db"], db=True, salt_db=False)
    res = releases.activate("r2", "build-r2")
    assert res.returncode == 0, res.stdout + res.stderr
    assert releases.link("current") == "releases/r2"
    assert "down=['salt_db']" in res.stdout and "WARNUNG: Schlüssel-DB" in res.stderr


def test_deploy_rolls_back_when_the_main_db_is_down(releases):
    """503 wegen der Haupt-DB: Das Release kann keine Meldung speichern, also zurück."""
    releases.setup("r1", None, "active")
    releases.healthz("r2", status="down", down=["db"], db=False, salt_db=True)
    res = releases.activate("r2", "build-r2")
    assert res.returncode == 1 and "Deploy zurückgerollt" in res.stderr, res.stdout + res.stderr
    assert "Letzte Antwort: /healthz: status=down" in res.stdout and "Ausfall: db" in res.stdout
    assert releases.link("current") == "releases/r1" and releases.build() == "build-r1"


def test_deploy_treats_unknown_down_reasons_as_failure(releases):
    releases.setup("r1", None, "active")
    releases.healthz("r2", status="down", down=["neu"], db=True, salt_db=True)
    res = releases.activate("r2", "build-r2")
    assert res.returncode == 1 and "Ausfall: neu" in res.stdout, res.stdout + res.stderr


def test_rollback_to_an_older_release_reads_its_healthz(releases):
    """Ältere Releases kennen 'down' nicht: kaputte Schlüssel-DB = HTTP 200 mit salt_db=false."""
    releases.setup("r2", "r1", "active")
    releases.healthz("r1", status="attention", db=True, salt_db=False, attention=["salt_db"])
    res = releases.rollback("--no-preflight")
    assert res.returncode == 0, res.stdout + res.stderr
    assert releases.link("current") == "releases/r1" and "WARNUNG: Schlüssel-DB" in res.stderr


def test_rollback_from_the_current_release_finds_its_health_check(releases):
    """So läuft es auf Hermes: current/scripts/rollback.sh. current zeigt danach auf ein älteres
    Release ohne healthcheck.py; die Prüfung muss trotzdem aus dem bisherigen Release kommen."""
    releases.setup("r2", "r1", "active")
    scripts = releases.base / "releases" / "r2" / "scripts"
    scripts.mkdir()
    for name in ("rollback.sh", "release-lib.sh", "healthcheck.py"):
        shutil.copy2(SCRIPTS / name, scripts / name)
    res = releases.bash(str(releases.base / "current" / "scripts" / "rollback.sh"), "--no-preflight")
    assert res.returncode == 0, res.stdout + res.stderr
    assert releases.link("current") == "releases/r1" and "/healthz: status=ok build=build-r1" in res.stdout


def test_deploy_leaves_stopped_service_stopped(releases):
    releases.setup("r1", None, "inactive")
    res = releases.activate("r2", "build-r2")
    assert res.returncode == 0 and "kein Neustart" in res.stdout
    assert releases.link("current") == "releases/r2" and "restart" not in releases.calls()


def test_rollback_restarts_failed_service(releases):
    """Typischer Fall: Das aktive Release ist abgestürzt (failed nach start-limit-hit)."""
    releases.setup("r2", "r1", "failed", limit=True)
    (releases.base / "releases" / "r2" / "BROKEN").touch()
    res = releases.rollback("--no-preflight")
    assert res.returncode == 0, res.stdout + res.stderr
    assert releases.link("current") == "releases/r1" and releases.link("previous") == "releases/r2"
    assert releases.state() == "active" and releases.build() == "build-r1"


def test_rollback_starts_stopped_service_only_with_start(releases):
    releases.setup("r2", "r1", "inactive")
    res = releases.rollback("--no-preflight")
    assert res.returncode == 0 and "kein Neustart" in res.stdout
    assert releases.link("current") == "releases/r1" and "restart" not in releases.calls()
    res = releases.rollback("--no-preflight", "--start")
    assert res.returncode == 0, res.stdout + res.stderr
    assert releases.link("current") == "releases/r2" and releases.build() == "build-r2"


def test_rollback_refuses_while_locked(releases):
    import fcntl
    releases.setup("r2", "r1", "active")
    with open(releases.base / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        res = releases.rollback("--no-preflight")
    assert res.returncode == 1 and "läuft bereits" in res.stderr
    assert releases.link("current") == "releases/r2" and releases.calls() == []


# ---------------------------------------------------------------- restore.sh mit systemctl-Attrappe

# Für Abbruch-Tests in den Attrappen: meldet über $STUB/waiting, dass restore.sh gerade hier wartet,
# und hält an, bis der Test $STUB/go anlegt (höchstens 30 s)
HOLD = r"""
hold() {
    touch "$STUB/waiting"
    for _ in $(seq 1 600); do [ -e "$STUB/go" ] && return 0; sleep 0.05; done
}
"""

RESTORE_STUB = "#!/usr/bin/env bash" + HOLD + r"""
# systemctl-Attrappe für restore.sh: jeder Aufruf mit allen Argumenten in $STUB/calls. Units sind
# 'active', außer $STUB/state-<unit> nennt einen anderen Zustand. Nach dem Start liegt /healthz als
# Datei $STUB/healthz.json bereit (urllib liest file://-URLs), mit der Datei $STUB/unhealthy als
# Ausfall der Haupt-DB. Mit $STUB/hold-stop bzw. $STUB/hold-start halten das Stoppen des Dienstes
# bzw. der Start an (hold).
echo "$*" >>"$STUB/calls"
case $1 in
    is-active)
        state=active
        [ ! -e "$STUB/state-$2" ] || state=$(cat "$STUB/state-$2")
        echo "$state"
        [ "$state" = active ] ;;
    stop)
        case " $* " in *" tatilvakti-v2.service "*) [ ! -e "$STUB/hold-stop" ] || hold ;; esac
        rm -f "$STUB/healthz.json" ;;
    reset-failed) ;;
    start)
        [ ! -e "$STUB/hold-start" ] || hold
        if [ -e "$STUB/unhealthy" ]; then
            echo '{"status": "down", "down": ["db"], "db": false, "salt_db": true, "build": "b"}' >"$STUB/healthz.json"
        else
            echo '{"status": "ok", "down": [], "db": true, "salt_db": true, "build": "b"}' >"$STUB/healthz.json"
        fi ;;
    *) echo "unerwartet: $*" >&2; exit 1 ;;
esac
"""

SERVICE_AND_TIMERS = ["tatilvakti-v2.service", "tatilvakti-v2-maintenance.timer", "tatilvakti-v2-backup.timer"]

RESTORE_STOP_START = [
    *(f"is-active {name}" for name in SERVICE_AND_TIMERS),
    "stop tatilvakti-v2-maintenance.timer tatilvakti-v2-backup.timer",
    "stop tatilvakti-v2-maintenance.service tatilvakti-v2-backup.service tatilvakti-v2.service",
    "reset-failed tatilvakti-v2.service",
    "start tatilvakti-v2.service tatilvakti-v2-maintenance.timer tatilvakti-v2-backup.timer",
]


def db_state(path: Path) -> tuple[str, int | None]:
    """(integrity_check, Zahl der Meldungen), so wie SQLite die Datei samt -wal liest."""
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        try:
            return (conn.execute("PRAGMA integrity_check").fetchone()[0],
                    conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0])
        except sqlite3.DatabaseError as exc:
            return str(exc), None
    finally:
        conn.close()


class Restore:
    """Testaufbau für restore.sh unter tmp_path: opt/ (nur für die Sperre), lib/ als
    /var/lib/tatilvakti-v2 mit backups/, Dienstbenutzer ist der aufrufende Benutzer (kein root
    nötig), systemctl-Attrappe."""

    def __init__(self, tmp_path: Path):
        import pwd
        self.tmp = tmp_path
        self.base = tmp_path / "opt"
        self.data = tmp_path / "lib"
        self.db = self.data / "tatilvakti.db"
        self.backups = self.data / "backups"
        self.stub = tmp_path / "stub"
        self.base.mkdir()
        self.backups.mkdir(parents=True)
        (self.stub / "bin").mkdir(parents=True)
        systemctl = self.stub / "bin" / "systemctl"
        systemctl.write_text(RESTORE_STUB)
        systemctl.chmod(0o755)
        self.env = {**os.environ, "PATH": f"{self.stub / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}",
                    "STUB": str(self.stub), "TV_RELEASE_BASE": str(self.base), "TV_RELEASE_DB": str(self.db),
                    "TV_RELEASE_USER": pwd.getpwuid(os.getuid()).pw_name,
                    "TV_RELEASE_HEALTH_URL": (self.stub / "healthz.json").as_uri(),
                    "TV_RELEASE_HEALTH_TRIES": "1", "TV_RESTORE_PYTHON": sys.executable}

    def backup(self, when: datetime = datetime(2026, 10, 7, 3, 40, tzinfo=timezone.utc)) -> Path:
        """Echte Sicherung mit scripts/backup.py aus einer App-DB mit 20 Meldungen (make_db)."""
        src = self.tmp / "app" / when.strftime("%Y%m%dT%H%M%SZ") / "tatilvakti.db"
        make_db(src)
        target, info = load_script("backup").backup(src, self.backups, when)
        assert info["reports"] == 20
        return target

    def crash(self) -> dict[str, bytes]:
        """Absturz nachstellen (Audit d5-restore-stale-wal): tatilvakti.db mit 200 Meldungen in einer
        -wal, die kein Checkpoint mehr erreicht hat, weil der Prozess ohne close endete (wie nach
        SIGKILL). Gibt den Inhalt aller Dateien im Datenverzeichnis zurück."""
        from tatilvakti.db import init_db
        init_db(str(self.db))  # legt auch die Schlüssel-DB daneben an (tatilvakti-salts.db)
        code = ("import os, sqlite3, sys\n"
                "c = sqlite3.connect(sys.argv[1], isolation_level=None)\n"
                "c.execute('PRAGMA journal_mode=WAL')\n"
                "c.execute('PRAGMA wal_autocheckpoint=0')\n"
                "for i in range(200):\n"
                "    c.execute(\"INSERT INTO reports (crossing, direction, bucket, observed_at, created_at, client)\"\n"
                "              \" VALUES ('kapikule', 'to_de', 2, ?, ?, 'alt')\", (i, i))\n"
                "os._exit(0)\n")
        subprocess.run([sys.executable, "-c", code, str(self.db)], check=True, timeout=60)
        assert db_state(self.db) == ("ok", 200)  # vor dem Abbild: Leser schreiben in die -shm
        files = self.files()
        assert {"tatilvakti.db", "tatilvakti.db-wal", "tatilvakti.db-shm"} <= set(files)
        return files

    def files(self) -> dict[str, bytes]:
        return {p.name: p.read_bytes() for p in self.data.iterdir() if p.is_file()}

    def pre_restore(self) -> list[Path]:
        return sorted(self.data.glob("pre-restore-*"))

    def calls(self) -> list[str]:
        path = self.stub / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(SCRIPTS / "restore.sh"), *args], env=self.env,
                              capture_output=True, text=True, timeout=180)

    def go_back(self, commands: list[str]) -> None:
        """Den ausgegebenen Weg zurück Zeile für Zeile ausführen, wie ihn jemand kopiert."""
        for command in commands:
            subprocess.run(["bash", "-c", command], env=self.env, check=True, timeout=30)


def way_back_commands(stderr: str) -> list[str]:
    """Die Befehle unter „Zurück auf den Stand vor dem Restore:“ in der Ausgabe von restore.sh."""
    way_back = stderr.split("Zurück auf den Stand vor dem Restore:\n", 1)[1]
    return [line.strip() for line in way_back.splitlines() if line.startswith("  ")]


def assert_note(old: Path, stderr: str, why: str) -> None:
    """pre-restore-*/zurueck.txt nennt den Grund und genau den ausgegebenen Weg zurück."""
    note = old / "zurueck.txt"
    assert stat.S_IMODE(note.stat().st_mode) == 0o600
    head, text = note.read_text().split("\n", 1)
    assert head.startswith("restore.sh ") and head.endswith(why), head
    assert stderr.endswith(f"{text}Diese Befehle stehen auch in {note}.\n"), stderr


@pytest.fixture
def restore(tmp_path):
    if not (shutil.which("bash") and shutil.which("flock")):
        pytest.skip("bash oder flock fehlt")
    return Restore(tmp_path)


def test_restore_moves_a_stale_wal_aside(restore, clock):
    """Audit d5-restore-stale-wal und d5-restore-untested: Nach einem Absturz liegt eine -wal neben
    der DB. Wer die Sicherung nur über die .db legt, bekommt alte Meldungen oder eine beschädigte DB.
    restore.sh verschiebt die alten Dateien, spielt die Sicherung zurück und startet den Dienst; die
    App läuft danach auf der Sicherung und nimmt Meldungen an."""
    backup = restore.backup()
    before = restore.crash()

    # Das Szenario aus dem Bericht: Sicherung über die .db gelegt, die alte -wal liegt noch da
    naive = restore.tmp / "naive"
    naive.mkdir()
    for name in ("tatilvakti.db", "tatilvakti.db-wal", "tatilvakti.db-shm"):
        (naive / name).write_bytes(before[name])
    shutil.copyfile(backup, naive / "tatilvakti.db")
    assert db_state(naive / "tatilvakti.db") != ("ok", 20)

    res = restore.run(str(backup))
    assert res.returncode == 0, res.stdout + res.stderr
    assert db_state(restore.db) == ("ok", 20)
    assert stat.S_IMODE(restore.db.stat().st_mode) == 0o600 and restore.db.stat().st_uid == os.getuid()
    assert restore.db.read_bytes() == backup.read_bytes()
    # Der bisherige Stand liegt vollständig und unverändert unter pre-restore-*, nichts gelöscht
    [old] = restore.pre_restore()
    assert stat.S_IMODE(old.stat().st_mode) == 0o700
    assert {p.name: p.read_bytes() for p in old.iterdir()} == {
        name: data for name, data in before.items() if name.startswith("tatilvakti.db")}
    # Keine -wal/-shm neben der zurückgespielten DB, keine Arbeitskopie, Schlüssel-DB unberührt
    after = restore.files()
    assert sorted(after) == ["tatilvakti-salts.db", "tatilvakti.db"]
    assert after["tatilvakti-salts.db"] == before["tatilvakti-salts.db"]
    assert restore.calls() == RESTORE_STOP_START
    assert "Sicherung geprüft: integrity_check ok, 20 Meldungen" in res.stdout
    assert "/healthz: status=ok" in res.stdout and f"Bisheriger Stand: {old}" in res.stdout
    assert "Restore fertig: tatilvakti-20261007T034000Z.db, 20 Meldungen" in res.stdout
    assert f"rm -r -- {old}\n" in res.stdout and "Gestoppt wie vor dem Restore" not in res.stdout

    # Die App startet auf der zurückgespielten Sicherung, zeigt die Meldungen und nimmt neue an
    from tatilvakti import create_app
    client = create_app({"TESTING": True, "TV_DB_PATH": str(restore.db), "TV_CLOCK": clock}).test_client()
    health = client.get("/healthz")
    assert health.status_code == 200 and health.get_json()["db"] is True
    assert client.get("/api/v1/borders/kapikule").get_json()["directions"]["to_tr"]["count"] == 20
    resp = client.post("/api/v1/borders/kapikule/reports", json={"direction": "to_tr", "bucket": 3})
    assert resp.status_code == 201
    assert resp.get_json()["crossing"]["directions"]["to_tr"]["count"] == 21


def test_restore_without_previous_db(restore):
    """Neuer Host: Es gibt noch keine DB. Kein leeres pre-restore-Verzeichnis bleibt zurück."""
    backup = restore.backup()
    res = restore.run(str(backup))
    assert res.returncode == 0, res.stdout + res.stderr
    assert "Keine bisherige Datenbank vorhanden" in res.stdout
    assert db_state(restore.db) == ("ok", 20) and restore.pre_restore() == []
    assert restore.calls() == RESTORE_STOP_START


def _broken(restore: Restore, kind: str) -> Path:
    path = restore.tmp / f"{kind}.db"
    if kind == "kaputt":  # echte Sicherung, alle Seiten nach der ersten beschädigt
        raw = restore.backup().read_bytes()
        path.write_bytes(raw[:4096] + b"\xff" * (len(raw) - 4096))
        assert load_script("backup").main(["--verify", str(path)]) == 1
    elif kind == "pruefwerte":  # Kopie ohne Bereinigung, etwa von Hand gezogen
        src = restore.tmp / "app.db"
        make_db(src)
        source, target = sqlite3.connect(str(src)), sqlite3.connect(str(path))
        try:
            source.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
        finally:
            source.close()
            target.close()
    elif kind == "wal":  # Dateikopie einer laufenden DB: WAL-Modus, die -wal fehlt
        src = restore.tmp / "app.db"
        make_db(src)
        shutil.copyfile(src, path)
    else:
        path.write_text("keine Datenbank\n")
    return path


@pytest.mark.parametrize("kind, needle", [
    ("kaputt", "Sicherung fehlerhaft"),
    ("pruefwerte", "noch Prüfwerte"),
    ("wal", "nicht im Rollback-Journal-Modus"),
    ("fremd", "keine SQLite-Datenbank"),
])
def test_restore_refuses_a_bad_backup_without_changes(restore, kind, needle):
    """Fehlerhafte Sicherung: Abbruch, bevor irgendetwas gestoppt oder verschoben ist."""
    bad = _broken(restore, kind)
    before = restore.crash()
    res = restore.run(str(bad))
    assert res.returncode == 1, res.stdout + res.stderr
    assert needle in res.stderr and "Abgebrochen, nichts geändert." in res.stderr
    assert restore.files() == before and restore.pre_restore() == []
    assert restore.calls() == []


def test_restore_names_the_way_back_when_unhealthy(restore):
    """Meldet /healthz nach dem Restore einen Ausfall, endet das Skript mit Fehler, lässt den Restore
    stehen und nennt die Befehle zurück. Genau diese Befehle stellen den alten Stand wieder her."""
    backup = restore.backup()
    before = restore.crash()
    (restore.stub / "unhealthy").touch()
    res = restore.run(str(backup))
    assert res.returncode == 1, res.stdout + res.stderr
    assert "nach dem Restore nicht gesund" in res.stderr and "Ausfall: db" in res.stdout
    assert db_state(restore.db) == ("ok", 20)
    [old] = restore.pre_restore()
    assert f"Abgebrochen.\nDer bisherige Stand liegt in {old}.\n" in res.stderr
    assert_note(old, res.stderr, f"FEHLER: tatilvakti-v2.service nach dem Restore nicht gesund: "
                                 "journalctl -u tatilvakti-v2.service -n 50")

    commands = way_back_commands(res.stderr)
    assert commands[:2] == ["systemctl " + c for c in RESTORE_STOP_START[3:5]]
    assert all(f"{restore.db}{suffix}" in commands[2] for suffix in ("-wal", "-shm", "-journal"))
    assert commands[-2:] == ["systemctl " + c for c in RESTORE_STOP_START[-2:]]
    restore.go_back(commands)
    assert restore.files() == before and db_state(restore.db) == ("ok", 200)
    assert [p.name for p in old.iterdir()] == ["zurueck.txt"]
    assert restore.calls()[-2:] == RESTORE_STOP_START[-2:]


@pytest.mark.parametrize("where, sig, group, moved", [
    # kill (SIGTERM) an das Skript, während es den Dienst stoppt: noch nichts verschoben
    ("stop", signal.SIGTERM, False, []),
    # Verbindung weg (SIGHUP) beim Verschieben der -wal: dieses mv läuft noch zu Ende
    ("mv", signal.SIGHUP, False, ["tatilvakti.db", "tatilvakti.db-wal"]),
    # Strg-C (SIGINT an die ganze Prozessgruppe) an derselben Stelle: das mv der -wal stirbt mit
    ("mv", signal.SIGINT, True, ["tatilvakti.db"]),
    # kill beim Start: Die Sicherung ist schon eingespielt, der Weg zurück räumt sie wieder weg
    ("start", signal.SIGTERM, False, ["tatilvakti.db", "tatilvakti.db-wal", "tatilvakti.db-shm"]),
])
def test_restore_names_the_way_back_when_interrupted(restore, where, sig, group, moved):
    """Review: Ein Abbruch per Signal nach dem Stoppen nannte keinen Weg zurück (die EXIT-Falle sah
    Exit 0), und mitten im Verschieben blieb die DB ohne jeden Hinweis halb verschoben. Ein
    `systemctl start` hätte dann die alte -wal gelöscht. Jetzt endet das Skript mit 128 + Signal und
    nennt den Weg zurück, ab dem Verschieben auch in zurueck.txt; welche Dateien verschoben sind,
    liest es aus pre-restore-* selbst. Die Befehle stellen den alten Stand wieder her."""
    backup = restore.backup()
    before = restore.crash()
    stub_mv = restore.stub / "bin" / "mv"
    if where in ("stop", "start"):
        (restore.stub / f"hold-{where}").touch()
    else:
        stub_mv.write_text("#!/usr/bin/env bash" + HOLD + 'case "$*" in *tatilvakti.db-wal*) hold ;; esac\n'
                           f'exec {shutil.which("mv")} "$@"\n')
        stub_mv.chmod(0o755)
    proc = subprocess.Popen(["bash", str(SCRIPTS / "restore.sh"), str(backup)], env=restore.env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        deadline = monotonic() + 60
        while not (restore.stub / "waiting").exists():
            assert proc.poll() is None and monotonic() < deadline, "restore.sh erreicht die Attrappe nicht"
            sleep(0.02)
        if group:
            os.killpg(proc.pid, sig)
        else:
            proc.send_signal(sig)
        (restore.stub / "go").touch()
        out, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    assert proc.returncode == 128 + sig, out + err
    assert f"Abgebrochen durch {sig.name}.\n" in err
    assert restore.calls() == RESTORE_STOP_START[:None if where == "start" else 5]
    # Erst beim Start abgebrochen: Die zurückgespielte DB liegt geprüft da, nur /healthz ist offen
    assert ("offen war nur der Start bzw. /healthz" in err) is (where == "start")

    commands = way_back_commands(err)
    start = ["systemctl " + c for c in RESTORE_STOP_START[-2:]]
    if moved:
        [old] = restore.pre_restore()
        assert f"Der bisherige Stand liegt in {old}.\n" in err
        assert sorted(p.name for p in old.iterdir()) == sorted([*moved, "zurueck.txt"])
        expected = [f"mv -- {' '.join(str(old / name) for name in moved)} {restore.data}/", *start]
        if where == "start":
            assert db_state(restore.db) == ("ok", 20)
            expected[:0] = ["systemctl " + c for c in RESTORE_STOP_START[3:5]] + [
                "rm -f -- " + " ".join(f"{restore.db}{suffix}" for suffix in ("", "-wal", "-shm", "-journal"))]
        assert commands == expected
        assert_note(old, err, f"Signal {sig.name}")
        stub_mv.unlink(missing_ok=True)
    else:
        # Vor dem Verschieben: kein pre-restore-Verzeichnis, die Dateien liegen unverändert da
        assert restore.pre_restore() == [] and restore.files() == before
        assert commands == start
    restore.go_back(commands)
    assert restore.files() == before and db_state(restore.db) == ("ok", 200)
    assert restore.calls()[-2:] == RESTORE_STOP_START[-2:]


@pytest.mark.parametrize("states, args, started", [
    # Dienst und Wartung bewusst gestoppt: danach läuft nur der Backup-Timer wieder
    ({"tatilvakti-v2.service": "inactive", "tatilvakti-v2-maintenance.timer": "inactive"}, [],
     ["tatilvakti-v2-backup.timer"]),
    # --start startet alles
    ({"tatilvakti-v2.service": "inactive", "tatilvakti-v2-maintenance.timer": "inactive"}, ["--start"],
     SERVICE_AND_TIMERS),
    # Ein abgestürzter Dienst (failed) soll laufen, wie bei rollback.sh
    ({"tatilvakti-v2.service": "failed", "tatilvakti-v2-maintenance.timer": "inactive",
      "tatilvakti-v2-backup.timer": "inactive"}, [], ["tatilvakti-v2.service"]),
])
def test_restore_starts_what_ran_before(restore, states, args, started):
    """Nach dem Restore startet restore.sh nur, was vorher lief oder abgestürzt war; ein bewusst
    gestoppter Dienst bleibt gestoppt (wie bei rollback.sh), mit --start läuft alles."""
    backup = restore.backup()
    restore.crash()
    for name, state in states.items():
        (restore.stub / f"state-{name}").write_text(state)
    res = restore.run(*args, str(backup))
    assert res.returncode == 0, res.stdout + res.stderr
    assert db_state(restore.db) == ("ok", 20)
    service = "tatilvakti-v2.service" in started
    assert restore.calls() == [*RESTORE_STOP_START[:5], *(["reset-failed tatilvakti-v2.service"] if service else []),
                               f"start {' '.join(started)}"]
    assert ("/healthz: status=ok" in res.stdout) is service
    stopped = [name for name in SERVICE_AND_TIMERS if name not in started]
    if stopped:
        assert (f"Gestoppt wie vor dem Restore: {' '.join(stopped)}. "
                f"Starten: systemctl start {' '.join(stopped)}") in res.stdout
    assert ("/healthz ist ungeprüft" in res.stdout) is not service


def test_restore_refuses_while_locked(restore):
    import fcntl
    backup = restore.backup()
    before = restore.crash()
    with open(restore.base / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        res = restore.run(str(backup))
    assert res.returncode == 1 and "restore.sh läuft bereits" in res.stderr
    assert restore.files() == before and restore.calls() == []


def test_restore_check_starts_the_app_on_the_newest_backup(restore):
    """Restore-Drill ohne Eingriff: --check prüft die neueste Sicherung und lässt den Preflight des
    aktiven Releases gegen eine Kopie laufen. Dienst, Timer und DB bleiben unberührt."""
    older = restore.backup(datetime(2026, 10, 6, 3, 40, tzinfo=timezone.utc))
    restore.backup()
    older.write_bytes(older.read_bytes()[:4096] + b"\xff" * 4096)  # die ältere ist kaputt
    before = restore.crash()
    release = restore.base / "releases" / "r1"
    (release / ".venv" / "bin").mkdir(parents=True)
    python = release / ".venv" / "bin" / "python"
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    python.chmod(0o755)
    (release / "scripts").symlink_to(SCRIPTS)
    os.symlink("releases/r1", restore.base / "current")
    env_file = restore.tmp / "tatilvakti-v2.env"
    env_file.write_text("TV_BASE_URL=https://tatilvakti.guenlab.de\nTV_TRUST_PROXY=1\n")
    restore.env["TV_RELEASE_ENV_FILE"] = str(env_file)

    res = restore.run("--check")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "preflight ok build=" in res.stdout
    assert "Restore-Probe ok: tatilvakti-20261007T034000Z.db, 20 Meldungen" in res.stdout
    res = restore.run("--check", str(older))
    assert res.returncode == 1 and "Sicherung fehlerhaft" in res.stderr
    assert "Abgebrochen, nichts geändert." in res.stderr
    assert restore.files() == before and restore.pre_restore() == [] and restore.calls() == []


def test_restore_check_without_backups(restore):
    """Neuer Host ohne backups/: --check sagt, was fehlt (endete vorher stumm mit Exit 1)."""
    restore.backups.rmdir()
    res = restore.run("--check")
    assert res.returncode == 1 and "es lief noch keine Sicherung" in res.stderr
    assert "systemctl start tatilvakti-v2-backup.service" in res.stderr
    res = restore.run("--check", "--start")
    assert res.returncode == 1 and "--start passt nicht dazu" in res.stderr
    assert restore.calls() == []
