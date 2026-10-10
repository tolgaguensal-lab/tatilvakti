#!/usr/bin/env bash
# Sicherung der Haupt-DB von tatilvakti v2 zurückspielen (als root):
#
#   sudo /opt/tatilvakti-v2/current/scripts/restore.sh /var/lib/tatilvakti-v2/backups/tatilvakti-<zeit>.db
#   sudo /opt/tatilvakti-v2/current/scripts/restore.sh --check [<sicherung>]   # Probe, ändert nichts
#
# 1. Sicherung als Kopie neben die DB legen (Dienstbenutzer, 0600), mit backup.py --verify prüfen.
#    Ist sie fehlerhaft, endet das Skript hier, Dienst und DB bleiben unverändert.
# 2. Timer, Wartung, Backup und Dienst stoppen (die Wartung legte sonst in der Lücke eine leere DB an).
# 3. tatilvakti.db samt -wal, -shm und -journal nach pre-restore-<UTC-Zeit>/ verschieben, nie löschen.
#    Eine liegengebliebene -wal (Absturz, SIGKILL) legte SQLite sonst über die Sicherung: alte Daten
#    oder eine beschädigte DB, und /healthz merkt das nicht.
# 4. Geprüfte Kopie an ihren Platz (rename), dort erneut prüfen: integrity_check, Meldungszahl.
# 5. Dienst und Timer starten, auf /healthz warten (scripts/healthcheck.py wie bei deploy.sh).
# Scheitert ein Schritt ab 2, nennt die Ausgabe die Befehle zurück auf den alten Stand.
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
BACKUP_DIR=$DATA_DIR/backups
TIMERS=("$APP-maintenance.timer" "$APP-backup.timer")
JOBS=("$APP-maintenance.service" "$APP-backup.service")
# Alles, was SQLite beim Öffnen von tatilvakti.db mitliest
SUFFIXES=("" -wal -shm -journal)

CHECK=0
SRC=
while [ $# -gt 0 ]; do
    case $1 in
        --check) CHECK=1; shift ;;
        -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
        -*) die "unbekannte Option: $1 (siehe --help)" ;;
        *) [ -z "$SRC" ] || die "nur eine Sicherung angeben"; SRC=$1; shift ;;
    esac
done

if [ "$(id -u)" -ne 0 ] && [ "$(id -un)" != "$SERVICE_USER" ]; then
    die "als root ausführen (sudo)"
fi
id "$SERVICE_USER" >/dev/null 2>&1 || die "Dienstbenutzer $SERVICE_USER fehlt"
SERVICE_GROUP=$(id -gn "$SERVICE_USER")
[ -d "$DATA_DIR" ] || die "$DATA_DIR fehlt. Erst den Dienst einmal starten: systemctl start $SERVICE"
if [ -z "$SRC" ]; then
    [ "$CHECK" = 1 ] || die "Sicherung angeben, z. B. $BACKUP_DIR/tatilvakti-<zeit>.db (siehe --help)"
    # Die Dateinamen tragen die UTC-Zeit, die neueste steht also sortiert am Ende
    SRC=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'tatilvakti-*.db' 2>/dev/null | sort | tail -n 1)
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
    [ "$write" = 1 ] && [ "$read" = 1 ] || die "$1 steht nicht im Rollback-Journal-Modus (WAL?), ist also" \
        "keine Sicherung von scripts/backup.py, sondern z. B. eine Dateikopie der laufenden DB."
}

# Meldungszahl aus "ok <datei> reports=<n>" (backup.py --verify); prüft dabei Integrität und Inhalt
verify_reports() {  # $1 = Datei
    local out n
    out=$(as_service_user "$PYTHON" "$LIB_DIR/backup.py" --verify "$1") || return 1
    n=$(sed -n 's/^ok .* reports=\([0-9][0-9]*\)$/\1/p' <<<"$out")
    [ -n "$n" ] || { printf 'unerwartete Ausgabe von backup.py --verify: %s\n' "$out" >&2; return 1; }
    printf '%s\n' "$n"
}

TS=$(date -u +%Y%m%dT%H%M%SZ)
# Arbeitskopie mit PID: Ein zweiter Aufruf in derselben Sekunde scheitert an der Sperre und
# räumt beim Beenden nur seine eigene Kopie weg
STAGE=$DATA_DIR/.restore-$TS-$$.db
OLD=$DATA_DIR/pre-restore-$TS
PHASE=prepare  # prepare → stopped → moving → installed → started
MOVED=()       # nach OLD verschobene Dateien

# Bei einem Fehler: Kopie wegräumen und den Weg zurück nennen, mit den echten Pfaden zum Kopieren
on_exit() {
    local rc=$? f moved=()
    rm -f -- "$STAGE" "$STAGE-wal" "$STAGE-shm" "$STAGE-journal"
    [ "$rc" -ne 0 ] || return 0
    for f in "${MOVED[@]}"; do moved+=("$OLD/$f"); done
    {
        if [ "$PHASE" = prepare ]; then
            echo "Abgebrochen, nichts geändert."
            return 0
        fi
        if [ ${#moved[@]} -gt 0 ]; then
            echo "Abgebrochen. Der bisherige Stand liegt in $OLD."
        else
            echo "Abgebrochen."
        fi
        echo "Zurück auf den Stand vor dem Restore:"
        if [ "$PHASE" = started ]; then
            echo "  systemctl stop ${TIMERS[*]}"
            echo "  systemctl stop ${JOBS[*]} $SERVICE"
        fi
        if [ "$PHASE" = installed ] || [ "$PHASE" = started ]; then
            # Die zurückgespielte DB ist eine Kopie, die Sicherung selbst bleibt in backups/
            echo "  rm -f -- $(for f in "${SUFFIXES[@]}"; do printf '%q ' "$DB$f"; done)"
            [ ${#moved[@]} -gt 0 ] || echo "  # vor dem Restore gab es keine $DB, die App legt eine leere an"
        fi
        if [ ${#moved[@]} -gt 0 ]; then
            echo "  mv -- $(printf '%q ' "${moved[@]}")$(printf '%q' "$DATA_DIR")/"
        fi
        echo "  systemctl reset-failed $SERVICE"
        echo "  systemctl start $SERVICE ${TIMERS[*]}"
    } >&2
}
trap on_exit EXIT

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

PHASE=stopped
log "Stoppe ${TIMERS[*]} ${JOBS[*]} $SERVICE"
systemctl stop "${TIMERS[@]}"
systemctl stop "${JOBS[@]}" "$SERVICE"

[ ! -e "$OLD" ] || die "$OLD existiert bereits"
install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_GROUP" -- "$OLD"
PHASE=moving
for suffix in "${SUFFIXES[@]}"; do
    if [ -e "$DB$suffix" ] || [ -L "$DB$suffix" ]; then
        mv -- "$DB$suffix" "$OLD/"
        MOVED+=("${DB##*/}$suffix")
    fi
done
for suffix in "${SUFFIXES[@]}"; do
    if [ -e "$DB$suffix" ] || [ -L "$DB$suffix" ]; then
        die "$DB$suffix ist nach dem Verschieben wieder da. Öffnet ein anderer Prozess die DB?"
    fi
done
if [ ${#MOVED[@]} -gt 0 ]; then
    log "Bisheriger Stand nach $OLD: ${MOVED[*]}"
else
    log "Keine bisherige Datenbank vorhanden"
fi

PHASE=installed
mv -T -- "$STAGE" "$DB"
AFTER=$(verify_reports "$DB") || die "Die zurückgespielte $DB ist fehlerhaft"
[ "$AFTER" = "$REPORTS" ] || die "$DB enthält $AFTER statt $REPORTS Meldungen"
log "$DB: integrity_check ok, $AFTER Meldungen wie in der Sicherung"

PHASE=started
log "Starte $SERVICE ${TIMERS[*]}"
systemctl reset-failed "$SERVICE" 2>/dev/null || true
systemctl start "$SERVICE" "${TIMERS[@]}" || die "Start fehlgeschlagen: journalctl -u $SERVICE -n 50"
wait_healthy "" || die "$SERVICE nach dem Restore nicht gesund: journalctl -u $SERVICE -n 50"

log "Restore fertig: ${SRC##*/}, $AFTER Meldungen"
if [ ${#MOVED[@]} -gt 0 ]; then
    log "Bisheriger Stand: $OLD"
    log "Löschen, sobald die Seite geprüft ist, spätestens nach 48 h (die alte DB enthält Prüfwerte,"
    log "die die Wartung sonst nach 48 h löscht): rm -r -- $OLD"
else
    rmdir -- "$OLD"
fi
