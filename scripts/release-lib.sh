# shellcheck shell=bash
# Gemeinsame Funktionen für scripts/deploy.sh und scripts/rollback.sh.
# Wird per "source" geladen, nicht direkt ausführen.
#
# Standardwerte passen zu deploy/tatilvakti-v2.service. Die TV_RELEASE_*-Variablen sind nur für
# Tests auf einem anderen Rechner gedacht (z. B. Releases unter einem Testverzeichnis).

APP=tatilvakti-v2
BASE=${TV_RELEASE_BASE:-/opt/$APP}
SERVICE=${TV_RELEASE_SERVICE:-$APP.service}
SERVICE_USER=${TV_RELEASE_USER:-$APP}
ENV_FILE=${TV_RELEASE_ENV_FILE:-/etc/$APP.env}
DB=${TV_RELEASE_DB:-/var/lib/$APP/tatilvakti.db}
HEALTH_URL=${TV_RELEASE_HEALTH_URL:-http://127.0.0.1:3096/healthz}

log() { printf '%s  %s\n' "$(date '+%H:%M:%S')" "$*"; }
die() { printf 'FEHLER: %s\n' "$*" >&2; exit 1; }

# Befehl als Dienstbenutzer ausführen (als root per runuser, sonst direkt). Wichtig für alles,
# was die Produktions-DB öffnet: SQLite legt dabei -wal/-shm an, die dem Dienst gehören müssen.
as_service_user() {
    if [ "$(id -u)" -eq 0 ] && [ "$SERVICE_USER" != root ]; then
        runuser -u "$SERVICE_USER" -- "$@"
    else
        "$@"
    fi
}

# Symlink in BASE atomar setzen: neuer Link unter Hilfsnamen, dann rename(2) über den alten.
set_link() {  # $1 = Ziel relativ zu BASE (z. B. releases/<name>), $2 = Linkname
    ln -sfn "$1" "$BASE/.$2.new"
    mv -Tf "$BASE/.$2.new" "$BASE/$2"
}

link_target() {  # $1 = Linkname in BASE; leer, wenn es ihn nicht gibt
    readlink "$BASE/$1" 2>/dev/null || true
}

service_active() {
    systemctl is-active --quiet "$SERVICE" 2>/dev/null
}

# Preflight eines Releases als Dienstbenutzer. Gibt die Ausgabe weiter und setzt PREFLIGHT_BUILD.
run_preflight() {  # $1 = Release-Verzeichnis, weitere Argumente gehen an preflight.py
    local rel=$1 out
    shift
    out=$(mktemp)
    if (cd "$rel" && as_service_user "$rel/.venv/bin/python" scripts/preflight.py \
            --env-file "$ENV_FILE" --db "$DB" "$@") 2>&1 | tee "$out"; then
        # shellcheck disable=SC2034  # Ergebnis für deploy.sh/rollback.sh
        PREFLIGHT_BUILD=$(sed -n 's/^preflight ok build=//p' "$out")
        rm -f "$out"
        return 0
    fi
    rm -f "$out"
    return 1
}

# Wartet bis zu 30 s, bis /healthz mit HTTP 200 antwortet und (falls angegeben) die erwartete
# Build-ID meldet. HTTP 503 heißt: Die App läuft, erreicht aber ihre Datenbank nicht.
wait_healthy() {  # $1 = erwartete Build-ID oder leer
    local expected=${1:-} _
    for _ in $(seq 1 30); do
        if python3 - "$HEALTH_URL" "$expected" <<'PY'
import json
import sys
import urllib.request

url, expected = sys.argv[1], sys.argv[2]
try:
    with urllib.request.urlopen(url, timeout=3) as resp:
        data = json.load(resp)
except Exception:
    sys.exit(1)
if expected and data.get("build") != expected:
    sys.exit(1)
print(f"/healthz: status={data.get('status')} build={data.get('build')} "
      f"attention={data.get('attention', [])}")
PY
        then
            return 0
        fi
        sleep 1
    done
    return 1
}

# Dienst neu starten, falls er läuft, und auf einen gesunden Start warten.
restart_if_active() {  # $1 = erwartete Build-ID oder leer
    if ! service_active; then
        log "$SERVICE läuft nicht, kein Neustart. Starten: systemctl enable --now $SERVICE"
        return 0
    fi
    log "Neustart $SERVICE"
    systemctl restart "$SERVICE" && wait_healthy "${1:-}"
}
