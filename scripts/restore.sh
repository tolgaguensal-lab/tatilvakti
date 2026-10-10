#!/usr/bin/env bash
# Sicherung der Haupt-DB von tatilvakti v2 zurückspielen (als root):
#
#   sudo /opt/tatilvakti-v2/current/scripts/restore.sh /var/lib/tatilvakti-v2/backups/tatilvakti-<zeit>.db
#   sudo /opt/tatilvakti-v2/current/scripts/restore.sh --start <sicherung>    # danach alles starten
#   sudo /opt/tatilvakti-v2/current/scripts/restore.sh --check [<sicherung>]  # Probe, ändert nichts
#
# 1. Sicherung als Kopie neben die DB legen (Dienstbenutzer, 0600), mit backup.py --verify prüfen.
#    Ist sie fehlerhaft, endet das Skript hier, Dienst und DB bleiben unverändert.
# 2. Timer, Wartung, Backup und Dienst stoppen (die Wartung legte sonst in der Lücke eine leere DB an).
# 3. tatilvakti.db samt -wal, -shm und -journal nach pre-restore-<UTC-Zeit>/ verschieben, nie löschen.
#    Eine liegengebliebene -wal (Absturz, SIGKILL) legte SQLite sonst über die Sicherung: alte Daten
#    oder eine beschädigte DB, und /healthz merkt das nicht.
# 4. Geprüfte Kopie an ihren Platz (rename), dort erneut prüfen: integrity_check, Meldungszahl.
# 5. Wieder starten, was vorher lief, und auf /healthz warten (scripts/healthcheck.py wie bei
#    deploy.sh). Wie bei rollback.sh bleibt ein bewusst gestoppter Dienst gestoppt; --start startet
#    Dienst und Timer in jedem Fall.
# Scheitert ein Schritt ab 2 oder wird das Skript abgebrochen (Strg-C, kill, Verbindung weg), nennt
# die Ausgabe die Befehle zurück auf den alten Stand. Ab Schritt 3 stehen sie auch in
# pre-restore-<UTC-Zeit>/zurueck.txt, falls niemand mehr die Ausgabe liest.
#
# --check: nur Schritt 1, dazu der Preflight des aktiven Releases gegen die Kopie (create_app,
# /healthz, alle Seiten). Ohne Angabe die neueste Sicherung. Für den Restore-Drill (README).
#
# Die Schlüssel-DB (/run/tatilvakti-v2/salts.db) bleibt unberührt, sie wird nie gesichert.
# deploy.sh, rollback.sh und restore.sh laufen nie gleichzeitig (Sperre /opt/tatilvakti-v2/.lock).
set -euo pipefail
umask 077

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=scripts/release-lib.sh
. "$HERE/release-lib.sh"

# Python für backup.py (nur Standardbibliothek, wie healthcheck.py). Die Variable ist für Tests.
PYTHON=${TV_RESTORE_PYTHON:-python3}
DATA_DIR=$(dirname "$DB")
NAME=${DB##*/}
BACKUP_DIR=$DATA_DIR/backups
TIMERS=("$APP-maintenance.timer" "$APP-backup.timer")
JOBS=("$APP-maintenance.service" "$APP-backup.service")
# Alles, was SQLite beim Öffnen von tatilvakti.db mitliest
SUFFIXES=("" -wal -shm -journal)

CHECK=0
START=0
SRC=
while [ $# -gt 0 ]; do
    case $1 in
        --check) CHECK=1; shift ;;
        --start) START=1; shift ;;
        -h|--help) sed -n '2,/^set -/{/^#/p}' "$0"; exit 0 ;;
        -*) die "unbekannte Option: $1 (siehe --help)" ;;
        *) [ -z "$SRC" ] || die "nur eine Sicherung angeben"; SRC=$1; shift ;;
    esac
done
[ "$CHECK" = 0 ] || [ "$START" = 0 ] || die "--check ändert nichts, --start passt nicht dazu"

if [ "$(id -u)" -ne 0 ] && [ "$(id -un)" != "$SERVICE_USER" ]; then
    die "als root ausführen (sudo)"
fi
id "$SERVICE_USER" >/dev/null 2>&1 || die "Dienstbenutzer $SERVICE_USER fehlt"
SERVICE_GROUP=$(id -gn "$SERVICE_USER")
[ -d "$DATA_DIR" ] || die "$DATA_DIR fehlt. Erst den Dienst einmal starten: systemctl start $SERVICE"
if [ -z "$SRC" ]; then
    [ "$CHECK" = 1 ] || die "Sicherung angeben, z. B. $BACKUP_DIR/tatilvakti-<zeit>.db (siehe --help)"
    [ -d "$BACKUP_DIR" ] || die "$BACKUP_DIR fehlt, es lief noch keine Sicherung." \
        "Jetzt sichern: systemctl start $APP-backup.service"
    # Die Dateinamen tragen die UTC-Zeit, die neueste steht also sortiert am Ende
    SRC=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'tatilvakti-*.db' | sort | tail -n 1)
    [ -n "$SRC" ] || die "keine Sicherung in $BACKUP_DIR"
fi
[ -f "$SRC" ] || die "Sicherung nicht gefunden: $SRC"
SRC=$(readlink -f -- "$SRC")
cd /  # der Dienstbenutzer darf das Verzeichnis des Aufrufers oft nicht lesen

# Sicherungen von backup.py stehen im Rollback-Journal-Modus (Bytes 18 und 19 des Dateikopfs = 1).
# Im WAL-Modus (2) ist es keine, sondern z. B. eine Dateikopie der laufenden DB, der die jüngsten
# Meldungen aus der -wal fehlen können.
check_header() {
    local write='' read=''
    [ "$(head -c 15 -- "$1" | tr -d '\0')" = "SQLite format 3" ] || die "$1 ist keine SQLite-Datenbank"
    read -r write read < <(od -An -tu1 -j18 -N2 -- "$1") || true
    if [ "$write" != 1 ] || [ "$read" != 1 ]; then
        die "$1 steht nicht im Rollback-Journal-Modus (WAL?), ist also" \
            "keine Sicherung von scripts/backup.py, sondern z. B. eine Dateikopie der laufenden DB."
    fi
}

# Meldungszahl aus "ok <datei> reports=<n>" (backup.py --verify); prüft dabei Integrität und Inhalt
verify_reports() {  # $1 = Datei
    local out n
    out=$(as_service_user "$PYTHON" "$LIB_DIR/backup.py" --verify "$1") || return 1
    n=$(sed -n 's/^ok .* reports=\([0-9][0-9]*\)$/\1/p' <<<"$out")
    [ -n "$n" ] || { printf 'unerwartete Ausgabe von backup.py --verify: %s\n' "$out" >&2; return 1; }
    printf '%s\n' "$n"
}

listed() {  # $1 = Wert, weitere Argumente = Liste; Erfolg, wenn der Wert darin steht
    local want=$1 item
    shift
    for item in "$@"; do [ "$item" != "$want" ] || return 0; done
    return 1
}

TS=$(date -u +%Y%m%dT%H%M%SZ)
# Arbeitskopie mit PID: Ein zweiter Aufruf in derselben Sekunde scheitert an der Sperre und
# räumt beim Beenden nur seine eigene Kopie weg
STAGE=$DATA_DIR/.restore-$TS-$$.db
OLD=$DATA_DIR/pre-restore-$TS
PHASE=prepare  # prepare → stopped → moving → installed → started
RUNNING=()     # Units, die vor dem Restore liefen; der Weg zurück startet genau diese
UNITS=()       # Units, die das Skript nach dem Restore startet
OLD_FILES=()
SIGNAL=        # INT, TERM oder HUP, wenn das Skript per Signal abbricht
REASON=        # letzte Fehlermeldung, für zurueck.txt

# Wie die Fassung in release-lib.sh, merkt sich aber die Meldung für zurueck.txt
die() { REASON=$*; printf 'FEHLER: %s\n' "$*" >&2; exit 1; }

# Was in OLD liegt, aus dem Verzeichnis gelesen statt mitgezählt: Ein Signal kann zwischen einem
# mv und jeder Buchführung danach kommen.
old_files() {
    local s
    OLD_FILES=()
    for s in "${SUFFIXES[@]}"; do
        if [ -e "$OLD/$NAME$s" ] || [ -L "$OLD/$NAME$s" ]; then OLD_FILES+=("$OLD/$NAME$s"); fi
    done
}

# Befehle zurück auf den Stand vor dem Restore, mit den echten Pfaden zum Kopieren
way_back() {
    local f
    old_files
    if [ -n "$SIGNAL" ]; then echo "Abgebrochen durch SIG$SIGNAL."; else echo "Abgebrochen."; fi
    [ ${#OLD_FILES[@]} -eq 0 ] || echo "Der bisherige Stand liegt in $OLD."
    if [ "$PHASE" = started ] && [ -n "$SIGNAL" ]; then
        echo "Die Sicherung war schon eingespielt und geprüft, offen war nur der Start bzw. /healthz:"
        echo "  systemctl is-active ${UNITS[*]}; curl -s $HEALTH_URL"
        echo "Laufen alle und meldet /healthz \"db\": true, ist der Restore fertig. Sonst:"
    fi
    echo "Zurück auf den Stand vor dem Restore:"
    if [ "$PHASE" = started ]; then
        echo "  systemctl stop ${TIMERS[*]}"
        echo "  systemctl stop ${JOBS[*]} $SERVICE"
    fi
    if [ "$PHASE" = installed ] || [ "$PHASE" = started ]; then
        # Die zurückgespielte DB ist eine Kopie, die Sicherung selbst bleibt in backups/
        echo "  rm -f -- $(for f in "${SUFFIXES[@]}"; do printf '%q ' "$DB$f"; done)"
        [ ${#OLD_FILES[@]} -gt 0 ] || echo "  # vor dem Restore gab es keine $DB, die App legt eine leere an"
    fi
    if [ ${#OLD_FILES[@]} -gt 0 ]; then
        echo "  mv -- $(printf '%q ' "${OLD_FILES[@]}")$(printf '%q' "$DATA_DIR")/"
    fi
    if listed "$SERVICE" "${RUNNING[@]}"; then echo "  systemctl reset-failed $SERVICE"; fi
    if [ ${#RUNNING[@]} -gt 0 ]; then echo "  systemctl start ${RUNNING[*]}"; fi
}

# Beim Beenden: Kopie wegräumen; bei einem Fehler oder Abbruch den Weg zurück nennen
on_exit() {
    local rc=$? text why
    trap '' INT TERM HUP PIPE  # das Aufräumen nicht selbst unterbrechen lassen
    rm -f -- "$STAGE" "$STAGE-wal" "$STAGE-shm" "$STAGE-journal"
    [ "$rc" -ne 0 ] || return 0
    if [ "$PHASE" = prepare ]; then
        echo "Abgebrochen, nichts geändert." >&2 || true
        return 0
    fi
    text=$(way_back)
    # Zuerst in die Datei: Nach einem Verbindungsabbruch (SIGHUP) liest die Ausgabe niemand mehr
    why="Exit $rc"
    [ -z "$REASON" ] || why="FEHLER: $REASON"
    [ -z "$SIGNAL" ] || why="Signal SIG$SIGNAL"
    if [ -d "$OLD" ] && printf 'restore.sh %s, %s, %s\n%s\n' "$SRC" "$(date '+%F %T %Z')" "$why" "$text" \
            2>/dev/null >"$OLD/zurueck.txt"; then
        text+=$'\n'"Diese Befehle stehen auch in $OLD/zurueck.txt."
    fi
    printf '%s\n' "$text" >&2 || true
}
trap on_exit EXIT
trap 'SIGNAL=INT; exit 130' INT
trap 'SIGNAL=TERM; exit 143' TERM
trap 'SIGNAL=HUP; exit 129' HUP

take_lock

log "Sicherung $SRC"
check_header "$SRC"
install -m 0600 -o "$SERVICE_USER" -g "$SERVICE_GROUP" -- "$SRC" "$STAGE"
REPORTS=$(verify_reports "$STAGE") || die "Sicherung fehlerhaft: $SRC"
log "Sicherung geprüft: integrity_check ok, $REPORTS Meldungen, keine Prüfwerte"

if [ "$CHECK" = 1 ]; then
    REL=$BASE/current
    [ -x "$REL/.venv/bin/python" ] || die "kein aktives Release unter $REL"
    log "Preflight von $(link_target current) gegen eine Kopie der Sicherung (ohne pytest)"
    (cd "$REL" && as_service_user "$REL/.venv/bin/python" scripts/preflight.py \
        --env-file "$ENV_FILE" --db "$STAGE" --no-tests) \
        || die "Preflight gegen die Sicherung fehlgeschlagen: $SRC"
    log "Restore-Probe ok: ${SRC##*/}, $REPORTS Meldungen. Dienst und Datenbank unverändert."
    exit 0
fi

[ ! -e "$OLD" ] || die "$OLD existiert bereits"
# Vor dem Stoppen festhalten, was laufen soll (wie bei rollback.sh: läuft, startet gerade oder ist
# abgestürzt). Genau das startet das Skript danach wieder, mit --start alles.
for unit in "$SERVICE" "${TIMERS[@]}"; do
    if service_wanted "$unit"; then RUNNING+=("$unit"); fi
done
if [ "$START" = 1 ]; then UNITS=("$SERVICE" "${TIMERS[@]}"); else UNITS=("${RUNNING[@]}"); fi

PHASE=stopped
log "Stoppe ${TIMERS[*]} ${JOBS[*]} $SERVICE (vorher aktiv: ${RUNNING[*]:-keine})"
systemctl stop "${TIMERS[@]}"
systemctl stop "${JOBS[@]}" "$SERVICE"

install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_GROUP" -- "$OLD"
PHASE=moving
for suffix in "${SUFFIXES[@]}"; do
    if [ -e "$DB$suffix" ] || [ -L "$DB$suffix" ]; then mv -- "$DB$suffix" "$OLD/"; fi
done
for suffix in "${SUFFIXES[@]}"; do
    if [ -e "$DB$suffix" ] || [ -L "$DB$suffix" ]; then
        die "$DB$suffix ist nach dem Verschieben wieder da. Öffnet ein anderer Prozess die DB?"
    fi
done
old_files
if [ ${#OLD_FILES[@]} -gt 0 ]; then
    log "Bisheriger Stand nach $OLD: ${OLD_FILES[*]##*/}"
else
    log "Keine bisherige Datenbank vorhanden"
fi

PHASE=installed
mv -T -- "$STAGE" "$DB"
AFTER=$(verify_reports "$DB") || die "Die zurückgespielte $DB ist fehlerhaft"
[ "$AFTER" = "$REPORTS" ] || die "$DB enthält $AFTER statt $REPORTS Meldungen"
log "$DB: integrity_check ok, $AFTER Meldungen wie in der Sicherung"

if [ ${#UNITS[@]} -gt 0 ]; then
    PHASE=started
    log "Starte ${UNITS[*]}"
    if listed "$SERVICE" "${UNITS[@]}"; then systemctl reset-failed "$SERVICE" 2>/dev/null || true; fi
    systemctl start "${UNITS[@]}" || die "Start fehlgeschlagen: journalctl -u $SERVICE -n 50"
fi
if listed "$SERVICE" "${UNITS[@]}"; then
    wait_healthy "" || die "$SERVICE nach dem Restore nicht gesund: journalctl -u $SERVICE -n 50"
fi

log "Restore fertig: ${SRC##*/}, $AFTER Meldungen"
STOPPED=()
for unit in "$SERVICE" "${TIMERS[@]}"; do
    listed "$unit" "${UNITS[@]}" || STOPPED+=("$unit")
done
if [ ${#STOPPED[@]} -gt 0 ]; then
    log "Gestoppt wie vor dem Restore: ${STOPPED[*]}. Starten: systemctl start ${STOPPED[*]}"
    listed "$SERVICE" "${UNITS[@]}" || log "/healthz ist ungeprüft, solange $SERVICE nicht läuft."
fi
if [ ${#OLD_FILES[@]} -gt 0 ]; then
    log "Bisheriger Stand: $OLD"
    log "Löschen, sobald die Seite geprüft ist, spätestens nach 48 h (die alte DB enthält Prüfwerte,"
    log "die die Wartung sonst nach 48 h löscht): rm -r -- $(printf '%q' "$OLD")"
else
    rmdir -- "$OLD"
fi
