#!/usr/bin/env python3
"""Startet tatilvakti für einen Nutzertest: eigene Temp-DB, eigene Uhr, wahlweise mit Meldungen.

    .venv/bin/python .claude/skills/nutzertest/scripts/serve.py \
        --now 2027-07-31T05:30 --seed saison --port 18900 --dir tmp/nutzertest-x/ayse

Die Uhr startet bei --now (Ortszeit Europe/Berlin, oder ISO mit Offset) und läuft danach normal
weiter, damit „vor 3 Min.“ und Meldesperren sich wie im echten Betrieb verhalten. Die App läuft,
bis der Prozess beendet wird (PID in <dir>/server.pid, Adresse in <dir>/server.url).

--seed:
  leer    keine Meldungen – so sieht die App direkt nach dem Start aus
  saison  ausgedachte Meldungen für eine volle Sommerwoche: vier Wochen Verlauf an den
          Hauptübergängen und frische Meldungen der letzten zwei Stunden. Die Zahlen sind
          erfunden. Ein Nutzertest beurteilt damit die Darstellung, nie die Richtigkeit der Werte.
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from werkzeug.serving import WSGIRequestHandler, make_server  # noqa: E402

from tatilvakti import create_app  # noqa: E402
from tatilvakti.db import connect  # noqa: E402

BERLIN = ZoneInfo("Europe/Berlin")

# Ausgedachte Grundlast je Übergang und Richtung (Stufe 0–5 der App, siehe borders.BUCKET_MINUTES).
# Kapıkule Richtung Türkei ist im Sommer der Engpass, Richtung Deutschland ruhig.
PROFILE = {
    ("kapikule", "to_tr"): 3, ("kapikule", "to_de"): 1,
    ("gradina", "to_tr"): 2, ("gradina", "to_de"): 1,
    ("horgos", "to_tr"): 2, ("horgos", "to_de"): 1,
    ("batrovci", "to_tr"): 1, ("batrovci", "to_de"): 1,
    ("ipsala", "to_tr"): 1, ("derekoy", "to_tr"): 1,
}


def parse_now(text: str) -> datetime:
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        value = value.replace(tzinfo=BERLIN)
    return value.astimezone(timezone.utc)


def seed_saison(db_path: str, now: datetime, rng: random.Random) -> int:
    """Vier Wochen Verlauf (je Stunde 0–2 Meldungen, nachmittags mehr Stau) und frische Meldungen."""
    conn = connect(db_path)
    count = 0
    try:
        stamp = int(now.timestamp())
        for (cid, direction), base in PROFILE.items():
            for hours_ago in range(3, 28 * 24):
                ts = stamp - hours_ago * 3600 - rng.randint(0, 3599)
                hour = datetime.fromtimestamp(ts, BERLIN).hour
                rush = 1 if 13 <= hour <= 22 else (-1 if hour <= 5 else 0)
                for _ in range(rng.choice((0, 1, 1, 2))):
                    bucket = max(0, min(5, base + rush + rng.choice((-1, 0, 0, 1))))
                    conn.execute("INSERT INTO reports (crossing, direction, bucket, observed_at, created_at) "
                                 "VALUES (?, ?, ?, ?, ?)", (cid, direction, bucket, ts, ts))
                    count += 1
            # Frisch: die letzten zwei Stunden, nicht an jedem Übergang (leere Stellen gehören dazu)
            if base >= 2 or rng.random() < 0.5:
                for _ in range(rng.randint(2, 6)):
                    ts = stamp - rng.randint(60, 110 * 60)
                    bucket = max(0, min(5, base + rng.choice((0, 0, 1))))
                    conn.execute("INSERT INTO reports (crossing, direction, bucket, observed_at, created_at) "
                                 "VALUES (?, ?, ?, ?, ?)", (cid, direction, bucket, ts, ts))
                    count += 1
    finally:
        conn.close()
    return count


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):  # der Service Worker lädt beim Install über 50 Seiten
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--now", required=True, help="Start der App-Uhr, z. B. 2027-07-31T05:30 (Berlin)")
    parser.add_argument("--seed", choices=("leer", "saison"), default="leer")
    parser.add_argument("--port", type=int, default=0, help="0 = freier Port")
    parser.add_argument("--dir", required=True, help="Arbeitsordner für DB, PID und URL")
    parser.add_argument("--random-seed", type=int, default=7, help="gleiche Zahl, gleiche Meldungen")
    args = parser.parse_args()

    workdir = Path(args.dir)
    workdir.mkdir(parents=True, exist_ok=True)
    db_path = workdir / "app.db"
    for suffix in ("", "-wal", "-shm", ".salts", ".salts-wal", ".salts-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)

    start_app = parse_now(args.now)
    start_real = datetime.now(timezone.utc)

    def clock() -> datetime:
        return start_app + (datetime.now(timezone.utc) - start_real)

    # Impressum und Datenschutz mit Platzhaltern, wie sie im Betrieb aus /etc/tatilvakti-v2.env kommen
    app = create_app({"TESTING": True, "TV_DB_PATH": str(db_path), "TV_CLOCK": clock,
                      "TV_OPERATOR_NAME": "Nutzertest", "TV_OPERATOR_ADDRESS": "Teststraße 1; 00000 Teststadt",
                      "TV_OPERATOR_EMAIL": "test@example.invalid",
                      "TV_HOSTING_PROVIDER": "Testhoster GmbH, Teststadt", "TV_PROXY_LOG_DAYS": "7"})
    seeded = seed_saison(str(db_path), start_app, random.Random(args.random_seed)) if args.seed == "saison" else 0

    server = make_server("127.0.0.1", args.port, app, threaded=True, request_handler=QuietHandler)
    url = f"http://127.0.0.1:{server.server_port}"
    (workdir / "server.url").write_text(url + "\n", encoding="utf-8")
    (workdir / "server.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    local = start_app.astimezone(BERLIN).strftime("%d.%m.%Y %H:%M")
    print(f"{url}  (App-Uhr ab {local} Berlin, Seed {args.seed}, {seeded} Meldungen)", flush=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        thread.join()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
