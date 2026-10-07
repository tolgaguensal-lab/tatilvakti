#!/usr/bin/env bash
# Zurück auf ein früheres Release von tatilvakti v2 (als root):
#
#   sudo scripts/rollback.sh                 # auf das vorige Release (Symlink previous)
#   sudo scripts/rollback.sh <release>       # auf ein bestimmtes Release aus releases/
#   sudo scripts/rollback.sh --list          # Releases anzeigen
#   sudo scripts/rollback.sh --no-preflight  # Notfall: ohne Vorabprüfung umschalten
#
# Prüft das Ziel vorher (Preflight ohne pytest: create_app gegen eine DB-Kopie, alle Seiten),
# schaltet current atomar um (previous zeigt danach auf das bisherige Release, ein zweiter
# Aufruf geht also wieder vor), startet den Dienst neu und wartet auf /healthz.
# Die Datenbank bleibt unverändert. Zurück zur ALT-App: docs/MIGRATION.md → Rollback.
set -euo pipefail
umask 022

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=scripts/release-lib.sh
. "$HERE/release-lib.sh"

PREFLIGHT=1
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
        -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
        -*) die "unbekannte Option: $1 (siehe --help)" ;;
        *) TARGET=releases/${1#releases/}; shift ;;
    esac
done

CUR=$(link_target current)
[ -n "$CUR" ] || die "$BASE/current fehlt, es gibt nichts zurückzurollen"
if [ -z "$TARGET" ]; then
    TARGET=$(link_target previous)
    [ -n "$TARGET" ] || die "kein voriges Release (previous). Verfügbar: $0 --list"
fi
[ -x "$BASE/$TARGET/.venv/bin/gunicorn" ] || die "$BASE/$TARGET ist kein vollständiges Release"
[ "$TARGET" != "$CUR" ] || die "$TARGET ist bereits aktiv"

PREFLIGHT_BUILD=
if [ "$PREFLIGHT" = 1 ]; then
    log "Preflight für $TARGET (ohne pytest)"
    run_preflight "$BASE/$TARGET" --no-tests || die "Preflight fehlgeschlagen, nichts umgeschaltet. Notfall: --no-preflight"
fi

set_link "$TARGET" current
set_link "$CUR" previous
log "current → $TARGET (vorher: $CUR)"
restart_if_active "$PREFLIGHT_BUILD" \
    || die "$TARGET startet nicht sauber: journalctl -u $SERVICE -n 50. Zurück: $0 ${CUR#releases/}"
log "Rollback fertig"
