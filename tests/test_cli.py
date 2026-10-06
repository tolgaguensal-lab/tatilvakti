"""Betriebsbefehle: flask --app tatilvakti maintenance / purge-reports."""
import pytest

from tatilvakti import borders as B
from tatilvakti.db import salt_db


@pytest.fixture
def runner(app):
    return app.test_cli_runner()


def report(db, clock, crossing="kapikule", direction="to_tr", ip="10.0.0.1", bucket=5):
    B.add_report(db, crossing, direction, bucket, ip, clock.ts)


def count(db, where="1"):
    return db.execute(f"SELECT COUNT(*) FROM reports WHERE {where}").fetchone()[0]


def test_maintenance_command_runs_immediately(runner, db, clock):
    report(db, clock)
    clock.advance(hours=B.CLIENT_HASH_TTL_H + 1)
    result = runner.invoke(args=["maintenance"])
    assert result.exit_code == 0, result.output
    assert "1 Prüfwerte gelöscht" in result.output and "1 Tagesschlüssel gelöscht" in result.output
    assert count(db, "client IS NOT NULL") == 0
    with salt_db(db) as sconn:
        assert sconn.execute("SELECT COUNT(*) FROM salts").fetchone()[0] == 0
    # erneut sofort: force, kein 10-Minuten-Guard
    assert runner.invoke(args=["maintenance"]).exit_code == 0


@pytest.fixture
def spam(db, clock):
    """10:00 UTC (12:00 Uhr deutscher Zeit): zwei ehrliche Meldungen, danach 3 Spam-Meldungen ab 10:30 UTC."""
    report(db, clock, ip="10.0.0.1", bucket=0)
    report(db, clock, ip="10.0.0.2", bucket=0, direction="to_de")
    clock.advance(minutes=30)
    for i in range(3):
        report(db, clock, ip=f"2001:db8:{i}::1")
    report(db, clock, ip="10.0.0.3", crossing="ipsala")
    return clock


def test_purge_dry_run_counts_without_deleting(runner, db, spam):
    result = runner.invoke(args=["purge-reports", "--crossing", "kapikule", "--since", "2026-10-06T12:15",
                                 "--dry-run"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("3 Meldungen würden gelöscht") and "Probelauf" in result.output
    assert count(db) == 6


def test_purge_deletes_by_arrival_time_crossing_and_direction(runner, app, db, spam):
    before = B.statuses(db, ["kapikule"], spam.ts)["kapikule"]["to_tr"]
    assert (before["bucket"], before["count"]) == (5, 4)
    result = runner.invoke(args=["purge-reports", "--crossing", "kapikule", "--direction", "to_tr",
                                 "--since", "2026-10-06T10:15:00Z"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("3 Meldungen gelöscht")
    assert count(db, "crossing = 'kapikule'") == 2 and count(db, "crossing = 'ipsala'") == 1
    # Status sofort ohne Spam, auch wenn er vorher im Cache lag
    after = B.statuses(db, ["kapikule"], spam.ts)["kapikule"]["to_tr"]
    assert (after["bucket"], after["count"]) == (0, 1)


def test_purge_until_is_exclusive_and_accepts_offsets(runner, db, spam):
    result = runner.invoke(args=["purge-reports", "--crossing", "kapikule", "--since", "2026-10-06T13:00+03:00",
                                 "--until", "2026-10-06T13:30+03:00"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("2 Meldungen gelöscht")  # 10:00 UTC ja, 10:30 UTC nicht mehr
    assert count(db, "crossing = 'kapikule'") == 3


@pytest.mark.parametrize("args,message", [
    (["--crossing", "atlantis", "--since", "2026-10-06"], "unbekannt"),
    (["--crossing", "kapikule", "--since", "gestern"], "kein ISO-8601-Zeitpunkt"),
    (["--crossing", "kapikule", "--since", "2026-10-06T12:00", "--until", "2026-10-06T11:00"], "vor --until"),
    (["--crossing", "kapikule", "--direction", "north", "--since", "2026-10-06"], "north"),
    (["--crossing", "kapikule"], "--since"),
])
def test_purge_rejects_bad_arguments(runner, db, spam, args, message):
    result = runner.invoke(args=["purge-reports", *args])
    assert result.exit_code == 2 and message in result.output
    assert count(db) == 6
