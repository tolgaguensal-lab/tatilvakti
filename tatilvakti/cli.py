"""Betriebsbefehle für die Kommandozeile.

    flask --app tatilvakti maintenance
    flask --app tatilvakti purge-reports --crossing kapikule [--direction to_tr] \\
          --since 2026-10-06T14:00 [--until 2026-10-06T16:00] [--dry-run]

Zeitpunkte ohne Zeitzone gelten als deutsche Zeit (Europe/Berlin), „Z“ bzw. „+03:00“ wie
angegeben. Gelöscht wird nach Eingangszeit der Meldung (created_at, nicht die gemeldete
Beobachtungszeit – die kann ein Spammer bis zu 90 Min. zurückdatieren), since ≤ t < until.
"""
from __future__ import annotations

from datetime import datetime, timezone

import click
from flask import Flask, current_app
from flask.cli import with_appcontext

from . import BERLIN, utcnow
from . import borders as B
from .db import get_db


def _now() -> int:
    return int(utcnow(current_app).timestamp())


def _fmt(ts: int) -> str:
    local = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(BERLIN)
    return local.strftime("%Y-%m-%d %H:%M:%S %Z")


def parse_when(value: str, param_hint: str | None = None) -> int:
    """ISO-8601 → Unix-Sekunden. Ohne Zeitzone: Europe/Berlin. Auch nur ein Datum (00:00 Uhr)."""
    text = value.strip()
    if text[-1:] in ("Z", "z"):
        text = text[:-1] + "+00:00"  # Python 3.10 kennt das „Z“ noch nicht
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise click.BadParameter(
            f"kein ISO-8601-Zeitpunkt: {value!r} (z. B. 2026-10-06T14:00 oder 2026-10-06T12:00Z)",
            param_hint=param_hint) from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=BERLIN)
    return int(moment.timestamp())


@click.command("maintenance")
@with_appcontext
def maintenance_command() -> None:
    """Datensparsamkeit sofort ausführen (Prüfwerte > 48 h, Meldungen > 400 Tage, alte Tagesschlüssel)."""
    result = B.maintenance(get_db(), _now(), force=True)
    click.echo(f"Wartung erledigt: {result['clients_cleared']} Prüfwerte gelöscht, "
               f"{result['reports_deleted']} alte Meldungen gelöscht, "
               f"{result['salts_deleted']} Tagesschlüssel gelöscht.")


@click.command("purge-reports")
@click.option("--crossing", required=True, help="Grenzübergang (id aus crossings.json, z. B. kapikule)")
@click.option("--direction", type=click.Choice(B.DIRECTIONS), default=None,
              help="Nur diese Richtung (sonst beide)")
@click.option("--since", "since_text", required=True, help="Eingang ab (ISO-8601, ohne Zone: Europe/Berlin)")
@click.option("--until", "until_text", default=None, help="Eingang bis ausschließlich (Standard: jetzt)")
@click.option("--dry-run", is_flag=True, help="Nur zählen, nichts löschen")
@with_appcontext
def purge_reports_command(crossing: str, direction: str | None, since_text: str, until_text: str | None,
                          dry_run: bool) -> None:
    """Meldungen eines Übergangs gezielt löschen (z. B. nach Spam) und die Anzahl ausgeben."""
    known = current_app.extensions["tv"].content.crossing_by_id
    if crossing not in known:
        raise click.BadParameter(f"unbekannt: {crossing!r}. Bekannt: {', '.join(sorted(known))}",
                                 param_hint="--crossing")
    since = parse_when(since_text, "--since")
    # +1: „bis jetzt“ schließt Meldungen dieser Sekunde ein
    until = parse_when(until_text, "--until") if until_text else _now() + 1
    if since >= until:
        raise click.BadParameter("--since muss vor --until liegen", param_hint="--since")
    count = B.purge_reports(get_db(), crossing, since, until, direction=direction, dry_run=dry_run)
    scope = f"{crossing}, {direction or 'beide Richtungen'}, Eingang {_fmt(since)} bis {_fmt(until)}"
    if dry_run:
        click.echo(f"{count} Meldungen würden gelöscht ({scope}). Probelauf, nichts geändert.")
    else:
        click.echo(f"{count} Meldungen gelöscht ({scope}).")


def register(app: Flask) -> None:
    app.cli.add_command(maintenance_command)
    app.cli.add_command(purge_reports_command)
