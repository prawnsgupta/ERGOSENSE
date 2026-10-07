"""PostgreSQL connection pool (psycopg 3). Rows come back as dicts."""
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ergosense import config

_pool = None


def get_pool(dsn=None):
    """Return the shared connection pool, creating it on first use."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            dsn or config.DATABASE_URL,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


def close_pool():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def fetch_all(sql, params=None):
    with get_pool().connection() as conn:
        return conn.execute(sql, params).fetchall()


def fetch_one(sql, params=None):
    with get_pool().connection() as conn:
        return conn.execute(sql, params).fetchone()


def vector_literal(values):
    """Format a list of floats as a pgvector literal, e.g. '[0.1,0.2]'.

    Passing vectors as text and casting with ::vector avoids registering a custom type adapter.
    """
    return "[" + ",".join(f"{float(v):.6f}" for v in values) + "]"
