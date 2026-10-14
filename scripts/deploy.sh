#!/usr/bin/env bash
# Neues Release von tatilvakti v2 bauen, prüfen und aktivieren. Als root, aus einem Git-Klon:
#
#   sudo scripts/deploy.sh [--rev <git-rev>] [--no-tests] [--keep <n>] [--force-schema "<Grund>"]
#                          [--no-snapshot "<Grund>"]
#
# 1. git archive <rev> → /opt/tatilvakti-v2/releases/<UTC-Zeit>-<commit> (nur Eingechecktes)
# 2. Schema-Version (scripts/schema_check.py): Kennt das Release die der Datenbank nicht (älterer
#    Stand per --rev) oder ist eine der Versionen nicht lesbar, Abbruch mit den Wegen;
#    --force-schema übersteuert das mit Begründung.
# 3. eigenes venv je Release, Pakete nur mit passenden Hashes aus requirements.txt
# 4. Preflight als Dienstbenutzer: Konfiguration, create_app gegen eine DB-Kopie, alle Seiten
#    und die Precache-Liste, pytest (in einem Wegwerf-venv; das Laufzeit-venv bleibt ohne pytest)
# 5. Sicherung der Datenbank mit scripts/backup.py als Dienstbenutzer (ohne Prüfwerte, konsistent)
#    nach /var/lib/tatilvakti-v2/backups/pre-deploy-<release>.db; die neuesten 3 bleiben und je
#    ältere Schema-Version die neueste. Fehlt Platz oder scheitert sie, endet deploy.sh hier.
#    Notfall --no-snapshot (z. B. Hotfix bei fast voller Platte): keine Sicherung, dafür eine
#    WARNUNG mit der jüngsten vorhandenen Sicherung (nächtlich oder vor einem früheren Deploy) und
#    ihrem Alter; der Grund steht im Journal. Nicht zusammen mit --force-schema: Dann ist die
#    Sicherung der einzige Weg zurück.
# 6. erst dann: Symlink current atomar umschalten, previous zeigt auf das bisherige Release
# 7. soll der Dienst laufen (aktiv, startet gerade oder abgestürzt): reset-failed und Neustart,
#    /healthz muss die neue Build-ID melden, sonst automatisch zurück und Neustart des alten Releases
# 8. alte Releases aufräumen (die neuesten --keep bleiben, Standard 5, current/previous immer)
#
# Scheitert ein Schritt vor 6, ändert sich am laufenden Dienst nichts. Gleichzeitige Läufe von
# deploy.sh, rollback.sh und restore.sh verhindert eine Sperre (/opt/tatilvakti-v2/.lock).
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
        --force-schema) force_reason "${2-}"; FORCE_SCHEMA=$2; shift 2 ;;
        --no-snapshot) force_reason "${2-}" --no-snapshot "Platte fast voll, Hotfix"; NO_SNAPSHOT=$2; shift 2 ;;
        -h|--help) sed -n '2,/^set -/{/^#/p}' "$0"; exit 0 ;;
        *) die "unbekannte Option: $1 (siehe --help)" ;;
    esac
done
{ [[ $KEEP =~ ^[0-9]+$ ]] && [ "$KEEP" -ge 2 ]; } || die "--keep muss eine Zahl ab 2 sein"
# Ein älteres Release auf neuerem Schema (--force-schema) ändert die Daten vielleicht falsch, zurück
# auf den Stand davor führt dann nur die Sicherung vor dem Umschalten
if [ -n "$FORCE_SCHEMA" ] && [ -n "$NO_SNAPSHOT" ]; then
    die "--no-snapshot nicht zusammen mit --force-schema: Läuft ein älteres Release auf neuerem Schema," \
        "ist die Sicherung vor dem Umschalten der einzige Weg zurück. Erst Platz schaffen (docs/RUNBOOK.md 2c)."
fi

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

# schema_check.py und backup.py laufen als Dienstbenutzer. Lesbare Kopie, denn den Klon darf er
# vielleicht nicht lesen (z. B. im Home-Verzeichnis). Diese Fassung, nicht die des Releases: Sie
# kennt die Prüfung und --pre-deploy auch dann, wenn --rev einen älteren Stand ausrollt.
install -m 0644 "$LIB_DIR/schema_check.py" "$LIB_DIR/backup.py" "$WORK/"
HELPERS=$WORK
ACTIVE=$(link_target current)  # bisher aktives Release (leer beim ersten Deploy)
# restore.sh für Weg 2 aus dem bisherigen Release: Ein älteres Ziel hat es vielleicht nicht
RESTORE=$BASE/${ACTIVE:-current}/scripts/restore.sh
FORCE_CMD="sudo $HERE/deploy.sh --rev $REV --force-schema \"<Grund>\""
SCHEMA_RC=0
check_schema "$REL" || SCHEMA_RC=$?
case $SCHEMA_RC in
    0) ;;
    2) schema_unreadable_hint "$FORCE_CMD"
       die "Schema-Version nicht lesbar, nichts umgeschaltet." ;;
    *) schema_hint "$FORCE_CMD" "$RESTORE"
       die "Schema-Version: $NAME passt nicht zur Datenbank, nichts umgeschaltet." ;;
esac

log "venv mit gehashten Abhängigkeiten (requirements.txt)"
python3 -m venv "$REL/.venv"
"$REL/.venv/bin/pip" install --quiet --disable-pip-version-check --no-input \
    --require-hashes -r "$REL/requirements.txt"
# Der Code ist für den Dienst schreibgeschützt (ProtectSystem=strict): .pyc jetzt erzeugen, auch für
# gunicorn.conf.py (lädt gunicorn beim Start aus dem Arbeitsverzeichnis)
"$REL/.venv/bin/python" -m compileall -q "$REL/tatilvakti" "$REL/wsgi.py" "$REL/gunicorn.conf.py" >/dev/null

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

SKIPPED=0  # Sicherung per --no-snapshot ausgelassen (nur mit DB, ohne gibt es ohnehin keine)
if [ -n "$NO_SNAPSHOT" ]; then
    [ ! -e "$DB" ] || SKIPPED=1
    skip_snapshot "$NAME"
else
    if ! pre_deploy_snapshot "$NAME"; then
        # --no-snapshot nur ohne --force-schema (siehe oben)
        HINT=" Notfall ohne diese Sicherung: --no-snapshot \"<Grund>\" (README → Update und Rollback)."
        [ -z "$FORCE_SCHEMA" ] || HINT=" Mit --force-schema geht es nicht ohne diese Sicherung (docs/RUNBOOK.md 2c)."
        die "Sicherung vor dem Umschalten fehlgeschlagen, nichts umgeschaltet. Platz schaffen bzw. Ursache" \
            "beheben, dann erneut (backups/ wie in README → Backup und Restore).$HINT"
    fi
fi
MIGRATES=0  # stellt das neue Release die DB beim Start auf eine neuere Schema-Version um?
if [ -n "$SCHEMA_DB" ] && [ "$SCHEMA_DB" -gt 0 ] && [ "$SCHEMA_RELEASE" -gt "$SCHEMA_DB" ]; then MIGRATES=1; fi
NOTE=
if [ -n "$SNAPSHOT" ]; then
    NOTE="Datenstand vor dem Deploy: $SNAPSHOT"
    if [ "$MIGRATES" = 1 ]; then
        # Das neue Release hat die DB beim Start umgestellt; das alte kennt die neue Fassung nicht
        NOTE+=". $NAME hat die Datenbank evtl. schon auf Schema-Version $SCHEMA_RELEASE umgestellt."
        NOTE+=" Zurück auf den Stand vor dem Deploy: sudo $REL/scripts/restore.sh $SNAPSHOT"
    fi
elif [ "$SKIPPED" = 1 ]; then
    # Ohne Sicherung vor diesem Deploy bleibt nur eine ältere (newest_backup), samt Verlust der
    # Meldungen seither
    NOTE=$(no_snapshot_note)
    if [ -n "$LATEST" ] && [ "$MIGRATES" = 1 ]; then
        NOTE+=". $NAME hat die Datenbank evtl. schon auf Schema-Version $SCHEMA_RELEASE umgestellt."
        NOTE+=" Zurück auf diesen Datenstand: sudo $REL/scripts/restore.sh $LATEST"
    fi
fi

# Ab hier bleibt das Release stehen, auch wenn es zurückgerollt wird (zur Analyse)
ACTIVATED=1
activate_release "releases/$NAME" "$PREFLIGHT_BUILD" "$NOTE"

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

warn_snapshot_prune "$REL"  # nur bei --rev mit einem Stand von vor dieser Sicherung
log "fertig: $NAME"
[ -z "$ACTIVE" ] || log "Zurück auf $ACTIVE: sudo $BASE/current/scripts/rollback.sh"
if [ -n "$SNAPSHOT" ] && [ -n "$FORCE_SCHEMA" ] && [ -n "$SCHEMA_DB" ] && [ "$SCHEMA_DB" -gt "$SCHEMA_RELEASE" ]; then
    # Die eben angelegte Sicherung hat die Version der DB, ist also nicht die für Weg 2
    log "Datenstand vor dem Deploy: $SNAPSHOT (Schema-Version $SCHEMA_DB, für $NAME zu neu)"
elif [ -n "$SNAPSHOT" ]; then
    log "Datenstand vor dem Deploy: $SNAPSHOT (README → Update und Rollback)"
elif [ "$SKIPPED" = 1 ]; then
    # Kein Weg 2 mit der Sicherung vor dem Deploy; stellt das Release die DB um, nennt rollback.sh
    # beim Abbruch die Sicherungen, die das bisherige Release kennt
    log "WARNUNG: $(no_snapshot_note)"
fi
forced_schema_note "$NAME" "$RESTORE"
