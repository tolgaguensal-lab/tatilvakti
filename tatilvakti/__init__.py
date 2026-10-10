"""tatilvakti – Reisevorbereitung Deutschland → Türkei (DE/TR, PWA, ohne Login)."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from flask import Flask
from flask.logging import default_handler
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
        # Datenschutztext: Hoster (Name, Sitz) und Aufbewahrung des Request-Logs des Proxys in Tagen
        TV_HOSTING_PROVIDER=os.environ.get("TV_HOSTING_PROVIDER", ""),
        TV_PROXY_LOG_DAYS=os.environ.get("TV_PROXY_LOG_DAYS", ""),
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
    _journal_logging(app)  # vor der ersten Log-Zeile

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
    if app.config["TV_BASE_URL"] and operator_imprint(app.config) is None:
        # TV_BASE_URL gesetzt heißt Produktion: Ohne vollständiges Impressum nicht unbemerkt live gehen
        app.logger.warning("Impressum unvollständig: TV_OPERATOR_NAME, TV_OPERATOR_ADDRESS und TV_OPERATOR_EMAIL "
                           "setzen (/etc/tatilvakti-v2.env). Bis dahin zeigt /info nur „noch nicht eingerichtet“.")
    privacy_missing = privacy_details(app.config)["missing"]
    if app.config["TV_BASE_URL"] and privacy_missing:
        # Wie beim Impressum: Hoster und Proxy-Log-Frist gehören vor dem Launch in den Datenschutztext
        app.logger.warning("Datenschutztext unvollständig, fehlt oder ungültig: %s (/etc/tatilvakti-v2.env). Bis "
                           "dahin steht auf /info, dass die Angaben folgen.", ", ".join(privacy_missing))

    from . import api, cli, views
    views.register(app)
    api.register(app)
    cli.register(app)
    # Erst nach allen eigenen Routen: Kill-Switch-Pfade und alte URLs dürfen keine treffen
    views.register_legacy_sw(app, views.parse_legacy_sw_paths(app.config["TV_LEGACY_SW_PATHS"]))
    problems = redirect_route_problems(content.redirects, lambda path: views.own_status(app, path))
    if problems:
        raise RuntimeError("Weiterleitungen (data/redirects.json) fehlerhaft:\n" + "\n".join(problems))
    return app


OPERATOR_FIELDS = ("name", "address", "email")


def operator_imprint(config) -> dict | None:
    """Impressumsangaben aus TV_OPERATOR_*; None, solange eine davon fehlt.

    Nie ein halbes Impressum: Name, ladungsfähige Anschrift und E-Mail (§ 18 MStV, § 5 DDG)
    gibt es nur zusammen. Dieselbe Prüfung nutzen die Info-Seite, /healthz (imprint_ok) und
    die Warnung beim Start.
    """
    values = {k: str(config.get(f"TV_OPERATOR_{k.upper()}") or "").strip() for k in OPERATOR_FIELDS}
    # Adresszeilen sind mit ; getrennt; nur Trennzeichen zählt nicht als Anschrift
    values["address_lines"] = [line.strip() for line in values["address"].split(";") if line.strip()]
    if not (values["name"] and values["address_lines"] and values["email"]):
        return None
    return values


# Aufbewahrung des Proxy-Logs: ganze Tage, ohne Vorzeichen, höchstens vierstellig
PROXY_LOG_DAYS_RE = re.compile(r"[0-9]{1,4}")


def privacy_details(config) -> dict:
    """Empfänger und Speicherdauer für den Datenschutztext (Art. 13 Abs. 1 lit. e, Abs. 2 lit. a DSGVO).

    hosting: Name und Sitz des Hosters aus TV_HOSTING_PROVIDER, '' solange leer.
    proxy_log_days: aus TV_PROXY_LOG_DAYS, so viele Tage bewahrt der Reverse-Proxy sein Request-Log
    (mit IP-Adresse) auf; 0 heißt, er führt keins. None, solange leer oder keine ganze Zahl.
    missing: die Variablen, die noch fehlen oder ungültig sind.

    Nichts davon rät die App: Fehlt eine Angabe, sagt die Info-Seite, dass sie folgt. Dieselbe
    Prüfung nutzen die Info-Seite, /healthz (privacy_ok) und die Warnung beim Start.
    """
    hosting = " ".join(str(config.get("TV_HOSTING_PROVIDER") or "").split())
    raw = config.get("TV_PROXY_LOG_DAYS")
    raw = "" if raw is None else str(raw).strip()  # nicht `or ""`: 0 ist ein gültiger Wert
    days = int(raw) if PROXY_LOG_DAYS_RE.fullmatch(raw) else None
    missing = [name for name, ok in (("TV_HOSTING_PROVIDER", bool(hosting)), ("TV_PROXY_LOG_DAYS", days is not None))
               if not ok]
    return {"hosting": hosting, "proxy_log_days": days, "missing": missing}


# ------------------------------------------------------------- Logs im Journal

# syslog-Priorität je Logging-Stufe (sd-daemon(3)): crit, err, warning, info; darunter debug (7)
JOURNAL_PRIORITIES = ((logging.CRITICAL, 2), (logging.ERROR, 3), (logging.WARNING, 4), (logging.INFO, 6))


def journal_priority(levelno: int) -> int:
    return next((prio for level, prio in JOURNAL_PRIORITIES if levelno >= level), 7)


def is_journal_stream(stream) -> bool:
    """Geht `stream` direkt ins Journal?

    systemd setzt JOURNAL_STREAM=<Gerät>:<Inode> des Streams, mit dem es stdout/stderr eines
    Dienstes ans Journal anschließt (systemd.exec(5)). Die Variable allein reicht nicht: Kind-
    prozesse erben sie auch, wenn ihre Ausgabe in eine Pipe, eine Datei oder ein Terminal geht
    (z. B. eine Shell in einem Terminal, das selbst als systemd-Dienst läuft). Deshalb zählt nur
    ein Stream mit genau diesem Gerät und Inode.
    """
    try:
        dev, ino = (int(part) for part in os.environ["JOURNAL_STREAM"].split(":"))
        st = os.fstat(stream.fileno())
    except (KeyError, ValueError, AttributeError, OSError):  # auch io.UnsupportedOperation
        return False
    return (st.st_dev, st.st_ino) == (dev, ino)


class JournalFormatter(logging.Formatter):
    """Text wie der umhüllte Formatter, jede Zeile mit dem Präfix <N> ihrer Stufe.

    journald liest das Präfix am Zeilenanfang als Priorität und entfernt es (SyslogLevelPrefix=,
    ab Werk an). Ohne Präfix ist jede Zeile auf stdout/stderr info, `journalctl -p warning` fände
    nichts. Jede Zeile, nicht nur die erste: journald liest zeilenweise, der Traceback einer
    Exception stünde sonst als info im Journal und fehlte bei `-p err`. Und keine Folgezeile (etwa
    aus einer Fehlermeldung) kann mit eigenem <N> eine Priorität vortäuschen, journald wertet je
    Zeile nur das erste Präfix aus.
    """

    def __init__(self, inner: logging.Formatter | None = None) -> None:
        super().__init__()
        self.inner = inner or logging.Formatter()

    def format(self, record: logging.LogRecord) -> str:
        prefix = f"<{journal_priority(record.levelno)}>"
        return "\n".join(prefix + line for line in self.inner.format(record).split("\n"))


def _journal_logging(app: Flask) -> None:
    """Unter systemd (stderr ist das Journal): App-Logs mit Journal-Priorität, sonst unverändert.

    Gilt für den App-Logger 'tatilvakti' und alles darunter (tatilvakti.borders, tatilvakti.db),
    in gunicorn wie im Flask-CLI der Wartung. Unbehandelte Exceptions laufen ebenfalls hierüber:
    Flask loggt sie als ERROR mit Traceback („Exception on <Pfad> [<Methode>]“, ohne IP und ohne
    Query), bevor der 500-Handler die Fehlerseite liefert. Text wie Flasks Standard, nur mit
    Präfix; Flasks eigener Handler fällt weg, sonst stünde jede Zeile doppelt im Journal. gunicorns
    eigene Logs: gunicorn.conf.py.
    """
    if not is_journal_stream(sys.stderr):
        return
    logger = logging.getLogger(app.name)  # derselbe Logger wie app.logger
    if any(isinstance(h.formatter, JournalFormatter) for h in logger.handlers):
        return  # schon eingerichtet (create_app mehrmals im selben Prozess)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JournalFormatter(default_handler.formatter))
    logger.removeHandler(default_handler)
    logger.addHandler(handler)


def utcnow(app: Flask) -> datetime:
    clock = app.config.get("TV_CLOCK")
    return clock() if clock else datetime.now(timezone.utc)


def today_berlin(app: Flask) -> date:
    return utcnow(app).astimezone(BERLIN).date()
