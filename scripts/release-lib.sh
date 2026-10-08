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
# Versuche im Sekundentakt auf /healthz nach einem Neustart. Ein Versuch dauert meist Millisekunden
# (Dienst startet noch: Verbindung abgelehnt), bei belegter Schreibsperre bis 5 s; hängt der Dienst,
# bis TIMEOUT_S in healthcheck.py (7 s). Im schlimmsten Fall also rund HEALTH_TRIES × 8 s (4 Min.).
HEALTH_TRIES=${TV_RELEASE_HEALTH_TRIES:-30}
# Verzeichnis dieser Datei, healthcheck.py liegt daneben. Physischer Pfad (pwd -P): rollback.sh
# läuft aus current/scripts, und current zeigt nach dem Umschalten auf ein anderes Release, das
# healthcheck.py vielleicht noch nicht hat.
LIB_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)

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

# Nur ein deploy.sh/rollback.sh zur Zeit: beide setzen current/previous und räumen releases/ auf.
# Die Sperre gilt bis zum Ende des Skripts (fd 9 bleibt offen).
take_lock() {
    [ -d "$BASE" ] || die "$BASE fehlt"
    exec 9>"$BASE/.lock"
    flock -n 9 || die "deploy.sh oder rollback.sh läuft bereits (Sperre $BASE/.lock)"
}

# Zustand laut systemd: active, activating, reloading, deactivating, inactive, failed …
service_state() {
    systemctl is-active "$SERVICE" 2>/dev/null || true
}

# Soll der Dienst laufen? Ja, wenn er läuft, gerade (neu) startet oder abgestürzt ist, auch nach
# zu vielen Fehlstarts (failed/start-limit-hit). 'inactive' heißt: bewusst gestoppt oder nie gestartet.
service_wanted() {
    case $(service_state) in
        active|activating|reloading|failed) return 0 ;;
        *) return 1 ;;
    esac
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

# Wartet bis zu HEALTH_TRIES Versuche lang (im Sekundentakt), bis /healthz gesund ist und (falls
# angegeben) die erwartete Build-ID meldet. Auswertung in scripts/healthcheck.py: Der Body zählt
# auch bei HTTP 503; 'db' unter 'down' ist ein Fehler, 'salt_db' allein nur eine WARNUNG (die
# Ursache liegt meist außerhalb des Releases, z. B. /run). Antworten älterer Releases zählen genauso.
wait_healthy() {  # $1 = erwartete Build-ID oder leer
    local expected=${1:-} i out=
    for i in $(seq 1 "$HEALTH_TRIES"); do
        if out=$(python3 "$LIB_DIR/healthcheck.py" "$HEALTH_URL" "$expected"); then
            printf '%s\n' "$out"
            return 0
        fi
        [ "$i" -ge "$HEALTH_TRIES" ] || sleep 1
    done
    log "Letzte Antwort: ${out:-keine}"
    return 1
}

# Dienst (neu) starten und auf einen gesunden Start warten. Vorher reset-failed: Nach zu vielen
# Fehlstarts (StartLimitBurst in der Unit) lehnt systemd sonst jeden weiteren Start ab, auch den
# eines heilen Releases, bis das Intervall abgelaufen ist.
restart_service() {  # $1 = erwartete Build-ID oder leer
    log "Neustart $SERVICE"
    systemctl reset-failed "$SERVICE" 2>/dev/null || true
    systemctl restart "$SERVICE" && wait_healthy "${1:-}"
}

# current auf ein neues Release umschalten (previous zeigt dann auf das bisherige) und den Dienst
# neu starten, falls er laufen soll. Startet das neue Release nicht sauber, geht es automatisch
# zurück auf das bisherige, und das Skript endet mit Fehler.
activate_release() {  # $1 = releases/<name>, $2 = erwartete Build-ID
    local new=$1 build=$2 prev old_prev wanted=0
    prev=$(link_target current)
    old_prev=$(link_target previous)
    # Vor dem Umschalten festhalten: Nach einem Fehlstart des neuen Releases steht der Dienst auf
    # 'activating' (Neustart-Takt) oder 'failed' (start-limit-hit) und sagt das nicht mehr.
    if service_wanted; then wanted=1; fi
    set_link "$new" current
    [ -z "$prev" ] || set_link "$prev" previous
    log "current → $new (vorher: ${prev:-keins}), Build $build"
    if [ "$wanted" = 0 ]; then
        log "$SERVICE läuft nicht ($(service_state)), kein Neustart. Starten: systemctl enable --now $SERVICE"
        return 0
    fi
    restart_service "$build" && return 0

    log "Das neue Release startet nicht sauber. Details: journalctl -u $SERVICE -n 50"
    [ -n "$prev" ] || die "Kein vorheriges Release für einen automatischen Rollback vorhanden."
    set_link "$prev" current
    if [ -n "$old_prev" ]; then set_link "$old_prev" previous; else rm -f "$BASE/previous"; fi
    log "Automatisch zurück auf $prev"
    restart_service "" || die "Auch $prev startet nicht. Notfall: docs/MIGRATION.md → Rollback"
    die "Deploy zurückgerollt. Das fehlerhafte Release bleibt zur Analyse unter $BASE/$new"
}
