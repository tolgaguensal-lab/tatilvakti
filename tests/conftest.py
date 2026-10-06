from datetime import datetime, timedelta, timezone

import pytest

from tatilvakti import create_app


class Clock:
    """Steuerbare Uhr für Tests (UTC)."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)

    @property
    def ts(self) -> int:
        return int(self.now.timestamp())


@pytest.fixture
def clock():
    return Clock(datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc))


@pytest.fixture
def app(tmp_path, clock):
    app = create_app({"TESTING": True, "TV_DB_PATH": str(tmp_path / "test.db"), "TV_CLOCK": clock,
                      "TV_BASE_URL": "https://tatilvakti.example"})
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    from tatilvakti.db import connect
    conn = connect(app.config["TV_DB_PATH"])
    yield conn
    conn.close()
