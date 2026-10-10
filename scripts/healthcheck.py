#!/usr/bin/env python3
"""/healthz eines frisch gestarteten Dienstes auswerten (für wait_healthy in scripts/release-lib.sh).

    python3 scripts/healthcheck.py <url> [<erwartete Build-ID>]

Nur Standardbibliothek: läuft mit dem System-Python, auch für ein Release ohne venv.

Der Body zählt auch bei HTTP 503 (status 'down', Gründe in 'down'):
- 'db': Die Haupt-DB nimmt keine Meldung an. Das Release gilt als nicht gesund.
- 'salt_db' allein: gesund mit WARNUNG. Die Seiten laufen, aber jede Meldung scheitert, und die
  Ursache liegt meist außerhalb des Releases (/run); ein Rollback änderte daran nichts.
- unbekannte Gründe (neuere Releases): nicht gesund, lieber einmal zu vorsichtig.
Ältere Releases (Rollback) antworten ohne 'down', bei kaputter Schlüssel-DB mit HTTP 200 und
salt_db=false, bei kaputter Haupt-DB mit 503 und db=false; das zählt genauso. Die knappe Antwort
für Aufrufe von außen (nur status, build, down) zählt ebenfalls, falls TV_RELEASE_HEALTH_URL
einmal auf die öffentliche Adresse zeigt.

Exit-Code 0 = gesund (und die erwartete Build-ID), sonst 1. Ausgabe: eine Zeile mit status,
build, down und attention, bei Fehlern mit Grund; die WARNUNG zur Schlüssel-DB auf stderr.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

# Wartezeit je Abruf: /healthz wartet bei belegter Schreibsperre bis zu db.BUSY_TIMEOUT_MS (5 s),
# dazu Spielraum. Nicht mehr, denn wait_healthy versucht es bis zu HEALTH_TRIES-mal: Hängt der
# Dienst, dauert das bis zu HEALTH_TRIES × (TIMEOUT_S + 1) s. test_healthcheck_timeout_… hält es gleich.
TIMEOUT_S = 7


def fetch(url: str) -> dict:
    """/healthz lesen, auch bei HTTP-Fehlern (503): Der Body nennt die Gründe."""
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        with exc:
            data = json.load(exc)
    if not isinstance(data, dict):
        raise ValueError("kein JSON-Objekt")
    return data


def evaluate(data: dict, expected: str = "") -> tuple[bool, str, list[str]]:
    """(gesund, Zeile für die Ausgabe, Warnungen)."""
    down = data.get("down")
    has_down = isinstance(down, list)
    if not has_down:  # älteres Release
        down = [name for name in ("db", "salt_db") if data.get(name) is False]
    fatal = [str(name) for name in down if name != "salt_db"]
    # db zählt immer, wenn die Antwort es nennt; ohne 'down' (älteres Release) muss es true sein.
    # Die knappe Antwort für Aufrufe von außen nennt nur 'down'.
    if (not has_down or "db" in data) and data.get("db") is not True and "db" not in fatal:
        fatal.append("db")
    line = (f"/healthz: status={data.get('status')} build={data.get('build')} "
            f"down={down} attention={data.get('attention', [])}")
    warnings = []
    if "salt_db" in down:
        warnings.append("WARNUNG: Schlüssel-DB (TV_SALT_DB_PATH) nicht nutzbar, jede Meldung scheitert. "
                        "Ursache: journalctl -u tatilvakti-v2 -n 50")
    if fatal:
        return False, f"{line} – Ausfall: {', '.join(fatal)}", warnings
    if expected and data.get("build") != expected:
        return False, f"{line} – erwartet build={expected}", warnings
    return True, line, warnings


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not 1 <= len(args) <= 2:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    url, expected = args[0], (args[1] if len(args) > 1 else "")
    try:
        data = fetch(url)
    except Exception as exc:  # Dienst startet noch, falsche URL, kein JSON …
        print(f"/healthz: keine auswertbare Antwort ({type(exc).__name__})")
        return 1
    healthy, line, warnings = evaluate(data, expected)
    print(line)
    if healthy:
        for warning in warnings:
            print(warning, file=sys.stderr)
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
