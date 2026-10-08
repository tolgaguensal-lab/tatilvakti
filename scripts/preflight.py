#!/usr/bin/env python3
"""Preflight für ein Release, bevor scripts/deploy.sh den Symlink umschaltet.

Im Release-Verzeichnis mit dessen venv ausführen, als Dienstbenutzer (deploy.sh macht das so):

    sudo -u tatilvakti-v2 .venv/bin/python scripts/preflight.py \
        --env-file /etc/tatilvakti-v2.env --db /var/lib/tatilvakti-v2/tatilvakti.db \
        [--test-python /pfad/zu/venv-test/bin/python] [--no-tests]

Nie als root gegen die Produktions-DB: SQLite legt beim Öffnen -wal/-shm an, gehören die root,
kann der Dienst danach nicht mehr schreiben. Das Skript bricht deshalb ab, wenn der aufrufende
Benutzer nicht Eigentümer der DB ist.

Prüft der Reihe nach:
1. Konfiguration aus der Env-Datei: TV_BASE_URL, TV_TRUST_PROXY, Impressum, keine Pfad-Variablen.
2. create_app() gegen eine Online-KOPIE der Produktions-DB in einem temporären Verzeichnis:
   Datenvalidierung und Schema. Echte DB und Salts-Datei bleiben unberührt.
3. Alle GET-Routen (jede Seite, jeder Grenzübergang, /healthz, /sw.js, Sitemap …) und jede URL
   der Precache-Liste aus /sw.js: kein Status ab 400. Sonst fehlt die Seite offline; bei einem
   Asset, einer Start- oder Offline-Seite installiert sich der neue Service Worker gar nicht (der
   alte bleibt aktiv). Kill-Switch-Pfade (TV_LEGACY_SW_PATHS) müssen JavaScript mit Status 200
   liefern. Canonical-Links müssen auf TV_BASE_URL zeigen.
4. pytest im Release, ohne TV_*-Variablen aus der Umgebung (--test-python: venv mit pytest,
   damit das Laufzeit-venv ohne Testwerkzeuge auskommt).

Letzte Zeile bei Erfolg: "preflight ok build=<id>". Exit-Code 1 bei jedem Fehler.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
SW_CONFIG_RE = re.compile(r"self\.TV_CONFIG\s*=\s*(\{.*?\});\s*\n")


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def ok(self, msg: str) -> None:
        print(f"OK       {msg}")

    def warn(self, msg: str) -> None:
        print(f"WARNUNG  {msg}")

    def error(self, msg: str) -> None:
        print(f"FEHLER   {msg}")
        self.errors.append(msg)


def parse_env_file(text: str) -> dict[str, str]:
    """Liest KEY=VALUE-Zeilen wie systemd (EnvironmentFile): Kommentare mit # oder ;, Werte
    optional in "…" oder '…'. Fortsetzungszeilen mit Backslash werden nicht unterstützt."""
    env: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#;" or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            quote, value = value[0], value[1:-1]
            if quote == '"':
                value = value.replace('\\"', '"').replace("\\\\", "\\")
        env[key] = value
    return env


def legacy_sw_paths(env: dict[str, str]) -> list[str]:
    return [p.strip() for p in env.get("TV_LEGACY_SW_PATHS", "").split(",") if p.strip()]


def check_config(env: dict[str, str], rep: Report) -> None:
    base = env.get("TV_BASE_URL", "").rstrip("/")
    if not base:
        rep.warn("TV_BASE_URL ist leer: Canonical/Teilen-Links folgen dann dem Host-Header des Proxys")
    else:
        parts = urlsplit(base)
        host = parts.hostname or ""
        if parts.scheme != "https" or not host:
            rep.error(f"TV_BASE_URL muss mit https:// beginnen: {base!r}")
        elif host.endswith((".example", ".invalid", ".test", "localhost")):
            rep.error(f"TV_BASE_URL ist ein Platzhalter: {base!r}")
        else:
            rep.ok(f"TV_BASE_URL={base}")
    hops = env.get("TV_TRUST_PROXY", "0")
    if not hops.isdigit():
        rep.error(f"TV_TRUST_PROXY muss eine Zahl sein: {hops!r}")
    elif hops == "0":
        rep.warn("TV_TRUST_PROXY=0: hinter Pangolin sähe der Spam-Schutz nur eine IP (Limit gilt dann für alle)")
    else:
        rep.ok(f"TV_TRUST_PROXY={hops}")
    # Gleiche Regel wie operator_imprint() in der App: nur Leerzeichen bzw. nur ";" zählt als leer
    missing = [k for k in ("TV_OPERATOR_NAME", "TV_OPERATOR_ADDRESS", "TV_OPERATOR_EMAIL")
               if not env.get(k, "").replace(";", "").strip()]
    if missing:
        rep.warn("Impressum unvollständig, vor dem Launch setzen: " + ", ".join(missing))
    else:
        rep.ok("Impressum-Angaben gesetzt")
    # Die Pfade gehören zu StateDirectory/RuntimeDirectory der Units. In der Env-Datei würden sie
    # die Units überschreiben: Schreiben scheitert dann an ProtectSystem=strict, oder die
    # Tagesschlüssel landen auf der Platte statt auf tmpfs.
    for key in ("TV_DB_PATH", "TV_SALT_DB_PATH"):
        if key in env:
            rep.error(f"{key} nicht in der Env-Datei setzen, das übernehmen die systemd-Units")
    for path in legacy_sw_paths(env):
        if not path.startswith("/"):
            rep.error(f"TV_LEGACY_SW_PATHS: Pfad muss mit / beginnen: {path!r}")


def check_owner(db: Path) -> None:
    """Die DB nur als ihr Eigentümer öffnen (siehe Modul-Docstring: -wal/-shm-Eigentümer)."""
    owner = db.stat().st_uid
    if hasattr(os, "geteuid") and os.geteuid() != owner:
        raise PermissionError(f"{db} gehört UID {owner}, Aufruf als UID {os.geteuid()}: "
                              "als Dienstbenutzer ausführen (z. B. sudo -u tatilvakti-v2 …)")


def copy_db(src: Path, dst: Path) -> None:
    """Konsistente Kopie per Backup-API (berücksichtigt die -wal-Datei, ändert das Original nicht)."""
    source = sqlite3.connect(str(src), timeout=30)
    try:
        target = sqlite3.connect(str(dst))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def route_urls(app, cids: list[str]) -> list[str]:
    """Alle GET-Routen ohne Pflichtparameter, Routen mit <cid> für jeden Grenzübergang."""
    adapter = app.url_map.bind("localhost")
    urls = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static" or "GET" not in (rule.methods or ()):
            continue
        needed = set(rule.arguments) - set(rule.defaults or {})
        if not needed:
            urls.append(adapter.build(rule.endpoint, {}))
        elif needed == {"cid"}:
            urls.extend(adapter.build(rule.endpoint, {"cid": cid}) for cid in cids)
    return urls


def precache_urls(sw_source: str) -> list[str] | None:
    """Seiten und Assets aus der Konfiguration, die /sw.js vorab speichert (None: nicht lesbar)."""
    match = SW_CONFIG_RE.search(sw_source)
    if not match:
        return None
    try:
        config = json.loads(match.group(1))
    except ValueError:
        return None
    return list(config.get("pages", [])) + list(config.get("assets", []))


def check_app(env: dict[str, str], db: Path | None, tmp: Path, rep: Report) -> str | None:
    os.environ.update(env)
    os.environ["TV_DB_PATH"] = str(tmp / "tatilvakti.db")
    os.environ["TV_SALT_DB_PATH"] = str(tmp / "salts.db")
    if db and db.exists():
        check_owner(db)
        copy_db(db, tmp / "tatilvakti.db")
        rep.ok(f"Kopie der Datenbank für den Test: {db}")
    else:
        rep.warn(f"keine bestehende Datenbank ({db}), Test mit leerer DB")

    sys.path.insert(0, str(ROOT))
    try:
        from tatilvakti import create_app
        app = create_app()
    except Exception as exc:  # Datenvalidierung, Schema, Import
        rep.error(f"create_app() schlägt fehl: {type(exc).__name__}: {exc}")
        return None
    client = app.test_client()

    health = client.get("/healthz")
    data = health.get_json(silent=True) or {}
    if health.status_code != 200:
        # 503: Melden ginge nicht. Hier liegen DB-Kopie und Schlüssel-DB im Temp-Verzeichnis,
        # die Ursache steckt also im Release oder in der kopierten DB, nicht in /run.
        rep.error(f"/healthz → {health.status_code}, ausgefallen: {', '.join(data.get('down') or []) or '?'} "
                  "(Grund in der Warnung darüber)")
    else:
        rep.ok(f"/healthz status={data.get('status')} build={data.get('build')}")
        if data.get("status") != "ok":
            rep.warn(f"/healthz meldet status={data.get('status')!r} {data.get('attention') or ''}")
        if data.get("due_items"):
            rep.warn(f"Prüfung fällig: {data['due_items']}")
        if data.get("imprint_ok") is False:
            rep.warn("/healthz: imprint_ok=false")

    borders = client.get("/api/v1/borders").get_json(silent=True) or {}
    cids = [c["id"] for c in borders.get("crossings", [])]
    if not cids:
        rep.error("/api/v1/borders liefert keine Grenzübergänge")
    urls = set(route_urls(app, cids))
    precache = precache_urls(client.get("/sw.js").get_data(as_text=True))
    if precache is None:
        rep.warn("Precache-Liste in /sw.js nicht lesbar, nur die Routen geprüft")
    else:
        urls.update(precache)
    failed = []
    for url in sorted(urls):
        resp = client.get(url)
        if resp.status_code >= 400:
            failed.append(f"{url} → {resp.status_code}")
    legacy = legacy_sw_paths(env)
    for path in legacy:
        resp = client.get(path)
        if resp.status_code != 200 or "javascript" not in (resp.mimetype or ""):
            failed.append(f"Kill-Switch {path} → {resp.status_code} {resp.mimetype}"
                          " (unterstützt dieses Release TV_LEGACY_SW_PATHS?)")
    if failed:
        for line in failed:
            rep.error(line)
    else:
        rep.ok(f"{len(urls) + len(legacy)} URLs ohne Fehler"
               + (f", davon {len(precache)} aus der Precache-Liste" if precache else ""))

    base = env.get("TV_BASE_URL", "").rstrip("/")
    if base:
        page = client.get("/de/").get_data(as_text=True)
        if f'href="{base}/de/' not in page:  # Canonical und hreflang
            rep.error(f"Canonical/hreflang auf /de/ zeigen nicht auf {base}")
    return data.get("build")


def run_tests(clean_env: dict[str, str], python: str, tmp: Path, rep: Report) -> None:
    env = {k: v for k, v in clean_env.items() if not k.startswith("TV_")}
    # Das Release ist für den Dienstbenutzer schreibgeschützt: keine .pyc, kein .pytest_cache
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    cmd = [python, "-m", "pytest", "-p", "no:cacheprovider", f"--basetemp={tmp / 'pytest'}"]
    try:
        result = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
    except OSError as exc:
        rep.error(f"pytest nicht startbar ({python}): {exc}")
        return
    tail = (result.stdout + result.stderr).strip().splitlines()[-15:]
    if result.returncode != 0:
        print("\n".join(tail))
        rep.error(f"pytest fehlgeschlagen (Exit-Code {result.returncode})")
    else:
        rep.ok(f"pytest: {tail[-1] if tail else 'ok'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preflight für ein tatilvakti-Release")
    parser.add_argument("--env-file", type=Path, help="z. B. /etc/tatilvakti-v2.env")
    parser.add_argument("--db", type=Path,
                        help="Produktions-DB, wird nur kopiert (z. B. /var/lib/tatilvakti-v2/tatilvakti.db)")
    parser.add_argument("--test-python", default=sys.executable,
                        help="Python mit pytest für Schritt 4 (Standard: dieses Python)")
    parser.add_argument("--no-tests", action="store_true", help="pytest auslassen (z. B. beim Rollback)")
    args = parser.parse_args(argv)

    original_env = dict(os.environ)
    rep = Report()
    env: dict[str, str] = {}
    if args.env_file:
        try:
            env = parse_env_file(args.env_file.read_text(encoding="utf-8"))
            check_config(env, rep)
        except OSError as exc:
            rep.error(f"Env-Datei nicht lesbar: {exc}")
    else:
        rep.warn("ohne --env-file: Konfiguration wird nicht geprüft")

    with tempfile.TemporaryDirectory(prefix="tv-preflight-") as name:
        tmp = Path(name)
        build = None
        try:
            build = check_app(env, args.db, tmp, rep)
        except (OSError, sqlite3.Error) as exc:
            rep.error(f"Datenbank-Kopie fehlgeschlagen: {exc}")
        if not args.no_tests:
            run_tests(original_env, args.test_python, tmp, rep)

    if rep.errors:
        print(f"preflight FEHLGESCHLAGEN ({len(rep.errors)} Fehler)")
        return 1
    print(f"preflight ok build={build}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
