#!/usr/bin/env bash
# Neues Release von tatilvakti v2 bauen, prüfen und aktivieren. Als root, aus einem Git-Klon:
#
#   sudo scripts/deploy.sh [--rev <git-rev>] [--no-tests] [--keep <n>]
#
# 1. git archive <rev> → /opt/tatilvakti-v2/releases/<UTC-Zeit>-<commit> (nur Eingechecktes)
# 2. eigenes venv je Release, Pakete nur mit passenden Hashes aus requirements.txt
# 3. Preflight als Dienstbenutzer: Konfiguration, create_app gegen eine DB-Kopie, alle Seiten
#    und die Precache-Liste, pytest (in einem Wegwerf-venv; das Laufzeit-venv bleibt ohne pytest)
# 4. erst dann: Symlink current atomar umschalten, previous zeigt auf das bisherige Release
# 5. soll der Dienst laufen (aktiv, startet gerade oder abgestürzt): reset-failed und Neustart,
#    /healthz muss die neue Build-ID melden, sonst automatisch zurück und Neustart des alten Releases
# 6. alte Releases aufräumen (die neuesten --keep bleiben, Standard 5, current/previous immer)
#
# Scheitert ein Schritt vor 4, ändert sich am laufenden Dienst nichts. Gleichzeitige Läufe von
# deploy.sh und rollback.sh verhindert eine Sperre (/opt/tatilvakti-v2/.lock).
set -euo pipefail
umask 022

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=scripts/release-lib.sh
. "$HERE/release-lib.sh"

REV=HEAD
TESTS=1
KEEP=5
while [ $# -gt 0 ]; do
    case $1 in
        --rev) REV=${2:?--rev braucht einen Wert}; shift 2 ;;
        --no-tests) TESTS=0; shift ;;
        --keep) KEEP=${2:?--keep braucht einen Wert}; shift 2 ;;
        -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
        *) die "unbekannte Option: $1 (siehe --help)" ;;
    esac
done
[[ $KEEP =~ ^[0-9]+$ ]] && [ "$KEEP" -ge 2 ] || die "--keep muss eine Zahl ab 2 sein"

# Git als root in einem Klon, der einem anderen Benutzer gehört: safe.directory nur für diesen Aufruf
SRC=$(cd "$HERE/.." && pwd)
GIT=(git -c "safe.directory=$SRC" -C "$SRC")
"${GIT[@]}" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || die "$SRC ist kein Git-Klon. deploy.sh aus dem Klon starten, nicht aus einem Release."
COMMIT=$("${GIT[@]}" rev-parse --verify --quiet "$REV^{commit}") || die "unbekannte Revision: $REV"
if [ "$REV" = HEAD ] && [ -n "$("${GIT[@]}" status --porcelain --untracked-files=no)" ]; then
    log "Hinweis: Nicht eingecheckte Änderungen im Klon werden NICHT ausgeliefert."
fi

command -v python3 >/dev/null || die "python3 fehlt"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "python3 ab 3.10 nötig"
id "$SERVICE_USER" >/dev/null 2>&1 || die "Dienstbenutzer $SERVICE_USER fehlt (README → Betrieb → Einrichten)"
[ -r "$ENV_FILE" ] || die "$ENV_FILE fehlt (Vorlage: deploy/tatilvakti-v2.env.example)"

NAME=$(date -u +%Y%m%dT%H%M%SZ)-${COMMIT:0:10}
REL=$BASE/releases/$NAME
install -d -m 0755 "$BASE" "$BASE/releases"
take_lock
[ ! -e "$REL" ] || die "$REL existiert bereits"

WORK=$(mktemp -d)
chmod 0755 "$WORK"  # das Test-venv muss für den Dienstbenutzer lesbar sein
ACTIVATED=0
cleanup() {
    rm -rf "$WORK"
    # Halb gebautes oder durchgefallenes Release entfernen, solange es nie aktiv war
    if [ "$ACTIVATED" = 0 ] && [ -d "$REL" ]; then
        rm -rf -- "$REL"
    fi
}
trap cleanup EXIT

log "Release $NAME aus $SRC ($REV)"
mkdir -m 0755 "$REL"
"${GIT[@]}" archive --format=tar "$COMMIT" | tar -x --no-same-owner --no-same-permissions -C "$REL"
printf '%s\n' "$COMMIT" >"$REL/REVISION"

log "venv mit gehashten Abhängigkeiten (requirements.txt)"
python3 -m venv "$REL/.venv"
"$REL/.venv/bin/pip" install --quiet --disable-pip-version-check --no-input \
    --require-hashes -r "$REL/requirements.txt"
# Der Code ist für den Dienst schreibgeschützt (ProtectSystem=strict): .pyc jetzt erzeugen
"$REL/.venv/bin/python" -m compileall -q "$REL/tatilvakti" "$REL/wsgi.py" >/dev/null

PREFLIGHT_ARGS=(--no-tests)
if [ "$TESTS" = 1 ]; then
    log "Wegwerf-venv mit pytest für den Preflight (requirements-dev.txt)"
    python3 -m venv "$WORK/venv-test"
    "$WORK/venv-test/bin/pip" install --quiet --disable-pip-version-check --no-input \
        --require-hashes -r "$REL/requirements-dev.txt"
    PREFLIGHT_ARGS=(--test-python "$WORK/venv-test/bin/python")
fi

log "Preflight als $SERVICE_USER"
PREFLIGHT_BUILD=
run_preflight "$REL" "${PREFLIGHT_ARGS[@]}" || die "Preflight fehlgeschlagen, nichts umgeschaltet."
[ -n "$PREFLIGHT_BUILD" ] || die "Preflight lieferte keine Build-ID"

# Ab hier bleibt das Release stehen, auch wenn es zurückgerollt wird (zur Analyse)
ACTIVATED=1
activate_release "releases/$NAME" "$PREFLIGHT_BUILD"

# Aufräumen: die neuesten KEEP Releases behalten, current und previous nie löschen
CUR=$(link_target current)
PREV=$(link_target previous)
n=0
while IFS= read -r dir; do
    n=$((n + 1))
    if [ "$n" -le "$KEEP" ] || [ "releases/$dir" = "$CUR" ] || [ "releases/$dir" = "$PREV" ]; then
        continue
    fi
    rm -rf -- "${BASE:?}/releases/$dir"
    log "altes Release entfernt: $dir"
done < <(find "$BASE/releases" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r)

# Units installiert der Betreiber selbst (README → Einrichten); geänderte Vorlagen nur melden
for f in "$REL"/deploy/*.service "$REL"/deploy/*.timer; do
    installed=/etc/systemd/system/${f##*/}
    if [ -e "$installed" ] && ! cmp -s "$f" "$installed"; then
        log "Hinweis: $installed weicht von deploy/${f##*/} ab (README → Update und Rollback)"
    fi
done

log "fertig: $NAME"
