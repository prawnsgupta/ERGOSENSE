"""Shared fixtures. Database tests use a separate 'ergosense_test' database, so running the
tests never touches the demo data. They are skipped if Postgres (docker compose) is not up."""
import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from ergosense import config
from ergosense.backend.init_db import init_db

TEST_DB = "ergosense_test"


def _test_dsn():
    params = conninfo_to_dict(config.DATABASE_URL)
    params["dbname"] = TEST_DB
    return make_conninfo(**params)


@pytest.fixture(scope="session")
def test_dsn():
    try:
        with psycopg.connect(config.DATABASE_URL, autocommit=True, connect_timeout=3) as admin:
            exists = admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,)).fetchone()
            if not exists:
                admin.execute(f"CREATE DATABASE {TEST_DB}")
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is not running (start it with: docker compose up -d)")
    return _test_dsn()


@pytest.fixture
def fresh_db(test_dsn, capsys):
    """A freshly reset and seeded test database; returns its connection string."""
    init_db(test_dsn, reset=True)
    capsys.readouterr()          # hide init_db's progress output
    return test_dsn


@pytest.fixture
def conn(fresh_db):
    from psycopg.rows import dict_row
    with psycopg.connect(fresh_db, row_factory=dict_row) as c:
        yield c
