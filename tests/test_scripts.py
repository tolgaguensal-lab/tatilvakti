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


def make_db(path: Path) -> None:
    """Haupt-DB im Schema der App, im WAL-Modus, mit Prüfwerten (und alten Schlüsseln, falls vorhanden).

    Fehlt die Spalte net (Anschluss-Prüfwert) im Schema dieses Stands, kommt sie dazu: Das Backup
    muss sie in jedem Fall leeren.
    """
    from tatilvakti.db import connect, init_db
    init_db(str(path))
    conn = connect(str(path))
    try:
        if "net" not in {r[1] for r in conn.execute("PRAGMA table_info(reports)")}:
            conn.execute("ALTER TABLE reports ADD COLUMN net TEXT")
        now = int(datetime(2026, 10, 6, 10, tzinfo=timezone.utc).timestamp())
        conn.executemany(
            "INSERT INTO reports (crossing, direction, bucket, observed_at, created_at, client, net) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [("kapikule", "to_tr", 1, now - i, now - i, f"pruefwert-geheim-{i:04d}", f"netz-geheim-{i:04d}")
             for i in range(20)])
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
    live.executemany("INSERT INTO reports (crossing, direction, bucket, observed_at, created_at, client, net) "
                     "VALUES ('kapikule', 'to_de', 2, 1, 1, ?, ?)",
                     [(f"pruefwert-wal-{i}", f"netz-wal-{i}") for i in range(30)])
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
    # Weder Prüfwerte (IP und Anschluss) noch Schlüssel, auch nicht im freien Speicher
    assert b"pruefwert" not in raw and b"netz-" not in raw and b"schluessel-geheim" not in raw
    copy = sqlite3.connect(str(target))
    try:
        assert copy.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert copy.execute("SELECT COUNT(*), COUNT(client), COUNT(net), COUNT(observed_at) "
                            "FROM reports").fetchone() == (50, 0, 0, 50)
    finally:
        copy.close()
    # Das Original bleibt unverändert
    orig = sqlite3.connect(str(db))
    try:
        assert orig.execute("SELECT COUNT(client), COUNT(net) FROM reports").fetchone() == (50, 50)
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
    assert "reports.net" in err and "reports.client" in err and "extra" in err


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


# ------------------------------------------------- deploy.sh/rollback.sh mit systemctl-Attrappe

SYSTEMCTL_STUB = r"""#!/usr/bin/env bash
# systemctl-Attrappe: Zustand in $STUB/state, Start-Sperre (start-limit-hit) in $STUB/limit,
# /healthz als Datei $STUB/healthz.json (urllib liest file://-URLs), jeder Aufruf in $STUB/calls.
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
            printf '{"status": "ok", "db": true, "build": "%s"}' "$(cat "$rel/BUILD")" >"$STUB/healthz.json"
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
