#!/usr/bin/env python3
"""Quellen-Links der kuratierten Daten prüfen: erreichbar, weitergeleitet oder kaputt.

    python3 scripts/check_sources.py [--only <host-teil>] [--strict] [--json] [--list]
                                     [--timeout <s>] [--workers <n>]

Auf Hermes: python3 /opt/tatilvakti-v2/current/scripts/check_sources.py
Nur Standardbibliothek: läuft mit dem System-Python (ab 3.10), ohne venv.

Sammelt alle http(s)-URLs aus tatilvakti/data/*.json und tatilvakti/i18n/*.json, mit
Fundstelle als JSON-Pfad (z. B. transit.json:countries.AT.shop.url), und aus den Templates
tatilvakti/templates/*.html, mit Zeile (z. B. info.html:41). Jede URL wird einmal abgerufen,
auch wenn sie an mehreren Stellen steht; die Ausgabe nennt alle Fundstellen.
XML-Namespaces (www.w3.org/2000/svg …), Beispiel-Domains (example.com, *.invalid …) und
URLs mit Platzhaltern ({slug}) zählen nicht.

Abruf je URL:
- Erst HEAD. Antwortet der Server darauf mit 4xx/5xx (manche kennen HEAD nicht: 405, 501,
  oder sperren es: 403) oder bricht die Verbindung ab, folgt ein GET. Vom GET wird nur
  der Kopf gelesen, der Body verworfen.
- Weiterleitungen folgt das Skript selbst, höchstens MAX_REDIRECTS, und nennt das Ziel.
  Cookies gelten wie im Browser, aber nur im Speicher und nur für diese eine URL (manche
  Seiten leiten ohne Cookie auf sich selbst weiter).
- Timeout und 5xx: eine Wiederholung nach RETRY_PAUSE_S, gleich mit GET. Mehr nicht.
- Höflich: eigener User-Agent, wenige Abrufe gleichzeitig (--workers) und je Host nur einer.
- Proxy-Variablen (HTTPS_PROXY, NO_PROXY) gelten wie bei urllib üblich.

Bewertung:
- OK: am Ende 2xx/3xx, ohne Weiterleitung oder nur innerhalb derselben Adresse
  (http → https, mit oder ohne „www.“, Schrägstrich am Ende, Prozentkodierung; Pfad und
  Query bleiben gleich).
- WEITERLEITUNG: Ziel mit anderem Pfad, anderer Query, auf einem anderen Host oder von https
  auf http. Ein Hinweis, kein Fehler; mit --strict sind Weiterleitungen auf andere Hosts ein
  Fehler.
- KAPUTT: HTTP 4xx/5xx, DNS, TLS, Timeout, Proxy, zu viele Weiterleitungen, Weiterleitung
  ohne Ziel.

Exit-Code 0 = nichts KAPUTT (mit --strict auch keine Weiterleitung auf einen anderen Host),
1 = sonst, 2 = Aufruf- oder Datenfehler, 130 = Strg+C (beendet sofort, laufende Abrufe werden
nicht abgewartet). Geprüft wird nur die Erreichbarkeit, nicht, ob die Seite noch belegt, was
die App sagt. Ablauf und Bewertung: docs/RUNBOOK.md, Abschnitt 5.
"""
from __future__ import annotations

import argparse
import http.client
import http.cookiejar
import json
import os
import re
import socket
import ssl
import string
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "tatilvakti" / "data"
I18N_DIR = ROOT / "tatilvakti" / "i18n"
TEMPLATES_DIR = ROOT / "tatilvakti" / "templates"

DEFAULT_TIMEOUT_S = 15
DEFAULT_WORKERS = 4
MAX_WORKERS = 16  # mehr wäre nicht mehr höflich
MAX_REDIRECTS = 5
RETRY_PAUSE_S = 2
USER_AGENT = "tatilvakti-quellencheck/1.0 (+https://tatilvakti.guenlab.de)"  # nur ASCII (HTTP-Header)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
    "Accept-Language": "de,tr;q=0.9,en;q=0.5",
}

OK, REDIRECT, BROKEN = "ok", "redirect", "broken"
LABEL = {OK: "OK", REDIRECT: "WEITERLEITUNG", BROKEN: "KAPUTT"}
REDIRECT_CODES = {301, 302, 303, 307, 308}

URL_RE = re.compile(r"https?://[^\s\"'<>\\^`|]+")
TRAILING = ".,;:!?…»«“”‘’"  # Satzzeichen nach einer URL im Fließtext; „)“ eigens, siehe find_urls()
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
NAMESPACE_PATHS = ("/2000/svg", "/1999/xlink", "/1999/xhtml", "/XML/1998/namespace")
EXAMPLE_HOSTS = ("example.com", "example.net", "example.org")  # RFC 2606
EXAMPLE_TLDS = (".example", ".invalid", ".test")
TUNNEL_RE = re.compile(r"Tunnel connection failed: (\d{3})\s*(.*)")


# ---------------------------------------------------------------- URLs sammeln

def default_files() -> list[Path]:
    return sorted(DATA_DIR.glob("*.json")) + sorted(I18N_DIR.glob("*.json")) + sorted(TEMPLATES_DIR.glob("*.html"))


def is_ignored(url: str) -> bool:
    """XML-Namespaces, Beispiel-Domains und Platzhalter-URLs sind keine Quellen."""
    if "{" in url or "}" in url:
        return True
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:  # z. B. kaputte IPv6-Adresse; dann prüft sie der Abruf
        return False
    if not host:
        return True
    if host == "www.w3.org" and parts.path.startswith(NAMESPACE_PATHS):
        return True
    return (host in EXAMPLE_HOSTS or host.endswith(tuple("." + h for h in EXAMPLE_HOSTS))
            or host.endswith(EXAMPLE_TLDS))


def find_urls(text: str) -> list[str]:
    """http(s)-URLs in einem Text; Satzzeichen und eine schließende Klammer dahinter gehören nicht dazu."""
    found = []
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(TRAILING)
        while url.endswith(")") and url.count(")") > url.count("("):
            url = url[:-1].rstrip(TRAILING)
        if not is_ignored(url):
            found.append(url)
    return found


def _child(path: str, key: str) -> str:
    if KEY_RE.match(key):
        return f"{path}.{key}" if path else key
    return f"{path}[{json.dumps(key, ensure_ascii=False)}]"


def _walk(node, path: str, found: list[tuple[str, str]]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            _walk(value, _child(path, str(key)), found)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _walk(value, f"{path}[{index}]", found)
    elif isinstance(node, str):
        found.extend((url, path) for url in find_urls(node))


def collect_urls(files: list[Path]) -> dict[str, list[str]]:
    """URL → Fundstellen („datei.json:pfad“, bei Templates „datei.html:zeile“), in der Reihenfolge
    des ersten Vorkommens."""
    urls: dict[str, list[str]] = {}
    for file in files:
        found: list[tuple[str, str]] = []
        text = file.read_text(encoding="utf-8")
        if file.suffix == ".json":
            _walk(json.loads(text), "", found)
        else:  # Template: zeilenweise, Jinja-Ausdrücke in URLs ({{ … }}) filtert is_ignored()
            for number, line in enumerate(text.splitlines(), 1):
                found.extend((url, str(number)) for url in find_urls(line))
        for url, path in found:
            origins = urls.setdefault(url, [])
            origin = f"{file.name}:{path}"
            if origin not in origins:
                origins.append(origin)
    return urls


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


# ---------------------------------------------------------------- prüfen

@dataclass
class Result:
    url: str
    status: str
    detail: str = ""  # Grund bei KAPUTT
    http_status: int | None = None
    final_url: str = ""
    redirects: list[tuple[int, str]] = field(default_factory=list)
    other_host: bool = False
    origins: list[str] = field(default_factory=list)
    to_http: bool = False  # Weiterleitung von https auf http


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Weiterleitungen nicht von urllib folgen lassen: _follow() geht sie Schritt für Schritt."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # → HTTPError mit dem 3xx-Status und Location


def make_opener(proxies: dict[str, str] | None = None) -> urllib.request.OpenerDirector:
    """proxies=None: Proxy aus der Umgebung (HTTPS_PROXY, NO_PROXY); {} = ohne Proxy.
    Eigener Cookie-Speicher je Opener, also je URL, nur im Speicher."""
    handlers: list[urllib.request.BaseHandler] = [
        _NoRedirect(), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())]
    if proxies is not None:
        handlers.append(urllib.request.ProxyHandler(proxies))
    return urllib.request.build_opener(*handlers)


def _ascii(url: str) -> str:
    """http.client verlangt ASCII: Umlaute in Pfad und Query prozentkodieren, Umlaut-Domains
    per IDNA (xn--…), wie der Browser."""
    if url.isascii():
        return url
    parts = urlsplit(url)
    userinfo, at, hostport = parts.netloc.rpartition("@")
    host, colon, port = hostport.rpartition(":")
    if not port.isdigit():
        host, colon, port = hostport, "", ""
    if not host.isascii():
        host = host.encode("idna").decode("ascii")
    netloc = quote(userinfo + at, safe=string.punctuation) + host + colon + port
    return urlunsplit((parts.scheme, netloc, *(quote(part, safe=string.punctuation)
                                               for part in (parts.path, parts.query, parts.fragment))))


def _fetch(opener: urllib.request.OpenerDirector, url: str, method: str, timeout: float) -> tuple[int, str]:
    """Ein Abruf ohne Weiterleitung: (HTTP-Status, Location). Vom Body wird nichts gelesen."""
    request = urllib.request.Request(_ascii(url), method=method, headers=HEADERS)
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, (exc.headers.get("Location") or "") if exc.headers else ""
        finally:
            if exc.fp is not None:
                exc.fp.close()
    with response:
        return response.status, response.headers.get("Location") or ""


def _error_kind(exc: BaseException) -> tuple[str, str]:
    """(Art, Text) für einen Abruf ohne HTTP-Antwort. Art: timeout, dns, tls, proxy, refused, other."""
    reason = exc
    if isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError):
        reason = exc.reason if isinstance(exc.reason, BaseException) else exc
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "timeout", ""
    if isinstance(reason, socket.gaierror):
        return "dns", reason.strerror or str(reason)
    if isinstance(reason, ssl.SSLError):  # verify_message bzw. reason setzt nur das ssl-Modul selbst
        return "tls", getattr(reason, "verify_message", "") or getattr(reason, "reason", "") or str(reason)
    tunnel = TUNNEL_RE.search(str(reason))
    if tunnel:
        return "proxy", f"{tunnel.group(1)} {tunnel.group(2)}".strip()
    if isinstance(reason, ConnectionRefusedError):
        return "refused", ""
    if isinstance(exc, urllib.error.URLError) and not isinstance(exc.reason, BaseException):
        return "other", str(exc.reason)  # z. B. „unknown url type“
    return "other", f"{type(reason).__name__}: {reason}".rstrip(": ")


def describe_error(exc: BaseException, timeout: float) -> tuple[str, bool]:
    """(Grund für die Ausgabe, Wiederholung sinnvoll) für einen Abruf ohne HTTP-Antwort."""
    kind, text = _error_kind(exc)
    if kind == "timeout":
        return f"Timeout nach {timeout:g} s", True
    if kind == "dns":
        return f"DNS: Name nicht auflösbar ({text})", False
    if kind == "tls":
        return f"TLS: {text}", False
    if kind == "proxy":
        return f"Proxy {text} (Proxy lässt die Verbindung nicht zu)", False
    if kind == "refused":
        return "Verbindung abgelehnt", False
    return f"Verbindungsfehler: {text}", False


def _probe(opener: urllib.request.OpenerDirector, url: str, timeout: float, head: bool) -> tuple[int, str]:
    """Ein Schritt: HEAD, bei 4xx/5xx oder abgebrochener Verbindung GET. (Status, Location)."""
    if head:
        try:
            status, location = _fetch(opener, url, "HEAD", timeout)
            if status < 400:
                return status, location
        except (OSError, http.client.HTTPException) as exc:
            # Timeout, DNS, TLS, Proxy und abgelehnte Verbindung änderte ein GET nicht
            if _error_kind(exc)[0] != "other":
                raise
    return _fetch(opener, url, "GET", timeout)


def _follow(opener: urllib.request.OpenerDirector, url: str, timeout: float, head: bool,
            stop: threading.Event | None = None) -> tuple[Result, bool]:
    """Weiterleitungen folgen und bewerten: (Ergebnis, Wiederholung sinnvoll)."""
    chain: list[tuple[int, str]] = []
    current = url
    after = ""
    for _ in range(MAX_REDIRECTS + 1):
        if stop is not None and stop.is_set():
            return Result(url, BROKEN, "abgebrochen", None, current, chain), False
        try:
            status, location = _probe(opener, current, timeout, head)
        except Exception as exc:  # nie abstürzen: jeder Fehler ist ein Befund zu dieser URL
            reason, retry = describe_error(exc, timeout)
            return Result(url, BROKEN, reason + after, None, current, chain), retry
        if status in REDIRECT_CODES:
            if not location:
                return Result(url, BROKEN, f"HTTP {status} ohne Ziel (Location fehlt){after}",
                              status, current, chain), False
            # wie urllib: Location ist als ISO-8859-1 gelesen, Nicht-ASCII-Bytes prozentkodieren
            target = urljoin(current, quote(location, encoding="iso-8859-1", safe=string.punctuation))
            chain.append((status, target))
            if urlsplit(target).scheme not in ("http", "https"):
                return Result(url, BROKEN, f"Weiterleitung auf {target}", status, target, chain), False
            current = target
            after = f" (nach Weiterleitung auf {current})"
            continue
        if status >= 400:
            return Result(url, BROKEN, f"HTTP {status}{after}", status, current, chain), status >= 500
        return _judge(url, current, status, chain), False
    return Result(url, BROKEN, f"mehr als {MAX_REDIRECTS} Weiterleitungen", None, current, chain), False


def _host_key(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    port = parts.port
    return host if port in (None, 80, 443) else f"{host}:{port}"


def _path_key(url: str) -> tuple[str, str]:
    """Pfad ohne Schrägstrich am Ende und Query, beide ohne Prozentkodierung (CELEX%3A = CELEX:)."""
    parts = urlsplit(url)
    return unquote(parts.path).rstrip("/"), unquote(parts.query)


def _judge(url: str, final: str, status: int, chain: list[tuple[int, str]]) -> Result:
    if not chain:
        return Result(url, OK, "", status, url)
    other_host = _host_key(url) != _host_key(final)
    to_http = urlsplit(url).scheme == "https" and urlsplit(final).scheme == "http"
    if other_host or to_http or _path_key(url) != _path_key(final):
        return Result(url, REDIRECT, "", status, final, chain, other_host, to_http=to_http)
    return Result(url, OK, "", status, final, chain)


def check_url(url: str, *, timeout: float = DEFAULT_TIMEOUT_S, retry_pause: float | None = None,
              proxies: dict[str, str] | None = None, stop: threading.Event | None = None) -> Result:
    """Eine URL prüfen. Bei Timeout oder 5xx genau eine Wiederholung, gleich mit GET.
    Ist stop gesetzt, folgt kein weiterer Schritt mehr; die Pause vor der Wiederholung endet sofort."""
    opener = make_opener(proxies)
    try:
        result, retry = _follow(opener, url, timeout, head=True, stop=stop)
        if retry:
            pause = RETRY_PAUSE_S if retry_pause is None else retry_pause
            stopped = stop.wait(pause) if stop is not None else time.sleep(pause)
            if not stopped:
                result, _ = _follow(opener, url, timeout, head=False, stop=stop)
    except Exception as exc:  # Fehler beim Auswerten, z. B. eine unlesbare Weiterleitungs-URL
        return Result(url, BROKEN, f"nicht prüfbar: {type(exc).__name__}: {exc}")
    return result


def _interleave(urls: list[str]) -> list[str]:
    """Reihum nach Host, damit die Worker nicht alle auf denselben Host warten."""
    by_host: dict[str, list[str]] = {}
    for url in urls:
        by_host.setdefault(host_of(url), []).append(url)
    queues = list(by_host.values())
    order = []
    while queues:
        order.extend(queue.pop(0) for queue in queues)
        queues = [queue for queue in queues if queue]
    return order


def check_all(urls: dict[str, list[str]], *, workers: int = DEFAULT_WORKERS,
              timeout: float = DEFAULT_TIMEOUT_S, retry_pause: float | None = None,
              proxies: dict[str, str] | None = None, progress=None) -> list[Result]:
    """Alle URLs prüfen, je Host nacheinander. Sortiert: KAPUTT, WEITERLEITUNG, OK, je nach URL.

    Bei Strg+C (oder einem Fehler in progress) kehrt es sofort zurück: Was noch nicht läuft oder
    auf seinen Host wartet, ruft nichts mehr ab; laufende Abrufe enden im Hintergrund, spätestens
    nach ihrem Timeout."""
    locks = {host_of(url): threading.Lock() for url in urls}
    stop = threading.Event()

    def task(url: str) -> Result | None:
        with locks[host_of(url)]:
            if stop.is_set():
                return None
            result = check_url(url, timeout=timeout, retry_pause=retry_pause, proxies=proxies, stop=stop)
        result.origins = list(urls[url])
        return result

    results: list[Result] = []
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [pool.submit(task, url) for url in _interleave(list(urls))]
        for done, future in enumerate(as_completed(futures), 1):
            result = future.result()
            if result is not None:
                results.append(result)
            if progress:
                progress(done, len(futures))
    except BaseException:
        stop.set()
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown()
    rank = {BROKEN: 0, REDIRECT: 1, OK: 2}
    return sorted(results, key=lambda r: (rank[r.status], r.url))


# ---------------------------------------------------------------- Ausgabe

def is_failure(result: Result, strict: bool) -> bool:
    return result.status == BROKEN or (strict and result.status == REDIRECT and result.other_host)


def no_server_reached(results: list[Result]) -> bool:
    """Mehrere URLs und keine einzige HTTP-Antwort: eher ein Netzproblem als lauter tote Links."""
    return len(results) > 1 and all(r.http_status is None and not r.redirects for r in results)


def format_text(results: list[Result], strict: bool) -> str:
    width = max(len(label) for label in LABEL.values()) + 2
    pad = " " * width
    lines = []
    for r in results:
        lines.append(f"{LABEL[r.status]:<{width}}{r.url}")
        if r.status == BROKEN:
            lines.append(f"{pad}{r.detail}")
        elif r.status == REDIRECT:
            codes = " → ".join(str(code) for code, _ in r.redirects)
            note = ", auf http statt https" if r.to_http else ""
            note += ", anderer Host" + (", mit --strict ein Fehler" if strict else "") if r.other_host else ""
            lines.append(f"{pad}→ {r.final_url} ({codes}{note})")
        lines.extend(f"{pad}aus {origin}" for origin in r.origins)
    counts = {status: sum(r.status == status for r in results) for status in LABEL}
    other = sum(r.status == REDIRECT and r.other_host for r in results)
    summary = [f"{len(results)} URLs geprüft: {counts[OK]} OK, {counts[REDIRECT]} WEITERLEITUNG"
               f"{f' (davon {other} auf andere Hosts)' if other else ''}, {counts[BROKEN]} KAPUTT"]
    if no_server_reached(results):
        summary.append("Kein Server hat geantwortet: Das liegt vermutlich am Netz dieses Rechners "
                       "(Proxy, DNS, Firewall), nicht an den Quellen.")
    return "\n".join(lines + [""] + summary)


def as_json(results: list[Result], strict: bool, exit_code: int) -> str:
    return json.dumps({
        "checked": len(results),
        "ok": sum(r.status == OK for r in results),
        "redirect": sum(r.status == REDIRECT for r in results),
        "redirect_other_host": sum(r.status == REDIRECT and r.other_host for r in results),
        "broken": sum(r.status == BROKEN for r in results),
        "no_server_reached": no_server_reached(results),
        "strict": strict,
        "exit_code": exit_code,
        "user_agent": USER_AGENT,
        "results": [{
            "url": r.url, "status": r.status, "detail": r.detail, "http_status": r.http_status,
            "final_url": r.final_url, "other_host": r.other_host, "to_http": r.to_http,
            "redirects": [{"code": code, "url": target} for code, target in r.redirects],
            "origins": r.origins,
        } for r in results],
    }, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_sources.py",
        description="Quellen-Links aus tatilvakti/data/*.json, tatilvakti/i18n/*.json und "
                    "tatilvakti/templates/*.html prüfen.",
        epilog="Exit-Code 0 = nichts KAPUTT, 1 = mindestens ein Fehler, 2 = Aufruf- oder Datenfehler, "
               "130 = Strg+C. "
               "Mehr: docs/RUNBOOK.md, Abschnitt 5.")
    parser.add_argument("--only", action="append", default=[], metavar="HOST-TEIL",
                        help="nur URLs, deren Host das enthält, z. B. zoll.de (mehrfach möglich)")
    parser.add_argument("--strict", action="store_true",
                        help="Weiterleitungen auf andere Hosts zählen als Fehler")
    parser.add_argument("--json", action="store_true", help="Ergebnis als JSON")
    parser.add_argument("--list", action="store_true", help="nur sammeln und auflisten, nichts abrufen")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S, metavar="S",
                        help=f"Wartezeit je Abruf in Sekunden (Standard {DEFAULT_TIMEOUT_S})")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, metavar="N",
                        help=f"Abrufe gleichzeitig, 1–{MAX_WORKERS} (Standard {DEFAULT_WORKERS})")
    args = parser.parse_args(argv)
    if not args.timeout > 0:
        parser.error("--timeout muss größer als 0 sein")
    if not 1 <= args.workers <= MAX_WORKERS:
        parser.error(f"--workers muss zwischen 1 und {MAX_WORKERS} liegen")

    try:
        urls = collect_urls(default_files())
    except (OSError, ValueError) as exc:
        print(f"Daten nicht lesbar: {exc}", file=sys.stderr)
        return 2
    if args.only:
        needles = [needle.lower() for needle in args.only]
        urls = {url: origins for url, origins in urls.items()
                if any(needle in host_of(url) for needle in needles)}
    if not urls:
        print("Keine URL gefunden" + (f" (--only {' '.join(args.only)})" if args.only else ""),
              file=sys.stderr)
        return 2

    if args.list:
        if args.json:
            print(json.dumps([{"url": url, "origins": origins} for url, origins in sorted(urls.items())],
                             ensure_ascii=False, indent=2))
        else:
            for url, origins in sorted(urls.items()):
                print(url)
                print("\n".join(f"    aus {origin}" for origin in origins))
            print(f"\n{len(urls)} URLs")
        return 0

    def progress(done: int, total: int) -> None:
        print(f"\r{done}/{total} geprüft", end="" if done < total else "\r\033[K", file=sys.stderr, flush=True)

    try:
        results = check_all(urls, workers=args.workers, timeout=args.timeout,
                            progress=progress if sys.stderr.isatty() else None)
    except KeyboardInterrupt:
        print("\nabgebrochen", file=sys.stderr)
        return 130
    exit_code = 1 if any(is_failure(r, args.strict) for r in results) else 0
    print(as_json(results, args.strict, exit_code) if args.json else format_text(results, args.strict))
    return exit_code


if __name__ == "__main__":
    code = main()
    if code == 130:  # Strg+C: laufende Abrufe nicht abwarten (der Interpreter wartete sonst bis zu --timeout)
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    sys.exit(code)
