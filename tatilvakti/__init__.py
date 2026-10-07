"""tatilvakti – Reisevorbereitung Deutschland → Türkei (DE/TR, PWA, ohne Login)."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .content import Content, load_content, redirect_route_problems, validate
from .db import close_db, default_salt_path, init_db
from .holidays import HolidayRadar
from .i18n import Translator

try:
    from zoneinfo import ZoneInfo
    BERLIN = ZoneInfo("Europe/Berlin")
except Exception:  # pragma: no cover - ohne tzdata auf UTC zurückfallen
    BERLIN = timezone.utc

STATIC_DIR = Path(__file__).parent / "static"


@dataclass
class State:
    content: Content
    radar: HolidayRadar
    tr: Translator
    asset_hashes: dict[str, str]
    build_id: str
    # Laufzeit-Zustand je Prozess (siehe views.register: before_request)
    runtime: dict = field(default_factory=lambda: {"maintenance_checked_at": None, "forwarded_ignored": False,
                                                   "salt_db_failed": False})


PACKAGE_DIR = Path(__file__).parent
# Ändern kein gerendertes HTML: Eine neue Weiterleitung soll nicht jedes Gerät alle
# Offline-Seiten neu laden lassen.
FINGERPRINT_SKIP = {"data/redirects.json"}


def _content_fingerprint() -> str:
    """Hash über alles, was das gerenderte HTML bestimmt: Templates, Texte, Daten, Code.

    Ändert sich davon irgendetwas, bekommt der Service Worker eine neue Version und
    speichert alle Seiten neu – auch wenn kein statisches Asset geändert wurde.
    """
    digest = hashlib.sha256()
    for pattern in ("templates/*", "i18n/*.json", "data/*.json", "*.py"):
        for path in sorted(PACKAGE_DIR.glob(pattern)):
            if path.is_file() and path.relative_to(PACKAGE_DIR).as_posix() not in FINGERPRINT_SKIP:
                digest.update(path.relative_to(PACKAGE_DIR).as_posix().encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()


def _hash_assets() -> dict[str, str]:
    hashes = {}
    for path in sorted(STATIC_DIR.rglob("*")):
        if path.is_file():
            rel = path.relative_to(STATIC_DIR).as_posix()
            hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()[:10]
    return hashes


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        TV_DB_PATH=os.environ.get("TV_DB_PATH", os.path.join(app.instance_path, "tatilvakti.db")),
        # Tagesschlüssel in eigener Datei, nie sichern, darf auf tmpfs liegen (leer: neben TV_DB_PATH)
        TV_SALT_DB_PATH=os.environ.get("TV_SALT_DB_PATH", ""),
        TV_TRUST_PROXY=int(os.environ.get("TV_TRUST_PROXY", "0")),
        TV_BASE_URL=os.environ.get("TV_BASE_URL", "").rstrip("/"),
        TV_OPERATOR_NAME=os.environ.get("TV_OPERATOR_NAME", ""),
        TV_OPERATOR_ADDRESS=os.environ.get("TV_OPERATOR_ADDRESS", ""),
        TV_OPERATOR_EMAIL=os.environ.get("TV_OPERATOR_EMAIL", ""),
        # Pfade alter Service Worker, unter denen ein Kill-Switch ausgeliefert wird (kommagetrennt)
        TV_LEGACY_SW_PATHS=os.environ.get("TV_LEGACY_SW_PATHS", ""),
        TV_CLOCK=None,  # Tests: Callable, das ein UTC-datetime liefert
        SEND_FILE_MAX_AGE_DEFAULT=31536000,  # Assets tragen ?v=<hash>
        MAX_CONTENT_LENGTH=16 * 1024,
    )
    if test_config:
        app.config.update(test_config)
        if "TV_SALT_DB_PATH" not in test_config:  # Tests nie gegen eine echte Schlüssel-DB
            app.config["TV_SALT_DB_PATH"] = ""
    if not app.config["TV_SALT_DB_PATH"]:
        app.config["TV_SALT_DB_PATH"] = default_salt_path(app.config["TV_DB_PATH"])
    app.json.ensure_ascii = False

    if app.config["TV_TRUST_PROXY"]:
        hops = app.config["TV_TRUST_PROXY"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops)

    content = load_content()
    problems = validate(content)
    if problems:
        raise RuntimeError("Datensätze fehlerhaft:\n" + "\n".join(problems))

    hashes = _hash_assets()
    build_id = hashlib.sha256(("".join(hashes.values()) + _content_fingerprint()).encode()).hexdigest()[:12]
    app.extensions["tv"] = State(content, HolidayRadar(content.holidays), Translator(), hashes, build_id)

    init_db(app.config["TV_DB_PATH"], app.config["TV_SALT_DB_PATH"])
    app.teardown_appcontext(close_db)

    from . import api, cli, views
    views.register(app)
    api.register(app)
    cli.register(app)
    # Erst nach allen eigenen Routen: Kill-Switch-Pfade und alte URLs dürfen keine treffen
    views.register_legacy_sw(app, views.parse_legacy_sw_paths(app.config["TV_LEGACY_SW_PATHS"]))
    problems = redirect_route_problems(content.redirects, lambda path: views.is_route(app, path))
    if problems:
        raise RuntimeError("Weiterleitungen (data/redirects.json) fehlerhaft:\n" + "\n".join(problems))
    return app


def utcnow(app: Flask) -> datetime:
    clock = app.config.get("TV_CLOCK")
    return clock() if clock else datetime.now(timezone.utc)


def today_berlin(app: Flask) -> date:
    return utcnow(app).astimezone(BERLIN).date()
