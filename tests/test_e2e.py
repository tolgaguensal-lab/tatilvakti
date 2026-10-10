"""Browser-Tests (Node-Playwright) für Service Worker, Offline-Warteschlange, Layout, Kill-Switch,
ältere Browser, präparierte Links, die Sticky-Toolbar sowie Kaltstart, Melden und Teilen.

Laufen nur mit TV_E2E=1, sonst werden sie übersprungen (CI ohne Browser bleibt grün). Playwright
muss für Node auffindbar sein (NODE_PATH oder PLAYWRIGHT_MODULE), zum Beispiel:

    TV_E2E=1 NODE_PATH=/opt/node-tools/node_modules .venv/bin/python -m pytest -m e2e

Jedes Skript in tests/e2e/ (außer lib.js) bekommt eine eigene App mit leerer Temp-DB und fester Uhr.
Chromium rendert mit fester Systemschrift DejaVu Sans (tests/e2e/fonts.conf, Paket fonts-dejavu-core),
damit die Layout-Prüfungen auf jedem Rechner und in der CI gleich messen. Ein eigenes FONTCONFIG_FILE
im Aufruf geht vor, z. B. um mit einer anderen Schrift zu prüfen.
"""
import os
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
from werkzeug.serving import WSGIRequestHandler, make_server

from tatilvakti import create_app

E2E_DIR = Path(__file__).parent / "e2e"
SCRIPTS = sorted(p.name for p in E2E_DIR.glob("*.js") if p.name != "lib.js")
FONTS_CONF = E2E_DIR / "fonts.conf"
FONT = "DejaVu Sans"
# Mitten in den Sommerferien 2027: Ferien-Radar und Grenzseiten zeigen echte Inhalte
NOW = datetime(2027, 7, 20, 8, 0, tzinfo=timezone.utc)
LEGACY_SW_PATHS = "/service-worker.js,/app/sw.js"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("TV_E2E") != "1",
                       reason="Browser-Tests nur mit TV_E2E=1 (braucht Node und Playwright, siehe Docstring)"),
]


class QuietHandler(WSGIRequestHandler):
    """Kein Zugriffslog je Anfrage (der Service Worker lädt beim Install über 50 Seiten)."""

    def log_request(self, *args, **kwargs):
        pass


@pytest.fixture
def live_app(tmp_path):
    app = create_app({"TESTING": True, "TV_DB_PATH": str(tmp_path / "e2e.db"), "TV_CLOCK": lambda: NOW,
                      "TV_LEGACY_SW_PATHS": LEGACY_SW_PATHS})
    server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=10)


@pytest.fixture(scope="module")
def node():
    binary = shutil.which("node")
    if not binary:
        pytest.fail("TV_E2E=1, aber node fehlt im PATH")
    probe = subprocess.run([binary, "-e", "require.resolve(process.env.PLAYWRIGHT_MODULE || 'playwright')"],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.fail("Playwright für Node nicht gefunden (NODE_PATH oder PLAYWRIGHT_MODULE setzen):\n" + probe.stderr)
    return binary


@pytest.fixture(scope="module")
def font_env():
    """FONTCONFIG_FILE für Chromium, außer der Aufruf setzt es selbst. Liefert fontconfig damit nicht
    DejaVu Sans, schlägt der Test fehl, statt mit einer schmaleren Ersatzschrift grün zu werden
    (ohne fc-match, Paket fontconfig, entfällt diese Prüfung)."""
    if "FONTCONFIG_FILE" in os.environ:
        return {}
    env = {"FONTCONFIG_FILE": str(FONTS_CONF)}
    fc_match = shutil.which("fc-match")
    if fc_match:
        got = subprocess.run([fc_match, "-f", "%{family[0]}", "system-ui"], env={**os.environ, **env},
                             capture_output=True, text=True).stdout
        if got != FONT:
            pytest.fail(f"fontconfig liefert für system-ui {got!r} statt {FONT!r} (Paket fonts-dejavu-core fehlt?)")
    return env


def test_scripts_are_found():
    assert {"service_worker.js", "report_queue.js", "layout_hint.js", "kill_switch.js",
            "old_browsers.js", "bad_params.js", "toolbar_layout.js", "share_report.js"} <= set(SCRIPTS)


@pytest.mark.parametrize("script", SCRIPTS)
def test_browser(node, font_env, live_app, script):
    env = {**os.environ, **font_env, "TV_E2E_BASE": live_app, "TV_E2E_NOW": NOW.isoformat().replace("+00:00", "Z"),
           "TV_E2E_LEGACY_SW": LEGACY_SW_PATHS}
    result = subprocess.run([node, str(E2E_DIR / script)], env=env, capture_output=True, text=True, timeout=600)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "FAIL" not in result.stdout, output
