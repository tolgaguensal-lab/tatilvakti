"""Logs im Journal (Audit d5-logging-unstructured): Unter systemd trägt jede Zeile das Präfix <N>
ihrer Stufe, damit `journalctl -p warning` bzw. `-p err` funktioniert; außerhalb bleibt alles,
wie es war. Dazu: keine IP-Adressen, keine Prüfwerte und keine Query in den Logs."""
from __future__ import annotations

import importlib.util
import io
import logging
import os
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep

import pytest
from flask.logging import default_handler

from tatilvakti import JournalFormatter, create_app, is_journal_stream, journal_priority

ROOT = Path(__file__).resolve().parent.parent


def journal_env(monkeypatch, path: Path) -> None:
    """JOURNAL_STREAM wie von systemd gesetzt: Gerät und Inode der Datei `path`."""
    st = os.stat(path)
    monkeypatch.setenv("JOURNAL_STREAM", f"{st.st_dev}:{st.st_ino}")


class Journal:
    """stderr als „Journal“: eine Datei, auf die JOURNAL_STREAM zeigt."""

    def __init__(self, path: Path, monkeypatch) -> None:
        self.path, self.monkeypatch = path, monkeypatch
        self.stream = open(path, "w", encoding="utf-8")  # schließt das Fixture
        journal_env(monkeypatch, path)

    def start(self) -> None:
        # Erst im Test selbst: pytest setzt sys.stderr zwischen Fixture und Test auf seine Capture zurück
        self.monkeypatch.setattr(sys, "stderr", self.stream)

    def read(self) -> str:
        self.stream.flush()
        return self.path.read_text(encoding="utf-8")


@pytest.fixture
def journal(tmp_path, monkeypatch):
    """Räumt danach die Handler am App-Logger auf: Der Logger ist global."""
    logger = logging.getLogger("tatilvakti")
    before = list(logger.handlers)
    sink = Journal(tmp_path / "journal.log", monkeypatch)
    yield sink
    logger.handlers[:] = before
    sink.stream.close()


def journal_app(tmp_path, clock, **config):
    return create_app({"TESTING": True, "TV_DB_PATH": str(tmp_path / "log.db"), "TV_CLOCK": clock,
                       "PROPAGATE_EXCEPTIONS": False, **config})


@pytest.mark.parametrize("level, prio", [
    (logging.CRITICAL, 2), (logging.ERROR, 3), (logging.WARNING, 4), (logging.INFO, 6), (logging.DEBUG, 7),
    (45, 3), (25, 6), (5, 7),  # Zwischenstufen: die nächst niedrigere bekannte
])
def test_journal_priority_follows_syslog_levels(level, prio):
    assert journal_priority(level) == prio


def test_journal_formatter_prefixes_every_line_and_keeps_the_text():
    inner = logging.Formatter("%(levelname)s in %(module)s: %(message)s")
    formatter = JournalFormatter(inner)
    try:
        raise RuntimeError("kaputt\n<0>gefälscht")
    except RuntimeError:
        record = logging.LogRecord("tatilvakti", logging.ERROR, __file__, 1, "Exception on %s", ("/x",),
                                   sys.exc_info())
    lines = formatter.format(record).split("\n")
    assert lines[0] == "<3>ERROR in test_logging: Exception on /x"
    assert lines[1] == "<3>Traceback (most recent call last):"
    assert all(line.startswith("<3>") for line in lines)  # der ganze Traceback unter -p err
    # Eine Zeile aus der Fehlermeldung kann keine eigene Priorität setzen: journald liest nur das erste Präfix
    assert "<3><0>gefälscht" in lines
    plain = logging.LogRecord("tatilvakti", logging.INFO, __file__, 1, "report crossing=%s", ("kapikule",), None)
    assert JournalFormatter().format(plain) == "<6>report crossing=kapikule"


def test_journal_stream_needs_the_exact_device_and_inode(tmp_path, monkeypatch):
    path, other = tmp_path / "journal", tmp_path / "anders"
    path.write_text("")
    other.write_text("")
    with open(path, "w") as stream, open(other, "w") as elsewhere:
        monkeypatch.delenv("JOURNAL_STREAM", raising=False)
        assert is_journal_stream(stream) is False  # kein systemd
        journal_env(monkeypatch, path)
        assert is_journal_stream(stream) is True
        # Geerbte Variable, Ausgabe aber woanders hin (Pipe, Terminal, Datei): nicht das Journal
        assert is_journal_stream(elsewhere) is False
        assert is_journal_stream(io.StringIO()) is False  # kein Dateideskriptor (z. B. pytest-Capture)
        assert is_journal_stream(None) is False
        for broken in ("", "x", "1:2:3", "1:x"):
            monkeypatch.setenv("JOURNAL_STREAM", broken)
            assert is_journal_stream(stream) is False


def test_app_logs_carry_the_journal_priority(tmp_path, clock, journal):
    journal.start()
    journal_app(tmp_path, clock, TV_BASE_URL="https://tatilvakti.example")
    lines = journal.read().splitlines()
    warnings = [line for line in lines if "unvollständig" in line]
    assert len(warnings) == 2  # Impressum und Datenschutztext, je einmal: Flasks Handler ist weg
    assert all(line.startswith("<4>") and "WARNING" in line for line in warnings)
    assert default_handler not in logging.getLogger("tatilvakti").handlers


def test_unhandled_exception_is_logged_as_error_with_traceback_without_ip(tmp_path, clock, journal):
    """500-Handler: Flask loggt die Exception vorher als ERROR samt Traceback, mit Pfad und Methode,
    aber ohne IP, Query und Prüfwerte. Unter systemd steht jede Zeile davon unter `-p err`."""
    journal.start()
    app = journal_app(tmp_path, clock, TV_TRUST_PROXY=1)

    def boom():
        raise RuntimeError("kaputt")

    app.view_functions["robots"] = boom
    resp = app.test_client().get("/robots.txt?land=NW", headers={"X-Forwarded-For": "203.0.113.77"},
                                 environ_base={"REMOTE_ADDR": "198.51.100.5"})
    assert resp.status_code == 500 and "text/html" in resp.content_type  # eigene Fehlerseite
    text = journal.read()
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if "Exception on /robots.txt [GET]" in line)
    block = lines[start:lines.index("<3>RuntimeError: kaputt") + 1]
    assert block[0].startswith("<3>[") and block[0].endswith("ERROR in app: Exception on /robots.txt [GET]")
    assert block[1] == "<3>Traceback (most recent call last):"
    assert all(line.startswith("<3>") for line in block)
    for secret in ("203.0.113.77", "198.51.100.5", "127.0.0.1", "land="):
        assert secret not in text


def test_api_error_is_logged_too(tmp_path, clock, journal):
    journal.start()
    app = journal_app(tmp_path, clock)

    def boom(*_args, **_kwargs):
        raise RuntimeError("kaputt")

    endpoint = next(r.endpoint for r in app.url_map.iter_rules() if r.rule == "/api/v1/borders")
    app.view_functions[endpoint] = boom
    resp = app.test_client().get("/api/v1/borders")
    assert resp.status_code == 500 and resp.get_json() == {"error": "server_error", "degraded": True}
    assert "<3>Traceback (most recent call last):" in journal.read()


def test_reports_are_logged_without_ip_and_without_check_values(tmp_path, clock, journal, caplog):
    """Eingegangene Meldung als info (wie in wsgi.py ab INFO): Übergang und Richtung, sonst nichts."""
    caplog.set_level(logging.INFO, logger="tatilvakti")  # setzt den Level danach zurück
    journal.start()
    app = journal_app(tmp_path, clock)
    client = app.test_client()
    resp = client.post("/api/v1/borders/kapikule/reports", json={"direction": "to_tr", "bucket": 2},
                       environ_base={"REMOTE_ADDR": "198.51.100.23"})
    assert resp.status_code == 201
    text = journal.read()
    assert "<6>" in text and "report crossing=kapikule direction=to_tr" in text
    conn = sqlite3.connect(app.config["TV_DB_PATH"])
    try:
        values = [v for row in conn.execute("SELECT client, net, block FROM reports") for v in row if v]
    finally:
        conn.close()
    assert values  # die Prüfwerte gibt es, sie stehen nur nicht im Log
    for secret in ("198.51.100.23", *values):
        assert secret not in text


def test_journal_handler_is_installed_once(tmp_path, clock, journal):
    journal.start()
    journal_app(tmp_path, clock)
    journal_app(tmp_path, clock)
    handlers = [h for h in logging.getLogger("tatilvakti").handlers if isinstance(h.formatter, JournalFormatter)]
    assert len(handlers) == 1


def test_outside_systemd_flask_logging_is_unchanged(tmp_path, clock, monkeypatch, caplog):
    monkeypatch.delenv("JOURNAL_STREAM", raising=False)
    with caplog.at_level("WARNING"):
        journal_app(tmp_path, clock, TV_BASE_URL="https://tatilvakti.example")
    handlers = logging.getLogger("tatilvakti").handlers
    assert not any(isinstance(h.formatter, JournalFormatter) for h in handlers)
    assert any("Impressum unvollständig" in r.getMessage() for r in caplog.records)


# ------------------------------------------------------------------ gunicorn

def load_gunicorn_conf():
    pytest.importorskip("gunicorn")
    spec = importlib.util.spec_from_file_location("tv_gunicorn_conf", ROOT / "gunicorn.conf.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gunicorn_loggers():
    """gunicorns Logger sind global: nach dem Test wieder ohne die hier angelegten Handler."""
    loggers = [logging.getLogger("gunicorn.error"), logging.getLogger("gunicorn.access")]
    before = {log.name: list(log.handlers) for log in loggers}
    yield
    for log in loggers:
        log.handlers[:] = before[log.name]


def gunicorn_logger(conf, **settings):
    from gunicorn.config import Config
    cfg = Config()
    cfg.set("logger_class", conf.logger_class)
    for key, value in settings.items():
        cfg.set(key, value)
    return cfg.logger_class(cfg)


def test_gunicorn_conf_gives_its_error_log_journal_priorities(tmp_path, monkeypatch, gunicorn_loggers):
    conf = load_gunicorn_conf()
    path = tmp_path / "journal.log"
    with open(path, "w", encoding="utf-8") as stream:
        journal_env(monkeypatch, path)
        monkeypatch.setattr(sys, "stderr", stream)
        monkeypatch.setattr(sys, "stdout", stream)
        log = gunicorn_logger(conf, errorlog="-", accesslog="-")
        log.critical("WORKER TIMEOUT (pid:%s)", 4711)
        log.error("Worker (pid:%s) was sent SIGKILL! Perhaps out of memory?", 4712)
        log.info("Booting worker with pid: %s", 4713)
        log.access_log.info("GET /de/ 200 0.001s")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("<2>[") and lines[0].endswith("[CRITICAL] WORKER TIMEOUT (pid:4711)")
    assert lines[1].startswith("<3>[") and "[ERROR] Worker (pid:4712) was sent SIGKILL!" in lines[1]
    assert lines[2].startswith("<6>[") and lines[2].endswith("[INFO] Booting worker with pid: 4713")
    assert lines[3] == "<6>GET /de/ 200 0.001s"  # Access-Log: Text unverändert, journald entfernt <6>


def test_gunicorn_conf_changes_nothing_outside_systemd(tmp_path, monkeypatch, gunicorn_loggers):
    conf = load_gunicorn_conf()
    monkeypatch.delenv("JOURNAL_STREAM", raising=False)
    log = gunicorn_logger(conf, errorlog="-")
    handler = next(h for h in log.error_log.handlers if getattr(h, "_gunicorn", False))
    assert not isinstance(handler.formatter, JournalFormatter)
    # Log-Datei statt stderr: auch unter systemd nie mit Präfix
    path = tmp_path / "journal.log"
    path.write_text("")
    journal_env(monkeypatch, path)
    log = gunicorn_logger(conf, errorlog=str(tmp_path / "gunicorn.log"))
    handler = next(h for h in log.error_log.handlers if getattr(h, "_gunicorn", False))
    assert not isinstance(handler.formatter, JournalFormatter)


def test_gunicorn_reads_the_conf_from_its_working_directory(tmp_path):
    """Wie der Dienst: gunicorn ohne --config im Release-Verzeichnis gestartet. Master und Worker
    schreiben mit Journal-Priorität, die App-Warnung beim Start eingeschlossen."""
    pytest.importorskip("gunicorn")
    out = tmp_path / "journal.log"
    out.write_text("")
    st = os.stat(out)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TV_", "GUNICORN_"))}
    env.update(JOURNAL_STREAM=f"{st.st_dev}:{st.st_ino}", PYTHONDONTWRITEBYTECODE="1",
               TV_DB_PATH=str(tmp_path / "g.db"), TV_SALT_DB_PATH=str(tmp_path / "g-salts.db"),
               TV_BASE_URL="https://tatilvakti.example")
    cmd = [sys.executable, "-m", "gunicorn", "--workers", "1", "--bind", f"unix:{tmp_path / 'g.sock'}",
           "--no-control-socket", "--error-logfile", "-", "--log-level", "info", "wsgi:app"]
    with open(out, "a", encoding="utf-8") as sink:
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=sink, stderr=sink)
    try:
        deadline = monotonic() + 30
        while "Datenschutztext unvollständig" not in out.read_text(encoding="utf-8"):
            assert proc.poll() is None and monotonic() < deadline, out.read_text(encoding="utf-8")
            sleep(0.1)
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    lines = out.read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("<6>[") and "[INFO] Starting gunicorn" in line for line in lines), lines
    assert any(line.startswith("<6>[") and "[INFO] Booting worker" in line for line in lines), lines
    assert any(line.startswith("<4>[") and "Datenschutztext unvollständig" in line for line in lines), lines
    assert not any(line.startswith("[") for line in lines), lines  # keine Log-Zeile ohne Priorität
