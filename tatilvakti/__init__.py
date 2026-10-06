"""tatilvakti – Reisevorbereitung Deutschland → Türkei (DE/TR, PWA, ohne Login)."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .content import Content, load_content, validate
from .db import close_db, init_db
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
        TV_TRUST_PROXY=int(os.environ.get("TV_TRUST_PROXY", "0")),
        TV_BASE_URL=os.environ.get("TV_BASE_URL", "").rstrip("/"),
        TV_OPERATOR_NAME=os.environ.get("TV_OPERATOR_NAME", ""),
        TV_OPERATOR_ADDRESS=os.environ.get("TV_OPERATOR_ADDRESS", ""),
        TV_OPERATOR_EMAIL=os.environ.get("TV_OPERATOR_EMAIL", ""),
        TV_CLOCK=None,  # Tests: Callable, das ein UTC-datetime liefert
        SEND_FILE_MAX_AGE_DEFAULT=31536000,  # Assets tragen ?v=<hash>
        MAX_CONTENT_LENGTH=16 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    app.json.ensure_ascii = False

    if app.config["TV_TRUST_PROXY"]:
        hops = app.config["TV_TRUST_PROXY"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops)

    content = load_content()
    problems = validate(content)
    if problems:
        raise RuntimeError("Datensätze fehlerhaft:\n" + "\n".join(problems))

    hashes = _hash_assets()
    data_fingerprint = "".join(content.meta(n)["as_of"] for n in ("holidays", "customs", "transit", "crossings"))
    build_id = hashlib.sha256(("".join(hashes.values()) + data_fingerprint).encode()).hexdigest()[:12]
    app.extensions["tv"] = State(content, HolidayRadar(content.holidays), Translator(), hashes, build_id)

    init_db(app.config["TV_DB_PATH"])
    app.teardown_appcontext(close_db)

    from . import api, views
    views.register(app)
    api.register(app)
    return app


def utcnow(app: Flask) -> datetime:
    clock = app.config.get("TV_CLOCK")
    return clock() if clock else datetime.now(timezone.utc)


def today_berlin(app: Flask) -> date:
    return utcnow(app).astimezone(BERLIN).date()
