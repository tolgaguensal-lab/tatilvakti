"""Skript scripts/check_sources.py: URLs sammeln (echte Daten) und prüfen (lokaler HTTP-Server).

Kein externer Netzverkehr: Die Prüflogik läuft gegen einen http.server auf 127.0.0.1 in einem
Thread, „anderer Host“ ist dort localhost, der Proxy-Fall ein lokaler Proxy, der CONNECT mit 403
ablehnt. Proxys aus der Umgebung sind abgeschaltet (proxies={} bzw. leere Umgebungsvariablen).
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import socket
import ssl
import subprocess
import sys
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_sources.py"
PROXY_VARS = ("http_proxy", "https_proxy", "all_proxy", "no_proxy")


def load_module():
    spec = importlib.util.spec_from_file_location("tv_check_sources", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses sucht das Modul der Klasse dort
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cs():
    return load_module()


# ---------------------------------------------------------------- lokaler Server

class Handler(BaseHTTPRequestHandler):
    # Pfad → (Status, Location); Sonderfälle in serve()
    ROUTES = {
        "/ok": (200, ""),
        "/moved": (301, "/ok"),
        "/dir": (301, "/dir/"),
        "/dir/": (200, ""),
        "/away": (301, "http://localhost:{port}/ok"),
        "/gone": (404, ""),
        "/moved-gone": (302, "/gone"),
        "/loop": (302, "/loop"),
        "/down": (503, ""),
    }

    def log_message(self, format, *args):
        pass

    def do_HEAD(self):
        self.serve()

    def do_GET(self):
        self.serve()

    def do_CONNECT(self):  # als Proxy: lehnt jeden Tunnel ab wie die Netzwerk-Policy der Entwicklungsumgebung
        self.server.record(self)
        self.send_response(403, "Forbidden")
        self.end_headers()

    def serve(self):
        site = self.server
        count = site.record(self)
        if self.path == "/no-head":  # HEAD unbekannt, GET geht
            code, location = (405, "") if self.command == "HEAD" else (200, "")
        elif self.path == "/slow":
            site.release.wait(10)
            code, location = 200, ""
        elif self.path == "/flaky":  # erst zwei Fehler, dann geht es
            code, location = (503, "") if count <= 2 else (200, "")
        else:
            code, location = self.ROUTES.get(self.path, (404, ""))
        self.send_response(code)
        if location:
            self.send_header("Location", location.format(port=site.server_address[1]))
        self.send_header("Content-Length", "0")
        self.end_headers()


class Site(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests: list[tuple[str, str]] = []
        self.user_agents: set[str] = set()
        self.release = threading.Event()
        self._lock = threading.Lock()

    def record(self, handler: Handler) -> int:
        """Abruf merken; Rückgabe: wievielter Abruf dieses Pfads."""
        with self._lock:
            self.requests.append((handler.command, handler.path))
            self.user_agents.add(handler.headers.get("User-Agent", ""))
            return sum(path == handler.path for _, path in self.requests)

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}{path}"

    def handle_error(self, request, client_address):
        pass  # abgebrochene Verbindungen (Timeout-Test) sind gewollt


@pytest.fixture()
def site():
    server = Site()
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield server
    server.release.set()
    server.shutdown()
    server.server_close()


@pytest.fixture()
def no_env_proxy(monkeypatch):
    for name in PROXY_VARS:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)


def check(cs, site, path, timeout=2.0):
    return cs.check_url(site.url(path), timeout=timeout, retry_pause=0, proxies={})


# ---------------------------------------------------------------- URLs sammeln

def test_collects_all_source_urls_from_real_data(cs):
    files = cs.default_files()
    names = {f.name for f in files}
    assert {"customs.json", "transit.json", "holidays.json", "crossings.json", "de.json", "tr.json"} <= names
    urls = cs.collect_urls(files)
    assert len(urls) > 0

    # jede url-Angabe der Datensätze ist dabei, mit Fundstelle
    def url_fields(node, out):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "url" and isinstance(value, str):
                    out.append(value)
                url_fields(value, out)
        elif isinstance(node, list):
            for value in node:
                url_fields(value, out)

    for file in sorted(cs.DATA_DIR.glob("*.json")):
        found: list[str] = []
        url_fields(json.loads(file.read_text(encoding="utf-8")), found)
        for url in found:
            assert url in urls, (file.name, url)
            assert any(o.startswith(f"{file.name}:") and o.endswith(".url") for o in urls[url]), url

    for url, origins in urls.items():
        assert url.startswith(("http://", "https://")), url
        assert "www.w3.org/" not in url, url  # XML-Namespaces
        assert urlsplit(url).hostname not in ("example.com", "example.net", "example.org"), url
        assert origins and all(re.match(r"^[a-z0-9_]+\.json:\S+$", o) for o in origins), origins
        assert len(origins) == len(set(origins)), url


def test_collect_paths_namespaces_examples_duplicates(cs, tmp_path):
    (tmp_path / "a.json").write_text(json.dumps({
        "countries": {"AT": {"shop": {"url": "https://shop.example.at"}},
                      "BG": {"sources": [{"url": "https://quelle.bg/x"}, {"url": "https://quelle.bg/x"}]}},
        "svg": '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"></svg>',
        "text": "Siehe https://quelle.bg/x. Oder (https://amt.de/seite) und https://amt.de/a_(b), danach",
        "beispiel": "https://example.com/pfad https://www.example.org https://foo.invalid/ https://bar.test",
        "vorlage": "https://amt.de/{slug}",
        "zwei.teile": "https://amt.de/punkt",
    }), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps([{"link": "https://quelle.bg/x"}]), encoding="utf-8")

    urls = cs.collect_urls([tmp_path / "a.json", tmp_path / "b.json"])
    assert urls == {
        "https://shop.example.at": ["a.json:countries.AT.shop.url"],
        "https://quelle.bg/x": ["a.json:countries.BG.sources[0].url", "a.json:countries.BG.sources[1].url",
                                "a.json:text", "b.json:[0].link"],
        "https://amt.de/seite": ["a.json:text"],
        "https://amt.de/a_(b)": ["a.json:text"],
        "https://amt.de/punkt": ['a.json:["zwei.teile"]'],
    }


# ---------------------------------------------------------------- prüfen (lokaler Server)

def test_ok_with_head_only_and_own_user_agent(cs, site):
    result = check(cs, site, "/ok")
    assert (result.status, result.http_status, result.redirects) == (cs.OK, 200, [])
    assert site.requests == [("HEAD", "/ok")]
    assert site.user_agents == {cs.USER_AGENT} and "tatilvakti" in cs.USER_AGENT


def test_redirect_to_other_path_names_target(cs, site):
    result = check(cs, site, "/moved")
    assert (result.status, result.other_host) == (cs.REDIRECT, False)
    assert result.final_url == site.url("/ok") and result.redirects == [(301, site.url("/ok"))]
    assert not cs.is_failure(result, strict=True)
    assert site.requests == [("HEAD", "/moved"), ("HEAD", "/ok")]


def test_redirect_with_trailing_slash_is_ok(cs, site):
    result = check(cs, site, "/dir")
    assert (result.status, result.final_url) == (cs.OK, site.url("/dir/"))


def test_redirect_to_other_host_fails_only_with_strict(cs, site):
    result = check(cs, site, "/away")
    port = site.server_address[1]
    assert (result.status, result.other_host, result.http_status) == (cs.REDIRECT, True, 200)
    assert result.final_url == f"http://localhost:{port}/ok"
    assert not cs.is_failure(result, strict=False)
    assert cs.is_failure(result, strict=True)
    text = cs.format_text([result], strict=True)
    assert f"→ http://localhost:{port}/ok (301, anderer Host, mit --strict ein Fehler)" in text


def test_404_is_broken_after_one_get(cs, site):
    result = check(cs, site, "/gone")
    assert (result.status, result.detail, result.http_status) == (cs.BROKEN, "HTTP 404", 404)
    assert site.requests == [("HEAD", "/gone"), ("GET", "/gone")]  # kein Wiederholen bei 4xx


def test_404_after_redirect_names_target(cs, site):
    result = check(cs, site, "/moved-gone")
    assert result.status == cs.BROKEN
    assert result.detail == f"HTTP 404 (nach Weiterleitung auf {site.url('/gone')})"


def test_head_405_falls_back_to_get(cs, site):
    result = check(cs, site, "/no-head")
    assert (result.status, result.http_status) == (cs.OK, 200)
    assert site.requests == [("HEAD", "/no-head"), ("GET", "/no-head")]


def test_timeout_is_broken_after_exactly_one_retry(cs, site):
    result = check(cs, site, "/slow", timeout=0.3)
    assert (result.status, result.detail) == (cs.BROKEN, "Timeout nach 0.3 s")
    # kein GET-Fallback nach dem Timeout, dann genau eine Wiederholung (gleich mit GET)
    assert site.requests == [("HEAD", "/slow"), ("GET", "/slow")]


def test_5xx_retried_once(cs, site):
    result = check(cs, site, "/flaky")
    assert (result.status, result.http_status) == (cs.OK, 200)
    assert site.requests == [("HEAD", "/flaky"), ("GET", "/flaky"), ("GET", "/flaky")]

    site.requests.clear()
    result = check(cs, site, "/down")
    assert (result.status, result.detail) == (cs.BROKEN, "HTTP 503")
    assert site.requests == [("HEAD", "/down"), ("GET", "/down"), ("GET", "/down")]


def test_redirect_loop_stops_after_max(cs, site):
    result = check(cs, site, "/loop")
    assert (result.status, result.detail) == (cs.BROKEN, f"mehr als {cs.MAX_REDIRECTS} Weiterleitungen")
    assert len(site.requests) == cs.MAX_REDIRECTS + 1


def test_connection_refused(cs):
    with socket.socket() as sock:  # freien Port finden, danach lauscht dort niemand
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    result = cs.check_url(f"http://127.0.0.1:{port}/", timeout=2, retry_pause=0, proxies={})
    assert (result.status, result.detail) == (cs.BROKEN, "Verbindung abgelehnt")


def test_proxy_403_is_reported_not_raised(cs, site, no_env_proxy):
    """Wie in der Entwicklungsumgebung: Der Proxy lehnt CONNECT ab, das Skript meldet KAPUTT (Proxy 403 …)."""
    result = cs.check_url("https://quelle.invalid/seite", timeout=2, retry_pause=0,
                          proxies={"https": site.url("")})
    assert result.status == cs.BROKEN
    assert result.detail.startswith("Proxy 403 Forbidden")
    assert site.requests == [("CONNECT", "quelle.invalid:443")]  # kein GET, keine Wiederholung
    text = cs.format_text([result, result], strict=False)
    assert "KAPUTT" in text and "Kein Server hat geantwortet" in text


def test_describe_error_without_network(cs):
    def reason(exc):
        return cs.describe_error(exc, 15)

    assert reason(urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))) == (
        "DNS: Name nicht auflösbar (Name or service not known)", False)
    assert reason(urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed")))[0].startswith("TLS: ")
    assert reason(urllib.error.URLError(TimeoutError("timed out"))) == ("Timeout nach 15 s", True)
    assert reason(TimeoutError("timed out")) == ("Timeout nach 15 s", True)  # Timeout beim Lesen der Antwort
    assert reason(urllib.error.URLError(OSError("Tunnel connection failed: 403 Forbidden")))[0].startswith(
        "Proxy 403 Forbidden")
    assert reason(urllib.error.URLError("unknown url type: ftp")) == (
        "Verbindungsfehler: unknown url type: ftp", False)


# ---------------------------------------------------------------- Aufruf

@pytest.fixture()
def local_data(cs, site, tmp_path, monkeypatch, no_env_proxy):
    def write(paths: dict[str, str]) -> None:
        data = {"sources": {key: {"url": site.url(path) if path.startswith("/") else path}
                            for key, path in paths.items()}}
        (tmp_path / "local.json").write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr(cs, "default_files", lambda: [tmp_path / "local.json"])
    monkeypatch.setattr(cs, "RETRY_PAUSE_S", 0)
    return write


def test_main_json_and_exit_code(cs, site, local_data, capsys):
    local_data({"a": "/ok", "b": "/moved", "c": "/away", "d": "/gone"})
    assert cs.main(["--json", "--timeout", "2"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert (report["checked"], report["ok"], report["redirect"], report["redirect_other_host"],
            report["broken"], report["exit_code"]) == (4, 1, 2, 1, 1, 1)
    first = report["results"][0]  # KAPUTT zuerst
    assert (first["url"], first["status"], first["http_status"]) == (site.url("/gone"), "broken", 404)
    assert first["origins"] == ["local.json:sources.d.url"]


def test_main_text_redirects_exit_0_unless_strict(cs, site, local_data, capsys):
    local_data({"a": "/ok", "c": "/away"})
    assert cs.main([]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == f"WEITERLEITUNG  {site.url('/away')}"
    assert "aus local.json:sources.c.url" in out
    assert out.rstrip().endswith("2 URLs geprüft: 1 OK, 1 WEITERLEITUNG (davon 1 auf andere Hosts), 0 KAPUTT")
    assert cs.main(["--strict"]) == 1


def test_main_only_and_list_without_network(cs, site, local_data, capsys):
    local_data({"a": "/ok", "fremd": "https://www.zoll.de/x"})
    assert cs.main(["--list", "--json", "--only", "127.0.0.1"]) == 0
    assert json.loads(capsys.readouterr().out) == [{"url": site.url("/ok"), "origins": ["local.json:sources.a.url"]}]
    assert cs.main(["--only", "nirgends.de"]) == 2
    assert site.requests == []


@pytest.mark.parametrize("argv", [["--workers", "0"], ["--workers", "17"], ["--timeout", "0"]])
def test_main_rejects_bad_options(cs, argv):
    with pytest.raises(SystemExit) as exc:
        cs.main(argv)
    assert exc.value.code == 2


def test_script_uses_only_stdlib_and_runs_standalone():
    """Läuft auf Hermes mit dem System-Python ohne venv."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    modules = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
               for alias in node.names}
    modules |= {node.module.split(".")[0] for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module and node.level == 0}
    assert modules - {"__future__"} <= set(sys.stdlib_module_names), modules

    res = subprocess.run([sys.executable, "-I", str(SCRIPT), "--list"], capture_output=True, text=True,
                         timeout=60, check=False)
    assert res.returncode == 0, res.stderr
    assert re.search(r"^\d+ URLs$", res.stdout, re.M)
