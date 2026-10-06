"""Grenz-Meldungen: ehrliche Aggregation, Spam-Schutz, Datensparsamkeit."""
import threading
import time

import pytest

from tatilvakti import borders as B
from tatilvakti.db import connect


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


def test_maintenance_removes_client_hash_and_old_data(db, clock):
    report(db, clock, 1)
    clock.advance(hours=B.CLIENT_HASH_TTL_H + 1)
    B.maintenance(db, clock.ts, force=True)
    assert db.execute("SELECT client FROM reports").fetchone()[0] is None
    assert db.execute("SELECT COUNT(*) FROM salts").fetchone()[0] == 0
    clock.advance(days=B.RETENTION_DAYS)
    B.maintenance(db, clock.ts, force=True)
    assert db.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 0


def test_hourly_pattern_needs_enough_reports(db, clock):
    assert B.hourly_pattern(db, "kapikule", "to_tr", "Europe/Istanbul", clock.ts)["enough"] is False
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
