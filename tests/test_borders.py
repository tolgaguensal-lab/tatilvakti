"""Grenz-Meldungen: ehrliche Aggregation, Spam-Schutz, Datensparsamkeit."""
import logging
import re
import threading
import time

import pytest

from tatilvakti import borders as B
from tatilvakti.db import connect, salt_db


def report(db, clock, bucket, ip="10.0.0.1", crossing="kapikule", direction="to_tr", **kw):
    return B.add_report(db, crossing, direction, bucket, ip, clock.ts, **kw)


def test_no_reports_is_explicit_none(db, clock):
    st = B.statuses(db, ["kapikule"], clock.ts)["kapikule"]["to_tr"]
    assert st == {"state": "none", "bucket": None, "level": "none", "count": 0, "last_at": None,
                  "window_min": B.WINDOW_MIN}


def test_median_of_recent_reports(db, clock):
    for i, bucket in enumerate([3, 3, 5]):
        report(db, clock, bucket, ip=f"10.0.0.{i}")
    st = B.statuses(db, ["kapikule"], clock.ts)["kapikule"]["to_tr"]
    assert (st["state"], st["bucket"], st["level"], st["count"]) == ("live", 3, "mid", 3)
    other = B.statuses(db, ["kapikule"], clock.ts)["kapikule"]["to_de"]
    assert other["state"] == "none"


def test_even_count_is_conservative(db, clock):
    report(db, clock, 0, ip="10.0.0.1")
    report(db, clock, 2, ip="10.0.0.2")
    assert B.statuses(db, ["kapikule"], clock.ts)["kapikule"]["to_tr"]["bucket"] == 2


def test_old_reports_expire_but_keep_their_age(db, clock):
    report(db, clock, 4)
    reported_at = clock.ts
    clock.advance(minutes=B.WINDOW_MIN + 1)
    st = B.statuses(db, ["kapikule"], clock.ts)["kapikule"]["to_tr"]
    assert st["state"] == "none"
    assert st["last_at"] == reported_at


@pytest.mark.parametrize("direction,bucket", [("north", 1), ("to_tr", 6), ("to_tr", -1), ("to_tr", "abc"), ("to_tr", None)])
def test_invalid_reports_are_rejected(db, clock, direction, bucket):
    with pytest.raises(B.InvalidReport):
        B.add_report(db, "kapikule", direction, bucket, "10.0.0.1", clock.ts)


def test_same_spot_cooldown(db, clock):
    report(db, clock, 2)
    with pytest.raises(B.RateLimited):
        report(db, clock, 3)
    report(db, clock, 3, direction="to_de")  # andere Richtung ist erlaubt
    clock.advance(minutes=B.SAME_SPOT_COOLDOWN_MIN + 1)
    report(db, clock, 3)


def test_hourly_cap_per_client(db, clock):
    crossings = ["kapikule", "derekoy", "ipsala", "pazarkule", "gradina", "horgos"]
    sent = 0
    for cid in crossings:
        for direction in B.DIRECTIONS:
            if sent == B.MAX_REPORTS_PER_HOUR:
                with pytest.raises(B.RateLimited):
                    report(db, clock, 1, crossing=cid, direction=direction)
                return
            report(db, clock, 1, crossing=cid, direction=direction)
            sent += 1


def test_offline_queued_report_keeps_observed_time_but_not_too_old(db, clock):
    report(db, clock, 2, observed_at=clock.ts - 30 * 60)
    row = db.execute("SELECT observed_at, created_at FROM reports").fetchone()
    assert row["observed_at"] == clock.ts - 30 * 60 and row["created_at"] == clock.ts
    with pytest.raises(B.StaleReport):
        report(db, clock, 2, ip="10.0.0.9", observed_at=clock.ts - (B.MAX_REPORT_AGE_MIN + 1) * 60)


def test_future_observed_time_is_clamped(db, clock):
    report(db, clock, 1, observed_at=clock.ts + 3600)
    assert db.execute("SELECT observed_at FROM reports").fetchone()[0] == clock.ts


def test_ip_is_never_stored_and_hash_rotates_daily(db, clock):
    report(db, clock, 1, ip="203.0.113.7")
    stored = db.execute("SELECT * FROM reports").fetchone()
    assert "203.0.113.7" not in " ".join(str(v) for v in tuple(stored))
    first = B.client_key(db, "203.0.113.7", clock.ts)
    clock.advance(days=1)
    assert B.client_key(db, "203.0.113.7", clock.ts) != first


def salt_days(db):
    with salt_db(db) as sconn:
        return [row["day"] for row in sconn.execute("SELECT day FROM salts ORDER BY day")]


def test_maintenance_removes_client_hash_and_old_data(db, clock):
    report(db, clock, 1)
    clock.advance(hours=B.CLIENT_HASH_TTL_H + 1)
    result = B.maintenance(db, clock.ts, force=True)
    assert result == {"clients_cleared": 1, "reports_deleted": 0, "salts_deleted": 1}
    assert db.execute("SELECT client FROM reports").fetchone()[0] is None
    assert salt_days(db) == []
    clock.advance(days=B.RETENTION_DAYS)
    B.maintenance(db, clock.ts, force=True)
    assert db.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 0


def test_client_hash_never_outlives_48_hours_between_maintenance_runs(db, clock):
    """Gelöscht wird ein Wartungsintervall vor Ablauf: Bei Läufen alle 10 Min. nie älter als 48 h."""
    report(db, clock, 1)
    clock.advance(hours=B.CLIENT_HASH_TTL_H, seconds=-B.MAINTENANCE_EVERY_S - 1)
    B.maintenance(db, clock.ts, force=True)
    assert db.execute("SELECT client FROM reports").fetchone()[0] is not None
    clock.advance(seconds=1)
    B.maintenance(db, clock.ts, force=True)
    assert db.execute("SELECT client FROM reports").fetchone()[0] is None


def test_maintenance_is_throttled_unless_forced(db, clock):
    assert B.maintenance(db, clock.ts) is not None
    clock.advance(seconds=B.MAINTENANCE_EVERY_S - 1)
    assert B.maintenance(db, clock.ts) is None
    assert B.maintenance(db, clock.ts, force=True) is not None
    clock.advance(seconds=B.MAINTENANCE_EVERY_S)
    assert B.maintenance(db, clock.ts) is not None


def test_salts_live_only_in_the_separate_salt_db(app, db, clock):
    report(db, clock, 1, ip="203.0.113.7")
    assert app.config["TV_SALT_DB_PATH"].endswith("test-salts.db")
    assert db.salt_path == app.config["TV_SALT_DB_PATH"]
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "salts" not in tables
    assert salt_days(db) == ["2026-10-06"]


def test_legacy_salts_table_in_main_db_is_removed_on_start(tmp_path, clock):
    from tatilvakti import create_app
    path = tmp_path / "alt.db"
    marker = b"ALTER-TAGESSCHLUESSEL-0123456789"
    conn = connect(str(path))
    conn.execute("CREATE TABLE salts (day TEXT PRIMARY KEY, salt BLOB NOT NULL)")
    conn.execute("INSERT INTO salts VALUES ('2026-10-01', ?)", (marker,))
    conn.close()
    assert any(marker in file.read_bytes() for file in tmp_path.glob("alt.db*"))
    create_app({"TESTING": True, "TV_DB_PATH": str(path), "TV_CLOCK": clock})
    conn = connect(str(path))
    try:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'salts'").fetchone() is None
    finally:
        conn.close()
    for file in tmp_path.glob("alt.db*"):  # auch keine Reste im freien Speicher oder im WAL
        assert marker not in file.read_bytes(), file


# ------------------------------------------------------------------ Spam-Robustheit

@pytest.mark.parametrize("ip,expected", [
    ("203.0.113.7", "203.0.113.7"),
    ("::ffff:203.0.113.7", "203.0.113.7"),
    ("2001:db8:1:2:aaaa:bbbb:cccc:dddd", "2001:db8:1:2::/64"),
    ("2001:db8:1:2::1", "2001:db8:1:2::/64"),
    ("2001:db8:1:3::1", "2001:db8:1:3::/64"),
    ("fe80::1%eth0", "fe80::/64"),
    ("kein-ip", "kein-ip"),
])
def test_ip_normalization(ip, expected):
    assert B.normalize_ip(ip) == expected


def test_ipv6_addresses_of_one_64_are_one_client(db, clock):
    report(db, clock, 5, ip="2001:db8:1:2::1")
    with pytest.raises(B.RateLimited):
        report(db, clock, 5, ip="2001:db8:1:2::19")
    report(db, clock, 0, ip="2001:db8:1:3::1")  # anderes /64 = anderer Anschluss
    assert B.client_key(db, "::ffff:198.51.100.4", clock.ts) == B.client_key(db, "198.51.100.4", clock.ts)


def test_one_vote_per_client_in_the_median(db, clock):
    """Audit-Szenario: Ein Troll meldet alle 21 Min. „über 5 Std.“, vier Reisende „unter 15 Min.“."""
    for _ in range(6):
        report(db, clock, 5, ip="198.51.100.66")
        clock.advance(minutes=B.SAME_SPOT_COOLDOWN_MIN + 1)
    for i in range(4):
        report(db, clock, 0, ip=f"10.0.1.{i}")
    st = B.statuses(db, ["kapikule"], clock.ts)["kapikule"]["to_tr"]
    assert (st["bucket"], st["level"], st["count"]) == (0, "ok", 5)


def test_latest_report_of_a_client_replaces_its_earlier_vote(db, clock):
    report(db, clock, 5, ip="10.0.0.1")
    report(db, clock, 4, ip="10.0.0.2")
    clock.advance(minutes=B.SAME_SPOT_COOLDOWN_MIN + 1)
    report(db, clock, 0, ip="10.0.0.1")
    st = B.statuses(db, ["kapikule"], clock.ts)["kapikule"]["to_tr"]
    assert (st["bucket"], st["count"]) == (4, 2)  # Stimmen 0 und 4, nicht 5, 4, 0


def test_reports_without_client_value_count_individually(db, clock):
    for bucket in (1, 1, 3):
        db.execute("INSERT INTO reports (crossing, direction, bucket, observed_at, created_at) "
                   "VALUES ('ipsala', 'to_de', ?, ?, ?)", (bucket, clock.ts, clock.ts))
    st = B.statuses(db, ["ipsala"], clock.ts)["ipsala"]["to_de"]
    assert (st["bucket"], st["count"]) == (1, 3)


def test_crossing_cap_limits_floods_from_many_addresses(db, clock, caplog):
    for i in range(B.CROSSING_CAP):
        report(db, clock, 5, ip=f"2001:db8:{i}::1")
    with caplog.at_level(logging.WARNING, logger="tatilvakti.borders"):
        with pytest.raises(B.RateLimited, match="crossing_busy"):
            report(db, clock, 5, ip="2001:db8:ffff::1")
        with pytest.raises(B.RateLimited):
            report(db, clock, 5, ip="2001:db8:fffe::1")
    warnings = [r for r in caplog.records if "Obergrenze" in r.getMessage()]
    assert len(warnings) == 1 and "kapikule" in warnings[0].getMessage()  # nur einmal, kein Log-Fluten
    report(db, clock, 1, ip="10.0.0.1", direction="to_de")  # andere Richtung bleibt offen
    report(db, clock, 1, ip="10.0.0.1", crossing="ipsala")
    clock.advance(minutes=B.CROSSING_CAP_MIN, seconds=1)
    report(db, clock, 1, ip="2001:db8:ffff::1")


# --------------------------------------------------------------- strikte Eingaben

@pytest.mark.parametrize("bucket", [1.0, 1.5, True, False, float("inf"), float("nan"), "1.0", " 1", "1e0",
                                    "٣", 10 ** 400, [1], {"x": 1}])
def test_bucket_must_be_a_real_integer(db, clock, bucket):
    with pytest.raises(B.InvalidReport):
        B.add_report(db, "kapikule", "to_tr", bucket, "10.0.0.1", clock.ts)


@pytest.mark.parametrize("observed_at", [1e400, float("inf"), float("-inf"), float("nan"), 1.5, True,
                                         10 ** 400, "1e400", "Infinity"])
def test_observed_at_must_be_a_real_integer(db, clock, observed_at):
    with pytest.raises(B.InvalidReport):
        B.add_report(db, "kapikule", "to_tr", 1, "10.0.0.1", clock.ts, observed_at)


def test_integer_text_from_forms_is_accepted(db, clock):
    B.add_report(db, "kapikule", "to_tr", "2", "10.0.0.1", clock.ts, str(clock.ts - 60))
    assert tuple(db.execute("SELECT bucket, observed_at FROM reports").fetchone()) == (2, clock.ts - 60)


# ------------------------------------------------------------- Leistung und Cache

def test_status_cache_sees_new_reports_from_other_connections(app, db, clock):
    assert B.statuses(db, ["kapikule"], clock.ts)["kapikule"]["to_tr"]["state"] == "none"
    other = connect(app.config["TV_DB_PATH"])  # wie ein zweiter gunicorn-Worker
    try:
        B.add_report(other, "kapikule", "to_tr", 2, "10.0.0.1", clock.ts)
    finally:
        other.close()
    st = B.statuses(db, ["kapikule"], clock.ts)["kapikule"]["to_tr"]
    assert (st["state"], st["bucket"]) == ("live", 2)


def test_status_cache_follows_the_clock(db, clock):
    report(db, clock, 3)
    assert B.statuses(db, ["kapikule"], clock.ts)["kapikule"]["to_tr"]["state"] == "live"
    # Direktes Schreiben an der App vorbei zählt den Meldungsstand nicht hoch: bis zu 15 s alt
    db.execute("INSERT INTO reports (crossing, direction, bucket, observed_at, created_at) "
               "VALUES ('kapikule', 'to_tr', 5, ?, ?)", (clock.ts, clock.ts))
    assert B.statuses(db, ["kapikule"], clock.ts)["kapikule"]["to_tr"]["count"] == 1
    clock.advance(seconds=B.STATUS_CACHE_S)
    assert B.statuses(db, ["kapikule"], clock.ts)["kapikule"]["to_tr"]["count"] == 2
    clock.advance(minutes=B.WINDOW_MIN + 1)
    assert B.statuses(db, ["kapikule"], clock.ts)["kapikule"]["to_tr"]["state"] == "none"


def test_hourly_pattern_is_cached_for_a_few_minutes(db, clock):
    def pattern():
        return B.hourly_pattern(db, "kapikule", "to_tr", "Europe/Istanbul", clock.ts)
    first = pattern()
    first["slots"][13]["count"] = 99  # Aufrufer verändern den Cache nicht
    for i in range(3):
        report(db, clock, 2, ip=f"10.0.2.{i}")
    assert pattern()["slots"][13]["count"] == 0  # neue Meldungen lösen keine Neuberechnung aus
    clock.advance(seconds=B.PATTERN_CACHE_S)
    assert pattern()["slots"][13]["count"] == 3


def test_cached_statuses_cannot_be_changed_by_callers(db, clock):
    report(db, clock, 3)
    first = B.statuses(db, ["kapikule"], clock.ts)
    first["kapikule"]["to_tr"]["bucket"] = 99
    assert B.statuses(db, ["kapikule"], clock.ts)["kapikule"]["to_tr"]["bucket"] == 3


def test_hot_queries_use_indexes_not_table_scans(app, db, clock):
    """Kein Volltabellen-Scan in den Abfragen pro Seitenaufruf und pro Meldung (Audit sec-2)."""
    statements = []
    db.set_trace_callback(statements.append)
    ids = [c["id"] for c in app.extensions["tv"].content.crossings["crossings"]]
    report(db, clock, 2)
    B.statuses(db, ids, clock.ts + 1)
    B.count_since(db, clock.ts - 86400)
    B.maintenance(db, clock.ts + 1, force=True)
    db.set_trace_callback(None)
    checked = 0
    for sql in statements:
        if "reports" not in sql or sql.lstrip().upper().startswith(("INSERT", "BEGIN", "COMMIT")):
            continue
        params = [None] * sql.count("?")
        plan = " | ".join(row[3] for row in db.execute("EXPLAIN QUERY PLAN " + sql, params))
        assert not re.search(r"\bSCAN (reports|r)\b", plan), (sql, plan)
        checked += 1
    assert checked >= 6


def test_hourly_pattern_needs_enough_reports(db, clock):
    assert B.hourly_pattern(db, "kapikule", "to_tr", "Europe/Istanbul", clock.ts)["enough"] is False
    clock.advance(seconds=B.PATTERN_CACHE_S)  # Tagesverlauf ist bis zu 5 Min. gecacht
    # 4 Stunden mit je 3 Meldungen an verschiedenen Tagen
    for day in range(3):
        for hour_offset in range(4):
            ts = clock.ts - day * 86400 - hour_offset * 3600
            db.execute("INSERT INTO reports (crossing, direction, bucket, observed_at, created_at) VALUES "
                       "('kapikule', 'to_tr', 2, ?, ?)", (ts, ts))
    pattern = B.hourly_pattern(db, "kapikule", "to_tr", "Europe/Istanbul", clock.ts)
    assert pattern["enough"] is True and pattern["usable_hours"] == 4
    # 10:00 UTC = 13:00 Istanbul
    assert pattern["slots"][13]["count"] == 3 and pattern["slots"][13]["bucket"] == 2


class _Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _SlowConn:
    """Verbindung, die nach der Cooldown-Prüfung kurz wartet – macht das Race-Fenster sichtbar."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):  # path, salt_path usw. der echten Verbindung
        return getattr(self._conn, name)

    def execute(self, sql, params=()):
        cur = self._conn.execute(sql, params)
        if sql.startswith("SELECT 1 FROM reports WHERE client"):
            row = cur.fetchone()
            time.sleep(0.05)
            return _Result(row)
        return cur


def test_concurrent_reports_from_one_client_are_serialized(app, clock):
    """Parallele Requests desselben Clients: genau einer kommt durch (atomare Sperre)."""
    path = app.config["TV_DB_PATH"]
    workers = 6
    barrier = threading.Barrier(workers)
    outcomes = []
    lock = threading.Lock()

    def send():
        conn = connect(path)
        try:
            barrier.wait()
            B.add_report(_SlowConn(conn), "kapikule", "to_tr", 5, "198.51.100.4", clock.ts)
            result = "ok"
        except B.RateLimited:
            result = "limited"
        finally:
            conn.close()
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=send) for _ in range(workers)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sorted(outcomes) == ["limited"] * (workers - 1) + ["ok"]
    conn = connect(path)
    assert conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 1
    conn.close()
