#!/usr/bin/env bash
# Zurück auf ein früheres Release von tatilvakti v2 (als root):
#
#   sudo scripts/rollback.sh                 # auf das vorige Release (Symlink previous)
#   sudo scripts/rollback.sh <release>       # auf ein bestimmtes Release aus releases/
#   sudo scripts/rollback.sh --list          # Releases anzeigen
#   sudo scripts/rollback.sh --no-preflight  # Notfall: ohne Preflight umschalten (Schema-Prüfung bleibt)
#   sudo scripts/rollback.sh --start         # Dienst auch starten, wenn er gestoppt ist
#   sudo scripts/rollback.sh --force-schema "<Grund>" [<release>]  # Notfall: trotz neuerer DB
#
# Prüft zuerst die Schema-Version (scripts/schema_check.py aus diesem Release, nicht aus dem Ziel):
# Hat die DB eine neuere als das Ziel kennt (ein Deploy hat sie umgestellt), bricht es ab und nennt
# die Wege, darunter passende Sicherungen (z. B. backups/pre-deploy-<release>.db von deploy.sh).
# Ebenso, wenn sich eine der Versionen nicht lesen lässt (z. B. Rechte an -wal/-shm, beschädigte DB).
# --force-schema schaltet trotzdem um; der Grund steht danach im Journal (-t tatilvakti-v2-release).
# Dann prüft es das Ziel (Preflight ohne pytest: create_app gegen eine DB-Kopie, alle Seiten),
# schaltet current atomar um (previous zeigt danach auf das bisherige Release, ein zweiter
# Aufruf geht also wieder vor), startet den Dienst neu und wartet auf /healthz. Neu gestartet
# wird, wenn der Dienst läuft, gerade startet oder abgestürzt ist ('failed', auch nach
# start-limit-hit; dafür vorher reset-failed). Einen gestoppten Dienst startet nur --start.
# Die Datenbank bleibt unverändert. Zurück zur ALT-App: docs/MIGRATION.md → Rollback.
set -euo pipefail
umask 022

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=scripts/release-lib.sh
. "$HERE/release-lib.sh"

PREFLIGHT=1
START=0
TARGET=
while [ $# -gt 0 ]; do
    case $1 in
        --list)
            CUR=$(link_target current)
            PREV=$(link_target previous)
            find "$BASE/releases" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort -r |
                while IFS= read -r dir; do
                    mark=
                    [ "releases/$dir" != "$CUR" ] || mark="  ← current"
                    [ "releases/$dir" != "$PREV" ] || mark="  ← previous"
                    printf '%s%s\n' "$dir" "$mark"
                done
            exit 0 ;;
        --no-preflight) PREFLIGHT=0; shift ;;
        --start) START=1; shift ;;
        --force-schema) force_reason "${2-}"; FORCE_SCHEMA=$2; shift 2 ;;
        -h|--help) sed -n '2,/^set -/{/^#/p}' "$0"; exit 0 ;;
        -*) die "unbekannte Option: $1 (siehe --help)" ;;
        *) TARGET=releases/${1#releases/}; shift ;;
    esac
done

take_lock
CUR=$(link_target current)
[ -n "$CUR" ] || die "$BASE/current fehlt, es gibt nichts zurückzurollen"
if [ -z "$TARGET" ]; then
    TARGET=$(link_target previous)
    [ -n "$TARGET" ] || die "kein voriges Release (previous). Verfügbar: $0 --list"
fi
[ -x "$BASE/$TARGET/.venv/bin/gunicorn" ] || die "$BASE/$TARGET ist kein vollständiges Release"
[ "$TARGET" != "$CUR" ] || die "$TARGET ist bereits aktiv"

FORCE_CMD="sudo $LIB_DIR/rollback.sh --force-schema \"<Grund>\" ${TARGET#releases/}"
SCHEMA_RC=0
check_schema "$BASE/$TARGET" || SCHEMA_RC=$?
case $SCHEMA_RC in
    0) ;;
    2) schema_unreadable_hint "$FORCE_CMD"
       die "Schema-Version nicht lesbar, nichts umgeschaltet." ;;
    *) schema_hint "$FORCE_CMD" "$LIB_DIR/restore.sh"
       die "Schema-Version: $TARGET passt nicht zur Datenbank, nichts umgeschaltet." ;;
esac

PREFLIGHT_BUILD=
if [ "$PREFLIGHT" = 1 ]; then
    log "Preflight für $TARGET (ohne pytest)"
    run_preflight "$BASE/$TARGET" --no-tests || die "Preflight fehlgeschlagen, nichts umgeschaltet. Notfall: --no-preflight"
fi

# Vor dem Umschalten festhalten, ob der Dienst laufen soll (siehe activate_release)
WANTED=$START
if service_wanted "$SERVICE"; then WANTED=1; fi
set_link "$TARGET" current
set_link "$CUR" previous
log "current → $TARGET (vorher: $CUR)"
if [ "$WANTED" = 1 ]; then
    restart_service "$PREFLIGHT_BUILD" \
        || die "$TARGET startet nicht sauber: journalctl -u $SERVICE -n 50. Zurück: $0 ${CUR#releases/}"
else
    log "$SERVICE ist gestoppt ($(service_state)), kein Neustart. Starten: systemctl start $SERVICE"
fi
forced_schema_note "$TARGET" "$LIB_DIR/restore.sh"
warn_snapshot_prune "$BASE/$TARGET"
log "Rollback fertig"
