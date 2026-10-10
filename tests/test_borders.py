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
    report(db, clock, 1, ip="2001:db8:1:2::1")
    assert None not in tuple(db.execute("SELECT client, net, block FROM reports").fetchone())
    clock.advance(hours=B.CLIENT_HASH_TTL_H + 1)
    result = B.maintenance(db, clock.ts, force=True)
    assert result == {"clients_cleared": 1, "reports_deleted": 0, "salts_deleted": 1}
    assert tuple(db.execute("SELECT client, net, block FROM reports").fetchone()) == (None, None, None)
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


def test_day_key_is_deleted_right_after_the_utc_day_change(db, clock):
    """Prüfwerte entstehen nur mit dem heutigen Schlüssel; der von gestern hat keinen Zweck mehr."""
    report(db, clock, 1)
    clock.now = clock.now.replace(hour=23, minute=55)
    B.maintenance(db, clock.ts, force=True)
    assert salt_days(db) == ["2026-10-06"]
    clock.advance(minutes=10)  # 00:05 UTC
    assert B.maintenance(db, clock.ts, force=True)["salts_deleted"] == 1
    assert salt_days(db) == []


def test_only_todays_day_key_exists_once_a_new_one_is_made(db, clock):
    """Der Schlüssel von gestern verschwindet schon mit der ersten Meldung des neuen Tages,
    nicht erst mit der nächsten Wartung."""
    report(db, clock, 1)
    assert salt_days(db) == ["2026-10-06"]
    clock.advance(days=1)
    B.client_key(db, "203.0.113.9", clock.ts)  # erste Anfrage nach 00:00 UTC
    assert salt_days(db) == ["2026-10-07"]
    before = B.client_key(db, "203.0.113.9", clock.ts)
    assert B.client_key(db, "203.0.113.9", clock.ts) == before  # derselbe Schlüssel den ganzen Tag
    assert salt_days(db) == ["2026-10-07"]


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


def test_lost_salt_db_is_recreated(app, tmp_path, clock):
    """tmpfs-Verzeichnis nach Neustart oder durch systemd geleert: neu anlegen, harmlos."""
    import shutil
    salt_dir = tmp_path / "run"
    conn = connect(app.config["TV_DB_PATH"], str(salt_dir / "salts.db"))
    try:
        report(conn, clock, 1)
        shutil.rmtree(salt_dir)
        clock.advance(minutes=B.SAME_SPOT_COOLDOWN_MIN + 1)
        report(conn, clock, 2)
        assert (salt_dir / "salts.db").exists()
        (salt_dir / "salts.db").unlink()  # nur die Datei weg: Tabelle wird neu angelegt
        report(conn, clock, 3, ip="10.0.0.2")
    finally:
        conn.close()


def test_check_salt_db_reports_unusable_paths(app, tmp_path):
    from tatilvakti.db import check_salt_db
    assert check_salt_db(app.config["TV_SALT_DB_PATH"]) is None
    blocker = tmp_path / "keine-verzeichnis"
    blocker.write_text("")  # Elternpfad ist eine Datei: nicht anlegbar, wie /run ohne Rechte
    problem = check_salt_db(str(blocker / "run" / "salts.db"))
    assert problem and "nicht anlegbar" in problem


@pytest.mark.parametrize("known", [("client",), ("client", "net")], ids=["ohne-net", "ohne-block"])
def test_old_database_gets_the_new_columns(tmp_path, clock, known):
    from tatilvakti import create_app
    path = tmp_path / "alt.db"
    conn = connect(str(path))
    conn.execute("CREATE TABLE reports (id INTEGER PRIMARY KEY, crossing TEXT NOT NULL, direction TEXT NOT NULL, "
                 "bucket INTEGER NOT NULL, observed_at INTEGER NOT NULL, created_at INTEGER NOT NULL, "
                 + ", ".join(f"{c} TEXT" for c in known) + ")")
    conn.execute(f"INSERT INTO reports (crossing, direction, bucket, observed_at, created_at, {', '.join(known)}) "
                 f"VALUES ('kapikule', 'to_tr', 3, ?, ?, {', '.join('?' * len(known))})",
                 (clock.ts, clock.ts, *(f"alt-{c}" for c in known)))
    conn.close()
    create_app({"TESTING": True, "TV_DB_PATH": str(path), "TV_CLOCK": clock})
    create_app({"TESTING": True, "TV_DB_PATH": str(path), "TV_CLOCK": clock})  # zweiter Start: nichts zu tun
    conn = connect(str(path))
    try:
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(reports)")]
        assert columns[-3:] == ["client", "net", "block"]
        for index in ("idx_reports_net", "idx_reports_block"):
            assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (index,)).fetchone()
        # Altbestand ohne block zählt je Anschluss bzw. Client weiter mit
        B.add_report(conn, "kapikule", "to_tr", 1, "10.0.0.1", clock.ts)
        st = B.statuses(conn, ["kapikule"], clock.ts)["kapikule"]["to_tr"]
        assert (st["bucket"], st["count"]) == (3, 2)
    finally:
        conn.close()


def test_hashes_left_by_an_older_release_are_cleared_on_start(app, db, clock):
    """Rollback auf ein Release, das block nicht kennt: Dessen Wartung leert client und net, aber
    nicht block. Beim nächsten Start dieses Stands verschwinden solche Reste."""
    from tatilvakti import create_app
    report(db, clock, 1, ip="2001:db8:1:2::1")
    report(db, clock, 2, ip="198.51.100.4")
    db.execute("UPDATE reports SET client = NULL, net = NULL WHERE bucket = 1")  # wie die alte Wartung
    db.execute("UPDATE reports SET client = NULL WHERE bucket = 2")  # noch älter: auch net blieb stehen
    create_app({"TESTING": True, "TV_DB_PATH": app.config["TV_DB_PATH"], "TV_CLOCK": clock})
    assert [tuple(r) for r in db.execute("SELECT client, net, block FROM reports")] == [(None, None, None)] * 2


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
    # Klammern und Ports, wie sie manche Proxys in X-Forwarded-For schreiben
    ("[2001:db8:1:2::1]", "2001:db8:1:2::/64"),
    ("[2001:db8:1:2::1]:443", "2001:db8:1:2::/64"),
    ("203.0.113.7:4444", "203.0.113.7"),
    ("[::ffff:203.0.113.7]:80", "203.0.113.7"),
])
def test_ip_normalization(ip, expected):
    assert B.normalize_ip(ip) == expected


@pytest.mark.parametrize("ip,expected", [
    ("203.0.113.7", "203.0.113.7"),
    ("::ffff:203.0.113.7", "203.0.113.7"),
    ("2001:db8:1:2::1", "2001:db8:1::/56"),
    ("2001:db8:1:ff:aaaa::1", "2001:db8:1::/56"),
    ("2001:db8:1:100::1", "2001:db8:1:100::/56"),
    ("[2001:db8:1:2::1]:443", "2001:db8:1::/56"),
])
def test_net_normalization(ip, expected):
    assert B.normalize_net(ip) == expected


@pytest.mark.parametrize("ip,expected", [
    ("203.0.113.7", "203.0.113.7"),
    ("::ffff:203.0.113.7", "203.0.113.7"),
    ("2001:db8:1:2::1", "2001:db8:1::/48"),
    ("2001:db8:1:ff00::1", "2001:db8:1::/48"),
    ("2001:db8:2::1", "2001:db8:2::/48"),
    ("[2001:db8:1:2::1]:443", "2001:db8:1::/48"),
])
def test_block_normalization(ip, expected):
    assert B.normalize_block(ip) == expected


def test_unreadable_address_warns_once_without_the_value(monkeypatch, caplog):
    monkeypatch.setattr(B, "_unparsable_warned", False)
    with caplog.at_level(logging.WARNING, logger="tatilvakti.borders"):
        B.normalize_ip("unbekannt-203.0.113.7")
        B.normalize_net("unbekannt-203.0.113.7")
    warnings = [r.getMessage() for r in caplog.records if "Unlesbare" in r.getMessage()]
    assert len(warnings) == 1 and "203.0.113" not in warnings[0]


def test_ipv4_has_the_same_client_net_and_block_value(db, clock):
    client, net, block = B.client_keys(db, "203.0.113.7", clock.ts)
    assert client == net == block == B.client_key(db, "203.0.113.7", clock.ts)
    client6, net6, block6 = B.client_keys(db, "2001:db8:1:2::1", clock.ts)
    assert len({client6, net6, block6}) == 3
    assert B.client_keys(db, "2001:db8:1:3::1", clock.ts)[1:] == (net6, block6)  # anderes /64
    assert B.client_keys(db, "2001:db8:1:ff00::1", clock.ts)[2] == block6  # anderes /56, selbes /48


def test_ipv6_addresses_of_one_64_are_one_client(db, clock):
    report(db, clock, 5, ip="2001:db8:1:2::1")
    with pytest.raises(B.RateLimited):
        report(db, clock, 5, ip="2001:db8:1:2::19")
    report(db, clock, 0, ip="2001:db8:1:3::1")  # anderes /64 = eigener Client (im selben /56)
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


def test_one_vote_per_connection_even_from_many_64s(db, clock):
    """Ein /56 (256 /64-Netze) zählt im Median als eine Stimme: die jüngste."""
    report(db, clock, 5, ip="2001:db8:aa:bb01::1")
    report(db, clock, 4, ip="2001:db8:aa:bb02::1")
    report(db, clock, 0, ip="10.0.3.1")
    st = B.statuses(db, ["kapikule"], clock.ts)["kapikule"]["to_tr"]
    assert (st["bucket"], st["count"]) == (4, 2)  # Stimmen 4 (/56) und 0, nicht 5, 4, 0


def test_connection_limit_per_hour_and_direction(db, clock):
    for i in range(B.NET_REPORTS_PER_HOUR):
        report(db, clock, 5, ip=f"2001:db8:aa:bb{i:02x}::1")
    with pytest.raises(B.RateLimited, match="net_per_hour"):
        report(db, clock, 5, ip="2001:db8:aa:bbff::1")
    report(db, clock, 5, ip="2001:db8:aa:bbff::1", direction="to_de")  # andere Richtung
    report(db, clock, 5, ip="2001:db8:aa:bbff::1", crossing="ipsala")  # anderer Übergang
    report(db, clock, 5, ip="2001:db8:ab:bb00::1")  # anderes /56
    clock.advance(hours=1, seconds=1)
    report(db, clock, 5, ip="2001:db8:aa:bbfe::1")


def test_one_56_can_neither_take_over_nor_lock_out_honest_reporters(db, clock):
    """Review-Szenario: Ein /56 meldet 2 h lang alle 10 Min. aus 30 frischen /64-Netzen „über 5 Std.“,
    dazwischen je zwei ehrliche Reisende mit IPv4 „unter 15 Min.“."""
    honest_ok = attacker_ok = 0
    for rnd in range(12):
        for i in range(30):
            try:
                report(db, clock, 5, ip=f"2001:db8:aa:bb{(rnd * 30 + i) % 256:02x}::1")
                attacker_ok += 1
            except B.RateLimited as exc:
                assert str(exc) == "net_per_hour"  # nie die Obergrenze: die bleibt für alle offen
        for h in range(2):
            report(db, clock, 0, ip=f"198.51.{rnd}.{h + 1}")
            honest_ok += 1
        clock.advance(minutes=10, seconds=1)
    clock.advance(seconds=-1)
    assert honest_ok == 24
    assert attacker_ok == 2 * B.NET_REPORTS_PER_HOUR
    st = B.statuses(db, ["kapikule"], clock.ts)["kapikule"]["to_tr"]
    assert (st["bucket"], st["level"]) == (0, "ok")


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
        with pytest.raises(B.CrossingBusy, match="crossing_busy"):
            report(db, clock, 5, ip="2001:db8:ffff::1")
        with pytest.raises(B.RateLimited):
            report(db, clock, 5, ip="2001:db8:fffe::1")
    warnings = [r for r in caplog.records if "Obergrenze" in r.getMessage()]
    assert len(warnings) == 1 and "kapikule" in warnings[0].getMessage()  # nur einmal, kein Log-Fluten
    report(db, clock, 1, ip="10.0.0.1", direction="to_de")  # andere Richtung bleibt offen
    report(db, clock, 1, ip="10.0.0.1", crossing="ipsala")
    clock.advance(minutes=B.CROSSING_CAP_MIN, seconds=1)
    report(db, clock, 1, ip="2001:db8:ffff::1")


def test_one_48_takes_at_most_its_share_of_the_cap(db, clock, caplog):
    """Audit d3-ratelimit-crossing-cap-48: Ein /48 hat 256 /56-Anschlüsse mit je eigenem Limit. Es
    darf die Obergrenze nicht allein füllen; die übrigen Plätze bleiben für alle anderen offen."""
    assert B.BLOCK_CAP < B.CROSSING_CAP
    for i in range(B.BLOCK_CAP):
        report(db, clock, 5, ip=f"2001:db8:aa:{i:02x}00::1")  # je ein anderes /56 im selben /48
    with caplog.at_level(logging.WARNING, logger="tatilvakti.borders"):
        for i in range(B.BLOCK_CAP, B.BLOCK_CAP + 3):
            with pytest.raises(B.BlockBusy, match="crossing_busy") as exc:
                report(db, clock, 5, ip=f"2001:db8:aa:{i:02x}00::1")
            # nach außen wie die Obergrenze: Formular-Code „busy“, API-Detail „crossing_busy“
            assert isinstance(exc.value, B.CrossingBusy) and exc.value.code == "busy"
    warnings = [r.getMessage() for r in caplog.records if "Netz-Anteil" in r.getMessage()]
    assert len(warnings) == 1 and "crossing=kapikule direction=to_tr" in warnings[0]  # einmal, kein Log-Fluten
    assert "2001:db8" not in warnings[0] and not re.search(r"[0-9a-f]{32}", warnings[0])  # weder IP noch Prüfwert
    report(db, clock, 5, ip="2001:db8:aa:ff00::1", direction="to_de")  # nur dieser Übergang und diese Richtung
    # Alle anderen bekommen die übrigen Plätze bis zur Obergrenze
    for i in range(B.CROSSING_CAP - B.BLOCK_CAP):
        report(db, clock, 0, ip=f"198.51.100.{i + 1}")
    with pytest.raises(B.CrossingBusy) as exc:
        report(db, clock, 0, ip="198.51.100.200")
    assert not isinstance(exc.value, B.BlockBusy)
    clock.advance(minutes=B.CROSSING_CAP_MIN, seconds=1)
    report(db, clock, 5, ip="2001:db8:aa:fe00::1")  # nach dem Fenster wieder offen


def test_one_48_gets_at_most_block_votes_in_the_median(db, clock):
    """Zehn Anschlüsse (/56) aus einem /48 melden „über 5 Std.“, drei Reisende „unter 15 Min.“:
    Das /48 zählt mit BLOCK_VOTES Stimmen und überstimmt die drei nicht."""
    for i in range(B.BLOCK_CAP):
        report(db, clock, 5, ip=f"2001:db8:aa:{i:02x}00::1")
    for i in range(3):
        report(db, clock, 0, ip=f"10.0.5.{i}")
    st = B.statuses(db, ["kapikule"], clock.ts)["kapikule"]["to_tr"]
    assert 1 < B.BLOCK_VOTES < 3
    assert (st["bucket"], st["level"], st["count"]) == (0, "ok", 3 + B.BLOCK_VOTES)


def test_reporters_sharing_a_48_keep_several_votes(db, clock):
    """Mobilfunkkunden eines Anbieters können sich ein /48 teilen: Unterhalb des Netz-Anteils wird
    jede Meldung gespeichert. Im Median zählen je Anschluss (/56) die jüngste Meldung und je /48
    die BLOCK_VOTES Anschlüsse mit den jüngsten Meldungen (früher: eine Stimme je /56, keine Grenze
    je /48; der erste Stand dieser Änderung: eine Stimme je /48)."""
    assert B.BLOCK_VOTES == 2  # die Erwartungen unten rechnen mit zwei Stimmen
    for i, bucket in enumerate([5, 1, 1]):
        report(db, clock, bucket, ip=f"2001:db8:cc:{i:02x}00::{i + 1}")
        clock.advance(minutes=1)
    assert db.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 3
    st = B.statuses(db, ["kapikule"], clock.ts)["kapikule"]["to_tr"]
    assert (st["bucket"], st["count"]) == (1, 2)  # die beiden jüngsten Anschlüsse: 1 und 1
    # Der erste Anschluss meldet neu: Seine Stimme ist jetzt die jüngste und verdrängt die älteste
    clock.advance(minutes=B.SAME_SPOT_COOLDOWN_MIN)
    report(db, clock, 2, ip="2001:db8:cc:0::1")
    st = B.statuses(db, ["kapikule"], clock.ts)["kapikule"]["to_tr"]
    assert (st["bucket"], st["count"]) == (2, 2)  # Stimmen 1 (dritter Anschluss) und 2
    # Ein anderes /48 und IPv4 zählen daneben ganz normal
    report(db, clock, 1, ip="2001:db8:cd:100::1")
    report(db, clock, 1, ip="198.51.100.31")
    st = B.statuses(db, ["kapikule"], clock.ts)["kapikule"]["to_tr"]
    assert (st["bucket"], st["count"]) == (1, 4)


def test_one_48_can_neither_take_over_nor_lock_out_honest_reporters(db, clock):
    """Audit-Szenario: Ein /48 meldet eine Stunde lang alle 10 Min. aus 30 frischen /56 „über 5 Std.“,
    dazwischen je zwei ehrliche Reisende aus anderen Netzen „unter 15 Min.“."""
    honest_ok = attacker_ok = 0
    for rnd in range(6):
        for i in range(30):
            try:
                report(db, clock, 5, ip=f"2001:db8:aa:{(rnd * 30 + i) % 256:02x}00::1")
                attacker_ok += 1
            except B.BlockBusy:
                pass  # nie die Obergrenze für alle: die bleibt offen
        for h in range(2):
            report(db, clock, 0, ip=f"2001:db8:{rnd + 1}{h}:1::1")
            honest_ok += 1
        clock.advance(minutes=10, seconds=1)
    clock.advance(seconds=-1)
    assert honest_ok == 12 and attacker_ok == 6 * B.BLOCK_CAP
    st = B.statuses(db, ["kapikule"], clock.ts)["kapikule"]["to_tr"]
    assert (st["bucket"], st["level"], st["count"]) == (0, "ok", 12 + B.BLOCK_VOTES)


def test_full_cap_is_noted_for_healthz(db, clock, caplog):
    """Audit d3-ratelimit-crossing-cap-48, Erkennung: Eine volle Obergrenze (auch der Anteil eines
    /48) landet nicht nur im Log, sondern als Zeitpunkt in kv (crossing_cap_at), trotz ROLLBACK der
    abgewiesenen Meldung. Eine Flut schreibt dabei nicht bei jeder Anfrage."""
    assert B.last_crossing_cap(db) is None
    for i in range(B.BLOCK_CAP):
        report(db, clock, 5, ip=f"2001:db8:ee:{i:02x}00::1")
    first = clock.ts
    with caplog.at_level(logging.WARNING, logger="tatilvakti.borders"):
        with pytest.raises(B.BlockBusy):
            report(db, clock, 5, ip="2001:db8:ee:ff00::1")
        assert B.last_crossing_cap(db) == first
        assert db.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == B.BLOCK_CAP  # abgewiesen
        assert not db.in_transaction
        clock.advance(minutes=1)
        observer = connect(db.path)  # data_version zählt nur Änderungen anderer Verbindungen
        try:
            seen = observer.execute("PRAGMA data_version").fetchone()[0]
            for i in range(5):
                with pytest.raises(B.BlockBusy):
                    report(db, clock, 5, ip=f"2001:db8:ee:f{i}00::1")
            assert B.last_crossing_cap(db) == first  # höchstens alle CROSSING_CAP_MIN Min. je Prozess
            assert observer.execute("PRAGMA data_version").fetchone()[0] == seen
        finally:
            observer.close()
        # Die Obergrenze für alle zählt genauso
        for i in range(B.CROSSING_CAP):
            report(db, clock, 1, ip=f"198.51.100.{i + 1}", direction="to_de")
        with pytest.raises(B.CrossingBusy):
            report(db, clock, 1, ip="198.51.100.99", direction="to_de")
    assert B.last_crossing_cap(db) == clock.ts


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


def test_hourly_pattern_cache_follows_purges(app, db, clock):
    """Nach purge-reports zeigt der Tagesverlauf den Spam nicht mehr – auch nicht aus dem Cache."""
    def pattern():
        return B.hourly_pattern(db, "kapikule", "to_tr", "Europe/Istanbul", clock.ts)
    for i in range(3):
        report(db, clock, 5, ip=f"10.0.4.{i}")
    assert pattern()["slots"][13]["count"] == 3
    other = connect(app.config["TV_DB_PATH"])  # z. B. das CLI in einem anderen Prozess
    try:
        assert B.purge_reports(other, "kapikule", clock.ts - 60, clock.ts + 1) == 3
    finally:
        other.close()
    assert pattern()["slots"][13]["count"] == 0


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
