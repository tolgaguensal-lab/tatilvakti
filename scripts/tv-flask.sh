#!/usr/bin/env bash
# Flask-Befehle von tatilvakti v2 auf dem Server ausführen: als Dienstbenutzer, mit derselben
# Env-Datei und denselben DB-Pfaden wie tatilvakti-v2.service, Ausgabe direkt im Terminal.
# Als root:
#
#   sudo /opt/tatilvakti-v2/current/scripts/tv-flask.sh maintenance
#   sudo /opt/tatilvakti-v2/current/scripts/tv-flask.sh purge-reports --crossing kapikule \
#        --since 2026-10-06T14:00 [--until 2026-10-06T16:00] [--direction to_tr] [--dry-run]
#
# Läuft per systemd-run als kurzlebige Unit, so liest systemd die Env-Datei genau wie beim Dienst.
set -euo pipefail

APP=tatilvakti-v2
BASE=/opt/$APP

if [ $# -eq 0 ] || [ "$1" = -h ] || [ "$1" = --help ]; then
    sed -n '2,10p' "$0"
    exit 2
fi
[ "$(id -u)" -eq 0 ] || { echo "FEHLER: als root ausführen (sudo)" >&2; exit 1; }
# Die Verzeichnisse legt systemd beim Start des Dienstes an (StateDirectory/RuntimeDirectory)
for dir in "/var/lib/$APP" "/run/$APP"; do
    [ -d "$dir" ] || { echo "FEHLER: $dir fehlt. Erst den Dienst starten: systemctl start $APP" >&2; exit 1; }
done

exec systemd-run --quiet --wait --pipe --collect \
    --uid="$APP" --gid="$APP" \
    -p WorkingDirectory="$BASE/current" \
    -p EnvironmentFile="/etc/$APP.env" \
    -p UMask=0077 \
    --setenv=TV_DB_PATH="/var/lib/$APP/tatilvakti.db" \
    --setenv=TV_SALT_DB_PATH="/run/$APP/salts.db" \
    "$BASE/current/.venv/bin/flask" --app tatilvakti "$@"
