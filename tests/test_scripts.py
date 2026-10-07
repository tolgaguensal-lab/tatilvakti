"""Betriebsskripte (scripts/) und systemd-Units (deploy/): Syntax, Schnittstellen, Backup, Preflight."""
from __future__ import annotations

import importlib.util
import os
import shutil
import sqlite3
import stat
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def make_db(path: Path) -> None:
    """Haupt-DB im Schema der App, im WAL-Modus, mit Prüfwerten (und alten Schlüsseln, falls vorhanden)."""
    from tatilvakti.db import connect, init_db
    init_db(str(path))
    conn = connect(str(path))
    try:
        now = int(datetime(2026, 10, 6, 10, tzinfo=timezone.utc).timestamp())
        conn.executemany(
            "INSERT INTO reports (crossing, direction, bucket, observed_at, created_at, client) VALUES (?, ?, ?, ?, ?, ?)",
            [("kapikule", "to_tr", 1, now - i, now - i, f"pruefwert-geheim-{i:04d}") for i in range(20)])
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
    live.executemany("INSERT INTO reports (crossing, direction, bucket, observed_at, created_at, client) "
                     "VALUES ('kapikule', 'to_de', 2, 1, 1, ?)", [(f"pruefwert-wal-{i}",) for i in range(30)])
    live.commit()
    try:
        now = datetime(2026, 10, 7, 3, 40, tzinfo=timezone.utc)
        target, info = bk.backup(db, tmp_path / "backups", now)
    finally:
        live.close()

    assert target.name == "tatilvakti-20261007T034000Z.db"
    assert info["reports"] == 50 and info["with_client"] == 0 and info["salts"] == 0
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not Path(str(target) + "-wal").exists()
    raw = target.read_bytes()
    assert b"pruefwert" not in raw and b"schluessel-geheim" not in raw  # auch nicht im freien Speicher
    copy = sqlite3.connect(str(target))
    try:
        assert copy.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        copy.close()
    # Das Original bleibt unverändert
    orig = sqlite3.connect(str(db))
    try:
        assert orig.execute("SELECT COUNT(*) FROM reports WHERE client IS NOT NULL").fetchone()[0] == 50
    finally:
        orig.close()
    assert bk.main(["--verify", str(target)]) == 0


def test_backup_prune_keeps_newest(tmp_path):
    bk = load_script("backup")
    now = datetime(2026, 10, 30, 3, 40, tzinfo=timezone.utc)
    names = [f"tatilvakti-{(now - timedelta(days=d)).strftime('%Y%m%dT%H%M%SZ')}.db" for d in (0, 13, 15, 40)]
    for name in [*names, ".tatilvakti-20261001T034000Z.db.tmp", "notiz.txt"]:
        (tmp_path / name).write_text("x")
    removed = bk.prune(tmp_path, 14, now, keep=tmp_path / names[0])
    assert sorted(p.name for p in removed) == sorted([names[2], names[3], ".tatilvakti-20261001T034000Z.db.tmp"])
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([names[0], names[1], "notiz.txt"])
    # Die neueste Sicherung bleibt auch bei Aufbewahrung von einem Tag
    assert bk.prune(tmp_path, 1, now + timedelta(days=30), keep=tmp_path / names[0]) == [tmp_path / names[1]]


def test_owner_check_refuses_foreign_db(tmp_path, monkeypatch):
    bk = load_script("backup")
    db = tmp_path / "x.db"
    db.write_bytes(b"")
    monkeypatch.setattr(os, "geteuid", lambda: db.stat().st_uid + 1)
    with pytest.raises(PermissionError):
        bk.check_owner(db)
