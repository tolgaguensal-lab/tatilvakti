# shellcheck shell=bash
# Gemeinsame Funktionen für scripts/deploy.sh, scripts/rollback.sh und scripts/restore.sh.
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
# Sicherungen von scripts/backup.py: nachts tatilvakti-<UTC-Zeit>.db, vor jedem Deploy
# pre-deploy-<release>.db (pre_deploy_snapshot)
BACKUP_DIR=$(dirname "$DB")/backups
HEALTH_URL=${TV_RELEASE_HEALTH_URL:-http://127.0.0.1:3096/healthz}
# Versuche im Sekundentakt auf /healthz nach einem Neustart. Ein Versuch dauert meist Millisekunden
# (Dienst startet noch: Verbindung abgelehnt), bei belegter Schreibsperre bis 5 s; hängt der Dienst,
# bis TIMEOUT_S in healthcheck.py (7 s). Im schlimmsten Fall also rund HEALTH_TRIES × 8 s (4 Min.).
HEALTH_TRIES=${TV_RELEASE_HEALTH_TRIES:-30}
# Verzeichnis dieser Datei, healthcheck.py liegt daneben. Physischer Pfad (pwd -P): rollback.sh
# läuft aus current/scripts, und current zeigt nach dem Umschalten auf ein anderes Release, das
# healthcheck.py vielleicht noch nicht hat.
LIB_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
# schema_check.py und backup.py laufen als Dienstbenutzer und kommen immer aus dem aufrufenden
# Skript, nicht aus dem Ziel-Release (ältere Releases kennen sie nicht oder ohne --pre-deploy).
# Standard: neben dieser Datei. deploy.sh läuft aus einem Git-Klon, den der Dienstbenutzer vielleicht
# nicht lesen darf, und setzt HELPERS deshalb auf eine lesbare Kopie.
HELPERS=$LIB_DIR
FORCE_SCHEMA=   # Begründung aus --force-schema (deploy.sh, rollback.sh)
SCHEMA_DB=      # von check_schema gesetzt: Schema-Version der DB …
SCHEMA_RELEASE= # … und die, die das geprüfte Release kennt (beide leer, wenn nicht lesbar)
SCHEMA_BACKUP=  # … und bei einer neueren DB die neueste Sicherung, die das Release kennt
SNAPSHOT=       # von pre_deploy_snapshot gesetzt: Sicherung vor dem Umschalten

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

# Nur ein deploy.sh/rollback.sh/restore.sh zur Zeit: deploy.sh und rollback.sh setzen
# current/previous und räumen releases/ auf, alle drei starten den Dienst neu, restore.sh tauscht
# die DB aus. Die Sperre gilt bis zum Ende des Skripts (fd 9 bleibt offen).
take_lock() {
    [ -d "$BASE" ] || die "$BASE fehlt"
    exec 9>"$BASE/.lock"
    flock -n 9 || die "deploy.sh, rollback.sh oder restore.sh läuft bereits (Sperre $BASE/.lock)"
}

# Zustand laut systemd: active, activating, reloading, deactivating, inactive, failed …
service_state() {  # $1 = Unit, Standard ist der Dienst
    systemctl is-active "${1:-$SERVICE}" 2>/dev/null || true
}

# Soll der Dienst laufen? Ja, wenn er läuft, gerade (neu) startet oder abgestürzt ist, auch nach
# zu vielen Fehlstarts (failed/start-limit-hit). 'inactive' heißt: bewusst gestoppt oder nie gestartet.
# restore.sh fragt so auch die Timer ab.
service_wanted() {  # $1 = Unit, Standard ist der Dienst
    case $(service_state "${1:-}") in
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

# --force-schema "<Grund>": Der Grund muss da sein und darf keine weitere Option sein
force_reason() {  # $1 = Wert nach --force-schema
    if ! [[ ${1-} =~ [^[:space:]] ]] || [[ $1 == -* ]]; then
        die "--force-schema braucht eine Begründung, z. B. --force-schema \"Startseite leer, Fix folgt\""
    fi
}

# Schema-Version (Audit d2-schema-version-rollback): Kennt das Release die Schema-Version der DB?
# schema_check.py liest als Dienstbenutzer user_version der DB und SCHEMA_VERSION des Releases.
# Setzt SCHEMA_DB, SCHEMA_RELEASE und SCHEMA_BACKUP. Endet mit 1, wenn die DB neuer ist (die Ausgabe
# nennt dann passende Sicherungen, danach schema_hint), mit 2, wenn eine Version nicht lesbar ist
# (danach schema_unreadable_hint). Beides nicht, wenn FORCE_SCHEMA eine Begründung enthält: Die
# steht dann auch im Journal (journalctl -t tatilvakti-v2-release).
check_schema() {  # $1 = Release-Verzeichnis
    local out rc=0 versions text
    out=$(cd / && as_service_user python3 "$HELPERS/schema_check.py" \
        --db "$DB" --release "$1" --backups "$BACKUP_DIR" 2>&1) || rc=$?
    versions=$(sed -n '1s/^schema db=\([0-9][0-9]*\) release=\([0-9][0-9]*\)$/\1 \2/p' <<<"$out")
    SCHEMA_DB=${versions% *}
    SCHEMA_RELEASE=${versions#* }
    # Erste Zeile der Liste "  <Pfad>  (Schema-Version <n>)" von schema_check.py
    SCHEMA_BACKUP=$(sed -n '/^  .*  (Schema-Version [0-9][0-9]*)$/{s/^  \(.*\)  (Schema-Version [0-9]*)$/\1/p;q;}' <<<"$out")
    if [ "$rc" = 0 ] && [ -n "$versions" ]; then
        text="Schema-Version: Datenbank $SCHEMA_DB, ${1##*/} kennt bis $SCHEMA_RELEASE"
        if [ "$SCHEMA_DB" -gt 0 ] && [ "$SCHEMA_RELEASE" -gt "$SCHEMA_DB" ]; then
            text+=" (stellt die Datenbank beim Start um)"
        fi
        log "$text"
        return 0
    fi
    [ "$rc" != 0 ] || { out="unerwartete Ausgabe von schema_check.py: $out"; rc=2; }
    [ -n "$versions" ] || rc=2  # ohne beide Versionen ist nichts bekannt, auch bei einem anderen Fehler
    sed '/^schema db=/d' <<<"$out" >&2
    [ -n "$FORCE_SCHEMA" ] || return "$rc"
    text="Schema-Prüfung für ${1##*/} übersteuert (--force-schema): $FORCE_SCHEMA"
    log "WARNUNG: $text"
    logger -t "$APP-release" -p user.warning -- "$text" 2>/dev/null || true
}

# Wege nach einem Abbruch wegen der Schema-Version (deploy.sh, rollback.sh)
schema_hint() {  # $1 = Aufruf mit --force-schema, $2 = restore.sh zum Zurückspielen
    cat >&2 <<EOF
Ein älteres Release pflegt neuere Daten nicht richtig, z. B. löscht seine Wartung Prüfwerte in
Spalten nicht, die es nicht kennt. Wege:
  1. Vorwärts statt zurück: den Fehler beheben und ein neueres Release ausrollen (deploy.sh).
  2. Zurück samt Datenstand einer passenden Sicherung (siehe oben, meist die vor dem Deploy).
     Meldungen seit dieser Sicherung gehen verloren:
       $1
       sudo $2 <Sicherung>
  3. Notfall, die Daten bleiben: nur der erste Befehl aus 2. Was das Release nicht kennt, bleibt
     liegen, bis wieder ein neueres Release läuft (es räumt beim Start auf). Bald vorwärts gehen.
Mehr: docs/RUNBOOK.md, README → Update und Rollback.
EOF
}

# Hinweis, wenn check_schema eine Version nicht lesen konnte (Ende mit 2): Dann ist nicht bekannt,
# ob das Ziel die Datenbank kennt. Meist liegt die Ursache an der Datenbank selbst.
schema_unreadable_hint() {  # $1 = Aufruf mit --force-schema
    cat >&2 <<EOF
Ohne lesbare Schema-Version lässt sich nicht prüfen, ob das Ziel die Datenbank kennt. Erst die
Ursache beheben, bei der Datenbank meist falsche Rechte an tatilvakti.db-wal/-shm (docs/RUNBOOK.md 2d)
oder eine beschädigte Datei (2e, dann Restore 3c), danach erneut. Notfall, ohne diese Prüfung:
  $1
EOF
}

# Abschluss nach --force-schema auf ein Release, das die Schema-Version der DB nicht kennt: Die DB
# behält ihre Version. Weg 2 aus schema_hint braucht jetzt noch die passende Sicherung.
forced_schema_note() {  # $1 = aktives Release, $2 = restore.sh zum Zurückspielen
    { [ -n "$FORCE_SCHEMA" ] && [ -n "$SCHEMA_DB" ] && [ "$SCHEMA_DB" -gt "$SCHEMA_RELEASE" ]; } || return 0
    log "Die Datenbank hat weiter Schema-Version $SCHEMA_DB, ${1##*/} kennt bis $SCHEMA_RELEASE."
    if [ -n "$SCHEMA_BACKUP" ]; then
        log "Weg 2, passende Sicherung zurückspielen: sudo $2 $SCHEMA_BACKUP"
    else
        log "Keine passende Sicherung gefunden (siehe oben)."
    fi
    log "Sonst bald vorwärts: ein neueres Release ausrollen (README → Update und Rollback → Schema-Version)."
}

# Sicherung vor dem Umschalten (Audit d4-kein-db-snapshot-vor-umschalten): backup.py als
# Dienstbenutzer nach BACKUP_DIR/pre-deploy-<release>.db, konsistent per Online-Backup, ohne
# Prüfwerte und Schlüssel. backup.py prüft vorher den Platz und behält danach die neuesten 3 solcher
# Sicherungen und je ältere Schema-Version die neueste (die nennt schema_hint für Weg 2). Setzt
# SNAPSHOT (leer ohne DB, etwa beim ersten Deploy). Endet mit Fehler, wenn die Sicherung scheitert.
pre_deploy_snapshot() {  # $1 = Name des neuen Releases (<UTC-Zeit>-<Commit>)
    local out
    SNAPSHOT=
    if [ ! -e "$DB" ]; then
        log "Keine Datenbank ($DB), also keine Sicherung vor dem Umschalten"
        return 0
    fi
    log "Sicherung vor dem Umschalten (backup.py)"
    if ! out=$(cd / && as_service_user python3 "$HELPERS/backup.py" \
            --db "$DB" --dest "$BACKUP_DIR" --pre-deploy "$1" 2>&1); then
        printf '%s\n' "$out" >&2
        return 1
    fi
    printf '%s\n' "$out"
    SNAPSHOT=$(sed -n 's/^ok \(.*\) reports=[0-9][0-9]* .*$/\1/p' <<<"$out")
    [ -n "$SNAPSHOT" ] || { printf 'unerwartete Ausgabe von backup.py: %s\n' "$out" >&2; return 1; }
}

# Releases von vor der Sicherung vor dem Deploy kennen pre-deploy-*.db nicht, ihre nächtliche
# Sicherung löscht sie also nicht nach 14 Tagen (Datenschutztext), ebenso wenig Reste abgebrochener
# Sicherungen (.pre-deploy-*.db.tmp, mit Prüfwerten). Dann die Befehle dafür nennen.
warn_snapshot_prune() {  # $1 = Release-Verzeichnis, das jetzt aktiv ist
    grep -q -- '--pre-deploy' "$1/scripts/backup.py" 2>/dev/null && return 0
    [ -n "$(find "$BACKUP_DIR" -maxdepth 1 \( -name 'pre-deploy-*.db' -o -name '.pre-deploy-*.db.tmp' \) \
        -print -quit 2>/dev/null)" ] || return 0
    log "WARNUNG: ${1##*/} löscht Sicherungen vor dem Deploy (pre-deploy-*.db) nachts nicht. Solange es"
    log "aktiv ist, ab 14 Tagen von Hand: sudo find $BACKUP_DIR -maxdepth 1 -name 'pre-deploy-*.db' -mtime +13 -delete"
    log "Reste abgebrochener Sicherungen (mit Prüfwerten) gleich: sudo find $BACKUP_DIR -maxdepth 1 -name '.pre-deploy-*.db.tmp' -delete"
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
# zurück auf das bisherige, und das Skript endet mit Fehler; davor steht der Hinweis $3 (deploy.sh:
# die Sicherung vor dem Umschalten).
activate_release() {  # $1 = releases/<name>, $2 = erwartete Build-ID, $3 = Hinweis (optional)
    local new=$1 build=$2 note=${3:-} prev old_prev wanted=0
    prev=$(link_target current)
    old_prev=$(link_target previous)
    # Vor dem Umschalten festhalten: Nach einem Fehlstart des neuen Releases steht der Dienst auf
    # 'activating' (Neustart-Takt) oder 'failed' (start-limit-hit) und sagt das nicht mehr.
    if service_wanted "$SERVICE"; then wanted=1; fi
    set_link "$new" current
    [ -z "$prev" ] || set_link "$prev" previous
    log "current → $new (vorher: ${prev:-keins}), Build $build"
    if [ "$wanted" = 0 ]; then
        log "$SERVICE läuft nicht ($(service_state)), kein Neustart. Starten: systemctl enable --now $SERVICE"
        return 0
    fi
    restart_service "$build" && return 0

    log "Das neue Release startet nicht sauber. Details: journalctl -u $SERVICE -n 50"
    [ -z "$note" ] || log "$note"
    [ -n "$prev" ] || die "Kein vorheriges Release für einen automatischen Rollback vorhanden."
    set_link "$prev" current
    if [ -n "$old_prev" ]; then set_link "$old_prev" previous; else rm -f "$BASE/previous"; fi
    log "Automatisch zurück auf $prev"
    restart_service "" || die "Auch $prev startet nicht. Notfall: docs/MIGRATION.md → Rollback"
    warn_snapshot_prune "$BASE/$prev"
    die "Deploy zurückgerollt. Das fehlerhafte Release bleibt zur Analyse unter $BASE/$new"
}
