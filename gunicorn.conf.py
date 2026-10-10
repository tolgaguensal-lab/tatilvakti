"""gunicorn-Einstellungen aus dem Repo (ergänzt deploy/tatilvakti-v2.service, ändert die Unit nicht).

gunicorn liest ./gunicorn.conf.py aus dem Arbeitsverzeichnis von selbst, im Dienst also aus
WorkingDirectory=/opt/tatilvakti-v2/current, d. h. aus dem aktiven Release. Schalter auf der
Kommandozeile (ExecStart, GUNICORN_CMD_ARGS) gehen vor; mit --config/-c liest gunicorn diese
Datei nicht mehr. Ältere Releases ohne diese Datei laufen nach einem Rollback wie bisher.

Logs mit Journal-Priorität: Läuft gunicorn unter systemd und geht sein Fehler-Log (--error-logfile -)
ins Journal, bekommt jede Zeile das Präfix <N> ihrer Stufe (tatilvakti.JournalFormatter): z. B.
„WORKER TIMEOUT“ als crit, „Worker … was sent SIGKILL! Perhaps out of memory?“ als err, sonst bleibt
es info. Damit findet `journalctl -u tatilvakti-v2 -p warning` auch gunicorns eigene Fehler. Der Text
bleibt gunicorns Format; im Terminal oder mit einer Log-Datei ändert sich nichts. Ein optionales
Access-Log (GUNICORN_CMD_ARGS, Env-Vorlage) bleibt info, sein Text ebenfalls unverändert.

Bewusst eine eigene logger_class statt logconfig_dict: Mit logconfig_dict schreibt gunicorn immer
ein Access-Log (Logger.access_log_enabled), im Standardformat samt Adresse des Aufrufers, Query
(?land=), Referer und Browserkennung.
"""
from gunicorn.glogging import Logger

from tatilvakti import JournalFormatter, is_journal_stream


class JournalLogger(Logger):
    """gunicorns Logger, Handler mit Ziel Journal schreiben mit Journal-Priorität je Zeile."""

    def setup(self, cfg):
        super().setup(cfg)  # legt die Handler bei jedem (Neu-)Laden der Konfiguration neu an
        for log in (self.error_log, self.access_log):
            for handler in log.handlers:
                if (is_journal_stream(getattr(handler, "stream", None))
                        and not isinstance(handler.formatter, JournalFormatter)):
                    handler.setFormatter(JournalFormatter(handler.formatter))


logger_class = JournalLogger
